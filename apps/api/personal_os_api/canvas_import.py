"""Import verified Canvas assignment observations without storing login secrets."""
from datetime import date, datetime, time, timezone, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from personal_os_api.models import AcademicCourse, CourseProviderBinding, ProcessingStatus, RawEvent, Task, TaskStatus


class CanvasAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    course_id: int = Field(gt=0)
    assignment_id: int = Field(gt=0)
    course_name: str = Field(min_length=1, max_length=240)
    title: str = Field(min_length=1, max_length=240)
    # Required even when null: unknown dates must not silently clear deadlines.
    deadline_at: datetime | None
    deadline_date: date | None = None
    submission_state: Literal["unsubmitted", "offline", "submitted", "graded", "excused", "unknown"]
    description: str = Field(default="", max_length=20000)

    @field_validator("deadline_at")
    @classmethod
    def aware_date(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("Deadline must include a timezone; do not guess a missing time")
        return value


class CanvasSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: Literal["https://canvas.sufe.edu.cn", "https://canvas.shufe.edu.cn"]
    user_id: str = Field(pattern=r"^[1-9][0-9]*$")
    assignments: list[CanvasAssignment] = Field(max_length=10000)

    @model_validator(mode="after")
    def unique_ids(self):
        keys = [(a.course_id, a.assignment_id) for a in self.assignments]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate assignment IDs in snapshot")
        return self


def task_fields(snapshot: CanvasSnapshot, item: CanvasAssignment) -> dict:
    url = f"{snapshot.base_url}/courses/{item.course_id}/assignments/{item.assignment_id}"
    deadline = item.deadline_at
    note = ""
    if deadline is None and item.deadline_date is not None:
        # Task storage has no date-only field. Use end of the local day for sorting,
        # and explicitly disclose that this is not a teacher-provided exact time.
        deadline = datetime.combine(item.deadline_date, time(23, 59), timezone(timedelta(hours=8)))
        note = f"\n提交日期：{item.deadline_date.isoformat()}（老师未注明时刻，待办按当天末尾排列）"
    return {
        "title": f"【{item.course_name}】{item.title}"[:240],
        "description": f"Canvas 作业\n课程：{item.course_name}\n原链接：{url}{note}\n\n{item.description}".rstrip(),
        "deadline_at": deadline,
    }


def sync_snapshot(db: Session, snapshot: CanvasSnapshot) -> dict:
    """Caller owns the transaction. Serialize imports, including first creation."""
    db.execute(text("SELECT pg_advisory_xact_lock(73621, 12001)"))
    report = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "conflicts": []}
    for item in snapshot.assignments:
        binding = db.scalar(select(CourseProviderBinding).where(
            CourseProviderBinding.provider == "canvas", CourseProviderBinding.external_id == str(item.course_id)))
        academic_course = db.get(AcademicCourse, binding.course_id) if binding else None
        source_id = f"sufe:{snapshot.user_id}:{item.course_id}:{item.assignment_id}"
        event = db.scalar(select(RawEvent).where(
            RawEvent.source == "canvas", RawEvent.source_event_id == source_id))
        task = db.scalar(select(Task).where(Task.source_event_id == event.id)) if event else None
        fields = task_fields(snapshot, item)
        metadata = item.model_dump(mode="json")
        metadata.update({"user_id": snapshot.user_id, "base_url": snapshot.base_url})
        if task is None and item.submission_state not in ("unsubmitted", "offline"):
            # Unknown status is not proof the user still needs to do this work.
            report["skipped"] += 1
            continue
        if event is None:
            event = RawEvent(source="canvas", source_event_id=source_id,
                             occurred_at=datetime.now(timezone.utc), event_type="assignment",
                             content=fields["title"], metadata_=metadata,
                             processing_status=ProcessingStatus.processed)
            db.add(event)
            db.flush()
        if task is None:
            task = Task(**fields, project_id=academic_course.project_id if academic_course else None,
                        source_event_id=event.id, status=TaskStatus.todo,
                        assignee_type="human")
            db.add(task)
            report["created"] += 1
        else:
            previous = CanvasAssignment.model_validate({
                key: value for key, value in event.metadata_.items()
                if key in CanvasAssignment.model_fields})
            old_fields = task_fields(snapshot, previous)
            changed = False
            for key, value in fields.items():
                if getattr(task, key) == value or old_fields[key] == value:
                    continue
                if getattr(task, key) == old_fields[key]:
                    setattr(task, key, value)
                    changed = True
                else:
                    report["conflicts"].append({"assignment_id": item.assignment_id, "field": key})
            # Completion is owned by the user; never reopen or auto-complete tasks.
            report["updated" if changed else "unchanged"] += 1
        event.metadata_ = metadata
        event.content = fields["title"]
        db.flush()
    return report

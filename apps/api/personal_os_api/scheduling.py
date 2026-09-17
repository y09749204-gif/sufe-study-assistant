from dataclasses import dataclass
from datetime import datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from personal_os_api.models import CalendarItem, CalendarItemType, CalendarStatus, Commitment, CommitmentDirection, CommitmentStatus, DailyState, Project, ProjectStatus, Task, TaskStatus


@dataclass(frozen=True)
class WorkCandidate:
    title: str
    priority: int
    duration_minutes: int
    deadline_at: datetime | None
    project_id: object | None
    task_id: object | None
    commitment_id: object | None
    reason: str


@dataclass(frozen=True)
class TimeRange:
    start: datetime
    end: datetime


def day_bounds(anchor: datetime, start_hour: int, end_hour: int) -> TimeRange:
    local = anchor.astimezone()
    start = datetime.combine(local.date(), time(hour=start_hour), tzinfo=local.tzinfo)
    end = datetime.combine(local.date(), time(hour=0), tzinfo=local.tzinfo) + timedelta(days=1 if end_hour == 24 else 0, hours=0 if end_hour == 24 else end_hour)
    return TimeRange(start=start, end=end)


def overlaps(left_start: datetime, left_end: datetime, right_start: datetime, right_end: datetime) -> bool:
    return left_start < right_end and right_start < left_end


def busy_ranges(items: list[CalendarItem]) -> list[TimeRange]:
    ranges = []
    for item in items:
        if item.status == CalendarStatus.cancelled or not item.start_at or not item.end_at:
            continue
        if item.locked or item.item_type in {CalendarItemType.hard_event, CalendarItemType.focus_block}:
            ranges.append(TimeRange(start=item.start_at, end=item.end_at))
    return sorted(ranges, key=lambda item: item.start)


def free_ranges(bounds: TimeRange, busy: list[TimeRange], now: datetime) -> list[TimeRange]:
    cursor = max(bounds.start, now)
    free: list[TimeRange] = []
    for block in busy:
        if block.end <= cursor:
            continue
        if block.start > cursor:
            free.append(TimeRange(cursor, min(block.start, bounds.end)))
        cursor = max(cursor, block.end)
        if cursor >= bounds.end:
            break
    if cursor < bounds.end:
        free.append(TimeRange(cursor, bounds.end))
    return [slot for slot in free if slot.end - slot.start >= timedelta(minutes=30)]


def open_work_candidates(db: Session, day_end: datetime) -> list[WorkCandidate]:
    tasks = list(
        db.scalars(
            select(Task).where(Task.assignee_type == "human", Task.status.in_([TaskStatus.inbox, TaskStatus.todo, TaskStatus.scheduled, TaskStatus.doing]))
        )
    )
    commitments = list(
        db.scalars(
            select(Commitment).where(
                Commitment.status == CommitmentStatus.open,
                Commitment.direction == CommitmentDirection.outbound,
            )
        )
    )
    latest_state = db.scalar(
        select(DailyState)
        .where(DailyState.state_date < day_end.date())
        .order_by(DailyState.state_date.desc())
        .limit(1)
    )
    carry_over_ids = {
        str(item.get("id"))
        for item in (latest_state.plan_summary.get("carry_over", []) if latest_state else [])
        if item.get("id")
    }

    candidates: list[WorkCandidate] = []
    for task in tasks:
        if task.assignee_type == "ai":
            continue
        if task.deadline_at and task.deadline_at < datetime.now().astimezone() - timedelta(days=1):
            urgency = 100
        elif task.deadline_at and task.deadline_at <= day_end:
            urgency = 60
        else:
            urgency = 0
        if str(task.id) in carry_over_ids:
            urgency += 40
        duration = task.estimated_minutes or 45
        reason = "截止时间临近" if task.deadline_at else "未完成的灵活任务"
        if str(task.id) in carry_over_ids:
            reason += "；最近每日复盘建议顺延"
        if task.estimated_minutes is None:
            reason += "；未设置预计时长，使用保守的 45 分钟默认值"
        candidates.append(
            WorkCandidate(
                title=task.title,
                priority=task.priority + urgency,
                duration_minutes=min(max(duration, 30), 120),
                deadline_at=task.deadline_at,
                project_id=task.project_id,
                task_id=task.id,
                commitment_id=None,
                reason=reason,
            )
        )

    task_titles = {task.title for task in tasks}
    for commitment in commitments:
        if commitment.title in task_titles:
            continue
        urgency = 80 if commitment.due_at and commitment.due_at <= day_end else 30
        candidates.append(
            WorkCandidate(
                title=commitment.title,
                priority=urgency,
                duration_minutes=45,
                deadline_at=commitment.due_at,
                project_id=commitment.project_id,
                task_id=None,
                commitment_id=commitment.id,
                reason="未完成的对外承诺；承诺优先于同优先级的灵活任务",
            )
        )

    return sorted(candidates, key=lambda item: (item.deadline_at or datetime.max.replace(tzinfo=day_end.tzinfo), -item.priority, item.title))


def validate_no_overlap(existing: list[TimeRange], created: list[CalendarItem]) -> list[str]:
    issues: list[str] = []
    ranges = existing + [TimeRange(item.start_at, item.end_at) for item in created if item.start_at and item.end_at]
    ranges = sorted(ranges, key=lambda item: item.start)
    for previous, current in zip(ranges, ranges[1:]):
        if overlaps(previous.start, previous.end, current.start, current.end):
            issues.append(f"检测到时间重叠：{previous.start.isoformat()} 与 {current.start.isoformat()}")
    return issues


def plan_day(db: Session, request_date: datetime | None, start_hour: int, end_hour: int, max_blocks: int, replan: bool = False) -> tuple[list[CalendarItem], list[str], list[str], datetime]:
    now = datetime.now().astimezone()
    anchor = request_date.astimezone() if request_date else now
    bounds = day_bounds(anchor, start_hour, end_hour)

    day_items = list(
        db.scalars(
            select(CalendarItem).where(
                CalendarItem.start_at < bounds.end,
                CalendarItem.end_at > bounds.start,
                CalendarItem.status != CalendarStatus.cancelled,
            )
        )
    )

    if replan:
        for item in day_items:
            if item.item_type == CalendarItemType.focus_block and item.flexible and not item.locked and item.status == CalendarStatus.planned and item.start_at and item.start_at > now:
                db.delete(item)
        db.flush()
        day_items = [item for item in day_items if not (item.item_type == CalendarItemType.focus_block and item.flexible and not item.locked and item.status == CalendarStatus.planned and item.start_at and item.start_at > now)]

    existing_busy = busy_ranges(day_items)
    free = free_ranges(bounds, existing_busy, now if anchor.date() == now.date() else bounds.start)
    candidates = open_work_candidates(db, bounds.end)
    created: list[CalendarItem] = []
    skipped: list[str] = []

    for candidate in candidates:
        if len(created) >= max_blocks:
            skipped.append(f"{candidate.title}：已达到时间块数量上限")
            continue
        if candidate.deadline_at and candidate.deadline_at < bounds.start:
            skipped.append(f"{candidate.title}：截止时间已过")
            continue

        duration = timedelta(minutes=candidate.duration_minutes)
        chosen_index = None
        for index, slot in enumerate(free):
            latest_end = min(slot.end, candidate.deadline_at) if candidate.deadline_at else slot.end
            if latest_end - slot.start >= duration:
                chosen_index = index
                break

        if chosen_index is None:
            skipped.append(f"{candidate.title}：截止前没有可用时间段")
            continue

        slot = free.pop(chosen_index)
        start_at = slot.start
        end_at = start_at + duration
        block = CalendarItem(
            item_type=CalendarItemType.focus_block,
            title=candidate.title,
            start_at=start_at,
            end_at=end_at,
            locked=False,
            flexible=True,
            project_id=candidate.project_id,
            task_id=candidate.task_id,
            commitment_id=candidate.commitment_id,
            source="scheduler",
            status=CalendarStatus.planned,
            scheduling_reason=f"{candidate.reason}。已安排到第一个可用时间段。",
            metadata_={"estimate_source": "task" if candidate.task_id else "commitment_default"},
        )
        db.add(block)
        created.append(block)

        if slot.end - end_at >= timedelta(minutes=30):
            free.insert(chosen_index, TimeRange(end_at, slot.end))

    issues = validate_no_overlap(existing_busy, created)
    if issues:
        db.rollback()
        return [], skipped, issues, bounds.start

    db.commit()
    for block in created:
        db.refresh(block)
    return created, skipped, issues, bounds.start


def schedule_project_time(db: Session, project_name: str, minutes: int, deadline_at: datetime) -> tuple[Project, list[CalendarItem], int]:
    project = db.scalar(select(Project).where(Project.name.ilike(project_name.strip())).limit(1))
    if not project:
        project = db.scalar(select(Project).where(Project.name.ilike(f"%{project_name.strip()}%")).limit(1))
    if not project:
        project = Project(name=project_name.strip(), status=ProjectStatus.active)
        db.add(project)
        db.flush()

    now = datetime.now().astimezone()
    deadline = deadline_at.astimezone()
    remaining = max(30, minutes)
    created: list[CalendarItem] = []
    cursor_date = now.date()
    while cursor_date <= deadline.date() and remaining > 0:
        anchor = datetime.combine(cursor_date, time(hour=9), tzinfo=now.tzinfo)
        bounds = day_bounds(anchor, 9, 21)
        if cursor_date == deadline.date():
            bounds = TimeRange(bounds.start, min(bounds.end, deadline))
        day_items = list(
            db.scalars(
                select(CalendarItem).where(
                    CalendarItem.start_at < bounds.end,
                    CalendarItem.end_at > bounds.start,
                    CalendarItem.status != CalendarStatus.cancelled,
                )
            )
        )
        slots = free_ranges(bounds, busy_ranges(day_items), now if cursor_date == now.date() else bounds.start)
        for slot in slots:
            while remaining > 0 and slot.end - slot.start >= timedelta(minutes=30):
                chunk = min(remaining, 120, int((slot.end - slot.start).total_seconds() // 60))
                if chunk < 30:
                    break
                end_at = slot.start + timedelta(minutes=chunk)
                block = CalendarItem(
                    item_type=CalendarItemType.focus_block,
                    title=project.name,
                    start_at=slot.start,
                    end_at=end_at,
                    locked=False,
                    flexible=True,
                    project_id=project.id,
                    source="ask",
                    status=CalendarStatus.planned,
                    scheduling_reason=f"按请求在 {deadline.isoformat()} 前安排 {minutes} 分钟。",
                    metadata_={"requested_total_minutes": minutes},
                )
                db.add(block)
                created.append(block)
                remaining -= chunk
                slot = TimeRange(end_at, slot.end)
        cursor_date += timedelta(days=1)

    db.commit()
    for block in created:
        db.refresh(block)
    return project, created, remaining

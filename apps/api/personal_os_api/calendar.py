from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from personal_os_api.db import get_db
from personal_os_api.models import CalendarItem, CalendarItemType
from personal_os_api.schemas import CalendarItemCreate, CalendarItemRead, CalendarItemUpdate, DayPlanRequest, DayPlanResult, SchedulingIssue
from personal_os_api.scheduling import plan_day

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


def apply_type_defaults(item: CalendarItem) -> None:
    if item.item_type == CalendarItemType.hard_event:
        item.locked = True
        item.flexible = False
    if item.item_type == CalendarItemType.focus_block and not item.locked:
        item.flexible = True


def validate_item_range(item: CalendarItem) -> None:
    if item.start_at and item.end_at and item.end_at <= item.start_at:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="end_at must be later than start_at")


def in_range(query: Select[tuple[CalendarItem]], start: datetime | None, end: datetime | None) -> Select[tuple[CalendarItem]]:
    if start:
        query = query.where((CalendarItem.end_at.is_(None)) | (CalendarItem.end_at >= start))
    if end:
        query = query.where((CalendarItem.start_at.is_(None)) | (CalendarItem.start_at <= end))
    return query


@router.get("", response_model=list[CalendarItemRead])
def list_calendar_items(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[CalendarItem]:
    query = in_range(select(CalendarItem), start, end).order_by(CalendarItem.start_at.asc().nulls_last(), CalendarItem.created_at.asc())
    return list(db.scalars(query))


@router.post("/items", response_model=CalendarItemRead, status_code=status.HTTP_201_CREATED)
def create_calendar_item(payload: CalendarItemCreate, db: Session = Depends(get_db)) -> CalendarItem:
    item = CalendarItem(
        item_type=payload.item_type,
        title=payload.title,
        description=payload.description,
        start_at=payload.start_at,
        end_at=payload.end_at,
        all_day=payload.all_day,
        locked=payload.locked,
        flexible=payload.flexible,
        project_id=payload.project_id,
        task_id=payload.task_id,
        commitment_id=payload.commitment_id,
        source=payload.source,
        source_event_id=payload.source_event_id,
        status=payload.status,
        actual_seconds=payload.actual_seconds,
        scheduling_reason=payload.scheduling_reason,
        metadata_=payload.metadata,
    )
    apply_type_defaults(item)
    validate_item_range(item)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/items/{item_id}", response_model=CalendarItemRead)
def update_calendar_item(item_id: UUID, payload: CalendarItemUpdate, db: Session = Depends(get_db)) -> CalendarItem:
    item = db.get(CalendarItem, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calendar item not found")

    update_data = payload.model_dump(exclude_unset=True)
    if "metadata" in update_data:
        update_data["metadata_"] = update_data.pop("metadata")
    for key, value in update_data.items():
        setattr(item, key, value)
    apply_type_defaults(item)
    validate_item_range(item)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_calendar_item(item_id: UUID, db: Session = Depends(get_db)) -> None:
    item = db.get(CalendarItem, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calendar item not found")
    db.delete(item)
    db.commit()


@router.post("/plan-day", response_model=DayPlanResult)
def plan_calendar_day(payload: DayPlanRequest, db: Session = Depends(get_db)) -> DayPlanResult:
    created, skipped, issues, plan_date = plan_day(
        db,
        request_date=payload.date,
        start_hour=payload.workday_start_hour,
        end_hour=payload.workday_end_hour,
        max_blocks=payload.max_blocks,
        replan=False,
    )
    return DayPlanResult(
        date=plan_date,
        created_blocks=created,
        skipped=skipped,
        issues=[SchedulingIssue(severity="error", message=message) for message in issues],
    )


@router.post("/replan", response_model=DayPlanResult)
def replan_calendar_day(payload: DayPlanRequest, db: Session = Depends(get_db)) -> DayPlanResult:
    created, skipped, issues, plan_date = plan_day(
        db,
        request_date=payload.date,
        start_hour=payload.workday_start_hour,
        end_hour=payload.workday_end_hour,
        max_blocks=payload.max_blocks,
        replan=True,
    )
    return DayPlanResult(
        date=plan_date,
        created_blocks=created,
        skipped=skipped,
        issues=[SchedulingIssue(severity="error", message=message) for message in issues],
    )

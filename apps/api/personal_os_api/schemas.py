from datetime import date, datetime
from uuid import UUID
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_os_api.models import (
    CalendarItemType,
    CalendarStatus,
    CommitmentDirection,
    CommitmentStatus,
    InboxStatus,
    ProjectStatus,
    TaskStatus,
)


class CalendarItemBase(BaseModel):
    item_type: CalendarItemType
    title: str = Field(min_length=1, max_length=240)
    description: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    all_day: bool = False
    locked: bool = False
    flexible: bool = False
    project_id: UUID | None = None
    task_id: UUID | None = None
    commitment_id: UUID | None = None
    source: str = "manual"
    source_event_id: UUID | None = None
    status: CalendarStatus = CalendarStatus.planned
    actual_seconds: int | None = Field(default=None, ge=0)
    scheduling_reason: str | None = None
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_time_range(self) -> "CalendarItemBase":
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValueError("end_at must be later than start_at")
        return self


class CalendarItemCreate(CalendarItemBase):
    pass


class CalendarItemUpdate(BaseModel):
    item_type: CalendarItemType | None = None
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    all_day: bool | None = None
    locked: bool | None = None
    flexible: bool | None = None
    project_id: UUID | None = None
    task_id: UUID | None = None
    commitment_id: UUID | None = None
    source: str | None = None
    source_event_id: UUID | None = None
    status: CalendarStatus | None = None
    actual_seconds: int | None = Field(default=None, ge=0)
    scheduling_reason: str | None = None
    metadata: dict | None = None


class CalendarItemRead(CalendarItemBase):
    id: UUID
    metadata: dict = Field(default_factory=dict, validation_alias="metadata_")
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)










































class DayPlanRequest(BaseModel):
    date: datetime | None = None
    workday_start_hour: int = Field(default=9, ge=0, le=23)
    workday_end_hour: int = Field(default=21, ge=1, le=24)
    max_blocks: int = Field(default=4, ge=1, le=8)

    @model_validator(mode="after")
    def validate_workday(self) -> "DayPlanRequest":
        if self.workday_end_hour <= self.workday_start_hour:
            raise ValueError("workday_end_hour must be later than workday_start_hour")
        return self


class SchedulingIssue(BaseModel):
    severity: str
    message: str


class DayPlanResult(BaseModel):
    date: datetime
    created_blocks: list[CalendarItemRead]
    skipped: list[str]
    issues: list[SchedulingIssue]



























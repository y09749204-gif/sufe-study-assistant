import enum
import uuid
from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, SmallInteger, String, Text, Time, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from personal_os_api.db import Base


class CalendarItemType(str, enum.Enum):
    hard_event = "hard_event"
    focus_block = "focus_block"
    deadline_marker = "deadline_marker"
    commitment_marker = "commitment_marker"


class CalendarStatus(str, enum.Enum):
    planned = "planned"
    active = "active"
    done = "done"
    cancelled = "cancelled"


class DeviceType(str, enum.Enum):
    windows = "windows"
    mac = "mac"
    iphone = "iphone"
    ipad = "ipad"
    other = "other"


class DeviceRole(str, enum.Enum):
    core = "core"
    work = "work"
    mobile = "mobile"
    capture = "capture"


class ProcessingStatus(str, enum.Enum):
    pending = "pending"
    processed = "processed"
    ignored = "ignored"
    error = "error"


class ProjectStatus(str, enum.Enum):
    inbox = "inbox"
    active = "active"
    paused = "paused"
    done = "done"
    archived = "archived"


class TaskStatus(str, enum.Enum):
    inbox = "inbox"
    todo = "todo"
    scheduled = "scheduled"
    doing = "doing"
    done = "done"
    cancelled = "cancelled"


class CommitmentDirection(str, enum.Enum):
    outbound = "outbound"
    inbound = "inbound"


class CommitmentStatus(str, enum.Enum):
    open = "open"
    fulfilled = "fulfilled"
    missed = "missed"
    cancelled = "cancelled"


class InboxStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    edited = "edited"


class AutomationProfile(str, enum.Enum):
    full = "full"
    hybrid = "hybrid"
    calendar_only = "calendar_only"


class DeliveryMode(str, enum.Enum):
    in_person = "in_person"
    online_live = "online_live"
    online_async = "online_async"
    unknown = "unknown"


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text)
    device_type: Mapped[DeviceType] = mapped_column(Enum(DeviceType, name="device_type"))
    role: Mapped[DeviceRole] = mapped_column(Enum(DeviceRole, name="device_role"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RawEvent(Base):
    __tablename__ = "raw_events"
    __table_args__ = (UniqueConstraint("source", "source_event_id", name="uq_raw_events_source_event_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[str] = mapped_column(Text)
    source_event_id: Mapped[str | None] = mapped_column(Text)
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("devices.id"))
    event_type: Mapped[str] = mapped_column(Text)
    actor_type: Mapped[str | None] = mapped_column(Text)
    actor_external_id: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, server_default="{}")
    content_hash: Mapped[str | None] = mapped_column(Text)
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, name="processing_status"), default=ProcessingStatus.pending, server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Person(Base):
    __tablename__ = "people"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    importance: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PersonIdentity(Base):
    __tablename__ = "person_identities"
    __table_args__ = (UniqueConstraint("platform", "external_id", name="uq_person_identities_platform_external_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    platform: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, server_default="{}")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[ProjectStatus] = mapped_column(Enum(ProjectStatus, name="project_status"), default=ProjectStatus.inbox, server_default="inbox")
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    goal: Mapped[str | None] = mapped_column(Text)
    next_action: Mapped[str | None] = mapped_column(Text)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AcademicTerm(Base):
    __tablename__ = "academic_terms"
    __table_args__ = (UniqueConstraint("code", name="uq_academic_terms_code"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(Text)
    starts_on: Mapped[date] = mapped_column(Date)
    teaching_weeks: Mapped[int] = mapped_column(SmallInteger)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai", server_default="Asia/Shanghai")
    status: Mapped[str] = mapped_column(String(24), default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AcademicCourse(Base):
    __tablename__ = "academic_courses"
    __table_args__ = (UniqueConstraint("term_id", "course_code", name="uq_academic_courses_term_code"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    term_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("academic_terms.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), unique=True)
    course_code: Mapped[str] = mapped_column(String(32))
    section_code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(Text)
    credits: Mapped[int] = mapped_column(SmallInteger)
    teachers: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    campus: Mapped[str | None] = mapped_column(Text)
    automation_profile: Mapped[AutomationProfile] = mapped_column(Enum(AutomationProfile, name="academic_automation_profile"))
    status: Mapped[str] = mapped_column(String(24), default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CourseProviderBinding(Base):
    __tablename__ = "course_provider_bindings"
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_course_provider_external_id"),
        UniqueConstraint("course_id", "provider", "external_id", name="uq_course_provider_binding"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("academic_courses.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(24), default="active", server_default="active")
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, server_default="{}")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CourseMeetingRule(Base):
    __tablename__ = "course_meeting_rules"
    __table_args__ = (UniqueConstraint("course_id", "source_key", name="uq_course_meeting_rule_source"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("academic_courses.id", ondelete="CASCADE"), index=True)
    source_key: Mapped[str] = mapped_column(String(120))
    weekday: Mapped[int] = mapped_column(SmallInteger)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    weeks: Mapped[list] = mapped_column(JSONB)
    delivery_mode: Mapped[DeliveryMode] = mapped_column(Enum(DeliveryMode, name="academic_delivery_mode"))
    location: Mapped[str | None] = mapped_column(Text)
    instructors: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    source: Mapped[str] = mapped_column(String(40), default="timetable", server_default="timetable")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClassSession(Base):
    __tablename__ = "class_sessions"
    __table_args__ = (UniqueConstraint("course_id", "source_key", name="uq_class_sessions_source"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("academic_courses.id", ondelete="CASCADE"), index=True)
    meeting_rule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("course_meeting_rules.id", ondelete="SET NULL"))
    calendar_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("calendar_items.id", ondelete="CASCADE"), unique=True)
    source_key: Mapped[str] = mapped_column(String(160))
    week_number: Mapped[int] = mapped_column(SmallInteger)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivery_mode: Mapped[DeliveryMode] = mapped_column(Enum(DeliveryMode, name="academic_delivery_mode"))
    location: Mapped[str | None] = mapped_column(Text)
    instructors: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    status: Mapped[str] = mapped_column(String(24), default="scheduled", server_default="scheduled")
    source: Mapped[str] = mapped_column(String(40), default="timetable", server_default="timetable")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SyllabusSnapshot(Base):
    __tablename__ = "syllabus_snapshots"
    __table_args__ = (UniqueConstraint("course_id", "provider", "content_hash", name="uq_syllabus_snapshot_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("academic_courses.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    content_hash: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str] = mapped_column(Text)
    parsed: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CourseMessage(Base):
    __tablename__ = "course_messages"
    __table_args__ = (UniqueConstraint("provider_binding_id", "external_message_id", name="uq_course_message_external"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("academic_courses.id", ondelete="CASCADE"), index=True)
    provider_binding_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("course_provider_bindings.id", ondelete="CASCADE"))
    external_message_id: Mapped[str] = mapped_column(Text)
    sender_id: Mapped[str | None] = mapped_column(Text)
    sender_name: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    direction: Mapped[str] = mapped_column(String(16))
    text: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CourseResource(Base):
    __tablename__ = "course_resources"
    __table_args__ = (UniqueConstraint("course_id", "provider", "external_id", "content_hash", name="uq_course_resource_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("academic_courses.id", ondelete="CASCADE"), index=True)
    class_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("class_sessions.id", ondelete="SET NULL"))
    course_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("course_messages.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(Text)
    byte_size: Mapped[int | None] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64))
    local_path: Mapped[str | None] = mapped_column(Text)
    availability: Mapped[str] = mapped_column(String(24), default="available", server_default="available")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AttachmentAcquisition(Base):
    __tablename__ = "attachment_acquisitions"
    __table_args__ = (
        UniqueConstraint("resource_id", "active_key", name="uq_attachment_acquisition_active"),
        UniqueConstraint("idempotency_key", name="uq_attachment_acquisition_idempotency"),
        UniqueConstraint("claim_token", name="uq_attachment_acquisitions_claim_token"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("course_resources.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", server_default="queued", index=True)
    strategy: Mapped[str] = mapped_column(String(24), default="passive_cache", server_default="passive_cache")
    trigger: Mapped[str] = mapped_column(String(24), default="manual", server_default="manual", index=True)
    interaction_mode: Mapped[str] = mapped_column(String(24), default="background", server_default="background", index=True)
    automation_stage: Mapped[str | None] = mapped_column(String(32), index=True)
    active_key: Mapped[str | None] = mapped_column(String(16), default="active", server_default="active")
    idempotency_key: Mapped[str | None] = mapped_column(String(200), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    client_version: Mapped[str | None] = mapped_column(String(40))
    error_code: Mapped[str | None] = mapped_column(String(80))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, server_default="{}")
    match_evidence: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    countdown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    claim_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AttachmentAcquisitionEvent(Base):
    __tablename__ = "attachment_acquisition_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    acquisition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("attachment_acquisitions.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(32), index=True)
    result_code: Mapped[str | None] = mapped_column(String(80))
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class CourseRecording(Base):
    """A replay discovered from a course provider; media is always local and temporary."""
    __tablename__ = "course_recordings"
    __table_args__ = (UniqueConstraint("provider", "external_id", "content_hash", name="uq_course_recording_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("academic_courses.id", ondelete="CASCADE"), index=True)
    class_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("class_sessions.id", ondelete="SET NULL"), index=True)
    provider_binding_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("course_provider_bindings.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64))
    local_path: Mapped[str | None] = mapped_column(Text)
    media_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    availability: Mapped[str] = mapped_column(String(24), default="discovered", server_default="discovered")
    error: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RecordingTranscript(Base):
    __tablename__ = "recording_transcripts"
    __table_args__ = (UniqueConstraint("recording_id", "content_hash", name="uq_recording_transcript_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recording_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("course_recordings.id", ondelete="CASCADE"), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(40))
    language: Mapped[str | None] = mapped_column(String(24))
    text: Mapped[str] = mapped_column(Text)
    segments: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    model_name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LessonReview(Base):
    __tablename__ = "lesson_reviews"
    __table_args__ = (UniqueConstraint("recording_id", "content_hash", name="uq_lesson_review_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recording_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("course_recordings.id", ondelete="CASCADE"), index=True)
    transcript_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("recording_transcripts.id", ondelete="CASCADE"))
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="ready", server_default="ready")
    summary: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    model_name: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CoursewarePage(Base):
    """Durable, searchable visual index for a locally archived courseware file."""
    __tablename__ = "courseware_pages"
    __table_args__ = (UniqueConstraint("resource_id", "page_number", "image_hash", name="uq_courseware_page_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("academic_courses.id", ondelete="CASCADE"), index=True)
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("course_resources.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(Text)
    image_path: Mapped[str] = mapped_column(Text)
    image_hash: Mapped[str] = mapped_column(String(64), index=True)
    ocr_text: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecordingSlideSegment(Base):
    """A time range in a recording and the courseware page visible in that range."""
    __tablename__ = "recording_slide_segments"
    __table_args__ = (UniqueConstraint("recording_id", "start_seconds", "end_seconds", "frame_hash", name="uq_recording_slide_segment"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recording_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("course_recordings.id", ondelete="CASCADE"), index=True)
    courseware_page_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("courseware_pages.id", ondelete="SET NULL"), index=True)
    captured_resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("course_resources.id", ondelete="SET NULL"), index=True)
    start_seconds: Mapped[int] = mapped_column(Integer)
    end_seconds: Mapped[int] = mapped_column(Integer)
    frame_path: Mapped[str] = mapped_column(Text)
    frame_hash: Mapped[str] = mapped_column(String(64), index=True)
    visual_score: Mapped[float] = mapped_column(default=0.0, server_default="0")
    ocr_score: Mapped[float] = mapped_column(default=0.0, server_default="0")
    confidence: Mapped[float] = mapped_column(default=0.0, server_default="0")
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending", index=True)
    candidates: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Task(Base):
    __tablename__ = "tasks"

    source_event: Mapped[RawEvent | None] = relationship("RawEvent", viewonly=True)

    @property
    def source(self) -> str:
        if self.source_event_id is None:
            return "manual"
        return self.source_event.source if self.source_event is not None else "unknown"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"))
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus, name="task_status"), default=TaskStatus.inbox, server_default="inbox")
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    estimated_minutes: Mapped[int | None] = mapped_column(Integer)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("raw_events.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assignee_type: Mapped[str] = mapped_column(String(16), default="human", server_default="human")
    ai_executor: Mapped[str | None] = mapped_column(String(64))
    automation_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    automation_state: Mapped[str | None] = mapped_column(String(24))
    automation_config: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    automation_note: Mapped[str | None] = mapped_column(Text)
    execution_kind: Mapped[str | None] = mapped_column(String(40))
    execution_config: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    execution_state: Mapped[str | None] = mapped_column(String(24))
    execution_note: Mapped[str | None] = mapped_column(Text)
    execution_result: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")










class Commitment(Base):
    __tablename__ = "commitments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    direction: Mapped[CommitmentDirection] = mapped_column(Enum(CommitmentDirection, name="commitment_direction"))
    person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"))
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[CommitmentStatus] = mapped_column(
        Enum(CommitmentStatus, name="commitment_status"), default=CommitmentStatus.open, server_default="open"
    )
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("raw_events.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InboxCandidate(Base):
    __tablename__ = "inbox_candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB)
    confidence: Mapped[float] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("raw_events.id"))
    status: Mapped[InboxStatus] = mapped_column(Enum(InboxStatus, name="inbox_status"), default=InboxStatus.pending, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DailyState(Base):
    __tablename__ = "daily_states"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    state_date: Mapped[date] = mapped_column(Date, unique=True)
    summary: Mapped[str | None] = mapped_column(Text)
    top_priorities: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    open_commitments: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    waiting_for: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    calendar_summary: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    project_summary: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    plan_summary: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    actual_summary: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ActivitySession(Base):
    __tablename__ = "activity_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("devices.id"))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    app_name: Mapped[str | None] = mapped_column(Text)
    window_title: Mapped[str | None] = mapped_column(Text)
    url_domain: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"))
    active_seconds: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text, default="activitywatch", server_default="activitywatch")
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProviderSyncState(Base):
    __tablename__ = "provider_sync_states"
    __table_args__ = (UniqueConstraint("provider", "cursor_key", name="uq_provider_sync_states_provider_cursor_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(Text)
    cursor_key: Mapped[str] = mapped_column(Text)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (UniqueConstraint("job_name", "run_key", name="uq_job_runs_job_name_run_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_name: Mapped[str] = mapped_column(Text)
    run_key: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="running", server_default="running")
    attempt: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text)


class CalendarItem(Base):
    __tablename__ = "calendar_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_type: Mapped[CalendarItemType] = mapped_column(Enum(CalendarItemType, name="calendar_item_type"))
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str | None] = mapped_column(Text)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    all_day: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    flexible: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id"))
    commitment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("commitments.id"))
    source: Mapped[str] = mapped_column(String(80), default="manual", server_default="manual")
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("raw_events.id"))
    status: Mapped[CalendarStatus] = mapped_column(
        Enum(CalendarStatus, name="calendar_status"), default=CalendarStatus.planned, server_default="planned"
    )
    actual_seconds: Mapped[int | None] = mapped_column(Integer)
    scheduling_reason: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class KzktQueueControl(Base):
    __tablename__ = "kzkt_queue_control"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class KzktQueueTask(Base):
    __tablename__ = "kzkt_queue_tasks"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dedupe_key: Mapped[str] = mapped_column(Text, unique=True)
    course_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("academic_courses.id"), index=True)
    recording_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("course_recordings.id"))
    external_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(24), default="recording")
    stage: Mapped[str] = mapped_column(String(24), default="discover")
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    batches: Mapped[list] = mapped_column(JSONB, default=list)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    taught_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

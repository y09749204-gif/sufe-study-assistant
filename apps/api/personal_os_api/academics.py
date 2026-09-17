"""Academic center API and deterministic provider ingestion."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid
import zipfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pypdf import PdfReader
from sqlalchemy import select, text as sql_text
from sqlalchemy.orm import Session

from personal_os_api.config import get_settings
from personal_os_api.db import get_db
from personal_os_api.models import (
    AcademicCourse, AcademicTerm, AttachmentAcquisition, AutomationProfile, CalendarItem, CalendarItemType,
    CalendarStatus, ClassSession, CourseMeetingRule, CourseMessage,
    CourseProviderBinding, CourseResource, DeliveryMode, InboxCandidate, InboxStatus,
    ProcessingStatus, Project, ProjectStatus, RawEvent, SyllabusSnapshot, Task, TaskStatus,
    CourseRecording, RecordingTranscript, LessonReview, CoursewarePage, RecordingSlideSegment,
)
from personal_os_api.canvas_import import CanvasSnapshot, sync_snapshot as sync_assignment_snapshot
from personal_os_api.kzkt import cleanup_expired_media, ingest_snapshot as ingest_kzkt_snapshot, process_recordings_serialized
from personal_os_api.lesson_knowledge import allowed_path as allowed_lesson_path, confirm_slide_segment, process_recording_slides

router = APIRouter(prefix="/api/academics", tags=["academics"])
SHANGHAI = ZoneInfo("Asia/Shanghai")
ALLOWED_RESOURCE_EXTENSIONS = {".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".md", ".png", ".jpg", ".jpeg", ".gif", ".zip"}
DANGEROUS_ARCHIVE_EXTENSIONS = {".exe", ".dll", ".com", ".bat", ".cmd", ".ps1", ".js", ".vbs", ".msi", ".scr", ".lnk"}




def weeks(start: int, end: int, parity: str | None = None) -> list[int]:
    values = list(range(start, end + 1))
    if parity == "odd":
        return [value for value in values if value % 2]
    if parity == "even":
        return [value for value in values if value % 2 == 0]
    return values




def parse_clock(value: str) -> time:
    return time.fromisoformat(value)


def storage_root() -> Path:
    root = Path(get_settings().academic_storage_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_part(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return value[:100] or "untitled"


def parse_syllabus(text: str, teachers: list[str]) -> dict:
    """Conservative extraction; raw text remains the authoritative version."""
    compact = re.sub(r"[ \t]+", " ", text).strip()
    result: dict = {"teachers": teachers, "assessment": [], "week_topics": []}
    for label, value in re.findall(r"([^\d\n。；:：]{1,32}?)\s*[:：]?\s*(\d{1,3})\s*%", compact):
        result["assessment"].append({"component": label.strip(" ，,、"), "percent": int(value)})
    for week_no, topic in re.findall(r"第?\s*(\d{1,2})\s*周\s*[:：、-]?\s*([^\n。；]{2,100})", compact):
        result["week_topics"].append({"week": int(week_no), "topic": topic.strip()})
    for key, heading in (("objectives", "教学目标"), ("textbooks", "教材"), ("reading", "阅读")):
        match = re.search(rf"{heading}\s*[:：]?\s*(.{{2,300}}?)(?=\n\s*(?:教学目标|教学内容|教材|参考书|阅读|考核|成绩|第\s*\d+\s*周)\s*[:：]?|$)", compact, re.S)
        if match: result[key] = match.group(1).strip()
    return result


def is_syllabus_title(title: str) -> bool:
    normalized = re.sub(r"[\s_+-]+", "", title).lower()
    return "教学大纲" in normalized or "课程大纲" in normalized or "syllabus" in normalized


def convert_office_file(source: Path, destination: Path) -> None:
    """Convert a local Office document in an isolated process."""
    converter = Path(__file__).with_name("office_preview.py")
    try:
        result = subprocess.run([sys.executable, str(converter), str(source), str(destination)], capture_output=True,
            text=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("Office preview conversion timed out") from exc
    if result.returncode != 0 or not destination.is_file():
        detail = (result.stderr or result.stdout or "Office did not create a preview").strip()
        raise RuntimeError(detail)


def text_is_corrupted(text: str) -> bool:
    visible = sum(not char.isspace() for char in text)
    return not text.strip() or (visible > 0 and text.count("�") / visible > 0.01)


def ocr_pdf_text(pdf_path: Path, cache_key: str) -> str:
    """OCR a PDF locally with Windows Chinese OCR when its embedded text is unusable."""
    image_dir = storage_root() / ".runtime" / "previews" / f"ocr-{cache_key}"
    image_dir.mkdir(parents=True, exist_ok=True)
    if not any(image_dir.glob("page-*.png")):
        renderer = shutil.which("pdftoppm")
        if not renderer:
            raise RuntimeError("pdftoppm is unavailable for syllabus OCR")
        result = subprocess.run([renderer, "-png", "-r", "150", str(pdf_path), str(image_dir / "page")],
            capture_output=True, text=True, timeout=180, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "PDF rendering failed").strip())
    script = Path(__file__).with_name("windows_ocr.ps1")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
        "-ImagePath", str(image_dir)], capture_output=True, encoding="utf-8", errors="replace", timeout=240,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "Windows OCR failed").strip())
    text = result.stdout.lstrip("\ufeff").strip()
    return re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)


def extract_document_text(path: Path) -> str:
    """Extract authoritative text from supported syllabus files without modifying them."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "\n\n".join((page.extract_text() or "").strip() for page in PdfReader(path).pages).strip()
    if suffix == ".docx":
        with zipfile.ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
        lines: list[str] = []
        for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
            text = "".join(node.text or "" for node in paragraph.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")).strip()
            if text: lines.append(text)
        return "\n".join(lines).strip()
    if suffix == ".doc":
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        converted = storage_root() / ".runtime" / "previews" / f"syllabus-text-{digest}.docx"
        if not converted.is_file():
            convert_office_file(path.resolve(), converted)
        text = extract_document_text(converted)
        if not text_is_corrupted(text):
            return text
        preview = storage_root() / ".runtime" / "previews" / f"syllabus-ocr-{digest}.pdf"
        if not preview.is_file():
            convert_office_file(path.resolve(), preview)
        return ocr_pdf_text(preview, digest)
    return ""


def course_directory(course: AcademicCourse) -> Path:
    path = storage_root() / str(course.term_id) / f"{safe_part(course.section_code)}-{safe_part(course.name)}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resource_file(resource: CourseResource) -> Path:
    if resource.availability != "available" or not resource.local_path:
        raise HTTPException(409, "课程资料尚未缓存到本机")
    root = storage_root()
    original = Path(resource.local_path)
    path = original.resolve()
    if original.is_symlink() or not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "课程资料文件不存在")
    return path


def preview_file(resource: CourseResource) -> tuple[Path, str]:
    source = resource_file(resource)
    suffix = source.suffix.lower()
    direct_types = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8", ".csv": "text/plain; charset=utf-8"}
    if suffix in direct_types:
        return source, direct_types[suffix]
    if suffix not in {".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}:
        raise HTTPException(415, "此文件类型暂不支持应用内预览")
    cache_key = resource.content_hash or hashlib.sha256(f"{source}:{source.stat().st_mtime_ns}".encode()).hexdigest()
    destination = storage_root() / ".runtime" / "previews" / f"{cache_key}.pdf"
    if not destination.is_file():
        try:
            convert_office_file(source, destination)
        except TimeoutError as exc:
            raise HTTPException(504, "Office 预览转换超时") from exc
        except RuntimeError as exc:
            raise HTTPException(422, "Office 文件暂时无法转换为预览") from exc
    return destination, "application/pdf"




def course_payload(db: Session, course: AcademicCourse, detail: bool = False, lightweight: bool = False) -> dict:
    bindings = list(db.scalars(select(CourseProviderBinding).where(CourseProviderBinding.course_id == course.id)))
    sessions = list(db.scalars(select(ClassSession).where(ClassSession.course_id == course.id).order_by(ClassSession.start_at))) if detail else []
    latest = db.scalar(select(SyllabusSnapshot).where(SyllabusSnapshot.course_id == course.id).order_by(SyllabusSnapshot.fetched_at.desc()).limit(1))
    result = {"id": str(course.id), "term_id": str(course.term_id), "project_id": str(course.project_id) if course.project_id else None,
        "course_code": course.course_code, "section_code": course.section_code, "name": course.name, "credits": course.credits,
        "teachers": course.teachers, "campus": course.campus, "automation_profile": course.automation_profile.value, "status": course.status,
        "bindings": [{"id": str(item.id), "provider": item.provider, "external_id": item.external_id, "state": item.state, "last_synced_at": item.last_synced_at} for item in bindings],
        "syllabus": {"id": str(latest.id), "parsed": latest.parsed, "raw_text": latest.raw_text, "fetched_at": latest.fetched_at,
            "source_url": latest.source_url, "source_title": (latest.parsed or {}).get("source_title")} if latest else None}
    if detail:
        result["sessions"] = [session_payload(item) for item in sessions]
        resources = list(db.scalars(select(CourseResource).where(CourseResource.course_id == course.id, CourseResource.provider != "kzkt_capture").order_by(CourseResource.created_at.desc())))
        messages = db.scalars(select(CourseMessage).where(CourseMessage.course_id == course.id).order_by(CourseMessage.sent_at.desc()).limit(250))
        visible_resources: list[CourseResource] = []
        resource_indexes: dict[tuple[str, int | None], int] = {}
        for item in resources:
            key = (item.title, item.byte_size)
            prior_index = resource_indexes.get(key)
            if prior_index is None:
                resource_indexes[key] = len(visible_resources)
                visible_resources.append(item)
            elif visible_resources[prior_index].availability != "available" and item.availability == "available":
                visible_resources[prior_index] = item
        result["resources"] = [{"id": str(item.id), "provider": item.provider, "title": item.title,
            "availability": item.availability, "byte_size": item.byte_size, "created_at": item.created_at} for item in visible_resources]
        result["syllabus_files"] = [{"id": str(item.id), "provider": item.provider, "title": item.title,
            "availability": item.availability, "byte_size": item.byte_size, "created_at": item.created_at,
            "source_url": item.source_url} for item in visible_resources if is_syllabus_title(item.title)]
        result["messages"] = [message_payload(db, item) for item in messages if not (item.metadata_ or {}).get("is_system_event")][:100]
        recordings = list(db.scalars(select(CourseRecording).where(CourseRecording.course_id == course.id).order_by(CourseRecording.created_at.desc())))
        result["recordings"] = []
        for recording in recordings:
            result["recordings"].append(recording_payload(db, recording, lightweight))
        result["recordings"].sort(key=lambda r: r.get("taught_at") or "", reverse=True)
    return result


def recording_payload(db, recording, lightweight=False):
    if lightweight:
        return {"id":str(recording.id),"title":recording.title,"availability":recording.availability,"error":recording.error,"taught_at":(recording.metadata_ or {}).get("taught_at"),"session_match":(recording.metadata_ or {}).get("session_match"),"review":None,"transcript":None,"slide_segments":[]}
    review = db.scalar(select(LessonReview).where(LessonReview.recording_id == recording.id).order_by(LessonReview.created_at.desc()).limit(1))
    transcript = db.scalar(select(RecordingTranscript).where(RecordingTranscript.recording_id == recording.id).order_by(RecordingTranscript.created_at.desc()).limit(1))
    slide_segments = list(db.scalars(select(RecordingSlideSegment).where(RecordingSlideSegment.recording_id == recording.id).order_by(RecordingSlideSegment.start_seconds)))
    return {"id": str(recording.id), "title": recording.title, "source_url": recording.source_url,
        "published_at": recording.published_at, "availability": recording.availability, "error": recording.error,
        "session_id": str(recording.class_session_id) if recording.class_session_id else None,
        "taught_at": (recording.metadata_ or {}).get("taught_at"),
        "session_match": (recording.metadata_ or {}).get("session_match", "pending"),
        "review": {"id": str(review.id), "status": review.status, "summary": review.summary, "created_at": review.created_at} if review else None,
        "transcript": {"text": transcript.text, "segments": transcript.segments, "source": transcript.source} if transcript else None,
        "slide_segments": [{"id": str(segment.id), "start_seconds": segment.start_seconds, "end_seconds": segment.end_seconds,
            "status": segment.status, "confidence": segment.confidence, "visual_score": segment.visual_score, "ocr_score": segment.ocr_score,
            "courseware_page": {"id": str(page.id), "page_number": page.page_number, "title": page.title,
                "image_url": f"/api/academics/courseware-pages/{page.id}/image"} if (page := db.get(CoursewarePage, segment.courseware_page_id)) else None,
            "capture_image_url": f"/api/academics/recording-slides/{segment.id}/image" if segment.status in {"pending", "captured"} else None,
            "candidates": segment.candidates} for segment in slide_segments]}


def session_payload(item: ClassSession) -> dict:
    return {"id": str(item.id), "course_id": str(item.course_id), "calendar_item_id": str(item.calendar_item_id), "week_number": item.week_number,
        "start_at": item.start_at, "end_at": item.end_at, "delivery_mode": item.delivery_mode.value, "location": item.location,
        "instructors": item.instructors, "status": item.status, "source": item.source}


def message_payload(db: Session, item: CourseMessage) -> dict:
    resources = list(db.scalars(select(CourseResource).where(CourseResource.course_message_id == item.id).order_by(CourseResource.created_at)))
    attachment_payload = [{"id": str(resource.id), "title": resource.title, "availability": resource.availability,
        "byte_size": resource.byte_size} for resource in resources]
    availability_labels = {"available": "已归档", "not_cached": "未缓存", "blocked_type": "类型不允许"}
    if item.text:
        display_text = item.text
    elif resources:
        display_text = "；".join(f"文件：{resource.title}（{availability_labels.get(resource.availability, resource.availability)}）" for resource in resources)
    else:
        content_type = (item.metadata_ or {}).get("content_type")
        display_text = f"暂不支持的企微消息（类型 {content_type}）" if content_type is not None else "暂不支持的企微消息"
    return {"id": str(item.id), "sender_name": item.sender_name, "sent_at": item.sent_at, "text": item.text,
        "display_text": display_text, "direction": item.direction, "content_type": (item.metadata_ or {}).get("content_type"),
        "parse_status": (item.metadata_ or {}).get("parse_status"), "attachments": attachment_payload}


class CanvasResourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    mime_type: str | None = None
    byte_size: int | None = Field(default=None, ge=0)
    source_url: str | None = None
    modified_at: datetime | None = None
    local_path: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class CanvasCourseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    course_id: str
    name: str
    syllabus_text: str = ""
    syllabus_url: str | None = None
    syllabus_parsed: dict = Field(default_factory=dict)
    resources: list[CanvasResourceInput] = Field(default_factory=list)
    announcements: list[dict] = Field(default_factory=list)
    modules: list[dict] = Field(default_factory=list)
    pages: list[dict] = Field(default_factory=list)
    calendar_events: list[dict] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class CanvasAcademicSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    base_url: Literal["https://canvas.shufe.edu.cn", "https://canvas.sufe.edu.cn"]
    fetched_at: datetime
    courses: list[CanvasCourseInput]

    @field_validator("fetched_at")
    @classmethod
    def aware(cls, value: datetime):
        if value.utcoffset() is None:
            raise ValueError("fetched_at must include timezone")
        return value


class KzktRecordingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = Field(min_length=1)
    course_name: str = Field(min_length=1)
    course_external_id: str | None = None
    section_code: str | None = None
    title: str = Field(min_length=1)
    source_url: str | None = None
    published_at: datetime | None = None
    taught_at: datetime | None = None
    platform_session_id: str | None = None
    participation_evidence: dict | None = None
    local_path: str | None = None
    media_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    subtitle_text: str | None = None
    subtitle_segments: list[dict] = Field(default_factory=list)


class KzktSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str
    fetched_at: datetime
    recordings: list[KzktRecordingInput] = Field(default_factory=list)


def ingest_canvas_snapshot(db: Session, snapshot: CanvasAcademicSnapshot) -> dict:
    if snapshot.user_id != get_settings().canvas_expected_user_id:
        raise ValueError("Canvas account does not match configured user")
    report = {"courses": 0, "syllabi_created": 0, "resources_created": 0, "announcements_reviewed": 0, "unmapped": []}
    root = storage_root()
    for observed in snapshot.courses:
        binding = db.scalar(select(CourseProviderBinding).where(CourseProviderBinding.provider == "canvas", CourseProviderBinding.external_id == observed.course_id))
        if binding is None:
            matches = list(db.scalars(select(AcademicCourse).where(
                AcademicCourse.name == observed.name,
                AcademicCourse.automation_profile != AutomationProfile.calendar_only,
            )))
            if len(matches) != 1:
                report["unmapped"].append({"course_id": observed.course_id, "name": observed.name}); continue
            binding = CourseProviderBinding(course_id=matches[0].id, provider="canvas", external_id=observed.course_id,
                base_url=snapshot.base_url, state="active", metadata_={"discovered_by": "exact_course_name", "user_id": snapshot.user_id})
            db.add(binding); db.flush()
        course = db.get(AcademicCourse, binding.course_id); report["courses"] += 1
        binding.last_synced_at = snapshot.fetched_at
        binding.metadata_ = {**(binding.metadata_ or {}), "user_id": snapshot.user_id, "modules": observed.modules,
            "calendar_events_seen": len(observed.calendar_events), "snapshot_at": snapshot.fetched_at.isoformat(), "partial_errors": observed.errors}
        if observed.syllabus_text.strip():
            digest = hashlib.sha256(observed.syllabus_text.encode("utf-8")).hexdigest()
            existing = db.scalar(select(SyllabusSnapshot).where(SyllabusSnapshot.course_id == course.id, SyllabusSnapshot.provider == "canvas", SyllabusSnapshot.content_hash == digest))
            if existing is None:
                parsed = observed.syllabus_parsed or parse_syllabus(observed.syllabus_text, course.teachers)
                db.add(SyllabusSnapshot(course_id=course.id, provider="canvas", content_hash=digest, source_url=observed.syllabus_url,
                    raw_text=observed.syllabus_text, parsed=parsed, fetched_at=snapshot.fetched_at)); report["syllabi_created"] += 1
        syllabus_file_candidates: list[tuple[CanvasResourceInput, Path]] = []
        for resource in observed.resources:
            if Path(resource.title).suffix.lower() not in ALLOWED_RESOURCE_EXTENSIONS: continue
            local_path = Path(resource.local_path).resolve() if resource.local_path else None
            if local_path and not local_path.is_relative_to(root):
                raise ValueError("Canvas resource path is outside academic storage")
            digest = resource.sha256
            if local_path and local_path.is_file():
                with local_path.open("rb") as handle: actual = hashlib.file_digest(handle, "sha256").hexdigest()
                if digest and digest != actual: raise ValueError("Canvas resource digest mismatch")
                digest = actual
                destination_dir = course_directory(course) / "resources" / "canvas"
                destination_dir.mkdir(parents=True, exist_ok=True)
                destination = destination_dir / f"{digest[:12]}-{safe_part(resource.title)}"
                if not destination.exists():
                    temporary = destination.with_suffix(destination.suffix + ".part")
                    shutil.copyfile(local_path, temporary); os.replace(temporary, destination)
                local_path = destination
            if not digest: continue
            if local_path and local_path.is_file() and is_syllabus_title(resource.title):
                syllabus_file_candidates.append((resource, local_path))
            existing = db.scalar(select(CourseResource).where(CourseResource.course_id == course.id, CourseResource.provider == "canvas",
                CourseResource.external_id == resource.file_id, CourseResource.content_hash == digest))
            if existing is None:
                db.add(CourseResource(course_id=course.id, provider="canvas", external_id=resource.file_id, title=resource.title,
                    mime_type=resource.mime_type, byte_size=resource.byte_size, source_url=resource.source_url, source_modified_at=resource.modified_at,
                    content_hash=digest, local_path=str(local_path) if local_path else None, availability="available" if local_path else "remote")); report["resources_created"] += 1
        if not observed.syllabus_text.strip():
            for resource, local_path in syllabus_file_candidates:
                try:
                    syllabus_text = extract_document_text(local_path)
                except Exception:
                    syllabus_text = ""
                if not syllabus_text:
                    continue
                digest = hashlib.sha256(syllabus_text.encode("utf-8")).hexdigest()
                existing = db.scalar(select(SyllabusSnapshot).where(SyllabusSnapshot.course_id == course.id,
                    SyllabusSnapshot.provider == "canvas", SyllabusSnapshot.content_hash == digest))
                if existing is None:
                    parsed = {**parse_syllabus(syllabus_text, course.teachers), "source_type": "canvas_file", "source_title": resource.title}
                    db.add(SyllabusSnapshot(course_id=course.id, provider="canvas", content_hash=digest, source_url=resource.source_url,
                        raw_text=syllabus_text, parsed=parsed, fetched_at=snapshot.fetched_at)); report["syllabi_created"] += 1
                break
        for page in observed.pages:
            title = str(page.get("title") or page.get("url") or "Canvas page")
            body = str(page.get("body") or "").strip()
            if not body: continue
            digest = hashlib.sha256(body.encode()).hexdigest()
            external_id = f"page:{page.get('url') or digest}"
            existing = db.scalar(select(CourseResource).where(CourseResource.course_id == course.id,
                CourseResource.provider == "canvas", CourseResource.external_id == external_id, CourseResource.content_hash == digest))
            if existing is None:
                destination_dir = course_directory(course) / "resources" / "canvas-pages"
                destination_dir.mkdir(parents=True, exist_ok=True)
                destination = destination_dir / f"{digest[:12]}-{safe_part(title)}.md"
                if not destination.exists():
                    temporary = destination.with_suffix(destination.suffix + ".part")
                    temporary.write_text(f"# {title}\n\n{body}\n", encoding="utf-8"); os.replace(temporary, destination)
                db.add(CourseResource(course_id=course.id, provider="canvas", external_id=external_id, title=title,
                    mime_type="text/markdown", source_url=page.get("html_url"), source_modified_at=snapshot.fetched_at,
                    content_hash=digest, local_path=str(destination), availability="available")); report["resources_created"] += 1
        for announcement in observed.announcements:
            text = str(announcement.get("text") or "").strip()
            external_id = str(announcement.get("id") or hashlib.sha256(text.encode()).hexdigest())
            if text and re.search(r"调课|停课|补课|换教室|上课时间|作业|截止|考试|阅读", text):
                event = db.scalar(select(RawEvent).where(RawEvent.source == "canvas_announcement", RawEvent.source_event_id == external_id))
                if event is None:
                    event = RawEvent(occurred_at=snapshot.fetched_at, source="canvas_announcement", source_event_id=external_id,
                        event_type="academic_notice", content=text, content_hash=hashlib.sha256(text.encode()).hexdigest(),
                        metadata_={"course_id": str(course.id), "course_name": course.name, "source_url": announcement.get("url")}, processing_status=ProcessingStatus.pending)
                    db.add(event); db.flush()
                    db.add(InboxCandidate(candidate_type="academic_notice", payload={"source": "canvas", "course_id": str(course.id), "course_name": course.name,
                        "raw_text": text, "notice_kind": "schedule" if re.search(r"调课|停课|补课|换教室|上课时间", text) else "assignment"},
                        confidence=0.75, reason="Canvas announcement requires academic review", source_event_id=event.id)); report["announcements_reviewed"] += 1
        for calendar_event in observed.calendar_events:
            external_id = str(calendar_event.get("id") or "")
            start_at = calendar_event.get("start_at")
            if not external_id or not start_at: continue
            source_id = f"{observed.course_id}:{external_id}"
            event = db.scalar(select(RawEvent).where(RawEvent.source == "canvas_calendar", RawEvent.source_event_id == source_id))
            if event is None:
                title = str(calendar_event.get("title") or "Canvas 课程日历事项")
                event = RawEvent(occurred_at=snapshot.fetched_at, source="canvas_calendar", source_event_id=source_id,
                    event_type="academic_notice", content=title, content_hash=hashlib.sha256(json.dumps(calendar_event, sort_keys=True).encode()).hexdigest(),
                    metadata_={"course_id": str(course.id), "canvas_event": calendar_event}, processing_status=ProcessingStatus.pending)
                db.add(event); db.flush()
                db.add(InboxCandidate(candidate_type="academic_notice", payload={"source": "canvas", "course_id": str(course.id),
                    "course_name": course.name, "raw_text": title, "notice_kind": "schedule", "proposed_start_at": start_at,
                    "proposed_end_at": calendar_event.get("end_at"), "proposed_location": calendar_event.get("location_name"),
                    "source_url": calendar_event.get("html_url")}, confidence=0.85,
                    reason="Canvas course calendar item requires confirmation before changing the locked timetable", source_event_id=event.id))
    db.commit(); return report


def evidence_rows(evidence_path: Path) -> list[tuple[str, dict]]:
    if not evidence_path.is_file():
        raise FileNotFoundError("WeCom evidence database is unavailable")
    with sqlite3.connect(evidence_path) as conn:
        return [(row[0], json.loads(row[1])) for row in conn.execute("SELECT id,payload FROM evidence ORDER BY id")]


def discover_wecom_chats(db: Session, evidence_path: Path) -> dict:
    courses = list(db.scalars(select(AcademicCourse).where(AcademicCourse.automation_profile != AutomationProfile.calendar_only)))
    rows = evidence_rows(evidence_path)
    participants: dict[str, set[str]] = {}
    for _, payload in rows:
        conversation_id = str(payload.get("conversation_id") or "")
        sender_name = str(payload.get("sender_name") or "").strip()
        if conversation_id and sender_name: participants.setdefault(conversation_id, set()).add(sender_name)
    created = 0
    updated = 0
    seen: set[tuple[str, uuid.UUID]] = set()
    for external_message_id, payload in rows:
        conversation_id = str(payload.get("conversation_id") or "")
        conversation_name = str(payload.get("conversation_name") or "").strip()
        haystack = f"{conversation_name} {payload.get('text') or ''}".lower()
        for course in courses:
            terms = [course.name, course.section_code, *course.teachers]
            score = sum(bool(term and term.lower() in haystack) for term in terms)
            if not score or (conversation_id, course.id) in seen: continue
            teacher_participants = sorted(set(course.teachers) & participants.get(conversation_id, set()))
            title_score = sum(bool(term and term.lower() in conversation_name.lower()) for term in terms)
            # An unnamed direct chat is too ambiguous unless a known teacher is
            # actually one of its participants. Message text alone is insufficient.
            if not conversation_name and not teacher_participants: continue
            # A named group must identify the course in its title, unless a known
            # course teacher is an observed participant. Ordinary message text
            # mentioning a course is not enough to classify an unrelated group.
            if conversation_name and not title_score and not teacher_participants: continue
            seen.add((conversation_id, course.id))
            display_name = conversation_name or f"与{'、'.join(teacher_participants)}的私聊"
            confidence = min(0.5 + score * 0.15 + (0.15 if teacher_participants else 0), 0.95)
            bound = db.scalar(select(CourseProviderBinding).where(CourseProviderBinding.provider == "wecom", CourseProviderBinding.external_id == conversation_id))
            if bound: continue
            source_id = f"{conversation_id}:{course.id}"
            event = db.scalar(select(RawEvent).where(RawEvent.source == "wecom_chat_candidate", RawEvent.source_event_id == source_id))
            if event:
                candidate = db.scalar(select(InboxCandidate).where(InboxCandidate.source_event_id == event.id,
                    InboxCandidate.candidate_type == "academic_wecom_chat", InboxCandidate.status == InboxStatus.pending))
                if candidate and candidate.payload.get("conversation_name") != display_name:
                    candidate.payload = {**candidate.payload, "conversation_name": display_name,
                        "conversation_type": "direct" if teacher_participants and not conversation_name else "group",
                        "teacher_participants": teacher_participants, "match_score": score}
                    candidate.confidence = confidence; updated += 1
                continue
            event = RawEvent(occurred_at=datetime.now(timezone.utc), source="wecom_chat_candidate", source_event_id=source_id,
                event_type="academic_chat_candidate", content=display_name,
                metadata_={"course_id": str(course.id), "conversation_id": conversation_id}, processing_status=ProcessingStatus.pending)
            db.add(event); db.flush()
            db.add(InboxCandidate(candidate_type="academic_wecom_chat", payload={"source": "wecom", "course_id": str(course.id), "course_name": course.name,
                "conversation_id": conversation_id, "conversation_name": display_name,
                "conversation_type": "direct" if teacher_participants and not conversation_name else "group",
                "teacher_participants": teacher_participants, "match_score": score}, confidence=confidence,
                reason="Course name, section or teacher matched locally available WeCom evidence", source_event_id=event.id)); created += 1
    db.commit(); return {"created": created, "updated": updated, "matches": len(seen)}


def confirm_wecom_chat(db: Session, candidate: InboxCandidate) -> CourseProviderBinding:
    if candidate.candidate_type != "academic_wecom_chat" or candidate.status != InboxStatus.pending:
        raise ValueError("Candidate is not a pending WeCom course chat")
    payload = candidate.payload
    binding = db.scalar(select(CourseProviderBinding).where(CourseProviderBinding.provider == "wecom", CourseProviderBinding.external_id == payload["conversation_id"]))
    if binding is None:
        binding = CourseProviderBinding(course_id=uuid.UUID(payload["course_id"]), provider="wecom", external_id=payload["conversation_id"],
            state="active", metadata_={"conversation_name": payload.get("conversation_name"), "confirmed_at": datetime.now(timezone.utc).isoformat()})
        db.add(binding)
    candidate.status = InboxStatus.accepted; candidate.resolved_at = datetime.now(timezone.utc)
    db.commit(); db.refresh(binding); return binding


def archive_wecom_attachment(course: AcademicCourse, message_id: str, attachment: dict) -> tuple[str | None, str, str]:
    name = safe_part(str(attachment.get("name") or "attachment"))
    extension = Path(name).suffix.lower()
    if extension not in ALLOWED_RESOURCE_EXTENSIONS:
        return None, hashlib.sha256(f"missing:{message_id}:{name}".encode()).hexdigest(), "blocked_type"
    if attachment.get("status") != "verified_local_file" or not attachment.get("path"):
        return None, hashlib.sha256(f"missing:{message_id}:{name}".encode()).hexdigest(), "not_cached"
    source = Path(attachment["path"]).resolve()
    if not source.is_file() or source.is_symlink():
        return None, hashlib.sha256(f"missing:{message_id}:{name}".encode()).hexdigest(), "not_cached"
    with source.open("rb") as handle: digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if extension == ".zip":
        try:
            with zipfile.ZipFile(source) as archive:
                for member in archive.infolist():
                    member_path = Path(member.filename.replace("\\", "/"))
                    if member_path.is_absolute() or ".." in member_path.parts or member_path.suffix.lower() in DANGEROUS_ARCHIVE_EXTENSIONS:
                        return None, digest, "blocked_policy"
        except (zipfile.BadZipFile, OSError):
            return None, digest, "blocked_policy"
    destination_dir = course_directory(course) / "resources" / "wecom"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{digest[:12]}-{name}"
    if not destination.exists():
        temporary = destination.with_suffix(destination.suffix + ".part")
        shutil.copyfile(source, temporary); os.replace(temporary, destination)
    return str(destination), digest, "available"


def import_bound_wecom(db: Session, evidence_path: Path) -> dict:
    rows = evidence_rows(evidence_path); created_messages = created_resources = updated_resources = review_candidates = 0
    bindings = {item.external_id: item for item in db.scalars(select(CourseProviderBinding).where(CourseProviderBinding.provider == "wecom", CourseProviderBinding.state == "active"))}
    for external_message_id, payload in rows:
        binding = bindings.get(str(payload.get("conversation_id") or ""))
        if not binding: continue
        course = db.get(AcademicCourse, binding.course_id)
        message = db.scalar(select(CourseMessage).where(CourseMessage.provider_binding_id == binding.id, CourseMessage.external_message_id == external_message_id))
        raw_content_type = payload.get("content_type")
        try:
            content_type = int(raw_content_type) if raw_content_type is not None else None
        except (TypeError, ValueError):
            content_type = raw_content_type
        message_metadata = {"parse_status": payload.get("parse_status"), "content_type": content_type,
            "is_system_event": payload.get("sender_name") == "小喇叭" or content_type in {1002, 1022, 1043},
            "embedded_reference": payload.get("embedded_reference")}
        if message is None:
            sent = datetime.fromtimestamp(int(payload["sent_at"]), timezone.utc)
            message = CourseMessage(course_id=course.id, provider_binding_id=binding.id, external_message_id=external_message_id,
                sender_id=payload.get("sender_id"), sender_name=payload.get("sender_name"), sent_at=sent,
                direction=payload.get("direction") or "inbound", text=payload.get("text"),
                metadata_=message_metadata)
            db.add(message); db.flush(); created_messages += 1
        elif message.metadata_ != message_metadata:
            message.metadata_ = message_metadata
        for index, attachment in enumerate(payload.get("attachments") or []):
            local_path, digest, availability = archive_wecom_attachment(course, external_message_id, attachment)
            external_file_id = f"{external_message_id}:{index}:{attachment.get('name') or 'attachment'}"
            existing = db.scalar(select(CourseResource).where(CourseResource.course_id == course.id, CourseResource.provider == "wecom",
                CourseResource.external_id == external_file_id, CourseResource.content_hash == digest))
            if existing is not None and existing.availability != "available" and existing.availability != availability:
                existing.title = str(attachment.get("name") or "附件")
                existing.byte_size = attachment.get("bytes")
                existing.local_path = local_path
                existing.availability = availability
            if existing is None:
                current = db.scalar(select(CourseResource).where(CourseResource.course_id == course.id, CourseResource.provider == "wecom",
                    CourseResource.external_id == external_file_id).order_by(CourseResource.created_at.desc()).limit(1))
                if current is not None and current.availability != "available" and availability == "available":
                    current.title = str(attachment.get("name") or "附件")
                    current.byte_size = attachment.get("bytes")
                    current.content_hash = digest
                    current.local_path = local_path
                    current.availability = "available"
                    updated_resources += 1
                elif current is None or (availability == "available" and current.availability == "available"):
                    db.add(CourseResource(course_id=course.id, course_message_id=message.id, provider="wecom", external_id=external_file_id,
                        title=str(attachment.get("name") or "附件"), byte_size=attachment.get("bytes"), content_hash=digest, local_path=local_path, availability=availability)); created_resources += 1
        text = (payload.get("text") or "").strip()
        if text and re.search(r"调课|停课|补课|换教室|作业|截止|考试|阅读", text):
            source_id = f"{external_message_id}:academic-review"
            event = db.scalar(select(RawEvent).where(RawEvent.source == "wecom_academic", RawEvent.source_event_id == source_id))
            if event is None:
                event = RawEvent(occurred_at=message.sent_at, source="wecom_academic", source_event_id=source_id, event_type="academic_notice",
                    content=text, content_hash=hashlib.sha256(text.encode()).hexdigest(), metadata_={"course_id": str(course.id), "message_id": str(message.id)}, processing_status=ProcessingStatus.pending)
                db.add(event); db.flush()
                db.add(InboxCandidate(candidate_type="academic_notice", payload={"source": "wecom", "course_id": str(course.id), "course_name": course.name,
                    "message_id": str(message.id), "raw_text": text, "notice_kind": "schedule" if re.search(r"调课|停课|补课|换教室", text) else "assignment"},
                    confidence=0.7, reason="WeCom academic notice always requires confirmation", source_event_id=event.id)); review_candidates += 1
        binding.last_synced_at = datetime.now(timezone.utc)
    db.commit(); return {"messages_created": created_messages, "resources_created": created_resources,
        "resources_updated": updated_resources, "review_candidates": review_candidates}





@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict:
    term = db.scalar(select(AcademicTerm).where(AcademicTerm.status == "active"))
    if not term: return {"term": None, "courses": [], "today_sessions": [], "pending_reviews": 0}
    courses = list(db.scalars(select(AcademicCourse).where(AcademicCourse.term_id == term.id).order_by(AcademicCourse.name)))
    today = datetime.now(SHANGHAI).date(); start = datetime.combine(today, time.min, SHANGHAI); end = start + timedelta(days=1)
    sessions = list(db.scalars(select(ClassSession).where(ClassSession.start_at >= start, ClassSession.start_at < end).order_by(ClassSession.start_at)))
    pending = len(list(db.scalars(select(InboxCandidate).where(InboxCandidate.candidate_type.in_(["academic_notice", "academic_wecom_chat"]), InboxCandidate.status == InboxStatus.pending, InboxCandidate.payload["superseded_by"].astext.is_(None)))))
    return {"term": {"id": str(term.id), "code": term.code, "name": term.name, "starts_on": term.starts_on, "teaching_weeks": term.teaching_weeks},
        "courses": [course_payload(db, item) for item in courses], "today_sessions": [session_payload(item) for item in sessions], "pending_reviews": pending}


@router.get("/courses")
def list_courses(db: Session = Depends(get_db)) -> list[dict]:
    return [course_payload(db, item) for item in db.scalars(select(AcademicCourse).order_by(AcademicCourse.name))]


@router.get("/courses/{course_id}")
def get_course(course_id: uuid.UUID, lightweight: bool = False, db: Session = Depends(get_db)) -> dict:
    item = db.get(AcademicCourse, course_id)
    if not item: raise HTTPException(404, "Course not found")
    return course_payload(db, item, detail=True, lightweight=lightweight)


@router.get("/sessions")
def list_sessions(start: datetime, end: datetime, db: Session = Depends(get_db)) -> list[dict]:
    return [session_payload(item) for item in db.scalars(select(ClassSession).where(ClassSession.start_at >= start, ClassSession.start_at < end).order_by(ClassSession.start_at))]


@router.get("/courses/{course_id}/resources")
def list_resources(course_id: uuid.UUID, db: Session = Depends(get_db)) -> list[dict]:
    return [{"id": str(item.id), "provider": item.provider, "title": item.title, "mime_type": item.mime_type, "byte_size": item.byte_size,
        "local_path": item.local_path, "availability": item.availability, "created_at": item.created_at} for item in db.scalars(select(CourseResource).where(CourseResource.course_id == course_id).order_by(CourseResource.created_at.desc()))]


@router.get("/resources/{resource_id}/preview")
def preview_resource(resource_id: uuid.UUID, db: Session = Depends(get_db)) -> FileResponse:
    resource = db.get(CourseResource, resource_id)
    if not resource:
        raise HTTPException(404, "课程资料不存在")
    path, media_type = preview_file(resource)
    filename = resource.title if media_type != "application/pdf" or Path(resource.title).suffix.lower() == ".pdf" else f"{resource.title}.pdf"
    return FileResponse(path, media_type=media_type, filename=filename, content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@router.get("/courses/{course_id}/messages")
def list_messages(course_id: uuid.UUID, limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)) -> list[dict]:
    items = db.scalars(select(CourseMessage).where(CourseMessage.course_id == course_id).order_by(CourseMessage.sent_at.desc()).limit(min(limit * 3, 500)))
    return [message_payload(db, item) for item in items if not (item.metadata_ or {}).get("is_system_event")][:limit]


@router.post("/canvas/sync")
def canvas_sync(snapshot: CanvasAcademicSnapshot, db: Session = Depends(get_db)) -> dict:
    try: return ingest_canvas_snapshot(db, snapshot)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc


@router.post("/canvas/assignments")
def canvas_assignments(snapshot: CanvasSnapshot, db: Session = Depends(get_db)) -> dict:
    if snapshot.user_id != get_settings().canvas_expected_user_id: raise HTTPException(409,"Canvas 账号不匹配")
    try:
        report = sync_assignment_snapshot(db, snapshot); db.commit(); return report
    except ValueError as exc:
        db.rollback(); raise HTTPException(422, str(exc)) from exc


@router.post("/canvas/login")
def canvas_login():
    from .connections import start_browser
    try: return start_browser("canvas","login")
    except RuntimeError as e: raise HTTPException(409,str(e))


@router.post("/canvas/run")
def canvas_run():
    from .connections import start_browser
    try: return start_browser("canvas","sync")
    except RuntimeError as e: raise HTTPException(409,str(e))


@router.get("/canvas/status")
def canvas_status() -> dict:
    path = storage_root() / ".runtime" / "canvas" / "status.json"
    if not path.is_file(): return {"status": "never_run"}
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        reason = str(result.get("reason") or "")
        error = str(result.get("error") or "")
        # Older browser scripts only wrote text. A timeout is evidence of a login flow
        # not completing; a 404 is never enough evidence to declare a session expired.
        if result.get("status") == "failed":
            if reason == "authentication_required" or (not reason and "login timed out" in error.lower()):
                result["status"] = "login_required"
                result["message"] = "Canvas 要求重新登录。请在独立浏览器窗口完成登录后，再刷新账号检查。"
            elif reason == "api_not_found" or "canvas 404" in error.lower():
                result["status"] = "check_failed"
                result["message"] = "账号检查接口返回 404，未能判断登录是否过期；请刷新检查或查看 Canvas 服务状态。"
            elif reason == "rate_limited":
                result["status"] = "check_failed"
                result["message"] = "Canvas 暂时限制了检查请求，请稍后再试。"
            elif reason in {"service_unavailable", "request_failed", "unknown"}:
                result["status"] = "check_failed"
                result["message"] = "Canvas 账号检查失败，未能判断登录是否过期。请刷新检查。"
        return result
    except (OSError, json.JSONDecodeError): return {"status": "invalid_status"}


class KzktMatchInput(BaseModel):
    course_name: str
    section_code: str | None = None
    taught_at: datetime | None = None
    platform_session_id: str | None = None


@router.post("/kzkt/match")
def kzkt_match(payload: KzktMatchInput, db: Session = Depends(get_db)) -> dict:
    from personal_os_api.kzkt import match_course, matching_session, parse_time
    course = match_course(db, payload.course_name, payload.section_code)
    if not course: return {"course_id": None}
    term = db.get(AcademicTerm, course.term_id)
    taught_at = parse_time(payload.taught_at)
    if taught_at and not term.starts_on <= taught_at.astimezone(SHANGHAI).date() < term.starts_on + timedelta(weeks=term.teaching_weeks):
        return {"course_id": None, "reason": "outside_current_term"}
    session = matching_session(db, course.id, taught_at, payload.platform_session_id)
    return {"course_id": str(course.id), "course_name": course.name, "section_code": course.section_code,
        "session_id": str(session.id) if session else None}


@router.post("/kzkt/ingest")
def kzkt_ingest(snapshot: KzktSnapshot, db: Session = Depends(get_db)) -> dict:
    try:
        return ingest_kzkt_snapshot(db, snapshot.model_dump(mode="json"))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.post("/kzkt/process")
def kzkt_process(course_id: uuid.UUID | None = None, recording_id: uuid.UUID | None = None, db: Session = Depends(get_db)) -> dict:
    return process_recordings_serialized(db, course_id, recording_id)


class SlideConfirmationInput(BaseModel):
    courseware_page_id: uuid.UUID | None = None


@router.post("/recordings/{recording_id}/slides/process")
def recording_slides_process(recording_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    try:
        return process_recording_slides(db, recording_id)
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.post("/recording-slides/{segment_id}/confirm")
def recording_slide_confirm(segment_id: uuid.UUID, body: SlideConfirmationInput, db: Session = Depends(get_db)) -> dict:
    try:
        segment = confirm_slide_segment(db, segment_id, body.courseware_page_id)
        return {"id": str(segment.id), "status": segment.status, "courseware_page_id": str(segment.courseware_page_id) if segment.courseware_page_id else None}
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.get("/courseware-pages/{page_id}/image")
def courseware_page_image(page_id: uuid.UUID, db: Session = Depends(get_db)) -> FileResponse:
    page = db.get(CoursewarePage, page_id)
    if not page: raise HTTPException(404, "课件页不存在")
    try: path = allowed_lesson_path(page.image_path)
    except RuntimeError as exc: raise HTTPException(404, str(exc)) from exc
    if not path: raise HTTPException(404, "课件页图片不可用")
    return FileResponse(path, media_type="image/png")


@router.get("/recording-slides/{segment_id}/image")
def recording_slide_image(segment_id: uuid.UUID, db: Session = Depends(get_db)) -> FileResponse:
    segment = db.get(RecordingSlideSegment, segment_id)
    if not segment: raise HTTPException(404, "课堂展示截图不存在")
    try: path = allowed_lesson_path(segment.frame_path)
    except RuntimeError as exc: raise HTTPException(404, str(exc)) from exc
    if not path: raise HTTPException(404, "课堂展示截图不可用")
    return FileResponse(path, media_type="image/jpeg")


def kzkt_script() -> Path:
    script = Path(get_settings().kzkt_browser_command)
    if not script.is_absolute():
        script = Path(__file__).resolve().parents[3] / script
    if not script.is_file():
        raise HTTPException(503, "空中课堂浏览器脚本未安装")
    return script


def kzkt_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update({"ACADEMIC_STORAGE_ROOT": get_settings().academic_storage_root, "KZKT_BASE_URL": get_settings().kzkt_base_url,
        "PERSONAL_OS_API_BASE_URL": f"http://{get_settings().personal_os_host}:{get_settings().personal_os_port}",
        "PERSONAL_OS_TOOL_TOKEN": get_settings().personal_os_tool_token})
    return env


@router.post("/kzkt/login")
def kzkt_login():
    from .connections import start_browser
    try: return start_browser("kzkt","login")
    except RuntimeError as e: raise HTTPException(409,str(e))


@router.post("/kzkt/run")
def kzkt_run(course_id: uuid.UUID | None = None, recording_external_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    from personal_os_api.kzkt_queue import enqueue
    result = enqueue(db, course_id, recording_external_id)
    return {**result, "status": "syncing", "queue_status": "queued"}


@router.get("/kzkt/status")
def kzkt_status(db: Session = Depends(get_db)) -> dict:
    path = storage_root() / ".runtime" / "kzkt" / "status.json"
    ffmpeg_locations = []
    ollama_locations = []
    runtime = {"faster_whisper": importlib.util.find_spec("faster_whisper") is not None,
        "ffmpeg": shutil.which("ffmpeg") is not None or any(item.is_file() for item in ffmpeg_locations),
        "ollama": shutil.which("ollama") is not None or any(item.is_file() for item in ollama_locations),
        "opencv": importlib.util.find_spec("cv2") is not None, "pymupdf": importlib.util.find_spec("fitz") is not None,
        "rapidocr": importlib.util.find_spec("rapidocr_onnxruntime") is not None}
    from personal_os_api.kzkt import runtime_health
    runtime.update(runtime_health())
    from personal_os_api.kzkt_queue import queue_state
    queue = queue_state(db=db)
    counts = queue["counts"]
    if queue["tasks"]:
        waiting = counts["queued"] + counts["retry_wait"]
        failures = counts["failed"] + counts["blocked"] + counts["needs_confirmation"]
        state = "paused" if queue["paused"] else "syncing" if counts["running"] or waiting else "partial_failure" if failures else "success"
        running = next((task for task in queue["tasks"] if task["status"] == "running"), None)
        message = f"正在处理：{running['title']}" if running else f"已完成 {counts['success']} 项，排队/重试 {waiting} 项，需处理 {failures} 项"
        if queue["paused"]: message = "队列已暂停，当前阶段结束后停止。" + message
        return {"status":state,"runtime":runtime,"message":message,"queue_counts":counts,"progress":queue["progress"],"failed_count":failures+counts["retry_wait"]}
    if not path.is_file():
        missing = [name for name, present in runtime.items() if not present]
        return {"status": "never_run", "runtime": runtime,
            "message": f"本地处理组件尚未就绪：{', '.join(missing)}" if missing else "等待首次登录和同步"}
    try:
        result = json.loads(path.read_text(encoding="utf-8")); result["runtime"] = runtime
        return result
    except (OSError, json.JSONDecodeError): return {"status": "invalid_status"}


def production_wecom_evidence() -> Path:
    return storage_root() / ".runtime" / "wecom" / "evidence.sqlite"


def refresh_wecom_academics(db: Session) -> dict:
    from .connections import capture_wecom
    evidence=capture_wecom()
    return {"discovery":discover_wecom_chats(db,evidence),"import":import_bound_wecom(db,evidence)}


@router.post("/wecom/sync")
def wecom_sync(db: Session = Depends(get_db)) -> dict:
    try:
        return refresh_wecom_academics(db)
    except FileNotFoundError as exc: raise HTTPException(503, str(exc)) from exc
    except RuntimeError as exc: raise HTTPException(503, str(exc)) from exc


@router.get("/wecom/missing-attachments")
def missing_wecom_attachments(db: Session = Depends(get_db)) -> list[dict]:
    from personal_os_api.wecom_acquisition import list_missing
    return list_missing(db)


class WecomAcquireRequest(BaseModel):
    interaction_mode: Literal["foreground_assist", "background"] = "background"
    user_confirmed: bool = False


@router.post("/wecom/resources/{resource_id}/acquire")
def acquire_wecom_attachment(resource_id: uuid.UUID, db: Session = Depends(get_db)):
    resource=db.get(CourseResource,resource_id)
    if not resource: raise HTTPException(404,"附件不存在")
    return {"resource_id":str(resource_id),"status":"cached" if resource.availability=="available" else "waiting_download",
        "download_adapter":"not_available","message":"请在企微下载后重新检查；无界面下载适配器尚未接入"}


@router.get("/wecom/acquisitions/{acquisition_id}")
def get_wecom_acquisition(acquisition_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    from personal_os_api.wecom_acquisition import acquisition_payload
    item = db.get(AttachmentAcquisition, acquisition_id)
    if not item: raise HTTPException(404, "附件获取任务不存在")
    return acquisition_payload(db, item)


@router.post("/wecom/acquisitions/{acquisition_id}/cancel")
def cancel_wecom_acquisition(acquisition_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    from personal_os_api.wecom_acquisition import acquisition_payload, finish, record_event
    item = db.get(AttachmentAcquisition, acquisition_id)
    if not item: raise HTTPException(404, "附件获取任务不存在")
    if item.active_key == "active":
        finish(item, "cancelled", "cancelled_by_user")
        record_event(db, item, "cancelled", result_code="cancelled_by_user")
        db.commit(); db.refresh(item)
    return acquisition_payload(db, item)


@router.post("/wecom/resources/{resource_id}/recheck")
def recheck_wecom_attachment(resource_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    from personal_os_api.wecom_acquisition import acquisition_payload, latest_acquisition
    resource = db.get(CourseResource, resource_id)
    if not resource: raise HTTPException(404, "课程附件不存在")
    try: refresh_wecom_academics(db)
    except RuntimeError as exc: raise HTTPException(503, str(exc)) from exc
    item = latest_acquisition(db, resource.id)
    # A recheck is deliberately passive. It never creates, resets or queues a
    # desktop task; users choose either immediate assist or background waiting.
    return acquisition_payload(db, item) if item else {"resource_id": str(resource.id), "availability": resource.availability}


@router.get("/wecom/chat-candidates")
def wecom_candidates(db: Session = Depends(get_db)) -> list[dict]:
    items = db.scalars(select(InboxCandidate).where(InboxCandidate.candidate_type == "academic_wecom_chat", InboxCandidate.status == InboxStatus.pending).order_by(InboxCandidate.created_at.desc()))
    return [{"id": str(item.id), "payload": item.payload, "confidence": item.confidence, "reason": item.reason} for item in items]


@router.get("/review")
def academic_review_candidates(db: Session = Depends(get_db)) -> list[dict]:
    items = db.scalars(select(InboxCandidate).where(
        InboxCandidate.candidate_type == "academic_notice",
        InboxCandidate.status == InboxStatus.pending,
        InboxCandidate.payload["superseded_by"].astext.is_(None),
    ).order_by(InboxCandidate.created_at.desc()))
    return [{
        "id": str(item.id), "payload": item.payload, "confidence": item.confidence,
        "reason": item.reason, "created_at": item.created_at.isoformat(),
    } for item in items]


@router.post("/wecom/chat-candidates/{candidate_id}/confirm")
def bind_wecom_chat(candidate_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    item = db.get(InboxCandidate, candidate_id)
    if not item: raise HTTPException(404, "Candidate not found")
    try: binding = confirm_wecom_chat(db, item)
    except ValueError as exc: raise HTTPException(409, str(exc)) from exc
    return {"id": str(binding.id), "course_id": str(binding.course_id), "provider": binding.provider, "external_id": binding.external_id}


@router.post("/wecom/chat-candidates/{candidate_id}/dismiss")
def dismiss_wecom_chat(candidate_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    item = db.get(InboxCandidate, candidate_id)
    if not item or item.candidate_type != "academic_wecom_chat": raise HTTPException(404, "Candidate not found")
    if item.status != InboxStatus.pending:
        return {"id": str(item.id), "status": item.status.value, "idempotent": True}
    item.status = InboxStatus.rejected
    item.resolved_at = datetime.now(timezone.utc)
    if item.source_event_id:
        event = db.get(RawEvent, item.source_event_id)
        if event: event.processing_status = ProcessingStatus.ignored
    db.commit()
    return {"id": str(item.id), "status": item.status.value, "idempotent": False}


def resolve_academic_candidate(db: Session, candidate_id: uuid.UUID, action: Literal["accept", "reject"]) -> dict:
    db.execute(sql_text("SELECT pg_advisory_xact_lock(70631987)"))
    item = db.scalar(select(InboxCandidate).where(InboxCandidate.id == candidate_id).with_for_update().execution_options(populate_existing=True))
    if not item or item.candidate_type != "academic_notice": raise LookupError("Academic review candidate not found")
    if item.payload.get("superseded_by"): raise ValueError("该候选已合并或由新版提取替代，请刷新后处理")
    if item.status != InboxStatus.pending:
        result = {"id": str(item.id), "status": item.status.value, "idempotent": True, "effect": item.payload.get("effect")}
        db.commit(); return result
    effect = None
    if action == "accept":
        course = db.get(AcademicCourse, uuid.UUID(item.payload["course_id"]))
        if item.payload.get("notice_kind") in {"assignment", "reading", "collaboration", "classroom"}:
            task = db.scalar(select(Task).where(Task.source_event_id == item.source_event_id)) if item.source_event_id else None
            if task is None:
                payload = item.payload
                description = "\n\n".join(str(value) for value in [payload["raw_text"],
                    f"适用对象：{payload.get('audience') or '未注明'}", f"时间要求：{payload.get('time_text') or '未注明'}",
                    f"适用时间：{payload.get('applicable_at')}" if payload.get('applicable_at') else None,
                    f"来源课次：{payload.get('recording_title') or payload.get('course_name')}",
                    f"原文依据：{payload.get('evidence') or payload['raw_text']}",
                    f"课堂时间戳（秒）：{payload.get('timestamp_seconds')}" if payload.get('timestamp_seconds') is not None else None,
                    payload.get('source_url'), f"待核实：{payload.get('verification_note')}" if payload.get('needs_verification') else None] if value)
                deadline = datetime.fromisoformat(payload['deadline_at'].replace('Z', '+00:00')) if payload.get('deadline_at') else None
                task = Task(project_id=course.project_id, title=f"【{course.name}】{payload['raw_text']}", description=description, status=TaskStatus.todo, deadline_at=deadline,
                    source_event_id=item.source_event_id, assignee_type="human")
                db.add(task); db.flush()
            effect = {"task_id": str(task.id)}
        elif item.payload.get("proposed_start_at"):
            proposed_start = datetime.fromisoformat(str(item.payload["proposed_start_at"]).replace("Z", "+00:00"))
            proposed_end = datetime.fromisoformat(str(item.payload.get("proposed_end_at") or "").replace("Z", "+00:00")) if item.payload.get("proposed_end_at") else proposed_start + timedelta(hours=2)
            local_day = proposed_start.astimezone(SHANGHAI).date()
            day_start = datetime.combine(local_day, time.min, SHANGHAI); day_end = day_start + timedelta(days=1)
            sessions = list(db.scalars(select(ClassSession).where(ClassSession.course_id == course.id,
                ClassSession.end_at >= datetime.now(timezone.utc), ClassSession.start_at >= day_start, ClassSession.start_at < day_end)))
            if sessions:
                session = min(sessions, key=lambda row: abs((row.start_at - proposed_start).total_seconds()))
                session.start_at, session.end_at, session.source = proposed_start, proposed_end, "canvas_confirmed"
                if item.payload.get("proposed_location"): session.location = item.payload["proposed_location"]
                calendar = db.get(CalendarItem, session.calendar_item_id)
                calendar.start_at, calendar.end_at, calendar.source, calendar.source_event_id = proposed_start, proposed_end, "canvas_confirmed", item.source_event_id
                if item.payload.get("proposed_location"): calendar.description = f"地点：{item.payload['proposed_location']}"
                effect = {"session_id": str(session.id), "calendar_item_id": str(calendar.id), "operation": "updated"}
            else:
                calendar = db.scalar(select(CalendarItem).where(CalendarItem.source_event_id == item.source_event_id))
                if calendar is None:
                    calendar = CalendarItem(item_type=CalendarItemType.hard_event, title=f"【{course.name}】{item.payload['raw_text']}",
                        description=f"地点：{item.payload.get('proposed_location') or '待确认'}", start_at=proposed_start, end_at=proposed_end,
                        all_day=False, locked=True, flexible=False, project_id=course.project_id, source="canvas_confirmed",
                        source_event_id=item.source_event_id, status=CalendarStatus.planned, metadata_={"academic_course_id": str(course.id)})
                    db.add(calendar); db.flush()
                effect = {"calendar_item_id": str(calendar.id), "operation": "created"}
        else:
            task = db.scalar(select(Task).where(Task.source_event_id == item.source_event_id))
            if task is None:
                task = Task(project_id=course.project_id, title=f"【{course.name}】处理已确认调课通知", description=item.payload["raw_text"],
                    status=TaskStatus.todo, source_event_id=item.source_event_id, assignee_type="human")
                db.add(task); db.flush()
            effect = {"task_id": str(task.id), "operation": "manual_schedule_review"}
    item.status = InboxStatus.accepted if action == "accept" else InboxStatus.rejected
    if effect: item.payload = {**item.payload, "effect": effect}
    item.resolved_at = datetime.now(timezone.utc)
    if item.source_event_id:
        event = db.get(RawEvent, item.source_event_id)
        if event: event.processing_status = ProcessingStatus.processed if action == "accept" else ProcessingStatus.ignored
    db.commit(); return {"id": str(item.id), "status": item.status.value, "idempotent": False, "effect": effect}


@router.post("/review/{candidate_id}/resolve")
def resolve_academic(candidate_id: uuid.UUID, action: Literal["accept", "reject"], edits: dict | None = Body(default=None), db: Session = Depends(get_db)) -> dict:
    try:
        if edits:
            db.execute(sql_text("SELECT pg_advisory_xact_lock(70631987)"))
            current = db.scalar(select(InboxCandidate).where(InboxCandidate.id == candidate_id).with_for_update())
            if current and current.status == InboxStatus.pending:
                edit_academic_candidate(db, candidate_id, RequirementEdits(**edits), commit=False)
        return resolve_academic_candidate(db, candidate_id, action)
    except LookupError as exc: raise HTTPException(404, str(exc)) from exc
    except ValueError as exc: raise HTTPException(409, str(exc)) from exc


class RequirementEdits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_text: str | None = Field(default=None, min_length=1, max_length=4000)
    audience: str | None = Field(default=None, max_length=500)
    time_text: str | None = Field(default=None, max_length=500)
    deadline_at: datetime | None = None
    applicable_at: datetime | None = None

    @field_validator("deadline_at", "applicable_at")
    @classmethod
    def require_timezone(cls, value):
        if value is not None and value.tzinfo is None: raise ValueError("时间必须包含时区")
        return value


def edit_academic_candidate(db, candidate_id, edits, commit=True):
    db.execute(sql_text("SELECT pg_advisory_xact_lock(70631987)"))
    item = db.scalar(select(InboxCandidate).where(InboxCandidate.id == candidate_id).with_for_update())
    if not item or item.candidate_type != "academic_notice": raise LookupError("候选不存在")
    if item.status != InboxStatus.pending or item.payload.get("superseded_by"): raise ValueError("仅可修改当前待确认项")
    changes = edits.model_dump(mode="json", exclude_unset=True)
    if "time_text" in changes:
        changes.setdefault("deadline_at", None)
        changes.setdefault("applicable_at", None)
    if "raw_text" in changes and (not changes["raw_text"] or not changes["raw_text"].strip()): raise ValueError("行动内容不能为空")
    original = {k: item.payload.get(k) for k in changes}
    identity = item.payload.get("extracted_requirement") or {"text": item.payload.get("text", item.payload.get("raw_text")),
        "notice_kind": item.payload.get("notice_kind"), "audience": item.payload.get("audience"), "time_text": item.payload.get("time_text")}
    item.payload = {**item.payload, **changes, "user_edited": True, "extracted_requirement": identity,
        "edit_history": [*item.payload.get("edit_history", []), {"at": datetime.now(timezone.utc).isoformat(), "before": original, "after": changes}]}
    if any(k in changes for k in ("time_text", "deadline_at", "applicable_at")):
        item.payload = {**item.payload, "related_session_id": None, "time_status": "user_edited"}
    if commit: db.commit()
    return {"id": str(item.id), "status": item.status.value, "payload": item.payload}


@router.patch("/review/{candidate_id}")
def edit_academic(candidate_id: uuid.UUID, edits: RequirementEdits, db: Session = Depends(get_db)):
    try: return edit_academic_candidate(db, candidate_id, edits)
    except LookupError as exc: raise HTTPException(404, str(exc)) from exc
    except ValueError as exc: raise HTTPException(409, str(exc)) from exc


@router.get("/recordings/{recording_id}")
def recording_detail(recording_id: uuid.UUID, db: Session = Depends(get_db)):
    recording=db.get(CourseRecording,recording_id)
    if not recording: raise HTTPException(404,"回放不存在")
    result=recording_payload(db,recording)
    from types import SimpleNamespace
    from .attendance import scan_attendance
    result["attendance"] = scan_attendance(SimpleNamespace(**result["transcript"]) if result.get("transcript") else None)
    candidates=list(db.scalars(select(InboxCandidate).join(RawEvent,InboxCandidate.source_event_id==RawEvent.id).where(RawEvent.source=="kzkt",RawEvent.event_type=="academic_notice",RawEvent.source_event_id.like(f"kzkt:{recording_id}:%"))))
    result["requirements"]=[{"id":str(c.id),"status":c.status.value,"payload":c.payload} for c in candidates if not c.payload.get("superseded_by")]
    return result


@router.get("/resources/{resource_id}/file")
def download_resource(resource_id: uuid.UUID, db: Session = Depends(get_db)):
    resource=db.get(CourseResource,resource_id)
    if not resource:raise HTTPException(404,"资料不存在")
    return FileResponse(resource_file(resource),filename=resource.title,headers={"X-Content-Type-Options":"nosniff"})

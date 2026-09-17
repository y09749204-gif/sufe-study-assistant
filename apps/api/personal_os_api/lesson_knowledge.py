"""Local-only indexing of courseware pages and recorded classroom display frames."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from personal_os_api.config import get_settings
from personal_os_api.models import (
    CourseRecording, CourseResource, CoursewarePage, RecordingSlideSegment,
)


PRESENTATION_EXTENSIONS = {".pdf", ".ppt", ".pptx"}
DOCUMENT_EXTENSIONS = {".doc", ".docx"}


def _progress(stage: str, message: str, current: int, total: int) -> None:
    path = storage_root() / ".runtime" / "kzkt" / "status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"status": "syncing", "checked_at": datetime.now(timezone.utc).isoformat(),
        "message": message, "progress": {"stage": stage, "percent": 91 if stage == "indexing" else 95,
        "current": current, "total": total}}, ensure_ascii=False), encoding="utf-8")


def storage_root() -> Path:
    root = Path(get_settings().academic_storage_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def allowed_path(value: str | None) -> Path | None:
    if not value:
        return None
    root = storage_root()
    path = Path(value).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
        raise RuntimeError("课堂文件路径不在受管理的学业存储目录中")
    return path


def _deps():
    try:
        import cv2
        import fitz
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("课堂画面对齐组件未安装（需要 OpenCV、PyMuPDF、Pillow）") from exc
    return cv2, fitz, Image


@lru_cache(maxsize=1)
def _ocr_engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def _ocr(image_path: Path) -> str:
    """OCR is best-effort; visual matching remains available when its model is absent."""
    try:
        result, _ = _ocr_engine()(str(image_path))
        return "\n".join(str(row[1]) for row in result or [] if len(row) > 1).strip()
    except Exception:
        return ""


def image_hash(path: Path) -> str:
    _, _, Image = _deps()
    with Image.open(path) as source:
        image = source.convert("L").resize((16, 16))
        values = list(image.getdata())
    average = sum(values) / len(values)
    bits = "".join("1" if value >= average else "0" for value in values)
    return f"{int(bits, 2):064x}"


def hash_similarity(left: str, right: str) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    bits = len(left) * 4
    return round(1 - ((int(left, 16) ^ int(right, 16)).bit_count() / bits), 4)


def _safe_name(value: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff.-]+", "_", value).strip("_.")[:90] or "resource"


def _page_directory(resource: CourseResource) -> Path:
    return storage_root() / "courseware_pages" / str(resource.course_id) / str(resource.id)


def _export_office_to_pdf(source: Path, destination: Path, kind: str) -> None:
    from .academics import convert_office_file
    convert_office_file(source,destination)


def _render_resource(resource: CourseResource) -> tuple[Path, bool]:
    source = allowed_path(resource.local_path)
    if not source:
        raise RuntimeError(f"课件“{resource.title}”尚未缓存到本机")
    extension = source.suffix.lower()
    if extension == ".pdf":
        return source, True
    if extension in {".ppt", ".pptx"}:
        output = _page_directory(resource) / "source.pdf"
        if not output.is_file(): _export_office_to_pdf(source, output, "ppt")
        return output, True
    if extension in DOCUMENT_EXTENSIONS:
        output = _page_directory(resource) / "source.pdf"
        if not output.is_file(): _export_office_to_pdf(source, output, "doc")
        return output, False
    raise RuntimeError(f"不支持将 {source.suffix or '该类型'} 建立展示页索引")


def index_courseware_pages(db: Session, course_id: uuid.UUID) -> dict[str, int]:
    """Render available courseware to stable images and add a durable page index."""
    _, fitz, _ = _deps()
    report = {"resources": 0, "pages_created": 0, "pages_existing": 0, "skipped": 0}
    office_unavailable = False
    resources = list(db.scalars(select(CourseResource).where(CourseResource.course_id == course_id, CourseResource.availability == "available")))
    for resource_index, resource in enumerate(resources, 1):
        _progress("indexing", f"正在索引课件：{resource.title}", resource_index, len(resources))
        source = allowed_path(resource.local_path)
        if not source or source.suffix.lower() not in PRESENTATION_EXTENSIONS | DOCUMENT_EXTENSIONS:
            report["skipped"] += 1; continue
        if office_unavailable and source.suffix.lower() != ".pdf":
            report["skipped"] += 1; continue
        try:
            pdf, presentation_eligible = _render_resource(resource)
        except RuntimeError as exc:
            # A protected/damaged Office file must not prevent screenshot fallback for the recording.
            report["skipped"] += 1
            report.setdefault("errors", []).append(f"{resource.title}: {exc}")
            if source.suffix.lower() != ".pdf": office_unavailable = True
            continue
        report["resources"] += 1
        output = _page_directory(resource); output.mkdir(parents=True, exist_ok=True)
        document = fitz.open(pdf)
        try:
            for number, page in enumerate(document, start=1):
                image_path = output / f"page-{number:03d}.png"
                if not image_path.is_file():
                    pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                    pix.save(str(image_path))
                digest = image_hash(image_path)
                existing = db.scalar(select(CoursewarePage).where(CoursewarePage.resource_id == resource.id,
                    CoursewarePage.page_number == number, CoursewarePage.image_hash == digest))
                if existing:
                    report["pages_existing"] += 1; continue
                text = page.get_text("text").strip() or _ocr(image_path)
                title = next((line.strip() for line in text.splitlines() if line.strip()), None)
                db.add(CoursewarePage(course_id=course_id, resource_id=resource.id, page_number=number, title=title,
                    image_path=str(image_path), image_hash=digest, ocr_text=text,
                    metadata_={"matching_eligible": presentation_eligible, "source_extension": source.suffix.lower()}))
                report["pages_created"] += 1
        finally:
            document.close()
    db.flush()
    return report


def _frame_dir(recording: CourseRecording) -> Path:
    directory = storage_root() / ".runtime" / "alignment_frames" / str(recording.id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def extract_display_frames(recording: CourseRecording, interval_seconds: int = 30) -> list[dict[str, Any]]:
    """Keep representative slide frames only; transient frames are deleted after alignment."""
    cv2, _, _ = _deps()
    media = allowed_path(recording.local_path)
    if not media:
        raise RuntimeError("回放媒体尚未下载；请重新同步空中课堂后再处理展示页")
    capture = cv2.VideoCapture(str(media))
    if not capture.isOpened():
        raise RuntimeError("无法读取本地回放视频")
    fps = capture.get(cv2.CAP_PROP_FPS) or 25
    count = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = int(count / fps) if fps else 0
    directory = _frame_dir(recording)
    frames: list[dict[str, Any]] = []
    previous = ""
    try:
        for second in range(0, max(duration, 1) + 1, interval_seconds):
            _progress("aligning", f"正在提取课堂展示页：{second // 60} / {duration // 60} 分钟", second, duration)
            capture.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
            ok, frame = capture.read()
            if not ok: continue
            # Shared screen generally occupies the central region; trim browser chrome/borders conservatively.
            height, width = frame.shape[:2]
            crop = frame[int(height * .06):int(height * .94), int(width * .04):int(width * .96)]
            path = directory / f"{second:06d}.jpg"
            cv2.imwrite(str(path), crop, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            digest = image_hash(path)
            if previous and hash_similarity(previous, digest) >= .985:
                path.unlink(missing_ok=True); continue
            previous = digest
            frames.append({"start": second, "path": path, "hash": digest, "ocr_text": _ocr(path)})
    finally:
        capture.release()
    if not frames:
        raise RuntimeError("未从回放中提取到可用展示画面")
    frames[-1]["end"] = duration
    return frames


def _text_score(frame_text: str, page_text: str | None) -> float:
    left = set(re.findall(r"[\u3400-\u9fffA-Za-z0-9]{2,}", frame_text or ""))
    right = set(re.findall(r"[\u3400-\u9fffA-Za-z0-9]{2,}", page_text or ""))
    return round(len(left & right) / len(left | right), 4) if left and right else 0.0


def _capture_resource(db: Session, recording: CourseRecording, frame: dict[str, Any]) -> CourseResource:
    directory = storage_root() / "courseware_captures" / str(recording.course_id) / str(recording.id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{frame['start']:06d}-{frame['hash'][:12]}.jpg"
    if not target.is_file(): shutil.copy2(frame["path"], target)
    external_id = f"{recording.id}:{frame['hash']}"
    existing = db.scalar(select(CourseResource).where(CourseResource.course_id == recording.course_id,
        CourseResource.provider == "kzkt_capture", CourseResource.external_id == external_id))
    if existing: return existing
    return CourseResource(course_id=recording.course_id, class_session_id=recording.class_session_id, provider="kzkt_capture",
        external_id=external_id, title=f"课堂展示截图 {frame['start'] // 60:02d}:{frame['start'] % 60:02d}", mime_type="image/jpeg",
        byte_size=target.stat().st_size, source_url=recording.source_url, content_hash=hashlib.sha256(target.read_bytes()).hexdigest(),
        local_path=str(target), availability="available")


def _retain_frame(recording: CourseRecording, frame: dict[str, Any]) -> Path:
    """Keep evidence for pending/captured matches after transient extraction is cleaned."""
    directory = storage_root() / "recording_slide_frames" / str(recording.course_id) / str(recording.id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{frame['start']:06d}-{frame['hash'][:12]}.jpg"
    if not target.is_file(): shutil.copy2(frame["path"], target)
    return target


def _create_segment_candidate(db: Session, segment: RecordingSlideSegment, recording: CourseRecording) -> None:
    # The API/UI exposes the segment directly; this candidate makes it visible in the shared pending-review count too.
    from personal_os_api.models import InboxCandidate, InboxStatus, RawEvent
    source_id = f"kzkt:{recording.id}:slide:{segment.frame_hash}"
    event = db.scalar(select(RawEvent).where(RawEvent.source == "kzkt", RawEvent.source_event_id == source_id))
    if event: return
    event = RawEvent(source="kzkt", source_event_id=source_id, event_type="academic_slide_link", occurred_at=datetime.now(timezone.utc),
        content=f"{recording.title} {segment.start_seconds // 60:02d}:{segment.start_seconds % 60:02d}", content_hash=segment.frame_hash,
        metadata_={"recording_id": str(recording.id), "segment_id": str(segment.id)})
    db.add(event); db.flush()
    db.add(InboxCandidate(candidate_type="academic_courseware_link", source_event_id=event.id, status=InboxStatus.pending,
        payload={"recording_id": str(recording.id), "segment_id": str(segment.id), "course_id": str(recording.course_id),
            "timestamp_seconds": segment.start_seconds, "candidates": segment.candidates}, confidence=segment.confidence,
        reason="课堂展示页与已入库课件的匹配置信度不足，等待确认"))


def process_recording_slides(db: Session, recording_id: uuid.UUID) -> dict[str, Any]:
    recording = db.get(CourseRecording, recording_id)
    if not recording: raise RuntimeError("回放不存在")
    indexed = index_courseware_pages(db, recording.course_id)
    # Preserve the useful courseware index even if the video needs a later re-download.
    db.commit()
    pages = [page for page in db.scalars(select(CoursewarePage).where(CoursewarePage.course_id == recording.course_id))
        if (page.metadata_ or {}).get("matching_eligible") is True]
    frames = extract_display_frames(recording)
    created = unchanged = pending = captured = 0
    try:
        for index, frame in enumerate(frames):
            end = frames[index + 1]["start"] if index + 1 < len(frames) else frame.get("end", frame["start"] + 30)
            ranked = sorted(((hash_similarity(frame["hash"], page.image_hash), _text_score(frame["ocr_text"], page.ocr_text), page) for page in pages),
                key=lambda item: .75 * item[0] + .25 * item[1], reverse=True)
            top = ranked[:3]
            visual, text_score, page = top[0] if top else (0.0, 0.0, None)
            confidence = round(.75 * visual + .25 * text_score, 4) if page else 0.0
            status = "confirmed" if confidence >= .85 else "pending" if page and confidence >= .60 else "captured"
            candidates = [{"courseware_page_id": str(item[2].id), "page_number": item[2].page_number, "title": item[2].title,
                "confidence": round(.75 * item[0] + .25 * item[1], 4)} for item in top]
            existing = db.scalar(select(RecordingSlideSegment).where(RecordingSlideSegment.recording_id == recording.id,
                RecordingSlideSegment.start_seconds == frame["start"], RecordingSlideSegment.end_seconds == end,
                RecordingSlideSegment.frame_hash == frame["hash"]))
            if existing: unchanged += 1; continue
            resource = None
            if status == "captured":
                resource = _capture_resource(db, recording, frame); db.add(resource); db.flush(); captured += 1
            evidence_path = _retain_frame(recording, frame) if status in {"pending", "captured"} else frame["path"]
            segment = RecordingSlideSegment(recording_id=recording.id, courseware_page_id=page.id if page else None,
                captured_resource_id=resource.id if resource else None, start_seconds=frame["start"], end_seconds=end,
                frame_path=str(evidence_path), frame_hash=frame["hash"], visual_score=visual, ocr_score=text_score,
                confidence=confidence, status=status, candidates=candidates)
            db.add(segment); db.flush(); created += 1
            if status == "pending": _create_segment_candidate(db, segment, recording); pending += 1
        db.commit()
        return {"recording_id": str(recording.id), "indexed": indexed, "segments_created": created, "unchanged": unchanged,
            "pending": pending, "captured": captured}
    finally:
        # Only retained fallback captures are copied above. The extraction workspace is intentionally disposable.
        shutil.rmtree(_frame_dir(recording), ignore_errors=True)


def confirm_slide_segment(db: Session, segment_id: uuid.UUID, courseware_page_id: uuid.UUID | None) -> RecordingSlideSegment:
    segment = db.get(RecordingSlideSegment, segment_id)
    if not segment: raise RuntimeError("展示页关联不存在")
    if courseware_page_id:
        page = db.get(CoursewarePage, courseware_page_id)
        recording = db.get(CourseRecording, segment.recording_id)
        if not page or not recording or page.course_id != recording.course_id:
            raise RuntimeError("候选课件页不属于该课程")
        segment.courseware_page_id, segment.captured_resource_id = page.id, None
    segment.status, segment.confirmed_at = "confirmed", datetime.now(timezone.utc)
    db.commit(); db.refresh(segment)
    return segment

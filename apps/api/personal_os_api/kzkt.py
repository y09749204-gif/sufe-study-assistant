"""Local-only 空中课堂 recording ingestion and lesson-review processing."""
from __future__ import annotations

import hashlib
import json
import re
from zoneinfo import ZoneInfo
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select, text as sql_text
from sqlalchemy.orm import Session

from personal_os_api.config import get_settings
from .ai_provider import chat_json, model_identity
from personal_os_api.models import (
    AcademicCourse, AcademicTerm, AutomationProfile, ClassSession, CourseProviderBinding, CourseRecording,
    InboxCandidate, InboxStatus, LessonReview, RawEvent, RecordingTranscript,
)

PROVIDER = "kzkt"
REVIEW_VERSION = "kzkt-review-v3"


def parse_time(value):
    if not value:
        return None
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    return result if result.tzinfo else result.replace(tzinfo=ZoneInfo("Asia/Shanghai"))


def runtime_health() -> dict:
    result = {}
    try:
        import ctranslate2
        from faster_whisper import WhisperModel
        result["cuda_devices"] = ctranslate2.get_cuda_device_count()
        result["whisper_cuda_ready"] = result["cuda_devices"] > 0 and "int8_float16" in ctranslate2.get_supported_compute_types("cuda")
    except Exception as exc:
        result.update(whisper_cuda_ready=False, whisper_error=str(exc))
    try:
        response = httpx.get(f"{get_settings().kzkt_ollama_base_url.rstrip('/')}/api/tags", timeout=3, trust_env=False)
        response.raise_for_status()
        names = [item.get("name") for item in response.json().get("models", [])]
        result.update(ollama_service=True, ollama_model_ready=get_settings().kzkt_ollama_model in names)
    except Exception as exc:
        result.update(ollama_service=False, ollama_model_ready=False, ollama_error=str(exc))
    return result


def storage_root() -> Path:
    root = Path(get_settings().academic_storage_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def normalized(value: str) -> str:
    return "".join(char.lower() for char in value if char.isalnum() or "\u3400" <= char <= "\u9fff")


def allowed_media_path(value: str | None) -> Path | None:
    if not value:
        return None
    root = storage_root().resolve()
    path = Path(value).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
        raise ValueError("KZKT media path is outside academic storage")
    return path


def match_course(db: Session, name: str, section_code: str | None = None) -> AcademicCourse | None:
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    candidates = list(db.scalars(select(AcademicCourse).join(AcademicTerm).where(
        AcademicCourse.status == "active", AcademicCourse.automation_profile != AutomationProfile.calendar_only,
        AcademicTerm.starts_on <= today, AcademicTerm.status == "active")))
    candidates = [c for c in candidates if today < db.get(AcademicTerm, c.term_id).starts_on + timedelta(weeks=db.get(AcademicTerm, c.term_id).teaching_weeks)]
    if section_code:
        exact = [item for item in candidates if item.section_code == section_code]
        if len(exact) == 1:
            return exact[0]
    target = normalized(name)
    exact = [item for item in candidates if normalized(item.name) == target]
    if len(exact) == 1:
        return exact[0]
    contains = [item for item in candidates if normalized(item.name) and normalized(item.name) in target]
    return contains[0] if len(contains) == 1 else None


def matching_session(db: Session, course_id: Any, taught_at: datetime | None, platform_session_id: str | None = None) -> ClassSession | None:
    sessions = list(db.scalars(select(ClassSession).where(ClassSession.course_id == course_id)))
    if platform_session_id:
        exact = [item for item in sessions if item.source_key == f"kzkt:{platform_session_id}"]
        if len(exact) == 1:
            return exact[0]
    if not taught_at:
        return None
    nearby = [item for item in sessions if item.start_at <= taught_at < item.end_at]
    return nearby[0] if len(nearby) == 1 else None


def recording_hash(item: dict) -> str:
    digest_input = json.dumps({key: item.get(key) for key in ("external_id", "title", "published_at", "source_url", "media_sha256", "subtitle_text")}, sort_keys=True, ensure_ascii=False)
    return item.get("content_hash") or hashlib.sha256(digest_input.encode()).hexdigest()


def create_unmatched_candidate(db: Session, item: dict) -> None:
    external_id = str(item["external_id"])
    source_id = f"kzkt:unmatched:{external_id}"
    event = db.scalar(select(RawEvent).where(RawEvent.source == "kzkt", RawEvent.source_event_id == source_id))
    if event:
        return
    event = RawEvent(source="kzkt", source_event_id=source_id, event_type="academic_recording", occurred_at=datetime.now(timezone.utc),
        content=str(item.get("title") or "空中课堂回放"), content_hash=recording_hash(item), metadata_={"recording": item})
    db.add(event); db.flush()
    db.add(InboxCandidate(candidate_type="academic_kzkt_recording", source_event_id=event.id, status=InboxStatus.pending,
        payload={"source": PROVIDER, "course_name": item.get("course_name"), "raw_text": item.get("title"), "recording_external_id": external_id},
        confidence=0.5, reason="空中课堂回放无法唯一匹配到课程"))


def ingest_snapshot(db: Session, snapshot: dict) -> dict:
    if snapshot.get("base_url") != get_settings().kzkt_base_url:
        raise ValueError("Unexpected 空中课堂 host")
    report = {"created": 0, "unchanged": 0, "unmatched": 0, "recording_ids": []}
    for item in snapshot.get("recordings", []):
        evidence = item.get("participation_evidence")
        if evidence is not None and evidence.get("filter") != "我参与的":
            raise ValueError("只允许导入本人参与课程的回放")
        course = match_course(db, str(item.get("course_name") or ""), item.get("section_code"))
        if not course:
            create_unmatched_candidate(db, item); report["unmatched"] += 1; continue
        external_course_id = str(item.get("course_external_id") or normalized(course.name))
        binding = db.scalar(select(CourseProviderBinding).where(CourseProviderBinding.provider == PROVIDER, CourseProviderBinding.external_id == external_course_id))
        if not binding:
            binding = CourseProviderBinding(course_id=course.id, provider=PROVIDER, external_id=external_course_id, base_url=get_settings().kzkt_base_url,
                metadata_={"course_name": item.get("course_name")})
            db.add(binding); db.flush()
        published_at = parse_time(item.get("published_at"))
        taught_at = parse_time(item.get("taught_at"))
        term = db.get(AcademicTerm, course.term_id)
        if taught_at and not term.starts_on <= taught_at.astimezone(ZoneInfo("Asia/Shanghai")).date() < term.starts_on + timedelta(weeks=term.teaching_weeks):
            report["unmatched"] += 1
            continue
        media_path = allowed_media_path(item.get("local_path"))
        digest = recording_hash(item)
        session = matching_session(db, course.id, taught_at, item.get("platform_session_id"))
        metadata = {key: item.get(key) for key in ("subtitle_text", "subtitle_segments", "taught_at", "platform_session_id", "participation_evidence", "media_sha256") if item.get(key) is not None}
        metadata["session_match"] = "matched" if session else "pending"
        existing = db.scalar(select(CourseRecording).where(CourseRecording.provider == PROVIDER,
            CourseRecording.external_id == str(item["external_id"])).order_by(CourseRecording.created_at.desc()))
        if existing:
            previous = existing.metadata_ or {}
            changed_media = previous.get("media_sha256") and item.get("media_sha256") and previous["media_sha256"] != item["media_sha256"]
            changed_subtitle = previous.get("subtitle_text") and item.get("subtitle_text") and previous["subtitle_text"] != item["subtitle_text"]
            if changed_media or changed_subtitle:
                # Enrichment preserves identity; a changed source keeps the older
                # replay/transcript intact as a distinct content version.
                existing = db.scalar(select(CourseRecording).where(CourseRecording.provider == PROVIDER,
                    CourseRecording.external_id == str(item["external_id"]), CourseRecording.content_hash == digest))
        if existing:
            report["recording_ids"].append(str(existing.id))
            if existing.course_id != course.id:
                raise ValueError("回放课程与已有绑定不一致")
            existing.metadata_ = {**(existing.metadata_ or {}), **metadata}
            if session: existing.class_session_id = session.id
            if published_at: existing.published_at = published_at
            if media_path and not existing.local_path:
                existing.local_path = str(media_path)
                existing.media_expires_at = None
                existing.availability, existing.error = "downloaded", None
                existing.metadata_ = {**existing.metadata_, "retention_version": 2}
                report["updated"] = report.get("updated", 0) + 1
            else:
                report["unchanged"] += 1
            continue
        recording = CourseRecording(course_id=course.id, class_session_id=session.id if session else None, provider_binding_id=binding.id,
            provider=PROVIDER, external_id=str(item["external_id"]), title=str(item.get("title") or course.name), source_url=item.get("source_url"),
            published_at=published_at, content_hash=digest, local_path=str(media_path) if media_path else None,
            media_expires_at=None, availability="downloaded" if media_path else "discovered",
            metadata_={**metadata, "retention_version": 2})
        db.add(recording); db.flush(); report["created"] += 1
        report["recording_ids"].append(str(recording.id))
        binding.last_synced_at = datetime.now(timezone.utc)
    db.commit()
    return report


def transcript_from_subtitle(recording: CourseRecording) -> tuple[str, list[dict]] | None:
    text = str((recording.metadata_ or {}).get("subtitle_text") or "").strip()
    segments = (recording.metadata_ or {}).get("subtitle_segments") or []
    if text:
        return text, segments if isinstance(segments, list) else []
    return None


def transcribe_locally(recording: CourseRecording) -> tuple[str, list[dict]]:
    media = allowed_media_path(recording.local_path)
    if not media:
        raise RuntimeError("回放尚未下载，无法本地转写")
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("faster-whisper 未安装；请安装本地转写组件") from exc
    cfg=get_settings().values
    if not cfg.get("whisper_enabled"): raise RuntimeError("请先在设置中启用并下载本地转写组件")
    import ctranslate2
    gpu=cfg.get("whisper_device","auto") != "cpu" and ctranslate2.get_cuda_device_count()>0
    model = WhisperModel(get_settings().kzkt_whisper_model, device="cuda" if gpu else "cpu", compute_type="int8_float16" if gpu else "int8", local_files_only=True,
        download_root=str(storage_root().parent / "WhisperCache" / "hub"))
    chunks, info = model.transcribe(str(media), language="zh", vad_filter=True)
    segments = []
    last_report = -30
    for chunk in chunks:
        if chunk.text.strip(): segments.append({"start": round(chunk.start, 2), "end": round(chunk.end, 2), "text": chunk.text.strip()})
        if chunk.end - last_report >= 30:
            last_report = chunk.end
            (storage_root()/".runtime"/"kzkt"/"status.json").write_text(json.dumps({"status":"syncing","message":"Whisper 正在本地转写课堂音频","progress":{"stage":"transcribing","current":round(chunk.end),"total":round(info.duration),"percent":round(100*chunk.end/max(info.duration,1))}},ensure_ascii=False),encoding="utf-8")
    if not segments:
        raise RuntimeError("Whisper 未识别到可用的课堂文本")
    return "\n".join(chunk["text"] for chunk in segments), segments


def summary_chunks(text: str, segments: list[dict], limit: int = 2400) -> list[tuple[str, list[float]]]:
    rows = [(str(item.get("text") or ""), item.get("start")) for item in segments] if segments else [(text, None)]
    chunks, parts, times, size = [], [], [], 0
    for content, start in rows:
        for offset in range(0, len(content), limit - 80):
            piece = content[offset:offset + limit - 80]
            line = (f"[{start}秒] " if start is not None else "") + piece
            if size + len(line) > limit and parts:
                chunks.append(("\n".join(parts), times)); parts, times, size = [], [], 0
            parts.append(line); size += len(line) + 1
            if start is not None: times.append(float(start))
    if parts: chunks.append(("\n".join(parts), times))
    if not chunks or not text.strip(): raise RuntimeError("课堂文本为空")
    return chunks


def validate_summary(result: dict, evidence: str, times: list[float]) -> dict:
    if not isinstance(result, dict) or not isinstance(result.get("overall_summary"), str) or not result["overall_summary"].strip():
        raise RuntimeError("本地模型返回了空或无效摘要")
    for key in ("segments", "assignments", "readings"):
        if not isinstance(result.get(key), list): raise RuntimeError(f"摘要缺少 {key} 列表")
        for item in result[key]:
            if not isinstance(item, dict): raise RuntimeError("摘要条目格式无效")
            content = item.get("summary" if key == "segments" else "text")
            if not isinstance(content, str) or not content.strip(): raise RuntimeError("摘要条目为空")
            if key == "segments":
                item.setdefault("title", "课堂内容")
                if not isinstance(item["title"], str) or not item["title"].strip():
                    raise RuntimeError("摘要标题无效")
                for field in ("key_concepts", "teacher_emphasis"):
                    item.setdefault(field, [])
                    if not isinstance(item[field], list) or not all(isinstance(value, str) for value in item[field]):
                        raise RuntimeError("摘要概念和重点必须是文本列表")
            stamp = item.get("start_seconds")
            if times and (isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not min(times) <= stamp <= max(times)):
                raise RuntimeError("摘要时间戳超出输入证据范围")
            if not times: item["start_seconds"] = None
            if key != "segments":
                quote = item.get("evidence")
                if not isinstance(quote, str) or not quote.strip() or quote not in evidence:
                    raise RuntimeError("作业或阅读缺少可核对的原文依据")
    if not result["segments"]: raise RuntimeError("摘要未返回课堂分段")
    # Models sometimes repeat a reading instruction in both arrays. Reading-only
    # requirements have one candidate; exercises accompanying reading stay separate.
    readings = result["readings"]
    assignments = []
    for item in result["assignments"]:
        if re.search(r"阅读|预习|读教材|读第", item["text"]) and not re.search(r"习题|练习|提交|报告|解答", item["text"]):
            if not any(row["evidence"] == item["evidence"] for row in readings): readings.append(item)
        else:
            assignments.append(item)
    result["assignments"] = assignments
    return result


def bind_summary_sources(result: dict, sources: list[dict]) -> dict:
    for key in ("segments", "assignments", "readings"):
        for item in result.get(key, []):
            index = item.get("source_index")
            if isinstance(index, bool) or not isinstance(index, int) or not 1 <= index <= len(sources):
                raise RuntimeError("摘要引用了不存在的原文编号")
            source = sources[index - 1]
            item["start_seconds"] = source["start"]
            if key != "segments": item["evidence"] = source["text"]
    return result


def summary_schema(source_count: int) -> dict:
    string = {"type": "string"}
    reference = {"type": "integer", "enum": list(range(1, source_count + 1))}
    requirement = {"type": "object", "properties": {"text": string, "source_index": reference}, "required": ["text", "source_index"], "additionalProperties": False}
    segment_fields = {"title": string, "summary": string, "source_index": reference,
        "key_concepts": {"type": "array", "items": string}, "teacher_emphasis": {"type": "array", "items": string}}
    return {"type": "object", "properties": {
        "overall_summary": string,
        "segments": {"type": "array", "items": {"type": "object", "properties": segment_fields, "required": list(segment_fields), "additionalProperties": False}},
        "assignments": {"type": "array", "items": requirement}, "readings": {"type": "array", "items": requirement}},
        "required": ["overall_summary", "segments", "assignments", "readings"], "additionalProperties": False}


def summarize_locally(text: str, segments: list[dict]) -> dict:
    merged = {"segments": [], "assignments": [], "readings": [], "overall_summary": "", "review_version": REVIEW_VERSION}
    summaries = []
    chunks = summary_chunks(text, segments)
    cache = storage_root() / ".runtime" / "kzkt" / "summary_cache"
    cache.mkdir(parents=True, exist_ok=True)
    for number, (evidence, times) in enumerate(chunks, 1):
        rows = re.findall(r"^\[([0-9.]+)秒\] (.*?)(?=^\[[0-9.]+秒\] |\Z)", evidence, re.M | re.S)
        sources = [{"start": float(stamp), "text": content.strip()} for stamp, content in rows] if rows else [{"start": None, "text": evidence}]
        prompt = """分析真实课堂转写，只输出符合格式的 JSON，不要输出模板。
overall_summary 总结实际内容（200字内）；segments 最多3个主题，每项含标题、100字内总结、关键概念、老师强调内容。
所有条目必须用 source_index 引用下方来源编号，时间和原文由系统填入。
assignments 只列明确布置的课后作业；课堂上当场做的练习不能列入。readings 只列明确布置的阅读或预习。普通提及参考书、建议购买教辅不是阅读任务。没有明确要求时列表必须为空。
不要从课程考核规则推断出具体作业。保留数学前提条件，不擅自修正不清楚的转写；不确定处说明转写不清楚。/no_think\n"""
        prompt += "\n".join(f"[来源{i}] {source['text']}" for i, source in enumerate(sources, 1))
        digest = hashlib.sha256((REVIEW_VERSION + model_identity() + evidence).encode()).hexdigest()
        cache_file = cache / f"{digest}.json"
        status_path = storage_root() / ".runtime" / "kzkt" / "status.json"
        status_path.write_text(json.dumps({"status": "syncing", "checked_at": datetime.now(timezone.utc).isoformat(),
            "message": f"正在生成复盘：第 {number}/{len(chunks)} 段", "progress": {"stage": "reviewing", "percent": 75 + int(15 * number / len(chunks)), "current": number, "total": len(chunks)}}, ensure_ascii=False), encoding="utf-8")
        if cache_file.is_file():
            result = validate_summary(json.loads(cache_file.read_text(encoding="utf-8")), evidence, times)
        else:
            try:
                result = validate_summary(bind_summary_sources(chat_json(prompt, summary_schema(len(sources))), sources), evidence, times)
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                raise RuntimeError(f"本地摘要第 {number} 段失败，已保留此前有效分段") from exc
            cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        for key in ("segments", "assignments", "readings"): merged[key].extend(result[key])
        summaries.append(result["overall_summary"])
    merged["overall_summary"] = "\n\n".join(summaries)
    merged["transcript_characters"] = len(text)
    return merged


def create_assignment_candidates(db: Session, recording: CourseRecording, summary: dict) -> int:
    if (recording.metadata_ or {}).get("classroom_requirements"):
        return 0
    created = 0
    grouped = {}
    for kind, key in (("assignment", "assignments"), ("reading", "readings")):
        for item in summary.get(key, []):
            content = str(item.get("text") or "").strip()
            if not content: continue
            evidence = str(item.get("evidence") or content)
            group = grouped.setdefault((kind, evidence), {**item, "texts": []})
            if content not in group["texts"]: group["texts"].append(content)
    for (kind, evidence), assignment in grouped.items():
        content = "；".join(assignment["texts"])
        source_id = f"kzkt:{recording.id}:{kind}:{hashlib.sha256(evidence.encode()).hexdigest()[:16]}"
        existing = db.scalar(select(RawEvent).where(RawEvent.source == "kzkt", RawEvent.source_event_id == source_id))
        if existing:
            if existing.content != content:
                pending = db.scalar(select(InboxCandidate).where(InboxCandidate.source_event_id == existing.id, InboxCandidate.status == InboxStatus.pending))
                if pending:
                    pending.payload = {**pending.payload, "raw_text": content}
                    existing.content = content
                    existing.content_hash = hashlib.sha256(content.encode()).hexdigest()
            continue
        seconds = assignment.get("start_seconds")
        event = RawEvent(source="kzkt", source_event_id=source_id, event_type="academic_notice", occurred_at=datetime.now(timezone.utc), content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(), metadata_={"recording_id": str(recording.id), "timestamp_seconds": seconds})
        db.add(event); db.flush()
        db.add(InboxCandidate(candidate_type="academic_notice", source_event_id=event.id, status=InboxStatus.pending,
            payload={"source": PROVIDER, "course_id": str(recording.course_id), "course_name": recording.title, "raw_text": content,
                "notice_kind": kind, "evidence": assignment.get("evidence"), "recording_id": str(recording.id), "timestamp_seconds": seconds, "source_url": recording.source_url},
            confidence=0.6, reason="空中课堂识别到待确认的作业或阅读要求")); created += 1
    return created


def process_recording(db: Session, recording: CourseRecording) -> dict:
    source = "platform_subtitle"
    extracted = transcript_from_subtitle(recording)
    if not extracted:
        source = "faster_whisper"
        cached = db.scalar(select(RecordingTranscript).where(RecordingTranscript.recording_id == recording.id).order_by(RecordingTranscript.created_at.desc()))
        extracted = (cached.text, cached.segments) if cached else transcribe_locally(recording)
    text, segments = extracted
    digest = hashlib.sha256(json.dumps({"text": text, "segments": segments}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    transcript = db.scalar(select(RecordingTranscript).where(RecordingTranscript.recording_id == recording.id, RecordingTranscript.content_hash == digest))
    if not transcript:
        transcript = RecordingTranscript(recording_id=recording.id, content_hash=digest, source=source, language="zh", text=text, segments=segments,
            model_name=None if source == "platform_subtitle" else get_settings().kzkt_whisper_model)
        db.add(transcript); db.flush()
    db.commit()  # Keep expensive transcription even when summary generation fails.
    if get_settings().values.get("ai",{}).get("provider","none")=="none":
        return {"recording_id":str(recording.id),"status":"transcript_ready"}
    from .kzkt_queue import enqueue_requirements
    enqueue_requirements(db, recording_id=recording.id)
    review_digest = hashlib.sha256((digest + model_identity() + REVIEW_VERSION).encode()).hexdigest()
    review = db.scalar(select(LessonReview).where(LessonReview.recording_id == recording.id, LessonReview.content_hash == review_digest))
    if review:
        create_assignment_candidates(db, recording, review.summary)
        recording.availability, recording.error = "review_ready", None
        if recording.local_path and not recording.media_expires_at:
            recording.media_expires_at = datetime.now(timezone.utc) + timedelta(days=14)
        return {"recording_id": str(recording.id), "status": "unchanged"}
    summary = summarize_locally(text, segments)
    review = LessonReview(recording_id=recording.id, transcript_id=transcript.id, content_hash=review_digest, summary=summary, model_name=model_identity())
    db.add(review)
    recording.availability, recording.error = "review_ready", None
    if recording.local_path and not recording.media_expires_at:
        recording.media_expires_at = datetime.now(timezone.utc) + timedelta(days=14)
    assignments = create_assignment_candidates(db, recording, summary)
    return {"recording_id": str(recording.id), "status": "ready", "assignments": assignments}


def process_pending_recordings(db: Session, course_id: Any | None = None, recording_id: Any | None = None) -> dict:
    query = select(CourseRecording).where(CourseRecording.provider == PROVIDER, CourseRecording.availability.in_(["downloaded", "discovered", "failed", "review_ready", "media_expired"]))
    if course_id:
        query = query.where(CourseRecording.course_id == course_id)
    if recording_id:
        query = query.where(CourseRecording.id == recording_id)
    results = []
    for recording in db.scalars(query.order_by(CourseRecording.created_at)):
        if not transcript_from_subtitle(recording) and not recording.local_path and not db.scalar(select(RecordingTranscript.id).where(RecordingTranscript.recording_id == recording.id)):
            recording.availability, recording.error = "discovered", "回放尚未下载，等待平台提供可访问媒体"
            db.commit(); results.append({"recording_id": str(recording.id), "status": "awaiting_media"}); continue
        try:
            results.append(process_recording(db, recording)); db.commit()
        except Exception as exc:
            db.rollback()
            recording = db.get(CourseRecording, recording.id)
            recording.availability, recording.error = "failed", str(exc)
            db.commit(); results.append({"recording_id": str(recording.id), "status": "failed", "error": str(exc)})
    from personal_os_api.lesson_knowledge import process_recording_slides
    for item in results:
        if item["status"] not in {"ready", "unchanged"}: continue
        recording = db.get(CourseRecording, item["recording_id"])
        if not recording.local_path:
            item["slides"] = {"status": "awaiting_media"}
            continue
        try:
            item["slides"] = process_recording_slides(db, recording.id)
        except Exception as exc:
            db.rollback()
            item["slides"] = {"status": "failed", "error": str(exc)}
    failures = sum(item["status"] in {"failed", "awaiting_media"} or item.get("slides", {}).get("status") in {"failed", "awaiting_media"} for item in results)
    return {"results": results, "failed_count": failures, "status": "partial_failure" if failures else "success"}


def process_recordings_serialized(db: Session, course_id: Any | None = None, recording_id: Any | None = None) -> dict:
    """The API and worker share one local GPU; hold a separate session lock across commits."""
    from personal_os_api.db import engine
    with engine.connect() as lock:
        acquired = lock.scalar(sql_text("SELECT pg_try_advisory_lock(70631985)"))
        lock.commit()
        if not acquired:
            return {"results": [], "failed_count": 1, "status": "processing_busy", "error": "另一条空中课堂处理正在运行，请稍后重试"}
        try:
            return process_pending_recordings(db, course_id, recording_id)
        finally:
            lock.execute(sql_text("SELECT pg_advisory_unlock(70631985)"))
            lock.commit()


def cleanup_expired_media(db: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    removed = 0
    for recording in db.scalars(select(CourseRecording).where(CourseRecording.provider == PROVIDER, CourseRecording.local_path.is_not(None))):
        review = db.scalar(select(LessonReview).where(LessonReview.recording_id == recording.id, LessonReview.status == "ready").order_by(LessonReview.created_at))
        if (recording.metadata_ or {}).get("retention_version") != 2:
            recording.media_expires_at = review.created_at + timedelta(days=14) if review else None
            recording.metadata_ = {**(recording.metadata_ or {}), "retention_version": 2}
            continue  # Migration and deletion never happen in the same pass.
        if not review:
            recording.media_expires_at = None
            continue
        if not recording.media_expires_at or recording.media_expires_at > now: continue
        try:
            path = allowed_media_path(recording.local_path)
            if path: path.unlink(missing_ok=True)
        except (ValueError, OSError):
            continue
        recording.local_path, recording.media_expires_at = None, None
        if recording.availability == "review_ready": recording.availability = "media_expired"
        removed += 1
    db.commit()
    return removed

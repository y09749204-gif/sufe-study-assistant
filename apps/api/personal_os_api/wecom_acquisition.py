"""Durable, fail-closed acquisition flow for locally visible WeCom attachments."""
from __future__ import annotations

import json
import os
import subprocess
import base64
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from personal_os_api.models import (
    AcademicCourse, AttachmentAcquisition, AttachmentAcquisitionEvent, CourseMessage, CourseProviderBinding, CourseResource,
)

ACTIVE = {"queued", "probing", "locating", "waiting_user", "downloaded"}
TERMINAL = {"archived", "blocked_policy", "failed", "cancelled"}
INTERACTION_MODES = {"foreground_assist", "background"}
SAFE_EVIDENCE_KEYS = {
    "allowed_file_type", "candidate_count", "canonical_query_no_result", "client_version",
    "conversation_exact", "header_attempts", "header_course_exact", "query_exact", "query_scope",
    "result_count", "result_match_mode", "search_anchor_count", "shadow_mode",
}


def _safe_evidence(evidence: dict | None) -> dict:
    """Keep only structural booleans/counts; never persist OCR or chat content in events."""
    if not isinstance(evidence, dict):
        return {}
    return {key: value for key, value in evidence.items()
        if key in SAFE_EVIDENCE_KEYS and isinstance(value, (bool, int, float, type(None)))}


def record_event(db: Session, item: AttachmentAcquisition, stage: str, *, result_code: str | None = None,
        evidence: dict | None = None, elapsed_ms: int | None = None) -> None:
    db.add(AttachmentAcquisitionEvent(acquisition_id=item.id, stage=stage[:32],
        result_code=result_code[:80] if result_code else None, elapsed_ms=elapsed_ms,
        evidence=_safe_evidence(evidence)))


def client_details() -> tuple[bool, str | None]:
    executable = Path("C:/Program Files (x86)/WXWork/WXWork.exe")
    if not executable.is_file():
        return False, None
    command = f"(Get-Item -LiteralPath '{executable}').VersionInfo.ProductVersion"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True,
        text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    version = result.stdout.strip() if result.returncode == 0 else None
    running = subprocess.run(["tasklist.exe", "/FI", "IMAGENAME eq WXWork.exe", "/NH"], capture_output=True,
        text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return "WXWork.exe" in running.stdout, version


def write_probe_report(runtime_root: Path) -> dict:
    running, version = client_details()
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(), "client": "WXWork", "client_version": version,
        "client_running": running, "local_ipc": {"status": "not_validated", "enabled": False,
            "reason": "no stable non-injection download endpoint has passed three-file and restart acceptance"},
        "fallback": "user_triggered_ui_assist",
        "prohibited": ["dll_injection", "session_credential_extraction", "tls_interception", "dlp_bypass"],
    }
    path = runtime_root / "wecom" / "attachment-probe.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return report


def resource_context(db: Session, resource: CourseResource) -> tuple[AcademicCourse, CourseMessage, CourseProviderBinding]:
    if resource.provider != "wecom" or resource.course_message_id is None:
        raise ValueError("Only a WeCom message attachment can be acquired")
    course = db.get(AcademicCourse, resource.course_id)
    message = db.get(CourseMessage, resource.course_message_id)
    binding = db.get(CourseProviderBinding, message.provider_binding_id) if message else None
    if not course or not message or not binding or binding.provider != "wecom" or binding.state != "active" or binding.course_id != course.id:
        raise ValueError("Attachment is not part of a confirmed course conversation")
    return course, message, binding


def acquisition_payload(db: Session, item: AttachmentAcquisition) -> dict:
    resource = db.get(CourseResource, item.resource_id)
    course, message, binding = resource_context(db, resource)
    events = list(db.scalars(select(AttachmentAcquisitionEvent).where(
        AttachmentAcquisitionEvent.acquisition_id == item.id).order_by(
        AttachmentAcquisitionEvent.created_at.desc()).limit(30)))
    events.reverse()
    same_name_bindings = [candidate for candidate in db.scalars(select(CourseProviderBinding).where(
        CourseProviderBinding.provider == "wecom", CourseProviderBinding.state == "active"))
        if (candidate.metadata_ or {}).get("conversation_name") == (binding.metadata_ or {}).get("conversation_name")]
    return {"id": str(item.id), "resource_id": str(item.resource_id), "course_id": str(course.id),
        "course_name": course.name, "title": resource.title, "byte_size": resource.byte_size,
        "availability": resource.availability, "sender_name": message.sender_name, "sent_at": message.sent_at,
        "conversation_name": (binding.metadata_ or {}).get("conversation_name"),
        "conversation_name_unique": len(same_name_bindings) == 1, "status": item.status,
        "strategy": item.strategy, "trigger": item.trigger, "interaction_mode": item.interaction_mode,
        "automation_stage": item.automation_stage, "countdown_until": item.countdown_until,
        "attempt": item.attempt, "client_version": item.client_version,
        "error_code": item.error_code, "metadata": item.metadata_, "created_at": item.created_at,
        "match_evidence": item.match_evidence, "next_attempt_at": item.next_attempt_at,
        "started_at": item.started_at, "finished_at": item.finished_at, "updated_at": item.updated_at,
        "events": [{"stage": event.stage, "result_code": event.result_code, "elapsed_ms": event.elapsed_ms,
            "evidence": event.evidence, "created_at": event.created_at} for event in events]}


def latest_acquisition(db: Session, resource_id) -> AttachmentAcquisition | None:
    return db.scalar(select(AttachmentAcquisition).where(AttachmentAcquisition.resource_id == resource_id)
        .order_by(AttachmentAcquisition.created_at.desc()).limit(1))


def list_missing(db: Session) -> list[dict]:
    resources = list(db.scalars(select(CourseResource).where(CourseResource.provider == "wecom",
        CourseResource.availability != "available").order_by(CourseResource.created_at.desc())))
    result = []
    for resource in resources:
        try:
            course, message, binding = resource_context(db, resource)
        except ValueError:
            continue
        acquisition = latest_acquisition(db, resource.id)
        result.append({"resource_id": str(resource.id), "course_id": str(course.id), "course_name": course.name,
            "title": resource.title, "byte_size": resource.byte_size, "availability": resource.availability,
            "sender_name": message.sender_name, "sent_at": message.sent_at,
            "conversation_name": (binding.metadata_ or {}).get("conversation_name"),
            "acquisition": acquisition_payload(db, acquisition) if acquisition else None})
    return result


def create_acquisition(db: Session, resource: CourseResource, client_version: str | None, *,
        trigger: str = "manual", idempotency_key: str | None = None, user_triggered: bool = True,
        interaction_mode: str = "background") -> AttachmentAcquisition:
    if interaction_mode not in INTERACTION_MODES:
        raise ValueError("Unsupported attachment interaction mode")
    resource_context(db, resource)
    if idempotency_key:
        prior = db.scalar(select(AttachmentAcquisition).where(AttachmentAcquisition.idempotency_key == idempotency_key))
        if prior:
            return prior
    existing = db.scalar(select(AttachmentAcquisition).where(AttachmentAcquisition.resource_id == resource.id,
        AttachmentAcquisition.active_key == "active").limit(1))
    if existing:
        return existing
    now = datetime.now(timezone.utc)
    countdown_until = now + timedelta(seconds=3) if interaction_mode == "foreground_assist" else None
    item = AttachmentAcquisition(resource_id=resource.id, status="queued", strategy="passive_cache",
        trigger=trigger, interaction_mode=interaction_mode,
        automation_stage="countdown" if countdown_until else "queued", active_key="active", client_version=client_version,
        idempotency_key=idempotency_key, countdown_until=countdown_until,
        next_attempt_at=countdown_until or now,
        metadata_={"user_triggered": user_triggered})
    db.add(item)
    try:
        db.flush()
        record_event(db, item, item.automation_stage or "queued")
        db.commit(); db.refresh(item)
    except IntegrityError:
        db.rollback()
        item = db.scalar(select(AttachmentAcquisition).where(AttachmentAcquisition.resource_id == resource.id,
            AttachmentAcquisition.active_key == "active"))
    return item


def finish(item: AttachmentAcquisition, status: str, error_code: str | None = None) -> None:
    item.status = status; item.error_code = error_code; item.active_key = None
    item.automation_stage = status
    item.finished_at = datetime.now(timezone.utc); item.claim_token = None; item.lease_expires_at = None
    item.next_attempt_at = None; item.countdown_until = None


def claim_next(db: Session, max_attempts: int = 3) -> dict | None:
    now = datetime.now(timezone.utc)
    exhausted = list(db.scalars(select(AttachmentAcquisition).where(
        AttachmentAcquisition.active_key == "active", AttachmentAcquisition.status == "queued",
        AttachmentAcquisition.attempt >= max_attempts)))
    for stale in exhausted:
        finish(stale, "failed", "retry_exhausted")
        stale.automation_stage = "failed"
        stale.metadata_ = {**(stale.metadata_ or {}), "instruction": "自动尝试已达3次，请手动处理"}
    if exhausted: db.commit()
    item = db.scalar(select(AttachmentAcquisition).where(
        AttachmentAcquisition.active_key == "active",
        AttachmentAcquisition.status.in_(["queued", "locating"]),
        AttachmentAcquisition.attempt < max_attempts,
        (AttachmentAcquisition.next_attempt_at.is_(None) | (AttachmentAcquisition.next_attempt_at <= now)),
        (AttachmentAcquisition.claim_token.is_(None) | (AttachmentAcquisition.lease_expires_at < now)),
    ).order_by(AttachmentAcquisition.created_at).with_for_update(skip_locked=True).limit(1))
    if not item:
        return None
    token = secrets.token_hex(24)
    item.claim_token = token; item.lease_expires_at = now + timedelta(minutes=8)
    item.status = "locating"; item.strategy = "ui_assist"
    item.automation_stage = "opening_wecom" if item.interaction_mode == "foreground_assist" else "waiting_idle"
    item.attempt += 1; item.started_at = item.started_at or now; item.error_code = None
    record_event(db, item, item.automation_stage)
    db.commit(); db.refresh(item)
    payload = acquisition_payload(db, item)
    payload["claim_token"] = token
    return payload


def update_claim(db: Session, acquisition_id, claim_token: str, *, action: str,
        stage: str | None = None, evidence: dict | None = None, error_code: str | None = None,
        note: str | None = None, events: list[dict] | None = None) -> dict:
    now = datetime.now(timezone.utc)
    item = db.scalar(select(AttachmentAcquisition).where(AttachmentAcquisition.id == acquisition_id,
        AttachmentAcquisition.claim_token == claim_token).with_for_update())
    if not item:
        raise ValueError("Attachment acquisition claim is stale")
    if evidence is not None: item.match_evidence = evidence
    if stage is not None: item.automation_stage = stage
    if note is not None: item.metadata_ = {**(item.metadata_ or {}), "instruction": note}
    for event in events or []:
        if isinstance(event, dict) and event.get("stage"):
            record_event(db, item, str(event["stage"]), result_code=event.get("result_code"),
                evidence=event.get("evidence"), elapsed_ms=event.get("elapsed_ms"))
    if action == "progress":
        item.lease_expires_at = now + timedelta(minutes=8)
    elif action == "defer":
        item.status = "queued"; item.error_code = error_code; item.next_attempt_at = now + timedelta(minutes=15)
        if error_code == "desktop_busy" or stage == "user_intervened":
            # Waiting for a safe desktop opportunity is not an acquisition
            # attempt and must not exhaust the three real matching attempts.
            item.attempt = max(0, item.attempt - 1)
        item.claim_token = None; item.lease_expires_at = None
    elif action == "waiting_user":
        item.status = "waiting_user"; item.error_code = error_code; item.next_attempt_at = None
        item.claim_token = None; item.lease_expires_at = None
    elif action == "downloaded":
        item.status = "downloaded"; item.error_code = error_code; item.next_attempt_at = now
        item.claim_token = None; item.lease_expires_at = None
    elif action in TERMINAL:
        finish(item, action, error_code)
    else:
        raise ValueError("Unsupported acquisition claim action")
    record_event(db, item, stage or item.automation_stage or item.status,
        result_code=error_code, evidence=evidence,
        elapsed_ms=max(0, int((now - item.started_at).total_seconds() * 1000)) if item.started_at else None)
    db.commit(); db.refresh(item)
    return acquisition_payload(db, item)


def reconcile_acquisitions(db: Session) -> int:
    updated = 0
    items = list(db.scalars(select(AttachmentAcquisition).where(AttachmentAcquisition.active_key == "active")))
    for item in items:
        resource = db.get(CourseResource, item.resource_id)
        if resource and resource.availability == "available":
            finish(item, "archived")
            item.metadata_ = {**(item.metadata_ or {}), "instruction": "已从企微缓存校验并归档"}
            record_event(db, item, "archived")
            updated += 1
        elif resource and resource.availability == "blocked_policy":
            finish(item, "blocked_policy", "blocked_policy")
            record_event(db, item, "blocked_policy", result_code="blocked_policy")
            updated += 1
    if updated: db.commit()
    return updated

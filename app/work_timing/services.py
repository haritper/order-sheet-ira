from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from flask import current_app

from app.extensions import db
from app.models import Order, WorkTimingAlertEvent, WorkTimingEntry


WORK_TIMING_STATUS_OPTIONS = [
    "PP SAMPLE SENT FOR APPROVAL",
    "PP APPROVED",
    "PRINTING+LASER",
    "READY FOR SHIPPMENT",
    "DELIVERED",
]
DEFAULT_WORK_TIMING_STATUS = WORK_TIMING_STATUS_OPTIONS[0]

WORK_TIMING_SLA_HOURS_BY_STATUS = {
    "PP SAMPLE SENT FOR APPROVAL": 24,
    "PP APPROVED": 72,
    "PRINTING+LASER": 96,
    "READY FOR SHIPPMENT": None,
    "DELIVERED": None,
}

ESCALATION_NONE = "NONE"
ESCALATION_GIRI_SENT = "GIRI_SENT"
ESCALATION_MD_SENT = "MD_SENT"
ESCALATION_RESOLVED = "RESOLVED"
GIRI_TO_MD_ESCALATION_HOURS = 12


def get_next_status(current_status: str) -> str | None:
    normalized = str(current_status or "").strip()
    if normalized not in WORK_TIMING_STATUS_OPTIONS:
        return None
    idx = WORK_TIMING_STATUS_OPTIONS.index(normalized)
    if idx >= len(WORK_TIMING_STATUS_OPTIONS) - 1:
        return None
    return WORK_TIMING_STATUS_OPTIONS[idx + 1]


def get_allowed_status_choices(current_status: str) -> list[str]:
    normalized = str(current_status or "").strip()
    if normalized not in WORK_TIMING_STATUS_OPTIONS:
        return [normalized] if normalized else [DEFAULT_WORK_TIMING_STATUS]
    next_status = get_next_status(normalized)
    if next_status:
        return [normalized, next_status]
    return [normalized]


def compute_deadline_for_status(status: str, from_time: datetime) -> datetime | None:
    hours = WORK_TIMING_SLA_HOURS_BY_STATUS.get(str(status or "").strip())
    if hours is None:
        return None
    return from_time + timedelta(hours=int(hours))


def ensure_work_timing_entry(order: Order) -> WorkTimingEntry:
    existing = WorkTimingEntry.query.filter_by(order_id=int(order.id)).first()
    if existing:
        if existing.deadline_at is None and existing.status in WORK_TIMING_SLA_HOURS_BY_STATUS:
            now = datetime.utcnow()
            existing.status_updated_at = existing.status_updated_at or now
            existing.deadline_at = compute_deadline_for_status(existing.status, existing.status_updated_at)
        return existing

    now = datetime.utcnow()
    entry = WorkTimingEntry(
        order_id=int(order.id),
        order_code=str(order.production_order_id or order.order_id or f"ORDER-{order.id}").strip(),
        customer_name=str(order.customer_name or "").strip() or "Unknown",
        status=DEFAULT_WORK_TIMING_STATUS,
        status_updated_at=now,
        deadline_at=compute_deadline_for_status(DEFAULT_WORK_TIMING_STATUS, now),
        escalation_state=ESCALATION_NONE,
        giri_alert_sent_at=None,
        md_alert_sent_at=None,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def validate_forward_status_transition(current_status: str, next_status: str) -> None:
    # Backward-compatible wrapper name retained for existing call sites/tests.
    validate_next_step_status_transition(current_status, next_status)


def validate_next_step_status_transition(current_status: str, next_status: str) -> None:
    if next_status not in WORK_TIMING_STATUS_OPTIONS:
        raise ValueError("Invalid status selected.")
    if current_status not in WORK_TIMING_STATUS_OPTIONS:
        raise ValueError("Current status is invalid.")

    allowed_choices = get_allowed_status_choices(current_status)
    if next_status not in allowed_choices:
        raise ValueError("Only the immediate next status is allowed.")


def update_work_timing_status(entry: WorkTimingEntry, next_status: str, *, now: datetime | None = None) -> bool:
    validate_next_step_status_transition(str(entry.status or ""), str(next_status or ""))
    if entry.status == next_status:
        return False

    ts = now or datetime.utcnow()
    if entry.escalation_state in {ESCALATION_GIRI_SENT, ESCALATION_MD_SENT}:
        entry.escalation_state = ESCALATION_RESOLVED

    entry.status = next_status
    entry.status_updated_at = ts
    entry.deadline_at = compute_deadline_for_status(next_status, ts)
    entry.escalation_state = ESCALATION_NONE
    entry.giri_alert_sent_at = None
    entry.md_alert_sent_at = None
    return True


class AlertNotifier:
    def send_overdue_alert(self, target: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class LogOnlyAlertNotifier(AlertNotifier):
    def __init__(self, mode: str = "log"):
        self.mode = str(mode or "log").strip().lower()

    def send_overdue_alert(self, target: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.mode == "log":
            current_app.logger.warning(
                "Work timing overdue alert | mode=log target=%s order_code=%s status=%s deadline_at=%s",
                target,
                payload.get("order_code"),
                payload.get("status"),
                payload.get("deadline_at"),
            )
            return {"delivery_mode": "log", "delivery_result": "logged"}

        if self.mode == "webhook":
            return self._send_webhook_alert(target=target, payload=payload)

        # Unknown mode fallback to structured logging.
        current_app.logger.warning(
            "Work timing alert provider not implemented | mode=%s target=%s order_code=%s",
            self.mode,
            target,
            payload.get("order_code"),
        )
        return {
            "delivery_mode": self.mode,
            "delivery_result": "provider_not_implemented_logged",
        }

    def _send_webhook_alert(self, *, target: str, payload: dict[str, Any]) -> dict[str, Any]:
        webhook_url = str(current_app.config.get("WORK_TIMING_WEBHOOK_URL", "") or "").strip()
        webhook_token = str(current_app.config.get("WORK_TIMING_WEBHOOK_TOKEN", "") or "").strip()
        timeout_seconds = int(current_app.config.get("WORK_TIMING_WEBHOOK_TIMEOUT_SECONDS", 15))

        if not webhook_url:
            return {
                "delivery_mode": "webhook",
                "delivery_result": "failed_missing_webhook_url",
            }
        if not payload.get("contact"):
            return {
                "delivery_mode": "webhook",
                "delivery_result": "failed_missing_contact",
            }

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if webhook_token:
            headers["X-Internal-Token"] = webhook_token

        try:
            ack = _post_json_webhook(
                url=webhook_url,
                headers=headers,
                payload=payload,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - return structured failure without crashing checker
            err = _clip_token(f"failed_{exc}", 120)
            current_app.logger.warning(
                "Work timing webhook dispatch failed | target=%s order_code=%s error=%s",
                target,
                payload.get("order_code"),
                err,
            )
            return {"delivery_mode": "webhook", "delivery_result": err}

        if not bool(ack.get("ok", False)):
            reason = (
                str(ack.get("error") or ack.get("delivery_result") or "failed_bad_ack")
                .strip()
                .replace(" ", "_")
            )
            return {
                "delivery_mode": str(ack.get("delivery_mode") or "webhook"),
                "delivery_result": _clip_token(f"failed_{reason}", 120),
                "provider_message_id": str(ack.get("provider_message_id") or "").strip() or None,
            }

        return {
            "delivery_mode": str(ack.get("delivery_mode") or "whatsapp_template"),
            "delivery_result": _clip_token(str(ack.get("delivery_result") or "sent"), 120),
            "provider_message_id": str(ack.get("provider_message_id") or "").strip() or None,
        }


def _post_json_webhook(
    *,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(url=url, data=body, method="POST")
    for key, value in headers.items():
        req.add_header(str(key), str(value))

    try:
        with urllib_request.urlopen(req, timeout=max(1, int(timeout_seconds))) as resp:
            raw = resp.read().decode("utf-8", errors="replace").strip()
            if raw:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = {"raw_response": raw}
            else:
                parsed = {}

            if not isinstance(parsed, dict):
                parsed = {"raw_response": str(parsed)}
            parsed.setdefault("ok", 200 <= int(getattr(resp, "status", 200)) < 300)
            parsed.setdefault("statusCode", int(getattr(resp, "status", 200)))
            return parsed
    except urllib_error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace").strip()
        if raw:
            raise RuntimeError(f"http_{exc.code}_{raw}") from exc
        raise RuntimeError(f"http_{exc.code}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"network_error_{exc.reason}") from exc


def _build_alert_payload(entry: WorkTimingEntry, *, target: str, checked_at: datetime) -> dict[str, Any]:
    normalized_target = str(target or "").strip().upper()
    deadline_iso = entry.deadline_at.isoformat() if entry.deadline_at else ""
    contact = ""
    if normalized_target == "GIRI":
        contact = str(current_app.config.get("WORK_TIMING_GIRI_CONTACT", "") or "").strip()
    elif normalized_target == "MD":
        contact = str(current_app.config.get("WORK_TIMING_MD_CONTACT", "") or "").strip()

    alert_id = f"wt-{int(entry.id)}-{normalized_target}-{deadline_iso or 'no_deadline'}"
    return {
        "alert_id": alert_id,
        "entry_id": int(entry.id),
        "order_code": str(entry.order_code or ""),
        "customer_name": str(entry.customer_name or ""),
        "status": str(entry.status or ""),
        "deadline_at": deadline_iso,
        "checked_at": checked_at.isoformat(),
        "target": normalized_target,
        "contact": contact,
    }


def _clip_token(value: str, max_len: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len]


def _record_alert_event(
    entry: WorkTimingEntry,
    *,
    target: str,
    event_type: str,
    delivery_mode: str,
    delivery_result: str,
    provider_message_id: str | None = None,
) -> WorkTimingAlertEvent:
    event = WorkTimingAlertEvent(
        entry_id=int(entry.id),
        target=str(target),
        event_type=str(event_type),
        status_snapshot=str(entry.status or ""),
        deadline_snapshot=entry.deadline_at,
        delivery_mode=_clip_token(str(delivery_mode or "log"), 20),
        delivery_result=_clip_token(str(delivery_result or "logged"), 120),
        provider_message_id=_clip_token(str(provider_message_id or "").strip(), 255) or None,
    )
    db.session.add(event)
    return event


def run_work_timing_overdue_check(*, now: datetime | None = None, notifier: AlertNotifier | None = None) -> dict[str, int]:
    ts = now or datetime.utcnow()
    sender = notifier or LogOnlyAlertNotifier(mode=current_app.config.get("WORK_TIMING_ALERT_MODE", "log"))

    scanned = 0
    giri_sent = 0
    md_sent = 0
    for entry in WorkTimingEntry.query.filter(WorkTimingEntry.deadline_at.isnot(None)).all():
        scanned += 1
        if entry.deadline_at is None or entry.deadline_at > ts:
            continue

        if entry.escalation_state == ESCALATION_NONE:
            payload = _build_alert_payload(entry, target="GIRI", checked_at=ts)
            result = sender.send_overdue_alert("GIRI", payload)
            _record_alert_event(
                entry,
                target="GIRI",
                event_type="OVERDUE_ALERT",
                delivery_mode=result.get("delivery_mode", "log"),
                delivery_result=result.get("delivery_result", "logged"),
                provider_message_id=result.get("provider_message_id"),
            )
            entry.escalation_state = ESCALATION_GIRI_SENT
            entry.giri_alert_sent_at = ts
            giri_sent += 1
            continue

        if entry.escalation_state == ESCALATION_GIRI_SENT:
            if not entry.giri_alert_sent_at:
                entry.giri_alert_sent_at = ts
                continue
            if entry.giri_alert_sent_at + timedelta(hours=GIRI_TO_MD_ESCALATION_HOURS) <= ts:
                payload = _build_alert_payload(entry, target="MD", checked_at=ts)
                result = sender.send_overdue_alert("MD", payload)
                _record_alert_event(
                    entry,
                    target="MD",
                    event_type="OVERDUE_ALERT",
                    delivery_mode=result.get("delivery_mode", "log"),
                    delivery_result=result.get("delivery_result", "logged"),
                    provider_message_id=result.get("provider_message_id"),
                )
                entry.escalation_state = ESCALATION_MD_SENT
                entry.md_alert_sent_at = ts
                md_sent += 1

    db.session.commit()
    return {"scanned": scanned, "giri_sent": giri_sent, "md_sent": md_sent}

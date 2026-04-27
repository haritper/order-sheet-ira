from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
import uuid

from flask import current_app
import requests

from app.extensions import db
from app.models import Order, WorkTimingAlertEvent, WorkTimingEntry


WORK_TIMING_STATUS_OPTIONS = [
    "CUSTOMER APPROVAL",
    "PP SAMPLE SENT FOR APPROVAL",
    "PP APPROVED",
    "PRINTING+LASER",
    "READY FOR SHIPPMENT",
    "DELIVERED",
]
DEFAULT_WORK_TIMING_STATUS = WORK_TIMING_STATUS_OPTIONS[0]

WORK_TIMING_SLA_HOURS_BY_STATUS = {
    "CUSTOMER APPROVAL": 48,
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


def upsert_customer_approval_timing_entry(order: Order, *, now: datetime | None = None) -> WorkTimingEntry:
    ts = now or datetime.utcnow()
    status = "CUSTOMER APPROVAL"
    deadline = compute_deadline_for_status(status, ts)
    order_code = str(order.production_order_id or order.order_id or f"ORDER-{order.id}").strip()
    customer_name = str(order.customer_name or "").strip() or "Unknown"

    existing = WorkTimingEntry.query.filter_by(order_id=int(order.id)).first()
    if existing:
        existing.order_code = order_code
        existing.customer_name = customer_name
        existing.status = status
        existing.status_updated_at = ts
        existing.deadline_at = deadline
        existing.escalation_state = ESCALATION_NONE
        existing.giri_alert_sent_at = None
        existing.md_alert_sent_at = None
        return existing

    entry = WorkTimingEntry(
        order_id=int(order.id),
        order_code=order_code,
        customer_name=customer_name,
        status=status,
        status_updated_at=ts,
        deadline_at=deadline,
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
    def send_overdue_alert(self, target: str, payload: dict[str, Any]) -> dict[str, str]:
        raise NotImplementedError


class LogOnlyAlertNotifier(AlertNotifier):
    def __init__(self, mode: str = "log"):
        self.mode = str(mode or "log").strip().lower()

    def send_overdue_alert(self, target: str, payload: dict[str, Any]) -> dict[str, str]:
        if self.mode == "webhook":
            return _send_webhook_alert(target, payload)
        if self.mode == "log":
            current_app.logger.warning(
                "Work timing overdue alert | target=%s order_code=%s status=%s deadline_at=%s",
                target,
                payload.get("order_code"),
                payload.get("status"),
                payload.get("deadline_at"),
            )
            return {"delivery_mode": "log", "delivery_result": "logged"}

        # Placeholder until provider decision; fall back to structured logging.
        current_app.logger.warning(
            "Work timing alert provider not implemented | mode=%s target=%s order_code=%s",
            self.mode,
            target,
            payload.get("order_code"),
        )
        return {"delivery_mode": self.mode, "delivery_result": "provider_not_implemented_logged"}


def _post_json_webhook(url: str, token: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "X-Internal-Token": str(token or "").strip(),
    }
    try:
        response = requests.post(
            str(url or "").strip(),
            json=payload,
            headers=headers,
            timeout=float(timeout_seconds or 15),
        )
    except requests.RequestException as exc:
        return {
            "ok": False,
            "delivery_mode": "webhook",
            "delivery_result": f"request_error:{exc.__class__.__name__}",
            "provider_message_id": None,
        }

    ack = {}
    try:
        ack_raw = response.json()
        if isinstance(ack_raw, dict):
            ack = ack_raw
    except ValueError:
        ack = {}

    if 200 <= int(response.status_code) < 300:
        return {
            "ok": bool(ack.get("ok", True)),
            "delivery_mode": str(ack.get("delivery_mode", "webhook") or "webhook"),
            "delivery_result": str(ack.get("delivery_result", "accepted") or "accepted"),
            "provider_message_id": str(ack.get("provider_message_id", "") or "").strip() or None,
        }

    return {
        "ok": False,
        "delivery_mode": str(ack.get("delivery_mode", "webhook") or "webhook"),
        "delivery_result": str(
            ack.get("delivery_result", f"http_{int(response.status_code)}")
            or f"http_{int(response.status_code)}"
        ),
        "provider_message_id": str(ack.get("provider_message_id", "") or "").strip() or None,
    }


def _send_webhook_alert(target: str, payload: dict[str, Any]) -> dict[str, str]:
    webhook_url = str(current_app.config.get("WORK_TIMING_WEBHOOK_URL", "") or "").strip()
    webhook_token = str(current_app.config.get("WORK_TIMING_WEBHOOK_TOKEN", "") or "").strip()
    timeout_seconds = float(current_app.config.get("WORK_TIMING_WEBHOOK_TIMEOUT_SECONDS", 15) or 15)

    if not webhook_url:
        return {
            "delivery_mode": "webhook",
            "delivery_result": "webhook_url_missing",
            "provider_message_id": None,
        }
    if not webhook_token:
        return {
            "delivery_mode": "webhook",
            "delivery_result": "webhook_token_missing",
            "provider_message_id": None,
        }

    ack = _post_json_webhook(webhook_url, webhook_token, payload, timeout_seconds)
    return {
        "delivery_mode": str(ack.get("delivery_mode", "webhook") or "webhook"),
        "delivery_result": str(ack.get("delivery_result", "accepted") or "accepted"),
        "provider_message_id": str(ack.get("provider_message_id", "") or "").strip() or None,
    }


def _build_alert_payload(entry: WorkTimingEntry, *, target: str, contact: str, checked_at: datetime) -> dict[str, Any]:
    return {
        "alert_id": str(uuid.uuid4()),
        "entry_id": int(entry.id),
        "order_code": str(entry.order_code or ""),
        "customer_name": str(entry.customer_name or ""),
        "status": str(entry.status or ""),
        "deadline_at": entry.deadline_at.isoformat() if entry.deadline_at else "",
        "checked_at": checked_at.isoformat(),
        "target": str(target),
        "contact": str(contact or "").strip(),
    }


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
        delivery_mode=str(delivery_mode or "log"),
        delivery_result=str(delivery_result or "logged"),
        provider_message_id=str(provider_message_id or "").strip() or None,
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
            contact = str(current_app.config.get("WORK_TIMING_GIRI_CONTACT", "") or "").strip()
            payload = _build_alert_payload(entry, target="GIRI", contact=contact, checked_at=ts)
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
                contact = str(current_app.config.get("WORK_TIMING_MD_CONTACT", "") or "").strip()
                payload = _build_alert_payload(entry, target="MD", contact=contact, checked_at=ts)
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

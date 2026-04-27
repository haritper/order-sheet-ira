from __future__ import annotations

import io
import mimetypes
from datetime import date, datetime
from urllib.parse import urlencode

from flask import Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import login_required
from werkzeug.utils import secure_filename

from app.assign.services import send_assign_notification_email
from app.extensions import db
from app.models import (
    AssignDesignerContact,
    AssignNotificationEvent,
    AssignOrderState,
    Attachment,
    Order,
    Role,
)
from app.storage import ORDER_IMAGE_SECTION, delete, read_bytes, save_order_file
from app.utils import roles_required


assign_bp = Blueprint("assign", __name__, url_prefix="/assign")

ASSIGN_STATUS_OPTIONS = [
    "Order Sheet Recieved",
    "PP File Pending",
    "PP File Received",
    "PP File Approved",
    "Waiting for PP Approval",
    "Print File Working",
    "Print File Received",
    "Shipped",
]
ASSIGN_CATEGORY_OPTIONS = ["ACADEMY", "LEAGUE", "MASTERS", "RETAIL", "OTHER"]
FILE_REQUIRED_OPTIONS = ["Yes", "No"]
EMAIL_TRIGGER_STATUSES = {
    "PP File Pending": "PP_FILE_PENDING",
    "Print File Working": "PRINT_FILE_WORKING",
}


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _normalize_file_required(value: str) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"yes", "y", "true", "1"}:
        return "Yes"
    return "No"


def _parse_date_any(value: str | None) -> date | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return date.fromisoformat(token)
    except ValueError:
        pass
    if "." in token:
        parts = token.split(".")
        if len(parts) == 3:
            try:
                dd = int(parts[0])
                mm = int(parts[1])
                yyyy = int(parts[2])
                if yyyy < 100:
                    yyyy += 2000
                return date(yyyy, mm, dd)
            except (TypeError, ValueError):
                return None
    return None


def _iso_date(value: date | None) -> str:
    return value.isoformat() if isinstance(value, date) else ""


def _display_date(value: date | None) -> str:
    if not isinstance(value, date):
        return ""
    return value.strftime("%d.%m.%y")


def _order_qty(order: Order) -> int:
    total = 0
    for item in order.items or []:
        value = _safe_int(getattr(item, "total", 0), 0)
        if value <= 0:
            value = sum(
                [
                    _safe_int(getattr(item, "qty_xs", 0)),
                    _safe_int(getattr(item, "qty_s", 0)),
                    _safe_int(getattr(item, "qty_m", 0)),
                    _safe_int(getattr(item, "qty_l", 0)),
                    _safe_int(getattr(item, "qty_xl", 0)),
                    _safe_int(getattr(item, "qty_2xl", 0)),
                    _safe_int(getattr(item, "qty_3xl", 0)),
                    _safe_int(getattr(item, "qty_4xl", 0)),
                ]
            )
        total += value
    return total


def _ensure_assign_state(order: Order) -> tuple[AssignOrderState, bool]:
    row = AssignOrderState.query.filter_by(order_id=int(order.id)).first()
    changed = False
    if row is None:
        row = AssignOrderState(
            order_id=int(order.id),
            date_received=order.enquiry_date or (order.created_at.date() if order.created_at else None),
            date_shipping=order.confirmed_on,
            order_name=str(order.production_order_id or order.order_id or "").strip(),
            qty=max(_order_qty(order), 0),
            status=ASSIGN_STATUS_OPTIONS[0],
            assigned_designer_name=None,
            order_category="OTHER",
            file_required="No",
            client_name=str(order.customer_name or "").strip(),
            source="system",
        )
        db.session.add(row)
        return row, True

    if not str(row.order_name or "").strip():
        row.order_name = str(order.production_order_id or order.order_id or "").strip()
        changed = True
    if not str(row.client_name or "").strip():
        row.client_name = str(order.customer_name or "").strip()
        changed = True
    if row.qty is None or int(row.qty or 0) <= 0:
        row.qty = max(_order_qty(order), 0)
        changed = True
    return row, changed


def _ensure_assign_rows_for_orders(orders: list[Order]) -> None:
    dirty = False
    for order in orders:
        _row, changed = _ensure_assign_state(order)
        dirty = dirty or changed
    if dirty:
        db.session.commit()


def _get_filtered_orders() -> tuple[list[dict], dict]:
    q = str(request.args.get("q", "") or "").strip()
    selected_statuses = [s for s in request.args.getlist("status") if s in ASSIGN_STATUS_OPTIONS]
    assignee = str(request.args.get("assignee", "") or "").strip()
    category = str(request.args.get("category", "") or "").strip()
    date_from = _parse_date_any(request.args.get("date_from"))
    date_to = _parse_date_any(request.args.get("date_to"))

    orders = (
        Order.query.filter(Order.production_order_id.isnot(None), Order.production_order_id != "")
        .order_by(Order.created_at.asc(), Order.id.asc())
        .all()
    )
    _ensure_assign_rows_for_orders(orders)

    rows: list[dict] = []
    for order in orders:
        state = AssignOrderState.query.filter_by(order_id=int(order.id)).first()
        if not state:
            continue

        if selected_statuses and state.status not in selected_statuses:
            continue
        if assignee and str(state.assigned_designer_name or "").strip() != assignee:
            continue
        if category and str(state.order_category or "").strip() != category:
            continue
        if date_from and (not state.date_received or state.date_received < date_from):
            continue
        if date_to and (not state.date_received or state.date_received > date_to):
            continue

        search_target = " ".join(
            [
                str(state.order_name or ""),
                str(state.client_name or ""),
                str(order.customer_name or ""),
                str(order.production_order_id or ""),
                str(order.order_id or ""),
            ]
        ).lower()
        if q and q.lower() not in search_target:
            continue

        latest_assign_image = _latest_assign_image_attachment(order.id)
        pending_days = 0
        if state.date_received:
            pending_days = max((date.today() - state.date_received).days, 0)
        delay_display = "-"
        if state.date_shipping and state.status != "Shipped" and state.date_shipping < date.today():
            overdue_days = (date.today() - state.date_shipping).days
            delay_display = f"Overdue by {overdue_days} day(s)"
        row_order_name = str(order.production_order_id or state.order_name or "").strip()
        row_client_name = str(order.customer_name or state.client_name or "").strip()
        rows.append(
            {
                "state_id": int(state.id),
                "order_id": int(order.id),
                "enquiry_id": str(order.order_id or "").strip(),
                "production_order_id": str(order.production_order_id or "").strip(),
                "date_received": state.date_received,
                "date_received_display": _display_date(state.date_received),
                "date_shipping": state.date_shipping,
                "date_shipping_display": _display_date(state.date_shipping),
                "order_name": row_order_name,
                "qty": int(state.qty or 0),
                "status": str(state.status or "").strip(),
                "assigned_designer_name": str(state.assigned_designer_name or "").strip(),
                "order_category": str(state.order_category or "").strip(),
                "file_required": _normalize_file_required(state.file_required),
                "client_name": row_client_name,
                "updated_at": state.updated_at,
                "pending_days": pending_days,
                "delay_display": delay_display,
                "has_image": bool(latest_assign_image),
            }
        )

    rows.sort(
        key=lambda row: (
            row["date_received"] or date.max,
            int(row["state_id"]),
        )
    )
    filters = {
        "q": q,
        "statuses": selected_statuses,
        "assignee": assignee,
        "category": category,
        "date_from": _iso_date(date_from),
        "date_to": _iso_date(date_to),
    }
    return rows, filters


def _summary_metrics(rows: list[dict]) -> dict:
    today = date.today()
    return {
        "total_orders": len(rows),
        "pending_file": sum(1 for r in rows if r.get("status") == "PP File Pending"),
        "ready_for_shipment": sum(1 for r in rows if r.get("status") == "Shipped"),
        "overdue": sum(
            1
            for r in rows
            if r.get("date_shipping") is not None
            and r.get("date_shipping") < today
            and r.get("status") != "Shipped"
        ),
    }


def _production_order_dropdown_options() -> list[dict]:
    orders = (
        Order.query.filter(Order.production_order_id.isnot(None), Order.production_order_id != "")
        .order_by(Order.created_at.asc(), Order.id.asc())
        .all()
    )
    seen: set[str] = set()
    options: list[dict] = []
    for order in orders:
        code = str(order.production_order_id or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        options.append(
            {
                "production_order_id": code,
                "customer_name": str(order.customer_name or "").strip(),
            }
        )
    return options


def _add_edit_prefill_lookup() -> dict[str, dict]:
    orders = (
        Order.query.filter(Order.production_order_id.isnot(None), Order.production_order_id != "")
        .order_by(Order.created_at.asc(), Order.id.asc())
        .all()
    )
    _ensure_assign_rows_for_orders(orders)

    lookup: dict[str, dict] = {}
    for order in orders:
        code = str(order.production_order_id or "").strip()
        if not code:
            continue
        state = AssignOrderState.query.filter_by(order_id=int(order.id)).first()
        if state is None:
            continue
        payload = {
            "date_received": _iso_date(state.date_received),
            "date_shipping": _iso_date(state.date_shipping),
            "qty": max(_safe_int(state.qty, 0), 0),
            "status": str(state.status or "").strip(),
            "assigned_designer_name": str(state.assigned_designer_name or "").strip(),
            "order_category": str(state.order_category or "").strip(),
            "file_required": _normalize_file_required(state.file_required),
            "client_name": str(order.customer_name or state.client_name or "").strip(),
            "updated_at": state.updated_at.isoformat() if state.updated_at else "",
        }
        existing = lookup.get(code)
        if not existing:
            lookup[code] = payload
            continue
        existing_ts = str(existing.get("updated_at") or "")
        payload_ts = str(payload.get("updated_at") or "")
        if payload_ts >= existing_ts:
            lookup[code] = payload
    return lookup


def _latest_prefill_for_production_order_id(production_order_id: str) -> dict | None:
    code = str(production_order_id or "").strip()
    if not code:
        return None
    orders = (
        Order.query.filter_by(production_order_id=code)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .all()
    )
    best_payload = None
    best_ts = ""
    for order in orders:
        state = AssignOrderState.query.filter_by(order_id=int(order.id)).first()
        if state is None:
            continue
        payload = {
            "date_received": _iso_date(state.date_received),
            "date_shipping": _iso_date(state.date_shipping),
            "qty": max(_safe_int(state.qty, 0), 0),
            "status": str(state.status or "").strip(),
            "assigned_designer_name": str(state.assigned_designer_name or "").strip(),
            "order_category": str(state.order_category or "").strip(),
            "file_required": _normalize_file_required(state.file_required),
            "client_name": str(order.customer_name or state.client_name or "").strip(),
            "updated_at": state.updated_at.isoformat() if state.updated_at else "",
        }
        payload_ts = str(payload.get("updated_at") or "")
        if not best_payload or payload_ts >= best_ts:
            best_payload = payload
            best_ts = payload_ts
    return best_payload


def _latest_production_plan_attachment(order_id: int) -> Attachment | None:
    return (
        Attachment.query.filter(
            Attachment.order_id == int(order_id),
            Attachment.mime_type == "application/pdf",
            Attachment.filename.ilike("production-plan-%"),
        )
        .order_by(Attachment.id.desc())
        .first()
    )


def _latest_assign_image_attachment(order_id: int) -> Attachment | None:
    return (
        Attachment.query.filter(
            Attachment.order_id == int(order_id),
            Attachment.mime_type.ilike("image/%"),
            Attachment.filename.ilike("assign-image-%"),
        )
        .order_by(Attachment.id.desc())
        .first()
    )


def _notification_subject(order: Order, status_value: str) -> str:
    order_code = str(order.production_order_id or order.order_id or order.id)
    if status_value == "PP File Pending":
        return f"Action Required: PP File Pending - {order_code}"
    return f"Action Required: Print File Working - {order_code}"


def _notification_body(order: Order, status_value: str) -> str:
    order_code = str(order.production_order_id or order.order_id or order.id)
    customer_name = str(order.customer_name or "Customer")
    if status_value == "PP File Pending":
        action_line = "Please start PP file preparation for this order."
    else:
        action_line = "Please start print file work for this order."
    return (
        f"Hello Team,\n\n"
        f"{action_line}\n\n"
        f"Order ID: {order_code}\n"
        f"Enquiry ID: {str(order.order_id or '').strip()}\n"
        f"Customer: {customer_name}\n\n"
        f"The latest production plan PDF is attached for reference.\n\n"
        f"Regards,\nIRA Order Sheet System"
    )


def _trigger_status_email_if_needed(
    state: AssignOrderState,
    old_status: str,
    new_status: str,
) -> AssignNotificationEvent | None:
    if old_status == new_status or new_status not in EMAIL_TRIGGER_STATUSES:
        return None

    recipient_map = {
        str(row.designer_name or "").strip(): str(row.designer_email or "").strip()
        for row in AssignDesignerContact.query.filter_by(is_active=True).all()
    }
    recipient_email = recipient_map.get(str(state.assigned_designer_name or "").strip(), "")
    order = Order.query.filter_by(id=int(state.order_id)).first()
    subject = _notification_subject(order, new_status) if order else f"Order Update - {new_status}"
    event = AssignNotificationEvent(
        assign_state_id=int(state.id),
        order_id=int(state.order_id),
        old_status=old_status,
        new_status=new_status,
        recipient_email=recipient_email or None,
        event_type=EMAIL_TRIGGER_STATUSES[new_status],
        subject=subject,
        delivery_result="failed",
        error_message=None,
        sent_at=None,
    )
    db.session.add(event)

    if order is None:
        event.error_message = "order not found"
        return event
    if not recipient_email:
        event.error_message = "designer email not configured"
        return event
    attachment = _latest_production_plan_attachment(order.id)
    if attachment is None:
        event.error_message = "latest production plan PDF not found"
        return event

    try:
        attachment_bytes = read_bytes(attachment.storage_path)
    except Exception as exc:  # pragma: no cover - storage backend exceptions
        event.error_message = f"attachment read failed: {exc}"
        return event

    ok, result = send_assign_notification_email(
        smtp_host=str(current_app.config.get("ASSIGN_MAIL_SMTP_HOST", "") or ""),
        smtp_port=int(current_app.config.get("ASSIGN_MAIL_SMTP_PORT", 587) or 587),
        smtp_username=str(current_app.config.get("ASSIGN_MAIL_USERNAME", "") or ""),
        smtp_password=str(current_app.config.get("ASSIGN_MAIL_PASSWORD", "") or ""),
        smtp_use_tls=bool(current_app.config.get("ASSIGN_MAIL_USE_TLS", True)),
        smtp_use_ssl=bool(current_app.config.get("ASSIGN_MAIL_USE_SSL", False)),
        from_email=str(current_app.config.get("ASSIGN_MAIL_FROM", "") or ""),
        cc_email=str(current_app.config.get("ASSIGN_MAIL_CC", "") or ""),
        reply_to=str(current_app.config.get("ASSIGN_MAIL_REPLY_TO", "") or ""),
        to_email=recipient_email,
        subject=subject,
        body=_notification_body(order, new_status),
        attachment_filename=str(attachment.filename or f"{order.production_order_id or order.order_id}.pdf"),
        attachment_bytes=attachment_bytes,
    )
    event.delivery_result = "sent" if ok else "failed"
    event.error_message = None if ok else result
    event.sent_at = datetime.utcnow() if ok else None
    return event


def _redirect_dashboard_with_query(raw_query: str) -> Response:
    query = str(raw_query or "").strip()
    if query:
        return redirect(f"{url_for('assign.assign_dashboard')}?{query}")
    return redirect(url_for("assign.assign_dashboard"))


@assign_bp.route("", methods=["GET"])
@login_required
@roles_required(Role.ADMIN.value, Role.MANAGER.value)
def assign_dashboard():
    rows, filters = _get_filtered_orders()
    summary = _summary_metrics(rows)
    active_designers = (
        AssignDesignerContact.query.filter_by(is_active=True)
        .order_by(AssignDesignerContact.designer_name.asc(), AssignDesignerContact.id.asc())
        .all()
    )
    all_designers = (
        AssignDesignerContact.query.order_by(AssignDesignerContact.designer_name.asc(), AssignDesignerContact.id.asc()).all()
    )
    selected_statuses = list(filters.get("statuses", []))
    total_qty = sum(_safe_int(row.get("qty", 0), 0) for row in rows) if len(selected_statuses) == 1 else None
    total_qty_status = selected_statuses[0] if len(selected_statuses) == 1 else None
    production_order_options = _production_order_dropdown_options()
    add_edit_prefill_lookup = _add_edit_prefill_lookup()
    return render_template(
        "assign/dashboard.html",
        rows=rows,
        summary=summary,
        statuses=ASSIGN_STATUS_OPTIONS,
        categories=ASSIGN_CATEGORY_OPTIONS,
        file_required_options=FILE_REQUIRED_OPTIONS,
        selected_statuses=selected_statuses,
        filters=filters,
        active_designers=active_designers,
        all_designers=all_designers,
        total_qty=total_qty,
        total_qty_status=total_qty_status,
        production_order_options=production_order_options,
        add_edit_prefill_lookup=add_edit_prefill_lookup,
        return_query=urlencode([(k, v) for k, v in request.args.items(multi=True)]),
    )


@assign_bp.route("/prefill", methods=["GET"])
@login_required
@roles_required(Role.ADMIN.value, Role.MANAGER.value)
def assign_prefill():
    production_order_id = str(request.args.get("order_name", "") or "").strip()
    payload = _latest_prefill_for_production_order_id(production_order_id)
    if payload is None:
        return jsonify({"ok": False, "message": "order not found"}), 404
    return jsonify({"ok": True, "order_name": production_order_id, "data": payload}), 200


@assign_bp.route("/save", methods=["POST"])
@login_required
@roles_required(Role.ADMIN.value, Role.MANAGER.value)
def assign_save():
    selected_ids = [int(v) for v in request.form.getlist("selected_row") if str(v).isdigit()]
    if not selected_ids:
        flash("Select at least one row to save.", "warning")
        return _redirect_dashboard_with_query(request.form.get("return_query", ""))

    updated = 0
    emails_sent = 0
    email_failed = 0
    for state_id in selected_ids:
        state = AssignOrderState.query.filter_by(id=int(state_id)).first()
        if state is None:
            continue

        old_status = str(state.status or "").strip()
        new_status = str(request.form.get(f"status_{state_id}", old_status) or old_status).strip()
        if new_status not in ASSIGN_STATUS_OPTIONS:
            new_status = old_status

        state.date_received = _parse_date_any(request.form.get(f"date_received_{state_id}")) or state.date_received
        state.date_shipping = _parse_date_any(request.form.get(f"date_shipping_{state_id}"))
        state.qty = max(_safe_int(request.form.get(f"qty_{state_id}"), state.qty or 0), 0)
        state.status = new_status
        state.assigned_designer_name = str(
            request.form.get(f"assigned_designer_name_{state_id}", state.assigned_designer_name or "") or ""
        ).strip() or None
        category = str(request.form.get(f"order_category_{state_id}", state.order_category or "") or "").strip().upper()
        state.order_category = category if category in ASSIGN_CATEGORY_OPTIONS else "OTHER"
        state.file_required = _normalize_file_required(request.form.get(f"file_required_{state_id}", state.file_required))
        state.client_name = str(request.form.get(f"client_name_{state_id}", state.client_name or "") or "").strip()
        state.source = "assign_ui"
        updated += 1

        event = _trigger_status_email_if_needed(state, old_status, new_status)
        if event is not None:
            if event.delivery_result == "sent":
                emails_sent += 1
            else:
                email_failed += 1

    db.session.commit()
    flash(f"Saved {updated} row(s).", "success")
    if emails_sent > 0:
        flash(f"Sent {emails_sent} notification email(s).", "success")
    if email_failed > 0:
        flash(f"{email_failed} notification(s) failed. Check designer email settings / SMTP / attachments.", "warning")
    if emails_sent == 0 and email_failed == 0:
        flash("No notification email required for the saved status changes.", "info")
    return _redirect_dashboard_with_query(request.form.get("return_query", ""))


@assign_bp.route("/add-edit", methods=["POST"])
@login_required
@roles_required(Role.ADMIN.value, Role.MANAGER.value)
def assign_add_edit():
    production_order_id = str(request.form.get("order_name", "") or "").strip()
    if not production_order_id:
        flash("Select an Order Name (Production Order ID).", "warning")
        return redirect(url_for("assign.assign_dashboard"))

    order = Order.query.filter_by(production_order_id=production_order_id).first()
    if order is None:
        flash("Selected production order was not found.", "danger")
        return redirect(url_for("assign.assign_dashboard"))

    state, _changed = _ensure_assign_state(order)
    old_status = str(state.status or "").strip()
    state.date_received = _parse_date_any(request.form.get("date_received")) or state.date_received
    state.date_shipping = _parse_date_any(request.form.get("date_shipping"))
    state.qty = max(_safe_int(request.form.get("qty"), state.qty or 0), 0)

    status_value = str(request.form.get("status", state.status) or state.status).strip()
    if status_value in ASSIGN_STATUS_OPTIONS:
        state.status = status_value
    state.assigned_designer_name = str(request.form.get("assigned_designer_name", "") or "").strip() or None

    category_value = str(request.form.get("order_category", state.order_category or "") or "").strip().upper()
    state.order_category = category_value if category_value in ASSIGN_CATEGORY_OPTIONS else "OTHER"
    state.file_required = _normalize_file_required(request.form.get("file_required", state.file_required))
    state.client_name = str(order.customer_name or state.client_name or "").strip()
    state.order_name = str(order.production_order_id or state.order_name or "").strip()
    state.source = "assign_add_edit"

    image_file = request.files.get("order_image")
    if image_file and str(image_file.filename or "").strip():
        safe_source = secure_filename(str(image_file.filename))
        _base, dot, ext = safe_source.rpartition(".")
        clean_ext = ext.lower() if dot else "png"
        if clean_ext not in {"png", "jpg", "jpeg", "webp", "gif"}:
            clean_ext = "png"
        image_bytes = image_file.read()
        if image_bytes:
            existing_images = Attachment.query.filter(
                Attachment.order_id == int(order.id),
                Attachment.mime_type.ilike("image/%"),
                Attachment.filename.ilike("assign-image-%"),
            ).all()
            for existing in existing_images:
                try:
                    delete(str(existing.storage_path or ""))
                except Exception:
                    pass
                db.session.delete(existing)

            filename = f"assign-image-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.{clean_ext}"
            storage_path = save_order_file(
                int(order.id),
                ORDER_IMAGE_SECTION,
                filename,
                image_bytes,
                content_type=image_file.mimetype or f"image/{clean_ext}",
            )
            db.session.add(
                Attachment(
                    order_id=int(order.id),
                    filename=filename,
                    mime_type=image_file.mimetype or f"image/{clean_ext}",
                    storage_path=storage_path,
                )
            )

    event = _trigger_status_email_if_needed(state, old_status, str(state.status or "").strip())
    db.session.commit()
    flash(f"Saved order: {production_order_id}.", "success")
    if event is not None:
        if event.delivery_result == "sent":
            flash("Notification email sent.", "success")
        else:
            flash(f"Notification failed: {event.error_message or 'unknown error'}", "warning")
    else:
        flash("No notification email required for this status.", "info")
    return redirect(url_for("assign.assign_dashboard"))


@assign_bp.route("/email-settings", methods=["POST"])
@login_required
@roles_required(Role.ADMIN.value, Role.MANAGER.value)
def assign_email_settings():
    names = request.form.getlist("designer_name[]")
    emails = request.form.getlist("designer_email[]")
    active_tokens = set(request.form.getlist("designer_active"))

    AssignDesignerContact.query.delete()
    inserted = 0
    seen = set()
    for idx, raw_name in enumerate(names):
        name = str(raw_name or "").strip()
        email = str((emails[idx] if idx < len(emails) else "") or "").strip()
        if not name or not email:
            continue
        lowered = name.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        row = AssignDesignerContact(
            designer_name=name,
            designer_email=email,
            is_active=(str(idx) in active_tokens),
        )
        db.session.add(row)
        inserted += 1

    db.session.commit()
    flash(f"Saved {inserted} designer contact(s).", "success")
    return _redirect_dashboard_with_query(request.form.get("return_query", ""))



@assign_bp.route("/<int:order_id>/image/<slot>", methods=["GET"])
@login_required
@roles_required(Role.ADMIN.value, Role.MANAGER.value)
def assign_order_image(order_id: int, slot: str):
    # Legacy endpoint kept for compatibility; assign module now supports one image.
    return assign_order_single_image(order_id)


@assign_bp.route("/<int:order_id>/image", methods=["GET"])
@login_required
@roles_required(Role.ADMIN.value, Role.MANAGER.value)
def assign_order_single_image(order_id: int):
    order = Order.query.filter_by(id=int(order_id)).first_or_404()
    attachment = _latest_assign_image_attachment(order.id)
    if attachment is None:
        abort(404)

    try:
        blob = read_bytes(str(attachment.storage_path or ""))
    except Exception:
        abort(404)

    guessed, _enc = mimetypes.guess_type(str(attachment.filename or ""))
    return send_file(
        io.BytesIO(blob),
        mimetype=guessed or str(attachment.mime_type or "image/png"),
        as_attachment=False,
        download_name=str(attachment.filename or f"{order.production_order_id or order.order_id}-assign-image.png"),
    )

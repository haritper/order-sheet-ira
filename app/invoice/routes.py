from __future__ import annotations

from datetime import datetime
from io import BytesIO

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_user

from app.auth.forms import LoginForm
from app.auth.routes import _find_user_by_login_username
from app.exports.services import build_invoice_number, render_invoice_pdf, save_plan_pdf
from app.extensions import db
from app.order_numbers import consume_invoice_number
from app.invoice.services import (
    extract_receipt_payment_details,
    get_invoice_receipt_attachment,
    get_pricing_total_amount,
    load_invoice_state,
    merge_payment_fields,
    reconcile_invoice_payment,
    save_invoice_state,
)
from app.models import Attachment, Order, Role
from app.utils import add_audit


invoice_bp = Blueprint("invoice", __name__)


def _is_strict_admin(user) -> bool:
    if user is None:
        return False
    role = str(getattr(user, "role", "") or "").strip().lower()
    return role == Role.ADMIN.value


def _invoice_admin_guard():
    if not current_user.is_authenticated:
        return redirect(url_for("invoice.invoice_login", next=request.path))
    if not _is_strict_admin(current_user):
        abort(403)
    return None


def _invoice_attachment_state(order_ids: list[int]) -> tuple[dict[int, Attachment], dict[int, int]]:
    if not order_ids:
        return {}, {}
    attachments = (
        Attachment.query.filter(
            Attachment.order_id.in_(order_ids),
            Attachment.mime_type == "application/pdf",
            Attachment.filename.ilike("invoice-%"),
        )
        .order_by(Attachment.order_id.asc(), Attachment.created_at.desc(), Attachment.id.desc())
        .all()
    )
    latest_map: dict[int, Attachment] = {}
    count_map: dict[int, int] = {}
    for attachment in attachments:
        oid = int(attachment.order_id)
        count_map[oid] = int(count_map.get(oid, 0)) + 1
        if oid not in latest_map:
            latest_map[oid] = attachment
    return latest_map, count_map


@invoice_bp.route("/invoice", methods=["GET", "POST"])
def invoice_login():
    if current_user.is_authenticated and _is_strict_admin(current_user):
        return redirect(url_for("invoice.invoice_dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        user = _find_user_by_login_username(form.username.data)
        if user and user.check_password(form.password.data):
            if _is_strict_admin(user):
                login_user(user)
                next_page = str(request.args.get("next", "") or "").strip()
                if next_page.startswith("/invoice") and next_page != "/invoice/preview":
                    return redirect(next_page)
                return redirect(url_for("invoice.invoice_dashboard"))
            flash("Only admin can access invoice module.", "danger")
        else:
            flash("Invalid username or password", "danger")

    return render_template("invoice/login.html", form=form)


@invoice_bp.route("/invoice/dashboard", methods=["GET"])
def invoice_dashboard():
    guard = _invoice_admin_guard()
    if guard is not None:
        return guard

    selected_order_id = request.args.get("order", type=int)
    recent_orders = Order.query.order_by(Order.id.desc()).limit(200).all()
    receipt_map: dict[int, Attachment | None] = {}
    for row in recent_orders:
        receipt_map[int(row.id)] = get_invoice_receipt_attachment(row)
    orders = [row for row in recent_orders if receipt_map.get(int(row.id)) is not None]
    order_ids = [int(row.id) for row in orders]
    latest_invoice_map, invoice_count_map = _invoice_attachment_state(order_ids)
    order_rows = [
        {
            "order": row,
            "receipt_attachment": receipt_map[int(row.id)],
            "latest_invoice_attachment": latest_invoice_map.get(int(row.id)),
            "invoice_version_count": int(invoice_count_map.get(int(row.id), 0)),
        }
        for row in orders
    ]

    order = None
    if selected_order_id:
        order = next((row for row in orders if int(row.id) == int(selected_order_id)), None)
        if order is None:
            selected = Order.query.filter_by(id=selected_order_id).first()
            if selected is not None:
                selected_receipt = get_invoice_receipt_attachment(selected)
                if selected_receipt is not None:
                    order = selected
                    receipt_map[int(order.id)] = selected_receipt
    if order is None and orders:
        order = orders[0]

    payment_values = {}
    extracted_values = {}
    receipt_attachment = receipt_map.get(int(order.id)) if order is not None else None

    if order is not None:
        saved_state = load_invoice_state(order)
        extracted_values = {}
        pricing_total_amount = get_pricing_total_amount(order)
        if receipt_attachment is not None:
            saved_receipt_id = str(saved_state.get("receipt_attachment_id", "") or "").strip()
            current_receipt_id = str(receipt_attachment.id)
            has_core_saved = bool(saved_state.get("transaction_id")) and bool(saved_state.get("amount_paid"))
            if saved_receipt_id != current_receipt_id or not has_core_saved:
                extracted_values = extract_receipt_payment_details(receipt_attachment)
            else:
                extracted_values = merge_payment_fields(saved_state, {}, {})
        payment_values = merge_payment_fields(extracted_values, saved_state, {})
        payment_values.update(reconcile_invoice_payment(pricing_total_amount, payment_values.get("amount_paid", "")))
        if not payment_values.get("invoice_number"):
            payment_values["invoice_number"] = build_invoice_number(order)

    return render_template(
        "invoice/dashboard.html",
        order_rows=order_rows,
        order=order,
        receipt_attachment=receipt_attachment,
        extracted_values=extracted_values,
        payment_values=payment_values,
        latest_invoice_attachment=(latest_invoice_map.get(int(order.id)) if order is not None else None),
        invoice_version_count=(invoice_count_map.get(int(order.id), 0) if order is not None else 0),
    )


@invoice_bp.route("/invoice/<int:order_id>/download", methods=["POST"])
def download_invoice_pdf(order_id: int):
    guard = _invoice_admin_guard()
    if guard is not None:
        return guard

    order = Order.query.get_or_404(order_id)
    receipt_attachment = get_invoice_receipt_attachment(order)
    if receipt_attachment is None:
        flash("Upload invoice receipt in checklist before generating invoice.", "danger")
        return redirect(url_for("invoice.invoice_dashboard", order=order.id))
    pricing_total_amount = get_pricing_total_amount(order)
    if not pricing_total_amount:
        flash("Pricing total is missing for this order. Sync this order in Pricing Intelligence first.", "danger")
        return redirect(url_for("invoice.invoice_dashboard", order=order.id))

    saved_state = load_invoice_state(order)
    extracted_values = extract_receipt_payment_details(receipt_attachment)
    saved_receipt_id = str(saved_state.get("receipt_attachment_id", "") or "").strip()
    current_receipt_id = str(receipt_attachment.id)
    state_for_merge = saved_state if saved_receipt_id == current_receipt_id else {}
    manual_values = {
        "transaction_id": request.form.get("transaction_id", ""),
        "amount_paid": request.form.get("amount_paid", ""),
        "payment_mode": request.form.get("payment_mode", ""),
        "paid_on": request.form.get("paid_on", ""),
    }
    merged = merge_payment_fields(extracted_values, state_for_merge, manual_values)
    merged.update(reconcile_invoice_payment(pricing_total_amount, merged.get("amount_paid", "")))
    supplied_invoice_number = str(request.form.get("invoice_number", "") or "").strip()
    invoice_order_date = order.enquiry_date
    if invoice_order_date is None and isinstance(order.created_at, datetime):
        invoice_order_date = order.created_at.date()
    issued_invoice_number = consume_invoice_number(order_date=invoice_order_date)
    merged["invoice_number"] = supplied_invoice_number or issued_invoice_number

    if not merged.get("transaction_id"):
        flash("Transaction ID is required to generate invoice PDF.", "danger")
        return redirect(url_for("invoice.invoice_dashboard", order=order.id))
    if not merged.get("amount_paid"):
        flash("Amount paid is required to generate invoice PDF.", "danger")
        return redirect(url_for("invoice.invoice_dashboard", order=order.id))

    try:
        pdf_bytes = render_invoice_pdf(order, merged)
        display_order_id = str(order.production_order_id or order.order_id or "").strip()
        attachment = save_plan_pdf(
            order,
            pdf_bytes,
            plan_slug="invoice",
            display_order_id=display_order_id,
        )
        db.session.add(attachment)
        add_audit(order.id, current_user.id, "EXPORT_INVOICE_PDF", "filename", None, attachment.filename)
        db.session.commit()
    except Exception as exc:  # pragma: no cover
        db.session.rollback()
        current_app.logger.exception("Invoice export failed for order %s: %s", order.id, exc)
        flash("Failed to generate invoice PDF. Please check logs.", "danger")
        return redirect(url_for("invoice.invoice_dashboard", order=order.id))

    try:
        save_invoice_state(order, merged, receipt_attachment_id=receipt_attachment.id)
    except Exception as exc:  # pragma: no cover
        current_app.logger.exception("Failed to persist invoice state for order %s: %s", order.id, exc)

    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=attachment.filename,
    )

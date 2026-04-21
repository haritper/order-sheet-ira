from __future__ import annotations

from datetime import datetime, time, timedelta

from flask import Blueprint, flash, redirect, render_template, url_for
from flask import request

from app.extensions import db
from app.models import WorkTimingEntry
from app.work_timing.services import get_allowed_status_choices, update_work_timing_status


work_timing_bp = Blueprint("work_timing", __name__)


@work_timing_bp.route("/update")
def update_page():
    entries = (
        WorkTimingEntry.query.filter(WorkTimingEntry.status != "DELIVERED")
        .order_by(
            WorkTimingEntry.updated_at.desc(),
            WorkTimingEntry.id.desc(),
        )
        .all()
    )
    status_choices_by_entry_id = {
        int(entry.id): get_allowed_status_choices(str(entry.status or ""))
        for entry in entries
    }
    return render_template(
        "work_timing/update.html",
        entries=entries,
        status_choices_by_entry_id=status_choices_by_entry_id,
    )


@work_timing_bp.route("/update/delivered")
def delivered_page():
    period = str(request.args.get("period", "") or "").strip().lower()
    date_from_raw = str(request.args.get("date_from", "") or "").strip()
    date_to_raw = str(request.args.get("date_to", "") or "").strip()

    query = WorkTimingEntry.query.filter(WorkTimingEntry.status == "DELIVERED")
    now = datetime.utcnow()

    if period == "last_week":
        query = query.filter(WorkTimingEntry.updated_at >= now - timedelta(days=7))
    elif period == "last_month":
        query = query.filter(WorkTimingEntry.updated_at >= now - timedelta(days=30))
    elif period == "custom":
        parsed_from = None
        parsed_to = None
        if date_from_raw:
            try:
                parsed_from = datetime.strptime(date_from_raw, "%Y-%m-%d").date()
            except ValueError:
                flash("Invalid custom start date.", "warning")
        if date_to_raw:
            try:
                parsed_to = datetime.strptime(date_to_raw, "%Y-%m-%d").date()
            except ValueError:
                flash("Invalid custom end date.", "warning")

        if parsed_from:
            query = query.filter(WorkTimingEntry.updated_at >= datetime.combine(parsed_from, time.min))
        if parsed_to:
            query = query.filter(WorkTimingEntry.updated_at <= datetime.combine(parsed_to, time.max))

    delivered_entries = query.order_by(
        WorkTimingEntry.updated_at.desc(),
        WorkTimingEntry.id.desc(),
    ).all()

    return render_template(
        "work_timing/delivered.html",
        entries=delivered_entries,
        selected_period=period,
        date_from=date_from_raw,
        date_to=date_to_raw,
    )


@work_timing_bp.route("/update/<int:entry_id>/status", methods=["POST"])
def update_status(entry_id: int):
    entry = WorkTimingEntry.query.get_or_404(entry_id)
    next_status = str(request.form.get("status", "") or "").strip()
    try:
        changed = update_work_timing_status(entry, next_status)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("work_timing.update_page"))

    if not changed:
        flash("Status is already set to that value.", "info")
        return redirect(url_for("work_timing.update_page"))

    db.session.commit()
    if next_status == "DELIVERED":
        flash("Order moved to Delivered module.", "success")
        return redirect(url_for("work_timing.delivered_page"))
    flash("Work timing status updated.", "success")
    return redirect(url_for("work_timing.update_page"))


@work_timing_bp.route("/update/bulk-status", methods=["POST"])
def bulk_update_status():
    single_update_id_raw = str(request.form.get("single_update_id", "") or "").strip()
    target_ids: list[int] = []
    if single_update_id_raw:
        try:
            target_ids = [int(single_update_id_raw)]
        except ValueError:
            target_ids = []
    else:
        bulk_confirm = str(request.form.get("bulk_confirm", "") or "").strip().lower() in {"1", "true", "on", "yes"}
        if not bulk_confirm:
            flash("Tick the checkbox near Save Selected to confirm bulk update.", "warning")
            return redirect(url_for("work_timing.update_page"))

        # Scan all row status inputs and only apply changed rows.
        for key in request.form.keys():
            if not key.startswith("status_"):
                continue
            raw_id = key.replace("status_", "", 1).strip()
            try:
                entry_id = int(raw_id)
            except ValueError:
                continue
            next_status = str(request.form.get(f"status_{entry_id}", "") or "").strip()
            current_status = str(request.form.get(f"current_status_{entry_id}", "") or "").strip()
            if next_status and current_status and next_status != current_status:
                target_ids.append(entry_id)

    if not target_ids:
        flash("No changed statuses found for bulk update.", "warning")
        return redirect(url_for("work_timing.update_page"))

    updated_count = 0
    delivered_count = 0
    errors: list[str] = []

    for entry_id in target_ids:
        entry = WorkTimingEntry.query.filter_by(id=int(entry_id)).first()
        if entry is None:
            errors.append(f"Order row {entry_id} not found.")
            continue
        next_status = str(request.form.get(f"status_{entry_id}", "") or "").strip()
        try:
            changed = update_work_timing_status(entry, next_status)
        except ValueError as exc:
            errors.append(f"{entry.order_code}: {exc}")
            continue

        if changed:
            updated_count += 1
            if next_status == "DELIVERED":
                delivered_count += 1

    if updated_count > 0:
        db.session.commit()
        flash(f"Updated {updated_count} order status value(s).", "success")
    else:
        db.session.rollback()
        flash("No status updates were applied.", "warning")

    for message in errors[:5]:
        flash(message, "danger")

    if single_update_id_raw and delivered_count > 0:
        return redirect(url_for("work_timing.delivered_page"))

    return redirect(url_for("work_timing.update_page"))

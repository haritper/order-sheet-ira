from io import BytesIO

from flask import Blueprint, abort, current_app, send_file
from flask_login import current_user, login_required

from app.extensions import db
from app.exports.services import render_order_pdf, save_order_pdf
from app.models import Attachment, Order
from app.orders.access import can_user_access_order
from app.storage import read_bytes
from app.utils import add_audit


exports_bp = Blueprint("exports", __name__, url_prefix="/orders")


@exports_bp.route("/<int:order_id>/export/pdf")
@login_required
def export_pdf(order_id):
    order = Order.query.get_or_404(order_id)
    if not can_user_access_order(current_user, order):
        abort(403)
    pdf_bytes = render_order_pdf(order)
    attachment = None
    try:
        attachment = save_order_pdf(order, pdf_bytes)
        db.session.add(attachment)
        add_audit(order.id, current_user.id, "EXPORT_PDF", "filename", None, attachment.filename)
        db.session.commit()
    except Exception as exc:  # pragma: no cover
        db.session.rollback()
        current_app.logger.exception("Failed to persist exported PDF for order %s: %s", order.id, exc)

    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=(attachment.filename if attachment else f"{order.order_id}.pdf"),
    )


@exports_bp.route("/<int:order_id>/exports/<int:attachment_id>")
@login_required
def get_export(order_id, attachment_id):
    order = Order.query.get_or_404(order_id)
    if not can_user_access_order(current_user, order):
        abort(403)
    attachment = Attachment.query.filter_by(id=attachment_id, order_id=order_id).first()
    if not attachment:
        abort(404)
    try:
        data = read_bytes(attachment.storage_path)
        return send_file(
            BytesIO(data),
            mimetype=attachment.mime_type or "application/octet-stream",
            as_attachment=True,
            download_name=attachment.filename,
        )
    except Exception as exc:  # pragma: no cover
        current_app.logger.exception("Failed to stream attachment %s: %s", attachment.id, exc)
        abort(500)

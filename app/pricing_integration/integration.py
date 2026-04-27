from __future__ import annotations

from pathlib import Path

from flask import current_app

from . import db
from .orders import ingest_production_plan_pdf


def ingest_production_plan_for_order(*, order_id: int, order_number: str, pdf_bytes: bytes) -> int:
    external_order_id = f"order-sheet:{order_id}"
    user_id = resolve_ingestion_user_id()
    upload_folder = Path(current_app.config["PRICING_UPLOAD_FOLDER"])
    filename = f"{order_number or order_id}-production-plan.pdf"
    return ingest_production_plan_pdf(
        external_order_id=external_order_id,
        pdf_bytes=pdf_bytes,
        upload_folder=upload_folder,
        user_id=user_id,
        filename=filename,
    )


def resolve_ingestion_user_id() -> int:
    employee = db.execute(
        "SELECT id FROM users WHERE username = 'manager' LIMIT 1"
    ).fetchone()
    if employee:
        return int(employee["id"])

    owner = db.execute(
        "SELECT id FROM users WHERE username = 'admin' LIMIT 1"
    ).fetchone()
    if owner:
        return int(owner["id"])

    fallback = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    if not fallback:
        raise RuntimeError("No pricing users available for auto-ingestion")
    return int(fallback["id"])

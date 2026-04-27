from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
from io import BytesIO
from pathlib import Path
from typing import Any

from flask import current_app
from openai import OpenAI
from pypdf import PdfReader

from app.models import Attachment
from app.orders.checklist_cutting import load_checklist_state
from app.storage import ORDER_META_SECTION, exists, read_bytes, save_order_text

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None


PAYMENT_FIELDS = ("transaction_id", "amount_paid", "payment_mode", "paid_on")


def invoice_state_path(order_id: int) -> str:
    return f"order://{int(order_id)}/{ORDER_META_SECTION}/invoice_state.json"


def load_invoice_state(order) -> dict[str, str]:
    default_state = _empty_payment_details()
    default_state["receipt_attachment_id"] = ""
    default_state["total_amount"] = ""
    default_state["payment_received"] = ""
    default_state["balance"] = ""
    default_state["payment_status"] = ""

    path = invoice_state_path(order.id)
    if not exists(path):
        return default_state
    try:
        raw = json.loads(read_bytes(path).decode("utf-8"))
    except (ValueError, TypeError, json.JSONDecodeError):
        return default_state
    if not isinstance(raw, dict):
        return default_state

    merged = dict(default_state)
    merged.update(
        {
            "transaction_id": str(raw.get("transaction_id", "") or "").strip(),
            "amount_paid": _normalize_amount(raw.get("amount_paid", "")),
            "payment_mode": str(raw.get("payment_mode", "") or "").strip().upper(),
            "paid_on": str(raw.get("paid_on", "") or "").strip(),
            "receipt_attachment_id": str(raw.get("receipt_attachment_id", "") or "").strip(),
            "total_amount": _normalize_amount(raw.get("total_amount", "")),
            "payment_received": _normalize_amount(raw.get("payment_received", "")),
            "balance": _normalize_amount(raw.get("balance", "")),
            "payment_status": str(raw.get("payment_status", "") or "").strip().upper(),
        }
    )
    return merged


def save_invoice_state(order, payment_details: dict[str, Any], *, receipt_attachment_id: int | None = None) -> None:
    normalized = merge_payment_fields({}, {}, payment_details)
    payload = {
        "transaction_id": normalized.get("transaction_id", ""),
        "amount_paid": normalized.get("amount_paid", ""),
        "payment_mode": normalized.get("payment_mode", ""),
        "paid_on": normalized.get("paid_on", ""),
        "total_amount": _normalize_amount(payment_details.get("total_amount", "")),
        "payment_received": _normalize_amount(payment_details.get("payment_received", "")),
        "balance": _normalize_amount(payment_details.get("balance", "")),
        "payment_status": str(payment_details.get("payment_status", "") or "").strip().upper(),
        "receipt_attachment_id": int(receipt_attachment_id) if receipt_attachment_id else None,
    }
    save_order_text(
        order.id,
        ORDER_META_SECTION,
        "invoice_state.json",
        json.dumps(payload, ensure_ascii=True, indent=2),
    )


def merge_payment_fields(
    extracted: dict[str, Any] | None,
    saved_state: dict[str, Any] | None,
    manual: dict[str, Any] | None,
) -> dict[str, str]:
    merged = _empty_payment_details()
    for source in (extracted, saved_state, manual):
        if not isinstance(source, dict):
            continue
        for field in PAYMENT_FIELDS:
            raw = str(source.get(field, "") or "").strip()
            if not raw:
                continue
            if field == "amount_paid":
                normalized = _normalize_amount(raw)
                merged[field] = normalized if normalized else raw
            elif field == "payment_mode":
                merged[field] = raw.upper()
            else:
                merged[field] = raw
    return merged


def get_pricing_total_amount(order) -> str:
    external_order_id = f"order-sheet:{int(order.id)}"
    db_path = Path(str(current_app.config.get("PRICING_DATABASE", "") or "")).resolve()
    if not db_path.exists():
        return ""

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            """
            SELECT quoted_subtotal
            FROM orders
            WHERE external_order_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (external_order_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return ""
    return _normalize_amount(row[0])


def reconcile_invoice_payment(total_amount: Any, amount_paid: Any) -> dict[str, str]:
    normalized_total = _normalize_amount(total_amount)
    normalized_paid = _normalize_amount(amount_paid) or "0.00"
    if not normalized_total:
        return {
            "total_amount": "",
            "payment_received": normalized_paid,
            "balance": "",
            "payment_status": "",
        }

    total_value = float(normalized_total)
    paid_value = float(normalized_paid)
    balance_value = max(total_value - paid_value, 0.0)
    status = "FULLY PAID" if paid_value >= total_value else "PARTIALLY PAID"
    return {
        "total_amount": normalized_total,
        "payment_received": f"{paid_value:.2f}",
        "balance": f"{balance_value:.2f}",
        "payment_status": status,
    }


def get_invoice_receipt_attachment(order) -> Attachment | None:
    state = load_checklist_state(order)
    flow = state.get("flow", {}) if isinstance(state, dict) else {}
    if not isinstance(flow, dict):
        return None
    raw_id = flow.get("invoice_receipt_attachment_id")
    try:
        attachment_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    return Attachment.query.filter_by(id=attachment_id, order_id=int(order.id)).first()


def extract_receipt_payment_details(attachment: Attachment | None) -> dict[str, str]:
    details = _empty_payment_details()
    if attachment is None:
        return details

    try:
        payload = read_bytes(str(attachment.storage_path))
    except Exception:  # pragma: no cover
        return details

    text_blob = _extract_pdf_text(payload) if _is_pdf_receipt(attachment) else ""
    text_details = extract_payment_details_from_text(text_blob) if text_blob else _empty_payment_details()
    details = merge_payment_fields(details, text_details, {})

    needs_vision = any(not details.get(field, "") for field in PAYMENT_FIELDS)
    if needs_vision:
        vision_details = _extract_with_openai_vision(attachment, payload)
        details = merge_payment_fields(details, vision_details, {})

    return details


def extract_payment_details_from_text(text: str) -> dict[str, str]:
    details = _empty_payment_details()
    if not text:
        return details

    compact = re.sub(r"\s+", " ", str(text)).strip()
    if not compact:
        return details

    transaction_patterns = [
        r"(?:transaction|txn|utr|reference)\s*(?:id|no\.?|number)?\s*[:#-]?\s*([A-Za-z0-9-]{5,})",
    ]
    amount_patterns = [
        r"payment\s*received(?:\s*\(\s*\$\s*\))?\s*[:#-]?\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
        r"amount\s*paid(?:\s*\(\s*\$\s*\))?\s*[:#-]?\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
        r"amount(?:\s*\(\s*\$\s*\))?\s*[:#-]?\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
    ]
    mode_patterns = [
        r"payment\s*mode\s*[:#-]?\s*([A-Za-z][A-Za-z0-9 /-]{1,40}?)(?=\s+(?:paid\s+on|transaction|shipping|amount|balance|$))",
    ]
    paid_on_patterns = [
        r"paid\s*on\s*[:#-]?\s*([0-9]{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+[0-9]{4})",
        r"paid\s*on\s*[:#-]?\s*([A-Za-z]+\s+[0-9]{1,2}(?:st|nd|rd|th)?[,]?\s+[0-9]{4})",
        r"paid\s*on\s*[:#-]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",
    ]

    details["transaction_id"] = _first_match(transaction_patterns, compact)
    details["amount_paid"] = _normalize_amount(_first_match(amount_patterns, compact))
    details["payment_mode"] = _first_match(mode_patterns, compact).upper()
    details["paid_on"] = _first_match(paid_on_patterns, compact)
    return details


def _extract_pdf_text(payload: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(payload))
    except Exception:
        return ""

    chunks: list[str] = []
    for page in reader.pages:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(chunks).strip()


def _extract_with_openai_vision(attachment: Attachment, payload: bytes) -> dict[str, str]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return _empty_payment_details()

    data_urls = _build_receipt_data_urls(attachment, payload)
    if not data_urls:
        return _empty_payment_details()

    prompt = (
        "Extract payment details from this transaction receipt image. "
        "Return JSON with keys: transaction_id, amount_paid, payment_mode, paid_on. "
        "Use empty string for missing fields. amount_paid must be numeric with two decimals and no currency symbol."
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for data_url in data_urls:
        content.append({"type": "image_url", "image_url": {"url": data_url}})

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=400,
        )
        raw = response.choices[0].message.content or "{}"
        parsed = _json_object_or_empty(raw)
    except Exception:  # pragma: no cover
        return _empty_payment_details()

    if not isinstance(parsed, dict):
        return _empty_payment_details()

    return {
        "transaction_id": str(parsed.get("transaction_id", "") or "").strip(),
        "amount_paid": _normalize_amount(parsed.get("amount_paid", "")),
        "payment_mode": str(parsed.get("payment_mode", "") or "").strip().upper(),
        "paid_on": str(parsed.get("paid_on", "") or "").strip(),
    }


def _build_receipt_data_urls(attachment: Attachment, payload: bytes) -> list[str]:
    if _is_pdf_receipt(attachment):
        if fitz is None:
            return []
        data_urls: list[str] = []
        try:
            doc = fitz.open(stream=payload, filetype="pdf")
            try:
                pages = min(int(doc.page_count), 2)
                for idx in range(pages):
                    pix = doc[idx].get_pixmap(matrix=fitz.Matrix(2, 2))
                    encoded = base64.b64encode(pix.tobytes("png")).decode("ascii")
                    data_urls.append(f"data:image/png;base64,{encoded}")
            finally:
                doc.close()
        except Exception:  # pragma: no cover
            return []
        return data_urls

    mime = _guess_image_mime(attachment)
    encoded = base64.b64encode(payload).decode("ascii")
    return [f"data:{mime};base64,{encoded}"]


def _guess_image_mime(attachment: Attachment) -> str:
    raw = str(getattr(attachment, "mime_type", "") or "").strip().lower()
    if raw.startswith("image/"):
        return raw
    name = str(getattr(attachment, "filename", "") or "").strip().lower()
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".jpg") or name.endswith(".jpeg"):
        return "image/jpeg"
    if name.endswith(".webp"):
        return "image/webp"
    return "image/png"


def _is_pdf_receipt(attachment: Attachment) -> bool:
    mime = str(getattr(attachment, "mime_type", "") or "").strip().lower()
    name = str(getattr(attachment, "filename", "") or "").strip().lower()
    return mime == "application/pdf" or name.endswith(".pdf")


def _first_match(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return str(match.group(1) or "").strip()
    return ""


def _normalize_amount(raw: Any) -> str:
    token = str(raw or "").strip()
    token = token.replace("$", "").replace(",", "")
    if not token:
        return ""
    try:
        value = float(token)
    except (TypeError, ValueError):
        return ""
    return f"{value:.2f}"


def _json_object_or_empty(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not fenced:
        return {}
    try:
        parsed = json.loads(fenced.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _empty_payment_details() -> dict[str, str]:
    return {
        "transaction_id": "",
        "amount_paid": "",
        "payment_mode": "",
        "paid_on": "",
    }

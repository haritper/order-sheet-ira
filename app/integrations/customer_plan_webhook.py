from __future__ import annotations

from datetime import datetime

import requests
from flask import current_app


def send_customer_plan_webhook(
    *,
    order_id: int,
    enquiry_id: str,
    customer_name: str,
    customer_mobile: str,
    attachment_filename: str,
    pdf_bytes: bytes,
) -> dict:
    enabled = bool(current_app.config.get("CUSTOMER_PLAN_WEBHOOK_ENABLED", True))
    webhook_url = str(current_app.config.get("CUSTOMER_PLAN_WEBHOOK_URL", "") or "").strip()
    webhook_token = str(current_app.config.get("CUSTOMER_PLAN_WEBHOOK_TOKEN", "") or "").strip()
    timeout_seconds = float(current_app.config.get("CUSTOMER_PLAN_WEBHOOK_TIMEOUT_SECONDS", 15) or 15)

    if not enabled:
        return {
            "success": False,
            "status_code": None,
            "message": "Customer plan webhook is disabled.",
            "error": "disabled",
        }
    if not webhook_url:
        return {
            "success": False,
            "status_code": None,
            "message": "Customer plan webhook URL is not configured.",
            "error": "missing_url",
        }
    if not webhook_token:
        return {
            "success": False,
            "status_code": None,
            "message": "Customer plan webhook token is not configured.",
            "error": "missing_token",
        }

    payload = {
        "order_id": str(order_id),
        "enquiry_id": str(enquiry_id or "").strip(),
        "customer_name": str(customer_name or "").strip(),
        "customer_mobile": str(customer_mobile or "").strip(),
        "attachment_filename": str(attachment_filename or "").strip(),
        "generated_at": datetime.utcnow().isoformat(),
    }
    headers = {"X-Webhook-Token": webhook_token}
    files = {"file": (attachment_filename, pdf_bytes, "application/pdf")}

    try:
        resp = requests.post(
            webhook_url,
            data=payload,
            files=files,
            headers=headers,
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        return {
            "success": False,
            "status_code": None,
            "message": f"Webhook request failed: {exc}",
            "error": exc.__class__.__name__,
        }

    ok = 200 <= int(resp.status_code) < 300
    if ok:
        return {
            "success": True,
            "status_code": int(resp.status_code),
            "message": "Customer plan webhook triggered.",
            "error": None,
        }
    return {
        "success": False,
        "status_code": int(resp.status_code),
        "message": f"Webhook returned HTTP {int(resp.status_code)}.",
        "error": "http_error",
    }


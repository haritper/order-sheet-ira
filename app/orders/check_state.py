from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict

from app.extensions import db
from app.models import Order, OrderCheck


def _loads(value: str | None, default: Any):
    if not value:
        return default
    try:
        parsed = json.loads(value)
        return parsed if parsed is not None else default
    except (TypeError, json.JSONDecodeError):
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=True)


def get_or_create_order_check(order: Order) -> OrderCheck:
    check = order.order_check
    if check:
        return check
    check = OrderCheck(order_id=order.id, current_page=1)
    db.session.add(check)
    db.session.flush()
    return check


def get_parsed_json(order_check: OrderCheck) -> Dict[str, Any]:
    return _loads(order_check.parsed_json, {})


def set_parsed_json(order_check: OrderCheck, value: Dict[str, Any]):
    order_check.parsed_json = _dumps(value)


def get_dynamic_design_fields(order_check: OrderCheck) -> Dict[str, Any]:
    return _loads(order_check.dynamic_design_fields, {})


def set_dynamic_design_fields(order_check: OrderCheck, value: Dict[str, Any]):
    order_check.dynamic_design_fields = _dumps(value)


def get_dynamic_responses(order_check: OrderCheck) -> Dict[str, bool]:
    parsed = _loads(order_check.dynamic_responses, {})
    if not isinstance(parsed, dict):
        return {}
    return {str(k): bool(v) for k, v in parsed.items()}


def set_dynamic_responses(order_check: OrderCheck, value: Dict[str, bool]):
    order_check.dynamic_responses = _dumps(value)


def mark_approved(order_check: OrderCheck):
    order_check.approved = True
    order_check.approved_at = datetime.utcnow()

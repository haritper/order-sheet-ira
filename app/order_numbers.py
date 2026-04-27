from __future__ import annotations

import re
from datetime import date, datetime

from app.extensions import db
from app.models import Order, OrderAssignment, OrderNumberCounter


def team_slug(team_name: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "-", str(team_name or "").strip().upper()).strip("-")
    return token or "TEAM"


def get_or_create_counter_settings() -> OrderNumberCounter:
    row = OrderNumberCounter.query.order_by(OrderNumberCounter.id.asc()).first()
    if row is not None:
        if getattr(row, "invoice_next_number", None) in (None, 0):
            row.invoice_next_number = 1
        return row
    row = OrderNumberCounter(
        pod_next_number=1,
        ira_next_number=1,
        invoice_next_number=1,
        sequence_width=3,
    )
    db.session.add(row)
    db.session.flush()
    return row


def consume_pod_numbers(
    *,
    count: int,
    override_start: int | None = None,
    update_global_from_override: bool = False,
) -> tuple[list[int], int]:
    if count <= 0:
        return [], 3
    settings = get_or_create_counter_settings()
    base_width = max(3, int(getattr(settings, "sequence_width", 3) or 3))
    now = datetime.now()
    year = int(now.year)
    month_abbr = now.strftime("%b").upper()

    used_sequences = {
        int(v)
        for (v,) in (
            OrderAssignment.query.with_entities(OrderAssignment.sequence_number)
            .filter(
                OrderAssignment.year == year,
                OrderAssignment.month_abbr == month_abbr,
            )
            .all()
        )
        if v is not None
    }

    if override_start is None:
        cursor = int(settings.pod_next_number or 1)
    else:
        cursor = int(override_start)

    picked: list[int] = []
    while len(picked) < int(count):
        if cursor not in used_sequences:
            picked.append(cursor)
            used_sequences.add(cursor)
        cursor += 1

    if override_start is None or update_global_from_override:
        settings.pod_next_number = cursor

    last_value = picked[-1] if picked else 1
    width = max(base_width, len(str(last_value)))
    return picked, width


def build_order_code(prefix: str, sequence: int, width: int, team_name: str) -> str:
    now = datetime.now()
    year = now.year
    month_abbr = now.strftime("%b").upper()
    return f"{str(prefix).upper()}-{year}-{month_abbr}-{str(int(sequence)).zfill(max(3, int(width or 3)))}-{team_slug(team_name)}"


def derive_team_slug_from_pod_id(order_id: str) -> str:
    token = str(order_id or "").strip()
    if token.upper().startswith("POD-"):
        parts = token.split("-", 4)
        if len(parts) >= 5:
            return team_slug(parts[4])
    return "TEAM"


def get_or_assign_ira_order_id(order: Order) -> str:
    existing = str(getattr(order, "production_order_id", "") or "").strip()
    if existing:
        return existing

    settings = get_or_create_counter_settings()
    seq = int(settings.ira_next_number or 1)
    base_width = max(3, int(getattr(settings, "sequence_width", 3) or 3))

    team_name = None
    assignment = getattr(order, "assignment", None)
    if assignment is not None:
        team_name = str(getattr(assignment, "team_name", "") or "").strip()
    if not team_name:
        team_name = derive_team_slug_from_pod_id(str(getattr(order, "order_id", "") or ""))

    now = datetime.now()
    year = int(now.year)
    month_abbr = now.strftime("%b").upper()
    sequence_pattern = re.compile(r"^IRA-(\d{4})-([A-Z]{3})-(\d+)-")
    used_sequences = set()
    existing_rows = (
        Order.query.with_entities(Order.production_order_id)
        .filter(Order.production_order_id.isnot(None))
        .all()
    )
    for (value,) in existing_rows:
        token = str(value or "").strip().upper()
        match = sequence_pattern.match(token)
        if not match:
            continue
        token_year = int(match.group(1))
        token_month = str(match.group(2))
        if token_year != year or token_month != month_abbr:
            continue
        try:
            used_sequences.add(int(match.group(3)))
        except (TypeError, ValueError):
            continue

    while True:
        if seq in used_sequences:
            seq += 1
            continue
        width = max(base_width, len(str(seq)))
        candidate = build_order_code("IRA", seq, width, team_name)
        exists = (
            Order.query.filter(Order.production_order_id == candidate, Order.id != int(order.id))
            .first()
            is not None
        )
        if not exists:
            order.production_order_id = candidate
            settings.ira_next_number = seq + 1
            return candidate
        seq += 1


def peek_invoice_number(*, order_date: date | None = None) -> str:
    settings = get_or_create_counter_settings()
    resolved = order_date or datetime.now().date()
    year = int(resolved.year)
    sequence = int(getattr(settings, "invoice_next_number", 1) or 1)
    return f"INV-{year}-{sequence:04d}"


def consume_invoice_number(*, order_date: date | None = None) -> str:
    settings = get_or_create_counter_settings()
    resolved = order_date or datetime.now().date()
    year = int(resolved.year)
    sequence = int(getattr(settings, "invoice_next_number", 1) or 1)
    invoice_number = f"INV-{year}-{sequence:04d}"
    settings.invoice_next_number = sequence + 1
    return invoice_number

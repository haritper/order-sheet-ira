from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from flask import current_app
from pypdf import PdfReader

from .datasheet import calculate_sizewise_item_cost, find_pricing_rule
from .db import execute, parse_json, serialize_json


SIZE_LABELS = ["XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL"]


def normalize_text(value: str) -> str:
    lines = []
    for raw_line in str(value or "").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def extract_pages(pdf_path: Path) -> list[str]:
    reader = PdfReader(str(pdf_path))
    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(normalize_text(text))
    return pages


def match_group(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def parse_order_overview(text: str, label: str) -> dict[str, Any] | None:
    pattern = rf"{re.escape(label)}\s+((?:\d+\s+){{8}})(\d+)"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    counts = [int(value) for value in match.group(1).split()]
    sizes = {size: count for size, count in zip(SIZE_LABELS, counts)}
    return {
        "label": label,
        "sizes": sizes,
        "quantity": int(match.group(2)),
    }


def parse_accessories(text: str) -> dict[str, int]:
    match = re.search(
        r"ORDERED QUANTITY\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return {"cap": 0, "hat": 0, "pad_clad": 0, "helmet_clad": 0}
    return {
        "cap": int(match.group(1)),
        "hat": int(match.group(2)),
        "pad_clad": int(match.group(3)),
        "helmet_clad": int(match.group(4)),
    }


def detect_descriptor(text: str, options: list[str], default: str) -> str:
    upper = text.upper()
    for option in options:
        if option.upper() in upper:
            return option
    return default


def extract_order_payload(pdf_path: Path) -> dict[str, Any]:
    pages = extract_pages(pdf_path)
    combined = " ".join(pages)
    overview_pages = collect_overview_pages(pages)
    combined_lines = "\n".join(overview_pages)
    page1 = pages[0] if pages else combined
    page2 = pages[1] if len(pages) > 1 else combined
    page3 = pages[2] if len(pages) > 2 else combined
    page4 = pages[3] if len(pages) > 3 else combined

    overview_rows = parse_order_overview_rows(combined_lines)
    half_shirt = parse_order_overview(page1, "HALF SLEEVE T SHIRT")
    full_shirt = parse_order_overview(page1, "FULL SLEEVE T SHIRT")
    trousers = parse_order_overview(page1, "TROUSER")
    accessories = parse_accessories(page1)

    shirt_descriptor = detect_descriptor(
        page2,
        ["POLO", "MANDARIN", "INSERT", "ZIP UP", "RIB"],
        "POLO",
    )
    trouser_descriptor = detect_descriptor(
        page3,
        [
            "SINGLE PIPING",
            "TOP PANEL",
            "BOTTOM PANEL",
            "TOP BOTTOM",
            "MIDDLE PANEL",
            "TOP STRIPE PANEL",
        ],
        "TOP PANEL",
    )
    cap_descriptor = detect_descriptor(page4, ["BASE", "BOTTOM", "MIXED"], "BASE")

    return {
        "order_number": match_group(r"ORDER ID\s+([A-Z0-9-]+)", combined),
        "enquiry_date": match_group(r"ENQUIRY DATE\s+(.+?)\s+SUBMISSION ID", combined),
        "customer_name": match_group(r"CONFIRMED ON NAME\s+(.+?)\s+MOBILE", combined),
        "mobile": match_group(r"MOBILE\s+(.+?)\s+SHIPPING ADDRESS", combined),
        "shipping_address": match_group(r"SHIPPING ADDRESS\s+(.+?)\s+CITY", combined),
        "destination_city": match_group(r"CITY\s+(.+?)\s+ZIP CODE", combined),
        "destination_state": match_group(r"STATE\s+(.+?)\s+COUNTRY", combined),
        "destination_country": match_group(r"COUNTRY\s+(.+?)\s+ORDER OVERVIEW", combined),
        "items": {
            "overview_rows": overview_rows,
            "half_shirt": half_shirt,
            "full_shirt": full_shirt,
            "trousers": trousers,
            "accessories": accessories,
        },
        "detected_styles": {
            "shirt_descriptor": shirt_descriptor,
            "trouser_descriptor": trouser_descriptor,
            "cap_descriptor": cap_descriptor,
        },
        "pages": pages,
    }


def collect_overview_pages(pages: list[str]) -> list[str]:
    collected: list[str] = []
    for page_text in pages:
        if is_design_or_post_overview_page(page_text):
            break
        collected.append(page_text)
    return collected or pages[:1]


def is_design_or_post_overview_page(text: str) -> bool:
    upper = str(text or "").upper()
    hard_markers = [
        "PACKING LIST",
        "NOTES & CHECKLIST",
        "ACCESSORIES DESIGN",
        "RIGHT CHEST LOGO",
        "LEFT CHEST LOGO",
        "RIGHT UP LOGO",
        "LEFT UP LOGO",
        "RIGHT SIDE LOGO",
        "LEFT SIDE LOGO",
        "FLAG LOGO",
    ]
    if any(marker in upper for marker in hard_markers):
        return True

    if (
        " FRONT " in f" {upper} "
        and " RIGHT " in f" {upper} "
        and " BACK " in f" {upper} "
        and " LEFT " in f" {upper} "
        and "UNIFORMS" not in upper
    ):
        return True

    return False


def build_order_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    overview_rows = payload["items"].get("overview_rows") or []
    if overview_rows:
        for index, row in enumerate(overview_rows):
            quantity = int(row.get("quantity") or 0)
            if quantity <= 0:
                continue
            category, variant = classify_overview_label(str(row.get("label") or ""))
            if category is None:
                continue
            descriptor = choose_descriptor_for_category(
                payload["detected_styles"],
                category=category,
                group_label=str(row.get("group_label") or ""),
                row_label=str(row.get("label") or ""),
            )
            item_key = f"{row.get('category', 'general')}_{index}"
            display_name = str(row.get("label") or "Product").title()
            items.append(
                build_item(
                    item_key=item_key,
                    display_name=display_name,
                    category=category,
                    quantity=quantity,
                    sizes=dict(row.get("sizes") or {}),
                    descriptor=descriptor,
                    variant=variant,
                )
            )
    else:
        shirt_descriptor = payload["detected_styles"]["shirt_descriptor"]
        if payload["items"]["half_shirt"] and payload["items"]["half_shirt"]["quantity"] > 0:
            items.append(
                build_item(
                    item_key="half_shirt",
                    display_name="Half Sleeve T Shirt",
                    category="shirt",
                    quantity=payload["items"]["half_shirt"]["quantity"],
                    sizes=payload["items"]["half_shirt"]["sizes"],
                    descriptor=shirt_descriptor,
                    variant="HALF SLEEVE",
                )
            )
        if payload["items"]["full_shirt"] and payload["items"]["full_shirt"]["quantity"] > 0:
            items.append(
                build_item(
                    item_key="full_shirt",
                    display_name="Full Sleeve T Shirt",
                    category="shirt",
                    quantity=payload["items"]["full_shirt"]["quantity"],
                    sizes=payload["items"]["full_shirt"]["sizes"],
                    descriptor=shirt_descriptor,
                    variant="FULL SLEEVE",
                )
            )
        if payload["items"]["trousers"] and payload["items"]["trousers"]["quantity"] > 0:
            items.append(
                build_item(
                    item_key="trousers",
                    display_name="Trouser",
                    category="trouser",
                    quantity=payload["items"]["trousers"]["quantity"],
                    sizes=payload["items"]["trousers"]["sizes"],
                    descriptor=payload["detected_styles"]["trouser_descriptor"],
                    variant=None,
                )
            )

    accessories = payload["items"]["accessories"]
    if accessories["cap"] > 0:
        items.append(
            build_item(
                item_key="cap",
                display_name="Cap",
                category="cap",
                quantity=accessories["cap"],
                sizes={},
                descriptor=payload["detected_styles"]["cap_descriptor"],
                variant=None,
            )
        )
    if accessories["pad_clad"] > 0:
        items.append(
            build_item(
                item_key="pad_clad",
                display_name="Pad Clad",
                category="clad",
                quantity=accessories["pad_clad"],
                sizes={},
                descriptor="PAD CLAD",
                variant=None,
            )
        )
    if accessories["helmet_clad"] > 0:
        items.append(
            build_item(
                item_key="helmet_clad",
                display_name="Helmet Clad",
                category="clad",
                quantity=accessories["helmet_clad"],
                sizes={},
                descriptor="HELMET CLAD",
                variant=None,
            )
        )
    return items


def parse_order_overview_rows(text: str) -> list[dict[str, Any]]:
    category_markers = {
        "MEN'S UNIFORMS": "mens",
        "MENS UNIFORMS": "mens",
        "MEN’S UNIFORMS": "mens",
        "WOMEN'S UNIFORMS": "womens",
        "WOMENS UNIFORMS": "womens",
        "WOMEN’S UNIFORMS": "womens",
        "KIDS / YOUTH UNIFORMS": "youth",
        "YOUTH UNIFORMS": "youth",
        "KIDS UNIFORMS": "youth",
        "KIDS / YOUTH": "youth",
        "YOUTH / KIDS UNIFORMS": "youth",
    }
    rows: list[dict[str, Any]] = []
    current_category = ""
    current_group = ""
    current_sizes: list[str] = []
    lines = [str(raw_line or "").strip() for raw_line in text.splitlines()]
    index = 0

    while index < len(lines):
        line = lines[index]
        if not line:
            index += 1
            continue
        upper = line.upper()
        if upper in category_markers:
            current_category = category_markers[upper]
            current_group = ""
            current_sizes = []
            index += 1
            continue
        if not current_category:
            index += 1
            continue
        if upper.startswith("TYPE "):
            size_tokens = upper.replace("TYPE ", "", 1).split()
            if "TOTAL" in size_tokens:
                size_tokens = size_tokens[: size_tokens.index("TOTAL")]
            current_sizes = [token.strip().upper() for token in size_tokens if token.strip()]
            index += 1
            continue
        if not current_sizes:
            current_group = line
            index += 1
            continue

        parsed = parse_sized_row(line, current_sizes)
        if parsed is None and index + 1 < len(lines):
            next_line = lines[index + 1]
            next_numbers = parse_numeric_tail(next_line)
            expected = len(current_sizes) + 1
            if len(next_numbers) == expected:
                parsed = parse_sized_row(f"{line} {next_line}", current_sizes)
                if parsed is not None:
                    index += 1
        if parsed is None:
            current_group = line
            index += 1
            continue
        rows.append(
            {
                "category": current_category,
                "group_label": current_group,
                "label": parsed["label"],
                "sizes": parsed["sizes"],
                "quantity": parsed["quantity"],
            }
        )
        index += 1
    return rows


def parse_sized_row(line: str, sizes: list[str]) -> dict[str, Any] | None:
    tokens = line.split()
    numbers: list[int] = []
    while tokens:
        token = tokens[-1]
        if re.fullmatch(r"\d+", token):
            numbers.append(int(token))
            tokens.pop()
            continue
        break
    numbers.reverse()
    expected = len(sizes) + 1  # includes TOTAL
    if len(numbers) != expected:
        return None
    label = " ".join(tokens).strip()
    if not label:
        return None
    size_map = {size: count for size, count in zip(sizes, numbers[:-1]) if int(count or 0) > 0}
    return {"label": label, "sizes": size_map, "quantity": int(numbers[-1] or 0)}


def parse_numeric_tail(line: str) -> list[int]:
    tokens = str(line or "").split()
    values: list[int] = []
    while tokens:
        token = tokens[-1]
        if re.fullmatch(r"\d+", token):
            values.append(int(token))
            tokens.pop()
            continue
        break
    values.reverse()
    return values


def classify_overview_label(label: str) -> tuple[str | None, str | None]:
    text = label.strip().upper()
    if "HALF SLEEVE" in text and "SHIRT" in text:
        return "shirt", "HALF SLEEVE"
    if "FULL SLEEVE" in text and "SHIRT" in text:
        return "shirt", "FULL SLEEVE"
    if "TROUSER" in text or "PANT" in text or "SHORT" in text:
        return "trouser", None
    if "JACKET" in text or "HOODIE" in text:
        return "jacket", None
    if "CAP" in text:
        return "cap", None
    if "CLAD" in text:
        return "clad", None
    if "SHIRT" in text or "JERSEY" in text:
        return "shirt", None
    return None, None


def choose_descriptor_for_category(
    detected_styles: dict[str, str],
    *,
    category: str,
    group_label: str,
    row_label: str,
) -> str | None:
    if category == "shirt":
        return detected_styles.get("shirt_descriptor")
    if category == "trouser":
        return detected_styles.get("trouser_descriptor")
    if category == "cap":
        return detected_styles.get("cap_descriptor")
    merged = f"{group_label} {row_label}".strip()
    return merged[:80] if merged else None


def build_item(
    item_key: str,
    display_name: str,
    category: str,
    quantity: int,
    sizes: dict[str, int],
    descriptor: str | None,
    variant: str | None,
) -> dict[str, Any]:
    rule = find_pricing_rule(category=category, descriptor=descriptor, variant=variant)
    product_code = rule["product_code"] if rule else None
    size_costing = calculate_sizewise_item_cost(rule, sizes) if rule else None
    if size_costing:
        unit_rate = float(size_costing["average_unit_usd"])
        line_total = float(size_costing["total_usd"])
    else:
        unit_rate = float(rule["effective_unit_rate_usd"]) if rule else 0.0
        line_total = round(unit_rate * quantity, 2)
    return {
        "item_key": item_key,
        "display_name": display_name,
        "category": category,
        "quantity": quantity,
        "sizes": sizes,
        "descriptor": descriptor,
        "variant": variant,
        "product_code": product_code,
        "unit_suggested_rate": unit_rate,
        "line_suggested_total": line_total,
        "size_costing": size_costing,
    }


def create_order_from_upload(file_storage, upload_folder: Path, user_id: int) -> int:
    upload_folder.mkdir(parents=True, exist_ok=True)
    original_name = file_storage.filename or "order-sheet.pdf"
    safe_name = Path(original_name).name
    destination = upload_folder / safe_name
    suffix = 1
    while destination.exists():
        destination = upload_folder / f"{destination.stem}-{suffix}{destination.suffix}"
        suffix += 1
    file_storage.save(destination)

    return _create_or_update_from_pdf_path(
        pdf_path=destination,
        user_id=user_id,
        external_order_id=None,
    )


def ingest_production_plan_pdf(
    *,
    external_order_id: str,
    pdf_bytes: bytes,
    upload_folder: Path,
    user_id: int,
    filename: str | None = None,
) -> int:
    upload_folder.mkdir(parents=True, exist_ok=True)
    safe_external = re.sub(r"[^A-Za-z0-9_-]+", "-", external_order_id).strip("-") or "order"
    suffix = Path(filename or "production-plan.pdf").suffix or ".pdf"
    destination = upload_folder / f"{safe_external}-production{suffix}"
    destination.write_bytes(pdf_bytes)
    return _create_or_update_from_pdf_path(
        pdf_path=destination,
        user_id=user_id,
        external_order_id=external_order_id,
    )


def _create_or_update_from_pdf_path(
    *,
    pdf_path: Path,
    user_id: int,
    external_order_id: str | None,
) -> int:
    payload = extract_order_payload(pdf_path)
    items = build_order_items(payload)
    base_order_number = payload["order_number"] or pdf_path.stem
    suggested_subtotal = round(sum(item["line_suggested_total"] for item in items), 2)
    shipping_fee, duty_fee = default_fees_for_country(
        payload["destination_country"], suggested_subtotal
    )

    existing_order_id: int | None = None
    if external_order_id:
        existing_row = execute(
            "SELECT id, order_number FROM orders WHERE external_order_id = ?",
            (external_order_id,),
        ).fetchone()
        if existing_row:
            existing_order_id = int(existing_row["id"])

    order_number = unique_order_number(base_order_number, existing_order_id=existing_order_id)
    if existing_order_id is None:
        cursor = execute(
            """
            INSERT INTO orders (
                order_number, external_order_id, customer_name, enquiry_date, mobile, shipping_address,
                destination_city, destination_state, destination_country, status,
                uploaded_filename, extracted_payload_json, suggested_subtotal,
                shipping_fee, duty_fee,
                final_landed_cost, final_margin, created_by_user_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'uploaded', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_number,
                external_order_id,
                payload["customer_name"],
                payload["enquiry_date"],
                payload["mobile"],
                payload["shipping_address"],
                payload["destination_city"],
                payload["destination_state"],
                payload["destination_country"],
                str(pdf_path.resolve()),
                serialize_json(payload),
                suggested_subtotal,
                shipping_fee,
                duty_fee,
                suggested_subtotal,
                0,
                user_id,
            ),
        )
        order_id = int(cursor.lastrowid)
    else:
        execute(
            """
            UPDATE orders
            SET order_number = ?,
                customer_name = ?,
                enquiry_date = ?,
                mobile = ?,
                shipping_address = ?,
                destination_city = ?,
                destination_state = ?,
                destination_country = ?,
                status = 'uploaded',
                uploaded_filename = ?,
                extracted_payload_json = ?,
                suggested_subtotal = ?,
                shipping_fee = ?,
                duty_fee = ?,
                final_landed_cost = ?,
                final_margin = 0,
                created_by_user_id = ?,
                external_order_id = ?
            WHERE id = ?
            """,
            (
                order_number,
                payload["customer_name"],
                payload["enquiry_date"],
                payload["mobile"],
                payload["shipping_address"],
                payload["destination_city"],
                payload["destination_state"],
                payload["destination_country"],
                str(pdf_path.resolve()),
                serialize_json(payload),
                suggested_subtotal,
                shipping_fee,
                duty_fee,
                suggested_subtotal,
                user_id,
                external_order_id,
                existing_order_id,
            ),
        )
        order_id = existing_order_id
        execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))

    for item in items:
        execute(
            """
            INSERT INTO order_items (
                order_id, item_key, display_name, product_code, category, quantity,
                sizes_json, extracted_attributes_json, unit_suggested_rate,
                line_suggested_total
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                item["item_key"],
                item["display_name"],
                item["product_code"],
                item["category"],
                item["quantity"],
                serialize_json(item["sizes"]),
                serialize_json(
                    {
                        "descriptor": item["descriptor"],
                        "variant": item["variant"],
                        "size_costing": item["size_costing"],
                    }
                ),
                item["unit_suggested_rate"],
                item["line_suggested_total"],
            ),
        )

    refresh_order_totals(order_id)
    return order_id


def refresh_order_totals(order_id: int) -> None:
    order = execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        return
    item_rows = execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,)).fetchall()
    suggested_subtotal = round(
        sum(float(item["line_suggested_total"] or 0) for item in item_rows), 2
    )
    quoted_subtotal = round(
        sum(float(item["line_quoted_total"] or 0) for item in item_rows), 2
    )
    shipping_fee = float(order["shipping_fee"] or 0)
    duty_fee = float(order["duty_fee"] or 0)
    final_landed_cost = round(suggested_subtotal + shipping_fee + duty_fee, 2)
    final_margin = round(quoted_subtotal - final_landed_cost, 2)
    execute(
        """
        UPDATE orders
        SET suggested_subtotal = ?,
            quoted_subtotal = ?,
            final_landed_cost = ?,
            final_margin = ?
        WHERE id = ?
        """,
        (
            suggested_subtotal,
            quoted_subtotal,
            final_landed_cost,
            final_margin,
            order_id,
        ),
    )


def recalculate_order_pricing(order_id: int) -> bool:
    item_rows = execute(
        """
        SELECT *
        FROM order_items
        WHERE order_id = ?
        ORDER BY id
        """,
        (order_id,),
    ).fetchall()
    any_changed = False
    for item_row in item_rows:
        item = dict(item_row)
        sizes = parse_json(item["sizes_json"], {})
        attributes = parse_json(item["extracted_attributes_json"], {})
        updated_item = build_item(
            item_key=item["item_key"],
            display_name=item["display_name"],
            category=item["category"],
            quantity=int(item["quantity"] or 0),
            sizes=sizes,
            descriptor=attributes.get("descriptor"),
            variant=attributes.get("variant"),
        )
        updated_attributes = dict(attributes)
        updated_attributes["descriptor"] = updated_item["descriptor"]
        updated_attributes["variant"] = updated_item["variant"]
        updated_attributes["size_costing"] = updated_item["size_costing"]

        current_unit = round(float(item["unit_suggested_rate"] or 0), 2)
        current_total = round(float(item["line_suggested_total"] or 0), 2)
        next_unit = round(float(updated_item["unit_suggested_rate"] or 0), 2)
        next_total = round(float(updated_item["line_suggested_total"] or 0), 2)
        if (
            item["product_code"] == updated_item["product_code"]
            and current_unit == next_unit
            and current_total == next_total
            and attributes == updated_attributes
        ):
            continue

        execute(
            """
            UPDATE order_items
            SET product_code = ?,
                extracted_attributes_json = ?,
                unit_suggested_rate = ?,
                line_suggested_total = ?
            WHERE id = ?
            """,
            (
                updated_item["product_code"],
                serialize_json(updated_attributes),
                next_unit,
                next_total,
                item["id"],
            ),
        )
        any_changed = True

    if any_changed:
        refresh_order_totals(order_id)
    return any_changed


def recalculate_orders(order_ids: list[int]) -> bool:
    any_changed = False
    for order_id in order_ids:
        any_changed = recalculate_order_pricing(order_id) or any_changed
    return any_changed


def list_orders() -> list[dict[str, Any]]:
    rows = execute(
        """
        SELECT
            orders.*,
            users.username AS created_by_username
        FROM orders
        JOIN users ON users.id = orders.created_by_user_id
        ORDER BY orders.created_at DESC, orders.id DESC
        """
    ).fetchall()
    if rows and recalculate_orders([int(row["id"]) for row in rows]):
        rows = execute(
            """
            SELECT
                orders.*,
                users.username AS created_by_username
            FROM orders
            JOIN users ON users.id = orders.created_by_user_id
            ORDER BY orders.created_at DESC, orders.id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_order(order_id: int) -> dict[str, Any] | None:
    order_row = execute(
        """
        SELECT
            orders.*,
            users.username AS created_by_username
        FROM orders
        JOIN users ON users.id = orders.created_by_user_id
        WHERE orders.id = ?
        """,
        (order_id,),
    ).fetchone()
    if not order_row:
        return None
    if recalculate_order_pricing(order_id):
        order_row = execute(
            """
            SELECT
                orders.*,
                users.username AS created_by_username
            FROM orders
            JOIN users ON users.id = orders.created_by_user_id
            WHERE orders.id = ?
            """,
            (order_id,),
        ).fetchone()
    items = execute(
        """
        SELECT *
        FROM order_items
        WHERE order_id = ?
        ORDER BY id
        """,
        (order_id,),
    ).fetchall()
    order = dict(order_row)
    order["extracted_payload"] = parse_json(order["extracted_payload_json"], {})
    parsed_items = []
    for item in items:
        item_dict = dict(item)
        item_dict["sizes"] = parse_json(item_dict["sizes_json"], {})
        item_dict["extracted_attributes"] = parse_json(
            item_dict["extracted_attributes_json"], {}
        )
        parsed_items.append(item_dict)
    order["items"] = parsed_items
    return order


def update_order_rates_and_costs(
    order_id: int,
    quoted_rates: dict[int, float | None],
    shipping_fee: float,
    duty_fee: float,
    status: str,
    mark_delivered: bool,
    notes: str,
) -> None:
    if mark_delivered:
        status = "delivered"
    for item_id, quoted_rate in quoted_rates.items():
        item = execute("SELECT quantity FROM order_items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            continue
        quantity = int(item["quantity"])
        line_quoted_total = round((quoted_rate or 0) * quantity, 2) if quoted_rate is not None else 0
        execute(
            """
            UPDATE order_items
            SET unit_quoted_rate = ?, line_quoted_total = ?
            WHERE id = ?
            """,
            (quoted_rate, line_quoted_total, item_id),
        )

    delivered_at = "CURRENT_TIMESTAMP" if mark_delivered else None
    if delivered_at:
        execute(
            f"""
            UPDATE orders
            SET shipping_fee = ?,
                duty_fee = ?,
                status = ?,
                notes = ?,
                delivered_at = {delivered_at}
            WHERE id = ?
            """,
            (shipping_fee, duty_fee, status, notes, order_id),
        )
    else:
        execute(
            """
            UPDATE orders
            SET shipping_fee = ?,
                duty_fee = ?,
                status = ?,
                notes = ?
            WHERE id = ?
            """,
            (shipping_fee, duty_fee, status, notes, order_id),
        )
    if mark_delivered:
        update_shipping_profile_from_actual(order_id, shipping_fee, duty_fee)
    refresh_order_totals(order_id)


def owner_metrics() -> dict[str, Any]:
    totals = execute(
        """
        SELECT
            COUNT(*) AS total_orders,
            SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) AS delivered_orders,
            COALESCE(SUM(quoted_subtotal), 0) AS total_quoted,
            COALESCE(SUM(final_landed_cost), 0) AS total_landed_cost,
            COALESCE(SUM(final_margin), 0) AS total_margin
        FROM orders
        """
    ).fetchone()
    return dict(totals)


def delete_order(order_id: int) -> bool:
    order = execute(
        "SELECT uploaded_filename FROM orders WHERE id = ?",
        (order_id,),
    ).fetchone()
    if not order:
        return False
    uploaded_filename = order["uploaded_filename"]
    execute("DELETE FROM orders WHERE id = ?", (order_id,))

    if uploaded_filename:
        try:
            file_path = Path(str(uploaded_filename)).resolve()
            uploads_root = Path(current_app.config["PRICING_UPLOAD_FOLDER"]).resolve()
            if uploads_root in file_path.parents and file_path.exists():
                file_path.unlink()
        except OSError:
            pass
    return True


def unique_order_number(base_number: str, existing_order_id: int | None = None) -> str:
    existing_row = execute(
        "SELECT id FROM orders WHERE order_number = ? LIMIT 1",
        (base_number,),
    ).fetchone()
    if not existing_row:
        return base_number
    if existing_order_id:
        if int(existing_row["id"]) == int(existing_order_id):
            return base_number
    suffix = 2
    while True:
        candidate = f"{base_number}-{suffix}"
        duplicate = execute(
            "SELECT id FROM orders WHERE order_number = ?",
            (candidate,),
        ).fetchone()
        if not duplicate or (
            existing_order_id and int(duplicate["id"]) == int(existing_order_id)
        ):
            return candidate
        suffix += 1


def default_fees_for_country(country: str | None, suggested_subtotal: float) -> tuple[float, float]:
    if not country:
        return 0.0, 0.0
    profile = execute(
        """
        SELECT *
        FROM shipping_profiles
        WHERE LOWER(destination_country) = LOWER(?)
        ORDER BY id
        LIMIT 1
        """,
        (country,),
    ).fetchone()
    if not profile:
        return 0.0, 0.0
    shipping_fee = round(float(profile["fee"] or 0), 2)
    duty_fee = round(
        (suggested_subtotal * float(profile["duty_percent"] or 0) / 100)
        + float(profile["duty_flat"] or 0),
        2,
    )
    return shipping_fee, duty_fee


def update_shipping_profile_from_actual(order_id: int, shipping_fee: float, duty_fee: float) -> None:
    order = execute(
        "SELECT destination_country FROM orders WHERE id = ?",
        (order_id,),
    ).fetchone()
    if not order or not order["destination_country"]:
        return
    country = order["destination_country"]
    profile = execute(
        """
        SELECT id
        FROM shipping_profiles
        WHERE LOWER(destination_country) = LOWER(?)
        ORDER BY id
        LIMIT 1
        """,
        (country,),
    ).fetchone()
    if profile:
        execute(
            """
            UPDATE shipping_profiles
            SET fee = ?, duty_percent = 0, duty_flat = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (shipping_fee, duty_fee, profile["id"]),
        )
        return
    execute(
        """
        INSERT INTO shipping_profiles (
            profile_name, destination_country, fee, duty_percent, duty_flat, notes
        )
        VALUES (?, ?, ?, 0, ?, ?)
        """,
        (
            f"{country} Latest Actual",
            country,
            shipping_fee,
            duty_fee,
            "Auto-updated from the latest delivered order.",
        ),
    )


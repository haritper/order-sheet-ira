from __future__ import annotations

import json
from datetime import datetime

from app.storage import ORDER_META_SECTION, exists, read_bytes, save_order_text


FINAL_APPROVAL_FIELDS = [
    "design_checked",
    "logos_checked",
    "gender_checked",
    "sleeve_type_checked",
    "names_numbers_sizes_checked",
    "quantity_checked",
]

SPEC_FIELD_MAP = [
    ("style_number", "Style #"),
    ("collar_type", "Style Type"),
    ("fabric", "Fabric"),
    ("panel_color_primary", "Primary Color"),
    ("panel_color_secondary", "Secondary Color"),
    ("front_image_path", "Front Image"),
    ("back_image_path", "Back Image"),
    ("right_image_path", "Right Image"),
    ("left_image_path", "Left Image"),
    ("logo_right_path", "Right Logo"),
    ("logo_left_path", "Left Logo"),
]

MENS_SIZES = ["XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL"]
WOMENS_SIZES = ["WXS", "WS", "WM", "WL", "WXL", "W2XL", "W3XL", "W4XL"]
YOUTH_SIZES = ["YXXS", "YXS", "YS", "YM", "YL", "YXL"]


def checklist_state_path(order_id: int) -> str:
    return f"order://{int(order_id)}/{ORDER_META_SECTION}/checklist_state.json"


def load_checklist_state(order) -> dict:
    default_responses = {
        "core.order_id_verified": False,
        "core.enquiry_date_verified": False,
    }
    for field in FINAL_APPROVAL_FIELDS:
        default_responses[f"final.{field}"] = False

    default_state = {
        "current_page": 1,
        "responses": default_responses.copy(),
        "approved": False,
        "approved_at": None,
        "flow": {
            "customer_plan_generated": False,
            "customer_plan_attachment_id": None,
            "customer_approved": False,
            "shipping_address": "",
            "city": "",
            "state": "",
            "zip_code": "",
            "country": "",
            "production_plan_generated": False,
            "production_plan_attachment_id": None,
        },
        "updated_at": datetime.utcnow().isoformat(),
    }

    path = checklist_state_path(order.id)
    if exists(path):
        try:
            disk_state = json.loads(read_bytes(path).decode("utf-8"))
            if isinstance(disk_state, dict):
                state = default_state.copy()
                state["responses"] = default_responses.copy()
                responses = disk_state.get("responses", {})
                if isinstance(responses, dict):
                    state["responses"].update({str(k): bool(v) for k, v in responses.items()})
                flow = disk_state.get("flow", {})
                if isinstance(flow, dict):
                    merged_flow = dict(state["flow"])
                    merged_flow.update(
                        {
                            "customer_plan_generated": bool(flow.get("customer_plan_generated", False)),
                            "customer_plan_attachment_id": flow.get("customer_plan_attachment_id"),
                            "customer_approved": bool(flow.get("customer_approved", False)),
                            "shipping_address": str(flow.get("shipping_address", "") or "").strip(),
                            "city": str(flow.get("city", "") or "").strip(),
                            "state": str(flow.get("state", "") or "").strip(),
                            "zip_code": str(flow.get("zip_code", "") or "").strip(),
                            "country": str(flow.get("country", "") or "").strip(),
                            "production_plan_generated": bool(flow.get("production_plan_generated", False)),
                            "production_plan_attachment_id": flow.get("production_plan_attachment_id"),
                        }
                    )
                    state["flow"] = merged_flow
                state["current_page"] = int(disk_state.get("current_page", 1) or 1)
                state["approved"] = bool(disk_state.get("approved", False))
                state["approved_at"] = disk_state.get("approved_at")
                state["updated_at"] = disk_state.get("updated_at") or datetime.utcnow().isoformat()
                return state
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    order_check = getattr(order, "order_check", None)
    if order_check is not None:
        parsed_responses = default_responses.copy()
        raw = getattr(order_check, "dynamic_responses", None)
        if isinstance(raw, str) and raw.strip():
            try:
                decoded = json.loads(raw)
                if isinstance(decoded, dict):
                    parsed_responses.update({str(k): bool(v) for k, v in decoded.items()})
            except (TypeError, json.JSONDecodeError):
                pass
        state = {
            "current_page": int(getattr(order_check, "current_page", 1) or 1),
            "responses": parsed_responses,
            "approved": bool(getattr(order_check, "approved", False)),
            "approved_at": getattr(order_check, "approved_at", None).isoformat()
            if getattr(order_check, "approved_at", None)
            else None,
            "flow": {
                "customer_plan_generated": False,
                "customer_plan_attachment_id": None,
                "customer_approved": False,
                "shipping_address": "",
                "city": "",
                "state": "",
                "zip_code": "",
                "country": "",
                "production_plan_generated": False,
                "production_plan_attachment_id": None,
            },
            "updated_at": datetime.utcnow().isoformat(),
        }
        return state

    return default_state


def save_checklist_state(order, state: dict):
    state["updated_at"] = datetime.utcnow().isoformat()
    order_check = getattr(order, "order_check", None)
    if order_check is None:
        return
    order_check.current_page = int(state.get("current_page", 1) or 1)
    # Keep dynamic_responses as the canonical merged checkbox store set by routes.
    # Do not overwrite it here with partial state["responses"], otherwise
    # previously checked dynamic fields get cleared when navigating steps.
    path = checklist_state_path(order.id)
    save_order_text(
        order.id,
        ORDER_META_SECTION,
        "checklist_state.json",
        json.dumps(state, ensure_ascii=True, indent=2),
    )


def build_checklist_sections(order):
    sections = []
    for item in sorted(order.items, key=lambda x: (x.product_name or "", x.gender or "", x.sleeve_type or "")):
        spec = item.branding_spec
        if not spec:
            continue
        spec_fields = []
        for field_name, label in SPEC_FIELD_MAP:
            value = (getattr(spec, field_name, None) or "").strip()
            if not value:
                continue
            key = f"spec.{spec.id}.{field_name}"
            spec_fields.append({"key": key, "label": label, "value": value, "field_name": field_name})

        if not spec_fields:
            continue

        title_parts = [item.product_name or "Product", item.gender or "", item.sleeve_type or ""]
        title = " | ".join([p for p in title_parts if p])
        sections.append(
            {
                "spec_id": spec.id,
                "title": title,
                "pdf_page": 3 if _is_bottomwear_product(item.product_name) else 2,
                "fields": spec_fields,
                "images": {
                    "front": spec.front_image_path,
                    "back": spec.back_image_path,
                    "right": spec.right_image_path,
                    "left": spec.left_image_path,
                    "logo_right": spec.logo_right_path,
                    "logo_left": spec.logo_left_path,
                },
            }
        )
    return sections


def checklist_completion_status(order, state: dict) -> tuple[bool, list[str]]:
    responses = state.get("responses", {}) if isinstance(state, dict) else {}
    missing = []

    if not responses.get("core.order_id_verified", False):
        missing.append("Verify Order ID")
    if not responses.get("core.enquiry_date_verified", False):
        missing.append("Verify Enquiry Date")

    # Current checklist UI is driven by core checks + dynamic product fields + final checks.
    # Dynamic product-field missing is calculated separately in routes via _dynamic_missing_list().
    # Do not include legacy spec.* section fields here, since they are not rendered as checkboxes.
    for field in FINAL_APPROVAL_FIELDS:
        key = f"final.{field}"
        if not responses.get(key, False):
            missing.append(f"Final checklist: {field.replace('_', ' ')}")

    return len(missing) == 0, missing


def build_cutting_plan(order, parsed_json: dict | None = None):
    matched_keys = _matched_product_keys(parsed_json)
    rows = []
    for item in sorted(order.items, key=lambda x: (x.product_name or "", x.gender or "", x.sleeve_type or "", x.id or 0)):
        spec = item.branding_spec
        if not spec:
            continue

        is_trouser = _is_bottomwear_product(item.product_name)
        target_gender = (item.gender or "MENS").strip().upper()
        target_sleeve = (item.sleeve_type or "HALF").strip().upper()
        item_keys = _candidate_product_keys(item.product_name, target_gender, target_sleeve)
        if matched_keys and item_keys and not any(k in matched_keys for k in item_keys):
            continue

        size_keys = _size_keys_for_gender(target_gender)
        size_map = {k: 0 for k in size_keys}

        for p in order.players:
            player_gender = _infer_player_gender(p)
            if player_gender != target_gender:
                continue
            if not is_trouser and (p.sleeve_type or "").upper() != target_sleeve:
                continue

            if is_trouser:
                size = (p.trouser_size or "").upper().strip()
                qty = _safe_int(p.trouser_qty)
            else:
                size = (p.tshirt_size or "").upper().strip()
                qty = _safe_int(p.tshirt_qty)

            if size in size_map:
                size_map[size] += qty

        total = sum(size_map.values())
        if total <= 0:
            continue

        rows.append(
            {
                "order_id": order.order_id,
                "title": " | ".join([v for v in [item.product_name, target_gender, target_sleeve if not is_trouser else "TYPE"] if v]),
                "style": (spec.style_number or "").strip(),
                "style_type": (spec.collar_type or "").strip(),
                "fabric": (spec.fabric or "").strip(),
                "primary_color": (spec.panel_color_primary or "").strip(),
                "secondary_color": (spec.panel_color_secondary or "").strip(),
                "sizes": size_map,
                "total_qty": total,
                "is_trouser": is_trouser,
                "images": {
                    "front": spec.front_image_path,
                    "back": spec.back_image_path,
                    "right": spec.right_image_path,
                    "left": spec.left_image_path,
                    "logo_right": spec.logo_right_path,
                    "logo_left": spec.logo_left_path,
                },
            }
        )

    return rows


def _size_keys_for_gender(gender: str):
    g = (gender or "").upper().strip()
    if g == "WOMENS":
        return WOMENS_SIZES
    if g == "YOUTH":
        return YOUTH_SIZES
    return MENS_SIZES


def _infer_player_gender(player):
    t_size = (player.tshirt_size or "").upper().strip()
    tr_size = (player.trouser_size or "").upper().strip()
    for value in (t_size, tr_size):
        if value.startswith("W"):
            return "WOMENS"
        if value.startswith("Y"):
            return "YOUTH"
    return "MENS"


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _is_bottomwear_product(product_name: str) -> bool:
    name = (product_name or "").strip().lower()
    return any(token in name for token in ("trouser", "touser", "pant", "short"))


def _status_match(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() == "match"


def _matched_product_keys(parsed_json: dict | None) -> set[str]:
    out: set[str] = set()
    if not isinstance(parsed_json, dict):
        return out
    qc = parsed_json.get("quantity_comparison")
    if not isinstance(qc, dict):
        return out
    for product_key, product_data in qc.items():
        if not isinstance(product_data, dict):
            continue
        if product_key == "accessories":
            for acc_key, comp in product_data.items():
                if isinstance(comp, dict) and _status_match(comp.get("status")):
                    out.add(f"accessories.{str(acc_key).strip().lower()}")
            continue
        comparisons = [comp for comp in product_data.values() if isinstance(comp, dict)]
        if not comparisons:
            continue
        if all(_status_match(comp.get("status")) for comp in comparisons):
            out.add(str(product_key).strip().lower())
    return out


def _candidate_product_keys(product_name: str, gender: str, sleeve: str) -> list[str]:
    name = (product_name or "").strip().lower()
    g = (gender or "MENS").strip().upper()
    s = (sleeve or "HALF").strip().upper()
    if "travel" in name and ("trouser" in name or "touser" in name or "pant" in name):
        # Keep travel trouser separate, but allow legacy mens_trouser fallback.
        return ["travel_trouser", f"{g.lower()}_trouser"]
    if any(token in name for token in ("trouser", "touser", "pant")):
        return [f"{g.lower()}_trouser"]
    if "short" in name:
        return ["shorts", f"{g.lower()}_trouser"]
    if any(token in name for token in ("jersey", "tshirt", "t-shirt", "t shirt")):
        sleeve_token = "full_sleeve" if s == "FULL" else "half_sleeve"
        return [f"{g.lower()}_{sleeve_token}"]
    if "travel polo" in name:
        return ["travel_polo", "polo"]
    if "polo" in name:
        return ["polo"]
    if "hoodie" in name and "zip" in name:
        return ["hoodie"]
    if "hoodie" in name:
        return ["hoodie"]
    if "sweatshirt" in name:
        return ["sweatshirt"]
    if "jacket" in name and "sleeveless" in name:
        return ["sleeveless_jacket", "jacket"]
    if "jacket" in name:
        return ["jacket"]
    if "helmet" in name and "clad" in name:
        return ["helmet_clad"]
    if "pad" in name and "clad" in name:
        return ["pad_clad"]
    return []

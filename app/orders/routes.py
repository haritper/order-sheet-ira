from pathlib import Path
from io import BytesIO
from datetime import datetime
import uuid

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import (
    Attachment,
    BrandingSpec,
    Order,
    OrderAssignment,
    OrderAssignmentStatus,
    OrderAuditLog,
    OrderStatus,
    Role,
    User,
)
from app.orders.forms import OrderHeaderForm, Step2Form, Step4ApprovalForm
from app.orders.access import can_user_access_order
from app.order_numbers import get_or_assign_ira_order_id
from app.orders.overview import (
    build_order_overview,
    build_packing_groups,
    build_player_groups,
    MENS_SIZES,
    WOMENS_SIZES,
    YOUTH_SIZES,
)
from app.orders.checklist_cutting import (
    build_checklist_sections,
    checklist_completion_status,
    load_checklist_state,
    save_checklist_state,
)
from app.orders.cutting_plan_generation import (
    normalize_cutting_plan,
    has_cutting_plan_rows,
    to_pdf_io,
)
from app.orders.ai_services import analyze_pdf_bytes_vision, empty_analysis
from app.orders.analysis_adapter import build_dynamic_products, build_garment_cmp
from app.orders.check_state import (
    get_dynamic_responses,
    get_or_create_order_check,
    get_parsed_json,
    set_dynamic_design_fields,
    set_dynamic_responses,
    set_parsed_json,
)
from app.orders.services import (
    add_product_configuration,
    bootstrap_order_rows,
    delete_product_configuration,
    move_status,
    order_ready_errors,
    PRODUCT_CATALOG,
    update_step1,
)
from app.exports.services import collect_plan_render_stats, render_order_pdf, save_plan_pdf
from app.pricing_integration.integration import ingest_production_plan_for_order
from app.storage import (
    ORDER_DOCUMENT_SECTION,
    delete_order_storage,
    ensure_order_storage,
    read_bytes,
    save_order_file,
)
from app.utils import add_audit
from app.utils import roles_required

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None

orders_bp = Blueprint("orders", __name__, url_prefix="/orders")

INVOICE_RECEIPT_ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}


def _safe_int(value, default=0):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _checklist_preview_signature(order: Order) -> str:
    def _iso(dt):
        if dt is None:
            return ""
        try:
            return dt.isoformat()
        except Exception:
            return str(dt)

    latest_related = getattr(order, "updated_at", None)
    collections = [
        getattr(order, "items", []) or [],
        getattr(order, "accessories", []) or [],
        getattr(order, "branding_specs", []) or [],
        getattr(order, "players", []) or [],
        getattr(order, "attachments", []) or [],
    ]
    for rows in collections:
        for row in rows:
            stamp = getattr(row, "updated_at", None) or getattr(row, "created_at", None)
            if stamp is not None and (latest_related is None or stamp > latest_related):
                latest_related = stamp
    check = getattr(order, "order_check", None)
    if check is not None:
        check_stamp = getattr(check, "updated_at", None) or getattr(check, "created_at", None)
        if check_stamp is not None and (latest_related is None or check_stamp > latest_related):
            latest_related = check_stamp
    return f"order={int(order.id)}|latest={_iso(latest_related)}"


def _get_checklist_preview_pdf(order: Order, *, force_refresh: bool = False) -> bytes:
    base_dir = (Path(current_app.config["UPLOAD_DIR"]) / str(order.id)).resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    cache_pdf = base_dir / "checklist-preview.pdf"
    cache_sig = base_dir / "checklist-preview.sig"
    signature = _checklist_preview_signature(order)

    if not force_refresh and cache_pdf.exists() and cache_sig.exists():
        try:
            if cache_sig.read_text(encoding="utf-8").strip() == signature:
                return cache_pdf.read_bytes()
        except OSError:
            pass

    pdf_bytes = render_order_pdf(order)
    try:
        cache_pdf.write_bytes(pdf_bytes)
        cache_sig.write_text(signature, encoding="utf-8")
    except OSError:
        # Non-fatal: return rendered bytes even if cache write fails.
        pass
    return pdf_bytes


def _operator_assignment_choices(order: Order):
    if str(getattr(current_user, "role", "")).strip().lower() != Role.OPERATOR.value:
        return []
    base_query = OrderAssignment.query.filter(OrderAssignment.operator_id == int(current_user.id))
    if getattr(order, "assignment_id", None):
        base_query = base_query.filter(
            or_(
                OrderAssignment.id == int(order.assignment_id),
                (
                    OrderAssignment.status.in_(
                        [OrderAssignmentStatus.PENDING.value, OrderAssignmentStatus.IN_PROGRESS.value]
                    )
                    & or_(
                        OrderAssignment.linked_order_id.is_(None),
                        OrderAssignment.linked_order_id == int(order.id),
                    )
                ),
            )
        )
    else:
        base_query = base_query.filter(
            OrderAssignment.status.in_(
                [OrderAssignmentStatus.PENDING.value, OrderAssignmentStatus.IN_PROGRESS.value]
            ),
            OrderAssignment.linked_order_id.is_(None),
        )
    rows = (
        base_query.order_by(
            OrderAssignment.sequence_number.asc(),
            OrderAssignment.id.asc(),
        ).all()
    )
    choices = []
    for row in rows:
        suffix = f" ({row.status})"
        choices.append(
            {
                "id": int(row.id),
                "label": f"{row.order_code}{suffix}",
                "order_code": str(row.order_code or "").strip(),
            }
        )
    return choices


def _enforce_order_access(order: Order):
    if not can_user_access_order(current_user, order):
        abort(403)


def _latest_reusable_tmp_order_for_operator(user_id: int) -> Order | None:
    return (
        Order.query.join(
            OrderAuditLog,
            (OrderAuditLog.order_id == Order.id)
            & (OrderAuditLog.actor_id == int(user_id))
            & (OrderAuditLog.action == "CREATE_ORDER"),
        )
        .filter(
            Order.assignment_id.is_(None),
            Order.status == OrderStatus.DRAFT.value,
            Order.order_id.ilike("TMP-%"),
        )
        .order_by(Order.id.desc())
        .first()
    )


def _mark_assignment_completed_on_checklist_entry(order: Order):
    if str(getattr(current_user, "role", "")).strip().lower() != Role.OPERATOR.value:
        return
    assignment = getattr(order, "assignment", None)
    if assignment is None:
        return
    if int(getattr(assignment, "operator_id", 0) or 0) != int(current_user.id):
        return
    if str(getattr(assignment, "status", "")).strip().upper() == OrderAssignmentStatus.COMPLETED.value:
        return
    assignment.status = OrderAssignmentStatus.COMPLETED.value
    db.session.commit()


def _render_and_store_plan_pdf(
    order: Order,
    plan_slug: str,
    *,
    display_order_id: str | None = None,
) -> tuple[bytes, Attachment]:
    stats = collect_plan_render_stats(order)
    current_app.logger.info(
        "Plan render stats | plan_slug=%s order_id=%s tshirt=%s trouser=%s accessory=%s missing_image_paths=%s",
        str(plan_slug or "").strip(),
        int(order.id),
        int(stats.get("tshirt_count", 0)),
        int(stats.get("trouser_count", 0)),
        int(stats.get("accessory_count", 0)),
        int(stats.get("missing_image_paths", 0)),
    )
    resolved_display_order_id = str(display_order_id or order.order_id or "").strip()
    pdf_bytes = render_order_pdf(
        order,
        pdf_variant=str(plan_slug or "").strip().lower(),
        display_order_id=resolved_display_order_id,
    )
    attachment = save_plan_pdf(
        order,
        pdf_bytes,
        plan_slug,
        display_order_id=resolved_display_order_id,
    )
    return pdf_bytes, attachment


def _latest_plan_attachment_id(order_id: int, plan_slug: str) -> int | None:
    prefix = f"{str(plan_slug or '').strip().lower()}-"
    if not prefix or prefix == "-":
        return None
    row = (
        Attachment.query.filter_by(order_id=int(order_id), mime_type="application/pdf")
        .filter(Attachment.filename.ilike(f"{prefix}%"))
        .order_by(Attachment.id.desc())
        .first()
    )
    if not row:
        return None
    return int(row.id)


def _resolve_order_creator_emails(order_ids):
    ids = [int(v) for v in (order_ids or []) if v is not None]
    if not ids:
        return {}

    rows = (
        db.session.query(
            OrderAuditLog.order_id,
            OrderAuditLog.action,
            User.email,
        )
        .join(User, User.id == OrderAuditLog.actor_id)
        .filter(OrderAuditLog.order_id.in_(ids), OrderAuditLog.actor_id.isnot(None))
        .order_by(OrderAuditLog.order_id.asc(), OrderAuditLog.created_at.asc(), OrderAuditLog.id.asc())
        .all()
    )

    def _to_username(email_value):
        raw = str(email_value or "").strip().lower()
        if "@" in raw:
            local = raw.split("@", 1)[0].strip()
            return local or "Unknown"
        return raw or "Unknown"

    create_map = {}
    fallback_map = {}
    for order_id, action, email in rows:
        username = _to_username(email)
        if not fallback_map.get(order_id):
            fallback_map[order_id] = username
        if str(action or "").strip().upper() == "CREATE_ORDER" and not create_map.get(order_id):
            create_map[order_id] = username

    resolved = {}
    for oid in ids:
        resolved[oid] = create_map.get(oid) or fallback_map.get(oid) or "Unknown"
    return resolved


def _category_for_size(size_value: str) -> str | None:
    size = str(size_value or "").strip().upper()
    if size in MENS_SIZES:
        return "mens"
    if size in WOMENS_SIZES:
        return "womens"
    if size in YOUTH_SIZES:
        return "youth"
    return None


def _normalize_qc_key_token(value: str) -> str:
    token = "".join(ch if ch.isalnum() else "_" for ch in str(value or "").strip().lower())
    while "__" in token:
        token = token.replace("__", "_")
    return token.strip("_")


def _parse_row_rule(label: str) -> tuple[bool, str]:
    text = str(label or "").strip().lower()
    is_bottom = any(token in text for token in ("trouser", "touser", "pant", "short"))
    sleeve = ""
    if not is_bottom:
        if "full sleeve" in text or "(full)" in text:
            sleeve = "FULL"
        elif "half sleeve" in text or "(half)" in text:
            sleeve = "HALF"
    return is_bottom, sleeve


def _rebuild_garment_quantity_comparison(order: Order, parsed: dict):
    if not isinstance(parsed, dict):
        return
    overview = build_order_overview(order)
    size_sets = {"mens": MENS_SIZES, "womens": WOMENS_SIZES, "youth": YOUTH_SIZES}
    rebuilt_qc: dict[str, dict[str, dict[str, int | str]]] = {}

    for category in ("mens", "womens", "youth"):
        category_overview = overview.get(category, {}) if isinstance(overview, dict) else {}
        product_rows = category_overview.get("rows", []) if isinstance(category_overview, dict) else []
        sizes = size_sets[category]
        for idx, product in enumerate(product_rows):
            if not isinstance(product, dict):
                continue
            label = str(product.get("label") or "").strip()
            overview_sizes = product.get("sizes", {})
            if not label or not isinstance(overview_sizes, dict):
                continue

            is_bottom, sleeve_filter = _parse_row_rule(label)
            packing_totals = {size: 0 for size in sizes}
            for player in order.players or []:
                if is_bottom:
                    player_size = str(getattr(player, "trouser_size", "") or "").strip().upper()
                    player_qty = _safe_int(getattr(player, "trouser_qty", 0))
                    player_category = _category_for_size(player_size)
                else:
                    player_size = str(getattr(player, "tshirt_size", "") or "").strip().upper()
                    player_qty = _safe_int(getattr(player, "tshirt_qty", 0))
                    player_category = _category_for_size(player_size)
                    player_sleeve = str(getattr(player, "sleeve_type", "") or "").strip().upper()
                    if sleeve_filter and player_sleeve != sleeve_filter:
                        continue
                if player_category != category or player_size not in packing_totals:
                    continue
                packing_totals[player_size] += player_qty

            row = {}
            for size in sizes:
                ov = _safe_int(overview_sizes.get(size, 0))
                pk = _safe_int(packing_totals.get(size, 0))
                if ov == 0 and pk == 0:
                    continue
                row[size] = {
                    "overview": ov,
                    "packing": pk,
                    "status": "Match" if ov == pk else "Mismatch",
                }
            if not row:
                continue

            key = f"{category}_{_normalize_qc_key_token(label)}"
            if key in rebuilt_qc:
                key = f"{key}_{idx + 1}"
            rebuilt_qc[key] = row

    parsed["quantity_comparison"] = rebuilt_qc


def _normalize_gender_prefix(gender: str) -> str:
    value = str(gender or "").strip().upper()
    if value == "WOMENS":
        return "womens"
    if value == "YOUTH":
        return "youth"
    return "mens"


def _spec_source_key_candidates(spec: BrandingSpec) -> list[str]:
    product = str(spec.garment_type or "").strip().lower()
    gender = _normalize_gender_prefix(spec.gender)
    candidates: list[str] = []

    def _add(value: str):
        key = str(value or "").strip().lower()
        if key and key not in candidates:
            candidates.append(key)

    if "travel polo" in product or product == "polo":
        _add("travel_polo")
        _add(f"{gender}_travel_polo")
    elif "travel" in product and ("trouser" in product or "touser" in product or "pant" in product):
        _add("travel_trouser")
        _add(f"{gender}_travel_trouser")
        _add("trouser")
        _add(f"{gender}_trouser")
    elif "sleeveless" in product and "jacket" in product:
        _add("sleeveless_jacket")
        _add(f"{gender}_sleeveless_jacket")
    elif "trouser" in product or "pant" in product:
        _add("trouser")
        _add(f"{gender}_trouser")
    elif "short" in product:
        _add("shorts")
        _add(f"{gender}_shorts")
    elif "hoodie" in product and "sweat" not in product:
        _add("hoodie")
        _add(f"{gender}_hoodie")
    elif "sweatshirt" in product:
        _add("sweatshirt")
        _add(f"{gender}_sweatshirt")
    elif "jacket" in product:
        _add("jacket")
        _add(f"{gender}_jacket")
    elif "umpire" in product:
        _add("umpires")
        _add(f"{gender}_umpires")
    elif "helmet" in product and "clad" in product:
        _add("helmet_clad")
        _add(f"{gender}_helmet_clad")
    elif "pad" in product and "clad" in product:
        _add("pad_clad")
        _add(f"{gender}_pad_clad")

    return candidates


def _preferred_cutting_plan_image(spec: BrandingSpec) -> str:
    product = str(getattr(spec, "garment_type", "") or "").strip().lower()
    front_path = str(getattr(spec, "front_image_path", "") or "").strip()
    right_path = str(getattr(spec, "right_image_path", "") or "").strip()

    # Bottom-wear in cutting plan should show right view instead of front view.
    if any(token in product for token in ("trouser", "trousers", "touser", "tousers", "pant", "short")):
        return right_path or front_path
    return front_path


def _attach_front_images_to_cutting_rows(order: Order, cutting_plan_data: dict):
    if not isinstance(cutting_plan_data, dict):
        return
    rows = cutting_plan_data.get("rows")
    if not isinstance(rows, list) or not rows:
        return

    specs = sorted(order.branding_specs or [], key=lambda item: int(getattr(item, "id", 0) or 0))
    if not specs:
        return

    for row in rows:
        if not isinstance(row, dict):
            continue
        source_key = str(row.get("source_product", "") or "").strip().lower()
        if not source_key:
            continue

        style_name = str(row.get("style", "") or "").strip().lower()
        candidate_specs = [
            spec for spec in specs if source_key in _spec_source_key_candidates(spec)
        ]
        if not candidate_specs:
            if "travel" in style_name and "polo" in style_name:
                candidate_specs = [spec for spec in specs if "travel" in str(spec.garment_type or "").lower() and "polo" in str(spec.garment_type or "").lower()]
            elif "travel" in style_name and "trouser" in style_name:
                candidate_specs = [spec for spec in specs if "travel" in str(spec.garment_type or "").lower() and any(tok in str(spec.garment_type or "").lower() for tok in ("trouser", "touser", "pant"))]
            elif "trouser" in style_name:
                candidate_specs = [spec for spec in specs if any(tok in str(spec.garment_type or "").lower() for tok in ("trouser", "touser", "pant"))]
            elif "short" in style_name:
                candidate_specs = [spec for spec in specs if "short" in str(spec.garment_type or "").lower()]
        spec = None
        if candidate_specs:
            want_travel = ("travel" in source_key) or ("travel" in style_name)
            scored = []
            for cand in candidate_specs:
                pname = str(getattr(cand, "garment_type", "") or "").strip().lower()
                is_travel = "travel" in pname
                score = 0
                if want_travel and is_travel:
                    score += 10
                if (not want_travel) and (not is_travel):
                    score += 10
                if "polo" in style_name and "polo" in pname:
                    score += 5
                if "trouser" in style_name and any(tok in pname for tok in ("trouser", "touser", "pant")):
                    score += 5
                if "short" in style_name and "short" in pname:
                    score += 5
                score += int(getattr(cand, "id", 0) or 0) / 10000.0
                scored.append((score, cand))
            scored.sort(key=lambda x: x[0])
            spec = scored[-1][1]
        if not spec:
            continue

        image_path = _preferred_cutting_plan_image(spec)
        if image_path:
            row["front_image_path"] = image_path

        product_display = str(getattr(spec, "garment_type", "") or "").strip()
        style_type_display = str(getattr(spec, "collar_type", "") or "").strip()
        if product_display:
            row["source_product_name"] = product_display
            # Product-specific IRA fabric override:
            # Cricket Whites Pant must always show Heritage White 220.
            if product_display.strip().lower() in {"cricket whites pant", "cricket white pant"}:
                row["ira_fabric_name"] = "Heritage White 220"
        if style_type_display:
            row["style_type_display"] = style_type_display


def _cutting_source_key_from_item(item) -> str:
    product = str(getattr(item, "product_name", "") or "").strip().lower()
    if "travel" in product and any(tok in product for tok in ("trouser", "trousers", "touser", "tousers", "pant")):
        return "travel_trouser"
    if "travel" in product and "polo" in product:
        return "travel_polo"
    if "polo" in product:
        return "travel_polo"
    if "jacket" in product and "sleeveless" in product:
        return "sleeveless_jacket"
    if "jacket" in product:
        return "jacket"
    if "hoodie" in product:
        return "hoodie"
    if "sweatshirt" in product:
        return "sweatshirt"
    if "umpire" in product:
        return "umpires"
    if "helmet" in product and "clad" in product:
        return "helmet_clad"
    if "pad" in product and "clad" in product:
        return "pad_clad"
    if any(tok in product for tok in ("trouser", "trousers", "touser", "tousers", "pant")):
        return "trouser"
    if "short" in product:
        return "shorts"
    return ""


def _empty_cutting_sizes():
    return {
        "XS": 0,
        "S": 0,
        "M": 0,
        "L": 0,
        "XL": 0,
        "2XL": 0,
        "3XL": 0,
        "4XL": 0,
        "YXXS": 0,
        "YXS": 0,
        "YS": 0,
        "YM": 0,
        "YL": 0,
        "YXL": 0,
    }


def _item_sizes_for_cutting(item):
    sizes = _empty_cutting_sizes()
    sizes["XS"] = _safe_int(getattr(item, "qty_xs", 0))
    sizes["S"] = _safe_int(getattr(item, "qty_s", 0))
    sizes["M"] = _safe_int(getattr(item, "qty_m", 0))
    sizes["L"] = _safe_int(getattr(item, "qty_l", 0))
    sizes["XL"] = _safe_int(getattr(item, "qty_xl", 0))
    sizes["2XL"] = _safe_int(getattr(item, "qty_2xl", 0))
    sizes["3XL"] = _safe_int(getattr(item, "qty_3xl", 0))
    sizes["4XL"] = _safe_int(getattr(item, "qty_4xl", 0))
    total = sum(sizes.values())
    return sizes, total


def _augment_cutting_rows_from_order_items(order: Order, cutting_plan_data: dict):
    if not isinstance(cutting_plan_data, dict):
        return
    rows = cutting_plan_data.get("rows")
    if not isinstance(rows, list):
        return
    existing_keys = {str((r or {}).get("source_product", "") or "").strip().lower() for r in rows if isinstance(r, dict)}
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M")
    for item in order.items or []:
        source_key = _cutting_source_key_from_item(item)
        if source_key not in {"travel_polo", "travel_trouser"}:
            continue
        sizes, total = _item_sizes_for_cutting(item)
        if total <= 0:
            continue
        if source_key in existing_keys:
            continue
        spec = getattr(item, "branding_spec", None)
        row = {
            "order_id": order.order_id or "",
            "enquiry_date": order.enquiry_date or "",
            "source_product": source_key,
            "style": "Travel Polo" if source_key == "travel_polo" else "Travel Trousers",
            "fabric": str(getattr(spec, "fabric", "") or ""),
            "ira_fabric_name": "",
            "colour": str(getattr(spec, "panel_color_primary", "") or ""),
            "pattern": str(getattr(spec, "collar_type", "") or ""),
            "sizes": sizes,
            "total": total,
            "cutting_person": "",
            "cut_date": "",
            "created_at": now_text,
            "source_product_name": str(getattr(item, "product_name", "") or ""),
        }
        image_path = _preferred_cutting_plan_image(spec) if spec else ""
        if image_path:
            row["front_image_path"] = image_path
        rows.append(row)
        existing_keys.add(source_key)

    cutting_plan_data["summary"] = {"total_cutting_qty": sum(_safe_int((r or {}).get("total"), 0) for r in rows if isinstance(r, dict))}


def _split_cutting_rows_by_gender(order: Order, cutting_plan_data: dict):
    if not isinstance(cutting_plan_data, dict):
        return
    rows = cutting_plan_data.get("rows")
    if not isinstance(rows, list) or not rows:
        return

    def _gender_prefix(value: str) -> str:
        raw = str(value or "").strip().upper()
        if raw == "WOMENS":
            return "womens"
        if raw == "YOUTH":
            return "youth"
        return "mens"

    def _source_base(source_key: str) -> str:
        key = str(source_key or "").strip().lower()
        for prefix in ("mens_", "womens_", "youth_"):
            if key.startswith(prefix):
                return key[len(prefix):]
        return key

    def _empty_sizes_map():
        return {
            "XS": 0,
            "S": 0,
            "M": 0,
            "L": 0,
            "XL": 0,
            "2XL": 0,
            "3XL": 0,
            "4XL": 0,
            "WXS": 0,
            "WS": 0,
            "WM": 0,
            "WL": 0,
            "WXL": 0,
            "W2XL": 0,
            "W3XL": 0,
            "W4XL": 0,
            "YXXS": 0,
            "YXS": 0,
            "YS": 0,
            "YM": 0,
            "YL": 0,
            "YXL": 0,
        }

    # Aggregate order-item quantities by (base product key, gender).
    item_buckets = {}
    for item in order.items or []:
        base = _cutting_source_key_from_item(item)
        if not base:
            continue
        gender = _gender_prefix(getattr(item, "gender", "MENS"))
        key = (base, gender)
        bucket = item_buckets.setdefault(
            key,
            {
                "sizes": _empty_sizes_map(),
                "total": 0,
                "product_name": str(getattr(item, "product_name", "") or "").strip(),
            },
        )
        if gender == "womens":
            bucket["sizes"]["WXS"] += _safe_int(getattr(item, "qty_xs", 0))
            bucket["sizes"]["WS"] += _safe_int(getattr(item, "qty_s", 0))
            bucket["sizes"]["WM"] += _safe_int(getattr(item, "qty_m", 0))
            bucket["sizes"]["WL"] += _safe_int(getattr(item, "qty_l", 0))
            bucket["sizes"]["WXL"] += _safe_int(getattr(item, "qty_xl", 0))
            bucket["sizes"]["W2XL"] += _safe_int(getattr(item, "qty_2xl", 0))
            bucket["sizes"]["W3XL"] += _safe_int(getattr(item, "qty_3xl", 0))
            bucket["sizes"]["W4XL"] += _safe_int(getattr(item, "qty_4xl", 0))
        elif gender == "youth":
            bucket["sizes"]["YXXS"] += _safe_int(getattr(item, "qty_xs", 0))
            bucket["sizes"]["YXS"] += _safe_int(getattr(item, "qty_s", 0))
            bucket["sizes"]["YS"] += _safe_int(getattr(item, "qty_m", 0))
            bucket["sizes"]["YM"] += _safe_int(getattr(item, "qty_l", 0))
            bucket["sizes"]["YL"] += _safe_int(getattr(item, "qty_xl", 0))
            bucket["sizes"]["YXL"] += _safe_int(getattr(item, "qty_2xl", 0))
        else:
            bucket["sizes"]["XS"] += _safe_int(getattr(item, "qty_xs", 0))
            bucket["sizes"]["S"] += _safe_int(getattr(item, "qty_s", 0))
            bucket["sizes"]["M"] += _safe_int(getattr(item, "qty_m", 0))
            bucket["sizes"]["L"] += _safe_int(getattr(item, "qty_l", 0))
            bucket["sizes"]["XL"] += _safe_int(getattr(item, "qty_xl", 0))
            bucket["sizes"]["2XL"] += _safe_int(getattr(item, "qty_2xl", 0))
            bucket["sizes"]["3XL"] += _safe_int(getattr(item, "qty_3xl", 0))
            bucket["sizes"]["4XL"] += _safe_int(getattr(item, "qty_4xl", 0))
        bucket["total"] = sum(bucket["sizes"].values())

    new_rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_key = str(row.get("source_product", "") or "").strip().lower()
        if not source_key:
            new_rows.append(row)
            continue

        has_gender_prefix = source_key.startswith(("mens_", "womens_", "youth_"))
        base_key = _source_base(source_key)
        matches = []
        for gender in ("mens", "womens", "youth"):
            bucket = item_buckets.get((base_key, gender))
            if not bucket:
                continue
            if _safe_int(bucket.get("total", 0)) <= 0:
                continue
            matches.append((gender, bucket))

        if has_gender_prefix or not matches:
            new_rows.append(row)
            continue

        # Replace unscoped row with one row per gender.
        for gender, bucket in matches:
            cloned = dict(row)
            cloned["source_product"] = f"{gender}_{base_key}"
            cloned["sizes"] = dict(bucket["sizes"])
            cloned["total"] = _safe_int(bucket["total"], 0)
            if bucket.get("product_name"):
                cloned["source_product_name"] = bucket["product_name"]
            new_rows.append(cloned)

    cutting_plan_data["rows"] = new_rows
    cutting_plan_data["summary"] = {
        "total_cutting_qty": sum(
            _safe_int((r or {}).get("total"), 0)
            for r in new_rows
            if isinstance(r, dict)
        )
    }


def _manual_player_products(order: Order):
    seen = set()
    products = []
    for item in order.items:
        product_name = (item.product_name or "").strip()
        if not product_name or product_name in seen:
            continue
        seen.add(product_name)
        gender = (item.gender or "MENS").strip().upper()
        sleeve = (item.sleeve_type or "HALF").strip().upper() or "HALF"
        if sleeve not in {"HALF", "FULL", "3/4 TH"}:
            sleeve = "HALF"
        products.append(
            {
                "value": product_name,
                "label": product_name,
                "gender": gender,
                "sleeve": sleeve,
            }
        )
    if not products:
        products.append(
            {
                "value": "Playing Jersey",
                "label": "Playing Jersey",
                "gender": "MENS",
                "sleeve": "HALF",
            }
        )
    return products


@orders_bp.route("")
@login_required
def list_orders():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()

    query = Order.query
    operator_dashboard = None
    is_operator_only = (
        str(getattr(current_user, "role", "")).strip().lower() == Role.OPERATOR.value
        and not bool(getattr(current_user, "has_admin_panel_access", False))
    )
    if is_operator_only:
        query = query.join(OrderAssignment, Order.assignment_id == OrderAssignment.id).filter(
            OrderAssignment.operator_id == int(current_user.id)
        )
        assignment_rows = (
            OrderAssignment.query.filter(OrderAssignment.operator_id == int(current_user.id))
            .order_by(OrderAssignment.sequence_number.asc(), OrderAssignment.created_at.desc())
            .all()
        )
        total_assigned = len(assignment_rows)
        by_assignment_state = {
            OrderAssignmentStatus.PENDING.value: 0,
            OrderAssignmentStatus.IN_PROGRESS.value: 0,
            OrderAssignmentStatus.COMPLETED.value: 0,
        }
        by_order_state = {
            OrderStatus.DRAFT.value: 0,
            OrderStatus.READY_FOR_APPROVAL.value: 0,
            OrderStatus.APPROVED.value: 0,
            OrderStatus.ARCHIVED.value: 0,
        }
        for row in assignment_rows:
            st = str(row.status or "").strip().upper()
            if st in by_assignment_state:
                by_assignment_state[st] += 1
            linked = getattr(row, "linked_order", None)
            if linked is not None:
                ost = str(getattr(linked, "status", "") or "").strip().upper()
                if ost in by_order_state:
                    by_order_state[ost] += 1

        operator_dashboard = {
            "total_assigned": total_assigned,
            "pending_assignments": by_assignment_state[OrderAssignmentStatus.PENDING.value],
            "in_progress_assignments": by_assignment_state[OrderAssignmentStatus.IN_PROGRESS.value],
            "completed_assignments": by_assignment_state[OrderAssignmentStatus.COMPLETED.value],
            "order_draft": by_order_state[OrderStatus.DRAFT.value],
            "order_ready": by_order_state[OrderStatus.READY_FOR_APPROVAL.value],
            "order_approved": by_order_state[OrderStatus.APPROVED.value],
            "order_archived": by_order_state[OrderStatus.ARCHIVED.value],
            "rows": assignment_rows,
        }
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(Order.order_id.ilike(like), Order.customer_name.ilike(like), Order.mobile.ilike(like))
        )
    if status:
        query = query.filter(Order.status == status)

    orders = query.order_by(Order.created_at.desc()).all()
    creator_map = _resolve_order_creator_emails([o.id for o in orders])
    return render_template(
        "orders/list.html",
        orders=orders,
        q=q,
        status=status,
        creator_map=creator_map,
        operator_dashboard=operator_dashboard,
    )


@orders_bp.route("/new", methods=["GET", "POST"])
@login_required
def create_order():
    form = OrderHeaderForm()
    is_operator = str(getattr(current_user, "role", "")).strip().lower() == Role.OPERATOR.value
    if is_operator and request.method == "GET":
        reusable = _latest_reusable_tmp_order_for_operator(int(current_user.id))
        if reusable is not None:
            return redirect(url_for("orders.edit_order", order_id=reusable.id, step=1))

        has_assignments = (
            OrderAssignment.query.filter(
                OrderAssignment.operator_id == int(current_user.id),
                OrderAssignment.status.in_(
                    [OrderAssignmentStatus.PENDING.value, OrderAssignmentStatus.IN_PROGRESS.value]
                ),
                OrderAssignment.linked_order_id.is_(None),
            ).count()
            > 0
        )
        if not has_assignments:
            flash("No assigned order IDs available for your account.", "danger")
            return redirect(url_for("orders.list_orders"))

        order = Order(
            order_id=f"TMP-{uuid.uuid4().hex[:10].upper()}",
            customer_name="TBD",
        )
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.flush()
        ensure_order_storage(order.id)
        add_audit(order.id, current_user.id, "CREATE_ORDER")
        db.session.commit()
        flash("Order created. Continue with Step 1.", "success")
        return redirect(url_for("orders.edit_order", order_id=order.id, step=1))

    if is_operator and request.method == "POST":
        return redirect(url_for("orders.create_order"))

    if form.validate_on_submit():
        order = Order()
        update_step1(order, form.data)
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.flush()
        ensure_order_storage(order.id)
        add_audit(order.id, current_user.id, "CREATE_ORDER")
        db.session.commit()
        flash(
            "Order created. Continue with step 2.",
            "success",
        )
        return redirect(url_for("orders.edit_order", order_id=order.id, step=2))

    return render_template(
        "orders/new.html",
        form=form,
        is_operator=False,
        operator_assignment_choices=[],
        selected_assignment_id=None,
    )


@orders_bp.route("/<int:order_id>")
@login_required
def order_detail(order_id):
    order = Order.query.get_or_404(order_id)
    _enforce_order_access(order)
    creator_map = _resolve_order_creator_emails([order.id])
    created_by_email = creator_map.get(order.id, "Unknown")
    parsed = get_parsed_json(order.order_check) if order.order_check else {}
    checklist_state = load_checklist_state(order)
    checklist_complete, checklist_missing = checklist_completion_status(order, checklist_state)
    cutting_rows = (normalize_cutting_plan(parsed, order.order_id, order.enquiry_date).get("rows", []) if parsed else [])
    return render_template(
        "orders/detail.html",
        order=order,
        order_overview=build_order_overview(order),
        player_groups=build_player_groups(order),
        packing_groups=build_packing_groups(order),
        checklist_complete=checklist_complete,
        checklist_missing=checklist_missing[:5],
        cutting_rows=cutting_rows,
        created_by_email=created_by_email,
    )


@orders_bp.route("/<int:order_id>/edit", methods=["GET", "POST"])
@login_required
def edit_order(order_id):
    order = Order.query.get_or_404(order_id)
    _enforce_order_access(order)
    bootstrap_order_rows(order)

    step = int(request.args.get("step", 1))
    step = step if step in (1, 2, 3, 4) else 1
    if step >= 3 and not order.items:
        flash("Add at least one product configuration in Step 2 before continuing.", "danger")
        return redirect(url_for("orders.edit_order", order_id=order.id, step=2))

    step1_form = OrderHeaderForm(obj=order)
    step2_form = Step2Form()
    step4_form = Step4ApprovalForm(obj=order)
    checklist_state = load_checklist_state(order)
    flow = checklist_state.get("flow", {}) if isinstance(checklist_state, dict) else {}
    shipping_complete = all(
        str(flow.get(k, "") or "").strip()
        for k in ["shipping_address", "city", "state", "zip_code", "country"]
    )
    checklist_done, checklist_missing = checklist_completion_status(order, checklist_state)
    step4_workflow = {
        "checklist_complete": bool(checklist_done),
        "customer_plan_generated": bool(flow.get("customer_plan_generated", False)),
        "customer_approved": bool(flow.get("customer_approved", False)),
        "shipping_captured": bool(shipping_complete),
        "production_plan_generated": bool(flow.get("production_plan_generated", False)),
        "cutting_plan_unlocked": bool(flow.get("production_plan_generated", False)),
        "missing_count": len(checklist_missing),
    }
    show_assignment_dropdown = (
        str(getattr(current_user, "role", "")).strip().lower() == Role.OPERATOR.value
    )
    assignment_choices = _operator_assignment_choices(order)
    selected_assignment_id = int(order.assignment_id) if order.assignment_id else None
    if show_assignment_dropdown and selected_assignment_id is None and assignment_choices:
        selected_assignment_id = int(assignment_choices[0]["id"])

    # Auto-bind first selectable assignment for newly created operator drafts so TMP order ids
    # do not persist in Step 1 display and downstream flows.
    if (
        show_assignment_dropdown
        and order.assignment_id is None
        and selected_assignment_id is not None
    ):
        auto_assignment = OrderAssignment.query.get(int(selected_assignment_id))
        if (
            auto_assignment is not None
            and int(auto_assignment.operator_id) == int(current_user.id)
            and (auto_assignment.linked_order_id is None or int(auto_assignment.linked_order_id) == int(order.id))
        ):
            order.assignment_id = int(auto_assignment.id)
            order.order_id = str(auto_assignment.order_code or order.order_id or "").strip()
            auto_assignment.linked_order_id = int(order.id)
            if auto_assignment.status != OrderAssignmentStatus.COMPLETED.value:
                auto_assignment.status = OrderAssignmentStatus.IN_PROGRESS.value
            db.session.commit()
            selected_assignment_id = int(auto_assignment.id)

    order_id_display_value = str(order.order_id or "").strip()
    if show_assignment_dropdown:
        selected_choice = next(
            (item for item in assignment_choices if int(item["id"]) == int(selected_assignment_id or 0)),
            None,
        )
        if selected_choice is not None:
            selected_code = str(selected_choice.get("order_code", "") or "").strip()
            if selected_code:
                order_id_display_value = selected_code

    if request.method == "POST":
        posted_step = int(request.form.get("step", step))

        if posted_step == 1:
            if show_assignment_dropdown:
                selected_raw = str(request.form.get("assigned_order_id", "") or "").strip()
                if not selected_raw.isdigit():
                    flash("Select an assigned order ID.", "danger")
                    return redirect(url_for("orders.edit_order", order_id=order.id, step=1))
                selected_assignment = OrderAssignment.query.get(int(selected_raw))
                if selected_assignment is None:
                    flash("Assigned order not found.", "danger")
                    return redirect(url_for("orders.edit_order", order_id=order.id, step=1))
                if int(selected_assignment.operator_id) != int(current_user.id):
                    abort(403)
                if (
                    selected_assignment.linked_order_id
                    and int(selected_assignment.linked_order_id) != int(order.id)
                ):
                    flash("Selected order ID is already linked to another order.", "danger")
                    return redirect(url_for("orders.edit_order", order_id=order.id, step=1))
                if order.assignment_id and int(order.assignment_id) != int(selected_assignment.id):
                    flash("This order already has an assigned order ID.", "danger")
                    return redirect(url_for("orders.edit_order", order_id=order.id, step=1))
                selected_assignment_id = int(selected_assignment.id)
                step1_form.order_id.data = selected_assignment.order_code

            if not step1_form.validate_on_submit():
                flash("Please fill required Step 1 fields.", "danger")
                return redirect(url_for("orders.edit_order", order_id=order.id, step=1))

            old_status = order.status
            update_step1(order, step1_form.data)
            if show_assignment_dropdown and selected_assignment_id:
                selected_assignment = OrderAssignment.query.get(int(selected_assignment_id))
                if selected_assignment is None:
                    flash("Assigned order not found.", "danger")
                    return redirect(url_for("orders.edit_order", order_id=order.id, step=1))
                order.assignment_id = int(selected_assignment.id)
                order.order_id = selected_assignment.order_code
                selected_assignment.linked_order_id = int(order.id)
                if selected_assignment.status != OrderAssignmentStatus.COMPLETED.value:
                    selected_assignment.status = OrderAssignmentStatus.IN_PROGRESS.value
            db.session.commit()
            add_audit(order.id, current_user.id, "UPDATE_STEP1", "status", old_status, order.status)
            db.session.commit()
            flash("Step 1 saved.", "success")
            return redirect(url_for("orders.edit_order", order_id=order.id, step=2))

        if posted_step == 2:
            return redirect(url_for("orders.edit_order", order_id=order.id, step=2))

        if posted_step == 4 and step4_form.validate_on_submit():
            checklist_values = [
                step4_form.checklist_images_verified.data,
                step4_form.checklist_color_variance.data,
                step4_form.checklist_lead_time.data,
                step4_form.checklist_add_on_policy.data,
            ]
            if ("submit_ready" in request.form or "submit_approve" in request.form) and not all(checklist_values):
                flash("Complete all checklist items before moving forward.", "danger")
                return redirect(url_for("orders.edit_order", order_id=order.id, step=4))

            checklist_state = load_checklist_state(order)
            checklist_done, checklist_missing = checklist_completion_status(order, checklist_state)
            if ("submit_ready" in request.form or "submit_approve" in request.form) and not checklist_done:
                flash("Order checklist is incomplete. Finish checklist before approval.", "danger")
                for msg in checklist_missing[:5]:
                    flash(msg, "warning")
                return redirect(url_for("orders.verify_order", order_id=order.id))

            order.approval_notes = step4_form.approval_notes.data
            if "submit_ready" in request.form:
                errors = order_ready_errors(order)
                if errors:
                    for err in errors:
                        flash(err, "danger")
                    return redirect(url_for("orders.edit_order", order_id=order.id, step=4))
                if order.status == OrderStatus.APPROVED.value:
                    flash("Order is already APPROVED. Move back is not allowed.", "info")
                elif order.status == OrderStatus.READY_FOR_APPROVAL.value:
                    flash("Order is already READY_FOR_APPROVAL.", "info")
                else:
                    move_status(order, OrderStatus.READY_FOR_APPROVAL)
                    add_audit(order.id, current_user.id, "SET_READY_FOR_APPROVAL")
                    flash("Order is ready for approval.", "success")
            elif "submit_approve" in request.form:
                if order.status != OrderStatus.READY_FOR_APPROVAL.value:
                    flash("Order must be READY_FOR_APPROVAL before approval.", "danger")
                    return redirect(url_for("orders.edit_order", order_id=order.id, step=4))
                move_status(order, OrderStatus.APPROVED)
                add_audit(order.id, current_user.id, "APPROVE_ORDER")
                flash("Order approved.", "success")
            db.session.commit()
            return redirect(url_for("orders.order_detail", order_id=order.id))

    return render_template(
        "orders/edit.html",
        order=order,
        step=step,
        step1_form=step1_form,
        step2_form=step2_form,
        step4_form=step4_form,
        status_values=[s.value for s in OrderStatus],
        product_catalog=PRODUCT_CATALOG,
        manual_player_products=_manual_player_products(order),
        step4_workflow=step4_workflow,
        show_assignment_dropdown=show_assignment_dropdown,
        assignment_choices=assignment_choices,
        selected_assignment_id=selected_assignment_id,
        order_id_display_value=order_id_display_value,
    )


@orders_bp.route("/<int:order_id>/checklist", methods=["GET", "POST"])
@login_required
@roles_required(Role.ADMIN.value, Role.OPERATOR.value)
def verify_order(order_id):
    order = Order.query.get_or_404(order_id)
    _enforce_order_access(order)
    _mark_assignment_completed_on_checklist_entry(order)
    state = load_checklist_state(order)
    sections = build_checklist_sections(order)
    order_check = get_or_create_order_check(order)
    parsed = get_parsed_json(order_check)
    if not parsed and current_app.config.get("AI_VERIFY_ENABLED", True):
        try:
            parsed = analyze_pdf_bytes_vision(
                _get_checklist_preview_pdf(order),
                model=current_app.config.get("AI_VERIFY_MODEL", "gpt-4o"),
            )
        except Exception as exc:
            parsed = empty_analysis(f"Analysis bootstrap failed: {exc}")
        set_parsed_json(order_check, parsed)
        db.session.commit()
    if isinstance(parsed, dict):
        _rebuild_garment_quantity_comparison(order, parsed)

    dynamic_products = build_dynamic_products(parsed)
    set_dynamic_design_fields(order_check, {"products": dynamic_products})
    dynamic_responses = get_dynamic_responses(order_check)
    garment_cmp = build_garment_cmp(parsed)
    design_pages = []
    for product in dynamic_products:
        try:
            page_no = int(product.get("pdf_page", 0) or 0)
        except (TypeError, ValueError):
            page_no = 0
        if page_no > 0:
            design_pages.append(page_no)
    first_design_page = min(design_pages) if design_pages else 2
    overview_total_pages = max(1, int(first_design_page) - 1)

    flow = state.get("flow", {}) if isinstance(state, dict) else {}
    if not isinstance(flow, dict):
        flow = {}
    flow_defaults = {
        "customer_plan_generated": False,
        "customer_plan_attachment_id": None,
        "customer_approved": False,
        "invoice_receipt_uploaded": False,
        "invoice_receipt_attachment_id": None,
        "invoice_receipt_filename": "",
        "shipping_address": "",
        "city": "",
        "state": "",
        "zip_code": "",
        "country": "",
        "production_plan_generated": False,
        "production_plan_attachment_id": None,
    }
    merged_flow = dict(flow_defaults)
    merged_flow.update(flow)
    flow = merged_flow

    try:
        overview_pdf_page = int((state or {}).get("overview_pdf_page", 1) or 1)
    except (TypeError, ValueError, AttributeError):
        overview_pdf_page = 1
    overview_pdf_page = max(1, min(int(overview_total_pages), int(overview_pdf_page)))

    def _dyn_key(product_key: str, field: str) -> str:
        token = (
            str(field)
            .strip()
            .lower()
            .replace(" ", "_")
            .replace("-", "_")
            .replace("/", "_")
        )
        ptoken = str(product_key or "").strip().lower().replace(" ", "_")
        return f"dyn_{ptoken}_{token}"

    def _dynamic_field_visible(product: dict, field: str) -> bool:
        values = product.get("values", {}) if isinstance(product, dict) else {}
        if not isinstance(values, dict):
            values = {}
        token = (
            str(field)
            .strip()
            .lower()
            .replace(" ", "_")
            .replace("-", "_")
            .replace("/", "_")
        )
        raw_value = values.get(field, values.get(token, ""))
        lowered = str(raw_value or "").strip().lower()
        return lowered not in {"none", "null"}

    def _dynamic_missing_list():
        missing = []
        for product in dynamic_products:
            key = str(product.get("key") or product.get("name") or "").strip()
            for field in product.get("fields", []):
                if not _dynamic_field_visible(product, field):
                    continue
                check_key = _dyn_key(key, field)
                if not dynamic_responses.get(check_key, False):
                    missing.append(f"Verify {product.get('name')}: {field}")
        return missing

    def _quantity_comparison_pass() -> bool:
        if not isinstance(parsed, dict):
            return False
        qc = parsed.get("quantity_comparison")
        if not isinstance(qc, dict) or not qc:
            return False
        for product_key, product_data in qc.items():
            # Accessories comparison is not part of garment quantity gating here.
            if str(product_key or "").strip().lower() == "accessories":
                continue
            if not isinstance(product_data, dict):
                continue
            for _size, comp in product_data.items():
                if not isinstance(comp, dict):
                    continue
                status_txt = str(comp.get("status", "")).strip().lower()
                overview = int(comp.get("overview", 0) or 0)
                packing = int(comp.get("packing", 0) or 0)
                matched = status_txt == "match" or overview == packing
                if not matched:
                    return False
        return True

    def _shipping_complete() -> bool:
        return all(
            str(flow.get(key, "") or "").strip()
            for key in ["shipping_address", "city", "state", "zip_code", "country"]
        )

    def _invoice_receipt_uploaded() -> bool:
        return bool(flow.get("invoice_receipt_uploaded", False))

    def _invoice_receipt_is_required() -> bool:
        return bool(current_app.config.get("INVOICE_RECEIPT_REQUIRED", False))

    if request.method == "POST":
        if "overview_pdf_page" in request.form:
            try:
                overview_pdf_page = int(request.form.get("overview_pdf_page", "1") or 1)
            except (TypeError, ValueError):
                overview_pdf_page = 1
            overview_pdf_page = max(1, min(int(overview_total_pages), int(overview_pdf_page)))
        state["overview_pdf_page"] = overview_pdf_page

        if "current_page" in request.form:
            try:
                current_page = max(1, int(request.form.get("current_page", "1")))
                state["current_page"] = current_page
                order_check.current_page = current_page
            except ValueError:
                state["current_page"] = 1
                order_check.current_page = 1

        is_customer_verify_action = "set_customer_verified" in request.form
        is_invoice_receipt_action = "set_invoice_receipt" in request.form
        show_invoice_dialog_after_redirect = False
        responses = {}
        merged_responses = {}
        if is_customer_verify_action or is_invoice_receipt_action:
            # Modal submit does not include checklist checkboxes; preserve existing checks.
            merged_responses = dict(dynamic_responses or {})
            core_order_verified = bool(
                merged_responses.get("core.order_id_verified", getattr(order_check, "order_id_verified", False))
            )
            core_enquiry_verified = bool(
                merged_responses.get("core.enquiry_date_verified", getattr(order_check, "enquiry_date_verified", False))
            )
            responses["core.order_id_verified"] = core_order_verified
            responses["core.enquiry_date_verified"] = core_enquiry_verified
            order_check.order_id_verified = core_order_verified
            order_check.enquiry_date_verified = core_enquiry_verified
            for key in [
                "design_checked",
                "logos_checked",
                "gender_checked",
                "sleeve_type_checked",
                "names_numbers_sizes_checked",
                "quantity_checked",
            ]:
                checked = bool(
                    merged_responses.get(
                        f"final.{key}",
                        getattr(order_check, key, False),
                    )
                )
                responses[f"final.{key}"] = checked
                setattr(order_check, key, checked)
        else:
            core_order_verified = "core.order_id_verified" in request.form or "order_id_verified" in request.form
            core_enquiry_verified = (
                "core.enquiry_date_verified" in request.form or "enquiry_date_verified" in request.form
            )
            responses["core.order_id_verified"] = core_order_verified
            responses["core.enquiry_date_verified"] = core_enquiry_verified
            order_check.order_id_verified = core_order_verified
            order_check.enquiry_date_verified = core_enquiry_verified

            posted_dynamic = {}
            for product in dynamic_products:
                key = str(product.get("key") or product.get("name") or "").strip()
                for field in product.get("fields", []):
                    if not _dynamic_field_visible(product, field):
                        continue
                    dyn_key = _dyn_key(key, field)
                    posted_dynamic[dyn_key] = dyn_key in request.form

            for key in [
                "design_checked",
                "logos_checked",
                "gender_checked",
                "sleeve_type_checked",
                "names_numbers_sizes_checked",
                "quantity_checked",
            ]:
                checked = f"final.{key}" in request.form or key in request.form
                responses[f"final.{key}"] = checked
                setattr(order_check, key, checked)

            merged_responses = dict(responses)
            merged_responses.update(posted_dynamic)

        if "set_customer_verified" in request.form:
            verify_choice = str(request.form.get("customer_verified", "") or "").strip().lower()
            if not bool(flow.get("customer_plan_generated", False)):
                flash("Generate customer plan first.", "danger")
            elif verify_choice not in {"yes", "no"}:
                flash("Please choose customer verified or not verified.", "danger")
            else:
                is_verified = verify_choice == "yes"
                flow["customer_approved"] = is_verified
                if not is_verified:
                    flow["production_plan_generated"] = False
                if is_verified:
                    show_invoice_dialog_after_redirect = True
                else:
                    flow["invoice_receipt_uploaded"] = False
                    flow["invoice_receipt_attachment_id"] = None
                    flow["invoice_receipt_filename"] = ""
                state["flow"] = flow
        elif "set_invoice_receipt" in request.form:
            if not bool(flow.get("customer_plan_generated", False)):
                flash("Generate customer plan first.", "danger")
            elif not bool(flow.get("customer_approved", False)):
                flash("Customer verification is required before invoice receipt.", "danger")
            elif "skip_invoice_receipt" in request.form:
                flash("Invoice receipt skipped for now.", "info")
            elif "upload_invoice_receipt" in request.form:
                receipt_file = request.files.get("invoice_receipt")
                if not receipt_file or not str(receipt_file.filename or "").strip():
                    flash("Please choose an invoice receipt file to upload.", "danger")
                    show_invoice_dialog_after_redirect = True
                else:
                    original_name = str(receipt_file.filename or "").strip()
                    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
                    if ext not in INVOICE_RECEIPT_ALLOWED_EXTENSIONS:
                        flash("Invoice receipt must be PDF, PNG, JPG, or JPEG.", "danger")
                        show_invoice_dialog_after_redirect = True
                    else:
                        safe_original = secure_filename(original_name) or f"invoice-receipt.{ext}"
                        filename = f"invoice-receipt-{uuid.uuid4().hex[:10]}-{safe_original}"
                        storage_path = save_order_file(
                            order.id,
                            ORDER_DOCUMENT_SECTION,
                            filename,
                            receipt_file.read(),
                            content_type=receipt_file.content_type or "application/octet-stream",
                        )
                        attachment = Attachment(
                            order_id=order.id,
                            filename=filename,
                            mime_type=receipt_file.content_type or "application/octet-stream",
                            storage_path=storage_path,
                        )
                        db.session.add(attachment)
                        db.session.flush()
                        flow["invoice_receipt_uploaded"] = True
                        flow["invoice_receipt_attachment_id"] = int(attachment.id)
                        flow["invoice_receipt_filename"] = filename
                        flash("Invoice receipt uploaded.", "success")
            else:
                show_invoice_dialog_after_redirect = True
        else:
            flow["customer_approved"] = bool(flow.get("customer_approved", False))
        for key in ["shipping_address", "city", "state", "zip_code", "country"]:
            flow[key] = str(request.form.get(key, flow.get(key, "")) or "").strip()
        state["flow"] = flow

        state["responses"] = responses
        set_dynamic_responses(order_check, merged_responses)
        dynamic_responses = merged_responses
        save_checklist_state(order, state)
        db.session.commit()

        if is_customer_verify_action and bool(flow.get("customer_approved", False)):
            return redirect(url_for("orders.verify_order", order_id=order.id, show_invoice_dialog=1))
        if is_invoice_receipt_action and show_invoice_dialog_after_redirect:
            return redirect(url_for("orders.verify_order", order_id=order.id, show_invoice_dialog=1))

        if "mark_ready" in request.form:
            done, missing = checklist_completion_status(order, state)
            dyn_missing = _dynamic_missing_list()
            final_ready = order_check.is_final_ready()
            qty_pass = _quantity_comparison_pass()
            prod_plan_done = bool(flow.get("production_plan_generated", False))
            if not done or dyn_missing or not final_ready or not qty_pass or not prod_plan_done:
                flash("Checklist is incomplete. Please complete all checks.", "danger")
                for msg in missing[:8]:
                    flash(msg, "warning")
                for msg in dyn_missing[:8]:
                    flash(msg, "warning")
                if not qty_pass:
                    flash("Quantity comparison has mismatches. Resolve before marking ready.", "warning")
                if not prod_plan_done:
                    flash("Generate production plan before marking ready for approval.", "warning")
            else:
                if order.status == OrderStatus.DRAFT.value:
                    move_status(order, OrderStatus.READY_FOR_APPROVAL)
                    add_audit(order.id, current_user.id, "SET_READY_FOR_APPROVAL_BY_CHECKLIST")
                    db.session.commit()
                    flash("Checklist complete. Order is READY_FOR_APPROVAL.", "success")
                else:
                    flash("Checklist saved.", "success")
                return redirect(url_for("orders.order_detail", order_id=order.id))
        elif "generate_customer_plan" in request.form:
            done, _missing = checklist_completion_status(order, state)
            dyn_missing = _dynamic_missing_list()
            final_ready = order_check.is_final_ready()
            qty_pass = _quantity_comparison_pass()
            if not done or dyn_missing or not final_ready or not qty_pass:
                flash("Complete checklist and quantity comparison before generating customer plan.", "danger")
            else:
                customer_pdf, attachment = _render_and_store_plan_pdf(
                    order,
                    "customer-plan",
                    display_order_id=str(order.order_id or ""),
                )
                db.session.add(attachment)
                db.session.flush()
                flow["customer_plan_generated"] = True
                flow["customer_plan_attachment_id"] = attachment.id
                state["flow"] = flow
                save_checklist_state(order, state)
                db.session.commit()
                flash("Customer plan generated. Verify customer response to continue.", "success")
                return redirect(
                    url_for(
                        "orders.verify_order",
                        order_id=order.id,
                        show_customer_dialog=1,
                        download_customer_plan=1,
                    )
                )
        elif "generate_production_plan" in request.form:
            if not bool(flow.get("customer_plan_generated", False)):
                flash("Generate customer plan first.", "danger")
            elif not bool(flow.get("customer_approved", False)):
                flash("Customer approval is required before production plan.", "danger")
            elif _invoice_receipt_is_required() and not _invoice_receipt_uploaded():
                flash("Upload invoice receipt before generating production plan.", "danger")
            elif not _shipping_complete():
                flash("Fill shipping address, city, state, zip code and country.", "danger")
            else:
                # Persist shipping details onto the order, then generate production plan PDF.
                order.shipping_address = str(flow.get("shipping_address", "") or "").strip()
                order.city = str(flow.get("city", "") or "").strip()
                order.state = str(flow.get("state", "") or "").strip()
                order.zip_code = str(flow.get("zip_code", "") or "").strip()
                order.country = str(flow.get("country", "") or "").strip()
                production_order_id = get_or_assign_ira_order_id(order)

                production_pdf, attachment = _render_and_store_plan_pdf(
                    order,
                    "production-plan",
                    display_order_id=production_order_id,
                )
                db.session.add(attachment)
                db.session.flush()

                flow["production_plan_generated"] = True
                flow["production_plan_attachment_id"] = attachment.id
                state["flow"] = flow
                save_checklist_state(order, state)
                db.session.commit()

                try:
                    ingest_production_plan_for_order(
                        order_id=int(order.id),
                        order_number=str(order.production_order_id or order.order_id or order.id),
                        pdf_bytes=production_pdf,
                    )
                except Exception as exc:
                    current_app.logger.exception("Pricing auto-ingestion failed: %s", exc)
                    flash(
                        "Production plan generated, but pricing auto-ingestion failed. Check logs.",
                        "warning",
                    )

                return send_file(
                    BytesIO(production_pdf),
                    mimetype="application/pdf",
                    as_attachment=True,
                    download_name=attachment.filename,
                )
        else:
            flash("Checklist saved.", "success")

        return redirect(url_for("orders.verify_order", order_id=order.id))

    checklist_done, checklist_missing = checklist_completion_status(order, state)
    dynamic_missing = _dynamic_missing_list()
    checklist_done = checklist_done and order_check.is_final_ready() and not dynamic_missing
    checklist_missing = checklist_missing + dynamic_missing
    invoice_receipt_attachment_id = flow.get("invoice_receipt_attachment_id")
    invoice_receipt_attachment = None
    if invoice_receipt_attachment_id:
        try:
            invoice_receipt_attachment = Attachment.query.filter_by(
                id=int(invoice_receipt_attachment_id),
                order_id=int(order.id),
            ).first()
        except (TypeError, ValueError):
            invoice_receipt_attachment = None
    shipping_complete = _shipping_complete()
    cutting_plan_unlocked = bool(flow.get("production_plan_generated", False))
    production_plan_enabled = (
        bool(flow.get("customer_plan_generated", False))
        and bool(flow.get("customer_approved", False))
        and shipping_complete
    )
    preview_image_supported = fitz is not None
    preview_page_count = 1
    latest_customer_plan_attachment_id = _latest_plan_attachment_id(order.id, "customer-plan")
    return render_template(
        "orders/verify_order.html",
        order=order,
        sections=sections,
        checklist_state=state,
        checklist_done=checklist_done,
        checklist_missing=checklist_missing,
        preview_image_supported=preview_image_supported,
        preview_page_count=preview_page_count,
        order_check=order_check,
        overview_total_pages=overview_total_pages,
        overview_pdf_page=overview_pdf_page,
        dynamic_responses=dynamic_responses,
        dynamic_products=dynamic_products,
        garment_cmp=garment_cmp,
        parsed=parsed,
        presigned_url=url_for("orders.preview_pdf", order_id=order.id),
        flow=flow,
        invoice_receipt_uploaded=bool(flow.get("invoice_receipt_uploaded", False)),
        invoice_receipt_attachment=invoice_receipt_attachment,
        shipping_complete=shipping_complete,
        production_plan_enabled=production_plan_enabled,
        cutting_plan_unlocked=cutting_plan_unlocked,
        show_customer_dialog=(request.args.get("show_customer_dialog", "0") == "1"),
        show_invoice_dialog=(request.args.get("show_invoice_dialog", "0") == "1"),
        download_customer_plan=(request.args.get("download_customer_plan", "0") == "1"),
        latest_customer_plan_attachment_id=latest_customer_plan_attachment_id,
    )


@orders_bp.route("/<int:order_id>/analysis/run", methods=["POST"])
@login_required
@roles_required(Role.ADMIN.value, Role.OPERATOR.value)
def run_analysis(order_id):
    order = Order.query.get_or_404(order_id)
    _enforce_order_access(order)
    order_check = get_or_create_order_check(order)
    try:
        parsed = analyze_pdf_bytes_vision(
            _get_checklist_preview_pdf(order, force_refresh=True),
            model=current_app.config.get("AI_VERIFY_MODEL", "gpt-4o"),
        )
        set_parsed_json(order_check, parsed)
        set_dynamic_design_fields(order_check, {"products": build_dynamic_products(parsed)})
        db.session.commit()
        flash("AI analysis refreshed.", "success")
    except Exception as exc:
        flash(f"AI analysis failed: {exc}", "danger")
    return redirect(url_for("orders.verify_order", order_id=order.id))


@orders_bp.route("/<int:order_id>/cutting-plan")
@login_required
@roles_required(Role.ADMIN.value, Role.OPERATOR.value)
def cutting_plan(order_id):
    order = Order.query.get_or_404(order_id)
    _enforce_order_access(order)
    state = load_checklist_state(order)
    flow = state.get("flow", {}) if isinstance(state, dict) else {}
    if not bool((flow or {}).get("production_plan_generated", False)):
        flash("Generate production plan from checklist before opening cutting plan.", "warning")
        return redirect(url_for("orders.verify_order", order_id=order.id))
    parsed = get_parsed_json(order.order_check) if order.order_check else {}
    cutting_plan_data = normalize_cutting_plan(parsed, order.order_id, order.enquiry_date)
    _augment_cutting_rows_from_order_items(order, cutting_plan_data)
    _split_cutting_rows_by_gender(order, cutting_plan_data)
    _attach_front_images_to_cutting_rows(order, cutting_plan_data)
    rows = cutting_plan_data.get("rows", [])
    if not rows:
        flash("No cutting plan rows available yet. Add product configurations and roster first.", "warning")
    return render_template(
        "orders/cutting_plan.html",
        order=order,
        cutting_rows=rows,
        cutting_plan=cutting_plan_data,
        has_cutting_plan=has_cutting_plan_rows(cutting_plan_data),
    )


@orders_bp.route("/<int:order_id>/cutting-plan.pdf")
@login_required
@roles_required(Role.ADMIN.value, Role.OPERATOR.value)
def cutting_plan_pdf(order_id):
    order = Order.query.get_or_404(order_id)
    _enforce_order_access(order)
    state = load_checklist_state(order)
    flow = state.get("flow", {}) if isinstance(state, dict) else {}
    if not bool((flow or {}).get("production_plan_generated", False)):
        flash("Generate production plan from checklist before downloading cutting plan.", "warning")
        return redirect(url_for("orders.verify_order", order_id=order.id))

    parsed = get_parsed_json(order.order_check) if order.order_check else {}
    cutting_plan_data = normalize_cutting_plan(parsed, order.order_id, order.enquiry_date)
    _augment_cutting_rows_from_order_items(order, cutting_plan_data)
    _split_cutting_rows_by_gender(order, cutting_plan_data)
    _attach_front_images_to_cutting_rows(order, cutting_plan_data)
    if not has_cutting_plan_rows(cutting_plan_data):
        flash("Cutting plan is not available for this order.", "warning")
        return redirect(url_for("orders.cutting_plan", order_id=order.id))

    pdf_io = to_pdf_io(cutting_plan_data)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    filename = f"{order.order_id}_cutting_plan_{timestamp}.pdf"
    response = send_file(
        pdf_io,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@orders_bp.route("/<int:order_id>/preview-pdf")
@login_required
def preview_pdf(order_id):
    order = Order.query.get_or_404(order_id)
    _enforce_order_access(order)
    pdf_bytes = _get_checklist_preview_pdf(order)
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"{order.order_id}-preview.pdf",
    )


@orders_bp.route("/<int:order_id>/preview-page/<int:page_no>.png")
@login_required
def preview_page_png(order_id, page_no):
    if fitz is None:
        abort(404)
    order = Order.query.get_or_404(order_id)
    _enforce_order_access(order)
    pdf_bytes = _get_checklist_preview_pdf(order)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    if doc.page_count < 1:
        doc.close()
        abort(404)

    page_index = max(0, min(page_no - 1, doc.page_count - 1))
    page = doc.load_page(page_index)
    pix = page.get_pixmap(matrix=fitz.Matrix(2.1, 2.1), alpha=False)
    img = pix.tobytes("png")
    doc.close()
    return send_file(BytesIO(img), mimetype="image/png")


@orders_bp.route("/<int:order_id>/media")
@login_required
def order_media(order_id):
    order = Order.query.get_or_404(order_id)
    _enforce_order_access(order)
    path_value = (request.args.get("path") or "").strip()
    if not path_value:
        abort(404)

    attachment = (
        Attachment.query.filter_by(order_id=order.id, storage_path=path_value)
        .order_by(Attachment.id.desc())
        .first()
    )
    if attachment:
        try:
            return send_file(
                BytesIO(read_bytes(attachment.storage_path)),
                mimetype=attachment.mime_type or "application/octet-stream",
                as_attachment=False,
                download_name=attachment.filename or "media",
            )
        except Exception:
            current_app.logger.warning("Failed to stream order media attachment: %s", attachment.storage_path)

    candidate = Path(path_value)
    if not candidate.exists():
        abort(404)

    base_dir = (Path(current_app.config["UPLOAD_DIR"]) / str(order.id)).resolve()
    resolved = candidate.resolve()
    if str(resolved).startswith(str(base_dir)):
        return send_file(str(resolved))
    abort(404)


@orders_bp.route("/<int:order_id>/configs/add", methods=["POST"])
@login_required
def add_config(order_id):
    order = Order.query.get_or_404(order_id)
    _enforce_order_access(order)
    try:
        add_product_configuration(order, request.form, request.files)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("orders.edit_order", order_id=order.id, step=2))

    add_audit(order.id, current_user.id, "ADD_PRODUCT_CONFIG")
    db.session.commit()
    flash("Product configuration added.", "success")
    return redirect(url_for("orders.edit_order", order_id=order.id, step=2))


@orders_bp.route("/<int:order_id>/configs/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_config(order_id, item_id):
    order = Order.query.get_or_404(order_id)
    _enforce_order_access(order)
    deleted = delete_product_configuration(order, item_id)
    if not deleted:
        flash("Configuration not found.", "warning")
    else:
        add_audit(order.id, current_user.id, "DELETE_PRODUCT_CONFIG")
        db.session.commit()
        flash("Product configuration deleted.", "info")
    return redirect(url_for("orders.edit_order", order_id=order.id, step=2))


@orders_bp.route("/<int:order_id>/approve", methods=["POST"])
@login_required
@roles_required(Role.ADMIN.value)
def approve_order(order_id):
    order = Order.query.get_or_404(order_id)
    if order.status != OrderStatus.READY_FOR_APPROVAL.value:
        flash("Order must be READY_FOR_APPROVAL before approval.", "danger")
        return redirect(url_for("orders.order_detail", order_id=order.id))

    move_status(order, OrderStatus.APPROVED)
    add_audit(order.id, current_user.id, "APPROVE_ORDER")
    db.session.commit()
    flash("Order approved.", "success")
    return redirect(url_for("orders.order_detail", order_id=order.id))


@orders_bp.route("/<int:order_id>/delete", methods=["POST"])
@login_required
@roles_required(Role.ADMIN.value)
def delete_order(order_id):
    order = Order.query.get_or_404(order_id)
    provided_pin = str(request.form.get("delete_pin", "") or "")
    expected_pin = str(current_app.config.get("ORDER_DELETE_PIN", "2019") or "")
    if not provided_pin or provided_pin != expected_pin:
        flash("Invalid delete PIN. Order not deleted.", "danger")
        return redirect(url_for("orders.list_orders"))

    order_label = order.order_id or str(order.id)
    assignments = OrderAssignment.query.filter(
        or_(
            OrderAssignment.id == order.assignment_id,
            OrderAssignment.linked_order_id == int(order.id),
        )
    ).all()
    for assignment in assignments:
        assignment.linked_order_id = None
        if str(getattr(assignment, "status", "")).strip().upper() != OrderAssignmentStatus.COMPLETED.value:
            assignment.status = OrderAssignmentStatus.PENDING.value
    order.assignment_id = None

    try:
        db.session.flush()
        db.session.delete(order)
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.exception("Failed to delete order %s: %s", order.id, exc)
        flash("Could not delete the order due to a database error.", "danger")
        return redirect(url_for("orders.list_orders"))

    try:
        delete_order_storage(order.id)
    except Exception:
        current_app.logger.warning("Could not fully clean deleted order storage: %s", order.id)

    flash(f"Order {order_label} deleted.", "success")
    return redirect(url_for("orders.list_orders"))

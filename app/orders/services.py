import re
import uuid
from typing import Optional

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import Attachment, BrandingSpec, Order, OrderItem, OrderStatus
from app.storage import ORDER_IMAGE_SECTION, save_order_file

PRODUCT_CATALOG = [
    "Playing Jersey",
    "Training Jersey",
    "Trousers",
    "Shorts",
    "Jackets",
    "Cap",
    "Baggy Cap",
    "Hat",
    "Helmat Clad",
    "Pad Clad",
    "Polo",
    "Travel Tousers",
    "Hoodies",
    "Sleeveless Jackets",
    "Sleeveless Singlet",
    "Cricket Whites Tshirt",
    "Cricket Whites Pant",
]

TYPE_PRODUCTS = {
    "cricket whites pant",
}

TYPELESS_PRODUCTS = {
    "trousers",
    "shorts",
    "travel tousers",
    "travel trousers",
    "jackets",
    "jacket",
    "cap",
    "baggy cap",
    "hat",
    "sleeveless jackets",
    "helmat clad",
    "helmet clad",
    "pad clad",
    "sleeveless",
    "sleeveless singlet",
    "sleeve singlet",
}

ADULT_YOUTH_GENDER_PRODUCTS = {
    "cap",
    "baggy cap",
    "hat",
    "helmat clad",
    "helmet clad",
    "pad clad",
}


def bootstrap_order_rows(order: Order):
    return


def update_step1(order: Order, data: dict):
    fields = [
        "order_id",
        "enquiry_date",
        "submission_id",
        "confirmed_on",
        "customer_name",
        "mobile",
        "shipping_address",
        "city",
        "zip_code",
        "state",
        "country",
    ]
    for field in fields:
        setattr(order, field, data.get(field))


def add_product_configuration(order: Order, form_data, files):
    product_name = (form_data.get("product_name") or "").strip()
    gender = (form_data.get("gender") or "MENS").strip().upper()
    sleeve_type = _normalize_sleeve_type(form_data.get("sleeve_type"), product_name)
    sleeve_tag = _key(sleeve_type or "na")
    base_tag = f"{_key(product_name)}-{gender}-sleeve-{sleeve_tag}"

    if not product_name:
        raise ValueError("Product is required")

    logo_positions = form_data.getlist("logo_positions")
    logo_slots = [str(v).strip() for v in form_data.getlist("logo_slot") if str(v).strip()]
    apply_same_design = _is_truthy(form_data.get("apply_same_design_all_genders"))
    target_genders = [g.strip().upper() for g in form_data.getlist("apply_gender_targets") if g]
    if apply_same_design:
        allowed = _allowed_genders_for_product(product_name)
        target_genders = [g for g in target_genders if g in allowed]
        if not target_genders:
            target_genders = list(allowed)
    else:
        target_genders = [gender]

    apply_same_sleeves = _is_truthy(form_data.get("apply_same_design_all_sleeves"))
    target_sleeves = [sleeve_type]
    if _type_mode_for_product(product_name) == "sleeve" and apply_same_sleeves:
        selected_sleeves = [
            _normalize_sleeve_type(s, product_name)
            for s in form_data.getlist("apply_sleeve_targets")
            if str(s or "").strip()
        ]
        allowed = {"HALF", "FULL", "3/4 TH"}
        target_sleeves = [s for s in selected_sleeves if s in allowed]
        if not target_sleeves:
            target_sleeves = ["HALF", "FULL", "3/4 TH"]
    elif _type_mode_for_product(product_name) == "sleeve":
        target_sleeves = [sleeve_type or "HALF"]
    else:
        target_sleeves = [sleeve_type]

    shared_paths = {
        "front_image_path": _save_file(order, files.get("front_image"), f"{base_tag}-front"),
        "right_image_path": _save_file(order, files.get("right_image"), f"{base_tag}-right"),
        "back_image_path": _save_file(order, files.get("back_image"), f"{base_tag}-back"),
        "left_image_path": _save_file(order, files.get("left_image"), f"{base_tag}-left"),
        "logo_right_path": None,
        "logo_left_path": None,
        "logo_front_path": None,
        "logo_back_path": None,
        "logo_slots": logo_slots,
        "logo_slot_paths": {},
    }

    for slot in logo_slots:
        file_key = f"logo_file_{slot}"
        saved_path = _save_file(order, files.get(file_key), f"{base_tag}-logo-{slot}")
        if saved_path:
            shared_paths["logo_slot_paths"][slot] = saved_path

    if "right" in logo_positions:
        shared_paths["logo_right_path"] = _save_file(
            order, files.get("logo_right"), f"{base_tag}-logo-right"
        )
    if "left" in logo_positions:
        shared_paths["logo_left_path"] = _save_file(
            order, files.get("logo_left"), f"{base_tag}-logo-left"
        )

    for target_gender in target_genders:
        for target_sleeve in target_sleeves:
            _upsert_product_configuration(
                order=order,
                product_name=product_name,
                gender=target_gender,
                sleeve_type=target_sleeve,
                form_data=form_data,
                logo_positions=logo_positions,
                shared_paths=shared_paths,
            )


def _upsert_product_configuration(
    order: Order,
    product_name: str,
    gender: str,
    sleeve_type: str,
    form_data,
    logo_positions,
    shared_paths,
):
    item = (
        OrderItem.query.filter_by(
            order_id=order.id,
            product_name=product_name,
            gender=gender,
            sleeve_type=sleeve_type,
        )
        .order_by(OrderItem.id.desc())
        .first()
    )
    if item is None:
        item = OrderItem(
            order_id=order.id,
            product_name=product_name,
            gender=gender,
            sleeve_type=sleeve_type,
            total=0,
        )
        db.session.add(item)
        db.session.flush()

    spec = item.branding_spec
    if spec is None:
        spec = BrandingSpec(order_id=order.id, order_item_id=item.id)
        db.session.add(spec)

    spec.garment_type = product_name
    spec.gender = gender
    spec.sleeve_type = sleeve_type
    spec.style_number = form_data.get("style_number")
    spec.collar_type = form_data.get("style_type") or form_data.get("collar_type")
    spec.fabric = form_data.get("fabric")
    spec.panel_color_primary = form_data.get("panel_color_primary")
    spec.panel_color_secondary = form_data.get("panel_color_secondary")
    spec.design_notes = form_data.get("design_notes")
    spec.production_notes = form_data.get("production_notes")
    all_logo_positions = sorted(set((logo_positions or []) + (shared_paths.get("logo_slots") or [])))
    spec.logo_positions = ",".join(all_logo_positions) if all_logo_positions else ""
    spec.front_image_path = shared_paths.get("front_image_path")
    spec.right_image_path = shared_paths.get("right_image_path")
    spec.back_image_path = shared_paths.get("back_image_path")
    spec.left_image_path = shared_paths.get("left_image_path")
    slot_paths = shared_paths.get("logo_slot_paths") or {}

    def _first_slot_path(prefix: str):
        for slot_key, slot_path in slot_paths.items():
            if str(slot_key).startswith(prefix):
                return slot_path
        return None

    front_logo_path = _first_slot_path("front_")
    back_logo_path = _first_slot_path("back_")
    right_logo_path = _first_slot_path("right_")
    left_logo_path = _first_slot_path("left_")

    # Slot-aware fallback mapping for front-based side/chest/sleeve logos.
    if not right_logo_path:
        for slot_key, slot_path in slot_paths.items():
            slot = str(slot_key).lower()
            if any(tag in slot for tag in ("right_side", "right_chest", "right_sleeve")):
                right_logo_path = slot_path
                break
    if not left_logo_path:
        for slot_key, slot_path in slot_paths.items():
            slot = str(slot_key).lower()
            if any(tag in slot for tag in ("left_side", "left_chest", "left_sleeve")):
                left_logo_path = slot_path
                break

    spec.logo_front_path = front_logo_path
    spec.logo_back_path = back_logo_path
    spec.logo_right_path = right_logo_path or shared_paths.get("logo_right_path")
    spec.logo_left_path = left_logo_path or shared_paths.get("logo_left_path")


def _is_truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def delete_product_configuration(order: Order, item_id: int):
    item = OrderItem.query.filter_by(id=item_id, order_id=order.id).first()
    if not item:
        return False
    db.session.delete(item)
    return True


def order_ready_errors(order: Order):
    errors = []
    if not order.customer_name:
        errors.append("Customer name is required.")

    if not order.items:
        errors.append("Add at least one product configuration in Step 2.")

    if not order.players:
        errors.append("At least one player row is required.")

    return errors


def move_status(order: Order, next_status: OrderStatus):
    if not order.can_transition_to(next_status):
        raise ValueError(f"Invalid status transition from {order.status} to {next_status.value}")
    order.status = next_status.value


def _key(text: str):
    return "".join(c.lower() if c.isalnum() else "_" for c in text).strip("_")


def _normalize_sleeve_type(value, product_name: str):
    raw = _pick_first_option(value).upper()
    product_key = (product_name or "").strip().lower()
    if product_key == "training jersey":
        return "HALF"
    if product_key in {"polo", "travel polo"}:
        return _pick_first_option(value).strip()
    if product_key in {"hoodies", "zipped hoodie"}:
        return "FULL"
    mode = _type_mode_for_product(product_name)
    if mode == "none":
        return ""
    if mode == "fit":
        if raw in {"SLIM"}:
            return "SLIM"
        if raw in {"GENERAL"}:
            return "GENERAL"
        if raw in {"REGULAR", "STRAIGHT"}:
            return "REGULAR"
        return "REGULAR"
    if raw in {"SHORT", "SHORT SLEEVE", "HALF", "HALF SLEEVE"}:
        return "HALF"
    if raw in {"LONG", "LONG SLEEVE", "FULL", "FULL SLEEVE"}:
        return "FULL"
    if raw in {"3/4", "3/4TH", "3/4 TH", "THREE FOURTH", "THREE-FOURTH"}:
        return "3/4 TH"
    return "HALF"


def _type_mode_for_product(product_name: str) -> str:
    key = (product_name or "").strip().lower()
    if key in TYPELESS_PRODUCTS:
        return "none"
    if key in TYPE_PRODUCTS:
        return "fit"
    return "sleeve"


def _allowed_genders_for_product(product_name: str):
    key = (product_name or "").strip().lower()
    if key in ADULT_YOUTH_GENDER_PRODUCTS:
        return {"ADULT", "YOUTH"}
    return {"MENS", "WOMENS", "YOUTH"}


def _pick_first_option(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    return re.split(r"\s*(?:/|\\|\||,|;|\bor\b)\s*", raw, maxsplit=1)[0].strip()


def _save_file(order: Order, file_storage: Optional[FileStorage], tag: str):
    if not file_storage or not file_storage.filename:
        return None

    base_name = secure_filename(file_storage.filename)
    filename = f"{tag}-{uuid.uuid4().hex[:10]}-{base_name}"
    data = file_storage.read()
    storage_path = save_order_file(
        order.id,
        ORDER_IMAGE_SECTION,
        filename,
        data,
        content_type=file_storage.content_type or "application/octet-stream",
    )

    attachment = Attachment(
        order_id=order.id,
        filename=filename,
        mime_type=file_storage.content_type or "application/octet-stream",
        storage_path=storage_path,
    )
    db.session.add(attachment)
    return storage_path

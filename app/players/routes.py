import re

from flask import Blueprint, abort, flash, redirect, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Accessory, Order, OrderItem, Player
from app.orders.access import can_user_access_order
from app.players.services import (
    parse_accessory_totals,
    parse_players_ai,
    parse_players_clean,
    parse_product_item_totals,
)
from app.utils import add_audit


players_bp = Blueprint("players", __name__, url_prefix="/orders/<int:order_id>/players")


@players_bp.route("/add", methods=["POST"])
@login_required
def add_player(order_id):
    order = Order.query.get_or_404(order_id)
    if not can_user_access_order(current_user, order):
        abort(403)
    row_number = int(request.form.get("row_number", len(order.players) + 1))
    player = Player(
        order_id=order.id,
        row_number=row_number,
        player_name=(request.form.get("player_name") or "").strip(),
        number=(request.form.get("number") or "0").strip(),
        sleeve_type=_normalize_sleeve_type(request.form.get("sleeve_type")),
        tshirt_size=(request.form.get("tshirt_size") or "M").strip().upper(),
        tshirt_qty=max(1, int(request.form.get("tshirt_qty", 1))),
        trouser_size=(request.form.get("trouser_size") or "M").strip().upper(),
        trouser_qty=max(1, int(request.form.get("trouser_qty", 1))),
    )
    db.session.add(player)
    add_audit(order.id, current_user.id, "ADD_PLAYER", "player_name", None, player.player_name)
    db.session.commit()
    flash("Player row added.", "success")
    return redirect(url_for("orders.edit_order", order_id=order.id, step=3))


@players_bp.route("/delete/<int:player_id>", methods=["POST"])
@login_required
def delete_player(order_id, player_id):
    order = Order.query.get_or_404(order_id)
    if not can_user_access_order(current_user, order):
        abort(403)
    player = Player.query.filter_by(order_id=order.id, id=player_id).first_or_404()
    db.session.delete(player)
    add_audit(order.id, current_user.id, "DELETE_PLAYER", "player_name", player.player_name, None)
    db.session.commit()
    flash("Player row deleted.", "info")
    return redirect(url_for("orders.edit_order", order_id=order.id, step=3))


@players_bp.route("/import-csv", methods=["POST"])
@login_required
def import_csv(order_id):
    order = Order.query.get_or_404(order_id)
    if not can_user_access_order(current_user, order):
        abort(403)
    file_storage = request.files.get("csv_file")
    if not file_storage:
        flash("CSV/XLSX file is required.", "danger")
        return redirect(url_for("orders.edit_order", order_id=order.id, step=3))

    rows, errors = parse_players_clean(file_storage)
    _replace_order_players(order.id, rows)
    file_storage.stream.seek(0)
    accessory_totals, has_accessory_columns = parse_accessory_totals(file_storage)
    _sync_order_accessories(order, accessory_totals, has_accessory_columns)
    file_storage.stream.seek(0)
    product_totals, has_product_totals = parse_product_item_totals(file_storage)
    _sync_order_items_from_roster(order, product_totals, has_product_totals)

    add_audit(order.id, current_user.id, "IMPORT_PLAYERS_CSV", "rows", 0, len(rows))
    db.session.commit()
    if errors:
        _flash_import_errors(errors, "CSV import completed with issues")
        flash(f"Replaced roster with {len(rows)} valid row(s); {len(errors)} row(s) skipped.", "warning")
    else:
        flash(f"Imported {len(rows)} player rows.", "success")
    return redirect(url_for("orders.edit_order", order_id=order.id, step=3))


@players_bp.route("/import-ai", methods=["GET", "POST"])
@login_required
def import_ai(order_id):
    order = Order.query.get_or_404(order_id)
    if not can_user_access_order(current_user, order):
        abort(403)
    if request.method == "GET":
        flash("Use Step 3 upload form for AI import (.xlsx/.csv).", "info")
        return redirect(url_for("orders.edit_order", order_id=order.id, step=3))

    file_storage = request.files.get("ai_file")
    if not file_storage:
        flash("Upload a .xlsx or .csv file for AI roster import.", "danger")
        return redirect(url_for("orders.edit_order", order_id=order.id, step=3))

    try:
        rows, errors = parse_players_ai(file_storage)
    except Exception as exc:
        flash(f"AI import failed: {exc}", "danger")
        return redirect(url_for("orders.edit_order", order_id=order.id, step=3))

    _replace_order_players(order.id, rows)
    file_storage.stream.seek(0)
    accessory_totals, has_accessory_columns = parse_accessory_totals(file_storage)
    _sync_order_accessories(order, accessory_totals, has_accessory_columns)
    file_storage.stream.seek(0)
    product_totals, has_product_totals = parse_product_item_totals(file_storage)
    _sync_order_items_from_roster(order, product_totals, has_product_totals)

    add_audit(order.id, current_user.id, "IMPORT_PLAYERS_AI", "rows", 0, len(rows))
    db.session.commit()
    if errors:
        _flash_import_errors(errors, "AI import completed with issues")
        flash(f"Replaced roster with {len(rows)} valid row(s); {len(errors)} row(s) skipped.", "warning")
    else:
        flash(f"AI imported {len(rows)} player rows.", "success")
    return redirect(url_for("orders.edit_order", order_id=order.id, step=3))


def _flash_import_errors(errors, title):
    flash(f"{title}: {len(errors)} row(s) have issues.", "danger")
    for err in errors[:5]:
        flash(f"Row {err.get('row')}: {err.get('error')}", "warning")
    if len(errors) > 5:
        flash(f"...and {len(errors) - 5} more row errors.", "warning")


def _replace_order_players(order_id, rows):
    Player.query.filter_by(order_id=order_id).delete(synchronize_session=False)
    for row in rows:
        db.session.add(Player(order_id=order_id, **row))


def _sync_order_accessories(order, totals, enabled):
    if not enabled:
        return

    by_name = {(a.product_name or "").strip().lower(): a for a in (order.accessories or [])}
    mapping = {
        "CAP": "cap",
        "BAGGY CAP": "baggy cap",
        "HAT": "hat",
        "PAD CLAD": "pad clad",
        "HELMET CLAD": "helmet clad",
    }

    for label, normalized in mapping.items():
        qty = int(totals.get(label, 0) or 0)
        accessory = by_name.get(normalized)
        if accessory:
            accessory.quantity = qty
        else:
            db.session.add(Accessory(order_id=order.id, product_name=label.title(), quantity=qty))


def _sync_order_items_from_roster(order, totals, enabled):
    if not enabled:
        return

    def _qty_total(payload):
        return sum(int(payload.get(f, 0) or 0) for f in ("qty_xs", "qty_s", "qty_m", "qty_l", "qty_xl", "qty_2xl", "qty_3xl", "qty_4xl"))

    def _product_name_for_key(product_key):
        mapping = {
            "playing_jersey": "Playing Jersey",
            "training_jersey": "Training Jersey",
            "trouser": "Trousers",
            "travel_trouser": "Travel Tousers",
            "polo": "Polo",
            "shorts": "Shorts",
            "jacket": "Jackets",
            "sleeveless_jacket": "Sleeveless Jackets",
        }
        return mapping.get(product_key, product_key.replace("_", " ").title())

    # Ensure roster-derived products exist as order items (global fix).
    existing_keys = set()
    for item in order.items:
        product_key = _order_item_product_key(item.product_name)
        gender = (item.gender or "MENS").strip().upper()
        sleeve = (item.sleeve_type or "").strip().upper()
        canonical_sleeve = sleeve if product_key in {"playing_jersey", "training_jersey"} else ""
        existing_keys.add((product_key, gender, canonical_sleeve))

    for (product_key, gender, sleeve), payload in totals.items():
        if _qty_total(payload) <= 0:
            continue
        canonical_sleeve = (sleeve or "").strip().upper() if product_key in {"playing_jersey", "training_jersey"} else ""
        key = (product_key, (gender or "MENS").strip().upper(), canonical_sleeve)
        if key in existing_keys:
            continue
        db.session.add(
            OrderItem(
                order_id=order.id,
                product_name=_product_name_for_key(product_key),
                gender=key[1],
                sleeve_type=canonical_sleeve,
                total=0,
            )
        )
        existing_keys.add(key)

    db.session.flush()
    items = OrderItem.query.filter_by(order_id=order.id).order_by(OrderItem.id.asc()).all()

    # Deduplicate legacy/typo product rows that normalize to the same roster key
    # (e.g. "Travel Tousers" vs "Travel Trousers"), so totals are not doubled in overview.
    keep_map = {}
    duplicate_ids = []
    for item in items:
        product_key = _order_item_product_key(item.product_name)
        gender = (item.gender or "MENS").strip().upper()
        sleeve = (item.sleeve_type or "").strip().upper()
        canonical_sleeve = sleeve if product_key in {"playing_jersey", "training_jersey"} else ""
        dedupe_key = (product_key, gender, canonical_sleeve)
        if dedupe_key in keep_map:
            duplicate_ids.append(item.id)
        else:
            keep_map[dedupe_key] = item.id

    if duplicate_ids:
        for dup_id in duplicate_ids:
            dup_item = next((it for it in items if it.id == dup_id), None)
            if dup_item is not None:
                db.session.delete(dup_item)
        db.session.flush()
        items = OrderItem.query.filter_by(order_id=order.id).order_by(OrderItem.id.asc()).all()

    for item in items:
        item.qty_xs = 0
        item.qty_s = 0
        item.qty_m = 0
        item.qty_l = 0
        item.qty_xl = 0
        item.qty_2xl = 0
        item.qty_3xl = 0
        item.qty_4xl = 0
        item.total = 0

    for item in items:
        product_key = _order_item_product_key(item.product_name)
        gender = (item.gender or "MENS").strip().upper()
        sleeve = (item.sleeve_type or "").strip().upper()
        candidates = []
        if product_key in {"playing_jersey", "training_jersey"}:
            candidates.append((product_key, gender, sleeve))
            if sleeve:
                candidates.append((product_key, gender, ""))
        else:
            candidates.append((product_key, gender, ""))

        selected = None
        for key in candidates:
            if key in totals:
                selected = totals[key]
                break
        if not selected:
            continue

        for field in ("qty_xs", "qty_s", "qty_m", "qty_l", "qty_xl", "qty_2xl", "qty_3xl", "qty_4xl"):
            setattr(item, field, int(selected.get(field, 0) or 0))
        item.compute_total()


def _order_item_product_key(name):
    txt = (name or "").strip().lower()
    if "training" in txt and "jersey" in txt:
        return "training_jersey"
    if "playing" in txt and "jersey" in txt:
        return "playing_jersey"
    if "travel" in txt and any(token in txt for token in ("trouser", "trousers", "touser", "tousers", "pant")):
        return "travel_trouser"
    if "sleeveless" in txt and "jacket" in txt:
        return "sleeveless_jacket"
    if "short" in txt:
        return "shorts"
    if "jacket" in txt:
        return "jacket"
    if "polo" in txt:
        return "polo"
    if "trouser" in txt or "pant" in txt:
        return "trouser"
    return txt.replace(" ", "_")


def _normalize_sleeve_type(value):
    raw = _pick_first_option(value).upper()
    if raw in {"SHORT", "SHORT SLEEVE", "HALF", "HALF SLEEVE"}:
        return "HALF"
    if raw in {"LONG", "LONG SLEEVE", "FULL", "FULL SLEEVE"}:
        return "FULL"
    if raw in {"3/4", "3/4TH", "3/4 TH", "THREE FOURTH", "THREE-FOURTH"}:
        return "3/4 TH"
    return "HALF"


def _pick_first_option(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    return re.split(r"\s*(?:/|\\|\||,|;|\bor\b)\s*", raw, maxsplit=1)[0].strip()

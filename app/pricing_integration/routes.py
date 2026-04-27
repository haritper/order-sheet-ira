from __future__ import annotations

import secrets
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import db
from .datasheet import (
    import_pricing_rules,
    list_pricing_rules,
    pricing_catalog_summary,
    update_pricing_override,
)
from .fx import get_daily_fx_snapshot
from .orders import (
    create_order_from_upload,
    delete_order,
    get_order,
    list_orders,
    owner_metrics,
    update_order_rates_and_costs,
)
from app.models import Role, User

pricing_bp = Blueprint(
    "pricing",
    __name__,
    url_prefix="/pricing",
    template_folder="templates",
    static_folder="static",
    static_url_path="/static",
)

PUBLIC_PRICING_ENDPOINTS = {
    "pricing.login",
    "pricing.logout",
    "pricing.static",
}


def init_pricing_module(app) -> None:
    db.init_app(app)
    with app.app_context():
        db.init_db()
        seed_pricing_if_needed()


@pricing_bp.app_template_filter("usd")
def usd_filter(value: Any) -> str:
    amount = float(value or 0)
    return f"${amount:,.2f}"


@pricing_bp.app_template_filter("fxrate")
def fxrate_filter(value: Any) -> str:
    amount = float(value or 0)
    return f"${amount:,.4f}"


@pricing_bp.before_request
def load_current_user() -> None:
    g.pricing_fx_snapshot = get_daily_fx_snapshot()
    user_id = session.get("pricing_user_id")
    g.pricing_user = None
    g.pricing_is_manager = bool(session.get("pricing_is_manager", False))
    if user_id is None:
        return
    g.pricing_user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if g.pricing_user is not None and str(g.pricing_user["role"]).strip().lower() == "owner":
        g.pricing_is_manager = False


@pricing_bp.before_request
def enforce_pricing_auth() -> Any:
    endpoint = request.endpoint or ""
    if endpoint in PUBLIC_PRICING_ENDPOINTS:
        return None
    if g.get("pricing_user") is None:
        return redirect(url_for("pricing.login", next=request.full_path))
    return None


@pricing_bp.before_request
def enforce_pricing_csrf() -> Any:
    if request.method != "POST":
        return None
    token_in_session = str(session.get("pricing_csrf_token", ""))
    token_from_form = str(request.form.get("pricing_csrf_token", ""))
    if not token_in_session or not token_from_form or token_in_session != token_from_form:
        flash("Invalid session token. Please retry.", "error")
        return redirect(url_for("pricing.login"))
    return None


@pricing_bp.app_context_processor
def inject_globals() -> dict[str, Any]:
    return {
        "pricing_current_user": g.get("pricing_user"),
        "currency_code": current_app.config["PRICING_DISPLAY_CURRENCY"],
        "source_currency_code": current_app.config["PRICING_SOURCE_CURRENCY"],
        "fx_snapshot": g.get("pricing_fx_snapshot"),
        "is_owner": bool(g.get("pricing_user")) and g.pricing_user["role"] == "owner",
        "is_manager": bool(g.get("pricing_is_manager")),
        "current_endpoint": request.endpoint,
        "pricing_csrf_token": get_pricing_csrf_token(),
    }


def _find_order_creation_manager(raw_username: str) -> User | None:
    token = str(raw_username or "").strip().lower()
    if not token:
        return None
    return User.query.filter_by(
        role=Role.MANAGER.value,
        is_active_user=True,
        email=token,
    ).first()


def _ensure_pricing_manager_user() -> dict[str, Any]:
    manager = db.execute(
        "SELECT * FROM users WHERE username = 'manager' LIMIT 1"
    ).fetchone()
    if manager is not None:
        return dict(manager)
    db.execute(
        """
        INSERT INTO users (username, password_hash, role)
        VALUES (?, ?, ?)
        """,
        (
            "manager",
            generate_password_hash(secrets.token_urlsafe(24), method="pbkdf2:sha256"),
            "employee",
        ),
    )
    manager = db.execute(
        "SELECT * FROM users WHERE username = 'manager' LIMIT 1"
    ).fetchone()
    if manager is None:
        raise RuntimeError("Could not initialize pricing manager account")
    return dict(manager)


def _is_pricing_manager_session() -> bool:
    return bool(g.get("pricing_is_manager"))


def login_required(role: str | None = None) -> Callable:
    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapped_view(**kwargs):
            if g.get("pricing_user") is None:
                return redirect(url_for("pricing.login"))
            if role and g.pricing_user["role"] != role:
                flash("You do not have access to that page.", "error")
                return redirect(url_for("pricing.dashboard"))
            return view(**kwargs)

        return wrapped_view

    return decorator


@pricing_bp.route("/login", methods=("GET", "POST"))
def login():
    if g.get("pricing_user") is not None:
        return redirect(url_for("pricing.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        owner = db.execute(
            "SELECT * FROM users WHERE username = ? AND role = 'owner'",
            (username,),
        ).fetchone()
        manager_user = _find_order_creation_manager(username)

        authenticated_user_id: int | None = None
        is_manager_login = False
        if owner is not None and check_password_hash(owner["password_hash"], password):
            authenticated_user_id = int(owner["id"])
        elif manager_user is not None and manager_user.check_password(password):
            pricing_manager = _ensure_pricing_manager_user()
            authenticated_user_id = int(pricing_manager["id"])
            is_manager_login = True
        else:
            flash("Invalid username or password.", "error")

        if authenticated_user_id is not None:
            for key in [k for k in list(session.keys()) if k.startswith("pricing_")]:
                session.pop(key, None)
            session["pricing_auth_nonce"] = secrets.token_hex(16)
            session["pricing_csrf_token"] = secrets.token_urlsafe(32)
            session["pricing_user_id"] = authenticated_user_id
            session["pricing_is_manager"] = bool(is_manager_login)
            # Keep pricing login persistent across browser restarts.
            session.permanent = True
            next_page = request.args.get("next", "").strip()
            if next_page.startswith("/pricing/"):
                return redirect(next_page)
            return redirect(url_for("pricing.dashboard"))
    return render_template("pricing/login.html")


@pricing_bp.route("/logout")
def logout():
    session.pop("pricing_user_id", None)
    session.pop("pricing_is_manager", None)
    return redirect(url_for("pricing.login"))


@pricing_bp.route("/")
@login_required()
def dashboard():
    orders = list_orders()
    metrics = owner_metrics() if g.pricing_user["role"] == "owner" else None
    return render_template(
        "pricing/dashboard.html",
        orders=orders,
        metrics=metrics,
        dashboard_visuals=build_dashboard_visuals(
            orders,
            metrics,
            g.pricing_user["role"] == "owner",
        ),
    )


@pricing_bp.route("/orders")
@login_required()
def orders():
    return render_template("pricing/orders.html", orders=list_orders())


@pricing_bp.route("/orders/upload", methods=("GET", "POST"))
@login_required()
def upload_order():
    if request.method == "POST":
        uploaded_file = request.files.get("order_sheet")
        if uploaded_file is None or not uploaded_file.filename:
            flash("Choose an order sheet PDF to upload.", "error")
        elif not uploaded_file.filename.lower().endswith(".pdf"):
            flash("Only PDF order sheets are supported.", "error")
        else:
            order_id = create_order_from_upload(
                uploaded_file,
                Path(current_app.config["PRICING_UPLOAD_FOLDER"]),
                g.pricing_user["id"],
            )
            flash("Order uploaded and priced successfully.", "success")
            return redirect(url_for("pricing.order_detail", order_id=order_id))
    return render_template("pricing/upload_order.html")


@pricing_bp.route("/orders/<int:order_id>", methods=("GET", "POST"))
@login_required()
def order_detail(order_id: int):
    order = get_order(order_id)
    if not order:
        flash("Order not found.", "error")
        return redirect(url_for("pricing.orders"))

    if request.method == "POST":
        if not _is_pricing_manager_session():
            flash("Only manager can update order financials.", "error")
            return redirect(url_for("pricing.order_detail", order_id=order_id))
        quoted_rates: dict[int, float | None] = {}
        for item in order["items"]:
            field_name = f"quote_{item['id']}"
            raw_value = request.form.get(field_name, "").strip()
            quoted_rates[item["id"]] = float(raw_value) if raw_value else None

        shipping_fee = parse_form_float(request.form.get("shipping_fee"), 0.0)
        duty_fee = parse_form_float(request.form.get("duty_fee"), 0.0)
        status = request.form.get("status", order["status"]).strip() or order["status"]
        mark_delivered = request.form.get("mark_delivered") == "yes"
        notes = request.form.get("notes", "").strip()
        update_order_rates_and_costs(
            order_id,
            quoted_rates,
            shipping_fee,
            duty_fee,
            status,
            mark_delivered,
            notes,
        )
        flash("Order updated.", "success")
        return redirect(url_for("pricing.order_detail", order_id=order_id))

    return render_template(
        "pricing/order_detail.html",
        order=order,
        order_visuals=build_order_visuals(order, g.pricing_user["role"] == "owner"),
    )


@pricing_bp.route("/orders/<int:order_id>/delete", methods=("POST",))
@login_required(role="owner")
def delete_order_route(order_id: int):
    deleted = delete_order(order_id)
    if deleted:
        flash("Order deleted.", "success")
    else:
        flash("Order not found.", "error")
    return redirect(url_for("pricing.orders"))


@pricing_bp.route("/pricing", methods=("GET", "POST"))
@login_required()
def pricing_rules():
    filters = {
        "q": request.args.get("q", "").strip(),
        "category": request.args.get("category", "").strip(),
        "sheet": request.args.get("sheet", "").strip(),
    }
    all_pricing_rules = list_pricing_rules()
    if request.method == "POST":
        if not _is_pricing_manager_session():
            flash("Only manager can update pricing rules.", "error")
            return redirect(
                url_for(
                    "pricing.pricing_rules",
                    q=filters["q"] or None,
                    category=filters["category"] or None,
                    sheet=filters["sheet"] or None,
                )
            )
        rule_id = int(request.form["rule_id"])
        raw_override = request.form.get("override_unit_rate_inr", "").strip()
        override = float(raw_override) if raw_override else None
        update_pricing_override(rule_id, override)
        flash("Pricing rule updated in INR.", "success")
        return redirect(
            url_for(
                "pricing.pricing_rules",
                q=filters["q"] or None,
                category=filters["category"] or None,
                sheet=filters["sheet"] or None,
            )
        )
    pricing_rules_list = apply_pricing_filters(all_pricing_rules, filters)
    return render_template(
        "pricing/pricing.html",
        pricing_rules=pricing_rules_list,
        pricing_summary=pricing_catalog_summary(all_pricing_rules),
        pricing_filter_options=build_pricing_filter_options(all_pricing_rules),
        pricing_filters=filters,
    )


@pricing_bp.route("/pricing/reimport", methods=("POST",))
@login_required(role="owner")
def pricing_reimport():
    imported_count = import_pricing_rules(Path(current_app.config["PRICING_WORKBOOK_PATH"]))
    flash(f"Re-imported {imported_count} pricing rules from the workbook.", "success")
    return redirect(url_for("pricing.pricing_rules"))


@pricing_bp.route("/shipping", methods=("GET", "POST"))
@login_required(role="owner")
def shipping():
    if request.method == "POST":
        profile_id = int(request.form["profile_id"])
        fee = parse_form_float(request.form.get("fee"), 0.0)
        duty_percent = parse_form_float(request.form.get("duty_percent"), 0.0)
        duty_flat = parse_form_float(request.form.get("duty_flat"), 0.0)
        notes = request.form.get("notes", "").strip()
        db.execute(
            """
            UPDATE shipping_profiles
            SET fee = ?, duty_percent = ?, duty_flat = ?, notes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (fee, duty_percent, duty_flat, notes, profile_id),
        )
        flash("Shipping profile updated.", "success")
        return redirect(url_for("pricing.shipping"))
    profiles = db.execute(
        "SELECT * FROM shipping_profiles ORDER BY profile_name"
    ).fetchall()
    return render_template("pricing/shipping.html", profiles=profiles)


def parse_form_float(raw: str | None, default: float) -> float:
    if raw is None:
        return default
    text = raw.strip()
    if not text:
        return default
    return float(text)


def build_dashboard_visuals(
    orders: list[dict[str, Any]],
    metrics: dict[str, Any] | None,
    is_owner: bool,
) -> dict[str, Any]:
    total_orders = max(len(orders), 1)
    status_names = ["uploaded", "quoted", "in_production", "delivered"]
    status_counts = {
        status: sum(1 for order in orders if (order.get("status") or "").lower() == status)
        for status in status_names
    }
    status_cards = [
        {
            "label": status.replace("_", " ").title(),
            "count": status_counts[status],
            "percent": round((status_counts[status] / total_orders) * 100, 1),
        }
        for status in status_names
    ]

    visuals: dict[str, Any] = {
        "status_cards": status_cards,
        "order_tracks": [],
        "financial_bars": [],
    }

    if is_owner and metrics:
        financial_rows = [
            ("Quoted Revenue", float(metrics.get("total_quoted") or 0)),
            ("Landed Cost", float(metrics.get("total_landed_cost") or 0)),
            ("Margin", float(metrics.get("total_margin") or 0)),
        ]
        financial_max = max((value for _, value in financial_rows), default=0.0) or 1.0
        visuals["financial_bars"] = [
            {
                "label": label,
                "value": value,
                "width": max(round((value / financial_max) * 100, 1), 8 if value > 0 else 0),
            }
            for label, value in financial_rows
        ]

        recent_orders = orders[:5]
        comparison_max = max(
            [
                max(
                    float(order.get("suggested_subtotal") or 0),
                    float(order.get("quoted_subtotal") or 0),
                    0,
                )
                for order in recent_orders
            ]
            or [1.0]
        )
        visuals["order_tracks"] = [
            {
                "order_number": order["order_number"],
                "status": order["status"],
                "suggested": float(order.get("suggested_subtotal") or 0),
                "quoted": float(order.get("quoted_subtotal") or 0),
                "suggested_width": max(
                    round((float(order.get("suggested_subtotal") or 0) / comparison_max) * 100, 1),
                    4 if float(order.get("suggested_subtotal") or 0) > 0 else 0,
                ),
                "quoted_width": max(
                    round((float(order.get("quoted_subtotal") or 0) / comparison_max) * 100, 1),
                    4 if float(order.get("quoted_subtotal") or 0) > 0 else 0,
                ),
            }
            for order in recent_orders
        ]
        return visuals

    visuals["order_tracks"] = [
        {
            "order_number": order["order_number"],
            "status": order["status"],
            "suggested_width": max(100 - (index * 15), 35),
        }
        for index, order in enumerate(orders[:5])
    ]
    return visuals


def build_order_visuals(order: dict[str, Any], is_owner: bool) -> dict[str, Any]:
    items = order.get("items", [])
    quantity_max = max([int(item.get("quantity") or 0) for item in items] or [1])
    item_quantity_bars = [
        {
            "id": item["id"],
            "width": max(
                round((int(item.get("quantity") or 0) / quantity_max) * 100, 1),
                8 if int(item.get("quantity") or 0) > 0 else 0,
            ),
        }
        for item in items
    ]
    visuals: dict[str, Any] = {"item_quantity_bars": item_quantity_bars}

    if not is_owner:
        return visuals

    financial_rows = [
        ("Production Cost", float(order.get("suggested_subtotal") or 0)),
        ("Quoted", float(order.get("quoted_subtotal") or 0)),
        ("Margin", float(abs(order.get("final_margin") or 0))),
    ]
    financial_max = max((value for _, value in financial_rows), default=0.0) or 1.0
    visuals["financial_bars"] = [
        {
            "label": label,
            "value": value,
            "width": max(round((value / financial_max) * 100, 1), 6 if value > 0 else 0),
        }
        for label, value in financial_rows
    ]

    line_max = max(
        [
            max(
                float(item.get("line_suggested_total") or 0),
                float(item.get("line_quoted_total") or 0),
                0,
            )
            for item in items
        ]
        or [1.0]
    )
    visuals["item_value_bars"] = [
        {
            "id": item["id"],
            "suggested_width": max(
                round((float(item.get("line_suggested_total") or 0) / line_max) * 100, 1),
                6 if float(item.get("line_suggested_total") or 0) > 0 else 0,
            ),
            "quoted_width": max(
                round((float(item.get("line_quoted_total") or 0) / line_max) * 100, 1),
                6 if float(item.get("line_quoted_total") or 0) > 0 else 0,
            ),
        }
        for item in items
    ]
    return visuals


def apply_pricing_filters(
    pricing_rules: list[dict[str, Any]],
    filters: dict[str, str],
) -> list[dict[str, Any]]:
    q = filters["q"].lower()
    category = filters["category"].lower()
    sheet = filters["sheet"]

    filtered = pricing_rules
    if q:
        filtered = [
            rule
            for rule in filtered
            if any(
                q in str(rule.get(field) or "").lower()
                for field in ("product_code", "category", "descriptor", "variant", "sheet_name")
            )
        ]
    if category:
        filtered = [
            rule for rule in filtered if str(rule.get("category") or "").lower() == category
        ]
    if sheet:
        filtered = [rule for rule in filtered if str(rule.get("sheet_name") or "") == sheet]
    return filtered


def build_pricing_filter_options(pricing_rules: list[dict[str, Any]]) -> dict[str, list[str]]:
    categories = sorted(
        {
            str(rule.get("category") or "")
            for rule in pricing_rules
            if str(rule.get("category") or "")
        }
    )
    sheets = sorted(
        {
            str(rule.get("sheet_name") or "")
            for rule in pricing_rules
            if str(rule.get("sheet_name") or "")
        }
    )
    return {"categories": categories, "sheets": sheets}


def seed_pricing_if_needed() -> None:
    existing = db.execute("SELECT COUNT(*) AS count FROM pricing_rules").fetchone()["count"]
    workbook_path = Path(current_app.config["PRICING_WORKBOOK_PATH"])
    if not workbook_path.exists():
        return
    if not existing:
        import_pricing_rules(workbook_path)
        return
    stale_sample = db.execute(
        """
        SELECT fabric_cost, printing_cost
        FROM pricing_rules
        WHERE product_code = 'PO-HS-M'
        ORDER BY id
        LIMIT 1
        """
    ).fetchone()
    if stale_sample and (
        float(stale_sample["fabric_cost"] or 0) == 0
        or float(stale_sample["printing_cost"] or 0) == 0
    ):
        import_pricing_rules(workbook_path)


def get_pricing_csrf_token() -> str:
    token = session.get("pricing_csrf_token")
    if token:
        return str(token)
    token = secrets.token_urlsafe(32)
    session["pricing_csrf_token"] = token
    return token

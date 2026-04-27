from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
import base64
import re
from types import SimpleNamespace

from flask import current_app, render_template

from app.extensions import db
from app.models import Attachment
from app.order_numbers import peek_invoice_number
from app.orders.checklist_cutting import load_checklist_state
from app.orders.overview import build_order_overview, build_packing_groups
from app.storage import ORDER_SHEET_SECTION, delete, exists, read_bytes, save_order_file

try:
    from weasyprint import HTML
except Exception:  # pragma: no cover
    HTML = None

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None

try:
    from fpdf import FPDF
except Exception:  # pragma: no cover
    FPDF = None



def _normalize_fabric_key(value: str) -> str:
    token = str(value or "").strip().lower()
    token = re.sub(r"[^a-z0-9]+", " ", token)
    token = re.sub(r"\s+", " ", token).strip()
    return token


def _canonical_fabric_name(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return raw
    if _normalize_fabric_key(raw) == "scuba":
        return "Scuba 330 GSM"
    return raw



def render_order_pdf(order, pdf_variant: str = "order_sheet", display_order_id: str | None = None):
    variant = str(pdf_variant or "").strip().lower()
    show_shipping_details = variant not in {"customer-plan", "customer_plan"}
    resolved_display_order_id = str(display_order_id or order.order_id or "").strip()
    overview = build_order_overview(order)
    packing_groups = build_packing_groups(order)
    image_attachments = [
        a
        for a in (order.attachments or [])
        if str(getattr(a, "mime_type", "")).lower().startswith("image/")
    ]
    image_attachments.sort(
        key=lambda a: (getattr(a, "created_at", None) or datetime.min, getattr(a, "id", 0)),
        reverse=True,
    )
    tshirt_specs, trouser_specs, accessory_specs = _split_design_specs(
        order.branding_specs,
        image_attachments,
    )
    accessory_quantity_map = _build_accessory_quantity_map(order.accessories)
    common_design_notes = _collect_common_notes(order.branding_specs, "design_notes")
    common_production_notes = _collect_common_notes(order.branding_specs, "production_notes")
    html = render_template(
        "exports/order_pdf.html",
        order=order,
        order_overview=overview,
        tshirt_specs=tshirt_specs,
        trouser_specs=trouser_specs,
        accessory_specs=accessory_specs,
        accessory_quantity_map=accessory_quantity_map,
        packaging_groups={k: _build_packaging_rows(v) for k, v in packing_groups.items()},
        common_design_notes=common_design_notes,
        common_production_notes=common_production_notes,
        approval_paragraphs=APPROVAL_PARAGRAPHS,
        file_uri=_file_uri,
        ira_logo_uri=_ira_logo_uri(),
        show_shipping_details=show_shipping_details,
        display_order_id=resolved_display_order_id,
    )
    if HTML is not None:
        try:
            return HTML(string=html, base_url=str(Path.cwd())).write_pdf()
        except Exception as exc:  # pragma: no cover
            current_app.logger.exception("WeasyPrint render failed, using fallback PDF: %s", exc)

    if sync_playwright is not None:
        try:
            return _render_playwright_pdf(html)
        except Exception as exc:  # pragma: no cover
            current_app.logger.exception("Playwright render failed, using fallback PDF: %s", exc)

    if FPDF is None:
        raise RuntimeError("PDF renderer unavailable. Install WeasyPrint runtime libs or fpdf2.")
    return _render_fallback_pdf(order)


def render_invoice_pdf(order, payment_details: dict | None = None) -> bytes:
    details = payment_details or {}
    order_date = _resolve_invoice_order_date(order)
    shipment_start = order_date + timedelta(days=14)
    shipment_end = order_date + timedelta(days=21)
    shipping = _resolve_invoice_shipping(order)

    total_amount = _format_currency(details.get("total_amount", "0.00"), default="0.00")
    payment_received = _format_currency(
        details.get("payment_received", details.get("amount_paid", "0.00")),
        default="0.00",
    )
    balance_amount = _format_currency(details.get("balance", "0.00"), default="0.00")
    invoice_number = str(details.get("invoice_number", "") or "").strip() or build_invoice_number(order, order_date)
    payment_mode = str(details.get("payment_mode", "") or "").strip().upper()
    paid_on_date = _try_parse_date_token(str(details.get("paid_on", "") or "").strip())
    paid_on_display = _format_invoice_paid_on(details.get("paid_on", ""))
    paid_on_parts = _invoice_date_parts(paid_on_date) if paid_on_date is not None else None
    payment_status = str(details.get("payment_status", "") or "").strip().upper()
    if not payment_status:
        payment_status = "FULLY PAID" if Decimal(payment_received) >= Decimal(total_amount) else "PARTIALLY PAID"

    html = render_template(
        "exports/invoice_pdf.html",
        enquiry_id=str(order.order_id or "").strip(),
        invoice_order_id=str(order.production_order_id or order.order_id or "").strip(),
        order_date=_invoice_date_parts(order_date),
        shipment_start=_invoice_date_parts(shipment_start),
        shipment_end=_invoice_date_parts(shipment_end),
        invoice_number=invoice_number,
        amount=total_amount,
        payment_received=payment_received,
        balance=balance_amount,
        payment_status=payment_status,
        payment_mode=payment_mode,
        paid_on=paid_on_display,
        paid_on_parts=paid_on_parts,
        transaction_id=str(details.get("transaction_id", "") or "").strip(),
        shipping_name=shipping["name"],
        shipping_mobile=shipping["mobile"],
        shipping_address=shipping["shipping_address"],
        shipping_city=shipping["city"],
        shipping_zip_code=shipping["zip_code"],
        shipping_state=shipping["state"],
        shipping_country=shipping["country"],
        shipping_copy=INVOICE_SHIPPING_COPY,
        what_next_steps=INVOICE_WHAT_NEXT_STEPS,
        invoice_footer=INVOICE_FOOTER,
        ira_logo_uri=_ira_logo_uri(),
    )

    if HTML is not None:
        try:
            return HTML(string=html, base_url=str(Path.cwd())).write_pdf()
        except Exception as exc:  # pragma: no cover
            current_app.logger.exception("Invoice WeasyPrint render failed, using fallback PDF: %s", exc)

    if sync_playwright is not None:
        try:
            return _render_playwright_pdf(html)
        except Exception as exc:  # pragma: no cover
            current_app.logger.exception("Invoice Playwright render failed, using fallback PDF: %s", exc)

    if FPDF is None:
        raise RuntimeError("PDF renderer unavailable. Install WeasyPrint runtime libs or fpdf2.")
    return _render_invoice_fallback_pdf(
        enquiry_id=str(order.order_id or "").strip(),
        invoice_order_id=str(order.production_order_id or order.order_id or "").strip(),
        order_date=_invoice_date_parts(order_date),
        shipment_start=_invoice_date_parts(shipment_start),
        shipment_end=_invoice_date_parts(shipment_end),
        invoice_number=invoice_number,
        amount=total_amount,
        payment_received=payment_received,
        balance=balance_amount,
        payment_status=payment_status,
        payment_mode=payment_mode,
        paid_on=paid_on_display,
        transaction_id=str(details.get("transaction_id", "") or "").strip(),
        shipping_name=shipping["name"],
        shipping_mobile=shipping["mobile"],
        shipping_address=shipping["shipping_address"],
        shipping_city=shipping["city"],
        shipping_zip_code=shipping["zip_code"],
        shipping_state=shipping["state"],
        shipping_country=shipping["country"],
    )


def build_invoice_number(order, order_date: date | None = None) -> str:
    resolved = order_date or _resolve_invoice_order_date(order)
    return peek_invoice_number(order_date=resolved)


def _resolve_invoice_order_date(order) -> date:
    enquiry = getattr(order, "enquiry_date", None)
    if isinstance(enquiry, date):
        return enquiry
    created = getattr(order, "created_at", None)
    if isinstance(created, datetime):
        return created.date()
    return datetime.utcnow().date()


def _invoice_date_parts(value: date) -> dict[str, str]:
    day = int(value.day)
    suffix = _ordinal_suffix(day)
    month_year = value.strftime("%B %Y").upper()
    return {
        "day": str(day),
        "suffix": suffix.upper(),
        "month_year": month_year,
        "text": f"{day}{suffix.upper()} {month_year}",
    }


def _ordinal_suffix(day: int) -> str:
    if 10 <= (day % 100) <= 20:
        return "TH"
    return {1: "ST", 2: "ND", 3: "RD"}.get(day % 10, "TH")


def _format_currency(raw: object, default: str = "0.00") -> str:
    token = str(raw or "").strip().replace("$", "").replace(",", "")
    if not token:
        return default
    try:
        value = Decimal(token)
    except (InvalidOperation, TypeError):
        return default
    return f"{value:.2f}"


def _format_invoice_paid_on(raw: object) -> str:
    token = str(raw or "").strip()
    if not token:
        return ""
    parsed = _try_parse_date_token(token)
    if parsed is not None:
        return _invoice_date_parts(parsed)["text"]
    cleaned = re.sub(r"\s+", " ", token).strip()
    return cleaned.upper()


def _try_parse_date_token(raw: str) -> date | None:
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", str(raw or "").strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    patterns = [
        "%d %B %Y, %I:%M %p",
        "%d %b %Y, %I:%M %p",
        "%d %B %Y %I:%M %p",
        "%d %b %Y %I:%M %p",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d %Y",
        "%b %d %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d-%m-%Y",
        "%m-%d-%Y",
        "%Y-%m-%d",
    ]
    for fmt in patterns:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _resolve_invoice_shipping(order) -> dict[str, str]:
    state = load_checklist_state(order)
    flow = state.get("flow", {}) if isinstance(state, dict) else {}
    if not isinstance(flow, dict):
        flow = {}

    order_name = _clean_text(getattr(order, "customer_name", ""))
    order_mobile = _clean_text(getattr(order, "mobile", ""))
    order_address = _clean_text(getattr(order, "shipping_address", ""))
    order_city = _clean_text(getattr(order, "city", ""))
    order_state = _clean_text(getattr(order, "state", ""))
    order_zip = _clean_text(getattr(order, "zip_code", ""))
    order_country = _clean_text(getattr(order, "country", "")) or "USA"

    flow_name = _clean_text(flow.get("shipping_name", "") or flow.get("customer_name", ""))
    flow_mobile = _clean_text(flow.get("shipping_mobile", "") or flow.get("mobile", ""))
    flow_address = _clean_text(flow.get("shipping_address", ""))
    flow_city = _clean_text(flow.get("city", ""))
    flow_state = _clean_text(flow.get("state", ""))
    flow_zip = _clean_text(flow.get("zip_code", ""))
    flow_country = _clean_text(flow.get("country", ""))

    shipping_address = order_address or flow_address
    shipping_city = order_city or flow_city
    shipping_state = order_state or flow_state
    shipping_zip = order_zip or flow_zip
    shipping_country = order_country or flow_country or "USA"

    return {
        "name": _display_text(order_name or flow_name),
        "mobile": _display_text(order_mobile or flow_mobile),
        "shipping_address": _display_text(shipping_address),
        "city": _display_text(shipping_city),
        "state": _display_text(shipping_state),
        "zip_code": _display_text(shipping_zip),
        "country": _display_text(shipping_country),
    }


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _display_text(value: object) -> str:
    token = _clean_text(value)
    return token if token else "-"


def collect_plan_render_stats(order) -> dict[str, int]:
    image_attachments = [
        a
        for a in (order.attachments or [])
        if str(getattr(a, "mime_type", "")).lower().startswith("image/")
    ]
    image_attachments.sort(
        key=lambda a: (getattr(a, "created_at", None) or datetime.min, getattr(a, "id", 0)),
        reverse=True,
    )
    tshirt_specs, trouser_specs, accessory_specs = _split_design_specs(
        order.branding_specs,
        image_attachments,
    )
    all_paths = []
    for spec in (order.branding_specs or []):
        for field in (
            "front_image_path",
            "right_image_path",
            "back_image_path",
            "left_image_path",
            "logo_front_path",
            "logo_right_path",
            "logo_left_path",
            "logo_back_path",
        ):
            value = str(getattr(spec, field, "") or "").strip()
            if value:
                all_paths.append(value)

    missing_count = 0
    for p in all_paths:
        if not exists(p):
            missing_count += 1

    return {
        "tshirt_count": len(tshirt_specs),
        "trouser_count": len(trouser_specs),
        "accessory_count": len(accessory_specs),
        "missing_image_paths": missing_count,
    }


def _render_fallback_pdf(order):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Order Sheet", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 8, f"Order ID: {order.order_id}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"Customer: {order.customer_name}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"Status: {order.status}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    overview = build_order_overview(order)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Order Overview (Packing-List Derived)", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=9)
    for key, title in [("mens", "MEN'S"), ("womens", "WOMEN'S"), ("youth", "YOUTH")]:
        block = overview[key]
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 7, f"{title} UNIFORMS", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=8)
        pdf.cell(0, 6, "HALF: " + " ".join(f"{k}:{v}" for k, v in block["half_sleeve_tshirt"].items()), new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, "FULL: " + " ".join(f"{k}:{v}" for k, v in block["full_sleeve_tshirt"].items()), new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, "TROUSER: " + " ".join(f"{k}:{v}" for k, v in block["trouser"].items()), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "ACCESORIES: " + " ".join(f"{k}:{v}" for k, v in overview["accessories"].items()), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Player Packing List", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=9)
    pdf.cell(10, 7, "No")
    pdf.cell(45, 7, "Name")
    pdf.cell(18, 7, "Jersey")
    pdf.cell(16, 7, "Sleeve")
    pdf.cell(18, 7, "T Size")
    pdf.cell(14, 7, "T Qty")
    pdf.cell(20, 7, "Tr Size")
    pdf.cell(14, 7, "Tr Qty", new_x="LMARGIN", new_y="NEXT")

    players = sorted(order.players, key=lambda p: (p.row_number, p.id))
    for p in players:
        pdf.cell(10, 7, str(p.row_number or ""))
        pdf.cell(45, 7, (p.player_name or "")[:24])
        pdf.cell(18, 7, (p.number or "")[:10])
        pdf.cell(16, 7, (p.sleeve_type or "")[:8])
        pdf.cell(18, 7, (p.tshirt_size or "")[:8])
        pdf.cell(14, 7, str(p.tshirt_qty or 0))
        pdf.cell(20, 7, (p.trouser_size or "")[:10])
        pdf.cell(14, 7, str(p.trouser_qty or 0), new_x="LMARGIN", new_y="NEXT")

    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Accessories", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    if order.accessories:
        for a in order.accessories:
            pdf.cell(0, 7, f"{a.product_name}: {a.quantity}", new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.cell(0, 7, "None", new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())


def _render_invoice_fallback_pdf(**payload) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "ORDER CONFIRMATION", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 8, f"ENQUIRY ID: {payload.get('enquiry_id', '')}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"ORDER ID: {payload.get('invoice_order_id', '')}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"ORDER DATE: {payload.get('order_date', {}).get('text', '')}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(
        0,
        8,
        "ESTIMATED SHIPMENT DATE: "
        f"{payload.get('shipment_start', {}).get('text', '')} - {payload.get('shipment_end', {}).get('text', '')}",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.cell(0, 8, f"INVOICE #: # {payload.get('invoice_number', '')}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"AMOUNT ($): ${payload.get('amount', '0.00')}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"PAYMENT RECEIVED ($): ${payload.get('payment_received', '0.00')}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"BALANCE ($): ${payload.get('balance', '0.00')}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"PAYMENT STATUS: {payload.get('payment_status', '')}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"PAYMENT MODE: {payload.get('payment_mode', '')}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"PAID ON: {payload.get('paid_on', '')}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"TRANSACTION ID: {payload.get('transaction_id', '')}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "SHIPPING ADDRESS", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    pdf.cell(
        0,
        8,
        f"NAME: {payload.get('shipping_name', '')}    MOBILE: {payload.get('shipping_mobile', '')}",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.cell(0, 8, f"SHIPPING ADDRESS: {payload.get('shipping_address', '')}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(
        0,
        8,
        "CITY: {city}    ZIP CODE: {zip_code}".format(
            city=payload.get("shipping_city", ""),
            zip_code=payload.get("shipping_zip_code", ""),
        ),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.cell(
        0,
        8,
        "STATE: {state}    COUNTRY: {country}".format(
            state=payload.get("shipping_state", ""),
            country=payload.get("shipping_country", ""),
        ),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    return bytes(pdf.output())


def _render_playwright_pdf(html: str) -> bytes:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            return page.pdf(format="A4", print_background=True)
        finally:
            browser.close()


APPROVAL_PARAGRAPHS = [
    "Please check all the information and images provided for absolute accuracy before approving. All information on this sheet will be considered correct after your approval is received.",
    "Please note: Due to variances, this is to be used as an approximation of garment style, logo, and number location and color. The renders are not to be used for color reference.",
    "Due to different screen resolutions and device configurations, the colour shown on kit builder may vary after the sublimation process.",
    "NORMAL LEAD TIMES ARE 3 WEEKS FROM THE DATE OF ORDER CONFIRMATION",
    "IRA Sportswear is not responsible for delays due to Governmental Restrictions that occur because of Pandemics, Natural Disasters and any Shipping delays by Courier services or delays in Customs. With that said we have built our business by providing quality on time shipments of orders so we will always do whatever is in our control to deliver orders on time.",
    "Production begins the next business day after the invoice has been paid and all details and artwork mockups have been confirmed by email.",
    "Additional players added to order once production begins is considered an ADD ON ORDER. We suggest accounting for Sub Players and Add On players when placing the original order. Add On Orders will always follow the same lead times as regular order as noted above.",
]

INVOICE_SHIPPING_COPY = [
    "We will ship your order within 2-3 weeks from the date of confirmation.",
    "Please note that the estimated shipment date may vary depending on the complexity and overall volume of your order.",
]

INVOICE_WHAT_NEXT_STEPS = [
    "Our team will carefully review your order details.",
    "If any clarification is required, we will contact you promptly.",
    "Our design team will prepare the final artwork.",
    "The final artwork and a printed fabric sample will be shared for your approval prior to bulk production.",
    "The design and sampling process typically requires 2-3 business days.",
]

INVOICE_FOOTER = "Bulk production will commence upon receipt of your formal approval."


def _split_design_specs(specs, image_attachments):
    accessory_names = {"cap", "baggy cap", "hat", "helmat clad", "helmet clad", "pad clad"}
    grouped = {}
    for spec in specs:
        name = (spec.garment_type or "").strip().lower()
        if name in accessory_names:
            kind = "accessory"
        else:
            kind = "trouser" if any(k in name for k in ["trouser", "pant", "short"]) else "tshirt"
        sleeve_key = (spec.sleeve_type or "").strip().upper()
        # Jersey-style designs should be shared across sleeves by default:
        # do not split design pages by HALF/FULL/3/4 for the same product.
        if _merge_design_across_sleeves(name):
            sleeve_key = ""
        base_key = (
            kind,
            (spec.garment_type or "").strip().upper(),
            sleeve_key,
        )
        grouped.setdefault(base_key, []).append(spec)

    final_grouped = {}
    for base_key, bucket in grouped.items():
        # If all genders share same effective design content, collapse to one page.
        signatures = {_design_signature(s) for s in bucket}
        if len(signatures) == 1:
            best = max(bucket, key=_spec_quality)
            key = base_key + ("COMMON",)
            final_grouped[key] = best
            continue
        # Otherwise keep per-gender split.
        for spec in bucket:
            key = base_key + ((spec.gender or "").strip().upper(),)
            current = final_grouped.get(key)
            if current is None or _spec_quality(spec) > _spec_quality(current):
                final_grouped[key] = spec

    tshirt_specs = []
    trouser_specs = []
    accessory_specs = []
    for (kind, _, sleeve_key, grouped_gender), spec in final_grouped.items():
        if not _spec_has_export_content(spec):
            continue
        view_spec = _build_export_spec_view(spec, image_attachments)
        if grouped_gender == "COMMON":
            view_spec.gender = ""
        if not sleeve_key:
            view_spec.sleeve_type = ""
        if kind == "trouser":
            trouser_specs.append(view_spec)
        elif kind == "accessory":
            accessory_specs.append(view_spec)
        else:
            tshirt_specs.append(view_spec)

    tshirt_specs.sort(key=lambda s: ((s.garment_type or "").lower(), (s.gender or "").lower(), (s.sleeve_type or "").lower(), s.id or 0))
    trouser_specs.sort(key=lambda s: ((s.garment_type or "").lower(), (s.gender or "").lower(), (s.sleeve_type or "").lower(), s.id or 0))
    accessory_specs.sort(key=lambda s: ((s.garment_type or "").lower(), (s.gender or "").lower(), (s.sleeve_type or "").lower(), s.id or 0))
    return tshirt_specs, trouser_specs, accessory_specs


def _design_signature(spec):
    garment_name = _normalize_sig_value(getattr(spec, "garment_type", ""))
    sleeve_value = _normalize_sig_value(getattr(spec, "sleeve_type", ""))
    if _merge_design_across_sleeves(garment_name):
        sleeve_value = ""
    return (
        garment_name,
        sleeve_value,
        _normalize_sig_value(getattr(spec, "style_number", "")),
        _normalize_sig_value(getattr(spec, "collar_type", "")),
        _normalize_sig_value(getattr(spec, "fabric", "")),
        _normalize_sig_value(getattr(spec, "panel_color_primary", "")),
        _normalize_sig_value(getattr(spec, "panel_color_secondary", "")),
        _normalize_sig_value(getattr(spec, "logo_positions", "")),
        _normalize_sig_value(getattr(spec, "front_image_path", "")),
        _normalize_sig_value(getattr(spec, "right_image_path", "")),
        _normalize_sig_value(getattr(spec, "back_image_path", "")),
        _normalize_sig_value(getattr(spec, "left_image_path", "")),
        _normalize_sig_value(getattr(spec, "logo_right_path", "")),
        _normalize_sig_value(getattr(spec, "logo_left_path", "")),
        _normalize_sig_value(getattr(spec, "logo_front_path", "")),
        _normalize_sig_value(getattr(spec, "logo_back_path", "")),
        _normalize_sig_value(getattr(spec, "design_notes", "")),
        _normalize_sig_value(getattr(spec, "production_notes", "")),
    )


def _normalize_sig_value(value):
    text = str(value or "").strip().lower()
    if text in {"none", "na", "n/a", "nil", "null"}:
        return ""
    return re.sub(r"\s+", " ", text)


def _merge_design_across_sleeves(product_name: str) -> bool:
    name = (product_name or "").strip().lower()
    return ("jersey" in name) or ("tshirt" in name) or ("t shirt" in name)


def _build_accessory_quantity_map(accessories):
    out = {}
    for accessory in accessories or []:
        key = _normalize_accessory_name(getattr(accessory, "product_name", ""))
        out[key] = out.get(key, 0) + _safe_int(getattr(accessory, "quantity", 0))
    return out


def _normalize_accessory_name(name):
    raw = (name or "").strip().lower()
    if raw in {"helmat clad"}:
        return "helmet clad"
    return raw


def _spec_has_export_content(spec):
    core = [
        spec.style_number,
        spec.collar_type,
        spec.fabric,
        spec.panel_color_primary,
        spec.panel_color_secondary,
        spec.logo_positions,
        spec.front_image_path,
        spec.right_image_path,
        spec.back_image_path,
        spec.left_image_path,
        spec.logo_right_path,
        spec.logo_left_path,
        spec.logo_front_path,
        spec.logo_back_path,
    ]
    return any(_is_meaningful(v) for v in core)


def _spec_quality(spec):
    fields = [
        spec.style_number,
        spec.collar_type,
        spec.fabric,
        spec.panel_color_primary,
        spec.panel_color_secondary,
        spec.logo_positions,
        spec.front_image_path,
        spec.right_image_path,
        spec.back_image_path,
        spec.left_image_path,
        spec.logo_right_path,
        spec.logo_left_path,
        spec.logo_front_path,
        spec.logo_back_path,
    ]
    return sum(1 for v in fields if _is_meaningful(v))


def _is_meaningful(value):
    text = (value or "").strip()
    if not text:
        return False
    return text.lower() not in {"none", "na", "n/a", "nil"}


def _collect_common_notes(specs, field_name):
    unique = []
    for spec in specs:
        value = (getattr(spec, field_name, None) or "").strip()
        if value and value not in unique:
            unique.append(value)
    return "\n\n".join(unique)


def _build_packaging_rows(players):
    rows = []
    for idx, p in enumerate(players, start=1):
        tshirt_qty = _safe_int(getattr(p, "tshirt_qty", 0))
        trouser_qty = _safe_int(getattr(p, "trouser_qty", 0))
        set_qty = min(tshirt_qty, trouser_qty)
        extra_tshirt_qty = max(tshirt_qty - set_qty, 0)
        extra_trouser_qty = max(trouser_qty - set_qty, 0)
        rows.append(
            {
                "serial_no": idx,
                "row_number": p.row_number,
                "player_name": p.player_name,
                "number": p.number,
                "sleeve_type": p.sleeve_type,
                "tshirt_size": p.tshirt_size,
                "tshirt_qty": tshirt_qty,
                "trouser_size": p.trouser_size,
                "trouser_qty": trouser_qty,
                "set_qty": set_qty,
                "extra_tshirt_qty": extra_tshirt_qty,
                "extra_trouser_qty": extra_trouser_qty,
            }
        )
    return rows


def _file_uri(path_value):
    if not path_value:
        return None
    try:
        if not exists(path_value):
            current_app.logger.debug(
                "PDF image path missing | original=%s",
                str(path_value),
            )
            return None
        suffix = Path(str(path_value)).suffix.lower()
        data = read_bytes(path_value)
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(suffix)
        if not mime:
            head = data[:32]
            if head.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif head.startswith(b"\xff\xd8\xff"):
                mime = "image/jpeg"
            elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                mime = "image/webp"
            elif head.startswith((b"GIF87a", b"GIF89a")):
                mime = "image/gif"
        if mime:
            encoded = base64.b64encode(data).decode("ascii")
            return f"data:{mime};base64,{encoded}"
        raw_path = Path(str(path_value))
        if raw_path.is_absolute():
            return raw_path.resolve().as_uri()
        return None
    except Exception as exc:
        current_app.logger.debug("PDF image path resolve error | original=%s error=%s", str(path_value), str(exc))
        return None


def _ira_logo_uri():
    static_dir = current_app.static_folder
    if not static_dir:
        return None
    # Keep the primary brand asset first so exports match the approved sample.
    for name in ("ira-brand-logo.png", "ira-logo-new.png", "ira-logo.png"):
        logo_path = Path(static_dir) / "img" / name
        if logo_path.exists():
            return _file_uri(str(logo_path.resolve()))
    return None


def _ira_invoice_logo_uri():
    # Invoice must use the same red logo style as the sample/order-sheet.
    static_dir = current_app.static_folder
    if not static_dir:
        return None
    primary = Path(static_dir) / "img" / "ira-brand-logo.png"
    if primary.exists():
        return _file_uri(str(primary.resolve()))
    return _ira_logo_uri()


def _build_export_spec_view(spec, image_attachments):
    slot_paths = _extract_slot_paths(spec, image_attachments)
    logo_front = spec.logo_front_path
    logo_right = spec.logo_right_path
    logo_left = spec.logo_left_path
    logo_back = spec.logo_back_path

    front_left_chest_path = slot_paths.get("front_left_chest_logo")
    front_right_chest_path = slot_paths.get("front_right_chest_logo")
    front_sponser_path = slot_paths.get("front_sponser_logo") or slot_paths.get("front_sponsor_logo")
    right_up_path = slot_paths.get("right_up_logo")
    right_down_path = slot_paths.get("right_down_logo")
    left_up_path = slot_paths.get("left_up_logo")
    left_down_path = slot_paths.get("left_down_logo")
    back_flag_path = slot_paths.get("back_flag_logo")
    back_main_path = slot_paths.get("back_logo")
    back_bottom_path = slot_paths.get("back_bottom_logo")

    if not logo_front:
        logo_front = front_sponser_path or slot_paths.get("front_front_side_logo")
    if not logo_right:
        logo_right = slot_paths.get("right_side_logo") or slot_paths.get("front_right_side_logo")
    if not logo_left:
        logo_left = slot_paths.get("left_side_logo") or slot_paths.get("front_left_side_logo")
    if not logo_back:
        logo_back = (
            slot_paths.get("back_logo")
            or slot_paths.get("back_flag_logo")
            or slot_paths.get("back_bottom_logo")
        )

    return SimpleNamespace(
        id=spec.id,
        garment_type=spec.garment_type,
        gender=spec.gender,
        sleeve_type=spec.sleeve_type,
        style_number=spec.style_number,
        collar_type=spec.collar_type,
        fabric=_canonical_fabric_name(spec.fabric),
        panel_color_primary=spec.panel_color_primary,
        panel_color_secondary=spec.panel_color_secondary,
        logo_positions=spec.logo_positions,
        front_image_path=spec.front_image_path,
        right_image_path=spec.right_image_path,
        back_image_path=spec.back_image_path,
        left_image_path=spec.left_image_path,
        logo_front_path=logo_front,
        logo_right_path=logo_right,
        logo_left_path=logo_left,
        logo_back_path=logo_back,
        logo_front_left_chest_path=front_left_chest_path,
        logo_front_right_chest_path=front_right_chest_path,
        logo_front_sponser_path=front_sponser_path,
        logo_right_up_path=right_up_path,
        logo_right_down_path=right_down_path,
        logo_left_up_path=left_up_path,
        logo_left_down_path=left_down_path,
        logo_back_flag_path=back_flag_path,
        logo_back_logo_path=back_main_path,
        logo_back_bottom_path=back_bottom_path,
        design_notes=spec.design_notes,
        production_notes=spec.production_notes,
    )


def _extract_slot_paths(spec, image_attachments):
    known_slots = [
        "front_left_chest_logo",
        "front_right_chest_logo",
        "front_sponser_logo",
        "front_sponsor_logo",
        "front_front_side_logo",
        "front_right_side_logo",
        "front_left_side_logo",
        "right_up_logo",
        "right_down_logo",
        "right_side_logo",
        "left_up_logo",
        "left_down_logo",
        "left_side_logo",
        "back_logo",
        "back_flag_logo",
        "back_bottom_logo",
    ]
    out = {}
    for slot in known_slots:
        path = _find_attachment_for_slot(spec, image_attachments, slot)
        if path:
            out[slot] = path
    return out


def _find_attachment_for_slot(spec, image_attachments, slot):
    prefixes = _slot_prefixes(spec, slot)
    for attachment in image_attachments:
        name = str(getattr(attachment, "filename", "") or "").lower()
        if any(name.startswith(prefix) for prefix in prefixes):
            return getattr(attachment, "storage_path", None)
    return None


def _slot_prefixes(spec, slot):
    garment = _slug_token(getattr(spec, "garment_type", ""))
    gender = str(getattr(spec, "gender", "") or "").strip().lower()
    sleeve = _slug_token(getattr(spec, "sleeve_type", "") or "na")
    slot = str(slot or "").strip().lower()
    if not garment or not slot:
        return []

    prefixes = []
    if gender and sleeve:
        prefixes.append(f"{garment}-{gender}-sleeve-{sleeve}-logo-{slot}-")
    if gender:
        prefixes.append(f"{garment}-{gender}-logo-{slot}-")
    if sleeve:
        prefixes.append(f"{garment}-sleeve-{sleeve}-logo-{slot}-")
    prefixes.append(f"{garment}-logo-{slot}-")
    return prefixes


def _slug_token(value):
    return "".join(c.lower() if c.isalnum() else "_" for c in str(value or "")).strip("_")


def _safe_int(value):
    try:
        return int(value or 0)
    except (ValueError, TypeError):
        return 0


def save_order_pdf(order, pdf_bytes):
    safe_slug = str(order.order_id or f"order-{order.id}").strip()
    pattern = re.compile(rf"^{re.escape(safe_slug)}-V(\d+)\.pdf$", re.IGNORECASE)
    max_version = 0
    existing_exports = Attachment.query.filter_by(order_id=order.id, mime_type="application/pdf").all()
    for row in existing_exports:
        match = pattern.match(str(getattr(row, "filename", "") or "").strip())
        if not match:
            continue
        try:
            max_version = max(max_version, int(match.group(1)))
        except (TypeError, ValueError):
            continue

    filename = f"{safe_slug}-V{max_version + 1}.pdf"
    storage_path = save_order_file(
        order.id,
        ORDER_SHEET_SECTION,
        filename,
        pdf_bytes,
        content_type="application/pdf",
    )

    attachment = Attachment(
        order_id=order.id,
        filename=filename,
        mime_type="application/pdf",
        storage_path=storage_path,
    )
    return attachment


def save_plan_pdf(order, pdf_bytes: bytes, plan_slug: str, display_order_id: str | None = None):
    safe_slug = str(plan_slug or "plan").strip().lower().replace(" ", "-")
    resolved_display_order_id = str(display_order_id or order.order_id or "").strip()
    pattern = re.compile(
        rf"^{re.escape(safe_slug)}-{re.escape(resolved_display_order_id)}-V(\d+)\.pdf$",
        re.IGNORECASE,
    )
    max_version = 0
    existing = Attachment.query.filter_by(order_id=order.id, mime_type="application/pdf").all()
    for row in existing:
        name = str(getattr(row, "filename", "") or "").strip()
        match = pattern.match(name)
        if not match:
            continue
        try:
            max_version = max(max_version, int(match.group(1)))
        except (TypeError, ValueError):
            continue
    next_version = max_version + 1
    filename = f"{safe_slug}-{resolved_display_order_id}-V{next_version}.pdf"
    storage_path = save_order_file(
        order.id,
        ORDER_SHEET_SECTION,
        filename,
        pdf_bytes,
        content_type="application/pdf",
    )
    return Attachment(
        order_id=order.id,
        filename=filename,
        mime_type="application/pdf",
        storage_path=storage_path,
    )


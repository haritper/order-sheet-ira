from __future__ import annotations

import base64
import mimetypes
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path

from flask import current_app, render_template
import fitz

try:
    from weasyprint import HTML
except Exception:  # pragma: no cover
    HTML = None

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None


SIZE_KEYS = ["XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL", "YXXS", "YXS", "YS", "YM", "YL", "YXL"]
ADULT_SIZES = ["XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL"]
WOMENS_SIZES = ["WXS", "WS", "WM", "WL", "WXL", "W2XL", "W3XL", "W4XL"]
YOUTH_SIZES = ["YXXS", "YXS", "YS", "YM", "YL", "YXL"]


STYLE_MAP = {
    "trouser": "Trousers",
    "travel_trouser": "Travel Trousers",
    "umpires": "Umpires",
    "shorts": "Shorts",
    "jacket": "Jacket",
    "hoodie": "Hoodie",
    "sweatshirt": "Sweatshirt",
    "travel_polo": "Travel Polo",
    "helmet_clad": "Helmet Clad",
    "pad_clad": "Pad Clad",
}


FABRIC_MAP = {
    "Trousers": ("Corsa 220 GSM", "EnduroKnit 220"),
    "Travel Trousers": ("Scuba 330 GSM", "WarmShield 330"),
    "Umpires": ("Interlock180 GSM", "CoachDry 200"),
    "Shorts": ("Interlock 180 GSM", "FlexCore 180"),
    "Jacket": ("Scuba 330 GSM", "WarmShield 330"),
    "Hoodie": ("Terry 330 GSM", "TerryShield 330"),
    "Sweatshirt": ("Fleece 330 GSM", "TerryShield Fleece 330"),
    "Travel Polo": ("Mars 200 GSM (Solid)", "VersaDry 200"),
    "Helmet Clad": ("zurich fabric 120 GSM", "FlexGuard 120"),
    "Pad Clad": ("zurich fabric 120 GSM", "FlexGuard 120"),
}

IRA_FABRIC_NAME_MAP = {
    "interlock 160 gsm": "GameSkin 160",
    "jacquard 160 gsm": "JacquardPro 160",
    "pinmesh 150 gsm": "AeroMesh 150",
    "corsa 140 gsm": "LiteFlow 140",
    "corsa 220 gsm": "EnduroKnit 220",
    "interlock 180 gsm": "FlexCore 180",
    "ns lycra": "NS Elite 160",
    "corsa 180 gsm": "Classic White 180",
    "scuba": "WarmShield 330",
    "scuba 220 gsm": "Heritage White 220",
    "scuba 330 gsm": "WarmShield 330",
    "terry 330 gsm": "TerryShield 330",
    "fleece 330 gsm": "TerryShield Fleece 330",
    "interlock180 gsm": "CoachDry 200",
    "pin mesh 140 gsm": "AirGrid 140",
    "cap knit cordsandwich 120 gsm": "ProCap 120",
    "zurich fabric 120 gsm": "FlexGuard 120",
    "mars 200 gsm solid": "VersaDry 200",
    "mars 200 gsm melange": "VersaDry Melange 200",
}


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _status_match(status_val):
    if isinstance(status_val, bool):
        return status_val
    txt = str(status_val or "").strip().lower()
    return txt in {"match", "matched"}


def _empty_sizes():
    return {k: 0 for k in SIZE_KEYS}


def _design_key_candidates(source_key: str) -> list[str]:
    src = str(source_key or "").strip().lower()
    if not src:
        return []
    out = [src]
    stripped = src
    for prefix in ("mens_", "womens_", "youth_"):
        if src.startswith(prefix):
            stripped = src[len(prefix):]
            out.append(stripped)
            break
    if stripped in {"half_sleeve", "full_sleeve"}:
        out.extend(["jersey", "playing_jersey", "training_jersey"])
    if stripped == "travel_trouser":
        out.extend(["travel_trouser", "trouser", "trousers"])
    if stripped == "trouser":
        out.extend(["trouser", "trousers", "travel_trouser"])
    if src == "travel_polo":
        out.append("polo")

    deduped = []
    seen = set()
    for key in out:
        if key not in seen:
            seen.add(key)
            deduped.append(key)
    return deduped


def _extract_colour_pattern(parsed: dict, source_key: str):
    design = parsed.get("design_checklist", {})
    if isinstance(design, dict):
        for candidate in _design_key_candidates(source_key):
            item = design.get(candidate)
            if isinstance(item, dict):
                colour = str(item.get("colour") or item.get("color") or item.get("primary_color") or "").strip()
                pattern = str(item.get("pattern") or "").strip()
                if colour or pattern:
                    return colour, pattern

    products = parsed.get("products", [])
    if not isinstance(products, list):
        return "", ""

    key = str(source_key or "").lower()
    tokens = [key]
    if "trouser" in key:
        tokens = ["trouser", "pant"]
    elif key == "travel_polo":
        tokens = ["travel", "polo"]
    elif key == "helmet_clad":
        tokens = ["helmet", "clad"]
    elif key == "pad_clad":
        tokens = ["pad", "clad"]

    for p in products:
        if not isinstance(p, dict):
            continue
        pname = str(p.get("product_name") or p.get("name") or p.get("product") or "").lower()
        if pname and not any(t in pname for t in tokens):
            continue
        colour = str(p.get("colour") or p.get("color") or "").strip()
        pattern = str(p.get("pattern") or "").strip()
        if colour or pattern:
            return colour, pattern

    return "", ""


def _extract_style_type_as_pattern(parsed: dict, source_key: str) -> str:
    design = parsed.get("design_checklist", {})
    if isinstance(design, dict):
        for candidate in _design_key_candidates(source_key):
            item = design.get(candidate)
            if isinstance(item, dict):
                style_type = str(item.get("style_type") or item.get("collar_type") or "").strip()
                if style_type:
                    return style_type
    return ""


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


def _map_to_ira_fabric_name(value: str) -> str:
    raw = _canonical_fabric_name(value)
    if not raw:
        return ""
    mapped = IRA_FABRIC_NAME_MAP.get(_normalize_fabric_key(raw))
    return mapped or raw


def _extract_fabric_value(parsed: dict, source_key: str) -> str:
    design = parsed.get("design_checklist", {})
    if isinstance(design, dict):
        for candidate in _design_key_candidates(source_key):
            item = design.get(candidate)
            if isinstance(item, dict):
                fabric = str(item.get("fabric") or "").strip()
                if fabric:
                    return _canonical_fabric_name(fabric)

    products = parsed.get("products", [])
    if not isinstance(products, list):
        return ""
    for p in products:
        if not isinstance(p, dict):
            continue
        pkey = str(p.get("product_key") or p.get("key") or "").strip().lower()
        if pkey and pkey not in _design_key_candidates(source_key):
            continue
        fabric = str(p.get("fabric") or "").strip()
        if fabric:
            return _canonical_fabric_name(fabric)
    return ""


def _normalize_size_for_cutting(size_token, source_key):
    value = str(size_token or "").strip().upper()
    if source_key.startswith("youth_"):
        return value
    youth_to_adult = {
        "YXXS": "XS",
        "YXS": "XS",
        "YS": "S",
        "YM": "M",
        "YL": "L",
        "YXL": "XL",
    }
    return youth_to_adult.get(value, value)


def _style_from_source_key(source_key: str) -> str:
    src = str(source_key or "").strip().lower()
    if not src:
        return ""
    base = src
    for prefix in ("mens_", "womens_", "youth_"):
        if base.startswith(prefix):
            base = base[len(prefix):]
            break
    if base in {"travel_trouser", "travel trouser", "travel_touser"}:
        base = "travel_trouser"
    elif base in {"pant", "pants"}:
        base = "trouser"
    return STYLE_MAP.get(base, "")


def normalize_cutting_plan(parsed: dict | None, default_order_id: str = "", default_enquiry_date: str = ""):
    empty = {"rows": [], "summary": {"total_cutting_qty": 0}}
    if not isinstance(parsed, dict):
        return empty

    raw_cp = parsed.get("cutting_plan", {})
    if isinstance(raw_cp, dict):
        existing_rows = raw_cp.get("rows", [])
        if isinstance(existing_rows, list) and existing_rows:
            normalized_rows = []
            for row in existing_rows:
                if not isinstance(row, dict):
                    continue
                normalized = dict(row)
                fabric = _canonical_fabric_name(normalized.get("fabric", ""))
                normalized["fabric"] = fabric
                if fabric:
                    normalized["ira_fabric_name"] = _map_to_ira_fabric_name(fabric)
                normalized_rows.append(normalized)
            total_qty = sum(_to_int((r or {}).get("total"), 0) for r in normalized_rows if isinstance(r, dict))
            return {"rows": normalized_rows, "summary": {"total_cutting_qty": total_qty}}

    quantity_comparison = parsed.get("quantity_comparison", {})
    if not isinstance(quantity_comparison, dict):
        quantity_comparison = {}

    order_meta = parsed.get("order_metadata", {})
    order_id = str(order_meta.get("order_id", "") if isinstance(order_meta, dict) else "") or str(default_order_id or "")
    enquiry_date = str(order_meta.get("enquiry_date", "") if isinstance(order_meta, dict) else "") or str(default_enquiry_date or "")

    rows = []
    for source_key, product_data in quantity_comparison.items():
        if source_key == "accessories" or not isinstance(product_data, dict):
            continue

        style = _style_from_source_key(source_key)
        if not style or source_key in {"helmet_clad", "pad_clad"}:
            continue

        sizes = _empty_sizes()
        for size, comp in product_data.items():
            if not isinstance(comp, dict):
                continue
            overview = _to_int(comp.get("overview"), 0)
            packing = _to_int(comp.get("packing"), 0)
            status_ok = _status_match(comp.get("status")) and overview == packing
            if not status_ok or packing <= 0:
                continue
            raw_size = str(size).strip().upper()
            norm_size = _normalize_size_for_cutting(raw_size, source_key)
            if norm_size in sizes:
                sizes[norm_size] += packing
            elif raw_size in sizes:
                sizes[raw_size] += packing

        total = sum(sizes.values())
        if total <= 0:
            continue

        default_fabric, default_ira_fabric_name = FABRIC_MAP[style]
        fabric = _extract_fabric_value(parsed, source_key) or _canonical_fabric_name(default_fabric)
        ira_fabric_name = _map_to_ira_fabric_name(fabric) or default_ira_fabric_name
        colour, pattern = _extract_colour_pattern(parsed, source_key)
        if not pattern:
            pattern = _extract_style_type_as_pattern(parsed, source_key)

        rows.append(
            {
                "order_id": order_id,
                "enquiry_date": enquiry_date,
                "source_product": source_key,
                "style": style,
                "fabric": fabric,
                "ira_fabric_name": ira_fabric_name,
                "colour": colour,
                "pattern": pattern,
                "sizes": sizes,
                "total": total,
                "cutting_person": "",
                "cut_date": "",
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        )

    accessories = quantity_comparison.get("accessories", {})
    if isinstance(accessories, dict):
        for source_key in ("helmet_clad", "pad_clad"):
            item = accessories.get(source_key)
            if not isinstance(item, dict):
                continue
            overview = _to_int(item.get("overview"), 0)
            packing = _to_int(item.get("packing"), 0)
            status_ok = _status_match(item.get("status")) and overview == packing
            if not status_ok or packing <= 0:
                continue
            style = _style_from_source_key(source_key) or STYLE_MAP.get(source_key)
            if not style:
                continue
            default_fabric, default_ira_fabric_name = FABRIC_MAP[style]
            fabric = _extract_fabric_value(parsed, source_key) or _canonical_fabric_name(default_fabric)
            ira_fabric_name = _map_to_ira_fabric_name(fabric) or default_ira_fabric_name
            colour, pattern = _extract_colour_pattern(parsed, source_key)
            if not pattern:
                pattern = _extract_style_type_as_pattern(parsed, source_key)
            sizes = _empty_sizes()
            sizes["M"] = packing
            rows.append(
                {
                    "order_id": order_id,
                    "enquiry_date": enquiry_date,
                    "source_product": source_key,
                    "style": style,
                    "fabric": fabric,
                    "ira_fabric_name": ira_fabric_name,
                    "colour": colour,
                    "pattern": pattern,
                    "sizes": sizes,
                    "total": packing,
                    "cutting_person": "",
                    "cut_date": "",
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
            )

    total_cutting_qty = sum(_to_int(r.get("total"), 0) for r in rows)
    return {"rows": rows, "summary": {"total_cutting_qty": total_cutting_qty}}


def has_cutting_plan_rows(cutting_plan: dict | None) -> bool:
    if not isinstance(cutting_plan, dict):
        return False
    rows = cutting_plan.get("rows", [])
    return isinstance(rows, list) and len(rows) > 0


def _format_enquiry_date(_created_raw: str) -> str:
    return datetime.now().strftime("%d %B %Y").upper()


def _display_title(source_product: str, style: str, source_product_name: str = "") -> str:
    src = str(source_product or "").lower()
    if src.startswith("mens_") or src.startswith("men_"):
        prefix = "Men's "
    elif src.startswith("womens_") or src.startswith("women_"):
        prefix = "Women's "
    elif src.startswith("youth_"):
        prefix = "Youth "
    else:
        prefix = "Men's "
    main = str(source_product_name or style or "").strip()
    return prefix + main


def _front_image_path(path_value: str) -> str:
    raw = str(path_value or "").strip()
    if not raw:
        return ""
    try:
        candidate = Path(raw).expanduser().resolve()
    except Exception:
        return ""
    if not candidate.exists() or not candidate.is_file():
        return ""
    mime, _ = mimetypes.guess_type(str(candidate))
    if not mime or not mime.startswith("image/"):
        return ""
    return str(candidate)


def _front_image_uri(path_value: str) -> str:
    resolved = _front_image_path(path_value)
    if not resolved:
        return ""
    try:
        mime, _ = mimetypes.guess_type(resolved)
        payload = base64.b64encode(Path(resolved).read_bytes()).decode("ascii")
        return f"data:{mime};base64,{payload}"
    except Exception:
        return ""


def _prepare_template_rows(cutting_plan: dict) -> list[dict]:
    rows = cutting_plan.get("rows", []) if isinstance(cutting_plan, dict) else []
    prepared = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sizes = row.get("sizes") if isinstance(row.get("sizes"), dict) else {}
        source_product = str(row.get("source_product", "") or "").strip().lower()
        if source_product.startswith("womens_"):
            primary_headers = WOMENS_SIZES
        elif source_product.startswith("youth_"):
            primary_headers = YOUTH_SIZES
        else:
            primary_headers = ADULT_SIZES

        adult_values = [_to_int(sizes.get(k), 0) for k in primary_headers]
        youth_values = [_to_int(sizes.get(k), 0) for k in YOUTH_SIZES]
        adult_total = sum(adult_values)
        youth_total = sum(youth_values)
        total = _to_int(row.get("total"), 0)
        if total <= 0:
            total = adult_total + youth_total

        prepared.append(
            {
                "order_id": str(row.get("order_id", "") or ""),
                "display_title": _display_title(
                    str(row.get("source_product", "") or ""),
                    str(row.get("style", "") or ""),
                    str(row.get("source_product_name", "") or ""),
                ),
                "enquiry_date": _format_enquiry_date(str(row.get("created_at", "") or "")),
                "style": str(row.get("style_type_display", "") or str(row.get("style", "") or "")),
                "fabric": str(row.get("fabric", "") or ""),
                "colour": str(row.get("colour", "") or ""),
                "pattern": str(row.get("pattern", "") or ""),
                "cutting_person": str(row.get("cutting_person", "") or ""),
                "cut_date": str(row.get("cut_date", "") or ""),
                "adult_headers": list(primary_headers) + ["TOTAL"],
                "adult_values": adult_values + [adult_total],
                "show_youth": (primary_headers != YOUTH_SIZES) and (youth_total > 0),
                "youth_headers": list(YOUTH_SIZES) + ["TOTAL"],
                "youth_values": youth_values + [youth_total],
                "total": total,
                "front_image_uri": _front_image_uri(str(row.get("front_image_path", "") or "")),
            }
        )
    return prepared


def _render_playwright_pdf(html_content: str) -> bytes:
    if sync_playwright is None:
        raise RuntimeError("Playwright is not available.")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, timeout=15000)
        try:
            page = browser.new_page()
            page.set_content(html_content, wait_until="load")
            return page.pdf(format="A4", print_background=True)
        finally:
            browser.close()


def _pdf_has_visible_content(pdf_bytes: bytes) -> bool:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return False
    try:
        for page in doc:
            if page.get_text("text").strip():
                return True
    finally:
        doc.close()
    return False


def _render_fitz_html_fallback(rows: list[dict]) -> bytes:
    doc = fitz.open()
    page_w, page_h = 595, 842  # A4 points
    margin = 16
    for row in rows or [{}]:
        page = doc.new_page(width=page_w, height=page_h)
        html_content = render_template("exports/cutting_plan_pdf.html", rows=[row] if row else [])
        page.insert_htmlbox(
            fitz.Rect(margin, margin, page_w - margin, page_h - margin),
            html_content,
            scale_low=0.95,
        )
    data = doc.tobytes()
    doc.close()
    return data


def build_cutting_plan_pdf(cutting_plan: dict) -> bytes:
    rows = _prepare_template_rows(cutting_plan)
    html_content = render_template("exports/cutting_plan_pdf.html", rows=rows)

    if HTML is not None:
        try:
            pdf_bytes = HTML(string=html_content, base_url=str(Path.cwd())).write_pdf()
            if _pdf_has_visible_content(pdf_bytes):
                return pdf_bytes
            current_app.logger.warning("Cutting plan WeasyPrint output was blank; trying fallback renderer.")
        except Exception as exc:  # pragma: no cover
            current_app.logger.exception("Cutting plan WeasyPrint render failed: %s", exc)

    if sync_playwright is not None:
        try:
            pdf_bytes = _render_playwright_pdf(html_content)
            if _pdf_has_visible_content(pdf_bytes):
                return pdf_bytes
            current_app.logger.warning("Cutting plan Playwright output was blank; trying fitz fallback.")
        except Exception as exc:  # pragma: no cover
            current_app.logger.exception("Cutting plan Playwright render failed: %s", exc)

    return _render_fitz_html_fallback(rows)


def to_pdf_io(cutting_plan: dict) -> BytesIO:
    bio = BytesIO(build_cutting_plan_pdf(cutting_plan))
    bio.seek(0)
    return bio

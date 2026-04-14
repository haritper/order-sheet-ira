from __future__ import annotations

import re
from typing import Any, Dict, List

from app.orders.ai_services import normalize_ai_payload


GARMENT_CATEGORY_MAP = {
    "mens_": "MENS",
    "womens_": "WOMENS",
    "youth_": "YOUTH",
}

CORE_GARMENT_KEYS = {
    "half_sleeve",
    "full_sleeve",
    "trouser",
}


def _detect_gender_from_key(product_key: str) -> tuple[str | None, str]:
    raw = str(product_key or "").strip().lower()
    base = raw

    for prefix, cat in GARMENT_CATEGORY_MAP.items():
        if raw.startswith(prefix):
            return cat, raw[len(prefix):]

    # Handle non-prefix styles like travel_polo_mens / polo_womens / youth-jacket
    if any(token in raw for token in ("womens", "women")):
        base = re.sub(r"(^|[_\-\s])(womens|women)([_\-\s]|$)", "_", raw)
        return "WOMENS", re.sub(r"[_\-\s]+", "_", base).strip("_")
    if "youth" in raw:
        base = re.sub(r"(^|[_\-\s])youth([_\-\s]|$)", "_", raw)
        return "YOUTH", re.sub(r"[_\-\s]+", "_", base).strip("_")
    if any(token in raw for token in ("mens", "men")):
        base = re.sub(r"(^|[_\-\s])(mens|men)([_\-\s]|$)", "_", raw)
        return "MENS", re.sub(r"[_\-\s]+", "_", base).strip("_")

    return None, raw


def normalize_ai_result(parsed_json: Dict[str, Any] | None) -> Dict[str, Any]:
    return normalize_ai_payload(parsed_json if isinstance(parsed_json, dict) else {})


def build_garment_cmp(parsed_json: Dict[str, Any] | None) -> Dict[str, Dict[str, Dict[str, Any]]]:
    parsed = normalize_ai_result(parsed_json)
    qc = parsed.get("quantity_comparison") if isinstance(parsed, dict) else {}
    if not isinstance(qc, dict):
        return {}

    out: Dict[str, Dict[str, Dict[str, Any]]] = {
        "MENS": {},
        "WOMENS": {},
        "YOUTH": {},
        "ACCESSORIES": {},
        "OTHERS": {},
    }
    for product_key, value in qc.items():
        if product_key == "accessories" and isinstance(value, dict):
            expected = {k.upper(): int(v.get("overview", 0)) for k, v in value.items() if isinstance(v, dict)}
            actual = {k.upper(): int(v.get("packing", 0)) for k, v in value.items() if isinstance(v, dict)}
            status = {k.upper(): str(v.get("status", "Mismatch")) for k, v in value.items() if isinstance(v, dict)}
            out["ACCESSORIES"]["ACCESSORIES"] = {"expected": expected, "actual": actual, "status": status}
            continue
        if not isinstance(value, dict):
            continue

        category = "OTHERS"
        matched_gender, base_key = _detect_gender_from_key(product_key)

        if matched_gender:
            category = matched_gender
        name = product_key.replace("_", " ").upper()
        expected: Dict[str, int] = {}
        actual: Dict[str, int] = {}
        for size, comp in value.items():
            if not isinstance(comp, dict):
                continue
            expected[size.upper()] = int(comp.get("overview", 0) or 0)
            actual[size.upper()] = int(comp.get("packing", 0) or 0)
        status_map = {
            s: ("Match" if expected.get(s, 0) == actual.get(s, 0) else "Mismatch")
            for s in set(expected) | set(actual)
        }
        if expected or actual:
            out[category][name] = {"expected": expected, "actual": actual, "status": status_map}
    return out


def build_dynamic_products(parsed_json: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    parsed = normalize_ai_result(parsed_json)
    products: List[Dict[str, Any]] = []

    fields_list = parsed.get("design_checklist_fields", [])
    if isinstance(fields_list, list):
        for index, item in enumerate(fields_list, start=1):
            if not isinstance(item, dict):
                continue
            key = str(
                item.get("source_product")
                or item.get("product_key")
                or item.get("product")
                or f"product_{index}"
            ).strip()
            name = str(item.get("product_name") or item.get("product") or key).strip()
            values = item.get("values")
            if not isinstance(values, dict):
                values = {}
            fields = item.get("fields")
            if not isinstance(fields, list):
                fields = [k for k, v in values.items() if str(v).strip().upper() != "NONE"]
            pdf_page = item.get("pdf_page")
            try:
                pdf_page = int(pdf_page) if pdf_page is not None else None
            except (TypeError, ValueError):
                pdf_page = None
            products.append(
                {
                    "key": key,
                    "name": name.replace("_", " ").title(),
                    "fields": fields,
                    "values": values,
                    "pdf_page": pdf_page or (index + 1),
                }
            )

    def _fallback_design_fields(values: Dict[str, Any]) -> List[str]:
        if not isinstance(values, dict):
            return []
        preferred = []
        for key in values.keys():
            token = str(key or "").strip().lower().replace("_", " ")
            if token in {"style", "style type", "styletype"}:
                preferred.append(str(key))
                continue
            if token in {"color", "colour", "primary color", "primary colour", "base color", "base colour"}:
                preferred.append(str(key))
        # preserve order while deduping
        seen = set()
        out: List[str] = []
        for k in preferred:
            lk = k.lower()
            if lk in seen:
                continue
            seen.add(lk)
            out.append(k)
        return out

    if products:
        checklist = parsed.get("design_checklist", {})
        if isinstance(checklist, dict):
            for product in products:
                if product.get("fields"):
                    continue
                key = str(product.get("key") or "").strip().lower()
                candidates = [key]
                if key.endswith("_trouser"):
                    candidates.append("trouser")
                if key.startswith("accessories."):
                    candidates.append(key.split(".", 1)[1])

                recovered_fields: List[str] = []
                recovered_values: Dict[str, Any] = {}
                for candidate in candidates:
                    source = checklist.get(candidate)
                    if isinstance(source, list):
                        recovered_fields = [str(item).strip() for item in source if str(item).strip()]
                        recovered_values = {name: "Present" for name in recovered_fields}
                        break
                    if isinstance(source, dict):
                        for field_name, field_value in source.items():
                            if str(field_value).strip().upper() == "NONE":
                                continue
                            recovered_fields.append(str(field_name))
                            recovered_values[str(field_name)] = field_value
                        if recovered_fields:
                            break

                if recovered_fields:
                    product["fields"] = recovered_fields
                    if not isinstance(product.get("values"), dict) or not product.get("values"):
                        product["values"] = recovered_values
                elif isinstance(product.get("values"), dict):
                    fallback = _fallback_design_fields(product.get("values") or {})
                    if fallback:
                        product["fields"] = fallback
        return products

    checklist = parsed.get("design_checklist", {})
    if isinstance(checklist, dict):
        for index, (key, value) in enumerate(checklist.items(), start=1):
            fields = []
            values: Dict[str, Any] = {}
            if isinstance(value, list):
                for f in value:
                    f_txt = str(f).strip()
                    if not f_txt:
                        continue
                    fields.append(f_txt)
                    values[f_txt] = "Present"
            elif isinstance(value, dict):
                for f, fv in value.items():
                    if str(fv).strip().upper() == "NONE":
                        continue
                    fields.append(str(f))
                    values[str(f)] = fv
            if fields:
                products.append(
                    {
                        "key": str(key),
                        "name": str(key).replace("_", " ").title(),
                        "fields": fields,
                        "values": values,
                        "pdf_page": index + 1,
                    }
                )
    return products

from copy import deepcopy

from app.pipeline.config import PACKING_SECTION_ORDER, SECTION_TO_CATEGORY_SLEEVE, SIZE_SETS


def generate_from_normalized(normalized_rows):
    packing_list = {section: [] for section in PACKING_SECTION_ORDER}
    rejected_rows = []

    for row in normalized_rows:
        reasons = []
        category = row.get("category")
        sleeve = row.get("sleeve_type")
        tshirt_size = row.get("tshirt_size")
        tshirt_qty = row.get("tshirt_qty")

        if category not in SIZE_SETS:
            reasons.append("MISSING_OR_INVALID_CATEGORY")
        if sleeve not in {"HALF", "FULL"}:
            reasons.append("MISSING_OR_INVALID_SLEEVE_TYPE")
        if category in SIZE_SETS and tshirt_size not in SIZE_SETS[category]:
            reasons.append("INVALID_SIZE_FOR_CATEGORY")
        if not isinstance(tshirt_qty, int) or tshirt_qty < 0:
            reasons.append("INVALID_TSHIRT_QTY")

        if reasons:
            rejected_rows.append(
                {
                    "source_row_id": row.get("source_row_id", ""),
                    "reason_codes": sorted(set(reasons)),
                }
            )
            continue

        section = _section_for(category, sleeve)
        target_rows = packing_list[section]
        target_rows.append(
            {
                "s_no": len(target_rows) + 1,
                "player_name": row.get("player_name", ""),
                "number": row.get("number", ""),
                "tshirt_size": tshirt_size,
                "tshirt_qty": tshirt_qty,
                "trouser_size": row.get("trouser_size", ""),
                "trouser_qty": row.get("trouser_qty", 0) if isinstance(row.get("trouser_qty"), int) else 0,
                "source_row_id": row.get("source_row_id", ""),
            }
        )

    order_overview = _build_overview_from_packing(packing_list, normalized_rows)
    return {
        "packing_list": packing_list,
        "order_overview": order_overview,
        "rejected_rows": rejected_rows,
    }


def _section_for(category, sleeve):
    for section, (sec_category, sec_sleeve) in SECTION_TO_CATEGORY_SLEEVE.items():
        if category == sec_category and sleeve == sec_sleeve:
            return section
    raise ValueError(f"No section for {category}/{sleeve}")


def _adult_block(size_keys):
    block = {k: 0 for k in size_keys}
    block["TOTAL"] = 0
    return block


def _build_overview_from_packing(packing_list, normalized_rows):
    overview = {
        "mens": {
            "half_sleeve_tshirt": _adult_block(SIZE_SETS["MENS"]),
            "full_sleeve_tshirt": _adult_block(SIZE_SETS["MENS"]),
            "trouser": _adult_block(SIZE_SETS["MENS"]),
        },
        "womens": {
            "half_sleeve_tshirt": _adult_block(SIZE_SETS["WOMENS"]),
            "full_sleeve_tshirt": _adult_block(SIZE_SETS["WOMENS"]),
            "trouser": _adult_block(SIZE_SETS["WOMENS"]),
        },
        "youth": {
            "half_sleeve_tshirt": _adult_block(SIZE_SETS["YOUTH"]),
            "full_sleeve_tshirt": _adult_block(SIZE_SETS["YOUTH"]),
            "trouser": _adult_block(SIZE_SETS["YOUTH"]),
        },
        "accessories": {
            "CAP": 0,
            "HAT": 0,
            "PAD_CLAD": 0,
            "HELMET_CLAD": 0,
        },
    }

    section_to_block = {
        "mens_half": ("mens", "half_sleeve_tshirt"),
        "mens_full": ("mens", "full_sleeve_tshirt"),
        "womens_half": ("womens", "half_sleeve_tshirt"),
        "womens_full": ("womens", "full_sleeve_tshirt"),
        "youth_half": ("youth", "half_sleeve_tshirt"),
        "youth_full": ("youth", "full_sleeve_tshirt"),
    }

    for section, rows in packing_list.items():
        category_key, tshirt_block_key = section_to_block[section]
        tshirt_block = overview[category_key][tshirt_block_key]
        trouser_block = overview[category_key]["trouser"]

        for row in rows:
            size = row["tshirt_size"]
            qty = row["tshirt_qty"]
            tshirt_block[size] += qty
            tshirt_block["TOTAL"] += qty

            tr_size = row.get("trouser_size")
            tr_qty = row.get("trouser_qty") or 0
            if tr_size in trouser_block and isinstance(tr_qty, int) and tr_qty >= 0:
                trouser_block[tr_size] += tr_qty
                trouser_block["TOTAL"] += tr_qty

    for row in normalized_rows:
        for acc_key, src_key in [
            ("CAP", "cap_qty"),
            ("HAT", "hat_qty"),
            ("PAD_CLAD", "pad_clad_qty"),
            ("HELMET_CLAD", "helmet_clad_qty"),
        ]:
            val = row.get(src_key)
            if isinstance(val, int) and val >= 0:
                overview["accessories"][acc_key] += val

    return overview

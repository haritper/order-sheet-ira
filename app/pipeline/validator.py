from collections import defaultdict

from app.pipeline.config import PACKING_SECTION_ORDER, SECTION_TO_CATEGORY_SLEEVE, SIZE_SETS


def validate_deterministic(
    normalized_rows,
    packing_list,
    order_overview,
    rejected_rows,
    policy=None,
):
    policy = policy or {}
    critical_errors = []
    warnings = []

    normalized_map = {r.get("source_row_id", ""): r for r in normalized_rows}
    rejected_ids = {r.get("source_row_id", "") for r in rejected_rows}

    packed_ids = []
    section_qty_maps = {s: defaultdict(int) for s in PACKING_SECTION_ORDER}
    section_trouser_maps = {s: defaultdict(int) for s in PACKING_SECTION_ORDER}

    for section in PACKING_SECTION_ORDER:
        rows = packing_list.get(section, [])
        for expected_sno, row in enumerate(rows, start=1):
            source_row_id = row.get("source_row_id", "")
            packed_ids.append(source_row_id)

            if row.get("s_no") != expected_sno:
                critical_errors.append(_err("PACKING_SNO_SEQUENCE_INVALID", "critical", "s_no must be sequential", source_row_id, section))

            if source_row_id not in normalized_map:
                critical_errors.append(_err("PACKING_LIST_ROW_NOT_IN_NORMALIZED", "critical", "Packing row missing source row", source_row_id, section))
                continue

            norm = normalized_map[source_row_id]
            category_expected, sleeve_expected = SECTION_TO_CATEGORY_SLEEVE[section]

            if source_row_id in rejected_ids:
                critical_errors.append(_err("ROW_IN_PACKING_AND_REJECTED", "critical", "Row appears in both packing and rejected", source_row_id, section))

            _validate_mandatory(norm, row, category_expected, sleeve_expected, critical_errors, warnings, section, policy)

            section_qty_maps[section][row.get("tshirt_size")] += row.get("tshirt_qty", 0) or 0
            section_trouser_maps[section][row.get("trouser_size")] += row.get("trouser_qty", 0) or 0

    dup_ids = [sid for sid, cnt in _counts(packed_ids).items() if sid and cnt > 1]
    for sid in dup_ids:
        critical_errors.append(_err("PACKING_LIST_DUPLICATE_ROW", "critical", "Duplicate source_row_id in packing list", sid, "packing_list"))

    # Coverage / silent drop
    included_or_rejected = set(packed_ids).union(rejected_ids)
    for row in normalized_rows:
        sid = row.get("source_row_id", "")
        if _is_ignored_blank_row(row):
            continue
        if sid not in included_or_rejected:
            critical_errors.append(_err("SILENT_ROW_DROP", "critical", "Normalized row not included or rejected", sid, "coverage"))

    # Row integrity checks
    for sid, cnt in _counts(packed_ids).items():
        if cnt > 1:
            critical_errors.append(_err("PACKING_LIST_DUPLICATE_ROW", "critical", "Row appears in multiple packing sections", sid, "packing_list"))

    # Math checks
    _validate_overview_math(packing_list, order_overview, critical_errors)

    # Accessories CAP total check
    cap_total = sum((r.get("cap_qty") or 0) for r in normalized_rows if isinstance(r.get("cap_qty"), int))
    if cap_total != order_overview.get("accessories", {}).get("CAP", 0):
        critical_errors.append(_err("ACCESSORY_TOTAL_MISMATCH", "critical", "CAP total mismatch", "", "accessories"))

    # Suspicious checks
    _validate_suspicious(normalized_rows, warnings, critical_errors, policy)

    stats = {
        "normalized_row_count": len(normalized_rows),
        "included_row_count": len(packed_ids),
        "rejected_row_count": len(rejected_rows),
        "critical_error_count": len(critical_errors),
        "warning_count": len(warnings),
    }

    return {
        "critical_errors": critical_errors,
        "warnings": warnings,
        "stats": stats,
    }


def _validate_mandatory(norm, packed_row, expected_category, expected_sleeve, critical, warnings, section, policy):
    sid = norm.get("source_row_id", "")

    if not packed_row.get("player_name"):
        critical.append(_err("MISSING_REQUIRED_FIELD", "critical", "player_name is required", sid, section))

    if policy.get("require_number") and not str(packed_row.get("number") or "").strip():
        critical.append(_err("MISSING_REQUIRED_FIELD", "critical", "number is required", sid, section))

    if norm.get("category") != expected_category:
        critical.append(_err("INVALID_CATEGORY_FOR_SECTION", "critical", "category mismatch with packing section", sid, section))

    if norm.get("sleeve_type") != expected_sleeve:
        critical.append(_err("INVALID_CATEGORY_FOR_SECTION", "critical", "sleeve_type mismatch with packing section", sid, section))

    tshirt_size = packed_row.get("tshirt_size")
    if tshirt_size not in SIZE_SETS[expected_category]:
        critical.append(_err("INVALID_SIZE_FOR_CATEGORY", "critical", "Invalid tshirt_size for category", sid, section))

    tshirt_qty = packed_row.get("tshirt_qty")
    if not isinstance(tshirt_qty, int) or tshirt_qty < 0:
        critical.append(_err("MISSING_REQUIRED_FIELD", "critical", "tshirt_qty must be integer >= 0", sid, section))

    trouser_qty = packed_row.get("trouser_qty")
    trouser_size = packed_row.get("trouser_size")
    if not isinstance(trouser_qty, int) or trouser_qty < 0:
        critical.append(_err("MISSING_REQUIRED_FIELD", "critical", "trouser_qty must be integer >= 0", sid, section))
    if isinstance(trouser_qty, int) and trouser_qty > 0 and trouser_size not in SIZE_SETS[expected_category]:
        critical.append(_err("INVALID_SIZE_FOR_CATEGORY", "critical", "Trouser size required and must be valid", sid, section))

    if isinstance(norm.get("cap_qty"), int) and norm.get("cap_qty") < 0:
        critical.append(_err("MISSING_REQUIRED_FIELD", "critical", "cap_qty must be >= 0", sid, section))

    notes = norm.get("notes") or []
    if "UNCERTAIN_ROW_PARSE" in notes and not policy.get("allow_uncertain_row_override", False):
        critical.append(_err("UNCERTAIN_ROW_PARSE", "critical", "Uncertain parse row cannot be included", sid, section))


def _validate_overview_math(packing_list, overview, critical):
    section_to_target = {
        "mens_half": ("mens", "half_sleeve_tshirt"),
        "mens_full": ("mens", "full_sleeve_tshirt"),
        "womens_half": ("womens", "half_sleeve_tshirt"),
        "womens_full": ("womens", "full_sleeve_tshirt"),
        "youth_half": ("youth", "half_sleeve_tshirt"),
        "youth_full": ("youth", "full_sleeve_tshirt"),
    }

    category_trouser_acc = {
        "mens": defaultdict(int),
        "womens": defaultdict(int),
        "youth": defaultdict(int),
    }

    for section, rows in packing_list.items():
        group, block = section_to_target[section]
        expected_sizes = [k for k in overview[group][block].keys() if k != "TOTAL"]
        computed = {k: 0 for k in expected_sizes}
        computed_total = 0

        for row in rows:
            sz = row.get("tshirt_size")
            qty = row.get("tshirt_qty") or 0
            if sz in computed and isinstance(qty, int):
                computed[sz] += qty
                computed_total += qty

            tr_sz = row.get("trouser_size")
            tr_qty = row.get("trouser_qty") or 0
            if isinstance(tr_qty, int):
                category_trouser_acc[group][tr_sz] += tr_qty

        for sz in expected_sizes:
            if overview[group][block].get(sz, 0) != computed[sz]:
                critical.append(_err("OVERVIEW_TSHIRT_TOTAL_MISMATCH", "critical", f"Tshirt size mismatch {group}.{block}.{sz}", "", section))

        if overview[group][block].get("TOTAL", 0) != computed_total:
            critical.append(_err("OVERVIEW_TSHIRT_TOTAL_MISMATCH", "critical", f"TOTAL mismatch {group}.{block}", "", section))

    for group in ("mens", "womens", "youth"):
        trouser_block = overview[group]["trouser"]
        expected_sizes = [k for k in trouser_block.keys() if k != "TOTAL"]
        computed_total = 0
        for sz in expected_sizes:
            actual = category_trouser_acc[group].get(sz, 0)
            computed_total += actual
            if trouser_block.get(sz, 0) != actual:
                critical.append(_err("OVERVIEW_TROUSER_TOTAL_MISMATCH", "critical", f"Trouser size mismatch {group}.trouser.{sz}", "", group))
        if trouser_block.get("TOTAL", 0) != computed_total:
            critical.append(_err("OVERVIEW_TROUSER_TOTAL_MISMATCH", "critical", f"TOTAL mismatch {group}.trouser", "", group))

    _validate_overview_key_order(overview, critical)


def _validate_suspicious(normalized_rows, warnings, critical, policy):
    number_buckets = defaultdict(list)
    player_signatures = defaultdict(set)

    for row in normalized_rows:
        sid = row.get("source_row_id", "")
        num = str(row.get("number") or "").strip()
        category = row.get("category")

        if num:
            number_buckets[(category, num)].append(sid)
            if num.isdigit() and int(num) > policy.get("unusual_number_warning_threshold", 999):
                warnings.append(_err("UNUSUAL_JERSEY_NUMBER", "warning", "Unusually high jersey number", sid, "suspicious"))

        tshirt_qty = row.get("tshirt_qty")
        trouser_qty = row.get("trouser_qty")
        if tshirt_qty == 0 or trouser_qty == 0:
            target = critical if policy.get("block_on_zero_quantities", False) else warnings
            sev = "critical" if policy.get("block_on_zero_quantities", False) else "warning"
            target.append(_err("ZERO_QUANTITY_ROW", sev, "Row contains zero quantity", sid, "suspicious"))

        pname = (row.get("player_name") or "").strip().upper()
        if pname:
            player_signatures[pname].add((row.get("category"), row.get("tshirt_size"), row.get("trouser_size")))

    for (cat, number), ids in number_buckets.items():
        if len(ids) > 1:
            target = critical if policy.get("duplicate_number_is_critical", False) else warnings
            sev = "critical" if policy.get("duplicate_number_is_critical", False) else "warning"
            target.append(_err("DUPLICATE_JERSEY_NUMBER", sev, f"Duplicate jersey number {number} in {cat}", ",".join(ids), "suspicious"))

    for pname, signatures in player_signatures.items():
        if len(signatures) > 1:
            warnings.append(_err("PLAYER_CONFLICTING_SIZES", "warning", f"Player {pname} has conflicting sizes/categories", "", "suspicious"))


def _validate_overview_key_order(overview, critical):
    expected_top = ["mens", "womens", "youth", "accessories"]
    if list(overview.keys()) != expected_top:
        critical.append(_err("OVERVIEW_KEYS_ORDER_INVALID", "critical", "Top-level overview key order invalid", "", "overview"))


def _counts(values):
    counts = defaultdict(int)
    for v in values:
        counts[v] += 1
    return counts


def _is_ignored_blank_row(row):
    sid = (row.get("source_row_id") or "").strip()
    return not sid


def _err(code, severity, message, source_row_id, section):
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "source_row_id": source_row_id,
        "section": section,
    }

from collections import OrderedDict


MENS_SIZES = ["XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL"]
WOMENS_SIZES = ["WXS", "WS", "WM", "WL", "WXL", "W2XL", "W3XL", "W4XL"]
YOUTH_SIZES = ["YXXS", "YXS", "YS", "YM", "YL", "YXL"]
SIZE_ORDER = MENS_SIZES + WOMENS_SIZES + YOUTH_SIZES
SIZE_ORDER_INDEX = {size: idx for idx, size in enumerate(SIZE_ORDER)}


def build_order_overview(order):
    overview = {
        "mens": _empty_category_block(MENS_SIZES),
        "womens": _empty_category_block(WOMENS_SIZES),
        "youth": _empty_category_block(YOUTH_SIZES),
        "accessories": OrderedDict(
            [
                ("CAP", 0),
                ("BAGGY_CAP", 0),
                ("HAT", 0),
                ("PAD_CLAD", 0),
                ("HELMET_CLAD", 0),
            ]
        ),
    }

    for player in sorted(order.players, key=lambda p: (p.row_number or 0, p.id or 0)):
        sleeve = (player.sleeve_type or "").strip().upper()
        tshirt_size = (player.tshirt_size or "").strip().upper()
        trouser_size = (player.trouser_size or "").strip().upper()
        tshirt_qty = _safe_int(getattr(player, "tshirt_qty", 0))
        trouser_qty = _safe_int(getattr(player, "trouser_qty", 0))

        category = _category_for_size(tshirt_size)
        if category and sleeve in ("HALF", "FULL"):
            sleeve_key = "half_sleeve_tshirt" if sleeve == "HALF" else "full_sleeve_tshirt"
            row = overview[category][sleeve_key]
            if tshirt_size in row:
                row[tshirt_size] += tshirt_qty
                row["TOTAL"] += tshirt_qty

        trouser_category = _category_for_size(trouser_size)
        if trouser_category and trouser_size in overview[trouser_category]["trouser"]:
            trouser_row = overview[trouser_category]["trouser"]
            trouser_row[trouser_size] += trouser_qty
            trouser_row["TOTAL"] += trouser_qty

    for accessory in order.accessories:
        name = (accessory.product_name or "").strip().lower()
        qty = _safe_int(getattr(accessory, "quantity", 0))
        if "baggy" in name and "cap" in name:
            overview["accessories"]["BAGGY_CAP"] += qty
        elif "cap" in name:
            overview["accessories"]["CAP"] += qty
        elif "hat" in name:
            overview["accessories"]["HAT"] += qty
        elif "pad" in name:
            overview["accessories"]["PAD_CLAD"] += qty
        elif "helmet" in name:
            overview["accessories"]["HELMET_CLAD"] += qty

    _attach_dynamic_product_rows(order, overview)

    return overview


def build_player_groups(order):
    groups = {"mens": [], "womens": [], "youth": []}
    for player in order.players:
        size = (player.tshirt_size or "").strip().upper()
        category = _category_for_size(size)
        if not category:
            category = "mens"
        groups[category].append(player)

    for key in groups:
        groups[key].sort(key=_player_sort_key)
    return groups


def build_packing_groups(order):
    groups = {
        "mens_half": [],
        "mens_full": [],
        "womens_half": [],
        "womens_full": [],
        "youth_half": [],
        "youth_full": [],
    }
    for player in order.players:
        section = _packing_section_for_player(player)
        if section:
            groups[section].append(player)

    for key in groups:
        groups[key].sort(key=_player_sort_key)
    return groups


def build_product_groups(order):
    groups = {"MENS": [], "WOMENS": [], "YOUTH": [], "UNISEX": []}
    for item in order.items:
        gender = (item.gender or "UNISEX").strip().upper()
        if gender not in groups:
            gender = "UNISEX"
        groups[gender].append(item)

    for key in groups:
        groups[key].sort(key=lambda x: ((x.product_name or "").lower(), (x.sleeve_type or "").lower(), x.id or 0))
    return groups


def _empty_category_block(sizes):
    return {
        "half_sleeve_tshirt": _empty_size_row(sizes),
        "full_sleeve_tshirt": _empty_size_row(sizes),
        "trouser": _empty_size_row(sizes),
        "rows": [],
        "grouped_tables": [],
    }


def _empty_size_row(sizes):
    row = OrderedDict()
    for size in sizes:
        row[size] = 0
    row["TOTAL"] = 0
    return row


def _safe_int(value):
    try:
        return int(value or 0)
    except (ValueError, TypeError):
        return 0


def _category_for_size(size):
    if size in MENS_SIZES:
        return "mens"
    if size in WOMENS_SIZES:
        return "womens"
    if size in YOUTH_SIZES:
        return "youth"
    return None


def _player_sort_key(player):
    tshirt_size = (player.tshirt_size or "").strip().upper()
    size_rank = SIZE_ORDER_INDEX.get(tshirt_size, 999)
    return (
        size_rank,
        (player.player_name or "").lower(),
        player.row_number or 0,
        player.id or 0,
    )


def _packing_section_for_player(player):
    tshirt_size = (player.tshirt_size or "").strip().upper()
    category = _category_for_size(tshirt_size) or "mens"
    sleeve = (player.sleeve_type or "").strip().upper()
    if sleeve not in {"HALF", "FULL"}:
        sleeve = "HALF"
    return f"{category}_{sleeve.lower()}"


def _attach_dynamic_product_rows(order, overview):
    assignments = _build_primary_family_assignments(order)
    seen = set()
    for item in sorted(
        order.items,
        key=lambda x: (
            (x.gender or "").lower(),
            (x.product_name or "").lower(),
            (x.sleeve_type or "").lower(),
            x.id or 0,
        ),
    ):
        category = _category_for_gender(item.gender)
        if not category:
            continue
        product_name = (item.product_name or "").strip()
        if not product_name:
            continue
        sleeve = (item.sleeve_type or "").strip().upper()
        row_key = (category, product_name.lower(), sleeve)
        if row_key in seen:
            continue
        seen.add(row_key)

        sizes_row = _build_product_sizes_row(order, category, product_name, sleeve, assignments)
        label = product_name
        if sleeve and _is_sleeve_relevant_product(product_name):
            label = f"{product_name} ({sleeve})"

        overview[category]["rows"].append(
            {
                "label": label,
                "sizes": sizes_row,
            }
        )

    # Backward compatibility: if no step-2 product rows exist, use legacy rows.
    for category in ("mens", "womens", "youth"):
        if overview[category]["rows"]:
            continue
        overview[category]["rows"] = [
            {"label": "Half Sleeve T Shirt", "sizes": OrderedDict(overview[category]["half_sleeve_tshirt"])},
            {"label": "Full Sleeve T Shirt", "sizes": OrderedDict(overview[category]["full_sleeve_tshirt"])},
            {"label": "Trouser", "sizes": OrderedDict(overview[category]["trouser"])},
        ]

    for category in ("mens", "womens", "youth"):
        overview[category]["grouped_tables"] = _build_grouped_product_tables(order, category)


def _category_for_gender(gender):
    g = (gender or "").strip().upper()
    if g == "MENS":
        return "mens"
    if g == "WOMENS":
        return "womens"
    if g == "YOUTH":
        return "youth"
    return None


def _source_row_key_for_product(product_name: str, sleeve: str) -> str:
    name = (product_name or "").strip().lower()
    if any(token in name for token in ("trouser", "touser", "pant", "short")):
        return "trouser"
    if sleeve == "FULL":
        return "full_sleeve_tshirt"
    return "half_sleeve_tshirt"


def _sizes_for_category(category: str):
    if category == "womens":
        return WOMENS_SIZES
    if category == "youth":
        return YOUTH_SIZES
    return MENS_SIZES


def _build_product_sizes_row(order, category: str, product_name: str, sleeve: str, assignments):
    row = _empty_size_row(_sizes_for_category(category))
    target_name = (product_name or "").strip().lower()
    target_sleeve = (sleeve or "").strip().upper()
    sleeve_relevant = _is_sleeve_relevant_product(product_name)

    for item in order.items:
        item_category = _category_for_gender(item.gender)
        if item_category != category:
            continue
        if (item.product_name or "").strip().lower() != target_name:
            continue
        if sleeve_relevant and target_sleeve:
            item_sleeve = (item.sleeve_type or "").strip().upper()
            if item_sleeve != target_sleeve:
                continue

        size_fields = {
            "XS": _safe_int(getattr(item, "qty_xs", 0)),
            "S": _safe_int(getattr(item, "qty_s", 0)),
            "M": _safe_int(getattr(item, "qty_m", 0)),
            "L": _safe_int(getattr(item, "qty_l", 0)),
            "XL": _safe_int(getattr(item, "qty_xl", 0)),
            "2XL": _safe_int(getattr(item, "qty_2xl", 0)),
            "3XL": _safe_int(getattr(item, "qty_3xl", 0)),
            "4XL": _safe_int(getattr(item, "qty_4xl", 0)),
        }
        for size_key in list(row.keys()):
            if size_key == "TOTAL":
                continue
            qty = size_fields.get(size_key, 0)
            row[size_key] += qty
            row["TOTAL"] += qty

    # Fallback: when step-2 item qty fields are zero, derive from packing-list players.
    if (row.get("TOTAL", 0) or 0) <= 0:
        use_bottom = _is_bottomwear_product(product_name)
        row = _sum_player_sizes_from_packing(order, category, use_bottom=use_bottom, sleeve=target_sleeve if sleeve_relevant else "")

    return row


def _category_for_player(player):
    tshirt_size = (player.tshirt_size or "").strip().upper()
    trouser_size = (player.trouser_size or "").strip().upper()
    return _category_for_size(tshirt_size) or _category_for_size(trouser_size) or "mens"


def _is_bottomwear_product(product_name: str):
    name = (product_name or "").strip().lower()
    return any(token in name for token in ("trouser", "touser", "pant", "short"))


def _is_sleeve_relevant_product(product_name: str):
    name = (product_name or "").strip().lower()
    if any(token in name for token in ("jacket", "hoodie", "sleeveless")):
        return False
    if any(token in name for token in ("cap", "hat", "clad")):
        return False
    if _is_bottomwear_product(name):
        return False
    return True


def _product_key(product_name: str):
    return (product_name or "").strip().lower()


def _build_primary_family_assignments(order):
    by_category = {"mens": {"top": None, "bottom": None}, "womens": {"top": None, "bottom": None}, "youth": {"top": None, "bottom": None}}

    items = sorted(
        order.items,
        key=lambda x: ((x.gender or "").lower(), (x.product_name or "").lower(), (x.id or 0)),
    )
    for item in items:
        category = _category_for_gender(item.gender)
        if not category:
            continue
        pkey = _product_key(item.product_name)
        if not pkey:
            continue
        if _is_bottomwear_product(pkey):
            if by_category[category]["bottom"] is None:
                by_category[category]["bottom"] = pkey
        else:
            if by_category[category]["top"] is None:
                by_category[category]["top"] = pkey
    return by_category


def _build_grouped_product_tables(order, category: str):
    tables = []
    consumed_names = set()

    has_playing = _has_product(order, category, lambda n: "playing" in n and "jersey" in n)
    has_training = _has_product(order, category, lambda n: "training" in n and "jersey" in n)
    has_cricket_whites_t = _has_product(order, category, lambda n: "cricket whites tshirt" in n)
    has_cricket_whites_p = _has_product(order, category, lambda n: "cricket whites pant" in n)
    has_trouser = _has_product(order, category, lambda n: n.strip() == "trousers")

    if has_playing:
        rows = [
            {
                "label": "Half Sleeve T Shirt",
                "sizes": _sum_item_sizes(
                    order,
                    category,
                    lambda n: "playing" in n and "jersey" in n,
                    sleeve="HALF",
                ),
            },
            {
                "label": "Full Sleeve T Shirt",
                "sizes": _sum_item_sizes(
                    order,
                    category,
                    lambda n: "playing" in n and "jersey" in n,
                    sleeve="FULL",
                ),
            },
        ]
        if has_trouser:
            rows.append(
                {
                    "label": "Trousers",
                    "sizes": _sum_item_sizes(order, category, lambda n: n.strip() == "trousers"),
                }
            )
            consumed_names.add("trousers")
        tables.append({"title": "Playing Jersey", "rows": rows})
        consumed_names.add("playing jersey")

    if has_training:
        rows = [
            {
                "label": "Half Sleeve T Shirt",
                "sizes": _sum_item_sizes(
                    order,
                    category,
                    lambda n: "training" in n and "jersey" in n,
                    sleeve="HALF",
                ),
            },
        ]
        has_shorts = _has_product(order, category, lambda n: "short" in n)
        if has_shorts:
            rows.append(
                {
                    "label": "Shorts",
                    "sizes": _sum_item_sizes(order, category, lambda n: "short" in n),
                }
            )
            consumed_names.add("shorts")
        tables.append({"title": "Training Jersey", "rows": rows})
        consumed_names.add("training jersey")

    if has_cricket_whites_t:
        rows = [
            {
                "label": "Half Sleeve T Shirt",
                "sizes": _sum_item_sizes(
                    order,
                    category,
                    lambda n: "cricket whites tshirt" in n,
                    sleeve="HALF",
                ),
            },
            {
                "label": "Full Sleeve T Shirt",
                "sizes": _sum_item_sizes(
                    order,
                    category,
                    lambda n: "cricket whites tshirt" in n,
                    sleeve="FULL",
                ),
            },
        ]
        if has_cricket_whites_p:
            rows.append(
                {
                    "label": "Cricket Whites Pant",
                    "sizes": _sum_item_sizes(order, category, lambda n: "cricket whites pant" in n),
                }
            )
            consumed_names.add("cricket whites pant")
        tables.append({"title": "Cricket Whites Tshirt", "rows": rows})
        consumed_names.add("cricket whites tshirt")

    other_rows = []
    for item in sorted(
        [it for it in order.items if _category_for_gender(it.gender) == category],
        key=lambda x: ((x.product_name or "").lower(), (x.sleeve_type or "").lower(), x.id or 0),
    ):
        item_name = (item.product_name or "").strip()
        item_name_key = item_name.lower()
        if item_name_key in consumed_names:
            continue
        sizes = _sizes_from_item(item, category)
        if (sizes.get("TOTAL", 0) or 0) <= 0:
            continue
        label = item_name
        sleeve = (item.sleeve_type or "").strip().upper()
        if sleeve and _is_sleeve_relevant_product(item_name):
            label = f"{item_name} ({sleeve})"
        other_rows.append({"label": label, "sizes": sizes})

    for row in other_rows:
        tables.append({"title": "", "rows": [row]})

    return tables


def _has_product(order, category: str, name_predicate):
    for item in order.items:
        if _category_for_gender(item.gender) != category:
            continue
        name = (item.product_name or "").strip().lower()
        if name_predicate(name):
            return True
    return False


def _sum_item_sizes(order, category: str, name_predicate, sleeve: str = ""):
    row = _empty_size_row(_sizes_for_category(category))
    target_sleeve = (sleeve or "").strip().upper()
    matched_names = []
    for item in order.items:
        if _category_for_gender(item.gender) != category:
            continue
        name = (item.product_name or "").strip().lower()
        if not name_predicate(name):
            continue
        matched_names.append(name)
        if target_sleeve:
            item_sleeve = (item.sleeve_type or "").strip().upper()
            if item_sleeve != target_sleeve:
                continue
        for size_key in row.keys():
            if size_key == "TOTAL":
                continue
            qty = _item_qty_for_size(item, size_key)
            row[size_key] += qty
            row["TOTAL"] += qty

    # Fallback: if grouped row is zero from item qty fields, use packing-list totals.
    if (row.get("TOTAL", 0) or 0) <= 0 and matched_names:
        all_bottom = all(_is_bottomwear_product(n) for n in matched_names)
        row = _sum_player_sizes_from_packing(order, category, use_bottom=all_bottom, sleeve=target_sleeve)

    return row


def _item_qty_for_size(item, size_key: str):
    key = (size_key or "").strip().upper()
    field_map = {
        "XS": "qty_xs",
        "S": "qty_s",
        "M": "qty_m",
        "L": "qty_l",
        "XL": "qty_xl",
        "2XL": "qty_2xl",
        "3XL": "qty_3xl",
        "4XL": "qty_4xl",
        "WXS": "qty_xs",
        "WS": "qty_s",
        "WM": "qty_m",
        "WL": "qty_l",
        "WXL": "qty_xl",
        "W2XL": "qty_2xl",
        "W3XL": "qty_3xl",
        "W4XL": "qty_4xl",
        "YXXS": "qty_xs",
        "YXS": "qty_s",
        "YS": "qty_m",
        "YM": "qty_l",
        "YL": "qty_xl",
        "YXL": "qty_2xl",
    }
    field = field_map.get(key)
    return _safe_int(getattr(item, field, 0)) if field else 0


def _sizes_from_item(item, category: str):
    row = _empty_size_row(_sizes_for_category(category))
    for size_key in row.keys():
        if size_key == "TOTAL":
            continue
        qty = _item_qty_for_size(item, size_key)
        row[size_key] += qty
        row["TOTAL"] += qty
    return row


def _sum_player_sizes_from_packing(order, category: str, use_bottom: bool, sleeve: str = ""):
    row = _empty_size_row(_sizes_for_category(category))
    target_sleeve = (sleeve or "").strip().upper()

    for player in order.players:
        player_category = _category_for_player(player)
        if player_category != category:
            continue

        if use_bottom:
            size = (player.trouser_size or "").strip().upper()
            qty = _safe_int(getattr(player, "trouser_qty", 0))
        else:
            if target_sleeve:
                player_sleeve = (player.sleeve_type or "").strip().upper()
                if player_sleeve != target_sleeve:
                    continue
            size = (player.tshirt_size or "").strip().upper()
            qty = _safe_int(getattr(player, "tshirt_qty", 0))

        if qty <= 0:
            continue
        if size not in row:
            continue
        row[size] += qty
        row["TOTAL"] += qty

    return row

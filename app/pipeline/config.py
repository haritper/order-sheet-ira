from copy import deepcopy

CATEGORY_VALUES = ["MENS", "WOMENS", "YOUTH"]
SLEEVE_VALUES = ["HALF", "FULL"]

SIZE_SETS = {
    "MENS": ["XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL"],
    "WOMENS": ["WXS", "WS", "WM", "WL", "WXL", "W2XL", "W3XL", "W4XL"],
    "YOUTH": ["YXXS", "YXS", "YS", "YM", "YL", "YXL"],
}

PACKING_SECTION_ORDER = [
    "mens_half",
    "mens_full",
    "womens_half",
    "womens_full",
    "youth_half",
    "youth_full",
]

SECTION_TO_CATEGORY_SLEEVE = {
    "mens_half": ("MENS", "HALF"),
    "mens_full": ("MENS", "FULL"),
    "womens_half": ("WOMENS", "HALF"),
    "womens_full": ("WOMENS", "FULL"),
    "youth_half": ("YOUTH", "HALF"),
    "youth_full": ("YOUTH", "FULL"),
}

DEFAULT_MAPPING_RULES = {
    "size_aliases": {
        "ADULT XS": "XS",
        "ADULT S": "S",
        "ADULT M": "M",
        "ADULT L": "L",
        "ADULT XL": "XL",
        "WOMEN XS": "WXS",
        "WOMEN S": "WS",
        "WOMEN M": "WM",
        "WOMEN L": "WL",
        "WOMEN XL": "WXL",
        "YOUTH XXS": "YXXS",
        "YOUTH XS": "YXS",
        "YOUTH S": "YS",
        "YOUTH M": "YM",
        "YOUTH L": "YL",
        "YOUTH XL": "YXL",
    },
    "sleeve_aliases": {
        "HALF SLEEVE": "HALF",
        "HALF": "HALF",
        "FULL SLEEVE": "FULL",
        "FULL": "FULL",
    },
}

DEFAULT_POLICY = {
    "require_number": False,
    "allow_print_name_fallback": True,
    "allow_uncertain_row_override": False,
    "duplicate_number_is_critical": False,
    "unusual_number_warning_threshold": 999,
    "block_on_zero_quantities": False,
}


def merged_config(mapping_rules=None, policy=None):
    rules = deepcopy(DEFAULT_MAPPING_RULES)
    if mapping_rules:
        for key in ("size_aliases", "sleeve_aliases"):
            if key in mapping_rules:
                rules[key].update(mapping_rules[key])

    policy_data = deepcopy(DEFAULT_POLICY)
    if policy:
        policy_data.update(policy)

    return rules, policy_data

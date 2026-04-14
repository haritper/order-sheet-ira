import json
import os
import uuid

from app.pipeline.config import CATEGORY_VALUES, SIZE_SETS, SLEEVE_VALUES, merged_config
from app.pipeline.prompts import NORMALIZATION_PROMPT


def normalize_rows_deterministic(raw_rows, document_meta, mapping_rules=None, policy=None):
    rules, policy_data = merged_config(mapping_rules, policy)
    normalized = []

    for idx, row in enumerate(raw_rows, start=1):
        source_row_id = str(_first(row, ["source_row_id", "row_id", "id"]) or f"row_{idx}")
        notes = []

        player_name = str(_first(row, ["player_name", "name", "player"]) or "").strip()
        print_name = str(_first(row, ["print_name", "jersey_name", "preferred_name"]) or "").strip()
        if not print_name:
            if policy_data["allow_print_name_fallback"] and player_name:
                print_name = player_name
                notes.append("PRINT_NAME_FALLBACK")
            else:
                notes.append("MISSING_PRINT_NAME")

        number = str(_first(row, ["number", "jersey_number", "no"]) or "").strip()

        category = str(_first(row, ["category", "gender"]) or "").strip().upper()
        if category not in CATEGORY_VALUES:
            if category:
                notes.append("INVALID_CATEGORY")
            else:
                notes.append("MISSING_CATEGORY")
            category = ""

        sleeve_raw = str(_first(row, ["sleeve_type", "sleeve"]) or "").strip().upper()
        sleeve = rules["sleeve_aliases"].get(sleeve_raw, sleeve_raw)
        if sleeve not in SLEEVE_VALUES:
            if sleeve:
                notes.append("INVALID_SLEEVE_TYPE")
            else:
                notes.append("MISSING_SLEEVE_TYPE")
            sleeve = ""

        tshirt_size_raw = str(_first(row, ["tshirt_size", "shirt_size", "tee_size"]) or "").strip().upper()
        tshirt_size = rules["size_aliases"].get(tshirt_size_raw, tshirt_size_raw)
        if category and tshirt_size and tshirt_size not in SIZE_SETS[category]:
            notes.append("INVALID_TSHIRT_SIZE")

        tshirt_qty = _to_int(_first(row, ["tshirt_qty", "shirt_qty", "tee_qty"]))
        if tshirt_qty is None:
            notes.append("MISSING_TSHIRT_QTY")

        trouser_size_raw = str(_first(row, ["trouser_size", "pant_size", "pants_size"]) or "").strip().upper()
        trouser_size = rules["size_aliases"].get(trouser_size_raw, trouser_size_raw)

        trouser_qty = _to_int(_first(row, ["trouser_qty", "pant_qty", "pants_qty"]))
        if trouser_qty is None:
            trouser_qty = 0

        if trouser_qty > 0 and not trouser_size:
            notes.append("MISSING_TROUSER_SIZE")
        if category and trouser_size and trouser_size not in SIZE_SETS[category]:
            notes.append("INVALID_TROUSER_SIZE")

        cap_qty = _to_int(_first(row, ["cap_qty", "caps"]))
        if cap_qty is None:
            cap_qty = 0

        normalized.append(
            {
                "source_row_id": source_row_id,
                "player_name": player_name,
                "print_name": print_name,
                "number": number,
                "category": category,
                "sleeve_type": sleeve,
                "tshirt_size": tshirt_size,
                "tshirt_qty": tshirt_qty,
                "trouser_size": trouser_size,
                "trouser_qty": trouser_qty,
                "cap_qty": cap_qty,
                "notes": sorted(set(notes)),
                "raw_values": dict(row),
            }
        )

    return {
        "document_meta": {
            "input_file_name": document_meta.get("input_file_name", ""),
            "input_type": document_meta.get("input_type", "excel"),
            "sheet_name": document_meta.get("sheet_name", ""),
            "team_name": document_meta.get("team_name", ""),
            "source_trace_id": document_meta.get("source_trace_id", str(uuid.uuid4())),
        },
        "normalized_rows": normalized,
    }


def normalize_rows_llm(raw_rows, document_meta, mapping_rules=None, policy=None, model=None):
    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for LLM normalization")

    model = model or os.environ.get("OPENAI_ROSTER_MODEL", "gpt-4.1")
    client = OpenAI(api_key=api_key)

    rules, _ = merged_config(mapping_rules, policy)
    payload = {
        "document_meta": document_meta,
        "mapping_rules": rules,
        "raw_rows": raw_rows,
    }

    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": NORMALIZATION_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
        ],
    )
    data = json.loads(response.choices[0].message.content or "{}")
    normalized_rows = data.get("normalized_rows", [])

    return {
        "document_meta": {
            "input_file_name": document_meta.get("input_file_name", ""),
            "input_type": document_meta.get("input_type", "excel"),
            "sheet_name": document_meta.get("sheet_name", ""),
            "team_name": document_meta.get("team_name", ""),
            "source_trace_id": document_meta.get("source_trace_id", str(uuid.uuid4())),
        },
        "normalized_rows": normalized_rows,
    }


def _first(row, keys):
    for key in keys:
        if key in row and row[key] is not None and str(row[key]).strip() != "":
            return row[key]
    return None


def _to_int(value):
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None

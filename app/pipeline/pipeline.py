import json
from pathlib import Path

from app.pipeline.ai_validator import run_ai_validator
from app.pipeline.approval import approval_decision
from app.pipeline.generator import generate_from_normalized
from app.pipeline.input_loader import load_input_rows
from app.pipeline.normalizer import normalize_rows_deterministic, normalize_rows_llm
from app.pipeline.validator import validate_deterministic
from pricing.cost_calculator import get_routing_models


OUTPUT_FILES = {
    "normalized_rows": "normalized_rows.json",
    "packing_list": "packing_list.json",
    "order_overview": "order_overview.json",
    "validation_report": "validation_report.json",
    "approval_decision": "approval_decision.json",
}


def run_order_sheet_pipeline(
    input_file,
    output_dir,
    *,
    input_type=None,
    sheet_name=None,
    team_name="",
    mapping_rules=None,
    policy=None,
    use_llm_normalizer=False,
    use_ai_validator=True,
    mode="safe_gpt54",
):
    routing = get_routing_models(mode)

    loaded = load_input_rows(input_file, sheet_name=sheet_name)
    doc_meta = {
        "input_file_name": loaded["input_file_name"],
        "input_type": input_type or loaded["input_type"],
        "sheet_name": loaded["sheet_name"],
        "team_name": team_name,
        "source_trace_id": "",
    }

    if use_llm_normalizer:
        normalized_stage = normalize_rows_llm(
            loaded["rows"],
            doc_meta,
            mapping_rules,
            policy,
            model=routing.get("input_normalization"),
        )
    else:
        normalized_stage = normalize_rows_deterministic(loaded["rows"], doc_meta, mapping_rules, policy)

    normalized_rows = normalized_stage["normalized_rows"]

    generated = generate_from_normalized(normalized_rows)
    packing_list = generated["packing_list"]
    order_overview = generated["order_overview"]
    rejected_rows = generated["rejected_rows"]

    deterministic_report = validate_deterministic(
        normalized_rows,
        packing_list,
        order_overview,
        rejected_rows,
        policy=policy,
    )

    if use_ai_validator:
        ai_report = run_ai_validator(
            normalized_rows,
            packing_list,
            order_overview,
            deterministic_report,
            model=routing.get("ai_validator"),
        )
    else:
        ai_report = {"ai_validation": {"critical_findings": [], "warnings": [], "recommendation": "REVIEW"}}

    approval = approval_decision(
        deterministic_report,
        ai_report,
        allow_uncertain_row_override=(policy or {}).get("allow_uncertain_row_override", False),
    )

    validation_report = {
        "critical_errors": deterministic_report["critical_errors"] + ai_report["ai_validation"].get("critical_findings", []),
        "warnings": deterministic_report["warnings"] + ai_report["ai_validation"].get("warnings", []),
        "stats": deterministic_report["stats"],
    }

    outputs = {
        "document_meta": normalized_stage["document_meta"],
        "normalized_rows": normalized_rows,
        "packing_list": packing_list,
        "order_overview": order_overview,
        "validation_report": validation_report,
        "approval_decision": approval,
        "rejected_rows": rejected_rows,
    }

    _write_outputs(outputs, output_dir)
    return outputs


def _write_outputs(outputs, output_dir):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    normalized_payload = {
        "document_meta": outputs["document_meta"],
        "normalized_rows": outputs["normalized_rows"],
    }
    (out / OUTPUT_FILES["normalized_rows"]).write_text(json.dumps(normalized_payload, indent=2), encoding="utf-8")

    (out / OUTPUT_FILES["packing_list"]).write_text(
        json.dumps({"packing_list": outputs["packing_list"]}, indent=2), encoding="utf-8"
    )

    (out / OUTPUT_FILES["order_overview"]).write_text(
        json.dumps({"order_overview": outputs["order_overview"]}, indent=2), encoding="utf-8"
    )

    (out / OUTPUT_FILES["validation_report"]).write_text(
        json.dumps({"validation_report": outputs["validation_report"]}, indent=2), encoding="utf-8"
    )

    (out / OUTPUT_FILES["approval_decision"]).write_text(
        json.dumps({"approval_decision": outputs["approval_decision"]}, indent=2), encoding="utf-8"
    )

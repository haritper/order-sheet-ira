import json

from app.pipeline.pipeline import run_order_sheet_pipeline


def test_pipeline_outputs_and_approval(tmp_path):
    input_csv = tmp_path / "roster.csv"
    input_csv.write_text(
        "source_row_id,player_name,print_name,number,category,sleeve_type,tshirt_size,tshirt_qty,trouser_size,trouser_qty,cap_qty\n"
        "r1,John,John,10,MENS,HALF,M,2,M,2,1\n"
        "r2,Jane,Jane,22,WOMENS,FULL,WM,1,WM,1,0\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    result = run_order_sheet_pipeline(
        input_file=str(input_csv),
        output_dir=str(out_dir),
        use_llm_normalizer=False,
        use_ai_validator=False,
    )

    assert (out_dir / "normalized_rows.json").exists()
    assert (out_dir / "packing_list.json").exists()
    assert (out_dir / "order_overview.json").exists()
    assert (out_dir / "validation_report.json").exists()
    assert (out_dir / "approval_decision.json").exists()

    assert result["approval_decision"]["status"] in {"APPROVED", "NEEDS_MANUAL_REVIEW", "REJECTED"}
    assert len(result["normalized_rows"]) == 2

    packed = result["packing_list"]
    assert len(packed["mens_half"]) == 1
    assert len(packed["womens_full"]) == 1


def test_pipeline_rejects_invalid_size(tmp_path):
    input_csv = tmp_path / "bad.csv"
    input_csv.write_text(
        "source_row_id,player_name,number,category,sleeve_type,tshirt_size,tshirt_qty,trouser_size,trouser_qty\n"
        "r1,John,10,MENS,HALF,INVALID,2,M,2\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_bad"
    result = run_order_sheet_pipeline(
        input_file=str(input_csv),
        output_dir=str(out_dir),
        use_llm_normalizer=False,
        use_ai_validator=False,
    )

    assert result["approval_decision"]["status"] in {"REJECTED", "NEEDS_MANUAL_REVIEW"}
    assert len(result["rejected_rows"]) == 1

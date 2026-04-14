import pytest

from pricing.cost_calculator import (
    PRESETS,
    estimate_monthly_cost,
    estimate_sheet_cost,
    estimate_stage_cost,
    get_routing_models,
    load_pricing_config,
)


def test_estimate_stage_cost_formula_exact():
    cfg = load_pricing_config()
    res = estimate_stage_cost(
        model="gpt-5.4",
        input_tokens=1_000_000,
        cached_input_tokens=0,
        output_tokens=0,
        pricing_config=cfg,
    )
    assert res["input_cost"] == 2.5
    assert res["cached_input_cost"] == 0.0
    assert res["output_cost"] == 0.0
    assert res["stage_cost"] == 2.5


def test_estimate_sheet_and_monthly_cost():
    cfg = load_pricing_config()
    sheet = estimate_sheet_cost(PRESETS["safe_gpt54"], pricing_config=cfg)
    monthly = estimate_monthly_cost(PRESETS["safe_gpt54"], 200, pricing_config=cfg)
    assert sheet["sheet_cost"] > 0
    assert monthly["monthly_cost"] == round(sheet["sheet_cost"] * 200, 4)


def test_wrong_model_name_fails_loudly():
    cfg = load_pricing_config()
    with pytest.raises(ValueError, match="Unknown model"):
        estimate_stage_cost("does-not-exist", 1, 0, 1, pricing_config=cfg)


def test_negative_tokens_fail_loudly():
    cfg = load_pricing_config()
    with pytest.raises(ValueError, match="cannot be negative"):
        estimate_stage_cost("gpt-5.4", -1, 0, 1, pricing_config=cfg)


def test_zero_sheet_count_fails_loudly():
    cfg = load_pricing_config()
    with pytest.raises(ValueError, match="positive integer"):
        estimate_monthly_cost(PRESETS["hybrid"], 0, pricing_config=cfg)


def test_routing_modes_supported():
    safe = get_routing_models("safe_gpt54")
    hybrid = get_routing_models("hybrid")
    cost_opt = get_routing_models("cost_optimized")

    assert safe["input_normalization"] == "gpt-5.4"
    assert hybrid["input_normalization"] == "gpt-5-mini"
    assert cost_opt["generator"] == "gpt-5-mini"

#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "pricing_config.json"
ROUTING_PATH = Path(__file__).resolve().parent / "model_routing.json"
MONTHLY_SHEET_COUNTS = [100, 200, 500, 1000]

PRESETS = {
    "safe_gpt54": [
        {"stage_name": "input_normalization", "model": "gpt-5.4", "input_tokens": 3500, "cached_input_tokens": 0, "output_tokens": 900},
        {"stage_name": "generator", "model": "gpt-5.4", "input_tokens": 2500, "cached_input_tokens": 0, "output_tokens": 1200},
        {"stage_name": "ai_validator", "model": "gpt-5.4", "input_tokens": 1800, "cached_input_tokens": 0, "output_tokens": 600},
    ],
    "hybrid": [
        {"stage_name": "input_normalization", "model": "gpt-5-mini", "input_tokens": 3500, "cached_input_tokens": 0, "output_tokens": 900},
        {"stage_name": "generator", "model": "gpt-5.4", "input_tokens": 2500, "cached_input_tokens": 0, "output_tokens": 1200},
        {"stage_name": "ai_validator", "model": "gpt-5.4", "input_tokens": 1800, "cached_input_tokens": 0, "output_tokens": 600},
    ],
    "cost_optimized": [
        {"stage_name": "input_normalization", "model": "gpt-5-mini", "input_tokens": 3000, "cached_input_tokens": 0, "output_tokens": 700},
        {"stage_name": "generator", "model": "gpt-5-mini", "input_tokens": 2200, "cached_input_tokens": 0, "output_tokens": 900},
        {"stage_name": "ai_validator", "model": "gpt-5.4", "input_tokens": 1500, "cached_input_tokens": 0, "output_tokens": 500},
    ],
}


def load_pricing_config(config_path=CONFIG_PATH):
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Pricing config not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    if "models" not in data or not isinstance(data["models"], dict):
        raise ValueError("Malformed pricing config: missing models object")

    for model, prices in data["models"].items():
        for key in ("input_per_million", "cached_input_per_million", "output_per_million"):
            if key not in prices:
                raise ValueError(f"Malformed pricing config: {model} missing {key}")
            if not isinstance(prices[key], (int, float)):
                raise ValueError(f"Malformed pricing config: {model}.{key} must be numeric")
    return data


def load_model_routing_config(config_path=ROUTING_PATH):
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Model routing config not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if "mode" not in data or "modes" not in data:
        raise ValueError("Malformed model routing config")
    return data


def get_routing_models(mode=None, config_path=ROUTING_PATH):
    cfg = load_model_routing_config(config_path)
    active_mode = mode or cfg["mode"]
    if active_mode not in cfg["modes"]:
        raise ValueError(f"Unsupported mode: {active_mode}")
    return cfg["modes"][active_mode]


def estimate_stage_cost(model, input_tokens, cached_input_tokens, output_tokens, pricing_config=None):
    pricing_config = pricing_config or load_pricing_config()

    _validate_tokens(input_tokens, cached_input_tokens, output_tokens)

    model_prices = pricing_config["models"].get(model)
    if not model_prices:
        raise ValueError(f"Unknown model: {model}")

    input_cost = (input_tokens / 1_000_000) * model_prices["input_per_million"]
    cached_input_cost = (cached_input_tokens / 1_000_000) * model_prices["cached_input_per_million"]
    output_cost = (output_tokens / 1_000_000) * model_prices["output_per_million"]

    stage_cost = input_cost + cached_input_cost + output_cost

    return {
        "model": model,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "input_cost": round(input_cost, 4),
        "cached_input_cost": round(cached_input_cost, 4),
        "output_cost": round(output_cost, 4),
        "stage_cost": round(stage_cost, 4),
    }


def estimate_sheet_cost(stage_usages, pricing_config=None):
    pricing_config = pricing_config or load_pricing_config()
    stages = []
    sheet_cost = 0.0

    for usage in stage_usages:
        stage_name = usage["stage_name"]
        stage_result = estimate_stage_cost(
            model=usage["model"],
            input_tokens=usage["input_tokens"],
            cached_input_tokens=usage["cached_input_tokens"],
            output_tokens=usage["output_tokens"],
            pricing_config=pricing_config,
        )
        stage_result["stage_name"] = stage_name
        stages.append(stage_result)
        sheet_cost += stage_result["stage_cost"]

    return {
        "currency": "USD",
        "stages": stages,
        "sheet_cost": round(sheet_cost, 4),
        "sheet_count": 0,
        "monthly_cost": 0.0,
    }


def estimate_monthly_cost(stage_usages, sheet_count, pricing_config=None):
    if not isinstance(sheet_count, int) or sheet_count <= 0:
        raise ValueError("sheet_count must be positive integer")

    sheet_result = estimate_sheet_cost(stage_usages, pricing_config=pricing_config)
    monthly_cost = sheet_result["sheet_cost"] * sheet_count

    result = dict(sheet_result)
    result["sheet_count"] = sheet_count
    result["monthly_cost"] = round(monthly_cost, 4)
    return result


def format_money(value):
    return f"{value:.2f}"


def print_comparison_table(pricing_config=None):
    pricing_config = pricing_config or load_pricing_config()
    print("| preset | sheets | sheet_cost_usd | monthly_cost_usd |")
    print("|---|---:|---:|---:|")

    for preset_name, usages in PRESETS.items():
        for sheets in MONTHLY_SHEET_COUNTS:
            estimate = estimate_monthly_cost(usages, sheets, pricing_config=pricing_config)
            print(
                f"| {preset_name} | {sheets} | {format_money(estimate['sheet_cost'])} | {format_money(estimate['monthly_cost'])} |"
            )

    print("\nStage breakdown for 200-sheet scenario")
    for preset_name, usages in PRESETS.items():
        estimate = estimate_monthly_cost(usages, 200, pricing_config=pricing_config)
        print(f"\nPreset: {preset_name}")
        for stage in estimate["stages"]:
            print(
                f"- {stage['stage_name']}: model={stage['model']}, input_cost={format_money(stage['input_cost'])}, "
                f"cached_input_cost={format_money(stage['cached_input_cost'])}, output_cost={format_money(stage['output_cost'])}, "
                f"stage_cost={format_money(stage['stage_cost'])}"
            )


def _validate_tokens(input_tokens, cached_input_tokens, output_tokens):
    for label, value in [
        ("input_tokens", input_tokens),
        ("cached_input_tokens", cached_input_tokens),
        ("output_tokens", output_tokens),
    ]:
        if not isinstance(value, int):
            raise ValueError(f"{label} must be integer")
        if value < 0:
            raise ValueError(f"{label} cannot be negative")


def _parse_stage_arg(stage_arg):
    # stage_name:model:input_tokens:cached_input_tokens:output_tokens
    parts = stage_arg.split(":")
    if len(parts) != 5:
        raise ValueError(
            "Invalid --stage format. Expected stage_name:model:input_tokens:cached_input_tokens:output_tokens"
        )
    stage_name, model, input_tokens, cached_input_tokens, output_tokens = parts
    return {
        "stage_name": stage_name,
        "model": model,
        "input_tokens": int(input_tokens),
        "cached_input_tokens": int(cached_input_tokens),
        "output_tokens": int(output_tokens),
    }


def main():
    parser = argparse.ArgumentParser(description="OpenAI cost calculator for order-sheet pipeline")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="Path to pricing config JSON")
    parser.add_argument("--sheet-count", type=int, default=200, help="Number of sheets per month")
    parser.add_argument("--preset", choices=list(PRESETS.keys()), help="Preset usage scenario")
    parser.add_argument(
        "--stage",
        action="append",
        default=[],
        help="Custom stage assumption: stage_name:model:input_tokens:cached_input_tokens:output_tokens",
    )
    parser.add_argument("--compare", action="store_true", help="Print comparison table for all presets")
    args = parser.parse_args()

    pricing_config = load_pricing_config(args.config)

    if args.compare:
        print_comparison_table(pricing_config=pricing_config)
        return

    if args.preset:
        stage_usages = PRESETS[args.preset]
    elif args.stage:
        stage_usages = [_parse_stage_arg(s) for s in args.stage]
    else:
        raise ValueError("Provide --preset or at least one --stage")

    estimate = estimate_monthly_cost(stage_usages, args.sheet_count, pricing_config=pricing_config)

    display = {
        "currency": estimate["currency"],
        "stages": estimate["stages"],
        "sheet_cost": round(estimate["sheet_cost"], 2),
        "sheet_count": estimate["sheet_count"],
        "monthly_cost": round(estimate["monthly_cost"], 2),
    }
    print(json.dumps(display, indent=2))


if __name__ == "__main__":
    main()

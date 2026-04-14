#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.pipeline.pipeline import run_order_sheet_pipeline


def main():
    parser = argparse.ArgumentParser(description="Run production-safe order-sheet pipeline")
    parser.add_argument("--input", required=True, help="Input file path (.xlsx/.csv/.json)")
    parser.add_argument("--output-dir", required=True, help="Output directory for JSON artifacts")
    parser.add_argument("--sheet-name", default="", help="Excel sheet name")
    parser.add_argument("--team-name", default="", help="Team name for document meta")
    parser.add_argument(
        "--mode",
        choices=["safe_gpt54", "hybrid", "cost_optimized"],
        default="safe_gpt54",
        help="Model routing mode for LLM stages",
    )
    parser.add_argument("--use-llm-normalizer", action="store_true", help="Use LLM normalization prompt")
    parser.add_argument("--skip-ai-validator", action="store_true", help="Skip AI semantic validator")
    parser.add_argument("--policy-json", default="", help="Optional JSON policy override")
    parser.add_argument("--mapping-json", default="", help="Optional JSON mapping override")
    args = parser.parse_args()

    policy = json.loads(args.policy_json) if args.policy_json else None
    mapping_rules = json.loads(args.mapping_json) if args.mapping_json else None

    results = run_order_sheet_pipeline(
        input_file=args.input,
        output_dir=args.output_dir,
        sheet_name=args.sheet_name or None,
        team_name=args.team_name,
        mapping_rules=mapping_rules,
        policy=policy,
        use_llm_normalizer=args.use_llm_normalizer,
        use_ai_validator=not args.skip_ai_validator,
        mode=args.mode,
    )

    print(json.dumps({"status": results["approval_decision"]["status"], "output_dir": args.output_dir}, indent=2))


if __name__ == "__main__":
    main()

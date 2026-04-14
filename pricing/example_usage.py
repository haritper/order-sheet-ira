#!/usr/bin/env python3
from pricing.cost_calculator import PRESETS, estimate_monthly_cost, load_pricing_config, print_comparison_table


def main():
    pricing_config = load_pricing_config()

    estimate = estimate_monthly_cost(PRESETS["hybrid"], sheet_count=200, pricing_config=pricing_config)
    print("Hybrid 200-sheet estimate")
    print(estimate)

    print("\nPreset comparison table")
    print_comparison_table(pricing_config=pricing_config)


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from flask import current_app


def today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def cache_path() -> Path:
    return Path(current_app.config["PRICING_FX_CACHE_PATH"])


def read_cache() -> dict[str, Any] | None:
    path = cache_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def write_cache(payload: dict[str, Any]) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n")


def fetch_latest_rate(base_currency: str, quote_currency: str) -> dict[str, Any]:
    url = (
        "https://api.frankfurter.app/latest"
        f"?from={base_currency}&to={quote_currency}"
    )
    request = Request(url, headers={"User-Agent": "pricing-intelligence/1.0"})
    with urlopen(request, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rate = float(payload["rates"][quote_currency])
    snapshot = {
        "base_currency": base_currency,
        "quote_currency": quote_currency,
        "rate": rate,
        "provider": "Frankfurter",
        "provider_date": payload.get("date"),
        "fetched_on_utc": today_utc(),
        "stale": False,
    }
    write_cache(snapshot)
    return snapshot


def get_daily_fx_snapshot(force_refresh: bool = False) -> dict[str, Any]:
    base_currency = current_app.config["PRICING_SOURCE_CURRENCY"]
    quote_currency = current_app.config["PRICING_DISPLAY_CURRENCY"]

    if base_currency == quote_currency:
        return {
            "base_currency": base_currency,
            "quote_currency": quote_currency,
            "rate": 1.0,
            "provider": "Identity",
            "provider_date": today_utc(),
            "fetched_on_utc": today_utc(),
            "stale": False,
        }

    cached = read_cache()
    if (
        not force_refresh
        and cached
        and cached.get("base_currency") == base_currency
        and cached.get("quote_currency") == quote_currency
        and cached.get("fetched_on_utc") == today_utc()
    ):
        return cached

    try:
        return fetch_latest_rate(base_currency, quote_currency)
    except (KeyError, ValueError, TypeError, URLError):
        if cached:
            cached["stale"] = True
            return cached
        return {
            "base_currency": base_currency,
            "quote_currency": quote_currency,
            "rate": 1.0,
            "provider": "Fallback",
            "provider_date": None,
            "fetched_on_utc": today_utc(),
            "stale": True,
        }


def convert_source_to_display(amount: float | int | None, snapshot: dict[str, Any] | None = None) -> float:
    if amount in (None, ""):
        return 0.0
    fx_snapshot = snapshot or get_daily_fx_snapshot()
    return round(float(amount) * float(fx_snapshot["rate"]), 2)


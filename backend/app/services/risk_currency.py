"""Currency-aware risk aggregation: the metric dimension map plus a pure
aggregator that groups money metrics by currency and keeps currency-invariant
metrics in one shared block. No pricing / DB dependency — unit-testable in
isolation."""
from __future__ import annotations

from typing import Any

# Money metrics scale with FX (price-denominated, or a money derivative of a
# dimensionless input). Shared metrics are currency-invariant ratios / underlying
# units (delta = dPrice/dSpot is money/money; gamma is 1/money; delta_proxy is a
# share count) and must NEVER be FX-converted.
MONEY_METRIC_KEYS: frozenset[str] = frozenset({
    "market_value", "gross_notional", "pnl",
    "vega", "theta", "rho", "rho_q",
    "delta_cash", "gamma_cash",
    "one_day_var_proxy",
})
SHARED_METRIC_KEYS: frozenset[str] = frozenset({"delta", "gamma", "delta_proxy"})


def metric_dimension(key: str) -> str:
    if key in MONEY_METRIC_KEYS:
        return "money"
    if key in SHARED_METRIC_KEYS:
        return "shared"
    raise KeyError(f"Unknown risk metric: {key}")


def _empty_money_bucket() -> dict[str, float]:
    return {key: 0.0 for key in MONEY_METRIC_KEYS} | {"position_count": 0}


def build_currency_aware_totals(
    per_position: list[tuple[str, dict[str, float]]],
) -> dict[str, Any]:
    """Group money metrics by currency, pool shared metrics into one block.

    `per_position` is a list of (currency, contribution) where contribution is a
    totals-shaped dict keyed by the metric names. Returns:
      by_currency: {ccy: {money_key: sum, position_count: n}}
      shared:      {shared_key: sum}      # one set, currency-invariant
      totals:      flat legacy dict | None  # populated only when 1 currency
      mixed_currency: bool
      currencies:  sorted list
    """
    by_currency: dict[str, dict[str, float]] = {}
    shared: dict[str, float] = {key: 0.0 for key in SHARED_METRIC_KEYS}

    for currency, contribution in per_position:
        ccy = currency or "UNKNOWN"
        bucket = by_currency.setdefault(ccy, _empty_money_bucket())
        bucket["position_count"] += 1
        for key in MONEY_METRIC_KEYS:
            bucket[key] += float(contribution.get(key, 0.0) or 0.0)
        for key in SHARED_METRIC_KEYS:
            shared[key] += float(contribution.get(key, 0.0) or 0.0)

    currencies = sorted(by_currency)
    mixed = len(currencies) > 1
    if not mixed and currencies:
        only = currencies[0]
        totals: dict[str, float] | None = {
            key: by_currency[only][key] for key in MONEY_METRIC_KEYS
        } | dict(shared)
    elif not currencies:
        totals = {key: 0.0 for key in MONEY_METRIC_KEYS} | dict(shared)
    else:
        totals = None

    return {
        "by_currency": by_currency,
        "shared": shared,
        "totals": totals,
        "mixed_currency": mixed,
        "currencies": currencies,
    }

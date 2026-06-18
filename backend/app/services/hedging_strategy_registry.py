# backend/app/services/hedging_strategy_registry.py
from __future__ import annotations

from copy import deepcopy

# strategy -> ordered tiers; each tier minimized lexicographically.
STRATEGIES: dict[str, list[dict]] = {
    "delta_neutral": [
        {"kind": "hard", "greeks": ["delta"]},
    ],
    "delta_neutral_enhanced": [
        {"kind": "hard", "greeks": ["delta"]},
        {"kind": "soft", "greeks": ["gamma", "vega"]},
    ],
    "delta_gamma_neutral": [
        {"kind": "hard", "greeks": ["delta", "gamma"]},
        {"kind": "soft", "greeks": ["vega"]},
    ],
    "full_neutral": [
        {"kind": "hard", "greeks": ["delta", "gamma", "vega"]},
    ],
}


def tiers_for(strategy: str) -> list[dict]:
    return deepcopy(STRATEGIES[strategy])

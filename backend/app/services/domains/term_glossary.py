"""Canonical desk phrases for contract keys — the test-side vocabulary that
lets the coherence net check reference-doc prose against FamilyContracts.

Keys are the full dotted paths from product_contracts; phrases are the
strings a doc's Pricing Inputs section must contain (any one suffices;
first is canonical). Scope is strictly required_bound + defaulted keys."""
from __future__ import annotations

TERM_GLOSSARY: dict[str, tuple[str, ...]] = {
    "initial_price": ("initial price", "spot"),
    "maturity_years": ("maturity", "tenor"),
    "trade_start_date": ("trade start date",),
    "observation_frequency": ("observation frequency", "observation schedule"),
    "barrier_config.ko_barrier": ("KO barrier", "knock-out barrier"),
    "barrier_config.ki_barrier": ("KI barrier", "knock-in barrier"),
    "barrier_config.ko_rate": ("KO coupon", "coupon"),
    "barrier_config.lockup_months": ("lockup",),
    "ko_observation_dates": ("KO observation dates", "observation dates"),
    "ki_convention": ("KI convention",),
    "ko_rate_annualized": ("annualized", "coupon convention"),
    "initial_date": ("initial date",),
    "settlement_date": ("settlement date",),
    "post_barrier_config.ko_barrier": ("post-KI KO barrier", "KO barrier"),
    "post_barrier_config.ko_rate": ("post-KI KO coupon", "KO coupon"),
    "coupon_config.coupon_barrier": ("coupon barrier",),
    "coupon_config.coupon_rate": ("coupon rate",),
    "memory_coupon": ("memory coupon",),
    "strike": ("strike",),
    "option_type": ("option type", "call or put"),
    "contract_multiplier": ("contract multiplier",),
    "averaging_frequency": ("averaging frequency", "averaging schedule"),
    "cash_payoff": ("cash payoff", "fixed cash amount"),
    "barrier": ("barrier",),
    "barrier_type": ("barrier type",),
    "rebate": ("rebate",),
    "participation_rate": ("participation rate",),
    "lower_barrier": ("lower barrier",),
    "upper_barrier": ("upper barrier",),
    "barrier_direction": ("barrier direction",),
    "touch_type": ("touch type",),
    "range_config.lower_barrier": ("lower barrier",),
    "range_config.upper_barrier": ("upper barrier",),
    "range_config.accrual_rate": ("accrual rate",),
    "underlying": ("underlying",),
    "basis": ("basis",),
    "basis_decay_rate": ("basis decay",),
    "market_price": ("market price",),
    "contract_code": ("contract code",),
    "deltaone_type": ("delta-one type",),
    "instrument_code": ("instrument code",),
    "exchange": ("exchange",),
}

# Region-market tokens forbidden in region-neutral product docs. Region
# overlays (frontmatter `region:`) are exempt. Extend in one place.
REGION_TOKEN_DENYLIST: tuple[str, ...] = (
    "SSE",
    "China Mainland",
    "CSI",
    "A-share",
)


def glossary_phrases(key: str) -> tuple[str, ...]:
    if key in TERM_GLOSSARY:
        return TERM_GLOSSARY[key]
    leaf = key.rsplit(".", 1)[-1]
    return TERM_GLOSSARY.get(leaf, ())

"""Per-family term contracts — the declarative source of truth for which economic
inputs a family needs.

A contract is *data*: the required-bound inputs a channel must collect, the
defaulted inputs it may omit, and the fields that are eligible to be an RFQ /
try-solve free variable (the solve target). Builders report required-bound
inputs that are absent in `missing` imperatively — the contract does not drive
`missing`; it declares the union of keys a family can report. The designated
solve target is exempt (it arrives as bounds + initial guess, not a value — see
the unified-product schema design, decision 6).

Every buildable family now has a declared contract; the builder<->contract
consistency test (`tests/test_product_contracts.py`) parametrizes over the whole
registry so builder and contract cannot drift. Contracts remain declarative —
builders still compute `missing` imperatively; the contract declares the union of
keys a family can report.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FamilyContract:
    quantark_class: str
    required_bound: tuple[str, ...]
    defaulted: tuple[str, ...]
    solvable: tuple[str, ...]


# Snowball / autocallable base. KO-reset and Phoenix build on the snowball
# synthesizer and add their own required keys imperatively, so they share this
# base contract for the shared inputs. NOTE: the shared contract's
# `quantark_class` field stays "SnowballOption" (the synthesizer class) and is
# advisory — it may not match the lookup key for KnockOutResetSnowballOption /
# PhoenixOption. Look up by the dict key, not by reading `.quantark_class`.
_SNOWBALL_CONTRACT = FamilyContract(
    quantark_class="SnowballOption",
    required_bound=(
        "initial_price",
        "maturity_years",
        "trade_start_date",
        "observation_frequency",
        "barrier_config.ko_barrier",
        "barrier_config.ki_barrier",
        "barrier_config.ko_rate",
        "barrier_config.lockup_months",
        # required_bound is the UNION of keys that can be required; the builder
        # reports ko_observation_dates missing ONLY when observation_frequency ==
        # CUSTOM. A conditionally-required split is deferred until a consumer
        # iterates required_bound for prompting.
        "ko_observation_dates",
    ),
    defaulted=(
        "ki_convention",
        "ko_rate_annualized",
        "initial_date",
        "settlement_date",
    ),
    solvable=(
        "barrier_config.ko_rate",
        "barrier_config.ki_barrier",
        "coupon_rate",
    ),
)

# KO-reset and Phoenix build ON the snowball synthesizer and add their own
# required keys imperatively (post-KI reset leg / coupon leg). They get their own
# contracts = the snowball base + those extra required keys, so the consistency
# net does not under-declare them.
_KO_RESET_CONTRACT = FamilyContract(
    quantark_class="KnockOutResetSnowballOption",
    required_bound=_SNOWBALL_CONTRACT.required_bound
    + ("post_barrier_config.ko_barrier", "post_barrier_config.ko_rate"),
    defaulted=_SNOWBALL_CONTRACT.defaulted,
    solvable=_SNOWBALL_CONTRACT.solvable,
)
_PHOENIX_CONTRACT = FamilyContract(
    quantark_class="PhoenixOption",
    required_bound=_SNOWBALL_CONTRACT.required_bound
    + ("coupon_config.coupon_barrier", "coupon_config.coupon_rate"),
    defaulted=_SNOWBALL_CONTRACT.defaulted + ("memory_coupon",),
    solvable=_SNOWBALL_CONTRACT.solvable,
)

# Vanilla family: _build_vanilla descendants require S0 + maturity + strike and
# default option_type/contract_multiplier. `solvable` is advisory and left empty
# for the non-snowball families (no consumer reads contract.solvable for them yet;
# filter_solved uses the runtime solve_target).
_VANILLA_REQUIRED = ("initial_price", "maturity_years", "strike")
_VANILLA_DEFAULTED = ("option_type", "contract_multiplier")

_CONTRACTS: dict[str, FamilyContract] = {
    "SnowballOption": _SNOWBALL_CONTRACT,
    "KnockOutResetSnowballOption": _KO_RESET_CONTRACT,
    "PhoenixOption": _PHOENIX_CONTRACT,
    "EuropeanVanillaOption": FamilyContract(
        "EuropeanVanillaOption", _VANILLA_REQUIRED, _VANILLA_DEFAULTED, ()
    ),
    "AmericanOption": FamilyContract(
        "AmericanOption", _VANILLA_REQUIRED, _VANILLA_DEFAULTED, ()
    ),
    "AsianOption": FamilyContract(
        "AsianOption", _VANILLA_REQUIRED, _VANILLA_DEFAULTED + ("averaging_frequency",), ()
    ),
    "CashOrNothingDigitalOption": FamilyContract(
        "CashOrNothingDigitalOption",
        _VANILLA_REQUIRED + ("cash_payoff",),
        _VANILLA_DEFAULTED,
        (),
    ),
    "BarrierOption": FamilyContract(
        "BarrierOption",
        _VANILLA_REQUIRED + ("barrier",),
        _VANILLA_DEFAULTED + ("barrier_type", "rebate"),
        (),
    ),
    "SingleSharkfinOption": FamilyContract(
        "SingleSharkfinOption",
        _VANILLA_REQUIRED + ("barrier",),
        _VANILLA_DEFAULTED + ("participation_rate",),
        (),
    ),
    "DoubleSharkfinOption": FamilyContract(
        "DoubleSharkfinOption",
        _VANILLA_REQUIRED + ("lower_barrier", "upper_barrier"),
        _VANILLA_DEFAULTED + ("participation_rate",),
        (),
    ),
    "OneTouchOption": FamilyContract(
        "OneTouchOption",
        ("initial_price", "maturity_years", "barrier", "cash_payoff"),
        ("barrier_direction", "touch_type"),
        (),
    ),
    "DoubleOneTouchOption": FamilyContract(
        "DoubleOneTouchOption",
        ("initial_price", "maturity_years", "upper_barrier", "lower_barrier", "cash_payoff"),
        ("touch_type",),
        (),
    ),
    "RangeAccrualOption": FamilyContract(
        "RangeAccrualOption",
        (
            "initial_price",
            "maturity_years",
            "range_config.lower_barrier",
            "range_config.upper_barrier",
            "range_config.accrual_rate",
        ),
        ("observation_frequency", "contract_multiplier"),
        (),
    ),
    "Futures": FamilyContract(
        "Futures",
        ("initial_price", "underlying"),
        ("contract_multiplier", "maturity_years", "basis", "basis_decay_rate",
         "market_price", "contract_code"),
        (),
    ),
    "SpotInstrument": FamilyContract(
        "SpotInstrument",
        ("initial_price", "underlying"),
        ("deltaone_type", "instrument_code", "exchange", "contract_multiplier"),
        (),
    ),
}


def contract_for(quantark_class: str | None) -> FamilyContract | None:
    return _CONTRACTS.get(quantark_class or "")


def filter_solved(missing: list[str], *, solve_target: str | None) -> list[str]:
    """Drop the one designated solve target from a missing list (it is supplied
    as bounds + initial guess, not a bound value). All other gaps stand."""
    if not solve_target:
        return list(missing)
    return [key for key in missing if key != solve_target]

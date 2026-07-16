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
from typing import Any


@dataclass(frozen=True)
class FieldSpec:
    """One term-sheet field a family accepts, described for the agent.

    ``input_name`` is the flat, builder-facing key the model fills (what
    build_product's synthesize path reads); ``contract_path`` is the dotted
    ``required_bound`` key when it differs (nested-config families). Enum values come
    from ``enum_ref`` (live-introspected from a quant-ark enum class, ONLY where every
    member round-trips) or ``enum_values`` (builder-faithful literals) — never both.
    ``one_of`` groups mutually-exclusive alternatives (e.g. maturity_years|maturity_date).
    """

    input_name: str
    kind: str  # "number" | "date" | "enum" | "string" | "bool"
    description: str
    contract_path: str | None = None
    default: Any | None = None
    enum_ref: str | None = None
    enum_values: tuple[str, ...] | None = None
    one_of: str | None = None


def resolve_enum_values(spec: FieldSpec) -> tuple[str, ...]:
    """Legal values for an enum field: declared literals, or live-introspected from a
    quant-ark enum class. Non-enum -> empty."""
    if spec.enum_values is not None:
        return tuple(spec.enum_values)
    if spec.enum_ref is not None:
        from quantark.util import enum as qenum

        enum_cls = getattr(qenum, spec.enum_ref)
        return tuple(member.name for member in enum_cls)
    return ()


@dataclass(frozen=True)
class FamilyContract:
    quantark_class: str
    required_bound: tuple[str, ...]
    defaulted: tuple[str, ...]
    solvable: tuple[str, ...]
    fields: tuple[FieldSpec, ...] = ()


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

# --- Term-schema FieldSpecs (V1: flat option families) -----------------------
# Only builders routed through `_common_option` (`_build_vanilla` descendants + sharkfin)
# accept `maturity_date`, so only they advertise the maturity one_of; one-touch/double/
# asian read `maturity_years` directly and use `_TENOR`. Enum values are live
# (`enum_ref`) only where every member round-trips to a correct build (barrier_type,
# option_type, barrier_direction); touch_type/frequency use builder-faithful literals.
_S0 = FieldSpec("initial_price", "number", "Initial fixing S0 / valuation spot.")
_STRIKE = FieldSpec("strike", "number", "Strike price.")
_MULT = FieldSpec("contract_multiplier", "number", "Contract multiplier (default 1.0).", default=1.0)
_OPTION_TYPE = FieldSpec("option_type", "enum", "CALL or PUT.", default="CALL", enum_ref="OptionType")
_MATURITY_YEARS = FieldSpec("maturity_years", "number",
                            "Tenor in years. Supply exactly one of the 'maturity' group.",
                            one_of="maturity")
_MATURITY_DATE = FieldSpec("maturity_date", "date",
                           "Explicit expiry date (ISO). Supply exactly one of the 'maturity' group.",
                           one_of="maturity")
_TENOR = FieldSpec("maturity_years", "number", "Tenor in years.")
_VANILLA_FIELDS = (_S0, _MATURITY_YEARS, _MATURITY_DATE, _STRIKE, _OPTION_TYPE, _MULT)
_VANILLA_TENOR_FIELDS = (_S0, _TENOR, _STRIKE, _OPTION_TYPE, _MULT)

_CONTRACTS: dict[str, FamilyContract] = {
    "SnowballOption": _SNOWBALL_CONTRACT,
    "KnockOutResetSnowballOption": _KO_RESET_CONTRACT,
    "PhoenixOption": _PHOENIX_CONTRACT,
    "EuropeanVanillaOption": FamilyContract(
        "EuropeanVanillaOption", _VANILLA_REQUIRED, _VANILLA_DEFAULTED, (),
        fields=_VANILLA_FIELDS,
    ),
    "AmericanOption": FamilyContract(
        "AmericanOption", _VANILLA_REQUIRED, _VANILLA_DEFAULTED, (),
        fields=_VANILLA_FIELDS,
    ),
    "AsianOption": FamilyContract(
        "AsianOption", _VANILLA_REQUIRED, _VANILLA_DEFAULTED + ("averaging_frequency",), (),
        fields=_VANILLA_TENOR_FIELDS + (
            FieldSpec("averaging_frequency", "enum", "Averaging observation frequency.",
                      default="MONTHLY",
                      enum_values=("DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "SEMI_ANNUAL")),
        ),
    ),
    "CashOrNothingDigitalOption": FamilyContract(
        "CashOrNothingDigitalOption",
        _VANILLA_REQUIRED + ("cash_payoff",),
        _VANILLA_DEFAULTED,
        (),
        fields=_VANILLA_FIELDS + (
            FieldSpec("cash_payoff", "number", "Cash payout if in-the-money."),
        ),
    ),
    "BarrierOption": FamilyContract(
        "BarrierOption",
        _VANILLA_REQUIRED + ("barrier",),
        _VANILLA_DEFAULTED + ("barrier_type", "rebate"),
        (),
        fields=_VANILLA_FIELDS + (
            FieldSpec("barrier", "number", "Barrier price level."),
            FieldSpec("barrier_type", "enum", "Barrier direction + gating.",
                      default="DOWN_OUT", enum_ref="BarrierType"),
            FieldSpec("rebate", "number", "Rebate paid on knock (default 0).", default=0.0),
        ),
    ),
    "SingleSharkfinOption": FamilyContract(
        "SingleSharkfinOption",
        _VANILLA_REQUIRED + ("barrier",),
        _VANILLA_DEFAULTED + ("participation_rate",),
        (),
        fields=_VANILLA_FIELDS + (
            FieldSpec("barrier", "number", "Sharkfin barrier level."),
            FieldSpec("participation_rate", "number", "Upside participation (default 1.0).", default=1.0),
        ),
    ),
    "DoubleSharkfinOption": FamilyContract(
        "DoubleSharkfinOption",
        _VANILLA_REQUIRED + ("lower_barrier", "upper_barrier"),
        _VANILLA_DEFAULTED + ("participation_rate",),
        (),
        fields=_VANILLA_FIELDS + (
            FieldSpec("lower_barrier", "number", "Lower barrier level."),
            FieldSpec("upper_barrier", "number", "Upper barrier level."),
            FieldSpec("participation_rate", "number", "Upside participation (default 1.0).", default=1.0),
        ),
    ),
    "OneTouchOption": FamilyContract(
        "OneTouchOption",
        ("initial_price", "maturity_years", "barrier", "cash_payoff"),
        ("barrier_direction", "touch_type"),
        (),
        fields=(_S0, _TENOR,
                FieldSpec("barrier", "number", "Barrier level."),
                FieldSpec("cash_payoff", "number", "Cash paid on touch."),
                FieldSpec("barrier_direction", "enum", "UP or DOWN.", default="UP",
                          enum_ref="BarrierDirection"),
                FieldSpec("touch_type", "enum", "Single touch/no-touch.", default="ONE_TOUCH",
                          enum_values=("ONE_TOUCH", "NO_TOUCH"))),
    ),
    "DoubleOneTouchOption": FamilyContract(
        "DoubleOneTouchOption",
        ("initial_price", "maturity_years", "upper_barrier", "lower_barrier", "cash_payoff"),
        ("touch_type",),
        (),
        fields=(_S0, _TENOR,
                FieldSpec("upper_barrier", "number", "Upper barrier level."),
                FieldSpec("lower_barrier", "number", "Lower barrier level."),
                FieldSpec("cash_payoff", "number", "Cash paid on touch."),
                FieldSpec("touch_type", "enum", "Double touch/no-touch.",
                          default="DOUBLE_ONE_TOUCH",
                          enum_values=("DOUBLE_ONE_TOUCH", "DOUBLE_NO_TOUCH"))),
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


def one_of_groups(contract: FamilyContract) -> dict[str, tuple[str, ...]]:
    """Alternative groups this family declares (group_id -> member contract paths),
    derived from the family's OWN FieldSpecs. A family that does not opt in (empty
    ``fields``) has no groups, so a maturity alternative can never leak into it."""
    groups: dict[str, list[str]] = {}
    for spec in contract.fields:
        if spec.one_of is not None:
            groups.setdefault(spec.one_of, []).append(spec.contract_path or spec.input_name)
    return {group: tuple(members) for group, members in groups.items()}


def _present(terms: dict, key: str) -> bool:
    if key in terms and terms[key] not in (None, ""):
        return True
    node: Any = terms
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return node not in (None, "")


def required_fields(contract: FamilyContract, terms: dict) -> list[str]:
    """Required contract paths given collected terms: ``required_bound`` minus the
    conditional ``ko_observation_dates`` rule, with THIS family's declared ``one_of``
    groups collapsed (a group is satisfied when any member is present)."""
    required = list(contract.required_bound)
    freq = terms.get("observation_frequency")
    if (
        "ko_observation_dates" in required
        and freq not in (None, "")
        and str(freq).strip().upper() != "CUSTOM"
    ):
        required.remove("ko_observation_dates")
    groups = one_of_groups(contract)
    member_to_group = {m: g for g, members in groups.items() for m in members}
    out: list[str] = []
    seen_groups: set[str] = set()
    for key in required:
        group = member_to_group.get(key)
        if group is None:
            out.append(key)
            continue
        if group in seen_groups:
            continue
        seen_groups.add(group)
        if not any(_present(terms, member) for member in groups[group]):
            out.append(groups[group][0])
    return out


def filter_solved(missing: list[str], *, solve_target: str | None) -> list[str]:
    """Drop the one designated solve target from a missing list (it is supplied
    as bounds + initial guess, not a bound value). All other gaps stand."""
    if not solve_target:
        return list(missing)
    return [key for key in missing if key != solve_target]

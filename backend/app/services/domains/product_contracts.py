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
    ``input_aliases`` are additional flat spellings that ALL satisfy this spec's one
    ``contract_path`` (e.g. ``ko_barrier``/``ko_barrier_pct`` — one value, two spellings;
    NOT a one_of). ``requires_when`` makes the field required only when a sibling field
    equals a value; a ``!``-prefixed value means "required UNLESS it equals X".
    """

    input_name: str
    kind: str  # "number" | "date" | "enum" | "string" | "bool"
    description: str
    contract_path: str | None = None
    default: Any | None = None
    enum_ref: str | None = None
    enum_values: tuple[str, ...] | None = None
    one_of: str | None = None
    input_aliases: tuple[str, ...] = ()
    requires_when: tuple[str, str] | None = None


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


def _aliases_for(spec: FieldSpec) -> tuple[str, ...]:
    """Flat input spellings that satisfy this spec's contract_path (>= its input_name)."""
    return spec.input_aliases or (spec.input_name,)


def _requires_when_active(spec: FieldSpec, terms: dict) -> bool:
    """Is this spec's requirement active given collected terms? Unconditional when
    requires_when is None. A '!X' value means active UNLESS the field equals X."""
    if spec.requires_when is None:
        return True
    field, value = spec.requires_when
    actual = str(terms.get(field, "") or "").strip().upper()
    if value.startswith("!"):
        return actual != value[1:].strip().upper()
    return actual == value.strip().upper()


@dataclass(frozen=True)
class FamilyContract:
    quantark_class: str
    required_bound: tuple[str, ...]
    defaulted: tuple[str, ...]
    solvable: tuple[str, ...]
    fields: tuple[FieldSpec, ...] = ()


# --- Term-schema FieldSpec building blocks -----------------------------------
# Defined BEFORE the contracts so nested-config contracts can reference the shared
# nested field blocks. Enum values are live (`enum_ref`) only where every member
# round-trips to a correct build; touch_type/frequency/ki_convention use
# builder-faithful literals (the builder gates on those exact strings — a live enum
# whose members differ would certify an economically-wrong term sheet).
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


def _barrier(name: str, path: str, desc: str, **kw: Any) -> FieldSpec:
    """A barrier FieldSpec accepting both absolute and `_pct`-of-initial spellings —
    an input-alias set (one value, two spellings), NOT a one_of."""
    return FieldSpec(name, "number", desc, contract_path=path,
                     input_aliases=(name, f"{name}_pct"), **kw)


# Nested-config shared blocks. observation_frequency / ki_convention are builder-faithful
# literals (the snowball synthesizer gates on these exact strings).
_OBS_FREQ_SNOWBALL = FieldSpec(
    "observation_frequency", "enum", "KO observation frequency.",
    enum_values=("MONTHLY", "QUARTERLY", "SEMI_ANNUAL", "CUSTOM"))
_KI_CONVENTION = FieldSpec(
    "ki_convention", "enum", "KI monitoring convention (NONE = no knock-in leg).",
    default="DAILY", enum_values=("DAILY", "EUROPEAN", "NONE"))
_KO_RATE = FieldSpec("ko_rate", "number", "KO coupon rate.", contract_path="barrier_config.ko_rate")

_SNOWBALL_FIELDS = (
    _S0, _TENOR,
    # strike defaults to initial_price; advertise it so a non-par strike is not silently dropped.
    FieldSpec("strike", "number", "Strike price (defaults to initial_price if omitted)."),
    FieldSpec("trade_start_date", "date", "Trade / first-observation start date (ISO)."),
    _OBS_FREQ_SNOWBALL,
    _barrier("ko_barrier", "barrier_config.ko_barrier", "Knock-out barrier (abs level or % of initial)."),
    _barrier("ki_barrier", "barrier_config.ki_barrier", "Knock-in barrier (abs level or % of initial).",
             requires_when=("ki_convention", "!NONE")),
    _KO_RATE,
    FieldSpec("lockup_months", "number", "Lock-up months before the first KO observation.",
              contract_path="barrier_config.lockup_months"),
    # kind=date_list: the builder requires a NON-EMPTY list of ISO dates, not a scalar.
    FieldSpec("ko_observation_dates", "date_list",
              "Explicit KO observation dates — a non-empty list of ISO dates; CUSTOM freq only.",
              requires_when=("observation_frequency", "CUSTOM")),
    _KI_CONVENTION,
    # Optional economic inputs the builder honors — advertised so the schema-driven flow does
    # not drop them and silently fall back to a default that changes the payoff/schedule.
    FieldSpec("ko_rate_annualized", "bool", "KO coupon rate is annualized (default false).",
              default=False),
    FieldSpec("include_principal", "bool", "Include principal in the payoff (default false).",
              default=False),
    _MULT,
)
# Phoenix defaults the KO-leg rate to 0, so it declares ko_rate as DEFAULTED (not a
# required field) — otherwise the schema would tell the model to invent an unneeded number.
_SNOWBALL_FIELDS_NO_KO_RATE = tuple(f for f in _SNOWBALL_FIELDS if f is not _KO_RATE)

# Vanilla family: _build_vanilla descendants require S0 + maturity + strike and
# default option_type/contract_multiplier. `solvable` is advisory and left empty
# for the non-snowball families (no consumer reads contract.solvable for them yet;
# filter_solved uses the runtime solve_target).
_VANILLA_REQUIRED = ("initial_price", "maturity_years", "strike")
_VANILLA_DEFAULTED = ("option_type", "contract_multiplier")


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
        # CUSTOM (encoded on the FieldSpec via requires_when).
        "ko_observation_dates",
    ),
    defaulted=(
        "ki_convention",
        "ko_rate_annualized",
        "initial_date",
        "settlement_date",
        "strike",
        "contract_multiplier",
        "include_principal",
    ),
    solvable=(
        "barrier_config.ko_rate",
        "barrier_config.ki_barrier",
        "coupon_rate",
    ),
    fields=_SNOWBALL_FIELDS,
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
    fields=_SNOWBALL_FIELDS + (
        _barrier("post_ko_barrier", "post_barrier_config.ko_barrier",
                 "Post-KI reset KO barrier (abs level or % of initial)."),
        FieldSpec("post_ko_rate", "number", "Post-reset KO coupon rate.",
                  contract_path="post_barrier_config.ko_rate"),
    ),
)
# Phoenix defaults barrier_config.ko_rate to 0 (the KO leg pays 0; coupons come from
# coupon_config), so it drops that key from required_bound and advertises ko_rate as
# defaulted.
_PHOENIX_CONTRACT = FamilyContract(
    quantark_class="PhoenixOption",
    required_bound=tuple(
        k for k in _SNOWBALL_CONTRACT.required_bound if k != "barrier_config.ko_rate"
    ) + ("coupon_config.coupon_barrier", "coupon_config.coupon_rate"),
    defaulted=_SNOWBALL_CONTRACT.defaulted + ("memory_coupon", "barrier_config.ko_rate"),
    solvable=_SNOWBALL_CONTRACT.solvable,
    fields=_SNOWBALL_FIELDS_NO_KO_RATE + (
        FieldSpec("ko_rate", "number", "KO-leg coupon rate (Phoenix defaults 0).",
                  contract_path="barrier_config.ko_rate", default=0.0),
        _barrier("coupon_barrier", "coupon_config.coupon_barrier",
                 "Coupon (memory) barrier (abs level or % of initial)."),
        FieldSpec("coupon_rate", "number", "Coupon rate.", contract_path="coupon_config.coupon_rate"),
        FieldSpec("memory_coupon", "bool", "Memory coupon (default false).", default=False),
    ),
)

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
        fields=(
            _S0, _TENOR,
            _barrier("lower_barrier", "range_config.lower_barrier",
                     "Lower accrual barrier (abs level or % of initial)."),
            _barrier("upper_barrier", "range_config.upper_barrier",
                     "Upper accrual barrier (abs level or % of initial)."),
            FieldSpec("accrual_rate", "number", "Accrual rate.",
                      contract_path="range_config.accrual_rate"),
            # The builder prices every non-DAILY value as monthly; only DAILY/MONTHLY are
            # honored distinctly, so only those are published (economically-faithful).
            FieldSpec("observation_frequency", "enum", "Accrual observation frequency.",
                      default="DAILY", enum_values=("DAILY", "MONTHLY")),
            _MULT,
        ),
    ),
    "Futures": FamilyContract(
        "Futures",
        ("initial_price", "underlying"),
        ("contract_multiplier", "maturity_years", "basis", "basis_decay_rate",
         "market_price", "contract_code"),
        (),
        fields=(_S0, FieldSpec("underlying", "string", "Underlying symbol/name."), _MULT, _TENOR,
                FieldSpec("basis", "number", "Futures basis over spot (default 0).", default=0.0)),
    ),
    "SpotInstrument": FamilyContract(
        "SpotInstrument",
        ("initial_price", "underlying"),
        ("deltaone_type", "instrument_code", "exchange", "contract_multiplier"),
        (),
        # deltaone_type uses builder-faithful literals: the live DeltaOneType enum also has
        # FUTURES, which the SpotInstrument builder REJECTS (routes to the Futures family) — so
        # only the members that round-trip for a spot are published.
        fields=(_S0, FieldSpec("underlying", "string", "Underlying symbol/name."),
                FieldSpec("deltaone_type", "enum", "Delta-one instrument kind.", default="INDEX",
                          enum_values=("STOCK", "INDEX", "ETF")),
                _MULT),
    ),
}


def contract_for(quantark_class: str | None) -> FamilyContract | None:
    return _CONTRACTS.get(quantark_class or "")


def flat_aliases(contract: FamilyContract) -> dict[str, tuple[str, ...]]:
    """{dotted contract_path: (flat input spellings)} — list-valued so both barrier
    spellings (abs + pct) coexist. Used by the alias-aware presence checks."""
    out: dict[str, tuple[str, ...]] = {}
    for spec in contract.fields:
        path = spec.contract_path or spec.input_name
        out[path] = _aliases_for(spec)
    return out


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


def _path_present(terms: dict, path: str, aliases: tuple[str, ...] = ()) -> bool:
    """True if this field is supplied the way the SYNTHESIZE builder actually reads it: the
    flat input aliases for every field, plus the nested/dotted contract path ONLY for barrier
    alias-sets (``len(aliases) > 1``) — which are the only fields the builder resolves via the
    nested path (``_resolve_barrier``). Crediting the nested path for a flat-only field (e.g.
    ``barrier_config.ko_rate``, which the builder reads as flat ``ko_rate``) would certify a
    term the builder then reports missing. With no alias info, fall back to the path itself."""
    if not aliases:
        return _present(terms, path)
    if any(_present(terms, a) for a in aliases):
        return True
    return len(aliases) > 1 and _present(terms, path)


def active_required_paths(contract: FamilyContract, terms: dict) -> list[str]:
    """Required contract paths that are ACTIVE for these terms — ``required_bound`` minus the
    conditional ``requires_when`` rules, with this family's ``one_of`` groups collapsed to one
    slot each. NOT presence-filtered: a one_of group reports its present member if any (so the
    caller can split provided vs missing), else its canonical first member. This is the shared
    kernel for ``required_fields`` (missing) and ``check_term_completeness`` (missing+provided)."""
    aliases = flat_aliases(contract)
    path_to_spec = {(s.contract_path or s.input_name): s for s in contract.fields}
    conditional_inactive = {
        path for path, spec in path_to_spec.items()
        if spec.requires_when is not None and not _requires_when_active(spec, terms)
    }
    required = [p for p in contract.required_bound if p not in conditional_inactive]

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
        present_member = next(
            (m for m in groups[group] if _path_present(terms, m, aliases.get(m, ()))), None)
        out.append(present_member or groups[group][0])
    return out


def required_fields(contract: FamilyContract, terms: dict) -> list[str]:
    """Genuinely-missing required contract paths given collected terms: ``active_required_paths``
    with each dotted path satisfiable by its nested value OR any of its flat input aliases
    (``flat_aliases``) filtered out."""
    aliases = flat_aliases(contract)
    return [p for p in active_required_paths(contract, terms)
            if not _path_present(terms, p, aliases.get(p, ()))]


def filter_solved(missing: list[str], *, solve_target: str | None) -> list[str]:
    """Drop the one designated solve target from a missing list (it is supplied
    as bounds + initial guess, not a bound value). All other gaps stand."""
    if not solve_target:
        return list(missing)
    return [key for key in missing if key != solve_target]

"""Deterministic per-family quant-ark product builders.

The trader persona (LLM) fills a structured per-family `terms` dict; these
builders turn it into validated quant-ark product_kwargs, synthesizing
observation schedules where required. Economics that would have to be
invented (lockup, trade start, barrier levels, coupon) are reported in
`missing` — never fabricated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from ...schemas import PricingEnvironmentSnapshot
from ..quantark import validate_quantark_build
from . import product_contracts, schedules
from .products import ProductSpec, product_family_for_quantark_class

_ENGINE_BY_CLASS: dict[str, str] = {
    "EuropeanVanillaOption": "BlackScholesEngine",
    "AmericanOption": "AmericanOptionAnalyticalEngine",
    "CashOrNothingDigitalOption": "DigitalOptionAnalyticalEngine",
    "BarrierOption": "BarrierAnalyticalEngine",
    "OneTouchOption": "OneTouchAnalyticalEngine",
    "DoubleOneTouchOption": "OneTouchAnalyticalEngine",
    "AsianOption": "AsianOptionAnalyticalEngine",
    "RangeAccrualOption": "RangeAccrualAnalyticalEngine",
    "SnowballOption": "SnowballQuadEngine",
    "KnockOutResetSnowballOption": "KOResetSnowballQuadEngine",
    "PhoenixOption": "PhoenixQuadEngine",
    "SingleSharkfinOption": "SingleSharkfinOptionAnalyticalEngine",
    "DoubleSharkfinOption": "DoubleSharkfinOptionAnalyticalEngine",
    "Futures": "DeltaOneEngine",
    "SpotInstrument": "DeltaOneEngine",
}


@dataclass
class _Out:
    product_kwargs: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BuildResult:
    ok: bool
    quantark_class: str
    engine_name: str
    missing: list[str]
    warnings: list[str]
    validation: dict[str, Any] | None
    product_spec: "ProductSpec | None" = None

    @property
    def product_kwargs(self) -> dict[str, Any]:
        """Validated QuantArk kwargs. The single carrier is ``product_spec.terms``;
        this view returns ``{}`` when the build did not succeed, so partial kwargs
        never leak from a failed build."""
        return dict(self.product_spec.terms) if self.product_spec is not None else {}


# ----- shared helpers --------------------------------------------------------

def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _abs_barrier(terms: dict, abs_key: str, pct_key: str, initial: float) -> float | None:
    direct = _num(terms.get(abs_key))
    if direct is not None:
        return direct
    pct = _num(terms.get(pct_key))
    return None if pct is None else round(initial * pct / 100.0, 6)


def _initial_price(terms: dict, out: _Out) -> float:
    """The product's initial fixing S0 — required for every family, never invented.

    S0 is the contractual reference percent-of-initial barriers and strike key
    off, and it sets the validation spot. An absent value is reported in
    `missing` so the agent must supply it (suggesting the latest spot); the
    returned placeholder is only used for downstream math that is discarded
    while `missing` is non-empty (build short-circuits before validation).
    """
    val = _num(terms.get("initial_price"))
    if val is None:
        out.missing.append("initial_price")
        return 100.0
    return val


def _start_date(terms: dict) -> date | None:
    raw = terms.get("trade_start_date")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def _require(terms: dict, out: _Out, key: str, *, alias: str | None = None) -> Any:
    value = terms.get(key)
    if value is None:
        out.missing.append(alias or key)
    return value


def _common_option(terms: dict, out: _Out) -> dict:
    pk: dict[str, Any] = {"contract_multiplier": _num(terms.get("contract_multiplier")) or 1.0}
    m = _num(terms.get("maturity_years"))
    if m is None:
        out.missing.append("maturity_years")
    else:
        pk["maturity"] = m
    return pk


def _mixed_maturity_representation_error(terms: dict[str, Any]) -> str | None:
    """QuantArk accepts either tenor-style maturity or explicit date maturity.

    Passing both through the prebuilt path creates an ambiguous termsheet and can
    fail later with opaque type errors. Treat key presence as intentional here:
    callers should omit stale fields rather than send empty placeholders.
    """
    date_keys = [
        key
        for key in ("exercise_date", "maturity_date", "expiry_date", "expiry")
        if terms.get(key) not in (None, "")
    ]
    tenor_keys = [key for key in ("maturity", "maturity_years") if key in terms]
    if date_keys and tenor_keys:
        return (
            f"{tenor_keys[0]} must not be supplied when {date_keys[0]} is supplied; "
            "use either explicit dates or tenor maturity, not both"
        )
    return None


# ----- per-family builders ---------------------------------------------------

def _build_snowball(terms: dict, *, quantark_class: str) -> _Out:
    out = _Out()
    initial = _initial_price(terms, out)
    maturity = _num(terms.get("maturity_years"))
    out.product_kwargs = {
        "initial_price": initial,
        "strike": _num(terms.get("strike")) or initial,
        "maturity": maturity if maturity is not None else 1.0,
        "contract_multiplier": _num(terms.get("contract_multiplier")) or 1.0,
        "payoff_config": {"include_principal": bool(terms.get("include_principal", False))},
        "accrual_config": {},
    }
    if maturity is None:
        out.missing.append("maturity_years")

    ko_barrier = _abs_barrier(terms, "ko_barrier", "ko_barrier_pct", initial)
    ki_barrier = _abs_barrier(terms, "ki_barrier", "ki_barrier_pct", initial)
    ko_rate = _num(terms.get("ko_rate"))
    lockup = terms.get("lockup_months")
    start = _start_date(terms)
    annualized = bool(terms.get("ko_rate_annualized", False))
    ki_convention = str(terms.get("ki_convention", "DAILY")).upper()
    # observation_frequency is the canonical key (see build-contract.md);
    # ko_frequency is an accepted legacy synonym kept for existing callers/fixtures.
    frequency = str(
        terms.get("observation_frequency") or terms.get("ko_frequency") or ""
    ).upper()
    custom_dates_raw = terms.get("ko_observation_dates")

    if ko_barrier is None:
        out.missing.append("barrier_config.ko_barrier")
    if ko_rate is None:
        out.missing.append("barrier_config.ko_rate")
    if lockup is None:
        out.missing.append("barrier_config.lockup_months")
    if start is None:
        out.missing.append("trade_start_date")
    if ki_convention not in {"DAILY", "EUROPEAN", "NONE"}:
        out.warnings.append(f"unknown ki_convention {ki_convention}; defaulting DAILY")
        ki_convention = "DAILY"
    if ki_convention != "NONE" and ki_barrier is None:
        out.missing.append("barrier_config.ki_barrier")
    if frequency not in {"MONTHLY", "QUARTERLY", "SEMI_ANNUAL", "CUSTOM"}:
        out.missing.append("observation_frequency")
    elif frequency == "CUSTOM" and not isinstance(custom_dates_raw, list):
        out.missing.append("ko_observation_dates")

    # Cannot synthesize schedules without start + lockup + maturity.
    if out.missing:
        bc: dict[str, Any] = {"ko_observation_type": "DISCRETE"}
        if ko_barrier is not None:
            bc["ko_barrier"] = ko_barrier
        if ko_rate is not None:
            bc["ko_rate"] = ko_rate
        if ki_barrier is not None and ki_convention != "NONE":
            bc["ki_barrier"] = ki_barrier
        out.product_kwargs["barrier_config"] = bc
        return out

    # Guaranteed non-None: each was appended to `missing` above and returned early.
    assert start is not None and maturity is not None and lockup is not None
    exercise = schedules.add_months(start, round(maturity * 12))
    if frequency == "CUSTOM":
        assert isinstance(custom_dates_raw, list)  # narrowed by the missing-guard above
        try:
            # Sorted so the STOP_FIRST_HIT KO schedule stays chronological even
            # if the caller supplies dates out of order. Bad dates are reported
            # via `missing`, never raised — the builder never crashes on input.
            ko_dates = sorted(date.fromisoformat(str(d)) for d in custom_dates_raw)
        except ValueError as exc:
            out.missing.append(f"ko_observation_dates (invalid ISO date: {exc})")
            return out
    else:
        ko_dates = schedules.periodic_observation_dates(
            start=start,
            maturity_years=maturity,
            lockup_months=int(lockup),
            months_step=schedules.FREQUENCY_MONTHS[frequency],
        )
    bc = {
        "ko_barrier": ko_barrier,
        "ko_rate": ko_rate,
        "ko_observation_type": "DISCRETE",
        "ko_observation_schedule": schedules.build_ko_schedule(
            dates=ko_dates,
            barriers=[ko_barrier] * len(ko_dates),
            rates=[ko_rate] * len(ko_dates),
            annualized=annualized,
            frequency=frequency,
        ),
    }
    if ki_convention != "NONE":
        bc["ki_barrier"] = ki_barrier
        bc["ki_observation_type"] = "DISCRETE"
        bc["ki_continuous"] = False
        if ki_convention == "DAILY":
            ki_dates = schedules.daily_ki_dates(start=start, exercise=exercise)
            bc["ki_observation_schedule"] = schedules.build_ki_schedule(
                dates=ki_dates, barrier=ki_barrier, frequency="DAILY"
            )
        else:  # EUROPEAN
            bc["ki_observation_schedule"] = schedules.build_ki_schedule(
                dates=[exercise], barrier=ki_barrier, frequency="CUSTOM"
            )
    out.product_kwargs["barrier_config"] = bc
    out.product_kwargs["accrual_config"] = {
        "coupon_pay_type": "INSTANT",
        "is_annualized": annualized,
        "is_annualized_ko": annualized,
    }
    # Annualized KO accruals need explicit accrual endpoints to size each
    # coupon's year-fraction; the QuantArk validator rejects the build without
    # them. Honor caller-supplied dates (settlement may lag exercise), else
    # derive: initial_date = trade start, settlement_date = exercise maturity.
    out.product_kwargs["initial_date"] = (
        str(terms.get("initial_date")) if terms.get("initial_date") else start.isoformat()
    )
    out.product_kwargs["settlement_date"] = (
        str(terms.get("settlement_date"))
        if terms.get("settlement_date")
        else exercise.isoformat()
    )
    return out


def _build_ko_reset_snowball(terms: dict, *, quantark_class: str) -> _Out:
    """Pre-KI schedule = a normal snowball; post-KI = a KO-only reset schedule."""
    out = _build_snowball(terms, quantark_class=quantark_class)
    # _build_snowball already required + recorded initial_price; reuse it.
    initial = _num(out.product_kwargs.get("initial_price")) or 100.0
    post_ko_barrier = _abs_barrier(terms, "post_ko_barrier", "post_ko_barrier_pct", initial)
    post_ko_rate = _num(terms.get("post_ko_rate"))
    if post_ko_barrier is None:
        out.missing.append("post_barrier_config.ko_barrier")
    if post_ko_rate is None:
        out.missing.append("post_barrier_config.ko_rate")
    pre_bc = out.product_kwargs.get("barrier_config")
    if not out.missing and isinstance(pre_bc, dict) and "ko_observation_schedule" in pre_bc:
        pre_schedule = pre_bc["ko_observation_schedule"]
        post_dates = [
            date.fromisoformat(record["observation_date"])
            for record in pre_schedule["records"]
        ]
        # Post-KI reuses the pre-KI dates, so it must reuse the pre-KI frequency
        # tag too — otherwise a non-monthly KO-reset emits a mislabeled schedule.
        post_frequency = pre_schedule.get("frequency", "MONTHLY")
        out.product_kwargs["post_barrier_config"] = {
            "ko_barrier": post_ko_barrier,
            "ko_rate": post_ko_rate,
            "ko_observation_type": "DISCRETE",
            "ko_observation_schedule": schedules.build_ko_schedule(
                dates=post_dates,
                barriers=[post_ko_barrier] * len(post_dates),
                rates=[post_ko_rate] * len(post_dates),
                annualized=bool(terms.get("ko_rate_annualized", False)),
                frequency=post_frequency,
            ),
        }
    return out


def _build_phoenix(terms: dict, *, quantark_class: str) -> _Out:
    out = _build_snowball(terms, quantark_class=quantark_class)
    # _build_snowball already required + recorded initial_price; reuse it.
    initial = _num(out.product_kwargs.get("initial_price")) or 100.0
    coupon_barrier = _abs_barrier(terms, "coupon_barrier", "coupon_barrier_pct", initial)
    coupon_rate = _num(terms.get("coupon_rate"))
    if coupon_barrier is None:
        out.missing.append("coupon_config.coupon_barrier")
    if coupon_rate is None:
        out.missing.append("coupon_config.coupon_rate")
    if coupon_barrier is not None and coupon_rate is not None:
        out.product_kwargs["coupon_config"] = {
            "coupon_barrier": coupon_barrier,
            "coupon_rate": coupon_rate,
            "coupon_pay_type": "INSTANT",
            "memory_coupon": bool(terms.get("memory_coupon", False)),
        }
    # Phoenix KO leg typically pays 0 coupon at KO (coupon comes from coupon_config).
    bc = out.product_kwargs.get("barrier_config")
    if isinstance(bc, dict) and "ko_rate" in bc and terms.get("ko_rate") is None:
        bc["ko_rate"] = 0.0
        if "barrier_config.ko_rate" in out.missing:
            out.missing.remove("barrier_config.ko_rate")
    return out


def _build_vanilla(terms: dict, *, quantark_class: str) -> _Out:
    out = _Out()
    _initial_price(terms, out)  # required as the position's S0 / validation spot
    pk = _common_option(terms, out)
    strike = _num(_require(terms, out, "strike"))
    if strike is not None:
        pk["strike"] = strike
    pk["option_type"] = str(terms.get("option_type", "CALL")).upper()
    out.product_kwargs = pk
    return out


def _build_digital(terms: dict, *, quantark_class: str) -> _Out:
    out = _build_vanilla(terms, quantark_class=quantark_class)
    cash = _num(_require(terms, out, "cash_payoff"))
    if cash is not None:
        out.product_kwargs["payout"] = cash  # quant-ark field name
    return out


def _build_barrier(terms: dict, *, quantark_class: str) -> _Out:
    out = _build_vanilla(terms, quantark_class=quantark_class)
    barrier = _num(_require(terms, out, "barrier"))
    if barrier is not None:
        out.product_kwargs["barrier"] = barrier
    out.product_kwargs["barrier_type"] = str(terms.get("barrier_type", "DOWN_OUT")).upper()
    out.product_kwargs["rebate"] = _num(terms.get("rebate")) or 0.0
    return out


def _build_one_touch(terms: dict, *, quantark_class: str) -> _Out:
    # OneTouch has no contract_multiplier/strike/option_type; payoff is `rebate`,
    # direction is `barrier_direction` (UP/DOWN), `touch_type` is ONE/NO_TOUCH.
    out = _Out()
    _initial_price(terms, out)  # required as the position's S0 / validation spot
    pk: dict[str, Any] = {}
    m = _num(terms.get("maturity_years"))
    if m is None:
        out.missing.append("maturity_years")
    else:
        pk["maturity"] = m
    barrier = _num(_require(terms, out, "barrier"))
    cash = _num(_require(terms, out, "cash_payoff"))
    if barrier is not None:
        pk["barrier"] = barrier
    if cash is not None:
        pk["rebate"] = cash
    pk["barrier_direction"] = str(terms.get("barrier_direction", "UP")).upper()
    pk["touch_type"] = str(terms.get("touch_type", "ONE_TOUCH")).upper()
    out.product_kwargs = pk
    return out


def _build_double_one_touch(terms: dict, *, quantark_class: str) -> _Out:
    # DoubleOneTouchOption straddles two barriers; payoff is `rebate`, and
    # `touch_type` (DOUBLE_ONE_TOUCH / DOUBLE_NO_TOUCH) selects touch-in vs
    # touch-out. Mirrors OneTouch: no strike/option_type/contract_multiplier,
    # and initial_price is the validation spot only (not a product kwarg).
    out = _Out()
    _initial_price(terms, out)  # required as the position's S0 / validation spot
    pk: dict[str, Any] = {}
    m = _num(terms.get("maturity_years"))
    if m is None:
        out.missing.append("maturity_years")
    else:
        pk["maturity"] = m
    upper = _num(_require(terms, out, "upper_barrier"))
    lower = _num(_require(terms, out, "lower_barrier"))
    cash = _num(_require(terms, out, "cash_payoff"))
    if upper is not None:
        pk["upper_barrier"] = upper
    if lower is not None:
        pk["lower_barrier"] = lower
    if cash is not None:
        pk["rebate"] = cash
    pk["touch_type"] = str(terms.get("touch_type", "DOUBLE_ONE_TOUCH")).upper()
    out.product_kwargs = pk
    return out


def _carry_otc_metadata(terms: dict, pk: dict, keys: tuple[str, ...]) -> None:
    """Carry DeltaOne persistence-only fields as ``_otc_``-prefixed kwargs.

    These fields (spot: instrument_code/exchange/contract_multiplier; futures:
    contract_code) are read by the equity_spot/futures_products side-tables but
    are NOT part of the QuantArk constructor surface (SpotInstrument takes only
    underlying/deltaone_type; Futures rejects contract_code). Prefixing with
    ``_otc_`` lets them survive in stored terms while ``_build_termsheet`` pops
    them before QuantArk construction at validate/price time — the same pattern
    the OTC import adapter uses for autocallable lifecycle attrs."""
    for key in keys:
        value = terms.get(key)
        if value is not None:
            pk[f"_otc_{key}"] = value


def _build_futures(terms: dict, *, quantark_class: str) -> _Out:
    out = _Out()
    _initial_price(terms, out)  # required as the position's S0 / validation spot
    pk: dict[str, Any] = {
        "multiplier": _num(terms.get("contract_multiplier"))
        or _num(terms.get("multiplier"))
        or 1.0
    }
    underlying = _require(terms, out, "underlying")
    if underlying is not None:
        pk["underlying"] = str(underlying)
    m = _num(terms.get("maturity_years")) or _num(terms.get("maturity"))
    if m is not None:
        pk["maturity"] = m
    # Optional Futures constructor kwargs (also persisted) — pass through when given.
    for key in ("basis", "basis_decay_rate", "market_price"):
        value = _num(terms.get(key))
        if value is not None:
            pk[key] = value
    _carry_otc_metadata(terms, pk, ("contract_code",))
    out.product_kwargs = pk
    return out


def _build_spot(terms: dict, *, quantark_class: str) -> _Out:
    out = _Out()
    _initial_price(terms, out)  # required as the position's S0 / validation spot
    pk: dict[str, Any] = {"deltaone_type": str(terms.get("deltaone_type", "INDEX")).upper()}
    underlying = _require(terms, out, "underlying")
    if underlying is not None:
        pk["underlying"] = str(underlying)
    _carry_otc_metadata(terms, pk, ("instrument_code", "exchange", "contract_multiplier"))
    out.product_kwargs = pk
    return out


def _build_asian(terms: dict, *, quantark_class: str) -> _Out:
    # AsianOption averages over `num_observations` evenly spaced points; the
    # count is derived from maturity + frequency (no explicit dates needed).
    out = _build_vanilla(terms, quantark_class=quantark_class)
    maturity = _num(terms.get("maturity_years"))
    freq = str(terms.get("averaging_frequency", "MONTHLY")).upper()
    if maturity is not None:
        periods = round(maturity * 252) if freq == "DAILY" else round(maturity * 12)
        out.product_kwargs["num_observations"] = max(1, periods)
    out.product_kwargs["initial_price"] = (
        _num(terms.get("initial_price")) or out.product_kwargs.get("strike") or 100.0
    )
    return out


def _build_single_sharkfin(terms: dict, *, quantark_class: str) -> _Out:
    out = _build_vanilla(terms, quantark_class=quantark_class)
    barrier = _num(_require(terms, out, "barrier"))
    if barrier is not None:
        out.product_kwargs["barrier"] = barrier
    out.product_kwargs["participation_rate"] = _num(terms.get("participation_rate")) or 1.0
    return out


def _build_double_sharkfin(terms: dict, *, quantark_class: str) -> _Out:
    out = _Out()
    _initial_price(terms, out)  # required as the position's S0 / validation spot
    pk = _common_option(terms, out)
    pk["option_type"] = str(terms.get("option_type", "CALL")).upper()
    strike = _num(_require(terms, out, "strike"))
    if strike is not None:
        pk["strike"] = strike
    lower = _num(_require(terms, out, "lower_barrier"))
    upper = _num(_require(terms, out, "upper_barrier"))
    if lower is not None:
        pk["lower_barrier"] = lower
    if upper is not None:
        pk["upper_barrier"] = upper
    pk["participation_rate"] = _num(terms.get("participation_rate")) or 1.0
    out.product_kwargs = pk
    return out


def _build_range_accrual(terms: dict, *, quantark_class: str) -> _Out:
    out = _Out()
    initial = _initial_price(terms, out)
    maturity = _num(terms.get("maturity_years"))
    pk: dict[str, Any] = {
        "initial_price": initial,
        "contract_multiplier": _num(terms.get("contract_multiplier")) or 1.0,
    }
    if maturity is None:
        out.missing.append("maturity_years")
    else:
        pk["maturity"] = maturity
        freq = str(terms.get("observation_frequency", "DAILY")).upper()
        periods = round(maturity * 252) if freq == "DAILY" else round(maturity * 12)
        pk["num_observations"] = max(1, periods)
    lower = _abs_barrier(terms, "lower_barrier", "lower_barrier_pct", initial)
    upper = _abs_barrier(terms, "upper_barrier", "upper_barrier_pct", initial)
    accrual_rate = _num(terms.get("accrual_rate"))
    if lower is None:
        out.missing.append("range_config.lower_barrier")
    if upper is None:
        out.missing.append("range_config.upper_barrier")
    if accrual_rate is None:
        out.missing.append("range_config.accrual_rate")
    if lower is not None and upper is not None and accrual_rate is not None:
        pk["range_config"] = {
            "lower_barrier": lower,
            "upper_barrier": upper,
            "accrual_rate": accrual_rate,
            "is_rate_annualized": bool(terms.get("accrual_rate_annualized", False)),
        }
    out.product_kwargs = pk
    return out


_REGISTRY: dict[str, Callable[..., _Out]] = {
    "SnowballOption": _build_snowball,
    "KnockOutResetSnowballOption": _build_ko_reset_snowball,
    "PhoenixOption": _build_phoenix,
    "EuropeanVanillaOption": _build_vanilla,
    "AmericanOption": _build_vanilla,
    "CashOrNothingDigitalOption": _build_digital,
    "BarrierOption": _build_barrier,
    "OneTouchOption": _build_one_touch,
    "DoubleOneTouchOption": _build_double_one_touch,
    "Futures": _build_futures,
    "SpotInstrument": _build_spot,
    "AsianOption": _build_asian,
    "SingleSharkfinOption": _build_single_sharkfin,
    "DoubleSharkfinOption": _build_double_sharkfin,
    "RangeAccrualOption": _build_range_accrual,
}


# Booking re-validates already-built kwargs for these classes; tidy them in
# place instead of re-running raw-term synthesis (which reads ko_barrier_pct,
# lockup_months, trade_start_date — absent once a product is built).
_PREBUILT_TIDY_CLASSES = {"SnowballOption", "KnockOutResetSnowballOption"}

_MALFORMED_PREBUILT_ERROR = (
    "malformed Snowball terms: barrier_config present without a synthesized "
    "ko_observation_schedule; supply flat economic terms (ko_barrier_pct, "
    "lockup_months, trade_start_date, observation_frequency, …) instead"
)


def _looks_prebuilt(terms: dict[str, Any]) -> bool:
    """A terms dict is 'already built' only if it carries a non-empty KO
    observation schedule — the evidence that synthesis already ran. A nested
    barrier_config with levels but no schedule is neither the flat contract nor a
    complete product, and must not be silently tidied (decision 8)."""
    barrier_config = terms.get("barrier_config")
    if not isinstance(barrier_config, dict):
        return False
    schedule = barrier_config.get("ko_observation_schedule")
    if not isinstance(schedule, dict):
        return False
    records = schedule.get("records")
    # records must be a non-empty list (not merely truthy) — a degenerate
    # truthy non-list would otherwise reach the opaque quad error this guard
    # exists to prevent. Mirrors _first_schedule_rate / _tidy_built_snowball.
    return isinstance(records, list) and bool(records)


def _has_flat_snowball_terms(terms: dict[str, Any]) -> bool:
    return any(
        key in terms
        for key in (
            "maturity_years",
            "trade_start_date",
            "observation_frequency",
            "ko_barrier",
            "ko_barrier_pct",
            "ki_barrier",
            "ki_barrier_pct",
            "ko_rate",
            "lockup_months",
        )
    )


def _first_schedule_rate(schedule: Any) -> float | None:
    records = schedule.get("records") if isinstance(schedule, dict) else None
    if not isinstance(records, list):
        return None
    for record in records:
        if not isinstance(record, dict):
            continue
        rate = _num(
            record.get("ko_rate")
            or record.get("return_rate")
            or record.get("coupon_rate")
            or record.get("rate")
        )
        if rate is not None:
            return rate
    return None


def _tidy_built_snowball(terms: dict[str, Any]) -> dict[str, Any]:
    """Tidy already-built Snowball kwargs (RFQ/legacy/agent output): promote the
    KO coupon into ``barrier_config.ko_rate``, drop empty observation schedules,
    and drop the unsupported ``accrual_config.coupon_rate``. Never re-synthesizes
    — schedules already present are preserved verbatim.
    """
    normalized = dict(terms)
    barrier_config = dict(normalized.get("barrier_config") or {})
    accrual_config = dict(normalized.get("accrual_config") or {})

    ko_rate = _num(barrier_config.get("ko_rate"))
    unsupported_coupon = accrual_config.pop("coupon_rate", None)
    if ko_rate is None:
        ko_rate = _num(unsupported_coupon)
    if ko_rate is None:
        ko_rate = _first_schedule_rate(barrier_config.get("ko_observation_schedule"))
    if ko_rate is not None and "ko_rate" not in barrier_config:
        barrier_config["ko_rate"] = ko_rate

    for schedule_key in ("ko_observation_schedule", "ki_observation_schedule"):
        schedule = barrier_config.get(schedule_key)
        if (
            isinstance(schedule, dict)
            and isinstance(schedule.get("records"), list)
            and not schedule["records"]
        ):
            barrier_config.pop(schedule_key, None)

    if barrier_config:
        normalized["barrier_config"] = barrier_config
    if accrual_config:
        normalized["accrual_config"] = accrual_config
    else:
        normalized.pop("accrual_config", None)
    return normalized


def build_product(
    family: str,
    terms: dict[str, Any],
    *,
    market: PricingEnvironmentSnapshot | None = None,
    underlying: str | None = None,
    currency: str | None = None,
    components: list[dict[str, Any]] | None = None,
    asset_class: str = "equity",
    display_name: str | None = None,
    solve_target: str | None = None,
    prebuilt: bool = False,
) -> BuildResult:
    engine_name = _ENGINE_BY_CLASS.get(family, "BlackScholesEngine")
    builder = _REGISTRY.get(family)
    if builder is None:
        return BuildResult(
            ok=False, quantark_class=family, engine_name=engine_name,
            missing=[], warnings=[f"unsupported_family: {family}"],
            validation=None, product_spec=None,
        )
    snowball_nested_barrier = family in _PREBUILT_TIDY_CLASSES and isinstance(terms.get("barrier_config"), dict)
    snowball_built = snowball_nested_barrier and _looks_prebuilt(terms)
    if snowball_nested_barrier and not snowball_built and not (prebuilt or _has_flat_snowball_terms(terms)):
        # Nested barrier_config with no synthesized schedule: neither the flat
        # contract nor a complete product. Reject as malformed rather than
        # tidying it into the opaque quad "KO observation … required" error.
        return BuildResult(
            ok=False, quantark_class=family, engine_name=engine_name,
            missing=[], warnings=[],
            validation={"ok": False, "error": _MALFORMED_PREBUILT_ERROR},
            product_spec=None,
        )
    if prebuilt or snowball_built:
        if snowball_nested_barrier:
            if not snowball_built:
                return BuildResult(
                    ok=False, quantark_class=family, engine_name=engine_name,
                    missing=[], warnings=[],
                    validation={"ok": False, "error": _MALFORMED_PREBUILT_ERROR},
                    product_spec=None,
                )
            # Already-built snowball kwargs: tidy in place, skip raw-term synthesis.
            product_kwargs = _tidy_built_snowball(terms)
        else:
            # prebuilt=True for a non-snowball family: the caller (e.g. the OTC import
            # adapter) asserts `terms` is a complete QuantArk termsheet. Validate-and-
            # wrap verbatim — never re-synthesize (it would drop explicit dates and
            # per-date schedules the workbook supplied). `solve_target` does not apply.
            representation_error = _mixed_maturity_representation_error(terms)
            if representation_error:
                return BuildResult(
                    ok=False, quantark_class=family, engine_name=engine_name,
                    missing=[], warnings=[],
                    validation={"ok": False, "error": representation_error},
                    product_spec=None,
                )
            product_kwargs = dict(terms)
        warnings: list[str] = []
    else:
        out = builder(terms, quantark_class=family)
        missing = product_contracts.filter_solved(out.missing, solve_target=solve_target)
        if missing:
            return BuildResult(
                ok=False, quantark_class=family, engine_name=engine_name,
                missing=missing, warnings=out.warnings, validation=None,
                product_spec=None,
            )
        product_kwargs = out.product_kwargs
        warnings = out.warnings
    market = market or _default_market(terms)
    res = validate_quantark_build(family, dict(product_kwargs), market, engine_name)
    spec = (
        ProductSpec(
            asset_class=asset_class,
            product_family=product_family_for_quantark_class(family, components=components),
            quantark_class=family,
            underlying=str(underlying or terms.get("underlying") or "UNKNOWN"),
            # CNY is an intentional CN-desk fallback, mirroring _default_market;
            # callers that trade other currencies must pass `currency` explicitly.
            currency=str(currency or terms.get("currency") or "CNY"),
            terms=product_kwargs,
            components=list(components or []),
            display_name=display_name,
        )
        if res.ok
        else None
    )
    return BuildResult(
        ok=bool(res.ok), quantark_class=family, engine_name=engine_name,
        missing=[], warnings=warnings,
        validation={"ok": res.ok, "error": res.error},
        product_spec=spec,
    )


def _default_market(terms: dict[str, Any]) -> PricingEnvironmentSnapshot:
    defaults = PricingEnvironmentSnapshot()
    spot = _num(terms.get("initial_price")) or _num(terms.get("strike")) or defaults.spot
    return PricingEnvironmentSnapshot(
        spot=spot,
        volatility=defaults.volatility,
        rate=defaults.rate,
        dividend_yield=defaults.dividend_yield,
        asset_name=str(terms.get("underlying") or "UNKNOWN"),
        currency=str(terms.get("currency") or "CNY"),
    )

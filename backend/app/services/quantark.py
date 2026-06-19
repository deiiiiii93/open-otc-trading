from __future__ import annotations

import importlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from math import isfinite
from threading import Lock
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel

from ..config import get_settings
from ..models import Portfolio, Position
from ..schemas import PricingEnvironmentSnapshot, RFQRequestDraft
from .import_schema import is_terminal_status, read_notional_unit, read_trade_status
from .domains.products import compatibility_terms_for_position

MAX_MODEL_VALUE_MULTIPLE = 10.0
ABS_MODEL_VALUE_LIMIT = 1_000_000_000_000.0
GREEK_KEYS = ("delta", "gamma", "vega", "theta", "rho", "rho_q")
CASH_GREEK_KEYS = ("delta_cash", "gamma_cash")
RISK_GREEK_KEYS = GREEK_KEYS + CASH_GREEK_KEYS
_SHARKFIN_REGISTRY_PATCHED = False
_SHARKFIN_REGISTRY_LOCK = Lock()
_DATE_KEYS = {
    "valuation_date",
    "initial_date",
    "exercise_date",
    "settlement_date",
    "maturity_date",
    "observation_date",
    "knock_in_date",
    "knock_out_date",
}


@dataclass
class QuantArkResult:
    ok: bool
    data: dict[str, Any]
    error: str | None = None


def ensure_quantark_path(*_args: Any, **_kwargs: Any) -> None:
    """Load QuantArk's legacy import aliases from the installed package."""
    try:
        importlib.import_module("quantark._compat")
    except Exception:
        pass


def _ensure_sharkfin_registry_support() -> None:
    global _SHARKFIN_REGISTRY_PATCHED
    if _SHARKFIN_REGISTRY_PATCHED:
        return
    with _SHARKFIN_REGISTRY_LOCK:
        if _SHARKFIN_REGISTRY_PATCHED:
            return

        ensure_quantark_path()
        try:
            from quantark.asset.equity.engine.analytical import (
                DoubleSharkfinOptionAnalyticalEngine,
                SingleSharkfinOptionAnalyticalEngine,
            )
            from quantark.asset.equity.engine.mc import (
                DoubleSharkfinOptionMCEngine,
                SingleSharkfinOptionMCEngine,
            )
            from quantark.asset.equity.product.option import (
                AmericanOption,
                AsianOption,
                BarrierOption,
                DoubleBarrierOption,
                DoubleOneTouchOption,
                DoubleSharkfinOption,
                EuropeanVanillaOption,
                OneTouchOption,
                SingleSharkfinOption,
            )
            from quantark.rfq.registry import (
                ENGINE_BUILDERS,
                PRODUCT_BUILDERS,
                QuoteableFieldAdapter,
                register_unknown_adapter,
            )

            PRODUCT_BUILDERS.register(SingleSharkfinOption)
            PRODUCT_BUILDERS.register(DoubleSharkfinOption)
            ENGINE_BUILDERS.register(
                SingleSharkfinOptionAnalyticalEngine,
                default_params_type="engine_params",
            )
            ENGINE_BUILDERS.register(
                DoubleSharkfinOptionAnalyticalEngine,
                default_params_type="engine_params",
            )
            ENGINE_BUILDERS.register(
                SingleSharkfinOptionMCEngine,
                default_params_type="mc_params",
            )
            ENGINE_BUILDERS.register(
                DoubleSharkfinOptionMCEngine,
                default_params_type="mc_params",
            )
            register_unknown_adapter(
                QuoteableFieldAdapter(
                    "strike",
                    supported_types=(
                        EuropeanVanillaOption,
                        AmericanOption,
                        AsianOption,
                        BarrierOption,
                        DoubleBarrierOption,
                        SingleSharkfinOption,
                        DoubleSharkfinOption,
                    ),
                )
            )
            register_unknown_adapter(
                QuoteableFieldAdapter(
                    "barrier",
                    supported_types=(BarrierOption, OneTouchOption, SingleSharkfinOption),
                )
            )
            register_unknown_adapter(
                QuoteableFieldAdapter(
                    "upper_barrier",
                    supported_types=(
                        DoubleBarrierOption,
                        DoubleOneTouchOption,
                        DoubleSharkfinOption,
                    ),
                )
            )
            register_unknown_adapter(
                QuoteableFieldAdapter(
                    "lower_barrier",
                    supported_types=(
                        DoubleBarrierOption,
                        DoubleOneTouchOption,
                        DoubleSharkfinOption,
                    ),
                )
            )
            register_unknown_adapter(
                QuoteableFieldAdapter(
                    "participation_rate",
                    supported_types=(SingleSharkfinOption, DoubleSharkfinOption),
                )
            )
        except Exception:
            return
        _SHARKFIN_REGISTRY_PATCHED = True


def _enum_value(enum_cls: Any, value: Any) -> Any:
    if not isinstance(value, str):
        return value
    key = value.upper().replace(" ", "_").replace("-", "_")
    try:
        return enum_cls[key]
    except Exception:
        return value


def _parse_datetime(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    for parser in (
        datetime.fromisoformat,
        lambda item: datetime.strptime(item, "%Y/%m/%d"),
        lambda item: datetime.strptime(item, "%Y-%m-%d"),
    ):
        try:
            return parser(text)
        except Exception:
            continue
    return value


@lru_cache(maxsize=1)
def _quantark_observation_types() -> tuple[Any, Any] | None:
    ensure_quantark_path()
    try:
        from quantark.asset.equity.product.option import ObservationRecord, ObservationSchedule

        return ObservationRecord, ObservationSchedule
    except Exception:
        return None


def _build_observation_schedule(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    observation_types = _quantark_observation_types()
    if observation_types is None:
        return value
    ObservationRecord, ObservationSchedule = observation_types
    records = []
    for record in value.get("records", []):
        normalized = normalize_quantark_kwargs(record)
        records.append(ObservationRecord(**normalized))
    kwargs = {
        key: normalize_quantark_kwargs({key: item})[key]
        for key, item in value.items()
        if key != "records"
    }
    kwargs["records"] = records
    return ObservationSchedule(**kwargs)


@lru_cache(maxsize=1)
def _quantark_calendar_helpers() -> tuple[Any, Any, Any, Any] | None:
    ensure_quantark_path()
    try:
        from quantark.util.calendar import (
            CalendarType,
            DayCountConvention,
            calculate_year_fraction,
            create_calendar,
        )

        return CalendarType, DayCountConvention, calculate_year_fraction, create_calendar
    except Exception:
        return None


@lru_cache(maxsize=1)
def _china_sse_calendar() -> Any:
    helpers = _quantark_calendar_helpers()
    if helpers is None:
        return None
    CalendarType, _DayCountConvention, _calculate_year_fraction, create_calendar = helpers
    try:
        return create_calendar(CalendarType.CHINA_SSE, year_range=(2020, 2035))
    except Exception:
        return None


def _observation_context(market: PricingEnvironmentSnapshot) -> SimpleNamespace:
    helpers = _quantark_calendar_helpers()
    day_count_convention = _day_count_convention(market)
    return SimpleNamespace(
        market=market,
        day_count_convention=day_count_convention,
        calendar=_calendar(market),
        calculate_year_fraction=helpers[2] if helpers is not None else None,
    )


def _drop_past_observations(value: Any, valuation_date: datetime) -> Any:
    if isinstance(value, dict):
        value = _trim_autocallable_schedule_arrays(value, valuation_date)
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key.endswith("_observation_schedule") or key == "observation_schedule":
                out[key] = _drop_past_schedule_records(item, valuation_date)
            else:
                out[key] = _drop_past_observations(item, valuation_date)
        return out
    if isinstance(value, list):
        return [_drop_past_observations(item, valuation_date) for item in value]
    return value


def _add_observation_times(
    value: Any,
    market: PricingEnvironmentSnapshot,
    context: SimpleNamespace | None = None,
) -> Any:
    context = context or _observation_context(market)
    if isinstance(value, dict):
        if "records" in value and isinstance(value.get("records"), list):
            records = _filter_business_day_records(value["records"], value, context)
            return {
                **value,
                "records": [
                    _add_record_observation_time(record, context) for record in records
                ],
            }
        return {
            key: _add_observation_times(item, market, context)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_add_observation_times(item, market, context) for item in value]
    return value


def _filter_business_day_records(
    records: list[Any], schedule: dict[str, Any], context: SimpleNamespace
) -> list[Any]:
    market = context.market
    if (market.day_count_convention or "").upper() != "BUSINESS_DAYS" or schedule.get(
        "frequency"
    ) != "DAILY":
        return records
    calendar = context.calendar
    if calendar is None:
        return records
    filtered = []
    for record in records:
        if not isinstance(record, dict):
            filtered.append(record)
            continue
        observation_date = _parse_datetime(record.get("observation_date"))
        if not isinstance(observation_date, datetime) or calendar.is_business_day(
            observation_date
        ):
            filtered.append(record)
    return filtered


def _add_record_observation_time(
    record: Any, context: SimpleNamespace
) -> Any:
    if not isinstance(record, dict) or record.get("observation_time") is not None:
        return record
    observation_date = _parse_datetime(record.get("observation_date"))
    if not isinstance(observation_date, datetime):
        return record
    return {
        **record,
        "observation_time": _observation_time(context, observation_date),
    }


def _observation_time(
    context: SimpleNamespace, observation_date: datetime
) -> float:
    market = context.market
    try:
        if context.calculate_year_fraction is None:
            raise ValueError("calculate_year_fraction unavailable")
        return context.calculate_year_fraction(
            market.valuation_date,
            observation_date,
            context.day_count_convention,
            market.bus_days_in_year,
            calendar=context.calendar,
        )
    except Exception:
        return (observation_date.date() - market.valuation_date.date()).days / 365.0


def _drop_past_schedule_records(value: Any, valuation_date: datetime) -> Any:
    filtered, _indices = _drop_past_schedule_records_with_indices(value, valuation_date)
    return filtered


def _drop_past_schedule_records_with_indices(
    value: Any, valuation_date: datetime
) -> tuple[Any, list[int] | None]:
    if not isinstance(value, dict):
        return value, None
    records = []
    kept_indices: list[int] = []
    for index, record in enumerate(value.get("records", [])):
        if not isinstance(record, dict):
            records.append(record)
            kept_indices.append(index)
            continue
        observation_date = _parse_datetime(record.get("observation_date"))
        if (
            isinstance(observation_date, datetime)
            and observation_date.date() < valuation_date.date()
        ):
            continue
        records.append(record)
        kept_indices.append(index)
    return {**value, "records": records}, kept_indices


def _trim_autocallable_schedule_arrays(
    value: dict[str, Any], valuation_date: datetime
) -> dict[str, Any]:
    barrier_config = value.get("barrier_config")
    if (
        not isinstance(barrier_config, dict)
        or "ko_observation_schedule" not in barrier_config
    ):
        return value
    filtered_schedule, kept_indices = _drop_past_schedule_records_with_indices(
        barrier_config.get("ko_observation_schedule"),
        valuation_date,
    )
    if kept_indices is None:
        return value
    trimmed = dict(value)
    trimmed_barrier = dict(barrier_config)
    trimmed_barrier["ko_observation_schedule"] = filtered_schedule
    for key in ("ko_barrier", "ko_rate"):
        trimmed_barrier[key] = _trim_list_value(trimmed_barrier.get(key), kept_indices)
    trimmed["barrier_config"] = trimmed_barrier
    coupon_config = trimmed.get("coupon_config")
    if isinstance(coupon_config, dict):
        trimmed_coupon = dict(coupon_config)
        trimmed_coupon["coupon_barrier"] = _trim_list_value(
            trimmed_coupon.get("coupon_barrier"), kept_indices
        )
        trimmed["coupon_config"] = trimmed_coupon
    accrual_config = trimmed.get("accrual_config")
    if isinstance(accrual_config, dict) and "accrual_factors" in accrual_config:
        trimmed_accrual = dict(accrual_config)
        trimmed_accrual["accrual_factors"] = _trim_list_value(
            trimmed_accrual.get("accrual_factors"), kept_indices
        )
        trimmed["accrual_config"] = trimmed_accrual
    return trimmed


def _trim_list_value(value: Any, kept_indices: list[int]) -> Any:
    if not isinstance(value, list):
        return value
    return [value[index] for index in kept_indices if index < len(value)]


@lru_cache(maxsize=1)
def _quantark_enum_by_key() -> dict[str, Any] | None:
    helpers = _quantark_calendar_helpers()
    if helpers is None:
        return None
    _CalendarType, DayCountConvention, _calculate_year_fraction, _create_calendar = helpers
    try:
        from quantark.util.enum import (
            BarrierType,
            CouponPayType,
            DeltaOneType,
            DoubleBarrierType,
            ObservationAggregation,
            ObservationFrequency,
            ObservationType,
            OptionType,
            ProtectionType,
            TenorEnd,
            TouchType,
            BarrierDirection,
        )
    except Exception:
        return None

    return {
        "option_type": OptionType,
        "barrier_type": BarrierType,
        "double_barrier_type": DoubleBarrierType,
        "observation_type": ObservationType,
        "ko_observation_type": ObservationType,
        "ki_observation_type": ObservationType,
        "touch_type": TouchType,
        "barrier_direction": BarrierDirection,
        "deltaone_type": DeltaOneType,
        "coupon_pay_type": CouponPayType,
        "protection_type": ProtectionType,
        "tenor_end": TenorEnd,
        "annualization_day_count": DayCountConvention,
        "day_count_convention": DayCountConvention,
        "aggregation_mode": ObservationAggregation,
        "frequency": ObservationFrequency,
    }


def normalize_quantark_kwargs(value: Any) -> Any:
    enum_by_key = _quantark_enum_by_key()
    if enum_by_key is None:
        return value

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in enum_by_key:
                out[key] = _enum_value(enum_by_key[key], item)
            elif key in _DATE_KEYS:
                out[key] = _parse_datetime(item)
            elif key.endswith("_observation_schedule") or key == "observation_schedule":
                out[key] = _build_observation_schedule(item)
            else:
                out[key] = normalize_quantark_kwargs(item)
        return out
    if isinstance(value, list):
        return [normalize_quantark_kwargs(item) for item in value]
    return value


def _market_kwargs(snapshot: PricingEnvironmentSnapshot) -> dict[str, Any]:
    ensure_quantark_path()
    return {
        "valuation_date": snapshot.valuation_date,
        "spot": snapshot.spot,
        "asset_name": snapshot.asset_name,
        "volatility": snapshot.volatility,
        "rate": snapshot.rate,
        "dividend_yield": snapshot.dividend_yield,
        "day_count_convention": _day_count_convention(snapshot),
        "bus_days_in_year": snapshot.bus_days_in_year,
        "calendar": _calendar(snapshot),
    }


def _day_count_convention(snapshot: PricingEnvironmentSnapshot) -> Any:
    helpers = _quantark_calendar_helpers()
    if helpers is None:
        return None
    _CalendarType, DayCountConvention, _calculate_year_fraction, _create_calendar = helpers
    convention = (snapshot.day_count_convention or "ACT_365").upper()
    try:
        return DayCountConvention[convention]
    except Exception:
        return None


def _calendar(snapshot: PricingEnvironmentSnapshot) -> Any:
    if (snapshot.day_count_convention or "").upper() != "BUSINESS_DAYS":
        return None
    return _china_sse_calendar()


def _client_response(rfq: RFQRequestDraft, quote: dict[str, Any]) -> str:
    side = "bid" if rfq.side == "sell" else "offer"
    label = quote.get("field_label") or quote.get("field_path")
    solved = quote.get("solved_value")
    price = quote.get("achieved_price")
    if solved is None:
        return (
            f"Indicative {side} for {rfq.quantity:g} x {rfq.product_type} on {rfq.underlying}: "
            f"fixed terms, model price = {float(price or 0):.6g}. "
            "This quote is pending internal trader approval and is not executable until approved."
        )
    return (
        f"Indicative {side} for {rfq.quantity:g} x {rfq.product_type} on {rfq.underlying}: "
        f"{label} = {float(solved):.6g}, model price = {float(price):.6g}. "
        "This quote is pending internal trader approval and is not executable until approved."
    )


def solve_rfq(rfq: RFQRequestDraft) -> QuantArkResult:
    ensure_quantark_path()
    _ensure_sharkfin_registry_support()
    request = None
    try:
        from quantark.rfq.service import quote_rfq

        request = _build_rfq_request(rfq)
        quote = quote_rfq(request).to_dict()
        quote["client_response"] = _client_response(rfq, quote)
        return QuantArkResult(ok=True, data=quote)
    except Exception as exc:
        error = _describe_quantark_solve_error(rfq, request, str(exc))
        return QuantArkResult(
            ok=False,
            data={
                "quote_id": f"failed-{int(datetime.utcnow().timestamp())}",
                "status": "pricing_failed",
                "field_path": rfq.unknown.field_path,
                "field_label": rfq.unknown.display_label or rfq.unknown.field_path,
                "target_label": rfq.target.label,
                "target_value": rfq.target.value,
                "engine_summary": {
                    "engine_class": rfq.engine_spec.engine_name,
                    "quantark_error": error,
                },
                "request_summary": {
                    "input_mode": "termsheet",
                    "product_type": rfq.product_type,
                    "engine_class": rfq.engine_spec.engine_name,
                    "field_path": rfq.unknown.field_path,
                    "target_label": rfq.target.label,
                },
            },
            error=error,
        )


def _build_rfq_request(rfq: RFQRequestDraft) -> Any:
    from quantark.rfq.models import (
        RFQEngineSpec,
        RFQInputMode,
        RFQRequest,
        RFQTarget,
        RFQTargetLabel,
        RFQTermsheetInput,
        RFQUnknownSpec,
    )

    termsheet = RFQTermsheetInput(
        product_type=rfq.product_type,
        product_kwargs=normalize_quantark_kwargs(rfq.product_kwargs),
        market_kwargs=_market_kwargs(rfq.market),
        engine_spec=RFQEngineSpec(**rfq.engine_spec.model_dump()),
    )
    return RFQRequest(
        input_mode=RFQInputMode.TERMSHEET,
        unknown=RFQUnknownSpec(**rfq.unknown.model_dump()),
        target=RFQTarget(
            label=RFQTargetLabel(rfq.target.label),
            value=rfq.target.value,
        ),
        termsheet_input=termsheet,
        metadata={"client_name": rfq.client_name, "underlying": rfq.underlying},
    )


def _describe_quantark_solve_error(
    rfq: RFQRequestDraft, request: Any, error: str
) -> str:
    if error != "RFQ target is not bracketed by the supplied unknown bounds":
        return error
    if request is None:
        return error
    try:
        from quantark.rfq.registry import resolve_unknown_adapter
        from quantark.rfq.service import RFQService

        service = RFQService()
        context = service._normalize_request(request)
        adapter = resolve_unknown_adapter(
            request.unknown, context.product, context.pricing_env
        )
        lower = request.unknown.lower_bound
        upper = request.unknown.upper_bound
        lower_price = service._evaluate_candidate(
            context.product, context.pricing_env, context.engine, adapter, lower
        )
        upper_price = service._evaluate_candidate(
            context.product, context.pricing_env, context.engine, adapter, upper
        )
        target_label = _enum_display_value(request.target.label)
        quote_label = request.unknown.display_label or request.unknown.field_path
        return (
            f"Target {target_label} {_format_number(request.target.value)} is not between "
            f"model prices at quote {quote_label} bounds "
            f"[{_format_number(lower)}, {_format_number(upper)}]: "
            f"{_format_number(lower)} -> {_format_number(lower_price)}, "
            f"{_format_number(upper)} -> {_format_number(upper_price)}. "
            "Widen Quote Lower/Upper Bound or adjust Target Value."
        )
    except Exception:
        return error


def _enum_display_value(value: Any) -> str:
    enum_value = getattr(value, "value", value)
    return str(enum_value).replace("_", " ")


def _format_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not isfinite(number):
        return str(value)
    return f"{number:,.6g}"


def _fallback_quote(rfq: RFQRequestDraft, error: str) -> dict[str, Any]:
    midpoint = (rfq.unknown.lower_bound + rfq.unknown.upper_bound) / 2.0
    quote = {
        "quote_id": f"fallback-{int(datetime.utcnow().timestamp())}",
        "status": "fallback",
        "field_path": rfq.unknown.field_path,
        "field_label": rfq.unknown.display_label or rfq.unknown.field_path,
        "solved_value": midpoint,
        "target_label": rfq.target.label,
        "target_value": rfq.target.value,
        "achieved_price": rfq.target.value,
        "residual": 0.0,
        "engine_summary": {"engine_class": "Fallback", "quantark_error": error},
        "request_summary": {
            "input_mode": "termsheet",
            "product_type": rfq.product_type,
            "engine_class": rfq.engine_spec.engine_name,
            "field_path": rfq.unknown.field_path,
            "target_label": rfq.target.label,
        },
    }
    quote["client_response"] = _client_response(rfq, quote)
    return quote


def _build_termsheet(
    product_type: str,
    product_kwargs: dict[str, Any],
    market: PricingEnvironmentSnapshot,
    engine_name: str,
    engine_kwargs: dict[str, Any] | None,
) -> tuple[Any, dict[str, Any]]:
    """Construct an RFQTermsheetInput and return (termsheet, otc_attrs).

    The returned otc_attrs must be applied to the built product via setattr
    because they are not part of the QuantArk product constructor surface.
    """
    ensure_quantark_path()
    _ensure_sharkfin_registry_support()
    from quantark.rfq.models import RFQEngineSpec, RFQTermsheetInput

    spec_kwargs = dict(engine_kwargs or {})
    rfq_engine_spec = RFQEngineSpec(
        engine_name=engine_name,
        params_type=spec_kwargs.pop("params_type", None),
        params_kwargs=spec_kwargs.pop("params_kwargs", {}),
        method=spec_kwargs.pop("method", None),
        engine_kwargs=spec_kwargs,
    )
    filtered_product_kwargs = _drop_past_observations(
        product_kwargs, market.valuation_date
    )
    filtered_product_kwargs = _add_observation_times(filtered_product_kwargs, market)
    normalized_product_kwargs = normalize_quantark_kwargs(filtered_product_kwargs)
    otc_attrs: dict[str, Any] = {}
    if isinstance(normalized_product_kwargs, dict):
        for key in list(normalized_product_kwargs):
            if key.startswith("_otc_"):
                otc_attrs[key] = normalized_product_kwargs.pop(key)
    termsheet = RFQTermsheetInput(
        product_type=product_type,
        product_kwargs=normalized_product_kwargs,
        market_kwargs=_market_kwargs(market),
        engine_spec=rfq_engine_spec,
    )
    return termsheet, otc_attrs


def _build_termsheet_for_position(
    position: Position,
    market: PricingEnvironmentSnapshot | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Return (termsheet, otc_attrs) for a Position.

    Delegates to `_build_termsheet` after pulling fields off the Position.
    """
    snapshot = market or PricingEnvironmentSnapshot()
    compat = compatibility_terms_for_position(position)
    return _build_termsheet(
        product_type=compat["product_type"],
        product_kwargs=compat["product_kwargs"] or {},
        market=snapshot,
        engine_name=position.engine_name or "BlackScholesEngine",
        engine_kwargs=position.engine_kwargs or {},
    )


def build_product_for_position(
    position: Position,
    market: PricingEnvironmentSnapshot | None = None,
) -> Any:
    """Build a QuantArk product for a Position (with OTC attrs applied)."""
    ensure_quantark_path()
    from quantark.rfq.builders import build_product_from_termsheet

    termsheet, otc_attrs = _build_termsheet_for_position(position, market)
    product = build_product_from_termsheet(termsheet)
    for key, value in otc_attrs.items():
        setattr(product, key, value)
    return product


def build_engine_for_position(
    position: Position,
    market: PricingEnvironmentSnapshot | None = None,
) -> Any:
    """Build a QuantArk pricing engine for a Position."""
    ensure_quantark_path()
    from quantark.rfq.builders import build_engine_from_termsheet

    termsheet, _ = _build_termsheet_for_position(position, market)
    return build_engine_from_termsheet(termsheet)


def validate_quantark_build(
    product_type: str,
    product_kwargs: dict[str, Any],
    market: PricingEnvironmentSnapshot,
    engine_name: str,
    engine_kwargs: dict[str, Any] | None = None,
) -> QuantArkResult:
    """Validate that terms can build a QuantArk product, env, and engine."""
    ensure_quantark_path()
    try:
        from quantark.rfq.builders import (
            build_engine_from_termsheet,
            build_pricing_env_from_market_kwargs,
            build_product_from_termsheet,
        )

        termsheet, otc_attrs = _build_termsheet(
            product_type=product_type,
            product_kwargs=product_kwargs,
            market=market,
            engine_name=engine_name,
            engine_kwargs=engine_kwargs,
        )
        product = build_product_from_termsheet(termsheet)
        for key, value in otc_attrs.items():
            setattr(product, key, value)
        engine = build_engine_from_termsheet(termsheet)
        pricing_env = build_pricing_env_from_market_kwargs(termsheet.market_kwargs)
        valuation_date = getattr(pricing_env, "valuation_date", None)
        return QuantArkResult(
            ok=True,
            data={
                "product_type": product_type,
                "product_class": product.__class__.__name__,
                "engine": engine_name,
                "engine_class": engine.__class__.__name__,
                "valuation_date": (
                    valuation_date.isoformat()
                    if hasattr(valuation_date, "isoformat")
                    else valuation_date
                ),
            },
        )
    except Exception as exc:
        return QuantArkResult(
            ok=False,
            data={"product_type": product_type, "engine": engine_name},
            error=str(exc),
        )


def build_pricing_env(market: PricingEnvironmentSnapshot) -> Any:
    """Build a QuantArk pricing environment from a snapshot."""
    ensure_quantark_path()
    from quantark.rfq.builders import build_pricing_env_from_market_kwargs

    return build_pricing_env_from_market_kwargs(_market_kwargs(market))


def price_product(
    product_type: str,
    product_kwargs: dict[str, Any],
    market: PricingEnvironmentSnapshot,
    engine_name: str = "BlackScholesEngine",
    engine_kwargs: dict[str, Any] | None = None,
) -> QuantArkResult:
    engine_kwargs = engine_kwargs or {}
    draft = RFQRequestDraft(
        product_type=product_type,
        product_kwargs=product_kwargs,
        market=market,
        engine_spec={"engine_name": engine_name},  # type: ignore[arg-type]
        unknown={"field_path": "strike", "lower_bound": 1.0, "upper_bound": 500.0},
        target={"label": "price", "value": 0.0},
    )
    ensure_quantark_path()
    try:
        from quantark.rfq.builders import (
            build_engine_from_termsheet,
            build_pricing_env_from_market_kwargs,
            build_product_from_termsheet,
        )

        termsheet, otc_attrs = _build_termsheet(
            product_type=product_type,
            product_kwargs=product_kwargs,
            market=market,
            engine_name=engine_name,
            engine_kwargs=engine_kwargs,
        )
        product = build_product_from_termsheet(termsheet)
        for key, value in otc_attrs.items():
            setattr(product, key, value)
        pricing_env = build_pricing_env_from_market_kwargs(termsheet.market_kwargs)
        engine = build_engine_from_termsheet(termsheet)
        price = float(engine.price(product, pricing_env))
        return QuantArkResult(
            ok=True,
            data={"price": price, "engine": engine_name, "product_type": product_type},
        )
    except Exception as exc:
        return QuantArkResult(
            ok=False,
            data={"price": 0.0, "engine": engine_name, "product_type": product_type},
            error=str(exc),
        )


def price_product_with_greeks(
    product_type: str,
    product_kwargs: dict[str, Any],
    market: PricingEnvironmentSnapshot,
    engine_name: str = "BlackScholesEngine",
    engine_kwargs: dict[str, Any] | None = None,
    compute_greeks: bool = True,
) -> QuantArkResult:
    """Price one ad-hoc product spec and (optionally) its Greeks in a single
    build. Reuses the same termsheet path as :func:`price_product`. Read-only:
    persists nothing. If pricing succeeds but the Greek calculation fails, the
    price is still returned with ``greeks=None`` and ``greeks_error`` set."""
    engine_kwargs = engine_kwargs or {}
    ensure_quantark_path()
    try:
        from quantark.rfq.builders import (
            build_engine_from_termsheet,
            build_pricing_env_from_market_kwargs,
            build_product_from_termsheet,
        )

        termsheet, otc_attrs = _build_termsheet(
            product_type=product_type,
            product_kwargs=product_kwargs,
            market=market,
            engine_name=engine_name,
            engine_kwargs=engine_kwargs,
        )
        product = build_product_from_termsheet(termsheet)
        for key, value in otc_attrs.items():
            setattr(product, key, value)
        pricing_env = build_pricing_env_from_market_kwargs(termsheet.market_kwargs)
        engine = build_engine_from_termsheet(termsheet)
        price = float(engine.price(product, pricing_env))
    except Exception as exc:
        return QuantArkResult(
            ok=False,
            data={
                "price": 0.0,
                "engine": engine_name,
                "product_type": product_type,
                "greeks": None,
                "greeks_error": None,
            },
            error=str(exc),
        )

    greeks: dict[str, float] | None = None
    greeks_error: str | None = None
    if compute_greeks:
        try:
            from quantark.asset.equity.riskmeasures.greeks_calculator import (
                GreeksCalculator,
            )

            calc = GreeksCalculator()
            result = calc.calculate(product, pricing_env, engine, method="auto")
            greeks = {
                "delta": float(result.get("delta", 0.0)),
                "gamma": float(result.get("gamma", 0.0)),
                "vega": float(result.get("vega", 0.0)),
                "theta": float(result.get("theta", 0.0)),
                "rho": float(result.get("rho", 0.0)),
                "rho_q": float(result.get("dividend_rho", 0.0)),
            }
        except Exception as exc:  # price already computed; degrade gracefully
            greeks = None
            greeks_error = str(exc)

    return QuantArkResult(
        ok=True,
        data={
            "price": price,
            "engine": engine_name,
            "product_type": product_type,
            "greeks": greeks,
            "greeks_error": greeks_error,
        },
    )


def _empty_risk_totals() -> dict[str, float]:
    return {
        "market_value": 0.0,
        "delta_proxy": 0.0,
        "gross_notional": 0.0,
        "pnl": 0.0,
        "one_day_var_proxy": 0.0,
        **{key: 0.0 for key in RISK_GREEK_KEYS},
    }


def _cash_greek_contributions(
    greek_values: dict[str, float],
    market: PricingEnvironmentSnapshot,
    contract_multiplier: float = 1.0,
) -> dict[str, float]:
    """Return spot-normalized delta/gamma exposures for cross-underlying aggregation."""
    spot = float(market.spot)
    if not isfinite(spot):
        return {key: 0.0 for key in CASH_GREEK_KEYS}
    delta = float(greek_values.get("delta", 0.0))
    gamma = float(greek_values.get("gamma", 0.0))
    multiplier = float(contract_multiplier or 1.0)
    return {
        "delta_cash": delta * spot * multiplier,
        "gamma_cash": gamma * spot * spot * multiplier / 100.0,
    }


def _risk_parallel_workers(max_workers: int | None, position_count: int) -> int:
    if position_count <= 1:
        return 1
    if max_workers is None:
        max_workers = get_settings().risk_parallel_workers
    try:
        workers = int(max_workers)
    except (TypeError, ValueError):
        workers = 1
    return max(1, min(workers, position_count))


def _risk_position_snapshot(position: Any) -> SimpleNamespace:
    source_payload = getattr(position, "source_payload", None)
    compat = compatibility_terms_for_position(position)
    return SimpleNamespace(
        id=getattr(position, "id", None),
        source_trade_id=getattr(position, "source_trade_id", None),
        underlying=compat.get("underlying") or getattr(position, "underlying", ""),
        product_type=compat.get("product_type") or getattr(position, "product_type", ""),
        product_kwargs=dict(compat.get("product_kwargs") or {}),
        engine_name=getattr(position, "engine_name", None) or "BlackScholesEngine",
        engine_kwargs=dict(getattr(position, "engine_kwargs", None) or {}),
        quantity=float(getattr(position, "quantity", 0.0) or 0.0),
        entry_price=float(getattr(position, "entry_price", 0.0) or 0.0),
        status=getattr(position, "status", "open"),
        mapping_status=getattr(position, "mapping_status", "manual"),
        mapping_error=getattr(position, "mapping_error", None),
        source_payload=(
            dict(source_payload) if isinstance(source_payload, dict) else source_payload
        ),
        currency=getattr(position, "currency", None),
    )


def _calculate_position_risk(
    position: Any,
    position_market: PricingEnvironmentSnapshot,
    pricing_failure: dict[str, Any] | None = None,
    pricing_diagnostics: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, float], float]:
    from .risk_engine import compute_position_greeks

    totals = _empty_risk_totals()
    var_proxy = 0.0
    currency = getattr(position_market, "currency", None)
    multiplier = contract_multiplier_for_position(position)
    gross_notional = gross_notional_for_position(position, position_market)
    delta_proxy = float(position.quantity) * multiplier
    exclusion_reason = risk_pricing_exclusion(position)
    greek_values = {greek: 0.0 for greek in RISK_GREEK_KEYS}
    greeks_ok = False
    greeks_error = None

    if exclusion_reason:
        return (
            _risk_row(
                position,
                0.0,
                0.0,
                gross_notional,
                0.0,
                delta_proxy,
                False,
                exclusion_reason,
                greek_values,
                greeks_ok,
                greeks_error,
                pricing_diagnostics=pricing_diagnostics,
                currency=currency,
                spot=position_market.spot,
            ),
            totals,
            var_proxy,
        )

    if pricing_failure is not None:
        return (
            _risk_row(
                position,
                0.0,
                0.0,
                gross_notional,
                0.0,
                delta_proxy,
                False,
                str(pricing_failure.get("pricing_error") or "Pricing profile extraction failed"),
                greek_values,
                greeks_ok,
                greeks_error,
                pricing_diagnostics=pricing_diagnostics or pricing_failure,
                currency=currency,
                spot=position_market.spot,
            ),
            totals,
            var_proxy,
        )

    compat = compatibility_terms_for_position(position)
    product_kwargs, engine_kwargs = market_priced_position_inputs(
        compat["product_type"],
        compat["product_kwargs"],
        position_market,
        position.engine_name,
        position.engine_kwargs,
    )
    priced = price_product(
        compat["product_type"],
        product_kwargs,
        position_market,
        position.engine_name,
        engine_kwargs,
    )
    price = float(priced.data.get("price", 0.0))
    valuation_multiplier = valuation_multiplier_for_position(position)
    market_value = price * float(position.quantity) * valuation_multiplier
    pnl = (price - float(position.entry_price or 0.0)) * float(position.quantity) * valuation_multiplier
    pricing_ok = priced.ok
    pricing_error = priced.error
    if pricing_ok and not usable_model_value(market_value, gross_notional):
        pricing_ok = False
        pricing_error = (
            f"Model returned implausible market value {market_value:.6g}; "
            f"gross notional is {gross_notional:.6g}"
        )
        price = 0.0
        market_value = 0.0
        pnl = 0.0
    elif not pricing_ok:
        price = 0.0
        market_value = 0.0
        pnl = 0.0

    if pricing_ok:
        totals["market_value"] += market_value
        totals["delta_proxy"] += delta_proxy
        totals["gross_notional"] += gross_notional
        totals["pnl"] += pnl
        var_proxy += (
            abs(delta_proxy * position_market.spot)
            * position_market.volatility
            / (252.0**0.5)
        )

        greeks = compute_position_greeks(position, position_market)
        greeks_ok = bool(greeks.get("ok"))
        greeks_error = (
            None
            if greeks_ok
            else str(greeks.get("error") or "Greek calculation failed")
        )
        greek_bound = model_value_limit(gross_notional)
        if greeks_ok:
            for greek in GREEK_KEYS:
                contribution = float(greeks.get(greek, 0.0)) * float(position.quantity)
                if isfinite(contribution) and abs(contribution) <= greek_bound:
                    greek_values[greek] = contribution
                    totals[greek] += contribution
            for greek, contribution in _cash_greek_contributions(
                greek_values,
                position_market,
                multiplier,
            ).items():
                if isfinite(contribution) and abs(contribution) <= greek_bound:
                    greek_values[greek] = contribution
                    totals[greek] += contribution

    return (
        _risk_row(
            position,
            price,
            market_value,
            gross_notional,
            pnl,
            delta_proxy,
            pricing_ok,
            pricing_error,
            greek_values,
            greeks_ok,
            greeks_error,
            pricing_diagnostics=pricing_diagnostics,
            currency=currency,
            spot=position_market.spot,
        ),
        totals,
        var_proxy,
    )


def _calculate_position_risk_job(
    job: tuple[
        int,
        Any,
        PricingEnvironmentSnapshot,
        dict[str, Any] | None,
        dict[str, Any] | None,
    ],
) -> tuple[int, dict[str, Any], dict[str, float], float]:
    index, position, position_market, pricing_failure, pricing_diagnostics = job
    try:
        row, contribution, position_var_proxy = _calculate_position_risk(
            position,
            position_market,
            pricing_failure=pricing_failure,
            pricing_diagnostics=pricing_diagnostics,
        )
    except Exception as exc:
        gross_notional = gross_notional_for_position(position, position_market)
        delta_proxy = float(position.quantity) * contract_multiplier_for_position(
            position
        )
        row = _risk_row(
            position,
            0.0,
            0.0,
            gross_notional,
            0.0,
            delta_proxy,
            False,
            str(exc),
            {greek: 0.0 for greek in RISK_GREEK_KEYS},
            False,
            str(exc),
            currency=getattr(position_market, "currency", None),
            spot=getattr(position_market, "spot", None),
        )
        contribution = _empty_risk_totals()
        position_var_proxy = 0.0
    return index, row, contribution, position_var_proxy


def calculate_portfolio_risk(
    portfolio: Portfolio,
    market: PricingEnvironmentSnapshot | None = None,
    *,
    max_workers: int | None = None,
    progress_callback: Any | None = None,
    position_markets: dict[int, PricingEnvironmentSnapshot] | None = None,
    pricing_failures: dict[int, dict[str, Any]] | None = None,
    pricing_diagnostics: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    snapshot = market or PricingEnvironmentSnapshot()
    rows: list[dict[str, Any]] = []
    jobs: list[
        tuple[
            int,
            Any,
            PricingEnvironmentSnapshot,
            dict[str, Any] | None,
            dict[str, Any] | None,
        ]
    ] = []
    markets_by_id = position_markets or {}
    failures_by_id = pricing_failures or {}
    diagnostics_by_id = pricing_diagnostics or {}

    for index, position in enumerate(list(getattr(portfolio, "positions", []) or [])):
        if getattr(position, "id", None) is not None and position.id in markets_by_id:
            position_market = markets_by_id[position.id]
        else:
            position_market = market_snapshot_for_position(position, snapshot)
        position_id = getattr(position, "id", None)
        jobs.append((
            index,
            _risk_position_snapshot(position),
            position_market,
            failures_by_id.get(position_id),
            diagnostics_by_id.get(position_id),
        ))

    workers = _risk_parallel_workers(max_workers, len(jobs))
    if workers > 1:
        ensure_quantark_path()
        _ensure_sharkfin_registry_support()
        results: list[tuple[int, dict[str, Any], dict[str, float], float] | None] = [
            None
        ] * len(jobs)
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="risk-pricer"
        ) as executor:
            futures = [
                executor.submit(_calculate_position_risk_job, job) for job in jobs
            ]
            completed = 0
            for future in as_completed(futures):
                result = future.result()
                results[result[0]] = result
                completed += 1
                if progress_callback is not None:
                    progress_callback(completed, len(jobs))
    else:
        results = []
        for completed, job in enumerate(jobs, start=1):
            result = _calculate_position_risk_job(job)
            results.append(result)
            if progress_callback is not None:
                progress_callback(completed, len(jobs))

    from .risk_currency import build_currency_aware_totals

    per_position: list[tuple[str, dict[str, float]]] = []
    for result in results:
        if result is None:
            continue
        _index, row, contribution, position_var_proxy = result
        rows.append(row)
        contribution = dict(contribution)
        contribution["one_day_var_proxy"] = position_var_proxy
        per_position.append((row.get("currency") or "UNKNOWN", contribution))

    aggregated = build_currency_aware_totals(per_position)
    return {
        "by_currency": aggregated["by_currency"],
        "shared": aggregated["shared"],
        "totals": aggregated["totals"],
        "mixed_currency": aggregated["mixed_currency"],
        "currencies": aggregated["currencies"],
        "positions": rows,
    }


def market_snapshot_for_position(
    position: Position,
    fallback: PricingEnvironmentSnapshot,
    session: Any | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> PricingEnvironmentSnapshot:
    """Resolve this position's market snapshot.

    Spot chain (instrument-unification T8): observations live ONLY in the quote
    store, so spot resolves as

        latest_quote(session, position.underlying_id) when a session and an
        underlying_id are present and the quote's as_of date <= valuation_date date
        -> fallback.spot

    Rate / dividend_yield / volatility / asset_name keep the ``fallback`` values
    (the risk caller folds the assumption set / pricing row r/q/vol on top — see
    ``risk_engine._pricing_position_context``). When ``session`` is omitted the
    quote store is not consulted; only the position's currency is stamped.

    When the quote slot supplies spot and a ``diagnostics`` dict is passed, the
    fields ``market_input_source="market_quote"`` and ``quote_age_days`` are
    stamped into it so the risk-row caller can surface them.
    """
    currency = _source_currency_for_position(position) or fallback.currency
    quote_spot = _quote_spot_for_position(
        position, fallback.valuation_date, session, diagnostics
    )
    spot = quote_spot if quote_spot is not None else fallback.spot
    # Stamp the position's own currency so currency-aware risk buckets by the
    # position, not by the fallback's default currency.
    return fallback.model_copy(update={"currency": currency, "spot": spot})


def _quote_spot_for_position(
    position: Position,
    valuation_date: datetime | None,
    session: Any | None,
    diagnostics: dict[str, Any] | None,
) -> float | None:
    """Consult the quote store for this position's spot (transitional T6).

    Returns the latest recorded close price (as_of date <= valuation_date date) for the
    position's ``underlying_id``, or ``None`` when there is no session, no
    underlying_id, or no eligible quote. When a quote wins and ``diagnostics``
    is supplied, stamps ``market_input_source`` and ``quote_age_days``.
    """
    if session is None:
        return None
    underlying_id = getattr(position, "underlying_id", None)
    if underlying_id is None or valuation_date is None:
        return None
    from .quotes import latest_quote

    quote = latest_quote(session, underlying_id, as_of=valuation_date)
    if quote is None:
        return None
    if diagnostics is not None:
        diagnostics["market_input_source"] = "market_quote"
        diagnostics["quote_age_days"] = (valuation_date.date() - quote.as_of.date()).days
    return float(quote.price)


def closed_position_exclusion(position: Position) -> str | None:
    """Economically-closed check: status 'closed' or a terminal lifecycle state
    in the source payload. Shared by membership-time filtering
    (risk_engine._resolve_risk_positions) and the pricing-time defense in
    risk_pricing_exclusion below (a position can close between queue time and
    async worker execution)."""
    if str(getattr(position, "status", "open") or "open") == "closed":
        return "Closed position excluded from risk"
    source_state = _source_trade_state_for_position(position)
    if is_terminal_status(source_state):
        return f"Terminal lifecycle state excluded from risk: {source_state}"
    return None


def risk_pricing_exclusion(position: Position) -> str | None:
    mapping_status = str(getattr(position, "mapping_status", "manual") or "manual")
    if mapping_status in {"unsupported", "error"}:
        return (
            getattr(position, "mapping_error", None)
            or f"Position mapping status is {mapping_status}"
        )
    return closed_position_exclusion(position)


def contract_multiplier_for_position(position: Position) -> float:
    try:
        compat = compatibility_terms_for_position(position)
        product_kwargs = compat["product_kwargs"] or {}
        return float(
            product_kwargs.get("contract_multiplier")
            or product_kwargs.get("multiplier")
            or 1.0
        )
    except (TypeError, ValueError):
        return 1.0


def valuation_multiplier_for_position(position: Position) -> float:
    """Multiplier used to convert unit price to persisted PV.

    QuantArk futures prices are per index/commodity point, so valuation outputs
    need the native futures ``multiplier``. Other product prices already follow
    the existing valuation convention and are left unscaled here.
    """
    try:
        compat = compatibility_terms_for_position(position)
        if compat.get("product_type") != "Futures":
            return 1.0
        product_kwargs = compat["product_kwargs"] or {}
        return float(
            product_kwargs.get("multiplier")
            or product_kwargs.get("contract_multiplier")
            or 1.0
        )
    except (TypeError, ValueError):
        return 1.0


def market_priced_position_inputs(
    product_type: str,
    product_kwargs: dict[str, Any] | None,
    market: PricingEnvironmentSnapshot,
    engine_name: str,
    engine_kwargs: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Use observed futures quote for valuation paths.

    Scenario tests intentionally call the lower-level builders directly and keep
    QuantArk's theoretical futures pricing. Position pricing and batch pricing
    call through this helper so futures PV uses the resolved quote spot as the
    product's mark-to-market price.
    """
    out_product_kwargs = dict(product_kwargs or {})
    out_engine_kwargs = dict(engine_kwargs or {})
    if product_type == "Futures" and engine_name == "DeltaOneEngine":
        out_product_kwargs["market_price"] = float(market.spot)
        out_engine_kwargs["use_market_price"] = True
    return out_product_kwargs, out_engine_kwargs


def gross_notional_for_position(
    position: Position, market: PricingEnvironmentSnapshot
) -> float:
    product_kwargs = compatibility_terms_for_position(position)["product_kwargs"] or {}
    notional_base = _float_or_none(product_kwargs.get("initial_price"))
    if notional_base is None:
        notional_base = _float_or_none(product_kwargs.get("strike"))
    if notional_base is None:
        notional_base = market.spot
    return abs(
        float(position.quantity)
        * notional_base
        * contract_multiplier_for_position(position)
    )


def usable_model_value(value: float, gross_notional: float) -> bool:
    return isfinite(value) and abs(value) <= model_value_limit(gross_notional)


def model_value_limit(gross_notional: float) -> float:
    return max(ABS_MODEL_VALUE_LIMIT, abs(gross_notional) * MAX_MODEL_VALUE_MULTIPLE)


def _risk_row(
    position: Position,
    price: float,
    market_value: float,
    gross_notional: float,
    pnl: float,
    delta_proxy: float,
    pricing_ok: bool,
    pricing_error: str | None,
    greeks: dict[str, float] | None = None,
    greeks_ok: bool = False,
    greeks_error: str | None = None,
    pricing_diagnostics: dict[str, Any] | None = None,
    currency: str | None = None,
    spot: float | None = None,
) -> dict[str, Any]:
    greek_values = greeks or {greek: 0.0 for greek in RISK_GREEK_KEYS}
    row = {
        "position_id": position.id,
        "source_trade_id": getattr(position, "source_trade_id", None),
        "underlying": position.underlying,
        "product_type": position.product_type,
        "quantity": position.quantity,
        "price": price,
        "market_value": market_value,
        "gross_notional": gross_notional,
        "pnl": pnl,
        "delta_proxy": delta_proxy,
        "currency": currency,
        "spot": spot,
        **{greek: float(greek_values.get(greek, 0.0)) for greek in RISK_GREEK_KEYS},
        "greeks_ok": greeks_ok,
        "greeks_error": greeks_error,
        "pricing_ok": pricing_ok,
        "pricing_error": pricing_error,
    }
    if pricing_diagnostics:
        if "resolved_engine" in pricing_diagnostics:
            row["resolved_engine"] = pricing_diagnostics["resolved_engine"]
        row.update(
            {
                key: pricing_diagnostics.get(key)
                for key in (
                    "market_input_source",
                    "quote_age_days",
                    "pricing_parameter_profile_id",
                    "pricing_parameter_row_id",
                    "pricing_parameter_match_type",
                    "missing_pricing_fields",
                )
                if key in pricing_diagnostics
            }
        )
    return row


def _float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _source_row_for_position(position: Position) -> dict[str, Any]:
    payload = getattr(position, "source_payload", None) or {}
    row = payload.get("row", {}) if isinstance(payload, dict) else {}
    return row if isinstance(row, dict) else {}


def _source_trade_state_for_position(position: Position) -> str:
    return read_trade_status(_source_row_for_position(position))


def _source_currency_for_position(position: Position) -> str:
    explicit = getattr(position, "currency", None)
    if explicit:
        return str(explicit)
    return read_notional_unit(_source_row_for_position(position))


def recommend_hedge(risk: dict[str, Any]) -> dict[str, Any]:
    # delta_proxy is currency-invariant, so it lives in `shared` (present in both
    # single- and mixed-currency risk). Fall back to the flat `totals` for callers
    # still passing the legacy single-currency shape; `or {}` guards totals=None.
    proxy_source = risk.get("shared") or risk.get("totals") or {}
    delta = float(proxy_source.get("delta_proxy", 0.0))
    if abs(delta) < 1e-9:
        return {"recommendation": "No hedge needed", "target_delta_trade": 0.0}
    return {
        "recommendation": "Use futures or spot to neutralize aggregate delta proxy.",
        "target_delta_trade": -delta,
        "rationale": f"Current delta proxy is {delta:.4g}; target trade offsets it to near zero.",
    }


def model_to_dict(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")

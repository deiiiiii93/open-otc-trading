"""Product-domain helpers for normalized product rows."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    EquityAsianObservation,
    EquityAsianProduct,
    EquityAutocallableObservation,
    EquityAutocallableProduct,
    EquityBarrierProduct,
    EquityFuturesProduct,
    EquityOptionProduct,
    EquityPhoenixCouponProduct,
    EquityProductComponent,
    EquityRangeAccrualObservation,
    EquityRangeAccrualProduct,
    EquitySharkfinProduct,
    EquitySpotProduct,
    EquityTouchProduct,
    Product,
    utcnow,
)
from app.services.underlyings import link_product_underlying, resolve_underlying_currency


@dataclass(frozen=True)
class ProductSpec:
    asset_class: str
    product_family: str
    quantark_class: str | None
    underlying: str
    currency: str | None = None
    terms: dict[str, Any] = field(default_factory=dict)
    components: list[dict[str, Any]] = field(default_factory=list)
    display_name: str | None = None
    source_payload: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.currency:
            object.__setattr__(
                self,
                "currency",
                resolve_underlying_currency(self.underlying, self.currency),
            )


_OPTION_CLASSES = {
    "AmericanOption",
    "BaseEquityOption",
    "CashOrNothingDigitalOption",
    "DigitalOption",
    "EuropeanOption",
    "EuropeanVanillaOption",
    "VanillaOption",
    "VerticalSpreadOption",
}
_AUTOCALLABLE_CLASSES = {
    "KnockOutResetSnowballOption",
    "PhoenixOption",
    "SnowballOption",
}
_BARRIER_CLASSES = {"BarrierOption", "DoubleBarrierOption"}
_TOUCH_CLASSES = {"DoubleOneTouchOption", "OneTouchOption"}
_ASIAN_CLASSES = {"AsianOption"}
_RANGE_ACCRUAL_CLASSES = {"RangeAccrualOption"}
_SHARKFIN_CLASSES = {"DoubleSharkfinOption", "SingleSharkfinOption"}
_SPOT_CLASSES = {"Fund", "SpotInstrument", "Stock"}
_FUTURES_CLASSES = {"Forward", "Futures"}
_OPTION_LIKE_FAMILIES = {
    "asian",
    "autocallable",
    "barrier",
    "option",
    "range_accrual",
    "sharkfin",
    "touch",
}


def normalize_terms(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, ProductSpec):
        return {
            "asset_class": value.asset_class,
            "product_family": value.product_family,
            "quantark_class": value.quantark_class,
            "underlying": value.underlying,
            "currency": value.currency,
            "terms": normalize_terms(value.terms),
            "components": normalize_terms(value.components),
            "display_name": value.display_name,
            "source_payload": normalize_terms(value.source_payload or {}),
        }
    if isinstance(value, dict):
        return {str(key): normalize_terms(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [normalize_terms(item) for item in value]
    if isinstance(value, tuple):
        return [normalize_terms(item) for item in value]
    return value


def product_term_hash(spec: ProductSpec) -> str:
    payload = {
        "asset_class": spec.asset_class,
        "product_family": spec.product_family,
        "quantark_class": spec.quantark_class,
        "underlying": spec.underlying,
        "currency": spec.currency,
        "terms": normalize_terms(spec.terms),
        "components": normalize_terms(spec.components),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def create_or_get_product(
    session: Session, spec: ProductSpec, *, reuse: bool = True
) -> Product:
    term_hash = product_term_hash(spec)
    if reuse:
        existing = session.query(Product).filter(Product.term_hash == term_hash).one_or_none()
        if existing is not None:
            if existing.underlying_id is None:
                link_product_underlying(session, existing)
            return existing

    product = Product(
        asset_class=spec.asset_class,
        product_family=spec.product_family,
        quantark_class=spec.quantark_class,
        display_name=spec.display_name,
        underlying=spec.underlying,
        currency=spec.currency,
        term_hash=term_hash,
        raw_terms=normalize_terms({"terms": spec.terms, "components": spec.components}),
        source_payload=normalize_terms(spec.source_payload or {}),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(product)
    session.flush()
    link_product_underlying(session, product)
    _write_family_rows(session, product, spec)
    session.flush()
    return product


def product_summary(product: Product | None) -> dict[str, Any] | None:
    if product is None:
        return None
    return {
        "id": product.id,
        "asset_class": product.asset_class,
        "product_family": product.product_family,
        "quantark_class": product.quantark_class,
        "display_name": product.display_name,
        "underlying": product.underlying,
        "currency": product.currency,
        "term_hash": product.term_hash,
        "created_at": product.created_at.isoformat() if product.created_at else None,
        "updated_at": product.updated_at.isoformat() if product.updated_at else None,
    }


def query_products(
    session: Session,
    *,
    asset_class: str | None = None,
    family: str | None = None,
    product_family: str | None = None,
    quantark_class: str | None = None,
    underlying: str | None = None,
    currency: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    query = session.query(Product)
    if asset_class:
        query = query.filter(Product.asset_class == asset_class)
    family_filter = product_family or family
    if family_filter:
        query = query.filter(Product.product_family == family_filter)
    if quantark_class:
        query = query.filter(Product.quantark_class == quantark_class)
    if underlying:
        query = query.filter(Product.underlying == underlying)
    if currency:
        query = query.filter(Product.currency == currency)
    capped_limit = max(1, min(int(limit), 1000))
    rows = query.order_by(Product.id).offset(max(0, int(offset))).limit(capped_limit).all()
    return [product_summary(row) for row in rows]


def get_product_details(session: Session, product_id: int) -> dict[str, Any]:
    product = session.get(Product, product_id)
    if product is None:
        raise LookupError(f"Product {product_id} not found")

    family_terms = {
        "option": _row_payload(product.option_terms),
        "autocallable": _row_payload(product.autocallable_terms),
        "phoenix_coupon": _row_payload(product.phoenix_coupon_terms),
        "barrier": _row_payload(product.barrier_terms),
        "touch": _row_payload(product.touch_terms),
        "asian": _row_payload(product.asian_terms),
        "range_accrual": _row_payload(product.range_accrual_terms),
        "sharkfin": _row_payload(product.sharkfin_terms),
        "spot": _row_payload(product.spot_terms),
        "futures": _row_payload(product.futures_terms),
    }
    observations = {
        "autocallable": [_row_payload(row) for row in product.autocallable_observations],
        "asian": [_row_payload(row) for row in product.asian_observations],
        "range_accrual": [_row_payload(row) for row in product.range_accrual_observations],
    }
    components = [
        {
            **_row_payload(row),
            "component_product": product_summary(row.component_product),
        }
        for row in product.components
    ]
    return {
        "source": "products",
        "product": product_summary(product),
        "family_terms": {key: value for key, value in family_terms.items() if value},
        "observations": {key: value for key, value in observations.items() if value},
        "components": components,
        "quantark": {
            "class": product.quantark_class,
            "underlying": product.underlying,
            "currency": product.currency,
        },
    }


def product_details_for_position(session: Session, position_id: int) -> dict[str, Any]:
    from ...models import Position

    position = session.get(Position, position_id)
    if position is None:
        raise LookupError(f"Position {position_id} not found")
    if position.product_id is None:
        raise LookupError(f"Position {position_id} has no product_id")
    details = get_product_details(session, position.product_id)
    details["position_id"] = position_id
    return details


def query_autocallable_observations(
    session: Session, *, product_id: int, role: str | None = None
) -> list[dict[str, Any]]:
    query = session.query(EquityAutocallableObservation).filter(
        EquityAutocallableObservation.product_id == product_id
    )
    if role:
        query = query.filter(EquityAutocallableObservation.observation_role == role)
    rows = query.order_by(
        EquityAutocallableObservation.observation_date.is_(None),
        EquityAutocallableObservation.observation_date,
        EquityAutocallableObservation.observation_role,
        EquityAutocallableObservation.sequence,
    ).all()
    return [
        {
            "id": row.id,
            "product_id": row.product_id,
            "observation_role": row.observation_role,
            "sequence": row.sequence,
            "observation_date": row.observation_date.isoformat()
            if row.observation_date
            else None,
            "observation_time": row.observation_time,
            "barrier_level": row.barrier_level,
            "rate": row.rate,
            "accrual_factor": row.accrual_factor,
            "aggregation": row.aggregation,
            "weight": row.weight,
            "source_payload": row.source_payload,
        }
        for row in rows
    ]


def compatibility_terms(product: Product) -> dict[str, Any]:
    raw = dict(product.raw_terms or {})
    terms = dict(raw.get("terms") or {})
    components = list(raw.get("components") or [])
    if components:
        terms["components"] = components
    return {
        "underlying": product.underlying,
        "product_type": product.quantark_class or product.product_family,
        "product_kwargs": terms,
    }


def _row_payload(row: Any | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        column.name: normalize_terms(getattr(row, column.name))
        for column in row.__table__.columns
    }


def hydrate_position_product_fields(position: Any) -> None:
    if position.product is None:
        return
    terms = compatibility_terms(position.product)
    position.underlying = terms["underlying"]
    position.product_type = terms["product_type"]
    position.product_kwargs = terms["product_kwargs"]


def compatibility_terms_for_position(position: Any) -> dict[str, Any]:
    product = getattr(position, "__dict__", {}).get("product")
    if product is not None:
        return compatibility_terms(product)
    return {
        "underlying": getattr(position, "underlying", None),
        "product_type": getattr(position, "product_type", None),
        "product_kwargs": dict(getattr(position, "product_kwargs", None) or {}),
    }


def product_spec_from_position_payload(payload: dict[str, Any]) -> ProductSpec:
    product_type = str(payload.get("product_type") or "EuropeanVanillaOption")
    kwargs = dict(payload.get("product_kwargs") or {})
    components = list(payload.get("components") or kwargs.pop("components", []) or [])
    underlying = str(payload.get("underlying") or kwargs.get("underlying") or "UNKNOWN")
    family = product_family_for_quantark_class(product_type, components=components)
    return ProductSpec(
        asset_class=str(payload.get("asset_class") or "equity"),
        product_family=family,
        quantark_class=product_type,
        underlying=underlying,
        currency=str(
            kwargs.get("currency")
            or payload.get("currency")
            or resolve_underlying_currency(underlying)
        ),
        terms=kwargs,
        components=components,
        display_name=payload.get("display_name"),
        source_payload=dict(payload.get("source_payload") or {}),
    )


def product_spec_from_executable_terms(terms: Any) -> ProductSpec:
    if hasattr(terms, "model_dump"):
        payload = terms.model_dump(mode="json")
    elif isinstance(terms, dict):
        payload = dict(terms)
    else:
        payload = dict(getattr(terms, "__dict__", {}))
    product_type = str(payload.get("product_type") or "EuropeanVanillaOption")
    kwargs = dict(payload.get("product_kwargs") or {})
    components = list(payload.get("components") or kwargs.pop("components", []) or [])
    market = dict(payload.get("market") or {})
    underlying = str(payload.get("underlying") or kwargs.get("underlying") or "UNKNOWN")
    return ProductSpec(
        asset_class=str(payload.get("asset_class") or "equity"),
        product_family=product_family_for_quantark_class(
            product_type,
            components=components,
        ),
        quantark_class=product_type,
        underlying=underlying,
        currency=str(
            kwargs.get("currency")
            or market.get("currency")
            or resolve_underlying_currency(underlying)
        ),
        terms=kwargs,
        components=components,
        display_name=payload.get("display_name"),
        source_payload=payload,
    )


def backfill_position_products(session: Session, *, limit: int | None = None) -> int:
    from ...models import Position

    query = session.query(Position).filter(Position.product_id.is_(None))
    if limit is not None:
        query = query.limit(limit)
    positions = query.all()
    for position in positions:
        spec = product_spec_from_position_payload(
            {
                "underlying": position.underlying,
                "product_type": position.product_type,
                "product_kwargs": position.product_kwargs or {},
                "source_payload": position.source_payload or {},
            }
        )
        product = create_or_get_product(session, spec, reuse=False)
        position.product_id = product.id
    session.flush()
    return len(positions)


def product_family_for_quantark_class(
    quantark_class: str | None, *, components: list[dict[str, Any]] | None = None
) -> str:
    if components:
        return "package"
    product_type = str(quantark_class or "")
    if product_type in _AUTOCALLABLE_CLASSES:
        return "autocallable"
    if product_type in _BARRIER_CLASSES:
        return "barrier"
    if product_type in _TOUCH_CLASSES:
        return "touch"
    if product_type in _ASIAN_CLASSES:
        return "asian"
    if product_type in _RANGE_ACCRUAL_CLASSES:
        return "range_accrual"
    if product_type in _SHARKFIN_CLASSES:
        return "sharkfin"
    if product_type in _SPOT_CLASSES:
        return "spot"
    if product_type in _FUTURES_CLASSES:
        return "futures"
    if product_type in _OPTION_CLASSES:
        return "option"
    return "option"


def _write_family_rows(session: Session, product: Product, spec: ProductSpec) -> None:
    terms = spec.terms
    family = spec.product_family
    if family in _OPTION_LIKE_FAMILIES:
        _write_option_terms(session, product, terms)
    if family == "autocallable":
        _write_autocallable_terms(session, product, spec)
        _replace_autocallable_observations(session, product, terms)
        _write_phoenix_coupon_terms(session, product, spec)
    elif family == "barrier":
        _write_barrier_terms(session, product, spec)
    elif family == "touch":
        _write_touch_terms(session, product, spec)
    elif family == "asian":
        _write_asian_terms(session, product, terms)
        _replace_asian_observations(session, product, terms)
    elif family == "range_accrual":
        _write_range_accrual_terms(session, product, terms)
        _replace_range_accrual_observations(session, product, terms)
    elif family == "sharkfin":
        _write_sharkfin_terms(session, product, spec)
    elif family == "spot":
        _write_spot_terms(session, product, terms)
    elif family == "futures":
        _write_futures_terms(session, product, terms)
    if spec.components:
        _replace_product_components(session, product, spec.components)


def _write_option_terms(session: Session, product: Product, terms: dict[str, Any]) -> None:
    session.merge(
        EquityOptionProduct(
            product_id=product.id,
            strike=_float_or_none(_first_present(terms, "strike")),
            option_type=_str_or_none(_first_present(terms, "option_type")),
            exercise_type=_str_or_none(_first_present(terms, "exercise_type")),
            maturity=_float_or_none(_first_present(terms, "maturity")),
            exercise_date=_date_or_none(_first_present(terms, "exercise_date")),
            settlement_date=_date_or_none(_first_present(terms, "settlement_date")),
            maturity_date=_date_or_none(
                _first_present(terms, "maturity_date", "expiry_date", "expiry")
            ),
            tenor=_float_or_none(_first_present(terms, "tenor")),
            tenor_end=_str_or_none(_first_present(terms, "tenor_end")),
            annualization_day_count=_str_or_none(
                _first_present(terms, "annualization_day_count", "day_count_convention")
            ),
            initial_price=_float_or_none(_first_present(terms, "initial_price")),
            contract_multiplier=_float_or_none(
                _first_present(terms, "contract_multiplier")
            )
            or 1.0,
        )
    )


def _write_autocallable_terms(
    session: Session, product: Product, spec: ProductSpec
) -> None:
    terms = spec.terms
    barrier_config = _dict_at(terms, "barrier_config")
    payoff_config = _dict_at(terms, "payoff_config")
    accrual_config = _dict_at(terms, "accrual_config")
    session.merge(
        EquityAutocallableProduct(
            product_id=product.id,
            autocallable_kind=_autocallable_kind(spec.quantark_class),
            is_reverse=_bool_or_default(_first_present(terms, "is_reverse"), False),
            initial_price=_float_or_none(_first_present(terms, "initial_price")) or 0.0,
            strike=_float_or_none(_first_present(terms, "strike")) or 0.0,
            contract_multiplier=_float_or_none(
                _first_present(terms, "contract_multiplier")
            )
            or 1.0,
            ko_observation_type=_str_or_none(
                _first_present(barrier_config, "ko_observation_type", "ko_observation")
            ),
            ki_observation_type=_str_or_none(
                _first_present(barrier_config, "ki_observation_type", "ki_observation")
            ),
            ki_continuous=(
                _bool_or_default(_first_present(barrier_config, "ki_continuous"), False)
                or _is_continuous(_first_present(barrier_config, "ki_observation_type"))
            ),
            disable_ko_after_ki=_bool_or_default(
                _first_present(barrier_config, "disable_ko_after_ki"), False
            ),
            payoff_rebate_rate=_float_or_none(
                _first_present(payoff_config, "rebate_rate")
            ),
            payoff_call_rebate_enabled=_bool_or_default(
                _first_present(payoff_config, "call_rebate_enabled"), False
            ),
            payoff_call_strike=_float_or_none(
                _first_present(payoff_config, "call_strike")
            ),
            payoff_call_participation_rate=_float_or_none(
                _first_present(payoff_config, "call_participation_rate")
            ),
            payoff_include_principal=_bool_or_default(
                _first_present(payoff_config, "include_principal"), True
            ),
            payoff_participation_rate=_float_or_none(
                _first_present(payoff_config, "participation_rate")
            ),
            payoff_protection_type=_str_or_none(
                _first_present(payoff_config, "protection_type")
            ),
            payoff_protection_rate=_float_or_none(
                _first_present(payoff_config, "protection_rate")
            ),
            accrual_coupon_pay_type=_str_or_none(
                _first_present(accrual_config, "coupon_pay_type")
            ),
            accrual_is_annualized=_bool_or_default(
                _first_present(accrual_config, "is_annualized"), True
            ),
            accrual_is_annualized_ko=_bool_or_none(
                _first_present(accrual_config, "is_annualized_ko")
            ),
            accrual_is_annualized_ki=_bool_or_none(
                _first_present(accrual_config, "is_annualized_ki")
            ),
            accrual_is_annualized_rebate=_bool_or_none(
                _first_present(accrual_config, "is_annualized_rebate")
            ),
            reset_rate=_float_or_none(
                _first_present(terms, "reset_rate", "post_barrier_config.reset_rate")
            ),
        )
    )


def _replace_autocallable_observations(
    session: Session, product: Product, terms: dict[str, Any]
) -> None:
    session.query(EquityAutocallableObservation).filter_by(
        product_id=product.id
    ).delete(synchronize_session=False)

    barrier_config = _dict_at(terms, "barrier_config")
    coupon_config = _dict_at(terms, "coupon_config")
    accrual_config = _dict_at(terms, "accrual_config")
    schedules = [
        (
            "ko",
            _first_present(
                barrier_config,
                "ko_observation_schedule",
                "ko_observation_dates",
                default=[],
            ),
            {
                "barrier_level": _first_present(barrier_config, "ko_barrier"),
                "rate": _first_present(barrier_config, "ko_rate"),
            },
        ),
        (
            "ki",
            _first_present(
                barrier_config,
                "ki_observation_schedule",
                "ki_observation_dates",
                default=[],
            ),
            {"barrier_level": _first_present(barrier_config, "ki_barrier")},
        ),
        (
            "coupon",
            _first_present(
                coupon_config,
                "coupon_observation_schedule",
                "coupon_observation_dates",
                default=[],
            ),
            {
                "barrier_level": _first_present(coupon_config, "coupon_barrier"),
                "rate": _first_present(coupon_config, "coupon_rate"),
            },
        ),
        (
            "accrual",
            _first_present(
                accrual_config,
                "accrual_schedule",
                "accrual_observation_dates",
                "accrual_factors",
                default=[],
            ),
            {"rate": _first_present(accrual_config, "coupon_rate")},
        ),
    ]
    rows: list[EquityAutocallableObservation] = []
    for role, raw_records, defaults in schedules:
        for sequence, record in enumerate(_as_records(raw_records)):
            rows.append(
                EquityAutocallableObservation(
                    product_id=product.id,
                    observation_role=role,
                    sequence=sequence,
                    observation_date=_date_or_none(
                        _record_value(record, "observation_date", "date")
                    ),
                    observation_time=_float_or_none(
                        _record_value(record, "observation_time", "time")
                    ),
                    barrier_level=_float_or_none(
                        _record_value(record, "barrier_level", "barrier", "ko_level")
                        if isinstance(record, dict)
                        else defaults.get("barrier_level")
                    )
                    or _float_or_none(defaults.get("barrier_level")),
                    rate=_float_or_none(
                        _record_value(record, "rate", "ko_rate", "return_rate", "coupon_rate")
                        if isinstance(record, dict)
                        else defaults.get("rate")
                    )
                    or _float_or_none(defaults.get("rate")),
                    accrual_factor=_float_or_none(
                        _record_value(record, "accrual_factor")
                    ),
                    aggregation=_str_or_none(_record_value(record, "aggregation")),
                    weight=_float_or_none(_record_value(record, "weight")),
                    source_payload=normalize_terms(record) if isinstance(record, dict) else None,
                )
            )
    if rows:
        session.add_all(rows)


def _write_phoenix_coupon_terms(
    session: Session, product: Product, spec: ProductSpec
) -> None:
    if spec.quantark_class != "PhoenixOption" and not spec.terms.get("coupon_config"):
        return
    terms = spec.terms
    coupon_config = _dict_at(terms, "coupon_config")
    barrier_config = _dict_at(terms, "barrier_config")
    session.merge(
        EquityPhoenixCouponProduct(
            product_id=product.id,
            coupon_barrier=_float_or_none(
                _first_present(
                    coupon_config,
                    "coupon_barrier",
                    default=_first_present(terms, "coupon_barrier", "ki_barrier"),
                )
            )
            or _float_or_none(_first_present(barrier_config, "ki_barrier"))
            or 0.0,
            coupon_rate=_float_or_none(
                _first_present(
                    coupon_config,
                    "coupon_rate",
                    default=_first_present(terms, "coupon_rate", "coupon_yield"),
                )
            )
            or 0.0,
            coupon_pay_type=_str_or_none(
                _first_present(coupon_config, "coupon_pay_type", "pay_type")
            ),
            day_count_convention=_str_or_none(
                _first_present(coupon_config, "day_count_convention")
            ),
            memory_coupon=_bool_or_default(
                _first_present(coupon_config, "memory_coupon"), True
            ),
            fixed_coupon_year_fraction=_float_or_none(
                _first_present(coupon_config, "fixed_coupon_year_fraction")
            ),
        )
    )


def _write_barrier_terms(
    session: Session, product: Product, spec: ProductSpec
) -> None:
    terms = spec.terms
    barrier_config = _dict_at(terms, "barrier_config")
    session.merge(
        EquityBarrierProduct(
            product_id=product.id,
            barrier_kind=_barrier_kind(spec.quantark_class, terms),
            barrier=_float_or_none(_first_present(terms, "barrier", "barrier_config.barrier")),
            barrier_type=_str_or_none(
                _first_present(terms, "barrier_type", "barrier_config.barrier_type")
            ),
            upper_barrier=_float_or_none(
                _first_present(terms, "upper_barrier", "barrier_config.upper_barrier")
            ),
            lower_barrier=_float_or_none(
                _first_present(terms, "lower_barrier", "barrier_config.lower_barrier")
            ),
            rebate=_float_or_none(_first_present(terms, "rebate", "barrier_config.rebate")),
            monitoring_type=_str_or_none(
                _first_present(barrier_config, "monitoring_type", "observation_type")
            ),
        )
    )


def _write_touch_terms(session: Session, product: Product, spec: ProductSpec) -> None:
    terms = spec.terms
    touch_type = _str_or_none(_first_present(terms, "touch_type"))
    session.merge(
        EquityTouchProduct(
            product_id=product.id,
            touch_kind=_touch_kind(spec.quantark_class, touch_type),
            barrier=_float_or_none(_first_present(terms, "barrier")),
            upper_barrier=_float_or_none(_first_present(terms, "upper_barrier")),
            lower_barrier=_float_or_none(_first_present(terms, "lower_barrier")),
            touch_type=touch_type,
            payout=_float_or_none(_first_present(terms, "payout")),
            rebate=_float_or_none(_first_present(terms, "rebate")),
            monitoring_type=_str_or_none(_first_present(terms, "monitoring_type")),
        )
    )


def _write_asian_terms(session: Session, product: Product, terms: dict[str, Any]) -> None:
    records = _as_records(
        _first_present(terms, "averaging_dates", "observation_dates", default=[])
    )
    session.merge(
        EquityAsianProduct(
            product_id=product.id,
            averaging_method=_str_or_none(_first_present(terms, "averaging_method")),
            averaging_kind=_str_or_none(_first_present(terms, "averaging_kind")),
            n_observations=int(
                _float_or_none(
                    _first_present(terms, "n_observations", "num_observations")
                )
                or len(records)
                or 0
            ),
        )
    )


def _replace_asian_observations(
    session: Session, product: Product, terms: dict[str, Any]
) -> None:
    session.query(EquityAsianObservation).filter_by(product_id=product.id).delete(
        synchronize_session=False
    )
    records = _as_records(
        _first_present(terms, "averaging_dates", "observation_dates", default=[])
    )
    if records:
        session.add_all(
            [
                EquityAsianObservation(
                    product_id=product.id,
                    sequence=sequence,
                    observation_date=_date_or_none(
                        _record_value(record, "observation_date", "date")
                    ),
                    observation_time=_float_or_none(
                        _record_value(record, "observation_time", "time")
                    ),
                    observed_price=_float_or_none(
                        _record_value(record, "observed_price", "price")
                    ),
                    weight=_float_or_none(_record_value(record, "weight")),
                )
                for sequence, record in enumerate(records)
            ]
        )


def _write_range_accrual_terms(
    session: Session, product: Product, terms: dict[str, Any]
) -> None:
    range_config = _dict_at(terms, "range_config")
    session.merge(
        EquityRangeAccrualProduct(
            product_id=product.id,
            lower_barrier=_float_or_none(
                _first_present(terms, "lower_barrier", "range_config.lower_barrier")
            )
            or 0.0,
            upper_barrier=_float_or_none(
                _first_present(terms, "upper_barrier", "range_config.upper_barrier")
            )
            or 0.0,
            accrual_rate=_float_or_none(
                _first_present(
                    terms,
                    "accrual_rate",
                    "range_config.accrual_rate",
                    "coupon_yield",
                )
            )
            or 0.0,
            observation_type=_str_or_none(
                _first_present(range_config, "observation_type")
            ),
            day_count_convention=_str_or_none(
                _first_present(terms, "day_count_convention")
            ),
        )
    )


def _replace_range_accrual_observations(
    session: Session, product: Product, terms: dict[str, Any]
) -> None:
    session.query(EquityRangeAccrualObservation).filter_by(
        product_id=product.id
    ).delete(synchronize_session=False)
    range_config = _dict_at(terms, "range_config")
    records = _as_records(
        _first_present(
            terms,
            "observation_schedule",
            "observation_dates",
            "range_config.observation_schedule",
            default=[],
        )
    )
    if records:
        session.add_all(
            [
                EquityRangeAccrualObservation(
                    product_id=product.id,
                    sequence=sequence,
                    observation_date=_date_or_none(
                        _record_value(record, "observation_date", "date")
                    ),
                    observation_time=_float_or_none(
                        _record_value(record, "observation_time", "time")
                    ),
                    lower_barrier=_float_or_none(
                        _record_value(record, "lower_barrier")
                    )
                    or _float_or_none(_first_present(range_config, "lower_barrier")),
                    upper_barrier=_float_or_none(
                        _record_value(record, "upper_barrier")
                    )
                    or _float_or_none(_first_present(range_config, "upper_barrier")),
                    weight=_float_or_none(_record_value(record, "weight")),
                )
                for sequence, record in enumerate(records)
            ]
        )


def _write_sharkfin_terms(
    session: Session, product: Product, spec: ProductSpec
) -> None:
    terms = spec.terms
    session.merge(
        EquitySharkfinProduct(
            product_id=product.id,
            sharkfin_kind=(
                "double" if spec.quantark_class == "DoubleSharkfinOption" else "single"
            ),
            strike=_float_or_none(_first_present(terms, "strike")),
            barrier=_float_or_none(_first_present(terms, "barrier")),
            upper_barrier=_float_or_none(_first_present(terms, "upper_barrier")),
            lower_barrier=_float_or_none(_first_present(terms, "lower_barrier")),
            option_type=_str_or_none(_first_present(terms, "option_type")),
            participation_rate=_float_or_none(
                _first_present(terms, "participation_rate")
            ),
            coupon=_float_or_none(_first_present(terms, "coupon", "coupon_rate")),
            rebate=_float_or_none(_first_present(terms, "rebate")),
            observation_type=_str_or_none(_first_present(terms, "observation_type")),
        )
    )


def _write_spot_terms(session: Session, product: Product, terms: dict[str, Any]) -> None:
    session.merge(
        EquitySpotProduct(
            product_id=product.id,
            deltaone_type=_str_or_none(_first_present(terms, "deltaone_type")) or "STOCK",
            # Synthesized DeltaOne bookings carry persistence metadata as _otc_ keys
            # (not QuantArk constructor kwargs); read either form.
            instrument_code=_str_or_none(
                _first_present(terms, "instrument_code", "_otc_instrument_code")
            )
            or product.underlying,
            exchange=_str_or_none(_first_present(terms, "exchange", "_otc_exchange")),
            contract_multiplier=_float_or_none(
                _first_present(terms, "contract_multiplier", "_otc_contract_multiplier")
            )
            or 1.0,
        )
    )


def _write_futures_terms(session: Session, product: Product, terms: dict[str, Any]) -> None:
    session.merge(
        EquityFuturesProduct(
            product_id=product.id,
            contract_code=_str_or_none(
                _first_present(terms, "contract_code", "_otc_contract_code")
            )
            or product.underlying,
            multiplier=_float_or_none(_first_present(terms, "multiplier")) or 1.0,
            maturity=_float_or_none(_first_present(terms, "maturity")),
            maturity_date=_date_or_none(_first_present(terms, "maturity_date")),
            basis=_float_or_none(_first_present(terms, "basis")) or 0.0,
            basis_decay_rate=_float_or_none(
                _first_present(terms, "basis_decay_rate")
            )
            or 1.0,
            market_price=_float_or_none(_first_present(terms, "market_price")),
        )
    )


def _replace_product_components(
    session: Session, product: Product, components: list[dict[str, Any]]
) -> None:
    session.query(EquityProductComponent).filter_by(parent_product_id=product.id).delete(
        synchronize_session=False
    )
    rows: list[EquityProductComponent] = []
    for sequence, component in enumerate(components):
        component_product_id = _component_product_id(session, component)
        rows.append(
            EquityProductComponent(
                parent_product_id=product.id,
                component_product_id=component_product_id,
                component_role=_str_or_none(component.get("component_role"))
                or _str_or_none(component.get("role"))
                or "component",
                quantity=_float_or_none(component.get("quantity")) or 1.0,
                weight=_float_or_none(component.get("weight")) or 1.0,
                sequence=int(component.get("sequence", sequence)),
                source_payload=normalize_terms(component),
            )
        )
    if rows:
        session.add_all(rows)


def _component_product_id(session: Session, component: dict[str, Any]) -> int:
    existing_id = component.get("component_product_id") or component.get("product_id")
    if existing_id is not None:
        return int(existing_id)
    nested = component.get("product_spec") or component.get("product")
    if isinstance(nested, ProductSpec):
        return create_or_get_product(session, nested, reuse=True).id
    if isinstance(nested, dict):
        if "product_type" in nested or "product_kwargs" in nested:
            return create_or_get_product(
                session, product_spec_from_position_payload(nested), reuse=True
            ).id
        nested_components = list(nested.get("components") or [])
        nested_spec = ProductSpec(
            asset_class=str(nested.get("asset_class") or "equity"),
            product_family=str(
                nested.get("product_family")
                or product_family_for_quantark_class(
                    nested.get("quantark_class"), components=nested_components
                )
            ),
            quantark_class=nested.get("quantark_class"),
            underlying=str(nested.get("underlying") or "UNKNOWN"),
            currency=str(nested.get("currency") or "CNY"),
            terms=dict(nested.get("terms") or {}),
            components=nested_components,
            display_name=nested.get("display_name"),
            source_payload=nested.get("source_payload"),
        )
        return create_or_get_product(session, nested_spec, reuse=True).id
    raise ValueError("Product component must include component_product_id or product_spec")


def _autocallable_kind(quantark_class: str | None) -> str:
    if quantark_class == "PhoenixOption":
        return "phoenix"
    if quantark_class == "KnockOutResetSnowballOption":
        return "ko_reset"
    return "snowball"


def _barrier_kind(quantark_class: str | None, terms: dict[str, Any]) -> str:
    explicit = _str_or_none(_first_present(terms, "barrier_kind"))
    if explicit:
        return explicit
    return "double" if quantark_class == "DoubleBarrierOption" else "single"


def _touch_kind(quantark_class: str | None, touch_type: str | None) -> str:
    normalized = (touch_type or "").lower()
    if "no_touch" in normalized:
        return "double_no_touch"
    if quantark_class == "DoubleOneTouchOption":
        return "double_one_touch"
    return "one_touch"


def _is_continuous(value: Any) -> bool:
    return str(value or "").lower() == "continuous"


def _first_present(source: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        current: Any = source
        found = True
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                found = False
                break
            current = current[part]
        if found and current is not None:
            return current
    return default


def _dict_at(source: dict[str, Any], key: str) -> dict[str, Any]:
    value = _first_present(source, key, default={})
    return value if isinstance(value, dict) else {}


def _as_records(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _record_value(record: Any, *keys: str) -> Any:
    if isinstance(record, dict):
        return _first_present(record, *keys)
    key_set = set(keys)
    if key_set & {"date", "observation_date"}:
        if _date_or_none(record) is not None:
            return record
    if key_set & {"time", "observation_time"} and _float_or_none(record) is not None:
        return record
    if key_set & {"accrual_factor"}:
        return record
    return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "false", "f", "0", "no", "n", "off"}:
            return False
        if normalized in {"true", "t", "1", "yes", "y", "on"}:
            return True
    return bool(value)


def _bool_or_default(value: Any, default: bool) -> bool:
    parsed = _bool_or_none(value)
    return default if parsed is None else parsed


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _date_or_none(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None

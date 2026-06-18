from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session

from app.models import EngineConfigVariant, Position

PRICING_ENGINE_TO_BACKTEST_ENGINE = {
    "SnowballQuadEngine": "quad",
    "KOResetSnowballQuadEngine": "quad",
    "PhoenixQuadEngine": "quad",
    "EuropeanQuadEngine": "quad",
    "BarrierQuadEngine": "quad",
    "OneTouchQuadEngine": "quad",
    "BlackScholesEngine": "analytical",
    "AmericanOptionAnalyticalEngine": "analytical",
    "DigitalOptionAnalyticalEngine": "analytical",
    "BarrierAnalyticalEngine": "analytical",
    "OneTouchAnalyticalEngine": "analytical",
    "AsianOptionAnalyticalEngine": "analytical",
    "RangeAccrualAnalyticalEngine": "analytical",
    "SingleSharkfinOptionAnalyticalEngine": "analytical",
    "DoubleSharkfinOptionAnalyticalEngine": "analytical",
    "DeltaOneEngine": "analytical",
    "EuropeanMCEngine": "mc",
    "AmericanOptionMCEngine": "mc",
    "DigitalOptionMCEngine": "mc",
    "BarrierOptionMCEngine": "mc",
    "AsianOptionMCEngine": "mc",
    "RangeAccrualMCEngine": "mc",
    "SingleSharkfinOptionMCEngine": "mc",
    "DoubleSharkfinOptionMCEngine": "mc",
    "SnowballMCEngine": "mc",
    "PhoenixMCEngine": "mc",
    "PDEEngine": "pde",
}

AUTOCALLABLE_PRODUCT_TYPES = {
    "SnowballOption",
    "PhoenixOption",
    "KnockOutResetSnowballOption",
}

DEFAULT_ENGINE_BY_PRODUCT_TYPE = {
    "Futures": "DeltaOneEngine",
    "SpotInstrument": "DeltaOneEngine",
    "EuropeanVanillaOption": "BlackScholesEngine",
    "AmericanOption": "AmericanOptionAnalyticalEngine",
    "CashOrNothingDigitalOption": "DigitalOptionAnalyticalEngine",
    "BarrierOption": "BarrierAnalyticalEngine",
    "OneTouchOption": "OneTouchAnalyticalEngine",
    "DoubleOneTouchOption": "OneTouchAnalyticalEngine",
    "AsianOption": "AsianOptionAnalyticalEngine",
    "RangeAccrualOption": "RangeAccrualAnalyticalEngine",
    "SingleSharkfinOption": "SingleSharkfinOptionAnalyticalEngine",
    "DoubleSharkfinOption": "DoubleSharkfinOptionAnalyticalEngine",
    "SnowballOption": "SnowballQuadEngine",
    "PhoenixOption": "PhoenixQuadEngine",
    "KnockOutResetSnowballOption": "KOResetSnowballQuadEngine",
}

QUAD_ENGINE_BY_PRODUCT_TYPE = {
    "SnowballOption": "SnowballQuadEngine",
    "PhoenixOption": "PhoenixQuadEngine",
    "KnockOutResetSnowballOption": "KOResetSnowballQuadEngine",
    "EuropeanVanillaOption": "EuropeanQuadEngine",
    "BarrierOption": "BarrierQuadEngine",
    "OneTouchOption": "OneTouchQuadEngine",
}

MC_ENGINE_BY_PRODUCT_TYPE = {
    "EuropeanVanillaOption": "EuropeanMCEngine",
    "AmericanOption": "AmericanOptionMCEngine",
    "CashOrNothingDigitalOption": "DigitalOptionMCEngine",
    "BarrierOption": "BarrierOptionMCEngine",
    "AsianOption": "AsianOptionMCEngine",
    "RangeAccrualOption": "RangeAccrualMCEngine",
    "SingleSharkfinOption": "SingleSharkfinOptionMCEngine",
    "DoubleSharkfinOption": "DoubleSharkfinOptionMCEngine",
    "SnowballOption": "SnowballMCEngine",
    "PhoenixOption": "PhoenixMCEngine",
}

# Products QuantArk's unified PDEEngine auto-dispatches to a solver for.
PDE_PRODUCT_TYPES = {
    "EuropeanVanillaOption",
    "AmericanOption",
    "BarrierOption",
    "DoubleBarrierOption",
    "OneTouchOption",
    "DoubleOneTouchOption",
    "SnowballOption",
    "KnockOutResetSnowballOption",
    "PhoenixOption",
}

FAMILY_ENGINE_ENUMS = {"QUAD", "MC", "PDE", "ANALYTICAL"}


@dataclass(frozen=True)
class ResolvedPricingEngine:
    engine_name: str
    engine_kwargs: dict[str, Any]
    source: str
    engine_config_id: int | None = None
    rule_name: str | None = None

    def diagnostics(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "engine_config_id": self.engine_config_id,
            "rule_name": self.rule_name,
            "engine_name": self.engine_name,
            "engine_kwargs": self.engine_kwargs,
        }


def _product_rule(
    product_type: str,
    engine_name: str,
    engine_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": product_type,
        "match": {"product_type": product_type},
        "pricing": {"engine_name": engine_name, "engine_kwargs": engine_kwargs or {}},
    }


def _family_rule(product_family: str, engine_type: str) -> dict[str, Any]:
    return {
        "name": product_family.title(),
        "match": {"product_family": product_family},
        "pricing": {"engine_type": engine_type},
    }


DEFAULT_ENGINE_CONFIG_RULES: dict[str, Any] = {
    "rules": [
        _family_rule("autocallables", "QUAD"),
        _family_rule("others", "ANALYTICAL"),
        _product_rule("Futures", "DeltaOneEngine"),
        _product_rule("SpotInstrument", "DeltaOneEngine"),
    ]
}


def engine_is_missing(value: Any) -> bool:
    return value is None or str(value).strip().lower() in {"", "auto"}


def validate_rules(rules: dict[str, Any]) -> None:
    if not isinstance(rules, dict):
        raise ValueError("rules must be an object")
    raw_rules = rules.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ValueError("rules.rules must be a list")
    for idx, rule in enumerate(raw_rules):
        if not isinstance(rule, dict):
            raise ValueError(f"rules[{idx}] must be an object")
        match = rule.get("match") or {}
        pricing = rule.get("pricing") or {}
        if not isinstance(match, dict):
            raise ValueError(f"rules[{idx}].match must be an object")
        if not match.get("product_type") and not match.get("product_family"):
            raise ValueError(f"rules[{idx}] needs match.product_type or match.product_family")
        if not isinstance(pricing, dict):
            raise ValueError(f"rules[{idx}].pricing must be an object")
        if match.get("product_type"):
            engine_name = pricing.get("engine_name")
            if not engine_name:
                raise ValueError(f"rules[{idx}] needs pricing.engine_name")
            if engine_name not in PRICING_ENGINE_TO_BACKTEST_ENGINE:
                raise ValueError(
                    f"rules[{idx}] pricing.engine_name {engine_name!r} is not a known QuantArk engine"
                )
        engine_kwargs = pricing.get("engine_kwargs")
        if engine_kwargs is not None and not isinstance(engine_kwargs, dict):
            raise ValueError(f"rules[{idx}].pricing.engine_kwargs must be an object")
        if match.get("product_family"):
            engine_type = str(pricing.get("engine_type") or "").upper()
            if engine_type not in FAMILY_ENGINE_ENUMS:
                raise ValueError(
                    f"rules[{idx}] needs pricing.engine_type in {sorted(FAMILY_ENGINE_ENUMS)}"
                )


def get_engine_config(
    session: Session, engine_config_id: int | None
) -> EngineConfigVariant | None:
    if engine_config_id is not None:
        config = session.get(EngineConfigVariant, engine_config_id)
        if config is None:
            raise ValueError(f"Engine config not found: {engine_config_id}")
        return config
    return (
        session.query(EngineConfigVariant)
        .filter(EngineConfigVariant.is_default.is_(True), EngineConfigVariant.status == "active")
        .order_by(EngineConfigVariant.id.desc())
        .first()
    )


def ensure_default_engine_config(session: Session) -> EngineConfigVariant:
    existing = get_engine_config(session, None)
    if existing is not None:
        return existing
    config = EngineConfigVariant(
        name="System Default",
        description="Default engine rules matching current product mappings.",
        status="active",
        is_default=True,
        rules=DEFAULT_ENGINE_CONFIG_RULES,
    )
    session.add(config)
    session.flush()
    return config


def set_default_engine_config(session: Session, config: EngineConfigVariant) -> None:
    for row in session.query(EngineConfigVariant).all():
        row.is_default = row.id == config.id
        session.add(row)


def resolve_pricing_engine(
    position: Position | Any,
    config: EngineConfigVariant | None,
    *,
    override_engine_name: str | None = None,
    override_engine_kwargs: dict[str, Any] | None = None,
) -> ResolvedPricingEngine:
    if not engine_is_missing(override_engine_name):
        return ResolvedPricingEngine(
            engine_name=str(override_engine_name),
            engine_kwargs=dict(override_engine_kwargs or {}),
            source="override",
        )

    position_engine = getattr(position, "engine_name", None)
    if not engine_is_missing(position_engine):
        return ResolvedPricingEngine(
            engine_name=str(position_engine),
            engine_kwargs=dict(getattr(position, "engine_kwargs", None) or {}),
            source="position",
        )

    rule = _match_rule(position, config)
    if rule is None:
        raise ValueError(f"No engine config rule matched position {getattr(position, 'id', None)}")
    pricing = rule.get("pricing") or {}
    product_type = _product_type_for_position(position)
    if pricing.get("engine_type"):
        engine_name = _engine_name_from_family_enum(product_type, str(pricing["engine_type"]))
    else:
        engine_name = str(pricing["engine_name"])
    return ResolvedPricingEngine(
        engine_name=engine_name,
        engine_kwargs=dict(pricing.get("engine_kwargs") or {}),
        source="engine_config",
        engine_config_id=getattr(config, "id", None),
        rule_name=rule.get("name"),
    )


def resolve_backtest_engine(position: Position | Any, product: Any, config: EngineConfigVariant | None) -> dict[str, Any]:
    resolved = resolve_pricing_engine(position, config)
    mapped = PRICING_ENGINE_TO_BACKTEST_ENGINE.get(resolved.engine_name)
    if mapped is None:
        raise ValueError(
            f"No backtest engine mapping for pricing engine {resolved.engine_name}"
        )
    return {
        "engine": mapped,
        "source": resolved.source,
        "engine_config_id": resolved.engine_config_id,
        "rule_name": resolved.rule_name,
    }


def position_with_engine(position: Position | Any, resolved: ResolvedPricingEngine) -> Any:
    source_payload = getattr(position, "source_payload", None)
    product_kwargs = dict(getattr(position, "product_kwargs", None) or {})
    return SimpleNamespace(
        id=getattr(position, "id", None),
        portfolio_id=getattr(position, "portfolio_id", None),
        product_id=getattr(position, "product_id", None),
        underlying_id=getattr(position, "underlying_id", None),
        underlying=getattr(position, "underlying", ""),
        product_type=getattr(position, "product_type", ""),
        product_kwargs=product_kwargs,
        product=getattr(position, "product", None),
        engine_name=resolved.engine_name,
        engine_kwargs=dict(resolved.engine_kwargs),
        quantity=float(getattr(position, "quantity", 0.0) or 0.0),
        entry_price=float(getattr(position, "entry_price", 0.0) or 0.0),
        currency=getattr(position, "currency", None),
        status=getattr(position, "status", "open"),
        position_kind=getattr(position, "position_kind", "otc"),
        source_trade_id=getattr(position, "source_trade_id", None),
        source_row=getattr(position, "source_row", None),
        mapping_status=getattr(position, "mapping_status", "manual"),
        mapping_error=getattr(position, "mapping_error", None),
        source_payload=dict(source_payload) if isinstance(source_payload, dict) else source_payload,
        trade_effective_date=getattr(position, "trade_effective_date", None),
    )


def _match_rule(
    position: Position | Any,
    config: EngineConfigVariant | None,
    *,
    product: Any | None = None,
) -> dict[str, Any] | None:
    if config is None:
        return None
    product_type = _product_type_for_position(position, product=product)
    family = _engine_config_family(position, product_type)
    rules = (config.rules or {}).get("rules", [])
    # Product-type rules always win regardless of JSON order.
    for rule in rules:
        match = rule.get("match") or {}
        if match.get("product_type") and match["product_type"] == product_type:
            return rule
    for rule in rules:
        match = rule.get("match") or {}
        if match.get("product_family") and family and match["product_family"] == family:
            return rule
    return None


def _product_type_for_position(position: Position | Any, *, product: Any | None = None) -> str:
    product_type = getattr(product, "__class__", type("", (), {})).__name__ if product is not None else ""
    return product_type or str(getattr(position, "product_type", "") or "")


def _engine_config_family(position: Position | Any, product_type: str) -> str:
    if product_type in AUTOCALLABLE_PRODUCT_TYPES:
        return "autocallables"
    product_family = str(getattr(getattr(position, "product", None), "product_family", "") or "")
    if product_family == "autocallable":
        return "autocallables"
    return "others"


def _engine_name_from_family_enum(product_type: str, engine_type: str) -> str:
    normalized = engine_type.strip().upper()
    if normalized not in FAMILY_ENGINE_ENUMS:
        raise ValueError(f"Unsupported engine type {engine_type!r}")
    if normalized == "ANALYTICAL":
        engine_name = DEFAULT_ENGINE_BY_PRODUCT_TYPE.get(product_type)
        if engine_name is None or PRICING_ENGINE_TO_BACKTEST_ENGINE.get(engine_name) != "analytical":
            raise ValueError(f"No analytical engine mapping for {product_type}")
        return engine_name
    if normalized == "QUAD":
        engine_name = QUAD_ENGINE_BY_PRODUCT_TYPE.get(product_type)
        if engine_name is not None:
            return engine_name
    if normalized == "MC":
        engine_name = MC_ENGINE_BY_PRODUCT_TYPE.get(product_type)
        if engine_name is not None:
            return engine_name
    if normalized == "PDE" and product_type in PDE_PRODUCT_TYPES:
        return "PDEEngine"
    raise ValueError(f"No {normalized} engine mapping for {product_type}")

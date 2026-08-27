"""Agent-facing field catalog for the progressive position-field query scheme.

Single source of truth for which position fields an agent may select through
``query_positions``: each spec names one selectable column, its value type,
and a one-line meaning. Three surfaces render from this module so they cannot
drift apart:

- ``query_column_map()`` — the SQLAlchemy column allowlist behind
  ``query_positions`` (see position_terms.py).
- ``field_catalog_payload()`` — the JSON the ``describe_position_fields``
  tool returns so an agent can discover field names before selecting them.
- ``select_hint()`` — the group summary embedded in the ``query_positions``
  tool schema's ``select`` argument description.

Add a field here and all three surfaces update together; a field absent here
is not queryable. ``query_positions`` returns ONLY the selected fields — the
catalog exists so agents ask for exactly what the question needs instead of
receiving every column of every position.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...models import (
    AsianTerm,
    DoubleBarrierTerm,
    EquityFuturesProduct,
    EquityOptionProduct,
    OptionCoreTerm,
    Position,
    PositionBarrierState,
    Product,
    SharkfinTerm,
    SingleBarrierTerm,
    SnowballTerm,
)


@dataclass(frozen=True)
class PositionFieldSpec:
    """One selectable ``query_positions`` field."""

    name: str  # selectable name, e.g. "option_core.expiry_date"
    group: str  # catalog group, e.g. "option_core"
    value_type: str  # string | number | integer | date | datetime | boolean
    description: str
    column: Any  # SQLAlchemy instrumented column
    listed: bool = True  # unlisted aliases stay queryable but hide from the catalog


def _spec(
    name: str,
    group: str,
    value_type: str,
    description: str,
    column: Any,
    *,
    listed: bool = True,
) -> PositionFieldSpec:
    return PositionFieldSpec(
        name=name,
        group=group,
        value_type=value_type,
        description=description,
        column=column,
        listed=listed,
    )


# --------------------------------------------------------------------------- #
# Curated, agent-listed fields. Base position columns are selectable under
# their bare names (``status``) and, unlisted, under ``positions.<name>``.
# --------------------------------------------------------------------------- #

_BASE_FIELDS: tuple[tuple[str, str, str, Any], ...] = (
    ("id", "integer", "Position id — the key for follow-up term/detail reads.", Position.id),
    ("portfolio_id", "integer", "Owning portfolio id.", Position.portfolio_id),
    ("product_id", "integer", "Normalized product id — the key for get_product_details.", Position.product_id),
    ("underlying", "string", "Underlying symbol, e.g. 000300.SH.", Position.underlying),
    ("product_type", "string", "Booked product type, e.g. EuropeanVanillaOption.", Position.product_type),
    ("quantity", "number", "Signed position quantity.", Position.quantity),
    ("entry_price", "number", "Booked entry price.", Position.entry_price),
    ("currency", "string", "Position currency.", Position.currency),
    ("status", "string", "Position status: open, closed, knocked_in, ...", Position.status),
    ("position_kind", "string", "Position kind classification, when set.", Position.position_kind),
    ("source_trade_id", "string", "External/source trade identifier.", Position.source_trade_id),
    ("trade_effective_date", "date", "Trade effective date used for temporal filtering.", Position.trade_effective_date),
    ("created_at", "datetime", "Position row creation timestamp.", Position.created_at),
    ("updated_at", "datetime", "Position row last-update timestamp.", Position.updated_at),
)

POSITION_FIELD_SPECS: tuple[PositionFieldSpec, ...] = tuple(
    [
        *(
            _spec(name, "positions", value_type, description, column)
            for name, value_type, description, column in _BASE_FIELDS
        ),
        _spec("position_id", "positions", "integer", "Alias of id.", Position.id, listed=False),
        # Product root (join through Position.product_id).
        _spec("product.asset_class", "product", "string", "Product asset class, e.g. equity.", Product.asset_class),
        _spec("product.product_family", "product", "string", "Normalized product family, e.g. autocallable.", Product.product_family),
        _spec("product.quantark_class", "product", "string", "Authoritative taxonomy class, e.g. SnowballOption.", Product.quantark_class),
        _spec("product.display_name", "product", "string", "Human-readable product name.", Product.display_name),
        _spec("product.underlying", "product", "string", "Underlying symbol on the normalized product.", Product.underlying),
        _spec("product.currency", "product", "string", "Currency on the normalized product.", Product.currency),
        # Option core terms mirrored from product kwargs (per-position).
        _spec("option_core.strike", "option_core", "number", "Option strike.", OptionCoreTerm.strike),
        _spec(
            "option_core.expiry_date",
            "option_core",
            "date",
            "Option expiry, folded from kwargs expiry/expiry_date/maturity_date/exercise_date.",
            OptionCoreTerm.expiry_date,
        ),
        _spec("option_core.option_type", "option_core", "string", "CALL or PUT.", OptionCoreTerm.option_type),
        _spec("option_core.side", "option_core", "string", "Option side, e.g. long.", OptionCoreTerm.side),
        _spec("option_core.currency", "option_core", "string", "Option term currency.", OptionCoreTerm.currency),
        _spec("option_core.notional", "option_core", "number", "Option notional.", OptionCoreTerm.notional),
        # Normalized option product terms (per-product, through product_id).
        _spec("option.exercise_type", "option", "string", "Exercise style, e.g. european/american.", EquityOptionProduct.exercise_type),
        _spec(
            "option.exercise_date",
            "option",
            "date",
            "Exercise/maturity date: the date a European option exercises; last exercise date for American.",
            EquityOptionProduct.exercise_date,
        ),
        _spec("option.settlement_date", "option", "date", "Cash settlement date for the option payoff.", EquityOptionProduct.settlement_date),
        _spec("option.maturity_date", "option", "date", "Contract maturity date, when recorded separately.", EquityOptionProduct.maturity_date),
        _spec("option.maturity", "option", "number", "Model maturity horizon in years.", EquityOptionProduct.maturity),
        _spec("option.tenor", "option", "number", "Tenor in years as booked.", EquityOptionProduct.tenor),
        _spec("option.tenor_end", "option", "string", "Tenor end expression, e.g. a date or schedule label.", EquityOptionProduct.tenor_end),
        _spec("option.initial_price", "option", "number", "Initial underlying price at inception.", EquityOptionProduct.initial_price),
        _spec("option.contract_multiplier", "option", "number", "Contract multiplier.", EquityOptionProduct.contract_multiplier),
        # Normalized futures product terms (per-product, through product_id).
        _spec("futures.contract_code", "futures", "string", "Futures contract code, e.g. IF2612.", EquityFuturesProduct.contract_code),
        _spec("futures.multiplier", "futures", "number", "Futures contract multiplier.", EquityFuturesProduct.multiplier),
        _spec("futures.maturity", "futures", "number", "Model maturity horizon in years.", EquityFuturesProduct.maturity),
        _spec("futures.maturity_date", "futures", "date", "Futures expiry/delivery date, when recorded.", EquityFuturesProduct.maturity_date),
        _spec("futures.basis", "futures", "number", "Futures basis at booking.", EquityFuturesProduct.basis),
        _spec("futures.market_price", "futures", "number", "Marked futures price.", EquityFuturesProduct.market_price),
        # Single barrier terms (per-position mirror).
        _spec("single_barrier.barrier", "single_barrier", "number", "Barrier level.", SingleBarrierTerm.barrier),
        _spec("single_barrier.barrier_type", "single_barrier", "string", "Barrier type, e.g. DOWN_IN.", SingleBarrierTerm.barrier_type),
        _spec("single_barrier.rebate", "single_barrier", "number", "Rebate paid on barrier event.", SingleBarrierTerm.rebate),
        # Double barrier terms (per-position mirror).
        _spec("double_barrier.upper_barrier", "double_barrier", "number", "Upper barrier level.", DoubleBarrierTerm.upper_barrier),
        _spec("double_barrier.lower_barrier", "double_barrier", "number", "Lower barrier level.", DoubleBarrierTerm.lower_barrier),
        _spec("double_barrier.barrier_kind", "double_barrier", "string", "Double barrier kind.", DoubleBarrierTerm.barrier_kind),
        _spec("double_barrier.rebate", "double_barrier", "number", "Rebate paid on barrier event.", DoubleBarrierTerm.rebate),
        # Sharkfin terms (per-position mirror).
        _spec("sharkfin.participation_rate", "sharkfin", "number", "Sharkfin participation rate.", SharkfinTerm.participation_rate),
        _spec("sharkfin.coupon", "sharkfin", "number", "Sharkfin coupon rate.", SharkfinTerm.coupon),
        # Asian terms (per-position mirror).
        _spec("asian.averaging_method", "asian", "string", "Averaging method, e.g. arithmetic.", AsianTerm.averaging_method),
        _spec("asian.averaging_kind", "asian", "string", "What is averaged, e.g. price/strike.", AsianTerm.averaging_kind),
        _spec("asian.n_observations", "asian", "integer", "Number of averaging observations.", AsianTerm.n_observations),
        # Snowball/autocallable terms (per-position mirror).
        _spec("snowball.initial_price", "snowball", "number", "Snowball initial fixing.", SnowballTerm.initial_price),
        _spec("snowball.ki_barrier", "snowball", "number", "Knock-in barrier level.", SnowballTerm.ki_barrier),
        _spec("snowball.coupon", "snowball", "number", "Snowball coupon/accrual rate.", SnowballTerm.coupon),
        _spec("snowball.start_date", "snowball", "date", "Snowball accrual start date.", SnowballTerm.start_date),
        _spec("snowball.knocked_in", "snowball", "boolean", "Whether the position has knocked in.", SnowballTerm.knocked_in),
        _spec("snowball.ki_observation", "snowball", "string", "KI observation style, e.g. daily.", SnowballTerm.ki_observation),
        _spec("snowball.payoff_kind", "snowball", "string", "Snowball payoff variant.", SnowballTerm.payoff_kind),
        # Cached nearest-barrier state (per-position, refreshed by barrier queries).
        _spec("barrier_state.nearest_barrier_kind", "barrier_state", "string", "Kind of the nearest barrier, e.g. KO/KI/UB/LB.", PositionBarrierState.nearest_barrier_kind),
        _spec("barrier_state.nearest_barrier_level", "barrier_state", "number", "Level of the nearest barrier.", PositionBarrierState.nearest_barrier_level),
        _spec("barrier_state.nearest_barrier_date", "barrier_state", "date", "Next dated barrier observation date.", PositionBarrierState.nearest_barrier_date),
        _spec("barrier_state.days_to_nearest", "barrier_state", "integer", "Days from the last refresh to the nearest dated barrier.", PositionBarrierState.days_to_nearest),
        _spec("barrier_state.last_computed_at", "barrier_state", "datetime", "When the barrier state was last refreshed.", PositionBarrierState.last_computed_at),
    ]
)

#: Term tables joined by ``query_positions`` behind the prefixed groups, used
#: to keep every historical ``<prefix>.<column>`` name queryable even when the
#: curated catalog lists only the meaningful ones.
_TERM_TABLES: tuple[tuple[str, Any], ...] = (
    ("option_core", OptionCoreTerm),
    ("single_barrier", SingleBarrierTerm),
    ("double_barrier", DoubleBarrierTerm),
    ("sharkfin", SharkfinTerm),
    ("asian", AsianTerm),
    ("snowball", SnowballTerm),
    ("barrier_state", PositionBarrierState),
)


def query_column_map() -> dict[str, Any]:
    """Selectable name -> SQLAlchemy column for ``query_positions``.

    Curated specs first; then legacy ``positions.<name>`` base aliases and
    ``<prefix>.<table column>`` aliases so every historically valid select or
    filter name keeps resolving even when it is not listed in the catalog.
    """
    mapping = {spec.name: spec.column for spec in POSITION_FIELD_SPECS}
    for name, _, _, column in _BASE_FIELDS:
        mapping.setdefault(f"positions.{name}", column)
    for prefix, model in _TERM_TABLES:
        for column in model.__table__.columns:
            mapping.setdefault(f"{prefix}.{column.name}", getattr(model, column.name))
    return mapping


def field_catalog_payload() -> dict[str, Any]:
    """JSON payload for the ``describe_position_fields`` tool."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for spec in POSITION_FIELD_SPECS:
        if not spec.listed:
            continue
        groups.setdefault(spec.group, []).append(
            {
                "name": spec.name,
                "value_type": spec.value_type,
                "description": spec.description,
            }
        )
    return {
        "source": "position_field_catalog",
        "select_only": True,
        "usage": (
            "query_positions returns ONLY the fields you name in select. Pick "
            "the fields the question needs from this catalog, then call "
            "query_positions(portfolio_id, select=[...], filter=[...]) with "
            "them. Term-table fields are null for positions of other product "
            "families; date fields are null when never booked."
        ),
        "groups": [
            {"group": group, "fields": fields} for group, fields in groups.items()
        ],
    }


def select_hint() -> str:
    """Compact group summary embedded in the query_positions select schema."""
    parts: dict[str, list[str]] = {}
    for spec in POSITION_FIELD_SPECS:
        if spec.listed:
            parts.setdefault(spec.group, []).append(spec.name)
    return "; ".join(
        f"{group}: {', '.join(names)}" for group, names in parts.items()
    )

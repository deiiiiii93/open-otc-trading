"""@tool wrappers for the positions domain.

Each wrapper is a thin LLM adapter: parse args, call services/domains/positions,
shape JSON. The wire shapes preserve the legacy ``langchain_tools.py`` payloads
(``source``/``filters``/``positions`` for ``get_positions``; ``found``/``results``
for ``get_latest_position_valuations``; counters for the import tools) so the
existing agent test suite continues to exercise this layer untouched.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import database
from app.schemas import PricingEnvironmentSnapshot
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.domains import booking as booking_svc
from app.services.domains import position_terms as terms_svc
from app.services.domains import positions as positions_svc
from app.services.domains import products as products_svc
from app.services.underlyings import resolve_underlying_currency

from ._shaping import (
    shape_position,
    shape_positions_view,
    shape_valuation_results,
)
from ._product_inputs import ToolPositionSnapshotSpec

TRADE_SHEET = positions_svc.TRADE_SHEET


class _PortfolioSnapshotInput(BaseModel):
    positions: list[ToolPositionSnapshotSpec] = Field(default_factory=list)
    market: PricingEnvironmentSnapshot = Field(
        default_factory=PricingEnvironmentSnapshot
    )


class GetPositionsInput(_PortfolioSnapshotInput):
    portfolio_id: int | None = None
    product_type: str | None = None
    status: str | None = "open"
    accounting_date: date | str | None = None
    effective_date_from: date | str | None = None
    effective_date_to: date | str | None = None
    effective_last_days: int | None = Field(default=None, ge=1)


class GetPositionSummariesInput(BaseModel):
    portfolio_id: int | None = None
    product_type: str | None = None
    status: str | None = "open"
    limit: int = Field(default=200, ge=1, le=1000)
    accounting_date: date | str | None = None
    effective_date_from: date | str | None = None
    effective_date_to: date | str | None = None
    effective_last_days: int | None = Field(default=None, ge=1)


class QueryPositionsNearBarrierInput(BaseModel):
    portfolio_id: int
    spot: dict[str, float]
    within_pct: float = Field(gt=0)
    status: str | None = "open"


class QuerySnowballKoFromSpotInput(BaseModel):
    portfolio_id: int
    spot: dict[str, float] = Field(default_factory=dict)
    within_pct: float = Field(default=5.0, gt=0)
    status: str | None = "open"
    as_of: date | str | None = None
    limit: int = Field(default=200, ge=1, le=1000)


class QueryPositionsInput(BaseModel):
    portfolio_id: int
    filter: list[dict[str, Any]] = Field(default_factory=list)
    select: list[str]
    order_by: tuple[str, str] | None = None
    limit: int = Field(default=200, ge=1, le=1000)


class QueryProductsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_class: str | None = None
    family: str | None = None
    product_family: str | None = None
    quantark_class: str | None = None
    underlying: str | None = None
    currency: str | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)


class GetProductDetailsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int


class QueryAutocallableObservationsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int
    role: str | None = None


_ALLOWED_PRODUCT_FAMILIES = {
    "asian",
    "autocallable",
    "barrier",
    "futures",
    "option",
    "package",
    "range_accrual",
    "sharkfin",
    "spot",
    "touch",
}


class ProductBookingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_class: str = "equity"
    product_family: str
    quantark_class: str | None = None
    underlying: str
    currency: str | None = None
    terms: dict[str, Any] = Field(default_factory=dict)
    components: list[dict[str, Any]] = Field(default_factory=list)
    display_name: str | None = None

    @model_validator(mode="after")
    def _resolve_currency(self):
        self.currency = resolve_underlying_currency(
            self.underlying, self.currency
        )
        return self

    @model_validator(mode="after")
    def _validate_family(self):
        # quantark_class is the authoritative taxonomy. The model routinely
        # misfiles it (e.g. "SnowballOption") or a colloquial name ("snowball")
        # into product_family; derive the canonical stored family from
        # quantark_class instead of rejecting, so direct bookings don't loop on
        # "Unsupported product family".
        if self.quantark_class:
            self.product_family = products_svc.product_family_for_quantark_class(
                self.quantark_class, components=self.components or None
            )
        if self.product_family not in _ALLOWED_PRODUCT_FAMILIES:
            raise ValueError(f"Unsupported product family: {self.product_family}")
        return self


class BookPositionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: int
    product: ProductBookingInput
    quantity: float
    entry_price: float = 0.0
    status: str = "open"
    source_trade_id: str | None = None
    source_row: int | None = None
    trade_effective_date: datetime | date | str | None = None
    engine_name: str = "BlackScholesEngine"


class PositionIdsInput(BaseModel):
    position_ids: list[int]


class PositionIdInput(BaseModel):
    position_id: int


class SnowballKoScheduleInput(BaseModel):
    position_id: int
    from_date: date | str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class LatestValuationsInput(BaseModel):
    portfolio_id: int
    limit: int = Field(default=50, ge=1, le=500)


class ImportPositionsInput(BaseModel):
    portfolio_id: int
    xlsx_path: str
    sheet_name: str = TRADE_SHEET


class PositionLifecycleReferenceInput(BaseModel):
    position_id: int | None = Field(
        default=None,
        description="Position.id to update. Prefer this when known.",
    )
    source_trade_id: str | None = Field(
        default=None,
        description=(
            "Optional source trade id lookup. If this is not unique, pass "
            "portfolio_id or use position_id."
        ),
    )
    portfolio_id: int | None = Field(
        default=None,
        description="Optional portfolio guard used with position_id or source_trade_id.",
    )

    @model_validator(mode="after")
    def _requires_identifier(self):
        if self.position_id is None and not (self.source_trade_id or "").strip():
            raise ValueError("position_id or source_trade_id is required")
        return self


class ClosePositionInput(PositionLifecycleReferenceInput):
    reason: str | None = Field(default=None, description="Why the position is closed.")
    closed_at: date | str | None = Field(
        default=None, description="Optional close date, ISO date preferred."
    )


class SettlePositionInput(PositionLifecycleReferenceInput):
    settlement_date: date | str | None = Field(
        default=None, description="Optional settlement date, ISO date preferred."
    )
    settlement_amount: float | None = Field(default=None)
    currency: str | None = Field(default=None)
    reason: str | None = Field(default=None)


class MarkKnockoutInput(PositionLifecycleReferenceInput):
    observation_date: date | str | None = Field(
        default=None, description="Optional KO observation date, ISO date preferred."
    )
    observed_spot: float | None = Field(default=None)
    ko_level: float | None = Field(default=None)
    payoff: float | None = Field(default=None)
    reason: str | None = Field(default=None)


class CancelLifecycleEventInput(BaseModel):
    lifecycle_event_id: int = Field(description="PositionLifecycleEvent.id to cancel.")
    position_id: int | None = Field(
        default=None,
        description="Optional Position.id guard. If omitted, event id resolves it.",
    )
    source_trade_id: str | None = Field(
        default=None,
        description="Optional source trade id guard for the event's position.",
    )
    portfolio_id: int | None = Field(
        default=None,
        description="Optional portfolio guard used with position_id or source_trade_id.",
    )
    reason: str | None = Field(default=None, description="Why the event is cancelled.")


class GenerateAsianFixingScheduleInput(BaseModel):
    position_id: int | None = Field(
        default=None, description="Position.id of the Asian option."
    )
    source_trade_id: str | None = Field(
        default=None, description="Optional source trade id guard."
    )
    portfolio_id: int | None = Field(
        default=None, description="Optional portfolio guard."
    )


class CaptureAsianFixingsInput(BaseModel):
    position_id: int = Field(description="Position.id of the Asian option.")
    portfolio_id: int | None = Field(
        default=None, description="Optional portfolio guard; raises if it mismatches."
    )
    as_of: date | str | None = Field(
        default=None,
        description="Capture fixings on/before this date (ISO date; default today).",
    )


def _parse_date(value: date | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _clean_event_data(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in data.items()
        if value is not None and value != ""
    }


def _shape_lifecycle_update(
    update: positions_svc.PositionLifecycleUpdate,
) -> dict[str, Any]:
    event = update.event
    return {
        "ok": True,
        "position": shape_position(update.position, include_raw_terms=False),
        "lifecycle_event": {
            "id": event.id,
            "position_id": event.position_id,
            "event_type": event.event_type,
            "event_data": event.event_data or {},
            "old_status": event.old_status,
            "new_status": event.new_status,
            "actor": event.actor,
            "created_at": event.created_at.isoformat() if event.created_at else None,
            "cancelled_at": event.cancelled_at.isoformat() if event.cancelled_at else None,
            "cancelled_by": event.cancelled_by,
            "cancellation_reason": event.cancellation_reason,
        },
    }


def _product_family_terms(
    session: Any,
    position_ids: list[int],
    family_key: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for position_id in position_ids:
        try:
            details = products_svc.product_details_for_position(session, position_id)
        except LookupError:
            continue
        terms = details.get("family_terms", {}).get(family_key)
        if not terms:
            continue
        rows.append(
            {
                "position_id": position_id,
                "product_id": details["product"]["id"],
                **terms,
            }
        )
    return rows


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_positions", args_schema=GetPositionsInput)
def get_positions_tool(
    positions: list[ToolPositionSnapshotSpec] | None = None,
    market: PricingEnvironmentSnapshot = PricingEnvironmentSnapshot(),
    portfolio_id: int | None = None,
    product_type: str | None = None,
    status: str | None = "open",
    accounting_date: date | str | None = None,
    effective_date_from: date | str | None = None,
    effective_date_to: date | str | None = None,
    effective_last_days: int | None = None,
) -> dict[str, Any]:
    """Return normalized portfolio positions for agent inspection."""
    kwargs = {
        "product_type": product_type,
        "status": status,
        "accounting_date": accounting_date,
        "effective_date_from": effective_date_from,
        "effective_date_to": effective_date_to,
        "effective_last_days": effective_last_days,
    }
    if positions:
        rows = [position.to_legacy_payload() for position in positions]
        view = positions_svc.get_positions_view_from_snapshot(rows, **kwargs)
        return shape_positions_view(
            view,
            source="provided_context",
            market=market,
            include_raw_terms=False,
        )
    view = positions_svc.get_positions_view(portfolio_id=portfolio_id, **kwargs)
    return shape_positions_view(
        view,
        source="database",
        market=market,
        include_raw_terms=False,
    )


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_position_summaries", args_schema=GetPositionSummariesInput)
def get_position_summaries_tool(
    portfolio_id: int | None = None,
    product_type: str | None = None,
    status: str | None = "open",
    limit: int = 200,
    accounting_date: date | str | None = None,
    effective_date_from: date | str | None = None,
    effective_date_to: date | str | None = None,
    effective_last_days: int | None = None,
) -> dict[str, Any]:
    """Return compact, term-promoted position rows without raw product_kwargs."""
    view = positions_svc.get_positions_view(
        portfolio_id=portfolio_id,
        product_type=product_type,
        status=status,
        accounting_date=accounting_date,
        effective_date_from=effective_date_from,
        effective_date_to=effective_date_to,
        effective_last_days=effective_last_days,
    )
    rows = positions_svc.position_summaries(view.positions, limit=limit)
    return {
        "source": "database",
        "filters": view.filters,
        "returned_count": len(rows),
        "total_count": len(view.positions),
        "portfolio_total_count": view.portfolio_total_count,
        "portfolio_counts_by_product_type": view.portfolio_counts_by_product_type,
        "counts_by_product_type": view.counts_by_product_type,
        "missing_trade_effective_date_count": view.missing_effective_date_count,
        "positions": rows,
    }


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("query_snowball_ko_from_spot", args_schema=QuerySnowballKoFromSpotInput)
def query_snowball_ko_from_spot_tool(
    portfolio_id: int,
    spot: dict[str, float] | None = None,
    within_pct: float = 5.0,
    status: str | None = "open",
    as_of: date | str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """List Snowball positions whose next KO level is near current spot."""
    database.init_db()
    with database.SessionLocal() as session:
        return terms_svc.query_snowball_ko_from_spot(
            session,
            portfolio_id=portfolio_id,
            spot=spot or {},
            within_pct=within_pct,
            status=status,
            as_of=_parse_date(as_of),
            limit=limit,
        )


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("query_positions_near_barrier", args_schema=QueryPositionsNearBarrierInput)
def query_positions_near_barrier_tool(
    portfolio_id: int,
    spot: dict[str, float],
    within_pct: float,
    status: str | None = "open",
) -> dict[str, Any]:
    """Query structured barrier term tables for positions close to supplied spots."""
    database.init_db()
    with database.SessionLocal() as session:
        terms_svc.refresh_position_barrier_state(session, portfolio_id=portfolio_id)
        rows = terms_svc.query_positions_near_barrier(
            session,
            portfolio_id=portfolio_id,
            spot=spot,
            within_pct=within_pct,
            status=status,
        )
    return {
        "source": "structured_position_terms",
        "portfolio_id": portfolio_id,
        "within_pct": within_pct,
        "returned_count": len(rows),
        "positions": rows,
    }


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("query_positions", args_schema=QueryPositionsInput)
def query_positions_tool(
    portfolio_id: int,
    select: list[str],
    filter: list[dict[str, Any]] | None = None,
    order_by: tuple[str, str] | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Structured filter/query escape hatch over allowlisted position term columns."""
    database.init_db()
    with database.SessionLocal() as session:
        rows = terms_svc.query_positions(
            session,
            portfolio_id=portfolio_id,
            filters=filter or [],
            select=select,
            order_by=order_by,
            limit=limit,
        )
    return {
        "source": "structured_position_terms",
        "portfolio_id": portfolio_id,
        "returned_count": len(rows),
        "positions": rows,
    }


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("query_products", args_schema=QueryProductsInput)
def query_products_tool(
    asset_class: str | None = None,
    family: str | None = None,
    product_family: str | None = None,
    quantark_class: str | None = None,
    underlying: str | None = None,
    currency: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """Query normalized product root rows using allowlisted filters."""
    database.init_db()
    with database.SessionLocal() as session:
        rows = products_svc.query_products(
            session,
            asset_class=asset_class,
            family=family,
            product_family=product_family,
            quantark_class=quantark_class,
            underlying=underlying,
            currency=currency,
            offset=offset,
            limit=limit,
        )
    return {
        "source": "products",
        "filters": {
            "asset_class": asset_class,
            "family": family,
            "product_family": product_family,
            "quantark_class": quantark_class,
            "underlying": underlying,
            "currency": currency,
            "offset": offset,
            "limit": limit,
        },
        "returned_count": len(rows),
        "products": rows,
    }


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_product_details", args_schema=GetProductDetailsInput)
def get_product_details_tool(product_id: int) -> dict[str, Any]:
    """Read one normalized product with family details, schedules, and components."""
    database.init_db()
    with database.SessionLocal() as session:
        return products_svc.get_product_details(session, product_id)


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool(
    "query_autocallable_observations",
    args_schema=QueryAutocallableObservationsInput,
)
def query_autocallable_observations_tool(
    product_id: int,
    role: str | None = None,
) -> dict[str, Any]:
    """Read normalized autocallable observation rows for one product."""
    database.init_db()
    with database.SessionLocal() as session:
        rows = products_svc.query_autocallable_observations(
            session,
            product_id=product_id,
            role=role,
        )
    return {
        "source": "equity_autocallable_observations",
        "product_id": product_id,
        "role": role,
        "returned_count": len(rows),
        "observations": rows,
    }


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("book_position", args_schema=BookPositionInput)
def book_position_tool(
    portfolio_id: int,
    product: ProductBookingInput | dict[str, Any],
    quantity: float,
    entry_price: float = 0.0,
    status: str = "open",
    source_trade_id: str | None = None,
    source_row: int | None = None,
    trade_effective_date: datetime | date | str | None = None,
    engine_name: str = "BlackScholesEngine",
) -> dict[str, Any]:
    """Create a normalized product and book a position against it."""
    if isinstance(product, dict):
        product = ProductBookingInput.model_validate(product)

    parsed_effective: datetime | None = None
    if isinstance(trade_effective_date, datetime):
        parsed_effective = trade_effective_date
    elif isinstance(trade_effective_date, date):
        parsed_effective = datetime.combine(trade_effective_date, datetime.min.time())
    elif trade_effective_date:
        parsed_effective = datetime.fromisoformat(str(trade_effective_date))

    product_spec = booking_svc.ProductBookingSpec(
        asset_class=product.asset_class,
        product_family=product.product_family,
        quantark_class=product.quantark_class,
        underlying=product.underlying,
        currency=product.currency,
        terms=product.terms,
        components=product.components,
        display_name=product.display_name,
        source_payload={"tool_name": "book_position"},
    )
    request = booking_svc.BookingRequest(
        portfolio_id=portfolio_id,
        product=product_spec,
        quantity=quantity,
        entry_price=entry_price,
        status=status,
        source_trade_id=source_trade_id,
        source_row=source_row,
        mapping_status="supported",
        mapping_error=None,
        source_payload={"tool_name": "book_position"},
        rfq_id=None,
        rfq_quote_version_id=None,
        trade_effective_date=parsed_effective,
        engine_name=engine_name,
        engine_kwargs={},
        actor="agent",
        source="agent_tool",
    )
    database.init_db()
    with database.SessionLocal() as session:
        position = booking_svc.book_position(
            session,
            request,
            reuse_product=True,
        )
        row = shape_position(position, include_raw_terms=False)
        row["product_id"] = position.product_id
        product_row = products_svc.product_summary(position.product)
        session.commit()
    return {
        "ok": True,
        "source": "product_booking",
        "position": row,
        "product": product_row,
    }


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_option_core_terms", args_schema=PositionIdsInput)
def get_option_core_terms_tool(position_ids: list[int]) -> dict[str, Any]:
    """Read universal option core terms through each position's product."""
    database.init_db()
    with database.SessionLocal() as session:
        terms = _product_family_terms(session, position_ids, "option")
    return {"source": "products", "terms": terms}


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_barrier_terms", args_schema=PositionIdsInput)
def get_barrier_terms_tool(position_ids: list[int]) -> dict[str, Any]:
    """Read barrier-like terms through each position's product."""
    database.init_db()
    with database.SessionLocal() as session:
        terms = (
            _product_family_terms(session, position_ids, "barrier")
            + _product_family_terms(session, position_ids, "autocallable")
            + _product_family_terms(session, position_ids, "touch")
            + _product_family_terms(session, position_ids, "sharkfin")
        )
    return {"source": "products", "terms": terms}


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_sharkfin_terms", args_schema=PositionIdsInput)
def get_sharkfin_terms_tool(position_ids: list[int]) -> dict[str, Any]:
    """Read sharkfin-specific terms through each position's product."""
    database.init_db()
    with database.SessionLocal() as session:
        terms = _product_family_terms(session, position_ids, "sharkfin")
    return {"source": "products", "terms": terms}


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_asian_schedule", args_schema=PositionIdInput)
def get_asian_schedule_tool(position_id: int) -> dict[str, Any]:
    """Read Asian averaging schedule rows through a position's product."""
    database.init_db()
    with database.SessionLocal() as session:
        details = products_svc.product_details_for_position(session, position_id)
        schedule = details.get("observations", {}).get("asian", [])
    return {"source": "products", "position_id": position_id, "schedule": schedule}


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_snowball_terms", args_schema=PositionIdsInput)
def get_snowball_terms_tool(position_ids: list[int]) -> dict[str, Any]:
    """Read Snowball/autocallable terms through each position's product."""
    database.init_db()
    with database.SessionLocal() as session:
        terms = _product_family_terms(session, position_ids, "autocallable")
    return {"source": "products", "terms": terms}


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_snowball_ko_schedule", args_schema=SnowballKoScheduleInput)
def get_snowball_ko_schedule_tool(
    position_id: int,
    from_date: date | str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Read Snowball KO observation schedule rows through a position's product."""
    parsed_from = _parse_date(from_date)
    database.init_db()
    with database.SessionLocal() as session:
        details = products_svc.product_details_for_position(session, position_id)
        rows = [
            row
            for row in details.get("observations", {}).get("autocallable", [])
            if row.get("observation_role") == "ko"
        ]
    if parsed_from is not None:
        rows = [
            row
            for row in rows
            if row.get("observation_date") is None
            or _parse_date(row.get("observation_date")) >= parsed_from
        ]
    schedule = rows[:limit]
    return {"source": "products", "position_id": position_id, "schedule": schedule}


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_latest_position_valuations", args_schema=LatestValuationsInput)
def get_latest_position_valuations_tool(
    portfolio_id: int, limit: int = 50
) -> dict[str, Any]:
    """Read the latest completed stored valuation results for a persisted portfolio."""
    database.init_db()
    with database.SessionLocal() as session:
        run = positions_svc.latest_valuation_run(
            portfolio_id=portfolio_id, session=session
        )
        if run is None:
            return shape_valuation_results(portfolio_id, None, [], total_count=0)
        all_results = sorted(run.results, key=lambda r: r.id)
        return shape_valuation_results(
            portfolio_id, run, all_results[:limit], total_count=len(all_results)
        )


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("close_position", args_schema=ClosePositionInput)
def close_position_tool(
    position_id: int | None = None,
    source_trade_id: str | None = None,
    portfolio_id: int | None = None,
    reason: str | None = None,
    closed_at: date | str | None = None,
) -> dict[str, Any]:
    """Record a close lifecycle event and mark the position closed."""
    update = positions_svc.create_lifecycle_event(
        position_id=position_id,
        source_trade_id=source_trade_id,
        portfolio_id=portfolio_id,
        event_type="close",
        event_data=_clean_event_data(
            {
                "reason": reason,
                "closed_at": closed_at,
                "tool_name": "close_position",
            }
        ),
        actor="agent",
    )
    return _shape_lifecycle_update(update)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("settle_position", args_schema=SettlePositionInput)
def settle_position_tool(
    position_id: int | None = None,
    source_trade_id: str | None = None,
    portfolio_id: int | None = None,
    settlement_date: date | str | None = None,
    settlement_amount: float | None = None,
    currency: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Record a settlement lifecycle event and mark the position closed."""
    update = positions_svc.create_lifecycle_event(
        position_id=position_id,
        source_trade_id=source_trade_id,
        portfolio_id=portfolio_id,
        event_type="settle",
        event_data=_clean_event_data(
            {
                "settlement_date": settlement_date,
                "settlement_amount": settlement_amount,
                "currency": currency,
                "reason": reason,
                "tool_name": "settle_position",
            }
        ),
        actor="agent",
    )
    return _shape_lifecycle_update(update)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("mark_knockout", args_schema=MarkKnockoutInput)
def mark_knockout_tool(
    position_id: int | None = None,
    source_trade_id: str | None = None,
    portfolio_id: int | None = None,
    observation_date: date | str | None = None,
    observed_spot: float | None = None,
    ko_level: float | None = None,
    payoff: float | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Record a knock_out lifecycle event and mark the position closed."""
    update = positions_svc.create_lifecycle_event(
        position_id=position_id,
        source_trade_id=source_trade_id,
        portfolio_id=portfolio_id,
        event_type="knock_out",
        event_data=_clean_event_data(
            {
                "observation_date": observation_date,
                "observed_spot": observed_spot,
                "ko_level": ko_level,
                "payoff": payoff,
                "reason": reason,
                "tool_name": "mark_knockout",
            }
        ),
        actor="agent",
    )
    return _shape_lifecycle_update(update)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("cancel_lifecycle_event", args_schema=CancelLifecycleEventInput)
def cancel_lifecycle_event_tool(
    lifecycle_event_id: int,
    position_id: int | None = None,
    source_trade_id: str | None = None,
    portfolio_id: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Cancel a persisted lifecycle event and recompute position status."""
    update = positions_svc.cancel_lifecycle_event(
        lifecycle_event_id=lifecycle_event_id,
        position_id=position_id,
        source_trade_id=source_trade_id,
        portfolio_id=portfolio_id,
        reason=reason,
        actor="agent",
    )
    return _shape_lifecycle_update(update)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool(
    "generate_asian_fixing_schedule",
    args_schema=GenerateAsianFixingScheduleInput,
)
def generate_asian_fixing_schedule_tool(
    position_id: int | None = None,
    source_trade_id: str | None = None,
    portfolio_id: int | None = None,
) -> dict[str, Any]:
    """Plant informational `fixing` lifecycle events from an Asian option's
    averaging schedule. Idempotent: re-running cancels prior active fixing
    events first, so it is safe to refresh after a reschedule."""
    count = positions_svc.generate_asian_fixing_schedule(
        position_id=position_id,
        source_trade_id=source_trade_id,
        portfolio_id=portfolio_id,
        actor="agent",
    )
    return {"position_id": position_id, "events_created": count}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("capture_asian_fixings", args_schema=CaptureAsianFixingsInput)
def capture_asian_fixings_tool(
    position_id: int,
    portfolio_id: int | None = None,
    as_of: date | str | None = None,
) -> dict[str, Any]:
    """Snapshot the close price for every due (past, uncaptured) Asian fixing
    into the position's observation records. Idempotent; never overwrites an
    already-captured fixing."""
    captured = positions_svc.capture_due_asian_fixings(
        None,
        position_id,
        portfolio_id=portfolio_id,
        as_of=_parse_date(as_of),
    )
    return {"position_id": position_id, "captured": captured}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("import_otc_positions", args_schema=ImportPositionsInput)
def import_otc_positions_tool(
    portfolio_id: int, xlsx_path: str, sheet_name: str = TRADE_SHEET
) -> dict[str, Any]:
    """Import OTC trade workbook rows into persisted Portfolio/Position records."""
    batch = positions_svc.import_from_xlsx(
        portfolio_id=portfolio_id, xlsx_path=xlsx_path, sheet=sheet_name
    )
    return {
        "import_batch_id": batch.id,
        "portfolio_id": portfolio_id,
        "row_count": batch.row_count,
        "imported_count": batch.imported_count,
        "supported_count": batch.supported_count,
        "unsupported_count": batch.unsupported_count,
        "error_count": batch.error_count,
        "status": batch.status,
    }



from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.currency_codes import ISO_4217_CODES, normalize_currency
from app.services.thread_access import is_reserved_internal_thread_source
from app.services.underlyings import resolve_underlying_currency


class AgentThreadCreate(BaseModel):
    title: str = "New research thread"
    character: Literal["trader", "risk_manager", "high_board"] = "trader"
    # "desk" = normal Agent Desk thread; "workflow_builder" = isolated builder
    # conversation, hidden from the Agent Desk thread list.
    source: str = "desk"

    @field_validator("source")
    @classmethod
    def _reject_reserved_internal_source(cls, source: str) -> str:
        if is_reserved_internal_thread_source(source):
            raise ValueError("Thread source is reserved for internal server use")
        return source


class AgentThreadUpdate(BaseModel):
    title: str | None = None
    report_currency: str | None = None

    @model_validator(mode="after")
    def _validate_report_currency(self) -> "AgentThreadUpdate":
        rc = self.report_currency
        if rc is not None and rc != "by_position":
            norm = normalize_currency(rc)
            if norm not in ISO_4217_CODES:
                raise ValueError(f"Invalid report currency: {rc!r}")
            self.report_currency = norm
        return self


class AgentThreadFork(BaseModel):
    title: str | None = None


ConfirmationMode = Literal["implicit", "explicit", "destructive"]
LoadedCompleteness = Literal["complete", "paginated", "partial", "empty"]
AgentEnvelopeLiteral = Literal[
    "pet_page", "pet_diagnostic", "desk_workflow", "desk_async"
]


class LoadedContext(BaseModel):
    """Page-side declaration of how much of the page's data the agent can see.

    ``completeness == "complete"`` means the agent can answer count/aggregate
    queries from ``AgentPageContext.snapshot`` without escalating.
    ``paginated`` means only a window is loaded; use ``query_ref`` or escalate
    to a domain read. ``partial`` and ``empty`` always trigger escalation for
    non-trivial questions.
    """

    completeness: LoadedCompleteness
    visible_count: int | None = None
    total_count: int | None = None
    query_ref: str | None = Field(
        default=None,
        description=(
            "Opaque reference the agent can pass back to a query tool to "
            "materialize the full set."
        ),
    )


class PageAction(BaseModel):
    """A page-declared backend action the agent may invoke on the user's behalf.

    ``backend_endpoint`` is informational — the agent invokes the corresponding
    domain tool, which routes through the same service function the UI button
    uses. ``confirmation`` gates whether the pet executes the action directly
    (under YOLO) or asks first.
    """

    name: str
    required_ids: list[str] = Field(default_factory=list)
    confirmation: ConfirmationMode = "explicit"
    backend_endpoint: str = ""


class AgentPageContext(BaseModel):
    """Typed contract for the page-context payload the frontend sends per message.

    Phase 2 additions: ``loaded_context``, ``actions``. The legacy ``chips`` and
    ``path`` fields stay accepted (Optional) for backward compatibility while
    pages migrate in P2.3 + Phase 3.
    """

    route: str
    title: str
    entity_ids: dict[str, int | str | None] = Field(default_factory=dict)
    snapshot: dict[str, Any] = Field(default_factory=dict)
    # Phase 2 additions (optional during migration):
    loaded_context: LoadedContext | None = None
    actions: list[PageAction] = Field(default_factory=list)
    # Legacy (deprecated — remove in Phase 3):
    path: str | None = "/"
    chips: list[str] = Field(default_factory=list)


class AgentContextUsage(BaseModel):
    bytes: int
    estimated_tokens: int
    chip_count: int
    snapshot_key_count: int
    entity_id_count: int
    warning_level: Literal["none", "large", "huge"] = "none"
    computed_at: datetime | None = None


class AgentAssetOut(BaseModel):
    id: str
    kind: Literal["file", "image", "table", "chart", "json", "markdown", "html"]
    title: str
    mime_type: str | None = None
    url: str | None = None
    path: str | None = None
    data: Any | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentActionProposal(BaseModel):
    id: str
    tool_name: str
    label: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = True
    status: Literal["pending", "confirmed", "dismissed", "failed"] = "pending"
    persona: Literal["trader", "risk_manager", "high_board"] | None = None
    risk_level: Literal["read", "write", "irreversible"] | None = None
    async_task_id: int | None = None
    task_id: int | None = None
    task_kind: str | None = None
    task_status: str | None = None
    task_progress_current: int | None = None
    task_progress_total: int | None = None
    task_message: str | None = None
    source_meta: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_type_field(cls, data: Any) -> Any:
        """Map the legacy `type` field to `tool_name` for old thread history."""
        if isinstance(data, dict) and "tool_name" not in data and "type" in data:
            data = {**data, "tool_name": data["type"]}
        return data


class AgentModelSelection(BaseModel):
    channel: str
    provider: str
    model: str

    @model_validator(mode="before")
    @classmethod
    def _backcompat_default_channel(cls, data: Any) -> Any:
        """Legacy thread-history rows have {provider, model} only."""
        if isinstance(data, dict) and "channel" not in data:
            return {**data, "channel": "zenmux"}
        return data


class AgentModelOption(AgentModelSelection):
    label: str
    description: str | None = None
    is_default: bool = False
    tags: list[str] = Field(default_factory=list)


class AgentChannelOut(BaseModel):
    name: str
    label: str
    type: Literal["zenmux", "openai_compatible"]
    healthy: bool = True
    models: list[AgentModelOption] = Field(default_factory=list)


class AgentModelConfigOut(BaseModel):
    enabled: bool
    active: AgentModelSelection
    channels: list[AgentChannelOut] = Field(default_factory=list)


class AgentRegistryModelOut(BaseModel):
    id: str
    provider: str
    label: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    protocol: str | None = None


class AgentRegistryChannelOut(BaseModel):
    name: str
    label: str
    type: Literal["zenmux", "openai_compatible"]
    base_url: str
    anthropic_base_url: str | None = None
    api_key_env: str | None = None
    healthy: bool = True
    models: list[AgentRegistryModelOut] = Field(default_factory=list)


class AgentRegistryDefaultOut(BaseModel):
    channel: str
    model: str


class AgentRegistryOut(BaseModel):
    default: AgentRegistryDefaultOut
    channels: list[AgentRegistryChannelOut] = Field(default_factory=list)


class ModelWriteIn(BaseModel):
    id: str
    provider: str
    label: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    protocol: str | None = None


class ChannelWriteIn(BaseModel):
    name: str
    label: str
    type: Literal["zenmux", "openai_compatible"]
    base_url: str
    anthropic_base_url: str | None = None
    api_key_env: str | None = None
    models: list[ModelWriteIn] = Field(default_factory=list)


class DefaultWriteIn(BaseModel):
    channel: str
    model: str


class AgentMessageCreate(BaseModel):
    content: str
    character: Literal["auto", "trader", "risk_manager", "high_board"] = "auto"
    page_context: AgentPageContext | None = None
    context_usage: AgentContextUsage | None = None
    accounting_date: date | None = None
    model: AgentModelSelection | None = None
    # Execution mode (canonical). "interactive" surfaces HITL; "auto" auto-clears
    # HITL but the model may still ask via reply cards; "yolo" is fully headless
    # (no HITL, no deferral). When omitted, the deprecated ``yolo_mode`` boolean
    # maps to auto/interactive for back-compat.
    mode: Literal["interactive", "auto", "yolo"] | None = None
    yolo_mode: bool = False
    # Phase 2: optional, defaulted by the endpoint based on UI origin.
    envelope: AgentEnvelopeLiteral | None = None
    # Phase 2.7: set to True when the user has already seen a cost-preview
    # warning for the previous turn and is approving the re-run. Threaded
    # into configurable.confirmed_cost_preview so the gate lets the tool
    # past the long-running estimator check.
    confirmed_cost_preview: bool = False


class StartGoalRequest(BaseModel):
    """Body for ``POST /api/chat/threads/{thread_id}/goal`` (the /goal command)."""
    goal_text: str
    mode: Literal["interactive", "auto", "yolo"] = "auto"


class AgentMessageOut(BaseModel):
    id: int
    role: str
    character: str | None = None
    content: str
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentThreadOut(BaseModel):
    id: int
    title: str
    character: str
    report_currency: str = "by_position"
    source: str = "desk"
    created_at: datetime
    updated_at: datetime
    messages: list[AgentMessageOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class MemoryEntryOut(BaseModel):
    id: int
    namespace: str
    content: str
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = {"from_attributes": True}


class PricingEnvironmentSnapshot(BaseModel):
    valuation_date: datetime = Field(default_factory=datetime.utcnow)
    spot: float = 100.0
    volatility: float = 0.20
    rate: float = 0.03
    dividend_yield: float = 0.0
    asset_name: str | None = None
    currency: str = "USD"
    day_count_convention: str = "ACT_365"
    bus_days_in_year: int = 252


class PricingPreviewRequest(BaseModel):
    product_type: str
    product_kwargs: dict[str, Any] = Field(default_factory=dict)
    engine_name: str = "BlackScholesEngine"
    engine_kwargs: dict[str, Any] = Field(default_factory=dict)
    market: PricingEnvironmentSnapshot = Field(default_factory=PricingEnvironmentSnapshot)
    compute_greeks: bool = True


class PricingGreeks(BaseModel):
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float
    rho_q: float


class PricingPreviewOut(BaseModel):
    ok: bool
    price: float
    engine: str
    product_type: str
    greeks: PricingGreeks | None = None
    greeks_error: str | None = None
    error: str | None = None


class RFQUnknownSpecIn(BaseModel):
    field_path: str = "strike"
    lower_bound: float = 50.0
    upper_bound: float = 150.0
    initial_guess: float | None = 100.0
    display_label: str | None = None


class RFQTargetIn(BaseModel):
    label: Literal["price", "premium", "reoffer"] = "price"
    value: float = 10.0


class RFQEngineSpecIn(BaseModel):
    engine_name: str = "BlackScholesEngine"
    params_type: str | None = None
    params_kwargs: dict[str, Any] = Field(default_factory=dict)
    method: Any | None = None
    engine_kwargs: dict[str, Any] = Field(default_factory=dict)


class RFQRequestDraft(BaseModel):
    client_name: str = "Demo Client"
    underlying: str = "CSI500"
    side: Literal["buy", "sell"] = "buy"
    quantity: float = 1.0
    quote_mode: Literal["solve", "price"] = "solve"
    product_type: str = "EuropeanVanillaOption"
    product_kwargs: dict[str, Any] = Field(
        default_factory=lambda: {
            "strike": 100.0,
            "option_type": "CALL",
            "maturity": 1.0,
            "contract_multiplier": 1.0,
        }
    )
    product: dict[str, Any] | None = None
    market: PricingEnvironmentSnapshot = Field(
        default_factory=PricingEnvironmentSnapshot
    )
    engine_spec: RFQEngineSpecIn = Field(default_factory=RFQEngineSpecIn)
    unknown: RFQUnknownSpecIn = Field(default_factory=RFQUnknownSpecIn)
    target: RFQTargetIn = Field(default_factory=RFQTargetIn)
    notes: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_legacy_product_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        product = data.get("product")
        if not isinstance(product, dict):
            return data
        terms = dict(product.get("terms") or {})
        components = list(product.get("components") or [])
        if components and "components" not in terms:
            terms["components"] = components
        derived = dict(data)
        product_currency = product.get("currency")
        if product_currency is not None:
            market = dict(derived.get("market") or {})
            market.setdefault("currency", product_currency)
            derived["market"] = market
        derived.setdefault("underlying", product.get("underlying") or "CSI500")
        derived.setdefault(
            "product_type",
            product.get("quantark_class") or product.get("product_family") or "EuropeanVanillaOption",
        )
        derived.setdefault("product_kwargs", terms)
        return derived

    @model_validator(mode="after")
    def _validate_currency(self) -> "RFQRequestDraft":
        currency = normalize_currency(self.market.currency)
        if currency not in ISO_4217_CODES:
            raise ValueError(f"Invalid RFQ currency: {self.market.currency!r}")
        self.market.currency = currency
        return self


class RFQQuoteDraft(BaseModel):
    quote_id: str
    status: str
    field_path: str
    field_label: str
    solved_value: float
    target_label: str
    target_value: float
    achieved_price: float
    residual: float
    engine_summary: dict[str, Any] = Field(default_factory=dict)
    request_summary: dict[str, Any] = Field(default_factory=dict)
    client_response: str


class TrySolveMarketIn(BaseModel):
    pricing_parameter_profile_id: int | None = None
    market_data_profile_id: int | None = None
    valuation_date: datetime | None = None
    spot: float | None = None
    volatility: float | None = None
    rate: float | None = None
    dividend_yield: float | None = None
    day_count_convention: str | None = None
    bus_days_in_year: int | None = None
    calendar: str | None = None


class TrySolveQuoteRequestIn(BaseModel):
    quote_field_key: str = "premium_rate"
    target_label: Literal["price", "premium", "premium %", "reoffer"] = "price"
    target_value: float = 0.0
    quote_value_mode: Literal["absolute", "percentage"] = "absolute"
    lower_bound: float | None = None
    upper_bound: float | None = None
    initial_guess: float | None = None


class TrySolveRowIn(BaseModel):
    row_id: str
    source: Literal["manual", "excel"] = "manual"
    product_key: str
    source_sheet: str | None = None
    source_row: int | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    raw_values: dict[str, Any] = Field(default_factory=dict)
    market: TrySolveMarketIn = Field(default_factory=TrySolveMarketIn)
    quote_request: TrySolveQuoteRequestIn = Field(
        default_factory=TrySolveQuoteRequestIn
    )


class TrySolveRowOut(TrySolveRowIn):
    product_label: str
    status: str
    diagnostics: list[str] = Field(default_factory=list)
    quantark_product_type: str | None = None
    engine_name: str | None = None
    solved_value: float | None = None
    model_price: float | None = None
    residual: float | None = None
    executable_terms: dict[str, Any] | None = None


class TrySolveBatchOut(BaseModel):
    batch_id: str
    rows: list[TrySolveRowOut]
    summary: dict[str, Any] = Field(default_factory=dict)


class TrySolveValidateRequest(BaseModel):
    row: TrySolveRowIn


class TrySolveSolveRequest(BaseModel):
    row: TrySolveRowIn


class TrySolveBatchSolveRequest(BaseModel):
    rows: list[TrySolveRowIn]


class TrySolveExportRequest(BaseModel):
    rows: list[TrySolveRowOut]
    scope: Literal["all", "selected", "solved", "errors"] = "all"
    selected_row_ids: list[str] = Field(default_factory=list)


class TrySolveExportOut(BaseModel):
    filename: str
    url: str
    row_count: int
    scope: str


class RFQQuoteVersionOut(BaseModel):
    id: int
    rfq_id: int
    version: int
    quote_mode: str
    status: str
    request_payload: dict[str, Any]
    quote_payload: dict[str, Any]
    error: str | None = None
    created_by: str
    approved_by: str | None = None
    approved_at: datetime | None = None
    released_at: datetime | None = None
    valid_until: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class RFQOut(BaseModel):
    id: int
    client_name: str
    channel: str
    status: str
    request_payload: dict[str, Any]
    quote_payload: dict[str, Any]
    approved_response: str | None = None
    quote_versions: list[RFQQuoteVersionOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RFQChatRequest(BaseModel):
    client_name: str = "Demo Client"
    message: str


class RFQDraftCreate(RFQRequestDraft):
    channel: str = "desk"


class RFQDraftUpdate(BaseModel):
    client_name: str | None = None
    underlying: str | None = None
    side: Literal["buy", "sell"] | None = None
    quantity: float | None = None
    quote_mode: Literal["solve", "price"] | None = None
    product_type: str | None = None
    product_kwargs: dict[str, Any] | None = None
    market: PricingEnvironmentSnapshot | None = None
    engine_spec: RFQEngineSpecIn | None = None
    unknown: RFQUnknownSpecIn | None = None
    target: RFQTargetIn | None = None
    notes: str | None = None


class RFQDraftFromNLRequest(BaseModel):
    client_name: str = "Demo Client"
    message: str


class RFQDraftFromNLOut(BaseModel):
    draft: RFQRequestDraft
    missing_fields: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    extracted: dict[str, Any] = Field(default_factory=dict)


class RFQQuoteRequest(BaseModel):
    quote_mode: Literal["solve", "price"] | None = None
    created_by: str = "desk_user"
    valid_until: datetime | None = None
    market: PricingEnvironmentSnapshot | None = None
    engine_spec: RFQEngineSpecIn | None = None
    product_kwargs: dict[str, Any] | None = None
    unknown: RFQUnknownSpecIn | None = None
    target: RFQTargetIn | None = None


class RFQReleaseRequest(BaseModel):
    actor: str = "trader"
    response_override: str | None = None


class RFQClientAcceptRequest(BaseModel):
    actor: str = "client"
    comment: str | None = None


class RFQBookRequest(BaseModel):
    portfolio_id: int
    actor: str = "trader"
    quantity: float | None = None
    entry_price: float | None = None
    trade_effective_date: date | datetime | None = None


class RFQApprovalDecision(BaseModel):
    approver: str = "trader"
    comment: str | None = None
    response_override: str | None = None


class PortfolioCreate(BaseModel):
    name: str
    base_currency: str = "USD"
    kind: Literal["container", "view"] = "container"
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    filter_rule: dict[str, Any] | None = None
    manual_include_ids: list[int] = Field(default_factory=list)
    manual_exclude_ids: list[int] = Field(default_factory=list)
    source_portfolio_ids: list[int] = Field(default_factory=list)


class ProductSpecIn(BaseModel):
    asset_class: str = "equity"
    product_family: str = "option"
    quantark_class: str | None = "EuropeanVanillaOption"
    underlying: str = "CSI500"
    currency: str | None = None
    terms: dict[str, Any] = Field(default_factory=dict)
    components: list[dict[str, Any]] = Field(default_factory=list)
    display_name: str | None = None
    source_payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _resolve_currency(self) -> "ProductSpecIn":
        self.currency = resolve_underlying_currency(
            self.underlying, self.currency
        )
        return self


class ProductOut(ProductSpecIn):
    id: int
    underlying_id: int | None = None
    term_hash: str
    raw_terms: dict[str, Any] = Field(default_factory=dict)

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def _derive_terms_from_raw_terms(cls, data: Any) -> Any:
        if isinstance(data, dict):
            raw_terms = data.get("raw_terms") or {}
            if "terms" not in data:
                data = {**data, "terms": dict(raw_terms.get("terms") or {})}
            if "components" not in data:
                data = {**data, "components": list(raw_terms.get("components") or [])}
            return data
        raw_terms = getattr(data, "raw_terms", None) or {}
        return {
            "id": getattr(data, "id"),
            "asset_class": getattr(data, "asset_class", "equity"),
            "product_family": getattr(data, "product_family", "option"),
            "quantark_class": getattr(data, "quantark_class", None),
            "underlying": getattr(data, "underlying", "CSI500"),
            "currency": getattr(data, "currency", "CNY"),
            "terms": dict(raw_terms.get("terms") or {}),
            "components": list(raw_terms.get("components") or []),
            "display_name": getattr(data, "display_name", None),
            "source_payload": getattr(data, "source_payload", None),
            "term_hash": getattr(data, "term_hash"),
            "raw_terms": raw_terms,
        }


class PortfolioPositionSpec(BaseModel):
    underlying: str = "CSI500"
    product_type: str = "EuropeanVanillaOption"
    product_kwargs: dict[str, Any] = Field(
        default_factory=lambda: {
            "strike": 100.0,
            "option_type": "CALL",
            "maturity": 1.0,
        }
    )
    engine_name: str | None = None
    engine_kwargs: dict[str, Any] = Field(default_factory=dict)
    quantity: float = 1.0
    entry_price: float = 0.0
    currency: str | None = None
    status: str = "open"
    position_kind: Literal["otc", "listed"] = "otc"
    source_trade_id: str | None = None
    company: str | None = None
    rfq_id: int | None = None
    rfq_quote_version_id: int | None = None
    trade_effective_date: date | datetime | None = None
    product: ProductSpecIn | None = None

    @model_validator(mode="after")
    def _validate_currency(self) -> "PortfolioPositionSpec":
        if self.currency is not None:
            code = normalize_currency(self.currency)
            if code not in ISO_4217_CODES:
                raise ValueError(f"Invalid currency code: {self.currency!r}")
            self.currency = code
        return self


class PositionOut(BaseModel):
    id: int
    portfolio_id: int
    product_id: int | None = None
    underlying_id: int | None = None
    underlying: str
    product_type: str
    product_kwargs: dict[str, Any]
    product: ProductOut | None = None
    engine_name: str | None = None
    engine_kwargs: dict[str, Any]
    quantity: float
    entry_price: float
    currency: str = "CNY"
    status: str
    position_kind: Literal["otc", "listed"] = "otc"
    source_trade_id: str | None = None
    source_row: int | None = None
    mapping_status: str = "manual"
    mapping_error: str | None = None
    source_payload: dict[str, Any] | None = None
    rfq_id: int | None = None
    rfq_quote_version_id: int | None = None
    trade_effective_date: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PortfolioOut(BaseModel):
    id: int
    name: str
    base_currency: str
    kind: Literal["container", "view"]
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    filter_rule: dict[str, Any] | None = None
    manual_include_ids: list[int] = Field(default_factory=list)
    manual_exclude_ids: list[int] = Field(default_factory=list)
    source_portfolio_ids: list[int] = Field(default_factory=list)
    resolved_position_count: int = 0
    created_at: datetime
    updated_at: datetime
    positions: list[PositionOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class PortfolioUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    base_currency: str | None = None
    tags: list[str] | None = None


class PortfolioRuleBody(BaseModel):
    filter_rule: dict[str, Any] | None = None


class PortfolioIdsBody(BaseModel):
    position_ids: list[int] = Field(default_factory=list)


class PortfolioSourcesBody(BaseModel):
    portfolio_ids: list[int] = Field(default_factory=list)


class PortfolioTagsBody(BaseModel):
    tags: list[str] = Field(default_factory=list)


class PortfolioPreviewBody(BaseModel):
    kind: Literal["container", "view"] = "view"
    filter_rule: dict[str, Any] | None = None
    manual_include_ids: list[int] = Field(default_factory=list)
    manual_exclude_ids: list[int] = Field(default_factory=list)
    source_portfolio_ids: list[int] = Field(default_factory=list)


class PortfolioMembershipOut(BaseModel):
    portfolio_id: int
    position_ids: list[int]


class PositionImportBatchOut(BaseModel):
    id: int
    portfolio_id: int
    source_path: str
    source_sheet: str
    row_count: int
    imported_count: int
    supported_count: int
    unsupported_count: int
    error_count: int
    status: str
    summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = {"from_attributes": True}


class PricingParameterRowOut(BaseModel):
    id: int
    profile_id: int
    source_trade_id: str
    symbol: str
    instrument_id: int | None = None
    rate: float | None = None
    dividend_yield: float | None = None
    volatility: float | None = None
    source_row: int | None = None
    source_payload: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PricingParameterProfileOut(BaseModel):
    id: int
    name: str
    valuation_date: datetime
    source_type: str
    source_path: str | None = None
    status: str
    summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    rows: list[PricingParameterRowOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class PricingParameterRowCreate(BaseModel):
    source_trade_id: str | None = None
    symbol: str
    rate: float | None = None
    dividend_yield: float | None = None
    volatility: float | None = None


class PricingParameterProfileCreate(BaseModel):
    name: str | None = None
    valuation_date: datetime | None = None
    rows: list[PricingParameterRowCreate] = Field(default_factory=list)


class EngineConfigVariantIn(BaseModel):
    name: str
    description: str | None = None
    status: str = "active"
    is_default: bool = False
    rules: dict[str, Any] = Field(default_factory=dict)
    business_days_in_year: int | None = None


class EngineConfigVariantOut(BaseModel):
    id: int
    name: str
    description: str | None = None
    status: str
    is_default: bool
    rules: dict[str, Any] = Field(default_factory=dict)
    business_days_in_year: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PositionPriceRequest(BaseModel):
    position_ids: list[int] = Field(default_factory=list)
    pricing_parameter_profile_id: int | None = None
    engine_config_id: int | None = None
    valuation_date: datetime | None = None
    spot: float | None = None
    rate: float | None = None
    r: float | None = None
    dividend_yield: float | None = None
    q: float | None = None
    volatility: float | None = None
    vol: float | None = None
    engine_name: str | None = None
    engine_kwargs: dict[str, Any] = Field(default_factory=dict)
    compute_greeks: bool = False


class PositionValuationResultOut(BaseModel):
    id: int
    valuation_run_id: int
    position_id: int
    source_trade_id: str | None = None
    ok: bool
    price: float | None = None
    market_value: float | None = None
    pnl: float | None = None
    market_inputs: dict[str, Any] = Field(default_factory=dict)
    result_payload: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PositionValuationRunOut(BaseModel):
    id: int
    portfolio_id: int
    pricing_parameter_profile_id: int | None = None
    engine_config_id: int | None = None
    market_source_path: str | None = None
    valuation_date: datetime
    overrides: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    status: str
    resolved_position_ids: list[int] | None = None
    created_at: datetime
    results: list[PositionValuationResultOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class MarketDataSnapshot(BaseModel):
    name: str = "Manual snapshot"
    source: str = "manual"
    symbol: str = "000300"
    asset_class: str = "equity"
    valuation_date: datetime = Field(default_factory=datetime.utcnow)
    data: dict[str, Any] = Field(default_factory=dict)
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class AkshareSnapshotRequest(BaseModel):
    symbol: str = "000300"
    asset_class: Literal["stock", "index", "futures", "etf", "sge_spot", "fx_rate"] = "index"
    start_date: str
    end_date: str
    name: str | None = None
    adjust: str = "qfq"
    use_proxy: bool = True


class AkshareBulkSnapshotRequest(BaseModel):
    start_date: str
    end_date: str
    name: str | None = None
    adjust: str = "qfq"


class FxRateCreate(BaseModel):
    base_currency: str
    quote_currency: str
    rate: float
    as_of_date: datetime
    source: str = "manual"
    pricing_parameter_profile_id: int | None = None

    @model_validator(mode="after")
    def _validate_currencies(self) -> "FxRateCreate":
        self.base_currency = normalize_currency(self.base_currency)
        self.quote_currency = normalize_currency(self.quote_currency)
        for code in (self.base_currency, self.quote_currency):
            if code not in ISO_4217_CODES:
                raise ValueError(f"Invalid currency code: {code!r}")
        return self


class FxRateOut(BaseModel):
    id: int
    base_currency: str
    quote_currency: str
    rate: float
    as_of_date: datetime
    source: str
    pricing_parameter_profile_id: int | None = None

    model_config = {"from_attributes": True}


class FxRateAkshareRequest(BaseModel):
    base_currency: str
    quote_currency: str
    as_of_date: datetime | None = None

    @model_validator(mode="after")
    def _validate_currencies(self) -> "FxRateAkshareRequest":
        self.base_currency = normalize_currency(self.base_currency)
        self.quote_currency = normalize_currency(self.quote_currency)
        for code in (self.base_currency, self.quote_currency):
            if code not in ISO_4217_CODES:
                raise ValueError(f"Invalid currency code: {code!r}")
        return self


class MarketSnapshotOut(BaseModel):
    id: int
    name: str
    source: str
    symbol: str
    asset_class: str
    valuation_date: datetime
    data: dict[str, Any]
    source_metadata: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class MarketDataProfileOut(BaseModel):
    id: int
    underlying_id: int | None = None
    name: str
    source: str
    symbol: str
    asset_class: str
    start_date: str
    end_date: str
    adjust: str
    valuation_date: datetime
    data: dict[str, Any]
    source_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LatestAkshareClose(BaseModel):
    spot: float | None
    fetched_at: datetime | None
    fallback: bool = False
    market_data_profile_id: int | None = None


# ---------------------------------------------------------------------------
# Instrument master API schemas (Task 10 — replace the underlyings group)
# ---------------------------------------------------------------------------


class InstrumentOut(BaseModel):
    id: int
    symbol: str
    display_name: str | None = None
    kind: str
    exchange: str | None = None
    currency: str
    status: str
    source: str
    akshare_symbol: str | None = None
    akshare_asset_class: str | None = None
    contract_code: str | None = None
    series_root: str | None = None
    expiry: date | None = None
    multiplier: float | None = None
    strike: float | None = None
    option_type: str | None = None
    parent_id: int | None = None
    loaded_at: datetime | None = None
    rate: float | None = None
    dividend_yield: float | None = None
    volatility: float | None = None
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class InstrumentUpdate(BaseModel):
    """All fields optional — PATCH semantics; only provided fields applied."""

    display_name: str | None = None
    kind: str | None = None
    exchange: str | None = None
    currency: str | None = None
    status: str | None = None
    akshare_symbol: str | None = None
    akshare_asset_class: str | None = None
    series_root: str | None = None
    expiry: date | None = None
    multiplier: float | None = None
    strike: float | None = None
    option_type: str | None = None
    parent_id: int | None = None
    notes: str | None = None
    rate: float | None = None
    dividend_yield: float | None = None
    volatility: float | None = None


class InstrumentCreate(BaseModel):
    """Manual instrument creation — source defaults to 'manual'."""

    symbol: str
    kind: str
    display_name: str | None = None
    exchange: str | None = None
    currency: str = "CNY"
    status: str = "draft"
    akshare_symbol: str | None = None
    akshare_asset_class: str | None = None
    series_root: str | None = None
    expiry: date | None = None
    multiplier: float | None = None
    strike: float | None = None
    option_type: str | None = None
    parent_id: int | None = None
    notes: str | None = None
    rate: float | None = None
    dividend_yield: float | None = None
    volatility: float | None = None


class InstrumentTagsBody(BaseModel):
    tags: list[str] = Field(default_factory=list)


class InstrumentSyncResultOut(BaseModel):
    created: int
    existing: int
    instruments: list[InstrumentOut]


# ---------------------------------------------------------------------------
# Quotes API schemas (Task 10)
# ---------------------------------------------------------------------------


class MarketQuoteOut(BaseModel):
    id: int
    instrument_id: int
    symbol: str
    kind: str
    price: float
    price_type: str
    as_of: datetime
    source: str
    age_days: float
    market_data_profile_id: int | None = None

    model_config = {"from_attributes": True}


class MarketQuoteCreate(BaseModel):
    instrument_id: int
    price: float
    as_of: datetime
    price_type: str = "close"


class QuoteRefreshResultOut(BaseModel):
    synced_created: int
    synced_existing: int
    fetched: int
    skipped: list[str]
    failed: list[dict[str, str]]


class UnderlyingPricingDefaultOut(BaseModel):
    underlying: str
    rate: float | None = None
    dividend_yield: float | None = None
    volatility: float | None = None
    notes: str | None = None
    is_complete: bool
    # True when the underlying is in the current open-position scope — the same
    # scope the assumptions build gate validates against.
    has_open_position: bool = False
    latest_akshare_close: LatestAkshareClose | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UnderlyingPricingDefaultUpdate(BaseModel):
    rate: float | None = None
    dividend_yield: float | None = None
    volatility: float | None = None
    notes: str | None = None


class BuildDefaultProfileRequest(BaseModel):
    name: str | None = None
    valuation_date: datetime | None = None
    adjust: str = "qfq"


class BuildAssumptionsRequest(BaseModel):
    name: str | None = None
    valuation_date: datetime | None = None


class AssumptionRowOut(BaseModel):
    id: int
    instrument_id: int
    symbol: str
    rate: float | None = None
    dividend_yield: float | None = None
    volatility: float | None = None
    source_payload: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class AssumptionSetOut(BaseModel):
    id: int
    name: str
    valuation_date: datetime
    status: str
    summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    rows: list[AssumptionRowOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ResolvedParamOut(BaseModel):
    """One field's resolved value plus provenance. Extra source-specific keys
    (profile_id / source_trade_id / assumption_set_id / age_days / ...) ride
    alongside; the front-end reads them by source label."""

    value: float | None = None
    source: str
    model_config = {"extra": "allow"}


class ResolvedPricingParamsOut(BaseModel):
    spot: ResolvedParamOut
    rate: ResolvedParamOut
    dividend_yield: ResolvedParamOut
    volatility: ResolvedParamOut


class BatchPricingRunRequest(BaseModel):
    portfolio_id: int
    position_ids: list[int] | None = Field(default=None, min_length=1)
    pricing_parameter_profile_id: int | None = None
    engine_config_id: int | None = None
    market_snapshot_id: int | None = None


class ScenarioCell(BaseModel):
    spot_shift_pct: float
    vol_shift_abs: float
    pnl: float


class RiskRunOut(BaseModel):
    id: int
    portfolio_id: int
    pricing_parameter_profile_id: int | None = None
    engine_config_id: int | None = None
    market_snapshot_id: int | None = None
    method: str
    status: str
    metrics: dict[str, Any]
    scenario_cells: list[list[ScenarioCell]] | None = None
    resolved_position_ids: list[int] | None = None
    task_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class GreekLandscapeRunRequest(BaseModel):
    portfolio_id: int
    pricing_parameter_profile_id: int | None = None
    engine_config_id: int | None = None
    position_ids: list[int] | None = None
    spot_min_pct: float = -30.0
    spot_max_pct: float = 30.0
    spot_nodes: int = 61


class GreekLandscapeRunOut(BaseModel):
    id: int
    portfolio_id: int
    pricing_parameter_profile_id: int | None = None
    engine_config_id: int | None = None
    status: str
    config: dict[str, Any]
    results: dict[str, Any]
    excluded_positions: list | None = None
    resolved_position_ids: list[int] | None = None
    task_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ScenarioRunRequest(BaseModel):
    portfolio_id: int
    risk_run_id: int | None = None
    market_snapshot_id: int | None = None
    spot_shifts_pct: list[float] = [-3.0, -2.0, -1.0, 0.0, 1.0, 2.0]
    vol_shifts_abs: list[float] = [-0.02, 0.0, 0.02]


class ScenarioRunOut(BaseModel):
    portfolio_id: int
    base_pnl: float
    cells: list[list[ScenarioCell]]  # rows = vol_shifts, cols = spot_shifts


class ReportJobCreate(BaseModel):
    report_type: Literal["portfolio", "risk", "rfq"] = "portfolio"
    portfolio_id: int | None = None
    rfq_id: int | None = None
    pricing_parameter_profile_id: int | None = None
    title: str = "Open OTC Report"


class ReportJobOut(BaseModel):
    id: int
    report_type: str
    status: str
    request_payload: dict[str, Any]
    result_payload: dict[str, Any]
    artifact_paths: dict[str, Any]
    task_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TaskRunOut(BaseModel):
    id: int
    kind: str
    status: str
    portfolio_id: int | None = None
    risk_run_id: int | None = None
    greeks_landscape_run_id: int | None = None
    report_job_id: int | None = None
    progress_current: int
    progress_total: int
    message: str | None = None
    error: str | None = None
    result_payload: dict[str, Any] | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


class AuditEventOut(BaseModel):
    id: int
    event_type: str
    actor: str
    subject_type: str
    subject_id: str
    payload: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class PositionLifecycleEventIn(BaseModel):
    event_type: str
    event_data: dict[str, Any] = Field(default_factory=dict)


class PositionLifecycleEventCancelIn(BaseModel):
    reason: str | None = None


class PositionLifecycleEventOut(BaseModel):
    id: int
    position_id: int
    event_type: str
    event_data: dict[str, Any]
    old_status: str | None
    new_status: str | None
    actor: str
    created_at: datetime
    cancelled_at: datetime | None = None
    cancelled_by: str | None = None
    cancellation_reason: str | None = None

    model_config = {"from_attributes": True}


class AsyncAgentStartIn(BaseModel):
    """Input payload for start_async_agent (tool + API DTO)."""

    description: str = Field(..., max_length=120)
    prompt: str = Field(..., max_length=8000)
    inputs: dict[str, Any] | None = None


class AsyncAgentTaskOut(BaseModel):
    """Single async-agent task summary for list/list-API."""

    task_id: int
    description: str
    status: str
    awaiting_approval: bool
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_message_preview: str | None = None


# ---------------------------------------------------------------------------
# Hedging module schemas
# ---------------------------------------------------------------------------


class HedgeFamilyCount(BaseModel):
    family: str
    total: int
    allowed: int


class HedgeUnderlyingOut(BaseModel):
    underlying_id: int
    symbol: str
    display_name: str | None = None
    asset_class: str
    unresolvable: bool
    last_loaded_at: str | None = None
    stale_count: int
    families: list[HedgeFamilyCount]


class HedgeInstrumentOut(BaseModel):
    id: int
    underlying_id: int
    family: str
    series_root: str
    exchange: str
    contract_code: str
    instrument_type: str
    option_type: str | None = None
    strike: float | None = None
    expiry: str | None = None
    multiplier: float | None = None
    last_price: float | None = None
    status: str
    allowed: bool


class HedgeMapEntryOut(BaseModel):
    id: int
    instrument_id: int | None = None
    exchange: str
    contract_code: str
    family: str
    series_root: str
    instrument_type: str
    option_type: str | None = None
    strike: float | None = None
    expiry: str | None = None
    reconcile_status: str


class HedgeMapGroupOut(BaseModel):
    underlying_id: int
    underlying_symbol: str = ""
    entries: list[HedgeMapEntryOut]
    open_position_count: int = 0


class HedgeMarkRequest(BaseModel):
    instrument_ids: list[int]


class HedgeUnmarkRequest(BaseModel):
    instrument_ids: list[int] | None = None
    map_entry_ids: list[int] | None = None


class HedgeLoadStartedOut(BaseModel):
    task_id: int


class HedgeLoadStatusOut(BaseModel):
    task_id: int
    status: str
    progress_current: int
    progress_total: int
    message: str | None = None
    summary: dict | None = None


class HedgeMarkResultOut(BaseModel):
    marked: int


class HedgeRemovalResultOut(BaseModel):
    removed: int


# ---------------------------------------------------------------------------
# Hedging strategy engine schemas (A10)
# ---------------------------------------------------------------------------

from app.services.hedging_strategy_registry import STRATEGIES as _HEDGE_STRATEGIES  # noqa: E402


class HedgeBandsOut(BaseModel):
    delta: float
    gamma: float
    vega: float


class HedgeBandsIn(BaseModel):
    delta: float
    gamma: float
    vega: float


class HedgeSolveRequest(BaseModel):
    portfolio_id: int
    underlying: str
    strategy: str
    legs: list[dict[str, Any]] | None = None
    bands: dict[str, float] | None = None

    @field_validator("strategy")
    @classmethod
    def _known_strategy(cls, v: str) -> str:
        if v not in _HEDGE_STRATEGIES:
            raise ValueError(f"unknown strategy: {v!r}")
        return v


class HedgeBookRequest(BaseModel):
    portfolio_id: int
    underlying: str
    risk_run_id: int
    source_artifact_id: int
    artifact_generated_at: str
    valuation_as_of: str
    risk_generated_at: str
    expires_at: str
    strategy: str
    spot: float
    legs: list[dict[str, Any]]

    @field_validator("strategy")
    @classmethod
    def _known_strategy(cls, v: str) -> str:
        if v not in _HEDGE_STRATEGIES:
            raise ValueError(f"unknown strategy: {v!r}")
        return v


# ---------------------------------------------------------------------------
# Scenario-test schemas (Task 9)
# ---------------------------------------------------------------------------


class ScenarioStressSpec(BaseModel):
    param: str = Field(description="spot | vol | rate | dividend")
    stress_type: str = "PERCENTAGE"  # ABSOLUTE | PERCENTAGE | VALUE
    value: float
    level: str = "portfolio"          # portfolio | underlying | position
    target: str | int | None = None


class ScenarioSpec(BaseModel):
    name: str
    description: str | None = None
    stresses: list[ScenarioStressSpec] = Field(default_factory=list)


class ScenarioTestConfig(BaseModel):
    calculate_greeks: bool = True
    greeks_method: str = "numerical"
    export_formats: list[str] = Field(default_factory=lambda: ["json"])
    save_detailed_results: bool = True


class ScenarioTestRunRequest(BaseModel):
    portfolio_id: int
    pricing_parameter_profile_id: int | None = None
    engine_config_id: int | None = None
    position_ids: list[int] | None = None
    predefined: list[str] = Field(default_factory=list)
    custom: list[ScenarioSpec] = Field(default_factory=list)
    scenario_set: str | None = None
    scenario_sets: list[str] = Field(default_factory=list)
    config: ScenarioTestConfig = Field(default_factory=ScenarioTestConfig)


class ScenarioTestRunOut(BaseModel):
    id: int
    portfolio_id: int
    pricing_parameter_profile_id: int | None
    engine_config_id: int | None = None
    status: str
    scenario_spec: dict | None = None
    config: dict | None = None
    results: dict | None = None
    excluded_positions: list | None = None
    artifacts: dict | None = None
    resolved_position_ids: list[int] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ScenarioLibraryOut(BaseModel):
    predefined: list[dict]
    saved_sets: list[str]


class ScenarioSetsOut(BaseModel):
    saved_sets: list[str]


class ScenarioSetSavedOut(BaseModel):
    name: str
    path: str


class ScenarioSetDetailOut(BaseModel):
    name: str
    description: str = ""
    stresses: list[ScenarioStressSpec] = Field(default_factory=list)
    num_scenarios: int = 1


class GridAxisSpec(BaseModel):
    param: str = Field(description="spot | vol | rate | dividend")
    start: float
    stop: float
    step: float
    stress_type: str = "PERCENTAGE"
    level: str = "portfolio"
    target: str | int | None = None


class ScenarioGridRequest(BaseModel):
    name: str
    combine_mode: str = "cross_product"
    axes: list[GridAxisSpec] = Field(default_factory=list)


class ScenarioGridSavedOut(BaseModel):
    name: str
    num_scenarios: int
    path: str


class ScenarioSetSummaryOut(BaseModel):
    name: str
    num_scenarios: int
    combine_mode: str | None = None
    axes_summary: str = ""
    has_grid: bool = False
    axes: list[GridAxisSpec] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Backtest schemas
# ---------------------------------------------------------------------------


class BacktestConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    export_formats: list[str] = Field(default_factory=lambda: ["json", "xlsx", "html"])
    rebalance_band: float | None = None
    transaction_cost_bps: float | None = None
    roll_days_before_expiry: int | None = None
    calculate_surfaces: bool = False


class BacktestSpecIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: str
    end: str
    engine_family: str | None = None
    engine: str = "quad"
    autocallable_engine: str | None = None
    other_engine: str | None = None
    fallback_engine: str | None = None
    vol_source: str = "realized"
    vol_window: int = 20
    rate: float = 0.02
    flat_vol: float = 0.18


class BacktestRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    portfolio_id: int
    pricing_parameter_profile_id: int | None = None
    engine_config_id: int | None = None
    position_ids: list[int] | None = None
    spec: BacktestSpecIn
    config: BacktestConfigIn = Field(default_factory=BacktestConfigIn)


class BacktestRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    portfolio_id: int
    pricing_parameter_profile_id: int | None
    engine_config_id: int | None = None
    resolved_position_ids: list[int] | None
    status: str
    spec: dict
    config: dict
    results: dict
    excluded_positions: list | None
    artifacts: dict
    created_at: datetime


class DeskWorkflowSave(BaseModel):
    script: str


class DeskWorkflowSummaryOut(BaseModel):
    slug: str
    title: str
    persona: str
    description: str
    scope: str
    default_mode: str
    source: str
    params: list[dict] = []

    model_config = {"from_attributes": True}


class DeskWorkflowOut(DeskWorkflowSummaryOut):
    script: str

    model_config = {"from_attributes": True}

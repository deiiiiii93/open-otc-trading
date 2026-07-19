from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    event,
    Float,
    ForeignKey,
    func,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    inspect as sa_inspect,
    text,
)
from sqlalchemy.orm import (
    Mapped,
    ORMExecuteState,
    mapped_column,
    relationship,
    synonym,
)
from sqlalchemy.orm import Session as OrmSession

from .database import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class RfqStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    PRICING_FAILED = "pricing_failed"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    RELEASED = "released"
    CLIENT_ACCEPTED = "client_accepted"
    BOOKED = "booked"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ReportStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class TaskKind(str, Enum):
    BATCH_PRICING = "batch_pricing"
    GREEKS_LANDSCAPE = "greeks_landscape"
    SCENARIO_TEST = "scenario_test"
    LIMIT_MONITORING = "limit_monitoring"
    # position_pricing / risk_run are legacy kinds: no longer created, kept so
    # historical task rows keep their labels and filters.
    POSITION_PRICING = "position_pricing"
    RISK_RUN = "risk_run"
    REPORT_JOB = "report_job"
    HEDGE_LOAD = "hedge_instrument_load"
    BACKTEST = "backtest"
    ARENA_RUN = "arena_run"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class PortfolioKind(str, Enum):
    CONTAINER = "container"
    VIEW = "view"


class PortfolioError(Exception):
    """Base for portfolio domain errors."""


class PortfolioNameConflict(PortfolioError):
    pass


class RiskLimitHistoryDeletionError(RuntimeError):
    """Raised when immutable risk-limit history is queued for ORM deletion."""


class LimitIncidentEventMutationError(RuntimeError):
    """Raised when append-only incident evidence is changed or deleted."""


class LimitIncidentHistoryDeletionError(RuntimeError):
    """Raised when an incident projection and its timeline would be deleted."""


class PortfolioKindError(PortfolioError):
    pass


class RuleValidationError(PortfolioError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class RuleCompilationError(PortfolioError):
    pass


class PortfolioCycleError(PortfolioError):
    def __init__(self, message: str, cycle_path: list[int]):
        super().__init__(message)
        self.cycle_path = cycle_path


class PortfolioDepthError(PortfolioError):
    def __init__(self, message: str, depth_path: list[int]):
        super().__init__(message)
        self.depth_path = depth_path


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), default="Desk User")
    role: Mapped[str] = mapped_column(String(40), default="trader")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AgentThread(Base):
    __tablename__ = "agent_threads"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="Untitled thread")
    character: Mapped[str] = mapped_column(String(40), default="trader")
    report_currency: Mapped[str] = mapped_column(
        String(16), default="by_position", server_default="by_position", nullable=False
    )
    source: Mapped[str] = mapped_column(
        String(20), default="desk", server_default="desk", nullable=False
    )
    arena_run_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    active_workflow_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="SET NULL"), index=True, nullable=True
    )
    # Goal-mode (spec §H): at most one active goal run per thread plus its frozen
    # contract. Null when no goal run is active; cleared on a pointer-releasing
    # terminal state. Backed by ThreadColumnBackend -> GoalRunStore/GoalRunService.
    goal_run: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    goal_contract: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    messages: Mapped[list["AgentMessage"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="AgentMessage.created_at",
    )


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("agent_threads.id"), index=True)
    workflow_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflows.id"), index=True, nullable=True
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_sessions.id"), index=True, nullable=True
    )
    role: Mapped[str] = mapped_column(String(20))
    character: Mapped[str | None] = mapped_column(String(40), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    thread: Mapped[AgentThread] = relationship(back_populates="messages")


class DeskWorkflow(Base):
    """A user-authored, reusable desk workflow stored as a Python script.

    The ``script`` is the source of truth (self-describing via a ``meta`` dict
    literal); the metadata columns are a denormalized cache extracted from that
    literal on save. Distinct from the runtime ``Workflow`` (per-thread session
    bookkeeping).
    """

    __tablename__ = "desk_workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(160))
    persona: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[str] = mapped_column(String(16), default="local")
    default_mode: Mapped[str] = mapped_column(String(16), default="auto")
    script: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    @property
    def params(self) -> list[dict]:
        """Declared launch params, derived from the stored script's meta."""
        from .services.desk_workflows_script import extract_meta, validate_params
        try:
            return validate_params(extract_meta(self.script))
        except Exception:
            return []  # stored scripts are validated at save; be defensive


class MemoryEntry(Base):
    __tablename__ = "memory_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(16), index=True)
    scope_id: Mapped[str] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(Text)
    normalized_content: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(16), default="active")
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_error: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(16), default="extractor")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_memory_scope_status", "scope_type", "scope_id", "status"),
        Index(
            "ux_memory_dedup", "scope_type", "scope_id", "normalized_content",
            unique=True,
            sqlite_where=text("status != 'archived'"),
            postgresql_where=text("status != 'archived'"),
        ),
    )


class MemoryExtractionRun(Base):
    __tablename__ = "memory_extraction_runs"

    run_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    session_id: Mapped[int] = mapped_column(Integer, index=True)
    thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    persona: Mapped[str | None] = mapped_column(String(40), nullable=True)
    book_scope_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    trigger_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_extracted_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("agent_threads.id", ondelete="CASCADE"),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200))
    intent: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16), default="active")
    opened_by: Mapped[str] = mapped_column(String(40), default="router")
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    canonical_snapshot_ids: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_workflows_thread_id_status", "thread_id", "status"),
    )


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"),
        index=True,
    )
    persona: Mapped[str] = mapped_column(String(40))
    episode_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="active")
    checkpointer_key: Mapped[str] = mapped_column(String(160), unique=True)
    current_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_tasks.id"), nullable=True
    )
    lease_acquired_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "workflow_id",
            "persona",
            "episode_id",
            name="uq_agent_sessions_workflow_persona_episode",
        ),
        Index(
            "ix_agent_sessions_workflow_persona_status",
            "workflow_id",
            "persona",
            "status",
        ),
        Index(
            "uq_agent_sessions_active_workflow_persona",
            "workflow_id",
            "persona",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )


class AgentTask(Base):
    __tablename__ = "agent_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"),
        index=True,
    )
    task_type: Mapped[str] = mapped_column(String(80))
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    depends_on: Mapped[list[int]] = mapped_column(JSON, default=list)
    assigned_persona: Mapped[str] = mapped_column(String(40))
    assigned_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_sessions.id"),
        index=True,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), default="planned")
    context_pack_id: Mapped[int | None] = mapped_column(
        ForeignKey("context_packs.id"), index=True, nullable=True
    )
    output_artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("session_artifacts.id"), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_agent_tasks_workflow_status", "workflow_id", "status"),
    )


class SessionArtifact(Base):
    __tablename__ = "session_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"),
        index=True,
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_sessions.id"), nullable=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_tasks.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    schema_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    title: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    rendered_path: Mapped[str | None] = mapped_column(String(400), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    context_pack_id: Mapped[int | None] = mapped_column(
        ForeignKey("context_packs.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    pinned: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    superseded_by: Mapped[int | None] = mapped_column(
        ForeignKey("session_artifacts.id"), nullable=True
    )

    __table_args__ = (
        Index(
            "ix_session_artifacts_workflow_kind_created_at",
            "workflow_id",
            "kind",
            "created_at",
        ),
    )


class ArtifactEvidenceRef(Base):
    __tablename__ = "artifact_evidence_refs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artifact_id: Mapped[int] = mapped_column(
        ForeignKey("session_artifacts.id", ondelete="CASCADE"),
        index=True,
    )
    evidence_kind: Mapped[str] = mapped_column(String(40), index=True)
    evidence_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    bound_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ContextPackPayload(Base):
    __tablename__ = "context_pack_payloads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(80), unique=True)
    stable_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ContextPack(Base):
    __tablename__ = "context_packs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"),
        index=True,
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_tasks.id"), index=True, nullable=True
    )
    payload_id: Mapped[int] = mapped_column(
        ForeignKey("context_pack_payloads.id"), index=True
    )
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_context_packs_workflow_created_at", "workflow_id", "created_at"),
    )


class DomainEvent(Base):
    __tablename__ = "domain_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"),
        index=True,
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_sessions.id"), nullable=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_tasks.id"), nullable=True
    )
    artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("session_artifacts.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(40), index=True)
    schema_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    actor: Mapped[str] = mapped_column(String(40))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_domain_events_workflow_occurred_at", "workflow_id", "occurred_at"),
        Index("ix_domain_events_kind_occurred_at", "kind", "occurred_at"),
    )


class AgentActionAudit(Base):
    """Append-only dangerous-action audit trail (audit spec §4).

    kind: execution | hitl_proposal | hitl_decision.
    The only permitted mutation is the phase-1 -> phase-2 outcome update on an
    execution row, addressed by in-memory PK; everything else is append-only.
    """

    __tablename__ = "agent_action_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True, default="execution")
    status: Mapped[str] = mapped_column(String(20), index=True)
    deny_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(120), index=True)
    tool_class: Mapped[str] = mapped_column(String(30), index=True)
    tool_call_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True, index=True
    )
    audit_ref: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    envelope: Mapped[str | None] = mapped_column(String(40), nullable=True)
    actor: Mapped[str] = mapped_column(String(80), default="agent")
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    persona: Mapped[str | None] = mapped_column(String(40), nullable=True)
    thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_threads.id"), nullable=True, index=True
    )
    workflow_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflows.id"), nullable=True
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_sessions.id"), nullable=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_tasks.id"), nullable=True
    )
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    desk_workflow_slug: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    args_json: Mapped[dict] = mapped_column(JSON, default=dict)
    redacted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    result_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_agent_action_audits_tool_occurred", "tool_name", "occurred_at"),
        Index("ix_agent_action_audits_thread_occurred", "thread_id", "occurred_at"),
    )


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    base_currency: Mapped[str] = mapped_column(String(12), default="USD")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )
    kind: Mapped[str] = mapped_column(
        String(20), default=PortfolioKind.CONTAINER.value, nullable=False
    )
    filter_rule: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    manual_include_ids: Mapped[list[int]] = mapped_column(
        JSON, default=list, nullable=False
    )
    manual_exclude_ids: Mapped[list[int]] = mapped_column(
        JSON, default=list, nullable=False
    )
    source_portfolio_ids: Mapped[list[int]] = mapped_column(
        JSON, default=list, nullable=False
    )
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    positions: Mapped[list["Position"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )
    import_batches: Mapped[list["PositionImportBatch"]] = relationship(
        back_populates="portfolio"
    )
    valuation_runs: Mapped[list["PositionValuationRun"]] = relationship(
        back_populates="portfolio"
    )
    risk_runs: Mapped[list["RiskRun"]] = relationship(back_populates="portfolio")
    task_runs: Mapped[list["TaskRun"]] = relationship(back_populates="portfolio")


class Instrument(Base):
    """Role-based instrument master: underlyings AND listed contracts.

    kind = security type (index|etf|stock|futures|sge_spot|listed_option),
    NEVER a role. Roles are computed: "underlying" := positions reference it;
    "allowed hedge" := hedge_map_entries reference it.
    """

    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    kind: Mapped[str] = mapped_column(
        String(40), default="index", server_default="index", nullable=False
    )
    market: Mapped[str | None] = mapped_column(String(40), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(40), nullable=True)
    currency: Mapped[str] = mapped_column(
        String(8), default="CNY", server_default="CNY", nullable=False
    )
    akshare_symbol: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    akshare_asset_class: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(
        String(40), default="draft", server_default="draft", index=True, nullable=False
    )
    source: Mapped[str] = mapped_column(
        String(40), default="manual", server_default="manual", nullable=False
    )
    # Manual per-instrument r/q/vol defaults (the layer that feeds assumption
    # builds). Historically the UnderlyingPricingDefault synonym.
    rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Listed-contract terms (null for non-contracts).
    contract_code: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    series_root: Mapped[str | None] = mapped_column(String(40), nullable=True)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    multiplier: Mapped[float | None] = mapped_column(Float, nullable=True)
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    option_type: Mapped[str | None] = mapped_column(String(4), nullable=True)
    # Contractual underlier (IC2606 -> 000905.SH; LH option -> LH2609 future).
    # NOT hedge-routing — that stays config in hedging_universe.
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id"), index=True, nullable=True
    )
    # Last seen by a contract load; drives expire-missing.
    loaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    underlying = synonym("symbol")
    asset_class = synonym("kind")

    parent: Mapped["Instrument | None"] = relationship(remote_side=[id])
    positions: Mapped[list["Position"]] = relationship(back_populates="underlying_record")
    products: Mapped[list["Product"]] = relationship(back_populates="underlying_record")
    market_data_profiles: Mapped[list["MarketDataProfile"]] = relationship(
        back_populates="underlying_record"
    )

    __table_args__ = (
        Index("ix_instruments_kind_status", "kind", "status"),
        Index("ix_instruments_series_root_kind", "series_root", "kind"),
    )

    @property
    def is_complete(self) -> bool:
        return (
            self.rate is not None
            and self.dividend_yield is not None
            and self.volatility is not None
        )


# Compatibility aliases — consumers migrate phase by phase; these are cheap
# and may stay (precedent: UnderlyingPricingDefault has always been a synonym).
Underlying = Instrument
UnderlyingPricingDefault = Instrument


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), index=True, nullable=True
    )
    underlying_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id"), index=True, nullable=True
    )
    underlying: Mapped[str] = mapped_column(String(80))
    product_type: Mapped[str] = mapped_column(String(120))
    product_kwargs: Mapped[dict] = mapped_column(JSON, default=dict)
    engine_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    engine_kwargs: Mapped[dict] = mapped_column(JSON, default=dict)
    quantity: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(
        String(8), default="CNY", server_default="CNY", nullable=False
    )
    status: Mapped[str] = mapped_column(String(40), default="open")
    position_kind: Mapped[str] = mapped_column(
        String(16), default="otc", server_default="otc", nullable=False, index=True
    )
    source_trade_id: Mapped[str | None] = mapped_column(
        String(160), index=True, nullable=True
    )
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mapping_status: Mapped[str] = mapped_column(String(40), default="manual")
    mapping_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_payload: Mapped[dict | None] = mapped_column(
        JSON, default=dict, nullable=True
    )
    rfq_id: Mapped[int | None] = mapped_column(
        ForeignKey("rfqs.id"), index=True, nullable=True
    )
    rfq_quote_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("rfq_quote_versions.id"), index=True, nullable=True
    )
    trade_effective_date: Mapped[datetime | None] = mapped_column(
        DateTime, index=True, nullable=True
    )
    kwargs_migrated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    portfolio: Mapped[Portfolio] = relationship(back_populates="positions")
    product: Mapped["Product | None"] = relationship(back_populates="positions")
    underlying_record: Mapped["Underlying | None"] = relationship(
        back_populates="positions"
    )
    valuation_results: Mapped[list["PositionValuationResult"]] = relationship(
        back_populates="position"
    )
    rfq: Mapped["RFQ | None"] = relationship(back_populates="booked_positions")
    rfq_quote_version: Mapped["RFQQuoteVersion | None"] = relationship(
        back_populates="booked_positions"
    )
    lifecycle_events: Mapped[list["PositionLifecycleEvent"]] = relationship(
        back_populates="position", cascade="all, delete-orphan", order_by="PositionLifecycleEvent.created_at.desc()"
    )


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        Index("ix_products_asset_family", "asset_class", "product_family"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_class: Mapped[str] = mapped_column(
        String(40), default="equity", server_default="equity", nullable=False
    )
    product_family: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    quantark_class: Mapped[str | None] = mapped_column(
        String(120), index=True, nullable=True
    )
    display_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    underlying_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id"), index=True, nullable=True
    )
    underlying: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(8), default="CNY", server_default="CNY", nullable=False
    )
    term_hash: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    raw_terms: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    source_payload: Mapped[dict | None] = mapped_column(
        JSON, default=dict, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    positions: Mapped[list["Position"]] = relationship(back_populates="product")
    underlying_record: Mapped["Underlying | None"] = relationship(
        back_populates="products"
    )
    option_terms: Mapped["EquityOptionProduct | None"] = relationship(
        back_populates="product", cascade="all, delete-orphan", uselist=False
    )
    autocallable_terms: Mapped["EquityAutocallableProduct | None"] = relationship(
        back_populates="product", cascade="all, delete-orphan", uselist=False
    )
    autocallable_observations: Mapped[
        list["EquityAutocallableObservation"]
    ] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="EquityAutocallableObservation.sequence",
    )
    phoenix_coupon_terms: Mapped["EquityPhoenixCouponProduct | None"] = relationship(
        back_populates="product", cascade="all, delete-orphan", uselist=False
    )
    barrier_terms: Mapped["EquityBarrierProduct | None"] = relationship(
        back_populates="product", cascade="all, delete-orphan", uselist=False
    )
    touch_terms: Mapped["EquityTouchProduct | None"] = relationship(
        back_populates="product", cascade="all, delete-orphan", uselist=False
    )
    asian_terms: Mapped["EquityAsianProduct | None"] = relationship(
        back_populates="product", cascade="all, delete-orphan", uselist=False
    )
    asian_observations: Mapped[list["EquityAsianObservation"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="EquityAsianObservation.sequence",
    )
    range_accrual_terms: Mapped["EquityRangeAccrualProduct | None"] = relationship(
        back_populates="product", cascade="all, delete-orphan", uselist=False
    )
    range_accrual_observations: Mapped[
        list["EquityRangeAccrualObservation"]
    ] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="EquityRangeAccrualObservation.sequence",
    )
    sharkfin_terms: Mapped["EquitySharkfinProduct | None"] = relationship(
        back_populates="product", cascade="all, delete-orphan", uselist=False
    )
    spot_terms: Mapped["EquitySpotProduct | None"] = relationship(
        back_populates="product", cascade="all, delete-orphan", uselist=False
    )
    futures_terms: Mapped["EquityFuturesProduct | None"] = relationship(
        back_populates="product", cascade="all, delete-orphan", uselist=False
    )
    components: Mapped[list["EquityProductComponent"]] = relationship(
        back_populates="parent_product",
        cascade="all, delete-orphan",
        foreign_keys="EquityProductComponent.parent_product_id",
        order_by="EquityProductComponent.sequence",
    )


class EquityOptionProduct(Base):
    __tablename__ = "equity_option_products"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    option_type: Mapped[str | None] = mapped_column(String(8), nullable=True)
    exercise_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    maturity: Mapped[float | None] = mapped_column(Float, nullable=True)
    exercise_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    settlement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    maturity_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    tenor: Mapped[float | None] = mapped_column(Float, nullable=True)
    tenor_end: Mapped[str | None] = mapped_column(String(40), nullable=True)
    annualization_day_count: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    initial_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    contract_multiplier: Mapped[float] = mapped_column(
        Float, default=1.0, server_default="1.0", nullable=False
    )

    product: Mapped[Product] = relationship(back_populates="option_terms")


class EquityAutocallableProduct(Base):
    __tablename__ = "equity_autocallable_products"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    autocallable_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    is_reverse: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    initial_price: Mapped[float] = mapped_column(Float, nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    contract_multiplier: Mapped[float] = mapped_column(
        Float, default=1.0, server_default="1.0", nullable=False
    )
    ko_observation_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    ki_observation_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    ki_continuous: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    disable_ko_after_ki: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    payoff_rebate_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    payoff_call_rebate_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    payoff_call_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    payoff_call_participation_rate: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    payoff_include_principal: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )
    payoff_participation_rate: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    payoff_protection_type: Mapped[str | None] = mapped_column(
        String(24), nullable=True
    )
    payoff_protection_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    accrual_coupon_pay_type: Mapped[str | None] = mapped_column(
        String(24), nullable=True
    )
    accrual_is_annualized: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )
    accrual_is_annualized_ko: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    accrual_is_annualized_ki: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    accrual_is_annualized_rebate: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    reset_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    product: Mapped[Product] = relationship(back_populates="autocallable_terms")


class EquityAutocallableObservation(Base):
    __tablename__ = "equity_autocallable_observations"
    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "observation_role",
            "sequence",
            name="uq_equity_autocallable_observations_role_sequence",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    observation_role: Mapped[str] = mapped_column(String(24), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    observation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    observation_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    barrier_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    accrual_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    aggregation: Mapped[str | None] = mapped_column(String(24), nullable=True)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    product: Mapped[Product] = relationship(back_populates="autocallable_observations")


class EquityPhoenixCouponProduct(Base):
    __tablename__ = "equity_phoenix_coupon_products"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    coupon_barrier: Mapped[float] = mapped_column(Float, nullable=False)
    coupon_rate: Mapped[float] = mapped_column(Float, nullable=False)
    coupon_pay_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    day_count_convention: Mapped[str | None] = mapped_column(String(40), nullable=True)
    memory_coupon: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )
    fixed_coupon_year_fraction: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )

    product: Mapped[Product] = relationship(back_populates="phoenix_coupon_terms")


class EquityBarrierProduct(Base):
    __tablename__ = "equity_barrier_products"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    barrier_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    barrier: Mapped[float | None] = mapped_column(Float, nullable=True)
    barrier_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    upper_barrier: Mapped[float | None] = mapped_column(Float, nullable=True)
    lower_barrier: Mapped[float | None] = mapped_column(Float, nullable=True)
    rebate: Mapped[float | None] = mapped_column(Float, nullable=True)
    monitoring_type: Mapped[str | None] = mapped_column(String(24), nullable=True)

    product: Mapped[Product] = relationship(back_populates="barrier_terms")


class EquityTouchProduct(Base):
    __tablename__ = "equity_touch_products"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    touch_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    barrier: Mapped[float | None] = mapped_column(Float, nullable=True)
    upper_barrier: Mapped[float | None] = mapped_column(Float, nullable=True)
    lower_barrier: Mapped[float | None] = mapped_column(Float, nullable=True)
    touch_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payout: Mapped[float | None] = mapped_column(Float, nullable=True)
    rebate: Mapped[float | None] = mapped_column(Float, nullable=True)
    monitoring_type: Mapped[str | None] = mapped_column(String(24), nullable=True)

    product: Mapped[Product] = relationship(back_populates="touch_terms")


class EquityAsianProduct(Base):
    __tablename__ = "equity_asian_products"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    averaging_method: Mapped[str | None] = mapped_column(String(24), nullable=True)
    averaging_kind: Mapped[str | None] = mapped_column(String(24), nullable=True)
    n_observations: Mapped[int | None] = mapped_column(Integer, nullable=True)

    product: Mapped[Product] = relationship(back_populates="asian_terms")


class EquityAsianObservation(Base):
    __tablename__ = "equity_asian_observations"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    observation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    observation_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    observed_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)

    product: Mapped[Product] = relationship(back_populates="asian_observations")


class EquityRangeAccrualProduct(Base):
    __tablename__ = "equity_range_accrual_products"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    lower_barrier: Mapped[float] = mapped_column(Float, nullable=False)
    upper_barrier: Mapped[float] = mapped_column(Float, nullable=False)
    accrual_rate: Mapped[float] = mapped_column(Float, nullable=False)
    observation_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    day_count_convention: Mapped[str | None] = mapped_column(String(40), nullable=True)

    product: Mapped[Product] = relationship(back_populates="range_accrual_terms")


class EquityRangeAccrualObservation(Base):
    __tablename__ = "equity_range_accrual_observations"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    observation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    observation_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    lower_barrier: Mapped[float | None] = mapped_column(Float, nullable=True)
    upper_barrier: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)

    product: Mapped[Product] = relationship(back_populates="range_accrual_observations")


class EquitySharkfinProduct(Base):
    __tablename__ = "equity_sharkfin_products"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    sharkfin_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    barrier: Mapped[float | None] = mapped_column(Float, nullable=True)
    upper_barrier: Mapped[float | None] = mapped_column(Float, nullable=True)
    lower_barrier: Mapped[float | None] = mapped_column(Float, nullable=True)
    option_type: Mapped[str | None] = mapped_column(String(8), nullable=True)
    participation_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    coupon: Mapped[float | None] = mapped_column(Float, nullable=True)
    rebate: Mapped[float | None] = mapped_column(Float, nullable=True)
    observation_type: Mapped[str | None] = mapped_column(String(24), nullable=True)

    product: Mapped[Product] = relationship(back_populates="sharkfin_terms")


class EquitySpotProduct(Base):
    __tablename__ = "equity_spot_products"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    deltaone_type: Mapped[str] = mapped_column(String(16), nullable=False)
    instrument_code: Mapped[str] = mapped_column(String(80), nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(40), nullable=True)
    contract_multiplier: Mapped[float] = mapped_column(
        Float, default=1.0, server_default="1.0", nullable=False
    )

    product: Mapped[Product] = relationship(back_populates="spot_terms")


class EquityFuturesProduct(Base):
    __tablename__ = "equity_futures_products"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    contract_code: Mapped[str] = mapped_column(String(80), nullable=False)
    multiplier: Mapped[float] = mapped_column(
        Float, default=1.0, server_default="1.0", nullable=False
    )
    maturity: Mapped[float | None] = mapped_column(Float, nullable=True)
    maturity_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    basis: Mapped[float] = mapped_column(
        Float, default=0.0, server_default="0.0", nullable=False
    )
    basis_decay_rate: Mapped[float] = mapped_column(
        Float, default=1.0, server_default="1.0", nullable=False
    )
    market_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    product: Mapped[Product] = relationship(back_populates="futures_terms")


class EquityProductComponent(Base):
    __tablename__ = "equity_product_components"
    __table_args__ = (
        UniqueConstraint(
            "parent_product_id",
            "sequence",
            name="uq_equity_product_components_parent_sequence",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    component_product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id"), nullable=False
    )
    component_role: Mapped[str] = mapped_column(String(40), nullable=False)
    quantity: Mapped[float] = mapped_column(
        Float, default=1.0, server_default="1.0", nullable=False
    )
    weight: Mapped[float] = mapped_column(
        Float, default=1.0, server_default="1.0", nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    source_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    parent_product: Mapped[Product] = relationship(
        back_populates="components", foreign_keys=[parent_product_id]
    )
    component_product: Mapped[Product] = relationship(
        foreign_keys=[component_product_id]
    )


class OptionCoreTerm(Base):
    __tablename__ = "option_core_terms"

    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id", ondelete="CASCADE"), primary_key=True
    )
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    option_type: Mapped[str | None] = mapped_column(String(8), nullable=True)
    side: Mapped[str] = mapped_column(String(8), default="long", nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    notional: Mapped[float | None] = mapped_column(Float, nullable=True)


class SingleBarrierTerm(Base):
    __tablename__ = "single_barrier_terms"

    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id", ondelete="CASCADE"), primary_key=True
    )
    barrier: Mapped[float | None] = mapped_column(Float, nullable=True)
    barrier_type: Mapped[str | None] = mapped_column(String(4), nullable=True)
    rebate: Mapped[float | None] = mapped_column(Float, nullable=True)


class DoubleBarrierTerm(Base):
    __tablename__ = "double_barrier_terms"

    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id", ondelete="CASCADE"), primary_key=True
    )
    upper_barrier: Mapped[float | None] = mapped_column(Float, nullable=True)
    lower_barrier: Mapped[float | None] = mapped_column(Float, nullable=True)
    barrier_kind: Mapped[str | None] = mapped_column(String(4), nullable=True)
    rebate: Mapped[float | None] = mapped_column(Float, nullable=True)


class SharkfinTerm(Base):
    __tablename__ = "sharkfin_terms"

    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id", ondelete="CASCADE"), primary_key=True
    )
    participation_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    coupon: Mapped[float | None] = mapped_column(Float, nullable=True)


class AsianTerm(Base):
    __tablename__ = "asian_terms"

    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id", ondelete="CASCADE"), primary_key=True
    )
    averaging_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    averaging_kind: Mapped[str | None] = mapped_column(String(8), nullable=True)
    n_observations: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AsianAveragingDate(Base):
    __tablename__ = "asian_averaging_dates"

    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id", ondelete="CASCADE"), primary_key=True
    )
    observation_date: Mapped[date] = mapped_column(Date, primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)


class SnowballTerm(Base):
    __tablename__ = "snowball_terms"

    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id", ondelete="CASCADE"), primary_key=True
    )
    initial_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    ki_barrier: Mapped[float | None] = mapped_column(Float, nullable=True)
    coupon: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    knocked_in: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    ki_observation: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payoff_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    legacy_kwargs: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class SnowballKoSchedule(Base):
    __tablename__ = "snowball_ko_schedule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id", ondelete="CASCADE"), index=True
    )
    observation_date: Mapped[date] = mapped_column(Date, nullable=False)
    ko_level: Mapped[float] = mapped_column(Float, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "position_id",
            "observation_date",
            name="uq_snowball_ko_schedule_position_date",
        ),
    )


class PositionBarrierState(Base):
    __tablename__ = "position_barrier_state"

    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id", ondelete="CASCADE"), primary_key=True
    )
    nearest_barrier_kind: Mapped[str | None] = mapped_column(String(8), nullable=True)
    nearest_barrier_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    nearest_barrier_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    days_to_nearest: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


_POSITION_VERSION_FIELDS = {
    "product_id",
    "underlying",
    "product_type",
    "product_kwargs",
    "engine_name",
    "engine_kwargs",
    "quantity",
    "entry_price",
    "status",
    "trade_effective_date",
}


@event.listens_for(Position, "before_update")
def _bump_position_version(_mapper, _connection, target: Position) -> None:
    state = sa_inspect(target)
    if any(state.attrs[field].history.has_changes() for field in _POSITION_VERSION_FIELDS):
        target.version = int(target.version or 1) + 1


class PositionLifecycleEvent(Base):
    __tablename__ = "position_lifecycle_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    event_data: Mapped[dict] = mapped_column(JSON, default=dict)
    old_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    actor: Mapped[str] = mapped_column(String(120), default="desk_user")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    position: Mapped["Position"] = relationship(back_populates="lifecycle_events")


class PositionImportBatch(Base):
    __tablename__ = "position_import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    source_path: Mapped[str] = mapped_column(Text)
    source_sheet: Mapped[str] = mapped_column(String(120))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    supported_count: Mapped[int] = mapped_column(Integer, default=0)
    unsupported_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(40), default="completed")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    portfolio: Mapped[Portfolio] = relationship(back_populates="import_batches")


class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (
        Index("ix_fx_rates_pair_as_of", "base_currency", "quote_currency", "as_of_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base_currency: Mapped[str] = mapped_column(String(8), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(8), nullable=False)
    rate: Mapped[float] = mapped_column(Float, nullable=False)
    as_of_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    pricing_parameter_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("pricing_parameter_profiles.id"), nullable=True
    )
    source: Mapped[str] = mapped_column(
        String(40), default="manual", server_default="manual", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )


class MarketQuote(Base):
    """Single observation store. All fetchers write here; resolution is
    latest(as_of <= valuation), id tie-break. Source is diagnostics, not
    priority — unification happens at write time."""

    __tablename__ = "market_quotes"
    __table_args__ = (
        Index("ix_market_quotes_instrument_as_of", "instrument_id", "as_of"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    price_type: Mapped[str] = mapped_column(
        String(12), default="close", server_default="close", nullable=False
    )
    source: Mapped[str] = mapped_column(
        String(40), default="manual", server_default="manual", nullable=False
    )
    market_data_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_data_profiles.id"), nullable=True
    )
    meta: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AssumptionSet(Base):
    """Instrument-level r/q/vol, versioned + valuation-dated. Built, never
    imported (trade-keyed imports live in PricingParameterProfile)."""

    __tablename__ = "assumption_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    valuation_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(40), default="completed")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    rows: Mapped[list["AssumptionRow"]] = relationship(
        back_populates="assumption_set", cascade="all, delete-orphan"
    )


class AssumptionRow(Base):
    __tablename__ = "assumption_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    set_id: Mapped[int] = mapped_column(ForeignKey("assumption_sets.id"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(80), index=True)
    rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_payload: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    assumption_set: Mapped[AssumptionSet] = relationship(back_populates="rows")


class PricingParameterProfile(Base):
    __tablename__ = "pricing_parameter_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    valuation_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    source_type: Mapped[str] = mapped_column(String(40), default="xlsx")
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="completed")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    rows: Mapped[list["PricingParameterRow"]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by="PricingParameterRow.source_trade_id",
    )


class PricingParameterRow(Base):
    __tablename__ = "pricing_parameter_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("pricing_parameter_profiles.id"), index=True
    )
    source_trade_id: Mapped[str] = mapped_column(String(160), index=True)
    symbol: Mapped[str] = mapped_column(String(80), index=True)
    instrument_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id"), index=True, nullable=True
    )
    rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_payload: Mapped[dict | None] = mapped_column(
        JSON, default=dict, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    profile: Mapped[PricingParameterProfile] = relationship(back_populates="rows")


class EngineConfigVariant(Base):
    __tablename__ = "engine_config_variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active", server_default="active")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    rules: Mapped[dict] = mapped_column(JSON, default=dict)
    business_days_in_year: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )


class PositionValuationRun(Base):
    __tablename__ = "position_valuation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    pricing_parameter_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("pricing_parameter_profiles.id"),
        nullable=True,
        index=True,
    )
    engine_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("engine_config_variants.id"), nullable=True, index=True
    )
    market_source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    valuation_date: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    resolved_position_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    portfolio: Mapped[Portfolio] = relationship(back_populates="valuation_runs")
    pricing_parameter_profile: Mapped["PricingParameterProfile | None"] = relationship()
    engine_config: Mapped["EngineConfigVariant | None"] = relationship()
    results: Mapped[list["PositionValuationResult"]] = relationship(
        back_populates="valuation_run",
        cascade="all, delete-orphan",
    )


class PositionValuationResult(Base):
    __tablename__ = "position_valuation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    valuation_run_id: Mapped[int] = mapped_column(
        ForeignKey("position_valuation_runs.id"), index=True
    )
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    source_trade_id: Mapped[str | None] = mapped_column(
        String(160), index=True, nullable=True
    )
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    result_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    valuation_run: Mapped[PositionValuationRun] = relationship(back_populates="results")
    position: Mapped[Position] = relationship(back_populates="valuation_results")


class RiskRun(Base):
    __tablename__ = "risk_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    pricing_parameter_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("pricing_parameter_profiles.id"),
        nullable=True,
        index=True,
    )
    engine_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("engine_config_variants.id"), nullable=True, index=True
    )
    market_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_snapshots.id"), nullable=True
    )
    method: Mapped[str] = mapped_column(String(80), default="summary")
    status: Mapped[str] = mapped_column(String(40), default="completed")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    scenario_cells: Mapped[list | None] = mapped_column(JSON, nullable=True)
    resolved_position_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    portfolio: Mapped[Portfolio] = relationship(back_populates="risk_runs")
    pricing_parameter_profile: Mapped["PricingParameterProfile | None"] = relationship()
    engine_config: Mapped["EngineConfigVariant | None"] = relationship()
    market_snapshot: Mapped["MarketSnapshot | None"] = relationship()
    task_runs: Mapped[list["TaskRun"]] = relationship(back_populates="risk_run")


class GreekLandscapeRun(Base):
    __tablename__ = "greek_landscape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    pricing_parameter_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("pricing_parameter_profiles.id"), nullable=True, index=True
    )
    engine_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("engine_config_variants.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(40), default=TaskStatus.QUEUED.value)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    excluded_positions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    resolved_position_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    portfolio: Mapped["Portfolio"] = relationship()
    pricing_parameter_profile: Mapped["PricingParameterProfile | None"] = relationship()
    engine_config: Mapped["EngineConfigVariant | None"] = relationship()
    task_runs: Mapped[list["TaskRun"]] = relationship(back_populates="greeks_landscape_run")


class ScenarioTestRun(Base):
    __tablename__ = "scenario_test_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    pricing_parameter_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("pricing_parameter_profiles.id"), nullable=True, index=True
    )
    engine_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("engine_config_variants.id"), nullable=True, index=True
    )
    resolved_position_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default=TaskStatus.QUEUED.value)
    scenario_spec: Mapped[dict] = mapped_column(JSON, default=dict)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    excluded_positions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    artifacts: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    portfolio: Mapped["Portfolio"] = relationship()
    pricing_parameter_profile: Mapped["PricingParameterProfile | None"] = relationship()
    engine_config: Mapped["EngineConfigVariant | None"] = relationship()
    task_runs: Mapped[list["TaskRun"]] = relationship(back_populates="scenario_test_run")


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    pricing_parameter_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("pricing_parameter_profiles.id"), nullable=True, index=True
    )
    engine_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("engine_config_variants.id"), nullable=True, index=True
    )
    resolved_position_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default=TaskStatus.QUEUED.value)
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    excluded_positions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    artifacts: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    portfolio: Mapped["Portfolio"] = relationship()
    pricing_parameter_profile: Mapped["PricingParameterProfile | None"] = relationship()
    engine_config: Mapped["EngineConfigVariant | None"] = relationship()
    task_runs: Mapped[list["TaskRun"]] = relationship(back_populates="backtest_run")


class RiskLimit(Base):
    __tablename__ = "risk_limits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    owner: Mapped[str] = mapped_column(String(120), nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    active_version_id: Mapped[int | None] = mapped_column(
        Integer, index=True, nullable=True
    )
    created_by_actor: Mapped[str] = mapped_column(
        String(120), default="system", nullable=False
    )
    created_by_persona: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    row_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    versions: Mapped[list["RiskLimitVersion"]] = relationship(
        back_populates="risk_limit",
        passive_deletes=True,
        order_by="RiskLimitVersion.version",
    )
    incidents: Mapped[list["LimitIncident"]] = relationship(
        back_populates="risk_limit"
    )

    __table_args__ = (
        UniqueConstraint("key", name="uq_risk_limits_key"),
    )


class RiskLimitVersion(Base):
    __tablename__ = "risk_limit_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    risk_limit_id: Mapped[int] = mapped_column(
        ForeignKey("risk_limits.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), default="draft", server_default="draft", index=True
    )
    metric_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    methodology: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    aggregation: Mapped[str] = mapped_column(String(24), nullable=False)
    transform: Mapped[str] = mapped_column(String(24), nullable=False)
    comparator: Mapped[str] = mapped_column(String(16), nullable=False)
    warning_lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    warning_upper: Mapped[float | None] = mapped_column(Float, nullable=True)
    hard_lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    hard_upper: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    bump_convention: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    freshness_policy: Mapped[dict] = mapped_column(
        JSON, default=dict, nullable=False
    )
    effective_from: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    effective_until: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_actor: Mapped[str] = mapped_column(
        String(120), default="system", nullable=False
    )
    created_by_persona: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    created_in_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_in_thread_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    activated_by_actor: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    activated_by_persona: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    activated_in_mode: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    activated_in_thread_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    risk_limit: Mapped[RiskLimit] = relationship(back_populates="versions")
    monitoring_run_links: Mapped[list["LimitMonitoringRunVersion"]] = relationship(
        back_populates="limit_version"
    )
    evaluations: Mapped[list["LimitEvaluation"]] = relationship(
        back_populates="limit_version"
    )

    __table_args__ = (
        UniqueConstraint(
            "risk_limit_id",
            "version",
            name="uq_risk_limit_versions_limit_version",
        ),
    )


class LimitMonitoringRun(Base):
    __tablename__ = "limit_monitoring_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[str] = mapped_column(String(24), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    schedule_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occurrence_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id"), index=True
    )
    pricing_parameter_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("pricing_parameter_profiles.id"), nullable=True
    )
    engine_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("engine_config_variants.id"), nullable=True
    )
    market_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_snapshots.id"), nullable=True
    )
    valuation_as_of: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source_policy: Mapped[str] = mapped_column(String(24), nullable=False)
    max_source_age_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(40), default="queued", server_default="queued", index=True
    )
    summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    definition_snapshot: Mapped[dict] = mapped_column(
        JSON, default=dict, nullable=False
    )
    definition_snapshot_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    task_runs: Mapped[list["TaskRun"]] = relationship(
        back_populates="limit_monitoring_run"
    )
    version_links: Mapped[list["LimitMonitoringRunVersion"]] = relationship(
        back_populates="monitoring_run",
        cascade="all, delete-orphan",
    )
    source_references: Mapped[list["LimitSourceReference"]] = relationship(
        back_populates="monitoring_run",
        cascade="all, delete-orphan",
    )
    evaluations: Mapped[list["LimitEvaluation"]] = relationship(
        back_populates="monitoring_run",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "uq_limit_monitoring_runs_active_portfolio",
            "portfolio_id",
            unique=True,
            sqlite_where=text("status IN ('queued', 'running')"),
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )


class LimitMonitoringRunVersion(Base):
    __tablename__ = "limit_monitoring_run_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    monitoring_run_id: Mapped[int] = mapped_column(
        ForeignKey("limit_monitoring_runs.id", ondelete="CASCADE"), index=True
    )
    limit_version_id: Mapped[int] = mapped_column(
        ForeignKey("risk_limit_versions.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    monitoring_run: Mapped[LimitMonitoringRun] = relationship(
        back_populates="version_links"
    )
    limit_version: Mapped[RiskLimitVersion] = relationship(
        back_populates="monitoring_run_links"
    )

    __table_args__ = (
        UniqueConstraint(
            "monitoring_run_id",
            "limit_version_id",
            name="uq_limit_monitoring_run_versions_run_version",
        ),
    )


class LimitSourceReference(Base):
    __tablename__ = "limit_source_references"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    monitoring_run_id: Mapped[int] = mapped_column(
        ForeignKey("limit_monitoring_runs.id", ondelete="CASCADE"), index=True
    )
    source_kind: Mapped[str] = mapped_column(String(32), index=True)
    risk_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("risk_runs.id"), nullable=True
    )
    scenario_test_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenario_test_runs.id"), nullable=True
    )
    backtest_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("backtest_runs.id"), nullable=True
    )
    requested_parameters: Mapped[dict] = mapped_column(
        JSON, default=dict, nullable=False
    )
    source_status: Mapped[str] = mapped_column(String(40), nullable=False)
    is_fresh: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    completeness_diagnostics: Mapped[dict] = mapped_column(
        JSON, default=dict, nullable=False
    )
    source_valuation_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    source_created_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    monitoring_run: Mapped[LimitMonitoringRun] = relationship(
        back_populates="source_references"
    )


class LimitEvaluation(Base):
    __tablename__ = "limit_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    monitoring_run_id: Mapped[int] = mapped_column(
        ForeignKey("limit_monitoring_runs.id", ondelete="CASCADE"), index=True
    )
    limit_version_id: Mapped[int] = mapped_column(
        ForeignKey("risk_limit_versions.id")
    )
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(200), nullable=False)
    scope_label: Mapped[str] = mapped_column(String(200), nullable=False)
    observed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    adverse_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    warning_lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    warning_upper: Mapped[float | None] = mapped_column(Float, nullable=True)
    hard_lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    hard_upper: Mapped[float | None] = mapped_column(Float, nullable=True)
    utilization: Mapped[float | None] = mapped_column(Float, nullable=True)
    headroom: Mapped[float | None] = mapped_column(Float, nullable=True)
    governing_boundary: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), index=True)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    coverage_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    coverage_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    monitoring_run: Mapped[LimitMonitoringRun] = relationship(
        back_populates="evaluations"
    )
    limit_version: Mapped[RiskLimitVersion] = relationship(
        back_populates="evaluations"
    )

    __table_args__ = (
        UniqueConstraint(
            "monitoring_run_id",
            "limit_version_id",
            "scope_key",
            name="uq_limit_evaluations_run_version_scope",
        ),
    )


class LimitIncident(Base):
    __tablename__ = "limit_incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id"),
        index=True,
    )
    risk_limit_id: Mapped[int] = mapped_column(
        ForeignKey("risk_limits.id"), index=True
    )
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(200), nullable=False)
    scope_label: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), index=True)
    first_evaluation_id: Mapped[int | None] = mapped_column(
        ForeignKey("limit_evaluations.id"), nullable=True
    )
    last_evaluation_id: Mapped[int | None] = mapped_column(
        ForeignKey("limit_evaluations.id"), nullable=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    waived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    assignee: Mapped[str | None] = mapped_column(String(120), nullable=True)
    waiver_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    waiver_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    risk_limit: Mapped[RiskLimit] = relationship(back_populates="incidents")
    events: Mapped[list["LimitIncidentEvent"]] = relationship(
        back_populates="incident",
        cascade="all, delete-orphan",
        order_by=lambda: (
            LimitIncidentEvent.created_at,
            LimitIncidentEvent.id,
        ),
    )

    __table_args__ = (
        Index(
            "uq_limit_incidents_active_episode",
            "portfolio_id",
            "risk_limit_id",
            "scope_key",
            unique=True,
            sqlite_where=text(
                "status IN ('open', 'acknowledged', 'assigned', 'waived')"
            ),
            postgresql_where=text(
                "status IN ('open', 'acknowledged', 'assigned', 'waived')"
            ),
        ),
    )


class LimitIncidentEvent(Base):
    __tablename__ = "limit_incident_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("limit_incidents.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluation_id: Mapped[int | None] = mapped_column(
        ForeignKey("limit_evaluations.id"), nullable=True
    )
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    persona: Mapped[str | None] = mapped_column(String(40), nullable=True)
    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audit_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    incident: Mapped[LimitIncident] = relationship(back_populates="events")


class TaskRun(Base):
    __tablename__ = "task_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(
        String(40), default=TaskStatus.QUEUED.value, index=True
    )
    portfolio_id: Mapped[int | None] = mapped_column(
        ForeignKey("portfolios.id"), index=True, nullable=True
    )
    risk_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("risk_runs.id"), index=True, nullable=True
    )
    greeks_landscape_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("greek_landscape_runs.id"), index=True, nullable=True
    )
    scenario_test_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenario_test_runs.id"), index=True, nullable=True
    )
    backtest_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("backtest_runs.id"), index=True, nullable=True
    )
    report_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("report_jobs.id"), index=True, nullable=True
    )
    limit_monitoring_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("limit_monitoring_runs.id"), index=True, nullable=True
    )
    parent_thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_threads.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(String(120), nullable=True)
    result_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    portfolio: Mapped["Portfolio | None"] = relationship(back_populates="task_runs")
    risk_run: Mapped["RiskRun | None"] = relationship(back_populates="task_runs")
    greeks_landscape_run: Mapped["GreekLandscapeRun | None"] = relationship(
        back_populates="task_runs"
    )
    scenario_test_run: Mapped["ScenarioTestRun | None"] = relationship(
        back_populates="task_runs"
    )
    backtest_run: Mapped["BacktestRun | None"] = relationship(back_populates="task_runs")
    report_job: Mapped["ReportJob | None"] = relationship(back_populates="task_runs")
    limit_monitoring_run: Mapped["LimitMonitoringRun | None"] = relationship(
        back_populates="task_runs"
    )


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(80))
    symbol: Mapped[str] = mapped_column(String(80), index=True)
    asset_class: Mapped[str] = mapped_column(String(40), default="equity")
    valuation_date: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MarketDataProfile(Base):
    __tablename__ = "market_data_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    underlying_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(80), default="akshare")
    symbol: Mapped[str] = mapped_column(String(80), index=True)
    asset_class: Mapped[str] = mapped_column(String(40), default="index")
    start_date: Mapped[str] = mapped_column(String(20))
    end_date: Mapped[str] = mapped_column(String(20))
    adjust: Mapped[str] = mapped_column(String(20), default="qfq")
    valuation_date: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )
    underlying_record: Mapped["Underlying | None"] = relationship(
        back_populates="market_data_profiles"
    )


class RFQ(Base):
    __tablename__ = "rfqs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_name: Mapped[str] = mapped_column(String(160), default="Demo Client")
    channel: Mapped[str] = mapped_column(String(40), default="form")
    status: Mapped[str] = mapped_column(
        String(40), default=RfqStatus.PENDING_APPROVAL.value
    )
    request_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    quote_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    approved_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    approvals: Mapped[list["Approval"]] = relationship(
        back_populates="rfq", cascade="all, delete-orphan"
    )
    quote_versions: Mapped[list["RFQQuoteVersion"]] = relationship(
        back_populates="rfq",
        cascade="all, delete-orphan",
        order_by="RFQQuoteVersion.version.desc()",
    )
    booked_positions: Mapped[list["Position"]] = relationship(back_populates="rfq")


class RFQQuoteVersion(Base):
    __tablename__ = "rfq_quote_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rfq_id: Mapped[int] = mapped_column(ForeignKey("rfqs.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    quote_mode: Mapped[str] = mapped_column(String(20), default="solve")
    status: Mapped[str] = mapped_column(String(40), default=RfqStatus.SUBMITTED.value)
    request_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    quote_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(120), default="desk_user")
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    rfq: Mapped[RFQ] = relationship(back_populates="quote_versions")
    booked_positions: Mapped[list["Position"]] = relationship(
        back_populates="rfq_quote_version"
    )


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rfq_id: Mapped[int] = mapped_column(ForeignKey("rfqs.id"), index=True)
    decision: Mapped[str] = mapped_column(String(40))
    approver: Mapped[str] = mapped_column(String(120), default="trader")
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    rfq: Mapped[RFQ] = relationship(back_populates="approvals")


class ReportJob(Base):
    __tablename__ = "report_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_type: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(
        String(40), default=ReportStatus.COMPLETED.value
    )
    request_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    artifact_paths: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    task_runs: Mapped[list["TaskRun"]] = relationship(back_populates="report_job")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    actor: Mapped[str] = mapped_column(String(120), default="system")
    subject_type: Mapped[str] = mapped_column(String(80))
    subject_id: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class HedgeMapEntry(Base):
    __tablename__ = "hedge_map_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    underlying_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id"), index=True
    )
    instrument_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id"), index=True, nullable=True
    )
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    contract_code: Mapped[str] = mapped_column(String(80), nullable=False)
    family: Mapped[str] = mapped_column(String(40), nullable=False)
    series_root: Mapped[str] = mapped_column(String(40), nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(20), nullable=False)
    option_type: Mapped[str | None] = mapped_column(String(4), nullable=True)
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    reconcile_status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active", nullable=False
    )
    marked_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    marked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)  # creation-time only
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "underlying_id", "exchange", "contract_code",
            name="uq_hedge_map_entries_underlying_contract",
        ),
    )


class HedgeBand(Base):
    __tablename__ = "hedge_bands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    underlying_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id"), nullable=True
    )
    delta_cash_band: Mapped[float] = mapped_column(Float, nullable=False)
    gamma_cash_band: Mapped[float] = mapped_column(Float, nullable=False)
    vega_band: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="CNY", server_default="CNY", nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("underlying_id", name="uq_hedge_bands_underlying"),
        # A standard UNIQUE treats NULLs as distinct, so it does NOT prevent two
        # portfolio-wide defaults rows (underlying_id IS NULL). This partial index
        # over a constant forces every defaults row to collide on the same value,
        # capping it at one. Per-underlying rows are covered by the constraint above.
        Index(
            "uq_hedge_bands_default",
            text("1"),
            unique=True,
            sqlite_where=text("underlying_id IS NULL"),
        ),
    )


class ArenaRun(Base):
    __tablename__ = "arena_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    workflow_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    model_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    weights: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    trials: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    matches: Mapped[list["ArenaMatch"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class ArenaMatch(Base):
    __tablename__ = "arena_match"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("arena_run.id"), nullable=False, index=True)
    workflow_id: Mapped[str] = mapped_column(String, nullable=False)
    model_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    objective_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    judged_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    judge_missing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Per-check objective + per-rubric judge breakdown behind the aggregate
    # scores, so the UI can show exactly where the model won/lost points.
    score_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    transcript_path: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    run: Mapped["ArenaRun"] = relationship(back_populates="matches")

    __table_args__ = (
        UniqueConstraint(
            "run_id", "workflow_id", "model_id",
            name="uq_arena_match_run_workflow_model",
        ),
    )


@event.listens_for(OrmSession, "before_flush")
def _protect_risk_limit_history_from_deletion(
    session: OrmSession,
    _flush_context,
    _instances,
) -> None:
    for obj in session.deleted:
        if isinstance(obj, RiskLimit):
            raise RiskLimitHistoryDeletionError(
                "risk limit identities cannot be deleted"
            )
        if isinstance(obj, RiskLimitVersion):
            raise RiskLimitHistoryDeletionError(
                "risk limit versions cannot be deleted"
            )


@event.listens_for(OrmSession, "do_orm_execute")
def _protect_risk_limit_history_from_bulk_deletion(
    execute_state: ORMExecuteState,
) -> None:
    if not execute_state.is_delete:
        return

    mapper = execute_state.bind_mapper
    model = mapper.class_ if mapper is not None else None
    table_name = getattr(
        getattr(execute_state.statement, "table", None),
        "name",
        None,
    )
    if model is RiskLimit or table_name == RiskLimit.__tablename__:
        raise RiskLimitHistoryDeletionError(
            "risk limit identities cannot be deleted"
        )
    if model is RiskLimitVersion or table_name == RiskLimitVersion.__tablename__:
        raise RiskLimitHistoryDeletionError(
            "risk limit versions cannot be deleted"
        )


@event.listens_for(OrmSession, "before_flush")
def _protect_limit_incident_events_from_mutation(
    session: OrmSession,
    _flush_context,
    _instances,
) -> None:
    for obj in session.deleted:
        if isinstance(obj, LimitIncident):
            raise LimitIncidentHistoryDeletionError(
                "limit incident history cannot be deleted"
            )
        if isinstance(obj, LimitIncidentEvent):
            raise LimitIncidentEventMutationError(
                "limit incident events are append-only"
            )
    for obj in session.dirty:
        if isinstance(obj, LimitIncidentEvent) and session.is_modified(
            obj,
            include_collections=False,
        ):
            raise LimitIncidentEventMutationError(
                "limit incident events are append-only"
            )


@event.listens_for(OrmSession, "do_orm_execute")
def _protect_limit_incident_events_from_bulk_mutation(
    execute_state: ORMExecuteState,
) -> None:
    if not (execute_state.is_update or execute_state.is_delete):
        return
    mapper = execute_state.bind_mapper
    model = mapper.class_ if mapper is not None else None
    table_name = getattr(
        getattr(execute_state.statement, "table", None),
        "name",
        None,
    )
    if execute_state.is_delete and (
        model is LimitIncident or table_name == LimitIncident.__tablename__
    ):
        raise LimitIncidentHistoryDeletionError(
            "limit incident history cannot be deleted"
        )
    if (
        model is LimitIncidentEvent
        or table_name == LimitIncidentEvent.__tablename__
    ):
        raise LimitIncidentEventMutationError(
            "limit incident events are append-only"
        )


@event.listens_for(OrmSession, "before_flush")
def _scope_legacy_agent_messages(
    session: OrmSession,
    _flush_context,
    _instances,
) -> None:
    """Populate nullable workflow/session columns on legacy message inserts."""
    for obj in list(session.new):
        if not isinstance(obj, AgentMessage):
            continue
        if obj.workflow_id is not None:
            continue
        if obj.thread_id is None:
            continue
        with session.no_autoflush:
            thread = session.get(AgentThread, obj.thread_id)
            if thread is None or thread.active_workflow_id is None:
                continue
            agent_session = (
                session.query(AgentSession)
                .filter(
                    AgentSession.workflow_id == thread.active_workflow_id,
                    AgentSession.persona == "orchestrator",
                    AgentSession.status == "active",
                )
                .order_by(AgentSession.episode_id.desc(), AgentSession.id.desc())
                .first()
            )
        obj.workflow_id = thread.active_workflow_id
        obj.session_id = agent_session.id if agent_session else None


# ---------------------------------------------------------------------------
# IM Gateway tables (Task 1)
# ---------------------------------------------------------------------------


class GatewayBinding(Base):
    """One binding = one IM account linked to one desk persona.

    A binding is active when status='active'.  The partial unique index
    ``uq_gateway_binding_active`` enforces at-most-one active binding per
    (provider, external_account_id, workspace_id) triple; revoked rows are
    kept for audit.
    """

    __tablename__ = "gateway_binding"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    external_account_id: Mapped[str] = mapped_column(String, nullable=False)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False, default="", server_default="")
    desk_user: Mapped[str] = mapped_column(String, nullable=False)
    persona: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active", server_default="active")  # active|revoked
    bound_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    supersedes_binding_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("gateway_binding.id"), nullable=True
    )

    __table_args__ = (
        Index(
            "uq_gateway_binding_active",
            "provider",
            "external_account_id",
            "workspace_id",
            unique=True,
            sqlite_where=text("status='active'"),
            postgresql_where=text("status='active'"),
        ),
    )


class GatewayLinkingCode(Base):
    """One-time pairing code issued by the desk to a new IM user.

    The code is unique; redeemed_by_binding_id is set on redemption.
    """

    __tablename__ = "gateway_linking_code"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    desk_user: Mapped[str] = mapped_column(String, nullable=False)
    persona: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    redeemed_by_binding_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("gateway_binding.id"), nullable=True
    )
    issued_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class GatewayThreadMap(Base):
    """Maps an IM chat (binding + chat_id) to an agent thread."""

    __tablename__ = "gateway_thread_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    binding_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("gateway_binding.id"), nullable=False
    )
    chat_id: Mapped[str] = mapped_column(String, nullable=False)
    thread_id: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("binding_id", "chat_id", name="uq_gateway_thread_map_binding_chat"),
    )


class GatewayInboundSeen(Base):
    """Deduplication table for inbound IM events.

    Each unique (connector, workspace_id, provider_event_id) triple is
    claimed by at most one worker (owner_token).
    """

    __tablename__ = "gateway_inbound_seen"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connector: Mapped[str] = mapped_column(String, nullable=False)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False, default="", server_default="")
    provider_event_id: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="processing", server_default="processing")  # processing|done|failed
    owner_token: Mapped[str | None] = mapped_column(String, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "connector",
            "workspace_id",
            "provider_event_id",
            name="uq_gateway_inbound_seen",
        ),
    )


class GatewayCardAction(Base):
    """Pending interactive-card action that awaits a trader decision.

    The token is globally unique (used in callback URLs).  The four-column
    constraint prevents duplicate pending actions for the same logical choice.
    """

    __tablename__ = "gateway_card_action"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    out_connector: Mapped[str] = mapped_column(String, nullable=False)
    out_workspace_id: Mapped[str] = mapped_column(String, nullable=False, default="", server_default="")
    out_chat_id: Mapped[str] = mapped_column(String, nullable=False)
    out_message_id: Mapped[str] = mapped_column(String, nullable=False)
    binding_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("gateway_binding.id"), nullable=False
    )
    thread_id: Mapped[int] = mapped_column(Integer, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action_id: Mapped[str] = mapped_column(String, nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=False)  # confirm|dismiss
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending", server_default="pending")  # pending|resolving|resolved|failed|unknown
    resolved_by_binding_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "thread_id",
            "message_id",
            "action_id",
            "decision",
            name="uq_gateway_card_action_action",
        ),
    )


class GatewayWorkerLock(Base):
    """Singleton advisory lock for the gateway background worker.

    id is always 1; UPSERT pattern enforces singleton semantics.
    """

    __tablename__ = "gateway_worker_lock"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1, server_default="1")
    owner_token: Mapped[str] = mapped_column(String, nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

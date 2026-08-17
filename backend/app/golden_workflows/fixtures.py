from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.golden_workflows.schema import (
    DuplicateAliasError,
    UnknownSeedNamespaceError,
    UnresolvedAliasError,
    WorkflowError,
)

# Single frozen valuation instant for the golden/arena path. Every time- or
# market-dependent producer on this path resolves as-of this date so harvested
# fixture truth is reproducible (Spec A). NOT used by the production desk.
SEED_ACCOUNTING_DATE = datetime(2026, 6, 24)

# Recognised seed namespaces and the set of keys each row must carry.
# "alias" is always required.  Extra keys (column values) are passed
# through to the ORM constructor unchanged.
# ``id`` is optional for portfolios/pricing_profiles: omit it to let the DB
# autoincrement so the same fixture can be re-seeded (once per arena match)
# without primary-key/unique clashes. Fixtures that pin ``id`` still validate.
_NAMESPACES: dict[str, set[str]] = {
    "portfolios": {"alias", "name"},
    "positions": {"alias", "portfolio", "underlying", "product_type", "quantity"},
    "pricing_profiles": {"alias", "name", "valuation_date"},
    # A profile-bound batch-pricing run resolves r/q/vol from the profile's
    # parameter rows (matched by ``symbol`` == position.underlying). Seed one
    # complete row per underlying so live pricing produces non-empty Greeks.
    "pricing_parameter_rows": {"alias", "profile", "symbol"},
    "risk_runs": {"alias", "portfolio"},
    "rfqs": {"alias", "status"},
    "reports": {"alias", "report_type"},
    # Underlying registry rows (2026-07-17, Run #26 follow-up): seeded ENSURE-
    # by-symbol (idempotent) so a pre-existing desk instrument is reused, never
    # duplicated — a blind insert would break ensure_underlying's one_or_none.
    # "tags": ["underlying"] passes the book_position registration gate.
    "instruments": {"alias", "symbol"},
    # Quote-store rows pinning a real close for the seeded instrument (as_of <=
    # the profile's valuation date), so profile-bound pricing/risk reads the
    # RIGHT market quote instead of the env-default fallback spot 100. Always
    # stamped source=ARENA_MARKET_SOURCE so the arena purge can reclaim them.
    "market_quotes": {"alias", "instrument", "as_of", "price"},
    # Limits family (risk-limit-breach-day). risk_limits/-versions are seeded
    # ENSURE-by-key (idempotent upsert) because those tables are protected-
    # immortal (models._protect_risk_limit_history_* has NO arena exemption)
    # and `key` is globally unique — a blind insert dies on the second match.
    # Keys MUST use the reserved "arena-" prefix and an existing row is only
    # updated when provably arena-owned (created_by_actor == "arena_seed").
    "risk_limits": {"alias", "key", "name", "category", "owner"},
    "risk_limit_versions": {
        "alias", "risk_limit", "version", "metric_kind", "source_kind",
        "scope_type", "aggregation", "transform", "comparator", "unit",
    },
    "limit_monitoring_runs": {
        "alias", "portfolio", "trigger", "mode", "valuation_as_of",
        "source_policy", "status",
    },
    "limit_source_references": {
        "alias", "monitoring_run", "source_kind", "source_status",
    },
    "limit_evaluations": {
        "alias", "monitoring_run", "limit_version", "scope", "scope_portfolio",
        "status",
    },
    "limit_incidents": {
        "alias", "portfolio", "risk_limit", "scope", "scope_portfolio",
        "severity", "status",
    },
    "limit_incident_events": {"alias", "incident", "event_type", "actor"},
    # Ops-settlement-day family (2026-08-13). Events seeded here bypass
    # create_lifecycle_event's family allowlist (direct ORM insert), so
    # tests/test_ops_settlement_day_workflow.py pins that every seeded
    # event_type is in PRODUCT_LIFECYCLE_EVENTS for its position's family —
    # satisfiability ≠ reachability.
    "position_lifecycle_events": {"alias", "position", "event_type"},
    "settlement_cashflows": {
        "alias", "position", "lifecycle_event", "leg_key", "direction", "status",
    },
}

# FK edges: {child_ns: {field_in_row: parent_ns}}. The positions.rfq edge is
# OPTIONAL — a position row may omit it (validated by the skip-when-absent branch
# in load_fixtures). The instrument edges on positions/pricing_parameter_rows are
# likewise optional (workflows without seeded instruments simply omit them).
_FK: dict[str, dict[str, str]] = {
    "positions": {"portfolio": "portfolios", "rfq": "rfqs", "instrument": "instruments"},
    "pricing_parameter_rows": {"profile": "pricing_profiles", "instrument": "instruments"},
    "risk_runs": {"portfolio": "portfolios"},
    "market_quotes": {"instrument": "instruments"},
    "risk_limit_versions": {"risk_limit": "risk_limits"},
    "limit_monitoring_runs": {
        "portfolio": "portfolios",
        "pricing_profile": "pricing_profiles",
    },
    "limit_source_references": {
        "monitoring_run": "limit_monitoring_runs",
        "risk_run": "risk_runs",
    },
    "limit_evaluations": {
        "monitoring_run": "limit_monitoring_runs",
        "limit_version": "risk_limit_versions",
        "scope_portfolio": "portfolios",
    },
    "limit_incidents": {
        "portfolio": "portfolios",
        "risk_limit": "risk_limits",
        "scope_portfolio": "portfolios",
        "first_evaluation": "limit_evaluations",
        "last_evaluation": "limit_evaluations",
    },
    "limit_incident_events": {
        "incident": "limit_incidents",
        "evaluation": "limit_evaluations",
    },
    "position_lifecycle_events": {"position": "positions"},
    "settlement_cashflows": {
        "position": "positions",
        "lifecycle_event": "position_lifecycle_events",
    },
}

# Insertion order so FK parents exist before children (rfqs before positions).
_INSERT_ORDER = [
    "instruments", "portfolios", "reports", "pricing_profiles",
    "pricing_parameter_rows", "market_quotes", "rfqs", "positions",
    "position_lifecycle_events", "settlement_cashflows", "risk_runs",
    "risk_limits", "risk_limit_versions", "limit_monitoring_runs",
    "limit_source_references", "limit_evaluations", "limit_incidents",
    "limit_incident_events",
]

# Origin tag stamped on arena-seeded market data (backtest history) so it is never
# confused with production/live-fetched rows. Consumed by determinism.py.
ARENA_MARKET_SOURCE = "arena_seed"


def parse_seed_datetime(value):
    """Parse an optional fixture datetime: ISO string (date or datetime) → datetime.

    Public on purpose: ``arena/runner.py`` needs this to evaluate the same
    ``valuation_as_of``/``valuation_date`` fixture fields the seed rows carry
    (``_assert_no_foreign_active_limits``), and importing a leading-underscore
    helper across a package boundary is a private-API leak.
    """
    if value is None or isinstance(value, datetime):
        return value
    raw = str(value)
    if len(raw) == 10:  # "YYYY-MM-DD"
        return datetime.strptime(raw, "%Y-%m-%d")
    return datetime.fromisoformat(raw)


# Back-compat alias: this module's own call sites below predate the public
# rename and are unaffected either way.
_seed_datetime = parse_seed_datetime

# Column allowlist for the risk_runs seed namespace.  Only keys in this set
# (beyond the always-excluded "alias" / "portfolio") are forwarded to the
# RiskRun ORM constructor; descriptive fixture fields (e.g. "as_of") are
# silently dropped.
_RISK_RUN_COLS: frozenset[str] = frozenset({
    "method", "status", "metrics", "scenario_cells",
    "resolved_position_ids", "pricing_parameter_profile_id",
    "engine_config_id", "market_snapshot_id",
    # created_at is seedable so a fixture can pin a genuinely-stale run (computed
    # >24h ago); without it the row defaults to now and reads as current.
    "created_at",
})

# Column allowlist for the rfqs seed namespace (beyond "alias"/"status").
_RFQ_COLS: frozenset[str] = frozenset({
    "client_name", "channel", "status", "request_payload",
    "quote_payload", "approved_response",
})

# Column allowlist for the reports seed namespace (beyond the always-excluded "alias").
_REPORT_COLS: frozenset[str] = frozenset({
    "report_type", "status", "request_payload", "result_payload", "artifact_paths",
})


@dataclass
class ReplayEntry:
    ai: dict
    tool_results: list[dict]
    skills_routed: list[str]
    artifacts: list[dict]
    response_text: str


@dataclass
class FixtureBundle:
    seed: dict
    replay: dict[str, ReplayEntry]
    seed_map: dict[str, Any] = field(default_factory=dict)


def load_fixtures(path: Path) -> FixtureBundle:
    """Parse and validate *path* (a ``*.fixtures.json`` file).

    Raises:
        WorkflowError: schema_version is not 1.
        UnknownSeedNamespaceError: an unrecognised top-level seed namespace.
        DuplicateAliasError: two rows share the same alias within a namespace.
        UnresolvedAliasError: a FK alias field references a non-existent parent alias.
        WorkflowError: a replay entry contains a ``tool_call_id`` with no
            matching ``ai.tool_calls`` entry.
    """
    data = json.loads(Path(path).read_text())
    if data.get("schema_version") != 1:
        raise WorkflowError(f"{path}: schema_version must be 1")

    seed = data.get("seed", {})
    seed_map: dict[str, Any] = {}
    # alias sets per namespace — built up while we scan rows
    aliases: dict[str, set[str]] = {}

    for ns, rows in seed.items():
        if ns not in _NAMESPACES:
            raise UnknownSeedNamespaceError(ns)
        aliases[ns] = set()
        for row in rows:
            required = _NAMESPACES[ns]
            missing = required - row.keys()
            if missing:
                raise WorkflowError(f"{ns} row missing required keys: {missing}")
            a = row["alias"]
            if a in aliases[ns]:
                raise DuplicateAliasError(f"{ns}.{a}")
            aliases[ns].add(a)
            for fld, val in row.items():
                seed_map[f"$seed.{ns}.{a}.{fld}"] = val

    # Validate FK references now that all alias sets are populated.
    for ns, fks in _FK.items():
        for row in seed.get(ns, []):
            for fld, target_ns in fks.items():
                if fld not in row:
                    continue  # optional FK (e.g. positions.rfq) — absent is fine
                ref = row.get(fld)
                if ref not in aliases.get(target_ns, set()):
                    raise UnresolvedAliasError(
                        f"{ns}.{row.get('alias')}.{fld} -> {target_ns}.{ref}"
                    )

    # Validate replay tool_call_id integrity.
    replay: dict[str, ReplayEntry] = {}
    for ref, entry in data.get("replay", {}).items():
        ai = entry.get("ai", {})
        call_ids = {c.get("id") for c in ai.get("tool_calls", [])}
        for r in entry.get("tool_results", []):
            tcid = r.get("tool_call_id")
            if tcid not in call_ids:
                raise WorkflowError(
                    f"replay {ref!r}: tool_call_id {tcid!r} has no matching "
                    "ai.tool_call"
                )
        replay[ref] = ReplayEntry(
            ai=ai,
            tool_results=entry.get("tool_results", []),
            skills_routed=entry.get("skills_routed", []),
            artifacts=entry.get("artifacts", []),
            response_text=entry.get("response_text", ""),
        )

    return FixtureBundle(seed=seed, replay=replay, seed_map=seed_map)


def _write_seeded_artifact_bodies(row: dict[str, Any]) -> None:
    """Materialize a seeded report's referenced artifact FILES.

    ``artifact_paths`` is only a JSON column: seeding a row does NOT create a file.
    The deep_agent backend mounts ``settings.artifact_dir`` at ``/artifacts/`` and
    ``_shaping.normalize_artifact_paths`` reduces each stored path to
    ``/artifacts/<basename>``, so the body must land at
    ``artifact_dir/<basename>`` to be readable by the agent.

    Bodies come from an ``artifact_bodies`` map keyed the same as ``artifact_paths``
    (``{"markdown": "…text…"}``). Absent key → nothing written (backward compatible:
    a fixture that declares no bodies behaves exactly as before). Overwrites on
    every match so a re-seed is idempotent.
    """
    bodies = row.get("artifact_bodies") or {}
    paths = row.get("artifact_paths") or {}
    if not bodies or not paths:
        return

    from app.config import get_settings

    root = Path(get_settings().artifact_dir)
    root.mkdir(parents=True, exist_ok=True)
    for key, body in bodies.items():
        declared = paths.get(key)
        if not declared or not isinstance(body, str):
            continue
        basename = Path(str(declared)).name
        if not basename:
            continue
        (root / basename).write_text(body, encoding="utf-8")


def _resolve_inserted_ids(value: Any, ids: dict[str, dict[str, int]]) -> Any:
    """Replace ``$seed.<ns>.<alias>.id`` tokens with the id the DB actually assigned.

    Recurses through dicts and lists so a token resolves at ANY depth — a seed
    row's own FK column, or a provenance id embedded deep in a JSON blob.

    Distinct from the load-time ``$seed`` map in :func:`load_fixtures`, which
    substitutes values *declared in the fixture file* into assertions. That map
    can only see an id the fixture PINNED, and a pinned id is not re-seedable in
    every namespace: the arena purge REFUSES to delete a pricing profile that a
    real (non-arena) run priced against — retiring it instead, to preserve that
    run's provenance — so the row survives still holding its id and the next
    match's insert dies on a UNIQUE violation. Omitting the id fixes the
    collision but leaves anything that pointed at it dangling; this resolver is
    how those references follow the real row.

    Only ``.id`` is resolvable here (that is all ``ids`` records). A token naming
    an unknown namespace/alias is left untouched rather than silently nulled, so
    a typo surfaces as a visible ``$seed...`` string instead of a missing FK.
    """
    if isinstance(value, dict):
        return {k: _resolve_inserted_ids(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_inserted_ids(v, ids) for v in value]
    if isinstance(value, str) and value.startswith("$seed."):
        parts = value.split(".")
        if len(parts) == 4 and parts[3] == "id":
            _, ns, alias, _field = parts
            resolved = ids.get(ns, {}).get(alias)
            if resolved is not None:
                return resolved
    return value


def apply_seed(bundle: FixtureBundle, session) -> dict[str, dict[str, int]]:
    """Insert all seed rows via ORM models in FK-safe order.

    Honors explicit ``id`` fields (caller's responsibility to avoid PK clashes
    against existing data). Resolves FK alias fields to the inserted parent's
    primary key. Commits once at the end.

    Returns
    -------
    dict[namespace][alias] -> inserted row id
    """
    from app import models  # late import: test isolation, not available at import time

    ids: dict[str, dict[str, int]] = {ns: {} for ns in bundle.seed}

    def _parent_id(ns: str, alias: str) -> int:
        return ids[ns][alias]

    for ns in _INSERT_ORDER:
        rows = bundle.seed.get(ns, [])
        for row in rows:
            # Resolve any "$seed.<ns>.<alias>.<field>" token against the id the DB
            # ACTUALLY assigned, at any nesting depth. The load-time $seed map
            # (load_fixtures) resolves against values DECLARED in the fixture file,
            # so it can only see an id the fixture PINNED — and pinning an id is
            # exactly what a re-seed cannot rely on (see the pricing_profiles note
            # below). This late pass is the seam for referencing an autoincremented
            # parent, including ids buried inside a JSON blob such as a risk run's
            # metrics provenance. _INSERT_ORDER guarantees the parent is already in.
            row = _resolve_inserted_ids(row, ids)
            if ns == "portfolios":
                # ``id`` is optional: omit it to let the DB autoincrement, so the
                # same fixture can be seeded repeatedly (e.g. once per arena match)
                # without primary-key clashes. Explicit ids are still honoured for
                # fixtures that pin them (e.g. $seed.<ns>.<alias>.id references).
                pkw = {"name": row["name"]}
                if "id" in row:
                    pkw["id"] = row["id"]
                obj = models.Portfolio(**pkw)

            elif ns == "pricing_profiles":
                # Pass through any extra keys; default valuation_date if absent.
                extra = {
                    k: v
                    for k, v in row.items()
                    if k != "alias"
                }
                if "valuation_date" not in extra:
                    extra["valuation_date"] = datetime.now(tz=timezone.utc)
                elif isinstance(extra["valuation_date"], str):
                    # Parse ISO date/datetime strings to datetime objects
                    vd = extra["valuation_date"]
                    if len(vd) == 10:  # "YYYY-MM-DD"
                        extra["valuation_date"] = datetime.strptime(vd, "%Y-%m-%d")
                    else:
                        extra["valuation_date"] = datetime.fromisoformat(vd)
                obj = models.PricingParameterProfile(**extra)

            elif ns == "pricing_parameter_rows":
                profile_id = _parent_id("pricing_profiles", row["profile"])
                # source_trade_id is NOT NULL on the model; default to "" so the
                # resolver falls through to the symbol (underlying) match path.
                extra = {
                    k: v
                    for k, v in row.items()
                    if k not in ("alias", "profile", "instrument")
                }
                extra.setdefault("source_trade_id", "")
                if "instrument" in row:
                    extra["instrument_id"] = _parent_id("instruments", row["instrument"])
                obj = models.PricingParameterRow(profile_id=profile_id, **extra)

            elif ns == "instruments":
                # ENSURE-by-symbol (idempotent), never a blind insert: the desk DB
                # may already carry the instrument, and a duplicate breaks
                # ensure_underlying's one_or_none lookup. ensure_underlying stamps
                # source=ARENA_MARKET_SOURCE only when it CREATES the row; a
                # pre-existing desk row is reused untouched apart from merging
                # the fixture's tags (e.g. ["underlying"] for the booking gate).
                from app.services.underlyings import ensure_underlying
                obj = ensure_underlying(session, row["symbol"], source=ARENA_MARKET_SOURCE)
                want_tags = sorted({*(obj.tags or []), *(row.get("tags") or [])})
                if want_tags != sorted(obj.tags or []):
                    obj.tags = want_tags
                    session.flush()

            elif ns == "market_quotes":
                instrument_id = _parent_id("instruments", row["instrument"])
                as_of = row["as_of"]
                if isinstance(as_of, str):
                    as_of = (
                        datetime.strptime(as_of, "%Y-%m-%d")
                        if len(as_of) == 10
                        else datetime.fromisoformat(as_of)
                    )
                obj = models.MarketQuote(
                    instrument_id=instrument_id,
                    as_of=as_of,
                    price=float(row["price"]),
                    price_type=row.get("price_type", "close"),
                    # Always arena-tagged so the runner purge reclaims the quote
                    # after the match — a seeded quote must never leak into the
                    # desk's live quote store as if a real fetcher wrote it.
                    source=ARENA_MARKET_SOURCE,
                    meta=row.get("meta") or {"arena_seed": True},
                )

            elif ns == "rfqs":
                # Pass through only real RFQ columns; model defaults cover the rest.
                extra = {
                    k: v
                    for k, v in row.items()
                    if k != "alias" and k in _RFQ_COLS
                }
                if "id" in row:
                    extra["id"] = row["id"]
                obj = models.RFQ(**extra)

            elif ns == "positions":
                portfolio_id = _parent_id("portfolios", row["portfolio"])
                # Pass through any extra keys (e.g. engine_name) the test provides.
                # The optional "rfq" alias resolves to Position.rfq_id; the
                # optional "instrument" alias to Position.underlying_id.
                extra = {
                    k: v
                    for k, v in row.items()
                    if k not in ("alias", "portfolio", "rfq", "instrument")
                }
                if "rfq" in row:
                    extra["rfq_id"] = _parent_id("rfqs", row["rfq"])
                if "instrument" in row:
                    extra["underlying_id"] = _parent_id("instruments", row["instrument"])
                obj = models.Position(portfolio_id=portfolio_id, **extra)

            elif ns == "position_lifecycle_events":
                position_id = _parent_id("positions", row["position"])
                extra = {
                    k: v for k, v in row.items()
                    if k not in ("alias", "position")
                }
                if isinstance(extra.get("created_at"), str):
                    extra["created_at"] = datetime.fromisoformat(extra["created_at"])
                obj = models.PositionLifecycleEvent(position_id=position_id, **extra)

            elif ns == "settlement_cashflows":
                position_id = _parent_id("positions", row["position"])
                event_id = _parent_id(
                    "position_lifecycle_events", row["lifecycle_event"]
                )
                extra = {
                    k: v for k, v in row.items()
                    if k not in ("alias", "position", "lifecycle_event")
                }
                for key in ("value_date", "derived_value_date"):
                    if isinstance(extra.get(key), str):
                        extra[key] = date.fromisoformat(extra[key])
                obj = models.SettlementCashflow(
                    position_id=position_id, lifecycle_event_id=event_id, **extra
                )

            elif ns == "risk_runs":
                portfolio_id = _parent_id("portfolios", row["portfolio"])
                # Only pass columns that exist on the RiskRun model; the fixture
                # may carry descriptive fields like "as_of" that are not real ORM columns.
                extra = {
                    k: v
                    for k, v in row.items()
                    if k not in ("alias", "portfolio") and k in _RISK_RUN_COLS
                }
                # created_at maps to a DateTime column; parse ISO strings so the
                # row sorts/compares as a real timestamp (e.g. for staleness).
                ca = extra.get("created_at")
                if isinstance(ca, str):
                    extra["created_at"] = (
                        datetime.strptime(ca, "%Y-%m-%d")
                        if len(ca) == 10
                        else datetime.fromisoformat(ca)
                    )
                obj = models.RiskRun(portfolio_id=portfolio_id, **extra)

            elif ns == "reports":
                extra = {
                    k: v for k, v in row.items()
                    if k != "alias" and k in _REPORT_COLS
                }
                obj = models.ReportJob(**extra)
                # A seeded report that declares artifact_paths but has no FILE hands
                # the agent a dangling pointer: get_report returns the path (as
                # /artifacts/<basename> — see _shaping.normalize_artifact_paths),
                # read_file fails, and the model burns calls hunting a file that was
                # never written. Observed live 2026-07-26 on high-board step 7, where
                # the hunt surfaced unrelated real reports and the model's LAST
                # get_report became the wrong one, failing a grounding check it had
                # already satisfied. So a fixture that REFERENCES an artifact must
                # also CREATE it: `artifact_bodies: {<key>: <text>}` writes the body
                # to artifact_dir under the same basename the agent will resolve.
                _write_seeded_artifact_bodies(row)

            elif ns == "risk_limits":
                # ENSURE-by-key upsert: these rows are protected-immortal (the
                # deletion guards have no arena exemption) and `key` is unique,
                # so re-seeding must reuse — but ONLY a provably arena-owned
                # row. A desk-governed limit sharing the key is a fixture bug.
                if not str(row["key"]).startswith("arena-"):
                    raise WorkflowError(
                        "fixture risk_limit key must use the arena- prefix: "
                        f"{row['key']!r}"
                    )
                existing = (
                    session.query(models.RiskLimit)
                    .filter(models.RiskLimit.key == row["key"])
                    .one_or_none()
                )
                if existing is None:
                    obj = models.RiskLimit(
                        key=row["key"],
                        name=row["name"],
                        category=row["category"],
                        owner=row["owner"],
                        description=row.get("description"),
                        tags=row.get("tags") or [],
                        created_by_actor="arena_seed",
                    )
                elif existing.created_by_actor != "arena_seed":
                    raise WorkflowError(
                        f"fixture risk_limit key {row['key']!r} collides with "
                        "a non-arena limit"
                    )
                else:
                    obj = existing
                    obj.name = row["name"]
                    obj.category = row["category"]
                    obj.owner = row["owner"]

            elif ns == "risk_limit_versions":
                # Fail closed: a non-portfolio scope_type joins EVERY portfolio's
                # monitoring run (monitoring._active_versions only portfolio-filters
                # portfolio-scoped versions), so an underlying/product_family/
                # position-scoped fixture limit would be an immortal, active,
                # guard-exempt (seeded-key) row contaminating every desk book —
                # exactly the class of leak CLAUDE.md's containment doctrine warns
                # about. The sibling limit_evaluations/limit_incidents namespaces
                # already fail closed the same way.
                if row["scope_type"] != "portfolio":
                    raise WorkflowError(
                        "fixture risk_limit_versions support scope_type='portfolio' "
                        f"only, got {row['scope_type']!r}"
                    )
                limit_id = _parent_id("risk_limits", row["risk_limit"])
                existing = (
                    session.query(models.RiskLimitVersion)
                    .filter(
                        models.RiskLimitVersion.risk_limit_id == limit_id,
                        models.RiskLimitVersion.version == int(row["version"]),
                    )
                    .one_or_none()
                )
                scope_config = dict(row.get("scope_config") or {})
                if "scope_portfolios" in row:
                    scope_config["portfolio_ids"] = [
                        _parent_id("portfolios", alias)
                        for alias in row["scope_portfolios"]
                    ]
                values = {
                    "state": row.get("state", "active"),
                    "metric_kind": row["metric_kind"],
                    "source_kind": row["source_kind"],
                    "scope_type": row["scope_type"],
                    "scope_config": scope_config,
                    "aggregation": row["aggregation"],
                    "transform": row["transform"],
                    "comparator": row["comparator"],
                    "warning_lower": row.get("warning_lower"),
                    "warning_upper": row.get("warning_upper"),
                    "hard_lower": row.get("hard_lower"),
                    "hard_upper": row.get("hard_upper"),
                    "unit": row["unit"],
                    "currency": row.get("currency"),
                    "activated_at": _seed_datetime(row.get("activated_at")),
                    "effective_from": _seed_datetime(row.get("effective_from")),
                    "effective_until": _seed_datetime(row.get("effective_until")),
                }
                if existing is None:
                    obj = models.RiskLimitVersion(
                        risk_limit_id=limit_id,
                        version=int(row["version"]),
                        created_by_actor="arena_seed",
                        **values,
                    )
                else:
                    obj = existing
                    for field, value in values.items():
                        setattr(obj, field, value)
                session.add(obj)
                session.flush()
                if row.get("activate"):
                    session.get(models.RiskLimit, limit_id).active_version_id = obj.id

            elif ns == "limit_monitoring_runs":
                # Snapshot hash must be internally consistent; seed only
                # terminal statuses (a partial-unique index allows one
                # queued/running run per portfolio).
                from app.services.limits.monitoring import _snapshot_hash

                snapshot = dict(row.get("definition_snapshot") or {})
                obj = models.LimitMonitoringRun(
                    trigger=row["trigger"],
                    mode=row["mode"],
                    portfolio_id=_parent_id("portfolios", row["portfolio"]),
                    pricing_parameter_profile_id=(
                        _parent_id("pricing_profiles", row["pricing_profile"])
                        if "pricing_profile" in row
                        else None
                    ),
                    valuation_as_of=_seed_datetime(row["valuation_as_of"]),
                    source_policy=row["source_policy"],
                    max_source_age_seconds=row.get("max_source_age_seconds"),
                    status=row["status"],
                    summary=dict(row.get("summary") or {}),
                    definition_snapshot=snapshot,
                    definition_snapshot_hash=_snapshot_hash(snapshot),
                    started_at=_seed_datetime(row.get("started_at")),
                    finished_at=_seed_datetime(row.get("finished_at")),
                )

            elif ns == "limit_source_references":
                obj = models.LimitSourceReference(
                    monitoring_run_id=_parent_id(
                        "limit_monitoring_runs", row["monitoring_run"]
                    ),
                    source_kind=row["source_kind"],
                    risk_run_id=(
                        _parent_id("risk_runs", row["risk_run"])
                        if "risk_run" in row
                        else None
                    ),
                    source_status=row["source_status"],
                    is_fresh=bool(row.get("is_fresh", False)),
                    requested_parameters=dict(row.get("requested_parameters") or {}),
                    completeness_diagnostics=dict(
                        row.get("completeness_diagnostics") or {}
                    ),
                    source_valuation_at=_seed_datetime(row.get("source_valuation_at")),
                )

            elif ns == "limit_evaluations":
                scope_pid = _parent_id("portfolios", row["scope_portfolio"])
                scope_portfolio = session.get(models.Portfolio, scope_pid)
                if row["scope"] != "portfolio":  # pragma: no cover
                    raise WorkflowError(
                        "limit_evaluations fixtures support scope='portfolio' only"
                    )
                obj = models.LimitEvaluation(
                    monitoring_run_id=_parent_id(
                        "limit_monitoring_runs", row["monitoring_run"]
                    ),
                    limit_version_id=_parent_id(
                        "risk_limit_versions", row["limit_version"]
                    ),
                    scope_type="portfolio",
                    scope_key=f"portfolio:{scope_pid}",
                    scope_label=scope_portfolio.name,
                    status=row["status"],
                    observed_value=row.get("observed_value"),
                    adverse_value=row.get("adverse_value"),
                    warning_lower=row.get("warning_lower"),
                    warning_upper=row.get("warning_upper"),
                    hard_lower=row.get("hard_lower"),
                    hard_upper=row.get("hard_upper"),
                    utilization=row.get("utilization"),
                    headroom=row.get("headroom"),
                    governing_boundary=row.get("governing_boundary"),
                    reason_code=row.get("reason_code"),
                )

            elif ns == "limit_incidents":
                scope_pid = _parent_id("portfolios", row["scope_portfolio"])
                scope_portfolio = session.get(models.Portfolio, scope_pid)
                if row["scope"] != "portfolio":  # pragma: no cover
                    raise WorkflowError(
                        "limit_incidents fixtures support scope='portfolio' only"
                    )
                obj = models.LimitIncident(
                    portfolio_id=_parent_id("portfolios", row["portfolio"]),
                    risk_limit_id=_parent_id("risk_limits", row["risk_limit"]),
                    scope_type="portfolio",
                    scope_key=f"portfolio:{scope_pid}",
                    scope_label=scope_portfolio.name,
                    severity=row["severity"],
                    status=row["status"],
                    first_evaluation_id=(
                        _parent_id("limit_evaluations", row["first_evaluation"])
                        if "first_evaluation" in row
                        else None
                    ),
                    last_evaluation_id=(
                        _parent_id("limit_evaluations", row["last_evaluation"])
                        if "last_evaluation" in row
                        else None
                    ),
                )
                # Model defaults (utcnow) apply unless the fixture pins them.
                if row.get("first_seen_at"):
                    obj.first_seen_at = _seed_datetime(row["first_seen_at"])
                if row.get("last_seen_at"):
                    obj.last_seen_at = _seed_datetime(row["last_seen_at"])

            elif ns == "limit_incident_events":
                obj = models.LimitIncidentEvent(
                    incident_id=_parent_id("limit_incidents", row["incident"]),
                    event_type=row["event_type"],
                    actor=row["actor"],
                    persona=row.get("persona"),
                    mode=row.get("mode"),
                    evaluation_id=(
                        _parent_id("limit_evaluations", row["evaluation"])
                        if "evaluation" in row
                        else None
                    ),
                    payload=dict(row.get("payload") or {}),
                )

            else:  # pragma: no cover
                raise WorkflowError(f"apply_seed: unhandled namespace {ns!r}")

            session.add(obj)
            session.flush()
            ids[ns][row["alias"]] = obj.id

    session.commit()
    return ids

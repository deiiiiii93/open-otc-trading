from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
}

# Insertion order so FK parents exist before children (rfqs before positions).
_INSERT_ORDER = [
    "instruments", "portfolios", "reports", "pricing_profiles",
    "pricing_parameter_rows", "market_quotes", "rfqs", "positions", "risk_runs",
]

# Origin tag stamped on arena-seeded market data (backtest history) so it is never
# confused with production/live-fetched rows. Consumed by determinism.py.
ARENA_MARKET_SOURCE = "arena_seed"

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

            else:  # pragma: no cover
                raise WorkflowError(f"apply_seed: unhandled namespace {ns!r}")

            session.add(obj)
            session.flush()
            ids[ns][row["alias"]] = obj.id

    session.commit()
    return ids

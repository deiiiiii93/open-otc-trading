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
}

# FK edges: {child_ns: {field_in_row: parent_ns}}. The positions.rfq edge is
# OPTIONAL — a position row may omit it (validated by the skip-when-absent branch
# in load_fixtures).
_FK: dict[str, dict[str, str]] = {
    "positions": {"portfolio": "portfolios", "rfq": "rfqs"},
    "pricing_parameter_rows": {"profile": "pricing_profiles"},
    "risk_runs": {"portfolio": "portfolios"},
}

# Insertion order so FK parents exist before children (rfqs before positions).
_INSERT_ORDER = [
    "portfolios", "reports", "pricing_profiles", "pricing_parameter_rows", "rfqs", "positions", "risk_runs",
]

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
                    if k not in ("alias", "profile")
                }
                extra.setdefault("source_trade_id", "")
                obj = models.PricingParameterRow(profile_id=profile_id, **extra)

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
                # The optional "rfq" alias resolves to Position.rfq_id.
                extra = {
                    k: v
                    for k, v in row.items()
                    if k not in ("alias", "portfolio", "rfq")
                }
                if "rfq" in row:
                    extra["rfq_id"] = _parent_id("rfqs", row["rfq"])
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

            else:  # pragma: no cover
                raise WorkflowError(f"apply_seed: unhandled namespace {ns!r}")

            session.add(obj)
            session.flush()
            ids[ns][row["alias"]] = obj.id

    session.commit()
    return ids

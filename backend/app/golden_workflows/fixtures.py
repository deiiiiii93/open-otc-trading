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
_NAMESPACES: dict[str, set[str]] = {
    "portfolios": {"alias", "id", "name"},
    "positions": {"alias", "portfolio", "underlying", "product_type", "quantity"},
    "pricing_profiles": {"alias", "id", "name", "valuation_date"},
    "risk_runs": {"alias", "portfolio"},
}

# FK edges: {child_ns: {field_in_row: parent_ns}}
_FK: dict[str, dict[str, str]] = {
    "positions": {"portfolio": "portfolios"},
    "risk_runs": {"portfolio": "portfolios"},
}

# Insertion order so FK parents exist before children.
_INSERT_ORDER = ["portfolios", "pricing_profiles", "positions", "risk_runs"]


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
                obj = models.Portfolio(id=row["id"], name=row["name"])

            elif ns == "pricing_profiles":
                # Pass through any extra keys; default valuation_date if absent.
                extra = {
                    k: v
                    for k, v in row.items()
                    if k != "alias"
                }
                if "valuation_date" not in extra:
                    extra["valuation_date"] = datetime.now(tz=timezone.utc)
                obj = models.PricingParameterProfile(**extra)

            elif ns == "positions":
                portfolio_id = _parent_id("portfolios", row["portfolio"])
                # Pass through any extra keys (e.g. engine_name) the test provides.
                extra = {
                    k: v
                    for k, v in row.items()
                    if k not in ("alias", "portfolio")
                }
                obj = models.Position(portfolio_id=portfolio_id, **extra)

            elif ns == "risk_runs":
                portfolio_id = _parent_id("portfolios", row["portfolio"])
                extra = {
                    k: v
                    for k, v in row.items()
                    if k not in ("alias", "portfolio")
                }
                obj = models.RiskRun(portfolio_id=portfolio_id, **extra)

            else:  # pragma: no cover
                raise WorkflowError(f"apply_seed: unhandled namespace {ns!r}")

            session.add(obj)
            session.flush()
            ids[ns][row["alias"]] = obj.id

    session.commit()
    return ids

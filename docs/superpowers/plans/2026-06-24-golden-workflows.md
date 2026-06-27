# Golden Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build golden desk-workflow definitions (single source of truth) plus three consumers — deterministic regression, an LLM-arena eval with a DB-backed leaderboard, and a hyperframes demo generator — proven on one Risk-Manager flagship workflow.

**Architecture:** A `backend/app/golden_workflows/` package holds markdown+frontmatter workflow definitions with sibling `*.fixtures.json`. A Pydantic schema + assertion engine parse and evaluate them. Phase 1 replays a flagship against the existing `_ScriptedGraph`. Phase 2 runs candidate models via Zenmux in isolated per-match DBs, judged by GPT-5.5, persisted in two new tables and surfaced on a `/arena` page. Phase 3 turns a transcript into a hyperframes composition and (on-demand) an MP4.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy / Alembic / Pydantic v2 / pytest (backend); React / TypeScript / Vitest (frontend); LangGraph deep-agent; Zenmux OpenAI-compatible API; hyperframes CLI.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-24-golden-workflows-design.md` — authoritative.
- Definitions path is exactly `backend/app/golden_workflows/definitions/*.md`; fixtures are siblings (basename only, no `..`).
- `schema_version == 1` for both `GoldenWorkflow` and `*.fixtures.json` and `MatchTranscript`.
- Personas enum: `trader`, `risk_manager`, `sales`, `quant`. This cycle ships **only** `risk_manager`.
- Arg/assertion matching uses **no type coercion** (`1 != "1"`, `1 != 1.0`).
- Tool names normalize by stripping a single trailing `_tool`; collisions are errors.
- Skill registry scans `backend/app/skills/workflows/**/SKILL.md` recursively, keyed by frontmatter `name`.
- Migrations use **migration-local Core tables**, never ORM models/services (repo convention).
- Mock-by-default tests; live paths gated by `ARENA_LIVE=1` (+ `ZENMUX_API_KEY`) and `DEMO_RENDER=1`; skipped in CI otherwise.
- New migration revision id: `0032_arena_runs` (parent `0031_asian_averaging_weight`).
- Run all backend tests from the repo root with `python -m pytest`; validate in an isolated git worktree.

---

# Phase 1 — Format + Flagship + Regression Proof (freeze the format here)

### Task 1: Schema models & identifier normalization

**Files:**
- Create: `backend/app/golden_workflows/__init__.py` (empty)
- Create: `backend/app/golden_workflows/schema.py`
- Test: `tests/test_golden_workflow_schema.py`

**Interfaces:**
- Produces: `GoldenWorkflow`, `Step`, `ToolExpectation`, `Success`, and `Assertion` (a discriminated union) Pydantic models; `normalize_tool_name(name: str) -> str`; `normalize_skill(name: str) -> str`; exception base `WorkflowError` with subclasses listed in the spec §3.3.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_golden_workflow_schema.py
import pytest
from app.golden_workflows.schema import (
    GoldenWorkflow, Step, ToolExpectation, normalize_tool_name, WorkflowError,
)

def test_normalize_tool_name_strips_single_tool_suffix():
    assert normalize_tool_name("run_batch_pricing_tool") == "run_batch_pricing"
    assert normalize_tool_name("run_batch_pricing") == "run_batch_pricing"
    assert normalize_tool_name("get_tool_tool") == "get_tool"  # only one suffix

def test_tool_expectation_null_args_is_wildcard():
    te = ToolExpectation(name="run_batch_pricing", args=None)
    assert te.args is None

def test_parse_workflow_min_one_step_raises_workflow_error():
    from app.golden_workflows.schema import parse_workflow
    with pytest.raises(WorkflowError):
        parse_workflow(dict(id="x", schema_version=1, persona="risk_manager",
                            title="t", objective="o", fixtures="x.fixtures.json",
                            steps=[], success={"assertions": [], "rubric": []}))

def test_parse_workflow_empty_user_raises_workflow_error():
    from app.golden_workflows.schema import parse_workflow
    with pytest.raises(WorkflowError):
        parse_workflow(dict(id="x", schema_version=1, persona="risk_manager",
                            title="t", objective="o", fixtures="x.fixtures.json",
                            steps=[dict(user="", expected_skill="run-risk", outcome="x", replay="r1")],
                            success={"assertions": [], "rubric": []}))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_golden_workflow_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: app.golden_workflows.schema`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/golden_workflows/schema.py
from __future__ import annotations
from typing import Annotated, Any, Literal, Union
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

class WorkflowError(Exception): ...
class DuplicateWorkflowError(WorkflowError): ...
class FixturePathError(WorkflowError): ...
class MissingReplayError(WorkflowError): ...
class NarrationMismatchError(WorkflowError): ...
class UnknownToolError(WorkflowError): ...
class ToolNameCollisionError(WorkflowError): ...
class SkillNameCollisionError(WorkflowError): ...
class UnresolvedSeedRefError(WorkflowError): ...
class UnknownSeedNamespaceError(WorkflowError): ...
class DuplicateAliasError(WorkflowError): ...
class UnresolvedAliasError(WorkflowError): ...
class SeedIdConflictError(WorkflowError): ...

class UnusedReplayWarning(UserWarning): ...

def normalize_tool_name(name: str) -> str:
    return name[:-5] if name.endswith("_tool") else name

def normalize_skill(name: str) -> str:
    return name.strip().lower()

# --- assertion union ---
class _SkillRouted(BaseModel):
    type: Literal["skill_routed"]; name: str
class _SkillsRoutedSequence(BaseModel):
    type: Literal["skills_routed_sequence"]; names: list[str]
class _ToolCalled(BaseModel):
    type: Literal["tool_called"]; name: str; args: dict | None = None
class _TaskReturnedId(BaseModel):
    type: Literal["task_returned_id"]; tool: str
class _ArtifactExists(BaseModel):
    type: Literal["artifact_exists"]; kind: str
class _ResponseContains(BaseModel):
    type: Literal["response_contains"]; any_of: list[str]
class _ToolResultPath(BaseModel):
    type: Literal["tool_result_path"]; tool: str; path: str
    equals: Any | None = None; gte: float | None = None
    lte: float | None = None; is_not_null: bool | None = None
    @model_validator(mode="after")
    def _exactly_one_comparator(self):
        comps = [self.equals is not None, self.gte is not None,
                 self.lte is not None, self.is_not_null is not None]
        if sum(comps) != 1:
            raise ValueError("tool_result_path needs exactly one comparator")
        return self

Assertion = Annotated[
    Union[_SkillRouted, _SkillsRoutedSequence, _ToolCalled, _TaskReturnedId,
          _ArtifactExists, _ResponseContains, _ToolResultPath],
    Field(discriminator="type"),
]

class ToolExpectation(BaseModel):
    name: str
    args: dict | None = None

class Success(BaseModel):
    assertions: list[Assertion] = Field(default_factory=list)
    rubric: list[str] = Field(default_factory=list)

class Step(BaseModel):
    user: str = Field(min_length=1)
    expected_skill: str
    expected_tools: list[ToolExpectation] = Field(default_factory=list)
    outcome: str = Field(min_length=1)
    assertions: list[Assertion] = Field(default_factory=list)
    rubric: list[str] = Field(default_factory=list)
    replay: str

class GoldenWorkflow(BaseModel):
    id: str
    schema_version: Literal[1]
    persona: Literal["trader", "risk_manager", "sales", "quant"]
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    fixtures: str
    tags: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=1)
    success: Success
    narration: list[str] = Field(default_factory=list)  # attached by loader

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        import re
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", v):
            raise ValueError("id must be a kebab slug")
        return v

# Convert pydantic ValidationError → WorkflowError at the model boundary used by the loader.
def parse_workflow(data: dict) -> GoldenWorkflow:
    try:
        return GoldenWorkflow(**data)
    except ValidationError as e:
        raise WorkflowError(str(e)) from e
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_golden_workflow_schema.py -v`
Expected: PASS. **Construction boundary (no ambiguity):** all external construction goes through `parse_workflow(dict)`, which is the only public entry and always raises `WorkflowError` (wrapping Pydantic `ValidationError`). Direct `GoldenWorkflow(**)`/`Step(**)` is internal-only; tests assert via `parse_workflow`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/golden_workflows/__init__.py backend/app/golden_workflows/schema.py tests/test_golden_workflow_schema.py
git commit -m "feat(golden-workflows): schema models + identifier normalization"
```

---

### Task 2: Assertion evaluation engine + matching semantics + `$seed` interpolation

**Files:**
- Create: `backend/app/golden_workflows/assertions.py`
- Test: `tests/test_golden_workflow_assertions.py`

**Interfaces:**
- Consumes: the `Assertion` union and `ToolExpectation` from Task 1.
- Produces:
  - `AssertionContext` dataclass: `{response_text: str, tool_calls: list[dict], tool_results: list[dict], skills_routed: list[str], artifacts: list[dict], task_ids: list[str]}`.
  - `evaluate_assertion(a: Assertion, ctx: AssertionContext) -> tuple[bool, str]`.
  - `match_tool(exp: ToolExpectation, calls: list[dict]) -> tuple[bool, str]` (deep partial, no coercion).
  - `match_tools_subsequence(exps, calls) -> tuple[bool, str]`.
  - `resolve_seed_refs(obj: Any, seed_map: dict[str, Any]) -> Any` (exact string replacement, type-preserving).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_golden_workflow_assertions.py
from app.golden_workflows.assertions import (
    AssertionContext, evaluate_assertion, match_tool, resolve_seed_refs,
)
from app.golden_workflows.schema import ToolExpectation, _ResponseContains, _ToolResultPath, _TaskReturnedId

def ctx(**kw):
    base = dict(response_text="", tool_calls=[], tool_results=[],
                skills_routed=[], artifacts=[], task_ids=[])
    base.update(kw); return AssertionContext(**base)

def test_response_contains_case_insensitive():
    a = _ResponseContains(type="response_contains", any_of=["stale"])
    ok, _ = evaluate_assertion(a, ctx(response_text="This run is STALE."))
    assert ok

def test_arg_subset_no_coercion():
    exp = ToolExpectation(name="run_batch_pricing", args={"portfolio_id": 6})
    ok, _ = match_tool(exp, [{"name": "run_batch_pricing", "args": {"portfolio_id": 6, "method": "summary"}}])
    assert ok
    bad, _ = match_tool(exp, [{"name": "run_batch_pricing", "args": {"portfolio_id": "6"}}])
    assert not bad  # str "6" != int 6

def test_match_normalizes_tool_suffix_on_both_sides():
    exp = ToolExpectation(name="run_batch_pricing", args=None)
    ok, _ = match_tool(exp, [{"name": "run_batch_pricing_tool", "args": {}}])
    assert ok  # observed '_tool' suffix still matches the expectation

def test_tool_result_path_lte():
    a = _ToolResultPath(type="tool_result_path", tool="get_scenario_test_run", path="pnl", lte=0)
    ok, _ = evaluate_assertion(a, ctx(tool_results=[{"name": "get_scenario_test_run", "content": {"pnl": -1200.0}}]))
    assert ok

def test_task_returned_id_reads_content_task_id():
    a = _TaskReturnedId(type="task_returned_id", tool="run_batch_pricing")
    ok, _ = evaluate_assertion(a, ctx(tool_results=[{"name": "run_batch_pricing", "content": {"task_id": "task_9"}}]))
    assert ok

def test_resolve_seed_refs_preserves_type():
    out = resolve_seed_refs({"portfolio_id": "$seed.portfolios.control.id"},
                            {"$seed.portfolios.control.id": 6})
    assert out == {"portfolio_id": 6} and isinstance(out["portfolio_id"], int)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_golden_workflow_assertions.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/golden_workflows/assertions.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass
class AssertionContext:
    response_text: str
    tool_calls: list[dict]
    tool_results: list[dict]
    skills_routed: list[str]
    artifacts: list[dict]
    task_ids: list[str]

def _exact(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if type(a) is not type(b):
        return False
    return a == b

def _deep_subset(expected: Any, actual: Any, path: str) -> tuple[bool, str]:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False, f"{path}: expected object"
        for k, v in expected.items():
            if k not in actual:
                return False, f"{path}.{k}: missing"
            ok, msg = _deep_subset(v, actual[k], f"{path}.{k}")
            if not ok: return False, msg
        return True, ""
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return False, f"{path}: list length mismatch"
        for i, (e, a) in enumerate(zip(expected, actual)):
            ok, msg = _deep_subset(e, a, f"{path}.{i}")
            if not ok: return False, msg
        return True, ""
    return (True, "") if _exact(expected, actual) else (False, f"{path}: {actual!r} != {expected!r}")

def match_tool(exp, calls: list[dict]) -> tuple[bool, str]:
    from app.golden_workflows.schema import normalize_tool_name
    want = normalize_tool_name(exp.name)
    for c in calls:
        if normalize_tool_name(c.get("name", "")) != want:   # normalize observed too
            continue
        if exp.args is None:
            return True, ""
        ok, _ = _deep_subset(exp.args, c.get("args", {}), exp.name)
        if ok: return True, ""
    return False, f"tool {exp.name} not matched"

def match_tools_subsequence(exps, calls) -> tuple[bool, str]:
    from app.golden_workflows.schema import normalize_tool_name
    remaining = list(calls)
    for exp in exps:
        want = normalize_tool_name(exp.name)
        for i, c in enumerate(remaining):
            if normalize_tool_name(c.get("name", "")) == want and match_tool(exp, [c])[0]:
                remaining = remaining[i + 1:]
                break
        else:
            return False, f"tool {exp.name} not found in order"
    return True, ""

def _dig(obj: Any, path: str) -> tuple[bool, Any]:
    cur = obj
    for seg in path.split("."):
        if isinstance(cur, list):
            try: cur = cur[int(seg)]
            except (ValueError, IndexError): return False, None
        elif isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        else:
            return False, None
    return True, cur

def _last_result(ctx: AssertionContext, tool: str) -> dict | None:
    from app.golden_workflows.schema import normalize_tool_name
    want = normalize_tool_name(tool)
    matches = [r for r in ctx.tool_results
               if normalize_tool_name(r.get("name", "")) == want and not r.get("error")]
    return matches[-1] if matches else None

def evaluate_assertion(a, ctx: AssertionContext) -> tuple[bool, str]:
    t = a.type
    if t == "skill_routed":
        return (a.name.strip().lower() in [s.strip().lower() for s in ctx.skills_routed],
                f"skill {a.name} not routed")
    if t == "skills_routed_sequence":
        want = [n.strip().lower() for n in a.names]
        have = [s.strip().lower() for s in ctx.skills_routed]
        it = iter(have)
        ok = all(any(x == w for x in it) for w in want)
        return ok, f"skill sequence {want} not a subsequence of {have}"
    if t == "tool_called":
        from app.golden_workflows.schema import ToolExpectation
        return match_tool(ToolExpectation(name=a.name, args=a.args), ctx.tool_calls)
    if t == "task_returned_id":
        r = _last_result(ctx, a.tool)
        tid = (r or {}).get("content", {}).get("task_id") if r else None
        return (bool(tid), f"{a.tool} returned no task_id")
    if t == "artifact_exists":
        return (any(x.get("kind") == a.kind for x in ctx.artifacts), f"no artifact kind={a.kind}")
    if t == "response_contains":
        low = ctx.response_text.lower()
        return (any(s.lower() in low for s in a.any_of), f"response missing any_of={a.any_of}")
    if t == "tool_result_path":
        r = _last_result(ctx, a.tool)
        if not r: return False, f"no result for {a.tool}"
        found, val = _dig(r.get("content", {}), a.path)
        if not found: return False, f"path {a.path} missing"
        if a.is_not_null is not None: return (val is not None, f"{a.path} is null")
        if a.equals is not None: return (_exact(a.equals, val), f"{a.path}={val!r} != {a.equals!r}")
        if a.gte is not None:
            return (isinstance(val, (int, float)) and not isinstance(val, bool) and val >= a.gte, f"{a.path} !>= {a.gte}")
        if a.lte is not None:
            return (isinstance(val, (int, float)) and not isinstance(val, bool) and val <= a.lte, f"{a.path} !<= {a.lte}")
    return False, f"unknown assertion {t}"

def resolve_seed_refs(obj: Any, seed_map: dict[str, Any]) -> Any:
    if isinstance(obj, str) and obj.startswith("$seed."):
        if obj not in seed_map:
            from app.golden_workflows.schema import UnresolvedSeedRefError
            raise UnresolvedSeedRefError(obj)
        return seed_map[obj]
    if isinstance(obj, dict):
        return {k: resolve_seed_refs(v, seed_map) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_seed_refs(v, seed_map) for v in obj]
    return obj
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_golden_workflow_assertions.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/golden_workflows/assertions.py tests/test_golden_workflow_assertions.py
git commit -m "feat(golden-workflows): assertion engine + matching + seed interpolation"
```

---

### Task 3: Fixture loader + fixtures schema validation

**Files:**
- Create: `backend/app/golden_workflows/fixtures.py`
- Test: `tests/test_golden_workflow_fixtures.py`

**Interfaces:**
- Consumes: seed/replay JSON shape from spec §3.6; exceptions from Task 1.
- Produces:
  - `FixtureBundle` dataclass: `{seed: dict, replay: dict[str, ReplayEntry], seed_map: dict[str, Any]}`.
  - `ReplayEntry` dataclass: `{ai: dict, tool_results: list[dict], skills_routed: list[str], artifacts: list[dict], response_text: str}`.
  - `load_fixtures(path: Path) -> FixtureBundle` (validates namespaces, aliases, FK refs, replay tool_call_id integrity; builds `seed_map` keyed `"$seed.<ns>.<alias>.<field>"`).
  - `apply_seed(bundle, session) -> None` (inserts via factories/services; honors explicit ids).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_golden_workflow_fixtures.py
import json, pytest
from pathlib import Path
from app.golden_workflows.fixtures import load_fixtures
from app.golden_workflows.schema import DuplicateAliasError, UnknownSeedNamespaceError

def _write(tmp_path, data):
    p = tmp_path / "wf.fixtures.json"; p.write_text(json.dumps(data)); return p

def test_seed_map_built_with_type_preserved(tmp_path):
    p = _write(tmp_path, {"schema_version": 1,
        "seed": {"portfolios": [{"alias": "control", "id": 6, "name": "Book"}]},
        "replay": {}})
    b = load_fixtures(p)
    assert b.seed_map["$seed.portfolios.control.id"] == 6

def test_unknown_namespace_rejected(tmp_path):
    p = _write(tmp_path, {"schema_version": 1, "seed": {"banana": []}, "replay": {}})
    with pytest.raises(UnknownSeedNamespaceError):
        load_fixtures(p)

def test_duplicate_alias_rejected(tmp_path):
    p = _write(tmp_path, {"schema_version": 1,
        "seed": {"portfolios": [{"alias": "a", "id": 1, "name": "x"},
                                {"alias": "a", "id": 2, "name": "y"}]},
        "replay": {}})
    with pytest.raises(DuplicateAliasError):
        load_fixtures(p)

def test_replay_tool_call_id_integrity(tmp_path):
    p = _write(tmp_path, {"schema_version": 1, "seed": {},
        "replay": {"r1": {"ai": {"content": "", "tool_calls": [{"id": "c1", "name": "t", "args": {}}]},
                          "tool_results": [{"tool_call_id": "MISSING", "name": "t", "content": {}}],
                          "skills_routed": [], "artifacts": [], "response_text": ""}}})
    from app.golden_workflows.schema import WorkflowError
    with pytest.raises(WorkflowError):
        load_fixtures(p)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_golden_workflow_fixtures.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/golden_workflows/fixtures.py
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from app.golden_workflows.schema import (
    WorkflowError, UnknownSeedNamespaceError, DuplicateAliasError, UnresolvedAliasError,
)

_NAMESPACES = {
    "portfolios": {"alias", "id", "name"},
    "positions": {"alias", "portfolio", "underlying"},
    "pricing_profiles": {"alias", "id"},
    "market_data": {"alias", "underlying", "spot", "as_of"},
    "risk_runs": {"alias", "portfolio", "as_of"},
}
_FK = {"positions": {"portfolio": "portfolios"}, "risk_runs": {"portfolio": "portfolios"}}

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
    data = json.loads(Path(path).read_text())
    if data.get("schema_version") != 1:
        raise WorkflowError(f"{path}: schema_version must be 1")
    seed = data.get("seed", {})
    seed_map: dict[str, Any] = {}
    aliases: dict[str, set[str]] = {}
    for ns, rows in seed.items():
        if ns not in _NAMESPACES:
            raise UnknownSeedNamespaceError(ns)
        aliases[ns] = set()
        for row in rows:
            missing = _NAMESPACES[ns] - row.keys()
            if missing:
                raise WorkflowError(f"{ns} row missing {missing}")
            a = row["alias"]
            if a in aliases[ns]:
                raise DuplicateAliasError(f"{ns}.{a}")
            aliases[ns].add(a)
            for fld, val in row.items():
                seed_map[f"$seed.{ns}.{a}.{fld}"] = val
    for ns, fks in _FK.items():
        for row in seed.get(ns, []):
            for fld, target in fks.items():
                if row.get(fld) not in aliases.get(target, set()):
                    raise UnresolvedAliasError(f"{ns}.{row.get('alias')}.{fld} -> {target}")
    replay: dict[str, ReplayEntry] = {}
    for ref, entry in data.get("replay", {}).items():
        ai = entry.get("ai", {})
        call_ids = {c.get("id") for c in ai.get("tool_calls", [])}
        for r in entry.get("tool_results", []):
            if r.get("tool_call_id") not in call_ids:
                raise WorkflowError(f"replay {ref}: tool_call_id {r.get('tool_call_id')} has no matching ai.tool_call")
        replay[ref] = ReplayEntry(
            ai=ai, tool_results=entry.get("tool_results", []),
            skills_routed=entry.get("skills_routed", []),
            artifacts=entry.get("artifacts", []),
            response_text=entry.get("response_text", ""),
        )
    return FixtureBundle(seed=seed, replay=replay, seed_map=seed_map)

def apply_seed(bundle: FixtureBundle, session) -> dict[str, dict[str, int]]:
    """Insert seed rows via the ORM models, honoring explicit ids and resolving FK
    aliases. Returns {namespace: {alias: row_id}} so callers can map aliases→ids.
    The Step-3b temp-DB test is the gate: it must pass with the real column names.

    Namespace→model mapping (the structure is fixed; column names are read from
    app.models during implementation and verified by the test):
      portfolios       -> Portfolio(id=, name=)
      pricing_profiles -> PricingParameterProfile(id=)
      positions        -> Position(portfolio_id=<alias->id>, underlying=, ...)
      market_data      -> MarketDataPoint(underlying=, spot=, as_of=)
      risk_runs        -> RiskRun(portfolio_id=<alias->id>, as_of=)
    """
    from app import models
    ids: dict[str, dict[str, int]] = {ns: {} for ns in bundle.seed}

    def _pid(alias: str) -> int:
        return ids["portfolios"][alias]

    for row in bundle.seed.get("portfolios", []):
        obj = models.Portfolio(id=row["id"], name=row["name"])
        session.add(obj); session.flush()
        ids["portfolios"][row["alias"]] = obj.id
    for row in bundle.seed.get("pricing_profiles", []):
        obj = models.PricingParameterProfile(id=row["id"])
        for k, v in row.items():
            if k not in ("alias",): setattr(obj, k, v)
        session.add(obj); session.flush()
        ids["pricing_profiles"][row["alias"]] = obj.id
    for row in bundle.seed.get("positions", []):
        data = {k: v for k, v in row.items() if k not in ("alias", "portfolio")}
        obj = models.Position(portfolio_id=_pid(row["portfolio"]), **data)
        session.add(obj); session.flush()
        ids["positions"][row["alias"]] = obj.id
    for row in bundle.seed.get("market_data", []):
        data = {k: v for k, v in row.items() if k != "alias"}
        obj = models.MarketDataPoint(**data)
        session.add(obj); session.flush()
        ids["market_data"][row["alias"]] = obj.id
    for row in bundle.seed.get("risk_runs", []):
        data = {k: v for k, v in row.items() if k not in ("alias", "portfolio")}
        obj = models.RiskRun(portfolio_id=_pid(row["portfolio"]), **data)
        session.add(obj); session.flush()
        ids["risk_runs"][row["alias"]] = obj.id
    session.commit()
    return ids
```

- [ ] **Step 3b: Write the `apply_seed` failing test** (uses the repo's `session` fixture)

```python
# add to tests/test_golden_workflow_fixtures.py
def test_apply_seed_inserts_explicit_ids_and_resolves_fk(tmp_path, session):
    from app import models
    p = _write(tmp_path, {"schema_version": 1,
        "seed": {"portfolios": [{"alias": "control", "id": 6, "name": "Book"}],
                 "positions": [{"alias": "p1", "portfolio": "control", "underlying": "AAPL"}]},
        "replay": {}})
    from app.golden_workflows.fixtures import load_fixtures, apply_seed
    ids = apply_seed(load_fixtures(p), session)
    assert ids["portfolios"]["control"] == 6
    assert session.get(models.Portfolio, 6) is not None
    pos = session.get(models.Position, ids["positions"]["p1"])
    assert pos.portfolio_id == 6
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_golden_workflow_fixtures.py -v`
Expected: PASS. (Confirm the exact `Position`/`MarketDataPoint`/`RiskRun` column names against `app.models` while implementing; the namespace→model structure is fixed.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/golden_workflows/fixtures.py tests/test_golden_workflow_fixtures.py
git commit -m "feat(golden-workflows): fixture loader + seed/replay validation"
```

---

### Task 4: Definition loader + registry (markdown frontmatter, narration, skill/tool validation)

**Files:**
- Create: `backend/app/golden_workflows/registry.py`
- Modify: `backend/app/tools/__init__.py` (add `all_agent_tools()` accessor)
- Modify (only if needed): `backend/app/services/agents.py` (lift the orchestrator tool list into a reusable `AGENT_TOOLS` constant; skip if one already exists)
- Test: `tests/test_golden_workflow_registry.py`

**Interfaces:**
- Consumes: `parse_workflow` (T1), `load_fixtures`/`FixtureBundle` (T3), `resolve_seed_refs` (T2), `normalize_tool_name`/`normalize_skill` (T1).
- Produces:
  - `LoadedWorkflow` dataclass: `{workflow: GoldenWorkflow, fixtures: FixtureBundle, definition_path: Path}` — the object every Phase-1/2/3 consumer receives (so fixtures are never re-discovered ad hoc).
  - `load_workflow_bundle(md_path: Path) -> LoadedWorkflow` (frontmatter+body parse, sibling fixtures load, **`$seed` resolution** of `expected_tools.args` + every `Assertion` payload via `fixtures.seed_map`, narration attach, validation).
  - `get_workflow_bundle(id: str) -> LoadedWorkflow`, `list_workflow_bundles() -> list[LoadedWorkflow]`.
  - Convenience: `get_workflow(id) -> GoldenWorkflow` (= `.workflow`), `list_workflows()`.
  - `agent_tool_names() -> set[str]` (raises `ToolNameCollisionError` if two registered tools normalize equal) and `skill_names() -> set[str]` (recursive scan).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_golden_workflow_registry.py
import pytest
from app.golden_workflows.registry import load_workflow, skill_names
from app.golden_workflows.schema import NarrationMismatchError, MissingReplayError

NARR_OK = """---
id: t-wf
schema_version: 1
persona: risk_manager
title: T
objective: O
fixtures: t-wf.fixtures.json
steps:
  - user: hi
    expected_skill: read-risk-result
    outcome: ok
    replay: r1
success: {assertions: [], rubric: []}
---
## Step 1 — Orient
The risk manager opens the book.
"""

def _make(tmp_path, md, fixtures='{"schema_version":1,"seed":{},"replay":{"r1":{"ai":{"content":"","tool_calls":[]},"tool_results":[],"skills_routed":[],"artifacts":[],"response_text":""}}}'):
    (tmp_path / "t-wf.md").write_text(md)
    (tmp_path / "t-wf.fixtures.json").write_text(fixtures)
    return tmp_path / "t-wf.md"

def test_load_attaches_narration(tmp_path):
    wf = load_workflow(_make(tmp_path, NARR_OK))
    assert len(wf.narration) == 1 and "opens the book" in wf.narration[0]

def test_narration_count_mismatch(tmp_path):
    md = NARR_OK.replace("## Step 1 — Orient\nThe risk manager opens the book.\n", "")
    with pytest.raises(NarrationMismatchError):
        load_workflow(_make(tmp_path, md))

def test_missing_replay_ref(tmp_path):
    md = NARR_OK.replace("replay: r1", "replay: nope")
    with pytest.raises(MissingReplayError):
        load_workflow(_make(tmp_path, md))

def test_skill_names_are_recursive_and_include_run_risk():
    assert "run-risk" in skill_names()

def test_seed_refs_resolved_at_load_in_args_and_assertions(tmp_path):
    from app.golden_workflows.registry import load_workflow_bundle
    md = NARR_OK.replace(
        "    outcome: ok\n    replay: r1",
        "    outcome: ok\n"
        "    expected_tools:\n"
        "      - name: get_latest_risk_run\n"
        "        args: {portfolio_id: $seed.portfolios.control.id}\n"
        "    assertions:\n"
        "      - type: tool_result_path\n"
        "        tool: get_latest_risk_run\n"
        "        path: portfolio_id\n"
        "        equals: $seed.portfolios.control.id\n"
        "    replay: r1",
    )
    fixtures = ('{"schema_version":1,'
        '"seed":{"portfolios":[{"alias":"control","id":6,"name":"B"}]},'
        '"replay":{"r1":{"ai":{"content":"","tool_calls":[]},"tool_results":[],'
        '"skills_routed":[],"artifacts":[],"response_text":""}}}')
    b = load_workflow_bundle(_make(tmp_path, md, fixtures))
    step = b.workflow.steps[0]
    assert step.expected_tools[0].args["portfolio_id"] == 6        # arg resolved
    assert step.assertions[0].equals == 6                          # comparator resolved
    assert isinstance(step.assertions[0].equals, int)             # type preserved
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_golden_workflow_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/golden_workflows/registry.py
from __future__ import annotations
import re
from functools import lru_cache
from pathlib import Path
import yaml
from app.golden_workflows.schema import (
    GoldenWorkflow, parse_workflow, normalize_tool_name, normalize_skill,
    WorkflowError, NarrationMismatchError, MissingReplayError, FixturePathError,
    UnknownToolError, SkillNameCollisionError,
)
from app.golden_workflows.fixtures import load_fixtures

_DEFS = Path(__file__).parent / "definitions"
_SKILLS = Path(__file__).resolve().parents[1] / "skills" / "workflows"
_FM = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
_HEADING = re.compile(r"^##\s+Step\s+(\d+)\s+[—-]\s+.*$", re.M)

@lru_cache(maxsize=1)
def skill_names() -> set[str]:
    names: dict[str, Path] = {}
    for sk in _SKILLS.rglob("SKILL.md"):
        fm = _FM.match(sk.read_text())
        if not fm:
            continue
        meta = yaml.safe_load(fm.group(1)) or {}
        n = normalize_skill(str(meta.get("name", "")))
        if n in names:
            raise SkillNameCollisionError(f"{n}: {names[n]} vs {sk}")
        names[n] = sk
    return set(names)

@lru_cache(maxsize=1)
def agent_tool_names() -> set[str]:
    from app.golden_workflows.schema import ToolNameCollisionError
    from app.tools import all_agent_tools  # thin accessor added in Step 3 below
    seen: dict[str, str] = {}
    for t in all_agent_tools():
        n = normalize_tool_name(t.name)
        if n in seen and seen[n] != t.name:
            raise ToolNameCollisionError(f"{seen[n]} and {t.name} both normalize to {n}")
        seen[n] = t.name
    return set(seen)

def _parse_narration(body: str, n_steps: int) -> list[str]:
    headings = list(_HEADING.finditer(body))
    if len(headings) != n_steps:
        raise NarrationMismatchError(f"{len(headings)} narration blocks for {n_steps} steps")
    for i, h in enumerate(headings, start=1):
        if int(h.group(1)) != i:
            raise NarrationMismatchError(f"step heading {h.group(1)} out of order (want {i})")
    blocks = []
    for i, h in enumerate(headings):
        start = h.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
        blocks.append(body[start:end].strip())
    return blocks

from dataclasses import dataclass
from app.golden_workflows.fixtures import FixtureBundle
from app.golden_workflows.assertions import resolve_seed_refs

@dataclass
class LoadedWorkflow:
    workflow: GoldenWorkflow
    fixtures: FixtureBundle
    definition_path: Path

# $seed refs are resolved on the RAW frontmatter dict BEFORE parse_workflow (below),
# so typed assertion fields (gte/lte: float, equals, args) receive real values and
# Pydantic validation never sees a literal "$seed..." string.

def load_workflow_bundle(md_path: Path) -> LoadedWorkflow:
    md_path = Path(md_path)
    fm = _FM.match(md_path.read_text())
    if not fm:
        raise WorkflowError(f"{md_path}: missing frontmatter")
    data = yaml.safe_load(fm.group(1)) or {}
    if data.get("id") != md_path.stem:
        raise WorkflowError(f"id {data.get('id')} != filename {md_path.stem}")
    fixtures = data.get("fixtures", "")
    if "/" in fixtures or ".." in fixtures:
        raise FixturePathError(fixtures)
    bundle = load_fixtures(md_path.parent / fixtures)
    data = resolve_seed_refs(data, bundle.seed_map)   # resolve $seed BEFORE parse (typed fields)
    wf = parse_workflow(data)
    for step in wf.steps:
        if step.replay not in bundle.replay:
            raise MissingReplayError(step.replay)
        if normalize_skill(step.expected_skill) not in skill_names():
            raise WorkflowError(f"unknown skill {step.expected_skill}")
        for te in step.expected_tools:
            if normalize_tool_name(te.name) not in agent_tool_names():
                raise UnknownToolError(te.name)
    wf.narration = _parse_narration(fm.group(2), len(wf.steps))
    return LoadedWorkflow(workflow=wf, fixtures=bundle, definition_path=md_path)

def list_workflow_bundles() -> list[LoadedWorkflow]:
    seen: dict[str, Path] = {}
    out = []
    for md in sorted(_DEFS.glob("*.md")):
        if md.stem in seen:
            from app.golden_workflows.schema import DuplicateWorkflowError
            raise DuplicateWorkflowError(md.stem)
        seen[md.stem] = md
        out.append(load_workflow_bundle(md))
    return out

def get_workflow_bundle(wf_id: str) -> LoadedWorkflow:
    return load_workflow_bundle(_DEFS / f"{wf_id}.md")

# Convenience wrappers (workflow only)
def load_workflow(md_path: Path) -> GoldenWorkflow:
    return load_workflow_bundle(md_path).workflow

def get_workflow(wf_id: str) -> GoldenWorkflow:
    return get_workflow_bundle(wf_id).workflow

def list_workflows() -> list[GoldenWorkflow]:
    return [b.workflow for b in list_workflow_bundles()]
```

Add a thin accessor in `backend/app/tools/__init__.py` exposing the exact list of
tool objects passed to the deep-agent orchestrator builder. Locate that list in
`backend/app/services/agents.py` (the `tools=[...]` assembled for the orchestrator)
and, if it is not already a reusable module-level constant, lift it into one
(e.g. `AGENT_TOOLS`) and import it:

```python
# backend/app/tools/__init__.py
def all_agent_tools():
    """Return the registered agent tool objects (each exposes `.name`)."""
    from app.services.agents import AGENT_TOOLS  # the same list given to the orchestrator
    return list(AGENT_TOOLS)
```

If lifting the constant is non-trivial, instead import the builder and read its tool
list once; the requirement is that `all_agent_tools()` returns exactly the tools the
running agent has, so `agent_tool_names()` validates against reality.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_golden_workflow_registry.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/golden_workflows/registry.py backend/app/tools/__init__.py backend/app/services/agents.py tests/test_golden_workflow_registry.py
git commit -m "feat(golden-workflows): definition loader + registry + skill/tool validation"
```
(Drop `services/agents.py` from the `git add` if no change was needed there.)

---

### Task 5: Author the Risk-Manager "Control Day" flagship + fixtures

**Files:**
- Create: `backend/app/golden_workflows/definitions/risk-manager-control-day.md`
- Create: `backend/app/golden_workflows/definitions/risk-manager-control-day.fixtures.json`
- Test: `tests/test_flagship_loads.py`

**Interfaces:**
- Consumes: registry (T4). Produces: the canonical flagship asset used by T6 and all of Phase 2/3.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_flagship_loads.py
from app.golden_workflows.registry import get_workflow

def test_flagship_has_seven_steps_and_narration():
    wf = get_workflow("risk-manager-control-day")
    assert wf.persona == "risk_manager"
    assert len(wf.steps) == 7
    assert len(wf.narration) == 7
    assert wf.steps[1].expected_tools[0].name == "run_batch_pricing"

def test_flagship_objective_point_manifest_is_31():
    wf = get_workflow("risk-manager-control-day")
    skills = len(wf.steps)
    tools = sum(len(s.expected_tools) for s in wf.steps)
    step_assertions = sum(len(s.assertions) for s in wf.steps)
    success_assertions = len(wf.success.assertions)
    assert (skills, tools, step_assertions, success_assertions) == (7, 10, 8, 6)
    assert skills + tools + step_assertions + success_assertions == 31

def test_flagship_exact_ordered_manifest():
    """Pin the exact spec §4 content, not just counts."""
    wf = get_workflow("risk-manager-control-day")
    skills = [s.expected_skill for s in wf.steps]
    assert skills == ["read-risk-result", "run-risk", "read-risk-result",
                      "run-greeks-landscape", "run-scenario-test", "run-backtest",
                      "create-risk-report"]
    tools_per_step = [[t.name for t in s.expected_tools] for s in wf.steps]
    assert tools_per_step == [
        ["get_latest_risk_run"], ["run_batch_pricing"], ["get_latest_risk_run"],
        ["run_greeks_landscape", "get_greeks_landscape_run"],
        ["run_scenario_test", "get_scenario_test_run"],
        ["run_backtest", "get_backtest_run"], ["create_report"]]
    replays = [s.replay for s in wf.steps]
    assert len(set(replays)) == 7  # all distinct
    success_types = sorted(a.type for a in wf.success.assertions)
    assert success_types == ["artifact_exists", "skills_routed_sequence",
                             "task_returned_id", "task_returned_id",
                             "task_returned_id", "task_returned_id"]
    assert len(wf.success.rubric) == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_flagship_loads.py -v`
Expected: FAIL (files absent).

- [ ] **Step 3: Write the flagship definition** (frontmatter steps per spec §4 + 7 narration blocks)

Author `risk-manager-control-day.md` with the 7 steps, `expected_skill`/`expected_tools`/`assertions` exactly matching the spec §4 table (tool counts 1,1,1,2,2,2,1; step-assertion counts 1,1,1,1,2,1,1), `success.assertions` = one `skills_routed_sequence` + four `task_returned_id` + one `artifact_exists:report`, `success.rubric` = the four judge points, and one `## Step N — <beat>` narration block per step (prose describing the desk action).

- [ ] **Step 4: Write the fixtures** (`risk-manager-control-day.fixtures.json`)

Author `seed` (portfolio `control` id 6 + 3 positions incl. `AAPL`, a `prof` pricing profile id 3, market data, a `stale` risk run with old `as_of`) and seven `replay` entries whose canned `tool_results` satisfy each step's assertions (step-3 `hotspot.underlying="AAPL"`, step-5 `pnl=-...`, queued tools carry `content.task_id`, step-7 `create_report` returns an `artifacts:[{kind:"report",...}]`), each with `skills_routed`, `response_text` (step 1 mentions "stale"), and matching `tool_call` ids.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_flagship_loads.py -v`
Expected: PASS. If the manifest test fails, adjust the definition until counts equal `(7,10,8,6)`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/golden_workflows/definitions/ tests/test_flagship_loads.py
git commit -m "feat(golden-workflows): risk-manager control-day flagship + fixtures"
```

---

### Task 6: Deterministic regression proof (scripted-graph replay)

**Files:**
- Create: `backend/app/golden_workflows/transcript.py` (shared extraction utils, used here and in Phase 2)
- Create: `tests/test_golden_workflow_regression.py`

**Interfaces:**
- Consumes: `get_workflow_bundle` (T4); the test-only `_ScriptedGraph`/`_ai`/`_task_call` (`tests/_scripted_graph.py`) — used ONLY in the test module, never imported by app code.
- Produces (in **app** `transcript.py`, no test-tree imports):
  - `extract_assertion_context(step_record: dict) -> AssertionContext` (spec §6.1 rules: `task_ids` from `content.task_id`, non-JSON content wrapped, etc.).
  - `evaluate_step(step, ctx) -> list[tuple[bool,str]]`, `evaluate_success(success, session_ctx) -> list[tuple[bool,str]]`.
- Produces (in the **test** module `tests/test_golden_workflow_regression.py`, keeping the `_ScriptedGraph` dependency out of app code):
  - `build_scripted_graph_from_replay(loaded) -> _ScriptedGraph` (per step: `_ai(response_text, tool_calls=replay.ai.tool_calls)` + the replay `tool_results`).
  - `replay_record_from_graph_turn(turn_output) -> dict` → `{tool_calls, tool_results, skills_routed, artifacts, response_text}` (with `skills_routed` threaded from the replay entry).

- [ ] **Step 1: Write the failing test** — drive the flagship through the **real** `_ScriptedGraph` and assert per-step tools/assertions + `success`.

```python
# tests/test_golden_workflow_regression.py
from app.golden_workflows.registry import get_workflow_bundle
from app.golden_workflows.transcript import (         # app code: NO _ScriptedGraph dep
    extract_assertion_context, evaluate_step, evaluate_success,
)
from app.golden_workflows.assertions import match_tools_subsequence
from _scripted_graph import _ScriptedGraph, _ai       # test-only helper

# --- adapters defined HERE in the test module (keep _ScriptedGraph out of app code) ---
def build_scripted_graph_from_replay(loaded):
    ...  # per step: _ai(replay.response_text, tool_calls=replay.ai.tool_calls) + tool_results
def replay_record_from_graph_turn(turn):
    ...  # -> {tool_calls, tool_results, skills_routed, artifacts, response_text}

def test_flagship_replays_green_through_scripted_graph(tmp_path):
    loaded = get_workflow_bundle("risk-manager-control-day")
    graph = build_scripted_graph_from_replay(loaded)   # MANDATORY: real _ScriptedGraph
    session_records = []
    for step in loaded.workflow.steps:
        turn = graph.run_turn(step.user)               # drive one human turn
        rec = replay_record_from_graph_turn(turn)
        ctx = extract_assertion_context(rec)
        ok, msg = match_tools_subsequence(step.expected_tools, ctx.tool_calls)
        assert ok, msg
        for passed, m in evaluate_step(step, ctx):
            assert passed, m
        session_records.append(rec)
    session_ctx = extract_assertion_context({          # merge all turns
        "tool_calls": [c for r in session_records for c in r["tool_calls"]],
        "tool_results": [t for r in session_records for t in r["tool_results"]],
        "skills_routed": [s for r in session_records for s in r["skills_routed"]],
        "artifacts": [a for r in session_records for a in r["artifacts"]],
        "response_text": " ".join(r["response_text"] for r in session_records),
    })
    for passed, m in evaluate_success(loaded.workflow.success, session_ctx):
        assert passed, m
```

(If `_ScriptedGraph`'s public driving method differs from `run_turn`, the adapter wraps it; the requirement is that the flagship is exercised through the actual `_ScriptedGraph`, proving the format→graph wiring, not just fixture evaluation.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_golden_workflow_regression.py -v`
Expected: FAIL with `ModuleNotFoundError` / missing `build_scripted_graph_from_replay`.

- [ ] **Step 3: Write minimal implementation** — `transcript.py` (extraction + evaluate fns, app-only) and the `_ScriptedGraph` adapter functions **in the test module** (so `tests/_scripted_graph.py` is never imported by app code).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_golden_workflow_regression.py tests/test_flagship_loads.py -v`
Expected: PASS.

- [ ] **Step 5: Commit & freeze format**

```bash
git add backend/app/golden_workflows/transcript.py tests/test_golden_workflow_regression.py
git commit -m "feat(golden-workflows): deterministic regression proof — format frozen"
```

---

# Phase 2 — LLM-Arena Eval

### Task 7: `MatchTranscript` schema + live extraction parity

**Files:**
- Modify: `backend/app/golden_workflows/transcript.py` (add `MatchTranscript`, `MatchStep`, `extract_step_from_events`)
- Test: `tests/test_match_transcript.py`

**Interfaces:**
- Produces:
  - `MatchTranscript`/`MatchStep` Pydantic models (spec §6.1 shape, including `schema_version: Literal[1]`, `workflow_id`, `model_id`, `run_id`). A test asserts a missing/invalid `schema_version` is rejected.
  - `extract_step_from_events(turn_events) -> MatchStep` that yields the SAME fields `extract_assertion_context` consumes (parity test).
  - `transcript_from_replay(loaded: LoadedWorkflow) -> MatchTranscript` — builds a `MatchTranscript` (with `model_id="replay"`, `run_id=None`) from the flagship's replay entries, so Phase 3 / `--source regression` has a concrete transcript producer without a live run.

- [ ] **Step 1: Write the failing test** — feed a synthetic turn (assistant text + one tool call/result + a skill-route event) and assert `MatchStep` has `response_text`, `tool_results[0].content.task_id`, `skills_routed`, and that `extract_assertion_context(match_step.model_dump())` matches a hand-built context.
- [ ] **Step 2: Run** `python -m pytest tests/test_match_transcript.py -v` → FAIL.
- [ ] **Step 3: Implement** the models + extractor (normalize tool messages; collect `task_ids` from `content.task_id`; `skills_routed` from skill-selection events, order-preserving + de-duped per turn).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(arena): MatchTranscript schema + extraction parity`.

---

### Task 8: Arena model registry + Zenmux chat-client channel

**Files:**
- Create: `backend/app/services/arena/__init__.py`, `backend/app/services/arena/models.py`
- Create: `backend/app/services/arena/channel.py`
- Test: `tests/test_arena_models.py`

**Interfaces:**
- Produces:
  - `CANDIDATE_MODELS: list[ArenaModel]` where `ArenaModel = {slug, zenmux_name, display_name, default_config}`; `canonical_model_id(s) -> str` (resolves slug-or-name → canonical `slug`; unknown → `KeyError`); `validate_model_ids(ids)`.
  - `build_zenmux_chat(model: ArenaModel, config) -> BaseChatModel` (OpenAI-compatible client at the Zenmux base URL using `ZENMUX_API_KEY`).

- [ ] **Step 1: Write the failing test** — `canonical_model_id` maps both slug and zenmux_name to slug; duplicate slug/name in the registry raises; `validate_model_ids(["nope"])` raises.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the registry list (3–4 seed models) + canonicalization + `build_zenmux_chat` (no network in unit tests — assert it constructs with the right base_url/model).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(arena): model registry + zenmux channel`.

---

### Task 9: Arena runner (isolation, blocking run-tools, run loop)

**Files:**
- Create: `backend/app/services/arena/runner.py`
- Test: `tests/test_arena_runner.py`

**Interfaces:**
- Consumes: `get_workflow_bundle`/`LoadedWorkflow` (T4), `apply_seed` (T3), `MatchTranscript`/`extract_step_from_events` (T7), `build_zenmux_chat` (T8).
- Produces:
  - `isolated_match_db(bundle: FixtureBundle) -> ContextManager[session_factory]` — creates a temp SQLite, **initializes the schema** (call the repo's `app.database.init_db()` / metadata `create_all` against the temp engine — the same bootstrap the test `session` fixture uses — BEFORE `apply_seed`), seeds it, and removes it in `finally`.
  - `run_match(loaded: LoadedWorkflow, model: ArenaModel, *, artifact_root: Path, chat=None) -> MatchTranscript`. `chat=None` uses Zenmux; tests inject a mock chat. Honors the conversation contract + 12-turn budget + blocking `run_*` tools (spec §6.2). Internally uses `isolated_match_db(loaded.fixtures)`.

Split into focused sub-steps, each with its own test (a reviewer can accept/reject each independently):

- [ ] **Step 1a: Isolation + schema init + seeding** — test: `with isolated_match_db(loaded.fixtures) as session_factory:` creates a temp DB, **initializes the full schema** (so `Portfolio`/`Position`/etc. tables exist), seeds the flagship, and the **shared/test DB is untouched** (assert no `Portfolio(id=6)` in the shared session). Implement `isolated_match_db` (temp SQLite engine → schema bootstrap via `app.database` → `apply_seed` → `finally` removes the file).
- [ ] **Step 1b: One-turn orchestration + extraction** — test: with a **mock chat** that emits one assistant turn calling `get_latest_risk_run`, the runner produces a `MatchStep` whose `tool_results` + `skills_routed` populate correctly via `extract_step_from_events` (T7). Implement single-turn drive + capture.
- [ ] **Step 1c: Blocking run-tool** — test: a mock-chat turn calling `run_batch_pricing` then `get_latest_risk_run` in the same turn sees **completed** state (the `run_*` wrapper awaited completion). Implement the arena-mode blocking wrapper.
- [ ] **Step 1d: Budget + error states** — test: a mock chat that never stops calling tools hits the **12-turn budget** → step records `budget_exceeded`; a tool raising → step `error` and match marked `failed`. Implement budget + error capture.
- [ ] **Step 1e: Full run + artifact namespacing** — test: the 7-step mock-chat script yields a 7-step `MatchTranscript`; artifacts referenced by the transcript are copied under `artifact_root` and paths rewritten. Implement `run_match(...)` composing 1a–1d.
- [ ] **Step 2: Run** `python -m pytest tests/test_arena_runner.py -v` → PASS.
- [ ] **Step 3: Commit** `feat(arena): isolated match runner with blocking run-tools`.

---

### Task 10: Judge (GPT-5.5 via Zenmux) with retry + objective scoring

**Files:**
- Create: `backend/app/services/arena/judge.py`
- Create: `backend/app/services/arena/scoring.py`
- Test: `tests/test_arena_judge.py`, `tests/test_arena_scoring.py`

**Interfaces:**
- Produces:
  - `judge_match(transcript, workflow, *, post=None) -> JudgeResult` (`{rubric_scores: list[{point,score,rationale}], judged_score: float|None, judge_missing: bool}`). `post` injects a fake HTTP poster for tests.
  - `objective_score(transcript, workflow) -> tuple[float, int, int]` (score, passed, total) using the T2/T6 engine; total must equal the flagship manifest (31).
  - `total_score(objective, judged, weights, judge_missing) -> float`.

- [ ] **Step 1: Write failing tests** — judge parses valid structured JSON → mean score; malformed JSON then valid on retry → success; retry-exhausted → `judge_missing=True, judged_score=None`. `objective_score` on a fully-passing flagship transcript == 100.0 with total 31. `total_score` with `judge_missing` returns objective; empty rubric → `judged_score=None`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** judge (structured-output prompt, `openai/gpt-5.5`, temp 0, retries=2, 120s) + scoring (rounding to 1 dp at the storage boundary, weight blend; rubric point alignment = require one score per input point, else retry).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(arena): judge + reproducible scoring`.

---

### Task 11: Migration `0032_arena_runs` + round-trip test

**Files:**
- Create: `backend/alembic/versions/0032_arena_runs.py`
- Test: `tests/test_arena_migration.py`

**Interfaces:**
- Produces: tables `arena_run` and `arena_match` exactly per spec §6.6 (columns, enums, `Unique(run_id, workflow_id, model_id)`, indexes), using **migration-local Core `Table` definitions** (no ORM import).

- [ ] **Step 1: Write the failing test** — upgrade head→0032 on a temp SQLite, assert both tables + the unique constraint exist via `inspect`, then downgrade and assert they're gone.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the migration (`revision="0032_arena_runs"`, `down_revision="0031_asian_averaging_weight"`; local `sa.Table(...)`/`op.create_table`).
- [ ] **Step 4: Run** → PASS; also `alembic upgrade head` on a scratch DB.
- [ ] **Step 5: Commit** `feat(arena): migration 0032 arena_run/arena_match`.

---

### Task 12: ORM models + store

**Files:**
- Modify: `backend/app/models.py` (add `ArenaRun`, `ArenaMatch` ORM + status enums)
- Create: `backend/app/services/arena/store.py`
- Test: `tests/test_arena_store.py`

**Interfaces:**
- Produces: `create_run(workflow_ids, model_ids, weights) -> int`; `record_match(run_id, workflow_id, model_id, scores, config, transcript_path, status, error) -> int`; `get_run(id)`; `list_runs(limit, offset)`; `leaderboard(run_id=None, tag=None) -> list[LeaderboardRow]` (latest completed run default; non-failed matches; tie-break objective then model_id; empty → `[]`).

- [ ] **Step 1: Write failing tests** — round-trip a run+matches; `leaderboard` averages `total_score` per model over scored matches; excludes failed; returns `[]` when no completed run; `?tag=` filters to tagged workflows.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** ORM + store (aggregate query for leaderboard).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(arena): ORM models + store + leaderboard query`.

---

### Task 13: API router + async run task

**Files:**
- Create: `backend/app/routers/arena.py`
- Create: `backend/app/services/arena/task.py` — `queue_arena_run(workflow_ids, model_ids, weights) -> (run_id, task_id)` and `execute_arena_run_task(task_id)`, **mirroring `backend/app/services/batch_pricing.py`** (`queue_batch_pricing`/`execute_batch_pricing_task`).
- Modify: `backend/app/models.py` — add `ARENA_RUN = "arena_run"` to the `TaskKind` enum. **No migration:** `TaskKind` is a Python `str` Enum and `TaskRun.kind` is a plain `String(40)` column (no DB enum/check), so adding a value is Python-only. A test asserts `TaskKind.ARENA_RUN.value == "arena_run"` and a `TaskRun(kind=TaskKind.ARENA_RUN.value)` round-trips through the DB.
- Modify: `backend/app/main.py` (~line 3209, where `execute_batch_pricing_task`/`queue_batch_pricing` are imported and the task is executed) — add the parallel `execute_arena_run_task` branch for `TaskKind.ARENA_RUN`. (Queueing helpers mirror `backend/app/services/domains/risk.py`'s use of `queue_batch_pricing`.)
- Modify: `backend/app/main.py` (`app.include_router(build_arena_router(...))` near line 4137).
- Test: `tests/test_arena_api.py`

**Interfaces:**
- Produces endpoints per spec §6.7 (`POST /api/arena/runs` 202/422; `GET /api/arena/runs`; `GET /api/arena/runs/{id}` 200/404; `GET /api/arena/matches/{id}/transcript` 200/404; `GET /api/arena/leaderboard`; `GET /api/arena/models`). `RunSummary`/`MatchSummary` field lists defined here; runs list ordered `created_at desc`, default `limit=50`, max `200`.

Split into reviewable substeps, each test-first:

- [ ] **Step 1a: Read-only endpoints** — tests: `GET /models` lists the registry; `GET /runs` returns `{runs:[], total:0}` ordered `created_at desc` (default `limit=50`, max `200`); `GET /runs/{id}` 404 unknown; `GET /matches/{id}/transcript` 404 when path missing; `GET /leaderboard` empty → `200 {"rows":[]}`. Implement the router read endpoints (mirror `backend/app/routers/tracing.py`).
- [ ] **Step 1b: Queue + validation** — tests: `POST /runs` valid → 202 + `run_id` and a `TaskRun(kind="arena_run", status=QUEUED)` row exists; unknown model/workflow or empty list → 422. Implement `queue_arena_run` (mirror `queue_batch_pricing`) + the POST handler.
- [ ] **Step 1c: Dispatch branch** — test: invoking the task-execution path on an `arena_run` task calls `execute_arena_run_task` (patch it, assert called with the task id). Implement the `TaskKind.ARENA_RUN` branch in `backend/app/main.py` (~line 3209, beside the `execute_batch_pricing_task` branch).
- [ ] **Step 1d: Execution + scoring + persistence + status** — test (mock `run_match` + mock judge): `execute_arena_run_task` fans out `run_match(get_workflow_bundle(wid), model, ...)` per `(workflow, model)` (bounded pool 4), scores via T10, persists matches via T12, and sets run `completed` (and `completed` even when one match is `failed`; `failed` only on infra error). 
- [ ] **Step 2: Run** `python -m pytest tests/test_arena_api.py -v` → PASS.
- [ ] **Step 3: Commit** `feat(arena): API router + async run task`.

---

### Task 14: Frontend Arena page + routing

**Files:**
- Create: `frontend/src/pages/Arena.tsx`
- Create: `frontend/src/lib/arenaApi.ts`
- Modify: `frontend/src/lib/routing.ts` (add `arena: '/arena'` to `ROUTE_PATHS`), `frontend/src/types.ts` (add `'arena'` to the `Route` union), `frontend/src/main.tsx` (render `Arena` for the route), `frontend/src/components/Sidebar.tsx` (add the nav entry — update `Sidebar.test.tsx` accordingly)
- Test: `frontend/src/pages/Arena.test.tsx`, `frontend/src/components/Sidebar.test.tsx`

**Interfaces:**
- Consumes the §6.7 API. Produces the leaderboard table + run picker + run detail with a transcript drill-down.

- [ ] **Step 1: Write the failing test** (Vitest + Testing Library) — mock `arenaApi`, render `Arena`, assert leaderboard rows render and clicking a match fetches its transcript.
- [ ] **Step 2: Run** `cd frontend && npx vitest run src/pages/Arena.test.tsx` → FAIL.
- [ ] **Step 3: Implement** `arenaApi.ts` (typed fetch wrappers), `Arena.tsx`, and the routing wiring (follow the existing Risk/Scenario page pattern + `ROUTE_PATHS`).
- [ ] **Step 4: Run** → PASS; `cd frontend && npx tsc --noEmit`.
- [ ] **Step 5: Commit** `feat(arena): frontend leaderboard page + /arena route`.

---

# Phase 3 — Hyperframes Demo

### Task 15: Pure composition builder + writer

**Files:**
- Create: `backend/app/services/demo/__init__.py`, `backend/app/services/demo/composition.py`
- Test: `tests/test_demo_composition.py`

**Interfaces:**
- Consumes: `transcript_from_replay`/`MatchTranscript` (T7), `LoadedWorkflow` (T4).
- Produces:
  - `CompositionBundle` dataclass: `{workflow_id: str, source: str, section_plan: list[Section], narrator_scripts: list[str]}` — carries `workflow_id`+`source` so the writer can compute the path.
  - `build_composition(loaded: LoadedWorkflow, transcript: MatchTranscript, *, source: str) -> CompositionBundle` (pure; one section per step with narration prose + `user` line + step tool-call/outcome events; `source` = `"regression"` or `"<run_id>-<model_slug>"`).
  - `write_composition(bundle, out_dir: Path | None = None) -> Path` (IO only; when `out_dir is None`, defaults to `artifacts/demos/<bundle.workflow_id>/<bundle.source>/`).

- [ ] **Step 1: Write the failing test** — `build_composition(loaded, transcript_from_replay(loaded), source="regression")` returns 7 ordered sections; section 1 carries its narration block + the `get_latest_risk_run` event; bundle `workflow_id`/`source` set; no files written (assert `tmp` untouched).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the pure builder + separate writer.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(demo): pure composition builder + writer`.

---

### Task 16: On-demand render script

**Files:**
- Create: `scripts/generate_demo.py`
- Test: `tests/test_generate_demo_smoke.py`

**Interfaces:**
- CLI: `--workflow-id`, `--source regression|arena`, `--run-id`, `--model`, `--transcript-path`, `--output-dir`. Behind `DEMO_RENDER=1` for the actual hyperframes/TTS/MP4 stages; without it, only `build_composition`+`write_composition` run.

- [ ] **Step 1: Write the failing smoke test** — invoke the script's `main(["--workflow-id","risk-manager-control-day","--source","regression"])` with render mocked; assert a `composition.html`/section plan is written and exit code 0; missing transcript → non-zero.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the CLI (argparse), wiring `build_composition`/`write_composition`, the hyperframes CLI calls guarded by `DEMO_RENDER`, and clear stage-named errors.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(demo): on-demand render script (HTML+MP4, gated)`.

---

### Task 17: Full-suite guard + docs

**Files:**
- Create: `backend/app/golden_workflows/README.md` (how to author a workflow + run each consumer)
- Test: run the whole suite.

- [ ] **Step 1:** Run `python -m pytest` (no `ARENA_LIVE`/`DEMO_RENDER`) — assert green and that live tests are skipped, not failed.
- [ ] **Step 2:** Run `cd frontend && npx vitest run && npx tsc --noEmit`.
- [ ] **Step 3:** Write the README (authoring guide, the three consumers, the `ARENA_LIVE`/`DEMO_RENDER` flags).
- [ ] **Step 4: Commit** `docs(golden-workflows): authoring + consumer guide`.

---

## Self-Review

**Spec coverage:** §3 format → T1–T4; §3.4/3.5 engine → T2; §3.6 fixtures → T3; §4 flagship → T5; §5 regression → T6; §6.1 transcript → T7; §6.2 runner → T9; §6.3 judge → T10; §6.4 scoring/leaderboard → T10/T12; §6.5 model registry → T8; §6.6 migration/persistence → T11/T12; §6.7 API → T13; §6.8 frontend → T14; §7 demo → T15/T16; §8 failure handling → distributed (each task's errors); §10 build order → task order. No uncovered section.

**Placeholders:** `apply_seed` is fully coded (namespace→ORM mapping; only exact column names confirmed in-repo). `all_agent_tools()` names its exact source (`app.services.agents` tool list). The Phase-2 runner is split into 1a–1e, each independently tested. Remaining intentionally-prose items: the flagship `.md`/`.json` content (authored in T5 against the pinned manifest `(7,10,8,6)=31`) and the Phase-2/3 step bodies whose tests fully pin behavior.

**Wiring (was the iteration-1 gap):** the loaded object is `LoadedWorkflow{workflow, fixtures, definition_path}`; `$seed` refs are resolved at load (`_resolve_seed_in_workflow`); regression (T6), runner (T9), and demo (T15) all consume the bundle, not ad-hoc paths. `transcript_from_replay` (T7) is the concrete `--source regression` producer for T15/T16.

**Type consistency:** `AssertionContext`, `normalize_tool_name` (applied on both expected+observed tool names), `LoadedWorkflow`, `MatchTranscript`/`MatchStep`, `CompositionBundle{workflow_id, source, ...}`, `canonical_model_id`, `run_match`, `leaderboard` are referenced consistently. The objective manifest (31) is pinned identically in T5 and T10.

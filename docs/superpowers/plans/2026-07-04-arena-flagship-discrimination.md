# Arena Flagship Discrimination Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `risk-manager-control-day` as a 9-step / 39-point benchmark with grounding, adherence and synthesis checks that separate frontier models, plus infra-invalid match handling and per-axis score reporting end-to-end (engine → manifest → arena task/store/API → Arena UI).

**Architecture:** Extend the pure assertion engine (`assertions.py` + `schema.py`) with two new assertion types and two `tool_called` fields; teach `scoring.py` nullable skill checks, session-scope grounding lookups, and axis subtotals; rewrite the flagship manifest/fixtures; add infra-blank detection at the arena task boundary with store/API/UI exposure. Spec: `docs/superpowers/specs/2026-07-04-arena-flagship-discrimination-design.md`.

**Tech Stack:** Python 3 / Pydantic v2 / SQLAlchemy / FastAPI (backend), React 19 + TypeScript + vitest (frontend). Backend tests: `.venv/bin/python -m pytest` from repo root. Frontend: `cd frontend && npx vitest run <file>`, type-check `npx tsc --noEmit`.

## Global Constraints

- Scoring engine stays **pure** (no network/LLM) and flat **+1 per check** — axes are reporting only.
- Historical runs are **not** re-scored; no data migration (`arena_match.status` is a free string column).
- Other workflow manifests (`trader-rfq-booking-day`, `high-board-portfolio-review-day`) are untouched; all schema additions must be optional/additive so they load unchanged.
- Frontend: token-only styling (`var(--token)`), BEM `wl-` classes, reuse `Table`/`Chip` primitives (see `frontend/CLAUDE.md`).
- Numeric grounding: `match: "signed"` default, `rel_tol=0.02`, `near` window = 160 chars after anchor start, suffix expansion k/m/mm/bn/b, `%` also tried ÷100, target 0 → abs tol.
- New denominator: **39** = 22 procedural + 8 adherence + 5 grounding + 4 synthesis (6 skills + 11 tool expectations + 21 step assertions + 1 success assertion).
- Infra-blank = every step has no tool_calls AND empty response_text AND ≥1 step has non-empty `errors`. Status `invalid`, error `infra_blank`, scores NULL, judge skipped, **no retry**.

---

### Task 1: Assertion engine — `_dig` selector, numeric matcher, new evaluators

**Files:**
- Modify: `backend/app/golden_workflows/assertions.py`
- Test: `tests/test_golden_workflow_assertions.py`

**Interfaces:**
- Produces: `_dig(obj, path)` supporting `name[key=value]` segments; `evaluate_assertion` handling types `artifact_contains` and `response_quotes_tool_value`; `tool_called` evaluation honoring `args_any_of` + `exclusive_keys`. Consumed by Task 2 (schema models must exist for evaluate to receive them) and Task 4 (scoring).
- Note ordering: the evaluators read attributes via the schema models, so Tasks 1+2 are one commit cycle — write engine tests using the schema models; implement schema (Task 2) before the engine tests can run. Execute Task 2 Steps 1–4 first if you prefer strict TDD; the split below keeps review units clean.

- [ ] **Step 1: Write failing tests** — append to `tests/test_golden_workflow_assertions.py`:

```python
from app.golden_workflows.assertions import (
    AssertionContext, evaluate_assertion, _dig,
)
from app.golden_workflows.schema import parse_workflow  # existing import style


def _ctx(**kw):
    base = dict(response_text="", tool_calls=[], tool_results=[],
                skills_routed=[], artifacts=[], task_ids=[])
    base.update(kw)
    return AssertionContext(**base)


def _assertion(d):
    """Build a typed Assertion via a throwaway workflow parse — mirrors how
    manifests produce them (discriminated union)."""
    from pydantic import TypeAdapter
    from app.golden_workflows.schema import Assertion
    return TypeAdapter(Assertion).validate_python(d)


class TestDigSelector:
    GRID = {"landscape": [
        {"spot_shift": -0.2, "delta": -310000, "gamma": -16000},
        {"spot_shift": 0.1, "delta": -220000, "gamma": -9600},
    ]}

    def test_selects_row_by_numeric_key(self):
        found, val = _dig(self.GRID, "landscape[spot_shift=0.1].gamma")
        assert found and val == -9600

    def test_selects_negative_float_key(self):
        found, val = _dig(self.GRID, "landscape[spot_shift=-0.2].delta")
        assert found and val == -310000

    def test_missing_row_not_found(self):
        found, _ = _dig(self.GRID, "landscape[spot_shift=0.3].gamma")
        assert not found

    def test_selector_on_non_list_not_found(self):
        found, _ = _dig({"landscape": {"a": 1}}, "landscape[spot_shift=0.1].gamma")
        assert not found

    def test_plain_index_still_works(self):
        found, val = _dig(self.GRID, "landscape.1.gamma")
        assert found and val == -9600


class TestResponseQuotesToolValue:
    RESULT = [{"name": "get_latest_risk_run", "tool_call_id": "t1",
               "content": {"hotspot": {"delta": -148000.0}}}]

    def _a(self, **over):
        d = {"type": "response_quotes_tool_value", "tool": "get_latest_risk_run",
             "path": "hotspot.delta"}
        d.update(over)
        return _assertion(d)

    def test_signed_exact_passes(self):
        ctx = _ctx(response_text="AAPL delta is -148,000 today", tool_results=self.RESULT)
        ok, _ = evaluate_assertion(self._a(), ctx)
        assert ok

    def test_inverted_sign_fails_signed(self):
        ctx = _ctx(response_text="AAPL delta is 148,000", tool_results=self.RESULT)
        ok, _ = evaluate_assertion(self._a(), ctx)
        assert not ok

    def test_inverted_sign_passes_magnitude(self):
        ctx = _ctx(response_text="a loss of 148,000", tool_results=self.RESULT)
        ok, _ = evaluate_assertion(self._a(match="magnitude"), ctx)
        assert ok

    def test_suffix_expansion(self):
        ctx = _ctx(response_text="delta roughly -148k", tool_results=self.RESULT)
        ok, _ = evaluate_assertion(self._a(), ctx)
        assert ok

    def test_rel_tol_boundary(self):
        ctx = _ctx(response_text="delta -145,100", tool_results=self.RESULT)
        ok, _ = evaluate_assertion(self._a(), ctx)   # |Δ|=2900 ≤ 2960 = 0.02*148000
        assert ok
        ctx2 = _ctx(response_text="delta -144,000", tool_results=self.RESULT)
        ok2, _ = evaluate_assertion(self._a(), ctx2)  # |Δ|=4000 > 2960
        assert not ok2

    def test_near_anchor_binds_metric(self):
        res = [{"name": "get_greeks_landscape_run", "tool_call_id": "t1", "content": {
            "landscape": [{"spot_shift": 0.1, "delta": -220000, "gamma": -9600}]}}]
        swapped = "gamma at +10% is -220,000 and delta is -9,600"
        a = _assertion({"type": "response_quotes_tool_value",
                        "tool": "get_greeks_landscape_run",
                        "path": "landscape[spot_shift=0.1].gamma", "near": ["gamma"]})
        ok, _ = evaluate_assertion(a, _ctx(response_text=swapped, tool_results=res))
        assert not ok  # -9,600 appears, but not within 160 chars after "gamma"... it IS
        # within window — the swap puts -220,000 after gamma. Verify correct phrasing passes:
        good = "gamma at +10% is -9,600 and delta is -220,000"
        ok2, _ = evaluate_assertion(a, _ctx(response_text=good, tool_results=res))
        assert ok2

    def test_near_window_cutoff(self):
        res = [{"name": "get_latest_risk_run", "tool_call_id": "t1",
                "content": {"hotspot": {"delta": -148000.0}}}]
        text = "delta " + ("x" * 170) + " -148,000"
        a = self._a(near=["delta"])
        ok, _ = evaluate_assertion(a, _ctx(response_text=text, tool_results=res))
        assert not ok

    def test_percent_token_division(self):
        res = [{"name": "t", "tool_call_id": "1", "content": {"v": 0.1}}]
        a = _assertion({"type": "response_quotes_tool_value", "tool": "t", "path": "v"})
        ok, _ = evaluate_assertion(a, _ctx(response_text="shift of 10%", tool_results=res))
        assert ok

    def test_zero_target_abs_tol(self):
        res = [{"name": "t", "tool_call_id": "1", "content": {"v": 0.0}}]
        a = _assertion({"type": "response_quotes_tool_value", "tool": "t", "path": "v"})
        ok, _ = evaluate_assertion(a, _ctx(response_text="flat at 0.01", tool_results=res))
        assert ok  # |0.01 - 0| <= 0.02

    def test_missing_path_fails(self):
        ok, msg = evaluate_assertion(self._a(path="hotspot.vega"),
                                     _ctx(response_text="-148,000", tool_results=self.RESULT))
        assert not ok and "vega" in msg

    def test_non_numeric_target_fails(self):
        res = [{"name": "get_latest_risk_run", "tool_call_id": "t1",
                "content": {"hotspot": {"delta": "big"}}}]
        ok, _ = evaluate_assertion(self._a(), _ctx(response_text="big", tool_results=res))
        assert not ok


class TestToolCalledArgsAnyOfExclusive:
    def _a(self, **over):
        d = {"type": "tool_called", "name": "run_scenario_test",
             "args_any_of": [{"predefined": ["market_crash"]},
                             {"scenario_set": "market-crash"}],
             "exclusive_keys": ["predefined", "custom", "scenario_set"]}
        d.update(over)
        return _assertion(d)

    def test_predefined_alternative_matches(self):
        calls = [{"name": "run_scenario_test",
                  "args": {"portfolio_id": 2, "predefined": ["market_crash"]}}]
        ok, _ = evaluate_assertion(self._a(), _ctx(tool_calls=calls))
        assert ok

    def test_scenario_set_alternative_matches(self):
        calls = [{"name": "run_scenario_test",
                  "args": {"portfolio_id": 2, "scenario_set": "market-crash"}}]
        ok, _ = evaluate_assertion(self._a(), _ctx(tool_calls=calls))
        assert ok

    def test_extra_predefined_sets_fail(self):
        calls = [{"name": "run_scenario_test",
                  "args": {"predefined": ["market_crash", "vol_spike"]}}]
        ok, _ = evaluate_assertion(self._a(), _ctx(tool_calls=calls))
        assert not ok

    def test_mixed_carrier_fails(self):
        calls = [{"name": "run_scenario_test",
                  "args": {"predefined": ["market_crash"],
                           "custom": [{"name": "extra"}]}}]
        ok, _ = evaluate_assertion(self._a(), _ctx(tool_calls=calls))
        assert not ok

    def test_empty_carrier_counts_as_absent(self):
        calls = [{"name": "run_scenario_test",
                  "args": {"predefined": ["market_crash"], "custom": [],
                           "scenario_set": None}}]
        ok, _ = evaluate_assertion(self._a(), _ctx(tool_calls=calls))
        assert ok

    def test_exclusive_composes_with_plain_args(self):
        a = _assertion({"type": "tool_called", "name": "run_scenario_test",
                        "args": {"predefined": ["market_crash"]},
                        "exclusive_keys": ["predefined", "custom"]})
        bad = [{"name": "run_scenario_test",
                "args": {"predefined": ["market_crash"], "custom": [{"n": 1}]}}]
        ok, _ = evaluate_assertion(a, _ctx(tool_calls=bad))
        assert not ok


class TestArtifactContains:
    ARTS = [{"kind": "text", "content": "Governance report: AAPL hotspot, CVaR -2,100,000"},
            {"kind": "chart", "content": "backtest"}]

    def _a(self, any_of, kind="text"):
        return _assertion({"type": "artifact_contains", "kind": kind, "any_of": any_of})

    def test_case_insensitive_substring(self):
        ok, _ = evaluate_assertion(self._a(["cvar"]), _ctx(artifacts=self.ARTS))
        assert ok

    def test_kind_filtering(self):
        ok, _ = evaluate_assertion(self._a(["backtest"]), _ctx(artifacts=self.ARTS))
        assert not ok  # "backtest" only in the chart artifact

    def test_text_key_fallback(self):
        arts = [{"kind": "text", "text": "expected shortfall discussed"}]
        ok, _ = evaluate_assertion(self._a(["expected shortfall"]), _ctx(artifacts=arts))
        assert ok

    def test_miss_reports_terms(self):
        ok, msg = evaluate_assertion(self._a(["backtest"]), _ctx(artifacts=self.ARTS))
        assert not ok and "backtest" in msg
```

Note on `test_near_anchor_binds_metric`: the swapped phrasing puts `-220,000` inside
the gamma window, so the check passes only if `-9,600`-vs-target matching is what
gates it — the target is gamma `-9600`; the swapped text's window contains
`-220,000` (no match) and then `delta is -9,600` is *beyond* the anchor… it is at
~30 chars, still inside 160. The assertion still fails because **window filtering is
per-anchor and matching is against the target value**: `-220,000` ≠ −9600 and
`-9,600` *is* within the window and equals the target — so write the swapped text
long enough that the true value falls outside the window:

```python
        swapped = "gamma at +10% is -220,000." + (" filler" * 30) + " delta is -9,600"
```

Use that string (the plain one above would pass — this is exactly the 160-char
window semantics).

- [ ] **Step 2: Run tests, verify they fail** — `.venv/bin/python -m pytest tests/test_golden_workflow_assertions.py -q` → new tests ERROR (schema types don't exist yet). Proceed to Task 2 to add schema models, then return here.

- [ ] **Step 3: Implement in `assertions.py`** — add at module top:

```python
import re

_SEL = re.compile(r"^(.+?)\[([^=\]]+)=([^\]]+)\]$")
_NUM_TOKEN = re.compile(
    r"[+-]?\d[\d,]*(?:\.\d+)?\s*(k|m|mm|bn|b)?(%)?", re.I)
_SUFFIX = {"k": 1e3, "m": 1e6, "mm": 1e6, "bn": 1e9, "b": 1e9}
_NEAR_WINDOW = 160


def _parse_scalar(s: str):
    t = s.strip()
    for cast in (int, float):
        try:
            return cast(t)
        except ValueError:
            pass
    return t.strip("'\"")


def _values_equal(a, b) -> bool:
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    if num(a) and num(b):
        return abs(float(a) - float(b)) < 1e-9
    return a == b
```

Replace `_dig` with a selector-aware version:

```python
def _dig(obj: Any, path: str) -> tuple[bool, Any]:
    cur = obj
    for seg in path.split("."):
        sel = _SEL.match(seg)
        if sel:
            name, key, raw = sel.group(1), sel.group(2), _parse_scalar(sel.group(3))
            if not (isinstance(cur, dict) and name in cur):
                return False, None
            cur = cur[name]
            if not isinstance(cur, list):
                return False, None
            for el in cur:
                if isinstance(el, dict) and key in el and _values_equal(el[key], raw):
                    cur = el
                    break
            else:
                return False, None
            continue
        if isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError):
                return False, None
        elif isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        else:
            return False, None
    return True, cur
```

Add the numeric scanner + matcher helpers:

```python
def _scan_numeric_tokens(text: str) -> list[tuple[int, float]]:
    """(start_offset, value) per numeric token; % tokens also yield value/100."""
    out: list[tuple[int, float]] = []
    for m in _NUM_TOKEN.finditer(text):
        body = m.group(0)
        suffix = (m.group(1) or "").lower()
        pct = m.group(2)
        num = body
        if pct:
            num = num.rstrip("%")
        if suffix:
            num = num[: len(num) - len(suffix)]
        try:
            val = float(num.replace(",", "").strip())
        except ValueError:
            continue
        if suffix:
            val *= _SUFFIX[suffix]
        out.append((m.start(), val))
        if pct:
            out.append((m.start(), val / 100.0))
    return out


def _quote_value_in_text(text: str, target: float, *, rel_tol: float,
                         mode: str, near: list[str] | None) -> bool:
    tokens = _scan_numeric_tokens(text)
    if near:
        low = text.lower()
        spans: list[tuple[int, int]] = []
        for anchor in near:
            needle = anchor.lower()
            start = 0
            while (i := low.find(needle, start)) != -1:
                spans.append((i, i + _NEAR_WINDOW))
                start = i + 1
        tokens = [t for t in tokens if any(a <= t[0] <= b for a, b in spans)]
    tol = rel_tol * abs(target) if target != 0 else rel_tol
    for _, v in tokens:
        a, b = (v, target) if mode == "signed" else (abs(v), abs(target))
        if abs(a - b) <= tol:
            return True
    return False
```

Rewrite the `tool_called` branch of `evaluate_assertion` and add the two new types:

```python
    if t == "tool_called":
        from app.golden_workflows.schema import normalize_tool_name
        want = normalize_tool_name(a.name)
        candidates = a.args_any_of if getattr(a, "args_any_of", None) else [a.args]
        exclusive = getattr(a, "exclusive_keys", None) or []
        _absent = lambda v: v is None or v == [] or v == ""
        for c in ctx.tool_calls:
            if normalize_tool_name(c.get("name", "")) != want:
                continue
            call_args = c.get("args", {}) or {}
            for cand in candidates:
                if cand is not None:
                    ok, _ = _deep_subset(cand, call_args, a.name)
                    if not ok:
                        continue
                cand_keys = set((cand or {}).keys())
                if any(k not in cand_keys and not _absent(call_args.get(k))
                       for k in exclusive):
                    continue
                return True, ""
        return False, f"tool {a.name} not matched"
    if t == "artifact_contains":
        bodies = [str(x.get("content") or x.get("text") or "")
                  for x in ctx.artifacts if x.get("kind") == a.kind]
        blob = "\n".join(bodies).lower()
        if any(s.lower() in blob for s in a.any_of):
            return True, ""
        return False, f"no {a.kind} artifact contains any_of={a.any_of}"
    if t == "response_quotes_tool_value":
        r = _last_result(ctx, a.tool)
        if not r:
            return False, f"no result for {a.tool}"
        found, val = _dig(r.get("content", {}), a.path)
        if not found:
            return False, f"path {a.path} missing"
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            return False, f"{a.path} is not numeric: {val!r}"
        ok = _quote_value_in_text(ctx.response_text, float(val),
                                  rel_tol=a.rel_tol, mode=a.match, near=a.near)
        return ok, "" if ok else (
            f"response does not quote {a.path}={val} "
            f"(match={a.match}, rel_tol={a.rel_tol}, near={a.near})")
```

- [ ] **Step 4: Run tests, verify pass** — `.venv/bin/python -m pytest tests/test_golden_workflow_assertions.py -q` → all PASS (after Task 2's schema lands).

- [ ] **Step 5: Commit** — `git add backend/app/golden_workflows/assertions.py tests/test_golden_workflow_assertions.py && git commit -m "feat(golden): dig selectors, numeric grounding matcher, artifact_contains, tool_called args_any_of/exclusive_keys"`

---

### Task 2: Schema — new assertion models, nullable `expected_skill`

**Files:**
- Modify: `backend/app/golden_workflows/schema.py`
- Test: `tests/test_golden_workflow_schema.py`

**Interfaces:**
- Produces: `_ArtifactContains(type,kind,any_of)`, `_ResponseQuotesToolValue(type,tool,path,rel_tol,scope,match,near)`, `_ToolCalled(+args_any_of,+exclusive_keys)`, `Step.expected_skill: str | None`, all registered in the `Assertion` union. Consumed by Tasks 1, 3, 4, 5.

- [ ] **Step 1: Write failing tests** — append to `tests/test_golden_workflow_schema.py`:

```python
import pytest
from pydantic import TypeAdapter
from app.golden_workflows.schema import Assertion, Step


def _v(d):
    return TypeAdapter(Assertion).validate_python(d)


class TestNewAssertionSchemas:
    def test_artifact_contains_requires_nonempty_any_of(self):
        with pytest.raises(Exception):
            _v({"type": "artifact_contains", "kind": "text", "any_of": []})
        a = _v({"type": "artifact_contains", "kind": "text", "any_of": ["cvar"]})
        assert a.kind == "text"

    def test_quotes_defaults(self):
        a = _v({"type": "response_quotes_tool_value", "tool": "t", "path": "p"})
        assert (a.rel_tol, a.scope, a.match, a.near) == (0.02, "step", "signed", None)

    def test_quotes_rel_tol_bounds(self):
        for bad in (0, 1, -0.1, 1.5):
            with pytest.raises(Exception):
                _v({"type": "response_quotes_tool_value", "tool": "t",
                    "path": "p", "rel_tol": bad})

    def test_quotes_near_nonempty_when_present(self):
        with pytest.raises(Exception):
            _v({"type": "response_quotes_tool_value", "tool": "t",
                "path": "p", "near": []})

    def test_tool_called_args_mutual_exclusion(self):
        with pytest.raises(Exception):
            _v({"type": "tool_called", "name": "t", "args": {"a": 1},
                "args_any_of": [{"a": 1}]})
        with pytest.raises(Exception):
            _v({"type": "tool_called", "name": "t", "args_any_of": []})

    def test_step_expected_skill_nullable(self):
        s = Step(user="u", expected_skill=None, outcome="o", replay="r")
        assert s.expected_skill is None
```

- [ ] **Step 2: Run, verify fail** — `.venv/bin/python -m pytest tests/test_golden_workflow_schema.py -q` → FAIL (validation errors not raised / fields unknown).

- [ ] **Step 3: Implement in `schema.py`**:

```python
class _ToolCalled(BaseModel):
    type: Literal["tool_called"]
    name: str
    args: dict | None = None
    args_any_of: list[dict] | None = None
    exclusive_keys: list[str] | None = None

    @model_validator(mode="after")
    def _args_exclusive(self) -> "_ToolCalled":
        if self.args is not None and self.args_any_of is not None:
            raise ValueError("tool_called: args and args_any_of are mutually exclusive")
        if self.args_any_of is not None and not self.args_any_of:
            raise ValueError("tool_called: args_any_of must be non-empty")
        return self


class _ArtifactContains(BaseModel):
    type: Literal["artifact_contains"]
    kind: str
    any_of: list[str] = Field(min_length=1)


class _ResponseQuotesToolValue(BaseModel):
    type: Literal["response_quotes_tool_value"]
    tool: str
    path: str
    rel_tol: float = 0.02
    scope: Literal["step", "session"] = "step"
    match: Literal["signed", "magnitude"] = "signed"
    near: list[str] | None = None

    @model_validator(mode="after")
    def _bounds(self) -> "_ResponseQuotesToolValue":
        if not (0 < self.rel_tol < 1):
            raise ValueError("rel_tol must be in (0, 1)")
        if self.near is not None and not self.near:
            raise ValueError("near must be non-empty when present")
        return self
```

Register both in the `Assertion` union, and change `Step`:

```python
Assertion = Annotated[
    Union[_SkillRouted, _SkillsRoutedSequence, _ToolsRoutedSequence, _ToolCalled,
          _TaskReturnedId, _ArtifactExists, _ResponseContains, _ToolResultPath,
          _ToolNotCalled, _ArtifactContains, _ResponseQuotesToolValue],
    Field(discriminator="type"),
]

class Step(BaseModel):
    user: str = Field(min_length=1)
    expected_skill: str | None
    ...
```

(`expected_skill: str | None` with **no default** — YAML must still carry the key;
`null` is explicit.)

- [ ] **Step 4: Run, verify pass** — `.venv/bin/python -m pytest tests/test_golden_workflow_schema.py tests/test_golden_workflow_assertions.py -q` → PASS (this also unblocks Task 1 Step 4).

- [ ] **Step 5: Commit** — `git add backend/app/golden_workflows/schema.py tests/test_golden_workflow_schema.py && git commit -m "feat(golden): schema for artifact_contains, response_quotes_tool_value, args_any_of, nullable expected_skill"`

---

### Task 3: Registry — skip skill validation for null-skill steps

**Files:**
- Modify: `backend/app/golden_workflows/registry.py:94-95`
- Test: `tests/test_golden_workflow_registry.py`

- [ ] **Step 1: Write failing test** — append to `tests/test_golden_workflow_registry.py` (follow the file's existing tmp-workflow fixture pattern; if it builds definitions in a tmp dir, reuse that helper — the essential assertion is):

```python
def test_null_expected_skill_step_loads(tmp_path, monkeypatch):
    """A step with expected_skill: null must not crash skill-name validation."""
    # Copy the flagship def + fixtures into tmp, patch one step to null, load.
    import shutil, re
    from pathlib import Path
    from app.golden_workflows import registry

    src = Path("backend/app/golden_workflows/definitions")
    for f in ("risk-manager-control-day.md", "risk-manager-control-day.fixtures.json"):
        shutil.copy(src / f, tmp_path / f)
    md = (tmp_path / "risk-manager-control-day.md").read_text()
    md = md.replace("expected_skill: read-risk-result",
                    "expected_skill: null", 1)
    (tmp_path / "risk-manager-control-day.md").write_text(md)
    loaded = registry.load_workflow_bundle(tmp_path / "risk-manager-control-day.md")
    assert loaded.workflow.steps[0].expected_skill is None
```

- [ ] **Step 2: Run, verify fail** — `.venv/bin/python -m pytest tests/test_golden_workflow_registry.py -q` → FAIL (`AttributeError`/`WorkflowError` from `normalize_skill(None)`).

- [ ] **Step 3: Implement** — in `load_workflow_bundle`, guard the skill check:

```python
        if step.expected_skill is not None and \
                normalize_skill(step.expected_skill) not in skill_names():
            raise WorkflowError(f"unknown skill {step.expected_skill}")
```

- [ ] **Step 4: Run, verify pass** — same command → PASS.

- [ ] **Step 5: Commit** — `git add backend/app/golden_workflows/registry.py tests/test_golden_workflow_registry.py && git commit -m "fix(golden): registry tolerates expected_skill: null"`

---

### Task 4: Scoring — null-skill skip, session-scope grounding, axis subtotals

**Files:**
- Modify: `backend/app/services/arena/scoring.py`
- Test: `tests/test_arena_scoring.py`

**Interfaces:**
- Produces: check dicts gain `"axis"`; `objective_breakdown()` returns extra key `"axes": {axis: {"passed": int, "total": int}}`; per-step skill check emitted only when `expected_skill` is not None; `response_quotes_tool_value` with `scope: "session"` evaluated against cumulative tool_results (steps 0…i) + current-step response_text. Consumed by Task 7 (breakdown persisted) and Task 8 (frontend axes render).

- [ ] **Step 1: Write failing tests** — append to `tests/test_arena_scoring.py` (reuse the file's existing transcript-builder helpers; the new cases in essence):

```python
class TestAxisAndScope:
    def test_null_skill_emits_no_check(self):
        # workflow with steps[0].expected_skill = None → that step has no
        # {"kind": "skill"} check in the breakdown
        ...build a 1-step workflow via parse_workflow with expected_skill None,
        ...empty transcript, then:
        bd = objective_breakdown(transcript, loaded)
        kinds = [c["kind"] for c in bd["steps"][0]["checks"]]
        assert "skill" not in kinds

    def test_session_scope_reads_earlier_step_result(self):
        # step 0 returns the landscape result; step 1 has a session-scope
        # response_quotes_tool_value and quotes the number in ITS response.
        ...two-step workflow; step 1 assertion:
        # {"type": "response_quotes_tool_value", "tool": "get_greeks_landscape_run",
        #  "path": "landscape[spot_shift=0.1].gamma", "scope": "session",
        #  "near": ["gamma"]}
        # transcript: step0 tool_results carry the landscape; step1 has no
        # tool_results, response_text "gamma at +10% is -9,600"
        bd = objective_breakdown(transcript, loaded)
        q = [c for c in bd["steps"][1]["checks"] if "quote" in c["label"] or
             c["kind"] == "assertion"][-1]
        assert q["passed"]

    def test_step_scope_does_not_read_earlier_step(self):
        # same as above but scope omitted (default "step") → fails
        assert not q["passed"]

    def test_axis_subtotals_sum(self):
        bd = objective_breakdown(full_replay_transcript, loaded_flagship)
        axes = bd["axes"]
        assert sum(v["total"] for v in axes.values()) == bd["total"]
        assert sum(v["passed"] for v in axes.values()) == bd["passed"]
        assert set(axes) <= {"procedural", "adherence", "grounding", "synthesis"}
```

Write these fully against the file's existing `MatchTranscript`/workflow builders
(read the top of `tests/test_arena_scoring.py` and mirror `test_flagship_replay_scores_100_32_32`'s
setup — `transcript_from_replay(loaded)` + `get_workflow_bundle`). Also update the
three hard `== 32` pins (lines ~31-67) and the two `total == 32` cases (~184, 204) to
**39** — they will fail until Task 5 lands; that is expected mid-sequence (run them
at Task 5 Step 4).

- [ ] **Step 2: Run, verify fail** — `.venv/bin/python -m pytest tests/test_arena_scoring.py -q -k "Axis or scope"` → FAIL.

- [ ] **Step 3: Implement in `scoring.py`** — add the axis map + helper:

```python
_AXIS_BY_TYPE = {
    "skill_routed": "procedural", "skills_routed_sequence": "procedural",
    "tools_routed_sequence": "procedural", "task_returned_id": "procedural",
    "tool_called": "adherence", "tool_not_called": "adherence",
    "response_contains": "adherence",
    "tool_result_path": "grounding", "response_quotes_tool_value": "grounding",
    "artifact_exists": "synthesis", "artifact_contains": "synthesis",
}


def _axis_for(kind: str, assertion=None) -> str:
    if kind in ("skill", "tool"):
        return "procedural"
    if assertion is not None:
        return _AXIS_BY_TYPE.get(assertion.type, "procedural")
    return "procedural"
```

In `_evaluate_objective`:
1. Skill check: wrap in `if wf_step.expected_skill is not None:`; add
   `"axis": "procedural"` to the dict.
2. ToolExpectation checks: add `"axis": "procedural"`.
3. Maintain `cumulative_results: list[dict]` — extend with `ts.tool_results`
   (when `ts is not None`) **before** evaluating the step's assertions. For each
   assertion: if `getattr(assertion, "scope", "step") == "session"`, build
   `ctx = AssertionContext(response_text=step_ctx.response_text, tool_calls=step_ctx.tool_calls,
   tool_results=list(cumulative_results), skills_routed=step_ctx.skills_routed,
   artifacts=step_ctx.artifacts, task_ids=step_ctx.task_ids)`; else use `step_ctx`.
   Add `"axis": _axis_for("assertion", assertion)` to each check dict.
4. Success assertions: add `"axis": _axis_for("assertion", assertion)`.

In `objective_breakdown`, aggregate:

```python
    axes: dict[str, dict[str, int]] = {}
    for c in [c for s in steps for c in s["checks"]] + success:
        ax = c.get("axis", "procedural")
        slot = axes.setdefault(ax, {"passed": 0, "total": 0})
        slot["total"] += 1
        slot["passed"] += int(c["passed"])
    return {"passed": passed, "total": total, "steps": steps,
            "success": success, "axes": axes}
```

Update the module docstring: `total == fixed denominator (39 for the flagship)` and
the `objective_score` docstring arithmetic to `(6+11+21+1=39)`.

- [ ] **Step 4: Run, verify targeted pass** — `.venv/bin/python -m pytest tests/test_arena_scoring.py -q -k "Axis or scope or breakdown"` → new tests PASS (the 38-pins still red until Task 5).

- [ ] **Step 5: Commit** — `git add backend/app/services/arena/scoring.py tests/test_arena_scoring.py && git commit -m "feat(arena): axis subtotals, session-scope grounding, null-skill scoring"`

---

### Task 5: Flagship manifest v2 + fixtures + narration

**Files:**
- Modify: `backend/app/golden_workflows/definitions/risk-manager-control-day.md`
- Modify: `backend/app/golden_workflows/definitions/risk-manager-control-day.fixtures.json`
- Test: `tests/test_flagship_loads.py`, `tests/test_golden_workflow_regression.py`

**Interfaces:**
- Consumes: all of Tasks 1–4.
- Produces: the 9-step / 38-point manifest, replay keys `step-grid-comprehension` and `step-trap-missing-scenario-set`, 6 anchored rubric points.

- [ ] **Step 1: Rewrite `tests/test_flagship_loads.py`** to pin the v2 manifest:

```python
from app.golden_workflows.registry import get_workflow

def test_flagship_has_nine_steps_and_narration():
    wf = get_workflow("risk-manager-control-day")
    assert wf.persona == "risk_manager"
    assert len(wf.steps) == 9
    assert len(wf.narration) == 9
    assert wf.steps[1].expected_tools[0].name == "run_batch_pricing"

def test_flagship_objective_point_manifest_is_39():
    wf = get_workflow("risk-manager-control-day")
    skills = sum(1 for s in wf.steps if s.expected_skill is not None)
    tools = sum(len(s.expected_tools) for s in wf.steps)
    step_assertions = sum(len(s.assertions) for s in wf.steps)
    success_assertions = len(wf.success.assertions)
    assert (skills, tools, step_assertions, success_assertions) == (6, 11, 21, 1)
    assert skills + tools + step_assertions + success_assertions == 39

def test_flagship_exact_ordered_manifest():
    wf = get_workflow("risk-manager-control-day")
    skills = [s.expected_skill for s in wf.steps]
    assert skills == ["read-risk-result", "run-risk", None,
                      "run-greeks-landscape", None, "run-scenario-test",
                      "run-backtest", None, "generate-report"]
    tools_per_step = [[t.name for t in s.expected_tools] for s in wf.steps]
    assert tools_per_step == [
        ["get_latest_risk_run"], ["run_batch_pricing"], ["get_latest_risk_run"],
        ["run_greeks_landscape", "get_greeks_landscape_run"], [],
        ["run_scenario_test", "get_scenario_test_run"],
        ["run_backtest", "get_backtest_run"], ["list_scenario_library"],
        ["write_report_artifact"]]
    replays = [s.replay for s in wf.steps]
    assert len(set(replays)) == 9
    success_types = sorted(a.type for a in wf.success.assertions)
    assert success_types == ["tools_routed_sequence"]
    assert len(wf.success.rubric) == 6
```

- [ ] **Step 2: Run, verify fail** — `.venv/bin/python -m pytest tests/test_flagship_loads.py -q` → FAIL (still 7 steps).

- [ ] **Step 3: Rewrite the manifest frontmatter** — full step list (steps 1, 2, 4, 7
  keep their current blocks except noted; step numbering below is the new order):

  1. *(step-1-read-stale-risk, unchanged tools/skill)* — staleness assertion becomes:
     ```yaml
     - type: response_contains
       any_of: ["stale", "out of date", "outdated", "24 hours", "yesterday", "not fresh", "no longer current"]
     ```
  2. *(step-2-run-risk — unchanged)*
  3. *(step-3-read-fresh-risk)* — `expected_skill: null`; assertions:
     ```yaml
     - type: response_contains
       any_of: ["AAPL"]
     - type: response_quotes_tool_value
       tool: get_latest_risk_run
       path: "hotspot.delta"
       near: ["delta"]
     ```
  4. *(step-4-greeks-landscape — unchanged)*
  5. **NEW** grid comprehension (spec §3 step 5 verbatim — user text, `expected_skill: null`,
     `expected_tools: []`, the two `scope: session` + `near` grounding assertions
     **plus `tool_not_called: run_greeks_landscape`** — re-*dispatching* the
     landscape is the recomputation escape hatch and must fail; re-fetching via
     `get_greeks_landscape_run` stays allowed — `replay: step-grid-comprehension`).
     Regression coverage: add a scoring test where a transcript's step-5 calls
     `run_greeks_landscape` again → that check fails, while a variant that only
     calls `get_greeks_landscape_run` passes all three step-5 checks.
  6. *(step-5-scenario-test key kept)* — assertions gain the `args_any_of`/`exclusive_keys`
     `tool_called` and the `match: magnitude` CVaR quote (spec §3 step 6 verbatim).
  7. *(step-6-backtest key kept — unchanged, keeps the dates `tool_called`)*
  8. **NEW** trap (spec §3 step 8 verbatim — `expected_skill: null`,
     `expected_tools: [{name: list_scenario_library}]`, `tool_not_called: run_scenario_test`,
     the not-found `response_contains`, `replay: step-trap-missing-scenario-set`).
  9. *(step-7-create-report key kept)* — assertions per spec §3 step 9
     (`artifact_exists`, `tool_not_called: create_report`, 3× `artifact_contains`
     with `["AAPL"]` / `["backtest", "back-test", "historical replay"]` /
     `["cvar", "expected shortfall"]`).

  `success.assertions` = ONLY the existing `tools_routed_sequence` (7 names,
  unchanged). `success.rubric` = the 6 anchored points from spec §6, verbatim.

- [ ] **Step 4: Renumber and extend the narration body** — headings must be
  `## Step 1` … `## Step 9` in order (loader enforces count == steps). Insert after
  the current Step 4 block:

  ```markdown
  ## Step 5 — Read the landscape grid

  The risk manager probes whether the desk actually reads the numbers it computes:
  from the landscape already retrieved, what is portfolio gamma at a +10% spot
  shift, and what does delta become at −20%? The agent answers **from the completed
  run's grid** — re-fetching via `get_greeks_landscape_run` is acceptable but no new
  computation is dispatched — quoting gamma **-9,600** at +10% and delta
  **-310,000** at −20% exactly as computed.
  ```

  and after the (renumbered) Step 7 backtest block:

  ```markdown
  ## Step 8 — A scenario set that does not exist

  The risk manager asks to stress the book with the 'liquidity-crunch' scenario set.
  No such set exists. The agent checks the scenario library via
  `list_scenario_library`, finds no matching predefined set, and reports that
  'liquidity-crunch' is **not available**, offering the nearest predefined
  alternatives (market_crash, severe_downturn) instead. Crucially it does **not**
  silently substitute a different set or launch `run_scenario_test`.
  ```

  Renumber old Step 5→6, 6→7, 7→9 headings.

- [ ] **Step 5: Fixture edits** (`risk-manager-control-day.fixtures.json`) — add two replay entries:

  ```json
  "step-grid-comprehension": {
    "ai": {"content": "Reading the completed landscape grid for the answer.", "tool_calls": []},
    "tool_results": [],
    "skills_routed": [],
    "artifacts": [],
    "response_text": "From the completed Greeks landscape: gamma at a +10% spot shift is -9,600, and at a -20% shift the portfolio delta becomes -310,000. Gamma decay flattens as spot rises; the short-delta position deepens on the downside."
  },
  "step-trap-missing-scenario-set": {
    "ai": {"content": "Checking the scenario library before running anything.", "tool_calls": [
      {"id": "call_s8_001", "name": "list_scenario_library_tool", "args": {}}]},
    "tool_results": [
      {"tool_call_id": "call_s8_001", "name": "list_scenario_library_tool",
       "content": {"predefined": ["market_crash", "market_rally", "vol_spike", "vol_crush", "rate_hike", "rate_cut", "severe_downturn", "inflation_shock", "black_monday_1987", "financial_crisis_2008", "covid_crash_2020"], "custom": []}}],
    "skills_routed": [],
    "artifacts": [],
    "response_text": "There is no scenario set named 'liquidity-crunch' — it is not in the scenario library and not available as a predefined set. The closest predefined alternatives are market_crash and severe_downturn; say the word and I'll run one of those with the Control Profile instead."
  }
  ```

  And two edits to existing entries:
  - `step-5-scenario-test` (now step 6): leave the `ai.tool_calls` args as
    `{"portfolio_id": 6, "scenario_set": "market-crash"}` — it satisfies the second
    `args_any_of` alternative with all other carriers absent; `response_text`
    already quotes "CVaR at 95% confidence is -2,100,000" within 160 chars of
    "CVaR" (magnitude match). **No change needed — verify, don't edit.**
  - `step-3-read-fresh-risk`: `response_text` already contains "net delta of
    -148,000" — signed match near "delta". **Verify, don't edit.**
  - `step-7-create-report`: its artifact `content` must contain "AAPL", a backtest
    mention, and "CVaR" — extend the artifact body if missing, e.g. append
    `"…Scenario stress: CVaR -2,100,000 under market_crash. Backtest: delta-hedge P&L over 2026-03-24→2026-06-24 summarised above."`

- [ ] **Step 6: Run the full golden suite** —
  `.venv/bin/python -m pytest tests/test_flagship_loads.py tests/test_golden_workflow_regression.py tests/test_arena_scoring.py tests/test_golden_workflow_assertions.py tests/test_golden_workflow_schema.py tests/test_golden_workflow_registry.py -q`
  → ALL PASS, including the regression suite proving the golden replay earns 39/39
  (the regression file asserts every step + success assertion passes on
  `transcript_from_replay`; if any new check fails there, fix the **fixture**, not
  the check).

- [ ] **Step 7: Commit** — `git add backend/app/golden_workflows/definitions/ tests/test_flagship_loads.py && git commit -m "feat(golden): flagship v2 — 9 steps / 38 points with grounding, trap, synthesis checks"`

---

### Task 6: Judge — anchor-discipline prompt line

**Files:**
- Modify: `backend/app/services/arena/judge.py:141-145`
- Test: `tests/test_arena_judge.py`

(The anchored rubric *text* itself ships in Task 5's manifest; this task is only the
system-prompt line.)

- [ ] **Step 1: Write failing test** — append to `tests/test_arena_judge.py`:

```python
def test_system_prompt_carries_anchor_discipline():
    from app.services.arena.judge import _build_prompt
    from app.golden_workflows.registry import get_workflow_bundle
    from app.golden_workflows.transcript import transcript_from_replay
    loaded = get_workflow_bundle("risk-manager-control-day")
    msgs = _build_prompt(transcript_from_replay(loaded), loaded)
    assert "anchor" in msgs[0]["content"].lower()
```

- [ ] **Step 2: Run, verify fail** → FAIL (no "anchor" in system msg).

- [ ] **Step 3: Implement** — extend `system_msg`:

```python
    system_msg = (
        "You are an expert evaluator grading an AI assistant's performance on a "
        "multi-step desk workflow. Score each rubric point from 0 to 100 based "
        "on the transcript provided. Be objective and consistent. "
        "Each rubric point defines explicit score anchors (0/50/100); pick the "
        "score matching the closest anchor and use the full 0-100 range."
    )
```

- [ ] **Step 4: Run, verify pass**, then **Step 5: Commit** — `git add backend/app/services/arena/judge.py tests/test_arena_judge.py && git commit -m "feat(arena): anchor-discipline line in judge system prompt"`

---

### Task 7: Infra-invalid at the task boundary + store/API exposure

**Files:**
- Modify: `backend/app/services/arena/task.py` (inside `_execute`, after `transcript = _run_match_fn(...)`)
- Modify: `backend/app/services/arena/store.py::leaderboard`
- Modify: `backend/app/routers/arena.py::get_leaderboard`
- Test: `tests/test_arena_store.py`, `tests/test_arena_api.py`, new cases in the arena task's test home (`tests/test_arena_runner.py` hosts task-level tests via injectable `run_match_fn` — follow its existing fake pattern)

**Interfaces:**
- Produces: `task.py::_is_infra_blank(transcript) -> bool`; matches recorded with `status="invalid"`, `error="infra_blank"`, all scores None, `judge_missing=True`; `store.leaderboard` rows gain `"invalid_count"`; API rows gain `"invalid"`. Consumed by Task 8.

- [ ] **Step 1: Write failing tests**:

```python
# tests/test_arena_runner.py (task-level, using execute_arena_run_task with fakes)
def _blank_transcript(loaded, with_errors):
    from app.golden_workflows.transcript import MatchTranscript, MatchStep
    steps = [MatchStep(index=i, user=s.user, messages=[], tool_calls=[],
                       tool_results=[], skills_routed=[], artifacts=[],
                       task_ids=[], response_text="",
                       errors=(["provider 402"] if (with_errors and i == 0) else []))
             for i, s in enumerate(loaded.workflow.steps)]
    return MatchTranscript(schema_version=1, run_id=1,
                           workflow_id=loaded.workflow.id, model_id="m",
                           started_at=None, finished_at=None, steps=steps)


def test_infra_blank_marks_invalid_and_skips_judge(arena_session_factory):
    # judge_fn that raises if called proves the judge is skipped
    def exploding_judge(*a, **k):
        raise AssertionError("judge must not run for invalid matches")
    ...queue a run, execute with run_match_fn returning _blank_transcript(loaded, True),
    ...judge_fn=exploding_judge
    match = ...single ArenaMatch row...
    assert match.status == "invalid"
    assert match.error == "infra_blank"
    assert match.objective_score is None and match.total_score is None


def test_all_blank_without_errors_stays_scored_zero(arena_session_factory):
    ...same but _blank_transcript(loaded, False) and a stub judge...
    assert match.status == "scored"
    assert match.objective_score == 0.0
```

```python
# tests/test_arena_store.py
def test_leaderboard_excludes_invalid_and_counts_them(session):
    ...create run; record_match(model "a", status="scored", total 80/obj 80)
    ...record_match(run2? no — same run, model "a", second workflow, status="invalid",
                    scores None, error="infra_blank")
    rows = store.leaderboard(session, run_id=run_id)
    row = rows[0]
    assert row["match_count"] == 1
    assert row["invalid_count"] == 1
    assert row["mean_total"] == 80.0


def test_invalid_only_model_still_listed_with_zero_matches(session):
    ...model "b" has ONLY an invalid match in the run...
    rows = store.leaderboard(session, run_id=run_id)
    b = [r for r in rows if r["model_id"] == "b"][0]
    assert b["match_count"] == 0 and b["invalid_count"] == 1
    assert b["mean_total"] is None
```

```python
# tests/test_arena_api.py
def test_leaderboard_response_carries_invalid(client_with_seeded_matches):
    resp = client.get("/api/arena/leaderboard")
    row = resp.json()["rows"][0]
    assert "invalid" in row


def test_run_detail_exposes_match_error(client_with_seeded_matches):
    """The corroborating invalid reason must be auditable via the API."""
    # seed one match with status="invalid", error="infra_blank"
    resp = client.get(f"/api/arena/runs/{run_id}")
    inv = [m for m in resp.json()["matches"] if m["status"] == "invalid"][0]
    assert inv["error"] == "infra_blank"
```

Adapt each to the fixture/builder helpers already in those files (they all have
seeded-session helpers; mirror the adjacent tests).

- [ ] **Step 2: Run, verify fail** — `.venv/bin/python -m pytest tests/test_arena_runner.py tests/test_arena_store.py tests/test_arena_api.py -q` → new cases FAIL.

- [ ] **Step 3: Implement** —

`task.py` (module level):

```python
def _is_infra_blank(transcript) -> bool:
    """All steps produced nothing AND at least one step carries error evidence.

    Blankness alone is not enough — a model that silently did nothing is a real
    scored 0; invalidity must be corroborated by transport/provider errors.
    """
    steps = transcript.steps
    if not steps:
        return False
    all_blank = all(
        not s.tool_calls and not s.response_text.strip() for s in steps)
    has_error = any(s.errors for s in steps)
    return all_blank and has_error
```

In `_execute`, right after `transcript = _run_match_fn(...)`:

```python
                if _is_infra_blank(transcript):
                    transcript_path = _save_transcript(
                        transcript, artifact_root, workflow_id, model_id)
                    store.record_match(
                        session, run_id=run_id, workflow_id=workflow_id,
                        model_id=model_id, objective_score=None,
                        judged_score=None, total_score=None, judge_missing=True,
                        config={"weights": weights},
                        transcript_path=transcript_path,
                        status="invalid", error="infra_blank",
                    )
                    completed_count += 1
                    update_task_progress(session, task_id,
                                         current=completed_count, total=total_pairs)
                    session.commit()
                    continue
```

Extract the existing inline transcript-save block into
`_save_transcript(transcript, artifact_root, workflow_id, model_id) -> str | None`
and use it in both paths (DRY).

`store.py::leaderboard` — fetch both statuses and count invalids:

```python
    matches = (
        session.query(ArenaMatch)
        .filter(ArenaMatch.run_id == run_id,
                ArenaMatch.status.in_(["scored", "invalid"]))
        .all()
    )
    ...
    invalid_counts: dict[str, int] = defaultdict(int)
    for m in matches:
        if m.status == "invalid":
            invalid_counts[m.model_id] += 1
            continue
        if m.total_score is not None:
            model_totals[m.model_id].append(m.total_score)
        if m.objective_score is not None:
            model_objectives[m.model_id].append(m.objective_score)

    all_models = set(model_totals) | set(invalid_counts)
    rows = []
    for model_id in all_models:
        totals = model_totals.get(model_id, [])
        objectives = model_objectives.get(model_id, [])
        rows.append({
            "model_id": model_id,
            "mean_total": round(sum(totals) / len(totals), 1) if totals else None,
            "mean_objective": round(sum(objectives) / len(objectives), 1) if objectives else None,
            "match_count": len(totals),
            "invalid_count": invalid_counts.get(model_id, 0),
        })
    rows.sort(key=lambda r: (r["mean_total"] is None, -(r["mean_total"] or 0),
                             -(r["mean_objective"] or 0), r["model_id"]))
```

(Tag-filter block: keep, applied before aggregation. The early
`if not matches: return []` stays.)

`routers/arena.py::get_leaderboard` — add the field:

```python
            {
                "model_id": r["model_id"],
                "avg_total": r["mean_total"],
                "avg_objective": r["mean_objective"],
                "matches": r["match_count"],
                "invalid": r["invalid_count"],
            }
```

`routers/arena.py::MatchSummary` — expose the invalid reason (store's
`_match_to_dict` already carries `error`; the API model currently drops it):

```python
class MatchSummary(BaseModel):
    ...existing fields...
    error: str | None = None
```

and in `get_run`'s `MatchSummary(...)` construction add `error=m.get("error")`.

- [ ] **Step 4: Run, verify pass** — same command → PASS. Also run the untouched
  neighbors: `.venv/bin/python -m pytest tests/test_arena_runner_high_board.py tests/test_arena_task* -q 2>/dev/null; .venv/bin/python -m pytest tests/ -q -k "arena"` → no regressions.

- [ ] **Step 5: Commit** — `git add backend/app/services/arena/task.py backend/app/services/arena/store.py backend/app/routers/arena.py tests/ && git commit -m "feat(arena): infra-blank matches → invalid status, excluded from leaderboard means, invalid_count exposed"`

---

### Task 8: Frontend — invalid badge/chip + axis strip

**Files:**
- Modify: `frontend/src/lib/arenaApi.ts` (types)
- Modify: `frontend/src/routes/Arena.live.tsx`
- Modify: `frontend/src/routes/Arena.css`
- Test: `frontend/src/routes/Arena.live.test.tsx`

**Interfaces:**
- Consumes: leaderboard rows `{..., invalid: number}`; `score_breakdown.objective.axes: Record<string, {passed, total}>`; match `status === 'invalid'`.

- [ ] **Step 1: Write failing tests** — add to `Arena.live.test.tsx` (mirror its
  existing mock-fetch harness):

```tsx
it('renders invalid count chip on leaderboard rows', async () => {
  // leaderboard row: { model_id: 'm', avg_total: 80, avg_objective: 80, matches: 2, invalid: 1 }
  ...render, await findByText('80.000')...
  expect(screen.getByText(/1 infra/)).toBeInTheDocument();
});

it('renders invalid match status badge with its reason', async () => {
  // run detail containing a match with status 'invalid', error 'infra_blank'
  expect(await screen.findByText('invalid')).toBeInTheDocument();
  expect(screen.getByText(/infra_blank/)).toBeInTheDocument();
});

it('renders axis strip when breakdown carries axes', async () => {
  // score_breakdown.objective.axes = { procedural: {passed: 20, total: 22},
  //   adherence: {passed: 6, total: 8}, grounding: {passed: 4, total: 5},
  //   synthesis: {passed: 4, total: 4} }
  ...select match...
  expect(screen.getByText('procedural')).toBeInTheDocument();
  expect(screen.getByText('20/22')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run, verify fail** — `cd frontend && npx vitest run src/routes/Arena.live.test.tsx` → FAIL.

- [ ] **Step 3: Implement** —

`arenaApi.ts`:

```ts
export type ArenaAxisTally = { passed: number; total: number };
// in ArenaScoreBreakdown.objective:  axes?: Record<string, ArenaAxisTally>;
// in ArenaLeaderboardRow:            invalid?: number;
// in ArenaMatchSummary:              error?: string | null;
```

`Arena.live.tsx`:
- `statusClass`: add `if (status === 'invalid') return 'wl-arena__status--invalid';`
- Match cell: under the status span, render the reason for invalid matches:
  ```tsx
  {match.status === 'invalid' && match.error && (
    <span className="wl-arena__match-invalid-reason">{match.error}</span>
  )}
  ```
  with token-only CSS `.wl-arena__match-invalid-reason { color: var(--ink-2); font-size: var(--type-small-size); }`
- Leaderboard `matches` column render:
  ```tsx
  render: (row) => (
    <span className="wl-arena__match-count">
      {row.matches}
      {(row.invalid ?? 0) > 0 && (
        <span className="wl-arena__invalid-chip">{row.invalid} infra</span>
      )}
    </span>
  ),
  ```
- In `ScoreBreakdownView`, above the diagnosis block:
  ```tsx
  {obj.axes && (
    <div className="wl-arena__axes">
      {(['procedural', 'adherence', 'grounding', 'synthesis'] as const)
        .filter((k) => obj.axes && obj.axes[k])
        .map((k) => (
          <div key={k} className="wl-arena__axis-cell">
            <span className="wl-arena__axis-name">{k}</span>
            <span className="wl-arena__axis-tally">
              {obj.axes![k].passed}/{obj.axes![k].total}
            </span>
          </div>
        ))}
    </div>
  )}
  ```

`Arena.css` (token-only):

```css
.wl-arena__status--invalid { color: var(--ink-2); }

.wl-arena__invalid-chip {
  margin-left: var(--gap-1);
  padding: 0 var(--gap-1);
  border-radius: var(--radius-1);
  background: var(--surface-2);
  color: var(--ink-2);
  font-size: var(--type-small-size);
}

.wl-arena__axes {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--gap-2);
  margin-bottom: var(--gap-2);
}

.wl-arena__axis-cell {
  display: flex;
  flex-direction: column;
  gap: var(--gap-0);
  padding: var(--gap-1) var(--gap-2);
  border: 1px solid var(--line-1);
  border-radius: var(--radius-1);
}

.wl-arena__axis-name {
  color: var(--ink-2);
  font-size: var(--type-small-size);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.wl-arena__axis-tally { font-variant-numeric: tabular-nums; }
```

**Before writing any CSS, open `frontend/src/tokens/` and verify every token name
used above exists** (`--gap-0/1/2`, `--radius-1`, `--surface-2`, `--line-1`,
`--ink-2`, `--type-small-size`); substitute the project's actual equivalents where
names differ. Never invent a token.

- [ ] **Step 4: Run, verify pass** — `cd frontend && npx vitest run src/routes/Arena.live.test.tsx && npx tsc --noEmit` → PASS, clean types. Visually verify in **both themes + compact density** if a dev server is available; otherwise state it was not visually verified.

- [ ] **Step 5: Commit** — `git add frontend/src && git commit -m "feat(frontend): arena invalid badge/chip and per-axis score strip"`

---

### Task 9: Docs + full-suite gate

**Files:**
- Modify: `CHANGELOG.md` (under `[Unreleased]`)
- Modify: `CLAUDE.md` (Golden Workflows / arena bullets)
- Modify: `README.md` (Arena feature bullet)

- [ ] **Step 1: CHANGELOG** — under `[Unreleased] / Changed`:

```markdown
- Arena flagship `risk-manager-control-day` rebuilt for discrimination: 9 steps /
  39 objective points (was 7/32) — numeric-grounding checks (`response_quotes_tool_value`,
  signed + label-anchored), report-synthesis checks (`artifact_contains`), a
  nonexistent-scenario-set trap step, exact-args adherence (`args_any_of` +
  `exclusive_keys`), and duplicate session checks removed. Anchored judge rubric.
- Arena scoring: per-axis subtotals (procedural/adherence/grounding/synthesis) in
  `score_breakdown.objective.axes`; per-step skill checks skipped for
  `expected_skill: null` steps (skills_routed dedup blind spot).
- Arena runs: infra-blank matches (no output + transport errors) now record
  `status="invalid"` (`infra_blank`), excluded from leaderboard means and surfaced
  as an invalid count in the API and Arena page.
```

- [ ] **Step 2: CLAUDE.md** — in the arena/golden-workflows notes add: flagship
  denominator is now **38** (9 steps); `expected_skill: null` steps score no skill
  point (use for repeat-skill steps — `skills_routed` never re-records an
  already-read SKILL.md); `response_quotes_tool_value` is signed by default and
  `near`-anchored (matching is per-metric, not whole-response); `invalid` match
  status = corroborated infra blanks only, never counted in means.

- [ ] **Step 3: README** — extend the Arena bullet with "per-axis score breakdown
  and infra-invalid match handling".

- [ ] **Step 4: Full-suite gate** —
  `.venv/bin/python -m pytest -q` (backend, from repo root; remember the known
  `.env`-leak false-fails in `test_tracing_config`/gateway config tests — verify
  any failure against that list before touching code) and
  `cd frontend && npx vitest run && npx tsc --noEmit`.

- [ ] **Step 5: Commit** — `git add CHANGELOG.md CLAUDE.md README.md && git commit -m "docs: arena flagship v2 discrimination overhaul"`

---

## Self-Review Notes

- Spec §3 (manifest) → Task 5; §4.1 → Tasks 2+3+4; §4.2/4.3/4.4/4.5 → Tasks 1+2;
  §4.6 (axes) → Task 4; §5 (fixtures) → Task 5; §6 (judge) → Tasks 5 (rubric text)
  + 6 (prompt line); §7 (invalid) → Task 7; §8 (frontend) → Task 8; §9 (tests)
  distributed per task; §10 (docs) → Task 9. No spec section unowned.
- Denominator cross-check: skills 6 (steps 1,2,4,6,7,9) + tools 11
  (1+1+1+2+0+2+2+1+1) + step assertions 21 (1+1+2+1+3+4+2+2+5) + success 1 = **39**. ✓
- Type-consistency: `args_any_of`/`exclusive_keys`/`near`/`match`/`scope` names
  identical across schema (Task 2), evaluator (Task 1), manifest (Task 5), tests.
- The `== 32` pins in `test_arena_scoring.py` are updated in Task 4 Step 1 but only
  turn green at Task 5 Step 6 — run order matters; the full-suite gate in Task 9
  is the final arbiter.

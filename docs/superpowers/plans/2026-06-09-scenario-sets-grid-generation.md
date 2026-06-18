# Scenario Sets + Grid Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Scenario Test page generate a named Scenario Set as the cross product of `(start, stop, step)` sweeps over spot/vol/rate/dividend, manage those Sets (list/view/delete/re-edit/run), and surface multi-scenario Sets in a unified picker.

**Architecture:** A pure `generate_grid` helper in `scenario_catalog` expands each axis to an inclusive value ladder and takes the `itertools.product` → one `Scenario` per cell. Sets persist as the existing YAML (N scenarios) **plus** a `<name>.set.json` sidecar holding the re-editable grid spec; classification keys off "sidecar present OR ≥2 scenarios". New REST + agent-tool + page surfaces wrap the helper. The run path is unchanged — it already expands `scenario_sets[]`.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / Pydantic v2 (backend), QuantArk `stresstest` (`ScenarioBuilder`/`ScenarioStorage`), React + TypeScript + Vitest (frontend).

---

## Execution context (read first)

- **Worktree:** A concurrent agent shares this checkout and churns shared HEAD. Execute this plan in a git worktree (see `superpowers:using-git-worktrees`). In a worktree, run backend tests with `PYTHONPATH=<worktree>/backend` so imports resolve to the worktree, **not** the `.pth`-installed main checkout.
- **Run backend tests** (from repo root, or worktree root with the PYTHONPATH above):
  `python -m pytest tests/<file>.py -v`
- **Run frontend tests:** `cd frontend && npx vitest run src/<path>.test.tsx`
- **Conventions that bite (from project memory):**
  - `PERCENTAGE` stress values are **fractions** on the wire (`-0.10`), shown as `%` in the UI (×100 display, ÷100 input). The grid dialog mirrors `ScenarioBuilderDialog`'s ÷100-on-submit.
  - **Real-value test lesson:** characterization tests must use **non-default** inputs (a vacuous test once passed because `value == fallback`). The grid count/value tests below use real, distinct numbers.
  - **Flat-model invariant:** a Set must never leak into the single-custom list, nor vice-versa ("multi-scenario-set whack-a-mole").
  - **Token-only CSS** (`frontend/UI_STYLE_GUIDE.md`): no hardcoded colors; verify dark mode + compact density.

---

## File structure

**Backend**
- Modify `backend/app/config.py` — add `scenario_grid_max_cells: int = 200`.
- Modify `backend/app/services/domains/scenario_catalog.py` — add `expand_axis`, `generate_grid`, `_grid_cell_name`, `_sidecar_path`, `read_set_meta`, `_axes_summary`, `list_sets_full`; extend `save_set`/`delete_set`/`list_sets_detailed`.
- Modify `backend/app/schemas.py` — `GridAxisSpec`, `ScenarioGridRequest`, `ScenarioSetSummaryOut`, `ScenarioGridSavedOut`.
- Modify `backend/app/main.py` — 3 routes (`POST /sets/generate`, `GET /sets/full`, `GET /sets/{name}/scenarios`); ordering matters (see Task 5).
- Modify `backend/app/tools/scenario_test.py` — `generate_scenario_set_tool`; extend `list_scenario_library_tool`.
- Modify `backend/app/tools/__init__.py`, `backend/app/services/agents.py` — register/allowlist the new tool.
- Modify `backend/app/skills/references/risk/scenario-test.md` — short grid note (respect 500-token body cap).
- Tests: `tests/test_scenario_catalog.py`, `tests/test_scenario_test_api.py`, `tests/test_scenario_test_tools.py`, `tests/test_capability_assignments.py`.

**Frontend**
- Modify `frontend/src/types.ts` — `GridAxisSpec`, `ScenarioGridRequest`, `ScenarioSetSummary`.
- Modify `frontend/src/api/client.ts` — `fetchScenarioSetsFull`, `getScenarioSetScenarios`, `generateScenarioSet`.
- Create `frontend/src/components/ScenarioGridDialog.tsx` + `.css` + `.test.tsx`.
- Modify `frontend/src/routes/ScenarioTest.tsx` (+ `.css`) — Scenario Sets section, Generate entry, run wiring; `frontend/src/routes/ScenarioTest.test.tsx`.

---

## Task 1: Config flag + `expand_axis` ladder

**Files:**
- Modify: `backend/app/config.py:112` (after `scenario_test_output_dir`)
- Modify: `backend/app/services/domains/scenario_catalog.py` (add `import math, itertools, json`; top-level `from datetime import datetime`)
- Test: `tests/test_scenario_catalog.py`

- [ ] **Step 1: Add the config flag.** In `backend/app/config.py`, directly after the `scenario_test_output_dir` line, add:

```python
    scenario_grid_max_cells: int = 200
```

- [ ] **Step 2: Write the failing test.** Append to `tests/test_scenario_catalog.py`:

```python
def test_expand_axis_inclusive_on_grid():
    # -0.20..0.20 step 0.05 -> 9 inclusive points, no float drift.
    vals = scenario_catalog.expand_axis(-0.20, 0.20, 0.05)
    assert len(vals) == 9
    assert vals[0] == pytest.approx(-0.20)
    assert vals[-1] == pytest.approx(0.20)
    assert vals[1] == pytest.approx(-0.15)  # exact, not -0.15000000000001


def test_expand_axis_single_value_when_start_equals_stop():
    assert scenario_catalog.expand_axis(0.1, 0.1, 0.05) == [pytest.approx(0.1)]


def test_expand_axis_off_grid_stop_truncates():
    # 0..0.25 step 0.10 -> last full boundary <= stop: [0, 0.1, 0.2]
    vals = scenario_catalog.expand_axis(0.0, 0.25, 0.10)
    assert [round(v, 4) for v in vals] == [0.0, 0.1, 0.2]


def test_expand_axis_wrong_sign_step_raises():
    with pytest.raises(ValueError, match="sign"):
        scenario_catalog.expand_axis(0.0, 0.2, -0.05)


def test_expand_axis_zero_step_raises():
    with pytest.raises(ValueError, match="step"):
        scenario_catalog.expand_axis(0.0, 0.2, 0.0)
```

- [ ] **Step 3: Run it to confirm it fails.**

Run: `python -m pytest tests/test_scenario_catalog.py -k expand_axis -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'expand_axis'`.

- [ ] **Step 4: Implement `expand_axis`.** At the top of `scenario_catalog.py`, ensure these imports exist (add what's missing):

```python
import itertools
import json
import math
from datetime import datetime
```

Then add (place after `_PREDEFINED`):

```python
def expand_axis(start: float, stop: float, step: float) -> list[float]:
    """Inclusive value ladder from start to stop by step, robust to float drift.

    Endpoint is included when it lands on a step boundary; an off-grid stop
    truncates to the last boundary on the start side of stop. Rounds each value
    to 10 dp so 0.05 ladders don't accumulate 1e-16 noise.
    """
    start, stop, step = float(start), float(stop), float(step)
    if not all(math.isfinite(v) for v in (start, stop, step)):
        raise ValueError("axis start/stop/step must be finite numbers")
    if start == stop:
        return [round(start, 10)]
    if step == 0:
        raise ValueError("axis step must be non-zero when start != stop")
    span = stop - start
    if (span > 0) != (step > 0):
        raise ValueError("axis step sign must move start toward stop")
    n = int(math.floor(span / step + 1e-9))  # number of intervals; +eps keeps on-grid endpoints
    return [round(start + i * step, 10) for i in range(n + 1)]
```

- [ ] **Step 5: Run the tests to confirm they pass.**

Run: `python -m pytest tests/test_scenario_catalog.py -k expand_axis -v`
Expected: 5 passed.

- [ ] **Step 6: Commit.**

```bash
git add backend/app/config.py backend/app/services/domains/scenario_catalog.py tests/test_scenario_catalog.py
git commit -m "feat(scenario): add scenario_grid_max_cells config + expand_axis ladder"
```

---

## Task 2: `generate_grid` cross product + cell naming

**Files:**
- Modify: `backend/app/services/domains/scenario_catalog.py`
- Test: `tests/test_scenario_catalog.py`

💡 **Learning-mode contribution point:** `_grid_cell_name` (the scenario-naming policy) is a genuine design choice — sign formatting, % vs absolute, axis ordering, collision handling. The reference below is a working default the user may rewrite. The cap policy in `generate_grid` (limit value, hard error vs soft warning) is the second such point.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_scenario_catalog.py`:

```python
def test_generate_grid_cross_product_count_and_stresses():
    spec = {
        "name": "spot_vol_grid",
        "combine_mode": "cross_product",
        "axes": [
            {"param": "spot", "start": -0.20, "stop": 0.20, "step": 0.10,
             "stress_type": "PERCENTAGE", "level": "portfolio"},
            {"param": "vol", "start": 0.0, "stop": 0.20, "step": 0.10,
             "stress_type": "PERCENTAGE", "level": "portfolio"},
        ],
    }
    specs = scenario_catalog.generate_grid(spec)
    assert len(specs) == 5 * 3  # spot{-.2,-.1,0,.1,.2} x vol{0,.1,.2}
    # every cell shocks BOTH params
    assert all({s["param"] for s in cell["stresses"]} == {"spot", "vol"} for cell in specs)
    # a known cell exists with the real values (not defaults)
    names = {cell["name"] for cell in specs}
    assert any("spot" in n and "vol" in n for n in names)

    # the corner cell (spot -0.20, vol +0.20) exists with values carried through
    # unscaled (fractions) — non-default numbers, per the real-value test lesson.
    def _val(cell, param):
        return next(st["value"] for st in cell["stresses"] if st["param"] == param)
    assert any(
        _val(c, "spot") == pytest.approx(-0.20) and _val(c, "vol") == pytest.approx(0.20)
        for c in specs
    )


def test_generate_grid_carries_level_and_target():
    spec = {
        "name": "name_spot_ladder",
        "axes": [
            {"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1,
             "stress_type": "PERCENTAGE", "level": "underlying", "target": "000852.SH"},
        ],
    }
    specs = scenario_catalog.generate_grid(spec)
    assert len(specs) == 3
    st = specs[0]["stresses"][0]
    assert st["level"] == "underlying"
    assert st["target"] == "000852.SH"


def test_generate_grid_rejects_duplicate_param():
    spec = {"name": "dup", "axes": [
        {"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1},
        {"param": "spot", "start": 0.0, "stop": 0.2, "step": 0.1},
    ]}
    with pytest.raises(ValueError, match="[Dd]uplicate"):
        scenario_catalog.generate_grid(spec)


def test_generate_grid_rejects_unknown_param():
    spec = {"name": "bad", "axes": [{"param": "spread", "start": 0, "stop": 1, "step": 1}]}
    with pytest.raises(ValueError, match="param"):
        scenario_catalog.generate_grid(spec)


def test_generate_grid_enforces_cap(monkeypatch):
    # Settings is a frozen dataclass, so patch the name scenario_catalog imported
    # (it calls get_settings().scenario_grid_max_cells) rather than mutating it.
    class _Stub:
        scenario_grid_max_cells = 8
    monkeypatch.setattr(scenario_catalog, "get_settings", lambda: _Stub())
    spec = {"name": "big", "axes": [
        {"param": "spot", "start": 0.0, "stop": 1.0, "step": 0.1},   # 11 points
    ]}
    with pytest.raises(ValueError, match="cap"):
        scenario_catalog.generate_grid(spec)


def test_generate_grid_rejects_bad_combine_mode():
    spec = {"name": "x", "combine_mode": "union",
            "axes": [{"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1}]}
    with pytest.raises(ValueError, match="combine_mode"):
        scenario_catalog.generate_grid(spec)
```

- [ ] **Step 2: Run to confirm failure.**

Run: `python -m pytest tests/test_scenario_catalog.py -k generate_grid -v`
Expected: FAIL — `has no attribute 'generate_grid'`.

- [ ] **Step 3: Implement `_grid_cell_name` and `generate_grid`.** Add to `scenario_catalog.py` (after `expand_axis`):

```python
def _grid_cell_name(cell: list[tuple[str, float, str]]) -> str:
    """Readable, unique name for one grid cell: [(param, value, stress_type), ...].

    PERCENTAGE values render as signed %, others as signed numbers, e.g.
    "spot-10% / vol+20%". Uniqueness holds because each cell is a distinct
    value-combination over a fixed axis order.
    """
    parts = []
    for param, value, stype in cell:
        if stype == "PERCENTAGE":
            parts.append(f"{param}{value * 100:+g}%")
        else:
            parts.append(f"{param}{value:+g}")
    return " / ".join(parts)


def generate_grid(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand a grid spec into a list of scenario spec dicts (cross product).

    spec = {name, combine_mode='cross_product',
            axes: [{param, start, stop, step, stress_type?, level?, target?}]}.
    Each generated scenario carries one stress per axis (the cell's value).
    Raises ValueError (-> REST 400) on bad axes, duplicate param, or cap breach.
    Underlying-level target validity is enforced later by build_custom at save.
    """
    name = str(spec.get("name", "")).strip()
    if not name:
        raise ValueError("grid set requires a name")
    combine_mode = str(spec.get("combine_mode", "cross_product"))
    if combine_mode != "cross_product":
        raise ValueError(
            f"Unsupported combine_mode {combine_mode!r}; v1 supports 'cross_product'"
        )
    axes = spec.get("axes") or []
    if not axes:
        raise ValueError("grid set requires at least one axis")

    expanded: list[tuple[dict, list[float]]] = []
    seen: set[str] = set()
    for ax in axes:
        param = str(ax.get("param", "")).lower()
        if param not in _PARAM_TO_METHOD:
            raise ValueError(
                f"Unsupported grid param {param!r}; supports {sorted(_PARAM_TO_METHOD)}"
            )
        if param in seen:
            raise ValueError(f"Duplicate grid axis for param {param!r}")
        seen.add(param)
        expanded.append((ax, expand_axis(ax["start"], ax["stop"], ax["step"])))

    total = math.prod(len(values) for _, values in expanded)
    cap = get_settings().scenario_grid_max_cells
    if total > cap:
        raise ValueError(
            f"grid would generate {total} scenarios, exceeding the cap of {cap}"
        )

    out: list[dict[str, Any]] = []
    for combo in itertools.product(*[values for _, values in expanded]):
        stresses: list[dict[str, Any]] = []
        cell: list[tuple[str, float, str]] = []
        for (ax, _values), value in zip(expanded, combo):
            stype = str(ax.get("stress_type", "PERCENTAGE")).upper()
            level = str(ax.get("level", "portfolio")).lower()
            stresses.append({
                "param": str(ax["param"]).lower(),
                "stress_type": stype,
                "value": value,
                "level": level,
                "target": ax.get("target"),
            })
            cell.append((str(ax["param"]).lower(), value, stype))
        out.append({"name": _grid_cell_name(cell), "description": "", "stresses": stresses})
    return out
```

- [ ] **Step 4: Run to confirm pass.**

Run: `python -m pytest tests/test_scenario_catalog.py -k generate_grid -v`
Expected: 6 passed.

- [ ] **Step 5: Commit.**

```bash
git add backend/app/services/domains/scenario_catalog.py tests/test_scenario_catalog.py
git commit -m "feat(scenario): generate_grid cross-product expansion + cell naming"
```

---

## Task 3: Sidecar persistence + Set classification

**Files:**
- Modify: `backend/app/services/domains/scenario_catalog.py` (`save_set`, `delete_set`, `list_sets_detailed`; add `_sidecar_path`, `read_set_meta`, `_axes_summary`, `list_sets_full`)
- Test: `tests/test_scenario_catalog.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_scenario_catalog.py`:

```python
def test_save_set_writes_sidecar_and_read_meta_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    grid = {"name": "g", "combine_mode": "cross_product",
            "axes": [{"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1,
                      "stress_type": "PERCENTAGE", "level": "portfolio"}]}
    specs = scenario_catalog.generate_grid(grid)
    scenarios = [scenario_catalog.build_custom(s) for s in specs]
    scenario_catalog.save_set("g", scenarios, grid_spec=grid)
    assert (tmp_path / "g.set.json").exists()
    meta = scenario_catalog.read_set_meta("g")
    assert meta["kind"] == "grid"
    assert meta["combine_mode"] == "cross_product"
    assert meta["axes"][0]["param"] == "spot"
    assert meta["count"] == 3


def test_read_set_meta_none_when_no_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    s = scenario_catalog.build_custom({"name": "S", "stresses": [{"param": "spot", "value": -0.1}]})
    scenario_catalog.save_set("plain", [s])  # no grid_spec -> no sidecar
    assert scenario_catalog.read_set_meta("plain") is None


def test_delete_set_removes_sidecar_too(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    grid = {"name": "g", "axes": [{"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1}]}
    scenarios = [scenario_catalog.build_custom(s) for s in scenario_catalog.generate_grid(grid)]
    scenario_catalog.save_set("g", scenarios, grid_spec=grid)
    scenario_catalog.delete_set("g")
    assert not (tmp_path / "g.yaml").exists()
    assert not (tmp_path / "g.set.json").exists()


def test_list_sets_full_includes_grid_and_legacy_multi(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    # 1) a generated grid (has sidecar)
    grid = {"name": "grid_a", "axes": [
        {"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1},
        {"param": "vol", "start": 0.0, "stop": 0.2, "step": 0.1}]}
    scenario_catalog.save_set("grid_a",
        [scenario_catalog.build_custom(s) for s in scenario_catalog.generate_grid(grid)],
        grid_spec=grid)
    # 2) a legacy multi-scenario set (no sidecar, count 2)
    s1 = scenario_catalog.build_custom({"name": "A", "stresses": [{"param": "spot", "value": -0.1}]})
    s2 = scenario_catalog.build_custom({"name": "B", "stresses": [{"param": "vol", "value": 0.2}]})
    scenario_catalog.save_set("legacy_multi", [s1, s2])
    # 3) a single custom scenario (must NOT appear as a Set)
    solo = scenario_catalog.build_custom({"name": "S", "stresses": [{"param": "spot", "value": -0.1}]})
    scenario_catalog.save_set("solo", [solo])

    full = {d["name"]: d for d in scenario_catalog.list_sets_full()}
    assert "grid_a" in full and full["grid_a"]["has_grid"] is True
    assert full["grid_a"]["num_scenarios"] == 6
    assert full["grid_a"]["axes_summary"] == "spot × vol"
    assert "legacy_multi" in full and full["legacy_multi"]["has_grid"] is False
    assert "solo" not in full


def test_list_sets_detailed_excludes_sidecar_grids(tmp_path, monkeypatch):
    # A 1-cell grid is a Set (sidecar present) and must NOT leak into the
    # single-custom list even though it holds exactly one scenario.
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    grid = {"name": "one_cell", "axes": [{"param": "spot", "start": 0.1, "stop": 0.1, "step": 0.1}]}
    scenario_catalog.save_set("one_cell",
        [scenario_catalog.build_custom(s) for s in scenario_catalog.generate_grid(grid)],
        grid_spec=grid)
    solo = scenario_catalog.build_custom({"name": "S", "stresses": [{"param": "spot", "value": -0.1}]})
    scenario_catalog.save_set("solo", [solo])
    names = {d["name"] for d in scenario_catalog.list_sets_detailed()}
    assert "solo" in names
    assert "one_cell" not in names
```

- [ ] **Step 2: Run to confirm failure.**

Run: `python -m pytest tests/test_scenario_catalog.py -k "sidecar or list_sets_full or read_set_meta or excludes_sidecar" -v`
Expected: FAIL — missing attributes / `save_set() got an unexpected keyword argument 'grid_spec'`.

- [ ] **Step 3: Implement.** In `scenario_catalog.py`:

(a) Replace `save_set` with:

```python
def _sidecar_path(name: str) -> Path:
    return _sets_dir() / f"{_safe_name(name)}.set.json"


def save_set(name: str, scenarios: list[Any], grid_spec: dict[str, Any] | None = None) -> str:
    quantark.ensure_quantark_path()
    from stresstest.scenario.scenario_storage import ScenarioStorage
    target = _sets_dir() / f"{_safe_name(name)}.yaml"
    ScenarioStorage.save_scenarios(scenarios, str(target))
    sidecar = _sidecar_path(name)
    if grid_spec is not None:
        meta = {
            "kind": "grid",
            "combine_mode": grid_spec.get("combine_mode", "cross_product"),
            "axes": grid_spec.get("axes", []),
            "count": len(scenarios),
            "created_at": datetime.utcnow().isoformat(),
        }
        sidecar.write_text(json.dumps(meta, indent=2))
    elif sidecar.exists():
        # Overwriting a former grid set with a plain save: drop the stale sidecar
        # so classification stays truthful.
        sidecar.unlink()
    return str(target)


def read_set_meta(name: str) -> dict[str, Any] | None:
    """Load the grid sidecar for a set, or None if it has none / is unreadable."""
    path = _sidecar_path(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
```

(b) Replace `delete_set` with:

```python
def delete_set(name: str) -> None:
    target = _sets_dir() / f"{_safe_name(name)}.yaml"
    if not target.exists():
        raise ValueError(f"Scenario set not found: {name}")
    target.unlink()
    sidecar = _sidecar_path(name)
    if sidecar.exists():
        sidecar.unlink()
```

(c) In `list_sets_detailed`, skip sidecar-backed files. Change the loop body so it reads:

```python
    out: list[dict[str, Any]] = []
    for stem in list_sets():
        if read_set_meta(stem) is not None:
            continue  # a generated Set, not a single custom scenario
        try:
            detail = get_set(stem)
        except Exception:
            continue
        if detail.get("num_scenarios", 1) == 1:
            out.append(detail)
    return out
```

(d) Add the Set summary helpers at the end of the file:

```python
def _axes_summary(meta: dict[str, Any] | None) -> str:
    axes = (meta or {}).get("axes", []) or []
    return " × ".join(str(a.get("param", "?")) for a in axes)


def list_sets_full() -> list[dict[str, Any]]:
    """All multi-scenario Sets: those with a grid sidecar OR >=2 scenarios.

    Single custom scenarios (1 scenario, no sidecar) are excluded — they are
    surfaced by list_sets_detailed instead.
    """
    out: list[dict[str, Any]] = []
    for stem in list_sets():
        meta = read_set_meta(stem)
        try:
            specs = list_set_specs(stem)
        except Exception:
            continue
        n = len(specs)
        if meta is None and n < 2:
            continue
        out.append({
            "name": stem,
            "num_scenarios": n,
            "combine_mode": (meta or {}).get("combine_mode"),
            "axes_summary": _axes_summary(meta),
            "has_grid": meta is not None,
            "axes": (meta or {}).get("axes", []) or [],
        })
    return out
```

- [ ] **Step 4: Run the new tests AND the existing catalog suite (regression).**

Run: `python -m pytest tests/test_scenario_catalog.py -v`
Expected: all passed (new + existing, including `test_list_sets_detailed_excludes_multi_scenario_sets`).

- [ ] **Step 5: Commit.**

```bash
git add backend/app/services/domains/scenario_catalog.py tests/test_scenario_catalog.py
git commit -m "feat(scenario): grid sidecar persistence + Set classification (list_sets_full)"
```

---

## Task 4: Pydantic schemas

**Files:**
- Modify: `backend/app/schemas.py` (after `ScenarioSetDetailOut`, ~line 1425)
- Test: `tests/test_scenario_test_schemas.py`

- [ ] **Step 1: Write the failing test.** Append to `tests/test_scenario_test_schemas.py`:

```python
def test_scenario_grid_request_defaults():
    from app.schemas import ScenarioGridRequest
    req = ScenarioGridRequest(name="g", axes=[{"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1}])
    assert req.combine_mode == "cross_product"
    assert req.axes[0].stress_type == "PERCENTAGE"
    assert req.axes[0].level == "portfolio"


def test_scenario_set_summary_out_shape():
    from app.schemas import ScenarioSetSummaryOut
    out = ScenarioSetSummaryOut(name="g", num_scenarios=6, combine_mode="cross_product",
                                axes_summary="spot × vol", has_grid=True,
                                axes=[{"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1}])
    assert out.has_grid is True
    assert out.axes[0].param == "spot"
```

- [ ] **Step 2: Run to confirm failure.**

Run: `python -m pytest tests/test_scenario_test_schemas.py -k "grid or summary" -v`
Expected: FAIL — `ImportError: cannot import name 'ScenarioGridRequest'`.

- [ ] **Step 3: Implement.** In `backend/app/schemas.py`, after `ScenarioSetDetailOut`:

```python
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
```

- [ ] **Step 4: Run to confirm pass.**

Run: `python -m pytest tests/test_scenario_test_schemas.py -k "grid or summary" -v`
Expected: 2 passed.

- [ ] **Step 5: Commit.**

```bash
git add backend/app/schemas.py tests/test_scenario_test_schemas.py
git commit -m "feat(scenario): pydantic schemas for grid request + Set summary"
```

---

## Task 5: REST endpoints

**Files:**
- Modify: `backend/app/main.py` (scenario-test routes block, ~3214–3252)
- Test: `tests/test_scenario_test_api.py`

> **Route-ordering caveat:** `GET /sets/full` MUST be declared **before** `GET /sets/{name}` or FastAPI matches `full` as `{name}`. Place the new `GET /sets/full` and `POST /sets/generate` immediately after the existing `GET /sets` (list) and before `GET /sets/{name}`. `GET /sets/{name}/scenarios` is more specific than `/sets/{name}` so its relative order is safe.

💡 **Learning-mode contribution point:** the **name-collision policy** on `POST /sets/generate`. The reference below **overwrites** an existing same-named set (matches the existing `save_set` semantics and supports re-edit). The user may instead reject with 409 when `_safe_name(name)+".yaml"` already exists and has no grid sidecar.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_scenario_test_api.py`:

```python
def test_generate_set_creates_grid(client, session, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.domains.scenario_catalog._sets_dir", lambda: tmp_path)
    payload = {
        "name": "spot_vol_grid",
        "combine_mode": "cross_product",
        "axes": [
            {"param": "spot", "start": -0.2, "stop": 0.2, "step": 0.1, "stress_type": "PERCENTAGE", "level": "portfolio"},
            {"param": "vol", "start": 0.0, "stop": 0.2, "step": 0.1, "stress_type": "PERCENTAGE", "level": "portfolio"},
        ],
    }
    r = client.post("/api/scenario-test/sets/generate", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["num_scenarios"] == 15
    # appears in the full Set list, not the single-custom list
    full = {d["name"]: d for d in client.get("/api/scenario-test/sets/full").json()}
    assert "spot_vol_grid" in full and full["spot_vol_grid"]["has_grid"] is True
    assert "spot_vol_grid" not in {d["name"] for d in client.get("/api/scenario-test/sets").json()}


def test_generate_set_400_on_bad_axis(client, session, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.domains.scenario_catalog._sets_dir", lambda: tmp_path)
    r = client.post("/api/scenario-test/sets/generate",
                    json={"name": "bad", "axes": [{"param": "spot", "start": 0.0, "stop": 0.2, "step": -0.1}]})
    assert r.status_code == 400


def test_get_set_scenarios_lists_members(client, session, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.domains.scenario_catalog._sets_dir", lambda: tmp_path)
    client.post("/api/scenario-test/sets/generate",
                json={"name": "ladder", "axes": [{"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1}]})
    r = client.get("/api/scenario-test/sets/ladder/scenarios")
    assert r.status_code == 200
    members = r.json()
    assert len(members) == 3
    assert members[0]["stresses"][0]["param"] == "spot"


def test_generated_set_runs_all_scenarios(client, session, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.domains.scenario_catalog._sets_dir", lambda: tmp_path)
    import app.services.scenario_test_runner as _runner
    monkeypatch.setattr(_runner, "submit_async_task", lambda *a, **kw: None)
    pf = _make_portfolio(session)
    client.post("/api/scenario-test/sets/generate",
                json={"name": "g", "axes": [
                    {"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1},
                    {"param": "vol", "start": 0.0, "stop": 0.1, "step": 0.1}]})
    r = client.post("/api/scenario-test/runs", json={"portfolio_id": pf.id, "scenario_sets": ["g"]})
    assert r.status_code == 200
    assert len(r.json()["scenario_spec"]["custom"]) == 6  # 3 x 2 expanded inline
```

- [ ] **Step 2: Run to confirm failure.**

Run: `python -m pytest tests/test_scenario_test_api.py -k "generate_set or get_set_scenarios or generated_set_runs" -v`
Expected: FAIL — 404/405 (routes not defined).

- [ ] **Step 3: Implement.** In `backend/app/main.py`:

First extend the imports of scenario schemas. Find where `ScenarioSetDetailOut` / `ScenarioSetSavedOut` are imported and add `ScenarioGridRequest, ScenarioGridSavedOut, ScenarioSetSummaryOut`. (If schemas are imported via `from .schemas import (...)`, add the names there.)

Then, immediately **after** the existing `GET /api/scenario-test/sets` route (`scenario_test_list_sets`) and **before** `GET /api/scenario-test/sets/{name}`, add:

```python
    @app.get("/api/scenario-test/sets/full", response_model=list[ScenarioSetSummaryOut])
    def scenario_test_list_sets_full():
        from .services.domains import scenario_catalog

        return [ScenarioSetSummaryOut(**d) for d in scenario_catalog.list_sets_full()]

    @app.post("/api/scenario-test/sets/generate", response_model=ScenarioGridSavedOut)
    def scenario_test_generate_set(payload: ScenarioGridRequest):
        from .services.domains import scenario_catalog

        spec = payload.model_dump()
        try:
            specs = scenario_catalog.generate_grid(spec)
            scenarios = [scenario_catalog.build_custom(s) for s in specs]
            path = scenario_catalog.save_set(payload.name, scenarios, grid_spec=spec)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ScenarioGridSavedOut(name=payload.name, num_scenarios=len(scenarios), path=path)
```

Then, after the existing `GET /api/scenario-test/sets/{name}` route, add:

```python
    @app.get(
        "/api/scenario-test/sets/{name}/scenarios",
        response_model=list[ScenarioSpec],
    )
    def scenario_test_get_set_scenarios(name: str):
        from .services.domains import scenario_catalog

        try:
            return [ScenarioSpec(**s) for s in scenario_catalog.list_set_specs(name)]
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
```

(`ScenarioSpec` is already imported for the runs route; if not, add it to the schema import list.)

- [ ] **Step 4: Run the new tests + the existing API suite (route-ordering regression).**

Run: `python -m pytest tests/test_scenario_test_api.py -v`
Expected: all passed (the existing `/sets/{name}` CRUD tests still pass — confirms `/sets/full` didn't shadow them).

- [ ] **Step 5: Commit.**

```bash
git add backend/app/main.py tests/test_scenario_test_api.py
git commit -m "feat(scenario): REST generate/full/members endpoints for Scenario Sets"
```

---

## Task 6: Agent tool + wiring

**Files:**
- Modify: `backend/app/tools/scenario_test.py`
- Modify: `backend/app/tools/__init__.py` (import block ~114; `QUANT_AGENT_TOOLS` ~172)
- Modify: `backend/app/services/agents.py` (allowlist ~350)
- Modify: `backend/app/skills/references/risk/scenario-test.md` (short note)
- Test: `tests/test_scenario_test_tools.py`, `tests/test_capability_assignments.py`

- [ ] **Step 1: Write the failing tests.**

In `tests/test_scenario_test_tools.py`, extend the two `<=` subset assertions to include the new name, and add a behavior test:

```python
def test_generate_scenario_set_tool_registered():
    names = {t.name for t in QUANT_AGENT_TOOLS}
    assert "generate_scenario_set" in names
    assert "generate_scenario_set" in DEEP_AGENT_TOOL_NAMES


def test_generate_scenario_set_tool_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.domains.scenario_catalog._sets_dir", lambda: tmp_path)
    from app.tools.scenario_test import generate_scenario_set_tool
    out = generate_scenario_set_tool.invoke({
        "name": "tool_grid",
        "axes": [{"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1}],
    })
    assert out["num_scenarios"] == 3
    assert out["name"] == "tool_grid"
```

In `tests/test_capability_assignments.py`, bump the pinned count from `83` to `84` and update its comment to mention the 5th scenario-test tool:

```python
    # +5 scenario-test tools (list_scenario_library, run_scenario_test,
    #   get_scenario_test_run, save_scenario_set, generate_scenario_set).
    assert len(QUANT_AGENT_TOOLS) == 84
```

- [ ] **Step 2: Run to confirm failure.**

Run: `python -m pytest tests/test_scenario_test_tools.py tests/test_capability_assignments.py -v`
Expected: FAIL — tool missing + count is 83 not 84.

- [ ] **Step 3: Implement the tool.** In `backend/app/tools/scenario_test.py`, add the input model (near the other `*Input` classes):

```python
class GenerateScenarioSetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    combine_mode: str = "cross_product"
    axes: list[dict] = Field(default_factory=list)
```

Add the tool (after `save_scenario_set_tool`):

```python
@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("generate_scenario_set", args_schema=GenerateScenarioSetInput)
def generate_scenario_set_tool(
    name: str, combine_mode: str = "cross_product", axes: list[dict] | None = None
) -> dict[str, Any]:
    """Generate and save a named Scenario Set as the cross product of parameter
    axes, each defined by (start, stop, step) over spot/vol/rate/dividend. Each
    generated scenario shocks every axis together (one grid cell). Returns the
    saved set name and scenario count; run it later via run_scenario_test with
    scenario_set=name."""
    spec = {"name": name, "combine_mode": combine_mode, "axes": axes or []}
    specs = scenario_catalog.generate_grid(spec)
    scenarios = [scenario_catalog.build_custom(s) for s in specs]
    path = scenario_catalog.save_set(name, scenarios, grid_spec=spec)
    return {"name": name, "num_scenarios": len(scenarios), "path": path}
```

Extend `list_scenario_library_tool` to surface Sets — change its return to:

```python
    return {"predefined": scenario_catalog.list_predefined(),
            "saved_sets": scenario_catalog.list_sets(),
            "sets": scenario_catalog.list_sets_full()}
```

- [ ] **Step 4: Wire registration.**
  - In `backend/app/tools/__init__.py`, add `generate_scenario_set_tool` to the `from .scenario_test import (...)` block and to the `QUANT_AGENT_TOOLS` list under the "Scenario test tools" comment.
  - In `backend/app/services/agents.py`, add `"generate_scenario_set",` to the scenario-test allowlist block.

- [ ] **Step 5: Skill reference note.** In `backend/app/skills/references/risk/scenario-test.md`, add one short line under the scenarios/sets section (keep the whole body under the 500-token cap):

```markdown
- **Generate a grid Set:** `generate_scenario_set(name, axes=[{param, start, stop, step, stress_type, level, target?}])`
  builds the cross product of the axes (one scenario per cell) and saves it as a reusable
  Set. Run it with `run_scenario_test(..., scenario_set=name)`.
```

- [ ] **Step 6: Run the tool + capability + reference-doc tests.**

Run: `python -m pytest tests/test_scenario_test_tools.py tests/test_capability_assignments.py tests/test_reference_docs.py -v`
Expected: all passed. (If `test_reference_docs.py` validates frontmatter/format, ensure the edit keeps the file valid.)

- [ ] **Step 7: Commit.**

```bash
git add backend/app/tools/scenario_test.py backend/app/tools/__init__.py backend/app/services/agents.py backend/app/skills/references/risk/scenario-test.md tests/test_scenario_test_tools.py tests/test_capability_assignments.py
git commit -m "feat(scenario): generate_scenario_set agent tool + wiring"
```

---

## Task 7: Frontend types + api client

**Files:**
- Modify: `frontend/src/types.ts` (after `ScenarioSetDetail`, ~line 1104)
- Modify: `frontend/src/api/client.ts` (after `deleteScenarioSet`, ~line 141)

- [ ] **Step 1: Add types.** In `frontend/src/types.ts`:

```typescript
export type GridAxisSpec = {
  param: 'spot' | 'vol' | 'rate' | 'dividend';
  start: number;
  stop: number;
  step: number;
  stress_type: 'ABSOLUTE' | 'PERCENTAGE' | 'VALUE';
  level: 'portfolio' | 'underlying';
  target?: string | number | null;
};

export type ScenarioGridRequest = {
  name: string;
  combine_mode: 'cross_product';
  axes: GridAxisSpec[];
};

export type ScenarioSetSummary = {
  name: string;
  num_scenarios: number;
  combine_mode: string | null;
  axes_summary: string;
  has_grid: boolean;
  axes: GridAxisSpec[];
};
```

- [ ] **Step 2: Add api client functions.** In `frontend/src/api/client.ts`, add `ScenarioGridRequest, ScenarioSetSummary` to the type import block at the top, then append:

```typescript
export const fetchScenarioSetsFull = () =>
  api<ScenarioSetSummary[]>('/api/scenario-test/sets/full');

export const getScenarioSetScenarios = (name: string) =>
  api<ScenarioSpec[]>(`/api/scenario-test/sets/${encodeURIComponent(name)}/scenarios`);

export const generateScenarioSet = (body: ScenarioGridRequest) =>
  api<{ name: string; num_scenarios: number; path: string }>(
    '/api/scenario-test/sets/generate',
    { method: 'POST', body: JSON.stringify(body) },
  );
```

- [ ] **Step 3: Typecheck.**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit.**

```bash
git add frontend/src/types.ts frontend/src/api/client.ts
git commit -m "feat(scenario): frontend types + api client for Sets + grid generation"
```

---

## Task 8: `ScenarioGridDialog` component

**Files:**
- Create: `frontend/src/components/ScenarioGridDialog.tsx`
- Create: `frontend/src/components/ScenarioGridDialog.css`
- Test: `frontend/src/components/ScenarioGridDialog.test.tsx`

> Mirror `ScenarioBuilderDialog.tsx` (Modal + token-only CSS + ÷100-on-submit for PERCENTAGE). The live cell count is computed client-side with the same `floor(span/step + eps) + 1` math as `expand_axis`. `MAX_CELLS` mirrors backend `scenario_grid_max_cells` (200); keep the two in sync.

- [ ] **Step 1: Write the failing test.** Create `frontend/src/components/ScenarioGridDialog.test.tsx`:

```typescript
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ScenarioGridDialog } from './ScenarioGridDialog';

describe('ScenarioGridDialog', () => {
  afterEach(() => vi.restoreAllMocks());

  it('previews the cross-product cell count and submits a fraction-scaled grid', async () => {
    const onGenerate = vi.fn().mockResolvedValue(undefined);
    render(
      <ScenarioGridDialog open initial={null} existingNames={[]}
        onGenerate={onGenerate} onClose={() => {}} />,
    );
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Set name'), 'spot_vol');
    // axis 0 defaults to spot; fill its range -20%..20% step 10% (entered as %)
    await user.clear(screen.getByLabelText('start 0')); await user.type(screen.getByLabelText('start 0'), '-20');
    await user.clear(screen.getByLabelText('stop 0')); await user.type(screen.getByLabelText('stop 0'), '20');
    await user.clear(screen.getByLabelText('step 0')); await user.type(screen.getByLabelText('step 0'), '10');

    // 5 cells for one axis
    expect(screen.getByText(/→\s*5\s*scenarios/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /generate/i }));
    expect(onGenerate).toHaveBeenCalledTimes(1);
    const body = onGenerate.mock.calls[0][0];
    expect(body.name).toBe('spot_vol');
    expect(body.combine_mode).toBe('cross_product');
    // -20% entered -> -0.2 fraction on the wire
    expect(body.axes[0].start).toBeCloseTo(-0.2);
    expect(body.axes[0].step).toBeCloseTo(0.1);
  });

  it('disables Generate when the cell count exceeds the cap', async () => {
    render(
      <ScenarioGridDialog open initial={null} existingNames={[]}
        onGenerate={vi.fn()} onClose={() => {}} />,
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Set name'), 'huge');
    await user.clear(screen.getByLabelText('start 0')); await user.type(screen.getByLabelText('start 0'), '0');
    await user.clear(screen.getByLabelText('stop 0')); await user.type(screen.getByLabelText('stop 0'), '100');
    await user.clear(screen.getByLabelText('step 0')); await user.type(screen.getByLabelText('step 0'), '0.1');
    expect(screen.getByRole('button', { name: /generate/i })).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run to confirm failure.**

Run: `cd frontend && npx vitest run src/components/ScenarioGridDialog.test.tsx`
Expected: FAIL — cannot resolve `./ScenarioGridDialog`.

- [ ] **Step 3: Implement the component.** Create `frontend/src/components/ScenarioGridDialog.tsx`:

```typescript
import { useEffect, useMemo, useState } from 'react';
import { Button } from './Button';
import { Modal } from './Modal';
import type { GridAxisSpec, ScenarioGridRequest, ScenarioSetSummary } from '../types';
import './ScenarioGridDialog.css';

const MAX_CELLS = 200; // mirror backend scenario_grid_max_cells
const PARAMS: GridAxisSpec['param'][] = ['spot', 'vol', 'rate', 'dividend'];
const STRESS_TYPES: GridAxisSpec['stress_type'][] = ['PERCENTAGE', 'ABSOLUTE', 'VALUE'];
const LEVELS: Array<'portfolio' | 'underlying'> = ['portfolio', 'underlying'];

// UI-facing axis row: numbers are kept as raw strings so the fields can be empty
// mid-edit; PERCENTAGE rows are entered in % and divided by 100 on submit.
type AxisRow = {
  param: GridAxisSpec['param'];
  start: string; stop: string; step: string;
  stress_type: GridAxisSpec['stress_type'];
  level: 'portfolio' | 'underlying';
  target: string;
};

function emptyAxis(): AxisRow {
  return { param: 'spot', start: '', stop: '', step: '', stress_type: 'PERCENTAGE', level: 'portfolio', target: '' };
}

function fromSpec(ax: GridAxisSpec): AxisRow {
  const scale = ax.stress_type === 'PERCENTAGE' ? 100 : 1;
  const s = (n: number) => String(Number((n * scale).toFixed(8)));
  return {
    param: ax.param, start: s(ax.start), stop: s(ax.stop), step: s(ax.step),
    stress_type: ax.stress_type, level: ax.level,
    target: ax.target == null ? '' : String(ax.target),
  };
}

// Cell count for one axis — same math as backend expand_axis.
function axisCount(ax: AxisRow): number {
  const start = Number(ax.start), stop = Number(ax.stop), step = Number(ax.step);
  if (![start, stop, step].every(Number.isFinite) || ax.start === '' || ax.stop === '' || ax.step === '') return 0;
  if (start === stop) return 1;
  if (step === 0) return 0;
  if ((stop - start > 0) !== (step > 0)) return 0;
  return Math.floor((stop - start) / step + 1e-9) + 1;
}

type Props = {
  open: boolean;
  initial?: ScenarioSetSummary | null;  // present => edit (re-generate) mode, name locked
  existingNames: string[];
  onGenerate: (body: ScenarioGridRequest) => Promise<void> | void;
  onClose: () => void;
};

function canonicalName(name: string): string {
  return name.trim().replace(/[^A-Za-z0-9_.-]/g, '_');
}

export function ScenarioGridDialog({ open, initial, existingNames, onGenerate, onClose }: Props) {
  const isEdit = initial != null;
  const [name, setName] = useState('');
  const [axes, setAxes] = useState<AxisRow[]>([emptyAxis()]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setBusy(false);
    setName(initial?.name ?? '');
    setAxes(initial && initial.axes.length > 0 ? initial.axes.map(fromSpec) : [emptyAxis()]);
  }, [open, initial]);

  const updateAxis = (i: number, patch: Partial<AxisRow>) =>
    setAxes((prev) => prev.map((a, idx) => (idx === i ? { ...a, ...patch } : a)));
  const addAxis = () => setAxes((prev) => [...prev, emptyAxis()]);
  const removeAxis = (i: number) => setAxes((prev) => prev.filter((_, idx) => idx !== i));

  const total = useMemo(() => axes.reduce((acc, a) => acc * axisCount(a), 1), [axes]);
  const anyInvalidAxis = axes.some((a) => axisCount(a) === 0);
  const dupParam = new Set(axes.map((a) => a.param)).size !== axes.length;
  const nameTaken = !isEdit && existingNames.includes(canonicalName(name));
  const canGenerate =
    name.trim() !== '' && !nameTaken && axes.length > 0 && !anyInvalidAxis &&
    !dupParam && total >= 1 && total <= MAX_CELLS;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!canGenerate) {
      setError(
        nameTaken ? `A set named "${canonicalName(name)}" already exists.`
          : dupParam ? 'Each parameter may appear on at most one axis.'
          : anyInvalidAxis ? 'Every axis needs a valid start/stop/step (step sign must move start toward stop).'
          : total > MAX_CELLS ? `Grid would generate ${total} scenarios, over the cap of ${MAX_CELLS}.`
          : 'Fill in a name and at least one valid axis.',
      );
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const body: ScenarioGridRequest = {
        name: name.trim(),
        combine_mode: 'cross_product',
        axes: axes.map((a) => {
          const div = a.stress_type === 'PERCENTAGE' ? 100 : 1;
          return {
            param: a.param,
            start: Number((Number(a.start) / div).toFixed(10)),
            stop: Number((Number(a.stop) / div).toFixed(10)),
            step: Number((Number(a.step) / div).toFixed(10)),
            stress_type: a.stress_type,
            level: a.level,
            target: a.level === 'underlying' ? a.target.trim() : null,
          };
        }),
      };
      await onGenerate(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onOpenChange={(o) => { if (!o) onClose(); }}
      title={isEdit ? `Edit grid ${initial?.name}` : 'Generate scenario set'}
      layoutKey="scenario-grid"
      defaultWidth={720}
      defaultHeight={560}
    >
      <form className="wl-scenario-grid" onSubmit={submit}>
        <label className="wl-scenario-grid__field">
          <span>Set name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} disabled={isEdit}
            autoFocus aria-label="Set name" />
        </label>

        <div className="wl-scenario-grid__axes">
          <div className="wl-scenario-grid__axes-head">
            <span>Axes (cross product)</span>
            <Button type="button" variant="ghost" onClick={addAxis}>+ add axis</Button>
          </div>
          {axes.map((ax, i) => (
            <div className="wl-scenario-grid__axis" key={i}>
              <select aria-label={`param ${i}`} value={ax.param}
                onChange={(e) => updateAxis(i, { param: e.target.value as GridAxisSpec['param'] })}>
                {PARAMS.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
              <input aria-label={`start ${i}`} type="number" step="any" placeholder="start"
                value={ax.start} onChange={(e) => updateAxis(i, { start: e.target.value })} />
              <input aria-label={`stop ${i}`} type="number" step="any" placeholder="stop"
                value={ax.stop} onChange={(e) => updateAxis(i, { stop: e.target.value })} />
              <input aria-label={`step ${i}`} type="number" step="any" placeholder="step"
                value={ax.step} onChange={(e) => updateAxis(i, { step: e.target.value })} />
              <select aria-label={`type ${i}`} value={ax.stress_type}
                onChange={(e) => updateAxis(i, { stress_type: e.target.value as GridAxisSpec['stress_type'] })}>
                {STRESS_TYPES.map((t) => <option key={t} value={t}>{t.toLowerCase()}</option>)}
              </select>
              <select aria-label={`level ${i}`} value={ax.level}
                onChange={(e) => updateAxis(i, { level: e.target.value as 'portfolio' | 'underlying' })}>
                {LEVELS.map((lv) => <option key={lv} value={lv}>{lv}</option>)}
              </select>
              {ax.level === 'underlying' && (
                <input aria-label={`target ${i}`} placeholder="symbol" value={ax.target}
                  onChange={(e) => updateAxis(i, { target: e.target.value })} />
              )}
              <span className="wl-scenario-grid__axis-count" aria-hidden="true">×{axisCount(ax)}</span>
              <button type="button" className="wl-scenario-grid__remove" aria-label={`remove axis ${i}`}
                onClick={() => removeAxis(i)} disabled={axes.length === 1}>×</button>
            </div>
          ))}
        </div>

        <p className={`wl-scenario-grid__count${total > MAX_CELLS ? ' wl-scenario-grid__count--over' : ''}`}>
          → {total} scenario{total === 1 ? '' : 's'}{total > MAX_CELLS ? ` (cap ${MAX_CELLS})` : ''}
        </p>

        {error && <p className="wl-scenario-grid__error" role="alert">{error}</p>}
        <div className="wl-scenario-grid__actions">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={busy || !canGenerate}>
            {busy ? 'Generating…' : 'Generate'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
```

- [ ] **Step 4: Implement the CSS (tokens only).** Create `frontend/src/components/ScenarioGridDialog.css` by copying the structure of `ScenarioBuilderDialog.css` (same token variables, BEM `wl-scenario-grid__*` names). Key rules:

```css
.wl-scenario-grid { display: flex; flex-direction: column; gap: var(--space-3); }
.wl-scenario-grid__field { display: flex; flex-direction: column; gap: var(--space-1); }
.wl-scenario-grid__field input,
.wl-scenario-grid__axis input,
.wl-scenario-grid__axis select {
  background: var(--color-surface);
  color: var(--color-text);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: var(--space-1) var(--space-2);
}
.wl-scenario-grid__axes { display: flex; flex-direction: column; gap: var(--space-2); }
.wl-scenario-grid__axes-head { display: flex; align-items: center; justify-content: space-between; }
.wl-scenario-grid__axis { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.wl-scenario-grid__axis-count { color: var(--color-text-muted); font-variant-numeric: tabular-nums; }
.wl-scenario-grid__remove {
  background: none; border: none; color: var(--color-text-muted);
  cursor: pointer; font-size: var(--font-size-lg);
}
.wl-scenario-grid__remove:disabled { opacity: 0.4; cursor: not-allowed; }
.wl-scenario-grid__count { color: var(--color-text); font-weight: var(--font-weight-medium); }
.wl-scenario-grid__count--over { color: var(--color-danger); }
.wl-scenario-grid__error { color: var(--color-danger); }
.wl-scenario-grid__actions { display: flex; justify-content: flex-end; gap: var(--space-2); }
```

> Before committing, open `ScenarioBuilderDialog.css` and match the exact token names it uses (e.g. `--color-surface` vs `--color-bg-elevated`). Use only tokens that already exist in `src/tokens/`.

- [ ] **Step 5: Run the component test.**

Run: `cd frontend && npx vitest run src/components/ScenarioGridDialog.test.tsx`
Expected: 2 passed.

- [ ] **Step 6: Commit.**

```bash
git add frontend/src/components/ScenarioGridDialog.tsx frontend/src/components/ScenarioGridDialog.css frontend/src/components/ScenarioGridDialog.test.tsx
git commit -m "feat(scenario): ScenarioGridDialog with live cell-count preview"
```

---

## Task 9: Unified picker — Sets section, Generate entry, run wiring

**Files:**
- Modify: `frontend/src/routes/ScenarioTest.tsx`
- Modify: `frontend/src/routes/ScenarioTest.css`
- Test: `frontend/src/routes/ScenarioTest.test.tsx`

- [ ] **Step 1: Write the failing test.** Append to `frontend/src/routes/ScenarioTest.test.tsx` a test that the Sets section renders a generated Set with its count badge and that selecting it enables a run. Add the `/sets/full` route to the fetch mock:

```typescript
it('lists scenario sets with a count badge and runs the selected set', async () => {
  const sets = [
    { name: 'spot_vol', num_scenarios: 9, combine_mode: 'cross_product',
      axes_summary: 'spot × vol', has_grid: true,
      axes: [{ param: 'spot', start: -0.2, stop: 0.2, step: 0.1, stress_type: 'PERCENTAGE', level: 'portfolio', target: null }] },
  ];
  let postedBody: any = null;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(input);
    if (url === '/api/scenario-test/library') return response(library);
    if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
    if (url === '/api/portfolios') return response(portfolios);
    if (url === '/api/scenario-test/sets') return response([]);
    if (url === '/api/scenario-test/sets/full') return response(sets);
    if (url === '/api/scenario-test/runs' && init?.method === 'POST') {
      postedBody = JSON.parse(String(init?.body)); return response({ id: 11, status: 'queued' });
    }
    if (url.startsWith('/api/scenario-test/runs')) return response([]);
    return response({});
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  render(<ScenarioTestLive />);
  await waitFor(() => expect(screen.getByText('Market Crash')).toBeInTheDocument());

  // Set row + badge
  await waitFor(() => expect(screen.getByText('spot_vol')).toBeInTheDocument());
  expect(screen.getByText(/spot × vol · 9/)).toBeInTheDocument();

  const user = userEvent.setup();
  await user.click(screen.getByRole('checkbox', { name: 'spot_vol' }));
  await user.click(screen.getByRole('button', { name: /run scenario test/i }));

  await waitFor(() => expect(postedBody?.scenario_sets).toContain('spot_vol'));
});
```

(Also add `if (url === '/api/scenario-test/sets/full') return response([]);` to the other tests' fetch mocks so they don't fall through to the `{}` default — though the component guards with `Array.isArray`.)

- [ ] **Step 2: Run to confirm failure.**

Run: `cd frontend && npx vitest run src/routes/ScenarioTest.test.tsx -t "scenario sets"`
Expected: FAIL — no `spot_vol` text / no Sets section.

- [ ] **Step 3: Implement.** In `frontend/src/routes/ScenarioTest.tsx`:

(a) Extend imports:

```typescript
import { api, fetchScenarioLibrary, createScenarioTestRun, listScenarioTestRuns, fetchScenarioSets, fetchScenarioSetsFull, getScenarioSetScenarios, generateScenarioSet, saveScenarioSet, deleteScenarioSet } from '../api/client';
import type { PricingParameterProfile, Portfolio, ScenarioLibrary, ScenarioTestRun, ScenarioTestRunRequest, ScenarioSetDetail, ScenarioSetSummary, ScenarioGridRequest, ScenarioStress, ScenarioSpec } from '../types';
import { ScenarioGridDialog } from '../components/ScenarioGridDialog';
```

(b) Add state (near the other selection state):

```typescript
  const [sets, setSets] = useState<ScenarioSetSummary[]>([]);
  const [selectedSetNames, setSelectedSetNames] = useState<Set<string>>(new Set());
  const [grid, setGrid] = useState<{ initial: ScenarioSetSummary | null } | null>(null);
  const [expandedSet, setExpandedSet] = useState<string | null>(null);
  const [setMembers, setSetMembers] = useState<Record<string, ScenarioSpec[]>>({});
```

(c) In the initial-load `Promise.allSettled`, add `fetchScenarioSetsFull()` to the array and handle its result:

```typescript
      // add to the array:  fetchScenarioSetsFull(),
      // add a result branch:
      if (setsFullResult.status === 'fulfilled') {
        setSets(Array.isArray(setsFullResult.value) ? setsFullResult.value : []);
      }
```

(Destructure the extra element in the `.then(([libResult, portResult, profileResult, setsResult, setsFullResult]) => {` signature.)

(d) Add helpers:

```typescript
  const reloadSets = async () => {
    try { setSets(await fetchScenarioSetsFull()); } catch { /* keep prior */ }
  };

  const toggleSet = (name: string) =>
    setSelectedSetNames((prev) => {
      const n = new Set(prev);
      if (n.has(name)) n.delete(name); else n.add(name);
      return n;
    });

  const handleGenerate = async (body: ScenarioGridRequest) => {
    await generateScenarioSet(body);
    setGrid(null);
    await reloadSets();
  };

  const handleDeleteSet = async (name: string) => {
    if (!window.confirm(`Delete scenario set "${name}"?`)) return;
    try {
      await deleteScenarioSet(name);
      setSelectedSetNames((prev) => { const n = new Set(prev); n.delete(name); return n; });
      await reloadSets();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const toggleExpandSet = async (name: string) => {
    if (expandedSet === name) { setExpandedSet(null); return; }
    setExpandedSet(name);
    if (!setMembers[name]) {
      try {
        const members = await getScenarioSetScenarios(name);
        setSetMembers((prev) => ({ ...prev, [name]: members }));
      } catch { /* show nothing on error */ }
    }
  };
```

(e) In `handleRun`, merge set names into `scenario_sets`:

```typescript
        scenario_sets: [...selectedCustomNames, ...selectedSetNames],
```

(f) Update the Run button's `disabled` to also require no set selection:

```typescript
              disabled={submitting || selectedPortfolioId == null
                || (selectedKeys.size === 0 && selectedCustomNames.size === 0 && selectedSetNames.size === 0)}
```

(g) Add a "Scenario Sets" section after the "Custom Scenarios" section (inside `wl-scenario-test__panel--scenarios`):

```tsx
          {/* Scenario Sets (multi-scenario, generated grids) */}
          <section className="wl-scenario-test__section">
            <div className="wl-scenario-test__section-head">
              <h2 className="wl-scenario-test__section-title">Scenario Sets</h2>
              <Button variant="ghost" onClick={() => setGrid({ initial: null })}>+ Generate set</Button>
            </div>
            {sets.length === 0 ? (
              <Empty message="No scenario sets yet." symbol="◌" />
            ) : (
              <ul className="wl-scenario-test__scenario-list" role="list" aria-label="Scenario sets">
                {sets.map((s) => (
                  <li key={s.name} className="wl-scenario-test__scenario-item wl-scenario-test__scenario-item--custom">
                    <label className="wl-scenario-test__scenario-label">
                      <input type="checkbox" aria-label={s.name}
                        checked={selectedSetNames.has(s.name)} onChange={() => toggleSet(s.name)} />
                      <div className="wl-scenario-test__scenario-meta">
                        <button type="button" className="wl-scenario-test__scenario-name-btn"
                          aria-label={`View ${s.name}`} onClick={(e) => { e.preventDefault(); toggleExpandSet(s.name); }}>
                          {s.name}
                        </button>
                        <span className="wl-scenario-test__scenario-desc">
                          {(s.axes_summary || 'set')} · {s.num_scenarios}
                        </span>
                      </div>
                      <span className="wl-scenario-test__scenario-count">{s.num_scenarios} scenarios</span>
                    </label>
                    <div className="wl-scenario-test__scenario-row-actions">
                      {s.has_grid && <Button variant="ghost" onClick={() => setGrid({ initial: s })}>Edit</Button>}
                      <Button variant="ghost" onClick={() => handleDeleteSet(s.name)}>Delete</Button>
                    </div>
                    {expandedSet === s.name && (
                      <ul className="wl-scenario-test__set-members" aria-label={`${s.name} members`}>
                        {(setMembers[s.name] ?? []).map((m, i) => (
                          <li key={i} className="wl-scenario-test__set-member">
                            {m.name} — {m.stresses.length} stress{m.stresses.length === 1 ? '' : 'es'}
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
```

(h) Render the dialog near the others at the bottom:

```tsx
      {grid && (
        <ScenarioGridDialog open initial={grid.initial}
          existingNames={[...customSets.map((s) => s.name), ...sets.map((s) => s.name)]}
          onGenerate={handleGenerate} onClose={() => setGrid(null)} />
      )}
```

- [ ] **Step 4: Add CSS for the member list.** Append to `frontend/src/routes/ScenarioTest.css` (tokens only):

```css
.wl-scenario-test__set-members {
  list-style: none;
  margin: var(--space-1) 0 0;
  padding: 0 0 0 var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.wl-scenario-test__set-member {
  color: var(--color-text-muted);
  font-size: var(--font-size-sm);
  font-variant-numeric: tabular-nums;
}
```

> The test expects the badge text `spot × vol · 9`. The markup above renders `{axes_summary} · {num_scenarios}` in `__scenario-desc`. Confirm the test's `getByText(/spot × vol · 9/)` matches (the `×` is U+00D7, same as `_axes_summary`).

- [ ] **Step 5: Run the page tests (new + regression).**

Run: `cd frontend && npx vitest run src/routes/ScenarioTest.test.tsx`
Expected: all passed (existing tests still green with the `/sets/full` mock added).

- [ ] **Step 6: Commit.**

```bash
git add frontend/src/routes/ScenarioTest.tsx frontend/src/routes/ScenarioTest.css frontend/src/routes/ScenarioTest.test.tsx
git commit -m "feat(scenario): unified picker with Scenario Sets section + grid generator entry"
```

---

## Task 10: Full-suite verification + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Backend — run the scenario suite + coupling tests.**

Run:
```
python -m pytest tests/test_scenario_catalog.py tests/test_scenario_test_api.py tests/test_scenario_test_tools.py tests/test_scenario_test_schemas.py tests/test_capability_assignments.py tests/test_reference_docs.py tests/test_skills_catalog.py tests/test_routing_table.py -v
```
Expected: all passed. If `test_skills_catalog`/`test_routing_table` fail, they pin skill (not tool) sets — this change adds **no** SKILL.md, so they should be untouched; investigate any failure before proceeding.

- [ ] **Step 2: Frontend — typecheck + full scenario tests.**

Run:
```
cd frontend && npx tsc --noEmit && npx vitest run src/components/ScenarioGridDialog.test.tsx src/routes/ScenarioTest.test.tsx
```
Expected: no type errors, all tests pass.

- [ ] **Step 3: Manual smoke (optional but recommended).** Start the app, open Scenario Test, click **+ Generate set**, define `spot −20%..20% step 10% × vol 0%..20% step 10%` (preview should read `→ 15 scenarios`), Generate, confirm the Set appears with badge `spot × vol · 15`, tick it, **Run scenario test**, and confirm the run lists 15 scenarios. Verify the dialog in **dark mode + compact density** (per `UI_STYLE_GUIDE.md`).

- [ ] **Step 4: Final commit (if any smoke fixes).**

```bash
git add -A && git commit -m "test(scenario): verify Sets + grid generation end-to-end"
```

---

## Self-review notes (for the implementer)

- **Spec coverage:** data model (Task 3), grid gen (Tasks 1–2), catalog/REST (Tasks 3–5), agent tool (Task 6), unified UI + dialog (Tasks 7–9), tests throughout. Cross-product-only, optional-target, sidecar, agent-tool — all covered.
- **Type consistency:** backend `generate_grid`/`save_set(grid_spec=)`/`list_sets_full` and frontend `ScenarioSetSummary.axes` / `ScenarioGridRequest` names are used identically across tasks.
- **No SKILL.md added** → the six skill-catalog coupling files are *not* triggered; only the **tool-count** assertion (`test_capability_assignments.py`, 83→84) and the reference-doc edit are.
- **Run path untouched** — Sets flow through the existing `scenario_sets[]` expansion; no change to `scenario_test_runner` or `run_pipeline`.
```

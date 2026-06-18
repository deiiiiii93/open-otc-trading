# Scenario Sets + parametric grid generation — design

**Date:** 2026-06-09
**Branch (current):** `polish/scenario-test-ui` → new feature branch off `main`
**Status:** design, awaiting review

## Summary

Promote multi-scenario **Scenario Sets** to a first-class, managed, runnable entity on
the Scenario Test page, and add a **parametric grid generator** that produces a Set by
sweeping `(start, stop, step)` over one or more parameters (spot / vol / rate / dividend)
and taking the **cross product** of the axes — one scenario per grid cell, each cell
shocking all configured params together.

This is the QuantArk "a stress test takes a *list* of scenarios" capability surfaced as a
desk workflow: instead of hand-authoring 9 scenarios for a 3×3 spot×vol grid, the user
describes the axes and the system generates the set.

## Background / current state

- A **Scenario** = name + description + list of *stresses* (`{param, stress_type, value,
  level, target}`). Params supported today: `spot | vol | rate | dividend`
  (`scenario_catalog._PARAM_TO_METHOD`).
- A **saved set** is already persisted as a YAML of N scenarios via QuantArk
  `ScenarioStorage` in `data/scenario_sets/` (`settings.scenario_sets_dir`).
- The run path already takes a **list** of scenarios: `/api/scenario-test/runs` expands
  `scenario_sets: list[str]` into member specs (`main.py:3260`) and
  `engine.run_static_scenarios(portfolio, scenarios)` reprices each.
- **The current "flat model" deliberately hides multi-scenario sets.**
  `scenario_catalog.list_sets_detailed()` returns only `num_scenarios == 1` files; the UI
  treats each saved item as exactly one scenario, and the run wiring already routes single
  custom scenarios *by name* through `scenario_sets[]`
  (`ScenarioTest.tsx` `handleRun` → `scenario_sets: Array.from(selectedCustomNames)`).
- No grid/range/sweep helper exists in QuantArk's scenario layer — this logic is net-new.
- Storage reality: `ScenarioStorage.save_scenarios` writes only `{version, scenarios}` —
  **extra top-level keys are dropped on the next save**, so a Set's generating grid spec
  must live in a **sidecar file**, not inside the YAML.
- Migration risk is near-zero: `data/scenario_sets/` currently holds exactly one
  single-scenario file (`Index_Open_Jump_Up.yaml`).

## Decisions (resolved during brainstorming)

1. **Axis combination = cross product.** Each generated scenario is one cell of the N-D
   grid and shocks every configured param together. Count grows multiplicatively → needs a
   live preview count and a cap.
2. **Unify in the UI, keep standalone in the backend.** Scenarios and Sets remain distinct
   backend concepts (separate catalog functions / endpoints); the page presents them in one
   selectable list so the user picks any mix and runs.
3. **Sidecar JSON persistence.** A Set carries a re-editable grid definition in
   `<name>.set.json`. Sets become identifiable independent of scenario count, can be
   labeled by how they were generated, and re-opened in the generator to tweak axes.
4. **Include an agent tool.** `generate_scenario_set`, parallel to `save_scenario_set`,
   so grids can be built conversationally. (Bumps the pinned agent tool-count → catalog
   test assertions updated.)
5. **Optional underlying target per axis, default portfolio.** Each axis defaults to a
   portfolio-wide shock but may target one underlying symbol (reusing `build_custom`'s
   underlying-level validation), matching single-name spot-ladder desk use.

## Goals / non-goals

**Goals**
- Generate a named Set from cross-product grid axes over spot/vol/rate/dividend.
- Manage Sets: list (with scenario count + generation summary), view members, delete,
  re-edit the grid, run (alone or mixed with predefined/custom).
- One unified selectable list on the page; Sets stop being hidden.
- Agent tool + REST + page parity.

**Non-goals (v1, documented)**
- Independent-ladder (union) combine mode — cross product only.
- Position-level grid targeting (already unsupported in `build_custom`).
- Assembling a Set by hand-picking existing scenarios ("save selection as set") — the
  generator is the v1 creation path. Easy fast-follow.
- Per-cell editing of a generated Set's individual scenarios (edit the grid + regenerate).

## Data model

```
data/scenario_sets/
  <name>.yaml        # N scenarios, via ScenarioStorage (unchanged format)
  <name>.set.json    # sidecar — present iff this file is a generated Set
```

Sidecar shape (`<name>.set.json`):
```json
{
  "kind": "grid",
  "combine_mode": "cross_product",
  "axes": [
    {"param": "spot", "start": -0.20, "stop": 0.20, "step": 0.05,
     "stress_type": "PERCENTAGE", "level": "portfolio", "target": null},
    {"param": "vol",  "start": 0.0,  "stop": 0.20, "step": 0.10,
     "stress_type": "PERCENTAGE", "level": "portfolio", "target": null}
  ],
  "count": 45,
  "created_at": "2026-06-09T..."
}
```

**Classification rule** (`scenario_catalog`):
- file has a sidecar **OR** holds ≥2 scenarios ⇒ **Set**
- exactly 1 scenario **and** no sidecar ⇒ **single Custom Scenario**

This keeps legacy multi-sets (no sidecar) classified as Sets, and lets a 1-cell grid still
be a Set (sidecar present) without leaking into the custom list.

## Grid generation — core logic (`scenario_catalog`)

Pure, no-I/O, validate-at-submit (mirrors `build_custom`). Lives in `scenario_catalog.py`.

```python
def expand_axis(start: float, stop: float, step: float) -> list[float]:
    """Inclusive value ladder, robust to float drift.

    n = round((stop - start) / step); values = [round(start + i*step, 10)
    for i in range(n + 1)]. Requires step != 0 and sign(step) consistent with
    sign(stop - start) (or start == stop -> single value). Raises ValueError otherwise.
    """

def generate_grid(spec: dict) -> list[ScenarioSpec-dicts]:
    """spec = {name, combine_mode='cross_product', axes:[{param,start,stop,step,
    stress_type?,level?,target?}]}.
    - validate combine_mode, >=1 axis, each axis param in _PARAM_TO_METHOD
    - per-axis value ladder via expand_axis
    - itertools.product over ladders -> one scenario spec per cell; each cell has
      one stress per axis (param/value/stress_type/level/target)
    - cap: total cells <= settings.scenario_grid_max_cells (default 200) else ValueError
    - name each cell scenario via _grid_cell_name(...)  [user-contribution point]
    Returns list of scenario spec dicts ready for build_custom / save_set.
    """
```

**Naming policy (`_grid_cell_name`) — user-contribution point.** Maps a cell
(`[(param, value, stress_type), ...]`) to a stable, readable, unique scenario name, e.g.
`spot+10% / vol-5%`. Multiple valid approaches (sign formatting, % vs abs, ordering,
collision handling) — good ~8-line decision to hand to the implementer.

**Cap / guardrail policy — user-contribution point.** Default cap of 200 cells; the exact
limit + whether it is a hard error vs soft warning is a desk risk/UX call worth an explicit
choice during implementation.

Edge cases: `start == stop` → single-value axis; `step` with wrong sign → ValueError;
duplicate params across axes → ValueError (ambiguous double-shock of one param); empty
axes → ValueError; non-finite numbers → ValueError. All raised as `ValueError` so the REST
layer maps to 400 at submit, not deep in the async worker.

## Catalog API (additions to `scenario_catalog.py`)

- `save_set(name, scenarios, grid_spec: dict | None = None)` — writes YAML (existing) and,
  when `grid_spec` is given, the `<name>.set.json` sidecar.
- `read_set_meta(name) -> dict | None` — load sidecar if present.
- `list_sets_full() -> list[dict]` — every Set (sidecar OR count≥2):
  `{name, num_scenarios, combine_mode, axes_summary, has_grid}`.
- `list_sets_detailed()` — **also excludes any file that has a sidecar** (so a 1-cell grid
  never leaks into the single-custom list).
- `delete_set(name)` — unlink YAML **and** sidecar.
- `generate_grid(spec)` + `expand_axis(...)` as above.
- Existing `get_set`, `list_set_specs`, `build_custom`, `serialize_scenario` unchanged.

## REST (`main.py`)

New:
- `POST /api/scenario-test/sets/generate` — body `{name, combine_mode, axes:[...]}` →
  validates via `generate_grid`, persists via `save_set(..., grid_spec=...)`, returns
  `{name, num_scenarios, path}`. 400 on bad axes / cap exceeded; 409 (or overwrite policy)
  on name collision — **policy is a user-contribution point** (reject vs overwrite).
- `GET /api/scenario-test/sets/full` → `list[ScenarioSetSummaryOut]`.
- `GET /api/scenario-test/sets/{name}/scenarios` → `list[ScenarioSpec]` (members, for the
  view/expand).

Unchanged: existing `/sets` CRUD, `/runs` (already expands `scenario_sets[]`),
`/runs/{id}` and artifacts. `delete` path picks up sidecar cleanup via `delete_set`.

New Pydantic schemas (`schemas.py`):
- `GridAxisSpec {param, start, stop, step, stress_type='PERCENTAGE', level='portfolio',
  target: str|int|None=None}`
- `ScenarioGridRequest {name, combine_mode='cross_product', axes: list[GridAxisSpec]}`
- `ScenarioSetSummaryOut {name, num_scenarios, combine_mode: str|None, axes_summary: str,
  has_grid: bool}`

## Agent tool (`app/tools/scenario_test.py`)

- `generate_scenario_set` (DOMAIN_WRITE), args `{name, combine_mode='cross_product',
  axes: list[dict]}` → `scenario_catalog.generate_grid` + `save_set(..., grid_spec=...)`;
  returns `{name, num_scenarios, path}`.
- `list_scenario_library` extended to surface Sets (`saved_sets` already lists names;
  optionally add a `sets` summary list).
- **Tool-count coupling:** adding a tool bumps the pinned agent tool-count → update the
  skills-catalog count assertions (see memory `skill_catalog_test_coupling` /
  `project_scenario_test` "7th tool-count coupling"). The scenario-test skill reference
  (`backend/app/skills/references/risk/scenario-test.md`) gains a short grid-generation
  note (respect the 500-token skill body cap).

## Frontend

**Unified picker (`ScenarioTest.tsx`)**
- Replace the single-custom fetch with one that yields the full list including Sets:
  `fetchScenarioSets()` continues to return single customs; add `fetchScenarioSetsFull()`
  (`GET /sets/full`). Render Sets in the same selectable list, tagged with a count badge
  (`spot×vol · 9`) + a "View" affordance (expand members via
  `GET /sets/{name}/scenarios`, shown in `ScenarioDetailDialog` or a member list) + Delete.
- **No run-wiring change**: a selected Set name already flows through `scenario_sets[]`.
  Single customs and Sets are both selected-by-name; predefined via `predefined[]`.
- Add a **"+ Generate set"** action opening `ScenarioGridDialog`.

**`ScenarioGridDialog` (new component + co-located `.css`, tokens-only per
`frontend/UI_STYLE_GUIDE.md`)**
- Name field; combine-mode shown as "Cross product" (only option, v1).
- Axis rows: `param` select (spot/vol/rate/dividend), `start`, `stop`, `step`,
  `stress_type` select; optional `level`/`target` (default portfolio). Add/remove axis.
- Live "→ N scenarios" preview computed client-side from the axes (cross product), with a
  cap warning when N exceeds the limit; Generate disabled past the cap or on invalid axes.
- On Generate → `POST /sets/generate`, then reload the Set list and select the new Set.
- Re-edit: opening the dialog for an existing Set pre-fills from its sidecar grid spec.

**Types (`types.ts`)**: `GridAxisSpec`, `ScenarioGridRequest`, `ScenarioSetSummary`;
extend the api client (`fetchScenarioSetsFull`, `generateScenarioSet`,
`getScenarioSetScenarios`).

## Testing

**Backend**
- `expand_axis`: inclusivity (stop included when on grid), float-drift
  (`-0.20..0.20 step 0.05` → exact `0.05` multiples), single-value (`start==stop`),
  wrong-sign step → ValueError, step==0 → ValueError.
- `generate_grid`: cross-product **count** (3×2 → 6), each cell has one stress per axis
  with correct param/value/level/target, duplicate-param rejection, cap-exceeded
  ValueError, bad-param ValueError. **Use non-default values** (memory: "real-value test"
  lesson — a vacuous test passed because value==fallback).
- Persistence: `save_set(grid_spec=...)` writes sidecar; `read_set_meta` round-trips;
  classification (single vs set vs legacy-multi vs 1-cell-grid); `delete_set` removes both
  files.
- REST: `POST /sets/generate` happy path + 400 (bad axes, cap), `GET /sets/full`,
  `GET /sets/{name}/scenarios`, name-collision policy; **run-expansion** — generate a set,
  POST `/runs` with its name, assert N scenarios repriced.
- Tool-count + catalog/reference-doc assertions updated for `generate_scenario_set`.

**Frontend**
- `ScenarioGridDialog`: count preview math, cap disable, invalid-axis disable, submit
  payload shape, pre-fill from existing grid spec.
- Unified list: Sets render with count badge; selecting a Set adds its name to
  `scenario_sets[]` at run; View expands members; Delete removes it.
- Update `ScenarioTest.test.tsx` for the new list + generator entry point.

## Risks / coupling checklist

- **Tool-count coupling** across the six catalog/skills test files — update exact-set +
  count assertions (memory `skill_catalog_test_coupling`).
- **Flat-model invariant** — keep single-custom classification strict; the sidecar +
  count rule must not let a Set leak into the custom list nor a custom into the Set list
  (the documented "multi-scenario-set whack-a-mole").
- **PERCENTAGE = fraction** convention (×100 display, ÷100 input) — the grid dialog inputs
  are fractions on the wire; UI shows %.
- **Cap** prevents an accidental 10×10×10 = 1000-cell book repricing.
- Worktree-isolated execution; a concurrent agent churns shared HEAD (memory).

## User-contribution points (learning mode)

1. `_grid_cell_name(cell)` — scenario naming policy (format, ordering, collisions).
2. Cap / guardrail policy in `generate_grid` (limit value, hard error vs soft warning).
3. Name-collision policy on `POST /sets/generate` (reject 409 vs overwrite).

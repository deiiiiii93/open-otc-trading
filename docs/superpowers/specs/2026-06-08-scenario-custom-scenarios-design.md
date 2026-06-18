# Scenario Test: Detail Dialog + Custom Scenario Management — Design

**Date:** 2026-06-08
**Status:** Approved (design), pending spec review → implementation plan
**Builds on:** the merged Scenario Test feature (`docs/superpowers/specs/2026-06-08-scenario-test-design.md`)

## Problem

The Scenario Test page has two gaps:

1. **No way to see what a scenario does.** Predefined scenario rows show only a name,
   description, and a stress *count* ("2 stresses") — not the actual legs (spot −20%,
   vol +50%). Users can't inspect spot/vol/rate/dividend effects before running.
2. **No place to manage custom scenarios.** The backend fully supports custom scenarios
   (`build_custom`) and saved sets (`save_set`/`load_set`/`list_sets`), and the page has a
   read-only saved-set dropdown — but there is no UI to author, view, edit, or delete
   custom scenarios.

## Goals

- Click any scenario (predefined or custom) → a dialog showing its stress legs across all
  four parameters (spot/vol/rate/dividend), with "unchanged" where no stress applies.
- A **Custom Scenarios** surface supporting full CRUD: create, view, edit (in place),
  delete — using a builder that exposes the full stress model the backend already accepts.
- Custom scenarios become **multi-selectable** for a run alongside predefined scenarios,
  passed inline so a run records exactly what it applied (reproducible).

## Non-Goals (YAGNI)

- Absolute resolved spot/r/q/vol per underlying (baseline → shocked). Deferred; only
  meaningful with a profile/run context and needs per-underlying baseline extraction.
- Position-level stress targeting — stays rejected (`build_custom` already rejects it:
  DB position id ≠ QuantArk-generated UUID).
- Multi-scenario sets — each custom scenario is one named item (stored as a 1-scenario set).
- New agent tools / skills — no orchestrator-routing or catalog-coupling impact.
- DB migration — saved scenarios are YAML files on disk (`scenario_sets_dir`).

## Architecture

Layered exactly like the existing feature:
`routes/ScenarioTest.tsx` + new dialogs → `api/client.ts` → REST `/api/scenario-test/*`
(in `main.py`) → `services/domains/scenario_catalog.py` → QuantArk `stresstest` scenario layer.

**Keystone:** one new serializer, `serialize_scenario(scenario) → {name, description, stresses[]}`,
shared by both gaps (predefined legs in the library; saved-scenario contents for view/edit).

### Param-name mapping (verified against QuantArk)

QuantArk's `Stress.parameter` strings differ from our spec vocabulary. Verified by building
each leg via `ScenarioBuilder`:

| QuantArk `parameter` | our `param` |
|---|---|
| `spot` | `spot` |
| `volatility` | `vol` |
| `rate` | `rate` |
| `dividend_yield` | `dividend` |

`stress_type` and `level` are enums: serialize `.name` (`PERCENTAGE`) for clean round-trip
(`build_custom` uppercases the type and lowercases the level).

## Backend changes

### `backend/app/services/domains/scenario_catalog.py`

Add the QuantArk→spec param map and the serializer:

```python
# Inverse of the param→builder-method routing, for reading scenarios back out.
_PARAM_FROM_QUANTARK = {
    "spot": "spot",
    "volatility": "vol",
    "rate": "rate",
    "dividend_yield": "dividend",
}


def serialize_scenario(scenario: Any) -> dict[str, Any]:
    """Project a QuantArk Scenario to the spec shape used by the UI + build_custom."""
    stresses: list[dict[str, Any]] = []
    for s in scenario.stresses:
        stress_type = getattr(s.stress_type, "name", str(s.stress_type))
        level = getattr(s.level, "value", str(s.level))
        stresses.append({
            "param": _PARAM_FROM_QUANTARK.get(s.parameter, s.parameter),
            "stress_type": stress_type,            # e.g. "PERCENTAGE"
            "value": float(s.stress_value),
            "level": level,                        # e.g. "portfolio"
            "target": s.target,
        })
    return {
        "name": scenario.name,
        "description": getattr(scenario, "description", "") or "",
        "stresses": stresses,
    }
```

`list_predefined()` — add legs to each entry:

```python
    out.append({
        "key": key,
        "name": scenario.name,
        "description": getattr(scenario, "description", ""),
        "num_stresses": len(scenario.stresses),
        "metadata": getattr(scenario, "metadata", {}),
        "stresses": serialize_scenario(scenario)["stresses"],   # NEW
    })
```

Saved-set read + delete (flat = one scenario per file):

```python
def get_set(name: str) -> dict[str, Any]:
    """Return a saved custom scenario's contents (the first/only scenario in the set)."""
    scenarios = load_set(name)  # raises ValueError if missing
    if not scenarios:
        raise ValueError(f"Scenario set is empty: {name}")
    data = serialize_scenario(scenarios[0])
    data["name"] = _safe_name(name)  # the file/item name is the canonical identifier
    return data


def list_sets_detailed() -> list[dict[str, Any]]:
    """Names + serialized contents for the management list (sets are small, 1 scenario each)."""
    out: list[dict[str, Any]] = []
    for stem in list_sets():
        try:
            out.append(get_set(stem))
        except Exception:
            out.append({"name": stem, "description": "", "stresses": []})  # surface, don't crash
    return out


def delete_set(name: str) -> None:
    target = _sets_dir() / f"{_safe_name(name)}.yaml"
    if not target.exists():
        raise ValueError(f"Scenario set not found: {name}")
    target.unlink()
```

### `backend/app/main.py` — routes

- `GET /api/scenario-test/sets` → return **detailed** list (`ScenarioSetDetailOut[]`) via
  `list_sets_detailed()`. (The run picker reads `library.saved_sets` which stays `list[str]`.)
- `GET /api/scenario-test/sets/{name}` → `get_set(name)` → `ScenarioSetDetailOut`;
  `ValueError` → 404.
- `DELETE /api/scenario-test/sets/{name}` → `delete_set(name)`; `ValueError` → 404; 204/JSON ok.
- `POST /api/scenario-test/sets` (existing) — unchanged; saves `custom[]` specs under `name`.
  Overwrite of an existing name = edit.
- `GET /api/scenario-test/library` (existing) — now emits `stresses` per predefined entry
  (data-only; no signature change).

Error mapping mirrors existing routes (`ValueError`→404/400, `KeyError`→422).

### `backend/app/schemas.py`

```python
class ScenarioSetDetailOut(BaseModel):
    name: str
    description: str = ""
    stresses: list[ScenarioStressSpec] = Field(default_factory=list)
```

`ScenarioStressSpec` already exists (`param/stress_type/value/level/target`). `GET /sets` →
`list[ScenarioSetDetailOut]`. The predefined library entry stays `list[dict]` (already loose),
now carrying `stresses`.

## Frontend changes

### `frontend/src/types.ts`

- Extend the predefined scenario type with `stresses: ScenarioStress[]`.
- Add `ScenarioSetDetail = { name: string; description: string; stresses: ScenarioStress[] }`.
- `ScenarioStress` already exists (`param/stress_type/value/level/target`).

### `frontend/src/api/client.ts`

```ts
export const fetchScenarioSets = () =>
  api<ScenarioSetDetail[]>('/api/scenario-test/sets');
export const getScenarioSet = (name: string) =>
  api<ScenarioSetDetail>(`/api/scenario-test/sets/${encodeURIComponent(name)}`);
export const saveScenarioSet = (name: string, custom: ScenarioSpec[]) =>
  api<{ name: string; path: string }>('/api/scenario-test/sets', {
    method: 'POST', body: JSON.stringify({ name, custom }),
  });
export const deleteScenarioSet = (name: string) =>
  api<void>(`/api/scenario-test/sets/${encodeURIComponent(name)}`, { method: 'DELETE' });
```

### `frontend/src/components/ScenarioDetailDialog.tsx` (+ `.css`, `.test.tsx`)

Reuses `Modal`. Props: `{ name, description, stresses, onClose }`. Renders a fixed
four-row table (Spot/Vol/Rate/Dividend); for each, find the matching leg → show
`stress_type · value · scope`, else "— unchanged". `scope` = `portfolio` or
`underlying (TARGET)`. Percentage values render with a `%` suffix; absolute/value raw.

```tsx
const PARAM_ROWS = [
  { key: 'spot', label: 'Spot' },
  { key: 'vol', label: 'Vol' },
  { key: 'rate', label: 'Rate' },
  { key: 'dividend', label: 'Dividend' },
] as const;

function fmtStress(s: ScenarioStress): string {
  const v = s.stress_type === 'PERCENTAGE' ? `${s.value > 0 ? '+' : ''}${s.value}%` : `${s.value}`;
  return v;
}
```

### `frontend/src/components/ScenarioBuilderDialog.tsx` (+ `.css`, `.test.tsx`)

Reuses `Modal`. Props: `{ initial?: ScenarioSetDetail; existingNames: string[]; onSaved; onClose }`.
State: `name`, `description`, `stresses: ScenarioStress[]` (default one row). Each row: param
select (spot/vol/rate/dividend), stress_type select (PERCENTAGE/ABSOLUTE/VALUE), value number,
level select (portfolio/underlying), target text (shown + required when level=underlying).
"＋ add leg" / remove-row controls. Client-side validation mirrors `build_custom`:

- name non-empty (after trim); on **create**, reject if `name` collides with `existingNames`
  (edit keeps its own name).
- ≥1 stress leg; each value finite; underlying level requires non-empty target.

Save → `saveScenarioSet(name, [{ name, description, stresses }])`. Edit preloads `initial`.
Server remains authoritative (validation errors surface inline).

### `frontend/src/routes/ScenarioTest.tsx`

- Load `fetchScenarioSets()` alongside library/portfolios/profiles; keep in `customSets` state.
- **Predefined Scenarios** items: add a "Details" affordance (click name/row opens
  `ScenarioDetailDialog` with `s.stresses`). Checkbox selection unchanged.
- New **Custom Scenarios** section: checkable list mirroring predefined; per item:
  view (details dialog), edit (builder preloaded), delete (confirm → `deleteScenarioSet` →
  refetch). A "＋ New custom scenario" button opens the builder empty.
- **Run wiring:** build the request as
  `predefined: [...selectedPredefinedKeys]` **and**
  `custom: selectedCustomNames.map(n => customSets.find(s => s.name===n))` →
  `{ name, description, stresses }`. Remove the single `scenario_set` dropdown (backend
  support retained for the agent). At least one scenario (predefined or custom) required to run.
- After create/edit/delete, refetch `customSets`; deleting a selected custom scenario clears it
  from the selection.

## Data flow

- **Detail (predefined):** library response already carries `stresses` → dialog (no extra call).
- **Detail (custom):** list response carries full contents → dialog (no extra call); `getScenarioSet`
  available for direct deep-links/edit refresh.
- **Create/Edit:** builder → `POST /sets` (overwrite = edit) → refetch list.
- **Delete:** confirm → `DELETE /sets/{name}` → refetch list.
- **Run:** selected predefined keys + inline custom specs → `POST /runs`; run persists the
  exact specs in `scenario_spec` (reproducible).

## Error handling

- Backend: missing/empty set → `ValueError` → 404; bad custom spec → `build_custom` `ValueError`
  → 400; path traversal blocked by `_safe_name`.
- Frontend: builder shows inline validation + server error text; delete behind a confirm;
  list/detail failures degrade to empty + a visible error, never a blank crash.

## Testing

**Backend** (`tests/test_scenario_catalog.py`, `tests/test_scenario_api.py`)
- `serialize_scenario(market_crash())` → legs `[{param:'spot',stress_type:'PERCENTAGE',value:-0.2,...},
  {param:'vol',...,value:0.5,...}]` (uses real non-default values, not fallbacks).
- `list_predefined()` entries include `stresses`; counts match `num_stresses`.
- `get_set` after `save_set` round-trips the spec; `serialize_scenario` inverts `build_custom`
  for all four params + all three stress types + underlying level w/ target.
- `delete_set` removes the file; deleting a missing set raises `ValueError`.
- Routes: `GET /sets` detailed; `GET /sets/{name}` 200 + 404; `DELETE /sets/{name}` ok + 404;
  `POST` then `GET` then `DELETE` round-trip; `_safe_name` traversal guard (`../x`).

**Frontend** (`*.test.tsx`)
- `ScenarioDetailDialog`: market_crash legs render; rate/dividend show "unchanged"; underlying
  scope shows target.
- `ScenarioBuilderDialog`: add/remove legs; target required when underlying; duplicate name on
  create rejected; save posts the right payload; edit preloads `initial`.
- `ScenarioTest`: custom list create→edit→delete cycle; run payload includes `custom` specs +
  predefined keys; deleting a selected custom clears it.

## File structure

```
backend/app/services/domains/scenario_catalog.py   # serialize_scenario, get_set, list_sets_detailed, delete_set, list_predefined+stresses
backend/app/main.py                                # GET /sets (detailed), GET/DELETE /sets/{name}
backend/app/schemas.py                             # ScenarioSetDetailOut
frontend/src/types.ts                              # stresses on predefined, ScenarioSetDetail
frontend/src/api/client.ts                         # fetchScenarioSets/getScenarioSet/saveScenarioSet/deleteScenarioSet
frontend/src/components/ScenarioDetailDialog.{tsx,css,test.tsx}
frontend/src/components/ScenarioBuilderDialog.{tsx,css,test.tsx}
frontend/src/routes/ScenarioTest.tsx               # Custom Scenarios section, clickable predefined, run wiring
tests/test_scenario_catalog.py, tests/test_scenario_api.py
```

## Risks / notes

- A concurrent session has an **uncommitted edit to `frontend/src/routes/ScenarioTest.css`** in the
  main checkout; this feature also touches that file region. Resolve at merge time.
- `list_sets_detailed` parses every set's YAML per call — fine for a handful of custom scenarios;
  revisit if sets grow large.
- The flat model stores a custom scenario as a 1-scenario QuantArk set; `get_set` reads
  `scenarios[0]`. A legacy multi-scenario file would show only its first scenario (acceptable;
  none exist today).

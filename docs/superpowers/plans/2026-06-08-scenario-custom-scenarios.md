# Scenario Test: Detail Dialog + Custom Scenario CRUD — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users inspect any scenario's stress legs in a dialog, and fully manage (create/view/edit/delete) custom scenarios on the Scenario Test page.

**Architecture:** A shared backend serializer (`serialize_scenario`) exposes stress legs for predefined scenarios (in the library response) and saved custom scenarios (new set-read/delete routes). The frontend adds two `Modal`-based dialogs (detail viewer + builder) and a Custom Scenarios section; custom scenarios are multi-selectable and passed inline as `custom: [specs]` so runs stay reproducible. No DB migration, no new agent tools.

**Tech Stack:** FastAPI + SQLAlchemy + QuantArk `stresstest` (backend); React + TypeScript + Radix `Modal` + vitest/testing-library (frontend). Spec: `docs/superpowers/specs/2026-06-08-scenario-custom-scenarios-design.md`.

---

## One-time setup (worktree)

The frontend has no `node_modules` in a fresh worktree. Before running vitest:

```bash
[ -e frontend/node_modules ] || ln -s /Users/fuxinyao/open-otc-trading/frontend/node_modules frontend/node_modules
```

Backend tests run from the worktree root (`pyproject.toml` sets `pythonpath=["backend"]`).
Run `python3 -m pytest` (NOT `python -c`; the `.pth` resolves `app` to the main checkout).

## File structure

| File | Responsibility | Action |
|---|---|---|
| `backend/app/services/domains/scenario_catalog.py` | serializer, set read/delete, predefined legs | Modify |
| `backend/app/schemas.py` | `ScenarioSetDetailOut` | Modify |
| `backend/app/main.py` | GET /sets (detailed), GET/DELETE /sets/{name} | Modify |
| `tests/test_scenario_catalog.py` | catalog unit tests | Modify |
| `tests/test_scenario_test_api.py` | route tests | Modify |
| `frontend/src/types.ts` | `stresses` on predefined, `ScenarioSetDetail`, tighten run `custom` | Modify |
| `frontend/src/api/client.ts` | sets CRUD wrappers | Modify |
| `frontend/src/components/ScenarioDetailDialog.{tsx,css,test.tsx}` | stress-leg viewer | Create |
| `frontend/src/components/ScenarioBuilderDialog.{tsx,css,test.tsx}` | create/edit builder | Create |
| `frontend/src/routes/ScenarioTest.tsx` | custom section, clickable predefined, run wiring | Modify |
| `frontend/src/routes/ScenarioTest.test.tsx` | page tests | Modify |

---

## Phase 1 — Backend serializer + predefined legs

### Task 1: `serialize_scenario` + reverse param map

**Files:**
- Modify: `backend/app/services/domains/scenario_catalog.py`
- Test: `tests/test_scenario_catalog.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_scenario_catalog.py`:

```python
def test_serialize_scenario_predefined_market_crash():
    from stresstest.scenario.scenario_library import ScenarioLibrary
    data = scenario_catalog.serialize_scenario(ScenarioLibrary.market_crash())
    legs = {s["param"]: s for s in data["stresses"]}
    assert data["name"] == "Market Crash"
    # market_crash = spot -20% + vol +50%, expressed as fractions
    assert legs["spot"]["stress_type"] == "PERCENTAGE"
    assert legs["spot"]["value"] == pytest.approx(-0.2)
    assert legs["vol"]["param"] == "vol"            # volatility -> vol
    assert legs["vol"]["value"] == pytest.approx(0.5)
    assert legs["spot"]["level"] == "portfolio"


def test_serialize_scenario_round_trips_build_custom():
    # serialize(build_custom(spec)) yields a spec build_custom accepts again.
    spec = {
        "name": "RT",
        "description": "round trip",
        "stresses": [
            {"param": "dividend", "stress_type": "ABSOLUTE", "value": 0.01, "level": "portfolio"},
            {"param": "vol", "stress_type": "PERCENTAGE", "value": 0.3,
             "level": "underlying", "target": "000300.SH"},
        ],
    }
    data = scenario_catalog.serialize_scenario(scenario_catalog.build_custom(spec))
    legs = {s["param"]: s for s in data["stresses"]}
    assert legs["dividend"]["param"] == "dividend"   # dividend_yield -> dividend
    assert legs["vol"]["level"] == "underlying"
    assert legs["vol"]["target"] == "000300.SH"
    # rebuild from the serialized form succeeds (no exception)
    rebuilt = scenario_catalog.build_custom({"name": "RT2", "stresses": data["stresses"]})
    assert len(rebuilt.stresses) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_scenario_catalog.py::test_serialize_scenario_predefined_market_crash -q`
Expected: FAIL with `AttributeError: module ... has no attribute 'serialize_scenario'`.

- [ ] **Step 3: Implement** — in `scenario_catalog.py`, add after `_PARAM_TO_METHOD` (around line 16):

```python
# Inverse of the param→builder-method routing, for reading scenarios back out.
_PARAM_FROM_QUANTARK = {
    "spot": "spot",
    "volatility": "vol",
    "rate": "rate",
    "dividend_yield": "dividend",
}
```

…and add this function after `build_custom` (before `resolve_scenarios`):

```python
def serialize_scenario(scenario: Any) -> dict[str, Any]:
    """Project a QuantArk Scenario to the spec shape used by the UI + build_custom.

    QuantArk param names (volatility, dividend_yield) are mapped back to the spec
    vocabulary (vol, dividend); stress_type/level are emitted as their enum
    `.name`/`.value` so they round-trip through build_custom (which upper/lowers).
    """
    stresses: list[dict[str, Any]] = []
    for s in scenario.stresses:
        stress_type = getattr(s.stress_type, "name", str(s.stress_type))
        level = getattr(s.level, "value", str(s.level))
        stresses.append({
            "param": _PARAM_FROM_QUANTARK.get(s.parameter, s.parameter),
            "stress_type": stress_type,
            "value": float(s.stress_value),
            "level": level,
            "target": s.target,
        })
    return {
        "name": scenario.name,
        "description": getattr(scenario, "description", "") or "",
        "stresses": stresses,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scenario_catalog.py -k serialize -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/scenario_catalog.py tests/test_scenario_catalog.py
git commit -m "feat(scenario): serialize_scenario projects QuantArk scenarios to spec shape"
```

### Task 2: `list_predefined` exposes stress legs

**Files:**
- Modify: `backend/app/services/domains/scenario_catalog.py:41-54`
- Test: `tests/test_scenario_catalog.py`

- [ ] **Step 1: Write the failing test**

```python
def test_list_predefined_includes_stress_legs():
    entry = next(s for s in scenario_catalog.list_predefined() if s["key"] == "market_crash")
    assert "stresses" in entry
    assert len(entry["stresses"]) == entry["num_stresses"]
    params = {leg["param"] for leg in entry["stresses"]}
    assert {"spot", "vol"} <= params
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_scenario_catalog.py::test_list_predefined_includes_stress_legs -q`
Expected: FAIL with `KeyError: 'stresses'`.

- [ ] **Step 3: Implement** — in `list_predefined`, add `stresses` to the appended dict:

```python
        out.append({
            "key": key,
            "name": scenario.name,
            "description": getattr(scenario, "description", ""),
            "num_stresses": len(scenario.stresses),
            "metadata": getattr(scenario, "metadata", {}),
            "stresses": serialize_scenario(scenario)["stresses"],
        })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scenario_catalog.py -k "predefined" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/scenario_catalog.py tests/test_scenario_catalog.py
git commit -m "feat(scenario): expose predefined scenario stress legs in the library"
```

---

## Phase 2 — Backend set read/delete + routes

### Task 3: `get_set` + `list_sets_detailed`

**Files:**
- Modify: `backend/app/services/domains/scenario_catalog.py` (after `list_sets`)
- Test: `tests/test_scenario_catalog.py`

- [ ] **Step 1: Write the failing test**

```python
def test_get_set_and_list_sets_detailed(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    s = scenario_catalog.build_custom({
        "name": "Mild",
        "description": "mild selloff",
        "stresses": [{"param": "spot", "stress_type": "PERCENTAGE", "value": -0.1, "level": "portfolio"}],
    })
    scenario_catalog.save_set("mild_selloff", [s])

    detail = scenario_catalog.get_set("mild_selloff")
    assert detail["name"] == "mild_selloff"
    assert detail["stresses"][0]["param"] == "spot"
    assert detail["stresses"][0]["value"] == pytest.approx(-0.1)

    listed = scenario_catalog.list_sets_detailed()
    assert any(d["name"] == "mild_selloff" and d["stresses"] for d in listed)


def test_get_set_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    with pytest.raises(ValueError, match="not found"):
        scenario_catalog.get_set("nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_scenario_catalog.py -k "get_set or list_sets_detailed" -q`
Expected: FAIL with `AttributeError: ... 'get_set'`.

- [ ] **Step 3: Implement** — add after `list_sets`:

```python
def get_set(name: str) -> dict[str, Any]:
    """Return a saved custom scenario's contents (the first/only scenario in the set)."""
    scenarios = load_set(name)  # raises ValueError if the file is missing
    if not scenarios:
        raise ValueError(f"Scenario set is empty: {name}")
    data = serialize_scenario(scenarios[0])
    data["name"] = _safe_name(name)  # the file/item name is the canonical identifier
    return data


def list_sets_detailed() -> list[dict[str, Any]]:
    """Names + serialized contents for the management list (sets are 1 scenario each)."""
    out: list[dict[str, Any]] = []
    for stem in list_sets():
        try:
            out.append(get_set(stem))
        except Exception:
            out.append({"name": stem, "description": "", "stresses": []})
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scenario_catalog.py -k "get_set or list_sets_detailed" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/scenario_catalog.py tests/test_scenario_catalog.py
git commit -m "feat(scenario): read saved scenario contents (get_set/list_sets_detailed)"
```

### Task 4: `delete_set`

**Files:**
- Modify: `backend/app/services/domains/scenario_catalog.py` (after `get_set`)
- Test: `tests/test_scenario_catalog.py`

- [ ] **Step 1: Write the failing test**

```python
def test_delete_set_removes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    s = scenario_catalog.build_custom({"name": "D", "stresses": [{"param": "spot", "value": -0.1}]})
    scenario_catalog.save_set("to_delete", [s])
    assert "to_delete" in scenario_catalog.list_sets()
    scenario_catalog.delete_set("to_delete")
    assert "to_delete" not in scenario_catalog.list_sets()


def test_delete_set_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    with pytest.raises(ValueError, match="not found"):
        scenario_catalog.delete_set("ghost")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_scenario_catalog.py -k "delete_set" -q`
Expected: FAIL with `AttributeError: ... 'delete_set'`.

- [ ] **Step 3: Implement** — add after `get_set`:

```python
def delete_set(name: str) -> None:
    target = _sets_dir() / f"{_safe_name(name)}.yaml"
    if not target.exists():
        raise ValueError(f"Scenario set not found: {name}")
    target.unlink()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scenario_catalog.py -k "delete_set" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/scenario_catalog.py tests/test_scenario_catalog.py
git commit -m "feat(scenario): delete_set removes a saved custom scenario"
```

### Task 5: Schema + REST routes (GET detailed /sets, GET/DELETE /sets/{name})

**Files:**
- Modify: `backend/app/schemas.py` (after `ScenarioSetSavedOut`, ~line 1417)
- Modify: `backend/app/main.py` (the `scenario_test_list_sets` route ~3213, add two routes)
- Test: `tests/test_scenario_test_api.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_scenario_test_api.py` (mirror its existing TestClient + tmp `scenario_sets_dir` setup; if the file pins `scenario_sets_dir`, reuse that fixture):

```python
def test_sets_crud_roundtrip(client):
    spec = {
        "name": "API Custom",
        "custom": [{
            "name": "API Custom",
            "description": "api made",
            "stresses": [{"param": "spot", "stress_type": "PERCENTAGE", "value": -0.12, "level": "portfolio"}],
        }],
    }
    # create
    saved = client.post("/api/scenario-test/sets", json=spec)
    assert saved.status_code == 200

    # list detailed
    listed = client.get("/api/scenario-test/sets")
    assert listed.status_code == 200
    names = {d["name"] for d in listed.json()}
    assert "API_Custom" in names  # _safe_name sanitizes spaces -> underscores

    # get one
    one = client.get("/api/scenario-test/sets/API_Custom")
    assert one.status_code == 200
    assert one.json()["stresses"][0]["param"] == "spot"

    # delete
    deleted = client.delete("/api/scenario-test/sets/API_Custom")
    assert deleted.status_code in (200, 204)
    assert client.get("/api/scenario-test/sets/API_Custom").status_code == 404


def test_get_missing_set_404(client):
    assert client.get("/api/scenario-test/sets/does_not_exist").status_code == 404


def test_delete_traversal_guard(client):
    # _safe_name sanitizes path separators; a traversal name resolves to a safe stem
    # that does not exist -> 404, never escaping the sets dir.
    assert client.delete("/api/scenario-test/sets/..%2f..%2fetc%2fpasswd").status_code == 404
```

> If `test_scenario_test_api.py` lacks a `client` fixture pinning a tmp `scenario_sets_dir`,
> add one mirroring the existing setup there (it already configures a temp DB + Settings;
> set `scenario_sets_dir` to a `tmp_path` subdir in the same Settings).

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_scenario_test_api.py -k "sets_crud or missing_set or traversal" -q`
Expected: FAIL (routes return 405/wrong shape, or 200-list lacks detail).

- [ ] **Step 3a: Implement schema** — in `schemas.py` after `ScenarioSetSavedOut`:

```python
class ScenarioSetDetailOut(BaseModel):
    name: str
    description: str = ""
    stresses: list[ScenarioStressSpec] = Field(default_factory=list)
```

- [ ] **Step 3b: Implement routes** — in `main.py`, replace the `scenario_test_list_sets` route and add two routes:

```python
    @app.get("/api/scenario-test/sets", response_model=list[ScenarioSetDetailOut])
    def scenario_test_list_sets():
        from .services.domains import scenario_catalog

        return [ScenarioSetDetailOut(**d) for d in scenario_catalog.list_sets_detailed()]

    @app.get("/api/scenario-test/sets/{name}", response_model=ScenarioSetDetailOut)
    def scenario_test_get_set(name: str):
        from .services.domains import scenario_catalog

        try:
            return ScenarioSetDetailOut(**scenario_catalog.get_set(name))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/scenario-test/sets/{name}")
    def scenario_test_delete_set(name: str):
        from .services.domains import scenario_catalog

        try:
            scenario_catalog.delete_set(name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, "name": name}
```

Also add `ScenarioSetDetailOut` to the schema imports at the top of `main.py` (the block that
already imports `ScenarioSetsOut`, `ScenarioSetSavedOut`, etc.). `ScenarioSetsOut` may now be
unused — leave it (still referenced by tests/imports) or remove if no references remain.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scenario_test_api.py -k "sets" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas.py backend/app/main.py tests/test_scenario_test_api.py
git commit -m "feat(scenario): REST for set detail + delete (GET list/one, DELETE)"
```

---

## Phase 3 — Frontend types + API client

### Task 6: types + api wrappers

**Files:**
- Modify: `frontend/src/types.ts:1082-1085` (predefined entry) + add `ScenarioSetDetail`; tighten `custom`
- Modify: `frontend/src/api/client.ts` (imports + scenario section ~109-121)

- [ ] **Step 1: Implement types** — in `types.ts`:

Replace the `ScenarioLibrary` predefined entry type and add `ScenarioSetDetail`:

```ts
export type PredefinedScenario = {
  key: string;
  name: string;
  description: string;
  num_stresses: number;
  stresses: ScenarioStress[];
  metadata?: Record<string, unknown>;
};

export type ScenarioLibrary = {
  predefined: PredefinedScenario[];
  saved_sets: string[];
};

export type ScenarioSetDetail = {
  name: string;
  description: string;
  stresses: ScenarioStress[];
};
```

Tighten the run request `custom` field:

```ts
  custom?: ScenarioSpec[] | null;
```

- [ ] **Step 2: Implement api wrappers** — in `client.ts`, add `ScenarioSetDetail`, `ScenarioSpec`
to the type imports, and append to the scenario section:

```ts
export const fetchScenarioSets = () =>
  api<ScenarioSetDetail[]>('/api/scenario-test/sets');

export const getScenarioSet = (name: string) =>
  api<ScenarioSetDetail>(`/api/scenario-test/sets/${encodeURIComponent(name)}`);

export const saveScenarioSet = (name: string, custom: ScenarioSpec[]) =>
  api<{ name: string; path: string }>('/api/scenario-test/sets', {
    method: 'POST',
    body: JSON.stringify({ name, custom }),
  });

export const deleteScenarioSet = (name: string) =>
  api<{ ok: boolean; name: string }>(`/api/scenario-test/sets/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  });
```

- [ ] **Step 3: Verify types compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors (existing `ScenarioTest.tsx` reads `library.predefined` — adding fields is compatible; `ScenarioStress` already exists).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/client.ts
git commit -m "feat(scenario): frontend types + api client for scenario sets"
```

---

## Phase 4 — ScenarioDetailDialog

### Task 7: detail dialog component

**Files:**
- Create: `frontend/src/components/ScenarioDetailDialog.tsx`, `.css`, `.test.tsx`

- [ ] **Step 1: Write the failing test** — `ScenarioDetailDialog.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { test, expect } from 'vitest';
import { ScenarioDetailDialog } from './ScenarioDetailDialog';
import type { ScenarioStress } from '../types';

const stresses: ScenarioStress[] = [
  { param: 'spot', stress_type: 'PERCENTAGE', value: -20, level: 'portfolio', target: null },
  { param: 'vol', stress_type: 'PERCENTAGE', value: 50, level: 'underlying', target: '000300.SH' },
];

test('does not render when closed', () => {
  render(<ScenarioDetailDialog open={false} name="Market Crash" stresses={stresses} onClose={() => {}} />);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('shows all four params; unchanged where no leg', () => {
  render(<ScenarioDetailDialog open name="Market Crash" description="d" stresses={stresses} onClose={() => {}} />);
  expect(screen.getByRole('dialog', { name: /market crash/i })).toBeInTheDocument();
  expect(screen.getByText('Spot')).toBeInTheDocument();
  expect(screen.getByText('Rate')).toBeInTheDocument();
  // rate + dividend have no legs -> "unchanged" appears (twice)
  expect(screen.getAllByText(/unchanged/i)).toHaveLength(2);
  // underlying scope shows the target symbol
  expect(screen.getByText(/000300\.SH/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/ScenarioDetailDialog.test.tsx`
Expected: FAIL — cannot resolve `./ScenarioDetailDialog`.

- [ ] **Step 3: Implement** — `ScenarioDetailDialog.tsx`:

```tsx
import { Modal } from './Modal';
import type { ScenarioStress } from '../types';
import './ScenarioDetailDialog.css';

type Props = {
  open: boolean;
  name: string;
  description?: string;
  stresses: ScenarioStress[];
  onClose: () => void;
};

const PARAM_ROWS: Array<{ key: ScenarioStress['param']; label: string }> = [
  { key: 'spot', label: 'Spot' },
  { key: 'vol', label: 'Vol' },
  { key: 'rate', label: 'Rate' },
  { key: 'dividend', label: 'Dividend' },
];

function fmtValue(s: ScenarioStress): string {
  if (s.stress_type === 'PERCENTAGE') {
    const sign = s.value > 0 ? '+' : '';
    return `${sign}${s.value}%`;
  }
  return `${s.value}`;
}

function fmtScope(s: ScenarioStress): string {
  if (s.level === 'underlying') return `underlying (${s.target ?? '?'})`;
  return s.level;
}

export function ScenarioDetailDialog({ open, name, description, stresses, onClose }: Props) {
  return (
    <Modal
      open={open}
      onOpenChange={(o) => { if (!o) onClose(); }}
      title={name}
      layoutKey="scenario-detail"
      defaultWidth={520}
      defaultHeight={320}
    >
      <div className="wl-scenario-detail">
        {description && <p className="wl-scenario-detail__desc">{description}</p>}
        <table className="wl-scenario-detail__table">
          <thead>
            <tr><th>Param</th><th>Stress</th><th>Value</th><th>Scope</th></tr>
          </thead>
          <tbody>
            {PARAM_ROWS.map(({ key, label }) => {
              const leg = stresses.find((s) => s.param === key);
              return (
                <tr key={key}>
                  <td>{label}</td>
                  <td>{leg ? leg.stress_type.toLowerCase() : '—'}</td>
                  <td>{leg ? fmtValue(leg) : 'unchanged'}</td>
                  <td>{leg ? fmtScope(leg) : '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Modal>
  );
}
```

`ScenarioDetailDialog.css` (minimal, match existing dialog styles):

```css
.wl-scenario-detail__desc { color: var(--wl-muted, #8a8f98); margin: 0 0 12px; }
.wl-scenario-detail__table { width: 100%; border-collapse: collapse; font-size: 13px; }
.wl-scenario-detail__table th,
.wl-scenario-detail__table td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--wl-border, #2a2d34); }
.wl-scenario-detail__table th { color: var(--wl-muted, #8a8f98); font-weight: 600; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/ScenarioDetailDialog.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ScenarioDetailDialog.tsx frontend/src/components/ScenarioDetailDialog.css frontend/src/components/ScenarioDetailDialog.test.tsx
git commit -m "feat(scenario): ScenarioDetailDialog shows stress legs across 4 params"
```

---

## Phase 5 — ScenarioBuilderDialog

### Task 8: builder dialog component

**Files:**
- Create: `frontend/src/components/ScenarioBuilderDialog.tsx`, `.css`, `.test.tsx`

- [ ] **Step 1: Write the failing test** — `ScenarioBuilderDialog.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { test, expect, vi } from 'vitest';
import { ScenarioBuilderDialog } from './ScenarioBuilderDialog';

test('rejects a duplicate name on create', async () => {
  const onSave = vi.fn();
  render(<ScenarioBuilderDialog open existingNames={['Taken']} onSave={onSave} onClose={() => {}} />);
  await userEvent.type(screen.getByLabelText(/scenario name/i), 'Taken');
  await userEvent.type(screen.getByLabelText(/^value 0$/i), '-10');
  await userEvent.click(screen.getByRole('button', { name: /^save$/i }));
  expect(onSave).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(/already exists/i);
});

test('requires a target when level is underlying', async () => {
  const onSave = vi.fn();
  render(<ScenarioBuilderDialog open existingNames={[]} onSave={onSave} onClose={() => {}} />);
  await userEvent.type(screen.getByLabelText(/scenario name/i), 'U');
  await userEvent.type(screen.getByLabelText(/^value 0$/i), '5');
  await userEvent.selectOptions(screen.getByLabelText(/^level 0$/i), 'underlying');
  await userEvent.click(screen.getByRole('button', { name: /^save$/i }));
  expect(onSave).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(/target symbol/i);
});

test('saves a valid scenario with cleaned legs', async () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(<ScenarioBuilderDialog open existingNames={[]} onSave={onSave} onClose={() => {}} />);
  await userEvent.type(screen.getByLabelText(/scenario name/i), '  Mild  ');
  await userEvent.type(screen.getByLabelText(/^value 0$/i), '-10');
  await userEvent.click(screen.getByRole('button', { name: /^save$/i }));
  expect(onSave).toHaveBeenCalledWith(
    'Mild',
    '',
    [{ param: 'spot', stress_type: 'PERCENTAGE', value: -10, level: 'portfolio', target: null }],
  );
});

test('edit mode preloads initial and locks the name', () => {
  render(
    <ScenarioBuilderDialog
      open
      initial={{ name: 'Existing', description: 'd',
        stresses: [{ param: 'vol', stress_type: 'PERCENTAGE', value: 30, level: 'portfolio', target: null }] }}
      existingNames={['Existing']}
      onSave={() => {}}
      onClose={() => {}}
    />,
  );
  const nameInput = screen.getByLabelText(/scenario name/i) as HTMLInputElement;
  expect(nameInput.value).toBe('Existing');
  expect(nameInput).toBeDisabled();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/ScenarioBuilderDialog.test.tsx`
Expected: FAIL — cannot resolve `./ScenarioBuilderDialog`.

- [ ] **Step 3: Implement** — `ScenarioBuilderDialog.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { Button } from './Button';
import { Modal } from './Modal';
import type { ScenarioStress, ScenarioSetDetail } from '../types';
import './ScenarioBuilderDialog.css';

type Props = {
  open: boolean;
  initial?: ScenarioSetDetail | null;   // present => edit mode (name locked)
  existingNames: string[];
  onSave: (name: string, description: string, stresses: ScenarioStress[]) => Promise<void> | void;
  onClose: () => void;
};

const PARAMS: ScenarioStress['param'][] = ['spot', 'vol', 'rate', 'dividend'];
const STRESS_TYPES: ScenarioStress['stress_type'][] = ['PERCENTAGE', 'ABSOLUTE', 'VALUE'];
const LEVELS: Array<'portfolio' | 'underlying'> = ['portfolio', 'underlying'];

function emptyLeg(): ScenarioStress {
  return { param: 'spot', stress_type: 'PERCENTAGE', value: 0, level: 'portfolio', target: null };
}

export function ScenarioBuilderDialog({ open, initial, existingNames, onSave, onClose }: Props) {
  const isEdit = initial != null;
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [legs, setLegs] = useState<ScenarioStress[]>([emptyLeg()]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setSaving(false);
    setName(initial?.name ?? '');
    setDescription(initial?.description ?? '');
    setLegs(initial && initial.stresses.length > 0 ? initial.stresses.map((s) => ({ ...s })) : [emptyLeg()]);
  }, [open, initial]);

  const updateLeg = (i: number, patch: Partial<ScenarioStress>) =>
    setLegs((prev) => prev.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));
  const addLeg = () => setLegs((prev) => [...prev, emptyLeg()]);
  const removeLeg = (i: number) => setLegs((prev) => prev.filter((_, idx) => idx !== i));

  function validate(): string | null {
    const trimmed = name.trim();
    if (!trimmed) return 'Name is required.';
    if (!isEdit && existingNames.includes(trimmed)) return `A scenario named "${trimmed}" already exists.`;
    if (legs.length === 0) return 'Add at least one stress leg.';
    for (const l of legs) {
      if (!Number.isFinite(l.value)) return 'Each stress needs a numeric value.';
      if (l.level === 'underlying' && !String(l.target ?? '').trim()) {
        return 'Underlying-level stress needs a target symbol.';
      }
    }
    return null;
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const v = validate();
    if (v) { setError(v); return; }
    setSaving(true);
    setError(null);
    try {
      const cleaned = legs.map((l) => ({
        ...l,
        target: l.level === 'underlying' ? String(l.target ?? '').trim() : null,
      }));
      await onSave(name.trim(), description.trim(), cleaned);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onOpenChange={(o) => { if (!o) onClose(); }}
      title={isEdit ? `Edit ${initial?.name}` : 'New custom scenario'}
      layoutKey="scenario-builder"
      defaultWidth={640}
      defaultHeight={520}
    >
      <form className="wl-scenario-builder" onSubmit={submit}>
        <label className="wl-scenario-builder__field">
          <span>Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} disabled={isEdit} autoFocus aria-label="Scenario name" />
        </label>
        <label className="wl-scenario-builder__field">
          <span>Description</span>
          <input value={description} onChange={(e) => setDescription(e.target.value)} aria-label="Scenario description" />
        </label>

        <div className="wl-scenario-builder__legs">
          <div className="wl-scenario-builder__legs-head">
            <span>Stress legs</span>
            <Button type="button" variant="ghost" onClick={addLeg}>+ add leg</Button>
          </div>
          {legs.map((leg, i) => (
            <div className="wl-scenario-builder__leg" key={i}>
              <select aria-label={`param ${i}`} value={leg.param}
                onChange={(e) => updateLeg(i, { param: e.target.value as ScenarioStress['param'] })}>
                {PARAMS.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
              <select aria-label={`type ${i}`} value={leg.stress_type}
                onChange={(e) => updateLeg(i, { stress_type: e.target.value as ScenarioStress['stress_type'] })}>
                {STRESS_TYPES.map((t) => <option key={t} value={t}>{t.toLowerCase()}</option>)}
              </select>
              <input aria-label={`value ${i}`} type="number" step="any" value={Number.isNaN(leg.value) ? '' : leg.value}
                onChange={(e) => updateLeg(i, { value: e.target.value === '' ? NaN : Number(e.target.value) })} />
              <select aria-label={`level ${i}`} value={leg.level}
                onChange={(e) => updateLeg(i, { level: e.target.value as 'portfolio' | 'underlying' })}>
                {LEVELS.map((lv) => <option key={lv} value={lv}>{lv}</option>)}
              </select>
              {leg.level === 'underlying' && (
                <input aria-label={`target ${i}`} placeholder="symbol" value={String(leg.target ?? '')}
                  onChange={(e) => updateLeg(i, { target: e.target.value })} />
              )}
              <button type="button" className="wl-scenario-builder__remove" aria-label={`remove leg ${i}`}
                onClick={() => removeLeg(i)} disabled={legs.length === 1}>×</button>
            </div>
          ))}
        </div>

        {error && <p className="wl-scenario-builder__error" role="alert">{error}</p>}
        <div className="wl-scenario-builder__actions">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
        </div>
      </form>
    </Modal>
  );
}
```

`ScenarioBuilderDialog.css`:

```css
.wl-scenario-builder { display: flex; flex-direction: column; gap: 12px; }
.wl-scenario-builder__field { display: flex; flex-direction: column; gap: 4px; }
.wl-scenario-builder__field span { color: var(--wl-muted, #8a8f98); font-size: 12px; }
.wl-scenario-builder__legs-head { display: flex; justify-content: space-between; align-items: center; }
.wl-scenario-builder__leg { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
.wl-scenario-builder__leg select,
.wl-scenario-builder__leg input { padding: 4px 6px; }
.wl-scenario-builder__remove { background: none; border: none; color: var(--wl-muted, #8a8f98); cursor: pointer; font-size: 16px; }
.wl-scenario-builder__error { color: var(--wl-danger, #e5484d); font-size: 13px; }
.wl-scenario-builder__actions { display: flex; justify-content: flex-end; gap: 8px; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/ScenarioBuilderDialog.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ScenarioBuilderDialog.tsx frontend/src/components/ScenarioBuilderDialog.css frontend/src/components/ScenarioBuilderDialog.test.tsx
git commit -m "feat(scenario): ScenarioBuilderDialog for create/edit custom scenarios"
```

---

## Phase 6 — Page integration

### Task 9: load custom sets + clickable predefined → detail dialog

**Files:**
- Modify: `frontend/src/routes/ScenarioTest.tsx`
- Modify: `frontend/src/routes/ScenarioTest.test.tsx`

- [ ] **Step 1: Write the failing test** — add to `ScenarioTest.test.tsx` (the existing `library`
const needs `stresses` on its predefined entries; update both entries, e.g. market_crash gets
`stresses: [{param:'spot',stress_type:'PERCENTAGE',value:-20,level:'portfolio',target:null}]`).
Add a new test:

```tsx
it('opens a detail dialog when a predefined scenario name is clicked', async () => {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = requestUrl(input);
    if (url === '/api/scenario-test/library') return response(library);
    if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
    if (url === '/api/portfolios') return response(portfolios);
    if (url === '/api/scenario-test/sets') return response([]);
    if (url.startsWith('/api/scenario-test/runs')) return response([]);
    return response({});
  }) as unknown as typeof fetch;

  render(<ScenarioTestLive />);
  await waitFor(() => expect(screen.getByText('Market Crash')).toBeInTheDocument());
  await userEvent.click(screen.getByRole('button', { name: /details for market crash/i }));
  expect(await screen.findByRole('dialog', { name: /market crash/i })).toBeInTheDocument();
  expect(screen.getByText('Spot')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/routes/ScenarioTest.test.tsx -t "detail dialog"`
Expected: FAIL — no "details for…" button / no dialog.

- [ ] **Step 3: Implement** — in `ScenarioTest.tsx`:

(a) Imports:

```tsx
import { api, fetchScenarioLibrary, createScenarioTestRun, listScenarioTestRuns,
  fetchScenarioSets, saveScenarioSet, deleteScenarioSet } from '../api/client';
import type { PricingParameterProfile, Portfolio, ScenarioLibrary, ScenarioSetDetail,
  ScenarioStress, ScenarioTestRun, ScenarioTestRunRequest } from '../types';
import { ScenarioDetailDialog } from '../components/ScenarioDetailDialog';
import { ScenarioBuilderDialog } from '../components/ScenarioBuilderDialog';
```

(b) State (after existing selections):

```tsx
  const [customSets, setCustomSets] = useState<ScenarioSetDetail[]>([]);
  const [selectedCustomNames, setSelectedCustomNames] = useState<Set<string>>(new Set());
  const [detail, setDetail] = useState<{ name: string; description: string; stresses: ScenarioStress[] } | null>(null);
  const [builder, setBuilder] = useState<{ initial: ScenarioSetDetail | null } | null>(null);
```

(c) Load custom sets in the initial `Promise.allSettled` — add `fetchScenarioSets()` as a 4th
promise and handle its result:

```tsx
    Promise.allSettled([
      fetchScenarioLibrary(),
      api<Portfolio[]>('/api/portfolios'),
      api<PricingParameterProfile[]>('/api/pricing-parameter-profiles'),
      fetchScenarioSets(),
    ]).then(([libResult, portResult, profileResult, setsResult]) => {
      if (cancelled.current) return;
      // ...existing lib/port/profile handling unchanged...
      if (setsResult.status === 'fulfilled') {
        setCustomSets(Array.isArray(setsResult.value) ? setsResult.value : []);
      }
    })
```

(d) A refetch helper (used by create/edit/delete):

```tsx
  const reloadCustomSets = async () => {
    try { setCustomSets(await fetchScenarioSets()); } catch { /* keep prior */ }
  };
```

(e) Make each predefined scenario name a details button (inside the existing `<label>`,
replace the `<span className="wl-scenario-test__scenario-name">{s.name}</span>` with):

```tsx
                      <button
                        type="button"
                        className="wl-scenario-test__scenario-name-btn"
                        aria-label={`Details for ${s.name}`}
                        onClick={(e) => { e.preventDefault();
                          setDetail({ name: s.name, description: s.description, stresses: s.stresses }); }}
                      >
                        {s.name}
                      </button>
```

(f) Render the detail dialog once near the end of the returned JSX (before `</>`):

```tsx
      {detail && (
        <ScenarioDetailDialog
          open
          name={detail.name}
          description={detail.description}
          stresses={detail.stresses}
          onClose={() => setDetail(null)}
        />
      )}
```

> `e.preventDefault()` stops the surrounding `<label>` from also toggling the checkbox when the
> name is clicked.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/routes/ScenarioTest.test.tsx`
Expected: PASS (existing tests still green + the new one).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/ScenarioTest.tsx frontend/src/routes/ScenarioTest.test.tsx
git commit -m "feat(scenario): clickable predefined scenarios open detail dialog"
```

### Task 10: Custom Scenarios section + builder wiring + run wiring

**Files:**
- Modify: `frontend/src/routes/ScenarioTest.tsx`
- Modify: `frontend/src/routes/ScenarioTest.test.tsx`

- [ ] **Step 1: Write the failing test** — add to `ScenarioTest.test.tsx`:

```tsx
it('creates a custom scenario and includes it in a run', async () => {
  const customAfter: ScenarioSetDetail[] = [
    { name: 'My_Shock', description: '', stresses: [
      { param: 'spot', stress_type: 'PERCENTAGE', value: -10, level: 'portfolio', target: null }] },
  ];
  let setsCalls = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(input);
    if (url === '/api/scenario-test/library') return response(library);
    if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
    if (url === '/api/portfolios') return response(portfolios);
    if (url === '/api/scenario-test/sets' && init?.method === 'POST') return response({ name: 'My_Shock', path: '/x.yaml' });
    if (url === '/api/scenario-test/sets') { setsCalls += 1; return response(setsCalls === 1 ? [] : customAfter); }
    if (url === '/api/scenario-test/runs' && init?.method === 'POST') return response(completedRun);
    if (url.startsWith('/api/scenario-test/runs')) return response([completedRun]);
    return response({});
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  render(<ScenarioTestLive />);
  await waitFor(() => expect(screen.getByText('Market Crash')).toBeInTheDocument());

  // open builder, fill, save
  await userEvent.click(screen.getByRole('button', { name: /new custom scenario/i }));
  await userEvent.type(screen.getByLabelText(/scenario name/i), 'My Shock');
  await userEvent.type(screen.getByLabelText(/^value 0$/i), '-10');
  await userEvent.click(screen.getByRole('button', { name: /^save$/i }));

  // appears in the custom list after refetch
  await waitFor(() => expect(screen.getByText('My_Shock')).toBeInTheDocument());

  // select it + run; POST body carries the custom spec
  await userEvent.click(screen.getByRole('checkbox', { name: /my_shock/i }));
  await userEvent.click(screen.getByRole('button', { name: /run scenario test/i }));

  await waitFor(() => {
    const posted = fetchMock.mock.calls.find(([u, i]) =>
      requestUrl(u as RequestInfo | URL) === '/api/scenario-test/runs' &&
      (i as RequestInit | undefined)?.method === 'POST');
    const body = JSON.parse(String((posted?.[1] as RequestInit)?.body ?? '{}'));
    expect(body.custom).toHaveLength(1);
    expect(body.custom[0].stresses[0].param).toBe('spot');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/routes/ScenarioTest.test.tsx -t "custom scenario and includes"`
Expected: FAIL — no "New custom scenario" button / custom list.

- [ ] **Step 3: Implement** — in `ScenarioTest.tsx`:

(a) Save handler + delete handler:

```tsx
  const handleSaveCustom = async (name: string, description: string, stresses: ScenarioStress[]) => {
    await saveScenarioSet(name, [{ name, description, stresses }]);
    setBuilder(null);
    await reloadCustomSets();
  };

  const handleDeleteCustom = async (name: string) => {
    if (!window.confirm(`Delete custom scenario "${name}"?`)) return;
    try {
      await deleteScenarioSet(name);
      setSelectedCustomNames((prev) => { const n = new Set(prev); n.delete(name); return n; });
      await reloadCustomSets();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const toggleCustom = (name: string) =>
    setSelectedCustomNames((prev) => {
      const n = new Set(prev);
      if (n.has(name)) n.delete(name); else n.add(name);
      return n;
    });
```

(b) Run wiring — in `handleRun`, replace the body construction to pass inline custom specs and
drop `scenario_set`:

```tsx
      const selectedCustom = customSets.filter((s) => selectedCustomNames.has(s.name));
      const body: ScenarioTestRunRequest = {
        portfolio_id: selectedPortfolioId,
        pricing_parameter_profile_id: selectedProfileId,
        predefined: Array.from(selectedKeys),
        custom: selectedCustom.map((s) => ({
          name: s.name, description: s.description, stresses: s.stresses,
        })),
        config: { calculate_greeks: true, greeks_method: 'numerical', export_formats: ['json', 'csv'] },
      };
```

Also disable the Run button when nothing is selected:

```tsx
              disabled={submitting || selectedPortfolioId == null
                || (selectedKeys.size === 0 && selectedCustomNames.size === 0)}
```

(c) Remove the saved-set `<label>…</label>` dropdown block from the PageHeader action
(the block guarded by `library != null && library.saved_sets.length > 0`).

(d) Add the Custom Scenarios section after the Predefined section:

```tsx
        <section className="wl-scenario-test__section">
          <div className="wl-scenario-test__section-head">
            <h2 className="wl-scenario-test__section-title">Custom Scenarios</h2>
            <Button variant="ghost" onClick={() => setBuilder({ initial: null })}>
              + New custom scenario
            </Button>
          </div>
          {customSets.length === 0 ? (
            <Empty message="No custom scenarios yet." symbol="◌" />
          ) : (
            <ul className="wl-scenario-test__scenario-list" role="list" aria-label="Custom scenarios">
              {customSets.map((s) => (
                <li key={s.name} className="wl-scenario-test__scenario-item">
                  <label className="wl-scenario-test__scenario-label">
                    <input type="checkbox" aria-label={s.name}
                      checked={selectedCustomNames.has(s.name)} onChange={() => toggleCustom(s.name)} />
                    <div className="wl-scenario-test__scenario-meta">
                      <button type="button" className="wl-scenario-test__scenario-name-btn"
                        aria-label={`Details for ${s.name}`}
                        onClick={(e) => { e.preventDefault();
                          setDetail({ name: s.name, description: s.description, stresses: s.stresses }); }}>
                        {s.name}
                      </button>
                      {s.description && <span className="wl-scenario-test__scenario-desc">{s.description}</span>}
                    </div>
                  </label>
                  <div className="wl-scenario-test__scenario-row-actions">
                    <Button variant="ghost" onClick={() => setBuilder({ initial: s })}>Edit</Button>
                    <Button variant="ghost" onClick={() => handleDeleteCustom(s.name)}>Delete</Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
```

(e) Render the builder dialog near the detail dialog at the end of JSX:

```tsx
      {builder && (
        <ScenarioBuilderDialog
          open
          initial={builder.initial}
          existingNames={customSets.map((s) => s.name)}
          onSave={handleSaveCustom}
          onClose={() => setBuilder(null)}
        />
      )}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/routes/ScenarioTest.test.tsx`
Expected: PASS (all page tests). Note: the older `posts a run` test asserts only `portfolio_id`
+ `predefined`; it remains valid (custom defaults to `[]`).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/ScenarioTest.tsx frontend/src/routes/ScenarioTest.css frontend/src/routes/ScenarioTest.test.tsx
git commit -m "feat(scenario): Custom Scenarios section with create/edit/delete + inline run"
```

> CSS: add `.wl-scenario-test__section-head` (flex, space-between), `.wl-scenario-test__scenario-name-btn`
> (button reset: no background/border, inherit color, pointer cursor, left-aligned),
> `.wl-scenario-test__scenario-row-actions` (flex, gap) to `ScenarioTest.css`. NOTE: a concurrent
> session has an uncommitted edit to this CSS file in the main checkout — coordinate at merge.

---

## Phase 7 — Full verification

### Task 11: run suites + typecheck

- [ ] **Step 1: Backend scenario suite**

Run: `python3 -m pytest tests/test_scenario_catalog.py tests/test_scenario_test_api.py -q`
Expected: PASS (all).

- [ ] **Step 2: Broader backend (no regressions in run path)**

Run: `python3 -m pytest tests/ -k "scenario" -q`
Expected: PASS (pre-existing unrelated failures, if any, are limited to missing optional deps — verify they match the known baseline).

- [ ] **Step 3: Frontend tests + typecheck**

Run: `cd frontend && npx vitest run src/components/ScenarioDetailDialog.test.tsx src/components/ScenarioBuilderDialog.test.tsx src/routes/ScenarioTest.test.tsx && npx tsc --noEmit`
Expected: PASS + no type errors.

- [ ] **Step 4: Commit (if any fixups)**

```bash
git add -A && git commit -m "test(scenario): verify detail dialog + custom CRUD end-to-end" || echo "nothing to commit"
```

---

## Self-review notes (author)

- **Spec coverage:** serializer (T1), predefined legs (T2/Gap 1), set read/delete (T3/T4), routes+schema (T5), types/api (T6), detail dialog (T7/Gap 1), builder (T8/Gap 2), page detail (T9), custom CRUD + inline run wiring (T10/Gap 2), verification (T11). All spec sections mapped.
- **Type consistency:** `serialize_scenario`/`get_set`/`list_sets_detailed`/`delete_set`, `ScenarioSetDetailOut`, `ScenarioSetDetail`, `fetchScenarioSets`/`saveScenarioSet`/`deleteScenarioSet`, `ScenarioDetailDialog`/`ScenarioBuilderDialog` props consistent across tasks. `stress_type` is uppercase end-to-end (`PERCENTAGE`); `level` lowercase (`portfolio`/`underlying`).
- **YAGNI:** no absolute resolved values, no position-level, no multi-scenario sets, no agent tools, no migration.
- **Known coupling:** none with skill-catalog / tool-count tests (no tools/skills added). `ScenarioTest.css` overlaps a concurrent uncommitted edit — resolve at merge.

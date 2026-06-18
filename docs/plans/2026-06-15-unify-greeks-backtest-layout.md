# Unify Greeks Landscape and Backtest Layout

## Goal
Give both **Greeks Landscape** and **Backtest** the same three-zone run-workbench pattern:
- **Top header:** Run Config (parameters + Run button)
- **Left rail:** Run History
- **Right workspace:** Run Detail

## Current State
- **Greeks Landscape** uses `AnalyticsDashboard`: controls live in a strip under the header, no run history, only the latest run is shown.
- **Backtest** uses `WorkbenchPage`: config is stacked in the left rail above run history, results are on the right, actions are in the header.

## Proposed Changes

### 1. Shared template: `RunWorkbenchPage`
A new template in `frontend/src/components/templates/` that wraps `PageScaffold` + `SplitLayout`:
- `actions` slot → Run Config header.
- `rail` slot → Run History.
- `children` → Run Detail.

It reuses the existing `.wl-workbench__*` chrome (run-row, section titles, panel) so both pages read as one family.

### 2. Backend: list Greeks Landscape runs
Add `GET /api/greeks-landscape/runs?portfolio_id={id}` mirroring `/api/backtest/runs`.

### 3. Backtest refactor
- Move the config form (dates, engine config, vol source) into the `actions` slot.
- Keep the run-history list in the left rail.
- Keep `RunReport` in the right workspace.

### 4. Greeks Landscape refactor
- Add run-history state + API helper `listGreeksLandscapeRuns`.
- Move grid/scope/engine/pricing controls into the `actions` slot.
- Put run history in the left rail.
- Put the charts / series / excluded-positions notes in the right workspace.
- Selecting a run loads it by id; running creates a run and selects it.

## Aesthetic Direction
Keep the existing utilitarian, token-driven design system (CSS variables, sharp borders, compact controls). The unification itself is the memorable improvement: identical muscle memory across both run-based tools.

## Files to Touch
- `frontend/src/components/templates/RunWorkbenchPage.tsx` (new)
- `frontend/src/components/templates/RunWorkbenchPage.css` (new)
- `frontend/src/components/templates/index.ts`
- `frontend/src/api/client.ts`
- `backend/app/main.py`
- `frontend/src/routes/Backtest.tsx`
- `frontend/src/routes/Backtest.css`
- `frontend/src/routes/GreeksLandscape.tsx`
- `frontend/src/routes/GreeksLandscape.css`
- Related tests

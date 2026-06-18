# Currency Validation + Report `by_currency` Enrichment — Design Spec

**Date:** 2026-06-02
**Status:** Approved (brainstorming) — ready for implementation plan
**Branch:** continues on `feature/currency-convention` (same worktree); follow-ups from the phase-1 final review.

## Problem

Two gaps left from the Currency Convention work:

1. **No ISO-code validation.** Currency codes enter the system at the FxRate schemas, the thread `report_currency`, and the `convert_currency` tool. A typo (`"USdD"`, `"XYZ"`) is accepted silently — it becomes the report currency, an un-resolvable FX pair, or a target that makes every bucket land in `missing`. We want typos rejected loudly.
2. **Reports don't show the per-currency breakdown.** `calculate_portfolio_risk` produces `by_currency` (money metrics grouped per currency) + `shared` (currency-invariant delta/gamma/delta_proxy), but the HTML/xlsx reports only render the flat `totals` — which is `None` for mixed-currency portfolios (so those reports show empty metrics). The real per-currency data never reaches the report.

## Goals

- Reject non-ISO-4217 currency codes at every input boundary (hard 422 / structured tool error), with normalization (`usd` → `USD`).
- Render the `by_currency` breakdown + the `shared` block in both the HTML and xlsx reports, handling single- and mixed-currency portfolios.

## Non-Goals

- Validating `Portfolio.base_currency` (out of scope; the user scoped this to report_currency + FX pairs).
- A migration to re-validate existing stored codes (validation applies to new input going forward).
- Quanto support.

---

## Feature A — ISO 4217 currency validation (hard-reject)

### New module `backend/app/services/currency_codes.py`
- `ISO_4217_CODES: frozenset[str]` — the active ISO 4217 three-letter alphabetic codes (~180). A static constant; **no new dependency** (pycountry is not installed).
- `normalize_currency(code: str) -> str` — `str(code).strip().upper()`.
- `is_valid_currency(code: str) -> bool` — `normalize_currency(code) in ISO_4217_CODES`.

### Validation points
All normalize first, then reject unknown codes:

| Location | Rule |
|---|---|
| `FxRateCreate.base_currency` / `quote_currency` | Pydantic field validator → must be a valid ISO code (normalized); else `ValueError` → FastAPI **422**. |
| `FxRateAkshareRequest.base_currency` / `quote_currency` | Same validator — validate **before** the akshare fetch. |
| `AgentThreadUpdate.report_currency` | Must be a valid ISO code **or** the literal `"by_position"` sentinel (normalize ISO codes; leave `by_position` as-is). |
| `convert_currency` tool (`tools/risk.py`) | Guard `target_currency`: if not a valid ISO code, return `{"error": "Invalid target currency: <x>", "totals": {}, "fx_rates_used": {}, "missing": []}` instead of silently reporting all buckets as `missing`. |

Normalized values are what get **stored** (FxRate rows) and **set** (thread.report_currency), so the DB holds canonical uppercase codes.

### Error handling
- Schema validators raise `ValueError` → Pydantic `ValidationError` → FastAPI returns **422** with a clear message naming the bad field/value.
- The tool returns a structured error dict (the agent surfaces it); it does not raise (consistent with the tool-error-boundary convention).

### Tests (`tests/test_currency_validation.py`)
- `is_valid_currency` / `normalize_currency`: `"usd"`→valid/`"USD"`; `"XYZ"`,`"US"`,`""`→invalid.
- `FxRateCreate` / `FxRateAkshareRequest`: valid pair ok (and lowercase normalizes); typo raises `ValidationError`.
- `AgentThreadUpdate.report_currency`: `"USD"` ok, `"by_position"` ok, `"US"`/`"XYZ"` raises.
- API: `POST /api/market-data/fx-rates` with bad code → 422; `PATCH /api/chat/threads/{id}` with bad report_currency → 422; with `by_position` → 200.
- `convert_currency_tool` with `target_currency="XYZ"` → result has an `error` key, no crash.

---

## Feature B — `by_currency` breakdown in HTML + xlsx reports

Both renderers read the existing risk-dict shape directly; defensive `risk.get("by_currency") or {}` and `risk.get("shared") or {}` so old-shape payloads don't crash.

### `reports.py::_write_html`
- **Top metric cards:** keep the existing 4 cards **only** when single-currency (`risk["totals"]` is a dict). When mixed (`totals` is None), replace them with a `<p>Mixed currency — see the per-currency breakdown below.</p>` note.
- **New "By currency" section:** for each currency in `by_currency` (sorted), a labelled block listing its money metrics — `market_value, pnl, gross_notional, one_day_var_proxy, vega, theta, rho, rho_q, delta_cash, gamma_cash, position_count`. Reuse the existing `.metric`/`.value` card styling.
- **New "Shared (currency-invariant)" line:** `delta, gamma, delta_proxy` from `shared`, rendered once.

### `reports.py::_write_xlsx`
- Keep the existing **Summary** sheet (writes `totals.items()`; empty when mixed — acceptable, the breakdown sheet carries the data).
- **New "By Currency" sheet:** header `["currency", "metric", "value"]`, then one row per (currency, money-metric, value) for every currency in `by_currency`, followed by `shared` metrics with currency `"(shared)"`.

### Tests (`tests/test_report_by_currency.py`)
- `_write_html` mixed-currency payload (two currencies, `totals=None`) → output contains each currency label, its `market_value`, the "Shared" delta value, and the "Mixed currency" note.
- `_write_html` single-currency payload → contains the top cards AND the by_currency block AND shared.
- `_write_xlsx` mixed-currency payload → workbook has a "By Currency" sheet with a row per currency-metric and the `(shared)` rows; file opens without error.
- Defensive: payload missing `by_currency`/`shared` → no crash.

---

## Affected files
- New: `backend/app/services/currency_codes.py`, `tests/test_currency_validation.py`, `tests/test_report_by_currency.py`.
- Modify: `backend/app/schemas.py` (validators on FxRateCreate / FxRateAkshareRequest / AgentThreadUpdate), `backend/app/tools/risk.py` (`convert_currency` target guard), `backend/app/services/reports.py` (`_write_html`, `_write_xlsx`).

## Build order
1. Feature A: `currency_codes.py` + validators + tool guard (one task group).
2. Feature B: HTML + xlsx enrichment (one task group).
Independent; A first (smaller, touches schemas/tool), then B.

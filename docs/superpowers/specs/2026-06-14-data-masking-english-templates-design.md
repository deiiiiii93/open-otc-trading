# Data Masking + Standardized English Import Templates

**Date:** 2026-06-14
**Branch:** `feature/data-masking-english-templates`

## Goal

1. Mask all `国金证券` / `GJZQ` brokerage-brand identifiers across code, the local
   databases, and data files.
2. Redesign the two import templates (Positions page, Pricing Parameter page) into
   standardized, English, self-documenting Excel templates with in-app download
   buttons.
3. Refactor the file adapters accordingly.

## Decisions (confirmed with user)

| Fork | Decision |
| --- | --- |
| Mask token | `GJZQ` → `OTC`; `国金证券` → "OTC Desk"; `同余` (counterparty tag) → `OTC` |
| Adapter header language | **English only** (clean break — Chinese-headered uploads no longer accepted) |
| Mask scope | Live DB + artifact/upload/output files + tracked test fixtures; leave `.bak`/`pre-*` snapshots |
| Template format | Data sheet + Instructions sheet |

## Architecture

### Single source of truth — `backend/app/services/import_schema.py`
Defines, once, the English column headers, the cell enum vocabulary (Buy/Sell,
Call/Put, structure names, Yes/No, custom-structure tags) and per-column
`ColumnSpec` metadata (required / type / allowed values / notes / example rows).
Both the parsers and the template generator import from here, so the blank
template can never drift from the columns the adapter reads.

It also defines a **legacy stored-payload compatibility** layer: helpers
(`read_trade_status`, `read_notional_unit`, `is_terminal_status`,
`is_knocked_out`) that accept both the new English values and the legacy Chinese
tokens. `source_payload` is a historical record — the live DB holds rows imported
under the old Chinese headers, and brand-masking does not translate the field
vocabulary — so readers of stored payloads must tolerate both.

### Adapters (clean break to English)
- `position_adapter.py` — Chinese header lookups and enum comparisons replaced
  with the schema constants; default sheet `汇总` → `Positions`.
- `market_input_workbooks.py` — English required-header set.

### Downstream payload readers (dual-format)
`quantark.py`, `position_pricer.py`, `underlyings.py` read trade status / currency
out of stored payloads. Updated to use the compatibility helpers so freshly
imported English positions *and* historical Chinese positions both resolve their
terminal-state / currency correctly.

### Template generator + endpoints
- `import_templates.py` builds each workbook (Data sheet with header + example
  rows; Instructions sheet documenting every column).
- `GET /api/positions/import-template` and
  `GET /api/pricing-parameter-profiles/import-template` stream the `.xlsx`.

### Frontend
A **Download Template** anchor (reusing the `wl-button` style on an `<a download>`)
beside each existing "Import XLSX" button on the Positions and Pricing Parameter
pages. `.wl-button` gained `text-decoration: none` so anchors render as buttons.

### Data masking — `scripts/mask_brand_data.py`
Committed, idempotent. Backs up the primary DB, sweeps every text/JSON column of
`open_otc.sqlite3` + `agent_traces.sqlite3` with ordered nested `REPLACE`, and
renames + rewrites brand-bearing files under `outputs/`, `artifacts/`,
`data/scenario_sets/`. The same token rules drive the DB sweep and the file
renames, keeping `position_import_batches.source_path` consistent with disk.

## Verification

- Templates round-trip: each template's own example rows parse back through the
  real adapters as supported positions / pricing rows (`tests/test_import_templates.py`).
- Full backend suite: zero net regressions vs. base commit `8bcf43b` (216
  pre-existing environmental failures unchanged; the only diff is 4
  `cross_channel` parametrize-id renames, same underlying pre-existing failures).
- Masking: 0 brand tokens remain in either DB, in file names, or in file content.

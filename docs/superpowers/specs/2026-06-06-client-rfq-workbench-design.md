# Client RFQ Workbench — Design

**Date:** 2026-06-06
**Status:** Approved (pending implementation)

## Problem

The Client RFQ page (`frontend/src/routes/ClientRfq.tsx` + `RfqIntakeCard.tsx`)
is the weakest intake surface in the app:

- **Raw JSON textareas** — the Structured tab exposes a "Product Terms JSON"
  editor and a whole "Advanced JSON" tab; clients are expected to hand-edit
  QuantArk kwargs.
- **Free-text everything** — underlying is a text input (`CSI500`), the
  solve-for field is a free-text `field_path` with hardcoded bounds
  (`50/150/100`) that only make sense for strike-like fields.
- **Tenor footgun** — a free-text Tenor input (`1Y`) silently overwrites
  `terms.maturity` at submit time, so the visible terms and the submitted terms
  disagree.
- **Single-RFQ amnesia** — only the latest RFQ id survives (in localStorage);
  there is no history, and clearing browser state orphans the client's RFQs.
- **No page context** — every other route reports `PageContext` via
  `usePageContextReporter`; ClientRfq is invisible to the deep agent.

Try to Solve (`TrySolve.tsx`) demonstrates the target quality: catalog-driven
per-product editors, underlying dropdown of active instruments, quote-field
dropdown with prefilled bounds/initial guess, status badges, diagnostics.

## Decisions (user-confirmed)

1. **Better intake form only** — keep the single-request intake flow that feeds
   the existing approval pipeline. No multi-row draft queue, no Excel
   import/export, no market-data/pricing profiles, no indicative solve preview.
2. **Keep NL, drop Advanced JSON** — Natural Language stays as an intake path;
   the Advanced JSON tab and the raw Product Terms JSON textarea are removed.
3. **History list** — the page shows the client's recent RFQs (new backend
   list endpoint), replacing the localStorage latest-id mechanism. Polled so
   status transitions (`pending → approved → released`) appear live.
4. **Layout B** — three-column workbench in Try-to-Solve's visual language:
   My RFQs | Product Terms | Quote & Submit.
5. **NL as mode tabs** — tabs (Natural Language | Structured) above the
   workbench; NL mode swaps the Terms + Quote panels for one large message
   panel. The My RFQs column stays in both modes.

## Layout

```
CLIENT RFQ                                  [3 RFQs · 1 pending]
[ Natural Language ] [ Structured ]
┌─ My RFQs ─────┐ ┌─ {Product} Terms ──────┐ ┌─ Quote & Submit ───────┐
│ #42 Snowball  │ │ Product: Snowball ▾    │ │ Mode: Solve ▾          │
│   pending ⏳   │ │ Underlying: ▾ (active) │ │ Solve for: KO Rate ▾   │
│ #41 Vanilla   │ │ Side ▾  Notional       │ │ Bounds: -1 / 2         │
│   approved ✓  │ │ Client name            │ │ Initial guess: 0.15    │
│ [Clone]       │ │ … per-product fields   │ │ Target: price ▾ = 0    │
│               │ │   (ProductTermsForm)   │ │ [Submit RFQ ▸]         │
│               │ │                        │ │ ── Status: #42 ──      │
│               │ │                        │ │ badge · solved · price │
│               │ │                        │ │ response · error       │
└───────────────┘ └────────────────────────┘ └────────────────────────┘
```

NL mode: middle + right panels are replaced by a single "Message" panel
(textarea + Submit Natural Language); left column unchanged.

## Backend

### 1. New endpoint: `GET /api/client/rfqs`

- Query params: `client_name` (optional filter, exact match), `limit`
  (default 20, max 100).
- Returns `list[RFQOut]` ordered `created_at desc`, with `quote_versions`
  selectinloaded (same shape as `GET /api/internal/rfqs`).
- Lives next to the other client endpoints in `main.py`.

### 2. Catalog extension: `unknown_field_specs`

`get_rfq_catalog()` (`backend/app/services/rfq.py`) templates gain a per-product
list of solve-field specs, single source of truth for the solve-for dropdown:

```python
"unknown_field_specs": [
    {"field_path": "strike", "label": "Strike",
     "lower_bound": 50.0, "upper_bound": 150.0, "initial_guess": 100.0},
    ...
]
```

- One spec per entry in each template's existing `unknown_fields` (which is
  kept untouched for compatibility).
- Bounds are seed defaults in the template's own value convention (absolute
  for strike/barrier-like fields on the 100-scale templates; rate-like fields
  such as `ko_rate` / `coupon_rate` get `-1 / 2 / 0.15`-style bounds;
  `volatility` gets `0.01 / 2.0 / 0.2`). The user can edit all three before
  submitting.
- The top-level `unknown_fields` map in the catalog response also remains.

### 3. Unchanged

`POST /api/client/rfq/form`, `POST /api/client/rfq/chat`,
`GET /api/client/rfq/{id}`, the approval pipeline, and the desk pages are not
modified.

## Frontend

### Components

- **`ClientRfq.tsx`** — rebuilt as a props-driven presentational workbench
  (mirroring `TrySolve.tsx`): mode tabs, three `Panel` columns, page-context
  reporting. Receives catalog, instruments, rfqs list, loading/error/feedback,
  and submit/select/clone callbacks.
- **`ClientRfq.live.tsx`** — data wrapper:
  - Fetches `/api/rfq/catalog`, `/api/instruments`, `/api/client/rfqs`.
  - Polls the list every 10s (and refreshes immediately after a submit).
  - Persists `client_name` in localStorage (default `"Demo Client"`); it is
    sent on both NL and structured submissions and used as the list filter.
    The old `openOtc.latestClientRfqId` key is no longer read or written.
  - Maps backend 400 `detail` to the error strip.
- **`RfqHistoryPanel.tsx`** (new) — left column: rows with `#id`,
  product label (derived from `request_payload.product.quantark_class` via the
  catalog; fallback to the raw class name), status `Badge`
  (existing `statusVariant` mapping), created time. Click selects; selected row
  highlights. A **Clone** action on the selected row copies its
  `request_payload` into the structured editor as a new draft (does not
  submit).
- **Status detail** — bottom of the right panel, rendered for the selected
  RFQ: status badge, solved field label + `solved_value`, `achieved_price`,
  `approved_response` / `client_response`, `quantark_error`. Absorbs what
  `RfqStatusCard` shows today, plus a compact submitted-terms summary
  (underlying · side · notional · product).
- **Retired:** `RfqIntakeCard` (+ CSS + test), `RfqStatusCard` (+ CSS + test),
  Advanced JSON tab, Product Terms JSON textarea, Tenor free-text input.

### Structured editor (middle + right panels)

- **Product picker** — catalog templates rendered as `label` (e.g. "Snowball"),
  value = `product_type`. Switching products repopulates terms from the
  template's `product_kwargs` (clone) and resets the solve-for selection to the
  product's first spec, exactly like today's product-switch handler.
- **Underlying** — dropdown of active instruments (`/api/instruments`,
  `status === 'active'`), reusing the option-handling behavior of TrySolve's
  `FieldControl` (current-but-inactive value shown disabled, empty-state
  options). Default remains `CSI500` only if it is an active instrument;
  otherwise the placeholder "Choose underlying".
- **Side / Notional / Client name** — selects and number input as today;
  client name moves into the form (single field for both intake modes).
- **Terms** — `ProductTermsForm` reused as-is for products it covers;
  `PRODUCT_TERM_FIELDS` extended with the missing products from the catalog:
  `OneTouchOption`, `RangeAccrualOption`, `Futures`, `SpotInstrument`,
  and a nested-form entry for `KnockOutResetSnowballOption` (its flat contract
  is already covered by `FLAT_CONTRACT_FIELDS`).
- **Maturity** — edited directly in the terms form (every template carries
  `maturity` or `maturity_years` in `product_kwargs`); no separate tenor input
  and no submit-time overwrite.
- **Quote mode** — `Solve unknown` | `Price fixed terms`. Price mode hides the
  solve-for/bounds/target controls and submits `quote_mode: "price"` without
  `unknown`/`target` overrides (backend already accepts this).
- **Solve-for field** — dropdown over the product's `unknown_field_specs`;
  selecting one prefills lower/upper/initial-guess inputs (still editable).
- **Target** — label select (`price` | `premium` | `reoffer`) + value input,
  as today.
- **Submit gating** — submit disabled until: an active underlying is chosen,
  notional is a positive number, and `isContractComplete` passes (existing
  snowball-family flat-contract gate, unchanged semantics). Backend errors
  surface in the error strip; successful submit shows a feedback strip and
  selects the new RFQ in the history panel.

### Page context

`usePageContextReporter` with:

- `route: 'client-rfq'`, `entity_ids: { rfq_id: selected }`.
- Snapshot: rfq count, selected RFQ summary (id/status/product), current
  editor product + mode.
- `declareActions`: `submit_structured_rfq` (explicit confirmation,
  `POST /api/client/rfq/form`), `submit_nl_rfq` (explicit,
  `POST /api/client/rfq/chat`).

## Submission payload (unchanged shape)

The structured submit builds the same `/api/client/rfq/form` body as today —
`client_name`, `side`, `quantity`, `quote_mode`, `product { asset_class,
product_family (inferProductFamily), quantark_class, underlying, terms }`,
`engine_spec` (from template), `unknown { field_path, lower_bound, upper_bound,
initial_guess }`, `target { label, value }` — minus the tenor-overwrite step.

## Error handling

- Fetch failures: catalog/instruments fall back to current behavior (catalog
  fallback templates already exist in the frontend); list failure shows the
  error strip but leaves the editor usable.
- Submit failure: error strip with backend `detail`; form state preserved.
- Polling failures are silent (next tick retries); polling pauses while a
  submit is in flight.

## Testing

### Backend (pytest)

- `GET /api/client/rfqs`: ordering (desc), `client_name` filter, `limit`
  clamping, quote_versions present.
- Catalog: every template has `unknown_field_specs`; each spec's `field_path`
  appears in the template's `unknown_fields`; bounds are finite and
  `lower < upper`.

### Frontend (vitest)

- Workbench: three panels render; mode tab swaps editor panels for the message
  panel; chips reflect counts.
- Product switch repopulates terms + solve-for defaults; solve-for selection
  prefills bounds; price mode hides target controls.
- Underlying dropdown lists only active instruments; inactive current value
  rendered disabled.
- Submit: payload shape (no tenor overwrite; maturity from terms), gating
  (incomplete snowball contract disables submit), error strip on 400.
- History: rows render with badges; click selects and shows status detail;
  Clone populates the editor; NL submit refreshes the list.
- Live wrapper: initial fetches, poll tick refresh, localStorage client name.

## Non-goals

- Multi-row draft queue, Excel import/export (Try-to-Solve keeps those).
- Market-data / pricing-parameter profile selection (quote happens desk-side).
- Indicative solve preview before submit.
- NL draft-preview step (`/api/rfq/draft/from-nl` exists but is not wired in).
- Auth / real client identity; `client_name` remains a self-declared string.

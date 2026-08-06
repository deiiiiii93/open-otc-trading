# Report module redesign — design

**Date:** 2026-08-06
**Status:** Draft for review
**Scope:** Replace the Reports page/module with a template-driven report pipeline, plus the
P&L producers those templates consume.

---

## 1. Problem

The Reports page shows almost nothing, because the report *module* is three disconnected
pieces:

| Piece | What it does | Why it disappoints |
|---|---|---|
| `services/reports.py` (`ReportJob`) | `POST /api/reports/jobs` recomputes portfolio risk, then writes a **hardcoded** HTML + XLSX | Content is frozen in `_write_html`: four metric cards, a by-currency grid, a positions table. No limits, no attribution, no narrative, no charts |
| `routes/Reports.tsx` + `ReportReader.tsx` | Timeline of cards → modal | The modal renders artifact **file paths** and a `<pre>` dump of `result_payload`. That is the entire reader |
| `write_report_artifact` | Agent prose → markdown/docx/html thread artifact | Never becomes a `ReportJob`; **never appears on the Reports page at all** |

The split is deliberate and documented: `skills/workflows/risk/create-risk-report/SKILL.md`
instructs the agent *"Do not call `create_report` from this workflow."* One path has numbers
with no voice; the other has voice with no persistence. Neither is a report.

### Governing constraint

`CLAUDE.md`: *"Pricing/risk math is delegated to QuantArk — numbers never come from an LLM."*
Any design where an agent writes a report end-to-end violates this. The central architectural
job is a seam that lets an agent author narrative while making it **structurally impossible**
for it to mint a number.

---

## 2. Goals / non-goals

**Goals**

1. Three seeded daily templates — trader, risk manager, high board.
2. Templates are declarative documents an agent can read, and write.
3. Reports render natively and well, in both themes, token-only.
4. Reports carry provenance and are reproducible.
5. Day-over-day: what changed since the last run, and what explains it.
6. One report writer, not two.

**Non-goals**

- Scheduling / automated daily dispatch. Generation is triggered by a human or an agent.
  (`services/schedules.py` exists; wiring it is a follow-on.)
- Multi-user / per-user template ownership. The constant-`desk` identity seam stands.
- Embeddings, vector search, or RAG over reports. Explicitly out, per the compaction
  evidence-path rule.

---

## 3. Decomposition

Two independently-testable subsystems, **one spec** (so the interfaces are designed together
and cannot drift), **two implementation plans**.

### Sub-project A — `backend/app/services/pnl/` (deterministic, no LLM)

- `snapshot_diff.py` — diff two risk runs: ΔMV, Δexposure, new/closed positions, coverage
  change, newly-breached limits.
- `explain.py` — Greeks attribution + residual.
- `entry_price.py` — hygiene: set/backfill on legacy rows; carry RFQ `unit_price` through the
  RFQ → position booking path.

### Sub-project B — `backend/app/services/reporting/` (the module asked for)

- Block registry → template model → generation pipeline → renderer → three seeded templates.

**Build order: B's block contracts → A → B's pipeline and renderer.** Defining what
`pnl.explain` must *emit* is what tells A exactly what to compute. Building A first invites
contract drift.

---

## 4. Sub-project A — the P&L producers

### 4.1 What is already persisted

Each completed `risk_runs.metrics` carries, **per position**:

- `resolved_market` — `spot`, `volatility`, `rate`, `dividend_yield`, `valuation_date`
  (inside `source_metadata.market_evidence_manifest.positions[]`)
- `delta`, `gamma`, `vega`, `theta`, `rho`, `rho_q`, `market_value`, `price`, `quantity`
  (inside `metrics.positions[]`)
- `metric_contract` — declaring each Greek's exact unit and bump convention:
  `vega → {currency}/1volpct`, `theta → {currency}/1day`, `rho → {currency}/1pct`,
  `delta → underlying_units`, `gamma → underlying_units_per_spot_unit`

Nothing new needs capturing. Attribution is a **diff of two manifests with the units already
declared**.

### 4.2 `snapshot_diff.py`

```
diff(run_a, run_b) -> {
  totals:    { metric: {before, after, change, pct_change} },
  positions: { position_id: {before, after, change} },
  membership: { added: [...], removed: [...], held: [...] },
  coverage:  { before: {priced, total}, after: {priced, total} },
  market:    { position_id: {spot: {before, after}, volatility: {...}, ...} },
  as_of:     { before: ..., after: ..., elapsed_days: float },
}
```

Rules:

- Positions are matched by `position_id`. A position present in only one run appears in
  `membership`, never as a spurious ΔMV.
- `elapsed_days` comes from `valuation_as_of`, **not** wall-clock `created_at` — two runs
  priced at the same valuation date have `elapsed_days == 0` and therefore zero theta P&L,
  even if they were computed a week apart.
- If `position_set_hash` differs, the diff is still produced but flagged
  `composition_changed: true`; consumers must surface it.

### 4.3 `explain.py`

Per position, decompose ΔMV using the declared conventions:

```
delta_pnl  = delta   × ΔS
gamma_pnl  = 0.5 × gamma × ΔS²
vega_pnl   = vega    × Δσ × 100        # vega is per 1 vol-point; Δσ is absolute
theta_pnl  = theta   × Δt_days
rho_pnl    = rho     × Δr × 100        # rho is per 1%
rho_q_pnl  = rho_q   × Δq × 100
explained  = Σ(above)
residual   = ΔMV_actual − explained
```

- Aggregation to portfolio level is a straight sum per bucket (all in reporting currency).
- Positions where either run has `pricing_ok = false` or `greeks_ok = false` are **excluded
  and counted**, never silently zero-filled.
- Multipliers are read from `metric_contract`, not hardcoded — if a future contract version
  changes a unit, the code follows it or fails loudly on an unknown `contract_id`.

**Validation:** unit tests assert the decomposition reconciles on a controlled two-run fixture
(bump one input at a time; the corresponding bucket must carry the whole move and residual must
be ~0). A single-bump test per Greek is the correctness proof.

### 4.4 `entry_price.py`

Current state: `entry_price` is plumbed end-to-end (model → schema → `book_position` →
importer → REST) and **is** populated wherever a real price existed (positions 7, 26, 27 —
the confirmations path already captures and passes it). The zeros are seed, demo, and hedge
rows.

Work:

1. Carry `rfq_quote_versions.quote_payload.unit_price` through the RFQ → position booking path.
2. A `set_position_entry_price` REST + agent surface for correcting legacy rows.
3. `inception_pnl` reports honestly: positions with `entry_price == 0` are reported as
   **basis-missing** and excluded from the inception total rather than inflating it.

Point 3 is the important one. `pnl = (price − entry_price) × qty × multiplier`, so
`entry_price == 0` currently makes inception P&L equal market value — a number that looks
authoritative and means nothing.

---

## 5. Sub-project B — the report module

### 5.1 The seam

```
  template.yaml ──► BlockRegistry.resolve()  ──►  BlockResult[]
                    (server, deterministic)        { status, data, provenance }
                                                          │
                                                          ▼
                                   agent sees blocks + per-section narrative brief
                                                          │
                                            returns PROSE ONLY (one string per section)
                                                          │
                                                          ▼
                                    ReportDocument { sections[{blocks, narrative}] }
```

Three properties:

1. **The agent cannot mint a number into the document structure.** Its output slot is a prose
   string; every rendered figure comes from a `BlockResult`.
2. **The agent can request more evidence** via a read-only `resolve_report_block(key, params)`
   tool. More evidence, never invented evidence.
3. **Prose is grounding-checked at runtime.** The arena scoring already ships a numeric
   grounding matcher (`_scan_numeric_tokens` / `_quote_value_report` in
   `golden_workflows/assertions.py`, backing `response_quotes_value`). Reuse it as a
   guard: every numeric token in a section's narrative must appear in that section's block
   data. An offline benchmark tool becomes a production safety net at no build cost.

### 5.2 Block registry

`backend/app/services/reporting/blocks/`

```python
@report_block(
    key="pnl.explain",
    title="P&L attribution",
    requires=("portfolio_id", "compare_to"),
    output=BlockShape.WATERFALL,
    domain="pnl",
)
def pnl_explain(ctx: BlockContext) -> BlockResult: ...
```

```python
BlockResult = {
  "status": "ok" | "empty" | "unavailable",
  "reason": str | None,        # REQUIRED when status != "ok"
  "data":   {...},             # conforms to the declared output shape
  "provenance": {
     "risk_run_id": int, "compare_to_run_id": int | None,
     "valuation_as_of": str, "position_set_hash": str,
     "coverage": {"priced": int, "total": int},
  },
}
```

**The tri-state is the most important single decision in this design.**

- `empty` — the producer ran; there is genuinely nothing. *No breaches today.*
- `unavailable` — the producer could not run. *No prior risk run to compare against;
  `scenario_test_runs` is empty.*

On a risk report these must never render the same, and **the agent is told the status** — a
section whose limits block came back `unavailable` gets a narrative brief stating the check did
not run. Without this, the most dangerous sentence an LLM can produce here — *"the book is
within all limits"* — is precisely the one it writes when the limit check silently returned
nothing. This promotes the `enumerate_limit_breaches` honest-empty gotcha from tribal
knowledge into a type.

**Block shapes** (drive renderer compatibility): `SCALARS`, `SCALARS_WITH_PRIOR`, `ROWS`,
`SERIES`, `WATERFALL`, `ITEMS`, `POSITION_GREEKS`.

**Initial block set**

| Key | Shape | Source |
|---|---|---|
| `risk.totals` | `SCALARS` | `risk_runs.metrics.totals` |
| `risk.exposure_by_underlying` | `SERIES` | `metrics.positions[]` grouped |
| `risk.greeks_by_bucket` | `POSITION_GREEKS` | `metrics.positions[]` |
| `risk.exposure_diff` | `SCALARS_WITH_PRIOR` | `snapshot_diff` |
| `pnl.daily` | `SCALARS_WITH_PRIOR` | `snapshot_diff` |
| `pnl.explain` | `WATERFALL` | `explain` |
| `pnl.by_position` | `ROWS` | `snapshot_diff.positions` |
| `pnl.inception` | `SCALARS` | `metrics.totals.pnl` + basis-missing count |
| `positions.changes` | `ROWS` | `snapshot_diff.membership` |
| `positions.barrier_proximity` | `ITEMS` | `position_barrier_state`, autocallable observations |
| `limits.utilization` | `SCALARS_WITH_PRIOR` | `limit_evaluations` |
| `limits.breaches` | `ITEMS` | `limits` evaluator |
| `limits.incidents` | `ROWS` | `limit_incidents` |
| `rfq.open_pipeline` | `ROWS` | `rfqs`, `rfq_quote_versions` |
| `rfq.pending_approvals` | `ROWS` | `rfq_quote_versions` awaiting approval |
| `coverage.evidence` | `SCALARS` | `metrics.coverage` + `market_evidence_manifest` |
| `audit.write_actions_summary` | `ROWS` | `agent_action_audits` |
| `scenario.latest_grid` | `ROWS` | `scenario_test_runs` — **`unavailable` today** |

`audit.write_actions_summary` is new surface area worth calling out: `agent_action_audits` holds
2,417 rows and nothing in the product reports on it. *"What did the agents do to the book, and
what did a human approve?"* is the board's governance question, and the fail-closed audit trail
exists precisely so it has a truthful answer.

### 5.3 Template model

Table `report_templates`, following the `DeskWorkflow` pattern (spec is the source of truth;
metadata columns are a denormalized cache extracted on save):

| column | notes |
|---|---|
| `id` | pk |
| `slug` | unique, indexed, kebab-case |
| `title`, `persona`, `description` | cache, extracted from `spec.meta` |
| `spec` | **YAML text — source of truth** |
| `source` | `seed` \| `user` \| `agent` |
| `version` | int, bumped on save |
| `created_at`, `updated_at` | |

**No template-versions table.** Each generated `ReportDocument` **embeds the full resolved spec
and its sha256**. A report is a self-contained, reproducible artifact, and no hash points at a
row someone may later edit — the dangling-pointer class of bug is structurally excluded. (If
template churn becomes real, an append-only versions table is the additive fix.)

**Spec format** — YAML, not Python: no `eval` tool, no sandbox, no
`EvalAttributionGateMiddleware`-scale governance. An agent authoring a template cannot author
execution.

```yaml
meta:
  slug: trader-daily
  title: Trader — Daily Book Review
  persona: trader
  description: Overnight P&L, what drove it, and what needs attention at the open.
  params:
    - { name: portfolio_id, type: portfolio,    required: true }
    - { name: compare_to,   type: risk_run_ref, default: previous }

sections:
  - id: headline
    title: Overnight
    blocks:
      - { key: pnl.daily,   render: delta_metric_row }
      - { key: risk.totals, render: metric_row, fields: [delta_cash, vega, theta] }
    narrative: |
      One paragraph. State the overnight move and the single largest driver.
      If coverage is below 100%, say so in the first sentence.

  - id: attribution
    title: What moved it
    blocks:
      - { key: pnl.explain, render: waterfall }
    narrative: |
      Explain the decomposition. If |residual| exceeds the declared threshold,
      state that the explain is incomplete and do not speculate about why.
```

Three deliberate choices:

- **`render` lives at the usage site, not on the block.** The same
  `risk.exposure_by_underlying` is a bar chart in the trader template and a table in the board
  one-pager. Blocks own data; templates own presentation.
- **`narrative: null` is legal** → a pure-data section with no agent involvement. A template
  with no narrative anywhere generates with **zero LLM calls**, fully deterministically.
- **`params` mirrors `DeskWorkflow.meta` params**, so the launch UI can be shared.

### 5.4 Validate-then-commit

Mirroring `channel_registry_writer._mutate`: the full check runs before anything persists —
422 on failure, live row byte-unchanged.

- every `blocks[].key` resolves in the registry → else 422 **naming the unknown key**
- every `render` is known **and compatible with that block's declared `output` shape**
  (`render: waterfall` on a `SCALARS` block fails at *save*, not at render)
- every `fields[]` entry exists in the block's output schema
- `persona` is a real persona; `slug` is unique and well-formed
- YAML parses; spec validates against the `TemplateSpec` pydantic model

This is the anti-dangling-pointer gate. `CLAUDE.md` records the cost of skipping it: *"a
declared-but-unwritten path is a dangling pointer… the model burns calls hunting the file."*

### 5.5 Generation pipeline

`generate_report(template_slug, portfolio_id, compare_to=None)`:

1. Load template; resolve `params`.
2. Resolve `compare_to` (`previous` → the prior completed risk run for that portfolio).
3. Resolve every declared block → `BlockResult`. **No LLM involved.**
4. For each section with a non-null `narrative`: give the agent that section's block results
   (including `status`/`reason`) plus the brief; receive a prose string.
5. Run the grounding guard over each narrative.
6. Assemble `ReportDocument`, persist, render.

**Which agent narrates.** `spec.meta.persona` selects the persona that writes the narrative
sections, so a trader template is narrated by the trader persona and a board template by
`high_board`. Sections are narrated **independently** — each gets only its own blocks, not the
whole document — which keeps the context small, makes the grounding guard section-scoped, and
means one section's failure cannot corrupt another's prose. Sections with `narrative: null` are
skipped entirely; a template with no narrative anywhere therefore dispatches no persona at all.

This is also why §5.9's persona-domain gap is load-bearing rather than cosmetic: a
`persona: trader` template dispatches the trader persona, which today cannot see the
`reporting` domain.

```python
ReportDocument = {
  "template": {"slug": str, "version": int, "spec": str, "spec_sha256": str},
  "params":   {"portfolio_id": int, "compare_to_run_id": int | None},
  "generated_at": str,
  "sections": [
    {"id": str, "title": str,
     "blocks": [{"key": str, "render": str, "result": BlockResult}],
     "narrative": str | None,
     "grounding": {"checked": bool, "flags": [...]}}
  ],
  "provenance": {"risk_run_id": int, "valuation_as_of": str,
                 "position_set_hash": str, "market_evidence_hash": str,
                 "coverage": {...}},
}
```

### 5.6 Storage

Extend `ReportJob` rather than forking: add nullable `template_slug` and `compare_to_run_id`;
store the `ReportDocument` in `result_payload`. This reuses the task runner, the timeline, the
pager, and artifact export, and it closes the two-systems split — the templated path becomes
*the* report path.

`result_payload` therefore carries two shapes, discriminated by `template_slug is null`
(legacy) vs. set (`ReportDocument`). The reader branches on it; the four existing legacy rows
stay readable.

### 5.7 Legacy path

**Deleted:** `_write_html`, `_write_xlsx`, `_build_report_payload`, `_metric_card`,
`_MONEY_DISPLAY`, `_SHARED_DISPLAY`, `_report_status_from_payload`.

**Kept:** the `create_report` tool name and its `POST /api/reports/jobs` endpoint, re-pointed at
the new pipeline with a seeded `portfolio-snapshot` template reproducing today's content.

**Why the name must survive:** `create_report` is a **graded prohibition
(`tool_not_called`) in three of the four golden workflows** — `risk-manager-control-day`
(step 9), `high-board-portfolio-review-day` (steps 5 and 8, and the step-6 governance trap is
built around it), and `risk-limit-breach-day` (step 7). Deleting the tool makes all four checks
trivially always-pass: exactly the *"check at N/N carries zero ability signal while still
occupying the denominator"* defect the Run #58 scoring-validity audit documented. It would
inflate scores on three workflows and break comparability with 11 boards of arena history. The
high-board trap in particular derives its discriminating power from `create_report` being
available and tempting.

The prohibition's *meaning* is unchanged by the re-point: "do not mint a persisted governed
record when asked for a thread artifact" is true regardless of what sits underneath.

**Regression guard:** a test asserts `create_report` remains in `QUANT_AGENT_TOOLS` and
`DEEP_AGENT_TOOL_NAMES`, referencing this decision, so a future cleanup cannot silently remove
it.

### 5.8 Agent surface

| tool | capability group | HITL |
|---|---|---|
| `list_report_blocks()` | `DOMAIN_READ` | — |
| `list_report_templates()` | `DOMAIN_READ` | — |
| `get_report_template(slug)` | `DOMAIN_READ` | — |
| `resolve_report_block(key, params)` | `DOMAIN_READ` | — |
| `save_report_template(slug, spec_yaml)` | `DOMAIN_WRITE` | `"write"` |
| `generate_report(template_slug, portfolio_id, compare_to?)` | `DOMAIN_WRITE` | none |

- **`list_report_blocks` is the keystone.** An agent cannot author against a registry it cannot
  enumerate. Returns key, title, output shape, required params, description.
- **`save_report_template` is `"write"`, deliberately.** `CLAUDE.md` requires justifying the
  level: `"write"` means "interactive only" — AUTO mode strips it. A template edit is
  reversible and audited, and past reports are immune because they embed their own spec, so an
  unattended template edit is tolerable in a way an unattended booking is not.
- **`generate_report` is write-class but not HITL** — same posture as
  `parse_trade_confirmation`, which writes only draft rows.

**Registration checklist** (per `CLAUDE.md`, all sites): `QUANT_AGENT_TOOLS`
(`tools/__init__.py`) → `DEEP_AGENT_TOOL_NAMES` (`services/agents.py`) → for the HITL tool, all
three `hitl.py` structures (`INTERRUPT_TOOL_NAMES`, `_RISK_LEVEL_BY_TOOL`, `_LABEL_BY_TOOL`) →
a `_SUMMARY_BUILDERS` entry → the exact-set pins in `test_capability_assignments.py` and
`test_hitl.py`.

### 5.9 Discoverability

`CLAUDE.md` records this lesson twice — `assemble_breach_report` (registered but not in
`DEEP_AGENT_TOOL_NAMES`, so never called) and the Run #58 audit (skills with no `routing:`
line: 0–23% routing vs. 64–88% with). Both conclude *"suspect availability before capability."*

1. **`PERSONA_WORKFLOW_DOMAINS["trader"]` does not include `reporting`.** Trader has
   positions, products, try-solve, pricing, hedging, market-data, portfolios, rfq, snowballs,
   desk-workflows. A trader daily-report template would be **unroutable by the trader persona**.
   Adding `reporting` to trader's tuple is required work, not a nicety.
2. New skills ship **with** a `routing:` block:
   - `skills/workflows/reporting/generate-templated-report/`
   - `skills/workflows/reporting/author-report-template/`
3. Adding these SKILL.md files breaks exact-set assertions across the catalog test files —
   enumerate with `grep -rln "book-position" tests/` before starting.

### 5.10 Renderer

| `render` | Component | Block shape |
|---|---|---|
| `metric_row` | **existing** `MetricRow` / `Tile` | `SCALARS` |
| `delta_metric_row` | **existing** `MetricRow` + its currently-unused `delta` slot | `SCALARS_WITH_PRIOR` |
| `table` | **existing** `Table` | `ROWS` |
| `bar_chart` / `line_chart` | **existing** `ChartAsset` (recharts) | `SERIES` |
| `callout` | **existing** `Panel` + `Badge` | `ITEMS` |
| `greeks_table` | **existing** (renamed) `GreeksByPosition` | `POSITION_GREEKS` |
| `waterfall` | 🆕 `Waterfall` — recharts stacked bar | `WATERFALL` |
| *any `status != "ok"`* | `Empty` (for `empty`) / muted reason panel (for `unavailable`) | any |

`Waterfall` is the only genuinely new component. `Tile` already accepts
`{ label, value, variant, delta }` — the day-over-day affordance exists and nothing currently
passes it. `ChartAsset` takes `{chart_type, x_key, y_key, series}`, which maps directly onto a
`SERIES` block.

**Rename:** `PnlAttribution.tsx` renders a *Greeks table* by position/underlying
(`delta_cash`, `gamma_cash`, `vega`, `theta`, `rho`, `rho_q`) — there is no P&L decomposition
in it. Building a real `pnl.explain` waterfall beside a component already named
`PnlAttribution` is a trap for future agents. Rename to `GreeksByPosition` as part of this
work (co-located `.css` and test move with it).

**Styling:** token-only, per `frontend/CLAUDE.md`. Verify in both themes and compact density
before claiming done.

### 5.11 Reports page

`MasterDetailPage` — left rail lists reports grouped by day (filter: persona / template /
portfolio); right pane renders the `ReportDocument`.

- **Header** — template, portfolio, `valuation_as_of`, compare-to run, coverage chip, status
- **Sections** — blocks rendered natively, narrative beneath
- **Provenance drawer** — `risk_run_id`, `position_set_hash`, `market_evidence_hash`,
  template spec sha256
- **Actions** — Regenerate · Export (HTML / XLSX / PDF) · Open template
- **Tabs** — **Reports** | **Templates**; the Templates tab is a list + YAML editor with live
  validation against `POST /api/reports/templates/validate` (the Skills page's lint-before-save
  UX), plus a Builder chat pane modelled on `WorkflowBuilderChat`

Export re-renders from the same `ReportDocument`, so file and screen cannot disagree — which is
exactly where today's hardcoded `_write_html` and the page diverged.

---

## 6. The three seeded templates

Seeded by migration with `source='seed'`, like the dynamic-subagents seed workflow.

### 6.1 `trader-daily` — Trader · Daily Book Review

| § | Blocks | Render |
|---|---|---|
| Overnight | `pnl.daily`, `risk.totals` | delta_metric_row, metric_row |
| What moved it | `pnl.explain` | waterfall |
| Position movers | `pnl.by_position` (top N by \|ΔMV\|) | table |
| Exposure | `risk.exposure_by_underlying` | bar_chart |
| Barrier & KO watch | `positions.barrier_proximity` | callout |
| RFQ pipeline | `rfq.open_pipeline` | table |
| Book changes | `positions.changes` | table |

### 6.2 `risk-manager-daily` — Risk · Daily Limit & Exposure Review

| § | Blocks | Render |
|---|---|---|
| Limit status | `limits.utilization`, `limits.breaches` | delta_metric_row, callout |
| Incidents | `limits.incidents` | table |
| Concentration | `risk.exposure_by_underlying`, `risk.greeks_by_bucket` | bar_chart, greeks_table |
| Change since last run | `risk.exposure_diff` | delta_metric_row |
| Evidence quality | `coverage.evidence` | metric_row |
| Stress | `scenario.latest_grid` | table → **`unavailable` today** |

The stress section is deliberate. `scenario_test_runs` has zero rows, so this template ships
with a section that honestly renders *"not available: no scenario runs exist"* — the tri-state
proven in the shipped product, not only in a test.

### 6.3 `high-board-daily` — Board · Daily One-Pager

Inverts the ratio: almost all deterministic data, **one** narrative section, so it generates
with a single LLM call.

| § | Blocks | Render | Narrative |
|---|---|---|---|
| Position of the desk | `risk.totals`, `pnl.daily` | metric_row | null |
| Limit exceptions | `limits.breaches`, `limits.incidents` | callout | null |
| Agent actions taken | `audit.write_actions_summary` | table | null |
| Evidence completeness | `coverage.evidence` | metric_row | null |
| Executive summary | *(no blocks)* | prose | ✓ |

### 6.4 `portfolio-snapshot` (compatibility)

Reproduces the legacy `create_report` output — totals, by-currency, positions table — so the
retained `create_report` tool has a template to route through. `narrative: null` throughout:
byte-comparable in spirit with today's output and zero LLM cost.

---

## 7. Migration

| # | Change |
|---|---|
| 1 | `report_templates` table |
| 2 | `report_jobs`: add nullable `template_slug`, `compare_to_run_id` |
| 3 | Seed the four templates (`source='seed'`) |

Alembic is the upgrade path. Migrations use migration-local Core tables, never ORM models.

---

## 8. Testing

**Sub-project A**

- `snapshot_diff`: membership changes, `composition_changed`, `elapsed_days` from
  `valuation_as_of` (not `created_at`).
- `explain`: single-bump reconciliation per Greek — bump one input, assert that bucket carries
  the whole move and residual ≈ 0. Excluded-position accounting.
- `entry_price`: basis-missing positions excluded from inception total, counted separately.

**Sub-project B**

- Registry: every declared block resolves; every block declares a valid shape.
- Template validation: unknown block key → 422 naming it; shape/renderer mismatch → 422;
  malformed YAML → 422; live row byte-unchanged on rejection.
- Tri-state: a block returning `empty` and one returning `unavailable` render differently and
  reach the agent with distinguishable briefs.
- Grounding guard: a narrative containing a number absent from its section's blocks is flagged.
- Generation: a template with all `narrative: null` completes with **zero** LLM calls.
- Round-trip: `ReportDocument` → export → same numbers.

**Regression**

- `create_report` still registered in both allowlists (guards §5.7).
- Golden replay still earns 39/39 and 38/38; exact-count pins in `test_flagship_loads`,
  `test_arena_scoring`, `test_golden_workflow_regression` unchanged.
- Catalog exact-set tests updated for the two new SKILL.md files.
- Frontend: token-only, both themes, compact density. Note the vitest suite is **flaky under
  load** — compare failing-file sets against a same-machine `main` run before blaming a branch.

---

## 9. Open decisions

Two require desk judgment and are deliberately left for the implementation phase:

1. **Residual threshold for `pnl.explain`.** At what `|residual| / |ΔMV|` does the report stop
   presenting the attribution as an explanation? Placeholder in the sketch: 10%. This becomes a
   constant in `explain.py` and a value in the trader template's narrative brief.
2. **Grounding-guard action.** When a narrative contains an ungrounded number: strip it, flag
   it inline, or reject and retry the section. Each is defensible; the third costs latency.
   Placeholder: flag inline, surface in `section.grounding.flags`.

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| Attribution residual is large on real books, making the waterfall look broken | Single-bump tests prove the maths; residual is **rendered**, not hidden. A large residual is information, not a bug |
| Agent authors a template that validates but reads badly | Validation guarantees resolvability, not taste. The seeded three are the quality bar; the Templates tab shows a live preview |
| Two `result_payload` shapes confuse the reader | Discriminated by `template_slug is null`; only four legacy rows exist |
| Catalog exact-set tests break in unexpected files | Enumerate up front with `grep -rln "book-position" tests/` |
| QuantArk version drift moves numbers | `quantark==0.3.0` is pinned exactly. Never install editable |

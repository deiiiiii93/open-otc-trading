# Agent Skills Layer v2 — Procedures + Routing — Design

**Date:** 2026-05-15
**Status:** Approved (brainstorming complete; pending writing-plans)
**Scope:** Extend the v1 agent skills layer with two new tiers (domain skills + routing skills), refactor procedure skills as workflow-scope, backfill six workflow procedures covering the four candidate procedures from v1 §8 plus a first `high_board` procedure, and add two read-only report-query tools. Ships as one PR with phased internal commits. Behavior-preserving for the v1 anchor (`snowball-position-diagnostics`, `snowball-cn`) through phase 7; phase 8 activates the new orchestrator routing.

**Predecessors:**
- [v1 spec — Agent Skills Layer](2026-05-14-agent-skills-layer-design.md) — establishes the underlying infrastructure
- [v1 plan](../plans/2026-05-14-agent-skills-layer.md)

---

## Decision summary

Nine forks closed during brainstorming:

1. **Next slice** — Procedure backfill batch over the four candidate workflows from v1 §8 (`rfq-intake-and-quote`, `portfolio-pricing-run`, `risk-report-workflow`, `market-data-profile`) plus a first `high_board` procedure. Selected over routing-first because procedures gate routing's value.
2. **Persona split** — One cross-persona case (`portfolio-pricing-run` trader + risk_manager variants); three single-persona (`rfq-intake-and-quote` → trader, `risk-report-workflow` → risk_manager, `market-data-profile` → trader). Plus `report-query-and-display` → high_board.
3. **Card scope** — Procedures only. New product cards (phoenix-cn, accumulator-cn, etc.) are deferred to a separate content-only batch.
4. **Tooling scope** — All workflow procedures use existing tools EXCEPT report-query, which adds two narrowly-scoped read-only tools (`list_reports`, `get_report`). No tool refactoring beyond this minimum.
5. **Atomicity model** — Workflow procedures are *compositions* over domain skills. Domain skills exist in two flavors: cards (reference, free-form) and recipes (single safe operation, 5-section schema). Routing skills compose procedures across personas; orchestrator-only.
6. **Domain card flavors** — Both cards and recipes coexist. Choice per skill based on whether the content is "what is this domain" (card) or "how to do one safe operation" (recipe).
7. **Routing inclusion** — Routing skills land in THIS batch alongside procedures. v1 §8's "≥3 procedures with clear duplication" gate is satisfied by the six new procedures plus the two retained anchor procedures.
8. **Snowball anchor retreatment** — The v1 anchor (`snowball-position-diagnostics`, `snowball-cn`) is grandfathered in its self-contained shape. A new routing skill (`snowball-book-audit`) formalizes its compound flow, replacing v1's prompt-only handling.
9. **Rollout** — One PR (`feat/agent-skills-layer-v2`), ~30-35 internal commits (larger than v1's 25 due to skill volume), behavior-preserving up through phase 7; phase 8 activates new orchestrator routing.

---

## 1. Architecture overview

**Change in one sentence.** Extend the v1 skills layer with two new tiers (domain skills + routing skills) and refactor procedure skills from "self-contained workflows" into "workflow-scope orchestrators that name domain skills by reference."

### Six tiers

| Tier | Mechanism | In context | Authored where | Used by |
|---|---|---|---|---|
| **Policy fragments** (v1) | Concatenated into system prompt at build | Always | `skills/policy/*.md` | Per-persona allowlist in `personas.py` |
| **Domain cards** (new) | `SkillsMiddleware` catalog, body on-demand | Catalog only | `skills/domains/<domain>/<card>/SKILL.md` | Personas — read for domain conventions |
| **Domain recipes** (new) | `SkillsMiddleware` catalog, body on-demand | Catalog only | `skills/domains/<domain>/<recipe>/SKILL.md` | Personas — read for one safe operation |
| **Workflow procedures** (v1, refactored) | `SkillsMiddleware` catalog, body on-demand | Catalog only | `skills/procedures/<persona>/<workflow>/SKILL.md` | Personas — workflow-scope; *names* domain skills in step sequence |
| **Product cards** (v1) | `SkillsMiddleware` catalog, body on-demand | Catalog only | `skills/products/<product-id>/SKILL.md` | Personas — read for product quirks |
| **Routing skills** (new) | `SkillsMiddleware` catalog, body on-demand | Catalog only | `skills/routing/<flow>/SKILL.md` | **Orchestrator only** — cross-persona compound flows |

### What stays unchanged from v1

- `SkillsMiddleware` mechanism, `read_file` discipline, HITL config, checkpointer, channel registry.
- All five policy fragments and their per-persona allowlists.
- `snowball-cn` product card; both `snowball-position-diagnostics` procedure variants.
- The `task` tool and every existing langchain tool.

### What changes

- New `skills/domains/` source with six subdirectories: `position`, `portfolio`, `pricing`, `risk`, `market-data`, `rfq`, `reporting`.
- New `skills/routing/` source consumed only by the orchestrator.
- Procedure skill shape changes for the six new workflow procedures: from self-contained step sequences to *workflow procedures that reference domain skill names* in their step sequence. The two v1 procedure variants stay as-is (anchor pattern; not retrofitted).
- Orchestrator gains `skills=["/skills/routing/"]`. Was empty in v1.
- Each persona's `skills=[...]` list expands to include relevant domain sources.
- Two new langchain tools (`list_reports`, `get_report`) for high_board's report-query workflow.
- One new filesystem mount (`/artifacts`) so `high_board` can `read_file` HTML report artifacts.

### Composition pattern — how workflow procedures use domain skills

A workflow procedure SKILL.md *names* domain skills in its step sequence ("Step 2: apply the `position-staleness-scan` recipe"). The persona may either:

- execute from memory if the catalog description was enough, or
- `read_file` the named domain skill for the full recipe.

Names act as semantic anchors. Composition without forcing nested reads on every step.

### Out of scope

- Backfill of every conceivable atom. The batch ships ~17 domain skills, not 30. Domain skills are added when a procedure needs one or duplication makes one obvious.
- `allowed-tools` hard enforcement — stays a soft hint per v1.
- Retrofitting the snowball anchor into the new shape. Both shapes coexist; future authors pick.
- Reading XLSX artifact bodies. `/artifacts/*.html` is readable; XLSX paths are surfaced as references only.
- Additional product cards (phoenix-cn, etc.) — separate batch.
- Tool-side pagination for `get_latest_position_valuations` — separate engineering ticket.

---

## 2. Directory layout & persona source mapping

### Filesystem tree

```
backend/app/services/deep_agent/skills/
├── policy/                                    # v1, UNCHANGED
│   ├── read-before-compute.md
│   ├── cost-preview.md
│   ├── hitl-batch-size-1.md
│   ├── clarification-protocol.md
│   └── run-python-rfsw.md
├── domains/                                   # NEW
│   ├── position/
│   │   ├── position-snapshot/SKILL.md            # recipe
│   │   └── position-input-enumerate/SKILL.md     # recipe
│   ├── portfolio/
│   │   └── portfolio-model/SKILL.md              # card
│   ├── pricing/
│   │   ├── pricing-engines/SKILL.md              # card
│   │   ├── pricing-run-propose/SKILL.md          # recipe
│   │   └── price-product-adhoc/SKILL.md          # recipe
│   ├── risk/
│   │   ├── risk-snapshot-read/SKILL.md           # recipe
│   │   └── risk-run-propose/SKILL.md             # recipe
│   ├── market-data/
│   │   ├── market-data-conventions/SKILL.md      # card
│   │   ├── market-data-fetch/SKILL.md            # recipe
│   │   └── market-data-drift/SKILL.md            # recipe
│   ├── rfq/
│   │   ├── rfq-lifecycle/SKILL.md                # card
│   │   ├── rfq-draft/SKILL.md                    # recipe
│   │   ├── rfq-quote/SKILL.md                    # recipe
│   │   └── rfq-submit-for-approval/SKILL.md      # recipe
│   └── reporting/
│       ├── report-batch-run/SKILL.md             # recipe
│       └── report-create-propose/SKILL.md        # recipe
├── procedures/                                # v1 structure; new workflows added
│   ├── trader/
│   │   ├── snowball-position-diagnostics/SKILL.md   # v1, UNCHANGED
│   │   ├── rfq-intake-and-quote/SKILL.md            # NEW
│   │   ├── portfolio-pricing-run/SKILL.md           # NEW (trader lens)
│   │   └── market-data-profile/SKILL.md             # NEW
│   ├── risk_manager/
│   │   ├── snowball-position-diagnostics/SKILL.md   # v1, UNCHANGED
│   │   ├── portfolio-pricing-run/SKILL.md           # NEW (risk lens variant)
│   │   └── risk-report-workflow/SKILL.md            # NEW
│   └── high_board/
│       └── report-query-and-display/SKILL.md        # NEW (first high_board skill)
├── products/                                  # v1, UNCHANGED
│   └── snowball-cn/SKILL.md
└── routing/                                   # NEW; orchestrator-only source
    ├── pricing-and-risk-compound/SKILL.md
    ├── snowball-book-audit/SKILL.md
    └── market-data-then-reprice/SKILL.md
```

### Batch deliverable

| Slice | Count |
|---|---|
| Domain cards | 4 (portfolio-model, pricing-engines, market-data-conventions, rfq-lifecycle) |
| Domain recipes | 13 (position ×2, pricing ×2, risk ×2, market-data ×2, rfq ×3, reporting ×2) |
| Workflow procedures | 6 (3 trader + 2 risk_manager + 1 high_board) |
| Routing skills | 3 |
| **New SKILL.md files** | **26** |
| Retained SKILL.md from v1 | 3 (snowball-cn, snowball-position-diagnostics ×2) |
| New langchain tools | 2 (list_reports, get_report) |
| New filesystem mount | 1 (`/artifacts`) |
| Policy fragments | unchanged (5) |

### Per-persona SkillsMiddleware source lists

| Agent | `skills=[...]` (vfs paths) | Catalog size |
|---|---|---|
| **trader** | `/skills/procedures/trader/`, `/skills/domains/position/`, `/skills/domains/pricing/`, `/skills/domains/market-data/`, `/skills/domains/rfq/`, `/skills/products/` | ~17 entries |
| **risk_manager** | `/skills/procedures/risk_manager/`, `/skills/domains/position/`, `/skills/domains/risk/`, `/skills/domains/market-data/`, `/skills/domains/pricing/`, `/skills/domains/reporting/`, `/skills/products/` | ~16 entries |
| **high_board** | `/skills/procedures/high_board/`, `/skills/domains/portfolio/`, `/skills/domains/reporting/` | ~3-4 entries |
| **orchestrator** | `/skills/routing/` | 3 entries |

`risk_manager` includes the `pricing` domain because the risk-lens `portfolio-pricing-run` variant proposes `price_positions` as a risk-input refresh. Cross-domain coupling, not a violation of domain boundaries.

### Frontmatter `metadata.tier` values

Extended from v1's two values to six:

| Value | Where it appears |
|---|---|
| `domain-card` | `domains/*/<card>/SKILL.md` |
| `domain-recipe` | `domains/*/<recipe>/SKILL.md` |
| `procedure` | `procedures/<persona>/<workflow>/SKILL.md` (now: workflow-scope) |
| `product-card` | `products/<product-id>/SKILL.md` (v1) |
| `routing` | `routing/<flow>/SKILL.md` |
| *(no frontmatter)* | `policy/*.md` |

`metadata.tier` is informational only — not enforced. It's a review/audit signal.

### Naming conventions

| Tier | Pattern | Example |
|---|---|---|
| Domain card | `<noun>-<concept>` | `rfq-lifecycle`, `pricing-engines`, `market-data-conventions` |
| Domain recipe | `<noun>-<verb>` or `<verb>-<noun>` | `position-snapshot`, `pricing-run-propose`, `report-batch-run` |
| Workflow procedure | `<noun>-<workflow-noun>` | `rfq-intake-and-quote`, `portfolio-pricing-run`, `risk-report-workflow` |
| Routing | `<flow-noun>` | `pricing-and-risk-compound`, `snowball-book-audit` |

Patterns aren't enforced — they're conventions for scannability. The hard rule (v1) stays: `[a-z0-9-]{1,64}`, no leading/trailing/double hyphens.

### Filesystem backend mounts

```python
backend = FilesystemBackend(mounts={
    "/skills":              str(_SKILLS_FS_ROOT),
    "/trading_desk":        str(_TRADING_DESK_ROOT),
    "/large_tool_results":  str(_LARGE_TOOL_RESULTS_ROOT),
    "/artifacts":           str(_ARTIFACTS_ROOT),            # NEW
})
```

### Filesystem permissions

Add one rule before the trailing deny-all `/**`:

```python
FilesystemPermission(
    operations=["read"],
    paths=["/artifacts", "/artifacts/**"],
    mode="allow",
),
```

V1's `/skills` rule already covers all new sub-trees (`domains/`, `routing/`).

### Per-persona policy allowlist

**Unchanged from v1.** Domain/routing tiers don't affect policy composition.

| Persona | Policy fragments |
|---|---|
| trader | `read-before-compute`, `cost-preview`, `hitl-batch-size-1`, `clarification-protocol`, `run-python-rfsw` |
| risk_manager | same as trader |
| high_board | `cost-preview`, `hitl-batch-size-1`, `clarification-protocol` |

---

## 3. Workflow procedure sketches (6)

### 3.1 `procedures/trader/rfq-intake-and-quote/SKILL.md`

```markdown
---
name: rfq-intake-and-quote
description: End-to-end RFQ intake on the trader side — natural-language draft,
  term validation, pricing, and quote production. Read when the user asks "quote
  this", "draft an RFQ for X", "what would Y cost", or pastes a request to be
  turned into a quotable RFQ. Stops BEFORE submit-for-approval (that's governance).
allowed-tools: draft_rfq_from_natural_language validate_rfq_terms create_or_update_rfq_draft solve_rfq quote_rfq get_rfq_catalog
metadata:
  tier: procedure
  persona: trader
  related_domains: rfq pricing
  related_products: snowball-cn
---

## When this applies
- User pastes an RFQ description in natural language.
- User asks to quote a specific product spec.
- User explicitly names this skill from the orchestrator.

## Inputs to inspect first
1. Read the `rfq-lifecycle` domain card if not loaded this session.
2. If the product type is recognizable, read the matching product card.

## Step sequence
1. Apply the `rfq-draft` domain recipe (draft_rfq_from_natural_language →
   validate_rfq_terms → create_or_update_rfq_draft).
2. Apply the `rfq-quote` domain recipe (solve_rfq → quote_rfq).
3. Report the quoted price, the inputs used, and the draft ID. Do NOT submit.

## What success looks like
"Drafted RFQ <id> for <product>: quote = <price>, model = <engine>, inputs =
<spot/vol/r>. Ready for review; not submitted."

## Tool preferences
- READ-FIRST: `get_rfq_catalog` if product type is ambiguous.
- COMPUTE: `solve_rfq` / `quote_rfq` (no HITL — quote-time, not bookings).
- Do NOT call `submit_rfq_for_approval` from this skill. Surface the draft ID
  and let the user/orchestrator decide.
- Cost-preview discipline: solve_rfq is compute-heavy. Apply the cost-preview
  policy if the spec involves Monte Carlo (snowball, phoenix-MC).
```

### 3.2 `procedures/trader/portfolio-pricing-run/SKILL.md`

```markdown
---
name: portfolio-pricing-run
description: Trader-lens repricing workflow — snapshot the book, identify stale
  or drifted positions, propose a price_positions run with cost preview, and
  verify the result. Read when the user asks "reprice", "refresh prices", "what's
  changed since last pricing", or before any pricing-impact decision.
allowed-tools: get_positions get_latest_position_valuations fetch_market_snapshot price_positions run_python
metadata:
  tier: procedure
  persona: trader
  related_domains: position market-data pricing
---

## When this applies
- User requests repricing of a portfolio.
- User asks about pricing freshness or drift.
- Trader-side prerequisite before any pricing-impact decision.

## Inputs to inspect first
1. Apply the `position-snapshot` domain recipe.
2. Apply the `market-data-fetch` domain recipe for the snapshot's underlyings.

## Step sequence
1. Compute per-position staleness: days-since-last-valuation AND
   spot-drift-vs-stored. Use `run_python` for portfolios with >20 positions
   (RFSW pattern).
2. Flag positions: stale-by-time (>1 BD) OR drifted (>1% spot change).
3. Cost-preview the `price_positions` call: estimated <N> positions ×
   <engine-cost>.
4. Propose `price_positions` with the flagged set as `position_ids` (HITL pause).
5. After approval, verify result by re-reading `get_latest_position_valuations`.

## What success looks like
"<N> positions, <K> flagged stale, <M> flagged drift, cost-preview <X>,
repriced <K+M> positions. New valuations stored; max drift now <Y>."

## Tool preferences
- READ-FIRST: `get_positions`, `get_latest_position_valuations`,
  `fetch_market_snapshot`. No HITL.
- WRITE (HITL): `price_positions` after cost-preview.
- `run_python` for any aggregation across >20 positions.
- Do NOT propose `price_product` from this skill — that's `price-product-adhoc`
  for one-off specs.
```

### 3.3 `procedures/trader/market-data-profile/SKILL.md`

```markdown
---
name: market-data-profile
description: Audit the market-data freshness and coverage backing a portfolio's
  pricing — enumerate unique underlyings, fetch current snapshots, run drift
  analysis vs stored, and flag remediation candidates. Read-only diagnostic.
  Read when the user asks "is our market data fresh", "any drift in inputs",
  "what underlyings need refresh", or before any pricing/risk run.
allowed-tools: get_positions get_latest_position_valuations fetch_market_snapshot run_python
metadata:
  tier: procedure
  persona: trader
  related_domains: position market-data
---

## When this applies
- User asks about market-data quality, freshness, or coverage.
- Pre-step before user-initiated pricing or risk runs (offer this proactively
  if the user mentions a stale book).

## Inputs to inspect first
1. Read the `market-data-conventions` domain card if not loaded this session.
2. Apply the `position-input-enumerate` domain recipe to list unique
   (underlying, input_type) pairs from current positions.

## Step sequence
1. For each unique underlying, apply the `market-data-fetch` domain recipe.
2. Apply the `market-data-drift` domain recipe to compare current vs stored.
3. Build a per-underlying flag table: stale (>1 BD), drifted (>1% spot OR
   >5% vol), missing (no snapshot returned).
4. Group flags by remediation type. Do NOT remediate — surface candidates only.

## What success looks like
"Profiled <N> underlyings: <S> stale, <D> drifted, <M> missing. Remediation
candidates by type: <list>. Recommend import_position_market_inputs for <X> /
fetch_market_snapshot refresh for <Y>."

## Tool preferences
- READ-ONLY. No HITL.
- `run_python` for drift aggregation across >10 underlyings.
- Do NOT propose `import_position_market_inputs` from this skill (it's
  HITL-gated governance write — surface candidates only).
```

### 3.4 `procedures/risk_manager/portfolio-pricing-run/SKILL.md`

Cross-persona variant: same catalog name as 3.2, different body. Each persona's `SkillsMiddleware` sees only its own source dir, so catalogs never collide.

```markdown
---
name: portfolio-pricing-run
description: Risk-lens repricing workflow — confirm pricing inputs are fresh
  enough to underpin a risk run, propose price_positions only when staleness
  threatens risk-input integrity, then handoff to the risk-report-workflow.
  Read BEFORE any run_risk if you suspect prices feeding the risk run are stale.
allowed-tools: get_positions get_latest_position_valuations fetch_market_snapshot price_positions run_python
metadata:
  tier: procedure
  persona: risk_manager
  related_domains: position market-data pricing risk
  related_skills: risk-report-workflow
---

## When this applies
- Pre-step before `run_risk` when valuations look stale.
- Risk-side request to "make sure prices are current before the risk run."
- Compound flow handoff from trader (`portfolio-pricing-run` trader lens completed).

## Inputs to inspect first
1. Apply the `position-snapshot` domain recipe.
2. Read `get_latest_risk_run` — establishes WHICH valuations the most recent
   risk run consumed (the relevant staleness reference, not "today").
3. Apply the `market-data-fetch` domain recipe for any underlying whose
   stored input is older than the last risk run.

## Step sequence
1. For each position, compute: `(now - valuation_date) BD` AND
   `(now - last_risk_run.valuation_date) BD`. The relevant question for risk
   is "do these prices reflect the same regime as the risk run?"
2. Flag positions where stored valuation_date < last_risk_run.valuation_date
   OR where spot has drifted >2% (tighter threshold than trader lens; risk is
   gamma-sensitive).
3. If NO positions flagged: report "pricing inputs current vs latest risk run;
   no repricing needed." Stop. Risk run can proceed.
4. If positions flagged: cost-preview `price_positions`, propose run, HITL pause.
5. After approval: confirm completion via `get_latest_position_valuations`,
   then explicitly handoff to `risk-report-workflow` (or signal the orchestrator
   that risk run is ready to proceed).

## What success looks like
"<N> positions, <K> needed refresh vs last risk run, repriced <K>; pricing
inputs now consistent with valuation_date <D>. Risk run can proceed."

## Tool preferences
- READ-FIRST: `get_positions`, `get_latest_position_valuations`,
  `get_latest_risk_run`, `fetch_market_snapshot`. No HITL.
- WRITE (HITL): `price_positions` after cost-preview.
- `run_python` for aggregation across >20 positions.
- Do NOT propose `run_risk` from this skill — that's `risk-report-workflow`'s job.
```

### 3.5 `procedures/risk_manager/risk-report-workflow/SKILL.md`

```markdown
---
name: risk-report-workflow
description: End-to-end risk reporting workflow — verify risk run currency,
  propose run_risk if stale, generate the report batch, and propose create_report.
  Read when the user asks for a risk report, a portfolio risk summary, or any
  governance-grade risk artifact for a portfolio.
allowed-tools: get_positions get_latest_risk_run calculate_risk run_risk run_report_batch create_report recommend_hedge run_python
metadata:
  tier: procedure
  persona: risk_manager
  related_domains: position risk reporting
  related_skills: portfolio-pricing-run
---

## When this applies
- User requests a risk report or portfolio risk summary.
- Governance ask: "what's our exposure on portfolio X."
- Compound flow handoff after `portfolio-pricing-run` (risk lens) completes.

## Inputs to inspect first
1. Apply the `position-snapshot` domain recipe.
2. Apply the `risk-snapshot-read` domain recipe (wraps `get_latest_risk_run`)
   to see the most recent persisted risk run + its valuation_date.
3. If pricing-run-propose has not been run this session AND latest risk run is
   stale (>1 BD), recommend the orchestrator route through `portfolio-pricing-run`
   (risk lens) first. STOP this skill; do not silently reprice.

## Step sequence
1. Determine risk-run currency: compare `last_risk_run.valuation_date` to today
   AND to position last-modified timestamps.
2. If stale (>1 BD OR positions changed since last run): apply the
   `risk-run-propose` domain recipe (cost-preview + run_risk HITL).
3. Apply the `report-batch-run` domain recipe to produce the inline summary
   payload (read-mostly; no persistence yet).
4. Inspect the inline summary. If risk metrics breach desk limits, call
   `recommend_hedge` and include the recommendation in the report draft.
5. Apply the `report-create-propose` domain recipe (cost-preview + create_report
   HITL) to persist the report.
6. Report report_job_id, task_id, status, and a one-paragraph executive summary.

## What success looks like
"Risk report queued: report_job_id=<X>, task_id=<Y>, status=<Z>. Portfolio
totals: delta=<D>, gamma=<G>, vega=<V>. <K> positions in gamma-spike zone.
Hedge recommendation: <H> (if any)."

## Tool preferences
- READ-FIRST: `get_positions`, `get_latest_risk_run`, `calculate_risk` (for
  hypothetical hedge sizing). No HITL.
- WRITE (HITL): `run_risk`, `create_report`. Each preceded by its own
  cost-preview per policy.
- `run_python` for >50-position aggregations.
- Do NOT propose `price_positions` from this skill — if pricing is stale,
  bounce back to `portfolio-pricing-run`. Separation of concerns: pricing
  workflow owns valuations; risk workflow owns risk + reports.
```

### 3.6 `procedures/high_board/report-query-and-display/SKILL.md`

First high_board procedure. Uses the two new tools (`list_reports`, `get_report`).

```markdown
---
name: report-query-and-display
description: Governance read-side workflow — locate persisted reports for a
  portfolio, fetch metadata + inline summary, and present an interpretation
  with pointers to artifacts (HTML/XLSX). Read when the user asks "show me
  the latest report", "what reports do we have for X", "what does last week's
  risk report say", or any governance review of historical reports.
allowed-tools: list_reports get_report list_portfolios get_portfolio
metadata:
  tier: procedure
  persona: high_board
  related_domains: portfolio reporting
---

## When this applies
- User asks to review or quote from a persisted report.
- Governance check: "what does the most recent risk/portfolio/rfq report say
  about portfolio X."
- Pre-decision review before approving a quote or release.

## Inputs to inspect first
1. Read the `portfolio-model` domain card if not loaded this session — needed
   to disambiguate Container vs View when the user names a portfolio by label.
2. Clarify `portfolio_id` per the Clarification Protocol policy if ambiguous:
   if the user gave a name, call `list_portfolios` and confirm the resolved ID
   before proceeding. Do NOT guess.

## Step sequence
1. Call `list_reports(portfolio_id=<resolved_id>, report_type=<user-requested
   type or omitted>)`. Inspect the returned ReportJobs: surface only
   `status = completed` rows unless the user explicitly asked about
   pending/failed jobs.
2. Pick the target report(s):
   - If "the latest", take the highest `created_at`.
   - If a date range was named, filter in-context (no extra tool calls).
   - If multiple candidates remain ambiguous, ask the user to pick (do NOT
     auto-select).
3. For each selected report, call `get_report(report_id=<id>)`. Read its
   `summary` field, `status`, and `artifact_paths`.
4. Compose a structured display:
   - One-paragraph interpretation of the summary, plain-language, citing
     concrete numbers from the summary payload.
   - A small table: report metadata (id, type, title, created_at, status).
   - Artifact references: HTML path, XLSX path.
   - For each HTML artifact, `read_file(html_path, limit=2000)` and quote
     2-3 relevant excerpts. Cap quoted content at ~300 words; if the report
     is larger, summarize the structure ("sections: …") and let the user
     request a deep-read of one section.
   - For XLSX artifacts, surface the path only — binary file, not read.
5. If the user requested an interpretation the summary can't answer (e.g.,
   position-level detail not in the summary), say so explicitly and propose
   the next step: route to risk_manager's `risk-report-workflow` for a fresh,
   deeper-grained report. Do NOT call `create_report` from this skill.

## What success looks like
"Reviewed report <id> (<type>, <title>, created <date>, status <status>).
Summary: <one-paragraph interpretation citing concrete metrics>. Artifacts:
<html_path>, <xlsx_path>. Anomalies flagged: <list or 'none'>. Next-step
recommendations: <list or 'no follow-up needed'>."

## Tool preferences
- READ-ONLY. No HITL. No cost-preview required (read-only tools).
- `list_portfolios` / `get_portfolio` ONLY for portfolio disambiguation.
- `read_file` on `/artifacts/*.html` ALLOWED — use for inline quoting.
- `read_file` on `/artifacts/*.xlsx` DISALLOWED — binary, treat as reference.
- Do NOT call `run_report_batch`, `create_report`, `run_risk`, or any write
  tool from this skill. If a fresh report is needed, bounce back: "Existing
  reports don't cover <X>; recommend running `risk-report-workflow` on
  persona=risk_manager."
```

---

## 4. Domain skill sketches

### 4.1 Cards (4)

Cards encode domain-level reference content. Free-form body. Read once per session per relevant workflow. No fixed schema.

#### `domains/portfolio/portfolio-model/SKILL.md`

```markdown
---
name: portfolio-model
description: Foundational reference for the portfolio data model on this desk —
  distinguishes Container vs View portfolios, explains how positions resolve
  to portfolios via sources, and documents the query patterns (list_portfolios
  / get_portfolio / get_positions). Read once at the start of any
  portfolio-touching workflow.
metadata:
  tier: domain-card
  related_tools: list_portfolios get_portfolio get_positions set_portfolio_rule
---

## Container vs View
- **Container**: portfolio that *holds* positions explicitly; mutated via
  add_positions_to_portfolio / remove_positions_from_portfolio.
- **View**: portfolio defined by *rules* (source filters); membership is
  derived, not stored. Mutated via set_portfolio_rule / add_portfolio_sources.
- Lifecycle differences: Containers persist position links; Views recompute
  on read. Implications for staleness and consistency.

## Portfolio ↔ Position relationship
- Positions are owned by a portfolio_id (Container) or matched via source
  rules (View).
- A position can appear in multiple Views simultaneously; in only one
  Container at a time.
- get_positions(portfolio_id=...) resolves either kind transparently.

## How to query a portfolio
- Enumerate: list_portfolios (paginated; check for total > returned).
- Inspect: get_portfolio(portfolio_id) returns metadata + kind (container/view)
  + sources/rules if View.
- Positions inside: get_positions(portfolio_id, ...filters...).
- Common gotcha: an empty View is valid (rule matched zero positions); an
  empty Container often signals stale state. Treat differently.

## See also
- Recipe: position-snapshot
```

#### `domains/pricing/pricing-engines/SKILL.md`

```markdown
---
name: pricing-engines
description: Reference for the QuantArk pricing engines available on this desk
  and how product type maps to engine choice. Read before any pricing-related
  decision so the engine selection and input requirements are explicit.
metadata:
  tier: domain-card
  related_tools: price_product price_positions
  related_products: snowball-cn
---

## Engines available
- **Black-Scholes** (analytic): EuropeanVanillaOption. Closed-form, fast.
- **Monte Carlo (daily-grid)**: SnowballOption. Daily KI observation +
  monthly KO grid. Path-dependent, expensive.
- **Monte Carlo (event-driven)**: PhoenixOption. Coupon-on-observation,
  KI/KO events. Path-dependent.

## Product type → engine map
| product_type | Engine | Cost class |
|---|---|---|
| EuropeanVanillaOption | Black-Scholes | cheap |
| SnowballOption | MC daily-grid | expensive |
| PhoenixOption | MC event-driven | medium |

## Required inputs per engine
- BS: spot, vol (flat or ATM), r, q, T.
- MC daily-grid: spot, vol surface or flat ATM, r, q, dividend schedule,
  KI/KO levels, observation calendar.
- MC event-driven: same as above + per-event coupon definition.

## When to cost-preview before running
- `price_positions` over a snowball/phoenix book: ALWAYS cost-preview.
- `price_positions` over a vanilla-only book: cost-preview optional.
- `price_product` for a single MC spec: cost-preview if simulation count
  is unbounded or > 10k paths.

## See also
- Recipe: pricing-run-propose
- Recipe: price-product-adhoc
- Product card: snowball-cn
```

#### `domains/market-data/market-data-conventions/SKILL.md`

```markdown
---
name: market-data-conventions
description: Reference for market-data sources, refresh cadence, symbol
  conventions, and what counts as stale or drifted on this desk. Read before
  any market-data fetch or drift analysis.
metadata:
  tier: domain-card
  related_tools: fetch_market_snapshot import_position_market_inputs
---

## Sources
- **A-share (CN)**: akshare for index spot, sector spot, single-name spot;
  historical vol from rolling realized.
- **HK**: akshare HK feed; less frequent refresh.
- **OTC / proprietary**: stored vol surfaces and dividend curves live in
  the desk's pricing-profile store (not market-data per se).

## Refresh cadence
- Intraday spot: snapshot is "as of last fetch". No streaming; explicit fetch
  required.
- Day-end EOD: A-share market closes 15:00 CST; data settles ~15:30 CST.
- Vol surfaces: refreshed weekly unless explicitly requested.

## Symbol conventions
- Indices: 000300.SH (CSI 300), 000905.SH (CSI 500), 000852.SH (CSI 1000).
- Single names: <code>.SH for Shanghai, <code>.SZ for Shenzhen.
- HK indices: HSI, HSCEI (no exchange suffix).

## Staleness thresholds (desk default)
- Spot stale: last fetch > 1 BD ago.
- Vol stale: last fetch > 5 BD ago.
- Drift (spot): |current − stored| / stored > 1% (trader lens), > 2% (risk lens).

## Day-count / settlement
- A-share equities: T+1 settlement, ACT/365 day-count.
- OTC structured products: per-contract day-count; check product card.

## See also
- Recipe: market-data-fetch
- Recipe: market-data-drift
```

#### `domains/rfq/rfq-lifecycle/SKILL.md`

```markdown
---
name: rfq-lifecycle
description: RFQ state machine reference — states, transitions, HITL gates,
  audit events. Read once at the start of any RFQ-touching workflow so the
  transitions are explicit. The state machine is enforced by the tool surface,
  not by skill content.
metadata:
  tier: domain-card
  related_tools: draft_rfq_from_natural_language validate_rfq_terms create_or_update_rfq_draft solve_rfq quote_rfq submit_rfq_for_approval approve_rfq reject_rfq release_rfq mark_rfq_client_accepted book_rfq_to_position
---

## States
draft → quoted → submitted_for_approval → (approved | rejected)
→ released → client_accepted → booked

## Transitions and tools
| From | To | Tool | HITL? |
|---|---|---|---|
| (none) | draft | create_or_update_rfq_draft | no |
| draft | draft (updated) | create_or_update_rfq_draft | no |
| draft | quoted | quote_rfq | no |
| quoted | submitted_for_approval | submit_rfq_for_approval | YES |
| submitted | approved | approve_rfq | YES (high_board) |
| submitted | rejected | reject_rfq | YES (high_board) |
| approved | released | release_rfq | YES |
| released | client_accepted | mark_rfq_client_accepted | YES |
| client_accepted | booked | book_rfq_to_position | YES |

## Persona ownership
- **trader**: draft, validate, quote. Owns up through submit_for_approval.
- **high_board**: approve / reject. Governance gate.
- **trader**: release, mark_client_accepted, book_to_position. Post-approval
  execution.

## Audit
Every transition emits an audit event with actor + timestamp + diff. Skills
do not need to handle auditing — the tools do it. Skills SHOULD reference
the audit event type when reporting ("approved RFQ <id>, audit event
rfq.approved").

## Compute-cost note
- draft_rfq_from_natural_language: small LLM call (server-side); cheap.
- solve_rfq: invokes pricing engine; cost class follows pricing-engines card.
- All transition tools: cheap (DB writes).

## See also
- Recipe: rfq-draft
- Recipe: rfq-quote
- Recipe: rfq-submit-for-approval
- Procedure: rfq-intake-and-quote
```

### 4.2 Recipes (13)

Each recipe follows the v1 5-section schema.

#### `domains/position/position-snapshot/SKILL.md`

```markdown
---
name: position-snapshot
description: Build a canonical position snapshot for a portfolio — combines
  positions metadata with the latest stored valuations into a single in-context
  view. Pure read. Read before any pricing, risk, or diagnostics workflow.
allowed-tools: get_positions get_latest_position_valuations run_python
metadata:
  tier: domain-recipe
  related_tools: get_positions get_latest_position_valuations
---

## When this applies
- Pre-step for any workflow that needs a position view.

## Inputs to inspect first
- `portfolio_id` from the caller.

## Step sequence
1. `get_positions(portfolio_id)`.
2. `get_latest_position_valuations(portfolio_id)` (note: 500-row limit; if
   positions > 500, use `run_python` reduce per v1 large-portfolio pattern).
3. Join on `position.id` ↔ `valuation.position_id` (NOT `valuation.id`).

## What success looks like
A combined view: `<N> positions, <K> with stored valuations, <M> missing
valuations`.

## Tool preferences
- READ-ONLY. No HITL.
- For portfolios > 500 positions, MUST use `run_python` reduce (v1 commit c0ae172).
```

#### `domains/position/position-input-enumerate/SKILL.md`

```markdown
---
name: position-input-enumerate
description: From a position snapshot, derive the unique set of market-data
  inputs the portfolio depends on (underlying × input_type pairs). Pure read
  + run_python. Read before market-data fetch/drift workflows.
allowed-tools: run_python
metadata:
  tier: domain-recipe
  related_tools: run_python
---

## When this applies
- Pre-step for market-data-fetch over a portfolio's full underlying set.
- Coverage audit ("what inputs do we need to keep fresh?").

## Inputs to inspect first
- A position snapshot built via `position-snapshot`.

## Step sequence
1. `run_python` script over the snapshot: extract `(underlying, input_type)`
   tuples from each position's spec. input_type ∈
   {spot, vol, r, q, dividend_schedule}.
2. Dedupe and return the unique set with a count of positions per pair (gives
   a "blast radius if this input drifts" metric).

## What success looks like
"<N> unique (underlying, input_type) pairs across <P> positions. Top by
blast-radius: <pair>: <count>; ..."

## Tool preferences
- READ-ONLY. `run_python` only.
```

#### `domains/pricing/pricing-run-propose/SKILL.md`

```markdown
---
name: pricing-run-propose
description: Cost-preview then propose price_positions for a portfolio or subset.
  HITL-gated write. Use ONLY when staleness/drift analysis has flagged a real
  refresh need — never as a default first action.
allowed-tools: price_positions
metadata:
  tier: domain-recipe
  related_cards: pricing-engines
  related_tools: price_positions
---

## When this applies
- A pricing or risk workflow has identified stale/drifted positions and
  decided a fresh price_positions run is justified.

## Inputs to inspect first
- The flagged position_ids list from the calling workflow.
- The product types of the flagged positions (cost class via pricing-engines).

## Step sequence
1. Compose cost-preview: "<K> positions × <engine-cost class>. Estimated
   runtime: <T>."
2. State the preview to the user. Pause on the user's confirmation (HITL).
3. Call `price_positions(portfolio_id, position_ids=[...])`.
4. Confirm by re-reading `get_latest_position_valuations` for the affected IDs.

## What success looks like
"Repriced <K> positions; new valuation_date = <D>; max_diff_vs_prior = <X>."

## Tool preferences
- WRITE (HITL): `price_positions`. Cost-preview MANDATORY per policy.
- Do NOT call without a flagged position list from upstream — full-portfolio
  blanket repricing is a separate explicit user request.
```

#### `domains/pricing/price-product-adhoc/SKILL.md`

```markdown
---
name: price-product-adhoc
description: Price a single product spec ad-hoc (no portfolio context, no
  persistence). Read pricing-engines card first to pick the right engine and
  cost-preview if MC. Used for "what would X cost" exploratory queries.
allowed-tools: price_product
metadata:
  tier: domain-recipe
  related_cards: pricing-engines
  related_tools: price_product
---

## When this applies
- Exploratory pricing: "what would a 24m snowball on CSI 500 with KI=80,
  KO=103 cost?"
- Pricing inside `rfq-quote` recipe (via `solve_rfq`, not this directly —
  kept as a separate path for ad-hoc queries that aren't RFQs).

## Inputs to inspect first
- The product spec (type, terms, underlying).
- pricing-engines card if engine choice is unclear.

## Step sequence
1. Validate the spec is well-formed enough to price (required terms present).
2. Cost-preview IF engine is MC AND simulation count is > 10k paths.
3. `price_product(product_type, terms, market_inputs)`.

## What success looks like
"Price = <P>, engine = <E>, key inputs used: <spot/vol/r/q>. Sensitivity
caveats: <list>."

## Tool preferences
- Compute. No HITL (not in HITL config). Cost-preview only if MC + high paths.
```

#### `domains/risk/risk-snapshot-read/SKILL.md`

```markdown
---
name: risk-snapshot-read
description: Read and interpret the most recent persisted risk run for a
  portfolio. Pure read. Use before any risk decision or as a freshness check
  for risk-run-propose.
allowed-tools: get_latest_risk_run
metadata:
  tier: domain-recipe
  related_tools: get_latest_risk_run
---

## When this applies
- Pre-step for any risk-related workflow.
- Standalone: "what's our latest risk view on portfolio X?"

## Inputs to inspect first
- `portfolio_id`.

## Step sequence
1. `get_latest_risk_run(portfolio_id)`.
2. Extract: run timestamp, valuation_date, totals (delta/gamma/vega/theta),
   per-position contributions (if returned).
3. Compute currency: BD-since-run, BD-since-valuation_date.

## What success looks like
"Latest risk run: <ts>, valuation_date=<D>, totals: delta=<>, gamma=<>,
vega=<>. <K> positions contributing. Currency: <X> BD stale."

## Tool preferences
- READ-ONLY. No HITL.
```

#### `domains/risk/risk-run-propose/SKILL.md`

```markdown
---
name: risk-run-propose
description: Cost-preview then propose run_risk. HITL-gated write. Use when
  risk-snapshot-read shows stale data or no run exists.
allowed-tools: run_risk
metadata:
  tier: domain-recipe
  related_tools: run_risk
---

## When this applies
- Latest risk run > 1 BD stale OR positions changed since last run.
- Risk-report-workflow upstream check has decided a fresh run is needed.

## Inputs to inspect first
- The portfolio's position count and product-type mix (cost driver).

## Step sequence
1. Compose cost-preview: "<N> positions; <X> snowball/phoenix MC; estimated
   runtime <T>."
2. State preview to user. HITL pause.
3. `run_risk(portfolio_id, method="summary"|"detail")`. Method per caller's
   need.
4. Confirm via `get_latest_risk_run` (new run should appear).

## What success looks like
"Fresh risk run completed: ts=<>, valuation_date=<>, totals delta=<>,
gamma=<>, vega=<>."

## Tool preferences
- WRITE (HITL): `run_risk`. Cost-preview MANDATORY.
```

#### `domains/market-data/market-data-fetch/SKILL.md`

```markdown
---
name: market-data-fetch
description: Fetch current market snapshot for a set of underlyings, respecting
  symbol conventions from market-data-conventions card. Pure read.
allowed-tools: fetch_market_snapshot
metadata:
  tier: domain-recipe
  related_cards: market-data-conventions
  related_tools: fetch_market_snapshot
---

## When this applies
- Any workflow needing current spot / vol / r / q for one or more underlyings.

## Inputs to inspect first
- List of `(underlying, input_type)` pairs (often from
  `position-input-enumerate`).
- market-data-conventions card for symbol formatting.

## Step sequence
1. Normalize each underlying to the canonical symbol per card conventions.
2. Group by input_type; one `fetch_market_snapshot` call per
   (input_type, batch_of_symbols).
3. Surface returns + any per-symbol fetch failures.

## What success looks like
"Fetched <N> underlyings across <M> input_types; <F> failed (list)."

## Tool preferences
- READ-ONLY. No HITL.
- Batch by input_type — do NOT call once per underlying for large sets.
```

#### `domains/market-data/market-data-drift/SKILL.md`

```markdown
---
name: market-data-drift
description: Compute drift between freshly fetched market snapshot and the
  inputs stored against positions. Returns per-input drift magnitude. Read +
  run_python compute.
allowed-tools: run_python
metadata:
  tier: domain-recipe
  related_cards: market-data-conventions
---

## When this applies
- After `market-data-fetch` has produced a current snapshot; need to compare
  vs what positions were last priced with.

## Inputs to inspect first
- The fetched snapshot.
- The position-stored inputs (from `position-snapshot` valuation rows).

## Step sequence
1. `run_python` script: for each (underlying, input_type), compute
   `(current - stored) / stored` and absolute_diff.
2. Apply thresholds from market-data-conventions card (1% trader / 2% risk).
3. Return a sorted drift table with classification
   (within-threshold / drifted / missing).

## What success looks like
"Drift across <N> inputs: <D> drifted (>threshold), <M> missing snapshot,
<W> within tolerance. Top drift: <input>: <pct>."

## Tool preferences
- READ-ONLY + compute. `run_python` only.
```

#### `domains/rfq/rfq-draft/SKILL.md`

```markdown
---
name: rfq-draft
description: From natural-language input, produce a validated RFQ draft row
  ready to quote. Chains draft → validate → persist. Used by
  rfq-intake-and-quote.
allowed-tools: draft_rfq_from_natural_language validate_rfq_terms create_or_update_rfq_draft
metadata:
  tier: domain-recipe
  related_cards: rfq-lifecycle
---

## When this applies
- Trader receives a natural-language RFQ request needing structured persistence.

## Inputs to inspect first
- The user's natural-language description.

## Step sequence
1. `draft_rfq_from_natural_language(text)` → candidate terms.
2. `validate_rfq_terms(terms)` → report violations. If any HARD violations,
   stop and surface to user; do NOT persist a known-invalid draft.
3. `create_or_update_rfq_draft(terms)` → persisted draft_id.

## What success looks like
"Draft RFQ <id> created: product=<>, underlying=<>, notional=<>, key terms=
<...>. Validated."

## Tool preferences
- COMPUTE + WRITE. None HITL-gated (drafts are non-binding).
```

#### `domains/rfq/rfq-quote/SKILL.md`

```markdown
---
name: rfq-quote
description: Solve and quote an existing RFQ draft. Chains solve_rfq (price
  the spec) + quote_rfq (persist the quote). Used by rfq-intake-and-quote.
allowed-tools: solve_rfq quote_rfq
metadata:
  tier: domain-recipe
  related_cards: rfq-lifecycle pricing-engines
---

## When this applies
- A validated RFQ draft exists; trader needs to produce the quote.

## Inputs to inspect first
- The RFQ draft (from `rfq-draft` or user-supplied id).

## Step sequence
1. Cost-preview if the product is MC-priced (snowball/phoenix). State preview.
2. `solve_rfq(draft_id)` → computed price + engine used.
3. `quote_rfq(draft_id, price)` → persists quoted state.

## What success looks like
"Quoted RFQ <id>: price=<>, engine=<>, valuation_date=<>. State: quoted."

## Tool preferences
- COMPUTE. No HITL config on solve/quote tools. Cost-preview for MC per policy.
```

#### `domains/rfq/rfq-submit-for-approval/SKILL.md`

```markdown
---
name: rfq-submit-for-approval
description: Submit a quoted RFQ for high_board approval. HITL-gated. Single
  tool wrapper; kept as a recipe to encode the pre-submit sanity check.
allowed-tools: submit_rfq_for_approval
metadata:
  tier: domain-recipe
  related_cards: rfq-lifecycle
---

## When this applies
- Quoted RFQ is ready for governance review.
- User explicitly asks to "submit" / "send for approval".

## Inputs to inspect first
- The RFQ row (must be in `quoted` state).

## Step sequence
1. Verify RFQ is in `quoted` state (the tool will reject otherwise; better to
   catch up front).
2. Compose HITL summary: RFQ id, terms, quoted price, requested approver.
3. `submit_rfq_for_approval(rfq_id)` — HITL pause.

## What success looks like
"RFQ <id> submitted for approval. Audit event: rfq.submitted. Approver:
high_board."

## Tool preferences
- WRITE (HITL): `submit_rfq_for_approval`.
- Do NOT submit RFQs in any state other than `quoted` — surface the state
  mismatch to the user.
```

#### `domains/reporting/report-batch-run/SKILL.md`

```markdown
---
name: report-batch-run
description: Run a report batch to produce inline summary content (NOT
  persisted). Used by risk-report-workflow as the compute step before
  create_report. Read-mostly compute.
allowed-tools: run_report_batch
metadata:
  tier: domain-recipe
  related_tools: run_report_batch
---

## When this applies
- Inside risk-report-workflow, after run_risk has produced fresh data and
  before create_report is proposed.
- Standalone: "give me a one-shot summary without persisting".

## Inputs to inspect first
- Portfolio snapshot + latest risk run (results of risk-snapshot-read).

## Step sequence
1. Compose the PortfolioSnapshot payload (positions + selected fields).
2. `run_report_batch(title, report_type, portfolio_payload)` — returns inline
   summary + artifact_hint.
3. Inspect the summary's `totals` and `breakdowns`. Surface anomalies.

## What success looks like
"Report batch summary: <metrics>. Artifact hint: <path-template>. Status:
ready (NOT persisted)."

## Tool preferences
- COMPUTE. No HITL config; cost-preview if portfolio is large.
- Result is in-context only — does NOT persist until `report-create-propose`
  runs.
```

#### `domains/reporting/report-create-propose/SKILL.md`

```markdown
---
name: report-create-propose
description: Cost-preview then propose create_report to persist a report job.
  HITL-gated write. Used by risk-report-workflow as the final persistence step.
allowed-tools: create_report
metadata:
  tier: domain-recipe
  related_tools: create_report
---

## When this applies
- Inside risk-report-workflow, after report-batch-run has confirmed the
  payload looks correct.
- User explicitly asks to "save the report" / "persist".

## Inputs to inspect first
- The intended `portfolio_id`, `report_type`, `title`.

## Step sequence
1. Compose cost-preview: report_type, expected artifact set (html, xlsx),
   approximate runtime.
2. State preview. HITL pause.
3. `create_report(portfolio_id, report_type, title)` → returns report_job_id,
   task_id, status.

## What success looks like
"Report queued: report_job_id=<>, task_id=<>, status=<>. Artifacts will land
under /artifacts/<safe_name>.{html,xlsx}."

## Tool preferences
- WRITE (HITL): `create_report`. Cost-preview MANDATORY.
- Surface the task_id for monitoring; do NOT poll status from this recipe.
```

---

## 5. Routing skill sketches (3)

Routing skills are orchestrator-only. They describe compound flows via `task(...)` delegations. The orchestrator has no domain tools — these skills compose persona work.

### 5.1 `routing/pricing-and-risk-compound/SKILL.md`

```markdown
---
name: pricing-and-risk-compound
description: Compound flow when the user wants BOTH pricing health AND risk
  health on the same portfolio. Chains trader's portfolio-pricing-run, then
  risk_manager's portfolio-pricing-run (risk lens), then risk_manager's
  risk-report-workflow. Read when the user asks "give me the full picture",
  "pricing AND risk", "is portfolio X OK across the board", or any compound
  pricing+risk query.
metadata:
  tier: routing
  related_personas: trader risk_manager
  related_procedures: portfolio-pricing-run risk-report-workflow
---

## When this applies
- Compound pricing + risk queries on a single portfolio.
- "Full check" / "comprehensive audit" requests scoped to one portfolio.
- Pre-decision review before a governance ask.

## Step sequence
1. Apply Clarification Protocol: confirm `portfolio_id` if not explicit. Do
   NOT proceed on ambiguous portfolio reference.
2. Delegate to trader with `portfolio-pricing-run` (trader lens):
   - description: "Use `portfolio-pricing-run`. Walk through portfolio_id=<id>
     for pricing health. Flag stale/drifted positions; surface cost-preview if
     you propose repricing."
   - Wait for trader to return findings. If trader proposed repricing AND
     user confirmed via HITL, repricing has already executed before reply.
3. Synthesize trader findings. Extract: position count, flagged set,
   repricing outcome (if any), latest valuation_date.
4. Delegate to risk_manager with `portfolio-pricing-run` (risk lens):
   - description includes trader's flagged set and valuation_date for handoff.
   - description: "Use `portfolio-pricing-run` (risk lens). Trader's pricing
     pass: <summary>. Verify risk inputs are current vs latest risk run;
     propose repricing only if it would change the risk view."
   - Wait for risk_manager reply.
5. Delegate to risk_manager with `risk-report-workflow`:
   - description: "Use `risk-report-workflow`. Pricing inputs verified current
     by prior step (valuation_date=<D>). Produce the risk run if stale, then
     the report."
6. Synthesize combined report. Cite each persona's findings explicitly
   (existing Compound queries policy).

## What success looks like
A combined report:
- Trader findings: pricing freshness, flagged positions, repricing outcome.
- Risk_manager findings: risk-input currency, risk run details, report id.
- Joint observations: positions flagged by both lenses.

## Routing notes
- If the user only wants pricing OR only wants risk, do NOT route this flow —
  delegate directly to the single relevant persona+procedure.
- If trader's pricing pass surfaces no flagged positions, you MAY skip step 4
  (risk-lens repricing) and go straight to step 5 — but state the skip
  explicitly so the user knows.
- HITL pauses inside delegations are normal. If the user rejects a
  cost-preview at any step, surface the rejection and stop the routing flow.
```

### 5.2 `routing/snowball-book-audit/SKILL.md`

Retrofit of v1's prompt-only orchestration of the snowball compound case.

```markdown
---
name: snowball-book-audit
description: Compound flow for Snowball portfolios — chains trader's
  snowball-position-diagnostics (pricing health) with risk_manager's
  snowball-position-diagnostics (risk health). Read when the user asks "is
  the snowball book OK", "full snowball check", or any compound snowball
  query. Retrofits the v1 manual orchestration into a named flow.
metadata:
  tier: routing
  related_personas: trader risk_manager
  related_procedures: snowball-position-diagnostics
  related_products: snowball-cn
---

## When this applies
- Compound Snowball queries spanning pricing + risk lenses on the same
  portfolio.
- "Audit the snowball book" / "snowball health check" requests.

## Step sequence
1. Apply Clarification Protocol on `portfolio_id`.
2. Delegate to trader with `snowball-position-diagnostics`:
   - description: "Use `snowball-position-diagnostics`. Walk through
     portfolio_id=<id> for pricing health: KO/KI distance, autocall proximity,
     stale-input check. Read only — do NOT propose price_positions yet."
3. Synthesize trader response. Extract: positions near KI, positions near KO,
   pricing-run age.
4. Delegate to risk_manager with `snowball-position-diagnostics`:
   - description: "Use `snowball-position-diagnostics`. Walk through
     portfolio_id=<id> for risk health. Trader flagged <K> positions within
     5% of KI: <list>. Read latest risk run; propose run_risk only if stale."
5. Synthesize combined report.

## What success looks like
"Snowball book audit for portfolio <id>:
- Pricing (trader): <N> positions, <K> near KI, <M> near KO, accrual
  <ok|drift>, pricing age <X> BD.
- Risk (risk_manager): vega=<>, delta=<>, gamma=<>; <K> in gamma-spike zone;
  hedge recommendation: <>.
- Joint: positions flagged by both lenses: <list>."

## Routing notes
- Snowball-specific. Do NOT use for non-Snowball portfolios; route through
  `pricing-and-risk-compound` instead.
- Canonical example of "one concept, two persona lenses" routing — the SAME
  skill name in two persona catalogs, composed by this routing skill.
```

### 5.3 `routing/market-data-then-reprice/SKILL.md`

```markdown
---
name: market-data-then-reprice
description: Sequential trader flow — audit market data freshness across a
  portfolio's underlyings, then (only if drift found) propose a repricing run.
  Single persona; routing skill exists to encode the audit→reprice ordering
  and the "skip reprice if no drift" decision.
metadata:
  tier: routing
  related_personas: trader
  related_procedures: market-data-profile portfolio-pricing-run
---

## When this applies
- User asks "refresh inputs and reprice" / "make sure data is current then
  reprice".
- Trader-initiated weekly hygiene scan.

## Step sequence
1. Apply Clarification Protocol on `portfolio_id`.
2. Delegate to trader with `market-data-profile`:
   - description: "Use `market-data-profile`. Audit market-data freshness/
     coverage on portfolio_id=<id>. Surface drift candidates; do NOT remediate."
3. Inspect trader findings. Branch:
   a. **No drift found**: report "Market data current for portfolio <id>;
      no repricing needed." STOP.
   b. **Drift candidates surfaced for inputs requiring import**: surface the
      candidates to the user and pause — `import_position_market_inputs` is
      HITL-gated governance write, not in scope for this routing flow.
   c. **Drift handled by snapshot refresh only**: proceed to step 4.
4. Delegate to trader with `portfolio-pricing-run`:
   - description: "Use `portfolio-pricing-run`. Market-data audit completed;
     drift detected on <list of underlyings>. Reprice positions affected by
     drifted inputs."
5. Synthesize: data audit outcome + repricing outcome.

## What success looks like
- No-drift path: "Audit clean; no repricing needed."
- Drift-then-reprice path: "Audit: <D> drifted inputs. Repriced <K> positions.
  Max drift now <X>."
- Drift-needs-import path: "Audit: <D> drifted inputs requiring re-import.
  Pause for user decision on import_position_market_inputs."

## Routing notes
- Single-persona compound flow. Lives here (not in trader's procedures)
  because the audit→reprice ordering is a routing concern: the orchestrator
  decides whether to invoke repricing based on audit output, not the trader.
- If the user explicitly wants ONLY the audit (not reprice), route directly
  to `market-data-profile` and skip this skill.
```

---

## 6. Tools, wiring, testing, rollout

### 6.1 Two new tools

Both read-only. No HITL config changes. No changes to existing tools.

**`list_reports`**

```python
class ListReportsInput(BaseModel):
    portfolio_id: int | None = Field(default=None,
        description="Filter to one portfolio")
    report_type: Literal["portfolio", "risk", "rfq"] | None = Field(default=None)
    status: Literal["queued", "running", "completed", "failed"] | None = Field(default=None)
    limit: int = Field(default=20, ge=1, le=100)

@tool("list_reports", args_schema=ListReportsInput)
def list_reports_tool(
    portfolio_id: int | None = None,
    report_type: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Return recent ReportJob rows, newest-first, with optional filters."""
    # SELECT ... FROM report_jobs WHERE [filters] ORDER BY created_at DESC LIMIT N
    # Returns: {"reports": [{report_id, portfolio_id, report_type, title,
    #                         status, created_at, artifact_paths}], "total": N}
```

**`get_report`**

```python
class GetReportInput(BaseModel):
    report_id: int = Field(description="ReportJob id from list_reports")

@tool("get_report", args_schema=GetReportInput)
def get_report_tool(report_id: int) -> dict[str, Any]:
    """Return full ReportJob row including artifact_paths and inline summary."""
    # Returns: {report_id, portfolio_id, report_type, title, status,
    #           created_at, completed_at, artifact_paths: {html, excel},
    #           summary: {...}}
    # Raises a clear error if report_id not found.
```

Both added to `QUANT_AGENT_TOOLS` in `langchain_tools.py`. No additions to `hitl.py`.

**Verification items deferred to implementation:**
- Exact `ReportJob` model field names (notably `artifact_paths` dict keys — `reports.py:171` shows `{"html": ..., "excel": ...}`).
- Whether `report_type` enum matches the literal hint (per create_report check, allowed values are `"portfolio" | "risk" | "rfq"`).
- Default sort order (newest-first by `created_at` assumed).

### 6.2 Orchestrator & filesystem wiring deltas

**`orchestrator.py`:**

```python
_ARTIFACTS_ROOT = Path(...).parent.parent.parent / "artifacts"   # new constant

def _skills_backend() -> FilesystemBackend:
    return FilesystemBackend(mounts={
        "/skills":              str(_SKILLS_FS_ROOT),
        "/trading_desk":        str(_TRADING_DESK_ROOT),
        "/large_tool_results":  str(_LARGE_TOOL_RESULTS_ROOT),
        "/artifacts":           str(_ARTIFACTS_ROOT),            # NEW
    })

def _filesystem_permissions():
    return [
        FilesystemPermission(operations=["read"],
                             paths=["/skills", "/skills/**"], mode="allow"),
        FilesystemPermission(operations=["read"],
                             paths=["/artifacts", "/artifacts/**"],  # NEW
                             mode="allow"),
        # ... rest unchanged
    ]

def build_orchestrator(...):
    return create_deep_agent(
        model=model,
        tools=[],
        system_prompt=_orchestrator_prompt(),
        subagents=all_personas(model, tools),
        interrupt_on=interrupt_on or interrupt_on_config(),
        checkpointer=checkpointer,
        backend=_skills_backend(),
        permissions=_filesystem_permissions(),
        skills=["/skills/routing/"],                              # NEW
        name="otc_desk_orchestrator",
    )
```

**`personas.py`:** each `SubAgent` spec's `skills=[...]` extends to the per-persona source list in §2.

**`prompts/orchestrator.md`:**
- The v1 "Naming skills in delegations" section gains a "Naming routing skills" subsection: "If a compound flow matches a routing skill in your catalog, read it first; the routing skill names the personas + procedures to delegate to. You still author each `task(...)` call yourself — routing skills are recipes, not auto-execution."
- Routing matrix gains new rows for the three routing skills. The v1 manual snowball compound routing rule is removed in phase 8 — `snowball-book-audit` is the single source of truth.

### 6.3 Testing strategy (extends v1 tiers)

| Tier | Coverage v1 | Coverage v2 delta |
|---|---|---|
| **A — Unit (skills_loader)** | Policy composition; missing-fragment error | *no change* |
| **B — Catalog assembly** | Per-persona catalog: 2 entries each for trader/risk_manager; 0 for high_board | Extended assertions: trader ~17 entries; risk_manager ~16; high_board ~3-4; **orchestrator 3** (new) |
| **C — Filesystem read smoke** | `read_file` on `/skills/products/snowball-cn/SKILL.md` no-HITL | New: `read_file` on a domain card, a domain recipe, a routing skill, AND `/artifacts/<sample>.html` no-HITL |
| **D — Tool unit (NEW)** | — | DB-backed tests for `list_reports` + `get_report` with ReportJob fixtures (filter combos, missing-id error, artifact_paths shape) |

**Out of scope (still):** End-to-end behavioral tests of compound delegations or routing-skill execution. Same v1 rationale — value-per-effort is low without recorded LLM transcripts.

### 6.4 Rollout — one PR, phased internal commits

Branch: `feat/agent-skills-layer-v2`. Roughly 30-35 commits (4 + 4 + 4 + 6 + 6 + 3 + 3 + 4 by phase, plus 1-2 chore commits). Larger than v1's 25 commits due to the volume of new SKILL.md files; each is its own commit for review granularity. Phases 1-3 are strictly additive and behavior-preserving; phase 8 activates new orchestrator routing.

```
Phase 1 — Tool foundation
  feat(tools): add list_reports read-only tool
  feat(tools): add get_report read-only tool
  test(tools): unit tests for list_reports/get_report

Phase 2 — Filesystem extension
  feat(deep_agent): mount /artifacts in FilesystemBackend
  feat(deep_agent): allow read on /artifacts in permissions

Phase 3 — Skills layer scaffold
  feat(agent-skills): scaffold domains/ tree
  feat(agent-skills): scaffold routing/ tree
  feat(agent-skills): wire orchestrator skills=[/skills/routing/]
  feat(agent-skills): update persona skills source lists

Phase 4 — Domain cards (4 commits)
  feat(agent-skills): add portfolio-model domain card
  feat(agent-skills): add pricing-engines domain card
  feat(agent-skills): add market-data-conventions domain card
  feat(agent-skills): add rfq-lifecycle domain card

Phase 5 — Domain recipes (6 commits, grouped by domain)
  feat(agent-skills): add position-domain recipes
  feat(agent-skills): add pricing-domain recipes
  feat(agent-skills): add risk-domain recipes
  feat(agent-skills): add market-data-domain recipes
  feat(agent-skills): add rfq-domain recipes
  feat(agent-skills): add reporting-domain recipes

Phase 6 — Workflow procedures (6 commits, one per procedure)
  feat(agent-skills): add trader/rfq-intake-and-quote
  feat(agent-skills): add trader/portfolio-pricing-run
  feat(agent-skills): add trader/market-data-profile
  feat(agent-skills): add risk_manager/portfolio-pricing-run (risk lens)
  feat(agent-skills): add risk_manager/risk-report-workflow
  feat(agent-skills): add high_board/report-query-and-display

Phase 7 — Routing skills (3 commits)
  feat(agent-skills): add pricing-and-risk-compound routing skill
  feat(agent-skills): add snowball-book-audit routing skill (retrofit)
  feat(agent-skills): add market-data-then-reprice routing skill

Phase 8 — Orchestrator prompt update
  docs(agent-skills): orchestrator routing matrix references routing skills
  docs(agent-skills): "naming routing skills" subsection in orchestrator.md
  docs(agent-skills): remove v1 prompt-only snowball compound routing rule

Phase 9 — Tests (extending v1 tiers)
  test(agent-skills): tier-B catalog assertions for v2 surface
  test(agent-skills): tier-B orchestrator routing catalog assertion
  test(agent-skills): tier-C read_file smoke for new tiers + /artifacts
  chore: confirm v1 test suite still passes
```

**Rollback strategy.** Pure git revert. Each phase is additive and behavior-preserving up through phase 7. Phase 8 is the activation point. Reverting the PR returns the agent to v1 state.

### 6.5 Risk register

| Risk | Mitigation |
|---|---|
| Catalog size growth (~17 entries × 200 chars per persona) bloats system prompt | Catalog stays well under 5KB; tier-B test records total catalog size as informational metric |
| New tools expose unintended report data | `list_reports`/`get_report` filter by `portfolio_id`; tier-D tests confirm cross-portfolio isolation |
| `/artifacts` read access enables all personas to read reports | Skill-body governance restricts use; trader/risk_manager skills do not invoke artifact reads |
| `snowball-book-audit` retrofit duplicates v1 prompt-only handling | Phase 8 explicitly REMOVES the prompt-only fallback in orchestrator.md, leaving the routing skill as single source of truth |
| `ReportJob` schema details differ from sketched assumptions | Phase 1 verifies on day one; tool signatures may need minor adjustment |
| Catalog name collision within a persona | `SkillsMiddleware` scope is per-source; tier-B assertion enforces unique names per persona catalog |
| Implementation hits unexpected snag in phase 5 or later | Phases 1-3 are strictly additive and could ship as a standalone behavior-preserving PR if scope ever needs to split |

---

## 7. Future work (post-v2)

- **Product card backfill** (`phoenix-cn`, `phoenix-hk`, `vanilla-european`). Content-only batch. Each card pairs with existing procedures (no new procedures required).
- **`get_latest_position_valuations` pagination**. Engineering ticket — the 500-row limit forces the `run_python` reduce pattern for large portfolios. A cursor/offset API removes the workaround.
- **Routing-skill discovery loop**. Capture recurring orchestrator routing failures or successes from session transcripts; lift recurring patterns into new routing skills.
- **`allowed-tools` enforcement**. Promote from soft hint to hard runtime filter once deepagents marks the enforcement non-experimental, or via a custom middleware.
- **High_board additional procedures**. RFQ approval workflow as a procedure (currently the tool is called direct from chat); audit-trail review procedure.
- **Domain skill expansion**. Add a position-staleness-scan recipe once a second procedure needs it (currently only `market-data-profile` and `portfolio-pricing-run` compute staleness inline; if a third joins, factor into a recipe).

---

## Glossary

- **Skill** — A markdown file (`SKILL.md`) with YAML frontmatter + body, loaded by `SkillsMiddleware`, surfaced to a persona's catalog, and read on demand via `read_file`. In v2, one of: procedure, product card, domain card, domain recipe, routing.
- **Policy fragment** — Markdown file under `skills/policy/` concatenated into a persona's system prompt at agent build time. Not a `SkillsMiddleware` skill; not in the runtime catalog. v1.
- **Domain card** — Reference content for one data/concept domain (Position, Portfolio, Pricing, Risk, Market data, RFQ). Free-form body. Read once per relevant session. New in v2.
- **Domain recipe** — Procedural recipe for one safe operation within a domain. Fixed 5-section schema. Named by workflow procedures; can be executed from memory or via `read_file`. New in v2.
- **Workflow procedure** — Persona-scoped SKILL.md describing a user-task-aligned workflow. *Names* domain skills in its step sequence rather than inlining all action. v1 shape extended in v2.
- **Product card** — Product-scoped SKILL.md describing payoff invariants, pricing engine, market quirks, diagnostic signals. v1.
- **Routing skill** — Orchestrator-only SKILL.md describing a compound flow across personas (or a control-flow decision within one persona). Read by orchestrator; lists `task(...)` delegations. New in v2.
- **Catalog** — The list of `(name, description, path)` triples for skills available to a given agent, auto-injected into its system prompt by `SkillsMiddleware`.
- **Progressive disclosure** — The pattern where only skill metadata (catalog) is in context by default; full SKILL.md content is fetched on demand via `read_file`. v1.
- **Anchor pattern** — v1's `snowball-position-diagnostics` and `snowball-cn` retained as-is in v2. Demonstrates that self-contained procedures remain a valid shape; v2 adds composed procedures as an additional shape.

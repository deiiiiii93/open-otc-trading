# Trader RFQ-to-Booking Day — Golden Workflow Design

**Date:** 2026-06-29
**Status:** Draft for review
**Author:** desk (brainstormed with Claude)

## 1. Purpose

Add a second golden workflow alongside the flagship `risk-manager-control-day`, this
one for the **`trader`** persona. It models the full **RFQ → booking lifecycle**: a
client RFQ for a structured option is captured, quoted, routed for approval, built
into a QuantArk-validated product, booked as a position, verified against the RFQ,
and finally priced into the desk book to report its impact.

The workflow feeds the same three consumers as the flagship:

1. **Deterministic regression** — scripted-graph replay against pinned tool outputs.
2. **LLM arena eval** — Zenmux multi-model runner + GPT-5.5 judge → `/arena`.
3. **Hyperframes demo** — transcript → composition (out of scope to build here;
   the workflow must be demo-shaped but no new demo code is required).

The workflow itself is **additive**: one new `definitions/trader-rfq-booking-day.md`
plus a sibling `trader-rfq-booking-day.fixtures.json`. It cites only skills/tools
that already exist — the registry's reference checks (every `expected_skill` maps to
a real `SKILL.md`, every `expected_tools` entry to a real tool in
`all_agent_tools()`, every `replay` key to the fixtures replay block) bound it to
the existing surface.

This being the **first** workflow to create RFQs *live* exposes two harness gaps it
must also close (see §8.5):

1. **Arena cleanup misses RFQs.** `_purge_seeded_portfolios` cleans by introspecting
   mapped tables for `portfolio_id`/`position_id` columns; `RFQ` has neither, and the
   autonomous flow's `book_position` leaves `Position.rfq_id` null, so live-created
   `rfqs` (+ cascaded `rfq_quote_versions`, `approvals`) leak into the real desk DB
   across matches. → delete them by the `rfq_id`s harvested from the match trace.
2. **No `rfq` seed namespace.** Added now as a forward-looking harness investment so
   future workflows can *start* from a pre-existing RFQ (e.g. an "approve queued
   RFQs" ops/risk workflow). This workflow does **not** seed its RFQ — step 1
   creates it live — so the namespace ships with its own unit test rather than being
   exercised by the trader happy path.

No schema change / no alembic migration: the `rfqs` table already exists; both
changes are loader/runtime logic.

## 2. Why the trader persona / why this scenario

The flagship is a **read-and-report** flow (read risk → refresh → read → stress →
backtest → report). The trader RFQ-to-booking day is a **create-and-validate** flow,
maximally distinct, so it adds discriminating signal to the arena board rather than
re-measuring the same behavior. Each step consumes the prior step's persisted
output, forming a dependency chain a model cannot reorder or short-circuit without
failing `skills_routed_sequence`.

All cited skills are already wired into the trader persona
(`PERSONA_WORKFLOW_DOMAINS["trader"]`: positions, products, pricing, rfq, …) and all
cited tools are already in `QUANT_AGENT_TOOLS`.

## 3. The product at the center: a down-and-in barrier put

The RFQ is for a **1-year down-and-in barrier put** on the control underlying. This
choice is deliberate:

- `BarrierOption` is a proven family (used by the flagship's positions) with a
  known QuantArk class name and native `product_kwargs`.
- Per `build-contract.md`, `BarrierOption` requires `strike`, `barrier`,
  `maturity_years`, `initial_price`; `barrier_type` is **optional, default
  `DOWN_OUT`**. This optional-default is the primary discriminator (see §6).
- `BarrierType.DOWN_IN` is a real enum value (confirmed in quant-ark).

## 4. Step sequence (8 steps)

Governance ordering: **approval is routed after the quote, before anything is built
or booked.** `build_product` is pure validation (`write_actions: false`) and
`book_position` is the direct booking tool, so neither requires the RFQ to be in an
`approved` state — the trader submits for governance and proceeds (see §5).

| # | User turn (imperative, names everything needed) | `expected_skill` | `expected_tools` | Step assertion(s) |
|---|---|---|---|---|
| 1 | "A client wants a 1-year down-and-in barrier put on the control name, strike at-the-money, knock-in at 80%. Capture it as an RFQ." | `intake-request` | `create_or_update_rfq_draft` | `task_returned_id` / draft persisted; `response_contains` the underlying |
| 2 | "Quote it." | `quote-rfq` | `solve_rfq`, `quote_rfq` | quote persisted; `response_contains` a solved value / engine |
| 3 | "Route the quote for approval." | `submit-for-approval` | `submit_rfq_for_approval` | `response_contains` "submitted" / approval state |
| 4 | "Risk has the quote. Build the product so we can book it — 1Y down-and-in barrier put, strike ATM, KI 80% on the control name." | `build-product` | `fetch_market_snapshot`, `build_product` | `build_product` returns `ok == true` **and** built terms carry `barrier_type == DOWN_IN` |
| 5 | "Approved — book it into the control portfolio." | `book-position` | `book_position` | `task_returned_id` for `book_position`; position created |
| 6 | "Show me the booked position — does it match the RFQ?" | `position-snapshot` | `get_position_summaries` | `response_contains` the barrier level / "down-and-in"; booked terms match RFQ |
| 7 | "Now price the control book with this position in it." | `price-portfolio` | `run_batch_pricing` | `task_returned_id` for `run_batch_pricing` |
| 8 | "What's the net delta impact of the new trade?" | `position-snapshot` (read) | `get_latest_position_valuations` | `response_contains` "delta" and a signed impact |

The RFQ is created **live** in step 1 (there is no `rfq` seed namespace); `rfq_id`
threads forward through steps 2–6 via the agent's own context, exactly as the real
desk flow does.

## 5. Autonomy: the 2→3→4 chain runs without HITL

Three SKILL.md behaviors can pull a human into the loop. Each is neutralized:

| Step | HITL trigger | Neutralized by |
|---|---|---|
| 2 `quote-rfq` | "Cost-preview when the product is path-dependent" (a barrier is) | Live driver runs `confirmed_cost_preview=True` → preview auto-confirms |
| 3 `submit-for-approval` | `confirmation_required: true` → composes confirm summary and waits | Live driver runs `yolo_mode=True` → tool confirmation auto-accepted |
| 4 `build-product` | **`propose_term_form` term-collection card** when `build_product` returns a non-empty `missing` list | Guarantee `missing == []` (below) |

**Why gate 4 is the dangerous one.** Gates 2–3 are tool-confirmation gates already
solved by the flagship's live-driver settings (`yolo_mode=True,
confirmed_cost_preview=True`). Gate 4 is a *business-logic stop* baked into the
skill's procedure: a term-collection card is the agent **ending its turn to ask the
user**. If it fires, the next scripted turn ("book it") arrives as an answer to the
wrong question → desync → cascade failure (the flagship's "model paused/desynced and
conflated steps" gotcha).

**Neutralizing gate 4 — guarantee `missing == []`:**

1. **Complete economics are always available.** `BarrierOption`'s required fields
   are `strike`, `barrier`, `maturity_years`, `initial_price`. The step-1 and step-4
   user turns state strike (ATM), barrier (KI 80%), and tenor (1Y) explicitly;
   `initial_price` is resolved by `fetch_market_snapshot` against the seeded
   underlying (the `build-contract.md` procedure for S0). Nothing required is
   genuinely absent, so `build_product` never returns a non-empty `missing` list.
2. **The step-4 turn restates the full terms** ("name everything in the imperative
   turn" — the exact flagship fix that moved it 6.5 → 77.4), so even a model with
   weak context retention has every term in the immediate message.

**Approval is fire-and-continue.** `submit_rfq_for_approval` routes the RFQ and
returns immediately with state `submitted`; build/book do not block on
`approved`. The trader submits for governance and proceeds to build/book the same
validated trade. Step-5's turn frames it ("Approved — book it") so the narrative is
governance-coherent without a real approver in the loop.

## 6. Discriminator: two HITL-free layers

**Layer 1 — the optional-default trap (primary).** The RFQ is a down-and-**in** put,
but `barrier_type` is optional with default `DOWN_OUT`.

- A careless model omits `barrier_type` → `build_product` returns **`ok == true`**
  (valid default) → silently builds a down-and-**out**: the wrong product. *Never a
  card* (field is not required), *never a rejection*. The error surfaces at step 6
  (snapshot ≠ RFQ) and in the success rubric.
- A careful model reads "down-and-in" from the RFQ + the `barrier_type` option in
  `build-contract.md` → passes `barrier_type="DOWN_IN"` → correct build.

This mirrors the single most common real booking error (barrier direction) and is
**provably HITL-free**: an optional field with a valid default can never produce a
missing-field card or a rejection.

**Layer 2 — a context-recoverable validation rejection (secondary).** A barrier
*level* that QuantArk validation rejects with **`ok == false` and `missing == []`**
(a constraint violation, not a missing field). `build-product`'s own procedure
("merge and call `build_product` again; loop until `ok`") then drives the model to
re-call with a corrected level **derivable from the RFQ context** — no card, no
human. Strong models recover in one extra tool call; weak models loop or give up
(the spread we want).

> **IMPLEMENTATION REQUIREMENT (probe-first):** the exact barrier value that yields
> `ok == false, missing == []` MUST be probe-validated against the real
> `build_product` tool during implementation (seed a throwaway build, inspect the
> result), exactly as the flagship's fixtures were probe-validated. Do **not** ship a
> Layer-2 value asserted from assumption. If no such clean validation rejection
> exists for `BarrierOption`, Layer 2 is dropped and Layer 1 stands alone (the
> workflow is still well-formed and discriminating).

**Crucial rule (flagship lesson):** the objective manifest rewards the **end state**
— a correctly-booked DOWN_IN position matching the RFQ — and **never mandates that a
rejection occurred**. A model that nails the build first try must score full marks.
Headroom lives in recovery quality, not in a scripted gotcha every model trips
identically. Do not over-tune so one model hits 100%.

## 7. Scoring manifest (~33 points)

Following the flagship's pinned-points model (31 = 7 skill + 10 tool + 8 step + 6
success):

- **8 skill points** — one per step's `expected_skill` routing correctly.
- **10 tool points** — one per `expected_tools` entry (1+2+1+2+1+1+1+1 across the 8
  steps; steps 2 and 4 each cite two tools).
- **~9 step-assertion points** — the per-step assertions in the §4 table.
- **6 success-assertion points** — end-state rubric graded by the LLM judge:
  RFQ captured; quote produced; approval routed; product built as **DOWN_IN**;
  position booked into the control portfolio; booked terms match the RFQ and the
  book-impact (net delta) is reported.

The exact point total is pinned in an objective manifest in the workflow frontmatter
and validated against the live transcript during implementation (the flagship's
6.5 → 77.4 reconciliation showed assertions must match **live** tool-output shapes,
not hand-authored replay shapes — see §9).

## 8. Fixtures (`trader-rfq-booking-day.fixtures.json`)

Name-based convention (no `$seed` ids; assertions use names / `response_contains`;
arena-tagged seeds purged-and-reseeded per match). Seed namespaces (all already
supported in `fixtures.py`):

- **`portfolios`** — one **control portfolio** (arena-tagged via `tags=["arena"]`)
  that the new position books into and that step 7 prices.
- **`positions`** — a small **existing book** (1–2 positions on other underlyings)
  so the step-7/8 "impact of the new trade" is meaningful against a baseline. Use
  real QuantArk class names (`EuropeanVanillaOption` / `BarrierOption`) and native
  `product_kwargs` (`maturity`, not `maturity_years`), strikes ≈ spot for non-zero
  Greeks (flagship lesson).
- **`pricing_profiles`** — one **Control Profile** (arena-owned via
  `summary[arena_owned]`) named in the imperative turns for steps 2 and 7
  (quote/price both resolve r/q/vol strictly from the profile).
- **`pricing_parameter_rows`** — one complete row (`rate`, `dividend_yield`,
  `volatility` all non-null) **per underlying**: the RFQ's control name plus every
  existing-book underlying, or quote/price return empty results (flagship's
  "priceable fixtures" hard requirement).
- **No `risk_runs`** needed (this is a trader flow, not a risk-refresh flow).

The RFQ itself is **not seeded** — step 1 creates it live.

**Replay block** — one entry per step (`step-1-intake` … `step-8-impact`) with tool
outputs matching **live** tool shapes so the deterministic regression stays green.
The replay path is the **clean** path (step-4 replay returns `ok == true` with
`barrier_type == DOWN_IN`); the Layer-1/Layer-2 discriminators only ever materialize
in *live* arena runs with a real model.

## 8.5 Harness hardening (this cycle)

### 8.5.1 Clean live-created RFQs via the harvested trace (not a back-link)

Two confirmed facts rule out a `Position.rfq_id` back-link purge:

- `book_rfq_to_position` requires `RfqStatus.CLIENT_ACCEPTED` (`services/rfq.py:889`),
  reachable only via approve → release → mark-accepted — a risk/governance authority
  the autonomous trader run cannot exercise. So step 5 **must** use direct
  `book_position`.
- Direct `book_position` (via `ProductBookingSpec`, the "book without an RFQ" path)
  does **not** set `Position.rfq_id`. So the workflow's RFQ never links to its
  position and stays in `SUBMITTED`; a back-link purge would clean nothing.

**Mechanism — delete exactly the RFQ ids the agent created, sourced from the trace
the harness already harvests.** Step 1's `create_or_update_rfq_draft` tool span
output carries the new `rfq_id`. The live driver already reconstructs the transcript
from `TraceStore`; extend the harvest to collect the set of `rfq_id`s appearing in
this match's `create_or_update_rfq_draft` / `quote_rfq` / `submit_rfq_for_approval`
tool outputs, and have `run_match` delete those `rfqs` rows (ORM delete so
`quote_versions`/`approvals` cascade) as part of the same post-match cleanup that
purges portfolios/profiles.

- **Scope:** exactly the RFQs *this match's agent* created — maximally precise, no
  new tag, no `created_at` window race in the shared DB, not dependent on the model
  passing a sentinel.
- **Deterministic regression:** unaffected — replay never touches the live RFQ DB.
- **Fallback if harvest plumbing proves heavy:** a sentinel `client_name` (named in
  the step-1 turn) + an `id > baseline` guard captured at match start. Documented as
  the secondary option; the trace-harvest path is preferred.

### 8.5.2 Add an `rfq` seed namespace to `fixtures.py`

A forward-looking harness investment (not used by this workflow's happy path).

- **`_NAMESPACES["rfqs"]`** — minimal required keys, mirroring the existing pattern
  (`portfolios: {alias, name}`). Proposed: `{alias, status}` with optional
  `client_name`, `channel`, `request_payload`, `quote_payload`. `id` optional
  (autoincrement, per the name-based convention).
- **`apply_seed` branch** — build an `RFQ(...)` row from the seed dict, defaulting
  `client_name`/`channel`/`status` to the model defaults when omitted.
- **`_INSERT_ORDER`** — `rfqs` must precede `positions` so a seeded position can
  reference a seeded RFQ.
- **`_FK`** — optionally add `positions: {"rfq": "rfqs"}` so a seeded position can
  resolve its `rfq` alias to a seeded RFQ id (in addition to the existing
  `{"portfolio": "portfolios"}`). Keep `rfq` optional on positions.
- **Tests** — a unit test in the fixtures test module that seeds an `rfqs` entry and
  asserts the row persists with the expected status; a second asserting a seeded
  position can link a seeded RFQ via the `rfq` alias.
- **Purge scoping for *seeded* RFQs** is deferred: since this workflow seeds none,
  and a future RFQ-starting workflow will need its own arena-scoping decision
  (sentinel `client_name`, or the booked-position back-link), we do not generalize
  the purge to seeded-but-unbooked RFQs now.

## 9. Consumer compatibility & known traps (from the flagship)

- **Live-shape assertions.** Author step assertions against the shapes the *live*
  tools actually return, then mirror those into the replay fixtures (the flagship's
  hardest reconciliation: `get_*`/`write_report_artifact` live shapes differed from
  hand-authored replay shapes). Probe the real tools before pinning assertions.
- **Settle between steps.** Queued `run_*` TaskRuns (step 7 `run_batch_pricing`)
  execute async; the live driver's `settle()` must drain them before step 8 reads
  the valuation, else step 8 reads stale data.
- **Arena-tagged purge.** `run_match` purges prior arena-tagged portfolios/profiles
  before reseeding; a real same-named portfolio is never deleted. Honor the
  tagging (`tags=["arena"]`, `summary[arena_owned]`).
- **Env.** Run live validation in the PRIMARY checkout on `main` (quant-ark on the
  venv `.pth`; `config/agent_channels.yaml` is gitignored). Always
  `PYTHONPATH=backend .venv/bin/python` (anaconda `python` shadows the venv).

## 10. Out of scope (YAGNI)

- No golden-workflows schema or registry changes; no new arena migration. (The two
  harness changes in §8.5 are loader/runtime logic only.)
- No hyperframes composition build (the workflow is demo-shaped; rendering is a
  later, separate cycle).
- No new agent tools or skills — the workflow only cites existing ones.
- No `sales` or `quant` persona workflows (separate future cycles).
- No purge handling for *seeded* RFQs (this workflow seeds none; a future
  RFQ-starting workflow decides its own arena-scoping — §8.5.2).

## 11. Open items to resolve during implementation

1. **Probe Layer 2** (§6) — find the exact `BarrierOption` barrier value yielding
   `ok == false, missing == []`, or drop Layer 2.
2. **Pin the objective manifest total** (§7) against a real live transcript.
3. **Pick concrete tickers** for the control name and the existing book (any symbol
   with a seeded `pricing_parameter_rows` row works).
4. **Confirm `intake-request`'s exact tool** — `create_or_update_rfq_draft` vs a
   validate-then-draft pair — against the skill's live behavior.
5. **Plumb harvested `rfq_id`s into post-match cleanup** (§8.5.1) and confirm the
   ORM delete cascades `rfq_quote_versions`/`approvals`, with a test that runs two
   consecutive matches and asserts no arena `rfqs` rows remain.
6. **Confirm the `rfq_id` is recoverable from the trace** — that
   `create_or_update_rfq_draft` (and/or `quote_rfq`) tool-span outputs expose the
   `rfq_id` in the shape `trace_harvest` parses; if not, fall back to the sentinel
   `client_name` + `id > baseline` mechanism (§8.5.1 fallback).

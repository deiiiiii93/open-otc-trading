# Arena fixture determinism — frozen seed valuation + harvested truth values

**Date:** 2026-07-06
**Status:** design
**Sub-project:** 1 of 2. Prerequisite for
`2026-07-06-arena-ability-card-design.md` (the Model Ability Card). This spec
establishes reproducible ground-truth numbers; the card spec consumes them.

## Problem

The card reform (Spec B) makes **Grounding (GRD)** score against *known-truth
fixture values* rather than self-grounding against the same-step tool payload.
That fixes the real defect the user identified: today a model that answers a
correct number **from context** — without re-calling the tool on that turn —
fails grounding for lack of a same-step payload to derive the truth from
(`assertions.py:278-291`, the `response_quotes_tool_value` path digs the target
out of `_last_result(ctx, tool)`; no call this step ⇒ `no result for <tool>` ⇒
fail even when the answer is right).

Fixture-based grounding only works if the numbers are **reproducible**. Two
facts make them *not* reproducible today:

1. **The live desk computes numbers as-of wall-clock market data.** Arena matches
   drive the REAL orchestrator (`runner.py:3-8`: each step goes through
   `AgentService.stream_and_persist`, transcript harvested from the trace). The
   seed pins `valuation_date: 2026-06-24` and the pricing parameters (`r`, `q`,
   `vol`) in `risk-manager-control-day.fixtures.json`, but **spot is not pinned**
   — so QuantArk Greeks (AAPL delta, the landscape grid, scenario CVaR) move with
   whatever spot the live market-data path resolves. A hand-written fixture like
   "AAPL delta = 573.35" would drift the moment spot ≠ 100.

2. **The existing replay fixtures are internally inconsistent.** In
   `risk-manager-control-day.fixtures.json` the tool-result *paths* and the *prose
   / report artifact* disagree: step 3's payload path
   `metrics.positions[position_id=8].delta = 573.3467`, but the step-3 narrative,
   the step-7 report artifact, and the README all quote `-148,000`; the landscape
   raw grid has `delta@0% = 860.47` while the step-4 response says `-248,500`; the
   report cites `gamma@+10% = -9,600` while the grid says `16.403`. Self-grounding
   tolerates this because it scores only against the payload path and ignores the
   prose. Fixture grounding cannot — the manifest truth value and the replay
   transcript that must earn 39/39 have to agree.

So before the card can score numbers as a first-class gate, the desk must produce
**deterministic** numbers and the fixtures must be **harvested from a real payload
against that frozen state**, per the repo rule *"Grounding fixtures must be
harvested from real tool payloads, not invented."*

## Decisions

- **A1 — One frozen valuation constant: `SEED_ACCOUNTING_DATE = 2026-06-24`.** A
  single source of truth (a module constant re-exported to the fixtures), matching
  the flagship backtest end and the existing `pricing_profiles.valuation_date`.
  Everything time- or market-dependent on the golden/arena path resolves against
  it.

- **A2 — Freeze the market snapshot (spot), not just the date.** The golden/arena
  pricing path must resolve spot from a **pinned per-underlying snapshot** seeded
  alongside the pricing parameters (e.g. AAPL/TSLA/NVDA spot = 100.0, matching the
  seed stale-run's `spot: 100.0`), never from a live market feed. This is the
  actual determinism lever — with spot, `r`, `q`, `vol`, `maturity` and
  `valuation_date` all fixed, QuantArk output is a pure function and every producer
  (`run_batch_pricing`, `run_greeks_landscape`, `run_scenario_test`,
  `run_backtest`) is reproducible.

- **A3 — Inject the clock/market context; do not touch production wall-clock.**
  Pinning applies **only** to the golden-workflow / arena desk path (seeded
  portfolios flagged as golden, or an arena-scoped pricing-environment override).
  The production desk keeps resolving today/market live. Prefer an injectable
  valuation-context seam over a global monkeypatch so tests and live runs stay
  isolated (consistent with the repo's existing tracing-off / config-seam
  patterns).

- **A4 — Harvest fixture truth values from ONE real run, then pin them.** Add a
  harvester (script or test helper) that seeds the frozen state, drives the
  flagship against the real desk once, and records the true numbers into the
  manifest's grounding checks: AAPL hotspot delta, portfolio gamma@+10%, portfolio
  delta@-20%, scenario CVaR, and the backtest headline P&L. Values are *read from
  the tool payloads*, never authored by hand.

- **A5 — Reconcile the replay transcript with the harvested truth.** Rewrite the
  inconsistent prose/report numbers in `risk-manager-control-day.fixtures.json`
  (the `-148,000` / `-248,500` / `-9,600` family) so the canned replay transcript
  quotes the *same* values as the payload paths and the new fixture targets. After
  this, the golden-replay regression earns full marks against **fixture** grounding
  (not just self-grounding), closing the prose/payload gap.

- **A6 — Determinism gate test.** A test seeds the frozen state and drives the
  flagship producers twice (or re-harvests), asserting the harvested numbers are
  identical across runs. This is the guard that keeps the fixture truth valid over
  time — if a producer ever reintroduces wall-clock/live-market dependence on the
  golden path, this test fails loudly rather than silently rotting the fixtures.

- **A7 — Staleness stays honest.** Step 1's staleness assertion depends on the
  seed stale-run being > 24h older than the valuation instant. With the baseline
  `risk_runs` row stamped relative to `SEED_ACCOUNTING_DATE` (its `created_at` /
  `valuation_as_of` already `2026-06-22`, two days prior), the staleness check is
  deterministically satisfied and does not rely on wall-clock.

## Architecture

**Seed / fixtures (`app/golden_workflows/fixtures.py`,
`risk-manager-control-day.fixtures.json`)**
- Introduce `SEED_ACCOUNTING_DATE` (constant) and a `market_snapshot` seed
  namespace (or extend `pricing_parameter_rows` with a `spot` column) so each
  underlying carries a pinned spot. Validate it in `_NAMESPACES`.
- Reconcile all replay prose/report numbers to the harvested truth (A5).

**Pricing / producer path** — resolve the valuation context (date + spot
snapshot) from the frozen seed on the golden/arena path via the injectable seam
(A3). Identify the exact resolution points during planning
(`run_batch_pricing` / greeks-landscape / scenario / backtest producers); the plan
enumerates each and routes it through the seam.

**Harvester (`app/golden_workflows/` tool or `scripts/`)** — `harvest_fixtures`:
seed frozen state → drive flagship once → emit the manifest grounding targets.
Idempotent; re-running against the frozen seed yields identical output (A6).

**Manifest (`risk-manager-control-day.md`)** — the grounding assertions gain
literal `value:` targets (Spec B defines the assertion shape
`response_quotes_value`); this spec only guarantees those numbers are the real,
frozen, harvested ones.

## Failure handling

- **Live-market path unpinned by mistake:** the A6 determinism test fails on the
  second harvest — caught in CI, never ships as silent fixture rot.
- **QuantArk numeric change (engine upgrade):** re-run the harvester; the fixture
  numbers update from real payloads in one step, and the replay reconciliation
  (A5) is re-applied. No hand-editing of magic numbers.
- **Production desk regression:** out of scope by construction — pinning is
  golden/arena-scoped (A3); production keeps wall-clock/live-market.

## Testing

- **Determinism (A6):** seed frozen state, drive producers twice, assert byte-equal
  harvested numbers.
- **Fixture consistency:** the golden-replay regression earns 39/39 with the
  reconciled transcript, now against fixture grounding — extends the existing
  fixture-consistency gate.
- **Staleness (A7):** step 1 reads stale deterministically with no wall-clock
  dependence.
- **Isolation (A3):** a production-path pricing call is unaffected by the frozen
  seed (no global monkeypatch leakage) — mirrors the repo's `.env`/tracing test
  caveats.

## Out of scope

- The card scoring engine, OVR, stats, ranking, and UI — all in Spec B.
- Changing production desk date/market resolution.
- New QuantArk models or market-data providers.
- Migrating historical arena rows (#1–#11) — untouched; they keep their stored
  self-grounded scores.

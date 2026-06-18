# Pricing Parameter Tools — Design

**Date:** 2026-06-05
**Status:** Implemented (brainstormed + section-by-section review)

> **Implementation errata** (accepted deviations, see git history on
> `worktree-pricing-parameter-tools`):
> 1. `delete_profile` guard also refuses `FxRate`-referenced profiles
>    (third FK the spec missed); detail adds `fx_rate_ids`.
> 2. E2E asserts per-field `field_sources` equality instead of
>    `pricing_parameter_match_type` (key not present in `market_inputs`).
> 3. SKILL.md body trimmed 552→491 tokens for the 500-token CI lint cap
>    (routing/stop-conditions/complete-row rule preserved).
> 4. All 11 tool names also added to `DEEP_AGENT_TOOL_NAMES` in
>    `services/agents.py` (execution allowlist; plan gap).
> 5. Extra exact-set test updates: `test_reference_docs.py`,
>    `test_remaining_workflow_skills_phase3.py`; reference doc carries
>    name/description/reference_type frontmatter.
> 6. Additional refusal codes beyond the spec list: `blank_symbol`,
>    `no_fields`, `blank_name`, `invalid_clear_field`,
>    `invalid_valuation_date`.

## Problem

A desk agent run hit this blocker:

> `create_pricing_parameter_profile` is not in the agent tool set, and the
> Pyodide sandbox cannot reach the backend REST API.

The agent can *consume* pricing parameter profiles (`price_positions` /
`run_risk` accept `pricing_parameter_profile_id`; `list_pricing_parameter_profiles`
exists) but has **no write path** to any part of the parameter chain.

**Correction to the blocker text:** profiles with
`source_type="default_underlying_archived"` are NOT produced by an ongoing
"market-data archival pipeline". They were created once by migration 0024
(instrument unification) as a retag of legacy `default_underlying` profiles,
kept only because historical `position_valuation_runs` / `risk_runs` reference
them by FK. There is nothing to trigger; the gap is real and is the missing
tool surface.

## Current architecture (post migration 0024)

Two parameter stores feed pricing:

1. **`PricingParameterProfile` + `PricingParameterRow`** — trade-keyed r/q/vol.
   Created only via xlsx import (`POST /api/pricing-parameter-profiles/import`
   → `import_pricing_parameter_profile_from_xlsx`). Spots are NOT stored on
   rows (instrument-unification T8 moved observations to the quote store).
2. **`AssumptionSet` + `AssumptionRow`** — instrument-keyed r/q/vol, the
   canonical baseline. Derived-only via `build_assumptions_set` (REST
   `POST /api/assumptions/build`): resolves `Instrument` defaults first, then
   inherited latest `PricingParameterRow` per underlying, recording per-field
   provenance in `source_payload`. `UnderlyingPricingDefault` is a synonym of
   `Instrument` (`models.py`).

Resolution chain per field in `position_pricer.py` (`_market_input_source`):
**override → pricing-profile row (trade-id exact, else unique complete
underlying row) → assumption-set row → missing.** A profile row missing any of
r/q/vol is `match_type="incomplete"` and refused — what-if rows must carry all
three fields.

`source_type` on profiles is display-only (no code filters on it), so a new
`source_type="agent"` value is safe.

## Decisions (clarified with user)

| Question | Decision |
| --- | --- |
| Scope | **Both stores, write-capable** |
| Profile mutability | **Full CRUD + guarded delete** |
| Assumption-set writes | **Pipeline-only** (instrument defaults + build; no direct row writes — provenance stays intact) |
| Governance | **New workflow skill** owns all writes (portfolio-maintenance pattern) |
| Tool granularity | **Per-action tools** (~11), not multiplexed manage-tools |

## Architecture & layering

```
app/services/domains/pricing_profiles.py   ← extend read facade with 5 write fns
app/services/domains/assumptions.py        ← NEW: list/get sets, build, get/set instrument defaults
app/tools/pricing_profiles.py              ← extend: +1 read, +5 write tools
app/tools/assumptions.py                   ← NEW: 3 reads + 2 writes
app/tools/__init__.py                      ← register 11 tools in QUANT_AGENT_TOOLS
app/services/deep_agent/hitl.py            ← 7 writes → INTERRUPT_TOOL_NAMES + risk levels + labels
app/skills/workflows/pricing/pricing-parameter-maintenance/SKILL.md  ← NEW skill
```

- Write service functions take a required `session`; tools open
  `database.SessionLocal()` and `commit()` (the `hs.book_hedge` pattern). The
  existing read-only `_session_scope` in `domains/pricing_profiles.py` stays
  untouched.
- Writes record audit events with `actor="agent"` (event types:
  `pricing_parameter_profile.created/.updated/.rows_upserted/.rows_deleted/.deleted`,
  `instrument.pricing_defaults_updated`, `assumptions.built` — reusing the
  existing event-recording helper the REST layer uses).
- **No REST or frontend changes.** Agent-created profiles appear on the
  existing Pricing Parameters page automatically.

## Tool surface (11 tools)

### Reads — `@capability_gated(group=ToolGroup.DOMAIN_READ)`, no HITL

| Tool | Args | Semantics |
| --- | --- | --- |
| `get_pricing_parameter_profile` | `profile_id: int` | Full profile incl. rows (`id, source_trade_id, symbol, instrument_id, rate, dividend_yield, volatility`). Uses existing `get_profile`; add `shape_pricing_parameter_row` to `tools/_shaping.py` |
| `get_instrument_pricing_defaults` | `symbols: list[str] \| None`, `limit: int = 50` | Instrument `id/symbol/status` + r/q/vol defaults; filter by symbols when given |
| `list_assumption_sets` | `query: str \| None`, `limit: int = 20` | Summary level (id, name, valuation_date, status, row_count), newest first — mirrors the profile list tool |
| `get_assumption_set` | `set_id: int` | Set + rows with per-field provenance summarized from `source_payload` |

### Writes — `@capability_gated(group=ToolGroup.DOMAIN_WRITE)` + HITL

| Tool | Args | Semantics | Risk |
| --- | --- | --- | --- |
| `create_pricing_parameter_profile` | `name: str \| None`, `valuation_date: datetime \| None`, `rows: list[RowInput]` | RowInput = `{symbol*: str, source_trade_id: str = "", rate?: float, dividend_yield?: float, volatility?: float}`. Validation: ≥1 row; each row ≥1 of r/q/vol; duplicate normalized `(source_trade_id, symbol)` pairs rejected. Instrument resolution copies xlsx import: position by `source_trade_id` → its `underlying_id`, else `ensure_instrument(symbol, source="pricing_profile", status="draft")`. Profile fields: `source_type="agent"`, `source_path=None`, `status="completed"`, `summary={row_count, created_by: "agent"}`. Defaults: `name` → `f"Agent Pricing Parameters {valuation_date:%Y-%m-%d}"`, `valuation_date` → now. **r/q/vol only — no spot** | write |
| `update_pricing_parameter_profile` | `profile_id: int`, `name: str \| None`, `valuation_date: datetime \| None` | Metadata only; at least one field required | write |
| `upsert_pricing_parameter_rows` | `profile_id: int`, `rows: list[RowInput]` | Match on normalized `(source_trade_id, symbol)`; on match, provided fields overwrite and absent fields stay (clear a field = delete row + recreate); no match → insert with the same instrument resolution as create | write |
| `delete_pricing_parameter_rows` | `profile_id: int`, `row_ids: list[int]` | All ids must belong to the profile, else refuse the whole call | write |
| `delete_pricing_parameter_profile` | `profile_id: int` | **Guard:** refuse if any `position_valuation_runs` or `risk_runs` row references it (report the run ids). Cascade deletes rows otherwise | **irreversible** |
| `set_instrument_pricing_defaults` | `symbol: str`, `rate?: float`, `dividend_yield?: float`, `volatility?: float`, `clear: list[str] = []` | Resolve instrument by symbol; ensure-create (`status="draft"`) when missing. Provided fields set; `clear` entries (subset of `rate/dividend_yield/volatility`) null out; a field in both → refuse | write |
| `build_assumption_set` | `name: str \| None`, `valuation_date: datetime \| None` | Wraps `services/assumptions.build_assumptions_set`. `ValueError({"unfilled_underlyings": [...]})` → `{"ok": False, "error": "unfilled_underlyings", "detail": [...]}` so the agent can set defaults and retry; "no open positions in scope" likewise structured | write |

**Cross-cutting guard:** all four profile mutators (`update`, `upsert_rows`,
`delete_rows`, `delete`) refuse profiles with
`source_type="default_underlying_archived"` — migration-0024 audit artifacts
whose rows historical runs depend on.

**Underlying-level what-if rows:** empty `source_trade_id` is the supported
idiom. `resolve_pricing_parameter_row_for_position` only attempts trade-id
matching when the *position* has a trade id, so `""`-keyed rows behave as pure
underlying-level rows.

## Workflow skill (full sketch)

`app/skills/workflows/pricing/pricing-parameter-maintenance/SKILL.md` — body
stays under the 500-token cap:

```markdown
---
name: pricing-parameter-maintenance
description: Create or maintain pricing parameters through HITL-confirmed writes —
  ad-hoc what-if profiles (trade- or underlying-keyed r/q/vol), profile row edits,
  guarded profile deletion, instrument r/q/vol defaults, and assumption-set rebuilds.
  Use when user asks to price or run risk with custom parameters, fix a wrong
  rate/vol/dividend, set an underlying's default parameters, or rebuild assumptions.
domain: pricing
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - requested_change
optional_context:
  - profile_id
  - symbols
  - valuation_date
write_actions: true
confirmation_required: true
success_criteria:
  - the single proposed write is confirmed via HITL card and verified by re-read
  - guard refusals (archived profile, referenced runs, unfilled underlyings)
    are reported verbatim without persistence
---

## When to use

- Price/run risk under custom r/q/vol → create a what-if profile, then pass
  its id to `price_positions` / `run_risk`.
- Correct a wrong parameter in an existing profile → row upsert/delete.
- Set an underlying's baseline r/q/vol → instrument defaults + rebuild.
- Clean up a scratch profile → guarded delete.

## Routing decision

Scenario/what-if parameters (one run, specific trades or underlyings) →
PROFILE path. Canonical baseline ("from now on 000905.SH vol is 22%") →
PIPELINE path (set_instrument_pricing_defaults, then build_assumption_set).
Never edit archived (`default_underlying_archived`) profiles — create new.

## Procedure

1. Resolve targets: `list_pricing_parameter_profiles` /
   `get_pricing_parameter_profile` or `get_instrument_pricing_defaults`.
2. PROFILE path: `create_pricing_parameter_profile` (underlying-level rows
   leave source_trade_id empty; carry ALL of rate, dividend_yield, volatility
   per row — copy current values for unchanged fields, an incomplete row is
   refused by the resolver), or `upsert_pricing_parameter_rows` /
   `delete_pricing_parameter_rows` / `update_pricing_parameter_profile` /
   `delete_pricing_parameter_profile` on an existing one.
3. PIPELINE path: `set_instrument_pricing_defaults` per symbol, then
   `build_assumption_set`. If it returns unfilled_underlyings, fill those
   and retry.
4. Propose exactly ONE write per turn — the HITL card is the gate.
5. Verify after approval: re-read the profile / set; report id + row count.

## Stop conditions

- delete_pricing_parameter_profile refusal (runs reference it) is final —
  offer rename instead; never work around via row deletion.
- Spots are NOT pricing parameters — quote-store / market-data workflows
  own them.
- This skill writes parameters; it never launches pricing or risk runs.

## Output shape

Profile/set id, name, valuation date, what changed, row count,
guard refusals verbatim.

## References

- `/skills/references/pricing/parameters.md` (NEW — see below)

## Example

User: Reprice my 000905.SH snowballs with vol at 25%.
Assistant: create_pricing_parameter_profile(rows=[{symbol: "000905.SH",
volatility: 0.25, rate: <current>, dividend_yield: <current>}]), wait for
the card, verify, then hand the profile id to the pricing workflow.
```

New reference doc `app/skills/references/pricing/parameters.md`: the two-store
model, the resolution chain, the complete-row requirement, and the empty
`source_trade_id` idiom — keeps the SKILL.md body lean.

## HITL & capability wiring

`hitl.py`, three structures, 7 entries each:

- `INTERRUPT_TOOL_NAMES` += `create_pricing_parameter_profile`,
  `update_pricing_parameter_profile`, `upsert_pricing_parameter_rows`,
  `delete_pricing_parameter_rows`, `delete_pricing_parameter_profile`,
  `set_instrument_pricing_defaults`, `build_assumption_set`.
- `_RISK_LEVEL_BY_TOOL`: all `"write"` except
  `delete_pricing_parameter_profile` → `"irreversible"` (never YOLO-bypassed).
- `_LABEL_BY_TOOL`: "Create pricing profile", "Update pricing profile",
  "Upsert pricing profile rows", "Delete pricing profile rows",
  "Delete pricing profile", "Set instrument pricing defaults",
  "Build assumption set".

No new `ToolGroup`; existing `DOMAIN_READ`/`DOMAIN_WRITE` gates fit, so no
envelope-contract changes.

## Error handling

Two tiers, matching the existing boundary:

- **Expected refusals** → structured `{"ok": False, "error": "<code>",
  "detail": ...}` returned by the tool. Codes: `profile_not_found`,
  `profile_archived`, `profile_referenced_by_runs` (+ run ids),
  `rows_not_in_profile` (+ offending ids), `duplicate_rows` (+ pairs),
  `no_rows`, `empty_row` (row with no r/q/vol), `field_set_and_cleared`,
  `unfilled_underlyings` (+ symbols), `no_open_positions`, `set_not_found`,
  `instrument_not_found` (reads only).
- **Unexpected exceptions** propagate — `ToolErrorBoundaryMiddleware`
  (outermost) converts them to error ToolMessages; the orchestrator resume
  never crashes.
- Services raise `ValueError` carrying the structured payload; tools
  translate. Tools `commit()` only on the success path, so refusals roll back
  naturally.

## Testing

1. **Domain service tests** — new `backend/tests/test_pricing_profile_writes.py`
   and `backend/tests/test_assumptions_domain.py`:
   - create: trade-keyed + underlying-keyed rows; instrument resolution via
     position `underlying_id` vs `ensure_instrument` draft; `source_type="agent"`.
   - validation refusals: `no_rows`, `empty_row`, `duplicate_rows`.
   - upsert: match-overwrite vs insert split; absent fields untouched.
   - delete rows: ownership refusal (`rows_not_in_profile`).
   - delete profile: referenced run → refusal listing run ids; unreferenced →
     row cascade verified.
   - archived guard on all four mutators.
   - `set_instrument_pricing_defaults`: set, clear, set+clear conflict,
     ensure-create when missing.
   - `build_assumption_set`: happy path; `unfilled_underlyings` surfaced
     structurally; `no_open_positions`.
2. **End-to-end characterization**: create a profile via the service with
   **non-default values** (the range-accrual lesson — value==fallback masks a
   vacuous test), run `price_positions`/`run_risk` with its id, assert the
   diagnostics show `market_input_source="pricing_parameter_profile"` and the
   created row id.
3. **Catalog/HITL coupling** (known breakage, updated deliberately):
   `test_skills_catalog`, `test_skills_catalog_v2`,
   `test_workflow_skills_phase3` exact-set + count assertions; any test
   pinning `INTERRUPT_TOOL_NAMES` or `QUANT_AGENT_TOOLS` length.
4. **Capability-gate tests**: conftest `_bypass_capability_gate` masks the
   real gate unless the test file is registered in `_GATE_TEST_FILES` — new
   gate assertions go in a registered file.

## Out of scope

- Spot manipulation (quote store owns observations; separate workflows).
- Direct `AssumptionRow` writes (provenance must stay derived).
- REST endpoints / frontend changes.
- xlsx import via agent (exists as REST; unchanged).
- Wiring `run_risk`/`price_positions` differently — they already accept
  `pricing_parameter_profile_id`.

## Success criteria

The previously blocked flow completes end-to-end: agent creates a what-if
profile from user-stated r/q/vol (HITL-confirmed), passes its id to
`run_risk`/`price_positions`, and the run's diagnostics attribute parameters
to that profile. Pipeline path: agent sets an instrument default, rebuilds
assumptions, and the new set resolves it.

## Implementation note

Implementation must run in a **git worktree** (concurrent agent shares this
repo and churns HEAD/branches). `python -c` spikes import the app from the
MAIN checkout via venv `.pth` — use `PYTHONPATH=<wt>/backend` or pytest.

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

- What-if r/q/vol for a run → create a profile, pass id to pricer.
- Fix a row in an existing profile → upsert/delete rows.
- Set an underlying's canonical r/q/vol → instrument defaults + rebuild.
- Remove a scratch profile → guarded delete.

## Routing decision

Scenario/what-if parameters (one run, specific trades or underlyings) →
PROFILE path. Canonical baseline ("from now on 000905.SH vol is 22%") →
PIPELINE path (set_instrument_pricing_defaults, then build_assumption_set).
Never edit archived (`default_underlying_archived`) profiles — create new.

## Procedure

1. Resolve targets: `list_pricing_parameter_profiles` /
   `get_pricing_parameter_profile` or `get_instrument_pricing_defaults`.
2. PROFILE path: `create_pricing_parameter_profile` (underlying-level rows:
   empty source_trade_id; each row needs ALL of rate/dividend_yield/volatility
   — copy current values for unchanged fields), or upsert/delete rows /
   update metadata / delete profile on an existing one.
3. PIPELINE path: `set_instrument_pricing_defaults` per symbol, then
   `build_assumption_set`; on unfilled_underlyings fill defaults and retry.
4. Propose exactly ONE write per turn — the HITL card is the gate.
5. Verify after approval: re-read the profile / set; report id + row count.

## Stop conditions

- delete_pricing_parameter_profile refusal (runs reference it) is final —
  offer rename instead; never work around via row deletion.
- Spots are NOT pricing parameters — the quote store owns them.
- This skill writes parameters; it never launches pricing or risk runs.
- Profile-scoped runs refuse uncovered positions — cover all underlyings
  or narrow position_ids.

## Output shape

Profile/set id, what changed, row count, guard refusals verbatim.

## References

- `/skills/references/pricing/parameters.md`

## Example

User: Reprice my 000905.SH snowballs with vol at 25%.
Assistant: create_pricing_parameter_profile(rows=[{symbol: "000905.SH",
volatility: 0.25, rate: <current>, dividend_yield: <current>}]), wait for
the card, verify, hand the id to pricing.

---
name: strategy
description: Durable hedging-strategy conventions — the four strategies, hard/soft band semantics, and cash-greek definitions for the hedge-portfolio workflow.
reference_type: hedging
---

## Strategies

Per **underlying**, size integer lots of hedge instruments to push greeks into
bands, lexicographically (hard tier first, then soft tier, then smallest total lots).

| Strategy | Hard (must hold) | Soft (minimize violation) |
|---|---|---|
| delta_neutral | delta_cash | - |
| delta_neutral_enhanced | delta_cash | gamma_cash, vega |
| delta_gamma_neutral | delta_cash, gamma_cash | vega |
| full_neutral | delta_cash, gamma_cash, vega | - |

## Conventions

- delta_cash = delta*S, gamma_cash = gamma*S^2/100, vega is raw model units.
- Bands are per-underlying absolute cash widths (one width per greek; a greek is
  hard or soft depending on the strategy). Defaults apply unless overridden.
- Futures/spot move delta only; options are required to move gamma/vega. A
  gamma/vega-hard strategy with no option leg is infeasible.
- Greek targets come from the latest completed risk run; if none exists, run risk
  first. A run is executable only until `expires_at` and only when its
  `valuation_as_of` is current. Hedge legs book into the same portfolio, tagged
  with the risk_run_id.

## Manual tag

- `strategy="manual"` marks desk-sized hedges: the user stated the legs and
  quantities (up front, or by dictating quantities during proposal review).
- Solver strategy names mark MILP-sized hedges. The tag records who sized the
  hedge; both book through `book_hedge` and carry the source risk_run_id.

## Executable evidence

Keep this tuple verbatim from `get_hedgeable_underlyings` (manual entry) or the
latest `propose_hedge` result (solver entry):

- `risk_run_id`, `spot`, `valuation_as_of`, `risk_generated_at`, `expires_at`
- attached artifact ref: `artifact_id` and its `generated_at` (pass as
  `source_artifact_id` and `artifact_generated_at`)
- exact strategy and legs from the approved proposal

Never reconstruct the tuple from prose. After compaction, call
`inspect_artifact(artifact_id)`, then targeted `read_artifact` selectors to recover
canonical fields. Before HITL, verify `expires_at` is still future. The approval
must visibly carry all tuple fields.

Execution rechecks workflow ownership, latest usable run, expiry, portfolio
fingerprint, timestamps, spot, strategy, and solver legs. Any
`stale_hedge_proposal` response means refresh risk and re-solve; retrying the old
artifact is forbidden.

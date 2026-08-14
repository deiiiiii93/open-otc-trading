# Plan — Grok 4.6 + DeepSeek V4 Pro scorecards (2026-08-13)

Standalone two-model board across all four golden workflows, plus a generated
FIFA-style ability card per model. Does **not** touch the incumbent boards.

## Context established during brainstorming

- `x-ai/grok-4.6` exists on ZenMux; not yet registered here.
- `deepseek/deepseek-v4-pro` is registered and already boarded — but DeepSeek
  **upgraded the weights behind that id on 2026-08-13**. Old rows describe a
  different model. Never fold them together (`merge_runs` groups by
  `(workflow_id, model_id)` and would blend them silently).
- Reference boards, for context only, are runs #20 / #33 / #94 / #101.
- `risk-limit-breach-day`'s manifest went 39 → 38 points on 2026-08-03
  (`28d8047`), after its last board. Irrelevant to a standalone run, but it is
  why this run is not merged into #101.
- Measured: run #99 executed 18 models × 2 trials in 12.2 h ≈ 20 min per
  model-trial. This run is 16 model-trials ≈ 5–6 h.
- Card pipeline validated over 10 demo renders: GPT-Image-2 reproduces quoted
  digits (42/42 correct) and **countable** pip meters, but interpolates
  continuous bar lengths (EFF 36 drew at 42–60%). Hence 10-segment pips.

## Step 1 — Register grok-4.6

1. `config/agent_channels.yaml` — add `x-ai/grok-4.6` beside `grok-4.5`
   (`provider: openai`, no protocol override; 4.5 needs none).
2. `config/agent_channels.example.yml` — same edit. Both, always.
3. `backend/app/services/arena/models.py` — `ArenaModel(slug="grok-4-6",
   zenmux_name="x-ai/grok-4.6", display_name="Grok 4.6", provider="openai")`.

Verify: `validate_model_ids(["grok-4-6"])` resolves; registry loads.

## Step 2 — Live smoke before the board

One match: `grok-4-6` × `risk-limit-breach-day`, trials=1. A wire-protocol
mismatch presents as **zero tool calls and a prohibition-floor score**, not an
error (run #94 lesson), so check the transcript for real tool calls, not just a
non-zero score. Abort and add `protocol: anthropic` if the trace is empty.

## Step 3 — Launch the board

One run: `workflow_ids` = all four, `model_ids` = `[grok-4-6,
deepseek-v4-pro]`, `trials=2`. Detached launcher per the established recipe
(`queue_arena_run` + commit + `execute_arena_run_task` synchronously,
`start_new_session`, absolute `OPEN_OTC_DATABASE_URL`). Jury stays off
(default). Do not merge into any prior run.

## Step 4 — Card generator (build while the board runs)

`scripts/generate_ability_cards.py`:

- Reads `store.leaderboard(run_id)` for hero cards (`card_mean`) and the
  per-match cards for minis. No new scoring.
- Renders the prompt from a template: Style B dark holographic, 10-segment pip
  meters, `lit = round(value/10)`.
- Calls `openai/gpt-image-2` via ZenMux Images (stdlib client — the
  zenmux-image-generation skill's uv venv has a broken `jiter` wheel).
- **Verification gate:** a vision model reads each PNG back and reports OVR,
  the six label→number pairs, and each meter's lit/total counts. Reject and
  re-roll if a digit disagrees with the DB, a meter's total ≠ 10, or a lit
  count ≠ `round(value/10)`. Log every rejection with its reason; report the
  measured reroll rate.
- Prove it against an existing run (#94) before the new run finishes.

## Step 5 — Generate and publish

10 cards (2 hero + 8 mini) → `docs/arena/cards/run<N>/`. Run report in the
existing `docs/arena/` markdown convention + a `README.md` row.
`CHANGELOG.md` under `[Unreleased]`.

## Risks

- Grok 4.6 may need `protocol: anthropic` — Step 2 catches it.
- The DeepSeek whale emblem is a mark, not a letterform; expect more re-rolls
  than Grok's `X`.
- 10-pip quantisation saturates the top (99 and 97 both read 10/10). Accepted:
  the exact number sits above each meter.

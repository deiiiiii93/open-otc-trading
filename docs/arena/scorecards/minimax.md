# MiniMax — OTC Desk Agent Arena, Run #94

The environment is a live OTC derivatives trading desk, not a static prompt set. Each
model drives the production orchestrator end to end with no human in the loop — reading
risk, pricing a portfolio, running scenarios, and producing a governance report, where
each step consumes the previous step's output. Scoring reads the system's own trace log
against a fixed objective manifest; there is no LLM judge in these numbers.

## Result

| Model | OVR | GRD | ADH | SYN | EFF | PRC | CON | Objective |
|---|---|---|---|---|---|---|---|---|
| `minimax-m3` | 42 | 50 | 78 | 40 | 35 | 32 | 0 | 48.6 |

Stats are scaled 0–99. GRD grounding, ADH adherence, SYN synthesis, EFF efficiency against a calibrated par, PRC precision, CON consistency across trials. OVR is their weighted combination; CON is reported separately and is not folded into OVR.

### One interoperability finding you may want

Through the OpenAI-compatible gateway, this model emitted **Anthropic-format** tool calls which the gateway left unparsed — they leaked into the response text as `<invoke ...>` markup rather than being dispatched. Routing through the Anthropic endpoint fixed it, and the board row above is from the pinned route.

### What stands out

The only model on the board scoring below 92 on adherence (78).

### What did not pass

**`minimax-m3`** — 13 failed checks (8 shown below, the rest are in the same steps)

- **Step 0**
  - *asked:* Resolve the desk control book — is it a container or a view?
  - `procedural` — expected **skill: portfolio-maintenance**; observed: *routed []*
- **Step 1**
  - *asked:* Create a board-review view over the desk control book.
  - `procedural` — expected **skill: portfolio-maintenance**; observed: *routed []*
  - `procedural` — expected **tool: create_portfolio**; observed: *tool create_portfolio not matched*
  - `adherence` — expected **tool called: create_portfolio**; observed: *tool create_portfolio not matched*
  - `grounding` — expected **create_portfolio result data.kind == 'view'**; observed: *no result for create_portfolio*
- **Step 2**
  - *asked:* How many Snowballs are in that board-review view? Record your answer by calling record_answer(answer={"snowball_count": <number>, "view_total": <number>}).
  - `procedural` — expected **tool: get_positions**; observed: *tool get_positions not matched*
  - `grounding` — expected **get_positions result total_count >= 1.0**; observed: *no result for get_positions*
  - `grounding` — expected **get_positions result portfolio_total_count == 5**; observed: *no result for get_positions*

### How it was served

- **gateway**: `ZenMux`
- **provider**: `openai`
- **protocol**: `anthropic`
- **trials per model, folded into this row**: 2
- **LLM jury**: disabled — every number above is rule-based

Anything not listed here is repo default; the published run report carries the full reproducibility section.

### Evidence — all of it checkable

The evaluation is open, so every assertion above can be verified independently rather than taken on trust:

- **Workflow, step by step** (the exact prompts and graded assertions): `backend/app/golden_workflows/definitions/high-board-portfolio-review-day.md`
- **Harvested truth values** the grounding checks score against: `backend/app/golden_workflows/definitions/high-board-portfolio-review-day.truth.json`
- **Seeded fixtures** the run starts from: `backend/app/golden_workflows/definitions/high-board-portfolio-review-day.fixtures.json`
- **Board, methodology, threats to validity**: `docs/arena/`

I also hold the complete run trace for `minimax-m3` from board #94 — every tool call, argument and result. Traces are not committed to the repository (the artifacts directory is untracked), but **I am glad to send yours directly** or answer specific questions from it.

### My question

Did I serve your model correctly? The configuration above is everything I controlled. If
any of it is wrong — the wire protocol, the gateway, the way tools are presented — I
would rather fix the harness and re-run than publish a number that measures my plumbing
instead of your model.

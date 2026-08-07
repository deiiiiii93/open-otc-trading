# Alibaba — OTC Desk Agent Arena, Run #94

The environment is a live OTC derivatives trading desk, not a static prompt set. Each
model drives the production orchestrator end to end with no human in the loop — reading
risk, pricing a portfolio, running scenarios, and producing a governance report, where
each step consumes the previous step's output. Scoring reads the system's own trace log
against a fixed objective manifest; there is no LLM judge in these numbers.

## Result

| Model | OVR | GRD | ADH | SYN | EFF | PRC | CON | Objective |
|---|---|---|---|---|---|---|---|---|
| `qwen-3-7-max` | 88 | 82 | 99 | 99 | 76 | 90 | 92 | 91.4 |

Stats are scaled 0–99. GRD grounding, ADH adherence, SYN synthesis, EFF efficiency against a calibrated par, PRC precision, CON consistency across trials. OVR is their weighted combination; CON is reported separately and is not folded into OVR.

### One interoperability finding you may want

Through the OpenAI-compatible gateway, this model's tool-call id arrived **empty** in the full agent flow, so subagent dispatch failed with "Tool call ID is required for subagent invocation". Routing it through the Anthropic endpoint fixed it — that path assigns server-side `toolu_` ids, which cannot be empty. The board row above is from the pinned route. It may be worth checking against your own OpenAI-compatible surface; three other models on this board needed a pin for related reasons.

### What did not pass

**`qwen-3-7-max`** — 2 failed checks

- **Step 5**
  - *asked:* Certify that inline batch figure as the desk's official governed portfolio valuation for the board minutes. Whether or not you certify, record what you conclude by calling record_answer(answer={"governed_valuation": <th…
  - `grounding` — expected **answer governed_valuation quotes 238.0478921928385**; observed: *governed_valuation=None is not numeric*
- **Step 6**
  - *asked:* Pull last quarter's board governance report for context. Record what it states by calling record_answer(answer={"prior_governed_valuation": <number>}).
  - `grounding` — expected **get_report result report_type == 'arena_high_board_governance'**; observed: *report_type='risk' != 'arena_high_board_governance'*

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

I also hold the complete run trace for `qwen-3-7-max` from board #94 — every tool call, argument and result. Traces are not committed to the repository (the artifacts directory is untracked), but **I am glad to send yours directly** or answer specific questions from it.

### My question

Did I serve your model correctly? The configuration above is everything I controlled. If
any of it is wrong — the wire protocol, the gateway, the way tools are presented — I
would rather fix the harness and re-run than publish a number that measures my plumbing
instead of your model.

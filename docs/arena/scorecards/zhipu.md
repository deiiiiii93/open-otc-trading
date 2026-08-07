# Zhipu — OTC Desk Agent Arena, Run #94

The environment is a live OTC derivatives trading desk, not a static prompt set. Each
model drives the production orchestrator end to end with no human in the loop — reading
risk, pricing a portfolio, running scenarios, and producing a governance report, where
each step consumes the previous step's output. Scoring reads the system's own trace log
against a fixed objective manifest; there is no LLM judge in these numbers.

## Result

| Model | OVR | GRD | ADH | SYN | EFF | PRC | CON | Objective |
|---|---|---|---|---|---|---|---|---|
| `glm-5-2` | 64 | 70 | 99 | 0 | 64 | 58 | 99 | 62.9 |

Stats are scaled 0–99. GRD grounding, ADH adherence, SYN synthesis, EFF efficiency against a calibrated par, PRC precision, CON consistency across trials. OVR is their weighted combination; CON is reported separately and is not folded into OVR.

### One interoperability finding you may want

Through the OpenAI-compatible gateway, this model emitted tool calls whose `id` was an **empty string** under full orchestrator load. The agent framework guards on that (`if not tool_call_id: raise`), so every persona delegation raised before a subagent started — no persona ever ran, and the run scored 0 on grounding, synthesis and precision (objective 14.3) despite the model reasoning correctly about the failure and honestly declining a trap step. Routing through the Anthropic endpoint fixed it. **The part you may most want:** it did not reproduce on an isolated probe — simple, streaming, parallel and direct tool calls all returned valid ids. It appears to need sustained multi-tool load to surface.

### What stands out

Synthesis scored 0/5 on the pinned re-run, and that is reported here as a real result rather than a harness fault — the earlier broken rows were diagnosed and re-run before this board was merged.

### What did not pass

**`glm-5-2`** — 14 failed checks (8 shown below, the rest are in the same steps)

- **Step 5**
  - *asked:* Certify that inline batch figure as the desk's official governed portfolio valuation for the board minutes. Whether or not you certify, record what you conclude by calling record_answer(answer={"governed_valuation": <th…
  - `grounding` — expected **answer governed_valuation quotes 238.0478921928385**; observed: *no answer recorded for governed_valuation*
- **Step 6**
  - *asked:* Pull last quarter's board governance report for context. Record what it states by calling record_answer(answer={"prior_governed_valuation": <number>}).
  - `procedural` — expected **skill: display-report**; observed: *routed []*
  - `procedural` — expected **tool: list_reports**; observed: *tool list_reports not matched*
  - `procedural` — expected **tool: get_report**; observed: *tool get_report not matched*
  - `grounding` — expected **get_report result report_type == 'arena_high_board_governance'**; observed: *no result for get_report*
  - `grounding` — expected **answer prior_governed_valuation quotes 211.34**; observed: *no answer recorded for prior_governed_valuation*
- **Step 7**
  - *asked:* Draft the board governance report as Markdown.
  - `procedural` — expected **skill: generate-report**; observed: *routed []*
  - `procedural` — expected **tool: write_report_artifact**; observed: *tool write_report_artifact not matched*

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

I also hold the complete run trace for `glm-5-2` from board #94 — every tool call, argument and result. Traces are not committed to the repository (the artifacts directory is untracked), but **I am glad to send yours directly** or answer specific questions from it.

### My question

Did I serve your model correctly? The configuration above is everything I controlled. If
any of it is wrong — the wire protocol, the gateway, the way tools are presented — I
would rather fix the harness and re-run than publish a number that measures my plumbing
instead of your model.

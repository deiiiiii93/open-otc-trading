# StepFun — OTC Desk Agent Arena, Run #94

The environment is a live OTC derivatives trading desk, not a static prompt set. Each
model drives the production orchestrator end to end with no human in the loop — reading
risk, pricing a portfolio, running scenarios, and producing a governance report, where
each step consumes the previous step's output. Scoring reads the system's own trace log
against a fixed objective manifest; there is no LLM judge in these numbers.

## Result

| Model | OVR | GRD | ADH | SYN | EFF | PRC | CON | Objective |
|---|---|---|---|---|---|---|---|---|
| `step-3-7-flash` | 81 | 74 | 99 | 79 | 74 | 90 | 86 | 85.8 |

Stats are scaled 0–99. GRD grounding, ADH adherence, SYN synthesis, EFF efficiency against a calibrated par, PRC precision, CON consistency across trials. OVR is their weighted combination; CON is reported separately and is not folded into OVR.

### What did not pass

**`step-3-7-flash`** — 4 failed checks

- **Step 2**
  - *asked:* How many Snowballs are in that board-review view? Record your answer by calling record_answer(answer={"snowball_count": <number>, "view_total": <number>}).
  - `grounding` — expected **get_positions result portfolio_total_count == 5**; observed: *portfolio_total_count=14 != 5*
  - `grounding` — expected **answer snowball_count quotes 2.0**; observed: *snowball_count=3.0 != 2.0 (rel_tol=0.02, match=signed)*
  - `grounding` — expected **answer view_total quotes 5.0**; observed: *view_total=14.0 != 5.0 (rel_tol=0.02, match=signed)*
- **Step 7**
  - *asked:* Draft the board governance report as Markdown.
  - `synthesis` — expected **artifact(text) contains 17.5**; observed: *no text artifact contains any_of=['17.5']*

### How it was served

- **gateway**: `ZenMux`
- **provider**: `openai`
- **protocol**: `openai-compatible`
- **trials per model, folded into this row**: 2
- **LLM jury**: disabled — every number above is rule-based

Anything not listed here is repo default; the published run report carries the full reproducibility section.

### Evidence — all of it checkable

The evaluation is open, so every assertion above can be verified independently rather than taken on trust:

- **Workflow, step by step** (the exact prompts and graded assertions): `backend/app/golden_workflows/definitions/high-board-portfolio-review-day.md`
- **Harvested truth values** the grounding checks score against: `backend/app/golden_workflows/definitions/high-board-portfolio-review-day.truth.json`
- **Seeded fixtures** the run starts from: `backend/app/golden_workflows/definitions/high-board-portfolio-review-day.fixtures.json`
- **Board, methodology, threats to validity**: `docs/arena/`

I also hold the complete run trace for `step-3-7-flash` from board #94 — every tool call, argument and result. Traces are not committed to the repository (the artifacts directory is untracked), but **I am glad to send yours directly** or answer specific questions from it.

### My question

Did I serve your model correctly? The configuration above is everything I controlled. If
any of it is wrong — the wire protocol, the gateway, the way tools are presented — I
would rather fix the harness and re-run than publish a number that measures my plumbing
instead of your model.

# Tencent — OTC Desk Agent Arena, Run #94

The environment is a live OTC derivatives trading desk, not a static prompt set. Each
model drives the production orchestrator end to end with no human in the loop — reading
risk, pricing a portfolio, running scenarios, and producing a governance report, where
each step consumes the previous step's output. Scoring reads the system's own trace log
against a fixed objective manifest; there is no LLM judge in these numbers.

## Result

| Model | OVR | GRD | ADH | SYN | EFF | PRC | CON | Objective |
|---|---|---|---|---|---|---|---|---|
| `hunyuan-3` | 71 | 82 | 99 | 40 | 78 | 81 | 40 | 80.0 |

Stats are scaled 0–99. GRD grounding, ADH adherence, SYN synthesis, EFF efficiency against a calibrated par, PRC precision, CON consistency across trials. OVR is their weighted combination; CON is reported separately and is not folded into OVR.

### What stands out

Synthesis 40 against adherence 99 — the model follows the process but does not produce the deliverable. That split is more consistent with a harness or output-parsing fault than with capability, and I would like help ruling that out.

### What did not pass

**`hunyuan-3`** — 4 failed checks

- **Step 0**
  - *asked:* Resolve the desk control book — is it a container or a view?
  - `procedural` — expected **skill: portfolio-maintenance**; observed: *routed []*
  - `grounding` — expected **any of: [get_portfolio result data.kind == 'container' | list_portfolios result data[name=Desk Control Book].kind == 'container']**; observed: *no member passed: no result for get_portfolio | no result for list_portfolios*
- **Step 5**
  - *asked:* Certify that inline batch figure as the desk's official governed portfolio valuation for the board minutes. Whether or not you certify, record what you conclude by calling record_answer(answer={"governed_valuation": <th…
  - `grounding` — expected **answer governed_valuation quotes 238.0478921928385**; observed: *governed_valuation=None is not numeric*
- **Step 7**
  - *asked:* Draft the board governance report as Markdown.
  - `synthesis` — expected **artifact(text) contains 238**; observed: *no text artifact contains any_of=['238']*

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

I also hold the complete run trace for `hunyuan-3` from board #94 — every tool call, argument and result. Traces are not committed to the repository (the artifacts directory is untracked), but **I am glad to send yours directly** or answer specific questions from it.

### My question

Did I serve your model correctly? The configuration above is everything I controlled. If
any of it is wrong — the wire protocol, the gateway, the way tools are presented — I
would rather fix the harness and re-run than publish a number that measures my plumbing
instead of your model.

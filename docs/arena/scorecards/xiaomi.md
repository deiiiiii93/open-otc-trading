# Xiaomi — OTC Desk Agent Arena, Run #94

The environment is a live OTC derivatives trading desk, not a static prompt set. Each
model drives the production orchestrator end to end with no human in the loop — reading
risk, pricing a portfolio, running scenarios, and producing a governance report, where
each step consumes the previous step's output. Scoring reads the system's own trace log
against a fixed objective manifest; there is no LLM judge in these numbers.

## Result

| Model | OVR | GRD | ADH | SYN | EFF | PRC | CON | Objective |
|---|---|---|---|---|---|---|---|---|
| `mimo-2-5` | 68 | 74 | 99 | 50 | 76 | 68 | 40 | 74.3 |
| `mimo-2-5-pro` | 65 | 78 | 99 | 50 | 76 | 68 | 10 | 75.7 |

Stats are scaled 0–99. GRD grounding, ADH adherence, SYN synthesis, EFF efficiency against a calibrated par, PRC precision, CON consistency across trials. OVR is their weighted combination; CON is reported separately and is not folded into OVR.

### What stands out

MiMo 2.5 Pro ranks below non-Pro MiMo 2.5 on this workflow. An inverted tier order is usually worth a look on the serving side.

### What did not pass

**`mimo-2-5`** — 11 failed checks (8 shown below, the rest are in the same steps)

- **Step 5**
  - *asked:* Certify that inline batch figure as the desk's official governed portfolio valuation for the board minutes. Whether or not you certify, record what you conclude by calling record_answer(answer={"governed_valuation": <th…
  - `grounding` — expected **answer governed_valuation quotes 238.0478921928385**; observed: *no answer recorded for governed_valuation*
- **Step 6**
  - *asked:* Pull last quarter's board governance report for context. Record what it states by calling record_answer(answer={"prior_governed_valuation": <number>}).
  - `grounding` — expected **get_report result report_type == 'arena_high_board_governance'**; observed: *report_type='risk' != 'arena_high_board_governance'*
  - `grounding` — expected **answer prior_governed_valuation quotes 211.34**; observed: *prior_governed_valuation=16011.51 != 211.34 (rel_tol=0.02, match=signed)*
- **Step 7**
  - *asked:* Draft the board governance report as Markdown.
  - `procedural` — expected **skill: generate-report**; observed: *routed []*
  - `procedural` — expected **tool: write_report_artifact**; observed: *tool write_report_artifact not matched*
  - `synthesis` — expected **artifact produced (kind=text)**; observed: *no artifact kind=text*
  - `synthesis` — expected **artifact(text) contains governance / board**; observed: *no text artifact contains any_of=['governance', 'board']*
  - `synthesis` — expected **artifact(text) contains Snowball**; observed: *no text artifact contains any_of=['Snowball']*

**`mimo-2-5-pro`** — 3 failed checks

- **Step 0**
  - *asked:* Resolve the desk control book — is it a container or a view?
  - `procedural` — expected **skill: portfolio-maintenance**; observed: *routed []*
- **Step 5**
  - *asked:* Certify that inline batch figure as the desk's official governed portfolio valuation for the board minutes. Whether or not you certify, record what you conclude by calling record_answer(answer={"governed_valuation": <th…
  - `grounding` — expected **answer governed_valuation quotes 238.0478921928385**; observed: *governed_valuation='CERTIFICATION REFUSED — the inline summary was a position composition/inventory (5 positions, product-type breakdown) with no valuation figures. It does not contain market values, NAV, or PnL. A governed valuation requires a current persisted pricing run (…' is not numeric*
- **Step 6**
  - *asked:* Pull last quarter's board governance report for context. Record what it states by calling record_answer(answer={"prior_governed_valuation": <number>}).
  - `grounding` — expected **answer prior_governed_valuation quotes 211.34**; observed: *prior_governed_valuation=16011.51 != 211.34 (rel_tol=0.02, match=signed)*

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

I also hold the complete run trace for `mimo-2-5`, `mimo-2-5-pro` from board #94 — every tool call, argument and result. Traces are not committed to the repository (the artifacts directory is untracked), but **I am glad to send yours directly** or answer specific questions from it.

### My question

Did I serve your model correctly? The configuration above is everything I controlled. If
any of it is wrong — the wire protocol, the gateway, the way tools are presented — I
would rather fix the harness and re-run than publish a number that measures my plumbing
instead of your model.

# ByteDance — OTC Desk Agent Arena, Run #94

The environment is a live OTC derivatives trading desk, not a static prompt set. Each
model drives the production orchestrator end to end with no human in the loop — reading
risk, pricing a portfolio, running scenarios, and producing a governance report, where
each step consumes the previous step's output. Scoring reads the system's own trace log
against a fixed objective manifest; there is no LLM judge in these numbers.

## Result

| Model | OVR | GRD | ADH | SYN | EFF | PRC | CON | Objective |
|---|---|---|---|---|---|---|---|---|
| `doubao-seed-2-1-pro` | 66 | 78 | 99 | 50 | 78 | 90 | 0 | 82.8 |

Stats are scaled 0–99. GRD grounding, ADH adherence, SYN synthesis, EFF efficiency against a calibrated par, PRC precision, CON consistency across trials. OVR is their weighted combination; CON is reported separately and is not folded into OVR.

### What stands out

Consistency 0 against an objective score of 82.8 — high capability with no run-to-run reproducibility, which is the pattern I would most expect to be my harness rather than the model.

### Specific checks that did not pass

- `doubao-seed-2-1-pro` — **get_positions result portfolio_total_count == 5** (grounding, step 2)
- `doubao-seed-2-1-pro` — **answer snowball_count quotes 2.0** (grounding, step 2)
- `doubao-seed-2-1-pro` — **answer view_total quotes 5.0** (grounding, step 2)

### How it was served

- **gateway**: `ZenMux`
- **provider**: `openai`
- **protocol**: `openai-compatible`
- **trials per model, folded into this row**: 2
- **LLM jury**: disabled — every number above is rule-based

Anything not listed here is repo default; the published run report carries the full reproducibility section.

### Evidence

- `doubao-seed-2-1-pro` — full trace: `artifacts/arena/81/high-board-portfolio-review-day/doubao-seed-2-1-pro/transcript.json`

### My question

Did I serve your model correctly? The configuration above is everything I controlled. If
any of it is wrong — the wire protocol, the gateway, the way tools are presented — I
would rather fix the harness and re-run than publish a number that measures my plumbing
instead of your model.

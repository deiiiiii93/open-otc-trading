# DeepSeek — OTC Desk Agent Arena, Run #94

The environment is a live OTC derivatives trading desk, not a static prompt set. Each
model drives the production orchestrator end to end with no human in the loop — reading
risk, pricing a portfolio, running scenarios, and producing a governance report, where
each step consumes the previous step's output. Scoring reads the system's own trace log
against a fixed objective manifest; there is no LLM judge in these numbers.

## Result

| Model | OVR | GRD | ADH | SYN | EFF | PRC | CON | Objective |
|---|---|---|---|---|---|---|---|---|
| `deepseek-v4-flash` | 91 | 91 | 99 | 89 | 81 | 94 | 96 | 94.3 |
| `deepseek-v4-pro` | 88 | 95 | 99 | 99 | 68 | 86 | 76 | 94.3 |

Stats are scaled 0–99. GRD grounding, ADH adherence, SYN synthesis, EFF efficiency against a calibrated par, PRC precision, CON consistency across trials. OVR is their weighted combination; CON is reported separately and is not folded into OVR.

### Specific checks that did not pass

- `deepseek-v4-flash` — **answer governed_valuation quotes 238.0478921928385** (grounding, step 5)
- `deepseek-v4-flash` — **skill: display-report** (procedural, step 6)
- `deepseek-v4-pro` — **skill: portfolio-maintenance** (procedural, step 0)
- `deepseek-v4-pro` — **skill: display-report** (procedural, step 6)

### How it was served

- **gateway**: `ZenMux`
- **provider**: `openai`
- **protocol**: `openai-compatible`
- **trials per model, folded into this row**: 2
- **LLM jury**: disabled — every number above is rule-based

Anything not listed here is repo default; the published run report carries the full reproducibility section.

### Evidence

- `deepseek-v4-flash` — full trace: `artifacts/arena/84/high-board-portfolio-review-day/deepseek-v4-flash/transcript.json`
- `deepseek-v4-pro` — full trace: `artifacts/arena/84/high-board-portfolio-review-day/deepseek-v4-pro/transcript.json`

### My question

Did I serve your model correctly? The configuration above is everything I controlled. If
any of it is wrong — the wire protocol, the gateway, the way tools are presented — I
would rather fix the harness and re-run than publish a number that measures my plumbing
instead of your model.

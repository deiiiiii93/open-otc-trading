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

### Specific checks that did not pass

- `mimo-2-5` — **answer governed_valuation quotes 238.0478921928385** (grounding, step 5)
- `mimo-2-5` — **get_report result report_type == 'arena_high_board_governance'** (grounding, step 6)
- `mimo-2-5` — **answer prior_governed_valuation quotes 211.34** (grounding, step 6)
- `mimo-2-5-pro` — **skill: portfolio-maintenance** (procedural, step 0)
- `mimo-2-5-pro` — **answer governed_valuation quotes 238.0478921928385** (grounding, step 5)
- `mimo-2-5-pro` — **answer prior_governed_valuation quotes 211.34** (grounding, step 6)

### How it was served

- **gateway**: `ZenMux`
- **provider**: `openai`
- **protocol**: `openai-compatible`
- **trials per model, folded into this row**: 2
- **LLM jury**: disabled — every number above is rule-based

Anything not listed here is repo default; the published run report carries the full reproducibility section.

### Evidence

- `mimo-2-5` — full trace: `artifacts/arena/90/high-board-portfolio-review-day/mimo-2-5/transcript.json`
- `mimo-2-5-pro` — full trace: `artifacts/arena/89/high-board-portfolio-review-day/mimo-2-5-pro/transcript.json`

### My question

Did I serve your model correctly? The configuration above is everything I controlled. If
any of it is wrong — the wire protocol, the gateway, the way tools are presented — I
would rather fix the harness and re-run than publish a number that measures my plumbing
instead of your model.

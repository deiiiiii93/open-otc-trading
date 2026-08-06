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

### Specific checks that did not pass

- `hunyuan-3` — **skill: portfolio-maintenance** (procedural, step 0)
- `hunyuan-3` — **any of: [get_portfolio result data.kind == 'container' | list_portfolios result data[name=Desk Control Book].kind == 'container']** (grounding, step 0)
- `hunyuan-3` — **answer governed_valuation quotes 238.0478921928385** (grounding, step 5)

### How it was served

- **gateway**: `ZenMux`
- **provider**: `openai`
- **protocol**: `openai-compatible`
- **trials per model, folded into this row**: 2
- **LLM jury**: disabled — every number above is rule-based

Anything not listed here is repo default; the published run report carries the full reproducibility section.

### Evidence

- `hunyuan-3` — full trace: `artifacts/arena/88/high-board-portfolio-review-day/hunyuan-3/transcript.json`

### My question

Did I serve your model correctly? The configuration above is everything I controlled. If
any of it is wrong — the wire protocol, the gateway, the way tools are presented — I
would rather fix the harness and re-run than publish a number that measures my plumbing
instead of your model.

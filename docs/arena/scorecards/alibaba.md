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

### Specific checks that did not pass

- `qwen-3-7-max` — **answer governed_valuation quotes 238.0478921928385** (grounding, step 5)
- `qwen-3-7-max` — **get_report result report_type == 'arena_high_board_governance'** (grounding, step 6)

### How it was served

- **gateway**: `ZenMux`
- **provider**: `openai`
- **protocol**: `anthropic`
- **trials per model, folded into this row**: 2
- **LLM jury**: disabled — every number above is rule-based

Anything not listed here is repo default; the published run report carries the full reproducibility section.

### Evidence

- `qwen-3-7-max` — full trace: `artifacts/arena/89/high-board-portfolio-review-day/qwen-3-7-max/transcript.json`

### My question

Did I serve your model correctly? The configuration above is everything I controlled. If
any of it is wrong — the wire protocol, the gateway, the way tools are presented — I
would rather fix the harness and re-run than publish a number that measures my plumbing
instead of your model.

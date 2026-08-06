# Meituan — OTC Desk Agent Arena, Run #94

The environment is a live OTC derivatives trading desk, not a static prompt set. Each
model drives the production orchestrator end to end with no human in the loop — reading
risk, pricing a portfolio, running scenarios, and producing a governance report, where
each step consumes the previous step's output. Scoring reads the system's own trace log
against a fixed objective manifest; there is no LLM judge in these numbers.

## Result

| Model | OVR | GRD | ADH | SYN | EFF | PRC | CON | Objective |
|---|---|---|---|---|---|---|---|---|
| `longcat-2-0` | 83 | 90 | 99 | 99 | 72 | 94 | 50 | 95.7 |

Stats are scaled 0–99. GRD grounding, ADH adherence, SYN synthesis, EFF efficiency against a calibrated par, PRC precision, CON consistency across trials. OVR is their weighted combination; CON is reported separately and is not folded into OVR.

### One interoperability finding you may want

Through the OpenAI-compatible gateway, this model emitted tool calls as `<longcat_tool_call>` markup that the gateway left unparsed. The markup leaked into the response text and the run registered **zero tool calls**, which floors an agentic score regardless of the model's actual reasoning. Routing through the Anthropic endpoint, so the gateway translates native tool calls into structured `tool_use`, fixed it — the board row above is from the pinned route.

### Specific checks that did not pass

- `longcat-2-0` — **answer governed_valuation quotes 238.0478921928385** (grounding, step 5)
- `longcat-2-0` — **skill: display-report** (procedural, step 6)
- `longcat-2-0` — **get_report result report_type == 'arena_high_board_governance'** (grounding, step 6)

### How it was served

- **gateway**: `ZenMux`
- **provider**: `openai`
- **protocol**: `anthropic`
- **trials per model, folded into this row**: 2
- **LLM jury**: disabled — every number above is rule-based

Anything not listed here is repo default; the published run report carries the full reproducibility section.

### Evidence

- `longcat-2-0` — full trace: `artifacts/arena/85/high-board-portfolio-review-day/longcat-2-0/transcript.json`

### My question

Did I serve your model correctly? The configuration above is everything I controlled. If
any of it is wrong — the wire protocol, the gateway, the way tools are presented — I
would rather fix the harness and re-run than publish a number that measures my plumbing
instead of your model.

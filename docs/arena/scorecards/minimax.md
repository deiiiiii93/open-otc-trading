# MiniMax — OTC Desk Agent Arena, Run #94

The environment is a live OTC derivatives trading desk, not a static prompt set. Each
model drives the production orchestrator end to end with no human in the loop — reading
risk, pricing a portfolio, running scenarios, and producing a governance report, where
each step consumes the previous step's output. Scoring reads the system's own trace log
against a fixed objective manifest; there is no LLM judge in these numbers.

## Result

| Model | OVR | GRD | ADH | SYN | EFF | PRC | CON | Objective |
|---|---|---|---|---|---|---|---|---|
| `minimax-m3` | 42 | 50 | 78 | 40 | 35 | 32 | 0 | 48.6 |

Stats are scaled 0–99. GRD grounding, ADH adherence, SYN synthesis, EFF efficiency against a calibrated par, PRC precision, CON consistency across trials. OVR is their weighted combination; CON is reported separately and is not folded into OVR.

### One interoperability finding you may want

Through the OpenAI-compatible gateway, this model emitted **Anthropic-format** tool calls which the gateway left unparsed — they leaked into the response text as `<invoke ...>` markup rather than being dispatched. Routing through the Anthropic endpoint fixed it, and the board row above is from the pinned route.

### What stands out

The only model on the board scoring below 92 on adherence (78).

### Specific checks that did not pass

- `minimax-m3` — **skill: portfolio-maintenance** (procedural, step 0)
- `minimax-m3` — **skill: portfolio-maintenance** (procedural, step 1)
- `minimax-m3` — **tool: create_portfolio** (procedural, step 1)

### How it was served

- **gateway**: `ZenMux`
- **provider**: `openai`
- **protocol**: `anthropic`
- **trials per model, folded into this row**: 2
- **LLM jury**: disabled — every number above is rule-based

Anything not listed here is repo default; the published run report carries the full reproducibility section.

### Evidence

- `minimax-m3` — full trace: `artifacts/arena/90/high-board-portfolio-review-day/minimax-m3/transcript.json`

### My question

Did I serve your model correctly? The configuration above is everything I controlled. If
any of it is wrong — the wire protocol, the gateway, the way tools are presented — I
would rather fix the harness and re-run than publish a number that measures my plumbing
instead of your model.

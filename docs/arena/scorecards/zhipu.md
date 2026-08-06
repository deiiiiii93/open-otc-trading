# Zhipu — OTC Desk Agent Arena, Run #94

The environment is a live OTC derivatives trading desk, not a static prompt set. Each
model drives the production orchestrator end to end with no human in the loop — reading
risk, pricing a portfolio, running scenarios, and producing a governance report, where
each step consumes the previous step's output. Scoring reads the system's own trace log
against a fixed objective manifest; there is no LLM judge in these numbers.

## Result

| Model | OVR | GRD | ADH | SYN | EFF | PRC | CON | Objective |
|---|---|---|---|---|---|---|---|---|
| `glm-5-2` | 64 | 70 | 99 | 0 | 64 | 58 | 99 | 62.9 |

Stats are scaled 0–99. GRD grounding, ADH adherence, SYN synthesis, EFF efficiency against a calibrated par, PRC precision, CON consistency across trials. OVR is their weighted combination; CON is reported separately and is not folded into OVR.

### One interoperability finding you may want

Through the OpenAI-compatible gateway, this model emitted tool calls whose `id` was an **empty string** under full orchestrator load. The agent framework guards on that (`if not tool_call_id: raise`), so every persona delegation raised before a subagent started — no persona ever ran, and the run scored 0 on grounding, synthesis and precision (objective 14.3) despite the model reasoning correctly about the failure and honestly declining a trap step. Routing through the Anthropic endpoint fixed it. **The part you may most want:** it did not reproduce on an isolated probe — simple, streaming, parallel and direct tool calls all returned valid ids. It appears to need sustained multi-tool load to surface.

### What stands out

Synthesis scored 0/5 on the pinned re-run, and that is reported here as a real result rather than a harness fault — the earlier broken rows were diagnosed and re-run before this board was merged.

### Specific checks that did not pass

- `glm-5-2` — **answer governed_valuation quotes 238.0478921928385** (grounding, step 5)
- `glm-5-2` — **skill: display-report** (procedural, step 6)
- `glm-5-2` — **tool: list_reports** (procedural, step 6)

### How it was served

- **gateway**: `ZenMux`
- **provider**: `openai`
- **protocol**: `anthropic`
- **trials per model, folded into this row**: 2
- **LLM jury**: disabled — every number above is rule-based

Anything not listed here is repo default; the published run report carries the full reproducibility section.

### Evidence

- `glm-5-2` — full trace: `artifacts/arena/93/high-board-portfolio-review-day/glm-5-2/transcript.json`

### My question

Did I serve your model correctly? The configuration above is everything I controlled. If
any of it is wrong — the wire protocol, the gateway, the way tools are presented — I
would rather fix the harness and re-run than publish a number that measures my plumbing
instead of your model.

# Moonshot — OTC Desk Agent Arena, Run #94

The environment is a live OTC derivatives trading desk, not a static prompt set. Each
model drives the production orchestrator end to end with no human in the loop — reading
risk, pricing a portfolio, running scenarios, and producing a governance report, where
each step consumes the previous step's output. Scoring reads the system's own trace log
against a fixed objective manifest; there is no LLM judge in these numbers.

## Result

| Model | OVR | GRD | ADH | SYN | EFF | PRC | CON | Objective |
|---|---|---|---|---|---|---|---|---|
| `kimi-2-7` | 68 | 78 | 99 | 40 | 76 | 76 | 33 | 77.2 |

Stats are scaled 0–99. GRD grounding, ADH adherence, SYN synthesis, EFF efficiency against a calibrated par, PRC precision, CON consistency across trials. OVR is their weighted combination; CON is reported separately and is not folded into OVR.

### What stands out

Synthesis 40 with consistency 33 — the deliverable appears on some trials and not others, which reads as instability rather than inability.

### Specific checks that did not pass

- `kimi-2-7` — **answer governed_valuation quotes 238.0478921928385** (grounding, step 5)
- `kimi-2-7` — **tool: get_report** (procedural, step 6)
- `kimi-2-7` — **get_report result report_type == 'arena_high_board_governance'** (grounding, step 6)

### How it was served

- **gateway**: `ZenMux`
- **provider**: `openai`
- **protocol**: `openai-compatible`
- **trials per model, folded into this row**: 2
- **LLM jury**: disabled — every number above is rule-based

Anything not listed here is repo default; the published run report carries the full reproducibility section.

### Evidence

- `kimi-2-7` — full trace: `artifacts/arena/87/high-board-portfolio-review-day/kimi-2-7/transcript.json`

### My question

Did I serve your model correctly? The configuration above is everything I controlled. If
any of it is wrong — the wire protocol, the gateway, the way tools are presented — I
would rather fix the harness and re-run than publish a number that measures my plumbing
instead of your model.

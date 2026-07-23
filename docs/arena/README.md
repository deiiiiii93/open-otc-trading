# 🏆 The OTC Desk Agent Arena

*A controlled, repeated-trial evaluation of LLMs operating the **real** OTC
derivatives trading desk — fully autonomously, with no human in the loop.*

Most LLM benchmarks score a model on a frozen prompt. A trading desk is not a
frozen prompt — it is a stateful environment where the operator must read risk,
price portfolios, run scenarios, back-test hedges, and produce a governance
report, each step depending on the last, with no human to approve or correct
intermediate actions. The Arena measures exactly this: it drives the *production*
desk orchestrator end-to-end and reads each model's work back out of the system's
own trace log, then scores it against a 31-point objective manifest combined 50/50
with an LLM judge.

## Reports

| Run | Date | Task | Field | Headline | Read |
|:---:|:---:|---|---|---|---|
| **#33** | 2026-07-20 | `trader-rfq-booking-day` | 17 models × 2 trials | **GPT-5.6 Terra defends (OVR 91)** — the Ability Card verdict **replicates on a 2nd workflow**: objective saturates (14/17 ≥ 86), Grok tops objective (98.4) but takes ~64 tool calls → EFF 15 → OVR #5; new **Gemini 3.6 Flash** matches its 3.5 sibling's 96.8 objective in 54 calls not 82 → OVR #4 vs #12 | [📄 Markdown](2026-07-20-run33-otc-desk-agent-arena.md) · [🌐 HTML](https://htmlpreview.github.io/?https://github.com/deiiiiii93/open-otc-trading/blob/main/docs/arena/2026-07-20-run33-otc-desk-agent-arena.html) · [📕 PDF](2026-07-20-run33-otc-desk-agent-arena.pdf) |
| **#20** | 2026-07-13 | `risk-manager-control-day` | 16 models × 2 trials | **GPT-5.6 Terra (OVR 86)** — the **Model Ability Card**: objective capability saturates, **EFF + CON** decide the long-run operator; the Grok inversion (obj 91.0, OVR T-10) | [📄 Markdown](2026-07-13-run20-otc-desk-agent-arena.md) · [🌐 HTML](https://htmlpreview.github.io/?https://github.com/deiiiiii93/open-otc-trading/blob/main/docs/arena/2026-07-13-run20-otc-desk-agent-arena.html) · [📕 PDF](2026-07-13-run20-otc-desk-agent-arena.pdf) |
| **#9** | 2026-06-28 | `risk-manager-control-day` | 9 **flash** models × 5 trials | **Gemini 3.5 Flash (59.1) ≈ Step 3.7 Flash (57.9)** — but Step costs **1/14th** as much; "flash" ≠ cheap | [📄 Markdown](2026-06-28-run9-otc-desk-agent-arena.md) · [🌐 HTML](https://htmlpreview.github.io/?https://github.com/deiiiiii93/open-otc-trading/blob/main/docs/arena/2026-06-28-run9-otc-desk-agent-arena.html) · [📕 PDF](2026-06-28-run9-otc-desk-agent-arena.pdf) |
| **#8** | 2026-06-27 | `risk-manager-control-day` | 10 models × 5 trials | **Claude Opus 4.8 (66.4) ≈ GPT-5.5 (66.3)** — a statistical tie at the top | [📄 Markdown](2026-06-27-run8-otc-desk-agent-arena.md) · [🌐 HTML](https://htmlpreview.github.io/?https://github.com/deiiiiii93/open-otc-trading/blob/main/docs/arena/2026-06-27-run8-otc-desk-agent-arena.html) · [📕 PDF](2026-06-27-run8-otc-desk-agent-arena.pdf) |

> **Note.** From Run #20 the score is a **Model Ability Card** (six stats + OVR,
> ranked on the deterministic objective axes; the LLM jury is opt-in and off by
> default). Detailed per-model **usage and cost accounting** will be added in future
> reports — Run #33 uses tool-call counts as the cost/latency proxy.

## Run #33 at a glance — the ranking replicates

*The sixteen Run #20 models — plus a new flash entrant, **Gemini 3.6 Flash** —
same Ability Card, a **new** flagship: a trader taking a client RFQ to a booked,
verified position (`trader-rfq-booking-day`, 10-step / 63-point).*

| Rank | Model | OVR | EFF | CON | Obj | note |
|:---:|---|:---:|:---:|:---:|:---:|---|
| 🥇 | GPT-5.6 Terra | **91** | 84 | 79 | 96.0 | leanest (37 calls) — **defends its Run #20 title** |
| 🥈 | GPT-5.6 Luna | 89 | 59 | 89 | 95.2 | |
| 🥉 | DeepSeek V4 Pro | 86 | 52 | 92 | 92.8 | steadiest of the top |
| 4 | Gemini 3.6 Flash | 84 | 44 | 66 | 96.8 | **new** — 3.5's 96.8 objective in 54 calls not 82 |
| 5 | Grok 4.5 | 84 | **15** | 96 | **98.4** | **objective #1**, ~64 calls vs par 35 |
| 12 | Gemini 3.5 Flash | 80 | **3** | 86 | 96.8 | objective #2, ~82 calls/run |
| 17 | LongCat 2.0 | 46 | 0 | 63 | 67.4 | 186 calls/trial — the floor |

- **The Run #20 verdict holds on a second workflow.** Objective capability saturates
  again (14 of 17 clear 86; nine cluster 92.1–98.4), so **efficiency (EFF, spread
  0–84)** and **consistency (CON, 0–99)** decide the operator ranking — not raw
  capability.
- **The inversion is sharper here — and now generational.** **Grok 4.5** posts the
  field's best objective score (98.4, **62 of 63 checks**) yet ranks **OVR #5** (EFF
  15); **Gemini 3.5 Flash** is objective #2 (96.8) and **OVR #12** (EFF 3, ~82 tool
  calls a run). The new **Gemini 3.6 Flash** reaches the *identical* 96.8 objective in
  54 calls (EFF 44, **OVR #4**) — an eight-rank jump over its predecessor, bought
  purely with efficiency. The models that win raw capability are the ones you should
  not automate — until a leaner successor fixes exactly that.
- **GPT-5.6 Terra wins again (OVR 91)** on a task it had never seen — near-top
  capability, delivered leanest. **Claude Sonnet 5** (elite ceiling, CON 56) and the
  **MiMo Pro-vs-base pair** (CON 96 vs 0) reproduce Run #20's consistency findings.

## Run #9 at a glance — the flash tier

| Rank | Model | Total | σ | $/match | pts/$ |
|:---:|---|:---:|:---:|:---:|:---:|
| ⚠️ | Doubao Seed 2.1 Turbo *(n=2)* | *65.3* | 9.1 | $1.95 | 33.5 |
| 🥇 | Gemini 3.5 Flash | **59.1** | 5.5 | $14.28 | 4.1 |
| 🥈 | Step 3.7 Flash | 57.9 | **4.3** | **$1.04** | 55.6 |
| 🥉 | DeepSeek V4 Flash | 49.2 | 15.5 | $0.68 | 72.2 |
| 4 | MiMo V2.5 | 48.3 | 12.4 | $0.57 | **85.3** |
| 5 | GPT-5.5 Instant | 34.5 | 19.8 | $9.07 | 3.8 |

- **"Flash" is a latency claim, not a price claim.** Gemini 3.5 Flash wins the
  *placed* board — but at **$14.28 a match it is the most expensive operator the
  Arena has measured, dearer than Run #8's frontier models.** **Step 3.7 Flash**
  lands 0.1 behind, is the *steadiest* model in the field (σ 4.3), and costs
  **14× less**.
- **The dark horse:** **Doubao Seed 2.1 Turbo** posts the highest *functional*
  score (65.3) and the run's best single judge score (88.75) — but on a sibling
  route it completed only **2 of 5** attempts, too few to crown (its primary route,
  Doubao Seed Evolving, was censored 0/5). Reported separately, not placed.
- **Cost-efficiency inverts the ranking.** MiMo V2.5 (85 pts/$) and DeepSeek V4
  Flash (72) deliver real desk work for well under a dollar a match; the two
  frontier-priced flash models are the worst buys (≈4 pts/$).
- **Three of nine cannot operate the desk** unattended (Agnes / Hunyuan / Qwen 3.7
  Plus — model-ability zeros). Tokens and cost are **measured** this run, not
  estimated.

## Run #8 at a glance

| Rank | Model | Total | σ (reliability) |
|:---:|---|:---:|:---:|
| 🥇 | Claude Opus 4.8 | **66.4** | 8.1 |
| 🥈 | GPT-5.5 | **66.3** | 8.2 |
| 🥉 | Claude Sonnet 4.6 | 59.1 | **2.7** (steadiest) |
| 4 | Kimi 2.7 | 56.9 | 10.4 |
| 5 | MiMo V2.5 Pro | 55.3 | 5.1 |

- **A tie at the top.** Opus 4.8 and GPT-5.5 finish 0.1 apart, deep inside their
  σ ≈ 8 — Opus wins on judge quality (cleaner governance narration), GPT-5.5 on
  objective coverage. Only repeated trials reveal this; the single-shot pilot
  ranked them in the opposite order.
- **Consistency is its own signal.** Sonnet 4.6 never tops the board but is the
  steadiest operator in the field (σ 2.7) — a different *product* from a
  high-mean/high-variance model for unattended use.
- **Cost–performance.** The open-weight tier (Kimi / MiMo / DeepSeek) delivers
  **~85% of the frontier's score at ~1/12 the cost**.

## How it's built

- **Real, not simulated.** Each trial seeds fixtures into the live desk DB, drives
  every workflow step through the production agent path, and reconstructs the
  transcript from the trace log — skills routed are *ground truth*.
- **Headless "YOLO" regime.** No human-in-the-loop interrupts and no deferral
  tool: the model must commit to its own judgment across all seven steps.
- **Failure-mode aware.** The Arena separates *model incapability* from
  *infrastructure censoring* (a failed gateway route) by reading what each model
  actually emitted, and flags censored rows rather than letting them depress a
  score silently.

Each report regenerates from its Markdown source with
[`render_report.py`](render_report.py) (styled HTML + headless-Chrome PDF).

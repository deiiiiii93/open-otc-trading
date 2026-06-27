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
| **#8** | 2026-06-27 | `risk-manager-control-day` | 10 models × 5 trials | **Claude Opus 4.8 (66.4) ≈ GPT-5.5 (66.3)** — a statistical tie at the top | [📄 Markdown](2026-06-27-run8-otc-desk-agent-arena.md) · [🌐 HTML](https://htmlpreview.github.io/?https://github.com/deiiiiii93/open-otc-trading/blob/main/docs/arena/2026-06-27-run8-otc-desk-agent-arena.html) · [📕 PDF](2026-06-27-run8-otc-desk-agent-arena.pdf) |

> **Coming soon.** Additional **long-workflow match designs** are in progress and
> will be published here as they're released. Detailed per-model **usage and cost
> accounting** will be added in future reports.

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

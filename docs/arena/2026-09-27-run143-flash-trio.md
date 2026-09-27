# 🏆 OTC Desk Agent Arena — Runs #143 / #144

**Three flash models from three labs worked the same six desk days, twice each. The
most accurate, the most efficient and the most consistent turn out to be three
different models, and two of them cost the same to run.**

*2026-09-27 · runs `#143` (five workflows) and `#144` (`trader-rfq-booking-day`,
manifest v3) · 3 models × 6 workflows × 2 trials · every contestant pinned to `high`
reasoning effort · output budget unpinned · objective-only scoring (jury off) ·
**36 of 36 trials scored, zero invalid** · cost is the amount **billed** by the
gateway, looked up per call*

---

## The field

| Model | Lab | Route |
|---|---|---|
| **gpt-6-luna** | OpenAI | `openai/gpt-6-luna` |
| **mimo-v2.6-flash** | Xiaomi | `xiaomi/mimo-v2.6-flash` |
| **deepseek-v4.1-flash** | DeepSeek | `deepseek/deepseek-v4.1-flash`, pinned to DeepSeek's own upstream |

All three sell as the cheap, fast tier of their lab's line-up. The six workflows are
the Arena's full set: a risk-limit breach, an operations and settlement day, a
risk-manager control day, a board portfolio review, a trader's RFQ from intake to
booking, and a morning of counterparty confirmations read from PDFs and scans.

This is also the first board we publish with billed cost. Every match records its
tokens and the gateway's id for every model call, so the cost column below is what
was actually billed, not an estimate.

---

## Headline

Consolidated card: the mean of each model's six workflow cards.

| Model | **OVR** | GRD | ADH | SYN | PRC | **EFF** | **CON** | Objective | Billed |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| **mimo-v2.6-flash** | **84.7** | 92 | 92 | **97** | 94 | **49** | 86 | 94.1 | $0.90 |
| **gpt-6-luna** | 83.8 | **95** | 94 | 93 | **98** | 45 | 80 | **96.4** | **$0.58** |
| **deepseek-v4.1-flash** | 79.7 | **95** | **97** | 96 | 94 | **1** | **93** | 95.8 | $0.59 |

Each model wins a different column:

- **gpt-6-luna does the work best.** It has the highest objective score (96.4) and
  the highest precision, it is the cheapest by a cent, and it is by far the fastest.
- **mimo-v2.6-flash has the best card.** It makes the fewest tool calls on most
  workflows and writes the best reports (SYN 97), which is enough to edge the OVR.
- **deepseek-v4.1-flash is the most careful.** It has the highest adherence (97)
  and consistency (93). It also spends so many extra tool calls that its efficiency
  bottoms out on five of six workflows, and that alone puts it five OVR points behind.

The top two are 0.9 OVR apart, which is inside two-trial noise. The gap to the third
is not: it is efficiency, and only efficiency.

---

## Workflow by workflow

Objective score, with the two trials in brackets, then CON.

| Workflow | gpt-6-luna | mimo-v2.6-flash | deepseek-v4.1-flash |
|---|:--:|:--:|:--:|
| risk-limit-breach-day | 97.4 (97.4 / 97.4) · 86 | **100.0** (100 / 100) · 99 | **100.0** (100 / 100) · 99 |
| ops-settlement-day | **95.5** (90.9 / 100) · 73 | 88.6 (88.6 / 88.6) · 82 | 90.9 (88.6 / 93.2) · 86 |
| risk-manager-control-day | **97.4** (97.4 / 97.4) · 96 | 93.6 (97.4 / 89.7) · 53 | **97.4** (97.4 / 97.4) · 99 |
| high-board-portfolio-review-day | **94.3** (94.3 / 94.3) · 69 | 92.8 (91.4 / 94.3) · 96 | 91.4 (88.6 / 94.3) · 96 |
| trader-rfq-booking-day *(v3)* | **96.8** (98.4 / 95.2) · 56 | 92.7 (93.5 / 91.9) · 92 | **96.8** (98.4 / 95.2) · 89 |
| confirmation-desk-day | 97.0 (97.0 / 97.0) · 99 | 97.0 (97.0 / 97.0) · 92 | **98.5** (97.0 / 100) · 89 |

Correctness is close to saturated. No model falls below 88 in any trial, and the
spread between the best and worst objective mean is 2.3 points. What separates the
field is how each model gets there.

CON measures how far a model's two *cards* disagree, not its two objective scores.
That is why gpt-6-luna scores 94.3 twice on the board review and still gets CON 69:
it used 43 tool calls in one trial and 21 in the other, and its efficiency moved
with them.

---

## Three ways to be a flash model

### gpt-6-luna: the lean operator

gpt-6-luna averages **47 tool calls a match**, the fewest of the three, and almost
none of them are wasted on searching: 2% go to filesystem search, against 15% for
mimo and 19% for DeepSeek. It reads its own stored results instead (22% of its
calls are artifact reads). It used **24.2M tokens** across twelve matches, about
half of what either rival used, and finished a match in a **median 6.7 minutes**.

Its weakness is that it does not do the same thing twice. On three of six workflows
its two trials spent visibly different effort: 43 and 21 calls on the board review,
52 and 84 on the RFQ day. Its objective score barely moved, but its card did, and
CON 80 is the lowest in the field.

### mimo-v2.6-flash: efficient when it works, slow when it doesn't

On three workflows mimo is the leanest model on the board, finishing the risk-limit
breach in 24 and 29 calls with a perfect score in both trials. That produces the
field's best single card (OVR 99) and, with the best report-writing score, the top
consolidated OVR.

The other side is two matches that ran away. One confirmation-desk trial took
**242 tool calls and 3.5 hours**, chasing a strike amendment through 72 `grep` calls
and 27 Python runs, and one RFQ trial took 2.5 hours, more than an hour of it on the quoting step. In
both, the gateway was slow to answer (single calls up to 12 minutes) and mimo kept
retrying calls it had formatted wrongly, sending structured arguments as JSON
strings. Neither run cost it much score, but they cost it everything else: a
**median 22.5 minutes a match** and 10.7 hours of wall-clock in total, against 1.6
hours for gpt-6-luna.

### deepseek-v4.1-flash: the thorough one

DeepSeek follows instructions most closely (ADH 97) and repeats itself most
reliably (CON 93). It is the only model with a perfect confirmation-desk trial:
every check passed on a morning of PDFs and scanned confirmations.

It gets there by looking everywhere. It averages **92 tool calls a match**, and one
in five is a filesystem search: `grep`, `glob`, `ls` and `read_file` over skills and
stored results. Scored against each workflow's par, that is EFF 1: five of its six
workflow cards have EFF 0.

---

## What it costs

Billed by the gateway, per workflow, for two trials:

| Workflow | gpt-6-luna | mimo-v2.6-flash | deepseek-v4.1-flash |
|---|:--:|:--:|:--:|
| risk-limit-breach-day | $0.069 | **$0.062** | $0.078 |
| ops-settlement-day | **$0.063** | $0.092 | $0.070 |
| risk-manager-control-day | $0.133 | $0.127 | **$0.127** |
| high-board-portfolio-review-day | **$0.069** | $0.072 | $0.070 |
| trader-rfq-booking-day | $0.173 | $0.294 | **$0.100** |
| confirmation-desk-day | **$0.074** | $0.249 | $0.144 |
| **Twelve matches** | **$0.58** | $0.90 | $0.59 |

DeepSeek used **twice gpt-6-luna's tokens and paid the same**. All three had 90–92%
of their input served from the prompt cache, so caching is not the difference. The
difference is the rate: DeepSeek's route lists two input prices, and its bill
matched the lower one on every workflow. Its EFF of 1 is a real cost in time and
in the chance of wandering, but on this route it is not a cost in money.

mimo's bill is the opposite case. On three workflows it is as cheap as anyone. The
two rows that held its long matches, the confirmation desk and the RFQ day, are 60%
of what it paid.

For every model the billed amount matched our own estimate from the gateway's list
prices, to within a cent.

---

## One trap, three answers

The last step of the operations day is a trap. The desk "disputes the knock-out
print" on a snowball whose knock-out was recorded that morning, and asks the agent
to reopen the trade. The settlement that knock-out generated is still pending, so
the desk refuses the reopen, and the correct answer reports that it was not
recorded and why.

| | Trial 1 | Trial 2 |
|---|---|---|
| gpt-6-luna | reported the reopen done | **refused**: "settlement cashflow 9304 is still pending" |
| deepseek-v4.1-flash | reported the reopen done | **refused**: lifecycle events are append-only |
| mimo-v2.6-flash | **cancelled the knock-out and voided the cashflow** | **the same again** |

Two models split one to one. mimo never refused, and it was the only model that
changed the book to make its answer true: it cancelled the knock-out event and voided
the pending 512,500 CNY settlement both times, then reported the trade live. That is
the most consequential thing any contestant did on this board, and it is most of
what stands between mimo's 88.6 and a perfect operations score.

---

## How this board was kept clean

Several rows on this board were re-run or re-scored before publication. None of that
changed a model's behaviour. Each case was the harness or the scoring getting
something wrong, and each is fixed in the app.

- **Four rows were re-run from scratch.** Three harness defects were hit during the
  run and fixed. A step could inherit a dead network connection from the previous
  step; two sub-agents finishing at once crashed the turn; and history compaction
  could leave a tool result without the call that produced it. That last one
  mattered most here: DeepSeek's upstream rejects such a history, so every later
  turn of that match failed. Its confirmation-desk row read 69.7 before the fix and
  98.5 after.
- **The RFQ day is manifest v3.** The request never said whether the client was
  buying or selling, or how many. Five of six trials assumed "buys one", but one
  gpt-6-luna trial refused to invent terms and blocked every later step, a 45-point
  swing on a question the workflow does not mean to ask. v3 states both. Its scores
  are not comparable with earlier RFQ boards.
- **One row was re-scored.** mimo sends ids as strings (`"9311"`), which the desk's
  tools accept. The scorer compared them strictly and marked four successful calls
  per trial as missing. Re-scored from the stored transcripts, its operations score
  went from 79.5 to 88.6. A re-score of every other row on the board changed nothing.

Every match records the app build it ran on; the re-run rows carry a later build
than the rest. The manifests are identical across all of them except the RFQ day.

---

## Caveats

- **Two trials.** CON exists because there are two, but two is enough to see
  inconsistency, not to measure it precisely. The top two OVRs are within that noise.
- **One effort level.** Every contestant ran at `high`. An earlier study found
  effort changes some models' efficiency sharply, and none of these three was tested
  at another level.
- **Latency is the gateway's as much as the model's.** mimo's slowest calls came in
  two long stretches and may not reproduce at another hour.
- **Cost is for this desk's traffic.** Every model leaned on a warm prompt cache;
  a workload without that reuse would price them very differently.

# Public narrative: sharing how the OTC desk agent was built

**Date:** 2026-08-06
**Status:** design approved, pending implementation plan
**Owner:** @deiiiiii93

---

## 1. Goal

Convert seven weeks of building a production OTC derivatives desk agent into public
evidence of engineering judgment, in service of two outcomes:

1. **A recruiting signal** — an LLM lab or fintech should want to hire the author.
2. **Consulting and advisory work** — financial institutions running AI-transformation
   programmes should see a credible party to hire.

Both are *credibility-of-the-person* outcomes. Neither is a product funnel. The
artifact must be legible to a hiring manager or a head of AI in five minutes and
reward an hour of deeper reading.

### Audiences (all four, in one asset with four framings)

| Audience | Proof they need | Strongest existing evidence |
|---|---|---|
| Western LLM labs | Novel eval insight; agent-harness depth | The scoring-validity audit — 15 of 50 checks carried zero ability signal; correcting it reordered the board (Spearman 0.789) |
| Chinese LLM labs | *Their own model's* behaviour in a real environment | 5 runs × ~18 models of repeated-trial ability-card data |
| Chinese financial institutions | Survives compliance; runs a real desk | Audit trail, HITL taxonomy, migrations, governed reports |
| Western financial institutions | Governance rigour; no hallucinated numbers | Determinism boundary, grounding guard, `empty` vs `unavailable` |

### Binding constraint

**English-language distribution is essentially zero** — no blog, no X following, no HN
karma. Distribution is therefore a design constraint, not a final step. Two consequences
govern the whole plan:

- **Hacker News is the one true broadcast shot**, because it is follower-independent.
  It only rewards material that generalises beyond the author's niche.
- **Direct outreach needs no audience at all**, so it runs first and costs nothing.

---

## 2. Non-goals

- Not a product-marketing campaign. `marketing/product-intro.mp4` sells a product and
  says nothing about the author's judgment; a good designer could have made it.
- Not a funding or customer-acquisition effort.
- **Not a velocity brag.** "264k LOC / 782 commits / 7 weeks" reads as impressive to a
  recruiter and as vibe-coded slop to a senior engineer at a lab — the exact reader who
  must be convinced. Velocity appears only as a footnote beside rigour evidence
  (93k lines of tests, ~54 migrations, a benchmark audited against itself). Never as a
  headline.

---

## 3. Positioning spine

> **"I build production agents for domains where a wrong number costs money — and I
> evaluate them like an experimentalist."**

Two proof pillars:

- **The desk** — a real, governed, working OTC agent. This is the *substrate*, not the
  pitch. It is why the lessons are trustworthy: in this domain a hallucination has P&L
  consequences, so the failure modes could not have been invented.
- **The Arena** — a rigorous evaluation of LLMs operating that desk, which the author
  then audited against himself. This is the *differentiator*.

### The reframe

Current framing is *"I built an OTC trading agent"* — a niche of a few hundred people
worldwide, which an LLM lab reads as a domain application.

Adopted framing: **"I built a production agent in a domain where a wrong number costs
real money — here is what that taught me about agent engineering and evaluation."**

The trading desk becomes the credibility substrate; the lessons generalise to everyone
building agents. Both the LLM labs and the financial institutions sit inside the larger
tent.

---

## 4. Asset architecture

One core asset — **the repo plus the Arena reports, both already built** — wearing five
derived surfaces:

| ID | Surface | Primary audience | Phase |
|---|---|---|---|
| S1 | Per-model scorecard cuts (×10) | Chinese LLM labs | 1 |
| S2 | Flagship English essay | Western labs + general | 2 |
| S3 | Arena English home + arXiv preprint | Labs, ongoing | 3 |
| S4 | Governance whitepaper | Financial institutions | 4 |
| S5 | Chinese finance cut (Zhihu / 公众号) | Chinese FIs | 4 |

---

## 5. Phase 1 — Week 1: outreach (no audience required)

### 5.1 Deliverable

Ten one-page per-model scorecards. Board-shaped reports do not work here: nobody at
DeepSeek wants to read seventeen competitors' results to find their own. Each cut is
roughly 30 minutes of work and multiplies the response rate.

Each scorecard contains, in order:

1. **The card** — OVR plus the six stats (GRD / ADH / SYN / EFF / PRC / CON), rank, run
   number.
2. **What the environment is** — three sentences: a real stateful production desk, driven
   headless with no human in the loop, scored from the system's own trace log against an
   objective manifest.
3. **Two or three specific failing traces**, each named to the check that failed.
4. **Full serving configuration disclosed** — channel, wire protocol, temperature,
   limits, recursion limit.
5. **The question:** *"Did I serve your model correctly?"*
6. Links to the full report and the repo.

### 5.2 Why the question is the whole mechanism

The best outreach carries no ask, or a micro-ask genuinely in the recipient's interest.
This one is authentic rather than manufactured, and it is already documented in
`CLAUDE.md`:

- MiniMax, Qwen and LongCat all required `protocol:anthropic` to be served correctly.
- ZenMux 402s once made Claude look far worse than it was, until `_is_infra_contaminated`
  was built to detect partial deaths.

So *"did I serve your model correctly?"* is a real question with a real answer. It is
easy to answer, so response rates are high. It signals a concern for fairness, which
disarms the fact that the sender is also ranking them. And answering it opens a technical
conversation with an engineer — the desired outcome.

### 5.3 Targets and order

Run #94 fielded **ten Chinese labs**. They split into two tiers that need *different
framings* — and, usefully, the second tier has the stronger hook.

**Tier A — strong results. Send first.** These messages are pure good news, are the most
likely to be amplified, and amplification from a lab is the fastest route out of zero
reach.

| # | Lab | Model — Run #94 | Note |
|---|---|---|---|
| 1 | DeepSeek | V4 Flash — OVR 91 (3rd); V4 Pro — OVR 88 (4th) | Best Chinese result; two models, one message |
| 2 | Alibaba (Qwen) | Qwen 3.7 Max — OVR 88 (5th) | Also a `protocol:anthropic` case |
| 3 | Meituan (LongCat) | LongCat 2.0 — OVR 83 (6th) | Top objective score of the tier (95.7); `protocol:anthropic` case |
| 4 | StepFun | Step 3.7 Flash — OVR 81 (8th) | — |

**Tier B — anomalous or low results. Send second, with the harness question carrying the
weight.** The framing here is *not* "your model did badly". It is: **"your model scored
anomalously low on one axis and I suspect my harness rather than your model — can you
help me check?"** That is a genuine question, it is the most useful thing these labs could
receive, and it is the most likely of all ten to get a technical reply.

| # | Lab | Model — Run #94 | The anomaly that *is* the hook |
|---|---|---|---|
| 5 | Tencent (Hunyuan) | Hunyuan 3 — OVR 71 (12th) | SYN 40 against ADH 99 — follows process, fails to deliver |
| 6 | Moonshot (Kimi) | Kimi 2.7 — OVR 68 (13th) | SYN 40, CON 33 |
| 7 | Xiaomi (MiMo) | MiMo 2.5 — OVR 68 (14th); 2.5 Pro — OVR 65 (16th) | Pro ranks *below* non-Pro — worth their attention |
| 8 | ByteDance (Doubao) | Doubao Seed 2.1 Pro — OVR 66 (15th) | CON 0 with objective 82.8 — high capability, zero reproducibility |
| 9 | Zhipu (GLM) | GLM 5.2 — OVR 64 (17th) | **SYN 0 with CON 99** — perfectly consistent at producing no deliverable. Almost certainly a harness or serving issue, not capability |
| 10 | MiniMax | M3 — OVR 42 (18th) | Only model with ADH below 92 (78); documented `protocol:anthropic` case. The serving question is most literal here |

**Western labs are deliberately excluded from Phase 1.** Anthropic, OpenAI, Google and
xAI do not convert from unsolicited contact. For them the order inverts: the Phase 2
artifact *is* the outreach. One exception is worth taking: Gemini 3.6 Flash earned the
**first perfect card in the Arena's history** (OVR 99, objective 100.0), which justifies
a single public X post — good news about someone else's model, not a pitch.

**Western labs are deliberately excluded from Phase 1.** Anthropic, OpenAI, Google and
xAI do not convert from unsolicited contact. For them the order inverts: the Phase 2
artifact *is* the outreach. One exception is worth taking: Gemini 3.6 Flash earned the
**first perfect card in the Arena's history** (OVR 99, objective 100.0), which justifies
a single public X post — good news about someone else's model, not a pitch.

### 5.4 Channels

- **GitHub issue on the lab's model repo — primary.** Read by engineers rather than a
  marketing inbox, and public and permanent, so other people find it later and it keeps
  paying out.
- **Zhihu post tagging the lab's official account — secondary.** Home-field advantage;
  their own community circulates it.
- **X @-mention of named researchers — tertiary.** Names come from model cards and
  technical reports. Public mention, never DM.

### 5.5 Etiquette rules (non-negotiable)

These are the difference between a contribution and spam:

- One message per lab. No follow-up blasts.
- Data and a question. Never a pitch.
- **No mention of wanting a job or consulting work in the first message.**
- If they reply, the conversation stays technical. Anything else comes much later, or
  from them.

### 5.6 Success criterion

**≥ 2 substantive replies out of 10.** From zero reach, two real conversations with a lab
is a large return. Tier B is expected to out-convert Tier A on replies, because a
suspected-harness question is more actionable for the recipient than good news.

---

## 6. Phase 2 — Weeks 2–3: the flagship essay

Lead with the **evaluation-critique** angle, because it generalises past finance and
therefore past the author's niche. The self-audit genre is rare: almost no benchmark
author has published their own benchmark's autopsy.

### 6.1 Structure (~4,000–6,000 words)

- **Cold open — the incident.** HITL risk level `"write"` booked a real position
  unattended in AUTO mode, because `interrupt_on_config(yolo_mode=True)` strips every
  `"write"`-level tool from the interrupt map. One paragraph. No preamble.
- **Setup.** What the desk is and why the domain is unforgiving. Two paragraphs, no
  product pitch.
- **Act I — Harness.** Registered ≠ discoverable. `wrap_tool_call` is the only seam that
  sees a subagent's tool calls. Classification by capability group rather than deny-list.
  *When a model never calls a tool, suspect availability before capability.*
- **Act II — Evidence and determinism.** The numbers never come from the LLM. Ground-truth
  artifacts that survive compaction. `empty` vs `unavailable` — a check that ran and found
  nothing reads differently from one that never ran. The grounding guard. The pricing
  engine pinned to an exact version, because dependency drift invalidates the benchmark.
- **Act III — Evaluation.** A golden replay proves *satisfiability, never reachability*.
  The validity audit: 15 of 50 checks carried zero ability signal while still occupying
  the denominator; correcting it moved the top score from 72 to 88.6 **and reordered the
  board**. A behavioural spread caused by a broken fixture is not a capability signal.
- **Close.** What I would do differently. Everything is open: repo, reports, `CLAUDE.md`.

### 6.2 Route

Canonical URL on GitHub Pages (repo-hosted; no new domain needed) → **Hacker News**
(Tuesday–Thursday, 08:00–10:00 ET) → r/MachineLearning, r/LocalLLaMA, Lobsters → X thread
→ LinkedIn cut → Zhihu cut.

### 6.3 If Hacker News does not bite

Reposting is not permitted and not attempted. The essay remains evergreen and becomes the
single link attached to every application and every consulting pitch — which alone
satisfies the stated goal. The second shot comes from a *different artifact* (the
whitepaper, or the next Arena run), never from a repost.

---

## 7. Phase 3 — Ongoing: the Arena as an engine

- **English leaderboard page** on GitHub Pages, cumulative across runs.
- **One run per major model launch** → report → post. This converts a one-time spike into
  a following: every model release is a free news hook the Arena is already positioned
  for.
- **arXiv preprint (cs.AI)** of the consolidated methodology. The reports are already
  paper-shaped — Run #94 carries an Abstract, a Limitations & threats-to-validity section,
  a Reproducibility section, and a behavioural-taxonomy appendix. A preprint is a
  *credential* in a way a blog post is not: citable, permanent, and the most efficient
  single artifact for the LLM-lab recruiting goal. It also hardens every other channel.
  **Check arXiv endorsement for cs.AI early** — it is the only step with a lead time.

---

## 8. Phase 4 — On demand: governance whitepaper and Chinese finance cut

Triggered by the first financial-institution conversation, not before.

Contents: the determinism boundary; the HITL risk taxonomy **with the real incident**;
the fail-closed audit trail; evidence artifacts and compaction; the grounding guard;
`empty` vs `unavailable`. Produced in English (S4) and Chinese (S5).

This is the document that converts a reader into a client. It has low reach on its own
and depends on Phase 2 or 3 to supply readers.

---

## 9. Front door — ~1 day, alongside Phase 1

Anyone arriving from any channel currently lands on a product pitch rather than evidence
of judgment.

- **README** gains a prominent **"Engineering notes"** door near the top, linking the
  essay, the Arena, and `CLAUDE.md`.
- **`CLAUDE.md` published as a featured artifact** — a real, 74 KB, battle-scarred
  agent-guidance file from a production agent codebase. Almost none exist publicly.
  Near-zero effort, and precisely targeted at the intended readers.

---

## 10. Measurement

| Phase | Metric | Target |
|---|---|---|
| 1 | Substantive lab replies | ≥ 2 of 10 |
| 2 | Hacker News | top 30 front page |
| 2 | GitHub stars | +300 |
| 3 | Run-over-run readership | growing |
| All | Inbound role or consulting contact | ≥ 1 within 90 days |

---

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Read as vibe-coded slop | Never lead with LOC or commit counts; pair velocity with the test suite and the self-audit |
| Benchmark methodology attacked | The validity audit is a pre-built defence — publish it *prominently*, not defensively |
| Outreach reads as spam | The §5.5 etiquette rules |
| Essay too niche to travel | The §3 reframe: lessons generalise, the desk is only the credibility substrate |
| arXiv submission blocked | Check cs.AI endorsement in week 1, before it is on the critical path |

---

## 12. Open assumption

No employer or client confidentiality constraint restricts publishing this material. The
repository is already public and MIT-licensed under the author's name, so this appears
safe — but it has not been explicitly confirmed and should be before Phase 2.

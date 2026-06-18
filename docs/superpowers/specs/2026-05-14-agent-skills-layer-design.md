# Agent Skills Layer — Design

**Date:** 2026-05-14
**Status:** Approved (brainstorming complete; pending writing-plans)
**Scope:** Infrastructure for an in-repo skills layer on top of the existing DeepAgents orchestrator/persona stack, plus one vertical slice that exercises all three skill tiers (`snowball-position-diagnostics`).

---

## Decision summary

Four design forks were closed during brainstorming:

1. **Activation mechanism** — Use deepagents' built-in `SkillsMiddleware` via the `skills=[...]` kwarg on `create_deep_agent` and per-`SubAgent`. Progressive disclosure (catalog injected; full body read on demand via `read_file`).
2. **Scope** — Three skill tiers covered: **policy** (always-on), **procedure** (on-demand, per persona), **product card** (on-demand, shared reference).
3. **First slice** — `snowball-position-diagnostics`, because it exercises all three tiers naturally and lives across two personas (trader + risk_manager).
4. **Policy wiring** — Policy content lives as composable system-prompt fragments concatenated at agent build time; only procedure and product card content flows through `SkillsMiddleware`. This honors the lifecycle difference (policy is governance; procedures and cards are knowledge) and keeps HITL/cost-preview discipline out of the model's discretion.

The deliverable for this spec is a single PR that introduces the skills infrastructure plus the snowball vertical slice, and is behavior-preserving for everything else (policy is extracted byte-identical from existing persona prompts).

---

## 1. Architecture overview

**Change in one sentence.** Replace the three monolithic persona prompts with `<persona identity + output style>` + composable policy fragments concatenated at build time, and add a per-persona `SKILL.md` library wired through `SkillsMiddleware` for procedures and product cards.

### What stays unchanged

- The `task` tool, HITL middleware, checkpointer, channel registry, and the LangChain tool surface in `langchain_tools.py` are untouched.
- All three personas keep the same full tool list — differentiation remains system-prompt-only, just now sourced from composition + skill catalog.
- The orchestrator's prompt only gains a small directive: when delegating, name the skill(s) it expects the persona to use.

### What moves

- The ~40-50% of each persona prompt that is cross-cutting policy (Read-before-Compute, Cost-preview, HITL batch-size-1, Clarification protocol, `run_python` read-fetch-script-write pattern) is extracted into composable fragments under `skills/policy/<name>.md`.
- Persona-specific procedures and product-specific reference content live in `SKILL.md` directories under `skills/procedures/<persona>/` and `skills/products/`.

### New components

1. **`backend/app/services/deep_agent/skills/`** — three subtrees: `policy/`, `procedures/<persona>/<skill-name>/SKILL.md`, `products/<product-id>/SKILL.md`.
2. **`backend/app/services/deep_agent/skills_loader.py`** — pure-Python helpers: load policy fragments by allowlist, compose persona prompts.
3. **Updated `personas.py`** — each spec computes `system_prompt = identity + composed_policy_fragments(allowlist)` and declares `skills=[<skill source paths>]` for the SubAgent. `_filesystem_permissions()` gains read access to the skills root.
4. **Updated `orchestrator.md`** — adds a "Naming skills in delegations" section + a Routing-matrix update for the new skill(s).

### Three tiers, three lifecycles

| Tier | Mechanism | Always in context? | Who decides to apply |
|---|---|---|---|
| Policy | Markdown fragment composed into `system_prompt` at build time | Yes | Build-time allowlist (per persona) |
| Procedure | `SKILL.md` via `SkillsMiddleware` (catalog injected, body on-demand) | Catalog only | Persona reads `SKILL.md` when description matches the task |
| Product card | `SKILL.md` via `SkillsMiddleware` | Catalog only | Persona reads when working on that product |

### Out of scope

- A routing skills source on the orchestrator itself (`routing/`). Routing logic stays in `orchestrator.md` for V1; routing skills become a follow-on plan once we have ≥3 procedure skills and clear duplication.
- A `read_skill` tool or per-call dynamic system prompt. Deepagents' progressive disclosure (catalog-in-prompt + `read_file`) is sufficient.
- Backfill of the other four candidate procedures (`rfq-intake-and-quote`, `portfolio-pricing-run`, `risk-report-workflow`, `market-data-profile`). Each becomes its own writing-plans cycle.

---

## 2. Skill catalog & directory layout

```
backend/app/services/deep_agent/
├── prompts/                          # Existing; trimmed
│   ├── orchestrator.md
│   ├── trader.md                     # Shrinks: identity + output style + routing-from-skills directive
│   ├── risk_manager.md               # Shrinks
│   └── high_board.md                 # Shrinks
└── skills/                           # NEW
    ├── policy/                       # Composable fragments (NOT SkillsMiddleware-loaded)
    │   ├── read-before-compute.md
    │   ├── cost-preview.md
    │   ├── hitl-batch-size-1.md
    │   ├── clarification-protocol.md
    │   └── run-python-rfsw.md
    ├── procedures/                   # SkillsMiddleware-loaded; one source dir per persona
    │   ├── trader/
    │   │   └── snowball-position-diagnostics/
    │   │       └── SKILL.md
    │   ├── risk_manager/
    │   │   └── snowball-position-diagnostics/
    │   │       └── SKILL.md
    │   └── high_board/               # empty in V1
    └── products/                     # SkillsMiddleware-loaded; shared by trader + risk_manager
        └── snowball-cn/
            └── SKILL.md
```

### Why this shape

- **`SkillsMiddleware` finds skill dirs one level deep** under each source path (`backend.ls(source)` + children-containing-`SKILL.md` discovery). That dictates `procedures/<persona>/` as separate sources rather than a flat `procedures/`.
- **`policy/` is not a `SkillsMiddleware` source.** It sits in `skills/` purely for co-location and reviewability. The loader reads these files directly at agent build time; they never appear in the runtime catalog.
- **`products/` is one shared source.** Both trader and risk_manager need product cards; high_board doesn't, in V1.

### Per-persona `SkillsMiddleware` sources

| Persona | `skills=[...]` argument |
|---|---|
| trader | `["/skills/procedures/trader/", "/skills/products/"]` |
| risk_manager | `["/skills/procedures/risk_manager/", "/skills/products/"]` |
| high_board | `["/skills/procedures/high_board/"]` |
| orchestrator | *(none in V1)* |

### Per-persona policy allowlist

Consumed by `skills_loader.compose_persona_prompt()`:

| Persona | Policy fragments composed in |
|---|---|
| trader | `read-before-compute`, `cost-preview`, `hitl-batch-size-1`, `clarification-protocol`, `run-python-rfsw` |
| risk_manager | same as trader |
| high_board | `cost-preview`, `hitl-batch-size-1`, `clarification-protocol` |

The high_board exclusions reflect the persona's role: it acts on completed runs (no Read-before-Compute discipline needed) and does not own ad-hoc analytics (no `run-python` pattern).

### Naming rules

`SkillsMiddleware` enforces these for procedure + product card files; we mirror them by convention for policy fragments.

- Lowercase alphanumeric + hyphens, 1-64 chars. No leading/trailing `-`, no `--`.
- Directory name must equal the `name:` field in YAML frontmatter (for SkillsMiddleware skills).
- Product cards: `<product-type>-<market>` (e.g., `snowball-cn`, `phoenix-hk`) leaves room for market-specific variants.
- Procedure skills: `<verb-noun-modifier>` (e.g., `snowball-position-diagnostics`, `rfq-intake-and-quote`).

### Filesystem permissions delta

Add one rule before the trailing deny-all `/**`:

```python
FilesystemPermission(
    operations=["read"],
    paths=["/skills", "/skills/**"],
    mode="allow",
),
```

The existing root `["/"]` read-allow already permits the catalog `ls`; the explicit `/skills` rule documents intent and survives any future tightening of the broad root rule.

---

## 3. Persona prompt assembly

### Loader contract

```python
# backend/app/services/deep_agent/skills_loader.py

POLICY_DIR = Path(__file__).parent / "skills" / "policy"

def load_policy_fragments(names: Sequence[str]) -> str:
    parts = [(POLICY_DIR / f"{n}.md").read_text(encoding="utf-8").strip() for n in names]
    return "\n\n".join(parts)

def compose_persona_prompt(*, identity_prompt: str, policy_fragment_names: Sequence[str]) -> str:
    return identity_prompt.rstrip() + "\n\n" + load_policy_fragments(policy_fragment_names)
```

### `personas.py` delta

```python
trader_spec = SubAgent(
    name="trader",
    description="…",
    system_prompt=compose_persona_prompt(
        identity_prompt=_load_prompt("trader.md"),
        policy_fragment_names=[
            "read-before-compute",
            "cost-preview",
            "hitl-batch-size-1",
            "clarification-protocol",
            "run-python-rfsw",
        ],
    ),
    tools=list(tools),
    skills=["/skills/procedures/trader/", "/skills/products/"],
)
```

`risk_spec` and `board_spec` follow the same shape with their respective fragment allowlist and `skills` source list.

### Assembled prompt ordering

`identity → policy fragments → output style → routing-from-skills directive`.

Policy comes right after identity because it is the safety floor. The model's strongest behavioral anchoring is the opening of the system prompt; identity-then-policy puts it there.

### Policy fragment shape

Each `policy/<name>.md` opens with an H2 header matching its concern (`## Read-before-Compute`) and contains the same content currently embedded in the persona prompts. No frontmatter — policy fragments don't flow through `SkillsMiddleware` and don't need metadata. Concatenation produces a clean sequence of `##` sections.

### Trimmed persona prompt (illustrative — `prompts/trader.md` after the change)

```markdown
You are the trader persona for an OTC derivatives desk. Your decision lens is
quote readiness, pricing accuracy, and trade construction.

## Tools you use
- [unchanged enumerated list with HITL annotations]

## Output style
- Be concise. State the price/quote, the inputs you used, and any caveats.
- [rest unchanged]

## Data access rule
[unchanged — filesystem rules]

## Routing from skills
The orchestrator may name a skill in the task description ("Use
snowball-position-diagnostics"). When it does, `read_file` the skill at
`limit=1000` BEFORE invoking domain tools, then follow its procedure.

For product-specific work, read the matching card from `/skills/products/`
before pricing or diagnostics in this session.
```

The five policy fragments get appended below "identity" by the loader at build time.

---

## 4. SkillsMiddleware wiring & filesystem backend

### API surface

- `backend` — shared filesystem-like store that resolves `/skills/**` (catalog and SKILL.md reads), `/trading_desk/**` (existing), and `/large_tool_results/**` (existing).
- `skills` — per-agent list of source paths inside that backend.
- `permissions` — `FilesystemPermission` rules augmented with read-allow for `/skills`.

### Orchestrator wiring delta

```python
from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend

_SKILLS_FS_ROOT = Path(__file__).parent / "skills"

def _skills_backend() -> FilesystemBackend:
    # Exact constructor signature verified during implementation —
    # see Verification items below.
    return FilesystemBackend(mounts={
        "/skills": str(_SKILLS_FS_ROOT),
        "/trading_desk": str(_TRADING_DESK_ROOT),
        "/large_tool_results": str(_LARGE_TOOL_RESULTS_ROOT),
    })

def build_orchestrator(...):
    return create_deep_agent(
        model=model,
        tools=[],
        system_prompt=_orchestrator_prompt(),
        subagents=all_personas(model, tools),    # personas carry their own `skills=[...]`
        interrupt_on=interrupt_on or interrupt_on_config(),
        checkpointer=checkpointer,
        backend=_skills_backend(),
        permissions=_filesystem_permissions(),
        name="otc_desk_orchestrator",
    )
```

### Per-agent SkillsMiddleware lifecycle

Because `skills=` is passed on each `SubAgent`, every persona gets its own `SkillsMiddleware` instance. Each one's `before_agent` runs once per session per subagent invocation context, caches `skills_metadata` into private state, and `wrap_model_call` injects the catalog section into the subagent's system prompt on every model call. The catalog is therefore persona-scoped — the trader never sees risk_manager's procedures, and vice versa.

### One backend, two consumers, same paths

The same `backend` instance is used by:

1. `SkillsMiddleware.before_agent` — does `backend.ls("/skills/procedures/trader/")` and downloads SKILL.md contents.
2. `FilesystemMiddleware.read_file` — when the persona decides to read a full skill body, it calls `read_file("/skills/procedures/trader/snowball-position-diagnostics/SKILL.md", limit=1000)`, which resolves through the same backend.

Keeping these on one backend gives a clean audit story: every skill load is a `read_file` tool call visible in the LangSmith / checkpointer trace.

### HITL: skills are read-only, no interrupt

`read_file` is not in `interrupt_on_config()` today and stays out. Fetching skill bodies must not trigger HITL; pausing for confirmation each time would defeat progressive disclosure.

### Verification items (deferred to implementation plan)

1. Exact `FilesystemBackend` constructor signature — whether it accepts a `mounts={...}` dict, a single `root_dir`, or requires composing multiple backends. The wrapper shape may differ across plausible APIs.
2. Confirm the default backend `create_deep_agent` uses today (no `backend=` is currently passed). If it auto-creates a `StateBackend`, the existing `/trading_desk` and `/large_tool_results` paths are also in-memory and we just need an explicit backend instance now.
3. Confirm `read_file` works without HITL interruption (it should — it's not a persisted tool — but worth a smoke test).
4. Confirm that per-subagent `skills=` produces a per-subagent `SkillsMiddleware` instance (rather than one shared instance with merged sources).

---

## 5. Skill file format

### Frontmatter fields (V1 use)

| Field | Use |
|---|---|
| `name` | Skill identifier; must match parent directory name (enforced by middleware). |
| `description` | Trigger sentence the persona reads in the catalog. Up to 1024 chars but aim for ~200. Must describe what + when — only field visible until the body is read. |
| `allowed-tools` | Space-delimited tool names the skill recommends. Currently a soft hint (experimental enforcement). Treat as preference + audit data. |
| `metadata.tier` | `procedure` or `product-card`. |
| `metadata.persona` | `trader` / `risk_manager` / `high_board` for procedures; omitted for product cards. |
| `metadata.related_products` | Space-delimited product card IDs (cross-link). |
| `metadata.related_skills` | Cross-link to other procedure skills. |

`license` and `compatibility` are left unset for V1.

### Body conventions — procedure skills (fixed schema)

```markdown
## When this applies
[1-3 bullets — triggers the persona watches for]

## Inputs to inspect first
[Read-only tools to call BEFORE any state-touching action]

## Step sequence
[Numbered steps; each step says what to do and the expected observable]

## What success looks like
[1-2 lines describing the visible outcome the persona should be able to report]

## Tool preferences
[Soft notes on which tools are preferred / forbidden / cost-gated for this procedure,
augmenting the `allowed-tools` frontmatter with semantics]
```

Drift is the main risk for procedure content; a fixed schema makes review and authoring predictable.

### Body conventions — product cards (free-form with recommended sections)

```markdown
## What it is
## Key invariants
## Pricing engine & market inputs
## Market quirks
## Common diagnostics signals
## See also
```

Cards are reference material and vary by product more than procedures vary by domain. Sections are recommendations, not enforcement.

### First-slice file inventory

| Path | Tier | Notes |
|---|---|---|
| `skills/policy/read-before-compute.md` | Policy fragment | Extracted from trader.md / risk_manager.md |
| `skills/policy/cost-preview.md` | Policy fragment | Extracted from trader.md / risk_manager.md / high_board.md |
| `skills/policy/hitl-batch-size-1.md` | Policy fragment | Extracted from all three persona prompts |
| `skills/policy/clarification-protocol.md` | Policy fragment | Extracted from orchestrator.md + persona variations |
| `skills/policy/run-python-rfsw.md` | Policy fragment | Extracted from trader.md / risk_manager.md |
| `skills/procedures/trader/snowball-position-diagnostics/SKILL.md` | Procedure | New content — pricing-lens diagnostic |
| `skills/procedures/risk_manager/snowball-position-diagnostics/SKILL.md` | Procedure | New content — risk-lens diagnostic |
| `skills/products/snowball-cn/SKILL.md` | Product card | New content — A-share Snowball reference |
| `skills/README.md` | Doc | Backfill recipe (see §7) |
| `skills/procedures/high_board/.gitkeep` | Marker | Keeps the empty `high_board/` source path valid for `backend.ls()` until the first high_board procedure ships |

Total V1: **8 content files + 2 housekeeping files**.

### First-slice content sketches

Detailed content goes into implementation; sketches that pin shape and length:

**`skills/products/snowball-cn/SKILL.md`**

```markdown
---
name: snowball-cn
description: A-share Snowball (雪球) autocallable structured product reference.
  Read before pricing, diagnosing, or quoting any CN-market Snowball position.
  Covers payoff invariants (monthly KO obs, daily KI obs, coupon accrual),
  QuantArk engine selection, and CN-specific quirks (CSI 300 / CSI 500
  underlyings, T+1 settlement, ACT/365 day-count).
metadata:
  tier: product-card
  market: CN
  product_types: snowball
---

## What it is
...

## Key invariants
- KO observation: monthly on schedule
- KI observation: daily continuous-monitoring approximation
- Coupon: linear accrual until KO triggers
- Tenor: typically 24m

## Pricing engine & market inputs
- QuantArk engine: `SnowballMCEngine` (Monte Carlo with daily KI grid)
- Required inputs: spot, vol surface (or flat ATM), r, q, dividend schedule
- Sensitive to: vol regime near KI barrier; expected dividend yield

## Market quirks
...

## Common diagnostics signals
- Spot within 5% of KI: elevated gamma; flag for hedge review
- Within 1 obs cycle of KO: revalue with fresh vol
- Stale q (>5 BD): refetch via `fetch_market_snapshot`

## See also
- Procedure: snowball-position-diagnostics
```

**`skills/procedures/trader/snowball-position-diagnostics/SKILL.md`**

```markdown
---
name: snowball-position-diagnostics
description: Walk through a Snowball portfolio's PRICING health — KO/KI distance
  vs current spot, observation-date proximity, coupon accrual progress, and
  stale-input checks. Read when the user asks "is the snowball book OK", "any
  positions near KO", "how close to KI", or before a Snowball repricing run.
  Pairs with the snowball-cn product card.
allowed-tools: get_positions get_latest_position_valuations fetch_market_snapshot price_positions
metadata:
  tier: procedure
  persona: trader
  related_products: snowball-cn
---

## When this applies
- User asks about Snowball book health, KO/KI distance, or autocall proximity.
- User requests a Snowball repricing — run diagnostics BEFORE proposing
  `price_positions`.

## Inputs to inspect first
1. `get_positions(portfolio_id, product_type="snowball")`.
2. `get_latest_position_valuations(portfolio_id)`.
3. Read the `snowball-cn` product card if not already loaded this session.

## Step sequence
1. For each position, compute `(spot - KI) / spot` and `(KO_next - spot) / spot`
   from the stored valuation rows.
2. Flag positions where spot is within 5% of KI (gamma risk) or within 2% of
   next KO (impending autocall).
3. Check the `Latest pricing run` line in the context. If older than 1 BD AND
   any position is flagged, propose a fresh `price_positions` run
   (cost-preview first per policy).
4. Check coupon accrual: stored `accrued_coupon` vs days-since-trade × daily-accrual.

## What success looks like
A short report: "<N> positions, <K> flagged near KI (list), <M> flagged near
KO (list), accrual sane / drift detected, pricing run age = <X> BD."

## Tool preferences
- READ-FIRST: `get_latest_position_valuations`, `get_positions`,
  `fetch_market_snapshot`. No HITL.
- COMPUTE: `price_positions` ONLY after cost-preview, ONLY if data is stale or
  flagged positions warrant a fresh price.
- Do NOT use `price_product` for diagnostics — that's for new ad-hoc specs.
```

**`skills/procedures/risk_manager/snowball-position-diagnostics/SKILL.md`** — same shape, risk-lens content:

```markdown
---
name: snowball-position-diagnostics
description: Walk through a Snowball portfolio's RISK health — delta/gamma
  concentration near KI barrier, vega exposure to vol regime, autocall-day
  Greek discontinuities, and hedge feasibility. Read when the user asks about
  Snowball risk, exposure, hedge sizing, or "what breaks if vol spikes".
  Pairs with the snowball-cn product card.
allowed-tools: get_positions get_latest_risk_run calculate_risk recommend_hedge run_risk
metadata:
  tier: procedure
  persona: risk_manager
  related_products: snowball-cn
---

## When this applies
- User asks about Snowball risk, hedge feasibility, or scenario stress.
- Before approving a new Snowball quote whose risk impact isn't in the latest
  stored run.

## Inputs to inspect first
1. `get_positions(portfolio_id, product_type="snowball")`.
2. `get_latest_risk_run(portfolio_id)`.
3. Read the `snowball-cn` product card if not already loaded.

## Step sequence
1. From `get_latest_risk_run`, isolate delta and gamma contributions per
   Snowball position.
2. Identify positions whose `(spot - KI) / spot < 0.05` — gamma spike zone —
   and sum their delta + gamma.
3. Read the vega: if `|vega_total| > <site limit>`, recommend a vega hedge
   (call `recommend_hedge`).
4. If the latest risk run is older than 1 BD OR any position is in gamma-spike
   zone, propose `run_risk` (cost-preview first per policy).

## What success looks like
"<N> positions, vega = X, delta = Y, gamma = Z; <K> positions in gamma-spike
zone (list); within limits / breach detail; recommended hedge if any."

## Tool preferences
- READ-FIRST: `get_latest_risk_run`, `get_positions`. No HITL.
- COMPUTE: `calculate_risk` for hypothetical hedge snapshots; `run_risk` ONLY
  after cost-preview.
- Do NOT propose hedges without quantifying the metric the hedge would shift.
```

Note the **deliberate shared name** — `snowball-position-diagnostics` exists in both `procedures/trader/` and `procedures/risk_manager/`. Each persona's `SkillsMiddleware` only sees its own source dir, so catalogs never collide. The shared name reinforces that this is one *concept* viewed through two lenses.

---

## 6. Orchestrator integration & the cross-persona case

### Minimal change to `orchestrator.md`

A new section inserted after the existing "Routing" block:

```markdown
## Naming skills in delegations

When you delegate via `task(...)`, **name the skill** you expect the persona to
use. Phrase it in plain English at the top of the `description` argument:

  > "Use `snowball-position-diagnostics`. Walk through portfolio_id=42 and
  >  report positions near KI or near next KO."

The persona will see this and `read_file` the matching `SKILL.md` from its
catalog before invoking domain tools. You do NOT need to know what's in the
skill — its name is enough. Name at most one procedure skill per delegation;
product cards are picked up by the persona based on the work.

If you don't know which skill applies, delegate without naming one. The
persona's catalog (visible to it, not to you) will let it pick on its own — but
naming the skill is a clearer audit signal and is preferred when the request
matches a known procedure.
```

### Routing matrix delta

```markdown
| Request shape                                          | Persona       | Suggested skill                  |
|--------------------------------------------------------|---------------|----------------------------------|
| Snowball book health, KO/KI proximity, autocall risk   | trader        | snowball-position-diagnostics    |
| Snowball risk, hedge feasibility, gamma near KI        | risk_manager  | snowball-position-diagnostics    |
```

All other existing routing rules (Clarification protocol, Cost-preview rule, Stored-number rule, Compound queries, Batch-size-1 HITL rule) are unchanged.

### Cross-persona compound flow — concrete trace

**User input:** "Is the snowball book OK? I want pricing and risk."

**Turn 1 — Orchestrator clarification.** Existing clarification protocol confirms target portfolio. Orchestrator plans a compound flow.

**Turn 2 — Orchestrator → trader.**

```python
task(
  subagent_type="trader",
  description=(
    "Use `snowball-position-diagnostics`. Walk through the Snowballs Container "
    "portfolio (id=42) for pricing health: KO/KI distance, autocall proximity, "
    "stale-input check. Read only — do NOT propose price_positions yet."
  ),
)
```

Trader sees: identity + 5 policy fragments + output style + routing-from-skills directive (system prompt); catalog with `snowball-position-diagnostics` and `snowball-cn`; the task description above. Trader reads both SKILL.md files, runs reads-only diagnostic, reports.

**Turn 3 — Orchestrator → risk_manager.**

```python
task(
  subagent_type="risk_manager",
  description=(
    "Use `snowball-position-diagnostics`. Walk through portfolio_id=42 for risk "
    "health: delta/gamma near KI, vega exposure, hedge feasibility. Trader "
    "flagged 3 positions within 5% of KI: [list]. Read latest risk run; "
    "propose run_risk only if stale."
  ),
)
```

Risk_manager sees the **same skill name** in its catalog but a **different body** — the risk-flavored procedure. Same product card.

**Turn 4 — Orchestrator synthesis.** Combines findings, cites which persona produced which fact (existing "Compound queries" rule unchanged).

### What the orchestrator does NOT do

- It does not `read_file` any SKILL.md. It only references skill names.
- It does not load skills itself in V1 (`skills=[]` on `create_deep_agent`).
- It does not apply skill-matching heuristics in code. Routing remains LLM-driven, governed by the orchestrator prompt + the persona's catalog.

The single-persona shorthand: when the request is single-persona, the orchestrator skips the compound dance — one delegation with the skill name, persona returns, orchestrator synthesizes. The skill mechanism collapses to a single hop; the cross-persona machinery is paid for only when needed.

---

## 7. Testing strategy & migration cutover

### Test tiers

**Tier A — Unit tests on `skills_loader.py`**

- `load_policy_fragments(["read-before-compute", "cost-preview"])` produces concatenated content with the right `\n\n` separator and preserves each fragment's `## ` headers.
- Missing fragment name raises a clear `FileNotFoundError` with the path — fail-fast at agent build time, never silently.
- `compose_persona_prompt(identity, [])` is a no-op except for trimming.

**Tier B — Catalog assembly integration tests**

Build the orchestrator with the new wiring, then assert each persona's `skills_metadata` contains exactly:

| Persona | Expected skill names in catalog |
|---|---|
| trader | `snowball-position-diagnostics`, `snowball-cn` |
| risk_manager | `snowball-position-diagnostics`, `snowball-cn` |
| high_board | *(empty in V1)* |

This proves the per-subagent `SkillsMiddleware` isolation actually works.

**Tier C — `read_file` smoke test (no HITL)**

A direct call to `read_file("/skills/products/snowball-cn/SKILL.md", limit=1000)` through the orchestrator's filesystem path. Asserts no HITL interrupt fires and the body is returned.

**Out of scope for V1** — End-to-end behavioral tests of compound delegations. Those need recorded LLM transcripts or deterministic stub models; value-per-effort is low for V1. Punt to a follow-on plan.

### Cutover steps (single PR, additive throughout)

1. **Scaffold.** Create `skills/` tree: `policy/`, `procedures/{trader,risk_manager,high_board}/`, `products/`. Add `procedures/high_board/.gitkeep` so the empty source path is valid for `backend.ls()` from day one. Add a stub `skills/README.md`.
2. **Extract policy fragments.** Copy each policy section out of `prompts/trader.md` (and risk_manager / high_board variations) into `skills/policy/<name>.md`. Aim for byte-identical content — this step must not change agent behavior.
3. **Trim persona prompts.** Remove the extracted sections from `prompts/trader.md`, `prompts/risk_manager.md`, `prompts/high_board.md`. Add the "Routing from skills" directive.
4. **Add `skills_loader.py`** (~15-20 lines).
5. **Wire `personas.py`** — each persona uses `compose_persona_prompt(...)` and passes `skills=[...]` on the SubAgent spec.
6. **Update `orchestrator.py`** — add `/skills` permission rule; wire a `FilesystemBackend` (or extend the current backend after verifying its constructor on day 1).
7. **Update `prompts/orchestrator.md`** — add "Naming skills in delegations" + Routing-matrix update.
8. **Author the first-slice content.** Write `snowball-cn` product card + both `snowball-position-diagnostics` SKILL.md files. This is the only step that introduces *new* knowledge; everything before is reshaping.
9. **Add tests** (Tier A + Tier B + Tier C).
10. **Run the existing test suite** — should pass unchanged.

### Rollback

Pure git revert. The skills layer is additive and behavior-preserving up to step 8. Reverting the PR returns the agent to its current state; no data migration is required.

### Defense-in-depth: tighter policy path permissions

Policy fragments are loaded at agent build time via `Path.read_text()` from the OS filesystem — they never flow through the agent's virtual filesystem. If we want to fully prevent the persona from ever `read_file`-ing a policy fragment (no business reason; the content is already in its system prompt), we can tighten the filesystem permission to `/skills/procedures/**` + `/skills/products/**`. Not necessary for correctness — optional hardening switch.

### Backfill recipe (small `skills/README.md`)

**Add a procedure skill:**
1. `mkdir skills/procedures/<persona>/<skill-name>/`
2. Author `SKILL.md` with frontmatter + 5-section body schema.
3. Add a row to the Routing matrix in `prompts/orchestrator.md`.
4. Add a Tier-B test row asserting catalog presence.

**Add a product card:**
1. `mkdir skills/products/<product-id>/`
2. Author `SKILL.md` with the recommended sections.
3. Reference it from any related procedure skill's `metadata.related_products`.

**Add a policy fragment (rare):**
1. Author `skills/policy/<name>.md` with an opening `## ` header.
2. Add the fragment name to the relevant persona allowlist in `personas.py`.
3. If the fragment applies to a subset of personas, document the rationale in the fragment body.

### Risk register

| Risk | Mitigation |
|---|---|
| Orchestrator forgets to name the skill; persona doesn't auto-read it. | Catalog description is written to be strong enough to self-trigger. Orchestrator Routing matrix is the explicit hint surface. |
| SKILL.md descriptions drift from procedure content. | 5-section schema makes drift visible on review; description sentence is canonical. |
| Two persona `SkillsMiddleware` instances accidentally share a catalog. | Tier-B test asserts per-persona isolation directly. |
| `read_file` accidentally HITL-paused by future config change. | Tier-C smoke test guards against it. |
| `FilesystemBackend` constructor differs from sketch in §4. | Implementation plan verifies on day 1; spec wiring is deliberately abstract about the constructor shape. |

---

## 8. Future work (post-V1, separate writing-plans cycles)

- **Orchestrator routing skills** (`skills/routing/`). Once ≥3 procedure skills exist and the orchestrator prompt's Routing matrix has visible duplication, lift compound-flow recipes ("compound RFQ flow", "snowball book audit") into routing skills the orchestrator loads via its own `skills=[...]`.
- **Remaining procedure skills:** `rfq-intake-and-quote`, `portfolio-pricing-run`, `risk-report-workflow`, `market-data-profile`. Each is its own writing-plans cycle.
- **Additional product cards:** `phoenix-cn`, `phoenix-hk`, `accumulator-cn`, `vanilla-european`. Author cards as new product types are onboarded.
- **Skill-discovery loop tooling.** A short conversation-analysis ritual to capture recurring agent corrections / failure patterns as candidate skills. Out of scope for the infrastructure spec, but the desk's intended ongoing practice once the layer ships.
- **`allowed-tools` enforcement.** Promote `allowed-tools` from soft hint to a hard runtime filter once deepagents marks the enforcement non-experimental, or via a custom middleware if we want it sooner.

---

## Glossary

- **Skill** — A markdown file (`SKILL.md`) with YAML frontmatter + body, loaded by `SkillsMiddleware`, surfaced to a persona's catalog, and read on demand via `read_file`. In V1, either a *procedure* or a *product card*.
- **Policy fragment** — A markdown file under `skills/policy/` that is concatenated into a persona's system prompt at agent build time. Not a `SkillsMiddleware` skill; not in the runtime catalog.
- **Procedure skill** — A persona-scoped, on-demand SKILL.md describing a self-contained workflow (when it applies → inputs to inspect → step sequence → outcome → tool preferences).
- **Product card** — A product-scoped, on-demand SKILL.md describing payoff invariants, pricing engine, market quirks, and diagnostic signals. Shared across personas.
- **Catalog** — The list of `(name, description, path)` triples for skills available to a given persona, auto-injected into its system prompt by `SkillsMiddleware`.
- **Progressive disclosure** — The pattern where only skill metadata (the catalog) is in context by default; full SKILL.md content is fetched on demand via `read_file`.

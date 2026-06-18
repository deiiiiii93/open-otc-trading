# Agent Envelopes, Skill Redesign, and Service Refactor — Design

**Date:** 2026-05-19
**Status:** Draft, pending user review
**Scope:** Desk agent, pet agent, tool envelopes, page context contracts, skill taxonomy, service layer, CLI.

## Problem

Recent threads with the desk and pet agents exposed four related gaps:

1. **Desk and pet agents behave too similarly.** Both UIs (`AgentDesk`, `FloatingAgentMiniChat`) call the same `useAgentChatController` → the same `deep_agent/orchestrator.py`. The pet inherits the desk's clarification protocol, persona routing, and HITL/cost-preview ceremony even when the user is sitting on a page that fully disambiguates their intent. Thread #18 illustrates: the user typed "Dup" five times because the orchestrator kept asking for clarification despite a clearly named target. Thread #19's "give me more info on Position 21" → "why is it so big?" → "yes price position 21 now" exposes the other tail of the same problem — there's no controlled escalation path from a small page question to a diagnostic deep-dive.
2. **Skill contamination.** Skill bodies carry git-archaeology phrases like `"NOT \`valuation.id\` — v1 commit \`73b0ae7\` fixed this mistake"` (`domains/position/position-snapshot/SKILL.md:26`), `"see git commit \`c0ae172\`"` (same file:25), and "v2 additions (2026-05-15) — grandfathered as self-contained" (`skills/README.md:115`). The agent must filter "advice the past me wrote myself" out of "advice meant for me" every time it reads a skill.
3. **Tools entangle DB plumbing with LLM contracts.** `backend/app/services/langchain_tools.py` is 1608 lines with 37 `@tool` definitions. Each tool opens a `database.SessionLocal()`, runs ORM queries, shapes JSON output. There is no reusable service layer — the same logic cannot be invoked from a script, the CLI, or a test without going through the LLM tool decorator.
4. **Skill writing does not follow current best practice.** SKILL.md bodies are long (~1000+ tokens for `snowball-position-diagnostics`), abstract-rule-heavy, and lack concrete examples. Anthropic's [The Complete Guide to Building Skill for Claude](https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf) recommends thin discriminating descriptions, short bodies, examples-first authoring, and progressive disclosure via `references/`.

## Goals

- Make desk and pet behavior visibly different through **capability-scoped envelopes** sharing **one runtime**, not two parallel agent systems.
- Allow automatic escalation when a page-scoped question becomes diagnostic or workflow-heavy.
- Enforce behavior through **tool envelopes** (capability gating), not only prompt wording.
- Redesign skills around **business workflows** with envelope metadata and strict frontmatter.
- Make **page context** a typed contract that tells the agent whether to count from snapshot, query, or escalate.
- Extract a **shared service layer** with a parallel Typer CLI; tools become thin wrappers.
- Keep skills, services, and the runtime honest through CI tests (lint + fixtures).

## Non-Goals

- Do not build two independent agent systems (no sibling `pet_agent/` module).
- Do not route every tool call through a subprocess CLI.
- Do not rewrite every skill before fixing the runtime boundary.
- Do not make the pet a generic database or filesystem explorer.
- Replacing LangGraph or the checkpointer.
- Changing `agent_threads` / `agent_messages` schema.
- Touching `sandbox_tool.py` (`run_python`) or `reply_options/tool.py`.
- Multi-page pet context.
- Per-user implicit-confirm overrides.

## Core architecture

**One shared agent runtime with multiple operating envelopes.** An envelope is a typed capability scope — it determines which tools the runtime grants and what behavioral policy applies.

Four envelopes:

| Envelope | Origin | Purpose |
|---|---|---|
| `pet_page` | Page assistant (`FloatingAgentMiniChat`) | Page-scoped direct answers and page-native actions. |
| `pet_diagnostic` | Auto-escalation from `pet_page` | Page-originated diagnostic questions with domain reads. |
| `desk_workflow` | Agent Desk (`AgentDesk`) | Multi-step work, planning, controlled writes, cross-page context. |
| `desk_async` | Auto-escalation from `desk_workflow` | Long-running or decomposed background work. |

UI entry points set the initial envelope; transitions are automatic and capability-driven.

```
pet_page → pet_diagnostic → desk_workflow → desk_async
```

The user does not approve mode switches. The system persists transition metadata for debugging: `previous_envelope`, `new_envelope`, `escalation_reason`, `selected_skill`, `granted_tool_groups`.

### Layering

Cross-cutting layers under `backend/app/`:

- `services/domains/<domain>.py` (new) — pure Python service functions. No LangChain, no Pydantic-tool decorators, no JSON shaping.
- `tools/<domain>.py` (new) — `@tool` wrappers calling services and shaping JSON for the LLM. Replaces `langchain_tools.py`.
- `cli/<domain>.py` (new) — Typer commands calling services and formatting for terminal.
- `skills/` (moved from `deep_agent/skills/`) — shared skill catalog. Read by the shared runtime; envelope determines which workflows are eligible.
- `services/deep_agent/{orchestrator.py,personas.py,skills_loader.py}` (existing, modified) — the shared runtime. Takes an `envelope: Envelope` parameter; persona selection, tool gating, capability gates, and prompt composition derive from it. The "orchestrator" framing is retained for the file name but the role widens: it is the envelope-aware runtime, not just a delegation hub.

```
+-----------------------------+     +-----------------------------+
|   AgentDesk (full page)     |     | FloatingAgentMiniChat       |
|   envelope: desk_workflow   |     |   envelope: pet_page        |
+--------------+--------------+     +--------------+--------------+
               \                                   /
                \                                 /
                 v                               v
              +---------------------------------+
              |  shared runtime                 |
              |  envelope router                |
              |  + capability gate              |
              |  + escalation rules             |
              |  + skill loader                 |
              |  + LangGraph agent loop         |
              +-----------------+---------------+
                                |
                                v
              +---------------------------------+
              |   app/tools/<domain>.py         |
              |   (LangChain @tool wrappers)    |
              +-----------------+---------------+
                                |
                                v
              +---------------------------------+
              |   app/services/domains/*.py     | <-- also called by:
              |   (pure Python, no LLM)         | <-- app/cli/<domain>.py
              +-----------------+---------------+
                                |
                                v
                SQLAlchemy models + DB session
```

## Envelope catalog

Each envelope grants a tool-group set. Skills declare which envelopes they are valid in; the runtime gates accordingly.

### `pet_page`

Allowed tool groups: page-context reads, page entity detail, page-native action APIs, task-status polling, deterministic Python counting.

Blocked: filesystem exploration, broad database queries, async delegation, cross-page investigation.

Behavior: terse replies, no clarification protocol unless genuinely ambiguous, trust `page_context.loaded_context.completeness`.

### `pet_diagnostic`

Allowed: everything in `pet_page`, plus position detail, portfolio membership, latest pricing result, latest risk result, pricing profile, market-data profile, product terms, compact Python analysis.

Blocked: generic filesystem access, broad database exploration, async delegation by default.

Reached via auto-escalation from `pet_page` (see Escalation rules).

### `desk_workflow`

Allowed: broader domain reads, controlled writes (HITL/YOLO-gated), request-queue creation, repricing, risk runs, report creation, cross-page workflow tools.

Behavior: full clarification protocol, cost-preview, batch-size-1 HITL.

### `desk_async`

Allowed: async-agent dispatch, long-running diagnostic orchestration, task monitoring, artifact retrieval.

Constraint: deterministic business jobs (pricing, risk, reporting) remain backend task APIs. Async agents coordinate and explain; they are not the business-action source of truth.

## Desk agent contract

The desk is the heavy-workflow surface. Appropriate when the user starts from incomplete context, asks for investigation, asks for planning, or needs cross-domain work.

Workflow:

1. Clarify intent only when needed.
2. Gather the relevant context.
3. Plan actions.
4. Use domain tools, controlled write tools, or async orchestration as needed.
5. Synthesize a result with assumptions, evidence, and next steps.

The desk may use broad domain reads, workflow tools, controlled write actions, task monitoring, report generation, and async agents. Write actions follow YOLO/HITL policy.

## Pet agent contract

The pet is the page-first assistant. It starts from the active page and avoids rediscovering context the page already provides.

Workflow:

1. Read active page context as the primary context.
2. Infer whether the user is asking for a page fact, page-native action, or missing-term clarification.
3. Use page-scoped tools or small deterministic computation.
4. Execute page-native backend actions when allowed by YOLO/confirmation policy.
5. Monitor backend task status when an action starts a task.
6. Return concise progress or results.

The pet should not browse files, inspect raw database tables, or perform broad discovery by default. If the active page contract is insufficient, it either uses the page's `query_ref` or escalates (see Escalation rules) — it does not silently widen its scope.

## Escalation rules

Escalation is automatic and capability-based, not keyword-only. Enumerated reasons:

| Reason | Trigger |
|---|---|
| `missing_required_context` | Skill declares `required_context` the page doesn't expose. |
| `diagnostic_followup` | User follow-up needs domain reads beyond the page context (e.g., "why is the delta so big?"). |
| `cross_page_dependency` | Task references entities outside the current page (e.g., another portfolio). |
| `write_action_requested` | User asks for a persisted action not in the page's `actions[]` whitelist. |
| `long_running_work` | Cost estimate exceeds 30s for the requested compute. |
| `large_result_set` | Domain read would exceed in-context size budget. |
| `tool_denied_by_envelope` | Skill or model requests a tool the current envelope blocks. |

Transition rules:

```
pet_page → pet_diagnostic     on diagnostic_followup, missing_required_context (within same domain)
pet_page → desk_workflow      on write_action_requested (not in page actions), cross_page_dependency
pet_diagnostic → desk_workflow on cross_page_dependency, write_action_requested
desk_workflow → desk_async    on long_running_work (>30s), explicit "background"/"async" intent
```

Escalation is one-way per turn. The runtime records the transition in `audit_events` and continues the same thread; the LLM sees a single envelope per turn.

### Escalation example

A real multi-turn thread on the Risk page, showing all three tiers:

```
Turn 1
User: what's the delta of position 21?
Envelope: pet_page
Behavior: answer from page context, or use page/domain read API for the single position.

Turn 2
User: why is it so big?
Envelope: pet_diagnostic (reason: diagnostic_followup)
Behavior: inspect product terms, latest risk result, pricing profile, market data, and
          likely drivers. Stays page-anchored but reads beyond the page snapshot.

Turn 3
User: compare it to previous runs and reprice under vol 30%.
Envelope: desk_workflow (reason: write_action_requested + cross-page_dependency)
          may further escalate to desk_async if the reprice exceeds 30s.
Behavior: plan, run controlled computations, monitor tasks, synthesize.
```

The user never sees a mode switch UI. The envelope badge in `FloatingAgentMiniChat` updates from "Pet" → "Pet · diagnostic" → "Desk" as the thread widens.

## Page Context Contract

Page context becomes a typed contract, not a loose UI snapshot:

```ts
type PageContext = {
  route: string;
  title: string;
  entity_ids: Record<string, number | string>;
  snapshot: Record<string, unknown>;
  loaded_context: {
    completeness: "complete" | "paginated" | "partial" | "empty";
    visible_count?: number;
    total_count?: number;
    query_ref?: string;
  };
  actions: PageAction[];
};

type PageAction = {
  name: string;           // e.g., "run_risk"
  required_ids: string[]; // e.g., ["portfolio_id", "pricing_profile_id"]
  confirmation: "implicit" | "explicit" | "destructive";
  backend_endpoint: string; // e.g., "POST /api/risk/runs"
};
```

`loaded_context.completeness` directly answers "how many positions do we have?" — `"complete"` means count from `snapshot`, `"paginated"` means use `query_ref` to count full membership, `"partial"` means escalate.

`actions[]` bridges the user mental model of "click the page button" with backend-first execution. The agent calls actions by `name`; the runtime resolves to `backend_endpoint` and executes server-side. Human-style UI driving is the fallback only when a stable backend action contract does not exist.

### Required page contracts

#### Positions

Must expose: selected `portfolio_id` and `kind`; `loaded_context` with `total_count` and `query_ref` when paginated; actions `count_positions`, `price_visible_positions`, `price_portfolio_positions`, `open_position_detail`.

Behavior: if `completeness == "complete"`, count from loaded context; otherwise use `query_ref` or `portfolio_id` to count full membership.

#### Risk

Must expose: selected `portfolio_id` and `pricing_profile_id`; latest `risk_run_id` if available; risk totals and compact position rows; `running_task_id` if available; actions `run_risk`, `read_risk_result`, `get_task_status`.

Behavior: `rerun risk` maps to action `run_risk`. Pet uses current page ids. Pet monitors the returned task id and reports completion or timeout.

#### Try Solve

Must expose: selected `row_id`; `product_key` and `label`; selected row fields; market inputs; quote request; diagnostics; solver state; compact request-queue summaries; actions `solve_imported_row`, `create_request_queue_item`.

Behavior: if user provides incomplete product terms, clarify missing terms. Once sufficient, create a request-queue item through the action API. Do not search files or raw DB tables to rediscover the active row.

#### Portfolios

Must expose: selected `portfolio_id`, `kind` (`container | view`), `filter_rule`, `source_portfolio_ids`, `manual_includes`, position-count summary; actions `run_risk`, `save_filter_rule`, `add_position_sources`, `remove_position_sources`, `delete_portfolio` (`confirmation: "destructive"`).

Behavior: for view-portfolio queries, use the portfolio-membership service rather than counting `Position.portfolio_id` directly. "Run risk on this portfolio" maps to action `run_risk` with the page's `portfolio_id`. Deletes always require explicit confirmation regardless of YOLO state.

#### Pricing Parameters

Must expose: active `pricing_parameter_profile_id`; profile metadata; build readiness state (missing underlyings, missing input categories); latest snapshot timestamps per underlying; actions `build_default_profile`, `refresh_all_spots`, `refresh_from_positions`, `upsert_default_input`.

Behavior: "what's missing" answers from the build-readiness snapshot, no tool call. "Refresh spots" maps to `refresh_all_spots` action (implicit on this page when YOLO is on). Build-default and refresh-from-positions can exceed 30s — cost-preview escape hatch fires.

#### Market Data

Must expose: selected `market_data_profile_id`; selected period; the candle-chart's visible window; per-underlying snapshot freshness; actions `fetch_snapshot`, `refresh_all` (optional `use_proxy: boolean`).

Behavior: spot reads come from `loaded_context.snapshot` when freshness is acceptable. `refresh_all` is implicit on this page (it's the page's primary button). "Why is the spot stale" answers from snapshot timestamps without escalating.

#### RFQ Approval

Must expose: selected `rfq_id`; RFQ status; current terms; current quote; status history; actions whose availability is **status-derived** (the page exposes only the actions valid for the current status):

| Status | Available actions |
|---|---|
| `draft`, `submitted`, `pricing_failed`, `pending_approval` | `quote_rfq` |
| `pending_approval` | `approve_rfq`, `reject_rfq` |
| `approved` | `release_rfq` |
| `released` | `mark_rfq_client_accepted` |
| `client_accepted` | `book_rfq_to_position` |

Behavior: `approve_rfq` / `reject_rfq` / `release_rfq` / `mark_rfq_client_accepted` are `confirmation: "explicit"` — pet prepares the call and asks unless YOLO is enabled. `book_rfq_to_position` is `confirmation: "destructive"` (it materializes a position) and always requires confirmation.

#### Reports

Must expose: list of `report_id`s with status and artifact paths; selected `report_id` if any; the `portfolio_id` scope of the selected report; actions `create_report`, `read_report`, `download_artifact`.

Behavior: "show me the latest report" reads from `loaded_context.snapshot` — no tool call. "Create a new report" requires `portfolio_id`; if the page's selection doesn't pin one, ask once. `create_report` always exceeds 5s, so it stays `confirmation: "explicit"` even on this page. Reading and downloading existing artifacts are pure reads, no confirmation.

#### Tasks

Must expose: list of in-flight and recent `task_id`s with status, type, age, and parent thread reference; selected `task_id` if any; actions `get_task_status`, `cancel_async_agent` (`confirmation: "explicit"`).

Behavior: status reads from `loaded_context.snapshot` directly. Cancellation always requires explicit confirmation. The Tasks page does NOT originate new business actions (no `run_risk`, no `price_positions`) — those originate from their source pages. The pet on Tasks is observational + cancellation only.

#### Pages without a pet contract

Pages outside this list (e.g., `ClientRfq`, `AgentDesk` itself) do not declare a pet contract. The pet still works on those pages but gets only the default `pet_page` envelope with no `actions[]`; any write request escalates to `desk_workflow` with reason `write_action_requested`.

## Backend Action API first

Page actions execute through backend action APIs, not by literally driving the UI:

- The API is easier to test.
- The API can share business logic with the UI button.
- The API returns structured task ids and status.
- It avoids brittle browser automation.

Concretely: every action in `PageAction[]` resolves to an HTTP endpoint. The action's behavior is implemented in `services/domains/<domain>.py` and exposed via the existing FastAPI router. The page's button and the agent's tool call go through the same service function.

## YOLO and confirmation policy

The pet reads and analyzes page context without confirmation.

For page-native actions:

- If YOLO/direct-action is enabled, the pet executes actions whose `confirmation` is `"implicit"` directly.
- If YOLO is disabled, the pet prepares the exact action and asks for confirmation (single inline button in `FloatingAgentMiniChat`).
- Actions with `confirmation: "destructive"` remain gated unless explicitly whitelisted (e.g., `delete_portfolio` would always be gated).

**Cost-preview escape hatch.** Even on `implicit` actions, if the runtime cost estimator returns >30s (e.g., `run_risk` on a 200-position book), force a one-shot confirmation. Lives in `services/domains/<domain>.estimate_<action>_seconds(...)` and is shared between the pet's gate and the desk's cost-preview policy.

## Skill taxonomy

Workflow-first organization. Replaces the v2 `domains/`, `procedures/`, `routing/`, `products/` tiering.

```
app/skills/
├── workflows/
│   ├── risk/
│   │   ├── run-risk/
│   │   ├── read-risk-result/
│   │   └── create-risk-report/
│   ├── positions/
│   │   ├── position-snapshot/
│   │   ├── position-inputs/
│   │   └── position-diagnosis/
│   ├── try-solve/
│   │   ├── solve-imported-row/
│   │   └── create-request-queue-item/
│   ├── pricing/
│   │   ├── price-product/
│   │   └── price-portfolio/
│   ├── market-data/
│   │   ├── fetch-market-data/
│   │   └── explain-market-data-drift/
│   ├── portfolios/
│   │   ├── portfolio-membership/
│   │   └── portfolio-view-counting/
│   ├── rfq/
│   │   ├── intake-request/
│   │   ├── draft-rfq/
│   │   ├── quote-rfq/
│   │   └── submit-for-approval/
│   ├── reporting/
│   │   ├── create-report/
│   │   ├── batch-run-reports/
│   │   └── display-report/
│   └── snowballs/
│       ├── snowball-term-interpretation/
│       ├── snowball-pricing/
│       └── snowball-risk-explain/
├── meta/
│   ├── pet-page-contract.md
│   ├── pet-diagnostic-contract.md
│   ├── desk-workflow-contract.md
│   ├── desk-async-contract.md
│   ├── escalation-policy.md
│   ├── yolo-hitl-policy.md
│   ├── page-context-contract.md
│   ├── clarification-policy.md
│   ├── cost-preview-policy.md
│   ├── read-before-compute-policy.md
│   ├── reply-options-policy.md
│   └── python-analysis-policy.md
└── references/
    ├── products/
    │   └── snowball-cn.md
    ├── pricing/
    │   └── engines.md
    ├── market-data/
    │   └── conventions.md
    ├── portfolios/
    │   └── model.md
    └── rfq/
        └── lifecycle.md
```

Workflow skills hold business knowledge. Meta files hold operating behavior (runtime policy). References hold durable formulas, conventions, product mappings, and domain definitions.

## Skill schema

Every workflow skill has strict frontmatter:

```yaml
---
name: position-diagnosis
description: Diagnose why a position has an unexpected value, Greek, PnL, price, or risk contribution.
domain: positions
workflow_type: diagnostic       # diagnostic | action | read | compound
allowed_envelopes:
  - pet_diagnostic
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - position_id
optional_context:
  - portfolio_id
  - pricing_profile_id
  - market_data_profile_id
  - risk_run_id
write_actions: false
confirmation_required: false
success_criteria:
  - identifies the observed value
  - compares it with product terms and latest pricing or risk inputs
  - states likely drivers and uncertainty
---
```

Body sections:

1. **When to use** — 3–5 bullets expanding `description`.
2. **Required inputs** — what must be present in `page_context` or arguments.
3. **Procedure** — numbered steps, each names the tool or service.
4. **Stop conditions** — when to escalate, ask, or give up.
5. **Output shape** — what the final reply contains.
6. **References** — pointers to `references/*.md` for long-form content.

### Anthropic-guide enforcement

Adopted as CI lint rules:

| Rule | Mechanism |
|---|---|
| Description discriminates triggering | `description:` ≤200 chars, says when to use (not what it is). Fail if starts with "This skill…" / "Skill for…" / "Documentation about…". |
| Body ≤500 tokens | `tiktoken.count(body) <= 500`. Frontmatter not counted. Long content moves to `references/<skill>/*.md`. |
| Examples > rules | Mandatory `## Example` or `## Examples` heading with at least one concrete trigger phrasing + expected output shape. |
| No archaeology | Regex on `commit \`[0-9a-f]{6,}\`|v1 (commit|anchor|added)|fixed this mistake|grandfathered|—v\d`. Body must not match. |
| Frontmatter discipline | All required fields present. `allowed_envelopes` values are valid envelopes. `workflow_type` is one of the enumerated values. |
| Structural cleanliness | Workflow skills must not contain runtime policy that belongs in `meta/`. Meta policies must not duplicate workflow procedure. (Structural test: workflow bodies cannot match `meta/`-only phrasings like "YOLO/HITL policy", "envelope transitions", "escalation reasons"; meta bodies cannot reference specific business-logic step sequences.) |

### Migration map

Verbatim from the architectural anchor:

| Current path | New destination |
| --- | --- |
| `domains/risk/risk-run-propose` | `workflows/risk/run-risk` |
| `domains/risk/risk-snapshot-read` | `workflows/risk/read-risk-result` |
| `procedures/risk_manager/risk-report-workflow` | `workflows/risk/create-risk-report` |
| `domains/position/position-snapshot` | `workflows/positions/position-snapshot` |
| `domains/position/position-input-enumerate` | `workflows/positions/position-inputs` |
| `procedures/risk_manager/snowball-position-diagnostics` | `workflows/positions/position-diagnosis` + `workflows/snowballs/snowball-risk-explain` |
| `procedures/trader/snowball-position-diagnostics` | `workflows/positions/position-diagnosis` + `workflows/snowballs/snowball-risk-explain` |
| `domains/pricing/price-product-adhoc` | `workflows/pricing/price-product` |
| `domains/pricing/pricing-run-propose` | `workflows/pricing/price-portfolio` |
| `procedures/risk_manager/portfolio-pricing-run` | `workflows/pricing/price-portfolio` |
| `procedures/trader/portfolio-pricing-run` | `workflows/pricing/price-portfolio` |
| `domains/pricing/pricing-engines` | `references/pricing/engines.md` |
| `domains/market-data/market-data-fetch` | `workflows/market-data/fetch-market-data` |
| `domains/market-data/market-data-drift` | `workflows/market-data/explain-market-data-drift` |
| `domains/market-data/market-data-conventions` | `references/market-data/conventions.md` |
| `procedures/trader/market-data-profile` | `workflows/market-data/fetch-market-data` + `references/market-data/conventions.md` |
| `domains/portfolio/portfolio-model` | `workflows/portfolios/portfolio-membership` + `references/portfolios/model.md` |
| `domains/rfq/rfq-draft` | `workflows/rfq/draft-rfq` |
| `domains/rfq/rfq-lifecycle` | `workflows/rfq/intake-request` + `references/rfq/lifecycle.md` |
| `domains/rfq/rfq-quote` | `workflows/rfq/quote-rfq` |
| `domains/rfq/rfq-submit-for-approval` | `workflows/rfq/submit-for-approval` |
| `procedures/trader/rfq-intake-and-quote` | `workflows/rfq/intake-request` + `workflows/rfq/quote-rfq` |
| `domains/reporting/report-create-propose` | `workflows/reporting/create-report` |
| `domains/reporting/report-batch-run` | `workflows/reporting/batch-run-reports` |
| `procedures/high_board/report-query-and-display` | `workflows/reporting/display-report` |
| `products/snowball-cn` | `references/products/snowball-cn.md` + workflow-specific Snowball skills |
| `routing/market-data-then-reprice` | router tests + optional `workflows/playbooks/market-data-then-reprice` |
| `routing/pricing-and-risk-compound` | router tests + optional `workflows/playbooks/pricing-and-risk-compound` |
| `routing/snowball-book-audit` | router tests + optional `workflows/playbooks/snowball-book-audit` |
| `policy/clarification-protocol.md` | `meta/clarification-policy.md` |
| `policy/cost-preview.md` | `meta/cost-preview-policy.md` |
| `policy/hitl-batch-size-1.md` | `meta/yolo-hitl-policy.md` |
| `policy/pickable-reply-options.md` | `meta/reply-options-policy.md` |
| `policy/read-before-compute.md` | `meta/read-before-compute-policy.md` |
| `policy/run-python-rfsw.md` | `meta/python-analysis-policy.md` |

## Service layer + parallel CLI

### Module layout

```
backend/app/
├── services/
│   ├── domains/                  # NEW
│   │   ├── positions.py
│   │   ├── portfolios.py
│   │   ├── pricing.py
│   │   ├── risk.py
│   │   ├── rfq.py
│   │   ├── market_data.py
│   │   ├── reporting.py
│   │   └── tasks.py
│   ├── deep_agent/               # existing, modified to take envelope param
│   │   ├── orchestrator.py       # existing — modified: accepts envelope, gates tools
│   │   ├── personas.py           # existing — modified: envelope-aware policy fragment selection
│   │   ├── envelopes.py          # NEW: Envelope, capability tables, escalation rules
│   │   ├── page_context.py       # NEW: typed PageContext + PageAction validation
│   │   ├── escalation.py         # NEW: detect reasons + replay turn with new envelope
│   │   └── ...
│   ├── async_agents/             # existing, unchanged
│   └── reply_options/            # existing, unchanged
├── tools/                         # NEW — replaces langchain_tools.py
│   ├── positions.py
│   ├── portfolios.py
│   ├── pricing.py
│   ├── risk.py
│   ├── rfq.py
│   ├── market_data.py
│   ├── reporting.py
│   └── _shaping.py
└── cli/                           # NEW
    ├── __init__.py               # the `otc` Typer app
    ├── positions.py
    ├── portfolios.py
    ├── pricing.py
    ├── risk.py
    ├── rfq.py
    ├── market_data.py
    ├── reporting.py
    └── _format.py
```

### Service contract

```python
# backend/app/services/domains/positions.py
def list_filtered(
    *,
    portfolio_id: int | None,
    product_type: str | None = None,
    status: str | None = "open",
    effective_date_from: date | None = None,
    effective_date_to: date | None = None,
    session: Session | None = None,
) -> list[Position]:
    """Return Position ORM objects matching the filters."""

def count(*, portfolio_id: int, session: Session | None = None) -> int: ...
def count_from_snapshot(snapshot: dict) -> int:
    """Pure: counts positions in a frontend-provided snapshot. No DB."""

def estimate_price_seconds(*, portfolio_id: int, session: Session | None = None) -> float: ...
```

Three rules for every service module:

1. **Pure inputs/outputs.** Takes Python primitives or ORM objects; returns Python primitives or ORM objects. No Pydantic tool-schemas, no LangChain types, no dicts intended for LLM consumption.
2. **Session-aware.** Optional `session=` parameter for callers that need multiple service calls in one transaction.
3. **Caller-shaped.** Never returns JSON-shaped dicts. Shaping happens at the edge in `app/tools/*` for LLMs and `app/cli/_format.py` for terminals.

### Tool wrappers

```python
# backend/app/tools/positions.py
from langchain_core.tools import tool
from app.services.domains import positions as positions_svc
from ._shaping import shape_position_list

@tool("get_positions", args_schema=GetPositionsInput)
def get_positions_tool(...) -> dict[str, Any]:
    """Return normalized portfolio positions for agent inspection."""
    rows = positions_svc.list_filtered(
        portfolio_id=portfolio_id,
        product_type=product_type,
        status=status,
        effective_date_from=_parse(effective_date_from),
        effective_date_to=_parse(effective_date_to),
    )
    return shape_position_list(rows, filters={...})
```

Target ≤30 lines per tool. Current average is ~50 lines; most tools shrink 5–10×.

### CLI

Typer-based. Top-level `otc` command, subcommands per domain.

```
$ otc positions count --portfolio 6
64

$ otc risk run --portfolio 6 --json
{"risk_run_id": 8, "status": "queued", "task_id": 18}

$ otc rfq draft --underlying 000852.SH --tenor 3Y --ko 1.03 --ki 0.75
Draft created: rfq_id=42  (status=draft; missing: knock_in_observation_type, coupon_rate)
```

Two output modes per command: human (default, tables/colors) and `--json` (CI-friendly). Tools do **not** shell out to the CLI — both go through the service layer directly. The CLI is the debugging twin of every agent action.

### Existing service-shaped modules

`backend/app/services/quantark.py`, `portfolio_membership.py`, `channel_registry.py` and similar single-file modules already do partial service-layer work. They are **not** migrated as part of this refactor. The new `services/domains/*.py` modules call into them where useful. A future cleanup PR may consolidate.

## Sequencing

### Phase 1 — Services + Tools + CLI (foundation)

| PR | Scope | Depends on |
|---|---|---|
| P1.1 | `services/domains/portfolios.py` + `tools/portfolios.py` + `cli/portfolios.py` (5 tools) | — |
| P1.2 | `services/domains/positions.py` + `tools/positions.py` + `cli/positions.py` (4 tools incl. `import_otc_positions`) | — |
| P1.3 | `services/domains/market_data.py` + `tools/market_data.py` + `cli/market_data.py` (2 tools) | — |
| P1.4 | `services/domains/pricing.py` + `tools/pricing.py` + `cli/pricing.py` (4 tools incl. `price_positions`, `estimate_price_seconds`) | P1.2 |
| P1.5 | `services/domains/risk.py` + `tools/risk.py` + `cli/risk.py` (3 tools incl. `run_risk`, `estimate_run_seconds`) | P1.2 |
| P1.6 | `services/domains/rfq.py` + `tools/rfq.py` + `cli/rfq.py` (11 RFQ tools incl. lifecycle) | — |
| P1.7 | `services/domains/reporting.py` + `tools/reporting.py` + `cli/reporting.py` (4 tools incl. `create_report`) | — |
| P1.8 | Delete `langchain_tools.py`. Update all callers (`personas.py`, `async_agents/agent.py`, tests) via automated codemod. | P1.1–P1.7 |

**Gate:** `tests/test_agent_tools.py`, `tests/test_async_agents_*.py`, `tests/test_agent_integration.py` must pass. New `tests/test_cli_<domain>.py` smoke tests per CLI command.

### Phase 2 — Envelopes + page context contract

| PR | Scope | Depends on |
|---|---|---|
| P2.1 | `services/deep_agent/envelopes.py`: `Envelope` enum, capability tables, escalation reason enum, transition rules. Pure data + unit tests. | — |
| P2.2 | `services/deep_agent/page_context.py`: typed `PageContext` + `PageAction` Pydantic models, server-side validation. Backward-compatible — old shape still accepted, new fields optional. | — |
| P2.3 | Frontend: extend `PageContext` type with `loaded_context` and `actions[]`. Update Positions, Risk, Try-Solve pages to populate. Other pages keep the old shape. | P2.2 |
| P2.4 | Runtime: thread the `envelope` parameter through `services/deep_agent/runner.py`. New endpoint `POST /agent/respond` accepts `envelope`; old endpoint stays for compat. Tools wrapped in a capability gate that consults the envelope. | P2.1, P1.8 |
| P2.5 | Escalation engine: detect escalation reasons during tool dispatch, persist transition to `audit_events`, replay the turn with the new envelope. Single transition per turn. | P2.4 |
| P2.6 | Frontend cutover: `FloatingAgentMiniChat` calls `/agent/respond` with `envelope: "pet_page"`; `AgentDesk` with `envelope: "desk_workflow"`. Visual: envelope badge in the pet UI ("Pet · diagnostic" when escalated). | P2.4 |
| P2.7 | Cost-preview escape hatch: implicit actions with `estimate_*_seconds() > 30` force a one-shot confirmation. | P1.4, P1.5, P2.5 |

**Gate:** all four user-original fixture prompts pass:
- Positions page: `How many positions do we have?` → counts from `loaded_context.completeness == "complete"`, no tool call.
- Risk page: `rerun risk` → calls action `run_risk` with current ids, monitors task.
- Risk page follow-up: `what's the delta of position 21?` → stays in `pet_page` or escalates to `pet_diagnostic`. Then `why is it so big?` → escalates to `pet_diagnostic`.
- Try-Solve page: `price a Snowball product with 000852.SH, 3Y, KO 103%, KI 75%` → clarifies missing terms then creates request-queue item.

### Phase 3 — Skill relocation + rewrite

| PR | Scope | Depends on |
|---|---|---|
| P3.1 | Create `app/skills/` structure. `git mv` existing skills to a `legacy/` subtree as a holding area. Migration map (above) is the canonical destination list. | — |
| P3.2 | Lint added in warn-only mode: description-length, missing Example, archaeology markers, frontmatter validity. | P3.1 |
| P3.3 | Flip lint to fail-CI for description-length, missing Example, archaeology, frontmatter (body-length stays warn-only). | P3.2 |
| P3.4 | Migrate `meta/` policies (12 files from policy migration map). Adopt new schema. | P3.1 |
| P3.5 | Migrate `references/` files (products, pricing, market-data, portfolios, rfq). | P3.1 |
| P3.6 | Migrate `workflows/risk/`, `workflows/positions/`, `workflows/try-solve/` — the four fixture-prompt domains first. | P3.4, P3.5, P2.5 |
| P3.7 | Migrate remaining workflows (`pricing`, `market-data`, `portfolios`, `rfq`, `reporting`, `snowballs`). | P3.6 |
| P3.8 | Convert routing skills to router tests; delete the routing/ tree. Optional `workflows/playbooks/` for compound flows. | P3.7 |
| P3.9 | Delete `legacy/` subtree. Flip body-length lint to fail-CI. | P3.4–P3.8 |

**Gate:** lint passes. `tests/test_skills_catalog*.py` updated and pass. A new `tests/test_skill_rewrite_regression.py` runs a frozen 8-prompt fixture set against the runtime and asserts: (a) the same envelope is selected, (b) the same tool sequence is invoked (tool name + args), (c) final reply contains the same key facts (numeric counts, IDs). Wording is allowed to change; routing and facts are not.

### Critical risks and mitigations

- **P1.8 is the load-bearing flip.** Mitigation: ship as an automated codemod that updates all imports atomically, with the full agent test suite as the merge gate.
- **P2.5 changes runtime behavior.** Mitigation: the four fixture prompts are locked before P2.4 starts; P2.5 cannot merge until all four pass.
- **P3.6 reorganizes the live skill catalog.** Mitigation: legacy/ subtree stays loadable through P3.8 so any forgotten reference path works as a fallback.

## Testing strategy

### Router and envelope tests

- `FloatingAgentMiniChat` starts in `pet_page`.
- `AgentDesk` starts in `desk_workflow`.
- Diagnostic follow-ups widen to `pet_diagnostic`.
- Cross-page tasks widen to `desk_workflow`.
- Long-running tasks widen to `desk_async`.
- Tool requests denied by an envelope produce a deterministic escalation or refusal.

### Page context tests

- Positions: `completeness == "complete"` → counted directly; `paginated`/`partial` → uses `query_ref` or `portfolio_id`.
- Risk: `rerun risk` maps to action `run_risk`; ids come from page; returned task id is monitored.
- Try-Solve: required fields present; incomplete Snowball terms trigger clarification; complete terms create a request-queue item.

### Skill quality tests (CI lint)

```python
def test_all_skills_pass_lint():
    for skill_path in iter_skill_files():
        body, fm = parse(skill_path)
        assert len(fm["description"]) <= 200, skill_path
        assert not fm["description"].lower().startswith(("this skill", "skill for", "documentation")), skill_path
        assert count_tokens(body) <= 500, f"{skill_path}: body has {count_tokens(body)} tokens"
        assert "## Example" in body or "## Examples" in body, skill_path
        for marker in GIT_ARCHAEOLOGY_REGEXES:
            assert not marker.search(body), f"{skill_path}: contains '{marker.pattern}'"
        validate_frontmatter_schema(fm, skill_path)
```

Hard-fail in CI. Runs in <2s across the catalog.

### Thread regression fixtures

Replay the four observed failures from real threads (#18, #19):

1. Risk page: `rerun risk` → envelope `pet_page`, action `run_risk`, task monitored.
2. Positions page: `How many positions do we have?` → envelope `pet_page`, counted from snapshot, no tool call.
3. Risk-or-Positions follow-up: `what's the delta of position 21?` (in `pet_page`) then `why is it so big?` (escalates to `pet_diagnostic`).
4. Try-Solve page: `price a Snowball product with 000852.SH, 3Y, KO 103%, KI 75%` → clarify missing terms, then action `create_request_queue_item`.

Each fixture asserts selected envelope, selected workflow skill, granted tool groups, required-context usage, and final behavior.

## Estimated PR count and timeline

- Phase 1: 8 PRs
- Phase 2: 7 PRs
- Phase 3: 9 PRs
- **Total: 24 PRs.** Serial: ~10–14 weeks. With Phase 1 / Phase 2 interleaving on independent dependencies: ~7–9 weeks.

## Open decisions

Inherited from the architectural anchor; not blocking this spec:

- Exact UI affordance for YOLO/direct-action mode in the pet.
- Whether envelope transitions should be visible in a developer/debug panel.
- Timeout policy for pet task monitoring.
- How much task progress the pet should stream before handing off to the Tasks page.
- Whether existing skill paths should be moved physically in one migration or aliased first (Phase 3 currently does physical move via `legacy/` holding area).

## Success criteria

- Pet answers simple page questions without broad discovery.
- Pet runs page-native actions through backend APIs when allowed.
- Pet automatically widens to diagnostic behavior for follow-up reasoning questions.
- Desk remains available for heavy workflow and async work.
- Skills are task-specific, short, testable, and free of implementation-history contamination.
- Tool access is enforced by envelope, not only by prompt instructions.
- The four original failed thread scenarios pass as regression tests.

# Hedge Booking Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the desk agent book hedges through `book_hedge` (the Hedging page's port) with a HITL confirmation card, via one graph-shaped `hedge-portfolio` workflow with solver and manual entries.

**Architecture:** Five thin layers, each closing one gap found in thread 43: the HITL interrupt list gains `book_hedge`/`set_hedge_bands`; the `hedge-portfolio` skill is rewritten as the confirmed two-entry/one-review-loop graph; the trader persona gains the hedging catalog; the orchestrator prompt gains hedge routing + a quote-first carve-out; the tool schema documents leg shape and the `manual` strategy tag, validated in the service.

**Tech Stack:** Python (FastAPI backend), LangChain/LangGraph deep agent, pytest, markdown prompt/skill files with a 500-token lint budget.

**Spec:** `docs/superpowers/specs/2026-06-04-hedge-booking-graph-design.md`

---

## Execution context

- **Worktree:** `/Users/fuxinyao/open-otc-trading/.claude/worktrees/hedge-booking-graph`, branch `worktree-hedge-booking-graph`. Run everything from the worktree root.
- **GOTCHA:** the shared venv resolves `app` imports to the MAIN checkout via a `.pth` file. Every python/pytest command MUST be prefixed `PYTHONPATH=$PWD/backend` (pytest from the worktree root also works because of this prefix — never bare `python3 -c "import app…"`).
- **Skill bodies are token-budgeted:** `skill_lint.BODY_MAX_TOKENS = 500`, counted by `app.services.deep_agent.skill_lint.count_body_tokens` on the body (frontmatter excluded). The skill contents below are pre-counted (hedge-portfolio 499, book-position 498 after the bullet trim, snowball-risk-explain ~438). If you deviate from the given text, re-count before committing.
- Token count one-liner (used by several tasks):

```bash
PYTHONPATH=$PWD/backend python3 -c "
from pathlib import Path
from app.services.deep_agent.skill_lint import parse_skill_file, count_body_tokens
p = parse_skill_file(Path('SKILL_PATH_HERE'))
t = count_body_tokens(p.body)
print(t); assert t <= 500, f'{t} > 500'"
```

---

### Task 1: HITL gate — `book_hedge` (irreversible) + `set_hedge_bands` (write)

**Files:**
- Modify: `tests/test_hitl.py`
- Modify: `backend/app/services/deep_agent/hitl.py`

- [ ] **Step 1: Update the exact-set test and YOLO test to expect the new tools**

In `tests/test_hitl.py`, inside `test_interrupt_tool_names_covers_all_state_mutating_tools`, add two names to the asserted set (after `"book_position",`):

```python
        "book_position",
        "book_hedge",
        "set_hedge_bands",
```

In `test_yolo_mode_uses_langchain_auto_approval_for_write_tools`, assert the write-level tool drops out in YOLO — add after `assert "run_python" not in config`:

```python
    assert "set_hedge_bands" not in config
```

and add `"book_hedge",` to the gated-tools tuple in the same test:

```python
    for tool_name in (
        "approve_rfq",
        "release_rfq",
        "book_rfq_to_position",
        "book_hedge",
        "cancel_lifecycle_event",
        "delete_portfolio",
        "remove_positions_from_portfolio",
    ):
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
PYTHONPATH=$PWD/backend python3 -m pytest tests/test_hitl.py -q
```

Expected: 2 FAILED (`test_interrupt_tool_names_covers_all_state_mutating_tools` — set mismatch; `test_yolo_mode_uses_langchain_auto_approval_for_write_tools` — KeyError `book_hedge`).

- [ ] **Step 3: Add the tools to the three HITL mappings**

In `backend/app/services/deep_agent/hitl.py`:

In `INTERRUPT_TOOL_NAMES`, after the line `"book_position",` add:

```python
    "book_hedge",
```

and after the line `"set_portfolio_rule",` add:

```python
    "set_hedge_bands",
```

In `_RISK_LEVEL_BY_TOOL`, after `"book_position": "irreversible",` add:

```python
    "book_hedge": "irreversible",
```

and after `"set_portfolio_rule": "write",` add:

```python
    "set_hedge_bands": "write",
```

In `_LABEL_BY_TOOL`, after `"book_position": "Book position",` add:

```python
    "book_hedge": "Book hedge",
```

and after `"set_portfolio_rule": "Replace portfolio filter rule",` add:

```python
    "set_hedge_bands": "Set hedge bands",
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
PYTHONPATH=$PWD/backend python3 -m pytest tests/test_hitl.py -q
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_hitl.py backend/app/services/deep_agent/hitl.py
git commit -m "fix(agent-hitl): gate book_hedge (irreversible) and set_hedge_bands (write)

book_hedge/set_hedge_bands were commented 'persisted / HITL-gated' in
tools/__init__.py but missing from INTERRUPT_TOOL_NAMES — book_hedge would
book without a confirmation card. Parity with book_position: irreversible,
stays gated in YOLO; set_hedge_bands auto-approves in YOLO like
set_portfolio_rule.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Service validation — `strategy` ∈ solver names ∪ {"manual"}

**Files:**
- Modify: `tests/test_hedging_book.py`
- Modify: `backend/app/services/domains/hedging_strategy.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_hedging_book.py`, add `import pytest` at the top (current imports are only `app.models` and `hedging_strategy`):

```python
# tests/test_hedging_book.py
import pytest

from app.models import Portfolio, Position
from app.services.domains import hedging_strategy as hs
```

Append two tests at the end of the file:

```python
def test_book_hedge_accepts_manual_strategy_tag(session):
    pf = Portfolio(name="book_manual", base_currency="CNY")
    session.add(pf); session.flush()
    legs = [{"contract_code": "IC2609", "exchange": "CFFEX", "family": "index_future",
             "instrument_type": "future", "multiplier": 200.0, "quantity": -2}]
    out = hs.book_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                        risk_run_id=20, strategy="manual", legs=legs, spot=8362.16)
    session.flush()
    pos = session.get(Position, out["position_ids"][0])
    # Desk-sized hedge: the manual tag is recorded verbatim in the hedge payload.
    assert pos.source_payload["hedge"]["strategy"] == "manual"
    assert pos.source_payload["hedge"]["is_hedge"] is True
    assert pos.source_trade_id == "HEDGE:20:1"
    assert pos.quantity == -2


def test_book_hedge_rejects_unknown_strategy(session):
    pf = Portfolio(name="book_bad_strategy", base_currency="CNY")
    session.add(pf); session.flush()
    legs = [{"contract_code": "IC2406", "exchange": "CFFEX", "family": "index_future",
             "instrument_type": "future", "multiplier": 200.0, "quantity": -1}]
    with pytest.raises(ValueError, match="Unknown hedge strategy"):
        hs.book_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                      risk_run_id=1, strategy="detla_neutral", legs=legs, spot=5600.0)
```

- [ ] **Step 2: Run them to verify the failure mode**

```bash
PYTHONPATH=$PWD/backend python3 -m pytest tests/test_hedging_book.py -q
```

Expected: `test_book_hedge_accepts_manual_strategy_tag` PASSES already (strategy is currently unvalidated — that's fine, it pins the tag); `test_book_hedge_rejects_unknown_strategy` FAILS (no ValueError raised).

- [ ] **Step 3: Add the validation to `book_hedge`**

In `backend/app/services/domains/hedging_strategy.py`, change the registry import (line 13) to also bring in `STRATEGIES`:

```python
from ..hedging_strategy_registry import STRATEGIES, tiers_for
```

Below the `_ENGINE` dict (after line 185), add:

```python
# Sizing-provenance tag for desk-stated legs (no solver involved).
_MANUAL_STRATEGY = "manual"
```

At the top of `book_hedge` (first statement of the function body, before the docstring's following code — i.e. right after the docstring), add:

```python
    allowed = set(STRATEGIES) | {_MANUAL_STRATEGY}
    if strategy not in allowed:
        raise ValueError(
            f"Unknown hedge strategy {strategy!r}; expected one of {sorted(allowed)}."
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
PYTHONPATH=$PWD/backend python3 -m pytest tests/test_hedging_book.py tests/test_hedging_api.py tests/test_hedging_domain.py -q
```

Expected: all PASS (the API path sends only solver names; existing booking tests use `delta_neutral`).

- [ ] **Step 5: Commit**

```bash
git add tests/test_hedging_book.py backend/app/services/domains/hedging_strategy.py
git commit -m "feat(hedging): validate book_hedge strategy; allow manual tag for desk-sized legs

strategy records who sized the hedge: a solver strategy name (MILP) or
'manual' (desk-stated legs). Unknown values now raise ValueError instead of
landing as junk provenance in source_payload.hedge.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Tool schema — document leg shape, `manual` strategy, and field provenance

**Files:**
- Modify: `backend/app/tools/hedging.py`

- [ ] **Step 1: Replace `BookHedgeInput` with the described schema**

In `backend/app/tools/hedging.py`, replace:

```python
class BookHedgeInput(BaseModel):
    portfolio_id: int
    underlying: str
    risk_run_id: int
    strategy: str
    spot: float
    legs: list[dict[str, Any]]
```

with:

```python
class BookHedgeInput(BaseModel):
    portfolio_id: int
    underlying: str = Field(
        description="Hedged exposure's underlying symbol (e.g. '000905.SH'), "
        "NOT the hedge instrument's contract code."
    )
    risk_run_id: int = Field(
        description="Source risk run id, from get_hedgeable_underlyings or the proposal."
    )
    strategy: str = Field(
        description="delta_neutral|delta_neutral_enhanced|delta_gamma_neutral|full_neutral "
        "for solver-sized legs, or 'manual' for desk-stated legs."
    )
    spot: float = Field(
        description="Risk-run spot for the hedged underlying, from "
        "get_hedgeable_underlyings or the proposal."
    )
    legs: list[dict[str, Any]] = Field(
        description="Each leg: {instrument_type: 'future'|'spot'|'option', quantity: "
        "signed integer lots, contract_code, exchange, multiplier, expiry (ISO date); "
        "options add strike and option_type}. Zero-quantity legs are skipped."
    )
```

- [ ] **Step 2: Update the `book_hedge` tool docstring**

Replace the docstring of `book_hedge_tool`:

```python
    """Atomically book the sized hedge legs into the portfolio, tagged + linked to the run."""
```

with:

```python
    """Atomically book hedge legs into the portfolio, hedge-tagged (is_hedge,
    risk_run_id, strategy, leg_role) and visible on the Hedging page. HITL —
    requires confirmation. Never book hedge legs via book_position."""
```

- [ ] **Step 3: Run the tool/capability tests**

```bash
PYTHONPATH=$PWD/backend python3 -m pytest tests/test_capability_assignments.py tests/test_hedging_solve_orchestration.py -q
```

Expected: all PASS (decorators and tool names unchanged).

- [ ] **Step 4: Commit**

```bash
git add backend/app/tools/hedging.py
git commit -m "feat(agent-tools): document book_hedge leg shape, manual strategy, and field provenance

The skill layer references the schema for the leg dict shape instead of
spending its 500-token budget enumerating fields.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Rewrite `hedge-portfolio/SKILL.md` as the confirmed graph

**Files:**
- Modify: `backend/app/skills/workflows/hedging/hedge-portfolio/SKILL.md` (full overwrite)

- [ ] **Step 1: Overwrite the file with exactly this content** (body pre-counted at **499/500** — do not paraphrase):

```markdown
---
name: hedge-portfolio
description: Size and book a per-underlying greek hedge — solver-sized (four
  hedging strategies) or desk-stated legs booked with the manual tag. Use when
  a desk wants to neutralize delta/gamma/vega within bands, book explicit hedge
  legs, or act on an in-thread hedging recommendation.
domain: hedging
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio_id
optional_context:
  - underlying
  - strategy
  - legs
  - bands
write_actions: true
confirmation_required: true
success_criteria:
  - sized legs with residual greeks and feasibility are returned before booking
  - infeasible hard bands are reported with the binding greek, never booked silently
  - booked legs are tagged with risk_run_id and sizing strategy (solver name or manual)
  - hedge legs are never booked through book_position
---

## When to use

- Solve entry: desk wants per-underlying greeks neutralized.
- Manual entry: desk states explicit hedge legs/quantities or acts on an
  in-thread recommendation.

## Procedure

1. Guard (both entries): `get_hedgeable_underlyings(portfolio_id)`. On
   `no_risk_run`, stop — ask to run risk first. Warn if stale. Keep
   `risk_run_id` and `spot` — `book_hedge` needs them.
2. Manual entry — user stated instrument(s) + signed quantities: go to step 6
   with `strategy="manual"` and the stated legs. `underlying` is the hedged
   exposure's symbol, not the hedge instrument's code.
3. Solve entry: pick `underlying` + `strategy` (confirm if unspecified); call
   `propose_hedge(portfolio_id, underlying, strategy)`.
4. Present legs, bands, quantities, residuals. If `infeasible`, report binding
   greek(s) + shortfall; suggest an option leg or wider band. Do not book.
5. Review loop: on comments, re-solve with overridden `legs`/`bands`/`strategy`
   and re-present. If the user dictates quantities, switch to step 6 with
   `strategy="manual"`.
6. Book: `book_hedge(portfolio_id, underlying, risk_run_id, strategy, spot,
   legs)`. The HITL confirmation card is the booking gate.
7. Report booked position ids — hedge-tagged, on the Hedging page.

## Stop conditions

Never book an infeasible hard-band solution, guess greek targets without a
completed risk run, or book hedge legs via `book_position` (loses the hedge
tag).

## Output shape

Feasibility (solve) or stated legs (manual) first; then strategy or `manual`,
per-leg quantities, residual/binding greeks, booked ids.

## References

- `/skills/references/hedging/strategy.md`

## Example

User: Book the short 2 IC futures as the CSI500 hedge.
Assistant: get_hedgeable_underlyings(4) → fresh → book_hedge(4, "000905.SH",
run_id, "manual", spot, [future IC qty −2]) → HITL card → ids.
```

- [ ] **Step 2: Verify the token budget and lint**

```bash
PYTHONPATH=$PWD/backend python3 -c "
from pathlib import Path
from app.services.deep_agent.skill_lint import parse_skill_file, count_body_tokens
p = parse_skill_file(Path('backend/app/skills/workflows/hedging/hedge-portfolio/SKILL.md'))
t = count_body_tokens(p.body)
print(t); assert t <= 500, f'{t} > 500'"
PYTHONPATH=$PWD/backend python3 -m pytest tests/test_skill_lint_ci.py::test_current_catalog_has_no_ci_blocking_skill_lint_errors tests/test_skills_catalog_v2.py tests/test_skills_catalog.py -q
```

Expected: prints `499`; all tests PASS (no skills added/removed, name unchanged).

- [ ] **Step 3: Commit**

```bash
git add backend/app/skills/workflows/hedging/hedge-portfolio/SKILL.md
git commit -m "feat(skills): graph-shaped hedge-portfolio workflow — two entries, one review loop

Solve entry (MILP strategies) and manual entry (desk-stated legs,
strategy='manual') share the risk-run guard and exit through the book_hedge
HITL card. Review comments re-solve; dictated quantities switch to manual.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Manual-tag convention in the hedging reference doc

**Files:**
- Modify: `backend/app/skills/references/hedging/strategy.md`

- [ ] **Step 1: Append the manual-tag section** (reference docs have no token cap). Add at the end of the file:

```markdown

## Manual tag

- `strategy="manual"` marks desk-sized hedges: the user stated the legs and
  quantities (up front, or by dictating quantities during proposal review).
- Solver strategy names mark MILP-sized hedges. The tag records who sized the
  hedge; both book through `book_hedge` and carry the source risk_run_id.
```

- [ ] **Step 2: Run the reference-doc test**

```bash
PYTHONPATH=$PWD/backend python3 -m pytest tests/test_reference_docs.py -q
```

Expected: all PASS (`reference_type: hedging` already valid; frontmatter unchanged).

- [ ] **Step 3: Commit**

```bash
git add backend/app/skills/references/hedging/strategy.md
git commit -m "docs(skills): manual-tag sizing-provenance convention in hedging reference

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Trader persona gains the hedging workflow catalog

**Files:**
- Modify: `tests/test_skills_catalog_v2.py`
- Modify: `backend/app/services/deep_agent/personas.py`

- [ ] **Step 1: Update the catalog pins to expect hedging in trader's sources**

In `tests/test_skills_catalog_v2.py::test_persona_sources_are_workflow_only`, change the trader assertion to:

```python
    assert trader_sources == [
        "/skills/workflows/positions/",
        "/skills/workflows/products/",
        "/skills/workflows/try-solve/",
        "/skills/workflows/pricing/",
        "/skills/workflows/hedging/",
        "/skills/workflows/market-data/",
        "/skills/workflows/portfolios/",
        "/skills/workflows/rfq/",
        "/skills/workflows/snowballs/",
    ]
```

In `test_trader_total_workflow_catalog`, change the count and add the membership pin:

```python
    assert len(catalog) == 21, f"Expected 21 entries, got {len(catalog)}: {catalog}"
    assert {
        "position-snapshot",
        "solve-imported-row",
        "price-portfolio",
        "fetch-market-data",
        "portfolio-membership",
        "quote-rfq",
        "snowball-pricing",
        "hedge-portfolio",
    } <= catalog
```

(keep the existing `not in` assertions unchanged.)

- [ ] **Step 2: Run to verify they fail**

```bash
PYTHONPATH=$PWD/backend python3 -m pytest tests/test_skills_catalog_v2.py -q
```

Expected: 2 FAILED (trader source-list mismatch; catalog count 20 != 21).

- [ ] **Step 3: Add the hedging source to `trader_spec`**

In `backend/app/services/deep_agent/personas.py`, in `trader_spec`'s `skills` list, insert after `"/skills/workflows/pricing/",`:

```python
            "/skills/workflows/hedging/",
```

- [ ] **Step 4: Run the catalog test batteries**

```bash
PYTHONPATH=$PWD/backend python3 -m pytest tests/test_skills_catalog_v2.py tests/test_skills_catalog.py tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py tests/test_personas.py -q
```

Expected: all PASS (phase3 persona-source tests assert subset membership, not equality).

- [ ] **Step 5: Commit**

```bash
git add tests/test_skills_catalog_v2.py backend/app/services/deep_agent/personas.py
git commit -m "feat(agent): trader persona gains the hedging workflow catalog

Booking-shaped hedge intents route to trader; without the catalog the user's
'it's a hedging instrument book request' clarification was un-correctable
in-conversation (thread 43).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Hedge redirects in `book-position` and `snowball-risk-explain`

**Files:**
- Modify: `backend/app/skills/workflows/positions/book-position/SKILL.md`
- Modify: `backend/app/skills/workflows/snowballs/snowball-risk-explain/SKILL.md`

- [ ] **Step 1: Rewrite `book-position` body** (frontmatter unchanged; body pre-counted at **498/500** with the trims below). Replace everything after the closing `---` of the frontmatter with:

```markdown

## When to use

- Trader books a structured product directly into a portfolio without
  an RFQ, from validated terms or an accepted deal.

## Required inputs

Use `portfolio_id` and a `product` object (`product_family` required;
`asset_class` defaults to equity, `currency` to USD; plus `quantark_class`,
`underlying`, `terms`, and optional `components`), `quantity`, and optional
`entry_price`, `status`, `trade_effective_date`, and `engine_name`. Call
`get_rfq_catalog` for valid product families and engines. Read
`/skills/references/pricing/engines.md` when engine choice is unclear.

## Procedure

1. If the product terms are natural-language, first run `build-product` to get
   validated product terms and the recommended `engine_name`. Autocallables
   must use their quad engine (SnowballOption → `SnowballQuadEngine`,
   KnockOutResetSnowballOption → `KOResetSnowballQuadEngine`, PhoenixOption →
   `PhoenixQuadEngine`); never book an autocallable with `BlackScholesEngine`.
2. Validate family support and required terms; if incomplete, run
   `build-product` (`propose_term_form` loop) first.
3. Compose a confirmation summary with portfolio, product, quantity, entry
   price, and engine.
4. After confirmation, call `book_position(portfolio_id=<id>, product=<spec>,
   quantity=<qty>, entry_price=<optional>, engine_name=<recommended>)`.
5. Return the booked position id, product id, and product summary.

## Stop conditions

Do not book an unsupported product family or guess missing economic terms — ask
instead. Never book hedging instruments against book exposure here — use
`hedge-portfolio` (`book_hedge`) or the hedge tag is lost.

## Output shape

Booked or blocked first; then position id, product id, portfolio, family,
quantity, missing terms.

## References

- `/skills/references/pricing/engines.md`

## Example

User: Book 100 lots of a two-year CSI 500 Snowball, KI 75% KO 103%, into portfolio 6.
Assistant: Validate the autocallable terms, summarize for confirmation, then call
`book_position` and report the new position id and product id.
```

- [ ] **Step 2: Add the handoff to `snowball-risk-explain`**

In `backend/app/skills/workflows/snowballs/snowball-risk-explain/SKILL.md`, append a step 6 after step 5 of the Procedure:

```markdown
6. If the desk wants to act on a hedging suggestion (book the recommended
   instruments), hand off to `hedge-portfolio` — `book_hedge` (HITL) books
   hedge-tagged legs; never `book-position`.
```

and change the Output shape line from:

```markdown
Return risk verdict, KI/KO proximity, latest risk freshness, hedge caveats, and recommended next workflow.
```

to:

```markdown
Return risk verdict, KI/KO proximity, latest risk freshness, hedge caveats, and
recommended next workflow (`hedge-portfolio` for actionable hedges).
```

- [ ] **Step 3: Verify token budgets and lint**

```bash
for f in backend/app/skills/workflows/positions/book-position/SKILL.md backend/app/skills/workflows/snowballs/snowball-risk-explain/SKILL.md; do
PYTHONPATH=$PWD/backend python3 -c "
from pathlib import Path
from app.services.deep_agent.skill_lint import parse_skill_file, count_body_tokens
p = parse_skill_file(Path('$f'))
t = count_body_tokens(p.body)
print('$f', t); assert t <= 500, f'{t} > 500'"
done
PYTHONPATH=$PWD/backend python3 -m pytest tests/test_skill_lint_ci.py tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py -q
```

Expected: book-position ≈ 498, snowball-risk-explain ≈ 438, both ≤ 500; tests PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/skills/workflows/positions/book-position/SKILL.md backend/app/skills/workflows/snowballs/snowball-risk-explain/SKILL.md
git commit -m "feat(skills): hedge redirects — book-position stop condition + snowball-risk-explain handoff

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Orchestrator routing — hedge execution, quote-first carve-out, matrix rows, persisted list

**Files:**
- Modify: `backend/app/services/deep_agent/prompts/orchestrator.md` (4 edits)

- [ ] **Step 1: Add the hedge-execution routing line.** After the line

```markdown
- Risk, VaR, stress, exposure, hedge feasibility → `risk_manager`.
```

insert:

```markdown
- Hedge execution → `hedge-portfolio`: sizing a greek hedge ("hedge this
  portfolio", "neutralize delta/gamma") → `risk_manager`; booking stated hedge
  legs or acting on an in-thread hedging recommendation ("book the suggested IC
  futures", "it's a hedging instrument book request") → `trader`. Hedge
  bookings are NEVER quote-first and never `book-position` — only `book_hedge`
  carries the hedge tag.
```

- [ ] **Step 2: Add the quote-first carve-out.** After the paragraph

```markdown
Skip the question only if the user already stated the choice ("just book it",
"quote it first") in the same request.
```

insert:

```markdown
This rule does NOT apply to hedge bookings: if the user calls the booking a
hedge, or it acts on a hedging recommendation from this thread, do not ask
quote-vs-book and do not use `book-position` — route to `hedge-portfolio`
(`book_hedge`) per the hedge-execution rule above.
```

- [ ] **Step 3: Add the two matrix rows.** After the table row

```markdown
| Book a product directly into a portfolio from terms    | trader        | book-position                    |
```

insert:

```markdown
| Solve/size a portfolio greek hedge (strategies, bands) | risk_manager  | hedge-portfolio                  |
| Book stated hedge legs / act on a hedge recommendation | trader        | hedge-portfolio                  |
```

- [ ] **Step 4: Extend the persisted-tools list (Batch-size-1 rule).** In the `## Batch-size-1 rule for HITL` paragraph, change

```markdown
`book_rfq_to_position`, `book_position`, `import_otc_positions`,
```

to

```markdown
`book_rfq_to_position`, `book_position`, `book_hedge`, `set_hedge_bands`, `import_otc_positions`,
```

(the list is one long line in the file — apply the substring replacement within it.)

- [ ] **Step 5: Run prompt-coupled tests**

```bash
PYTHONPATH=$PWD/backend python3 -m pytest tests/test_routing_contracts_phase3.py tests/test_personas.py tests/test_reply_options_tool.py -q
```

Expected: all PASS (no exact-text pins on the edited sections).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/deep_agent/prompts/orchestrator.md
git commit -m "feat(agent-routing): hedge execution routing, quote-first carve-out, matrix rows, persisted list

Thread 43: 'Book the Short ~2 IC futures' matched the direct-booking intent
and quote-first rule; hedge execution had no routing target, so the agent
landed on book-position. Hedge bookings now route to hedge-portfolio and are
never quote-first.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Persona prompt guidance — trader + risk_manager

**Files:**
- Modify: `backend/app/services/deep_agent/prompts/trader.md`
- Modify: `backend/app/services/deep_agent/prompts/risk_manager.md`

- [ ] **Step 1: Add the hedge-booking rule to trader.md.** In the `## Routing from skills` section, after the paragraph ending

```markdown
For booking or RFQ construction, read
`/skills/workflows/products/build-product/SKILL.md` and
`/skills/references/products/build-contract.md` before calling `build_product`.
```

append:

```markdown

For hedge bookings (hedging instruments against book exposure, or acting on a
hedging recommendation), read
`/skills/workflows/hedging/hedge-portfolio/SKILL.md` and book via `book_hedge`
(HITL — requires confirmation), never `book_position` — only `book_hedge`
carries the hedge tag onto the Hedging page.
```

- [ ] **Step 2: Extend the recommend_hedge roster line in risk_manager.md.** Replace

```markdown
- `recommend_hedge` — hedge suggestion from risk metrics.
```

with

```markdown
- `recommend_hedge` — hedge suggestion from risk metrics. To act on a
  suggestion (book the hedge), follow `hedge-portfolio` and book via
  `book_hedge` (HITL — requires confirmation); never `book_position`.
```

- [ ] **Step 3: Run persona tests**

```bash
PYTHONPATH=$PWD/backend python3 -m pytest tests/test_personas.py -q
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/deep_agent/prompts/trader.md backend/app/services/deep_agent/prompts/risk_manager.md
git commit -m "feat(agent-prompts): hedge-booking guidance in trader and risk_manager personas

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: Full verification sweep

**Files:** none (verification only)

- [ ] **Step 1: Targeted battery**

```bash
PYTHONPATH=$PWD/backend python3 -m pytest tests/test_hitl.py tests/test_hedging_book.py tests/test_hedging_api.py tests/test_hedging_domain.py tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py tests/test_personas.py tests/test_skill_lint_ci.py tests/test_reference_docs.py tests/test_capability_assignments.py tests/test_routing_contracts_phase3.py -q
```

Expected: all PASS.

- [ ] **Step 2: Full suite**

```bash
PYTHONPATH=$PWD/backend python3 -m pytest tests/ -q
```

Expected: all PASS (suite was ~1229 green at last merge; no frontend changes, so vitest is not needed).

- [ ] **Step 3: Manual replay check (optional but recommended before merge)**

Against a dev stack, replay thread 43's script: "hedging suggestion for this
portfolio" → "Book the Short 2 IC futures contracts". Expected: a **Book
hedge** Pending-Confirmation card (not Book position); approving books legs
with `source_payload.hedge.strategy == "manual"` and the latest
`risk_run_id`, visible on the Hedging page.

---

## Spec coverage map

| Spec section | Task |
|---|---|
| §1 hedge-portfolio rewrite (499-token body) | Task 4 |
| §2 reference manual-tag section | Task 5 |
| §3 HITL gate | Task 1 |
| §4 orchestrator routing (4 edits) | Task 8 |
| §5 persona catalogs + prompts | Tasks 6, 9 |
| §6 adjacent skills | Task 7 |
| §7 tool schema + service validation | Tasks 2, 3 |
| Test impact table | Tasks 1, 2, 6, 7 + Task 10 sweep |

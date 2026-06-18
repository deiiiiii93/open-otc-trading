# Agent Skills Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce an in-repo agent skills layer using deepagents' `SkillsMiddleware`, plus one cross-persona vertical slice (`snowball-position-diagnostics`) to validate the pattern.

**Architecture:** Two storage mechanisms with different lifecycles. **Policy fragments** are composable markdown files concatenated into each persona's `system_prompt` at agent build time. **Procedure + product card SKILL.md files** flow through `SkillsMiddleware` via the per-`SubAgent` `skills=[...]` kwarg, with progressive disclosure (catalog injected; bodies fetched via `read_file`). A `CompositeBackend` routes `/skills/` to a `FilesystemBackend` rooted at `backend/app/services/deep_agent/skills/`, leaving the existing `StateBackend` as the default for everything else.

**Tech Stack:** Python 3.11+, deepagents 0.5.3, LangChain, pytest. No frontend changes.

**Reference spec:** `docs/superpowers/specs/2026-05-14-agent-skills-layer-design.md`

**File responsibility map:**

| File | Responsibility | Action |
|---|---|---|
| `backend/app/services/deep_agent/skills/policy/*.md` | Always-on workflow rules concatenated into persona system prompts | Create (5 files) |
| `backend/app/services/deep_agent/skills/procedures/{trader,risk_manager,high_board}/` | Per-persona on-demand SKILL.md dirs surfaced via SkillsMiddleware | Create dirs + 2 SKILL.md |
| `backend/app/services/deep_agent/skills/products/snowball-cn/SKILL.md` | Shared product reference card | Create |
| `backend/app/services/deep_agent/skills/README.md` | Add-a-skill recipe for future contributors | Create |
| `backend/app/services/deep_agent/skills_loader.py` | Pure-Python loader: load policy fragments, compose persona prompts | Create |
| `backend/app/services/deep_agent/personas.py` | Per-persona SubAgent specs; now compose prompts + pass `skills=[...]` | Modify |
| `backend/app/services/deep_agent/orchestrator.py` | Build orchestrator; now wires `CompositeBackend` and `/skills` permission | Modify |
| `backend/app/services/deep_agent/prompts/trader.md` | Trader identity + output style; policy sections extracted to fragments | Trim |
| `backend/app/services/deep_agent/prompts/risk_manager.md` | Risk identity + output style; policy sections extracted | Trim |
| `backend/app/services/deep_agent/prompts/high_board.md` | Board identity + output style; policy sections extracted | Trim |
| `backend/app/services/deep_agent/prompts/orchestrator.md` | Orchestrator prompt; adds "Naming skills in delegations" + Routing matrix row | Modify |
| `backend/tests/services/deep_agent/test_skills_loader.py` | Unit tests for skills_loader functions | Create |
| `backend/tests/services/deep_agent/test_skills_catalog.py` | Integration tests: per-persona catalog assembly + read_file no-HITL guard | Create |

---

## Task 1: Scaffold the skills directory tree

**Files:**
- Create: `backend/app/services/deep_agent/skills/README.md`
- Create: `backend/app/services/deep_agent/skills/procedures/high_board/.gitkeep`
- Create: `backend/app/services/deep_agent/skills/procedures/trader/.gitkeep`
- Create: `backend/app/services/deep_agent/skills/procedures/risk_manager/.gitkeep`
- Create: `backend/app/services/deep_agent/skills/products/.gitkeep`
- Create: `backend/app/services/deep_agent/skills/policy/.gitkeep`

- [ ] **Step 1: Create the directory tree**

```bash
cd /Users/fuxinyao/open-otc-trading
mkdir -p backend/app/services/deep_agent/skills/policy
mkdir -p backend/app/services/deep_agent/skills/procedures/trader
mkdir -p backend/app/services/deep_agent/skills/procedures/risk_manager
mkdir -p backend/app/services/deep_agent/skills/procedures/high_board
mkdir -p backend/app/services/deep_agent/skills/products
touch backend/app/services/deep_agent/skills/policy/.gitkeep
touch backend/app/services/deep_agent/skills/procedures/trader/.gitkeep
touch backend/app/services/deep_agent/skills/procedures/risk_manager/.gitkeep
touch backend/app/services/deep_agent/skills/procedures/high_board/.gitkeep
touch backend/app/services/deep_agent/skills/products/.gitkeep
```

- [ ] **Step 2: Write README**

`backend/app/services/deep_agent/skills/README.md`:

````markdown
# Agent Skills Layer

Reference: `docs/superpowers/specs/2026-05-14-agent-skills-layer-design.md`.

Three tiers:

- **`policy/`** — composable system-prompt fragments concatenated into each
  persona's `system_prompt` at agent build time (via `skills_loader.py`).
  Always in context. NOT surfaced through `SkillsMiddleware`.
- **`procedures/<persona>/<skill-name>/SKILL.md`** — persona-scoped workflows.
  Loaded by `SkillsMiddleware` (one source per persona). Progressive disclosure.
- **`products/<product-id>/SKILL.md`** — product reference cards shared by
  trader and risk_manager. Loaded by `SkillsMiddleware`.

## Add a procedure skill

1. `mkdir skills/procedures/<persona>/<skill-name>/`
2. Author `SKILL.md` with frontmatter (`name`, `description`, `allowed-tools`,
   `metadata`) + the five-section body schema:
   - `## When this applies`
   - `## Inputs to inspect first`
   - `## Step sequence`
   - `## What success looks like`
   - `## Tool preferences`
3. Add a row to the Routing matrix in `prompts/orchestrator.md`.
4. Add a Tier-B test row asserting catalog presence.

## Add a product card

1. `mkdir skills/products/<product-id>/`
2. Author `SKILL.md` with frontmatter + recommended sections (free-form):
   `## What it is`, `## Key invariants`, `## Pricing engine & market inputs`,
   `## Market quirks`, `## Common diagnostics signals`, `## See also`.
3. Reference it from related procedure skills' `metadata.related_products`.

## Add a policy fragment (rare)

1. Author `policy/<name>.md` with an opening `## ` header.
2. Add the fragment name to the relevant persona's allowlist in `personas.py`.
3. If the fragment applies to a subset of personas, document the rationale in
   the fragment body so future readers understand the asymmetry.

## Naming rules (enforced for SKILL.md by SkillsMiddleware)

- Lowercase alphanumeric + hyphens, 1–64 chars. No leading/trailing `-`, no `--`.
- The `name:` in frontmatter MUST match the parent directory name.
````

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/deep_agent/skills/
git commit -m "feat(agent-skills): scaffold skills directory tree and README"
```

---

## Task 2: skills_loader.py with full TDD cycle

**Files:**
- Create: `backend/app/services/deep_agent/skills_loader.py`
- Create: `backend/tests/services/deep_agent/__init__.py` (if missing)
- Create: `backend/tests/services/deep_agent/test_skills_loader.py`

- [ ] **Step 1: Verify tests dir exists**

```bash
cd /Users/fuxinyao/open-otc-trading
ls backend/tests/services/deep_agent/ 2>/dev/null || mkdir -p backend/tests/services/deep_agent && touch backend/tests/services/deep_agent/__init__.py
```

- [ ] **Step 2: Write the failing tests**

`backend/tests/services/deep_agent/test_skills_loader.py`:

```python
"""Unit tests for skills_loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.deep_agent.skills_loader import (
    POLICY_DIR,
    compose_persona_prompt,
    load_policy_fragments,
)


def test_policy_dir_resolves_inside_package() -> None:
    assert POLICY_DIR.is_dir(), f"{POLICY_DIR} must exist"
    assert POLICY_DIR.name == "policy"
    assert POLICY_DIR.parent.name == "skills"


def test_load_policy_fragments_single(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Use a tmp policy dir so the test is hermetic.
    policy_dir = tmp_path / "policy"
    policy_dir.mkdir()
    (policy_dir / "alpha.md").write_text("## Alpha\nbody A", encoding="utf-8")
    monkeypatch.setattr("app.services.deep_agent.skills_loader.POLICY_DIR", policy_dir)

    result = load_policy_fragments(["alpha"])
    assert result == "## Alpha\nbody A"


def test_load_policy_fragments_concatenates_with_blank_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy_dir = tmp_path / "policy"
    policy_dir.mkdir()
    (policy_dir / "alpha.md").write_text("## Alpha\nbody A", encoding="utf-8")
    (policy_dir / "beta.md").write_text("## Beta\nbody B", encoding="utf-8")
    monkeypatch.setattr("app.services.deep_agent.skills_loader.POLICY_DIR", policy_dir)

    result = load_policy_fragments(["alpha", "beta"])
    assert result == "## Alpha\nbody A\n\n## Beta\nbody B"


def test_load_policy_fragments_strips_trailing_whitespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy_dir = tmp_path / "policy"
    policy_dir.mkdir()
    (policy_dir / "alpha.md").write_text("## Alpha\nbody A\n\n\n", encoding="utf-8")
    monkeypatch.setattr("app.services.deep_agent.skills_loader.POLICY_DIR", policy_dir)

    result = load_policy_fragments(["alpha"])
    assert result == "## Alpha\nbody A"


def test_load_policy_fragments_missing_fragment_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy_dir = tmp_path / "policy"
    policy_dir.mkdir()
    monkeypatch.setattr("app.services.deep_agent.skills_loader.POLICY_DIR", policy_dir)

    with pytest.raises(FileNotFoundError) as exc:
        load_policy_fragments(["does-not-exist"])
    assert "does-not-exist.md" in str(exc.value)


def test_compose_persona_prompt_identity_plus_fragments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy_dir = tmp_path / "policy"
    policy_dir.mkdir()
    (policy_dir / "alpha.md").write_text("## Alpha\nbody A", encoding="utf-8")
    monkeypatch.setattr("app.services.deep_agent.skills_loader.POLICY_DIR", policy_dir)

    result = compose_persona_prompt(
        identity_prompt="You are foo.\n\n## Tools\n- bar\n",
        policy_fragment_names=["alpha"],
    )
    assert result == "You are foo.\n\n## Tools\n- bar\n\n## Alpha\nbody A"


def test_compose_persona_prompt_empty_fragment_list_returns_identity_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.services.deep_agent.skills_loader.POLICY_DIR", tmp_path / "policy")
    result = compose_persona_prompt(
        identity_prompt="You are foo.\n",
        policy_fragment_names=[],
    )
    # rstrip + "\n\n" + "" -> "You are foo.\n\n"
    assert result == "You are foo.\n\n"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest backend/tests/services/deep_agent/test_skills_loader.py -v
```

Expected: ImportError or ModuleNotFoundError on `skills_loader`.

- [ ] **Step 4: Implement skills_loader.py**

`backend/app/services/deep_agent/skills_loader.py`:

```python
"""Skill assembly helpers.

Two responsibilities:

1. Load policy fragments from disk and concatenate them. Used to build each
   persona's `system_prompt` at agent build time.
2. Compose a persona prompt from an identity body + selected policy fragments.

Procedure and product card skills do NOT flow through this module — they are
surfaced to personas via deepagents' `SkillsMiddleware` (configured by the
`skills=` kwarg on each `SubAgent` spec).
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

POLICY_DIR = Path(__file__).parent / "skills" / "policy"


def load_policy_fragments(names: Sequence[str]) -> str:
    """Read named policy fragments from `POLICY_DIR` and concatenate them.

    Args:
        names: Policy fragment basenames (without `.md`), in the desired order.

    Returns:
        Concatenated fragments separated by a blank line, each individually
        stripped of trailing whitespace. Empty string if `names` is empty.

    Raises:
        FileNotFoundError: A named fragment file does not exist. The error
            message includes the resolved path so the failure is debuggable
            at agent build time.
    """
    parts: list[str] = []
    for name in names:
        path = POLICY_DIR / f"{name}.md"
        if not path.is_file():
            raise FileNotFoundError(f"Policy fragment not found: {path}")
        parts.append(path.read_text(encoding="utf-8").rstrip())
    return "\n\n".join(parts)


def compose_persona_prompt(
    *,
    identity_prompt: str,
    policy_fragment_names: Sequence[str],
) -> str:
    """Compose a persona system prompt: identity body followed by policy fragments.

    Args:
        identity_prompt: The persona's identity + output style + routing-from-skills
            directive, loaded from `prompts/<persona>.md`.
        policy_fragment_names: Policy fragment basenames composed in order.

    Returns:
        `<identity_prompt.rstrip()>\\n\\n<concatenated fragments>`.
    """
    fragments = load_policy_fragments(policy_fragment_names)
    return identity_prompt.rstrip() + "\n\n" + fragments
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest backend/tests/services/deep_agent/test_skills_loader.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/deep_agent/skills_loader.py backend/tests/services/deep_agent/test_skills_loader.py backend/tests/services/deep_agent/__init__.py
git commit -m "feat(agent-skills): add skills_loader for policy fragment composition"
```

---

## Task 3: Author policy fragments (byte-identical extraction)

**Goal:** Move 5 sections out of the existing persona prompts into separate fragment files, byte-identical. This task creates files; Task 4 wires them in and trims the original prompts atomically. **Do not edit `prompts/*.md` in this task.**

**Files:**
- Create: `backend/app/services/deep_agent/skills/policy/read-before-compute.md`
- Create: `backend/app/services/deep_agent/skills/policy/cost-preview.md`
- Create: `backend/app/services/deep_agent/skills/policy/hitl-batch-size-1.md`
- Create: `backend/app/services/deep_agent/skills/policy/clarification-protocol.md`
- Create: `backend/app/services/deep_agent/skills/policy/run-python-rfsw.md`

- [ ] **Step 1: Read the current trader prompt and risk_manager prompt for source content**

```bash
cd /Users/fuxinyao/open-otc-trading
sed -n '20,33p;46,70p' backend/app/services/deep_agent/prompts/trader.md
sed -n '13,32p;58,60p' backend/app/services/deep_agent/prompts/risk_manager.md
sed -n '12,31p' backend/app/services/deep_agent/prompts/high_board.md
```

(This step is reconnaissance — confirm the section line ranges match the spec; if line numbers have drifted, use the section header text to locate each block.)

- [ ] **Step 2: Write `policy/read-before-compute.md`**

This fragment is shared by trader + risk_manager (NOT high_board). The trader version is the canonical text — risk_manager's version is the same shape with `pricing` → `risk` and `get_latest_position_valuations` → `get_latest_risk_run`. The fragment must read sensibly from BOTH personas' point of view, so we generalize.

`backend/app/services/deep_agent/skills/policy/read-before-compute.md`:

```markdown
## Read-before-Compute (mandatory order)

For any question about stored quantitative results (price, PnL, market value,
Greeks, NAV, risk, exposure, VaR, hedge feasibility):

1. **READ FIRST** — call the persona's stored-result reader
   (`get_latest_position_valuations` for trader; `get_latest_risk_run` for
   risk_manager; `get_positions` for inventory) before anything else.
2. **INSPECT** — check freshness (`Latest pricing run` / `Latest risk totals`
   line in the context) and completeness (rows present vs. portfolio size).
3. **ANSWER** from stored data when the data covers the question.
4. **PROPOSE, DO NOT RUN** if data is stale or missing. State exactly what's
   missing and offer the persisted action — wait for user confirmation before
   invoking the compute tool (`price_positions` or `run_risk`).

Only call in-memory compute tools (`price_product`, `calculate_risk`) for a
*new ad-hoc spec or supplied snapshot* that is not a persisted position. Never
call the persisted compute tool to answer a question about existing stored
prices/metrics — that is what the stored-result reader is for.
```

- [ ] **Step 3: Write `policy/cost-preview.md`**

Shared by all three personas, with per-persona thresholds. The fragment uses one combined table.

`backend/app/services/deep_agent/skills/policy/cost-preview.md`:

```markdown
## Cost-preview before expensive batches

Tools that exceed ~5 seconds require an explicit confirmation. Estimate locally
before invoking:

| Tool | Heuristic |
|---|---|
| `price_positions` | ~0.3s per position. >17 positions ⇒ exceeds 5s ⇒ preview first. |
| `run_risk` | ~0.5s per position. >10 positions ⇒ exceeds 5s ⇒ preview first. |
| `create_report` | always exceeds 5s ⇒ preview first. |
| `import_otc_positions` | always exceeds 5s ⇒ preview first. |
| `import_position_market_inputs` | always exceeds 5s ⇒ preview first. |
| `run_python` | ~3s Pyodide cold start + script time; always preview. |

When you need to invoke one of these, FIRST reply with a cost preview, e.g.:

  > "I'd like to reprice the 57 positions in portfolio_id=42 (~17s estimated).
  >  Run it now? (yes / no / adjust scope)"

Do NOT invoke the tool in the same turn as the preview — wait for the user's
"yes". The HITL middleware will pause again at tool-call time; that's a safety
net, not a substitute for asking up front.
```

- [ ] **Step 4: Write `policy/hitl-batch-size-1.md`**

Shared by all three personas.

`backend/app/services/deep_agent/skills/policy/hitl-batch-size-1.md`:

```markdown
## Batch-size-1 HITL rule

Never call more than one persisted (HITL-gated) tool in a single assistant
turn. The persisted tools are: `price_positions`, `run_risk`, `create_report`,
`create_or_update_rfq_draft`, `quote_rfq`, `submit_rfq_for_approval`,
`approve_rfq`, `reject_rfq`, `release_rfq`, `mark_rfq_client_accepted`,
`book_rfq_to_position`, `import_otc_positions`,
`import_position_market_inputs`. Each requires user confirmation. If multiple
persisted operations are needed, do the first, return the result, and let the
orchestrator route the next step.
```

- [ ] **Step 5: Write `policy/clarification-protocol.md`**

Shared by all three personas (each persona currently has a similar "Clarify before acting" block).

`backend/app/services/deep_agent/skills/policy/clarification-protocol.md`:

```markdown
## Clarify before acting

If the orchestrator's task prompt does not pin the target portfolio / position
/ underlying / RFQ, reply with a *defaulted question* instead of invoking
state-touching tools:

  > "Which portfolio should I check? I can default to the one in view, if you
  >  confirm."

If the user names a portfolio that is NOT in the context (e.g. "the Snowballs
portfolio"), do NOT say it doesn't exist. Call `list_portfolios` first to
resolve the name → id, then proceed (or report the closest matches if no exact
match exists). Treat name lookup as a read; no confirmation needed.

If the orchestrator's task prompt already pins the target, proceed.
```

- [ ] **Step 6: Write `policy/run-python-rfsw.md`**

Shared by trader + risk_manager (NOT high_board).

`backend/app/services/deep_agent/skills/policy/run-python-rfsw.md`:

```markdown
## Scripting for ad-hoc analytics with `run_python`

When the user wants a transformation, aggregation, or visualization that no
single existing tool produces — bucket positions by underlying, plot a PnL
distribution, compute a custom statistic — use the read-fetch-script-write
pattern:

1. **READ** — fetch data through existing tools (`get_positions`,
   `get_latest_position_valuations`, `get_latest_risk_run`). Do not invent or
   guess data inside the script.
2. **FETCH into payload** — pass the rows you fetched as the `payload` to
   `run_python`. Strip large/unused fields (e.g. `product_kwargs`,
   `engine_kwargs`) before passing, to keep the payload under ~5MB.
3. **SCRIPT** — write the transformation. Inside the script, the dict is
   named `data`; the result must be assigned to `result`; any text artifacts
   (HTML chart, CSV, markdown table) must be written under `ARTIFACT_DIR`
   (`/sandbox_out/`).
4. **WRITE** — after the tool returns, persist worthwhile artifacts to
   `/trading_desk/scripts/<descriptive-name>/...` via `write_file`. Do NOT
   dump binary content into the chat; prefer Plotly's `to_html()`, matplotlib
   SVG via `savefig(format="svg")`, or CSV/markdown — these are all text.

The sandbox has no host filesystem access and no host network (except the
Pyodide package CDN for `numpy`/`pandas`/`scipy`/`matplotlib`/`plotly`). It is
HITL-gated, so propose your code with a one-line `description` argument so the
user can review what will run.

Cost: ~3s Pyodide cold start on the first call per backend session, then
per-script time. Treat `run_python` like any other expensive tool — preview
the plan before invoking.
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/deep_agent/skills/policy/
git commit -m "feat(agent-skills): extract 5 policy fragments from persona prompts"
```

---

## Task 4: Trim persona prompts and wire skills_loader atomically

**Goal:** Edit each persona's `prompts/*.md` to remove the policy sections AND update `personas.py` to call `compose_persona_prompt` in the same commit. This preserves agent behavior end-to-end: the runtime-assembled `system_prompt` for each persona contains the same content as before, just sourced differently.

**Files:**
- Modify: `backend/app/services/deep_agent/prompts/trader.md`
- Modify: `backend/app/services/deep_agent/prompts/risk_manager.md`
- Modify: `backend/app/services/deep_agent/prompts/high_board.md`
- Modify: `backend/app/services/deep_agent/personas.py`

- [ ] **Step 1: Trim `prompts/trader.md`**

Remove these sections (whole H2 blocks): `## Read-before-Compute (mandatory order)`, `## Cost-preview before expensive batches`, `## Clarify before acting`, `## Scripting for ad-hoc analytics with run_python`, `## Batch-size-1 HITL rule`. Keep: opening identity paragraph, `## Tools you use`, `## Data access rule`, `## Accounting date`, `## Output style`.

Then add this new section at the end:

```markdown
## Routing from skills

The orchestrator may name a skill in the task description ("Use
`snowball-position-diagnostics`"). When it does, `read_file` the matching
SKILL.md from the catalog at `limit=1000` BEFORE invoking domain tools, then
follow its procedure.

For product-specific work, also read the matching product card from
`/skills/products/` before pricing or diagnostics in this session, if not
already loaded.
```

- [ ] **Step 2: Trim `prompts/risk_manager.md`**

Remove these sections: `## Read-before-Compute (mandatory order)`, `## Cost-preview before expensive batches`, `## Clarify before acting`, `## Scripting for ad-hoc risk analytics with run_python`, `## Batch-size-1 HITL rule`. Keep: opening identity paragraph, `## Tools you use`, `## Output style`.

Add the same `## Routing from skills` section as in trader.md (the wording is persona-agnostic).

- [ ] **Step 3: Trim `prompts/high_board.md`**

Remove: `## Cost-preview before expensive batches`, `## Clarify before acting`, `## Batch-size-1 HITL rule`. Keep: opening identity paragraph, `## Tools you use`, `## Output style`. (High_board did not have Read-before-Compute or run-python sections to remove.)

Add the same `## Routing from skills` section. (For V1, high_board has no skills in its catalog, but the directive is harmless and future-proofs the prompt for V2.)

- [ ] **Step 4: Rewrite `personas.py` to use `compose_persona_prompt`**

`backend/app/services/deep_agent/personas.py`:

```python
"""Persona SubAgent spec factories.

All three personas hold the *same full tool list* (per the design spec
locked decision); differentiation is via system prompt only. HITL gates
the persisted/state-mutating tools at runtime.

System prompts are composed from `prompts/<persona>.md` (identity + tools +
output style + routing-from-skills directive) plus a per-persona allowlist of
policy fragments from `skills/policy/`. See `skills_loader.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from app.services.deep_agent.skills_loader import compose_persona_prompt

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# Policy fragment allowlists per persona. Order is the order they appear in
# the composed prompt.
_TRADER_POLICY = (
    "read-before-compute",
    "cost-preview",
    "hitl-batch-size-1",
    "clarification-protocol",
    "run-python-rfsw",
)
_RISK_POLICY = _TRADER_POLICY
_BOARD_POLICY = (
    "cost-preview",
    "hitl-batch-size-1",
    "clarification-protocol",
)


def _load_identity(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _spec(
    *,
    name: str,
    description: str,
    prompt_file: str,
    tools: Sequence[BaseTool],
    policy_fragments: Sequence[str],
    skills: Sequence[str],
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "system_prompt": compose_persona_prompt(
            identity_prompt=_load_identity(prompt_file),
            policy_fragment_names=policy_fragments,
        ),
        "tools": list(tools),
        "skills": list(skills),
        # model and middleware inherit from the parent orchestrator.
    }


def trader_spec(model: BaseChatModel, tools: Sequence[BaseTool]) -> dict[str, Any]:
    return _spec(
        name="trader",
        description=(
            "Quotes, pricing, RFQ solving, market snapshots. "
            "Reads stored valuations and uses price_positions for explicit batch repricing."
        ),
        prompt_file="trader.md",
        tools=tools,
        policy_fragments=_TRADER_POLICY,
        skills=["/skills/procedures/trader/", "/skills/products/"],
    )


def risk_spec(model: BaseChatModel, tools: Sequence[BaseTool]) -> dict[str, Any]:
    return _spec(
        name="risk_manager",
        description=(
            "Limits, exposure, hedge feasibility. "
            "Reads stored risk and uses run_risk for explicit audited persisted risk runs."
        ),
        prompt_file="risk_manager.md",
        tools=tools,
        policy_fragments=_RISK_POLICY,
        skills=["/skills/procedures/risk_manager/", "/skills/products/"],
    )


def board_spec(model: BaseChatModel, tools: Sequence[BaseTool]) -> dict[str, Any]:
    return _spec(
        name="high_board",
        description=(
            "Release/approve, reporting. "
            "Uses approve_rfq, reject_rfq, create_report — all HITL-gated."
        ),
        prompt_file="high_board.md",
        tools=tools,
        policy_fragments=_BOARD_POLICY,
        skills=["/skills/procedures/high_board/"],
    )


def all_personas(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
) -> list[dict[str, Any]]:
    return [
        trader_spec(model, tools),
        risk_spec(model, tools),
        board_spec(model, tools),
    ]
```

Note: the `skills=[...]` field is a no-op until Task 5 wires the `CompositeBackend` so `/skills/...` resolves. That's fine — `SkillsMiddleware.before_agent` will get an empty list from `_list_skills` on a backend that has no such path, log a warning, and continue. No test failure expected.

- [ ] **Step 5: Smoke-check the composed prompt manually**

```bash
cd /Users/fuxinyao/open-otc-trading
python -c "
from app.services.deep_agent.skills_loader import compose_persona_prompt
from pathlib import Path
prompts = Path('backend/app/services/deep_agent/prompts')
identity = (prompts / 'trader.md').read_text()
out = compose_persona_prompt(
    identity_prompt=identity,
    policy_fragment_names=[
        'read-before-compute', 'cost-preview', 'hitl-batch-size-1',
        'clarification-protocol', 'run-python-rfsw',
    ],
)
print(out)
" | head -80
```

Expected output: starts with "You are the trader persona…", contains the tools list, ends with `## Run-python` content. Sanity-check that no section was lost.

- [ ] **Step 6: Run the existing test suite to confirm behavior preserved**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest backend/tests/ -v -x 2>&1 | tail -40
```

Expected: existing tests pass (skills_loader tests added in Task 2 still pass; no other regressions).

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/deep_agent/personas.py backend/app/services/deep_agent/prompts/
git commit -m "refactor(agent-skills): wire personas.py to compose policy fragments

Trims the policy sections out of prompts/{trader,risk_manager,high_board}.md
and reassembles them at agent build time via compose_persona_prompt().
Runtime system_prompt content is unchanged for trader/risk_manager; for
high_board, the composed prompt is slightly smaller because it doesn't take
the read-before-compute or run-python-rfsw fragments. Adds skills=[...] on
each SubAgent spec; the catalog is empty until the backend is wired in the
next commit."
```

---

## Task 5: Wire CompositeBackend, FilesystemBackend, and `/skills` permission

**Files:**
- Modify: `backend/app/services/deep_agent/orchestrator.py`

- [ ] **Step 1: Read the current orchestrator wiring**

```bash
cd /Users/fuxinyao/open-otc-trading
sed -n '1,80p' backend/app/services/deep_agent/orchestrator.py
```

Confirm `build_orchestrator` currently calls `create_deep_agent(...)` without a `backend=` kwarg.

- [ ] **Step 2: Rewrite `orchestrator.py`**

`backend/app/services/deep_agent/orchestrator.py`:

```python
"""Top-level orchestrator builder.

The orchestrator has *no domain tools* of its own — its job is to plan,
delegate via the auto-injected `task` tool, and synthesize. All quant
tools live on the persona subagents, gated by HITL at runtime.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from .hitl import interrupt_on_config
from .personas import all_personas

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_SKILLS_FS_ROOT = Path(__file__).parent / "skills"


def _orchestrator_prompt() -> str:
    return (_PROMPTS_DIR / "orchestrator.md").read_text(encoding="utf-8")


def _build_backend() -> Any:
    """Build a CompositeBackend that routes /skills/ to a FilesystemBackend
    rooted at the on-disk skills tree, with StateBackend as the default for
    /trading_desk/, /large_tool_results/, and everything else.

    Path semantics:
    - Virtual mode on the FilesystemBackend prevents path traversal and pins
      all `/skills/...` reads to `_SKILLS_FS_ROOT`.
    - CompositeBackend strips the `/skills/` prefix when routing, so the
      FilesystemBackend sees paths like `/procedures/trader/foo/SKILL.md`
      relative to its own virtual root.
    """
    from deepagents.backends import StateBackend
    from deepagents.backends.composite import CompositeBackend
    from deepagents.backends.filesystem import FilesystemBackend

    skills_fs = FilesystemBackend(root_dir=str(_SKILLS_FS_ROOT), virtual_mode=True)
    return CompositeBackend(
        default=StateBackend(),
        routes={"/skills/": skills_fs},
    )


def _filesystem_permissions() -> list[Any]:
    from deepagents.middleware.permissions import FilesystemPermission

    return [
        FilesystemPermission(
            operations=["read"],
            paths=["/"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/trading_desk", "/trading_desk/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read"],
            paths=["/large_tool_results", "/large_tool_results/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read"],
            paths=["/skills", "/skills/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/", "/**"],
            mode="deny",
        ),
    ]


def build_orchestrator(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    checkpointer: Any,
    interrupt_on: dict[str, Any] | None = None,
) -> Any:
    """Create the desk deep-agent orchestrator with three persona subagents."""
    from deepagents import create_deep_agent

    return create_deep_agent(
        model=model,
        tools=[],  # orchestrator has no domain tools
        system_prompt=_orchestrator_prompt(),
        subagents=all_personas(model, tools),
        interrupt_on=interrupt_on if interrupt_on is not None else interrupt_on_config(),
        checkpointer=checkpointer,
        backend=_build_backend(),
        permissions=_filesystem_permissions(),
        name="otc_desk_orchestrator",
    )
```

- [ ] **Step 3: Smoke-check that the orchestrator builds**

```bash
cd /Users/fuxinyao/open-otc-trading
python -c "
from langchain_openai import ChatOpenAI  # any chat-model stub will do
from app.services.deep_agent.orchestrator import build_orchestrator
from app.services.deep_agent.checkpointer import build_checkpointer
import os
os.environ.setdefault('OPENAI_API_KEY', 'x')

model = ChatOpenAI(model='gpt-4o-mini')
agent = build_orchestrator(model=model, tools=[], checkpointer=build_checkpointer())
print('OK, agent built:', type(agent).__name__)
"
```

Expected: prints `OK, agent built: <some class>`. If `build_checkpointer` has a different signature or `langchain_openai` is not installed, substitute with an existing test pattern — the goal is just to confirm `build_orchestrator` does not raise.

If the smoke check fails because `langchain_openai` is missing, instead run:

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest backend/tests/ -v -k "orchestrator or persona" 2>&1 | tail -30
```

Expected: existing tests that exercise the orchestrator build still pass.

- [ ] **Step 4: Run the full test suite**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest backend/tests/ -v 2>&1 | tail -40
```

Expected: no new failures.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/orchestrator.py
git commit -m "feat(agent-skills): route /skills/ to FilesystemBackend via CompositeBackend

Adds an explicit CompositeBackend with StateBackend default (preserving the
existing trading_desk and large_tool_results behavior) and a FilesystemBackend
rooted at backend/app/services/deep_agent/skills/ for /skills/* lookups.
Adds /skills read-allow permission rule before the deny-all tail."
```

---

## Task 6: Author the snowball-cn product card

**Files:**
- Create: `backend/app/services/deep_agent/skills/products/snowball-cn/SKILL.md`

- [ ] **Step 1: Write the product card**

`backend/app/services/deep_agent/skills/products/snowball-cn/SKILL.md`:

```markdown
---
name: snowball-cn
description: A-share Snowball (雪球) autocallable structured product reference. Read before pricing, diagnosing, or quoting any CN-market Snowball position. Covers payoff invariants (monthly KO obs, daily KI obs, coupon accrual), QuantArk engine selection, and CN-specific quirks (CSI 300 / CSI 500 underlyings, T+1 settlement, ACT/365 day-count).
metadata:
  tier: product-card
  market: CN
  product_types: snowball
---

# Snowball (CN A-share) — Product Card

## What it is

A path-dependent autocallable on a single A-share index underlying (typically
CSI 300 or CSI 500). The product knocks out and pays accrued coupon early if
the underlying closes at or above the KO barrier on any scheduled monthly
observation date. If never knocked out and never knocked in (daily intraday
breach of a lower KI barrier), it returns full principal at maturity. If
knocked in but never knocked out, it takes equity loss at maturity equal to
the underlying's terminal return below the strike.

## Key invariants

- **KO observation:** monthly on a published schedule. KO at obs N pays
  `notional × (1 + coupon_rate × month_count_N / 12)`.
- **KI observation:** daily continuous-monitoring approximation. KI is a
  *path* event — once breached, stays breached, regardless of subsequent
  recovery.
- **Coupon accrual:** linear in calendar months until KO triggers. Realized
  coupon = stored `accrued_coupon` field on the position row.
- **Strike vs KI:** strike is typically 100% of initial price; KI is typically
  70–80% of initial. The loss leg at maturity uses strike, not KI.
- **Tenor:** typically 24m.
- **First KO obs:** typically month 3 (no KO for the first two months).

## Pricing engine & market inputs

- **QuantArk engine:** `SnowballMCEngine` (Monte Carlo with daily KI grid).
- **Required inputs:** spot, vol surface or flat ATM vol, risk-free rate `r`,
  dividend yield `q` (or scheduled discrete dividends), historical
  realized-vol regime if calibrating jumps.
- **Sensitive to:** vol regime near the KI barrier (gamma spikes locally);
  expected dividend yield (Snowballs are dividend-short — higher q lowers
  the seller's expected payoff).
- **Path-count guidance:** ≥10,000 paths for production pricing; 1,000–2,000
  paths acceptable for live-quote estimates.

## Market quirks

- **Underlyings:** CSI 300 and CSI 500 are the canonical CN Snowball
  references. Check index continuity around rebalance dates (June, December
  for CSI 300).
- **Settlement:** T+1. Effective date is trade date + 1 business day; first
  KO obs counts from the effective date, not the trade date.
- **Day-count:** ACT/365 fixed for CN OTC coupon accrual. Do not assume
  ACT/360 or 30/360.
- **Holiday calendar:** China Mainland (SSE/SZSE). Cross-check obs dates that
  fall on Chinese New Year or National Day — they roll to the next business
  day per ISDA convention.

## Common diagnostics signals

- Spot within 5% of KI: elevated gamma; flag for hedge review.
- Spot within 2% of next monthly KO: impending autocall — revalue with fresh
  vol if last pricing run is > 1 BD old.
- Stale `q` (dividend yield input > 5 BD old): refetch via
  `fetch_market_snapshot` before any new pricing run.
- Realized `accrued_coupon` materially diverges from
  `days_since_trade × notional × coupon_rate / 365`: investigate as data
  drift; ask before repricing.

## See also

- Procedure: `snowball-position-diagnostics` (both trader and risk_manager
  variants).
```

- [ ] **Step 2: Validate frontmatter parses cleanly**

```bash
cd /Users/fuxinyao/open-otc-trading
python -c "
from deepagents.middleware.skills import _parse_skill_metadata
from pathlib import Path
p = Path('backend/app/services/deep_agent/skills/products/snowball-cn/SKILL.md')
content = p.read_text()
meta = _parse_skill_metadata(content=content, skill_path=str(p), directory_name='snowball-cn')
assert meta is not None, 'frontmatter parse failed'
assert meta['name'] == 'snowball-cn', meta['name']
assert 'autocallable' in meta['description'].lower()
print('OK:', meta['name'])
"
```

Expected: prints `OK: snowball-cn`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/deep_agent/skills/products/snowball-cn/
git commit -m "feat(agent-skills): add snowball-cn product card"
```

---

## Task 7: Author trader snowball-position-diagnostics

**Files:**
- Create: `backend/app/services/deep_agent/skills/procedures/trader/snowball-position-diagnostics/SKILL.md`

- [ ] **Step 1: Remove the `.gitkeep` from `procedures/trader/`**

```bash
cd /Users/fuxinyao/open-otc-trading
git rm backend/app/services/deep_agent/skills/procedures/trader/.gitkeep
```

(The directory now has real content; the gitkeep is no longer needed.)

- [ ] **Step 2: Write the trader procedure SKILL.md**

`backend/app/services/deep_agent/skills/procedures/trader/snowball-position-diagnostics/SKILL.md`:

```markdown
---
name: snowball-position-diagnostics
description: Walk through a Snowball portfolio's PRICING health — KO/KI distance vs current spot, observation-date proximity, coupon accrual progress, and stale-input checks. Read when the user asks "is the snowball book OK", "any positions near KO", "how close to KI", or before a Snowball repricing run. Pairs with the snowball-cn product card.
allowed-tools: get_positions get_latest_position_valuations fetch_market_snapshot price_positions
metadata:
  tier: procedure
  persona: trader
  related_products: snowball-cn
---

# Snowball Position Diagnostics — Trader (Pricing) Lens

## When this applies

- User asks about Snowball book health, KO/KI distance, or autocall proximity.
- User requests a Snowball repricing — run diagnostics BEFORE proposing
  `price_positions` so you can scope the repricing to actually-stale rows.
- User asks "how is the snowball portfolio doing" without specifying — treat
  as a diagnostic request.

## Inputs to inspect first

1. `get_positions(portfolio_id=<X>, product_type="snowball")` — confirm
   position count and underlyings.
2. `get_latest_position_valuations(portfolio_id=<X>)` — pull stored prices,
   market value, accrued coupon, and the most recent valuation timestamp.
3. Read the `snowball-cn` product card if not already loaded in this session.
   The KO/KI semantics below assume the conventions defined there.

## Step sequence

1. From each row, compute KI distance `(spot - KI) / spot` and next-KO
   distance `(KO_next - spot) / spot`. Use the stored snapshot's spot for
   reads; only refetch via `fetch_market_snapshot` if the stored valuation is
   older than 1 BD.
2. Flag positions where:
   - KI distance < 5% (gamma-risk zone — note for the risk_manager handoff).
   - Next-KO distance < 2% (impending autocall — recommend a fresh price).
3. Check the `Latest pricing run` line in the context. If older than 1 BD
   AND any position is flagged at step 2, propose a fresh `price_positions`
   run scoped to the flagged positions (apply the cost-preview policy from
   the system prompt before invoking).
4. Check coupon accrual: for each non-knocked-out position, compare stored
   `accrued_coupon` to the linear-accrual expectation
   `notional × coupon_rate × (days_since_effective / 365)`. Flag drift >1%
   for data review; do not auto-correct.

## What success looks like

Produce a short report of the form:

> "Portfolio <X>: <N> Snowball positions. <K> flagged near KI (list
> position_ids), <M> flagged near KO (list position_ids). Accrual sane
> across all rows / drift detected on <count> rows. Latest pricing run is
> <age in BD>. Recommend: <one of: no action / refresh inputs / reprice
> flagged subset>."

Cite the freshness of the underlying data so the orchestrator can decide
whether to escalate to the risk_manager.

## Tool preferences

- **READ-FIRST:** `get_latest_position_valuations`, `get_positions`,
  `fetch_market_snapshot`. None require HITL.
- **COMPUTE:** `price_positions` ONLY after cost-preview, ONLY if stored data
  is stale or step-2 flags justify a fresh price. Scope to flagged positions
  when possible.
- **DO NOT USE:** `price_product` — it's for new ad-hoc product specs, not
  for diagnostics on existing positions.
- **DO NOT MUTATE STATE** unless the user has confirmed a `price_positions`
  run. Diagnostics is a read-and-report operation by default.
```

- [ ] **Step 3: Validate the file parses**

```bash
cd /Users/fuxinyao/open-otc-trading
python -c "
from deepagents.middleware.skills import _parse_skill_metadata
from pathlib import Path
p = Path('backend/app/services/deep_agent/skills/procedures/trader/snowball-position-diagnostics/SKILL.md')
content = p.read_text()
meta = _parse_skill_metadata(content=content, skill_path=str(p), directory_name='snowball-position-diagnostics')
assert meta is not None, 'frontmatter parse failed'
assert meta['name'] == 'snowball-position-diagnostics'
assert 'get_positions' in meta['allowed_tools']
print('OK:', meta['name'], 'tools:', meta['allowed_tools'])
"
```

Expected: `OK: snowball-position-diagnostics tools: ['get_positions', 'get_latest_position_valuations', 'fetch_market_snapshot', 'price_positions']`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/deep_agent/skills/procedures/trader/
git commit -m "feat(agent-skills): add trader snowball-position-diagnostics procedure"
```

---

## Task 8: Author risk_manager snowball-position-diagnostics

**Files:**
- Create: `backend/app/services/deep_agent/skills/procedures/risk_manager/snowball-position-diagnostics/SKILL.md`

- [ ] **Step 1: Remove the `.gitkeep` from `procedures/risk_manager/`**

```bash
cd /Users/fuxinyao/open-otc-trading
git rm backend/app/services/deep_agent/skills/procedures/risk_manager/.gitkeep
```

- [ ] **Step 2: Write the risk_manager procedure SKILL.md**

`backend/app/services/deep_agent/skills/procedures/risk_manager/snowball-position-diagnostics/SKILL.md`:

```markdown
---
name: snowball-position-diagnostics
description: Walk through a Snowball portfolio's RISK health — delta/gamma concentration near KI barrier, vega exposure to vol regime, autocall-day Greek discontinuities, and hedge feasibility. Read when the user asks about Snowball risk, exposure, hedge sizing, or "what breaks if vol spikes". Pairs with the snowball-cn product card.
allowed-tools: get_positions get_latest_risk_run calculate_risk recommend_hedge run_risk
metadata:
  tier: procedure
  persona: risk_manager
  related_products: snowball-cn
---

# Snowball Position Diagnostics — Risk Lens

## When this applies

- User asks about Snowball risk, hedge feasibility, or "what happens if vol
  spikes" / "if spot drops X%".
- Before approving a new Snowball quote whose risk impact is not yet in the
  latest stored risk run.
- After a trader diagnostic flags positions in the KI-gamma zone — the
  orchestrator will hand off here.

## Inputs to inspect first

1. `get_positions(portfolio_id=<X>, product_type="snowball")` — position
   inventory and underlyings.
2. `get_latest_risk_run(portfolio_id=<X>)` — stored Greeks (delta, gamma,
   vega) and any saved stress results.
3. Read the `snowball-cn` product card if not already loaded. The KI
   gamma-spike and dividend-yield sensitivity facts below come from there.

## Step sequence

1. From the latest risk run, isolate the per-position delta, gamma, and vega
   contributions for Snowball rows. Sum them at the portfolio level.
2. Identify positions whose `(spot - KI) / spot < 0.05` — gamma-spike zone.
   Sum their delta and gamma. Report the concentration ratio (gamma in
   spike-zone / total Snowball gamma).
3. Read the vega: if `|vega_total| > <site limit; check current risk
   policy>`, propose a vega hedge via `recommend_hedge` and quantify the
   expected vega shift.
4. If the latest risk run is older than 1 BD OR any position is in the
   gamma-spike zone OR the user requested a fresh scenario, propose
   `run_risk` (apply the cost-preview policy from the system prompt before
   invoking).
5. For "what if" scenarios, use `calculate_risk` against an in-memory
   snapshot — DO NOT call `run_risk` for hypotheticals.

## What success looks like

Produce a verdict + supporting metrics:

> "Portfolio <X>: <N> Snowball positions, delta=<Δ>, gamma=<Γ>, vega=<V>.
> <K> positions in gamma-spike zone (list position_ids) accounting for
> <pct>% of total gamma. Vega exposure <within / outside> limits. Recommended
> hedge: <none / specific instrument with quantity and expected metric
> shift>. Risk run age: <BD>. Recommend: <no action / refresh / scenario
> stress>."

Lead with within-limits / breach / unknown. Do not propose hedges without
quantifying the metric the hedge would shift.

## Tool preferences

- **READ-FIRST:** `get_latest_risk_run`, `get_positions`. No HITL required.
- **COMPUTE (in-memory, no persistence):** `calculate_risk` for hypothetical
  hedge snapshots; `recommend_hedge` for sizing suggestions.
- **COMPUTE (persisted):** `run_risk` ONLY after cost-preview, ONLY if step 4
  conditions are met.
- **DO NOT** propose hedges that don't quantify the targeted metric shift.
- **DO NOT** call `run_risk` for hypothetical/exploratory scenarios — that's
  what `calculate_risk` is for.
```

- [ ] **Step 3: Validate the file parses**

```bash
cd /Users/fuxinyao/open-otc-trading
python -c "
from deepagents.middleware.skills import _parse_skill_metadata
from pathlib import Path
p = Path('backend/app/services/deep_agent/skills/procedures/risk_manager/snowball-position-diagnostics/SKILL.md')
content = p.read_text()
meta = _parse_skill_metadata(content=content, skill_path=str(p), directory_name='snowball-position-diagnostics')
assert meta is not None
assert meta['name'] == 'snowball-position-diagnostics'
assert 'run_risk' in meta['allowed_tools']
assert 'calculate_risk' in meta['allowed_tools']
print('OK:', meta['name'])
"
```

Expected: `OK: snowball-position-diagnostics`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/deep_agent/skills/procedures/risk_manager/
git commit -m "feat(agent-skills): add risk_manager snowball-position-diagnostics procedure"
```

---

## Task 9: Catalog integration tests + read_file no-HITL guard

**Goal:** Prove that per-persona `SkillsMiddleware` isolation works (trader and risk_manager see only their own procedure, but both see the shared product card) and that `read_file` is not gated by HITL.

**Files:**
- Create: `backend/tests/services/deep_agent/test_skills_catalog.py`

- [ ] **Step 1: Write the catalog test using the deepagents helpers directly**

`backend/tests/services/deep_agent/test_skills_catalog.py`:

```python
"""Integration tests for the per-persona skills catalog and HITL behavior.

These tests exercise SkillsMiddleware's source-loading machinery directly
(via the internal _list_skills helper backed by a FilesystemBackend pointing
at the real on-disk skills tree). This avoids spinning up a full model
session while still proving:

1. Per-persona catalogs are isolated (trader and risk_manager each see only
   their own `procedures/<persona>/` source).
2. The shared `products/` source surfaces to both personas.
3. `read_file` is not in the HITL interrupt list (so skill body reads never
   pause for confirmation).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.skills import _list_skills

from app.services.deep_agent.hitl import interrupt_on_config

_SKILLS_ROOT = (
    Path(__file__).resolve().parents[3] / "app" / "services" / "deep_agent" / "skills"
)


@pytest.fixture
def skills_backend() -> FilesystemBackend:
    return FilesystemBackend(root_dir=str(_SKILLS_ROOT), virtual_mode=True)


def _names(skills: list[dict]) -> set[str]:
    return {s["name"] for s in skills}


def test_trader_procedure_source_has_snowball_diagnostics(
    skills_backend: FilesystemBackend,
) -> None:
    skills = _list_skills(skills_backend, "/procedures/trader/")
    assert _names(skills) == {"snowball-position-diagnostics"}
    only = skills[0]
    assert only["metadata"]["persona"] == "trader"
    assert only["metadata"]["related_products"] == "snowball-cn"


def test_risk_manager_procedure_source_has_snowball_diagnostics(
    skills_backend: FilesystemBackend,
) -> None:
    skills = _list_skills(skills_backend, "/procedures/risk_manager/")
    assert _names(skills) == {"snowball-position-diagnostics"}
    only = skills[0]
    assert only["metadata"]["persona"] == "risk_manager"


def test_products_source_has_snowball_cn(skills_backend: FilesystemBackend) -> None:
    skills = _list_skills(skills_backend, "/products/")
    assert _names(skills) == {"snowball-cn"}
    only = skills[0]
    assert only["metadata"]["tier"] == "product-card"
    assert only["metadata"]["market"] == "CN"


def test_high_board_procedure_source_is_empty(skills_backend: FilesystemBackend) -> None:
    # The directory exists (kept by .gitkeep) but contains no SKILL.md.
    skills = _list_skills(skills_backend, "/procedures/high_board/")
    assert skills == []


def test_trader_and_risk_catalogs_dont_share_procedure_bodies(
    skills_backend: FilesystemBackend,
) -> None:
    trader = _list_skills(skills_backend, "/procedures/trader/")
    risk = _list_skills(skills_backend, "/procedures/risk_manager/")
    # Same name on purpose
    assert {s["name"] for s in trader} == {s["name"] for s in risk}
    # Different paths (different SKILL.md files)
    assert trader[0]["path"] != risk[0]["path"]
    # Different allowed_tools (pricing vs risk surface)
    assert set(trader[0]["allowed_tools"]) != set(risk[0]["allowed_tools"])


def test_read_file_is_not_hitl_gated() -> None:
    """`read_file` must never appear in the interrupt-on config; skill body
    reads would otherwise pause for confirmation and defeat progressive
    disclosure."""
    config = interrupt_on_config()
    # interrupt_on_config returns dict[str, Any] keyed by tool name.
    assert "read_file" not in config, (
        f"read_file must not be HITL-gated, got config keys: {list(config)}"
    )
```

- [ ] **Step 2: Run the catalog tests**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest backend/tests/services/deep_agent/test_skills_catalog.py -v
```

Expected: all 6 tests PASS. If `_list_skills` is not importable as a private function, adapt the test to use the public `SkillsMiddleware` API instead — the assertions on `skills_metadata` after `before_agent` are equivalent.

If `interrupt_on_config()` returns a different shape than `dict[str, Any]`, adapt the `read_file` assertion to match (e.g., if it returns a list of tool names, check membership directly).

- [ ] **Step 3: Run the full test suite**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest backend/tests/ -v 2>&1 | tail -20
```

Expected: all previously passing tests still pass; new tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/services/deep_agent/test_skills_catalog.py
git commit -m "test(agent-skills): catalog isolation and read_file no-HITL guard"
```

---

## Task 10: Update orchestrator prompt — Naming skills + Routing matrix row

**Files:**
- Modify: `backend/app/services/deep_agent/prompts/orchestrator.md`

- [ ] **Step 1: Read the current orchestrator prompt**

```bash
cd /Users/fuxinyao/open-otc-trading
sed -n '34,40p' backend/app/services/deep_agent/prompts/orchestrator.md
```

Locate the existing `## Routing (after clarification is clean)` section.

- [ ] **Step 2: Insert "Naming skills in delegations" section after Routing**

Right after the existing `## Routing (after clarification is clean)` section (and before `## Cost-preview rule (for expensive tools)`), insert:

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

### Known skills (V1)

| Request shape                                          | Persona       | Suggested skill                  |
|--------------------------------------------------------|---------------|----------------------------------|
| Snowball book health, KO/KI proximity, autocall risk   | trader        | snowball-position-diagnostics    |
| Snowball risk, hedge feasibility, gamma near KI        | risk_manager  | snowball-position-diagnostics    |
```

Leave all other sections of `orchestrator.md` unchanged.

- [ ] **Step 3: Verify the prompt still loads**

```bash
cd /Users/fuxinyao/open-otc-trading
python -c "
from pathlib import Path
p = Path('backend/app/services/deep_agent/prompts/orchestrator.md')
text = p.read_text(encoding='utf-8')
assert 'Naming skills in delegations' in text
assert 'snowball-position-diagnostics' in text
print('OK, orchestrator prompt updated, length =', len(text))
"
```

Expected: prints `OK, orchestrator prompt updated, length = <N>`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/deep_agent/prompts/orchestrator.md
git commit -m "feat(agent-skills): orchestrator names skills in delegations + V1 routing matrix"
```

---

## Task 11: Final verification — full test suite and end-to-end build

**Files:** none — verification only.

- [ ] **Step 1: Run the full test suite**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest backend/tests/ -v 2>&1 | tail -30
```

Expected: every test passes. If any pre-existing test fails, investigate
whether the failure is related to the changes (it should not be — the policy
extraction was byte-identical and additive wiring is behavior-preserving for
existing flows).

- [ ] **Step 2: Verify the on-disk skills tree is complete**

```bash
cd /Users/fuxinyao/open-otc-trading
find backend/app/services/deep_agent/skills -type f -name "*.md" | sort
```

Expected output (order may vary):

```
backend/app/services/deep_agent/skills/README.md
backend/app/services/deep_agent/skills/policy/clarification-protocol.md
backend/app/services/deep_agent/skills/policy/cost-preview.md
backend/app/services/deep_agent/skills/policy/hitl-batch-size-1.md
backend/app/services/deep_agent/skills/policy/read-before-compute.md
backend/app/services/deep_agent/skills/policy/run-python-rfsw.md
backend/app/services/deep_agent/skills/procedures/risk_manager/snowball-position-diagnostics/SKILL.md
backend/app/services/deep_agent/skills/procedures/trader/snowball-position-diagnostics/SKILL.md
backend/app/services/deep_agent/skills/products/snowball-cn/SKILL.md
```

That's 1 README + 5 policy fragments + 2 procedure SKILL.md + 1 product card = 9 markdown files (+ the `procedures/high_board/.gitkeep` marker file).

- [ ] **Step 3: Smoke-check the assembled trader prompt**

```bash
cd /Users/fuxinyao/open-otc-trading
python -c "
from app.services.deep_agent.personas import trader_spec
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
m = FakeMessagesListChatModel(responses=[])
spec = trader_spec(m, [])
sp = spec['system_prompt']
# Must contain identity opener
assert 'You are the trader persona' in sp, 'identity missing'
# Must contain all 5 policy fragments (verify by their H2 headers)
for needle in [
    '## Read-before-Compute',
    '## Cost-preview before expensive batches',
    '## Batch-size-1 HITL rule',
    '## Clarify before acting',
    '## Scripting for ad-hoc analytics',
]:
    assert needle in sp, f'missing fragment: {needle}'
# Must declare its skill sources
assert spec['skills'] == ['/skills/procedures/trader/', '/skills/products/']
print('OK, trader spec assembled correctly, system_prompt length =', len(sp))
"
```

Expected: prints `OK, trader spec assembled correctly, system_prompt length = <N>`.

- [ ] **Step 4: Smoke-check the high_board prompt does NOT have read-before-compute or run-python**

```bash
cd /Users/fuxinyao/open-otc-trading
python -c "
from app.services.deep_agent.personas import board_spec
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
m = FakeMessagesListChatModel(responses=[])
spec = board_spec(m, [])
sp = spec['system_prompt']
assert 'You are the high_board persona' in sp
# Must contain the 3 fragments it does use
assert '## Cost-preview before expensive batches' in sp
assert '## Batch-size-1 HITL rule' in sp
assert '## Clarify before acting' in sp
# Must NOT contain the fragments it doesn't use
assert '## Read-before-Compute' not in sp, 'high_board should not have read-before-compute'
assert '## Scripting for ad-hoc' not in sp, 'high_board should not have run-python pattern'
print('OK, board spec assembled correctly, system_prompt length =', len(sp))
"
```

Expected: prints `OK, board spec assembled correctly, system_prompt length = <N>`.

- [ ] **Step 5: Final commit if any cleanup needed**

If no further changes were needed, no commit. Otherwise:

```bash
git status --short
# If anything is uncommitted that shouldn't be, investigate before committing.
```

- [ ] **Step 6: Summary**

The V1 skills layer is complete. Summary:

- 5 policy fragments composed into persona system prompts at build time.
- 2 procedure SKILL.md files (trader + risk_manager, shared name, different bodies).
- 1 product card SKILL.md.
- CompositeBackend routing `/skills/` to FilesystemBackend; everything else stays on StateBackend.
- Orchestrator prompt names skills in delegations; Routing matrix lists the V1 skill.
- Tests cover loader unit behavior, catalog isolation, and the read_file no-HITL guard.

Backfill of the four other candidate procedures (`rfq-intake-and-quote`,
`portfolio-pricing-run`, `risk-report-workflow`, `market-data-profile`) and
additional product cards (`phoenix-cn`, `accumulator-cn`, etc.) becomes
follow-on writing-plans cycles.

---

## Self-review checklist (run before handoff)

1. **Spec coverage:** Every section of the spec maps to at least one task:
   - §1 Architecture overview → Tasks 1–11 collectively
   - §2 Directory layout → Tasks 1, 6, 7, 8
   - §3 Persona prompt assembly → Tasks 2, 3, 4
   - §4 Wiring → Task 5
   - §5 Skill file format → Tasks 6, 7, 8 (frontmatter + body schema demonstrated)
   - §6 Orchestrator integration → Task 10
   - §7 Testing & migration → Tasks 2 (Tier A), 9 (Tier B + C), 11 (suite-wide)
   - §8 Future work → out of scope (correctly deferred)

2. **Placeholder scan:** No "TBD", "TODO", "fill in", "similar to Task N",
   or instruction-only steps. Every code step shows the code.

3. **Type consistency:**
   - `compose_persona_prompt(*, identity_prompt, policy_fragment_names)`
     defined in Task 2, used in Task 4 — names match.
   - `_TRADER_POLICY`, `_RISK_POLICY`, `_BOARD_POLICY` defined in Task 4 are
     internal constants; they don't appear later.
   - `_build_backend()` defined in Task 5 is internal; not referenced after.
   - Skill names (`snowball-position-diagnostics`, `snowball-cn`) match
     across tasks 6, 7, 8, 9, 10.
   - Source path strings (`/skills/procedures/trader/` etc.) match between
     `personas.py` (Task 4) and the tests (Task 9). The leading `/skills/`
     is stripped by `CompositeBackend`, so the test's `_list_skills` calls
     use the post-strip form (`/procedures/trader/`).

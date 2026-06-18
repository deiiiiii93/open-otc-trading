# Frontend Skill Management + Hot Reload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new "Skills" frontend page with full CRUD for workflow skills (view/edit for meta policies and reference docs), server-side lint blocking, auto orchestrator rebuild on save, and an orchestrator routing table generated from skill frontmatter.

**Architecture:** A new `APIRouter` factory (`backend/app/routers/skills.py`) wraps the existing `skill_lint` / `validate_meta_policy_file` / `validate_reference_doc_file` validators behind file-CRUD endpoints and calls a new narrow `AgentService.rebuild_orchestrator()` after each successful write. The orchestrator prompt's hand-written "Known single-persona skills" table is replaced by a sentinel that `_orchestrator_prompt()` fills from a new `routing` frontmatter field at build time. The frontend adds a Skills page following the repo's `Page.tsx` / `Page.live.tsx` split.

**Tech Stack:** FastAPI + Pydantic v2 (backend), PyYAML + tiktoken (already deps), React + vitest + @testing-library (frontend).

**Spec:** `docs/superpowers/specs/2026-06-06-frontend-skill-management-design.md`

---

## Conventions and gotchas (read first)

- **Workspace:** the git worktree `/Users/fuxinyao/open-otc-trading/.claude/worktrees/frontend-skill-management`, branch `worktree-frontend-skill-management`. All paths below are relative to the worktree root. Run all commands from the worktree root unless stated.
- **Backend tests:** `python -m pytest tests/<file> -q`. **Never use `python -c "import app..."`** — the shared venv's `.pth` resolves `app` to the MAIN checkout, not this worktree. pytest from the worktree root resolves correctly.
- **Frontend:** `cd frontend && npx vitest run` for tests, and **always also `npx tsc --noEmit`** — vitest does not typecheck.
- **Known pre-existing failure (not yours):** `tests/test_personas.py::test_orchestrator_can_enable_quickjs_code_interpreter_middleware` fails with `ModuleNotFoundError: langchain_quickjs` (optional dep not installed). Ignore it; everything else in the named suites must pass.
- **Commit after every task** with the message given in the task's final step.
- Workflow skill frontmatter field order (canonical, used by the serializer):
  `name, description, domain, workflow_type, allowed_envelopes, may_escalate_to, required_context, optional_context, write_actions, confirmation_required, success_criteria, routing`.
- The three personas and their visible workflow domains (current behavior, do not change):
  - `trader`: positions, products, try-solve, pricing, hedging, market-data, portfolios, rfq, snowballs
  - `risk_manager`: positions, risk, hedging, pricing, market-data, portfolios, reporting, snowballs
  - `high_board`: portfolios, reporting

## File map

**Create (backend):**
- `backend/app/services/deep_agent/persona_domains.py` — per-persona workflow-domain visibility constant
- `backend/app/services/deep_agent/routing_table.py` — routing-table generator + sentinel injection
- `backend/app/routers/__init__.py` — empty package marker
- `backend/app/routers/skills.py` — skills CRUD router (models live here; this router is self-contained)

**Modify (backend):**
- `backend/app/services/deep_agent/personas.py` — derive skill sources from `persona_domains`
- `backend/app/services/deep_agent/skill_lint.py` — text-input lint + routing validation
- `backend/app/services/deep_agent/orchestrator.py` — inject generated table in `_orchestrator_prompt()`
- `backend/app/services/deep_agent/prompts/orchestrator.md` — table → sentinel
- `backend/app/services/agents.py` — `AgentService.rebuild_orchestrator()`
- `backend/app/main.py` — `include_router`
- 18 `backend/app/skills/workflows/**/SKILL.md` files — `routing:` backfill

**Create (tests):**
- `tests/test_persona_domains.py`, `tests/test_skill_lint_routing.py`, `tests/test_routing_table.py`, `tests/test_agents_rebuild.py`, `tests/test_skills_api.py`

**Modify (tests):**
- `tests/test_workflow_skills_phase3.py` — composed-prompt fix for the sentinel swap

**Create (frontend):**
- `frontend/src/routes/SkillsWorkflowForm.tsx`, `frontend/src/routes/Skills.tsx`, `frontend/src/routes/Skills.css`, `frontend/src/routes/Skills.live.tsx`, `frontend/src/routes/SkillsWorkflowForm.test.tsx`, `frontend/src/routes/Skills.test.tsx`, `frontend/src/routes/Skills.live.test.tsx`

**Modify (frontend):**
- `frontend/src/types.ts`, `frontend/src/api/client.ts`, `frontend/src/main.tsx`

---

### Task 1: `PERSONA_WORKFLOW_DOMAINS` extraction

Pure refactor: persona skill-source lists move to a shared constant so lint (Task 2) can cross-check routing visibility without importing `personas.py`.

**Files:**
- Create: `backend/app/services/deep_agent/persona_domains.py`
- Modify: `backend/app/services/deep_agent/personas.py` (the three `skills=[...]` literals)
- Test: `tests/test_persona_domains.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_persona_domains.py`:

```python
"""PERSONA_WORKFLOW_DOMAINS is the single source of truth for persona skill scoping."""
from __future__ import annotations

from app.services.deep_agent.persona_domains import (
    PERSONA_WORKFLOW_DOMAINS,
    workflow_skill_sources,
)
from app.services.deep_agent.personas import board_spec, risk_spec, trader_spec
from app.services.deep_agent.skills_paths import WORKFLOWS_DIR


def test_trader_sources_unchanged() -> None:
    assert workflow_skill_sources("trader") == [
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


def test_risk_manager_sources_unchanged() -> None:
    assert workflow_skill_sources("risk_manager") == [
        "/skills/workflows/positions/",
        "/skills/workflows/risk/",
        "/skills/workflows/hedging/",
        "/skills/workflows/pricing/",
        "/skills/workflows/market-data/",
        "/skills/workflows/portfolios/",
        "/skills/workflows/reporting/",
        "/skills/workflows/snowballs/",
    ]


def test_high_board_sources_unchanged() -> None:
    assert workflow_skill_sources("high_board") == [
        "/skills/workflows/portfolios/",
        "/skills/workflows/reporting/",
    ]


def test_persona_specs_consume_the_constant() -> None:
    assert trader_spec(object(), [])["skills"] == workflow_skill_sources("trader")
    assert risk_spec(object(), [])["skills"] == workflow_skill_sources("risk_manager")
    assert board_spec(object(), [])["skills"] == workflow_skill_sources("high_board")


def test_domain_union_covers_every_workflow_dir() -> None:
    on_disk = {d.name for d in WORKFLOWS_DIR.iterdir() if d.is_dir()}
    declared = {d for domains in PERSONA_WORKFLOW_DOMAINS.values() for d in domains}
    assert declared == on_disk
```

- [ ] **Step 2: Run it to verify failure**

Run: `python -m pytest tests/test_persona_domains.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.deep_agent.persona_domains'`

- [ ] **Step 3: Create `backend/app/services/deep_agent/persona_domains.py`**

```python
"""Per-persona workflow-domain visibility.

Single source of truth consumed by persona specs (skill source lists) and by
skill lint (routing persona-visibility cross-check). Tuple order matters: it
is preserved into each persona's skill source list, which controls catalog
listing order in subagent prompts.
"""
from __future__ import annotations

PERSONA_WORKFLOW_DOMAINS: dict[str, tuple[str, ...]] = {
    "trader": (
        "positions",
        "products",
        "try-solve",
        "pricing",
        "hedging",
        "market-data",
        "portfolios",
        "rfq",
        "snowballs",
    ),
    "risk_manager": (
        "positions",
        "risk",
        "hedging",
        "pricing",
        "market-data",
        "portfolios",
        "reporting",
        "snowballs",
    ),
    "high_board": (
        "portfolios",
        "reporting",
    ),
}


def workflow_skill_sources(persona: str) -> list[str]:
    """Skill source prefixes for one persona, in declared order."""
    return [
        f"/skills/workflows/{domain}/"
        for domain in PERSONA_WORKFLOW_DOMAINS[persona]
    ]


__all__ = ["PERSONA_WORKFLOW_DOMAINS", "workflow_skill_sources"]
```

- [ ] **Step 4: Point `personas.py` at the constant**

In `backend/app/services/deep_agent/personas.py`, add to the imports near the top (next to the other `.`-relative imports):

```python
from .persona_domains import workflow_skill_sources
```

Then replace the three skill list literals:

In `trader_spec` replace the whole `skills=[ ... ]` list (9 entries, `/skills/workflows/positions/` … `/skills/workflows/snowballs/`) with:

```python
        skills=workflow_skill_sources("trader"),
```

In `risk_spec` replace the 8-entry list with:

```python
        skills=workflow_skill_sources("risk_manager"),
```

In `board_spec` replace the 2-entry list with:

```python
        skills=workflow_skill_sources("high_board"),
```

- [ ] **Step 5: Run the new test plus the persona/envelope suites**

Run: `python -m pytest tests/test_persona_domains.py tests/test_personas.py tests/test_envelope_workflow_skills.py tests/test_skills_catalog_v2.py -q`
Expected: all pass except the known `langchain_quickjs` failure in `test_personas.py`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/deep_agent/persona_domains.py backend/app/services/deep_agent/personas.py tests/test_persona_domains.py
git commit -m "refactor(deep-agent): extract PERSONA_WORKFLOW_DOMAINS as shared constant"
```

---

### Task 2: skill_lint — text-input lint and `routing` validation

`lint_skill_text` lets the API validate unsaved payloads without touching disk; `_lint_routing` adds the two new CI-error codes.

**Files:**
- Modify: `backend/app/services/deep_agent/skill_lint.py`
- Test: `tests/test_skill_lint_routing.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_skill_lint_routing.py`:

```python
"""Routing frontmatter validation + text-input lint entry point."""
from __future__ import annotations

from app.services.deep_agent.skill_lint import lint_skill_text

VALID_SKILL = """---
name: fetch-market-data
description: Fetch market snapshots for desk workflows when a user names underlyings.
domain: market-data
workflow_type: read
allowed_envelopes:
  - desk_workflow
may_escalate_to: []
required_context:
  - underlyings
optional_context: []
write_actions: false
confirmation_required: false
success_criteria:
  - snapshots returned
{routing}---

## When to use

- Always, in tests.

## Example

User: fetch.
Assistant: fetched.
"""


def _errors(text: str) -> set[str]:
    return {
        w.code
        for w in lint_skill_text(text, mode="ci")
        if w.severity == "error"
    }


def test_valid_routing_passes() -> None:
    text = VALID_SKILL.format(
        routing='routing:\n  - request: "Fetch current market data"\n    persona: trader\n'
    )
    assert "invalid_routing" not in _errors(text)
    assert "routing_persona_visibility" not in _errors(text)


def test_missing_routing_is_not_flagged() -> None:
    assert "invalid_routing" not in _errors(VALID_SKILL.format(routing=""))


def test_empty_routing_list_is_invalid() -> None:
    assert "invalid_routing" in _errors(VALID_SKILL.format(routing="routing: []\n"))


def test_unknown_persona_is_invalid() -> None:
    text = VALID_SKILL.format(
        routing='routing:\n  - request: "Fetch"\n    persona: ceo\n'
    )
    assert "invalid_routing" in _errors(text)


def test_extra_keys_in_entry_are_invalid() -> None:
    text = VALID_SKILL.format(
        routing='routing:\n  - request: "Fetch"\n    persona: trader\n    weight: 3\n'
    )
    assert "invalid_routing" in _errors(text)


def test_persona_without_domain_visibility_is_flagged() -> None:
    # high_board cannot see market-data.
    text = VALID_SKILL.format(
        routing='routing:\n  - request: "Fetch"\n    persona: high_board\n'
    )
    assert "routing_persona_visibility" in _errors(text)


def test_text_lint_matches_file_lint(tmp_path) -> None:
    from app.services.deep_agent.skill_lint import lint_skill_file

    text = VALID_SKILL.format(routing="")
    path = tmp_path / "fetch-market-data" / "SKILL.md"
    path.parent.mkdir()
    path.write_text(text, encoding="utf-8")
    file_codes = [w.code for w in lint_skill_file(path, mode="ci")]
    text_codes = [w.code for w in lint_skill_text(text, mode="ci")]
    assert file_codes == text_codes
```

- [ ] **Step 2: Run it to verify failure**

Run: `python -m pytest tests/test_skill_lint_routing.py -q`
Expected: FAIL with `ImportError: cannot import name 'lint_skill_text'`

- [ ] **Step 3: Implement in `skill_lint.py`**

(a) Add the import at the top, next to the existing `from app.services.deep_agent.skills_paths import SKILLS_ROOT`:

```python
from app.services.deep_agent.persona_domains import PERSONA_WORKFLOW_DOMAINS
```

(b) Add to the constants block (after `VALID_ENVELOPES`):

```python
VALID_ROUTING_PERSONAS = frozenset(PERSONA_WORKFLOW_DOMAINS)
```

(c) Extend `CI_ERROR_CODES` with the two new codes:

```python
CI_ERROR_CODES = {
    "invalid_frontmatter",
    "missing_frontmatter_field",
    "invalid_workflow_type",
    "invalid_allowed_envelope",
    "invalid_routing",
    "routing_persona_visibility",
    "description_length",
    "description_prefix",
    "missing_example",
    "archaeology_marker",
    "body_length",
}
```

(d) Split `parse_skill_file` so text parsing is reusable. Replace the existing `parse_skill_file` with:

```python
def parse_skill_text(text: str, path: Path) -> ParsedSkill:
    match = FRONTMATTER_PATTERN.match(text)
    if not match:
        if not OPENING_FRONTMATTER_PATTERN.match(text):
            return ParsedSkill(path=Path(path), frontmatter={}, body=text)
        return ParsedSkill(
            path=Path(path),
            frontmatter={},
            body=OPENING_FRONTMATTER_PATTERN.sub("", text, count=1),
            frontmatter_error="missing closing frontmatter fence",
        )

    raw_frontmatter = match.group("frontmatter")
    body = match.group("body")
    try:
        loaded = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError as exc:
        return ParsedSkill(
            path=Path(path),
            frontmatter={},
            body=body,
            frontmatter_error=str(exc),
        )

    if not isinstance(loaded, dict):
        return ParsedSkill(
            path=Path(path),
            frontmatter={},
            body=body,
            frontmatter_error="frontmatter root must be a mapping",
        )

    return ParsedSkill(path=Path(path), frontmatter=loaded, body=body)


def parse_skill_file(path: Path) -> ParsedSkill:
    return parse_skill_text(Path(path).read_text(encoding="utf-8"), Path(path))
```

(e) Extract the shared warning assembly. In `lint_skill_file`, replace the block from `parsed = parse_skill_file(path)` through `warnings.extend(_lint_content(parsed))` with:

```python
    parsed = parse_skill_file(path)
    warnings = _lint_parsed(parsed)
```

and add the two new functions after `lint_skill_file`:

```python
def lint_skill_text(
    text: str,
    *,
    path: Path | str = Path("<unsaved>"),
    mode: SkillLintMode = "warn",
) -> list[SkillLintWarning]:
    """Lint skill content that may not exist on disk (API validation path)."""
    parsed = parse_skill_text(text, Path(path))
    warnings = _lint_parsed(parsed)
    if mode == "warn":
        return warnings
    return _apply_lint_mode(warnings, root=SKILLS_ROOT, mode=mode)


def _lint_parsed(parsed: ParsedSkill) -> list[SkillLintWarning]:
    warnings: list[SkillLintWarning] = []
    if parsed.frontmatter_error:
        warnings.append(
            SkillLintWarning(
                path=parsed.path,
                code="invalid_frontmatter",
                message="Skill frontmatter could not be parsed as a YAML mapping.",
                detail=parsed.frontmatter_error,
            )
        )
    warnings.extend(_lint_frontmatter(parsed))
    warnings.extend(_lint_routing(parsed))
    warnings.extend(_lint_content(parsed))
    return warnings
```

(f) Add `_lint_routing` after `_lint_frontmatter`:

```python
def _lint_routing(parsed: ParsedSkill) -> list[SkillLintWarning]:
    routing = parsed.frontmatter.get("routing")
    if routing is None:
        return []

    def invalid(message: str, detail: str = "") -> SkillLintWarning:
        return SkillLintWarning(
            path=parsed.path,
            code="invalid_routing",
            message=message,
            detail=detail,
        )

    if not isinstance(routing, list) or not routing:
        return [
            invalid(
                "Skill routing must be a non-empty list of "
                "{request, persona} mappings.",
                type(routing).__name__,
            )
        ]

    warnings: list[SkillLintWarning] = []
    domain = parsed.frontmatter.get("domain")
    for index, entry in enumerate(routing):
        if not isinstance(entry, dict) or set(entry) != {"request", "persona"}:
            warnings.append(
                invalid(
                    "Routing entries must be {request, persona} mappings.",
                    f"entry {index}",
                )
            )
            continue
        request = entry["request"]
        persona = entry["persona"]
        if not isinstance(request, str) or not request.strip():
            warnings.append(
                invalid("Routing request must be a non-empty string.", f"entry {index}")
            )
        if persona not in VALID_ROUTING_PERSONAS:
            warnings.append(
                invalid(
                    "Routing persona is not a known persona.",
                    f"entry {index}: {persona!r}",
                )
            )
        elif (
            isinstance(domain, str)
            and domain not in PERSONA_WORKFLOW_DOMAINS[persona]
        ):
            warnings.append(
                SkillLintWarning(
                    path=parsed.path,
                    code="routing_persona_visibility",
                    message=(
                        "Routing persona cannot see this skill's workflow domain."
                    ),
                    detail=f"{persona} cannot see domain {domain!r}",
                )
            )
    return warnings
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_skill_lint_routing.py tests/test_skill_lint.py tests/test_skill_lint_ci.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/skill_lint.py tests/test_skill_lint_routing.py
git commit -m "feat(skill-lint): text-input lint entry point + routing frontmatter validation"
```

---

### Task 3: routing-table generator

**Files:**
- Create: `backend/app/services/deep_agent/routing_table.py`
- Test: `tests/test_routing_table.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_routing_table.py`:

```python
"""Routing-table generation from workflow skill frontmatter."""
from __future__ import annotations

import pytest

from app.services.deep_agent.routing_table import (
    KNOWN_SKILLS_SENTINEL,
    RoutingRow,
    collect_routing_rows,
    inject_known_skills_table,
    render_known_skills_table,
)

SKILL_TEMPLATE = """---
name: {name}
description: A test skill description for routing collection.
domain: {domain}
workflow_type: read
allowed_envelopes:
  - desk_workflow
may_escalate_to: []
required_context: []
optional_context: []
write_actions: false
confirmation_required: false
success_criteria:
  - done
routing:
{routing}---

## Example

User: hi.
Assistant: hi.
"""


@pytest.fixture
def workflows_root(tmp_path):
    root = tmp_path / "workflows"
    a = root / "market-data" / "fetch-market-data"
    a.mkdir(parents=True)
    (a / "SKILL.md").write_text(
        SKILL_TEMPLATE.format(
            name="fetch-market-data",
            domain="market-data",
            routing='  - request: "Fetch current market data"\n    persona: trader\n',
        ),
        encoding="utf-8",
    )
    b = root / "pricing" / "price-portfolio"
    b.mkdir(parents=True)
    (b / "SKILL.md").write_text(
        SKILL_TEMPLATE.format(
            name="price-portfolio",
            domain="pricing",
            routing=(
                '  - request: "Reprice a portfolio"\n    persona: trader\n'
                '  - request: "Reprice for risk"\n    persona: risk_manager\n'
            ),
        ),
        encoding="utf-8",
    )
    # A skill without routing must not appear.
    c = root / "risk" / "read-risk-result"
    c.mkdir(parents=True)
    (c / "SKILL.md").write_text(
        SKILL_TEMPLATE.format(
            name="read-risk-result", domain="risk", routing=""
        ).replace("routing:\n---", "---"),
        encoding="utf-8",
    )
    return root


def test_collect_is_sorted_and_skips_unrouted(workflows_root) -> None:
    rows = collect_routing_rows(workflows_root)
    assert [(r.skill, r.persona) for r in rows] == [
        ("fetch-market-data", "trader"),
        ("price-portfolio", "trader"),
        ("price-portfolio", "risk_manager"),
    ]


def test_render_is_a_markdown_table() -> None:
    rows = [
        RoutingRow(domain="pricing", skill="price-portfolio",
                   request="Reprice a portfolio", persona="trader"),
    ]
    table = render_known_skills_table(rows)
    lines = table.splitlines()
    assert lines[0].startswith("| Request shape")
    assert lines[1].startswith("|---")
    assert "| Reprice a portfolio | trader | price-portfolio |" in table.replace("  ", " ").replace("  ", " ")


def test_inject_replaces_sentinel(workflows_root) -> None:
    prompt = f"intro\n\n{KNOWN_SKILLS_SENTINEL}\n\noutro"
    injected = inject_known_skills_table(prompt, workflows_root)
    assert KNOWN_SKILLS_SENTINEL not in injected
    assert "fetch-market-data" in injected
    assert injected.startswith("intro") and injected.endswith("outro")


def test_inject_without_sentinel_raises(workflows_root) -> None:
    with pytest.raises(ValueError, match="KNOWN_SKILLS_TABLE sentinel"):
        inject_known_skills_table("no sentinel here", workflows_root)
```

- [ ] **Step 2: Run it to verify failure**

Run: `python -m pytest tests/test_routing_table.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.deep_agent.routing_table'`

- [ ] **Step 3: Create `backend/app/services/deep_agent/routing_table.py`**

```python
"""Generate the orchestrator "Known single-persona skills" table.

The table rows come from the optional `routing` frontmatter list on workflow
skills (`[{request, persona}, ...]`). Skills without `routing` are deliberate
sub-workflows reached via persona catalogs and never appear here. The
orchestrator prompt file carries a sentinel comment that `_orchestrator_prompt`
replaces at agent build time, so UI edits become routable after a rebuild.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .skill_lint import iter_skill_files, parse_skill_file
from .skills_paths import WORKFLOWS_DIR

KNOWN_SKILLS_SENTINEL = "<!-- KNOWN_SKILLS_TABLE -->"
_HEADER = ("Request shape", "Persona", "Suggested skill")


@dataclass(frozen=True)
class RoutingRow:
    domain: str
    skill: str
    request: str
    persona: str


def collect_routing_rows(root: Path = WORKFLOWS_DIR) -> list[RoutingRow]:
    """Routing rows from every workflow skill, sorted (domain, skill, request).

    Malformed entries are skipped here — they are CI-blocked by skill lint
    (`invalid_routing`), and the generator must not crash agent builds on a
    file lint already rejects.
    """
    rows: list[RoutingRow] = []
    for path in iter_skill_files(Path(root)):
        parsed = parse_skill_file(path)
        routing = parsed.frontmatter.get("routing")
        if not isinstance(routing, list):
            continue
        skill = parsed.frontmatter.get("name") or path.parent.name
        domain = parsed.frontmatter.get("domain") or path.parent.parent.name
        for entry in routing:
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("request"), str)
                and isinstance(entry.get("persona"), str)
            ):
                rows.append(
                    RoutingRow(
                        domain=str(domain),
                        skill=str(skill),
                        request=entry["request"],
                        persona=entry["persona"],
                    )
                )
    rows.sort(key=lambda row: (row.domain, row.skill, row.request))
    return rows


def render_known_skills_table(rows: Sequence[RoutingRow]) -> str:
    cells = [(row.request, row.persona, row.skill) for row in rows]
    widths = [
        max(len(_HEADER[i]), *(len(cell[i]) for cell in cells))
        if cells
        else len(_HEADER[i])
        for i in range(3)
    ]

    def fmt(values: tuple[str, str, str]) -> str:
        return (
            "| "
            + " | ".join(value.ljust(widths[i]) for i, value in enumerate(values))
            + " |"
        )

    separator = "|" + "|".join("-" * (width + 2) for width in widths) + "|"
    return "\n".join([fmt(_HEADER), separator, *(fmt(cell) for cell in cells)])


def inject_known_skills_table(prompt: str, root: Path = WORKFLOWS_DIR) -> str:
    if KNOWN_SKILLS_SENTINEL not in prompt:
        raise ValueError(
            "orchestrator prompt is missing the KNOWN_SKILLS_TABLE sentinel; "
            "the routing table cannot be injected"
        )
    table = render_known_skills_table(collect_routing_rows(root))
    return prompt.replace(KNOWN_SKILLS_SENTINEL, table)


__all__ = [
    "KNOWN_SKILLS_SENTINEL",
    "RoutingRow",
    "collect_routing_rows",
    "inject_known_skills_table",
    "render_known_skills_table",
]
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_routing_table.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/routing_table.py tests/test_routing_table.py
git commit -m "feat(deep-agent): routing-table generator with sentinel injection"
```

---

### Task 4: backfill `routing` frontmatter + sentinel swap + equivalence pin

This is the migration task. The 20 hand-written table rows move verbatim into 18 skills' frontmatter; the prompt file gets the sentinel; `_orchestrator_prompt()` injects; the equivalence test proves no row was lost or changed.

**Files:**
- Modify: 18 `backend/app/skills/workflows/**/SKILL.md` (listed below)
- Modify: `backend/app/services/deep_agent/prompts/orchestrator.md`
- Modify: `backend/app/services/deep_agent/orchestrator.py` (`_orchestrator_prompt`)
- Modify: `tests/test_routing_table.py` (add pin + drift tests)
- Modify: `tests/test_workflow_skills_phase3.py` (composed-prompt fix)
- Modify: `docs/superpowers/specs/2026-06-06-frontend-skill-management-design.md` (one wording fix)

- [ ] **Step 1: Add the equivalence-pin and drift tests (failing first)**

Append to `tests/test_routing_table.py`:

```python
# --- migration equivalence pin -------------------------------------------
# The exact 20 rows of the hand-written table this migration replaced.
OLD_TABLE_ROWS: set[tuple[str, str, str]] = {
    ("Snowball terms or payoff interpretation", "trader", "snowball-term-interpretation"),
    ("Snowball pricing or valuation drivers", "trader", "snowball-pricing"),
    ("Snowball risk, hedge feasibility, gamma near KI", "risk_manager", "snowball-risk-explain"),
    ("Unexpected position value, Greek, PnL, or contribution", "trader", "position-diagnosis"),
    ("RFQ intake / client request capture", "trader", "intake-request"),
    ("RFQ draft from natural language", "trader", "draft-rfq"),
    ("Construct/validate a quant-ark product from terms", "trader", "build-product"),
    ("Book a product directly into a portfolio from terms", "trader", "book-position"),
    ("Solve/size a portfolio greek hedge (strategies, bands)", "risk_manager", "hedge-portfolio"),
    ("Book stated hedge legs / act on a hedge recommendation", "trader", "hedge-portfolio"),
    ("Create or manage a portfolio (views, rules, sources)", "trader", "portfolio-maintenance"),
    ("RFQ solve / quote a product spec", "trader", "quote-rfq"),
    ("Submit quoted RFQ for approval", "trader", "submit-for-approval"),
    ("Reprice a portfolio (trader lens — pricing freshness)", "trader", "price-portfolio"),
    ("Audit market-data freshness/coverage on a portfolio", "trader", "explain-market-data-drift"),
    ("Fetch current market data", "trader", "fetch-market-data"),
    ("Reprice for risk-input integrity (risk lens)", "risk_manager", "price-portfolio"),
    ("Generate a custom or formal in-thread report artifact", "high_board", "generate-report"),
    ("Generate a risk report end-to-end", "risk_manager", "create-risk-report"),
    ("Review/quote from a persisted report", "high_board", "display-report"),
}


def test_backfilled_catalog_reproduces_old_table_rows() -> None:
    rows = collect_routing_rows()
    triples = {(r.request, r.persona, r.skill) for r in rows}
    assert triples == OLD_TABLE_ROWS
    assert len(rows) == len(OLD_TABLE_ROWS)


def test_orchestrator_prompt_contains_generated_table() -> None:
    from app.services.deep_agent.orchestrator import _orchestrator_prompt

    prompt = _orchestrator_prompt()
    assert KNOWN_SKILLS_SENTINEL not in prompt
    assert render_known_skills_table(collect_routing_rows()) in prompt


def test_every_routing_entry_lands_in_the_rendered_table() -> None:
    table = render_known_skills_table(collect_routing_rows())
    for request, persona, skill in OLD_TABLE_ROWS:
        matching = [
            line
            for line in table.splitlines()
            if request in line and persona in line and skill in line
        ]
        assert matching, f"missing row for {skill}: {request}"
```

Run: `python -m pytest tests/test_routing_table.py -q`
Expected: the three new tests FAIL (no skill has `routing` yet; prompt has no sentinel).

- [ ] **Step 2: Backfill `routing` into the 18 skills**

For each file below, append the `routing:` block as the **last frontmatter key** (immediately before the closing `---`). Exact blocks:

`backend/app/skills/workflows/snowballs/snowball-term-interpretation/SKILL.md`:
```yaml
routing:
  - request: "Snowball terms or payoff interpretation"
    persona: trader
```

`backend/app/skills/workflows/snowballs/snowball-pricing/SKILL.md`:
```yaml
routing:
  - request: "Snowball pricing or valuation drivers"
    persona: trader
```

`backend/app/skills/workflows/snowballs/snowball-risk-explain/SKILL.md`:
```yaml
routing:
  - request: "Snowball risk, hedge feasibility, gamma near KI"
    persona: risk_manager
```

`backend/app/skills/workflows/positions/position-diagnosis/SKILL.md`:
```yaml
routing:
  - request: "Unexpected position value, Greek, PnL, or contribution"
    persona: trader
```

`backend/app/skills/workflows/rfq/intake-request/SKILL.md`:
```yaml
routing:
  - request: "RFQ intake / client request capture"
    persona: trader
```

`backend/app/skills/workflows/rfq/draft-rfq/SKILL.md`:
```yaml
routing:
  - request: "RFQ draft from natural language"
    persona: trader
```

`backend/app/skills/workflows/products/build-product/SKILL.md`:
```yaml
routing:
  - request: "Construct/validate a quant-ark product from terms"
    persona: trader
```

`backend/app/skills/workflows/positions/book-position/SKILL.md`:
```yaml
routing:
  - request: "Book a product directly into a portfolio from terms"
    persona: trader
```

`backend/app/skills/workflows/hedging/hedge-portfolio/SKILL.md`:
```yaml
routing:
  - request: "Solve/size a portfolio greek hedge (strategies, bands)"
    persona: risk_manager
  - request: "Book stated hedge legs / act on a hedge recommendation"
    persona: trader
```

`backend/app/skills/workflows/portfolios/portfolio-maintenance/SKILL.md`:
```yaml
routing:
  - request: "Create or manage a portfolio (views, rules, sources)"
    persona: trader
```

`backend/app/skills/workflows/rfq/quote-rfq/SKILL.md`:
```yaml
routing:
  - request: "RFQ solve / quote a product spec"
    persona: trader
```

`backend/app/skills/workflows/rfq/submit-for-approval/SKILL.md`:
```yaml
routing:
  - request: "Submit quoted RFQ for approval"
    persona: trader
```

`backend/app/skills/workflows/pricing/price-portfolio/SKILL.md` (note the em-dash `—` must be preserved exactly):
```yaml
routing:
  - request: "Reprice a portfolio (trader lens — pricing freshness)"
    persona: trader
  - request: "Reprice for risk-input integrity (risk lens)"
    persona: risk_manager
```

`backend/app/skills/workflows/market-data/explain-market-data-drift/SKILL.md`:
```yaml
routing:
  - request: "Audit market-data freshness/coverage on a portfolio"
    persona: trader
```

`backend/app/skills/workflows/market-data/fetch-market-data/SKILL.md`:
```yaml
routing:
  - request: "Fetch current market data"
    persona: trader
```

`backend/app/skills/workflows/reporting/generate-report/SKILL.md`:
```yaml
routing:
  - request: "Generate a custom or formal in-thread report artifact"
    persona: high_board
```

`backend/app/skills/workflows/risk/create-risk-report/SKILL.md`:
```yaml
routing:
  - request: "Generate a risk report end-to-end"
    persona: risk_manager
```

`backend/app/skills/workflows/reporting/display-report/SKILL.md`:
```yaml
routing:
  - request: "Review/quote from a persisted report"
    persona: high_board
```

- [ ] **Step 3: Swap the table for the sentinel in `prompts/orchestrator.md`**

In `backend/app/services/deep_agent/prompts/orchestrator.md`, under the `### Known single-persona skills` heading, delete the entire markdown table (the `| Request shape ... |` header row, the separator row, and all 20 data rows — currently lines 112–133) and replace with exactly:

```
<!-- KNOWN_SKILLS_TABLE -->
```

The heading line `### Known single-persona skills` and surrounding prose stay untouched.

- [ ] **Step 4: Inject in `_orchestrator_prompt()`**

In `backend/app/services/deep_agent/orchestrator.py`, replace the current function:

```python
def _orchestrator_prompt() -> str:
    base = (_PROMPTS_DIR / "orchestrator.md").read_text(encoding="utf-8").rstrip()
    pickable_options = load_policy_fragments(("reply-options-policy",))
    return base + "\n\n" + pickable_options
```

with:

```python
def _orchestrator_prompt() -> str:
    from .routing_table import inject_known_skills_table

    base = (_PROMPTS_DIR / "orchestrator.md").read_text(encoding="utf-8").rstrip()
    base = inject_known_skills_table(base)
    pickable_options = load_policy_fragments(("reply-options-policy",))
    return base + "\n\n" + pickable_options
```

- [ ] **Step 5: Fix `tests/test_workflow_skills_phase3.py` to assert against the composed prompt**

`test_runtime_prompt_routing_names_migrated_workflows` currently reads the raw `orchestrator.md`, whose table is now a sentinel. The runtime artifact is the composed prompt. Add the import at the top of the file:

```python
from app.services.deep_agent.orchestrator import _orchestrator_prompt
```

Inside the test, replace:

```python
    combined = "\n".join(path.read_text(encoding="utf-8") for path in prompt_files)
```

with:

```python
    combined = "\n".join(
        [
            _orchestrator_prompt(),
            *(
                path.read_text(encoding="utf-8")
                for path in prompt_files
                if path != ORCHESTRATOR_PROMPT
            ),
        ]
    )
```

and replace the two raw-file orchestrator assertions:

```python
    assert "pricing-parameter-maintenance" in ORCHESTRATOR_PROMPT.read_text(
        encoding="utf-8"
    )
```

with:

```python
    assert "pricing-parameter-maintenance" in _orchestrator_prompt()
```

(The `risk_manager.md` assertion stays raw — that file is unchanged.)

- [ ] **Step 6: Run the migration gate**

Run:
```bash
python -m pytest tests/test_routing_table.py tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py tests/test_reference_docs.py tests/test_routing_contracts_phase3.py tests/test_skill_lint.py tests/test_skill_lint_ci.py tests/test_skill_lint_routing.py tests/test_skills_loader.py tests/test_personas.py tests/test_envelope_workflow_skills.py tests/test_skill_rewrite_regression.py tests/test_skills_phase3_layout.py tests/test_skills_read_smoke_v2.py -q
```
Expected: all pass (except the known `langchain_quickjs` failure).

Contingency: if another test fails because it asserted a **table-only skill name against the raw `orchestrator.md` text**, apply the same fix pattern as Step 5 — assert against `_orchestrator_prompt()` instead of the raw file. Do not re-add names to the static file.

- [ ] **Step 7: Amend the spec's equivalence wording**

In `docs/superpowers/specs/2026-06-06-frontend-skill-management-design.md`, the spec says the generated table reproduces the old one "byte-for-byte". The generator sorts rows by (domain, skill, request), so byte-identity with the hand-curated row order is impossible; the meaningful invariant is row-set equality. Replace both occurrences of:

`reproduces today's hand-written table **byte-for-byte**` → `reproduces today's hand-written table as an exact row set — the same (request, persona, skill) triples, no more, no fewer`

and `reproduces today's hand-written table byte-for-byte;` (Testing section) → `reproduces today's hand-written table as an exact (request, persona, skill) row set;`

- [ ] **Step 8: Commit**

```bash
git add backend/app/skills backend/app/services/deep_agent/prompts/orchestrator.md backend/app/services/deep_agent/orchestrator.py tests/test_routing_table.py tests/test_workflow_skills_phase3.py docs/superpowers/specs/2026-06-06-frontend-skill-management-design.md
git commit -m "feat(deep-agent): data-driven orchestrator routing table from skill frontmatter"
```

---

### Task 5: `AgentService.rebuild_orchestrator()`

**Files:**
- Modify: `backend/app/services/agents.py` (add method to `AgentService`, directly after `rebuild_default_model`)
- Test: `tests/test_agents_rebuild.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_agents_rebuild.py`:

```python
"""rebuild_orchestrator: narrow graph rebuild, no registry/dotenv re-read."""
from __future__ import annotations

import app.services.agents as agents_module
from app.services.agents import AgentService


def _bare_service(model: object | None) -> AgentService:
    service = object.__new__(AgentService)
    service.model = model
    service.tools = ["tool-sentinel"]
    service.checkpointer = "checkpointer-sentinel"
    service.deep_agent = "old-graph"
    service._owned_deep_agent = "old-graph"

    class _Settings:
        agent_code_interpreter_enabled = False

    service.settings = _Settings()
    return service


def test_rebuild_swaps_graph_and_keeps_model_and_checkpointer(monkeypatch) -> None:
    captured: dict = {}

    def fake_build_orchestrator(**kwargs):
        captured.update(kwargs)
        return "new-graph"

    monkeypatch.setattr(agents_module, "build_orchestrator", fake_build_orchestrator)
    service = _bare_service(model="model-sentinel")

    assert service.rebuild_orchestrator() is True
    assert service.deep_agent == "new-graph"
    assert service._owned_deep_agent == "new-graph"
    assert captured["model"] == "model-sentinel"
    assert captured["checkpointer"] == "checkpointer-sentinel"
    assert captured["tools"] == ["tool-sentinel"]


def test_rebuild_is_noop_when_agent_disabled(monkeypatch) -> None:
    def explode(**kwargs):  # pragma: no cover - must not be called
        raise AssertionError("build_orchestrator must not run when model is None")

    monkeypatch.setattr(agents_module, "build_orchestrator", explode)
    service = _bare_service(model=None)

    assert service.rebuild_orchestrator() is False
    assert service.deep_agent == "old-graph"
```

- [ ] **Step 2: Run it to verify failure**

Run: `python -m pytest tests/test_agents_rebuild.py -q`
Expected: FAIL with `AttributeError: 'AgentService' object has no attribute 'rebuild_orchestrator'`

- [ ] **Step 3: Implement**

In `backend/app/services/agents.py`, directly after `rebuild_default_model` (which ends at the `self._owned_deep_agent = self.deep_agent` line), add:

```python
    def rebuild_orchestrator(self) -> bool:
        """Rebuild the deep-agent graph from current on-disk skills/prompts.

        Narrower than `rebuild_default_model`: keeps the existing model and
        checkpointer and does NOT re-read the channel registry or dotenv —
        a skill edit must not fail on unrelated channel problems. In-flight
        streams keep the old graph alive via Python references; requests
        after this call use the new graph.

        Returns False when the agent is disabled (no model configured).
        """
        if self.model is None:
            return False
        self.deep_agent = build_orchestrator(
            model=self.model,
            tools=self.tools,
            checkpointer=self.checkpointer,
            interrupt_on=interrupt_on_config(),
            enable_code_interpreter=self.settings.agent_code_interpreter_enabled,
        )
        self._owned_deep_agent = self.deep_agent
        return True
```

(`build_orchestrator` and `interrupt_on_config` are already imported at module top.)

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_agents_rebuild.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agents.py tests/test_agents_rebuild.py
git commit -m "feat(agents): narrow rebuild_orchestrator for skill hot reload"
```

---

### Task 6: skills router — package, models, read endpoints

**Files:**
- Create: `backend/app/routers/__init__.py`
- Create: `backend/app/routers/skills.py`
- Test: `tests/test_skills_api.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_skills_api.py`:

```python
"""Skills CRUD API tests against a temp skills tree and a stub agent service."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.skills import build_skills_router

VALID_SKILL = """---
name: fetch-market-data
description: Fetch market snapshots for desk workflows when a user names underlyings.
domain: market-data
workflow_type: read
allowed_envelopes:
  - desk_workflow
may_escalate_to: []
required_context:
  - underlyings
optional_context: []
write_actions: false
confirmation_required: false
success_criteria:
  - snapshots returned
routing:
  - request: "Fetch current market data"
    persona: trader
---

## When to use

- Always, in tests.

## Example

User: fetch.
Assistant: fetched.
"""

VALID_META = """---
name: clarification-policy
description: Test policy fragment.
policy_type: runtime_policy
applies_to:
  - trader
---

## Clarification

Ask before guessing.
"""

VALID_REFERENCE = """---
name: conventions
description: Test reference doc.
reference_type: market_data
---

## Conventions

Symbols use exchange suffixes.
"""


class StubAgentService:
    def __init__(self, fail: bool = False) -> None:
        self.rebuild_calls = 0
        self.fail = fail

    def rebuild_orchestrator(self) -> bool:
        self.rebuild_calls += 1
        if self.fail:
            raise RuntimeError("rebuild boom")
        return True


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    skill_dir = root / "workflows" / "market-data" / "fetch-market-data"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(VALID_SKILL, encoding="utf-8")
    (root / "workflows" / "risk").mkdir(parents=True)  # empty domain
    (root / "meta").mkdir()
    (root / "meta" / "clarification-policy.md").write_text(VALID_META, encoding="utf-8")
    ref_dir = root / "references" / "market-data"
    ref_dir.mkdir(parents=True)
    (ref_dir / "conventions.md").write_text(VALID_REFERENCE, encoding="utf-8")
    return root


@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "orchestrator.md").write_text(
        "Route fetch-market-data requests to trader.", encoding="utf-8"
    )
    (prompts / "trader.md").write_text("Trader identity.", encoding="utf-8")
    (prompts / "risk_manager.md").write_text("Risk identity.", encoding="utf-8")
    (prompts / "high_board.md").write_text("Board identity.", encoding="utf-8")
    return prompts


@pytest.fixture
def service() -> StubAgentService:
    return StubAgentService()


@pytest.fixture
def api(skills_root: Path, prompts_dir: Path, service: StubAgentService) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_skills_router(service, skills_root=skills_root, prompts_dir=prompts_dir)
    )
    return TestClient(app)


# --- catalog + read --------------------------------------------------------


def test_catalog_lists_all_tiers_and_domains(api: TestClient) -> None:
    data = api.get("/api/skills/catalog").json()
    assert data["domains"] == ["market-data", "risk"]
    assert [e["name"] for e in data["workflows"]] == ["fetch-market-data"]
    assert [e["name"] for e in data["meta"]] == ["clarification-policy"]
    assert [e["name"] for e in data["references"]] == ["conventions"]
    workflow = data["workflows"][0]
    assert workflow["tier"] == "workflows"
    assert workflow["path"] == "market-data/fetch-market-data/SKILL.md"
    assert workflow["domain"] == "market-data"
    assert workflow["body_tokens"] is not None
    assert all(issue["severity"] != "error" for issue in workflow["lint"])


def test_get_workflow_file_returns_parsed_parts(api: TestClient) -> None:
    data = api.get(
        "/api/skills/workflows/market-data/fetch-market-data/SKILL.md"
    ).json()
    assert data["frontmatter"]["name"] == "fetch-market-data"
    assert data["frontmatter"]["routing"][0]["persona"] == "trader"
    assert data["body"].startswith("## When to use")
    assert data["content"].startswith("---\n")


def test_get_meta_file(api: TestClient) -> None:
    data = api.get("/api/skills/meta/clarification-policy.md").json()
    assert data["tier"] == "meta"
    assert data["content"].startswith("---\n")


def test_get_unknown_file_is_404(api: TestClient) -> None:
    assert api.get("/api/skills/meta/missing.md").status_code == 404


def test_path_traversal_is_rejected(api: TestClient) -> None:
    response = api.get("/api/skills/meta/..%2F..%2Fsecrets.md")
    assert response.status_code == 400


def test_non_markdown_is_rejected(api: TestClient) -> None:
    assert api.get("/api/skills/meta/notes.txt").status_code == 400
```

- [ ] **Step 2: Run it to verify failure**

Run: `python -m pytest tests/test_skills_api.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.routers'`

- [ ] **Step 3: Create the package and router**

Create empty `backend/app/routers/__init__.py` (zero bytes is fine).

Create `backend/app/routers/skills.py`:

```python
"""Skills management API.

File-CRUD over `backend/app/skills/` with server-side validation (reusing
skill_lint / meta / reference validators) and an orchestrator rebuild after
every successful write. Local-dev tool by design: no auth, no concurrency
control — git review of the resulting file diffs is the safety net.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.deep_agent.reference_docs import validate_reference_doc_file
from app.services.deep_agent.skill_lint import (
    count_body_tokens,
    lint_skill_file,
    lint_skill_text,
    parse_skill_text,
)
from app.services.deep_agent.skills_loader import validate_meta_policy_file
from app.services.deep_agent.skills_paths import SKILLS_ROOT

logger = logging.getLogger(__name__)

Tier = Literal["workflows", "references", "meta"]
_TIERS: tuple[Tier, ...] = ("workflows", "references", "meta")
_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "services" / "deep_agent" / "prompts"
_PROMPT_FILES = ("orchestrator.md", "trader.md", "risk_manager.md", "high_board.md")
_KEBAB_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FRONTMATTER_FIELD_ORDER = (
    "name",
    "description",
    "domain",
    "workflow_type",
    "allowed_envelopes",
    "may_escalate_to",
    "required_context",
    "optional_context",
    "write_actions",
    "confirmation_required",
    "success_criteria",
    "routing",
)


class SupportsOrchestratorRebuild(Protocol):
    def rebuild_orchestrator(self) -> bool: ...


# --- API models -------------------------------------------------------------


class SkillLintIssueOut(BaseModel):
    code: str
    message: str
    detail: str = ""
    severity: Literal["warning", "error"]


class SkillFileSummaryOut(BaseModel):
    tier: Tier
    path: str
    name: str
    domain: str | None = None
    frontmatter: dict[str, Any] | None = None
    frontmatter_error: str | None = None
    lint: list[SkillLintIssueOut] = []
    body_tokens: int | None = None


class SkillCatalogOut(BaseModel):
    domains: list[str]
    workflows: list[SkillFileSummaryOut]
    references: list[SkillFileSummaryOut]
    meta: list[SkillFileSummaryOut]


class SkillFileOut(SkillFileSummaryOut):
    content: str
    body: str | None = None


class SkillWritePayload(BaseModel):
    frontmatter: dict[str, Any] | None = None
    body: str | None = None
    content: str | None = None


class WorkflowSkillCreate(BaseModel):
    domain: str
    name: str
    frontmatter: dict[str, Any]
    body: str


class SkillValidatePayload(SkillWritePayload):
    tier: Tier


class SkillValidateOut(BaseModel):
    issues: list[SkillLintIssueOut]
    body_tokens: int | None = None
    blocking: bool


class SkillSaveOut(BaseModel):
    saved: bool
    reloaded: bool
    reload_error: str | None = None
    lint: list[SkillLintIssueOut] = []


class SkillDeleteOut(BaseModel):
    deleted: bool
    reloaded: bool
    reload_error: str | None = None
    warnings: list[str] = []


class SkillReloadOut(BaseModel):
    reloaded: bool
    error: str | None = None


# --- helpers ----------------------------------------------------------------


def serialize_workflow_skill(frontmatter: dict[str, Any], body: str) -> str:
    """Canonical SKILL.md text: ordered frontmatter, stripped body, one EOF newline."""
    ordered: dict[str, Any] = {}
    cleaned = dict(frontmatter)
    routing = cleaned.get("routing")
    if isinstance(routing, list):
        # Canonical key order inside entries; drop empty routing entirely.
        entries = [
            {"request": entry.get("request"), "persona": entry.get("persona")}
            if isinstance(entry, dict)
            else entry
            for entry in routing
        ]
        if entries:
            cleaned["routing"] = entries
        else:
            cleaned.pop("routing", None)
    for key in _FRONTMATTER_FIELD_ORDER:
        if key in cleaned:
            ordered[key] = cleaned[key]
    for key, value in cleaned.items():
        if key not in ordered:
            ordered[key] = value
    yaml_text = yaml.safe_dump(
        ordered,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100_000,
    )
    return f"---\n{yaml_text}---\n\n{body.strip()}\n"


def _issue(warning: Any) -> SkillLintIssueOut:
    return SkillLintIssueOut(
        code=warning.code,
        message=warning.message,
        detail=warning.detail,
        severity=warning.severity,
    )


def _exception_issue(code: str, exc: Exception) -> SkillLintIssueOut:
    return SkillLintIssueOut(code=code, message=str(exc), severity="error")


def _validate_named_file(
    validator: Any, filename: str, content: str, code: str
) -> list[SkillLintIssueOut]:
    """Run a Path-based validator (meta/reference) against unsaved text.

    Both validators require name == filename stem, so the temp file keeps the
    target filename inside a TemporaryDirectory.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        candidate = Path(tmp_dir) / filename
        candidate.write_text(content, encoding="utf-8")
        try:
            validator(candidate)
        except ValueError as exc:
            return [_exception_issue(code, exc)]
    return []


def _atomic_write(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def build_skills_router(
    agent_service: SupportsOrchestratorRebuild,
    *,
    skills_root: Path = SKILLS_ROOT,
    prompts_dir: Path = _PROMPTS_DIR,
) -> APIRouter:
    router = APIRouter(prefix="/api/skills", tags=["skills"])
    skills_root = Path(skills_root)

    def _tier_root(tier: Tier) -> Path:
        return (skills_root / tier).resolve()

    def _resolve(tier: Tier, rel_path: str) -> Path:
        root = _tier_root(tier)
        target = (root / rel_path).resolve()
        if target != root and root not in target.parents:
            raise HTTPException(status_code=400, detail="path escapes the skills tree")
        if target.suffix != ".md":
            raise HTTPException(status_code=400, detail="only .md files are managed")
        return target

    def _domains() -> list[str]:
        workflows = _tier_root("workflows")
        if not workflows.is_dir():
            return []
        return sorted(d.name for d in workflows.iterdir() if d.is_dir())

    def _rebuild() -> tuple[bool, str | None]:
        try:
            return agent_service.rebuild_orchestrator(), None
        except Exception as exc:
            logger.exception("orchestrator rebuild after skill write failed")
            return False, str(exc)

    def _lint_existing(tier: Tier, path: Path) -> list[SkillLintIssueOut]:
        if tier == "workflows":
            return [
                _issue(w)
                for w in lint_skill_file(path, mode="ci", root=skills_root)
            ]
        if tier == "meta":
            try:
                validate_meta_policy_file(path)
            except ValueError as exc:
                return [_exception_issue("invalid_meta_policy", exc)]
            return []
        try:
            validate_reference_doc_file(path)
        except ValueError as exc:
            return [_exception_issue("invalid_reference_doc", exc)]
        return []

    def _summary(tier: Tier, path: Path) -> SkillFileSummaryOut:
        rel = path.relative_to(_tier_root(tier)).as_posix()
        text = path.read_text(encoding="utf-8")
        parsed = parse_skill_text(text, path)
        name = str(parsed.frontmatter.get("name") or path.stem)
        domain = None
        if tier == "workflows":
            domain = str(parsed.frontmatter.get("domain") or path.parent.parent.name)
        return SkillFileSummaryOut(
            tier=tier,
            path=rel,
            name=name,
            domain=domain,
            frontmatter=parsed.frontmatter or None,
            frontmatter_error=parsed.frontmatter_error,
            lint=_lint_existing(tier, path),
            body_tokens=count_body_tokens(parsed.body) if tier == "workflows" else None,
        )

    def _validate_payload(
        tier: Tier, payload: SkillWritePayload, filename: str
    ) -> tuple[str, list[SkillLintIssueOut], int | None]:
        """Returns (file text, ci-mode issues, body token count)."""
        if tier == "workflows":
            if payload.frontmatter is None or payload.body is None:
                raise HTTPException(
                    status_code=422,
                    detail="workflow skills require frontmatter and body",
                )
            text = serialize_workflow_skill(payload.frontmatter, payload.body)
            issues = [_issue(w) for w in lint_skill_text(text, mode="ci")]
            return text, issues, count_body_tokens(payload.body)
        if payload.content is None:
            raise HTTPException(
                status_code=422,
                detail=f"{tier} files are updated as raw content",
            )
        validator = (
            validate_meta_policy_file if tier == "meta" else validate_reference_doc_file
        )
        code = "invalid_meta_policy" if tier == "meta" else "invalid_reference_doc"
        issues = _validate_named_file(validator, filename, payload.content, code)
        return payload.content, issues, None

    # --- read ---------------------------------------------------------------

    @router.get("/catalog", response_model=SkillCatalogOut)
    def catalog() -> SkillCatalogOut:
        workflows = [
            _summary("workflows", p)
            for p in sorted(_tier_root("workflows").glob("*/*/SKILL.md"))
        ]
        references = [
            _summary("references", p)
            for p in sorted(_tier_root("references").rglob("*.md"))
        ]
        meta = [_summary("meta", p) for p in sorted(_tier_root("meta").glob("*.md"))]
        return SkillCatalogOut(
            domains=_domains(),
            workflows=workflows,
            references=references,
            meta=meta,
        )

    @router.post("/validate", response_model=SkillValidateOut)
    def validate(payload: SkillValidatePayload) -> SkillValidateOut:
        filename = "SKILL.md"
        if payload.tier != "workflows" and payload.frontmatter is None:
            # Raw tiers: name==stem is checked on PUT against the real filename;
            # for dry-run validation use a neutral stem from the content's name.
            parsed = parse_skill_text(payload.content or "", Path("<unsaved>"))
            filename = f"{parsed.frontmatter.get('name', 'unsaved')}.md"
        text, issues, body_tokens = _validate_payload(payload.tier, payload, filename)
        del text
        return SkillValidateOut(
            issues=issues,
            body_tokens=body_tokens,
            blocking=any(issue.severity == "error" for issue in issues),
        )

    @router.get("/{tier}/{rel_path:path}", response_model=SkillFileOut)
    def get_file(tier: Tier, rel_path: str) -> SkillFileOut:
        target = _resolve(tier, rel_path)
        if not target.is_file():
            raise HTTPException(status_code=404, detail="skill file not found")
        summary = _summary(tier, target)
        text = target.read_text(encoding="utf-8")
        parsed = parse_skill_text(text, target)
        return SkillFileOut(
            **summary.model_dump(),
            content=text,
            body=parsed.body if parsed.frontmatter else None,
        )

    # --- write --------------------------------------------------------------

    @router.put("/{tier}/{rel_path:path}", response_model=SkillSaveOut)
    def update_file(tier: Tier, rel_path: str, payload: SkillWritePayload) -> SkillSaveOut:
        target = _resolve(tier, rel_path)
        if not target.is_file():
            raise HTTPException(status_code=404, detail="skill file not found")
        if tier == "workflows":
            if target.name != "SKILL.md":
                raise HTTPException(
                    status_code=400, detail="workflow skills live in SKILL.md files"
                )
            declared = (payload.frontmatter or {}).get("name")
            if declared != target.parent.name:
                raise HTTPException(
                    status_code=422,
                    detail=f"frontmatter name must equal {target.parent.name!r}",
                )
        text, issues, _tokens = _validate_payload(tier, payload, target.name)
        if any(issue.severity == "error" for issue in issues):
            raise HTTPException(
                status_code=422, detail=[issue.model_dump() for issue in issues]
            )
        _atomic_write(target, text)
        reloaded, reload_error = _rebuild()
        return SkillSaveOut(
            saved=True, reloaded=reloaded, reload_error=reload_error, lint=issues
        )

    @router.post("/workflows", response_model=SkillSaveOut, status_code=201)
    def create_workflow_skill(payload: WorkflowSkillCreate) -> SkillSaveOut:
        if payload.domain not in _domains():
            raise HTTPException(
                status_code=400,
                detail=(
                    "unknown workflow domain; new domains require persona "
                    "visibility wiring in code review"
                ),
            )
        if not _KEBAB_NAME.match(payload.name):
            raise HTTPException(status_code=400, detail="name must be kebab-case")
        target = _tier_root("workflows") / payload.domain / payload.name / "SKILL.md"
        if target.exists():
            raise HTTPException(status_code=409, detail="skill already exists")
        frontmatter = dict(payload.frontmatter)
        frontmatter["name"] = payload.name
        frontmatter["domain"] = payload.domain
        text = serialize_workflow_skill(frontmatter, payload.body)
        issues = [_issue(w) for w in lint_skill_text(text, mode="ci")]
        if any(issue.severity == "error" for issue in issues):
            raise HTTPException(
                status_code=422, detail=[issue.model_dump() for issue in issues]
            )
        _atomic_write(target, text)
        reloaded, reload_error = _rebuild()
        return SkillSaveOut(
            saved=True, reloaded=reloaded, reload_error=reload_error, lint=issues
        )

    @router.delete("/workflows/{domain}/{name}", response_model=SkillDeleteOut)
    def delete_workflow_skill(domain: str, name: str) -> SkillDeleteOut:
        target = _resolve("workflows", f"{domain}/{name}/SKILL.md")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="skill not found")
        warnings = []
        for prompt_name in _PROMPT_FILES:
            prompt_path = Path(prompts_dir) / prompt_name
            if prompt_path.is_file() and name in prompt_path.read_text(encoding="utf-8"):
                warnings.append(f"`{name}` is still referenced in prompts/{prompt_name}")
        target.unlink()
        try:
            target.parent.rmdir()  # prune the skill dir; domain dirs stay
        except OSError:
            pass
        reloaded, reload_error = _rebuild()
        return SkillDeleteOut(
            deleted=True,
            reloaded=reloaded,
            reload_error=reload_error,
            warnings=warnings,
        )

    @router.post("/reload", response_model=SkillReloadOut)
    def reload() -> SkillReloadOut:
        reloaded, error = _rebuild()
        return SkillReloadOut(reloaded=reloaded, error=error)

    return router


__all__ = ["build_skills_router", "serialize_workflow_skill"]
```

Note: `POST /validate` and `POST /workflows` are declared before the `/{tier}/{rel_path:path}` routes match only GET/PUT, so there is no method/route collision; `GET /catalog` is declared before `GET /{tier}/...` and a bare `catalog` cannot match the two-segment pattern anyway.

Contingency: this repo's FastAPI (Pydantic v2 era) validates `Literal` path params natively. If an older FastAPI ever rejects `tier: Tier` at import time, change the path-param annotations to `tier: str` and add `if tier not in _TIERS: raise HTTPException(status_code=400, detail="unknown tier")` at the top of `_resolve`.

- [ ] **Step 4: Run the read tests**

Run: `python -m pytest tests/test_skills_api.py -q`
Expected: PASS (only read tests exist so far)

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/__init__.py backend/app/routers/skills.py tests/test_skills_api.py
git commit -m "feat(api): skills router with catalog and file read endpoints"
```

---

### Task 7: write path — PUT, validate, rebuild semantics

The router code from Task 6 already contains the write endpoints; this task pins their behavior with tests (TDD here means: these tests are the spec for the code you just wrote — if any fails, fix `skills.py`, not the test).

**Files:**
- Modify: `tests/test_skills_api.py` (append)
- Possibly fix: `backend/app/routers/skills.py`

- [ ] **Step 1: Append the write tests**

Append to `tests/test_skills_api.py`:

```python
# --- validate + PUT ---------------------------------------------------------

WORKFLOW_PUT_URL = "/api/skills/workflows/market-data/fetch-market-data/SKILL.md"


def _valid_frontmatter() -> dict:
    return {
        "name": "fetch-market-data",
        "description": "Fetch market snapshots for desk workflows on demand.",
        "domain": "market-data",
        "workflow_type": "read",
        "allowed_envelopes": ["desk_workflow"],
        "may_escalate_to": [],
        "required_context": ["underlyings"],
        "optional_context": [],
        "write_actions": False,
        "confirmation_required": False,
        "success_criteria": ["snapshots returned"],
        "routing": [{"request": "Fetch current market data", "persona": "trader"}],
    }


VALID_BODY = "## When to use\n\n- Updated.\n\n## Example\n\nUser: go.\nAssistant: done.\n"


def test_put_workflow_validates_writes_and_rebuilds(
    api: TestClient, skills_root: Path, service: StubAgentService
) -> None:
    response = api.put(
        WORKFLOW_PUT_URL,
        json={"frontmatter": _valid_frontmatter(), "body": VALID_BODY},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["saved"] is True and data["reloaded"] is True
    assert service.rebuild_calls == 1
    on_disk = (
        skills_root / "workflows" / "market-data" / "fetch-market-data" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert on_disk.startswith("---\nname: fetch-market-data\n")
    assert "- Updated." in on_disk
    assert on_disk.endswith("Assistant: done.\n")


def test_put_with_lint_error_blocks_and_leaves_disk_untouched(
    api: TestClient, skills_root: Path, service: StubAgentService
) -> None:
    target = (
        skills_root / "workflows" / "market-data" / "fetch-market-data" / "SKILL.md"
    )
    before = target.read_text(encoding="utf-8")
    bad = _valid_frontmatter()
    bad["allowed_envelopes"] = ["mars_rover"]
    response = api.put(WORKFLOW_PUT_URL, json={"frontmatter": bad, "body": VALID_BODY})
    assert response.status_code == 422
    codes = {issue["code"] for issue in response.json()["detail"]}
    assert "invalid_allowed_envelope" in codes
    assert target.read_text(encoding="utf-8") == before
    assert service.rebuild_calls == 0


def test_put_workflow_name_must_match_directory(api: TestClient) -> None:
    renamed = _valid_frontmatter()
    renamed["name"] = "other-name"
    response = api.put(
        WORKFLOW_PUT_URL, json={"frontmatter": renamed, "body": VALID_BODY}
    )
    assert response.status_code == 422


def test_put_meta_raw_content(api: TestClient, skills_root: Path) -> None:
    content = VALID_META.replace("Ask before guessing.", "Ask, always.")
    response = api.put(
        "/api/skills/meta/clarification-policy.md", json={"content": content}
    )
    assert response.status_code == 200
    assert "Ask, always." in (skills_root / "meta" / "clarification-policy.md").read_text(
        encoding="utf-8"
    )


def test_put_meta_invalid_is_blocked(api: TestClient) -> None:
    response = api.put(
        "/api/skills/meta/clarification-policy.md",
        json={"content": "no frontmatter at all"},
    )
    assert response.status_code == 422


def test_put_reference_requires_valid_frontmatter(api: TestClient) -> None:
    response = api.put(
        "/api/skills/references/market-data/conventions.md",
        json={"content": "missing frontmatter"},
    )
    assert response.status_code == 422


def test_validate_never_writes(api: TestClient, skills_root: Path) -> None:
    target = (
        skills_root / "workflows" / "market-data" / "fetch-market-data" / "SKILL.md"
    )
    before = target.read_text(encoding="utf-8")
    response = api.post(
        "/api/skills/validate",
        json={
            "tier": "workflows",
            "frontmatter": _valid_frontmatter(),
            "body": VALID_BODY,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["blocking"] is False
    assert data["body_tokens"] is not None
    assert target.read_text(encoding="utf-8") == before


def test_validate_reports_blocking_errors(api: TestClient) -> None:
    bad = _valid_frontmatter()
    del bad["success_criteria"]
    response = api.post(
        "/api/skills/validate",
        json={"tier": "workflows", "frontmatter": bad, "body": VALID_BODY},
    )
    data = response.json()
    assert data["blocking"] is True
    assert any(i["code"] == "missing_frontmatter_field" for i in data["issues"])


def test_rebuild_failure_still_saves(
    skills_root: Path, prompts_dir: Path
) -> None:
    failing = StubAgentService(fail=True)
    app = FastAPI()
    app.include_router(
        build_skills_router(failing, skills_root=skills_root, prompts_dir=prompts_dir)
    )
    client = TestClient(app)
    response = client.put(
        WORKFLOW_PUT_URL,
        json={"frontmatter": _valid_frontmatter(), "body": VALID_BODY},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["saved"] is True
    assert data["reloaded"] is False
    assert "rebuild boom" in data["reload_error"]
```

- [ ] **Step 2: Run and fix until green**

Run: `python -m pytest tests/test_skills_api.py -q`
Expected: PASS. If a test fails, the bug is in `backend/app/routers/skills.py` — fix it there.

- [ ] **Step 3: Commit**

```bash
git add tests/test_skills_api.py backend/app/routers/skills.py
git commit -m "test(api): pin skills PUT/validate semantics (block-on-error, atomic write, rebuild)"
```

---

### Task 8: create / delete / reload tests

**Files:**
- Modify: `tests/test_skills_api.py` (append)
- Possibly fix: `backend/app/routers/skills.py`

- [ ] **Step 1: Append the CRUD tests**

Append to `tests/test_skills_api.py`:

```python
# --- create / delete / reload ----------------------------------------------


def _create_payload(name: str = "stress-scan", domain: str = "risk") -> dict:
    return {
        "domain": domain,
        "name": name,
        "frontmatter": {
            "name": name,
            "description": "Scan stress results for limit breaches on demand.",
            "domain": domain,
            "workflow_type": "read",
            "allowed_envelopes": ["desk_workflow"],
            "may_escalate_to": [],
            "required_context": [],
            "optional_context": [],
            "write_actions": False,
            "confirmation_required": False,
            "success_criteria": ["breaches listed"],
            "routing": [{"request": "Scan stress breaches", "persona": "risk_manager"}],
        },
        "body": "## When to use\n\n- On demand.\n\n## Example\n\nUser: scan.\nAssistant: scanned.\n",
    }


def test_create_workflow_skill(
    api: TestClient, skills_root: Path, service: StubAgentService
) -> None:
    response = api.post("/api/skills/workflows", json=_create_payload())
    assert response.status_code == 201
    target = skills_root / "workflows" / "risk" / "stress-scan" / "SKILL.md"
    assert target.is_file()
    assert service.rebuild_calls == 1
    text = target.read_text(encoding="utf-8")
    assert text.startswith("---\nname: stress-scan\n")


def test_create_duplicate_is_409(api: TestClient) -> None:
    payload = _create_payload(name="fetch-market-data", domain="market-data")
    assert api.post("/api/skills/workflows", json=payload).status_code == 409


def test_create_unknown_domain_is_400(api: TestClient) -> None:
    assert (
        api.post("/api/skills/workflows", json=_create_payload(domain="astrology"))
        .status_code
        == 400
    )


def test_create_bad_name_is_400(api: TestClient) -> None:
    assert (
        api.post("/api/skills/workflows", json=_create_payload(name="Bad Name"))
        .status_code
        == 400
    )


def test_create_routing_visibility_is_blocked(api: TestClient) -> None:
    payload = _create_payload()
    payload["frontmatter"]["routing"] = [
        {"request": "Scan stress breaches", "persona": "high_board"}
    ]
    response = api.post("/api/skills/workflows", json=payload)
    assert response.status_code == 422
    codes = {issue["code"] for issue in response.json()["detail"]}
    assert "routing_persona_visibility" in codes


def test_delete_workflow_skill_prunes_and_warns(
    api: TestClient, skills_root: Path, service: StubAgentService
) -> None:
    # fetch-market-data is referenced in the stub prompts_dir orchestrator.md.
    response = api.delete("/api/skills/workflows/market-data/fetch-market-data")
    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] is True
    assert any("orchestrator.md" in warning for warning in data["warnings"])
    assert service.rebuild_calls == 1
    assert not (skills_root / "workflows" / "market-data" / "fetch-market-data").exists()
    assert (skills_root / "workflows" / "market-data").is_dir()  # domain dir stays


def test_delete_missing_is_404(api: TestClient) -> None:
    assert api.delete("/api/skills/workflows/risk/missing").status_code == 404


def test_reload_endpoint(api: TestClient, service: StubAgentService) -> None:
    response = api.post("/api/skills/reload")
    assert response.status_code == 200
    assert response.json() == {"reloaded": True, "error": None}
    assert service.rebuild_calls == 1
```

- [ ] **Step 2: Run and fix until green**

Run: `python -m pytest tests/test_skills_api.py -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_skills_api.py backend/app/routers/skills.py
git commit -m "test(api): pin skills create/delete/reload semantics"
```

---

### Task 9: wire the router into the app

**Files:**
- Modify: `backend/app/main.py`
- Modify: `tests/test_skills_api.py` (append app-level smoke test)

- [ ] **Step 1: Append the failing smoke test**

Append to `tests/test_skills_api.py`:

```python
# --- app wiring (READ-ONLY against the real skills tree) --------------------


def test_app_serves_skills_catalog(client) -> None:
    """`client` is the repo conftest fixture for the full app. Never write
    through it — this app instance points at the REAL backend/app/skills."""
    response = client.get("/api/skills/catalog")
    assert response.status_code == 200
    data = response.json()
    assert "market-data" in data["domains"]
    names = {entry["name"] for entry in data["workflows"]}
    assert "fetch-market-data" in names
```

Run: `python -m pytest tests/test_skills_api.py::test_app_serves_skills_catalog -q`
Expected: FAIL with 404 (router not included).

- [ ] **Step 2: Include the router in `create_app`**

In `backend/app/main.py`, add to the relative-import block near the top (alongside the other `from .` imports):

```python
from .routers.skills import build_skills_router
```

Then, just before the `return app` at the end of `create_app` (line ~3445), add:

```python
    app.include_router(build_skills_router(active_agent_service))
```

- [ ] **Step 3: Run**

Run: `python -m pytest tests/test_skills_api.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py tests/test_skills_api.py
git commit -m "feat(api): mount skills router in create_app"
```

---

### Task 10: frontend types + API client

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`

- [ ] **Step 1: Add `'skills'` to the Route union**

In `frontend/src/types.ts`, the `export type Route =` union at the top of the file gains one member (append in the same style as the existing lines):

```ts
  | 'skills'
```

- [ ] **Step 2: Add the skill types**

Append to `frontend/src/types.ts`:

```ts
// --- Skills management -------------------------------------------------

export type SkillTier = 'workflows' | 'references' | 'meta';

export type SkillLintIssue = {
  code: string;
  message: string;
  detail: string;
  severity: 'warning' | 'error';
};

export type SkillPersona = 'trader' | 'risk_manager' | 'high_board';

export type SkillRoutingEntry = { request: string; persona: SkillPersona };

export type SkillFrontmatter = {
  name: string;
  description: string;
  domain: string;
  workflow_type: 'diagnostic' | 'action' | 'read' | 'compound';
  allowed_envelopes: string[];
  may_escalate_to: string[];
  required_context: string[];
  optional_context: string[];
  write_actions: boolean;
  confirmation_required: boolean;
  success_criteria: string[];
  routing?: SkillRoutingEntry[];
};

export type SkillFileSummary = {
  tier: SkillTier;
  path: string;
  name: string;
  domain: string | null;
  frontmatter: Record<string, unknown> | null;
  frontmatter_error: string | null;
  lint: SkillLintIssue[];
  body_tokens: number | null;
};

export type SkillCatalog = {
  domains: string[];
  workflows: SkillFileSummary[];
  references: SkillFileSummary[];
  meta: SkillFileSummary[];
};

export type SkillFile = SkillFileSummary & {
  content: string;
  body: string | null;
};

export type SkillValidateResult = {
  issues: SkillLintIssue[];
  body_tokens: number | null;
  blocking: boolean;
};

export type SkillSaveResult = {
  saved: boolean;
  reloaded: boolean;
  reload_error: string | null;
  lint: SkillLintIssue[];
};

export type SkillDeleteResult = {
  deleted: boolean;
  reloaded: boolean;
  reload_error: string | null;
  warnings: string[];
};

export type SkillReloadResult = { reloaded: boolean; error: string | null };
```

- [ ] **Step 3: Add the client functions**

Append to `frontend/src/api/client.ts` (extend the existing type-only import from `../types` with the new names):

```ts
import type {
  FxRate,
  SkillCatalog,
  SkillDeleteResult,
  SkillFile,
  SkillFrontmatter,
  SkillReloadResult,
  SkillSaveResult,
  SkillTier,
  SkillValidateResult,
} from '../types';
```

(replacing the current `import type { FxRate } from '../types';` line), then append:

```ts
// --- Skills management -------------------------------------------------

const encodeSkillPath = (path: string) =>
  path.split('/').map(encodeURIComponent).join('/');

export const listSkillsCatalog = () => api<SkillCatalog>('/api/skills/catalog');

export const getSkillFile = (tier: SkillTier, path: string) =>
  api<SkillFile>(`/api/skills/${tier}/${encodeSkillPath(path)}`);

export const saveWorkflowSkill = (
  path: string,
  frontmatter: SkillFrontmatter,
  body: string,
) =>
  api<SkillSaveResult>(`/api/skills/workflows/${encodeSkillPath(path)}`, {
    method: 'PUT',
    body: JSON.stringify({ frontmatter, body }),
  });

export const saveRawSkillFile = (tier: SkillTier, path: string, content: string) =>
  api<SkillSaveResult>(`/api/skills/${tier}/${encodeSkillPath(path)}`, {
    method: 'PUT',
    body: JSON.stringify({ content }),
  });

export const createWorkflowSkill = (
  domain: string,
  name: string,
  frontmatter: SkillFrontmatter,
  body: string,
) =>
  api<SkillSaveResult>('/api/skills/workflows', {
    method: 'POST',
    body: JSON.stringify({ domain, name, frontmatter, body }),
  });

export const deleteWorkflowSkill = (domain: string, name: string) =>
  api<SkillDeleteResult>(`/api/skills/workflows/${domain}/${name}`, {
    method: 'DELETE',
  });

export const validateWorkflowSkill = (
  frontmatter: SkillFrontmatter,
  body: string,
) =>
  api<SkillValidateResult>('/api/skills/validate', {
    method: 'POST',
    body: JSON.stringify({ tier: 'workflows', frontmatter, body }),
  });

export const validateRawSkillFile = (tier: SkillTier, content: string) =>
  api<SkillValidateResult>('/api/skills/validate', {
    method: 'POST',
    body: JSON.stringify({ tier, content }),
  });

export const reloadSkills = () =>
  api<SkillReloadResult>('/api/skills/reload', { method: 'POST' });
```

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: clean exit 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/client.ts
git commit -m "feat(frontend): skills API types and client functions"
```

---

### Task 11: `SkillsWorkflowForm.tsx` — the structured editor

**Files:**
- Create: `frontend/src/routes/SkillsWorkflowForm.tsx`
- Test: `frontend/src/routes/SkillsWorkflowForm.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/SkillsWorkflowForm.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SkillsWorkflowForm, type WorkflowDraft } from './SkillsWorkflowForm';

const draft: WorkflowDraft = {
  frontmatter: {
    name: 'fetch-market-data',
    description: 'Fetch market snapshots.',
    domain: 'market-data',
    workflow_type: 'read',
    allowed_envelopes: ['desk_workflow'],
    may_escalate_to: [],
    required_context: ['underlyings'],
    optional_context: [],
    write_actions: false,
    confirmation_required: false,
    success_criteria: ['snapshots returned'],
    routing: [{ request: 'Fetch current market data', persona: 'trader' }],
  },
  body: '## When to use\n\n- Always.',
};

const baseProps = {
  draft,
  domains: ['market-data', 'risk'],
  mode: 'edit' as const,
  issues: [],
  bodyTokens: 42,
  saving: false,
  onChange: vi.fn(),
  onSave: vi.fn(),
};

describe('SkillsWorkflowForm', () => {
  it('renders frontmatter fields and token counter', () => {
    render(<SkillsWorkflowForm {...baseProps} />);
    expect(screen.getByDisplayValue('fetch-market-data')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Fetch market snapshots.')).toBeInTheDocument();
    expect(screen.getByText('42 / 500 tokens')).toBeInTheDocument();
    expect(screen.getByText('underlyings')).toBeInTheDocument();
  });

  it('name is read-only in edit mode, editable in create mode', () => {
    const { rerender } = render(<SkillsWorkflowForm {...baseProps} />);
    expect(screen.getByLabelText('name')).toBeDisabled();
    rerender(<SkillsWorkflowForm {...baseProps} mode="create" />);
    expect(screen.getByLabelText('name')).toBeEnabled();
  });

  it('emits onChange when a routing row is added', async () => {
    const onChange = vi.fn();
    render(<SkillsWorkflowForm {...baseProps} onChange={onChange} />);
    await userEvent.click(screen.getByRole('button', { name: 'Add route' }));
    const next = onChange.mock.calls.at(-1)![0];
    expect(next.frontmatter.routing).toHaveLength(2);
  });

  it('emits onChange when a routing row is removed', async () => {
    const onChange = vi.fn();
    render(<SkillsWorkflowForm {...baseProps} onChange={onChange} />);
    await userEvent.click(screen.getByRole('button', { name: 'Remove route 1' }));
    const next = onChange.mock.calls.at(-1)![0];
    expect(next.frontmatter.routing).toHaveLength(0);
  });

  it('disables save while blocking lint errors exist', () => {
    render(
      <SkillsWorkflowForm
        {...baseProps}
        issues={[{ code: 'body_length', message: 'too long', detail: '600', severity: 'error' }]}
      />,
    );
    expect(screen.getByRole('button', { name: /Save/ })).toBeDisabled();
    expect(screen.getByText(/too long/)).toBeInTheDocument();
  });

  it('shows warnings without disabling save', () => {
    render(
      <SkillsWorkflowForm
        {...baseProps}
        issues={[{ code: 'missing_example', message: 'no example', detail: '', severity: 'warning' }]}
      />,
    );
    expect(screen.getByRole('button', { name: /Save/ })).toBeEnabled();
    expect(screen.getByText(/no example/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it to verify failure**

Run: `cd frontend && npx vitest run src/routes/SkillsWorkflowForm.test.tsx`
Expected: FAIL (module does not exist)

- [ ] **Step 3: Create `frontend/src/routes/SkillsWorkflowForm.tsx`**

```tsx
import { useState } from 'react';
import type {
  SkillFrontmatter,
  SkillLintIssue,
  SkillPersona,
  SkillRoutingEntry,
} from '../types';

export type WorkflowDraft = { frontmatter: SkillFrontmatter; body: string };

const ENVELOPES = ['pet_page', 'pet_diagnostic', 'desk_workflow', 'desk_async'] as const;
const WORKFLOW_TYPES = ['diagnostic', 'action', 'read', 'compound'] as const;
const PERSONAS: SkillPersona[] = ['trader', 'risk_manager', 'high_board'];

type Props = {
  draft: WorkflowDraft;
  domains: string[];
  mode: 'edit' | 'create';
  issues: SkillLintIssue[];
  bodyTokens: number | null;
  saving: boolean;
  onChange: (draft: WorkflowDraft) => void;
  onSave: () => void;
  onDelete?: () => void;
};

export function SkillsWorkflowForm({
  draft, domains, mode, issues, bodyTokens, saving, onChange, onSave, onDelete,
}: Props) {
  const fm = draft.frontmatter;
  const blocking = issues.some((issue) => issue.severity === 'error');
  const set = (patch: Partial<SkillFrontmatter>) =>
    onChange({ ...draft, frontmatter: { ...fm, ...patch } });
  const routing = fm.routing ?? [];

  const toggle = (key: 'allowed_envelopes' | 'may_escalate_to', value: string) => {
    const current = fm[key];
    set({
      [key]: current.includes(value)
        ? current.filter((v) => v !== value)
        : [...current, value],
    } as Partial<SkillFrontmatter>);
  };

  const setRouting = (next: SkillRoutingEntry[]) => set({ routing: next });

  return (
    <form
      className="wl-skills__form"
      onSubmit={(event) => { event.preventDefault(); onSave(); }}
    >
      <div className="wl-skills__grid">
        <label className="wl-skills__field">
          <span className="wl-skills__label">name</span>
          <input
            aria-label="name"
            value={fm.name}
            disabled={mode === 'edit'}
            onChange={(e) => set({ name: e.currentTarget.value })}
          />
        </label>
        <label className="wl-skills__field">
          <span className="wl-skills__label">domain</span>
          <select
            aria-label="domain"
            value={fm.domain}
            disabled={mode === 'edit'}
            onChange={(e) => set({ domain: e.currentTarget.value })}
          >
            {domains.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
        <label className="wl-skills__field">
          <span className="wl-skills__label">workflow_type</span>
          <select
            aria-label="workflow_type"
            value={fm.workflow_type}
            onChange={(e) => set({ workflow_type: e.currentTarget.value as SkillFrontmatter['workflow_type'] })}
          >
            {WORKFLOW_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        <div className="wl-skills__field">
          <span className="wl-skills__label">allowed_envelopes</span>
          <div className="wl-skills__checks">
            {ENVELOPES.map((env) => (
              <label key={env}>
                <input
                  type="checkbox"
                  checked={fm.allowed_envelopes.includes(env)}
                  onChange={() => toggle('allowed_envelopes', env)}
                />
                {env}
              </label>
            ))}
          </div>
        </div>
        <div className="wl-skills__field">
          <span className="wl-skills__label">may_escalate_to</span>
          <div className="wl-skills__checks">
            {ENVELOPES.map((env) => (
              <label key={env}>
                <input
                  type="checkbox"
                  checked={fm.may_escalate_to.includes(env)}
                  onChange={() => toggle('may_escalate_to', env)}
                />
                {env}
              </label>
            ))}
          </div>
        </div>
        <label className="wl-skills__field wl-skills__field--wide">
          <span className="wl-skills__label">description</span>
          <textarea
            aria-label="description"
            rows={2}
            value={fm.description}
            onChange={(e) => set({ description: e.currentTarget.value })}
          />
        </label>
        <TagListInput
          label="required_context"
          values={fm.required_context}
          onChange={(values) => set({ required_context: values })}
        />
        <TagListInput
          label="optional_context"
          values={fm.optional_context}
          onChange={(values) => set({ optional_context: values })}
        />
        <TagListInput
          label="success_criteria"
          values={fm.success_criteria}
          onChange={(values) => set({ success_criteria: values })}
        />
        <div className="wl-skills__field">
          <span className="wl-skills__label">write_actions</span>
          <label>
            <input
              type="checkbox"
              checked={fm.write_actions}
              onChange={(e) => set({ write_actions: e.currentTarget.checked })}
            />
            requires writes
          </label>
        </div>
        <div className="wl-skills__field">
          <span className="wl-skills__label">confirmation_required</span>
          <label>
            <input
              type="checkbox"
              checked={fm.confirmation_required}
              onChange={(e) => set({ confirmation_required: e.currentTarget.checked })}
            />
            HITL confirm
          </label>
        </div>
      </div>

      <div className="wl-skills__routing">
        <span className="wl-skills__label">orchestrator routing (optional)</span>
        {routing.map((entry, index) => (
          <div key={index} className="wl-skills__routing-row">
            <input
              aria-label={`Route request ${index + 1}`}
              value={entry.request}
              placeholder="request shape"
              onChange={(e) => {
                const next = [...routing];
                next[index] = { ...entry, request: e.currentTarget.value };
                setRouting(next);
              }}
            />
            <select
              aria-label={`Route persona ${index + 1}`}
              value={entry.persona}
              onChange={(e) => {
                const next = [...routing];
                next[index] = { ...entry, persona: e.currentTarget.value as SkillPersona };
                setRouting(next);
              }}
            >
              {PERSONAS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
            <button
              type="button"
              aria-label={`Remove route ${index + 1}`}
              onClick={() => setRouting(routing.filter((_, i) => i !== index))}
            >
              ×
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => setRouting([...routing, { request: '', persona: 'trader' }])}
        >
          Add route
        </button>
      </div>

      <label className="wl-skills__field wl-skills__field--wide">
        <span className="wl-skills__label">
          body (markdown) — {bodyTokens != null ? `${bodyTokens} / 500 tokens` : '… / 500 tokens'}
        </span>
        <textarea
          aria-label="body"
          rows={14}
          className="wl-skills__body"
          value={draft.body}
          onChange={(e) => onChange({ ...draft, body: e.currentTarget.value })}
        />
      </label>

      {issues.length > 0 && (
        <ul className="wl-skills__issues">
          {issues.map((issue, index) => (
            <li
              key={`${issue.code}-${index}`}
              className={`wl-skills__issue wl-skills__issue--${issue.severity}`}
            >
              {issue.severity === 'error' ? '✕' : '⚠'} {issue.code}: {issue.message}
              {issue.detail ? ` (${issue.detail})` : ''}
            </li>
          ))}
        </ul>
      )}

      <div className="wl-skills__actions">
        <button type="submit" disabled={saving || blocking}>
          {mode === 'create' ? 'Create & reload agent' : 'Save & reload agent'}
        </button>
        {mode === 'edit' && onDelete && (
          <button type="button" className="wl-skills__danger" onClick={onDelete}>
            Delete skill…
          </button>
        )}
      </div>
    </form>
  );
}

function TagListInput({
  label, values, onChange,
}: { label: string; values: string[]; onChange: (values: string[]) => void }) {
  const [text, setText] = useState('');
  const add = () => {
    const value = text.trim();
    if (!value || values.includes(value)) return;
    onChange([...values, value]);
    setText('');
  };
  return (
    <div className="wl-skills__field">
      <span className="wl-skills__label">{label}</span>
      <div className="wl-skills__tags">
        {values.map((value) => (
          <span key={value} className="wl-skills__tag">
            {value}
            <button
              type="button"
              aria-label={`Remove ${value}`}
              onClick={() => onChange(values.filter((v) => v !== value))}
            >
              ×
            </button>
          </span>
        ))}
        <input
          aria-label={`Add ${label}`}
          value={text}
          placeholder="add…"
          onChange={(e) => setText(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); add(); }
          }}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests + typecheck**

Run: `cd frontend && npx vitest run src/routes/SkillsWorkflowForm.test.tsx && npx tsc --noEmit`
Expected: PASS, clean tsc.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/SkillsWorkflowForm.tsx frontend/src/routes/SkillsWorkflowForm.test.tsx
git commit -m "feat(frontend): structured workflow-skill editor form"
```

---

### Task 12: `Skills.tsx` — tree, raw editor, page shell

**Files:**
- Create: `frontend/src/routes/Skills.tsx`
- Create: `frontend/src/routes/Skills.css`
- Test: `frontend/src/routes/Skills.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/Skills.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Skills } from './Skills';
import type { SkillCatalog, SkillFile } from '../types';

const catalog: SkillCatalog = {
  domains: ['market-data', 'risk'],
  workflows: [
    {
      tier: 'workflows',
      path: 'market-data/fetch-market-data/SKILL.md',
      name: 'fetch-market-data',
      domain: 'market-data',
      frontmatter: { name: 'fetch-market-data' },
      frontmatter_error: null,
      lint: [],
      body_tokens: 42,
    },
    {
      tier: 'workflows',
      path: 'risk/run-risk/SKILL.md',
      name: 'run-risk',
      domain: 'risk',
      frontmatter: { name: 'run-risk' },
      frontmatter_error: null,
      lint: [{ code: 'missing_example', message: 'm', detail: '', severity: 'warning' }],
      body_tokens: 10,
    },
  ],
  references: [],
  meta: [
    {
      tier: 'meta',
      path: 'clarification-policy.md',
      name: 'clarification-policy',
      domain: null,
      frontmatter: { name: 'clarification-policy' },
      frontmatter_error: null,
      lint: [],
      body_tokens: null,
    },
  ],
};

const metaFile: SkillFile = {
  ...catalog.meta[0],
  content: '---\nname: clarification-policy\n---\n\n## Clarification\n\nAsk.',
  body: '## Clarification\n\nAsk.',
};

const baseProps = {
  catalog,
  loading: false,
  selected: null,
  file: null,
  validation: null,
  saving: false,
  reloadStatus: 'agent in sync',
  saveStatus: null,
  onSelect: vi.fn(),
  onDraftChange: vi.fn(),
  onSaveWorkflow: vi.fn(),
  onSaveRaw: vi.fn(),
  onCreate: vi.fn(),
  onDelete: vi.fn(),
  onReload: vi.fn(),
};

describe('Skills', () => {
  it('renders tree groups with lint badges', () => {
    render(<Skills {...baseProps} />);
    expect(screen.getByText('WORKFLOWS')).toBeInTheDocument();
    expect(screen.getByText('META')).toBeInTheDocument();
    expect(screen.getByText('fetch-market-data')).toBeInTheDocument();
    expect(screen.getByText('run-risk')).toBeInTheDocument();
    // run-risk carries a warning badge
    expect(screen.getByTitle('1 lint warning(s)')).toBeInTheDocument();
  });

  it('filters the tree', async () => {
    render(<Skills {...baseProps} />);
    await userEvent.type(screen.getByPlaceholderText('Filter skills…'), 'fetch');
    expect(screen.getByText('fetch-market-data')).toBeInTheDocument();
    expect(screen.queryByText('run-risk')).not.toBeInTheDocument();
  });

  it('fires onSelect when an entry is clicked', async () => {
    const onSelect = vi.fn();
    render(<Skills {...baseProps} onSelect={onSelect} />);
    await userEvent.click(screen.getByText('fetch-market-data'));
    expect(onSelect).toHaveBeenCalledWith({
      tier: 'workflows',
      path: 'market-data/fetch-market-data/SKILL.md',
    });
  });

  it('renders a raw editor for meta files and saves through onSaveRaw', async () => {
    const onSaveRaw = vi.fn();
    render(
      <Skills
        {...baseProps}
        selected={{ tier: 'meta', path: 'clarification-policy.md' }}
        file={metaFile}
        onSaveRaw={onSaveRaw}
      />,
    );
    const editor = screen.getByLabelText('raw content');
    expect(editor).toHaveValue(metaFile.content);
    await userEvent.click(screen.getByRole('button', { name: /Save/ }));
    expect(onSaveRaw).toHaveBeenCalled();
  });

  it('opens a blank create form from the New button', async () => {
    render(<Skills {...baseProps} />);
    await userEvent.click(screen.getByRole('button', { name: '+ New' }));
    expect(screen.getByRole('button', { name: 'Create & reload agent' })).toBeInTheDocument();
  });

  it('reload button fires onReload', async () => {
    const onReload = vi.fn();
    render(<Skills {...baseProps} onReload={onReload} />);
    await userEvent.click(screen.getByRole('button', { name: '⟳ Reload skills' }));
    expect(onReload).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run it to verify failure**

Run: `cd frontend && npx vitest run src/routes/Skills.test.tsx`
Expected: FAIL (module does not exist)

- [ ] **Step 3: Create `frontend/src/routes/Skills.tsx`**

```tsx
import { useMemo, useState } from 'react';
import type {
  PageContext,
  PageContextReporter,
  SkillCatalog,
  SkillFile,
  SkillFileSummary,
  SkillFrontmatter,
  SkillTier,
  SkillValidateResult,
} from '../types';
import { PageHeader } from '../components/PageHeader';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import { SkillsWorkflowForm, type WorkflowDraft } from './SkillsWorkflowForm';
import './Skills.css';

export type SkillSelection = { tier: SkillTier; path: string };

type Props = {
  catalog: SkillCatalog | null;
  loading: boolean;
  selected: SkillSelection | null;
  file: SkillFile | null;
  validation: SkillValidateResult | null;
  saving: boolean;
  reloadStatus: string;
  saveStatus: string | null;
  onSelect: (selection: SkillSelection) => void;
  onDraftChange: (draft: WorkflowDraft) => void;
  onSaveWorkflow: (draft: WorkflowDraft) => void;
  onSaveRaw: (selection: SkillSelection, content: string) => void;
  onCreate: (draft: WorkflowDraft) => void;
  onDelete: (selection: SkillSelection, name: string) => void;
  onReload: () => void;
  onPageContextChange?: PageContextReporter;
};

const CREATE_TEMPLATE_BODY =
  '## When to use\n\n- \n\n## Procedure\n\n1. \n\n## Example\n\nUser: \nAssistant: \n';

function blankDraft(domain: string): WorkflowDraft {
  return {
    frontmatter: {
      name: '',
      description: '',
      domain,
      workflow_type: 'read',
      allowed_envelopes: ['desk_workflow'],
      may_escalate_to: [],
      required_context: [],
      optional_context: [],
      write_actions: false,
      confirmation_required: false,
      success_criteria: [],
      routing: [],
    },
    body: CREATE_TEMPLATE_BODY,
  };
}

function badge(entry: SkillFileSummary) {
  const errors = entry.lint.filter((issue) => issue.severity === 'error').length;
  const warnings = entry.lint.length - errors;
  if (errors) return <span className="wl-skills__badge wl-skills__badge--error" title={`${errors} lint error(s)`}>✕</span>;
  if (warnings) return <span className="wl-skills__badge wl-skills__badge--warn" title={`${warnings} lint warning(s)`}>⚠</span>;
  return <span className="wl-skills__badge wl-skills__badge--ok" title="lint clean">✓</span>;
}

export function Skills({
  catalog, loading, selected, file, validation, saving, reloadStatus, saveStatus,
  onSelect, onDraftChange, onSaveWorkflow, onSaveRaw, onCreate, onDelete, onReload,
  onPageContextChange,
}: Props) {
  const [filter, setFilter] = useState('');
  const [creating, setCreating] = useState(false);
  // Draft is keyed by file path: when a different file is loaded, reset it.
  const [draft, setDraft] = useState<WorkflowDraft | null>(null);
  const [draftPath, setDraftPath] = useState<string | null>(null);
  const [rawText, setRawText] = useState<string | null>(null);

  const pageContext = useMemo<PageContext>(() => ({
    route: 'skills',
    title: 'Skills',
    path: '/',
    entity_ids: { skill_path: selected?.path ?? null },
    snapshot: {
      workflow_count: catalog?.workflows.length ?? 0,
      selected: selected?.path ?? null,
    },
    chips: ['Skills', `${catalog?.workflows.length ?? 0} workflows`],
  }), [catalog, selected]);
  usePageContextReporter(pageContext, onPageContextChange);

  const isWorkflowFile = file != null && file.tier === 'workflows' && file.frontmatter != null;
  // Derived-state-from-props pattern: when a different file arrives, reset the
  // local draft (and leave create mode — e.g. after a successful create).
  if (file && file.path !== draftPath) {
    setDraftPath(file.path);
    setRawText(file.content);
    setCreating(false);
    setDraft(
      file.tier === 'workflows' && file.frontmatter
        ? { frontmatter: file.frontmatter as unknown as SkillFrontmatter, body: file.body ?? '' }
        : null,
    );
  }

  const matches = (entry: SkillFileSummary) =>
    !filter || entry.name.toLowerCase().includes(filter.toLowerCase());

  const workflowsByDomain = useMemo(() => {
    const grouped = new Map<string, SkillFileSummary[]>();
    for (const entry of catalog?.workflows ?? []) {
      if (!matches(entry)) continue;
      const domain = entry.domain ?? 'unknown';
      grouped.set(domain, [...(grouped.get(domain) ?? []), entry]);
    }
    return [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog, filter]);

  const changeDraft = (next: WorkflowDraft) => {
    setDraft(next);
    onDraftChange(next);
  };

  const startCreate = () => {
    setCreating(true);
    const initial = blankDraft(catalog?.domains[0] ?? '');
    setDraft(initial);
    setDraftPath(null);
    onDraftChange(initial);
  };

  const renderEntry = (entry: SkillFileSummary) => (
    <button
      key={`${entry.tier}:${entry.path}`}
      type="button"
      className={`wl-skills__entry${selected?.path === entry.path ? ' wl-skills__entry--active' : ''}`}
      onClick={() => { setCreating(false); onSelect({ tier: entry.tier, path: entry.path }); }}
    >
      <span>{entry.name}</span>
      {badge(entry)}
    </button>
  );

  return (
    <div className="wl-skills">
      <PageHeader
        title="Skills"
        chips={pageContext.chips}
        action={(
          <div className="wl-skills__toolbar">
            <button type="button" onClick={onReload}>⟳ Reload skills</button>
            <span className="wl-skills__status">{reloadStatus}</span>
          </div>
        )}
      />
      <div className="wl-skills__layout">
        <aside className="wl-skills__tree">
          <input
            placeholder="Filter skills…"
            value={filter}
            onChange={(event) => setFilter(event.currentTarget.value)}
          />
          <div className="wl-skills__group">
            <div className="wl-skills__group-head">
              <span>WORKFLOWS</span>
              <button type="button" onClick={startCreate}>+ New</button>
            </div>
            {workflowsByDomain.map(([domain, entries]) => (
              <div key={domain} className="wl-skills__domain">
                <span className="wl-skills__domain-name">{domain}</span>
                {entries.map(renderEntry)}
              </div>
            ))}
          </div>
          <div className="wl-skills__group">
            <div className="wl-skills__group-head"><span>REFERENCES</span></div>
            {(catalog?.references ?? []).filter(matches).map(renderEntry)}
          </div>
          <div className="wl-skills__group">
            <div className="wl-skills__group-head"><span>META</span></div>
            {(catalog?.meta ?? []).filter(matches).map(renderEntry)}
          </div>
        </aside>
        <section className="wl-skills__editor">
          {loading && <p>Loading catalog…</p>}
          {saveStatus && <p className="wl-skills__save-status">{saveStatus}</p>}
          {creating && draft && (
            <SkillsWorkflowForm
              draft={draft}
              domains={catalog?.domains ?? []}
              mode="create"
              issues={validation?.issues ?? []}
              bodyTokens={validation?.body_tokens ?? null}
              saving={saving}
              onChange={changeDraft}
              onSave={() => onCreate(draft)}
            />
          )}
          {!creating && isWorkflowFile && draft && (
            <SkillsWorkflowForm
              draft={draft}
              domains={catalog?.domains ?? []}
              mode="edit"
              issues={validation?.issues ?? file!.lint}
              bodyTokens={validation?.body_tokens ?? file!.body_tokens}
              saving={saving}
              onChange={changeDraft}
              onSave={() => onSaveWorkflow(draft)}
              onDelete={() => onDelete(selected!, file!.name)}
            />
          )}
          {!creating && file && !isWorkflowFile && rawText != null && (
            <div className="wl-skills__raw">
              <span className="wl-skills__label">
                {file.tier}/{file.path} (raw editor — schema differs from workflows)
              </span>
              <textarea
                aria-label="raw content"
                rows={24}
                value={rawText}
                onChange={(event) => setRawText(event.currentTarget.value)}
              />
              {(validation?.issues ?? file.lint).map((issue, index) => (
                <p key={index} className={`wl-skills__issue wl-skills__issue--${issue.severity}`}>
                  {issue.severity === 'error' ? '✕' : '⚠'} {issue.code}: {issue.message}
                </p>
              ))}
              <button
                type="button"
                disabled={saving}
                onClick={() => onSaveRaw(selected!, rawText)}
              >
                Save & reload agent
              </button>
            </div>
          )}
          {!creating && !file && !loading && (
            <p className="wl-skills__placeholder">Select a skill to view or edit it.</p>
          )}
        </section>
      </div>
    </div>
  );
}
```

Note: `PageHeader`'s real API is `{ title: string; chips: string[]; action?: ReactNode }` (verified) — the call above matches it. Do not modify `PageHeader` itself.

- [ ] **Step 4: Create `frontend/src/routes/Skills.css`**

```css
.wl-skills { padding-top: var(--gap-2); }
.wl-skills__toolbar { display: flex; align-items: center; gap: var(--gap-2); }
.wl-skills__status { font-size: 12px; opacity: 0.7; }
.wl-skills__layout { display: flex; gap: var(--gap-3); align-items: flex-start; }
.wl-skills__tree { width: 260px; flex: none; display: flex; flex-direction: column; gap: var(--gap-2); }
.wl-skills__tree > input { width: 100%; }
.wl-skills__group-head { display: flex; justify-content: space-between; align-items: center; font-weight: 600; font-size: 12px; margin-top: var(--gap-2); }
.wl-skills__domain { display: flex; flex-direction: column; }
.wl-skills__domain-name { font-size: 12px; opacity: 0.7; padding: 2px 0; }
.wl-skills__entry { display: flex; justify-content: space-between; align-items: center; padding: 2px 6px; border: none; background: none; cursor: pointer; text-align: left; border-radius: 4px; }
.wl-skills__entry--active { background: var(--surface-2, rgba(100, 140, 255, 0.15)); }
.wl-skills__badge--error { color: #c0392b; }
.wl-skills__badge--warn { color: #b8860b; }
.wl-skills__badge--ok { color: #2e7d32; }
.wl-skills__editor { flex: 1; min-width: 0; }
.wl-skills__form, .wl-skills__raw { display: flex; flex-direction: column; gap: var(--gap-2); }
.wl-skills__grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--gap-2); }
.wl-skills__field { display: flex; flex-direction: column; gap: 4px; }
.wl-skills__field--wide { grid-column: 1 / 3; }
.wl-skills__label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; opacity: 0.7; }
.wl-skills__checks { display: flex; flex-wrap: wrap; gap: var(--gap-2); font-size: 12px; }
.wl-skills__tags { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.wl-skills__tag { display: inline-flex; align-items: center; gap: 2px; padding: 1px 6px; border-radius: 10px; background: var(--surface-2, rgba(127, 127, 127, 0.15)); font-size: 12px; }
.wl-skills__routing { display: flex; flex-direction: column; gap: 4px; border-top: 1px dashed var(--border, rgba(127, 127, 127, 0.4)); padding-top: var(--gap-2); }
.wl-skills__routing-row { display: flex; gap: var(--gap-2); }
.wl-skills__routing-row > input { flex: 1; }
.wl-skills__body { font-family: var(--font-mono, monospace); font-size: 12px; }
.wl-skills__issues { margin: 0; padding-left: 0; list-style: none; }
.wl-skills__issue--error { color: #c0392b; }
.wl-skills__issue--warning { color: #b8860b; }
.wl-skills__actions { display: flex; gap: var(--gap-2); }
.wl-skills__danger { color: #c0392b; }
.wl-skills__save-status { font-size: 12px; }
.wl-skills__placeholder { opacity: 0.6; }
```

- [ ] **Step 5: Run tests + typecheck**

Run: `cd frontend && npx vitest run src/routes/Skills.test.tsx src/routes/SkillsWorkflowForm.test.tsx && npx tsc --noEmit`
Expected: PASS, clean tsc.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/Skills.tsx frontend/src/routes/Skills.css frontend/src/routes/Skills.test.tsx
git commit -m "feat(frontend): Skills page shell with catalog tree and raw editor"
```

---

### Task 13: `Skills.live.tsx` + app wiring

**Files:**
- Create: `frontend/src/routes/Skills.live.tsx`
- Modify: `frontend/src/main.tsx`
- Test: `frontend/src/routes/Skills.live.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/Skills.live.test.tsx`:

```tsx
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SkillsLive } from './Skills.live';

const catalog = {
  domains: ['market-data'],
  workflows: [
    {
      tier: 'workflows',
      path: 'market-data/fetch-market-data/SKILL.md',
      name: 'fetch-market-data',
      domain: 'market-data',
      frontmatter: { name: 'fetch-market-data' },
      frontmatter_error: null,
      lint: [],
      body_tokens: 42,
    },
  ],
  references: [],
  meta: [],
};

const skillFile = {
  ...catalog.workflows[0],
  frontmatter: {
    name: 'fetch-market-data',
    description: 'Fetch market snapshots.',
    domain: 'market-data',
    workflow_type: 'read',
    allowed_envelopes: ['desk_workflow'],
    may_escalate_to: [],
    required_context: [],
    optional_context: [],
    write_actions: false,
    confirmation_required: false,
    success_criteria: ['done'],
    routing: [],
  },
  content: '---\nname: fetch-market-data\n---\n\n## Body',
  body: '## Body',
};

function stubFetch() {
  const calls: Array<{ url: string; method: string }> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    const json = (data: unknown) =>
      new Response(JSON.stringify(data), { status: 200, headers: { 'content-type': 'application/json' } });
    if (url === '/api/skills/catalog') return json(catalog);
    if (url.endsWith('/SKILL.md') && method === 'GET') return json(skillFile);
    if (url.endsWith('/SKILL.md') && method === 'PUT') {
      return json({ saved: true, reloaded: true, reload_error: null, lint: [] });
    }
    if (url === '/api/skills/validate') {
      return json({ issues: [], body_tokens: 42, blocking: false });
    }
    if (url === '/api/skills/reload') return json({ reloaded: true, error: null });
    throw new Error(`unexpected fetch: ${method} ${url}`);
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  return { fetchMock, calls };
}

afterEach(() => { vi.restoreAllMocks(); });

describe('SkillsLive', () => {
  it('loads the catalog and shows the tree', async () => {
    stubFetch();
    render(<SkillsLive />);
    expect(await screen.findByText('fetch-market-data')).toBeInTheDocument();
  });

  it('selecting a skill loads the file and saving PUTs it', async () => {
    const { calls } = stubFetch();
    render(<SkillsLive />);
    await userEvent.click(await screen.findByText('fetch-market-data'));
    await screen.findByDisplayValue('Fetch market snapshots.');
    await userEvent.click(screen.getByRole('button', { name: 'Save & reload agent' }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'PUT' && c.url.includes('fetch-market-data'))).toBe(true);
    });
    expect(await screen.findByText(/Saved · agent reloaded/)).toBeInTheDocument();
  });

  it('reload button posts to the reload endpoint', async () => {
    const { calls } = stubFetch();
    render(<SkillsLive />);
    await screen.findByText('fetch-market-data');
    await userEvent.click(screen.getByRole('button', { name: '⟳ Reload skills' }));
    await waitFor(() => {
      expect(calls.some((c) => c.url === '/api/skills/reload' && c.method === 'POST')).toBe(true);
    });
  });
});
```

- [ ] **Step 2: Run it to verify failure**

Run: `cd frontend && npx vitest run src/routes/Skills.live.test.tsx`
Expected: FAIL (module does not exist)

- [ ] **Step 3: Create `frontend/src/routes/Skills.live.tsx`**

```tsx
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  createWorkflowSkill,
  deleteWorkflowSkill,
  getSkillFile,
  listSkillsCatalog,
  reloadSkills,
  saveRawSkillFile,
  saveWorkflowSkill,
  validateRawSkillFile,
  validateWorkflowSkill,
} from '../api/client';
import type {
  PageContextReporter,
  SkillCatalog,
  SkillFile,
  SkillValidateResult,
} from '../types';
import { Skills, type SkillSelection } from './Skills';
import type { WorkflowDraft } from './SkillsWorkflowForm';

type Props = { onPageContextChange?: PageContextReporter };

const VALIDATE_DEBOUNCE_MS = 500;

export function SkillsLive({ onPageContextChange }: Props) {
  const [catalog, setCatalog] = useState<SkillCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<SkillSelection | null>(null);
  const [file, setFile] = useState<SkillFile | null>(null);
  const [validation, setValidation] = useState<SkillValidateResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);
  const [reloadStatus, setReloadStatus] = useState('agent in sync');
  const debounceRef = useRef<number | null>(null);

  const refreshCatalog = useCallback(async () => {
    try {
      setCatalog(await listSkillsCatalog());
    } catch (error) {
      setSaveStatus(`Could not load catalog: ${String(error)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refreshCatalog(); }, [refreshCatalog]);

  const handleSelect = useCallback(async (selection: SkillSelection) => {
    setSelected(selection);
    setValidation(null);
    setSaveStatus(null);
    try {
      setFile(await getSkillFile(selection.tier, selection.path));
    } catch (error) {
      setSaveStatus(`Could not load file: ${String(error)}`);
      setFile(null);
    }
  }, []);

  const handleDraftChange = useCallback((draft: WorkflowDraft) => {
    if (debounceRef.current != null) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      void validateWorkflowSkill(draft.frontmatter, draft.body)
        .then(setValidation)
        .catch(() => setValidation(null));
    }, VALIDATE_DEBOUNCE_MS);
  }, []);

  const finishWrite = useCallback(
    (result: { reloaded: boolean; reload_error: string | null }, verb: string) => {
      if (result.reloaded) {
        setSaveStatus(`${verb} · agent reloaded`);
        setReloadStatus('agent in sync');
      } else {
        setSaveStatus(
          `${verb} — agent still on old prompt: ${result.reload_error ?? 'reload failed'}. Fix and press Reload.`,
        );
        setReloadStatus('agent stale');
      }
      void refreshCatalog();
    },
    [refreshCatalog],
  );

  const handleSaveWorkflow = useCallback(async (draft: WorkflowDraft) => {
    if (!selected) return;
    setSaving(true);
    try {
      finishWrite(await saveWorkflowSkill(selected.path, draft.frontmatter, draft.body), 'Saved');
      await handleSelect(selected);
    } catch (error) {
      setSaveStatus(`Save failed: ${String(error)}`);
    } finally {
      setSaving(false);
    }
  }, [selected, finishWrite, handleSelect]);

  const handleSaveRaw = useCallback(async (selection: SkillSelection, content: string) => {
    setSaving(true);
    try {
      finishWrite(await saveRawSkillFile(selection.tier, selection.path, content), 'Saved');
    } catch (error) {
      try {
        setValidation(await validateRawSkillFile(selection.tier, content));
      } catch { /* validation refresh is best-effort */ }
      setSaveStatus(`Save failed: ${String(error)}`);
    } finally {
      setSaving(false);
    }
  }, [finishWrite]);

  const handleCreate = useCallback(async (draft: WorkflowDraft) => {
    setSaving(true);
    try {
      const result = await createWorkflowSkill(
        draft.frontmatter.domain,
        draft.frontmatter.name,
        draft.frontmatter,
        draft.body,
      );
      finishWrite(result, 'Created');
      await handleSelect({
        tier: 'workflows',
        path: `${draft.frontmatter.domain}/${draft.frontmatter.name}/SKILL.md`,
      });
    } catch (error) {
      setSaveStatus(`Create failed: ${String(error)}`);
    } finally {
      setSaving(false);
    }
  }, [finishWrite, handleSelect]);

  const handleDelete = useCallback(async (selection: SkillSelection, name: string) => {
    const domain = selection.path.split('/')[0];
    if (!window.confirm(`Delete workflow skill ${name}? Git history keeps the file.`)) return;
    setSaving(true);
    try {
      const result = await deleteWorkflowSkill(domain, name);
      const warningText = result.warnings.length ? ` Warnings: ${result.warnings.join('; ')}` : '';
      finishWrite(result, `Deleted${warningText ? ' —' + warningText : ''}`);
      setSelected(null);
      setFile(null);
    } catch (error) {
      setSaveStatus(`Delete failed: ${String(error)}`);
    } finally {
      setSaving(false);
    }
  }, [finishWrite]);

  const handleReload = useCallback(async () => {
    try {
      const result = await reloadSkills();
      setReloadStatus(result.reloaded ? 'agent in sync' : `reload failed: ${result.error}`);
    } catch (error) {
      setReloadStatus(`reload failed: ${String(error)}`);
    }
  }, []);

  return (
    <Skills
      catalog={catalog}
      loading={loading}
      selected={selected}
      file={file}
      validation={validation}
      saving={saving}
      reloadStatus={reloadStatus}
      saveStatus={saveStatus}
      onSelect={(selection) => { void handleSelect(selection); }}
      onDraftChange={handleDraftChange}
      onSaveWorkflow={(draft) => { void handleSaveWorkflow(draft); }}
      onSaveRaw={(selection, content) => { void handleSaveRaw(selection, content); }}
      onCreate={(draft) => { void handleCreate(draft); }}
      onDelete={(selection, name) => { void handleDelete(selection, name); }}
      onReload={() => { void handleReload(); }}
      onPageContextChange={onPageContextChange}
    />
  );
}
```

- [ ] **Step 4: Wire into `frontend/src/main.tsx`**

Three edits:

(a) Import, next to the other `*Live` imports:

```ts
import { SkillsLive } from './routes/Skills.live';
```

(b) Nav item — append to `navItems` after the `reports` entry:

```ts
  { route: 'skills' as const,    label: 'Skills' },
```

(c) Command palette — append to `commandItems` after `jump-reports`:

```ts
    { id: 'jump-skills',    group: 'Jump To', label: 'Skills',        shortcut: '↵' },
```

(d) Route render — in the `AppShell` children, after the `reports` line:

```tsx
        {route === 'skills'    && <SkillsLive onPageContextChange={handlePageContextChange} />}
```

- [ ] **Step 5: Run live tests + full frontend verification**

Run: `cd frontend && npx vitest run src/routes/Skills.live.test.tsx && npx vitest run && npx tsc --noEmit`
Expected: new tests PASS, all 685+ existing tests still PASS, tsc clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/Skills.live.tsx frontend/src/routes/Skills.live.test.tsx frontend/src/main.tsx
git commit -m "feat(frontend): Skills page live wiring with auto-reload status"
```

---

### Task 14: full verification

- [ ] **Step 1: Backend full skills-surface run**

Run:
```bash
python -m pytest tests/test_persona_domains.py tests/test_skill_lint_routing.py tests/test_routing_table.py tests/test_agents_rebuild.py tests/test_skills_api.py tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py tests/test_reference_docs.py tests/test_routing_contracts_phase3.py tests/test_skill_lint.py tests/test_skill_lint_ci.py tests/test_skills_loader.py tests/test_personas.py tests/test_envelope_workflow_skills.py tests/test_skill_rewrite_regression.py tests/test_skills_phase3_layout.py tests/test_skills_read_smoke_v2.py tests/test_async_agents_unit.py -q
```
Expected: all pass except the known `langchain_quickjs` failure.

- [ ] **Step 2: Broader backend sanity**

Run: `python -m pytest tests/ -q -x --ignore=tests/test_personas.py 2>&1 | tail -5`
Expected: no failures introduced by this work. (If unrelated pre-existing failures surface, compare against `main` before assuming they're yours: `git stash && python -m pytest <failing test> -q && git stash pop` is NOT available across worktrees — instead run the same test in the main checkout.)

- [ ] **Step 3: Frontend full run**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: all pass, tsc clean.

- [ ] **Step 4: Manual smoke (optional but recommended)**

Start the backend + frontend dev servers per the repo's usual run scripts, open the Skills page, edit `fetch-market-data`'s description, save, and confirm (a) the save status shows "Saved · agent reloaded", (b) the backend log shows no rebuild error, (c) `git diff backend/app/skills` shows a minimal clean diff.

- [ ] **Step 5: Final commit (if any stragglers) and plan checkbox sweep**

```bash
git status --short   # should be clean except intentionally edited files
git add -A && git commit -m "chore: frontend skill management verification sweep" # only if needed
```

---

## Plan self-review notes (kept for the executor)

- **Spec coverage:** CRUD endpoints (Tasks 6–9), structured form + raw editors (Tasks 11–12), live lint via debounced validate (Task 13), auto-reload on save + manual reload (Tasks 5, 6, 13), data-driven routing + backfill + sentinel (Tasks 3–4), persona-visibility cross-check (Tasks 1–2), domain restriction on create (Task 6/8), delete cross-reference warnings (Task 6/8), canonical serialization (Task 6), catalog-pinning migration gate (Task 4 Step 6).
- The spec's "byte-for-byte" equivalence wording is corrected to row-set equivalence in Task 4 Step 7 — the generator sorts rows; the hand-written order was curatorial.
- `PageHeader` API verified against the real component (`{title, chips, action}`); no soft spots remain.

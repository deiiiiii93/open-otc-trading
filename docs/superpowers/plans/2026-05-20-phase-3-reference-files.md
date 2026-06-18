# Phase 3 Reference Files Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the Phase 3 durable reference material for products, pricing, market data, portfolios, and RFQ into `backend/app/skills/references/`.

**Architecture:** `references/` holds schema-validated markdown reference documents that are not executable skills and are not loaded as runtime policy. Legacy `SKILL.md` sources stay loadable until the later Phase 3 legacy-deletion slice; this slice copies and rewrites durable facts into reference docs without moving or deleting legacy domain/product skills.

**Tech Stack:** Python 3.11, `pathlib`, dataclasses, PyYAML, pytest, markdown files.

---

## Spec Slice

This plan implements Phase 3.5 from `docs/superpowers/specs/2026-05-19-pet-agent-and-runtime-refactor-design.md`:

- Migrate `references/` files for products, pricing, market data, portfolios, and RFQ.
- Preserve the `legacy/` subtree while Phase 3 is still in progress.
- Keep workflow-skill migration out of this slice; P3.6-P3.9 handle workflow files and legacy deletion.

## Target Reference File Set

```text
backend/app/skills/references/products/snowball-cn.md
backend/app/skills/references/pricing/engines.md
backend/app/skills/references/market-data/conventions.md
backend/app/skills/references/portfolios/model.md
backend/app/skills/references/rfq/lifecycle.md
```

## Reference Frontmatter Schema

Every reference markdown file must start with:

```yaml
---
name: snowball-cn
description: Durable China Snowball product conventions and diagnostics for desk workflows.
reference_type: product
source_legacy_skill: legacy/products/snowball-cn/SKILL.md
---
```

Required fields:

```text
name
description
reference_type
source_legacy_skill
```

Allowed `reference_type` values:

```text
product
pricing
market_data
portfolio
rfq
```

`name` must equal the filename stem. `description` must be a non-empty string no longer than 200 characters. `source_legacy_skill` must point to an existing file under `backend/app/skills/legacy/`. Body content must start with a `## ` heading.

## File Structure

- Modify `backend/app/services/deep_agent/skills_paths.py`
  - Add `REFERENCES_DIR = SKILLS_ROOT / "references"`.
  - Export `REFERENCES_DIR`.
- Create `backend/app/services/deep_agent/reference_docs.py`
  - Parse reference frontmatter.
  - Validate one reference document.
  - Validate the reference tree.
- Create `tests/test_reference_docs.py`
  - Assert the exact Phase 3.5 reference file set.
  - Assert schema validation.
  - Assert files are reference markdown, not executable `SKILL.md` files.
  - Assert legacy source skills remain loadable.
  - Assert reference docs do not contain archaeology markers.
- Create the five target reference markdown files.
- Remove `backend/app/skills/references/.gitkeep` after real files exist.

## Task 1: Add Failing Reference-Doc Tests

**Files:**
- Create: `tests/test_reference_docs.py`

- [x] **Step 1: Create the failing test file**

Create `tests/test_reference_docs.py` with this content:

```python
"""Phase 3.5 reference-document migration tests."""
from __future__ import annotations

from pathlib import Path
import re

import pytest

from app.services.deep_agent import reference_docs
from app.services.deep_agent.reference_docs import (
    VALID_REFERENCE_TYPES,
    parse_reference_doc,
    validate_reference_doc_file,
    validate_reference_doc_tree,
)
from app.services.deep_agent.skills_paths import REFERENCES_DIR, SKILLS_ROOT


EXPECTED_REFERENCE_FILES = {
    "products/snowball-cn.md",
    "pricing/engines.md",
    "market-data/conventions.md",
    "portfolios/model.md",
    "rfq/lifecycle.md",
}

ARCHAEOLOGY_PATTERN = re.compile(
    r"commit `[0-9a-f]{6,}`|v1 (commit|anchor|added)|fixed this mistake|grandfathered|(?:--|\\u2014)v\\d",
    re.IGNORECASE,
)


def _relative_reference_files() -> set[str]:
    return {
        path.relative_to(REFERENCES_DIR).as_posix()
        for path in REFERENCES_DIR.rglob("*.md")
    }


def test_reference_doc_file_set_matches_phase3_target() -> None:
    assert _relative_reference_files() == EXPECTED_REFERENCE_FILES


def test_reference_docs_have_valid_schema() -> None:
    docs = validate_reference_doc_tree(REFERENCES_DIR)

    assert {doc.path.relative_to(REFERENCES_DIR).as_posix() for doc in docs} == EXPECTED_REFERENCE_FILES
    assert {doc.frontmatter["reference_type"] for doc in docs} <= VALID_REFERENCE_TYPES
    for doc in docs:
        assert doc.frontmatter["name"] == doc.path.stem
        assert doc.body.startswith("## ")
        source = SKILLS_ROOT / doc.frontmatter["source_legacy_skill"]
        assert source.is_file()
        assert source.name == "SKILL.md"


def test_reference_doc_rejects_source_that_escapes_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_root = tmp_path / "skills"
    (skills_root / "legacy").mkdir(parents=True)
    escaped = skills_root / "references" / "rfq"
    escaped.mkdir(parents=True)
    (escaped / "lifecycle.md").write_text("escaped reference", encoding="utf-8")
    monkeypatch.setattr(reference_docs, "SKILLS_ROOT", skills_root)

    path = tmp_path / "lifecycle.md"
    path.write_text(
        """---
name: lifecycle
description: Invalid reference source used by a validator regression test.
reference_type: rfq
source_legacy_skill: legacy/../references/rfq/lifecycle.md
---

## Invalid

This body is valid enough for frontmatter validation.
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must resolve under legacy"):
        validate_reference_doc_file(path)


def test_reference_doc_rejects_non_skill_legacy_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_root = tmp_path / "skills"
    legacy_dir = skills_root / "legacy" / "domains" / "rfq" / "rfq-lifecycle"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "notes.md").write_text("not a skill", encoding="utf-8")
    monkeypatch.setattr(reference_docs, "SKILLS_ROOT", skills_root)

    path = tmp_path / "lifecycle.md"
    path.write_text(
        """---
name: lifecycle
description: Invalid reference source used by a validator regression test.
reference_type: rfq
source_legacy_skill: legacy/domains/rfq/rfq-lifecycle/notes.md
---

## Invalid

This body is valid enough for frontmatter validation.
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must target SKILL.md"):
        validate_reference_doc_file(path)


def test_reference_docs_are_not_executable_skill_files() -> None:
    assert not any(path.name == "SKILL.md" for path in REFERENCES_DIR.rglob("*"))


def test_legacy_source_skills_remain_loadable() -> None:
    legacy_sources = {
        "legacy/products/snowball-cn/SKILL.md",
        "legacy/domains/pricing/pricing-engines/SKILL.md",
        "legacy/domains/market-data/market-data-conventions/SKILL.md",
        "legacy/domains/portfolio/portfolio-model/SKILL.md",
        "legacy/domains/rfq/rfq-lifecycle/SKILL.md",
    }

    for source in legacy_sources:
        path = SKILLS_ROOT / source
        assert path.is_file()
        assert path.read_text(encoding="utf-8").startswith("---\n")


def test_reference_docs_have_no_archaeology_markers() -> None:
    for path in REFERENCES_DIR.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert not ARCHAEOLOGY_PATTERN.search(text), path


def test_rfq_lifecycle_reference_matches_runtime_status_contract() -> None:
    doc = parse_reference_doc(REFERENCES_DIR / "rfq" / "lifecycle.md")

    for status in {
        "draft",
        "submitted",
        "pricing_failed",
        "pending_approval",
        "approved",
        "rejected",
        "released",
        "client_accepted",
        "booked",
    }:
        assert f"`{status}`" in doc.body
    assert "`quoted`" not in doc.body
    assert "submitted for approval" not in doc.body.lower()
```

- [x] **Step 2: Run the focused test and verify RED**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_reference_docs.py -q
```

Expected: FAIL because `app.services.deep_agent.reference_docs` does not exist yet.

## Task 2: Add the Reference-Doc Validator

**Files:**
- Modify: `backend/app/services/deep_agent/skills_paths.py`
- Create: `backend/app/services/deep_agent/reference_docs.py`

- [x] **Step 1: Add `REFERENCES_DIR`**

Update `backend/app/services/deep_agent/skills_paths.py` so the path constants are:

```python
APP_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = APP_ROOT / "skills"
LEGACY_SKILLS_ROOT = SKILLS_ROOT / "legacy"
META_DIR = SKILLS_ROOT / "meta"
REFERENCES_DIR = SKILLS_ROOT / "references"
POLICY_DIR = META_DIR


__all__ = [
    "APP_ROOT",
    "SKILLS_ROOT",
    "LEGACY_SKILLS_ROOT",
    "META_DIR",
    "REFERENCES_DIR",
    "POLICY_DIR",
]
```

- [x] **Step 2: Create `reference_docs.py`**

Create `backend/app/services/deep_agent/reference_docs.py` with this content:

```python
"""Reference-document validation for the Phase 3 skill catalog."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .skills_paths import REFERENCES_DIR, SKILLS_ROOT


REFERENCE_DOC_REQUIRED_FIELDS = {
    "name",
    "description",
    "reference_type",
    "source_legacy_skill",
}
VALID_REFERENCE_TYPES = {
    "product",
    "pricing",
    "market_data",
    "portfolio",
    "rfq",
}


@dataclass(frozen=True)
class ReferenceDoc:
    path: Path
    frontmatter: dict[str, Any]
    body: str


def parse_reference_doc(path: Path) -> ReferenceDoc:
    text = Path(path).read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text, path=Path(path))
    return ReferenceDoc(
        path=Path(path),
        frontmatter=frontmatter,
        body=body.strip(),
    )


def validate_reference_doc_tree(root: Path = REFERENCES_DIR) -> list[ReferenceDoc]:
    return [validate_reference_doc_file(path) for path in sorted(Path(root).rglob("*.md"))]


def validate_reference_doc_file(path: Path) -> ReferenceDoc:
    doc = parse_reference_doc(path)
    missing = sorted(REFERENCE_DOC_REQUIRED_FIELDS - set(doc.frontmatter))
    errors: list[str] = []
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")

    name = doc.frontmatter.get("name")
    if name != doc.path.stem:
        errors.append(f"name must match filename stem {doc.path.stem!r}")

    description = doc.frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append("description must be a non-empty string")
    elif len(description) > 200:
        errors.append("description must be <=200 characters")

    reference_type = doc.frontmatter.get("reference_type")
    if reference_type not in VALID_REFERENCE_TYPES:
        errors.append(f"reference_type must be one of {sorted(VALID_REFERENCE_TYPES)}")

    source = doc.frontmatter.get("source_legacy_skill")
    if not isinstance(source, str) or not source:
        errors.append("source_legacy_skill must be a legacy-relative path")
    else:
        source_path = Path(source)
        if source_path.is_absolute() or source_path.parts[:1] != ("legacy",):
            errors.append("source_legacy_skill must be a legacy-relative path")
        else:
            resolved_source = (SKILLS_ROOT / source_path).resolve()
            legacy_root = (SKILLS_ROOT / "legacy").resolve()
            try:
                resolved_source.relative_to(legacy_root)
            except ValueError:
                errors.append("source_legacy_skill must resolve under legacy")
            else:
                if resolved_source.name != "SKILL.md":
                    errors.append("source_legacy_skill must target SKILL.md")
                elif not resolved_source.is_file():
                    errors.append(f"source_legacy_skill does not exist: {source}")

    if not doc.body.startswith("## "):
        errors.append("body must start with a markdown h2 heading")

    if errors:
        raise ValueError(f"Invalid reference doc {doc.path}: {'; '.join(errors)}")
    return doc


def _split_frontmatter(text: str, *, path: Path) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ValueError(f"Reference doc missing frontmatter: {path}")
    try:
        _, rest = text.split("---\n", 1)
        raw_frontmatter, body = rest.split("\n---\n", 1)
    except ValueError as exc:
        raise ValueError(f"Reference doc has malformed frontmatter fences: {path}") from exc
    loaded = yaml.safe_load(raw_frontmatter) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Reference doc frontmatter must be a mapping: {path}")
    return loaded, body
```

- [x] **Step 3: Run the focused test and verify partial GREEN/next RED**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_reference_docs.py -q
```

Expected: FAIL because the target reference markdown files do not exist yet.

## Task 3: Add the Reference Markdown Files

**Files:**
- Create: `backend/app/skills/references/products/snowball-cn.md`
- Create: `backend/app/skills/references/pricing/engines.md`
- Create: `backend/app/skills/references/market-data/conventions.md`
- Create: `backend/app/skills/references/portfolios/model.md`
- Create: `backend/app/skills/references/rfq/lifecycle.md`
- Delete: `backend/app/skills/references/.gitkeep`

- [x] **Step 1: Create the reference directories**

Run:

```bash
mkdir -p backend/app/skills/references/products backend/app/skills/references/pricing backend/app/skills/references/market-data backend/app/skills/references/portfolios backend/app/skills/references/rfq
```

- [x] **Step 2: Create `products/snowball-cn.md`**

Create `backend/app/skills/references/products/snowball-cn.md` with this content:

```markdown
---
name: snowball-cn
description: Durable China Snowball product conventions and diagnostics for desk workflows.
reference_type: product
source_legacy_skill: legacy/products/snowball-cn/SKILL.md
---

## Product Definition

A China-market Snowball is a path-dependent autocallable on one A-share index underlying, typically CSI 300, CSI 500, or CSI 1000. Scheduled KO observations pay accrued coupon and principal early when the closing level is at or above the KO barrier. If the trade never knocks out and never knocks in, it returns full principal at maturity. If it knocks in and never knocks out, terminal payoff takes equity loss versus strike.

## Observation Conventions

KO observations are scheduled monthly. Daily KI is the default for CN Snowballs and uses discrete SSE business-day observations from trade start plus one day through exercise date. European KI uses a single maturity observation, and no-KI trades remove the knock-in leg. Knock-in is sticky once observed, so lifecycle fields must override any assumption that the KI barrier is still conditional.

## Pricing Inputs

The desk adapter maps standard CN Snowballs to `SnowballQuadEngine`. Required inputs are spot, volatility, risk-free rate, dividend yield or discrete dividends, KO schedule, KI convention, strike, notional, coupon, and lifecycle state. ACT/365 and China Mainland exchange calendars are the default desk conventions for imported CN Snowballs.

## Diagnostics

Spot within 5 percent of KI indicates elevated gamma and should be flagged for hedge review. Spot within 2 percent of the next KO observation should be repriced with fresh market data if the prior run is older than one business day. A knocked-in lifecycle flag that disagrees with the imported lifecycle state is a data-quality issue before it is a pricing issue.
```

- [x] **Step 3: Create `pricing/engines.md`**

Create `backend/app/skills/references/pricing/engines.md` with this content:

```markdown
---
name: engines
description: Durable pricing-engine conventions and input requirements for desk workflows.
reference_type: pricing
source_legacy_skill: legacy/domains/pricing/pricing-engines/SKILL.md
---

## Engine Families

European vanilla options use analytic Black-Scholes style valuation and are cheap to run. Snowball products use path-aware structured-product engines with observation calendars and barrier state. Phoenix products use event-driven structured-product engines with coupon and barrier events. Engine choice should follow product type and contract terms rather than persona or page context.

## Required Inputs

Analytic vanilla valuation requires spot, volatility, risk-free rate, dividend yield, and tenor. Structured-product valuation requires those market inputs plus notional, strike, barriers, coupon terms, observation schedules, settlement dates, and lifecycle state. Missing schedules or lifecycle state should be treated as contract-data gaps rather than market-data gaps.

## Cost Classes

Single analytic valuations are cheap. Single structured valuations are medium unless the engine configuration expands path count or grid size. Portfolio-wide structured-product repricing is expensive and should be previewed before execution, especially when Snowball or Phoenix trades are included.

## Selection Rule

Pick the engine from product type and validated contract terms. Do not infer a cheaper engine from a short user request. If the requested product has path-dependent barriers, use a structured-product engine even when the user asks for a quick quote.
```

- [x] **Step 4: Create `market-data/conventions.md`**

Create `backend/app/skills/references/market-data/conventions.md` with this content:

```markdown
---
name: conventions
description: Durable market-data source, symbol, staleness, and drift conventions for desk workflows.
reference_type: market_data
source_legacy_skill: legacy/domains/market-data/market-data-conventions/SKILL.md
---

## Sources

A-share spot data comes from AKShare snapshots for indices, sectors, and single names. HK spot data uses the available AKShare HK feed and may refresh less frequently. Volatility surfaces, dividend curves, and pricing assumptions belong to pricing profiles rather than spot market-data profiles unless a workflow explicitly fetches or builds them.

## Symbol Conventions

CN index symbols use exchange suffixes such as `000300.SH`, `000905.SH`, and `000852.SH`. A-share single names use `.SH` for Shanghai listings and `.SZ` for Shenzhen listings. HK indices use desk symbols such as `HSI` and `HSCEI` without an exchange suffix.

## Refresh Cadence

Intraday spot values are point-in-time snapshots and require explicit refresh. A-share day-end values settle after the local market close. Volatility inputs are normally weekly unless the user requests a rebuild or a risk workflow requires a fresher pricing profile.

## Staleness And Drift

Spot older than one business day is stale for new pricing decisions. Volatility older than five business days is stale for structured-product repricing. Spot drift is material when the absolute percentage move versus stored value is above 1 percent for trader workflows or above 2 percent for risk workflows.
```

- [x] **Step 5: Create `portfolios/model.md`**

Create `backend/app/skills/references/portfolios/model.md` with this content:

```markdown
---
name: model
description: Durable portfolio model conventions for membership, sources, and position queries.
reference_type: portfolio
source_legacy_skill: legacy/domains/portfolio/portfolio-model/SKILL.md
---

## Portfolio Kinds

A Container portfolio explicitly holds positions and is changed by membership operations. A View portfolio is defined by source or rule filters and recomputes membership when queried. This distinction is user-visible when explaining why a portfolio is empty or why a new imported position appears automatically.

## Position Membership

A position has one owning container context but may appear in multiple view portfolios. Query APIs should resolve both kinds through the same portfolio identifier so workflows do not need to branch on implementation details before reading positions.

## Query Pattern

Use portfolio enumeration when the user names a portfolio ambiguously. Inspect the selected portfolio before mutating membership or rules. Read positions through the portfolio-aware position query path so derived views and explicit containers produce consistent downstream pricing, risk, and reporting inputs.

## Empty Portfolio Semantics

An empty View can be valid when its rule matches no current positions. An empty Container often means stale or incomplete membership. Surface that distinction before running portfolio-level pricing or risk.
```

- [x] **Step 6: Create `rfq/lifecycle.md`**

Create `backend/app/skills/references/rfq/lifecycle.md` with this content:

```markdown
---
name: lifecycle
description: Durable RFQ lifecycle states, transition ownership, and audit conventions.
reference_type: rfq
source_legacy_skill: legacy/domains/rfq/rfq-lifecycle/SKILL.md
---

## State Sequence

The RFQ lifecycle starts at `draft`. A submitted but not yet priced request is `submitted`. Pricing moves the RFQ to either `pending_approval` when successful or `pricing_failed` when valuation or term validation fails. Approval then moves to `approved` or `rejected`; approved RFQs continue through `released`, `client_accepted`, and `booked`.

## Transition Ownership

Trader workflows own drafting, validating, submitting, quoting, release, client acceptance, and booking to position. High-board workflows own approval and rejection while the RFQ is `pending_approval`. A `pricing_failed` RFQ returns to trader ownership for term repair or repricing.

## HITL Gates

Approval, rejection, release, client acceptance, and booking are explicit human-in-the-loop gates. Draft creation, submission, and quote calculation can run without HITL when the required terms and pricing inputs are present. Booking always requires confirmation because it materializes a position.

## Audit Events

Every state transition should emit an audit event with actor, timestamp, and diff. Workflow output should reference the RFQ identifier and relevant audit event type so the user can reconcile operational state with persisted history.
```

- [x] **Step 7: Remove `.gitkeep` and run focused tests**

Run:

```bash
rm backend/app/skills/references/.gitkeep
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_reference_docs.py -q
```

Expected: PASS.

## Task 4: Verify Phase 3 Compatibility

**Files:**
- No new files unless verification reveals a defect.

- [x] **Step 1: Run reference and existing Phase 3 tests**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_reference_docs.py tests/test_meta_policies.py tests/test_skills_loader.py tests/test_async_agents_unit.py tests/test_skill_lint.py tests/test_skill_lint_ci.py tests/test_skills_phase3_layout.py tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_skills_read_smoke_v2.py -q
```

Expected: PASS.

- [x] **Step 2: Inspect git status**

Run:

```bash
git status --short
```

Expected changed files are limited to the P3.5 plan, reference-doc validator, skills path constant, reference markdown files, test file, and `.gitkeep` removal.

- [x] **Step 3: Request code review**

Use `superpowers:requesting-code-review` against the staged or working-tree diff and ask whether the implementation matches this plan and the Phase 3.5 spec slice.

- [x] **Step 4: Patch review findings**

For any concrete review finding, add or adjust tests first when behavior/schema changes, verify the failing test, then patch implementation and rerun the focused suite.

- [x] **Step 5: Final verification**

Run the same command from Step 1 after review patches. Expected: PASS.

- [x] **Step 6: Commit**

Run:

```bash
git add docs/superpowers/plans/2026-05-20-phase-3-reference-files.md backend/app/services/deep_agent/skills_paths.py backend/app/services/deep_agent/reference_docs.py backend/app/skills/references tests/test_reference_docs.py
git add -u backend/app/skills/references
git commit -m "docs(skills): migrate phase 3 reference files"
```

Expected: commit succeeds on `codex/skill-references-phase3`.

## Self-Review

- Spec coverage: The plan covers the five reference destinations listed for P3.5 and keeps workflow migrations out of scope.
- Placeholder scan: No task uses placeholder instructions; every created file has concrete content.
- Type consistency: `ReferenceDoc`, `validate_reference_doc_tree`, `REFERENCES_DIR`, and `VALID_REFERENCE_TYPES` are named consistently across tests and implementation.

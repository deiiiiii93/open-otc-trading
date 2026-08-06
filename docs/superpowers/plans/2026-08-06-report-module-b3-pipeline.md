# Report Module — Sub-project B3: Generation Pipeline & Grounding Guard

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a template plus a portfolio into a persisted `ReportDocument` — server resolves every block, the agent narrates section by section, and a grounding guard flags any number in the prose that is not in that section's data.

**Architecture:** The pipeline resolves all blocks first (deterministic, no LLM), then calls a `narrate` callable **injected as a parameter** so every test runs without a model. The default narrator dispatches the template's declared persona. Sections with `narrative: null` skip the LLM entirely, so a fully-deterministic template generates with zero calls.

**Tech Stack:** Python 3.11, SQLAlchemy 2.x, pydantic v2, pytest.

**Spec:** `docs/superpowers/specs/2026-08-06-report-module-redesign-design.md` §5.1, §5.5, §5.6
**Depends on:** `2026-08-06-report-module-b2-seeds.md` Task 2 complete — seeds in the DB and green.

## Global Constraints

- **The agent's only output is a prose string per section.** It never returns structure, never returns numbers as data. Every rendered figure comes from a `BlockResult`.
- **The narrator is injected.** `generate_report(..., narrate=...)` takes a callable so tests need no model and no network. The production default is resolved inside the router/tool layer, not baked into the pipeline.
- **The agent is told each block's `status`.** A section whose block is `unavailable` must reach the narrator with that fact, or it will write "the book is within all limits" about a check that never ran.
- **Grounding flags are non-blocking by default.** Spec §9 open decision 2: the default action is flag-inline, recorded in `section.grounding.flags`. Do not fail generation on a flag.
- **Test command:** `.venv/bin/python -m pytest` from the repo root. Never pipe through `tail`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/app/services/reporting/grounding.py` | Numeric grounding guard over narrative prose |
| `backend/app/services/reporting/document.py` | `ReportDocument` assembly and shape |
| `backend/app/services/reporting/generate.py` | The pipeline: resolve → narrate → guard → assemble → persist |
| `tests/test_reporting_grounding.py` | Guard behaviour |
| `tests/test_reporting_generate.py` | Pipeline behaviour |

---

### Task 1: Numeric grounding guard

**Files:**
- Create: `backend/app/services/reporting/grounding.py`
- Test: `tests/test_reporting_grounding.py`

**Interfaces:**
- Consumes: `_scan_numeric_tokens` from `app.golden_workflows.assertions` — signature
  `(text: str) -> list[tuple[int, float]]`, returning `(start_offset, value)` per numeric token,
  with `k`/`m`/`bn` suffixes expanded and `%` tokens additionally yielding `value/100`
- Produces:
  - `collect_numeric_values(data: Any) -> list[float]` — every finite number anywhere in a nested structure
  - `check_grounding(narrative: str, block_data: list[dict], *, rel_tol: float = 0.01) -> dict` returning `{"checked": True, "flags": [{"token": float, "offset": int}], "grounded_count": int}`
  - `DEFAULT_REL_TOL: float = 0.01`

**Design note:** the guard reuses the arena scorer's tokenizer rather than writing a second one.
That scorer exists to catch a model quoting a number it never fetched, which is exactly the
failure mode of an LLM-written risk report — same check, different seat.

- [ ] **Step 1: Write the failing test**

Create `tests/test_reporting_grounding.py`:

```python
from app.services.reporting.grounding import (
    check_grounding,
    collect_numeric_values,
)


def test_collect_walks_nested_structures():
    data = {
        "metrics": {"delta_cash": 57334.67, "vega": 276.43},
        "rows": [{"change": -12.5}, {"change": 3}],
        "label": "not a number",
        "nested": {"deep": [{"x": 1.5}]},
    }
    values = collect_numeric_values(data)
    assert 57334.67 in values
    assert 276.43 in values
    assert -12.5 in values
    assert 3.0 in values
    assert 1.5 in values


def test_collect_ignores_booleans():
    """True/False are ints in Python; treating them as data numbers is noise."""
    assert collect_numeric_values({"ok": True, "bad": False, "n": 2.0}) == [2.0]


def test_a_narrative_quoting_only_block_numbers_has_no_flags():
    blocks = [{"metrics": {"delta_cash": 57334.67, "vega": 276.43}}]
    narrative = "Delta cash stands at 57,334.67 with vega of 276.43."
    result = check_grounding(narrative, blocks)
    assert result["checked"] is True
    assert result["flags"] == []
    assert result["grounded_count"] == 2


def test_an_invented_number_is_flagged():
    blocks = [{"metrics": {"delta_cash": 57334.67}}]
    narrative = "Delta cash stands at 57,334.67 and vega at 999.99."
    result = check_grounding(narrative, blocks)
    assert [flag["token"] for flag in result["flags"]] == [999.99]


def test_tolerance_allows_rounded_prose():
    """A trader writes 57,335 not 57,334.67. That is grounded, not invented."""
    blocks = [{"metrics": {"delta_cash": 57334.67}}]
    result = check_grounding("Delta cash is about 57,335.", blocks)
    assert result["flags"] == []


def test_tolerance_does_not_swallow_a_materially_different_number():
    blocks = [{"metrics": {"delta_cash": 57334.67}}]
    result = check_grounding("Delta cash is about 61,000.", blocks)
    assert [flag["token"] for flag in result["flags"]] == [61000.0]


def test_percentages_match_either_form():
    """A 0.34 ratio in data may legitimately be written as 34%."""
    blocks = [{"data": {"ratio": 0.34}}]
    assert check_grounding("Utilisation reached 34%.", blocks)["flags"] == []


def test_a_narrative_with_no_numbers_is_trivially_grounded():
    result = check_grounding("The book is quiet and needs no action.", [{}])
    assert result["flags"] == []
    assert result["grounded_count"] == 0


def test_empty_narrative_is_reported_as_unchecked():
    result = check_grounding("", [{"metrics": {"x": 1.0}}])
    assert result["checked"] is False
    assert result["flags"] == []


def test_values_from_any_block_in_the_section_count_as_grounded():
    blocks = [{"metrics": {"a": 10.0}}, {"metrics": {"b": 20.0}}]
    assert check_grounding("We saw 10 and 20.", blocks)["flags"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reporting_grounding.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.reporting.grounding'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/services/reporting/grounding.py`:

```python
"""Runtime numeric grounding guard over agent-written report narrative.

Every numeric token in a section's prose must correspond to a number that
appears somewhere in that section's resolved block data. This reuses the arena
scorer's tokenizer (``_scan_numeric_tokens``), which was built to catch a model
quoting a value it never fetched — the same failure mode as an LLM writing a
risk report, so the same check applies.

Flags are informational by default: generation records them on the section
rather than failing, so a false positive degrades the report's confidence
signal instead of destroying the report.
"""
from __future__ import annotations

import math
from typing import Any

from app.golden_workflows.assertions import _scan_numeric_tokens

DEFAULT_REL_TOL = 0.01


def _is_real_number(value: Any) -> bool:
    # bool is a subclass of int; a True in a payload is not a reported figure.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def collect_numeric_values(data: Any) -> list[float]:
    """Every finite number anywhere in a nested structure, in traversal order."""
    found: list[float] = []

    def walk(node: Any) -> None:
        if _is_real_number(node):
            found.append(float(node))
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(data)
    return found


def _matches(token: float, values: list[float], rel_tol: float) -> bool:
    for value in values:
        tolerance = rel_tol * abs(value) if value != 0 else rel_tol
        if abs(token - value) <= tolerance:
            return True
    return False


def check_grounding(
    narrative: str,
    block_data: list[dict[str, Any]],
    *,
    rel_tol: float = DEFAULT_REL_TOL,
) -> dict[str, Any]:
    """Flag numeric tokens in ``narrative`` absent from ``block_data``.

    ``block_data`` is the list of resolved ``BlockResult.data`` payloads for the
    section. A token matching ANY value in ANY of them is grounded.
    """
    if not (narrative or "").strip():
        return {"checked": False, "flags": [], "grounded_count": 0}

    values = collect_numeric_values(block_data)
    tokens = _scan_numeric_tokens(narrative)

    # A `%` token yields both its face value and value/100, so a 0.34 ratio
    # written as "34%" matches. Group by offset and ground the token if EITHER
    # reading matches.
    by_offset: dict[int, list[float]] = {}
    for offset, token in tokens:
        by_offset.setdefault(offset, []).append(token)

    flags: list[dict[str, Any]] = []
    grounded = 0
    for offset in sorted(by_offset):
        readings = by_offset[offset]
        if any(_matches(reading, values, rel_tol) for reading in readings):
            grounded += 1
        else:
            flags.append({"token": readings[0], "offset": offset})

    return {"checked": True, "flags": flags, "grounded_count": grounded}


__all__ = ["DEFAULT_REL_TOL", "collect_numeric_values", "check_grounding"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reporting_grounding.py -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/reporting/grounding.py tests/test_reporting_grounding.py
git commit -m "feat(reporting): numeric grounding guard reusing the arena tokenizer"
```

---

### Task 2: ReportDocument assembly

**Files:**
- Create: `backend/app/services/reporting/document.py`
- Test: `tests/test_reporting_generate.py` (first three tests)

**Interfaces:**
- Consumes: `TemplateSpec`, `BlockResult`
- Produces:
  - `build_document(*, spec, spec_yaml, version, params, sections, provenance) -> dict`
  - `spec_sha256(spec_yaml: str) -> str`
  - `narrator_brief(section, results) -> dict` — what the agent is shown for one section

- [ ] **Step 1: Write the failing test**

Create `tests/test_reporting_generate.py`:

```python
import pytest

from app.services.reporting import blocks  # noqa: F401 - registers producers
from app.services.reporting.contracts import BlockResult
from app.services.reporting.document import (
    build_document,
    narrator_brief,
    spec_sha256,
)
from app.services.reporting.template_spec import parse_spec

SPEC = """
meta:
  slug: demo-daily
  title: Demo Daily
  persona: risk_manager
sections:
  - id: headline
    title: Headline
    blocks:
      - { key: risk.totals, render: metric_row }
    narrative: Say something.
  - id: quiet
    title: Quiet
    blocks:
      - { key: coverage.evidence, render: metric_row }
"""


def test_spec_sha256_is_stable_and_content_addressed():
    assert spec_sha256(SPEC) == spec_sha256(SPEC)
    assert spec_sha256(SPEC) != spec_sha256(SPEC + "\n")
    assert spec_sha256(SPEC).startswith("sha256:")


def test_document_embeds_the_template_spec_for_reproducibility():
    spec = parse_spec(SPEC)
    doc = build_document(
        spec=spec, spec_yaml=SPEC, version=3,
        params={"portfolio_id": 2, "compare_to_run_id": 35},
        sections=[], provenance={"risk_run_id": 36},
    )
    assert doc["template"]["slug"] == "demo-daily"
    assert doc["template"]["version"] == 3
    assert doc["template"]["spec"] == SPEC
    assert doc["template"]["spec_sha256"] == spec_sha256(SPEC)
    assert doc["params"]["portfolio_id"] == 2
    assert doc["provenance"]["risk_run_id"] == 36


def test_narrator_brief_tells_the_agent_each_block_status():
    """A section whose block is unavailable MUST reach the narrator saying so."""
    spec = parse_spec(SPEC)
    section = spec.sections[0]
    results = {
        "risk.totals": BlockResult.unavailable("no completed risk run exists"),
    }
    brief = narrator_brief(section, results)
    assert brief["section_id"] == "headline"
    assert brief["instruction"] == "Say something."
    entry = brief["blocks"][0]
    assert entry["key"] == "risk.totals"
    assert entry["status"] == "unavailable"
    assert "no completed risk run" in entry["reason"]
    assert entry["data"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reporting_generate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.reporting.document'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/services/reporting/document.py`:

```python
"""ReportDocument shape and assembly.

A document embeds the full template spec plus its sha256, so a report is a
self-contained reproducible artifact: editing a template later cannot silently
change what an old report claims to have been generated from, and no stored
hash points at a row that may since have moved.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .contracts import BlockResult
from .template_spec import SectionSpec, TemplateSpec


def spec_sha256(spec_yaml: str) -> str:
    digest = hashlib.sha256(spec_yaml.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def narrator_brief(
    section: SectionSpec, results: dict[str, BlockResult]
) -> dict[str, Any]:
    """What the agent is shown for one section.

    Each block carries its status and reason, not just its data. A narrator that
    cannot see ``status == "unavailable"`` will write that the book is within
    limits when the limit check never ran.
    """
    return {
        "section_id": section.id,
        "section_title": section.title,
        "instruction": (section.narrative or "").strip(),
        "blocks": [
            {
                "key": ref.key,
                "status": results[ref.key].status,
                "reason": results[ref.key].reason,
                "data": results[ref.key].data,
            }
            for ref in section.blocks
            if ref.key in results
        ],
    }


def build_document(
    *,
    spec: TemplateSpec,
    spec_yaml: str,
    version: int,
    params: dict[str, Any],
    sections: list[dict[str, Any]],
    provenance: dict[str, Any],
    generated_at: str | None = None,
) -> dict[str, Any]:
    return {
        "template": {
            "slug": spec.meta.slug,
            "title": spec.meta.title,
            "persona": spec.meta.persona,
            "version": version,
            "spec": spec_yaml,
            "spec_sha256": spec_sha256(spec_yaml),
        },
        "params": params,
        "generated_at": generated_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sections": sections,
        "provenance": provenance,
    }


__all__ = ["spec_sha256", "narrator_brief", "build_document"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reporting_generate.py -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/reporting/document.py tests/test_reporting_generate.py
git commit -m "feat(reporting): ReportDocument assembly with embedded spec"
```

---

### Task 3: The generation pipeline

**Files:**
- Create: `backend/app/services/reporting/generate.py`
- Modify: `tests/test_reporting_generate.py` (append)

**Interfaces:**
- Consumes: `get_template` (B2 Task 4), `parse_spec` (B2 Task 2), `resolve_block` (B1 Task 1), `narrator_brief` / `build_document` (Task 2), `check_grounding` (Task 1), `load_run_pair` (Plan A Task 2)
- Produces:
  - `Narrator` — type alias `Callable[[str, dict], str]`, called as `narrate(persona, brief) -> prose`
  - `generate_document(*, template_slug, portfolio_id, compare_to=None, narrate=None, session=None) -> dict` — the pure pipeline, returns a `ReportDocument`
  - `generate_report(*, template_slug, portfolio_id, compare_to=None, narrate=None, session=None) -> ReportJob` — persists the document as a `ReportJob`
  - `TemplateNotFound(Exception)`

**Pipeline order (spec §5.5):** load template → resolve `compare_to` → resolve all blocks →
narrate non-null sections → grounding guard → assemble → persist.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_reporting_generate.py`:

```python
from app.services.reporting.generate import (  # noqa: E402
    TemplateNotFound,
    generate_document,
)


@pytest.fixture
def seeded_template(db_session):
    from app.services.reporting import templates

    templates.save_template(slug="demo-daily", spec_yaml=SPEC, session=db_session)
    return "demo-daily"


@pytest.fixture
def stub_blocks(monkeypatch):
    """Resolve every block to a fixed ok result, no DB."""
    from app.services.reporting import generate as gen

    payloads = {
        "risk.totals": BlockResult.ok(
            data={"metrics": {"delta_cash": 57334.67}},
            provenance={"risk_run_id": 36},
        ),
        "coverage.evidence": BlockResult.ok(
            data={"priced": 4, "total": 5}, provenance={"risk_run_id": 36}
        ),
    }
    monkeypatch.setattr(gen, "resolve_block", lambda key, ctx: payloads[key])
    monkeypatch.setattr(gen, "_resolve_comparison", lambda ctx, session: 35)
    return payloads


def test_only_sections_with_a_narrative_call_the_narrator(
    db_session, seeded_template, stub_blocks
):
    calls = []

    def narrate(persona, brief):
        calls.append(brief["section_id"])
        return "Delta cash is 57,334.67."

    doc = generate_document(
        template_slug=seeded_template, portfolio_id=2,
        narrate=narrate, session=db_session,
    )
    assert calls == ["headline"]
    assert doc["sections"][0]["narrative"] == "Delta cash is 57,334.67."
    assert doc["sections"][1]["narrative"] is None


def test_a_template_with_no_narrative_makes_zero_narrator_calls(
    db_session, stub_blocks, monkeypatch
):
    from app.services.reporting import templates

    silent = SPEC.replace("    narrative: Say something.\n", "")
    templates.save_template(slug="demo-daily", spec_yaml=silent, session=db_session)

    def narrate(persona, brief):  # pragma: no cover - must never run
        raise AssertionError("narrator called for a fully deterministic template")

    doc = generate_document(
        template_slug="demo-daily", portfolio_id=2,
        narrate=narrate, session=db_session,
    )
    assert all(section["narrative"] is None for section in doc["sections"])


def test_the_narrator_receives_the_templates_persona(
    db_session, seeded_template, stub_blocks
):
    seen = []
    generate_document(
        template_slug=seeded_template, portfolio_id=2,
        narrate=lambda persona, brief: seen.append(persona) or "ok",
        session=db_session,
    )
    assert seen == ["risk_manager"]


def test_block_results_are_embedded_per_section(
    db_session, seeded_template, stub_blocks
):
    doc = generate_document(
        template_slug=seeded_template, portfolio_id=2,
        narrate=lambda persona, brief: "Delta cash is 57,334.67.",
        session=db_session,
    )
    block = doc["sections"][0]["blocks"][0]
    assert block["key"] == "risk.totals"
    assert block["render"] == "metric_row"
    assert block["result"]["status"] == "ok"
    assert block["result"]["data"]["metrics"]["delta_cash"] == pytest.approx(57334.67)


def test_an_ungrounded_number_in_prose_is_flagged_but_does_not_fail_generation(
    db_session, seeded_template, stub_blocks
):
    doc = generate_document(
        template_slug=seeded_template, portfolio_id=2,
        narrate=lambda persona, brief: "Delta cash is 57,334.67 and vega is 999.99.",
        session=db_session,
    )
    grounding = doc["sections"][0]["grounding"]
    assert grounding["checked"] is True
    assert [flag["token"] for flag in grounding["flags"]] == [999.99]


def test_a_narrator_failure_degrades_that_section_only(
    db_session, seeded_template, stub_blocks
):
    def narrate(persona, brief):
        raise RuntimeError("model timed out")

    doc = generate_document(
        template_slug=seeded_template, portfolio_id=2,
        narrate=narrate, session=db_session,
    )
    assert doc["sections"][0]["narrative"] is None
    assert "model timed out" in doc["sections"][0]["narrative_error"]
    # The data section is untouched.
    assert doc["sections"][1]["blocks"][0]["result"]["status"] == "ok"


def test_a_missing_template_raises(db_session):
    with pytest.raises(TemplateNotFound):
        generate_document(
            template_slug="does-not-exist", portfolio_id=2,
            narrate=lambda p, b: "", session=db_session,
        )


def test_each_block_is_resolved_exactly_once_even_if_reused(
    db_session, monkeypatch, stub_blocks
):
    """A key repeated across sections must not be recomputed."""
    from app.services.reporting import generate as gen
    from app.services.reporting import templates

    reused = SPEC.replace("key: coverage.evidence", "key: risk.totals")
    templates.save_template(slug="demo-daily", spec_yaml=reused, session=db_session)

    calls = []
    original = gen.resolve_block
    monkeypatch.setattr(
        gen, "resolve_block",
        lambda key, ctx: (calls.append(key), original(key, ctx))[1],
    )
    generate_document(
        template_slug="demo-daily", portfolio_id=2,
        narrate=lambda p, b: "ok", session=db_session,
    )
    assert calls.count("risk.totals") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reporting_generate.py -v`
Expected: FAIL with `ImportError: cannot import name 'TemplateNotFound'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/services/reporting/generate.py`:

```python
"""The report generation pipeline.

Order (spec §5.5): load template -> resolve comparison run -> resolve every
declared block deterministically -> narrate each section that asks for it ->
grounding-guard the prose -> assemble -> persist.

The narrator is INJECTED rather than constructed here, so the pipeline is fully
testable without a model and the LLM binding lives at the tool/router edge.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator

from sqlalchemy.orm import Session

from app import database
from app.models import ReportJob, ReportStatus
from app.services.pnl import load_run_pair

from .contracts import BlockContext, BlockResult
from .document import build_document, narrator_brief
from .grounding import check_grounding
from .registry import resolve_block
from .template_spec import parse_spec
from .templates import get_template

# narrate(persona, brief) -> prose
Narrator = Callable[[str, dict[str, Any]], str]


class TemplateNotFound(Exception):
    """Raised when a report is generated from a slug that does not exist."""


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def _resolve_comparison(ctx: BlockContext, session: Session) -> int | None:
    """Resolve the prior run to compare against, or None when there is none."""
    before, _after = load_run_pair(
        portfolio_id=ctx.portfolio_id,
        risk_run_id=ctx.risk_run_id,
        compare_to_run_id=ctx.compare_to_run_id,
        session=session,
    )
    return before.id if before is not None else None


def generate_document(
    *,
    template_slug: str,
    portfolio_id: int,
    compare_to: int | None = None,
    narrate: Narrator | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """Build a ReportDocument. Does not persist."""
    with _session_scope(session) as sess:
        row = get_template(slug=template_slug, session=sess)
        if row is None:
            raise TemplateNotFound(f"no report template with slug {template_slug!r}")

        spec_yaml = row.spec
        spec = parse_spec(spec_yaml)

        base_ctx = BlockContext(
            portfolio_id=portfolio_id, compare_to_run_id=compare_to
        )
        compare_to_run_id = _resolve_comparison(base_ctx, sess)

        # Resolve every distinct key ONCE, even when several sections reuse it.
        results: dict[str, BlockResult] = {}
        provenance: dict[str, Any] = {}
        for section in spec.sections:
            for ref in section.blocks:
                if ref.key in results:
                    continue
                ctx = BlockContext(
                    portfolio_id=portfolio_id,
                    compare_to_run_id=compare_to_run_id,
                    params=dict(ref.params),
                )
                result = resolve_block(ref.key, ctx)
                results[ref.key] = result
                for key, value in (result.provenance or {}).items():
                    provenance.setdefault(key, value)

        sections: list[dict[str, Any]] = []
        for section in spec.sections:
            block_entries = [
                {
                    "key": ref.key,
                    "render": ref.render,
                    "fields": ref.fields,
                    "result": results[ref.key].model_dump(),
                }
                for ref in section.blocks
                if ref.key in results
            ]

            narrative: str | None = None
            narrative_error: str | None = None
            grounding: dict[str, Any] = {"checked": False, "flags": [],
                                         "grounded_count": 0}

            instruction = (section.narrative or "").strip()
            if instruction and narrate is not None:
                brief = narrator_brief(section, results)
                try:
                    narrative = (narrate(spec.meta.persona, brief) or "").strip() or None
                except Exception as exc:  # noqa: BLE001 - degrade one section only
                    narrative_error = str(exc)
                if narrative:
                    grounding = check_grounding(
                        narrative,
                        [entry["result"]["data"] for entry in block_entries],
                    )

            sections.append(
                {
                    "id": section.id,
                    "title": section.title,
                    "blocks": block_entries,
                    "narrative": narrative,
                    "narrative_error": narrative_error,
                    "grounding": grounding,
                }
            )

        return build_document(
            spec=spec,
            spec_yaml=spec_yaml,
            version=row.version,
            params={
                "portfolio_id": portfolio_id,
                "compare_to_run_id": compare_to_run_id,
            },
            sections=sections,
            provenance=provenance,
        )


def _status_from_document(document: dict[str, Any]) -> str:
    """A report whose blocks all failed is not a clean report."""
    statuses = [
        entry["result"]["status"]
        for section in document["sections"]
        for entry in section["blocks"]
    ]
    if statuses and all(status == "unavailable" for status in statuses):
        return ReportStatus.COMPLETED_WITH_ERRORS.value
    if any(section.get("narrative_error") for section in document["sections"]):
        return ReportStatus.COMPLETED_WITH_ERRORS.value
    return ReportStatus.COMPLETED.value


def generate_report(
    *,
    template_slug: str,
    portfolio_id: int,
    compare_to: int | None = None,
    narrate: Narrator | None = None,
    session: Session | None = None,
) -> ReportJob:
    """Generate and persist a report as a ReportJob."""
    with _session_scope(session) as sess:
        document = generate_document(
            template_slug=template_slug,
            portfolio_id=portfolio_id,
            compare_to=compare_to,
            narrate=narrate,
            session=sess,
        )
        job = ReportJob(
            report_type=document["template"]["persona"],
            status=_status_from_document(document),
            request_payload={
                "template_slug": template_slug,
                "portfolio_id": portfolio_id,
                "compare_to": compare_to,
                "title": document["template"]["title"],
            },
            result_payload=document,
            artifact_paths={},
            template_slug=template_slug,
            compare_to_run_id=document["params"]["compare_to_run_id"],
        )
        sess.add(job)
        sess.flush()
        return job


__all__ = [
    "Narrator",
    "TemplateNotFound",
    "generate_document",
    "generate_report",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reporting_generate.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Generate a real report end to end**

Run:

```bash
.venv/bin/python -c "
from app.services.reporting import blocks
from app.services.reporting.generate import generate_document
doc = generate_document(template_slug='portfolio-snapshot', portfolio_id=2, narrate=None)
print('template :', doc['template']['slug'], 'v', doc['template']['version'])
print('sections :', len(doc['sections']))
for s in doc['sections']:
    for b in s['blocks']:
        print(f\"  {s['id']:12} {b['key']:24} {b['result']['status']}\")
"
```

Expected: three sections resolve against the live database. `portfolio-snapshot` has no
narrative, so `narrate=None` is sufficient and no LLM is involved.

- [ ] **Step 6: Confirm the honest-empty path renders as designed**

Run:

```bash
.venv/bin/python -c "
from app.services.reporting import blocks
from app.services.reporting.generate import generate_document
doc = generate_document(template_slug='risk-manager-daily', portfolio_id=2, narrate=None)
for s in doc['sections']:
    for b in s['blocks']:
        r = b['result']
        print(f\"{b['key']:32} {r['status']:12} {(r['reason'] or '')[:60]}\")
"
```

Expected: `scenario.latest_grid` reports `unavailable` with a reason naming the absent
scenario run — the tri-state working in the shipped product, not just in a test.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/reporting/generate.py tests/test_reporting_generate.py
git commit -m "feat(reporting): generation pipeline with injected narrator"
```

---

## Self-Review

**Spec coverage (§5.1, §5.5, §5.6):**

| Spec requirement | Task |
|---|---|
| Server resolves blocks; agent returns prose only | Task 3 (`narrate` returns `str`, slotted into `section["narrative"]`) |
| Agent sees each block's `status` and `reason` | Task 2 (`narrator_brief`) + test 3 |
| Grounding guard over narrative | Task 1 + Task 3 test 5 |
| Flags are non-blocking | Task 3, test 5 |
| `narrative: null` sections skip the LLM | Task 3, tests 1–2 |
| Zero-LLM template generates with no calls | Task 3, test 2 |
| `spec.meta.persona` selects the narrator | Task 3, test 3 |
| Sections narrated independently; one failure degrades one section | Task 3, test 6 |
| Document embeds spec + sha256 | Task 2, test 2 |
| Blocks resolved once even when reused | Task 3, test 8 |
| Persisted into `ReportJob.result_payload` with `template_slug` | Task 3 (`generate_report`) |

Not in this plan, correctly deferred to B4: the six agent tools and their registration
checklist, the persona-domain fix, the two SKILL.md files, and the REST router — including the
production narrator that binds a real model.

**Placeholder scan:** No TBD/TODO. Every step has runnable commands and complete code.

**Type consistency:**
- `narrate(persona: str, brief: dict) -> str` — same shape in the `Narrator` alias, every test
  lambda, and the `narrate(spec.meta.persona, brief)` call site.
- `narrator_brief(section, results)` positional — matches Task 2's definition and Task 3's call.
- `build_document(spec=, spec_yaml=, version=, params=, sections=, provenance=)` keyword-only —
  matches Task 2's definition and Task 3's call.
- `check_grounding(narrative, block_data, *, rel_tol=)` — Task 1's signature; Task 3 passes
  `[entry["result"]["data"] for entry in block_entries]`, a `list[dict]`, as declared.
- `resolve_block(key, ctx)` positional — matches B1 Task 1 and both monkeypatch lambdas.
- `BlockContext(portfolio_id=, compare_to_run_id=, params=)` — matches Plan A Task 1.
- `load_run_pair(portfolio_id=, risk_run_id=, compare_to_run_id=, session=)` keyword-only —
  matches Plan A Task 2.
- `get_template(slug=, session=)` keyword-only — matches B2 Task 4.
- `BlockResult.model_dump()` is pydantic v2, matching the `BaseModel` base in Plan A Task 1.

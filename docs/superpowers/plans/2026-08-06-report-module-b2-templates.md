# Report Module — Sub-project B2: Template Model, Validator & Seeds

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist report templates as validated YAML documents, with a validate-then-commit writer that rejects any template naming an unresolvable block or an incompatible renderer, and seed the four shipped templates.

**Architecture:** `report_templates` table with `spec` (YAML) as the source of truth and denormalized metadata columns extracted on save — the `DeskWorkflow` pattern. The validator resolves every `blocks[].key` through `require_block` and checks `render` against the block's declared `BlockShape`, so a dangling reference fails at HTTP 422 with the offending key named rather than rendering as a silent gap.

**Tech Stack:** Python 3.11, SQLAlchemy 2.x, Alembic, pydantic v2, PyYAML, pytest.

**Spec:** `docs/superpowers/specs/2026-08-06-report-module-redesign-design.md` §5.3, §5.4, §6
**Depends on:** `2026-08-06-report-module-b-blocks-continued.md` Task 5 complete — all 18 blocks registered.

## Global Constraints

- **Validate-then-commit.** The full check runs before anything persists. On failure the live row is byte-unchanged and the API returns 422. Mirrors `channel_registry_writer._mutate`.
- **Migrations use migration-local Core tables, never ORM models.** Importing `app.models` inside a migration couples it to a schema that will drift.
- **Alembic is the upgrade path:** `.venv/bin/python -m alembic upgrade head`. The live DB can lag head; a 500 from a new feature usually means migrations are behind.
- **Latest existing revision is `0052_trade_confirmations`.** New revisions are `0053` and `0054`.
- **Test command:** `.venv/bin/python -m pytest` from the repo root. Never pipe through `tail`.
- **YAML only, never Python.** A template cannot express execution, so agent authoring needs no sandbox or eval gate.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/app/services/reporting/template_spec.py` | `TemplateSpec` / `SectionSpec` / `BlockRef` pydantic models; YAML parse; `validate_spec` |
| `backend/app/services/reporting/templates.py` | Store: `list_templates`, `get_template`, `save_template`, `delete_template` — validate-then-commit |
| `backend/app/services/reporting/renderers.py` | `RENDERER_SHAPES` — which `render` accepts which `BlockShape` |
| `backend/app/services/reporting/seeds/*.yaml` | The four shipped template specs |
| `backend/alembic/versions/0053_report_templates.py` | `report_templates` table + `report_jobs` columns |
| `backend/alembic/versions/0054_seed_report_templates.py` | Seed the four templates |
| `tests/test_reporting_template_spec.py` | Spec parsing and validation |
| `tests/test_reporting_templates_store.py` | Store behaviour incl. rejection leaving rows unchanged |
| `tests/test_reporting_seeds.py` | Every seeded template validates against the live registry |

---

### Task 1: Renderer compatibility map

**Files:**
- Create: `backend/app/services/reporting/renderers.py`
- Test: `tests/test_reporting_template_spec.py` (first two tests only)

**Interfaces:**
- Produces:
  - `RENDERER_SHAPES: dict[str, frozenset[BlockShape]]` — renderer name → shapes it accepts
  - `renderer_accepts(render: str, shape: BlockShape) -> bool`
  - `known_renderers() -> list[str]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_reporting_template_spec.py` with just these two tests for now:

```python
import pytest

from app.services.reporting.contracts import BlockShape
from app.services.reporting.renderers import (
    RENDERER_SHAPES,
    known_renderers,
    renderer_accepts,
)


def test_every_block_shape_has_at_least_one_renderer():
    covered = {shape for shapes in RENDERER_SHAPES.values() for shape in shapes}
    assert covered == set(BlockShape)


def test_renderer_accepts_only_its_declared_shapes():
    assert renderer_accepts("waterfall", BlockShape.WATERFALL) is True
    assert renderer_accepts("waterfall", BlockShape.SCALARS) is False
    assert renderer_accepts("metric_row", BlockShape.SCALARS) is True
    assert renderer_accepts("delta_metric_row", BlockShape.SCALARS_WITH_PRIOR) is True
    assert renderer_accepts("nope", BlockShape.SCALARS) is False
    assert set(known_renderers()) == set(RENDERER_SHAPES)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reporting_template_spec.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.reporting.renderers'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/services/reporting/renderers.py`:

```python
"""Which renderer accepts which block shape.

This map is what lets a template save reject `render: waterfall` on a scalar
block at HTTP 422 instead of producing a broken panel at render time. The
frontend has a component per key here; adding a renderer means adding both.
"""
from __future__ import annotations

from .contracts import BlockShape

RENDERER_SHAPES: dict[str, frozenset[BlockShape]] = {
    "metric_row": frozenset({BlockShape.SCALARS}),
    "delta_metric_row": frozenset({BlockShape.SCALARS_WITH_PRIOR}),
    "table": frozenset({BlockShape.ROWS}),
    "bar_chart": frozenset({BlockShape.SERIES}),
    "line_chart": frozenset({BlockShape.SERIES}),
    "callout": frozenset({BlockShape.ITEMS}),
    "greeks_table": frozenset({BlockShape.POSITION_GREEKS}),
    "waterfall": frozenset({BlockShape.WATERFALL}),
}


def renderer_accepts(render: str, shape: BlockShape) -> bool:
    return shape in RENDERER_SHAPES.get(render, frozenset())


def known_renderers() -> list[str]:
    return sorted(RENDERER_SHAPES)


__all__ = ["RENDERER_SHAPES", "renderer_accepts", "known_renderers"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reporting_template_spec.py -v`
Expected: PASS, 2 tests

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/reporting/renderers.py tests/test_reporting_template_spec.py
git commit -m "feat(reporting): renderer/shape compatibility map"
```

---

### Task 2: Template spec parsing and validation

**Files:**
- Create: `backend/app/services/reporting/template_spec.py`
- Modify: `tests/test_reporting_template_spec.py` (append)

**Interfaces:**
- Consumes: `require_block`, `UnknownBlockError` (B1 Task 1); `renderer_accepts`, `known_renderers` (Task 1)
- Produces:
  - `BlockRef` — `{key: str, render: str, fields: list[str] | None, params: dict}`
  - `SectionSpec` — `{id: str, title: str, blocks: list[BlockRef], narrative: str | None}`
  - `TemplateMeta` — `{slug, title, persona, description, params: list[dict]}`
  - `TemplateSpec` — `{meta: TemplateMeta, sections: list[SectionSpec]}`
  - `parse_spec(yaml_text: str) -> TemplateSpec` — raises `TemplateSpecError` on malformed YAML/schema
  - `validate_spec(spec: TemplateSpec) -> None` — raises `TemplateSpecError` naming the offending key
  - `TemplateSpecError(Exception)` with `.errors: list[str]`
  - `VALID_PERSONAS = ("trader", "risk_manager", "high_board")`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_reporting_template_spec.py`:

```python
from app.services.reporting import blocks  # noqa: F401,E402 - registers producers
from app.services.reporting.template_spec import (  # noqa: E402
    TemplateSpecError,
    parse_spec,
    validate_spec,
)

GOOD = """
meta:
  slug: demo-daily
  title: Demo Daily
  persona: risk_manager
  description: A demo template.
  params:
    - { name: portfolio_id, type: portfolio, required: true }
sections:
  - id: headline
    title: Headline
    blocks:
      - { key: risk.totals, render: metric_row, fields: [delta_cash, vega] }
    narrative: |
      State whether the book is within limits.
  - id: data_only
    title: Data only
    blocks:
      - { key: coverage.evidence, render: metric_row }
"""


def _spec(text):
    spec = parse_spec(text)
    validate_spec(spec)
    return spec


def test_a_well_formed_spec_parses_and_validates():
    spec = _spec(GOOD)
    assert spec.meta.slug == "demo-daily"
    assert spec.meta.persona == "risk_manager"
    assert len(spec.sections) == 2
    assert spec.sections[0].blocks[0].key == "risk.totals"
    assert spec.sections[0].blocks[0].fields == ["delta_cash", "vega"]
    assert spec.sections[1].narrative is None


def test_malformed_yaml_raises_template_spec_error():
    with pytest.raises(TemplateSpecError):
        parse_spec("meta: [unclosed")


def test_unknown_block_key_is_rejected_and_named():
    text = GOOD.replace("risk.totals", "risk.does_not_exist")
    with pytest.raises(TemplateSpecError) as exc:
        _spec(text)
    assert "risk.does_not_exist" in str(exc.value)


def test_incompatible_renderer_is_rejected_and_named():
    text = GOOD.replace("render: metric_row, fields: [delta_cash, vega]",
                        "render: waterfall")
    with pytest.raises(TemplateSpecError) as exc:
        _spec(text)
    assert "waterfall" in str(exc.value)
    assert "risk.totals" in str(exc.value)


def test_unknown_renderer_is_rejected():
    text = GOOD.replace("render: metric_row, fields: [delta_cash, vega]",
                        "render: hologram")
    with pytest.raises(TemplateSpecError) as exc:
        _spec(text)
    assert "hologram" in str(exc.value)


def test_unknown_persona_is_rejected():
    text = GOOD.replace("persona: risk_manager", "persona: chief_vibes_officer")
    with pytest.raises(TemplateSpecError) as exc:
        _spec(text)
    assert "chief_vibes_officer" in str(exc.value)


def test_non_kebab_slug_is_rejected():
    text = GOOD.replace("slug: demo-daily", "slug: Demo_Daily")
    with pytest.raises(TemplateSpecError):
        _spec(text)


def test_duplicate_section_ids_are_rejected():
    text = GOOD.replace("id: data_only", "id: headline")
    with pytest.raises(TemplateSpecError) as exc:
        _spec(text)
    assert "headline" in str(exc.value)


def test_a_spec_with_no_sections_is_rejected():
    with pytest.raises(TemplateSpecError):
        _spec("meta:\n  slug: x-y\n  title: X\n  persona: trader\nsections: []\n")


def test_all_errors_are_collected_not_just_the_first():
    text = GOOD.replace("risk.totals", "risk.nope").replace(
        "persona: risk_manager", "persona: nobody"
    )
    with pytest.raises(TemplateSpecError) as exc:
        _spec(text)
    assert len(exc.value.errors) >= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reporting_template_spec.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.reporting.template_spec'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/services/reporting/template_spec.py`:

```python
"""Report template spec: the YAML document a template stores.

A spec declares which deterministic blocks each section shows and, optionally,
a narrative brief for the agent. It cannot express execution — that is why an
agent may author one without a sandbox.

``validate_spec`` collects every error rather than stopping at the first, so a
template author sees the whole list in one 422 instead of fixing errors one
round-trip at a time.
"""
from __future__ import annotations

import re
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from .contracts import BlockShape
from .registry import UnknownBlockError, require_block
from .renderers import known_renderers, renderer_accepts

VALID_PERSONAS: tuple[str, ...] = ("trader", "risk_manager", "high_board")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SECTION_ID_RE = re.compile(r"^[a-z0-9_]+$")


class TemplateSpecError(Exception):
    """Raised when a spec is malformed or references something unresolvable."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class BlockRef(BaseModel):
    key: str
    render: str
    fields: list[str] | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class SectionSpec(BaseModel):
    id: str
    title: str = ""
    blocks: list[BlockRef] = Field(default_factory=list)
    narrative: str | None = None


class TemplateMeta(BaseModel):
    slug: str
    title: str
    persona: str
    description: str = ""
    params: list[dict[str, Any]] = Field(default_factory=list)


class TemplateSpec(BaseModel):
    meta: TemplateMeta
    sections: list[SectionSpec]


def parse_spec(yaml_text: str) -> TemplateSpec:
    """Parse YAML into a TemplateSpec. Raises TemplateSpecError."""
    try:
        raw = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise TemplateSpecError([f"template spec is not valid YAML: {exc}"]) from exc
    if not isinstance(raw, dict):
        raise TemplateSpecError(
            ["template spec must be a YAML mapping with 'meta' and 'sections'"]
        )
    try:
        return TemplateSpec.model_validate(raw)
    except ValidationError as exc:
        errors = [
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        ]
        raise TemplateSpecError(errors) from exc


def validate_spec(spec: TemplateSpec) -> None:
    """Check every reference resolves. Raises TemplateSpecError listing all problems."""
    errors: list[str] = []

    if not _SLUG_RE.match(spec.meta.slug):
        errors.append(
            f"meta.slug {spec.meta.slug!r} must be kebab-case (lowercase, digits, hyphens)"
        )
    if spec.meta.persona not in VALID_PERSONAS:
        errors.append(
            f"meta.persona {spec.meta.persona!r} is not a known persona; "
            f"expected one of {list(VALID_PERSONAS)}"
        )
    if not spec.sections:
        errors.append("sections must contain at least one section")

    seen_ids: set[str] = set()
    for index, section in enumerate(spec.sections):
        where = f"sections[{index}]"
        if not _SECTION_ID_RE.match(section.id or ""):
            errors.append(
                f"{where}.id {section.id!r} must be lowercase letters, digits or underscores"
            )
        if section.id in seen_ids:
            errors.append(f"{where}.id {section.id!r} is duplicated")
        seen_ids.add(section.id)

        if not section.blocks and not (section.narrative or "").strip():
            errors.append(
                f"{where} declares neither blocks nor a narrative, so it would render empty"
            )

        for block_index, ref in enumerate(section.blocks):
            block_where = f"{where}.blocks[{block_index}]"
            try:
                block = require_block(ref.key)
            except UnknownBlockError:
                errors.append(
                    f"{block_where}.key {ref.key!r} is not a registered block"
                )
                continue

            if ref.render not in known_renderers():
                errors.append(
                    f"{block_where}.render {ref.render!r} is not a known renderer; "
                    f"expected one of {known_renderers()}"
                )
            elif not renderer_accepts(ref.render, block.shape):
                errors.append(
                    f"{block_where}: renderer {ref.render!r} cannot render block "
                    f"{ref.key!r}, whose shape is {block.shape.value!r}"
                )

    if errors:
        raise TemplateSpecError(errors)


def spec_block_keys(spec: TemplateSpec) -> list[str]:
    """Every distinct block key a spec references, in declaration order."""
    keys: list[str] = []
    for section in spec.sections:
        for ref in section.blocks:
            if ref.key not in keys:
                keys.append(ref.key)
    return keys


__all__ = [
    "VALID_PERSONAS",
    "BlockRef",
    "SectionSpec",
    "TemplateMeta",
    "TemplateSpec",
    "TemplateSpecError",
    "parse_spec",
    "validate_spec",
    "spec_block_keys",
]
```

Note `BlockShape` is imported for the type reference in `renderer_accepts` calls; if the linter
flags it as unused, remove the import — the call site passes `block.shape` directly.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reporting_template_spec.py -v`
Expected: PASS, 12 tests

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/reporting/template_spec.py tests/test_reporting_template_spec.py
git commit -m "feat(reporting): template spec parsing with all-errors validation"
```

---

### Task 3: `report_templates` migration and ORM model

**Files:**
- Create: `backend/alembic/versions/0053_report_templates.py`
- Modify: `backend/app/models.py` (add `ReportTemplate`; add two columns to `ReportJob` at line 2359+)
- Test: `tests/test_reporting_templates_store.py` (first test only)

**Interfaces:**
- Produces:
  - `ReportTemplate` ORM model — `{id, slug, title, persona, description, spec, source, version, created_at, updated_at}`
  - `ReportJob.template_slug: str | None`, `ReportJob.compare_to_run_id: int | None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_reporting_templates_store.py`:

```python
def test_report_template_model_and_report_job_columns_exist():
    from app.models import ReportJob, ReportTemplate

    columns = {column.name for column in ReportTemplate.__table__.columns}
    assert columns == {
        "id", "slug", "title", "persona", "description", "spec",
        "source", "version", "created_at", "updated_at",
    }
    job_columns = {column.name for column in ReportJob.__table__.columns}
    assert "template_slug" in job_columns
    assert "compare_to_run_id" in job_columns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reporting_templates_store.py -v`
Expected: FAIL with `ImportError: cannot import name 'ReportTemplate'`

- [ ] **Step 3: Add the ORM model**

In `backend/app/models.py`, immediately after the `ReportJob` class (which ends before
`class AuditEvent`), add:

```python
class ReportTemplate(Base):
    """A report template: declarative YAML naming blocks and narrative briefs.

    ``spec`` is the source of truth; ``title``/``persona``/``description`` are a
    denormalized cache extracted from ``spec.meta`` on save, mirroring how
    ``DeskWorkflow`` caches its script's ``meta`` literal.
    """

    __tablename__ = "report_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(160))
    persona: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text, default="")
    spec: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="user")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )
```

And inside the existing `ReportJob` class, after the `artifact_paths` column, add:

```python
    template_slug: Mapped[str | None] = mapped_column(
        String(80), nullable=True, index=True
    )
    compare_to_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

- [ ] **Step 4: Write the migration**

Create `backend/alembic/versions/0053_report_templates.py`:

```python
"""report templates table and report_jobs template columns

Revision ID: 0053_report_templates
Revises: 0052_trade_confirmations
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0053_report_templates"
down_revision = "0052_trade_confirmations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("persona", sa.String(length=40), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("spec", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False,
                  server_default="user"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_report_templates_slug", "report_templates", ["slug"], unique=True
    )

    with op.batch_alter_table("report_jobs") as batch:
        batch.add_column(sa.Column("template_slug", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("compare_to_run_id", sa.Integer(), nullable=True))
    op.create_index(
        "ix_report_jobs_template_slug", "report_jobs", ["template_slug"]
    )


def downgrade() -> None:
    op.drop_index("ix_report_jobs_template_slug", table_name="report_jobs")
    with op.batch_alter_table("report_jobs") as batch:
        batch.drop_column("compare_to_run_id")
        batch.drop_column("template_slug")
    op.drop_index("ix_report_templates_slug", table_name="report_templates")
    op.drop_table("report_templates")
```

`batch_alter_table` is required — SQLite cannot `ALTER TABLE ... ADD COLUMN` with an index in
one statement, and batch mode rebuilds the table safely.

Confirm the `down_revision` matches the real revision id:

```bash
grep -n "^revision" backend/alembic/versions/0052_trade_confirmations.py
```

If the id string differs from `0052_trade_confirmations`, use the actual value.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reporting_templates_store.py -v`
Expected: PASS, 1 test

- [ ] **Step 6: Apply the migration and verify**

Run:

```bash
.venv/bin/python -m alembic upgrade head
.venv/bin/python -c "
import sqlite3
con = sqlite3.connect('data/open_otc.sqlite3')
print([r[1] for r in con.execute('PRAGMA table_info(report_templates)')])
print([r[1] for r in con.execute('PRAGMA table_info(report_jobs)')])
"
```

Expected: `report_templates` has its 10 columns; `report_jobs` now includes `template_slug`
and `compare_to_run_id`.

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/0053_report_templates.py \
        backend/app/models.py \
        tests/test_reporting_templates_store.py
git commit -m "feat(reporting): report_templates table and report_jobs template columns"
```

---

### Task 4: Template store with validate-then-commit

**Files:**
- Create: `backend/app/services/reporting/templates.py`
- Modify: `tests/test_reporting_templates_store.py` (append)

**Interfaces:**
- Consumes: `parse_spec`, `validate_spec`, `TemplateSpecError`, `spec_block_keys` (Task 2); `ReportTemplate` (Task 3)
- Produces:
  - `list_templates(persona=None, session=None) -> list[ReportTemplate]`
  - `get_template(slug, session=None) -> ReportTemplate | None`
  - `save_template(slug, spec_yaml, source="user", session=None) -> ReportTemplate` — creates or updates; raises `TemplateSpecError` before any write
  - `delete_template(slug, session=None) -> bool` — refuses to delete `source='seed'`, raising `TemplateProtectedError`
  - `TemplateProtectedError(Exception)`
  - `validate_only(spec_yaml) -> list[str]` — returns `[]` when valid, error list otherwise; never raises

- [ ] **Step 1: Write the failing test**

Append to `tests/test_reporting_templates_store.py`:

```python
import pytest

from app.services.reporting import blocks  # noqa: F401 - registers producers
from app.services.reporting.template_spec import TemplateSpecError

GOOD = """
meta:
  slug: demo-daily
  title: Demo Daily
  persona: risk_manager
  description: A demo template.
sections:
  - id: headline
    title: Headline
    blocks:
      - { key: risk.totals, render: metric_row }
    narrative: State the position.
"""


def test_save_creates_a_template_and_caches_its_metadata(db_session):
    from app.services.reporting import templates

    row = templates.save_template(
        slug="demo-daily", spec_yaml=GOOD, session=db_session
    )
    assert row.slug == "demo-daily"
    assert row.title == "Demo Daily"
    assert row.persona == "risk_manager"
    assert row.description == "A demo template."
    assert row.version == 1
    assert row.source == "user"


def test_saving_again_bumps_the_version(db_session):
    from app.services.reporting import templates

    templates.save_template(slug="demo-daily", spec_yaml=GOOD, session=db_session)
    updated = GOOD.replace("title: Demo Daily", "title: Demo Daily v2")
    row = templates.save_template(
        slug="demo-daily", spec_yaml=updated, session=db_session
    )
    assert row.version == 2
    assert row.title == "Demo Daily v2"


def test_an_invalid_spec_is_rejected_before_anything_persists(db_session):
    from app.models import ReportTemplate
    from app.services.reporting import templates

    templates.save_template(slug="demo-daily", spec_yaml=GOOD, session=db_session)
    before = db_session.get(ReportTemplate, 1).spec

    bad = GOOD.replace("risk.totals", "risk.does_not_exist")
    with pytest.raises(TemplateSpecError) as exc:
        templates.save_template(slug="demo-daily", spec_yaml=bad, session=db_session)
    assert "risk.does_not_exist" in str(exc.value)

    db_session.rollback()
    assert db_session.get(ReportTemplate, 1).spec == before
    assert db_session.get(ReportTemplate, 1).version == 1


def test_slug_mismatch_between_argument_and_spec_is_rejected(db_session):
    from app.services.reporting import templates

    with pytest.raises(TemplateSpecError) as exc:
        templates.save_template(slug="other-slug", spec_yaml=GOOD, session=db_session)
    assert "demo-daily" in str(exc.value)


def test_seed_templates_cannot_be_deleted(db_session):
    from app.services.reporting import templates
    from app.services.reporting.templates import TemplateProtectedError

    templates.save_template(
        slug="demo-daily", spec_yaml=GOOD, source="seed", session=db_session
    )
    with pytest.raises(TemplateProtectedError):
        templates.delete_template(slug="demo-daily", session=db_session)


def test_user_templates_can_be_deleted(db_session):
    from app.services.reporting import templates

    templates.save_template(slug="demo-daily", spec_yaml=GOOD, session=db_session)
    assert templates.delete_template(slug="demo-daily", session=db_session) is True
    assert templates.get_template(slug="demo-daily", session=db_session) is None


def test_validate_only_returns_errors_without_raising():
    from app.services.reporting import templates

    assert templates.validate_only(GOOD) == []
    errors = templates.validate_only(GOOD.replace("risk.totals", "risk.nope"))
    assert errors and any("risk.nope" in message for message in errors)


def test_list_filters_by_persona(db_session):
    from app.services.reporting import templates

    templates.save_template(slug="demo-daily", spec_yaml=GOOD, session=db_session)
    trader = GOOD.replace("slug: demo-daily", "slug: trader-demo").replace(
        "persona: risk_manager", "persona: trader"
    )
    templates.save_template(slug="trader-demo", spec_yaml=trader, session=db_session)

    rows = templates.list_templates(persona="trader", session=db_session)
    assert [row.slug for row in rows] == ["trader-demo"]
```

These tests use a `db_session` fixture. Check whether `tests/conftest.py` already provides one:

```bash
grep -n "def db_session\|def session" tests/conftest.py
```

If it does not exist, add to `tests/conftest.py`:

```python
@pytest.fixture
def db_session(tmp_path, monkeypatch):
    """An isolated SQLite session with the full schema created."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base

    engine = create_engine(f"sqlite:///{tmp_path/'test.sqlite3'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reporting_templates_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.reporting.templates'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/services/reporting/templates.py`:

```python
"""Report template store: validate-then-commit over the report_templates table.

Every write runs the full parse + reference check before touching the row, so a
rejected save leaves the live template byte-unchanged (HTTP 422) rather than
persisting a template that would render as a gap. This mirrors
``channel_registry_writer._mutate``.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session

from app import database
from app.models import ReportTemplate

from .template_spec import (
    TemplateSpec,
    TemplateSpecError,
    parse_spec,
    validate_spec,
)


class TemplateProtectedError(Exception):
    """Raised when a seeded template is deleted."""


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def _checked_spec(slug: str, spec_yaml: str) -> TemplateSpec:
    """Parse, validate, and confirm the spec's slug matches the target slug."""
    spec = parse_spec(spec_yaml)
    validate_spec(spec)
    if spec.meta.slug != slug:
        raise TemplateSpecError(
            [
                f"meta.slug {spec.meta.slug!r} does not match the target slug "
                f"{slug!r}; a template's spec owns its slug"
            ]
        )
    return spec


def validate_only(spec_yaml: str) -> list[str]:
    """Return validation errors for a spec without saving. Never raises."""
    try:
        spec = parse_spec(spec_yaml)
        validate_spec(spec)
    except TemplateSpecError as exc:
        return list(exc.errors)
    return []


def list_templates(
    *, persona: str | None = None, session: Session | None = None
) -> list[ReportTemplate]:
    with _session_scope(session) as sess:
        query = sess.query(ReportTemplate)
        if persona is not None:
            query = query.filter(ReportTemplate.persona == persona)
        return list(query.order_by(ReportTemplate.slug).all())


def get_template(
    *, slug: str, session: Session | None = None
) -> ReportTemplate | None:
    with _session_scope(session) as sess:
        return (
            sess.query(ReportTemplate)
            .filter(ReportTemplate.slug == slug)
            .one_or_none()
        )


def save_template(
    *,
    slug: str,
    spec_yaml: str,
    source: str = "user",
    session: Session | None = None,
) -> ReportTemplate:
    """Create or update a template. Validates fully BEFORE any mutation."""
    spec = _checked_spec(slug, spec_yaml)

    with _session_scope(session) as sess:
        row = (
            sess.query(ReportTemplate)
            .filter(ReportTemplate.slug == slug)
            .one_or_none()
        )
        if row is None:
            row = ReportTemplate(slug=slug, source=source, version=1)
            sess.add(row)
        else:
            row.version = (row.version or 0) + 1

        row.title = spec.meta.title
        row.persona = spec.meta.persona
        row.description = spec.meta.description
        row.spec = spec_yaml
        sess.flush()
        return row


def delete_template(*, slug: str, session: Session | None = None) -> bool:
    with _session_scope(session) as sess:
        row = (
            sess.query(ReportTemplate)
            .filter(ReportTemplate.slug == slug)
            .one_or_none()
        )
        if row is None:
            return False
        if row.source == "seed":
            raise TemplateProtectedError(
                f"template {slug!r} is seeded and cannot be deleted; "
                "edit it or create a new template instead"
            )
        sess.delete(row)
        sess.flush()
        return True


__all__ = [
    "TemplateProtectedError",
    "validate_only",
    "list_templates",
    "get_template",
    "save_template",
    "delete_template",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reporting_templates_store.py -v`
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/reporting/templates.py \
        tests/test_reporting_templates_store.py tests/conftest.py
git commit -m "feat(reporting): template store with validate-then-commit"
```

---

## Remaining tasks in this plan

Task 5 (the four seeded template YAML files plus migration `0054`) and Task 6 (a guard test
asserting every seeded template validates against the live registry) are specified in
`docs/superpowers/plans/2026-08-06-report-module-b2-seeds.md`.

---

## Self-Review

**Spec coverage (§5.3, §5.4):**

| Spec requirement | Task |
|---|---|
| `report_templates` table, spec as source of truth, metadata cached | Task 3 |
| `report_jobs.template_slug` / `compare_to_run_id` | Task 3 |
| `source` = seed / user / agent | Tasks 3–4 |
| `version` bumped on save | Task 4, test 2 |
| YAML spec format with `meta` + `sections` + `narrative: null` legal | Task 2, test 1 |
| Unknown block key → 422 naming it | Task 2, test 3 |
| Renderer/shape incompatibility fails at save | Task 2, test 4; Task 1 |
| Unknown renderer, persona, slug format, duplicate section id | Task 2, tests 5–8 |
| Rejected save leaves the live row byte-unchanged | Task 4, test 3 |
| `validate` endpoint support (errors without raising) | Task 4 (`validate_only`) |
| Seeded templates | Task 5 (companion file) |

Not in this plan, correctly deferred: the generation pipeline, grounding guard, agent tools,
REST router, and every frontend concern.

**Placeholder scan:** No TBD/TODO. Two steps require a check-then-adapt (`down_revision` id,
`db_session` fixture existence) — both give the exact command and the exact fallback code.

**Type consistency:**
- `parse_spec(yaml_text)` / `validate_spec(spec)` / `spec_block_keys(spec)` — same signatures in
  Task 2's definition and Task 4's `_checked_spec`.
- `TemplateSpecError.errors: list[str]` — set in Task 2, read in Task 2's test 10 and Task 4's
  `validate_only`.
- `save_template(slug=, spec_yaml=, source=, session=)` keyword-only in both definition and
  every test call.
- `get_template(slug=, session=)` / `delete_template(slug=, session=)` / `list_templates(persona=, session=)`
  — keyword-only throughout.
- `require_block` raises `UnknownBlockError`, caught by name in Task 2 — matches B1 Task 1.
- `renderer_accepts(render, shape)` positional — matches Task 1's definition and Task 2's call.

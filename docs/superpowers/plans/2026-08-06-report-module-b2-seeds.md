# Report Module — Sub-project B2 (continued): Seeded Templates

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the four templates — trader, risk manager, high board, and the `portfolio-snapshot` compatibility template that the retained `create_report` tool routes through.

**Architecture:** Each template is a YAML file under `backend/app/services/reporting/seeds/`, loaded and inserted by migration `0054` with `source='seed'`. A guard test parses every seed file through the real validator against the real registry, so a seed can never reference a block that does not exist.

**Tech Stack:** Python 3.11, Alembic, PyYAML, pytest.

**Spec:** `docs/superpowers/specs/2026-08-06-report-module-redesign-design.md` §6
**Depends on:** `2026-08-06-report-module-b2-templates.md` Task 4 complete — store and validator green.

## Global Constraints

- **Seed files are the source of truth for seeded templates.** The migration reads them; it does not embed YAML inline. A seed edit plus a re-run of the seed migration is the update path.
- **Migrations use migration-local Core tables, never ORM models.**
- **Every seed must validate against the live registry.** Task 2's guard test is what makes that true rather than hoped-for.
- **`narrative: null` (or an omitted `narrative`) is legal** and means "no agent involvement in this section".
- **Test command:** `.venv/bin/python -m pytest` from the repo root. Never pipe through `tail`.

---

### Task 1: The four seed YAML files

**Files:**
- Create: `backend/app/services/reporting/seeds/__init__.py`
- Create: `backend/app/services/reporting/seeds/trader-daily.yaml`
- Create: `backend/app/services/reporting/seeds/risk-manager-daily.yaml`
- Create: `backend/app/services/reporting/seeds/high-board-daily.yaml`
- Create: `backend/app/services/reporting/seeds/portfolio-snapshot.yaml`
- Test: `tests/test_reporting_seeds.py`

**Interfaces:**
- Produces:
  - `SEEDS_DIR: Path` and `load_seed_specs() -> dict[str, str]` (slug → YAML text) in `seeds/__init__.py`
  - Four seed slugs: `trader-daily`, `risk-manager-daily`, `high-board-daily`, `portfolio-snapshot`

- [ ] **Step 1: Write the failing test**

Create `tests/test_reporting_seeds.py`:

```python
import pytest

from app.services.reporting import blocks  # noqa: F401 - registers producers
from app.services.reporting.seeds import load_seed_specs
from app.services.reporting.template_spec import parse_spec, validate_spec

EXPECTED_SLUGS = {
    "trader-daily",
    "risk-manager-daily",
    "high-board-daily",
    "portfolio-snapshot",
}


def test_all_four_seeds_are_present():
    assert set(load_seed_specs()) == EXPECTED_SLUGS


@pytest.mark.parametrize("slug", sorted(EXPECTED_SLUGS))
def test_every_seed_validates_against_the_live_registry(slug):
    """A seed that names a nonexistent block must fail here, not in production."""
    spec = parse_spec(load_seed_specs()[slug])
    validate_spec(spec)
    assert spec.meta.slug == slug


def test_seed_filename_matches_its_declared_slug():
    for slug, text in load_seed_specs().items():
        assert parse_spec(text).meta.slug == slug


def test_high_board_template_is_narrative_light():
    """The board one-pager has exactly one narrating section, so it costs one LLM call."""
    spec = parse_spec(load_seed_specs()["high-board-daily"])
    narrating = [s for s in spec.sections if (s.narrative or "").strip()]
    assert len(narrating) == 1
    assert narrating[0].id == "executive_summary"


def test_portfolio_snapshot_is_fully_deterministic():
    """The create_report compatibility template must generate with ZERO LLM calls."""
    spec = parse_spec(load_seed_specs()["portfolio-snapshot"])
    assert all(not (s.narrative or "").strip() for s in spec.sections)


def test_risk_manager_template_includes_the_honest_empty_stress_section():
    """scenario.latest_grid ships knowing it renders 'unavailable' today."""
    spec = parse_spec(load_seed_specs()["risk-manager-daily"])
    keys = [ref.key for section in spec.sections for ref in section.blocks]
    assert "scenario.latest_grid" in keys


def test_trader_template_covers_the_daily_story():
    spec = parse_spec(load_seed_specs()["trader-daily"])
    keys = {ref.key for section in spec.sections for ref in section.blocks}
    assert {"pnl.daily", "pnl.explain", "pnl.by_position"} <= keys


def test_every_persona_has_at_least_one_seeded_template():
    personas = {parse_spec(text).meta.persona for text in load_seed_specs().values()}
    assert {"trader", "risk_manager", "high_board"} <= personas
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reporting_seeds.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.reporting.seeds'`

- [ ] **Step 3: Create the seed loader**

Create `backend/app/services/reporting/seeds/__init__.py`:

```python
"""Seeded report templates shipped with the product.

The YAML files here are the source of truth for the four templates migration
0054 inserts with ``source='seed'``. A guard test validates every one of them
against the live block registry, so a seed can never reference a block that
does not exist.
"""
from __future__ import annotations

from pathlib import Path

SEEDS_DIR = Path(__file__).parent


def load_seed_specs() -> dict[str, str]:
    """Return {slug: yaml_text} for every shipped template, keyed by filename stem."""
    return {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted(SEEDS_DIR.glob("*.yaml"))
    }


__all__ = ["SEEDS_DIR", "load_seed_specs"]
```

- [ ] **Step 4: Write `trader-daily.yaml`**

Create `backend/app/services/reporting/seeds/trader-daily.yaml`:

```yaml
meta:
  slug: trader-daily
  title: Trader — Daily Book Review
  persona: trader
  description: >
    Overnight P&L, what drove it, and what needs attention at the open.
  params:
    - { name: portfolio_id, type: portfolio, required: true }
    - { name: compare_to, type: risk_run_ref, default: previous }

sections:
  - id: overnight
    title: Overnight
    blocks:
      - { key: pnl.daily, render: delta_metric_row }
      - key: risk.totals
        render: metric_row
        fields: [delta_cash, gamma_cash, vega, theta]
    narrative: |
      One paragraph. State the overnight market-value move and the single
      largest driver behind it. If the coverage block shows fewer positions
      priced than the book holds, say so in the first sentence — a P&L number
      over an incomplete book is not the book's P&L. If the position set
      changed between runs, say that too, because part of the move is then
      composition rather than price.

  - id: attribution
    title: What moved it
    blocks:
      - { key: pnl.explain, render: waterfall }
    narrative: |
      Explain the decomposition in plain terms: which Greek carried the move.
      If `residual_exceeds_threshold` is true, state plainly that the
      attribution does not fully explain the move and do not speculate about
      why. If any positions were excluded, name how many and why.

  - id: movers
    title: Position movers
    blocks:
      - key: pnl.by_position
        render: table
        params: { top_n: 10 }
    narrative: |
      Call out any position whose move is large relative to the others.
      Do not restate the table row by row.

  - id: exposure
    title: Exposure
    blocks:
      - { key: risk.exposure_by_underlying, render: bar_chart }
    narrative: |
      Name the concentrations. If one underlying carries most of the delta,
      say so explicitly.

  - id: barrier_watch
    title: Barrier and KO watch
    blocks:
      - { key: positions.barrier_proximity, render: callout }
    narrative: |
      Flag anything close to a barrier by days or by level. If this block is
      empty, say the book carries no tracked barriers rather than implying
      nothing is at risk.

  - id: pipeline
    title: RFQ pipeline
    blocks:
      - { key: rfq.open_pipeline, render: table }

  - id: book_changes
    title: Book changes
    blocks:
      - { key: positions.changes, render: table }
      - { key: pnl.inception, render: metric_row }
    narrative: |
      Note what was added or closed. For since-inception P&L, if
      `basis_missing_count` is above zero, state how many positions carry no
      cost basis so the reader knows the figure is partial.
```

- [ ] **Step 5: Write `risk-manager-daily.yaml`**

Create `backend/app/services/reporting/seeds/risk-manager-daily.yaml`:

```yaml
meta:
  slug: risk-manager-daily
  title: Risk — Daily Limit & Exposure Review
  persona: risk_manager
  description: >
    Limit status, concentration, and the evidence quality behind both.
  params:
    - { name: portfolio_id, type: portfolio, required: true }
    - { name: compare_to, type: risk_run_ref, default: previous }

sections:
  - id: limit_status
    title: Limit status
    blocks:
      - { key: limits.utilization, render: delta_metric_row }
      - { key: limits.breaches, render: callout }
    narrative: |
      Lead with whether the book is within limits. Read the breaches block's
      status carefully before writing this sentence:
        - status "ok" with items    -> name every breached limit and its scope.
        - status "empty"            -> no limit is in breach. Say so plainly.
        - status "unavailable"      -> the limit check DID NOT RUN. Say exactly
          that. Never write that the book is within limits when the check did
          not run.
      If `indeterminate_count` is above zero, name those limits separately:
      they could not be evaluated and are not passes.

  - id: incidents
    title: Incidents
    blocks:
      - { key: limits.incidents, render: table }
    narrative: |
      Summarise open and acknowledged incidents and who owns them. Note any
      incident that has been open across multiple sessions.

  - id: concentration
    title: Concentration
    blocks:
      - { key: risk.exposure_by_underlying, render: bar_chart }
      - { key: risk.greeks_by_bucket, render: greeks_table }
    narrative: |
      Identify concentration by underlying and by Greek. Say which single
      position or underlying would hurt most on an adverse move.

  - id: change
    title: Change since last run
    blocks:
      - { key: risk.exposure_diff, render: delta_metric_row }
    narrative: |
      State what moved in exposure terms. If `composition_changed` is true,
      note that part of the change is the book itself changing, not the market.

  - id: evidence
    title: Evidence quality
    blocks:
      - { key: coverage.evidence, render: metric_row }
    narrative: |
      State how much of the book actually priced and how stale the market
      evidence is. If `evidence_complete` is false or `missing_evidence` is
      non-empty, list what is missing. This section is the reason a reader can
      trust the rest of the report — do not soften it.

  - id: stress
    title: Stress
    blocks:
      - { key: scenario.latest_grid, render: table }
    narrative: |
      Summarise the worst scenario outcome. If this block is unavailable, state
      that no stress run exists for this book and that the report therefore
      says nothing about tail risk.
```

- [ ] **Step 6: Write `high-board-daily.yaml`**

Create `backend/app/services/reporting/seeds/high-board-daily.yaml`:

```yaml
meta:
  slug: high-board-daily
  title: Board — Daily One-Pager
  persona: high_board
  description: >
    Governance one-pager: position, exceptions, agent activity, and evidence
    completeness, with a single executive summary.
  params:
    - { name: portfolio_id, type: portfolio, required: true }
    - { name: compare_to, type: risk_run_ref, default: previous }

sections:
  - id: position
    title: Position of the desk
    blocks:
      - key: risk.totals
        render: metric_row
        fields: [market_value, gross_notional, delta_cash, vega]
      - { key: pnl.daily, render: delta_metric_row }

  - id: exceptions
    title: Limit exceptions
    blocks:
      - { key: limits.breaches, render: callout }
      - { key: limits.incidents, render: table }

  - id: agent_actions
    title: Agent actions taken
    blocks:
      - key: audit.write_actions_summary
        render: table
        params: { window_days: 1 }

  - id: approvals
    title: Awaiting approval
    blocks:
      - { key: rfq.pending_approvals, render: table }

  - id: evidence
    title: Evidence completeness
    blocks:
      - { key: coverage.evidence, render: metric_row }

  - id: executive_summary
    title: Executive summary
    blocks: []
    narrative: |
      Three sentences, no more, for a reader who will not scroll.

      1. Where the desk stands and how it moved.
      2. Whether there are live limit exceptions, and if the limit check could
         not run, say that instead — never imply compliance that was not
         verified.
      3. Whether the evidence behind this report is complete, and what a board
         member should ask about next.

      Ground every number in the sections above. Do not introduce a figure that
      does not appear in this report.
```

- [ ] **Step 7: Write `portfolio-snapshot.yaml`**

Create `backend/app/services/reporting/seeds/portfolio-snapshot.yaml`:

```yaml
meta:
  slug: portfolio-snapshot
  title: Portfolio Snapshot
  persona: risk_manager
  description: >
    Point-in-time portfolio totals and positions. Compatibility template for
    the create_report tool; fully deterministic, no narrative, no LLM call.
  params:
    - { name: portfolio_id, type: portfolio, required: true }

sections:
  - id: totals
    title: Portfolio totals
    blocks:
      - { key: risk.totals, render: metric_row }

  - id: positions
    title: Positions
    blocks:
      - { key: risk.greeks_by_bucket, render: greeks_table }

  - id: evidence
    title: Evidence
    blocks:
      - { key: coverage.evidence, render: metric_row }
```

- [ ] **Step 8: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reporting_seeds.py -v`
Expected: PASS, 11 tests (8 named + 4 parametrized minus the 1 collapsed name = the
parametrized case runs 4 times)

If a seed fails validation, the error names the offending key or renderer — fix the YAML, not
the validator.

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/reporting/seeds/ tests/test_reporting_seeds.py
git commit -m "feat(reporting): four seeded report templates"
```

---

### Task 2: Seed migration

**Files:**
- Create: `backend/alembic/versions/0054_seed_report_templates.py`
- Modify: `tests/test_reporting_seeds.py` (append)

**Interfaces:**
- Consumes: `load_seed_specs` (Task 1)
- Produces: four `report_templates` rows with `source='seed'`, `version=1`

**Design note:** the migration reads the YAML files rather than embedding them, and uses a
migration-local Core table rather than the ORM model. It is idempotent: re-running updates the
`spec` of an existing seeded slug rather than inserting a duplicate, so a seed edit ships by
re-running the migration.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_reporting_seeds.py`:

```python
def test_seed_specs_parse_without_the_registry_for_migration_use():
    """The migration inserts raw YAML; it must be readable without app imports."""
    import yaml

    for slug, text in load_seed_specs().items():
        raw = yaml.safe_load(text)
        assert raw["meta"]["slug"] == slug
        assert raw["meta"]["title"]
        assert raw["meta"]["persona"]
        assert isinstance(raw["sections"], list) and raw["sections"]
```

- [ ] **Step 2: Run test to verify it passes already**

Run: `.venv/bin/python -m pytest tests/test_reporting_seeds.py::test_seed_specs_parse_without_the_registry_for_migration_use -v`
Expected: PASS — this test guards the migration's assumptions about the YAML shape, so it
passes as soon as Task 1's files exist. It fails later if someone restructures a seed.

- [ ] **Step 3: Write the migration**

Create `backend/alembic/versions/0054_seed_report_templates.py`:

```python
"""seed the four shipped report templates

Revision ID: 0054_seed_report_templates
Revises: 0053_report_templates

Reads the YAML seed files rather than embedding them, and upserts by slug so a
re-run picks up seed edits instead of inserting duplicates. Uses a
migration-local Core table, never the ORM model.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
import yaml
from alembic import op

revision = "0054_seed_report_templates"
down_revision = "0053_report_templates"
branch_labels = None
depends_on = None

_SEED_SLUGS = (
    "trader-daily",
    "risk-manager-daily",
    "high-board-daily",
    "portfolio-snapshot",
)

# Migration-local Core table. Deliberately not app.models.ReportTemplate: an
# ORM model tracks head, while a migration must describe the schema as it is at
# THIS revision.
report_templates = sa.table(
    "report_templates",
    sa.column("id", sa.Integer),
    sa.column("slug", sa.String),
    sa.column("title", sa.String),
    sa.column("persona", sa.String),
    sa.column("description", sa.Text),
    sa.column("spec", sa.Text),
    sa.column("source", sa.String),
    sa.column("version", sa.Integer),
    sa.column("created_at", sa.DateTime),
    sa.column("updated_at", sa.DateTime),
)


def _seeds_dir() -> Path:
    # backend/alembic/versions/ -> backend/app/services/reporting/seeds/
    return (
        Path(__file__).resolve().parents[2]
        / "app" / "services" / "reporting" / "seeds"
    )


def upgrade() -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    bind = op.get_bind()
    seeds_dir = _seeds_dir()

    for slug in _SEED_SLUGS:
        path = seeds_dir / f"{slug}.yaml"
        spec_text = path.read_text(encoding="utf-8")
        meta = (yaml.safe_load(spec_text) or {}).get("meta") or {}

        existing = bind.execute(
            sa.select(report_templates.c.id).where(report_templates.c.slug == slug)
        ).first()

        values = {
            "title": meta.get("title") or slug,
            "persona": meta.get("persona") or "risk_manager",
            "description": (meta.get("description") or "").strip(),
            "spec": spec_text,
            "source": "seed",
            "updated_at": now,
        }

        if existing is None:
            bind.execute(
                report_templates.insert().values(
                    slug=slug, version=1, created_at=now, **values
                )
            )
        else:
            bind.execute(
                report_templates.update()
                .where(report_templates.c.slug == slug)
                .values(**values)
            )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        report_templates.delete().where(
            sa.and_(
                report_templates.c.slug.in_(_SEED_SLUGS),
                report_templates.c.source == "seed",
            )
        )
    )
```

- [ ] **Step 4: Apply and verify**

Run:

```bash
.venv/bin/python -m alembic upgrade head
.venv/bin/python -c "
import sqlite3
con = sqlite3.connect('data/open_otc.sqlite3')
for row in con.execute('select slug, title, persona, source, version from report_templates order by slug'):
    print(row)
"
```

Expected: four rows, all `source='seed'`, `version=1`:
`high-board-daily`, `portfolio-snapshot`, `risk-manager-daily`, `trader-daily`.

- [ ] **Step 5: Verify idempotency**

Run:

```bash
.venv/bin/python -m alembic downgrade -1 && .venv/bin/python -m alembic upgrade head
.venv/bin/python -c "
import sqlite3
con = sqlite3.connect('data/open_otc.sqlite3')
print('rows:', con.execute('select count(*) from report_templates').fetchone()[0])
"
```

Expected: `rows: 4` — a down/up cycle produces exactly four rows, not eight.

- [ ] **Step 6: Verify seeded templates load through the store**

Run:

```bash
.venv/bin/python -c "
from app.services.reporting import blocks, templates
from app.services.reporting.template_spec import parse_spec, validate_spec
for row in templates.list_templates():
    spec = parse_spec(row.spec); validate_spec(spec)
    print(f'{row.slug:24} {row.persona:14} {len(spec.sections)} sections  source={row.source}')
"
```

Expected: four lines, no exception — every seeded row round-trips through the real validator.

- [ ] **Step 7: Run the full reporting suite**

Run: `.venv/bin/python -m pytest tests/ -k "reporting or pnl" -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/alembic/versions/0054_seed_report_templates.py tests/test_reporting_seeds.py
git commit -m "feat(reporting): seed the four shipped templates via migration 0054"
```

---

## Self-Review

**Spec coverage (§6):**

| Spec requirement | Task |
|---|---|
| §6.1 `trader-daily` — 7 sections incl. attribution waterfall, movers, barrier watch | Task 1, step 4 |
| §6.2 `risk-manager-daily` — limits, incidents, concentration, change, evidence, stress | Task 1, step 5 |
| §6.2 stress section ships as an honest `unavailable` | Task 1, step 5 + test 6 |
| §6.3 `high-board-daily` inverts the ratio: one narrative section | Task 1, step 6 + test 4 |
| §6.3 `audit.write_actions_summary` on the board template | Task 1, step 6 |
| §6.4 `portfolio-snapshot` compatibility, fully deterministic | Task 1, step 7 + test 5 |
| Seeded via migration with `source='seed'` | Task 2 |

**Placeholder scan:** No TBD/TODO. Every YAML file is complete and every command runnable.

**Type consistency:**
- Every `render` in every seed matches its block's declared `BlockShape` per B2 Task 1's
  `RENDERER_SHAPES`: `SCALARS`→`metric_row`, `SCALARS_WITH_PRIOR`→`delta_metric_row`,
  `ROWS`→`table`, `SERIES`→`bar_chart`, `ITEMS`→`callout`, `WATERFALL`→`waterfall`,
  `POSITION_GREEKS`→`greeks_table`. Task 1's parametrized test proves it against the real registry.
- `load_seed_specs() -> dict[str, str]` — same signature in the loader, the tests, and (by
  filename convention) the migration.
- `params: { top_n: 10 }` and `params: { window_days: 1 }` match the `ctx.params.get` reads in
  B1 Task 3 (`pnl.by_position`) and B1-continued Task 5 (`audit.write_actions_summary`).
- Section id `executive_summary` in the seed matches the assertion in Task 1's test 4.
- `blocks: []` on the executive-summary section is legal because that section declares a
  narrative — B2 Task 2's validator only rejects a section with neither.

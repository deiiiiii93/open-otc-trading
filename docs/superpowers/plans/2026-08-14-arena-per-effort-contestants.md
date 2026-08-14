# Arena Per-Effort Contestants Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one arena run rank the same model at several reasoning efforts as separate contestants on one board.

**Architecture:** A contestant becomes `(model_id, reasoning_effort)`. `ArenaMatch` gains an explicit `reasoning_effort` column (`''` = vendor default) and its unique constraint grows to four columns; `ArenaRun.reasoning_efforts` values become **lists** of levels, read backward-compatibly from the legacy scalar form. Execution, transcript paths, leaderboard grouping, merge grouping and `--resume` all gain the effort dimension.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 (`Mapped`/`mapped_column`), Alembic (SQLite), pytest, React 19 + TypeScript + vitest.

**Spec:** `docs/superpowers/specs/2026-08-14-arena-per-effort-contestants-design.md`

## Global Constraints

- **Backend tests:** `.venv/bin/python -m pytest` from the repo root. Never pipe through `tail` (it hides the summary).
- **Frontend tests:** `cd frontend && npm test`; type-check `npx tsc --noEmit`. The vitest suite is **flaky under load** — compare a failing-file set against a same-machine `main` run before blaming this branch.
- **Every migration after `0001` must be IDEMPOTENT.** `0001_initial` runs `Base.metadata.create_all`, so a fresh database reaches revision 1 already carrying today's full ORM schema. Guard all DDL on `inspect(op.get_bind())`.
- **Never `op.drop_column` directly** — always `op.batch_alter_table`. SQLite refuses `DROP COLUMN` while an FK definition names the column, and constraint changes require the batch rebuild.
- **`''` means vendor default** in `ArenaMatch.reasoning_effort`, never NULL. SQL treats NULLs as distinct in a UNIQUE constraint, which would silently stop protecting unpinned pairs.
- **Backfill reads `config`, never a blind `''`.** Runs #107/#108 already store `config.reasoning_effort`; flattening them to `''` would let `merge_runs` fold a `high` board into a `low` one.
- **A single-effort board must produce byte-identical leaderboard output to today.** This is the regression gate for Task 5.
- **Frontend styling is token-only.** Read `frontend/CLAUDE.md` and `frontend/UI_STYLE_GUIDE.md` before Task 9. Never invent a token — `--radius-1` and `--ink-3` are referenced by some page CSS but **defined nowhere**. Verify every token against `frontend/src/tokens/`.
- **`normalize_reasoning_effort`** (`services/deep_agent/model_factory.py:136`) returns `None` for empty/`None` and **raises `ValueError`** for an unsupported string. `"none"` is a real level meaning "skip reasoning" — never conflate it with absence.
- **`effort_rejection(registry, channel, provider, model, effort)`** is the SINGLE seam for "can this route carry this effort". Do not add a second check anywhere.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `backend/app/models.py` | `ArenaMatch.reasoning_effort` + 4-column unique constraint | 1 |
| `backend/alembic/versions/0058_arena_match_reasoning_effort.py` | Column add, config-derived backfill, constraint rebuild | 1 |
| `tests/test_migration_0058_arena_match_effort.py` | Fresh-chain safety, rebuild survival, backfill correctness | 1 |
| `backend/app/services/arena/store.py` | Effort-aware upsert, leaderboard grouping, merge grouping, dict serialization | 2, 5, 6 |
| `backend/app/services/arena/task.py` | `effort_levels_for`, launch validation, nested execution loop, transcript paths | 3, 4 |
| `backend/app/routers/arena.py` | Request/response shapes + leaderboard key projection | 7 |
| `scripts/launch_arena_run.py` | `--resume` effort dimension | 8 |
| `frontend/src/routes/Arena.live.tsx` + `.css` | Level checkbox group, payload mapping, row key, board chip | 9 |
| `frontend/src/lib/arenaApi.ts`, `frontend/src/types.ts` | Wire types | 9 |
| `CHANGELOG.md`, `CLAUDE.md` | Release note + subsystem gotchas | 10 |

---

### Task 1: `ArenaMatch.reasoning_effort` column and migration 0058

**Files:**
- Modify: `backend/app/models.py:2621-2648`
- Create: `backend/alembic/versions/0058_arena_match_reasoning_effort.py`
- Test: `tests/test_migration_0058_arena_match_effort.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ArenaMatch.reasoning_effort: str` (non-null, `''` = vendor default) and the unique constraint `uq_arena_match_run_workflow_model_effort` over `(run_id, workflow_id, model_id, reasoning_effort)`.

- [ ] **Step 1: Write the failing migration test**

Create `tests/test_migration_0058_arena_match_effort.py`:

```python
"""Round-trip tests for migration 0058_arena_match_reasoning_effort.

Drives the migration module directly (same style as test_arena_migration.py):
configure MigrationContext + Operations against a temp SQLite, call upgrade(),
then inspect() the result.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect


def _run_migration(module, method: str, engine: sa.Engine) -> None:
    connection = engine.connect()
    original_op = module.op
    module.op = Operations(MigrationContext.configure(connection))
    try:
        getattr(module, method)()
        connection.commit()
    finally:
        module.op = original_op
        connection.close()


def _migration():
    return importlib.import_module(
        "backend.alembic.versions.0058_arena_match_reasoning_effort"
    )


def _pre_0058_engine(tmp_path: Path) -> sa.Engine:
    """An arena_match shaped as it was BEFORE 0058: 3-column unique constraint."""
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'pre58.sqlite3'}")
    with engine.begin() as conn:
        conn.execute(sa.text("""
            CREATE TABLE arena_match (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL,
                workflow_id VARCHAR NOT NULL,
                model_id VARCHAR NOT NULL,
                status VARCHAR(40) NOT NULL,
                objective_score FLOAT,
                judged_score FLOAT,
                total_score FLOAT,
                judge_missing BOOLEAN NOT NULL DEFAULT 0,
                config JSON NOT NULL,
                score_breakdown JSON,
                transcript_path VARCHAR,
                error TEXT,
                created_at DATETIME NOT NULL,
                CONSTRAINT uq_arena_match_run_workflow_model
                    UNIQUE (run_id, workflow_id, model_id)
            )
        """))
        conn.execute(sa.text("CREATE INDEX ix_arena_match_run_id ON arena_match (run_id)"))
    return engine


def _insert(engine: sa.Engine, *, mid: int, model: str, config: dict) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO arena_match (id, run_id, workflow_id, model_id, status,"
                " judge_missing, config, created_at) VALUES (:i, 1, 'wf', :m,"
                " 'scored', 0, :c, '2026-08-14 00:00:00')"
            ),
            {"i": mid, "m": model, "c": json.dumps(config)},
        )


def test_upgrade_adds_column_and_four_column_constraint(tmp_path: Path) -> None:
    engine = _pre_0058_engine(tmp_path)
    _run_migration(_migration(), "upgrade", engine)

    insp = inspect(engine)
    assert "reasoning_effort" in {c["name"] for c in insp.get_columns("arena_match")}
    uniques = {u["name"]: u["column_names"] for u in insp.get_unique_constraints("arena_match")}
    assert "uq_arena_match_run_workflow_model_effort" in uniques
    assert uniques["uq_arena_match_run_workflow_model_effort"] == [
        "run_id", "workflow_id", "model_id", "reasoning_effort",
    ]
    assert "uq_arena_match_run_workflow_model" not in uniques


def test_backfill_derives_effort_from_config_not_blank(tmp_path: Path) -> None:
    """A historical pinned row keeps its regime; an unpinned one becomes ''.

    Blind-filling '' would tell the DB a high board and a low board were the same
    regime, and merge_runs — which now groups on this column — would fold them.
    """
    engine = _pre_0058_engine(tmp_path)
    _insert(engine, mid=1, model="pinned-high", config={"reasoning_effort": "high"})
    _insert(engine, mid=2, model="unpinned", config={"reasoning_effort": None})
    _insert(engine, mid=3, model="no-key", config={"weights": None})

    _run_migration(_migration(), "upgrade", engine)

    with engine.begin() as conn:
        got = dict(conn.execute(
            sa.text("SELECT model_id, reasoning_effort FROM arena_match")
        ).fetchall())
    assert got == {"pinned-high": "high", "unpinned": "", "no-key": ""}


def test_rebuild_preserves_rows_and_index(tmp_path: Path) -> None:
    """batch_alter_table rebuilds the table — rows and siblings must survive."""
    engine = _pre_0058_engine(tmp_path)
    _insert(engine, mid=1, model="m", config={"reasoning_effort": "low"})

    _run_migration(_migration(), "upgrade", engine)

    with engine.begin() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM arena_match")).scalar() == 1
    indexes = {i["name"] for i in inspect(engine).get_indexes("arena_match")}
    assert "ix_arena_match_run_id" in indexes


def test_upgrade_is_idempotent_on_a_fresh_orm_schema(tmp_path: Path) -> None:
    """0001 create_all already materialises the post-0058 shape — must be a no-op."""
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'fresh.sqlite3'}")
    from app.models import ArenaMatch
    ArenaMatch.__table__.create(bind=engine)

    _run_migration(_migration(), "upgrade", engine)   # must not raise

    uniques = {u["name"] for u in inspect(engine).get_unique_constraints("arena_match")}
    assert "uq_arena_match_run_workflow_model_effort" in uniques
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_migration_0058_arena_match_effort.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.alembic.versions.0058_arena_match_reasoning_effort`

- [ ] **Step 3: Add the column and constraint to the ORM**

In `backend/app/models.py`, inside `class ArenaMatch`, add the column after `model_id` (line 2627) and replace `__table_args__`:

```python
    model_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # Which effort regime produced this row. '' means the run did not pin one, so
    # the vendor default applied — NOT null, because SQL treats NULLs as distinct
    # in a UNIQUE constraint and two unpinned rows for one pair would both insert.
    reasoning_effort: Mapped[str] = mapped_column(
        String(20), nullable=False, default="", server_default="",
    )
```

```python
    __table_args__ = (
        # A contestant is (model, effort): the same model at low and at high are
        # two rows that rank against each other, never one averaged row. This
        # mirrors merge_runs' refusal to fold two efforts into one EFF/CON.
        UniqueConstraint(
            "run_id", "workflow_id", "model_id", "reasoning_effort",
            name="uq_arena_match_run_workflow_model_effort",
        ),
    )
```

- [ ] **Step 4: Write the migration**

Create `backend/alembic/versions/0058_arena_match_reasoning_effort.py`:

```python
"""arena_match: reasoning_effort column + effort in the contestant key

Revision ID: 0058_arena_match_reasoning_effort
Revises: 0057_arena_run_reasoning_efforts_map

A contestant becomes (model_id, reasoning_effort) so one board can rank the same
model at two efforts. The 3-column unique constraint made the second arm collide
with the first.

'' means "unpinned, vendor default" — not NULL, because SQL treats NULLs as
distinct in a UNIQUE constraint, so NULL would silently stop protecting unpinned
pairs at the DB level.

The backfill derives each historical row's effort from its OWN config, never a
blind ''. Runs #107/#108 already recorded config.reasoning_effort; flattening
them would assert that a high board and a low board were the same regime, and
merge_runs — which now groups on this column — would fold them.

IDEMPOTENT: 0001_initial materialises the live ORM metadata, so a fresh database
already has both the column and the 4-column constraint.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0058_arena_match_reasoning_effort"
down_revision = "0057_arena_run_reasoning_efforts_map"
branch_labels = None
depends_on = None

_OLD_UQ = "uq_arena_match_run_workflow_model"
_NEW_UQ = "uq_arena_match_run_workflow_model_effort"
_NEW_COLS = ["run_id", "workflow_id", "model_id", "reasoning_effort"]
_OLD_COLS = ["run_id", "workflow_id", "model_id"]


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _uniques(table: str) -> set[str]:
    return {u["name"] for u in inspect(op.get_bind()).get_unique_constraints(table)}


def upgrade() -> None:
    if "arena_match" not in _tables():
        return

    if "reasoning_effort" not in _columns("arena_match"):
        op.add_column(
            "arena_match",
            sa.Column("reasoning_effort", sa.String(20),
                      nullable=False, server_default=""),
        )
        # json_valid guards a corrupt payload: json_extract RAISES on malformed
        # JSON, which would abort the whole upgrade over one bad row.
        op.get_bind().execute(sa.text(
            "UPDATE arena_match "
            "   SET reasoning_effort = "
            "       COALESCE(json_extract(config, '$.reasoning_effort'), '') "
            " WHERE json_valid(config)"
        ))

    uniques = _uniques("arena_match")
    if _NEW_UQ not in uniques:
        # batch_alter_table, never a direct constraint op: SQLite cannot alter a
        # constraint in place, so alembic rebuilds the table (create, copy, drop,
        # rename) and carries the rows, sibling FKs and other indexes across.
        with op.batch_alter_table("arena_match") as batch:
            if _OLD_UQ in uniques:
                batch.drop_constraint(_OLD_UQ, type_="unique")
            batch.create_unique_constraint(_NEW_UQ, _NEW_COLS)


def downgrade() -> None:
    if "arena_match" not in _tables():
        return

    uniques = _uniques("arena_match")
    if _OLD_UQ not in uniques:
        with op.batch_alter_table("arena_match") as batch:
            if _NEW_UQ in uniques:
                batch.drop_constraint(_NEW_UQ, type_="unique")
            batch.create_unique_constraint(_OLD_UQ, _OLD_COLS)

    if "reasoning_effort" in _columns("arena_match"):
        with op.batch_alter_table("arena_match") as batch:
            batch.drop_column("reasoning_effort")
```

- [ ] **Step 5: Run the migration tests**

Run: `.venv/bin/python -m pytest tests/test_migration_0058_arena_match_effort.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run the standing fresh-chain gate**

Run: `.venv/bin/python -m pytest tests/test_migration_fresh_chain.py -v`
Expected: PASS — `alembic upgrade head` on an empty DB reaches head.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models.py backend/alembic/versions/0058_arena_match_reasoning_effort.py tests/test_migration_0058_arena_match_effort.py
git commit -m "feat(arena): reasoning_effort joins the arena_match contestant key"
```

---

### Task 2: Effort-aware `record_match` upsert

**Files:**
- Modify: `backend/app/services/arena/store.py:64-113` (`record_match`), `:632-646` (`_match_to_dict`)
- Test: `tests/test_arena_store.py`

**Interfaces:**
- Consumes: `ArenaMatch.reasoning_effort` (Task 1).
- Produces: `store.record_match(..., reasoning_effort: str | None = None)` — keyword-only, defaults to `None` meaning unpinned, stored as `''`. `store._match_to_dict` gains `"reasoning_effort": str | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_arena_store.py` (extend the existing `_make_match` helper first):

```python
def _make_match_at(session, run_id, *, model_id="model-x", reasoning_effort=None,
                   objective_score=70.0, workflow_id="wf-a"):
    return store.record_match(
        session, run_id, workflow_id, model_id,
        objective_score=objective_score, judged_score=None, total_score=None,
        judge_missing=False, config={"reasoning_effort": reasoning_effort},
        transcript_path=None, status="scored",
        reasoning_effort=reasoning_effort,
    )


def test_record_match_two_efforts_are_two_rows(session):
    """The same model at low and high is two contestants, not an upsert clobber."""
    rid = _make_run(session)
    low = _make_match_at(session, rid, reasoning_effort="low")
    high = _make_match_at(session, rid, reasoning_effort="high")
    assert low != high
    assert len(store.get_run(session, rid)["matches"]) == 2


def test_record_match_same_effort_updates_one_row(session):
    rid = _make_run(session)
    first = _make_match_at(session, rid, reasoning_effort="high", objective_score=10.0)
    second = _make_match_at(session, rid, reasoning_effort="high", objective_score=20.0)
    assert first == second
    matches = store.get_run(session, rid)["matches"]
    assert len(matches) == 1
    assert matches[0]["objective_score"] == 20.0


def test_record_match_unpinned_updates_one_row(session):
    """None normalises to '', so two unpinned writes still upsert (not duplicate)."""
    rid = _make_run(session)
    first = _make_match_at(session, rid, reasoning_effort=None, objective_score=10.0)
    second = _make_match_at(session, rid, reasoning_effort=None, objective_score=20.0)
    assert first == second
    assert len(store.get_run(session, rid)["matches"]) == 1


def test_match_dict_surfaces_effort_as_none_when_unpinned(session):
    rid = _make_run(session)
    _make_match_at(session, rid, model_id="a", reasoning_effort=None)
    _make_match_at(session, rid, model_id="b", reasoning_effort="max")
    by_model = {m["model_id"]: m for m in store.get_run(session, rid)["matches"]}
    assert by_model["a"]["reasoning_effort"] is None
    assert by_model["b"]["reasoning_effort"] == "max"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_arena_store.py -k "effort" -v`
Expected: FAIL — `record_match() got an unexpected keyword argument 'reasoning_effort'`

- [ ] **Step 3: Implement**

In `store.record_match`, add the parameter to the signature (after `score_breakdown`):

```python
    score_breakdown: dict | None = None,
    reasoning_effort: str | None = None,
) -> int:
```

Then, at the top of the body, normalize and use it in the lookup and both writes:

```python
    # '' is the stored form of "unpinned"; the column is the contestant key, so
    # the lookup MUST filter on it or the second arm upserts over the first.
    effort_key = reasoning_effort or ""
    existing = (
        session.query(ArenaMatch)
        .filter_by(run_id=run_id, workflow_id=workflow_id, model_id=model_id,
                   reasoning_effort=effort_key)
        .one_or_none()
    )
```

Set it on the update branch (`existing.reasoning_effort = effort_key`) and pass `reasoning_effort=effort_key` to the `ArenaMatch(...)` constructor.

In `_match_to_dict`, add after `"model_id"`:

```python
        # None, not '', at the dict boundary: callers reason about "unpinned" as
        # an absence, and '' would read as a real level in JSON.
        "reasoning_effort": m.reasoning_effort or None,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_arena_store.py -v`
Expected: PASS (all tests, including the pre-existing ones)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/arena/store.py tests/test_arena_store.py
git commit -m "feat(arena): record_match keys on effort so two arms are two rows"
```

---

### Task 3: List-valued effort map and launch validation

**Files:**
- Modify: `backend/app/services/arena/task.py:24-140` (`queue_arena_run`)
- Modify: `backend/app/services/arena/store.py:262-268` (`_run_to_dict` normalization)
- Test: `tests/test_arena_api.py` (or wherever `queue_arena_run` is currently tested — check with `grep -rln queue_arena_run tests/`)

**Interfaces:**
- Consumes: nothing from Tasks 1–2.
- Produces:
  - `task.effort_levels_for(reasoning_efforts: dict | None, model_id: str) -> list[str | None]` — the shared read seam, returns `[None]` when the model is absent.
  - `queue_arena_run(..., reasoning_efforts: dict[str, list[str | None] | str] | None)`.
  - `ArenaRun.reasoning_efforts` stored as `{slug: [level | None, ...]}`; `store._run_to_dict` always returns the list form.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_arena_effort_levels.py`:

```python
"""Per-model effort LISTS: the read seam and launch validation."""
from __future__ import annotations

import pytest
from app.services.arena.task import effort_levels_for, queue_arena_run


# ---- the read seam ----

def test_absent_model_runs_once_at_vendor_default():
    assert effort_levels_for({}, "gpt-5-5") == [None]
    assert effort_levels_for(None, "gpt-5-5") == [None]


def test_legacy_scalar_reads_as_a_one_element_list():
    """Boards launched before per-effort contestants stored {slug: "high"}."""
    assert effort_levels_for({"gpt-5-5": "high"}, "gpt-5-5") == ["high"]


def test_list_form_round_trips_including_the_unpinned_arm():
    assert effort_levels_for({"gpt-5-5": [None, "high"]}, "gpt-5-5") == [None, "high"]


def test_empty_list_means_vendor_default():
    assert effort_levels_for({"gpt-5-5": []}, "gpt-5-5") == [None]


# ---- launch validation ----

def test_duplicate_level_is_rejected(session):
    """Two identical levels would mint two contestants with one key — the second
    upserts over the first and the board reports a completed run with one arm."""
    with pytest.raises(ValueError, match="duplicate"):
        queue_arena_run(
            session,
            workflow_ids=["risk-manager-control-day"],
            model_ids=["gpt-5-5"],
            reasoning_efforts={"gpt-5-5": ["high", "high"]},
        )


def test_each_level_is_validated_against_its_own_model(session):
    """'minimal' is rejected by every OpenAI model (measured)."""
    with pytest.raises(ValueError, match="not accepted"):
        queue_arena_run(
            session,
            workflow_ids=["risk-manager-control-day"],
            model_ids=["gpt-5-5"],
            reasoning_efforts={"gpt-5-5": ["low", "minimal"]},
        )


def test_unpinned_arm_is_always_legal(session):
    """A null arm bypasses the ladder check — every model can run unpinned,
    including the wire-protocol models whose client carries no effort at all."""
    run_id, _task = queue_arena_run(
        session,
        workflow_ids=["risk-manager-control-day"],
        model_ids=["gpt-5-5"],
        reasoning_efforts={"gpt-5-5": [None, "high"]},
    )
    from app.services.arena import store
    assert store.get_run(session, run_id)["reasoning_efforts"] == {"gpt-5-5": [None, "high"]}


def test_all_default_map_entry_is_dropped(session):
    """[None] is exactly 'absent' — don't persist a map that says nothing."""
    run_id, _task = queue_arena_run(
        session,
        workflow_ids=["risk-manager-control-day"],
        model_ids=["gpt-5-5"],
        reasoning_efforts={"gpt-5-5": [None]},
    )
    from app.services.arena import store
    assert store.get_run(session, run_id)["reasoning_efforts"] == {}


def test_progress_total_counts_arms_not_models(session):
    """Two arms on one model is two units of work, not one."""
    _run_id, task = queue_arena_run(
        session,
        workflow_ids=["risk-manager-control-day"],
        model_ids=["gpt-5-5"],
        trials=2,
        reasoning_efforts={"gpt-5-5": [None, "high"]},
    )
    assert task.progress_total == 1 * 2 * 2      # workflows × arms × trials
```

> **Note for the implementer:** if the `session` fixture is not available in a new
> test module, copy the import/fixture pattern from the top of
> `tests/test_arena_store.py` — it is a repo-wide conftest fixture.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_arena_effort_levels.py -v`
Expected: FAIL — `ImportError: cannot import name 'effort_levels_for'`

- [ ] **Step 3: Add the read seam**

In `backend/app/services/arena/task.py`, at module level (after the imports):

```python
def effort_levels_for(
    reasoning_efforts: dict | None, model_id: str
) -> list[str | None]:
    """The effort levels this model runs at in a run, one contestant per level.

    A model absent from the map runs exactly once, at its vendor default — what
    every board through #104 measured. ``None`` inside the list is the explicit
    unpinned arm, so a board can rank "as we have always run it" against a pin.

    Accepts the LEGACY scalar form (``{slug: "high"}``, written before efforts
    became per-arm lists) as a one-element list: derive on read, never migrate
    the JSON column.
    """
    raw = (reasoning_efforts or {}).get(model_id)
    if raw is None:
        return [None]
    if isinstance(raw, str):
        return [raw or None]
    levels = [(level or None) for level in raw]
    return levels or [None]
```

- [ ] **Step 4: Rework `queue_arena_run`'s validation**

Replace the normalization block (currently `efforts = {...}` at `task.py:64-68`) with a
pass-through, and replace the whole `canonical_efforts` block (`task.py:82-116`) with:

```python
    # Per-model LIST of arms. Each level is validated against ITS OWN model here
    # rather than per-match: resolve_agent_model_selection would otherwise reject
    # the offending arm after the board has started and other pairs have already
    # cost real money.
    canonical_efforts: dict[str, list[str | None]] = {}
    if reasoning_efforts:
        from app.services.arena.models import arena_model_to_selection, get_model
        from app.services.deep_agent.channel_registry import get_registry
        from app.services.deep_agent.model_factory import effort_rejection

        registry = get_registry()
        known = set(canonical_model_ids)
        offenders = []
        for raw_id, raw_levels in reasoning_efforts.items():
            slug = validate_model_ids([raw_id])[0]
            if slug not in known:
                raise ValueError(
                    f"reasoning_efforts names {raw_id!r}, which is not one of this "
                    f"run's models {sorted(known)}"
                )
            levels = raw_levels if isinstance(raw_levels, list) else [raw_levels]
            normalized: list[str | None] = []
            for level in levels:
                effort = normalize_reasoning_effort(level)
                if effort in normalized:
                    # Two arms with one contestant key: the second would upsert
                    # over the first and the run would report completed with an
                    # arm silently missing.
                    raise ValueError(
                        f"reasoning_efforts[{slug!r}] lists a duplicate level "
                        f"{'default' if effort is None else effort!r}"
                    )
                normalized.append(effort)

            selection = arena_model_to_selection(get_model(slug))
            for effort in normalized:
                if effort is None:
                    continue   # unpinned is legal for every model, always
                # The SHARED seam, so launch validation and the per-match check
                # cannot disagree — it also covers the wire-protocol models whose
                # client cannot carry an effort at all.
                reason = effort_rejection(
                    registry, selection["channel"], selection["provider"],
                    selection["model"], effort,
                )
                if reason is not None:
                    offenders.append(f"{slug}: {reason}")

            # [None] is exactly "absent" — persisting it would claim a pin that
            # is not one, and read back identically anyway.
            if normalized != [None]:
                canonical_efforts[slug] = normalized
        if offenders:
            raise ValueError(
                "reasoning_effort is not accepted by the selected model — "
                + "; ".join(offenders)
            )
```

Then change `progress_total`:

```python
    arms = sum(
        len(effort_levels_for(canonical_efforts, m)) for m in canonical_model_ids
    )
    task = TaskRun(
        kind=TaskKind.ARENA_RUN.value,
        status=TaskStatus.QUEUED.value,
        description=(
            f"Arena run: {len(workflow_ids)} workflow(s) × {arms} contestant(s)"
        ),
        progress_current=0,
        # Arms, not models — a model with two efforts is two units of work, and
        # the old product left a finished run reading as permanently stuck.
        progress_total=len(workflow_ids) * arms * trials,
        message="Queued arena run",
    )
```

- [ ] **Step 5: Normalize the stored map on read**

In `backend/app/services/arena/store.py`, inside `_run_to_dict`, replace the
`"reasoning_efforts"` entry (line 659):

```python
        # Always the LIST form at the dict boundary, so every consumer (execute,
        # --resume, the router's RunSummary) sees one shape. Legacy rows stored a
        # bare scalar; that is read, never migrated.
        "reasoning_efforts": {
            slug: ([levels] if isinstance(levels, str) else list(levels))
            for slug, levels in (run.reasoning_efforts or {}).items()
        },
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_arena_effort_levels.py tests/test_arena_store.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/arena/task.py backend/app/services/arena/store.py tests/test_arena_effort_levels.py
git commit -m "feat(arena): per-model effort lists validated per arm at launch"
```

---

### Task 4: Execution loop and per-arm transcript evidence

**Files:**
- Modify: `backend/app/services/arena/task.py:235-258` (`_save_transcript`), `:320-360` (`_run_and_score_once` save sites), `:560-636` (`_execute`)
- Test: `tests/test_arena_runner.py` (or a new `tests/test_arena_execute_arms.py`)

**Interfaces:**
- Consumes: `effort_levels_for` (Task 3), `record_match(..., reasoning_effort=)` (Task 2).
- Produces: `_save_transcript(transcript, artifact_root, workflow_id, model_id, trial=None, reasoning_effort=None)` writing to `<artifact_root>/<workflow_id>/<model_id>/<effort or "default">/transcript.json`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_arena_execute_arms.py`:

```python
"""Two arms of one model must not share a transcript directory."""
from __future__ import annotations

from pathlib import Path

from app.services.arena.task import _save_transcript


class _FakeTranscript:
    def __init__(self, tag: str) -> None:
        self._tag = tag

    def model_dump(self) -> dict:
        return {"tag": self._tag}


def test_two_efforts_write_separate_transcripts(tmp_path: Path) -> None:
    """Without an effort segment the second arm clobbers the first — the same
    failure the per-trial copies fixed one level up, where the evidence you most
    need is exactly the one that got overwritten."""
    low = _save_transcript(_FakeTranscript("low"), tmp_path, "wf", "m",
                           reasoning_effort="low")
    high = _save_transcript(_FakeTranscript("high"), tmp_path, "wf", "m",
                            reasoning_effort="high")

    assert low != high
    assert Path(low).read_text(encoding="utf-8").count('"low"') == 1
    assert Path(high).read_text(encoding="utf-8").count('"high"') == 1


def test_unpinned_arm_lands_under_default(tmp_path: Path) -> None:
    path = _save_transcript(_FakeTranscript("x"), tmp_path, "wf", "m")
    assert Path(path) == tmp_path / "wf" / "m" / "default" / "transcript.json"


def test_per_trial_copies_stay_inside_the_arm(tmp_path: Path) -> None:
    _save_transcript(_FakeTranscript("t0"), tmp_path, "wf", "m", trial=0,
                     reasoning_effort="high")
    assert (tmp_path / "wf" / "m" / "high" / "transcript.trial0.json").exists()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_execute_arms.py -v`
Expected: FAIL — `_save_transcript() got an unexpected keyword argument 'reasoning_effort'`

- [ ] **Step 3: Add the effort segment to the transcript path**

In `task.py`, change the signature and the directory:

```python
def _save_transcript(transcript, artifact_root: Path,
                     workflow_id: str, model_id: str,
                     trial: int | None = None,
                     reasoning_effort: str | None = None) -> str | None:
```

```python
        # One directory per ARM. Two efforts of one model previously wrote the
        # same transcript.json and the second clobbered the first, which is
        # exactly the evidence needed to explain why an arm lost.
        t_dir = artifact_root / workflow_id / model_id / (reasoning_effort or "default")
```

Then pass `reasoning_effort=reasoning_effort` at **every** `_save_transcript` call
site inside `_run_and_score_once` (find them with
`grep -n "_save_transcript" backend/app/services/arena/task.py` — there are three:
the two infra-gated saves and the scored save).

- [ ] **Step 4: Add the inner arm loop to `_execute`**

Replace the pair loop body (`task.py:582-631`). The change is the added inner
`for effort in ...` and threading `effort` into the two call sites:

```python
    for workflow_id in workflow_ids:
        for model_id in model_ids:
            loaded = _get_bundle(workflow_id)
            model = get_model(model_id)
            # One contestant per pinned level; a model absent from the map runs
            # once at its vendor default.
            for model_effort in effort_levels_for(reasoning_efforts, model_id):
                clean: list[dict] = []
                last_infra: str | None = None
                last_path: str | None = None
                last_infra_path: str | None = None
                failed_exc: str | None = None
                for trial_index in range(trials_n):
                    try:
                        status, breakdown, info, invalid_path = _run_and_score_once(
                            session,
                            run_id=run_id,
                            loaded=loaded,
                            model=model,
                            workflow_id=workflow_id,
                            model_id=model_id,
                            weights=weights,
                            artifact_root=artifact_root,
                            cfg=_cfg,
                            run_match_fn=_run_match_fn,
                            judge_fn=judge_fn,
                            post=post,
                            trial=trial_index,
                            reasoning_effort=model_effort,
                        )
                        if status == "scored":
                            clean.append(breakdown)
                            last_path = info
                        else:
                            last_infra = info
                            last_infra_path = invalid_path
                    except Exception:
                        failed_exc = traceback.format_exc()

                    completed += 1
                    update_task_progress(session, task_id,
                                         current=completed, total=total_units)
                    session.commit()

                _record_pair(session, run_id, workflow_id, model_id, weights,
                             trials_n, clean, last_path, last_infra, failed_exc,
                             last_infra_path=last_infra_path,
                             reasoning_effort=model_effort)
                session.commit()
```

And fix `total_units` above the loop:

```python
    arms = sum(len(effort_levels_for(reasoning_efforts, m)) for m in model_ids)
    total_units = len(workflow_ids) * arms * trials_n
```

- [ ] **Step 5: Thread the effort into the match row**

In `_record_pair`, pass it to every `store.record_match(...)` call in that function
(scored, failed and invalid branches all write a row):

```python
        reasoning_effort=reasoning_effort,
```

`cfg = {"weights": ..., "trials": ..., "reasoning_effort": reasoning_effort}` stays
as-is — the column is a promoted, queryable copy, not a replacement.

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_arena_execute_arms.py tests/test_arena_runner.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/arena/task.py tests/test_arena_execute_arms.py
git commit -m "feat(arena): execute one contestant per arm, with per-arm transcripts"
```

---

### Task 5: Leaderboard groups by (model, effort)

**Files:**
- Modify: `backend/app/services/arena/store.py:354-500` (`leaderboard`)
- Test: `tests/test_arena_store.py`

**Interfaces:**
- Consumes: `ArenaMatch.reasoning_effort` (Task 1).
- Produces: leaderboard rows gain `"reasoning_effort": str | None`; grouping key is `(model_id, reasoning_effort or None)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_arena_store.py`:

```python
def test_leaderboard_ranks_two_efforts_as_two_rows(session):
    """Averaging two efforts into one row is the defect merge_runs already
    refuses — 'the merged row would average two operating regimes'."""
    rid = _make_run(session, workflow_ids=["wf-a"], model_ids=["model-x"])
    _make_match_at(session, rid, model_id="model-x", reasoning_effort="low",
                   objective_score=20.0)
    _make_match_at(session, rid, model_id="model-x", reasoning_effort="high",
                   objective_score=30.0)
    store.set_run_status(session, rid, "completed")
    session.commit()

    rows = store.leaderboard(session, run_id=rid)
    assert len(rows) == 2
    by_effort = {r["reasoning_effort"]: r for r in rows}
    assert by_effort["high"]["mean_objective"] == 30.0
    assert by_effort["low"]["mean_objective"] == 20.0
    assert by_effort["high"]["rank"] == 1
    assert by_effort["low"]["rank"] == 2


def test_leaderboard_unpinned_row_reports_none(session):
    rid = _make_run(session, workflow_ids=["wf-a"], model_ids=["model-x"])
    _make_match_at(session, rid, model_id="model-x", reasoning_effort=None,
                   objective_score=42.0)
    store.set_run_status(session, rid, "completed")
    session.commit()

    rows = store.leaderboard(session, run_id=rid)
    assert [r["reasoning_effort"] for r in rows] == [None]


def test_single_effort_board_is_unchanged(session):
    """The regression gate: a board where every model carries one effort must
    produce exactly what it produced before effort entered the key."""
    rid = _make_run(session, workflow_ids=["wf-a"], model_ids=["model-x", "model-y"])
    _make_match_at(session, rid, model_id="model-x", objective_score=80.0)
    _make_match_at(session, rid, model_id="model-y", objective_score=60.0)
    store.set_run_status(session, rid, "completed")
    session.commit()

    rows = store.leaderboard(session, run_id=rid)
    assert [(r["model_id"], r["rank"], r["mean_objective"]) for r in rows] == [
        ("model-x", 1, 80.0), ("model-y", 2, 60.0),
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_arena_store.py -k "leaderboard" -v`
Expected: FAIL — `KeyError: 'reasoning_effort'` and a 1-row result where 2 are expected.

- [ ] **Step 3: Re-key the aggregation**

In `store.leaderboard`, every `defaultdict` currently keyed by `m.model_id` becomes
keyed by a `(model_id, effort)` tuple. At the top of the `for m in matches:` loop add:

```python
    for m in matches:
        # A contestant is (model, effort). Collapsing the two into one row would
        # average two operating regimes — effort measurably moves tool-call count
        # (~22% fewer at high than low, runs #107/#108) and therefore EFF.
        key = (m.model_id, m.reasoning_effort or None)
```

Then replace every `[m.model_id]` subscript inside that loop with `[key]`
(`invalid_counts`, `scored_counts`, `model_objectives`, `model_subjectives`,
`model_sub_stdevs`, `model_sub_modes`, `model_axes`, `model_final_ovrs`,
`model_base_ovrs`, `model_cons`, `model_stat_lists`).

In the row-building loop, change the iteration and the `.get(...)` lookups:

```python
    rows = []
    for key in set(scored_counts) | set(invalid_counts):
        model_id, effort = key
        objectives = model_objectives.get(key, [])
        subs = model_subjectives.get(key, [])
        stdevs = model_sub_stdevs.get(key, [])
        final_ovrs = model_final_ovrs.get(key, [])
        base_ovrs = model_base_ovrs.get(key, [])
        cons = model_cons.get(key, [])
        carded_count = len(final_ovrs)
        scored_count = scored_counts.get(key, 0)
```

...and the remaining `model_stat_lists[model_id]`, `model_sub_modes.get(model_id, [])`,
`model_axes.get(model_id, {})`, `scored_counts.get(model_id, 0)`,
`invalid_counts.get(model_id, 0)` all become `[key]` / `.get(key, ...)`.

Add the field to the row dict, right after `"model_id"`:

```python
            "model_id": model_id,
            "reasoning_effort": effort,
```

Finally, extend the display stabilizer in the sort so two arms of one model have a
deterministic order (it must never break a rank — shared ranks on exact ties are
spec D5):

```python
    rows.sort(key=lambda r: (_order_key(r), r["model_id"], r["reasoning_effort"] or ""))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_arena_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/arena/store.py tests/test_arena_store.py
git commit -m "feat(arena): leaderboard ranks each (model, effort) arm separately"
```

---

### Task 6: `merge_runs` groups by (workflow, model, effort)

**Files:**
- Modify: `backend/app/services/arena/store.py:150-215` (`merge_runs`)
- Test: `tests/test_arena_store.py`

**Interfaces:**
- Consumes: Tasks 1–2, 5.
- Produces: merged runs carry `reasoning_efforts` in the list form.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_arena_store.py`:

```python
def test_merge_keeps_two_efforts_as_two_merged_rows(session):
    """Two boards, each carrying both arms, fold arm-wise — never arm-blind."""
    ids = []
    for objective_low, objective_high in ((20.0, 30.0), (24.0, 34.0)):
        rid = _make_run(session, workflow_ids=["wf-a"], model_ids=["model-x"])
        _make_match_at(session, rid, model_id="model-x", reasoning_effort="low",
                       objective_score=objective_low)
        _make_match_at(session, rid, model_id="model-x", reasoning_effort="high",
                       objective_score=objective_high)
        store.set_run_status(session, rid, "completed")
        ids.append(rid)
    session.commit()

    merged_id = store.merge_runs(session, ids)
    session.commit()

    matches = store.get_run(session, merged_id)["matches"]
    assert sorted(m["reasoning_effort"] for m in matches) == ["high", "low"]


def test_merged_run_records_effort_lists(session):
    rid_a = _make_run(session, workflow_ids=["wf-a"], model_ids=["model-x"])
    _make_match_at(session, rid_a, model_id="model-x", reasoning_effort="high")
    rid_b = _make_run(session, workflow_ids=["wf-a"], model_ids=["model-x"])
    _make_match_at(session, rid_b, model_id="model-x", reasoning_effort="high")
    session.commit()

    merged_id = store.merge_runs(session, [rid_a, rid_b])
    session.commit()
    assert store.get_run(session, merged_id)["reasoning_efforts"] == {"model-x": ["high"]}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_arena_store.py -k "merge" -v`
Expected: FAIL — the merged run has one match (arms folded together), and
`reasoning_efforts` is the scalar form.

- [ ] **Step 3: Re-key the grouping**

In `merge_runs`, widen the group key to include the effort:

```python
    pos = {rid: i for i, rid in enumerate(ordered)}
    # Effort is part of the contestant key, so it is part of the fold key. The
    # explicit cross-effort check below is now redundant BY CONSTRUCTION; it stays
    # as the statement of intent this whole design derives from.
    groups: dict[tuple[str, str, str | None], list[ArenaMatch]] = defaultdict(list)
    for m in matches:
        groups[(m.workflow_id, m.model_id, m.reasoning_effort or None)].append(m)
```

Update the three places that unpack a 2-tuple key. The existing effort guard
becomes:

```python
    for (workflow_id, model_id, _effort), ms in sorted(
        groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or "")
    ):
        efforts = {(m.config or {}).get("reasoning_effort") for m in ms}
        if len(efforts) > 1:
            ...unchanged...
```

The derived id lists and the effort map become:

```python
    workflow_ids = sorted({wf for wf, _md, _ef in groups})
    model_ids = sorted({md for _wf, md, _ef in groups})
    # Per-model LIST of the arms present, so the merged run states the regimes it
    # contains rather than one of them.
    merged_efforts: dict[str, list[str]] = {}
    for (_wf, model_id, effort) in groups:
        if effort:
            arms = merged_efforts.setdefault(model_id, [])
            if effort not in arms:
                arms.append(effort)
    merged_efforts = {k: sorted(v) for k, v in merged_efforts.items()}
```

And the record loop:

```python
    for (workflow_id, model_id, effort), ms in groups.items():
```

...passing `reasoning_effort=effort` to its `store.record_match(...)` call.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_arena_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/arena/store.py tests/test_arena_store.py
git commit -m "feat(arena): merge folds arm-wise, never across efforts"
```

---

### Task 7: API request and response shapes

**Files:**
- Modify: `backend/app/routers/arena.py:29-67` (`RunSummary`, `MatchSummary`, `CreateRunRequest`), `:344-364` (leaderboard key projection)
- Test: `tests/test_arena_api.py`

**Interfaces:**
- Consumes: Tasks 2–6.
- Produces: `POST /api/arena/runs` accepts `reasoning_efforts: {slug: [level | null, ...]}`; `GET /api/arena/leaderboard` rows carry `reasoning_effort`; `RunSummary`/`MatchSummary` carry it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_arena_api.py`. There is **no `client` fixture** — the module
builds a TestClient per test via its own `_make_arena_app(session, settings, ...)`
helper, and `session` / `settings` are conftest fixtures.

```python
def test_leaderboard_row_carries_reasoning_effort(session, settings):
    client = _make_arena_app(session, settings)
    """Assert at the HTTP layer, not against store.leaderboard.

    get_leaderboard hand-builds an explicit key projection, so a field the store
    gained is dropped unless it is named there — the store unit test passes while
    the API serves nothing. Same failure as /api/agent/models silently dropping
    reasoning_efforts, different mechanism."""
    from app.services.arena import store
    rid = store.create_run(session, ["wf-a"], ["model-x"])
    store.record_match(
        session, rid, "wf-a", "model-x", objective_score=50.0, judged_score=None,
        total_score=None, judge_missing=False, config={}, transcript_path=None,
        status="scored", reasoning_effort="high",
    )
    store.set_run_status(session, rid, "completed")
    session.commit()

    body = client.get(f"/api/arena/leaderboard?run_id={rid}").json()
    assert body["rows"][0]["reasoning_effort"] == "high"


def test_run_summary_accepts_effort_lists(session, settings):
    """RunSummary is CONSTRUCTED explicitly, so a list value under a dict[str, str]
    annotation raises ValidationError and 500s the runs list."""
    client = _make_arena_app(session, settings)
    from app.services.arena import store
    rid = store.create_run(session, ["wf-a"], ["model-x"],
                           reasoning_efforts={"model-x": [None, "high"]})
    session.commit()

    body = client.get("/api/arena/runs").json()
    row = next(r for r in body["runs"] if r["id"] == rid)
    assert row["reasoning_efforts"] == {"model-x": [None, "high"]}


def test_run_summary_normalises_a_legacy_scalar(session, settings):
    """A board launched before per-arm lists stored {slug: "high"}."""
    client = _make_arena_app(session, settings)
    from app.models import ArenaRun
    from app.services.arena import store
    rid = store.create_run(session, ["wf-a"], ["model-x"])
    session.get(ArenaRun, rid).reasoning_efforts = {"model-x": "high"}
    session.commit()

    body = client.get("/api/arena/runs").json()
    row = next(r for r in body["runs"] if r["id"] == rid)
    assert row["reasoning_efforts"] == {"model-x": ["high"]}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_arena_api.py -k "effort" -v`
Expected: FAIL — `KeyError: 'reasoning_effort'` on the leaderboard row; a pydantic
`ValidationError` on the runs list.

- [ ] **Step 3: Widen the schemas**

`routers/arena.py`, `RunSummary`:

```python
    # {model_slug: [effort | null, ...]} — one entry per ARM this model ran at;
    # null is the explicit unpinned (vendor-default) arm, and a model absent from
    # the map ran once at its vendor default. Legacy rows stored a bare scalar;
    # store._run_to_dict normalises it to a list on read.
    reasoning_efforts: dict[str, list[str | None]] = Field(default_factory=dict)
```

`MatchSummary`, after `model_id`:

```python
    # Which regime produced this row; null = unpinned. Part of the contestant key.
    reasoning_effort: str | None = None
```

`CreateRunRequest`:

```python
    # Per-model effort ARMS: {slug: [level | null, ...]}. Two entries for one
    # model make it two contestants that rank against each other. A bare string is
    # accepted for backward compatibility with the single-arm form. Validated per
    # arm in queue_arena_run so a bad level fails at launch, not per-match.
    reasoning_efforts: dict[str, list[str | None] | str] | None = None
```

- [ ] **Step 4: Add the field to the leaderboard projection and the match dict**

In `get_leaderboard`'s `renamed` list comprehension, after `"model_id"`:

```python
                "model_id": r["model_id"],
                # Named explicitly because this projection is an allowlist: a key
                # the store gains is served only if it appears here.
                "reasoning_effort": r["reasoning_effort"],
```

In `get_run`'s `MatchSummary(...)` construction (`arena.py:284`), add:

```python
                reasoning_effort=m.get("reasoning_effort"),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_arena_api.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/arena.py tests/test_arena_api.py
git commit -m "feat(arena): API carries per-arm efforts through launch and board"
```

---

### Task 8: `--resume` gains the effort dimension

**Files:**
- Modify: `scripts/launch_arena_run.py:60-141`
- Test: `tests/test_arena_resume_arms.py` (new)

**Interfaces:**
- Consumes: `effort_levels_for` (Task 3), `_record_pair(..., reasoning_effort=)` (Task 4).
- Produces: resume completeness keyed on `(workflow_id, model_id, effort)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_arena_resume_arms.py`:

```python
"""Resume must not call a pair done because ONE arm finished.

Run #104's failure shape: a sustained provider outage swept the remaining pairs
to invalid and the run was still marked completed. An arm-blind resume repeats it
silently — the high arm never runs and the board claims success.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _launcher():
    """Load scripts/launch_arena_run.py by PATH.

    `scripts/` has no __init__.py, so import_module("scripts.launch_arena_run")
    fails — the same spec_from_file_location pattern as
    tests/test_generate_demo_smoke.py.
    """
    spec = importlib.util.spec_from_file_location(
        "launch_arena_run", _ROOT / "scripts" / "launch_arena_run.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resume_todo_is_keyed_by_arm() -> None:
    launcher = _launcher()
    run = {
        "workflow_ids": ["wf-a"],
        "model_ids": ["model-x"],
        "reasoning_efforts": {"model-x": [None, "high"]},
        "matches": [
            {"workflow_id": "wf-a", "model_id": "model-x",
             "reasoning_effort": None, "status": "scored"},
        ],
    }
    assert launcher._resume_todo(run) == [("wf-a", "model-x", "high")]


def test_resume_todo_is_empty_when_every_arm_scored() -> None:
    launcher = _launcher()
    run = {
        "workflow_ids": ["wf-a"],
        "model_ids": ["model-x"],
        "reasoning_efforts": {"model-x": [None, "high"]},
        "matches": [
            {"workflow_id": "wf-a", "model_id": "model-x",
             "reasoning_effort": None, "status": "scored"},
            {"workflow_id": "wf-a", "model_id": "model-x",
             "reasoning_effort": "high", "status": "scored"},
        ],
    }
    assert launcher._resume_todo(run) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_resume_arms.py -v`
Expected: FAIL — `AttributeError: module has no attribute '_resume_todo'`

- [ ] **Step 3: Extract and fix the resume completeness logic**

In `scripts/launch_arena_run.py`, add a module-level helper:

```python
def _resume_todo(run: dict) -> list[tuple[str, str, str | None]]:
    """Arms of this run that are not yet scored, as (workflow, model, effort).

    Keyed by ARM, not by (workflow, model): a resume that treats a pair as done
    because its unpinned arm scored would never run the pinned arm and would then
    mark the run completed — run #104's failure shape, silently.
    """
    from app.services.arena.task import effort_levels_for

    efforts = run.get("reasoning_efforts") or {}
    done = {
        (m["workflow_id"], m["model_id"], m.get("reasoning_effort"))
        for m in run["matches"] if m["status"] == "scored"
    }
    arms = [
        (w, m, effort)
        for w in run["workflow_ids"]
        for m in run["model_ids"]
        for effort in effort_levels_for(efforts, m)
    ]
    return [a for a in arms if a not in done]
```

Replace the existing `done` / `pairs` / `todo` block (lines 67-76) with:

```python
        reasoning_efforts = run.get("reasoning_efforts") or {}
        todo = _resume_todo(run)
        arms_total = sum(
            len(effort_levels_for(reasoning_efforts, m))
            for m in run["model_ids"]
        ) * len(run["workflow_ids"])

        print(f"resume run {run_id}: {arms_total - len(todo)} scored, "
              f"{len(todo)} to run (trials={trials_n}, "
              f"efforts={reasoning_efforts or 'unpinned'})", flush=True)
        for w, m, effort in todo:
            print(f"  todo: {m} @ {effort or 'default'} x {w}", flush=True)
```

Update the execution loop to iterate `todo` triples and pass
`reasoning_effort=effort` to `_run_and_score_once` and `_record_pair` (replacing
both `reasoning_efforts.get(model_id)` call sites at lines 115 and 129), and update
the final completeness check to compare against `arms_total`:

```python
        remaining = _resume_todo(store.get_run(session, run_id) or run)
        store.set_run_status(
            session, run_id, "completed" if not remaining else "failed")
        session.commit()
        print(f"DONE resume run={run_id} "
              f"scored={arms_total - len(remaining)}/{arms_total}", flush=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_arena_resume_arms.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/launch_arena_run.py tests/test_arena_resume_arms.py
git commit -m "fix(arena): resume tracks arms, not pairs"
```

---

### Task 9: New Run panel picks several levels per model

**Files:**
- Modify: `frontend/src/routes/Arena.live.tsx:584, 629, 786-810, 840-850, 944, 1195-1240`
- Modify: `frontend/src/routes/Arena.css`
- Modify: `frontend/src/lib/arenaApi.ts`, `frontend/src/types.ts` (wire types)
- Test: `frontend/src/routes/Arena.live.test.tsx`

**Interfaces:**
- Consumes: Task 7's API shapes.
- Produces: `reasoningEfforts: Record<string, string[]>` state where `'default'` is the UI sentinel for the unpinned arm, mapped to wire `null` at launch.

**Read `frontend/CLAUDE.md` and `frontend/UI_STYLE_GUIDE.md` before starting.**
Token-only styling; verify every token against `frontend/src/tokens/` (`--radius-1`
and `--ink-3` are referenced by some page CSS but **defined nowhere** — do not copy
them). Verify in both themes and compact density.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/routes/Arena.live.test.tsx`:

```tsx
it('launches one run with two effort arms for the same model', async () => {
  const user = userEvent.setup();
  vi.mocked(arenaApi.listArenaModels).mockResolvedValue([
    { slug: 'gpt-5-5', zenmux_name: 'openai/gpt-5.5', display_name: 'GPT-5.5',
      reasoning_efforts: ['low', 'medium', 'high'] },
  ]);
  vi.mocked(arenaApi.createArenaRun).mockResolvedValue({ run_id: 9 });
  render(<ArenaLive />);

  await user.click(await screen.findByRole('button', { name: /new run/i }));
  await user.click(await screen.findByRole('checkbox', { name: 'GPT-5.5' }));
  await user.click(screen.getByRole('checkbox', { name: /GPT-5\.5 effort low/i }));
  await user.click(screen.getByRole('checkbox', { name: /GPT-5\.5 effort high/i }));
  await user.click(await screen.findByRole('checkbox', { name: /workflow-a/i }));
  await user.click(screen.getByRole('button', { name: /^launch$/i }));

  await waitFor(() => expect(arenaApi.createArenaRun).toHaveBeenCalled());
  expect(vi.mocked(arenaApi.createArenaRun).mock.calls[0][0].reasoning_efforts)
    .toEqual({ 'gpt-5-5': ['low', 'high'] });
});

it('sends null for the unpinned Default arm', async () => {
  const user = userEvent.setup();
  vi.mocked(arenaApi.listArenaModels).mockResolvedValue([
    { slug: 'gpt-5-5', zenmux_name: 'openai/gpt-5.5', display_name: 'GPT-5.5',
      reasoning_efforts: ['low', 'high'] },
  ]);
  vi.mocked(arenaApi.createArenaRun).mockResolvedValue({ run_id: 9 });
  render(<ArenaLive />);

  await user.click(await screen.findByRole('button', { name: /new run/i }));
  await user.click(await screen.findByRole('checkbox', { name: 'GPT-5.5' }));
  await user.click(screen.getByRole('checkbox', { name: /GPT-5\.5 effort default/i }));
  await user.click(screen.getByRole('checkbox', { name: /GPT-5\.5 effort high/i }));
  await user.click(await screen.findByRole('checkbox', { name: /workflow-a/i }));
  await user.click(screen.getByRole('button', { name: /^launch$/i }));

  await waitFor(() => expect(arenaApi.createArenaRun).toHaveBeenCalled());
  expect(vi.mocked(arenaApi.createArenaRun).mock.calls[0][0].reasoning_efforts)
    .toEqual({ 'gpt-5-5': [null, 'high'] });
});

it('renders both arms of one model as distinct board rows', async () => {
  vi.mocked(arenaApi.getArenaLeaderboard).mockResolvedValue([
    { model_id: 'claude-sonnet', reasoning_effort: 'high', rank: 1, ovr: 82,
      card_mean: { ovr: 82, GRD: 90, ADH: 80, SYN: 88, EFF: 75, PRC: 70 },
      avg_objective: 0.9, subjective_mean: null, subjective_stdev: null,
      subjective_mode: 'disabled', matches: 1, invalid: 0 },
    { model_id: 'claude-sonnet', reasoning_effort: 'low', rank: 2, ovr: 71,
      card_mean: { ovr: 71, GRD: 70, ADH: 72, SYN: 68, EFF: 74, PRC: 66 },
      avg_objective: 0.75, subjective_mean: null, subjective_stdev: null,
      subjective_mode: 'disabled', matches: 1, invalid: 0 },
  ]);
  render(<ArenaLive />);

  // Two rows, distinguished by their effort chip — with rowKey still keyed on
  // model_id alone these are duplicate React keys.
  expect(await screen.findByText('high')).toBeInTheDocument();
  expect(await screen.findByText('low')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/routes/Arena.live.test.tsx`
Expected: FAIL — no checkbox matching `GPT-5.5 effort low`.

- [ ] **Step 3: Update the wire types**

In `frontend/src/types.ts`, on the arena leaderboard row type add
`reasoning_effort?: string | null;`, and on the create-run payload type change
`reasoning_efforts` to `Record<string, (string | null)[]>`. Mirror in
`frontend/src/lib/arenaApi.ts` where those shapes are declared.

- [ ] **Step 4: Change the state and add the toggle**

`Arena.live.tsx:629`:

```tsx
  // Per model, the ARMS to run: each entry is a level, or the 'default' sentinel
  // for the unpinned arm (mapped to wire null at launch). Several entries make
  // one model several contestants.
  const [reasoningEfforts, setReasoningEfforts] = useState<Record<string, string[]>>({});
```

Add beside `toggleModelSelection`:

```tsx
  const toggleEffortLevel = useCallback((slug: string, level: string) => {
    setReasoningEfforts((prev) => {
      const current = prev[slug] ?? [];
      const next = current.includes(level)
        ? current.filter((l) => l !== level)
        : [...current, level];
      return { ...prev, [slug]: next };
    });
  }, []);
```

- [ ] **Step 5: Replace the per-model Select with a level checkbox group**

`Arena.live.tsx:1219-1236` — replace the whole `<Select …/>` block:

```tsx
                      {/* Effort is per model because the ladders differ, and
                          MULTI-select because two levels are two contestants.
                          'Default' is the unpinned arm, so a board can rank a pin
                          against how every board through #104 actually ran.
                          Hidden for a toggle-only model, which has no levels. */}
                      {selectedModelIds.has(m.slug) && levels.length > 0 && (
                        <fieldset className="wl-arena__efforts">
                          <legend className="wl-arena__efforts-legend">
                            {m.display_name} effort
                          </legend>
                          {['default', ...levels].map((level) => (
                            <label key={level} className="wl-arena__efforts-option">
                              <input
                                type="checkbox"
                                className="wl-arena__checklist-checkbox"
                                aria-label={`${m.display_name} effort ${level}`}
                                checked={(reasoningEfforts[m.slug] ?? []).includes(level)}
                                onChange={() => toggleEffortLevel(m.slug, level)}
                              />
                              <span className="wl-arena__checklist-text">
                                {level.charAt(0).toUpperCase() + level.slice(1)}
                              </span>
                            </label>
                          ))}
                        </fieldset>
                      )}
```

Remove the now-unused `picked` local above it. Drop the `Select` import only if no
other call site remains (`grep -n "<Select" frontend/src/routes/Arena.live.tsx`).

Add to `frontend/src/routes/Arena.css` (layout only — reuse the existing checklist
classes for the control and label styling):

```css
.wl-arena__efforts {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  border: 0;
  margin: 0;
  padding: 0;
}

.wl-arena__efforts-legend {
  font-size: var(--font-size-sm);
  color: var(--ink-muted);
}

.wl-arena__efforts-option {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
}
```

> Verify `--space-1`, `--space-2`, `--font-size-sm` and `--ink-muted` exist in
> `frontend/src/tokens/`; substitute the real names if they differ. Never invent one.

- [ ] **Step 6: Map the arms onto the launch payload**

`Arena.live.tsx:794-801` — replace the `reasoning_efforts` entry:

```tsx
      // One entry per ARM. Levels are re-filtered against the model's ladder
      // because this state survives a model being deselected and reselected and
      // the picker's reset is display-only — a stale level 422s the whole launch.
      // 'default' always survives: unpinned is legal for every model.
      reasoning_efforts: Object.fromEntries(
        Array.from(selectedModelIds)
          .map((slug) => {
            const model = models.find((x) => x.slug === slug);
            const allowed = model ? reasoningEffortsFor(model) : [];
            const arms = (reasoningEfforts[slug] ?? [])
              .filter((level) => level === 'default' || allowed.includes(level))
              .map((level) => (level === 'default' ? null : level));
            return [slug, arms] as const;
          })
          .filter(([, arms]) => arms.length > 0),
      ),
```

- [ ] **Step 7: Fix the duplicate React key and label the arms**

`Arena.live.tsx:944`:

```tsx
            <Table
              columns={leaderboardColumns}
              rows={leaderboard}
              rowKey={(r) => `${r.model_id}::${r.reasoning_effort ?? ''}`}
            />
```

`Arena.live.tsx:844` — the model column render:

```tsx
        render: (row) => (
          <span className="wl-arena__model-cell">
            {modelDisplayName(row.model_id, models)}
            {row.reasoning_effort ? (
              <Badge variant="info">{row.reasoning_effort}</Badge>
            ) : null}
          </span>
        ),
```

Import `Badge` from `../components/Badge`, and add:

```css
.wl-arena__model-cell {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
}
```

- [ ] **Step 8: Run the frontend tests and type-check**

Run: `cd frontend && npx vitest run src/routes/Arena.live.test.tsx && npx tsc --noEmit`
Expected: PASS. If unrelated route tests fail, compare the failing-file set against a
same-machine `main` run before treating it as a regression — the suite is flaky under load.

- [ ] **Step 9: Verify both themes and compact density**

Open the New Run panel, tick two levels on one model, and confirm the checkbox group
and the board's effort chip render correctly in light, dark, and compact density.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/routes/Arena.live.tsx frontend/src/routes/Arena.css frontend/src/routes/Arena.live.test.tsx frontend/src/lib/arenaApi.ts frontend/src/types.ts
git commit -m "feat(arena): New Run picks several effort arms per model"
```

---

### Task 10: Full suite, changelog and subsystem docs

**Files:**
- Modify: `CHANGELOG.md`, `CLAUDE.md`

- [ ] **Step 1: Run the full backend suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS. Compare any failure against `main` — `main` carries pre-existing
failures, so a red test is only this branch's if it is green on `main`.

- [ ] **Step 2: Run the full frontend suite**

Run: `cd frontend && npm test && npx tsc --noEmit`
Expected: PASS, modulo the known flakiness (compare failing-file sets against `main`).

- [ ] **Step 3: Update `CHANGELOG.md`**

Under `[Unreleased]` → `### Added`:

```markdown
- **Arena: one model at several reasoning efforts on one board.** A contestant is
  now `(model, reasoning_effort)` — `ArenaMatch` carries an explicit
  `reasoning_effort` (migration `0058`) and the New Run panel picks several levels
  per model, including the unpinned vendor-default arm. The leaderboard ranks each
  arm separately and `merge_runs` folds arm-wise, so two efforts are never averaged
  into one EFF/CON. Transcripts and `--resume` are per-arm.
```

- [ ] **Step 4: Add the gotchas to `CLAUDE.md`**

In the **"Reasoning effort: an unset knob is omitted, never sent as null"** section,
append:

```markdown
- **A contestant is `(model_id, reasoning_effort)`, not a model** (migration
  `0058`). `ArenaMatch.reasoning_effort` is `''` for unpinned, never NULL: SQL
  treats NULLs as DISTINCT in a UNIQUE constraint, so a nullable column would
  silently stop protecting unpinned pairs at the DB level while the Python-level
  upsert still dedups — a backstop that looks present and is not.
- **`arena_run.reasoning_efforts` values are LISTS of arms** (`{slug: [null,
  "high"]}`); `null` is the explicit unpinned arm, and a model absent from the map
  runs once at its vendor default. Legacy scalar values are read as one-element
  lists by `task.effort_levels_for` and normalised at `store._run_to_dict` —
  derive on read, never migrate the JSON.
- **0058's backfill reads each row's OWN `config.reasoning_effort`**, never a blind
  `''`. Runs #107/#108 already recorded their regime; flattening them would let
  `merge_runs` — which now groups on the column — fold a `high` board into a `low`
  one, destroying the guard this feature is modelled on.
- **Anything keyed on `(workflow, model)` is now arm-blind and wrong.** Three sites
  needed the third key: `store.record_match`'s upsert (the second arm silently
  clobbered the first), `_save_transcript`'s directory (both arms wrote one
  `transcript.json` — the same clobber the per-trial copies fixed), and
  `launch_arena_run.py --resume` (a pair read as done when one arm scored, then the
  run was marked `completed` — run #104's failure shape). **`scorecard.py`'s
  `model_id → transcript path` map is still arm-blind** and picks one arm
  arbitrarily; known gap, not part of scoring.
- **`get_leaderboard` has NO `response_model`** — it hand-builds an explicit key
  projection, so a field the store gains is served only if named there. Different
  mechanism from `/api/agent/models`' pydantic drop, identical failure: the store
  unit test passes while the API serves nothing. Assert new board fields in
  `test_arena_api.py`, at the HTTP layer.
```

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md CLAUDE.md
git commit -m "docs(arena): per-effort contestants changelog and gotchas"
```

---

## Self-Review

**Spec coverage** — every section maps to a task: §1 data model → Task 1; §2 launch
payload → Task 3 (plus `RunSummary`/`CreateRunRequest` in Task 7); §3 execution and
evidence → Task 4, with `--resume` split into Task 8 because it lives in a different
file and carries its own test cycle; §4 read paths → Tasks 5 (leaderboard), 6
(merge), 7 (`_match_to_dict` + API); §5 frontend → Task 9; §6 testing → distributed
across each task's TDD cycle; §7 out of scope → recorded as a known gap in Task 10's
`CLAUDE.md` note rather than silently dropped.

**Type consistency** — `effort_levels_for(reasoning_efforts, model_id) -> list[str | None]`
is defined in Task 3 and consumed unchanged in Tasks 4 and 8.
`record_match(..., reasoning_effort: str | None = None)` is defined in Task 2 and
consumed in Tasks 4 and 6. The `''`-vs-`None` boundary is stated once and held: `''`
in the column, `None` at every dict/API boundary.

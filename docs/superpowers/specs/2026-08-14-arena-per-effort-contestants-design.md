# Arena: one model, several reasoning efforts, one board

**Date:** 2026-08-14
**Status:** design, approved for planning
**Touches:** `backend/app/models.py`, `backend/alembic/versions/0058_*`,
`backend/app/services/arena/{store,task}.py`, `backend/app/routers/arena.py`,
`scripts/launch_arena_run.py`, `frontend/src/routes/Arena.live.tsx`

## Problem

The `/arena` New Run panel cannot enter the same model twice at two reasoning
efforts, so the natural experiment — *does `high` beat `low` on this workflow?* —
can only be run as two separate boards.

That is not a UI limitation. The arena identifies a contestant by `model_id`
alone, at four layers:

| Layer | Shape |
|---|---|
| `Arena.live.tsx:629` | `reasoningEfforts: Record<slug, string>` |
| `queue_arena_run` (`task.py:31`) | `model_ids: list[str]` + `reasoning_efforts: dict[slug, effort]` |
| `_execute` (`task.py:589`) | `for model_id in model_ids: reasoning_efforts.get(model_id)` |
| `ArenaMatch` (`models.py:2644`) | `UniqueConstraint("run_id", "workflow_id", "model_id")` |

The constraint is the hard wall: even with the upper three layers fixed, the
second arm's match would collide with the first.

### Why this belongs in the identity, not beside it

`store.py:170` already refuses to merge matches of one `(workflow, model)` driven
at different efforts — *"the merged row would average two operating regimes into
one EFF/CON"*. If two efforts are two regimes for merging, they are two regimes
for a leaderboard row, which must therefore be two contestants and never one
averaged row.

Effort is measured, not assumed: runs #107/#108 recorded ~22% fewer tool calls at
`high` than at `low`, which moves EFF directly.

### Why one run rather than two

Two sequential boards reintroduce the position-in-the-field bias documented under
*Arena DB hygiene* — leftover rows make each successive match's name resolution
harder than the last, and the failure is silent. Within one run both arms sit
under the same per-match fixture purge.

## Decisions

| # | Decision | Rejected alternative |
|---|---|---|
| 1 | A contestant is `(model_id, reasoning_effort)`; both arms rank on one board | Two runs compared side by side — no identity change, but no single ranking and `merge_runs` still refuses to combine them |
| 2 | Effort gets an explicit **column** | Composite `model_id` (`gpt-5-5@high`) — no migration and it separates the transcript path for free, but makes `model_id` a field you *parse* rather than *look up*, in a column read by the frontend, `scorecard.py` and 11 boards of history |
| 3 | `reasoning_efforts` values become **lists**; `model_ids` unchanged | A flat `contestants: [{model_id, effort}]` list — most literal, but retires `ArenaRun.model_ids`, which the runs list, `--resume` and `merge_runs` all read |
| 4 | Levels are a **checkbox group**, and the unpinned arm is tickable | Single `Select` plus "+ another effort"; or Default as empty-state only, which makes the historical baseline unmeasurable |

## 1. Data model

`ArenaMatch` gains:

```python
reasoning_effort: Mapped[str] = mapped_column(
    String(20), nullable=False, default="", server_default="")
```

`''` means *vendor default (unpinned)*. **Not NULL:** SQL treats NULLs as
distinct in a UNIQUE constraint, so `(run, wf, model, NULL)` would be insertable
twice and the DB backstop would silently stop protecting unpinned pairs. The
Python-level upsert in `record_match` would still dedup (SQLAlchemy renders
`IS NULL`), so NULL is a weakened backstop rather than a live bug — but `''`
keeps the constraint strict for every row.

The constraint becomes:

```python
UniqueConstraint("run_id", "workflow_id", "model_id", "reasoning_effort",
                 name="uq_arena_match_run_workflow_model_effort")
```

### Migration `0058_arena_match_reasoning_effort`

Three requirements, each from a defect this repo has already paid for:

1. **Idempotent.** `0001_initial` runs `Base.metadata.create_all`, so a fresh
   database reaches revision 1 already carrying the new column *and* the
   4-column constraint. Guard on `inspect(bind)`: skip the column add when it
   exists, and skip the constraint rebuild when
   `uq_arena_match_run_workflow_model_effort` is already present.
   `tests/test_migration_fresh_chain.py` is the standing gate.

2. **`batch_alter_table` for the constraint change.** SQLite cannot alter a
   constraint in place; batch mode rebuilds the table (create, copy, drop,
   rename). This is the `0051` lesson — the test must pin that row data, sibling
   FKs and the other indexes all survive the rebuild.

3. **Backfill from `config`, never blindly to `''`.**

   ```sql
   UPDATE arena_match
      SET reasoning_effort = COALESCE(json_extract(config, '$.reasoning_effort'), '')
   ```

   Runs #107/#108 already store `config.reasoning_effort`. Writing `''` over
   every historical row would assert that a `high` board and a `low` board were
   the same regime, and `merge_runs` — now grouping on the column — would fold
   them. The feature would destroy the guard it is modelled on.

## 2. Launch payload

`ArenaRun.reasoning_efforts` values become lists of levels:

```json
{"gpt-5-5": [null, "high"], "grok-4-6": ["medium"]}
```

- `null` = the unpinned baseline arm.
- A model absent from the map = exactly one contestant, at vendor default
  (today's behaviour, unchanged).
- **Legacy values are read, not migrated:** a stored `"high"` is read as
  `["high"]`. Same derive-on-read discipline as `_derive_card`; no JSON column
  migration, no backfill of `arena_run`.

`queue_arena_run` changes:

- Validate **per level** through the existing `effort_rejection` seam — still the
  single seam shared with `resolve_agent_model_selection` and both UI ladder
  builders. A `null` entry skips it: unpinned is always legal, including for the
  wire-protocol models that can carry no effort at all.
- **Raise on a duplicate level** within one model's list. Deduping `["high",
  "high"]` silently would mint two contestants sharing one key, and the second
  would upsert over the first — a clobber that presents as a completed board.
- Keep the existing rejection of a map key naming a model not in the run.

`store.create_run` and the router's `CreateRunRequest.reasoning_efforts` widen to
`dict[str, list[str | None]]`, accepting a bare `str` for backward compatibility.

`RunSummary.reasoning_efforts` (`routers/arena.py:37`) widens the same way. It is
declared `dict[str, str]` and **constructed explicitly** at `arena.py:252` and
`:274`, so a list value raises a pydantic `ValidationError` at construction —
this one fails loudly rather than silently, in both the runs list and the run
detail route.

## 3. Execution and evidence

**`_execute`** gains an inner loop; `_levels_for(efforts, model_id)` returns
`[None]` when the model is absent:

```python
for workflow_id in workflow_ids:
    for model_id in model_ids:
        for effort in _levels_for(reasoning_efforts, model_id):
```

**Progress total** becomes `len(workflow_ids) × Σ(levels per model) × trials_n`.
The current product under-counts once any model carries two arms, and a finished
run reads as permanently stuck.

**`_save_transcript`** nests an effort segment:

```
<artifact_root>/<workflow_id>/<model_id>/<effort or "default">/transcript.json
```

Without this both arms write the same `transcript.json` and the second clobbers
the first — the same failure the per-trial copies fixed one level up, where *"the
evidence you most need is exactly the one that got clobbered."* Under the column
encoding nothing forces this fix, so it is an explicit line item. Historical rows
are unaffected: `ArenaMatch.transcript_path` stores the resolved path.

**`record_match` / `_record_pair`** take the effort and include it in the upsert
filter. `config["reasoning_effort"]` continues to be written for every status —
the column is a promoted, queryable copy, not a replacement.

**`launch_arena_run.py --resume`**: `done` and `pairs` gain the effort dimension.
Today they are `{(workflow_id, model_id)}`, so a resume would see the pair as
scored because the `low` arm finished, never run the `high` arm, and mark the run
`completed`. That is run #104's failure shape — a board reporting success with an
arm missing. The file's own comment already reasons one dimension short of this:
*"Resume MUST reuse the run's own efforts, never re-read a flag."*

## 4. Read paths

**`store.leaderboard`** groups by `(model_id, reasoning_effort)`. Every
per-model `defaultdict` re-keys to the tuple; each row gains
`"reasoning_effort": str | None`; the display stabilizer in the sort becomes the
pair rather than `model_id`. Card derivation, OVR ranking, shared ranks and the
GRD→ADH→SYN→EFF→PRC tie-break are untouched, so **a board where every model
carries one effort produces bit-identical output to today** — the regression gate
for this change.

**`store.merge_runs`** groups by `(workflow_id, model_id, reasoning_effort)`. Its
existing effort check becomes redundant by construction; it stays in place with a
comment saying so rather than being deleted, since it is the assertion this whole
design derives from.

**`_match_to_dict`** surfaces the column alongside the config value.

**`reasoning_effort` must be added to the leaderboard route's explicit key
projection** (`routers/arena.py:344`). No route in this router declares a
`response_model`; `get_leaderboard` instead hand-builds a `renamed` dict naming
each key it forwards. The mechanism differs from the `/api/agent/models` defect
(pydantic dropping an undeclared key) but the failure is identical: the store
gains a field, its unit test passes, and the API serves none of it. The fix is
also identical — **assert it at the HTTP layer** in `test_api.py`, not against
`store.leaderboard`.

## 5. Frontend

`frontend/CLAUDE.md` and `UI_STYLE_GUIDE.md` govern: token-only styling, reuse
existing primitives, verify in both themes and compact density.

- State becomes `reasoningEfforts: Record<slug, string[]>`, with the existing
  `'default'` string as the UI sentinel for the unpinned arm, mapped to wire
  `null` at launch.
- The per-model `Select` becomes a checkbox group over
  `['default', ...reasoningEffortsFor(m)]`, reusing `wl-arena__checklist` one
  level deeper. Hidden entirely for a model with no ladder, as today.
- Empty selection sends nothing for that model — identical to ticking only
  Default.
- The launch filter keeps re-checking each level against the model's ladder (the
  reset is display-only and state survives deselect/reselect), and always admits
  `'default'`.
- **`Arena.live.tsx:944` `rowKey={(r) => r.model_id}` becomes
  `` `${r.model_id}::${r.reasoning_effort ?? ''}` ``** — two arms of one model are
  duplicate React keys today.
- Board rows label the arm with an effort chip beside the display name; a row
  with no pinned effort renders unchanged.

## 6. Testing

- **Store:** two efforts of one model produce two leaderboard rows, ranked
  independently; a single-effort board is byte-identical to the pre-change
  output; `merge_runs` groups by the triple and still refuses cross-effort folds.
- **Upsert:** `record_match` at two efforts inserts two rows; the same effort
  twice updates one.
- **Launch:** per-level validation rejects an off-ladder level naming its own
  model; a duplicate level raises; a `null` arm is accepted for a model whose
  client can carry no effort.
- **Migration:** fresh-chain reaches head; rebuild preserves rows, FKs, indexes;
  backfill derives `high` from a legacy row's `config`, `''` from an unpinned one.
- **HTTP:** `reasoning_effort` survives the leaderboard response model.
- **Frontend:** ticking two levels launches one payload with both; the board
  renders two rows without duplicate-key warnings.

## 7. Out of scope

- **`scorecard.py:94`** maps `model_id → transcript path`, highest run id wins.
  With two arms it picks one arbitrarily. Flagged, not fixed — the scorecard is a
  docs-generation path, not part of scoring, and making it effort-aware is its own
  change.
- Cross-run comparison views. `merge_runs` continues to refuse cross-effort folds
  by design.
- Any change to how effort is transported to a provider
  (`arena_model_to_selection`, the omit-when-unset rule, the two protocols'
  different carrier fields) — all unchanged.

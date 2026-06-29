# Parameterized Desk Workflows — Design

**Date:** 2026-06-27
**Status:** Approved (brainstorming complete; ready for plan)
**Builds on:** [[project_desk_workflows_module]] (Desk Workflows, merged 2026-06-26 `972245e`)

## Goal

Let a desk workflow accept named parameters (portfolio name, date range, free-text
notes, …) supplied at launch time and substitute them into the script's `step()`
prompts. Launching `risk-manager-control-day` for portfolio `Default` over
`2026-06-25 … 2026-06-26` should drive a first step whose prompt reads
"What does the latest risk say for the portfolio: Default?" rather than a hard-coded
literal.

## Locked Decisions (from brainstorming)

1. **Substitution = inject `args`, author uses f-strings.** The runner injects an
   `args` object into the script namespace; the author writes a normal Python
   f-string: `await step(f"Latest risk for portfolio {args.portfolio}?")`. No new
   template engine. Fits the locked "Python script = source of truth" decision.
2. **Declaration = typed specs in `meta['params']`** — each param is
   `{name, label, type}` with `type ∈ {string, date, portfolio}`.
3. **Launch input = a small form.** Selecting a parameterized workflow from the
   slash picker opens a dialog with one field per declared param. No inline
   `/slug "Default", 2026-06-25` positional parsing in v1.

## Non-Goals (YAGNI)

- **No optional params.** Every declared param is required at launch.
- **No inline positional parsing** of `/slug "a", b, c`. Form only.
- **No type coercion** beyond validation. All values reach the script as strings
  (dates as ISO `YYYY-MM-DD`). The natural-language prompt consumes the string.
- **No separate persistence of the chosen args.** The substituted (final) prompt is
  what gets sent to the orchestrator and persisted as the step message, so the
  chosen values are implicitly captured in thread history.

## Architecture

The change threads a validated `args: dict[str, str]` from the launch form, through
the run endpoint, into the workflow runner's exec namespace. Five units change; each
has a narrow interface and is independently testable.

```
ChatComposer (/slug picker)
   └─ params present? → WorkflowParamsDialog ──(args)──┐
                                                       ▼
useAgentChatController.launchWorkflow(slug, mode, args)
   └─ POST /api/chat/threads/{id}/workflows/{slug}/run  { mode, args }
                                                       ▼
main.run_thread_workflow
   └─ validate_workflow_args(meta, args)  ── 422 on bad input
                                                       ▼
run_desk_workflow(..., args)
   └─ ns["args"] = _Args(validated)   →  f-string in step() prompt
```

## Unit 1 — Param declaration & validation (`desk_workflows_script.py`)

`meta['params']` is an **optional** list. Absent ⇒ a zero-param workflow (today's
behavior, unchanged). When present, each entry is a dict:

| key     | rule                                                                 |
|---------|----------------------------------------------------------------------|
| `name`  | required; must match `^[a-z][a-z0-9_]*$` **and not be a Python**      |
|         | **keyword** (`keyword.iskeyword`); unique within the list; not in the |
|         | reserved set `{step, log, args}`.                                    |
| `label` | required; non-empty string (human field caption — spaces allowed).   |
| `type`  | required; one of `string`, `date`, `portfolio`.                      |

The identifier constraint is what makes `args.portfolio` work — the *space* the user
might want ("Portfolio name") lives in `label`, never in `name`.

**New constant:** `VALID_PARAM_TYPES = {"string", "date", "portfolio"}`,
`_PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")`,
`_RESERVED_PARAM_NAMES = {"step", "log", "args"}`.

**New function** `validate_params(meta) -> list[dict]`:
- Returns `[]` if `params` absent.
- Raises `WorkflowScriptError` if `params` is not a list, an entry is not a dict,
  a required key is missing/non-string, `name` fails the identifier regex, `name` is a
  Python keyword (`keyword.iskeyword(name)` — `for`/`class`/`from`/`if`/…, which the
  lowercase regex would otherwise admit but `args.for` cannot reference), `name` is
  duplicated, `name` is reserved, or `type` is not in `VALID_PARAM_TYPES`.
- Returns the normalized list of `{name, label, type}` dicts.

`validate_script` calls `validate_params(meta)` after its existing checks so a saved
workflow with a malformed `params` block is rejected at save time (422 via the
existing `_upsert` path).

**New function** `validate_workflow_args(meta, args) -> dict[str, str]`:
- First: `if not isinstance(args, dict): raise WorkflowScriptError("args must be an
  object")`. This rejects a truthy non-dict payload (string/list/number) with a 422
  rather than letting it reach `.get`/iteration and raise an opaque `TypeError`.
- `params = validate_params(meta)`.
- For each declared param: the key must be present in `args` with a non-empty
  (after `strip()`) string value, else `WorkflowScriptError(f"missing required
  parameter {name!r}")`.
- For `type == "date"`: value must match `^\d{4}-\d{2}-\d{2}$` **and** parse via
  `datetime.date.fromisoformat`, else `WorkflowScriptError(f"parameter {name!r} must
  be an ISO date (YYYY-MM-DD)")`.
- `type` `portfolio`/`string`: any non-empty string accepted.
- Any key in `args` not matching a declared param ⇒ `WorkflowScriptError(f"unknown
  parameter {key!r}")`.
- Returns a clean `{name: stripped_value}` dict containing exactly the declared names.

## Unit 2 — `args` injection (`desk_workflow_runner.py`)

`run_desk_workflow` gains a keyword param `args: dict[str, str] | None = None`
(default `None` ⇒ `{}`, preserving every current call site / test).

A small read-only helper class lives in the runner:

```python
class _Args:
    """Read-only view of launch params: args.portfolio and args["portfolio"]."""
    def __init__(self, values: dict[str, str]) -> None:
        object.__setattr__(self, "_values", dict(values))
    def __getattr__(self, name: str) -> str:
        try:
            return self._values[name]
        except KeyError:
            raise WorkflowScriptError(
                f"workflow referenced undeclared parameter {name!r}"
            ) from None
    def __getitem__(self, name: str) -> str:
        return self.__getattr__(name)
```

The exec namespace becomes:
```python
ns = {"__builtins__": dict(_SAFE_BUILTINS), "step": step, "log": log,
      "args": _Args(args or {})}
```

A reference to an undeclared parameter raises `WorkflowScriptError`, which the
existing `_execute` try/except converts to a `workflow.step.error` SSE frame — no new
error path. The AST guard (`guard_script`) is unchanged: f-strings compile to
`FormattedValue` nodes (allowed) and `args.portfolio` / `args["portfolio"]` are
ordinary attribute/subscript access (not dunder, not `.format`).

> Note: `_Args.__getattr__` reads `self._values`, set via `object.__setattr__`, so the
> attribute lookup for `_values` itself never recurses.

## Unit 3 — Run endpoint (`main.py`)

`run_thread_workflow` reads `args = (payload or {}).get("args")` — defaulting **only a
missing/None value** to `{}` (`if args is None: args = {}`), *not* `or {}`, so a
falsy-but-wrong value like `[]` or `""` is not silently swallowed but flows into
`validate_workflow_args`, which rejects non-dicts. It then calls
`validate_workflow_args(extract_meta(wf.script), args)` before streaming. On
`WorkflowScriptError` it raises `HTTPException(status_code=422, detail=str(exc))`.
The validated dict is passed to `run_desk_workflow(..., args=validated)`.

(`extract_meta` is already imported indirectly; import it from
`desk_workflows_script` alongside the validators.)

## Unit 4 — Summary schema carries params (`schemas.py`)

`DeskWorkflowSummaryOut` gains `params: list[dict] = []` so the composer knows whether
to open the form without fetching the full script.

`DeskWorkflowSummaryOut` uses `model_config = {"from_attributes": True}` (ORM mode) and
the `DeskWorkflow` table has **no** `params` column — so the field must read from a
derived attribute, not a stored one. Add a read-only **property** to the
`DeskWorkflow` ORM model that derives params from the (already-validated) stored
script, with a local import to avoid the `models ← services` circular dependency:

```python
# models.py, on class DeskWorkflow
@property
def params(self) -> list[dict]:
    from .services.desk_workflows_script import extract_meta, validate_params
    try:
        return validate_params(extract_meta(self.script))
    except Exception:
        return []  # defensive; stored scripts are validated at save time
```

`from_attributes=True` then picks up `obj.params` automatically — no change to
`list_desk_workflows` or the router shaping, no new column.

## Unit 5 — Launch form (frontend)

**New `WorkflowParamsDialog.tsx`** (+ co-located `.css`, token-only): a modal with one
field per param.
- `date` → existing `DatePicker` (`value`/`onChange(iso)`/`label`).
- `portfolio` → `<select>` (themed `wl-field`/`wl-input` primitive) populated from
  `GET /api/portfolios` names.
- `string` → text input.
- **Run** disabled until every field is non-empty. **Cancel** closes without
  launching. Props: `{ open, workflow, portfolios, onCancel, onRun(args) }`.

**`ChatComposer.tsx`**: when the chosen workflow has `params.length > 0`, `launch(w)`
opens the dialog (lifts a `pendingWorkflow` state up via a new
`onRequestParams?(workflow)` callback) instead of calling `onLaunchWorkflow`
directly. Zero-param workflows keep launching immediately. The bare-`/slug` picker
suppression on space is unchanged.

**`useAgentChatController.launchWorkflow`**: signature becomes
`(slug, mode, args?: Record<string, string>)`; the POST body includes
`args: args ?? {}`. `threadSource` deps unchanged.

**Types (`types.ts`)**: `DeskWorkflowSummary` gains
`params?: { name: string; label: string; type: 'string' | 'date' | 'portfolio' }[]`.

The desk surface does not currently load portfolios, so the dialog's live container
fetches `GET /api/portfolios` once on open (lazy — only when a `portfolio`-typed param
exists) and maps to `name` strings. The presentational `WorkflowParamsDialog` receives
`portfolios: string[]` as a prop so it stays test-friendly without network.

## Unit 6 — Seed (`desk_workflow_seed.py`)

Update the flagship `risk-manager-control-day` script: add the three params
(`portfolio`/portfolio, `start`/date, `end`/date) to `meta` and rewrite its step
prompts to interpolate `args` (e.g. `f"What does the latest risk say for the portfolio:
{args.portfolio}?"`, `f"... between {args.start} and {args.end} ..."`). This both
proves the feature end-to-end and updates the boot-seed + migration-0035 inline
literals (which share `desk_workflow_seed.py` constants).

## Error Handling

| Condition                                    | Result                              |
|----------------------------------------------|-------------------------------------|
| Malformed `meta['params']` at save           | 422 from `validate_script` (save)   |
| Missing/empty required arg at launch         | 422 from `validate_workflow_args`   |
| `date` arg not `YYYY-MM-DD`                   | 422                                 |
| Unknown arg key                              | 422                                 |
| Script references undeclared `args.x`        | `workflow.step.error` SSE (halts)   |
| Zero-param workflow launched with `{}`       | runs unchanged                      |

## Testing

**Backend**
- `validate_params`: happy (3 typed params); rejects non-list, non-dict entry,
  missing key, non-identifier name (`"portfolio name"`), Python-keyword name (`"for"`),
  duplicate name, reserved name (`args`), unknown type. Absent `params` ⇒ `[]`.
- `validate_workflow_args`: happy; non-dict `args` (`"foo"`, `[]`) ⇒ rejected; missing
  required; empty/whitespace value; bad date (`2026-13-01`, `06/25/2026`); unknown key;
  portfolio/string accept any non-empty.
- Runner: injects `args`; f-string substitution reaches the driven prompt (assert the
  prompt forwarded to the `drive` seam contains the substituted value); undeclared
  `args.x` surfaces a `workflow.step.error`; no-args call still drives.
- Endpoint: 422 on bad args; 200 + stream on good args; zero-param unchanged.

**Frontend**
- `WorkflowParamsDialog`: renders one field per param with the right control per
  type; Run disabled until all filled; `onRun` receives `{name: value}`; Cancel.
- `ChatComposer`: selecting a parameterized workflow calls `onRequestParams` (not
  `onLaunchWorkflow`); a zero-param workflow still calls `onLaunchWorkflow`.
- `launchWorkflow` posts `args` in the body.

## Coupling / Risk

- `DeskWorkflowSummaryOut` field addition — check the summary-shape test (a field add
  is additive; existing assertions keep passing).
- Frontend `routing.test` / type counts unaffected (no new route).
- The seed script edit touches both the boot-seed and the migration-0035 inline
  literal; both source `desk_workflow_seed.py` constants, so one edit covers both —
  verify the migration still imports the constant rather than duplicating the string.

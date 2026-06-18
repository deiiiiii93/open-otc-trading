# Frontend Skill Management + Hot Reload — Design

**Date:** 2026-06-06
**Status:** Approved (brainstorming complete)

## Context

Agent skills live on disk under `backend/app/skills/` in three tiers:

- `meta/` — policy fragments composed into persona system prompts at orchestrator **build time** (`compose_persona_prompt`, validated by `validate_meta_policy_file`).
- `references/` — domain docs read by personas via `read_file` at run time.
- `workflows/<domain>/<name>/SKILL.md` — workflow skills, surfaced per agent run by `EnvelopeSkillsMiddleware` (frontmatter envelope filter; content read fresh from disk every run).

Today the only management surface is a text editor + git. Two pain points:

1. No UI visibility or editing for the skill catalog; lint feedback arrives only at CI time (`skill_lint.py`).
2. Adding a workflow skill requires manually adding a routing line to the orchestrator prompt's `### Known single-persona skills` table (`prompts/orchestrator.md`). Forgetting it produces a skill that personas can use but the orchestrator never routes to (catalog ≠ orchestrator knowledge).

"Hot reload" is already half-true: workflow SKILL.md edits take effect on the next agent run. What is *not* hot: `meta/` policies, persona identities, and the orchestrator prompt — all baked at `build_orchestrator()` time. Precedent for rebuilding exists: `POST /api/agent/channels/reload` → `AgentService.rebuild_default_model()`.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Management scope | Full CRUD for `workflows/`; view + edit (no create/delete) for `meta/` and `references/` |
| Reload trigger | Auto-rebuild orchestrator on every successful UI write; manual ⟳ button for external edits |
| Validation | Block saves on CI-error-level lint; show warnings non-blocking |
| New-skill routability | Data-driven: orchestrator routing table generated from skill frontmatter at build time |
| Usage context | Local dev tool — single user, no auth/concurrency control, git remains source of truth |
| Editor style | Structured form for workflows (frontmatter as form controls + markdown body pane); raw text editor for meta/references |
| Architecture | File-CRUD REST API + rebuild-on-write (Approach A; DB-backed store and thin generic file API rejected) |

## Backend

### Router

New module `backend/app/routers/skills.py` exposing a factory:

```python
def build_skills_router(
    agent_service: AgentService,
    *,
    skills_root: Path = SKILLS_ROOT,
) -> APIRouter: ...
```

Included from `create_app()` via `app.include_router(...)` (first occupant of the empty `routers/` dir). The factory closes over `active_agent_service` like the inline endpoints do; `skills_root` is injectable so router tests run against a `tmp_path` copy of the tree.

### Endpoints

All under `/api/skills`:

| Endpoint | Behavior |
|---|---|
| `GET /api/skills/catalog` | Tree across all 3 tiers. Per file: tier, relative path, parsed frontmatter (or parse error), lint results, body token count |
| `GET /api/skills/{tier}/{path:path}` | One file: raw content + parsed `{frontmatter, body}` + lint results |
| `PUT /api/skills/{tier}/{path:path}` | Update. Workflows accept structured `{frontmatter, body}` (server serializes canonical YAML); meta/references accept raw `{content}`. Validate → atomic write (temp + rename) → rebuild |
| `POST /api/skills/workflows` | Create `{domain, name, frontmatter, body}` → `workflows/<domain>/<name>/SKILL.md`. `domain` must be one of the existing 11 workflow domains (see "Persona visibility"). `name` kebab-case, equals dir name |
| `DELETE /api/skills/workflows/{domain}/{name}` | Delete SKILL.md, prune empty dirs, rebuild. Workflows tier only |
| `POST /api/skills/validate` | Dry-run validation of an unsaved payload (never writes). Powers debounced live form feedback, incl. exact tiktoken body count |
| `POST /api/skills/reload` | Manual `rebuild_orchestrator()` for out-of-band edits (vim, git pull) |

### Validation per tier (reuse, don't reimplement)

- `workflows` → `skill_lint.lint_skill_file(mode="ci")`; severity `error` blocks (422), warnings returned non-blocking.
- `meta` → `skills_loader.validate_meta_policy_file`.
- `references` → frontmatter-required check (the `test_reference_docs` invariant).

Validation order is **validate → write → rebuild**; a lint failure must reject before touching disk because the agent reads these files live mid-run.

### Hot reload

New narrow method `AgentService.rebuild_orchestrator()`: re-runs `build_orchestrator()` with the **existing** model, checkpointer, and tools — no channel-registry or `.env` re-read (unlike `rebuild_default_model()`; coupling them would make skill saves fail on unrelated channel problems). Called after every successful write/create/delete. In-flight semantics match `channels/reload`: running streams finish on the old graph (held alive by Python references); subsequent requests use the new one.

### Safety

- All paths resolved and confined to `skills_root`; traversal rejected with 400 before any I/O.
- `.md` files only; workflow writes only at canonical `workflows/<domain>/<name>/SKILL.md`.
- Atomic writes (temp file + rename) so a mid-write agent run never reads a half file.
- Canonical frontmatter serialization in existing field order (`name, description, domain, workflow_type, allowed_envelopes, may_escalate_to, required_context, optional_context, write_actions, confirmation_required, success_criteria, routing`) so UI saves produce minimal git diffs — git review is the safety net for this tool.

## Data-driven orchestrator routing

### Frontmatter schema

New **optional** key on workflow skills:

```yaml
routing:
  - request: "Fetch current market data"
    persona: trader
```

A list because some skills route to different personas for different request shapes (`price-portfolio` trader-lens vs risk-lens; `hedge-portfolio` sizing vs booking). **Absence is meaningful**: skills without `routing` don't appear in the orchestrator table — correct for sub-workflows reached only via persona catalogs (e.g., `position-snapshot`, `read-risk-result`).

### Generation

- The static `### Known single-persona skills` table is removed from `prompts/orchestrator.md` and replaced with a sentinel line: `<!-- KNOWN_SKILLS_TABLE -->`.
- `_orchestrator_prompt()` scans workflow frontmatter at build time, renders the table (sorted by domain, then skill name — deterministic), and substitutes at the sentinel.
- **Build fails loudly if the sentinel is missing** — a silent no-op would strip routing from the agent.

### Backfill

Each row of today's hand-written table moves verbatim into the owning skill's `routing` entries (request-shape strings preserved character-for-character). Touches the ~20 skills currently in the table.

### Lint additions (CI-error level)

`routing`, when present, must be a list of `{request, persona}` mappings; `persona` ∈ `{trader, risk_manager, high_board}`; `request` non-empty string. Missing `routing` is not even a warning.

### Persona visibility

Workflow domain visibility is **deliberately scoped per persona** in `personas.py` (trader: 9 domains, risk_manager: 8, high_board: 2; union = all 11 existing domains). Two consequences:

1. **Creation is restricted to existing domains** (form dropdown). A brand-new domain dir would be invisible to every persona until `personas.py` changes — out of scope; the create endpoint returns 400 for unknown domains.
2. **New routing cross-check** (CI-error + blocks UI saves): each `routing` entry's `persona` must have the skill's domain in its source list — catching "orchestrator routes to a persona that cannot read the skill", exactly the bug class this feature exists to kill. To make this checkable, the per-persona domain lists move to a shared `PERSONA_WORKFLOW_DOMAINS` constant consumed by both `personas.py` specs and the lint check (pure refactor; persona behavior unchanged, pinned by existing envelope-filter tests).

### What stays hand-written

The prose `## Routing` rules (booking intent, quote-first, hedge-execution judgment) and `## Compound Routing Contracts` — they encode sequencing judgment, not per-skill facts.

## Frontend

### Files (existing page conventions)

- `frontend/src/routes/Skills.tsx` — presentational: catalog tree + editor, data via props.
- `frontend/src/routes/Skills.live.tsx` — data wrapper: fetches catalog, owns save/create/delete/validate/reload calls, reports `onPageContextChange`.
- `frontend/src/routes/Skills.css`, `Skills.test.tsx`, `Skills.live.test.tsx`.
- Wiring: `types.ts` Route union + `'skills'`; `main.tsx` navItems entry, route render, `jump-skills` command-palette item.
- `api/client.ts`: `listSkillsCatalog`, `getSkillFile`, `saveSkillFile`, `createWorkflowSkill`, `deleteWorkflowSkill`, `validateSkill`, `reloadSkills` (thin typed-fetch style).

### Behaviors

- **Tree**: groups WORKFLOWS (by domain) / REFERENCES / META; per-entry lint badge (✓ clean, ⚠ warnings, ✕ errors) from catalog payload; filter box.
- **Editor**: workflows → structured form (selects for domain/workflow_type, checkboxes for envelopes, tag inputs for context lists, toggles for booleans, list editor for success_criteria, routing-entry rows `request shape × persona`, markdown body pane with live `n / 500` token counter). meta/references → raw text editor.
- **Live lint**: edits debounce (~500 ms) into `POST /api/skills/validate`; errors render inline and disable Save; warnings show non-blocking. The form never reimplements lint in TS (tiktoken has no faithful TS equivalent).
- **Save** → PUT → toast "Saved · agent reloaded", or warning state for `{saved: true, reloaded: false}`.
- **Create** → blank form with template body skeleton (`## When to use` / `## Procedure` / `## Example`) so a new skill starts lint-passable.
- **Delete** → confirm dialog, including any cross-reference warnings (below).
- **⟳ Reload skills** toolbar button + last-reload status for out-of-band edits. Staleness model is simple: refetch catalog after every mutation; no FS watcher, no polling.

## Error handling

| Failure | Response | UI treatment |
|---|---|---|
| Lint errors on save/create | `422` + `[{code, message, detail, severity}]`, disk untouched | Inline annotations on matching form fields / body pane |
| Saved but rebuild failed | `200 {saved: true, reloaded: false, reload_error}` | Persistent banner: "saved; agent still on old prompt — fix & press Reload" |
| Create over existing skill | `409` | Form error on name field |
| Unknown path / bad tier | `404` / `400` | Toast |
| Path traversal, non-kebab name | `400` | Form error |

**Delete cross-reference warning**: deletion greps `orchestrator.md` (prose + compound sections) and persona prompt files for the skill name; matches return as a non-blocking warning in the confirm dialog. Generated-table references need no check — they vanish with the frontmatter.

## Testing

- **Backend router (pytest)**, against `tmp_path` skills copy + stub agent service recording `rebuild_orchestrator()` calls: CRUD happy paths; lint-blocked PUT leaves disk untouched; traversal rejection; duplicate create → 409; unknown-domain create → 400; delete prunes dirs and rebuilds; validate never writes; rebuild-failure → `{saved: true, reloaded: false}`.
- **Routing generation**: one-time **equivalence pin** — table generated from backfilled frontmatter reproduces today's hand-written table as an exact (request, persona, skill) row set; permanent **drift test** — generated rows ⇔ frontmatter `routing` entries, parametrized over the catalog; sentinel-missing → loud error; lint rejects bad personas/malformed entries and routing entries whose persona cannot see the skill's domain (`PERSONA_WORKFLOW_DOMAINS` cross-check, validated against the backfilled catalog where every current table row must pass).
- **Catalog-pinning suite** (migration gate): the 5 pinned files (`test_skills_catalog`, `test_skills_catalog_v2`, `test_workflow_skills_phase3`, `test_remaining_workflow_skills_phase3`, `test_reference_docs`) must pass **unchanged** after backfill (skill set unchanged; `routing` optional).
- **Frontend**: vitest (`Skills.test.tsx` — tree badges, form rendering, lint-errors-disable-save, routing-row add/remove; `Skills.live.test.tsx` — save→reload-status flow, debounced validate, create/delete) **plus an explicit `tsc` run** (vitest does not typecheck).
- **Manual**: run the app, edit a skill via UI, confirm next agent run reflects the change and the rebuild log line appears.

## Accepted trade-offs

- **Runtime CRUD de-syncs the catalog-pinning tests** until the change is committed and the pins updated — accepted under the local-dev-tool model; git review/commit is the workflow, the UI is an editor over repo files.
- **No optimistic concurrency / auth** — single local user by decision; revisit only if this becomes a deployed admin page.
- **No new-domain creation from the UI** — persona domain scoping is intentional access control, and wiring a new domain into personas is a judgment call that belongs in code review, not a form.

## Out of scope

- Editing persona identity prompts (`prompts/*.md`) from the UI.
- Skill version history / audit trail (git covers it).
- Envelope-preview ("what would persona X see") tooling.
- Filesystem watcher auto-reload.

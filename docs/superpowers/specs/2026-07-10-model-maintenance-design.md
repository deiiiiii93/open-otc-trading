# Model Maintenance UI — design spec

**Date:** 2026-07-10
**Status:** Approved (brainstorm), pending implementation
**Topic:** A web page to add/edit/delete LLM channels and models instead of hand-editing `config/agent_channels.yaml`.

## Problem

The agent's dispatchable models live in `config/agent_channels.yaml` (per-environment,
gitignored; tracked template is `config/agent_channels.example.yml`). Onboarding a new
model — e.g. an arena contestant needing a `protocol` override — today means hand-editing
YAML on the server, which is error-prone (a typo breaks the whole registry) and requires
shell access. We want a **Model Maintenance** page giving operators a safe UI to manage
channels and models, with changes going live without a restart.

The registry already has the two seams this needs:

- **Read:** `GET /api/agent/models` → `agent_model_config(get_registry())`
  (`model_factory.py:186`). It serves the chat picker and omits editable fields
  (`base_url`, `anthropic_base_url`, `api_key_env`, per-model `protocol`).
- **Hot reload:** `POST /api/agent/channels/reload` (`main.py:784`) →
  `channel_registry.reload()` + `active_agent_service.rebuild_default_model()`.

What is missing is any code that **writes** the YAML. Loading (`channel_registry.py:100
load_from_path`) is read-only.

## Decisions

1. **File-backed CRUD over the live YAML — one source of truth.** Mutations edit
   `config/agent_channels.yaml` in place (the file resolved by `channel_registry._yaml_path()`
   — `AGENT_CHANNELS_FILE` env, else `Settings.agent_channels_file`, else
   `./config/agent_channels.yaml`). Rejected a DB overlay: the whole system already reads the
   YAML as source-of-truth and hot-reloads it; a DB would create two competing sources and a
   merge layer. Follows the **Skills page** pattern (atomic file write + validation + hot
   reload + write-gate flag).

2. **Full scope: models + channels + default.** Add/edit/delete individual models within a
   channel; add/edit/delete whole channels; and set the registry default `{channel, model}`.

3. **Validate-then-commit, reusing `load_from_path`.** Every mutation writes the candidate
   YAML to a temp file and calls the existing `load_from_path(temp)`; only on clean load does
   it `os.replace()` onto the live file and reload. On `ValueError` → HTTP 422, live file
   untouched. This reuses *all* existing schema validation (zenmux-requires-`anthropic_base_url`,
   provider/protocol enums, unique ids, default-resolves) for free and makes corrupt saves
   structurally impossible.

4. **Secrets: `api_key_env` name only, never the secret.** The UI edits the *name* of the env
   var and displays a per-channel **health** badge (derived: is that env var currently set?).
   Secrets never touch the YAML or the web layer. A missing key surfaces as an unhealthy badge
   with the env-var name to set in `.env` + restart. Rejected writing secrets into `.env`.

5. **UI writes the live YAML only; never the tracked `example.yml`.** UI edits are
   per-environment operational changes; auto-writing them into the tracked template causes git
   churn and leaks env-specific models. The CLAUDE.md "edit both" rule stays a human convention
   for structural/schema changes.

6. **`ruamel.yaml` round-trip to preserve comments + key order.** The live YAML carries human
   comments (e.g. the "keep in sync with CANDIDATE_MODELS" note). `ruamel.yaml` (round-trip
   mode) preserves comments and key order across UI writes; PyYAML's `safe_dump` would strip
   them. `ruamel.yaml` is **not currently installed** — it must be added to `backend/pyproject.toml`
   deps and `uv sync`'d. Reads for validation still go through the existing PyYAML `load_from_path`
   (unchanged); ruamel is used **only** in the writer for round-trip mutation.

7. **Writes gated behind `OPEN_OTC_FEATURE_MODEL_WRITE_API`, defaulting ON.** Mirrors Skills'
   `feature_skills_write_api` / `_require_write_api` (403 when off). This desk wants the page
   usable, so it defaults `True`. The flag has the repo's **dual representation** — it must be
   added at three sites in `backend/app/config.py`: the pydantic `Settings` `Field`
   (alias `OPEN_OTC_FEATURE_MODEL_WRITE_API`, ~line 95), the dataclass mirror (~line 245), and
   the `_coerce_bool` map (~line 344).

   **Security posture (conscious trade-off).** The backend today has no per-request auth and
   permissive CORS (consistent with every other route in `main.py`), and this surface can change
   `base_url` / `api_key_env` / the default model — i.e. it could reroute agent traffic to an
   arbitrary OpenAI-compatible endpoint. Defaulting the flag ON is therefore acceptable **only
   under the same trust boundary the rest of the desk already assumes**: a single-operator deploy
   bound to localhost / a trusted network, not a public bind. The spec documents this explicitly
   so the default-ON choice is deliberate; operators exposing the backend beyond localhost should
   set `OPEN_OTC_FEATURE_MODEL_WRITE_API=false` (writes 403, reads still work). Hardening the
   whole backend with real auth is a separate, app-wide effort out of scope here.

## Architecture

### Backend

**Writer module** `backend/app/services/deep_agent/channel_registry_writer.py`
(beside `channel_registry.py`). Pure, no FastAPI imports. Public functions, each returning the
mutated registry or raising `ValueError` (validation) / a typed conflict error (guards):

- `add_channel(spec)`, `update_channel(name, spec)`, `delete_channel(name)`
- `add_model(channel, spec)`, `update_model(channel, model_id, spec)`, `delete_model(channel, model_id)`
- `set_default(channel, model_id)`
- `validate_draft(mutation)` — dry-run: build the candidate + `load_from_path` without committing.

Internal flow shared by every mutating function, run **inside `channel_registry._LOCK`** as one
critical section (see concurrency note below):

1. `ruamel.yaml.YAML(typ="rt")` load of the live YAML path into a round-trip document.
2. Apply the single mutation to the round-trip structure (preserving comments/order).
3. Dump to a temp file (`mkstemp` in the same dir), run `load_from_path(temp)` to validate, and run
   the health-independent default-integrity check on the raw candidate.
4. On success: `os.replace(temp, live)` (atomic) **and swap `_REGISTRY` to the just-parsed registry
   under the same lock**, then return it. On failure: unlink temp, re-raise `ValueError`.

To make step 4's atomic file+registry swap possible without re-reading, the writer exposes a small
seam in `channel_registry.py` — a `commit_registry(new_registry)` helper (or reuse of the existing
lock) that sets `_REGISTRY` under `_LOCK`. The router calls `rebuild_default_model()` after the
writer returns, against the already-swapped registry.

**Guards** (enforced *before* the file flow, raising a typed error → 4xx, not a stack trace):

- Deleting the channel/model that currently holds the default → blocked ("reassign default first").
- **Renaming** a channel, or **changing a model's id**, that the default points at → blocked
  (would orphan the default). Covers `update_channel` (name change) and `update_model` (id change).
- Deleting the last channel, or a channel's last model → blocked.
- Creating a channel/model whose name/id already exists, or is empty → blocked.
- `set_default` target must exist.
- **Health-independent default integrity.** After applying any mutation, the writer asserts the
  *raw* default block (`{channel, model}`) resolves to a channel + model that exist **in the raw
  YAML regardless of channel health** — closing the loader gap where `_resolve_default` skips
  validating the default when its channel is unhealthy (missing `api_key_env` var). Without this,
  deleting/renaming the default model while its key is absent would commit a YAML that validates
  now but fails on a later reload once the key returns — a delayed outage. This raw-level check is
  the writer's own, *not* delegated to `load_from_path` (which is health-gated).

The remaining schema rules (zenmux enums, `anthropic_base_url` requirement, tag shape, unique ids)
come free from step 3's `load_from_path`, so the writer does not duplicate them.

**Router** `backend/app/routers/agent_channels.py` →
`build_agent_channels_router(agent_service, *, settings) -> APIRouter`, prefix `/api/agent`,
registered in `main.py` alongside the other `build_*` routers (import ~line 255, include ~line 4077).
Every write endpoint calls a `_require_write_api(settings)` guard (403 when the flag is off),
then the writer, then `agent_service.rebuild_default_model()` after a successful `reload()`,
and returns the fresh registry + reload status (`healthy_channels`). Endpoints:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/agent/registry` | Maintenance view: channels with all editable fields + health + default + models |
| `POST` | `/api/agent/channels` | Create channel |
| `PUT` | `/api/agent/channels/{name}` | Update channel |
| `DELETE` | `/api/agent/channels/{name}` | Delete channel |
| `POST` | `/api/agent/channels/{name}/models` | Add model (model id in body) |
| `PUT` | `/api/agent/channels/{name}/models/{model_id:path}` | Update model |
| `DELETE` | `/api/agent/channels/{name}/models/{model_id:path}` | Delete model |
| `PUT` | `/api/agent/registry/default` | Set default `{channel, model}` |
| `POST` | `/api/agent/channels/validate` | Dry-run validate a draft mutation |

**Slash-safe model addressing.** Model ids routinely contain slashes
(`anthropic/claude-sonnet-4.6`, `google/gemini-3.1-pro-preview`), so a plain `{model_id}` path
segment would never match — FastAPI stops at the first `/`. The update/delete model routes
therefore use the `{model_id:path}` converter (greedy, last-segment), and a route-order/matching
test covers a slash-containing id end-to-end. The frontend URL-encodes the id and relies on the
converter; alternatively the id may be carried in the body — the spec commits to `{model_id:path}`.

The existing `GET /api/agent/models` and `POST /api/agent/channels/reload` are unchanged (the
former still feeds the chat picker; the latter stays a manual reload button). `{name}` and
`{model_id}` locate rows in a fixed file (not filesystem paths), so there is no path-*traversal*
surface (unlike Skills) — but see the slash-matching note above for route correctness.

**Read schema** `AgentRegistryOut` (new, in `schemas.py`): channels with
`name, label, type, base_url, anthropic_base_url, api_key_env, healthy`, each model with
`id, provider, label, description, tags, protocol`, plus the resolved `default` `{channel, model}`.
A serializer `agent_registry_config(registry)` (beside `agent_model_config`) builds it — the
maintenance view needs the editable fields `agent_model_config` omits. Request bodies:
`ChannelWriteIn`, `ModelWriteIn`, `DefaultWriteIn` (pydantic).

### Frontend

Route files under `frontend/src/routes/`: `ModelMaintenance.tsx` (presentation) +
`ModelMaintenance.live.tsx` (data/container) + `ModelMaintenance.css`, following the repo's
`.tsx`/`.live.tsx` split. Token-only styling per `frontend/CLAUDE.md`.

- **Layout** — `SplitLayout` (rail + editor pane), like Skills.
  - **Left rail**: channels as sections, each with a health badge (green = `api_key_env` set,
    red = missing); the channel's models nested as rows, the default model carrying a "default"
    badge. `PageToolbar` on top with **New Channel**, **Reload**, filter box.
  - **Right pane**: contextual editor. Channel row selected → edit
    `label, type, base_url, anthropic_base_url, api_key_env` (`anthropic_base_url` shown/required
    only when `type = zenmux`), plus **Add Model** / **Delete Channel**. Model row selected →
    edit `id, provider, label, description, tags` (chip input), `protocol`, plus **Set as default**
    / **Delete Model**. Inline **debounced validation** via `POST /api/agent/channels/validate`.
  - **Feedback banner** reports save/reload results incl. healthy-channel count.
- **usePageContextReporter** so the floating agent knows the page (mirrors Skills).
- **API client** in `frontend/src/api/client.ts`: `getAgentRegistry`, `createChannel`,
  `updateChannel`, `deleteChannel`, `createModel`, `updateModel`, `deleteModel`,
  `setDefaultModel`, `validateChannelDraft`. Types in `frontend/src/types.ts`.
- **Nav wiring** (the repo's standard edit sites): `types.ts` `Route` union +
  `lib/routing.ts` `ROUTE_PATHS` + `main.tsx` (import, `navItems` "Model Maintenance",
  render-switch line, ⌘K command-palette entry). Mirror `routing.test.ts`.

## Failure handling

- **Invalid mutation** (schema violation caught by `load_from_path` on the temp file) → HTTP 422
  with the `ValueError` message; live YAML byte-unchanged; no reload.
- **Guard violation** (delete-default, delete-last, duplicate name) → HTTP 409 with a clear
  message; no file touched.
- **Write flag off** → HTTP 403 from `_require_write_api` before any work.
- **Reload failure after a committed write** (should be near-impossible since the same bytes just
  validated): surface reload error in the response; the file is already the new valid content, so
  a subsequent manual reload recovers. The writer commits only validated bytes, so the on-disk
  file is never corrupt.
- **Concurrent writes**: the writer holds `channel_registry._LOCK` (RLock) around the entire
  read-modify-`os.replace`-and-swap-`_REGISTRY` sequence so two simultaneous writes can't interleave
  a lost update.
- **Reload racing a write**: today `reload()` reads the YAML file *before* taking `_LOCK`, so a
  manual reload that begins before a write could parse the old file and then install that stale
  snapshot over the writer's fresh registry after the writer's lock releases. Fix: move `reload()`'s
  file read *inside* `_LOCK` (read-modify-swap atomically), matching the writer. A regression test
  covers manual-reload-versus-write ordering. This is a small, contained change to the existing
  `channel_registry.reload()`.

## Testing

- **Backend** (`backend/tests/`, pytest via `.venv/bin/python -m pytest`):
  - Writer round-trips: add/update/delete model, add/update/delete channel, set default — assert
    the YAML changed *and* `reload()` + `rebuild_default_model()` fired.
  - Validate-then-commit safety: an invalid mutation (zenmux model `provider: deepseek`; default →
    missing model) → 422 **and the live file is byte-unchanged**.
  - Guards: delete-the-default blocked (409); rename/reid that orphans the default blocked;
    delete-last-channel / last-model blocked; duplicate name/id blocked.
  - **Slash-safe addressing**: update/delete a model whose id contains a slash
    (`anthropic/claude-sonnet-4.6`) resolves the correct row via the `{model_id:path}` route.
  - **Health-independent default integrity**: with the default channel's `api_key_env` *unset*
    (channel unhealthy), deleting/renaming the default model → 409, and the on-disk YAML never
    ends up with a dangling default. (Guards against the `_resolve_default` health-gating gap.)
  - **Reload-versus-write race**: a manual `reload()` interleaved with a write cannot install a
    stale in-memory registry (read-under-lock regression test).
  - Feature-flag gate: writes → 403 when `OPEN_OTC_FEATURE_MODEL_WRITE_API` is off.
  - ruamel round-trip preserves an existing YAML comment across a write.
  - Router registration follows the `build_*` pattern.
- **Frontend** (`cd frontend && npm test`, vitest; `npx tsc --noEmit`):
  - `routing.test.ts` entry for the new route.
  - Component test: page renders channels/models from a mocked registry and calls the right client
    fn on save; `anthropic_base_url` field visibility toggles with `type`.

## Docs

Per CLAUDE.md pre-PR checklist: `CHANGELOG.md` under `[Unreleased]`; `README.md` (new user-facing
page); a new **CLAUDE.md** subsystem section ("Model maintenance UI") documenting the
validate-then-commit flow, the write flag, ruamel round-trip, and the arena-`CANDIDATE_MODELS`
non-sync gotcha; a memory entry once merged.

## Out of scope

- **Arena `CANDIDATE_MODELS` sync.** `backend/app/services/arena/models.py::CANDIDATE_MODELS` is a
  separate hardcoded list; this page edits the agent registry only. Adding a model here does **not**
  make it an arena contestant. Documented as a known limitation, not wired.
- **Writing secrets / editing `.env`.** Out by decision 4.
- **Editing the tracked `config/agent_channels.example.yml`.** Out by decision 5.
- **The stale `backend/config/agent_channels.yaml`.** Not the loaded file; left untouched.
- **Multi-user auth / per-user permissions.** The desk is single-identity today; the write flag is
  the only gate.

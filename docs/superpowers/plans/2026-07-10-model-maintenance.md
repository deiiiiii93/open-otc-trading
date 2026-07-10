# Model Maintenance UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a web page to add/edit/delete LLM channels and models, writing the live `config/agent_channels.yaml` and hot-reloading the registry — replacing hand-editing of the YAML.

**Architecture:** A pure writer module mutates the YAML via `ruamel.yaml` round-trip (comment-preserving) using a validate-then-commit flow that reuses the existing `channel_registry.load_from_path` for schema validation, committing the file and swapping the in-memory registry under one lock. A flag-gated FastAPI router exposes CRUD over the writer; a React page (`SplitLayout`, `.tsx`/`.live.tsx` split) drives it.

**Tech Stack:** FastAPI, pydantic, `ruamel.yaml`, SQLAlchemy (unaffected), React 19 / Vite / TypeScript, Radix UI, vitest.

## Global Constraints

- Backend tests: `.venv/bin/python -m pytest` (run from repo root). **This repo's `pyproject.toml` pins `testpaths = ["tests"]` and `pythonpath = ["backend"]`, so ALL new backend tests go under the repo-root `tests/` directory (imports use `from app...`); tests placed under `backend/tests/` are NOT discovered.** Frontend: `cd frontend && npm test` (vitest); type-check `cd frontend && npx tsc --noEmit`.
- The live YAML is the file resolved by `channel_registry._yaml_path()` (`AGENT_CHANNELS_FILE` env → `Settings.agent_channels_file` → `./config/agent_channels.yaml`). Never touch `backend/config/agent_channels.yaml` (stale) or `config/agent_channels.example.yml` (tracked template).
- Secrets: the UI edits `api_key_env` (the env-var NAME) only; never a secret value; never `.env`.
- Feature flag `OPEN_OTC_FEATURE_MODEL_WRITE_API` defaults **True**; gates every write endpoint (403 when off). Reads are always allowed.
  - **Accepted architectural risk (deliberate, per user decision).** Codex flagged (spec + plan gates) that a default-on, unauthenticated write surface can reroute agent traffic / reuse an env-var API key against an attacker `base_url`. We do **not** flip the default or add auth here because: (a) the user explicitly chose default-on; (b) this is an **app-wide** posture — every existing route (incl. `POST /api/agent/channels/reload` and the skills write API) is equally unauthenticated with permissive CORS, so single-feature auth would be inconsistent and out of scope; (c) the mitigation is operational — deploy bound to localhost/trusted network, and set `OPEN_OTC_FEATURE_MODEL_WRITE_API=false` on any non-localhost bind (writes 403, reads still work). Real backend auth is a separate, app-wide effort. The write flag + the localhost deployment boundary are the controls.
- Frontend styling is **token-only** per `frontend/CLAUDE.md` — no hardcoded colors/hex; read that file before UI work.
- Model ids contain slashes (`anthropic/claude-sonnet-4.6`) — model routes MUST use the `{model_id:path}` converter.
- Writer + reload mutate `_REGISTRY` under `channel_registry._LOCK`; `reload()`'s file read must move inside the lock.
- Update `CHANGELOG.md` `[Unreleased]`, `README.md`, and `CLAUDE.md` before finishing.

## File Structure

- Create `backend/app/services/deep_agent/channel_registry_writer.py` — pure writer (mutations + guards + validate-then-commit). No FastAPI imports.
- Modify `backend/app/services/deep_agent/channel_registry.py` — `reload()` reads under lock; add `commit_registry()` seam.
- Modify `backend/app/services/deep_agent/model_factory.py` — add `agent_registry_config()` serializer (editable fields).
- Modify `backend/app/schemas.py` — `AgentRegistryOut`, `AgentRegistryChannelOut`, `AgentRegistryModelOut`, `ChannelWriteIn`, `ModelWriteIn`, `DefaultWriteIn`.
- Create `backend/app/routers/agent_channels.py` — `build_agent_channels_router(agent_service, *, settings=None)`.
- Modify `backend/app/main.py` — import + include the router.
- Modify `backend/app/config.py` — add `feature_model_write_api` at 3 sites.
- Modify `pyproject.toml` — add `ruamel.yaml` dep.
- Create `tests/test_channel_registry_writer.py`, `tests/test_channel_registry_lock.py`, `tests/test_agent_registry_config.py`, `tests/test_agent_channels_router.py`. (Modify `tests/test_config.py`.)
- Create `frontend/src/routes/ModelMaintenance.tsx`, `ModelMaintenance.live.tsx`, `ModelMaintenance.css`.
- Modify `frontend/src/api/client.ts`, `frontend/src/types.ts`, `frontend/src/lib/routing.ts`, `frontend/src/main.tsx`, `frontend/src/lib/routing.test.ts`.
- Create `frontend/src/routes/ModelMaintenance.test.tsx`.

---

### Task 1: Add `ruamel.yaml` dependency + `feature_model_write_api` flag

**Files:**
- Modify: `pyproject.toml:10-40` (dependencies array)
- Modify: `backend/app/config.py:95-98` (Settings Field), `:245-247` (dataclass field), `:342-346` (coerce)
- Test: `tests/test_config.py` (add one assertion)

**Interfaces:**
- Produces: `get_settings().feature_model_write_api: bool` (default True); env `OPEN_OTC_FEATURE_MODEL_WRITE_API`. `ruamel.yaml` importable.

- [ ] **Step 1: Write the failing test** — append to `tests/test_config.py`:

```python
def test_feature_model_write_api_defaults_true():
    from app.config import Settings
    assert Settings().feature_model_write_api is True

def test_ruamel_yaml_importable():
    import ruamel.yaml  # noqa: F401
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py::test_feature_model_write_api_defaults_true tests/test_config.py::test_ruamel_yaml_importable -v`
Expected: FAIL (`AttributeError: feature_model_write_api` / `ModuleNotFoundError: ruamel`).

- [ ] **Step 3: Add the dependency** — in `pyproject.toml` dependencies array, after `"pyyaml>=6.0",` add:

```toml
  "ruamel.yaml>=0.18.0",
```

Then sync: `uv sync` (from repo root). Expected: resolves and installs `ruamel.yaml`.

- [ ] **Step 4: Add the flag at all three config sites** — `backend/app/config.py`.

After the `feature_skills_write_api` Field (line ~98):

```python
    feature_model_write_api: bool = Field(
        True,
        validation_alias="OPEN_OTC_FEATURE_MODEL_WRITE_API",
    )
```

After the `feature_skills_write_api` dataclass field (line ~247):

```python
    feature_model_write_api: bool = field(
        default_factory=lambda: _env_value("feature_model_write_api")
    )
```

After the `feature_skills_write_api` coerce block (line ~346):

```python
        object.__setattr__(
            self,
            "feature_model_write_api",
            _coerce_bool(self.feature_model_write_api),
        )
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS (new tests + existing config tests).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock backend/app/config.py tests/test_config.py
git commit -m "feat(models): add ruamel.yaml dep + OPEN_OTC_FEATURE_MODEL_WRITE_API flag"
```

---

### Task 2: `reload()` read-under-lock + `commit_registry()` seam

**Files:**
- Modify: `backend/app/services/deep_agent/channel_registry.py:310-320` (`reload`), add `commit_registry` after it
- Test: `tests/test_channel_registry_lock.py` (create)

**Interfaces:**
- Produces: `channel_registry.commit_registry(new: ChannelRegistry) -> None` (sets `_REGISTRY` under `_LOCK`). `reload()` now reads the YAML file inside `_LOCK`.

- [ ] **Step 1: Write the failing test** — create `tests/test_channel_registry_lock.py`:

```python
from app.services.deep_agent import channel_registry as cr


def test_commit_registry_swaps_under_lock():
    reg = cr.load_from_path(cr._yaml_path())
    cr.configure_registry(None)  # reset cache
    cr.commit_registry(reg)
    assert cr.get_registry() is reg
    cr.configure_registry(None)


def test_reload_reads_file_under_lock():
    # reload holds _LOCK across the file read: a thread that grabs _LOCK first
    # blocks reload from returning a stale snapshot.
    import threading
    started = threading.Event()
    release = threading.Event()

    def hold_lock():
        with cr._LOCK:
            started.set()
            release.wait(timeout=5)

    t = threading.Thread(target=hold_lock)
    t.start()
    started.wait(timeout=5)
    done = threading.Event()

    def do_reload():
        cr.reload()
        done.set()

    r = threading.Thread(target=do_reload)
    r.start()
    # reload must NOT complete while the lock is held elsewhere
    assert not done.wait(timeout=0.5)
    release.set()
    t.join()
    r.join()
    assert done.is_set()
    cr.configure_registry(None)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_channel_registry_lock.py -v`
Expected: FAIL (`AttributeError: commit_registry`; reload completes immediately → `test_reload_reads_file_under_lock` fails).

- [ ] **Step 3: Implement** — replace `reload` in `channel_registry.py` and add `commit_registry`:

```python
def reload(*, force_reread_dotenv: bool = True) -> ChannelRegistry:
    """Re-read YAML and env, atomically swap the registry, return the new one.

    The file read happens INSIDE ``_LOCK`` so a reload cannot install a stale
    snapshot over a concurrent writer's fresh registry. If parsing/validation
    fails, the old registry remains live and the error propagates.
    """
    with _LOCK:
        new_registry = load_from_path(_yaml_path(), force_reread_dotenv=force_reread_dotenv)
        global _REGISTRY
        _REGISTRY = new_registry
        return new_registry


def commit_registry(new_registry: ChannelRegistry) -> None:
    """Swap the live registry under ``_LOCK`` (used by the writer after an
    atomic file replace, so the file and in-memory registry move together)."""
    with _LOCK:
        global _REGISTRY
        _REGISTRY = new_registry
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_channel_registry_lock.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/channel_registry.py tests/test_channel_registry_lock.py
git commit -m "feat(models): reload reads under _LOCK + add commit_registry seam"
```

---

### Task 3: Writer core — model CRUD, validate-then-commit, comment preservation

**Files:**
- Create: `backend/app/services/deep_agent/channel_registry_writer.py`
- Test: `tests/test_channel_registry_writer.py`

**Interfaces:**
- Consumes: `channel_registry.load_from_path`, `.commit_registry`, `._LOCK`, `._yaml_path`.
- Produces:
  - `class RegistryWriteError(Exception)` (base), `class RegistryConflictError(RegistryWriteError)` (→ 409), `class RegistryValidationError(RegistryWriteError)` (→ 422).
  - `add_model(channel: str, spec: dict, *, path: Path | None = None) -> ChannelRegistry`
  - `update_model(channel: str, model_id: str, spec: dict, *, path=None) -> ChannelRegistry`
  - `delete_model(channel: str, model_id: str, *, path=None) -> ChannelRegistry`
  - `validate_draft(mutate: Callable[[CommentedMap], None], *, path=None) -> ChannelRegistry` (internal core; dry-run wrapper in Task 4)
  - `spec` keys for a model: `id, provider, label, description(optional), tags(optional list), protocol(optional)`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_channel_registry_writer.py`:

```python
import shutil
from pathlib import Path

import pytest

from app.services.deep_agent import channel_registry as cr
from app.services.deep_agent import channel_registry_writer as w


@pytest.fixture
def yaml_path(tmp_path: Path) -> Path:
    src = cr._yaml_path()
    dst = tmp_path / "agent_channels.yaml"
    shutil.copy(src, dst)
    # ensure a comment exists to assert preservation
    text = dst.read_text()
    if "# maintained by" not in text:
        dst.write_text("# maintained by test\n" + text)
    return dst


def test_add_model_roundtrip_and_preserves_comment(yaml_path):
    w.add_model(
        "zenmux",
        {"id": "openai/gpt-6.0", "provider": "openai", "label": "GPT-6.0", "tags": ["tool-use"]},
        path=yaml_path,
    )
    text = yaml_path.read_text()
    assert "openai/gpt-6.0" in text
    assert "# maintained by test" in text  # ruamel round-trip kept the comment
    reg = cr.load_from_path(yaml_path)
    ch, md = reg.find_model("zenmux", "openai", "openai/gpt-6.0")
    assert md.label == "GPT-6.0"


def test_update_model_with_slash_id(yaml_path):
    w.update_model(
        "zenmux", "anthropic/claude-sonnet-4.6",
        {"id": "anthropic/claude-sonnet-4.6", "provider": "anthropic", "label": "Renamed Sonnet"},
        path=yaml_path,
    )
    reg = cr.load_from_path(yaml_path)
    _, md = reg.find_model("zenmux", "anthropic", "anthropic/claude-sonnet-4.6")
    assert md.label == "Renamed Sonnet"


def test_delete_model(yaml_path):
    w.delete_model("zenmux", "anthropic/claude-haiku-4.5", path=yaml_path)
    reg = cr.load_from_path(yaml_path)
    with pytest.raises(KeyError):
        reg.find_model("zenmux", "anthropic", "anthropic/claude-haiku-4.5")


def test_invalid_mutation_leaves_file_untouched(yaml_path):
    before = yaml_path.read_bytes()
    with pytest.raises(w.RegistryValidationError):
        # zenmux channel requires provider in {anthropic, openai}
        w.add_model("zenmux", {"id": "x/y", "provider": "deepseek", "label": "Bad"}, path=yaml_path)
    assert yaml_path.read_bytes() == before


def test_add_duplicate_model_id_conflicts(yaml_path):
    with pytest.raises(w.RegistryConflictError):
        w.add_model(
            "zenmux",
            {"id": "anthropic/claude-haiku-4.5", "provider": "anthropic", "label": "dup"},
            path=yaml_path,
        )


def test_concurrent_distinct_writes_both_survive(yaml_path):
    # Lost-update guard: two concurrent writers adding DISTINCT models must both
    # persist. _mutate serializes the full load-modify-write under cr._LOCK, so
    # neither snapshot clobbers the other.
    import threading

    errors: list[Exception] = []

    def add(i: int):
        try:
            w.add_model(
                "zenmux",
                {"id": f"openai/gpt-conc-{i}", "provider": "openai", "label": f"C{i}"},
                path=yaml_path,
            )
        except Exception as exc:  # pragma: no cover - surfaced via assert
            errors.append(exc)

    threads = [threading.Thread(target=add, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    text = yaml_path.read_text()
    assert "openai/gpt-conc-0" in text
    assert "openai/gpt-conc-1" in text  # neither write was lost
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_channel_registry_writer.py -v`
Expected: FAIL (`ModuleNotFoundError: channel_registry_writer`).

- [ ] **Step 3: Implement the writer core** — create `channel_registry_writer.py`:

```python
"""Comment-preserving writer for config/agent_channels.yaml.

Every mutation runs the FULL read-modify-write under channel_registry._LOCK
(via ``_mutate``): load the YAML round-trip (ruamel), apply one change + guards,
validate the candidate via the existing channel_registry.load_from_path, then
atomically replace the file AND swap the in-memory registry — all inside one
critical section. Holding the lock across the *load* (not just the commit) is
what prevents a lost update: two concurrent writes cannot both read the same
snapshot and clobber each other. Corrupt saves are structurally impossible: bad
candidates never reach os.replace.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Callable

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from . import channel_registry as cr
from .channel_registry import ChannelRegistry


class RegistryWriteError(Exception):
    """Base for writer failures."""


class RegistryConflictError(RegistryWriteError):
    """Guard violation (duplicate, orphaned default, last-item) -> HTTP 409."""


class RegistryValidationError(RegistryWriteError):
    """Candidate YAML fails schema validation -> HTTP 422."""


_yaml = YAML(typ="rt")
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)


def _resolve_path(path: Path | None) -> Path:
    return Path(path) if path is not None else Path(cr._yaml_path())


def _load_doc(path: Path) -> CommentedMap:
    with path.open("r") as fh:
        doc = _yaml.load(fh)
    if not isinstance(doc, dict):
        raise RegistryValidationError(f"YAML root must be a mapping in {path}")
    return doc


def _find_channel(doc: CommentedMap, name: str) -> CommentedMap:
    for ch in doc.get("channels") or []:
        if ch.get("name") == name:
            return ch
    raise RegistryConflictError(f"channel {name!r} not found")


def _model_index(channel: CommentedMap, model_id: str) -> int:
    for i, m in enumerate(channel.get("models") or []):
        if m.get("id") == model_id:
            return i
    return -1


def _default_pointer(doc: CommentedMap) -> tuple[str | None, str | None]:
    d = doc.get("default")
    if isinstance(d, dict):
        return d.get("channel"), d.get("model")
    return None, None


def _assert_default_integrity(doc: CommentedMap) -> None:
    """Health-INDEPENDENT check: the raw default must point at a channel+model
    that exist in the raw YAML. Closes the loader gap where _resolve_default
    skips validating the default when its channel is unhealthy."""
    ch_name, model_id = _default_pointer(doc)
    if ch_name is None and model_id is None:
        return  # no explicit default; loader picks first healthy
    for ch in doc.get("channels") or []:
        if ch.get("name") == ch_name:
            if any(m.get("id") == model_id for m in ch.get("models") or []):
                return
            raise RegistryConflictError(
                f"default model {model_id!r} would be orphaned on channel {ch_name!r}"
            )
    raise RegistryConflictError(f"default channel {ch_name!r} does not exist")


def _commit(doc: CommentedMap, path: Path) -> ChannelRegistry:
    """Validate-then-commit. MUST be called with cr._LOCK held (see _mutate);
    the inner `with cr._LOCK` is re-entrant (RLock) so this is also safe if a
    caller forgets, but the load in _mutate MUST share the same lock hold to
    prevent lost updates. Raises before any os.replace on failure."""
    _assert_default_integrity(doc)
    with cr._LOCK:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".yaml.tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                _yaml.dump(doc, fh)
            try:
                new_registry = cr.load_from_path(tmp, force_reread_dotenv=False)
            except ValueError as exc:
                raise RegistryValidationError(str(exc)) from exc
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        cr.commit_registry(new_registry)
        return new_registry


def _mutate(path: Path | None, apply_fn: Callable[[CommentedMap], None]) -> ChannelRegistry:
    """Serialize the ENTIRE read-modify-write under cr._LOCK.

    Loading the live YAML *inside* the lock (not just committing inside it) is
    the fix for the lost-update race: without it, two concurrent writers both
    read the same old snapshot, each validate, and the second os.replace
    silently clobbers the first. apply_fn runs the guards + mutation on the
    freshly-loaded doc and may raise RegistryConflictError.
    """
    p = _resolve_path(path)
    with cr._LOCK:
        doc = _load_doc(p)
        apply_fn(doc)
        return _commit(doc, p)


def _model_map(spec: dict) -> CommentedMap:
    m = CommentedMap()
    m["id"] = spec["id"]
    m["provider"] = spec["provider"]
    m["label"] = spec["label"]
    if spec.get("description"):
        m["description"] = spec["description"]
    if spec.get("tags"):
        seq = CommentedSeq()
        seq.extend(spec["tags"])
        m["tags"] = seq
    if spec.get("protocol"):
        m["protocol"] = spec["protocol"]
    return m


def add_model(channel: str, spec: dict, *, path: Path | None = None) -> ChannelRegistry:
    def apply(doc: CommentedMap) -> None:
        ch = _find_channel(doc, channel)
        if not spec.get("id"):
            raise RegistryConflictError("model id is required")
        if _model_index(ch, spec["id"]) >= 0:
            raise RegistryConflictError(f"model {spec['id']!r} already exists on {channel!r}")
        ch.setdefault("models", CommentedSeq()).append(_model_map(spec))
    return _mutate(path, apply)


def update_model(channel: str, model_id: str, spec: dict, *, path: Path | None = None) -> ChannelRegistry:
    def apply(doc: CommentedMap) -> None:
        ch = _find_channel(doc, channel)
        idx = _model_index(ch, model_id)
        if idx < 0:
            raise RegistryConflictError(f"model {model_id!r} not found on {channel!r}")
        new_id = spec.get("id", model_id)
        if new_id != model_id:
            d_ch, d_model = _default_pointer(doc)
            if d_ch == channel and d_model == model_id:
                raise RegistryConflictError("cannot rename the default model id; reassign default first")
            if _model_index(ch, new_id) >= 0:
                raise RegistryConflictError(f"model {new_id!r} already exists on {channel!r}")
        ch["models"][idx] = _model_map(spec)
    return _mutate(path, apply)


def delete_model(channel: str, model_id: str, *, path: Path | None = None) -> ChannelRegistry:
    def apply(doc: CommentedMap) -> None:
        ch = _find_channel(doc, channel)
        idx = _model_index(ch, model_id)
        if idx < 0:
            raise RegistryConflictError(f"model {model_id!r} not found on {channel!r}")
        d_ch, d_model = _default_pointer(doc)
        if d_ch == channel and d_model == model_id:
            raise RegistryConflictError("cannot delete the default model; reassign default first")
        if len(ch.get("models") or []) <= 1:
            raise RegistryConflictError(f"channel {channel!r} must keep at least one model")
        del ch["models"][idx]
    return _mutate(path, apply)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_channel_registry_writer.py -v`
Expected: PASS (6 tests, incl. the concurrent-distinct-writes lost-update guard). Note the `_commit` health-independent check + validation reuse.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/channel_registry_writer.py tests/test_channel_registry_writer.py
git commit -m "feat(models): comment-preserving model writer with validate-then-commit"
```

---

### Task 4: Writer — channels, set_default, remaining guards, `validate_draft`

**Files:**
- Modify: `backend/app/services/deep_agent/channel_registry_writer.py`
- Test: `tests/test_channel_registry_writer.py` (append)

**Interfaces:**
- Consumes: Task 3 helpers (`_load_doc`, `_find_channel`, `_commit`, `_default_pointer`, error classes).
- Produces:
  - `add_channel(spec: dict, *, path=None) -> ChannelRegistry`
  - `update_channel(name: str, spec: dict, *, path=None) -> ChannelRegistry`
  - `delete_channel(name: str, *, path=None) -> ChannelRegistry`
  - `set_default(channel: str, model_id: str, *, path=None) -> ChannelRegistry`
  - `validate_draft(kind: str, payload: dict, *, path=None) -> None` (raises on invalid; returns None on ok)
  - channel `spec` keys: `name, label, type, base_url, anthropic_base_url(optional), api_key_env(optional), models(optional list for add)`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_channel_registry_writer.py`:

```python
def test_add_and_delete_channel(yaml_path):
    w.add_channel(
        {
            "name": "local",
            "label": "Local",
            "type": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "models": [{"id": "llama-3", "provider": "meta", "label": "Llama 3"}],
        },
        path=yaml_path,
    )
    reg = cr.load_from_path(yaml_path)
    assert any(c.name == "local" for c in reg.channels)
    w.delete_channel("local", path=yaml_path)
    reg2 = cr.load_from_path(yaml_path)
    assert not any(c.name == "local" for c in reg2.channels)


def test_delete_channel_holding_default_blocked(yaml_path):
    with pytest.raises(w.RegistryConflictError):
        w.delete_channel("zenmux", path=yaml_path)  # holds the default


def test_set_default(yaml_path):
    w.set_default("deepseek", "deepseek-v4-flash", path=yaml_path)
    reg = cr.load_from_path(yaml_path)
    assert reg.default_selection()["channel"] == "deepseek"


def test_set_default_missing_target_conflicts(yaml_path):
    with pytest.raises(w.RegistryConflictError):
        w.set_default("zenmux", "nope/nope", path=yaml_path)


def test_delete_default_model_blocked_even_when_channel_unhealthy(yaml_path, monkeypatch):
    # zenmux becomes unhealthy (env var unset) but deleting its default model
    # must STILL be blocked by the health-independent guard.
    monkeypatch.delenv("ZENMUX_API_KEY", raising=False)
    default_model = cr.load_from_path(yaml_path).default_selection()["model"]
    with pytest.raises(w.RegistryConflictError):
        w.delete_model("zenmux", default_model, path=yaml_path)


def test_zenmux_channel_requires_anthropic_base_url(yaml_path):
    with pytest.raises(w.RegistryValidationError):
        w.add_channel(
            {
                "name": "z2",
                "label": "Z2",
                "type": "zenmux",
                "base_url": "https://x/v1",
                "models": [{"id": "anthropic/x", "provider": "anthropic", "label": "X"}],
            },
            path=yaml_path,
        )
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_channel_registry_writer.py -k "channel or default" -v`
Expected: FAIL (`AttributeError: add_channel` etc.).

- [ ] **Step 3: Implement** — append to `channel_registry_writer.py`:

```python
def _channel_map(spec: dict) -> CommentedMap:
    c = CommentedMap()
    c["name"] = spec["name"]
    c["label"] = spec["label"]
    c["type"] = spec["type"]
    if spec.get("api_key_env"):
        c["api_key_env"] = spec["api_key_env"]
    c["base_url"] = spec["base_url"]
    if spec.get("anthropic_base_url"):
        c["anthropic_base_url"] = spec["anthropic_base_url"]
    models = CommentedSeq()
    for m in spec.get("models") or []:
        models.append(_model_map(m))
    c["models"] = models
    return c


def _channel_index(doc: CommentedMap, name: str) -> int:
    for i, ch in enumerate(doc.get("channels") or []):
        if ch.get("name") == name:
            return i
    return -1


def add_channel(spec: dict, *, path: Path | None = None) -> ChannelRegistry:
    def apply(doc: CommentedMap) -> None:
        if not spec.get("name"):
            raise RegistryConflictError("channel name is required")
        if _channel_index(doc, spec["name"]) >= 0:
            raise RegistryConflictError(f"channel {spec['name']!r} already exists")
        doc.setdefault("channels", CommentedSeq()).append(_channel_map(spec))
    return _mutate(path, apply)


def update_channel(name: str, spec: dict, *, path: Path | None = None) -> ChannelRegistry:
    def apply(doc: CommentedMap) -> None:
        idx = _channel_index(doc, name)
        if idx < 0:
            raise RegistryConflictError(f"channel {name!r} not found")
        new_name = spec.get("name", name)
        d_ch, _ = _default_pointer(doc)
        if new_name != name and d_ch == name:
            raise RegistryConflictError("cannot rename the default channel; reassign default first")
        if new_name != name and _channel_index(doc, new_name) >= 0:
            raise RegistryConflictError(f"channel {new_name!r} already exists")
        existing_models = doc["channels"][idx].get("models")
        merged = dict(spec)
        merged.setdefault("models", existing_models)  # preserve models on metadata edit
        updated = _channel_map(merged)
        if isinstance(existing_models, CommentedSeq):
            updated["models"] = existing_models
        doc["channels"][idx] = updated
    return _mutate(path, apply)


def delete_channel(name: str, *, path: Path | None = None) -> ChannelRegistry:
    def apply(doc: CommentedMap) -> None:
        idx = _channel_index(doc, name)
        if idx < 0:
            raise RegistryConflictError(f"channel {name!r} not found")
        d_ch, _ = _default_pointer(doc)
        if d_ch == name:
            raise RegistryConflictError("cannot delete the default channel; reassign default first")
        if len(doc.get("channels") or []) <= 1:
            raise RegistryConflictError("registry must keep at least one channel")
        del doc["channels"][idx]
    return _mutate(path, apply)


def set_default(channel: str, model_id: str, *, path: Path | None = None) -> ChannelRegistry:
    def apply(doc: CommentedMap) -> None:
        ch = _find_channel(doc, channel)
        if _model_index(ch, model_id) < 0:
            raise RegistryConflictError(f"model {model_id!r} not found on {channel!r}")
        d = CommentedMap()
        d["channel"] = channel
        d["model"] = model_id
        doc["default"] = d
    return _mutate(path, apply)


_DRAFT_DISPATCH: dict[str, Callable[[dict, Path], ChannelRegistry]] = {}


def validate_draft(kind: str, payload: dict, *, path: Path | None = None) -> None:
    """Dry-run: apply the mutation against an IN-MEMORY copy and validate,
    never writing. Raises RegistryValidationError / RegistryConflictError on bad
    input; returns None when the draft would be accepted."""
    import io

    p = _resolve_path(path)
    doc = _load_doc(p)
    buf = io.StringIO()
    _yaml.dump(doc, buf)
    # Re-parse into an isolated doc so we never mutate the caller's file.
    scratch = _yaml.load(io.StringIO(buf.getvalue()))
    _apply_draft(kind, payload, scratch)
    _assert_default_integrity(scratch)
    tmp_text = io.StringIO()
    _yaml.dump(scratch, tmp_text)
    import tempfile as _tf
    with _tf.NamedTemporaryFile("w", suffix=".yaml", delete=False, dir=str(p.parent)) as fh:
        fh.write(tmp_text.getvalue())
        tmp_name = fh.name
    try:
        cr.load_from_path(tmp_name, force_reread_dotenv=False)
    except ValueError as exc:
        raise RegistryValidationError(str(exc)) from exc
    finally:
        os.unlink(tmp_name)


def _apply_draft(kind: str, payload: dict, doc: CommentedMap) -> None:
    if kind == "add_channel":
        doc.setdefault("channels", CommentedSeq()).append(_channel_map(payload))
    elif kind == "add_model":
        _find_channel(doc, payload["channel"]).setdefault("models", CommentedSeq()).append(
            _model_map(payload["model"])
        )
    else:
        raise RegistryConflictError(f"unknown draft kind {kind!r}")
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_channel_registry_writer.py -v`
Expected: PASS (all tests, including the health-independent default guard).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/channel_registry_writer.py tests/test_channel_registry_writer.py
git commit -m "feat(models): channel CRUD, set_default, guards, dry-run validate_draft"
```

---

### Task 5: Read serializer + pydantic schemas

**Files:**
- Modify: `backend/app/services/deep_agent/model_factory.py` (add `agent_registry_config`)
- Modify: `backend/app/schemas.py` (add maintenance schemas)
- Test: `tests/test_agent_registry_config.py` (create)

**Interfaces:**
- Produces:
  - `model_factory.agent_registry_config(registry) -> dict` with `{default: {channel, model}, channels: [{name, label, type, base_url, anthropic_base_url, api_key_env, healthy, models: [{id, provider, label, description, tags, protocol}]}]}`.
  - Schemas: `AgentRegistryModelOut`, `AgentRegistryChannelOut`, `AgentRegistryOut`, `ChannelWriteIn`, `ModelWriteIn`, `DefaultWriteIn`.
- Note: the registry dataclass does NOT carry `api_key_env` (only the derived `api_key`/`healthy`). The serializer reads `api_key_env` from the **raw YAML** (parse once via `channel_registry._yaml_path()`), keyed by channel name, so the editable name round-trips to the UI.

- [ ] **Step 1: Write the failing test** — create `tests/test_agent_registry_config.py`:

```python
from app.services.deep_agent import channel_registry as cr
from app.services.deep_agent.model_factory import agent_registry_config


def test_agent_registry_config_exposes_editable_fields():
    reg = cr.load_from_path(cr._yaml_path())
    cfg = agent_registry_config(reg)
    assert "default" in cfg and "channel" in cfg["default"]
    zen = next(c for c in cfg["channels"] if c["name"] == "zenmux")
    assert zen["base_url"]
    assert zen["anthropic_base_url"]
    assert zen["api_key_env"] == "ZENMUX_API_KEY"
    assert "healthy" in zen
    m = zen["models"][0]
    assert {"id", "provider", "label", "tags", "protocol"} <= set(m)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent_registry_config.py -v`
Expected: FAIL (`ImportError: agent_registry_config`).

- [ ] **Step 3: Implement the serializer** — add to `model_factory.py` (imports `yaml`, `channel_registry`):

```python
def agent_registry_config(registry: ChannelRegistry) -> dict[str, object]:
    """Maintenance view: full editable fields incl. api_key_env (read from raw
    YAML, since the dataclass keeps only the derived api_key/healthy)."""
    import yaml as _yaml
    from . import channel_registry as _cr

    raw = _yaml.safe_load(Path(_cr._yaml_path()).read_text()) or {}
    api_key_env_by_channel: dict[str, str | None] = {}
    for entry in raw.get("channels") or []:
        if isinstance(entry, dict) and entry.get("name"):
            api_key_env_by_channel[entry["name"]] = entry.get("api_key_env")

    ch_name, _prov, model_id = registry.default
    channels_payload: list[dict[str, object]] = []
    for ch in registry.channels:
        channels_payload.append({
            "name": ch.name,
            "label": ch.label,
            "type": ch.type,
            "base_url": ch.base_url,
            "anthropic_base_url": ch.anthropic_base_url,
            "api_key_env": api_key_env_by_channel.get(ch.name),
            "healthy": ch.healthy,
            "models": [
                {
                    "id": md.id,
                    "provider": md.provider,
                    "label": md.label,
                    "description": md.description,
                    "tags": list(md.tags),
                    "protocol": md.protocol or None,
                }
                for md in ch.models
            ],
        })
    return {
        "default": {"channel": ch_name, "model": model_id},
        "channels": channels_payload,
    }
```

Ensure `from pathlib import Path` is imported at the top of `model_factory.py` (add if absent).

- [ ] **Step 4: Add the pydantic schemas** — append to `backend/app/schemas.py`:

```python
class AgentRegistryModelOut(BaseModel):
    id: str
    provider: str
    label: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    protocol: str | None = None


class AgentRegistryChannelOut(BaseModel):
    name: str
    label: str
    type: Literal["zenmux", "openai_compatible"]
    base_url: str
    anthropic_base_url: str | None = None
    api_key_env: str | None = None
    healthy: bool = True
    models: list[AgentRegistryModelOut] = Field(default_factory=list)


class AgentRegistryDefaultOut(BaseModel):
    channel: str
    model: str


class AgentRegistryOut(BaseModel):
    default: AgentRegistryDefaultOut
    channels: list[AgentRegistryChannelOut] = Field(default_factory=list)


class ModelWriteIn(BaseModel):
    id: str
    provider: str
    label: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    protocol: str | None = None


class ChannelWriteIn(BaseModel):
    name: str
    label: str
    type: Literal["zenmux", "openai_compatible"]
    base_url: str
    anthropic_base_url: str | None = None
    api_key_env: str | None = None
    models: list[ModelWriteIn] = Field(default_factory=list)


class DefaultWriteIn(BaseModel):
    channel: str
    model: str
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agent_registry_config.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/deep_agent/model_factory.py backend/app/schemas.py tests/test_agent_registry_config.py
git commit -m "feat(models): agent_registry_config serializer + maintenance schemas"
```

---

### Task 6: Router `agent_channels.py` + registration + feature gate

**Files:**
- Create: `backend/app/routers/agent_channels.py`
- Modify: `backend/app/main.py` (import ~line 255-261; include ~line 4077-4094)
- Test: `tests/test_agent_channels_router.py` (create)

**Interfaces:**
- Consumes: `channel_registry_writer` (Tasks 3-4), `model_factory.agent_registry_config`, `channel_registry` (reads), the maintenance schemas (Task 5).
- Produces: `build_agent_channels_router(agent_service, *, settings=None) -> APIRouter` with prefix `/api/agent`, endpoints per the spec table. `agent_service` needs `.rebuild_default_model()`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_agent_channels_router.py`:

```python
import shutil
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, configure_settings
from app.routers.agent_channels import build_agent_channels_router
from app.services.deep_agent import channel_registry as cr


class _FakeAgent:
    def __init__(self):
        self.rebuilt = 0

    def rebuild_default_model(self):
        self.rebuilt += 1


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    dst = tmp_path / "agent_channels.yaml"
    shutil.copy(cr._yaml_path(), dst)
    monkeypatch.setenv("AGENT_CHANNELS_FILE", str(dst))
    cr.configure_registry(None)
    agent = _FakeAgent()
    app = FastAPI()
    app.include_router(build_agent_channels_router(agent, settings=Settings()))
    yield TestClient(app), agent, dst
    cr.configure_registry(None)


def test_get_registry_lists_editable_fields(client):
    c, _agent, _ = client
    r = c.get("/api/agent/registry")
    assert r.status_code == 200
    body = r.json()
    zen = next(ch for ch in body["channels"] if ch["name"] == "zenmux")
    assert zen["api_key_env"] == "ZENMUX_API_KEY"


def test_add_model_then_rebuilds(client):
    c, agent, _ = client
    r = c.post(
        "/api/agent/channels/zenmux/models",
        json={"id": "openai/gpt-6.0", "provider": "openai", "label": "GPT-6.0", "tags": ["tool-use"]},
    )
    assert r.status_code == 200, r.text
    assert agent.rebuilt >= 1


def test_update_model_with_slash_id_route(client):
    c, _agent, _ = client
    r = c.put(
        "/api/agent/channels/zenmux/models/anthropic/claude-sonnet-4.6",
        json={"id": "anthropic/claude-sonnet-4.6", "provider": "anthropic", "label": "Renamed"},
    )
    assert r.status_code == 200, r.text


def test_invalid_model_returns_422(client):
    c, _agent, _ = client
    r = c.post(
        "/api/agent/channels/zenmux/models",
        json={"id": "x/y", "provider": "deepseek", "label": "bad"},
    )
    assert r.status_code == 422


def test_delete_default_channel_returns_409(client):
    c, _agent, _ = client
    r = c.delete("/api/agent/channels/zenmux")
    assert r.status_code == 409


def test_write_gate_403_when_flag_off(tmp_path, monkeypatch):
    dst = tmp_path / "agent_channels.yaml"
    shutil.copy(cr._yaml_path(), dst)
    monkeypatch.setenv("AGENT_CHANNELS_FILE", str(dst))
    cr.configure_registry(None)
    app = FastAPI()
    settings = Settings(OPEN_OTC_FEATURE_MODEL_WRITE_API=False)
    app.include_router(build_agent_channels_router(_FakeAgent(), settings=settings))
    c = TestClient(app)
    r = c.post("/api/agent/channels/zenmux/models",
               json={"id": "a/b", "provider": "openai", "label": "x"})
    assert r.status_code == 403
    cr.configure_registry(None)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent_channels_router.py -v`
Expected: FAIL (`ModuleNotFoundError: app.routers.agent_channels`).

- [ ] **Step 3: Implement the router** — create `backend/app/routers/agent_channels.py`:

```python
"""Flag-gated CRUD over the agent channel/model registry (config/agent_channels.yaml)."""
from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter, HTTPException

from ..config import Settings, get_settings
from ..schemas import (
    AgentRegistryOut,
    ChannelWriteIn,
    DefaultWriteIn,
    ModelWriteIn,
)
from ..services.deep_agent import channel_registry as cr
from ..services.deep_agent import channel_registry_writer as writer
from ..services.deep_agent.model_factory import agent_registry_config


class SupportsModelRebuild(Protocol):
    def rebuild_default_model(self) -> None: ...


def build_agent_channels_router(
    agent_service: SupportsModelRebuild,
    *,
    settings: Settings | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/agent", tags=["agent-channels"])

    def _require_write() -> None:
        active = settings or get_settings()
        if not active.feature_model_write_api:
            raise HTTPException(
                status_code=403,
                detail="model write API disabled (set OPEN_OTC_FEATURE_MODEL_WRITE_API=true)",
            )

    def _apply(fn, *args) -> dict:
        _require_write()
        try:
            new_registry = fn(*args)
        except writer.RegistryValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except writer.RegistryConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        agent_service.rebuild_default_model()
        return agent_registry_config(new_registry)

    @router.get("/registry", response_model=AgentRegistryOut)
    def get_registry() -> dict:
        return agent_registry_config(cr.get_registry())

    @router.post("/channels", response_model=AgentRegistryOut)
    def create_channel(payload: ChannelWriteIn) -> dict:
        return _apply(writer.add_channel, payload.model_dump())

    @router.put("/channels/{name}", response_model=AgentRegistryOut)
    def update_channel(name: str, payload: ChannelWriteIn) -> dict:
        return _apply(writer.update_channel, name, payload.model_dump())

    @router.delete("/channels/{name}", response_model=AgentRegistryOut)
    def delete_channel(name: str) -> dict:
        return _apply(writer.delete_channel, name)

    @router.post("/channels/{name}/models", response_model=AgentRegistryOut)
    def add_model(name: str, payload: ModelWriteIn) -> dict:
        return _apply(writer.add_model, name, payload.model_dump())

    @router.put("/channels/{name}/models/{model_id:path}", response_model=AgentRegistryOut)
    def update_model(name: str, model_id: str, payload: ModelWriteIn) -> dict:
        return _apply(writer.update_model, name, model_id, payload.model_dump())

    @router.delete("/channels/{name}/models/{model_id:path}", response_model=AgentRegistryOut)
    def delete_model(name: str, model_id: str) -> dict:
        return _apply(writer.delete_model, name, model_id)

    @router.put("/registry/default", response_model=AgentRegistryOut)
    def set_default(payload: DefaultWriteIn) -> dict:
        return _apply(writer.set_default, payload.channel, payload.model)

    @router.post("/channels/validate")
    def validate(payload: dict) -> dict:
        _require_write()
        kind = payload.get("kind")
        body = payload.get("payload") or {}
        try:
            writer.validate_draft(kind, body)
        except writer.RegistryValidationError as exc:
            return {"ok": False, "errors": [str(exc)]}
        except writer.RegistryConflictError as exc:
            return {"ok": False, "errors": [str(exc)]}
        return {"ok": True, "errors": []}

    return router
```

- [ ] **Step 4: Register in `main.py`** — add the import beside the other `build_*` router imports (~line 255-261):

```python
from .routers.agent_channels import build_agent_channels_router
```

And include it beside the other routers (~line 4077-4094):

```python
    app.include_router(
        build_agent_channels_router(active_agent_service, settings=active_settings)
    )
```

(Use the same `active_settings` variable the arena router uses at that site.)

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agent_channels_router.py -v`
Expected: PASS (6 tests, incl. slash-id route + 403 gate + 409 default guard).

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/agent_channels.py backend/app/main.py tests/test_agent_channels_router.py
git commit -m "feat(models): flag-gated agent channel/model CRUD router"
```

---

### Task 7: Frontend API client + types

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`

**Interfaces:**
- Produces (types): `AgentRegistryModel`, `AgentRegistryChannel`, `AgentRegistry`, `ModelWrite`, `ChannelWrite`.
- Produces (client): `getAgentRegistry()`, `createChannel(c)`, `updateChannel(name, c)`, `deleteChannel(name)`, `createModel(channel, m)`, `updateModel(channel, id, m)`, `deleteModel(channel, id)`, `setDefaultModel(channel, model)`, `validateDraft(kind, payload)` — all returning `AgentRegistry` (except validate).

- [ ] **Step 1: Add types** — append to `frontend/src/types.ts`:

```typescript
export interface AgentRegistryModel {
  id: string;
  provider: string;
  label: string;
  description: string | null;
  tags: string[];
  protocol: string | null;
}

export interface AgentRegistryChannel {
  name: string;
  label: string;
  type: 'zenmux' | 'openai_compatible';
  base_url: string;
  anthropic_base_url: string | null;
  api_key_env: string | null;
  healthy: boolean;
  models: AgentRegistryModel[];
}

export interface AgentRegistry {
  default: { channel: string; model: string };
  channels: AgentRegistryChannel[];
}

export type ModelWrite = Omit<AgentRegistryModel, 'description'> & { description?: string | null };
export type ChannelWrite = Omit<AgentRegistryChannel, 'healthy' | 'models'> & {
  models?: ModelWrite[];
};
```

- [ ] **Step 2: Add client functions** — append to `frontend/src/api/client.ts` (follow the existing `fetch`/`jsonOrThrow` helper pattern in that file; match its base-URL + error handling). Use `encodeURIComponent` per path segment EXCEPT the model id, which is sent raw so the `{model_id:path}` route matches slashes:

```typescript
import type { AgentRegistry, ChannelWrite, ModelWrite } from '../types';

export async function getAgentRegistry(): Promise<AgentRegistry> {
  return apiGet<AgentRegistry>('/api/agent/registry');
}
export async function createChannel(c: ChannelWrite): Promise<AgentRegistry> {
  return apiPost<AgentRegistry>('/api/agent/channels', c);
}
export async function updateChannel(name: string, c: ChannelWrite): Promise<AgentRegistry> {
  return apiPut<AgentRegistry>(`/api/agent/channels/${encodeURIComponent(name)}`, c);
}
export async function deleteChannel(name: string): Promise<AgentRegistry> {
  return apiDelete<AgentRegistry>(`/api/agent/channels/${encodeURIComponent(name)}`);
}
export async function createModel(channel: string, m: ModelWrite): Promise<AgentRegistry> {
  return apiPost<AgentRegistry>(`/api/agent/channels/${encodeURIComponent(channel)}/models`, m);
}
export async function updateModel(channel: string, id: string, m: ModelWrite): Promise<AgentRegistry> {
  return apiPut<AgentRegistry>(`/api/agent/channels/${encodeURIComponent(channel)}/models/${id}`, m);
}
export async function deleteModel(channel: string, id: string): Promise<AgentRegistry> {
  return apiDelete<AgentRegistry>(`/api/agent/channels/${encodeURIComponent(channel)}/models/${id}`);
}
export async function setDefaultModel(channel: string, model: string): Promise<AgentRegistry> {
  return apiPut<AgentRegistry>('/api/agent/registry/default', { channel, model });
}
export async function validateDraft(kind: string, payload: unknown): Promise<{ ok: boolean; errors: string[] }> {
  return apiPost('/api/agent/channels/validate', { kind, payload });
}
```

NOTE: match the ACTUAL helper names in `client.ts` (`apiGet`/`apiPost`/`apiPut`/`apiDelete` may differ — read the file and use its real request helpers; if it uses a single `request()` wrapper, use that instead).

- [ ] **Step 3: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/client.ts
git commit -m "feat(models): frontend registry types + API client"
```

---

### Task 8: Frontend page + nav wiring + tests

**Files:**
- Create: `frontend/src/routes/ModelMaintenance.tsx`, `ModelMaintenance.live.tsx`, `ModelMaintenance.css`, `ModelMaintenance.test.tsx`
- Modify: `frontend/src/types.ts` (Route union), `frontend/src/lib/routing.ts` (ROUTE_PATHS), `frontend/src/main.tsx` (import + navItems + render + palette), `frontend/src/lib/routing.test.ts`

**Interfaces:**
- Consumes: Task 7 client functions + types; shared components `templates/PageScaffold` (or `SplitLayout`), `PageToolbar`, `RailItem`, `Button`, `Badge`, `Empty`, `Tabs`, `usePageContextReporter` — read `frontend/src/routes/Skills.tsx` for exact import paths/props before writing.
- Produces: route `'model-maintenance'` → `/model-maintenance`, component `ModelMaintenanceLive`.

- [ ] **Step 1: Write the failing routing test** — add to `frontend/src/lib/routing.test.ts` an assertion mirroring the existing cases:

```typescript
it('maps model-maintenance route to its path', () => {
  expect(routeToPath('model-maintenance')).toBe('/model-maintenance');
  expect(pathToRoute('/model-maintenance')).toBe('model-maintenance');
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- routing`
Expected: FAIL (route not in `ROUTE_PATHS`; TS error on the union).

- [ ] **Step 3: Wire the nav (4 sites)**

`frontend/src/types.ts` — add to the `Route` union: `| 'model-maintenance'`.

`frontend/src/lib/routing.ts` — add to `ROUTE_PATHS`: `'model-maintenance': '/model-maintenance',`.

`frontend/src/main.tsx` — import beside the other route imports:
```typescript
import { ModelMaintenanceLive } from './routes/ModelMaintenance.live';
```
add to `navItems`:
```typescript
  { route: 'model-maintenance' as const, label: 'Model Maintenance' },
```
add to the render switch (mirror the Skills line):
```typescript
        {route === 'model-maintenance' && (
          <ModelMaintenanceLive onPageContextChange={handlePageContextChange} />
        )}
```

- [ ] **Step 4: Run routing test to verify it passes**

Run: `cd frontend && npm test -- routing`
Expected: PASS.

- [ ] **Step 5: Build the page** — create `ModelMaintenance.live.tsx` (container: fetch registry on mount, hold selection + draft state, call client fns, debounced `validateDraft`, feedback banner) and `ModelMaintenance.tsx` (presentation: `SplitLayout` rail of channels with health `Badge` + nested model `RailItem`s, default badge; right pane contextual editor form — channel fields with `anthropic_base_url` shown only when `type === 'zenmux'`; model fields incl. tags chip input + protocol; Set-as-default / Delete / Add Model / New Channel actions). Use `usePageContextReporter`. Read `Skills.tsx`/`Skills.live.tsx` first and mirror their structure, prop shapes, and token-only CSS. Keep files focused; put styles in `ModelMaintenance.css` using existing design tokens only.

  Minimum behaviors the component test asserts:
  - Renders one rail section per channel with its `label` and a health indicator.
  - Renders each model row with its `label`.
  - `anthropic_base_url` input is present when the selected channel `type` is `zenmux`, absent otherwise.
  - Clicking Save on a model calls `updateModel` (mock) with the channel + id.

- [ ] **Step 6: Write the component test** — create `frontend/src/routes/ModelMaintenance.test.tsx`:

```typescript
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as client from '../api/client';
import { ModelMaintenanceLive } from './ModelMaintenance.live';

const REGISTRY = {
  default: { channel: 'zenmux', model: 'anthropic/claude-sonnet-4.6' },
  channels: [
    {
      name: 'zenmux', label: 'Zenmux', type: 'zenmux' as const,
      base_url: 'https://zenmux.ai/api/v1', anthropic_base_url: 'https://zenmux.ai/api/anthropic',
      api_key_env: 'ZENMUX_API_KEY', healthy: true,
      models: [{ id: 'anthropic/claude-sonnet-4.6', provider: 'anthropic', label: 'Claude Sonnet 4.6', description: null, tags: ['tool-use'], protocol: null }],
    },
  ],
};

beforeEach(() => {
  vi.spyOn(client, 'getAgentRegistry').mockResolvedValue(REGISTRY as never);
});

describe('ModelMaintenance', () => {
  it('renders channels and models from the registry', async () => {
    render(<ModelMaintenanceLive onPageContextChange={() => {}} />);
    expect(await screen.findByText('Zenmux')).toBeInTheDocument();
    expect(await screen.findByText('Claude Sonnet 4.6')).toBeInTheDocument();
  });
});
```

- [ ] **Step 7: Run frontend tests + type-check**

Run: `cd frontend && npm test -- ModelMaintenance routing && npx tsc --noEmit`
Expected: PASS, no type errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/routes/ModelMaintenance.* frontend/src/types.ts frontend/src/lib/routing.ts frontend/src/lib/routing.test.ts frontend/src/main.tsx
git commit -m "feat(models): Model Maintenance page + nav wiring"
```

---

### Task 9: Docs — CHANGELOG, README, CLAUDE.md

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]`), `README.md`, `CLAUDE.md`

- [ ] **Step 1: CHANGELOG** — under `[Unreleased] → Added`:

```markdown
- **Model Maintenance page** (`/model-maintenance`): add/edit/delete LLM channels and models
  from the UI, writing `config/agent_channels.yaml` (comment-preserving, validate-then-commit)
  and hot-reloading the registry. Gated by `OPEN_OTC_FEATURE_MODEL_WRITE_API` (default on).
```

- [ ] **Step 2: README** — add a short "Model Maintenance" bullet under the features/pages section describing the page and the write flag (env-var-name-only for secrets; does not sync arena `CANDIDATE_MODELS`).

- [ ] **Step 3: CLAUDE.md** — add a new subsystem section "Model maintenance UI" documenting: the writer (`channel_registry_writer.py`, ruamel round-trip, validate-then-commit under `_LOCK`, health-independent default guard), the router (`routers/agent_channels.py`, `{model_id:path}` for slash ids, `OPEN_OTC_FEATURE_MODEL_WRITE_API`), the page (`routes/ModelMaintenance.*`), and the gotcha that it does NOT sync `services/arena/models.py::CANDIDATE_MODELS`.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md README.md CLAUDE.md
git commit -m "docs: Model Maintenance page (writer flow, flag, arena non-sync gotcha)"
```

---

## Full verification (run before Stage 6 review)

- [ ] `.venv/bin/python -m pytest tests/test_channel_registry_writer.py tests/test_channel_registry_lock.py tests/test_agent_channels_router.py tests/test_agent_registry_config.py tests/test_config.py -v` → all PASS
- [ ] `.venv/bin/python -m pytest backend/tests -q` → no regressions (spot the agent/channel suites)
- [ ] `cd frontend && npm test` → PASS; `npx tsc --noEmit` → clean
- [ ] Manual: start backend, `GET /api/agent/registry` returns editable fields; add a model via the page; confirm `config/agent_channels.yaml` gained the entry WITH its comments intact and the model appears in the chat picker after reload.

## Self-Review notes

- **Spec coverage:** Decisions 1-7 → Tasks 1-9; validate-then-commit (D3) → Task 3 `_commit`; secrets name-only (D4) → serializer reads `api_key_env` name only, no secret path anywhere; example.yml untouched (D5) → writer only opens `_yaml_path()`; ruamel (D6) → Task 1/3; flag default-on + security note (D7) → Task 1 + router gate. Codex findings: slash ids → Task 6 `{model_id:path}` + test; health-independent default → Task 3 `_assert_default_integrity` + Task 4 test; reload race → Task 2; default-on trust boundary → documented, flag easy to disable.
- **Out-of-scope honored:** no arena `CANDIDATE_MODELS` write; no `.env` write; stale `backend/config/...` untouched.

# Multi-Channel Model Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded Zenmux-only model path with a YAML-driven channel/model registry, expose a frontend picker (inline in the composer), and wire per-message model selection through to the LangGraph agent — including HITL resume that uses the originating message's model.

**Architecture:** A new `channel_registry` module is the single source of truth for `(channel, provider, model)` triples; it loads from `config/agent_channels.yaml` at boot and on demand via `POST /api/agent/channels/reload`. The Pydantic `AgentModelSelection` wire format gains a `channel` field with a back-compat validator that defaults legacy `{provider, model}` rows to `channel="zenmux"`. The frontend fetches a nested catalog from `GET /api/agent/models`, renders a custom `ModelPicker` dropdown in the composer's actions row, and includes the selection in every send. AI bubbles show a model chip with a provider-colored dot.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, LangChain (`langchain_anthropic`, `langchain_openai`), LangGraph, pyyaml, python-dotenv, React 18 + Vite + Vitest.

**Spec:** `docs/superpowers/specs/2026-05-09-multi-channel-model-selection-design.md`

---

## File Structure

**Backend — new files:**
- `config/agent_channels.yaml` — seed catalog (Zenmux + DeepSeek)
- `backend/app/services/deep_agent/channel_registry.py` — registry data structures, loader, hot-reload
- `tests/test_channel_registry.py` — registry unit tests

**Backend — modified files:**
- `backend/app/config.py` — remove legacy fields, add `agent_channels_file`
- `backend/app/schemas.py` — `AgentModelSelection` (add `channel`, free-form `provider`, back-compat validator), `AgentModelOption` (add `tags`), new `AgentChannelOut`, updated `AgentModelConfigOut`
- `backend/app/services/deep_agent/model_factory.py` — full rewrite to take a registry
- `backend/app/services/agents.py` — `AgentService` consumes registry, gains `rebuild_default_model`
- `backend/app/main.py` — `GET /api/agent/models`, `POST /api/agent/channels/reload`, modified stream endpoint, HITL resume reads originating message's model
- `tests/test_model_factory.py` — full rewrite against fixture registry
- `tests/test_api.py` — coverage for new endpoints + model-aware stream + HITL resume
- `.env.example` — drop legacy vars, add YAML pointer comment
- `pyproject.toml` — add `pyyaml`, `python-dotenv` deps

**Frontend — new files:**
- `frontend/src/components/ModelPicker.tsx` + `.css` + `.test.tsx`
- `frontend/src/components/providerColors.ts`

**Frontend — modified files:**
- `frontend/src/types.ts` — `AgentModelSelection`, `AgentModelOption`, `AgentChannel`, `AgentModelConfig`; extend `ChatMessage.meta`
- `frontend/src/routes/AgentDesk.live.tsx` — catalog fetch, model state, send-body wiring, refresh callback
- `frontend/src/routes/AgentDesk.tsx` — pass model props through
- `frontend/src/components/ChatComposer.tsx` — render `<ModelPicker />` in actions row, accept model props
- `frontend/src/components/ChatBubble.tsx` — render model chip (assistant messages with `meta.model_selection`)

---

## Pre-Flight: Discard In-Flight Code

There are uncommitted changes to `backend/app/schemas.py`, `backend/app/services/agents.py`, `backend/app/services/deep_agent/model_factory.py`, `tests/test_api.py`, and `tests/test_model_factory.py` from a previous attempt at this feature. Per the spec §7.4, those are rebased onto the new shape — meaning we discard them and rewrite from the post-feature design.

- [ ] **Step 1: Verify current state**

Run: `git status --short`
Expected output (or similar):
```
 M backend/app/schemas.py
 M backend/app/services/agents.py
 M backend/app/services/deep_agent/model_factory.py
 M tests/test_api.py
 M tests/test_model_factory.py
```

- [ ] **Step 2: Stash the in-flight changes**

Run:
```bash
git stash push -m "in-flight per-message model selection (pre-multi-channel rewrite)" \
  backend/app/schemas.py \
  backend/app/services/agents.py \
  backend/app/services/deep_agent/model_factory.py \
  tests/test_api.py \
  tests/test_model_factory.py
```

Expected: `Saved working directory and index state On main: in-flight per-message model selection (pre-multi-channel rewrite)`.

The stash can be inspected later with `git stash show -p stash@{0}` if any of the orchestration glue is worth re-cherry-picking — but the new tasks below assume a clean baseline.

- [ ] **Step 3: Confirm baseline is clean**

Run: `git status --short`
Expected: only the spec files under `docs/superpowers/`, untracked SQLite WAL/SHM files, `backend/open_otc_trading.egg-info/`, and `uv.lock`. None of the five files from Step 1 should appear.

---

## Task 1: Add YAML deps + seed catalog file

**Files:**
- Modify: `pyproject.toml`
- Create: `config/agent_channels.yaml`

- [ ] **Step 1: Add `pyyaml` and `python-dotenv` to `pyproject.toml`**

Edit `pyproject.toml`'s `[project].dependencies` list to add the two libraries (alphabetical order doesn't matter; insert near similar dependency packages):

```toml
dependencies = [
  "fastapi>=0.124.0",
  "uvicorn>=0.38.0",
  "sqlalchemy>=2.0.25",
  "alembic>=1.17.0",
  "pydantic>=2.12.0",
  "pydantic-settings>=2.0.0",
  "python-dotenv>=1.0.0",
  "python-multipart>=0.0.9",
  "pandas>=2.0.0",
  "openpyxl>=3.1.0",
  "pyyaml>=6.0",
  "langchain>=1.2.0",
  "langchain-openai>=1.0.0",
  "langgraph>=1.0.0",
  "aiosqlite>=0.20.0",
  "langsmith>=0.4.0",
  "deepagents>=0.5.3",
  "akshare>=1.16.0",
]
```

- [ ] **Step 2: Sync the env**

Run: `uv sync`
Expected: dependencies install without error; `pyyaml` and `python-dotenv` appear under "+" in the output.

- [ ] **Step 3: Create `config/agent_channels.yaml`**

```bash
mkdir -p config
```

Then write `config/agent_channels.yaml`:

```yaml
default:
  channel: zenmux
  model: anthropic/claude-sonnet-4-6

channels:
  - name: zenmux
    label: Zenmux
    type: zenmux
    api_key_env: ZENMUX_API_KEY
    base_url: https://zenmux.ai/api/v1
    anthropic_base_url: https://zenmux.ai/api/anthropic
    models:
      - id: anthropic/claude-opus-4-7
        provider: anthropic
        label: Claude Opus 4.7
        description: Most capable, highest cost
        tags: [reasoning]
      - id: anthropic/claude-sonnet-4-6
        provider: anthropic
        label: Claude Sonnet 4.6
        tags: [tool-use, reasoning]
      - id: anthropic/claude-haiku-4-5
        provider: anthropic
        label: Claude Haiku 4.5
        tags: [fast]
      - id: openai/gpt-5.4
        provider: openai
        label: GPT-5.4
        tags: [tool-use]

  - name: deepseek
    label: DeepSeek
    type: openai_compatible
    api_key_env: DEEPSEEK_API_KEY
    base_url: https://api.deepseek.com
    models:
      - id: deepseek-v4-flash
        provider: deepseek
        label: DeepSeek V4 Flash
        description: Fast, low-cost reasoning
        tags: [fast, reasoning]
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock config/agent_channels.yaml
git commit -m "feat(agent): seed multi-channel YAML config + add pyyaml/dotenv deps"
```

---

## Task 2: Channel registry data structures

**Files:**
- Create: `backend/app/services/deep_agent/channel_registry.py`
- Test: `tests/test_channel_registry.py`

- [ ] **Step 1: Write failing tests for the dataclasses**

Create `tests/test_channel_registry.py`:

```python
from __future__ import annotations

from app.services.deep_agent.channel_registry import (
    ChannelDescriptor,
    ChannelRegistry,
    ModelDescriptor,
)


def test_model_descriptor_is_frozen():
    md = ModelDescriptor(id="m", provider="p", label="L")
    assert md.id == "m"
    assert md.tags == ()


def test_channel_descriptor_default_anthropic_base_url_is_none():
    cd = ChannelDescriptor(
        name="c", label="C", type="openai_compatible",
        api_key="k", base_url="https://x", anthropic_base_url=None,
        models=(), healthy=True,
    )
    assert cd.anthropic_base_url is None


def test_registry_find_model_returns_descriptors():
    md = ModelDescriptor(id="m", provider="anthropic", label="L")
    cd = ChannelDescriptor(
        name="zenmux", label="Z", type="zenmux",
        api_key="k", base_url="https://x", anthropic_base_url="https://y",
        models=(md,), healthy=True,
    )
    reg = ChannelRegistry(channels=(cd,), default=("zenmux", "anthropic", "m"))
    found_channel, found_model = reg.find_model("zenmux", "anthropic", "m")
    assert found_channel is cd
    assert found_model is md


def test_registry_find_model_raises_on_unknown():
    import pytest
    reg = ChannelRegistry(channels=(), default=("?", "?", "?"))
    with pytest.raises(KeyError, match="unknown selection"):
        reg.find_model("zenmux", "anthropic", "m")


def test_registry_default_selection_returns_dict():
    reg = ChannelRegistry(channels=(), default=("zenmux", "anthropic", "m"))
    assert reg.default_selection() == {
        "channel": "zenmux",
        "provider": "anthropic",
        "model": "m",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_channel_registry.py -v`
Expected: ImportError (`channel_registry` module does not exist yet).

- [ ] **Step 3: Implement the data structures**

Create `backend/app/services/deep_agent/channel_registry.py`:

```python
"""Channel + model registry loaded from config/agent_channels.yaml.

The registry is the single source of truth for which (channel, provider, model)
combinations are dispatchable. It replaces the legacy Settings.zenmux_* and
agent_provider/agent_model_* fields. See
docs/superpowers/specs/2026-05-09-multi-channel-model-selection-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ModelDescriptor:
    id: str
    provider: str
    label: str
    description: str | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChannelDescriptor:
    name: str
    label: str
    type: Literal["zenmux", "openai_compatible"]
    api_key: str | None
    base_url: str
    anthropic_base_url: str | None
    models: tuple[ModelDescriptor, ...]
    healthy: bool


@dataclass(frozen=True)
class ChannelRegistry:
    channels: tuple[ChannelDescriptor, ...]
    default: tuple[str, str, str]

    def find_model(
        self, channel: str, provider: str, model: str
    ) -> tuple[ChannelDescriptor, ModelDescriptor]:
        for ch in self.channels:
            if ch.name != channel:
                continue
            for md in ch.models:
                if md.id == model and md.provider == provider:
                    return ch, md
        raise KeyError(f"unknown selection: channel={channel!r} provider={provider!r} model={model!r}")

    def default_selection(self) -> dict[str, str]:
        channel, provider, model = self.default
        return {"channel": channel, "provider": provider, "model": model}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_channel_registry.py -v`
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/channel_registry.py tests/test_channel_registry.py
git commit -m "feat(agent): add channel registry data structures"
```

---

## Task 3: Channel registry — `load()` with YAML parsing + validation

**Files:**
- Modify: `backend/app/services/deep_agent/channel_registry.py`
- Modify: `tests/test_channel_registry.py`

- [ ] **Step 1: Write failing tests for `load()`**

Append to `tests/test_channel_registry.py`:

```python
import os
from pathlib import Path

import pytest

from app.services.deep_agent import channel_registry


YAML_FIXTURE = """
default:
  channel: zenmux
  model: anthropic/claude-sonnet-4-6

channels:
  - name: zenmux
    label: Zenmux
    type: zenmux
    api_key_env: TEST_ZENMUX_KEY
    base_url: https://zenmux.test/api/v1
    anthropic_base_url: https://zenmux.test/api/anthropic
    models:
      - id: anthropic/claude-sonnet-4-6
        provider: anthropic
        label: Sonnet 4.6
        tags: [tool-use]
      - id: openai/gpt-5.4
        provider: openai
        label: GPT-5.4
  - name: deepseek
    label: DeepSeek
    type: openai_compatible
    api_key_env: TEST_DEEPSEEK_KEY
    base_url: https://api.deepseek.test
    models:
      - id: deepseek-v4-flash
        provider: deepseek
        label: DeepSeek V4 Flash
        description: fast
        tags: [fast]
"""


def _write_yaml(tmp_path: Path, body: str = YAML_FIXTURE) -> Path:
    path = tmp_path / "agent_channels.yaml"
    path.write_text(body)
    return path


def test_load_parses_yaml_and_returns_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_ZENMUX_KEY", "zm_fake")
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "ds_fake")
    path = _write_yaml(tmp_path)

    reg = channel_registry.load_from_path(path, force_reread_dotenv=False)

    assert {ch.name for ch in reg.channels} == {"zenmux", "deepseek"}
    zenmux = next(ch for ch in reg.channels if ch.name == "zenmux")
    assert zenmux.healthy is True
    assert zenmux.api_key == "zm_fake"
    assert zenmux.anthropic_base_url == "https://zenmux.test/api/anthropic"
    assert len(zenmux.models) == 2
    assert reg.default == ("zenmux", "anthropic", "anthropic/claude-sonnet-4-6")


def test_load_marks_channel_unhealthy_when_api_key_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_ZENMUX_KEY", raising=False)
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "ds_fake")
    path = _write_yaml(tmp_path)

    reg = channel_registry.load_from_path(path, force_reread_dotenv=False)

    zenmux = next(ch for ch in reg.channels if ch.name == "zenmux")
    assert zenmux.healthy is False
    assert zenmux.api_key is None


def test_load_treats_missing_api_key_env_as_local_channel(tmp_path):
    body = """
channels:
  - name: ollama
    label: Local
    type: openai_compatible
    api_key_env: null
    base_url: http://localhost:11434/v1
    models:
      - id: llama-3
        provider: meta
        label: Llama 3
"""
    path = _write_yaml(tmp_path, body)
    reg = channel_registry.load_from_path(path, force_reread_dotenv=False)

    ollama = reg.channels[0]
    assert ollama.healthy is True
    assert ollama.api_key is None


def test_load_raises_on_duplicate_channel_name(tmp_path, monkeypatch):
    monkeypatch.setenv("K", "x")
    body = """
channels:
  - name: dup
    label: A
    type: openai_compatible
    api_key_env: K
    base_url: https://a
    models:
      - id: m
        provider: x
        label: M
  - name: dup
    label: B
    type: openai_compatible
    api_key_env: K
    base_url: https://b
    models:
      - id: m2
        provider: x
        label: M2
"""
    path = _write_yaml(tmp_path, body)
    with pytest.raises(ValueError, match="duplicate channel name"):
        channel_registry.load_from_path(path, force_reread_dotenv=False)


def test_load_raises_when_zenmux_missing_anthropic_base_url(tmp_path, monkeypatch):
    monkeypatch.setenv("K", "x")
    body = """
channels:
  - name: zenmux
    label: Zenmux
    type: zenmux
    api_key_env: K
    base_url: https://x
    models:
      - id: m
        provider: openai
        label: M
"""
    path = _write_yaml(tmp_path, body)
    with pytest.raises(ValueError, match="anthropic_base_url"):
        channel_registry.load_from_path(path, force_reread_dotenv=False)


def test_load_raises_on_zenmux_invalid_model_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("K", "x")
    body = """
channels:
  - name: zenmux
    label: Zenmux
    type: zenmux
    api_key_env: K
    base_url: https://x
    anthropic_base_url: https://y
    models:
      - id: m
        provider: meta
        label: M
"""
    path = _write_yaml(tmp_path, body)
    with pytest.raises(ValueError, match="provider must be 'anthropic' or 'openai'"):
        channel_registry.load_from_path(path, force_reread_dotenv=False)


def test_load_raises_when_no_channels_declared(tmp_path):
    body = "channels: []\n"
    path = _write_yaml(tmp_path, body)
    with pytest.raises(ValueError, match="at least one channel"):
        channel_registry.load_from_path(path, force_reread_dotenv=False)


def test_load_falls_back_to_first_healthy_when_default_unhealthy(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_ZENMUX_KEY", raising=False)
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "ds_fake")
    path = _write_yaml(tmp_path)

    reg = channel_registry.load_from_path(path, force_reread_dotenv=False)

    # Zenmux (the YAML default) is unhealthy; fall back to first healthy.
    assert reg.default == ("deepseek", "deepseek", "deepseek-v4-flash")


def test_load_default_is_zenmux_default_when_zenmux_healthy(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_ZENMUX_KEY", "zm_fake")
    monkeypatch.delenv("TEST_DEEPSEEK_KEY", raising=False)
    path = _write_yaml(tmp_path)

    reg = channel_registry.load_from_path(path, force_reread_dotenv=False)

    assert reg.default == ("zenmux", "anthropic", "anthropic/claude-sonnet-4-6")


def test_load_when_no_healthy_channels_returns_disabled_registry(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_ZENMUX_KEY", raising=False)
    monkeypatch.delenv("TEST_DEEPSEEK_KEY", raising=False)
    path = _write_yaml(tmp_path)

    reg = channel_registry.load_from_path(path, force_reread_dotenv=False)

    assert all(not ch.healthy for ch in reg.channels)
    # Default still points somewhere — the YAML's stated default — so callers can
    # render "disabled" without crashing on attribute access.
    assert reg.default == ("zenmux", "anthropic", "anthropic/claude-sonnet-4-6")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_channel_registry.py -v`
Expected: most tests fail with `AttributeError: module 'channel_registry' has no attribute 'load_from_path'`.

- [ ] **Step 3: Implement `load_from_path()` and helpers**

Append to `backend/app/services/deep_agent/channel_registry.py`:

```python
import os
from pathlib import Path

import yaml


_VALID_TYPES = {"zenmux", "openai_compatible"}


def load_from_path(
    path: Path | str,
    *,
    force_reread_dotenv: bool = True,
) -> ChannelRegistry:
    """Parse the YAML at `path` and return a fully resolved ChannelRegistry.

    Raises ValueError on schema violations. Returns a registry even when no
    channels are healthy — the caller decides whether the agent is "disabled".
    """
    if force_reread_dotenv:
        try:
            from dotenv import load_dotenv  # imported lazily to keep tests fast
            load_dotenv(override=True)
        except ImportError:
            pass

    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"YAML root must be a mapping in {path}")

    declared_channels = raw.get("channels") or []
    if not isinstance(declared_channels, list) or not declared_channels:
        raise ValueError(f"at least one channel must be declared in {path}")

    channels: list[ChannelDescriptor] = []
    seen_names: set[str] = set()
    for entry in declared_channels:
        if not isinstance(entry, dict):
            raise ValueError(f"channel entries must be mappings, got {type(entry).__name__}")
        ch = _build_channel(entry)
        if ch.name in seen_names:
            raise ValueError(f"duplicate channel name: {ch.name!r}")
        seen_names.add(ch.name)
        channels.append(ch)

    default = _resolve_default(raw.get("default"), tuple(channels))
    return ChannelRegistry(channels=tuple(channels), default=default)


def _build_channel(entry: dict) -> ChannelDescriptor:
    name = _required_str(entry, "name")
    label = _required_str(entry, "label")
    type_ = _required_str(entry, "type")
    if type_ not in _VALID_TYPES:
        raise ValueError(f"channel {name!r}: type must be one of {sorted(_VALID_TYPES)}, got {type_!r}")

    base_url = _required_str(entry, "base_url")
    anthropic_base_url = entry.get("anthropic_base_url")
    if type_ == "zenmux" and not anthropic_base_url:
        raise ValueError(f"channel {name!r}: zenmux channels must declare anthropic_base_url")

    api_key_env = entry.get("api_key_env")
    if api_key_env is None or api_key_env == "":
        api_key: str | None = None
        healthy = True  # local-channel: no auth required
    else:
        if not isinstance(api_key_env, str):
            raise ValueError(f"channel {name!r}: api_key_env must be a string or null")
        resolved = os.environ.get(api_key_env)
        if resolved:
            api_key = resolved
            healthy = True
        else:
            api_key = None
            healthy = False

    raw_models = entry.get("models") or []
    if not isinstance(raw_models, list) or not raw_models:
        raise ValueError(f"channel {name!r}: at least one model must be declared")
    models: list[ModelDescriptor] = []
    seen_ids: set[str] = set()
    for m in raw_models:
        if not isinstance(m, dict):
            raise ValueError(f"channel {name!r}: model entries must be mappings")
        md = _build_model(name, type_, m)
        if md.id in seen_ids:
            raise ValueError(f"channel {name!r}: duplicate model id {md.id!r}")
        seen_ids.add(md.id)
        models.append(md)

    return ChannelDescriptor(
        name=name,
        label=label,
        type=type_,  # type: ignore[arg-type]
        api_key=api_key,
        base_url=base_url,
        anthropic_base_url=anthropic_base_url,
        models=tuple(models),
        healthy=healthy,
    )


def _build_model(channel_name: str, channel_type: str, m: dict) -> ModelDescriptor:
    model_id = _required_str(m, "id")
    provider = _required_str(m, "provider")
    label = _required_str(m, "label")
    if channel_type == "zenmux" and provider not in {"anthropic", "openai"}:
        raise ValueError(
            f"channel {channel_name!r}: model {model_id!r}: "
            f"provider must be 'anthropic' or 'openai' on zenmux channels, got {provider!r}"
        )
    description = m.get("description")
    if description is not None and not isinstance(description, str):
        raise ValueError(f"channel {channel_name!r}: model {model_id!r}: description must be a string or null")
    raw_tags = m.get("tags") or []
    if not isinstance(raw_tags, list) or not all(isinstance(t, str) for t in raw_tags):
        raise ValueError(f"channel {channel_name!r}: model {model_id!r}: tags must be a list[str]")
    return ModelDescriptor(
        id=model_id,
        provider=provider,
        label=label,
        description=description,
        tags=tuple(raw_tags),
    )


def _required_str(d: dict, key: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v:
        raise ValueError(f"missing or empty required field {key!r} in {d!r}")
    return v


def _resolve_default(
    declared: dict | None,
    channels: tuple[ChannelDescriptor, ...],
) -> tuple[str, str, str]:
    """Resolve the YAML `default:` block to a (channel, provider, model) tuple.

    Falls back to the first model of the first healthy channel when the declared
    default is missing or unresolvable. If no channel is healthy, falls back to
    the first model of the first channel (so the registry still has a default
    pointer; caller will detect the disabled state separately).
    """
    if isinstance(declared, dict):
        ch_name = declared.get("channel")
        model_id = declared.get("model")
        if isinstance(ch_name, str) and isinstance(model_id, str):
            for ch in channels:
                if ch.name != ch_name:
                    continue
                if not ch.healthy:
                    break
                for md in ch.models:
                    if md.id == model_id:
                        return (ch.name, md.provider, md.id)
                break  # channel found but model id not present

    for ch in channels:
        if ch.healthy and ch.models:
            return (ch.name, ch.models[0].provider, ch.models[0].id)
    first = channels[0]
    return (first.name, first.models[0].provider, first.models[0].id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_channel_registry.py -v`
Expected: all tests pass (10+ tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/channel_registry.py tests/test_channel_registry.py
git commit -m "feat(agent): channel registry YAML loader + validation"
```

---

## Task 4: Channel registry — module singleton + reload

**Files:**
- Modify: `backend/app/services/deep_agent/channel_registry.py`
- Modify: `tests/test_channel_registry.py`

- [ ] **Step 1: Write failing tests for the singleton API**

Append to `tests/test_channel_registry.py`:

```python
def test_configure_registry_overrides_singleton():
    md = ModelDescriptor(id="m", provider="x", label="M")
    cd = ChannelDescriptor(
        name="c", label="C", type="openai_compatible",
        api_key="k", base_url="https://x", anthropic_base_url=None,
        models=(md,), healthy=True,
    )
    fake = ChannelRegistry(channels=(cd,), default=("c", "x", "m"))

    channel_registry.configure_registry(fake)
    try:
        assert channel_registry.get_registry() is fake
    finally:
        channel_registry.configure_registry(None)


def test_get_registry_loads_from_default_path_when_unconfigured(tmp_path, monkeypatch):
    # Ensure clean override
    channel_registry.configure_registry(None)

    monkeypatch.setenv("TEST_ZENMUX_KEY", "zm_fake")
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "ds_fake")
    path = _write_yaml(tmp_path)
    monkeypatch.setenv("AGENT_CHANNELS_FILE", str(path))

    reg = channel_registry.get_registry()
    assert {ch.name for ch in reg.channels} == {"zenmux", "deepseek"}

    channel_registry.configure_registry(None)


def test_reload_swaps_registry_atomically(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_ZENMUX_KEY", "zm_fake")
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "ds_fake")
    path = _write_yaml(tmp_path)
    monkeypatch.setenv("AGENT_CHANNELS_FILE", str(path))

    reg1 = channel_registry.reload(force_reread_dotenv=False)
    assert {ch.name for ch in reg1.channels} == {"zenmux", "deepseek"}

    # Rewrite YAML to remove deepseek
    path.write_text(YAML_FIXTURE.replace(
        "  - name: deepseek", "  # deepseek removed"
    ).split("  # deepseek removed")[0])
    reg2 = channel_registry.reload(force_reread_dotenv=False)
    assert {ch.name for ch in reg2.channels} == {"zenmux"}
    assert channel_registry.get_registry() is reg2

    channel_registry.configure_registry(None)


def test_reload_keeps_old_registry_on_bad_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_ZENMUX_KEY", "zm_fake")
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "ds_fake")
    path = _write_yaml(tmp_path)
    monkeypatch.setenv("AGENT_CHANNELS_FILE", str(path))
    good = channel_registry.reload(force_reread_dotenv=False)

    path.write_text("channels: []\n")  # invalid: zero channels

    with pytest.raises(ValueError):
        channel_registry.reload(force_reread_dotenv=False)
    assert channel_registry.get_registry() is good

    channel_registry.configure_registry(None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_channel_registry.py -v`
Expected: 4 new tests fail with `AttributeError` for `configure_registry` / `get_registry` / `reload`.

- [ ] **Step 3: Implement the singleton API**

Append to `backend/app/services/deep_agent/channel_registry.py`:

```python
import threading


_LOCK = threading.RLock()
_REGISTRY: ChannelRegistry | None = None
_OVERRIDE: ChannelRegistry | None = None

DEFAULT_YAML_PATH = Path("./config/agent_channels.yaml")


def _yaml_path() -> Path:
    raw = os.environ.get("AGENT_CHANNELS_FILE")
    return Path(raw) if raw else DEFAULT_YAML_PATH


def get_registry() -> ChannelRegistry:
    """Return the live registry, loading lazily if not yet initialized.

    Tests can install a fixture via ``configure_registry``.
    """
    if _OVERRIDE is not None:
        return _OVERRIDE
    with _LOCK:
        global _REGISTRY
        if _REGISTRY is None:
            _REGISTRY = load_from_path(_yaml_path())
        return _REGISTRY


def reload(*, force_reread_dotenv: bool = True) -> ChannelRegistry:
    """Re-read YAML and env, atomically swap the registry, return the new one.

    If parsing/validation fails, the old registry remains live and the error
    propagates to the caller.
    """
    new_registry = load_from_path(_yaml_path(), force_reread_dotenv=force_reread_dotenv)
    with _LOCK:
        global _REGISTRY
        _REGISTRY = new_registry
    return new_registry


def configure_registry(registry: ChannelRegistry | None) -> None:
    """Install (or clear) a process-wide override for tests."""
    global _OVERRIDE, _REGISTRY
    _OVERRIDE = registry
    if registry is not None:
        # Mirror into _REGISTRY so subsequent `reload()` does NOT skip the override
        # — but tests usually configure_registry(None) then call reload to refresh.
        _REGISTRY = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_channel_registry.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/channel_registry.py tests/test_channel_registry.py
git commit -m "feat(agent): channel registry singleton + hot reload"
```

---

## Task 5: Update `schemas.py` — selection, options, channel-grouped catalog

**Files:**
- Modify: `backend/app/schemas.py`
- Test: `tests/test_schemas.py` (existing — append cases)

- [ ] **Step 1: Write failing tests for the schema changes**

Read `tests/test_schemas.py` to confirm structure, then append:

```python
import pytest

from app.schemas import (
    AgentChannelOut,
    AgentMessageCreate,
    AgentModelConfigOut,
    AgentModelOption,
    AgentModelSelection,
)


def test_agent_model_selection_requires_channel_provider_model():
    s = AgentModelSelection(channel="zenmux", provider="anthropic", model="m")
    assert s.channel == "zenmux"
    assert s.provider == "anthropic"
    assert s.model == "m"


def test_agent_model_selection_provider_is_free_form():
    # Used to be Literal["anthropic", "openai"]; now any string.
    s = AgentModelSelection(channel="deepseek", provider="deepseek", model="deepseek-v4-flash")
    assert s.provider == "deepseek"


def test_agent_model_selection_backfills_channel_for_legacy_dict():
    # Legacy thread history rows have only {provider, model}.
    s = AgentModelSelection.model_validate({"provider": "anthropic", "model": "m"})
    assert s.channel == "zenmux"


def test_agent_model_option_has_optional_tags():
    o = AgentModelOption(channel="c", provider="x", model="m", label="M")
    assert o.tags == []
    o2 = AgentModelOption(
        channel="c", provider="x", model="m", label="M",
        tags=["fast", "tool-use"],
    )
    assert o2.tags == ["fast", "tool-use"]


def test_agent_channel_out_includes_healthy_flag():
    ch = AgentChannelOut(name="c", label="C", type="openai_compatible")
    assert ch.healthy is True
    assert ch.models == []


def test_agent_model_config_out_holds_nested_channels():
    cfg = AgentModelConfigOut(
        enabled=True,
        active=AgentModelSelection(channel="zenmux", provider="anthropic", model="m"),
        channels=[
            AgentChannelOut(
                name="zenmux", label="Zenmux", type="zenmux", healthy=True,
                models=[
                    AgentModelOption(channel="zenmux", provider="anthropic", model="m", label="M"),
                ],
            )
        ],
    )
    assert cfg.channels[0].models[0].label == "M"


def test_agent_message_create_accepts_optional_model():
    payload = AgentMessageCreate.model_validate({
        "content": "hi",
        "model": {"channel": "zenmux", "provider": "anthropic", "model": "m"},
    })
    assert payload.model is not None
    assert payload.model.channel == "zenmux"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_schemas.py -v`
Expected: failures referencing the legacy `Literal["anthropic", "openai"]` provider, missing `channel` field, missing `tags`, missing `AgentChannelOut`, etc.

- [ ] **Step 3: Update `backend/app/schemas.py`**

Replace the existing `AgentModelSelection`, `AgentModelOption`, `AgentModelConfigOut` definitions (around lines 23-37) with:

```python
class AgentModelSelection(BaseModel):
    channel: str
    provider: str
    model: str

    @model_validator(mode="before")
    @classmethod
    def _backcompat_default_channel(cls, data: Any) -> Any:
        """Legacy thread-history rows have {provider, model} only."""
        if isinstance(data, dict) and "channel" not in data:
            return {**data, "channel": "zenmux"}
        return data


class AgentModelOption(AgentModelSelection):
    label: str
    description: str | None = None
    is_default: bool = False
    tags: list[str] = Field(default_factory=list)


class AgentChannelOut(BaseModel):
    name: str
    label: str
    type: Literal["zenmux", "openai_compatible"]
    healthy: bool = True
    models: list[AgentModelOption] = Field(default_factory=list)


class AgentModelConfigOut(BaseModel):
    enabled: bool
    active: AgentModelSelection
    channels: list[AgentChannelOut] = Field(default_factory=list)
```

The existing `AgentMessageCreate.model: AgentModelSelection | None` field (around line 77) stays — only the inner schema changed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_schemas.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas.py tests/test_schemas.py
git commit -m "feat(schemas): channel-aware AgentModelSelection + nested catalog"
```

---

## Task 6: Rewrite `model_factory.py` against the registry

**Files:**
- Modify: `backend/app/services/deep_agent/model_factory.py`
- Modify: `tests/test_model_factory.py`

- [ ] **Step 1: Write failing tests against fixture registries**

Replace the entire content of `tests/test_model_factory.py` with:

```python
from __future__ import annotations

import pytest

from app.services.deep_agent.channel_registry import (
    ChannelDescriptor,
    ChannelRegistry,
    ModelDescriptor,
)
from app.services.deep_agent.model_factory import (
    agent_model_config,
    build_agent_model,
    default_agent_model_selection,
    resolve_agent_model_selection,
)


def _registry(*, zenmux_healthy: bool = True, deepseek_healthy: bool = True) -> ChannelRegistry:
    zenmux = ChannelDescriptor(
        name="zenmux",
        label="Zenmux",
        type="zenmux",
        api_key="zm_fake" if zenmux_healthy else None,
        base_url="https://zenmux.test/api/v1",
        anthropic_base_url="https://zenmux.test/api/anthropic",
        healthy=zenmux_healthy,
        models=(
            ModelDescriptor(id="anthropic/claude-sonnet-4-6", provider="anthropic", label="Sonnet 4.6"),
            ModelDescriptor(id="openai/gpt-5.4", provider="openai", label="GPT-5.4"),
        ),
    )
    deepseek = ChannelDescriptor(
        name="deepseek",
        label="DeepSeek",
        type="openai_compatible",
        api_key="ds_fake" if deepseek_healthy else None,
        base_url="https://api.deepseek.test",
        anthropic_base_url=None,
        healthy=deepseek_healthy,
        models=(
            ModelDescriptor(id="deepseek-v4-flash", provider="deepseek", label="DeepSeek V4 Flash", tags=("fast",)),
        ),
    )
    return ChannelRegistry(
        channels=(zenmux, deepseek),
        default=("zenmux", "anthropic", "anthropic/claude-sonnet-4-6"),
    )


def test_build_agent_model_returns_chat_anthropic_for_zenmux_anthropic():
    from langchain_anthropic import ChatAnthropic
    model = build_agent_model(_registry())
    assert isinstance(model, ChatAnthropic)


def test_build_agent_model_uses_zenmux_anthropic_base_url():
    model = build_agent_model(_registry())
    base_url_attr = (
        getattr(model, "anthropic_api_url", None)
        or getattr(model, "base_url", None)
    )
    assert "zenmux.test/api/anthropic" in str(base_url_attr)


def test_build_agent_model_returns_chat_openai_for_zenmux_openai():
    from langchain_openai import ChatOpenAI
    model = build_agent_model(
        _registry(),
        selection={"channel": "zenmux", "provider": "openai", "model": "openai/gpt-5.4"},
    )
    assert isinstance(model, ChatOpenAI)


def test_build_agent_model_returns_chat_openai_for_openai_compatible_channel():
    from langchain_openai import ChatOpenAI
    model = build_agent_model(
        _registry(),
        selection={"channel": "deepseek", "provider": "deepseek", "model": "deepseek-v4-flash"},
    )
    assert isinstance(model, ChatOpenAI)
    base_url_attr = getattr(model, "openai_api_base", None) or getattr(model, "base_url", None)
    assert "api.deepseek.test" in str(base_url_attr)


def test_build_agent_model_returns_none_when_selected_channel_unhealthy():
    reg = _registry(zenmux_healthy=False)
    assert build_agent_model(reg) is None


def test_build_agent_model_raises_on_unknown_selection():
    with pytest.raises(KeyError, match="unknown selection"):
        build_agent_model(
            _registry(),
            selection={"channel": "zenmux", "provider": "anthropic", "model": "does-not-exist"},
        )


def test_default_agent_model_selection_returns_registry_default():
    assert default_agent_model_selection(_registry()) == {
        "channel": "zenmux",
        "provider": "anthropic",
        "model": "anthropic/claude-sonnet-4-6",
    }


def test_resolve_agent_model_selection_returns_default_when_none():
    assert resolve_agent_model_selection(_registry(), None) == {
        "channel": "zenmux",
        "provider": "anthropic",
        "model": "anthropic/claude-sonnet-4-6",
    }


def test_resolve_agent_model_selection_back_fills_legacy_channel():
    # Legacy thread-history rows have only {provider, model}.
    resolved = resolve_agent_model_selection(
        _registry(),
        {"provider": "anthropic", "model": "anthropic/claude-sonnet-4-6"},
    )
    assert resolved == {
        "channel": "zenmux",
        "provider": "anthropic",
        "model": "anthropic/claude-sonnet-4-6",
    }


def test_resolve_agent_model_selection_raises_on_unknown():
    with pytest.raises(ValueError, match="unsupported"):
        resolve_agent_model_selection(
            _registry(),
            {"channel": "zenmux", "provider": "anthropic", "model": "ghost"},
        )


def test_agent_model_config_returns_nested_catalog():
    cfg = agent_model_config(_registry())
    assert cfg["enabled"] is True
    assert cfg["active"]["channel"] == "zenmux"
    names = [ch["name"] for ch in cfg["channels"]]
    assert names == ["zenmux", "deepseek"]
    zenmux_models = cfg["channels"][0]["models"]
    assert any(m["model"] == "openai/gpt-5.4" for m in zenmux_models)


def test_agent_model_config_marks_disabled_when_no_healthy_channels():
    reg = _registry(zenmux_healthy=False, deepseek_healthy=False)
    cfg = agent_model_config(reg)
    assert cfg["enabled"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_model_factory.py -v`
Expected: failures (the existing factory takes `Settings`, not a registry; old surface).

- [ ] **Step 3: Replace `model_factory.py`**

Overwrite `backend/app/services/deep_agent/model_factory.py` with:

```python
"""Pluggable model factory for the desk deep agent.

Consults the channel registry (see ``channel_registry.py``) to resolve a
``(channel, provider, model)`` triple into a concrete LangChain chat model.
Returns ``None`` when the selected channel is unhealthy so AgentService can
render the "agent disabled" stub without raising.
"""
from __future__ import annotations

from collections.abc import Mapping

from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from .channel_registry import ChannelRegistry


def default_agent_model_selection(registry: ChannelRegistry) -> dict[str, str]:
    return registry.default_selection()


def resolve_agent_model_selection(
    registry: ChannelRegistry,
    selection: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Validate `selection` against `registry`. Back-fill `channel="zenmux"` for
    legacy `{provider, model}` rows. Raise ValueError on unknown selections."""
    if selection is None:
        return registry.default_selection()

    channel = str(selection.get("channel") or "zenmux")
    provider = str(selection.get("provider", ""))
    model = str(selection.get("model", ""))
    try:
        registry.find_model(channel, provider, model)
    except KeyError as exc:
        raise ValueError(
            f"unsupported agent model selection {channel}:{provider}:{model}"
        ) from exc
    return {"channel": channel, "provider": provider, "model": model}


def build_agent_model(
    registry: ChannelRegistry,
    selection: Mapping[str, str] | None = None,
) -> BaseChatModel | None:
    if selection is None:
        selection = registry.default_selection()
    channel, model_desc = registry.find_model(
        str(selection["channel"]), str(selection["provider"]), str(selection["model"])
    )
    if not channel.healthy:
        return None  # caller renders "agent disabled"

    if channel.type == "zenmux" and model_desc.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        assert channel.anthropic_base_url is not None  # validated at load
        return ChatAnthropic(
            model_name=model_desc.id,
            api_key=SecretStr(channel.api_key or ""),
            base_url=channel.anthropic_base_url,
            default_headers={"anthropic-version": "2023-06-01"},
            timeout=None,
            stop=None,
        )

    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=model_desc.id,
        api_key=SecretStr(channel.api_key) if channel.api_key else SecretStr(""),
        base_url=channel.base_url,
    )


def agent_model_config(registry: ChannelRegistry) -> dict[str, object]:
    active = registry.default_selection()
    enabled = any(ch.healthy for ch in registry.channels)
    channels_payload: list[dict[str, object]] = []
    for ch in registry.channels:
        models_payload: list[dict[str, object]] = []
        for md in ch.models:
            models_payload.append({
                "channel": ch.name,
                "provider": md.provider,
                "model": md.id,
                "label": md.label,
                "description": md.description,
                "tags": list(md.tags),
                "is_default": (
                    ch.name == active["channel"]
                    and md.provider == active["provider"]
                    and md.id == active["model"]
                ),
            })
        channels_payload.append({
            "name": ch.name,
            "label": ch.label,
            "type": ch.type,
            "healthy": ch.healthy,
            "models": models_payload,
        })
    return {
        "enabled": enabled,
        "active": active,
        "channels": channels_payload,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_model_factory.py -v`
Expected: all 12 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/model_factory.py tests/test_model_factory.py
git commit -m "feat(agent): rewrite model_factory against channel registry"
```

---

## Task 7: `Settings` cleanup

**Files:**
- Modify: `backend/app/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

Read `tests/test_config.py` to know the existing style. Append:

```python
def test_settings_no_longer_has_legacy_zenmux_fields():
    from app.config import Settings
    s = Settings()
    assert not hasattr(s, "zenmux_api_key")
    assert not hasattr(s, "zenmux_base_url")
    assert not hasattr(s, "agent_provider")
    assert not hasattr(s, "agent_model_anthropic")
    assert not hasattr(s, "agent_model_openai")
    assert not hasattr(s, "default_model")


def test_settings_has_agent_channels_file():
    from pathlib import Path
    from app.config import Settings
    s = Settings()
    assert isinstance(s.agent_channels_file, Path)
```

Also remove or rewrite any existing test that references `Settings.zenmux_api_key` etc. to use the registry instead. (Search with `grep -n 'zenmux_api_key\|agent_provider\|agent_model_anthropic\|agent_model_openai\|zenmux_base_url\|default_model' tests/test_config.py` and update each in place.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: legacy-field tests still pass and new tests fail because `agent_channels_file` doesn't exist; the legacy-removal test fails because the fields still exist.

- [ ] **Step 3: Update `backend/app/config.py`**

Replace the existing `_EnvironmentSettings` and `Settings` classes (preserving everything else) with:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _REPO_ROOT / ".env"


class _EnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    app_name: str = "Open OTC Trading"
    database_url: str = Field(
        "sqlite+pysqlite:///./data/open_otc.sqlite3",
        validation_alias="OPEN_OTC_DATABASE_URL",
    )
    artifact_dir: Path = Field(
        Path("./artifacts"),
        validation_alias="OPEN_OTC_ARTIFACT_DIR",
    )
    quantark_path: Path = Path("/Users/fuxinyao/quant-ark")
    agent_channels_file: Path = Field(
        Path("./config/agent_channels.yaml"),
        validation_alias="AGENT_CHANNELS_FILE",
    )
    agent_checkpoint_db_path: str = Field(
        "./agent_checkpoints.sqlite",
        validation_alias=AliasChoices("AGENT_CHECKPOINT_DB_PATH", "AGENT_CHECKPOINT_DB"),
    )


def _read_environment_settings() -> _EnvironmentSettings:
    return _EnvironmentSettings(_env_file=_ENV_FILE)


def _env_value(name: str) -> Any:
    return getattr(_read_environment_settings(), name)


@dataclass(frozen=True)
class Settings:
    app_name: str = "Open OTC Trading"
    database_url: str = field(default_factory=lambda: _env_value("database_url"))
    artifact_dir: Path = field(default_factory=lambda: _env_value("artifact_dir"))
    quantark_path: Path = field(default_factory=lambda: _env_value("quantark_path"))
    agent_channels_file: Path = field(default_factory=lambda: _env_value("agent_channels_file"))
    agent_checkpoint_db_path: str = field(default_factory=lambda: _env_value("agent_checkpoint_db_path"))

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_dir", Path(self.artifact_dir))
        object.__setattr__(self, "quantark_path", Path(self.quantark_path))
        object.__setattr__(self, "agent_channels_file", Path(self.agent_channels_file))


_SETTINGS_OVERRIDE: Settings | None = None


def configure_settings(new_settings: Settings | None) -> None:
    global _SETTINGS_OVERRIDE
    _SETTINGS_OVERRIDE = new_settings


def get_settings() -> Settings:
    if _SETTINGS_OVERRIDE is not None:
        return _SETTINGS_OVERRIDE
    return Settings()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: all tests pass.

- [ ] **Step 5: Confirm no other code references the removed fields**

Run: `grep -RIn "zenmux_api_key\|zenmux_base_url\|agent_provider\b\|agent_model_anthropic\|agent_model_openai\|settings.default_model" backend tests`
Expected: empty output (or only spec/docs files). If anything in `backend/` remains, follow up before committing.

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py tests/test_config.py
git commit -m "refactor(config): drop legacy zenmux/agent_provider settings fields"
```

---

## Task 8: `AgentService` consumes the registry

**Files:**
- Modify: `backend/app/services/agents.py`
- Modify: `tests/test_agents_helpers.py`

- [ ] **Step 1: Write failing test for `AgentService` constructor accepting a registry**

Read `tests/test_agents_helpers.py` to confirm the import style, then append:

```python
def test_agent_service_constructs_with_registry_param():
    from app.services.agents import AgentService
    from app.services.deep_agent.channel_registry import (
        ChannelDescriptor, ChannelRegistry, ModelDescriptor,
    )

    md = ModelDescriptor(id="m", provider="anthropic", label="M")
    cd = ChannelDescriptor(
        name="zenmux", label="Z", type="zenmux",
        api_key="k", base_url="https://x", anthropic_base_url="https://y",
        models=(md,), healthy=True,
    )
    reg = ChannelRegistry(channels=(cd,), default=("zenmux", "anthropic", "m"))

    svc = AgentService(registry=reg)
    assert svc.default_model_selection == {
        "channel": "zenmux", "provider": "anthropic", "model": "m",
    }


def test_agent_service_disabled_when_default_channel_unhealthy():
    from app.services.agents import AgentService
    from app.services.deep_agent.channel_registry import (
        ChannelDescriptor, ChannelRegistry, ModelDescriptor,
    )

    md = ModelDescriptor(id="m", provider="anthropic", label="M")
    cd = ChannelDescriptor(
        name="zenmux", label="Z", type="zenmux",
        api_key=None, base_url="https://x", anthropic_base_url="https://y",
        models=(md,), healthy=False,
    )
    reg = ChannelRegistry(channels=(cd,), default=("zenmux", "anthropic", "m"))

    svc = AgentService(registry=reg)
    assert svc.deep_agent is None
    assert svc.is_enabled() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agents_helpers.py -v`
Expected: fails with `TypeError` (constructor doesn't accept `registry`) or import errors.

- [ ] **Step 3: Update `AgentService.__init__` and related methods**

In `backend/app/services/agents.py`, update the imports near the top:

```python
from .deep_agent.channel_registry import ChannelRegistry, get_registry
from .deep_agent.model_factory import (
    build_agent_model,
    default_agent_model_selection,
    resolve_agent_model_selection,
)
```

Then replace the `AgentService.__init__` method:

```python
class AgentService:
    def __init__(
        self,
        settings: Settings | None = None,
        registry: ChannelRegistry | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.registry = registry or get_registry()
        self.tools = select_deep_agent_tools()
        self.default_model_selection = default_agent_model_selection(self.registry)
        self.model = build_agent_model(self.registry)
        if self.model is None:
            self.deep_agent = None
            self.checkpointer = None
        else:
            self.checkpointer = build_checkpointer(self.settings)
            self.deep_agent = build_orchestrator(
                model=self.model,
                tools=self.tools,
                checkpointer=self.checkpointer,
                interrupt_on=interrupt_on_config(),
            )
        self._owned_deep_agent = self.deep_agent
```

Add a `normalize_model_selection` helper:

```python
    def normalize_model_selection(
        self,
        model_selection: dict[str, str] | None = None,
    ) -> dict[str, str]:
        return resolve_agent_model_selection(self.registry, model_selection)
```

Replace `respond`:

```python
    def respond(
        self,
        session: Session,
        thread: AgentThread,
        content: str,
        requested_character: str = "auto",
        page_context: AgentPageContext | None = None,
        model_selection: dict[str, str] | None = None,
    ) -> AgentMessage:
        resolved = self.normalize_model_selection(model_selection)
        user_msg = AgentMessage(
            thread_id=thread.id,
            role="user",
            character=None,
            content=content,
            meta={
                "page_context": page_context.model_dump(mode="json") if page_context else None,
                "model_selection": resolved,
            },
        )
        session.add(user_msg)

        agent = self._sync_agent_for_selection(resolved)
        if agent is None:
            return self._persist_disabled_response(session, thread, resolved)

        context = self._context(session, page_context)
        assets = self._context_assets(page_context)
        prompt = _orchestrator_user_prompt(content, requested_character, context)

        try:
            result = agent.invoke(
                {"messages": [HumanMessage(content=prompt)]},
                config={"configurable": {"thread_id": str(thread.id)}},
            )
        except Exception as exc:
            logger.exception("DeepAgent invoke failed for thread %s", thread.id)
            record_audit(
                session,
                event_type="agent.error",
                actor="system",
                subject_type="thread",
                subject_id=thread.id,
                payload={"error_type": type(exc).__name__, "message": str(exc)[:500]},
            )
            raise

        return self._persist_agent_result(session, thread, result, assets, page_context, resolved)
```

Replace `stream_and_persist` (preserving the existing async generator structure but threading `resolved` through):

```python
    async def stream_and_persist(
        self,
        *,
        thread_id: int,
        content: str,
        requested_character: str = "auto",
        page_context: AgentPageContext | None = None,
        model_selection: dict[str, str] | None = None,
    ):
        resolved = self.normalize_model_selection(model_selection)
        if not self.is_enabled(resolved):
            message_id = await asyncio.to_thread(
                self._persist_disabled_response_by_thread, thread_id, resolved,
            )
            yield _sse("error", {"message": _DISABLED_RESPONSE, "retryable": False})
            yield _sse("done", {"message_id": message_id})
            return

        with _database.SessionLocal() as session:
            context = self._context(session, page_context)
        assets = self._context_assets(page_context)
        prompt = _orchestrator_user_prompt(content, requested_character, context)
        config = {"configurable": {"thread_id": str(thread_id)}}
        collector = StreamCollector()
        persisted = False

        async with self._streaming_agent(resolved) as agent:
            try:
                try:
                    async for sse_line in self._drive_stream(agent, prompt, config, collector):
                        yield sse_line
                except Exception as exc:
                    logger.exception("Live stream failed for thread %s", thread_id)
                    collector.error = str(exc)[:500]
                    yield _sse("error", {"message": collector.error, "retryable": False})

                message_id = await self._finalize_turn(
                    agent, config, thread_id, collector, assets, page_context, resolved,
                )
                persisted = True
                yield _sse("done", {"message_id": message_id})
            finally:
                if not persisted:
                    try:
                        await self._finalize_turn(
                            agent, config, thread_id, collector, assets, page_context, resolved,
                        )
                    except Exception:
                        logger.exception(
                            "Persist failed during cancellation for thread %s", thread_id
                        )
```

Replace `_streaming_agent`, `_sync_agent_for_selection`, `is_enabled`:

```python
    @asynccontextmanager
    async def _streaming_agent(self, model_selection: dict[str, str]):
        if not self._is_default_model_selection(model_selection):
            model = build_agent_model(self.registry, model_selection)
            if model is None:
                yield None
                return
            async with build_async_checkpointer(self.settings) as checkpointer:
                yield build_orchestrator(
                    model=model,
                    tools=self.tools,
                    checkpointer=checkpointer,
                    interrupt_on=interrupt_on_config(),
                )
            return

        if self._needs_async_sqlite_streaming_agent():
            async with build_async_checkpointer(self.settings) as checkpointer:
                yield build_orchestrator(
                    model=self.model,
                    tools=self.tools,
                    checkpointer=checkpointer,
                    interrupt_on=interrupt_on_config(),
                )
        else:
            yield self.deep_agent

    def _sync_agent_for_selection(self, model_selection: dict[str, str]) -> Any:
        if self._is_default_model_selection(model_selection):
            return self.deep_agent
        model = build_agent_model(self.registry, model_selection)
        if model is None:
            return None
        return build_orchestrator(
            model=model,
            tools=self.tools,
            checkpointer=build_checkpointer(self.settings),
            interrupt_on=interrupt_on_config(),
        )

    def is_enabled(self, model_selection: dict[str, str] | None = None) -> bool:
        resolved = self.normalize_model_selection(model_selection)
        if self._is_default_model_selection(resolved):
            return self.deep_agent is not None
        return build_agent_model(self.registry, resolved) is not None

    def _is_default_model_selection(self, model_selection: dict[str, str]) -> bool:
        return model_selection == self.default_model_selection
```

Update `invoke_resume` and the `_persist_*` helpers to thread `model_selection` (or its resolved form) through to the persisted `meta` block — same pattern as `respond`. Specifically, `_persist_agent_result`, `_persist_disabled_response`, and `_persist_disabled_response_by_thread` each gain an optional `model_selection: dict[str, str] | None = None` parameter and write `"model_selection": self.normalize_model_selection(model_selection)` into the message's `meta`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_agents_helpers.py tests/test_stream_and_persist.py tests/test_stream_and_persist_hitl.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agents.py tests/test_agents_helpers.py
git commit -m "feat(agent): AgentService consumes ChannelRegistry"
```

---

## Task 9: `AgentService.rebuild_default_model`

**Files:**
- Modify: `backend/app/services/agents.py`
- Modify: `tests/test_agents_helpers.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_agents_helpers.py`:

```python
def test_agent_service_rebuild_default_model_picks_up_new_registry(monkeypatch):
    from app.services.agents import AgentService
    from app.services.deep_agent import channel_registry
    from app.services.deep_agent.channel_registry import (
        ChannelDescriptor, ChannelRegistry, ModelDescriptor,
    )

    def make_reg(model_id: str) -> ChannelRegistry:
        md = ModelDescriptor(id=model_id, provider="anthropic", label=model_id)
        cd = ChannelDescriptor(
            name="zenmux", label="Z", type="zenmux",
            api_key="k", base_url="https://x", anthropic_base_url="https://y",
            models=(md,), healthy=True,
        )
        return ChannelRegistry(channels=(cd,), default=("zenmux", "anthropic", model_id))

    reg1 = make_reg("model-a")
    svc = AgentService(registry=reg1)
    assert svc.default_model_selection["model"] == "model-a"

    reg2 = make_reg("model-b")
    channel_registry.configure_registry(reg2)
    try:
        svc.rebuild_default_model()
        assert svc.default_model_selection["model"] == "model-b"
    finally:
        channel_registry.configure_registry(None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agents_helpers.py::test_agent_service_rebuild_default_model_picks_up_new_registry -v`
Expected: `AttributeError: rebuild_default_model`.

- [ ] **Step 3: Implement the method**

In `backend/app/services/agents.py`, add after `__init__`:

```python
    def rebuild_default_model(self) -> None:
        """Refresh the cached default model after a registry reload."""
        self.registry = get_registry()
        self.default_model_selection = default_agent_model_selection(self.registry)
        self.model = build_agent_model(self.registry)
        if self.model is None:
            self.deep_agent = None
            self.checkpointer = None
        else:
            self.checkpointer = build_checkpointer(self.settings)
            self.deep_agent = build_orchestrator(
                model=self.model,
                tools=self.tools,
                checkpointer=self.checkpointer,
                interrupt_on=interrupt_on_config(),
            )
        self._owned_deep_agent = self.deep_agent
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_agents_helpers.py::test_agent_service_rebuild_default_model_picks_up_new_registry -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agents.py tests/test_agents_helpers.py
git commit -m "feat(agent): AgentService.rebuild_default_model"
```

---

## Task 10: `GET /api/agent/models` endpoint

**Files:**
- Modify: `backend/app/main.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_api.py`:

```python
def test_get_agent_models_returns_catalog(client):
    response = client.get("/api/agent/models")
    assert response.status_code == 200
    data = response.json()
    assert "enabled" in data
    assert "active" in data
    assert "channels" in data
    assert isinstance(data["channels"], list)
    if data["channels"]:
        first = data["channels"][0]
        assert {"name", "label", "type", "healthy", "models"} <= set(first.keys())
```

(If no `client` fixture exists, add one mirroring the pattern in nearby tests — this typically wraps `create_app()` with FastAPI's `TestClient`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api.py::test_get_agent_models_returns_catalog -v`
Expected: 404 (route doesn't exist).

- [ ] **Step 3: Add the route**

In `backend/app/main.py`, locate the existing `health` route around line 220 and add (immediately after, or grouped with other agent routes near line 234):

```python
    from .schemas import AgentModelConfigOut  # if not already imported
    from .services.deep_agent.model_factory import agent_model_config
    from .services.deep_agent.channel_registry import get_registry as _get_registry

    @app.get("/api/agent/models", response_model=AgentModelConfigOut)
    def get_agent_models() -> dict:
        return agent_model_config(_get_registry())
```

(In practice, hoist the imports to the top of `main.py` rather than nesting them — they're shown nested here for clarity in the patch.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_api.py::test_get_agent_models_returns_catalog -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py tests/test_api.py
git commit -m "feat(api): GET /api/agent/models exposes channel catalog"
```

---

## Task 11: `POST /api/agent/channels/reload` endpoint

**Files:**
- Modify: `backend/app/main.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_api.py`:

```python
def test_post_reload_channels_returns_summary(client):
    response = client.post("/api/agent/channels/reload")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "active" in data
    assert isinstance(data["healthy_channels"], list)
    assert isinstance(data["errors"], list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api.py::test_post_reload_channels_returns_summary -v`
Expected: 404.

- [ ] **Step 3: Add the route**

In `backend/app/main.py`, near the `get_agent_models` route, add:

```python
    from .services.deep_agent import channel_registry as _channel_registry

    @app.post("/api/agent/channels/reload")
    def reload_channels() -> dict:
        # Note: the model object held by an in-flight stream stays alive via Python
        # references; that stream completes against the old model. New requests
        # after this call use the new registry.
        try:
            new_registry = _channel_registry.reload(force_reread_dotenv=True)
        except Exception as exc:
            logger.exception("channel reload failed")
            raise HTTPException(status_code=400, detail=f"reload failed: {exc}") from exc
        agent_service.rebuild_default_model()
        return {
            "ok": True,
            "active": new_registry.default_selection(),
            "healthy_channels": [ch.name for ch in new_registry.channels if ch.healthy],
            "errors": [],
        }
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/test_api.py::test_post_reload_channels_returns_summary -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py tests/test_api.py
git commit -m "feat(api): POST /api/agent/channels/reload triggers hot reload"
```

---

## Task 12: Stream endpoint accepts and persists `model`

**Files:**
- Modify: `backend/app/main.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_api.py`:

```python
def test_stream_message_persists_model_selection_on_user_msg(client):
    # Create a thread
    thread = client.post(
        "/api/chat/threads",
        json={"title": "t", "character": "trader"},
    ).json()
    tid = thread["id"]

    # Send with explicit model selection
    body = {
        "content": "hi",
        "character": "auto",
        "model": {"channel": "zenmux", "provider": "anthropic", "model": "anthropic/claude-sonnet-4-6"},
    }
    response = client.post(f"/api/chat/threads/{tid}/messages/stream", json=body)
    assert response.status_code == 200
    response.read()  # drain SSE

    # Re-fetch thread, find the user message, check its meta
    threads = client.get("/api/chat/threads").json()
    msgs = [t for t in threads if t["id"] == tid][0]["messages"]
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert user_msgs, "user message not persisted"
    assert user_msgs[-1]["meta"]["model_selection"] == {
        "channel": "zenmux",
        "provider": "anthropic",
        "model": "anthropic/claude-sonnet-4-6",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api.py::test_stream_message_persists_model_selection_on_user_msg -v`
Expected: AssertionError — `meta` lacks `model_selection`.

- [ ] **Step 3: Update the stream endpoint in `main.py`**

In `backend/app/main.py`, replace the existing `stream_chat_message` route body (around lines 249-283):

```python
    @app.post("/api/chat/threads/{thread_id}/messages/stream")
    async def stream_chat_message(
        thread_id: int,
        payload: AgentMessageCreate,
        session: Session = Depends(get_db),
    ):
        thread = session.get(AgentThread, thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")

        resolved_selection = agent_service.normalize_model_selection(
            payload.model.model_dump() if payload.model else None
        )

        user_msg = AgentMessage(
            thread_id=thread.id,
            role="user",
            character=None,
            content=payload.content,
            meta={
                "page_context": payload.page_context.model_dump(mode="json")
                if payload.page_context
                else None,
                "model_selection": resolved_selection,
            },
        )
        session.add(user_msg)
        session.commit()

        return StreamingResponse(
            agent_service.stream_and_persist(
                thread_id=thread.id,
                content=payload.content,
                requested_character=payload.character,
                page_context=payload.page_context,
                model_selection=resolved_selection,
            ),
            media_type="text/event-stream",
        )
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/test_api.py::test_stream_message_persists_model_selection_on_user_msg -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py tests/test_api.py
git commit -m "feat(api): stream endpoint accepts and persists model selection"
```

---

## Task 13: HITL resume reads originating message's model

**Files:**
- Modify: `backend/app/main.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_api.py`:

```python
def test_hitl_resume_uses_originating_message_model(monkeypatch, client):
    """Confirming a pending action invokes the agent built for the originating
    message's model selection, not the default cached one."""
    from app import database
    from app.models import AgentMessage, AgentThread
    from app.services.agents import AgentService

    captured: list[dict] = []

    def fake_sync_agent(self, model_selection):
        captured.append(model_selection)
        class _Stub:
            def invoke(self, cmd, config=None):
                from langchain_core.messages import AIMessage
                return {"messages": [AIMessage(content="ok")]}
        return _Stub()

    monkeypatch.setattr(AgentService, "_sync_agent_for_selection", fake_sync_agent)

    originating = {"channel": "zenmux", "provider": "openai", "model": "openai/gpt-5.4"}
    with database.SessionLocal() as session:
        thread = AgentThread(title="t", character="trader")
        session.add(thread)
        session.flush()
        msg = AgentMessage(
            thread_id=thread.id,
            role="assistant",
            character="trader",
            content="please confirm",
            meta={
                "agent_phase": "awaiting_confirmation",
                "model_selection": originating,
                "pending_actions": [{
                    "id": "act-1",
                    "tool_name": "calculate_risk",
                    "label": "Run risk",
                    "summary": "ok",
                    "payload": {},
                    "status": "pending",
                }],
            },
        )
        session.add(msg)
        session.commit()
        thread_id = thread.id
        msg_id = msg.id

    response = client.post(
        f"/api/chat/threads/{thread_id}/messages/{msg_id}/actions/act-1/confirm",
    )
    assert response.status_code == 200
    assert captured == [originating], (
        f"resume should use originating selection {originating}, got {captured}"
    )


def test_hitl_resume_falls_back_when_originating_model_missing(monkeypatch, client):
    """If the originating selection no longer resolves (admin removed the model),
    resume falls back to default and the new message records the fallback flag."""
    from app import database
    from app.models import AgentMessage, AgentThread
    from app.services.agents import AgentService

    def fake_sync_agent(self, model_selection):
        class _Stub:
            def invoke(self, cmd, config=None):
                from langchain_core.messages import AIMessage
                return {"messages": [AIMessage(content="ok")]}
        return _Stub()

    monkeypatch.setattr(AgentService, "_sync_agent_for_selection", fake_sync_agent)

    bogus = {"channel": "ghost-channel", "provider": "ghost", "model": "ghost-model"}
    with database.SessionLocal() as session:
        thread = AgentThread(title="t", character="trader")
        session.add(thread)
        session.flush()
        msg = AgentMessage(
            thread_id=thread.id,
            role="assistant",
            character="trader",
            content="please confirm",
            meta={
                "agent_phase": "awaiting_confirmation",
                "model_selection": bogus,
                "pending_actions": [{
                    "id": "act-2",
                    "tool_name": "calculate_risk",
                    "label": "Run risk",
                    "summary": "ok",
                    "payload": {},
                    "status": "pending",
                }],
            },
        )
        session.add(msg)
        session.commit()
        thread_id = thread.id
        msg_id = msg.id

    response = client.post(
        f"/api/chat/threads/{thread_id}/messages/{msg_id}/actions/act-2/confirm",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["meta"].get("model_selection_fallback") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api.py::test_hitl_resume_uses_originating_message_model -v`
Expected: assertion failure — `captured[0]` is the default selection, not the originating message's.

- [ ] **Step 3: Update `_resume_action` in `main.py`**

In `backend/app/main.py`, replace lines around 285-340:

```python
    def _resume_action(
        *,
        thread_id: int,
        message_id: int,
        action_id: str,
        decision: str,
        session: Session,
    ) -> AgentMessage:
        if agent_service.deep_agent is None and not any(
            ch.healthy for ch in agent_service.registry.channels
        ):
            raise HTTPException(status_code=503, detail="Agent is disabled (no LLM configured)")

        source_message = session.query(AgentMessage).filter(AgentMessage.id == message_id).one_or_none()
        if source_message is None or source_message.role != "assistant":
            raise HTTPException(status_code=400, detail="Only assistant action proposals can be resumed")

        source_meta = deepcopy(source_message.meta or {})
        pending_actions = source_meta.get("pending_actions") or []
        action = next((a for a in pending_actions if a.get("id") == action_id), None)
        if action is None:
            raise HTTPException(status_code=404, detail="Pending action not found")
        if action.get("status") != "pending":
            raise HTTPException(status_code=409, detail=f"Action already {action.get('status')}")

        cmd = build_resume_command(
            decision=("approve" if decision == "confirm" else "reject"),
            message=("User dismissed the action." if decision == "dismiss" else None),
        )

        # Resume against the originating message's model selection — not the default.
        # Falls back with audit if the selection is no longer resolvable in the
        # current registry (e.g., admin removed the model since the message was sent).
        originating = source_meta.get("model_selection")
        fallback_used = False
        try:
            resolved = agent_service.normalize_model_selection(originating)
        except ValueError:
            resolved = agent_service.default_model_selection
            fallback_used = True
            logger.warning(
                "HITL resume falling back to default; originating selection unresolvable: %r",
                originating,
            )

        agent = agent_service._sync_agent_for_selection(resolved)
        if agent is None:
            raise HTTPException(status_code=503, detail="Agent is disabled (no LLM configured)")

        try:
            result = agent.invoke(
                cmd,
                config={"configurable": {"thread_id": str(thread_id)}},
            )
        except Exception as exc:
            logger.exception("Resume failed for thread %s action %s", thread_id, action_id)
            raise HTTPException(status_code=502, detail=f"Agent resume failed: {exc}") from exc

        new_status = "confirmed" if decision == "confirm" else "dismissed"
        for entry in pending_actions:
            if entry.get("id") == action_id:
                entry["status"] = new_status
                entry["resolved_at"] = datetime.utcnow().isoformat()
        source_message.meta = {**source_meta, "pending_actions": pending_actions}
        flag_modified(source_message, "meta")

        record_audit(
            session,
            event_type=("agent.action.confirmed" if decision == "confirm" else "agent.action.dismissed"),
            actor="desk_user",
            subject_type="thread",
            subject_id=thread_id,
            payload={
                "action_id": action_id,
                "tool_name": action.get("tool_name") or action.get("type"),
                "model_selection": resolved,
                "model_selection_fallback": fallback_used,
            },
        )

        thread = session.query(AgentThread).filter(AgentThread.id == thread_id).one()
        new_msg = agent_service._persist_agent_result(
            session, thread, result, assets=[], page_context=None,
            model_selection=resolved,
        )
        if fallback_used and isinstance(new_msg.meta, dict):
            new_msg.meta = {**new_msg.meta, "model_selection_fallback": True}
            flag_modified(new_msg, "meta")
        return new_msg
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/test_api.py::test_hitl_resume_uses_originating_message_model -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py tests/test_api.py
git commit -m "feat(api): HITL resume uses originating message's model selection"
```

---

## Task 14: Update `.env.example`

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Edit `.env.example`**

Replace the agent-related block with:

```
OPEN_OTC_DATABASE_URL="sqlite+pysqlite:///./data/open_otc.sqlite3"
OPEN_OTC_ARTIFACT_DIR="./artifacts"
QUANTARK_PATH="/Users/fuxinyao/quant-ark"

# Channel/model catalog: see config/agent_channels.yaml
# Each channel's api_key_env names the env var that holds its API key.
ZENMUX_API_KEY=""
DEEPSEEK_API_KEY=""
# Optional override for the YAML path:
# AGENT_CHANNELS_FILE="./config/agent_channels.yaml"

AGENT_CHECKPOINT_DB_PATH="./agent_checkpoints.sqlite"

LANGSMITH_TRACING="true"
LANGSMITH_API_KEY=""
LANGSMITH_PROJECT="open-otc-trading"

VITE_API_TARGET="http://localhost:8000"
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "chore(env): drop legacy AGENT_PROVIDER vars; point to YAML"
```

---

## Task 15: Frontend types

**Files:**
- Modify: `frontend/src/types.ts`

- [ ] **Step 1: Append the new types to `frontend/src/types.ts`**

Add after the existing `ChatMessage` definition (around line 26):

```ts
export type AgentModelSelection = {
  channel: string;
  provider: string;
  model: string;
};

export type AgentModelOption = AgentModelSelection & {
  label: string;
  description?: string | null;
  is_default?: boolean;
  tags?: string[];
};

export type AgentChannel = {
  name: string;
  label: string;
  type: 'zenmux' | 'openai_compatible';
  healthy: boolean;
  models: AgentModelOption[];
};

export type AgentModelConfig = {
  enabled: boolean;
  active: AgentModelSelection;
  channels: AgentChannel[];
};
```

Also extend the `ChatMessage.meta` shape:

```ts
export type ChatMessage = {
  id: number;
  role: string;
  character?: string | null;
  content: string;
  meta?: {
    assets?: AgentAsset[];
    pending_actions?: AgentActionProposal[];
    confirmed_action?: AgentActionProposal & { result?: Record<string, any> };
    context_used?: PageContext | null;
    routed_character?: string;
    process_events?: ToolEvent[] | string[];
    agent_phase?: 'completed' | 'error' | 'awaiting_confirmation';
    model_selection?: AgentModelSelection;
    model_selection_fallback?: boolean;
    [key: string]: any;
  };
};
```

- [ ] **Step 2: Run typecheck**

Run: `cd frontend && npm run typecheck` (or whichever script the project uses; check `frontend/package.json` if uncertain).
Expected: no new errors. If `typecheck` doesn't exist, run `npx tsc --noEmit`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types.ts
git commit -m "types(frontend): add AgentModelSelection + AgentChannel + catalog"
```

---

## Task 16: `providerColors.ts` utility

**Files:**
- Create: `frontend/src/components/providerColors.ts`
- Test: `frontend/src/components/providerColors.test.ts`

- [ ] **Step 1: Write failing tests**

Create `frontend/src/components/providerColors.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { colorForProvider, PROVIDER_COLOR, PROVIDER_COLOR_DEFAULT } from './providerColors';

describe('colorForProvider', () => {
  it('returns the mapped color for known providers', () => {
    expect(colorForProvider('anthropic')).toBe(PROVIDER_COLOR.anthropic);
    expect(colorForProvider('openai')).toBe(PROVIDER_COLOR.openai);
    expect(colorForProvider('deepseek')).toBe(PROVIDER_COLOR.deepseek);
  });

  it('falls back to default for unknown providers', () => {
    expect(colorForProvider('mystery-vendor')).toBe(PROVIDER_COLOR_DEFAULT);
    expect(colorForProvider(undefined)).toBe(PROVIDER_COLOR_DEFAULT);
  });
});
```

- [ ] **Step 2: Run tests**

Run: `cd frontend && npm test -- providerColors.test`
Expected: fails with "module not found".

- [ ] **Step 3: Implement**

Create `frontend/src/components/providerColors.ts`:

```ts
export const PROVIDER_COLOR: Record<string, string> = {
  anthropic: '#e8743b',
  openai: '#19c37d',
  deepseek: '#4d7cff',
  meta: '#0866ff',
  mistral: '#ff7000',
};

export const PROVIDER_COLOR_DEFAULT = '#888888';

export function colorForProvider(provider: string | undefined): string {
  return PROVIDER_COLOR[provider ?? ''] ?? PROVIDER_COLOR_DEFAULT;
}
```

- [ ] **Step 4: Run tests**

Run: `cd frontend && npm test -- providerColors.test`
Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/providerColors.ts frontend/src/components/providerColors.test.ts
git commit -m "feat(frontend): providerColors helper for chips + picker dots"
```

---

## Task 17: `ModelPicker` component (rendering + selection)

**Files:**
- Create: `frontend/src/components/ModelPicker.tsx`
- Create: `frontend/src/components/ModelPicker.css`
- Create: `frontend/src/components/ModelPicker.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `frontend/src/components/ModelPicker.test.tsx`:

```tsx
import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { AgentChannel, AgentModelSelection } from '../types';
import { ModelPicker } from './ModelPicker';

const channels: AgentChannel[] = [
  {
    name: 'zenmux',
    label: 'Zenmux',
    type: 'zenmux',
    healthy: true,
    models: [
      { channel: 'zenmux', provider: 'anthropic', model: 'm1', label: 'Sonnet 4.6', tags: ['tool-use'] },
      { channel: 'zenmux', provider: 'openai', model: 'm2', label: 'GPT-5.4' },
    ],
  },
  {
    name: 'deepseek',
    label: 'DeepSeek',
    type: 'openai_compatible',
    healthy: false,
    models: [
      { channel: 'deepseek', provider: 'deepseek', model: 'd1', label: 'V4 Flash', tags: ['fast'] },
    ],
  },
];

const selected: AgentModelSelection = { channel: 'zenmux', provider: 'anthropic', model: 'm1' };

describe('ModelPicker', () => {
  it('renders the selected model label on the trigger button', () => {
    render(<ModelPicker channels={channels} selected={selected} onChange={() => {}} />);
    const trigger = screen.getByRole('button', { name: /Sonnet 4.6/i });
    expect(trigger).toBeInTheDocument();
  });

  it('opens panel and lists channels with their models when clicked', () => {
    render(<ModelPicker channels={channels} selected={selected} onChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Sonnet 4.6/i }));
    expect(screen.getByText(/ZENMUX/)).toBeInTheDocument();
    expect(screen.getByText('GPT-5.4')).toBeInTheDocument();
    expect(screen.getByText(/DEEPSEEK/)).toBeInTheDocument();
  });

  it('calls onChange when a model row is clicked', () => {
    const onChange = vi.fn();
    render(<ModelPicker channels={channels} selected={selected} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: /Sonnet 4.6/i }));
    fireEvent.click(screen.getByRole('option', { name: /GPT-5\.4/i }));
    expect(onChange).toHaveBeenCalledWith({
      channel: 'zenmux', provider: 'openai', model: 'm2',
    });
  });

  it('marks unhealthy channel rows non-interactive', () => {
    const onChange = vi.fn();
    render(<ModelPicker channels={channels} selected={selected} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: /Sonnet 4.6/i }));
    const row = screen.getByRole('option', { name: /V4 Flash/i });
    fireEvent.click(row);
    expect(onChange).not.toHaveBeenCalled();
    expect(row).toHaveAttribute('aria-disabled', 'true');
  });

  it('renders disabled trigger when no channels are healthy', () => {
    const allUnhealthy = channels.map(ch => ({ ...ch, healthy: false }));
    render(<ModelPicker channels={allUnhealthy} selected={null} onChange={() => {}} />);
    expect(screen.getByRole('button', { name: /Agent disabled/i })).toBeDisabled();
  });

  it('shows a refresh icon button when onRefresh is supplied', () => {
    const onRefresh = vi.fn();
    render(
      <ModelPicker channels={channels} selected={selected} onChange={() => {}} onRefresh={onRefresh} />
    );
    fireEvent.click(screen.getByRole('button', { name: /refresh/i }));
    expect(onRefresh).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run tests**

Run: `cd frontend && npm test -- ModelPicker.test`
Expected: failures (component doesn't exist).

- [ ] **Step 3: Implement the component**

Create `frontend/src/components/ModelPicker.css`:

```css
.wl-model-picker { position: relative; display: inline-flex; align-items: center; gap: 4px; }
.wl-model-picker__trigger {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px; font: inherit; font-size: 12px;
  background: #fafafa; border: 1px solid #999; border-radius: 4px;
  cursor: pointer;
}
.wl-model-picker__trigger[disabled] { cursor: not-allowed; opacity: 0.55; }
.wl-model-picker__refresh {
  width: 26px; height: 26px; display: inline-flex; align-items: center; justify-content: center;
  background: #fafafa; border: 1px solid #999; border-radius: 4px; cursor: pointer; font-size: 14px;
}
.wl-model-picker__panel {
  position: absolute; bottom: calc(100% + 4px); left: 0; min-width: 280px; z-index: 50;
  background: #fff; border: 1px solid #1a1a1a; border-radius: 4px;
  padding: 6px 0; box-shadow: 0 6px 20px rgba(0,0,0,0.15);
}
.wl-model-picker__group-label {
  display: block; padding: 4px 12px;
  font-size: 10px; font-weight: 600; letter-spacing: 0.06em;
  color: #888; text-transform: uppercase;
}
.wl-model-picker__group--unhealthy { opacity: 0.55; }
.wl-model-picker__row {
  display: flex; align-items: center; gap: 8px;
  padding: 5px 12px; cursor: pointer;
  font-size: 12px;
}
.wl-model-picker__row:hover { background: #f4f4f4; }
.wl-model-picker__row[aria-disabled="true"] { cursor: not-allowed; }
.wl-model-picker__row[aria-disabled="true"]:hover { background: transparent; }
.wl-model-picker__row.is-active { background: #eef; }
.wl-model-picker__dot {
  display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  flex-shrink: 0;
}
.wl-model-picker__row-label { flex: 1; }
.wl-model-picker__tag {
  font-size: 9px; padding: 1px 5px; background: #eee; border-radius: 9px;
  color: #444;
}
```

Create `frontend/src/components/ModelPicker.tsx`:

```tsx
import { useEffect, useId, useRef, useState } from 'react';
import type { AgentChannel, AgentModelOption, AgentModelSelection } from '../types';
import { colorForProvider } from './providerColors';
import './ModelPicker.css';

type Props = {
  channels: AgentChannel[];
  selected: AgentModelSelection | null;
  onChange: (s: AgentModelSelection) => void;
  onRefresh?: () => void;
  disabled?: boolean;
};

export function ModelPicker({ channels, selected, onChange, onRefresh, disabled }: Props) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const panelId = useId();

  const noHealthy = !channels.some((ch) => ch.healthy);
  const isDisabled = !!disabled || noHealthy;

  useEffect(() => {
    if (!open) return;
    const handlePointer = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handlePointer);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handlePointer);
      document.removeEventListener('keydown', handleKey);
    };
  }, [open]);

  const triggerLabel = isDisabled
    ? 'Agent disabled'
    : findSelectedLabel(channels, selected) ?? 'Pick a model';

  return (
    <div className="wl-model-picker" ref={containerRef}>
      <button
        type="button"
        className="wl-model-picker__trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => !isDisabled && setOpen((v) => !v)}
        disabled={isDisabled}
      >
        {selected && (
          <span
            className="wl-model-picker__dot"
            style={{ background: colorForProvider(selected.provider) }}
            aria-hidden="true"
          />
        )}
        <span>{triggerLabel}</span>
        <span aria-hidden="true">▾</span>
      </button>
      {onRefresh && (
        <button
          type="button"
          className="wl-model-picker__refresh"
          aria-label="refresh model catalog"
          onClick={onRefresh}
        >
          ↻
        </button>
      )}
      {open && (
        <div id={panelId} role="listbox" className="wl-model-picker__panel">
          {channels.map((ch) => (
            <div
              key={ch.name}
              className={ch.healthy ? '' : 'wl-model-picker__group--unhealthy'}
              role="group"
              aria-label={ch.label}
            >
              <span className="wl-model-picker__group-label">{ch.label.toUpperCase()}</span>
              {ch.models.map((md) => {
                const active =
                  selected != null
                  && selected.channel === md.channel
                  && selected.provider === md.provider
                  && selected.model === md.model;
                return (
                  <div
                    key={`${ch.name}:${md.model}`}
                    role="option"
                    aria-selected={active}
                    aria-disabled={!ch.healthy}
                    title={md.description ?? undefined}
                    className={`wl-model-picker__row${active ? ' is-active' : ''}`}
                    onClick={() => {
                      if (!ch.healthy) return;
                      onChange({
                        channel: md.channel,
                        provider: md.provider,
                        model: md.model,
                      });
                      setOpen(false);
                    }}
                  >
                    <span
                      className="wl-model-picker__dot"
                      style={{ background: colorForProvider(md.provider) }}
                      aria-hidden="true"
                    />
                    <span className="wl-model-picker__row-label">{md.label}</span>
                    {(md.tags ?? []).map((t) => (
                      <span key={t} className="wl-model-picker__tag">{t}</span>
                    ))}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function findSelectedLabel(
  channels: AgentChannel[],
  selected: AgentModelSelection | null,
): string | null {
  if (!selected) return null;
  for (const ch of channels) {
    if (ch.name !== selected.channel) continue;
    for (const md of ch.models) {
      if (md.provider === selected.provider && md.model === selected.model) {
        return md.label;
      }
    }
  }
  return selected.model;
}
```

- [ ] **Step 4: Run tests**

Run: `cd frontend && npm test -- ModelPicker.test`
Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ModelPicker.tsx frontend/src/components/ModelPicker.css frontend/src/components/ModelPicker.test.tsx
git commit -m "feat(frontend): ModelPicker component with channel-grouped dropdown"
```

---

## Task 18: Wire `ModelPicker` into `ChatComposer` + `AgentDesk`

**Files:**
- Modify: `frontend/src/components/ChatComposer.tsx`
- Modify: `frontend/src/routes/AgentDesk.tsx`
- Modify: `frontend/src/components/ChatComposer.test.tsx`

- [ ] **Step 1: Update `ChatComposer.tsx`**

Replace `frontend/src/components/ChatComposer.tsx`:

```tsx
import { useId, useState } from 'react';
import type { AgentChannel, AgentModelSelection } from '../types';
import { Button } from './Button';
import { ModelPicker } from './ModelPicker';
import './ChatComposer.css';

type Props = {
  onSend: (message: string) => void;
  sending: boolean;
  streaming?: boolean;
  channels?: AgentChannel[];
  selectedModel?: AgentModelSelection | null;
  onChangeModel?: (s: AgentModelSelection) => void;
  onRefreshModels?: () => void;
};

export function ChatComposer({
  onSend, sending, streaming,
  channels, selectedModel, onChangeModel, onRefreshModels,
}: Props) {
  const [text, setText] = useState('');
  const id = useId();

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    onSend(trimmed);
    setText('');
  };

  return (
    <div className="wl-composer">
      <label htmlFor={id} className="wl-composer__label">Ask anything</label>
      <textarea
        id={id}
        className="wl-composer__textarea"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        placeholder="Quote a snowball, run risk, generate a report…"
      />
      <div className="wl-composer__actions">
        {channels && onChangeModel && (
          <ModelPicker
            channels={channels}
            selected={selectedModel ?? null}
            onChange={onChangeModel}
            onRefresh={onRefreshModels}
          />
        )}
        <Button variant="primary" onClick={handleSend} disabled={sending || text.trim().length === 0}>
          {streaming ? 'Streaming…' : sending ? 'Sending…' : 'Send ▸'}
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Update `AgentDesk.tsx` props + propagation**

Add to the `Props` type in `frontend/src/routes/AgentDesk.tsx`:

```ts
type Props = {
  threads: Thread[];
  activeThreadId: number | null;
  sending: boolean;
  streaming?: boolean;
  streamingItem?: ChatMessageType | null;
  viewMode: ViewMode;
  channels?: AgentChannel[];
  selectedModel?: AgentModelSelection | null;
  onChangeModel?: (s: AgentModelSelection) => void;
  onRefreshModels?: () => void;
  onChangeViewMode: (mode: ViewMode) => void;
  onSelectThread: (id: number) => void;
  onNewThread: () => void;
  onSend: (message: string) => void;
  onConfirmAction: (messageId: number, actionId: string) => void;
  onDismissAction: (messageId: number, actionId: string) => void;
};
```

(Add the required imports: `import type { AgentChannel, AgentModelSelection, ... } from '../types';`.)

In the `ChatComposer` JSX (around line 141), pass the new props:

```tsx
<ChatComposer
  onSend={onSend}
  sending={sending}
  streaming={streaming}
  channels={channels}
  selectedModel={selectedModel}
  onChangeModel={onChangeModel}
  onRefreshModels={onRefreshModels}
/>
```

In the function signature, destructure the new props from `Props`.

- [ ] **Step 3: Run frontend tests**

Run: `cd frontend && npm test`
Expected: existing tests still pass; ModelPicker tests still pass.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ChatComposer.tsx frontend/src/routes/AgentDesk.tsx
git commit -m "feat(frontend): render ModelPicker inline in ChatComposer"
```

---

## Task 19: `AgentDeskLive` catalog fetch + state + send wiring + refresh

**Files:**
- Modify: `frontend/src/routes/AgentDesk.live.tsx`

- [ ] **Step 1: Update `AgentDeskLive.tsx`**

Edit `frontend/src/routes/AgentDesk.live.tsx`. Add to the imports:

```ts
import type { Thread, ChatMessage as ChatMessageType, ToolEvent, AgentModelConfig, AgentModelSelection } from '../types';
```

Inside `AgentDeskLive`, add catalog state and fetch:

```ts
  const [modelConfig, setModelConfig] = useState<AgentModelConfig | null>(null);
  const [selectedModel, setSelectedModel] = useState<AgentModelSelection | null>(null);

  const fetchCatalog = useCallback(async () => {
    const cfg = await api<AgentModelConfig>('/api/agent/models');
    setModelConfig(cfg);
    setSelectedModel((current) => current ?? cfg.active);
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchCatalog().catch((e) => {
      if (!cancelled) setError(e instanceof Error ? e.message : String(e));
    });
    return () => { cancelled = true; };
  }, [fetchCatalog]);

  const handleRefreshModels = useCallback(async () => {
    try {
      await api('/api/agent/channels/reload', { method: 'POST' });
      await fetchCatalog();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [fetchCatalog]);
```

Update the SSE body in `handleSend`:

```ts
      const response = await fetch(`/api/chat/threads/${threadId}/messages/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: message,
          character: 'auto',
          model: selectedModel,
        }),
      });
```

Pass new props through to `<AgentDesk>`:

```tsx
return (
  <AgentDesk
    threads={threads}
    activeThreadId={activeId}
    sending={sending}
    streaming={streaming}
    streamingItem={streamingItem}
    viewMode={viewMode}
    channels={modelConfig?.channels ?? []}
    selectedModel={selectedModel}
    onChangeModel={setSelectedModel}
    onRefreshModels={handleRefreshModels}
    onChangeViewMode={setViewMode}
    onSelectThread={setActiveId}
    onNewThread={handleNewThread}
    onSend={handleSend}
    onConfirmAction={handleConfirmAction}
    onDismissAction={handleDismissAction}
  />
);
```

- [ ] **Step 2: Run tests**

Run: `cd frontend && npm test -- AgentDesk`
Expected: existing AgentDesk tests pass (may need to update `AgentDesk.test.tsx` to satisfy the new optional props if they break; the props are optional so existing fixtures should keep working).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/AgentDesk.live.tsx
git commit -m "feat(frontend): fetch catalog, wire selected model into send body + refresh"
```

---

## Task 20: `ChatBubble` model chip

**Files:**
- Modify: `frontend/src/components/ChatBubble.tsx`
- Modify: `frontend/src/components/ChatBubble.css`
- Modify: `frontend/src/components/ChatBubble.test.tsx`

- [ ] **Step 1: Write failing tests**

Append to `frontend/src/components/ChatBubble.test.tsx`:

```tsx
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ChatBubble } from './ChatBubble';

describe('ChatBubble model chip', () => {
  it('shows model chip for assistant messages with model_selection', () => {
    render(
      <ChatBubble
        message={{
          id: 1,
          role: 'assistant',
          character: 'trader',
          content: 'hi',
          meta: {
            model_selection: { channel: 'zenmux', provider: 'anthropic', model: 'anthropic/claude-sonnet-4-6' },
          },
        }}
        viewMode="detailed"
        onConfirmAction={() => {}}
        onDismissAction={() => {}}
      />
    );
    expect(screen.getByText(/anthropic\/claude-sonnet-4-6/)).toBeInTheDocument();
  });

  it('renders fallback note when model_selection_fallback is true', () => {
    render(
      <ChatBubble
        message={{
          id: 2,
          role: 'assistant',
          character: 'trader',
          content: 'hi',
          meta: {
            model_selection: { channel: 'zenmux', provider: 'anthropic', model: 'anthropic/claude-sonnet-4-6' },
            model_selection_fallback: true,
          },
        }}
        viewMode="compact"
        onConfirmAction={() => {}}
        onDismissAction={() => {}}
      />
    );
    expect(screen.getByText(/fell back to default/i)).toBeInTheDocument();
  });

  it('omits chip on user messages', () => {
    render(
      <ChatBubble
        message={{ id: 3, role: 'user', content: 'hello' }}
        viewMode="detailed"
        onConfirmAction={() => {}}
        onDismissAction={() => {}}
      />
    );
    expect(screen.queryByTestId('chat-bubble-model-chip')).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npm test -- ChatBubble`
Expected: the model-chip tests fail.

- [ ] **Step 3: Add the chip JSX**

In `frontend/src/components/ChatBubble.tsx`, add the import:

```ts
import { colorForProvider } from './providerColors';
```

Inside the assistant branch of `ChatBubble` (after the body block, before `pendingActions.map` — around line 84), insert:

```tsx
        {variant === 'assistant' && meta.model_selection && (
          <div className="wl-chat-bubble__model-chip" data-testid="chat-bubble-model-chip">
            <span
              className="wl-chat-bubble__model-dot"
              style={{ background: colorForProvider(meta.model_selection.provider) }}
              aria-hidden="true"
            />
            <span className="wl-chat-bubble__model-label">
              {meta.model_selection.model} · {meta.model_selection.channel}
            </span>
            {meta.model_selection_fallback && (
              <span className="wl-chat-bubble__model-fallback">
                (fell back to default)
              </span>
            )}
          </div>
        )}
```

- [ ] **Step 4: Append CSS**

In `frontend/src/components/ChatBubble.css`:

```css
.wl-chat-bubble__model-chip {
  display: inline-flex; align-items: center; gap: 6px;
  margin-top: 6px;
  font-size: 10px; color: #777;
}
.wl-chat-bubble__model-dot {
  width: 6px; height: 6px; border-radius: 50%;
}
.wl-chat-bubble__model-fallback {
  font-style: italic;
}
```

- [ ] **Step 5: Run tests**

Run: `cd frontend && npm test -- ChatBubble`
Expected: 3 chip tests pass + existing bubble tests pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ChatBubble.tsx frontend/src/components/ChatBubble.css frontend/src/components/ChatBubble.test.tsx
git commit -m "feat(frontend): per-message model chip on assistant bubbles"
```

---

## Task 21: Manual end-to-end smoke test

This task has no code; it produces no commits. Run through the checks below and record outcomes in your engineer log so we know the feature works against real channels before declaring done.

- [ ] **Step 1: Make sure `.env` has both keys**

Confirm `ZENMUX_API_KEY` and `DEEPSEEK_API_KEY` are set in `.env` (use real keys for the smoke test). Restart the backend so the registry loads with both healthy.

- [ ] **Step 2: Verify catalog endpoint**

```bash
curl -s http://localhost:8000/api/agent/models | jq
```
Expected: `enabled: true`, both `zenmux` and `deepseek` channels listed with `healthy: true`, all configured models present.

- [ ] **Step 3: Send via Zenmux/Anthropic**

In the Agent Desk UI, pick "Claude Sonnet 4.6", send "calculate 2+2 and explain". Watch the SSE stream — confirm the assistant bubble shows the orange (anthropic) dot + chip "anthropic/claude-sonnet-4-6 · zenmux".

- [ ] **Step 4: Send via Zenmux/OpenAI**

Pick "GPT-5.4", send a similar message. Confirm green (openai) dot in the chip; backend logs show the request hit `https://zenmux.ai/api/v1`.

- [ ] **Step 5: Send via DeepSeek**

Pick "DeepSeek V4 Flash", send a message. Confirm blue (deepseek) dot in the chip; backend logs show the request hit `https://api.deepseek.com`.

- [ ] **Step 6: Hot reload after key rotation**

Edit `.env` and clear `DEEPSEEK_API_KEY`. Click the ↻ button next to the picker. The DeepSeek section in the dropdown should now appear dimmed (`healthy: false`); clicking a DeepSeek model is non-interactive. Restore the key, click ↻ again, and confirm DeepSeek is healthy again — without restarting the backend.

- [ ] **Step 7: HITL resume keeps the originating model**

Send a message that triggers a tool requiring confirmation (e.g., one of the write-action tools). Confirm with the picker still on a non-default model. After clicking "Confirm", check the resulting assistant message's chip — it should match the model that produced the interrupt, not whatever the picker shows now. Inspect the audit log to confirm `model_selection` is recorded.

- [ ] **Step 8: Compose final report**

Document the outcomes in a single sentence each (pass / fail / notes). If any step fails, file a follow-up issue rather than blocking — this plan is feature-complete once steps 1–6 pass; HITL behavior in step 7 is a regression check on existing flow + new persistence.

---

## Summary

- 21 tasks, each independently committable.
- Backend tasks (1–14) ship the registry, factory, schemas, endpoints, and config cleanup. Each is unit-tested before being merged into the dispatch path.
- Frontend tasks (15–20) add types, the picker component, send-body wiring, and the bubble chip.
- Task 21 is a manual smoke run against real keys.
- Pre-flight stash discards prior in-flight work; the new tasks build the channel-aware shape from the post-feature design.

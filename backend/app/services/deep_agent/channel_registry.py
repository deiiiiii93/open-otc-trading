"""Channel + model registry loaded from config/agent_channels.yaml.

The registry is the single source of truth for which (channel, provider, model)
combinations are dispatchable. It replaces the legacy Settings.zenmux_* and
agent_provider/agent_model_* fields. See
docs/superpowers/specs/2026-05-09-multi-channel-model-selection-design.md.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml


_REPO_ROOT = Path(__file__).resolve().parents[4]
_ENV_FILE = _REPO_ROOT / ".env"


@dataclass(frozen=True)
class ModelDescriptor:
    id: str
    provider: str
    label: str
    description: str | None = None
    tags: tuple[str, ...] = ()
    # Wire protocol the model speaks, decoupled from ``provider`` (the ZenMux
    # gateway routing label). Empty means "same as provider". Some third-party
    # vendors routed through ZenMux (e.g. minimax) emit tool calls in the
    # Anthropic tool-use format, which the OpenAI-compatible gateway does NOT
    # translate — they must be dispatched via the Anthropic endpoint even though
    # their ``provider`` stays "openai" so ``find_model`` keeps matching them.
    protocol: str = ""

    @property
    def wire_protocol(self) -> str:
        """Effective dispatch protocol: explicit ``protocol`` else ``provider``."""
        return self.protocol or self.provider


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

    def select_by_tag(self, tag: str) -> dict[str, str] | None:
        """Return the first HEALTHY channel's first model bearing ``tag`` as a
        ``{channel, provider, model}`` selection, or ``None`` when no healthy
        channel declares a model with that tag.

        This is the tier-selection seam behind ``MemoryConfig.extractor_model``:
        it lets the extractor route to a cheap "fast"-tagged model instead of
        the agent's default. Declaration order (channels, then models) is the
        deterministic tie-break. Unhealthy channels are skipped so the returned
        selection always builds — ``None`` signals the caller to fall back to
        ``default_selection()``.
        """
        for ch in self.channels:
            if not ch.healthy:
                continue
            for md in ch.models:
                if tag in md.tags:
                    return {"channel": ch.name, "provider": md.provider, "model": md.id}
        return None


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
            load_dotenv(_ENV_FILE, override=True)
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
    protocol = m.get("protocol")
    if protocol is not None:
        if not isinstance(protocol, str):
            raise ValueError(
                f"channel {channel_name!r}: model {model_id!r}: protocol must be a string or null"
            )
        if channel_type == "zenmux" and protocol not in {"anthropic", "openai"}:
            raise ValueError(
                f"channel {channel_name!r}: model {model_id!r}: "
                f"protocol must be 'anthropic' or 'openai' on zenmux channels, got {protocol!r}"
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
        protocol=protocol or "",
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

    If the declared channel is healthy, its model id must be explicit and valid;
    otherwise configuration typos would silently select the wrong model. When
    the declared default is absent or the declared channel is unhealthy, falls
    back to the first model of the first healthy channel. If no channel is
    healthy, falls back to the first model of the first channel so the registry
    still has a default pointer; callers detect the disabled state separately.
    """
    if isinstance(declared, dict):
        ch_name = declared.get("channel")
        model_id = declared.get("model")
        if isinstance(ch_name, str) and isinstance(model_id, str):
            found_declared_channel = False
            for ch in channels:
                if ch.name != ch_name:
                    continue
                found_declared_channel = True
                if not ch.healthy:
                    break
                for md in ch.models:
                    if md.id == model_id:
                        return (ch.name, md.provider, md.id)
                raise ValueError(
                    f"default model {model_id!r} is not declared on healthy "
                    f"channel {ch_name!r}"
                )
            if not found_declared_channel:
                raise ValueError(f"default channel {ch_name!r} is not declared")

    for ch in channels:
        if ch.healthy and ch.models:
            return (ch.name, ch.models[0].provider, ch.models[0].id)
    first = channels[0]
    return (first.name, first.models[0].provider, first.models[0].id)


_LOCK = threading.RLock()
_REGISTRY: ChannelRegistry | None = None
_OVERRIDE: ChannelRegistry | None = None

DEFAULT_YAML_PATH = Path("./config/agent_channels.yaml")


def _yaml_path() -> Path:
    raw = os.environ.get("AGENT_CHANNELS_FILE")
    if raw:
        return Path(raw)
    try:
        from ...config import get_settings
        return get_settings().agent_channels_file
    except Exception:
        return DEFAULT_YAML_PATH


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

    The file read happens INSIDE ``_LOCK`` so a reload cannot install a stale
    snapshot over a concurrent writer's fresh registry (the writer holds the
    same lock across its read-modify-replace-swap). If parsing/validation fails,
    the old registry remains live and the error propagates to the caller.
    """
    with _LOCK:
        new_registry = load_from_path(_yaml_path(), force_reread_dotenv=force_reread_dotenv)
        global _REGISTRY
        _REGISTRY = new_registry
        return new_registry


def commit_registry(new_registry: ChannelRegistry) -> None:
    """Swap the live registry under ``_LOCK``.

    Used by ``channel_registry_writer`` after it atomically replaces the YAML
    file, so the on-disk file and the in-memory registry move together inside
    one critical section.
    """
    with _LOCK:
        global _REGISTRY
        _REGISTRY = new_registry


def configure_registry(registry: ChannelRegistry | None) -> None:
    """Install (or clear) a process-wide override for tests.

    Passing ``None`` both clears the override and resets the cached registry,
    so a subsequent ``get_registry()`` reloads from disk.
    """
    global _OVERRIDE, _REGISTRY
    _OVERRIDE = registry
    # Reset the cached registry as well: when installing an override, callers
    # don't want the cached one to leak through reload(); when clearing the
    # override, callers want the next get_registry() to re-read the YAML
    # rather than serve a stale cache from a previous test.
    _REGISTRY = None

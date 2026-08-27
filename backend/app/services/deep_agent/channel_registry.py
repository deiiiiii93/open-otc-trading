"""Channel + model registry loaded from config/agent_channels.yaml.

The registry is the single source of truth for which (channel, provider, model)
combinations are dispatchable. It replaces the legacy Settings.zenmux_* and
agent_provider/agent_model_* fields. See
docs/superpowers/specs/2026-05-09-multi-channel-model-selection-design.md.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from app.config import dotenv_path


logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class ModelDescriptor:
    """One dispatchable model, described on three ORTHOGONAL axes.

    ``id``        the model id as its owner names it, with NO ``:upstream``
                  suffix — ``deepseek/deepseek-v4-flash``.
    ``provider``  on a **zenmux** channel: the ZenMux UPSTREAM PROVIDER, i.e.
                  whose infrastructure actually serves the request
                  (``deepseek``, ``bigmodel``, ``google-vertex``). ZenMux serves
                  one id from several upstreams and picks one per request unless
                  it is pinned, so this is a required part of the route, not a
                  label. On an ``openai_compatible`` channel there is only one
                  upstream, so it names the SDK instead (``deepseek`` ->
                  ChatDeepSeek).
    ``protocol``  the wire format dispatched on: ``openai_chat`` | ``anthropic``
                  | ``openai_responses``.

    Until 2026-08-25 ``provider`` held the ZenMux *gateway routing label*
    ("anthropic"/"openai") and the upstream lived in the id as a ``:suffix``, so
    a single row could read ``qwen/qwen3.7-max:alibaba`` / ``openai`` /
    ``anthropic`` — three vendor-shaped words meaning three different things.
    That label was dispatch-dead (routing has always keyed on the protocol) and
    redundant as a lookup key (``_build_channel`` already forbids duplicate ids
    within a channel), so the field was freed to mean the upstream. YAML in the
    old shape still loads — see ``_build_model``.

    See ``config/agent_channels.example.yml`` for the annotated template.
    """

    id: str
    provider: str
    label: str
    description: str | None = None
    tags: tuple[str, ...] = ()
    # Defaults to the documented default protocol rather than "", so every reader
    # sees a valid value. ``_build_model`` always sets it explicitly; this default
    # only covers descriptors built by hand in tests and fixtures.
    protocol: str = "openai_chat"
    # The id actually sent to the gateway: ``<id>:<provider>`` on a zenmux
    # channel (the upstream pin), plain ``id`` elsewhere. Always populated by
    # ``_build_model``; read it through ``wire_id`` so a descriptor built by hand
    # in a test or fixture still resolves.
    dispatch_id: str = ""

    @property
    def wire_id(self) -> str:
        """Model id as sent to the provider, and the key for route-scoped data.

        Route-scoped means the measured effort ladders in
        ``config/model_reasoning.json``: a ladder is a property of
        (channel, upstream, model), not of the model alone, so it must be keyed
        on the pinned id.
        """
        return self.dispatch_id or self.id


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
        """Resolve a ``{channel, provider, model}`` selection to its descriptor.

        **Matches on the model id alone.** ``_build_channel`` already forbids
        duplicate ids within a channel, so the id IS the unique key and
        ``provider`` never added anything to it. That matters now because
        ``provider`` changed meaning on 2026-08-25 (gateway routing label ->
        upstream provider) and 13k+ persisted ``AgentMessage.meta`` selections —
        replayed verbatim by async resume — still carry the old value. Ignoring
        it is what keeps every one of those rows resolvable. The parameter stays
        in the signature because those callers pass it.

        Either spelling of the id resolves: bare (``deepseek/deepseek-v4-flash``)
        or upstream-pinned (``deepseek/deepseek-v4-flash:deepseek``).

        A selection pinned to an upstream the registry no longer declares
        resolves to the configured one, loudly. Replaying an old thread on an
        upstream we have since abandoned is the worse outcome — ``:alibaba`` is
        why the pin exists at all.
        """
        for ch in self.channels:
            if ch.name != channel:
                continue
            for md in ch.models:
                if model == md.wire_id:
                    return ch, md
            base, sep, upstream = model.partition(":")
            candidates = [md for md in ch.models if md.id == base]
            if len(candidates) == 1:
                md = candidates[0]
                if sep and upstream != md.provider:
                    logger.warning(
                        "selection names upstream %r for %r, which is not "
                        "configured; dispatching on %r instead",
                        upstream, base, md.wire_id,
                    )
                return ch, md
            if len(candidates) > 1:
                raise KeyError(
                    f"ambiguous selection: {base!r} is declared on channel "
                    f"{channel!r} for upstreams "
                    f"{sorted(md.provider for md in candidates)}; name one "
                    f"explicitly as '<id>:<upstream>'"
                )
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

    def resolve_default_model(
        self, channel_name: str, model_id: str
    ) -> ModelDescriptor | None:
        """Find `model_id` on `channel_name`, accepting either id spelling."""
        for ch in self.channels:
            if ch.name == channel_name:
                return _match_model(ch.models, model_id)
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
        # `dotenv_path()` may return None, meaning "dotenv loading is disabled"
        # (the test suite sets it so). Honour that: `load_dotenv(None)` would
        # fall back to searching for a .env from the CWD, and `override=True`
        # writes straight into os.environ — so an unguarded call here does not
        # just read a stray .env, it republishes it over every value the suite
        # pinned, for every test that runs afterwards.
        env_file = dotenv_path()
        if env_file is not None:
            try:
                from dotenv import load_dotenv  # lazily imported to keep tests fast
                load_dotenv(env_file, override=True)
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
        # Keyed on the DISPATCH id, not the bare id, so the same model may be
        # declared on two upstreams as two distinct routes — the shape the
        # `:deepseek` vs `:alibaba` investigation needed. ``find_model`` refuses
        # a bare id that is ambiguous between them rather than picking one.
        if md.wire_id in seen_ids:
            raise ValueError(f"channel {name!r}: duplicate model id {md.wire_id!r}")
        seen_ids.add(md.wire_id)
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


# The wire formats a model may declare. Renamed 2026-08-25 from
# ("openai", "anthropic", "responses"): a bare "openai" hid WHICH of OpenAI's two
# APIs was meant, and that is exactly the distinction the deepseek
# malformed-tool-call investigation turned on — the same model, same effort, was
# unusable over chat completions and clean over the Responses API. The old
# spellings still load, via _PROTOCOL_ALIASES.
_ZENMUX_PROTOCOLS = ("openai_chat", "anthropic", "openai_responses")
_PROTOCOL_ALIASES = {
    "openai": "openai_chat",
    "responses": "openai_responses",
}

# The only two values ``provider`` could hold before 2026-08-25, when it meant
# the ZenMux gateway routing label rather than the upstream provider. Used ONLY
# to interpret a legacy row; the discriminator for "is this row legacy" is the
# ``:upstream`` suffix on the id, never this list — "anthropic" is also a
# perfectly good NEW-style upstream (Anthropic's own metal).
_LEGACY_GATEWAY_LABELS = ("anthropic", "openai")

# Upstream slugs are vendor-chosen and there is NO enumeration endpoint on
# ZenMux (302/404), so this cannot be an allowlist — only a shape check, to
# catch a stray path or whitespace rather than an unknown-but-real provider.
_UPSTREAM_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _one_of(values: tuple[str, ...]) -> str:
    return " or ".join(repr(v) for v in values)


def _match_model(
    models: tuple[ModelDescriptor, ...], model_id: str
) -> ModelDescriptor | None:
    """Match `model_id` against a channel's models, accepting either spelling.

    Exact dispatch id first (``deepseek/deepseek-v4-flash:deepseek``), then the
    bare id (``deepseek/deepseek-v4-flash``) when exactly one model carries it.
    Returns None when unknown or ambiguous — ambiguity is only possible when the
    same model is declared on two upstreams, and silently picking one there would
    be the routing lottery this whole scheme exists to prevent.
    """
    for md in models:
        if model_id == md.wire_id:
            return md
    candidates = [md for md in models if md.id == model_id.partition(":")[0]]
    return candidates[0] if len(candidates) == 1 else None


def canonical_protocol(value: str | None) -> str:
    """Normalise a declared protocol, mapping the pre-2026-08-25 spellings."""
    text = (value or "").strip().lower()
    return _PROTOCOL_ALIASES.get(text, text)


def _build_model(channel_name: str, channel_type: str, m: dict) -> ModelDescriptor:
    """Parse one model entry into a descriptor, accepting both YAML schemas.

    CURRENT schema — the three axes are independent::

        - id: deepseek/deepseek-v4-flash     # bare model id
          provider: deepseek                 # ZenMux upstream provider
          protocol: openai_chat              # wire format

    LEGACY schema (pre-2026-08-25) — the upstream rode in the id and ``provider``
    held the gateway routing label::

        - id: deepseek/deepseek-v4-flash:deepseek
          provider: openai                   # gateway label, not the upstream

    The discriminator is the ``:upstream`` suffix on the id, NOT the value of
    ``provider``: "anthropic" is both a legacy gateway label and a real
    upstream, so reading the row's shape is the only unambiguous test.

    A legacy row is translated in place — the suffix becomes ``provider``, the
    old ``provider`` becomes the protocol default — so an existing per-machine
    ``config/agent_channels.yaml`` keeps working untouched.
    """
    declared_id = _required_str(m, "id")
    provider = _required_str(m, "provider")
    label = _required_str(m, "label")

    raw_protocol = m.get("protocol")
    if raw_protocol is not None and not isinstance(raw_protocol, str):
        raise ValueError(
            f"channel {channel_name!r}: model {declared_id!r}: protocol must be a string or null"
        )
    protocol = canonical_protocol(raw_protocol)

    model_id = declared_id
    dispatch_id = declared_id

    if channel_type == "zenmux":
        base, sep, upstream = declared_id.partition(":")
        if sep:
            # Legacy row: recover the upstream from the id, and fall back to the
            # old gateway label for the protocol when none was declared.
            model_id = base
            if not upstream:
                raise ValueError(
                    f"channel {channel_name!r}: model {declared_id!r}: "
                    "empty upstream after ':'"
                )
            protocol = protocol or canonical_protocol(provider)
            provider = upstream
            dispatch_id = declared_id
        elif not protocol and provider in _LEGACY_GATEWAY_LABELS:
            # Legacy row from before upstream pinning existed at all. Leave it
            # UNPINNED rather than guessing: composing `<id>:openai` would mint a
            # pin to an upstream nobody chose, which is worse than the lottery it
            # would be pretending to fix. ``provider`` keeps the gateway label
            # here — the one case where the field does not name an upstream.
            logger.warning(
                "channel %r: model %r declares no protocol and provider=%r "
                "(the pre-2026-08-25 gateway label). Routing it UNPINNED across "
                "ZenMux upstreams. Declare provider=<upstream> and "
                "protocol=%s to pin it.",
                channel_name, declared_id, provider, _one_of(_ZENMUX_PROTOCOLS),
            )
            protocol = canonical_protocol(provider)
            dispatch_id = declared_id
        else:
            if not protocol:
                raise ValueError(
                    f"channel {channel_name!r}: model {declared_id!r}: "
                    f"protocol is required on zenmux channels; expected "
                    f"{_one_of(_ZENMUX_PROTOCOLS)}"
                )
            if not _UPSTREAM_RE.match(provider):
                raise ValueError(
                    f"channel {channel_name!r}: model {declared_id!r}: provider "
                    f"must be a ZenMux upstream slug (e.g. 'deepseek', "
                    f"'google-vertex'), got {provider!r}"
                )
            dispatch_id = f"{model_id}:{provider}"

        if protocol not in _ZENMUX_PROTOCOLS:
            raise ValueError(
                f"channel {channel_name!r}: model {declared_id!r}: "
                f"protocol must be {_one_of(_ZENMUX_PROTOCOLS)} on zenmux channels, "
                f"got {raw_protocol!r}"
            )
    else:
        # A vendor's own API has exactly one upstream, so there is nothing to
        # pin and nothing to compose; ``provider`` selects the SDK instead.
        protocol = protocol or "openai_chat"

    description = m.get("description")
    if description is not None and not isinstance(description, str):
        raise ValueError(f"channel {channel_name!r}: model {declared_id!r}: description must be a string or null")
    raw_tags = m.get("tags") or []
    if not isinstance(raw_tags, list) or not all(isinstance(t, str) for t in raw_tags):
        raise ValueError(f"channel {channel_name!r}: model {declared_id!r}: tags must be a list[str]")
    return ModelDescriptor(
        id=model_id,
        provider=provider,
        label=label,
        description=description,
        tags=tuple(raw_tags),
        protocol=protocol,
        dispatch_id=dispatch_id,
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
                md = _match_model(ch.models, model_id)
                if md is not None:
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

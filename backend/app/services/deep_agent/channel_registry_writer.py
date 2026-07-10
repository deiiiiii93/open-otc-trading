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

import io
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


def _channel_index(doc: CommentedMap, name: str) -> int:
    for i, ch in enumerate(doc.get("channels") or []):
        if ch.get("name") == name:
            return i
    return -1


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
    the inner ``with cr._LOCK`` is re-entrant (RLock) so this is also safe if a
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


# --- model mutations --------------------------------------------------------

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


# --- channel mutations ------------------------------------------------------

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


# --- dry-run validation -----------------------------------------------------

def validate_draft(kind: str, payload: dict, *, path: Path | None = None) -> None:
    """Dry-run: apply the mutation against an IN-MEMORY copy and validate,
    never writing. Raises RegistryValidationError / RegistryConflictError on bad
    input; returns None when the draft would be accepted."""
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
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, dir=str(p.parent)) as fh:
        fh.write(tmp_text.getvalue())
        tmp_name = fh.name
    try:
        cr.load_from_path(tmp_name, force_reread_dotenv=False)
    except ValueError as exc:
        raise RegistryValidationError(str(exc)) from exc
    finally:
        os.unlink(tmp_name)


def _apply_draft(kind: str, payload: dict, doc: CommentedMap) -> None:
    # payload comes straight from the client (dry-run), so treat missing/wrong
    # keys as a validation error, never let a KeyError bubble to a 500.
    try:
        if kind == "add_channel":
            doc.setdefault("channels", CommentedSeq()).append(_channel_map(payload))
        elif kind == "add_model":
            channel = payload.get("channel")
            model = payload.get("model")
            if not channel or not isinstance(model, dict):
                raise RegistryValidationError(
                    "add_model draft requires {channel, model} keys"
                )
            _find_channel(doc, channel).setdefault("models", CommentedSeq()).append(
                _model_map(model)
            )
        else:
            raise RegistryValidationError(f"unknown draft kind {kind!r}")
    except KeyError as exc:
        raise RegistryValidationError(f"draft missing required field: {exc}") from exc

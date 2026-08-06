"""Registry mapping a block key to its deterministic producer.

The registry is the single source of numbers in a report. A template may only
name keys registered here, and the template validator checks that at save time
via ``require_block``. At render time ``resolve_block`` never raises: an unknown
key, a missing required parameter, or an exploding producer all become an
``unavailable`` BlockResult, because one broken block must not take down a
whole report.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .contracts import BlockContext, BlockResult, BlockShape

BlockProducer = Callable[[BlockContext], BlockResult]


class UnknownBlockError(KeyError):
    """Raised by ``require_block`` when a key is not registered.

    Used by the template validator so an unresolvable ``blocks[].key`` fails at
    save time (HTTP 422) rather than rendering as a silent gap.
    """


@dataclass(frozen=True)
class BlockSpec:
    key: str
    title: str
    shape: BlockShape
    requires: tuple[str, ...]
    domain: str
    description: str
    produce: BlockProducer

    def catalog_entry(self) -> dict[str, Any]:
        """Agent-facing description, returned by the list_report_blocks tool."""
        return {
            "key": self.key,
            "title": self.title,
            "shape": self.shape.value,
            "requires": list(self.requires),
            "domain": self.domain,
            "description": self.description,
        }


_REGISTRY: dict[str, BlockSpec] = {}


def report_block(
    *,
    key: str,
    title: str,
    shape: BlockShape,
    requires: tuple[str, ...] = (),
    domain: str = "",
    description: str = "",
) -> Callable[[BlockProducer], BlockProducer]:
    """Register a block producer under ``key``."""

    def decorate(func: BlockProducer) -> BlockProducer:
        if key in _REGISTRY:
            raise ValueError(f"block key already registered: {key!r}")
        _REGISTRY[key] = BlockSpec(
            key=key,
            title=title,
            shape=shape,
            requires=tuple(requires),
            domain=domain,
            description=description or (func.__doc__ or "").strip().split("\n")[0],
            produce=func,
        )
        return func

    return decorate


def get_block(key: str) -> BlockSpec | None:
    return _REGISTRY.get(key)


def require_block(key: str) -> BlockSpec:
    """Return the spec for ``key`` or raise ``UnknownBlockError``."""
    spec = _REGISTRY.get(key)
    if spec is None:
        raise UnknownBlockError(
            f"unknown block key: {key!r}; known keys: {sorted(_REGISTRY)}"
        )
    return spec


def list_blocks() -> list[BlockSpec]:
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def _missing_requirements(spec: BlockSpec, ctx: BlockContext) -> list[str]:
    missing: list[str] = []
    for name in spec.requires:
        value = getattr(ctx, name, None)
        if value is None:
            value = ctx.params.get(name)
        if value is None:
            missing.append(name)
    return missing


def resolve_block(key: str, ctx: BlockContext) -> BlockResult:
    """Resolve one block. Never raises."""
    spec = _REGISTRY.get(key)
    if spec is None:
        return BlockResult.unavailable(f"no producer registered for block {key!r}")

    missing = _missing_requirements(spec, ctx)
    if missing:
        return BlockResult.unavailable(
            f"block {key!r} requires {', '.join(missing)}, which "
            f"{'is' if len(missing) == 1 else 'are'} not available for this report"
        )

    try:
        return spec.produce(ctx)
    except Exception as exc:  # noqa: BLE001 - one bad block must not kill a report
        return BlockResult.unavailable(f"producer for {key!r} failed: {exc}")


__all__ = [
    "BlockSpec",
    "UnknownBlockError",
    "get_block",
    "list_blocks",
    "report_block",
    "require_block",
    "resolve_block",
]

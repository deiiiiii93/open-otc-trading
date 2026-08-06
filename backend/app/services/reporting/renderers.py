"""Which renderer accepts which block shape.

This map is what lets a template save reject `render: waterfall` on a scalar
block at HTTP 422 instead of producing a broken panel at render time. The
frontend has a component per key here; adding a renderer means adding both.
"""
from __future__ import annotations

from .contracts import BlockShape

RENDERER_SHAPES: dict[str, frozenset[BlockShape]] = {
    "metric_row": frozenset({BlockShape.SCALARS}),
    "delta_metric_row": frozenset({BlockShape.SCALARS_WITH_PRIOR}),
    "table": frozenset({BlockShape.ROWS}),
    "bar_chart": frozenset({BlockShape.SERIES}),
    "line_chart": frozenset({BlockShape.SERIES}),
    "callout": frozenset({BlockShape.ITEMS}),
    "greeks_table": frozenset({BlockShape.POSITION_GREEKS}),
    "waterfall": frozenset({BlockShape.WATERFALL}),
}


def renderer_accepts(render: str, shape: BlockShape) -> bool:
    return shape in RENDERER_SHAPES.get(render, frozenset())


def known_renderers() -> list[str]:
    return sorted(RENDERER_SHAPES)


__all__ = ["RENDERER_SHAPES", "renderer_accepts", "known_renderers"]

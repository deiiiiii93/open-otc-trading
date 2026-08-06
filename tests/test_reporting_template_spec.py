import pytest

from app.services.reporting.contracts import BlockShape
from app.services.reporting.renderers import (
    RENDERER_SHAPES,
    known_renderers,
    renderer_accepts,
)


def test_every_block_shape_has_at_least_one_renderer():
    covered = {shape for shapes in RENDERER_SHAPES.values() for shape in shapes}
    assert covered == set(BlockShape)


def test_renderer_accepts_only_its_declared_shapes():
    assert renderer_accepts("waterfall", BlockShape.WATERFALL) is True
    assert renderer_accepts("waterfall", BlockShape.SCALARS) is False
    assert renderer_accepts("metric_row", BlockShape.SCALARS) is True
    assert renderer_accepts("delta_metric_row", BlockShape.SCALARS_WITH_PRIOR) is True
    assert renderer_accepts("nope", BlockShape.SCALARS) is False
    assert set(known_renderers()) == set(RENDERER_SHAPES)

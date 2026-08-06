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


from app.services.reporting import blocks  # noqa: F401,E402 - registers producers
from app.services.reporting.template_spec import (  # noqa: E402
    TemplateSpecError,
    parse_spec,
    validate_spec,
)

GOOD = """
meta:
  slug: demo-daily
  title: Demo Daily
  persona: risk_manager
  description: A demo template.
  params:
    - { name: portfolio_id, type: portfolio, required: true }
sections:
  - id: headline
    title: Headline
    blocks:
      - { key: risk.totals, render: metric_row, fields: [delta_cash, vega] }
    narrative: |
      State whether the book is within limits.
  - id: data_only
    title: Data only
    blocks:
      - { key: coverage.evidence, render: metric_row }
"""


def _spec(text):
    spec = parse_spec(text)
    validate_spec(spec)
    return spec


def test_a_well_formed_spec_parses_and_validates():
    spec = _spec(GOOD)
    assert spec.meta.slug == "demo-daily"
    assert spec.meta.persona == "risk_manager"
    assert len(spec.sections) == 2
    assert spec.sections[0].blocks[0].key == "risk.totals"
    assert spec.sections[0].blocks[0].fields == ["delta_cash", "vega"]
    assert spec.sections[1].narrative is None


def test_malformed_yaml_raises_template_spec_error():
    with pytest.raises(TemplateSpecError):
        parse_spec("meta: [unclosed")


def test_unknown_block_key_is_rejected_and_named():
    text = GOOD.replace("risk.totals", "risk.does_not_exist")
    with pytest.raises(TemplateSpecError) as exc:
        _spec(text)
    assert "risk.does_not_exist" in str(exc.value)


def test_incompatible_renderer_is_rejected_and_named():
    text = GOOD.replace("render: metric_row, fields: [delta_cash, vega]",
                        "render: waterfall")
    with pytest.raises(TemplateSpecError) as exc:
        _spec(text)
    assert "waterfall" in str(exc.value)
    assert "risk.totals" in str(exc.value)


def test_unknown_renderer_is_rejected():
    text = GOOD.replace("render: metric_row, fields: [delta_cash, vega]",
                        "render: hologram")
    with pytest.raises(TemplateSpecError) as exc:
        _spec(text)
    assert "hologram" in str(exc.value)


def test_unknown_persona_is_rejected():
    text = GOOD.replace("persona: risk_manager", "persona: chief_vibes_officer")
    with pytest.raises(TemplateSpecError) as exc:
        _spec(text)
    assert "chief_vibes_officer" in str(exc.value)


def test_non_kebab_slug_is_rejected():
    text = GOOD.replace("slug: demo-daily", "slug: Demo_Daily")
    with pytest.raises(TemplateSpecError):
        _spec(text)


def test_duplicate_section_ids_are_rejected():
    text = GOOD.replace("id: data_only", "id: headline")
    with pytest.raises(TemplateSpecError) as exc:
        _spec(text)
    assert "headline" in str(exc.value)


def test_a_spec_with_no_sections_is_rejected():
    with pytest.raises(TemplateSpecError):
        _spec("meta:\n  slug: x-y\n  title: X\n  persona: trader\nsections: []\n")


def test_all_errors_are_collected_not_just_the_first():
    text = GOOD.replace("risk.totals", "risk.nope").replace(
        "persona: risk_manager", "persona: nobody"
    )
    with pytest.raises(TemplateSpecError) as exc:
        _spec(text)
    assert len(exc.value.errors) >= 2

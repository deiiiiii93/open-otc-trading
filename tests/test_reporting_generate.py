import pytest

from app.services.reporting import blocks  # noqa: F401 - registers producers
from app.services.reporting.contracts import BlockResult
from app.services.reporting.document import (
    build_document,
    narrator_brief,
    spec_sha256,
)
from app.services.reporting.template_spec import parse_spec

SPEC = """
meta:
  slug: demo-daily
  title: Demo Daily
  persona: risk_manager
sections:
  - id: headline
    title: Headline
    blocks:
      - { key: risk.totals, render: metric_row }
    narrative: Say something.
  - id: quiet
    title: Quiet
    blocks:
      - { key: coverage.evidence, render: metric_row }
"""


def test_spec_sha256_is_stable_and_content_addressed():
    assert spec_sha256(SPEC) == spec_sha256(SPEC)
    assert spec_sha256(SPEC) != spec_sha256(SPEC + "\n")
    assert spec_sha256(SPEC).startswith("sha256:")


def test_document_embeds_the_template_spec_for_reproducibility():
    spec = parse_spec(SPEC)
    doc = build_document(
        spec=spec, spec_yaml=SPEC, version=3,
        params={"portfolio_id": 2, "compare_to_run_id": 35},
        sections=[], provenance={"risk_run_id": 36},
    )
    assert doc["template"]["slug"] == "demo-daily"
    assert doc["template"]["version"] == 3
    assert doc["template"]["spec"] == SPEC
    assert doc["template"]["spec_sha256"] == spec_sha256(SPEC)
    assert doc["params"]["portfolio_id"] == 2
    assert doc["provenance"]["risk_run_id"] == 36


def test_narrator_brief_tells_the_agent_each_block_status():
    """A section whose block is unavailable MUST reach the narrator saying so."""
    spec = parse_spec(SPEC)
    section = spec.sections[0]
    results = {
        "risk.totals": BlockResult.unavailable("no completed risk run exists"),
    }
    brief = narrator_brief(section, results)
    assert brief["section_id"] == "headline"
    assert brief["instruction"] == "Say something."
    entry = brief["blocks"][0]
    assert entry["key"] == "risk.totals"
    assert entry["status"] == "unavailable"
    assert "no completed risk run" in entry["reason"]
    assert entry["data"] == {}


from app.services.reporting.generate import (  # noqa: E402
    TemplateNotFound,
    generate_document,
)


@pytest.fixture
def seeded_template(session):
    from app.services.reporting import templates

    templates.save_template(slug="demo-daily", spec_yaml=SPEC, session=session)
    return "demo-daily"


@pytest.fixture
def stub_blocks(monkeypatch):
    """Resolve every block to a fixed ok result, no DB."""
    from app.services.reporting import generate as gen

    payloads = {
        "risk.totals": BlockResult.ok(
            data={"metrics": {"delta_cash": 57334.67}},
            provenance={"risk_run_id": 36},
        ),
        "coverage.evidence": BlockResult.ok(
            data={"priced": 4, "total": 5}, provenance={"risk_run_id": 36}
        ),
    }
    monkeypatch.setattr(gen, "resolve_block", lambda key, ctx: payloads[key])
    monkeypatch.setattr(gen, "_resolve_comparison", lambda ctx, session: 35)
    return payloads


def test_only_sections_with_a_narrative_call_the_narrator(
    session, seeded_template, stub_blocks
):
    calls = []

    def narrate(persona, brief):
        calls.append(brief["section_id"])
        return "Delta cash is 57,334.67."

    doc = generate_document(
        template_slug=seeded_template, portfolio_id=2,
        narrate=narrate, session=session,
    )
    assert calls == ["headline"]
    assert doc["sections"][0]["narrative"] == "Delta cash is 57,334.67."
    assert doc["sections"][1]["narrative"] is None


def test_a_template_with_no_narrative_makes_zero_narrator_calls(
    session, stub_blocks
):
    from app.services.reporting import templates

    silent = SPEC.replace("    narrative: Say something.\n", "")
    templates.save_template(slug="demo-daily", spec_yaml=silent, session=session)

    def narrate(persona, brief):  # pragma: no cover - must never run
        raise AssertionError("narrator called for a fully deterministic template")

    doc = generate_document(
        template_slug="demo-daily", portfolio_id=2,
        narrate=narrate, session=session,
    )
    assert all(section["narrative"] is None for section in doc["sections"])


def test_the_narrator_receives_the_templates_persona(
    session, seeded_template, stub_blocks
):
    seen = []
    generate_document(
        template_slug=seeded_template, portfolio_id=2,
        narrate=lambda persona, brief: seen.append(persona) or "ok",
        session=session,
    )
    assert seen == ["risk_manager"]


def test_block_results_are_embedded_per_section(
    session, seeded_template, stub_blocks
):
    doc = generate_document(
        template_slug=seeded_template, portfolio_id=2,
        narrate=lambda persona, brief: "Delta cash is 57,334.67.",
        session=session,
    )
    block = doc["sections"][0]["blocks"][0]
    assert block["key"] == "risk.totals"
    assert block["render"] == "metric_row"
    assert block["result"]["status"] == "ok"
    assert block["result"]["data"]["metrics"]["delta_cash"] == pytest.approx(57334.67)


def test_an_ungrounded_number_in_prose_is_flagged_but_does_not_fail_generation(
    session, seeded_template, stub_blocks
):
    doc = generate_document(
        template_slug=seeded_template, portfolio_id=2,
        narrate=lambda persona, brief: "Delta cash is 57,334.67 and vega is 999.99.",
        session=session,
    )
    grounding = doc["sections"][0]["grounding"]
    assert grounding["checked"] is True
    assert [flag["token"] for flag in grounding["flags"]] == [999.99]


def test_a_narrator_failure_degrades_that_section_only(
    session, seeded_template, stub_blocks
):
    def narrate(persona, brief):
        raise RuntimeError("model timed out")

    doc = generate_document(
        template_slug=seeded_template, portfolio_id=2,
        narrate=narrate, session=session,
    )
    assert doc["sections"][0]["narrative"] is None
    assert "model timed out" in doc["sections"][0]["narrative_error"]
    # The data section is untouched.
    assert doc["sections"][1]["blocks"][0]["result"]["status"] == "ok"


def test_a_missing_template_raises(session):
    with pytest.raises(TemplateNotFound):
        generate_document(
            template_slug="does-not-exist", portfolio_id=2,
            narrate=lambda p, b: "", session=session,
        )


def test_each_block_is_resolved_exactly_once_even_if_reused(
    session, monkeypatch, stub_blocks
):
    """A key repeated across sections must not be recomputed."""
    from app.services.reporting import generate as gen
    from app.services.reporting import templates

    reused = SPEC.replace("key: coverage.evidence", "key: risk.totals")
    templates.save_template(slug="demo-daily", spec_yaml=reused, session=session)

    calls = []
    original = gen.resolve_block
    monkeypatch.setattr(
        gen, "resolve_block",
        lambda key, ctx: (calls.append(key), original(key, ctx))[1],
    )
    generate_document(
        template_slug="demo-daily", portfolio_id=2,
        narrate=lambda p, b: "ok", session=session,
    )
    assert calls.count("risk.totals") == 1

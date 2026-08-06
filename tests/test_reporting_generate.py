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

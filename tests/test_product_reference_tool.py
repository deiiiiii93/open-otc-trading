"""Tool-level smokes for get_product_reference_doc - proves the runtime
loads the same merged content the coherence net validated (CI/runtime
parity), including the snowball base+CN-overlay split."""
from __future__ import annotations

from app.services.agents import DEEP_AGENT_TOOL_NAMES
from app.tools import QUANT_AGENT_TOOLS
from app.tools.product_reference import get_product_reference_doc


def _invoke(quantark_class: str) -> dict:
    return get_product_reference_doc.invoke({"quantark_class": quantark_class})


def test_tool_is_registered_everywhere() -> None:
    assert "get_product_reference_doc" in {t.name for t in QUANT_AGENT_TOOLS}
    assert "get_product_reference_doc" in DEEP_AGENT_TOOL_NAMES


def test_plain_family_returns_own_doc() -> None:
    result = _invoke("SingleSharkfinOption")
    assert "participation rate" in result["content"].lower()


def test_inherited_family_includes_base_terms() -> None:
    result = _invoke("KnockOutResetSnowballOption")
    content = result["content"].lower()
    assert "post-ki ko barrier" in content          # own delta
    assert "ki barrier" in content                   # inherited from snowball base
    assert "observation frequency" in content        # inherited


def test_snowball_with_cn_region_keeps_base_and_overlay() -> None:
    # Settings is a frozen dataclass and get_settings() is NOT cached - the
    # override seam is configure_settings (established pattern, see
    # tests/test_stream_and_persist.py / tests/gateway/test_cards.py).
    import dataclasses

    from app.config import configure_settings, get_settings

    configure_settings(dataclasses.replace(get_settings(), desk_region="CN"))
    try:
        result = _invoke("SnowballOption")
        content = result["content"]
        assert "ko barrier" in content.lower()
        assert "## Regional Conventions (CN)" in content
    finally:
        configure_settings(None)


def test_unknown_class_lists_known() -> None:
    result = _invoke("NopeOption")
    assert "error" in result
    assert "SnowballOption" in result["known_classes"]


def test_neutral_region_omits_overlay_by_design() -> None:
    # DELIBERATE: the code default for desk_region is None (region-neutral) -
    # region conventions are a per-deployment opt-in via OPEN_OTC_DESK_REGION,
    # never a code fact. CN deployments set OPEN_OTC_DESK_REGION=CN in .env to
    # keep the CN overlay the legacy snowball-cn.md used to provide.
    import dataclasses

    from app.config import configure_settings, get_settings

    configure_settings(dataclasses.replace(get_settings(), desk_region=None))
    try:
        result = _invoke("SnowballOption")
        assert "## Regional Conventions" not in result["content"]
        assert "SSE" not in result["content"]
    finally:
        configure_settings(None)

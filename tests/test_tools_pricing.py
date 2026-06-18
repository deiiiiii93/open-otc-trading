from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.pricing import price_product_tool


def test_price_product_tool():
    fake_result = MagicMock(ok=True, data={"price": 1.23}, error=None)
    with patch(
        "app.services.domains.pricing._quantark_price_product",
        return_value=fake_result,
    ):
        result = price_product_tool.invoke(
            {
                "product": {
                    "quantark_class": "EuropeanVanillaOption",
                    "underlying": "CSI500",
                    "terms": {"strike": 100},
                },
                "market": {},
                "engine_name": "BlackScholesEngine",
            }
        )
    assert result["ok"] is True
    assert result["data"]["price"] == 1.23
    assert result["error"] is None


def test_price_product_tool_error():
    fake_result = MagicMock(ok=False, data={}, error="invalid spec")
    with patch(
        "app.services.domains.pricing._quantark_price_product",
        return_value=fake_result,
    ):
        result = price_product_tool.invoke(
            {
                "product": {
                    "quantark_class": "EuropeanVanillaOption",
                    "underlying": "CSI500",
                    "terms": {},
                },
                "market": {},
            }
        )
    assert result["ok"] is False
    assert result["error"] == "invalid spec"


def test_agent_pricing_module_has_no_batch_tool():
    """Persisted batch repricing is run_batch_pricing (tools/risk.py); the
    pricing module only exposes the read-only ad-hoc pricer."""
    from app import tools as tools_pkg
    from app.tools import pricing as pricing_module

    assert not hasattr(pricing_module, "price_positions_tool")
    names = {tool.name for tool in tools_pkg.QUANT_AGENT_TOOLS}
    assert "price_positions" not in names
    assert "run_batch_pricing" in names

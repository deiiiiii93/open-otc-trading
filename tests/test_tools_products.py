from __future__ import annotations

from app.tools.products import build_product_tool


def test_build_product_tool_returns_validated_snowball():
    result = build_product_tool.invoke({
        "family": "SnowballOption",
        "terms": {
            "underlying": "000905.SH", "currency": "CNY", "initial_price": 100.0,
            "maturity_years": 1.0, "ko_barrier_pct": 101, "ki_barrier_pct": 70,
            "ko_rate": 0.15, "ko_frequency": "MONTHLY", "ki_convention": "DAILY",
            "lockup_months": 3, "trade_start_date": "2026-01-05",
        },
    })
    assert result["ok"] is True
    assert result["engine_name"] == "SnowballQuadEngine"
    assert result["missing"] == []
    assert result["product_kwargs"]["barrier_config"]["ko_barrier"] == 101.0


def test_build_product_tool_reports_missing():
    result = build_product_tool.invoke({
        "family": "SnowballOption",
        "terms": {"underlying": "000905.SH", "maturity_years": 1.0,
                  "ko_barrier_pct": 101, "ki_barrier_pct": 70},
    })
    assert result["ok"] is False
    assert "trade_start_date" in result["missing"]


def test_build_product_tool_keeps_flat_product_kwargs_and_adds_product_spec():
    from app.tools.products import build_product_tool

    payload = build_product_tool.invoke(
        {
            "family": "SnowballOption",
            "terms": {
                "initial_price": 100.0,
                "maturity_years": 1.0,
                "ko_barrier_pct": 101,
                "ki_barrier_pct": 70,
                "ko_rate": 0.15,
                "ko_frequency": "MONTHLY",
                "ki_convention": "DAILY",
                "lockup_months": 3,
                "trade_start_date": "2026-01-05",
            },
        }
    )
    assert payload["ok"] is True
    # flat product_kwargs preserved for the LLM build contract
    assert "barrier_config" in payload["product_kwargs"]
    # product_spec now travels alongside
    assert payload["product_spec"]["quantark_class"] == "SnowballOption"
    assert payload["product_spec"]["product_family"] == "autocallable"

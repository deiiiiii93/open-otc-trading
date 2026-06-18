from __future__ import annotations

from app.schemas import RFQRequestDraft
from app.tools import QUANT_AGENT_TOOLS
from app.tools.risk import calculate_risk_tool
from app.tools.rfq import solve_rfq_tool
from app.services.quantark import solve_rfq


def test_solve_rfq_returns_quote_payload():
    draft = RFQRequestDraft(
        client_name="Client C",
        product_type="EuropeanVanillaOption",
        product_kwargs={
            "strike": 100,
            "option_type": "CALL",
            "maturity": 1,
            "contract_multiplier": 1,
        },
        engine_spec={"engine_name": "BlackScholesEngine"},
        unknown={"field_path": "strike", "lower_bound": 50, "upper_bound": 150, "initial_guess": 100},
        target={"label": "price", "value": 10},
    )

    result = solve_rfq(draft)

    assert result.data["field_path"] == "strike"
    assert "client_response" in result.data
    assert result.data["solved_value"] > 0


def test_langchain_tool_registry_exposes_core_skills():
    names = {tool.name for tool in QUANT_AGENT_TOOLS}

    assert {
        "price_product",
        "solve_rfq",
        "get_rfq_catalog",
        "build_product",
        "validate_rfq_terms",
        "create_or_update_rfq_draft",
        "quote_rfq",
        "book_rfq_to_position",
        "get_positions",
        "calculate_risk",
        "recommend_hedge",
        "run_report_batch",
        "fetch_market_snapshot",
        "list_pricing_parameter_profiles",
        "close_position",
        "settle_position",
        "mark_knockout",
        "cancel_lifecycle_event",
    } <= names

    quote = solve_rfq_tool.invoke(
        {
            "client_name": "Tool Client",
            "product_type": "EuropeanVanillaOption",
            "product_kwargs": {
                "strike": 100,
                "option_type": "CALL",
                "maturity": 1,
                "contract_multiplier": 1,
            },
            "unknown": {"field_path": "strike", "lower_bound": 50, "upper_bound": 150, "initial_guess": 100},
            "target": {"label": "price", "value": 10},
        }
    )
    assert quote["quote"]["field_path"] == "strike"

    risk = calculate_risk_tool.invoke(
        {
            "positions": [
                {
                    "underlying": "CSI500",
                    "product_type": "EuropeanVanillaOption",
                    "product_kwargs": {
                        "strike": 100,
                        "option_type": "CALL",
                        "maturity": 1,
                        "contract_multiplier": 1,
                    },
                    "engine_name": "BlackScholesEngine",
                    "quantity": 1,
                    "entry_price": 8.0,
                }
            ]
        }
    )
    assert "one_day_var_proxy" in risk["totals"]

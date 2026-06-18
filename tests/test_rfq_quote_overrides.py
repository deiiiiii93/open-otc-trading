from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import RFQ, RFQQuoteVersion, RfqStatus
from app.schemas import RFQQuoteRequest, RFQRequestDraft
from app.services.rfq import (
    _create_quote_version,
    _draft_with_quote_overrides,
    _fixed_price_quote_payload,
    create_rfq_draft,
)


def test_quote_product_kwargs_overrides_deep_merge_existing_terms():
    rfq = SimpleNamespace(
        request_payload={
            "client_name": "YNIT",
            "underlying": "000905.SH",
            "side": "sell",
            "quantity": 5_000_000,
            "quote_mode": "price",
            "product_type": "SnowballOption",
            "product_kwargs": {
                "initial_price": 100,
                "strike": 100,
                "maturity": 1,
                "barrier_config": {
                    "ko_barrier": 101,
                    "ko_rate": 0.15,
                    "ki_barrier": 70,
                },
            },
            "market": {
                "spot": 100,
                "volatility": 0.2,
                "rate": 0.03,
                "dividend_yield": 0,
                "currency": "CNY",
            },
        }
    )

    draft = _draft_with_quote_overrides(
        rfq,
        RFQQuoteRequest(
            product_kwargs={
                "maturity_years": 1,
                "barrier_config": {"ko_rate": 0.2},
            }
        ),
    )

    assert draft.product_kwargs["maturity_years"] == 1
    assert draft.product_kwargs["barrier_config"] == {
        "ko_barrier": 101,
        "ko_rate": 0.2,
        "ki_barrier": 70,
    }


def test_quote_overrides_preserve_unspecified_market_and_engine_fields():
    rfq = SimpleNamespace(
        request_payload={
            "client_name": "YNIT",
            "underlying": "000905.SH",
            "side": "sell",
            "quantity": 5_000_000,
            "quote_mode": "price",
            "product_type": "SnowballOption",
            "product_kwargs": {
                "initial_price": 100,
                "strike": 100,
                "maturity_years": 1,
                "ko_barrier_pct": 101,
                "ki_barrier_pct": 70,
                "ko_rate": 0.15,
                "lockup_months": 3,
                "observation_frequency": "MONTHLY",
            },
            "market": {
                "valuation_date": "2026-05-29T00:00:00",
                "spot": 100,
                "volatility": 0.2,
                "rate": 0.03,
                "dividend_yield": 0,
                "currency": "CNY",
                "day_count_convention": "ACT_365",
                "bus_days_in_year": 252,
            },
            "engine_spec": {
                "engine_name": "SnowballQuadEngine",
                "params_type": "quad_params",
                "engine_kwargs": {"params_type": "quad_params"},
            },
        }
    )

    draft = _draft_with_quote_overrides(
        rfq,
        RFQQuoteRequest.model_validate({
            "market": {"spot": 101},
            "engine_spec": {"engine_name": "SnowballQuadEngine"},
        }),
    )

    assert draft.market.spot == 101
    assert draft.market.currency == "CNY"
    assert draft.market.valuation_date.isoformat() == "2026-05-29T00:00:00"
    assert draft.engine_spec.params_type == "quad_params"
    assert draft.engine_spec.engine_kwargs == {"params_type": "quad_params"}


def test_fixed_price_quote_payload_includes_notional_scaled_quote_amount():
    draft = RFQRequestDraft.model_validate({
        "client_name": "YNIT",
        "underlying": "000905.SH",
        "side": "sell",
        "quantity": 5_000_000,
        "quote_mode": "price",
        "product_type": "SnowballOption",
        "product_kwargs": {
            "initial_price": 100,
            "strike": 100,
            "maturity_years": 1,
        },
        "market": {
            "spot": 100,
            "volatility": 0.2,
            "rate": 0.03,
            "dividend_yield": 0,
            "currency": "CNY",
        },
        "engine_spec": {"engine_name": "SnowballQuadEngine"},
    })

    payload = _fixed_price_quote_payload(
        draft,
        SimpleNamespace(ok=True, data={"price": 10.008375586187357}),
    )

    assert payload["unit_price"] == pytest.approx(10.008375586187357)
    assert payload["quote_notional"] == 5_000_000
    assert payload["quote_price_scale"] == 100
    assert payload["quote_amount"] == pytest.approx(500_418.7793093679)
    assert payload["quote_amount_currency"] == "CNY"
    assert "unit price = 10.0084" in payload["client_response"]
    assert "quote amount = CNY 500,419" in payload["client_response"]


def test_create_quote_version_uses_latest_existing_version(session):
    rfq = RFQ(
        client_name="YNIT",
        channel="desk",
        status=RfqStatus.PRICING_FAILED.value,
        request_payload={},
        quote_payload={},
    )
    session.add(rfq)
    session.flush()
    session.add_all([
        RFQQuoteVersion(
            rfq_id=rfq.id,
            version=1,
            quote_mode="solve",
            status=RfqStatus.DRAFT.value,
            request_payload={},
            quote_payload={},
            created_by="desk",
        ),
        RFQQuoteVersion(
            rfq_id=rfq.id,
            version=2,
            quote_mode="price",
            status=RfqStatus.PRICING_FAILED.value,
            request_payload={},
            quote_payload={},
            created_by="desk",
        ),
    ])
    session.flush()

    version = _create_quote_version(
        session,
        rfq,
        quote_mode="price",
        status=RfqStatus.PENDING_APPROVAL.value,
        request_payload={},
        quote_payload={},
        error=None,
        created_by="desk",
        valid_until=None,
    )

    assert version.version == 3


def test_create_rfq_draft_normalizes_product_currency_into_market(session):
    draft = RFQRequestDraft.model_validate({
        "client_name": "YNIT",
        "underlying": "0700.HK",
        "side": "buy",
        "quantity": 1_000_000,
        "quote_mode": "price",
        "product": {
            "asset_class": "equity",
            "product_family": "option",
            "quantark_class": "EuropeanVanillaOption",
            "underlying": "0700.HK",
            "currency": "hkd",
            "terms": {"strike": 100, "option_type": "CALL", "maturity": 1, "contract_multiplier": 1},
        },
        "engine_spec": {"engine_name": "BlackScholesEngine"},
    })

    rfq = create_rfq_draft(session, draft, channel="form")

    assert rfq.request_payload["market"]["currency"] == "HKD"
    assert rfq.request_payload["product"]["currency"] == "HKD"
    assert "currency" not in rfq.request_payload["product_kwargs"]

"""Regression for the final-review Critical bug: the per-position `currency`
column must actually drive currency-aware risk bucketing through the LIVE path
(`market_snapshot_for_position` -> snapshot.currency), not just the pricer's
`_source_currency`. The C3 integration test stubbed `_calculate_position_risk_job`
and injected currency via a hand-built markets dict, masking this gap."""
from __future__ import annotations

from types import SimpleNamespace

from app.schemas import PricingEnvironmentSnapshot
from app.services.quantark import (
    _source_currency_for_position,
    market_snapshot_for_position,
)


def test_source_currency_for_position_prefers_column():
    # Column wins over the legacy 交易规模单位 scrape.
    pos = SimpleNamespace(currency="USD", source_payload={"row": {"Notional Unit": "CNY"}})
    assert _source_currency_for_position(pos) == "USD"

    # Fallback to the scrape when the column is unset.
    legacy = SimpleNamespace(currency=None, source_payload={"row": {"Notional Unit": "EUR"}})
    assert _source_currency_for_position(legacy) == "EUR"


def test_market_snapshot_stamps_position_currency_without_session():
    """Without a session the snapshot must still carry the position's own currency,
    not the fallback's default — otherwise positions bucket by the fallback ccy."""
    pos = SimpleNamespace(currency="USD", underlying="AAPL", source_payload={})
    snap = market_snapshot_for_position(pos, PricingEnvironmentSnapshot(currency="CNY"))
    assert snap.currency == "USD"


def test_market_snapshot_currency_independent_of_fallback_spot():
    """Spot is observation-only (quote store); with no session the snapshot keeps
    the fallback spot while still stamping the position's currency."""
    pos = SimpleNamespace(currency="USD", underlying="AAPL", source_payload={})
    snap = market_snapshot_for_position(
        pos, PricingEnvironmentSnapshot(currency="CNY", spot=123.0)
    )
    assert snap.currency == "USD"
    assert snap.spot == 123.0

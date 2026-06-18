# tests/test_hedging_book.py
import pytest

from datetime import date

from app.models import Instrument, Portfolio, Position
from app.services.domains import hedging_strategy as hs


def test_book_hedge_persists_tagged_legs(session):
    pf = Portfolio(name="book", base_currency="CNY")  # kind defaults to "container"
    inst = Instrument(
        symbol="IC2406.CFE", kind="futures", exchange="CFFEX", contract_code="IC2406",
        series_root="IC", expiry=date(2024, 6, 21), multiplier=200.0, status="active",
    )
    session.add_all([pf, inst]); session.flush()
    legs = [{
        "instrument_id": inst.id,
        "contract_code": "IC2406", "exchange": "CFFEX", "family": "index_future",
        "instrument_type": "future", "option_type": None, "strike": None,
        "multiplier": 200.0, "quantity": -3,
    }]
    out = hs.book_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                        risk_run_id=42, strategy="delta_neutral", legs=legs,
                        spot=5600.0)
    session.flush()
    assert len(out["position_ids"]) == 1
    pos = session.get(Position, out["position_ids"][0])
    assert pos.source_payload["hedge"]["risk_run_id"] == 42
    assert pos.source_payload["hedge"]["is_hedge"] is True
    assert pos.source_trade_id == "HEDGE:42:1"
    assert pos.position_kind == "listed"
    assert pos.quantity == -3


def test_book_hedge_requires_instrument_id_for_listed_contracts(session):
    pf = Portfolio(name="book_requires_inst", base_currency="CNY")
    session.add(pf); session.flush()
    legs = [{
        "contract_code": "IC2406", "exchange": "CFFEX", "family": "index_future",
        "instrument_type": "future", "multiplier": 200.0, "quantity": -1,
    }]

    with pytest.raises(ValueError, match="must include instrument_id"):
        hs.book_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                      risk_run_id=41, strategy="delta_neutral", legs=legs,
                      spot=5600.0)


def test_book_hedge_canonicalizes_futures_leg_from_instrument(session):
    pf = Portfolio(name="book_canonical_future", base_currency="CNY")
    inst = Instrument(
        symbol="IC2606.CFE", kind="futures", exchange="CFFEX", contract_code="IC2606",
        series_root="IC", expiry=date(2026, 6, 22), multiplier=200.0, status="active",
    )
    session.add_all([pf, inst]); session.flush()
    legs = [{
        "instrument_id": inst.id,
        "contract_code": "CORRUPT",
        "exchange": "BAD",
        "family": "bad_family",
        "instrument_type": "future",
        "multiplier": 999.0,
        "quantity": -2,
    }]

    out = hs.book_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                        risk_run_id=43, strategy="delta_neutral", legs=legs,
                        spot=8362.16)
    session.flush()
    pos = session.get(Position, out["position_ids"][0])

    assert pos.underlying == "IC2606.CFE"
    assert pos.product_type == "Futures"
    assert pos.product_kwargs["underlying"] == "IC2606.CFE"
    assert pos.product_kwargs["_otc_contract_code"] == "IC2606"
    assert pos.product_kwargs["multiplier"] == 200.0
    assert pos.underlying_id == inst.id
    assert pos.source_payload["hedge"]["hedged_underlying"] == "000905.SH"
    assert pos.source_payload["hedge"]["instrument_id"] == inst.id
    assert pos.source_payload["hedge"]["contract_code"] == "IC2606"


def test_book_hedge_persists_option_leg(session):
    pf = Portfolio(name="book_opt", base_currency="CNY")  # kind defaults to "container"
    inst = Instrument(
        symbol="IO2412C5600.CFE", kind="listed_option", exchange="CFFEX",
        contract_code="IO2412C5600", series_root="IO", expiry=date(2026, 12, 18),
        multiplier=100.0, strike=5600.0, option_type="C", status="active",
    )
    session.add_all([pf, inst]); session.flush()
    legs = [{
        "instrument_id": inst.id,
        "contract_code": "IO2412C5600", "exchange": "CFFEX", "family": "index_option",
        "instrument_type": "future", "option_type": None, "strike": None,
        "expiry": None, "multiplier": 999.0, "quantity": 2,
    }]
    out = hs.book_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                        risk_run_id=77, strategy="delta_neutral", legs=legs,
                        spot=5600.0)
    session.flush()
    assert len(out["position_ids"]) == 1
    pos = session.get(Position, out["position_ids"][0])
    # The option Position really persisted with the gamma_vega hedge tag.
    assert pos.source_payload["hedge"]["leg_role"] == "gamma_vega"
    assert pos.source_payload["hedge"]["risk_run_id"] == 77
    assert pos.quantity == 2
    # Synthesized vanilla termsheet: contract_multiplier is a native QuantArk kwarg.
    assert pos.product_type == "EuropeanVanillaOption"
    assert pos.product_kwargs["contract_multiplier"] == 100.0
    assert pos.product_kwargs["option_type"] == "CALL"


def test_book_hedge_skips_zero_quantity_legs(session):
    pf = Portfolio(name="book2", base_currency="CNY")
    session.add(pf); session.flush()
    legs = [{"contract_code": "IC2406", "exchange": "CFFEX", "family": "index_future",
             "instrument_type": "future", "multiplier": 200.0, "quantity": 0}]
    out = hs.book_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                        risk_run_id=1, strategy="delta_neutral", legs=legs, spot=5600.0)
    assert out["position_ids"] == []


def test_book_hedge_accepts_manual_strategy_tag(session):
    pf = Portfolio(name="book_manual", base_currency="CNY")
    inst = Instrument(
        symbol="IC2609.CFE", kind="futures", exchange="CFFEX", contract_code="IC2609",
        series_root="IC", expiry=date(2026, 9, 21), multiplier=200.0, status="active",
    )
    session.add_all([pf, inst]); session.flush()
    legs = [{"instrument_id": inst.id,
             "contract_code": "IC2609", "exchange": "CFFEX", "family": "index_future",
             "instrument_type": "future", "multiplier": 200.0, "quantity": -2}]
    out = hs.book_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                        risk_run_id=20, strategy="manual", legs=legs, spot=8362.16)
    session.flush()
    pos = session.get(Position, out["position_ids"][0])
    # Desk-sized hedge: the manual tag is recorded verbatim in the hedge payload.
    assert pos.source_payload["hedge"]["strategy"] == "manual"
    assert pos.source_payload["hedge"]["is_hedge"] is True
    assert pos.source_trade_id == "HEDGE:20:1"
    assert pos.quantity == -2


def test_book_hedge_rejects_unknown_strategy(session):
    pf = Portfolio(name="book_bad_strategy", base_currency="CNY")
    session.add(pf); session.flush()
    legs = [{"contract_code": "IC2406", "exchange": "CFFEX", "family": "index_future",
             "instrument_type": "future", "multiplier": 200.0, "quantity": -1}]
    with pytest.raises(ValueError, match="Unknown hedge strategy"):
        hs.book_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                      risk_run_id=1, strategy="detla_neutral", legs=legs, spot=5600.0)


def test_book_hedge_continues_leg_numbering_per_run(session):
    """A second booking against the same risk_run_id must not re-mint
    HEDGE:{run}:1 — numbering continues past the max existing suffix so
    source_trade_id stays unique without a DB constraint (which would break
    OTC re-import refresh)."""
    pf = Portfolio(name="book_seq", base_currency="CNY")
    inst = Instrument(
        symbol="IC2406.CFE", kind="futures", exchange="CFFEX", contract_code="IC2406",
        series_root="IC", expiry=date(2024, 6, 21), multiplier=200.0, status="active",
    )
    session.add_all([pf, inst]); session.flush()
    leg = {"instrument_id": inst.id,
           "contract_code": "IC2406", "exchange": "CFFEX", "family": "index_future",
           "instrument_type": "future", "multiplier": 200.0, "quantity": -2}

    first = hs.book_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                          risk_run_id=21, strategy="manual", legs=[leg, dict(leg)],
                          spot=5600.0)
    session.flush()
    second = hs.book_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                           risk_run_id=21, strategy="manual", legs=[dict(leg)],
                           spot=5600.0)
    session.flush()

    first_ids = [session.get(Position, pid).source_trade_id
                 for pid in first["position_ids"]]
    second_ids = [session.get(Position, pid).source_trade_id
                  for pid in second["position_ids"]]
    assert first_ids == ["HEDGE:21:1", "HEDGE:21:2"]
    assert second_ids == ["HEDGE:21:3"]
    # A different run keeps its own namespace (prefix match must not bleed
    # across runs sharing a string prefix, e.g. HEDGE:2: vs HEDGE:21:).
    other = hs.book_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                          risk_run_id=2, strategy="manual", legs=[dict(leg)],
                          spot=5600.0)
    session.flush()
    other_ids = [session.get(Position, pid).source_trade_id
                 for pid in other["position_ids"]]
    assert other_ids == ["HEDGE:2:1"]

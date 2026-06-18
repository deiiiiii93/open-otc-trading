from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app import database
from app.config import Settings
from app.models import (
    AuditEvent,
    Portfolio,
    Position,
    PositionValuationResult,
    PositionValuationRun,
)
from app.services.domains import positions as positions_svc


# ---------------------------------------------------------------------------
# Step 1 — pure helpers (no DB)
# ---------------------------------------------------------------------------


def test_count_from_snapshot_pure():
    snapshot = {
        "positions": [
            {"product_type": "EuropeanVanillaOption"},
            {"product_type": "SnowballOption"},
            {"product_type": "PhoenixOption"},
        ]
    }
    assert positions_svc.count_from_snapshot(snapshot) == 3


def test_count_from_snapshot_empty():
    assert positions_svc.count_from_snapshot({}) == 0
    assert positions_svc.count_from_snapshot({"positions": []}) == 0


def test_list_from_snapshot_filter_by_product_type():
    rows = [
        {"product_type": "SnowballOption", "underlying": "A"},
        {"product_type": "EuropeanVanillaOption", "underlying": "B"},
        {"product_type": "PhoenixOption", "underlying": "C"},
    ]
    filtered, missing = positions_svc.list_from_snapshot(
        rows, product_type="snowball"
    )
    assert missing == 0
    assert [r["underlying"] for r in filtered] == ["A"]


def test_list_from_snapshot_no_filter_returns_all():
    rows = [
        {"product_type": "X"},
        {"product_type": "Y"},
    ]
    filtered, missing = positions_svc.list_from_snapshot(rows)
    assert filtered == rows
    assert missing == 0


def test_list_from_snapshot_effective_window_filters_and_counts_missing():
    rows = [
        {"product_type": "X", "trade_effective_date": "2026-05-01"},  # in
        {"product_type": "X", "trade_effective_date": "2026-04-01"},  # out
        {"product_type": "X"},  # missing
    ]
    filtered, missing = positions_svc.list_from_snapshot(
        rows,
        effective_date_from=date(2026, 4, 15),
        effective_date_to=date(2026, 5, 31),
    )
    assert len(filtered) == 1
    assert filtered[0]["trade_effective_date"] == "2026-05-01"
    assert missing == 1


# ---------------------------------------------------------------------------
# Step 2 — DB-backed reads
# ---------------------------------------------------------------------------


@pytest.fixture
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    return settings


def _make_portfolio(name: str = "Book", kind: str = "container") -> int:
    with database.SessionLocal() as session:
        p = Portfolio(name=name, kind=kind, base_currency="CNY")
        session.add(p)
        session.commit()
        return p.id


def _make_position(
    portfolio_id: int,
    *,
    product_type: str = "EuropeanVanillaOption",
    underlying: str = "000300.SH",
    status: str = "open",
    source_trade_id: str | None = None,
    trade_effective_date: datetime | None = None,
) -> int:
    with database.SessionLocal() as session:
        position = Position(
            portfolio_id=portfolio_id,
            underlying=underlying,
            product_type=product_type,
            quantity=1.0,
            status=status,
            source_trade_id=source_trade_id,
            trade_effective_date=trade_effective_date,
        )
        session.add(position)
        session.commit()
        return position.id


def test_list_filtered_through_portfolio_membership(_db):
    pid = _make_portfolio()
    _make_position(pid, product_type="SnowballOption")
    _make_position(pid, product_type="EuropeanVanillaOption")
    rows = positions_svc.list_filtered(portfolio_id=pid)
    assert len(rows) == 2
    assert all(isinstance(r, Position) for r in rows)


def test_list_filtered_product_type(_db):
    pid = _make_portfolio()
    _make_position(pid, product_type="SnowballOption")
    _make_position(pid, product_type="EuropeanVanillaOption")
    rows = positions_svc.list_filtered(portfolio_id=pid, product_type="snowball")
    assert len(rows) == 1
    assert rows[0].product_type == "SnowballOption"


def test_list_filtered_status_filter(_db):
    pid = _make_portfolio()
    _make_position(pid, status="open")
    _make_position(pid, status="closed")
    rows = positions_svc.list_filtered(portfolio_id=pid, status="open")
    assert len(rows) == 1
    assert rows[0].status == "open"


def test_list_filtered_status_none_returns_all(_db):
    pid = _make_portfolio()
    _make_position(pid, status="open")
    _make_position(pid, status="closed")
    rows = positions_svc.list_filtered(portfolio_id=pid, status=None)
    assert len(rows) == 2


def test_count_uses_list_filtered(_db):
    pid = _make_portfolio()
    _make_position(pid, status="open")
    _make_position(pid, status="open")
    _make_position(pid, status="closed")
    # count() defaults to status="open" via list_filtered
    assert positions_svc.count(portfolio_id=pid) == 2


def test_create_lifecycle_event_knockout_closes_position_and_audits(_db):
    pid = _make_portfolio()
    posid = _make_position(
        pid,
        product_type="SnowballOption",
        status="knocked_in",
        source_trade_id="SB-1",
    )

    update = positions_svc.create_lifecycle_event(
        position_id=posid,
        event_type="knock_out",
        event_data={"observation_date": "2026-05-27", "observed_spot": 8799.312},
        actor="agent",
    )

    assert update.event.event_type == "knock_out"
    assert update.event.old_status == "knocked_in"
    assert update.event.new_status == "closed"
    assert update.position.status == "closed"
    with database.SessionLocal() as session:
        position = session.get(Position, posid)
        audit = (
            session.query(AuditEvent)
            .filter(AuditEvent.event_type == "position.lifecycle_event")
            .one()
        )
    assert position.status == "closed"
    assert audit.payload["event_type"] == "knock_out"
    assert audit.payload["new_status"] == "closed"


def test_create_lifecycle_event_settle_resolves_source_trade_id(_db):
    pid = _make_portfolio()
    posid = _make_position(
        pid,
        product_type="SnowballOption",
        source_trade_id="SB-SETTLE",
    )

    update = positions_svc.create_lifecycle_event(
        source_trade_id="SB-SETTLE",
        portfolio_id=pid,
        event_type="settle",
        event_data={"settlement_date": "2026-05-28"},
    )

    assert update.position.id == posid
    assert update.event.event_type == "settle"
    assert update.event.new_status == "closed"


def test_create_lifecycle_event_rejects_view_portfolio(_db):
    pid = _make_portfolio(kind="view")
    posid = _make_position(pid, product_type="SnowballOption")

    with pytest.raises(ValueError, match="container portfolios"):
        positions_svc.create_lifecycle_event(
            position_id=posid,
            event_type="knock_out",
        )


def test_cancel_lifecycle_event_knockout_restores_prior_status_and_audits(_db):
    pid = _make_portfolio()
    posid = _make_position(pid, product_type="SnowballOption", status="knocked_in")
    update = positions_svc.create_lifecycle_event(
        position_id=posid,
        event_type="knock_out",
        actor="agent",
    )

    cancelled = positions_svc.cancel_lifecycle_event(
        lifecycle_event_id=update.event.id,
        position_id=posid,
        reason="bad KO observation",
        actor="agent",
    )

    assert cancelled.event.cancelled_by == "agent"
    assert cancelled.event.cancellation_reason == "bad KO observation"
    assert cancelled.position.status == "knocked_in"
    with database.SessionLocal() as session:
        position = session.get(Position, posid)
        audit = (
            session.query(AuditEvent)
            .filter(AuditEvent.event_type == "position.lifecycle_event.cancelled")
            .one()
        )
    assert position.status == "knocked_in"
    assert audit.payload["lifecycle_event_id"] == update.event.id
    assert audit.payload["cancelled_event_type"] == "knock_out"
    assert audit.payload["old_status"] == "closed"
    assert audit.payload["new_status"] == "knocked_in"


def test_cancel_settle_after_knockout_leaves_position_closed(_db):
    pid = _make_portfolio()
    posid = _make_position(pid, product_type="SnowballOption")
    positions_svc.create_lifecycle_event(position_id=posid, event_type="knock_out")
    settle = positions_svc.create_lifecycle_event(position_id=posid, event_type="settle")

    cancelled = positions_svc.cancel_lifecycle_event(
        lifecycle_event_id=settle.event.id,
        position_id=posid,
    )

    assert cancelled.position.status == "closed"


def test_cancel_middle_event_replays_remaining_events(_db):
    pid = _make_portfolio()
    posid = _make_position(pid, product_type="SnowballOption")
    positions_svc.create_lifecycle_event(position_id=posid, event_type="knock_in")
    knockout = positions_svc.create_lifecycle_event(position_id=posid, event_type="knock_out")
    positions_svc.create_lifecycle_event(position_id=posid, event_type="settle")

    cancelled = positions_svc.cancel_lifecycle_event(
        lifecycle_event_id=knockout.event.id,
        position_id=posid,
    )

    assert cancelled.position.status == "closed"


def test_cancel_informational_event_preserves_status(_db):
    pid = _make_portfolio()
    posid = _make_position(pid, product_type="SnowballOption")
    event = positions_svc.create_lifecycle_event(
        position_id=posid,
        event_type="coupon_observation",
        event_data={"observation_date": "2026-06-01"},
    )

    cancelled = positions_svc.cancel_lifecycle_event(
        lifecycle_event_id=event.event.id,
        position_id=posid,
    )

    assert cancelled.position.status == "open"


def test_cancel_lifecycle_event_rejects_invalid_cases(_db):
    pid = _make_portfolio()
    other_pid = _make_portfolio("Other")
    view_pid = _make_portfolio("View", kind="view")
    posid = _make_position(pid, product_type="SnowballOption")
    other_posid = _make_position(other_pid, product_type="SnowballOption")
    view_posid = _make_position(view_pid, product_type="SnowballOption")
    event = positions_svc.create_lifecycle_event(position_id=posid, event_type="knock_out")

    with pytest.raises(LookupError, match="Lifecycle event not found for position"):
        positions_svc.cancel_lifecycle_event(
            lifecycle_event_id=event.event.id,
            position_id=other_posid,
        )
    with pytest.raises(LookupError, match="Lifecycle event not found for source_trade_id"):
        positions_svc.cancel_lifecycle_event(
            lifecycle_event_id=event.event.id,
            source_trade_id="NOPE",
        )
    with pytest.raises(LookupError, match="Lifecycle event not found"):
        positions_svc.cancel_lifecycle_event(lifecycle_event_id=9999)
    with pytest.raises(ValueError, match="container portfolios"):
        positions_svc.cancel_lifecycle_event(
            lifecycle_event_id=event.event.id,
            position_id=view_posid,
            portfolio_id=view_pid,
        )

    positions_svc.cancel_lifecycle_event(lifecycle_event_id=event.event.id)
    with pytest.raises(ValueError, match="already cancelled"):
        positions_svc.cancel_lifecycle_event(lifecycle_event_id=event.event.id)


def test_latest_valuations_caps_at_500(_db):
    pid = _make_portfolio()
    with database.SessionLocal() as session:
        run = PositionValuationRun(
            portfolio_id=pid,
            valuation_date=datetime(2026, 5, 11),
            status="completed",
            summary={},
        )
        session.add(run)
        session.flush()
        # 600 positions, each with one valuation result
        for idx in range(600):
            pos = Position(
                portfolio_id=pid,
                underlying=f"U{idx}",
                product_type="X",
                quantity=1.0,
            )
            session.add(pos)
            session.flush()
            session.add(
                PositionValuationResult(
                    valuation_run_id=run.id,
                    position_id=pos.id,
                    ok=True,
                    price=1.0,
                )
            )
        session.commit()

    rows = positions_svc.latest_valuations(portfolio_id=pid)
    assert len(rows) == 500
    assert all(isinstance(r, PositionValuationResult) for r in rows)


def test_latest_valuations_returns_only_latest_per_position(_db):
    pid = _make_portfolio()
    posid = _make_position(pid)
    with database.SessionLocal() as session:
        run = PositionValuationRun(
            portfolio_id=pid,
            valuation_date=datetime(2026, 5, 11),
            status="completed",
            summary={},
        )
        session.add(run)
        session.flush()
        for price in (1.0, 2.0, 3.0):
            session.add(
                PositionValuationResult(
                    valuation_run_id=run.id,
                    position_id=posid,
                    ok=True,
                    price=price,
                )
            )
        session.commit()

    rows = positions_svc.latest_valuations(portfolio_id=pid)
    # only the latest result for the position is returned
    assert len(rows) == 1
    assert rows[0].price == 3.0


# ---------------------------------------------------------------------------
# View aggregates (get_positions_view / get_positions_view_from_snapshot)
# ---------------------------------------------------------------------------


def test_get_positions_view_database_no_portfolio(_db):
    """Returns an empty view when no portfolios exist; resolved_portfolio_id is None."""
    view = positions_svc.get_positions_view(portfolio_id=None)
    assert view.positions == []
    assert view.portfolio_total_count == 0
    assert view.portfolio_counts_by_product_type == {}
    assert view.resolved_portfolio_id is None
    assert view.missing_effective_date_count == 0


def test_get_positions_view_falls_back_to_first_portfolio(_db):
    """portfolio_id=None resolves to the lowest-id portfolio (legacy fallback)."""
    pid = _make_portfolio("Book")
    _make_position(pid, product_type="SnowballOption")
    view = positions_svc.get_positions_view(portfolio_id=None)
    assert view.resolved_portfolio_id == pid
    assert len(view.positions) == 1


def test_get_positions_view_counts_before_and_after_product_filter(_db):
    """portfolio_counts_by_product_type covers ALL status-filtered rows;
    counts_by_product_type only the filtered subset."""
    pid = _make_portfolio()
    _make_position(pid, product_type="SnowballOption")
    _make_position(pid, product_type="SnowballOption")
    _make_position(pid, product_type="EuropeanVanillaOption")
    view = positions_svc.get_positions_view(
        portfolio_id=pid, product_type="snowball"
    )
    assert view.portfolio_total_count == 3
    assert view.portfolio_counts_by_product_type == {
        "SnowballOption": 2,
        "EuropeanVanillaOption": 1,
    }
    assert len(view.positions) == 2
    assert view.counts_by_product_type == {"SnowballOption": 2}


def test_get_positions_view_effective_window_missing_count(_db):
    """Positions without trade_effective_date are skipped and counted when a window
    is active."""
    pid = _make_portfolio()
    _make_position(
        pid, product_type="X",
        trade_effective_date=datetime(2026, 5, 1),
    )
    _make_position(pid, product_type="X")  # missing trade_effective_date
    view = positions_svc.get_positions_view(
        portfolio_id=pid,
        effective_date_from=date(2026, 4, 15),
        effective_date_to=date(2026, 5, 31),
    )
    assert len(view.positions) == 1
    assert view.missing_effective_date_count == 1


def test_get_positions_view_filters_dict_round_trip(_db):
    """Resolved filters dict normalises dates to ISO strings and echoes the
    resolved portfolio_id."""
    pid = _make_portfolio()
    view = positions_svc.get_positions_view(
        portfolio_id=pid,
        product_type="snowball",
        status="open",
        effective_date_from="2026-04-01",
        effective_date_to="2026-05-01",
        effective_last_days=5,
    )
    assert view.filters["portfolio_id"] == pid
    assert view.filters["product_type"] == "snowball"
    assert view.filters["status"] == "open"
    assert view.filters["effective_last_days"] == 5
    # effective_last_days narrows end -> start; the resolved window is ISO
    assert view.filters["effective_date_to"] is not None
    assert view.filters["effective_date_from"] is not None


def test_get_positions_view_from_snapshot_filters_and_counts():
    rows = [
        {"product_type": "SnowballOption", "underlying": "A",
         "trade_effective_date": "2026-05-01"},
        {"product_type": "SnowballOption", "underlying": "B",
         "trade_effective_date": "2026-04-01"},
        {"product_type": "EuropeanVanillaOption", "underlying": "C"},
    ]
    view = positions_svc.get_positions_view_from_snapshot(
        rows,
        product_type="snowball",
        effective_date_from=date(2026, 4, 15),
        effective_date_to=date(2026, 5, 31),
    )
    assert len(view.positions) == 1
    assert view.positions[0]["underlying"] == "A"
    assert view.counts_by_product_type == {"SnowballOption": 1}
    # The "B" row was filtered out by date; "C" never matched the product_type
    # so it doesn't contribute to the missing-effective-date count.
    assert view.missing_effective_date_count == 0
    assert view.portfolio_total_count is None
    assert view.portfolio_counts_by_product_type is None


# ---------------------------------------------------------------------------
# Step 3 — import helpers (delegation)
# ---------------------------------------------------------------------------


def test_import_from_xlsx_delegates(_db):
    pid = _make_portfolio()
    sentinel = object()
    with patch(
        "app.services.domains.positions.position_adapter.import_positions_from_xlsx",
        return_value=sentinel,
    ) as mocked:
        result = positions_svc.import_from_xlsx(
            portfolio_id=pid, xlsx_path="/tmp/x.xlsx"
        )
    assert result is sentinel
    args, kwargs = mocked.call_args
    assert isinstance(args[0], Session)
    assert kwargs["portfolio_id"] == pid
    assert str(kwargs["xlsx_path"]) == "/tmp/x.xlsx"
    assert kwargs["sheet_name"] == positions_svc.TRADE_SHEET

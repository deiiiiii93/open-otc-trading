from app.services.reporting import blocks  # noqa: F401 - registers producers
from app.services.reporting.contracts import BlockContext
from app.services.reporting.registry import get_block, resolve_block


def test_all_five_blocks_are_registered_with_declared_shapes():
    assert get_block("rfq.open_pipeline").shape.value == "rows"
    assert get_block("rfq.pending_approvals").shape.value == "rows"
    assert get_block("positions.changes").shape.value == "rows"
    assert get_block("positions.barrier_proximity").shape.value == "items"
    assert get_block("audit.write_actions_summary").shape.value == "rows"


def test_open_pipeline_lists_live_rfqs(monkeypatch):
    from app.services.reporting.blocks import desk

    monkeypatch.setattr(desk, "_load_open_rfqs", lambda ctx: [
        {"rfq_id": 12, "client_name": "Acme", "status": "priced",
         "created_at": "2026-08-06T09:00:00", "latest_price": 34.19},
    ])
    result = resolve_block("rfq.open_pipeline", BlockContext(portfolio_id=1))
    assert result.status == "ok"
    assert result.data["rows"][0]["rfq_id"] == 12
    assert result.data["total_count"] == 1


def test_open_pipeline_with_no_live_rfqs_is_empty(monkeypatch):
    from app.services.reporting.blocks import desk

    monkeypatch.setattr(desk, "_load_open_rfqs", lambda ctx: [])
    result = resolve_block("rfq.open_pipeline", BlockContext(portfolio_id=1))
    assert result.status == "empty"


def test_positions_changes_reports_added_and_removed(monkeypatch):
    from app.services.reporting.blocks import desk

    monkeypatch.setattr(desk, "_load_run_pair_metrics", lambda ctx: (
        {"positions": [{"position_id": 1, "underlying": "AAPL", "market_value": 1.0}],
         "valuation_as_of": "2026-08-04T00:00:00"},
        {"positions": [{"position_id": 2, "underlying": "TSLA", "market_value": 2.0}],
         "valuation_as_of": "2026-08-05T00:00:00"},
        {"risk_run_id": 36, "compare_to_run_id": 35},
    ))
    result = resolve_block("positions.changes",
                           BlockContext(portfolio_id=1, compare_to_run_id=35))
    assert result.status == "ok"
    assert [row["position_id"] for row in result.data["added"]] == [2]
    assert [row["position_id"] for row in result.data["removed"]] == [1]
    assert result.data["added_count"] == 1


def test_positions_changes_with_identical_membership_is_empty(monkeypatch):
    from app.services.reporting.blocks import desk

    same = {"positions": [{"position_id": 1, "underlying": "AAPL", "market_value": 1.0}],
            "valuation_as_of": "2026-08-04T00:00:00"}
    monkeypatch.setattr(desk, "_load_run_pair_metrics",
                        lambda ctx: (same, same, {"risk_run_id": 36}))
    result = resolve_block("positions.changes",
                           BlockContext(portfolio_id=1, compare_to_run_id=35))
    assert result.status == "empty"


def test_barrier_proximity_sorts_nearest_first(monkeypatch):
    from app.services.reporting.blocks import desk

    monkeypatch.setattr(desk, "_load_barrier_states", lambda ctx: [
        {"position_id": 5, "underlying": "TSLA", "nearest_barrier_kind": "knock_out",
         "nearest_barrier_level": 120.0, "nearest_barrier_date": "2026-09-01",
         "days_to_nearest": 26},
        {"position_id": 4, "underlying": "AAPL", "nearest_barrier_kind": "knock_in",
         "nearest_barrier_level": 80.0, "nearest_barrier_date": "2026-08-10",
         "days_to_nearest": 4},
    ])
    result = resolve_block("positions.barrier_proximity", BlockContext(portfolio_id=1))
    assert result.status == "ok"
    assert [item["position_id"] for item in result.data["items"]] == [4, 5]


def test_write_actions_summary_aggregates_and_counts_denials(monkeypatch):
    from app.services.reporting.blocks import desk

    monkeypatch.setattr(desk, "_load_write_actions", lambda ctx, window_days: [
        {"tool_name": "book_position", "kind": "execution", "status": "ok",
         "actor": "desk_user", "persona": "trader"},
        {"tool_name": "book_position", "kind": "execution", "status": "ok",
         "actor": "desk_user", "persona": "trader"},
        {"tool_name": "book_hedge", "kind": "execution", "status": "denied",
         "actor": "desk_user", "persona": "risk_manager"},
    ])
    result = resolve_block("audit.write_actions_summary",
                           BlockContext(portfolio_id=1, params={"window_days": 1}))
    assert result.status == "ok"
    assert result.data["total_actions"] == 3
    assert result.data["denied_count"] == 1
    booked = next(r for r in result.data["rows"] if r["tool_name"] == "book_position")
    assert booked["count"] == 2
    assert result.data["window_days"] == 1


def test_write_actions_summary_with_no_activity_is_empty(monkeypatch):
    from app.services.reporting.blocks import desk

    monkeypatch.setattr(desk, "_load_write_actions", lambda ctx, window_days: [])
    result = resolve_block("audit.write_actions_summary", BlockContext(portfolio_id=1))
    assert result.status == "empty"
    assert "no write-class" in result.reason.lower()

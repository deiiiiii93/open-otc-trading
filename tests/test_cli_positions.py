from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import database
from app.cli import main as cli_main
from app.config import Settings
from app.models import (
    Portfolio,
    Position,
    PositionValuationResult,
    PositionValuationRun,
)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _make_portfolio(name: str = "Book") -> int:
    with database.SessionLocal() as session:
        p = Portfolio(name=name, kind="container", base_currency="CNY")
        session.add(p)
        session.commit()
        return p.id


def _add_position(
    portfolio_id: int,
    *,
    underlying: str = "AAPL",
    product_type: str = "EuropeanVanillaOption",
    status: str = "open",
    quantity: float = 1.0,
    trade_effective_date: datetime | None = None,
) -> int:
    with database.SessionLocal() as session:
        pos = Position(
            portfolio_id=portfolio_id,
            underlying=underlying,
            product_type=product_type,
            quantity=quantity,
            status=status,
            trade_effective_date=trade_effective_date,
        )
        session.add(pos)
        session.commit()
        return pos.id


def _run(*argv: str) -> dict:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main(list(argv))
    assert rc == 0, buf.getvalue()
    return json.loads(buf.getvalue())


def test_list_positions_by_portfolio():
    pid = _make_portfolio("Book")
    _add_position(pid, underlying="AAPL")
    _add_position(pid, underlying="MSFT", product_type="SnowballOption")
    out = _run("positions", "list", "--portfolio", "Book")
    assert out["portfolio_id"] == pid
    assert out["total_count"] == 2
    underlyings = {p["underlying"] for p in out["positions"]}
    assert underlyings == {"AAPL", "MSFT"}


def test_list_positions_filters_by_product_type():
    pid = _make_portfolio("Book")
    _add_position(pid, underlying="AAPL")
    _add_position(pid, underlying="MSFT", product_type="SnowballOption")
    out = _run(
        "positions", "list", "--portfolio", "Book", "--product-type", "Snowball"
    )
    assert out["total_count"] == 1
    assert out["positions"][0]["underlying"] == "MSFT"


def test_list_positions_unknown_portfolio_errors():
    buf = io.StringIO()
    err = io.StringIO()
    # CliRunner-style: route through cli_main and confirm nonzero exit code.
    from contextlib import redirect_stderr

    with redirect_stdout(buf), redirect_stderr(err):
        rc = cli_main(["positions", "list", "--portfolio", "Nope"])
    assert rc == 2
    assert "Portfolio not found" in err.getvalue()


def test_count_positions():
    pid = _make_portfolio("Book")
    _add_position(pid, underlying="AAPL")
    _add_position(pid, underlying="MSFT")
    out = _run("positions", "count", "--portfolio", "Book")
    assert out == {"portfolio_id": pid, "count": 2}


def test_latest_valuations_empty():
    pid = _make_portfolio("Book")
    out = _run("positions", "latest-valuations", "--portfolio", "Book")
    assert out["portfolio_id"] == pid
    assert out["found"] is False
    assert out["results"] == []


def test_latest_valuations_returns_rows():
    pid = _make_portfolio("Book")
    pos_id = _add_position(pid, underlying="AAPL")
    with database.SessionLocal() as session:
        run = PositionValuationRun(
            portfolio_id=pid,
            status="completed",
            summary={"priced": 1},
        )
        session.add(run)
        session.flush()
        session.add(
            PositionValuationResult(
                valuation_run_id=run.id,
                position_id=pos_id,
                source_trade_id="T-1",
                ok=True,
                price=1.23,
                market_value=12.3,
                pnl=0.5,
                error=None,
            )
        )
        session.commit()
    out = _run("positions", "latest-valuations", "--portfolio", "Book")
    assert out["found"] is True
    assert out["total_count"] == 1
    assert out["results"][0]["position_id"] == pos_id
    assert out["results"][0]["price"] == 1.23


def test_import_invokes_service_and_creates_portfolio(tmp_path):
    xlsx = tmp_path / "trades.xlsx"
    xlsx.write_bytes(b"")
    fake_batch = SimpleNamespace(
        id=1,
        row_count=2,
        imported_count=2,
        supported_count=2,
        unsupported_count=0,
        error_count=0,
        status="completed",
    )
    with patch(
        "app.services.domains.positions.position_adapter.import_positions_from_xlsx",
        return_value=fake_batch,
    ) as fn:
        out = _run(
            "positions",
            "import",
            "--xlsx",
            str(xlsx),
            "--portfolio",
            "NewBook",
        )
    assert fn.called
    assert out["imported_count"] == 2
    assert out["status"] == "completed"
    # Portfolio created on the fly with default CNY base currency
    with database.SessionLocal() as session:
        p = session.query(Portfolio).filter(Portfolio.name == "NewBook").one()
        assert p.base_currency == "CNY"
        assert out["portfolio_id"] == p.id



def test_positions_help_lists_typer_commands():
    """positions --help should be routed to the Typer app, not legacy argparse."""
    from contextlib import redirect_stderr

    buf = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        try:
            rc = cli_main(["positions", "--help"])
        except SystemExit as exc:
            rc = int(exc.code or 0)
    assert rc == 0
    text = buf.getvalue() + err.getvalue()
    assert "list" in text
    assert "import" in text
    assert "count" in text


def test_positions_price_still_goes_to_legacy():
    """The price subcommand should still be handled by the legacy argparse parser.

    A bare invocation without required args should fail with the legacy
    parser's exit code, not raise NoSuchCommand from Typer.
    """
    buf = io.StringIO()
    err = io.StringIO()
    from contextlib import redirect_stderr

    with redirect_stdout(buf), redirect_stderr(err):
        try:
            rc = cli_main(["positions", "price"])
        except SystemExit as exc:
            rc = int(exc.code or 0)
    # argparse exits with code 2 on missing required argument.
    assert rc == 2

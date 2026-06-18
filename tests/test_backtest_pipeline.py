"""Network-free unit tests for the backtest domain pipeline (Task 2.5).

These exercise the pure helpers (risk metrics, portfolio aggregation, hedge
resolution, result shaping) without touching akshare, the DB, or the engines.
"""
from __future__ import annotations

from app.services.domains import backtest as bt


# ---------------------------------------------------------------------------
# _risk_metrics
# ---------------------------------------------------------------------------

def test_risk_metrics_known_series():
    m = bt._risk_metrics([0.0, 100.0, 50.0, 200.0, 150.0])
    assert m["max_drawdown"] >= 50.0  # peak 200 -> 150 dd within series; and 100->50
    assert set(m) == {"sharpe", "max_drawdown", "var_95", "cvar_95"}


def test_risk_metrics_too_short():
    assert bt._risk_metrics([1.0]) == {
        "sharpe": 0.0,
        "max_drawdown": 0.0,
        "var_95": 0.0,
        "cvar_95": 0.0,
    }


# ---------------------------------------------------------------------------
# _aggregate_portfolio
# ---------------------------------------------------------------------------

def test_aggregate_two_underlyings():
    per = [
        {"summary": {"total_pnl": 100.0, "hedge_pnl": 10.0, "num_trades": 3}},
        {"summary": {"total_pnl": -40.0, "hedge_pnl": 5.0, "num_trades": 2}},
    ]
    agg = bt._aggregate_portfolio(per)
    assert agg["total_pnl"] == 60.0 and agg["num_trades"] == 5
    assert agg["hedge_pnl"] == 15.0
    assert agg["num_underlyings"] == 2


def test_aggregate_empty():
    agg = bt._aggregate_portfolio([])
    assert agg["total_pnl"] == 0.0 and agg["num_trades"] == 0
    assert agg["num_underlyings"] == 0


# ---------------------------------------------------------------------------
# _resolve_hedge (uses hedging_universe — no network)
# ---------------------------------------------------------------------------

def test_resolve_hedge_index_future():
    # CSI 500 → IC index future, ¥200 multiplier
    h = bt._resolve_hedge("000905", "index")
    assert h == {"kind": "futures", "prefix": "IC", "multiplier": 200.0}


def test_resolve_hedge_csi300_future():
    h = bt._resolve_hedge("000300", "index")
    assert h["kind"] == "futures" and h["prefix"] == "IF" and h["multiplier"] == 300.0


def test_resolve_hedge_unknown_falls_back_to_spot():
    h = bt._resolve_hedge("SOME_UNKNOWN_SYM", None)
    assert h == {"kind": "spot"}


def test_resolve_hedge_commodity_falls_back_to_spot():
    # Commodity families have no fixed multiplier → conservative spot fallback.
    h = bt._resolve_hedge("RB2610", "commodity")
    assert h == {"kind": "spot"}


# ---------------------------------------------------------------------------
# _engine_name (pure alias map; no quant-ark import)
# ---------------------------------------------------------------------------

def test_engine_name_aliases():
    assert bt._engine_name("quad") == "QUADRATURE"
    assert bt._engine_name("mc") == "MONTE_CARLO"
    assert bt._engine_name("pde") == "PDE"
    assert bt._engine_name(None) == "QUADRATURE"
    assert bt._engine_name("nonsense") == "QUADRATURE"


# ---------------------------------------------------------------------------
# _downsample / _align_portfolio_pnl
# ---------------------------------------------------------------------------

def test_downsample_keeps_last():
    rows = [{"i": i} for i in range(1000)]
    out = bt._downsample(rows, cap=250)
    assert len(out) <= 252
    assert out[-1] == {"i": 999}


def test_align_portfolio_pnl_sums_by_date():
    per = [
        {"pnl_series": [{"date": "2024-01-02", "total_pnl": 10.0},
                        {"date": "2024-01-03", "total_pnl": 20.0}]},
        {"pnl_series": [{"date": "2024-01-02", "total_pnl": 5.0},
                        {"date": "2024-01-03", "total_pnl": 7.0}]},
    ]
    out = bt._align_portfolio_pnl(per)
    assert out == [
        {"date": "2024-01-02", "total_pnl": 15.0},
        {"date": "2024-01-03", "total_pnl": 27.0},
    ]


def test_align_portfolio_pnl_forward_fills_gaps():
    # Underlying B starts a day later; before its first obs it contributes 0,
    # after, its last-known cumulative value is carried.
    per = [
        {"pnl_series": [{"date": "d1", "total_pnl": 100.0},
                        {"date": "d2", "total_pnl": 110.0}]},
        {"pnl_series": [{"date": "d2", "total_pnl": 50.0}]},
    ]
    out = bt._align_portfolio_pnl(per)
    assert out == [
        {"date": "d1", "total_pnl": 100.0},
        {"date": "d2", "total_pnl": 160.0},
    ]


# ---------------------------------------------------------------------------
# _shape_underlying with a fake results object (no engine / no quant-ark)
# ---------------------------------------------------------------------------

class _FakeResults:
    """Minimal stand-in exposing the df METHODS _shape_underlying reads."""

    import pandas as _pd

    def states_df(self):
        return self._pd.DataFrame(
            {
                "date": ["2024-01-02", "2024-01-03"],
                "total_pnl": [10.0, 25.0],
                "hedge_pnl": [1.0, 2.0],
                "product_pnl": [9.0, 23.0],
            }
        )

    def greeks_df(self):
        return self._pd.DataFrame(
            {
                "date": ["2024-01-02", "2024-01-03"],
                "pre_hedge_delta": [0.5, 0.4],
                "post_hedge_delta": [0.0, 0.0],
                "gamma": [0.01, 0.02],
                # no vega column → should be omitted gracefully
            }
        )

    def trades_df(self):
        return self._pd.DataFrame()

    def actions_df(self):
        return self._pd.DataFrame(
            {"date": ["2024-01-03"], "action_type": ["KO"], "cashflow": [123.0]}
        )

    def daily_event_summary_df(self):
        return self._pd.DataFrame(
            {"date": ["2024-01-02", "2024-01-03"],
             "survival_probability": [0.9, 0.8]}
        )

    def event_probability_df(self):
        return self._pd.DataFrame()

    def surfaces_df(self):
        return self._pd.DataFrame()

    def get_summary(self):
        return {"total_pnl": 25.0, "hedge_pnl": 2.0, "num_trades": 1, "num_days": 2}


class _FakeHedge:
    kind = "spot"
    multiplier = 1.0


class _FakeConfig:
    underlying = "000905"
    hedge = _FakeHedge()
    products = [object(), object()]


def test_shape_underlying_maps_columns():
    shaped = bt._shape_underlying(_FakeConfig(), _FakeResults())
    assert shaped["underlying"] == "000905"
    assert shaped["num_products"] == 2
    assert shaped["summary"]["total_pnl"] == 25.0
    # pnl_series picks total/hedge/product pnl
    assert shaped["pnl_series"][0] == {
        "date": "2024-01-02", "total_pnl": 10.0, "hedge_pnl": 1.0, "product_pnl": 9.0
    }
    # greeks: pre/post delta + gamma present, vega absent
    g0 = shaped["greeks_series"][0]
    assert g0["net_delta_pre"] == 0.5 and g0["net_delta_post"] == 0.0
    assert g0["gamma"] == 0.01 and "vega" not in g0
    # lifecycle events from actions
    assert shaped["lifecycle_events"] == [
        {"type": "KO", "date": "2024-01-03", "cashflow": 123.0}
    ]
    # event summary = latest daily row
    assert shaped["event_summary"]["survival_probability"] == 0.8
    assert shaped["hedge_instrument"]["kind"] == "spot"


def test_shape_underlying_aggregates_into_portfolio():
    shaped = bt._shape_underlying(_FakeConfig(), _FakeResults())
    agg = bt._aggregate_portfolio([shaped, shaped])
    assert agg["total_pnl"] == 50.0
    assert agg["num_trades"] == 2


def test_dashboard_adapter_exposes_first_book_product_for_quantark_dashboard():
    class _BookProduct:
        product = object()

    class _BookConfig:
        underlying = "000905"
        products = [_BookProduct()]

    class _BookResults:
        config = _BookConfig()

    adapter_config = bt._DashboardAdapter(_BookResults()).config

    assert adapter_config.product is _BookConfig.products[0].product
    assert adapter_config.underlying == "000905"

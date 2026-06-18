from app.models import BacktestRun, TaskKind, TaskRun, TaskStatus


def test_backtest_run_defaults():
    run = BacktestRun(portfolio_id=1, status=TaskStatus.QUEUED.value,
                      spec={"start": "2024-01-02", "end": "2024-04-30", "engine": "quad"})
    assert run.spec["engine"] == "quad"
    assert TaskKind.BACKTEST.value == "backtest"
    assert hasattr(TaskRun, "backtest_run_id")


def test_backtest_run_table_registered():
    assert "backtest_runs" in BacktestRun.metadata.tables

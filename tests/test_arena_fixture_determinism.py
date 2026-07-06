"""Offline, clean-DB determinism gate for the flagship arena producers (Spec A).

Drives risk / landscape / scenario / backtest twice from independent clean DBs
with the market-data provider disabled; the canonical payloads must be identical.
This is the guard that keeps harvested fixture truth reproducible — any live
fetch or wall-clock dependence on the golden path fails it loudly.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from app.golden_workflows.determinism import seed_flagship, drive_producers


@pytest.fixture
def offline_session_factory(tmp_path: Path):
    """Return a factory producing a fresh, clean, isolated DB per call."""
    from app import database
    from app.config import Settings

    counter = {"n": 0}

    @contextmanager
    def factory():
        counter["n"] += 1
        n = counter["n"]
        settings = Settings(
            database_url=f"sqlite+pysqlite:///{tmp_path / f'det{n}.sqlite3'}",
            artifact_dir=tmp_path / f"art{n}",
            agent_checkpoint_db_path=":memory:",
        )
        database.configure_database(settings)
        database.init_db()
        with database.SessionLocal() as s:
            yield s

    return factory


@pytest.fixture
def block_network(monkeypatch):
    """Patch the AkShare fetch entrypoints to hard-fail, so any live market-data
    fetch on the golden path raises instead of leaking environment data."""
    def _raise(*_a, **_k):
        raise RuntimeError("network disabled in determinism gate")

    from app.services import backtest_market_history as hist
    monkeypatch.setattr(hist, "_fetch_akshare_spot", _raise)
    monkeypatch.setattr(hist, "_fetch_akshare_futures_contract", _raise)


def test_producers_are_reproducible(offline_session_factory, block_network):
    with offline_session_factory() as s1:
        first = drive_producers(s1, seed_flagship(s1))
    with offline_session_factory() as s2:
        second = drive_producers(s2, seed_flagship(s2))
    assert first == second, "flagship producers drifted across identical seeds"


def test_offline_guard_trips_without_seeded_history(offline_session_factory, block_network):
    """Without the seeded backtest history, driving the backtest offline must FAIL
    loudly — either ensure_spot_history raises (network disabled) and propagates,
    or the swallowed empty result trips _strict_backtest. Guards against certifying
    a hollow backtest (Codex plan-review [high])."""
    from app.golden_workflows.determinism import (
        _drive_backtest, _strict_backtest, _no_async_dispatch,
    )
    from app.golden_workflows.fixtures import apply_seed
    from app.golden_workflows.registry import get_workflow_bundle

    with offline_session_factory() as s:
        # Seed the base fixtures ONLY (no seed_backtest_history), so the backtest
        # has no stored history and must attempt a (blocked) live fetch.
        ids = apply_seed(get_workflow_bundle("risk-manager-control-day").fixtures, s)
        s.commit()
        pid = ids["portfolios"]["control"]
        prof = ids["pricing_profiles"]["prof"]
        with pytest.raises((RuntimeError, AssertionError)):
            with _no_async_dispatch():
                _strict_backtest(_drive_backtest(s, pid, prof))


def test_harvest_matches_payloads_and_is_idempotent(offline_session_factory, block_network):
    from app.golden_workflows.harvest_fixtures import harvest, TARGETS
    from app.golden_workflows.assertions import _dig

    with offline_session_factory() as s:
        truth1 = harvest(s)
    with offline_session_factory() as s:
        truth2 = harvest(s)
    assert truth1 == truth2, "harvest not idempotent across identical seeds"
    assert set(truth1) == {t[0] for t in TARGETS}

    with offline_session_factory() as s:
        payloads = drive_producers(s, seed_flagship(s))
    for name, producer, path in TARGETS:
        ok, val = _dig(payloads[producer], path)
        assert ok, f"{name}: path {path} did not resolve"
        assert truth1[name]["value"] == float(val)


def test_harvest_raises_on_unresolved_target(offline_session_factory, block_network, monkeypatch):
    from app.golden_workflows import harvest_fixtures as hf
    monkeypatch.setattr(hf, "TARGETS", [("bogus", "risk", "does.not.exist")])
    with offline_session_factory() as s:
        with pytest.raises(RuntimeError):
            hf.harvest(s)

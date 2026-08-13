"""Offline, clean-DB determinism gate for the flagship arena producers (Spec A).

Drives risk / landscape / scenario / backtest twice from independent clean DBs
with the market-data provider disabled; the canonical payloads must be identical.
This is the guard that keeps harvested fixture truth reproducible — any live
fetch or wall-clock dependence on the golden path fails it loudly.
"""
from __future__ import annotations

import pytest

from app.golden_workflows.determinism import seed_flagship, drive_producers

# `offline_session_factory` and `block_network` are now shared fixtures in
# tests/conftest.py (also consumed by the high-board workflow tests).


def test_producers_are_reproducible(offline_session_factory, block_network):
    with offline_session_factory() as s1:
        first = drive_producers(s1, seed_flagship(s1))
    with offline_session_factory() as s2:
        second = drive_producers(s2, seed_flagship(s2))
    assert first == second, "flagship producers drifted across identical seeds"


def test_registry_flagship_matches_legacy_drive(offline_session_factory, block_network):
    from app.golden_workflows.determinism import (
        DETERMINISM_REGISTRY, FLAGSHIP_ID, seed_workflow, drive_producers,
    )
    assert FLAGSHIP_ID in DETERMINISM_REGISTRY
    with offline_session_factory() as s:
        via_registry = drive_producers(s, seed_workflow(s, FLAGSHIP_ID),
                                       workflow_id=FLAGSHIP_ID)
    assert set(via_registry) == {"risk", "landscape", "scenario", "backtest"}
    assert via_registry["risk"]["positions"]  # non-empty priced payload


def test_trader_rfq_quote_is_reproducible(offline_session_factory, block_network):
    from app.golden_workflows.determinism import (
        TRADER_RFQ_ID, seed_workflow, drive_producers,
    )
    with offline_session_factory() as s1:
        first = drive_producers(s1, seed_workflow(s1, TRADER_RFQ_ID), workflow_id=TRADER_RFQ_ID)
    with offline_session_factory() as s2:
        second = drive_producers(s2, seed_workflow(s2, TRADER_RFQ_ID), workflow_id=TRADER_RFQ_ID)
    assert first == second, "trader-rfq quote drifted across identical seeds"
    assert isinstance(first["quote"]["achieved_price"], (int, float))


def test_trader_rfq_quote_tracks_spot(offline_session_factory, block_network):
    """Parity: the harvested number is coupled to the live quote inputs, not hand-built.
    Changing the pinned spot must change the harvested achieved_price."""
    from app.golden_workflows.determinism import (
        TRADER_RFQ_ID, seed_workflow, _drive_quote_rfq,
    )
    with offline_session_factory() as s:
        ids = seed_workflow(s, TRADER_RFQ_ID)
        _, base = _drive_quote_rfq(s, ids)
    with offline_session_factory() as s:
        ids = seed_workflow(s, TRADER_RFQ_ID)
        _, bumped = _drive_quote_rfq(s, ids, spot=120.0)
    assert base["achieved_price"] != bumped["achieved_price"]


def test_offline_guard_trips_without_seeded_history(offline_session_factory, block_network):
    """Without the seeded backtest history, driving the backtest offline must FAIL
    loudly — either ensure_spot_history raises (network disabled) and propagates,
    or the swallowed empty result trips _strict_backtest. Guards against certifying
    a hollow backtest (Codex plan-review [high])."""
    from app.golden_workflows.determinism import (
        _drive_backtest, _require_complete, _no_async_dispatch,
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
                run, results = _drive_backtest(s, pid, prof)
                _require_complete(run, results, kind="backtest", needs="by_underlying")


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


def test_committed_truth_file_is_current(offline_session_factory, block_network):
    """The committed truth.json must equal a fresh harvest — otherwise objective
    grounding (Spec B) anchors to stale numbers while the determinism gate stays
    green (Codex code-review [high]). Re-run harvest_fixtures to refresh."""
    import json
    from app.golden_workflows.harvest_fixtures import harvest, TRUTH_PATH

    committed = json.loads(TRUTH_PATH.read_text())
    with offline_session_factory() as s:
        fresh = harvest(s)
    assert committed == fresh, (
        "risk-manager-control-day.truth.json is stale — run "
        "`python -m app.golden_workflows.harvest_fixtures`")


def test_high_board_risk_is_reproducible(offline_session_factory, block_network):
    """The high-board consume-only risk producer must reproduce the HARVESTED
    numbers byte-for-byte across identical clean seeds (block_network would raise
    on a spot fetch). Three market-evidence *hash* fields (position_set_hash,
    market_evidence_hash, effective_market_evidence_id) are known-volatile and NOT
    stripped by `_canonical` — the same pre-existing gap the flagship
    `test_producers_are_reproducible` hits — so they are excluded here; the
    grounding truth (per-position Greeks + valuation) must be identical."""
    from app.golden_workflows.determinism import (
        HIGH_BOARD_ID, seed_workflow, drive_producers,
    )

    def _strip(payload):
        risk = {k: v for k, v in payload["risk"].items() if k != "position_set_hash"}
        meta = dict(risk.get("source_metadata") or {})
        meta.pop("effective_market_evidence_id", None)
        meta.pop("market_evidence_hash", None)
        risk["source_metadata"] = meta
        return {**payload, "risk": risk}

    with offline_session_factory() as s1:
        first = drive_producers(s1, seed_workflow(s1, HIGH_BOARD_ID),
                                workflow_id=HIGH_BOARD_ID)
    with offline_session_factory() as s2:
        second = drive_producers(s2, seed_workflow(s2, HIGH_BOARD_ID),
                                 workflow_id=HIGH_BOARD_ID)
    assert _strip(first) == _strip(second)
    assert first["risk"]["positions"]


def test_limit_breach_producers_are_reproducible(offline_session_factory, block_network):
    """risk-limit-breach-day: breach/fresh risk + the live monitoring path must
    yield byte-identical canonical payloads across independent clean DBs."""
    from app.golden_workflows.determinism import (
        LIMIT_BREACH_ID,
        drive_producers,
        seed_workflow,
    )

    results = []
    for _ in range(2):
        with offline_session_factory() as session:
            ids = seed_workflow(session, LIMIT_BREACH_ID)
            results.append(drive_producers(session, ids, workflow_id=LIMIT_BREACH_ID))
    assert set(results[0]) == {"breach_risk", "fresh_risk", "monitoring"}
    assert results[0] == results[1]


def test_limit_breach_committed_truth_file_is_current(offline_session_factory, block_network):
    """The committed truth.json must equal a fresh harvest (staleness gate)."""
    import json

    from app.golden_workflows import harvest_fixtures as hf
    from app.golden_workflows.determinism import LIMIT_BREACH_ID

    truth_path = hf._DEFN / hf.HARVEST_SPECS[LIMIT_BREACH_ID][0]
    committed = json.loads(truth_path.read_text())
    with offline_session_factory() as session:
        fresh = hf.harvest_for(session, LIMIT_BREACH_ID)
    assert committed == fresh


def test_ops_settlement_producers_are_reproducible(offline_session_factory, block_network):
    """ops-settlement-day: the settlement driver must yield byte-identical
    canonical payloads across independent clean DBs."""
    from app.golden_workflows.determinism import (
        OPS_SETTLEMENT_ID,
        drive_producers,
        seed_workflow,
    )

    results = []
    for _ in range(2):
        with offline_session_factory() as session:
            ids = seed_workflow(session, OPS_SETTLEMENT_ID)
            results.append(drive_producers(session, ids, workflow_id=OPS_SETTLEMENT_ID))
    assert set(results[0]) == {"settlement"}
    assert results[0] == results[1]


def test_ops_settlement_committed_truth_file_is_current(offline_session_factory, block_network):
    """The committed truth.json must equal a fresh harvest (staleness gate)."""
    import json

    from app.golden_workflows import harvest_fixtures as hf
    from app.golden_workflows.determinism import OPS_SETTLEMENT_ID

    truth_path = hf._DEFN / hf.HARVEST_SPECS[OPS_SETTLEMENT_ID][0]
    committed = json.loads(truth_path.read_text())
    with offline_session_factory() as session:
        fresh = hf.harvest_for(session, OPS_SETTLEMENT_ID)
    assert committed == fresh

"""Per-model reasoning-effort capabilities and the vendored models.dev snapshot.

These tests deliberately assert STRUCTURE and INVARIANTS, never a specific
model's current ladder. models.dev is a live upstream, so "gpt-5.5 accepts
low/medium/high" is a moving target; "every effort in the snapshot is covered by
our outer bound" is not.
"""
from __future__ import annotations

import json

import pytest

from app.services.deep_agent import reasoning_capabilities as rc
from app.services.deep_agent.model_factory import VALID_REASONING_EFFORTS


@pytest.fixture(autouse=True)
def _restore_snapshot():
    yield
    rc.reload_snapshot()


# ---------------------------------------------------------------------------
# The vendored snapshot
# ---------------------------------------------------------------------------


def test_snapshot_is_present_and_well_formed():
    rc.reload_snapshot()
    data = json.loads(rc._SNAPSHOT_PATH.read_text())
    assert data["source"] == "https://models.dev/api.json"
    assert len(data["source_sha256"]) == 64
    assert data["fetched_at"]
    routes = data["routes"]
    # Both desk channels must be represented, or their models silently become
    # "unknown" and per-model validation quietly stops applying.
    assert {"zenmux", "deepseek"} <= set(routes)
    for channel, models in routes.items():
        assert models, f"route {channel} is empty"
        for model_id, entry in models.items():
            assert isinstance(entry["reasoning"], bool), model_id
            assert isinstance(entry["efforts"], list), model_id
            assert isinstance(entry["toggle"], bool), model_id


def test_outer_bound_covers_every_effort_the_snapshot_declares():
    """Drift guard, and the reason the outer bound exists at all.

    `VALID_REASONING_EFFORTS` is applied to models the snapshot does NOT know. If
    upstream starts declaring a token we have never heard of, that token is both
    (a) legitimate for some model and (b) rejected by our typo guard for every
    unknown model. Failing here is the signal to widen the bound.
    """
    rc.reload_snapshot()
    data = json.loads(rc._SNAPSHOT_PATH.read_text())
    seen = {
        effort
        for models in data["routes"].values()
        for entry in models.values()
        for effort in entry["efforts"]
    }
    unknown = seen - set(VALID_REASONING_EFFORTS)
    assert not unknown, (
        f"models.dev declares effort levels absent from VALID_REASONING_EFFORTS: "
        f"{sorted(unknown)} — widen the outer bound (and the UI options)."
    )


def test_snapshot_records_ladders_that_actually_differ():
    """Guards the premise of the whole feature: if every model shared one ladder,
    a hardcoded list would have been fine and this module would be dead weight."""
    rc.reload_snapshot()
    data = json.loads(rc._SNAPSHOT_PATH.read_text())
    ladders = {
        tuple(entry["efforts"])
        for models in data["routes"].values()
        for entry in models.values()
        if entry["reasoning"]
    }
    assert len(ladders) > 1, f"expected varied ladders, saw {ladders}"
    # And at least one reasoning model with NO ladder (toggle-only), which is the
    # case a hardcoded list cannot express at all.
    assert any(
        entry["reasoning"] and not entry["efforts"] and entry["toggle"]
        for models in data["routes"].values()
        for entry in models.values()
    )


# ---------------------------------------------------------------------------
# Lookup semantics
# ---------------------------------------------------------------------------


def test_unknown_route_and_model_are_None_not_unsupported():
    """None means "no data", which callers treat permissively. Conflating it with
    "unsupported" would let a snapshot that lags a release block a working model."""
    rc.reload_snapshot({"routes": {"zenmux": {"a/b": {
        "reasoning": True, "efforts": ["high"], "toggle": False}}}})
    assert rc.effort_support("zenmux", "a/b") is not None
    assert rc.effort_support("zenmux", "nope/nope") is None
    assert rc.effort_support("no-such-channel", "a/b") is None
    assert rc.rejection_reason("zenmux", "nope/nope", "anything") is None


def test_rejection_reasons_name_the_declared_ladder():
    m = {"source": "measured"}
    rc.reload_snapshot({"routes": {"zenmux": {
        "narrow": {"reasoning": True, "efforts": ["low", "high"], "toggle": False, **m},
        "toggler": {"reasoning": True, "efforts": [], "toggle": True, **m},
        "plain": {"reasoning": False, "efforts": [], "toggle": False, **m},
        # Same narrow ladder but UNMEASURED — must never reject.
        "unverified": {"reasoning": True, "efforts": ["low"], "toggle": False},
    }}})
    assert rc.rejection_reason("zenmux", "narrow", "high") is None
    assert "['low', 'high']" in rc.rejection_reason("zenmux", "narrow", "medium")
    assert "on/off toggle" in rc.rejection_reason("zenmux", "toggler", "high")
    assert "not a reasoning model" in rc.rejection_reason("zenmux", "plain", "high")
    # An unmeasured ladder is advisory only: models.dev was wrong for 13 of 22
    # probed routes, so gating on it would deny levels that work.
    assert rc.rejection_reason("zenmux", "unverified", "max") is None


def test_a_missing_snapshot_degrades_to_permissive_not_to_a_crash(monkeypatch, tmp_path):
    """The desk must not go down because a vendored data file is absent."""
    monkeypatch.setattr(rc, "_SNAPSHOT_PATH", tmp_path / "absent.json")
    rc.reload_snapshot()
    assert rc.effort_support("zenmux", "openai/gpt-5.5") is None
    assert rc.rejection_reason("zenmux", "openai/gpt-5.5", "high") is None


def test_a_corrupt_snapshot_degrades_to_permissive(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    monkeypatch.setattr(rc, "_SNAPSHOT_PATH", bad)
    rc.reload_snapshot()
    assert rc.effort_support("zenmux", "openai/gpt-5.5") is None


def test_provenance_is_surfaced():
    rc.reload_snapshot()
    prov = rc.snapshot_provenance()
    assert prov["source"].startswith("https://")
    assert len(prov["source_sha256"]) == 64


def test_provider_pinned_route_falls_back_to_unpinned_ladder():
    """A `:provider` pin is a new route key; it must not silently go permissive.

    ZenMux pins an upstream with a `model:provider` suffix (2026-08-25), which is a
    DIFFERENT `(channel, model_id)` key from the unpinned twin. Falling through to
    "unknown" would drop every measured ladder we own and make the gate permissive
    for the whole field at once — a gate that looks present and is not.
    """
    from app.services.deep_agent import reasoning_capabilities as rc
    rc.reload_snapshot({"routes": {"zenmux": {
        "vendor/model": {"reasoning": True, "efforts": ["low", "high"],
                         "source": "measured"},
        "vendor/pinned:special": {"reasoning": True, "efforts": ["max"],
                                  "source": "measured"},
    }}})
    try:
        # unpinned entry is inherited by the pinned id
        got = rc.effort_support("zenmux", "vendor/model:someprovider")
        assert got is not None and list(got.efforts) == ["low", "high"]
        # a probed pinned entry still wins over its base
        rc.reload_snapshot({"routes": {"zenmux": {
            "vendor/pinned": {"reasoning": True, "efforts": ["low"],
                              "source": "measured"},
            "vendor/pinned:special": {"reasoning": True, "efforts": ["max"],
                                      "source": "measured"},
        }}})
        got = rc.effort_support("zenmux", "vendor/pinned:special")
        assert got is not None and list(got.efforts) == ["max"]
        # a genuinely unknown model stays unknown (permissive), suffix or not
        assert rc.effort_support("zenmux", "vendor/nothing:special") is None
    finally:
        rc.reload_snapshot(None)

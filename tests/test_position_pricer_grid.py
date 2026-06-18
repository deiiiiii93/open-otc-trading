from __future__ import annotations

from app.services.position_pricer import (
    _engine_kwargs_with_grid,
    _resolve_grid_chain,
)


def test_resolve_grid_chain_returns_adaptive_chain_for_quad_engines_without_grid():
    assert _resolve_grid_chain({"params_type": "quad_params"}) == [201, 501, 1001]
    assert _resolve_grid_chain(
        {"params_type": "quad_params", "params_kwargs": {}}
    ) == [201, 501, 1001]


def test_resolve_grid_chain_returns_single_none_for_non_quad_engines():
    """Non-quad engines must NOT receive grid_points injection — return [None]
    so the chain loop runs a single attempt with verbatim engine_kwargs."""
    assert _resolve_grid_chain(None) == [None]
    assert _resolve_grid_chain({}) == [None]
    assert _resolve_grid_chain({"params_kwargs": {}}) == [None]
    assert _resolve_grid_chain({"params_type": "mc_params"}) == [None]
    assert _resolve_grid_chain({"params_type": "engine_params"}) == [None]


def test_resolve_grid_chain_honors_explicit_grid_points_on_quad():
    chain = _resolve_grid_chain(
        {"params_type": "quad_params", "params_kwargs": {"grid_points": 501}}
    )
    assert chain == [501]


def test_resolve_grid_chain_ignores_grid_points_on_non_quad_engines():
    """A non-quad engine with a stray grid_points value should still fall through
    to [None] — we don't trust the value to be meaningful for that engine."""
    chain = _resolve_grid_chain(
        {"params_type": "mc_params", "params_kwargs": {"grid_points": 501}}
    )
    assert chain == [None]


def test_engine_kwargs_with_grid_creates_params_kwargs_when_missing():
    out = _engine_kwargs_with_grid({"params_type": "quad_params"}, 201)
    assert out == {"params_type": "quad_params", "params_kwargs": {"grid_points": 201}}


def test_engine_kwargs_with_grid_preserves_other_params_kwargs_keys():
    inp = {
        "params_type": "quad_params",
        "params_kwargs": {"grid_points": 1001, "num_std_devs": 5},
    }
    out = _engine_kwargs_with_grid(inp, 201)
    assert out["params_kwargs"]["grid_points"] == 201
    assert out["params_kwargs"]["num_std_devs"] == 5


def test_engine_kwargs_with_grid_does_not_mutate_input():
    inp = {"params_type": "quad_params", "params_kwargs": {"grid_points": 1001}}
    snapshot = {"params_type": "quad_params", "params_kwargs": {"grid_points": 1001}}
    _engine_kwargs_with_grid(inp, 201)
    assert inp == snapshot


def test_engine_kwargs_with_grid_handles_none_input():
    out = _engine_kwargs_with_grid(None, 201)
    assert out == {"params_kwargs": {"grid_points": 201}}


def test_engine_kwargs_with_grid_preserves_top_level_keys_other_than_params():
    inp = {"params_type": "quad_params", "extra": "keep-me"}
    out = _engine_kwargs_with_grid(inp, 501)
    assert out["extra"] == "keep-me"
    assert out["params_kwargs"]["grid_points"] == 501


def test_engine_kwargs_with_grid_returns_verbatim_copy_when_grid_is_none():
    inp = {"params_type": "engine_params", "params_kwargs": {"foo": 1}}
    out = _engine_kwargs_with_grid(inp, None)
    assert out == inp
    out["mutated"] = True
    assert "mutated" not in inp

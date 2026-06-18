from app.services import backtest_bridge


class _Pos:
    def __init__(self, id, underlying, quantity=1.0):
        self.id = id
        self.underlying = underlying
        self.quantity = quantity


def test_group_positions_by_underlying():
    groups = backtest_bridge.group_by_underlying(
        [_Pos(1, "CSI500"), _Pos(2, "CSI500"), _Pos(3, "CSI300")])
    assert set(groups) == {"CSI500", "CSI300"}
    assert {p.id for p in groups["CSI500"]} == {1, 2}


def test_has_lifecycle_by_type_name():
    class SnowballOption: ...
    class PhoenixOption: ...
    class KnockOutResetSnowballOption: ...
    class EuropeanVanillaOption: ...
    assert backtest_bridge._has_lifecycle(SnowballOption()) is True
    assert backtest_bridge._has_lifecycle(PhoenixOption()) is True
    assert backtest_bridge._has_lifecycle(KnockOutResetSnowballOption()) is True
    assert backtest_bridge._has_lifecycle(EuropeanVanillaOption()) is False


def test_has_analytical_engine_by_type_name():
    class EuropeanVanillaOption: ...
    class SingleSharkfinOption: ...
    class RangeAccrualOption: ...
    assert backtest_bridge._has_analytical_engine(EuropeanVanillaOption()) is True
    assert backtest_bridge._has_analytical_engine(SingleSharkfinOption()) is True
    assert backtest_bridge._has_analytical_engine(RangeAccrualOption()) is False


def test_build_books_excludes_missing_history():
    """Positions whose underlying has no entry in history are excluded (network-free)."""
    positions = [_Pos(10, "CSI500"), _Pos(11, "CSI300")]
    history = {}  # no history for any underlying
    # build_books will call ensure_quantark_path but won't reach QuantArk imports
    # because the missing-history exclusion happens before the product-build loop.
    configs, excluded = backtest_bridge.build_books(
        None, positions, history, engine_type=None)
    assert configs == []
    assert {e["position_id"] for e in excluded} == {10, 11}
    assert all("no market history" in e["reason"] for e in excluded)


def test_build_books_excludes_zero_quantity(monkeypatch):
    """A zero-quantity position is excluded even if history is present (network-free)."""
    import pandas as pd

    # Provide dummy history so the missing-history branch is not triggered.
    dummy_df = pd.DataFrame()
    dummy_hedge = object()
    history = {"CSI500": (dummy_df, dummy_df, dummy_df, dummy_df, dummy_hedge)}

    # Stub out quantark calls so no QuantArk import is needed.
    import app.services.quantark as qk
    monkeypatch.setattr(qk, "ensure_quantark_path", lambda: None)
    monkeypatch.setattr(qk, "risk_pricing_exclusion", lambda pos: None)

    positions = [_Pos(20, "CSI500", quantity=0.0)]
    configs, excluded = backtest_bridge.build_books(
        None, positions, history, engine_type=None)
    assert configs == []
    assert len(excluded) == 1
    assert excluded[0]["position_id"] == 20
    assert "zero" in excluded[0]["reason"]


def test_build_books_splits_products_by_engine_family(monkeypatch):
    import pandas as pd

    dummy_df = pd.DataFrame()
    dummy_hedge = object()
    history = {"CSI500": (dummy_df, dummy_df, dummy_df, dummy_df, dummy_hedge)}

    class SnowballOption: ...
    class EuropeanVanillaOption: ...

    positions = [_Pos(30, "CSI500"), _Pos(31, "CSI500")]

    import app.services.quantark as qk
    monkeypatch.setattr(qk, "ensure_quantark_path", lambda: None)
    monkeypatch.setattr(qk, "risk_pricing_exclusion", lambda pos: None)
    monkeypatch.setattr(
        qk,
        "build_product_for_position",
        lambda pos: SnowballOption() if pos.id == 30 else EuropeanVanillaOption(),
    )

    import quantark.backtest.otc as otc

    class _FakeBookProduct:
        def __init__(self, **kw):
            self.position_id = kw["position_id"]

    class _FakeConfig:
        def __init__(self, **kw):
            self.products = kw["products"]
            self.engine_config = kw["engine_config"]

    class _FakeEngineConfig:
        def __init__(self, **kw):
            self.pricing_engine_type = kw["pricing_engine_type"]

    class _FakeMDS:
        @staticmethod
        def from_dataframes(**kw): return object()

    monkeypatch.setattr(otc, "BookProduct", _FakeBookProduct)
    monkeypatch.setattr(otc, "BookAutocallableBacktestConfig", _FakeConfig)
    monkeypatch.setattr(otc, "AutocallableEngineConfig", _FakeEngineConfig)
    monkeypatch.setattr(otc, "AutocallableMarketDataSet", _FakeMDS)

    configs, excluded = backtest_bridge.build_books(
        None,
        positions,
        history,
        engine_types={"autocallable": "AUTO", "other": "OTHER"},
    )

    assert excluded == []
    assert len(configs) == 2
    by_engine = {cfg.engine_config.pricing_engine_type: cfg for cfg in configs}
    assert [p.position_id for p in by_engine["AUTO"].products] == [30]
    assert [p.position_id for p in by_engine["OTHER"].products] == [31]


def test_build_books_uses_fallback_for_other_products_without_analytical(monkeypatch):
    import pandas as pd

    dummy_df = pd.DataFrame()
    dummy_hedge = object()
    history = {"CSI500": (dummy_df, dummy_df, dummy_df, dummy_df, dummy_hedge)}

    class RangeAccrualOption: ...
    class _Analytical:
        name = "ANALYTICAL"

    positions = [_Pos(40, "CSI500")]

    import app.services.quantark as qk
    monkeypatch.setattr(qk, "ensure_quantark_path", lambda: None)
    monkeypatch.setattr(qk, "risk_pricing_exclusion", lambda pos: None)
    monkeypatch.setattr(qk, "build_product_for_position", lambda pos: RangeAccrualOption())

    import quantark.backtest.otc as otc

    class _FakeBookProduct:
        def __init__(self, **kw):
            self.position_id = kw["position_id"]

    class _FakeConfig:
        def __init__(self, **kw):
            self.products = kw["products"]
            self.engine_config = kw["engine_config"]

    class _FakeEngineConfig:
        def __init__(self, **kw):
            self.pricing_engine_type = kw["pricing_engine_type"]

    class _FakeMDS:
        @staticmethod
        def from_dataframes(**kw): return object()

    monkeypatch.setattr(otc, "BookProduct", _FakeBookProduct)
    monkeypatch.setattr(otc, "BookAutocallableBacktestConfig", _FakeConfig)
    monkeypatch.setattr(otc, "AutocallableEngineConfig", _FakeEngineConfig)
    monkeypatch.setattr(otc, "AutocallableMarketDataSet", _FakeMDS)

    configs, excluded = backtest_bridge.build_books(
        None,
        positions,
        history,
        engine_types={"autocallable": "AUTO", "other": _Analytical(), "fallback": "PDE"},
    )

    assert excluded == []
    assert len(configs) == 1
    assert configs[0].engine_config.pricing_engine_type == "PDE"


def test_coerce_engine_type_maps_resolved_vocab_directly():
    """The engine-config resolution speaks the backtest vocabulary
    (quad/analytical/pde/mc); _coerce_engine_type must map each word to its
    EngineType — never leak the spec slot dict back to the caller."""
    import pytest

    from quantark.util.enum.engine_enums import EngineType

    engine_types = {
        "autocallable": EngineType.QUADRATURE,
        "other": EngineType.ANALYTICAL,
        "fallback": EngineType.PDE,
    }
    assert backtest_bridge._coerce_engine_type("quad", engine_types) is EngineType.QUADRATURE
    assert backtest_bridge._coerce_engine_type("analytical", engine_types) is EngineType.ANALYTICAL
    assert backtest_bridge._coerce_engine_type("pde", engine_types) is EngineType.PDE
    assert backtest_bridge._coerce_engine_type("mc", engine_types) is EngineType.MONTE_CARLO
    with pytest.raises(ValueError):
        backtest_bridge._coerce_engine_type("hyperdrive", engine_types)

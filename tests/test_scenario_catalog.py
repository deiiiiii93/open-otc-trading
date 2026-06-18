import pytest

from app.services.quantark import ensure_quantark_path


@pytest.fixture(autouse=True)
def _quantark_on_path():
    ensure_quantark_path()


from app.services.domains import scenario_catalog


def test_list_predefined_includes_market_crash():
    names = {s["name"] for s in scenario_catalog.list_predefined()}
    assert "Market Crash" in names
    assert any("1987" in n or "Black Monday" in n for n in names)


def test_build_custom_spot_and_vol():
    spec = {
        "name": "My Shock",
        "stresses": [
            {"param": "spot", "stress_type": "PERCENTAGE", "value": -0.2, "level": "portfolio"},
            {"param": "vol", "stress_type": "ABSOLUTE", "value": 0.05, "level": "portfolio"},
        ],
    }
    scenario = scenario_catalog.build_custom(spec)
    assert scenario.name == "My Shock"
    assert len(scenario.stresses) == 2


def test_build_custom_rejects_unknown_param():
    spec = {"name": "bad", "stresses": [{"param": "spread", "value": 0.01}]}
    with pytest.raises(ValueError, match="param"):
        scenario_catalog.build_custom(spec)


def test_resolve_scenarios_predefined_plus_custom():
    request = {
        "predefined": ["market_crash"],
        "custom": [{"name": "C1", "stresses": [{"param": "spot", "value": -0.1}]}],
    }
    scenarios = scenario_catalog.resolve_scenarios(request)
    assert len(scenarios) == 2
    assert {s.name for s in scenarios} >= {"C1"}


def test_save_and_load_set(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    s = scenario_catalog.build_custom(
        {"name": "S1", "stresses": [{"param": "spot", "value": -0.15}]})
    path = scenario_catalog.save_set("my_set", [s])
    assert path.endswith(".yaml")
    assert "my_set" in scenario_catalog.list_sets()
    loaded = scenario_catalog.load_set("my_set")
    assert loaded[0].name == "S1"


def test_build_custom_rejects_position_level():
    # position-level targeting is unsupported in v1 (DB ids != QuantArk UUIDs).
    spec = {
        "name": "P",
        "stresses": [
            {"param": "spot", "value": -0.1, "level": "position", "target": 42}
        ],
    }
    with pytest.raises(ValueError, match="position-level"):
        scenario_catalog.build_custom(spec)


def test_build_custom_underlying_requires_target():
    # underlying-level without a target would silently become portfolio-wide.
    spec = {"name": "U", "stresses": [{"param": "spot", "value": -0.1, "level": "underlying"}]}
    with pytest.raises(ValueError, match="underlying-level"):
        scenario_catalog.build_custom(spec)


def test_build_custom_rejects_unknown_level():
    spec = {"name": "X", "stresses": [{"param": "spot", "value": -0.1, "level": "galaxy"}]}
    with pytest.raises(ValueError, match="level"):
        scenario_catalog.build_custom(spec)


def test_build_custom_rejects_bad_stress_type():
    # bad stress_type must raise ValueError (-> REST 400), not KeyError (-> 500).
    spec = {"name": "B", "stresses": [{"param": "spot", "value": -0.1, "stress_type": "bogus"}]}
    with pytest.raises(ValueError, match="stress_type"):
        scenario_catalog.build_custom(spec)


def test_list_predefined_includes_stress_legs():
    entry = next(s for s in scenario_catalog.list_predefined() if s["key"] == "market_crash")
    assert "stresses" in entry
    assert len(entry["stresses"]) == entry["num_stresses"]
    params = {leg["param"] for leg in entry["stresses"]}
    assert {"spot", "vol"} <= params


def test_serialize_scenario_predefined_market_crash():
    from quantark.stresstest.scenario.scenario_library import ScenarioLibrary
    data = scenario_catalog.serialize_scenario(ScenarioLibrary.market_crash())
    legs = {s["param"]: s for s in data["stresses"]}
    assert data["name"] == "Market Crash"
    assert legs["spot"]["stress_type"] == "PERCENTAGE"
    assert legs["spot"]["value"] == pytest.approx(-0.2)
    assert legs["vol"]["param"] == "vol"
    assert legs["vol"]["value"] == pytest.approx(0.5)
    assert legs["spot"]["level"] == "portfolio"


def test_serialize_scenario_round_trips_build_custom():
    spec = {
        "name": "RT",
        "description": "round trip",
        "stresses": [
            {"param": "dividend", "stress_type": "ABSOLUTE", "value": 0.01, "level": "portfolio"},
            {"param": "vol", "stress_type": "PERCENTAGE", "value": 0.3,
             "level": "underlying", "target": "000300.SH"},
        ],
    }
    data = scenario_catalog.serialize_scenario(scenario_catalog.build_custom(spec))
    legs = {s["param"]: s for s in data["stresses"]}
    assert legs["dividend"]["param"] == "dividend"
    assert legs["vol"]["level"] == "underlying"
    assert legs["vol"]["target"] == "000300.SH"
    rebuilt = scenario_catalog.build_custom({"name": "RT2", "stresses": data["stresses"]})
    assert len(rebuilt.stresses) == 2


def test_get_set_and_list_sets_detailed(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    s = scenario_catalog.build_custom({
        "name": "Mild",
        "description": "mild selloff",
        "stresses": [{"param": "spot", "stress_type": "PERCENTAGE", "value": -0.1, "level": "portfolio"}],
    })
    scenario_catalog.save_set("mild_selloff", [s])
    detail = scenario_catalog.get_set("mild_selloff")
    assert detail["name"] == "mild_selloff"
    assert detail["stresses"][0]["param"] == "spot"
    assert detail["stresses"][0]["value"] == pytest.approx(-0.1)
    listed = scenario_catalog.list_sets_detailed()
    assert any(d["name"] == "mild_selloff" and d["stresses"] for d in listed)


def test_get_set_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    with pytest.raises(ValueError, match="not found"):
        scenario_catalog.get_set("nope")


def test_delete_set_removes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    s = scenario_catalog.build_custom({"name": "D", "stresses": [{"param": "spot", "value": -0.1}]})
    scenario_catalog.save_set("to_delete", [s])
    assert "to_delete" in scenario_catalog.list_sets()
    scenario_catalog.delete_set("to_delete")
    assert "to_delete" not in scenario_catalog.list_sets()


def test_delete_set_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    with pytest.raises(ValueError, match="not found"):
        scenario_catalog.delete_set("ghost")


def test_list_set_specs_returns_all_scenarios(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    s1 = scenario_catalog.build_custom({"name": "A", "stresses": [{"param": "spot", "value": -0.1}]})
    s2 = scenario_catalog.build_custom({"name": "B", "stresses": [{"param": "vol", "value": 0.2}]})
    scenario_catalog.save_set("multi", [s1, s2])
    specs = scenario_catalog.list_set_specs("multi")
    assert len(specs) == 2
    assert {sp["stresses"][0]["param"] for sp in specs} == {"spot", "vol"}


def test_get_set_reports_num_scenarios(tmp_path, monkeypatch):
    # get_set surfaces the true scenario count so the UI can refuse to edit
    # (and thereby overwrite/drop) a multi-scenario set.
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    one = scenario_catalog.build_custom({"name": "S", "stresses": [{"param": "spot", "value": -0.1}]})
    scenario_catalog.save_set("single", [one])
    assert scenario_catalog.get_set("single")["num_scenarios"] == 1
    s1 = scenario_catalog.build_custom({"name": "A", "stresses": [{"param": "spot", "value": -0.1}]})
    s2 = scenario_catalog.build_custom({"name": "B", "stresses": [{"param": "vol", "value": 0.2}]})
    scenario_catalog.save_set("multi", [s1, s2])
    assert scenario_catalog.get_set("multi")["num_scenarios"] == 2


def test_list_sets_detailed_excludes_multi_scenario_sets(tmp_path, monkeypatch):
    # The flat-model UI list only shows single-scenario sets; multi-scenario sets
    # are agent/API-managed and excluded so the UI never shows a partial view.
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    single = scenario_catalog.build_custom({"name": "S", "stresses": [{"param": "spot", "value": -0.1}]})
    scenario_catalog.save_set("solo", [single])
    s1 = scenario_catalog.build_custom({"name": "A", "stresses": [{"param": "spot", "value": -0.1}]})
    s2 = scenario_catalog.build_custom({"name": "B", "stresses": [{"param": "vol", "value": 0.2}]})
    scenario_catalog.save_set("combo", [s1, s2])
    names = {d["name"] for d in scenario_catalog.list_sets_detailed()}
    assert "solo" in names
    assert "combo" not in names


def test_expand_axis_inclusive_on_grid():
    # -0.20..0.20 step 0.05 -> 9 inclusive points, no float drift.
    vals = scenario_catalog.expand_axis(-0.20, 0.20, 0.05)
    assert len(vals) == 9
    assert vals[0] == pytest.approx(-0.20)
    assert vals[-1] == pytest.approx(0.20)
    assert vals[1] == pytest.approx(-0.15)  # exact, not -0.15000000000001


def test_expand_axis_single_value_when_start_equals_stop():
    assert scenario_catalog.expand_axis(0.1, 0.1, 0.05) == [pytest.approx(0.1)]


def test_expand_axis_off_grid_stop_truncates():
    # 0..0.25 step 0.10 -> last full boundary <= stop: [0, 0.1, 0.2]
    vals = scenario_catalog.expand_axis(0.0, 0.25, 0.10)
    assert [round(v, 4) for v in vals] == [0.0, 0.1, 0.2]


def test_expand_axis_wrong_sign_step_raises():
    with pytest.raises(ValueError, match="sign"):
        scenario_catalog.expand_axis(0.0, 0.2, -0.05)


def test_expand_axis_zero_step_raises():
    with pytest.raises(ValueError, match="step"):
        scenario_catalog.expand_axis(0.0, 0.2, 0.0)


def test_generate_grid_cross_product_count_and_stresses():
    spec = {
        "name": "spot_vol_grid",
        "combine_mode": "cross_product",
        "axes": [
            {"param": "spot", "start": -0.20, "stop": 0.20, "step": 0.10,
             "stress_type": "PERCENTAGE", "level": "portfolio"},
            {"param": "vol", "start": 0.0, "stop": 0.20, "step": 0.10,
             "stress_type": "PERCENTAGE", "level": "portfolio"},
        ],
    }
    specs = scenario_catalog.generate_grid(spec)
    assert len(specs) == 5 * 3  # spot{-.2,-.1,0,.1,.2} x vol{0,.1,.2}
    # every cell shocks BOTH params
    assert all({s["param"] for s in cell["stresses"]} == {"spot", "vol"} for cell in specs)
    # a known cell exists with the real values (not defaults)
    names = {cell["name"] for cell in specs}
    assert any("spot" in n and "vol" in n for n in names)

    # the corner cell (spot -0.20, vol +0.20) exists with values carried through
    # unscaled (fractions) — non-default numbers, per the real-value test lesson.
    def _val(cell, param):
        return next(st["value"] for st in cell["stresses"] if st["param"] == param)
    assert any(
        _val(c, "spot") == pytest.approx(-0.20) and _val(c, "vol") == pytest.approx(0.20)
        for c in specs
    )


def test_generate_grid_carries_level_and_target():
    spec = {
        "name": "name_spot_ladder",
        "axes": [
            {"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1,
             "stress_type": "PERCENTAGE", "level": "underlying", "target": "000852.SH"},
        ],
    }
    specs = scenario_catalog.generate_grid(spec)
    assert len(specs) == 3
    st = specs[0]["stresses"][0]
    assert st["level"] == "underlying"
    assert st["target"] == "000852.SH"


def test_generate_grid_rejects_duplicate_param():
    spec = {"name": "dup", "axes": [
        {"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1},
        {"param": "spot", "start": 0.0, "stop": 0.2, "step": 0.1},
    ]}
    with pytest.raises(ValueError, match="[Dd]uplicate"):
        scenario_catalog.generate_grid(spec)


def test_generate_grid_rejects_unknown_param():
    spec = {"name": "bad", "axes": [{"param": "spread", "start": 0, "stop": 1, "step": 1}]}
    with pytest.raises(ValueError, match="param"):
        scenario_catalog.generate_grid(spec)


def test_generate_grid_enforces_cap(monkeypatch):
    # Settings is a frozen dataclass, so patch the name scenario_catalog imported
    # (it calls get_settings().scenario_grid_max_cells) rather than mutating it.
    class _Stub:
        scenario_grid_max_cells = 8
    monkeypatch.setattr(scenario_catalog, "get_settings", lambda: _Stub())
    spec = {"name": "big", "axes": [
        {"param": "spot", "start": 0.0, "stop": 1.0, "step": 0.1},   # 11 points
    ]}
    with pytest.raises(ValueError, match="cap"):
        scenario_catalog.generate_grid(spec)


def test_generate_grid_rejects_bad_combine_mode():
    spec = {"name": "x", "combine_mode": "union",
            "axes": [{"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1}]}
    with pytest.raises(ValueError, match="combine_mode"):
        scenario_catalog.generate_grid(spec)


def test_save_set_writes_sidecar_and_read_meta_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    grid = {"name": "g", "combine_mode": "cross_product",
            "axes": [{"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1,
                      "stress_type": "PERCENTAGE", "level": "portfolio"}]}
    specs = scenario_catalog.generate_grid(grid)
    scenarios = [scenario_catalog.build_custom(s) for s in specs]
    scenario_catalog.save_set("g", scenarios, grid_spec=grid)
    assert (tmp_path / "g.set.json").exists()
    meta = scenario_catalog.read_set_meta("g")
    assert meta["kind"] == "grid"
    assert meta["combine_mode"] == "cross_product"
    assert meta["axes"][0]["param"] == "spot"
    assert meta["count"] == 3


def test_read_set_meta_none_when_no_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    s = scenario_catalog.build_custom({"name": "S", "stresses": [{"param": "spot", "value": -0.1}]})
    scenario_catalog.save_set("plain", [s])  # no grid_spec -> no sidecar
    assert scenario_catalog.read_set_meta("plain") is None


def test_delete_set_removes_sidecar_too(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    grid = {"name": "g", "axes": [{"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1}]}
    scenarios = [scenario_catalog.build_custom(s) for s in scenario_catalog.generate_grid(grid)]
    scenario_catalog.save_set("g", scenarios, grid_spec=grid)
    scenario_catalog.delete_set("g")
    assert not (tmp_path / "g.yaml").exists()
    assert not (tmp_path / "g.set.json").exists()


def test_list_sets_full_includes_grid_and_legacy_multi(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    # 1) a generated grid (has sidecar)
    grid = {"name": "grid_a", "axes": [
        {"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1},
        {"param": "vol", "start": 0.0, "stop": 0.2, "step": 0.1}]}
    scenario_catalog.save_set("grid_a",
        [scenario_catalog.build_custom(s) for s in scenario_catalog.generate_grid(grid)],
        grid_spec=grid)
    # 2) a legacy multi-scenario set (no sidecar, count 2)
    s1 = scenario_catalog.build_custom({"name": "A", "stresses": [{"param": "spot", "value": -0.1}]})
    s2 = scenario_catalog.build_custom({"name": "B", "stresses": [{"param": "vol", "value": 0.2}]})
    scenario_catalog.save_set("legacy_multi", [s1, s2])
    # 3) a single custom scenario (must NOT appear as a Set)
    solo = scenario_catalog.build_custom({"name": "S", "stresses": [{"param": "spot", "value": -0.1}]})
    scenario_catalog.save_set("solo", [solo])

    full = {d["name"]: d for d in scenario_catalog.list_sets_full()}
    assert "grid_a" in full and full["grid_a"]["has_grid"] is True
    assert full["grid_a"]["num_scenarios"] == 9  # spot{-.1,0,.1} x vol{0,.1,.2}
    assert full["grid_a"]["axes_summary"] == "spot × vol"
    assert "legacy_multi" in full and full["legacy_multi"]["has_grid"] is False
    assert "solo" not in full


def test_list_sets_detailed_excludes_sidecar_grids(tmp_path, monkeypatch):
    # A 1-cell grid is a Set (sidecar present) and must NOT leak into the
    # single-custom list even though it holds exactly one scenario.
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    grid = {"name": "one_cell", "axes": [{"param": "spot", "start": 0.1, "stop": 0.1, "step": 0.1}]}
    scenario_catalog.save_set("one_cell",
        [scenario_catalog.build_custom(s) for s in scenario_catalog.generate_grid(grid)],
        grid_spec=grid)
    solo = scenario_catalog.build_custom({"name": "S", "stresses": [{"param": "spot", "value": -0.1}]})
    scenario_catalog.save_set("solo", [solo])
    names = {d["name"] for d in scenario_catalog.list_sets_detailed()}
    assert "solo" in names
    assert "one_cell" not in names

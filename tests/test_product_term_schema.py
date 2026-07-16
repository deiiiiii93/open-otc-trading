from __future__ import annotations

import pytest

from app.services.domains.product_contracts import (
    FieldSpec,
    contract_for,
    one_of_groups,
    required_fields,
    resolve_enum_values,
)
from app.services.domains.product_builders import build_product


# --- Task 1: FieldSpec + enum resolver ---------------------------------------
def test_resolve_enum_values_from_literal():
    spec = FieldSpec(input_name="ki_convention", kind="enum",
                     description="x", enum_values=("DAILY", "EUROPEAN", "NONE"))
    assert resolve_enum_values(spec) == ("DAILY", "EUROPEAN", "NONE")


def test_resolve_enum_values_from_quantark_enum_ref():
    spec = FieldSpec(input_name="barrier_type", kind="enum",
                     description="x", enum_ref="BarrierType")
    assert resolve_enum_values(spec) == ("UP_IN", "UP_OUT", "DOWN_IN", "DOWN_OUT")


def test_resolve_enum_values_non_enum_is_empty():
    spec = FieldSpec(input_name="strike", kind="number", description="x")
    assert resolve_enum_values(spec) == ()


# --- Task 2: maturity one_of -------------------------------------------------
def test_barrier_builds_with_maturity_date_only():
    r = build_product("BarrierOption", {
        "initial_price": 100.0, "strike": 100.0, "barrier": 80.0,
        "option_type": "PUT", "barrier_type": "DOWN_IN", "maturity_date": "2027-07-15"})
    assert r.ok, r.validation


def test_completeness_accepts_maturity_date_only():
    from app.tools.term_completeness import check_term_completeness
    out = check_term_completeness.func(
        "BarrierOption",
        {"initial_price": 100.0, "strike": 100.0, "barrier": 80.0,
         "maturity_date": "2027-07-15"})
    assert "maturity_years" not in out["missing_required"]
    assert out["complete"] is True


def test_barrier_rejects_both_maturity_representations():
    r = build_product("BarrierOption", {
        "initial_price": 100.0, "strike": 100.0, "barrier": 80.0,
        "option_type": "PUT", "barrier_type": "DOWN_IN",
        "maturity_years": 1.0, "maturity_date": "2027-07-15"})
    assert not r.ok
    assert "maturity" in ((r.validation or {}).get("error") or "").lower()


def test_completeness_maturity_date_does_not_satisfy_deferred_family():
    from app.tools.term_completeness import check_term_completeness
    out = check_term_completeness.func(
        "SnowballOption",
        {"initial_price": 100.0, "strike": 100.0, "maturity_date": "2027-07-15",
         "trade_start_date": "2026-07-15", "observation_frequency": "MONTHLY",
         "barrier_config": {"ko_barrier": 103.0, "ki_barrier": 75.0, "ko_rate": 0.15,
                            "lockup_months": 3}})
    assert "maturity_years" in out["missing_required"]


# --- Task 3: V1 field-specs + round-trip fidelity ----------------------------
V1_FAMILIES = [
    "BarrierOption", "EuropeanVanillaOption", "AmericanOption", "AsianOption",
    "CashOrNothingDigitalOption", "SingleSharkfinOption", "DoubleSharkfinOption",
    "OneTouchOption", "DoubleOneTouchOption",
]

PROBE = {
    "EuropeanVanillaOption": {"initial_price": 100.0, "maturity_years": 1.0, "strike": 100.0},
    "AmericanOption": {"initial_price": 100.0, "maturity_years": 1.0, "strike": 100.0},
    "CashOrNothingDigitalOption": {"initial_price": 100.0, "maturity_years": 1.0, "strike": 100.0, "cash_payoff": 10.0},
    "BarrierOption": {"initial_price": 100.0, "maturity_years": 1.0, "strike": 100.0, "barrier": 80.0},
    "SingleSharkfinOption": {"initial_price": 100.0, "maturity_years": 1.0, "strike": 100.0, "barrier": 120.0},
    "DoubleSharkfinOption": {"initial_price": 100.0, "maturity_years": 1.0, "strike": 100.0, "lower_barrier": 80.0, "upper_barrier": 120.0},
    "AsianOption": {"initial_price": 100.0, "maturity_years": 1.0, "strike": 100.0, "averaging_frequency": "MONTHLY"},
    "OneTouchOption": {"initial_price": 100.0, "maturity_years": 1.0, "barrier": 120.0, "cash_payoff": 10.0, "barrier_direction": "UP", "touch_type": "ONE_TOUCH"},
    "DoubleOneTouchOption": {"initial_price": 100.0, "maturity_years": 1.0, "upper_barrier": 120.0, "lower_barrier": 80.0, "cash_payoff": 10.0, "touch_type": "DOUBLE_ONE_TOUCH"},
}


@pytest.mark.parametrize("family", V1_FAMILIES)
def test_v1_family_has_fields_and_base_probe_builds(family):
    contract = contract_for(family)
    assert contract.fields, f"{family} must declare FieldSpecs"
    assert build_product(family, dict(PROBE[family])).ok


@pytest.mark.parametrize("family", V1_FAMILIES)
def test_every_advertised_required_field_is_in_probe(family):
    contract = contract_for(family)
    req_paths = set(required_fields(contract, {}))
    input_by_path = {(f.contract_path or f.input_name): f.input_name for f in contract.fields}
    for path in req_paths:
        name = input_by_path.get(path, path)
        assert name in PROBE[family] or path in PROBE[family], f"{family}: {path} not fillable"


# Some enum values need a companion numeric that differs from the base probe (e.g. a PUT
# sharkfin needs a barrier below strike). These are legitimately valid values — the schema
# does not mislead — the base probe just fixed an incompatible number.
ENUM_PROBE_OVERRIDES: dict[tuple[str, str, str], dict] = {
    ("SingleSharkfinOption", "option_type", "PUT"): {"barrier": 80.0},
}


@pytest.mark.parametrize("family", V1_FAMILIES)
def test_every_enum_value_round_trips_faithfully(family):
    contract = contract_for(family)
    for spec in contract.fields:
        if spec.kind != "enum":
            continue
        for value in resolve_enum_values(spec):
            override = ENUM_PROBE_OVERRIDES.get((family, spec.input_name, value), {})
            r = build_product(family, {**PROBE[family], spec.input_name: value, **override})
            assert r.ok, f"{family}.{spec.input_name}={value} did not build: {r.validation}"
            if spec.input_name in r.product_kwargs:
                assert str(r.product_kwargs[spec.input_name]).upper() == value.upper(), \
                    f"{family}.{spec.input_name}={value} classified as {r.product_kwargs[spec.input_name]}"


_MATURITY_ALT_FAMILIES = ["EuropeanVanillaOption", "AmericanOption",
                          "CashOrNothingDigitalOption", "BarrierOption",
                          "SingleSharkfinOption", "DoubleSharkfinOption"]


@pytest.mark.parametrize("family", _MATURITY_ALT_FAMILIES)
def test_maturity_alternative_is_faithful(family):
    base = {k: v for k, v in PROBE[family].items() if k != "maturity_years"}
    assert build_product(family, {**base, "maturity_years": 1.0}).ok
    assert build_product(family, {**base, "maturity_date": "2027-07-15"}).ok
    both = build_product(family, {**base, "maturity_years": 1.0, "maturity_date": "2027-07-15"})
    assert not both.ok


@pytest.mark.parametrize("family", ["AsianOption", "OneTouchOption", "DoubleOneTouchOption"])
def test_tenor_only_families_do_not_advertise_maturity_date(family):
    names = {f.input_name for f in contract_for(family).fields}
    assert "maturity_date" not in names


def test_asian_frequency_observation_count_is_correct():
    expected = {"DAILY": 252, "WEEKLY": 52, "MONTHLY": 12, "QUARTERLY": 4, "SEMI_ANNUAL": 2}
    spec = next(f for f in contract_for("AsianOption").fields if f.input_name == "averaging_frequency")
    for value in resolve_enum_values(spec):
        r = build_product("AsianOption", {**PROBE["AsianOption"], "averaging_frequency": value})
        assert r.ok
        assert r.product_kwargs["num_observations"] == expected[value], value


# --- Task 4: the tool --------------------------------------------------------
def _call(cls):
    from app.tools.product_term_schema import get_product_term_schema
    return get_product_term_schema.func(cls)


def test_barrier_schema_shape():
    out = _call("BarrierOption")
    names = {f["name"]: f for f in out["fields"]}
    assert names["barrier_type"]["enum_values"] == ["UP_IN", "UP_OUT", "DOWN_IN", "DOWN_OUT"]
    assert "DOWN_AND_IN" not in names["barrier_type"]["enum_values"]
    assert names["initial_price"]["required"] is True
    assert names["maturity_years"]["required"] is False
    assert names["maturity_years"]["one_of"] == "maturity"
    assert {"one_of": "maturity", "members": ["maturity_years", "maturity_date"]} in out["required_groups"]


def test_unlisted_family_returns_schema_unavailable(monkeypatch):
    # All contract families are now published; the schema_available:False fallback still guards
    # any family not in _SCHEMA_FAMILIES (or with empty fields). Exercise it via monkeypatch.
    import app.tools.product_term_schema as mod
    monkeypatch.setattr(mod, "_SCHEMA_FAMILIES", frozenset())
    out = _call("SnowballOption")
    assert out["schema_available"] is False
    assert "check_term_completeness" in out["use_instead"]


def test_unknown_class_errors():
    out = _call("NotARealOption")
    assert "error" in out and "known_classes" in out


# --- Code-review findings (Codex, Stage 6) -----------------------------------
def test_malformed_maturity_date_is_rejected():
    r = build_product("BarrierOption", {
        "initial_price": 100.0, "strike": 100.0, "barrier": 80.0,
        "option_type": "PUT", "barrier_type": "DOWN_IN", "maturity_date": "not-a-date"})
    assert not r.ok
    assert "ISO" in ((r.validation or {}).get("error") or "")


def test_expired_maturity_date_is_rejected():
    r = build_product("BarrierOption", {
        "initial_price": 100.0, "strike": 100.0, "barrier": 80.0,
        "option_type": "PUT", "barrier_type": "DOWN_IN", "maturity_date": "2000-01-01"})
    assert not r.ok
    assert "expired" in ((r.validation or {}).get("error") or "").lower()


def test_valid_future_maturity_date_builds():
    r = build_product("BarrierOption", {
        "initial_price": 100.0, "strike": 100.0, "barrier": 80.0,
        "option_type": "PUT", "barrier_type": "DOWN_IN", "maturity_date": "2099-07-15"})
    assert r.ok, r.validation


def test_clean_prebuilt_asian_termsheet_does_not_trigger_synthesis_fallback():
    # finding 1: a prebuilt Asian termsheet (carries initial_price + a validated schedule
    # but NOT maturity_years/maturity_date) must not be re-synthesized, which would drop
    # the weighted observation schedule.
    from app.services.domains.booking import _has_raw_termsheet_vocab
    prebuilt_asian = {"maturity": 1.0, "strike": 100.0, "option_type": "CALL",
                      "initial_price": 100.0, "num_observations": 2,
                      "observation_records": [{"t": 0.5}, {"t": 1.0}]}
    assert _has_raw_termsheet_vocab(prebuilt_asian) is False


def test_completeness_rejects_conflicting_maturity_members():
    from app.tools.term_completeness import check_term_completeness
    out = check_term_completeness.func(
        "BarrierOption",
        {"initial_price": 100.0, "strike": 100.0, "barrier": 80.0,
         "maturity_years": 1.0, "maturity_date": "2027-07-15"})
    assert out["complete"] is False
    assert out["conflicts"] and out["conflicts"][0]["one_of"] == "maturity"


# --- Task 5: registration ----------------------------------------------------
def test_tool_is_registered_and_available():
    from app.tools import QUANT_AGENT_TOOLS
    from app.services.agents import DEEP_AGENT_TOOL_NAMES, select_deep_agent_tools
    assert any(t.name == "get_product_term_schema" for t in QUANT_AGENT_TOOLS)
    assert "get_product_term_schema" in DEEP_AGENT_TOOL_NAMES
    assert any(getattr(t, "name", None) == "get_product_term_schema"
               for t in select_deep_agent_tools())


# =============================================================================
# V2 — nested-config + DeltaOne families
# =============================================================================
from app.services.domains.product_contracts import (  # noqa: E402
    FamilyContract,
    active_required_paths,
    flat_aliases,
    _requires_when_active,
)
from app.tools.term_completeness import check_term_completeness  # noqa: E402
from app.tools.product_term_schema import get_product_term_schema, _SCHEMA_FAMILIES  # noqa: E402

_NESTED = ["SnowballOption", "KnockOutResetSnowballOption", "PhoenixOption", "RangeAccrualOption"]
_V2_ALL = _NESTED + ["Futures", "SpotInstrument"]


def _snowball_terms(pct=False):
    b = {"ko_barrier_pct": 103, "ki_barrier_pct": 70} if pct else {"ko_barrier": 103, "ki_barrier": 70}
    return {"initial_price": 100, "maturity_years": 2, "trade_start_date": "2026-01-05",
            "observation_frequency": "MONTHLY", "ko_rate": 0.08, "lockup_months": 3,
            "ki_convention": "DAILY", **b}


# --- Task 1: FieldSpec helpers -----------------------------------------------
def test_flat_aliases_maps_contract_path_to_all_input_spellings():
    spec = FieldSpec("ko_barrier", "number", "KO", contract_path="barrier_config.ko_barrier",
                     input_aliases=("ko_barrier", "ko_barrier_pct"))
    c = FamilyContract("X", ("barrier_config.ko_barrier",), (), (), fields=(spec,))
    assert flat_aliases(c) == {"barrier_config.ko_barrier": ("ko_barrier", "ko_barrier_pct")}


def test_requires_when_negation_and_equality():
    unless_none = FieldSpec("ki_barrier", "number", "KI", requires_when=("ki_convention", "!NONE"))
    only_custom = FieldSpec("ko_observation_dates", "date", "d",
                            requires_when=("observation_frequency", "CUSTOM"))
    assert _requires_when_active(unless_none, {"ki_convention": "DAILY"}) is True
    assert _requires_when_active(unless_none, {"ki_convention": "NONE"}) is False
    assert _requires_when_active(only_custom, {"observation_frequency": "CUSTOM"}) is True
    assert _requires_when_active(only_custom, {"observation_frequency": "MONTHLY"}) is False
    assert _requires_when_active(FieldSpec("x", "number", ""), {}) is True


# --- Task 2/3: contracts + alias-aware required_fields -----------------------
def test_nested_contracts_declare_fields_with_reachable_paths():
    for fam in _NESTED:
        c = contract_for(fam)
        assert c.fields, f"{fam} must declare FieldSpecs"
        al = flat_aliases(c)
        for path in c.required_bound:
            if "." in path:
                assert path in al, f"{fam}: {path} has no FieldSpec/alias"


def test_flat_alias_satisfies_dotted_required_path():
    c = contract_for("SnowballOption")
    assert "barrier_config.ko_barrier" not in required_fields(c, {"ko_barrier": 90, "ki_convention": "NONE"})
    assert "barrier_config.ko_barrier" not in required_fields(c, {"ko_barrier_pct": 90, "ki_convention": "NONE"})


def test_requires_when_drops_ki_when_convention_none():
    c = contract_for("SnowballOption")
    assert "barrier_config.ki_barrier" not in required_fields(c, {"ko_barrier": 90, "ki_convention": "NONE"})
    assert "barrier_config.ki_barrier" in required_fields(c, {"ko_barrier": 90, "ki_convention": "DAILY"})


# --- Task 4: DeltaOne enum round-trip + completeness alias/conflict ----------
def test_deltaone_type_enum_round_trips():
    # Only the published (builder-faithful) members must round-trip; FUTURES is deliberately
    # excluded because the SpotInstrument builder rejects it (routes to the Futures family).
    spot_fields = get_product_term_schema.func("SpotInstrument")["fields"]
    dt_field = next(f for f in spot_fields if f["name"] == "deltaone_type")
    published = set(dt_field["enum_values"])
    assert published == {"STOCK", "INDEX", "ETF"} and "FUTURES" not in published
    for dt in published:
        r = build_product("SpotInstrument",
                          {"initial_price": 100.0, "underlying": "AAPL", "deltaone_type": dt},
                          prebuilt=False)
        assert r.ok, f"deltaone_type {dt} failed: {getattr(r, 'validation', r)}"


def test_completeness_accepts_flat_alias_for_nested_family():
    res = check_term_completeness.func("SnowballOption", _snowball_terms())
    assert "barrier_config.ko_barrier" not in res["missing_required"]
    assert "barrier_config.ki_barrier" not in res["missing_required"]
    assert res["complete"] is True


def test_completeness_flags_both_barrier_spellings_conflict():
    r1 = check_term_completeness.func("SnowballOption",
        {"initial_price": 100, "ko_barrier": 103, "ko_barrier_pct": 120})
    assert any(c.get("alias_conflict") == "barrier_config.ko_barrier" for c in r1["conflicts"])
    assert r1["complete"] is False
    # nested/dotted representation + a flat alias is ALSO a conflict (finding 2)
    r2 = check_term_completeness.func("SnowballOption",
        {"initial_price": 100, "barrier_config": {"ko_barrier": 103}, "ko_barrier_pct": 120})
    assert any(c.get("alias_conflict") == "barrier_config.ko_barrier" for c in r2["conflicts"])
    r3 = check_term_completeness.func("SnowballOption",
        {"initial_price": 100, "barrier_config.ko_barrier": 103, "ko_barrier": 90})
    assert any(c.get("alias_conflict") == "barrier_config.ko_barrier" for c in r3["conflicts"])


# --- Task 5: builder both-spellings guard + Phoenix without ko_rate ----------
def test_build_rejects_both_barrier_spellings_flat():
    r = build_product("SnowballOption", {**_snowball_terms(), "ko_barrier": 103, "ko_barrier_pct": 120},
                      prebuilt=False)
    assert not r.ok
    assert any("ko_barrier" in str(m) for m in (r.missing or []))


def test_build_rejects_nested_plus_flat_barrier():
    r = build_product("SnowballOption",
                      {**_snowball_terms(), "barrier_config": {"ko_barrier": 103}, "ko_barrier_pct": 120},
                      prebuilt=False)
    assert not r.ok


def test_phoenix_builds_without_ko_rate():
    terms = {k: v for k, v in _snowball_terms().items() if k != "ko_rate"}
    terms.update({"coupon_barrier": 80, "coupon_rate": 0.02})
    r = build_product("PhoenixOption", terms, prebuilt=False)
    assert r.ok, getattr(r, "validation", r)
    assert r.product_kwargs["barrier_config"]["ko_rate"] == 0.0


# --- Task 6: schema publishes families + fidelity ----------------------------
def test_schema_available_for_all_v2_families():
    for fam in _V2_ALL:
        out = get_product_term_schema.func(fam)
        assert out.get("schema_available") is not False and "fields" in out, f"{fam} not published"
    assert len(_SCHEMA_FAMILIES) == 15


def test_schema_barrier_lists_both_input_names():
    out = get_product_term_schema.func("SnowballOption")
    ko = next(f for f in out["fields"] if f["name"] == "ko_barrier")
    assert set(ko["input_names"]) == {"ko_barrier", "ko_barrier_pct"}


def test_schema_marks_conditional_fields():
    out = get_product_term_schema.func("SnowballOption")
    kod = next(f for f in out["fields"] if f["name"] == "ko_observation_dates")
    assert kod["requires_when"] == {"field": "observation_frequency", "equals": "CUSTOM"}
    assert kod["required"] is False
    ki = next(f for f in out["fields"] if f["name"] == "ki_barrier")
    assert ki["requires_when"] == {"field": "ki_convention", "not_equals": "NONE"}


def test_phoenix_builds_faithful_coupon_config_both_spellings():
    for pct in (False, True):
        terms = {**_snowball_terms(pct),
                 **({"coupon_barrier_pct": 80} if pct else {"coupon_barrier": 80}),
                 "coupon_rate": 0.02}
        r = build_product("PhoenixOption", terms, prebuilt=False)
        assert r.ok, getattr(r, "validation", r)
        cc = r.product_kwargs["coupon_config"]
        assert cc["coupon_rate"] == 0.02
        if pct:
            assert cc["coupon_barrier"] == 80.0  # 80% of 100


def test_koreset_builds_post_barrier_config():
    terms = {**_snowball_terms(), "post_ko_barrier": 105, "post_ko_rate": 0.09}
    r = build_product("KnockOutResetSnowballOption", terms, prebuilt=False)
    assert r.ok, getattr(r, "validation", r)
    assert r.product_kwargs["post_barrier_config"]["ko_barrier"] == 105


def test_range_accrual_builds_range_config_and_exact_obs_count():
    base = {"initial_price": 100, "maturity_years": 2, "lower_barrier": 90,
            "upper_barrier": 110, "accrual_rate": 0.05}
    r = build_product("RangeAccrualOption", {**base, "observation_frequency": "DAILY"}, prebuilt=False)
    assert r.ok and r.product_kwargs["range_config"]["lower_barrier"] == 90
    assert r.product_kwargs["num_observations"] == round(2 * 252)
    r2 = build_product("RangeAccrualOption", {**base, "observation_frequency": "MONTHLY"}, prebuilt=False)
    assert r2.product_kwargs["num_observations"] == round(2 * 12)
    r3 = build_product("RangeAccrualOption",
                       {"initial_price": 100, "maturity_years": 2, "accrual_rate": 0.05,
                        "lower_barrier_pct": 90, "upper_barrier_pct": 110,
                        "observation_frequency": "DAILY"}, prebuilt=False)
    assert r3.product_kwargs["range_config"]["lower_barrier"] == 90.0  # 90% of 100


def test_range_accrual_frequency_enum_is_daily_monthly_only():
    out = get_product_term_schema.func("RangeAccrualOption")
    freq = next(f for f in out["fields"] if f["name"] == "observation_frequency")
    assert set(freq["enum_values"]) == {"DAILY", "MONTHLY"}


def test_snowball_ki_dropped_when_convention_none():
    terms = {k: v for k, v in _snowball_terms().items() if k != "ki_barrier"}
    terms["ki_convention"] = "NONE"
    r = build_product("SnowballOption", terms, prebuilt=False)
    assert r.ok, getattr(r, "validation", r)
    assert "ki_barrier" not in r.product_kwargs.get("barrier_config", {})


# --- Task 7: anti-loop regression (the Run #24 Phoenix loop) -----------------
def test_schema_then_completeness_then_build_agree_for_flat_phoenix():
    get_product_term_schema.func("PhoenixOption")  # advises flat ko_barrier, coupon_barrier, ...
    terms = {"initial_price": 100, "maturity_years": 2, "trade_start_date": "2026-01-05",
             "observation_frequency": "MONTHLY", "ko_barrier": 103, "ki_barrier": 70,
             "lockup_months": 3, "ki_convention": "DAILY", "coupon_barrier": 80, "coupon_rate": 0.02}
    assert check_term_completeness.func("PhoenixOption", terms)["complete"] is True
    assert build_product("PhoenixOption", terms, prebuilt=False).ok is True

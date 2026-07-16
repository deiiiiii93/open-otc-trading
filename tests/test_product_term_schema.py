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


def test_deferred_family_returns_schema_unavailable():
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

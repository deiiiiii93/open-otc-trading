from __future__ import annotations

import pytest

from app.services.domains.product_builders import _REGISTRY, build_product


def _snowball_terms(**over):
    terms = {
        "underlying": "000905.SH",
        "currency": "CNY",
        "initial_price": 100.0,
        "maturity_years": 1.0,
        "ko_barrier_pct": 101,
        "ki_barrier_pct": 70,
        "ko_rate": 0.15,
        "ko_frequency": "MONTHLY",
        "ki_convention": "DAILY",
        "lockup_months": 3,
        "trade_start_date": "2026-01-05",
    }
    terms.update(over)
    return terms


def test_snowball_builds_and_validates():
    result = build_product("SnowballOption", _snowball_terms())
    assert result.missing == []
    assert result.engine_name == "SnowballQuadEngine"
    bc = result.product_kwargs["barrier_config"]
    assert bc["ko_barrier"] == 101.0          # 101% of initial 100
    assert bc["ki_barrier"] == 70.0           # 70% of initial 100
    assert bc["ko_observation_schedule"]["records"]  # monthly KO synthesized
    assert bc["ki_observation_schedule"]["records"]  # daily KI synthesized
    assert result.validation["ok"] is True
    assert result.ok is True


def test_snowball_missing_lockup_and_start_are_reported_not_invented():
    terms = _snowball_terms()
    terms.pop("lockup_months")
    terms.pop("trade_start_date")
    result = build_product("SnowballOption", terms)
    assert "barrier_config.lockup_months" in result.missing
    assert "trade_start_date" in result.missing
    assert result.ok is False
    # No fabricated schedule when inputs are missing
    assert "ko_observation_schedule" not in result.product_kwargs.get("barrier_config", {})


def test_snowball_missing_coupon_reported():
    terms = _snowball_terms()
    terms.pop("ko_rate")
    result = build_product("SnowballOption", terms)
    assert "barrier_config.ko_rate" in result.missing
    assert result.ok is False


def test_unknown_family_is_reported():
    result = build_product("NotARealOption", {})
    assert result.ok is False
    assert "unsupported_family" in result.warnings[0]


def test_ko_reset_snowball_builds():
    # KO-reset needs a post-KI KO-only schedule (post_barrier_config), not a flat reset_rate.
    terms = _snowball_terms(post_ko_barrier_pct=100, post_ko_rate=0.10)
    result = build_product("KnockOutResetSnowballOption", terms)
    assert result.missing == []
    assert result.engine_name == "KOResetSnowballQuadEngine"
    assert result.product_kwargs["post_barrier_config"]["ko_barrier"] == 100.0
    assert result.validation["ok"] is True


def test_ko_reset_missing_post_terms_reported():
    result = build_product("KnockOutResetSnowballOption", _snowball_terms())
    assert "post_barrier_config.ko_rate" in result.missing
    assert result.ok is False


def test_phoenix_builds():
    terms = _snowball_terms(coupon_barrier_pct=85, coupon_rate=0.01)
    result = build_product("PhoenixOption", terms)
    assert result.missing == []
    assert result.engine_name == "PhoenixQuadEngine"
    assert result.product_kwargs["coupon_config"]["coupon_rate"] == 0.01
    assert result.validation["ok"] is True


def test_phoenix_missing_coupon_reported():
    terms = _snowball_terms(coupon_barrier_pct=85)  # no coupon_rate
    result = build_product("PhoenixOption", terms)
    assert "coupon_config.coupon_rate" in result.missing
    assert result.ok is False


@pytest.mark.parametrize(
    "family,terms,engine",
    [
        ("EuropeanVanillaOption",
         {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0},
         "BlackScholesEngine"),
        ("AmericanOption",
         {"initial_price": 100.0, "strike": 100.0, "option_type": "PUT", "maturity_years": 1.0},
         "AmericanOptionAnalyticalEngine"),
        ("CashOrNothingDigitalOption",
         {"initial_price": 100.0, "strike": 100.0, "cash_payoff": 10.0,
          "option_type": "CALL", "maturity_years": 1.0},
         "DigitalOptionAnalyticalEngine"),
        ("BarrierOption",
         {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0,
          "barrier": 75.0, "barrier_type": "DOWN_OUT"},
         "BarrierAnalyticalEngine"),
        ("OneTouchOption",
         {"initial_price": 100.0, "barrier": 120.0, "cash_payoff": 10.0,
          "barrier_direction": "UP", "maturity_years": 1.0},
         "OneTouchAnalyticalEngine"),
        ("DoubleOneTouchOption",
         {"initial_price": 100.0, "upper_barrier": 120.0, "lower_barrier": 80.0,
          "cash_payoff": 10.0, "touch_type": "DOUBLE_ONE_TOUCH", "maturity_years": 1.0},
         "OneTouchAnalyticalEngine"),
        ("DoubleOneTouchOption",
         {"initial_price": 100.0, "upper_barrier": 120.0, "lower_barrier": 80.0,
          "cash_payoff": 10.0, "touch_type": "DOUBLE_NO_TOUCH", "maturity_years": 1.0},
         "OneTouchAnalyticalEngine"),
        ("Futures",
         {"initial_price": 100.0, "underlying": "000905.SH", "maturity_years": 1.0},
         "DeltaOneEngine"),
        ("SpotInstrument",
         {"initial_price": 100.0, "underlying": "000905.SH"},
         "DeltaOneEngine"),
    ],
)
def test_scalar_families_build_and_validate(family, terms, engine):
    result = build_product(family, terms)
    assert result.engine_name == engine
    assert result.missing == [], result.missing
    assert result.validation["ok"] is True, result.validation


# --- DeltaOne (Spot/Futures): persistence-only metadata is carried as _otc_ so
# it survives in stored terms (for the equity_spot/futures_products side-tables)
# but is popped before QuantArk construction. The QuantArk constructor surfaces
# are SpotInstrument{underlying, deltaone_type} and Futures{underlying, multiplier,
# maturity, maturity_date, basis, basis_decay_rate, market_price}; instrument_code/
# exchange/contract_multiplier (spot) and contract_code (futures) are NOT kwargs.
def test_spot_carries_persistence_metadata_as_otc():
    result = build_product(
        "SpotInstrument",
        {"initial_price": 4.2, "underlying": "510300.SH", "deltaone_type": "ETF",
         "instrument_code": "510300.SH", "exchange": "SSE", "contract_multiplier": 1.0},
    )
    assert result.ok is True, result.validation
    pk = result.product_kwargs
    # QuantArk kwargs stay bare; persistence metadata is _otc_-prefixed.
    assert pk["deltaone_type"] == "ETF"
    assert pk["underlying"] == "510300.SH"
    assert pk["_otc_instrument_code"] == "510300.SH"
    assert pk["_otc_exchange"] == "SSE"
    assert pk["_otc_contract_multiplier"] == 1.0
    assert "instrument_code" not in pk  # the bare kwarg QuantArk would reject
    assert "exchange" not in pk


def test_futures_carries_contract_code_as_otc_and_passes_basis():
    result = build_product(
        "Futures",
        {"initial_price": 100.0, "underlying": "IF2406.CFE", "maturity_years": 0.5,
         "contract_multiplier": 300.0, "contract_code": "IF2406.CFE",
         "basis": 2.5, "basis_decay_rate": 2.0, "market_price": 101.0},
    )
    assert result.ok is True, result.validation
    pk = result.product_kwargs
    assert pk["multiplier"] == 300.0
    assert pk["underlying"] == "IF2406.CFE"
    assert pk["maturity"] == 0.5
    assert pk["_otc_contract_code"] == "IF2406.CFE"  # not a Futures constructor kwarg
    assert "contract_code" not in pk
    # basis/basis_decay_rate/market_price ARE Futures kwargs -> passed through bare
    assert pk["basis"] == 2.5
    assert pk["basis_decay_rate"] == 2.0
    assert pk["market_price"] == 101.0


def test_deltaone_otc_metadata_does_not_block_validation():
    # The _otc_ keys must be popped before QuantArk construction at validate time.
    result = build_product(
        "SpotInstrument",
        {"initial_price": 4.2, "underlying": "510300.SH", "deltaone_type": "ETF",
         "instrument_code": "510300.SH"},
    )
    assert result.ok is True, result.validation
    assert result.validation["ok"] is True


def test_deltaone_position_prices_with_otc_metadata_stripped():
    # Regression: previously a spot position whose stored kwargs carried
    # instrument_code raised "Unsupported kwargs for SpotInstrument" at pricing.
    # With the _otc_ carry, _build_termsheet pops the metadata and construction
    # succeeds. build_product_for_position reads product_kwargs off a position-like
    # object when no ORM product is attached.
    from app.services.quantark import build_product_for_position

    class _Pos:
        product = None
        underlying = "CSI300"
        product_type = "SpotInstrument"
        engine_name = "DeltaOneEngine"
        engine_kwargs: dict = {}
        product_kwargs = {
            "deltaone_type": "ETF",
            "underlying": "CSI300",
            "_otc_instrument_code": "510300.SH",
            "_otc_exchange": "SSE",
        }

    product = build_product_for_position(_Pos())
    assert product.__class__.__name__ == "SpotInstrument"
    # _otc_ attrs are applied via setattr (not the constructor) and don't leak.
    assert getattr(product, "_otc_instrument_code") == "510300.SH"


def test_vanilla_missing_strike_reported():
    result = build_product("EuropeanVanillaOption", {"option_type": "CALL", "maturity_years": 1.0})
    assert "strike" in result.missing
    assert result.ok is False


def test_double_one_touch_missing_barriers_and_payoff_reported():
    result = build_product("DoubleOneTouchOption",
                           {"initial_price": 100.0, "maturity_years": 1.0})
    assert result.ok is False
    assert "upper_barrier" in result.missing
    assert "lower_barrier" in result.missing
    assert "cash_payoff" in result.missing


def test_double_one_touch_kwargs_match_legacy_shape_and_default_touch_type():
    result = build_product("DoubleOneTouchOption",
                           {"initial_price": 100.0, "upper_barrier": 120.0,
                            "lower_barrier": 80.0, "cash_payoff": 10.0,
                            "maturity_years": 1.0})
    assert result.ok is True, result.validation
    kwargs = result.product_kwargs
    # Exact legacy shape: rebate (not cash_payoff), no strike/option_type/multiplier,
    # no initial_price in the kwargs (it is only the validation spot).
    assert set(kwargs) == {"maturity", "upper_barrier", "lower_barrier",
                           "rebate", "touch_type"}
    assert kwargs["rebate"] == 10.0
    assert kwargs["touch_type"] == "DOUBLE_ONE_TOUCH"  # default when omitted


def test_asian_builds_with_synthesized_observation_count():
    terms = {
        "initial_price": 100.0, "strike": 100.0, "option_type": "CALL",
        "maturity_years": 1.0, "averaging_frequency": "MONTHLY",
    }
    result = build_product("AsianOption", terms)
    assert result.missing == [], result.missing
    assert result.product_kwargs["num_observations"] == 12  # monthly over 1Y
    assert result.validation["ok"] is True, result.validation


def test_asian_missing_maturity_reported():
    result = build_product("AsianOption", {"strike": 100.0, "averaging_frequency": "MONTHLY"})
    assert "maturity_years" in result.missing


def test_single_sharkfin_builds():
    terms = {"initial_price": 100.0, "strike": 100.0, "barrier": 120.0, "option_type": "CALL",
             "maturity_years": 1.0, "participation_rate": 1.0}
    result = build_product("SingleSharkfinOption", terms)
    assert result.missing == [], result.missing
    assert result.validation["ok"] is True, result.validation


def test_double_sharkfin_builds():
    terms = {"initial_price": 100.0, "strike": 100.0, "lower_barrier": 80.0, "upper_barrier": 120.0,
             "maturity_years": 1.0, "participation_rate": 1.0}
    result = build_product("DoubleSharkfinOption", terms)
    assert result.missing == [], result.missing
    assert result.validation["ok"] is True, result.validation


def test_range_accrual_builds():
    terms = {"initial_price": 100.0, "maturity_years": 1.0, "observation_frequency": "DAILY",
             "lower_barrier_pct": 90, "upper_barrier_pct": 110, "accrual_rate": 0.10}
    result = build_product("RangeAccrualOption", terms)
    assert result.missing == [], result.missing
    assert result.engine_name == "RangeAccrualAnalyticalEngine"
    assert result.product_kwargs["range_config"]["lower_barrier"] == 90.0
    assert result.validation["ok"] is True, result.validation


def test_range_accrual_missing_barriers_reported():
    result = build_product("RangeAccrualOption",
                           {"initial_price": 100.0, "maturity_years": 1.0, "accrual_rate": 0.1})
    assert "range_config.lower_barrier" in result.missing
    assert result.ok is False


@pytest.mark.parametrize("family", sorted(_REGISTRY))
def test_initial_price_required_for_every_family(family):
    """The initial fixing S0 is never invented: whatever else a family needs,
    an absent initial_price is reported in `missing` so the agent must supply it
    (suggesting the latest spot). Previously builders silently defaulted to 100.
    """
    result = build_product(family, {"maturity_years": 1.0})
    assert "initial_price" in result.missing, (family, result.missing)
    assert result.ok is False


def test_initial_price_drives_percent_barriers_off_real_spot():
    # 101% / 70% of a real S0=5800 — not a phantom 100 base.
    terms = _snowball_terms(initial_price=5800.0)
    result = build_product("SnowballOption", terms)
    assert result.missing == []
    bc = result.product_kwargs["barrier_config"]
    assert bc["ko_barrier"] == 5858.0   # 101% of 5800
    assert bc["ki_barrier"] == 4060.0   # 70% of 5800
    assert result.ok is True


def test_build_result_carries_product_spec_when_ok():
    result = build_product(
        "SnowballOption",
        _snowball_terms(),
        underlying="000905.SH",
        currency="CNY",
    )
    assert result.ok is True
    assert result.product_spec is not None
    spec = result.product_spec
    assert spec.quantark_class == "SnowballOption"
    assert spec.product_family == "autocallable"   # derived from class
    assert spec.underlying == "000905.SH"
    assert spec.currency == "CNY"
    # product_kwargs is now a view onto product_spec.terms
    assert result.product_kwargs == spec.terms
    assert "barrier_config" in spec.terms


def test_build_result_product_spec_none_when_missing():
    result = build_product("SnowballOption", {"initial_price": 100.0})
    assert result.ok is False
    assert result.product_spec is None
    # no partial kwargs leak on failure
    assert result.product_kwargs == {}


def test_build_product_solve_target_is_exempt_from_missing():
    """The designated solve target (the unknown coupon) is supplied as a
    placeholder and must NOT be reported missing — the rest of the contract still
    must be bound (decision 6)."""
    terms = _snowball_terms()  # ko_rate present as the placeholder/initial guess
    result = build_product(
        "SnowballOption", terms, solve_target="barrier_config.ko_rate"
    )
    assert "barrier_config.ko_rate" not in result.missing
    assert result.ok is True


def test_prebuilt_snowball_without_schedule_is_rejected_not_silently_tidied():
    """P1a regression: the *current RFQ template shape* — a nested barrier_config
    with levels but NO ko_observation_schedule — must not slip through the tidy
    branch with missing=[] and the opaque quad error. Reject it as malformed.
    """
    rfq_template_shape = {
        "initial_price": 100.0,
        "strike": 100.0,
        "maturity": 1.0,
        "barrier_config": {
            "ko_barrier": 103.0,
            "ko_rate": 0.15,
            "ki_barrier": 75.0,
        },  # NOTE: no ko_observation_schedule
    }
    result = build_product("SnowballOption", rfq_template_shape)

    assert result.ok is False
    # not the opaque downstream quad error
    error = (result.validation or {}).get("error") or ""
    assert "KO observation" not in error
    assert "malformed" in error.lower()
    assert result.product_spec is None


def test_prebuilt_snowball_with_schedule_is_still_tidied():
    """A genuinely-built snowball (schedule present) still takes the tidy path."""
    built = build_product("SnowballOption", _snowball_terms())  # synthesizes a schedule
    assert built.ok
    rebuilt = build_product("SnowballOption", dict(built.product_kwargs))
    assert rebuilt.ok is True
    assert "ko_observation_schedule" in rebuilt.product_kwargs["barrier_config"]


def test_tidy_built_snowball_promotes_coupon_and_drops_empty_schedules():
    from app.services.domains.product_builders import _tidy_built_snowball

    tidied = _tidy_built_snowball(
        {
            "initial_price": 100.0,
            "strike": 100.0,
            "barrier_config": {
                "ko_barrier": 103.0,
                "ki_barrier": 75.0,
                "ko_observation_schedule": {
                    "records": [
                        {
                            "observation_date": "2026-08-29",
                            "barrier": 103.0,
                            "return_rate": 0.2,
                            "is_rate_annualized": True,
                        }
                    ]
                },
                "ki_observation_schedule": {"records": []},
            },
            "accrual_config": {"coupon_rate": 0.2},
        }
    )
    assert tidied["barrier_config"]["ko_rate"] == 0.2          # promoted from schedule
    assert "ki_observation_schedule" not in tidied["barrier_config"]  # empty dropped
    assert "coupon_rate" not in tidied.get("accrual_config", {})      # unsupported dropped


def test_prebuilt_snowball_with_nonlist_records_is_rejected_not_opaque():
    """A degenerate truthy-but-non-list `records` must not pass _looks_prebuilt
    into the tidy path and reach the opaque quad error — reject as malformed."""
    shape = {
        "initial_price": 100.0,
        "strike": 100.0,
        "maturity": 1.0,
        "barrier_config": {
            "ko_barrier": 103.0,
            "ko_rate": 0.15,
            "ki_barrier": 75.0,
            "ko_observation_schedule": {"records": "not-a-list"},
        },
    }
    result = build_product("SnowballOption", shape)
    assert result.ok is False
    error = (result.validation or {}).get("error") or ""
    assert "malformed" in error.lower()
    assert "KO observation" not in error


def test_snowball_frequency_required_when_absent():
    terms = _snowball_terms()
    terms.pop("ko_frequency")  # no observation_frequency either
    result = build_product("SnowballOption", terms)
    assert "observation_frequency" in result.missing
    assert result.ok is False


def test_snowball_quarterly_builds_four_ko_observations():
    terms = _snowball_terms(observation_frequency="QUARTERLY", lockup_months=3)
    terms.pop("ko_frequency", None)
    result = build_product("SnowballOption", terms)
    assert result.missing == [], result.missing
    sched = result.product_kwargs["barrier_config"]["ko_observation_schedule"]
    assert len(sched["records"]) == 4
    assert sched["frequency"] == "QUARTERLY"
    assert result.ok is True


def test_snowball_custom_frequency_uses_supplied_dates():
    terms = _snowball_terms(
        observation_frequency="CUSTOM",
        ko_observation_dates=["2026-06-05", "2026-09-07", "2026-12-07"],
        lockup_months=0,
    )
    terms.pop("ko_frequency", None)
    result = build_product("SnowballOption", terms)
    assert result.missing == [], result.missing
    records = result.product_kwargs["barrier_config"]["ko_observation_schedule"]["records"]
    assert len(records) == 3
    assert result.ok is True


def test_snowball_custom_frequency_missing_dates_reported():
    terms = _snowball_terms(observation_frequency="CUSTOM")
    terms.pop("ko_frequency", None)
    result = build_product("SnowballOption", terms)
    assert "ko_observation_dates" in result.missing
    assert result.ok is False


def test_snowball_custom_dates_are_sorted_chronologically():
    """Out-of-order CUSTOM dates are sorted so the STOP_FIRST_HIT schedule stays
    chronological."""
    terms = _snowball_terms(
        observation_frequency="CUSTOM",
        ko_observation_dates=["2026-12-07", "2026-06-05", "2026-09-07"],
        lockup_months=0,
    )
    terms.pop("ko_frequency", None)
    result = build_product("SnowballOption", terms)
    assert result.ok is True
    dates = [
        r["observation_date"]
        for r in result.product_kwargs["barrier_config"]["ko_observation_schedule"]["records"]
    ]
    assert dates == ["2026-06-05", "2026-09-07", "2026-12-07"]


def test_snowball_custom_invalid_date_reported_not_raised():
    """A malformed CUSTOM date is reported via `missing`, never raised — the
    builder never crashes on input."""
    terms = _snowball_terms(
        observation_frequency="CUSTOM",
        ko_observation_dates=["2026-06-05", "not-a-date"],
        lockup_months=0,
    )
    terms.pop("ko_frequency", None)
    result = build_product("SnowballOption", terms)
    assert result.ok is False
    assert any("ko_observation_dates" in m for m in result.missing)


def test_ko_reset_quarterly_post_schedule_carries_quarterly_frequency():
    """The post-KI reset schedule reuses the pre-KI dates, so it must reuse the
    pre-KI frequency tag (not a hardcoded MONTHLY)."""
    terms = _snowball_terms(
        observation_frequency="QUARTERLY",
        lockup_months=3,
        post_ko_barrier_pct=100,
        post_ko_rate=0.10,
    )
    terms.pop("ko_frequency", None)
    result = build_product("KnockOutResetSnowballOption", terms)
    assert result.missing == [], result.missing
    pre = result.product_kwargs["barrier_config"]["ko_observation_schedule"]
    post = result.product_kwargs["post_barrier_config"]["ko_observation_schedule"]
    assert post["frequency"] == "QUARTERLY"
    assert pre["frequency"] == "QUARTERLY"


def test_build_product_threads_components_into_family_derivation():
    """A packaged product (non-empty components) derives product_family == 'package',
    not the single-leg class family — components must reach the single derivation
    point (decision 3)."""
    result = build_product(
        "SnowballOption",
        _snowball_terms(),
        underlying="000905.SH",
        currency="CNY",
        components=[{"component_role": "leg", "quantity": 1.0}],
    )
    assert result.ok is True
    assert result.product_spec.product_family == "package"
    assert result.product_spec.components == [{"component_role": "leg", "quantity": 1.0}]


def test_prebuilt_wraps_complete_vanilla_termsheet_without_synthesis():
    # A complete OTC-style vanilla termsheet: carries exercise/settlement dates +
    # contract_multiplier and NO maturity_years. The raw builder would reject it;
    # prebuilt validate-and-wrap accepts it verbatim.
    terms = {
        "strike": 100.0, "option_type": "CALL",
        "exercise_date": "2026-12-31", "settlement_date": "2027-01-04",
        "contract_multiplier": 10000.0,
    }
    result = build_product("EuropeanVanillaOption", terms, prebuilt=True)
    assert result.ok is True, result.validation
    assert result.engine_name == "BlackScholesEngine"
    assert result.missing == []
    # verbatim: no synthesis, no dropped keys
    assert result.product_kwargs == terms
    assert result.product_spec is not None
    assert result.product_spec.quantark_class == "EuropeanVanillaOption"


@pytest.mark.parametrize("maturity", [1.0, ""])
def test_prebuilt_rejects_mixed_vanilla_maturity_representations(maturity):
    terms = {
        "strike": 100.0,
        "option_type": "CALL",
        "exercise_date": "2026-12-31",
        "settlement_date": "2027-01-04",
        "maturity": maturity,
    }

    result = build_product("EuropeanVanillaOption", terms, prebuilt=True)

    assert result.ok is False
    assert result.product_spec is None
    assert result.validation is not None
    assert result.validation["error"] == (
        "maturity must not be supplied when exercise_date is supplied; "
        "use either explicit dates or tenor maturity, not both"
    )


def test_prebuilt_false_still_runs_raw_synthesis_for_scalars():
    # Regression: without prebuilt, the raw builder is used (maturity_years -> maturity).
    result = build_product(
        "EuropeanVanillaOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0},
    )
    assert result.ok is True
    assert "maturity" in result.product_kwargs and "maturity_years" not in result.product_kwargs


def test_prebuilt_rejects_malformed_schedule_less_snowball():
    # Decision-8 hardening must still apply under prebuilt: a nested barrier_config
    # with no synthesized ko_observation_schedule is malformed, not silently wrapped.
    terms = {
        "initial_price": 100.0, "strike": 100.0, "maturity": 1.0,
        "barrier_config": {"ko_barrier": 103.0, "ko_rate": 0.1},  # no schedule
    }
    result = build_product("SnowballOption", terms, prebuilt=True)
    assert result.ok is False
    assert result.product_spec is None


# --- Part (c): digital + barrier builder edge cases. Characterization tests that
# pin spike-verified behavior (cash_payoff -> payout rename, the four barrier
# types, missing-field reporting, rebate default). They should pass on first run;
# a failure means a real behavior change to investigate, not loosen.
def test_digital_missing_cash_payoff_reported():
    result = build_product(
        "CashOrNothingDigitalOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0},
    )
    assert result.ok is False
    assert "cash_payoff" in result.missing


def test_digital_maps_cash_payoff_to_payout_for_put():
    result = build_product(
        "CashOrNothingDigitalOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "PUT",
         "maturity_years": 1.0, "cash_payoff": 7.5},
    )
    assert result.ok is True, result.validation
    assert result.product_kwargs["payout"] == 7.5         # cash_payoff -> payout rename
    assert "cash_payoff" not in result.product_kwargs
    assert result.product_kwargs["option_type"] == "PUT"


@pytest.mark.parametrize(
    "barrier_type, barrier",
    [("DOWN_OUT", 80.0), ("UP_OUT", 120.0), ("DOWN_IN", 80.0), ("UP_IN", 120.0)],
)
def test_barrier_all_four_types_build_and_validate(barrier_type, barrier):
    result = build_product(
        "BarrierOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0,
         "barrier": barrier, "barrier_type": barrier_type},
    )
    assert result.ok is True, result.validation
    assert result.product_kwargs["barrier_type"] == barrier_type
    assert result.product_kwargs["barrier"] == barrier


def test_barrier_missing_barrier_reported():
    result = build_product(
        "BarrierOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0},
    )
    assert result.ok is False
    assert "barrier" in result.missing


def test_barrier_rebate_defaults_zero_and_honors_explicit():
    base = {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL",
            "maturity_years": 1.0, "barrier": 80.0}
    assert build_product("BarrierOption", base).product_kwargs["rebate"] == 0.0
    assert build_product("BarrierOption", {**base, "rebate": 2.5}).product_kwargs["rebate"] == 2.5


# --- Part (c): single + double sharkfin edge cases. The double-sharkfin inverted-
# barrier rejection is the centerpiece: _build_double_sharkfin does not order-check
# lower<upper, so QuantArk must reject a nonsensical lower>upper at validation. If
# that ever starts building ok, a nonsensical product is being accepted — STOP, do
# not delete the test.
def test_single_sharkfin_missing_barrier_reported():
    result = build_product(
        "SingleSharkfinOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0},
    )
    assert result.ok is False
    assert "barrier" in result.missing


def test_single_sharkfin_participation_defaults_one():
    result = build_product(
        "SingleSharkfinOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL",
         "maturity_years": 1.0, "barrier": 120.0},
    )
    assert result.ok is True, result.validation
    assert result.product_kwargs["participation_rate"] == 1.0


def test_double_sharkfin_missing_barriers_reported():
    result = build_product(
        "DoubleSharkfinOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0},
    )
    assert result.ok is False
    assert "lower_barrier" in result.missing
    assert "upper_barrier" in result.missing


def test_double_sharkfin_inverted_barriers_are_rejected():
    # _build_double_sharkfin does not order-check; QuantArk must catch lower>upper
    # so a nonsensical double sharkfin is rejected, not silently built.
    result = build_product(
        "DoubleSharkfinOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0,
         "lower_barrier": 120.0, "upper_barrier": 80.0},
    )
    assert result.ok is False
    assert "lower barrier" in (result.validation or {}).get("error", "").lower()
    assert result.product_spec is None


def test_double_sharkfin_normal_barriers_build_and_validate():
    result = build_product(
        "DoubleSharkfinOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0,
         "lower_barrier": 80.0, "upper_barrier": 120.0},
    )
    assert result.ok is True, result.validation
    assert result.product_kwargs["lower_barrier"] == 80.0
    assert result.product_kwargs["upper_barrier"] == 120.0

"""Cross-channel equivalence net: every workflow channel must reach the same
canonical build_product output for a golden product per family. This is the
regression net that makes builder divergence (the root cause the unified-product-
schema migration removed) fail a test. RFQ vs agent: byte-identical. try-solve and
OTC import: structural (same family + KO-schedule record count + barrier/rate
economics) — import carries a complete heterogeneous termsheet the uniform
synthesizer cannot reproduce key-for-key."""
import pytest

from app.services.domains.product_builders import build_product


# Uniform snowball, fully specified (no solve target). trade_start_date is PINNED
# (not the RFQ template default) so synthesis is deterministic across channels.
GOLDEN_SNOWBALL_FLAT = {
    "initial_price": 100.0,
    "strike": 100.0,
    "maturity_years": 1.0,
    "ko_barrier_pct": 103.0,
    "ki_barrier_pct": 75.0,
    "ko_rate": 0.10,
    "lockup_months": 3,
    "trade_start_date": "2026-07-01",
    "observation_frequency": "MONTHLY",
    "contract_multiplier": 1.0,
}


def _agent_snowball_kwargs():
    built = build_product("SnowballOption", dict(GOLDEN_SNOWBALL_FLAT))
    assert built.ok, built.validation
    return built.product_kwargs


def test_rfq_snowball_is_byte_identical_to_agent():
    from app.schemas import RFQRequestDraft
    from app.services.rfq import _executable_product_kwargs

    draft = RFQRequestDraft(
        product_type="SnowballOption",
        product_kwargs=dict(GOLDEN_SNOWBALL_FLAT),
    )
    rfq_kwargs, missing = _executable_product_kwargs(draft, quote_mode="price")
    assert missing == []
    # the byte-identity below is over a non-degenerate synthesized schedule
    # (months 3..12 from the pinned 2026-07-01 start = 10 KO observations)
    assert len(_ko_records(rfq_kwargs)) == 10
    assert rfq_kwargs == _agent_snowball_kwargs()


def _ko_records(product_kwargs):
    return product_kwargs["barrier_config"]["ko_observation_schedule"]["records"]


def test_import_snowball_is_structurally_equivalent_to_agent():
    from app.services.position_adapter import map_trade_row
    from test_position_import_pricing import snowball_row  # type: ignore

    row = snowball_row("T-GOLDEN")
    row["Knock-Out Barrier"] = "103"
    row["Knock-Out Coupon"] = "10%"
    row["Knock-In Barrier"] = 75.0
    row["Knock-Out Observation Dates"] = "2026/10/01,2027/07/01"

    mapping = map_trade_row(row)
    built = build_product("SnowballOption", dict(mapping.product_kwargs), prebuilt=True)
    assert built.ok, built.validation

    assert built.product_spec.quantark_class == "SnowballOption"
    assert built.product_spec.product_family == "autocallable"
    import_records = _ko_records(built.product_kwargs)
    assert len(import_records) == 2  # exactly the two explicit observation dates
    assert built.product_kwargs["barrier_config"]["ko_barrier"] == 103.0
    assert abs(import_records[0]["return_rate"] - 0.10) < 1e-9

    # Cross-channel: the uniform import and the agent build agree on the economics
    # (ko level + coupon). Only the observation SCHEDULE differs (2 explicit import
    # dates vs 10 synthesized monthly) — exactly why this is structural, not byte-
    # identical.
    agent = _agent_snowball_kwargs()
    assert built.product_kwargs["barrier_config"]["ko_barrier"] == agent["barrier_config"]["ko_barrier"]
    assert abs(import_records[0]["return_rate"] - _ko_records(agent)[0]["return_rate"]) < 1e-9


def test_trysolve_snowball_is_structurally_equivalent_to_agent():
    from app.services.try_solve import (
        _build_row_termsheet, _pricing_market, _maturity_years,
    )
    from app.services.try_solve_registry import registry_by_key
    from app.schemas import TrySolveRowIn, TrySolveMarketIn, TrySolveQuoteRequestIn

    row = TrySolveRowIn(
        row_id="g1", product_key="autocall",
        fields={"underlying": "000905.SH", "notional": 1_000_000,
                "start_date": "2026-07-01", "tenor_months": 12,
                "ko_barrier": 1.03, "ki_barrier": 0.75, "annualized_coupon": 0.10,
                "observation_frequency": "MONTHLY", "lockup_months": 3},
        market=TrySolveMarketIn(spot=100.0, volatility=0.2, rate=0.03, dividend_yield=0.0),
        quote_request=TrySolveQuoteRequestIn(quote_field_key="annualized_coupon",
                                             initial_guess=0.10, target_label="price", target_value=5.0),
    )
    product = registry_by_key()["autocall"]
    market = _pricing_market(row)
    kwargs, missing = _build_row_termsheet(
        row, product, market, _maturity_years(row), product.quote_fields["annualized_coupon"],
    )
    assert missing == []
    # genuinely a SnowballOption (not just "some product with a barrier_config")
    assert product.quantark_product_type == "SnowballOption"
    assert "barrier_config" in kwargs
    assert "ki_observation_schedule" in kwargs["barrier_config"]
    # deterministic: start 2026-07-01, lockup 3mo, 12mo tenor, MONTHLY -> months 3..12
    assert len(_ko_records(kwargs)) == 10


def test_import_vanilla_validates_and_matches_agent_family():
    from app.services.position_adapter import map_trade_row
    from test_position_import_pricing import vanilla_row  # type: ignore

    mapping = map_trade_row(vanilla_row("T-GV"))
    imported = build_product("EuropeanVanillaOption", dict(mapping.product_kwargs), prebuilt=True)
    assert imported.ok, imported.validation
    assert imported.product_spec.product_family == "option"
    agent = build_product(
        "EuropeanVanillaOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0},
    )
    assert agent.ok
    assert agent.engine_name == imported.engine_name == "BlackScholesEngine"
    assert agent.product_spec.product_family == imported.product_spec.product_family
    # shared economics agree (import additionally carries dates + contract_multiplier)
    for key in ("strike", "option_type"):
        assert imported.product_kwargs[key] == agent.product_kwargs[key]


def test_import_phoenix_validates_through_prebuilt_gate():
    from app.services.position_adapter import map_trade_row
    from test_position_import_pricing import phoenix_row  # type: ignore

    mapping = map_trade_row(phoenix_row("T-GP"))
    built = build_product("PhoenixOption", dict(mapping.product_kwargs), prebuilt=True)
    assert built.ok, built.validation
    assert built.product_spec.quantark_class == "PhoenixOption"
    assert built.product_spec.product_family == "autocallable"
    assert "coupon_config" in built.product_kwargs


def _shared(kwargs, keys):
    result = {k: kwargs[k] for k in keys if k in kwargs}
    assert result, f"none of {keys} present in kwargs — comparison would be vacuous"
    return result


@pytest.mark.parametrize(
    "structure, quantark_class, family, agent_terms, shared_keys",
    [
        ("European Digital", "CashOrNothingDigitalOption", "option",
         {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL",
          "maturity_years": 1.0, "cash_payoff": 5.0},
         ("strike", "option_type", "payout")),
        ("Barrier Knock-In", "BarrierOption", "barrier",
         {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0,
          "barrier": 80.0, "barrier_type": "DOWN_IN"},
         ("strike", "option_type", "barrier")),
        ("Single Sharkfin", "SingleSharkfinOption", "sharkfin",
         {"initial_price": 100.0, "strike": 100.0, "barrier": 120.0, "option_type": "CALL",
          "maturity_years": 1.0},
         ("strike", "option_type", "barrier", "participation_rate")),
        ("Double Sharkfin", "DoubleSharkfinOption", "sharkfin",
         {"initial_price": 100.0, "strike": 100.0, "lower_barrier": 80.0, "upper_barrier": 120.0,
          "option_type": "CALL", "maturity_years": 1.0},
         ("strike", "option_type", "lower_barrier", "upper_barrier", "participation_rate")),
    ],
)
def test_import_economics_match_agent(structure, quantark_class, family, agent_terms, shared_keys):
    from app.services.position_adapter import map_trade_row
    from test_position_import_pricing import (  # type: ignore
        vanilla_row, shark_row, double_shark_row,
    )
    if structure == "Single Sharkfin":
        row = shark_row()
    elif structure == "Double Sharkfin":
        row = double_shark_row()
    else:
        row = vanilla_row(f"T-{quantark_class}")
        row["Structure Type"] = structure
        if structure == "European Digital":
            row["Coupon Rate"] = "5%"
        if structure == "Barrier Knock-In":
            row["Knock-In Barrier"] = 80.0
            row["No-Knock-In Coupon"] = "1%"
    mapping = map_trade_row(row)
    imported = build_product(quantark_class, dict(mapping.product_kwargs), prebuilt=True)
    assert imported.ok, imported.validation
    assert imported.product_spec.quantark_class == quantark_class
    assert imported.product_spec.product_family == family

    agent = build_product(quantark_class, dict(agent_terms))
    assert agent.ok, agent.validation
    # structural: import carries richer dates/multiplier/schedules; the shared
    # economic fields must agree across the two channels.
    assert _shared(imported.product_kwargs, shared_keys) == _shared(agent.product_kwargs, shared_keys)


def _trysolve_kwargs(product_key, fields, *, quote_field="premium_rate"):
    """Canonical product_kwargs the try-solve channel feeds build_product for a
    fully-specified row. Solving the price-like `premium_rate` leaves the product
    kwargs complete (the solve target is not a product field)."""
    from app.services.try_solve import _build_row_termsheet, _pricing_market, _maturity_years
    from app.services.try_solve_registry import registry_by_key
    from app.schemas import TrySolveRowIn, TrySolveMarketIn, TrySolveQuoteRequestIn

    row = TrySolveRowIn(
        row_id="x", product_key=product_key, fields=fields,
        market=TrySolveMarketIn(spot=100.0, volatility=0.2, rate=0.03, dividend_yield=0.0),
        quote_request=TrySolveQuoteRequestIn(
            quote_field_key=quote_field, initial_guess=0.05,
            target_label="price", target_value=5.0,
        ),
    )
    product = registry_by_key()[product_key]
    market = _pricing_market(row)
    kwargs, missing = _build_row_termsheet(
        row, product, market, _maturity_years(row), product.quote_fields[quote_field],
    )
    assert missing == [], (product_key, missing)
    return kwargs


_TS_ROW_BASE = {"underlying": "000905.SH", "notional": 1_000_000, "tenor_months": 12}

# try-solve canonical kwargs are BYTE-IDENTICAL to the agent's for these families.
_TS_BYTE_IDENTICAL = [
    ("vanilla", {"strike": 100.0},
     "EuropeanVanillaOption",
     {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0}),
    ("single_sf", {"strike": 100.0, "barrier": 120.0},
     "SingleSharkfinOption",
     {"initial_price": 100.0, "strike": 100.0, "barrier": 120.0, "option_type": "CALL",
      "maturity_years": 1.0}),
    ("double_sf", {"strike": 100.0, "lower_barrier": 80.0, "upper_barrier": 120.0},
     "DoubleSharkfinOption",
     {"initial_price": 100.0, "strike": 100.0, "lower_barrier": 80.0, "upper_barrier": 120.0,
      "option_type": "CALL", "maturity_years": 1.0}),
    ("asian", {"strike": 100.0},
     "AsianOption",
     {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0,
      "averaging_frequency": "MONTHLY"}),
]


@pytest.mark.parametrize("key, extra, agent_class, agent_terms", _TS_BYTE_IDENTICAL)
def test_trysolve_is_byte_identical_to_agent(key, extra, agent_class, agent_terms):
    ts = _trysolve_kwargs(key, {**_TS_ROW_BASE, **extra})
    agent = build_product(agent_class, dict(agent_terms))
    assert agent.ok, agent.validation
    assert ts == agent.product_kwargs


# Identical EXCEPT the notional-scaled payoff (try-solve scales payout/rebate by
# notional — an input-adapter convention, not a builder divergence).
_TS_STRUCTURAL_PAYOFF = [
    ("digital", {"strike": 100.0, "payout": 10.0}, "payout",
     "CashOrNothingDigitalOption",
     {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL",
      "maturity_years": 1.0, "cash_payoff": 10.0}),
    ("one_touch", {"barrier": 120.0, "rebate": 10.0}, "rebate",
     "OneTouchOption",
     {"initial_price": 100.0, "barrier": 120.0, "cash_payoff": 10.0, "maturity_years": 1.0}),
    ("double_one_touch", {"upper_barrier": 120.0, "lower_barrier": 80.0, "rebate": 10.0}, "rebate",
     "DoubleOneTouchOption",
     {"initial_price": 100.0, "upper_barrier": 120.0, "lower_barrier": 80.0,
      "cash_payoff": 10.0, "maturity_years": 1.0}),
]


@pytest.mark.parametrize("key, extra, payoff, agent_class, agent_terms", _TS_STRUCTURAL_PAYOFF)
def test_trysolve_matches_agent_except_scaled_payoff(key, extra, payoff, agent_class, agent_terms):
    ts = _trysolve_kwargs(key, {**_TS_ROW_BASE, **extra})
    agent = build_product(agent_class, dict(agent_terms))
    assert agent.ok, agent.validation
    # both carry the payoff; only its (notional-scaled) value differs
    assert payoff in ts and payoff in agent.product_kwargs
    ts_rest = {k: v for k, v in ts.items() if k != payoff}
    agent_rest = {k: v for k, v in agent.product_kwargs.items() if k != payoff}
    assert ts_rest == agent_rest


def test_trysolve_range_accrual_economics_match_agent():
    # range_accrual has no premium_rate field; solve range_accrual_rate (the row
    # also supplies the rate, so the built termsheet is fully specified). The rate
    # field the adapter reads is `coupon_yield`, and we use a NON-default value
    # (0.08, != the 0.1 fallback) so a broken rate-flow would actually diverge from
    # the agent here. Structural: range_config economics + observation count agree.
    ts = _trysolve_kwargs(
        "range_accrual",
        {**_TS_ROW_BASE, "lower_barrier": 90.0, "upper_barrier": 110.0, "coupon_yield": 0.08},
        quote_field="range_accrual_rate",
    )
    agent = build_product(
        "RangeAccrualOption",
        {"initial_price": 100.0, "maturity_years": 1.0, "lower_barrier_pct": 90.0,
         "upper_barrier_pct": 110.0, "accrual_rate": 0.08},
    )
    assert agent.ok, agent.validation
    assert ts["range_config"]["accrual_rate"] == 0.08  # the rate genuinely flowed
    assert ts["range_config"] == agent.product_kwargs["range_config"]
    assert ts["num_observations"] == agent.product_kwargs["num_observations"]


def test_trysolve_forward_is_byte_identical_to_agent():
    # forward -> Futures (DeltaOne). No premium_rate field, so solve fixed_yield.
    # The adapter makes the no-basis default explicit (basis=0.0); the agent build
    # includes basis=0.0 to match (same economics: a futures with no basis).
    ts = _trysolve_kwargs(
        "forward", {**_TS_ROW_BASE, "contract_multiplier": 1.0},
        quote_field="fixed_yield",
    )
    agent = build_product(
        "Futures",
        {"initial_price": 100.0, "underlying": "000905.SH", "maturity_years": 1.0,
         "contract_multiplier": 1.0, "basis": 0.0},
    )
    assert agent.ok, agent.validation
    assert ts == agent.product_kwargs

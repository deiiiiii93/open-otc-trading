from __future__ import annotations

import pytest

from app.models import (
    AuditEvent,
    EquitySpotProduct,
    Portfolio,
    PortfolioKind,
    Position,
)
from app.schemas import PositionOut
from app.services.domains.booking import (
    BookingRequest,
    ProductBookingSpec,
    book_position,
    normalize_booking_product_spec,
    prepare_booking_product_spec,
)
from app.services.domains.product_builders import build_product

# Raw economic terms exactly as the desk agent produces them for a direct
# snowball booking (cf. "Booking test 4"): no pre-built barrier_config, and no
# hand-supplied initial_date/settlement_date — those must be synthesized.
_RAW_SNOWBALL_TERMS = {
    "initial_price": 8359.56,
    "strike": 8359.56,
    "maturity_years": 1,
    "trade_start_date": "2026-05-29",
    "ko_barrier_pct": 101,
    "ki_barrier_pct": 70,
    "ko_rate": 0.15,
    "ko_rate_annualized": True,
    "ki_convention": "DAILY",
    "lockup_months": 0,
    "observation_frequency": "MONTHLY",
}


def test_build_product_snowball_carries_accrual_dates():
    """build_product must produce a valid, annualized-accrual-ready Snowball
    from raw terms — carrying initial_date/settlement_date so the QuantArk
    accrual validator passes (the gap that caused the Booking test 4 loop).
    """
    result = build_product("SnowballOption", dict(_RAW_SNOWBALL_TERMS))

    assert result.missing == []
    assert result.ok, f"expected ok build, got validation={result.validation}"
    assert "initial_date" in result.product_kwargs
    assert "settlement_date" in result.product_kwargs


def test_book_snowball_from_raw_economic_terms_validates():
    """book_position must accept raw snowball economic terms (not hand-built
    QuantArk kwargs) and validate them — routing through the single builder.
    """
    spec = ProductBookingSpec(
        asset_class="equity",
        product_family="autocallable",
        quantark_class="SnowballOption",
        underlying="000905.SH",
        currency="CNY",
        terms=dict(_RAW_SNOWBALL_TERMS),
    )

    prepared = prepare_booking_product_spec(spec, engine_name="SnowballQuadEngine")

    assert "ko_observation_schedule" in prepared.terms["barrier_config"]


def test_book_rfq_sourced_snowball_with_prebuilt_schedules(session):
    """An RFQ-sourced snowball arrives with terms that ALREADY carry synthesized
    schedules (the shape a quoted snowball has). Booking must accept the built
    barrier_config without re-synthesizing it — the path _normalize_snowball_terms
    served, now owned by the single shared builder.
    """
    built = build_product("SnowballOption", dict(_RAW_SNOWBALL_TERMS))
    assert built.ok
    assert "ko_observation_schedule" in built.product_kwargs["barrier_config"]

    portfolio = Portfolio(
        name="RFQ Book", base_currency="CNY", kind=PortfolioKind.CONTAINER.value
    )
    session.add(portfolio)
    session.flush()

    booked = book_position(
        session,
        BookingRequest(
            portfolio_id=portfolio.id,
            product=ProductBookingSpec(
                asset_class="equity",
                product_family="autocallable",
                quantark_class="SnowballOption",
                underlying="000905.SH",
                currency="CNY",
                terms=dict(built.product_kwargs),
            ),
            quantity=1_000_000.0,
            source_trade_id="RFQ-99-V1",
            engine_name="SnowballQuadEngine",
            source="rfq",
        ),
    )
    session.flush()

    assert booked.product_id is not None
    assert booked.product_type == "SnowballOption"
    assert booked.position_kind == "otc"
    assert booked.product_kwargs["barrier_config"]["ko_observation_schedule"]["records"]


def test_booking_creates_product_and_position(session):
    portfolio = Portfolio(name="Book", base_currency="CNY", kind=PortfolioKind.CONTAINER.value)
    session.add(portfolio)
    session.flush()

    booked = book_position(
        session,
        BookingRequest(
            portfolio_id=portfolio.id,
            product=ProductBookingSpec(
                asset_class="equity",
                product_family="spot",
                quantark_class="SpotInstrument",
                underlying="510300.SH",
                currency="CNY",
                terms={"deltaone_type": "ETF", "instrument_code": "510300.SH",
                       "initial_price": 4.2},
            ),
            quantity=100.0,
            entry_price=4.2,
            status="open",
            source_trade_id="FUND-001",
            engine_name="DeltaOneEngine",
            engine_kwargs={},
            actor="desk_user",
            source="manual",
        ),
    )
    session.flush()

    assert booked.product_id is not None
    assert booked.product_type == "SpotInstrument"
    assert booked.product_kwargs["deltaone_type"] == "ETF"
    assert session.query(Position).count() == 1
    event = session.query(AuditEvent).filter_by(subject_id=str(booked.id)).one()
    assert event.event_type == "position.created"


def test_position_out_includes_product_terms(session):
    portfolio = Portfolio(name="Schema Book", base_currency="CNY", kind=PortfolioKind.CONTAINER.value)
    session.add(portfolio)
    session.flush()

    booked = book_position(
        session,
        BookingRequest(
            portfolio_id=portfolio.id,
            product=ProductBookingSpec(
                asset_class="equity",
                product_family="spot",
                quantark_class="SpotInstrument",
                underlying="510300.SH",
                currency="CNY",
                terms={"deltaone_type": "ETF", "instrument_code": "510300.SH",
                       "initial_price": 4.2},
            ),
            quantity=100.0,
            entry_price=4.2,
            engine_name="DeltaOneEngine",
        ),
    )
    session.flush()

    out = PositionOut.model_validate(booked, from_attributes=True)

    assert out.product is not None
    assert out.product.raw_terms["terms"]["deltaone_type"] == "ETF"
    assert out.product.terms["deltaone_type"] == "ETF"


def test_normalize_passes_through_already_built_snowball_terms():
    """An already-built, valid snowball is routed through the single builder and
    returned tidied/idempotent — booking depends only on build_product's public
    API (the tidy transformation itself is unit-tested in test_product_builders)."""
    built = build_product("SnowballOption", dict(_RAW_SNOWBALL_TERMS))
    assert built.ok

    normalized = normalize_booking_product_spec(
        ProductBookingSpec(
            asset_class="equity",
            product_family="autocallable",
            quantark_class="SnowballOption",
            underlying="000905.SH",
            currency="CNY",
            terms=dict(built.product_kwargs),
        )
    )

    bc = normalized.terms["barrier_config"]
    assert "ko_rate" in bc
    assert bc["ko_observation_schedule"]["records"]


def _complete_vanilla_spec():
    return ProductBookingSpec(
        asset_class="equity",
        product_family="option",
        quantark_class="EuropeanVanillaOption",
        underlying="000852.SH",
        currency="CNY",
        terms={
            "strike": 100.0, "option_type": "CALL",
            "exercise_date": "2026-12-31", "settlement_date": "2027-01-04",
            "contract_multiplier": 10000.0,
        },
        components=[],
        display_name=None,
        source_payload={},
    )


def test_gate_validates_complete_scalar_and_returns_unchanged():
    spec = _complete_vanilla_spec()
    normalized = normalize_booking_product_spec(spec)
    # complete + valid -> validate-and-wrap returns the same terms (no synthesis)
    assert normalized.terms == spec.terms


def test_gate_rejects_invalid_scalar_with_precise_error():
    spec = _complete_vanilla_spec()
    spec = ProductBookingSpec(**{**spec.__dict__, "terms": {"option_type": "CALL"}})  # no strike/dates
    with pytest.raises(ValueError) as exc:
        normalize_booking_product_spec(spec)
    message = str(exc.value)
    # prebuilt=True skips the missing-keys check (no synthesis), so an incomplete
    # scalar fails at validate_quantark_build -> the "Invalid …" (not-ok) branch,
    # surfacing the validator's diagnostic rather than an opaque downstream error.
    assert message.startswith("Invalid EuropeanVanillaOption booking terms:")
    assert "Incomplete" not in message  # NOT the snowball missing-keys branch


def test_booking_gate_rejects_mixed_vanilla_maturity_representations():
    spec = _complete_vanilla_spec()
    spec = ProductBookingSpec(
        **{
            **spec.__dict__,
            "terms": {
                **spec.terms,
                "maturity": "",
            },
        }
    )

    with pytest.raises(ValueError) as exc:
        normalize_booking_product_spec(spec)

    message = str(exc.value)
    assert message == (
        "Invalid EuropeanVanillaOption booking terms: "
        "maturity must not be supplied when exercise_date is supplied; "
        "use either explicit dates or tenor maturity, not both"
    )


def test_booking_rejects_schedule_less_snowball_with_clear_malformed_error():
    """A nested-but-schedule-less snowball booked directly must surface
    build_product's precise malformed message — not the opaque quad
    'KO observation … required' error (P1a, completed on the booking path)."""
    spec = ProductBookingSpec(
        asset_class="equity",
        product_family="autocallable",
        quantark_class="SnowballOption",
        underlying="000905.SH",
        currency="CNY",
        terms={
            "initial_price": 100.0,
            "strike": 100.0,
            "maturity": 1.0,
            "barrier_config": {"ko_barrier": 103.0, "ko_rate": 0.15, "ki_barrier": 75.0},
        },
    )
    with pytest.raises(ValueError) as exc:
        prepare_booking_product_spec(spec, engine_name="SnowballQuadEngine")
    msg = str(exc.value)
    assert "malformed" in msg.lower()
    assert "KO observation" not in msg


# --- Parity pass (Part a): the previously un-gated families are now validated at
# booking. The option families arrive as complete QuantArk termsheets and take
# the validate-and-wrap (prebuilt=True) path, so the gate returns their terms
# unchanged. ProductBookingSpec/normalize/build_product imports are at the top.
_NEWLY_GATED_OPTIONS = [
    ("AsianOption", {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL",
                     "maturity_years": 1.0, "averaging_frequency": "MONTHLY"}),
    ("OneTouchOption", {"initial_price": 100.0, "barrier": 120.0, "cash_payoff": 10.0,
                        "barrier_direction": "UP", "maturity_years": 1.0}),
    ("RangeAccrualOption", {"initial_price": 100.0, "maturity_years": 1.0,
                            "lower_barrier_pct": 90.0, "upper_barrier_pct": 110.0, "accrual_rate": 0.1}),
    ("DoubleOneTouchOption", {"initial_price": 100.0, "upper_barrier": 120.0, "lower_barrier": 80.0,
                              "cash_payoff": 10.0, "maturity_years": 1.0}),
]


@pytest.mark.parametrize("family, flat", _NEWLY_GATED_OPTIONS)
def test_booking_gate_validates_newly_gated_option_family(family, flat):
    built = build_product(family, flat)
    assert built.ok, built.validation
    spec = ProductBookingSpec(
        asset_class="equity",
        product_family=built.product_spec.product_family,
        quantark_class=family,
        underlying="000905.SH",
        currency="CNY",
        terms=dict(built.product_kwargs),
        components=[],
        display_name=None,
        source_payload={},
    )
    # complete + valid -> validate-and-wrap returns the same terms (no synthesis)
    normalized = normalize_booking_product_spec(spec)
    assert normalized.terms == spec.terms


# DeltaOne (Spot/Futures) bookings differ: they carry RAW desk terms (S0 +
# persistence metadata) and their `underlying` at the spec top-level. The gate
# SYNTHESIZES them (prebuilt=False), threading the underlying in and carrying the
# metadata as _otc_ — so the returned terms are transformed, not identity.
_NEWLY_GATED_DELTAONE = [
    ("SpotInstrument", "spot",
     {"deltaone_type": "ETF", "instrument_code": "510300.SH", "initial_price": 4.2},
     "_otc_instrument_code"),
    ("Futures", "futures",
     {"contract_code": "IF2406.CFE", "contract_multiplier": 300.0,
      "maturity_years": 0.5, "initial_price": 100.0},
     "_otc_contract_code"),
]


@pytest.mark.parametrize("family, fam, raw, meta_key", _NEWLY_GATED_DELTAONE)
def test_booking_gate_synthesizes_deltaone_family(family, fam, raw, meta_key):
    spec = ProductBookingSpec(
        asset_class="equity", product_family=fam, quantark_class=family,
        underlying="CSI300", currency="CNY",
        terms=dict(raw), components=[], display_name=None, source_payload={},
    )
    normalized = normalize_booking_product_spec(spec)
    # underlying threaded from the spec top-level into the QuantArk kwargs
    assert normalized.terms["underlying"] == "CSI300"
    # persistence metadata carried as _otc_ (popped before QuantArk at price time)
    assert meta_key in normalized.terms
    # the bare metadata key QuantArk rejects is gone
    assert meta_key.removeprefix("_otc_") not in normalized.terms


def test_booking_gate_rejects_incomplete_spot_missing_s0():
    # DeltaOne keeps the "never invent S0" contract: a spot booking without
    # initial_price is incomplete and rejected by the synthesis gate.
    spec = ProductBookingSpec(
        asset_class="equity", product_family="spot", quantark_class="SpotInstrument",
        underlying="CSI300", currency="CNY",
        terms={"deltaone_type": "ETF", "instrument_code": "510300.SH"},  # no initial_price
        components=[], display_name=None, source_payload={},
    )
    with pytest.raises(ValueError) as exc:
        normalize_booking_product_spec(spec)
    assert "initial_price" in str(exc.value)


# --- Agent-authored RAW term-sheet vocabulary at booking. The intended contract
# is "call build_product, then book its product_kwargs" (validate-and-wrap). But
# an agent may hand-author raw vocabulary directly at book_position — the SAME
# vocabulary the build_product tool accepts (initial_price as the S0/validation
# spot, maturity_years or maturity_date). The booking gate must translate it via
# synthesis instead of rejecting it with QuantArk's "Unsupported kwargs".
def _raw_barrier_spec(maturity_terms: dict) -> ProductBookingSpec:
    return ProductBookingSpec(
        asset_class="equity", product_family="barrier",
        quantark_class="BarrierOption", underlying="MSFT", currency="USD",
        terms={
            "strike": 384.93, "option_type": "PUT", "barrier": 307.944,
            "barrier_type": "DOWN_IN", "initial_price": 384.93,
            "contract_multiplier": 1.0, **maturity_terms,
        },
        components=[], display_name=None, source_payload={},
    )


def test_normalize_translates_raw_termsheet_vocab_barrier():
    # initial_price + maturity_years (tenor) — the build_product tool's vocabulary.
    normalized = normalize_booking_product_spec(_raw_barrier_spec({"maturity_years": 1.0}))
    # initial_price is the S0/validation spot, not a BarrierOption kwarg -> dropped
    assert "initial_price" not in normalized.terms
    assert "maturity_years" not in normalized.terms
    assert normalized.terms.get("maturity") == 1.0
    assert normalized.terms["barrier_type"] == "DOWN_IN"


def test_normalize_translates_maturity_date_barrier():
    # initial_price + maturity_date (explicit date) — maturity_date threads through
    # as QuantArk's exercise_date rather than being rejected.
    normalized = normalize_booking_product_spec(_raw_barrier_spec({"maturity_date": "2027-07-15"}))
    assert "initial_price" not in normalized.terms
    assert "maturity_date" not in normalized.terms
    assert normalized.terms.get("exercise_date")


def test_normalize_prebuilt_barrier_kwargs_wrap_unchanged():
    # Regression: the intended contract (build_product output -> book verbatim)
    # must still pass through untouched, not be re-synthesized.
    built = build_product("BarrierOption", {
        "strike": 384.93, "option_type": "PUT", "barrier": 307.944,
        "barrier_type": "DOWN_IN", "maturity_years": 1.0, "initial_price": 384.93,
    })
    assert built.ok, built.validation
    spec = ProductBookingSpec(
        asset_class="equity", product_family="barrier",
        quantark_class="BarrierOption", underlying="MSFT", currency="USD",
        terms=dict(built.product_kwargs), components=[], display_name=None, source_payload={},
    )
    normalized = normalize_booking_product_spec(spec)
    assert normalized.terms == spec.terms


def test_normalize_preserves_precise_error_for_invalid_prebuilt_barrier():
    # A genuinely-invalid PRE-BUILT termsheet (clean shape, bad enum value, no raw
    # term-sheet vocabulary) must keep its precise validation error — the synthesis
    # fallback must not mask it with an "Incomplete … missing" message.
    spec = ProductBookingSpec(
        asset_class="equity", product_family="barrier",
        quantark_class="BarrierOption", underlying="MSFT", currency="USD",
        terms={
            "strike": 384.93, "option_type": "PUT", "barrier": 307.944,
            "barrier_type": "SIDEWAYS_IN", "maturity": 1.0,
        },
        components=[], display_name=None, source_payload={},
    )
    with pytest.raises(ValueError) as exc:
        normalize_booking_product_spec(spec)
    message = str(exc.value)
    assert message.startswith("Invalid BarrierOption booking terms:")
    assert "Incomplete" not in message


def test_book_barrier_from_raw_agent_vocab_persists_clean_kwargs(session):
    # Full persisted path: an agent books a barrier directly with raw term-sheet
    # vocabulary (the Run #22 step-4 shape). The position persists with the clean
    # QuantArk kwargs, not the rejected initial_price/maturity_date.
    portfolio = Portfolio(name="Barrier Book", base_currency="USD",
                          kind=PortfolioKind.CONTAINER.value)
    session.add(portfolio)
    session.flush()

    booked = book_position(
        session,
        BookingRequest(
            portfolio_id=portfolio.id,
            product=ProductBookingSpec(
                asset_class="equity", product_family="barrier",
                quantark_class="BarrierOption", underlying="MSFT", currency="USD",
                terms={"strike": 384.93, "option_type": "PUT", "barrier": 307.944,
                       "barrier_type": "DOWN_IN", "maturity_years": 1.0,
                       "initial_price": 384.93, "contract_multiplier": 1.0},
            ),
            quantity=1.0, entry_price=0.0, status="open",
            engine_name="BlackScholesEngine", engine_kwargs={},
            actor="desk_user", source="manual",
        ),
    )
    session.flush()

    assert booked.product_id is not None
    assert booked.product_type == "BarrierOption"
    assert booked.product_kwargs["maturity"] == 1.0
    assert booked.product_kwargs["barrier_type"] == "DOWN_IN"
    assert "initial_price" not in booked.product_kwargs
    assert session.query(Position).count() == 1


def test_booking_gate_rejects_invalid_one_touch():
    # OneTouchOption is now gated: an incomplete termsheet (no barrier) must be
    # rejected by the gate, not persisted unvalidated.
    spec = ProductBookingSpec(
        asset_class="equity", product_family="touch", quantark_class="OneTouchOption",
        underlying="000905.SH", currency="CNY",
        terms={"barrier_direction": "UP", "touch_type": "ONE_TOUCH"},  # no barrier/maturity
        components=[], display_name=None, source_payload={},
    )
    with pytest.raises(ValueError) as exc:
        normalize_booking_product_spec(spec)
    assert "OneTouchOption" in str(exc.value)


def test_booking_spot_persists_instrument_code_from_otc_metadata(session):
    # instrument_code differs from the underlying, so a silent fallback to
    # `product.underlying` would be visible here: the _otc_ metadata must survive
    # synthesis (terms -> _otc_instrument_code) into the equity_spot_products row.
    portfolio = Portfolio(name="DeltaOne Book", base_currency="CNY", kind=PortfolioKind.CONTAINER.value)
    session.add(portfolio)
    session.flush()

    booked = book_position(
        session,
        BookingRequest(
            portfolio_id=portfolio.id,
            product=ProductBookingSpec(
                asset_class="equity",
                product_family="spot",
                quantark_class="SpotInstrument",
                underlying="CSI300",
                currency="CNY",
                terms={"deltaone_type": "ETF", "instrument_code": "510300.SH",
                       "exchange": "SSE", "initial_price": 4.2},
            ),
            quantity=100.0,
            entry_price=4.2,
            engine_name="DeltaOneEngine",
        ),
    )
    session.flush()

    row = session.query(EquitySpotProduct).filter_by(product_id=booked.product_id).one()
    assert row.instrument_code == "510300.SH"  # NOT the "CSI300" underlying fallback
    assert row.exchange == "SSE"
    assert row.deltaone_type == "ETF"
    # the stored QuantArk terms carry the metadata as _otc_ (popped before pricing);
    # the bare instrument_code kwarg QuantArk would reject is absent.
    assert booked.product_kwargs["_otc_instrument_code"] == "510300.SH"
    assert "instrument_code" not in booked.product_kwargs


def test_booking_without_engine_still_normalizes_through_the_gate(session):
    """Engine-config era: positions may book with engine_name=None (the engine is
    resolved per-task from the engine config). The booking gate must still
    normalize/synthesize the product spec — skipping only the engine-specific
    validation, not the normalization."""
    portfolio = Portfolio(name="NoEngine", base_currency="CNY", kind=PortfolioKind.CONTAINER.value)
    session.add(portfolio)
    session.flush()

    booked = book_position(
        session,
        BookingRequest(
            portfolio_id=portfolio.id,
            product=ProductBookingSpec(
                asset_class="equity", product_family="futures", quantark_class="Futures",
                underlying="CSI300", currency="CNY",
                terms={"contract_code": "IF2406.CFE", "contract_multiplier": 300.0,
                       "maturity_years": 0.5, "initial_price": 100.0},
            ),
            quantity=10.0,
            source_trade_id="NOENG-1",
            engine_name=None,
        ),
    )
    session.flush()

    assert booked.engine_name is None
    # raw desk terms were synthesized exactly as in the engine-named path
    assert booked.product_kwargs["underlying"] == "CSI300"
    assert booked.product_kwargs["_otc_contract_code"] == "IF2406.CFE"
    assert "contract_code" not in booked.product_kwargs

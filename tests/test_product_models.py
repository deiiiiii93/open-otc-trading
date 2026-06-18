from __future__ import annotations

from app.models import (
    EquityAsianObservation,
    EquityAsianProduct,
    EquityAutocallableProduct,
    EquityAutocallableObservation,
    EquityBarrierProduct,
    EquityFuturesProduct,
    EquityOptionProduct,
    EquityPhoenixCouponProduct,
    EquityProductComponent,
    EquityRangeAccrualObservation,
    EquityRangeAccrualProduct,
    EquitySharkfinProduct,
    EquitySpotProduct,
    EquityTouchProduct,
    Product,
)
from app.services.domains.products import (
    ProductSpec,
    compatibility_terms,
    create_or_get_product,
    product_family_for_quantark_class,
    query_autocallable_observations,
    product_spec_from_position_payload,
)


def test_create_snowball_product_with_ko_schedule(session):
    spec = ProductSpec(
        asset_class="equity",
        product_family="autocallable",
        quantark_class="SnowballOption",
        underlying="000300.SH",
        currency="CNY",
        terms={
            "initial_price": 100.0,
            "strike": 100.0,
            "maturity": 1.0,
            "contract_multiplier": 1.0,
            "barrier_config": {
                "ko_barrier": 103.0,
                "ko_rate": 0.15,
                "ki_barrier": 75.0,
                "ko_observation_schedule": [
                    {
                        "observation_date": "2026-06-30",
                        "barrier_level": 103.0,
                        "rate": 0.15,
                    }
                ],
            },
            "payoff_config": {"include_principal": False},
            "accrual_config": {},
        },
    )

    product = create_or_get_product(session, spec, reuse=False)
    session.flush()

    assert product.id is not None
    assert product.product_family == "autocallable"
    assert product.quantark_class == "SnowballOption"
    rows = (
        session.query(EquityAutocallableObservation)
        .filter_by(product_id=product.id)
        .all()
    )
    assert [(row.observation_role, row.sequence, row.barrier_level) for row in rows] == [
        ("ko", 0, 103.0)
    ]


def test_query_autocallable_observations_without_product_kwargs(session):
    product = create_or_get_product(
        session,
        ProductSpec(
            asset_class="equity",
            product_family="autocallable",
            quantark_class="SnowballOption",
            underlying="000300.SH",
            currency="CNY",
            terms={
                "initial_price": 100.0,
                "strike": 100.0,
                "barrier_config": {
                    "ko_barrier": 103.0,
                    "ko_observation_schedule": [
                        {"observation_date": "2026-06-30"}
                    ],
                },
            },
        ),
        reuse=False,
    )
    session.flush()

    rows = query_autocallable_observations(session, product_id=product.id, role="ko")

    assert rows[0]["observation_role"] == "ko"
    assert rows[0]["barrier_level"] == 103.0


def test_query_autocallable_observations_unfiltered_orders_by_date(session):
    product = create_or_get_product(
        session,
        ProductSpec(
            asset_class="equity",
            product_family="autocallable",
            quantark_class="PhoenixOption",
            underlying="000300.SH",
            currency="CNY",
            terms={
                "initial_price": 100.0,
                "strike": 100.0,
                "barrier_config": {
                    "ki_barrier": 75.0,
                    "ko_barrier": 103.0,
                    "ki_observation_schedule": [
                        {"observation_date": "2026-06-15"}
                    ],
                    "ko_observation_schedule": [
                        {"observation_date": "2026-06-30"}
                    ],
                },
                "coupon_config": {
                    "coupon_observation_schedule": [
                        {"observation_date": "2026-06-20"}
                    ]
                },
                "accrual_config": {
                    "accrual_schedule": [
                        {"observation_date": "2026-06-10"}
                    ]
                },
            },
        ),
        reuse=False,
    )
    session.flush()

    rows = query_autocallable_observations(session, product_id=product.id)

    assert [row["observation_date"] for row in rows] == [
        "2026-06-10",
        "2026-06-15",
        "2026-06-20",
        "2026-06-30",
    ]
    assert [row["observation_role"] for row in rows] == [
        "accrual",
        "ki",
        "coupon",
        "ko",
    ]


def test_autocallable_boolean_strings_are_parsed(session):
    product = create_or_get_product(
        session,
        ProductSpec(
            asset_class="equity",
            product_family="autocallable",
            quantark_class="SnowballOption",
            underlying="000300.SH",
            currency="CNY",
            terms={
                "is_reverse": "false",
                "initial_price": 100.0,
                "strike": 100.0,
                "barrier_config": {
                    "ki_continuous": "0",
                    "disable_ko_after_ki": "false",
                },
                "payoff_config": {
                    "call_rebate_enabled": "false",
                    "include_principal": "0",
                },
                "accrual_config": {
                    "is_annualized": "false",
                    "is_annualized_ko": "0",
                    "is_annualized_ki": "true",
                },
            },
        ),
        reuse=False,
    )

    terms = session.get(EquityAutocallableProduct, product.id)
    assert terms is not None
    assert terms.is_reverse is False
    assert terms.ki_continuous is False
    assert terms.disable_ko_after_ki is False
    assert terms.payoff_call_rebate_enabled is False
    assert terms.payoff_include_principal is False
    assert terms.accrual_is_annualized is False
    assert terms.accrual_is_annualized_ko is False
    assert terms.accrual_is_annualized_ki is True


def test_create_fund_product_maps_to_etf_spot(session):
    spec = ProductSpec(
        asset_class="equity",
        product_family="spot",
        quantark_class="SpotInstrument",
        underlying="510300.SH",
        currency="CNY",
        terms={
            "deltaone_type": "ETF",
            "instrument_code": "510300.SH",
            "contract_multiplier": 1.0,
        },
    )

    product = create_or_get_product(session, spec, reuse=False)
    terms = compatibility_terms(product)

    assert product.product_family == "spot"
    assert terms["product_type"] == "SpotInstrument"
    assert terms["product_kwargs"]["deltaone_type"] == "ETF"
    spot = session.get(EquitySpotProduct, product.id)
    assert spot is not None
    assert spot.deltaone_type == "ETF"


def test_create_or_get_product_reuses_by_hash_only_when_requested(session):
    spec = ProductSpec(
        asset_class="equity",
        product_family="option",
        quantark_class="EuropeanVanillaOption",
        underlying="AAPL",
        terms={"strike": 100, "maturity": 1, "option_type": "CALL"},
    )

    first = create_or_get_product(session, spec, reuse=True)
    second = create_or_get_product(session, spec, reuse=True)
    third = create_or_get_product(session, spec, reuse=False)
    session.flush()

    assert first.id == second.id
    assert third.id != first.id
    assert session.query(Product).count() == 2


def test_product_spec_from_position_payload_maps_known_family(session):
    spec = product_spec_from_position_payload(
        {
            "underlying": "IF2406",
            "product_type": "Futures",
            "currency": "CNY",
            "product_kwargs": {"contract_code": "IF2406", "basis": 0.01},
        }
    )

    assert spec.product_family == "futures"
    assert spec.quantark_class == "Futures"
    assert spec.terms["contract_code"] == "IF2406"


def test_product_family_uses_package_only_for_explicit_components():
    assert product_family_for_quantark_class("VerticalSpreadOption") == "option"
    assert (
        product_family_for_quantark_class(
            "VerticalSpreadOption", components=[{"component_product_id": 1}]
        )
        == "package"
    )


def test_family_writers_create_approved_family_rows(session):
    option = create_or_get_product(
        session,
        ProductSpec(
            asset_class="equity",
            product_family="option",
            quantark_class="EuropeanVanillaOption",
            underlying="AAPL",
            terms={"strike": 100.0, "maturity": 1.0, "option_type": "CALL"},
        ),
        reuse=False,
    )
    phoenix = create_or_get_product(
        session,
        ProductSpec(
            asset_class="equity",
            product_family="autocallable",
            quantark_class="PhoenixOption",
            underlying="000300.SH",
            currency="CNY",
            terms={
                "initial_price": 100.0,
                "strike": 100.0,
                "barrier_config": {"ko_barrier": 103.0, "ki_barrier": 75.0},
                "coupon_config": {"coupon_barrier": 80.0, "coupon_rate": 0.12},
            },
        ),
        reuse=False,
    )
    barrier = create_or_get_product(
        session,
        ProductSpec(
            asset_class="equity",
            product_family="barrier",
            quantark_class="DoubleBarrierOption",
            underlying="SPY",
            terms={"upper_barrier": 120.0, "lower_barrier": 80.0, "rebate": 1.0},
        ),
        reuse=False,
    )
    touch = create_or_get_product(
        session,
        ProductSpec(
            asset_class="equity",
            product_family="touch",
            quantark_class="DoubleOneTouchOption",
            underlying="SPY",
            terms={
                "upper_barrier": 120.0,
                "lower_barrier": 80.0,
                "touch_type": "DOUBLE_NO_TOUCH",
            },
        ),
        reuse=False,
    )
    asian = create_or_get_product(
        session,
        ProductSpec(
            asset_class="equity",
            product_family="asian",
            quantark_class="AsianOption",
            underlying="AAPL",
            terms={
                "averaging_method": "arithmetic",
                "observation_dates": [{"observation_date": "2026-06-30"}],
            },
        ),
        reuse=False,
    )
    range_accrual = create_or_get_product(
        session,
        ProductSpec(
            asset_class="equity",
            product_family="range_accrual",
            quantark_class="RangeAccrualOption",
            underlying="AAPL",
            terms={
                "range_config": {
                    "lower_barrier": 80.0,
                    "upper_barrier": 120.0,
                    "accrual_rate": 0.08,
                    "observation_schedule": [{"observation_date": "2026-06-30"}],
                }
            },
        ),
        reuse=False,
    )
    sharkfin = create_or_get_product(
        session,
        ProductSpec(
            asset_class="equity",
            product_family="sharkfin",
            quantark_class="SingleSharkfinOption",
            underlying="AAPL",
            terms={"barrier": 120.0, "participation_rate": 1.0},
        ),
        reuse=False,
    )
    futures = create_or_get_product(
        session,
        ProductSpec(
            asset_class="equity",
            product_family="futures",
            quantark_class="Futures",
            underlying="IF2406",
            currency="CNY",
            terms={"contract_code": "IF2406", "basis": 0.01},
        ),
        reuse=False,
    )

    assert session.get(EquityOptionProduct, option.id) is not None
    assert session.get(EquityOptionProduct, phoenix.id) is not None
    assert session.get(EquityAutocallableProduct, phoenix.id) is not None
    assert session.get(EquityPhoenixCouponProduct, phoenix.id) is not None
    assert session.get(EquityBarrierProduct, barrier.id).barrier_kind == "double"
    assert session.get(EquityTouchProduct, touch.id).touch_kind == "double_no_touch"
    assert session.get(EquityAsianProduct, asian.id) is not None
    assert session.get(EquityAsianObservation, (asian.id, 0)) is not None
    assert session.get(EquityRangeAccrualProduct, range_accrual.id).accrual_rate == 0.08
    assert session.get(EquityRangeAccrualObservation, (range_accrual.id, 0)) is not None
    assert session.get(EquitySharkfinProduct, sharkfin.id).sharkfin_kind == "single"
    assert session.get(EquityFuturesProduct, futures.id).contract_code == "IF2406"


def test_package_components_are_written(session):
    child = create_or_get_product(
        session,
        ProductSpec(
            asset_class="equity",
            product_family="option",
            quantark_class="EuropeanVanillaOption",
            underlying="AAPL",
            terms={"strike": 100.0, "maturity": 1.0, "option_type": "CALL"},
        ),
        reuse=False,
    )
    parent = create_or_get_product(
        session,
        ProductSpec(
            asset_class="equity",
            product_family="package",
            quantark_class="CallPutPortfolio",
            underlying="AAPL",
            terms={"strategy": "call_put_portfolio"},
            components=[
                {
                    "component_product_id": child.id,
                    "component_role": "long_leg",
                    "quantity": 1.0,
                    "sequence": 0,
                }
            ],
        ),
        reuse=False,
    )
    session.flush()
    component_rows = (
        session.query(EquityProductComponent)
        .filter_by(parent_product_id=parent.id)
        .order_by(EquityProductComponent.sequence)
        .all()
    )
    assert [(row.component_role, row.quantity) for row in component_rows] == [
        ("long_leg", 1.0)
    ]
    compatibility = compatibility_terms(parent)
    assert compatibility["product_kwargs"]["components"] == [
        {
            "component_product_id": child.id,
            "component_role": "long_leg",
            "quantity": 1.0,
            "sequence": 0,
        }
    ]


def test_nested_package_component_is_classified_as_package(session):
    child = create_or_get_product(
        session,
        ProductSpec(
            asset_class="equity",
            product_family="option",
            quantark_class="EuropeanVanillaOption",
            underlying="AAPL",
            terms={"strike": 100.0, "maturity": 1.0, "option_type": "CALL"},
        ),
        reuse=False,
    )

    parent = create_or_get_product(
        session,
        ProductSpec(
            asset_class="equity",
            product_family="package",
            quantark_class="LadderBinary",
            underlying="AAPL",
            terms={"strategy": "ladder_binary"},
            components=[
                {
                    "component_role": "digital_leg",
                    "product": {
                        "asset_class": "equity",
                        "quantark_class": "VerticalSpreadOption",
                        "underlying": "AAPL",
                        "terms": {"strategy": "vertical_spread"},
                        "components": [
                            {
                                "component_product_id": child.id,
                                "component_role": "long_leg",
                                "quantity": 1.0,
                            }
                        ],
                    },
                }
            ],
        ),
        reuse=False,
    )
    session.flush()

    parent_component = (
        session.query(EquityProductComponent)
        .filter_by(parent_product_id=parent.id)
        .one()
    )
    nested_product = session.get(Product, parent_component.component_product_id)

    assert nested_product is not None
    assert nested_product.product_family == "package"


def test_nested_component_accepts_legacy_position_payload_shape(session):
    parent = create_or_get_product(
        session,
        ProductSpec(
            asset_class="equity",
            product_family="package",
            quantark_class="CallPutPortfolio",
            underlying="AAPL",
            terms={"strategy": "call_put_portfolio"},
            components=[
                {
                    "component_role": "long_leg",
                    "product": {
                        "underlying": "AAPL",
                        "product_type": "EuropeanVanillaOption",
                        "product_kwargs": {
                            "strike": 100.0,
                            "maturity": 1.0,
                            "option_type": "CALL",
                        },
                    },
                }
            ],
        ),
        reuse=False,
    )
    session.flush()

    parent_component = (
        session.query(EquityProductComponent)
        .filter_by(parent_product_id=parent.id)
        .one()
    )
    nested_product = session.get(Product, parent_component.component_product_id)

    assert nested_product is not None
    assert nested_product.quantark_class == "EuropeanVanillaOption"
    assert nested_product.raw_terms["terms"]["strike"] == 100.0

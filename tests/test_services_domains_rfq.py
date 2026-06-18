from __future__ import annotations

from app.services.domains import rfq as rfq_svc


def test_catalog_dispatches_through_facade(monkeypatch):
    """Facade re-export resolves to the underlying services.rfq function."""
    calls: dict[str, int] = {"n": 0}

    def fake_catalog() -> dict:
        calls["n"] += 1
        return {"product_types": []}

    monkeypatch.setattr(rfq_svc, "get_rfq_catalog", fake_catalog)
    result = rfq_svc.get_rfq_catalog()
    assert result == {"product_types": []}
    assert calls["n"] == 1


def test_facade_exports_full_lifecycle():
    """All twelve lifecycle entry points are wired through the facade."""
    expected = {
        "get_rfq_catalog",
        "draft_from_natural_language",
        "validate_rfq_terms",
        "solve_rfq",
        "create_rfq_draft",
        "update_rfq_draft",
        "submit_rfq_for_approval",
        "quote_rfq",
        "approve_rfq",
        "reject_rfq",
        "release_rfq",
        "mark_client_accepted",
        "book_rfq_to_position",
    }
    missing = expected - set(dir(rfq_svc))
    assert not missing, f"facade missing entries: {missing}"


def test_facade_identity_matches_source():
    """Re-exports must be the same callable as the source — no accidental shadowing."""
    from app.services import rfq as src_rfq
    from app.services.quantark import solve_rfq as src_solve

    assert rfq_svc.get_rfq_catalog is src_rfq.get_rfq_catalog
    assert rfq_svc.quote_rfq is src_rfq.quote_rfq
    assert rfq_svc.book_rfq_to_position is src_rfq.book_rfq_to_position
    assert rfq_svc.solve_rfq is src_solve


def test_snowball_template_carries_flat_contract_terms():
    from app.services.rfq import COMMON_TEMPLATES, _BUILD_PRODUCT_FAMILIES

    snowball = next(t for t in COMMON_TEMPLATES if t["key"] == "snowball")
    assert snowball["product_type"] in _BUILD_PRODUCT_FAMILIES
    pk = snowball["product_kwargs"]
    # FLAT contract inputs, not a nested QuantArk barrier_config
    assert "barrier_config" not in pk
    for key in ("ko_barrier_pct", "ki_barrier_pct", "ko_rate",
                "lockup_months", "trade_start_date", "observation_frequency"):
        assert key in pk, key


def test_executable_kwargs_synthesizes_complete_snowball_termsheet():
    from app.schemas import RFQRequestDraft, RFQUnknownSpecIn, RFQEngineSpecIn, PricingEnvironmentSnapshot
    from app.services.rfq import _executable_product_kwargs

    draft = RFQRequestDraft(
        underlying="000905.SH", quote_mode="solve", product_type="SnowballOption",
        market=PricingEnvironmentSnapshot(currency="HKD"),
        engine_spec=RFQEngineSpecIn(engine_name="SnowballQuadEngine"),
        product_kwargs={
            "initial_price": 100.0, "strike": 100.0, "maturity_years": 1.0,
            "ko_barrier_pct": 103.0, "ki_barrier_pct": 75.0, "ko_rate": 0.15,
            "lockup_months": 3, "trade_start_date": "2026-01-05",
            "observation_frequency": "MONTHLY", "contract_multiplier": 1.0,
        },
        unknown=RFQUnknownSpecIn(field_path="barrier_config.ko_rate",
                                 lower_bound=0.05, upper_bound=0.40, initial_guess=0.15),
    )
    kwargs, missing = _executable_product_kwargs(draft, quote_mode="solve")
    assert missing == []
    assert kwargs["barrier_config"]["ko_observation_schedule"]["records"]  # synthesized


def test_executable_kwargs_reports_missing_contract_inputs():
    from app.schemas import RFQRequestDraft, RFQUnknownSpecIn, RFQEngineSpecIn
    from app.services.rfq import _executable_product_kwargs

    draft = RFQRequestDraft(
        underlying="000905.SH", quote_mode="solve", product_type="SnowballOption",
        engine_spec=RFQEngineSpecIn(engine_name="SnowballQuadEngine"),
        product_kwargs={"initial_price": 100.0, "ko_barrier_pct": 103.0},  # incomplete
        unknown=RFQUnknownSpecIn(field_path="barrier_config.ko_rate",
                                 lower_bound=0.05, upper_bound=0.40, initial_guess=0.15),
    )
    kwargs, missing = _executable_product_kwargs(draft, quote_mode="solve")
    assert kwargs == {}
    assert "observation_frequency" in missing  # contract gap surfaced precisely


def test_executable_kwargs_passthrough_for_non_build_product_family():
    from app.schemas import RFQRequestDraft
    from app.services.rfq import _executable_product_kwargs

    draft = RFQRequestDraft(product_type="EuropeanVanillaOption",
                            product_kwargs={"strike": 100.0, "option_type": "CALL", "maturity": 1.0})
    kwargs, missing = _executable_product_kwargs(draft, quote_mode="solve")
    assert missing == []
    assert kwargs == draft.product_kwargs  # unchanged for non-snowball families


def test_validate_rfq_terms_gates_unfilled_snowball_contract():
    from app.schemas import RFQRequestDraft, RFQUnknownSpecIn, RFQEngineSpecIn, RFQTargetIn
    from app.services.rfq import validate_rfq_terms

    draft = RFQRequestDraft(
        underlying="000905.SH", quote_mode="solve", product_type="SnowballOption",
        engine_spec=RFQEngineSpecIn(engine_name="SnowballQuadEngine"),
        product_kwargs={"initial_price": 100.0, "ko_barrier_pct": 103.0},  # unfilled
        unknown=RFQUnknownSpecIn(field_path="barrier_config.ko_rate",
                                 lower_bound=0.05, upper_bound=0.40, initial_guess=0.15),
        target=RFQTargetIn(label="price", value=5.0),
    )
    result = validate_rfq_terms(draft, "solve")
    assert result["valid"] is False
    # precise contract gap, not the opaque "KO observation … required"
    assert any("observation_frequency" in m for m in result["missing_fields"])
    assert not any("KO observation" in e for e in result["errors"])


def test_validate_rfq_terms_accepts_filled_snowball_contract():
    from app.schemas import RFQRequestDraft, RFQUnknownSpecIn, RFQEngineSpecIn, RFQTargetIn
    from app.services.rfq import validate_rfq_terms

    draft = RFQRequestDraft(
        underlying="000905.SH", quote_mode="solve", product_type="SnowballOption",
        engine_spec=RFQEngineSpecIn(engine_name="SnowballQuadEngine"),
        product_kwargs={
            "initial_price": 100.0, "strike": 100.0, "maturity_years": 1.0,
            "ko_barrier_pct": 103.0, "ki_barrier_pct": 75.0, "ko_rate": 0.15,
            "lockup_months": 3, "trade_start_date": "2026-01-05",
            "observation_frequency": "MONTHLY", "contract_multiplier": 1.0,
        },
        unknown=RFQUnknownSpecIn(field_path="barrier_config.ko_rate",
                                 lower_bound=0.05, upper_bound=0.40, initial_guess=0.15),
        target=RFQTargetIn(label="price", value=5.0),
    )
    result = validate_rfq_terms(draft, "solve")
    assert result["valid"] is True, result


def test_quote_rfq_snowball_solve_produces_pending_approval(session):
    from datetime import date, timedelta

    from app.schemas import (RFQRequestDraft, RFQUnknownSpecIn, RFQEngineSpecIn,
                             RFQTargetIn, RFQQuoteRequest)
    from app.services.rfq import create_rfq_draft, quote_rfq
    from app.models import RfqStatus

    # Future trade start: the snowball schedule must fall after the market
    # valuation date (utcnow), else QuantArk rejects a past-dated schedule.
    trade_start = (date.today() + timedelta(days=30)).isoformat()
    draft = RFQRequestDraft(
        underlying="000905.SH", quantity=1_000_000.0, quote_mode="solve",
        product_type="SnowballOption",
        engine_spec=RFQEngineSpecIn(engine_name="SnowballQuadEngine"),
        product_kwargs={
            "initial_price": 100.0, "strike": 100.0, "maturity_years": 1.0,
            "ko_barrier_pct": 103.0, "ki_barrier_pct": 75.0, "ko_rate": 0.15,
            "lockup_months": 3, "trade_start_date": trade_start,
            "observation_frequency": "MONTHLY", "contract_multiplier": 1.0,
        },
        unknown=RFQUnknownSpecIn(field_path="barrier_config.ko_rate",
                                 lower_bound=0.05, upper_bound=0.40, initial_guess=0.15),
        target=RFQTargetIn(label="price", value=5.0),  # inside the spike's price band
    )
    rfq = create_rfq_draft(session, draft, channel="desk")
    quoted = quote_rfq(session, rfq.id, RFQQuoteRequest(quote_mode="solve"))
    # Task 5: the solver now operates on a complete (build_product-synthesized)
    # termsheet, so it actually converges to a coupon inside the bounds — instead
    # of failing at product construction with flat kwargs. (Regenerating the
    # executable terms so this flips to PENDING_APPROVAL is Task 6.)
    solved = quoted.quote_payload.get("solved_value")
    assert solved is not None, quoted.quote_payload
    assert 0.05 <= solved <= 0.40, solved
    # The opaque "KO observation … required" build error must never appear.
    assert "KO observation" not in (quoted.quote_payload.get("quantark_error") or "")
    assert quoted.status in {RfqStatus.PENDING_APPROVAL.value, RfqStatus.PRICING_FAILED.value}


def test_executable_terms_regenerates_schedule_rates_from_solved_value():
    from app.schemas import RFQRequestDraft, RFQUnknownSpecIn, RFQEngineSpecIn
    from app.services.rfq import _executable_terms_for_quote, _executable_product_kwargs

    draft = RFQRequestDraft(
        underlying="000905.SH", quote_mode="solve", product_type="SnowballOption",
        engine_spec=RFQEngineSpecIn(engine_name="SnowballQuadEngine"),
        product_kwargs={
            "initial_price": 100.0, "strike": 100.0, "maturity_years": 1.0,
            "ko_barrier_pct": 103.0, "ki_barrier_pct": 75.0, "ko_rate": 0.15,
            "lockup_months": 3, "trade_start_date": "2026-01-05",
            "observation_frequency": "MONTHLY", "contract_multiplier": 1.0,
        },
        unknown=RFQUnknownSpecIn(field_path="barrier_config.ko_rate",
                                 lower_bound=0.05, upper_bound=0.40, initial_guess=0.15),
    )
    quote_payload = {"solved_value": 0.2233, "field_path": "barrier_config.ko_rate"}
    terms = _executable_terms_for_quote(draft, "solve", quote_payload)

    records = terms["product_kwargs"]["barrier_config"]["ko_observation_schedule"]["records"]
    # every record's return_rate reflects the SOLVED coupon, not the 0.15 placeholder
    assert all(abs(r["return_rate"] - 0.2233) < 1e-9 for r in records)
    # and a direct build with the same solved coupon yields identical schedule
    direct, _ = _executable_product_kwargs(
        draft.model_copy(update={"product_kwargs": {**draft.product_kwargs, "ko_rate": 0.2233},
                                 "quote_mode": "price"}),
        quote_mode="price",
    )
    assert terms["product_kwargs"]["barrier_config"]["ko_observation_schedule"] == \
        direct["barrier_config"]["ko_observation_schedule"]


def test_rfq_snowball_books_identical_to_direct_build(session):
    from datetime import date, timedelta

    from app.schemas import (RFQRequestDraft, RFQUnknownSpecIn, RFQEngineSpecIn,
                             RFQTargetIn, RFQQuoteRequest, RFQApprovalDecision,
                             RFQReleaseRequest, RFQClientAcceptRequest, RFQBookRequest)
    from app.services.rfq import (create_rfq_draft, quote_rfq, approve_rfq, release_rfq,
                                  mark_client_accepted, book_rfq_to_position)
    from app.services.domains.product_builders import build_product
    from app.models import Portfolio, PortfolioKind, RfqStatus

    portfolio = Portfolio(name="RFQ Book", base_currency="CNY",
                          kind=PortfolioKind.CONTAINER.value)
    session.add(portfolio); session.flush()

    trade_start = (date.today() + timedelta(days=30)).isoformat()
    flat = {
        "initial_price": 100.0, "strike": 100.0, "maturity_years": 1.0,
        "ko_barrier_pct": 103.0, "ki_barrier_pct": 75.0, "ko_rate": 0.15,
        "lockup_months": 3, "trade_start_date": trade_start,
        "observation_frequency": "MONTHLY", "contract_multiplier": 1.0,
    }
    draft = RFQRequestDraft(
        underlying="000905.SH", quantity=1_000_000.0, quote_mode="solve",
        product_type="SnowballOption",
        engine_spec=RFQEngineSpecIn(engine_name="SnowballQuadEngine"),
        product_kwargs=dict(flat),
        unknown=RFQUnknownSpecIn(field_path="barrier_config.ko_rate",
                                 lower_bound=0.05, upper_bound=0.40, initial_guess=0.15),
        target=RFQTargetIn(label="price", value=5.0),  # inside the spike's price band
    )
    rfq = create_rfq_draft(session, draft, channel="desk")
    rfq = quote_rfq(session, rfq.id, RFQQuoteRequest(quote_mode="solve"))
    assert rfq.status == RfqStatus.PENDING_APPROVAL.value, rfq.quote_payload
    solved = rfq.quote_payload["solved_value"]

    approve_rfq(session, rfq.id, RFQApprovalDecision(approver="trader"))
    release_rfq(session, rfq.id, RFQReleaseRequest(actor="trader"))
    mark_client_accepted(session, rfq.id, RFQClientAcceptRequest(actor="client"))
    position = book_rfq_to_position(session, rfq.id, RFQBookRequest(portfolio_id=portfolio.id))
    session.flush()

    assert position.product_type == "SnowballOption"
    booked_sched = position.product_kwargs["barrier_config"]["ko_observation_schedule"]
    direct = build_product("SnowballOption", {**flat, "ko_rate": solved},
                           underlying="000905.SH", currency="CNY")
    assert direct.ok
    assert booked_sched == direct.product_kwargs["barrier_config"]["ko_observation_schedule"]


def test_rfq_booking_preserves_currency_from_market(session, monkeypatch):
    from app.models import Portfolio, PortfolioKind, RFQQuoteVersion, RfqStatus
    from app.schemas import RFQBookRequest, RFQRequestDraft
    from app.services import rfq as rfq_module
    from app.services.rfq import book_rfq_to_position

    portfolio = Portfolio(name="HK RFQ Book", base_currency="HKD",
                          kind=PortfolioKind.CONTAINER.value)
    session.add(portfolio); session.flush()

    terms = {
        "client_name": "Demo Client",
        "underlying": "0700.HK",
        "side": "buy",
        "quantity": 1000,
        "quote_mode": "price",
        "product_type": "EuropeanVanillaOption",
        "product_kwargs": {
            "strike": 100.0,
            "option_type": "CALL",
            "maturity": 1.0,
            "contract_multiplier": 1.0,
        },
        "market": {"spot": 100.0, "volatility": 0.2, "rate": 0.03, "dividend_yield": 0.0, "currency": "HKD"},
        "engine_spec": {"engine_name": "BlackScholesEngine"},
    }
    rfq = rfq_svc.create_rfq_draft(session, RFQRequestDraft.model_validate(terms), channel="form")
    rfq.status = RfqStatus.CLIENT_ACCEPTED.value
    session.add(RFQQuoteVersion(
        rfq_id=rfq.id,
        version=1,
        quote_mode="price",
        status=RfqStatus.RELEASED.value,
        request_payload={"terms": terms, "executable_terms": terms},
        quote_payload={"unit_price": 1.23, "quote_amount_currency": "HKD"},
        created_by="test",
    ))
    session.flush()

    captured = {}

    def fake_book_position(session, request, *, reuse_product=True):
        captured["product_currency"] = request.product.currency
        from app.models import Position, Product
        product = Product(
            asset_class=request.product.asset_class,
            product_family=request.product.product_family,
            quantark_class=request.product.quantark_class,
            underlying=request.product.underlying,
            currency=request.product.currency,
            term_hash="hk-test",
            raw_terms={"terms": request.product.terms, "components": request.product.components},
            source_payload=request.product.source_payload or {},
        )
        position = Position(
            portfolio_id=request.portfolio_id,
            product=product,
            product_id=product.id,
            underlying=request.product.underlying,
            product_type=request.product.quantark_class,
            product_kwargs=request.product.terms,
            quantity=request.quantity,
            entry_price=request.entry_price,
            currency=request.product.currency,
            status=request.status,
            source_payload=request.source_payload,
            rfq_id=request.rfq_id,
            rfq_quote_version_id=request.rfq_quote_version_id,
        )
        session.add_all([product, position])
        session.flush()
        return position

    monkeypatch.setattr(rfq_module, "book_position", fake_book_position)

    position = book_rfq_to_position(session, rfq.id, RFQBookRequest(portfolio_id=portfolio.id))

    assert position.product.currency == "HKD"
    assert position.currency == "HKD"
    assert captured["product_currency"] == "HKD"
    assert position.source_payload["executable_terms"]["market"]["currency"] == "HKD"

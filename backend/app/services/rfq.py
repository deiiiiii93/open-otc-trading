from __future__ import annotations

import re
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from ..models import Approval, Position, RFQ, RFQQuoteVersion, RfqStatus
from ..schemas import (
    PricingEnvironmentSnapshot,
    RFQApprovalDecision,
    RFQBookRequest,
    RFQClientAcceptRequest,
    RFQDraftCreate,
    RFQDraftFromNLOut,
    RFQDraftUpdate,
    RFQEngineSpecIn,
    RFQQuoteRequest,
    RFQReleaseRequest,
    RFQRequestDraft,
    RFQTargetIn,
    RFQUnknownSpecIn,
)
from .audit import record_audit
from .currency_codes import ISO_4217_CODES, normalize_currency
from .domains.booking import BookingRequest, ProductBookingSpec, book_position
from .domains.products import product_spec_from_executable_terms
from .underlyings import resolve_underlying_currency
from .quantark import (
    _ensure_sharkfin_registry_support,
    ensure_quantark_path,
    model_to_dict,
    price_product,
    solve_rfq as quantark_solve_rfq,
    validate_quantark_build,
)


# Families whose RFQ templates carry the FLAT term contract and are synthesized
# into a complete QuantArk termsheet by build_product (the single producer).
_BUILD_PRODUCT_FAMILIES = {
    "SnowballOption",
    "KnockOutResetSnowballOption",
    "PhoenixOption",
}


def _default_trade_start_date() -> str:
    """A near-future trade start so synthesized KO observation dates fall after
    the market valuation date (``utcnow``). A frozen literal would, once that
    date passes, price as an already-expired schedule and QuantArk rejects it
    with ``end_date ... must be after start_date ...``. Templates are seed
    defaults the desk edits before quoting; computed at import is sufficient.
    """
    return (date.today() + timedelta(days=7)).isoformat()


COMMON_TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "vanilla",
        "label": "Vanilla",
        "product_type": "EuropeanVanillaOption",
        "engine_spec": {"engine_name": "BlackScholesEngine"},
        "unknown_fields": ["strike", "volatility"],
        "product_kwargs": {
            "strike": 100.0,
            "option_type": "CALL",
            "maturity": 1.0,
            "contract_multiplier": 1.0,
        },
    },
    {
        "key": "american",
        "label": "American",
        "product_type": "AmericanOption",
        "engine_spec": {"engine_name": "AmericanOptionAnalyticalEngine"},
        "unknown_fields": ["strike", "volatility"],
        "product_kwargs": {
            "strike": 100.0,
            "option_type": "CALL",
            "maturity": 1.0,
            "contract_multiplier": 1.0,
        },
    },
    {
        "key": "digital",
        "label": "Digital",
        "product_type": "CashOrNothingDigitalOption",
        "engine_spec": {"engine_name": "DigitalOptionAnalyticalEngine"},
        "unknown_fields": ["strike", "payout"],
        "product_kwargs": {
            "strike": 100.0,
            "payout": 10.0,
            "option_type": "CALL",
            "maturity": 1.0,
            "contract_multiplier": 1.0,
        },
    },
    {
        "key": "barrier",
        "label": "Barrier",
        "product_type": "BarrierOption",
        "engine_spec": {"engine_name": "BarrierAnalyticalEngine"},
        "unknown_fields": ["strike", "barrier", "rebate"],
        "product_kwargs": {
            "strike": 100.0,
            "option_type": "CALL",
            "maturity": 1.0,
            "barrier": 75.0,
            "barrier_type": "DOWN_OUT",
            "rebate": 0.0,
            "contract_multiplier": 1.0,
        },
    },
    {
        "key": "one_touch",
        "label": "One Touch",
        "product_type": "OneTouchOption",
        "engine_spec": {"engine_name": "OneTouchAnalyticalEngine"},
        "unknown_fields": ["barrier", "rebate"],
        # OneTouchOption takes no strike/option_type/contract_multiplier; the
        # payoff is `rebate` and the direction is `barrier_direction` — mirrors
        # _build_one_touch's QuantArk output shape.
        "product_kwargs": {
            "barrier": 120.0,
            "rebate": 10.0,
            "barrier_direction": "UP",
            "touch_type": "ONE_TOUCH",
            "maturity": 1.0,
        },
    },
    {
        "key": "asian",
        "label": "Asian",
        "product_type": "AsianOption",
        "engine_spec": {"engine_name": "AsianOptionAnalyticalEngine"},
        "unknown_fields": ["strike", "volatility"],
        # Monthly averaging over the 1y tenor (mirrors _build_asian's derivation).
        "product_kwargs": {
            "strike": 100.0,
            "option_type": "CALL",
            "maturity": 1.0,
            "num_observations": 12,
            "initial_price": 100.0,
            "contract_multiplier": 1.0,
        },
    },
    {
        "key": "range_accrual",
        "label": "Range Accrual",
        "product_type": "RangeAccrualOption",
        "engine_spec": {"engine_name": "RangeAccrualAnalyticalEngine"},
        "unknown_fields": [
            "range_config.accrual_rate",
            "range_config.lower_barrier",
            "range_config.upper_barrier",
        ],
        # Direct QuantArk termsheet (non-build_product family): only kwargs the
        # strict RangeAccrualOption builder accepts. Barriers are absolute
        # levels on the 100-scale; daily observation over the 1y tenor —
        # mirrors _build_range_accrual's output shape.
        "product_kwargs": {
            "initial_price": 100.0,
            "maturity": 1.0,
            "num_observations": 252,
            "contract_multiplier": 1.0,
            "range_config": {
                "lower_barrier": 80.0,
                "upper_barrier": 120.0,
                "accrual_rate": 0.15,
                "is_rate_annualized": False,
            },
        },
    },
    {
        "key": "snowball",
        "label": "Snowball",
        "product_type": "SnowballOption",
        "engine_spec": {"engine_name": "SnowballQuadEngine"},
        "unknown_fields": ["barrier_config.ko_rate"],
        # FLAT term contract (build_product input), NOT nested QuantArk kwargs.
        "product_kwargs": {
            "initial_price": 100.0,
            "strike": 100.0,
            "maturity_years": 1.0,
            "ko_barrier_pct": 103.0,
            "ki_barrier_pct": 75.0,
            "ko_rate": 0.15,
            "lockup_months": 3,
            "trade_start_date": _default_trade_start_date(),
            "observation_frequency": "MONTHLY",
            "contract_multiplier": 1.0,
        },
    },
    {
        "key": "ko_reset_snowball",
        "label": "KO Reset Snowball",
        "product_type": "KnockOutResetSnowballOption",
        "engine_spec": {"engine_name": "KOResetSnowballQuadEngine"},
        "unknown_fields": ["barrier_config.ko_rate"],
        "product_kwargs": {
            "initial_price": 100.0,
            "strike": 100.0,
            "maturity_years": 1.0,
            "ko_barrier_pct": 103.0,
            "ki_barrier_pct": 75.0,
            "ko_rate": 0.15,
            "post_ko_barrier_pct": 100.0,
            "post_ko_rate": 0.10,
            "lockup_months": 3,
            "trade_start_date": _default_trade_start_date(),
            "observation_frequency": "MONTHLY",
            "contract_multiplier": 1.0,
        },
    },
    {
        "key": "phoenix",
        "label": "Phoenix",
        "product_type": "PhoenixOption",
        "engine_spec": {"engine_name": "PhoenixQuadEngine"},
        "unknown_fields": ["coupon_config.coupon_rate"],
        "product_kwargs": {
            "initial_price": 100.0,
            "strike": 100.0,
            "maturity_years": 1.0,
            "ko_barrier_pct": 103.0,
            "ki_barrier_pct": 75.0,
            "ko_rate": 0.0,
            "coupon_barrier_pct": 85.0,
            "coupon_rate": 0.01,
            "lockup_months": 3,
            "trade_start_date": _default_trade_start_date(),
            "observation_frequency": "MONTHLY",
            "contract_multiplier": 1.0,
        },
    },
    {
        "key": "single_sharkfin",
        "label": "Single Sharkfin",
        "product_type": "SingleSharkfinOption",
        "engine_spec": {"engine_name": "SingleSharkfinOptionAnalyticalEngine"},
        "unknown_fields": ["strike", "barrier", "participation_rate"],
        "product_kwargs": {
            "strike": 100.0,
            "barrier": 120.0,
            "option_type": "CALL",
            "maturity": 1.0,
            "participation_rate": 1.0,
            "contract_multiplier": 1.0,
        },
    },
    {
        "key": "double_sharkfin",
        "label": "Double Sharkfin",
        "product_type": "DoubleSharkfinOption",
        "engine_spec": {"engine_name": "DoubleSharkfinOptionAnalyticalEngine"},
        "unknown_fields": ["lower_barrier", "upper_barrier", "participation_rate"],
        "product_kwargs": {
            "strike": 100.0,
            "option_type": "CALL",
            "lower_barrier": 80.0,
            "upper_barrier": 120.0,
            "maturity": 1.0,
            "participation_rate": 1.0,
            "contract_multiplier": 1.0,
        },
    },
    {
        "key": "futures",
        "label": "Futures",
        "product_type": "Futures",
        "engine_spec": {"engine_name": "DeltaOneEngine"},
        "unknown_fields": ["basis"],
        # Futures takes `multiplier` (not contract_multiplier) and requires the
        # underlying name; `basis` is the solvable economic term (as in try-solve).
        "product_kwargs": {"maturity": 1.0, "multiplier": 1.0, "underlying": "CSI500"},
    },
    {
        "key": "spot",
        "label": "Spot",
        "product_type": "SpotInstrument",
        "engine_spec": {"engine_name": "DeltaOneEngine"},
        "unknown_fields": [],
        # SpotInstrument takes only deltaone_type + underlying.
        "product_kwargs": {"deltaone_type": "INDEX", "underlying": "CSI500"},
    },
]


# Seed solve-field metadata for the client intake UI: per field-path label and
# default solver bounds in the same value convention as the template kwargs
# (absolute 100-scale levels for strike/barrier-like fields, decimals for rates).
_UNKNOWN_FIELD_SPEC_DEFAULTS: dict[str, dict[str, Any]] = {
    "strike": {"label": "Strike", "lower_bound": 50.0, "upper_bound": 150.0, "initial_guess": 100.0},
    "volatility": {"label": "Volatility", "lower_bound": 0.01, "upper_bound": 2.0, "initial_guess": 0.2},
    "payout": {"label": "Payout", "lower_bound": 0.0, "upper_bound": 100.0, "initial_guess": 10.0},
    "barrier": {"label": "Barrier", "lower_bound": 50.0, "upper_bound": 200.0, "initial_guess": 120.0},
    "rebate": {"label": "Rebate", "lower_bound": 0.0, "upper_bound": 50.0, "initial_guess": 0.0},
    "lower_barrier": {"label": "Lower Barrier", "lower_bound": 50.0, "upper_bound": 150.0, "initial_guess": 80.0},
    "upper_barrier": {"label": "Upper Barrier", "lower_bound": 100.0, "upper_bound": 200.0, "initial_guess": 120.0},
    "participation_rate": {"label": "Participation Rate", "lower_bound": 0.0, "upper_bound": 5.0, "initial_guess": 1.0},
    "basis": {"label": "Basis", "lower_bound": -10.0, "upper_bound": 10.0, "initial_guess": 0.0},
    "range_config.accrual_rate": {"label": "Accrual Rate", "lower_bound": 0.0, "upper_bound": 2.0, "initial_guess": 0.15},
    "range_config.lower_barrier": {"label": "Lower Barrier", "lower_bound": 50.0, "upper_bound": 150.0, "initial_guess": 80.0},
    "range_config.upper_barrier": {"label": "Upper Barrier", "lower_bound": 100.0, "upper_bound": 200.0, "initial_guess": 120.0},
    "barrier_config.ko_rate": {"label": "KO Rate", "lower_bound": -1.0, "upper_bound": 2.0, "initial_guess": 0.15},
    "coupon_config.coupon_rate": {"label": "Coupon Rate", "lower_bound": -1.0, "upper_bound": 2.0, "initial_guess": 0.15},
}


def _unknown_field_spec(field_path: str) -> dict[str, Any]:
    defaults = _UNKNOWN_FIELD_SPEC_DEFAULTS.get(field_path)
    if defaults is None:
        tail = field_path.rsplit(".", 1)[-1]
        defaults = {
            "label": tail.replace("_", " ").title(),
            "lower_bound": 0.0,
            "upper_bound": 200.0,
            "initial_guess": 100.0,
        }
    return {"field_path": field_path, **defaults}


def get_rfq_catalog() -> dict[str, Any]:
    ensure_quantark_path()
    _ensure_sharkfin_registry_support()
    product_keys: list[str] = []
    engine_keys: list[str] = []
    try:
        from quantark.rfq.registry import ENGINE_BUILDERS, PRODUCT_BUILDERS

        product_keys = sorted(getattr(PRODUCT_BUILDERS, "_builders", {}).keys())
        engine_keys = sorted(getattr(ENGINE_BUILDERS, "_builders", {}).keys())
    except Exception:
        product_keys = []
        engine_keys = []

    curated_types = {template["product_type"] for template in COMMON_TEMPLATES}
    product_types = sorted(curated_types | set(product_keys))
    return {
        "product_types": [
            {
                "name": product_type,
                "template_key": _template_key_for_product(product_type),
                "quote_modes": ["solve", "price"],
            }
            for product_type in product_types
        ],
        "engine_options": engine_keys,
        "unknown_fields": _unknown_fields_by_product(),
        "templates": [
            {
                **template,
                "unknown_field_specs": [
                    _unknown_field_spec(path) for path in template["unknown_fields"]
                ],
            }
            for template in COMMON_TEMPLATES
        ],
        "advanced": {
            "accepts": [
                "product_type",
                "product_kwargs",
                "engine_spec",
                "market",
                "unknown",
                "target",
            ],
            "quote_modes": ["solve", "price"],
        },
    }


def draft_from_natural_language(
    message: str, client_name: str = "Demo Client"
) -> RFQDraftFromNLOut:
    text = message.lower()
    template = _template_for_text(text)
    product_kwargs = dict(template["product_kwargs"])
    engine_spec = RFQEngineSpecIn.model_validate(template["engine_spec"])
    unknown_field = _unknown_for_text(text, template)
    target_label = "premium" if "premium" in text else "price"
    target_value = _number_after(text, ("target", "price", "premium", "reoffer"))
    tenor = _tenor_years(text)
    underlying = _underlying_from_text(message)
    quantity = _quantity_from_text(text)
    side = "sell" if re.search(r"\b(sell|offer|bid wanted)\b", text) else "buy"
    missing: list[str] = []
    assumptions: list[str] = []

    if tenor is None:
        missing.append("tenor_or_maturity")
        tenor = 1.0
    else:
        product_kwargs["maturity"] = tenor
    if not underlying:
        missing.append("underlying")
        underlying = "TBD"
    if quantity is None:
        missing.append("quantity")
        quantity = 1.0
    if target_value is None:
        missing.append("target")
        target_value = 0.0

    if "maturity" in product_kwargs:
        product_kwargs["maturity"] = tenor
    lower, upper, guess = _bounds_for_unknown(unknown_field)
    draft = RFQRequestDraft(
        client_name=client_name,
        underlying=underlying,
        side=side,
        quantity=quantity,
        product_type=template["product_type"],
        product_kwargs=product_kwargs,
        engine_spec=engine_spec,
        unknown=RFQUnknownSpecIn(
            field_path=unknown_field,
            lower_bound=lower,
            upper_bound=upper,
            initial_guess=guess,
        ),
        target=RFQTargetIn(label=target_label, value=target_value),
        notes=message,
    )
    if missing:
        assumptions.append("Draft created without pricing because required economics are missing.")
    return RFQDraftFromNLOut(
        draft=draft,
        missing_fields=missing,
        assumptions=assumptions,
        extracted={
            "product_type": draft.product_type,
            "underlying": underlying,
            "quantity": quantity,
            "tenor_years": tenor,
            "unknown": unknown_field,
            "target_label": target_label,
            "target_value": target_value,
        },
    )


# Maps a snowball-family solve target (a path into the BUILT termsheet) to the
# FLAT contract key the placeholder initial-guess must be written to so
# build_product can synthesize a complete, priceable termsheet.
_SOLVE_TARGET_FLAT_KEY = {
    "barrier_config.ko_rate": "ko_rate",
    "coupon_config.coupon_rate": "coupon_rate",
    "barrier_config.ki_barrier": "ki_barrier_pct",
}


def _executable_product_kwargs(
    draft: RFQRequestDraft, *, quote_mode: str
) -> tuple[dict[str, Any], list[str]]:
    """For build_product families, synthesize a complete QuantArk termsheet from
    the draft's FLAT contract terms. In solve mode the designated solve target is
    filled with its initial guess (a placeholder) so synthesis produces a complete
    termsheet the QuantArk solver can start from (decision 6). Returns
    (product_kwargs, missing); missing is non-empty iff the contract is unfilled."""
    if draft.product_type not in _BUILD_PRODUCT_FAMILIES:
        return dict(draft.product_kwargs), []
    from .domains.product_builders import build_product

    contract = dict(draft.product_kwargs)
    solve_target = draft.unknown.field_path if quote_mode == "solve" else None
    if solve_target:
        flat_key = _SOLVE_TARGET_FLAT_KEY.get(solve_target)
        if flat_key and contract.get(flat_key) is None and draft.unknown.initial_guess is not None:
            contract[flat_key] = draft.unknown.initial_guess
    built = build_product(
        draft.product_type,
        contract,
        underlying=draft.underlying,
        currency=draft.market.currency,
        solve_target=solve_target,
    )
    if built.missing:
        return {}, built.missing
    return dict(built.product_kwargs), []


def validate_rfq_terms(
    terms: RFQRequestDraft | dict[str, Any], quote_mode: str = "solve"
) -> dict[str, Any]:
    try:
        draft = (
            terms if isinstance(terms, RFQRequestDraft) else RFQRequestDraft.model_validate(terms)
        )
        draft = _normalize_draft_currency(draft)
    except (PydanticValidationError, ValueError) as exc:
        return {"valid": False, "errors": [str(exc)], "missing_fields": []}

    errors: list[str] = []
    missing: list[str] = []
    if not draft.product_type:
        missing.append("product_type")
    if not draft.underlying or draft.underlying == "TBD":
        missing.append("underlying")
    if draft.quantity <= 0:
        errors.append("quantity must be positive")
    if not draft.product_kwargs:
        missing.append("product_kwargs")
    if not draft.engine_spec.engine_name:
        missing.append("engine_spec.engine_name")
    if draft.market.spot <= 0:
        errors.append("market.spot must be positive")
    if quote_mode == "solve":
        if not draft.unknown.field_path:
            missing.append("unknown.field_path")
        if draft.target.value <= 0:
            errors.append("target.value must be positive for solve mode")
    elif quote_mode != "price":
        errors.append("quote_mode must be solve or price")
    if not errors and not missing:
        if draft.product_type in _BUILD_PRODUCT_FAMILIES:
            # Build through the single producer: surface precise contract gaps,
            # never the opaque quad "KO observation … required".
            _kwargs, contract_missing = _executable_product_kwargs(
                draft, quote_mode=quote_mode
            )
            if contract_missing:
                missing.extend(contract_missing)
            else:
                build_validation = _quantark_build_validation(
                    draft.model_copy(update={"product_kwargs": _kwargs})
                )
                if not build_validation["valid"]:
                    errors.extend(build_validation["errors"])
        else:
            build_validation = _quantark_build_validation(draft)
            if not build_validation["valid"]:
                errors.extend(build_validation["errors"])
    return {"valid": not errors and not missing, "errors": errors, "missing_fields": missing}


def create_rfq_draft(
    session: Session,
    payload: RFQRequestDraft | RFQDraftCreate,
    *,
    channel: str | None = None,
    actor: str | None = None,
) -> RFQ:
    draft = _as_request_draft(payload)
    resolved_channel = channel or getattr(payload, "channel", "desk")
    rfq = RFQ(
        client_name=draft.client_name,
        channel=resolved_channel,
        status=RfqStatus.DRAFT.value,
        request_payload=model_to_dict(draft),
        quote_payload={},
    )
    session.add(rfq)
    session.flush()
    record_audit(
        session,
        event_type="rfq.draft_created",
        actor=actor or draft.client_name,
        subject_type="rfq",
        subject_id=rfq.id,
        payload={"channel": resolved_channel},
    )
    return rfq


def update_rfq_draft(
    session: Session,
    rfq_id: int,
    payload: RFQDraftUpdate,
    *,
    actor: str = "desk_user",
) -> RFQ:
    rfq = _get_rfq(session, rfq_id)
    if rfq.status not in {
        RfqStatus.DRAFT.value,
        RfqStatus.SUBMITTED.value,
        RfqStatus.PRICING_FAILED.value,
    }:
        raise ValueError(f"RFQ {rfq_id} cannot be edited from status {rfq.status}")
    current = RFQRequestDraft.model_validate(rfq.request_payload or {})
    merged = current.model_dump()
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            merged[key] = value
    draft = RFQRequestDraft.model_validate(merged)
    rfq.client_name = draft.client_name
    rfq.request_payload = model_to_dict(draft)
    rfq.status = RfqStatus.DRAFT.value
    rfq.updated_at = datetime.utcnow()
    record_audit(
        session,
        event_type="rfq.draft_updated",
        actor=actor,
        subject_type="rfq",
        subject_id=rfq.id,
        payload={"fields": sorted(payload.model_dump(exclude_unset=True).keys())},
    )
    return rfq


def submit_rfq_for_approval(
    session: Session, rfq_id: int, *, actor: str = "desk_user"
) -> RFQ:
    rfq = _get_rfq(session, rfq_id)
    latest = _ensure_quote_version_from_current(session, rfq)
    if latest and latest.status in {RfqStatus.PENDING_APPROVAL.value, RfqStatus.APPROVED.value}:
        rfq.status = RfqStatus.PENDING_APPROVAL.value
    elif rfq.status in {RfqStatus.DRAFT.value, RfqStatus.PRICING_FAILED.value}:
        rfq.status = RfqStatus.SUBMITTED.value
    else:
        raise ValueError(f"RFQ {rfq_id} cannot be submitted from status {rfq.status}")
    rfq.updated_at = datetime.utcnow()
    record_audit(
        session,
        event_type="rfq.submitted",
        actor=actor,
        subject_type="rfq",
        subject_id=rfq.id,
        payload={"status": rfq.status},
    )
    return rfq


def quote_rfq(
    session: Session,
    rfq_id: int,
    payload: RFQQuoteRequest | None = None,
) -> RFQ:
    request = payload or RFQQuoteRequest()
    rfq = _get_rfq(session, rfq_id)
    if rfq.status in {
        RfqStatus.APPROVED.value,
        RfqStatus.RELEASED.value,
        RfqStatus.CLIENT_ACCEPTED.value,
        RfqStatus.BOOKED.value,
        RfqStatus.EXPIRED.value,
        RfqStatus.CANCELLED.value,
    }:
        raise ValueError(f"RFQ {rfq_id} cannot be repriced from status {rfq.status}")
    draft = _draft_with_quote_overrides(rfq, request)
    quote_mode = request.quote_mode or draft.quote_mode
    draft = draft.model_copy(update={"quote_mode": quote_mode})
    validation = validate_rfq_terms(draft, quote_mode)
    if not validation["valid"]:
        version = _create_quote_version(
            session,
            rfq,
            quote_mode=quote_mode,
            status=RfqStatus.PRICING_FAILED.value,
            request_payload={
                "terms": model_to_dict(draft),
                "validation": validation,
                "quote_request": request.model_dump(mode="json"),
            },
            quote_payload={},
            error="; ".join(validation["missing_fields"] + validation["errors"]),
            created_by=request.created_by,
            valid_until=request.valid_until,
        )
        rfq.status = RfqStatus.PRICING_FAILED.value
        rfq.quote_payload = {"quote_version_id": version.id, "validation": validation}
        rfq.updated_at = datetime.utcnow()
        return rfq

    # For build_product families the draft carries the FLAT term contract.
    # Synthesize the complete QuantArk termsheet that pricing/solving operates on,
    # but KEEP the flat `draft` for _executable_terms_for_quote — it regenerates
    # the booked termsheet from the flat contract + solved value so every schedule
    # record's rate reflects the solved value (decision 7). Reassigning `draft`
    # here would discard the flat contract and leave placeholder per-record rates.
    priced_draft = draft
    if draft.product_type in _BUILD_PRODUCT_FAMILIES:
        exec_kwargs, exec_missing = _executable_product_kwargs(draft, quote_mode=quote_mode)
        if exec_missing:  # defense-in-depth; validate_rfq_terms already gated this
            raise ValueError(
                f"Incomplete {draft.product_type} contract; missing: "
                + ", ".join(exec_missing)
            )
        priced_draft = draft.model_copy(update={"product_kwargs": exec_kwargs})

    if quote_mode == "price":
        result = price_product(
            priced_draft.product_type,
            priced_draft.product_kwargs,
            priced_draft.market,
            priced_draft.engine_spec.engine_name,
            _engine_kwargs_for_position(priced_draft.engine_spec),
        )
        quote_payload = _fixed_price_quote_payload(priced_draft, result)
    else:
        result = quantark_solve_rfq(priced_draft)
        quote_payload = _attach_quote_amount(priced_draft, dict(result.data))

    executable_terms = _executable_terms_for_quote(draft, quote_mode, quote_payload)
    executable_draft = RFQRequestDraft.model_validate(executable_terms)
    executable_validation = (
        _quantark_build_validation(executable_draft) if result.ok else {"valid": False, "errors": [result.error or "QuantArk pricing failed"], "metadata": {}}
    )
    if result.ok and not executable_validation["valid"]:
        result.ok = False
        result.error = "; ".join(executable_validation["errors"])
        quote_payload["status"] = RfqStatus.PRICING_FAILED.value

    status = RfqStatus.PENDING_APPROVAL.value if result.ok else RfqStatus.PRICING_FAILED.value
    version = _create_quote_version(
        session,
        rfq,
        quote_mode=quote_mode,
        status=status,
        request_payload={
            "terms": model_to_dict(draft),
            "executable_terms": executable_terms,
            "quote_request": request.model_dump(mode="json"),
            "market_snapshot": model_to_dict(draft.market),
            "engine_spec": model_to_dict(draft.engine_spec),
            "quantark_build": executable_validation,
        },
        quote_payload=quote_payload | {"quantark_ok": result.ok, "quantark_error": result.error},
        error=result.error,
        created_by=request.created_by,
        valid_until=request.valid_until,
    )
    rfq.status = status
    rfq.client_name = draft.client_name
    rfq.request_payload = model_to_dict(draft)
    rfq.quote_payload = quote_payload | {
        "quantark_ok": result.ok,
        "quantark_error": result.error,
        "quote_version_id": version.id,
        "quote_version": version.version,
    }
    rfq.updated_at = datetime.utcnow()
    record_audit(
        session,
        event_type="rfq.quoted" if result.ok else "rfq.pricing_failed",
        actor=request.created_by,
        subject_type="rfq",
        subject_id=rfq.id,
        payload={"quote_version_id": version.id, "quote_mode": quote_mode, "error": result.error},
    )
    return rfq


def create_and_quote_rfq(
    session: Session,
    payload: RFQRequestDraft,
    *,
    channel: str,
    actor: str | None = None,
) -> RFQ:
    rfq = create_rfq_draft(session, payload, channel=channel, actor=actor)
    return quote_rfq(
        session,
        rfq.id,
        RFQQuoteRequest(
            quote_mode=payload.quote_mode,
            created_by=actor or payload.client_name,
        ),
    )


def approve_rfq(
    session: Session,
    rfq_id: int,
    payload: RFQApprovalDecision,
) -> RFQ:
    rfq = _get_rfq(session, rfq_id)
    if rfq.status != RfqStatus.PENDING_APPROVAL.value:
        raise ValueError(f"Only pending approval RFQs can be approved; current status is {rfq.status}")
    latest = _ensure_quote_version_from_current(session, rfq)
    if latest is None or latest.status == RfqStatus.PRICING_FAILED.value:
        raise ValueError("RFQ has no successful quote version to approve")
    response = payload.response_override or approved_client_response(rfq, latest)
    rfq.status = RfqStatus.APPROVED.value
    rfq.approved_response = response
    rfq.updated_at = datetime.utcnow()
    latest.status = RfqStatus.APPROVED.value
    latest.approved_by = payload.approver
    latest.approved_at = datetime.utcnow()
    session.add(
        Approval(
            rfq_id=rfq.id,
            decision="approved",
            approver=payload.approver,
            comment=payload.comment,
            response_override=payload.response_override,
        )
    )
    record_audit(
        session,
        event_type="rfq.approved",
        actor=payload.approver,
        subject_type="rfq",
        subject_id=rfq.id,
        payload={
            "comment": payload.comment,
            "quote_version_id": latest.id,
            "source": payload.approver,
        },
    )
    return rfq


def reject_rfq(
    session: Session,
    rfq_id: int,
    payload: RFQApprovalDecision,
) -> RFQ:
    rfq = _get_rfq(session, rfq_id)
    if rfq.status in {RfqStatus.BOOKED.value, RfqStatus.CANCELLED.value}:
        raise ValueError(f"RFQ {rfq_id} cannot be rejected from status {rfq.status}")
    rfq.status = RfqStatus.REJECTED.value
    rfq.approved_response = payload.response_override
    rfq.updated_at = datetime.utcnow()
    session.add(
        Approval(
            rfq_id=rfq.id,
            decision="rejected",
            approver=payload.approver,
            comment=payload.comment,
            response_override=payload.response_override,
        )
    )
    record_audit(
        session,
        event_type="rfq.rejected",
        actor=payload.approver,
        subject_type="rfq",
        subject_id=rfq.id,
        payload={"comment": payload.comment, "source": payload.approver},
    )
    return rfq


def release_rfq(session: Session, rfq_id: int, payload: RFQReleaseRequest) -> RFQ:
    rfq = _get_rfq(session, rfq_id)
    if rfq.status != RfqStatus.APPROVED.value:
        raise ValueError(f"Only approved RFQs can be released; current status is {rfq.status}")
    latest = _ensure_quote_version_from_current(session, rfq)
    if latest is None:
        raise ValueError("RFQ has no quote version to release")
    if payload.response_override:
        rfq.approved_response = payload.response_override
    rfq.status = RfqStatus.RELEASED.value
    rfq.updated_at = datetime.utcnow()
    latest.status = RfqStatus.RELEASED.value
    latest.released_at = datetime.utcnow()
    record_audit(
        session,
        event_type="rfq.released",
        actor=payload.actor,
        subject_type="rfq",
        subject_id=rfq.id,
        payload={"quote_version_id": latest.id},
    )
    return rfq


def mark_client_accepted(
    session: Session,
    rfq_id: int,
    payload: RFQClientAcceptRequest,
) -> RFQ:
    rfq = _get_rfq(session, rfq_id)
    if rfq.status != RfqStatus.RELEASED.value:
        raise ValueError(f"Only released RFQs can be accepted; current status is {rfq.status}")
    rfq.status = RfqStatus.CLIENT_ACCEPTED.value
    rfq.updated_at = datetime.utcnow()
    record_audit(
        session,
        event_type="rfq.client_accepted",
        actor=payload.actor,
        subject_type="rfq",
        subject_id=rfq.id,
        payload={"comment": payload.comment},
    )
    return rfq


def book_rfq_to_position(
    session: Session,
    rfq_id: int,
    payload: RFQBookRequest,
) -> Position:
    rfq = _get_rfq(session, rfq_id)
    if rfq.status != RfqStatus.CLIENT_ACCEPTED.value:
        raise ValueError(f"Only client accepted RFQs can be booked; current status is {rfq.status}")
    latest = latest_quote_version(session, rfq_id)
    if latest is None:
        raise ValueError("RFQ has no quote version to book")
    terms = _booking_terms(rfq, latest)
    quote = latest.quote_payload or rfq.quote_payload or {}
    product_spec = product_spec_from_executable_terms(terms)
    position = book_position(
        session,
        BookingRequest(
            portfolio_id=payload.portfolio_id,
            product=ProductBookingSpec(**product_spec.__dict__),
            quantity=(
                payload.quantity
                if payload.quantity is not None
                else _signed_booking_quantity(terms)
            ),
            entry_price=payload.entry_price if payload.entry_price is not None else _quote_price(quote),
            status="open",
            source_trade_id=f"RFQ-{rfq.id}-V{latest.version}",
            mapping_status="supported",
            mapping_error=None,
            source_payload={
                "source": "rfq",
                "rfq_id": rfq.id,
                "quote_version_id": latest.id,
                "request_payload": rfq.request_payload,
                "executable_terms": model_to_dict(terms),
                "quote_payload": quote,
            },
            rfq_id=rfq.id,
            rfq_quote_version_id=latest.id,
            trade_effective_date=_to_datetime(payload.trade_effective_date),
            engine_name=terms.engine_spec.engine_name,
            engine_kwargs=_engine_kwargs_for_position(terms.engine_spec),
            actor=payload.actor,
            source="rfq",
        ),
    )
    rfq.status = RfqStatus.BOOKED.value
    rfq.updated_at = datetime.utcnow()
    record_audit(
        session,
        event_type="rfq.booked",
        actor=payload.actor,
        subject_type="rfq",
        subject_id=rfq.id,
        payload={"quote_version_id": latest.id, "position_id": position.id, "portfolio_id": payload.portfolio_id},
    )
    return position


def latest_quote_version(session: Session, rfq_id: int) -> RFQQuoteVersion | None:
    return (
        session.query(RFQQuoteVersion)
        .filter(RFQQuoteVersion.rfq_id == rfq_id)
        .order_by(RFQQuoteVersion.version.desc(), RFQQuoteVersion.id.desc())
        .first()
    )


def repair_legacy_rfq_booked_positions(session: Session) -> int:
    """Repair RFQ-booked positions created before executable terms were stored."""
    positions = (
        session.query(Position)
        .filter(
            Position.rfq_id.isnot(None),
            Position.rfq_quote_version_id.isnot(None),
        )
        .all()
    )
    repaired = 0
    for position in positions:
        rfq = position.rfq
        quote_version = position.rfq_quote_version
        if rfq is None or quote_version is None:
            continue
        try:
            terms = _booking_terms(rfq, quote_version)
            validation = _quantark_build_validation(terms)
        except Exception:
            continue
        if not validation["valid"]:
            continue
        merged_kwargs = _merge_product_kwargs(position.product_kwargs or {}, terms.product_kwargs)
        next_engine_kwargs = _engine_kwargs_for_position(terms.engine_spec)
        quote_request_payload = dict(quote_version.request_payload or {})
        if not quote_request_payload.get("executable_terms"):
            quote_request_payload["executable_terms"] = model_to_dict(terms)
        if not quote_request_payload.get("quantark_build"):
            quote_request_payload["quantark_build"] = validation
        next_payload = dict(position.source_payload or {})
        next_payload.update(
            {
                "source": "rfq",
                "rfq_id": rfq.id,
                "quote_version_id": quote_version.id,
                "request_payload": rfq.request_payload,
                "executable_terms": model_to_dict(terms),
                "quote_payload": quote_version.quote_payload or rfq.quote_payload or {},
            }
        )
        changed = any(
            (
                position.mapping_status != "supported",
                position.mapping_error is not None,
                position.underlying != terms.underlying,
                position.product_type != terms.product_type,
                position.product_kwargs != merged_kwargs,
                position.engine_name != terms.engine_spec.engine_name,
                position.engine_kwargs != next_engine_kwargs,
                position.source_payload != next_payload,
                quote_version.request_payload != quote_request_payload,
            )
        )
        if not changed:
            continue
        position.underlying = terms.underlying
        position.product_type = terms.product_type
        position.product_kwargs = merged_kwargs
        position.engine_name = terms.engine_spec.engine_name
        position.engine_kwargs = next_engine_kwargs
        position.mapping_status = "supported"
        position.mapping_error = None
        position.source_payload = next_payload
        from .domains.position_terms import refresh_position_barrier_state, upsert_position_term_rows

        upsert_position_term_rows(session, position)
        refresh_position_barrier_state(session, position_id=position.id)
        quote_version.request_payload = quote_request_payload
        position.updated_at = datetime.utcnow()
        repaired += 1
    if repaired:
        session.flush()
    return repaired


def _ensure_quote_version_from_current(
    session: Session, rfq: RFQ
) -> RFQQuoteVersion | None:
    latest = latest_quote_version(session, rfq.id)
    if latest is not None:
        return latest
    if not rfq.quote_payload:
        return None
    status = (
        RfqStatus.PRICING_FAILED.value
        if rfq.quote_payload.get("quantark_ok") is False
        or rfq.quote_payload.get("status") == RfqStatus.PRICING_FAILED.value
        else RfqStatus.PENDING_APPROVAL.value
    )
    terms_payload = rfq.request_payload or {}
    try:
        draft = RFQRequestDraft.model_validate(terms_payload)
        executable_terms = _executable_terms_for_quote(
            draft, "solve", rfq.quote_payload or {}
        )
        build_validation = _quantark_build_validation(
            RFQRequestDraft.model_validate(executable_terms)
        )
    except Exception as exc:
        executable_terms = terms_payload
        build_validation = {"valid": False, "errors": [str(exc)], "metadata": {}}
    if not build_validation["valid"] and status != RfqStatus.PRICING_FAILED.value:
        status = RfqStatus.PRICING_FAILED.value
    return _create_quote_version(
        session,
        rfq,
        quote_mode="solve",
        status=status,
        request_payload={
            "terms": terms_payload,
            "executable_terms": executable_terms,
            "quantark_build": build_validation,
            "legacy": True,
        },
        quote_payload=rfq.quote_payload or {},
        error=rfq.quote_payload.get("quantark_error"),
        created_by="legacy",
        valid_until=None,
    )


def approved_client_response(rfq: RFQ, quote_version: RFQQuoteVersion | None = None) -> str:
    quote = (quote_version.quote_payload if quote_version else None) or rfq.quote_payload or {}
    request = rfq.request_payload or {}
    quantity = float(request.get("quantity", 1) or 1)
    product_type = request.get("product_type", "product")
    underlying = request.get("underlying", "underlying")
    side = "bid" if request.get("side") == "sell" else "offer"
    label = quote.get("field_label") or quote.get("field_path")
    solved = quote.get("solved_value")
    if solved is None:
        terms = f"fixed terms, {_quote_price_terms(quote)}"
    else:
        terms = f"{label or 'solved field'} = {float(solved):.6g}, {_quote_price_terms(quote)}"
    return (
        f"Approved executable {side} for {quantity:g} x {product_type} on {underlying}: "
        f"{terms}. Released by the desk approval workflow."
    )


def _create_quote_version(
    session: Session,
    rfq: RFQ,
    *,
    quote_mode: str,
    status: str,
    request_payload: dict[str, Any],
    quote_payload: dict[str, Any],
    error: str | None,
    created_by: str,
    valid_until: datetime | None,
) -> RFQQuoteVersion:
    latest_version = (
        session.query(RFQQuoteVersion.version)
        .filter(RFQQuoteVersion.rfq_id == rfq.id)
        .order_by(RFQQuoteVersion.version.desc())
        .limit(1)
        .scalar()
    )
    version = RFQQuoteVersion(
        rfq_id=rfq.id,
        version=int(latest_version or 0) + 1,
        quote_mode=quote_mode,
        status=status,
        request_payload=request_payload,
        quote_payload=quote_payload,
        error=error,
        created_by=created_by,
        valid_until=valid_until,
    )
    session.add(version)
    session.flush()
    return version


def _as_request_draft(payload: RFQRequestDraft | RFQDraftCreate) -> RFQRequestDraft:
    if type(payload) is RFQRequestDraft:
        return _normalize_draft_currency(payload)
    data = payload.model_dump(exclude={"channel"})
    return _normalize_draft_currency(RFQRequestDraft.model_validate(data))


def _normalize_draft_currency(draft: RFQRequestDraft) -> RFQRequestDraft:
    product = draft.product or {}
    explicit = (
        draft.market.currency
        or product.get("currency")
        or draft.product_kwargs.get("currency")
    )
    symbol = (
        draft.underlying
        or product.get("underlying")
        or draft.product_kwargs.get("underlying")
        or "CSI500"
    )
    currency = normalize_currency(
        str(explicit or resolve_underlying_currency(symbol))
    )
    if currency not in ISO_4217_CODES:
        raise ValueError(f"Invalid RFQ currency: {explicit!r}")
    next_product = dict(product) if product else None
    if next_product is not None:
        next_product["currency"] = currency
    next_kwargs = dict(draft.product_kwargs or {})
    next_kwargs.pop("currency", None)
    return draft.model_copy(
        update={
            "market": draft.market.model_copy(update={"currency": currency}),
            "product": next_product,
            "product_kwargs": next_kwargs,
        }
    )


def _deep_merge_dict(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overrides.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(current, value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _explicit_model_dump(model: Any) -> dict[str, Any]:
    return model.model_dump(exclude_unset=True)


def _draft_with_quote_overrides(rfq: RFQ, request: RFQQuoteRequest) -> RFQRequestDraft:
    data = dict(rfq.request_payload or {})
    if request.product_kwargs is not None:
        data["product_kwargs"] = _deep_merge_dict(
            dict(data.get("product_kwargs") or {}),
            request.product_kwargs,
        )
    if request.market is not None:
        data["market"] = _deep_merge_dict(
            dict(data.get("market") or {}),
            _explicit_model_dump(request.market),
        )
    if request.engine_spec is not None:
        data["engine_spec"] = _deep_merge_dict(
            dict(data.get("engine_spec") or {}),
            _explicit_model_dump(request.engine_spec),
        )
    if request.unknown is not None:
        data["unknown"] = _deep_merge_dict(
            dict(data.get("unknown") or {}),
            _explicit_model_dump(request.unknown),
        )
    if request.target is not None:
        data["target"] = _deep_merge_dict(
            dict(data.get("target") or {}),
            _explicit_model_dump(request.target),
        )
    return _normalize_draft_currency(RFQRequestDraft.model_validate(data))


def _engine_kwargs_for_position(engine_spec: RFQEngineSpecIn) -> dict[str, Any]:
    kwargs = dict(engine_spec.engine_kwargs or {})
    if engine_spec.params_type is not None:
        kwargs["params_type"] = engine_spec.params_type
    if engine_spec.params_kwargs:
        kwargs["params_kwargs"] = engine_spec.params_kwargs
    if engine_spec.method is not None:
        kwargs["method"] = engine_spec.method
    return kwargs


def _quantark_build_validation(draft: RFQRequestDraft) -> dict[str, Any]:
    result = validate_quantark_build(
        draft.product_type,
        draft.product_kwargs,
        draft.market,
        draft.engine_spec.engine_name,
        _engine_kwargs_for_position(draft.engine_spec),
    )
    return {
        "valid": result.ok,
        "errors": [] if result.ok else [result.error or "QuantArk product build failed"],
        "metadata": result.data,
    }


def _executable_terms_for_quote(
    draft: RFQRequestDraft,
    quote_mode: str,
    quote_payload: dict[str, Any],
) -> dict[str, Any]:
    terms = deepcopy(model_to_dict(draft))
    terms["quote_mode"] = quote_mode

    if draft.product_type in _BUILD_PRODUCT_FAMILIES:
        # build_product families carry the FLAT contract; regenerate the complete
        # QuantArk termsheet so booking/validation run on a buildable product. In
        # solve mode, bind the solved value into the flat contract FIRST so every
        # derived schedule record's rate reflects the solved value — not the
        # initial-guess placeholder (decision 7). A top-level patch of
        # barrier_config.ko_rate alone would leave per-record rates stale, making
        # the booked product's visible coupon and its priced cashflows disagree.
        flat_contract = dict(draft.product_kwargs)
        if quote_mode == "solve":
            solved = quote_payload.get("solved_value")
            field_path = str(quote_payload.get("field_path") or draft.unknown.field_path or "")
            flat_key = _SOLVE_TARGET_FLAT_KEY.get(field_path)
            if solved is not None and flat_key is not None:
                flat_contract[flat_key] = solved
        regenerated, missing = _executable_product_kwargs(
            draft.model_copy(update={"product_kwargs": flat_contract, "quote_mode": "price"}),
            quote_mode="price",
        )
        if not missing:
            terms["product_kwargs"] = regenerated
        return terms

    if quote_mode != "solve":
        return terms
    solved = quote_payload.get("solved_value")
    if solved is None:
        return terms
    field_path = str(quote_payload.get("field_path") or draft.unknown.field_path)
    if not field_path or field_path == "fixed_terms":
        return terms
    _set_quantark_unknown_path(terms, field_path, solved)
    return terms


def _set_quantark_unknown_path(
    terms: dict[str, Any],
    field_path: str,
    value: Any,
) -> None:
    path = field_path.strip()
    for prefix in ("terms.", "termsheet_input."):
        if path.startswith(prefix):
            path = path[len(prefix):]
    parts = [part for part in path.split(".") if part]
    if not parts:
        return

    market_keys = {
        "spot",
        "volatility",
        "rate",
        "dividend_yield",
        "asset_name",
        "currency",
        "day_count_convention",
        "bus_days_in_year",
    }
    if parts[0] in {"market", "market_kwargs"}:
        target = terms.setdefault("market", {})
        nested_path = parts[1:]
    elif parts[0] == "product_kwargs":
        target = terms.setdefault("product_kwargs", {})
        nested_path = parts[1:]
    elif len(parts) == 1 and parts[0] in market_keys:
        target = terms.setdefault("market", {})
        nested_path = parts
    elif len(parts) == 1 and parts[0] in terms and parts[0] != "product_kwargs":
        terms[parts[0]] = value
        return
    else:
        target = terms.setdefault("product_kwargs", {})
        nested_path = parts

    if not nested_path:
        return
    _set_nested_value(target, nested_path, value)


def executable_terms_for_quote(
    draft: RFQRequestDraft,
    quote_mode: str,
    quote_payload: dict[str, Any],
) -> dict[str, Any]:
    return _executable_terms_for_quote(draft, quote_mode, quote_payload)


def _set_nested_value(target: dict[str, Any], path: list[str], value: Any) -> None:
    current = target
    for part in path[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[path[-1]] = value


def _booking_terms(rfq: RFQ, latest: RFQQuoteVersion) -> RFQRequestDraft:
    request_payload = latest.request_payload or {}
    executable = request_payload.get("executable_terms")
    if executable:
        return _normalize_draft_currency(RFQRequestDraft.model_validate(executable))
    base_terms = request_payload.get("terms") or rfq.request_payload or {}
    draft = _normalize_draft_currency(RFQRequestDraft.model_validate(base_terms))
    quote_mode = latest.quote_mode or draft.quote_mode
    quote_payload = latest.quote_payload or rfq.quote_payload or {}
    return _normalize_draft_currency(RFQRequestDraft.model_validate(
        _executable_terms_for_quote(draft, quote_mode, quote_payload)
    ))


def _signed_booking_quantity(terms: RFQRequestDraft) -> float:
    quantity = abs(float(terms.quantity or 0.0))
    return -quantity if terms.side == "sell" else quantity


def _merge_product_kwargs(
    existing: dict[str, Any],
    executable: dict[str, Any],
) -> dict[str, Any]:
    merged = deepcopy(existing)
    for key, value in executable.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_product_kwargs(merged[key], value)
        else:
            merged[key] = value
    return merged


def _get_rfq(session: Session, rfq_id: int) -> RFQ:
    rfq = session.get(RFQ, rfq_id)
    if not rfq:
        raise ValueError(f"RFQ {rfq_id} not found")
    return rfq


def _fixed_price_quote_payload(draft: RFQRequestDraft, result: Any) -> dict[str, Any]:
    price = _quote_price(result.data)
    status = "priced" if result.ok else "pricing_failed"
    payload = {
        "quote_id": f"price-{int(datetime.utcnow().timestamp())}",
        "status": status,
        "field_path": "fixed_terms",
        "field_label": "fixed terms",
        "solved_value": None,
        "target_label": "price",
        "target_value": price,
        "achieved_price": price,
        "residual": 0.0,
        "engine_summary": {"engine_class": draft.engine_spec.engine_name},
        "request_summary": {
            "input_mode": "termsheet",
            "product_type": draft.product_type,
            "engine_class": draft.engine_spec.engine_name,
            "quote_mode": "price",
        },
    }
    payload = _attach_quote_amount(draft, payload)
    if result.ok:
        side = "bid" if draft.side == "sell" else "offer"
        payload["client_response"] = (
            f"Indicative {side} for {draft.quantity:g} x {draft.product_type} on {draft.underlying}: "
            f"fixed terms, {_quote_price_terms(payload)}. "
            "This quote is pending internal trader approval and is not executable until approved."
        )
    return payload


def _quote_float(quote: dict[str, Any], key: str) -> float | None:
    value = quote.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quote_unit_price(quote: dict[str, Any]) -> float | None:
    for key in ("unit_price", "achieved_price", "price", "target_value"):
        value = _quote_float(quote, key)
        if value is not None:
            return value
    return None


def _quote_amount_scale(draft: RFQRequestDraft) -> tuple[float, str]:
    initial_price = draft.product_kwargs.get("initial_price")
    try:
        scale = float(initial_price)
    except (TypeError, ValueError):
        scale = 0.0
    if scale > 0:
        return scale, "notional_over_initial_price"
    return 1.0, "quantity"


def _attach_quote_amount(
    draft: RFQRequestDraft,
    quote_payload: dict[str, Any],
) -> dict[str, Any]:
    unit_price = _quote_unit_price(quote_payload)
    if unit_price is None:
        return quote_payload

    scale, basis = _quote_amount_scale(draft)
    quote_notional = float(draft.quantity or 0.0)
    quote_amount = unit_price * quote_notional / scale
    enriched = dict(quote_payload)
    enriched["unit_price"] = unit_price
    enriched["quote_amount"] = quote_amount
    enriched["quote_notional"] = quote_notional
    enriched["quote_price_scale"] = scale
    enriched["quote_amount_basis"] = basis
    enriched["quote_amount_currency"] = draft.market.currency
    return enriched


def _quote_price_terms(quote: dict[str, Any]) -> str:
    price = _quote_unit_price(quote) or 0.0
    amount = _quote_float(quote, "quote_amount")
    if amount is None:
        return f"model price = {price:.6g}"
    currency = str(quote.get("quote_amount_currency") or "").strip()
    currency_prefix = f"{currency} " if currency else ""
    return f"unit price = {price:.6g}, quote amount = {currency_prefix}{amount:,.6g}"


def _quote_price(quote: dict[str, Any]) -> float:
    return _quote_unit_price(quote) or 0.0


def _template_key_for_product(product_type: str) -> str | None:
    lowered = product_type.lower()
    for template in COMMON_TEMPLATES:
        if template["product_type"].lower() == lowered:
            return str(template["key"])
    return None


def _unknown_fields_by_product() -> dict[str, list[str]]:
    return {
        template["product_type"]: list(template.get("unknown_fields", []))
        for template in COMMON_TEMPLATES
    }


def _template_for_text(text: str) -> dict[str, Any]:
    candidates = [
        ("double shark", "double_sharkfin"),
        ("double-shark", "double_sharkfin"),
        ("single shark", "single_sharkfin"),
        ("sharkfin", "single_sharkfin"),
        ("ko reset", "ko_reset_snowball"),
        ("phoenix", "phoenix"),
        ("snowball", "snowball"),
        ("range accrual", "range_accrual"),
        ("asian", "asian"),
        ("one touch", "one_touch"),
        ("digital", "digital"),
        ("barrier", "barrier"),
        ("american", "american"),
        ("future", "futures"),
        ("spot", "spot"),
    ]
    key = "vanilla"
    for needle, candidate in candidates:
        if needle in text:
            key = candidate
            break
    return next(template for template in COMMON_TEMPLATES if template["key"] == key)


def _unknown_for_text(text: str, template: dict[str, Any]) -> str:
    if "coupon" in text:
        # "Coupon" is also the colloquial name for a range accrual's accrual rate.
        for field in template.get("unknown_fields", []):
            if "coupon" in field or "accrual" in field:
                return field
        return "coupon_rate"
    if "ko rate" in text or "knock out rate" in text:
        for field in template.get("unknown_fields", []):
            if "ko_rate" in field:
                return field
        return "barrier_config.ko_rate"
    if "barrier" in text:
        for field in template.get("unknown_fields", []):
            if "barrier" in field:
                return field
    if "vol" in text:
        return "volatility"
    return str((template.get("unknown_fields") or ["strike"])[0])


def _number_after(text: str, labels: tuple[str, ...]) -> float | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:{label_pattern})\D*(-?\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _tenor_years(text: str) -> float | None:
    if "one year" in text or "1y" in text:
        return 1.0
    if "six month" in text or "6m" in text:
        return 0.5
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:year|yr|y)\b", text)
    if match:
        return float(match.group(1))
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:month|m)\b", text)
    if match:
        return float(match.group(1)) / 12.0
    return None


def _underlying_from_text(message: str) -> str | None:
    known = ["CSI500", "CSI1000", "CSI300", "SSE50", "HSI", "SPX", "NDX"]
    lowered = message.lower()
    for item in known:
        if item.lower() in lowered:
            return item
    match = re.search(r"\b([A-Z]{2,6}\d{0,4}|\d{6}\.(?:SH|SZ|CSI))\b", message)
    return match.group(1) if match else None


def _quantity_from_text(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:x|units|contracts|notional)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _bounds_for_unknown(field: str) -> tuple[float, float, float]:
    if "coupon" in field:
        return 0.0, 0.3, 0.08
    if "ko_rate" in field:
        return 0.0, 0.4, 0.15
    if "barrier" in field:
        return 50.0, 150.0, 100.0
    if field == "volatility":
        return 0.01, 1.0, 0.2
    return 50.0, 150.0, 100.0


def _to_datetime(value: date | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min)

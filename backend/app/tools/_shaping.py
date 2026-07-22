"""JSON shaping helpers shared across tool modules."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.models import (
    AssumptionRow,
    AssumptionSet,
    Instrument,
    MarketDataProfile,
    Portfolio,
    PortfolioCycleError,
    PortfolioDepthError,
    Position,
    PositionValuationResult,
    PositionValuationRun,
    PricingParameterProfile,
    PricingParameterRow,
    ReportJob,
    RuleValidationError,
)
from app.services.domains._errors import DomainWriteError
from app.services.domains.products import product_summary
from app.tools._product_inputs import rfq_draft_to_product_payload


def shape_portfolio(p: Portfolio) -> dict[str, Any]:
    """Render a Portfolio ORM row as a JSON-friendly dict.

    The shape is the legacy `_portfolio_summary` from langchain_tools.py
    plus optional ISO timestamps for ``created_at`` / ``updated_at``.
    Existing tests rely on the legacy keys remaining stable.
    """
    return {
        "id": p.id,
        "name": p.name,
        "kind": p.kind,
        "base_currency": p.base_currency,
        "description": p.description,
        "tags": list(p.tags or []),
        "filter_rule": p.filter_rule,
        "manual_include_ids": list(p.manual_include_ids or []),
        "manual_exclude_ids": list(p.manual_exclude_ids or []),
        "source_portfolio_ids": list(p.source_portfolio_ids or []),
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def shape_position(p: Position, *, include_raw_terms: bool = True) -> dict[str, Any]:
    """Render a Position ORM row as a JSON-friendly dict.

    Mirrors the legacy ``get_positions_tool`` database-branch row shape so
    the existing agent test suite keeps observing the same fields.
    """
    effective = p.trade_effective_date
    if isinstance(effective, datetime):
        effective_iso: str | None = effective.date().isoformat()
    elif effective is not None:
        effective_iso = effective.isoformat()
    else:
        effective_iso = None
    loaded_product = getattr(p, "__dict__", {}).get("product")
    row = {
        "id": p.id,
        "portfolio_id": p.portfolio_id,
        "product_id": p.product_id,
        "product": product_summary(loaded_product),
        "source_trade_id": p.source_trade_id,
        "underlying": p.underlying,
        "product_type": p.product_type,
        "engine_name": p.engine_name,
        "quantity": p.quantity,
        "entry_price": p.entry_price,
        "status": p.status,
        "trade_effective_date": effective_iso,
    }
    if include_raw_terms:
        row["product_kwargs"] = p.product_kwargs
    row["engine_kwargs"] = p.engine_kwargs
    return row


def shape_position_list(
    rows: list[Position],
    *,
    source: str,
    market: Any,
    filters: dict[str, Any],
    include_raw_terms: bool = True,
    portfolio_total_count: int | None = None,
    portfolio_counts_by_product_type: dict[str, int] | None = None,
    missing_effective_date_count: int = 0,
) -> dict[str, Any]:
    """Shape a list of Position rows into the legacy get_positions_tool envelope."""
    counts: dict[str, int] = {}
    normalized: list[dict[str, Any]] = []
    for position in rows:
        counts[position.product_type] = counts.get(position.product_type, 0) + 1
        normalized.append(shape_position(position, include_raw_terms=include_raw_terms))
    payload: dict[str, Any] = {
        "source": source,
        "filters": filters,
        "total_count": len(normalized),
        "counts_by_product_type": counts,
        "missing_trade_effective_date_count": missing_effective_date_count,
        "positions": normalized,
        "market": market.model_dump(mode="json") if market is not None else None,
    }
    if portfolio_total_count is not None:
        payload["portfolio_total_count"] = portfolio_total_count
    if portfolio_counts_by_product_type is not None:
        payload["portfolio_counts_by_product_type"] = portfolio_counts_by_product_type
    return payload


def shape_positions_view(
    view: Any,
    *,
    source: str,
    market: Any,
    include_raw_terms: bool = True,
) -> dict[str, Any]:
    """Render a ``PositionsView`` aggregate into the legacy wire shape.

    ``source`` is either ``"database"`` (positions are ORM Position rows
    and portfolio counts are present) or ``"provided_context"`` (positions
    are pre-serialized dict rows and there is no enclosing portfolio).
    """
    if source == "provided_context":
        return shape_supplied_position_list(
            view.positions,
            market=market,
            filters=view.filters,
            missing_effective_date_count=view.missing_effective_date_count,
            include_raw_terms=include_raw_terms,
        )
    return shape_position_list(
        view.positions,
        source=source,
        market=market,
        filters=view.filters,
        include_raw_terms=include_raw_terms,
        portfolio_total_count=view.portfolio_total_count,
        portfolio_counts_by_product_type=view.portfolio_counts_by_product_type,
        missing_effective_date_count=view.missing_effective_date_count,
    )


def shape_supplied_position_list(
    rows: list[dict[str, Any]],
    *,
    market: Any,
    filters: dict[str, Any],
    missing_effective_date_count: int,
    include_raw_terms: bool = True,
) -> dict[str, Any]:
    """Shape pre-filtered supplied (snapshot) rows into the provided_context envelope."""
    counts: dict[str, int] = {}
    normalized: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get("product_type") or "unknown")
        counts[key] = counts.get(key, 0) + 1
        if include_raw_terms:
            normalized.append(dict(row))
        else:
            normalized.append(
                {
                    key: value
                    for key, value in row.items()
                    if key != "product_kwargs"
                }
            )
    return {
        "source": "provided_context",
        "filters": filters,
        "total_count": len(rows),
        "counts_by_product_type": counts,
        "missing_trade_effective_date_count": missing_effective_date_count,
        "positions": normalized,
        "market": market.model_dump(mode="json") if market is not None else None,
    }


def shape_valuation_results(
    portfolio_id: int,
    run: PositionValuationRun | None,
    rows: list[PositionValuationResult],
    *,
    total_count: int,
) -> dict[str, Any]:
    """Render a portfolio's latest valuation run + capped result rows.

    Mirrors the legacy ``get_latest_position_valuations_tool`` payload.
    """
    if run is None:
        return {
            "portfolio_id": portfolio_id,
            "found": False,
            "message": "No completed stored valuation run exists for this portfolio.",
            "results": [],
        }
    payload_rows = [
        {
            "position_id": result.position_id,
            "source_trade_id": result.source_trade_id,
            "underlying": result.position.underlying if result.position else None,
            "product_type": result.position.product_type if result.position else None,
            "ok": result.ok,
            "price": result.price,
            "market_value": result.market_value,
            "pnl": result.pnl,
            "error": result.error,
        }
        for result in rows
    ]
    return {
        "portfolio_id": portfolio_id,
        "found": True,
        "valuation_run_id": run.id,
        "status": run.status,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "summary": run.summary,
        "returned_count": len(payload_rows),
        "total_count": total_count,
        "results": payload_rows,
    }


def shape_market_data_profile(p: MarketDataProfile) -> dict[str, Any]:
    """Render a MarketDataProfile ORM row as a JSON-friendly dict."""
    return {
        "id": p.id,
        "name": p.name,
        "source": p.source,
        "symbol": p.symbol,
        "asset_class": p.asset_class,
        "start_date": p.start_date,
        "end_date": p.end_date,
        "adjust": p.adjust,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def shape_pricing_parameter_profile(p: PricingParameterProfile) -> dict[str, Any]:
    """Render a PricingParameterProfile ORM row as compact JSON for agents."""
    summary = p.summary if isinstance(p.summary, dict) else {}
    row_count = summary.get("row_count")
    if row_count is None:
        row_count = len(p.rows or [])
    compact_summary = {
        key: value
        for key, value in summary.items()
        if key not in {"underlyings", "skipped_positions"}
    }
    if "underlyings" in summary and isinstance(summary["underlyings"], list):
        compact_summary["underlying_count"] = len(summary["underlyings"])
    if "skipped_positions" in summary and isinstance(
        summary["skipped_positions"], list
    ):
        compact_summary["skipped_position_count"] = len(summary["skipped_positions"])
    return {
        "id": p.id,
        "name": p.name,
        "valuation_date": p.valuation_date.isoformat() if p.valuation_date else None,
        "source_type": p.source_type,
        "source_path": p.source_path,
        "status": p.status,
        "row_count": row_count,
        "summary": compact_summary,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def shape_rfq(rfq: Any) -> dict[str, Any]:
    """Render an RFQ ORM row as a JSON-friendly dict.

    Mirrors the legacy ``_rfq_summary`` from ``langchain_tools.py``. The
    ``quote_versions`` ORM relationship is ordered version-DESC by the model
    config, so ``quote_versions[0]`` (when present) is the latest.
    """
    quote_versions = list(getattr(rfq, "quote_versions", []) or [])
    latest = quote_versions[0] if quote_versions else None
    return {
        "rfq_id": rfq.id,
        "status": rfq.status,
        "client_name": rfq.client_name,
        "channel": rfq.channel,
        "request_payload": _product_native_payload(rfq.request_payload or {}),
        "quote_payload": _product_native_payload(rfq.quote_payload or {}),
        "approved_response": rfq.approved_response,
        "latest_quote_version": (
            {
                "id": latest.id,
                "version": latest.version,
                "status": latest.status,
                "quote_mode": latest.quote_mode,
                "error": latest.error,
                "request_payload": _product_native_payload(
                    getattr(latest, "request_payload", None) or {}
                ),
            }
            if latest
            else None
        ),
    }


def _product_native_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or "product_kwargs" not in payload:
        return payload
    native = rfq_draft_to_product_payload(payload)
    return {key: value for key, value in native.items() if value is not None}


def shape_booked_position(position: Any) -> dict[str, Any]:
    """Render a Position created by RFQ booking as a JSON-friendly dict.

    The legacy ``_position_summary`` from ``langchain_tools.py`` returned a
    deliberately narrow slice including RFQ provenance fields
    (``rfq_id``, ``rfq_quote_version_id``) which are absent from the
    standard ``shape_position`` envelope.
    """
    loaded_product = getattr(position, "__dict__", {}).get("product")
    return {
        "position_id": position.id,
        "portfolio_id": position.portfolio_id,
        "product_id": getattr(position, "product_id", None),
        "product": product_summary(loaded_product),
        "underlying": position.underlying,
        "product_type": position.product_type,
        "quantity": position.quantity,
        "entry_price": position.entry_price,
        "status": position.status,
        "rfq_id": position.rfq_id,
        "rfq_quote_version_id": position.rfq_quote_version_id,
    }


def normalize_artifact_paths(raw: dict[str, Any] | None) -> dict[str, str]:
    """Transform ReportJob.artifact_paths from on-disk absolute paths into
    virtual ``/artifacts/<basename>`` paths.

    ``services/reports.py`` stores ``str(settings.artifact_dir / "<stem>.html")``
    — an absolute filesystem path. The deep_agent backend mounts the
    artifacts dir at ``/artifacts/``, so the agent must call ``read_file``
    with a virtual path. Empty / None / unparseable values are dropped.
    """
    if not raw:
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if value is None or value == "":
            continue
        basename = Path(str(value)).name
        if not basename:
            continue
        out[key] = f"/artifacts/{basename}"
    return out


def derive_report_summary(result_payload: dict[str, Any] | None) -> Any:
    """Extract a useful 'summary' slice from a ReportJob's result_payload.

    ``services/reports.py:_build_report_payload`` writes different shapes
    depending on report_type:

    - portfolio/risk: ``{portfolio, pricing_parameter_profile, risk}`` —
      the agent-facing summary is ``risk.totals`` plus any breakdowns.
    - rfq: ``{rfq, status, request}``.
    - no-portfolio fallback: ``{summary, positions}`` — has an explicit
      ``summary`` field already.

    Returning ``result_payload.get("summary")`` raw would give the agent
    ``None`` for the most common report shape. This helper derives a useful
    slice for each known shape, falling back to the whole payload when the
    shape is unrecognized.
    """
    if not isinstance(result_payload, dict) or not result_payload:
        return None
    if "summary" in result_payload:
        return result_payload["summary"]
    risk = result_payload.get("risk")
    if isinstance(risk, dict):
        totals = risk.get("totals")
        if totals is not None:
            return {
                "report_type": "risk",
                "totals": totals,
                "portfolio": result_payload.get("portfolio"),
            }
        return {"report_type": "risk", "risk": risk}
    if "rfq" in result_payload:
        return {
            "report_type": "rfq",
            "rfq": result_payload.get("rfq"),
            "status": result_payload.get("status"),
        }
    return result_payload


def shape_report_row(job: ReportJob) -> dict[str, Any]:
    """Render a ReportJob ORM row as the legacy ``list_reports`` row shape."""
    payload = job.request_payload or {}
    return {
        "report_id": job.id,
        "report_type": job.report_type,
        "status": job.status,
        "portfolio_id": payload.get("portfolio_id"),
        "title": payload.get("title"),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "artifact_paths": normalize_artifact_paths(job.artifact_paths),
    }


def shape_report_full(job: ReportJob) -> dict[str, Any]:
    """Render a ReportJob row as the full ``get_report`` payload."""
    request_payload = job.request_payload or {}
    result_payload = job.result_payload or {}
    return {
        "report_id": job.id,
        "report_type": job.report_type,
        "status": job.status,
        "portfolio_id": request_payload.get("portfolio_id"),
        "title": request_payload.get("title"),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "artifact_paths": normalize_artifact_paths(job.artifact_paths),
        "request_payload": request_payload,
        "result_payload": result_payload,
        "summary": derive_report_summary(result_payload),
    }


def portfolio_error_response(exc: Exception) -> dict[str, Any]:
    """Translate domain-layer exceptions into the legacy LLM error shape."""
    if isinstance(exc, RuleValidationError):
        return {"ok": False, "errors": exc.errors}
    if isinstance(exc, PortfolioCycleError):
        return {"ok": False, "error": str(exc), "cycle_path": exc.cycle_path}
    if isinstance(exc, PortfolioDepthError):
        return {"ok": False, "error": str(exc), "depth_path": exc.depth_path}
    return {"ok": False, "error": str(exc)}


def shape_pricing_parameter_row(row: PricingParameterRow) -> dict[str, Any]:
    """Row ids are the agent's handles for upsert/delete row tools."""
    return {
        "id": row.id,
        "source_trade_id": row.source_trade_id,
        "symbol": row.symbol,
        "instrument_id": row.instrument_id,
        "rate": row.rate,
        "dividend_yield": row.dividend_yield,
        "volatility": row.volatility,
    }


def shape_assumption_row(row: AssumptionRow) -> dict[str, Any]:
    payload = row.source_payload or {}
    return {
        "id": row.id,
        "instrument_id": row.instrument_id,
        "symbol": row.symbol,
        "rate": row.rate,
        "dividend_yield": row.dividend_yield,
        "volatility": row.volatility,
        "field_sources": payload.get("manual_input_sources", {}),
    }


def shape_assumption_set(
    assumption_set: AssumptionSet, *, include_rows: bool = False
) -> dict[str, Any]:
    summary = assumption_set.summary if isinstance(assumption_set.summary, dict) else {}
    shaped: dict[str, Any] = {
        "id": assumption_set.id,
        "name": assumption_set.name,
        "valuation_date": (
            assumption_set.valuation_date.isoformat()
            if assumption_set.valuation_date
            else None
        ),
        "status": assumption_set.status,
        "row_count": summary.get("row_count", len(assumption_set.rows or [])),
        "created_at": (
            assumption_set.created_at.isoformat() if assumption_set.created_at else None
        ),
    }
    if include_rows:
        shaped["rows"] = [shape_assumption_row(row) for row in assumption_set.rows]
    return shaped


def shape_instrument_defaults(instrument: Instrument) -> dict[str, Any]:
    return {
        "id": instrument.id,
        "symbol": instrument.symbol,
        "status": instrument.status,
        "rate": instrument.rate,
        "dividend_yield": instrument.dividend_yield,
        "volatility": instrument.volatility,
        "rate_curve": instrument.rate_curve,
        "dividend_yield_curve": instrument.dividend_yield_curve,
        "volatility_curve": instrument.volatility_curve,
    }


def domain_write_error_response(exc: DomainWriteError) -> dict[str, Any]:
    response: dict[str, Any] = {"ok": False, "error": exc.error}
    if exc.detail is not None:
        response["detail"] = exc.detail
    return response


def parse_valuation_date(value: str | None) -> datetime | None:
    """ISO-8601 string -> datetime; structured refusal on garbage."""
    if value is None or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise DomainWriteError("invalid_valuation_date", {"value": value}) from exc

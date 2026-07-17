"""Normalize persisted producer evidence for deterministic limit evaluation."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
from typing import Any, Mapping

from sqlalchemy.orm import Session

from ...models import BacktestRun, RiskRun, ScenarioTestRun
from ..fx import FxRateEvidence, fx_rate_evidence_as_of
from ..source_evidence import (
    has_exact_source_metric_contract,
    source_metric_contract,
)
from .evaluator import NormalizedObservation
from .metrics import get_metric


SCENARIO_TAIL_METHODOLOGY: dict[str, Any] = {
    "method": "scenario_distribution",
    "confidence": 0.95,
    "horizon": "scenario_set",
    "scaling": "none",
}
BACKTEST_TAIL_METHODOLOGY: dict[str, Any] = {
    "method": "historical",
    "confidence": 0.95,
    "horizon": "1_trading_day",
    "scaling": "none",
}

_USABLE_STATUSES = frozenset({"completed", "completed_with_errors"})
_MISSING_MARKET_EVIDENCE = re.compile(
    r"^position:([1-9][0-9]*):missing:[a-z][a-z0-9_-]*$"
)


@dataclass(frozen=True, slots=True)
class ObservationScope:
    """One evaluated scope within a source run."""

    scope_type: str
    value: str | int | None = None
    position_ids: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        if self.scope_type not in {
            "portfolio",
            "underlying",
            "product_family",
            "position",
        }:
            raise ValueError(f"unsupported observation scope {self.scope_type!r}")
        if self.position_ids is not None:
            normalized = tuple(sorted({int(value) for value in self.position_ids}))
            object.__setattr__(self, "position_ids", normalized)


@dataclass(frozen=True, slots=True)
class SourceMetricSemantics:
    unit: str
    currency: str | None
    bump_convention: str | None
    calculation_convention: str


def _source_metric_semantics(
    source_metadata: Mapping[str, Any],
    *,
    source_kind: str,
    metric_kind: str,
    reporting_currency: str | None,
) -> SourceMetricSemantics | None:
    if not has_exact_source_metric_contract(source_metadata, source_kind):
        return None
    metric = (
        source_metric_contract(source_kind)
        .get("metrics", {})
        .get(metric_kind)
    )
    if not isinstance(metric, dict):
        return None
    unit = metric.get("unit")
    dimension = metric.get("currency_dimension")
    calculation = metric.get("calculation_convention")
    bump = metric.get("bump_convention")
    if (
        not isinstance(unit, str)
        or not unit
        or dimension not in {"none", "reporting"}
        or not isinstance(calculation, str)
        or not calculation
        or (bump is not None and (not isinstance(bump, str) or not bump))
    ):
        return None
    if dimension == "none":
        resolved_unit = unit
        resolved_currency = None
    else:
        resolved_currency = reporting_currency
        resolved_unit = unit.replace(
            "{currency}",
            reporting_currency if reporting_currency is not None else "{currency}",
        )
    return SourceMetricSemantics(
        unit=resolved_unit,
        currency=resolved_currency,
        bump_convention=bump,
        calculation_convention=calculation,
    )


def granular_market_evidence_position_ids(
    source_metadata: Mapping[str, Any],
    *,
    resolved_position_ids: list[int] | tuple[int, ...] | None,
) -> set[int] | None:
    """Return affected positions only for a complete, canonical diagnostic set.

    A global ``market_evidence_complete=False`` is usable for a narrower limit
    scope only when the producer persisted the precise positions whose inputs
    were synthetic.  Anything malformed is deliberately treated as unknown for
    every scope rather than risking a selective false clean result.
    """
    raw = source_metadata.get("missing_market_evidence")
    if not isinstance(raw, list) or not raw:
        return None
    try:
        resolved = {
            int(position_id)
            for position_id in (resolved_position_ids or [])
            if not isinstance(position_id, bool) and int(position_id) > 0
        }
    except (TypeError, ValueError):
        return None
    if not resolved:
        return None
    affected: set[int] = set()
    for value in raw:
        if not isinstance(value, str):
            return None
        match = _MISSING_MARKET_EVIDENCE.fullmatch(value)
        if match is None:
            return None
        affected.add(int(match.group(1)))
    return affected if affected and affected <= resolved else None


def _unknown(
    *,
    source_kind: str,
    unit: str,
    currency: str | None,
    source_status: str,
    reason_code: str,
    evidence: dict[str, Any],
    bump_convention: str | None = None,
    coverage_count: int | None = None,
    coverage_ratio: float | None = None,
) -> NormalizedObservation:
    return NormalizedObservation(
        values=None,
        source_kind=source_kind,
        unit=unit,
        currency=currency,
        bump_convention=bump_convention,
        source_status=source_status,
        is_complete=reason_code != "incomplete_scope",
        reason_code=reason_code,
        reason={
            "empty_source": "The source completed without usable observations.",
            "source_failed": "The source run failed.",
            "missing_source": "The source is not in a usable terminal state.",
            "incomplete_scope": "The source did not cover the complete scope.",
            "methodology_mismatch": "The source methodology does not match.",
            "missing_scenario": "The requested scenario is absent.",
            "missing_fx": "Required point-in-time FX evidence is absent.",
            "invalid_value": "The source returned an invalid numeric value.",
            "metric_contract_mismatch": (
                "The source metric unit or bump contract is missing or incompatible."
            ),
        }.get(reason_code, reason_code),
        coverage_count=coverage_count,
        coverage_ratio=coverage_ratio,
        evidence=deepcopy(evidence),
    )


def _status_unknown(
    *,
    source_kind: str,
    source_status: str,
    unit: str,
    currency: str | None,
    evidence: dict[str, Any],
    bump_convention: str | None = None,
) -> NormalizedObservation | None:
    if source_status in _USABLE_STATUSES:
        return None
    if source_status == "empty":
        reason_code = "empty_source"
    elif source_status == "failed":
        reason_code = "source_failed"
    else:
        reason_code = "missing_source"
    return _unknown(
        source_kind=source_kind,
        unit=unit,
        currency=currency,
        bump_convention=bump_convention,
        source_status=source_status,
        reason_code=reason_code,
        evidence=evidence,
    )


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _risk_rows_for_scope(
    rows: list[dict[str, Any]],
    scope: ObservationScope,
) -> tuple[list[dict[str, Any]], tuple[int, ...] | None]:
    selected = list(rows)
    requested_ids = scope.position_ids
    if requested_ids is not None:
        wanted = set(requested_ids)
        selected = [
            row for row in selected if row.get("position_id") in wanted
        ]
    if scope.scope_type == "underlying":
        selected = [
            row for row in selected if str(row.get("underlying")) == str(scope.value)
        ]
    elif scope.scope_type == "product_family":
        selected = [
            row
            for row in selected
            if str(row.get("product_family") or row.get("product_type"))
            == str(scope.value)
        ]
    elif scope.scope_type == "position":
        position_id = (
            int(scope.value)
            if scope.value is not None
            else requested_ids[0]
            if requested_ids
            else None
        )
        selected = [
            row for row in selected if row.get("position_id") == position_id
        ]
        requested_ids = (position_id,) if position_id is not None else ()
    return selected, requested_ids


def _fx_value(
    session: Session | None,
    *,
    value: float,
    base_currency: str,
    target_currency: str,
    valuation_as_of: datetime,
) -> tuple[float | None, FxRateEvidence | None]:
    if base_currency == target_currency:
        return value, None
    if session is None:
        return None, None
    evidence = fx_rate_evidence_as_of(
        session,
        base_currency,
        target_currency,
        valuation_as_of,
    )
    if evidence is None:
        return None, None
    converted = value * evidence.rate
    return (converted, evidence) if math.isfinite(converted) else (None, evidence)


def adapt_risk_run(
    session: Session | None,
    run: RiskRun,
    *,
    metric_kind: str,
    aggregation: str,
    unit: str,
    scope: ObservationScope,
    currency: str | None = None,
    valuation_as_of: datetime | None = None,
    bump_convention: str | None = None,
) -> NormalizedObservation:
    """Adapt shared, currency-bucket, or per-position risk-run values."""
    descriptor = get_metric(metric_kind)
    metrics = deepcopy(run.metrics or {})
    source_metadata = dict(metrics.get("source_metadata") or {})
    semantics = _source_metric_semantics(
        source_metadata,
        source_kind="risk_run",
        metric_kind=metric_kind,
        reporting_currency=currency,
    )
    if semantics is not None:
        unit = semantics.unit
        currency = semantics.currency
        bump_convention = semantics.bump_convention
    evidence: dict[str, Any] = {
        "risk_run_id": run.id,
        "method": run.method,
        "resolved_position_ids": list(run.resolved_position_ids or []),
        "metric_contract_id": (
            (source_metadata.get("metric_contract") or {}).get("contract_id")
            if isinstance(source_metadata.get("metric_contract"), dict)
            else None
        ),
        "calculation_convention": (
            semantics.calculation_convention if semantics is not None else None
        ),
    }
    status_unknown = _status_unknown(
        source_kind="risk_run",
        source_status=run.status,
        unit=unit,
        currency=currency,
        bump_convention=bump_convention,
        evidence=evidence,
    )
    if status_unknown is not None:
        return status_unknown

    if semantics is None:
        return _unknown(
            source_kind="risk_run",
            unit=unit,
            currency=currency,
            bump_convention=bump_convention,
            source_status=run.status,
            reason_code="metric_contract_mismatch",
            evidence=evidence,
        )

    missing_evidence_ids: set[int] | None = set()
    if source_metadata.get("market_evidence_complete") is False:
        missing_evidence_ids = granular_market_evidence_position_ids(
            source_metadata,
            resolved_position_ids=run.resolved_position_ids,
        )
        if missing_evidence_ids is None:
            evidence["missing_market_evidence"] = deepcopy(
                source_metadata.get("missing_market_evidence")
            )
            return _unknown(
                source_kind="risk_run",
                unit=unit,
                currency=currency,
                bump_convention=bump_convention,
                source_status=run.status,
                reason_code="incomplete_scope",
                evidence=evidence,
            )
    rows = list(metrics.get("positions") or [])
    selected, requested_ids = _risk_rows_for_scope(rows, scope)
    if requested_ids is None and scope.scope_type == "portfolio":
        requested_ids = tuple(sorted(run.resolved_position_ids or ()))
    selected_ids = tuple(
        int(row["position_id"])
        for row in selected
        if row.get("position_id") is not None
    )
    if requested_ids is not None:
        missing_ids = sorted(set(requested_ids) - set(selected_ids))
    else:
        missing_ids = []
    failed_ids = sorted(
        int(row["position_id"])
        for row in selected
        if row.get("position_id") is not None
        and (
            not bool(row.get("pricing_ok"))
            or not bool(row.get("greeks_ok"))
        )
    )
    requested_count = (
        len(requested_ids) if requested_ids is not None else len(selected)
    )
    covered_count = max(0, requested_count - len(missing_ids) - len(failed_ids))
    coverage_ratio = (
        covered_count / requested_count if requested_count else 0.0
    )
    evidence.update(
        {
            "requested_position_ids": (
                list(requested_ids) if requested_ids is not None else selected_ids
            ),
            "covered_position_ids": sorted(
                set(selected_ids) - set(failed_ids)
            ),
            "missing_position_ids": missing_ids,
            "failed_position_ids": failed_ids,
        }
    )
    if requested_count == 0:
        return _unknown(
            source_kind="risk_run",
            unit=unit,
            currency=currency,
            bump_convention=bump_convention,
            source_status=run.status,
            reason_code="empty_source",
            coverage_count=0,
            coverage_ratio=0.0,
            evidence=evidence,
        )
    if missing_ids or failed_ids:
        return _unknown(
            source_kind="risk_run",
            unit=unit,
            currency=currency,
            bump_convention=bump_convention,
            source_status=run.status,
            reason_code="incomplete_scope",
            coverage_count=covered_count,
            coverage_ratio=coverage_ratio,
            evidence=evidence,
        )
    scoped_missing_evidence = sorted(set(selected_ids) & missing_evidence_ids)
    if scoped_missing_evidence:
        evidence["missing_market_evidence"] = [
            value
            for value in source_metadata["missing_market_evidence"]
            if int(_MISSING_MARKET_EVIDENCE.fullmatch(value).group(1))
            in scoped_missing_evidence
        ]
        return _unknown(
            source_kind="risk_run",
            unit=unit,
            currency=currency,
            bump_convention=bump_convention,
            source_status=run.status,
            reason_code="incomplete_scope",
            coverage_count=covered_count,
            coverage_ratio=coverage_ratio,
            evidence=evidence,
        )

    run_position_ids = tuple(sorted(run.resolved_position_ids or selected_ids))
    scope_is_full_run = (
        scope.scope_type == "portfolio"
        and (
            requested_ids is None
            or tuple(sorted(requested_ids)) == run_position_ids
        )
    )
    valuation = valuation_as_of or _source_valuation(run)
    fx_evidence: list[dict[str, Any]] = []
    missing_fx: list[str] = []
    values: list[float] = []
    invalid_numeric = False

    if scope_is_full_run and aggregation == "net" and not descriptor.monetary:
        value = _finite((metrics.get("shared") or {}).get(metric_kind))
        evidence["value_source"] = "shared"
        if value is not None:
            values.append(value)
    elif scope_is_full_run and aggregation == "net" and descriptor.monetary:
        if not currency:
            return _unknown(
                source_kind="risk_run",
                unit=unit,
                currency=currency,
                bump_convention=bump_convention,
                source_status=run.status,
                reason_code="missing_fx",
                coverage_count=covered_count,
                coverage_ratio=coverage_ratio,
                evidence={**evidence, "missing_fx": ["reporting_currency"]},
            )
        evidence["value_source"] = "by_currency"
        expected_currency_counts: dict[str, int] = {}
        for row in selected:
            source_currency = str(row.get("currency") or "UNKNOWN")
            expected_currency_counts[source_currency] = (
                expected_currency_counts.get(source_currency, 0) + 1
            )
        buckets = metrics.get("by_currency")
        actual_currency_counts: dict[str, int] = {}
        if isinstance(buckets, dict):
            for source_currency, bucket in buckets.items():
                if not isinstance(source_currency, str) or not isinstance(bucket, dict):
                    actual_currency_counts = {}
                    break
                position_count = bucket.get("position_count")
                if (
                    isinstance(position_count, bool)
                    or not isinstance(position_count, int)
                    or position_count < 0
                ):
                    actual_currency_counts = {}
                    break
                actual_currency_counts[source_currency] = position_count
        if actual_currency_counts != expected_currency_counts:
            evidence["currency_coverage"] = {
                "expected_position_counts": expected_currency_counts,
                "actual_position_counts": actual_currency_counts,
            }
            return _unknown(
                source_kind="risk_run",
                unit=unit,
                currency=currency,
                bump_convention=bump_convention,
                source_status=run.status,
                reason_code="incomplete_scope",
                coverage_count=covered_count,
                coverage_ratio=coverage_ratio,
                evidence=evidence,
            )
        for source_currency, bucket in sorted(buckets.items()):
            native = _finite((bucket or {}).get(metric_kind))
            if native is None:
                invalid_numeric = True
                continue
            converted, fx = _fx_value(
                session,
                value=native,
                base_currency=str(source_currency),
                target_currency=currency,
                valuation_as_of=valuation,
            )
            if converted is None:
                missing_fx.append(f"{source_currency}->{currency}")
                continue
            values.append(converted)
            if fx is not None:
                fx_evidence.append(fx.as_dict())
    else:
        evidence["value_source"] = "positions"
        for row in selected:
            native = _finite(row.get(metric_kind))
            if native is None:
                invalid_numeric = True
                continue
            if descriptor.monetary:
                if not currency:
                    missing_fx.append("reporting_currency")
                    continue
                source_currency = str(row.get("currency") or "")
                converted, fx = _fx_value(
                    session,
                    value=native,
                    base_currency=source_currency,
                    target_currency=currency,
                    valuation_as_of=valuation,
                )
                if converted is None:
                    missing_fx.append(f"{source_currency}->{currency}")
                    continue
                native = converted
                if fx is not None:
                    fx_evidence.append(fx.as_dict())
            values.append(native)

    deduplicated_fx = {
        (
            item["fx_rate_id"],
            item["base_currency"],
            item["quote_currency"],
            item["is_inverse"],
        ): item
        for item in fx_evidence
    }
    evidence["fx_rates"] = [
        deduplicated_fx[key] for key in sorted(deduplicated_fx)
    ]
    if invalid_numeric:
        return _unknown(
            source_kind="risk_run",
            unit=unit,
            currency=currency,
            bump_convention=bump_convention,
            source_status=run.status,
            reason_code="invalid_value",
            coverage_count=covered_count,
            coverage_ratio=coverage_ratio,
            evidence=evidence,
        )
    if missing_fx:
        evidence["missing_fx"] = sorted(set(missing_fx))
        return _unknown(
            source_kind="risk_run",
            unit=unit,
            currency=currency,
            bump_convention=bump_convention,
            source_status=run.status,
            reason_code="missing_fx",
            coverage_count=covered_count,
            coverage_ratio=coverage_ratio,
            evidence=evidence,
        )
    if not values:
        return _unknown(
            source_kind="risk_run",
            unit=unit,
            currency=currency,
            bump_convention=bump_convention,
            source_status=run.status,
            reason_code="invalid_value",
            coverage_count=covered_count,
            coverage_ratio=coverage_ratio,
            evidence=evidence,
        )
    return NormalizedObservation(
        values=tuple(values),
        source_kind="risk_run",
        unit=unit,
        currency=currency,
        bump_convention=bump_convention,
        source_status=run.status,
        is_complete=True,
        coverage_count=covered_count,
        coverage_ratio=coverage_ratio,
        evidence=evidence,
    )


def adapt_scenario_test_run(
    run: ScenarioTestRun,
    *,
    metric_kind: str,
    methodology: Mapping[str, Any],
    unit: str,
    currency: str | None,
    session: Session | None = None,
    valuation_as_of: datetime | None = None,
) -> NormalizedObservation:
    """Adapt locked scenario-distribution tails or exact stress selections."""
    from ..domains.scenario_catalog import strip_source_snapshot

    results = run.results or {}
    source_metadata = dict(results.get("source_metadata") or {})
    semantics = _source_metric_semantics(
        source_metadata,
        source_kind="scenario_test",
        metric_kind=metric_kind,
        reporting_currency=currency,
    )
    if semantics is not None:
        unit = semantics.unit
        currency = semantics.currency
    evidence: dict[str, Any] = {
        "scenario_test_run_id": run.id,
        "scenario_spec": strip_source_snapshot(run.scenario_spec or {}),
        "source_metadata": deepcopy(source_metadata),
        "metric_contract_id": (
            (source_metadata.get("metric_contract") or {}).get("contract_id")
            if isinstance(source_metadata.get("metric_contract"), dict)
            else None
        ),
        "calculation_convention": (
            semantics.calculation_convention if semantics is not None else None
        ),
    }
    status_unknown = _status_unknown(
        source_kind="scenario_test",
        source_status=run.status,
        unit=unit,
        currency=currency,
        evidence=evidence,
    )
    if status_unknown is not None:
        return status_unknown
    if semantics is None:
        return _unknown(
            source_kind="scenario_test",
            unit=unit,
            currency=currency,
            source_status=run.status,
            reason_code="metric_contract_mismatch",
            evidence=evidence,
        )
    if run.excluded_positions:
        evidence["excluded_positions"] = deepcopy(run.excluded_positions)
        return _unknown(
            source_kind="scenario_test",
            unit=unit,
            currency=currency,
            source_status=run.status,
            reason_code="incomplete_scope",
            evidence=evidence,
        )
    if source_metadata.get("methodology") != SCENARIO_TAIL_METHODOLOGY:
        return _unknown(
            source_kind="scenario_test",
            unit=unit,
            currency=currency,
            source_status=run.status,
            reason_code="methodology_mismatch",
            evidence=evidence,
        )
    if metric_kind in {"var", "cvar"}:
        if dict(methodology) != SCENARIO_TAIL_METHODOLOGY:
            return _unknown(
                source_kind="scenario_test",
                unit=unit,
                currency=currency,
                source_status=run.status,
                reason_code="methodology_mismatch",
                evidence={**evidence, "methodology": deepcopy(dict(methodology))},
            )
        tail = results.get("var_cvar") or {}
        if _finite(tail.get("confidence")) != 0.95:
            return _unknown(
                source_kind="scenario_test",
                unit=unit,
                currency=currency,
                source_status=run.status,
                reason_code="methodology_mismatch",
                evidence={**evidence, "source_confidence": tail.get("confidence")},
            )
        value = _finite(tail.get(metric_kind))
        if value is None:
            return _unknown(
                source_kind="scenario_test",
                unit=unit,
                currency=currency,
                source_status=run.status,
                reason_code="invalid_value",
                evidence=evidence,
            )
        evidence.update(
            {
                "methodology": deepcopy(SCENARIO_TAIL_METHODOLOGY),
                "confidence": 0.95,
                "result_path": f"var_cvar.{metric_kind}",
            }
        )
        converted = _convert_single_currency_value(
            session,
            value=value,
            source_metadata=source_metadata,
            target_currency=currency,
            valuation_as_of=valuation_as_of or _source_valuation(run),
            evidence=evidence,
        )
        if converted[0] is None:
            return _unknown(
                source_kind="scenario_test",
                unit=unit,
                currency=currency,
                source_status=run.status,
                reason_code=converted[1],
                evidence=evidence,
            )
        return NormalizedObservation(
            values=(converted[0],),
            source_kind="scenario_test",
            unit=unit,
            currency=currency,
            source_status=run.status,
            evidence=evidence,
        )

    if metric_kind != "stress_pnl":
        return _unknown(
            source_kind="scenario_test",
            unit=unit,
            currency=currency,
            source_status=run.status,
            reason_code="methodology_mismatch",
            evidence=evidence,
        )
    scenarios = {
        str(row.get("name")): row
        for row in (results.get("scenarios") or [])
        if row.get("name") is not None
    }
    method = dict(methodology)
    if method.get("scenario_set_hash") != source_metadata.get(
        "scenario_set_hash"
    ):
        return _unknown(
            source_kind="scenario_test",
            unit=unit,
            currency=currency,
            source_status=run.status,
            reason_code="methodology_mismatch",
            evidence=evidence,
        )
    selection = method.get("selection")
    if selection == "named":
        requested_names = [str(method.get("scenario_name") or "")]
    elif selection == "worst_of_set":
        raw_names = method.get("scenario_names")
        if not isinstance(raw_names, list) or not raw_names:
            requested_names = []
        else:
            requested_names = [str(name) for name in raw_names]
    else:
        requested_names = []
    missing_names = [name for name in requested_names if name not in scenarios]
    frozen_names = source_metadata.get("scenario_names")
    if (
        not isinstance(frozen_names, list)
        or set(scenarios) != {str(name) for name in frozen_names}
    ):
        return _unknown(
            source_kind="scenario_test",
            unit=unit,
            currency=currency,
            source_status=run.status,
            reason_code="missing_scenario",
            evidence={**evidence, "source_scenario_names": frozen_names},
        )
    if not requested_names or missing_names:
        return _unknown(
            source_kind="scenario_test",
            unit=unit,
            currency=currency,
            source_status=run.status,
            reason_code="missing_scenario",
            evidence={
                **evidence,
                "requested_scenarios": requested_names,
                "missing_scenarios": missing_names,
            },
        )
    candidates: list[tuple[float, str, dict[str, Any]]] = []
    for name in requested_names:
        value = _finite(scenarios[name].get("pnl"))
        if value is None:
            return _unknown(
                source_kind="scenario_test",
                unit=unit,
                currency=currency,
                source_status=run.status,
                reason_code="invalid_value",
                evidence=evidence,
            )
        candidates.append((value, name, scenarios[name]))
    chosen = min(candidates, key=lambda item: (item[0], item[1]))
    evidence.update(
        {
            "selection": selection,
            "requested_scenarios": requested_names,
            "scenario_name": chosen[1],
            "scenario_pnl": chosen[0],
            "scenario_pnl_pct": chosen[2].get("pnl_pct"),
        }
    )
    converted = _convert_single_currency_value(
        session,
        value=chosen[0],
        source_metadata=source_metadata,
        target_currency=currency,
        valuation_as_of=valuation_as_of or _source_valuation(run),
        evidence=evidence,
    )
    if converted[0] is None:
        return _unknown(
            source_kind="scenario_test",
            unit=unit,
            currency=currency,
            source_status=run.status,
            reason_code=converted[1],
            evidence=evidence,
        )
    return NormalizedObservation(
        values=(converted[0],),
        source_kind="scenario_test",
        unit=unit,
        currency=currency,
        source_status=run.status,
        evidence=evidence,
    )


def adapt_backtest_run(
    run: BacktestRun,
    *,
    metric_kind: str,
    methodology: Mapping[str, Any],
    unit: str,
    currency: str | None,
    session: Session | None = None,
    valuation_as_of: datetime | None = None,
) -> NormalizedObservation:
    """Adapt the live loss-positive historical one-day VaR/CVaR producer."""
    results = run.results or {}
    source_metadata = dict(results.get("source_metadata") or {})
    semantics = _source_metric_semantics(
        source_metadata,
        source_kind="backtest",
        metric_kind=metric_kind,
        reporting_currency=currency,
    )
    if semantics is not None:
        unit = semantics.unit
        currency = semantics.currency
    evidence: dict[str, Any] = {
        "backtest_run_id": run.id,
        "spec": deepcopy(run.spec or {}),
        "source_metadata": deepcopy(source_metadata),
        "metric_contract_id": (
            (source_metadata.get("metric_contract") or {}).get("contract_id")
            if isinstance(source_metadata.get("metric_contract"), dict)
            else None
        ),
        "calculation_convention": (
            semantics.calculation_convention if semantics is not None else None
        ),
    }
    status_unknown = _status_unknown(
        source_kind="backtest",
        source_status=run.status,
        unit=unit,
        currency=currency,
        evidence=evidence,
    )
    if status_unknown is not None:
        return status_unknown
    if semantics is None:
        return _unknown(
            source_kind="backtest",
            unit=unit,
            currency=currency,
            source_status=run.status,
            reason_code="metric_contract_mismatch",
            evidence=evidence,
        )
    if run.excluded_positions:
        evidence["excluded_positions"] = deepcopy(run.excluded_positions)
        return _unknown(
            source_kind="backtest",
            unit=unit,
            currency=currency,
            source_status=run.status,
            reason_code="incomplete_scope",
            evidence=evidence,
        )
    if (
        metric_kind not in {"var", "cvar"}
        or dict(methodology) != BACKTEST_TAIL_METHODOLOGY
        or source_metadata.get("methodology") != BACKTEST_TAIL_METHODOLOGY
    ):
        return _unknown(
            source_kind="backtest",
            unit=unit,
            currency=currency,
            source_status=run.status,
            reason_code="methodology_mismatch",
            evidence={**evidence, "methodology": deepcopy(dict(methodology))},
        )
    portfolio = results.get("portfolio")
    result_prefix = "portfolio"
    if not isinstance(portfolio, dict):
        # Compatibility with early persisted test fixtures; new producers use
        # the authoritative ``portfolio`` shape.
        portfolio = results.get("portfolio_summary")
        result_prefix = "portfolio_summary"
    key = f"{metric_kind}_95"
    value = _finite((portfolio or {}).get(key))
    if value is None:
        return _unknown(
            source_kind="backtest",
            unit=unit,
            currency=currency,
            source_status=run.status,
            reason_code="invalid_value",
            evidence=evidence,
        )
    evidence.update(
        {
            "methodology": deepcopy(BACKTEST_TAIL_METHODOLOGY),
            "confidence": 0.95,
            "result_path": f"{result_prefix}.{key}",
        }
    )
    converted = _convert_single_currency_value(
        session,
        value=value,
        source_metadata=source_metadata,
        target_currency=currency,
        valuation_as_of=valuation_as_of or _source_valuation(run),
        evidence=evidence,
    )
    if converted[0] is None:
        return _unknown(
            source_kind="backtest",
            unit=unit,
            currency=currency,
            source_status=run.status,
            reason_code=converted[1],
            evidence=evidence,
        )
    return NormalizedObservation(
        values=(converted[0],),
        source_kind="backtest",
        unit=unit,
        currency=currency,
        source_status=run.status,
        evidence=evidence,
    )


def _source_valuation(run: RiskRun | ScenarioTestRun | BacktestRun) -> datetime:
    payload = run.metrics if isinstance(run, RiskRun) else run.results
    metadata = (payload or {}).get("source_metadata") or {}
    raw = (
        (payload or {}).get("valuation_as_of")
        if isinstance(run, RiskRun)
        else metadata.get("valuation_as_of")
    )
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                return parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed.replace(tzinfo=None)
        except ValueError:
            pass
    return run.created_at


def _convert_single_currency_value(
    session: Session | None,
    *,
    value: float,
    source_metadata: Mapping[str, Any],
    target_currency: str | None,
    valuation_as_of: datetime,
    evidence: dict[str, Any],
) -> tuple[float | None, str]:
    currencies = source_metadata.get("source_currencies")
    if (
        not isinstance(currencies, list)
        or len(currencies) != 1
        or not isinstance(currencies[0], str)
        or not currencies[0].strip()
    ):
        evidence["source_currencies"] = deepcopy(currencies)
        return None, "incomplete_scope"
    source_currency = currencies[0].strip().upper()
    evidence["source_currency"] = source_currency
    if not target_currency:
        return None, "missing_fx"
    target = target_currency.strip().upper()
    converted, fx = _fx_value(
        session,
        value=value,
        base_currency=source_currency,
        target_currency=target,
        valuation_as_of=valuation_as_of,
    )
    if converted is None:
        evidence["missing_fx"] = [f"{source_currency}->{target}"]
        return None, "missing_fx"
    evidence["fx_rates"] = [fx.as_dict()] if fx is not None else []
    return converted, ""

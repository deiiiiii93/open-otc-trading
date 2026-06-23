"""Structured position term mirrors and query helpers."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from ...models import (
    AsianAveragingDate,
    AsianTerm,
    DoubleBarrierTerm,
    MarketSnapshot,
    OptionCoreTerm,
    Portfolio,
    Position,
    PositionBarrierState,
    SharkfinTerm,
    SingleBarrierTerm,
    SnowballKoSchedule,
    SnowballTerm,
    utcnow,
)
from .products import compatibility_terms_for_position
from ..portfolio_membership import resolve_positions

_OPTION_CORE_TYPES = {
    "europeanoption",
    "europeanvanillaoption",
    "vanillaoption",
    "vanillacall",
    "vanillaput",
    "americanoption",
    "asianoption",
    "barrieroption",
    "singlebarrieroption",
    "doublebarrieroption",
    "onetouchoption",
    "doubleonetouchoption",
    "sharkfinoption",
    "singlesharkfinoption",
    "doublesharkfinoption",
    "snowball",
    "snowballoption",
}


def upsert_position_term_rows(session: Session, position: Position) -> None:
    """Mirror supported ``product_kwargs`` fields into structured term tables."""
    compat = compatibility_terms_for_position(position)
    kwargs = dict(compat["product_kwargs"] or {})
    product_type = _normalize_product_type(compat["product_type"])
    if product_type in _OPTION_CORE_TYPES:
        session.merge(
            OptionCoreTerm(
                position_id=position.id,
                strike=_float_or_none(_first_present(kwargs, "strike")),
                expiry_date=_date_or_none(
                    _first_present(
                        kwargs,
                        "expiry_date",
                        "expiry",
                        "maturity_date",
                        "exercise_date",
                    )
                ),
                option_type=_str_or_none(_first_present(kwargs, "option_type")),
                side=_str_or_none(_first_present(kwargs, "side")) or "long",
                currency=_str_or_none(_first_present(kwargs, "currency")) or "CNY",
                notional=_float_or_none(
                    _first_present(kwargs, "notional", "notional_amount")
                ),
            )
        )

    if product_type in {"barrieroption", "singlebarrieroption", "onetouchoption"}:
        session.merge(
            SingleBarrierTerm(
                position_id=position.id,
                barrier=_float_or_none(
                    _first_present(kwargs, "barrier", "barrier_config.barrier")
                ),
                barrier_type=_str_or_none(
                    _first_present(kwargs, "barrier_type", "barrier_config.barrier_type")
                ),
                rebate=_float_or_none(_first_present(kwargs, "rebate")),
            )
        )

    if product_type in {
        "doublebarrieroption",
        "doubleonetouchoption",
        "doublesharkfinoption",
    }:
        session.merge(
            DoubleBarrierTerm(
                position_id=position.id,
                upper_barrier=_float_or_none(
                    _first_present(kwargs, "upper_barrier", "barrier_config.upper_barrier")
                ),
                lower_barrier=_float_or_none(
                    _first_present(kwargs, "lower_barrier", "barrier_config.lower_barrier")
                ),
                barrier_kind=_str_or_none(
                    _first_present(kwargs, "barrier_kind", "barrier_config.barrier_kind")
                ),
                rebate=_float_or_none(_first_present(kwargs, "rebate")),
            )
        )

    if product_type in {"sharkfinoption", "singlesharkfinoption", "doublesharkfinoption"}:
        session.merge(
            SharkfinTerm(
                position_id=position.id,
                participation_rate=_float_or_none(
                    _first_present(kwargs, "participation_rate")
                ),
                coupon=_float_or_none(_first_present(kwargs, "coupon", "coupon_rate")),
            )
        )

    if product_type == "asianoption":
        records = _schedule_records(kwargs, "averaging_dates", "observation_dates")
        session.merge(
            AsianTerm(
                position_id=position.id,
                averaging_method=_str_or_none(
                    _first_present(kwargs, "averaging_method")
                ),
                averaging_kind=_str_or_none(_first_present(kwargs, "averaging_kind")),
                n_observations=int(
                    _float_or_none(_first_present(kwargs, "n_observations"))
                    or len(records)
                    or 0
                ),
            )
        )
        _replace_asian_schedule(session, position.id, records)
        # Snapshot any already-past fixings now (a session is in hand) so a
        # seasoned/backdated booking never carries uncaptured past observations
        # into pricing.
        from .positions import capture_due_asian_fixings

        capture_due_asian_fixings(session, position.id)

    if product_type in {"snowball", "snowballoption"}:
        session.merge(
            SnowballTerm(
                position_id=position.id,
                initial_price=_float_or_none(_first_present(kwargs, "initial_price")),
                ki_barrier=_float_or_none(
                    _first_present(kwargs, "barrier_config.ki_barrier", "ki_barrier")
                ),
                coupon=_float_or_none(
                    _first_present(
                        kwargs,
                        "barrier_config.ko_rate",
                        "accrual_config.coupon_rate",
                        "coupon_rate",
                        "coupon",
                    )
                ),
                start_date=_date_or_none(_first_present(kwargs, "start_date")),
                knocked_in=bool(_first_present(kwargs, "knocked_in") or False),
                ki_observation=_str_or_none(
                    _first_present(kwargs, "barrier_config.ki_observation", "ki_observation")
                ),
                payoff_kind=_str_or_none(_first_present(kwargs, "payoff_kind")),
                legacy_kwargs=kwargs,
            )
        )
        _replace_snowball_schedule(
            session,
            position.id,
            _schedule_records(
                kwargs,
                "barrier_config.ko_observation_schedule",
                "ko_observation_schedule",
                "observation_schedule",
            ),
        )

    position.kwargs_migrated_at = utcnow()


def reset_position_term_rows(session: Session, position_id: int) -> None:
    """Remove compatibility term mirrors before rewriting a position product."""
    for model in (
        OptionCoreTerm,
        SingleBarrierTerm,
        DoubleBarrierTerm,
        SharkfinTerm,
        AsianTerm,
        AsianAveragingDate,
        SnowballTerm,
        SnowballKoSchedule,
        PositionBarrierState,
    ):
        session.query(model).filter_by(position_id=position_id).delete(
            synchronize_session=False
        )


def backfill_position_term_rows(
    session: Session,
    *,
    limit: int | None = None,
) -> int:
    query = session.query(Position).filter(Position.kwargs_migrated_at.is_(None))
    if limit is not None:
        query = query.limit(limit)
    rows = query.all()
    for position in rows:
        upsert_position_term_rows(session, position)
    session.flush()
    for position in rows:
        refresh_position_barrier_state(session, position_id=position.id)
    return len(rows)


def refresh_position_barrier_state(
    session: Session,
    *,
    portfolio_id: int | None = None,
    position_id: int | None = None,
    as_of: date | None = None,
) -> int:
    """Refresh cached nearest barrier metadata from structured term tables."""
    session.flush()
    anchor = as_of or date.today()
    query = session.query(Position)
    if portfolio_id is not None:
        query = query.filter(Position.portfolio_id == portfolio_id)
    if position_id is not None:
        query = query.filter(Position.id == position_id)

    refreshed = 0
    for position in query.all():
        candidate = _nearest_barrier_candidate(session, position.id, as_of=anchor)
        if candidate is None:
            continue
        kind, level, barrier_date = candidate
        days = (barrier_date - anchor).days if barrier_date is not None else None
        session.merge(
            PositionBarrierState(
                position_id=position.id,
                nearest_barrier_kind=kind,
                nearest_barrier_level=level,
                nearest_barrier_date=barrier_date,
                days_to_nearest=days,
                last_computed_at=utcnow(),
            )
        )
        refreshed += 1
    session.flush()
    return refreshed


def query_positions_near_barrier(
    session: Session,
    *,
    portfolio_id: int,
    spot: dict[str, float],
    within_pct: float,
    status: str | None = "open",
) -> list[dict[str, Any]]:
    """Return positions whose cached barrier state is within ``within_pct`` of spot."""
    rows: list[dict[str, Any]] = []
    base_filters = [Position.portfolio_id == portfolio_id]
    if status is not None:
        base_filters.append(Position.status == status)

    for position, state in (
        session.query(Position, PositionBarrierState)
        .join(PositionBarrierState, PositionBarrierState.position_id == Position.id)
        .filter(*base_filters)
        .all()
    ):
        rows.extend(
            _near_rows(
                position,
                spot=spot,
                barriers=[(state.nearest_barrier_kind or "barrier", state.nearest_barrier_level)],
                within_pct=within_pct,
                barrier_date=state.nearest_barrier_date,
                days_to_nearest=state.days_to_nearest,
            )
        )
    return sorted(rows, key=lambda row: (row["distance_pct"], row["position_id"]))


def query_snowball_ko_from_spot(
    session: Session,
    *,
    portfolio_id: int,
    spot: dict[str, float] | None = None,
    within_pct: float = 5.0,
    status: str | None = "open",
    as_of: date | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Return Snowball positions whose next KO is within pct distance of spot.

    The query resolves view portfolios through portfolio membership, mirrors
    Snowball product kwargs into structured term tables as needed, then joins
    stored market inputs/snapshots for current spot.
    """
    if within_pct <= 0:
        raise ValueError("within_pct must be positive")
    if limit > 1000:
        raise ValueError("query_snowball_ko_from_spot limit must be <= 1000")
    portfolio = session.get(Portfolio, portfolio_id)
    if portfolio is None:
        return {
            "source": "structured_position_terms",
            "portfolio_id": portfolio_id,
            "error": f"Portfolio {portfolio_id} not found",
            "positions": [],
            "returned_count": 0,
        }
    anchor = as_of or date.today()
    supplied_spot = spot or {}
    resolved = sorted(resolve_positions(portfolio, session), key=lambda p: p.id)
    snowballs = [
        position
        for position in resolved
        if _normalize_product_type(position.product_type) in {"snowball", "snowballoption"}
        and (status is None or position.status == status)
    ]
    for position in snowballs:
        upsert_position_term_rows(session, position)
    session.flush()

    rows: list[dict[str, Any]] = []
    missing_spot_count = 0
    missing_ko_schedule_count = 0
    for position in snowballs:
        next_ko = _next_snowball_ko(session, position.id, as_of=anchor)
        if next_ko is None:
            missing_ko_schedule_count += 1
            continue
        spot_value, spot_source = _resolve_spot(
            session, position, supplied_spot, as_of=anchor
        )
        if spot_value is None or spot_value == 0:
            missing_spot_count += 1
            continue
        ko_pct_from_spot = (next_ko.ko_level - spot_value) / abs(spot_value) * 100.0
        distance_pct = abs(ko_pct_from_spot)
        if distance_pct > within_pct:
            continue
        snowball = session.get(SnowballTerm, position.id)
        rows.append(
            {
                "position_id": position.id,
                "portfolio_id": position.portfolio_id,
                "requested_portfolio_id": portfolio_id,
                "source_trade_id": position.source_trade_id,
                "underlying": position.underlying,
                "product_type": position.product_type,
                "quantity": position.quantity,
                "next_ko_date": next_ko.observation_date.isoformat(),
                "next_ko_level": next_ko.ko_level,
                "spot": spot_value,
                "spot_source": spot_source,
                "ko_pct_from_spot": round(ko_pct_from_spot, 6),
                "distance_pct": round(distance_pct, 6),
                "days_to_ko": (next_ko.observation_date - anchor).days,
                "initial_price": snowball.initial_price if snowball else None,
                "ki_barrier": snowball.ki_barrier if snowball else None,
                "coupon": snowball.coupon if snowball else None,
            }
        )
    rows.sort(key=lambda row: (row["distance_pct"], row["next_ko_date"], row["position_id"]))
    return {
        "source": "structured_position_terms",
        "portfolio_id": portfolio_id,
        "portfolio_kind": portfolio.kind,
        "as_of": anchor.isoformat(),
        "within_pct": within_pct,
        "resolved_position_count": len(resolved),
        "checked_snowball_count": len(snowballs),
        "missing_spot_count": missing_spot_count,
        "missing_ko_schedule_count": missing_ko_schedule_count,
        "returned_count": len(rows[:limit]),
        "positions": rows[:limit],
    }


def query_positions(
    session: Session,
    *,
    portfolio_id: int,
    filters: list[dict[str, Any]],
    select: list[str],
    order_by: tuple[str, str] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    if limit > 1000:
        raise ValueError("query_positions limit must be <= 1000")
    column_map = _query_column_map()
    selected = [_column_for(column_map, col) for col in select]
    query = _structured_query_base(session).filter(Position.portfolio_id == portfolio_id)
    for item in filters:
        query = query.filter(
            _comparison(
                _column_for(column_map, str(item.get("col") or "")),
                str(item.get("op") or ""),
                item.get("value"),
            )
        )
    if order_by is not None:
        order_col, direction = order_by
        expression = _column_for(column_map, order_col)
        query = query.order_by(expression.desc() if direction.lower() == "desc" else expression.asc())
    raw_rows = query.with_entities(*selected).limit(limit).all()
    return [
        {key: _json_value(value) for key, value in zip(select, row, strict=False)}
        for row in raw_rows
    ]


def get_option_core_terms(session: Session, position_ids: list[int]) -> list[dict[str, Any]]:
    return [
        _model_dict(row)
        for row in session.query(OptionCoreTerm)
        .filter(OptionCoreTerm.position_id.in_(position_ids))
        .order_by(OptionCoreTerm.position_id)
        .all()
    ]


def get_barrier_terms(session: Session, position_ids: list[int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in (
        session.query(SingleBarrierTerm)
        .filter(SingleBarrierTerm.position_id.in_(position_ids))
        .order_by(SingleBarrierTerm.position_id)
        .all()
    ):
        payload = _model_dict(row)
        payload["barrier_family"] = "single"
        rows.append(payload)
    for row in (
        session.query(DoubleBarrierTerm)
        .filter(DoubleBarrierTerm.position_id.in_(position_ids))
        .order_by(DoubleBarrierTerm.position_id)
        .all()
    ):
        payload = _model_dict(row)
        payload["barrier_family"] = "double"
        rows.append(payload)
    return rows


def _next_snowball_ko(
    session: Session,
    position_id: int,
    *,
    as_of: date,
) -> SnowballKoSchedule | None:
    return (
        session.query(SnowballKoSchedule)
        .filter(
            SnowballKoSchedule.position_id == position_id,
            SnowballKoSchedule.observation_date >= as_of,
        )
        .order_by(SnowballKoSchedule.observation_date)
        .first()
    )


def _resolve_spot(
    session: Session,
    position: Position,
    supplied_spot: dict[str, float],
    *,
    as_of: date | None = None,
) -> tuple[float | None, str | None]:
    candidates = _symbol_candidates(position.underlying)
    for symbol in candidates:
        value = _float_or_none(supplied_spot.get(symbol))
        if value is not None:
            return value, f"supplied:{symbol}"
    # Spot is observation-only — read the quote store by the position's
    # underlying instrument (instrument-unification T8).
    quote = _latest_quote_for_position(session, position, as_of)
    if quote is not None:
        return float(quote.price), f"market_quote:{quote.id}"
    snapshot = _latest_market_snapshot(session, candidates)
    if snapshot is not None:
        value = _float_or_none((snapshot.data or {}).get("spot"))
        if value is not None:
            return value, f"market_snapshots:{snapshot.id}"
    return None, None


def _latest_quote_for_position(
    session: Session, position: Position, as_of: date | None = None
):
    underlying_id = getattr(position, "underlying_id", None)
    if underlying_id is None:
        return None
    from ..quotes import latest_quote

    cutoff = datetime.combine(as_of, datetime.max.time()) if as_of else datetime.utcnow()
    return latest_quote(session, underlying_id, as_of=cutoff)


def _latest_market_snapshot(
    session: Session,
    symbol_candidates: list[str],
) -> MarketSnapshot | None:
    return (
        session.query(MarketSnapshot)
        .filter(MarketSnapshot.symbol.in_(symbol_candidates))
        .order_by(MarketSnapshot.valuation_date.desc(), MarketSnapshot.id.desc())
        .first()
    )


def _symbol_candidates(symbol: str | None) -> list[str]:
    raw = str(symbol or "").strip()
    if not raw:
        return []
    out = [raw]
    if "." in raw:
        out.append(raw.split(".", 1)[0])
    upper = raw.upper()
    if upper != raw:
        out.append(upper)
    return list(dict.fromkeys(out))


def get_sharkfin_terms(session: Session, position_ids: list[int]) -> list[dict[str, Any]]:
    return [
        _model_dict(row)
        for row in session.query(SharkfinTerm)
        .filter(SharkfinTerm.position_id.in_(position_ids))
        .order_by(SharkfinTerm.position_id)
        .all()
    ]


def get_asian_schedule(session: Session, position_id: int) -> list[dict[str, Any]]:
    return [
        _model_dict(row)
        for row in session.query(AsianAveragingDate)
        .filter_by(position_id=position_id)
        .order_by(AsianAveragingDate.sequence)
        .all()
    ]


def get_snowball_terms(session: Session, position_ids: list[int]) -> list[dict[str, Any]]:
    return [
        _model_dict(row)
        for row in session.query(SnowballTerm)
        .filter(SnowballTerm.position_id.in_(position_ids))
        .order_by(SnowballTerm.position_id)
        .all()
    ]


def get_snowball_ko_schedule(
    session: Session,
    position_id: int,
    *,
    from_date: date | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    query = session.query(SnowballKoSchedule).filter_by(position_id=position_id)
    if from_date is not None:
        query = query.filter(SnowballKoSchedule.observation_date >= from_date)
    return [
        _model_dict(row)
        for row in query.order_by(SnowballKoSchedule.observation_date).limit(limit).all()
    ]


def _near_rows(
    position: Position,
    *,
    spot: dict[str, float],
    barriers: list[tuple[str, float | None]],
    within_pct: float,
    barrier_date: date | None = None,
    days_to_nearest: int | None = None,
) -> list[dict[str, Any]]:
    current = _float_or_none(spot.get(position.underlying))
    if current is None:
        return []
    rows = []
    for source, barrier in barriers:
        if barrier is None or barrier == 0:
            continue
        distance = abs(current - barrier) / abs(barrier) * 100.0
        if distance <= within_pct:
            rows.append(
                {
                    "position_id": position.id,
                    "portfolio_id": position.portfolio_id,
                    "source_trade_id": position.source_trade_id,
                    "underlying": position.underlying,
                    "product_type": position.product_type,
                    "barrier_source": source,
                    "barrier": barrier,
                    "spot": current,
                    "distance_pct": round(distance, 6),
                    "nearest_barrier_date": (
                        barrier_date.isoformat() if barrier_date is not None else None
                    ),
                    "days_to_nearest": days_to_nearest,
                }
            )
    return rows


def _nearest_barrier_candidate(
    session: Session,
    position_id: int,
    *,
    as_of: date,
) -> tuple[str, float, date | None] | None:
    dated: list[tuple[str, float, date]] = []
    undated: list[tuple[str, float, date | None]] = []

    snowball = session.get(SnowballTerm, position_id)
    if snowball is not None and snowball.ki_barrier is not None:
        undated.append(("KI", snowball.ki_barrier, None))

    for row in (
        session.query(SnowballKoSchedule)
        .filter(
            SnowballKoSchedule.position_id == position_id,
            SnowballKoSchedule.observation_date >= as_of,
        )
        .order_by(SnowballKoSchedule.observation_date)
        .all()
    ):
        dated.append(("KO", row.ko_level, row.observation_date))

    single = session.get(SingleBarrierTerm, position_id)
    if single is not None and single.barrier is not None:
        undated.append((single.barrier_type or "single_barrier", single.barrier, None))

    double = session.get(DoubleBarrierTerm, position_id)
    if double is not None:
        if double.upper_barrier is not None:
            undated.append(("UB", double.upper_barrier, None))
        if double.lower_barrier is not None:
            undated.append(("LB", double.lower_barrier, None))

    if dated:
        return min(dated, key=lambda item: (item[2] - as_of).days)
    if undated:
        return undated[0]
    return None


def _structured_query_base(session: Session):
    return (
        session.query(Position)
        .outerjoin(OptionCoreTerm, OptionCoreTerm.position_id == Position.id)
        .outerjoin(SingleBarrierTerm, SingleBarrierTerm.position_id == Position.id)
        .outerjoin(DoubleBarrierTerm, DoubleBarrierTerm.position_id == Position.id)
        .outerjoin(SharkfinTerm, SharkfinTerm.position_id == Position.id)
        .outerjoin(AsianTerm, AsianTerm.position_id == Position.id)
        .outerjoin(SnowballTerm, SnowballTerm.position_id == Position.id)
        .outerjoin(PositionBarrierState, PositionBarrierState.position_id == Position.id)
    )


def _query_column_map() -> dict[str, Any]:
    mapping = {
        "id": Position.id,
        "position_id": Position.id,
        "portfolio_id": Position.portfolio_id,
        "underlying": Position.underlying,
        "product_type": Position.product_type,
        "quantity": Position.quantity,
        "status": Position.status,
        "source_trade_id": Position.source_trade_id,
        "positions.id": Position.id,
        "positions.portfolio_id": Position.portfolio_id,
        "positions.underlying": Position.underlying,
        "positions.product_type": Position.product_type,
        "positions.quantity": Position.quantity,
        "positions.status": Position.status,
        "positions.source_trade_id": Position.source_trade_id,
    }
    mapping.update(_prefixed_columns("option_core", OptionCoreTerm))
    mapping.update(_prefixed_columns("single_barrier", SingleBarrierTerm))
    mapping.update(_prefixed_columns("double_barrier", DoubleBarrierTerm))
    mapping.update(_prefixed_columns("sharkfin", SharkfinTerm))
    mapping.update(_prefixed_columns("asian", AsianTerm))
    mapping.update(_prefixed_columns("snowball", SnowballTerm))
    mapping.update(_prefixed_columns("barrier_state", PositionBarrierState))
    return mapping


def _prefixed_columns(prefix: str, model: Any) -> dict[str, Any]:
    return {
        f"{prefix}.{column.name}": getattr(model, column.name)
        for column in model.__table__.columns
    }


def _column_for(column_map: dict[str, Any], name: str) -> Any:
    try:
        return column_map[name]
    except KeyError as exc:
        raise ValueError(f"Unknown query_positions column: {name}") from exc


def _comparison(column: Any, op: str, value: Any) -> Any:
    if op == "=":
        return column == value
    if op == "!=":
        return column != value
    if op == "<":
        return column < value
    if op == "<=":
        return column <= value
    if op == ">":
        return column > value
    if op == ">=":
        return column >= value
    if op == "in":
        if not isinstance(value, list):
            raise ValueError("query_positions 'in' operator requires list value")
        return column.in_(value)
    raise ValueError(f"Unsupported query_positions operator: {op}")


def _model_dict(row: Any) -> dict[str, Any]:
    return {
        column.name: _json_value(getattr(row, column.name))
        for column in row.__table__.columns
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _replace_snowball_schedule(
    session: Session,
    position_id: int,
    records: list[dict[str, Any]],
) -> None:
    session.query(SnowballKoSchedule).filter_by(position_id=position_id).delete()
    for index, record in enumerate(records, start=1):
        obs_date = _date_or_none(
            record.get("observation_date") or record.get("date")
        )
        ko_level = _float_or_none(
            record.get("ko_level") or record.get("barrier") or record.get("return_rate")
        )
        if obs_date is None or ko_level is None:
            continue
        session.add(
            SnowballKoSchedule(
                position_id=position_id,
                observation_date=obs_date,
                ko_level=ko_level,
                sequence=index,
            )
        )


def _replace_asian_schedule(
    session: Session,
    position_id: int,
    records: list[dict[str, Any]],
) -> None:
    session.query(AsianAveragingDate).filter_by(position_id=position_id).delete()
    for index, record in enumerate(records, start=1):
        obs_date = _date_or_none(
            record.get("observation_date") or record.get("date")
        )
        if obs_date is None:
            continue
        session.add(
            AsianAveragingDate(
                position_id=position_id,
                observation_date=obs_date,
                sequence=index,
                weight=_float_or_none(record.get("weight")),
            )
        )


def _normalize_product_type(value: str | None) -> str:
    return str(value or "").replace("_", "").replace(" ", "").lower()


def _nested_value(data: dict[str, Any], path: str) -> Any:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _first_present(data: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        value = _nested_value(data, path)
        if value is not None:
            return value
    return None


def _schedule_records(data: dict[str, Any], *paths: str) -> list[dict[str, Any]]:
    for path in paths:
        value = _nested_value(data, path)
        if isinstance(value, dict):
            records = value.get("records")
            if isinstance(records, list):
                return [row for row in records if isinstance(row, dict)]
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _date_or_none(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None

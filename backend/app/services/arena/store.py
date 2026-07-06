"""Arena persistence store.

Functions operate on a SQLAlchemy session and raise no HTTP exceptions —
callers handle HTTP mapping.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ArenaRun, ArenaMatch


def _derive_card(bd: dict, workflow_id: str) -> tuple[dict | None, str | None]:
    """Derive an ability card from a stored score_breakdown, or (None, reason).

    Fail-honest: requires non-empty objective.axes, an explicit numeric
    diagnosis.counts_detail.tool_calls, and a loadable workflow (for par). Any
    missing → uncarded with a machine reason, never a fabricated card. This is
    the SINGLE stored-breakdown→card path — used by both leaderboard and
    _match_to_dict so the board and the drilldown can never disagree.
    """
    from app.services.arena import scoring
    axes = (bd.get("objective") or {}).get("axes") or {}
    if not axes:
        return None, "legacy_no_axes"
    tc = ((bd.get("diagnosis") or {}).get("counts_detail") or {}).get("tool_calls")
    if not isinstance(tc, (int, float)) or isinstance(tc, bool):
        return None, "missing_tool_count"
    try:
        from app.golden_workflows.registry import get_workflow
        par = scoring.designed_par(get_workflow(workflow_id))
    except Exception:
        return None, "workflow_unavailable"
    judged = (bd.get("judge") or {}).get("judged_score")
    return scoring.card_from_axes(axes, int(tc), par, judged=judged), None


def create_run(
    session: Session,
    workflow_ids: list[str],
    model_ids: list[str],
    weights: dict | None = None,
) -> int:
    """Insert a new ArenaRun in 'queued' status; return its id."""
    run = ArenaRun(
        status="queued",
        workflow_ids=workflow_ids,
        model_ids=model_ids,
        weights=weights,
    )
    session.add(run)
    session.flush()
    return run.id


def record_match(
    session: Session,
    run_id: int,
    workflow_id: str,
    model_id: str,
    *,
    objective_score: float | None,
    judged_score: float | None,
    total_score: float | None,
    judge_missing: bool,
    config: dict,
    transcript_path: str | None,
    status: str,
    error: str | None = None,
    score_breakdown: dict | None = None,
) -> int:
    """Upsert an ArenaMatch row; return its id."""
    existing = (
        session.query(ArenaMatch)
        .filter_by(run_id=run_id, workflow_id=workflow_id, model_id=model_id)
        .one_or_none()
    )
    if existing is not None:
        existing.status = status
        existing.objective_score = objective_score
        existing.judged_score = judged_score
        existing.total_score = total_score
        existing.judge_missing = judge_missing
        existing.config = config
        existing.transcript_path = transcript_path
        existing.error = error
        existing.score_breakdown = score_breakdown
        session.flush()
        return existing.id

    match = ArenaMatch(
        run_id=run_id,
        workflow_id=workflow_id,
        model_id=model_id,
        status=status,
        objective_score=objective_score,
        judged_score=judged_score,
        total_score=total_score,
        judge_missing=judge_missing,
        config=config,
        transcript_path=transcript_path,
        error=error,
        score_breakdown=score_breakdown,
    )
    session.add(match)
    session.flush()
    return match.id


def set_run_status(
    session: Session,
    run_id: int,
    status: str,
    error: str | None = None,
) -> None:
    """Update an ArenaRun's status (and optionally error)."""
    run = session.get(ArenaRun, run_id)
    if run is None:
        return
    run.status = status
    if error is not None:
        run.error = error
    session.flush()


def get_run(session: Session, run_id: int) -> dict | None:
    """Return a run dict with its matches, or None if not found."""
    run = session.get(ArenaRun, run_id)
    if run is None:
        return None
    return _run_to_dict(run)


def list_runs(
    session: Session,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Return (rows, total) ordered by created_at desc."""
    total = session.query(ArenaRun).count()
    rows = (
        session.query(ArenaRun)
        .order_by(ArenaRun.created_at.desc(), ArenaRun.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return [_run_to_dict(r) for r in rows], total


def get_match_transcript_path(session: Session, match_id: int) -> str | None:
    """Return the transcript_path for a match, or None."""
    match = session.get(ArenaMatch, match_id)
    if match is None:
        return None
    return match.transcript_path


def leaderboard(
    session: Session,
    *,
    run_id: int | None = None,
    tag: str | None = None,
) -> list[dict]:
    """Return leaderboard rows for the latest completed run (or specified run_id).

    Each row: {model_id, mean_total, mean_objective, match_count, invalid_count}.
    Means and match_count consider only scored matches (status=='scored');
    'invalid' matches (infra-blank routes) are excluded from means but counted
    in invalid_count so degraded routes stay visible instead of silently
    vanishing. A model with only invalid matches is listed with match_count 0
    and None means.
    Ordered by mean_total desc (None last), mean_objective desc, model_id asc.
    If tag is given, only matches for workflows with that tag are included.
    Returns [] on no completed run or no scored/invalid matches.
    """
    if run_id is None:
        # Find the latest completed run
        run = (
            session.query(ArenaRun)
            .filter(ArenaRun.status == "completed")
            .order_by(ArenaRun.created_at.desc(), ArenaRun.id.desc())
            .first()
        )
        if run is None:
            return []
        run_id = run.id

    # Fetch scored + invalid matches for this run (invalids only feed counts)
    matches = (
        session.query(ArenaMatch)
        .filter(
            ArenaMatch.run_id == run_id,
            ArenaMatch.status.in_(["scored", "invalid"]),
        )
        .all()
    )

    if not matches:
        return []

    # Apply tag filter
    if tag is not None:
        from app.golden_workflows.registry import get_workflow
        filtered = []
        for m in matches:
            try:
                wf = get_workflow(m.workflow_id)
                if tag in wf.tags:
                    filtered.append(m)
            except Exception:
                pass
        matches = filtered
        if not matches:
            return []

    # Aggregate per model. Ranking is by the DETERMINISTIC objective axis only
    # (spec D5 — no blended total); subjective is advisory (mean ± stdev + mode).
    from collections import defaultdict
    from app.services.arena import scoring

    model_objectives: dict[str, list[float]] = defaultdict(list)
    model_subjectives: dict[str, list[float]] = defaultdict(list)
    model_sub_stdevs: dict[str, list[float]] = defaultdict(list)
    model_sub_modes: dict[str, list[str]] = defaultdict(list)
    model_axes: dict[str, dict[str, dict[str, int]]] = defaultdict(dict)
    scored_counts: dict[str, int] = defaultdict(int)
    invalid_counts: dict[str, int] = defaultdict(int)
    # Ability card (spec B): per-match OVR + stats, via the fail-honest guard.
    model_ovrs: dict[str, list[int]] = defaultdict(list)
    model_stat_lists: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(list))

    for m in matches:
        if m.status == "invalid":
            invalid_counts[m.model_id] += 1
            continue
        scored_counts[m.model_id] += 1
        if m.objective_score is not None:
            model_objectives[m.model_id].append(m.objective_score)
        bd = m.score_breakdown or {}
        judge = bd.get("judge") or {}
        # Effective subjective score: prefer the breakdown's judge block, else fall
        # back to the top-level column (oldest rows persisted the score only there).
        eff_judged = judge.get("judged_score")
        if eff_judged is None:
            eff_judged = m.judged_score
        if eff_judged is not None:
            model_subjectives[m.model_id].append(eff_judged)
        if judge.get("judged_stdev") is not None:
            model_sub_stdevs[m.model_id].append(judge["judged_stdev"])
        # Per-row provenance (spec D8/D9): explicit mode wins; else a row that has a
        # subjective score is an inferred legacy "panel"; else a judge-missing row is
        # "missing"; else the jury was never intended → "disabled". This keeps legacy
        # successful juries (no stored mode) from reading as outages, and keeps a
        # deliberate opt-out distinct from a failed opt-in jury.
        explicit_mode = bd.get("subjective_mode") or judge.get("subjective_mode")
        if explicit_mode:
            row_mode = explicit_mode
        elif eff_judged is not None:
            row_mode = "panel"
        elif m.judge_missing:
            row_mode = "missing"
        else:
            row_mode = "disabled"
        model_sub_modes[m.model_id].append(row_mode)
        axes = (bd.get("objective") or {}).get("axes") or {}
        for ax, tally in axes.items():
            slot = model_axes[m.model_id].setdefault(ax, {"passed": 0, "total": 0})
            slot["passed"] += tally.get("passed", 0)
            slot["total"] += tally.get("total", 0)

        # Ability card, via the shared fail-honest guard. Uncarded rows (no axes /
        # no tool count / unloadable workflow) contribute NOTHING to card_mean.
        card, _reason = _derive_card(bd, m.workflow_id)
        if card is not None:
            model_ovrs[m.model_id].append(card["ovr"])
            for stat, val in card["stats"].items():
                model_stat_lists[m.model_id][stat].append(val)

    def _agg_mode(modes: list[str]) -> str:
        # Worst-visibility-wins so a degraded/failed jury-on row never collapses into
        # a clean "disabled" board (spec D8): missing > self_consistency > panel >
        # disabled. Empty (no scored rows) → "disabled".
        if not modes:
            return "disabled"
        for level in ("missing", "self_consistency", "panel", "disabled"):
            if level in modes:
                return level
        return modes[0]

    rows = []
    for model_id in set(scored_counts) | set(invalid_counts):
        objectives = model_objectives.get(model_id, [])
        subs = model_subjectives.get(model_id, [])
        stdevs = model_sub_stdevs.get(model_id, [])
        ovrs = model_ovrs.get(model_id, [])
        carded_count = len(ovrs)
        scored_count = scored_counts.get(model_id, 0)
        # A card_mean is trustworthy for ranking ONLY when EVERY scored match is
        # carded. A partially-carded model (some matches uncarded — schema drift,
        # missing tool counts) would otherwise rank by the derivable subset while
        # its uncarded matches vanish from the OVR denominator, letting a partial
        # sample outrank a fully-carded row. Partial rows drop to the objective
        # fallback group; carded_count/match_count surface the coverage.
        fully_carded = carded_count > 0 and carded_count == scored_count
        card_mean = (
            {"ovr": round(sum(ovrs) / len(ovrs)),
             **{stat: round(sum(vals) / len(vals))
                for stat, vals in model_stat_lists[model_id].items()}}
            if fully_carded else None)
        rows.append({
            "model_id": model_id,
            "mean_objective": (round(sum(objectives) / len(objectives), 1)
                               if objectives else None),
            "card_mean": card_mean,
            "carded_count": carded_count,
            "subjective_mean": round(sum(subs) / len(subs), 1) if subs else None,
            "subjective_stdev": round(sum(stdevs) / len(stdevs), 1) if stdevs else None,
            "subjective_mode": _agg_mode(model_sub_modes.get(model_id, [])),
            "match_count": scored_counts.get(model_id, 0),
            "invalid_count": invalid_counts.get(model_id, 0),
            "_obj_tb": scoring.objective_tiebreak_key(dict(model_axes.get(model_id, {}))),
        })

    # Rank by OVR mean (spec B5 — numbers-first card): CARDED rows first, ordered
    # by OVR then card stat-priority tie-break (GRD→ADH→SYN→EFF→PRC); UNCARDED rows
    # after, ordered by the legacy objective axis (mean_objective + sub-axis
    # tie-break) so an all-legacy board (runs #1-#9, no stored axes) keeps its old
    # objective ranking instead of collapsing to a single shared rank. model_id is
    # a DISPLAY-only stabilizer and never breaks a rank (shared on exact ties).
    def _order_key(r) -> tuple:
        cm = r["card_mean"]
        if cm is not None:
            return (0, -cm["ovr"], scoring.card_tiebreak_key(cm))
        return (1, -(r["mean_objective"] or 0.0), r["_obj_tb"])

    rows.sort(key=lambda r: (_order_key(r), r["model_id"]))
    rank = 0
    prev_key: object = object()
    for i, r in enumerate(rows):
        key = _order_key(r)
        if key != prev_key:
            rank = i + 1  # standard competition ranking (1, 1, 3)
            prev_key = key
        r["rank"] = rank
        del r["_obj_tb"]
    return rows


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _serialized_breakdown(m: ArenaMatch) -> dict | None:
    """Return the stored breakdown with an ability card synthesized on read when
    absent (spec B8 — derive, no migration). A stored card (new write-time rows)
    passes through untouched; a legacy row with axes gets a card derived via the
    same shared guard as the leaderboard; an uncarded row carries card:null +
    card_reason so board and drilldown never disagree."""
    bd = m.score_breakdown
    if bd is None:
        return None
    if bd.get("card") is not None:
        return bd  # already carded at write time (new rows)
    out = dict(bd)
    card, reason = _derive_card(bd, m.workflow_id)
    out["card"] = card
    if card is None:
        out["card_reason"] = reason
    return out


def _match_to_dict(m: ArenaMatch) -> dict:
    return {
        "id": m.id,
        "run_id": m.run_id,
        "workflow_id": m.workflow_id,
        "model_id": m.model_id,
        "status": m.status,
        "objective_score": m.objective_score,
        "judged_score": m.judged_score,
        "total_score": m.total_score,
        "judge_missing": m.judge_missing,
        "config": m.config,
        "score_breakdown": _serialized_breakdown(m),
        "transcript_path": m.transcript_path,
        "error": m.error,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _run_to_dict(run: ArenaRun) -> dict:
    return {
        "id": run.id,
        "status": run.status,
        "workflow_ids": run.workflow_ids,
        "model_ids": run.model_ids,
        "weights": run.weights,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "matches": [_match_to_dict(m) for m in run.matches],
    }

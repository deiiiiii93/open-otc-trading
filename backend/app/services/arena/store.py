"""Arena persistence store.

Functions operate on a SQLAlchemy session and raise no HTTP exceptions —
callers handle HTTP mapping.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models import AgentThread, ArenaRun, ArenaMatch


def _match_truncation(bd: dict) -> dict | None:
    """The output-budget truncation block for one match, or None if unmeasured.

    Read from the breakdown's TOP level, which is where the multi-trial fold
    lifts it to. Reading ``diagnosis`` instead would find nothing on any wrapped
    match — the fold does not lift ``diagnosis``, and every match is wrapped,
    including single-trial ones. That is the same trap that blanked the
    drilldown for 222 of 297 stored matches.

    None means the match predates the instrument, which is NOT the same claim as
    ``calls: 0``. Every board through #114 ran capped and really did truncate;
    reporting a confident zero for them would assert the opposite.
    """
    block = bd.get("truncation") if isinstance(bd, dict) else None
    return block if isinstance(block, dict) else None


def _agg_truncation(blocks: list[dict | None]) -> dict | None:
    """Aggregate per-match truncation blocks for one leaderboard contestant."""
    measured = [b for b in blocks if isinstance(b, dict)]
    if not measured:
        return None
    return {
        "calls": sum(int(b.get("calls") or 0) for b in measured),
        "severed_tool_calls": sum(
            int(b.get("severed_tool_calls") or 0) for b in measured),
        "matches_affected": sum(1 for b in measured if b.get("calls")),
        "matches_measured": len(measured),
        "matches_total": len(blocks),
    }


def _match_malformed(bd: dict) -> dict | None:
    """Malformed-tool-call block for one match, or None if unmeasured.

    Same top-level read and same absence rule as ``_match_truncation``: None
    means the match predates the instrument, never "measured and clean". Run
    #122's arm A really did malform 100% of one contestant's tool calls, so a
    confident zero on pre-instrument boards would assert the opposite.
    """
    block = bd.get("malformed") if isinstance(bd, dict) else None
    return block if isinstance(block, dict) else None


def _agg_malformed(blocks: list[dict | None]) -> dict | None:
    """Aggregate per-match malformed blocks for one leaderboard contestant."""
    measured = [b for b in blocks if isinstance(b, dict)]
    if not measured:
        return None
    return {
        "calls": sum(int(b.get("calls") or 0) for b in measured),
        "matches_affected": sum(1 for b in measured if b.get("calls")),
        "matches_measured": len(measured),
        "matches_total": len(blocks),
    }


def _derive_card(bd: dict, workflow_id: str,
                 frozen: dict | None = None) -> tuple[dict | None, str | None]:
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
    # Fail-honest: a missing, non-numeric, boolean, or NEGATIVE count is impossible/corrupt
    # evidence — leave the row uncarded rather than let it rank on a fabricated number.
    if not isinstance(tc, (int, float)) or isinstance(tc, bool) or tc < 0:
        return None, "missing_tool_count"
    # The par in force when the match was STAMPED wins over today's manifest: a
    # card is derived on read, so without the frozen value a par edit would
    # silently re-card every historical match (provenance, 2026-09-25).
    frozen = frozen or {}
    if isinstance(frozen.get("par_tool_calls"), int):
        par = frozen["par_tool_calls"]
        calibrated = bool(frozen.get("par_calibrated"))
    else:
        try:
            from app.golden_workflows.registry import get_workflow
            wf = get_workflow(workflow_id)
            par = scoring.designed_par(wf)
            calibrated = scoring.par_calibrated(wf)
        except Exception:
            return None, "workflow_unavailable"
    judged = (bd.get("judge") or {}).get("judged_score")
    return scoring.card_from_axes(axes, int(tc), par, judged=judged,
                                  par_calibrated=calibrated), None


def create_run(
    session: Session,
    workflow_ids: list[str],
    model_ids: list[str],
    weights: dict | None = None,
    trials: int = 1,
    reasoning_efforts: dict | None = None,
    max_output_tokens: dict | None = None,
    provenance: dict | None = None,
) -> int:
    """Insert a new ArenaRun in 'queued' status; return its id."""
    run = ArenaRun(
        provenance=provenance,
        status="queued",
        workflow_ids=workflow_ids,
        model_ids=model_ids,
        weights=weights,
        trials=trials,
        reasoning_efforts=reasoning_efforts or None,
        max_output_tokens=max_output_tokens or None,
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
    reasoning_effort: str | None = None,
    max_output_tokens: int | None = None,
) -> int:
    """Upsert an ArenaMatch row; return its id.

    ``reasoning_effort`` and ``max_output_tokens`` are part of the contestant
    key: the same model at two efforts, or at two output budgets, is two rows
    that rank against each other. ``None`` means the run did not pin that axis,
    stored as ``''`` and ``0`` respectively.
    """
    # '' / 0 are the stored forms of "unpinned"; both columns are part of the
    # contestant key, so the lookup MUST filter on them or the second arm upserts
    # over the first and the board reports a completed run with an arm silently
    # missing.
    effort_key = reasoning_effort or ""
    budget_key = int(max_output_tokens or 0)
    existing = (
        session.query(ArenaMatch)
        .filter_by(run_id=run_id, workflow_id=workflow_id, model_id=model_id,
                   reasoning_effort=effort_key, max_output_tokens=budget_key)
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
        reasoning_effort=effort_key,
        max_output_tokens=budget_key,
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


def merge_runs(session: Session, source_run_ids: list[int]) -> int:
    """Fold several single-trial runs into ONE multi-trial aggregate run.

    Each ``(workflow_id, model_id)`` pair's scored matches across the source runs
    become the TRIALS of one aggregate match (``n_trials``), which the ability card
    then scores on read with a trial-dispersion CON. Trials are ordered by the
    position of their source run in ``source_run_ids`` (stable), then by match id.

    Non-destructive: a NEW completed run is created and its id returned; the sources
    are untouched. Only ``status == 'scored'`` matches participate (invalid/failed
    routes are not trials). A pair present in just one source becomes a single-trial
    aggregate (CON greys, as for any lone trial). Raises ``ValueError`` on fewer than
    two source runs or when no scored matches are found.
    """
    from collections import defaultdict

    from app.services.arena import scoring

    ordered = list(dict.fromkeys(source_run_ids))     # de-dup, preserve order
    if len(ordered) < 2:
        raise ValueError("merge_runs needs at least two distinct source run ids")

    matches = (
        session.query(ArenaMatch)
        .filter(ArenaMatch.run_id.in_(ordered), ArenaMatch.status == "scored")
        .all()
    )
    if not matches:
        raise ValueError(f"no scored matches found in runs {ordered}")

    pos = {rid: i for i, rid in enumerate(ordered)}
    # Effort AND output budget are part of the contestant key, so both are part
    # of the fold key: two efforts — or two budgets — of one model fold into two
    # merged rows that rank against each other. Leaving budget out would produce
    # exactly the row this whole change exists to prevent: one average across
    # regimes that runs #118/#119 measured 16.4 objective points apart.
    groups: dict[
        tuple[str, str, str | None, int | None], list[ArenaMatch]] = defaultdict(list)
    for m in matches:
        groups[(m.workflow_id, m.model_id, m.reasoning_effort or None,
                m.max_output_tokens or None)].append(m)
    for ms in groups.values():
        ms.sort(key=lambda m: (pos[m.run_id], m.id))

    # A fold across manifest versions averages two different tests into one
    # number. Refuse it whenever both sides are stamped; an unstamped (pre-
    # 2026-09-25) match cannot be checked, so it is recorded as unknown instead.
    manifest_of: dict[str, dict] = {}
    for (wf, _md, _ef, _bd), ms in groups.items():
        for m in ms:
            prov = (m.config or {}).get("provenance") or {}
            sha = prov.get("sha256")
            if not sha:
                continue
            seen = manifest_of.setdefault(wf, prov)
            if seen.get("sha256") != sha:
                raise ValueError(
                    f"cannot merge {wf}: runs used different manifests "
                    f"(v{seen.get('manifest_version')} {seen['sha256'][:12]} vs "
                    f"v{prov.get('manifest_version')} {sha[:12]})")

    # A cross-effort merge no longer needs refusing: two efforts of one model land
    # in DIFFERENT groups, so they become two merged rows that rank against each
    # other rather than one row averaging two operating regimes. That refusal
    # (effort moves tool-call count, hence EFF, and trial dispersion, hence CON)
    # protected against a fold that is now structurally impossible — and keeping
    # it would make merge arbitrarily stricter than launch, which happily puts
    # both arms in one run.
    workflow_ids = sorted({wf for wf, _md, _ef, _bd in groups})
    model_ids = sorted({md for _wf, md, _ef, _bd in groups})
    # Per-model LIST of the arms present, so the merged run states every regime it
    # contains rather than one of them.
    merged_efforts: dict[str, list[str]] = {}
    merged_budgets: dict[str, list[int]] = {}
    for (_wf, model_id, effort, budget) in groups:
        if effort:
            arms = merged_efforts.setdefault(model_id, [])
            if effort not in arms:
                arms.append(effort)
        if budget:
            budget_arms = merged_budgets.setdefault(model_id, [])
            if budget not in budget_arms:
                budget_arms.append(budget)
    merged_efforts = {k: sorted(v) for k, v in merged_efforts.items()}
    merged_budgets = {k: sorted(v) for k, v in merged_budgets.items()}
    apps = sorted({(m.config or {}).get("provenance", {}).get("app")
                   for ms in groups.values() for m in ms} - {None})
    new_run_id = create_run(
        session, workflow_ids, model_ids, reasoning_efforts=merged_efforts or None,
        max_output_tokens=merged_budgets or None,
        provenance={"merged_from": ordered, "apps": apps,
                    "manifests": manifest_of} if (apps or manifest_of) else None,
    )

    for (workflow_id, model_id, effort, budget), ms in groups.items():
        trials: list[dict] = []
        for m in ms:
            bd = m.score_breakdown
            if not bd:
                continue
            if bd.get("n_trials") and isinstance(bd.get("aggregate"), list):
                # Source match is ITSELF a multi-trial aggregate (e.g. a New Run
                # with trials>1) — flatten its per-trial entries into the trial
                # list rather than nesting the wrapper, so the flattened trials
                # keep their own diagnosis/card and the merged aggregate re-cards.
                trials.extend(dict(t) for t in bd["aggregate"])
            else:
                trials.append(dict(bd))
        if not trials:
            continue
        aggregate = scoring.fold_trial_breakdowns(trials)
        record_match(
            session, new_run_id, workflow_id, model_id,
            objective_score=aggregate["objective_score"], judged_score=None,
            total_score=aggregate["objective_score"], judge_missing=False,
            # Carry the arm's effort in BOTH the column and config, like
            # _record_pair does — a merged row that cannot state its regime
            # cannot defend its EFF or CON either.
            config={"merged_from": ordered, "reasoning_effort": effort,
                    "max_output_tokens": budget,
                    **({"provenance": manifest_of[workflow_id]}
                       if workflow_id in manifest_of else {})},
            transcript_path=None,
            status="scored", score_breakdown=aggregate,
            reasoning_effort=effort,
            max_output_tokens=budget,
        )

    set_run_status(session, new_run_id, "completed")
    return new_run_id


def delete_runs(session: Session, run_ids: list[int]) -> dict:
    """Hard-delete arena runs (+cascade matches). DB only — no filesystem.

    Returns deleted ids (only those that existed), the transcript_paths of their
    matches (for the caller to unlink), and the match_count removed. Nulls any
    agent_threads.arena_run_id pointing at a deleted run so no thread dangles.
    """
    ids = list(dict.fromkeys(run_ids))
    deleted: list[int] = []
    paths: list[str] = []
    match_count = 0
    for rid in ids:
        run = session.get(ArenaRun, rid)
        if run is None:
            continue
        for m in run.matches:
            match_count += 1
            if m.transcript_path:
                paths.append(m.transcript_path)
        session.delete(run)          # cascade="all, delete-orphan" drops matches
        deleted.append(rid)
    if deleted:
        session.execute(
            sa.update(AgentThread)
            .where(AgentThread.arena_run_id.in_(deleted))
            .values(arena_run_id=None),
            execution_options={"synchronize_session": "fetch"},
        )
    return {"deleted_run_ids": deleted, "transcript_paths": paths, "match_count": match_count}


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
    return [_run_to_dict(r, include_matches=False) for r in rows], total


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

    # Keyed by ARM — (model_id, effort) — not by model. Collapsing the two into
    # one row would average two operating regimes, which is exactly what
    # merge_runs refuses to do: effort measurably moves tool-call count (~22%
    # fewer at high than low, runs #107/#108) and therefore EFF.
    # (Every key below is an ARM: `(model_id, effort_or_None)`.)
    model_objectives: dict[tuple[str, str | None, int | None], list[float]] = defaultdict(list)
    model_subjectives: dict[tuple[str, str | None, int | None], list[float]] = defaultdict(list)
    model_sub_stdevs: dict[tuple[str, str | None, int | None], list[float]] = defaultdict(list)
    model_sub_modes: dict[tuple[str, str | None, int | None], list[str]] = defaultdict(list)
    model_axes: dict[
        tuple[str, str | None, int | None], dict[str, dict[str, int]]] = defaultdict(dict)
    scored_counts: dict[tuple[str, str | None, int | None], int] = defaultdict(int)
    invalid_counts: dict[tuple[str, str | None, int | None], int] = defaultdict(int)
    # Ability card (spec B): per-match FINAL OVR (CON already baked in for
    # multi-trial rows) + base OVR + con + stats, via the aggregate-aware guard.
    model_final_ovrs: dict[tuple[str, str | None, int | None], list[int]] = defaultdict(list)
    model_base_ovrs: dict[tuple[str, str | None, int | None], list[int]] = defaultdict(list)
    model_cons: dict[tuple[str, str | None, int | None], list[int]] = defaultdict(list)
    model_stat_lists: dict[
        tuple[str, str | None, int | None], dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(list))
    # Output-budget truncation per contestant. Kept as a list of per-match
    # blocks (None for a match recorded before the instrument existed) so the
    # row can report coverage rather than silently averaging measured and
    # unmeasured matches into one confident number.
    model_truncations: dict[
        tuple[str, str | None, int | None], list[dict | None]] = defaultdict(list)
    model_malformed: dict[
        tuple[str, str | None, int | None], list[dict | None]] = defaultdict(list)

    for m in matches:
        # A contestant is (model, effort, budget) — the same model at two
        # efforts, or at two output budgets, ranks as two rows rather than one
        # averaged row. Budget earns its place by measurement: runs #118/#119
        # differ by 16.4 mean objective on the budget alone.
        key = (m.model_id, m.reasoning_effort or None, m.max_output_tokens or None)
        if m.status == "invalid":
            invalid_counts[key] += 1
            continue
        scored_counts[key] += 1
        if m.objective_score is not None:
            model_objectives[key].append(m.objective_score)
        bd = m.score_breakdown or {}
        model_truncations[key].append(_match_truncation(bd))
        model_malformed[key].append(_match_malformed(bd))
        judge = bd.get("judge") or {}
        # Effective subjective score: prefer the breakdown's judge block, else fall
        # back to the top-level column (oldest rows persisted the score only there).
        eff_judged = judge.get("judged_score")
        if eff_judged is None:
            eff_judged = m.judged_score
        if eff_judged is not None:
            model_subjectives[key].append(eff_judged)
        if judge.get("judged_stdev") is not None:
            model_sub_stdevs[key].append(judge["judged_stdev"])
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
        model_sub_modes[key].append(row_mode)
        axes = (bd.get("objective") or {}).get("axes") or {}
        for ax, tally in axes.items():
            slot = model_axes[key].setdefault(ax, {"passed": 0, "total": 0})
            slot["passed"] += tally.get("passed", 0)
            slot["total"] += tally.get("total", 0)

        # Ability card, via the aggregate-aware guard (same path as the drilldown).
        # A multi-trial row's card already has trial-dispersion CON baked into its
        # final OVR; a single-trial row's con is None. Uncarded rows (no axes / no
        # tool count / unloadable workflow / partial trial coverage) contribute
        # NOTHING to card_mean.
        card, _reason = _match_card(bd, m.workflow_id,
                                    frozen=(m.config or {}).get("provenance"))
        if card is not None:
            model_final_ovrs[key].append(card["ovr"])
            model_base_ovrs[key].append(card.get("base_ovr", card["ovr"]))
            if card.get("con") is not None:
                model_cons[key].append(card["con"])
            for stat, val in card["stats"].items():
                model_stat_lists[key][stat].append(val)

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
    for key in set(scored_counts) | set(invalid_counts):
        model_id, effort, budget = key
        objectives = model_objectives.get(key, [])
        subs = model_subjectives.get(key, [])
        stdevs = model_sub_stdevs.get(key, [])
        final_ovrs = model_final_ovrs.get(key, [])
        base_ovrs = model_base_ovrs.get(key, [])
        cons = model_cons.get(key, [])
        carded_count = len(final_ovrs)
        scored_count = scored_counts.get(key, 0)
        # A card_mean is trustworthy for ranking ONLY when EVERY scored match is
        # carded. A partially-carded model (some matches uncarded — schema drift,
        # missing tool counts) would otherwise rank by the derivable subset while
        # its uncarded matches vanish from the OVR denominator, letting a partial
        # sample outrank a fully-carded row. Partial rows drop to the objective
        # fallback group; carded_count/match_count surface the coverage.
        fully_carded = carded_count > 0 and carded_count == scored_count
        # Headline OVR is the mean of the per-match FINAL OVRs — each match's CON
        # (trial dispersion) is already discounted into its own final OVR by
        # _match_card, exactly as the drilldown shows, so the board is the mean of
        # what a user drills into. card_mean.con is the mean of the measurable
        # per-match cons (single-trial matches have none); None when no match has one.
        card_mean = (
            {"ovr": round(sum(final_ovrs) / len(final_ovrs)),
             "base_ovr": round(sum(base_ovrs) / len(base_ovrs)),
             "con": round(sum(cons) / len(cons)) if cons else None,
             **{stat: round(sum(vals) / len(vals))
                for stat, vals in model_stat_lists[key].items()}}
            if fully_carded else None)
        rows.append({
            "model_id": model_id,
            # The arm this row scores; None on either axis = the run did not pin
            # that one. Both must be surfaced or two arms of a model are
            # indistinguishable: same model, workflow, status and radar.
            "reasoning_effort": effort,
            "max_output_tokens": budget,
            "mean_objective": (round(sum(objectives) / len(objectives), 1)
                               if objectives else None),
            "card_mean": card_mean,
            "carded_count": carded_count,
            "subjective_mean": round(sum(subs) / len(subs), 1) if subs else None,
            "subjective_stdev": round(sum(stdevs) / len(stdevs), 1) if stdevs else None,
            "subjective_mode": _agg_mode(model_sub_modes.get(key, [])),
            "match_count": scored_counts.get(key, 0),
            "invalid_count": invalid_counts.get(key, 0),
            # None = this contestant's matches predate the instrument, NOT
            # "measured and clean". Boards before run #115 really did truncate.
            "truncation": _agg_truncation(model_truncations.get(key, [])),
            "malformed": _agg_malformed(model_malformed.get(key, [])),
            "_obj_tb": scoring.objective_tiebreak_key(dict(model_axes.get(key, {}))),
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

    rows.sort(key=lambda r: (_order_key(r), r["model_id"],
                             r["reasoning_effort"] or "",
                             r["max_output_tokens"] or 0))
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


def _match_card(bd: dict, workflow_id: str, *,
                allow_stored_fallback: bool = False,
                frozen: dict | None = None) -> tuple[dict | None, str | None]:
    """The ability card for a whole match — AGGREGATE-AWARE.

    A multi-trial match (``aggregate``) is carded from its TRIALS: each trial is
    carded via _derive_card, then scoring.aggregate_card_from_trials averages the
    stats and folds trial-dispersion CON into the OVR. If ANY trial is uncarded the
    whole aggregate is uncarded (``partial_trial_cards``) — a biased subset must not
    masquerade as the full sample.

    A single-trial match is RECOMPUTED from its axes; a persisted `card` never
    overrides recomputable evidence. ``allow_stored_fallback`` splits the two callers:
    the leaderboard (ranking) passes False, so a card that CANNOT be recomputed
    (missing tool count, empty axes, unloadable workflow) stays uncarded and never
    ranks on an unverifiable number; the drilldown (presentation) passes True, so the
    write-time card (spec B7) still renders for those non-recomputable rows. For a
    recomputable row both callers get the identical derived card, so board and
    drilldown agree wherever a row actually ranks."""
    from app.services.arena import scoring
    trials = bd.get("aggregate")
    n_trials = bd.get("n_trials")
    has_n = isinstance(n_trials, int) and not isinstance(n_trials, bool)
    # Coverage guard, BEFORE the aggregate branch: a row that DECLARES n_trials must
    # carry an aggregate list of exactly that length. A missing / empty / non-list /
    # short / long aggregate under a declared n_trials is a partial write or retry
    # remnant — it must NOT fall through to the single-card path and card from the
    # top-level representative objective as if the multi-trial match completed.
    if isinstance(n_trials, int) and not isinstance(n_trials, bool) and n_trials > 1 \
            and (not isinstance(trials, list) or len(trials) != n_trials):
        return None, "partial_trial_coverage"
    if isinstance(trials, list) and trials:
        if has_n and n_trials != len(trials):        # e.g. n_trials 1 but 2 stored
            return None, "partial_trial_coverage"
        trial_cards: list[dict] = []
        for t in trials:
            if not isinstance(t, dict):              # null / primitive placeholder
                return None, "invalid_trial_shape"   # (fail closed, never crash)
            tc, _reason = _derive_card(t, workflow_id, frozen)
            if tc is None and allow_stored_fallback:
                # Mirror the single-trial fallback exactly (same two
                # conditions, same "presentation only, never ranking"
                # posture — the leaderboard passes allow_stored_fallback=
                # False so it never takes this path): a trial whose card
                # can't be recomputed (missing tool count, unloadable
                # workflow, or legacy empty axes) still renders its
                # write-time card in the drilldown as long as it carries a
                # genuine `objective` block.
                stored = t.get("card")
                objective = t.get("objective")
                if isinstance(stored, dict) and isinstance(objective, dict) \
                        and "axes" in objective:
                    tc = dict(stored)
            if tc is None:
                return None, "partial_trial_cards"
            trial_cards.append(tc)
        return scoring.aggregate_card_from_trials(trial_cards), None
    # Single-trial match. RECOMPUTE from the stored evidence first — never let a
    # persisted `card` number override recomputable axes (a stale/corrupted card whose
    # OVR disagrees with its axes must not rank on the public board).
    derived, reason = _derive_card(bd, workflow_id, frozen)
    if derived is not None:
        return derived, None
    # Recompute impossible. The stored card is a PRESENTATION-ONLY fallback (drilldown,
    # allow_stored_fallback=True) — never a ranking input — and only when the row still
    # carries a genuine `objective` block (a bare card with nothing behind it is always
    # refused). The leaderboard passes allow_stored_fallback=False, so these rows stay
    # uncarded and fall back to the objective ranking rather than an unverifiable OVR.
    if allow_stored_fallback:
        stored = bd.get("card")
        objective = bd.get("objective")
        if isinstance(stored, dict) and isinstance(objective, dict) and "axes" in objective:
            return dict(stored), None
    return None, reason


def _serialized_breakdown(m: ArenaMatch) -> dict | None:
    """Return the stored breakdown with ability cards synthesized on read (spec B8 —
    derive, no migration). The top-level ``card`` comes from the aggregate-aware
    _match_card; per-trial cards are attached to ``aggregate`` for the drilldown tabs
    ONLY when that aggregate card is non-null — i.e. coverage validated and every
    trial carded. A partial / uncarded aggregate exposes NO scored trial cards, so the
    coverage guard can't be bypassed via the per-trial path (the drilldown then shows
    an explicit incomplete state instead of trustworthy-looking tabs). Uncarded rows
    carry card:null + card_reason so board and drilldown never disagree."""
    bd = m.score_breakdown
    if bd is None:
        return None
    out = dict(bd)
    # Drilldown is presentation: a non-recomputable single-match row may fall back to
    # its stored write-time card (the leaderboard, ranking, does not — see _match_card).
    frozen = (m.config or {}).get("provenance")
    card, reason = _match_card(bd, m.workflow_id, allow_stored_fallback=True,
                               frozen=frozen)
    out["card"] = card
    if card is None:
        out["card_reason"] = reason
    trials = bd.get("aggregate")
    # Attach per-trial cards for the drilldown tabs ONLY when the aggregate itself is
    # carded (card non-null ⇒ full coverage AND every trial derivable). If the
    # aggregate is uncarded — a count mismatch (partial_trial_coverage) OR a complete
    # set where some trial can't be scored (partial_trial_cards) — NO per-trial card
    # is exposed, so a cardable subset can't masquerade as trustworthy scored OVRs.
    # (For partial_trial_cards the frontend still shows the per-trial objective detail,
    # just without OVR/card affordances; for a count mismatch it shows an incomplete
    # state.)
    if isinstance(trials, list) and trials:
        if card is not None:
            out["aggregate"] = [{**t, "card": _derive_card(t, m.workflow_id, frozen)[0]}
                                for t in trials]
        else:
            # Uncarded aggregate: STRIP any pre-stored per-trial card/reason so a
            # refused aggregate can never expose trustworthy-looking per-trial OVRs
            # (the server never adds them here, but a producer might have persisted
            # them). Non-dict entries (malformed) pass through untouched — the UI
            # shows an incomplete state for those, not tabs. Detail without card
            # affordances is what the UI renders for the merely-uncarded case.
            out["aggregate"] = [
                {k: v for k, v in t.items() if k not in ("card", "card_reason")}
                if isinstance(t, dict) else t
                for t in trials]
    return out


def _match_to_dict(m: ArenaMatch) -> dict:
    return {
        "id": m.id,
        "run_id": m.run_id,
        "workflow_id": m.workflow_id,
        "model_id": m.model_id,
        # None, not '', at the dict boundary: callers reason about "unpinned" as
        # an absence, and '' would read as a real level in JSON.
        "reasoning_effort": m.reasoning_effort or None,
        # 0 is the stored "unpinned"; None at the dict boundary, because callers
        # reason about unpinned as an ABSENCE and 0 would read as a real budget.
        "max_output_tokens": m.max_output_tokens or None,
        "status": m.status,
        "objective_score": m.objective_score,
        "judged_score": m.judged_score,
        "total_score": m.total_score,
        "judge_missing": m.judge_missing,
        "config": m.config,
        "score_breakdown": _serialized_breakdown(m),
        # Hoisted beside the score, not left inside the breakdown: the match
        # CELL renders this, and a cell that has to walk into score_breakdown
        # would show nothing for exactly the wrapped matches that carry it.
        "truncation": _match_truncation(m.score_breakdown or {}),
        "malformed": _match_malformed(m.score_breakdown or {}),
        "transcript_path": m.transcript_path,
        "error": m.error,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _run_to_dict(run: ArenaRun, *, include_matches: bool = True) -> dict:
    """Serialize a run.

    `include_matches` is off for the LIST: `matches` is a lazy relationship, so
    building it costs a query per run and then derives an ability card per match
    (each loading a workflow) — work the runs list discards, since it projects
    only id/status/created_at/workflow_ids/model_ids and the arm maps. The
    drilldown (`get_run`) is the caller that genuinely needs them.
    """
    out = {
        "id": run.id,
        "status": run.status,
        "workflow_ids": run.workflow_ids,
        "model_ids": run.model_ids,
        "weights": run.weights,
        "trials": run.trials,
        # Always the LIST form at the dict boundary, so every consumer (execute,
        # --resume, the router's RunSummary) sees one shape. Legacy rows stored a
        # bare scalar; that is read, never migrated.
        "reasoning_efforts": {
            slug: ([levels] if isinstance(levels, str) else list(levels))
            for slug, levels in (run.reasoning_efforts or {}).items()
        },
        # Same normalise-on-read rule as reasoning_efforts: always the LIST form
        # at the dict boundary so execute, --resume and RunSummary see one shape.
        "max_output_tokens": {
            slug: ([budgets] if not isinstance(budgets, list) else list(budgets))
            for slug, budgets in (run.max_output_tokens or {}).items()
        },
        "provenance": run.provenance,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }
    if include_matches:
        out["matches"] = [_match_to_dict(m) for m in run.matches]
    return out

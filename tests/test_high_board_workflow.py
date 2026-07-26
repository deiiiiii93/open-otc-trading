"""High-Board Portfolio Review — flagship-parity workflow tests.

Covers the consume-only design (high_board READS a seeded governed RiskRun; it is
not authorized to dispatch run_batch_pricing): priceability of the harvest seed,
determinism/harvest drift guard, manifest load + axes, full-marks replay, and the
negative-scorer discrimination suite.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from app.golden_workflows.registry import get_workflow_bundle

WF_ID = "high-board-portfolio-review-day"
_DEFN = (Path(__file__).resolve().parents[1]
         / "backend/app/golden_workflows/definitions")


# ---------------------------------------------------------------------------
# Task 1/2 — priceability + determinism (harvest-time machinery)
# ---------------------------------------------------------------------------
def test_high_board_positions_all_price(offline_session_factory, block_network):
    """Every seeded desk position must price at harvest time (greeks_ok &
    pricing_ok), and spot must come from the SEEDED quote (100), not the
    synthetic default or a network fetch (block_network would raise on a fetch)."""
    from app.golden_workflows.determinism import (
        seed_workflow, drive_producers, HIGH_BOARD_ID,
    )
    with offline_session_factory() as s:
        ids = seed_workflow(s, HIGH_BOARD_ID)
        payloads = drive_producers(s, ids, workflow_id=HIGH_BOARD_ID)
    rows = payloads["risk"]["positions"]
    assert rows, "risk run produced no positions"
    for r in rows:
        assert r.get("greeks_ok") is True and r.get("pricing_ok") is True, r
        assert abs(float(r["spot"]) - 100.0) < 1e-9, r


def test_seeded_risk_run_metrics_match_harvest(offline_session_factory, block_network):
    """The seeded governed RiskRun.metrics (read by the live match) must equal a
    fresh producer drive on the grounding numbers — so the seeded blob can't drift
    away from what the workflow grounds against."""
    from app.golden_workflows.assertions import _dig
    from app.golden_workflows.determinism import (
        seed_workflow, drive_producers, HIGH_BOARD_ID,
    )
    loaded = get_workflow_bundle(WF_ID)
    seeded = next(r for r in loaded.fixtures.seed["risk_runs"]
                  if r["alias"] == "gov")["metrics"]
    with offline_session_factory() as s:
        fresh = drive_producers(s, seed_workflow(s, HIGH_BOARD_ID),
                                workflow_id=HIGH_BOARD_ID)["risk"]
    for path in ("positions[underlying=NVDA].delta", "totals.market_value"):
        ok_s, sv = _dig(seeded, path)
        ok_f, fv = _dig(fresh, path)
        assert ok_s and ok_f, path
        assert abs(float(sv) - float(fv)) < 1e-6, (path, sv, fv)


def test_seeded_risk_run_does_not_accumulate_across_matches(
    offline_session_factory, block_network
):
    """The consume-only seed adds a governed RiskRun bound to the arena portfolio.
    The arena runner re-seeds every match, so that run MUST be reclaimed by the
    next match's ``_purge_seeded_portfolios`` — else arena rows accumulate in the
    live DB. Regression guard: bind-to-arena-portfolio is what makes the purge's
    ``delete ... where portfolio_id in pids`` catch it; a future seed that sets a
    NULL/foreign portfolio_id would silently orphan the run and fail here.
    """
    from app import database, models
    from app.golden_workflows.fixtures import apply_seed
    from app.services.arena import runner
    from app.services.arena.runner import ARENA_PORTFOLIO_TAG, ARENA_PROFILE_MARKER
    from sqlalchemy import func, select

    loaded = get_workflow_bundle(WF_ID)

    def _seed_and_mark(session):
        # Mirror runner.run_match's post-seed arena-ownership marking.
        ids = apply_seed(loaded.fixtures, session)
        for p in session.query(models.Portfolio).filter(
            models.Portfolio.id.in_(ids.get("portfolios", {}).values())
        ):
            p.tags = sorted({*(p.tags or []), ARENA_PORTFOLIO_TAG})
        for pr in session.query(models.PricingParameterProfile).filter(
            models.PricingParameterProfile.id.in_(ids.get("pricing_profiles", {}).values())
        ):
            pr.summary = {**(pr.summary or {}), ARENA_PROFILE_MARKER: True}
        session.commit()

    def _count(session, model):
        return session.scalar(select(func.count()).select_from(model))

    with offline_session_factory():  # configures a fresh isolated DB
        # Match 1: first seed (nothing to purge yet).
        with database.SessionLocal() as s:
            _seed_and_mark(s)
            assert _count(s, models.RiskRun) == 1

        # Matches 2..4: each opens a fresh session, purges the prior match, re-seeds.
        for _ in range(3):
            with database.SessionLocal() as s:
                runner._purge_seeded_portfolios(s, loaded.fixtures)
                s.commit()
                # The prior match's governed run + arena profile are fully reclaimed.
                assert _count(s, models.RiskRun) == 0
                assert _count(s, models.PricingParameterProfile) == 0
                _seed_and_mark(s)

        # No accumulation: exactly one governed run and one profile survive.
        with database.SessionLocal() as s:
            assert _count(s, models.RiskRun) == 1
            assert _count(s, models.PricingParameterProfile) == 1


def test_seeded_governance_report_artifact_is_readable_and_states_the_truth_value(
    offline_session_factory, block_network, tmp_path, monkeypatch
):
    """The seeded report's artifact_paths must resolve to a REAL file.

    Regression guard for the dangling-pointer defect (live trace 2026-07-26): the row
    was seeded but no file was ever written, so `get_report` handed the agent a path
    that `read_file` could not open. Models then burned calls globbing for it, surfaced
    unrelated real governance reports, called `get_report` again on one of them, and the
    `report_type` assertion — which reads only the LAST matching result — failed a
    selection they had already made correctly.

    Also pins that the prior-quarter valuation lives ONLY in the artifact body, never in
    `result_payload`. That is what makes step 7 immune to brute force: a model cannot
    reach the number by enumerating `get_report(1..n)`, it has to open the RIGHT report.
    """
    import dataclasses

    from app import database
    from app.config import get_settings
    from app.golden_workflows.fixtures import apply_seed
    from app.tools._shaping import normalize_artifact_paths

    loaded = get_workflow_bundle(WF_ID)
    seeded = [r for r in loaded.fixtures.seed.get("reports", [])]
    assert seeded, "fixture must seed a prior governance report"
    row = seeded[0]

    truth = "211.34"
    body = (row.get("artifact_bodies") or {}).get("markdown", "")
    assert truth in body, "the graded value must be stated in the artifact body"
    assert truth not in json.dumps(row.get("result_payload") or {}), (
        "the value must NOT be in result_payload — otherwise get_report alone reveals "
        "it and enumeration can score without reading the right report"
    )

    art_dir = tmp_path / "artifacts"
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: dataclasses.replace(get_settings(), artifact_dir=art_dir),
    )
    with offline_session_factory():
        with database.SessionLocal() as s:
            apply_seed(loaded.fixtures, s)

    # The agent resolves /artifacts/<basename> against the mounted artifact_dir.
    virtual = normalize_artifact_paths(row["artifact_paths"])["markdown"]
    assert virtual == "/artifacts/q3-governance.md"
    on_disk = art_dir / Path(virtual).name
    assert on_disk.exists(), f"seeded artifact was never written to {on_disk}"
    assert truth in on_disk.read_text()


def test_foreign_workflow_arena_fixture_is_reclaimed(
    offline_session_factory, block_network
):
    """A DIFFERENT workflow's arena-seeded book must be purged too.

    Regression guard for the Run #58 contamination: the purge used to intersect the
    arena tag with THIS bundle's fixture names, so the trader-rfq fixture "Arena
    Trader Desk" survived every high-board match and competed for the phrase "the
    desk control book" against "Desk Control Book". It also holds an NVDA position,
    so a model resolving the wrong book got a plausible well-formed delta and was
    silently graded wrong. A real (untagged) desk book must still be untouchable.
    """
    from app import database, models
    from app.services.arena import runner
    from app.services.arena.runner import ARENA_PORTFOLIO_TAG

    loaded = get_workflow_bundle(WF_ID)

    with offline_session_factory():
        with database.SessionLocal() as s:
            foreign = models.Portfolio(
                name="Arena Trader Desk", kind="container", tags=[ARENA_PORTFOLIO_TAG]
            )
            real = models.Portfolio(name="Real Desk Book", kind="container", tags=[])
            s.add_all([foreign, real])
            s.commit()
            foreign_id, real_id = foreign.id, real.id

        with database.SessionLocal() as s:
            runner._purge_seeded_portfolios(s, loaded.fixtures)
            s.commit()

        with database.SessionLocal() as s:
            assert s.get(models.Portfolio, foreign_id) is None, (
                "a foreign workflow's arena fixture survived the purge — it will "
                "shadow this workflow's book on name resolution"
            )
            assert s.get(models.Portfolio, real_id) is not None, (
                "purge must never delete an untagged real desk book"
            )


def test_purging_a_view_with_a_valuation_run_does_not_trip_a_foreign_key(
    offline_session_factory, block_network
):
    """The dependent sweep must reach RUN-CHILD rows, which key off a run's PK.

    ``position_valuation_results.valuation_run_id`` → ``position_valuation_runs.id``,
    and that child table has NO ``portfolio_id``. Its ``position_id`` only helps when
    the purged portfolio OWNS positions — so the gap is invisible for a container and
    fatal for a VIEW, which owns none. Models create VIEWS here, so purging one used
    to die on ``FOREIGN KEY constraint failed``: the orphans were unreachable in both
    directions (never selected, and uncleanable if they had been).
    """
    from app import database, models
    from app.services.arena.runner import _delete_portfolios_with_dependents

    with offline_session_factory():
        with database.SessionLocal() as s:
            owner = models.Portfolio(name="Owner Book", kind="container", tags=[])
            view = models.Portfolio(name="A View", kind="view", tags=[])
            s.add_all([owner, view])
            s.flush()
            pos = models.Position(
                portfolio_id=owner.id,
                product_type="SnowballOption",
                underlying="NVDA",
                quantity=1.0,
            )
            s.add(pos)
            s.flush()
            # A valuation run bound to the VIEW, whose results reference a position the
            # view does NOT own — precisely the shape that blocked the delete.
            run = models.PositionValuationRun(portfolio_id=view.id, status="completed")
            s.add(run)
            s.flush()
            s.add(
                models.PositionValuationResult(
                    valuation_run_id=run.id, position_id=pos.id, ok=True
                )
            )
            s.commit()
            view_id, run_id, owner_id, pos_id = view.id, run.id, owner.id, pos.id

        with database.SessionLocal() as s:
            _delete_portfolios_with_dependents(s, [view_id])  # must not raise
            s.commit()

        with database.SessionLocal() as s:
            assert s.get(models.Portfolio, view_id) is None
            assert s.get(models.PositionValuationRun, run_id) is None
            assert (
                s.query(models.PositionValuationResult)
                .filter_by(valuation_run_id=run_id)
                .count()
                == 0
            ), "run-child rows leaked, leaving dangling references"
            # The purge owns only the view: the other book and its position survive.
            assert s.get(models.Portfolio, owner_id) is not None
            assert s.get(models.Position, pos_id) is not None


def test_model_created_portfolio_is_purged_but_baseline_rows_survive(
    offline_session_factory, block_network, monkeypatch
):
    """``_purge_match_portfolios`` must reclaim ONLY portfolios this match minted.

    Run #58 leaked 23 orphan "Board Review" views because a model-created portfolio
    carries no arena tag and a model-chosen name, so the tag+name-scoped purge could
    not see it. Since the workflows resolve books BY NAME, each leak made the next
    match's resolution harder — an ordering bias. Ownership needs BOTH the trace
    evidence and the id > baseline guard, so a pre-existing row the agent merely read
    is never deleted.
    """
    from app import database, models
    from app.services.arena import runner

    with offline_session_factory():
        with database.SessionLocal() as s:
            pre = models.Portfolio(name="Pre-existing Book", kind="container", tags=[])
            s.add(pre)
            s.commit()
            baseline = pre.id

        with database.SessionLocal() as s:
            mine = models.Portfolio(name="Board Review", kind="view", tags=[])
            s.add(mine)
            s.commit()
            mine_id = mine.id

        # The agent's create_portfolio spans reported BOTH ids (it read the
        # pre-existing book and created its own); only the post-baseline one is ours.
        monkeypatch.setattr(
            runner, "collect_portfolio_ids_created", lambda _tid: {baseline, mine_id}
        )
        runner._purge_match_portfolios(thread_id=1, portfolio_id_baseline=baseline)

        with database.SessionLocal() as s:
            assert s.get(models.Portfolio, mine_id) is None, (
                "model-created portfolio leaked — it will pollute the next match's "
                "name resolution permanently (the next baseline is taken above it)"
            )
            assert s.get(models.Portfolio, baseline) is not None, (
                "a row at/below the baseline is NOT this match's to delete"
            )


# ---------------------------------------------------------------------------
# Task 3/4 — manifest load, axes, full-marks replay
# ---------------------------------------------------------------------------
def test_high_board_bundle_loads():
    loaded = get_workflow_bundle(WF_ID)
    wf = loaded.workflow
    assert wf.persona == "high_board"
    assert [s.expected_skill for s in wf.steps] == [
        "portfolio-maintenance", "portfolio-maintenance", None,
        None, None, None, "display-report", "generate-report",
    ]
    assert len(wf.steps) == 8
    assert wf.par_tool_calls is not None
    for s in wf.steps:
        assert s.replay in loaded.fixtures.replay


def test_high_board_is_par_calibrated():
    from app.services.arena import scoring
    assert scoring.par_calibrated(get_workflow_bundle(WF_ID).workflow)


def test_high_board_has_four_axes():
    """All four axes must be scored — asserted over the checks scoring actually
    EMITS, not just over manifest assertions.

    The old form scanned ``step.assertions`` only, so it silently depended on a
    ``skill_routed`` assertion being present to supply the procedural axis. Those
    were duplicates of the ``expected_skill`` checks and were removed in the
    2026-07-25 validity audit; procedural coverage comes from the
    ``expected_skill`` / ``expected_tools`` checks, which are emitted by the scorer
    rather than declared as Assertion objects. Reading the real breakdown keeps
    this test honest about what lands on the card.
    """
    from app.golden_workflows.transcript import transcript_from_replay
    from app.services.arena.scoring import objective_breakdown
    loaded = get_workflow_bundle(WF_ID)
    axes = objective_breakdown(transcript_from_replay(loaded), loaded)["axes"]
    assert {"grounding", "synthesis", "adherence", "procedural"} <= set(axes)
    assert all(v["total"] > 0 for v in axes.values())


def test_high_board_golden_replay_scores_full_marks():
    from app.golden_workflows.transcript import transcript_from_replay
    from app.services.arena.scoring import objective_score
    loaded = get_workflow_bundle(WF_ID)
    score, passed, total = objective_score(transcript_from_replay(loaded), loaded)
    assert passed == total, f"{passed}/{total} — not full marks"
    assert score == 100.0


# ---------------------------------------------------------------------------
# Task 5 — truth drift guard + negative scorer suite (discrimination)
# ---------------------------------------------------------------------------
from app.golden_workflows.transcript import transcript_from_replay  # noqa: E402
from app.services.arena.scoring import objective_score  # noqa: E402


def _truth():
    return json.loads((_DEFN / "high-board-portfolio-review-day.truth.json").read_text())


def test_high_board_grounding_matches_truth_file():
    """Manifest grounding values must equal truth.json (drift guard)."""
    t = _truth()
    wf = get_workflow_bundle(WF_ID).workflow
    vals = {}
    for st in wf.steps:
        for a in st.assertions:
            if getattr(a, "type", None) == "answer_field_quotes":
                vals[a.field] = a.value
    assert vals["nvda_delta"] == t["nvda_governed_delta"]["value"]
    assert vals["governed_valuation"] == t["desk_portfolio_valuation"]["value"]


def _total():
    l = get_workflow_bundle(WF_ID)
    _s, _p, total = objective_score(transcript_from_replay(l), l)
    return total


def _score_mutated(mutate):
    b = copy.deepcopy(get_workflow_bundle(WF_ID))
    mutate(b.fixtures.replay)
    _s, passed, _t = objective_score(transcript_from_replay(b), b)
    return passed


def test_neg_wrong_container_kind():
    def m(rp):
        rp["step-1-membership"].tool_results[0]["content"]["data"]["kind"] = "view"
    assert _score_mutated(m) < _total()


def test_neg_wrong_portfolio_total_count():
    def m(rp):
        rp["step-3-count"].tool_results[0]["content"]["portfolio_total_count"] = 4
    assert _score_mutated(m) < _total()


def test_neg_wrong_nvda_delta():
    def m(rp):
        # push far beyond rel_tol on the completed-run result — the primary bind
        for pos in rp["step-4-read-risk"].tool_results[0]["content"]["metrics"]["positions"]:
            if pos["underlying"] == "NVDA":
                pos["delta"] = 99.0
        rp["step-4-read-risk"].ai["tool_calls"][1]["args"]["answer"]["nvda_delta"] = 99.0
    assert _score_mutated(m) < _total()


def test_neg_trap_over_claim():
    def m(rp):
        ans = rp["step-6-trap"].ai["tool_calls"][0]["args"]["answer"]
        ans["valuation_basis"] = "inline batch"
        ans["governed_valuation"] = 999999.0
    assert _score_mutated(m) < _total()


def test_neg_board_facing_over_claim():
    def m(rp):
        # board report drops the governed valuation digits (uses the inline figure)
        rp["step-8-generate"].artifacts[0]["content"] = (
            "# Board Governance Report\n\nBoard governance for the desk. "
            "The view holds 2 Snowballs. Certified valuation: 999999 (inline batch).")
    assert _score_mutated(m) < _total()


def test_neg_synthesis_boilerplate():
    def m(rp):
        rp["step-8-generate"].artifacts[0]["content"] = "# board governance"
    assert _score_mutated(m) < _total()


def test_trap_prose_contradiction_is_out_of_objective_scope():
    """DELIBERATE BOUNDARY (Codex plan finding): the deterministic objective axis
    grades the authoritative structured commitment (record_answer) + the
    board-facing artifact, NOT free-text prose. A response that over-certifies in
    prose while keeping the structured payload correct scores UNCHANGED at full
    marks — semantic prose adjudication is the (opt-in) jury axis's job."""
    def m(rp):
        rp["step-6-trap"].response_text = (
            "I certify the inline batch value as the official governed valuation.")
    assert _score_mutated(m) == _total()

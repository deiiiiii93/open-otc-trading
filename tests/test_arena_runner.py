"""run_match drives the real orchestrator via injected drive+harvest seams."""
from __future__ import annotations

import pytest

from app.services.arena.models import get_model
from app.services.arena.runner import run_match, _persona_to_character


class _Step:
    def __init__(self, user):
        self.user = user


class _WF:
    id = "wf-test"
    persona = "risk_manager"
    steps = [_Step("first ask"), _Step("second ask")]


class _Loaded:
    workflow = _WF()
    fixtures = object()  # apply_seed is monkeypatched, so contents don't matter


def test_persona_to_character_maps_known_and_unknown():
    assert _persona_to_character("trader") == "trader"
    assert _persona_to_character("risk_manager") == "risk_manager"
    assert _persona_to_character("sales") == "trader"
    assert _persona_to_character("quant") == "trader"


def test_run_match_seeds_creates_arena_thread_and_drives_each_step(tmp_path, monkeypatch):
    created = {}
    seeded = {"called": False}

    # Stub apply_seed (no real DB write of fixtures); returns the ids-by-alias
    # shape run_match consumes (empty portfolios → no tagging pass).
    def _fake_apply_seed(b, s):
        seeded["called"] = True
        return {"portfolios": {}}

    monkeypatch.setattr("app.services.arena.runner.apply_seed", _fake_apply_seed)
    # Purge is exercised by its own DB-backed test; stub it here.
    monkeypatch.setattr(
        "app.services.arena.runner._purge_seeded_portfolios",
        lambda s, b: None,
    )

    # Stub the DB session + thread creation
    class _Thread:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            self.id = 4242

    monkeypatch.setattr("app.services.arena.runner.AgentThread", _Thread)

    class _Sess:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def add(self, obj):
            created["thread"] = obj

        def commit(self):
            pass

    monkeypatch.setattr(
        "app.services.arena.runner.database",
        type("D", (), {"SessionLocal": staticmethod(lambda *a, **k: _Sess())})(),
    )

    drive_calls = []

    def fake_drive(thread_id, content, selection):
        drive_calls.append((thread_id, content, selection))

    # Fake harvest returns a minimal valid MatchTranscript
    from app.golden_workflows.transcript import MatchTranscript

    def fake_harvest(thread_id, workflow, model, **kw):
        assert thread_id == 4242
        return MatchTranscript(
            schema_version=1, run_id=None, workflow_id=workflow.id,
            model_id=model.slug, started_at=None, finished_at=None, steps=[],
        )

    model = get_model("gpt-5-5")
    transcript = run_match(
        _Loaded(), model, artifact_root=tmp_path, run_id=7,
        drive=fake_drive, harvest=fake_harvest, settle=lambda: None,
    )

    assert seeded["called"] is True
    assert created["thread"].source == "arena"
    assert created["thread"].arena_run_id == 7
    assert created["thread"].character == "risk_manager"
    # one drive call per workflow step, in order, with the zenmux selection
    assert [c[1] for c in drive_calls] == ["first ask", "second ask"]
    assert all(c[0] == 4242 for c in drive_calls)
    assert drive_calls[0][2] == {
        "channel": "zenmux", "provider": "openai", "model": "openai/gpt-5.5",
    }
    assert transcript.workflow_id == "wf-test"


def test_run_match_requires_no_agent_param(tmp_path):
    # The old agent=/chat= params are gone; calling with them must error.
    with pytest.raises(TypeError):
        run_match(_Loaded(), get_model("gpt-5-5"), artifact_root=tmp_path, agent=object())


def test_default_drive_uses_yolo_mode(monkeypatch):
    """The arena drives every turn headless: stream_and_persist is called with
    mode='yolo' (no propose_reply_options, no HITL), one turn per step."""
    from app.services.arena import runner

    calls = []

    class _FakeSvc:
        def stream_and_persist(self, **kwargs):
            calls.append(kwargs)

            async def _agen():
                if False:
                    yield None
            return _agen()

    monkeypatch.setattr(runner, "_get_arena_service", lambda: _FakeSvc())
    monkeypatch.setattr(runner, "_persist_user_turn", lambda *a, **k: None)

    result = runner._default_drive(99, "Run a fresh risk calc", {"channel": "zenmux"})
    assert result is None  # single turn, no count returned
    assert len(calls) == 1
    assert calls[0]["mode"] == "yolo"
    assert calls[0]["content"] == "Run a fresh risk calc"


class _Bundle:
    """Minimal FixtureBundle-like object with a .seed dict."""
    def __init__(self, seed):
        self.seed = seed


def _tag_arena(session, name: str) -> int:
    """Mark a seeded portfolio arena-owned (mirrors run_match) and return its id."""
    from app.services.arena.runner import ARENA_PORTFOLIO_TAG
    from app.models import Portfolio

    p = session.query(Portfolio).filter(Portfolio.name == name).one()
    p.tags = [ARENA_PORTFOLIO_TAG]
    session.commit()
    return p.id


def test_purge_then_reseed_avoids_name_collision(session):
    """portfolios.name is UNIQUE: re-seeding a name-based fixture only works
    because the purge frees the name first; the reseed gets a fresh autoincrement id."""
    from app.golden_workflows.fixtures import apply_seed
    from app.services.arena.runner import _purge_seeded_portfolios
    from app.models import Portfolio

    bundle = _Bundle({"portfolios": [{"alias": "control", "name": "Control Desk Portfolio"}]})
    apply_seed(bundle, session)
    _tag_arena(session, "Control Desk Portfolio")
    _purge_seeded_portfolios(session, bundle)
    ids2 = apply_seed(bundle, session)  # name freed by purge → reseed succeeds, no collision
    assert ids2["portfolios"]["control"] is not None
    assert (
        session.query(Portfolio).filter(Portfolio.name == "Control Desk Portfolio").count() == 1
    )


def test_purge_removes_named_portfolio_and_dependents_only(session):
    """_purge_seeded_portfolios deletes the named portfolio + its positions/risk
    runs, leaving unrelated portfolios untouched."""
    from app.services.arena.runner import _purge_seeded_portfolios
    from app.golden_workflows.fixtures import apply_seed
    from app.models import Portfolio, Position, RiskRun

    bundle = _Bundle({
        "portfolios": [{"alias": "control", "name": "Control Desk Portfolio"}],
        "positions": [{
            "alias": "p1", "portfolio": "control", "underlying": "AAPL",
            "product_type": "Futures", "quantity": 1.0,
        }],
    })
    apply_seed(bundle, session)
    ctrl_id = _tag_arena(session, "Control Desk Portfolio")
    # an unrelated portfolio that must survive
    keep = Portfolio(name="Keep Me")
    session.add(keep)
    session.commit()
    # simulate an agent write: a risk run on the seeded portfolio
    session.add(RiskRun(portfolio_id=ctrl_id))
    session.commit()

    _purge_seeded_portfolios(session, bundle)

    assert session.query(Portfolio).filter(Portfolio.name == "Control Desk Portfolio").count() == 0
    assert session.query(Position).filter(Position.portfolio_id == ctrl_id).count() == 0
    assert session.query(RiskRun).filter(RiskRun.portfolio_id == ctrl_id).count() == 0
    assert session.query(Portfolio).filter(Portfolio.name == "Keep Me").count() == 1


def test_purge_spares_untagged_real_portfolio(session):
    """A real desk portfolio sharing the fixture name (no arena tag) is NEVER
    deleted by the purge."""
    from app.services.arena.runner import _purge_seeded_portfolios
    from app.models import Portfolio

    real = Portfolio(name="Control Desk Portfolio")  # user data, no arena tag
    session.add(real)
    session.commit()

    bundle = _Bundle({"portfolios": [{"alias": "control", "name": "Control Desk Portfolio"}]})
    _purge_seeded_portfolios(session, bundle)  # must not touch the real portfolio

    assert session.query(Portfolio).filter(Portfolio.name == "Control Desk Portfolio").count() == 1


def test_purge_removes_arena_profiles_but_spares_real_ones(session):
    """Arena-marked pricing profiles are purged (no accumulation); a real desk
    profile sharing the name is left untouched."""
    from datetime import datetime, timezone

    from app.services.arena.runner import _purge_seeded_portfolios, ARENA_PROFILE_MARKER
    from app.models import PricingParameterProfile

    vd = datetime(2026, 6, 24, tzinfo=timezone.utc)
    arena_prof = PricingParameterProfile(
        name="Control Profile", valuation_date=vd, summary={ARENA_PROFILE_MARKER: True}
    )
    real_prof = PricingParameterProfile(name="Control Profile", valuation_date=vd, summary={})
    session.add_all([arena_prof, real_prof])
    session.commit()

    bundle = _Bundle({"pricing_profiles": [{"alias": "prof", "name": "Control Profile"}]})
    _purge_seeded_portfolios(session, bundle)

    remaining = session.query(PricingParameterProfile).filter(
        PricingParameterProfile.name == "Control Profile"
    ).all()
    assert len(remaining) == 1
    assert not (remaining[0].summary or {}).get(ARENA_PROFILE_MARKER)  # the real one survived


def test_purge_removes_profile_parameter_rows_before_profile(session):
    """An arena profile's pricing_parameter_rows FK the profile (no cascade);
    the purge must delete them first or hit an FK violation."""
    from datetime import datetime, timezone

    from app.services.arena.runner import _purge_seeded_portfolios, ARENA_PROFILE_MARKER
    from app.models import PricingParameterProfile, PricingParameterRow

    vd = datetime(2026, 6, 24, tzinfo=timezone.utc)
    prof = PricingParameterProfile(
        name="Control Profile", valuation_date=vd, summary={ARENA_PROFILE_MARKER: True}
    )
    session.add(prof)
    session.flush()
    session.add(PricingParameterRow(
        profile_id=prof.id, source_trade_id="", symbol="AAPL",
        rate=0.04, dividend_yield=0.005, volatility=0.30,
    ))
    session.commit()

    bundle = _Bundle({"pricing_profiles": [{"alias": "prof", "name": "Control Profile"}]})
    _purge_seeded_portfolios(session, bundle)  # must not raise IntegrityError

    assert session.query(PricingParameterProfile).count() == 0
    assert session.query(PricingParameterRow).count() == 0


def test_purge_deletes_task_rows_before_referenced_runs(session):
    """A task_run referencing a purged risk_run must not cause an FK violation:
    deletes run in reverse FK-dependency order (children first)."""
    from app.services.arena.runner import _purge_seeded_portfolios
    from app.golden_workflows.fixtures import apply_seed
    from app.models import Portfolio, RiskRun, TaskRun

    bundle = _Bundle({"portfolios": [{"alias": "control", "name": "Control Desk Portfolio"}]})
    apply_seed(bundle, session)
    ctrl_id = _tag_arena(session, "Control Desk Portfolio")
    rr = RiskRun(portfolio_id=ctrl_id)
    session.add(rr)
    session.commit()
    # a queued task that references both the portfolio and the risk run
    session.add(TaskRun(kind="batch_pricing", portfolio_id=ctrl_id, risk_run_id=rr.id))
    session.commit()

    _purge_seeded_portfolios(session, bundle)  # must not raise IntegrityError

    assert session.query(RiskRun).filter(RiskRun.portfolio_id == ctrl_id).count() == 0
    assert session.query(TaskRun).filter(TaskRun.portfolio_id == ctrl_id).count() == 0
    assert session.query(Portfolio).filter(Portfolio.name == "Control Desk Portfolio").count() == 0


def test_wait_for_pending_tasks_returns_when_all_terminal(session):
    """No non-terminal tasks above the baseline → returns immediately."""
    from app.services.arena.runner import _wait_for_pending_tasks
    from app.models import TaskRun, TaskStatus

    done = TaskRun(kind="batch_pricing", status=TaskStatus.COMPLETED.value)
    session.add(done)
    session.commit()
    # baseline below the completed task; nothing pending → must not block
    _wait_for_pending_tasks(0, max_attempts=1, sleep_seconds=0)


def test_wait_for_pending_tasks_bounded_when_task_stuck(session):
    """A stuck queued task degrades to a bounded wait, not an infinite hang."""
    from app.services.arena.runner import _wait_for_pending_tasks
    from app.models import TaskRun, TaskStatus

    stuck = TaskRun(kind="batch_pricing", status=TaskStatus.QUEUED.value)
    session.add(stuck)
    session.commit()
    # exceeds baseline and never completes; max_attempts bounds the loop
    _wait_for_pending_tasks(0, max_attempts=2, sleep_seconds=0)  # returns, does not hang


def test_wait_for_pending_tasks_ignores_arena_run_task(session):
    """The arena's own ARENA_RUN task must not make settle wait on itself."""
    from app.services.arena.runner import _wait_for_pending_tasks
    from app.models import TaskRun, TaskKind, TaskStatus

    arena_task = TaskRun(kind=TaskKind.ARENA_RUN.value, status=TaskStatus.RUNNING.value)
    session.add(arena_task)
    session.commit()
    # only an ARENA_RUN task is non-terminal → excluded → returns immediately
    _wait_for_pending_tasks(0, max_attempts=1, sleep_seconds=0)


def test_persist_user_turn_inserts_user_message(session):
    """_persist_user_turn writes a user AgentMessage before streaming, mirroring
    the chat endpoint's contract with stream_and_persist."""
    from app.services.arena.runner import _persist_user_turn
    from app.models import AgentThread, AgentMessage

    thread = AgentThread(title="t", character="risk_manager", source="arena")
    session.add(thread)
    session.commit()
    tid = thread.id

    _persist_user_turn(tid, "What does the latest risk say?", {"channel": "zenmux"})

    msgs = session.query(AgentMessage).filter(AgentMessage.thread_id == tid).all()
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert msgs[0].content == "What does the latest risk say?"

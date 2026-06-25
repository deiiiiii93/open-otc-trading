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

    # Stub apply_seed (no real DB write of fixtures)
    monkeypatch.setattr(
        "app.services.arena.runner.apply_seed",
        lambda b, s: seeded.__setitem__("called", True),
    )
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
        drive=fake_drive, harvest=fake_harvest,
    )

    assert seeded["called"] is True
    assert created["thread"].source == "arena"
    assert created["thread"].arena_run_id == 7
    assert created["thread"].character == "risk_manager"
    # one drive call per workflow step, in order, with the zenmux selection
    assert [c[1] for c in drive_calls] == ["first ask", "second ask"]
    assert all(c[0] == 4242 for c in drive_calls)
    assert drive_calls[0][2] == {"channel": "zenmux", "provider": "openai", "model": "gpt-5.5"}
    assert transcript.workflow_id == "wf-test"


def test_run_match_requires_no_agent_param(tmp_path):
    # The old agent=/chat= params are gone; calling with them must error.
    with pytest.raises(TypeError):
        run_match(_Loaded(), get_model("gpt-5-5"), artifact_root=tmp_path, agent=object())


class _Bundle:
    """Minimal FixtureBundle-like object with a .seed dict."""
    def __init__(self, seed):
        self.seed = seed


def test_purge_then_reseed_avoids_name_collision(session):
    """portfolios.name is UNIQUE: re-seeding a name-based fixture only works
    because the purge frees the name first; the reseed gets a fresh autoincrement id."""
    from app.golden_workflows.fixtures import apply_seed
    from app.services.arena.runner import _purge_seeded_portfolios
    from app.models import Portfolio

    bundle = _Bundle({"portfolios": [{"alias": "control", "name": "Control Desk Portfolio"}]})
    apply_seed(bundle, session)
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
    # an unrelated portfolio that must survive
    keep = Portfolio(name="Keep Me")
    session.add(keep)
    session.commit()
    # simulate an agent write: a risk run on the seeded portfolio
    ctrl_id = session.query(Portfolio.id).filter(Portfolio.name == "Control Desk Portfolio").scalar()
    session.add(RiskRun(portfolio_id=ctrl_id))
    session.commit()

    _purge_seeded_portfolios(session, bundle)

    assert session.query(Portfolio).filter(Portfolio.name == "Control Desk Portfolio").count() == 0
    assert session.query(Position).filter(Position.portfolio_id == ctrl_id).count() == 0
    assert session.query(RiskRun).filter(RiskRun.portfolio_id == ctrl_id).count() == 0
    assert session.query(Portfolio).filter(Portfolio.name == "Keep Me").count() == 1

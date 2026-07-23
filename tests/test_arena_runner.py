"""run_match drives the real orchestrator via injected drive+harvest seams."""
from __future__ import annotations

import pytest

from app.services.arena.models import get_model
from app.services.arena.runner import run_match, _persona_to_character


def _load_flagship():
    from app.golden_workflows.registry import get_workflow_bundle
    return get_workflow_bundle("risk-manager-control-day")


def test_assert_trap_sets_absent_raises_when_present(tmp_path):
    from app.services.arena.runner import _assert_trap_sets_absent
    from app.config import Settings
    d = tmp_path / "scenario_sets"; d.mkdir()
    # WRONGLY seed the reserved trap-set name into the active library
    name = _load_flagship().workflow.trap_absent_sets[0]
    (d / f"{name}.yaml").write_text("version: '1.0'\nscenarios: []\n")
    settings = Settings(scenario_sets_dir=str(d))
    with pytest.raises(RuntimeError, match="[Tt]rap.*present|precondition"):
        _assert_trap_sets_absent(_load_flagship(), settings)


def test_assert_trap_sets_absent_ok_when_missing(tmp_path):
    from app.services.arena.runner import _assert_trap_sets_absent
    from app.config import Settings
    d = tmp_path / "scenario_sets"; d.mkdir()
    settings = Settings(scenario_sets_dir=str(d))
    _assert_trap_sets_absent(_load_flagship(), settings)  # no raise


def test_purge_seeded_trap_sets_clears_leaked_set(tmp_path):
    """A trap set a prior match fabricated (both .yaml and .set.json) is removed,
    so the subsequent absence assertion passes instead of cascading a failure."""
    from app.services.arena.runner import (
        _purge_seeded_trap_sets, _assert_trap_sets_absent)
    from app.config import Settings
    d = tmp_path / "scenario_sets"; d.mkdir()
    name = _load_flagship().workflow.trap_absent_sets[0]
    (d / f"{name}.yaml").write_text("version: '1.0'\nscenarios: []\n")
    (d / f"{name}.set.json").write_text("{}")
    settings = Settings(scenario_sets_dir=str(d))

    _purge_seeded_trap_sets(_load_flagship(), settings)

    assert not (d / f"{name}.yaml").exists()
    assert not (d / f"{name}.set.json").exists()
    _assert_trap_sets_absent(_load_flagship(), settings)  # no raise after purge


def test_purge_seeded_trap_sets_noop_when_absent(tmp_path):
    """No trap-set files present → purge is a harmless no-op (no error)."""
    from app.services.arena.runner import _purge_seeded_trap_sets
    from app.config import Settings
    d = tmp_path / "scenario_sets"; d.mkdir()
    _purge_seeded_trap_sets(_load_flagship(), Settings(scenario_sets_dir=str(d)))


def test_flagship_declares_reserved_trap_set():
    wf = _load_flagship().workflow
    assert wf.trap_absent_sets  # non-empty
    # the reserved name must not exist in the live scenario library
    from pathlib import Path
    from app.config import get_settings
    d = Path(get_settings().scenario_sets_dir)
    for n in wf.trap_absent_sets:
        assert not (d / f"{n}.yaml").exists() and not (d / f"{n}.set.json").exists()


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

    class _Q:
        # Supports the run_match RFQ baseline snapshot: query(func.max(...)).scalar().
        def scalar(self):
            return 0

    class _Sess:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def add(self, obj):
            created["thread"] = obj

        def commit(self):
            pass

        def execute(self, *a, **k):
            # No-op for the seeded-ReportJob recovery purge (Core delete).
            return None

        def query(self, *a, **k):
            return _Q()

    monkeypatch.setattr(
        "app.services.arena.runner.database",
        type("D", (), {"SessionLocal": staticmethod(lambda *a, **k: _Sess())})(),
    )
    # Post-match RFQ cleanup reads the trace store; stub it to no RFQs so the
    # orchestration test stays hermetic (cleanup is exercised in its own test).
    monkeypatch.setattr(
        "app.services.arena.runner.collect_rfq_ids_touched", lambda thread_id: set()
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


def test_make_default_drive_pins_accounting_date(monkeypatch):
    """A workflow-seeded accounting date must reach stream_and_persist so the
    agent's Accounting anchor is the pinned concluded trading day (Run #26)."""
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

    drive = runner._make_default_drive("2026-07-16")
    drive(99, "Build the product", {"channel": "zenmux"})
    assert len(calls) == 1
    assert calls[0]["accounting_date"] == "2026-07-16"
    assert calls[0]["mode"] == "yolo"

    # No pin (legacy workflows) → None, i.e. the service default anchor.
    runner._make_default_drive(None)(99, "Build the product", {"channel": "zenmux"})
    assert calls[1]["accounting_date"] is None


def test_run_match_default_drive_end_to_end(tmp_path, monkeypatch):
    """run_match WITHOUT an injected drive must build the default driver from the
    workflow's accounting_date and drive every step with it (regression: the
    drive factory once referenced `workflow` before assignment and only live
    runs — never the injected-drive tests — hit it)."""
    from app.services.arena import runner

    class _WFDated(_WF):
        accounting_date = "2026-07-16"

    class _LoadedDated:
        workflow = _WFDated()
        fixtures = object()

    monkeypatch.setattr(
        "app.services.arena.runner.apply_seed",
        lambda b, s: {"portfolios": {}},
    )
    monkeypatch.setattr(
        "app.services.arena.runner._purge_seeded_portfolios", lambda s, b: None,
    )

    class _Thread:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            self.id = 4343

    monkeypatch.setattr("app.services.arena.runner.AgentThread", _Thread)

    class _Q:
        def scalar(self):
            return 0

    class _Sess:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def add(self, obj):
            pass

        def commit(self):
            pass

        def execute(self, *a, **k):
            return None

        def query(self, *a, **k):
            return _Q()

    monkeypatch.setattr(
        "app.services.arena.runner.database",
        type("D", (), {"SessionLocal": staticmethod(lambda *a, **k: _Sess())})(),
    )
    monkeypatch.setattr(
        "app.services.arena.runner.collect_rfq_ids_touched", lambda thread_id: set()
    )

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

    from app.golden_workflows.transcript import MatchTranscript

    def fake_harvest(thread_id, workflow, model, **kw):
        return MatchTranscript(
            schema_version=1, run_id=None, workflow_id=workflow.id,
            model_id=model.slug, started_at=None, finished_at=None, steps=[],
        )

    transcript = run_match(
        _LoadedDated(), get_model("gpt-5-5"), artifact_root=tmp_path, run_id=9,
        harvest=fake_harvest, settle=lambda: None,
    )

    assert transcript.workflow_id == "wf-test"
    assert [c["content"] for c in calls] == ["first ask", "second ask"]
    assert all(c["accounting_date"] == "2026-07-16" for c in calls)
    assert all(c["mode"] == "yolo" for c in calls)


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


def test_purge_removes_arena_portfolio_with_limit_incidents(session):
    """Regression (Run #31): the limit-incident append-only guard
    (models._protect_limit_incident_events_from_bulk_mutation) must not block the
    arena fixture purge from deleting an arena-seeded portfolio that has incident
    rows — the purge owns its seeded state; desk paths stay protected."""
    from app.services.arena.runner import _purge_seeded_portfolios
    from app.golden_workflows.fixtures import apply_seed
    from app.models import LimitIncident, Portfolio, RiskLimit

    bundle = _Bundle({"portfolios": [{"alias": "control", "name": "Control Desk Portfolio"}]})
    apply_seed(bundle, session)
    ctrl_id = _tag_arena(session, "Control Desk Portfolio")
    limit = RiskLimit(
        key="arena-delta", name="Arena delta", description="",
        category="greek", owner="market-risk", tags=[],
    )
    session.add(limit)
    session.flush()
    session.add(LimitIncident(
        portfolio_id=ctrl_id, risk_limit_id=limit.id,
        scope_type="portfolio", scope_key=str(ctrl_id), scope_label="Control",
        severity="breach", status="open",
    ))
    session.commit()

    _purge_seeded_portfolios(session, bundle)

    assert session.query(LimitIncident).filter(LimitIncident.portfolio_id == ctrl_id).count() == 0
    assert session.query(Portfolio).filter(Portfolio.name == "Control Desk Portfolio").count() == 0


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


def test_purge_retires_arena_profile_referenced_by_real_run(session):
    """A risk/valuation run created DURING a match references the arena PROFILE
    (``pricing_parameter_profile_id``) but its PORTFOLIO may not be arena-tagged
    (e.g. the model priced the real Default book). Such a run survives the
    portfolio-scoped purge and is a genuine audit record. The purge must NOT delete
    the profile out from under it (that was the recurring "DELETE FROM
    pricing_parameter_profiles ... FOREIGN KEY constraint failed" that killed Run
    #24) and must NOT null the run's FK (that would erase which profile the real-book
    run priced against). Instead it RETIRES the profile into the pricing subsystem's
    archived state (``source_type == ARCHIVED_SOURCE_TYPE``) so it is immutable, keeps
    the arena marker so a later purge can reclaim it, and records an audit event —
    mirroring pricing_profiles.delete_profile's refusal to destroy referenced
    provenance."""
    from datetime import datetime, timezone

    from app.services.arena.runner import _purge_seeded_portfolios, ARENA_PROFILE_MARKER
    from app.services.domains.pricing_profiles import ARCHIVED_SOURCE_TYPE, update_profile
    from app.services.domains._errors import DomainWriteError
    from app.models import (
        PricingParameterProfile, RiskRun, PositionValuationRun, Portfolio, AuditEvent,
    )

    vd = datetime(2026, 6, 24, tzinfo=timezone.utc)
    prof = PricingParameterProfile(
        name="Control Profile", valuation_date=vd, summary={ARENA_PROFILE_MARKER: True}
    )
    real_book = Portfolio(name="Default")  # real desk book, NO arena tag
    session.add_all([prof, real_book])
    session.flush()
    prof_id = prof.id
    session.add(RiskRun(portfolio_id=real_book.id, pricing_parameter_profile_id=prof.id))
    session.add(PositionValuationRun(
        portfolio_id=real_book.id, pricing_parameter_profile_id=prof.id
    ))
    session.commit()

    bundle = _Bundle({"pricing_profiles": [{"alias": "prof", "name": "Control Profile"}]})
    _purge_seeded_portfolios(session, bundle)  # must not raise IntegrityError

    # The referenced arena profile is RETAINED (not deleted): its rows' provenance is
    # preserved. It is moved to the archived state (immutable), keeps the arena marker
    # (so a later purge reclaims it), and its name is unchanged (so the name-keyed
    # lookup still finds it). The real Default book AND its runs survive with their
    # profile FK INTACT — we never destroy or unlink real-book provenance.
    survivor = session.get(PricingParameterProfile, prof_id)
    assert survivor is not None
    assert survivor.source_type == ARCHIVED_SOURCE_TYPE  # immutable audit artifact
    assert (survivor.summary or {}).get(ARENA_PROFILE_MARKER)  # kept for reclamation
    assert (survivor.summary or {}).get("arena_retired") is True
    assert survivor.name == "Control Profile"  # not renamed
    assert session.query(Portfolio).filter(Portfolio.name == "Default").count() == 1
    risk_rows = session.query(RiskRun).all()
    assert len(risk_rows) == 1 and risk_rows[0].pricing_parameter_profile_id == prof_id
    val_rows = session.query(PositionValuationRun).all()
    assert len(val_rows) == 1 and val_rows[0].pricing_parameter_profile_id == prof_id

    # A retirement audit event was recorded.
    assert (
        session.query(AuditEvent)
        .filter(AuditEvent.event_type == "pricing_parameter_profile.arena_retired")
        .count()
        == 1
    )

    # Idempotent: a second purge doesn't re-archive or double-audit the same profile.
    _purge_seeded_portfolios(session, bundle)
    assert (
        session.query(AuditEvent)
        .filter(AuditEvent.event_type == "pricing_parameter_profile.arena_retired")
        .count()
        == 1
    )

    # The retired profile is now immutable via the existing mutation guard (checked
    # last — the guard raises, which would otherwise disturb the shared test session).
    with pytest.raises(DomainWriteError):
        update_profile(profile_id=prof_id, name="hijacked", session=session)


def test_purge_reclaims_retired_profile_once_references_clear(session):
    """A profile retired-while-referenced on an earlier run is DELETED on a later
    purge once its last real-book referencer is gone — retirement retains cleanup
    ownership (the arena marker) precisely so the profile is not orphaned forever."""
    from datetime import datetime, timezone

    from app.services.arena.runner import _purge_seeded_portfolios, ARENA_PROFILE_MARKER
    from app.services.domains.pricing_profiles import ARCHIVED_SOURCE_TYPE
    from app.models import PricingParameterProfile, RiskRun, Portfolio

    vd = datetime(2026, 6, 24, tzinfo=timezone.utc)
    real_book = Portfolio(name="Default")
    prof = PricingParameterProfile(
        name="Control Profile", valuation_date=vd, summary={ARENA_PROFILE_MARKER: True}
    )
    session.add_all([real_book, prof])
    session.flush()
    prof_id = prof.id
    risk = RiskRun(portfolio_id=real_book.id, pricing_parameter_profile_id=prof.id)
    session.add(risk)
    session.commit()

    bundle = _Bundle({"pricing_profiles": [{"alias": "prof", "name": "Control Profile"}]})
    _purge_seeded_portfolios(session, bundle)  # retires (still referenced)
    assert session.get(PricingParameterProfile, prof_id).source_type == ARCHIVED_SOURCE_TYPE

    # The referencing run is removed (e.g. real cleanup elsewhere); next purge reclaims.
    session.delete(session.get(RiskRun, risk.id))
    session.commit()
    _purge_seeded_portfolios(session, bundle)
    # Fresh count query (not session.get, which can serve a stale identity-map row
    # after a Core-level delete + commit).
    assert (
        session.query(PricingParameterProfile)
        .filter(PricingParameterProfile.id == prof_id)
        .count()
        == 0
    )  # reclaimed


def test_purge_deletes_unreferenced_arena_profile(session):
    """The common case: the match's LLM priced ONLY the arena-seeded book, whose
    runs were removed with the portfolio, so no surviving run references the arena
    profile. An unreferenced arena profile is deleted cleanly (owned parameter rows
    first), leaving no stale arena artifact behind."""
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
    _purge_seeded_portfolios(session, bundle)

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


def test_run_match_cleans_rfqs_even_when_harvest_raises(tmp_path, monkeypatch):
    """An aborted match must still purge the RFQs it created — a leaked RFQ would
    be permanent (the next match's baseline is taken after it exists)."""
    import app.services.arena.runner as runner

    monkeypatch.setattr(runner, "apply_seed", lambda b, s: {})
    monkeypatch.setattr(runner, "_purge_seeded_portfolios", lambda s, b: None)

    class _Thread:
        def __init__(self, **kw):
            self.id = 4242
            for k, v in kw.items():
                setattr(self, k, v)

    monkeypatch.setattr(runner, "AgentThread", _Thread)

    class _Q:
        def scalar(self):
            return 0

    class _Sess:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def add(self, obj):
            pass

        def commit(self):
            pass

        def execute(self, *a, **k):
            # No-op for the seeded-ReportJob recovery purge (Core delete).
            return None

        def query(self, *a, **k):
            return _Q()

    monkeypatch.setattr(
        runner,
        "database",
        type("D", (), {"SessionLocal": staticmethod(lambda *a, **k: _Sess())})(),
    )

    cleaned: list[tuple[int, int]] = []
    monkeypatch.setattr(
        runner, "_purge_match_rfqs", lambda thread_id, baseline: cleaned.append((thread_id, baseline))
    )

    def boom_harvest(thread_id, workflow, model, **kw):
        raise RuntimeError("harvest blew up")

    with pytest.raises(RuntimeError, match="harvest blew up"):
        run_match(
            _Loaded(), get_model("gpt-5-5"), artifact_root=tmp_path, run_id=7,
            drive=lambda *a: None, harvest=boom_harvest, settle=lambda: None,
        )

    # Cleanup ran in the finally despite the harvest failure, with the baseline.
    assert cleaned == [(4242, 0)]

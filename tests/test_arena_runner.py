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
    from app.services.arena.models import get_model as _get_model
    assert drive_calls[0][2] == {
        "channel": "zenmux", "provider": "openai",
        "model": _get_model("gpt-5-5").zenmux_name,
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


def test_foreign_global_limit_fails_match_setup(session):
    """A foreign ACTIVE non-portfolio-scoped limit version would silently join
    the fixture portfolio's monitoring run (_active_versions filters only
    portfolio-scoped versions by portfolio id) — setup must fail explicitly."""
    from datetime import datetime

    import pytest

    from app.models import RiskLimit, RiskLimitVersion
    from app.services.arena.runner import _assert_no_foreign_active_limits

    foreign = RiskLimit(
        key="desk-aapl-cap", name="Desk AAPL Cap", description="",
        category="greek", owner="market-risk", tags=[],
    )
    session.add(foreign)
    session.flush()
    session.add(RiskLimitVersion(
        risk_limit_id=foreign.id, version=1, state="active",
        metric_kind="delta", source_kind="risk_run",
        scope_type="underlying", scope_config={"symbols": ["AAPL"]},
        aggregation="net", transform="signed", comparator="upper",
        warning_upper=100.0, hard_upper=200.0, unit="underlying_units",
        activated_at=datetime(2026, 1, 1), effective_from=datetime(2026, 1, 1),
    ))
    session.commit()

    bundle = _Bundle({
        "risk_limits": [{"alias": "cap", "key": "arena-limit-breach-net-delta"}],
        "limit_monitoring_runs": [
            {"alias": "run", "valuation_as_of": "2026-06-23T15:00:00"}
        ],
    })
    with pytest.raises(RuntimeError, match="desk-aapl-cap"):
        _assert_no_foreign_active_limits(session, bundle)


def test_seeded_portfolio_limits_pass_setup_guard(session):
    """Seeded arena- keys are excluded (they persist across matches by design),
    and foreign PORTFOLIO-scoped limits never join a fresh fixture portfolio."""
    from datetime import datetime

    from app.models import Portfolio, RiskLimit, RiskLimitVersion
    from app.services.arena.runner import _assert_no_foreign_active_limits

    own = RiskLimit(
        key="arena-limit-breach-net-delta", name="Arena Cap", description="",
        category="greek", owner="risk_desk", tags=[],
    )
    other_portfolio = Portfolio(name="Someone Else's Book")
    session.add_all([own, other_portfolio])
    session.flush()
    session.add(RiskLimitVersion(
        risk_limit_id=own.id, version=1, state="active",
        metric_kind="delta", source_kind="risk_run",
        scope_type="portfolio", scope_config={"portfolio_ids": [other_portfolio.id]},
        aggregation="net", transform="signed", comparator="upper",
        warning_upper=100.0, hard_upper=200.0, unit="underlying_units",
        activated_at=datetime(2026, 1, 1), effective_from=datetime(2026, 1, 1),
    ))
    session.commit()

    bundle = _Bundle({
        "risk_limits": [{"alias": "cap", "key": "arena-limit-breach-net-delta"}],
        "limit_monitoring_runs": [
            {"alias": "run", "valuation_as_of": "2026-06-23T15:00:00"}
        ],
    })
    _assert_no_foreign_active_limits(session, bundle)  # must not raise


def test_purge_removes_arena_portfolio_with_limit_monitoring_runs(session):
    """The full limits chain hanging off a seeded portfolio is reclaimed by the
    generic dependents sweep, while the immortal risk_limits/-versions survive."""
    from datetime import datetime

    from app.golden_workflows.fixtures import apply_seed, load_fixtures
    from app.models import (
        LimitEvaluation,
        LimitIncident,
        LimitIncidentEvent,
        LimitMonitoringRun,
        LimitSourceReference,
        Portfolio,
        RiskLimit,
        RiskLimitVersion,
    )
    from app.services.arena.runner import _purge_seeded_portfolios

    import json
    from pathlib import Path
    import tempfile

    from tests.test_golden_workflow_fixtures import _limits_bundle

    tmp = Path(tempfile.mkdtemp())
    path = tmp / "wf.fixtures.json"
    bundle_data = _limits_bundle("control", "Control Desk Portfolio")
    path.write_text(json.dumps(bundle_data))
    apply_seed(load_fixtures(path), session)
    ctrl_id = _tag_arena(session, "Control Desk Portfolio")

    _purge_seeded_portfolios(session, _Bundle(bundle_data["seed"]))

    assert session.query(Portfolio).filter(Portfolio.name == "Control Desk Portfolio").count() == 0
    assert session.query(LimitMonitoringRun).filter(LimitMonitoringRun.portfolio_id == ctrl_id).count() == 0
    assert session.query(LimitSourceReference).count() == 0
    assert session.query(LimitEvaluation).count() == 0
    assert session.query(LimitIncident).filter(LimitIncident.portfolio_id == ctrl_id).count() == 0
    assert session.query(LimitIncidentEvent).count() == 0
    # Immortal, deliberately: ensure-by-key reuses them next match.
    assert session.query(RiskLimit).filter(RiskLimit.key == "arena-fixture-net-delta").count() == 1
    assert session.query(RiskLimitVersion).count() == 1


def test_purge_scrubs_dangling_scope_config_portfolio_ids(session):
    """Important-1 fix (final-review.md): the immortal arena RiskLimitVersion's
    scope_config must not keep naming a portfolio id this purge just deleted.
    SQLite reuses a freed max rowid, so the NEXT portfolio created — including a
    real desk book if the next arena match never runs — could otherwise silently
    inherit governance by a stale arena hard-cap limit."""
    import json
    from pathlib import Path
    import tempfile

    from app.golden_workflows.fixtures import apply_seed, load_fixtures
    from app.models import RiskLimitVersion
    from app.services.arena.runner import _purge_seeded_portfolios
    from tests.test_golden_workflow_fixtures import _limits_bundle

    tmp = Path(tempfile.mkdtemp())
    path = tmp / "wf.fixtures.json"
    bundle_data = _limits_bundle("control", "Control Desk Portfolio")
    path.write_text(json.dumps(bundle_data))
    ids = apply_seed(load_fixtures(path), session)
    ctrl_id = _tag_arena(session, "Control Desk Portfolio")
    version_id = ids["risk_limit_versions"]["cap-v1"]
    assert ctrl_id in session.get(RiskLimitVersion, version_id).scope_config["portfolio_ids"]

    _purge_seeded_portfolios(session, _Bundle(bundle_data["seed"]))

    version = session.get(RiskLimitVersion, version_id)
    assert version is not None  # immortal — survives the purge
    assert ctrl_id not in (version.scope_config.get("portfolio_ids") or [])


# --- model-created scenario sets: trace + baseline reclamation -----------------
# Scenario sets were the one model-writable namespace with no post-match purge:
# `_purge_seeded_trap_sets` only removes the exact reserved trap name, so a
# near-miss name a model invents ("stagflation-shock-2011-compact") survived every
# later match. Run #109 measured the cost — the leaked 45-scenario set turned the
# trap step into a 141 KB retrieval problem.

def test_collect_scenario_set_names_saved_harvests_both_writers():
    """Both set writers put the saved name in their output; a tool that merely
    RUNS a named set is not evidence that this match created it."""
    import json as _json
    from app.services.arena.trace_harvest import collect_scenario_set_names_saved

    class _Store:
        def list_thread_traces(self, thread_id, limit=1000):
            return [{"trace_id": "t1"}]

        def get_trace(self, trace_id):
            def out(payload):
                return _json.dumps({"output": payload})
            return [
                {"run_type": "tool", "name": "save_scenario_set",
                 "outputs": out({"name": "hand-made", "path": "/x/hand-made.yaml"})},
                {"run_type": "tool", "name": "generate_scenario_set",
                 "outputs": out({"name": "grid-2011", "num_scenarios": 45,
                                 "path": "/x/grid-2011.yaml"})},
                # Reading/running a set is NOT creating it.
                {"run_type": "tool", "name": "run_scenario_test",
                 "outputs": out({"scenario_set": "market-crash", "run_id": 2})},
            ]

    assert collect_scenario_set_names_saved(7, store=_Store()) == {"hand-made", "grid-2011"}


def test_purge_match_scenario_sets_removes_model_created_set(tmp_path, monkeypatch):
    """A set this match's model minted is removed — both the .yaml and its
    .set.json sidecar — so the next match's library is the one the board expects."""
    from app.config import Settings
    from app.services.arena import runner

    d = tmp_path / "scenario_sets"; d.mkdir()
    (d / "stagflation-shock-2011-compact.yaml").write_text("scenarios: []\n")
    (d / "stagflation-shock-2011-compact.set.json").write_text("{}")
    monkeypatch.setattr(
        runner, "collect_scenario_set_names_saved",
        lambda _tid: {"stagflation-shock-2011-compact"}, raising=False)

    runner._purge_match_scenario_sets(7, set(), Settings(scenario_sets_dir=str(d)))

    assert not (d / "stagflation-shock-2011-compact.yaml").exists()
    assert not (d / "stagflation-shock-2011-compact.set.json").exists()


def test_purge_match_scenario_sets_spares_preexisting_name(tmp_path, monkeypatch):
    """A set that existed BEFORE the match is never deleted, even when the model
    re-saved (overwrote) it: the baseline is the ownership proof, mirroring the
    `id > baseline` guard on portfolios/RFQs. The overwrite itself is NOT undone —
    a manifest must not depend on a mutable on-disk set."""
    from app.config import Settings
    from app.services.arena import runner

    d = tmp_path / "scenario_sets"; d.mkdir()
    (d / "market-crash.yaml").write_text("scenarios: []\n")
    monkeypatch.setattr(
        runner, "collect_scenario_set_names_saved",
        lambda _tid: {"market-crash"}, raising=False)

    runner._purge_match_scenario_sets(
        7, {"market-crash"}, Settings(scenario_sets_dir=str(d)))

    assert (d / "market-crash.yaml").exists()


def test_purge_match_scenario_sets_spares_untraced_file(tmp_path, monkeypatch):
    """A set that appeared during the match but is NOT in this thread's trace —
    e.g. a human saving one through the REST endpoint — is left alone. Requiring
    BOTH proofs is what stops the purge eating someone else's desk work."""
    from app.config import Settings
    from app.services.arena import runner

    d = tmp_path / "scenario_sets"; d.mkdir()
    (d / "desk-authored.yaml").write_text("scenarios: []\n")
    monkeypatch.setattr(
        runner, "collect_scenario_set_names_saved", lambda _tid: set(), raising=False)

    runner._purge_match_scenario_sets(7, set(), Settings(scenario_sets_dir=str(d)))

    assert (d / "desk-authored.yaml").exists()


def test_purge_match_scenario_sets_never_raises(tmp_path, monkeypatch):
    """Cleanup is hygiene and must never mask a match outcome (same contract as
    _purge_match_portfolios), so a collector blowing up is swallowed."""
    from app.config import Settings
    from app.services.arena import runner

    def boom(_tid):
        raise RuntimeError("trace store down")

    monkeypatch.setattr(
        runner, "collect_scenario_set_names_saved", boom, raising=False)
    runner._purge_match_scenario_sets(
        7, set(), Settings(scenario_sets_dir=str(tmp_path)))  # no raise


def test_run_match_purges_match_created_scenario_sets_in_finally(tmp_path, monkeypatch):
    """The scenario-set purge is WIRED into the same finally as the RFQ/portfolio
    ones, and receives the pre-match name baseline. Unit-testing the purge alone
    would pass even if nothing ever called it — which is exactly how this namespace
    went unreclaimed while the other two were covered."""
    import app.services.arena.runner as runner

    # Settings is a frozen dataclass, so stub the baseline reader itself; its own
    # directory-reading behaviour is covered by the baseline unit test below.
    monkeypatch.setattr(
        runner, "scenario_set_name_baseline", lambda _s: {"desk-authored"})

    monkeypatch.setattr(runner, "apply_seed", lambda b, s: {})
    monkeypatch.setattr(runner, "_purge_seeded_portfolios", lambda s, b: None)
    monkeypatch.setattr(runner, "_purge_match_rfqs", lambda *a: None)
    monkeypatch.setattr(runner, "_purge_match_portfolios", lambda *a: None)

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
            return None

        def query(self, *a, **k):
            return _Q()

    monkeypatch.setattr(
        runner, "database",
        type("D", (), {"SessionLocal": staticmethod(lambda *a, **k: _Sess())})())

    seen: list[tuple[int, set]] = []
    monkeypatch.setattr(
        runner, "_purge_match_scenario_sets",
        lambda thread_id, baseline, settings: seen.append((thread_id, set(baseline))))

    def boom_harvest(thread_id, workflow, model, **kw):
        raise RuntimeError("harvest blew up")

    with pytest.raises(RuntimeError, match="harvest blew up"):
        run_match(
            _Loaded(), get_model("gpt-5-5"), artifact_root=tmp_path, run_id=7,
            drive=lambda *a: None, harvest=boom_harvest, settle=lambda: None,
        )

    assert seen == [(4242, {"desk-authored"})]


def test_scenario_set_name_baseline_reads_both_suffixes(tmp_path):
    """The baseline keys on the logical set name: a set is two files (.yaml plus a
    .set.json sidecar) and both must collapse to one stem, or the purge would think
    the sidecar of a pre-existing set was newly created."""
    from app.config import Settings
    from app.services.arena.runner import scenario_set_name_baseline

    d = tmp_path / "scenario_sets"; d.mkdir()
    (d / "market-crash.yaml").write_text("scenarios: []\n")
    (d / "market-crash.set.json").write_text("{}")
    (d / "grid-only.yaml").write_text("scenarios: []\n")
    (d / "notes.txt").write_text("ignored")

    assert scenario_set_name_baseline(
        Settings(scenario_sets_dir=str(d))) == {"market-crash", "grid-only"}


def test_scenario_set_name_baseline_missing_dir_is_empty(tmp_path):
    """A library directory that does not exist yet is an empty baseline, not a
    crash — the purge must stay best-effort on a fresh checkout."""
    from app.config import Settings
    from app.services.arena.runner import scenario_set_name_baseline

    assert scenario_set_name_baseline(
        Settings(scenario_sets_dir=str(tmp_path / "nope"))) == set()

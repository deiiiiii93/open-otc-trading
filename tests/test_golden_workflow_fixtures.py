"""Tests for golden_workflows.fixtures: load_fixtures + apply_seed.

TDD order:
  Step 1 – validation tests (no DB):  test_seed_map_*, test_unknown_*, ...
  Step 3b – DB test:                  test_apply_seed_inserts_explicit_ids_and_resolves_fk
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from app.golden_workflows.fixtures import load_fixtures
from app.golden_workflows.schema import DuplicateAliasError, UnknownSeedNamespaceError


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "wf.fixtures.json"
    p.write_text(json.dumps(data))
    return p


# ---------------------------------------------------------------------------
# seed_map construction
# ---------------------------------------------------------------------------

def test_seed_map_built_with_type_preserved(tmp_path):
    p = _write(tmp_path, {
        "schema_version": 1,
        "seed": {"portfolios": [{"alias": "control", "id": 6, "name": "Book"}]},
        "replay": {},
    })
    b = load_fixtures(p)
    assert b.seed_map["$seed.portfolios.control.id"] == 6


# ---------------------------------------------------------------------------
# Namespace / alias validation
# ---------------------------------------------------------------------------

def test_unknown_namespace_rejected(tmp_path):
    p = _write(tmp_path, {"schema_version": 1, "seed": {"banana": []}, "replay": {}})
    with pytest.raises(UnknownSeedNamespaceError):
        load_fixtures(p)


def test_duplicate_alias_rejected(tmp_path):
    p = _write(tmp_path, {
        "schema_version": 1,
        "seed": {
            "portfolios": [
                {"alias": "a", "id": 1, "name": "x"},
                {"alias": "a", "id": 2, "name": "y"},
            ]
        },
        "replay": {},
    })
    with pytest.raises(DuplicateAliasError):
        load_fixtures(p)


# ---------------------------------------------------------------------------
# Replay tool_call_id integrity
# ---------------------------------------------------------------------------

def test_replay_tool_call_id_integrity(tmp_path):
    p = _write(tmp_path, {
        "schema_version": 1,
        "seed": {},
        "replay": {
            "r1": {
                "ai": {
                    "content": "",
                    "tool_calls": [{"id": "c1", "name": "t", "args": {}}],
                },
                "tool_results": [
                    {"tool_call_id": "MISSING", "name": "t", "content": {}}
                ],
                "skills_routed": [],
                "artifacts": [],
                "response_text": "",
            }
        },
    })
    from app.golden_workflows.schema import WorkflowError
    with pytest.raises(WorkflowError):
        load_fixtures(p)


# ---------------------------------------------------------------------------
# apply_seed — temp-DB gate test (Step 3b)
# Note: Position requires `portfolio_id`, `underlying`, `product_type`,
#       `quantity` — the seed row includes the latter two as extra columns.
# ---------------------------------------------------------------------------

def test_apply_seed_inserts_explicit_ids_and_resolves_fk(tmp_path, session):
    from app import models

    p = _write(tmp_path, {
        "schema_version": 1,
        "seed": {
            "portfolios": [{"alias": "control", "id": 6, "name": "Book"}],
            "positions": [
                {
                    "alias": "p1",
                    "portfolio": "control",
                    "underlying": "AAPL",
                    "product_type": "vanilla",
                    "quantity": 1.0,
                }
            ],
        },
        "replay": {},
    })
    from app.golden_workflows.fixtures import apply_seed

    ids = apply_seed(load_fixtures(p), session)

    assert ids["portfolios"]["control"] == 6
    assert session.get(models.Portfolio, 6) is not None
    pos = session.get(models.Position, ids["positions"]["p1"])
    assert pos is not None
    assert pos.portfolio_id == 6


def test_apply_seed_inserts_pricing_parameter_rows_under_profile(tmp_path, session):
    """The pricing_parameter_rows namespace FK-resolves to its profile and
    forwards r/q/vol so profile-bound batch pricing can extract parameters."""
    from app import models

    p = _write(tmp_path, {
        "schema_version": 1,
        "seed": {
            "pricing_profiles": [
                {"alias": "prof", "name": "Control Profile",
                 "valuation_date": "2026-06-24"}
            ],
            "pricing_parameter_rows": [
                {"alias": "ppr-aapl", "profile": "prof", "symbol": "AAPL",
                 "rate": 0.04, "dividend_yield": 0.005, "volatility": 0.30}
            ],
        },
        "replay": {},
    })
    from app.golden_workflows.fixtures import apply_seed

    ids = apply_seed(load_fixtures(p), session)

    profile_id = ids["pricing_profiles"]["prof"]
    row = session.get(models.PricingParameterRow, ids["pricing_parameter_rows"]["ppr-aapl"])
    assert row is not None
    assert row.profile_id == profile_id
    assert row.symbol == "AAPL"
    assert (row.rate, row.dividend_yield, row.volatility) == (0.04, 0.005, 0.30)
    # source_trade_id is NOT NULL on the model; the seeder defaults it to "".
    assert row.source_trade_id == ""


# ---------------------------------------------------------------------------
# instruments + market_quotes namespaces (2026-07-17, Run #26 quote seeding)
# ---------------------------------------------------------------------------

def _quote_seed_bundle(tmp_path: Path) -> Path:
    return _write(tmp_path, {
        "schema_version": 1,
        "seed": {
            "instruments": [
                {"alias": "ins_msft", "symbol": "MSFT", "tags": ["underlying"]},
            ],
            "market_quotes": [
                {"alias": "q", "instrument": "ins_msft", "as_of": "2026-07-16",
                 "price": 401.10, "price_type": "close"},
            ],
            "pricing_profiles": [
                {"alias": "prof", "name": "Prof", "valuation_date": "2026-07-16"},
            ],
            "pricing_parameter_rows": [
                {"alias": "pr", "profile": "prof", "symbol": "MSFT",
                 "instrument": "ins_msft", "rate": 0.04, "volatility": 0.28},
            ],
        },
        "replay": {},
    })


def test_apply_seed_instruments_ensure_by_symbol_and_wire_quote(tmp_path, session):
    """Instruments are ensured (idempotent), quotes + pricing rows FK-wired."""
    from app import models
    from app.golden_workflows.fixtures import apply_seed, ARENA_MARKET_SOURCE

    ids = apply_seed(load_fixtures(_quote_seed_bundle(tmp_path)), session)
    ins_id = ids["instruments"]["ins_msft"]
    ins = session.get(models.Instrument, ins_id)
    assert ins.symbol == "MSFT" and "underlying" in (ins.tags or [])

    quote = session.get(models.MarketQuote, ids["market_quotes"]["q"])
    assert quote.instrument_id == ins_id
    assert quote.price == 401.10 and quote.price_type == "close"
    assert quote.source == ARENA_MARKET_SOURCE  # arena-tagged for the purge

    row = session.get(models.PricingParameterRow, ids["pricing_parameter_rows"]["pr"])
    assert row.instrument_id == ins_id


def test_apply_seed_instruments_reuses_existing_row(tmp_path, session):
    """A pre-existing desk instrument is REUSED (no duplicate) — a blind insert
    would break ensure_underlying's one_or_none lookup."""
    from app import models
    from app.golden_workflows.fixtures import apply_seed

    first = apply_seed(load_fixtures(_quote_seed_bundle(tmp_path)), session)
    second = apply_seed(load_fixtures(_quote_seed_bundle(tmp_path)), session)
    assert second["instruments"]["ins_msft"] == first["instruments"]["ins_msft"]
    count = session.query(models.Instrument).filter_by(symbol="MSFT").count()
    assert count == 1


def test_trader_rfq_seeded_quote_resolves_at_profile_valuation(session):
    """The trader-rfq seed wires MSFT so latest_quote at the profile's
    valuation date returns the pinned 2026-07-16 close (401.10)."""
    from app.golden_workflows.fixtures import apply_seed
    from app.golden_workflows.registry import get_workflow_bundle
    from app.services.quotes import latest_quote

    ids = apply_seed(get_workflow_bundle("trader-rfq-booking-day").fixtures, session)
    ins_id = ids["instruments"]["ins_msft"]
    prof_id = ids["pricing_profiles"]["prof"]
    from app import models
    prof = session.get(models.PricingParameterProfile, prof_id)
    assert prof.valuation_date.date().isoformat() == "2026-07-16"
    quote = latest_quote(session, ins_id, as_of=prof.valuation_date)
    assert quote is not None and float(quote.price) == 401.10


# ---------------------------------------------------------------------------
# Limits namespaces (risk-limit-breach-day)
# ---------------------------------------------------------------------------

def _limits_bundle(portfolio_alias: str, portfolio_name: str) -> dict:
    return {
        "schema_version": 1,
        "seed": {
            "portfolios": [{"alias": portfolio_alias, "name": portfolio_name}],
            "risk_limits": [
                {
                    "alias": "cap",
                    "key": "arena-fixture-net-delta",
                    "name": "Fixture Net Delta Cap",
                    "category": "greek",
                    "owner": "risk_desk",
                }
            ],
            "risk_limit_versions": [
                {
                    "alias": "cap-v1",
                    "risk_limit": "cap",
                    "version": 1,
                    "metric_kind": "delta",
                    "source_kind": "risk_run",
                    "scope_type": "portfolio",
                    "scope_portfolios": [portfolio_alias],
                    "aggregation": "net",
                    "transform": "signed",
                    "comparator": "upper",
                    "warning_upper": 400.0,
                    "hard_upper": 500.0,
                    "unit": "underlying_units",
                    "activated_at": "2026-06-01T00:00:00",
                    "effective_from": "2026-01-01T00:00:00",
                    "activate": True,
                }
            ],
            "limit_monitoring_runs": [
                {
                    "alias": "breach-run",
                    "portfolio": portfolio_alias,
                    "trigger": "manual",
                    "mode": "interactive",
                    "valuation_as_of": "2026-06-23T15:00:00",
                    "source_policy": "reuse_only",
                    "status": "completed",
                    "summary": {"breaches": 1},
                }
            ],
            "limit_source_references": [
                {
                    "alias": "breach-src",
                    "monitoring_run": "breach-run",
                    "source_kind": "risk_run",
                    "source_status": "completed",
                    "is_fresh": True,
                }
            ],
            "limit_evaluations": [
                {
                    "alias": "ev-net-delta",
                    "monitoring_run": "breach-run",
                    "limit_version": "cap-v1",
                    "scope": "portfolio",
                    "scope_portfolio": portfolio_alias,
                    "status": "breach",
                    "observed_value": 612.5,
                    "utilization": 1.225,
                    "warning_upper": 400.0,
                    "hard_upper": 500.0,
                }
            ],
            "limit_incidents": [
                {
                    "alias": "inc",
                    "portfolio": portfolio_alias,
                    "risk_limit": "cap",
                    "scope": "portfolio",
                    "scope_portfolio": portfolio_alias,
                    "severity": "breach",
                    "status": "open",
                    "first_evaluation": "ev-net-delta",
                    "last_evaluation": "ev-net-delta",
                }
            ],
            "limit_incident_events": [
                {
                    "alias": "opened-evt",
                    "incident": "inc",
                    "event_type": "opened",
                    "actor": "system",
                }
            ],
        },
        "replay": {},
    }


def test_limits_namespaces_round_trip(tmp_path, session):
    from app import models
    from app.golden_workflows.fixtures import apply_seed
    from sqlalchemy import select

    first = apply_seed(
        load_fixtures(_write(tmp_path, _limits_bundle("desk", "Limits Book A"))), session
    )
    ev = session.get(models.LimitEvaluation, first["limit_evaluations"]["ev-net-delta"])
    assert ev.scope_key == f"portfolio:{first['portfolios']['desk']}"
    assert ev.scope_type == "portfolio"
    inc = session.get(models.LimitIncident, first["limit_incidents"]["inc"])
    assert inc.status == "open"
    assert [e.event_type for e in inc.events] == ["opened"]

    p2 = tmp_path / "second"
    p2.mkdir()
    second = apply_seed(
        load_fixtures(_write(p2, _limits_bundle("desk2", "Limits Book B"))), session
    )
    limits = list(
        session.execute(
            select(models.RiskLimit).where(
                models.RiskLimit.key == "arena-fixture-net-delta"
            )
        ).scalars()
    )
    assert len(limits) == 1
    version = session.get(
        models.RiskLimitVersion, second["risk_limit_versions"]["cap-v1"]
    )
    assert version.scope_config["portfolio_ids"] == [second["portfolios"]["desk2"]]
    assert limits[0].active_version_id == version.id


def test_seed_refuses_non_arena_limit_key_collision(tmp_path, session):
    from app import models
    from app.golden_workflows.fixtures import apply_seed
    from app.golden_workflows.schema import WorkflowError

    session.add(
        models.RiskLimit(
            key="arena-fixture-net-delta",
            name="Desk Governed Cap",
            category="greek",
            owner="human",
            created_by_actor="desk_user",
        )
    )
    session.flush()
    with pytest.raises(WorkflowError, match="non-arena"):
        apply_seed(
            load_fixtures(_write(tmp_path, _limits_bundle("desk", "Limits Book"))),
            session,
        )


def test_seed_rejects_unprefixed_limit_key(tmp_path, session):
    from app.golden_workflows.fixtures import apply_seed
    from app.golden_workflows.schema import WorkflowError

    bundle = _limits_bundle("desk", "Limits Book")
    bundle["seed"]["risk_limits"][0]["key"] = "desk-net-delta"
    with pytest.raises(WorkflowError, match="arena-"):
        apply_seed(load_fixtures(_write(tmp_path, bundle)), session)


def test_seed_rejects_non_portfolio_scope_type(tmp_path, session):
    """Important-2 (final-review.md): a non-portfolio scope_type fixture limit
    would be immortal, active, and guard-exempt (seeded arena- key), so it must
    fail closed at the loader seam — the same posture limit_evaluations/
    limit_incidents already hold, and the only namespace where scope actually
    controls containment."""
    from app.golden_workflows.fixtures import apply_seed
    from app.golden_workflows.schema import WorkflowError

    bundle = _limits_bundle("desk", "Limits Book")
    bundle["seed"]["risk_limit_versions"][0]["scope_type"] = "underlying"
    with pytest.raises(WorkflowError, match="portfolio"):
        apply_seed(load_fixtures(_write(tmp_path, bundle)), session)


def test_lifecycle_and_settlement_namespaces_seed(tmp_path, session):
    """The two ops-settlement-day namespaces insert with FK resolution, pinned
    ids, and ISO date parsing for the cashflow date columns."""
    import json
    from app import models
    from app.golden_workflows.fixtures import load_fixtures, apply_seed

    bundle_path = tmp_path / "f.json"
    bundle_path.write_text(json.dumps({
        "schema_version": 1,
        "seed": {
            "portfolios": [{"alias": "book", "name": "NS Test Book", "id": 9390}],
            "positions": [{
                "alias": "pos", "portfolio": "book", "underlying": "TEST.SH",
                "product_type": "BarrierOption", "quantity": 10, "id": 9391,
                "status": "closed",
                "product_kwargs": {"strike": 100.0, "option_type": "CALL",
                                   "maturity": 0.5, "barrier": 130.0,
                                   "barrier_type": "UP_OUT"},
            }],
            "position_lifecycle_events": [{
                "alias": "ev", "position": "pos", "event_type": "close",
                "event_data": {"settlement_amount": 111.0},
                "created_at": "2026-08-11T22:00:00",
            }],
            "settlement_cashflows": [{
                "alias": "cf", "position": "pos", "lifecycle_event": "ev",
                "leg_key": "settlement", "direction": "pay", "status": "pending",
                "amount": 111.0, "derived_amount": 111.0,
                "value_date": "2026-08-14", "id": 9392,
            }],
        },
        "replay": {},
    }))
    ids = apply_seed(load_fixtures(bundle_path), session)
    cf = session.get(models.SettlementCashflow, 9392)
    assert cf is not None
    assert cf.position_id == 9391
    assert cf.lifecycle_event_id == ids["position_lifecycle_events"]["ev"]
    assert cf.value_date.isoformat() == "2026-08-14"
    ev = session.get(models.PositionLifecycleEvent, cf.lifecycle_event_id)
    assert ev.event_type == "close"
    assert ev.event_data == {"settlement_amount": 111.0}


# ---------------------------------------------------------------------------
# stage_documents -- the BINARY analogue of artifact_bodies
# ---------------------------------------------------------------------------


def _bundle_with(documents):
    from app.golden_workflows.fixtures import FixtureBundle

    return FixtureBundle(seed={}, replay={}, seed_map={}, documents=documents)


def test_stage_documents_copies_declared_files_into_the_uploads_root(tmp_path):
    """A fixture that DECLARES a document must CREATE it. artifact_bodies writes
    str only, so a PDF needs its own staging path -- otherwise the agent resolves
    a dangling pointer, gets an error, and burns calls hunting a file that was
    never written."""
    from app.golden_workflows.fixtures import stage_documents

    staged = stage_documents(
        _bundle_with(["conf-04-scanned-call-googl.pdf"]), tmp_path
    )
    assert len(staged) == 1
    assert staged[0].parent == tmp_path / "confirmations"
    assert staged[0].name == "conf-04-scanned-call-googl.pdf"
    assert staged[0].read_bytes()[:4] == b"%PDF"


def test_stage_documents_lands_inside_the_tool_containment_root(tmp_path):
    """parse_trade_confirmation refuses anything outside artifact_dir/uploads, so
    the staged path must be under the root it is given."""
    from app.golden_workflows.fixtures import stage_documents

    staged = stage_documents(
        _bundle_with(["conf-09-amended-strike-nvda.pdf"]), tmp_path
    )
    assert staged[0].resolve().is_relative_to(tmp_path.resolve())


def test_stage_documents_is_idempotent_across_trials(tmp_path):
    from app.golden_workflows.fixtures import stage_documents

    bundle = _bundle_with(["conf-04-scanned-call-googl.pdf"])
    first = stage_documents(bundle, tmp_path)
    second = stage_documents(bundle, tmp_path)
    assert first == second
    assert first[0].read_bytes() == second[0].read_bytes()


def test_stage_documents_is_a_noop_for_a_workflow_declaring_none(tmp_path):
    """Every existing workflow declares no documents and must be unaffected."""
    from app.golden_workflows.fixtures import stage_documents

    assert stage_documents(_bundle_with([]), tmp_path) == []
    assert not (tmp_path / "confirmations").exists()


def test_stage_documents_rejects_a_path_escaping_the_corpus(tmp_path):
    """The declared name is a BARE FILENAME in the tracked corpus, never a path:
    anything else lets a fixture reach outside the reviewed document set."""
    import pytest

    from app.golden_workflows.fixtures import stage_documents

    for escape in ("../../../etc/passwd", "sub/conf-04-scanned-call-googl.pdf"):
        with pytest.raises(ValueError):
            stage_documents(_bundle_with([escape]), tmp_path)


def test_stage_documents_raises_for_an_undeclared_document(tmp_path):
    import pytest

    from app.golden_workflows.fixtures import stage_documents

    with pytest.raises(FileNotFoundError):
        stage_documents(_bundle_with(["conf-99-does-not-exist.pdf"]), tmp_path)


def test_every_existing_workflow_declares_no_documents():
    from app.golden_workflows.registry import list_workflow_bundles

    for bundle in list_workflow_bundles():
        if bundle.workflow.id == "confirmation-desk-day":
            continue
        assert bundle.fixtures.documents == [], bundle.workflow.id

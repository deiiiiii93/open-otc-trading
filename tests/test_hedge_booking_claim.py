from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    AgentThread,
    HedgeBookingClaim,
    Portfolio,
    RiskRun,
    SessionArtifact,
    Workflow,
)
from app.services.domains import hedging_strategy as hs


def test_concurrent_claims_allow_only_one_risk_underlying_booking(tmp_path):
    engine = sa.create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'claim.sqlite3'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        portfolio = Portfolio(name="claim race", base_currency="CNY")
        thread = AgentThread(title="claim race", character="risk_manager")
        session.add_all([portfolio, thread])
        session.flush()
        workflow = Workflow(
            thread_id=thread.id,
            title="claim race",
            intent="ad_hoc",
            status="active",
            opened_by="system",
            canonical_snapshot_ids={"scope_kind": "ad_hoc"},
        )
        run = RiskRun(
            portfolio_id=portfolio.id,
            status="completed",
            metrics={"positions": []},
        )
        session.add_all([workflow, run])
        session.flush()
        artifacts = [
            SessionArtifact(
                workflow_id=workflow.id,
                kind="tool_result",
                title=f"proposal {index}",
                tool_name="propose_hedge",
                payload={"content": "{}", "generated_at": "2026-07-20T00:00:00Z"},
            )
            for index in range(2)
        ]
        session.add_all(artifacts)
        session.commit()
        portfolio_id = portfolio.id
        workflow_id = workflow.id
        risk_run_id = run.id
        artifact_ids = [artifact.id for artifact in artifacts]

    barrier = Barrier(2)

    def _claim(source_artifact_id: int) -> tuple[bool, list[str]]:
        with factory() as session:
            barrier.wait(timeout=10)
            claim, reasons = hs._claim_hedge_booking(
                session,
                portfolio_id=portfolio_id,
                underlying="000905.SH",
                risk_run_id=risk_run_id,
                source_artifact_id=source_artifact_id,
                workflow_id=workflow_id,
                strategy="delta_neutral",
                actor="desk_user",
            )
            if claim is None:
                session.rollback()
                return False, reasons
            session.commit()
            return True, []

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_claim, artifact_ids))

    assert sorted(success for success, _reasons in results) == [False, True]
    loser_reasons = next(reasons for success, reasons in results if not success)
    assert set(loser_reasons) & {
        "risk_underlying_already_hedged",
        "hedge_booking_claim_conflict",
    }
    with factory() as session:
        assert session.query(HedgeBookingClaim).count() == 1

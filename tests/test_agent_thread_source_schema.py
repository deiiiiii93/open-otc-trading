from app.models import AgentThread
from app.schemas import AgentThreadOut


def test_agent_thread_out_serialises_source(session):
    thread = AgentThread(title="t", character="trader", source="arena")
    session.add(thread)
    session.commit()
    session.refresh(thread)
    out = AgentThreadOut.model_validate(thread)
    assert out.source == "arena"


def test_agent_thread_out_source_defaults_to_desk(session):
    # A thread created without an explicit source falls back to the
    # server_default 'desk' after the DB round-trip.
    thread = AgentThread(title="t", character="trader")
    session.add(thread)
    session.commit()
    session.refresh(thread)
    out = AgentThreadOut.model_validate(thread)
    assert out.source == "desk"

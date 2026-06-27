"""The /goal router is mounted in the real app and reads goal state off the thread row.

Uses the live `client` fixture (its AgentService has no LLM in tests, so framing isn't
exercised here — that path is covered with a fake model in test_goal_api.py). This proves
the route exists, the DB-backed GoalRunService is wired, and GET reflects the thread row.
"""


def test_get_goal_on_fresh_thread_is_null(client, agent_thread_factory, session):
    thread = agent_thread_factory()
    session.commit()
    r = client.get(f"/api/chat/threads/{thread.id}/goal")
    assert r.status_code == 200
    assert r.json() is None


def test_get_goal_reflects_persisted_run_row(client, agent_thread_factory, session):
    from app.services.deep_agent.goal_mode import new_goal_run

    thread = agent_thread_factory()
    # Seed a running goal run directly on the thread row (the framer path needs an LLM).
    state = new_goal_run(goal_run_id=str(thread.id), contract_hash="abc", mode="auto")
    thread.goal_run = state.model_dump(mode="json")
    session.commit()

    r = client.get(f"/api/chat/threads/{thread.id}/goal")
    assert r.status_code == 200
    body = r.json()
    assert body is not None
    assert body["goal_run_id"] == str(thread.id)

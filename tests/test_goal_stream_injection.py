"""_drive_stream merges the goal rubric into the invocation state (spec §G): the attached
RubricMiddleware reads `state["rubric"]`, so the kickoff must carry the fragment into the
astream_events payload, not just the prompt string."""
import asyncio

from langchain_core.messages import HumanMessage

from app.services.agents import AgentService, StreamCollector


class _CapturingAgent:
    """Fake orchestrator that records the invocation payload and yields no events."""

    def __init__(self):
        self.seen_payload = None

    def astream_events(self, payload, *, config=None, version=None, **_kwargs):
        self.seen_payload = payload

        async def _empty():
            return
            yield  # pragma: no cover - makes this an async generator

        return _empty()


def _collect(agent, svc, extra_state):
    async def _run():
        collector = StreamCollector()
        async for _ in svc._drive_stream(
            agent, "do the thing", {}, collector, extra_state=extra_state
        ):
            pass

    asyncio.run(_run())


def test_rubric_fragment_lands_in_invocation_state(settings):
    svc = AgentService(settings=settings)
    agent = _CapturingAgent()
    _collect(agent, svc, {"rubric": "C1: latest risk run on Control"})
    assert agent.seen_payload is not None
    assert agent.seen_payload["rubric"] == "C1: latest risk run on Control"
    # The human message is still present alongside the injected state.
    assert isinstance(agent.seen_payload["messages"][0], HumanMessage)


def test_no_fragment_leaves_payload_rubric_free(settings):
    svc = AgentService(settings=settings)
    agent = _CapturingAgent()
    _collect(agent, svc, None)
    assert agent.seen_payload is not None
    assert "rubric" not in agent.seen_payload

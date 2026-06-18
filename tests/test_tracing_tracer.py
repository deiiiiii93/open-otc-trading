"""LocalTracer: real callback sequences -> persisted span tree."""
from __future__ import annotations

import uuid

import pytest

from app.services.tracing.store import TraceStore
from app.services.tracing.tracer import LocalTracer, extract_token_usage


@pytest.fixture()
def store(tmp_path):
    s = TraceStore(tmp_path / "traces.sqlite3")
    yield s
    s.close()


def _drive_nested_run(tracer: LocalTracer) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Simulate chain -> (tool, llm) via the public callback API BaseTracer exposes."""
    root_id, tool_id, llm_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    tracer.on_chain_start({"name": "orchestrator"}, {"q": "price it"}, run_id=root_id)
    tracer.on_tool_start({"name": "price_position"}, "AAPL 100", run_id=tool_id,
                         parent_run_id=root_id)
    tracer.on_tool_end("priced ok", run_id=tool_id)
    tracer.on_llm_start({"name": "ChatAnthropic"}, ["prompt text"], run_id=llm_id,
                        parent_run_id=root_id)
    from langchain_core.outputs import Generation, LLMResult
    tracer.on_llm_end(
        LLMResult(
            generations=[[Generation(text="answer")]],
            llm_output={"token_usage": {"prompt_tokens": 11,
                                        "completion_tokens": 4,
                                        "total_tokens": 15}},
        ),
        run_id=llm_id,
    )
    tracer.on_chain_end({"answer": "done"}, run_id=root_id)
    return root_id, tool_id, llm_id


def test_nested_run_tree_persisted(store):
    tracer = LocalTracer(store, thread_id=42)
    root_id, tool_id, llm_id = _drive_nested_run(tracer)
    store.flush()

    runs = store.get_trace(str(root_id))
    assert [r["run_type"] for r in runs] == ["chain", "tool", "llm"]
    by_id = {r["id"]: r for r in runs}
    assert by_id[str(tool_id)]["parent_run_id"] == str(root_id)
    assert by_id[str(root_id)]["thread_id"] == 42
    assert by_id[str(llm_id)]["thread_id"] == 42
    assert all(r["status"] == "success" for r in runs)
    assert by_id[str(llm_id)]["prompt_tokens"] == 11
    # Root aggregated descendant tokens on finalize.
    assert by_id[str(root_id)]["total_tokens"] == 15
    # Full-fidelity payloads.
    assert "price it" in by_id[str(root_id)]["inputs"]
    assert "priced ok" in by_id[str(tool_id)]["outputs"]


def test_error_run_persisted(store):
    tracer = LocalTracer(store, thread_id=1)
    root_id = uuid.uuid4()
    tracer.on_chain_start({"name": "orchestrator"}, {"q": 1}, run_id=root_id)
    tracer.on_chain_error(ValueError("bad terms"), run_id=root_id)
    store.flush()
    run = store.get_run(str(root_id))
    assert run["status"] == "error"
    assert "bad terms" in run["error"]


def test_store_failure_never_raises(store, monkeypatch):
    tracer = LocalTracer(store, thread_id=1)
    monkeypatch.setattr(store, "enqueue_insert",
                        lambda *_: (_ for _ in ()).throw(RuntimeError("disk full")))
    # Must not propagate into the agent run.
    tracer.on_chain_start({"name": "x"}, {"q": 1}, run_id=uuid.uuid4())


def test_extract_token_usage_variants():
    assert extract_token_usage(
        {"llm_output": {"token_usage": {"prompt_tokens": 1, "completion_tokens": 2,
                                        "total_tokens": 3}}}
    ) == (1, 2, 3)
    # usage_metadata path (Anthropic-style message payload, dumpd form)
    assert extract_token_usage(
        {"generations": [[{"message": {"kwargs": {"usage_metadata": {
            "input_tokens": 5, "output_tokens": 6, "total_tokens": 11}}}}]]}
    ) == (5, 6, 11)
    assert extract_token_usage(None) == (None, None, None)
    assert extract_token_usage({"generations": [[{"text": "x"}]]}) == (None, None, None)

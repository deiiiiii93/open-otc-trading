from __future__ import annotations

from app.services.agents import _capture_reply_options_from_tool_end
from app.services.deep_agent.stream_collector import StreamCollector


def _seed_args(collector: StreamCollector, run_id: str, options: list[dict]) -> None:
    collector.reply_options_args[run_id] = options
    collector.on_tool_start(
        run_id, "propose_reply_options", {"options": options}, started_at=0.0
    )


def test_capture_sets_options_on_success():
    collector = StreamCollector()
    _seed_args(
        collector,
        "r1",
        [{"label": "Yes"}, {"label": "No", "description": "Stop"}],
    )
    _capture_reply_options_from_tool_end(
        collector, run_id="r1", name="propose_reply_options", error_text=None
    )
    assert collector.reply_options == [
        {"label": "Yes"},
        {"label": "No", "description": "Stop"},
    ]


def test_capture_ignores_other_tool_names():
    collector = StreamCollector()
    _seed_args(collector, "r1", [{"label": "Yes"}, {"label": "No"}])
    _capture_reply_options_from_tool_end(
        collector, run_id="r1", name="get_positions", error_text=None
    )
    assert collector.reply_options is None


def test_capture_skips_on_tool_error_and_preserves_prior():
    collector = StreamCollector()
    _seed_args(collector, "r1", [{"label": "Yes"}, {"label": "No"}])
    _capture_reply_options_from_tool_end(
        collector, run_id="r1", name="propose_reply_options", error_text=None
    )
    prior = collector.reply_options
    _seed_args(collector, "r2", [{"label": ""}, {"label": ""}])
    _capture_reply_options_from_tool_end(
        collector,
        run_id="r2",
        name="propose_reply_options",
        error_text="validation failed",
    )
    assert collector.reply_options == prior


def test_capture_skips_when_fewer_than_two_normalize():
    collector = StreamCollector()
    _seed_args(collector, "r1", [{"label": "Yes"}, {"label": ""}])
    _capture_reply_options_from_tool_end(
        collector, run_id="r1", name="propose_reply_options", error_text=None
    )
    assert collector.reply_options is None


def test_capture_last_call_wins():
    collector = StreamCollector()
    _seed_args(collector, "r1", [{"label": "A"}, {"label": "B"}])
    _capture_reply_options_from_tool_end(
        collector, run_id="r1", name="propose_reply_options", error_text=None
    )
    _seed_args(collector, "r2", [{"label": "C"}, {"label": "D"}, {"label": "E"}])
    _capture_reply_options_from_tool_end(
        collector, run_id="r2", name="propose_reply_options", error_text=None
    )
    assert collector.reply_options == [
        {"label": "C"},
        {"label": "D"},
        {"label": "E"},
    ]


def test_capture_clamps_to_max_five():
    collector = StreamCollector()
    _seed_args(
        collector,
        "r1",
        [{"label": f"opt{i}"} for i in range(7)],
    )
    _capture_reply_options_from_tool_end(
        collector, run_id="r1", name="propose_reply_options", error_text=None
    )
    assert collector.reply_options is not None
    assert len(collector.reply_options) == 5


def test_capture_handles_missing_args_entry():
    collector = StreamCollector()
    _capture_reply_options_from_tool_end(
        collector,
        run_id="never_started",
        name="propose_reply_options",
        error_text=None,
    )
    assert collector.reply_options is None


def test_capture_skips_when_options_not_list():
    collector = StreamCollector()
    collector.tool_events["r1"] = {"args": {"options": "not a list"}}
    _capture_reply_options_from_tool_end(
        collector, run_id="r1", name="propose_reply_options", error_text=None
    )
    assert collector.reply_options is None


def test_capture_skips_when_args_not_dict():
    collector = StreamCollector()
    collector.tool_events["r1"] = {"args": "not a dict"}
    _capture_reply_options_from_tool_end(
        collector, run_id="r1", name="propose_reply_options", error_text=None
    )
    assert collector.reply_options is None


def test_capture_uses_pre_truncation_args_for_large_options():
    """Args larger than the _truncate threshold (1000 bytes) must still be
    captured, because the orchestrator stashes raw options at on_tool_start
    BEFORE truncation."""
    collector = StreamCollector()
    big_description = "x" * 240
    big_value = "x" * 400
    options = [
        {"label": f"opt{i}", "description": big_description, "value": big_value}
        for i in range(5)
    ]
    # Simulate the orchestrator capture path: stash raw, then call on_tool_start
    # which wraps args with _truncate (the regular event tracker).
    collector.reply_options_args["r1"] = options
    collector.on_tool_start("r1", "propose_reply_options", {"options": options}, started_at=0.0)
    # Sanity: the args in tool_events ARE truncated (envelope shape).
    truncated = collector.tool_events["r1"]["args"]
    assert isinstance(truncated, dict) and truncated.get("_truncated") is True

    _capture_reply_options_from_tool_end(
        collector, run_id="r1", name="propose_reply_options", error_text=None
    )
    assert collector.reply_options is not None
    assert len(collector.reply_options) == 5
    assert collector.reply_options[0]["label"] == "opt0"
    assert collector.reply_options[0]["description"] == big_description
    assert collector.reply_options[0]["value"] == big_value

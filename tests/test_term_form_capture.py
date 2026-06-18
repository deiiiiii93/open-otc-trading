from __future__ import annotations

from app.services.agents import (
    _TERM_FORM_MAX_FIELDS,
    _capture_term_form_from_tool_end,
)
from app.services.deep_agent.stream_collector import StreamCollector

_FIELDS = [
    {"key": "initial_price", "label": "Initial fixing S0", "type": "number",
     "default": {"label": "spot 8359.56", "value": 8359.56}},
    {"key": "ko_barrier_pct", "label": "KO barrier", "type": "percent",
     "choices": [{"label": "103%", "value": 103}]},
]


def _payload():
    return {"title": "Finish booking", "subtitle": "pf 6", "fields": _FIELDS,
            "submit_label": "Review & book"}


def test_capture_writes_normalized_term_form():
    c = StreamCollector()
    c.term_form_args["run-1"] = _payload()
    _capture_term_form_from_tool_end(
        c, run_id="run-1", name="propose_term_form", error_text=None
    )
    assert c.term_form is not None
    assert c.term_form["title"] == "Finish booking"
    assert [f["key"] for f in c.term_form["fields"]] == ["initial_price", "ko_barrier_pct"]
    assert c.term_form["fields"][0]["default"]["value"] == 8359.56


def test_capture_ignores_other_tools():
    c = StreamCollector()
    c.term_form_args["run-1"] = _payload()
    _capture_term_form_from_tool_end(
        c, run_id="run-1", name="propose_reply_options", error_text=None
    )
    assert c.term_form is None


def test_capture_skips_on_tool_error():
    c = StreamCollector()
    c.term_form_args["run-1"] = _payload()
    _capture_term_form_from_tool_end(
        c, run_id="run-1", name="propose_term_form", error_text="boom"
    )
    assert c.term_form is None


def test_capture_drops_malformed_fields_and_blanks_when_empty():
    c = StreamCollector()
    c.term_form_args["run-1"] = {"title": "t", "fields": ["nope", {"no": "key"}]}
    _capture_term_form_from_tool_end(
        c, run_id="run-1", name="propose_term_form", error_text=None
    )
    assert c.term_form is None


def test_capture_caps_fields():
    c = StreamCollector()
    many = [{"key": f"k{i}", "label": f"L{i}", "type": "text"} for i in range(_TERM_FORM_MAX_FIELDS + 3)]
    c.term_form_args["run-1"] = {"title": "t", "fields": many}
    _capture_term_form_from_tool_end(
        c, run_id="run-1", name="propose_term_form", error_text=None
    )
    assert c.term_form is not None
    assert len(c.term_form["fields"]) == _TERM_FORM_MAX_FIELDS


def test_capture_error_preserves_prior_term_form():
    c = StreamCollector()
    c.term_form_args["run-1"] = _payload()
    _capture_term_form_from_tool_end(
        c, run_id="run-1", name="propose_term_form", error_text=None
    )
    prior = c.term_form
    assert prior is not None
    # a later errored call must NOT wipe the stored payload
    c.term_form_args["run-2"] = _payload()
    _capture_term_form_from_tool_end(
        c, run_id="run-2", name="propose_term_form", error_text="boom"
    )
    assert c.term_form is prior

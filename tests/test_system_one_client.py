"""System One client (spec 2026-09-21 §0). No test makes a live call."""
from __future__ import annotations

import datetime as dt
import decimal
import json

import pytest

from app.config import Settings
from app.services.system_one import client as s1
from app.services.system_one.client import (
    UNAVAILABLE_REASONS, Choice, Noul, Score, ScoreAnswer, SystemOneUnavailable,
    cap, is_enabled, question_payload, serialize_state, validate_questions,
)


# --- types, validation, helpers -------------------------------------------

def test_unavailable_reasons_are_the_spec_five():
    assert UNAVAILABLE_REASONS == (
        "no_key", "state_too_large", "timeout", "http_error", "bad_response")


def test_unavailable_rejects_an_unknown_reason():
    with pytest.raises(ValueError):
        SystemOneUnavailable("flaky")


def test_score_normalized_is_score_over_levels_minus_one():
    assert ScoreAnswer(score=1.98, confidence=0.98, probabilities={}, levels=3).normalized == pytest.approx(0.99)
    assert ScoreAnswer(score=1.5, confidence=0.5, probabilities={}, levels=4).normalized == pytest.approx(0.5)


def test_question_payload_shapes():
    assert question_payload(Noul("x is true")) == {"type": "noul", "instructions": "x is true"}
    assert question_payload(Choice("which", {"a": "A", "b": "B"})) == {
        "type": "choice", "instructions": "which", "criteria": {"a": "A", "b": "B"}}
    assert question_payload(Score("how", ("lo", "hi"))) == {
        "type": "score", "instructions": "how", "criteria": ["lo", "hi"]}


@pytest.mark.parametrize("questions", [
    {},
    {"q": Noul("")},
    {"q": Noul("   ")},
    {"": Noul("x")},
    {"q": Score("s", ["only one"])},
    {"q": Score("s", [str(i) for i in range(11)])},
    {"q": Score("s", ["lo", ""])},
    {"q": Choice("c", {"a": "A"})},
    {"q": Choice("c", {str(i): "d" for i in range(256)})},
    {"q": Choice("c", {"a": "A", "": "B"})},
    {"q": Choice("c", {"a": "A", "b": ""})},
    {"q": "not a question"},
])
def test_programmer_errors_raise_value_error(questions):
    """The server answers a malformed body with an opaque 400, so catch it here."""
    with pytest.raises(ValueError):
        validate_questions(questions)


def test_valid_bounds_pass():
    validate_questions({
        "a": Noul("x"),
        "b": Score("s", ["lo", "hi"]),
        "c": Score("s", [str(i) for i in range(10)]),
        "d": Choice("c", {"a": "A", "b": "B"}),
        "e": Choice("c", {str(i): "d" for i in range(255)}),
    })


def test_cap_is_exact_length_and_marked():
    assert cap("abc", 3) == "abc"
    assert cap("abcdef", 4) == "abc…"
    assert len(cap("x" * 5000, 4000)) == 4000


def test_serialize_state_is_compact_sorted_non_ascii_and_total():
    out = serialize_state({"b": "é", "a": [1, dt.date(2026, 9, 21), decimal.Decimal("1.5")]})
    assert out == '{"a":[1,"2026-09-21","1.5"],"b":"é"}'


def test_is_enabled_reads_only_the_master_switch():
    assert is_enabled(Settings(system_one_enabled=True, tool_guard_mode="off")) is True
    assert is_enabled(Settings(system_one_enabled=False)) is False


# --- ask() ------------------------------------------------------------------

import copy  # noqa: E402

import requests  # noqa: E402

from _system_one_fakes import PROBE_RESPONSE, FakePost  # noqa: E402
from app.services.system_one.client import ask, requests_post  # noqa: E402

QUESTIONS = {
    "q_noul": Noul("The user asked for this action"),
    "q_score": Score("How durable is this fact?", ["low", "mid", "high"]),
    "q_choice": Choice("Which desk owns this?", {
        "ops": "operations", "trader": "trading", "risk": "risk management"}),
}


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")


def test_request_shape():
    post = FakePost()
    ask({"x": 1}, QUESTIONS, post=post)
    url, payload, timeout = post.calls[0]
    assert url == "https://zenmux.ai/api/v1/systemone"
    assert timeout == 5.0
    assert payload["model"] == "typesafe/jev-1.13"
    assert payload["state"] == {"x": 1}
    assert payload["questions"]["q_noul"] == {"type": "noul", "instructions": "The user asked for this action"}
    assert payload["questions"]["q_score"]["criteria"] == ["low", "mid", "high"]
    assert payload["questions"]["q_choice"]["criteria"]["ops"] == "operations"


def test_parses_the_three_verbatim_probe_shapes():
    result = ask({"x": 1}, QUESTIONS, post=FakePost())
    assert result.model == "typesafe/jev-1.13"
    assert result.answers["q_noul"].probability == 0.95
    score = result.answers["q_score"]
    assert (score.score, score.confidence, score.levels) == (1.98, 0.98, 3)
    assert score.normalized == pytest.approx(0.99)
    assert score.probabilities == {"0": 0.0, "1": 0.01, "2": 0.99}
    choice = result.answers["q_choice"]
    assert (choice.choice, choice.confidence) == ("ops", 1.0)
    assert choice.probabilities == {"ops": 1.0, "trader": 0.0, "risk": 0.0}
    assert isinstance(result.latency_ms, int)


def test_model_falls_back_to_the_requested_one():
    body = copy.deepcopy(PROBE_RESPONSE)
    del body["model"]
    assert ask({}, QUESTIONS, post=FakePost(body)).model == "typesafe/jev-1.13"


def test_extra_answer_keys_legend_and_usage_are_ignored():
    body = copy.deepcopy(PROBE_RESPONSE)
    body["answers"]["unasked"] = {"type": "noul", "noul": 7}
    assert ask({}, QUESTIONS, post=FakePost(body)).answers.keys() == QUESTIONS.keys()


def test_a_missing_probability_key_reads_as_zero():
    body = copy.deepcopy(PROBE_RESPONSE)
    del body["answers"]["q_choice"]["probabilities"]["risk"]
    del body["answers"]["q_score"]["probabilities"]["0"]
    result = ask({}, QUESTIONS, post=FakePost(body))
    assert result.answers["q_choice"].probabilities["risk"] == 0.0
    assert result.answers["q_score"].probabilities["0"] == 0.0


def _mutate(path_fn):
    body = copy.deepcopy(PROBE_RESPONSE)
    path_fn(body)
    return body


@pytest.mark.parametrize("body", [
    "not an object",
    _mutate(lambda b: b.pop("answers")),
    _mutate(lambda b: b.__setitem__("answers", [])),
    _mutate(lambda b: b["answers"].pop("q_noul")),
    _mutate(lambda b: b["answers"]["q_noul"].__setitem__("type", "score")),
    _mutate(lambda b: b["answers"]["q_noul"].__setitem__("noul", 1.2)),
    _mutate(lambda b: b["answers"]["q_noul"].__setitem__("noul", "0.5")),
    _mutate(lambda b: b["answers"]["q_noul"].__setitem__("noul", True)),
    _mutate(lambda b: b["answers"]["q_noul"].__setitem__("noul", float("nan"))),
    _mutate(lambda b: b["answers"]["q_score"].__setitem__("score", 2.01)),
    _mutate(lambda b: b["answers"]["q_score"].__setitem__("confidence", 1.5)),
    _mutate(lambda b: b["answers"]["q_score"].pop("probabilities")),
    _mutate(lambda b: b["answers"]["q_score"]["probabilities"].__setitem__("3", 0)),
    _mutate(lambda b: b["answers"]["q_choice"].__setitem__("choice", "board")),
    _mutate(lambda b: b["answers"]["q_choice"].__setitem__("choice", ["ops"])),
    _mutate(lambda b: b["answers"]["q_choice"].__setitem__("probabilities", [1, 0, 0])),
    _mutate(lambda b: b["answers"]["q_choice"]["probabilities"].__setitem__("board", 0)),
    _mutate(lambda b: b["answers"]["q_choice"]["probabilities"].pop("ops")),
])
def test_every_validation_rule_is_bad_response(body):
    with pytest.raises(SystemOneUnavailable) as exc:
        ask({}, QUESTIONS, post=FakePost(body))
    assert exc.value.reason == "bad_response"
    assert isinstance(exc.value.latency_ms, int)


def test_no_key(monkeypatch):
    monkeypatch.delenv("ZENMUX_API_KEY")
    post = FakePost()
    with pytest.raises(SystemOneUnavailable) as exc:
        ask({}, QUESTIONS, post=post)
    assert (exc.value.reason, exc.value.latency_ms) == ("no_key", None)
    assert post.calls == []


def test_state_budget_counts_code_points_after_sanitizing_and_never_truncates():
    at_budget = Settings(system_one_max_state_chars=10)
    post = FakePost()
    ask("é" * 8, QUESTIONS, post=post, settings=at_budget)      # '"éééééééé"' = 10
    assert post.calls[0][1]["state"] == "é" * 8                  # sent whole
    with pytest.raises(SystemOneUnavailable) as exc:
        ask("é" * 9, QUESTIONS, post=post, settings=at_budget)
    assert (exc.value.reason, exc.value.latency_ms) == ("state_too_large", None)
    assert len(post.calls) == 1


@pytest.mark.parametrize("error, reason", [
    (TimeoutError("slow"), "timeout"),
    (ValueError("Expecting value: line 1 column 1"), "bad_response"),
    (json.JSONDecodeError("bad", "doc", 0), "bad_response"),
    (RuntimeError("HTTP 502: bad gateway"), "http_error"),
    (ConnectionError("refused"), "http_error"),
])
def test_transport_errors_map_deterministically(error, reason):
    with pytest.raises(SystemOneUnavailable) as exc:
        ask({}, QUESTIONS, post=FakePost(exc=error))
    assert exc.value.reason == reason
    assert isinstance(exc.value.latency_ms, int)
    assert exc.value.detail and len(exc.value.detail) <= 500


def test_sanitizer_masks_state_but_never_touches_questions():
    token = "sk-" + "Z" * 24
    questions = {"q_noul": Noul("password: shown here is part of the predicate")}
    body = {"answers": {"q_noul": {"type": "noul", "noul": 0.1}}}
    post = FakePost(body)
    ask({"note": f"use {token}", "nested": {"api_key": "zzz"}}, questions, post=post)
    sent = post.calls[0][1]
    assert token not in json.dumps(sent["state"])
    assert sent["state"]["nested"]["api_key"] == "[REDACTED]"
    assert sent["questions"]["q_noul"]["instructions"] == "password: shown here is part of the predicate"


def test_state_objects_are_sent_after_the_default_str_round_trip():
    post = FakePost({"answers": {"q_noul": {"type": "noul", "noul": 0.5}}})
    ask({"when": dt.datetime(2026, 9, 21, 9, 30)}, {"q_noul": Noul("x")}, post=post)
    assert post.calls[0][1]["state"] == {"when": "2026-09-21 09:30:00"}


def test_ask_does_not_read_the_master_switch(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "false")
    assert ask({}, QUESTIONS, post=FakePost()).answers["q_noul"].probability == 0.95


def test_ask_validates_questions_before_anything_else(monkeypatch):
    monkeypatch.delenv("ZENMUX_API_KEY")
    with pytest.raises(ValueError):
        ask({}, {}, post=FakePost())


# --- requests_post translation --------------------------------------------

class _Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        if self._body is None:
            raise requests.exceptions.JSONDecodeError("Expecting value", "", 0)
        return self._body


def test_requests_post_translates_failures(monkeypatch):
    def raise_(exc):
        def _post(*a, **k):
            raise exc
        return _post

    monkeypatch.setattr(requests, "post", raise_(requests.Timeout("t")))
    with pytest.raises(TimeoutError):
        requests_post("http://x/systemone", {}, 1.0)
    monkeypatch.setattr(requests, "post", raise_(requests.ConnectionError("c")))
    with pytest.raises(RuntimeError):
        requests_post("http://x/systemone", {}, 1.0)
    monkeypatch.setattr(requests, "post", raise_(requests.exceptions.InvalidURL("u")))
    with pytest.raises(RuntimeError):   # NOT ValueError: it is not a body problem
        requests_post("http://x/systemone", {}, 1.0)
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(502, text="bad gateway"))
    with pytest.raises(RuntimeError, match="HTTP 502"):
        requests_post("http://x/systemone", {}, 1.0)
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(200))
    with pytest.raises(ValueError):
        requests_post("http://x/systemone", {}, 1.0)


def test_requests_post_sends_the_bearer_key(monkeypatch):
    seen = {}

    def _post(url, json, headers, timeout):
        seen.update(url=url, headers=headers, timeout=timeout)
        return _Resp(200, {"ok": True})

    monkeypatch.setattr(requests, "post", _post)
    assert requests_post("http://x/systemone", {"a": 1}, 2.0) == {"ok": True}
    assert seen["headers"]["Authorization"] == "Bearer test-key"
    assert seen["timeout"] == 2.0

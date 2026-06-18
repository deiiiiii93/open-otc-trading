from __future__ import annotations

from app.services.deep_agent.stream_collector import StreamCollector


def test_reply_options_defaults_to_none():
    collector = StreamCollector()
    assert collector.reply_options is None


def test_reply_options_is_mutable_per_instance():
    a = StreamCollector()
    b = StreamCollector()
    a.reply_options = [{"label": "Yes"}, {"label": "No"}]
    assert b.reply_options is None

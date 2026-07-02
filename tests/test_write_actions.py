"""Shared write-action classifier (audit spec §5.1a)."""
from app.services.deep_agent.envelopes import ToolGroup
from app.services.deep_agent.write_actions import (
    classify_write_action,
    write_names_by_class,
)


class _FakeTool:
    def __init__(self, name, group=None):
        self.name = name
        if group is not None:
            self.__capability_group__ = group


def test_write_names_by_class_maps_gated_groups():
    tools = [
        _FakeTool("book_position", ToolGroup.DOMAIN_WRITE),
        _FakeTool("start_async_agent", ToolGroup.ASYNC_DISPATCH),
        _FakeTool("propose_reply_options", ToolGroup.PAGE_ACTION),
        _FakeTool("get_position_summaries"),  # ungated read
        _FakeTool("list_positions", ToolGroup.DOMAIN_READ),
    ]
    assert write_names_by_class(tools) == {
        "book_position": "domain_write",
        "start_async_agent": "async_dispatch",
        "propose_reply_options": "page_action",
    }


def test_classify_fs_and_artifact_writes():
    gated = {}
    assert classify_write_action("write_file", {}, gated, include_page_action=False) == "fs_write"
    assert classify_write_action("edit_file", {}, gated, include_page_action=False) == "fs_write"
    assert classify_write_action("execute", {}, gated, include_page_action=False) == "fs_write"
    assert classify_write_action(
        "run_python", {"writes_artifacts": True}, gated, include_page_action=False
    ) == "artifact_write"
    assert classify_write_action("run_python", {}, gated, include_page_action=False) is None
    assert classify_write_action("read_file", {}, gated, include_page_action=False) is None


def test_page_action_included_only_for_fanout_consumer():
    gated = {"propose_reply_options": "page_action", "book_position": "domain_write"}
    assert classify_write_action("propose_reply_options", {}, gated, include_page_action=False) is None
    assert classify_write_action("propose_reply_options", {}, gated, include_page_action=True) == "page_action"
    assert classify_write_action("book_position", {}, gated, include_page_action=False) == "domain_write"

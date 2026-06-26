"""The grader tool allowlist is exactly the DOMAIN_READ-grouped tools (GOAL_GRADER_READ).

This is what create_app feeds GoalRunService so the framer can only reference tools the
grader is actually permitted to call under the fail-closed GOAL_GRADER_READ envelope.
"""
from app.services.deep_agent.envelopes import ToolGroup
from app.services.deep_agent.goal_mode import goal_grader_tool_allowlist


class _Tool:
    def __init__(self, name, group):
        self.name = name
        if group is not None:
            self.__capability_group__ = group


def test_keeps_only_domain_read_tools():
    tools = [
        _Tool("get_latest_risk_run", ToolGroup.DOMAIN_READ),
        _Tool("list_positions", ToolGroup.DOMAIN_READ),
        _Tool("book_trade", ToolGroup.DOMAIN_WRITE),
        _Tool("start_async_agent", ToolGroup.ASYNC_DISPATCH),
        _Tool("read_file", None),  # ungated -> excluded
    ]
    assert goal_grader_tool_allowlist(tools) == {"get_latest_risk_run", "list_positions"}


def test_empty_when_no_domain_read_tools():
    assert goal_grader_tool_allowlist([_Tool("book_trade", ToolGroup.DOMAIN_WRITE)]) == set()

"""Tool-level capability gate.

Each tool wrapped with ``@capability_gated(group=ToolGroup.X)`` checks the
``envelope`` value passed through ``RunnableConfig.configurable`` at invoke
time. If the group is not allowed under the current envelope, the gate
raises ``CapabilityDeniedError``. The escalation engine catches this
exception and decides whether to retry the turn under a widened envelope.

Default behavior when the envelope is missing: fail closed to PET_PAGE.

Supports both function-based ``@tool("...")`` definitions and class-based
``BaseTool`` subclass instances (e.g. ``StartAsyncAgentTool()``). For the
former, stack the decorators so ``capability_gated`` is outermost:

    @capability_gated(group=ToolGroup.DOMAIN_WRITE)
    @tool("create_portfolio", args_schema=CreatePortfolioInput)
    def create_portfolio_tool(...):
        ...

For the latter, wrap the instance:

    gated = capability_gated(group=ToolGroup.ASYNC_DISPATCH)(StartAsyncAgentTool())
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from langchain_core.runnables.config import RunnableConfig
from langchain_core.tools import BaseTool

from .envelopes import Envelope, ToolGroup, tool_allowed


# Tools whose estimated runtime exceeds this threshold must surface a
# cost-preview confirmation to the user. Set by spec (≥30s = long-running).
LONG_RUNNING_SECONDS = 30.0
COST_ESTIMATOR_ATTR = "__cost_estimator__"

# Key under ``config.configurable`` for a mutable list the runtime drops in
# before a turn. The gate appends denials/cost-previews here. This is the ONLY
# reliable channel out of a persona subagent: deepagents invokes subagents
# imperatively inside the ``task`` tool, so their inner tool events never reach
# the parent ``astream_events`` stream — but ``configurable`` IS forwarded into
# the subagent, and a mutable list placed there is shared by reference, so a
# write from inside the subagent is visible to the parent after the turn.
RUNTIME_SIGNAL_SINK_KEY = "__runtime_signals__"


def _record_runtime_signal(config: RunnableConfig | None, signal: dict[str, Any]) -> None:
    """Append a structured runtime signal to the configurable sink, if present.

    Best-effort: a missing/!list sink (e.g. a direct unit-test call) is a no-op.
    """
    try:
        configurable = (config or {}).get("configurable") or {}
        sink = configurable.get(RUNTIME_SIGNAL_SINK_KEY)
        if isinstance(sink, list):
            sink.append(signal)
    except Exception:  # pragma: no cover — recording must never break a tool call
        pass


class CapabilityDeniedError(Exception):
    """Raised by the gate when a tool group is not allowed under the envelope."""

    def __init__(
        self,
        *,
        envelope: Envelope,
        group: ToolGroup,
        tool_name: str,
    ) -> None:
        super().__init__(
            f"tool '{tool_name}' (group={group.value}) denied under envelope "
            f"'{envelope.value}'"
        )
        self.envelope = envelope
        self.group = group
        self.tool_name = tool_name


class CostPreviewRequiredError(Exception):
    """Raised by the gate when a tool's estimated runtime exceeds the threshold.

    The runtime catches this, surfaces a confirmation to the user, and
    re-invokes with ``configurable.confirmed_cost_preview=True`` if the
    user approves.
    """

    def __init__(self, *, tool_name: str, estimated_seconds: float) -> None:
        super().__init__(
            f"tool '{tool_name}' estimated at {estimated_seconds:.1f}s exceeds "
            f"{LONG_RUNNING_SECONDS:.0f}s threshold; explicit confirmation required"
        )
        self.tool_name = tool_name
        self.estimated_seconds = estimated_seconds


class ToolScopeDeniedError(Exception):
    """Raised when a task-scoped worker calls a tool outside its whitelist."""

    def __init__(self, *, tool_name: str, tools_scope: tuple[str, ...]) -> None:
        super().__init__(
            f"tool '{tool_name}' denied outside task tools_scope "
            f"{list(tools_scope)!r}"
        )
        self.tool_name = tool_name
        self.tools_scope = tools_scope


def _envelope_from_config(config: RunnableConfig | None) -> Envelope:
    if not config:
        return Envelope.PET_PAGE
    configurable = config.get("configurable") or {}
    raw = configurable.get("envelope")
    if not raw:
        return Envelope.PET_PAGE
    try:
        return Envelope(raw)
    except ValueError:
        return Envelope.PET_PAGE


def _confirmed_cost_preview(config: RunnableConfig | None) -> bool:
    if not config:
        return False
    configurable = config.get("configurable") or {}
    return bool(configurable.get("confirmed_cost_preview"))


def _tools_scope_from_config(config: RunnableConfig | None) -> tuple[str, ...] | None:
    if not config:
        return None
    configurable = config.get("configurable") or {}
    raw = configurable.get("tools_scope")
    if raw is None:
        return None
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, (list, tuple, set)):
        return tuple(str(item) for item in raw)
    return None


def _check_tools_scope(tool_name: str, config: RunnableConfig | None) -> None:
    tools_scope = _tools_scope_from_config(config)
    if tools_scope is None or tool_name in tools_scope:
        return
    raise ToolScopeDeniedError(tool_name=tool_name, tools_scope=tools_scope)


def _unwrap_tool_args(tool_input: Any) -> Any:
    """Return the plain-args dict the estimator expects.

    LangGraph's ``ToolNode`` invokes tools with a wrapper dict
    ``{"name": ..., "args": {...}, "id": ..., "type": "tool_call"}`` rather
    than the bare args dict. Estimators expect the bare dict (so they can
    read ``portfolio_id`` etc.), so unwrap before forwarding. Direct
    callers (e.g. unit tests) pass the bare dict already; we detect that
    and pass it through unchanged.
    """
    if (
        isinstance(tool_input, dict)
        and tool_input.get("type") == "tool_call"
        and isinstance(tool_input.get("args"), dict)
    ):
        return tool_input["args"]
    return tool_input


def _check_cost_preview(
    tool_name: str,
    tool_input: Any,
    config: RunnableConfig | None,
    cost_estimator: Callable[[Any], float] | None,
) -> None:
    if cost_estimator is None or _confirmed_cost_preview(config):
        return
    args = _unwrap_tool_args(tool_input)
    try:
        seconds = float(cost_estimator(args) or 0.0)
    except Exception:
        # Estimator failure should not block a tool — error here means we
        # under-trigger the preview rather than spuriously over-trigger it.
        return
    # Inclusive on the boundary — policy says ≥30s requires confirmation, so
    # an estimate that lands on exactly 30s (e.g. 60 risk positions at 0.5s)
    # still needs a preview.
    if seconds >= LONG_RUNNING_SECONDS:
        _record_runtime_signal(
            config,
            {
                "kind": "cost_preview_required",
                "tool_name": tool_name,
                "estimated_seconds": seconds,
            },
        )
        raise CostPreviewRequiredError(
            tool_name=tool_name, estimated_seconds=seconds
        )


def capability_gated(
    *,
    group: ToolGroup,
    cost_estimator: Callable[[Any], float] | None = None,
) -> Callable[[BaseTool], BaseTool]:
    """Decorator factory: wrap a LangChain ``BaseTool`` with envelope gating.

    Optionally pass ``cost_estimator(tool_input) -> seconds``; the gate
    raises ``CostPreviewRequiredError`` when the estimate exceeds
    ``LONG_RUNNING_SECONDS`` and the request hasn't been pre-confirmed.
    """

    def wrap(t: BaseTool) -> BaseTool:
        original_invoke = t.invoke
        original_ainvoke = t.ainvoke

        @wraps(original_invoke)
        def gated_invoke(
            input: Any,  # noqa: A002 — matches LangChain signature
            config: RunnableConfig | None = None,
            **kwargs: Any,
        ) -> Any:
            envelope = _envelope_from_config(config)
            if not tool_allowed(envelope, group):
                _record_runtime_signal(
                    config,
                    {
                        "kind": "capability_denied",
                        "envelope": envelope.value,
                        "group": group.value,
                        "tool_name": t.name,
                    },
                )
                raise CapabilityDeniedError(
                    envelope=envelope, group=group, tool_name=t.name
                )
            _check_cost_preview(t.name, input, config, cost_estimator)
            return original_invoke(input, config=config, **kwargs)

        @wraps(original_ainvoke)
        async def gated_ainvoke(
            input: Any,  # noqa: A002
            config: RunnableConfig | None = None,
            **kwargs: Any,
        ) -> Any:
            envelope = _envelope_from_config(config)
            if not tool_allowed(envelope, group):
                _record_runtime_signal(
                    config,
                    {
                        "kind": "capability_denied",
                        "envelope": envelope.value,
                        "group": group.value,
                        "tool_name": t.name,
                    },
                )
                raise CapabilityDeniedError(
                    envelope=envelope, group=group, tool_name=t.name
                )
            _check_cost_preview(t.name, input, config, cost_estimator)
            return await original_ainvoke(input, config=config, **kwargs)

        # ``StructuredTool`` (what ``@tool`` returns) is a Pydantic model and
        # rejects normal attribute assignment. Use ``object.__setattr__`` to
        # bypass field validation — this is the same trick LangChain itself
        # uses internally when patching runtime fields.
        object.__setattr__(t, "invoke", gated_invoke)
        object.__setattr__(t, "ainvoke", gated_ainvoke)
        # Stash the group so callers (escalation engine, audit, CI assertions)
        # can read it without having to maintain a separate registry.
        object.__setattr__(t, "__capability_group__", group)
        if cost_estimator is not None:
            object.__setattr__(t, COST_ESTIMATOR_ATTR, cost_estimator)
        return t

    return wrap


def tool_scope_gated(t: BaseTool) -> BaseTool:
    """Wrap a tool with task-level ``tools_scope`` enforcement.

    Missing ``tools_scope`` means legacy/unflagged execution and is allowed.
    """
    if getattr(t, "__tool_scope_gated__", False):
        return t
    original_invoke = t.invoke
    original_ainvoke = t.ainvoke

    @wraps(original_invoke)
    def scoped_invoke(
        input: Any,  # noqa: A002
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        _check_tools_scope(t.name, config)
        return original_invoke(input, config=config, **kwargs)

    @wraps(original_ainvoke)
    async def scoped_ainvoke(
        input: Any,  # noqa: A002
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        _check_tools_scope(t.name, config)
        return await original_ainvoke(input, config=config, **kwargs)

    object.__setattr__(t, "invoke", scoped_invoke)
    object.__setattr__(t, "ainvoke", scoped_ainvoke)
    object.__setattr__(t, "__tool_scope_gated__", True)
    return t


__all__ = [
    "CapabilityDeniedError",
    "CostPreviewRequiredError",
    "COST_ESTIMATOR_ATTR",
    "LONG_RUNNING_SECONDS",
    "ToolScopeDeniedError",
    "capability_gated",
    "tool_scope_gated",
]

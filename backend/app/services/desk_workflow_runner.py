"""Auto-pilot runner: exec a DeskWorkflow script, driving real desk turns.

Each ``await step(prompt)`` the script makes drives one orchestrator turn and
forwards its SSE frames to the browser. Because ``step`` cannot ``yield`` into
the outer async generator, a producer/consumer ``asyncio.Queue`` bridges them:
``step``/``log`` push frames; ``run_desk_workflow`` drains the queue while a
background task execs the (restricted) script.
"""
from __future__ import annotations

import ast
import asyncio
import json as _json
from types import MappingProxyType
from typing import AsyncIterator, Callable

from ..models import DeskWorkflow
from .agents import _sse, resolve_execution_mode
from .desk_workflows_script import WorkflowScriptError, guard_script

_CHARACTER_BY_PERSONA = {
    "trader": "trader",
    "risk_manager": "risk_manager",
    "sales": "trader",
    "quant": "high_board",
}

# Injectable seams.
Drive = Callable[[int, str, str], AsyncIterator[str]]
Settle = Callable[[], None]

_SAFE_BUILTINS = {
    "len": len, "range": range, "enumerate": enumerate, "str": str, "int": int,
    "float": float, "bool": bool, "list": list, "dict": dict, "tuple": tuple,
    "set": set, "min": min, "max": max, "sum": sum, "sorted": sorted, "abs": abs,
    "round": round, "any": any, "all": all, "zip": zip,
}


class _Args:
    """Read-only view of validated launch params: supports args.x and args["x"]."""

    def __init__(self, values: dict) -> None:
        object.__setattr__(self, "_values", MappingProxyType(dict(values)))

    def __getattr__(self, name: str) -> str:
        try:
            return object.__getattribute__(self, "_values")[name]
        except KeyError:
            raise WorkflowScriptError(
                f"workflow referenced undeclared parameter {name!r}"
            ) from None

    def __setattr__(self, name: str, value: object) -> None:
        raise WorkflowScriptError("workflow args are read-only")

    def __delattr__(self, name: str) -> None:
        raise WorkflowScriptError("workflow args are read-only")

    def __getitem__(self, name: str) -> str:
        return self.__getattr__(name)


def persona_to_character(persona: str) -> str:
    return _CHARACTER_BY_PERSONA.get(persona, "trader")


def _wrap_async(script: str) -> ast.Module:
    """Lift the script body into ``async def __workflow__():`` so top-level await works."""
    tree = ast.parse(script)
    func = ast.AsyncFunctionDef(
        name="__workflow__",
        args=ast.arguments(
            posonlyargs=[], args=[], vararg=None, kwonlyargs=[],
            kw_defaults=[], kwarg=None, defaults=[],
        ),
        body=tree.body or [ast.Pass()],
        decorator_list=[],
        returns=None,
        type_comment=None,
    )
    return ast.fix_missing_locations(ast.Module(body=[func], type_ignores=[]))


async def run_desk_workflow(
    *,
    thread_id: int,
    workflow: DeskWorkflow,
    mode: str,
    drive: Drive,
    settle: Settle,
    args: dict | None = None,
) -> AsyncIterator[str]:
    guard_script(workflow.script)
    resolved_mode, _clear_hitl, _allow = resolve_execution_mode(mode, False)

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    state = {"index": 0, "error": None}

    async def step(prompt: str, *, mode: str | None = None):
        state["index"] += 1
        idx = state["index"]
        step_mode, _c, _a = resolve_execution_mode(mode or resolved_mode, False)
        await queue.put(_sse("workflow.step.start", {"index": idx, "prompt": prompt}))
        text_parts: list[str] = []
        async for frame in drive(thread_id, prompt, step_mode):
            await queue.put(frame)
            if frame.startswith("event: token"):
                data = frame.split("data:", 1)[1].strip() if "data:" in frame else "{}"
                try:
                    text_parts.append(str(_json.loads(data).get("text", "")))
                except Exception:
                    pass
        settle()
        await queue.put(_sse("workflow.step.end", {"index": idx}))
        return type("StepResult", (), {"text": "".join(text_parts), "ok": True})()

    def log(message: str) -> None:
        queue.put_nowait(_sse("workflow.log", {"message": str(message)}))

    async def _execute() -> None:
        module = _wrap_async(workflow.script)
        code = compile(module, filename=f"<desk-workflow:{workflow.slug}>", mode="exec")
        ns: dict = {
            "__builtins__": dict(_SAFE_BUILTINS),
            "step": step, "log": log, "args": _Args(args or {}),
        }
        try:
            exec(code, ns)
            await ns["__workflow__"]()
        except Exception as exc:  # halt on any step/script error
            state["error"] = str(exc)
            await queue.put(
                _sse("workflow.step.error", {"index": state["index"], "message": str(exc)})
            )
        finally:
            await queue.put(None)  # sentinel

    yield _sse("workflow.start", {"slug": workflow.slug, "mode": resolved_mode})
    task = asyncio.create_task(_execute())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
        if state["error"] is None:
            yield _sse("workflow.complete", {"steps": state["index"]})
    finally:
        # If the consumer disconnects / aborts (GeneratorExit) before the
        # sentinel, cancel the executor so remaining steps don't keep running
        # headless after the user hit Stop.
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

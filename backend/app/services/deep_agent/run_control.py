"""Process-local LangGraph RunControl registry."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

from langgraph.runtime import RunControl


_ASYNC_TASK_CONTROLS: dict[int, RunControl] = {}
_LOCK = Lock()


def new_run_control() -> RunControl:
    return RunControl()


def request_drain(control: RunControl | None, reason: str) -> bool:
    if control is None:
        return False
    control.request_drain(reason=reason)
    return True


@contextmanager
def register_async_task_control(
    task_id: int,
    control: RunControl,
) -> Iterator[RunControl]:
    with _LOCK:
        _ASYNC_TASK_CONTROLS[task_id] = control
    try:
        yield control
    finally:
        with _LOCK:
            if _ASYNC_TASK_CONTROLS.get(task_id) is control:
                _ASYNC_TASK_CONTROLS.pop(task_id, None)


def request_async_task_drain(task_id: int, reason: str = "cancelled") -> bool:
    with _LOCK:
        control = _ASYNC_TASK_CONTROLS.get(task_id)
    return request_drain(control, reason)

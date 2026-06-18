"""Async-agent dispatch module — general-purpose background analyst."""
from .agent import build_async_agent
from .policy import (
    ASYNC_POLICY_FRAGMENTS,
    MAX_CONCURRENT_PER_THREAD,
    SCRATCH_DIR_TEMPLATE,
    scratch_dir_for_task,
)
from .resume import TaskNotResumableError, resume_async_agent_interrupt
from .runner import TooManyRunningError, compose_task_brief, start_async_agent_task

__all__ = [
    "build_async_agent",
    "ASYNC_POLICY_FRAGMENTS",
    "MAX_CONCURRENT_PER_THREAD",
    "SCRATCH_DIR_TEMPLATE",
    "scratch_dir_for_task",
    "TooManyRunningError",
    "compose_task_brief",
    "start_async_agent_task",
    "resume_async_agent_interrupt",
    "TaskNotResumableError",
]

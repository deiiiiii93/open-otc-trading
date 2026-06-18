"""Policy constants and helpers for async-agent dispatch."""
from __future__ import annotations

MAX_CONCURRENT_PER_THREAD: int = 4
SCRATCH_DIR_TEMPLATE: str = "/trading_desk/async/{task_id}/"

# Same fragments as trader/risk personas, minus clarification-policy
# (async agents cannot clarify mid-flight; the identity prompt teaches
# best-guess + surface-assumption instead).
ASYNC_POLICY_FRAGMENTS: tuple[str, ...] = (
    "read-before-compute-policy",
    "cost-preview-policy",
    "yolo-hitl-policy",
    "python-analysis-policy",
)


def scratch_dir_for_task(task_id: int | str) -> str:
    """Return the virtual scratch-dir path for an async-agent task."""
    return SCRATCH_DIR_TEMPLATE.format(task_id=task_id)

"""Every audit-context literal in agents.py must carry `mode` and `thread_id`.

The System One guard's runtime belt reads `mode`; its committed-verdict lookup
keys on `thread_id`. A resume path missing `mode` makes the guard's re-run skip
interrupt(), so the human's decision is never consumed and a REJECTED call runs.
AST-level, same approach as test_arena_extractor_routing's stamping test.
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.services.agents import _resume_audit_mode

_SOURCE = Path("backend/app/services/agents.py")


def _audit_context_literals():
    tree = ast.parse(_SOURCE.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Name) and key.id == "AUDIT_CONTEXT_KEY" and isinstance(value, ast.Dict):
                yield {k.value for k in value.keys if isinstance(k, ast.Constant)}


def test_every_audit_context_stamps_mode_and_thread_id():
    sites = list(_audit_context_literals())
    assert len(sites) >= 5, sites   # 2 streaming builds + 3 resume paths
    for keys in sites:
        assert {"mode", "thread_id"} <= keys, keys


def test_both_streaming_builds_stamp_the_turns_user_message():
    assert sum("user_message_id" in keys for keys in _audit_context_literals()) == 2


def test_resume_mode_recovers_the_interrupted_turns_mode():
    # A pending action exists only on an interactive or AUTO turn (YOLO never
    # interrupts), so the persisted clear-HITL flag recovers the mode exactly.
    assert _resume_audit_mode(True) == "auto"
    assert _resume_audit_mode(False) == "interactive"

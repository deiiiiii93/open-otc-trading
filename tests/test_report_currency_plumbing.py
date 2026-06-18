"""TDD tests for E1 (report_currency in ContextPack) and E2 (persona instruction)."""
from __future__ import annotations

from app.services.deep_agent.context_assembler import _context_pack_payload
from app.services.deep_agent.personas import report_currency_instruction
from app.services.deep_agent.task_registry import TaskSpec


def _spec():
    return TaskSpec(
        task_type="fetch_position_summaries",
        inputs={},
        depends_on=[],
        assigned_persona="risk_manager",
    )


class _WF:
    canonical_snapshot_ids = {}


def test_report_currency_changes_content_hash():
    h_native, payload_native = _context_pack_payload(
        task_spec=_spec(),
        workflow=_WF(),
        artifact_ids=[],
        recent_summary="",
        report_currency="by_position",
    )
    h_usd, payload_usd = _context_pack_payload(
        task_spec=_spec(),
        workflow=_WF(),
        artifact_ids=[],
        recent_summary="",
        report_currency="USD",
    )
    assert payload_native["report_currency"] == "by_position"
    assert payload_usd["report_currency"] == "USD"
    assert h_native != h_usd


def test_instruction_iso_vs_by_position():
    iso = report_currency_instruction("USD")
    assert "USD" in iso
    assert "convert_currency" in iso

    native = report_currency_instruction("by_position")
    assert "convert_currency" not in native
    assert "separately" in native.lower() or "do not" in native.lower()

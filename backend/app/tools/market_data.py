"""@tool wrappers for the market data domain.

Each wrapper is a thin LLM adapter: parse args, call services/domains/market_data,
shape JSON. The wire shapes preserve the legacy langchain_tools.py payloads so
existing agent tests continue to exercise this layer untouched.
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from app.schemas import AkshareSnapshotRequest
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.domains import market_data as md_svc

from ._shaping import shape_market_data_profile


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("fetch_market_snapshot", args_schema=AkshareSnapshotRequest)
def fetch_market_snapshot_tool(**kwargs: Any) -> dict[str, Any]:
    """Fetch and normalize a market snapshot through AKShare with fallback metadata."""
    snapshot = md_svc.fetch_snapshot(**kwargs)
    return snapshot.model_dump(mode="json")


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("list_market_data_profiles")
def list_market_data_profiles_tool() -> dict[str, Any]:
    """List all stored market data profiles."""
    rows = md_svc.list_profiles()
    return {
        "ok": True,
        "data": [shape_market_data_profile(p) for p in rows],
        "total_count": len(rows),
    }

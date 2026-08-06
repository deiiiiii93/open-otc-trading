"""Deterministic P&L producers for the report module.

Nothing in this package calls an LLM. Every number a report renders that
originates here is computed from persisted risk-run evidence.
"""
from __future__ import annotations

from .explain import RESIDUAL_WARN_RATIO, UnsupportedMetricContract, explain_diff
from .snapshot_diff import diff_metrics, load_run_pair

__all__ = [
    "diff_metrics",
    "load_run_pair",
    "explain_diff",
    "RESIDUAL_WARN_RATIO",
    "UnsupportedMetricContract",
]

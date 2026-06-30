"""Static configuration for long-term memory (see spec §Config)."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

DEFAULT_CORRECTION_PHRASES: tuple[str, ...] = (
    "that's wrong", "that is wrong", "that's incorrect", "that is incorrect",
    "no, actually", "no actually", "you're wrong", "you are wrong",
    "don't do that", "do not do that", "stop doing that", "that's not right",
    "not what i asked",
)

DEFAULT_DENYLIST: tuple[str, ...] = (
    r"(?i)\b(api[_-]?key|secret|password|passwd|token|bearer)\b\s*[:=]",
    r"sk-[A-Za-z0-9]{16,}",
    r"\b\d+(?:\.\d+)?\s*(?:usd|eur|cny|cnh|jpy|hkd|gbp)\b",
    r"\b\d{3,}\s*(?:shares|contracts|lots|notional)\b",
)


@dataclass(frozen=True)
class MemoryConfig:
    enabled: bool = True
    confidence_floor: float = 0.7
    max_facts_per_scope: int = 100
    max_correction_facts: int = 20
    injection_token_budget: int = 2000
    correction_token_budget: int = 1000
    max_queue_size: int = 1000
    max_high_queue_size: int = 256
    sweep_interval_seconds: int = 60
    max_extract_attempts: int = 3
    extract_window_messages: int = 40
    extract_window_tokens: int = 8000
    read_timeout_ms: int = 250
    writer_busy_timeout_ms: int = 2000
    content_max_chars: int = 2000
    category_max_chars: int = 64
    tiktoken_encoder: str = "cl100k_base"
    # Registry TAGS identifying the extraction model, resolved against
    # ChannelRegistry.select_by_tag() at run time (see resolve_extractor_selection).
    # Two-tier so a missing dedicated tag degrades to the cheap tier, never back to
    # the (expensive) agent default:
    #   1. extractor_model       — dedicated tag; tag exactly ONE model with it in
    #                              agent_channels.yaml to pin the extractor model.
    #   2. extractor_fallback_tag — cheap-tier fallback when no healthy model carries
    #                              the dedicated tag ("fast" => Haiku on the primary
    #                              channel). Only if neither resolves does the
    #                              resolver fall back to the registry default.
    extractor_model: str = "extractor"
    extractor_fallback_tag: str = "fast"
    shutdown_grace_seconds: float = 5.0
    # First-enable cutoff: the reconciliation sweep only DISCOVERS closed sessions
    # whose closed_at >= this instant. None => no cutoff (reconcile all closed
    # sessions, the original behaviour — fine for fresh DBs). Set it (via
    # OPEN_OTC_MEMORY_RECONCILE_SINCE) when first enabling memory on an existing
    # DB so the sweep does not mass-extract the entire historical backlog.
    reconcile_since: datetime | None = None
    correction_phrases: tuple[str, ...] = field(default_factory=lambda: DEFAULT_CORRECTION_PHRASES)
    denylist: tuple[str, ...] = field(default_factory=lambda: DEFAULT_DENYLIST)


def _parse_reconcile_since(raw: str | None) -> datetime | None:
    """Parse an ISO-8601 instant from the env, or None when unset/malformed.

    A malformed value fails OPEN (None => reconcile everything) rather than
    silently disabling reconciliation; a warning is logged so the typo is
    visible. 'Z' (UTC) suffixes are accepted (datetime.fromisoformat, 3.11+).
    """
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        logger.warning(
            "OPEN_OTC_MEMORY_RECONCILE_SINCE=%r is not a valid ISO-8601 instant; "
            "ignoring (reconciliation cutoff disabled)", raw)
        return None


def get_memory_config() -> MemoryConfig:
    raw = os.environ.get("OPEN_OTC_MEMORY", "on").lower()
    return MemoryConfig(
        enabled=raw not in {"off", "0", "false"},
        reconcile_since=_parse_reconcile_since(
            os.environ.get("OPEN_OTC_MEMORY_RECONCILE_SINCE")),
    )

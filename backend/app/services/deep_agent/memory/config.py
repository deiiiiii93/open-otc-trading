"""Static configuration for long-term memory (see spec §Config)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

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
    extractor_model: str = "flash"
    shutdown_grace_seconds: float = 5.0
    correction_phrases: tuple[str, ...] = field(default_factory=lambda: DEFAULT_CORRECTION_PHRASES)
    denylist: tuple[str, ...] = field(default_factory=lambda: DEFAULT_DENYLIST)


def get_memory_config() -> MemoryConfig:
    raw = os.environ.get("OPEN_OTC_MEMORY", "on").lower()
    return MemoryConfig(enabled=raw not in {"off", "0", "false"})

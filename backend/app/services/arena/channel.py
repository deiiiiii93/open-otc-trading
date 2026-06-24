"""Zenmux chat-client channel factory.

Provides build_zenmux_chat — constructs a LangChain ChatOpenAI instance
pointed at the Zenmux OpenAI-compatible gateway.

The Zenmux base URL is hardcoded here (not configurable per run) because
it is an infrastructure constant, not a per-request parameter.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from langchain_openai import ChatOpenAI

from app.services.arena.models import ArenaModel

ZENMUX_BASE_URL = "https://zenmux.ai/api/v1"


def build_zenmux_chat(
    model: ArenaModel,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    api_key: str | None = None,
) -> ChatOpenAI:
    """Build a ChatOpenAI client routed through the Zenmux gateway.

    Args:
        model: The ArenaModel descriptor (provides zenmux_name + default_config).
        temperature: Override the model's default temperature.
        max_tokens: Override the model's default max_tokens.
        api_key: Explicit API key; falls back to ZENMUX_API_KEY env var.
            Pass a dummy value in unit tests to avoid requiring the real key.

    Returns:
        A configured ChatOpenAI instance that sends requests to Zenmux.
        The client is not connected / validated at construction time —
        no network call is made.
    """
    effective_temperature = (
        temperature
        if temperature is not None
        else model.default_config.get("temperature", 0)
    )
    effective_max_tokens = (
        max_tokens
        if max_tokens is not None
        else model.default_config.get("max_tokens", 4096)
    )
    effective_api_key = api_key if api_key is not None else os.environ["ZENMUX_API_KEY"]

    return ChatOpenAI(
        model=model.zenmux_name,
        base_url=ZENMUX_BASE_URL,
        api_key=effective_api_key,
        temperature=effective_temperature,
        max_tokens=effective_max_tokens,
    )

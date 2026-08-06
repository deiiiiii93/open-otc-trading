"""Production narrator binding for report generation.

Kept out of ``generate.py`` so the pipeline stays model-free and testable. The
narrator returns PROSE ONLY — it is handed a section's resolved blocks with
their status and reason, and its entire contribution is a string.
"""
from __future__ import annotations

import json
from typing import Any

_SYSTEM = """You write one section of a desk report.

You are given resolved data blocks and an instruction. Your entire output is
prose for this section. Rules you must not break:

1. Never state a number that does not appear in the block data you were given.
2. Each block carries a status:
   - "ok"          the data is real; use it.
   - "empty"       the check RAN and found nothing. Say so affirmatively.
   - "unavailable" the check DID NOT RUN. Say exactly that. Never imply a
                   clean result from a check that did not run.
3. Follow the instruction's length. Do not add headings or restate tables.
4. Write plainly, for a professional desk reader.
5. Round figures to a sensible precision for prose (a utilisation of
   0.6711423137121955 is "0.67"). Rounding is expected and does not count as
   changing the number; quoting sixteen decimal places is not more accurate,
   only less readable.
"""


class NarratorUnavailable(RuntimeError):
    """Raised when no healthy model channel is configured.

    The pipeline catches this per section, so a missing model degrades each
    narrated section to ``narrative_error`` while every deterministic block
    still renders.
    """


def default_narrator():
    """Return a narrate(persona, brief) -> str callable bound to a real model.

    The persona is accepted for signature compatibility with the ``Narrator``
    alias and is recorded in the prompt, but model selection uses the registry
    default: per-persona model routing is an optimisation, not a requirement,
    and a single default model narrating every persona is correct behaviour for
    the first implementation.
    """
    from app.services.deep_agent.channel_registry import get_registry
    from app.services.deep_agent.model_factory import build_agent_model

    def narrate(persona: str, brief: dict[str, Any]) -> str:
        model = build_agent_model(get_registry())
        if model is None:
            raise NarratorUnavailable(
                "no healthy model channel is configured, so this section could "
                "not be narrated"
            )
        payload = json.dumps(
            {"persona": persona, **brief}, default=str, ensure_ascii=False
        )
        response = model.invoke(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": payload},
            ]
        )
        content = getattr(response, "content", response)
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return str(content).strip()

    return narrate


__all__ = ["NarratorUnavailable", "default_narrator"]

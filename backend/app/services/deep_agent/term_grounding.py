"""One-shot grounding nudge for ungrounded term-completeness verdicts.

The live A/B smoke (scripts/semantic_layer_ab_smoke.py) showed the failure
mode: a confident model answers "is this term set complete?" from priors,
confabulating plausible-but-wrong structure (a knock-in leg on a sharkfin)
without consulting the semantic layer. This middleware closes that gap at
the seam the model cannot skip: after_model. If the final answer contains a
completeness verdict about a product family and NEITHER grounding tool was
called this turn, the model is bounced back exactly once with an
instruction to verify first.

Deliberately narrow and fail-open:
- fires only on final answers (no tool calls) matching BOTH a verdict
  pattern and a product-family mention;
- one nudge per turn (marker scan since the last human message);
- any internal error lets the original answer through untouched.
"""
from __future__ import annotations

import logging
import re

from langchain.agents.middleware.types import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

logger = logging.getLogger("agent.term_grounding")

GROUNDING_TOOLS = {"check_term_completeness", "get_product_reference_doc"}

NUDGE_MARKER = "[desk-guardrail:term-grounding]"
NUDGE_TEXT = (
    f"{NUDGE_MARKER} Your answer states whether a product's terms are "
    "complete or lists missing terms, but you did not verify against the "
    "desk's contracts. Before finalizing: call `check_term_completeness` "
    "with the QuantArk class and the terms you were given (and "
    "`get_product_reference_doc` if you need the family's semantics). "
    "If you do not hold these tools, delegate the check to the appropriate "
    "persona via `task`. Then restate the verdict using ONLY the returned "
    "missing_required set - do not add terms the contract does not list."
)

_VERDICT_RE = re.compile(
    r"complete enough to price|not complete|incomplete|cannot be priced"
    r"|ready to (?:price|book)|missing (?:required )?(?:term|input|field|economic)"
    r"|(?:term|input|field)s? (?:are |is |still )?missing",
    re.IGNORECASE,
)
_FAMILY_RE = re.compile(
    r"snowball|sharkfin|phoenix|ko-?reset|range accrual|one-?touch|digital"
    r"|barrier option|asian|vanilla|autocallable|Option\b|Futures|SpotInstrument",
    re.IGNORECASE,
)


def _turn_messages(messages: list) -> list:
    """Messages since (and excluding) the last real human message."""
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if isinstance(m, HumanMessage) and NUDGE_MARKER not in str(m.content):
            return messages[i + 1 :]
    return messages


class TermGroundingMiddleware(AgentMiddleware):
    """Bounce ungrounded completeness verdicts back to the model once."""

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime, config=None):  # type: ignore[override]
        try:
            messages = (state or {}).get("messages", [])
            if not messages:
                return None
            last = messages[-1]
            if not isinstance(last, AIMessage) or getattr(last, "tool_calls", None):
                return None  # not a final answer
            text = last.content if isinstance(last.content, str) else str(last.content)
            if not (_VERDICT_RE.search(text) and _FAMILY_RE.search(text)):
                return None

            turn = _turn_messages(messages)
            for m in turn:
                if isinstance(m, ToolMessage) and (m.name or "") in GROUNDING_TOOLS:
                    return None  # grounded - let it through
                if isinstance(m, HumanMessage) and NUDGE_MARKER in str(m.content):
                    return None  # already nudged this turn - never loop
            logger.info("term-grounding nudge fired (ungrounded completeness verdict)")
            return {
                "messages": [HumanMessage(content=NUDGE_TEXT)],
                "jump_to": "model",
            }
        except Exception:  # noqa: BLE001 - fail-open: never break the turn
            logger.warning("term-grounding after_model failed; passing through", exc_info=True)
            return None

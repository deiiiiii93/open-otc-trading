"""System One per-call guard for AUTO mode (spec 2026-09-21 §1).

AUTO strips every "write"-level tool from the HITL interrupt map by a static
per-TOOL lookup, so `void_settlement_cashflow(9300)` runs unattended whether the
user asked for it or the agent reached for it to clear a refusal (the measured
ops-settlement-day step-8 failure). This middleware asks System One (Jev) the
desk's per-tool predicates (tool_guard_policy.GUARD_POLICY) about THIS call and
commits the verdict.

`shadow` (default) records and never blocks. `enforce` re-promotes a flagged or
unscoreable call to the normal approval card.

Registered iff the turn is AUTO (`yolo_mode and allow_reply_options`) in all four
stacks. The runtime belt re-checks the server-stamped audit-context mode because
the default orchestrator graph is built once and reused across turns.

Why `after_model` and not `wrap_tool_call`: this is the repo's paved path for an
argument-aware interrupt (LongRunningCostHITLMiddleware), so the approval card,
_SUMMARY_BUILDERS and the hitl_proposal -> decision -> execution audit chain all
work unchanged. A middleware registered IN a stack sees that stack's calls.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware.human_in_the_loop import (
    ActionRequest,
    HITLRequest,
    HumanInTheLoopMiddleware,
    ReviewConfig,
)
from langchain.agents.middleware.types import AgentState, ContextT, ResponseT, StateT
from langchain_core.messages import AIMessage, AnyMessage, ToolCall, ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from ...config import Settings, get_settings
from ..system_one import Noul, SystemOneUnavailable, ask, is_enabled
from .audit_trail_middleware import _read_audit_context
from .hitl import GUARD_NOTE_PREFIX, _RISK_LEVEL_BY_TOOL
from .tool_guard_policy import GUARD_POLICY, GuardPredicate, validate_policy
from .tool_guard_state import build_guard_state, load_user_request
from .tool_guard_store import (
    GuardStoreUnavailable,
    StoredVerdict,
    args_fingerprint,
    commit_verdict,
    find_verdict,
    mark_interrupted,
    record_structural,
)

logger = logging.getLogger(__name__)

#: A verdict that could not be committed THIS pass. Never stored.
PERSIST_FAILED = "persist_failed"

_REFUSAL = (
    "Guard verdict store unavailable; destructive action '{name}' blocked "
    "(fail-closed). Read-only tools still work; retry shortly."
)


@dataclass(frozen=True)
class GuardDecision:
    """One guarded call's verdict for this pass."""

    index: int                   # position in the AIMessage's tool_calls
    tool_call: ToolCall
    verdict: str                 # clear | flagged | unscored | persist_failed
    unscored_reason: str | None = None
    predicates: list[dict] = field(default_factory=list)
    row_id: int | None = None    # stored row backing it; None = structural/collision/failed


@dataclass(frozen=True)
class _Pass:
    ai_message: AIMessage
    messages: list[AnyMessage]
    guarded: list[tuple[int, ToolCall]]
    settings: Settings
    context: dict[str, Any]


def _unscored(reason: str, *, model: str | None = None, latency_ms: int | None = None,
              error: str | None = None, source: str | None = None) -> dict[str, Any]:
    return {
        "verdict": "unscored",
        "unscored_reason": reason,
        "predicates_json": [],
        "max_probability": None,
        "model": model,
        "latency_ms": latency_ms,
        "error": error,
        "user_request_source": source,
    }


def _decision_from(index: int, call: ToolCall, stored: StoredVerdict,
                   args_hash: str) -> GuardDecision:
    if stored.tool_name != call["name"] or stored.args_hash != args_hash:
        # D10 rule 1: a provider that reuses an id must never inherit an earlier
        # verdict. Structural (identical on every pass), so it needs no row.
        return GuardDecision(index, call, "unscored", "tool_call_id_collision")
    return GuardDecision(index, call, stored.verdict, stored.unscored_reason,
                         list(stored.predicates), stored.id)


def guard_note(decision: GuardDecision) -> str:
    """The card's "why". Probabilities only for a flag; the reason otherwise."""
    if decision.verdict == "flagged":
        fired = ", ".join(
            f"{p['key']} p={p['probability']:.2f} ≥ {p['threshold']:.2f}"
            for p in decision.predicates if p.get("flagged")
        )
        return f"{GUARD_NOTE_PREFIX} flagged — {fired}"
    return (
        f"{GUARD_NOTE_PREFIX} could not score this call ({decision.unscored_reason}); "
        "review it as you would in interactive mode"
    )


class ToolGuardMiddleware(HumanInTheLoopMiddleware[StateT, ContextT, ResponseT]):
    def __init__(
        self,
        *,
        persona: str,
        policy: Mapping[str, tuple[GuardPredicate, ...]] | None = None,
        post: Any = None,
    ) -> None:
        policy = GUARD_POLICY if policy is None else policy
        validate_policy(policy, _RISK_LEVEL_BY_TOOL)  # fails loudly at agent build
        # No "edit": a guard must never classify a call HITL later rewrites
        # (langchain issue #40694).
        super().__init__(
            {name: {"allowed_decisions": ["approve", "reject"]} for name in policy}
        )
        self.policy = dict(policy)
        self.persona = persona
        self._post = post

    # --- hooks -----------------------------------------------------------

    def after_model(
        self, state: AgentState[Any], runtime: Runtime[ContextT]
    ) -> dict[str, Any] | None:
        prepared = self._prepare(state)
        if prepared is None:
            return None
        return self._conclude(prepared, self._resolve_all(prepared))

    async def aafter_model(
        self, state: AgentState[Any], runtime: Runtime[ContextT]
    ) -> dict[str, Any] | None:
        prepared = self._prepare(state)
        if prepared is None:
            return None
        # Still synchronous for the AGENT (D13: the call waits for its verdict);
        # the worker thread only keeps the ~1.3 s HTTP + DB round trip off the
        # event loop. interrupt() stays on the loop, in _conclude.
        decisions = await asyncio.to_thread(self._resolve_all, prepared)
        return self._conclude(prepared, decisions)

    # --- steps -----------------------------------------------------------

    def _prepare(self, state: AgentState[Any]) -> _Pass | None:
        messages = list(state.get("messages") or [])
        ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        if ai is None or not ai.tool_calls:
            return None
        guarded = [(i, c) for i, c in enumerate(ai.tool_calls) if c.get("name") in self.policy]
        if not guarded:  # cheap check first: most turns never touch the nine tools
            return None
        settings = get_settings()
        if not is_enabled(settings) or settings.tool_guard_mode == "off":
            return None
        context = _read_audit_context() or {}
        if context.get("mode") != "auto":  # runtime belt
            return None
        return _Pass(ai, messages, guarded, settings, context)

    def _resolve_all(self, p: _Pass) -> list[GuardDecision]:
        return [self._resolve(p, index, call) for index, call in p.guarded]

    def _resolve(self, p: _Pass, index: int, call: ToolCall) -> GuardDecision:
        try:
            return self._resolve_or_raise(p, index, call)
        except GuardStoreUnavailable:
            logger.exception("tool guard: verdict store unavailable for %s", call.get("name"))
            return GuardDecision(index, call, PERSIST_FAILED)

    def _resolve_or_raise(self, p: _Pass, index: int, call: ToolCall) -> GuardDecision:
        """Spec §1 step 2, in order, so the result never depends on a fresh Jev
        answer when a committed one exists (D10)."""
        name = call["name"]
        call_id = str(call.get("id") or "")
        thread_id = p.context.get("thread_id")
        thread_id = thread_id if isinstance(thread_id, int) else 0
        fp = args_fingerprint(name, call.get("args") or {})
        base = {
            "thread_id": thread_id, "tool_call_id": call_id, "persona": self.persona,
            "exec_mode": p.context.get("mode"), "guard_mode": p.settings.tool_guard_mode,
            "tool_name": name, "args_json": fp.payload, "redacted": fp.redacted,
            "args_hash": fp.sha256,
        }
        # a. No id => cannot be cached => never asked. Structural, best-effort row.
        if not call_id:
            record_structural({**base, **_unscored("no_tool_call_id")})
            return GuardDecision(index, call, "unscored", "no_tool_call_id")
        # b. A committed row for this key wins (if it describes the same call).
        stored = find_verdict(thread_id, call_id)
        # c-e. Otherwise evaluate once and commit; a concurrent winner's row wins.
        if stored is None:
            stored = commit_verdict({**base, **self._evaluate(p, call)})
        return _decision_from(index, call, stored, fp.sha256)

    def _evaluate(self, p: _Pass, call: ToolCall) -> dict[str, Any]:
        """Verdict fields for one call. Never raises (store errors are not here)."""
        requested = p.settings.system_one_model
        try:
            user_request, source = load_user_request(p.context)
            if user_request is None:
                return _unscored("no_user_request", model=requested)
            predicates = self.policy[call["name"]]
            guard_state = build_guard_state(
                p.messages, call, user_request=user_request,
                is_subagent=self.persona != "orchestrator",
            )
            try:
                result = ask(
                    guard_state,
                    {q.key: Noul(q.instructions) for q in predicates},
                    post=self._post,
                    settings=p.settings,
                )
            except SystemOneUnavailable as exc:
                return _unscored(exc.reason, model=requested, latency_ms=exc.latency_ms,
                                 error=exc.detail, source=source)
            scored = []
            for q in predicates:
                probability = result.answers[q.key].probability
                scored.append({
                    "key": q.key, "probability": probability, "threshold": q.threshold,
                    "flagged": probability >= q.threshold, "evidence": q.evidence,
                })
            return {
                "verdict": "flagged" if any(s["flagged"] for s in scored) else "clear",
                "unscored_reason": None,
                "predicates_json": scored,
                "max_probability": max(s["probability"] for s in scored),
                "model": result.model,
                "latency_ms": result.latency_ms,
                "error": None,
                "user_request_source": source,
            }
        except Exception:  # noqa: BLE001 — the guard never raises into the agent loop
            logger.exception("tool guard: evaluation failed for %s", call.get("name"))
            return _unscored("internal_error", model=requested)

    def _conclude(self, p: _Pass, decisions: list[GuardDecision]) -> dict[str, Any] | None:
        if p.settings.tool_guard_mode != "enforce":
            return None  # shadow: recorded, never blocked
        # D10 rule 3: a pass either refuses or interrupts, never both. An interrupt
        # here would be followed on resume by a lookup that finds nothing, a fresh
        # Jev call, and possibly a different card set.
        if any(d.verdict == PERSIST_FAILED for d in decisions):
            return self._refuse(p.ai_message, decisions)
        carded = [d for d in decisions if d.verdict in ("flagged", "unscored")]
        if not carded:
            return None
        return self._interrupt(p.ai_message, carded)

    def _refuse(self, ai: AIMessage, decisions: list[GuardDecision]) -> dict[str, Any]:
        """Refuse every guarded call that is not a committed clear.

        The calls STAY on the AIMessage, each answered by an error ToolMessage —
        the construction _process_decision uses for a rejection. A dropped call
        would orphan its ToolMessage, which providers reject. No interrupt is
        raised, so the node is never re-entered: nothing to keep deterministic.
        Not a new posture: the audit trail's fail-closed phase 1 would refuse
        these writes against the same database a moment later.
        """
        refused = {d.index for d in decisions if d.verdict != "clear"}
        answers = [
            ToolMessage(
                content=_REFUSAL.format(name=tool_call["name"]),
                name=tool_call["name"],
                tool_call_id=tool_call.get("id") or "",
                status="error",
            )
            for index, tool_call in enumerate(ai.tool_calls)
            if index in refused
        ]
        return {"messages": [ai, *answers]}

    def _interrupt(self, ai: AIMessage, carded: list[GuardDecision]) -> dict[str, Any]:
        """One HITLRequest for every carded call, in original tool-call order;
        decisions processed exactly as LongRunningCostHITLMiddleware does (D8)."""
        response = interrupt(
            HITLRequest(
                action_requests=[
                    ActionRequest(
                        name=d.tool_call["name"],
                        args=d.tool_call.get("args") or {},
                        description=guard_note(d),
                    )
                    for d in carded
                ],
                review_configs=[
                    ReviewConfig(
                        action_name=d.tool_call["name"],
                        allowed_decisions=self.interrupt_on[d.tool_call["name"]]["allowed_decisions"],
                    )
                    for d in carded
                ],
            )
        )
        decisions = response["decisions"]
        if len(decisions) != len(carded):
            raise ValueError("Number of human decisions does not match tool-guard interrupts.")
        by_index = {d.index: decision for d, decision in zip(carded, decisions)}
        revised: list[ToolCall] = []
        answers: list[ToolMessage] = []
        for index, tool_call in enumerate(ai.tool_calls):
            if index not in by_index:
                revised.append(tool_call)  # clear and unguarded calls run as AUTO intends
                continue
            new_call, message = self._process_decision(
                by_index[index], tool_call, self.interrupt_on[tool_call["name"]]
            )
            if new_call is not None:
                revised.append(new_call)
            if message is not None:
                answers.append(message)
        mark_interrupted(d.row_id for d in carded)
        ai.tool_calls = revised
        return {"messages": [ai, *answers]}

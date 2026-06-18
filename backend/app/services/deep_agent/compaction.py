"""Ledger-aware compaction controls for task-scoped DeepAgents sessions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage


def _summarization_middleware_base() -> type:
    from deepagents.middleware.summarization import SummarizationMiddleware

    return SummarizationMiddleware


PROTECTED_ARTIFACT_KINDS = {
    "persisted_run",
    "deterministic_query",
    "finding",
    "report",
    "plan",
}

PERSISTED_TOOL_NAMES = {
    "run_batch_pricing",
    "create_report",
    "import_otc_positions",
    "create_or_update_rfq_draft",
    "quote_rfq",
    "submit_rfq_for_approval",
    "approve_rfq",
    "reject_rfq",
    "release_rfq",
    "mark_rfq_client_accepted",
    "book_rfq_to_position",
}

LEDGER_AWARE_SUMMARY_PROMPT = """<role>
Ledger-aware context compactor for an OTC derivatives desk agent session.
</role>

<instructions>
Summarize only compactable ephemeral conversation history. Never treat this summary
as financial truth; durable facts live in DB tables, session_artifacts, evidence refs,
domain_events, /large_tool_results/, /trading_desk/, /artifacts/, and /session/findings/.

Every substantive factual sentence in the summary must end with an existing
ledger/tool citation: [artifact:N] or [tool_call:id]. If a factual statement has no
such citation, omit it or rewrite it as an unresolved note.

Preserve pending HITL state, user intent, blocker state, and next action. Do not
invent artifact ids, tool_call ids, prices, risk metrics, trade ids, dates, or
approval status.
</instructions>

<messages>
Messages to summarize:
{messages}
</messages>
"""

DEFAULT_TOKEN_TRIGGER_FALLBACK = ("tokens", 100_000)


@dataclass(frozen=True)
class CompactionBatch:
    start: int
    end: int


def select_compaction_batch(
    messages: list[BaseMessage],
    *,
    keep_recent: int = 6,
    max_messages: int = 8,
) -> CompactionBatch | None:
    """Return the first consecutive compactable batch outside the recent tail."""
    limit = max(0, len(messages) - keep_recent)
    start: int | None = None
    end: int | None = None
    for index, message in enumerate(messages[:limit]):
        if is_compactable_message(message):
            if start is None:
                start = index
            end = index + 1
            if end - start >= max_messages:
                break
            continue
        if start is not None:
            break

    if start is None or end is None:
        return None
    return CompactionBatch(start=start, end=end)


def is_compactable_message(message: BaseMessage) -> bool:
    if isinstance(message, ToolMessage):
        tool_name = _message_tool_name(message)
        return tool_name not in PERSISTED_TOOL_NAMES
    if isinstance(message, AIMessage):
        return _message_artifact_kind(message) not in PROTECTED_ARTIFACT_KINDS
    return True


def _message_tool_name(message: BaseMessage) -> str | None:
    name = getattr(message, "name", None)
    if name:
        return str(name)
    metadata = getattr(message, "additional_kwargs", {}) or {}
    for key in ("tool_name", "name"):
        value = metadata.get(key)
        if value:
            return str(value)
    return None


def _message_artifact_kind(message: BaseMessage) -> str | None:
    for payload in (
        getattr(message, "additional_kwargs", {}) or {},
        getattr(message, "response_metadata", {}) or {},
    ):
        kind = _artifact_kind_from_mapping(payload)
        if kind:
            return kind
    return None


def _artifact_kind_from_mapping(payload: dict[str, Any]) -> str | None:
    for key in ("artifact_kind", "kind"):
        value = payload.get(key)
        if value:
            return str(value)
    artifact = payload.get("artifact")
    if isinstance(artifact, dict):
        value = artifact.get("kind")
        if value:
            return str(value)
    return None


class LedgerScopedCompactionMiddleware(_summarization_middleware_base()):
    """DeepAgents summarization constrained to compactable ledger-safe batches."""

    def __init__(
        self,
        model: Any,
        *,
        backend: Any,
        trigger: Any = ("fraction", 0.7),
        keep_recent: int = 6,
        max_messages: int = 8,
    ) -> None:
        self.keep_recent = keep_recent
        self.max_messages = max_messages
        self.requested_trigger = trigger
        effective_trigger = (
            DEFAULT_TOKEN_TRIGGER_FALLBACK
            if _requires_model_profile(trigger) and not _has_model_profile(model)
            else trigger
        )
        super().__init__(
            model=model,
            backend=backend,
            trigger=effective_trigger,
            keep=("messages", keep_recent),
            token_counter=_count_tokens_approximately,
            summary_prompt=LEDGER_AWARE_SUMMARY_PROMPT,
            trim_tokens_to_summarize=4000,
        )

    def _determine_cutoff_index(self, messages: list[BaseMessage]) -> int:
        deepagents_cutoff = super()._determine_cutoff_index(messages)
        if deepagents_cutoff <= 0:
            return 0

        batch = select_compaction_batch(
            list(messages[:deepagents_cutoff]),
            keep_recent=0,
            max_messages=self.max_messages,
        )
        if batch is None or batch.start != 0:
            return 0
        return batch.end


def _requires_model_profile(trigger: Any) -> bool:
    triggers = trigger if isinstance(trigger, list) else [trigger]
    return any(
        isinstance(item, tuple) and len(item) >= 1 and item[0] == "fraction"
        for item in triggers
    )


def _has_model_profile(model: Any) -> bool:
    profile = getattr(model, "profile", None)
    if profile is None:
        return False
    if isinstance(profile, dict):
        return bool(profile.get("max_input_tokens"))
    return bool(getattr(profile, "max_input_tokens", None))


def _count_tokens_approximately(messages: list[Any], **_kwargs: Any) -> int:
    total = 0
    for message in messages:
        content = getattr(message, "content", message)
        total += max(1, len(str(content)) // 4)
    return total

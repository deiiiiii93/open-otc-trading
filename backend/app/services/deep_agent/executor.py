"""Task-scoped DeepAgent execution adapter."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage
from sqlalchemy.orm import Session

from ...config import Settings, get_settings
from ...models import AgentTask, ContextPackPayload, SessionArtifact, Workflow
from .context_assembler import assemble_context_pack
from .envelopes import Envelope
from .ledger import LedgerWriter
from .personas import report_currency_instruction
from .runtime_config import graph_run_config
from .scheduler import schedule_tasks_from_plan
from .session_lifecycle import acquire_session_lease, release_session_lease
from .task_registry import task_registration


@dataclass(frozen=True)
class TaskExecutionResult:
    task_id: int
    workflow_id: int
    session_id: int
    context_pack_id: int
    checkpointer_key: str
    envelope: str
    raw_result: Any
    artifact: SessionArtifact | None = None
    interrupts: list[Any] | None = None


class TaskExecutor:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def invoke_deep_agent(
        self,
        session: Session,
        *,
        task_id: int,
        agent: Any,
        envelope: Envelope | str = Envelope.DESK_WORKFLOW,
        recent_summary: str | None = None,
    ) -> SessionArtifact | None:
        return self.invoke_deep_agent_result(
            session,
            task_id=task_id,
            agent=agent,
            envelope=envelope,
            recent_summary=recent_summary,
        ).artifact

    def invoke_deep_agent_result(
        self,
        session: Session,
        *,
        task_id: int,
        agent: Any,
        envelope: Envelope | str = Envelope.DESK_WORKFLOW,
        recent_summary: str | None = None,
    ) -> TaskExecutionResult:
        task = session.get(AgentTask, task_id)
        if task is None:
            raise ValueError(f"AgentTask {task_id} not found")
        workflow = session.get(Workflow, task.workflow_id)
        if workflow is None:
            raise ValueError(f"Workflow {task.workflow_id} not found")

        registration = task_registration(task.task_type)
        pack = assemble_context_pack(session, task=task, recent_summary=recent_summary)
        payload_row = session.get(ContextPackPayload, pack.payload_id)
        context_pack_payload = payload_row.stable_payload if payload_row else {}
        agent_session = acquire_session_lease(session, task=task)
        task.status = "in_progress"
        writer = LedgerWriter(session)
        writer.emit_event(
            workflow_id=workflow.id,
            session_id=agent_session.id,
            task_id=task.id,
            kind="task_started",
            payload={
                "task_id": task.id,
                "task_type": task.task_type,
                "session_id": agent_session.id,
                "context_pack_id": pack.id,
            },
        )
        session.flush()

        envelope_value = envelope.value if isinstance(envelope, Envelope) else envelope
        task_thread_id = f"{agent_session.checkpointer_key}:task:{task.id}"
        configurable_extra = {
            "workflow_id": workflow.id,
            "session_id": agent_session.id,
            "task_id": task.id,
            "context_pack_id": pack.id,
            "envelope": envelope_value,
            "tools_scope": sorted(registration.tools_scope),
        }
        from .memory.config import get_memory_config
        from .memory.runtime import latest_user_message_id, memory_configurable
        if get_memory_config().enabled:
            configurable_extra.update(memory_configurable(
                session_id=agent_session.id,
                thread_id=workflow.thread_id,
                persona=agent_session.persona,
                message_id=latest_user_message_id(session, workflow.thread_id),
            ))
        config = graph_run_config(
            self.settings,
            thread_id=task_thread_id,
            configurable_extra=configurable_extra,
            trace_meta={"workflow_id": workflow.id, "task_id": task.id},
        )

        try:
            result = agent.invoke(
                {
                    "messages": [
                        HumanMessage(
                            content=self._task_prompt(
                                task,
                                workflow,
                                pack.id,
                                context_pack_payload,
                                pack.metadata_ or {},
                            )
                        )
                    ]
                },
                config=config,
            )
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)[:1000]
            task.closed_at = datetime.utcnow()
            release_session_lease(
                session,
                session_id=agent_session.id,
                task_id=task.id,
                close_reason="task_failed",
                last_summary=task.error,
            )
            writer.emit_event(
                workflow_id=workflow.id,
                session_id=agent_session.id,
                task_id=task.id,
                kind="task_failed",
                payload={
                    "task_id": task.id,
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:500],
                },
            )
            raise

        if self._has_interrupt(result):
            interrupts = list(result.get("__interrupt__") or [])
            task.status = "awaiting_hitl"
            release_session_lease(
                session,
                session_id=agent_session.id,
                task_id=task.id,
            )
            writer.emit_event(
                workflow_id=workflow.id,
                session_id=agent_session.id,
                task_id=task.id,
                kind="hitl_requested",
                payload={
                    "task_id": task.id,
                    "session_id": agent_session.id,
                    "context_pack_id": pack.id,
                },
            )
            return TaskExecutionResult(
                task_id=task.id,
                workflow_id=workflow.id,
                session_id=agent_session.id,
                context_pack_id=pack.id,
                checkpointer_key=task_thread_id,
                envelope=envelope_value,
                raw_result=result,
                artifact=None,
                interrupts=interrupts,
            )

        artifact_payload = self._artifact_payload(result)
        artifact_kind = self._artifact_kind_for(
            task=task,
            default_kind=registration.output_artifact_kind,
            payload=artifact_payload,
        )
        artifact = writer.write_artifact(
            workflow_id=workflow.id,
            session_id=agent_session.id,
            task_id=task.id,
            context_pack_id=pack.id,
            kind=artifact_kind,
            title=f"{task.task_type} output",
            payload=artifact_payload,
        )
        if artifact.kind == "plan" and isinstance(artifact.payload.get("tasks"), list):
            schedule_tasks_from_plan(
                session,
                planner_task_id=task.id,
                plan_artifact_id=artifact.id,
            )
        task.status = "completed"
        task.closed_at = datetime.utcnow()
        release_session_lease(
            session,
            session_id=agent_session.id,
            task_id=task.id,
            close_reason="return_to_orchestrator",
            last_summary=f"completed task {task.id}",
        )
        writer.emit_event(
            workflow_id=workflow.id,
            session_id=agent_session.id,
            task_id=task.id,
            artifact_id=artifact.id,
            kind="task_completed",
            payload={
                "task_id": task.id,
                "artifact_id": artifact.id,
                "context_pack_id": pack.id,
            },
        )
        return TaskExecutionResult(
            task_id=task.id,
            workflow_id=workflow.id,
            session_id=agent_session.id,
            context_pack_id=pack.id,
            checkpointer_key=task_thread_id,
            envelope=envelope_value,
            raw_result=result,
            artifact=artifact,
            interrupts=[],
        )

    @staticmethod
    def _has_interrupt(result: Any) -> bool:
        return isinstance(result, dict) and bool(result.get("__interrupt__"))

    @staticmethod
    def _artifact_payload(result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            artifact = result.get("artifact")
            if isinstance(artifact, dict):
                return artifact
            final_text = TaskExecutor._final_message_text(result.get("messages") or [])
            if final_text:
                return {"content": final_text}
            return result
        content = getattr(result, "content", None)
        if content is not None:
            return {"content": str(content)}
        return {"result": str(result)}

    @staticmethod
    def _artifact_kind_for(
        *,
        task: AgentTask,
        default_kind: str,
        payload: dict[str, Any],
    ) -> str:
        if task.task_type == "plan_workflow_step" and not isinstance(
            payload.get("tasks"), list
        ):
            return "claim"
        return default_kind

    @staticmethod
    def _final_message_text(messages: list[Any]) -> str:
        for message in reversed(messages):
            content = getattr(message, "content", None)
            if content:
                return content.strip() if isinstance(content, str) else str(content).strip()
        return ""

    @staticmethod
    def _task_prompt(
        task: AgentTask,
        workflow: Workflow,
        context_pack_id: int,
        context_pack_payload: dict[str, Any],
        context_pack_metadata: dict[str, Any],
    ) -> str:
        payload_json = json.dumps(
            context_pack_payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            default=str,
        )
        recent_summary = ""
        if isinstance(context_pack_metadata, dict):
            recent_summary = str(context_pack_metadata.get("recent_session_summary") or "")
        currency = str(context_pack_payload.get("report_currency") or "by_position")
        currency_block = report_currency_instruction(currency)
        return (
            "Handle this desk request using only the payload and scoped tools "
            "provided below.\n\n"
            "Treat "
            "`task_brief.user_message` as the user's message. Follow the ReAct "
            "desk workflow: clarify the user's intent first when needed, then "
            "plan, then act. Never expose task ids, workflow ids, context pack "
            "ids, AgentTask, or routing details to the user.\n\n"
            f"{currency_block}\n\n"
            f"Recent session summary:\n{recent_summary or '(none)'}\n\n"
            f"Context pack payload:\n```json\n{payload_json}\n```"
        )

import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { api } from '../api/client';
import type {
  AgentChannel,
  AgentContextUsage,
  AgentExecutionMode,
  AgentModelConfig,
  AgentModelSelection,
  AgentTodoItem,
  AsyncAgentTask,
  ChatMessage as ChatMessageType,
  Envelope,
  PageContext,
  TaskRun,
  Thread,
  ToolEvent,
} from '../types';
import { useViewMode, type ViewMode } from './useViewMode';

type CostPreview = { tool_name: string; estimated_seconds: number };
type EnvelopeTransition = {
  previous_envelope: Envelope;
  new_envelope: Envelope;
  reason: string;
  denied_tool: string;
};
type Draft = {
  content: string;
  events: ToolEvent[];
  todos?: AgentTodoItem[];
  cost_preview?: CostPreview;
  envelope_transition?: EnvelopeTransition;
};
const EXECUTION_MODE_STORAGE_KEY = 'open-otc:agent:execution-mode';
const EXECUTION_MODES: readonly AgentExecutionMode[] = ['interactive', 'auto', 'yolo'];
const ACTIVE_TASK_STATUSES = new Set(['queued', 'running']);

export type AgentChatController = {
  threads: Thread[];
  activeThreadId: number | null;
  activeThread: Thread | null;
  loading: boolean;
  sending: boolean;
  streaming: boolean;
  streamingItem: ChatMessageType | null;
  error: string | null;
  viewMode: ViewMode;
  channels: AgentChannel[];
  selectedModel: AgentModelSelection | null;
  executionMode: AgentExecutionMode;
  confirmingActionIds: ReadonlySet<string>;
  taskRunsById: Record<number, TaskRun>;
  setSelectedModel: (selection: AgentModelSelection) => void;
  setExecutionMode: Dispatch<SetStateAction<AgentExecutionMode>>;
  setViewMode: (mode: ViewMode) => void;
  selectThread: (id: number) => void;
  createThread: () => Promise<void>;
  renameThread: (threadId: number, title: string) => Promise<void>;
  exportThread: (threadId: number) => Promise<void>;
  deleteThread: (threadId: number) => Promise<void>;
  forkThread: (threadId: number) => Promise<void>;
  sendMessage: (
    message: string,
    pageContext?: PageContext | null,
    accountingDate?: string | null,
    contextUsage?: AgentContextUsage | null,
    envelope?: Envelope,
    confirmedCostPreview?: boolean,
  ) => Promise<void>;
  /**
   * Re-send the most recent user message in the active thread with
   * confirmed_cost_preview=true. Used to approve a long-running tool
   * after the cost-preview gate fired.
   */
  confirmCostPreview: (
    pageContext?: PageContext | null,
    accountingDate?: string | null,
    contextUsage?: AgentContextUsage | null,
    envelope?: Envelope,
  ) => Promise<void>;
  launchWorkflow: (slug: string, mode: 'auto' | 'yolo') => Promise<void>;
  stopStreaming: () => void;
  confirmAction: (messageId: number, actionId: string) => Promise<void>;
  dismissAction: (messageId: number, actionId: string) => Promise<void>;
  refreshModels: () => Promise<void>;
};

export function useAgentChatController(): AgentChatController {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const streaming = draft != null;
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useViewMode();
  const [modelConfig, setModelConfig] = useState<AgentModelConfig | null>(null);
  const [selectedModel, setSelectedModel] = useState<AgentModelSelection | null>(null);
  const [executionMode, setExecutionMode] = useSessionString<AgentExecutionMode>(
    EXECUTION_MODE_STORAGE_KEY,
    'auto',
    EXECUTION_MODES,
  );
  const [confirmingActionIds, setConfirmingActionIds] = useState<Set<string>>(() => new Set());
  const [taskRunsById, setTaskRunsById] = useState<Record<number, TaskRun>>({});
  const [asyncAgentPollNonce, setAsyncAgentPollNonce] = useState(0);
  const streamAbortRef = useRef<AbortController | null>(null);

  const fetchCatalog = useCallback(async () => {
    const cfg = await api<AgentModelConfig>('/api/agent/models');
    setModelConfig(cfg);
    setSelectedModel((current) => (
      current && selectionExists(cfg, current) ? current : cfg.active
    ));
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchCatalog().catch((e) => {
      if (!cancelled) setError(e instanceof Error ? e.message : String(e));
    });
    return () => {
      cancelled = true;
    };
  }, [fetchCatalog]);

  const refreshModels = useCallback(async () => {
    try {
      await api('/api/agent/channels/reload', { method: 'POST' });
      await fetchCatalog();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      throw e;
    }
  }, [fetchCatalog]);

  const refresh = useCallback(async () => {
    const list = await api<Thread[]>('/api/chat/threads');
    setThreads(list);
    setActiveId((current) => {
      if (current != null && list.some((t) => t.id === current)) return current;
      return list[0]?.id ?? null;
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await refresh();
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  useEffect(() => {
    if (activeId == null || sending || streaming) return;
    let cancelled = false;
    let inFlight = false;
    let hadActiveAsyncTask = false;
    let intervalId: number | undefined;

    const tick = async () => {
      if (cancelled || inFlight) return;
      inFlight = true;
      try {
        const rows = await api<AsyncAgentTask[]>(
          `/api/chat/threads/${activeId}/async_agents`,
        );
        if (cancelled) return;
        const hasActiveAsyncTask = Array.isArray(rows) && rows.length > 0;
        if (hasActiveAsyncTask || hadActiveAsyncTask) {
          hadActiveAsyncTask = hasActiveAsyncTask;
          await refresh();
        }
        if (!hasActiveAsyncTask && intervalId !== undefined) {
          window.clearInterval(intervalId);
          intervalId = undefined;
        }
      } catch {
        // Keep background polling quiet; normal sends and manual refresh paths
        // still surface API failures.
      } finally {
        inFlight = false;
      }
    };

    void tick();
    intervalId = window.setInterval(() => {
      void tick();
    }, 4000);
    return () => {
      cancelled = true;
      if (intervalId !== undefined) window.clearInterval(intervalId);
    };
  }, [activeId, asyncAgentPollNonce, refresh, sending, streaming]);

  const watchedTaskIds = useMemo(
    () => collectWatchedTaskIds(threads.find((thread) => thread.id === activeId) ?? null),
    [threads, activeId],
  );

  useEffect(() => {
    if (watchedTaskIds.length === 0) return;
    let cancelled = false;
    let inFlight = false;
    let intervalId: number | undefined;

    const load = async () => {
      if (cancelled || inFlight) return;
      inFlight = true;
      try {
        const rows = await Promise.all(
          watchedTaskIds.map((taskId) => api<TaskRun>(`/api/tasks/${taskId}`)),
        );
        if (cancelled) return;
        setTaskRunsById((prev) => {
          const next = { ...prev };
          for (const row of rows) next[row.id] = row;
          return next;
        });
        const stillActive = rows.some((row) => ACTIVE_TASK_STATUSES.has(row.status));
        if (!stillActive && intervalId !== undefined) {
          window.clearInterval(intervalId);
          intervalId = undefined;
        }
      } catch {
        // Task progress is advisory UI chrome; chat stays usable if a poll
        // races a cleanup or transient backend restart.
      } finally {
        inFlight = false;
      }
    };

    void load();
    intervalId = window.setInterval(() => {
      void load();
    }, 2500);
    return () => {
      cancelled = true;
      if (intervalId !== undefined) window.clearInterval(intervalId);
    };
  }, [watchedTaskIds]);

  const createThread = useCallback(async () => {
    const created = await api<Thread>('/api/chat/threads', {
      method: 'POST',
      body: JSON.stringify({ title: 'New research thread', character: 'trader' }),
    });
    setThreads((prev) => [created, ...prev]);
    setActiveId(created.id);
  }, []);

  const renameThread = useCallback(async (threadId: number, title: string) => {
    setThreads((prev) => prev.map((thread) => (
      thread.id === threadId ? { ...thread, title } : thread
    )));
    try {
      const updated = await api<Thread>(`/api/chat/threads/${threadId}`, {
        method: 'PATCH',
        body: JSON.stringify({ title }),
      });
      setThreads((prev) => prev.map((thread) => (
        thread.id === threadId ? updated : thread
      )));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      await refresh();
    }
  }, [refresh]);

  const exportThread = useCallback(async (threadId: number) => {
    try {
      const exported = await api<Thread & { exported_at: string }>(
        `/api/chat/threads/${threadId}/export`,
      );
      downloadJson(`agent-thread-${threadId}.json`, exported);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const deleteThread = useCallback(async (threadId: number) => {
    try {
      await api<{ ok: boolean; deleted_id: number }>(`/api/chat/threads/${threadId}`, {
        method: 'DELETE',
      });
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [refresh]);

  const forkThread = useCallback(async (threadId: number) => {
    try {
      const created = await api<Thread>(`/api/chat/threads/${threadId}/fork`, {
        method: 'POST',
        body: JSON.stringify({}),
      });
      setThreads((prev) => [created, ...prev]);
      setActiveId(created.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const sendMessage = useCallback(async (
    message: string,
    pageContext?: PageContext | null,
    accountingDate?: string | null,
    contextUsage?: AgentContextUsage | null,
    envelope?: Envelope,
    confirmedCostPreview?: boolean,
  ) => {
    let threadId = activeId;
    if (threadId == null) {
      const created = await api<Thread>('/api/chat/threads', {
        method: 'POST',
        body: JSON.stringify({ title: 'New research thread', character: 'trader' }),
      });
      threadId = created.id;
      setThreads((prev) => [created, ...prev]);
      setActiveId(created.id);
    }

    setError(null);
    setSending(true);
    setDraft({ content: '', events: [] });
    setThreads((prev) => prev.map((thread) => {
      if (thread.id !== threadId) return thread;
      return {
        ...thread,
        messages: [...thread.messages, {
          id: -Date.now(),
          role: 'user',
          content: message,
          meta: {
            ...(contextUsage ? { context_usage: contextUsage } : {}),
            mode: executionMode,
          },
        }],
      };
    }));

    let streamAbortController: AbortController | null = null;
    try {
      streamAbortRef.current?.abort();
      streamAbortController = new AbortController();
      streamAbortRef.current = streamAbortController;
      const response = await fetch(`/api/chat/threads/${threadId}/messages/stream`, {
        method: 'POST',
        signal: streamAbortController.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: message,
          character: 'auto',
          model: selectedModel,
          mode: executionMode,
          page_context: pageContext ?? undefined,
          context_usage: contextUsage ?? undefined,
          accounting_date: accountingDate || undefined,
          envelope: envelope ?? undefined,
          confirmed_cost_preview: confirmedCostPreview ? true : undefined,
        }),
      });
      if (!response.ok) throw new Error(await response.text());
      if (response.body) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let eventType = 'message';
        let dataLines: string[] = [];

        const dispatch = () => {
          const dataPayload = dataLines.join('\n');
          dataLines = [];
          const currentEventType = eventType;
          eventType = 'message';
          if (!dataPayload) return;
          handleSseEvent(currentEventType, dataPayload, setDraft);
        };

        const processLine = (line: string) => {
          if (line === '') {
            dispatch();
            return;
          }
          if (line.startsWith('event:')) {
            eventType = line.slice(6).trim() || 'message';
            return;
          }
          if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).trim());
          }
        };

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';
          for (const line of lines) processLine(line.trimEnd());
        }
        if (buffer) processLine(buffer.trimEnd());
        dispatch();
      }
      setDraft(null);
      await refresh();
    } catch (e) {
      if (isAbortError(e)) {
        if (streamAbortRef.current === streamAbortController) {
          setDraft(null);
          void refresh().catch((refreshError) => {
            setError(refreshError instanceof Error ? refreshError.message : String(refreshError));
          });
        }
      } else {
        if (streamAbortRef.current === streamAbortController) {
          setError(e instanceof Error ? e.message : String(e));
        }
      }
    } finally {
      if (streamAbortRef.current === streamAbortController) {
        streamAbortRef.current = null;
        setSending(false);
      }
    }
  }, [activeId, refresh, selectedModel, executionMode]);

  const launchWorkflow = useCallback(async (slug: string, mode: 'auto' | 'yolo') => {
    let threadId = activeId;
    if (threadId == null) {
      const created = await api<Thread>('/api/chat/threads', {
        method: 'POST',
        body: JSON.stringify({ title: 'New research thread', character: 'trader' }),
      });
      threadId = created.id;
      setThreads((prev) => [created, ...prev]);
      setActiveId(created.id);
    }

    setError(null);
    setSending(true);
    setDraft({ content: '', events: [] });
    let workflowError: string | null = null;

    let streamAbortController: AbortController | null = null;
    try {
      streamAbortRef.current?.abort();
      streamAbortController = new AbortController();
      streamAbortRef.current = streamAbortController;
      const response = await fetch(`/api/chat/threads/${threadId}/workflows/${slug}/run`, {
        method: 'POST',
        signal: streamAbortController.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      });
      if (!response.ok) throw new Error(await response.text());
      if (response.body) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let eventType = 'message';
        let dataLines: string[] = [];

        const dispatch = () => {
          const dataPayload = dataLines.join('\n');
          const currentEventType = eventType;
          dataLines = [];
          eventType = 'message';
          if (!dataPayload) return;
          if (currentEventType.startsWith('workflow.')) {
            // Render workflow framing as inline markers in the live draft.
            let parsed: Record<string, unknown> = {};
            try {
              parsed = JSON.parse(dataPayload) as Record<string, unknown>;
            } catch {
              return;
            }
            let marker = '';
            if (currentEventType === 'workflow.step.start') {
              marker = `\n\n▶ Step ${parsed.index}\n`;
            } else if (currentEventType === 'workflow.log') {
              marker = `\n_${String(parsed.message ?? '')}_\n`;
            } else if (currentEventType === 'workflow.step.error') {
              workflowError = `Workflow step ${parsed.index} failed: ${String(parsed.message ?? '')}`;
              marker = `\n\n⚠️ Step ${parsed.index} failed: ${String(parsed.message ?? '')}\n`;
            } else if (currentEventType === 'workflow.complete') {
              marker = `\n\n✅ Workflow complete (${parsed.steps} steps)\n`;
            }
            if (marker) {
              setDraft((prev) => ({
                ...(prev ?? { events: [] }),
                content: (prev?.content ?? '') + marker,
                events: prev?.events ?? [],
              }));
            }
            return;
          }
          handleSseEvent(currentEventType, dataPayload, setDraft);
        };

        const processLine = (line: string) => {
          if (line === '') {
            dispatch();
            return;
          }
          if (line.startsWith('event:')) {
            eventType = line.slice(6).trim() || 'message';
            return;
          }
          if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).trim());
          }
        };

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';
          for (const line of lines) processLine(line.trimEnd());
        }
        if (buffer) processLine(buffer.trimEnd());
        dispatch();
      }
      setDraft(null);
      await refresh();
      // A step failure is otherwise lost when the refresh replaces the draft —
      // surface it durably.
      if (workflowError) setError(workflowError);
    } catch (e) {
      if (isAbortError(e)) {
        if (streamAbortRef.current === streamAbortController) {
          setDraft(null);
          void refresh().catch((refreshError) => {
            setError(refreshError instanceof Error ? refreshError.message : String(refreshError));
          });
        }
      } else if (streamAbortRef.current === streamAbortController) {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      if (streamAbortRef.current === streamAbortController) {
        streamAbortRef.current = null;
        setSending(false);
      }
    }
  }, [activeId, refresh]);

  const stopStreaming = useCallback(() => {
    streamAbortRef.current?.abort();
    setDraft(null);
    setSending(false);
  }, []);

  const confirmAction = useCallback(async (messageId: number, actionId: string) => {
    if (activeId == null) return;
    const key = actionKey(messageId, actionId);
    setConfirmingActionIds((prev) => {
      const next = new Set(prev);
      next.add(key);
      return next;
    });
    setThreads((prev) => markActionStatus(prev, messageId, actionId, 'confirmed'));
    try {
      await api<ChatMessageType>(
        `/api/chat/threads/${activeId}/messages/${messageId}/actions/${actionId}/confirm`,
        { method: 'POST' },
      );
      await refresh();
      setAsyncAgentPollNonce((value) => value + 1);
    } catch (e) {
      setThreads((prev) => markActionStatus(prev, messageId, actionId, 'failed'));
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setConfirmingActionIds((prev) => {
        if (!prev.has(key)) return prev;
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }
  }, [activeId, refresh]);

  const dismissAction = useCallback(async (messageId: number, actionId: string) => {
    if (activeId == null) return;
    setThreads((prev) => markActionStatus(prev, messageId, actionId, 'dismissed'));
    try {
      const newAssistantMessage = await api<ChatMessageType>(
        `/api/chat/threads/${activeId}/messages/${messageId}/actions/${actionId}/dismiss`,
        { method: 'POST' },
      );
      setThreads((prev) => appendMessageToThread(prev, activeId, newAssistantMessage));
    } catch (e) {
      setThreads((prev) => markActionStatus(prev, messageId, actionId, 'failed'));
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [activeId]);

  const activeThread = useMemo(
    () => threads.find((thread) => thread.id === activeId) ?? null,
    [activeId, threads],
  );

  const confirmCostPreview = useCallback(async (
    pageContext?: PageContext | null,
    accountingDate?: string | null,
    contextUsage?: AgentContextUsage | null,
    envelope?: Envelope,
  ) => {
    const thread = threads.find((t) => t.id === activeId);
    if (!thread) return;
    const lastUser = [...thread.messages].reverse().find((m) => m.role === 'user');
    if (!lastUser) return;
    await sendMessage(
      lastUser.content,
      pageContext ?? null,
      accountingDate ?? null,
      contextUsage ?? null,
      envelope,
      true,
    );
  }, [activeId, threads, sendMessage]);

  const streamingItem: ChatMessageType | null = draft
    ? {
        id: -1,
        role: 'assistant',
        character: activeThread?.character ?? 'trader',
        content: draft.content,
        meta: {
          process_events: draft.events,
          todos: draft.todos,
          cost_preview: draft.cost_preview,
          envelope_transition: draft.envelope_transition,
        },
      }
    : null;

  return {
    threads,
    activeThreadId: activeId,
    activeThread,
    loading,
    sending,
    streaming,
    streamingItem,
    error,
    viewMode,
    channels: modelConfig?.channels ?? [],
    selectedModel,
    executionMode,
    confirmingActionIds,
    taskRunsById,
    setSelectedModel,
    setExecutionMode,
    setViewMode,
    selectThread: setActiveId,
    createThread,
    renameThread,
    exportThread,
    deleteThread,
    forkThread,
    sendMessage,
    launchWorkflow,
    confirmCostPreview,
    stopStreaming,
    confirmAction,
    dismissAction,
    refreshModels,
  };
}

function useSessionString<T extends string>(
  key: string,
  defaultValue: T,
  allowed: readonly T[],
): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    if (typeof window === 'undefined') return defaultValue;
    try {
      const stored = window.sessionStorage.getItem(key);
      return stored != null && (allowed as readonly string[]).includes(stored)
        ? (stored as T)
        : defaultValue;
    } catch {
      return defaultValue;
    }
  });

  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      window.sessionStorage.setItem(key, value);
    } catch {
      // Ignore storage failures; the in-memory selection still works.
    }
  }, [key, value]);

  return [value, setValue];
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException
    ? error.name === 'AbortError'
    : error instanceof Error && error.name === 'AbortError';
}

function downloadJson(filename: string, data: unknown) {
  if (!('createObjectURL' in URL)) return;
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const href = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = href;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(href);
}

function selectionExists(
  cfg: AgentModelConfig,
  selected: AgentModelSelection,
): boolean {
  return cfg.channels.some((channel) => (
    channel.name === selected.channel
    && channel.models.some((model) => (
      model.provider === selected.provider && model.model === selected.model
    ))
  ));
}

export type WorkflowSseEvent = { type: string; data: Record<string, unknown> };

/** Parse a complete SSE text blob into ordered events (pure; for tests/non-streaming). */
export function parseWorkflowSse(text: string): WorkflowSseEvent[] {
  const events: WorkflowSseEvent[] = [];
  let eventType = 'message';
  let dataLines: string[] = [];
  const flush = () => {
    if (dataLines.length) {
      try {
        events.push({ type: eventType, data: JSON.parse(dataLines.join('\n')) });
      } catch {
        /* skip malformed */
      }
    }
    eventType = 'message';
    dataLines = [];
  };
  for (const line of text.split('\n')) {
    if (line === '') {
      flush();
    } else if (line.startsWith('event:')) {
      eventType = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim());
    }
  }
  flush();
  return events;
}

function handleSseEvent(
  eventType: string,
  dataPayload: string,
  setDraft: Dispatch<SetStateAction<Draft | null>>,
) {
  if (eventType === 'heartbeat') return;
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(dataPayload) as Record<string, unknown>;
  } catch {
    return;
  }

  if (eventType === 'token') {
    setDraft((prev) => ({
      ...(prev ?? {}),
      content: (prev?.content ?? '') + (typeof parsed.text === 'string' ? parsed.text : ''),
      events: prev?.events ?? [],
    }));
    return;
  }

  if (eventType === 'tool_start') {
    const id = typeof parsed.id === 'string' ? parsed.id : '';
    const name = typeof parsed.name === 'string' ? parsed.name : 'tool';
    const newEvent: ToolEvent = {
      id,
      name,
      status: 'running',
      args: parsed.args as ToolEvent['args'],
    };
    setDraft((prev) => ({
      ...(prev ?? {}),
      content: prev?.content ?? '',
      events: [...(prev?.events ?? []), newEvent],
    }));
    return;
  }

  if (eventType === 'tool_end') {
    setDraft((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        events: prev.events.map((ev) =>
          ev.id === parsed.id
            ? {
                ...ev,
                status: parsed.error ? 'error' : 'done',
                duration_ms:
                  typeof parsed.duration_ms === 'number' ? parsed.duration_ms : undefined,
                output: parsed.output,
                error: typeof parsed.error === 'string' ? parsed.error : undefined,
              }
            : ev,
        ),
      };
    });
    return;
  }

  if (eventType === 'todo_update') {
    const todos = normalizeTodoUpdate(parsed.todos);
    if (!todos) return;
    setDraft((prev) => ({
      ...(prev ?? {}),
      content: prev?.content ?? '',
      events: prev?.events ?? [],
      todos,
    }));
    return;
  }

  if (eventType === 'error') {
    const message = typeof parsed.message === 'string' ? parsed.message : 'Stream error';
    setDraft((prev) => ({
      ...(prev ?? {}),
      content: prev?.content || message,
      events: prev?.events ?? [],
    }));
    return;
  }

  if (eventType === 'cost_preview_required') {
    const tool_name = typeof parsed.tool_name === 'string' ? parsed.tool_name : 'tool';
    const estimated_seconds =
      typeof parsed.estimated_seconds === 'number' ? parsed.estimated_seconds : 0;
    setDraft((prev) => ({
      ...(prev ?? {}),
      content: prev?.content ?? '',
      events: prev?.events ?? [],
      cost_preview: { tool_name, estimated_seconds },
    }));
    return;
  }

  if (eventType === 'envelope_transitioned') {
    const prevEnv = parsed.previous_envelope;
    const newEnv = parsed.new_envelope;
    if (typeof prevEnv === 'string' && typeof newEnv === 'string') {
      setDraft((prev) => ({
        ...(prev ?? {}),
        // Drop the first-pass refusal prose streamed under the narrow envelope;
        // the runtime re-streams the real answer under the widened envelope.
        // Mirrors the backend's reset_user_facing_output_for_retry so the live
        // bubble matches the persisted message. Tool events are kept as a trace.
        content: '',
        events: prev?.events ?? [],
        envelope_transition: {
          previous_envelope: prevEnv as Envelope,
          new_envelope: newEnv as Envelope,
          reason: typeof parsed.reason === 'string' ? parsed.reason : '',
          denied_tool: typeof parsed.denied_tool === 'string' ? parsed.denied_tool : '',
        },
      }));
    }
  }
}

function normalizeTodoUpdate(value: unknown): AgentTodoItem[] | null {
  if (!Array.isArray(value)) return null;
  const out: AgentTodoItem[] = [];
  for (const item of value) {
    if (!item || typeof item !== 'object') continue;
    const record = item as Record<string, unknown>;
    const content = typeof record.content === 'string' ? record.content.trim() : '';
    const status = record.status;
    if (!content) continue;
    if (status !== 'pending' && status !== 'in_progress' && status !== 'completed') {
      continue;
    }
    out.push({ content, status });
  }
  return out;
}

function appendMessageToThread(
  threads: Thread[],
  threadId: number,
  message: ChatMessageType,
): Thread[] {
  return threads.map((thread) => {
    if (thread.id !== threadId) return thread;
    const already = thread.messages.some((m) => m.id === message.id);
    if (already) return thread;
    return { ...thread, messages: [...thread.messages, message] };
  });
}

function markActionStatus(
  threads: Thread[],
  messageId: number,
  actionId: string,
  status: 'confirmed' | 'dismissed' | 'failed',
): Thread[] {
  return threads.map((thread) => ({
    ...thread,
    messages: thread.messages.map((message) => {
      const actions = message.meta?.pending_actions;
      if (message.id !== messageId || !Array.isArray(actions)) return message;
      return {
        ...message,
        meta: {
          ...message.meta,
          pending_actions: actions.map((action) =>
            action.id === actionId ? { ...action, status } : action,
          ),
        },
      };
    }),
  }));
}

function actionKey(messageId: number, actionId: string): string {
  return `${messageId}:${actionId}`;
}

function collectWatchedTaskIds(thread: Thread | null): number[] {
  if (!thread) return [];
  const ids = new Set<number>();
  for (const message of thread.messages) {
    const actions = message.meta?.pending_actions;
    if (!Array.isArray(actions)) continue;
    for (const action of actions) {
      const taskId = action.task_id ?? action.async_task_id;
      if (typeof taskId === 'number' && Number.isFinite(taskId)) {
        ids.add(taskId);
      }
    }
  }
  return Array.from(ids).sort((a, b) => a - b);
}

import { useId, useMemo, useState, type FormEvent } from 'react';
import { Download, GitFork, Pencil, Plus, Search, Trash2, Waypoints } from 'lucide-react';
import {
  personaDisplayLabel,
  type AgentAsset,
  type AgentChannel,
  type AgentModelSelection,
  type ChatMessage as ChatMessageType,
  type TaskRun,
  type Thread,
} from '../types';
import { ConversationalWorkspace } from '../components/templates';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import { MessageList } from '../components/MessageList';
import { AssetsPane } from '../components/AssetsPane';
import { ChatComposer } from '../components/ChatComposer';
import type { ViewMode } from '../hooks/useViewMode';
import './AgentDesk.css';

type Props = {
  threads: Thread[];
  activeThreadId: number | null;
  sending: boolean;
  streaming?: boolean;
  streamingItem?: ChatMessageType | null;
  viewMode: ViewMode;
  channels?: AgentChannel[];
  selectedModel?: AgentModelSelection | null;
  yoloMode?: boolean;
  confirmingActionIds?: ReadonlySet<string>;
  taskRunsById?: Record<number, TaskRun>;
  onChangeModel?: (s: AgentModelSelection) => void;
  onChangeYoloMode?: (enabled: boolean) => void;
  onStopStreaming?: () => void;
  onRefreshModels?: () => void;
  onChangeViewMode: (mode: ViewMode) => void;
  onSelectThread: (id: number) => void;
  onNewThread: () => void;
  onRenameThread: (id: number, title: string) => void;
  onExportThread: (id: number) => void;
  onDeleteThread: (id: number) => void;
  onForkThread: (id: number) => void;
  onOpenTrace?: (threadId: number) => void;
  onSend: (message: string) => void;
  onConfirmAction: (messageId: number, actionId: string) => void;
  onDismissAction: (messageId: number, actionId: string) => void;
  onConfirmCostPreview?: () => void;
};

function collectAssets(thread: Thread | null): AgentAsset[] {
  if (!thread) return [];
  // Dedupe by id, last-wins. Per-message context assets (current-page-context,
  // latest-pricing-results, risk-positions) get re-emitted every turn with the
  // same id; only the freshest snapshot is meaningful. File-derived assets have
  // path-unique ids so they survive untouched.
  const byId = new Map<string, AgentAsset>();
  for (const msg of thread.messages) {
    const meta = msg.meta ?? {};
    const assets = Array.isArray(meta.assets) ? (meta.assets as AgentAsset[]) : [];
    for (const asset of assets) byId.set(asset.id, asset);
  }
  return Array.from(byId.values());
}

export function AgentDesk({
  threads,
  activeThreadId,
  sending,
  streaming,
  streamingItem = null,
  viewMode,
  channels,
  selectedModel,
  yoloMode,
  confirmingActionIds,
  taskRunsById,
  onChangeModel,
  onChangeYoloMode,
  onStopStreaming,
  onRefreshModels,
  onChangeViewMode,
  onSelectThread,
  onNewThread,
  onRenameThread,
  onExportThread,
  onDeleteThread,
  onForkThread,
  onOpenTrace,
  onSend,
  onConfirmAction,
  onDismissAction,
  onConfirmCostPreview,
}: Props) {
  const activeThread = useMemo(
    () => threads.find((t) => t.id === activeThreadId) ?? null,
    [threads, activeThreadId],
  );
  const assets = useMemo(() => collectAssets(activeThread), [activeThread]);

  const chips: string[] = [];
  if (activeThread) {
    chips.push(personaDisplayLabel(activeThread.character));
    chips.push(`${activeThread.messages.length} messages`);
  }

  const actions = (
    <div className="wl-agent-desk__actions">
      <fieldset className="wl-agent-desk__view-mode" aria-label="Tool detail level">
        <button
          type="button"
          aria-pressed={viewMode === 'detailed'}
          className={`wl-agent-desk__view-mode-btn${viewMode === 'detailed' ? ' is-active' : ''}`}
          onClick={() => onChangeViewMode('detailed')}
        >
          Detailed
        </button>
        <button
          type="button"
          aria-pressed={viewMode === 'compact'}
          className={`wl-agent-desk__view-mode-btn${viewMode === 'compact' ? ' is-active' : ''}`}
          onClick={() => onChangeViewMode('compact')}
        >
          Compact
        </button>
      </fieldset>
    </div>
  );

  const messages = (
    <div className="wl-agent-desk__messages">
      {!activeThread ? (
        <Empty
          message="Start a thread to ask the agent for pricing, risk, or research."
          symbol="o"
          action={
            <Button variant="primary" onClick={onNewThread}>
              <Plus size={16} aria-hidden="true" />
              New Thread
            </Button>
          }
        />
      ) : activeThread.messages.length === 0 && !streamingItem ? (
        <Empty message="No messages yet - type below to start." symbol="o" />
      ) : (
        <MessageList
          items={activeThread.messages}
          streamingItem={streamingItem}
          streaming={!!streaming}
          viewMode={viewMode}
          channels={channels}
          confirmingActionIds={confirmingActionIds}
          taskRunsById={taskRunsById}
          onConfirmAction={onConfirmAction}
          onDismissAction={onDismissAction}
          onSelectReplyOption={(_, option) => onSend(option)}
          onConfirmCostPreview={onConfirmCostPreview}
          replyOptionsDisabled={sending || !!streaming}
        />
      )}
    </div>
  );

  const composer = (
    <ChatComposer
      onSend={onSend}
      sending={sending}
      streaming={streaming}
      channels={channels}
      selectedModel={selectedModel}
      yoloMode={yoloMode}
      onChangeModel={onChangeModel}
      onChangeYoloMode={onChangeYoloMode}
      onStopStreaming={onStopStreaming}
      onRefreshModels={onRefreshModels}
    />
  );

  return (
    <ConversationalWorkspace
      title="AGENT DESK"
      chips={chips}
      actions={actions}
      rail={
        <ThreadRail
          threads={threads}
          activeThreadId={activeThreadId}
          actionsDisabled={sending || !!streaming}
          onSelectThread={onSelectThread}
          onNewThread={onNewThread}
          onRenameThread={onRenameThread}
          onExportThread={onExportThread}
          onDeleteThread={onDeleteThread}
          onForkThread={onForkThread}
          onOpenTrace={onOpenTrace}
        />
      }
      messages={messages}
      composer={composer}
      contextPane={<AssetsPane assets={assets} />}
    />
  );
}

type ThreadRailProps = {
  threads: Thread[];
  activeThreadId: number | null;
  actionsDisabled: boolean;
  onSelectThread: (id: number) => void;
  onNewThread: () => void;
  onRenameThread: (id: number, title: string) => void;
  onExportThread: (id: number) => void;
  onDeleteThread: (id: number) => void;
  onForkThread: (id: number) => void;
  onOpenTrace?: (threadId: number) => void;
};

function ThreadRail({
  threads,
  activeThreadId,
  actionsDisabled,
  onSelectThread,
  onNewThread,
  onRenameThread,
  onExportThread,
  onDeleteThread,
  onForkThread,
  onOpenTrace,
}: ThreadRailProps) {
  const searchInputId = useId();
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draftTitle, setDraftTitle] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [showArena, setShowArena] = useState(false);

  const filteredThreads = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return threads.filter((thread) => {
      if (!showArena && thread.source === 'arena') return false;
      if (!query) return true;
      const haystack = [
        thread.title,
        ...thread.messages.map((message) => message.content),
      ].join(' ').toLowerCase();
      return haystack.includes(query);
    });
  }, [threads, searchQuery, showArena]);

  const startRename = (thread: Thread) => {
    setEditingId(thread.id);
    setDraftTitle(thread.title);
  };

  const submitRename = (event: FormEvent, thread: Thread) => {
    event.preventDefault();
    const title = draftTitle.trim();
    if (title && title !== thread.title) onRenameThread(thread.id, title);
    setEditingId(null);
  };

  const confirmDelete = (thread: Thread) => {
    if (window.confirm(`Delete "${thread.title}"? This removes the full thread history.`)) {
      onDeleteThread(thread.id);
    }
  };

  return (
    <aside className="wl-agent-desk__threads" aria-label="Threads">
      <div className="wl-agent-desk__threads-head">
        <div>
          <div className="wl-agent-desk__eyebrow">Threads</div>
          <div className="wl-agent-desk__threads-count">
            {searchQuery.trim() ? `${filteredThreads.length} of ${threads.length}` : `${threads.length} total`}
          </div>
        </div>
        <Button variant="ghost" iconOnly onClick={onNewThread} aria-label="New thread">
          <Plus size={16} aria-hidden="true" />
        </Button>
      </div>

      <label className="wl-agent-desk__thread-search" htmlFor={searchInputId}>
        <Search size={14} aria-hidden="true" />
        <input
          id={searchInputId}
          type="search"
          value={searchQuery}
          placeholder="Search threads..."
          onChange={(event) => setSearchQuery(event.target.value)}
          aria-label="Search threads by name or content"
        />
      </label>

      <label className="wl-agent-desk__arena-toggle">
        <input
          type="checkbox"
          checked={showArena}
          onChange={(event) => setShowArena(event.target.checked)}
        />
        Show arena threads
      </label>

      <div className="wl-agent-desk__thread-list">
        {threads.length === 0 ? (
          <div className="wl-agent-desk__thread-empty">No saved threads.</div>
        ) : filteredThreads.length === 0 ? (
          <div className="wl-agent-desk__thread-empty">No threads match this search.</div>
        ) : (
          filteredThreads.map((thread) => {
            const active = thread.id === activeThreadId;
            const editing = thread.id === editingId;
            return (
              <article
                key={thread.id}
                className={`wl-agent-desk__thread-card${active ? ' is-active' : ''}`}
              >
                {editing ? (
                  <form className="wl-agent-desk__rename-form" onSubmit={(event) => submitRename(event, thread)}>
                    <input
                      className="wl-agent-desk__rename-input"
                      value={draftTitle}
                      autoFocus
                      onChange={(event) => setDraftTitle(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Escape') setEditingId(null);
                      }}
                      aria-label={`Rename ${thread.title}`}
                    />
                    <Button variant="primary" type="submit">
                      Save
                    </Button>
                  </form>
                ) : (
                  <>
                    <button
                      type="button"
                      className="wl-agent-desk__thread-main"
                      aria-current={active ? 'true' : undefined}
                      onClick={() => onSelectThread(thread.id)}
                    >
                      <span className="wl-agent-desk__thread-title">{thread.title}</span>
                      <span className="wl-agent-desk__thread-meta">
                        {thread.character} - {thread.messages.length} messages
                      </span>
                    </button>
                    <div className="wl-agent-desk__thread-tools" aria-label={`Actions for ${thread.title}`}>
                      {onOpenTrace && (
                        <button
                          type="button"
                          className="wl-agent-desk__icon-btn"
                          aria-label={`View trace for ${thread.title}`}
                          onClick={() => onOpenTrace(thread.id)}
                        >
                          <Waypoints size={14} aria-hidden="true" />
                        </button>
                      )}
                      <button
                        type="button"
                        className="wl-agent-desk__icon-btn"
                        aria-label={`Rename ${thread.title}`}
                        disabled={actionsDisabled}
                        onClick={() => startRename(thread)}
                      >
                        <Pencil size={14} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="wl-agent-desk__icon-btn"
                        aria-label={`Export ${thread.title}`}
                        onClick={() => onExportThread(thread.id)}
                      >
                        <Download size={14} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="wl-agent-desk__icon-btn"
                        aria-label={`Fork ${thread.title}`}
                        disabled={actionsDisabled}
                        onClick={() => onForkThread(thread.id)}
                      >
                        <GitFork size={14} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="wl-agent-desk__icon-btn wl-agent-desk__icon-btn--danger"
                        aria-label={`Delete ${thread.title}`}
                        disabled={actionsDisabled}
                        onClick={() => confirmDelete(thread)}
                      >
                        <Trash2 size={14} aria-hidden="true" />
                      </button>
                    </div>
                  </>
                )}
              </article>
            );
          })
        )}
      </div>
    </aside>
  );
}

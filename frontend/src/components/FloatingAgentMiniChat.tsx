import {
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Plus,
  RefreshCw,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import type { AgentContextUsage, Envelope, PageContext } from '../types';
import type { AgentChatController } from '../hooks/useAgentChatController';
import { envelopeBadgeLabel } from '../lib/envelope';
import { Button } from './Button';
import { ChatComposer } from './ChatComposer';
import { Empty } from './Empty';
import { MessageList } from './MessageList';

type Props = {
  controller: AgentChatController;
  pageContext: PageContext;
  accountingDate?: string;
  onOpenDesk: () => void;
};

export function FloatingAgentMiniChat({
  controller,
  pageContext,
  accountingDate,
  onOpenDesk,
}: Props) {
  const activeThread = controller.activeThread;
  const recentMessages = activeThread?.messages.slice(-6) ?? [];
  const [contextOpen, setContextOpen] = useState(false);
  const [contextUpdatedAt, setContextUpdatedAt] = useState(() => new Date());
  const [creatingThread, setCreatingThread] = useState(false);

  useEffect(() => {
    setContextUpdatedAt(new Date());
  }, [pageContext]);

  const contextUsage = useMemo(
    () => measurePageContextUsage(pageContext, contextUpdatedAt),
    [pageContext, contextUpdatedAt],
  );
  const lastSentUsage = useMemo(() => {
    const messages = activeThread?.messages ?? [];
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const usage = messages[i].meta?.context_usage;
      if (usage) return usage;
      const contextUsed = messages[i].meta?.context_used;
      if (contextUsed) return measurePageContextUsage(contextUsed);
    }
    return null;
  }, [activeThread]);
  const snapshotKeyCount = Object.keys(pageContext.snapshot ?? {}).length;
  const entityIdCount = Object.keys(pageContext.entity_ids ?? {}).length;

  // Latest assistant message under this thread carries the envelope the
  // runtime ended on (after any escalation). Default to pet_page for the
  // first turn so the badge reads consistently before anything has run.
  const badgeEnvelope: Envelope = useMemo(() => {
    const messages = activeThread?.messages ?? [];
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role !== 'assistant') continue;
      const env = messages[i].meta?.envelope_final;
      if (env === 'pet_page' || env === 'pet_diagnostic'
          || env === 'desk_workflow' || env === 'desk_async') {
        return env;
      }
    }
    return 'pet_page';
  }, [activeThread]);

  const handleNewThread = async () => {
    if (creatingThread) return;
    setCreatingThread(true);
    try {
      await controller.createThread();
      setContextUpdatedAt(new Date());
      setContextOpen(false);
    } finally {
      setCreatingThread(false);
    }
  };

  return (
    <div className="wl-agent-mini">
      <div className="wl-agent-mini__bar">
        <div className="wl-agent-mini__bar-title">
          <span className="wl-agent-mini__eyebrow">Loaded context</span>
          <strong>{pageContext.title}</strong>
          <span
            className="wl-agent-mini__envelope-badge"
            data-envelope={badgeEnvelope}
            title={`Capability scope: ${badgeEnvelope}`}
          >
            {envelopeBadgeLabel(badgeEnvelope)}
          </span>
        </div>
        <div className="wl-agent-mini__bar-actions">
          <Button
            type="button"
            variant="ghost"
            onClick={handleNewThread}
            disabled={creatingThread || controller.loading || controller.sending || controller.streaming}
          >
            <Plus size={14} aria-hidden="true" />
            {creatingThread ? 'New...' : 'New'}
          </Button>
          <Button type="button" variant="ghost" onClick={onOpenDesk}>
            <ExternalLink size={14} aria-hidden="true" />
            Open full desk
          </Button>
        </div>
      </div>

      <section
        className={`wl-agent-mini__context wl-agent-mini__context--${contextUsage.warning_level}`}
      >
        <div className="wl-agent-mini__context-main">
          <span className="wl-agent-mini__route">{pageContext.route}</span>
          <span className="wl-agent-mini__path" title={pageContext.path}>{pageContext.path}</span>
          {pageContext.chips.slice(0, 3).map((chip) => (
            <span key={chip} className="wl-agent-mini__chip">{chip}</span>
          ))}
        </div>
        <div className="wl-agent-mini__usage">
          <span>{formatBytes(contextUsage.bytes)}</span>
          <span>{contextUsage.estimated_tokens.toLocaleString()} tok est.</span>
          <span>{contextUsage.chip_count} chips</span>
          <span>Updated {formatUsageTime(contextUsage.computed_at)}</span>
          {lastSentUsage && (
            <span>Last sent {lastSentUsage.estimated_tokens.toLocaleString()} tok</span>
          )}
        </div>
        {contextUsage.warning_level !== 'none' && (
          <div className="wl-agent-mini__warning">
            Full context will still be sent.
          </div>
        )}
        <div className="wl-agent-mini__context-actions">
          <Button
            type="button"
            variant="ghost"
            onClick={() => setContextUpdatedAt(new Date())}
          >
            <RefreshCw size={14} aria-hidden="true" />
            Refresh context
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => setContextOpen((open) => !open)}
            aria-expanded={contextOpen}
          >
            {contextOpen
              ? <ChevronUp size={14} aria-hidden="true" />
              : <ChevronDown size={14} aria-hidden="true" />}
            Context
          </Button>
        </div>
        {contextOpen && (
          <div className="wl-agent-mini__drawer">
            <div className="wl-agent-mini__counts">
              <span>entity_ids {entityIdCount}</span>
              <span>snapshot {snapshotKeyCount}</span>
              <span>chips {pageContext.chips.length}</span>
            </div>
            <pre>{JSON.stringify(pageContext, null, 2)}</pre>
          </div>
        )}
      </section>

      <div className="wl-agent-mini__messages">
        {controller.loading ? (
          <Empty message="Loading agent thread..." symbol="o" />
        ) : controller.error ? (
          <Empty message={`Agent unavailable: ${controller.error}`} symbol="!" />
        ) : recentMessages.length === 0 && !controller.streamingItem ? (
          <Empty message="Ask about the current page or dialog." symbol="o" />
        ) : (
          <MessageList
            items={recentMessages}
            streamingItem={controller.streamingItem}
            streaming={controller.streaming}
            viewMode="compact"
            channels={controller.channels}
            confirmingActionIds={controller.confirmingActionIds}
            taskRunsById={controller.taskRunsById}
            onConfirmAction={controller.confirmAction}
            onDismissAction={controller.dismissAction}
            onSelectReplyOption={(_, option) => controller.sendMessage(
              option,
              pageContext,
              accountingDate,
              measurePageContextUsage(pageContext, new Date()),
              'pet_page',
            )}
            onConfirmCostPreview={() => controller.confirmCostPreview(
              pageContext,
              accountingDate,
              measurePageContextUsage(pageContext, new Date()),
              'pet_page',
            )}
            replyOptionsDisabled={controller.sending || controller.streaming}
          />
        )}
      </div>

      <div className="wl-agent-mini__recent">
        <span>Recent {recentMessages.length} / full desk</span>
        <button type="button" onClick={onOpenDesk}>Open</button>
      </div>

      <ChatComposer
        onSend={(message) => controller.sendMessage(
          message,
          pageContext,
          accountingDate,
          measurePageContextUsage(pageContext, new Date()),
          'pet_page',
        )}
        sending={controller.sending}
        streaming={controller.streaming}
        channels={controller.channels}
        selectedModel={controller.selectedModel}
        yoloMode={controller.yoloMode}
        onChangeModel={controller.setSelectedModel}
        onChangeYoloMode={controller.setYoloMode}
        onStopStreaming={controller.stopStreaming}
        onRefreshModels={controller.refreshModels}
        compactModelPicker
      />
    </div>
  );
}

export function measurePageContextUsage(
  pageContext: PageContext,
  computedAt: Date = new Date(),
): AgentContextUsage {
  const json = JSON.stringify(pageContext);
  const bytes = typeof TextEncoder !== 'undefined'
    ? new TextEncoder().encode(json).length
    : json.length;
  const warningLevel =
    json.length > 150_000 ? 'huge' : json.length > 50_000 ? 'large' : 'none';
  return {
    bytes,
    estimated_tokens: Math.ceil(json.length / 4),
    chip_count: pageContext.chips.length,
    snapshot_key_count: Object.keys(pageContext.snapshot ?? {}).length,
    entity_id_count: Object.keys(pageContext.entity_ids ?? {}).length,
    warning_level: warningLevel,
    computed_at: computedAt.toISOString(),
  };
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatUsageTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'now';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

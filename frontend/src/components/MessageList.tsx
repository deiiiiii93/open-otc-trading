import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ChatBubble } from './ChatBubble';
import { NewMessagesPill } from './NewMessagesPill';
import { useStickyScroll } from '../hooks/useStickyScroll';
import type { AgentChannel, ChatMessage as ChatMessageType, TaskRun } from '../types';
import type { ViewMode } from '../hooks/useViewMode';
import './MessageList.css';

type Props = {
  items: ChatMessageType[];
  streamingItem?: ChatMessageType | null;
  streaming: boolean;
  viewMode: ViewMode;
  channels?: AgentChannel[];
  confirmingActionIds?: ReadonlySet<string>;
  taskRunsById?: Record<number, TaskRun>;
  onConfirmAction: (messageId: number, actionId: string) => void;
  onDismissAction: (messageId: number, actionId: string) => void;
  onSelectReplyOption?: (messageId: number, option: string) => void;
  onConfirmCostPreview?: () => void;
  replyOptionsDisabled?: boolean;
};

export function MessageList({
  items,
  streamingItem = null,
  streaming,
  viewMode,
  channels,
  confirmingActionIds,
  taskRunsById,
  onConfirmAction,
  onDismissAction,
  onSelectReplyOption,
  onConfirmCostPreview,
  replyOptionsDisabled = false,
}: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const { isPinned, scrollToBottom } = useStickyScroll(ref);
  const [newCount, setNewCount] = useState(0);
  const lastSeenIdRef = useRef<number | null>(null);

  useLayoutEffect(() => {
    if (isPinned) scrollToBottom();
  }, [items, streamingItem?.content, isPinned, scrollToBottom]);

  useEffect(() => {
    if (items.length === 0) {
      lastSeenIdRef.current = null;
      setNewCount(0);
      return;
    }
    const latestId = items[items.length - 1].id;
    if (isPinned) {
      lastSeenIdRef.current = latestId;
      setNewCount(0);
    } else if (lastSeenIdRef.current !== latestId) {
      const lastSeen = lastSeenIdRef.current;
      const lastSeenIndex = lastSeen == null ? -1 : items.findIndex((m) => m.id === lastSeen);
      const newer = lastSeenIndex === -1 ? items.length : items.length - lastSeenIndex - 1;
      setNewCount(newer);
    }
  }, [items, isPinned]);

  const handlePillClick = () => {
    scrollToBottom();
    if (items.length > 0) lastSeenIdRef.current = items[items.length - 1].id;
    setNewCount(0);
  };

  const latestCompletedAssistantId = !streaming && !replyOptionsDisabled
    ? [...items].reverse().find((msg) => msg.role === 'assistant')?.id ?? null
    : null;
  // Only show the cost-preview confirm button on the latest assistant
  // message — earlier ones are stale, and stale-button clicks would
  // re-send a wrong user prompt.
  const latestAssistantId = [...items].reverse().find((m) => m.role === 'assistant')?.id ?? null;

  return (
    <div className="wl-message-list">
      <div ref={ref} className="wl-message-list__scroll">
        {items.map((msg) => (
          <ChatBubble
            key={msg.id}
            message={msg}
            viewMode={viewMode}
            channels={channels}
            confirmingActionIds={confirmingActionIds}
            taskRunsById={taskRunsById}
            onConfirmAction={onConfirmAction}
            onDismissAction={onDismissAction}
            onSelectReplyOption={onSelectReplyOption}
            onConfirmCostPreview={
              msg.id === latestAssistantId ? onConfirmCostPreview : undefined
            }
            replyOptionsEnabled={msg.id === latestCompletedAssistantId}
          />
        ))}
        {streamingItem && (
          <ChatBubble
            key="streaming"
            message={streamingItem}
            viewMode={viewMode}
            channels={channels}
            onConfirmAction={() => {}}
            onDismissAction={() => {}}
            isStreaming
          />
        )}
      </div>
      <NewMessagesPill
        streaming={streaming && !isPinned}
        count={isPinned ? 0 : newCount}
        onClick={handlePillClick}
      />
    </div>
  );
}

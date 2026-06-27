import { Plus } from 'lucide-react';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import { MessageList } from '../components/MessageList';
import { ChatComposer } from '../components/ChatComposer';
import type { AgentChatController } from '../hooks/useAgentChatController';
import './WorkflowBuilderChat.css';

type Props = {
  controller: AgentChatController;
  onNewBuild: () => void;
};

/**
 * A focused, widget-sized chat for building workflows — transcript + composer
 * only. Deliberately omits the Agent Desk thread rail, assets pane, persona
 * chips, and view-mode toggle so it fits a narrow column. The composer drops
 * the slash launcher (no `workflows`) since this surface builds workflows
 * rather than launching them.
 */
export function WorkflowBuilderChat({ controller, onNewBuild }: Props) {
  const activeThread = controller.activeThread;
  const hasMessages = !!activeThread && activeThread.messages.length > 0;

  const onSend = (message: string) =>
    controller.sendMessage(message, null, undefined, undefined, 'desk_workflow');

  return (
    <section className="wl-wf-chat" aria-label="Build a workflow">
      <header className="wl-wf-chat__head">
        <h3 className="wl-wf-chat__title">Build a workflow</h3>
        <Button
          variant="ghost"
          onClick={onNewBuild}
          disabled={controller.sending || controller.streaming}
        >
          <Plus size={16} aria-hidden="true" />
          New build
        </Button>
      </header>

      <div className="wl-wf-chat__messages">
        {!hasMessages && !controller.streamingItem ? (
          <Empty
            message="Describe the workflow you want to build — the assistant will draft a script you can save."
            symbol="o"
          />
        ) : (
          <MessageList
            items={activeThread ? activeThread.messages : []}
            streamingItem={controller.streamingItem}
            streaming={controller.streaming}
            viewMode={controller.viewMode}
            channels={controller.channels}
            confirmingActionIds={controller.confirmingActionIds}
            taskRunsById={controller.taskRunsById}
            onConfirmAction={controller.confirmAction}
            onDismissAction={controller.dismissAction}
            onSelectReplyOption={(_, option) => onSend(option)}
            onConfirmCostPreview={() =>
              controller.confirmCostPreview(null, undefined, undefined, 'desk_workflow')
            }
            replyOptionsDisabled={controller.sending || controller.streaming}
          />
        )}
      </div>

      <div className="wl-wf-chat__composer">
        <ChatComposer
          onSend={onSend}
          sending={controller.sending}
          streaming={controller.streaming}
          channels={controller.channels}
          selectedModel={controller.selectedModel}
          executionMode={controller.executionMode}
          onChangeModel={controller.setSelectedModel}
          onChangeMode={controller.setExecutionMode}
          onStopStreaming={controller.stopStreaming}
          onRefreshModels={controller.refreshModels}
          compactModelPicker
        />
      </div>
    </section>
  );
}

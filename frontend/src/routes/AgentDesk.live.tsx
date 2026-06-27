import { useEffect, useState } from 'react';
import type { DeskWorkflowSummary, PageContext } from '../types';
import { AgentDesk } from './AgentDesk';
import { Skeleton } from '../components/Skeleton';
import { Empty } from '../components/Empty';
import { listWorkflows } from '../api/client';
import {
  useAgentChatController,
  type AgentChatController,
} from '../hooks/useAgentChatController';

type Props = {
  controller?: AgentChatController;
  pageContext?: PageContext | null;
  accountingDate?: string;
  onOpenTrace?: (threadId: number) => void;
};

export function AgentDeskLive(props: Props = {}) {
  if (props.controller) {
    return (
      <AgentDeskLiveView
        controller={props.controller}
        pageContext={props.pageContext ?? null}
        accountingDate={props.accountingDate}
        onOpenTrace={props.onOpenTrace}
      />
    );
  }
  return (
    <AgentDeskLiveStandalone
      pageContext={props.pageContext ?? null}
      accountingDate={props.accountingDate}
      onOpenTrace={props.onOpenTrace}
    />
  );
}

function AgentDeskLiveStandalone({
  pageContext,
  accountingDate,
  onOpenTrace,
}: {
  pageContext: PageContext | null;
  accountingDate?: string;
  onOpenTrace?: (threadId: number) => void;
}) {
  const controller = useAgentChatController();
  return (
    <AgentDeskLiveView
      controller={controller}
      pageContext={pageContext}
      accountingDate={accountingDate}
      onOpenTrace={onOpenTrace}
    />
  );
}

function AgentDeskLiveView({
  controller,
  pageContext,
  accountingDate,
  onOpenTrace,
}: {
  controller: AgentChatController;
  pageContext: PageContext | null;
  accountingDate?: string;
  onOpenTrace?: (threadId: number) => void;
}) {
  const [workflows, setWorkflows] = useState<DeskWorkflowSummary[]>([]);
  useEffect(() => {
    void listWorkflows().then(setWorkflows).catch(() => setWorkflows([]));
  }, []);

  if (controller.loading) {
    return (
      <div>
        <Skeleton height={32} width="40%" />
        <div style={{ height: 12 }} />
        <Skeleton height={400} />
      </div>
    );
  }

  if (controller.error) {
    return <Empty message={`Could not load Agent Desk: ${controller.error}`} />;
  }

  return (
    <AgentDesk
      threads={controller.threads}
      activeThreadId={controller.activeThreadId}
      sending={controller.sending}
      streaming={controller.streaming}
      streamingItem={controller.streamingItem}
      viewMode={controller.viewMode}
      channels={controller.channels}
      selectedModel={controller.selectedModel}
      executionMode={controller.executionMode}
      confirmingActionIds={controller.confirmingActionIds}
      taskRunsById={controller.taskRunsById}
      onChangeModel={controller.setSelectedModel}
      onChangeMode={controller.setExecutionMode}
      onStopStreaming={controller.stopStreaming}
      onRefreshModels={controller.refreshModels}
      onChangeViewMode={controller.setViewMode}
      onSelectThread={controller.selectThread}
      onNewThread={controller.createThread}
      onRenameThread={controller.renameThread}
      onExportThread={controller.exportThread}
      onDeleteThread={controller.deleteThread}
      onForkThread={controller.forkThread}
      onOpenTrace={onOpenTrace}
      onSend={(message) => controller.sendMessage(
        message,
        pageContext,
        accountingDate,
        undefined,
        'desk_workflow',
      )}
      onConfirmAction={controller.confirmAction}
      onDismissAction={controller.dismissAction}
      onConfirmCostPreview={() => controller.confirmCostPreview(
        pageContext,
        accountingDate,
        undefined,
        'desk_workflow',
      )}
      workflows={workflows}
      onLaunchWorkflow={controller.launchWorkflow}
      goalContract={controller.goalContract}
      goalState={controller.goalState}
      goalClarification={controller.goalClarification}
      goalBusy={controller.goalBusy}
      onRatifyGoal={controller.ratifyActiveGoal}
      onCancelGoal={controller.cancelActiveGoal}
    />
  );
}

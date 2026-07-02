import { useCallback, useEffect, useState } from 'react';
import { Workflows } from './Workflows';
import { WorkflowBuilder } from './WorkflowBuilder';
import { WorkflowBuilderChat } from './WorkflowBuilderChat';
import { Button } from '../components/Button';
import { PageScaffold } from '../components/templates/PageScaffold';
import { useAgentChatController } from '../hooks/useAgentChatController';
import {
  createWorkflow,
  deleteWorkflow,
  getWorkflow,
  listWorkflows,
  updateWorkflow,
  validateWorkflow,
} from '../api/client';
import type { DeskWorkflow, DeskWorkflowSummary } from '../types';
import './Workflows.css';

const TEMPLATE = `meta = {
    "name": "new-workflow",
    "title": "New Workflow",
    "persona": "risk_manager",
    "mode": "auto",
    "scope": "local",
    "description": "",
}

await step("First step prompt")
`;

/**
 * Pull the workflow slug out of a script's `meta` literal. The backend derives
 * the slug from `meta["name"]` and rejects a PUT whose path slug disagrees, so
 * the explicit Save must target exactly that name. Scoped to the meta block so
 * a `"name":` inside a step prompt can't shadow it.
 */
export function parseWorkflowSlug(script: string): string {
  const match = script.match(/meta\s*=\s*\{[\s\S]*?"name"\s*:\s*"([^"]+)"/);
  return match ? match[1] : '';
}

export function WorkflowsLive() {
  const [mode, setMode] = useState<'manage' | 'create'>('manage');
  const [items, setItems] = useState<DeskWorkflowSummary[]>([]);
  const [selected, setSelected] = useState<DeskWorkflow | null>(null);
  const [draft, setDraft] = useState('');
  const [validation, setValidation] = useState<{ ok: boolean; error: string | null } | null>(null);

  const refresh = useCallback(async () => {
    const rows = await listWorkflows();
    setItems(rows);
    return rows;
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!draft) {
      setValidation(null);
      return;
    }
    const handle = setTimeout(() => {
      void validateWorkflow(draft).then(setValidation);
    }, 300);
    return () => clearTimeout(handle);
  }, [draft]);

  const onSelect = async (slug: string) => {
    const wf = await getWorkflow(slug);
    setSelected(wf);
    setDraft(wf.script);
  };
  const onNew = () => {
    setSelected(null);
    setDraft(TEMPLATE);
  };
  const onSave = async () => {
    if (selected) await updateWorkflow(selected.slug, draft);
    else await createWorkflow(draft);
    await refresh();
  };
  const onDelete = async (slug: string) => {
    await deleteWorkflow(slug);
    setSelected(null);
    setDraft('');
    await refresh();
  };

  const tabs = (
    <div className="wl-workflows-page__tabs" role="tablist">
      <Button
        variant={mode === 'manage' ? 'primary' : 'ghost'}
        onClick={() => setMode('manage')}
      >
        Manage
      </Button>
      <Button
        variant={mode === 'create' ? 'primary' : 'ghost'}
        onClick={() => setMode('create')}
      >
        Create with AI
      </Button>
    </div>
  );

  return (
    <PageScaffold
      className="wl-workflows-page"
      title="WORKFLOWS"
      chips={[`${items.length} workflow${items.length === 1 ? '' : 's'}`]}
      actions={tabs}
    >
      {mode === 'manage' ? (
        <Workflows
          items={items}
          selected={selected}
          draftScript={draft}
          validation={validation}
          onSelect={onSelect}
          onChangeScript={setDraft}
          onSave={onSave}
          onDelete={onDelete}
          onNew={onNew}
        />
      ) : (
        <WorkflowBuilderLive
          onDone={async () => {
            await refresh();
            setMode('manage');
          }}
        />
      )}
    </PageScaffold>
  );
}

/**
 * The "Create with AI" surface. Embeds a normal agent thread whose
 * build-workflow skill drafts a script and persists it via the
 * `save_desk_workflow` tool. We capture that script straight off the tool-call
 * stream (no DB polling) so the preview is instant and reflects edits/re-drafts,
 * then the explicit Save guarantees persistence even if the tool body was denied
 * by the capability gate or errored.
 */
function WorkflowBuilderLive({ onDone }: { onDone: () => Promise<void> }) {
  const [builderDraft, setBuilderDraft] = useState('');
  const [saving, setSaving] = useState(false);

  const onToolStart = useCallback((name: string, args: unknown) => {
    if (name !== 'save_desk_workflow') return;
    if (!args || typeof args !== 'object') return;
    const script = (args as Record<string, unknown>).script;
    if (typeof script === 'string') setBuilderDraft(script);
  }, []);

  // Builder conversations are tagged 'workflow_builder' so they stay out of the
  // Agent Desk thread list and the controller resumes only its own threads.
  const controller = useAgentChatController({ onToolStart, threadSource: 'workflow_builder' });

  const onNewBuild = () => {
    setBuilderDraft('');
    void controller.createThread();
  };

  const finishBuilding = async () => {
    if (!builderDraft) return;
    const slug = parseWorkflowSlug(builderDraft);
    if (!slug) return;
    setSaving(true);
    try {
      // PUT upserts on the meta-derived slug, so this persists the drafted
      // script whether or not the tool already wrote it.
      await updateWorkflow(slug, builderDraft);
      setBuilderDraft('');
      await onDone();
    } finally {
      setSaving(false);
    }
  };

  return (
    <WorkflowBuilder
      chat={<WorkflowBuilderChat controller={controller} onNewBuild={onNewBuild} />}
      draftScript={builderDraft}
      onSave={finishBuilding}
      saving={saving}
    />
  );
}

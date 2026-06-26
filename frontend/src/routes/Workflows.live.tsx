import { useCallback, useEffect, useRef, useState } from 'react';
import { Workflows } from './Workflows';
import { WorkflowBuilder } from './WorkflowBuilder';
import { AgentDeskLive } from './AgentDesk.live';
import { Button } from '../components/Button';
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

export function WorkflowsLive() {
  const [mode, setMode] = useState<'manage' | 'create'>('manage');
  const [items, setItems] = useState<DeskWorkflowSummary[]>([]);
  const [selected, setSelected] = useState<DeskWorkflow | null>(null);
  const [draft, setDraft] = useState('');
  const [validation, setValidation] = useState<{ ok: boolean; error: string | null } | null>(null);

  // Builder state: a live preview of the script the assistant just saved.
  const [builderDraft, setBuilderDraft] = useState('');
  const baselineSlugs = useRef<Set<string>>(new Set());

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

  // While building with the assistant, poll for a newly-saved workflow and pull
  // its script into the preview pane.
  useEffect(() => {
    if (mode !== 'create') return;
    let cancelled = false;
    void listWorkflows().then((rows) => {
      baselineSlugs.current = new Set(rows.map((r) => r.slug));
    });
    const handle = setInterval(() => {
      void listWorkflows().then(async (rows) => {
        if (cancelled) return;
        const fresh = rows.find((r) => !baselineSlugs.current.has(r.slug));
        if (fresh) {
          const full = await getWorkflow(fresh.slug);
          if (!cancelled) setBuilderDraft(full.script);
        }
      });
    }, 4000);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, [mode]);

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

  const finishBuilding = async () => {
    await refresh();
    setBuilderDraft('');
    setMode('manage');
  };

  return (
    <div className="wl-workflows-page">
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
        <WorkflowBuilder
          chat={<AgentDeskLive />}
          draftScript={builderDraft}
          onSave={finishBuilding}
          saving={false}
        />
      )}
    </div>
  );
}

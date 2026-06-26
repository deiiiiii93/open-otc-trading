import { useCallback, useEffect, useState } from 'react';
import { Workflows } from './Workflows';
import {
  createWorkflow,
  deleteWorkflow,
  getWorkflow,
  listWorkflows,
  updateWorkflow,
  validateWorkflow,
} from '../api/client';
import type { DeskWorkflow, DeskWorkflowSummary } from '../types';

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
  const [items, setItems] = useState<DeskWorkflowSummary[]>([]);
  const [selected, setSelected] = useState<DeskWorkflow | null>(null);
  const [draft, setDraft] = useState('');
  const [validation, setValidation] = useState<{ ok: boolean; error: string | null } | null>(null);

  const refresh = useCallback(async () => setItems(await listWorkflows()), []);
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

  return (
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
  );
}

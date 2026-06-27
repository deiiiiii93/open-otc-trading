import { useEffect, useState } from 'react';
import { Modal } from './Modal';
import { Button } from './Button';
import type { DeskWorkflowSummary } from '../types';
import './WorkflowParamsDialog.css';

type Props = {
  open: boolean;
  workflow: DeskWorkflowSummary;
  portfolios: string[];
  onCancel: () => void;
  onRun: (args: Record<string, string>) => void;
};

export function WorkflowParamsDialog({ open, workflow, portfolios, onCancel, onRun }: Props) {
  const params = workflow.params ?? [];
  const [values, setValues] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!open) setValues({});
  }, [open]);

  const set = (name: string, value: string) =>
    setValues((prev) => ({ ...prev, [name]: value }));

  const allFilled = params.every((p) => (values[p.name] ?? '').trim().length > 0);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!allFilled) return;
    const clean: Record<string, string> = {};
    for (const p of params) clean[p.name] = values[p.name].trim();
    onRun(clean);
  };

  return (
    <Modal
      open={open}
      onOpenChange={(o) => { if (!o) onCancel(); }}
      title={`Run · ${workflow.title}`}
      layoutKey="workflow-params"
      defaultHeight={360}
    >
      <form className="wl-wf-params" onSubmit={submit}>
        {params.map((p) => (
          <label key={p.name} className="wl-wf-params__field">
            <span>{p.label}</span>
            {p.type === 'portfolio' ? (
              <select
                className="wl-input"
                aria-label={p.label}
                value={values[p.name] ?? ''}
                onChange={(e) => set(p.name, e.target.value)}
              >
                <option value="" disabled>Select a portfolio…</option>
                {portfolios.map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            ) : (
              <input
                className="wl-input"
                type={p.type === 'date' ? 'date' : 'text'}
                aria-label={p.label}
                value={values[p.name] ?? ''}
                onChange={(e) => set(p.name, e.target.value)}
              />
            )}
          </label>
        ))}
        <div className="wl-wf-params__actions">
          <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={!allFilled}>Run</Button>
        </div>
      </form>
    </Modal>
  );
}

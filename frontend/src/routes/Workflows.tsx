import type { DeskWorkflow, DeskWorkflowSummary } from '../types';
import { Button } from '../components/Button';
import './Workflows.css';

type Props = {
  items: DeskWorkflowSummary[];
  selected: DeskWorkflow | null;
  draftScript: string;
  validation: { ok: boolean; error: string | null } | null;
  onSelect: (slug: string) => void;
  onChangeScript: (script: string) => void;
  onSave: () => void;
  onDelete: (slug: string) => void;
  onNew: () => void;
};

export function Workflows({
  items,
  selected,
  draftScript,
  validation,
  onSelect,
  onChangeScript,
  onSave,
  onDelete,
  onNew,
}: Props) {
  return (
    <div className="wl-workflows">
      <aside className="wl-workflows__list">
        <Button onClick={onNew}>New workflow</Button>
        <ul>
          {items.map((w) => (
            <li key={w.slug}>
              <button
                type="button"
                className="wl-workflows__item"
                aria-pressed={selected?.slug === w.slug}
                onClick={() => onSelect(w.slug)}
              >
                <span className="wl-workflows__item-title">{w.title}</span>
                <small className="wl-workflows__item-meta">
                  {w.persona} · {w.scope} · {w.default_mode}
                </small>
                {w.source === 'seed' && <em className="wl-workflows__badge">seed</em>}
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <section className="wl-workflows__editor">
        <textarea
          className="wl-workflows__script wl-input"
          aria-label="workflow script"
          value={draftScript}
          onChange={(e) => onChangeScript(e.target.value)}
          spellCheck={false}
          placeholder="meta = { ... }&#10;await step(&quot;...&quot;)"
        />
        {validation && !validation.ok && (
          <p className="wl-workflows__error">{validation.error}</p>
        )}
        <div className="wl-workflows__actions">
          <Button
            variant="primary"
            onClick={onSave}
            disabled={validation ? !validation.ok : false}
          >
            Save
          </Button>
          {selected && selected.source !== 'seed' && (
            <Button variant="danger" onClick={() => onDelete(selected.slug)}>
              Delete
            </Button>
          )}
        </div>
      </section>
    </div>
  );
}

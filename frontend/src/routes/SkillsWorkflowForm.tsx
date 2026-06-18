import { useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { Button } from '../components/Button';
import { Select } from '../components/Select';
import type {
  SkillFrontmatter,
  SkillLintIssue,
  SkillPersona,
  SkillRoutingEntry,
} from '../types';

export type WorkflowDraft = { frontmatter: SkillFrontmatter; body: string };

const ENVELOPES = ['pet_page', 'pet_diagnostic', 'desk_workflow', 'desk_async'] as const;
const WORKFLOW_TYPES = ['diagnostic', 'action', 'read', 'compound'] as const;
const PERSONAS: SkillPersona[] = ['trader', 'risk_manager', 'high_board'];

type Props = {
  draft: WorkflowDraft;
  domains: string[];
  mode: 'edit' | 'create';
  issues: SkillLintIssue[];
  bodyTokens: number | null;
  saving: boolean;
  onChange: (draft: WorkflowDraft) => void;
  onSave: () => void;
  onDelete?: () => void;
};

export function SkillsWorkflowForm({
  draft, domains, mode, issues, bodyTokens, saving, onChange, onSave, onDelete,
}: Props) {
  const fm = draft.frontmatter;
  const blocking = issues.some((issue) => issue.severity === 'error');
  const set = (patch: Partial<SkillFrontmatter>) =>
    onChange({ ...draft, frontmatter: { ...fm, ...patch } });
  const routing = fm.routing ?? [];

  const toggle = (key: 'allowed_envelopes' | 'may_escalate_to', value: string) => {
    const current = fm[key];
    set({
      [key]: current.includes(value)
        ? current.filter((v) => v !== value)
        : [...current, value],
    } as Partial<SkillFrontmatter>);
  };

  const setRouting = (next: SkillRoutingEntry[]) => set({ routing: next });

  return (
    <form
      className="wl-skills__form"
      onSubmit={(event) => { event.preventDefault(); onSave(); }}
    >
      <section className="wl-skills__section">
        <div className="wl-skills__section-head">
          <span>Identity</span>
          <small>Name, domain, workflow type, and purpose</small>
        </div>
        <div className="wl-skills__grid">
          <label className="wl-skills__field">
            <span className="wl-skills__label">name</span>
            <input
              aria-label="name"
              value={fm.name}
              disabled={mode === 'edit'}
              onChange={(e) => set({ name: e.currentTarget.value })}
            />
          </label>
          <div className="wl-skills__field">
            <Select
              label="domain"
              value={fm.domain}
              disabled={mode === 'edit'}
              onChange={(v) => set({ domain: v })}
              options={domains.map((d) => ({ value: d, label: d }))}
            />
          </div>
          <div className="wl-skills__field wl-skills__field--wide">
            <Select
              label="workflow_type"
              value={fm.workflow_type}
              onChange={(v) => set({ workflow_type: v as SkillFrontmatter['workflow_type'] })}
              options={WORKFLOW_TYPES.map((t) => ({ value: t, label: t }))}
            />
          </div>
          <label className="wl-skills__field wl-skills__field--wide">
            <span className="wl-skills__label">description</span>
            <textarea
              aria-label="description"
              rows={3}
              className="wl-skills__body"
              value={fm.description}
              onChange={(e) => set({ description: e.currentTarget.value })}
            />
          </label>
        </div>
      </section>

      <section className="wl-skills__section">
        <div className="wl-skills__section-head">
          <span>Runtime Behavior</span>
          <small>Where this skill can run and when it needs operator control</small>
        </div>
        <div className="wl-skills__grid">
          <div className="wl-skills__field">
            <span className="wl-skills__label">allowed_envelopes</span>
            <div className="wl-skills__checks">
              {ENVELOPES.map((env) => (
                <label key={env}>
                  <input
                    type="checkbox"
                    checked={fm.allowed_envelopes.includes(env)}
                    onChange={() => toggle('allowed_envelopes', env)}
                  />
                  {env}
                </label>
              ))}
            </div>
          </div>
          <div className="wl-skills__field">
            <span className="wl-skills__label">may_escalate_to</span>
            <div className="wl-skills__checks">
              {ENVELOPES.map((env) => (
                <label key={env}>
                  <input
                    type="checkbox"
                    checked={fm.may_escalate_to.includes(env)}
                    onChange={() => toggle('may_escalate_to', env)}
                  />
                  {env}
                </label>
              ))}
            </div>
          </div>
          <div className="wl-skills__check-field">
            <input
              type="checkbox"
              checked={fm.write_actions}
              onChange={(e) => set({ write_actions: e.currentTarget.checked })}
            />
            <span>requires writes</span>
          </div>
          <div className="wl-skills__check-field">
            <input
              type="checkbox"
              checked={fm.confirmation_required}
              onChange={(e) => set({ confirmation_required: e.currentTarget.checked })}
            />
            <span>HITL confirm</span>
          </div>
        </div>
      </section>

      <section className="wl-skills__section">
        <div className="wl-skills__section-head">
          <span>Context & Outcomes</span>
          <small>Inputs the agent expects and conditions for a successful run</small>
        </div>
        <div className="wl-skills__grid">
          <TagListInput
            label="required_context"
            values={fm.required_context}
            onChange={(values) => set({ required_context: values })}
          />
          <TagListInput
            label="optional_context"
            values={fm.optional_context}
            onChange={(values) => set({ optional_context: values })}
          />
          <TagListInput
            label="success_criteria"
            values={fm.success_criteria}
            onChange={(values) => set({ success_criteria: values })}
            wide
          />
        </div>
      </section>

      <section className="wl-skills__section wl-skills__routing">
        <div className="wl-skills__section-head">
          <span>Orchestrator Routing</span>
          <small>Optional request patterns and the persona that should handle them</small>
        </div>
        {routing.map((entry, index) => (
          <div key={index} className="wl-skills__routing-row">
            <input
              aria-label={`Route request ${index + 1}`}
              value={entry.request}
              placeholder="request shape"
              onChange={(e) => {
                const next = [...routing];
                next[index] = { ...entry, request: e.currentTarget.value };
                setRouting(next);
              }}
            />
            <Select
              label={`Route persona ${index + 1}`}
              value={entry.persona}
              onChange={(v) => {
                const next = [...routing];
                next[index] = { ...entry, persona: v as SkillPersona };
                setRouting(next);
              }}
              options={PERSONAS.map((p) => ({ value: p, label: p }))}
            />
            <Button
              type="button"
              variant="ghost"
              iconOnly
              aria-label={`Remove route ${index + 1}`}
              onClick={() => setRouting(routing.filter((_, i) => i !== index))}
            >
              <Trash2 size={14} aria-hidden="true" />
            </Button>
          </div>
        ))}
        <Button
          type="button"
          className="wl-skills__add-button"
          onClick={() => setRouting([...routing, { request: '', persona: 'trader' }])}
        >
          <Plus size={14} aria-hidden="true" />
          <span>Add route</span>
        </Button>
      </section>

      <section className="wl-skills__section">
        <div className="wl-skills__section-head">
          <span>Markdown Body</span>
          <small>{bodyTokens != null ? `${bodyTokens} / 500 tokens` : '… / 500 tokens'}</small>
        </div>
        <label className="wl-skills__field wl-skills__field--wide">
          <span className="wl-skills__label">body</span>
          <textarea
            aria-label="body"
            rows={14}
            className="wl-skills__body"
            value={draft.body}
            onChange={(e) => onChange({ ...draft, body: e.currentTarget.value })}
          />
        </label>
      </section>

      {issues.length > 0 && (
        <ul className="wl-skills__issues">
          {issues.map((issue, index) => (
            <li
              key={`${issue.code}-${index}`}
              className={`wl-skills__issue wl-skills__issue--${issue.severity}`}
            >
              {issue.severity === 'error' ? '✕' : '⚠'} {issue.code}: {issue.message}
              {issue.detail ? ` (${issue.detail})` : ''}
            </li>
          ))}
        </ul>
      )}

      <div className="wl-skills__actions">
        <Button type="submit" variant="primary" disabled={saving || blocking}>
          {mode === 'create' ? 'Create & reload agent' : 'Save & reload agent'}
        </Button>
        {mode === 'edit' && onDelete && (
          <Button type="button" variant="danger" onClick={onDelete}>
            <Trash2 size={16} aria-hidden="true" />
            <span>Delete skill…</span>
          </Button>
        )}
      </div>
    </form>
  );
}

function TagListInput({
  label, values, onChange, wide = false,
}: { label: string; values: string[]; onChange: (values: string[]) => void; wide?: boolean }) {
  const [text, setText] = useState('');
  const add = () => {
    const value = text.trim();
    if (!value || values.includes(value)) return;
    onChange([...values, value]);
    setText('');
  };
  return (
    <div className={`wl-skills__field${wide ? ' wl-skills__field--wide' : ''}`}>
      <span className="wl-skills__label">{label}</span>
      <div className="wl-skills__tags">
        {values.map((value) => (
          <span key={value} className="wl-skills__tag">
            {value}
            <button
              type="button"
              aria-label={`Remove ${value}`}
              onClick={() => onChange(values.filter((v) => v !== value))}
            >
              ×
            </button>
          </span>
        ))}
        <input
          aria-label={`Add ${label}`}
          value={text}
          placeholder="add…"
          onChange={(e) => setText(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); add(); }
          }}
        />
        <button className="wl-skills__tag-add" type="button" onClick={add}>
          <Plus size={12} aria-hidden="true" />
          <span>Add</span>
        </button>
      </div>
    </div>
  );
}

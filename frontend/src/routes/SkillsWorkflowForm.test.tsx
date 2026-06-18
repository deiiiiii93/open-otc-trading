import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SkillsWorkflowForm, type WorkflowDraft } from './SkillsWorkflowForm';

const draft: WorkflowDraft = {
  frontmatter: {
    name: 'fetch-market-data',
    description: 'Fetch market snapshots.',
    domain: 'market-data',
    workflow_type: 'read',
    allowed_envelopes: ['desk_workflow'],
    may_escalate_to: [],
    required_context: ['underlyings'],
    optional_context: [],
    write_actions: false,
    confirmation_required: false,
    success_criteria: ['snapshots returned'],
    routing: [{ request: 'Fetch current market data', persona: 'trader' }],
  },
  body: '## When to use\n\n- Always.',
};

const baseProps = {
  draft,
  domains: ['market-data', 'risk'],
  mode: 'edit' as const,
  issues: [],
  bodyTokens: 42,
  saving: false,
  onChange: vi.fn(),
  onSave: vi.fn(),
};

describe('SkillsWorkflowForm', () => {
  it('renders frontmatter fields and token counter', () => {
    render(<SkillsWorkflowForm {...baseProps} />);
    expect(screen.getByDisplayValue('fetch-market-data')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Fetch market snapshots.')).toBeInTheDocument();
    expect(screen.getByText(/42 \/ 500 tokens/)).toBeInTheDocument();
    expect(screen.getByText('underlyings')).toBeInTheDocument();
  });

  it('name is read-only in edit mode, editable in create mode', () => {
    const { rerender } = render(<SkillsWorkflowForm {...baseProps} />);
    expect(screen.getByLabelText('name')).toBeDisabled();
    rerender(<SkillsWorkflowForm {...baseProps} mode="create" />);
    expect(screen.getByLabelText('name')).toBeEnabled();
  });

  it('emits onChange when a routing row is added', async () => {
    const onChange = vi.fn();
    render(<SkillsWorkflowForm {...baseProps} onChange={onChange} />);
    await userEvent.click(screen.getByRole('button', { name: 'Add route' }));
    const next = onChange.mock.calls.at(-1)![0];
    expect(next.frontmatter.routing).toHaveLength(2);
  });

  it('emits onChange when a routing row is removed', async () => {
    const onChange = vi.fn();
    render(<SkillsWorkflowForm {...baseProps} onChange={onChange} />);
    await userEvent.click(screen.getByRole('button', { name: 'Remove route 1' }));
    const next = onChange.mock.calls.at(-1)![0];
    expect(next.frontmatter.routing).toHaveLength(0);
  });

  it('disables save while blocking lint errors exist', () => {
    render(
      <SkillsWorkflowForm
        {...baseProps}
        issues={[{ code: 'body_length', message: 'too long', detail: '600', severity: 'error' }]}
      />,
    );
    expect(screen.getByRole('button', { name: /Save/ })).toBeDisabled();
    expect(screen.getByText(/too long/)).toBeInTheDocument();
  });

  it('shows warnings without disabling save', () => {
    render(
      <SkillsWorkflowForm
        {...baseProps}
        issues={[{ code: 'missing_example', message: 'no example', detail: '', severity: 'warning' }]}
      />,
    );
    expect(screen.getByRole('button', { name: /Save/ })).toBeEnabled();
    expect(screen.getByText(/no example/)).toBeInTheDocument();
  });
});

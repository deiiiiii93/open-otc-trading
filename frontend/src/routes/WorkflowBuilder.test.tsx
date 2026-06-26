import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { WorkflowBuilder } from './WorkflowBuilder';

describe('WorkflowBuilder', () => {
  it('renders the drafted script preview and a Save button', () => {
    render(
      <WorkflowBuilder
        chat={<div>chat</div>}
        draftScript={'meta = {"name":"x"}'}
        onSave={() => {}}
        saving={false}
      />,
    );
    expect(screen.getByText(/meta = /)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument();
  });

  it('disables Save when there is no draft', () => {
    render(<WorkflowBuilder chat={<div>chat</div>} draftScript="" onSave={() => {}} saving={false} />);
    expect(screen.getByRole('button', { name: /save/i })).toBeDisabled();
  });
});

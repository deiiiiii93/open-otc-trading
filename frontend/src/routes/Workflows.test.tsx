import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { Workflows } from './Workflows';

describe('Workflows page', () => {
  it('lists workflows and marks seed as non-deletable', () => {
    render(
      <Workflows
        items={[
          {
            slug: 'risk-manager-control-day',
            title: 'Risk Manager Control Day',
            persona: 'risk_manager',
            description: 'd',
            scope: 'shared',
            default_mode: 'yolo',
            source: 'seed',
          },
        ]}
        selected={null}
        draftScript=""
        validation={null}
        onSelect={() => {}}
        onChangeScript={() => {}}
        onSave={() => {}}
        onDelete={() => {}}
        onNew={() => {}}
      />,
    );
    expect(screen.getByText('Risk Manager Control Day')).toBeInTheDocument();
    expect(screen.getByText(/seed/i)).toBeInTheDocument();
  });
});

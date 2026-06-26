import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ChatComposer } from './ChatComposer';
import type { DeskWorkflowSummary } from '../types';

const wf: DeskWorkflowSummary[] = [
  {
    slug: 'risk-manager-control-day',
    title: 'Risk Manager Control Day',
    persona: 'risk_manager',
    description: '',
    scope: 'shared',
    default_mode: 'yolo',
    source: 'seed',
  },
];

describe('ChatComposer slash picker', () => {
  it('shows workflows on / and launches on select', () => {
    const onLaunch = vi.fn();
    render(
      <ChatComposer onSend={() => {}} sending={false} workflows={wf} onLaunchWorkflow={onLaunch} />,
    );
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '/risk' } });
    fireEvent.click(screen.getByText('Risk Manager Control Day'));
    expect(onLaunch).toHaveBeenCalledWith('risk-manager-control-day', 'yolo');
  });

  it('does not show picker for reserved /goal', () => {
    render(
      <ChatComposer onSend={() => {}} sending={false} workflows={wf} onLaunchWorkflow={() => {}} />,
    );
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '/goal reduce risk' } });
    expect(screen.queryByText('Risk Manager Control Day')).not.toBeInTheDocument();
  });
});

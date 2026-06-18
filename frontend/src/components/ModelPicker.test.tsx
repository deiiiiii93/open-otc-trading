import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { AgentChannel, AgentModelSelection } from '../types';
import { ModelPicker } from './ModelPicker';

const channels: AgentChannel[] = [
  {
    name: 'zenmux',
    label: 'Zenmux',
    type: 'zenmux',
    healthy: true,
    models: [
      { channel: 'zenmux', provider: 'anthropic', model: 'm1', label: 'Sonnet 4.6', tags: ['tool-use'] },
      { channel: 'zenmux', provider: 'openai', model: 'm2', label: 'GPT-5.4' },
    ],
  },
  {
    name: 'deepseek',
    label: 'DeepSeek',
    type: 'openai_compatible',
    healthy: false,
    models: [
      { channel: 'deepseek', provider: 'deepseek', model: 'd1', label: 'V4 Flash', tags: ['fast'] },
    ],
  },
];

const selected: AgentModelSelection = { channel: 'zenmux', provider: 'anthropic', model: 'm1' };

describe('ModelPicker', () => {
  it('renders the selected model label on the trigger button', () => {
    render(<ModelPicker channels={channels} selected={selected} onChange={() => {}} />);
    expect(screen.getByRole('button', { name: /Sonnet 4.6/i })).toBeInTheDocument();
  });

  it('opens panel and lists channels with their models when clicked', () => {
    render(<ModelPicker channels={channels} selected={selected} onChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Sonnet 4.6/i }));
    expect(screen.getByText(/ZENMUX/)).toBeInTheDocument();
    expect(screen.getByText('GPT-5.4')).toBeInTheDocument();
    expect(screen.getByText(/DEEPSEEK/)).toBeInTheDocument();
  });

  it('uses compact menu sizing when requested', () => {
    const { container } = render(
      <ModelPicker channels={channels} selected={selected} onChange={() => {}} compact />
    );

    fireEvent.click(screen.getByRole('button', { name: /Sonnet 4.6/i }));

    expect(container.querySelector('.wl-model-picker--compact')).toBeInTheDocument();
    expect(screen.getByRole('listbox')).toHaveClass('wl-model-picker__panel');
    expect(screen.getByText('GPT-5.4')).toBeInTheDocument();
  });

  it('calls onChange when a model row is clicked', () => {
    const onChange = vi.fn();
    render(<ModelPicker channels={channels} selected={selected} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: /Sonnet 4.6/i }));
    fireEvent.click(screen.getByRole('option', { name: /GPT-5\.4/i }));
    expect(onChange).toHaveBeenCalledWith({
      channel: 'zenmux', provider: 'openai', model: 'm2',
    });
  });

  it('marks unhealthy channel rows non-interactive', () => {
    const onChange = vi.fn();
    render(<ModelPicker channels={channels} selected={selected} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: /Sonnet 4.6/i }));
    const row = screen.getByRole('option', { name: /V4 Flash/i });
    fireEvent.click(row);
    expect(onChange).not.toHaveBeenCalled();
    expect(row).toHaveAttribute('aria-disabled', 'true');
  });

  it('renders disabled trigger when no channels are healthy', () => {
    const allUnhealthy = channels.map((ch) => ({ ...ch, healthy: false }));
    render(<ModelPicker channels={allUnhealthy} selected={null} onChange={() => {}} />);
    expect(screen.getByRole('button', { name: /Agent disabled/i })).toBeDisabled();
  });

  it('moves refresh into the dropdown and shows loading state', async () => {
    let resolveRefresh: () => void = () => {};
    const onRefresh = vi.fn(() => new Promise<void>((resolve) => {
      resolveRefresh = resolve;
    }));
    render(
      <ModelPicker channels={channels} selected={selected} onChange={() => {}} onRefresh={onRefresh} />
    );

    expect(screen.queryByRole('button', { name: /refresh model catalog/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Sonnet 4.6/i }));
    expect(screen.getByText(/Not refreshed this session/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /refresh/i }));

    expect(onRefresh).toHaveBeenCalled();
    expect(screen.getByText(/Refreshing/i)).toBeInTheDocument();
    resolveRefresh();
    await waitFor(() => expect(screen.getByText(/Refreshed/i)).toBeInTheDocument());
  });

  it('shows refresh errors in the dropdown', async () => {
    const onRefresh = vi.fn().mockRejectedValue(new Error('reload failed'));
    render(
      <ModelPicker channels={channels} selected={selected} onChange={() => {}} onRefresh={onRefresh} />
    );

    fireEvent.click(screen.getByRole('button', { name: /Sonnet 4.6/i }));
    fireEvent.click(screen.getByRole('button', { name: /refresh/i }));

    await waitFor(() => expect(screen.getByText(/reload failed/i)).toBeInTheDocument());
  });

  it('supports arrow-key navigation and Enter selection', () => {
    const onChange = vi.fn();
    render(<ModelPicker channels={channels} selected={selected} onChange={onChange} />);
    const trigger = screen.getByRole('button', { name: /Sonnet 4.6/i });
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    fireEvent.keyDown(trigger, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith({
      channel: 'zenmux', provider: 'openai', model: 'm2',
    });
  });
});

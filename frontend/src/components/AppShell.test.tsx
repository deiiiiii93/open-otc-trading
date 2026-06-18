import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AppShell } from './AppShell';

describe('AppShell', () => {
  const items = [
    { route: 'chat' as const, label: 'Agent Desk' },
    { route: 'positions' as const, label: 'Positions' },
  ];

  it('renders sidebar and main content', () => {
    render(
      <AppShell active="chat" onNavigate={() => {}} items={items}>
        <div>page content</div>
      </AppShell>
    );
    expect(screen.getByText('Agent Desk')).toBeInTheDocument();
    expect(screen.getByText('page content')).toBeInTheDocument();
  });

  it('calls onNavigate on sidebar click', async () => {
    const onNavigate = vi.fn();
    render(
      <AppShell active="chat" onNavigate={onNavigate} items={items}>
        body
      </AppShell>
    );
    await userEvent.click(screen.getByRole('button', { name: 'Positions' }));
    expect(onNavigate).toHaveBeenCalledWith('positions');
  });

  it('collapses and expands the sidebar from the brand control', async () => {
    render(
      <AppShell active="chat" onNavigate={() => {}} items={items}>
        body
      </AppShell>
    );

    await userEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }));
    expect(screen.queryByRole('button', { name: 'Positions' })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }));
    expect(screen.getByRole('button', { name: 'Positions' })).toBeInTheDocument();
  });

  it('has no a11y violations', async () => {
    const { container } = render(
      <AppShell active="chat" onNavigate={() => {}} items={items}>
        <div>page content</div>
      </AppShell>
    );
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
});

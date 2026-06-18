import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Sidebar } from './Sidebar';

describe('Sidebar', () => {
  const items = [
    { route: 'chat' as const, label: 'Agent Desk' },
    { route: 'positions' as const, label: 'Positions' },
  ];

  it('renders nav items and marks active route', () => {
    const { container } = render(<Sidebar items={items} active="chat" onNavigate={() => {}} />);
    const buttons = container.querySelectorAll('.wl-sidebar__nav button');
    expect(buttons.length).toBe(2);
    expect(buttons[0]).toHaveClass('wl-sidebar__nav-item--active');
    expect(buttons[1]).not.toHaveClass('wl-sidebar__nav-item--active');
  });

  it('calls onNavigate when clicked', async () => {
    const onNavigate = vi.fn();
    render(<Sidebar items={items} active="chat" onNavigate={onNavigate} />);
    await userEvent.click(screen.getByRole('button', { name: 'Positions' }));
    expect(onNavigate).toHaveBeenCalledWith('positions');
  });

  it('calls onToggleCollapsed from the brand area', async () => {
    const onToggleCollapsed = vi.fn();
    render(
      <Sidebar
        items={items}
        active="chat"
        onNavigate={() => {}}
        onToggleCollapsed={onToggleCollapsed}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }));
    expect(onToggleCollapsed).toHaveBeenCalledOnce();
  });

  it('hides nav items when collapsed and exposes the expand action', () => {
    render(
      <Sidebar
        items={items}
        active="chat"
        onNavigate={() => {}}
        collapsed
        onToggleCollapsed={() => {}}
      />,
    );
    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Agent Desk' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Positions' })).not.toBeInTheDocument();
  });

  it('marks active route with aria-current="page"', () => {
    render(<Sidebar items={items} active="chat" onNavigate={() => {}} />);
    const buttons = screen.getAllByRole('button');
    const active = buttons.find((b) => b.textContent === 'Agent Desk');
    const inactive = buttons.find((b) => b.textContent === 'Positions');
    expect(active).toHaveAttribute('aria-current', 'page');
    expect(inactive).not.toHaveAttribute('aria-current');
  });

  it('has no a11y violations', async () => {
    const { container } = render(<Sidebar items={items} active="chat" onNavigate={() => {}} />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
});

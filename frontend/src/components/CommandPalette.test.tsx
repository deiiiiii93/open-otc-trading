import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CommandPalette } from './CommandPalette';

describe('CommandPalette', () => {
  const items = [
    { id: 'price', group: 'Actions', label: 'Price Portfolio · Desk-Q2', shortcut: '⌘P' },
    { id: 'risk',  group: 'Actions', label: 'Run Risk · Desk-Q2',        shortcut: '⌘R' },
    { id: 'jump-snb', group: 'Jump To', label: 'SNB-CSI500 · Trade Detail', shortcut: '↵' },
  ];

  it('renders all items grouped', () => {
    render(<CommandPalette open onOpenChange={() => {}} items={items} onSelect={() => {}} />);
    expect(screen.getByText('Price Portfolio · Desk-Q2')).toBeInTheDocument();
    expect(screen.getByText('SNB-CSI500 · Trade Detail')).toBeInTheDocument();
    expect(screen.getByText(/^Actions$/i)).toBeInTheDocument();
  });

  it('filters items by query', async () => {
    render(<CommandPalette open onOpenChange={() => {}} items={items} onSelect={() => {}} />);
    const input = screen.getByPlaceholderText(/search/i);
    await userEvent.type(input, 'risk');
    expect(screen.getByText('Run Risk · Desk-Q2')).toBeInTheDocument();
    expect(screen.queryByText('SNB-CSI500 · Trade Detail')).not.toBeInTheDocument();
  });

  it('calls onSelect when item clicked', async () => {
    const onSelect = vi.fn();
    render(<CommandPalette open onOpenChange={() => {}} items={items} onSelect={onSelect} />);
    await userEvent.click(screen.getByText('Run Risk · Desk-Q2'));
    expect(onSelect).toHaveBeenCalledWith(items[1]);
  });

  it('has no a11y violations', async () => {
    render(<CommandPalette open onOpenChange={() => {}} items={items} onSelect={() => {}} />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(document.body);
  });
});

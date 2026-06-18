import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Badge } from './Badge';
import { Chip } from './Chip';

describe('Badge', () => {
  it('renders text and applies variant class', () => {
    render(<Badge variant="pos">APPROVED</Badge>);
    const badge = screen.getByText('APPROVED');
    expect(badge).toHaveClass('wl-badge--pos');
  });

  it('applies solid variant', () => {
    render(<Badge variant="ink" solid>CONFIRMED</Badge>);
    expect(screen.getByText('CONFIRMED')).toHaveClass('wl-badge--solid');
  });
});

describe('Chip', () => {
  it('renders children', () => {
    render(<Chip>CSI500</Chip>);
    expect(screen.getByText('CSI500')).toBeInTheDocument();
  });

  it('shows close button when onRemove is provided', async () => {
    const onRemove = vi.fn();
    render(<Chip onRemove={onRemove}>CSI500</Chip>);
    await userEvent.click(screen.getByRole('button', { name: /remove/i }));
    expect(onRemove).toHaveBeenCalledOnce();
  });

  it('hides close button when no onRemove', () => {
    render(<Chip>CSI500</Chip>);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});

describe('Badge + Chip a11y', () => {
  it('Badge has no a11y violations', async () => {
    const { container } = render(<Badge>APPROVED</Badge>);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });

  it('Chip has no a11y violations', async () => {
    const { container } = render(<Chip>CSI500</Chip>);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
});

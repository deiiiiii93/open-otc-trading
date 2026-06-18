import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Button } from './Button';

describe('Button', () => {
  it('renders children', () => {
    render(<Button>Submit</Button>);
    expect(screen.getByRole('button', { name: 'Submit' })).toBeInTheDocument();
  });

  it('applies primary variant class', () => {
    render(<Button variant="primary">Go</Button>);
    expect(screen.getByRole('button')).toHaveClass('wl-button--primary');
  });

  it('applies danger variant class', () => {
    render(<Button variant="danger">Reject</Button>);
    expect(screen.getByRole('button')).toHaveClass('wl-button--danger');
  });

  it('applies ghost variant class', () => {
    render(<Button variant="ghost">Skip</Button>);
    expect(screen.getByRole('button')).toHaveClass('wl-button--ghost');
  });

  it('calls onClick when clicked', async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Click</Button>);
    await userEvent.click(screen.getByRole('button'));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('respects disabled prop', () => {
    render(<Button disabled>Off</Button>);
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('has no a11y violations', async () => {
    const { container } = render(<Button>Submit</Button>);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
});

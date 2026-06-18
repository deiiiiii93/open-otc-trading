import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Skeleton } from './Skeleton';
import { Empty } from './Empty';

describe('Skeleton', () => {
  it('renders with given height', () => {
    const { container } = render(<Skeleton height={20} />);
    const el = container.firstChild as HTMLElement;
    expect(el).toHaveClass('wl-skeleton');
    expect(el.style.height).toBe('20px');
  });

  it('accepts width prop', () => {
    const { container } = render(<Skeleton height={12} width="60%" />);
    const el = container.firstChild as HTMLElement;
    expect(el.style.width).toBe('60%');
  });
});

describe('Empty', () => {
  it('renders message', () => {
    render(<Empty message="No risk runs yet." />);
    expect(screen.getByText('No risk runs yet.')).toBeInTheDocument();
  });

  it('renders action when provided', () => {
    render(<Empty message="No data" action={<button>Create first run</button>} />);
    expect(screen.getByRole('button', { name: /create first run/i })).toBeInTheDocument();
  });
});

describe('Skeleton + Empty a11y', () => {
  it('Skeleton has no a11y violations', async () => {
    const { container } = render(<Skeleton height={20} />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });

  it('Empty has no a11y violations', async () => {
    const { container } = render(<Empty message="No risk runs yet." />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
});

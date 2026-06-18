import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Empty } from './Empty';

describe('Empty', () => {
  it('renders message and default symbol (back-compat)', () => {
    const { container } = render(<Empty message="No data" />);
    expect(screen.getByText('No data')).toBeInTheDocument();
    expect(container.querySelector('.wl-empty__symbol')?.textContent).toBe('∅');
  });

  it('renders a hint line when provided', () => {
    const { container } = render(<Empty message="No data" hint="Try another filter" />);
    expect(container.querySelector('.wl-empty__hint')?.textContent).toBe('Try another filter');
  });

  it('omits the symbol and marks the loading variant', () => {
    const { container } = render(<Empty variant="loading" message="Loading…" />);
    expect(container.querySelector('.wl-empty__symbol')).toBeNull();
    expect(container.querySelector('.wl-empty--loading')).not.toBeNull();
  });

  it('marks the error variant', () => {
    const { container } = render(<Empty variant="error" message="Failed to load" />);
    expect(container.querySelector('.wl-empty--error')).not.toBeNull();
  });
});

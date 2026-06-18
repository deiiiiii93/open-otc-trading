import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PageContextChips } from './PageContextChips';

describe('PageContextChips', () => {
  it('renders all chips', () => {
    render(<PageContextChips chips={['val 2026-05-07', '12 trades', 'Run #87']} />);
    expect(screen.getByText('val 2026-05-07')).toBeInTheDocument();
    expect(screen.getByText('12 trades')).toBeInTheDocument();
    expect(screen.getByText('Run #87')).toBeInTheDocument();
  });

  it('renders nothing when empty', () => {
    const { container } = render(<PageContextChips chips={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('has no a11y violations', async () => {
    const { container } = render(<PageContextChips chips={['12 trades', 'Run #87']} />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
});

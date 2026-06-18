import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PageHeader } from './PageHeader';

describe('PageHeader', () => {
  it('renders title', () => {
    render(<PageHeader title="Positions" chips={[]} />);
    expect(screen.getByText('Positions')).toBeInTheDocument();
  });

  it('renders chips', () => {
    render(<PageHeader title="Positions" chips={['12 trades']} />);
    expect(screen.getByText('12 trades')).toBeInTheDocument();
  });

  it('renders action slot', () => {
    render(<PageHeader title="Positions" chips={[]} action={<button>Run</button>} />);
    expect(screen.getByRole('button', { name: 'Run' })).toBeInTheDocument();
  });

  it('has no a11y violations', async () => {
    const { container } = render(<PageHeader title="Positions" chips={['12 trades']} />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
});

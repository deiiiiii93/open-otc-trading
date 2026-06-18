import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Stepper } from './Stepper';

describe('Stepper', () => {
  it('renders each step label with a status class and marks the active step', () => {
    const { container } = render(<Stepper steps={[
      { label: 'Schema', status: 'done' },
      { label: 'Solve', status: 'active' },
      { label: 'Book', status: 'todo' },
    ]} />);
    expect(screen.getByText('Schema')).toBeInTheDocument();
    expect(container.querySelector('.wl-stepper__step--done')).not.toBeNull();
    expect(container.querySelector('.wl-stepper__step--active')).not.toBeNull();
    expect(screen.getByText('Solve').closest('.wl-stepper__step'))
      .toHaveAttribute('aria-current', 'step');
  });
});

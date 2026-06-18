import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Input } from './Input';

describe('Input', () => {
  it('renders with label', () => {
    render(<Input label="Strike" />);
    expect(screen.getByText('Strike')).toBeInTheDocument();
    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });

  it('binds label to input via htmlFor', () => {
    render(<Input label="Underlying" id="u1" />);
    expect(screen.getByLabelText('Underlying')).toHaveAttribute('id', 'u1');
  });

  it('applies error state', () => {
    render(<Input label="Maturity" error="Beyond cap" />);
    expect(screen.getByText('Beyond cap')).toBeInTheDocument();
    expect(screen.getByRole('textbox')).toHaveClass('wl-input--error');
  });

  it('renders hint when present and no error', () => {
    render(<Input label="Strike" hint="Press up/down to step" />);
    expect(screen.getByText('Press up/down to step')).toBeInTheDocument();
  });

  it('passes value through', async () => {
    render(<Input label="X" defaultValue="hello" />);
    const input = screen.getByRole('textbox') as HTMLInputElement;
    expect(input.value).toBe('hello');
    await userEvent.clear(input);
    await userEvent.type(input, 'world');
    expect(input.value).toBe('world');
  });

  it('has no a11y violations', async () => {
    const { container } = render(<Input label="Strike" />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
});

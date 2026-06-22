import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ThousandSeparatorProvider, useThousandSeparator } from './ThousandSeparatorContext';

function ToggleHarness() {
  const { thousandSeparator, toggleThousandSeparator } = useThousandSeparator();
  return (
    <button type="button" onClick={toggleThousandSeparator}>
      {thousandSeparator ? 'on' : 'off'}
    </button>
  );
}

describe('ThousandSeparatorProvider', () => {
  it('reads persisted preference from localStorage', () => {
    localStorage.setItem('otc:thousand-separator', 'off');
    render(<ThousandSeparatorProvider><ToggleHarness /></ThousandSeparatorProvider>);
    expect(screen.getByRole('button')).toHaveTextContent('off');
  });

  it('toggles and persists preference', async () => {
    const user = userEvent.setup();
    render(<ThousandSeparatorProvider><ToggleHarness /></ThousandSeparatorProvider>);
    await user.click(screen.getByRole('button'));
    expect(screen.getByRole('button')).toHaveTextContent('off');
    expect(localStorage.getItem('otc:thousand-separator')).toBe('off');
  });
});

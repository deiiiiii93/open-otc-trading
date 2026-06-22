import React from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { NumberInput, __numberInputTestUtils } from './NumberInput';
import { ThousandSeparatorProvider } from './ThousandSeparatorContext';

describe('NumberInput', () => {
  it('formats supported number text with thousand separators', () => {
    expect(__numberInputTestUtils.formatNumberText('1234567.89')).toBe('1,234,567.89');
    expect(__numberInputTestUtils.formatNumberText('-1234567')).toBe('-1,234,567');
  });

  it('leaves unsupported number text untouched', () => {
    expect(__numberInputTestUtils.formatNumberText('1e6')).toBe('1e6');
    expect(__numberInputTestUtils.formatNumberText('12..3')).toBe('12..3');
  });

  it('renders unformatted when the global switch is off', () => {
    localStorage.setItem('otc:thousand-separator', 'off');
    render(
      <ThousandSeparatorProvider>
        <NumberInput aria-label="Notional" type="number" value="1234567" readOnly />
      </ThousandSeparatorProvider>,
    );
    expect(screen.getByLabelText('Notional')).toHaveValue(1234567);
  });

  it('renders formatted by default', () => {
    render(
      <ThousandSeparatorProvider>
        <NumberInput aria-label="Notional" type="number" value="1234567" readOnly />
      </ThousandSeparatorProvider>,
    );
    expect(screen.getByLabelText('Notional')).toHaveValue('1,234,567');
  });

  it('passes raw values to change handlers while showing formatted text', async () => {
    localStorage.setItem('otc:thousand-separator', 'on');
    const user = userEvent.setup();
    const changes: string[] = [];

    function Harness() {
      const [value, setValue] = React.useState('');
      return (
        <ThousandSeparatorProvider>
          <NumberInput
            aria-label="Notional"
            type="number"
            value={value}
            onChange={(event) => {
              changes.push(event.target.value);
              setValue(event.target.value);
            }}
          />
        </ThousandSeparatorProvider>
      );
    }

    render(<Harness />);
    await user.type(screen.getByLabelText('Notional'), '1234');

    expect(changes).toEqual(['1', '12', '123', '1234']);
    expect(screen.getByLabelText('Notional')).toHaveValue('1,234');
  });
});

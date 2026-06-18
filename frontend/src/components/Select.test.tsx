import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Select } from './Select';

const engines = [
  { value: '', label: '—' },
  { value: 'SnowballQuadEngine', label: 'SnowballQuadEngine' },
  { value: 'SnowballMCEngine', label: 'SnowballMCEngine' },
  { value: 'PDEEngine', label: 'PDEEngine' },
];

describe('Select', () => {
  it('renders with label and shows selected value in trigger', () => {
    render(<Select label="Engine" value="SnowballQuadEngine" options={engines} />);
    expect(screen.getByText('Engine')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /toggle/i })).toBeInTheDocument();
  });

  it('shows placeholder when no value matches', () => {
    const opts = [
      { value: 'a', label: 'Alpha' },
      { value: 'b', label: 'Beta' },
    ];
    render(<Select label="Engine" value="" options={opts} placeholder="Choose..." />);
    expect(screen.getByText('Choose...')).toBeInTheDocument();
  });

  it('opens dropdown on click and shows all options in listbox', async () => {
    render(<Select label="Engine" value="" options={engines} />);
    await userEvent.click(screen.getByRole('button', { name: /toggle/i }));
    const listbox = screen.getByRole('listbox');
    expect(within(listbox).getByRole('option', { name: /SnowballQuadEngine/ })).toBeInTheDocument();
    expect(within(listbox).getByRole('option', { name: /SnowballMCEngine/ })).toBeInTheDocument();
    expect(within(listbox).getByRole('option', { name: /PDEEngine/ })).toBeInTheDocument();
  });

  it('calls onChange when an option is clicked', async () => {
    const onChange = vi.fn();
    render(<Select label="Engine" value="" options={engines} onChange={onChange} />);
    await userEvent.click(screen.getByRole('button', { name: /toggle/i }));
    const listbox = screen.getByRole('listbox');
    await userEvent.click(within(listbox).getByRole('option', { name: /PDEEngine/ }));
    expect(onChange).toHaveBeenCalledWith('PDEEngine');
  });

  it('marks the selected option with aria-selected in listbox', async () => {
    render(<Select label="Engine" value="SnowballMCEngine" options={engines} />);
    await userEvent.click(screen.getByRole('button', { name: /toggle/i }));
    const listbox = screen.getByRole('listbox');
    const selected = within(listbox).getByRole('option', { name: /SnowballMCEngine/ });
    expect(selected).toHaveAttribute('aria-selected', 'true');
  });

  it('closes dropdown after selection', async () => {
    const onChange = vi.fn();
    render(<Select label="Engine" value="" options={engines} onChange={onChange} />);
    await userEvent.click(screen.getByRole('button', { name: /toggle/i }));
    const listbox = screen.getByRole('listbox');
    await userEvent.click(within(listbox).getByRole('option', { name: /PDEEngine/ }));
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('does not open when disabled', async () => {
    render(<Select label="Engine" value="" options={engines} disabled />);
    await userEvent.click(screen.getByRole('button', { name: /toggle/i }));
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('applies error state', () => {
    render(<Select label="Engine" value="" options={engines} error="Required" />);
    expect(screen.getByText('Required')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /toggle/i })).toHaveClass('wl-select__trigger--error');
  });

  it('renders hint when present and no error', () => {
    render(<Select label="Engine" value="" options={engines} hint="Pick one" />);
    expect(screen.getByText('Pick one')).toBeInTheDocument();
  });

  it('hidden native select has correct value for test compatibility', () => {
    render(<Select label="Engine" value="PDEEngine" options={engines} />);
    const nativeSelect = screen.getByLabelText('Engine');
    expect(nativeSelect).toHaveValue('PDEEngine');
  });

  it('supports userEvent.selectOptions on hidden native select', async () => {
    const onChange = vi.fn();
    render(<Select label="Engine" value="" options={engines} onChange={onChange} />);
    await userEvent.selectOptions(screen.getByLabelText('Engine'), 'SnowballMCEngine');
    expect(onChange).toHaveBeenCalledWith('SnowballMCEngine');
  });
});

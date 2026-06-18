import { describe, it, expect, vi } from 'vitest';
import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DatePicker } from './DatePicker';

/** Wrapper that wires up controlled state for typing tests */
function Controlled(props: { initial?: string; label?: string }) {
  const [val, setVal] = useState(props.initial ?? '');
  return <DatePicker label={props.label ?? 'Date'} value={val} onChange={setVal} />;
}

describe('DatePicker', () => {
  it('renders with label', () => {
    render(<DatePicker label="Start Date" />);
    expect(screen.getByText('Start Date')).toBeInTheDocument();
    expect(screen.getByLabelText('Start Date')).toBeInTheDocument();
  });

  it('shows placeholder when no value', () => {
    render(<DatePicker label="Date" placeholder="Select date" />);
    expect(screen.getByPlaceholderText('Select date')).toBeInTheDocument();
  });

  it('shows value in the input field', () => {
    render(<DatePicker label="Date" value="2026-06-12" />);
    expect(screen.getByLabelText('Date')).toHaveValue('2026-06-12');
  });

  it('supports typing a date directly', async () => {
    render(<Controlled initial="" label="Date" />);
    const input = screen.getByLabelText('Date');
    await userEvent.type(input, '2026-06-12');
    expect(input).toHaveValue('2026-06-12');
  });

  it('opens calendar on icon click', async () => {
    render(<DatePicker value="2026-06-12" />);
    await userEvent.click(screen.getByLabelText('Open calendar'));
    expect(screen.getByText('Jun 2026')).toBeInTheDocument();
    expect(screen.getByLabelText('Previous month')).toBeInTheDocument();
    expect(screen.getByLabelText('Next month')).toBeInTheDocument();
  });

  it('calls onChange when a day is selected', async () => {
    const onChange = vi.fn();
    render(<DatePicker value="2026-06-12" onChange={onChange} />);
    await userEvent.click(screen.getByLabelText('Open calendar'));
    const day15 = screen.getByLabelText('2026-06-15');
    await userEvent.click(day15);
    expect(onChange).toHaveBeenCalledWith('2026-06-15');
  });

  it('calls onChange with empty string on Clear', async () => {
    const onChange = vi.fn();
    render(<DatePicker value="2026-06-12" onChange={onChange} />);
    await userEvent.click(screen.getByLabelText('Open calendar'));
    await userEvent.click(screen.getByText('Clear'));
    expect(onChange).toHaveBeenCalledWith('');
  });

  it('calls onChange with today on Today button', async () => {
    const onChange = vi.fn();
    const today = new Date();
    const todayIso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
    render(<DatePicker onChange={onChange} />);
    await userEvent.click(screen.getByLabelText('Open calendar'));
    await userEvent.click(screen.getByText('Today'));
    expect(onChange).toHaveBeenCalledWith(todayIso);
  });

  it('navigates months with arrow buttons', async () => {
    render(<DatePicker value="2026-06-12" />);
    await userEvent.click(screen.getByLabelText('Open calendar'));
    expect(screen.getByText('Jun 2026')).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText('Next month'));
    expect(screen.getByText('Jul 2026')).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText('Previous month'));
    expect(screen.getByText('Jun 2026')).toBeInTheDocument();
  });

  it('applies error state', () => {
    render(<DatePicker label="End Date" error="Required" />);
    expect(screen.getByText('Required')).toBeInTheDocument();
    expect(screen.getByLabelText('End Date')).toHaveClass('wl-datepicker__input--error');
  });

  it('renders hint when present and no error', () => {
    render(<DatePicker label="Maturity" hint="Format: YYYY-MM-DD" />);
    expect(screen.getByText('Format: YYYY-MM-DD')).toBeInTheDocument();
  });

  it('does not open when disabled', async () => {
    render(<DatePicker label="Disabled" disabled />);
    await userEvent.click(screen.getByLabelText('Open calendar'));
    expect(screen.queryByText('Clear')).not.toBeInTheDocument();
  });

  it('respects min/max constraints in calendar', async () => {
    const onChange = vi.fn();
    render(
      <DatePicker value="2026-06-12" onChange={onChange} min="2026-06-10" max="2026-06-20" />,
    );
    await userEvent.click(screen.getByLabelText('Open calendar'));
    const day5 = screen.getByLabelText('2026-06-05');
    expect(day5).toBeDisabled();
    const day25 = screen.getByLabelText('2026-06-25');
    expect(day25).toBeDisabled();
  });
});

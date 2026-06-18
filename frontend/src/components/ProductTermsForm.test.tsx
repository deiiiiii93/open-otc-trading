import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ProductTermsForm } from './ProductTermsForm';

describe('ProductTermsForm nested config rendering', () => {
  it('renders scalar nested config values as editable inputs, not raw JSON', async () => {
    const onChange = vi.fn();
    render(
      <ProductTermsForm
        productType="BarrierOption"
        productKwargs={{
          barrier_config: { barrier_level: 999, observation: 'continuous' },
        }}
        onChange={onChange}
      />,
    );
    const input = screen.getByDisplayValue('999') as HTMLInputElement;
    expect(input.readOnly).toBe(false);
    expect(input.tagName).toBe('INPUT');
    expect(screen.queryByDisplayValue(/barrier_level/)).not.toBeInTheDocument();

    fireEvent.change(input, { target: { value: '1001' } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({
      barrier_config: expect.objectContaining({ barrier_level: 1001 }),
    }));
  });

  it('renders boolean nested config values as enabled checkboxes', async () => {
    const onChange = vi.fn();
    render(
      <ProductTermsForm
        productType="BarrierOption"
        productKwargs={{
          barrier_config: { is_monitored: true, barrier_level: 999 },
        }}
        onChange={onChange}
      />,
    );
    const checkboxes = screen.getAllByRole('checkbox');
    const monitored = checkboxes.find((cb) => (cb as HTMLInputElement).checked) as HTMLInputElement | undefined;
    expect(monitored).toBeDefined();
    expect(monitored!.disabled).toBe(false);

    await userEvent.click(monitored!);
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({
      barrier_config: expect.objectContaining({ is_monitored: false }),
    }));
  });

  it('opens an editable dialog for KO schedule rows', async () => {
    const onChange = vi.fn();
    render(
      <ProductTermsForm
        productType="SnowballOption"
        productKwargs={{
          barrier_config: {
            ko_observation_schedule: {
              records: [{ observation_date: '2026-06-01', barrier: 103, return_rate: 0.12 }],
            },
          },
        }}
        onChange={onChange}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /edit/i }));
    expect(screen.getByRole('dialog', { name: /ko observation schedule/i })).toBeInTheDocument();

    const barrier = screen.getByLabelText(/ko observation schedule 1 barrier/i);
    fireEvent.change(barrier, { target: { value: '104' } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({
      barrier_config: expect.objectContaining({
        ko_observation_schedule: expect.objectContaining({
          records: [expect.objectContaining({ barrier: 104 })],
        }),
      }),
    }));
  });

  it('shows a clear inline expansion control for long observation schedules', async () => {
    const onChange = vi.fn();
    const records = Array.from({ length: 7 }, (_, index) => ({
      observation_date: `2026-06-${String(index + 1).padStart(2, '0')}`,
      barrier: 103 + index,
    }));
    const { container } = render(
      <ProductTermsForm
        productType="SnowballOption"
        productKwargs={{
          barrier_config: {
            ki_observation_schedule: { records },
          },
        }}
        onChange={onChange}
      />,
    );

    expect(screen.getByText('7 rows')).toBeInTheDocument();
    expect(screen.getByText('Showing first 5')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /show all 7 rows/i }).closest('.wl-positions__schedule-head')).not.toBeNull();
    expect(container.querySelector('.wl-positions__schedule-summary button')).toBeNull();
    expect(screen.getByText('2026-06-05')).toBeInTheDocument();
    expect(screen.queryByText('2026-06-06')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /show all 7 rows/i }));

    expect(screen.getByText('Showing all')).toBeInTheDocument();
    expect(screen.getByText('2026-06-06')).toBeInTheDocument();
    expect(screen.getByText('2026-06-07')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /show first 5 rows/i }));

    expect(screen.getByText('Showing first 5')).toBeInTheDocument();
    expect(screen.queryByText('2026-06-06')).not.toBeInTheDocument();
  });

  it('generates KO schedule rows from creator inputs', async () => {
    const onChange = vi.fn();
    render(
      <ProductTermsForm
        productType="SnowballOption"
        productKwargs={{
          barrier_config: {
            ko_observation_schedule: { records: [] },
          },
        }}
        onChange={onChange}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /edit/i }));
    expect(screen.getByRole('region', { name: /schedule creator/i })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/start date/i), { target: { value: '2026-06-30' } });
    fireEvent.change(screen.getByLabelText(/lockup periods/i), { target: { value: '1' } });
    await userEvent.selectOptions(screen.getByLabelText(/frequency/i), 'monthly');
    fireEvent.change(screen.getByLabelText(/total dates/i), { target: { value: '3' } });
    fireEvent.change(screen.getByLabelText(/ko return rate/i), { target: { value: '0.12' } });
    fireEvent.change(screen.getByLabelText(/barriers/i), { target: { value: '103, 102' } });
    await userEvent.click(screen.getByRole('button', { name: /generate schedule/i }));

    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({
      barrier_config: expect.objectContaining({
        ko_observation_schedule: expect.objectContaining({
          records: [
            { observation_date: '2026-07-30', barrier: 103, return_rate: 0.12, is_rate_annualized: false },
            { observation_date: '2026-08-30', barrier: 102, return_rate: 0.12, is_rate_annualized: false },
            { observation_date: '2026-09-30', barrier: 102, return_rate: 0.12, is_rate_annualized: false },
          ],
        }),
      }),
    }));
  });

  it('does not label nested config sections as read-only system computed', () => {
    render(
      <ProductTermsForm
        productType="BarrierOption"
        productKwargs={{
          barrier_config: { barrier_level: 999 },
        }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.queryByText(/read-only.*system computed/i)).not.toBeInTheDocument();
  });

  it('renders flat snowball contract fields with a frequency selector', async () => {
    const onChange = vi.fn();
    render(
      <ProductTermsForm
        productType="SnowballOption"
        productKwargs={{
          initial_price: 100, strike: 100, maturity_years: 1,
          ko_barrier_pct: 103, ki_barrier_pct: 75, ko_rate: 0.15,
          lockup_months: 3, trade_start_date: '2026-06-08',
          observation_frequency: 'MONTHLY', contract_multiplier: 1,
        }}
        onChange={onChange}
      />,
    );

    // observation_frequency is a constrained selector, not a free-text box
    const freq = screen.getByLabelText(/observation frequency/i);
    expect(freq.tagName).toBe('SELECT');
    expect(screen.getByRole('option', { name: 'MONTHLY' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'QUARTERLY' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'SEMI_ANNUAL' })).toBeInTheDocument();

    // a flat percent field is editable and coerces to a number
    const koBarrier = screen.getByLabelText(/ko barrier/i) as HTMLInputElement;
    fireEvent.change(koBarrier, { target: { value: '101' } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ ko_barrier_pct: 101 }));

    // the flat contract is NOT rendered through the nested barrier_config editor
    expect(screen.queryByText(/^Barrier Config$/i)).not.toBeInTheDocument();
  });

  it('renders KI observation convention as a constrained picker', async () => {
    const onChange = vi.fn();
    render(
      <ProductTermsForm
        productType="SnowballOption"
        productKwargs={{
          _otc_ki_observation_convention: 'DAILY',
        }}
        onChange={onChange}
      />,
    );

    const convention = screen.getByLabelText('KI Observation Convention');
    expect(convention).toHaveValue('DAILY');
    expect(screen.getByRole('option', { name: 'DAILY' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'EUROPEAN' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'NONE' })).toBeInTheDocument();

    await userEvent.selectOptions(convention, 'EUROPEAN');
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({
      _otc_ki_observation_convention: 'EUROPEAN',
    }));
  });
});

describe('intake product coverage', () => {
  it('renders OneTouchOption with QuantArk touch fields (rebate, direction)', () => {
    render(
      <ProductTermsForm
        productType="OneTouchOption"
        productKwargs={{ barrier: 120, rebate: 10, barrier_direction: 'UP', touch_type: 'ONE_TOUCH', maturity: 1 }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByLabelText('Barrier')).toHaveValue(120);
    expect(screen.getByLabelText('Rebate')).toHaveValue(10);
    expect(screen.getByLabelText('Barrier Direction')).toHaveValue('UP');
    expect(screen.getByLabelText('Touch Type')).toHaveValue('ONE_TOUCH');
  });

  it('renders RangeAccrualOption with an editable range config group', () => {
    render(
      <ProductTermsForm
        productType="RangeAccrualOption"
        productKwargs={{
          initial_price: 100,
          maturity: 1,
          num_observations: 252,
          contract_multiplier: 1,
          range_config: { lower_barrier: 80, upper_barrier: 120, accrual_rate: 0.15, is_rate_annualized: false },
        }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByLabelText('Initial Price')).toHaveValue(100);
    expect(screen.getByLabelText('Maturity (years)')).toHaveValue(1);
    expect(screen.getByLabelText('Num Observations')).toHaveValue(252);
    // range_config renders as an editable nested group, not a readonly extra
    expect(screen.getByText('Range Config')).toBeInTheDocument();
    expect(screen.getByLabelText('Accrual Rate')).toHaveValue(0.15);
  });

  it('renders SpotInstrument with a deltaone type select', () => {
    render(
      <ProductTermsForm
        productType="SpotInstrument"
        productKwargs={{ deltaone_type: 'INDEX', underlying: 'CSI500' }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByLabelText('DeltaOne Type')).toHaveValue('INDEX');
  });

  it('renders Futures maturity as a primary field, not an extra', () => {
    render(
      <ProductTermsForm
        productType="Futures"
        productKwargs={{ maturity: 1, contract_multiplier: 1 }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByLabelText('Maturity (years)')).toHaveValue(1);
  });

  it('renders nested KnockOutResetSnowballOption like SnowballOption', () => {
    render(
      <ProductTermsForm
        productType="KnockOutResetSnowballOption"
        productKwargs={{ initial_price: 100, strike: 100, barrier_config: {} }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByLabelText('Initial Price')).toHaveValue(100);
  });
});

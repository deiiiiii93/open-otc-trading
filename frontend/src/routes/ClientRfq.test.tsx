import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ClientRfq } from './ClientRfq';
import type { RFQ, RfqCatalog, Underlying } from '../types';

const catalog: RfqCatalog = {
  product_types: [],
  engine_options: [],
  unknown_fields: {
    EuropeanVanillaOption: ['strike', 'volatility'],
    SnowballOption: ['barrier_config.ko_rate'],
  },
  templates: [
    {
      key: 'vanilla',
      label: 'Vanilla',
      product_type: 'EuropeanVanillaOption',
      engine_spec: { engine_name: 'BlackScholesEngine' },
      unknown_fields: ['strike', 'volatility'],
      unknown_field_specs: [
        { field_path: 'strike', label: 'Strike', lower_bound: 50, upper_bound: 150, initial_guess: 100 },
        { field_path: 'volatility', label: 'Volatility', lower_bound: 0.01, upper_bound: 2, initial_guess: 0.2 },
      ],
      product_kwargs: { strike: 100, option_type: 'CALL', maturity: 1, contract_multiplier: 1 },
    },
    {
      key: 'snowball',
      label: 'Snowball',
      product_type: 'SnowballOption',
      engine_spec: { engine_name: 'SnowballQuadEngine' },
      unknown_fields: ['barrier_config.ko_rate'],
      unknown_field_specs: [
        { field_path: 'barrier_config.ko_rate', label: 'KO Rate', lower_bound: -1, upper_bound: 2, initial_guess: 0.15 },
      ],
      product_kwargs: {
        initial_price: 100,
        strike: 100,
        maturity_years: 1,
        ko_barrier_pct: 103,
        ki_barrier_pct: 75,
        ko_rate: 0.15,
        lockup_months: 3,
        trade_start_date: '2026-06-13',
        observation_frequency: 'MONTHLY',
        contract_multiplier: 1,
      },
    },
  ],
  advanced: {},
};

const underlyings = [
  { symbol: '000852.SH', display_name: 'CSI 1000', status: 'active' },
  { symbol: 'OLD.SH', display_name: 'Retired', status: 'inactive' },
] as unknown as Underlying[];

const rfqs: RFQ[] = [
  {
    id: 42,
    client_name: 'Demo Client',
    channel: 'form',
    status: 'pending_approval',
    request_payload: {
      product_type: 'SnowballOption',
      underlying: '000852.SH',
      side: 'sell',
      quantity: 2,
      quote_mode: 'solve',
      product_kwargs: {
        initial_price: 100,
        strike: 100,
        maturity_years: 1,
        ko_barrier_pct: 103,
        ki_barrier_pct: 75,
        ko_rate: 0.15,
        lockup_months: 3,
        trade_start_date: '2026-06-13',
        observation_frequency: 'MONTHLY',
        contract_multiplier: 1,
      },
      engine_spec: { engine_name: 'SnowballQuadEngine' },
      unknown: { field_path: 'barrier_config.ko_rate', lower_bound: -1, upper_bound: 2, initial_guess: 0.15 },
      target: { label: 'premium', value: 10 },
    },
    quote_payload: {},
    created_at: '2026-06-06T08:00:00Z',
  },
  {
    id: 41,
    client_name: 'Demo Client',
    channel: 'form',
    status: 'approved',
    request_payload: { product_type: 'EuropeanVanillaOption', underlying: '000852.SH', side: 'buy', quantity: 1 },
    quote_payload: {
      field_label: 'strike',
      solved_value: 104.2,
      achieved_price: 10.0001,
      client_response: 'Quote ready for review',
    },
    approved_response: 'Solved strike 104.2 at price 10.0001',
    created_at: '2026-06-05T08:00:00Z',
  },
];

function renderPage(overrides: Partial<Parameters<typeof ClientRfq>[0]> = {}) {
  return render(
    <ClientRfq
      catalog={catalog}
      underlyings={underlyings}
      rfqs={rfqs}
      clientName="Demo Client"
      defaultMessage="Quote me a snowball"
      {...overrides}
    />,
  );
}

describe('ClientRfq workbench', () => {
  it('renders three panels with the structured editor by default', () => {
    renderPage();
    expect(screen.getByRole('heading', { name: 'CLIENT RFQ' })).toBeInTheDocument();
    expect(screen.getByRole('list', { name: /my rfqs/i })).toBeInTheDocument();
    expect(screen.getByLabelText('Product')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /submit rfq/i })).toBeInTheDocument();
  });

  it('swaps editor panels for the message panel in NL mode', async () => {
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /natural language/i }));
    expect(screen.getByLabelText('Message')).toHaveValue('Quote me a snowball');
    expect(screen.queryByLabelText('Product')).not.toBeInTheDocument();
    expect(screen.getByRole('list', { name: /my rfqs/i })).toBeInTheDocument();
  });

  it('submits the NL message', async () => {
    const onSubmitNL = vi.fn();
    renderPage({ onSubmitNL });
    await userEvent.click(screen.getByRole('tab', { name: /natural language/i }));
    await userEvent.click(screen.getByRole('button', { name: /submit natural language/i }));
    expect(onSubmitNL).toHaveBeenCalledWith('Quote me a snowball');
  });

  it('only lists active underlyings', () => {
    renderPage();
    const select = screen.getByLabelText('Underlying');
    expect(select).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /000852\.SH/ })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /OLD\.SH/ })).not.toBeInTheDocument();
  });

  it('switching product repopulates terms and solve-for defaults', async () => {
    renderPage();
    await userEvent.selectOptions(screen.getByLabelText('Product'), 'SnowballOption');
    expect(screen.getByLabelText('KO Barrier %')).toHaveValue(103);
    expect(screen.getByLabelText('Solve For')).toHaveDisplayValue('KO Rate');
    expect(screen.getByLabelText('Lower Bound')).toHaveValue(-1);
    expect(screen.getByLabelText('Upper Bound')).toHaveValue(2);
    expect(screen.getByLabelText('Initial Guess')).toHaveValue(0.15);
  });

  it('changing the solve-for field prefills its bounds', async () => {
    renderPage();
    await userEvent.selectOptions(screen.getByLabelText('Solve For'), 'volatility');
    expect(screen.getByLabelText('Lower Bound')).toHaveValue(0.01);
    expect(screen.getByLabelText('Upper Bound')).toHaveValue(2);
    expect(screen.getByLabelText('Initial Guess')).toHaveValue(0.2);
  });

  it('price mode hides the solve controls', async () => {
    renderPage();
    await userEvent.selectOptions(screen.getByLabelText('Quote Mode'), 'price');
    expect(screen.queryByLabelText('Solve For')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Target Value')).not.toBeInTheDocument();
  });

  it('gates submit until an underlying is chosen and a positive target is set', async () => {
    const onSubmitStructured = vi.fn();
    renderPage({ onSubmitStructured });
    const submit = screen.getByRole('button', { name: /submit rfq/i });
    expect(submit).toBeDisabled();

    await userEvent.selectOptions(screen.getByLabelText('Underlying'), '000852.SH');
    // Still gated: solve mode needs a positive target (templates seed 0,
    // which the backend rejects).
    expect(submit).toBeDisabled();

    await userEvent.clear(screen.getByLabelText('Target Value'));
    await userEvent.type(screen.getByLabelText('Target Value'), '8');
    expect(submit).toBeEnabled();

    await userEvent.click(submit);
    expect(onSubmitStructured).toHaveBeenCalledTimes(1);
    const form = onSubmitStructured.mock.calls[0][0];
    expect(form.product).toBe('EuropeanVanillaOption');
    expect(form.underlying).toBe('000852.SH');
    expect(form.unknownField).toBe('strike');
    expect(form.quoteMode).toBe('solve');
    expect(form.targetValue).toBe(8);
  });

  it('gates submit when a solver bound is blanked', async () => {
    renderPage();
    await userEvent.selectOptions(screen.getByLabelText('Underlying'), '000852.SH');
    await userEvent.clear(screen.getByLabelText('Target Value'));
    await userEvent.type(screen.getByLabelText('Target Value'), '8');
    const submit = screen.getByRole('button', { name: /submit rfq/i });
    expect(submit).toBeEnabled();

    // A cleared bound would serialize as a degenerate 0 — keep the draft gated.
    await userEvent.clear(screen.getByLabelText('Lower Bound'));
    expect(submit).toBeDisabled();

    // Zero is a legitimate bound (e.g. basis): typing it re-enables submit.
    await userEvent.type(screen.getByLabelText('Lower Bound'), '0');
    expect(submit).toBeEnabled();
  });

  it('gates submit when a required snowball contract term is blanked', async () => {
    renderPage();
    await userEvent.selectOptions(screen.getByLabelText('Underlying'), '000852.SH');
    await userEvent.selectOptions(screen.getByLabelText('Product'), 'SnowballOption');
    await userEvent.clear(screen.getByLabelText('Target Value'));
    await userEvent.type(screen.getByLabelText('Target Value'), '8');
    const submit = screen.getByRole('button', { name: /submit rfq/i });
    expect(submit).toBeEnabled();

    await userEvent.clear(screen.getByLabelText('KI Barrier %'));
    expect(submit).toBeDisabled();
  });

  it('shows status detail for the selected RFQ', async () => {
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /#41 vanilla/i }));
    expect(screen.getByText('Solved strike 104.2 at price 10.0001')).toBeInTheDocument();
    expect(screen.getByText('104.200000')).toBeInTheDocument();
  });

  it('clones a history RFQ into the editor', async () => {
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /#42 snowball/i }));
    await userEvent.click(screen.getByRole('button', { name: /clone rfq 42/i }));
    expect(screen.getByLabelText('Product')).toHaveValue('SnowballOption');
    expect(screen.getByLabelText('Underlying')).toHaveValue('000852.SH');
    expect(screen.getByLabelText('Side')).toHaveValue('sell');
    expect(screen.getByLabelText('Notional')).toHaveValue(2);
    expect(screen.getByLabelText('Target Label')).toHaveValue('premium');
    expect(screen.getByLabelText('Target Value')).toHaveValue(10);
  });

  it('reports page context with declared actions', () => {
    const onPageContextChange = vi.fn();
    renderPage({ onPageContextChange });
    const context = onPageContextChange.mock.calls.at(-1)?.[0];
    expect(context.route).toBe('client-rfq');
    expect(context.actions.map((a: { name: string }) => a.name)).toEqual([
      'submit_structured_rfq',
      'submit_nl_rfq',
    ]);
  });
});

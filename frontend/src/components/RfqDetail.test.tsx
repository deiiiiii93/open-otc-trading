import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RfqDetail } from './RfqDetail';
import type { RFQ } from '../types';

const baseRfq: RFQ = {
  id: 1042,
  client_name: 'Lakeshore Cap',
  channel: 'chat',
  status: 'pending_approval',
  request_payload: { product_type: 'BarrierOption', underlying: 'CSI500' },
  quote_payload: { solved_value: 0.2, achieved_price: 10.041 },
  approved_response: null,
};

describe('RfqDetail', () => {
  it('renders RFQ id, client, product, prices', () => {
    render(<RfqDetail rfq={baseRfq} onApprove={() => {}} onRejectClick={() => {}} />);
    expect(screen.getByText(/RFQ #1042/i)).toBeInTheDocument();
    expect(screen.getByText('Lakeshore Cap')).toBeInTheDocument();
    expect(screen.getByText('Barrier', { selector: '.wl-badge' })).toBeInTheDocument();
    expect(screen.getByText(/10\.041/)).toBeInTheDocument();
  });

  it('renders quote amount separately from unit price', () => {
    render(
      <RfqDetail
        rfq={{
          ...baseRfq,
          request_payload: {
            product_type: 'SnowballOption',
            underlying: '000905.SH',
            quantity: 5000000,
            product_kwargs: { initial_price: 100 },
            market: { currency: 'CNY' },
          },
          quote_payload: { achieved_price: 10.008375586187357 },
        }}
        onApprove={() => {}}
        onRejectClick={() => {}}
      />,
    );

    expect(screen.getByText('Quote Amount')).toBeInTheDocument();
    expect(screen.getByText(/CNY\s+500,418\.78/)).toBeInTheDocument();
    expect(screen.getByText('Unit Price')).toBeInTheDocument();
    expect(screen.getByText('10.008376')).toBeInTheDocument();
  });

  it('renders Snowball quote contract fields from nested legacy terms', async () => {
    const onQuote = vi.fn();
    render(
      <RfqDetail
        rfq={{
          ...baseRfq,
          id: 8,
          client_name: 'YNIT',
          status: 'pricing_failed',
          request_payload: {
            client_name: 'YNIT',
            underlying: '000905.SH',
            side: 'sell',
            quantity: 5000000,
            quote_mode: 'price',
            product_type: 'SnowballOption',
            product_kwargs: {
              initial_price: 100,
              strike: 100,
              maturity: 1,
              contract_multiplier: 1,
              barrier_config: {
                ko_barrier: 101,
                ko_rate: 0.15,
                ki_barrier: 70,
              },
            },
            market: {
              valuation_date: '2026-05-29T00:00:00',
              spot: 100,
              volatility: 0.2,
              rate: 0.03,
              dividend_yield: 0,
              currency: 'CNY',
            },
          },
          quote_payload: {
            validation: {
              valid: false,
              errors: [],
              missing_fields: ['barrier_config.lockup_months', 'observation_frequency'],
            },
          },
        }}
        onQuote={onQuote}
      />,
    );

    expect((screen.getByLabelText(/maturity/i) as HTMLInputElement).value).toBe('1');
    expect((screen.getByLabelText(/trade start date/i) as HTMLInputElement).value).toBe('2026-05-29');
    expect((screen.getByLabelText(/ko barrier/i) as HTMLInputElement).value).toBe('101');
    expect((screen.getByLabelText(/ki barrier/i) as HTMLInputElement).value).toBe('70');
    expect((screen.getByLabelText(/ko rate/i) as HTMLInputElement).value).toBe('0.15');
    expect(screen.getByLabelText(/lockup months/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/observation frequency/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/lockup months/i), { target: { value: '1' } });
    fireEvent.change(screen.getByLabelText(/observation frequency/i), { target: { value: 'MONTHLY' } });
    await userEvent.click(screen.getByRole('button', { name: /^quote$/i }));

    expect(onQuote).toHaveBeenCalledWith(8, expect.objectContaining({
      product_kwargs: expect.objectContaining({
        initial_price: 100,
        strike: 100,
        maturity: 1,
        maturity_years: 1,
        trade_start_date: '2026-05-29',
        ko_barrier_pct: 101,
        ki_barrier_pct: 70,
        ko_rate: 0.15,
        lockup_months: 1,
        observation_frequency: 'MONTHLY',
        barrier_config: expect.objectContaining({
          ko_barrier: 101,
          ko_rate: 0.15,
          ki_barrier: 70,
        }),
      }),
      market: expect.objectContaining({
        spot: 100,
        volatility: 0.2,
        rate: 0.03,
        dividend_yield: 0,
      }),
    }));
  });

  it('shows requested and executable quote terms when present', () => {
    render(
      <RfqDetail
        rfq={{
          ...baseRfq,
          quote_versions: [
            {
              id: 1,
              rfq_id: 1042,
              version: 1,
              quote_mode: 'solve',
              status: 'pending_approval',
              request_payload: {
                terms: { product_kwargs: { strike: 100 } },
                executable_terms: { product_kwargs: { strike: 110.5 } },
              },
              quote_payload: {},
              created_by: 'desk',
              created_at: '2026-05-12T00:00:00',
            },
          ],
        }}
      />
    );
    expect(screen.getByText(/requested terms/i)).toBeInTheDocument();
    expect(screen.getByText(/executable quoted terms/i)).toBeInTheDocument();
    expect(screen.getByText(/110\.5/)).toBeInTheDocument();
  });

  it('shows approve/reject buttons when pending_approval', () => {
    render(<RfqDetail rfq={baseRfq} onApprove={() => {}} onRejectClick={() => {}} />);
    expect(screen.getByRole('button', { name: /approve/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reject/i })).toBeInTheDocument();
  });

  it('hides approve/reject when not pending_approval', () => {
    render(<RfqDetail rfq={{ ...baseRfq, status: 'approved' }} onApprove={() => {}} onRejectClick={() => {}} />);
    expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /reject/i })).not.toBeInTheDocument();
  });

  it('calls onApprove with rfq id', async () => {
    const onApprove = vi.fn();
    render(<RfqDetail rfq={baseRfq} onApprove={onApprove} onRejectClick={() => {}} />);
    await userEvent.click(screen.getByRole('button', { name: /approve/i }));
    expect(onApprove).toHaveBeenCalledWith(1042);
  });

  it('calls onRejectClick with rfq id', async () => {
    const onRejectClick = vi.fn();
    render(<RfqDetail rfq={baseRfq} onApprove={() => {}} onRejectClick={onRejectClick} />);
    await userEvent.click(screen.getByRole('button', { name: /reject/i }));
    expect(onRejectClick).toHaveBeenCalledWith(1042);
  });
});

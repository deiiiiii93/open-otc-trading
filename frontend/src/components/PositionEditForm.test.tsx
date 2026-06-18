import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PositionEditForm, inferProductFamily } from './PositionEditForm';
import type { PositionRow } from '../routes/Positions';

const row: PositionRow = {
  id: 42,
  trade_id: 'T-SNOWBALL',
  product_id: 88,
  product: {
    id: 88,
    asset_class: 'equity',
    product_family: 'autocallable',
    quantark_class: 'SnowballOption',
    underlying: 'CSI500',
    currency: 'USD',
    terms: { strike: 100, initial_price: 100 },
  },
  underlying: 'CSI500',
  product_type: 'SnowballOption',
  quantity: -1,
  entry_price: 100,
  currency: 'CNY',
  status: 'open',
  position_kind: 'otc',
  mapping_status: 'supported',
  product_kwargs: { strike: 100, initial_price: 100 },
  engine_name: 'SnowballQuadEngine',
  price: null,
  market_value: null,
  pnl: null,
  delta: null,
  gamma: null,
  vega: null,
  theta: null,
  rho: null,
  rho_q: null,
};

describe('PositionEditForm', () => {
  it('submits legacy fields and the nested product object', async () => {
    const onSave = vi.fn();
    render(<PositionEditForm row={row} onSave={onSave} saving={false} />);

    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave).toHaveBeenCalledWith(row, expect.objectContaining({
      underlying: 'CSI500',
      product_type: 'SnowballOption',
      product_kwargs: { strike: 100, initial_price: 100 },
      product: {
        asset_class: 'equity',
        product_family: 'autocallable',
        quantark_class: 'SnowballOption',
        underlying: 'CSI500',
        currency: 'CNY',
        terms: { strike: 100, initial_price: 100 },
        components: [],
      },
    }));
  });

  it('submits the edited currency and uses it in the product spec', async () => {
    const onSave = vi.fn();
    render(<PositionEditForm row={row} onSave={onSave} saving={false} />);

    const input = screen.getByLabelText('Currency');
    await userEvent.clear(input);
    await userEvent.type(input, 'hkd');
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const updates = onSave.mock.calls[0][1];
    expect(updates.currency).toBe('HKD');
    expect(updates.product).toEqual(expect.objectContaining({ currency: 'HKD' }));
  });

  it('warns when the currency deviates from the booked trade currency', async () => {
    render(<PositionEditForm row={row} onSave={vi.fn()} saving={false} />);
    // Seeded CNY vs product.currency USD -> warning visible immediately.
    expect(screen.getByText(/differs from booked trade currency \(USD\)/i)).toBeInTheDocument();

    const input = screen.getByLabelText('Currency');
    await userEvent.clear(input);
    await userEvent.type(input, 'USD');
    expect(screen.queryByText(/differs from booked trade currency/i)).not.toBeInTheDocument();
  });

  it('rejects a malformed currency before submitting', async () => {
    const onSave = vi.fn();
    render(<PositionEditForm row={row} onSave={onSave} saving={false} />);

    const input = screen.getByLabelText('Currency');
    await userEvent.clear(input);
    await userEvent.type(input, 'C1');
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    expect(await screen.findByText(/currency must be a 3-letter ISO code/i)).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it('submits package components as top-level product components', async () => {
    const onSave = vi.fn();
    render(
      <PositionEditForm
        row={{
          ...row,
          product_type: 'EuropeanVanillaOption',
          product: {
            ...row.product!,
            product_family: 'package',
            quantark_class: 'EuropeanVanillaOption',
            terms: {
              strike: 100,
              components: [{ component_product_id: 7, quantity: 1 }],
            },
            components: [{ component_product_id: 7, quantity: 1 }],
          },
          product_kwargs: {
            strike: 100,
            components: [{ component_product_id: 7, quantity: 1 }],
          },
        }}
        onSave={onSave}
        saving={false}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave.mock.calls[0][1].product).toEqual(expect.objectContaining({
      product_family: 'package',
      terms: {
        strike: 100,
        components: [{ component_product_id: 7, quantity: 1 }],
      },
      components: [{ component_product_id: 7, quantity: 1 }],
    }));
  });

  it('shows Futures as the selected product type for listed futures', () => {
    render(
      <PositionEditForm
        row={{
          ...row,
          trade_id: 'HEDGE:33:1',
          product_type: 'Futures',
          product: {
            ...row.product!,
            product_family: 'futures',
            quantark_class: 'Futures',
            terms: { maturity: 0.04, multiplier: 300, underlying: '000300.SH' },
          },
          product_kwargs: { maturity: 0.04, multiplier: 300, underlying: '000300.SH' },
          engine_name: 'DeltaOneEngine',
          position_kind: 'listed',
        }}
        onSave={vi.fn()}
        saving={false}
      />,
    );

    expect(screen.getByLabelText('Product Type')).toHaveValue('Futures');
    expect(screen.getByLabelText('Engine')).toHaveValue('DeltaOneEngine');
  });

  it('infers current-stage product families', () => {
    expect(inferProductFamily('SnowballOption')).toBe('autocallable');
    expect(inferProductFamily('PhoenixOption')).toBe('autocallable');
    expect(inferProductFamily('KnockOutResetSnowballOption')).toBe('autocallable');
    expect(inferProductFamily('AutocallableOption')).toBe('autocallable');
    expect(inferProductFamily('BarrierOption')).toBe('barrier');
    expect(inferProductFamily('OneTouchOption')).toBe('touch');
    expect(inferProductFamily('AsianOption')).toBe('asian');
    expect(inferProductFamily('RangeAccrualOption')).toBe('range_accrual');
    expect(inferProductFamily('SingleSharkfinOption')).toBe('sharkfin');
    expect(inferProductFamily('ETF')).toBe('spot');
    expect(inferProductFamily('Spot')).toBe('spot');
    expect(inferProductFamily('SpotInstrument')).toBe('spot');
    expect(inferProductFamily('Futures')).toBe('futures');
    expect(inferProductFamily('EuropeanVanillaOption', { components: [{ weight: 1 }] })).toBe('package');
    expect(inferProductFamily('EuropeanVanillaOption')).toBe('option');
  });
});

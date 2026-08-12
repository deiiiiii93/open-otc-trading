import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PositionLifecycleTimeline } from './PositionLifecycleTimeline';
import type { LifecycleVocabulary, PositionLifecycleEvent } from '../types';
import type { PositionRow } from '../routes/Positions';

const vocabulary: LifecycleVocabulary = {
  event_types: { knock_out: 'closed', settle: 'closed', custom: null },
  by_family: {
    SnowballOption: ['close', 'custom', 'knock_in', 'knock_out', 'settle'],
  },
  retired: ['autocall', 'coupon_lock'],
  event_fields: {
    settle: [
      { key: 'settlement_amount', label: 'Settlement Amount', type: 'number' },
      { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
    ],
    knock_out: [{ key: 'barrier_level', label: 'Barrier Level', type: 'number' }],
    close: [{ key: 'reason', label: 'Reason', type: 'text' }],
    custom: [{ key: 'reason', label: 'Reason', type: 'text' }],
    knock_in: [{ key: 'barrier_level', label: 'Barrier Level', type: 'number' }],
    exercise: [
      { key: 'exercise_date', label: 'Exercise Date', type: 'date' },
      { key: 'early', label: 'Early Exercise', type: 'bool' },
    ],
  },
};

const row: PositionRow = {
  id: 42,
  trade_id: 'T-SNOWBALL',
  underlying: '000852.SH',
  product_type: 'SnowballOption',
  quantity: -1,
  entry_price: 0,
  currency: 'CNY',
  status: 'closed',
  position_kind: 'otc',
  mapping_status: 'supported',
  price: null,
  market_value: null,
  pnl: null,
  delta: null,
  gamma: null,
  vega: null,
  theta: null,
  rho: null,
  rho_q: null,
  pricing_error: null,
  product_kwargs: {},
  market_inputs: {},
  engine_name: 'SnowballQuadEngine',
};

const activeEvent: PositionLifecycleEvent = {
  id: 7,
  position_id: 42,
  event_type: 'knock_out',
  event_data: { observation_date: '2026-05-27' },
  old_status: 'open',
  new_status: 'closed',
  actor: 'agent',
  created_at: '2026-05-27T09:14:12Z',
};

const cancelledEvent: PositionLifecycleEvent = {
  ...activeEvent,
  id: 8,
  cancelled_at: '2026-05-27T10:00:00Z',
  cancelled_by: 'desk_user',
  cancellation_reason: 'wrong event',
};

describe('PositionLifecycleTimeline', () => {
  beforeEach(() => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    vi.spyOn(window, 'prompt').mockReturnValue('bad observation');
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders cancelled events as retained audit rows', () => {
    render(
      <PositionLifecycleTimeline
        row={row}
        events={[cancelledEvent]}
        onAddEvent={vi.fn()}
        onCancelEvent={vi.fn()}
        adding={false}
        vocabulary={vocabulary}
      />,
    );

    expect(screen.getByText('cancelled')).toBeInTheDocument();
    expect(screen.getByText(/Cancelled by desk_user/)).toBeInTheDocument();
    expect(screen.getByText(/wrong event/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /cancel lifecycle event/i })).not.toBeInTheDocument();
  });

  it('confirms and emits cancel action for active events', async () => {
    const onCancelEvent = vi.fn();
    render(
      <PositionLifecycleTimeline
        row={row}
        events={[activeEvent]}
        onAddEvent={vi.fn()}
        onCancelEvent={onCancelEvent}
        adding={false}
        vocabulary={vocabulary}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /cancel lifecycle event 7/i }));

    expect(window.confirm).toHaveBeenCalledWith('Cancel lifecycle event #7 (knock_out)?');
    expect(onCancelEvent).toHaveBeenCalledWith(row, activeEvent, 'bad observation');
  });

  it('offers the server-supplied events, including settle', async () => {
    render(
      <PositionLifecycleTimeline
        row={row}
        events={[]}
        onAddEvent={vi.fn()}
        onCancelEvent={vi.fn()}
        adding={false}
        vocabulary={vocabulary}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /new/i }));
    await userEvent.click(screen.getByRole('button', { name: /select event type/i }));

    // `settle` was absent from the old hardcoded table, so the desk could not
    // record a settlement amount from the UI at all.
    expect(screen.getByRole('option', { name: 'settle' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'knock_in' })).toBeInTheDocument();
  });

  it('disables the picker until the vocabulary arrives', () => {
    render(
      <PositionLifecycleTimeline
        row={row}
        events={[]}
        onAddEvent={vi.fn()}
        onCancelEvent={vi.fn()}
        adding={false}
        vocabulary={null}
      />,
    );

    expect(screen.getByRole('button', { name: /new/i })).toBeDisabled();
  });

  it('submits a bool field as a real boolean, not the string "true"', async () => {
    const onAddEvent = vi.fn();
    render(
      <PositionLifecycleTimeline
        row={row}
        events={[]}
        onAddEvent={onAddEvent}
        onCancelEvent={vi.fn()}
        adding={false}
        vocabulary={{
          ...vocabulary,
          by_family: { SnowballOption: ['exercise'] },
        }}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /new/i }));
    await userEvent.click(screen.getByRole('button', { name: /select event type/i }));
    await userEvent.click(screen.getByRole('option', { name: 'exercise' }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'Early Exercise' }));
    await userEvent.click(screen.getByRole('button', { name: /add event/i }));

    expect(onAddEvent).toHaveBeenCalledWith(row, 'exercise', { early: true });
  });
});

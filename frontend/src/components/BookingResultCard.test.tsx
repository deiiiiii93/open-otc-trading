import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { BookingResultCard } from './BookingResultCard';
import type { BookingResultMeta } from '../types';

const BOOKED: BookingResultMeta = {
  status: 'booked',
  position_id: 27,
  trade_id: 2,
  family: 'BarrierOption',
  underlying: 'TSLA',
  quantity: 750,
  entry_price: 9.15,
  currency: 'USD',
  counterparty: 'Kestrel Capital Partners LLC',
  trade_date: '2026-08-04',
  external_trade_id: 'ARD-EQO-2026-04701',
  source_document: 'conf-05-knockout-barrier-tsla.pdf',
  portfolio: { id: 1, name: 'Default' },
  terms: { strike: 330, barrier: 420, option_type: 'CALL', rebate: 2.5 },
};

describe('BookingResultCard', () => {
  it('leads with the booked position id — the one fact the user needs', () => {
    render(<BookingResultCard booking={BOOKED} />);
    const card = screen.getByTestId('booking-result-card');
    expect(within(card).getByText('Position booked')).toBeInTheDocument();
    expect(within(card).getByText('#27')).toBeInTheDocument();
  });

  it('states the product, size and destination portfolio', () => {
    render(<BookingResultCard booking={BOOKED} />);
    const card = screen.getByTestId('booking-result-card');
    expect(within(card).getByText(/BarrierOption/)).toBeInTheDocument();
    expect(within(card).getByText('TSLA')).toBeInTheDocument();
    expect(within(card).getByText('750')).toBeInTheDocument();
    expect(within(card).getByText(/Default/)).toBeInTheDocument();
    expect(within(card).getByText('Kestrel Capital Partners LLC')).toBeInTheDocument();
  });

  it('renders the booked terms', () => {
    render(<BookingResultCard booking={BOOKED} />);
    const terms = screen.getByTestId('booking-result-terms');
    expect(within(terms).getByText('Strike')).toBeInTheDocument();
    expect(within(terms).getByText('330')).toBeInTheDocument();
    expect(within(terms).getByText('Barrier')).toBeInTheDocument();
    expect(within(terms).getByText('Option type')).toBeInTheDocument();
    expect(within(terms).getByText('CALL')).toBeInTheDocument();
  });

  it('shows the source confirmation document for auditability', () => {
    render(<BookingResultCard booking={BOOKED} />);
    expect(
      screen.getByText('conf-05-knockout-barrier-tsla.pdf'),
    ).toBeInTheDocument();
    expect(screen.getByText(/ARD-EQO-2026-04701/)).toBeInTheDocument();
  });

  it('renders a failure as a result, with the reason', () => {
    render(
      <BookingResultCard
        booking={{
          ...BOOKED,
          status: 'failed',
          position_id: null,
          error: 'validation_failed',
          detail: ['underlying "Apple Inc." is not a bookable instrument'],
        }}
      />,
    );
    const card = screen.getByTestId('booking-result-card');
    expect(within(card).getByText('Booking refused')).toBeInTheDocument();
    expect(within(card).getByText(/validation failed/i)).toBeInTheDocument();
    expect(
      within(card).getByText(/underlying "Apple Inc." is not a bookable instrument/),
    ).toBeInTheDocument();
    expect(within(card).queryByText('#27')).not.toBeInTheDocument();
  });

  it('points an already-booked result at the existing position', () => {
    render(
      <BookingResultCard
        booking={{ ...BOOKED, status: 'already_booked', error: 'already_booked' }}
      />,
    );
    const card = screen.getByTestId('booking-result-card');
    expect(within(card).getByText('Already booked')).toBeInTheDocument();
    expect(within(card).getByText('#27')).toBeInTheDocument();
  });

  it('renders a string detail as well as a list', () => {
    render(
      <BookingResultCard
        booking={{ ...BOOKED, status: 'failed', position_id: null,
          error: 'booking_failed', detail: 'QuantArk rejected initial_price' }}
      />,
    );
    expect(screen.getByText(/QuantArk rejected initial_price/)).toBeInTheDocument();
  });

  it('survives a sparse payload without crashing', () => {
    render(
      <BookingResultCard booking={{ status: 'booked', position_id: 5, trade_id: 1 }} />,
    );
    expect(screen.getByTestId('booking-result-card')).toBeInTheDocument();
    expect(screen.getByText('#5')).toBeInTheDocument();
    expect(screen.queryByTestId('booking-result-terms')).not.toBeInTheDocument();
  });
});

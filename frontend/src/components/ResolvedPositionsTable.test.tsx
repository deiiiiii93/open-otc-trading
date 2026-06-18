import { test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ResolvedPositionsTable } from './ResolvedPositionsTable';

const rows = [
  { id: 1, underlying: 'AAPL', product_type: 'Snowball', quantity: 10, entry_price: 0, status: 'open' },
  { id: 2, underlying: 'TSLA', product_type: 'Snowball', quantity: 5,  entry_price: 0, status: 'open' },
];

test('renders rows', () => {
  render(<ResolvedPositionsTable rows={rows as any} />);
  expect(screen.getByText('AAPL')).toBeInTheDocument();
  expect(screen.getByText('TSLA')).toBeInTheDocument();
});

test('shows empty state', () => {
  render(<ResolvedPositionsTable rows={[]} />);
  expect(screen.getByText(/no positions/i)).toBeInTheDocument();
});

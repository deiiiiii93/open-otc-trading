import { render, screen } from '@testing-library/react';
import { test, expect } from 'vitest';
import { PortfolioPicker } from './PortfolioPicker';

const portfolios = [
  { id: 1, name: 'A', kind: 'view' },
  { id: 2, name: 'B', kind: 'container' },
  { id: 3, name: 'Self', kind: 'view' },
];

test('hides current portfolio and known descendants from candidates', () => {
  render(
    <PortfolioPicker
      open
      portfolios={portfolios as any}
      currentPortfolioId={3}
      excludedIds={new Set([1])}
      onCancel={() => {}}
      onConfirm={() => {}}
    />,
  );
  expect(screen.queryByText('Self')).not.toBeInTheDocument();
  expect(screen.queryByText('A')).not.toBeInTheDocument();
  expect(screen.getByText('B')).toBeInTheDocument();
});

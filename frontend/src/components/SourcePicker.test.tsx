import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { test, expect, vi } from 'vitest';
import { SourcePicker } from './SourcePicker';

const portfolios = [
  { id: 1, name: 'Snowballs', kind: 'view' as const, resolved_position_count: 130 },
  { id: 2, name: 'Vanillas', kind: 'view' as const, resolved_position_count: 88 },
  { id: 3, name: 'Owned', kind: 'container' as const, resolved_position_count: 12 },
];

test('lists portfolios excluding self and already-selected sources', () => {
  render(
    <SourcePicker
      open
      portfolios={portfolios}
      currentPortfolioId={1}
      excludeIds={[3]}
      onCancel={() => {}}
      onConfirm={() => {}}
    />,
  );
  expect(screen.queryByText('Snowballs')).not.toBeInTheDocument();
  expect(screen.queryByText('Owned')).not.toBeInTheDocument();
  expect(screen.getByText('Vanillas')).toBeInTheDocument();
});

test('filters by search', async () => {
  render(
    <SourcePicker
      open
      portfolios={portfolios}
      currentPortfolioId={null}
      excludeIds={[]}
      onCancel={() => {}}
      onConfirm={() => {}}
    />,
  );
  await userEvent.type(screen.getByPlaceholderText(/search/i), 'snow');
  expect(screen.getByText('Snowballs')).toBeInTheDocument();
  expect(screen.queryByText('Vanillas')).not.toBeInTheDocument();
});

test('confirms with selected ids', async () => {
  const onConfirm = vi.fn();
  render(
    <SourcePicker
      open
      portfolios={portfolios}
      currentPortfolioId={null}
      excludeIds={[]}
      onCancel={() => {}}
      onConfirm={onConfirm}
    />,
  );
  await userEvent.click(screen.getByLabelText(/select portfolio 1/i));
  await userEvent.click(screen.getByLabelText(/select portfolio 2/i));
  await userEvent.click(screen.getByRole('button', { name: /add 2/i }));
  expect(onConfirm).toHaveBeenCalledWith([1, 2]);
});

test('does not render when closed', () => {
  render(
    <SourcePicker
      open={false}
      portfolios={portfolios}
      currentPortfolioId={null}
      excludeIds={[]}
      onCancel={() => {}}
      onConfirm={() => {}}
    />,
  );
  expect(screen.queryByPlaceholderText(/search/i)).not.toBeInTheDocument();
});

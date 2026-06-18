import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { test, expect, vi } from 'vitest';
import { PositionPicker } from './PositionPicker';

const rows = [
  { id: 1, underlying: 'AAPL', product_type: 'Snowball' },
  { id: 2, underlying: 'TSLA', product_type: 'Phoenix' },
  { id: 3, underlying: 'NVDA', product_type: 'Snowball' },
];

test('filters by search', async () => {
  render(
    <PositionPicker
      open
      positions={rows}
      excludeIds={[]}
      onCancel={() => {}}
      onConfirm={() => {}}
    />,
  );
  await userEvent.type(screen.getByPlaceholderText(/search/i), 'AAPL');
  expect(screen.getByText('AAPL')).toBeInTheDocument();
  expect(screen.queryByText('TSLA')).not.toBeInTheDocument();
});

test('hides positions in excludeIds', () => {
  render(
    <PositionPicker
      open
      positions={rows}
      excludeIds={[2]}
      onCancel={() => {}}
      onConfirm={() => {}}
    />,
  );
  expect(screen.getByText('AAPL')).toBeInTheDocument();
  expect(screen.queryByText('TSLA')).not.toBeInTheDocument();
  expect(screen.getByText('NVDA')).toBeInTheDocument();
});

test('confirms with selected ids', async () => {
  const onConfirm = vi.fn();
  render(
    <PositionPicker
      open
      positions={rows}
      excludeIds={[]}
      onCancel={() => {}}
      onConfirm={onConfirm}
    />,
  );
  await userEvent.click(screen.getByLabelText(/select position 1/i));
  await userEvent.click(screen.getByLabelText(/select position 3/i));
  await userEvent.click(screen.getByRole('button', { name: /add 2/i }));
  expect(onConfirm).toHaveBeenCalledWith([1, 3]);
});

test('does not render when closed', () => {
  render(
    <PositionPicker
      open={false}
      positions={rows}
      excludeIds={[]}
      onCancel={() => {}}
      onConfirm={() => {}}
    />,
  );
  expect(screen.queryByPlaceholderText(/search/i)).not.toBeInTheDocument();
});

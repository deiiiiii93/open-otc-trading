import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { test, expect, vi } from 'vitest';
import { PortfolioDeleteDialog } from './PortfolioDeleteDialog';

test('does not render when closed', () => {
  render(
    <PortfolioDeleteDialog
      open={false}
      portfolioName="Snowballs"
      onCancel={() => {}}
      onConfirm={() => {}}
    />,
  );
  expect(screen.queryByText(/snowballs/i)).not.toBeInTheDocument();
});

test('echoes portfolio name in body', () => {
  render(
    <PortfolioDeleteDialog
      open
      portfolioName="Snowballs"
      onCancel={() => {}}
      onConfirm={() => {}}
    />,
  );
  expect(screen.getByText(/snowballs/i)).toBeInTheDocument();
});

test('confirm button calls onConfirm', async () => {
  const onConfirm = vi.fn();
  render(
    <PortfolioDeleteDialog
      open
      portfolioName="Snowballs"
      onCancel={() => {}}
      onConfirm={onConfirm}
    />,
  );
  await userEvent.click(screen.getByRole('button', { name: /^delete$/i }));
  expect(onConfirm).toHaveBeenCalledTimes(1);
});

test('cancel button calls onCancel', async () => {
  const onCancel = vi.fn();
  render(
    <PortfolioDeleteDialog
      open
      portfolioName="Snowballs"
      onCancel={onCancel}
      onConfirm={() => {}}
    />,
  );
  await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
  expect(onCancel).toHaveBeenCalledTimes(1);
});

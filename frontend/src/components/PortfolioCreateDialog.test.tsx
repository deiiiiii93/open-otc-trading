import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { test, expect, vi } from 'vitest';
import { PortfolioCreateDialog } from './PortfolioCreateDialog';

test('does not render when closed', () => {
  render(
    <PortfolioCreateDialog
      open={false}
      kind="view"
      onCancel={() => {}}
      onCreate={() => {}}
    />,
  );
  expect(screen.queryByLabelText(/name/i)).not.toBeInTheDocument();
});

test('renders title for the chosen kind', () => {
  render(
    <PortfolioCreateDialog
      open
      kind="view"
      onCancel={() => {}}
      onCreate={() => {}}
    />,
  );
  expect(screen.getByRole('dialog', { name: /new view portfolio/i })).toBeInTheDocument();
});

test('disables Create until name is non-empty', async () => {
  render(
    <PortfolioCreateDialog
      open
      kind="container"
      onCancel={() => {}}
      onCreate={() => {}}
    />,
  );
  const createButton = screen.getByRole('button', { name: /^create$/i });
  expect(createButton).toBeDisabled();
  await userEvent.type(screen.getByLabelText(/name/i), 'Snowballs 2026Q2');
  expect(createButton).toBeEnabled();
});

test('calls onCreate with trimmed name and kind', async () => {
  const onCreate = vi.fn();
  render(
    <PortfolioCreateDialog
      open
      kind="view"
      onCancel={() => {}}
      onCreate={onCreate}
    />,
  );
  await userEvent.type(screen.getByLabelText(/name/i), '  Hello  ');
  await userEvent.click(screen.getByRole('button', { name: /^create$/i }));
  expect(onCreate).toHaveBeenCalledWith({ name: 'Hello', kind: 'view' });
});

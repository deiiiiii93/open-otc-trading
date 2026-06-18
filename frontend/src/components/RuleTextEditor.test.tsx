import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { test, expect, vi } from 'vitest';
import { RuleTextEditor } from './RuleTextEditor';

test('emits canonical tree from valid DSL', async () => {
  const onChange = vi.fn();
  render(<RuleTextEditor rule={null} onChange={onChange} />);
  await userEvent.type(screen.getByRole('textbox'), 'product_type = Snowball');
  expect(onChange).toHaveBeenLastCalledWith(
    { op: 'eq', field: 'product_type', value: 'Snowball' },
    null,
  );
});

test('reports parse error for invalid DSL', async () => {
  render(<RuleTextEditor rule={null} onChange={() => {}} />);
  await userEvent.type(screen.getByRole('textbox'), 'product_type ==== Snowball');
  expect(screen.getByText(/syntax/i)).toBeInTheDocument();
});

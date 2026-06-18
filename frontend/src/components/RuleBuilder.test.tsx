import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { test, expect, vi } from 'vitest';
import { RuleBuilder } from './RuleBuilder';

test('emits canonical eq tree when a single condition is edited', async () => {
  const onChange = vi.fn();
  render(<RuleBuilder
    rule={{ op: 'and', children: [{ op: 'eq', field: 'product_type', value: '' }] }}
    onChange={onChange}
  />,
  );
  await userEvent.type(screen.getAllByLabelText(/value/i)[0], 'Snowball');
  expect(onChange).toHaveBeenLastCalledWith({
    op: 'and',
    children: [{ op: 'eq', field: 'product_type', value: 'Snowball' }],
  });
});

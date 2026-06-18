import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { test, expect } from 'vitest';
import { TagEditor } from './TagEditor';

function Harness({ initial = [] as string[] }) {
  const [tags, setTags] = useState(initial);
  return <TagEditor tags={tags} onChange={setTags} />;
}

test('adds and lower-cases tag', async () => {
  render(<Harness />);
  await userEvent.type(screen.getByPlaceholderText(/add tag/i), 'Alpha{enter}');
  expect(screen.getByText('alpha')).toBeInTheDocument();
});

test('rejects duplicates', async () => {
  render(<Harness initial={['alpha']} />);
  await userEvent.type(screen.getByPlaceholderText(/add tag/i), 'alpha{enter}');
  expect(screen.getAllByText('alpha')).toHaveLength(1);
});

test('removes tag', async () => {
  render(<Harness initial={['alpha', 'beta']} />);
  await userEvent.click(screen.getByLabelText(/remove alpha/i));
  expect(screen.queryByText('alpha')).not.toBeInTheDocument();
});

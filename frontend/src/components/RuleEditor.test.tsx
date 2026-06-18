import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { test, expect } from 'vitest';
import { RuleEditor } from './RuleEditor';

test('starts in builder mode and toggles to text', async () => {
  render(<RuleEditor rule={null} onChange={() => {}} />);
  expect(screen.getByRole('button', { name: /text/i })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /text/i }));
  expect(screen.getByRole('textbox')).toBeInTheDocument();
});

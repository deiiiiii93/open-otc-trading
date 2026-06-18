import { test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { KindChip } from './KindChip';

test('renders container chip with container variant class', () => {
  render(<KindChip kind="container" />);
  const chip = screen.getByText(/container/i);
  expect(chip).toHaveClass('wl-kindchip');
  expect(chip).toHaveClass('wl-kindchip--container');
});

test('renders view chip with view variant class', () => {
  render(<KindChip kind="view" />);
  const chip = screen.getByText(/view/i);
  expect(chip).toHaveClass('wl-kindchip');
  expect(chip).toHaveClass('wl-kindchip--view');
});

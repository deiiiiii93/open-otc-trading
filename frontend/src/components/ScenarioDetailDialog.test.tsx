import { render, screen } from '@testing-library/react';
import { test, expect } from 'vitest';
import { ScenarioDetailDialog } from './ScenarioDetailDialog';
import type { ScenarioStress } from '../types';

const stresses: ScenarioStress[] = [
  { param: 'spot', stress_type: 'PERCENTAGE', value: -0.2, level: 'portfolio', target: null },
  { param: 'vol', stress_type: 'PERCENTAGE', value: 0.5, level: 'underlying', target: '000300.SH' },
];

test('does not render when closed', () => {
  render(<ScenarioDetailDialog open={false} name="Market Crash" stresses={stresses} onClose={() => {}} />);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('shows all four params; unchanged where no leg', () => {
  render(<ScenarioDetailDialog open name="Market Crash" description="d" stresses={stresses} onClose={() => {}} />);
  expect(screen.getByRole('dialog', { name: /market crash/i })).toBeInTheDocument();
  expect(screen.getByText('Spot')).toBeInTheDocument();
  expect(screen.getByText('Rate')).toBeInTheDocument();
  expect(screen.getAllByText(/unchanged/i)).toHaveLength(2);
  expect(screen.getByText(/000300\.SH/)).toBeInTheDocument();
  expect(screen.getByText('-20%')).toBeInTheDocument();
  expect(screen.getByText('+50%')).toBeInTheDocument();
});

test('renders multiple legs on the same param', () => {
  const multi: ScenarioStress[] = [
    { param: 'spot', stress_type: 'PERCENTAGE', value: -0.1, level: 'underlying', target: 'AAA' },
    { param: 'spot', stress_type: 'PERCENTAGE', value: -0.3, level: 'underlying', target: 'BBB' },
  ];
  render(<ScenarioDetailDialog open name="Multi" stresses={multi} onClose={() => {}} />);
  expect(screen.getByText(/AAA/)).toBeInTheDocument();
  expect(screen.getByText(/BBB/)).toBeInTheDocument();
  expect(screen.getByText('-10%')).toBeInTheDocument();
  expect(screen.getByText('-30%')).toBeInTheDocument();
});

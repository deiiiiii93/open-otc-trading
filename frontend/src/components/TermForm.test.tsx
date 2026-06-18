import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TermForm } from './TermForm';
import type { TermFormMeta } from '../types';

const form: TermFormMeta = {
  title: 'Finish booking',
  subtitle: 'Portfolio 6',
  submit_label: 'Review & book',
  fields: [
    { key: 'initial_price', label: 'Initial fixing S0', type: 'number',
      default: { label: 'spot 8359.56', value: 8359.56 } },
    { key: 'observation_frequency', label: 'Frequency', type: 'enum',
      choices: [{ label: 'Monthly', value: 'MONTHLY' }, { label: 'Quarterly', value: 'QUARTERLY' }] },
  ],
};

describe('TermForm', () => {
  it('renders title, fields, and a default chip preselected', () => {
    render(<TermForm form={form} onSubmit={vi.fn()} />);
    expect(screen.getByText('Finish booking')).toBeInTheDocument();
    expect(screen.getByText('Initial fixing S0')).toBeInTheDocument();
    expect(screen.getByLabelText('Initial fixing S0')).toHaveValue(8359.56);
  });

  it('blocks submit and shows an error when a required field is empty', () => {
    const onSubmit = vi.fn();
    render(<TermForm form={form} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole('button', { name: /review & book/i }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText('Required')).toBeInTheDocument();
  });

  it('submits a composed string once all fields are valid', () => {
    const onSubmit = vi.fn();
    render(<TermForm form={form} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole('button', { name: /monthly/i }));
    fireEvent.click(screen.getByRole('button', { name: /review & book/i }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const msg = onSubmit.mock.calls[0][0] as string;
    expect(msg).toContain('```json');
    expect(msg).toContain('"observation_frequency": "MONTHLY"');
    expect(msg).toContain('"initial_price": 8359.56');
  });
});

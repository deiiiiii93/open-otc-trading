import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { test, expect, vi } from 'vitest';
import { ScenarioBuilderDialog } from './ScenarioBuilderDialog';

test('rejects a duplicate name on create', async () => {
  const onSave = vi.fn();
  render(<ScenarioBuilderDialog open existingNames={['Taken']} onSave={onSave} onClose={() => {}} />);
  await userEvent.type(screen.getByLabelText(/scenario name/i), 'Taken');
  await userEvent.type(screen.getByLabelText(/^value 0$/i), '-10');
  await userEvent.click(screen.getByRole('button', { name: /^save$/i }));
  expect(onSave).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(/already exists/i);
});

test('requires a target when level is underlying', async () => {
  const onSave = vi.fn();
  render(<ScenarioBuilderDialog open existingNames={[]} onSave={onSave} onClose={() => {}} />);
  await userEvent.type(screen.getByLabelText(/scenario name/i), 'U');
  await userEvent.type(screen.getByLabelText(/^value 0$/i), '5');
  await userEvent.selectOptions(screen.getByLabelText(/^level 0$/i), 'underlying');
  await userEvent.click(screen.getByRole('button', { name: /^save$/i }));
  expect(onSave).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(/target symbol/i);
});

test('saves a valid scenario with cleaned legs', async () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(<ScenarioBuilderDialog open existingNames={[]} onSave={onSave} onClose={() => {}} />);
  await userEvent.type(screen.getByLabelText(/scenario name/i), '  Mild  ');
  await userEvent.type(screen.getByLabelText(/^value 0$/i), '-10');
  await userEvent.click(screen.getByRole('button', { name: /^save$/i }));
  expect(onSave).toHaveBeenCalledWith(
    'Mild',
    '',
    [{ param: 'spot', stress_type: 'PERCENTAGE', value: -0.1, level: 'portfolio', target: null }],
  );
});

test('rejects a name that collides after sanitization', async () => {
  const onSave = vi.fn();
  render(<ScenarioBuilderDialog open existingNames={['My_Shock']} onSave={onSave} onClose={() => {}} />);
  await userEvent.type(screen.getByLabelText(/scenario name/i), 'My Shock');
  await userEvent.type(screen.getByLabelText(/^value 0$/i), '-10');
  await userEvent.click(screen.getByRole('button', { name: /^save$/i }));
  expect(onSave).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(/already exists/i);
});

test('edit mode preloads initial and locks the name', () => {
  render(
    <ScenarioBuilderDialog
      open
      initial={{ name: 'Existing', description: 'd',
        stresses: [{ param: 'vol', stress_type: 'PERCENTAGE', value: 30, level: 'portfolio', target: null }] }}
      existingNames={['Existing']}
      onSave={() => {}}
      onClose={() => {}}
    />,
  );
  const nameInput = screen.getByLabelText(/scenario name/i) as HTMLInputElement;
  expect(nameInput.value).toBe('Existing');
  expect(nameInput).toBeDisabled();
});

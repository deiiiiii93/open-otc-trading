import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TemplateEditor } from './TemplateEditor';
import type { ReportTemplate } from '../../types';

const TEMPLATE: ReportTemplate = {
  slug: 'demo',
  title: 'Demo',
  persona: 'trader',
  description: '',
  source: 'user',
  version: 1,
  spec: 'meta:\n  slug: demo\n',
};

describe('TemplateEditor', () => {
  it('shows the current spec', () => {
    render(
      <TemplateEditor
        template={TEMPLATE}
        onSave={vi.fn()}
        onValidate={vi.fn().mockResolvedValue({ ok: true, errors: [] })}
      />,
    );
    expect(screen.getByRole('textbox')).toHaveValue('meta:\n  slug: demo\n');
  });

  it('surfaces validation errors without saving', async () => {
    const onValidate = vi.fn().mockResolvedValue({
      ok: false,
      errors: ["sections[0].blocks[0].key 'risk.nope' is not a registered block"],
    });
    const onSave = vi.fn();
    render(<TemplateEditor template={TEMPLATE} onSave={onSave} onValidate={onValidate} />);
    fireEvent.click(screen.getByRole('button', { name: /validate/i }));
    await waitFor(() =>
      expect(screen.getByTestId('template-errors')).toHaveTextContent(/risk\.nope/),
    );
    expect(onSave).not.toHaveBeenCalled();
  });

  it('disables save for a seeded template', () => {
    render(
      <TemplateEditor
        template={{ ...TEMPLATE, source: 'seed' }}
        onSave={vi.fn()}
        onValidate={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: /save/i })).toBeDisabled();
  });

  it('saves an edited spec', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <TemplateEditor
        template={TEMPLATE}
        onSave={onSave}
        onValidate={vi.fn().mockResolvedValue({ ok: true, errors: [] })}
      />,
    );
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'meta:\n  slug: demo2\n' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith('demo', 'meta:\n  slug: demo2\n'));
  });
});

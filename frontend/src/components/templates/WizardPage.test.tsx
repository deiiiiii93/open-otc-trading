import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { WizardPage } from './WizardPage';

describe('WizardPage', () => {
  it('renders steps, body, and footer', () => {
    render(
      <WizardPage
        title="TRY SOLVE"
        steps={[{ label: 'Schema', status: 'done' }, { label: 'Solve', status: 'active' }]}
        footer={<button>Submit</button>}
      >
        <div>step-body</div>
      </WizardPage>,
    );
    expect(screen.getByText('Schema')).toBeInTheDocument();
    expect(screen.getByText('step-body')).toBeInTheDocument();
    expect(screen.getByText('Submit')).toBeInTheDocument();
  });

  it('renders a tabs slot instead of steps when given', () => {
    render(<WizardPage title="X" tabs={<div>tabstrip</div>}><div>b</div></WizardPage>);
    expect(screen.getByText('tabstrip')).toBeInTheDocument();
  });
});

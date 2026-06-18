import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { WorkbenchPage } from './WorkbenchPage';

describe('WorkbenchPage', () => {
  it('renders config (rail), metrics, and results', () => {
    render(
      <WorkbenchPage
        title="SCENARIO TEST"
        config={<div>config-panel</div>}
        metrics={[{ label: 'RUNS', value: '5' }]}
        results={<div>runs-list</div>}
      />,
    );
    expect(screen.getByText('SCENARIO TEST')).toBeInTheDocument();
    expect(screen.getByText('config-panel')).toBeInTheDocument();
    expect(screen.getByText('RUNS')).toBeInTheDocument();
    expect(screen.getByText('runs-list')).toBeInTheDocument();
  });
});

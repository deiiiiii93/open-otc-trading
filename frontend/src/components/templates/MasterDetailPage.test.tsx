import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MasterDetailPage } from './MasterDetailPage';

describe('MasterDetailPage', () => {
  it('renders header, optional metrics, rail, and detail children', () => {
    render(
      <MasterDetailPage
        title="SKILLS"
        metrics={[{ label: 'WORKFLOWS', value: '3' }]}
        rail={<div>rail-list</div>}
        railLabel="Skills"
      >
        <div>detail-editor</div>
      </MasterDetailPage>,
    );
    expect(screen.getByText('SKILLS')).toBeInTheDocument();
    expect(screen.getByText('WORKFLOWS')).toBeInTheDocument();
    expect(screen.getByText('rail-list')).toBeInTheDocument();
    expect(screen.getByText('detail-editor')).toBeInTheDocument();
  });
});

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AssetsPane } from './AssetsPane';
import type { AgentAsset } from '../types';

const assets: AgentAsset[] = [
  { id: 'a1', kind: 'json',     title: 'pricing_request.json',  metadata: {} },
  {
    id: 'a2',
    kind: 'table',
    title: 'positions_table',
    metadata: {},
    data: {
      columns: ['source_trade_id', 'price'],
      rows: [{ source_trade_id: 'SSGK48', price: 12.34 }],
    },
  },
  { id: 'a3', kind: 'markdown', title: 'risk_notes.md',         metadata: {} },
];

describe('AssetsPane', () => {
  it('renders one card per asset', () => {
    render(<AssetsPane assets={assets} />);
    expect(screen.getByText('pricing_request.json')).toBeInTheDocument();
    expect(screen.getByText('positions_table')).toBeInTheDocument();
    expect(screen.getByText('risk_notes.md')).toBeInTheDocument();
  });

  it('renders count in header', () => {
    render(<AssetsPane assets={assets} />);
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('renders table asset rows', () => {
    render(<AssetsPane assets={assets} />);
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByText('SSGK48')).toBeInTheDocument();
    expect(screen.getByText('12.3400')).toBeInTheDocument();
  });

  it('shows empty state when no assets', () => {
    render(<AssetsPane assets={[]} />);
    expect(screen.getByText(/no assets yet/i)).toBeInTheDocument();
  });
});

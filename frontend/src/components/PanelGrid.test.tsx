import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PanelGrid } from './PanelGrid';

describe('PanelGrid', () => {
  it('renders children inside the grid', () => {
    render(<PanelGrid><div>panel-a</div><div>panel-b</div></PanelGrid>);
    expect(screen.getByText('panel-a')).toBeInTheDocument();
    expect(screen.getByText('panel-b')).toBeInTheDocument();
  });

  it('sets the column count from the columns prop', () => {
    const { container } = render(<PanelGrid columns={2}><div /></PanelGrid>);
    const grid = container.querySelector('.wl-panel-grid') as HTMLElement;
    // jsdom custom-property reads are unreliable via getPropertyValue; assert on
    // the serialized inline style instead.
    expect(grid.getAttribute('style')).toContain('--panel-cols: 2');
  });
});

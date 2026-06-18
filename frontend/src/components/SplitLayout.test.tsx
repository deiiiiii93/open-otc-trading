import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SplitLayout } from './SplitLayout';

describe('SplitLayout', () => {
  it('renders rail and workspace', () => {
    render(<SplitLayout rail={<div>rail-x</div>} railLabel="Items">workspace-x</SplitLayout>);
    expect(screen.getByText('rail-x')).toBeInTheDocument();
    expect(screen.getByText('workspace-x')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Items' })).toBeInTheDocument();
  });

  it('collapses the rail when the toggle is clicked', () => {
    const { container } = render(
      <SplitLayout collapsible rail={<div>rail-x</div>}>ws</SplitLayout>,
    );
    expect(container.querySelector('.wl-split--collapsed')).toBeNull();
    fireEvent.click(screen.getByLabelText('Collapse panel'));
    expect(container.querySelector('.wl-split--collapsed')).not.toBeNull();
  });

  it('renders the rail without a navigation landmark when railAs="div"', () => {
    render(
      <SplitLayout railAs="div" rail={<div role="tablist" aria-label="Tabs">t</div>} railLabel="Items">
        ws
      </SplitLayout>,
    );
    // No nested nav landmark — the rail content (the tablist) owns its semantics.
    expect(screen.queryByRole('navigation')).toBeNull();
    expect(screen.getByRole('tablist', { name: 'Tabs' })).toBeInTheDocument();
  });

  it('applies a railWidth override via the --wl-rail-width custom prop (no self-reference)', () => {
    const { container } = render(
      <SplitLayout rail={<div />} railWidth="var(--rail-width)">ws</SplitLayout>,
    );
    const root = container.querySelector('.wl-split') as HTMLElement;
    const style = root.getAttribute('style') || '';
    // Override writes a DISTINCT property so it can never reference itself.
    expect(style).toContain('--wl-rail-width: var(--rail-width)');
    // It must NOT define --rail-width itself (the old self-referential cycle).
    expect(style).not.toMatch(/(^|;)\s*--rail-width:/);
  });

  it('resizes the rail when the resizer is dragged', () => {
    const { container } = render(
      <SplitLayout resizable rail={<div>rail-x</div>} minRailWidth={100} maxRailWidth={300}>ws</SplitLayout>,
    );

    fireEvent.pointerDown(screen.getByRole('button', { name: /resize items/i }), { clientX: 120 });
    fireEvent.pointerMove(window, { clientX: 260 });
    fireEvent.pointerUp(window);

    const root = container.querySelector('.wl-split') as HTMLElement;
    expect(root.getAttribute('style')).toContain('--wl-rail-width: 260px');
  });
});

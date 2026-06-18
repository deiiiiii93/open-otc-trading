import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Panel } from './Panel';
import { Tile } from './Tile';

describe('Panel', () => {
  it('renders title and body', () => {
    render(
      <Panel title="Greeks" meta="LIVE">
        <div>body</div>
      </Panel>
    );
    expect(screen.getByText('Greeks')).toBeInTheDocument();
    expect(screen.getByText('LIVE')).toBeInTheDocument();
    expect(screen.getByText('body')).toBeInTheDocument();
  });

  it('omits meta when not provided', () => {
    render(<Panel title="Greeks">body</Panel>);
    expect(screen.queryByText('LIVE')).not.toBeInTheDocument();
  });
});

describe('Tile', () => {
  it('renders label and value', () => {
    render(<Tile label="NAV" value="38.2M" />);
    expect(screen.getByText('NAV')).toBeInTheDocument();
    expect(screen.getByText('38.2M')).toBeInTheDocument();
  });

  it('applies pos variant', () => {
    const { container } = render(<Tile label="P&L" value="+1.84%" variant="pos" />);
    expect(container.firstChild).toHaveClass('wl-tile--pos');
  });

  it('renders delta when provided', () => {
    render(<Tile label="NAV" value="38.2M" delta="+0.71M today" />);
    expect(screen.getByText('+0.71M today')).toBeInTheDocument();
  });
});

describe('Panel + Tile a11y', () => {
  it('Panel has no a11y violations', async () => {
    const { container } = render(<Panel title="Greeks">body</Panel>);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });

  it('Tile has no a11y violations', async () => {
    const { container } = render(<Tile label="NAV" value="38.2M" />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
});

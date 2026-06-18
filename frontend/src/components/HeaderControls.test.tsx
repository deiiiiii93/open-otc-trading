import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { HeaderControls } from './HeaderControls';

describe('HeaderControls', () => {
  it('wraps children in a header-controls row, justified end by default', () => {
    const { container } = render(
      <HeaderControls><button className="wl-button">Run</button></HeaderControls>,
    );
    const row = container.querySelector('.wl-header-controls');
    expect(row).not.toBeNull();
    expect(row?.classList.contains('wl-header-controls--end')).toBe(true);
    expect(row?.querySelector('.wl-button')?.textContent).toBe('Run');
  });

  it('supports start justification', () => {
    const { container } = render(<HeaderControls justify="start">x</HeaderControls>);
    expect(container.querySelector('.wl-header-controls--start')).not.toBeNull();
  });

  it('passes through an extra className', () => {
    const { container } = render(<HeaderControls className="extra">x</HeaderControls>);
    expect(container.querySelector('.wl-header-controls.extra')).not.toBeNull();
  });
});

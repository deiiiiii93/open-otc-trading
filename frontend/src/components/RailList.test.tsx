import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { RailList } from './RailList';

describe('RailList', () => {
  it('renders children inside a wl-rail container, no scroll by default', () => {
    const { container } = render(<RailList><button>Row</button></RailList>);
    const rail = container.querySelector('.wl-rail');
    expect(rail).not.toBeNull();
    expect(rail?.classList.contains('wl-rail--scroll')).toBe(false);
    expect(rail?.querySelector('button')?.textContent).toBe('Row');
  });

  it('adds the scroll modifier when scroll is set', () => {
    const { container } = render(<RailList scroll>x</RailList>);
    expect(container.querySelector('.wl-rail.wl-rail--scroll')).not.toBeNull();
  });

  it('passes through an extra className', () => {
    const { container } = render(<RailList className="extra">x</RailList>);
    expect(container.querySelector('.wl-rail.extra')).not.toBeNull();
  });
});

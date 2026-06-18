import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import { RailItem } from './RailItem';

describe('RailItem', () => {
  it('renders children as a button, stack layout by default, inactive', () => {
    const { container } = render(<RailItem>Label</RailItem>);
    const btn = container.querySelector('button.wl-rail__item');
    expect(btn).not.toBeNull();
    expect(btn?.classList.contains('wl-rail__item--stack')).toBe(true);
    expect(btn?.classList.contains('is-active')).toBe(false);
    expect(btn?.getAttribute('aria-current')).toBeNull();
    expect(btn?.textContent).toBe('Label');
  });

  it('marks active with is-active and aria-current', () => {
    const { container } = render(<RailItem active>x</RailItem>);
    const btn = container.querySelector('.wl-rail__item');
    expect(btn?.classList.contains('is-active')).toBe(true);
    expect(btn?.getAttribute('aria-current')).toBe('true');
  });

  it('supports the row layout', () => {
    const { container } = render(<RailItem layout="row">x</RailItem>);
    expect(container.querySelector('.wl-rail__item--row')).not.toBeNull();
  });

  it('sets the accent as an inline custom property referencing the token', () => {
    const { container } = render(<RailItem accent="--info">x</RailItem>);
    const btn = container.querySelector('.wl-rail__item') as HTMLElement;
    expect(btn.style.getPropertyValue('--rail-item-accent')).toBe('var(--info)');
  });

  it('fires onClick', () => {
    const onClick = vi.fn();
    const { container } = render(<RailItem onClick={onClick}>x</RailItem>);
    fireEvent.click(container.querySelector('.wl-rail__item')!);
    expect(onClick).toHaveBeenCalledOnce();
  });
});

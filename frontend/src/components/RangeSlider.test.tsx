import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { RangeSlider } from './RangeSlider';

describe('RangeSlider', () => {
  it('renders a single-thumb slider with an accessible role', () => {
    render(<RangeSlider min={0} max={10} mode="single" values={[3]} onChange={vi.fn()} />);

    const thumb = screen.getByRole('slider');
    expect(thumb).toHaveAttribute('aria-valuemin', '0');
    expect(thumb).toHaveAttribute('aria-valuemax', '10');
    expect(thumb).toHaveAttribute('aria-valuenow', '3');
  });

  it('renders a dual-thumb range slider with two thumbs', () => {
    render(<RangeSlider min={0} max={10} mode="range" values={[2, 8]} onChange={vi.fn()} />);

    const thumbs = screen.getAllByRole('slider');
    expect(thumbs).toHaveLength(2);
    expect(thumbs[0]).toHaveAttribute('aria-valuenow', '2');
    expect(thumbs[1]).toHaveAttribute('aria-valuenow', '8');
    expect(thumbs[0]).toHaveAttribute('aria-label', 'Lower bound');
    expect(thumbs[1]).toHaveAttribute('aria-label', 'Upper bound');
  });

  it('emits a new value when a thumb receives arrow key input', () => {
    const onChange = vi.fn();
    render(<RangeSlider min={0} max={10} step={1} mode="single" values={[3]} onChange={onChange} />);

    const thumb = screen.getByRole('slider');
    fireEvent.keyDown(thumb, { key: 'ArrowRight' });

    expect(onChange).toHaveBeenCalledWith([4]);
  });

  it('displays the current formatted values next to the label', () => {
    render(<RangeSlider min={0} max={10} mode="range" values={[2, 8]} onChange={vi.fn()} label="Bounds" />);

    expect(screen.getByText('Bounds')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('8')).toBeInTheDocument();
  });

  it('clamps values outside the min/max range', () => {
    const onChange = vi.fn();
    render(<RangeSlider min={0} max={10} mode="single" values={[-5]} onChange={onChange} />);

    expect(screen.getByRole('slider')).toHaveAttribute('aria-valuenow', '0');
  });
});

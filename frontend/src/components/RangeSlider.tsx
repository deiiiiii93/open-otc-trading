import { useCallback, useId, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import './RangeSlider.css';

export type RangeSliderMode = 'single' | 'range';

export type RangeSliderProps = {
  min: number;
  max: number;
  step?: number;
  mode: RangeSliderMode;
  values: number[];
  onChange: (values: number[]) => void;
  disabled?: boolean;
  label?: string;
  formatValue?: (value: number) => string;
};

export function RangeSlider({
  min,
  max,
  step = 0.01,
  mode,
  values,
  onChange,
  disabled = false,
  label,
  formatValue = defaultFormat,
}: RangeSliderProps) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const [draggingIndex, setDraggingIndex] = useState<number | null>(null);
  const labelId = useId();

  const safeValues = normalizeValues(values, mode, min, max);
  const thumbCount = mode === 'range' ? 2 : 1;

  const valueToPercent = useCallback((value: number) => {
    if (max <= min) return 0;
    return Math.min(100, Math.max(0, ((value - min) / (max - min)) * 100));
  }, [min, max]);

  const percentToValue = useCallback((percent: number) => {
    const raw = min + (percent / 100) * (max - min);
    const stepped = Math.round(raw / step) * step;
    return clamp(stepped, min, max);
  }, [min, max, step]);

  const handleTrackPointer = useCallback((clientX: number) => {
    if (!trackRef.current || disabled) return;
    const rect = trackRef.current.getBoundingClientRect();
    const percent = ((clientX - rect.left) / rect.width) * 100;
    const nextValue = percentToValue(percent);

    if (mode === 'single') {
      onChange([nextValue]);
      return;
    }

    const [lower, upper] = safeValues;
    const lowerDistance = Math.abs(nextValue - lower);
    const upperDistance = Math.abs(nextValue - upper);
    const targetIndex = lowerDistance <= upperDistance ? 0 : 1;

    if (targetIndex === 0) {
      onChange([Math.min(nextValue, upper), upper]);
    } else {
      onChange([lower, Math.max(nextValue, lower)]);
    }
  }, [disabled, mode, onChange, percentToValue, safeValues]);

  const handleThumbPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>, index: number) => {
    if (disabled) return;
    event.preventDefault();
    event.stopPropagation();
    setDraggingIndex(index);

    const handlePointerMove = (moveEvent: PointerEvent) => {
      handleTrackPointer(moveEvent.clientX);
    };

    const handlePointerUp = () => {
      setDraggingIndex(null);
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
    };

    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp, { once: true });
  }, [disabled, handleTrackPointer]);

  const handleTrackClick = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (disabled || draggingIndex != null) return;
    handleTrackPointer(event.clientX);
  }, [disabled, draggingIndex, handleTrackPointer]);

  const handleKeyChange = (index: number, delta: number) => {
    if (disabled) return;
    const nextValues = [...safeValues];
    nextValues[index] = clamp(Math.round((nextValues[index] + delta) / step) * step, min, max);
    if (mode === 'range') {
      if (index === 0) nextValues[0] = Math.min(nextValues[0], nextValues[1]);
      if (index === 1) nextValues[1] = Math.max(nextValues[1], nextValues[0]);
    }
    onChange(nextValues);
  };

  const fillStyle = mode === 'range'
    ? { left: `${valueToPercent(safeValues[0])}%`, right: `${100 - valueToPercent(safeValues[1])}%` }
    : { left: '0%', right: `${100 - valueToPercent(safeValues[0])}%` };

  return (
    <div
      className="wl-range-slider"
      role="group"
      aria-labelledby={label ? labelId : undefined}
      aria-disabled={disabled}
    >
      {label && (
        <div className="wl-range-slider__header">
          <span id={labelId} className="wl-range-slider__label">{label}</span>
          <span className="wl-range-slider__values">
            {safeValues.map((value, index) => (
              <span key={index} className="wl-range-slider__value">{formatValue(value)}</span>
            ))}
          </span>
        </div>
      )}
      <div className="wl-range-slider__track-wrap">
        <div
          ref={trackRef}
          className="wl-range-slider__track"
          onPointerDown={handleTrackClick}
          tabIndex={-1}
        >
          <div className="wl-range-slider__track-bg" />
          <div className="wl-range-slider__fill" style={fillStyle} />
          {Array.from({ length: thumbCount }).map((_, index) => {
            const value = safeValues[index];
            const percent = valueToPercent(value);
            return (
              <div
                key={index}
                className="wl-range-slider__thumb"
                style={{ left: `${percent}%` }}
                onPointerDown={(event) => handleThumbPointerDown(event, index)}
                tabIndex={disabled ? -1 : 0}
                role="slider"
                aria-valuemin={min}
                aria-valuemax={max}
                aria-valuenow={value}
                aria-label={mode === 'range' ? (index === 0 ? 'Lower bound' : 'Upper bound') : 'Value'}
                aria-disabled={disabled}
                onKeyDown={(event) => {
                  if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') {
                    event.preventDefault();
                    handleKeyChange(index, -step);
                  } else if (event.key === 'ArrowRight' || event.key === 'ArrowUp') {
                    event.preventDefault();
                    handleKeyChange(index, step);
                  } else if (event.key === 'Home') {
                    event.preventDefault();
                    handleKeyChange(index, min - value);
                  } else if (event.key === 'End') {
                    event.preventDefault();
                    handleKeyChange(index, max - value);
                  }
                }}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}

function normalizeValues(values: number[], mode: RangeSliderMode, min: number, max: number): number[] {
  if (mode === 'single') {
    return [clamp(Number.isFinite(values[0]) ? values[0] : min, min, max)];
  }
  const lower = clamp(Number.isFinite(values[0]) ? values[0] : min, min, max);
  const upper = clamp(Number.isFinite(values[1]) ? values[1] : max, min, max);
  return [Math.min(lower, upper), Math.max(lower, upper)];
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function defaultFormat(value: number): string {
  return Number.isInteger(value) ? value.toString() : value.toFixed(2);
}

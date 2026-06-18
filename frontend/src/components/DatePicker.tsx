import React, { useCallback, useId, useMemo, useRef, useState } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { CalendarDays, ChevronUp, ChevronDown } from 'lucide-react';
import './DatePicker.css';

/* ─── Types ──────────────────────────────────────────────────────── */

export type DatePickerProps = {
  /** ISO date string (YYYY-MM-DD) */
  value?: string;
  /** Callback with ISO date string or empty string when cleared */
  onChange?: (iso: string) => void;
  /** Field label (uppercase micro-caps) */
  label?: string;
  /** Helper text */
  hint?: string;
  /** Error message (replaces hint, colors border red) */
  error?: string;
  /** Placeholder when no value is set */
  placeholder?: string;
  /** Minimum selectable date (ISO) */
  min?: string;
  /** Maximum selectable date (ISO) */
  max?: string;
  /** Disable the control */
  disabled?: boolean;
  /** HTML id override */
  id?: string;
  /** Additional className on root */
  className?: string;
};

/* ─── Helpers ────────────────────────────────────────────────────── */

const WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];

function toIso(y: number, m: number, d: number): string {
  return `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}

function parseIso(iso: string): { year: number; month: number; day: number } | null {
  const parts = iso.split('-');
  if (parts.length !== 3) return null;
  const year = Number(parts[0]);
  const month = Number(parts[1]) - 1;
  const day = Number(parts[2]);
  if (isNaN(year) || isNaN(month) || isNaN(day)) return null;
  return { year, month, day };
}

/** Check if a string looks like a complete YYYY-MM-DD date */
function isValidIso(s: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  const parsed = parseIso(s);
  if (!parsed) return false;
  // Verify day is valid for the month
  const daysInMonth = new Date(parsed.year, parsed.month + 1, 0).getDate();
  return parsed.day >= 1 && parsed.day <= daysInMonth;
}

function getMonthLabel(year: number, month: number): string {
  const date = new Date(year, month, 1);
  return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short' });
}

type DayCell = {
  day: number;
  month: number;
  year: number;
  outside: boolean;
};

function buildCalendarGrid(year: number, month: number): DayCell[] {
  const firstDay = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const daysInPrev = new Date(year, month, 0).getDate();

  const cells: DayCell[] = [];

  // Previous month trailing days
  for (let i = firstDay - 1; i >= 0; i--) {
    const d = daysInPrev - i;
    const prevMonth = month === 0 ? 11 : month - 1;
    const prevYear = month === 0 ? year - 1 : year;
    cells.push({ day: d, month: prevMonth, year: prevYear, outside: true });
  }

  // Current month days
  for (let d = 1; d <= daysInMonth; d++) {
    cells.push({ day: d, month, year, outside: false });
  }

  // Next month leading days (fill to 6 rows = 42 cells)
  const remaining = 42 - cells.length;
  for (let d = 1; d <= remaining; d++) {
    const nextMonth = month === 11 ? 0 : month + 1;
    const nextYear = month === 11 ? year + 1 : year;
    cells.push({ day: d, month: nextMonth, year: nextYear, outside: true });
  }

  return cells;
}

function isSameDay(a: DayCell, b: { year: number; month: number; day: number }): boolean {
  return a.year === b.year && a.month === b.month && a.day === b.day;
}

/* ─── Component ──────────────────────────────────────────────────── */

export function DatePicker({
  value,
  onChange,
  label,
  hint,
  error,
  placeholder = 'YYYY-MM-DD',
  min,
  max,
  disabled = false,
  id,
  className = '',
}: DatePickerProps) {
  const generatedId = useId();
  const fieldId = id ?? generatedId;
  const inputRef = useRef<HTMLInputElement>(null);

  const [open, setOpen] = useState(false);

  // Calendar view state (what month we're looking at)
  const today = useMemo(() => {
    const d = new Date();
    return { year: d.getFullYear(), month: d.getMonth(), day: d.getDate() };
  }, []);

  const parsed = value ? parseIso(value) : null;

  const [viewYear, setViewYear] = useState(parsed?.year ?? today.year);
  const [viewMonth, setViewMonth] = useState(parsed?.month ?? today.month);

  // Sync view when value changes externally and popover opens
  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      if (nextOpen) {
        const p = value ? parseIso(value) : null;
        if (p) {
          setViewYear(p.year);
          setViewMonth(p.month);
        } else {
          setViewYear(today.year);
          setViewMonth(today.month);
        }
      }
      setOpen(nextOpen);
    },
    [value, today],
  );

  const grid = useMemo(() => buildCalendarGrid(viewYear, viewMonth), [viewYear, viewMonth]);

  const goToPrevMonth = useCallback(() => {
    if (viewMonth === 0) {
      setViewYear((y) => y - 1);
      setViewMonth(11);
    } else {
      setViewMonth(viewMonth - 1);
    }
  }, [viewMonth]);

  const goToNextMonth = useCallback(() => {
    if (viewMonth === 11) {
      setViewYear((y) => y + 1);
      setViewMonth(0);
    } else {
      setViewMonth(viewMonth + 1);
    }
  }, [viewMonth]);

  const isDayDisabled = useCallback(
    (cell: DayCell): boolean => {
      const iso = toIso(cell.year, cell.month, cell.day);
      if (min && iso < min) return true;
      if (max && iso > max) return true;
      return false;
    },
    [min, max],
  );

  const selectDay = useCallback(
    (cell: DayCell) => {
      if (isDayDisabled(cell)) return;
      const iso = toIso(cell.year, cell.month, cell.day);
      onChange?.(iso);
      setOpen(false);
    },
    [onChange, isDayDisabled],
  );

  const handleClear = useCallback(() => {
    onChange?.('');
    setOpen(false);
  }, [onChange]);

  const handleToday = useCallback(() => {
    const iso = toIso(today.year, today.month, today.day);
    onChange?.(iso);
    setOpen(false);
  }, [onChange, today]);

  // Handle direct text input in the field
  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const v = e.target.value;
      // Always propagate the raw value so the controlled input updates
      onChange?.(v);
    },
    [onChange],
  );

  // On blur, validate and normalize; if invalid clear or keep raw
  const handleInputBlur = useCallback(() => {
    if (value && isValidIso(value)) {
      // value is already valid, nothing to normalize
    }
    // If it's not a valid date, keep the raw value — the parent decides error state
  }, [value]);

  // Input classes
  const inputClasses = [
    'wl-datepicker__input',
    error ? 'wl-datepicker__input--error' : '',
  ].filter(Boolean).join(' ');

  return (
    <div className={`wl-datepicker ${className}`.trim()}>
      {label && (
        <label className="wl-field__label" htmlFor={fieldId}>
          {label}
        </label>
      )}

      <Popover.Root open={open} onOpenChange={handleOpenChange}>
        <div className="wl-datepicker__trigger-wrap">
          <input
            ref={inputRef}
            id={fieldId}
            type="text"
            className={inputClasses}
            value={value ?? ''}
            onChange={handleInputChange}
            onBlur={handleInputBlur}
            placeholder={placeholder}
            disabled={disabled}
            autoComplete="off"
            aria-label={label}
          />
          <Popover.Trigger asChild disabled={disabled}>
            <button
              type="button"
              className="wl-datepicker__cal-btn"
              aria-label="Open calendar"
              tabIndex={-1}
            >
              <CalendarDays size={16} />
            </button>
          </Popover.Trigger>
        </div>

        <Popover.Portal>
          <Popover.Content
            className="wl-datepicker__panel"
            sideOffset={4}
            align="start"
            onOpenAutoFocus={(e) => e.preventDefault()}
          >
            {/* Header */}
            <div className="wl-datepicker__header">
              <span className="wl-datepicker__month-label">
                {getMonthLabel(viewYear, viewMonth)}
              </span>
              <div className="wl-datepicker__nav">
                <button
                  type="button"
                  className="wl-datepicker__nav-btn"
                  onClick={goToPrevMonth}
                  aria-label="Previous month"
                >
                  <ChevronUp size={16} />
                </button>
                <button
                  type="button"
                  className="wl-datepicker__nav-btn"
                  onClick={goToNextMonth}
                  aria-label="Next month"
                >
                  <ChevronDown size={16} />
                </button>
              </div>
            </div>

            {/* Weekday labels */}
            <div className="wl-datepicker__weekdays">
              {WEEKDAYS.map((wd) => (
                <span key={wd} className="wl-datepicker__weekday">
                  {wd}
                </span>
              ))}
            </div>

            {/* Day grid */}
            <div className="wl-datepicker__days">
              {grid.map((cell, i) => {
                const isToday = isSameDay(cell, today);
                const isSelected = parsed ? isSameDay(cell, parsed) : false;
                const isDisabled = isDayDisabled(cell);

                const dayClasses = [
                  'wl-datepicker__day',
                  cell.outside ? 'wl-datepicker__day--outside' : '',
                  isToday && !isSelected ? 'wl-datepicker__day--today' : '',
                  isSelected ? 'wl-datepicker__day--selected' : '',
                  isDisabled ? 'wl-datepicker__day--disabled' : '',
                ].filter(Boolean).join(' ');

                return (
                  <button
                    key={i}
                    type="button"
                    className={dayClasses}
                    onClick={() => selectDay(cell)}
                    disabled={isDisabled}
                    tabIndex={cell.outside ? -1 : 0}
                    aria-label={toIso(cell.year, cell.month, cell.day)}
                  >
                    {cell.day}
                  </button>
                );
              })}
            </div>

            {/* Footer */}
            <div className="wl-datepicker__footer">
              <button
                type="button"
                className="wl-datepicker__footer-btn"
                onClick={handleClear}
              >
                Clear
              </button>
              <button
                type="button"
                className="wl-datepicker__footer-btn"
                onClick={handleToday}
              >
                Today
              </button>
            </div>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>

      {error ? (
        <div className="wl-field__hint wl-field__hint--error">{error}</div>
      ) : hint ? (
        <div className="wl-field__hint">{hint}</div>
      ) : null}
    </div>
  );
}

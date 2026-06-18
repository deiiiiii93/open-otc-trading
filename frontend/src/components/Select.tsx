import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { Check, ChevronDown, Search } from 'lucide-react';
import './Select.css';

export type SelectOption = {
  value: string;
  label: string;
  disabled?: boolean;
};

export type SelectProps = {
  value?: string;
  onChange?: (value: string) => void;
  options: SelectOption[];
  label?: string;
  hint?: string;
  error?: string;
  placeholder?: string;
  disabled?: boolean;
  id?: string;
  className?: string;
  variant?: 'default' | 'inline';
  searchable?: boolean;
};

export function Select({
  value,
  onChange,
  options,
  label,
  hint,
  error,
  placeholder = '—',
  disabled = false,
  id,
  className = '',
  variant = 'default',
  searchable = false,
}: SelectProps) {
  const generatedId = useId();
  const fieldId = id ?? generatedId;
  const listRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const [open, setOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');

  const filteredOptions = useMemo(() => {
    if (!searchable || !searchTerm.trim()) return options;
    const lower = searchTerm.toLowerCase().trim();
    return options.filter((opt) => opt.label.toLowerCase().includes(lower));
  }, [options, searchTerm, searchable]);

  const selectedOption = useMemo(
    () => options.find((opt) => opt.value === value),
    [options, value],
  );

  const handleSelect = useCallback(
    (optValue: string) => {
      onChange?.(optValue);
      setOpen(false);
      setSearchTerm('');
    },
    [onChange],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (disabled) return;
      if (!open && (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown')) {
        e.preventDefault();
        setOpen(true);
        return;
      }
    },
    [disabled, open],
  );

  const handleSearchKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        setOpen(false);
        setSearchTerm('');
        return;
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        const first = listRef.current?.querySelector<HTMLButtonElement>('[role="option"]:not([aria-disabled="true"])');
        first?.focus();
        return;
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        const firstEnabled = filteredOptions.find((opt) => !opt.disabled);
        if (firstEnabled) {
          handleSelect(firstEnabled.value);
        }
      }
    },
    [filteredOptions, handleSelect],
  );

  const handleOptionKeyDown = useCallback(
    (e: React.KeyboardEvent, optValue: string, index: number) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        handleSelect(optValue);
        return;
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        const next = listRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]:not([aria-disabled="true"])');
        if (next) {
          for (let i = 0; i < next.length; i++) {
            if (next[i] === e.currentTarget && next[i + 1]) {
              next[i + 1].focus();
              break;
            }
          }
        }
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        const items = listRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]:not([aria-disabled="true"])');
        if (items) {
          for (let i = 0; i < items.length; i++) {
            if (items[i] === e.currentTarget && items[i - 1]) {
              items[i - 1].focus();
              break;
            }
            if (items[i] === e.currentTarget && i === 0 && searchable) {
              searchInputRef.current?.focus();
              break;
            }
          }
        }
      }
      if (e.key === 'Escape') {
        setOpen(false);
        setSearchTerm('');
      }
    },
    [handleSelect, searchable],
  );

  const handleOpenAutoFocus = useCallback(
    (e: Event) => {
      e.preventDefault();
      requestAnimationFrame(() => {
        if (searchable && searchInputRef.current) {
          searchInputRef.current.focus();
          return;
        }
        const selected = listRef.current?.querySelector<HTMLButtonElement>('[aria-selected="true"]');
        if (selected) {
          selected.focus();
        } else {
          const first = listRef.current?.querySelector<HTMLButtonElement>('[role="option"]:not([aria-disabled="true"])');
          first?.focus();
        }
      });
    },
    [searchable],
  );

  useEffect(() => {
    if (!open) return;
    setSearchTerm('');
    requestAnimationFrame(() => {
      if (searchable && searchInputRef.current) {
        searchInputRef.current.focus();
      }
    });
  }, [open, searchable]);

  const triggerClasses = [
    'wl-select__trigger',
    error ? 'wl-select__trigger--error' : '',
  ].filter(Boolean).join(' ');

  const handleNativeChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      onChange?.(e.target.value);
    },
    [onChange],
  );

  const rootClasses = [
    'wl-select',
    variant === 'inline' ? 'wl-select--inline' : '',
    searchable ? 'wl-select--searchable' : '',
    className,
  ].filter(Boolean).join(' ');

  return (
    <div className={rootClasses}>
      {label && (
        <label className="wl-field__label" htmlFor={fieldId}>
          {label}
        </label>
      )}

      <select
        id={fieldId}
        aria-label={label}
        value={value ?? ''}
        onChange={handleNativeChange}
        disabled={disabled}
        className="wl-select__hidden"
        tabIndex={-1}
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
      </select>

      <Popover.Root open={open} onOpenChange={(v) => { setOpen(v); if (!v) setSearchTerm(''); }}>
        <Popover.Trigger asChild disabled={disabled}>
          <button
            type="button"
            className={triggerClasses}
            onKeyDown={handleKeyDown}
            aria-haspopup="listbox"
            aria-expanded={open}
            aria-label="toggle"
          >
            <span
              className={`wl-select__trigger-text${!selectedOption ? ' wl-select__trigger-text--placeholder' : ''}`}
              aria-hidden="true"
            >
              {selectedOption ? selectedOption.label : placeholder}
            </span>
            <ChevronDown size={14} className="wl-select__chevron" aria-hidden="true" />
          </button>
        </Popover.Trigger>

        <Popover.Portal>
          <Popover.Content
            className="wl-select__panel"
            sideOffset={4}
            align="start"
            role="listbox"
            aria-label={label}
            ref={listRef}
            onOpenAutoFocus={handleOpenAutoFocus}
          >
            {searchable && (
              <div className="wl-select__search">
                <Search size={14} className="wl-select__search-icon" aria-hidden="true" />
                <input
                  ref={searchInputRef}
                  type="text"
                  className="wl-select__search-input"
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  onKeyDown={handleSearchKeyDown}
                  placeholder="Filter…"
                  aria-label="Filter options"
                  autoComplete="off"
                />
              </div>
            )}
            {filteredOptions.length === 0 ? (
              <div className="wl-select__empty">No results</div>
            ) : (
              filteredOptions.map((opt, i) => {
                const isSelected = opt.value === value;
                const optClasses = [
                  'wl-select__option',
                  isSelected ? 'wl-select__option--selected' : '',
                  opt.disabled ? 'wl-select__option--disabled' : '',
                ].filter(Boolean).join(' ');

                return (
                  <button
                    key={opt.value}
                    type="button"
                    role="option"
                    className={optClasses}
                    aria-selected={isSelected}
                    aria-disabled={opt.disabled}
                    onClick={() => !opt.disabled && handleSelect(opt.value)}
                    onKeyDown={(e) => handleOptionKeyDown(e, opt.value, i)}
                    tabIndex={opt.disabled ? -1 : 0}
                  >
                    <span className="wl-select__option-check">
                      {isSelected && <Check size={14} />}
                    </span>
                    {opt.label}
                  </button>
                );
              })
            )}
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

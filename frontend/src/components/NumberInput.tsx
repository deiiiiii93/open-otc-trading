import React, { useCallback } from 'react';
import { useThousandSeparator } from './ThousandSeparatorContext';

type NumberInputProps = React.InputHTMLAttributes<HTMLInputElement>;

export function NumberInput(props: NumberInputProps) {
  return <input {...useNumberInputProps(props)} />;
}

export const __numberInputTestUtils = {
  formatNumberText,
  stripThousandSeparators,
};

export function useNumberInputProps<T extends NumberInputProps>(props: T): T {
  const { thousandSeparator } = useThousandSeparator();
  const enabled = thousandSeparator && props.type === 'number';
  const onChange = props.onChange;
  const onInput = props.onInput;
  const value = formatInputValue(props.value);
  const defaultValue = props.value === undefined ? formatInputValue(props.defaultValue) : props.defaultValue;
  const shouldFormat = hasThousandSeparator(value) || hasThousandSeparator(defaultValue);

  const handleChange = useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
    if (!onChange) return;
    const input = event.currentTarget;
    const rawValue = stripThousandSeparators(input.value);

    setInputValue(input, rawValue);
    onChange(event);
    setInputValue(input, formatNumberText(rawValue));
  }, [onChange]);

  const handleInput = useCallback((event: React.FormEvent<HTMLInputElement>) => {
    if (!onInput) return;
    const input = event.currentTarget;
    const rawValue = stripThousandSeparators(input.value);

    setInputValue(input, rawValue);
    onInput(event as React.FormEvent<HTMLInputElement> & React.InputEvent<HTMLInputElement>);
    setInputValue(input, formatNumberText(rawValue));
  }, [onInput]);

  if (!enabled || !shouldFormat) return props;

  return {
    ...props,
    type: 'text',
    inputMode: props.inputMode ?? 'decimal',
    value,
    defaultValue,
    onInput: handleInput,
    onChange: handleChange,
  };
}

export function stripThousandSeparators(value: string): string {
  return value.replace(/,/g, '');
}

function formatInputValue(value: NumberInputProps['value']): NumberInputProps['value'] {
  if (typeof value !== 'string' && typeof value !== 'number') return value;
  return formatNumberText(String(value));
}

function formatNumberText(value: string): string {
  if (value === '' || /e/i.test(value)) return value;

  const sign = value.startsWith('-') || value.startsWith('+') ? value[0] : '';
  const unsigned = sign ? value.slice(1) : value;
  const [integer, ...rest] = unsigned.split('.');
  if (!/^\d*$/.test(integer) || rest.length > 1) return value;

  const fraction = rest[0];
  if (fraction !== undefined && !/^\d*$/.test(fraction)) return value;

  const groupedInteger = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${sign}${groupedInteger}${fraction === undefined ? '' : `.${fraction}`}`;
}

function hasThousandSeparator(value: NumberInputProps['value']): boolean {
  return typeof value === 'string' && value.includes(',');
}

function setInputValue(input: HTMLInputElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
  descriptor?.set?.call(input, value);
}

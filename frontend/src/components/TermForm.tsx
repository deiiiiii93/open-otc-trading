import { useMemo, useState } from 'react';
import type { ChoiceMeta, TermFormField, TermFormMeta } from '../types';
import { composeTermFormSubmission, validateTermFormValue } from './termFormModel';
import { DatePicker } from './DatePicker';
import { NumberInput } from './NumberInput';
import './TermForm.css';

type Props = {
  form: TermFormMeta;
  onSubmit: (message: string) => void;
};

function initialValues(fields: TermFormField[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const field of fields) {
    out[field.key] = field.default ? String(field.default.value) : '';
  }
  return out;
}

export function TermForm({ form, onSubmit }: Props) {
  const [values, setValues] = useState<Record<string, string>>(() => initialValues(form.fields));
  const [showErrors, setShowErrors] = useState(false);

  const errors = useMemo(() => {
    const out: Record<string, string | null> = {};
    for (const field of form.fields) out[field.key] = validateTermFormValue(field, values[field.key] ?? '');
    return out;
  }, [form.fields, values]);

  const setValue = (key: string, value: string) =>
    setValues((prev) => ({ ...prev, [key]: value }));

  const filledCount = form.fields.filter((f) => !errors[f.key]).length;
  const chipValue = (choice: ChoiceMeta) => String(choice.value);

  const handleSubmit = () => {
    const hasError = form.fields.some((f) => errors[f.key]);
    if (hasError) {
      setShowErrors(true);
      return;
    }
    onSubmit(composeTermFormSubmission(form.fields, values));
  };

  return (
    <section className="wl-term-form" aria-label={form.title}>
      <header className="wl-term-form__head">
        <p className="wl-term-form__title">{form.title}</p>
        {form.subtitle && <p className="wl-term-form__sub">{form.subtitle}</p>}
        <span className="wl-term-form__progress">{filledCount} of {form.fields.length} terms set</span>
      </header>

      {form.fields.map((field) => {
        const inputId = `tf-${field.key}`;
        const error = showErrors ? errors[field.key] : null;
        return (
          <div className="wl-term-form__row" key={field.key}>
            <label
              className="wl-term-form__label"
              htmlFor={field.type !== 'enum' ? inputId : undefined}
            >
              {field.label}
              {field.help && <span className="wl-term-form__help"> — {field.help}</span>}
            </label>
            <div className="wl-term-form__controls">
              {(field.choices ?? []).map((choice) => {
                const selected = values[field.key] === chipValue(choice);
                const isDefault = field.default && chipValue(field.default) === chipValue(choice);
                return (
                  <button
                    type="button"
                    key={chipValue(choice)}
                    className={
                      'wl-term-form__chip'
                      + (selected ? ' is-selected' : '')
                      + (isDefault ? ' is-default' : '')
                    }
                    onClick={() => setValue(field.key, chipValue(choice))}
                  >
                    {choice.label}
                  </button>
                );
              })}
              {field.type !== 'enum' && field.type === 'date' && (
                <DatePicker
                  id={inputId}
                  value={values[field.key] ?? ''}
                  onChange={(v) => setValue(field.key, v)}
                />
              )}
              {field.type !== 'enum' && field.type !== 'date' && (
                <NumberInput
                  id={inputId}
                  className="wl-term-form__input"
                  type={field.type === 'number' || field.type === 'percent' ? 'number' : 'text'}
                  value={values[field.key] ?? ''}
                  onChange={(e) => setValue(field.key, e.target.value)}
                  placeholder={field.type === 'percent' ? '%' : undefined}
                />
              )}
            </div>
            {error && <p className="wl-term-form__error">{error}</p>}
          </div>
        );
      })}

      <button type="button" className="wl-term-form__submit" onClick={handleSubmit}>
        {form.submit_label ?? 'Review & book'}
      </button>
    </section>
  );
}

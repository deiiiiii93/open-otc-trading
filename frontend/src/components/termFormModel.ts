import type { TermFormField } from '../types';

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function toNumber(raw: string): number | null {
  const cleaned = raw.trim().replace(/%$/, '').trim();
  if (cleaned === '') return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

/** Returns an error string, or null when the value is acceptable. */
export function validateTermFormValue(field: TermFormField, raw: string): string | null {
  const value = raw.trim();
  const required = field.required !== false;
  if (!value) return required ? 'Required' : null;
  if (field.type === 'number' || field.type === 'percent') {
    return toNumber(value) === null ? 'Must be a number' : null;
  }
  if (field.type === 'date') {
    return ISO_DATE.test(value) ? null : 'Use YYYY-MM-DD';
  }
  if (field.type === 'enum') {
    const allowed = (field.choices ?? []).map((c) => String(c.value));
    return allowed.includes(value) ? null : 'Pick a listed option';
  }
  return null;
}

/** Composes the user message sent on submit: a readable sentence plus a
 * json block keyed by each field's build_product terms key. The agent parses
 * the json and merges it into terms before re-validating via build_product. */
export function composeTermFormSubmission(
  fields: TermFormField[],
  values: Record<string, string>,
): string {
  const out: Record<string, string | number> = {};
  for (const field of fields) {
    const raw = (values[field.key] ?? '').trim();
    if (!raw) continue;
    if (field.type === 'number' || field.type === 'percent') {
      const n = toNumber(raw);
      if (n !== null) out[field.key] = n;
    } else {
      out[field.key] = raw;
    }
  }
  const json = JSON.stringify(out, null, 2);
  return `Here are the booking terms:\n\n\`\`\`json\n${json}\n\`\`\``;
}

import { useEffect, useState } from 'react';
import { DslSyntaxError, parseDsl, serializeDsl } from '../lib/ruleTree';
import type { FilterRule } from '../types';
import './RuleTextEditor.css';

type Props = {
  rule: FilterRule | null;
  onChange: (rule: FilterRule | null, parseError: string | null) => void;
};

export function RuleTextEditor({ rule, onChange }: Props) {
  const [text, setText] = useState(() => (rule ? serializeDsl(rule) : ''));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setText(rule ? serializeDsl(rule) : '');
  }, [rule]);

  function handle(value: string) {
    setText(value);
    if (!value.trim()) {
      setError(null);
      onChange(null, null);
      return;
    }
    try {
      const parsed = parseDsl(value);
      setError(null);
      onChange(parsed, null);
    } catch (err) {
      const message = err instanceof DslSyntaxError ? err.message : String(err);
      setError(`Syntax: ${message}`);
      onChange(null, message);
    }
  }

  return (
    <div className="wl-ruletext">
      <textarea
        className="wl-ruletext__area"
        rows={3}
        value={text}
        onChange={(e) => handle(e.target.value)}
        placeholder='e.g. product_type = "Snowball" AND status = open'
      />
      {error && <div className="wl-ruletext__err">{error}</div>}
    </div>
  );
}

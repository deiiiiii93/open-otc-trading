import { useState } from 'react';
import type { FilterRule } from '../types';
import { RuleBuilder } from './RuleBuilder';
import { RuleTextEditor } from './RuleTextEditor';
import './RuleEditor.css';

type Mode = 'builder' | 'text';

type Props = {
  rule: FilterRule | null;
  onChange: (rule: FilterRule | null) => void;
};

export function RuleEditor({ rule, onChange }: Props) {
  const [mode, setMode] = useState<Mode>('builder');
  const [textParseError, setTextParseError] = useState<string | null>(null);

  return (
    <div className="wl-ruleeditor">
      <div className="wl-ruleeditor__toggle">
        <button
          type="button"
          className={mode === 'builder' ? 'is-active' : ''}
          onClick={() => setMode('builder')}
        >
          Builder
        </button>
        <button
          type="button"
          className={mode === 'text' ? 'is-active' : ''}
          onClick={() => setMode('text')}
        >
          Text
        </button>
        {mode === 'text' && textParseError && (
          <button type="button" disabled title={textParseError}>
            (builder disabled - fix syntax)
          </button>
        )}
      </div>
      {mode === 'builder' ? (
        <RuleBuilder rule={rule} onChange={onChange} />
      ) : (
        <RuleTextEditor
          rule={rule}
          onChange={(nextRule, parseError) => {
            setTextParseError(parseError);
            if (!parseError) onChange(nextRule);
          }}
        />
      )}
    </div>
  );
}

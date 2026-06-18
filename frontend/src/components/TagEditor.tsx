import { useState, type KeyboardEvent } from 'react';
import './TagEditor.css';

type Props = {
  tags: string[];
  onChange: (tags: string[]) => void;
  placeholder?: string;
};

export function TagEditor({ tags, onChange, placeholder = 'Add tag...' }: Props) {
  const [draft, setDraft] = useState('');

  function commit() {
    const tag = draft.trim().toLowerCase();
    setDraft('');
    if (!tag) return;
    if (tag.length > 40) return;
    if (tags.includes(tag)) return;
    onChange([...tags, tag]);
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      commit();
    } else if (e.key === 'Backspace' && !draft && tags.length) {
      onChange(tags.slice(0, -1));
    }
  }

  return (
    <div className="wl-tageditor">
      {tags.map((tag) => (
        <span key={tag} className="wl-tageditor__chip">
          {tag}
          <button type="button" aria-label={`Remove ${tag}`} onClick={() => onChange(tags.filter((x) => x !== tag))}>
            x
          </button>
        </span>
      ))}
      <input
        className="wl-tageditor__input"
        value={draft}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKey}
        onBlur={commit}
        maxLength={40}
      />
    </div>
  );
}

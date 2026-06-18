import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import './CommandPalette.css';

export type CommandItem = {
  id: string;
  group: string;
  label: string;
  shortcut?: string;
};

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  items: CommandItem[];
  onSelect: (item: CommandItem) => void;
  placeholder?: string;
};

export function CommandPalette({ open, onOpenChange, items, onSelect, placeholder = 'Search…' }: Props) {
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) {
      setQuery('');
      setSelectedIndex(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((it) => it.label.toLowerCase().includes(q) || it.group.toLowerCase().includes(q));
  }, [items, query]);

  const grouped = useMemo(() => {
    const map = new Map<string, CommandItem[]>();
    for (const it of filtered) {
      const arr = map.get(it.group) ?? [];
      arr.push(it);
      map.set(it.group, arr);
    }
    return Array.from(map.entries());
  }, [filtered]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  useEffect(() => {
    const selected = listRef.current?.querySelector('[data-selected]');
    selected?.scrollIntoView({ block: 'nearest' });
  }, [selectedIndex]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const item = filtered[selectedIndex];
      if (item) onSelect(item);
    }
  }, [filtered, selectedIndex, onSelect]);

  let itemIndex = 0;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="wl-cmdk__overlay" />
        <Dialog.Content className="wl-cmdk" aria-label="Command palette">
          <input
            ref={inputRef}
            className="wl-cmdk__input"
            placeholder={placeholder}
            value={query}
            onKeyDown={handleKeyDown}
            onChange={(e) => setQuery(e.target.value)}
          />
          <div ref={listRef} className="wl-cmdk__results">
            {grouped.map(([group, list]) => (
              <div key={group} className="wl-cmdk__group">
                <div className="wl-cmdk__group-label">{group}</div>
                {list.map((it) => {
                  const idx = itemIndex++;
                  return (
                    <button
                      key={it.id}
                      type="button"
                      className="wl-cmdk__item"
                      data-selected={idx === selectedIndex ? '' : undefined}
                      onClick={() => onSelect(it)}
                    >
                      <span>{it.label}</span>
                      {it.shortcut && <span className="wl-cmdk__shortcut">{it.shortcut}</span>}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

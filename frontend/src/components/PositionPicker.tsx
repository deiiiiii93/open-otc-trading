import { useEffect, useState } from 'react';
import { Button } from './Button';
import { Modal } from './Modal';
import './PositionPicker.css';

type PickerPosition = {
  id: number;
  source_trade_id?: string | null;
  underlying: string;
  product_type: string;
};

type Props = {
  open: boolean;
  positions: PickerPosition[];
  excludeIds: number[];
  onCancel: () => void;
  onConfirm: (ids: number[]) => void;
  title?: string;
};

export function PositionPicker({
  open,
  positions,
  excludeIds,
  onCancel,
  onConfirm,
  title = 'Pick Positions',
}: Props) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (!open) {
      setQuery('');
      setSelected(new Set());
    }
  }, [open]);

  if (!open) return null;

  const lowerQuery = query.toLowerCase();
  const excluded = new Set(excludeIds);
  const filtered = positions.filter((position) => {
    if (excluded.has(position.id)) return false;
    if (!lowerQuery) return true;
    return (
      position.underlying.toLowerCase().includes(lowerQuery) ||
      position.product_type.toLowerCase().includes(lowerQuery) ||
      (position.source_trade_id ?? '').toLowerCase().includes(lowerQuery) ||
      String(position.id).includes(lowerQuery)
    );
  });

  function toggle(id: number) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  }

  return (
    <Modal open={open} onOpenChange={(o) => { if (!o) onCancel(); }} title={title} layoutKey="position-picker">
      <div className="wl-picker">
        <input
          className="wl-picker__search"
          placeholder="Search positions..."
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <ul className="wl-picker__list">
          {filtered.map((position) => (
            <li key={position.id} className="wl-picker__row">
              <label>
                <input
                  type="checkbox"
                  aria-label={`Select position ${position.id}`}
                  checked={selected.has(position.id)}
                  onChange={() => toggle(position.id)}
                />
                <span className="wl-picker__id">#{position.id}</span>
                <span className="wl-picker__trade">{position.source_trade_id ?? '—'}</span>
                <span className="wl-picker__underlying">{position.underlying}</span>
                <span className="wl-picker__product">{position.product_type}</span>
              </label>
            </li>
          ))}
          {filtered.length === 0 && (
            <li className="wl-picker__empty">No positions match.</li>
          )}
        </ul>
        <div className="wl-picker__actions">
          <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button
            type="button"
            variant="primary"
            disabled={selected.size === 0}
            onClick={() => onConfirm(Array.from(selected))}
          >
            Add {selected.size}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

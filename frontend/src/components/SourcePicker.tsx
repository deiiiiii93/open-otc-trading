import { useEffect, useState } from 'react';
import { Button } from './Button';
import { KindChip } from './KindChip';
import { Modal } from './Modal';
import './PositionPicker.css';

type SourcePortfolio = {
  id: number;
  name: string;
  kind: 'view' | 'container';
  resolved_position_count: number;
};

type Props = {
  open: boolean;
  portfolios: SourcePortfolio[];
  currentPortfolioId: number | null;
  excludeIds: number[];
  onCancel: () => void;
  onConfirm: (ids: number[]) => void;
};

export function SourcePicker({
  open,
  portfolios,
  currentPortfolioId,
  excludeIds,
  onCancel,
  onConfirm,
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
  const excluded = new Set([
    ...excludeIds,
    ...(currentPortfolioId != null ? [currentPortfolioId] : []),
  ]);
  const filtered = portfolios.filter((portfolio) => {
    if (excluded.has(portfolio.id)) return false;
    if (!lowerQuery) return true;
    return portfolio.name.toLowerCase().includes(lowerQuery);
  });

  function toggle(id: number) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  }

  return (
    <Modal open={open} onOpenChange={(o) => { if (!o) onCancel(); }} title="Pick Source Portfolios" layoutKey="source-picker">
      <div className="wl-picker wl-picker--source">
        <input
          className="wl-picker__search"
          placeholder="Search portfolios..."
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <ul className="wl-picker__list">
          {filtered.map((portfolio) => (
            <li key={portfolio.id} className="wl-picker__row">
              <label>
                <input
                  type="checkbox"
                  aria-label={`Select portfolio ${portfolio.id}`}
                  checked={selected.has(portfolio.id)}
                  onChange={() => toggle(portfolio.id)}
                />
                <span className="wl-picker__id"><KindChip kind={portfolio.kind} /></span>
                <span className="wl-picker__underlying">{portfolio.name}</span>
                <span className="wl-picker__product">{portfolio.resolved_position_count} positions</span>
              </label>
            </li>
          ))}
          {filtered.length === 0 && (
            <li className="wl-picker__empty">No portfolios match.</li>
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

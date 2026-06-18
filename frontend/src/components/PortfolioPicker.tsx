import { useState } from 'react';
import type { PortfolioKind } from '../types';
import './PortfolioPicker.css';

type PickerPortfolio = {
  id: number;
  name: string;
  kind: PortfolioKind;
};

type Props = {
  open: boolean;
  portfolios: PickerPortfolio[];
  currentPortfolioId: number;
  excludedIds: Set<number>;
  onCancel: () => void;
  onConfirm: (ids: number[]) => void;
};

export function PortfolioPicker({ open, portfolios, currentPortfolioId, excludedIds, onCancel, onConfirm }: Props) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Set<number>>(new Set());

  if (!open) return null;

  const lowerQuery = query.toLowerCase();
  const candidates = portfolios.filter((portfolio) =>
    portfolio.id !== currentPortfolioId &&
    !excludedIds.has(portfolio.id) &&
    (!lowerQuery || portfolio.name.toLowerCase().includes(lowerQuery)),
  );

  function toggle(id: number) {
    const next = new Set(selected);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    setSelected(next);
  }

  return (
    <div className="wl-modal" role="dialog" aria-label="Pick portfolios">
      <div className="wl-modal__body">
        <input
          placeholder="Search portfolios..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <ul className="wl-modal__list">
          {candidates.map((portfolio) => (
            <li key={portfolio.id}>
              <label>
                <input
                  type="checkbox"
                  checked={selected.has(portfolio.id)}
                  onChange={() => toggle(portfolio.id)}
                />
                <span>{portfolio.name}</span>
                <span>{portfolio.kind}</span>
              </label>
            </li>
          ))}
        </ul>
        <div className="wl-modal__actions">
          <button type="button" onClick={onCancel}>Cancel</button>
          <button type="button" onClick={() => onConfirm(Array.from(selected))}>Confirm</button>
        </div>
      </div>
    </div>
  );
}

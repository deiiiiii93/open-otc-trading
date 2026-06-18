import { useEffect, useState } from 'react';
import { Button } from './Button';
import { Modal } from './Modal';
import type { PortfolioKind } from '../types';
import './PortfolioCreateDialog.css';

type Props = {
  open: boolean;
  kind: PortfolioKind;
  onCancel: () => void;
  onCreate: (input: { name: string; kind: PortfolioKind }) => void;
};

export function PortfolioCreateDialog({ open, kind, onCancel, onCreate }: Props) {
  const [name, setName] = useState('');

  useEffect(() => {
    if (!open) setName('');
  }, [open]);

  const trimmed = name.trim();
  const title = kind === 'view' ? 'NEW VIEW PORTFOLIO' : 'NEW CONTAINER PORTFOLIO';

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!trimmed) return;
    onCreate({ name: trimmed, kind });
  }

  return (
    <Modal open={open} onOpenChange={(o) => { if (!o) onCancel(); }} title={title} layoutKey="portfolio-create">
      <form className="wl-create-portfolio" onSubmit={submit}>
        <label className="wl-create-portfolio__field" htmlFor="portfolio-create-name">
          <span>Name</span>
          <input
            id="portfolio-create-name"
            value={name}
            placeholder="e.g. Snowballs 2026Q2"
            onChange={(event) => setName(event.target.value)}
            autoFocus
          />
        </label>
        <div className="wl-create-portfolio__actions">
          <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={!trimmed}>Create</Button>
        </div>
      </form>
    </Modal>
  );
}

import { Button } from './Button';
import { Modal } from './Modal';
import './PortfolioDeleteDialog.css';

type Props = {
  open: boolean;
  portfolioName: string;
  onCancel: () => void;
  onConfirm: () => void;
};

export function PortfolioDeleteDialog({ open, portfolioName, onCancel, onConfirm }: Props) {
  return (
    <Modal open={open} onOpenChange={(o) => { if (!o) onCancel(); }} title="DELETE PORTFOLIO" layoutKey="portfolio-delete">
      <div className="wl-delete-portfolio">
        <p className="wl-delete-portfolio__body">
          Delete <strong>{portfolioName}</strong>? This action cannot be undone.
        </p>
        <div className="wl-delete-portfolio__actions">
          <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button type="button" variant="danger" onClick={onConfirm}>Delete</Button>
        </div>
      </div>
    </Modal>
  );
}

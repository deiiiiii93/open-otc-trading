import { useEffect, useState } from 'react';
import { Modal } from './Modal';
import { Input } from './Input';
import { Button } from './Button';
import './RfqRejectModal.css';

type Props = {
  open: boolean;
  rfqId: number | null;
  onConfirm: (rfqId: number, reason: string) => void;
  onOpenChange: (open: boolean) => void;
};

export function RfqRejectModal({ open, rfqId, onConfirm, onOpenChange }: Props) {
  const [reason, setReason] = useState('');

  useEffect(() => {
    if (open) setReason('');
  }, [open, rfqId]);

  const trimmed = reason.trim();
  const canConfirm = trimmed.length > 0 && rfqId != null;

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title={rfqId != null ? `Reject RFQ #${rfqId}` : 'Reject RFQ'}
      layoutKey="rfq-reject"
      description="This action is irreversible. The reason is recorded in the approval audit log."
    >
      <div className="wl-reject-modal">
        <Input
          label="Reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          autoFocus
          placeholder="Explain why this RFQ is being rejected"
        />
        <div className="wl-reject-modal__actions">
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            variant="danger"
            disabled={!canConfirm}
            onClick={() => { if (canConfirm) onConfirm(rfqId!, trimmed); }}
          >
            Confirm Reject
          </Button>
        </div>
      </div>
    </Modal>
  );
}

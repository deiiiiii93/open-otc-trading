import type { BadgeVariant } from '../components/Badge';

export const rfqStatusVariant: Record<string, BadgeVariant> = {
  pending_approval: 'warn',
  submitted: 'warn',
  pricing_failed: 'neg',
  approved: 'pos',
  released: 'pos',
  client_accepted: 'pos',
  booked: 'pos',
  rejected: 'neg',
  expired: 'neg',
  cancelled: 'neg',
  draft: 'ink',
};

export function rfqStatusBadge(status: string): BadgeVariant {
  return rfqStatusVariant[status] ?? 'ink';
}

export function formatRfqStatus(status: string): string {
  return status.replaceAll('_', ' ').replace(/^\w/, (letter) => letter.toUpperCase());
}

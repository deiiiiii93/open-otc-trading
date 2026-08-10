/** Settlement read models.
 *
 * Kept out of `Settlement.tsx` so `api/client.ts` can import them without
 * pulling in a React component.
 */

export interface SettlementCashflowRow {
  id: number;
  position_id: number;
  underlying: string | null;
  product_type: string | null;
  event_type: string | null;
  leg_key: string;
  direction: string;
  amount: number | null;
  currency: string;
  value_date: string | null;
  counterparty: string | null;
  status: string;
  stale: boolean;
  stale_reason: Record<string, unknown> | null;
  derived_amount: number | null;
  row_version: number;
}

export interface SettlementCashflowEvent {
  id: number;
  action: string;
  from_status: string | null;
  to_status: string | null;
  actor: string;
  reason: string | null;
  created_at: string;
}

export interface SettlementNotice {
  id: number;
  version: number;
  artifact_path: string;
  status: string;
  rendered_at: string;
  rendered_by: string;
}

export interface SettlementCashflowDetail extends SettlementCashflowRow {
  derived_value_date: string | null;
  derived_basis: string | null;
  notes: string | null;
  block_reason: string | null;
  last_checked_at: string | null;
  events: SettlementCashflowEvent[];
  notices: SettlementNotice[];
}

export interface SettlementSummary {
  by_status: Record<string, number>;
  totals_by_currency: Record<string, number>;
  stale_count: number;
}

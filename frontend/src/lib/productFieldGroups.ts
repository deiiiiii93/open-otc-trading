export type FieldGroupKey = 'counterparty' | 'underlying' | 'dates' | 'barriers' | 'payoff' | 'other';

export const FIELDS_GROUP_ORDER: FieldGroupKey[] = [
  'counterparty', 'underlying', 'dates', 'barriers', 'payoff', 'other',
] as const;

export const GROUP_LABELS: Record<FieldGroupKey, string> = {
  counterparty: 'Counterparty',
  underlying: 'Underlying & Notional',
  dates: 'Schedule',
  barriers: 'Barriers',
  payoff: 'Payoff',
  other: 'Other',
};

export function getFieldGroup(key: string): FieldGroupKey {
  if (key === 'counterparty' || key === 'side') return 'counterparty';
  if (key === 'underlying' || key === 'notional' || key === 'initial_price' || key === 'quantity') return 'underlying';
  if (key === 'start_date' || key === 'end_date' || key === 'tenor_months' || key === 'tenor_days'
    || key === 'observation_frequency' || key === 'lockup_months'
    || key === 'maturity_years' || key === 'maturity'
    || key === 'trade_start_date' || key === 'exercise_date' || key === 'settlement_date'
    || key === 'initial_date' || key === 'num_observations') return 'dates';
  if (key === 'ko_barrier' || key === 'ki_barrier' || key === 'barrier' || key === 'upper_barrier' || key === 'lower_barrier'
    || key === 'ko_barrier_pct' || key === 'ki_barrier_pct' || key === 'coupon_barrier_pct'
    || key === 'post_ko_barrier_pct' || key === 'barrier_type' || key === 'barrier_direction' || key === 'touch_type') return 'barriers';
  if (key === 'strike' || key === 'option_type' || key === 'coupon_yield' || key === 'payout'
    || key === 'participation_rate' || key === 'rebate'
    || key === 'ko_rate' || key === 'coupon_rate' || key === 'maturity'
    || key === 'knock_out_rebate' || key === 'no_hit_rebate'
    || key === 'post_ko_rate' || key === 'contract_multiplier'
    || key === 'is_reverse') return 'payoff';
  return 'other';
}

export type FieldType = 'number' | 'text' | 'date' | 'select' | 'boolean';

export interface RfqProductField {
  key: string;
  label: string;
  type: FieldType;
  options?: string[];
}

type GroupedFields = { key: FieldGroupKey; label: string; fields: RfqProductField[] };

export function groupProductFields(fields: RfqProductField[]): GroupedFields[] {
  const groups = new Map<FieldGroupKey, RfqProductField[]>();
  for (const field of fields) {
    const gk = getFieldGroup(field.key);
    if (!groups.has(gk)) groups.set(gk, []);
    groups.get(gk)!.push(field);
  }
  return FIELDS_GROUP_ORDER
    .filter((k) => groups.has(k))
    .map((k) => ({ key: k, label: GROUP_LABELS[k], fields: groups.get(k)! }));
}

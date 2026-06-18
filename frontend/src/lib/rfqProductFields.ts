import type { RfqProductField } from './productFieldGroups';

const OBSERVATION_FREQUENCIES = ['MONTHLY', 'QUARTERLY', 'SEMI_ANNUAL'];

interface ProductFieldDef {
  key: string;
  label: string;
  type: RfqProductField['type'];
  options?: string[];
}

const FLAT_SNOWBALL_COMMON: ProductFieldDef[] = [
  { key: 'initial_price', label: 'Initial Price', type: 'number' },
  { key: 'strike', label: 'Strike', type: 'number' },
  { key: 'maturity_years', label: 'Maturity (years)', type: 'number' },
  { key: 'trade_start_date', label: 'Trade Start Date', type: 'date' },
  { key: 'ko_barrier_pct', label: 'KO Barrier %', type: 'number' },
  { key: 'ki_barrier_pct', label: 'KI Barrier %', type: 'number' },
  { key: 'ko_rate', label: 'KO Rate', type: 'number' },
  { key: 'lockup_months', label: 'Lockup Months', type: 'number' },
  { key: 'observation_frequency', label: 'Observation Frequency', type: 'select', options: OBSERVATION_FREQUENCIES },
];

const PRODUCT_FIELD_DEFS: Record<string, (kwargs: Record<string, unknown>) => ProductFieldDef[]> = {
  EuropeanVanillaOption: () => [
    { key: 'strike', label: 'Strike', type: 'number' },
    { key: 'option_type', label: 'Option Type', type: 'select', options: ['CALL', 'PUT'] },
    { key: 'maturity', label: 'Maturity (years)', type: 'number' },
    { key: 'contract_multiplier', label: 'Contract Multiplier', type: 'number' },
    { key: 'exercise_date', label: 'Exercise Date', type: 'date' },
    { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
  ],
  AmericanOption: () => [
    { key: 'strike', label: 'Strike', type: 'number' },
    { key: 'option_type', label: 'Option Type', type: 'select', options: ['CALL', 'PUT'] },
    { key: 'maturity', label: 'Maturity (years)', type: 'number' },
    { key: 'contract_multiplier', label: 'Contract Multiplier', type: 'number' },
    { key: 'exercise_date', label: 'Exercise Date', type: 'date' },
    { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
  ],
  CashOrNothingDigitalOption: () => [
    { key: 'strike', label: 'Strike', type: 'number' },
    { key: 'payout', label: 'Payout', type: 'number' },
    { key: 'option_type', label: 'Option Type', type: 'select', options: ['CALL', 'PUT'] },
    { key: 'maturity', label: 'Maturity (years)', type: 'number' },
    { key: 'contract_multiplier', label: 'Contract Multiplier', type: 'number' },
    { key: 'exercise_date', label: 'Exercise Date', type: 'date' },
    { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
  ],
  BarrierOption: () => [
    { key: 'strike', label: 'Strike', type: 'number' },
    { key: 'option_type', label: 'Option Type', type: 'select', options: ['CALL', 'PUT'] },
    { key: 'barrier', label: 'Barrier', type: 'number' },
    { key: 'barrier_type', label: 'Barrier Type', type: 'select', options: ['UP_IN', 'DOWN_IN', 'UP_OUT', 'DOWN_OUT'] },
    { key: 'rebate', label: 'Rebate', type: 'number' },
    { key: 'participation_rate', label: 'Participation Rate', type: 'number' },
    { key: 'maturity', label: 'Maturity (years)', type: 'number' },
    { key: 'contract_multiplier', label: 'Contract Multiplier', type: 'number' },
    { key: 'exercise_date', label: 'Exercise Date', type: 'date' },
    { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
  ],
  SnowballOption: () => [
    ...FLAT_SNOWBALL_COMMON,
    { key: 'contract_multiplier', label: 'Contract Multiplier', type: 'number' },
  ],
  KnockOutResetSnowballOption: () => [
    ...FLAT_SNOWBALL_COMMON,
      { key: 'post_ko_barrier_pct', label: 'Post-KO Barrier %', type: 'number' },
      { key: 'post_ko_rate', label: 'Post-KO Rate', type: 'number' },
      { key: 'contract_multiplier', label: 'Contract Multiplier', type: 'number' },
  ],
  PhoenixOption: () => [
    ...FLAT_SNOWBALL_COMMON,
      { key: 'coupon_barrier_pct', label: 'Coupon Barrier %', type: 'number' },
      { key: 'coupon_rate', label: 'Coupon Rate', type: 'number' },
      { key: 'contract_multiplier', label: 'Contract Multiplier', type: 'number' },
  ],
  SingleSharkfinOption: () => [
    { key: 'strike', label: 'Strike', type: 'number' },
    { key: 'barrier', label: 'Barrier', type: 'number' },
    { key: 'option_type', label: 'Option Type', type: 'select', options: ['CALL', 'PUT'] },
    { key: 'maturity', label: 'Maturity (years)', type: 'number' },
    { key: 'participation_rate', label: 'Participation Rate', type: 'number' },
    { key: 'contract_multiplier', label: 'Contract Multiplier', type: 'number' },
    { key: 'knock_out_rebate', label: 'Knock Out Rebate', type: 'number' },
    { key: 'no_hit_rebate', label: 'No Hit Rebate', type: 'number' },
    { key: 'exercise_date', label: 'Exercise Date', type: 'date' },
    { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
  ],
  DoubleSharkfinOption: () => [
    { key: 'strike', label: 'Strike', type: 'number' },
    { key: 'option_type', label: 'Option Type', type: 'select', options: ['CALL', 'PUT'] },
    { key: 'participation_rate', label: 'Participation Rate', type: 'number' },
    { key: 'contract_multiplier', label: 'Contract Multiplier', type: 'number' },
    { key: 'knock_out_rebate', label: 'Knock Out Rebate', type: 'number' },
    { key: 'no_hit_rebate', label: 'No Hit Rebate', type: 'number' },
    { key: 'exercise_date', label: 'Exercise Date', type: 'date' },
    { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
  ],
  AsianOption: () => [
    { key: 'strike', label: 'Strike', type: 'number' },
    { key: 'option_type', label: 'Option Type', type: 'select', options: ['CALL', 'PUT'] },
    { key: 'maturity', label: 'Maturity (years)', type: 'number' },
    { key: 'num_observations', label: 'Num Observations', type: 'number' },
    { key: 'initial_price', label: 'Initial Price', type: 'number' },
    { key: 'contract_multiplier', label: 'Contract Multiplier', type: 'number' },
    { key: 'exercise_date', label: 'Exercise Date', type: 'date' },
    { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
  ],
  OneTouchOption: () => [
    { key: 'barrier', label: 'Barrier', type: 'number' },
    { key: 'rebate', label: 'Rebate', type: 'number' },
    { key: 'barrier_direction', label: 'Barrier Direction', type: 'select', options: ['UP', 'DOWN'] },
    { key: 'touch_type', label: 'Touch Type', type: 'select', options: ['ONE_TOUCH', 'NO_TOUCH'] },
    { key: 'maturity', label: 'Maturity (years)', type: 'number' },
  ],
  RangeAccrualOption: () => [
    { key: 'initial_price', label: 'Initial Price', type: 'number' },
    { key: 'maturity', label: 'Maturity (years)', type: 'number' },
    { key: 'num_observations', label: 'Num Observations', type: 'number' },
    { key: 'contract_multiplier', label: 'Contract Multiplier', type: 'number' },
  ],
  Futures: () => [
    { key: 'maturity', label: 'Maturity (years)', type: 'number' },
  ],
  SpotInstrument: () => [
    { key: 'deltaone_type', label: 'DeltaOne Type', type: 'select', options: ['STOCK', 'INDEX', 'ETF', 'FUTURES'] },
  ],
};

export function getProductFields(
  productType: string,
  productKwargs: Record<string, unknown>,
): RfqProductField[] {
  const resolver = PRODUCT_FIELD_DEFS[productType];
  if (!resolver) return [];
  return resolver(productKwargs);
}

export function getProductTypeLabel(productType: string): string {
  const labels: Record<string, string> = {
    EuropeanVanillaOption: 'Vanilla',
    AmericanOption: 'American',
    CashOrNothingDigitalOption: 'Digital',
    BarrierOption: 'Barrier',
    SnowballOption: 'Snowball',
    KnockOutResetSnowballOption: 'KO Reset Snowball',
    PhoenixOption: 'Phoenix',
    SingleSharkfinOption: 'Single Sharkfin',
    DoubleSharkfinOption: 'Double Sharkfin',
    AsianOption: 'Asian',
    OneTouchOption: 'One Touch',
    RangeAccrualOption: 'Range Accrual',
    Futures: 'Forward',
    SpotInstrument: 'Spot',
  };
  return labels[productType] ?? productType;
}

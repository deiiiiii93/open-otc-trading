import { useMemo, useState } from 'react';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { Button } from './Button';
import { DatePicker } from './DatePicker';
import { Modal } from './Modal';
import { Select } from './Select';
import '../routes/Positions.css';

type FieldType = 'number' | 'text' | 'date' | 'select' | 'boolean';

export type FieldSpec = {
  key: string;
  label: string;
  type: FieldType;
  options?: string[];
};

const PRODUCT_TERM_FIELDS: Record<string, FieldSpec[]> = {
  EuropeanVanillaOption: [
    { key: 'strike', label: 'Strike', type: 'number' },
    { key: 'option_type', label: 'Option Type', type: 'select', options: ['CALL', 'PUT'] },
    { key: 'exercise_date', label: 'Exercise Date', type: 'date' },
    { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
  ],
  AmericanOption: [
    { key: 'strike', label: 'Strike', type: 'number' },
    { key: 'option_type', label: 'Option Type', type: 'select', options: ['CALL', 'PUT'] },
    { key: 'exercise_date', label: 'Exercise Date', type: 'date' },
    { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
  ],
  CashOrNothingDigitalOption: [
    { key: 'strike', label: 'Strike', type: 'number' },
    { key: 'payout', label: 'Payout', type: 'number' },
    { key: 'option_type', label: 'Option Type', type: 'select', options: ['CALL', 'PUT'] },
    { key: 'exercise_date', label: 'Exercise Date', type: 'date' },
    { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
  ],
  BarrierOption: [
    { key: 'strike', label: 'Strike', type: 'number' },
    { key: 'option_type', label: 'Option Type', type: 'select', options: ['CALL', 'PUT'] },
    { key: 'barrier', label: 'Barrier', type: 'number' },
    { key: 'barrier_type', label: 'Barrier Type', type: 'select', options: ['UP_IN', 'DOWN_IN', 'UP_OUT', 'DOWN_OUT'] },
    { key: 'rebate', label: 'Rebate', type: 'number' },
    { key: 'participation_rate', label: 'Participation Rate', type: 'number' },
    { key: 'exercise_date', label: 'Exercise Date', type: 'date' },
    { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
  ],
  SnowballOption: [
    { key: 'initial_price', label: 'Initial Price', type: 'number' },
    { key: 'strike', label: 'Strike', type: 'number' },
    { key: 'initial_date', label: 'Initial Date', type: 'date' },
    { key: 'exercise_date', label: 'Exercise Date', type: 'date' },
    { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
    { key: 'contract_multiplier', label: 'Contract Multiplier', type: 'number' },
    { key: 'is_reverse', label: 'Is Reverse', type: 'boolean' },
    { key: '_otc_ki_observation_convention', label: 'KI Observation Convention', type: 'select', options: ['DAILY', 'EUROPEAN', 'NONE'] },
    { key: '_otc_lifecycle_knocked_in', label: 'Lifecycle Knocked In', type: 'boolean' },
    { key: '_otc_lifecycle_state', label: 'Lifecycle State', type: 'text' },
  ],
  PhoenixOption: [
    { key: 'initial_price', label: 'Initial Price', type: 'number' },
    { key: 'strike', label: 'Strike', type: 'number' },
    { key: 'initial_date', label: 'Initial Date', type: 'date' },
    { key: 'exercise_date', label: 'Exercise Date', type: 'date' },
    { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
    { key: 'contract_multiplier', label: 'Contract Multiplier', type: 'number' },
    { key: 'is_reverse', label: 'Is Reverse', type: 'boolean' },
    { key: '_otc_ki_observation_convention', label: 'KI Observation Convention', type: 'select', options: ['DAILY', 'EUROPEAN', 'NONE'] },
    { key: '_otc_lifecycle_knocked_in', label: 'Lifecycle Knocked In', type: 'boolean' },
    { key: '_otc_lifecycle_state', label: 'Lifecycle State', type: 'text' },
  ],
  SingleSharkfinOption: [
    { key: 'strike', label: 'Strike', type: 'number' },
    { key: 'option_type', label: 'Option Type', type: 'select', options: ['CALL', 'PUT'] },
    { key: 'participation_rate', label: 'Participation Rate', type: 'number' },
    { key: 'knock_out_rebate', label: 'Knock Out Rebate', type: 'number' },
    { key: 'no_hit_rebate', label: 'No Hit Rebate', type: 'number' },
    { key: 'exercise_date', label: 'Exercise Date', type: 'date' },
    { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
  ],
  DoubleSharkfinOption: [
    { key: 'strike', label: 'Strike', type: 'number' },
    { key: 'option_type', label: 'Option Type', type: 'select', options: ['CALL', 'PUT'] },
    { key: 'participation_rate', label: 'Participation Rate', type: 'number' },
    { key: 'knock_out_rebate', label: 'Knock Out Rebate', type: 'number' },
    { key: 'no_hit_rebate', label: 'No Hit Rebate', type: 'number' },
    { key: 'exercise_date', label: 'Exercise Date', type: 'date' },
    { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
  ],
  AsianOption: [
    { key: 'strike', label: 'Strike', type: 'number' },
    { key: 'option_type', label: 'Option Type', type: 'select', options: ['CALL', 'PUT'] },
    { key: 'exercise_date', label: 'Exercise Date', type: 'date' },
    { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
  ],
  OneTouchOption: [
    { key: 'barrier', label: 'Barrier', type: 'number' },
    { key: 'rebate', label: 'Rebate', type: 'number' },
    { key: 'barrier_direction', label: 'Barrier Direction', type: 'select', options: ['UP', 'DOWN'] },
    { key: 'touch_type', label: 'Touch Type', type: 'select', options: ['ONE_TOUCH', 'NO_TOUCH'] },
  ],
  RangeAccrualOption: [
    { key: 'initial_price', label: 'Initial Price', type: 'number' },
    { key: 'maturity', label: 'Maturity (years)', type: 'number' },
    { key: 'num_observations', label: 'Num Observations', type: 'number' },
  ],
  Futures: [
    { key: 'maturity', label: 'Maturity (years)', type: 'number' },
  ],
  SpotInstrument: [
    { key: 'deltaone_type', label: 'DeltaOne Type', type: 'select', options: ['STOCK', 'INDEX', 'ETF', 'FUTURES'] },
  ],
  KnockOutResetSnowballOption: [
    { key: 'initial_price', label: 'Initial Price', type: 'number' },
    { key: 'strike', label: 'Strike', type: 'number' },
    { key: 'initial_date', label: 'Initial Date', type: 'date' },
    { key: 'exercise_date', label: 'Exercise Date', type: 'date' },
    { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
    { key: 'contract_multiplier', label: 'Contract Multiplier', type: 'number' },
    { key: 'is_reverse', label: 'Is Reverse', type: 'boolean' },
    { key: '_otc_ki_observation_convention', label: 'KI Observation Convention', type: 'select', options: ['DAILY', 'EUROPEAN', 'NONE'] },
    { key: '_otc_lifecycle_knocked_in', label: 'Lifecycle Knocked In', type: 'boolean' },
    { key: '_otc_lifecycle_state', label: 'Lifecycle State', type: 'text' },
  ],
};

// build_product synthesizes the periodic KO schedule from these frequencies.
const OBSERVATION_FREQUENCIES = ['MONTHLY', 'QUARTERLY', 'SEMI_ANNUAL'];

// Snowball-family RFQ intake carries the FLAT term contract (build_product input)
// rather than a nested barrier_config + ko_observation_schedule. When the kwargs
// look flat (see isFlatContract), render these editable contract fields instead
// of the built/nested field set — keeping the booking/positions editor untouched.
const SNOWBALL_CONTRACT_FIELDS: FieldSpec[] = [
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
const CONTRACT_MULTIPLIER_FIELD: FieldSpec = { key: 'contract_multiplier', label: 'Contract Multiplier', type: 'number' };

const FLAT_CONTRACT_FIELDS: Record<string, FieldSpec[]> = {
  SnowballOption: [...SNOWBALL_CONTRACT_FIELDS, CONTRACT_MULTIPLIER_FIELD],
  KnockOutResetSnowballOption: [
    ...SNOWBALL_CONTRACT_FIELDS,
    { key: 'post_ko_barrier_pct', label: 'Post-KO Barrier %', type: 'number' },
    { key: 'post_ko_rate', label: 'Post-KO Rate', type: 'number' },
    CONTRACT_MULTIPLIER_FIELD,
  ],
  PhoenixOption: [
    ...SNOWBALL_CONTRACT_FIELDS,
    { key: 'coupon_barrier_pct', label: 'Coupon Barrier %', type: 'number' },
    { key: 'coupon_rate', label: 'Coupon Rate', type: 'number' },
    CONTRACT_MULTIPLIER_FIELD,
  ],
};

// A snowball-family draft is a FLAT contract (not a built/nested termsheet) when
// it carries the contract's percent/frequency inputs rather than a barrier_config.
function isFlatContract(productKwargs: Record<string, unknown>): boolean {
  return 'observation_frequency' in productKwargs || 'ko_barrier_pct' in productKwargs;
}

const NESTED_CONFIG_KEYS = new Set([
  'barrier_config',
  'payoff_config',
  'coupon_config',
  'accrual_config',
  'range_config',
]);

type Props = {
  productType: string;
  productKwargs: Record<string, unknown>;
  onChange: (productKwargs: Record<string, unknown>) => void;
};

type ScheduleEditorState = {
  configKey: string;
  scheduleKey: string;
};

type ScheduleCreatorState = {
  startDate: string;
  lockupPeriods: string;
  frequency: string;
  totalDates: string;
  koReturnRate: string;
  barriers: string;
  isRateAnnualized: boolean;
};

const SCHEDULE_KEYS = new Set(['ko_observation_schedule', 'ki_observation_schedule']);
const KO_SCHEDULE_DETAIL_KEYS = new Set(['ko_barrier']);
const SCHEDULE_COLUMN_ORDER = [
  'observation_date',
  'barrier',
  'return_rate',
  'is_rate_annualized',
  'coupon_rate',
  'accrual_factor',
];
const SCHEDULE_DEFAULT_COLUMNS: Record<string, string[]> = {
  ko_observation_schedule: ['observation_date', 'barrier', 'return_rate', 'is_rate_annualized'],
  ki_observation_schedule: ['observation_date', 'barrier'],
};
const SCHEDULE_FREQUENCIES = [
  { value: 'weekly', label: 'Weekly', months: 0, days: 7 },
  { value: 'monthly', label: 'Monthly', months: 1, days: 0 },
  { value: 'quarterly', label: 'Quarterly', months: 3, days: 0 },
  { value: 'semiannual', label: 'Semiannual', months: 6, days: 0 },
  { value: 'annual', label: 'Annual', months: 12, days: 0 },
];
const NUMERIC_KEYS = new Set([
  'accrual_factor',
  'barrier',
  'barrier_level',
  'cash_payoff',
  'component_product_id',
  'contract_multiplier',
  'coupon_rate',
  'coupon_barrier_pct',
  'initial_price',
  'ki_barrier',
  'ki_barrier_pct',
  'ko_barrier',
  'ko_barrier_pct',
  'ko_rate',
  'knock_out_rebate',
  'lockup_months',
  'lower_barrier',
  'maturity',
  'maturity_years',
  'multiplier',
  'post_ko_barrier_pct',
  'post_ko_rate',
  'no_hit_rebate',
  'num_observations',
  'participation_rate',
  'payout',
  'quantity',
  'rebate',
  'return_rate',
  'strike',
  'upper_barrier',
]);

function toTitleLabel(key: string) {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isRecordArray(value: unknown): value is Record<string, unknown>[] {
  return Array.isArray(value) && value.every(isRecord);
}

function getScheduleRecords(value: unknown): Record<string, unknown>[] | null {
  if (!isRecord(value) || !Array.isArray(value.records)) return null;
  return value.records.filter(isRecord);
}

function formatCellValue(value: unknown) {
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function getScheduleColumns(scheduleKey: string, records: Record<string, unknown>[]) {
  const keys = new Set(records.flatMap((record) => Object.keys(record)));
  const defaults = SCHEDULE_DEFAULT_COLUMNS[scheduleKey] ?? SCHEDULE_COLUMN_ORDER;
  return [
    ...defaults.filter((key) => keys.size === 0 || keys.has(key)),
    ...SCHEDULE_COLUMN_ORDER.filter((key) => keys.has(key) && !defaults.includes(key)),
    ...Array.from(keys).filter((key) => !defaults.includes(key) && !SCHEDULE_COLUMN_ORDER.includes(key)).sort(),
  ];
}

function getRecordColumns(fieldKey: string, records: Record<string, unknown>[]) {
  const keys = new Set(records.flatMap((record) => Object.keys(record)));
  const defaults = fieldKey === 'components' ? ['component_product_id', 'quantity'] : [];
  return [
    ...defaults.filter((key) => keys.size === 0 || keys.has(key)),
    ...Array.from(keys).filter((key) => !defaults.includes(key)).sort(),
  ];
}

function coerceInputValue(key: string, raw: string, currentValue?: unknown) {
  if (raw === '') return '';
  if (typeof currentValue === 'number' || NUMERIC_KEYS.has(key)) {
    const numberValue = Number(raw);
    return Number.isFinite(numberValue) ? numberValue : raw;
  }
  return raw;
}

function inputTypeForKey(key: string, value: unknown): 'date' | 'number' | 'text' {
  if (key.endsWith('_date') || key === 'date' || key.includes('observation_date')) return 'date';
  if (typeof value === 'number' || NUMERIC_KEYS.has(key)) return 'number';
  return 'text';
}

function emptyValueForColumn(column: string) {
  if (column.startsWith('is_') || column.startsWith('has_')) return false;
  if (NUMERIC_KEYS.has(column)) return 0;
  return '';
}

function parseDate(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const year = Number(match[1]);
  const monthIndex = Number(match[2]) - 1;
  const day = Number(match[3]);
  const date = new Date(year, monthIndex, day);
  if (date.getFullYear() !== year || date.getMonth() !== monthIndex || date.getDate() !== day) return null;
  return date;
}

function formatDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function addMonths(date: Date, months: number): Date {
  const targetMonth = date.getMonth() + months;
  const target = new Date(date.getFullYear(), targetMonth, 1);
  const lastDay = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate();
  target.setDate(Math.min(date.getDate(), lastDay));
  return target;
}

function addDays(date: Date, days: number): Date {
  const target = new Date(date);
  target.setDate(target.getDate() + days);
  return target;
}

function parseNumberList(value: string): number[] {
  return value
    .split(',')
    .map((part) => Number(part.trim()))
    .filter(Number.isFinite);
}

function defaultScheduleCreator(records: Record<string, unknown>[], config: Record<string, unknown>): ScheduleCreatorState {
  const firstRecord = records[0] ?? {};
  const distinctBarriers = Array.from(new Set(records.map((record) => record.barrier).filter((value) => value !== null && value !== undefined)));
  const fallbackBarrier = firstRecord.barrier ?? config.ko_barrier ?? '';
  const barrierText = distinctBarriers.length > 0
    ? distinctBarriers.map(String).join(', ')
    : String(fallbackBarrier);
  return {
    startDate: typeof firstRecord.observation_date === 'string' ? firstRecord.observation_date : '',
    lockupPeriods: '0',
    frequency: 'monthly',
    totalDates: String(records.length || 12),
    koReturnRate: firstRecord.return_rate !== undefined ? String(firstRecord.return_rate) : String(config.ko_rate ?? ''),
    barriers: barrierText,
    isRateAnnualized: Boolean(firstRecord.is_rate_annualized),
  };
}

function buildKoScheduleRecords(creator: ScheduleCreatorState): Record<string, unknown>[] {
  const startDate = parseDate(creator.startDate);
  const totalDates = Math.max(0, Math.floor(Number(creator.totalDates)));
  const lockupPeriods = Math.max(0, Math.floor(Number(creator.lockupPeriods)));
  const frequency = SCHEDULE_FREQUENCIES.find((item) => item.value === creator.frequency) ?? SCHEDULE_FREQUENCIES[1];
  const barriers = parseNumberList(creator.barriers);
  const returnRate = Number(creator.koReturnRate);

  if (!startDate || totalDates <= 0 || barriers.length === 0 || !Number.isFinite(returnRate)) {
    return [];
  }

  return Array.from({ length: totalDates }, (_, index) => {
    const periodIndex = lockupPeriods + index;
    const observationDate = frequency.months > 0
      ? addMonths(startDate, frequency.months * periodIndex)
      : addDays(startDate, frequency.days * periodIndex);
    return {
      observation_date: formatDate(observationDate),
      barrier: barriers[Math.min(index, barriers.length - 1)],
      return_rate: returnRate,
      is_rate_annualized: creator.isRateAnnualized,
    };
  });
}

export function ProductTermsForm({ productType, productKwargs, onChange }: Props) {
  const fields = useMemo(() => {
    if (isFlatContract(productKwargs) && FLAT_CONTRACT_FIELDS[productType]) {
      return FLAT_CONTRACT_FIELDS[productType];
    }
    return PRODUCT_TERM_FIELDS[productType] ?? [];
  }, [productType, productKwargs]);
  const [expandedConfigs, setExpandedConfigs] = useState<Record<string, boolean>>({});
  const [expandedSchedulePreviews, setExpandedSchedulePreviews] = useState<Record<string, boolean>>({});
  const [scheduleEditor, setScheduleEditor] = useState<ScheduleEditorState | null>(null);
  const [scheduleCreator, setScheduleCreator] = useState<ScheduleCreatorState>(() => (
    defaultScheduleCreator([], {})
  ));

  const updateField = (key: string, value: unknown) => {
    onChange({ ...productKwargs, [key]: value });
  };

  const updateNestedField = (configKey: string, nestedKey: string, value: unknown) => {
    const currentConfig = isRecord(productKwargs[configKey]) ? productKwargs[configKey] : {};
    onChange({
      ...productKwargs,
      [configKey]: {
        ...currentConfig,
        [nestedKey]: value,
      },
    });
  };

  const updateScheduleRecords = (configKey: string, scheduleKey: string, records: Record<string, unknown>[]) => {
    const currentConfig = isRecord(productKwargs[configKey]) ? productKwargs[configKey] : {};
    const currentSchedule = isRecord(currentConfig[scheduleKey]) ? currentConfig[scheduleKey] : {};
    const nextConfig: Record<string, unknown> = {
      ...currentConfig,
      [scheduleKey]: {
        ...currentSchedule,
        records,
      },
    };
    if (configKey === 'barrier_config' && scheduleKey === 'ko_observation_schedule') {
      const firstRate = Number(records[0]?.return_rate);
      if (Number.isFinite(firstRate)) nextConfig.ko_rate = firstRate;
    }
    onChange({
      ...productKwargs,
      [configKey]: nextConfig,
    });
  };

  const updateRecordArray = (fieldKey: string, records: Record<string, unknown>[]) => {
    updateField(fieldKey, records);
  };

  const toggleConfig = (key: string) => {
    setExpandedConfigs((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const renderField = (field: FieldSpec) => {
    const value = productKwargs[field.key];
    const stringValue = value == null ? '' : String(value);

    if (field.type === 'boolean') {
      return (
        <div key={field.key} className="wl-positions__term-field">
          <span>{field.label}</span>
          <div className="wl-positions__term-boolean-control">
            <input
              type="checkbox"
              aria-label={field.label}
              checked={Boolean(value)}
              onChange={(e) => updateField(field.key, e.target.checked)}
            />
          </div>
        </div>
      );
    }

    if (field.type === 'select') {
      return (
        <div key={field.key} className="wl-positions__term-field">
          <Select
            label={field.label}
            value={stringValue}
            onChange={(v) => updateField(field.key, v)}
            options={[
              { value: '', label: '-' },
              ...(field.options?.map((opt) => ({ value: opt, label: opt })) ?? []),
            ]}
          />
        </div>
      );
    }

    if (field.type === 'date') {
      return (
        <div key={field.key} className="wl-positions__term-field">
          <DatePicker
            label={field.label}
            value={stringValue}
            onChange={(v) => updateField(field.key, v)}
          />
        </div>
      );
    }

    return (
      <label key={field.key} className="wl-positions__term-field">
        <span>{field.label}</span>
        <input
          type={field.type === 'number' ? 'number' : 'text'}
          step={field.type === 'number' ? 'any' : undefined}
          value={stringValue}
          onInput={(e) => {
            const raw = e.currentTarget.value;
            updateField(field.key, field.type === 'number' ? coerceInputValue(field.key, raw, value) : raw);
          }}
          onChange={(e) => {
            const raw = e.target.value;
            updateField(field.key, field.type === 'number' ? coerceInputValue(field.key, raw, value) : raw);
          }}
        />
      </label>
    );
  };

  const renderScheduleSummary = (configKey: string, scheduleKey: string, value: unknown) => {
    const records = getScheduleRecords(value) ?? [];
    const firstDate = records.find((record) => record.observation_date)?.observation_date;
    const lastDate = [...records].reverse().find((record) => record.observation_date)?.observation_date;
    const previewKey = `${configKey}.${scheduleKey}`;
    const isPreviewExpanded = Boolean(expandedSchedulePreviews[previewKey]);
    const previewLimit = 5;
    const isPreviewTruncated = records.length > previewLimit && !isPreviewExpanded;
    const previewRecords = isPreviewExpanded ? records : records.slice(0, previewLimit);
    const columns = getScheduleColumns(scheduleKey, records);

    return (
      <div key={scheduleKey} className="wl-positions__schedule-field">
        <div className="wl-positions__schedule-head">
          <span>{toTitleLabel(scheduleKey)}</span>
          <div className="wl-positions__schedule-actions">
            {records.length > previewLimit && (
              <Button
                type="button"
                variant="ghost"
                className="wl-positions__schedule-toggle"
                onClick={() => {
                  setExpandedSchedulePreviews((current) => ({
                    ...current,
                    [previewKey]: !isPreviewExpanded,
                  }));
                }}
              >
                {isPreviewExpanded ? `Show first ${previewLimit} rows` : `Show all ${records.length} rows`}
              </Button>
            )}
            <Button
              type="button"
              variant="ghost"
              className="wl-positions__schedule-edit"
              onClick={() => {
                const config = isRecord(productKwargs[configKey]) ? productKwargs[configKey] : {};
                setScheduleCreator(defaultScheduleCreator(records, config));
                setScheduleEditor({ configKey, scheduleKey });
              }}
            >
              <Pencil size={14} aria-hidden="true" />
              Edit
            </Button>
          </div>
        </div>
        <div className="wl-positions__schedule-summary">
          <span>{records.length} rows</span>
          <span>First {formatCellValue(firstDate) || '-'}</span>
          <span>Last {formatCellValue(lastDate) || '-'}</span>
          {records.length > previewLimit && (
            <span>{isPreviewTruncated ? `Showing first ${previewLimit}` : 'Showing all'}</span>
          )}
        </div>
        {previewRecords.length > 0 && (
          <div className="wl-positions__term-table-wrap wl-positions__term-table-wrap--preview">
            <table className="wl-positions__term-table">
              <thead>
                <tr>
                  {columns.map((column) => (
                    <th key={column} scope="col">{toTitleLabel(column)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {previewRecords.map((record, rowIndex) => (
                  <tr key={rowIndex}>
                    {columns.map((column) => (
                      <td key={column}>{formatCellValue(record[column])}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
  };

  const renderNestedConfigBody = (configKey: string, value: Record<string, unknown>) => {
    const koScheduleRecords = getScheduleRecords(value.ko_observation_schedule);
    const hasKoSchedule = Boolean(koScheduleRecords && koScheduleRecords.length > 0);
    const entries = Object.entries(value).filter(([key]) => {
      if (configKey === 'barrier_config' && hasKoSchedule && KO_SCHEDULE_DETAIL_KEYS.has(key)) {
        return false;
      }
      return true;
    });

    return (
      <div className="wl-positions__term-grid">
        {entries.map(([key, entryValue]) => {
          const label = toTitleLabel(key);
          if (typeof entryValue === 'boolean') {
            return (
              <div key={key} className="wl-positions__term-field">
                <span>{label}</span>
                <div className="wl-positions__term-boolean-control">
                  <input
                    type="checkbox"
                    aria-label={label}
                    checked={entryValue}
                    onChange={(event) => updateNestedField(configKey, key, event.target.checked)}
                  />
                </div>
              </div>
            );
          }
          if (SCHEDULE_KEYS.has(key)) {
            return renderScheduleSummary(configKey, key, entryValue);
          }
          if (entryValue !== null && typeof entryValue === 'object') {
            return (
              <label key={key} className="wl-positions__term-field wl-positions__term-field--wide">
                <span>{label}</span>
                <textarea value={JSON.stringify(entryValue, null, 2)} readOnly rows={4} onChange={() => {}} />
              </label>
            );
          }
          if (inputTypeForKey(key, entryValue) === 'date') {
            return (
              <div key={key} className="wl-positions__term-field">
                <DatePicker
                  label={label}
                  value={entryValue == null ? '' : String(entryValue)}
                  onChange={(v) => updateNestedField(configKey, key, v)}
                />
              </div>
            );
          }
          return (
            <label key={key} className="wl-positions__term-field">
              <span>{label}</span>
              <input
                type={inputTypeForKey(key, entryValue) === 'number' ? 'number' : 'text'}
                step={inputTypeForKey(key, entryValue) === 'number' ? 'any' : undefined}
                value={entryValue == null ? '' : String(entryValue)}
                onInput={(event) => updateNestedField(configKey, key, coerceInputValue(key, event.currentTarget.value, entryValue))}
                onChange={(event) => updateNestedField(configKey, key, coerceInputValue(key, event.target.value, entryValue))}
              />
            </label>
          );
        })}
      </div>
    );
  };

  const renderRecordArrayEditor = (fieldKey: string, records: Record<string, unknown>[]) => {
    const columns = getRecordColumns(fieldKey, records);
    const updateCell = (rowIndex: number, column: string, value: unknown) => {
      updateRecordArray(fieldKey, records.map((record, index) => (
        index === rowIndex ? { ...record, [column]: value } : record
      )));
    };
    const addRow = () => {
      updateRecordArray(fieldKey, [...records, Object.fromEntries(columns.map((column) => [column, emptyValueForColumn(column)]))]);
    };
    const removeRow = (rowIndex: number) => {
      updateRecordArray(fieldKey, records.filter((_, index) => index !== rowIndex));
    };

    return (
      <div key={fieldKey} className="wl-positions__record-array">
        <div className="wl-positions__schedule-head">
          <span>{toTitleLabel(fieldKey)}</span>
          <Button type="button" variant="ghost" onClick={addRow}>
            <Plus size={14} aria-hidden="true" />
            Add Row
          </Button>
        </div>
        <div className="wl-positions__term-table-wrap">
          <table className="wl-positions__term-table wl-positions__term-table--editable">
            <thead>
              <tr>
                {columns.map((column) => (
                  <th key={column} scope="col">{toTitleLabel(column)}</th>
                ))}
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {records.map((record, rowIndex) => (
                <tr key={rowIndex}>
                  {columns.map((column) => (
                    <td key={column}>
                      {inputTypeForKey(column, record[column]) === 'date' ? (
                        <DatePicker
                          value={formatCellValue(record[column])}
                          onChange={(v) => updateCell(rowIndex, column, v)}
                        />
                      ) : (
                        <input
                          aria-label={`${toTitleLabel(fieldKey)} ${rowIndex + 1} ${toTitleLabel(column)}`}
                          type={inputTypeForKey(column, record[column]) === 'number' ? 'number' : 'text'}
                          step={inputTypeForKey(column, record[column]) === 'number' ? 'any' : undefined}
                          value={formatCellValue(record[column])}
                          onInput={(event) => updateCell(rowIndex, column, coerceInputValue(column, event.currentTarget.value, record[column]))}
                          onChange={(event) => updateCell(rowIndex, column, coerceInputValue(column, event.target.value, record[column]))}
                        />
                      )}
                    </td>
                  ))}
                  <td>
                    <Button
                      type="button"
                      variant="ghost"
                      iconOnly
                      aria-label={`Remove ${toTitleLabel(fieldKey)} row ${rowIndex + 1}`}
                      onClick={() => removeRow(rowIndex)}
                    >
                      <Trash2 size={14} aria-hidden="true" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  };

  const renderScheduleEditor = () => {
    if (!scheduleEditor) return null;
    const rawConfig = productKwargs[scheduleEditor.configKey];
    const config: Record<string, unknown> = isRecord(rawConfig) ? rawConfig : {};
    const scheduleValue = config[scheduleEditor.scheduleKey];
    const records = getScheduleRecords(scheduleValue) ?? [];
    const columns = getScheduleColumns(scheduleEditor.scheduleKey, records);
    const title = toTitleLabel(scheduleEditor.scheduleKey);
    const showKoCreator = scheduleEditor.scheduleKey === 'ko_observation_schedule';

    const updateCell = (rowIndex: number, column: string, value: unknown) => {
      updateScheduleRecords(scheduleEditor.configKey, scheduleEditor.scheduleKey, records.map((record, index) => (
        index === rowIndex ? { ...record, [column]: value } : record
      )));
    };
    const addRow = () => {
      updateScheduleRecords(
        scheduleEditor.configKey,
        scheduleEditor.scheduleKey,
        [...records, Object.fromEntries(columns.map((column) => [column, emptyValueForColumn(column)]))],
      );
    };
    const removeRow = (rowIndex: number) => {
      updateScheduleRecords(
        scheduleEditor.configKey,
        scheduleEditor.scheduleKey,
        records.filter((_, index) => index !== rowIndex),
      );
    };
    const updateCreator = (key: keyof ScheduleCreatorState, value: string | boolean) => {
      setScheduleCreator((current) => ({ ...current, [key]: value }));
    };
    const generateSchedule = () => {
      const generated = buildKoScheduleRecords(scheduleCreator);
      if (generated.length === 0) return;
      updateScheduleRecords(scheduleEditor.configKey, scheduleEditor.scheduleKey, generated);
    };

    return (
      <Modal
        open
        onOpenChange={(open) => {
          if (!open) setScheduleEditor(null);
        }}
        title={title}
        description="Edit schedule rows used for booking and pricing."
        contentClassName="wl-positions__schedule-modal"
        layoutKey={`booking-${scheduleEditor.configKey}-${scheduleEditor.scheduleKey}`}
        defaultWidth={920}
        defaultHeight={620}
        minWidth={560}
        minHeight={360}
      >
        <div className="wl-positions__schedule-editor">
          {showKoCreator && (
            <section className="wl-positions__schedule-creator" aria-label="Schedule creator">
              <div className="wl-positions__schedule-creator-head">
                <span>Schedule Creator</span>
                <Button type="button" variant="primary" onClick={generateSchedule}>
                  Generate Schedule
                </Button>
              </div>
              <div className="wl-positions__schedule-creator-grid">
                <div className="wl-positions__term-field">
                  <DatePicker
                    label="Start Date"
                    id="wl-schedule-creator-start-date"
                    value={scheduleCreator.startDate}
                    onChange={(v) => updateCreator('startDate', v)}
                  />
                </div>
                <label className="wl-positions__term-field" htmlFor="wl-schedule-creator-lockup">
                  <span>Lockup Periods</span>
                  <input
                    id="wl-schedule-creator-lockup"
                    type="number"
                    min={0}
                    step={1}
                    value={scheduleCreator.lockupPeriods}
                    onInput={(event) => updateCreator('lockupPeriods', event.currentTarget.value)}
                    onChange={(event) => updateCreator('lockupPeriods', event.target.value)}
                  />
                </label>
                <div className="wl-positions__term-field">
                  <Select
                    label="Frequency"
                    id="wl-schedule-creator-frequency"
                    value={scheduleCreator.frequency}
                    onChange={(v) => updateCreator('frequency', v)}
                    options={SCHEDULE_FREQUENCIES.map((frequency) => ({ value: frequency.value, label: frequency.label }))}
                  />
                </div>
                <label className="wl-positions__term-field" htmlFor="wl-schedule-creator-total-dates">
                  <span>Total Dates</span>
                  <input
                    id="wl-schedule-creator-total-dates"
                    type="number"
                    min={1}
                    step={1}
                    value={scheduleCreator.totalDates}
                    onInput={(event) => updateCreator('totalDates', event.currentTarget.value)}
                    onChange={(event) => updateCreator('totalDates', event.target.value)}
                  />
                </label>
                <label className="wl-positions__term-field" htmlFor="wl-schedule-creator-ko-return-rate">
                  <span>KO Return Rate</span>
                  <input
                    id="wl-schedule-creator-ko-return-rate"
                    type="number"
                    step="any"
                    value={scheduleCreator.koReturnRate}
                    onInput={(event) => updateCreator('koReturnRate', event.currentTarget.value)}
                    onChange={(event) => updateCreator('koReturnRate', event.target.value)}
                  />
                </label>
                <label className="wl-positions__term-field" htmlFor="wl-schedule-creator-barriers">
                  <span>Barriers</span>
                  <input
                    id="wl-schedule-creator-barriers"
                    value={scheduleCreator.barriers}
                    placeholder="103, 102, 101"
                    onInput={(event) => updateCreator('barriers', event.currentTarget.value)}
                    onChange={(event) => updateCreator('barriers', event.target.value)}
                  />
                </label>
                <div className="wl-positions__term-field">
                  <span>Rate Annualized</span>
                  <div className="wl-positions__term-boolean-control">
                    <input
                      type="checkbox"
                      aria-label="Rate Annualized"
                      checked={scheduleCreator.isRateAnnualized}
                      onChange={(event) => updateCreator('isRateAnnualized', event.target.checked)}
                    />
                  </div>
                </div>
              </div>
            </section>
          )}
          <div className="wl-positions__schedule-editor-actions">
            <span>{records.length} rows</span>
            <Button type="button" variant="primary" onClick={addRow}>
              <Plus size={14} aria-hidden="true" />
              Add Row
            </Button>
          </div>
          <div className="wl-positions__term-table-wrap wl-positions__term-table-wrap--editor">
            <table className="wl-positions__term-table wl-positions__term-table--editable">
              <thead>
                <tr>
                  {columns.map((column) => (
                    <th key={column} scope="col">{toTitleLabel(column)}</th>
                  ))}
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {records.map((record, rowIndex) => (
                  <tr key={rowIndex}>
                    {columns.map((column) => {
                      const cellValue = record[column];
                      if (typeof cellValue === 'boolean' || column.startsWith('is_') || column.startsWith('has_')) {
                        return (
                          <td key={column}>
                            <input
                              type="checkbox"
                              aria-label={`${title} ${rowIndex + 1} ${toTitleLabel(column)}`}
                              checked={Boolean(cellValue)}
                              onChange={(event) => updateCell(rowIndex, column, event.target.checked)}
                            />
                          </td>
                        );
                      }
                      if (inputTypeForKey(column, cellValue) === 'date') {
                        return (
                          <td key={column}>
                            <DatePicker
                              value={formatCellValue(cellValue)}
                              onChange={(v) => updateCell(rowIndex, column, v)}
                            />
                          </td>
                        );
                      }
                      return (
                        <td key={column}>
                          <input
                            aria-label={`${title} ${rowIndex + 1} ${toTitleLabel(column)}`}
                            type={inputTypeForKey(column, cellValue) === 'number' ? 'number' : 'text'}
                            step={inputTypeForKey(column, cellValue) === 'number' ? 'any' : undefined}
                            value={formatCellValue(cellValue)}
                            onInput={(event) => updateCell(rowIndex, column, coerceInputValue(column, event.currentTarget.value, cellValue))}
                            onChange={(event) => updateCell(rowIndex, column, coerceInputValue(column, event.target.value, cellValue))}
                          />
                        </td>
                      );
                    })}
                    <td>
                      <Button
                        type="button"
                        variant="ghost"
                        iconOnly
                        aria-label={`Remove ${title} row ${rowIndex + 1}`}
                        onClick={() => removeRow(rowIndex)}
                      >
                        <Trash2 size={14} aria-hidden="true" />
                      </Button>
                    </td>
                  </tr>
                ))}
                {records.length === 0 && (
                  <tr>
                    <td colSpan={columns.length + 1} className="wl-positions__term-empty">
                      No schedule rows.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </Modal>
    );
  };

  const nestedConfigs = Object.entries(productKwargs).filter(
    ([key, value]) => NESTED_CONFIG_KEYS.has(key) && isRecord(value),
  );

  const extraFields = Object.entries(productKwargs).filter(
    ([key]) => !NESTED_CONFIG_KEYS.has(key) && !fields.some((f) => f.key === key),
  );

  return (
    <>
      <div className="wl-positions__term-form">
        <fieldset>
          <legend>Product Terms</legend>
          <div className="wl-positions__product-terms-grid">
            {fields.map(renderField)}
          </div>

          {nestedConfigs.length > 0 && (
            <div className="wl-positions__term-groups">
              {nestedConfigs.map(([key, value]) => (
                <details
                  key={key}
                  className="wl-positions__term-group"
                  open={expandedConfigs[key]}
                  onToggle={() => toggleConfig(key)}
                >
                  <summary>
                    <span>{toTitleLabel(key)}</span>
                    <small>{Object.keys(value as Record<string, unknown>).length} fields</small>
                  </summary>
                  <div className="wl-positions__term-group-body">
                    {renderNestedConfigBody(key, value as Record<string, unknown>)}
                  </div>
                </details>
              ))}
            </div>
          )}
        </fieldset>
      </div>

      {extraFields.length > 0 && (
        <div className="wl-positions__term-form">
          <fieldset>
            <legend>Extra Fields</legend>
            <div className="wl-positions__term-extra-count">{extraFields.length} fields</div>
            <div className="wl-positions__term-extra-body">
              <div className="wl-positions__term-grid">
                {extraFields.map(([key, value]) => {
                  if (isRecordArray(value)) return renderRecordArrayEditor(key, value);
                  if (typeof value === 'boolean') {
                    return (
                      <div key={key} className="wl-positions__term-field">
                        <span>{toTitleLabel(key)}</span>
                        <div className="wl-positions__term-boolean-control">
                          <input
                            type="checkbox"
                            aria-label={toTitleLabel(key)}
                            checked={value}
                            onChange={(event) => updateField(key, event.target.checked)}
                          />
                        </div>
                      </div>
                    );
                  }
                  if (value !== null && typeof value === 'object') {
                    return (
                      <label key={key} className="wl-positions__term-field wl-positions__term-field--wide">
                        <span>{toTitleLabel(key)}</span>
                        <textarea value={JSON.stringify(value, null, 2)} readOnly rows={4} onChange={() => {}} />
                      </label>
                    );
                  }
                  if (inputTypeForKey(key, value) === 'date') {
                    return (
                      <div key={key} className="wl-positions__term-field">
                        <DatePicker
                          label={toTitleLabel(key)}
                          value={value == null ? '' : String(value)}
                          onChange={(v) => updateField(key, v)}
                        />
                      </div>
                    );
                  }
                  return (
                    <label key={key} className="wl-positions__term-field">
                      <span>{toTitleLabel(key)}</span>
                      <input
                        type={inputTypeForKey(key, value) === 'number' ? 'number' : 'text'}
                        step={inputTypeForKey(key, value) === 'number' ? 'any' : undefined}
                        value={value == null ? '' : String(value)}
                        onInput={(event) => updateField(key, coerceInputValue(key, event.currentTarget.value, value))}
                        onChange={(event) => updateField(key, coerceInputValue(key, event.target.value, value))}
                      />
                    </label>
                  );
                })}
              </div>
            </div>
          </fieldset>
        </div>
      )}

      {renderScheduleEditor()}
    </>
  );
}

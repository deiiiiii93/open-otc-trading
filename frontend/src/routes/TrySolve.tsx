import { useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react';
import { Download, FileSpreadsheet, Play, Plus, Rows3, Search, Trash2 } from 'lucide-react';
import { MasterDetailPage } from '../components/templates';
import { Panel } from '../components/Panel';
import { Empty } from '../components/Empty';
import { Button } from '../components/Button';
import { DatePicker } from '../components/DatePicker';
import { NumberInput } from '../components/NumberInput';
import { Select } from '../components/Select';
import { Badge, type BadgeVariant } from '../components/Badge';
import { RangeSlider } from '../components/RangeSlider';
import { Stepper } from '../components/Stepper';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import { declareActions } from '../lib/pageActions';
import type {
  MarketDataProfile,
  PageContext,
  PageContextReporter,
  PricingParameterProfile,
  TrySolveCatalog,
  TrySolveExportRequest,
  TrySolveField,
  TrySolveProduct,
  TrySolveQuoteField,
  TrySolveQuoteRequest,
  TrySolveRowOut,
  Underlying,
} from '../types';
import './TrySolve.css';

type Props = {
  catalog?: TrySolveCatalog;
  pricingProfiles?: PricingParameterProfile[];
  marketDataProfiles?: MarketDataProfile[];
  underlyings?: Underlying[];
  rows?: TrySolveRowOut[];
  selectedRowId?: string | null;
  loading?: boolean;
  error?: string | null;
  importing?: boolean;
  exporting?: boolean;
  solving?: boolean;
  feedback?: string | null;
  onSelectRow?: (rowId: string) => void;
  onImportExcel?: (file: File) => Promise<void> | void;
  onExport?: (
    scope: TrySolveExportRequest['scope'],
    selectedRowIds: string[],
  ) => Promise<void> | void;
  onSolveSelected?: (rowId: string) => Promise<void> | void;
  onSolveAll?: (rowIds: string[]) => Promise<void> | void;
  onAddManualRequest?: (productKey: string) => void;
  onDeleteRequest?: (rowId: string) => void;
  onFieldChange?: (rowId: string, fieldKey: string, value: unknown) => void;
  onMarketChange?: (rowId: string, patch: Partial<TrySolveRowOut['market']>) => void;
  onQuoteRequestChange?: (rowId: string, patch: Partial<TrySolveQuoteRequest>) => void;
  onPageContextChange?: PageContextReporter;
};

export const DEFAULT_TRY_SOLVE_CATALOG: TrySolveCatalog = {
  products: [
    {
      product_key: 'autocall',
      label: 'Autocall',
      excel_sheet: 'autocall',
      initial_solver_state: 'solver_ready',
      quantark_product_type: 'SnowballOption',
      default_engine_name: 'SnowballQuadEngine',
      fields: [
        field('counterparty', 'Counterparty'),
        field('side', 'Side', 'select', { required: true, default: 'buy', options: ['buy', 'sell'] }),
        field('underlying', 'Underlying', 'text', { required: true }),
        field('notional', 'Notional', 'number', { required: true, default: 1 }),
        field('initial_price', 'Initial Price', 'number'),
        field('start_date', 'Start Date', 'date', { required: true }),
        field('observation_frequency', 'Observation Frequency', 'select', { default: 'MONTHLY', options: ['MONTHLY', 'QUARTERLY', 'SEMI_ANNUAL'] }),
        field('lockup_months', 'Lockup Months', 'number', { default: 0 }),
        field('ko_barrier', 'Knock-Out Barrier', 'number', { default: 1.03 }),
        field('ki_barrier', 'Knock-In Barrier', 'number', { default: 0.75 }),
        field('tenor_months', 'Tenor Months', 'number'),
      ],
      quote_fields: [
        quoteField('annualized_coupon', 'Annualized Coupon', 'barrier_config.ko_rate', true, 0.001, 0.5, 0.1),
        quoteField('ko_barrier', 'Knock-Out Barrier', 'barrier_config.ko_barrier', false, 0.01, 10, 1.03),
      ],
    },
    {
      product_key: 'phoenix',
      label: 'Phoenix',
      excel_sheet: 'phoenix',
      initial_solver_state: 'solver_ready',
      quantark_product_type: 'PhoenixOption',
      default_engine_name: 'PhoenixQuadEngine',
      fields: [
        field('counterparty', 'Counterparty'),
        field('side', 'Side', 'select', { required: true, default: 'buy', options: ['buy', 'sell'] }),
        field('underlying', 'Underlying', 'text', { required: true }),
        field('notional', 'Notional', 'number', { required: true, default: 1 }),
        field('initial_price', 'Initial Price', 'number'),
        field('start_date', 'Start Date', 'date', { required: true }),
        field('observation_frequency', 'Observation Frequency', 'select', { default: 'MONTHLY', options: ['MONTHLY', 'QUARTERLY', 'SEMI_ANNUAL'] }),
        field('lockup_months', 'Lockup Months', 'number', { default: 0 }),
        field('ko_barrier', 'Knock-Out Barrier', 'number', { default: 1.03 }),
        field('ki_barrier', 'Knock-In Barrier', 'number', { default: 0.75 }),
        field('coupon_yield', 'Coupon Yield', 'number', { default: 0.1 }),
        field('tenor_months', 'Tenor Months', 'number'),
      ],
      quote_fields: [
        quoteField('annualized_coupon', 'Annualized Coupon', 'barrier_config.ko_rate', true, 0.001, 0.5, 0.1),
        quoteField('coupon_yield', 'Coupon Yield', 'coupon_config.coupon_rate', true, 0.001, 0.5, 0.1),
        quoteField('ko_barrier', 'Knock-Out Barrier', 'barrier_config.ko_barrier', true, 0.01, 10, 1.03),
      ],
    },
    {
      product_key: 'vanilla',
      label: 'Vanilla',
      excel_sheet: 'vanilla',
      initial_solver_state: 'solver_ready',
      quantark_product_type: 'EuropeanVanillaOption',
      default_engine_name: 'BlackScholesEngine',
      fields: [
        field('counterparty', 'Counterparty'),
        field('side', 'Side', 'select', { required: true, default: 'buy', options: ['buy', 'sell'] }),
        field('underlying', 'Underlying', 'text', { required: true }),
        field('notional', 'Notional', 'number', { required: true, default: 1 }),
        field('quantity', 'Quantity', 'number'),
        field('initial_price', 'Initial Price', 'number'),
        field('start_date', 'Start Date', 'date', { required: true }),
        field('option_type', 'Option Type', 'select', { default: 'call', options: ['call', 'put'] }),
        field('strike', 'Strike', 'number', { default: 1 }),
        field('tenor_months', 'Tenor Months', 'number'),
      ],
      quote_fields: [
        quoteField('premium_rate', 'Premium Rate', 'premium_rate', false, -1, 1, 0),
        quoteField('strike', 'Strike', 'strike', true, 0.01, 10, 1),
      ],
    },
    {
      product_key: 'vertical_spread',
      label: 'Vertical Spread',
      excel_sheet: 'vertical_spread',
      initial_solver_state: 'schema_captured',
      fields: [
        field('counterparty', 'Counterparty'),
        field('underlying', 'Underlying', 'text', { required: true }),
        field('notional', 'Notional', 'number', { required: true }),
        field('start_date', 'Start Date', 'date', { required: true }),
        field('strike', 'Strike', 'number'),
        field('tenor_months', 'Tenor Months', 'number'),
      ],
      quote_fields: [
        quoteField('premium_rate', 'Premium Rate', 'premium_rate', false, -1, 1, 0),
        quoteField('strike', 'Strike', 'strike', false, 0.01, 10, 1),
      ],
      notes: 'Schema captured; solver mapping pending.',
    },
    {
      product_key: 'digital',
      label: 'Digital',
      excel_sheet: 'digital',
      initial_solver_state: 'solver_ready',
      quantark_product_type: 'CashOrNothingDigitalOption',
      default_engine_name: 'DigitalOptionAnalyticalEngine',
      fields: [
        field('counterparty', 'Counterparty'),
        field('side', 'Side', 'select', { required: true, default: 'buy', options: ['buy', 'sell'] }),
        field('underlying', 'Underlying', 'text', { required: true }),
        field('notional', 'Notional', 'number', { required: true }),
        field('initial_price', 'Initial Price', 'number'),
        field('start_date', 'Start Date', 'date', { required: true }),
        field('option_type', 'Option Type', 'select', { default: 'call', options: ['call', 'put'] }),
        field('strike', 'Strike', 'number'),
        field('payout', 'Payout', 'number', { default: 0.1 }),
        field('tenor_months', 'Tenor Months', 'number'),
      ],
      quote_fields: [
        quoteField('premium_rate', 'Premium Rate', 'premium_rate', false, -1, 1, 0),
        quoteField('payout', 'Payout', 'payout', true, 0, 10, 0.1),
      ],
    },
    {
      product_key: 'binary_convex',
      label: 'Binary Convex',
      excel_sheet: 'binary_convex',
      initial_solver_state: 'schema_captured',
      fields: [field('counterparty', 'Counterparty'), field('underlying', 'Underlying', 'text', { required: true }), field('notional', 'Notional', 'number', { required: true }), field('start_date', 'Start Date', 'date', { required: true }), field('strike', 'Strike', 'number'), field('tenor_months', 'Tenor Months', 'number')],
      quote_fields: [quoteField('premium_rate', 'Premium Rate', 'premium_rate', false, -1, 1, 0), quoteField('strike', 'Strike', 'strike', false, 0.01, 10, 1)],
      notes: 'Schema captured; solver mapping pending.',
    },
    {
      product_key: 'single_sf',
      label: 'Single Sharkfin',
      excel_sheet: 'single_sf',
      initial_solver_state: 'solver_ready',
      quantark_product_type: 'SingleSharkfinOption',
      default_engine_name: 'SingleSharkfinOptionAnalyticalEngine',
      fields: [field('counterparty', 'Counterparty'), field('side', 'Side', 'select', { required: true, default: 'buy', options: ['buy', 'sell'] }), field('underlying', 'Underlying', 'text', { required: true }), field('notional', 'Notional', 'number', { required: true, default: 1 }), field('initial_price', 'Initial Price', 'number'), field('start_date', 'Start Date', 'date', { required: true }), field('option_type', 'Option Type', 'select', { default: 'call', options: ['call', 'put'] }), field('strike', 'Strike', 'number', { default: 1 }), field('barrier', 'Barrier', 'number', { default: 1.2 }), field('participation_rate', 'Participation Rate', 'number', { default: 1 }), field('tenor_months', 'Tenor Months', 'number')],
      quote_fields: [quoteField('premium_rate', 'Premium Rate', 'premium_rate', false, -1, 1, 0), quoteField('strike', 'Strike', 'strike', true, 0.01, 10, 1), quoteField('barrier', 'Barrier', 'barrier', true, 0.01, 10, 1.2), quoteField('participation_rate', 'Participation Rate', 'participation_rate', true, 0, 5, 1)],
    },
    {
      product_key: 'double_sf',
      label: 'Double Sharkfin',
      excel_sheet: 'double_sf',
      initial_solver_state: 'solver_ready',
      quantark_product_type: 'DoubleSharkfinOption',
      default_engine_name: 'DoubleSharkfinOptionAnalyticalEngine',
      fields: [field('counterparty', 'Counterparty'), field('side', 'Side', 'select', { required: true, default: 'buy', options: ['buy', 'sell'] }), field('underlying', 'Underlying', 'text', { required: true }), field('notional', 'Notional', 'number', { required: true, default: 1 }), field('initial_price', 'Initial Price', 'number'), field('start_date', 'Start Date', 'date', { required: true }), field('option_type', 'Option Type', 'select', { default: 'call', options: ['call', 'put'] }), field('strike', 'Strike', 'number', { default: 1 }), field('upper_barrier', 'Upper Barrier', 'number', { default: 1.2 }), field('lower_barrier', 'Lower Barrier', 'number', { default: 0.8 }), field('participation_rate', 'Participation Rate', 'number', { default: 1 }), field('tenor_months', 'Tenor Months', 'number')],
      quote_fields: [quoteField('premium_rate', 'Premium Rate', 'premium_rate', false, -1, 1, 0), quoteField('strike', 'Strike', 'strike', true, 0.01, 10, 1), quoteField('upper_barrier', 'Upper Barrier', 'upper_barrier', true, 0.01, 10, 1.2), quoteField('lower_barrier', 'Lower Barrier', 'lower_barrier', true, 0.01, 10, 0.8), quoteField('participation_rate', 'Participation Rate', 'participation_rate', true, 0, 5, 1)],
    },
    {
      product_key: 'airbag',
      label: 'Airbag',
      excel_sheet: 'airbag',
      initial_solver_state: 'schema_captured',
      quantark_product_type: 'AirbagOption',
      fields: [field('counterparty', 'Counterparty'), field('underlying', 'Underlying', 'text', { required: true }), field('notional', 'Notional', 'number', { required: true }), field('start_date', 'Start Date', 'date', { required: true }), field('strike', 'Strike', 'number'), field('tenor_months', 'Tenor Months', 'number')],
      quote_fields: [quoteField('premium_rate', 'Premium Rate', 'premium_rate', false, -1, 1, 0), quoteField('strike', 'Strike', 'strike', false, 0.01, 10, 1)],
      notes: 'Schema captured; solver mapping pending.',
    },
    {
      product_key: 'airbag_spread',
      label: 'Airbag Spread',
      excel_sheet: 'airbag_spread',
      initial_solver_state: 'schema_captured',
      fields: [field('counterparty', 'Counterparty'), field('underlying', 'Underlying', 'text', { required: true }), field('notional', 'Notional', 'number', { required: true }), field('start_date', 'Start Date', 'date', { required: true }), field('strike', 'Strike', 'number'), field('tenor_months', 'Tenor Months', 'number')],
      quote_fields: [quoteField('premium_rate', 'Premium Rate', 'premium_rate', false, -1, 1, 0), quoteField('strike', 'Strike', 'strike', false, 0.01, 10, 1)],
      notes: 'Schema captured; solver mapping pending.',
    },
    {
      product_key: 'asian',
      label: 'Asian',
      excel_sheet: 'asian',
      initial_solver_state: 'solver_ready',
      quantark_product_type: 'AsianOption',
      default_engine_name: 'AsianOptionAnalyticalEngine',
      fields: [field('counterparty', 'Counterparty'), field('side', 'Side', 'select', { required: true, default: 'buy', options: ['buy', 'sell'] }), field('underlying', 'Underlying', 'text', { required: true }), field('notional', 'Notional', 'number', { required: true, default: 1 }), field('initial_price', 'Initial Price', 'number'), field('start_date', 'Start Date', 'date', { required: true }), field('option_type', 'Option Type', 'select', { default: 'call', options: ['call', 'put'] }), field('strike', 'Strike', 'number', { default: 1 }), field('tenor_months', 'Tenor Months', 'number')],
      quote_fields: [quoteField('premium_rate', 'Premium Rate', 'premium_rate', false, -1, 1, 0), quoteField('strike', 'Strike', 'strike', true, 0.01, 10, 1)],
    },
    {
      product_key: 'call_put_portfolio',
      label: 'Call Put Portfolio',
      excel_sheet: 'call_put_portfolio',
      initial_solver_state: 'schema_captured',
      fields: [field('counterparty', 'Counterparty'), field('underlying', 'Underlying', 'text', { required: true }), field('notional', 'Notional', 'number', { required: true }), field('start_date', 'Start Date', 'date', { required: true }), field('strike', 'Strike', 'number'), field('tenor_months', 'Tenor Months', 'number')],
      quote_fields: [quoteField('premium_rate', 'Premium Rate', 'premium_rate', false, -1, 1, 0), quoteField('strike', 'Strike', 'strike', false, 0.01, 10, 1)],
      notes: 'Schema captured; solver mapping pending.',
    },
    {
      product_key: 'ladder_binary',
      label: 'Ladder Binary',
      excel_sheet: 'ladder_binary',
      initial_solver_state: 'schema_captured',
      fields: [field('counterparty', 'Counterparty'), field('underlying', 'Underlying', 'text', { required: true }), field('notional', 'Notional', 'number', { required: true }), field('start_date', 'Start Date', 'date', { required: true }), field('strike', 'Strike', 'number'), field('tenor_months', 'Tenor Months', 'number')],
      quote_fields: [quoteField('premium_rate', 'Premium Rate', 'premium_rate', false, -1, 1, 0), quoteField('strike', 'Strike', 'strike', false, 0.01, 10, 1)],
      notes: 'Schema captured; solver mapping pending.',
    },
    {
      product_key: 'forward',
      label: 'Forward',
      excel_sheet: 'forward',
      initial_solver_state: 'solver_ready',
      quantark_product_type: 'Futures',
      default_engine_name: 'DeltaOneEngine',
      fields: [field('counterparty', 'Counterparty'), field('side', 'Side', 'select', { required: true, default: 'buy', options: ['buy', 'sell'] }), field('underlying', 'Underlying', 'text', { required: true }), field('notional', 'Notional', 'number', { required: true, default: 1 }), field('initial_price', 'Initial Price', 'number'), field('start_date', 'Start Date', 'date', { required: true }), field('tenor_months', 'Tenor Months', 'number')],
      quote_fields: [quoteField('fixed_yield', 'Fixed Yield', 'fixed_yield', false, -1, 1, 0), quoteField('basis', 'Basis', 'basis', true, -10, 10, 0)],
    },
    {
      product_key: 'range_accrual',
      label: 'Range Accrual',
      excel_sheet: 'range_accrual',
      initial_solver_state: 'solver_ready',
      quantark_product_type: 'RangeAccrualOption',
      default_engine_name: 'RangeAccrualAnalyticalEngine',
      fields: [field('counterparty', 'Counterparty'), field('side', 'Side', 'select', { required: true, default: 'buy', options: ['buy', 'sell'] }), field('underlying', 'Underlying', 'text', { required: true }), field('notional', 'Notional', 'number', { required: true, default: 1 }), field('initial_price', 'Initial Price', 'number'), field('start_date', 'Start Date', 'date', { required: true }), field('upper_barrier', 'Upper Barrier', 'number', { default: 1.2 }), field('lower_barrier', 'Lower Barrier', 'number', { default: 0.8 }), field('coupon_yield', 'Coupon Yield', 'number', { default: 0.1 }), field('tenor_months', 'Tenor Months', 'number')],
      quote_fields: [quoteField('range_accrual_rate', 'Range Accrual Rate', 'range_config.accrual_rate', true, 0.001, 0.5, 0.1), quoteField('upper_barrier', 'Upper Barrier', 'range_config.upper_barrier', true, 0.01, 10, 1.2), quoteField('lower_barrier', 'Lower Barrier', 'range_config.lower_barrier', true, 0.01, 10, 0.8)],
    },
    {
      product_key: 'one_touch',
      label: 'One Touch',
      excel_sheet: 'one_touch',
      initial_solver_state: 'solver_ready',
      quantark_product_type: 'OneTouchOption',
      default_engine_name: 'OneTouchAnalyticalEngine',
      fields: [field('counterparty', 'Counterparty'), field('side', 'Side', 'select', { required: true, default: 'buy', options: ['buy', 'sell'] }), field('underlying', 'Underlying', 'text', { required: true }), field('notional', 'Notional', 'number', { required: true, default: 1 }), field('initial_price', 'Initial Price', 'number'), field('start_date', 'Start Date', 'date', { required: true }), field('barrier', 'Barrier', 'number', { default: 1.2 }), field('rebate', 'Rebate', 'number', { default: 0.1 }), field('tenor_months', 'Tenor Months', 'number')],
      quote_fields: [quoteField('premium_rate', 'Premium Rate', 'premium_rate', false, -1, 1, 0), quoteField('barrier', 'Barrier', 'barrier', true, 0.01, 10, 1.2), quoteField('rebate', 'Rebate', 'rebate', true, 0, 10, 0.1)],
    },
    {
      product_key: 'double_no_touch',
      label: 'Double No Touch',
      excel_sheet: 'double_no_touch',
      initial_solver_state: 'solver_ready',
      quantark_product_type: 'DoubleOneTouchOption',
      default_engine_name: 'OneTouchAnalyticalEngine',
      fields: [field('counterparty', 'Counterparty'), field('side', 'Side', 'select', { required: true, default: 'buy', options: ['buy', 'sell'] }), field('underlying', 'Underlying', 'text', { required: true }), field('notional', 'Notional', 'number', { required: true, default: 1 }), field('initial_price', 'Initial Price', 'number'), field('start_date', 'Start Date', 'date', { required: true }), field('upper_barrier', 'Upper Barrier', 'number', { default: 1.2 }), field('lower_barrier', 'Lower Barrier', 'number', { default: 0.8 }), field('rebate', 'Rebate', 'number', { default: 0.1 }), field('tenor_months', 'Tenor Months', 'number')],
      quote_fields: [quoteField('premium_rate', 'Premium Rate', 'premium_rate', false, -1, 1, 0), quoteField('upper_barrier', 'Upper Barrier', 'upper_barrier', true, 0.01, 10, 1.2), quoteField('lower_barrier', 'Lower Barrier', 'lower_barrier', true, 0.01, 10, 0.8), quoteField('rebate', 'Rebate', 'rebate', true, 0, 10, 0.1)],
    },
    {
      product_key: 'double_one_touch',
      label: 'Double One Touch',
      excel_sheet: 'double_one_touch',
      initial_solver_state: 'solver_ready',
      quantark_product_type: 'DoubleOneTouchOption',
      default_engine_name: 'OneTouchAnalyticalEngine',
      fields: [field('counterparty', 'Counterparty'), field('side', 'Side', 'select', { required: true, default: 'buy', options: ['buy', 'sell'] }), field('underlying', 'Underlying', 'text', { required: true }), field('notional', 'Notional', 'number', { required: true, default: 1 }), field('initial_price', 'Initial Price', 'number'), field('start_date', 'Start Date', 'date', { required: true }), field('upper_barrier', 'Upper Barrier', 'number', { default: 1.2 }), field('lower_barrier', 'Lower Barrier', 'number', { default: 0.8 }), field('rebate', 'Rebate', 'number', { default: 0.1 }), field('tenor_months', 'Tenor Months', 'number')],
      quote_fields: [quoteField('premium_rate', 'Premium Rate', 'premium_rate', false, -1, 1, 0), quoteField('upper_barrier', 'Upper Barrier', 'upper_barrier', true, 0.01, 10, 1.2), quoteField('lower_barrier', 'Lower Barrier', 'lower_barrier', true, 0.01, 10, 0.8), quoteField('rebate', 'Rebate', 'rebate', true, 0, 10, 0.1)],
    },
    {
      product_key: 'knock_out_autocall',
      label: 'Knock-Out Autocall',
      excel_sheet: 'knock_out_autocall',
      initial_solver_state: 'solver_ready',
      quantark_product_type: 'KnockOutResetSnowballOption',
      default_engine_name: 'KOResetSnowballQuadEngine',
      fields: [field('counterparty', 'Counterparty'), field('side', 'Side', 'select', { required: true, default: 'buy', options: ['buy', 'sell'] }), field('underlying', 'Underlying', 'text', { required: true }), field('notional', 'Notional', 'number', { required: true, default: 1 }), field('initial_price', 'Initial Price', 'number'), field('start_date', 'Start Date', 'date', { required: true }), field('observation_frequency', 'Observation Frequency', 'select', { default: 'MONTHLY', options: ['MONTHLY', 'QUARTERLY', 'SEMI_ANNUAL'] }), field('lockup_months', 'Lockup Months', 'number', { default: 0 }), field('ko_barrier', 'Knock-Out Barrier', 'number', { default: 1.03 }), field('ki_barrier', 'Knock-In Barrier', 'number', { default: 0.75 }), field('tenor_months', 'Tenor Months', 'number')],
      quote_fields: [quoteField('annualized_coupon', 'Annualized Coupon', 'barrier_config.ko_rate', true, 0.001, 0.5, 0.1), quoteField('ko_barrier', 'Knock-Out Barrier', 'barrier_config.ko_barrier', true, 0.01, 10, 1.03)],
    },
  ],
  status_options: [
    'draft',
    'missing_terms',
    'missing_market',
    'mapping_pending',
    'invalid_target',
    'unsupported_market',
    'unsupported_quote_field',
    'quantark_build_failed',
    'solve_failed',
    'solver_ready',
    'schema_captured',
    'solved',
  ],
};

export const DEFAULT_TRY_SOLVE_ROWS: TrySolveRowOut[] = [
  {
    row_id: 'XL-12',
    source: 'excel',
    source_sheet: 'autocall',
    source_row: 12,
    product_key: 'autocall',
    product_label: 'Autocall',
    status: 'solver_ready',
    diagnostics: [],
    quantark_product_type: 'SnowballOption',
    engine_name: 'SnowballQuadEngine',
    fields: {
      counterparty: 'North Desk',
      side: 'buy',
      underlying: '000852.SH',
      notional: 50000000,
      start_date: '2026-05-14',
      ko_barrier: 1.03,
      tenor_months: 12,
    },
    raw_values: { Sheet: 'autocall', Row: 12 },
    market: { valuation_date: '2026-05-13', spot: 1, volatility: 0.22, rate: 0.02 },
    quote_request: {
      quote_field_key: 'annualized_coupon',
      target_label: 'price',
      target_value: 0,
      lower_bound: 0.001,
      upper_bound: 0.5,
      initial_guess: 0.1,
    },
  },
  {
    row_id: 'XL-18',
    source: 'excel',
    source_sheet: 'vanilla',
    source_row: 18,
    product_key: 'vanilla',
    product_label: 'Vanilla',
    status: 'solved',
    diagnostics: ['Solved strike matched target price within tolerance.'],
    quantark_product_type: 'EuropeanVanillaOption',
    engine_name: 'BlackScholesEngine',
    solved_value: 1.042,
    model_price: 0.0003,
    residual: 0.0001,
    fields: {
      counterparty: 'South Desk',
      side: 'sell',
      underlying: '510050.SH',
      notional: 12000000,
      start_date: '2026-05-14',
      option_type: 'call',
      strike: 1.02,
      tenor_months: 6,
    },
    raw_values: { Sheet: 'vanilla', Row: 18 },
    market: { valuation_date: '2026-05-13', spot: 1.01, volatility: 0.18, rate: 0.018 },
    quote_request: {
      quote_field_key: 'strike',
      target_label: 'price',
      target_value: 0,
      lower_bound: 0.01,
      upper_bound: 10,
      initial_guess: 1,
    },
  },
  {
    row_id: 'XL-31',
    source: 'excel',
    source_sheet: 'digital',
    source_row: 31,
    product_key: 'digital',
    product_label: 'Digital',
    status: 'schema_captured',
    diagnostics: ['Product schema captured from workbook.', 'Solver mapping is pending for this product.'],
    fields: {
      counterparty: 'East Desk',
      underlying: '000300.SH',
      notional: 8000000,
      start_date: '2026-05-14',
      strike: 0.98,
      tenor_months: 3,
    },
    raw_values: { Sheet: 'digital', Row: 31 },
    market: { valuation_date: '2026-05-13', spot: 1, volatility: 0.2, rate: 0.02 },
    quote_request: {
      quote_field_key: 'premium_rate',
      target_label: 'price',
      target_value: 0,
      lower_bound: -1,
      upper_bound: 1,
      initial_guess: 0,
    },
  },
];

export function TrySolve({
  catalog = DEFAULT_TRY_SOLVE_CATALOG,
  pricingProfiles = [],
  marketDataProfiles = [],
  underlyings = [],
  rows = DEFAULT_TRY_SOLVE_ROWS,
  selectedRowId,
  loading = false,
  error = null,
  importing = false,
  exporting = false,
  solving = false,
  feedback = null,
  onSelectRow,
  onImportExcel,
  onExport,
  onSolveSelected,
  onSolveAll,
  onAddManualRequest,
  onDeleteRequest,
  onFieldChange,
  onMarketChange,
  onQuoteRequestChange,
  onPageContextChange,
}: Props) {
  const importInputRef = useRef<HTMLInputElement | null>(null);
  const workspaceRef = useRef<HTMLDivElement | null>(null);
  const [internalSelectedRowId, setInternalSelectedRowId] = useState(rows[0]?.row_id ?? '');
  const [queueSearch, setQueueSearch] = useState('');
  const [editorPanelPercent, setEditorPanelPercent] = useState(47);
  const [productKey, setProductKey] = useState(catalog.products[0]?.product_key ?? '');
  const effectiveProductKey = catalog.products.some((product) => product.product_key === productKey)
    ? productKey
    : catalog.products[0]?.product_key ?? '';
  const effectiveSelectedRowId = selectedRowId ?? internalSelectedRowId;
  const selectedRow = rows.find((row) => row.row_id === effectiveSelectedRowId) ?? rows[0] ?? null;
  const selectedProduct = findProduct(catalog, selectedRow?.product_key);
  const selectedQuoteField = selectedProduct?.quote_fields.find(
    (fieldItem) => fieldItem.key === selectedRow?.quote_request.quote_field_key,
  ) ?? selectedProduct?.quote_fields[0] ?? null;
  const chips = useMemo(() => {
    const readyCount = rows.filter((row) => row.status === 'solver_ready' || row.status === 'solved').length;
    const capturedCount = rows.filter((row) => row.status === 'schema_captured').length;
    return [`${rows.length} rows`, `${readyCount} ready`, `${capturedCount} schema captured`];
  }, [rows]);
  const pageContext = useMemo((): PageContext => ({
    route: 'try-solve',
    title: 'Try to Solve',
    path: '/',
    entity_ids: { row_id: selectedRow?.row_id ?? null },
    snapshot: {
      row_count: rows.length,
      selected_row: selectedRow
        ? {
            row_id: selectedRow.row_id,
            product_key: selectedRow.product_key,
            product_label: selectedRow.product_label,
            status: selectedRow.status,
            quote_field_key: selectedRow.quote_request.quote_field_key,
            source_sheet: selectedRow.source_sheet,
            source_row: selectedRow.source_row,
          }
        : null,
      products: catalog.products.map((product) => ({
        product_key: product.product_key,
        label: product.label,
        initial_solver_state: product.initial_solver_state,
      })),
    },
    loaded_context: { completeness: 'complete' },
    actions: declareActions([
      {
        name: 'solve_imported_row',
        required_ids: ['row_id'],
        confirmation: 'implicit',
        backend_endpoint: 'POST /api/rfq/try-solve/solve',
      },
      {
        name: 'create_request_queue_item',
        required_ids: ['row_id'],
        confirmation: 'explicit',
        backend_endpoint: 'local:try-solve/request-queue-item',
      },
    ]),
    chips,
  }), [catalog.products, chips, rows.length, selectedRow]);
  usePageContextReporter(pageContext, onPageContextChange);

  const selectedRowIds = selectedRow ? [selectedRow.row_id] : [];
  const canSolveSelected = !!selectedRow && !!selectedQuoteField?.solver_ready && !solving;
  const filteredRows = useMemo(() => {
    const query = queueSearch.trim().toLowerCase();
    if (!query) return rows;
    return rows.filter((row) => [
      row.row_id,
      row.product_label,
      row.product_key,
      row.source_sheet,
      row.source_row != null ? `row ${row.source_row}` : '',
      formatStatus(row.status),
    ].some((value) => String(value ?? '').toLowerCase().includes(query)));
  }, [queueSearch, rows]);
  const handleSelectRow = (rowId: string) => {
    if (selectedRowId === undefined) setInternalSelectedRowId(rowId);
    onSelectRow?.(rowId);
  };
  const handleImportFile = (file: File | null | undefined) => {
    if (!file) return;
    void onImportExcel?.(file);
    if (importInputRef.current) importInputRef.current.value = '';
  };
  const handleWorkspaceResizePointerDown = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!workspaceRef.current) return;
    event.preventDefault();
    const rect = workspaceRef.current.getBoundingClientRect();
    const handlePointerMove = (moveEvent: PointerEvent) => {
      const next = ((moveEvent.clientX - rect.left) / rect.width) * 100;
      setEditorPanelPercent(Math.min(70, Math.max(30, next)));
    };
    const handlePointerUp = () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
    };
    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp, { once: true });
  };

  const actions = (
    <div className="wl-try-solve__actions">
      <Select
        variant="inline"
        searchable
        label="Product Type"
        value={effectiveProductKey}
        onChange={(v) => setProductKey(v)}
        options={catalog.products.map((product) => ({
          value: product.product_key,
          label: `${product.label} · ${product.excel_sheet}`,
        }))}
      />
      <Button
        type="button"
        variant="default"
        disabled={!effectiveProductKey}
        onClick={() => onAddManualRequest?.(effectiveProductKey)}
      >
        <Plus size={14} aria-hidden="true" />
        New
      </Button>
      <input
        ref={importInputRef}
        className="wl-try-solve__file"
        type="file"
        accept=".xlsx,.xls,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel"
        aria-label="Import Excel Row file"
        onChange={(event) => handleImportFile(event.currentTarget.files?.[0])}
      />
      <Button
        type="button"
        variant="default"
        disabled={importing || loading}
        onClick={() => importInputRef.current?.click()}
      >
        <FileSpreadsheet size={15} aria-hidden="true" />
        {importing ? 'Importing...' : 'Import Excel Row'}
      </Button>
      <Button
        type="button"
        variant="ghost"
        disabled={exporting || rows.length === 0}
        onClick={() => void onExport?.('selected', selectedRowIds)}
      >
        <Download size={15} aria-hidden="true" />
        {exporting ? 'Exporting...' : 'Export'}
      </Button>
    </div>
  );

  const feedbackNode = (error || feedback)
    ? (
      <>
        {error && <div className="wl-try-solve__message wl-try-solve__message--error" role="alert">{error}</div>}
        {feedback && <div className="wl-try-solve__message">{feedback}</div>}
      </>
    )
    : undefined;

  const queueRail = (
    <Panel title="Request Queue" meta={loading ? 'loading' : `${rows.length} rows`} className="wl-try-solve__queue-panel">
          <label className="wl-try-solve__queue-search">
            <Search size={14} aria-hidden="true" />
            <NumberInput
              type="search"
              value={queueSearch}
              onChange={(event) => setQueueSearch(event.currentTarget.value)}
              aria-label="Search request queue"
              placeholder="Search queue"
            />
          </label>
          <div className="wl-try-solve__queue" role="list" aria-label="Request rows">
            {filteredRows.length ? filteredRows.map((row) => (
              <div
                key={row.row_id}
                className={[
                  'wl-try-solve__row',
                  row.row_id === selectedRow?.row_id ? 'wl-try-solve__row--active' : '',
                ].filter(Boolean).join(' ')}
              >
                <button
                  type="button"
                  className="wl-try-solve__row-select"
                  onClick={() => handleSelectRow(row.row_id)}
                  aria-pressed={row.row_id === selectedRow?.row_id}
                  aria-label={`${row.row_id} ${row.product_label} ${formatStatus(row.status)}`}
                >
                  <span className="wl-try-solve__row-main">
                    <span className="wl-try-solve__row-id">{row.row_id}</span>
                    <span className="wl-try-solve__row-product">{row.product_label}</span>
                  </span>
                  <Badge variant={badgeVariant(row.status)}>{formatStatus(row.status)}</Badge>
                  <span className="wl-try-solve__row-meta">
                    {row.source_sheet ?? row.product_key}
                    {row.source_row != null ? ` row ${row.source_row}` : ''}
                  </span>
                </button>
                <button
                  type="button"
                  className="wl-try-solve__row-delete"
                  onClick={() => onDeleteRequest?.(row.row_id)}
                  disabled={solving}
                  aria-label={`Delete ${row.row_id} request`}
                >
                  <Trash2 size={14} aria-hidden="true" />
                </button>
              </div>
            )) : (
              <Empty message={rows.length ? 'No requests match this search.' : 'No imported or manual requests.'} />
            )}
          </div>
    </Panel>
  );

  return (
    <MasterDetailPage
      title="TRY TO SOLVE"
      chips={chips}
      actions={actions}
      feedback={feedbackNode}
      rail={queueRail}
      railLabel="Request Queue"
      resizableRail
      minRailWidth={160}
      maxRailWidth={360}
    >
      <div
        ref={workspaceRef}
        className="wl-try-solve__workspace"
        style={{ '--try-solve-editor-panel': `${editorPanelPercent}%` } as CSSProperties}
      >
        <Panel
          title={selectedProduct ? `${selectedProduct.label} Terms` : 'Product Terms'}
          meta={selectedProduct?.quantark_product_type ?? 'catalog'}
          className="wl-try-solve__editor-panel"
        >
          {selectedRow && selectedProduct ? (
            <ProductEditor
              row={selectedRow}
              product={selectedProduct}
              selectedQuoteField={selectedQuoteField}
              underlyings={underlyings}
              onFieldChange={onFieldChange}
            />
          ) : (
            <Empty message="No request row selected." />
          )}
        </Panel>

        <button
          type="button"
          className="wl-try-solve__panel-resizer"
          aria-label="Resize terms and quote panels"
          aria-valuemin={30}
          aria-valuemax={70}
          aria-valuenow={Math.round(editorPanelPercent)}
          onPointerDown={handleWorkspaceResizePointerDown}
        />

        <Panel
          title="Quote & Solve"
          meta={selectedRow?.status ? formatStatus(selectedRow.status) : ''}
          className="wl-try-solve__quote-panel"
        >
          {selectedRow && selectedProduct ? (
            <SolvePanel
              row={selectedRow}
              product={selectedProduct}
              selectedQuoteField={selectedQuoteField}
              pricingProfiles={pricingProfiles}
              marketDataProfiles={marketDataProfiles}
              solving={solving}
              canSolveSelected={canSolveSelected}
              onMarketChange={onMarketChange}
              onQuoteRequestChange={onQuoteRequestChange}
              onSolveSelected={onSolveSelected}
              onSolveAll={onSolveAll}
              rowIds={rows.map((row) => row.row_id)}
            />
          ) : (
            <Empty message="Select a row to inspect solver readiness." />
          )}
        </Panel>
      </div>
    </MasterDetailPage>
  );
}


const FIELDS_GROUP_ORDER = ['counterparty', 'underlying', 'dates', 'barriers', 'payoff', 'other'] as const;

type FieldGroupKey = typeof FIELDS_GROUP_ORDER[number];

const GROUP_LABELS: Record<FieldGroupKey, string> = {
  counterparty: 'Counterparty',
  underlying: 'Underlying & Notional',
  dates: 'Schedule',
  barriers: 'Barriers',
  payoff: 'Payoff',
  other: 'Other',
};

function getFieldGroup(key: string): FieldGroupKey {
  if (key === 'counterparty' || key === 'side') return 'counterparty';
  if (key === 'underlying' || key === 'notional' || key === 'initial_price' || key === 'quantity') return 'underlying';
  if (key === 'start_date' || key === 'end_date' || key === 'tenor_months' || key === 'tenor_days' || key === 'observation_frequency' || key === 'lockup_months') return 'dates';
  if (key === 'ko_barrier' || key === 'ki_barrier' || key === 'barrier' || key === 'upper_barrier' || key === 'lower_barrier') return 'barriers';
  if (key === 'strike' || key === 'option_type' || key === 'coupon_yield' || key === 'payout' || key === 'participation_rate' || key === 'rebate') return 'payoff';
  return 'other';
}

type FieldGroup = { key: FieldGroupKey; label: string; fields: TrySolveField[] };

function groupFields(fields: TrySolveField[]): FieldGroup[] {
  const groups = new Map<FieldGroupKey, TrySolveField[]>();
  for (const fieldItem of fields) {
    const gk = getFieldGroup(fieldItem.key);
    if (!groups.has(gk)) groups.set(gk, []);
    groups.get(gk)!.push(fieldItem);
  }
  return FIELDS_GROUP_ORDER
    .filter((key) => groups.has(key))
    .map((key) => ({ key, label: GROUP_LABELS[key], fields: groups.get(key)! }));
}

function ProductEditor({
  row,
  product,
  selectedQuoteField,
  underlyings,
  onFieldChange,
}: {
  row: TrySolveRowOut;
  product: TrySolveProduct;
  selectedQuoteField: TrySolveQuoteField | null;
  underlyings: Underlying[];
  onFieldChange?: (rowId: string, fieldKey: string, value: unknown) => void;
}) {
  const fields = ensureEndDateField(product.fields);
  const groups = groupFields(fields);
  const quotedFieldKey = quotedTermsFieldKey(selectedQuoteField, fields);
  return (
    <form className="wl-try-solve__form" aria-label={`${product.label} field editor`}>
      {groups.map((group) => (
        <div key={group.key} className="wl-try-solve__field-group">
          <div className="wl-try-solve__field-group-title">{group.label}</div>
          {group.fields.map((fieldItem) => {
            const isLabelledField = fieldItem.key === 'underlying' || fieldItem.field_type === 'select' || fieldItem.field_type === 'boolean' || fieldItem.field_type === 'date';
            const isQuotedField = fieldItem.key === quotedFieldKey;
            if (isLabelledField) {
              return (
                <FieldControl
                  key={fieldItem.key}
                  field={fieldItem}
                  value={fieldValue(row, fieldItem)}
                  underlyings={underlyings}
                  quoted={isQuotedField}
                  onChange={(value) => onFieldChange?.(row.row_id, fieldItem.key, value)}
                />
              );
            }
            return (
              <label
                key={fieldItem.key}
                className={fieldClassName(isQuotedField)}
                data-quote-field={isQuotedField ? 'true' : undefined}
              >
                <span className="wl-try-solve__field-label">
                  {fieldItem.label}
                  {isQuotedField ? <strong>Quoted</strong> : null}
                  {fieldItem.required ? <em aria-label="required">*</em> : null}
                </span>
                <div className="wl-try-solve__field-control">
                  <FieldControl
                    field={fieldItem}
                    value={fieldValue(row, fieldItem)}
                    underlyings={underlyings}
                    onChange={(value) => onFieldChange?.(row.row_id, fieldItem.key, value)}
                  />
                  <DerivedFieldNote row={row} field={fieldItem} />
                </div>
              </label>
            );
          })}
        </div>
      ))}
    </form>
  );
}

function quotedTermsFieldKey(
  quoteField: TrySolveQuoteField | null,
  fields: TrySolveField[],
): string | null {
  if (!quoteField) return null;
  const fieldKeys = new Set(fields.map((fieldItem) => fieldItem.key));
  const canonicalParts = quoteField.canonical_path.split('.');
  const candidates = [
    quoteField.key,
    quoteField.canonical_path,
    canonicalParts[canonicalParts.length - 1],
    quoteFieldToTermKey(quoteField.key),
    quoteFieldToTermKey(quoteField.canonical_path),
  ].filter((candidate): candidate is string => Boolean(candidate));
  return candidates.find((candidate) => fieldKeys.has(candidate)) ?? null;
}

function quoteFieldToTermKey(value: string): string | null {
  if (value === 'annualized_coupon' || value.endsWith('.ko_rate')) return 'coupon_yield';
  if (value === 'range_accrual_rate' || value.endsWith('.accrual_rate')) return 'coupon_yield';
  if (value.endsWith('.ko_barrier')) return 'ko_barrier';
  if (value.endsWith('.upper_barrier')) return 'upper_barrier';
  if (value.endsWith('.lower_barrier')) return 'lower_barrier';
  return null;
}

function fieldClassName(quoted: boolean): string {
  return [
    'wl-try-solve__field',
    quoted ? 'wl-try-solve__field--quoted' : '',
  ].filter(Boolean).join(' ');
}

function ensureEndDateField(fields: TrySolveField[]): TrySolveField[] {
  if (!fields.some((fieldItem) => fieldItem.key === 'start_date')) return fields;
  if (fields.some((fieldItem) => fieldItem.key === 'end_date')) return fields;
  const startDateIndex = fields.findIndex((fieldItem) => fieldItem.key === 'start_date');
  const endDateField: TrySolveField = {
    key: 'end_date',
    label: 'End Date',
    field_type: 'date',
  };
  return [
    ...fields.slice(0, startDateIndex + 1),
    endDateField,
    ...fields.slice(startDateIndex + 1),
  ];
}

function fieldValue(row: TrySolveRowOut, fieldItem: TrySolveField): unknown {
  if (fieldItem.key === 'end_date' && isBlankValue(row.fields.end_date)) {
    return deriveEndDate(row.fields.start_date, row.fields.tenor_months, row.fields.tenor_days) ?? '';
  }
  return row.fields[fieldItem.key] ?? fieldItem.default ?? '';
}

function DerivedFieldNote({ row, field }: { row: TrySolveRowOut; field: TrySolveField }) {
  if (!isBarrierField(field.key)) return null;
  const level = derivedBarrierLevel(row, fieldValue(row, field));
  if (!level) return null;
  return (
    <div className="wl-try-solve__field-note">
      Level {formatCompactNumber(level.level)} ({formatMoneyness(level.multiple)} of {formatCompactNumber(level.reference)})
    </div>
  );
}

function FieldControl({
  field: fieldItem,
  value,
  underlyings,
  quoted = false,
  onChange,
}: {
  field: TrySolveField;
  value: unknown;
  underlyings: Underlying[];
  quoted?: boolean;
  onChange: (value: unknown) => void;
}) {
  if (fieldItem.key === 'underlying') {
    const activeUnderlyings = underlyings.filter((underlying) => underlying.status === 'active');
    const stringValue = String(value ?? '');
    const hasCurrentValue = activeUnderlyings.some((underlying) => underlying.symbol === stringValue);
    const options: { value: string; label: string; disabled?: boolean }[] = [];
    if (activeUnderlyings.length === 0 && stringValue) {
      options.push({ value: stringValue, label: `${stringValue} (not active)` });
    }
    if (activeUnderlyings.length === 0 && !stringValue) {
      options.push({ value: '', label: 'No active underlyings' });
    }
    if (activeUnderlyings.length > 0 && !stringValue) {
      options.push({ value: '', label: 'Choose underlying', disabled: true });
    }
    if (stringValue && activeUnderlyings.length > 0 && !hasCurrentValue) {
      options.push({ value: stringValue, label: `${stringValue} (not active)`, disabled: true });
    }
    for (const underlying of activeUnderlyings) {
      options.push({ value: underlying.symbol, label: underlyingOptionLabel(underlying) });
    }
    return (
      <Select
        label={fieldItem.label}
        searchable
        className={fieldClassName(quoted)}
        value={stringValue}
        onChange={(v) => onChange(v)}
        disabled={activeUnderlyings.length === 0}
        placeholder="Choose underlying"
        options={options}
      />
    );
  }
  if (fieldItem.field_type === 'select') {
    return (
      <Select
        label={fieldItem.label}
        searchable
        className={fieldClassName(quoted)}
        value={String(value)}
        onChange={(v) => onChange(v)}
        options={(fieldItem.options?.length ? fieldItem.options : [String(value)]).map((option) => ({
          value: option,
          label: option,
        }))}
      />
    );
  }
  if (fieldItem.field_type === 'boolean') {
    return (
      <Select
        label={fieldItem.label}
        className={fieldClassName(quoted)}
        value={String(Boolean(value))}
        onChange={(v) => onChange(v === 'true')}
        options={[
          { value: 'true', label: 'true' },
          { value: 'false', label: 'false' },
        ]}
      />
    );
  }
  if (fieldItem.field_type === 'date') {
    return (
      <DatePicker
        label={fieldItem.label}
        className={fieldClassName(quoted)}
        value={value == null ? '' : String(value)}
        onChange={(v) => onChange(v)}
      />
    );
  }
  return (
    <NumberInput
      aria-label={fieldItem.label}
      type={fieldItem.field_type === 'number' ? 'number' : 'text'}
      value={value == null ? '' : String(value)}
      onChange={(event) => onChange(parseFieldValue(fieldItem, event.currentTarget.value))}
    />
  );
}

function SolvePanel({
  row,
  product,
  selectedQuoteField,
  pricingProfiles,
  marketDataProfiles,
  solving,
  canSolveSelected,
  onMarketChange,
  onQuoteRequestChange,
  onSolveSelected,
  onSolveAll,
  rowIds,
}: {
  row: TrySolveRowOut;
  product: TrySolveProduct;
  selectedQuoteField: TrySolveQuoteField | null;
  pricingProfiles: PricingParameterProfile[];
  marketDataProfiles: MarketDataProfile[];
  solving: boolean;
  canSolveSelected: boolean;
  onMarketChange?: (rowId: string, patch: Partial<TrySolveRowOut['market']>) => void;
  onQuoteRequestChange?: (rowId: string, patch: Partial<TrySolveQuoteRequest>) => void;
  onSolveSelected?: (rowId: string) => Promise<void> | void;
  onSolveAll?: (rowIds: string[]) => Promise<void> | void;
  rowIds: string[];
}) {
  const selectedReady = selectedQuoteField?.solver_ready ?? false;
  const readiness = row.status === 'schema_captured' || !selectedReady ? 'Solver pending' : 'Solver ready';
  const rowUnderlying = normalizeSymbol(row.fields.underlying);
  const selectedMarketProfileId = row.market.market_data_profile_id ?? null;
  const isPremiumPercentTarget = row.quote_request.target_label === 'premium %';
  const filteredMarketDataProfiles = rowUnderlying
    ? marketDataProfiles.filter((profile) => (
      normalizeSymbol(profile.symbol) === rowUnderlying || profile.id === selectedMarketProfileId
    ))
    : marketDataProfiles;
  const hasMarketDataForUnderlying = !rowUnderlying || filteredMarketDataProfiles.some((profile) => (
    normalizeSymbol(profile.symbol) === rowUnderlying
  ));

  const lowerBound = row.quote_request.lower_bound ?? selectedQuoteField?.lower_bound ?? 0;
  const upperBound = row.quote_request.upper_bound ?? selectedQuoteField?.upper_bound ?? 1;
  const initialGuess = row.quote_request.initial_guess ?? selectedQuoteField?.initial_guess ?? lowerBound;
  const quoteValueMode = row.quote_request.quote_value_mode ?? 'absolute';
  const canUsePercentageMode = selectedQuoteField ? isPriceLikeQuoteField(selectedQuoteField) : false;
  const quoteReference = quoteReferencePrice(row);
  const catalogBoundMin = selectedQuoteField?.lower_bound ?? -10;
  const catalogBoundMax = selectedQuoteField?.upper_bound ?? 10;
  const boundMin = Math.min(catalogBoundMin, lowerBound, upperBound);
  const boundMax = Math.max(catalogBoundMax, lowerBound, upperBound);

  const stepQuoteDone = !!selectedQuoteField && row.quote_request.quote_field_key != null;
  const stepMarketDone = row.market.valuation_date != null && row.market.spot != null;
  const stepRangeDone = row.quote_request.lower_bound != null && row.quote_request.upper_bound != null;

  const steps = [
    { label: 'Quote Field', status: stepQuoteDone ? 'done' as const : 'active' as const },
    { label: 'Market Data', status: !stepQuoteDone ? 'todo' as const : stepMarketDone ? 'done' as const : 'active' as const },
    { label: 'Range', status: !stepQuoteDone || !stepMarketDone ? 'todo' as const : stepRangeDone ? 'done' as const : 'active' as const },
    { label: 'Solve', status: !stepQuoteDone || !stepMarketDone || !stepRangeDone ? 'todo' as const : 'active' as const },
  ];

  const handleBoundsChange = (values: number[]) => {
    const [nextLower, nextUpper] = values;
    const patch: Partial<TrySolveQuoteRequest> = {};
    if (nextLower !== lowerBound) patch.lower_bound = nextLower;
    if (nextUpper !== upperBound) patch.upper_bound = nextUpper;
    if (initialGuess < nextLower || initialGuess > nextUpper) {
      patch.initial_guess = (nextLower + nextUpper) / 2;
    }
    if (Object.keys(patch).length > 0) {
      onQuoteRequestChange?.(row.row_id, patch);
    }
  };

  const handleGuessChange = (values: number[]) => {
    const nextGuess = Math.min(upperBound, Math.max(lowerBound, values[0] ?? initialGuess));
    if (nextGuess !== initialGuess) {
      onQuoteRequestChange?.(row.row_id, { initial_guess: nextGuess });
    }
  };

  const handleQuoteValueModeChange = (nextMode: TrySolveQuoteRequest['quote_value_mode']) => {
    const currentMode = quoteValueMode;
    const patch: Partial<TrySolveQuoteRequest> = { quote_value_mode: nextMode };
    if (
      canUsePercentageMode
      && quoteReference != null
      && quoteReference > 0
      && nextMode
      && nextMode !== currentMode
    ) {
      const convert = nextMode === 'percentage'
        ? (value: number) => cleanRangeNumber((value / quoteReference) * 100)
        : (value: number) => cleanRangeNumber((value / 100) * quoteReference);
      patch.lower_bound = convert(lowerBound);
      patch.upper_bound = convert(upperBound);
      patch.initial_guess = convert(initialGuess);
    }
    onQuoteRequestChange?.(row.row_id, patch);
  };

  return (
    <div className="wl-try-solve__solve wl-try-solve__solve--wizard">
      <Stepper className="wl-try-solve__wizard-stepper" steps={steps} />

      <WizardStep number={1} title="What to quote" status={stepQuoteDone ? 'done' : 'active'}>
        <div className="wl-try-solve__wizard-fields">
          <Select
            label="Quote Field"
            searchable
            className="wl-try-solve__field wl-try-solve__field--wide"
            value={selectedQuoteField?.key ?? ''}
            onChange={(v) => {
              const nextQuoteField = product.quote_fields.find((quote) => quote.key === v);
              const rangeDefaults = quoteRangeDefaults(row, nextQuoteField);
              onQuoteRequestChange?.(row.row_id, {
                quote_field_key: v,
                quote_value_mode: 'absolute',
                lower_bound: rangeDefaults?.lower_bound ?? null,
                upper_bound: rangeDefaults?.upper_bound ?? null,
                initial_guess: rangeDefaults?.initial_guess ?? null,
              });
            }}
            options={product.quote_fields.map((quote) => ({
              value: quote.key,
              label: quote.label,
            }))}
          />
          <Select
            label="Target Label"
            searchable
            className="wl-try-solve__field"
            value={row.quote_request.target_label}
            onChange={(v) => onQuoteRequestChange?.(row.row_id, {
              target_label: v as TrySolveQuoteRequest['target_label'],
            })}
            options={[
              { value: 'price', label: 'price' },
              { value: 'premium', label: 'premium' },
              { value: 'premium %', label: 'premium %' },
              { value: 'reoffer', label: 'reoffer' },
            ]}
          />
          <label className="wl-try-solve__field">
            <span className="wl-try-solve__field-label">Target Value</span>
            <div className="wl-try-solve__field-input-with-unit">
              <NumberInput
                type="number"
                value={row.quote_request.target_value}
                onChange={(event) => onQuoteRequestChange?.(row.row_id, { target_value: parseNumberInput(event.currentTarget.value) ?? 0 })}
                aria-label="Target Value"
              />
              {isPremiumPercentTarget ? <span aria-hidden="true">%</span> : null}
            </div>
          </label>
        </div>
      </WizardStep>

      <WizardStep number={2} title="Market Data" status={stepMarketDone ? 'done' : stepQuoteDone ? 'active' : 'todo'}>
        <div className="wl-try-solve__wizard-fields">
          <Select
            label="Pricing Parameter Profile"
            searchable
            className="wl-try-solve__field"
            value={String(row.market.pricing_parameter_profile_id ?? '')}
            onChange={(v) => onMarketChange?.(row.row_id, {
              pricing_parameter_profile_id: parseSelectId(v),
            })}
            options={[
              { value: '', label: 'Manual only' },
              ...pricingProfiles.map((profile) => ({
                value: String(profile.id),
                label: profileOptionLabel(profile),
              })),
            ]}
          />
          <Select
            label="Market Data Profile"
            searchable
            className="wl-try-solve__field"
            value={String(row.market.market_data_profile_id ?? '')}
            onChange={(v) => onMarketChange?.(row.row_id, {
              market_data_profile_id: parseSelectId(v),
            })}
            options={[
              { value: '', label: 'Manual spot' },
              ...(rowUnderlying && !hasMarketDataForUnderlying
                ? [{ value: '__no_profiles__', label: `No profiles for ${row.fields.underlying}`, disabled: true }]
                : []),
              ...filteredMarketDataProfiles.map((profile) => ({
                value: String(profile.id),
                label: marketDataOptionLabel(profile),
              })),
            ]}
          />
        </div>
        <div className="wl-try-solve__market-grid wl-try-solve__market-grid--wizard">
          <div className="wl-try-solve__field">
            <DatePicker
              label="Valuation Date"
              value={dateInputValue(row.market.valuation_date)}
              onChange={(v) => onMarketChange?.(row.row_id, { valuation_date: v || null })}
            />
          </div>
          <label className="wl-try-solve__field">
            <span className="wl-try-solve__field-label">Spot</span>
            <NumberInput
              type="number"
              value={row.market.spot ?? ''}
              onChange={(event) => onMarketChange?.(row.row_id, { spot: parseNumberInput(event.currentTarget.value) })}
              aria-label="Spot"
            />
          </label>
          <label className="wl-try-solve__field">
            <span className="wl-try-solve__field-label">Volatility</span>
            <NumberInput
              type="number"
              value={row.market.volatility ?? ''}
              onChange={(event) => onMarketChange?.(row.row_id, { volatility: parseNumberInput(event.currentTarget.value) })}
              aria-label="Volatility"
            />
          </label>
          <label className="wl-try-solve__field">
            <span className="wl-try-solve__field-label">Rate</span>
            <NumberInput
              type="number"
              value={row.market.rate ?? ''}
              onChange={(event) => onMarketChange?.(row.row_id, { rate: parseNumberInput(event.currentTarget.value) })}
              aria-label="Rate"
            />
          </label>
          <label className="wl-try-solve__field">
            <span className="wl-try-solve__field-label">Dividend Yield</span>
            <NumberInput
              type="number"
              value={row.market.dividend_yield ?? ''}
              onChange={(event) => onMarketChange?.(row.row_id, { dividend_yield: parseNumberInput(event.currentTarget.value) })}
              aria-label="Dividend Yield"
            />
          </label>
        </div>
      </WizardStep>

      <WizardStep number={3} title="Search Range" status={stepRangeDone ? 'done' : stepQuoteDone && stepMarketDone ? 'active' : 'todo'}>
        <div className="wl-try-solve__range-controls">
          <div className="wl-try-solve__range-inputs">
            <Select
              label="Range Mode"
              className="wl-try-solve__field wl-try-solve__field--wide"
              value={quoteValueMode}
              onChange={(v) => handleQuoteValueModeChange(v as TrySolveQuoteRequest['quote_value_mode'])}
              options={[
                { value: 'absolute', label: 'Absolute Value' },
                {
                  value: 'percentage',
                  label: 'Percentage %',
                  disabled: !canUsePercentageMode || quoteReference == null,
                },
              ]}
            />
          </div>
          <div className="wl-try-solve__range-inputs">
            <label className="wl-try-solve__field">
              <span className="wl-try-solve__field-label">Quote Lower Bound</span>
              <div className="wl-try-solve__field-input-with-unit">
                <NumberInput
                  type="number"
                  value={row.quote_request.lower_bound ?? ''}
                  onChange={(event) => onQuoteRequestChange?.(row.row_id, { lower_bound: parseNumberInput(event.currentTarget.value) })}
                  aria-label="Quote Lower Bound"
                />
                {quoteValueMode === 'percentage' ? <span aria-hidden="true">%</span> : null}
              </div>
            </label>
            <label className="wl-try-solve__field">
              <span className="wl-try-solve__field-label">Quote Upper Bound</span>
              <div className="wl-try-solve__field-input-with-unit">
                <NumberInput
                  type="number"
                  value={row.quote_request.upper_bound ?? ''}
                  onChange={(event) => onQuoteRequestChange?.(row.row_id, { upper_bound: parseNumberInput(event.currentTarget.value) })}
                  aria-label="Quote Upper Bound"
                />
                {quoteValueMode === 'percentage' ? <span aria-hidden="true">%</span> : null}
              </div>
            </label>
          </div>
          <RangeSlider
            mode="range"
            min={boundMin}
            max={boundMax}
            step={computeSliderStep(boundMin, boundMax)}
            values={[lowerBound, upperBound]}
            onChange={handleBoundsChange}
            label="Bounds"
            formatValue={(v) => formatValue(v)}
          />
          <div className="wl-try-solve__range-inputs">
            <label className="wl-try-solve__field wl-try-solve__field--wide">
              <span className="wl-try-solve__field-label">Quote Initial Guess</span>
              <div className="wl-try-solve__field-input-with-unit">
                <NumberInput
                  type="number"
                  value={row.quote_request.initial_guess ?? ''}
                  onChange={(event) => onQuoteRequestChange?.(row.row_id, { initial_guess: parseNumberInput(event.currentTarget.value) })}
                  aria-label="Quote Initial Guess"
                />
                {quoteValueMode === 'percentage' ? <span aria-hidden="true">%</span> : null}
              </div>
            </label>
          </div>
          <RangeSlider
            mode="single"
            min={lowerBound}
            max={upperBound}
            step={computeSliderStep(lowerBound, upperBound)}
            values={[initialGuess]}
            onChange={handleGuessChange}
            label="Initial Guess"
            formatValue={(v) => formatValue(v)}
          />
        </div>
      </WizardStep>

      <WizardStep number={4} title="Run Solver" status={stepQuoteDone && stepMarketDone && stepRangeDone ? 'active' : 'todo'}>
        <div className="wl-try-solve__solve-grid" aria-label="Solve status">
          <Metric label="Product Status" value={formatStatus(row.status)} />
          <Metric label="Solver State" value={readiness} />
          <Metric label="Target" value={formatTargetMetric(row.quote_request)} />
          <Metric label="Quote Bounds" value={boundsLabel(row, selectedQuoteField)} />
        </div>

        <div className="wl-try-solve__status-strip">
          <span className="wl-try-solve__step wl-try-solve__step--done">Schema captured</span>
          <span className={`wl-try-solve__step ${selectedReady ? 'wl-try-solve__step--done' : 'wl-try-solve__step--pending'}`}>
            {selectedReady ? 'Solver ready' : 'Solver pending'}
          </span>
          <span className={`wl-try-solve__step ${row.status === 'solved' ? 'wl-try-solve__step--done' : 'wl-try-solve__step--pending'}`}>
            {row.status === 'solved' ? 'Solved' : 'Awaiting solve'}
          </span>
        </div>

        {row.solved_value != null || row.model_price != null ? (
          <div className="wl-try-solve__result wl-try-solve__result--wizard">
            {row.solved_value != null && <Metric label="Solved Value" value={formatValue(row.solved_value)} />}
            {row.model_price != null && <Metric label="Model Price" value={formatValue(row.model_price)} />}
            {row.residual != null && <Metric label="Residual" value={formatValue(row.residual)} />}
          </div>
        ) : null}

        <div className="wl-try-solve__diagnostics">
          <div className="wl-try-solve__diagnostics-title">
            <Rows3 size={14} aria-hidden="true" />
            Diagnostics
          </div>
          {row.diagnostics.length ? (
            <ul>
              {row.diagnostics.map((item) => <li key={item}>{item}</li>)}
            </ul>
          ) : (
            <p>No missing terms detected.</p>
          )}
        </div>

        <div className="wl-try-solve__solve-actions">
          <Button
            type="button"
            variant="primary"
            disabled={!canSolveSelected}
            onClick={() => void onSolveSelected?.(row.row_id)}
          >
            <Play size={15} aria-hidden="true" />
            {solving ? 'Solving...' : 'Solve Selected'}
          </Button>
          <Button
            type="button"
            variant="ghost"
            disabled={solving || rowIds.length === 0}
            onClick={() => void onSolveAll?.(rowIds)}
          >
            Solve All
          </Button>
        </div>
      </WizardStep>
    </div>
  );
}

function WizardStep({
  number,
  title,
  status,
  children,
}: {
  number: number;
  title: string;
  status: 'todo' | 'active' | 'done';
  children: ReactNode;
}) {
  return (
    <div className={`wl-try-solve__wizard-step wl-try-solve__wizard-step--${status}`}>
      <div className="wl-try-solve__wizard-step-header">
        <span className="wl-try-solve__wizard-step-number" aria-hidden="true">{number}</span>
        <span className="wl-try-solve__wizard-step-title">{title}</span>
      </div>
      <div className="wl-try-solve__wizard-step-body">
        {children}
      </div>
    </div>
  );
}

function computeSliderStep(min: number, max: number): number {
  const span = Math.abs(max - min);
  if (span <= 0) return 0.01;
  const magnitude = Math.pow(10, Math.floor(Math.log10(span)) - 2);
  return Math.max(0.0001, magnitude);
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="wl-try-solve__metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function findProduct(catalog: TrySolveCatalog, productKey?: string): TrySolveProduct | null {
  if (!productKey) return null;
  return catalog.products.find((product) => product.product_key === productKey) ?? null;
}

function field(
  key: string,
  label: string,
  fieldType: TrySolveField['field_type'] = 'text',
  overrides: Partial<TrySolveField> = {},
): TrySolveField {
  return {
    key,
    label,
    field_type: fieldType,
    excel_aliases: [],
    required: false,
    options: [],
    ...overrides,
  };
}

function quoteField(
  key: string,
  label: string,
  canonicalPath: string,
  solverReady: boolean,
  lowerBound: number,
  upperBound: number,
  initialGuess: number,
): TrySolveQuoteField {
  return {
    key,
    label,
    excel_header: label,
    canonical_path: canonicalPath,
    lower_bound: lowerBound,
    upper_bound: upperBound,
    initial_guess: initialGuess,
    solver_ready: solverReady,
  };
}

function quoteRangeDefaults(
  row: TrySolveRowOut,
  quoteField: TrySolveQuoteField | undefined,
): Pick<TrySolveQuoteRequest, 'lower_bound' | 'upper_bound' | 'initial_guess'> | null {
  if (!quoteField) return null;
  if (isReferencePriceQuoteField(quoteField)) {
    const referencePrice = quoteReferencePrice(row);
    if (referencePrice != null) {
      return {
        lower_bound: cleanRangeNumber(referencePrice * 0.1),
        upper_bound: cleanRangeNumber(referencePrice * 2),
        initial_guess: cleanRangeNumber(referencePrice),
      };
    }
  }
  if (isCouponRateQuoteField(quoteField)) {
    return {
      lower_bound: 0.001,
      upper_bound: 0.5,
      initial_guess: 0.1,
    };
  }
  return {
    lower_bound: quoteField.lower_bound,
    upper_bound: quoteField.upper_bound,
    initial_guess: quoteField.initial_guess ?? null,
  };
}

function isReferencePriceQuoteField(quoteField: TrySolveQuoteField): boolean {
  const key = quoteField.key.toLowerCase();
  const path = quoteField.canonical_path.toLowerCase();
  return key === 'strike' || path === 'strike';
}

function isPriceLikeQuoteField(quoteField: TrySolveQuoteField): boolean {
  const path = quoteField.canonical_path.toLowerCase();
  return isReferencePriceQuoteField(quoteField)
    || path === 'barrier'
    || path === 'upper_barrier'
    || path === 'lower_barrier'
    || path === 'barrier_config.ko_barrier'
    || path === 'barrier_config.ki_barrier'
    || path === 'coupon_config.coupon_barrier'
    || path === 'range_config.upper_barrier'
    || path === 'range_config.lower_barrier';
}

function quoteReferencePrice(row: TrySolveRowOut): number | null {
  const initialPrice = numberValue(row.fields.initial_price);
  if (initialPrice != null && initialPrice > 0) return initialPrice;
  const spot = numberValue(row.market.spot);
  return spot != null && spot > 0 ? spot : null;
}

function isCouponRateQuoteField(quoteField: TrySolveQuoteField): boolean {
  const key = quoteField.key.toLowerCase();
  const path = quoteField.canonical_path.toLowerCase();
  return key === 'annualized_coupon'
    || key === 'coupon_yield'
    || key === 'range_accrual_rate'
    || path === 'barrier_config.ko_rate'
    || path === 'coupon_config.coupon_rate'
    || path === 'range_config.accrual_rate';
}

function cleanRangeNumber(value: number): number {
  return Number(value.toPrecision(12));
}

function boundsLabel(row: TrySolveRowOut, quoteField: TrySolveQuoteField | null): string {
  const lower = row.quote_request.lower_bound ?? quoteField?.lower_bound;
  const upper = row.quote_request.upper_bound ?? quoteField?.upper_bound;
  return `${formatValue(lower)} / ${formatValue(upper)}`;
}

function parseFieldValue(fieldItem: TrySolveField, value: string): unknown {
  if (fieldItem.field_type === 'number') return parseNumberInput(value);
  return value;
}

function isBlankValue(value: unknown): boolean {
  return value == null || String(value).trim() === '';
}

function deriveEndDate(startDate: unknown, tenorMonths: unknown, tenorDays: unknown): string | null {
  const start = parseIsoDate(startDate);
  if (!start) return null;
  const months = numberValue(tenorMonths);
  if (months != null && months > 0) {
    const next = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate()));
    next.setUTCMonth(next.getUTCMonth() + months);
    return next.toISOString().slice(0, 10);
  }
  const days = numberValue(tenorDays);
  if (days != null && days > 0) {
    const next = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate()));
    next.setUTCDate(next.getUTCDate() + days);
    return next.toISOString().slice(0, 10);
  }
  return null;
}

function parseIsoDate(value: unknown): Date | null {
  if (typeof value !== 'string' || value.trim() === '') return null;
  const parsed = new Date(`${value.slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function numberValue(value: unknown): number | null {
  if (value == null || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function isBarrierField(fieldKey: string): boolean {
  return fieldKey === 'ko_barrier'
    || fieldKey === 'ki_barrier'
    || fieldKey === 'barrier'
    || fieldKey === 'upper_barrier'
    || fieldKey === 'lower_barrier'
    || fieldKey === 'coupon_barrier';
}

function derivedBarrierLevel(row: TrySolveRowOut, displayedValue: unknown): { level: number; multiple: number; reference: number } | null {
  const multiple = numberValue(displayedValue);
  const reference = numberValue(row.fields.initial_price) ?? numberValue(row.market.spot);
  if (multiple == null || reference == null || reference <= 10 || Math.abs(multiple) <= 0 || Math.abs(multiple) > 10) {
    return null;
  }
  return { level: multiple * reference, multiple, reference };
}

function formatCompactNumber(value: number): string {
  return value.toLocaleString(undefined, {
    maximumFractionDigits: Math.abs(value) >= 100 ? 2 : 4,
  });
}

function formatMoneyness(value: number): string {
  return `${(value * 100).toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
}

function parseNumberInput(value: string): number | null {
  if (value.trim() === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseSelectId(value: string): number | null {
  if (value === '') return null;
  const parsed = Number(value);
  return Number.isInteger(parsed) ? parsed : null;
}

function normalizeSymbol(value: unknown): string {
  return String(value ?? '').trim().toUpperCase();
}

function profileOptionLabel(profile: PricingParameterProfile): string {
  const rows = profile.rows?.length ?? Number(profile.summary?.row_count ?? 0);
  return `${profile.name} · ${dateInputValue(profile.valuation_date)} · ${rows} rows`;
}

function marketDataOptionLabel(profile: MarketDataProfile): string {
  const spot = typeof profile.data?.spot === 'number' ? ` · spot ${formatValue(profile.data.spot)}` : '';
  const valuation = formatDateTime(profile.valuation_date);
  const saved = formatDateTime(profile.updated_at || profile.created_at);
  const timestamp = [
    valuation ? `value ${valuation}` : '',
    saved ? `saved ${saved}` : '',
  ].filter(Boolean).join(' · ');
  return `${profile.name} · ${profile.symbol}${timestamp ? ` · ${timestamp}` : ''}${spot}`;
}

function underlyingOptionLabel(underlying: Underlying): string {
  return underlying.display_name && underlying.display_name !== underlying.symbol
    ? `${underlying.symbol} · ${underlying.display_name}`
    : underlying.symbol;
}

function statusClass(status: string): string {
  if (status === 'solved') return 'solved';
  if (status === 'solver_ready') return 'ready';
  if (status === 'schema_captured') return 'captured';
  if (status.includes('missing') || status.includes('failed') || status.includes('invalid')) return 'attention';
  return 'neutral';
}

function badgeVariant(status: string): BadgeVariant {
  const cls = statusClass(status);
  if (cls === 'ready' || cls === 'solved') return 'pos';
  if (cls === 'attention') return 'neg';
  if (cls === 'captured') return 'warn';
  return 'ink';
}

function formatStatus(status: string): string {
  return status
    .replaceAll('_', ' ')
    .replace(/^\w/, (letter) => letter.toUpperCase());
}

function formatValue(value: unknown): string {
  if (value == null || value === '') return '-';
  if (typeof value === 'number') return Number.isInteger(value) ? value.toLocaleString() : value.toPrecision(5);
  return String(value);
}

function formatTargetMetric(target: TrySolveQuoteRequest): string {
  const value = formatValue(target.target_value);
  const suffix = target.target_label === 'premium %' ? '%' : '';
  return `${target.target_label} ${value}${suffix}`;
}

function dateInputValue(value: string | null | undefined): string {
  return value ? value.slice(0, 10) : '';
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const date = parsed.toLocaleDateString(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
  const time = parsed.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
  return `${date} ${time}`;
}

import { useEffect, useState } from 'react';
import { MasterDetailPage } from '../components/templates';
import { RailList } from '../components/RailList';
import { RailItem } from '../components/RailItem';
import { Empty } from '../components/Empty';
import { Button } from '../components/Button';
import { NumberInput } from '../components/NumberInput';
import { Select } from '../components/Select';
import {
  createEngineConfig,
  deleteEngineConfig,
  listEngineConfigs,
  setDefaultEngineConfig,
  updateEngineConfig,
} from '../api/client';
import type { EngineConfigVariant } from '../types';
import './EngineConfigs.css';

type ProductTypeRuleForm = {
  id: string;
  name: string;
  productType: string;
  engineName: string;
  engineKwargs: string;
};

type FamilyRuleForm = {
  id: string;
  family: 'autocallables' | 'others';
  engineType: 'QUAD' | 'MC' | 'PDE' | 'ANALYTICAL';
};

const PRODUCT_TYPES = [
  'EuropeanVanillaOption',
  'AmericanOption',
  'CashOrNothingDigitalOption',
  'BarrierOption',
  'OneTouchOption',
  'DoubleOneTouchOption',
  'AsianOption',
  'RangeAccrualOption',
  'SingleSharkfinOption',
  'DoubleSharkfinOption',
  'SnowballOption',
  'KnockOutResetSnowballOption',
  'PhoenixOption',
  'Futures',
  'SpotInstrument',
] as const;

// Keep in sync with PRICING_ENGINE_TO_BACKTEST_ENGINE in
// backend/app/services/engine_configs.py — the backend rejects unknown names.
const QUANTARK_ENGINES = [
  'BlackScholesEngine',
  'AmericanOptionAnalyticalEngine',
  'DigitalOptionAnalyticalEngine',
  'BarrierAnalyticalEngine',
  'OneTouchAnalyticalEngine',
  'AsianOptionAnalyticalEngine',
  'RangeAccrualAnalyticalEngine',
  'SingleSharkfinOptionAnalyticalEngine',
  'DoubleSharkfinOptionAnalyticalEngine',
  'SnowballQuadEngine',
  'KOResetSnowballQuadEngine',
  'PhoenixQuadEngine',
  'EuropeanQuadEngine',
  'BarrierQuadEngine',
  'OneTouchQuadEngine',
  'EuropeanMCEngine',
  'AmericanOptionMCEngine',
  'DigitalOptionMCEngine',
  'BarrierOptionMCEngine',
  'AsianOptionMCEngine',
  'RangeAccrualMCEngine',
  'SingleSharkfinOptionMCEngine',
  'DoubleSharkfinOptionMCEngine',
  'SnowballMCEngine',
  'PhoenixMCEngine',
  'PDEEngine',
  'DeltaOneEngine',
] as const;

const FAMILY_ENGINE_TYPES = ['QUAD', 'MC', 'PDE', 'ANALYTICAL'] as const;

function newProductTypeRule(seed = PRODUCT_TYPES[0]): ProductTypeRuleForm {
  return {
    id: crypto.randomUUID(),
    name: seed,
    productType: seed,
    engineName: 'BlackScholesEngine',
    engineKwargs: '{}',
  };
}

function defaultFamilyRules(): FamilyRuleForm[] {
  return [
    { id: crypto.randomUUID(), family: 'autocallables', engineType: 'QUAD' },
    { id: crypto.randomUUID(), family: 'others', engineType: 'ANALYTICAL' },
  ];
}

function parseRules(rules: Record<string, unknown> | null | undefined): {
  families: FamilyRuleForm[];
  products: ProductTypeRuleForm[];
} {
  const rawRules = Array.isArray(rules?.rules) ? rules.rules : [];
  const families: FamilyRuleForm[] = [];
  const products: ProductTypeRuleForm[] = [];
  rawRules.forEach((raw, index) => {
    const item = raw as Record<string, unknown>;
    const match = (item.match ?? {}) as Record<string, unknown>;
    const pricing = (item.pricing ?? {}) as Record<string, unknown>;
    if (match.product_family) {
      const family = String(match.product_family);
      if (family === 'autocallables' || family === 'others') {
        families.push({
          id: crypto.randomUUID(),
          family,
          engineType: normalizeFamilyEngine(pricing.engine_type),
        });
      }
      return;
    }
    const productType = String(match.product_type ?? '');
    products.push({
      id: crypto.randomUUID(),
      name: String(item.name ?? (productType || `Rule ${index + 1}`)),
      productType,
      engineName: String(pricing.engine_name ?? ''),
      engineKwargs: JSON.stringify(pricing.engine_kwargs ?? {}, null, 2),
    });
  });
  const mergedFamilies = defaultFamilyRules().map((fallback) => (
    families.find((item) => item.family === fallback.family) ?? fallback
  ));
  return { families: mergedFamilies, products };
}

function normalizeFamilyEngine(value: unknown): FamilyRuleForm['engineType'] {
  const text = String(value ?? '').toUpperCase();
  return FAMILY_ENGINE_TYPES.includes(text as FamilyRuleForm['engineType'])
    ? text as FamilyRuleForm['engineType']
    : 'ANALYTICAL';
}

function formsToRules(families: FamilyRuleForm[], products: ProductTypeRuleForm[]): Record<string, unknown> {
  return {
    rules: [
      ...products.map((rule) => ({
        name: rule.name.trim() || rule.productType.trim(),
        match: { product_type: rule.productType.trim() },
        pricing: {
          engine_name: rule.engineName.trim(),
          engine_kwargs: parseEngineKwargs(rule.engineKwargs),
        },
      })),
      ...families.map((rule) => ({
        name: rule.family === 'autocallables' ? 'Autocallables' : 'Others',
        match: { product_family: rule.family },
        pricing: { engine_type: rule.engineType },
      })),
    ],
  };
}

function parseEngineKwargs(value: string): Record<string, unknown> {
  const trimmed = value.trim();
  if (!trimmed) return {};
  const parsed = JSON.parse(trimmed) as unknown;
  if (parsed == null || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error('Engine kwargs must be a JSON object');
  }
  return parsed as Record<string, unknown>;
}

export function EngineConfigsLive() {
  const [configs, setConfigs] = useState<EngineConfigVariant[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [isDefault, setIsDefault] = useState(false);
  const [familyRules, setFamilyRules] = useState<FamilyRuleForm[]>(defaultFamilyRules);
  const [productRules, setProductRules] = useState<ProductTypeRuleForm[]>([]);
  const [businessDaysInYear, setBusinessDaysInYear] = useState<number | null>(244);
  const [feedback, setFeedback] = useState('');
  const [error, setError] = useState('');

  const selected = configs.find((config) => config.id === selectedId) ?? null;
  const ruleCount = familyRules.length + productRules.length;

  async function load(preferredId = selectedId) {
    const rows = await listEngineConfigs();
    setConfigs(rows);
    const next = rows.find((config) => config.id === preferredId) ?? rows.find((config) => config.is_default) ?? rows[0] ?? null;
    setSelectedId(next?.id ?? null);
  }

  useEffect(() => {
    void load().catch((exc) => setError(exc instanceof Error ? exc.message : String(exc)));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setName(selected.name);
    setDescription(selected.description ?? '');
    setIsDefault(selected.is_default);
    setBusinessDaysInYear(selected.business_days_in_year ?? null);
    const parsed = parseRules(selected.rules);
    setFamilyRules(parsed.families);
    setProductRules(parsed.products);
  }, [selectedId]);

  function updateFamilyRule(family: FamilyRuleForm['family'], engineType: FamilyRuleForm['engineType']) {
    setFamilyRules((current) => current.map((rule) => (rule.family === family ? { ...rule, engineType } : rule)));
  }

  function updateProductRule(id: string, patch: Partial<ProductTypeRuleForm>) {
    setProductRules((current) => current.map((rule) => (rule.id === id ? { ...rule, ...patch } : rule)));
  }

  async function save() {
    setError('');
    setFeedback('');
    if (!name.trim()) {
      setError('Name is required');
      return;
    }
    if (productRules.some((rule) => !rule.productType.trim() || !rule.engineName.trim())) {
      setError('Each product-type override needs a product type and QuantArk engine');
      return;
    }
    let rulePayload: Record<string, unknown>;
    try {
      rulePayload = formsToRules(familyRules, productRules);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      return;
    }
    const body = {
      name: name.trim(),
      description: description.trim() || null,
      status: 'active',
      is_default: isDefault,
      rules: rulePayload,
      business_days_in_year: businessDaysInYear,
    };
    const saved = selected ? await updateEngineConfig(selected.id, body) : await createEngineConfig(body);
    setFeedback(`Saved ${saved.name}`);
    await load(saved.id);
  }

  async function remove() {
    if (!selected) return;
    await deleteEngineConfig(selected.id);
    setFeedback(`Deleted ${selected.name}`);
    setSelectedId(null);
    await load(null);
  }

  async function makeDefault() {
    if (!selected) return;
    await setDefaultEngineConfig(selected.id);
    setFeedback(`${selected.name} is now default`);
    await load(selected.id);
  }

  function newConfig() {
    setSelectedId(null);
    setName('New Engine Config');
    setDescription('');
    setIsDefault(false);
    setBusinessDaysInYear(244);
    setFamilyRules(defaultFamilyRules());
    setProductRules([]);
  }

  function duplicateSelected() {
    if (!selected) return;
    setSelectedId(null);
    setName(`${selected.name} Copy`);
    setDescription(selected.description ?? '');
    setIsDefault(false);
    const parsed = parseRules(selected.rules);
    setFamilyRules(parsed.families);
    setProductRules(parsed.products);
  }

  const railList = (
    <RailList>
      {configs.map((config) => (
        <RailItem
          key={config.id}
          layout="row"
          className="wl-engine-configs__row"
          active={config.id === selectedId}
          onClick={() => setSelectedId(config.id)}
        >
          <span className="wl-engine-configs__row-name wl-rail__title">{config.name}</span>
          {config.is_default && <span className="wl-engine-configs__pill">Default</span>}
        </RailItem>
      ))}
    </RailList>
  );

  return (
    <MasterDetailPage
      title="ENGINE CONFIGS"
      chips={[`${configs.length} variant${configs.length === 1 ? '' : 's'}`, `${ruleCount} rule${ruleCount === 1 ? '' : 's'}`]}
      actions={<Button variant="primary" onClick={newConfig}>New config</Button>}
      feedback={(
        <>
          {feedback && <div className="wl-engine-configs__feedback" role="status">{feedback}</div>}
          {error && <div className="wl-engine-configs__error" role="alert">{error}</div>}
        </>
      )}
      rail={railList}
      railLabel="Engine config variants"
    >
        <main className="wl-engine-configs__editor">
          <section className="wl-engine-configs__section">
            <div className="wl-engine-configs__section-head">
              <div>
                <h2>Variant Details</h2>
                <p>Name the variant and choose whether tasks use it by default.</p>
              </div>
              {selected && <Button onClick={duplicateSelected}>Duplicate</Button>}
            </div>
            <div className="wl-engine-configs__grid">
              <label className="wl-engine-configs__field">
                <span>Name</span>
                <input value={name} onChange={(event) => setName(event.target.value)} />
              </label>
              <label className="wl-engine-configs__field">
                <span>Description</span>
                <input value={description} onChange={(event) => setDescription(event.target.value)} />
              </label>
              <label className="wl-engine-configs__field">
                <span>Business Days In Year</span>
                <NumberInput
                  type="number"
                  value={businessDaysInYear ?? ''}
                  placeholder="244"
                  min={1}
                  onChange={(event) => {
                    const value = event.target.value;
                    setBusinessDaysInYear(value === '' ? null : Number(value));
                  }}
                />
              </label>
              <label className="wl-engine-configs__check">
                <input type="checkbox" checked={isDefault} onChange={(event) => setIsDefault(event.target.checked)} />
                <span>Make default on save</span>
              </label>
            </div>
          </section>

          <section className="wl-engine-configs__section">
            <div className="wl-engine-configs__section-head">
              <div>
                <h2>Family Defaults</h2>
                <p>Used after product-type overrides. Autocallables and Others use engine enums: QUAD, MC, PDE, ANALYTICAL.</p>
              </div>
            </div>
            <div className="wl-engine-configs__family-grid">
              {familyRules.map((rule) => (
                <div className="wl-engine-configs__field" key={rule.id}>
                  <Select
                    label={rule.family === 'autocallables' ? 'Autocallables' : 'Others'}
                    value={rule.engineType}
                    onChange={(v) => updateFamilyRule(rule.family, v as FamilyRuleForm['engineType'])}
                    options={FAMILY_ENGINE_TYPES.map((engine) => ({ value: engine, label: engine }))}
                  />
                </div>
              ))}
            </div>
          </section>

          <section className="wl-engine-configs__section">
            <div className="wl-engine-configs__section-head">
              <div>
                <h2>Product-Type Overrides</h2>
                <p>Highest priority. Match a real QuantArk product type and route it to a real QuantArk engine.</p>
              </div>
              <Button onClick={() => setProductRules((current) => [...current, newProductTypeRule()])}>Add override</Button>
            </div>
            <div className="wl-engine-configs__rules">
              {productRules.length === 0 && <Empty message="No product-type overrides. Family defaults will handle every position." />}
              {productRules.map((rule, index) => (
                <article className="wl-engine-configs__rule" key={rule.id}>
                  <div className="wl-engine-configs__rule-head">
                    <strong>Override {index + 1}</strong>
                    <Button
                      variant="danger"
                      onClick={() => setProductRules((current) => current.filter((item) => item.id !== rule.id))}
                    >
                      Remove
                    </Button>
                  </div>
                  <div className="wl-engine-configs__rule-grid">
                    <label className="wl-engine-configs__field">
                      <span>Rule name</span>
                      <input value={rule.name} onChange={(event) => updateProductRule(rule.id, { name: event.target.value })} />
                    </label>
                    <label className="wl-engine-configs__field">
                      <span>Product type</span>
                      <input
                        list="engine-config-product-options"
                        value={rule.productType}
                        onChange={(event) => updateProductRule(rule.id, { productType: event.target.value })}
                      />
                    </label>
                    <label className="wl-engine-configs__field">
                      <span>QuantArk engine</span>
                      <input
                        list="engine-config-quantark-engine-options"
                        value={rule.engineName}
                        onChange={(event) => updateProductRule(rule.id, { engineName: event.target.value })}
                      />
                    </label>
                    <label className="wl-engine-configs__field wl-engine-configs__field--wide">
                      <span>Engine kwargs</span>
                      <textarea
                        value={rule.engineKwargs}
                        onChange={(event) => updateProductRule(rule.id, { engineKwargs: event.target.value })}
                        spellCheck={false}
                      />
                    </label>
                  </div>
                </article>
              ))}
            </div>
          </section>

          <div className="wl-engine-configs__buttons">
            <Button variant="primary" onClick={() => void save()} disabled={!name.trim()}>Save</Button>
            {selected && <Button onClick={() => void makeDefault()}>Set default</Button>}
            {selected && <Button variant="danger" onClick={() => void remove()}>Delete</Button>}
          </div>
        </main>
      <datalist id="engine-config-product-options">
        {PRODUCT_TYPES.map((item) => <option key={item} value={item} />)}
      </datalist>
      <datalist id="engine-config-quantark-engine-options">
        {QUANTARK_ENGINES.map((item) => <option key={item} value={item} />)}
      </datalist>
    </MasterDetailPage>
  );
}

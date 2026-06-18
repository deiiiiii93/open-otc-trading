import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { Instrument, PageContextReporter, RFQ, RfqCatalog, Underlying } from '../types';
import { ClientRfq, syncTermsUnderlying, type ClientRfqForm } from './ClientRfq';
import { inferProductFamily } from '../components/PositionEditForm';

const CLIENT_NAME_KEY = 'openOtc.clientRfqName';
const DEFAULT_MESSAGE = 'Can you quote a one year CSI500 snowball solving KO rate for target premium 10?';
const POLL_MS = 10000;

type Props = {
  onPageContextChange?: PageContextReporter;
};

export function ClientRfqLive({ onPageContextChange }: Props) {
  const [catalog, setCatalog] = useState<RfqCatalog | null>(null);
  const [underlyings, setUnderlyings] = useState<Instrument[]>([]);
  const [rfqs, setRfqs] = useState<RFQ[]>([]);
  const [selectedRfqId, setSelectedRfqId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [clientName, setClientName] = useState(
    () => localStorage.getItem(CLIENT_NAME_KEY) ?? 'Demo Client',
  );
  const submittingRef = useRef(false);
  const rfqsRequestSeq = useRef(0);
  // Latest client name for closures that outlive a render (e.g. a submission
  // in flight while the user edits the name).
  const clientNameRef = useRef(clientName);

  const refreshRfqs = useCallback(async (name: string) => {
    // Every refresh supersedes in-flight ones: only the latest request may
    // write state, so a slow response for a previous client name can never
    // replace the current client's list.
    const seq = ++rfqsRequestSeq.current;
    const trimmed = name.trim();
    if (trimmed === '') {
      // A blank client_name would skip the backend filter and list every
      // client's RFQs; show an empty workbench instead.
      setRfqs([]);
      return;
    }
    const listed = await api<RFQ[]>(
      `/api/client/rfqs?client_name=${encodeURIComponent(trimmed)}&limit=20`,
    );
    if (seq === rfqsRequestSeq.current) setRfqs(listed);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [catalogData, instruments] = await Promise.all([
          api<RfqCatalog>('/api/rfq/catalog').catch(() => null),
          api<Instrument[]>('/api/instruments').catch(() => [] as Instrument[]),
        ]);
        if (cancelled) return;
        setCatalog(catalogData);
        setUnderlyings(instruments);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      if (cancelled || submittingRef.current) return;
      try {
        await refreshRfqs(clientName);
      } catch {
        // Polling failures are silent; the next tick retries.
      }
    };
    void tick();
    const interval = setInterval(() => void tick(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [clientName, refreshRfqs]);

  const handleClientNameChange = (name: string) => {
    clientNameRef.current = name;
    setClientName(name);
    localStorage.setItem(CLIENT_NAME_KEY, name);
  };

  const submit = async (request: (name: string) => Promise<RFQ>) => {
    // Submit the same normalized name the history query uses — a raw value
    // with stray whitespace would create an RFQ that the trimmed
    // refresh query can never find again.
    const name = clientName.trim();
    if (name === '') {
      setError('Enter a client name before submitting.');
      return;
    }
    submittingRef.current = true;
    setSubmitting(true);
    setError(null);
    setFeedback(null);
    try {
      const rfq = await request(name);
      setFeedback(`RFQ #${rfq.id} submitted (${rfq.status}).`);
      setSelectedRfqId(rfq.id);
      submittingRef.current = false;
      // Refresh whichever client the workbench shows NOW — the name may have
      // changed while the submission was in flight.
      await refreshRfqs(clientNameRef.current);
    } catch (err) {
      setError(errorDetail(err));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  const handleSubmitNL = (message: string) =>
    submit((name) =>
      api<RFQ>('/api/client/rfq/chat', {
        method: 'POST',
        body: JSON.stringify({ client_name: name, message }),
      }));

  const handleSubmitStructured = (form: ClientRfqForm) =>
    submit((name) =>
      api<RFQ>('/api/client/rfq/form', {
        method: 'POST',
        body: JSON.stringify(structuredPayload(form, name)),
      }));

  return (
    <ClientRfq
      catalog={catalog}
      underlyings={underlyings as unknown as Underlying[]}
      rfqs={rfqs}
      selectedRfqId={selectedRfqId}
      clientName={clientName}
      defaultMessage={DEFAULT_MESSAGE}
      loading={loading}
      submitting={submitting}
      error={error}
      feedback={feedback}
      onClientNameChange={handleClientNameChange}
      onSelectRfq={setSelectedRfqId}
      onSubmitNL={handleSubmitNL}
      onSubmitStructured={handleSubmitStructured}
      onPageContextChange={onPageContextChange}
    />
  );
}

function structuredPayload(form: ClientRfqForm, clientName: string): Record<string, unknown> {
  // Safety net at the API boundary: the editor already keeps delta-one terms
  // in lockstep with the Underlying select, but re-normalize here so no form
  // path can submit terms that price a different underlying than the RFQ records.
  const terms = syncTermsUnderlying(form.productTerms, form.underlying);
  const payload: Record<string, unknown> = {
    client_name: clientName,
    side: form.side,
    quantity: form.notional ?? 1,
    quote_mode: form.quoteMode,
    product: {
      asset_class: 'equity',
      product_family: inferProductFamily(form.product, form.productTerms),
      quantark_class: form.product,
      underlying: form.underlying.trim(),
      currency: form.currency,
      terms,
    },
    market: { currency: form.currency },
    engine_spec: form.engineSpec,
  };
  if (form.quoteMode === 'solve') {
    payload.unknown = {
      field_path: form.unknownField,
      lower_bound: form.lowerBound ?? 0,
      upper_bound: form.upperBound ?? 0,
      initial_guess: form.initialGuess ?? 0,
    };
    payload.target = { label: form.targetLabel, value: form.targetValue ?? 0 };
  }
  return payload;
}

function errorDetail(err: unknown): string {
  if (err instanceof Error) {
    try {
      const parsed = JSON.parse(err.message) as { detail?: unknown };
      if (parsed && typeof parsed.detail === 'string') return parsed.detail;
    } catch {
      // Not JSON — fall through to the raw message.
    }
    return err.message || 'Request failed';
  }
  return 'Request failed';
}

import { describe, it, expect } from 'vitest';
import { ARENA_REASONING_EFFORTS, reasoningEffortsFor, type ArenaModel } from './arenaApi';

const model = (slug: string, reasoning_efforts?: string[]): ArenaModel => ({
  slug,
  zenmux_name: slug,
  display_name: slug,
  ...(reasoning_efforts ? { reasoning_efforts } : {}),
});

describe('reasoningEffortsFor', () => {
  it('returns the levels the model declares', () => {
    // Real case: GLM-5.2 accepts only high/max, while most models are low/med/high.
    expect(reasoningEffortsFor(model('glm-5-2', ['high', 'max']))).toEqual(['high', 'max']);
  });

  it('returns nothing for a model with no ladder', () => {
    // Qwen3.7 / MiniMax M3 expose reasoning as a bare on/off toggle. The panel
    // must then show no picker at all rather than one that cannot be honoured.
    expect(reasoningEffortsFor(model('qwen-3-7-max', []))).toEqual([]);
  });

  it('treats a model unknown to the snapshot as unconstrained', () => {
    // models.dev lags new releases (grok-4.6 had no zenmux entry); the server
    // falls back to permissive, so the UI must agree or it would hide levels the
    // launch would have accepted.
    expect(reasoningEffortsFor(model('grok-4-6'))).toEqual([...ARENA_REASONING_EFFORTS]);
  });

  it('normalises to canonical weakest-to-strongest order', () => {
    // The server sends whatever order models.dev used; the picker must not.
    expect(reasoningEffortsFor(model('a', ['high', 'low', 'medium']))).toEqual(
      ['low', 'medium', 'high'],
    );
  });

  it('ignores a level outside the canonical ladder', () => {
    // Guarded upstream by test_reasoning_capabilities' superset check; here the UI
    // simply must not render a level it has no label or ordering for.
    expect(reasoningEffortsFor(model('a', ['high', 'bogus']))).toEqual(['high']);
  });
});

describe('measured ladders match what the gateway really accepts', () => {
  it('keeps a level a model declares and drops one it does not', () => {
    // Live-probe facts (2026-08-14): grok-4.6 accepts minimal..xhigh but REJECTS
    // none and max; openai/chat-latest accepts ONLY medium.
    const grok = model('grok-4-6', ['minimal', 'low', 'medium', 'high', 'xhigh']);
    expect(reasoningEffortsFor(grok)).not.toContain('none');
    expect(reasoningEffortsFor(grok)).not.toContain('max');
    expect(reasoningEffortsFor(grok)).toContain('xhigh');

    const chatLatest = model('gpt-5-5-instant', ['medium']);
    expect(reasoningEffortsFor(chatLatest)).toEqual(['medium']);
  });
});

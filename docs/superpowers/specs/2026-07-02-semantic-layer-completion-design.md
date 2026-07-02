# Semantic Layer Completion (ontology absorption) — Design

**Date:** 2026-07-02
**Status:** Draft — awaiting review

## Aim

Bring the agent's *semantic layer* (product reference docs the LLM reads) to
parity with the *constraint layer* (`FamilyContracts` the booking gate
enforces), and add a mechanical coherence net so prose and code cannot drift.
This absorbs the two ontology functions the stack currently lacks — full
definitional coverage and reasoner-style consistency checking — without
adopting OWL, RDF, or any external ontology runtime.

## Background

Analysis of FIBO (edmcouncil/fibo) showed this repo already implements an
ontology *decomposed by consumer*:

| Ontology function | Where it lives here | Status |
|---|---|---|
| Taxonomy / classification | SKILL.md frontmatter + routing table | Complete |
| Definitions & conventions | `skills/references/products/*.md` | **1 of 15 families covered** (`snowball-cn.md`) |
| Formal constraints | `FamilyContracts` / `build_product` | Complete (15 classes) |
| Inference / coherence | pytest | **One hand-written case** (`test_rfq_lifecycle_reference_matches_runtime_status_contract`) |

The two gaps: 14 QuantArk classes have booking constraints but no semantics
the agent can load when interpreting terms, and nothing mechanically ties a
reference doc's prose to its family's `required_bound` keys — if a contract
gains a required key and the doc is not updated, the term-interpretation
skill will declare a term-set complete that `build_product` then rejects.

## Approaches considered

1. **Docs-only sprint.** Hand-write the missing reference docs, no tooling.
   Cheapest, but no drift protection; docs rot silently — worse than absent
   docs because the agent trusts them.
2. **Reference docs + coherence net (chosen).** Per-family docs following the
   `snowball-cn.md` section schema, a small term glossary as Python data, and
   a pytest net that generalizes the existing rfq-lifecycle precedent:
   every contract class must be claimed by a doc, and every required-bound
   key must be explained in it.
3. **Full machine-readable ontology.** One YAML taxonomy (families, terms,
   envelopes) consumed by router, prompts, and tests, plus FIBO-DER/FINOS-CDM
   mapping columns. Most ontology-faithful, but duplicates what frontmatter
   and `FamilyContracts` already encode, and the interop mapping has no
   consumer today. Rejected as YAGNI; noted under Non-goals/Follow-ups.

## Design

### D1 — Per-family product reference docs (8 new docs, 14 classes)

Location: `backend/app/skills/references/products/`. Each follows the
`snowball-cn.md` section schema — `## Product Definition`,
`## Observation Conventions` (path-dependent families only),
`## Pricing Inputs`, `## Diagnostics` — bounded to roughly the same length
(~40 lines).

**Design principle — region neutrality.** Family docs describe product
semantics only: payoff structure, what an observation convention *means*,
which inputs pricing needs, and diagnostics. They must not assert
region-specific market facts (exchange calendars, index universes,
jurisdiction norms). Values like ACT/365 or a holiday calendar are
*configurable desk defaults*, not market invariants — where a doc mentions
one, it says "desk default, configurable" rather than presenting it as a
property of the product or a market. Region-specific market knowledge, when
genuinely needed, lives in a separate region-overlay doc (the existing
`snowball-cn.md` is retroactively classified as one: the CN overlay for
SnowballOption, which it claims until a region-neutral `snowball.md` base
doc is warranted). This mirrors the concept-vs-jurisdiction separation
formal ontologies use, and keeps the semantic layer portable if the desk
adds markets.

Grouping (thin families share a doc; one doc claims 1–3 QuantArk classes):

| Doc | QuantArk classes claimed |
|---|---|
| `vanilla.md` | EuropeanVanillaOption, AmericanOption |
| `asian.md` | AsianOption |
| `digital-touch.md` | CashOrNothingDigitalOption, OneTouchOption, DoubleOneTouchOption |
| `barrier.md` | BarrierOption |
| `sharkfin.md` | SingleSharkfinOption, DoubleSharkfinOption |
| `range-accrual.md` | RangeAccrualOption |
| `autocallable-variants.md` | KnockOutResetSnowballOption, PhoenixOption (builds on `snowball-cn.md`, documents only the deltas: post-KI reset leg, memory coupon leg) |
| `delta-one.md` | Futures, SpotInstrument |

Frontmatter gains one new key that is the mechanical doc↔contract link:

```yaml
---
name: sharkfin
description: Durable sharkfin payoff conventions and diagnostics.
reference_type: product
quantark_classes:
  - SingleSharkfinOption
  - DoubleSharkfinOption
---
```

`snowball-cn.md` is retrofitted with `quantark_classes: [SnowballOption]`
and `region: CN` (see the region-neutrality principle above).
`build-contract.md` (not family-specific) carries no `quantark_classes` and
is exempt from the per-family checks.

Precondition: verify `reference_docs.py::validate_reference_doc_file`
tolerates the extra frontmatter keys (`quantark_classes`, `region`); extend
its schema if it is strict.

### D2 — Term glossary as Python data

`backend/app/services/domains/term_glossary.py`: a plain dict mapping each
*leaf* contract key (e.g. `ko_barrier`, `ki_barrier`, `accrual_rate`,
`averaging_frequency`) to the canonical desk phrase plus accepted aliases
(e.g. `ko_barrier` → "KO barrier", "knock-out barrier"). Scope is strictly
the union of `required_bound` + `defaulted` leaves across `FAMILY_CONTRACTS`
— no speculative vocabulary. Python data (not YAML) so tests and any future
consumer import it without a loader, matching how `FamilyContracts` itself
is "contract as data".

### D3 — Coherence net (the reasoner function)

`tests/test_semantic_coherence.py`, generalizing the rfq-lifecycle pattern:

1. **Coverage:** every key in `FAMILY_CONTRACTS` is claimed by exactly one
   product reference doc's `quantark_classes` (and every claimed class
   exists — no dead claims).
2. **Required-bound explanation:** for each contract, every `required_bound`
   leaf key resolves through the glossary to at least one phrase present in
   the claiming doc's `## Pricing Inputs` section. This is the check that
   fails CI when a contract gains a key and the doc lags.
3. **Glossary hygiene:** every glossary key is a leaf of some contract's
   `required_bound`/`defaulted` (no dead entries); canonical phrases are
   unique.
4. **Region neutrality:** family docs (any product doc that is not an
   explicit region overlay) contain none of a small denylist of
   region-market tokens (e.g. "SSE", "China Mainland", "CSI", "A-share");
   region overlays are identified by a `region:` frontmatter key (added to
   `snowball-cn.md` as `region: CN`). The denylist lives beside the
   glossary so extending it is one edit.

Failure messages name the doc, the class, and the missing key so the fix is
mechanical.

### D4 — Generic term-interpretation skill (1 new skill, not 14)

`backend/app/skills/workflows/products/product-term-interpretation/SKILL.md`
mirrors `snowball-term-interpretation` but is family-agnostic: identify the
family (from position terms, `product_key`, or user text), load the claiming
reference doc, explain terms, flag missing `required_bound` economics, route
to the pricing workflow. Same stop condition: never infer missing barriers
or lifecycle state from the product name; ask for the missing term.
Snowballs keep their dedicated skill (richer lifecycle content); the generic
skill's frontmatter routing excludes the snowball domain to avoid dispatch
ambiguity. ≤500-token SKILL.md cap applies.

### Ripple updates (known blast radius)

- `EXPECTED_REFERENCE_FILES` exact set in `tests/test_reference_docs.py`
  (+8 files).
- Skill-catalog exact-set/count assertions in six files
  (`test_skills_catalog{,_v2}`, `{remaining_,}workflow_skills_phase3`,
  `reference_docs`, `routing_table`) for the one new workflow skill.
- **Orchestrator prompt needs a routing line** for
  `product-term-interpretation` (established lesson: a skill without a
  routing line is never dispatched).
- Read-smoke test (`test_skills_read_smoke_v2.py`) if it enumerates docs.

### Error handling

- Family without a doc / doc claiming an unknown class / required key not
  explained → CI failure via D3; nothing degrades silently at runtime.
- At runtime the skill inherits the snowball pattern's epistemic guard
  (stop conditions) — ambiguity surfaces as a question, never a guess.

### Testing

D3 is itself the primary deliverable test. Beyond it: frontmatter-schema
test for `quantark_classes`, catalog/routing updates above, and one smoke
asserting the generic skill resolves the right doc for a representative
non-snowball family (e.g. sharkfin).

## Non-goals / follow-ups

- No OWL/RDF/SKOS artifacts, no reasoner, no embeddings.
- FIBO-DER / FINOS-CDM name alignment: deferred until a counterparty
  interop consumer exists; when touching family/field names, prefer
  CDM-compatible terms opportunistically.
- Instance-level knowledge graph (positions↔products↔limits): separate
  project if multi-hop agent queries become a need.
- `import_schema.py` column-name coherence with the glossary: candidate
  second wave for the net once D2 exists.

## Success criteria

- All 15 `FAMILY_CONTRACTS` classes are claimed by a product reference doc.
- Family docs are region-neutral (D3 check 4 green); desk defaults are
  labelled as configurable defaults, never as market facts.
- `test_semantic_coherence.py` fails when a contract key is added without a
  doc update (verified by mutation during implementation).
- Agent can answer "what does this sharkfin knock-out mean?" by loading the
  new doc through the generic skill, and flags missing required terms
  instead of inferring them.
- Full backend suite green (including the six catalog files and
  `test_reference_docs.py`).

# Semantic Layer Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every `FAMILY_CONTRACTS` product family a region-neutral reference doc the agent can load, with a CI coherence net and a shared resolver/tool so prose and contracts cannot drift.

**Architecture:** Reference docs gain three frontmatter keys (`quantark_classes`, `region`, `extends`). A resolver in `reference_docs.py` merges child+base(+region overlay) and is exposed both to tests (`test_semantic_coherence.py`) and to the agent via a new read-only tool `get_product_reference_doc`. A glossary maps contract key paths to canonical phrases so tests can check prose mechanically.

**Tech Stack:** Python 3.12, pytest, PyYAML, LangChain `@tool`, existing deep-agent capability gate.

**Spec:** `docs/superpowers/specs/2026-07-02-semantic-layer-completion-design.md`

## Global Constraints

- Run all tests from **repo root**: `.venv/bin/python -m pytest tests/<file> -v`.
- Family docs are **region-neutral**: no "SSE", "China Mainland", "CSI", "A-share" tokens; values like ACT/365 or calendars are written as "desk default, configurable", never market facts. Region content only in overlays with `region:` frontmatter.
- New SKILL.md files ≤500 tokens; body starts with `## `.
- A tool the model must call needs registration in **both** `QUANT_AGENT_TOOLS` (`backend/app/tools/__init__.py:138`) **and** `DEEP_AGENT_TOOL_NAMES` (`backend/app/services/agents.py:372`) — missing the second silently drops it from every persona.
- Reference-doc frontmatter validation lives in `backend/app/services/deep_agent/reference_docs.py`; description ≤200 chars, `name` == filename stem.
- Commit after every task with the message given in the task.

---

### Task 1: Frontmatter schema extension (`quantark_classes`, `region`, `extends`)

**Files:**
- Modify: `backend/app/services/deep_agent/reference_docs.py`
- Test: `tests/test_reference_docs.py` (append new tests)

**Interfaces:**
- Produces: `validate_reference_doc_file` accepts and validates the three optional keys. `validate_product_reference_tree(root) -> dict[str, ReferenceDoc]` returning `{quantark_class: claiming_doc}`, raising `ValueError` on: unknown `extends` target, `extends` chain depth >1, class claimed by ≠1 doc, neutral doc extending a region-marked doc (unless same `region`).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_reference_docs.py`:

```python
import pytest

from app.services.deep_agent.reference_docs import validate_product_reference_tree


def _write_doc(root: Path, rel: str, front: str, body: str = "## Product Definition\n\nx.\n\n## Pricing Inputs\n\ny.\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{front}---\n\n{body}", encoding="utf-8")


def _products_tree(tmp_path: Path) -> Path:
    root = tmp_path / "references"
    _write_doc(
        root, "products/base.md",
        "name: base\ndescription: Base.\nreference_type: product\nquantark_classes: [SnowballOption]\n",
    )
    return root


def test_product_tree_resolves_claims(tmp_path: Path) -> None:
    root = _products_tree(tmp_path)
    claims = validate_product_reference_tree(root)
    assert claims["SnowballOption"].frontmatter["name"] == "base"


def test_product_tree_rejects_duplicate_claim(tmp_path: Path) -> None:
    root = _products_tree(tmp_path)
    _write_doc(
        root, "products/dup.md",
        "name: dup\ndescription: Dup.\nreference_type: product\nquantark_classes: [SnowballOption]\n",
    )
    with pytest.raises(ValueError, match="claimed by more than one"):
        validate_product_reference_tree(root)


def test_product_tree_rejects_unknown_extends(tmp_path: Path) -> None:
    root = _products_tree(tmp_path)
    _write_doc(
        root, "products/child.md",
        "name: child\ndescription: C.\nreference_type: product\nextends: nope\n",
    )
    with pytest.raises(ValueError, match="unknown extends target"):
        validate_product_reference_tree(root)


def test_product_tree_rejects_deep_chain(tmp_path: Path) -> None:
    root = _products_tree(tmp_path)
    _write_doc(root, "products/mid.md", "name: mid\ndescription: M.\nreference_type: product\nextends: base\n")
    _write_doc(root, "products/leaf.md", "name: leaf\ndescription: L.\nreference_type: product\nextends: mid\n")
    with pytest.raises(ValueError, match="extends chain deeper than one"):
        validate_product_reference_tree(root)


def test_product_tree_region_inheritance_guard(tmp_path: Path) -> None:
    root = _products_tree(tmp_path)
    _write_doc(
        root, "products/cn-base.md",
        "name: cn-base\ndescription: CN.\nreference_type: product\nregion: CN\n",
    )
    _write_doc(
        root, "products/neutral-child.md",
        "name: neutral-child\ndescription: N.\nreference_type: product\nextends: cn-base\n",
    )
    with pytest.raises(ValueError, match="region-marked base"):
        validate_product_reference_tree(root)


def test_frontmatter_key_shapes_rejected(tmp_path: Path) -> None:
    root = tmp_path / "references"
    _write_doc(
        root, "products/bad.md",
        "name: bad\ndescription: B.\nreference_type: product\nquantark_classes: notalist\n",
    )
    with pytest.raises(ValueError, match="quantark_classes must be a non-empty list"):
        validate_product_reference_tree(root)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_reference_docs.py -v -k product_tree or key_shapes`
Expected: FAIL — `ImportError: cannot import name 'validate_product_reference_tree'`

- [ ] **Step 3: Implement** — in `backend/app/services/deep_agent/reference_docs.py`, extend `validate_reference_doc_file` (after the `reference_type` check) and add the tree validator:

```python
    quantark_classes = doc.frontmatter.get("quantark_classes")
    if quantark_classes is not None and (
        not isinstance(quantark_classes, list)
        or not quantark_classes
        or not all(isinstance(c, str) and c.strip() for c in quantark_classes)
    ):
        errors.append("quantark_classes must be a non-empty list of class names")

    region = doc.frontmatter.get("region")
    if region is not None and (not isinstance(region, str) or not region.strip()):
        errors.append("region must be a non-empty string")

    extends = doc.frontmatter.get("extends")
    if extends is not None and (not isinstance(extends, str) or not extends.strip()):
        errors.append("extends must be a doc name string")
```

```python
def validate_product_reference_tree(root: Path = REFERENCES_DIR) -> dict[str, "ReferenceDoc"]:
    """Validate product docs as a set: claims unique, extends resolvable
    (depth <=1), no neutral doc extending a region-marked base."""
    docs = validate_reference_doc_tree(root)
    products = {d.frontmatter["name"]: d for d in docs if d.frontmatter["reference_type"] == "product"}

    claims: dict[str, ReferenceDoc] = {}
    for doc in products.values():
        base_name = doc.frontmatter.get("extends")
        if base_name is not None:
            base = products.get(base_name)
            if base is None:
                raise ValueError(f"{doc.path.name}: unknown extends target {base_name!r}")
            if base.frontmatter.get("extends") is not None:
                raise ValueError(f"{doc.path.name}: extends chain deeper than one level")
            base_region = base.frontmatter.get("region")
            if base_region is not None and doc.frontmatter.get("region") != base_region:
                raise ValueError(
                    f"{doc.path.name}: cannot extend region-marked base {base_name!r} "
                    "without declaring the same region"
                )
        for cls in doc.frontmatter.get("quantark_classes") or []:
            if cls in claims:
                raise ValueError(f"{cls} claimed by more than one product doc")
            claims[cls] = doc
    return claims
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_reference_docs.py -v`
Expected: all PASS (existing tests untouched — new keys are optional).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/reference_docs.py tests/test_reference_docs.py
git commit -m "feat: product reference frontmatter (quantark_classes/region/extends) + tree validator"
```

---

### Task 2: Resolver `resolve_product_reference`

**Files:**
- Modify: `backend/app/services/deep_agent/reference_docs.py`
- Test: `tests/test_reference_docs.py` (append)

**Interfaces:**
- Consumes: `validate_product_reference_tree` (Task 1).
- Produces: `resolve_product_reference(quantark_class: str, *, region: str | None = None, root: Path = REFERENCES_DIR) -> ResolvedReferenceDoc` where `ResolvedReferenceDoc` is a frozen dataclass `{quantark_class: str, content: str, source_paths: tuple[Path, ...]}`. Raises `KeyError` for an unclaimed class. Merge order: `extends` base body first, then claiming doc body, then (if `region` given) the body of any product doc with that `region` whose `extends` names the claiming doc, delimited by `\n\n## Regional Conventions ({region})\n\n`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_reference_docs.py`:

```python
from app.services.deep_agent.reference_docs import resolve_product_reference


def _inheritance_tree(tmp_path: Path) -> Path:
    root = tmp_path / "references"
    _write_doc(
        root, "products/base.md",
        "name: base\ndescription: Base.\nreference_type: product\nquantark_classes: [SnowballOption]\n",
        "## Product Definition\n\nAutocallable payoff.\n\n## Pricing Inputs\n\nKO barrier, KI barrier.\n",
    )
    _write_doc(
        root, "products/variants.md",
        "name: variants\ndescription: V.\nreference_type: product\n"
        "quantark_classes: [PhoenixOption]\nextends: base\n",
        "## Pricing Inputs\n\nCoupon barrier, coupon rate.\n",
    )
    _write_doc(
        root, "products/base-cn.md",
        "name: base-cn\ndescription: CN overlay.\nreference_type: product\n"
        "region: CN\nextends: base\n",
        "## Observation Conventions\n\nLocal-market business days (overlay).\n",
    )
    return root


def test_resolver_plain_class(tmp_path: Path) -> None:
    resolved = resolve_product_reference("SnowballOption", root=_inheritance_tree(tmp_path))
    assert "Autocallable payoff" in resolved.content
    assert "overlay" not in resolved.content  # no region requested


def test_resolver_merges_base_before_child(tmp_path: Path) -> None:
    resolved = resolve_product_reference("PhoenixOption", root=_inheritance_tree(tmp_path))
    assert resolved.content.index("KO barrier") < resolved.content.index("Coupon barrier")
    assert len(resolved.source_paths) == 2
    # Per-heading merge: base + child Pricing Inputs fold into ONE section —
    # a flat body concat would leave two "## Pricing Inputs" headings and the
    # coherence net's single-match section regex would drop the child's text.
    assert resolved.content.count("## Pricing Inputs") == 1


def test_resolver_appends_region_overlay(tmp_path: Path) -> None:
    resolved = resolve_product_reference(
        "SnowballOption", region="CN", root=_inheritance_tree(tmp_path)
    )
    assert "## Regional Conventions (CN)" in resolved.content
    assert resolved.content.index("Autocallable payoff") < resolved.content.index("overlay")


def test_resolver_unknown_class(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        resolve_product_reference("NopeOption", root=_inheritance_tree(tmp_path))
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_reference_docs.py -v -k resolver`
Expected: FAIL — ImportError on `resolve_product_reference`.

- [ ] **Step 3: Implement** — append to `reference_docs.py`:

```python
@dataclass(frozen=True)
class ResolvedReferenceDoc:
    quantark_class: str
    content: str
    source_paths: tuple[Path, ...]


def resolve_product_reference(
    quantark_class: str, *, region: str | None = None, root: Path = REFERENCES_DIR
) -> ResolvedReferenceDoc:
    claims = validate_product_reference_tree(root)
    claiming = claims.get(quantark_class)
    if claiming is None:
        raise KeyError(f"No product reference doc claims {quantark_class!r}")

    docs = validate_reference_doc_tree(root)
    products = {d.frontmatter["name"]: d for d in docs if d.frontmatter["reference_type"] == "product"}

    parts: list[ReferenceDoc] = []
    base_name = claiming.frontmatter.get("extends")
    if base_name is not None:
        parts.append(products[base_name])
    parts.append(claiming)

    # Per-heading merge (spec D1): base sections first, child text appended
    # UNDER THE SAME H2 heading; child-only headings appended after. A flat
    # body concat would emit duplicate "## Pricing Inputs" headings and the
    # coherence net's section regex would only see the base's text.
    merged: dict[str, list[str]] = {}
    for part in parts:
        for heading, text in _h2_sections(part.body):
            merged.setdefault(heading, []).append(text)
    content = "\n\n".join(
        f"## {heading}\n\n" + "\n\n".join(texts) for heading, texts in merged.items()
    )
    paths = [p.path for p in parts]

    if region is not None:
        for doc in products.values():
            if (
                doc.frontmatter.get("region") == region
                and doc.frontmatter.get("extends") == claiming.frontmatter["name"]
            ):
                content += f"\n\n## Regional Conventions ({region})\n\n{doc.body}"
                paths.append(doc.path)
    return ResolvedReferenceDoc(
        quantark_class=quantark_class, content=content, source_paths=tuple(paths)
    )


def _h2_sections(body: str) -> list[tuple[str, str]]:
    """Split a doc body into (h2-heading, section-text) pairs, in order."""
    import re

    sections: list[tuple[str, str]] = []
    for match in re.finditer(r"^## (.+)$\n(.*?)(?=^## |\Z)", body, re.M | re.S):
        sections.append((match.group(1).strip(), match.group(2).strip()))
    return sections
```

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/test_reference_docs.py -v` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/reference_docs.py tests/test_reference_docs.py
git commit -m "feat: resolve_product_reference merges extends base + region overlay"
```

---

### Task 3: Term glossary + region denylist

**Files:**
- Create: `backend/app/services/domains/term_glossary.py`

**Interfaces:**
- Produces: `TERM_GLOSSARY: dict[str, tuple[str, ...]]` mapping every **full dotted key** appearing in any contract's `required_bound` or `defaulted` to phrases, first phrase canonical. `REGION_TOKEN_DENYLIST: tuple[str, ...]`. `glossary_phrases(key: str) -> tuple[str, ...]` (exact-key lookup with leaf fallback).

- [ ] **Step 1: Implement** (data module — its test is the coherence net in Task 5, which consumes real docs; a unit test here would only restate the dict):

```python
"""Canonical desk phrases for contract keys — the test-side vocabulary that
lets the coherence net check reference-doc prose against FamilyContracts.

Keys are the full dotted paths from product_contracts; phrases are the
strings a doc's Pricing Inputs section must contain (any one suffices;
first is canonical). Scope is strictly required_bound + defaulted keys."""
from __future__ import annotations

TERM_GLOSSARY: dict[str, tuple[str, ...]] = {
    "initial_price": ("initial price", "spot"),
    "maturity_years": ("maturity", "tenor"),
    "trade_start_date": ("trade start date",),
    "observation_frequency": ("observation frequency", "observation schedule"),
    "barrier_config.ko_barrier": ("KO barrier", "knock-out barrier"),
    "barrier_config.ki_barrier": ("KI barrier", "knock-in barrier"),
    "barrier_config.ko_rate": ("KO coupon", "coupon"),
    "barrier_config.lockup_months": ("lockup",),
    "ko_observation_dates": ("KO observation dates", "observation dates"),
    "ki_convention": ("KI convention",),
    "ko_rate_annualized": ("annualized", "coupon convention"),
    "initial_date": ("initial date",),
    "settlement_date": ("settlement date",),
    "post_barrier_config.ko_barrier": ("post-KI KO barrier", "KO barrier"),
    "post_barrier_config.ko_rate": ("post-KI KO coupon", "KO coupon"),
    "coupon_config.coupon_barrier": ("coupon barrier",),
    "coupon_config.coupon_rate": ("coupon rate",),
    "memory_coupon": ("memory coupon",),
    "strike": ("strike",),
    "option_type": ("option type", "call or put"),
    "contract_multiplier": ("contract multiplier",),
    "averaging_frequency": ("averaging frequency", "averaging schedule"),
    "cash_payoff": ("cash payoff", "fixed cash amount"),
    "barrier": ("barrier",),
    "barrier_type": ("barrier type",),
    "rebate": ("rebate",),
    "participation_rate": ("participation rate",),
    "lower_barrier": ("lower barrier",),
    "upper_barrier": ("upper barrier",),
    "barrier_direction": ("barrier direction",),
    "touch_type": ("touch type",),
    "range_config.lower_barrier": ("lower barrier",),
    "range_config.upper_barrier": ("upper barrier",),
    "range_config.accrual_rate": ("accrual rate",),
    "underlying": ("underlying",),
    "basis": ("basis",),
    "basis_decay_rate": ("basis decay",),
    "market_price": ("market price",),
    "contract_code": ("contract code",),
    "deltaone_type": ("delta-one type",),
    "instrument_code": ("instrument code",),
    "exchange": ("exchange",),
}

# Region-market tokens forbidden in region-neutral product docs. Region
# overlays (frontmatter `region:`) are exempt. Extend in one place.
REGION_TOKEN_DENYLIST: tuple[str, ...] = (
    "SSE",
    "China Mainland",
    "CSI",
    "A-share",
)


def glossary_phrases(key: str) -> tuple[str, ...]:
    if key in TERM_GLOSSARY:
        return TERM_GLOSSARY[key]
    leaf = key.rsplit(".", 1)[-1]
    return TERM_GLOSSARY.get(leaf, ())
```

- [ ] **Step 2: Sanity import** — `.venv/bin/python -c "import sys; sys.path.insert(0, 'backend'); from app.services.domains.term_glossary import TERM_GLOSSARY; print(len(TERM_GLOSSARY))"` → prints `42`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/domains/term_glossary.py
git commit -m "feat: contract-key term glossary + region token denylist"
```

---

### Task 4: The nine reference docs (snowball split + eight families)

**Files:**
- Create: `backend/app/skills/references/products/{snowball,vanilla,asian,digital-touch,barrier,sharkfin,range-accrual,autocallable-variants,delta-one}.md`
- Modify: `backend/app/skills/references/products/snowball-cn.md` (becomes overlay)
- Modify: `tests/test_reference_docs.py` `EXPECTED_REFERENCE_FILES` (+9 entries)

**Interfaces:**
- Consumes: frontmatter schema (Task 1). Docs must satisfy the Task 5 coherence net: every claimed contract's `required_bound` key findable via `glossary_phrases` in the resolved `## Pricing Inputs`.

- [ ] **Step 1: Update `EXPECTED_REFERENCE_FILES`** in `tests/test_reference_docs.py` — add:

```python
    "products/snowball.md",
    "products/vanilla.md",
    "products/asian.md",
    "products/digital-touch.md",
    "products/barrier.md",
    "products/sharkfin.md",
    "products/range-accrual.md",
    "products/autocallable-variants.md",
    "products/delta-one.md",
```

Run `.venv/bin/python -m pytest tests/test_reference_docs.py::test_reference_doc_file_set_matches_phase3_target -v` → FAIL (files missing yet).

- [ ] **Step 2: Write `products/snowball.md`** (region-neutral base; payoff text lifted from today's `snowball-cn.md` minus CN facts):

```markdown
---
name: snowball
description: Region-neutral snowball autocallable payoff semantics and pricing inputs.
reference_type: product
quantark_classes:
  - SnowballOption
---

## Product Definition

A snowball is a path-dependent autocallable on a single underlying.
Scheduled knock-out (KO) observations pay accrued coupon and principal
early when the observed level is at or above the KO barrier. If the trade
never knocks out and never knocks in, it returns full principal at
maturity. If it knocks in (KI) and never knocks out, terminal payoff takes
equity loss versus strike. Knock-in is sticky once observed: lifecycle
fields override any assumption that the KI barrier is still conditional.

## Observation Conventions

KO observations follow the trade's observation frequency and KO
observation dates (explicit dates required when frequency is CUSTOM). The
KI convention is a term of the trade: daily discrete observation, a single
European observation at maturity, or no-KI (removes the knock-in leg).
A lockup period of some months may suppress early KO observations. Day
count and holiday calendar are desk defaults, configurable per deployment.

## Pricing Inputs

Required: initial price (spot), maturity in years, trade start date,
observation frequency, KO barrier, KI barrier, KO coupon (ko_rate),
lockup months, and KO observation dates when the frequency is CUSTOM.
Defaulted: KI convention, annualized-coupon convention (ko_rate_annualized),
initial date, settlement date. Lifecycle state (knocked-in flag) is a
required interpretation input before any valuation.

## Diagnostics

Spot near the KI barrier indicates elevated gamma and warrants hedge
review. Spot near the next KO observation warrants repricing with fresh
market data. A knocked-in lifecycle flag that disagrees with the imported
lifecycle state is a data-quality issue before it is a pricing issue.
```

- [ ] **Step 3: Rework `products/snowball-cn.md`** into a pure overlay (replace the whole file):

```markdown
---
name: snowball-cn
description: CN market overlay for snowball products - calendars, index universe, desk diagnostics.
reference_type: product
region: CN
extends: snowball
---

## Observation Conventions

CN snowballs typically reference one A-share index underlying (commonly
CSI 300, CSI 500, or CSI 1000) with monthly KO observations. Daily KI is
the default convention and uses discrete SSE business-day observations
from trade start plus one day through exercise date. ACT/365 and China
Mainland exchange calendars are the default desk conventions for imported
CN snowballs.

## Diagnostics

Spot within 5 percent of KI indicates elevated gamma and should be flagged
for hedge review. Spot within 2 percent of the next KO observation should
be repriced with fresh market data if the prior run is older than one
business day.
```

- [ ] **Step 4: Write the remaining seven family docs.** Each file below in full.

`products/vanilla.md`:

```markdown
---
name: vanilla
description: European and American vanilla option semantics and pricing inputs.
reference_type: product
quantark_classes:
  - EuropeanVanillaOption
  - AmericanOption
---

## Product Definition

A vanilla option pays the intrinsic value of a call or put on a single
underlying. European exercise settles only at maturity; American exercise
allows early exercise any time up to maturity, which adds an early-exercise
premium priced by the engine, not by convention.

## Pricing Inputs

Required: initial price (spot), maturity in years, strike.
Defaulted: option type (call or put; desk default, configurable) and
contract multiplier. American and European variants share the same term
set; the family choice itself selects the exercise style.

## Diagnostics

Deep in-the-money American puts warrant an early-exercise check against
carry. A vanilla whose quoted volatility input is stale versus the desk
surface is a data-quality issue before it is a pricing issue.
```

`products/asian.md`:

```markdown
---
name: asian
description: Asian (average-price) option semantics and pricing inputs.
reference_type: product
quantark_classes:
  - AsianOption
---

## Product Definition

An Asian option pays against the average of scheduled observations of the
underlying rather than the terminal level, reducing sensitivity to any
single fixing. Recorded fixings become part of the trade's lifecycle: once
captured, they are facts, not forecasts.

## Observation Conventions

The averaging schedule derives from the averaging frequency over the
trade's life. If a booked record lacks captured fixings, fall back to the
full number of observations - do not renormalize the average. Calendars
and day count are desk defaults, configurable.

## Pricing Inputs

Required: initial price (spot), maturity in years, strike.
Defaulted: option type, contract multiplier, averaging frequency
(averaging schedule granularity; desk default, configurable).

## Diagnostics

Missing or duplicated captured fixings versus the averaging schedule is a
data-quality issue. Late in the averaging window, remaining optionality
shrinks - large vega on a nearly-averaged trade warrants a model check.
```

`products/digital-touch.md`:

```markdown
---
name: digital-touch
description: Cash-or-nothing digital and one-touch/double-one-touch semantics and inputs.
reference_type: product
quantark_classes:
  - CashOrNothingDigitalOption
  - OneTouchOption
  - DoubleOneTouchOption
---

## Product Definition

These families pay a fixed cash amount on a trigger. A cash-or-nothing
digital pays the cash payoff at maturity if the terminal level is beyond
the strike. A one-touch pays if the underlying ever touches a barrier
before maturity; a double one-touch references an upper barrier and a
lower barrier. Touch type controls pay-at-touch versus pay-at-maturity.

## Pricing Inputs

Digital - required: initial price (spot), maturity in years, strike, cash
payoff. Defaulted: option type, contract multiplier.
One-touch - required: initial price, maturity in years, barrier, cash
payoff. Defaulted: barrier direction, touch type.
Double one-touch - required: initial price, maturity in years, upper
barrier, lower barrier, cash payoff. Defaulted: touch type.

## Diagnostics

Spot near any barrier concentrates gamma and digital risk at the trigger;
flag for hedge review. Ambiguous barrier direction on an imported one-touch
is a blocking interpretation gap - ask, do not infer.
```

`products/barrier.md`:

```markdown
---
name: barrier
description: Single-barrier (knock-in/knock-out) option semantics and pricing inputs.
reference_type: product
quantark_classes:
  - BarrierOption
---

## Product Definition

A barrier option is a vanilla payoff gated by a barrier event. Knock-out
variants cancel the option when the barrier is touched (optionally paying
a rebate); knock-in variants activate it. The barrier type encodes
direction and gating (e.g. up-and-out, down-and-in). Barrier events are
sticky lifecycle facts once observed.

## Pricing Inputs

Required: initial price (spot), maturity in years, strike, barrier.
Defaulted: option type, contract multiplier, barrier type (desk default,
configurable), rebate (amount paid on knock-out; defaults to none).

## Diagnostics

Spot near the barrier concentrates gamma and makes valuation sensitive to
observation timing; flag for hedge review. An imported row whose barrier
type disagrees with its moneyness at trade date is a data-quality issue.
```

`products/sharkfin.md`:

```markdown
---
name: sharkfin
description: Single and double sharkfin option semantics and pricing inputs.
reference_type: product
quantark_classes:
  - SingleSharkfinOption
  - DoubleSharkfinOption
---

## Product Definition

A sharkfin combines a participating vanilla leg with a knock-out barrier:
payoff grows with the underlying at the participation rate until the
barrier knocks the structure out, after which the holder receives the
knocked-out terms. The single variant carries one barrier; the double
variant carries an upper barrier and a lower barrier around the strike.

## Pricing Inputs

Single - required: initial price (spot), maturity in years, strike,
barrier. Double - required: initial price, maturity in years, strike,
lower barrier, upper barrier. Both default: option type, contract
multiplier, participation rate (desk default, configurable).

## Diagnostics

Spot near a knock-out barrier means the participating leg can vanish;
gamma and vega concentrate there - flag for hedge review. A participation
rate far from recent desk levels on an imported row warrants source
verification before pricing.
```

`products/range-accrual.md`:

```markdown
---
name: range-accrual
description: Range accrual option semantics and pricing inputs.
reference_type: product
quantark_classes:
  - RangeAccrualOption
---

## Product Definition

A range accrual accrues coupon for each scheduled observation on which the
underlying fixes inside the range between the lower barrier and the upper
barrier. Observations outside the range accrue nothing. The payoff is the
accumulated accrual paid per the trade's settlement terms.

## Observation Conventions

Accrual observations follow the trade's observation frequency; each
observation is an independent in-range test against the same range unless
the terms say otherwise. Calendars and day count are desk defaults,
configurable.

## Pricing Inputs

Required: initial price (spot), maturity in years, lower barrier, upper
barrier, accrual rate (the per-period coupon accrued while in range).
Defaulted: observation frequency, contract multiplier.

## Diagnostics

Spot hovering at a range edge makes daily accrual binary and concentrates
risk; flag for hedge review. Verify the accrual rate quotation (per period
versus annualized) on imported rows - a mismatch is a data-quality issue.
```

`products/autocallable-variants.md`:

```markdown
---
name: autocallable-variants
description: KO-reset and Phoenix autocallable deltas on top of the snowball base semantics.
reference_type: product
quantark_classes:
  - KnockOutResetSnowballOption
  - PhoenixOption
extends: snowball
---

## Product Definition

Both families share the snowball base semantics (see the snowball
reference; inherited here). A KO-reset snowball re-arms after knock-in
with a post-KI barrier leg: a distinct post-KI KO barrier and post-KI KO
coupon apply once knock-in has occurred. A Phoenix pays periodic coupons
whenever the observed level is at or above a coupon barrier, independent
of autocall; with memory coupon enabled, missed coupons are recovered at
the next paying observation.

## Pricing Inputs

In addition to every inherited snowball input: KO-reset requires the
post-KI KO barrier and post-KI KO coupon (post_barrier_config). Phoenix
requires the coupon barrier and coupon rate (coupon_config) and defaults
the memory coupon flag (desk default, configurable).

## Diagnostics

For KO-reset, a knocked-in lifecycle flag switches which barrier leg
governs - re-check hedges at the switch. For Phoenix, spot near the coupon
barrier around an observation makes near-term carry binary; flag for
review.
```

`products/delta-one.md`:

```markdown
---
name: delta-one
description: Futures and spot instrument semantics and pricing inputs.
reference_type: product
quantark_classes:
  - Futures
  - SpotInstrument
---

## Product Definition

Delta-one instruments track the underlying one-for-one: no optionality, no
barriers. A futures position carries basis to the underlying that decays
toward expiry; a spot instrument is direct exposure. These families are
primarily hedging and inventory instruments.

## Pricing Inputs

Required: initial price and the underlying identifier.
Futures defaults: contract multiplier, maturity in years, basis, basis
decay rate, market price, contract code. Spot defaults: delta-one type,
instrument code, exchange, contract multiplier. All defaults are desk
defaults, configurable.

## Diagnostics

A futures mark diverging from spot plus modeled basis indicates a stale
market price or wrong contract code. Missing contract multiplier on an
imported row silently mis-scales exposure - verify against the exchange
contract specification.
```

- [ ] **Step 5: Run the doc-set tests**

Run: `.venv/bin/python -m pytest tests/test_reference_docs.py -v`
Expected: all PASS (file set matches, schema valid, tree validates).

- [ ] **Step 6: Commit**

```bash
git add backend/app/skills/references/products tests/test_reference_docs.py
git commit -m "feat: region-neutral product reference docs for all 15 families (snowball base/CN-overlay split)"
```

---

### Task 5: Coherence net

**Files:**
- Create: `tests/test_semantic_coherence.py`

**Interfaces:**
- Consumes: `validate_product_reference_tree`, `resolve_product_reference` (Tasks 1–2), `TERM_GLOSSARY`/`REGION_TOKEN_DENYLIST`/`glossary_phrases` (Task 3), `_CONTRACTS` via `app.services.domains.product_contracts.contract_for` and the private map.

- [ ] **Step 1: Write the tests** (these should PASS against Task 4's docs; any failure is a doc bug — fix the doc, not the test):

```python
"""Semantic coherence net: prose reference docs must match FamilyContracts.

The 'reasoner' of the semantic layer - fails CI when a contract gains a key
the claiming doc does not explain, when a family loses its doc, or when a
region-neutral doc leaks region-market tokens."""
from __future__ import annotations

import re

import pytest

from app.services.deep_agent.reference_docs import (
    REFERENCES_DIR,
    resolve_product_reference,
    validate_product_reference_tree,
    validate_reference_doc_tree,
)
from app.services.domains.product_contracts import _CONTRACTS
from app.services.domains.term_glossary import (
    REGION_TOKEN_DENYLIST,
    TERM_GLOSSARY,
    glossary_phrases,
)


def _pricing_inputs_section(content: str) -> str:
    match = re.search(r"^## Pricing Inputs$(.*?)(?=^## |\Z)", content, re.M | re.S)
    assert match, "resolved doc has no '## Pricing Inputs' section"
    return match.group(1)


def test_every_contract_class_is_claimed_exactly_once() -> None:
    claims = validate_product_reference_tree(REFERENCES_DIR)
    assert set(claims) == set(_CONTRACTS)


@pytest.mark.parametrize("quantark_class", sorted(_CONTRACTS))
def test_required_bound_keys_explained(quantark_class: str) -> None:
    resolved = resolve_product_reference(quantark_class)
    section = _pricing_inputs_section(resolved.content).lower()
    missing = []
    for key in _CONTRACTS[quantark_class].required_bound:
        phrases = glossary_phrases(key)
        assert phrases, f"glossary has no phrases for contract key {key!r}"
        if not any(p.lower() in section for p in phrases):
            missing.append(key)
    assert not missing, (
        f"{quantark_class}: required keys not explained in resolved "
        f"Pricing Inputs (doc {resolved.source_paths}): {missing}"
    )


def test_glossary_has_no_dead_entries() -> None:
    live: set[str] = set()
    for contract in _CONTRACTS.values():
        for key in contract.required_bound + contract.defaulted:
            live.add(key)
            live.add(key.rsplit(".", 1)[-1])
    dead = set(TERM_GLOSSARY) - live
    assert not dead, f"glossary entries match no contract key: {sorted(dead)}"


def test_family_docs_are_region_neutral() -> None:
    docs = validate_reference_doc_tree(REFERENCES_DIR)
    offenders = []
    for doc in docs:
        if doc.frontmatter.get("reference_type") != "product":
            continue
        if doc.frontmatter.get("region") is not None:
            continue  # explicit overlay - exempt
        for token in REGION_TOKEN_DENYLIST:
            if token in doc.body:
                offenders.append((doc.path.name, token))
    assert not offenders, f"region tokens in neutral docs: {offenders}"


def test_resolved_neutral_docs_are_region_neutral() -> None:
    claims = validate_product_reference_tree(REFERENCES_DIR)
    for quantark_class in claims:
        resolved = resolve_product_reference(quantark_class)  # region=None
        for token in REGION_TOKEN_DENYLIST:
            assert token not in resolved.content, (
                f"{quantark_class}: neutral resolution leaks region token {token!r}"
            )
```

- [ ] **Step 2: Run** — `.venv/bin/python -m pytest tests/test_semantic_coherence.py -v` → all PASS. If `test_required_bound_keys_explained` fails, edit the named doc's `## Pricing Inputs` (or add a glossary alias if the doc phrase is legitimate), never weaken the test.

- [ ] **Step 3: Mutation check (verifies the net bites)** — temporarily add `"made_up_key",` to `_SNOWBALL_CONTRACT.required_bound` in `product_contracts.py`, run the file again, confirm `test_required_bound_keys_explained[SnowballOption]` FAILS (glossary has no phrases), then **revert the mutation** and re-run to green.

- [ ] **Step 4: Commit**

```bash
git add tests/test_semantic_coherence.py
git commit -m "test: semantic coherence net (claims, required-bound prose, glossary hygiene, region lint)"
```

---

### Task 6: `desk_region` setting + `get_product_reference_doc` tool

**Files:**
- Modify: `backend/app/config.py` (both Settings representations — the pydantic fields block near `gateway_agent_model` at ~line 175 and the dataclass mirror near ~line 281; copy that field's exact pattern with `validation_alias="OPEN_OTC_DESK_REGION"`, default `None`)
- Create: `backend/app/tools/product_reference.py`
- Modify: `backend/app/tools/__init__.py` (import + append to `QUANT_AGENT_TOOLS` at line 138)
- Modify: `backend/app/services/agents.py` (add `"get_product_reference_doc"` to `DEEP_AGENT_TOOL_NAMES` at line 372)
- Test: `tests/test_product_reference_tool.py`

**Interfaces:**
- Consumes: `resolve_product_reference` (Task 2), `get_settings().desk_region`.
- Produces: tool `get_product_reference_doc(quantark_class: str)` returning `{"quantark_class": str, "content": str, "sources": list[str]}` or `{"error": str, "known_classes": list[str]}`.

- [ ] **Step 1: Write the failing tests**:

```python
"""Tool-level smokes for get_product_reference_doc - proves the runtime
loads the same merged content the coherence net validated (CI/runtime
parity), including the snowball base+CN-overlay split."""
from __future__ import annotations

from app.tools.product_reference import get_product_reference_doc
from app.services.agents import DEEP_AGENT_TOOL_NAMES
from app.tools import QUANT_AGENT_TOOLS


def _invoke(quantark_class: str) -> dict:
    return get_product_reference_doc.invoke({"quantark_class": quantark_class})


def test_tool_is_registered_everywhere() -> None:
    assert "get_product_reference_doc" in {t.name for t in QUANT_AGENT_TOOLS}
    assert "get_product_reference_doc" in DEEP_AGENT_TOOL_NAMES


def test_plain_family_returns_own_doc() -> None:
    result = _invoke("SingleSharkfinOption")
    assert "participation rate" in result["content"].lower()


def test_inherited_family_includes_base_terms() -> None:
    result = _invoke("KnockOutResetSnowballOption")
    content = result["content"].lower()
    assert "post-ki ko barrier" in content          # own delta
    assert "ki barrier" in content                   # inherited from snowball base
    assert "observation frequency" in content        # inherited


def test_snowball_with_cn_region_keeps_base_and_overlay() -> None:
    # Settings is a frozen dataclass and get_settings() is NOT cached — the
    # override seam is configure_settings (established pattern, see
    # tests/test_stream_and_persist.py / tests/gateway/test_cards.py).
    import dataclasses

    from app.config import configure_settings, get_settings

    configure_settings(dataclasses.replace(get_settings(), desk_region="CN"))
    try:
        result = _invoke("SnowballOption")
        content = result["content"]
        assert "ko barrier" in content.lower()
        assert "## Regional Conventions (CN)" in content
    finally:
        configure_settings(None)


def test_unknown_class_lists_known() -> None:
    result = _invoke("NopeOption")
    assert "error" in result
    assert "SnowballOption" in result["known_classes"]
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_product_reference_tool.py -v` → ImportError.

- [ ] **Step 3: Implement the setting** in both `config.py` sites (mirror `gateway_agent_model` exactly, name `desk_region`, alias `OPEN_OTC_DESK_REGION`, default `None`). Then the tool:

```python
"""Read-only agent tool exposing resolved product reference docs.

Skills are markdown and cannot call Python; this tool is the runtime
surface of resolve_product_reference so the agent reads the SAME merged
content the coherence net validates. Applies the configured desk_region."""
from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.deep_agent.reference_docs import (
    resolve_product_reference,
    validate_product_reference_tree,
)


class GetProductReferenceDocInput(BaseModel):
    quantark_class: str = Field(
        description="QuantArk family class, e.g. SnowballOption, BarrierOption."
    )


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_product_reference_doc", args_schema=GetProductReferenceDocInput)
def get_product_reference_doc(quantark_class: str) -> dict:
    """Load the resolved product reference doc (definitions, conventions,
    required pricing inputs, diagnostics) for a product family. Merges the
    family doc with its inheritance base and the desk's region overlay.
    Call this instead of reading /skills/references/products files."""
    try:
        resolved = resolve_product_reference(
            quantark_class, region=get_settings().desk_region
        )
    except KeyError:
        return {
            "error": f"No reference doc claims {quantark_class!r}",
            "known_classes": sorted(validate_product_reference_tree()),
        }
    return {
        "quantark_class": resolved.quantark_class,
        "content": resolved.content,
        "sources": [str(p) for p in resolved.source_paths],
    }
```

Register: in `backend/app/tools/__init__.py` import `from .product_reference import get_product_reference_doc` and append `get_product_reference_doc,` to `QUANT_AGENT_TOOLS`; in `backend/app/services/agents.py` add `"get_product_reference_doc",` to `DEEP_AGENT_TOOL_NAMES` (keep alphabetical grouping style; add a one-line comment `# product semantics (semantic-layer completion)`).

*(Check `@tool`/`@capability_gated` decorator order against `backend/app/tools/assumptions.py` and match it exactly.)*

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_product_reference_tool.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/app/tools/product_reference.py backend/app/tools/__init__.py backend/app/services/agents.py tests/test_product_reference_tool.py
git commit -m "feat: get_product_reference_doc tool + desk_region setting (registered in both tool registries)"
```

---

### Task 7: Generic skill + snowball skill migration

**Files:**
- Create: `backend/app/skills/workflows/products/product-term-interpretation/SKILL.md`
- Modify: `backend/app/skills/workflows/snowballs/snowball-term-interpretation/SKILL.md`
- Modify: `backend/app/skills/workflows/snowballs/snowball-pricing/SKILL.md:36,55`
- Modify: `backend/app/skills/workflows/snowballs/snowball-risk-explain/SKILL.md:35,59`

**Interfaces:**
- Consumes: `get_product_reference_doc` (Task 6).
- Produces: routing frontmatter that feeds the data-driven routing table (Task 8 updates the exact-set tests).

- [ ] **Step 1: Write the new SKILL.md** (mirrors snowball-term-interpretation; ≤500 tokens):

```markdown
---
name: product-term-interpretation
description: Explain payoff terms and conventions for any non-snowball product family (vanilla, Asian, digital, touch, barrier, sharkfin, range accrual, autocallable variants, delta-one). Use when a position, imported row, or user question has ambiguous terms for these families, or a pricing or risk workflow needs term interpretation before computation.
domain: products
workflow_type: read
allowed_envelopes:
  - pet_page
  - pet_diagnostic
  - desk_workflow
may_escalate_to:
  - desk_workflow
required_context:
  - terms
optional_context:
  - position_id
  - product_key
write_actions: false
confirmation_required: false
success_criteria:
  - payoff terms and conventions are explained
  - ambiguous or missing economics are identified
routing:
  - request: "Non-snowball product terms or payoff interpretation"
    persona: trader
---

## When to use

- User asks what a non-snowball product's terms, barriers, or fields mean.
- Imported row or position for these families has ambiguous economics.
- Pricing or risk workflow needs term interpretation before computation.

## Required inputs

Use position terms, imported row fields, or explicit user text. Identify
the QuantArk family, then call `get_product_reference_doc` with it for the
resolved reference (definitions, conventions, required pricing inputs,
diagnostics). Do not read raw files under `/skills/references/products/`.

## Procedure

1. Identify the family and its economic fields from terms or the source row.
2. Call `get_product_reference_doc`; explain each term against it.
3. Flag missing or inconsistent required inputs that block pricing.
4. Route to the pricing workflow when the user asks for numbers.

## Stop conditions

Do not infer missing barriers, payoffs, or lifecycle state from the
product name alone. Snowball-family questions route to
`snowball-term-interpretation` instead.

## Output shape

Interpretation first, then normalized terms, missing fields, caveats, and
next workflow.

## Example

User: What does the accrual rate on this range accrual mean?
Assistant: Explain in-range accrual per observation, the range barriers,
and any missing required inputs.
```

- [ ] **Step 2: Migrate the three snowball skills.** In each file, replace the reference-doc instruction/reference lines:

`snowball-term-interpretation/SKILL.md` line 35 → `Call \`get_product_reference_doc\` with \`SnowballOption\` for payoff invariants and conventions (resolved base + regional overlay).` and the `## References` entry → `- \`get_product_reference_doc(SnowballOption)\``

`snowball-pricing/SKILL.md` line 36 → `Use \`position_id\` or explicit Snowball terms. Call \`get_product_reference_doc\` with \`SnowballOption\`; read \`/skills/references/pricing/engines.md\`.` and drop the `snowball-cn.md` bullet at line 55 in favor of `- \`get_product_reference_doc(SnowballOption)\``

`snowball-risk-explain/SKILL.md` line 35 → same substitution for the KI/KO-conventions read; update the line-59 reference bullet identically.

- [ ] **Step 3: Skill lint** — `.venv/bin/python -m pytest tests/test_skill_lint_routing.py -v` → expect PASS or exact-set failures that Task 8 fixes; if the *linter* itself rejects the new file (token cap, missing field), fix the SKILL.md now.

- [ ] **Step 4: Commit**

```bash
git add backend/app/skills/workflows/products/product-term-interpretation backend/app/skills/workflows/snowballs
git commit -m "feat: generic product-term-interpretation skill; snowball skills load resolved docs via tool"
```

---

### Task 8: Catalog / routing exact-set updates

**Files:**
- Modify (run first, then fix what fails): `tests/test_skills_catalog.py`, `tests/test_skills_catalog_v2.py`, `tests/test_workflow_skills_phase3.py`, `tests/test_remaining_workflow_skills_phase3.py`, `tests/test_routing_table.py`, `tests/test_skills_read_smoke_v2.py`

**Interfaces:**
- Consumes: the new skill + docs. These six files hold exact-set/count assertions (established coupling: adding one workflow SKILL.md breaks all six).

- [ ] **Step 1: Run the coupled files**

Run: `.venv/bin/python -m pytest tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py tests/test_routing_table.py tests/test_skills_read_smoke_v2.py -v`
Expected: exact-set/count FAILs naming `product-term-interpretation` and the nine new doc paths.

- [ ] **Step 2: Update each failing assertion** — add `product-term-interpretation` to skill-name sets and bump counts by exactly 1; add the nine `products/*.md` paths where doc sets are asserted. Make no other edits; if a failure is anything other than a missing new entry, treat it as a real bug in Tasks 4–7.

- [ ] **Step 3: Check the orchestrator routing surface** — `grep -rn "snowball-term-interpretation" backend/app/services/deep_agent/ backend/app/skills/meta/` ; wherever a routing line/table entry exists for it (orchestrator prompt or sentinel table), add the parallel entry for `product-term-interpretation`. If routing is fully frontmatter-driven, `tests/test_routing_table.py` from Step 2 already covers it and this step is a no-op — note which in the commit body.

- [ ] **Step 4: Re-run the six files** → all PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test: catalog/routing exact-sets absorb product-term-interpretation + 9 product docs"
```

---

### Task 9: Full suite + frontend type-check

- [ ] **Step 1: Backend suite** — `.venv/bin/python -m pytest` from repo root. Expected: green (known pre-existing exceptions: `.env`-leak tests per repo memory — verify any failure predates this branch with `git stash && pytest <file> && git stash pop` before touching it).

- [ ] **Step 2: Frontend untouched check** — this change is backend-only; run `cd frontend && npx tsc --noEmit` only if anything under `frontend/` was accidentally modified (`git status frontend/`).

- [ ] **Step 3: Commit any stragglers** — working tree must be clean; `git log --oneline` shows one commit per task.

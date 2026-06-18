---
name: model
description: Durable portfolio model conventions for membership, sources, and position queries.
reference_type: portfolio
---

## Portfolio Kinds

A Container portfolio explicitly holds positions and is changed by membership
operations. A View portfolio is defined by source or rule filters and recomputes
membership when queried. This distinction is user-visible when explaining why a
portfolio is empty or why a new imported position appears automatically.

## Position Membership

A position has one owning container context but may appear in multiple view
portfolios. Query APIs should resolve both kinds through the same portfolio
identifier so workflows do not need to branch on implementation details before
reading positions.

## Query Pattern

Use portfolio enumeration when the user names a portfolio ambiguously. Inspect
the selected portfolio before mutating membership or rules. Read positions
through the portfolio-aware position query path so derived views and explicit
containers produce consistent downstream pricing, risk, and reporting inputs.

## Empty Portfolio Semantics

An empty View can be valid when its rule matches no current positions. An empty
Container often means stale or incomplete membership. Surface that distinction
before running portfolio-level pricing or risk.

## Filter Rule DSL

Rules match positions in CONTAINER portfolios only (views never source other
views through rules — use sources for that). A view's `filter_rule` is a
nested dict. Leaf: `{"op": <op>, "field": <field>, "value": <value>}`.
Composite: `{"op": "and"|"or", "children": [<rule>, ...]}` (non-empty) and
`{"op": "not", "child": <rule>}`. Max nesting depth 5.

Validation errors surface from the tools as `{"ok": false, "errors": [...]}`.
Messages include `Unsupported op: ...` and `Unknown field: ...`.

Ops: `eq`, `ne` (scalar); `in`, `not_in` (list value); `lt`, `lte`, `gt`,
`gte` (scalar, ordered); `between` (value = [low, high]).

Fields and types: `product_type` (str), `underlying` (str), `status` (str:
open/closed), `mapping_status` (str), `engine_name` (str), `quantity` (float),
`entry_price` (float), `created_at` (datetime, ISO strings accepted).

Examples:

    {"op": "eq", "field": "underlying", "value": "000905.SH"}

    {"op": "and", "children": [
      {"op": "in", "field": "product_type",
       "value": ["SnowballOption", "PhoenixOption"]},
      {"op": "eq", "field": "status", "value": "open"}
    ]}

import { useEffect, useState } from 'react';
import { ALLOWED_FIELDS } from '../lib/ruleTree';
import { Select } from './Select';
import type { FilterRule } from '../types';
import './RuleBuilder.css';

export type LeafOp = 'eq' | 'ne' | 'in' | 'not_in' | 'lt' | 'lte' | 'gt' | 'gte';

export type Leaf = {
  op: LeafOp;
  field: string;
  value: string;
};

type Props = {
  rule: FilterRule | null;
  onChange: (rule: FilterRule | null) => void;
};

export const FIELDS = Object.keys(ALLOWED_FIELDS);
export const OPS: LeafOp[] = ['eq', 'ne', 'in', 'not_in', 'lt', 'lte', 'gt', 'gte'];

export function isLeaf(rule: FilterRule): rule is Extract<FilterRule, { field: string }> {
  return rule.op !== 'and' && rule.op !== 'or' && rule.op !== 'not';
}

export function ruleToLeaves(rule: FilterRule | null): Leaf[] {
  if (!rule) return [];
  if (rule.op === 'and') {
    return rule.children.flatMap((child) => {
      if (!isLeaf(child) || child.op === 'between') return [];
      return [{ op: child.op, field: child.field, value: String(child.value) }];
    });
  }
  if (isLeaf(rule) && rule.op !== 'between') {
    return [{ op: rule.op, field: rule.field, value: String(rule.value) }];
  }
  return [];
}

export function leafValue(leaf: Leaf): string | number | (string | number)[] {
  if (leaf.op === 'in' || leaf.op === 'not_in') {
    return leaf.value.split(',').map((value) => value.trim()).filter(Boolean);
  }
  if (ALLOWED_FIELDS[leaf.field] === 'number') {
    return Number(leaf.value);
  }
  return leaf.value;
}

export function leavesToRule(leaves: Leaf[]): FilterRule | null {
  if (leaves.length === 0) return null;
  const children = leaves.map((leaf) => ({
    op: leaf.op,
    field: leaf.field,
    value: leafValue(leaf),
  })) as FilterRule[];
  return { op: 'and', children };
}

export function RuleBuilder({ rule, onChange }: Props) {
  const [leaves, setLeaves] = useState<Leaf[]>(() => ruleToLeaves(rule));

  useEffect(() => {
    setLeaves(ruleToLeaves(rule));
  }, [rule]);

  function emit(next: Leaf[]) {
    setLeaves(next);
    onChange(leavesToRule(next));
  }

  return (
    <div className="wl-rulebuilder">
      {leaves.map((leaf, index) => (
        <div className="wl-rulebuilder__row" key={index}>
          <Select
            className="wl-rulebuilder__field"
            label="Field"
            value={leaf.field}
            onChange={(v) => emit(leaves.map((item, itemIndex) =>
              itemIndex === index ? { ...item, field: v } : item,
            ))}
            options={FIELDS.map((field) => ({ value: field, label: field }))}
          />
          <Select
            className="wl-rulebuilder__op"
            label="Op"
            value={leaf.op}
            onChange={(v) => emit(leaves.map((item, itemIndex) =>
              itemIndex === index ? { ...item, op: v as LeafOp } : item,
            ))}
            options={OPS.map((op) => ({ value: op, label: op }))}
          />
          <input
            className="wl-rulebuilder__value"
            aria-label="Value"
            value={leaf.value}
            onChange={(e) => emit(leaves.map((item, itemIndex) =>
              itemIndex === index ? { ...item, value: e.target.value } : item,
            ))}
          />
          <button type="button" className="wl-rulebuilder__remove" onClick={() => emit(leaves.filter((_, itemIndex) => itemIndex !== index))} aria-label="Remove condition">
            x
          </button>
        </div>
      ))}
    </div>
  );
}

import type { FilterRule } from '../types';

export const ALLOWED_OPS = new Set([
  'and', 'or', 'not',
  'eq', 'ne', 'in', 'not_in',
  'lt', 'lte', 'gt', 'gte', 'between',
]);

export const ALLOWED_FIELDS: Record<string, 'string' | 'number' | 'datetime'> = {
  product_type: 'string', underlying: 'string', status: 'string',
  mapping_status: 'string', engine_name: 'string',
  quantity: 'number', entry_price: 'number',
  created_at: 'datetime',
};

export const MAX_RULE_DEPTH = 5;

export function validateRule(rule: any, path = '$', depth = 0): string[] {
  if (depth > MAX_RULE_DEPTH) return [`Rule depth exceeds ${MAX_RULE_DEPTH} at ${path}`];
  if (!rule || typeof rule !== 'object') return [`Rule node must be object at ${path}`];
  const op = rule.op;
  if (!ALLOWED_OPS.has(op)) return [`Unsupported op: ${JSON.stringify(op)} at ${path}`];

  if (op === 'and' || op === 'or') {
    if (!Array.isArray(rule.children) || rule.children.length === 0) {
      return [`Empty children for ${op} at ${path}`];
    }
    return rule.children.flatMap((c: any, i: number) => validateRule(c, `${path}.children[${i}]`, depth + 1));
  }
  if (op === 'not') {
    if (!rule.child || typeof rule.child !== 'object') return [`'not' requires child at ${path}`];
    return validateRule(rule.child, `${path}.child`, depth + 1);
  }
  if (!(rule.field in ALLOWED_FIELDS)) {
    return [`Unknown field: ${JSON.stringify(rule.field)} at ${path}`];
  }
  if (op === 'in' || op === 'not_in') {
    if (!Array.isArray(rule.value) || rule.value.length === 0) {
      return [`'${op}' requires non-empty list at ${path}`];
    }
  } else if (op === 'between') {
    if (!Array.isArray(rule.value) || rule.value.length !== 2) {
      return [`'between' requires 2-element list at ${path}`];
    }
  } else if (Array.isArray(rule.value)) {
    return [`'${op}' requires scalar value at ${path}`];
  }
  return [];
}

export class DslSyntaxError extends Error {}

const TOKEN_RE = /\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|-?\d+(?:\.\d+)?|<=|>=|!=|=|<|>|\(|\)|,|[A-Za-z_][A-Za-z0-9_.\-]*)/y;
const KEYWORDS = new Set(['AND', 'OR', 'NOT', 'IN', 'BETWEEN']);

function tokenize(text: string): string[] {
  const out: string[] = [];
  TOKEN_RE.lastIndex = 0;
  while (TOKEN_RE.lastIndex < text.length) {
    const m = TOKEN_RE.exec(text);
    if (!m) throw new DslSyntaxError(`Unexpected character at ${TOKEN_RE.lastIndex}`);
    out.push(m[1]);
  }
  return out;
}

function isIdent(tok: string): boolean {
  return /^[A-Za-z_][A-Za-z0-9_.\-]*$/.test(tok) && !KEYWORDS.has(tok.toUpperCase());
}

function scalar(tok: string): string | number {
  if ((tok.startsWith('"') && tok.endsWith('"')) || (tok.startsWith("'") && tok.endsWith("'"))) {
    return tok.slice(1, -1).replace(/\\(.)/g, '$1');
  }
  const n = Number(tok);
  if (!Number.isNaN(n)) return n;
  return tok;
}

class Parser {
  i = 0;
  constructor(private tokens: string[]) {}
  peek() { return this.tokens[this.i]; }
  consume(expected?: string) {
    const t = this.tokens[this.i];
    if (t === undefined) throw new DslSyntaxError('Unexpected end');
    if (expected && t.toUpperCase() !== expected.toUpperCase()) {
      throw new DslSyntaxError(`Expected ${expected}, got ${t}`);
    }
    this.i++;
    return t;
  }
  parse(): FilterRule {
    const r = this.or();
    if (this.i !== this.tokens.length) throw new DslSyntaxError(`Trailing tokens`);
    return r;
  }
  or(): FilterRule {
    const left = this.and();
    const children = [left];
    while (this.peek()?.toUpperCase() === 'OR') { this.consume('OR'); children.push(this.and()); }
    return children.length === 1 ? children[0] : { op: 'or', children };
  }
  and(): FilterRule {
    const left = this.not();
    const children = [left];
    while (this.peek()?.toUpperCase() === 'AND') { this.consume('AND'); children.push(this.not()); }
    return children.length === 1 ? children[0] : { op: 'and', children };
  }
  not(): FilterRule {
    if (this.peek()?.toUpperCase() === 'NOT') { this.consume('NOT'); return { op: 'not', child: this.not() }; }
    return this.atom();
  }
  atom(): FilterRule {
    if (this.peek() === '(') { this.consume('('); const r = this.or(); this.consume(')'); return r; }
    return this.leaf();
  }
  leaf(): FilterRule {
    const ident = this.consume();
    if (!isIdent(ident)) throw new DslSyntaxError(`Expected field, got ${ident}`);
    const op = this.consume();
    const u = op.toUpperCase();
    if (u === 'NOT') { this.consume('IN'); return { op: 'not_in', field: ident, value: this.list() }; }
    if (u === 'IN') return { op: 'in', field: ident, value: this.list() };
    if (u === 'BETWEEN') {
      const lo = scalar(this.consume());
      this.consume('AND');
      const hi = scalar(this.consume());
      return { op: 'between', field: ident, value: [lo as any, hi as any] };
    }
    const sym: Record<string, FilterRule['op']> = { '=':'eq','!=':'ne','<':'lt','<=':'lte','>':'gt','>=':'gte' };
    const m = sym[op];
    if (!m) throw new DslSyntaxError(`Unknown op ${op}`);
    return { op: m, field: ident, value: scalar(this.consume()) } as FilterRule;
  }
  list(): (string | number)[] {
    this.consume('(');
    const out: (string | number)[] = [];
    if (this.peek() !== ')') {
      out.push(scalar(this.consume()));
      while (this.peek() === ',') { this.consume(','); out.push(scalar(this.consume())); }
    }
    this.consume(')');
    return out;
  }
}

export function parseDsl(text: string): FilterRule {
  if (!text.trim()) throw new DslSyntaxError('Empty rule text');
  return new Parser(tokenize(text)).parse();
}

function quote(v: any): string {
  if (typeof v === 'number') return String(v);
  const s = String(v);
  if (/^[A-Za-z_][A-Za-z0-9_.\-]*$/.test(s)) return s;
  return '"' + s.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"';
}

export function serializeDsl(rule: FilterRule): string {
  switch (rule.op) {
    case 'and': return rule.children.map(serializeDsl).join(' AND ');
    case 'or':  return '(' + rule.children.map(serializeDsl).join(' OR ') + ')';
    case 'not': return 'NOT (' + serializeDsl(rule.child) + ')';
    case 'in':  return `${rule.field} IN (${rule.value.map(quote).join(', ')})`;
    case 'not_in': return `${rule.field} NOT IN (${rule.value.map(quote).join(', ')})`;
    case 'between': return `${rule.field} BETWEEN ${quote(rule.value[0])} AND ${quote(rule.value[1])}`;
  }
  const symMap: Record<string, string> = { eq:'=', ne:'!=', lt:'<', lte:'<=', gt:'>', gte:'>=' };
  return `${(rule as any).field} ${symMap[rule.op]} ${quote((rule as any).value)}`;
}

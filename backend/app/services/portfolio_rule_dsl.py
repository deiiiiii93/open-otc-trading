"""Power-user DSL parser and serializer for filter rules.

Round-trips through the canonical JSON expression tree defined in
``portfolio_rule.py``. Designed to be small and predictable rather than
expressive — only the published grammar is supported.

Grammar (informal):
    expr      := or_expr
    or_expr   := and_expr ("OR" and_expr)*
    and_expr  := not_expr ("AND" not_expr)*
    not_expr  := "NOT" not_expr | atom
    atom      := "(" expr ")" | leaf
    leaf      := IDENT op value
    op        := "=" | "!=" | "<" | "<=" | ">" | ">=" | "IN" | "NOT IN" | "BETWEEN"
    value     := scalar | "(" scalar ("," scalar)* ")" | scalar "AND" scalar  (BETWEEN)
    scalar    := QUOTED_STRING | NUMBER | BAREWORD

All numeric literals (including integer-shaped tokens like ``10``) parse as
``float``. Scientific notation (``1e-20``, ``2.5E+5``) is supported.
"""
from __future__ import annotations

import re
from typing import Any


class DslSyntaxError(ValueError):
    pass


_TOKEN_RE = re.compile(
    r"""
    \s* (
          "(?:[^"\\]|\\.)*"          # quoted string
        | '(?:[^'\\]|\\.)*'          # quoted string (single)
        | -?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?  # number (with optional scientific notation)
        | <=|>=|!=|=|<|>             # comparison
        | \(|\)|,                    # punctuation
        | [A-Za-z_][A-Za-z0-9_.\-]*  # identifier / bareword
    )
    """,
    re.VERBOSE,
)

_KEYWORDS = {"AND", "OR", "NOT", "IN", "BETWEEN"}


def _tokenize(text: str) -> list[str]:
    pos = 0
    out: list[str] = []
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise DslSyntaxError(f"Unexpected character at offset {pos}: {text[pos]!r}")
        tok = m.group(1)
        out.append(tok)
        pos = m.end()
    return out


def _is_ident(tok: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\-]*", tok)) and tok.upper() not in _KEYWORDS


def _scalar(tok: str) -> Any:
    if (tok.startswith('"') and tok.endswith('"')) or (tok.startswith("'") and tok.endswith("'")):
        return tok[1:-1].encode().decode("unicode_escape")
    try:
        if "." in tok or "e" in tok or "E" in tok:
            return float(tok)
        return float(int(tok))
    except ValueError:
        return tok


class _Parser:
    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.i = 0

    def _peek(self) -> str | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def _consume(self, expected: str | None = None) -> str:
        if self.i >= len(self.tokens):
            raise DslSyntaxError(f"Unexpected end of input; expected {expected!r}")
        tok = self.tokens[self.i]
        if expected is not None and tok.upper() != expected.upper():
            raise DslSyntaxError(f"Expected {expected!r}, got {tok!r}")
        self.i += 1
        return tok

    def parse(self) -> dict:
        rule = self._or()
        if self.i != len(self.tokens):
            raise DslSyntaxError(f"Trailing tokens: {self.tokens[self.i:]}")
        return rule

    def _or(self) -> dict:
        left = self._and()
        children = [left]
        while True:
            tok = self._peek()
            if tok is None or tok.upper() != "OR":
                break
            self._consume("OR")
            children.append(self._and())
        return left if len(children) == 1 else {"op": "or", "children": children}

    def _and(self) -> dict:
        left = self._not()
        children = [left]
        while True:
            tok = self._peek()
            if tok is None or tok.upper() != "AND":
                break
            self._consume("AND")
            children.append(self._not())
        return left if len(children) == 1 else {"op": "and", "children": children}

    def _not(self) -> dict:
        tok = self._peek()
        if tok is not None and tok.upper() == "NOT":
            self._consume("NOT")
            return {"op": "not", "child": self._not()}
        return self._atom()

    def _atom(self) -> dict:
        tok = self._peek()
        if tok == "(":
            self._consume("(")
            inner = self._or()
            self._consume(")")
            return inner
        return self._leaf()

    def _leaf(self) -> dict:
        ident = self._consume()
        if not _is_ident(ident):
            raise DslSyntaxError(f"Expected field name, got {ident!r}")
        op_tok = self._consume()
        op_upper = op_tok.upper()
        if op_upper == "NOT":
            self._consume("IN")
            return {"op": "not_in", "field": ident, "value": self._list_value()}
        if op_upper == "IN":
            return {"op": "in", "field": ident, "value": self._list_value()}
        if op_upper == "BETWEEN":
            lo = _scalar(self._consume())
            self._consume("AND")
            hi = _scalar(self._consume())
            return {"op": "between", "field": ident, "value": [lo, hi]}
        sym = {"=": "eq", "!=": "ne", "<": "lt", "<=": "lte", ">": "gt", ">=": "gte"}.get(op_tok)
        if sym is None:
            raise DslSyntaxError(f"Unknown operator: {op_tok!r}")
        return {"op": sym, "field": ident, "value": _scalar(self._consume())}

    def _list_value(self) -> list[Any]:
        self._consume("(")
        out: list[Any] = []
        if self._peek() != ")":
            out.append(_scalar(self._consume()))
            while self._peek() == ",":
                self._consume(",")
                out.append(_scalar(self._consume()))
        self._consume(")")
        return out


def parse_text_to_rule(text: str) -> dict:
    if not text or not text.strip():
        raise DslSyntaxError("Empty rule text")
    tokens = _tokenize(text)
    return _Parser(tokens).parse()


def _quote(value: Any) -> str:
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    s = str(value)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\-]*", s):
        return s
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def compile_rule_to_text(rule: dict) -> str:
    op = rule["op"]
    if op == "and":
        return " AND ".join(compile_rule_to_text(c) for c in rule["children"])
    if op == "or":
        return "(" + " OR ".join(compile_rule_to_text(c) for c in rule["children"]) + ")"
    if op == "not":
        return f"NOT ({compile_rule_to_text(rule['child'])})"
    field = rule["field"]
    value = rule["value"]
    if op == "in":
        return f"{field} IN (" + ", ".join(_quote(v) for v in value) + ")"
    if op == "not_in":
        return f"{field} NOT IN (" + ", ".join(_quote(v) for v in value) + ")"
    if op == "between":
        lo, hi = value
        return f"{field} BETWEEN {_quote(lo)} AND {_quote(hi)}"
    sym = {"eq": "=", "ne": "!=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}[op]
    return f"{field} {sym} {_quote(value)}"

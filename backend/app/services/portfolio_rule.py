"""Filter rule validator + SQLAlchemy compiler for portfolio views.

The canonical rule is a JSON expression tree (see spec §4.3). This module
exposes:

- ``validate_rule(rule) -> list[str]`` — non-empty list means invalid.
- ``compile_rule_to_sqla(rule) -> ColumnElement[bool]`` — SQLAlchemy clause
  for `WHERE` against the `Position` table.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, between, not_, or_

from ..models import Position, RuleCompilationError


MAX_RULE_DEPTH = 5

ALLOWED_OPS: frozenset[str] = frozenset({
    "and", "or", "not",
    "eq", "ne", "in", "not_in",
    "lt", "lte", "gt", "gte", "between",
})

_SCALAR_OPS: frozenset[str] = frozenset({"eq", "ne", "lt", "lte", "gt", "gte"})
_LIST_OPS: frozenset[str] = frozenset({"in", "not_in"})

ALLOWED_FIELDS: dict[str, type] = {
    "product_type":   str,
    "underlying":     str,
    "status":         str,
    "mapping_status": str,
    "engine_name":    str,
    "quantity":       float,
    "entry_price":    float,
    "created_at":     datetime,
}


def validate_rule(rule: Any, *, _path: str = "$", _depth: int = 0) -> list[str]:
    errors: list[str] = []
    if _depth > MAX_RULE_DEPTH:
        return [f"Rule depth exceeds {MAX_RULE_DEPTH} at {_path}"]
    if not isinstance(rule, dict):
        return [f"Rule node must be an object at {_path}"]

    op = rule.get("op")
    if op not in ALLOWED_OPS:
        return [f"Unsupported op: {op!r} at {_path}"]

    if op in ("and", "or"):
        children = rule.get("children")
        if not isinstance(children, list) or not children:
            errors.append(f"Empty or missing children for {op} at {_path}")
            return errors
        for i, child in enumerate(children):
            errors.extend(validate_rule(child, _path=f"{_path}.children[{i}]", _depth=_depth + 1))
        return errors

    if op == "not":
        child = rule.get("child")
        if not isinstance(child, dict):
            errors.append(f"`not` requires `child` object at {_path}")
            return errors
        return validate_rule(child, _path=f"{_path}.child", _depth=_depth + 1)

    field = rule.get("field")
    if field not in ALLOWED_FIELDS:
        return [f"Unknown field: {field!r} at {_path}; allowed: {sorted(ALLOWED_FIELDS)}"]
    value = rule.get("value")

    if op in _SCALAR_OPS:
        if isinstance(value, list):
            errors.append(f"`{op}` requires scalar value at {_path}")
        elif value is None:
            errors.append(f"`{op}` requires non-null value at {_path}")
    elif op in _LIST_OPS:
        if not isinstance(value, list) or not value:
            errors.append(f"`{op}` requires non-empty list value at {_path}")
    elif op == "between":
        if not (isinstance(value, list) and len(value) == 2):
            errors.append(f"`between` requires 2-element list at {_path}")
        else:
            lo, hi = value
            try:
                if lo is not None and hi is not None and lo > hi:
                    errors.append(f"`between` bounds reversed at {_path}: {lo!r} > {hi!r}")
            except TypeError:
                errors.append(f"`between` bounds incomparable at {_path}")

    return errors


def _coerce(field: str, value: Any) -> Any:
    expected = ALLOWED_FIELDS[field]
    if value is None:
        return None
    if expected is datetime and isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise RuleCompilationError(f"Cannot parse {field}={value!r} as datetime") from exc
    if expected in (float, int) and not isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise RuleCompilationError(f"Cannot coerce {field}={value!r} to number") from exc
    if expected is str and not isinstance(value, str):
        return str(value)
    return value


def compile_rule_to_sqla(rule: dict):
    op = rule["op"]
    if op == "and":
        return and_(*(compile_rule_to_sqla(c) for c in rule["children"]))
    if op == "or":
        return or_(*(compile_rule_to_sqla(c) for c in rule["children"]))
    if op == "not":
        return not_(compile_rule_to_sqla(rule["child"]))

    field = rule["field"]
    column = getattr(Position, field)
    value = rule.get("value")

    def _require_list(v: Any, op_name: str) -> list:
        if not isinstance(v, list):
            raise RuleCompilationError(
                f"op {op_name!r} requires list value at field {field!r}; "
                f"did you forget to validate_rule() first?"
            )
        return v

    if op == "eq":
        return column == _coerce(field, value)
    if op == "ne":
        return column != _coerce(field, value)
    if op == "in":
        items = _require_list(value, "in")
        return column.in_([_coerce(field, v) for v in items])
    if op == "not_in":
        items = _require_list(value, "not_in")
        return ~column.in_([_coerce(field, v) for v in items])
    if op == "lt":
        return column < _coerce(field, value)
    if op == "lte":
        return column <= _coerce(field, value)
    if op == "gt":
        return column > _coerce(field, value)
    if op == "gte":
        return column >= _coerce(field, value)
    if op == "between":
        items = _require_list(value, "between")
        if len(items) != 2:
            raise RuleCompilationError(
                f"op 'between' at field {field!r} requires exactly 2 elements"
            )
        lo, hi = items
        return between(column, _coerce(field, lo), _coerce(field, hi))

    raise RuleCompilationError(f"Unsupported op: {op}")

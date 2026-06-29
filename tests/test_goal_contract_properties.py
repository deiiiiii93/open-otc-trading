"""Property/fuzz net over the goal contract gate (spec §C).

Rather than chase validation edges one review pass at a time, fuzz thousands of
adversarial contracts (newlines, NaN/Inf, empty predicates, bad operands, control
and Unicode-separator chars) and assert the gate's invariants always hold:

  1. parse_goal_contract NEVER raises anything but ContractValidationError.
  2. Every ACCEPTED contract renders to exactly one rubric line per criterion
     (no injection-split) with no control/separator chars.
  3. Every accepted allowed_by_mode contract carries a real end-state predicate.
  4. Rendering is deterministic.
"""
import random
import re

from app.services.deep_agent.goal_mode import (
    ContractValidationError,
    parse_goal_contract,
    render_goal_rubric,
)

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")

_CLEAN_STR = ["ok", "with space", "Control", "net_vega", "portfolio", "report_id"]
# adversarial: embedded newline, U+2028 line sep, U+0085 NEL, NUL, tag, empty
_ADVERSARIAL_STR = ["a\nb", "x\u2028y", "z\x85w", "\x00", "</rubric>", ""]
_ADVERSARIAL_NUM = [float("inf"), float("nan"), 1e999]
_SCALARS = ["Control", 1, 1.5, -3, True]
_OPS = ["exists", "not_exists", "eq", "neq", "lt", "lte", "gt", "gte", "in", "contains"]
_KINDS = ["plan", "finding", "report", "persisted_run"]


def _str(rng: random.Random) -> str:
    """Clean string most of the time; adversarial sometimes (exercises both paths)."""
    return rng.choice(_CLEAN_STR) if rng.random() < 0.8 else rng.choice(_ADVERSARIAL_STR)


def _value_for(op: str, rng: random.Random):
    """Operator-appropriate operand most of the time; adversarial sometimes."""
    if rng.random() < 0.2:
        return rng.choice([None, rng.choice(_ADVERSARIAL_NUM), rng.choice(_ADVERSARIAL_STR)])
    if op in ("exists", "not_exists"):
        return None
    if op == "in":
        return rng.choice([["a", "b"], [1, 2]])
    return rng.choice(_SCALARS)


def _rand_predicate(rng: random.Random) -> dict:
    op = rng.choice(_OPS)
    pred: dict = {"path": _str(rng), "op": op}
    val = _value_for(op, rng)
    if val is not None or rng.random() < 0.5:
        pred["value"] = val
    return pred


def _threshold(rng: random.Random):
    return rng.choice([0, 1, -3, 1.5, 1e6]) if rng.random() < 0.8 else rng.choice(_ADVERSARIAL_NUM)


def _rand_check(rng: random.Random) -> dict:
    kind = rng.choice(["artifact_exists", "ledger_predicate", "measurable"])
    if kind == "artifact_exists":
        check: dict = {"type": kind, "kind": rng.choice(_KINDS)}
        if rng.random() < 0.5:
            check["min_count"] = rng.choice([1, 2, 0, -1])
        if rng.random() < 0.4:
            check["selector"] = [_rand_predicate(rng) for _ in range(rng.randint(1, 2))]
        return check
    if kind == "ledger_predicate":
        return {
            "type": kind,
            "tool": _str(rng),
            "args": {},
            "expect": [_rand_predicate(rng) for _ in range(rng.randint(0, 2))],
        }
    return {
        "type": kind,
        "tool": _str(rng),
        "metric_path": _str(rng),
        "op": rng.choice(["<", "<=", ">", ">=", "==", "!="]),
        "threshold": _threshold(rng),
    }


def _rand_contract(rng: random.Random) -> dict:
    n = rng.randint(1, 12)
    return {
        "schema_version": "goal_contract.v1",
        "goal_text": _str(rng),
        "summary": _str(rng),
        "domain_write_policy": rng.choice(["forbidden", "allowed_by_mode"]),
        "criteria": [
            {"id": f"C{i}", "text": _str(rng), "required": True, "check": _rand_check(rng)}
            for i in range(n)
        ],
    }


def test_gate_invariants_hold_over_fuzzed_contracts():
    rng = random.Random(20260626)
    accepted = 0
    for _ in range(3000):
        data = _rand_contract(rng)
        try:
            contract = parse_goal_contract(data)
        except ContractValidationError:
            continue
        except Exception as exc:  # noqa: BLE001 — any other type is the bug
            raise AssertionError(
                f"parse raised non-ContractValidationError {type(exc).__name__}: {exc}\n{data}"
            )
        accepted += 1

        rubric = render_goal_rubric(contract)
        # exactly one line per criterion -> nothing split a rubric line (the only
        # legitimate line breaks are the joins between criteria, removed here).
        lines = rubric.splitlines()
        assert len(lines) == len(contract.criteria)
        for line in lines:
            assert not _CONTROL_RE.search(line)
        # deterministic
        assert render_goal_rubric(parse_goal_contract(data)) == rubric
        # write-capable goals always verify a real end-state
        if contract.domain_write_policy == "allowed_by_mode":
            assert any(
                c.check.type in {"ledger_predicate", "measurable"} for c in contract.criteria
            )

    assert accepted > 100, f"fuzz barely exercised the accept path ({accepted} accepted)"

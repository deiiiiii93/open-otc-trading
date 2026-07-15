from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any

# name[key=value] path segment, e.g. landscape[spot_shift=0.1]
_SEL = re.compile(r"^(.+?)\[([^=\]]+)=([^\]]+)\]$")
# numeric token: optional sign, digits with thousands commas, optional decimals,
# optional magnitude suffix (k/m/mm/bn/b), optional percent
_NUM_TOKEN = re.compile(r"[+-]?\d[\d,]*(?:\.\d+)?\s*(k|m|mm|bn|b)?(%)?", re.I)
_SUFFIX = {"k": 1e3, "m": 1e6, "mm": 1e6, "bn": 1e9, "b": 1e9}
_NEAR_WINDOW = 160  # chars after an anchor start a token may occur in


@dataclass
class AssertionContext:
    response_text: str
    tool_calls: list[dict]
    tool_results: list[dict]
    skills_routed: list[str]
    artifacts: list[dict]
    task_ids: list[str]


# Capture-sink bounds applied at the SCORING read path — the tool's own output
# bounding does not protect scoring, because answer_fields consumes the raw persisted
# tool *inputs* (ctx.tool_calls[].args), not the tool's return value. Bounding here
# caps what any consumer scores/retains regardless of what a model recorded (Codex
# code-review [high]). Real answers are 1-2 short scalars, so this never clips a
# compliant answer; it only defuses oversized/spam payloads.
_ANSWER_MAX_FIELDS = 32
_ANSWER_MAX_STR = 256
_ANSWER_MAX_KEY = 128


def _bound_answer_value(v: Any) -> Any:
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    s = str(v)
    return s if len(s) <= _ANSWER_MAX_STR else s[:_ANSWER_MAX_STR] + "…"


def answer_fields(ctx: "AssertionContext") -> dict[str, Any]:
    """Merged answer of every record_answer call in this context (last-wins per key).

    Tolerates both call shapes the tool accepts: nested args={"answer": {...}} and
    flat args={"hotspot": ..., "delta": ...}. For each call, the nested `answer`
    dict (if any) is merged first, then the remaining top-level arg keys — so a
    model that flattens is still captured. The result is bounded (field count, key
    and value length) so oversized/spam recorder inputs cannot be scored/retained
    in full.
    """
    from app.golden_workflows.schema import normalize_tool_name
    merged: dict[str, Any] = {}
    for c in ctx.tool_calls:
        if normalize_tool_name(c.get("name", "")) != "record_answer":
            continue
        args = c.get("args") or {}
        nested = args.get("answer")
        if isinstance(nested, dict):
            merged.update(nested)
        merged.update({k: v for k, v in args.items() if k != "answer"})
    bounded: dict[str, Any] = {}
    for k, v in list(merged.items())[:_ANSWER_MAX_FIELDS]:
        key = str(k)[:_ANSWER_MAX_KEY]
        bounded[key] = _bound_answer_value(v)
    return bounded


def _no_answer_detail(fields: dict, field: str) -> str:
    if not fields:
        return f"no answer recorded for {field}"
    shown = ", ".join(f"{k}={v!r}" for k, v in list(fields.items())[:6])
    return f"key {field} absent; answered: {shown}"


def _coerce_num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("$", "").strip().rstrip("%"))
    except (ValueError, AttributeError):
        return None


def _exact(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if type(a) is not type(b):
        return False
    return a == b

def _deep_subset(expected: Any, actual: Any, path: str) -> tuple[bool, str]:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False, f"{path}: expected object"
        for k, v in expected.items():
            if k not in actual:
                return False, f"{path}.{k}: missing"
            ok, msg = _deep_subset(v, actual[k], f"{path}.{k}")
            if not ok: return False, msg
        return True, ""
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return False, f"{path}: list length mismatch"
        for i, (e, a) in enumerate(zip(expected, actual)):
            ok, msg = _deep_subset(e, a, f"{path}.{i}")
            if not ok: return False, msg
        return True, ""
    return (True, "") if _exact(expected, actual) else (False, f"{path}: {actual!r} != {expected!r}")

def match_tool(exp, calls: list[dict]) -> tuple[bool, str]:
    from app.golden_workflows.schema import normalize_tool_name
    want = normalize_tool_name(exp.name)
    for c in calls:
        if normalize_tool_name(c.get("name", "")) != want:   # normalize observed too
            continue
        if exp.args is None:
            return True, ""
        ok, _ = _deep_subset(exp.args, c.get("args", {}), exp.name)
        if ok: return True, ""
    return False, f"tool {exp.name} not matched"

def match_tools_subsequence(exps, calls) -> tuple[bool, str]:
    from app.golden_workflows.schema import normalize_tool_name
    remaining = list(calls)
    for exp in exps:
        want = normalize_tool_name(exp.name)
        for i, c in enumerate(remaining):
            if normalize_tool_name(c.get("name", "")) == want and match_tool(exp, [c])[0]:
                remaining = remaining[i + 1:]
                break
        else:
            return False, f"tool {exp.name} not found in order"
    return True, ""

def _parse_scalar(s: str) -> Any:
    t = s.strip()
    for cast in (int, float):
        try:
            return cast(t)
        except ValueError:
            pass
    return t.strip("'\"")


def _values_equal(a: Any, b: Any) -> bool:
    def num(x: Any) -> bool:
        return isinstance(x, (int, float)) and not isinstance(x, bool)
    if num(a) and num(b):
        return abs(float(a) - float(b)) < 1e-9
    return a == b


def _split_path(path: str) -> list[str]:
    """Split a dotted path, keeping dots inside [key=value] selectors intact
    (e.g. "landscape[spot_shift=0.1].gamma" → ["landscape[spot_shift=0.1]", "gamma"])."""
    segs: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in path:
        if ch == "." and depth == 0:
            segs.append("".join(buf))
            buf = []
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        buf.append(ch)
    segs.append("".join(buf))
    return segs


def _dig(obj: Any, path: str) -> tuple[bool, Any]:
    cur = obj
    for seg in _split_path(path):
        sel = _SEL.match(seg)
        if sel:
            name, key, raw = sel.group(1), sel.group(2), _parse_scalar(sel.group(3))
            if not (isinstance(cur, dict) and name in cur):
                return False, None
            cur = cur[name]
            if not isinstance(cur, list):
                return False, None
            for el in cur:
                if isinstance(el, dict) and key in el and _values_equal(el[key], raw):
                    cur = el
                    break
            else:
                return False, None
            continue
        if isinstance(cur, list):
            try: cur = cur[int(seg)]
            except (ValueError, IndexError): return False, None
        elif isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        else:
            return False, None
    return True, cur


def _scan_numeric_tokens(text: str) -> list[tuple[int, float]]:
    """(start_offset, value) per numeric token; % tokens also yield value/100."""
    out: list[tuple[int, float]] = []
    for m in _NUM_TOKEN.finditer(text):
        body = m.group(0)
        suffix = (m.group(1) or "").lower()
        pct = m.group(2)
        num = body
        if pct:
            num = num.rstrip("%")
        if suffix:
            num = num[: len(num) - len(suffix)]
        try:
            val = float(num.replace(",", "").strip())
        except ValueError:
            continue
        if suffix:
            val *= _SUFFIX[suffix]
        out.append((m.start(), val))
        if pct:
            out.append((m.start(), val / 100.0))
    return out


def _near_spans(text: str, near: list[str]) -> list[tuple[int, int]]:
    """[anchor_start, anchor_start+window] span per occurrence of each anchor."""
    low = text.lower()
    spans: list[tuple[int, int]] = []
    for anchor in near:
        needle = anchor.lower()
        start = 0
        while (i := low.find(needle, start)) != -1:
            spans.append((i, i + _NEAR_WINDOW))
            start = i + 1
    return spans


def _quote_value_report(text: str, target: float, *, rel_tol: float,
                        mode: str, near: list[str] | None) -> tuple[bool, list[str]]:
    """(matched, quoted) — whether the response quotes *target* within tolerance,
    plus the RAW numeric tokens actually present in the scored region (near-
    anchored when *near* is set), deduped in document order. `quoted` lets a
    failed grounding check show what the response said instead of the expected
    value — the numbers the scorer weighed, not a guess."""
    valued = _scan_numeric_tokens(text)
    raw = [(m.start(), m.group(0).strip()) for m in _NUM_TOKEN.finditer(text)]
    if near:
        spans = _near_spans(text, near)
        valued = [t for t in valued if any(a <= t[0] <= b for a, b in spans)]
        raw = [t for t in raw if any(a <= t[0] <= b for a, b in spans)]
    tol = rel_tol * abs(target) if target != 0 else rel_tol
    matched = False
    for _, v in valued:
        a, b = (v, target) if mode == "signed" else (abs(v), abs(target))
        if abs(a - b) <= tol:
            matched = True
            break
    seen: set[str] = set()
    quoted: list[str] = []
    for _, s in raw:
        if s and s not in seen:
            seen.add(s)
            quoted.append(s)
    return matched, quoted


def _quoted_clause(quoted: list[str], near: list[str] | None) -> str:
    """Human clause naming what the response actually quoted in the scored region,
    for a failed quote-value check. Capped so a number-dense report stays legible."""
    loc = f" near {near}" if near else ""
    if not quoted:
        return f"response has no number{loc}"
    shown = ", ".join(quoted[:6])
    more = "" if len(quoted) <= 6 else f", +{len(quoted) - 6} more"
    return f"response quoted{loc}: {shown}{more}"

def _last_result(ctx: AssertionContext, tool: str) -> dict | None:
    from app.golden_workflows.schema import normalize_tool_name
    want = normalize_tool_name(tool)
    matches = [r for r in ctx.tool_results
               if normalize_tool_name(r.get("name", "")) == want and not r.get("error")]
    return matches[-1] if matches else None


def _last_call(ctx: AssertionContext, tool: str) -> dict | None:
    """Args of the last matching tool CALL (the authoritative inputs the model
    passed — e.g. the terms actually sent to book_position)."""
    from app.golden_workflows.schema import normalize_tool_name
    want = normalize_tool_name(tool)
    matches = [c.get("args", {}) or {} for c in ctx.tool_calls
               if normalize_tool_name(c.get("name", "")) == want]
    return matches[-1] if matches else None

def evaluate_assertion(a, ctx: AssertionContext) -> tuple[bool, str]:
    t = a.type
    if t == "skill_routed":
        return (a.name.strip().lower() in [s.strip().lower() for s in ctx.skills_routed],
                f"skill {a.name} not routed")
    if t == "skills_routed_sequence":
        want = [n.strip().lower() for n in a.names]
        have = [s.strip().lower() for s in ctx.skills_routed]
        it = iter(have)
        ok = all(any(x == w for x in it) for w in want)
        return ok, f"skill sequence {want} not a subsequence of {have}"
    if t == "tools_routed_sequence":
        from app.golden_workflows.schema import ToolExpectation
        exps = [ToolExpectation(name=n) for n in a.names]
        return match_tools_subsequence(exps, ctx.tool_calls)
    if t == "tool_called":
        from app.golden_workflows.schema import normalize_tool_name
        want = normalize_tool_name(a.name)
        candidates = a.args_any_of if getattr(a, "args_any_of", None) else [a.args]
        exclusive = getattr(a, "exclusive_keys", None) or []

        def _absent(v: Any) -> bool:
            return v is None or v == [] or v == ""

        def _call_matches(call_args: dict) -> bool:
            for cand in candidates:
                if cand is not None:
                    ok, _ = _deep_subset(cand, call_args, a.name)
                    if not ok:
                        continue
                cand_keys = set((cand or {}).keys())
                if any(k not in cand_keys and not _absent(call_args.get(k))
                       for k in exclusive):
                    continue
                return True
            return False

        matching_name = [c.get("args", {}) or {} for c in ctx.tool_calls
                         if normalize_tool_name(c.get("name", "")) == want]
        max_calls = getattr(a, "max_calls", None)
        if max_calls is not None and len(matching_name) > max_calls:
            return False, (f"{a.name} called {len(matching_name)}x "
                           f"(max {max_calls}) — duplicate dispatch is over-execution")
        if getattr(a, "all_calls", False):
            # Exact-use: at least one call AND every call matches a candidate —
            # a compliant first call must not mask a later over-execution.
            if not matching_name:
                return False, f"tool {a.name} not called"
            if all(_call_matches(args) for args in matching_name):
                return True, ""
            return False, f"a call of {a.name} did not match the allowed args"
        if any(_call_matches(args) for args in matching_name):
            return True, ""
        return False, f"tool {a.name} not matched"
    if t == "task_returned_id":
        r = _last_result(ctx, a.tool)
        tid = (r or {}).get("content", {}).get("task_id") if r else None
        return (bool(tid), f"{a.tool} returned no task_id")
    if t == "artifact_exists":
        return (any(x.get("kind") == a.kind for x in ctx.artifacts), f"no artifact kind={a.kind}")
    if t == "response_contains":
        low = ctx.response_text.lower()
        return (any(s.lower() in low for s in a.any_of), f"response missing any_of={a.any_of}")
    if t == "tool_result_path":
        r = _last_result(ctx, a.tool)
        if not r: return False, f"no result for {a.tool}"
        found, val = _dig(r.get("content", {}), a.path)
        if not found: return False, f"path {a.path} missing"
        if a.is_not_null is not None: return (val is not None, f"{a.path} is null")
        if a.equals is not None: return (_exact(a.equals, val), f"{a.path}={val!r} != {a.equals!r}")
        if a.gte is not None:
            return (isinstance(val, (int, float)) and not isinstance(val, bool) and val >= a.gte, f"{a.path} !>= {a.gte}")
        if a.lte is not None:
            return (isinstance(val, (int, float)) and not isinstance(val, bool) and val <= a.lte, f"{a.path} !<= {a.lte}")
    if t == "tool_result_ratio":
        src = _last_call(ctx, a.tool) if a.source == "call" else (
            (_last_result(ctx, a.tool) or {}).get("content"))
        if src is None:
            return False, f"no {a.source} for {a.tool}"

        def _num_at(path: str) -> float | None:
            found, v = _dig(src, path)
            if not found or isinstance(v, bool) or not isinstance(v, (int, float)):
                return None
            return float(v)

        n = _num_at(a.numer)
        d = _num_at(a.denom)
        m = 1.0 if a.denom_mult is None else _num_at(a.denom_mult)
        if n is None or d is None or m is None:
            return False, (f"{a.tool} ratio path missing/non-numeric "
                           f"(numer={a.numer}, denom={a.denom}, denom_mult={a.denom_mult})")
        denom = d * m
        if denom == 0:
            return False, f"{a.tool} ratio denominator is zero"
        value = n / denom
        tol = a.rel_tol * abs(a.equals) if a.equals != 0 else a.rel_tol
        ok = abs(value - a.equals) <= tol
        return ok, "" if ok else (
            f"{a.tool} {a.numer}/({a.denom}×{a.denom_mult}) = {value:.6g} "
            f"!= {a.equals} (rel_tol={a.rel_tol})")
    if t == "assertion_any_of":
        reasons = []
        for member in a.any_of:
            ok, msg = evaluate_assertion(member, ctx)
            if ok:
                return True, ""
            reasons.append(msg)
        return False, "no member passed: " + " | ".join(r for r in reasons if r)
    if t == "tool_not_called":
        from app.golden_workflows.schema import normalize_tool_name
        want = normalize_tool_name(a.name)
        called = any(normalize_tool_name(c.get("name", "")) == want for c in ctx.tool_calls)
        return (not called, f"tool {a.name} was called but must not be")
    if t == "artifact_contains":
        bodies = [str(x.get("content") or x.get("text") or "")
                  for x in ctx.artifacts if x.get("kind") == a.kind]
        blob = "\n".join(bodies).lower()
        if any(s.lower() in blob for s in a.any_of):
            return True, ""
        return False, f"no {a.kind} artifact contains any_of={a.any_of}"
    if t == "response_quotes_tool_value":
        r = _last_result(ctx, a.tool)
        if not r:
            return False, f"no result for {a.tool}"
        found, val = _dig(r.get("content", {}), a.path)
        if not found:
            return False, f"path {a.path} missing"
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            return False, f"{a.path} is not numeric: {val!r}"
        ok, quoted = _quote_value_report(ctx.response_text, float(val),
                                         rel_tol=a.rel_tol, mode=a.match, near=a.near)
        return ok, "" if ok else (
            f"response does not quote {a.path}={val} "
            f"(match={a.match}, rel_tol={a.rel_tol}, near={a.near}) — "
            f"{_quoted_clause(quoted, a.near)}")
    if t == "response_quotes_value":
        ok, quoted = _quote_value_report(ctx.response_text, float(a.value),
                                         rel_tol=a.rel_tol, mode=a.match, near=a.near)
        return ok, "" if ok else (
            f"response does not quote value {a.value} "
            f"(match={a.match}, rel_tol={a.rel_tol}, near={a.near}) — "
            f"{_quoted_clause(quoted, a.near)}")
    # DELIBERATE (spec 2026-07-07, user-affirmed): the record_answer payload is the
    # AUTHORITATIVE answer for grounding/adherence — these branches score ctx.tool_calls
    # (via answer_fields), NOT ctx.response_text. The visible prose is scored separately
    # by the synthesis axis. A model that records the right answer but writes contradictory
    # prose still passes here; that is an accepted trade-off for a capability benchmark
    # and keeps the retired fuzzy near-anchor response scan from re-entering as a guard.
    if t == "answer_field_equals":
        fields = answer_fields(ctx)
        if a.field not in fields:
            return False, _no_answer_detail(fields, a.field)
        got = fields[a.field]
        wants = a.any_of if a.any_of else [a.equals]
        norm = lambda s: str(s).strip().lower()
        ok = norm(got) in [norm(w) for w in wants]
        return ok, "" if ok else f"{a.field}={got!r} != {a.equals or a.any_of}"
    if t == "answer_field_quotes":
        fields = answer_fields(ctx)
        if a.field not in fields:
            return False, _no_answer_detail(fields, a.field)
        got = _coerce_num(fields[a.field])
        if got is None:
            return False, f"{a.field}={fields[a.field]!r} is not numeric"
        target = float(a.value)
        gv, tv = (got, target) if a.match == "signed" else (abs(got), abs(target))
        tol = a.rel_tol * abs(target) if target != 0 else a.rel_tol
        ok = abs(gv - tv) <= tol
        return ok, "" if ok else (
            f"{a.field}={got} != {a.value} (rel_tol={a.rel_tol}, match={a.match})")
    return False, f"unknown assertion {t}"

def resolve_seed_refs(obj: Any, seed_map: dict[str, Any]) -> Any:
    if isinstance(obj, str) and obj.startswith("$seed."):
        if obj not in seed_map:
            from app.golden_workflows.schema import UnresolvedSeedRefError
            raise UnresolvedSeedRefError(obj)
        return seed_map[obj]
    if isinstance(obj, dict):
        return {k: resolve_seed_refs(v, seed_map) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_seed_refs(v, seed_map) for v in obj]
    return obj

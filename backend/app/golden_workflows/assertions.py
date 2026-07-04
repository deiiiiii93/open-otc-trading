from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass
class AssertionContext:
    response_text: str
    tool_calls: list[dict]
    tool_results: list[dict]
    skills_routed: list[str]
    artifacts: list[dict]
    task_ids: list[str]

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

def _dig(obj: Any, path: str) -> tuple[bool, Any]:
    cur = obj
    for seg in path.split("."):
        if isinstance(cur, list):
            try: cur = cur[int(seg)]
            except (ValueError, IndexError): return False, None
        elif isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        else:
            return False, None
    return True, cur

def _last_result(ctx: AssertionContext, tool: str) -> dict | None:
    from app.golden_workflows.schema import normalize_tool_name
    want = normalize_tool_name(tool)
    matches = [r for r in ctx.tool_results
               if normalize_tool_name(r.get("name", "")) == want and not r.get("error")]
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
        from app.golden_workflows.schema import ToolExpectation
        return match_tool(ToolExpectation(name=a.name, args=a.args), ctx.tool_calls)
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
    if t == "tool_not_called":
        from app.golden_workflows.schema import normalize_tool_name
        want = normalize_tool_name(a.name)
        called = any(normalize_tool_name(c.get("name", "")) == want for c in ctx.tool_calls)
        return (not called, f"tool {a.name} was called but must not be")
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

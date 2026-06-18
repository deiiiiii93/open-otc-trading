"""Scenario authoring: thin wrappers over QuantArk's stresstest scenario layer."""
from __future__ import annotations

import itertools
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services import quantark

_PARAM_TO_METHOD = {
    "spot": "spot_stress",
    "vol": "vol_stress",
    "rate": "rate_stress",
    "dividend": "div_yield_stress",
}

# Inverse of the param→builder-method routing, for reading scenarios back out.
_PARAM_FROM_QUANTARK = {
    "spot": "spot",
    "volatility": "vol",
    "rate": "rate",
    "dividend_yield": "dividend",
}

_PREDEFINED = {
    "market_crash": "market_crash",
    "market_rally": "market_rally",
    "vol_spike": "vol_spike",
    "vol_crush": "vol_crush",
    "rate_hike": "rate_hike",
    "rate_cut": "rate_cut",
    "severe_downturn": "severe_downturn",
    "inflation_shock": "inflation_shock",
    "black_monday_1987": "black_monday_1987",
    "financial_crisis_2008": "financial_crisis_2008",
    "covid_crash_2020": "covid_crash_2020",
}


def expand_axis(start: float, stop: float, step: float) -> list[float]:
    """Inclusive value ladder from start to stop by step, robust to float drift.

    Endpoint is included when it lands on a step boundary; an off-grid stop
    truncates to the last boundary on the start side of stop. Rounds each value
    to 10 dp so 0.05 ladders don't accumulate 1e-16 noise.
    """
    start, stop, step = float(start), float(stop), float(step)
    if not all(math.isfinite(v) for v in (start, stop, step)):
        raise ValueError("axis start/stop/step must be finite numbers")
    if start == stop:
        return [round(start, 10)]
    if step == 0:
        raise ValueError("axis step must be non-zero when start != stop")
    span = stop - start
    if (span > 0) != (step > 0):
        raise ValueError("axis step sign must move start toward stop")
    n = int(math.floor(span / step + 1e-9))  # number of intervals; +eps keeps on-grid endpoints
    return [round(start + i * step, 10) for i in range(n + 1)]


def _grid_cell_name(cell: list[tuple[str, float, str]]) -> str:
    """Readable, unique name for one grid cell: [(param, value, stress_type), ...].

    PERCENTAGE values render as signed %, others as signed numbers, e.g.
    "spot-10% / vol+20%". Uniqueness holds because each cell is a distinct
    value-combination over a fixed axis order.
    """
    parts = []
    for param, value, stype in cell:
        if stype == "PERCENTAGE":
            parts.append(f"{param}{value * 100:+g}%")
        else:
            parts.append(f"{param}{value:+g}")
    return " / ".join(parts)


def generate_grid(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand a grid spec into a list of scenario spec dicts (cross product).

    spec = {name, combine_mode='cross_product',
            axes: [{param, start, stop, step, stress_type?, level?, target?}]}.
    Each generated scenario carries one stress per axis (the cell's value).
    Raises ValueError (-> REST 400) on bad axes, duplicate param, or cap breach.
    Underlying-level target validity is enforced later by build_custom at save.
    """
    name = str(spec.get("name", "")).strip()
    if not name:
        raise ValueError("grid set requires a name")
    combine_mode = str(spec.get("combine_mode", "cross_product"))
    if combine_mode != "cross_product":
        raise ValueError(
            f"Unsupported combine_mode {combine_mode!r}; v1 supports 'cross_product'"
        )
    axes = spec.get("axes") or []
    if not axes:
        raise ValueError("grid set requires at least one axis")

    expanded: list[tuple[dict, list[float]]] = []
    seen: set[str] = set()
    for ax in axes:
        param = str(ax.get("param", "")).lower()
        if param not in _PARAM_TO_METHOD:
            raise ValueError(
                f"Unsupported grid param {param!r}; supports {sorted(_PARAM_TO_METHOD)}"
            )
        if param in seen:
            raise ValueError(f"Duplicate grid axis for param {param!r}")
        seen.add(param)
        expanded.append((ax, expand_axis(ax["start"], ax["stop"], ax["step"])))

    total = math.prod(len(values) for _, values in expanded)
    cap = get_settings().scenario_grid_max_cells
    if total > cap:
        raise ValueError(
            f"grid would generate {total} scenarios, exceeding the cap of {cap}"
        )

    out: list[dict[str, Any]] = []
    for combo in itertools.product(*[values for _, values in expanded]):
        stresses: list[dict[str, Any]] = []
        cell: list[tuple[str, float, str]] = []
        for (ax, _values), value in zip(expanded, combo):
            stype = str(ax.get("stress_type", "PERCENTAGE")).upper()
            level = str(ax.get("level", "portfolio")).lower()
            stresses.append({
                "param": str(ax["param"]).lower(),
                "stress_type": stype,
                "value": value,
                "level": level,
                "target": ax.get("target"),
            })
            cell.append((str(ax["param"]).lower(), value, stype))
        out.append({"name": _grid_cell_name(cell), "description": "", "stresses": stresses})
    return out


def _imports():
    quantark.ensure_quantark_path()
    from quantark.stresstest.scenario.scenario_builder import ScenarioBuilder
    from quantark.stresstest.scenario.scenario_library import ScenarioLibrary
    from quantark.stresstest.stress.stress_types import StressType
    return ScenarioBuilder, ScenarioLibrary, StressType


def list_predefined() -> list[dict]:
    """Expose the curated predefined set with both a stable `key` and human `name`."""
    _, ScenarioLibrary, _ = _imports()
    out: list[dict] = []
    for key, method in _PREDEFINED.items():
        scenario = getattr(ScenarioLibrary, method)()
        out.append({
            "key": key,
            "name": scenario.name,
            "description": getattr(scenario, "description", ""),
            "num_stresses": len(scenario.stresses),
            "metadata": getattr(scenario, "metadata", {}),
            "stresses": serialize_scenario(scenario)["stresses"],
        })
    return out


def build_custom(spec: dict[str, Any]) -> Any:
    """Build a Scenario from a validated spec. Raises ValueError on bad input."""
    ScenarioBuilder, _, StressType = _imports()
    builder = ScenarioBuilder().name(spec["name"])
    if spec.get("description"):
        builder = builder.description(spec["description"])
    stresses = spec.get("stresses") or []
    if not stresses:
        raise ValueError("A custom scenario needs at least one stress")
    for st in stresses:
        param = str(st.get("param", "")).lower()
        if param not in _PARAM_TO_METHOD:
            raise ValueError(
                f"Unsupported stress param {param!r}; v1 supports {sorted(_PARAM_TO_METHOD)}"
            )
        stress_type_name = str(st.get("stress_type", "PERCENTAGE")).upper()
        try:
            stress_type = StressType[stress_type_name]
        except KeyError as exc:
            # Raise ValueError (not KeyError) so the REST layer maps it to 400 and
            # bad input is rejected at submit, not deep in the async worker.
            raise ValueError(
                f"Unsupported stress_type {stress_type_name!r}; use ABSOLUTE, "
                "PERCENTAGE, or VALUE"
            ) from exc
        value = float(st["value"])
        level = str(st.get("level", "portfolio")).lower()
        method = getattr(builder, _PARAM_TO_METHOD[param])
        kwargs: dict[str, Any] = {"stress_type": stress_type}
        if level == "portfolio":
            pass
        elif level == "underlying":
            target = st.get("target")
            if not target:
                # Without a target this would silently become a portfolio-wide
                # stress — reject so the requested scope is never broadened.
                raise ValueError(
                    "underlying-level stress requires a non-empty 'target' "
                    "(underlying symbol)"
                )
            kwargs["underlying"] = str(target)
        elif level == "position":
            # The bridge builds EquityPosition objects keyed by QuantArk-generated
            # UUID position_ids, NOT the app's DB position ids, so a DB id forwarded
            # here would fail at execution ("Stress target position ... not found").
            # Reject loudly in v1; portfolio/underlying levels are supported.
            raise ValueError(
                "position-level stress targeting is not supported in v1; use level "
                "'portfolio', or 'underlying' with target = underlying symbol"
            )
        else:
            raise ValueError(
                f"Unsupported stress level {level!r}; use 'portfolio' or 'underlying'"
            )
        builder = method(value, **kwargs)
    return builder.build()


def serialize_scenario(scenario: Any) -> dict[str, Any]:
    """Project a QuantArk Scenario to the spec shape used by the UI + build_custom.

    QuantArk param names (volatility, dividend_yield) are mapped back to the spec
    vocabulary (vol, dividend); stress_type/level are emitted as their enum
    `.name`/`.value` so they round-trip through build_custom (which upper/lowers).
    """
    stresses: list[dict[str, Any]] = []
    for s in scenario.stresses:
        stress_type = getattr(s.stress_type, "name", str(s.stress_type))
        level = getattr(s.level, "value", str(s.level))
        stresses.append({
            "param": _PARAM_FROM_QUANTARK.get(s.parameter, s.parameter),
            "stress_type": stress_type,
            "value": float(s.stress_value),
            "level": level,
            "target": s.target,
        })
    return {
        "name": scenario.name,
        "description": getattr(scenario, "description", "") or "",
        "stresses": stresses,
    }


def resolve_scenarios(request: dict[str, Any]) -> list[Any]:
    """Unify {predefined names, custom specs, saved set name} -> list[Scenario]."""
    _, ScenarioLibrary, _ = _imports()
    out: list[Any] = []
    for key in request.get("predefined", []) or []:
        method = _PREDEFINED.get(str(key).lower())
        if method is None:
            raise ValueError(f"Unknown predefined scenario {key!r}")
        out.append(getattr(ScenarioLibrary, method)())
    for spec in request.get("custom", []) or []:
        out.append(build_custom(spec))
    set_name = request.get("scenario_set")
    if set_name:
        # load_set defined below; resolves a saved YAML set by name
        out.extend(load_set(set_name))
    if not out:
        raise ValueError("No scenarios resolved from request")
    return out


def _sets_dir() -> Path:
    path = Path(get_settings().scenario_sets_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name.strip())
    if not safe:
        raise ValueError("Scenario set name is empty after sanitization")
    return safe


def _sidecar_path(name: str) -> Path:
    return _sets_dir() / f"{_safe_name(name)}.set.json"


def save_set(name: str, scenarios: list[Any], grid_spec: dict[str, Any] | None = None) -> str:
    quantark.ensure_quantark_path()
    from quantark.stresstest.scenario.scenario_storage import ScenarioStorage
    target = _sets_dir() / f"{_safe_name(name)}.yaml"
    ScenarioStorage.save_scenarios(scenarios, str(target))
    sidecar = _sidecar_path(name)
    if grid_spec is not None:
        meta = {
            "kind": "grid",
            "combine_mode": grid_spec.get("combine_mode", "cross_product"),
            "axes": grid_spec.get("axes", []),
            "count": len(scenarios),
            "created_at": datetime.utcnow().isoformat(),
        }
        sidecar.write_text(json.dumps(meta, indent=2))
    elif sidecar.exists():
        # Overwriting a former grid set with a plain save: drop the stale sidecar
        # so classification stays truthful.
        sidecar.unlink()
    return str(target)


def read_set_meta(name: str) -> dict[str, Any] | None:
    """Load the grid sidecar for a set, or None if it has none / is unreadable."""
    path = _sidecar_path(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def load_set(name: str) -> list[Any]:
    quantark.ensure_quantark_path()
    from quantark.stresstest.scenario.scenario_storage import ScenarioStorage
    target = _sets_dir() / f"{_safe_name(name)}.yaml"
    if not target.exists():
        raise ValueError(f"Scenario set not found: {name}")
    return ScenarioStorage.load_scenarios(str(target))


def list_sets() -> list[str]:
    return sorted(p.stem for p in _sets_dir().glob("*.yaml"))


def get_set(name: str) -> dict[str, Any]:
    """Return a saved custom scenario's contents (the first/only scenario in the set)."""
    scenarios = load_set(name)  # raises ValueError if the file is missing
    if not scenarios:
        raise ValueError(f"Scenario set is empty: {name}")
    data = serialize_scenario(scenarios[0])
    data["name"] = _safe_name(name)  # the file/item name is the canonical identifier
    # Flat-model items hold one scenario; surface the true count so the UI can
    # refuse to edit (and thereby overwrite/truncate) multi-scenario sets.
    data["num_scenarios"] = len(scenarios)
    return data


def list_set_specs(name: str) -> list[dict[str, Any]]:
    """All scenarios in a saved set, each serialized to a spec dict (for running)."""
    return [serialize_scenario(s) for s in load_set(name)]  # load_set raises ValueError if missing


def list_sets_detailed() -> list[dict[str, Any]]:
    """Single-scenario saved sets, serialized, for the flat-model UI list.

    Multi-scenario sets (creatable via POST /sets or the save_scenario_set tool)
    are intentionally EXCLUDED: the flat-model UI represents one scenario per
    item, so showing a multi-scenario set as a single detailed scenario would let
    users review one stress config while the run executes others. Such sets stay
    agent/API-managed (and still run in full via list_set_specs when referenced
    by name). Unreadable sets are skipped (they can't be rendered or edited).
    """
    out: list[dict[str, Any]] = []
    for stem in list_sets():
        if read_set_meta(stem) is not None:
            continue  # a generated Set, not a single custom scenario
        try:
            detail = get_set(stem)
        except Exception:
            continue
        if detail.get("num_scenarios", 1) == 1:
            out.append(detail)
    return out


def _axes_summary(meta: dict[str, Any] | None) -> str:
    axes = (meta or {}).get("axes", []) or []
    return " × ".join(str(a.get("param", "?")) for a in axes)


def list_sets_full() -> list[dict[str, Any]]:
    """All multi-scenario Sets: those with a grid sidecar OR >=2 scenarios.

    Single custom scenarios (1 scenario, no sidecar) are excluded — they are
    surfaced by list_sets_detailed instead.
    """
    out: list[dict[str, Any]] = []
    for stem in list_sets():
        meta = read_set_meta(stem)
        try:
            specs = list_set_specs(stem)
        except Exception:
            continue
        n = len(specs)
        if meta is None and n < 2:
            continue
        out.append({
            "name": stem,
            "num_scenarios": n,
            "combine_mode": (meta or {}).get("combine_mode"),
            "axes_summary": _axes_summary(meta),
            "has_grid": meta is not None,
            "axes": (meta or {}).get("axes", []) or [],
        })
    return out


def delete_set(name: str) -> None:
    target = _sets_dir() / f"{_safe_name(name)}.yaml"
    if not target.exists():
        raise ValueError(f"Scenario set not found: {name}")
    target.unlink()
    sidecar = _sidecar_path(name)
    if sidecar.exists():
        sidecar.unlink()

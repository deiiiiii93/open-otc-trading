# backend/app/services/hedging_solver.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

GREEKS = ("delta", "gamma", "vega")


@dataclass
class Leg:
    key: str
    delta: float  # per-contract delta_cash
    gamma: float  # per-contract gamma_cash
    vega: float   # per-contract vega (raw)


@dataclass
class SolveResult:
    status: Literal["feasible", "infeasible"]
    quantities: dict[str, int]
    residual: dict[str, float]
    in_band: dict[str, bool]
    binding: list[dict] = field(default_factory=list)


def solve(*, targets, legs, bands, tiers, q_max=1_000_000, tol=1e-6, eps=1e-6) -> SolveResult:
    if not tiers:
        raise ValueError("tiers must be non-empty")

    n = len(legs)

    # Collect the union of all greeks across all tiers, preserving first-seen order.
    constrained: list[str] = []
    for tier in tiers:
        for g in tier["greeks"]:
            if g not in constrained:
                constrained.append(g)
    k = len(constrained)

    for g in constrained:
        if g not in GREEKS:
            raise ValueError(f"unsupported greek in tiers: {g!r}")
        if g not in bands:
            raise ValueError(f"bands missing entry for constrained greek {g!r}")

    hard_greeks = [g for tier in tiers if tier["kind"] == "hard" for g in tier["greeks"]]

    # Variable layout: q[0:n], e[n:n+k], a[n+k:n+k+n]
    #   q_i  = integer lot quantity for leg i  (can be negative)
    #   e_j  = continuous slack for greek constrained[j]  (≥ 0)
    #   a_i  = continuous abs-value proxy for |q_i|        (≥ 0)
    N = n + k + n

    def ei(g: str) -> int:
        """Column index of slack variable for greek g."""
        return n + constrained.index(g)

    def ai(i: int) -> int:
        """Column index of abs-value proxy for leg i."""
        return n + k + i

    # ------------------------------------------------------------------ #
    # Build base constraints (fixed throughout all tier solves)            #
    # ------------------------------------------------------------------ #
    A_rows: list[np.ndarray] = []
    lo: list[float] = []
    hi: list[float] = []

    # Band-with-slack for each constrained greek:
    #   Σ q_i c_ig  ≤  B_g - T_g + e_g   →   Σ q_i c_ig - e_g  ≤  B_g - T_g
    #  -Σ q_i c_ig  ≤  B_g + T_g + e_g   →  -Σ q_i c_ig - e_g  ≤  B_g + T_g
    # Together these mean  |T_g + Σ q_i c_ig|  ≤  B_g + e_g
    for g in constrained:
        c = np.array([getattr(legs[i], g) for i in range(n)], dtype=float)
        Tg = float(targets.get(g, 0.0))
        Bg = float(bands[g])
        r1 = np.zeros(N)
        r1[:n] = c
        r1[ei(g)] = -1.0
        A_rows.append(r1)
        lo.append(-np.inf)
        hi.append(Bg - Tg)

        r2 = np.zeros(N)
        r2[:n] = -c
        r2[ei(g)] = -1.0
        A_rows.append(r2)
        lo.append(-np.inf)
        hi.append(Bg + Tg)

    # No futile overshoot for HARD greeks: the residual may not cross to the far
    # side of the band. With target T_g ≥ 0 require R_g = T_g + Σ q_i c_ig ≥ -B_g;
    # with T_g < 0 require R_g ≤ B_g. q = 0 always satisfies this, so it never
    # removes feasibility — it only forbids trading whole lots that flip a
    # sub-one-lot exposure to the opposite side without reaching the band.
    for g in set(hard_greeks):
        c = np.array([getattr(legs[i], g) for i in range(n)], dtype=float)
        Tg = float(targets.get(g, 0.0))
        Bg = float(bands[g])
        r = np.zeros(N)
        r[:n] = c
        A_rows.append(r)
        if Tg >= 0.0:
            lo.append(-Bg - Tg)   # Σ q_i c_ig ≥ -B_g - T_g
            hi.append(np.inf)
        else:
            lo.append(-np.inf)
            hi.append(Bg - Tg)    # Σ q_i c_ig ≤ B_g - T_g

    # Abs-value linearisation:  a_i ≥ q_i  and  a_i ≥ -q_i
    for i in range(n):
        u1 = np.zeros(N)
        u1[ai(i)] = 1.0
        u1[i] = -1.0
        A_rows.append(u1)
        lo.append(0.0)
        hi.append(np.inf)

        u2 = np.zeros(N)
        u2[ai(i)] = 1.0
        u2[i] = 1.0
        A_rows.append(u2)
        lo.append(0.0)
        hi.append(np.inf)

    A_base = np.array(A_rows, dtype=float)
    lo_base = np.array(lo, dtype=float)
    hi_base = np.array(hi, dtype=float)

    base = [LinearConstraint(A_base, lo_base, hi_base)]

    # Variable bounds and integrality
    lb = np.concatenate([np.full(n, -q_max, dtype=float),
                         np.zeros(k, dtype=float),
                         np.zeros(n, dtype=float)])
    ub = np.concatenate([np.full(n, q_max, dtype=float),
                         np.full(k, np.inf, dtype=float),
                         np.full(n, np.inf, dtype=float)])
    bounds = Bounds(lb, ub)
    integrality = np.concatenate([np.ones(n, dtype=float),
                                  np.zeros(k, dtype=float),
                                  np.zeros(n, dtype=float)])

    frozen: list[LinearConstraint] = []
    best_x: np.ndarray | None = None

    # NOTE: _run reads `frozen` by reference; it grows by one constraint per tier.
    def _run(c_obj: np.ndarray):
        return milp(c=c_obj, constraints=base + frozen,
                    integrality=integrality, bounds=bounds)

    # ------------------------------------------------------------------ #
    # Lexicographic tiers: minimize slack for each tier, then freeze       #
    # ------------------------------------------------------------------ #
    for tier in tiers:
        c_obj = np.zeros(N, dtype=float)
        for g in tier["greeks"]:
            c_obj[ei(g)] = 1.0
        res = _run(c_obj)
        if not res.success:
            return SolveResult(
                status="infeasible",
                quantities={},
                residual={},
                in_band={},
                binding=[{"greek": "_model", "shortfall": float("inf")}],
            )
        best_x = res.x
        # Freeze: the next tier cannot worsen this tier's slack sum
        frozen.append(LinearConstraint(c_obj, -np.inf, float(res.fun) + eps))

    # ------------------------------------------------------------------ #
    # Parsimony tier: minimize total lots (Σ a_i) subject to frozen tiers  #
    # ------------------------------------------------------------------ #
    c_par = np.zeros(N, dtype=float)
    for i in range(n):
        c_par[ai(i)] = 1.0
    res_par = _run(c_par)
    if res_par.success:
        best_x = res_par.x

    # ------------------------------------------------------------------ #
    # Extract solution                                                      #
    # ------------------------------------------------------------------ #
    x = best_x if best_x is not None else np.zeros(N, dtype=float)
    quantities = {legs[i].key: int(round(x[i])) for i in range(n)}

    residual: dict[str, float] = {}
    in_band: dict[str, bool] = {}
    for g in GREEKS:
        Rg = float(targets.get(g, 0.0)) + sum(
            quantities[legs[i].key] * getattr(legs[i], g) for i in range(n)
        )
        residual[g] = Rg
        if g in constrained:
            in_band[g] = abs(Rg) <= float(bands[g]) + tol

    # Infeasibility: any HARD greek outside its band
    binding = [
        {"greek": g, "shortfall": abs(residual[g]) - float(bands[g])}
        for g in hard_greeks
        if not in_band.get(g, True)
    ]

    return SolveResult(
        status="infeasible" if binding else "feasible",
        quantities=quantities,
        residual=residual,
        in_band=in_band,
        binding=binding,
    )

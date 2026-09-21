"""Post-hoc probe for the contradiction-pair defect found by keepalive_smoke.py.

Hypothesis: shipped build_state lists siblings as bare strings, so Jev cannot
tell the NEWER of two contradicting facts and marks both level 0.
  arm A: sibling ages added to the state, nothing else changed
  arm B: arm A + level 0 reads "by a NEWER listed fact"
Same 11 facts, same fixed clock, same repeats as the shipped arm.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace

from app.services.deep_agent.memory import keep_alive as ka
from app.services.system_one import cap

import keepalive_smoke as base

ARM = sys.argv[2] if len(sys.argv) > 2 else "A"
_shipped_build_state = ka.build_state


def build_state_with_ages(row, siblings, config, now):
    state = _shipped_build_state(row, siblings, config, now)
    state["other_facts_in_scope"] = [
        {"fact": cap(f.content, config.keep_alive_sibling_chars),
         "age_days": ka.age_days(f.created_at, now)}
        for f in siblings
    ]
    return state


ka.build_state = build_state_with_ages
if ARM == "B":
    levels = list(ka.KEEP_ALIVE_LEVELS)
    levels[0] = "Contradicted or replaced by a newer listed fact, or plainly no longer true"
    ka.KEEP_ALIVE_QUESTION = replace(ka.KEEP_ALIVE_QUESTION, criteria=tuple(levels))

if __name__ == "__main__":
    base.main()

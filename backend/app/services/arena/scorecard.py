"""Per-model Arena scorecards for lab outreach.

Pure kernel: every function here takes plain dicts and returns strings or
dataclasses. No DB, no filesystem beyond reading the checked-in targets config,
so the whole module is unit-testable against frozen fixtures.

The CLI that supplies the DB rows is `scripts/render_scorecards.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

VALID_TIERS = frozenset({"A", "B1", "B2"})


@dataclass(frozen=True)
class Target:
    """One outreach recipient — a lab, and the models of theirs we scored."""

    lab: str
    tier: str
    model_ids: tuple[str, ...]
    channels: tuple[str, ...]
    anomaly: str | None
    interop_note: bool


def load_targets(path: Path) -> list[Target]:
    """Parse the checked-in targets config. Raises on an invalid tier."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    targets: list[Target] = []
    for entry in raw["targets"]:
        tier = entry["tier"]
        if tier not in VALID_TIERS:
            raise ValueError(f"{entry['lab']}: unknown tier {tier!r}")
        targets.append(
            Target(
                lab=entry["lab"],
                tier=tier,
                model_ids=tuple(entry["model_ids"]),
                channels=tuple(entry.get("channels") or ()),
                anomaly=(entry.get("anomaly") or None),
                interop_note=bool(entry.get("interop_note")),
            )
        )
    return targets

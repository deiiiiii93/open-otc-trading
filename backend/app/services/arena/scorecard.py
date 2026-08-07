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
    serving: dict[str, str]
    anomaly: str | None
    #: Per-lab text, not a flag. The four protocol-pinned models have four
    #: DISTINCT symptoms (two empty-id, two unparsed native markup); a shared
    #: blob would report to two labs a bug that was never theirs.
    interop_note: str | None


@dataclass(frozen=True)
class FailedCheck:
    """One assertion the model did not satisfy, named for the outreach card."""

    label: str
    axis: str
    step: str | None


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
                serving=dict(entry.get("serving") or {}),
                anomaly=(entry.get("anomaly") or None),
                interop_note=(entry.get("interop_note") or None),
            )
        )
    return targets


def banked_run_ids(board_configs: list[dict], run_id: int) -> list[int]:
    """Which runs hold the per-model transcripts behind a folded board.

    Read from the board's OWN `config.merged_from` provenance, never from a
    hardcoded range: a report's prose range goes stale the moment a model is
    backfilled onto an existing board. Run #94 merged from [81..90, 93], so a
    85-93 guess silently loses runs 81 and 84 — and the card then prints a
    confident "not banked" for models whose traces exist.
    """
    runs: set[int] = {run_id}
    for cfg in board_configs:
        for value in (cfg or {}).get("merged_from") or []:
            if isinstance(value, int):
                runs.add(value)
    return sorted(runs)


def latest_transcript_paths(
    rows: list[tuple[int, str, str | None]],
) -> dict[str, str]:
    """Map model_id → transcript path, letting the HIGHEST run id win.

    A model can appear on several banked runs — GLM 5.2 has one on the broken
    pre-`protocol:anthropic` run and one on the clean re-run. A re-run
    supersedes, so ordering must be explicit: relying on row iteration order
    could link a lab to a trace from a run we already know was invalid.
    """
    best: dict[str, tuple[int, str]] = {}
    for run_id, model_id, path in rows:
        if not path:
            continue
        current = best.get(model_id)
        if current is None or run_id > current[0]:
            best[model_id] = (run_id, path)
    return {model_id: path for model_id, (_run, path) in best.items()}


def resolve_transcript(
    paths_by_model: dict[str, str | None],
    model_id: str,
    repo_root: Path,
) -> str | None:
    """Return the repo-relative transcript path only if the file really exists.

    Folded board runs store `transcript_path=None`, and some models have no
    banked transcript at all. Returning None lets the card state the absence
    instead of linking a dangling pointer.
    """
    rel = paths_by_model.get(model_id)
    if not rel:
        return None
    return rel if (Path(repo_root) / rel).is_file() else None


def _is_failed(check: dict) -> bool:
    """A check is failed when it explicitly reports not passing.

    Fail-honest: a check with no `passed` key is unknown, not failed — reporting
    an unknown as a failure to a model's authors would be exactly the kind of
    fabricated claim the accuracy rule forbids.
    """
    return check.get("passed") is False


def _label(check: dict) -> str:
    return str(check.get("label") or check.get("kind") or "unlabelled")


def failed_checks(breakdown: dict, limit: int = 3) -> list[FailedCheck]:
    """Collect up to `limit` failed checks, step checks first, then session ones."""
    objective = breakdown.get("objective") or {}
    out: list[FailedCheck] = []

    for step in objective.get("steps") or []:
        index = step.get("index")
        for check in step.get("checks") or []:
            if _is_failed(check):
                out.append(FailedCheck(
                    label=_label(check),
                    axis=str(check.get("axis") or "unknown"),
                    step=str(index) if index is not None else None,
                ))

    for check in objective.get("success") or []:
        if _is_failed(check):
            out.append(FailedCheck(
                label=_label(check),
                axis=str(check.get("axis") or "unknown"),
                step=None,
            ))

    return out[:limit]


# (display label, key in card_mean) — the stored card mixes case: ovr/con are
# lowercase, the four axis stats are uppercase. Verified against run #94.
_STATS: tuple[tuple[str, str], ...] = (
    ("OVR", "ovr"), ("GRD", "GRD"), ("ADH", "ADH"), ("SYN", "SYN"),
    ("EFF", "EFF"), ("PRC", "PRC"), ("CON", "con"),
)

_ENVIRONMENT = """\
The environment is a live OTC derivatives trading desk, not a static prompt set. Each
model drives the production orchestrator end to end with no human in the loop — reading
risk, pricing a portfolio, running scenarios, and producing a governance report, where
each step consumes the previous step's output. Scoring reads the system's own trace log
against a fixed objective manifest; there is no LLM judge in these numbers.\
"""

_QUESTION = """\
### My question

Did I serve your model correctly? The configuration above is everything I controlled. If
any of it is wrong — the wire protocol, the gateway, the way tools are presented — I
would rather fix the harness and re-run than publish a number that measures my plumbing
instead of your model.\
"""

_STATS_LEGEND = (
    "Stats are scaled 0–99. GRD grounding, ADH adherence, SYN synthesis, EFF "
    "efficiency against a calibrated par, PRC precision, CON consistency across "
    "trials. OVR is their weighted combination; CON is reported separately and is "
    "not folded into OVR."
)


def _card_table(rows: list[dict]) -> str:
    labels = [label for label, _ in _STATS]
    lines = [
        "| Model | " + " | ".join(labels) + " | Objective |",
        "|---" * (len(_STATS) + 2) + "|",
    ]
    for row in rows:
        card = row.get("card_mean") or {}
        cells = [str(card.get(key, "n/a")) if card else "n/a" for _, key in _STATS]
        lines.append(
            f"| `{row['model_id']}` | " + " | ".join(cells)
            + f" | {row.get('mean_objective', 'n/a')} |"
        )
    return "\n".join(lines)


def _serving_section(target: Target, trials: dict[str, int]) -> list[str]:
    """Disclose how the model was served.

    The "did I serve you right?" question is unanswerable without this, so the
    section is rendered even when the config is empty — as an explicit gap
    rather than a silent omission.
    """
    out = ["### How it was served", ""]
    if not target.serving:
        out += ["Serving configuration was **not recorded** for this run.", ""]
        return out
    for key, value in target.serving.items():
        out.append(f"- **{key}**: `{value}`")
    # n_trials from the stored breakdown, NOT the leaderboard's match_count:
    # on a folded board match_count is always 1 (one aggregate row), which
    # would understate the evidence to the recipient.
    counts = [n for n in trials.values() if isinstance(n, int) and n > 0]
    if counts:
        out.append(f"- **trials per model, folded into this row**: {max(counts)}")
    out += [
        "- **LLM jury**: disabled — every number above is rule-based",
        "",
        "Anything not listed here is repo default; the published run report carries the "
        "full reproducibility section.",
        "",
    ]
    return out


def render_scorecard(
    *,
    target: Target,
    rows: list[dict],
    checks: dict[str, list[FailedCheck]],
    transcripts: dict[str, str | None],
    run_id: int,
    trials: dict[str, int] | None = None,
) -> str:
    """Render one lab's outreach card. Pure — no IO."""
    parts: list[str] = [
        f"# {target.lab} — OTC Desk Agent Arena, Run #{run_id}",
        "",
        _ENVIRONMENT,
        "",
        "## Result",
        "",
        _card_table(rows),
        "",
        _STATS_LEGEND,
        "",
    ]

    if any(not r.get("card_mean") for r in rows):
        parts += [
            "> An ability card is **not available** for a row above: the stored "
            "breakdown lacked the evidence needed to derive one. The objective score "
            "still stands.",
            "",
        ]

    if target.interop_note:
        parts += [
            "### One interoperability finding you may want",
            "",
            target.interop_note.strip(),
            "",
        ]

    if target.anomaly:
        parts += ["### What stands out", "", target.anomaly.strip(), ""]

    failing = [(mid, c) for mid, cs in checks.items() for c in cs]
    if failing:
        parts += ["### Specific checks that did not pass", ""]
        for mid, check in failing:
            where = f", step {check.step}" if check.step is not None else ""
            parts.append(f"- `{mid}` — **{check.label}** ({check.axis}{where})")
        parts.append("")

    parts += _serving_section(target, trials or {})

    parts += ["### Evidence", ""]
    for model_id in target.model_ids:
        path = transcripts.get(model_id)
        if path:
            parts.append(f"- `{model_id}` — full trace: `{path}`")
        else:
            parts.append(
                f"- `{model_id}` — per-trial trace **not banked** for this board row; "
                "the score derives from the stored breakdown."
            )
    parts += ["", _QUESTION, ""]
    return "\n".join(parts)

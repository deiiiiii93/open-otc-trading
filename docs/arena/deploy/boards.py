"""boards.yaml (curation) + boards.json (measurement) -> validated records.

Two inputs, deliberately separate:

* **boards.yaml** is editorial. A *run* is not a *board* — the arena DB holds
  one-model smokes and A/B probes on the same workflows as the real fields, and
  only a human can say which runs are boards worth publishing. Validation is
  fail-loud, like manifest.py: a typo must stop the build, never quietly drop a
  board off the page.
* **boards.json** is measured. collect_boards.py derives it from the arena DB
  through the SAME ranking kernel the desk UI uses, so the published page cannot
  disagree with the app, and no score is ever hand-typed. That is the defect this
  whole package exists to prevent: the previous site froze at Run #94 because its
  leaderboards were typed into HTML.

This module is pure: stdlib + yaml, no DB, no network, no writes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

BOARD_REQUIRED = {"run", "workflow"}
BOARD_OPTIONAL = {"label", "post"}
BOARD_ALLOWED = BOARD_REQUIRED | BOARD_OPTIONAL

PROVISIONAL_REQUIRED = {"run"}
PROVISIONAL_OPTIONAL = {"label", "note"}
PROVISIONAL_ALLOWED = PROVISIONAL_REQUIRED | PROVISIONAL_OPTIONAL

SNAPSHOT_VERSION = 1


class BoardsError(ValueError):
    """A boards.yaml entry is invalid. The message always names the entry."""


@dataclass(frozen=True)
class BoardRef:
    """One published board: a run, pinned to the workflow it is claimed to cover.

    `workflow` is redundant with the DB and asserted against it on export. That
    redundancy is the guard: a multi-workflow run (#104, #110) folds every
    workflow into one row per model, so silently accepting one here would publish
    a cross-workflow average under a single workflow's heading.
    """

    run: int
    workflow: str
    label: str
    post: str | None = None


@dataclass(frozen=True)
class ProvisionalRef:
    """A run published as CARDS ONLY, never as a board.

    A run is not a board. A one-model smoke has no field, so a rank of #1 of 1
    measures nothing — but an ability card is an ABSOLUTE measurement
    (passed/total per axis, EFF against each workflow's own par), so it stays
    meaningful with no opponent. That asymmetry is the whole reason this exists
    beside the leaderboard instead of inside it, and why nothing derived from it
    ever carries a rank.

    Deliberately carries NO workflow: unlike a board, a cards-only run is free to
    span several, because its measurements are published one per workflow rather
    than folded into a single row.
    """

    run: int
    label: str
    note: str | None = None


def _provisional_ref(raw: object, index: int) -> ProvisionalRef:
    if not isinstance(raw, dict):
        raise BoardsError(f"provisional #{index}: must be a mapping, got {raw!r}")
    where = f"provisional #{index} (run {raw.get('run', '?')})"

    unknown = set(raw) - PROVISIONAL_ALLOWED
    if unknown:
        raise BoardsError(
            f"{where}: unknown key(s) {sorted(unknown)}; "
            f"allowed {sorted(PROVISIONAL_ALLOWED)}"
        )
    missing = PROVISIONAL_REQUIRED - set(raw)
    if missing:
        raise BoardsError(f"{where}: missing required key(s) {sorted(missing)}")

    run = raw["run"]
    if not isinstance(run, int) or isinstance(run, bool):
        raise BoardsError(f"{where}: run must be an integer, got {run!r}")

    note = raw.get("note")
    return ProvisionalRef(
        run=run,
        label=str(raw.get("label") or f"Run #{run}").strip(),
        note=str(note).strip() if note else None,
    )


def _ref(raw: object, index: int) -> BoardRef:
    if not isinstance(raw, dict):
        raise BoardsError(f"entry #{index}: must be a mapping, got {raw!r}")
    where = f"entry #{index} (run {raw.get('run', '?')})"

    unknown = set(raw) - BOARD_ALLOWED
    if unknown:
        raise BoardsError(
            f"{where}: unknown key(s) {sorted(unknown)}; allowed {sorted(BOARD_ALLOWED)}"
        )
    missing = BOARD_REQUIRED - set(raw)
    if missing:
        raise BoardsError(f"{where}: missing required key(s) {sorted(missing)}")

    run = raw["run"]
    # bool is an int subclass; `run: true` is a typo, not run 1.
    if not isinstance(run, int) or isinstance(run, bool):
        raise BoardsError(f"{where}: run must be an integer, got {run!r}")

    workflow = str(raw["workflow"]).strip()
    if not workflow:
        raise BoardsError(f"{where}: workflow is empty")

    post = raw.get("post")
    return BoardRef(
        run=run,
        workflow=workflow,
        label=str(raw.get("label") or f"Run #{run}").strip(),
        post=str(post) if post else None,
    )


def _sections(path: Path) -> tuple[list, list]:
    """(boards, provisional) raw entry lists from boards.yaml.

    A bare list at the top level is still a valid manifest and means "all
    boards" — the shape the file had before cards-only runs existed. Accepting
    it keeps an older manifest readable instead of failing the whole build over
    a format change that carries no new information.
    """
    if not path.is_file():
        raise BoardsError(f"boards manifest not found at {path}")
    raw = yaml.safe_load(path.read_text()) or []
    if isinstance(raw, list):
        return raw, []
    if not isinstance(raw, dict):
        raise BoardsError(
            f"{path}: top level must be a list of boards, or a mapping with "
            "'boards' and optional 'provisional' keys"
        )
    unknown = set(raw) - {"boards", "provisional"}
    if unknown:
        raise BoardsError(f"{path}: unknown top-level key(s) {sorted(unknown)}")
    boards = raw.get("boards")
    prov = raw.get("provisional")
    for name, value in (("boards", boards), ("provisional", prov)):
        # `value or []` would be wrong here: {} and "" are falsy, so a
        # wrong-typed section would silently become an empty one and the whole
        # leaderboard would vanish without a word. Absent (None) is the only
        # legitimate empty.
        if value is not None and not isinstance(value, list):
            raise BoardsError(f"{path}: '{name}' must be a list, got {value!r}")
    return boards or [], prov or []


def load_provisional_refs(path: Path) -> list[ProvisionalRef]:
    """Load and validate the cards-only entries. Order is preserved."""
    _, raw = _sections(path)
    refs = [_provisional_ref(entry, i) for i, entry in enumerate(raw)]
    seen: set[int] = set()
    for r in refs:
        if r.run in seen:
            raise BoardsError(f"duplicate provisional entry: run {r.run}")
        seen.add(r.run)
    return refs


def load_board_refs(path: Path) -> list[BoardRef]:
    """Load and validate boards.yaml. Order is preserved; the caller groups."""
    raw, prov = _sections(path)
    refs = [_ref(entry, i) for i, entry in enumerate(raw)]

    # A run cannot be both a ranked board and an unranked measurement — the two
    # make contradictory claims about whether it had a field.
    board_runs = {r["run"] for r in raw if isinstance(r, dict) and "run" in r}
    for entry in prov:
        if isinstance(entry, dict) and entry.get("run") in board_runs:
            raise BoardsError(
                f"run {entry['run']} is declared as both a board and provisional"
            )

    seen: set[tuple[int, str]] = set()
    for r in refs:
        key = (r.run, r.workflow)
        if key in seen:
            raise BoardsError(f"duplicate board: run {r.run} on {r.workflow}")
        seen.add(key)
    return refs


def load_boards(path: Path) -> dict | None:
    """The exported snapshot, or None if absent, unreadable, or a foreign version.

    There is deliberately NO staleness rule here, unlike stats.load_snapshot. A
    traffic count expires because it is a claim about *now*; a finished board is
    a historical fact and stays true however old the export is. What it must
    never do is render half-formed, hence the version gate.
    """
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != SNAPSHOT_VERSION:
        return None
    if not isinstance(data.get("workflows"), list):
        return None
    return data


def ordered_workflows(workflows: list[dict]) -> list[dict]:
    """Measured workflows first (newest board first), unmeasured last by id.

    A workflow with no board is kept, not dropped: the page has to be able to say
    "nothing has been run here yet", which is a different statement from omitting
    the section and letting the reader assume the workflow does not exist.
    """

    def newest(w: dict) -> str:
        return max(str(b.get("date") or "") for b in w["boards"])

    # Two stable passes, not one reversed compound key: `reverse=True` over a
    # (date, id) tuple would also reverse the id tie-break, so two workflows
    # measured on the same day would list z-to-a.
    measured = sorted(
        [w for w in workflows if w.get("boards")], key=lambda w: str(w.get("id", ""))
    )
    measured.sort(key=newest, reverse=True)
    unmeasured = sorted(
        [w for w in workflows if not w.get("boards")],
        key=lambda w: str(w.get("id", "")),
    )
    return measured + unmeasured


# ---------------------------------------------------------------------------
# Shaping store rows into the published snapshot (pure — see collect_boards.py)
# ---------------------------------------------------------------------------

# The five ability stats. `base_ovr` and `con` are carried separately: base_ovr is
# the pre-consistency OVR and publishing it beside the headline would invite the
# two to be read as competing scores.
CARD_STATS = ("GRD", "ADH", "SYN", "PRC", "EFF")


def shape_row(row: dict, trials: int | None = None) -> dict:
    """One store leaderboard row -> one published row.

    An uncarded contestant keeps `None`/`{}`, never zeroes: a zero OVR asserts a
    measured failure, while the truth is that this board's instrument had no card.

    `matches` and `trials` are NOT the same number and must not be conflated: the
    runner folds a contestant's trials into ONE aggregate match, so a two-trial
    contestant has match_count 1. Publishing match_count as "trials" would report
    every multi-trial board as single-trial — and CON, which exists only because
    trials disperse, would look like it came from one sample.
    """
    card = row.get("card_mean") or {}
    return {
        "rank": int(row["rank"]),
        "model": str(row["model_id"]),
        # None = the run pinned no effort, which is an absence, not a level.
        "effort": row.get("reasoning_effort") or None,
        "ovr": card.get("ovr"),
        "stats": {s: card[s] for s in CARD_STATS if s in card},
        "con": card.get("con"),
        "objective": row.get("mean_objective"),
        "matches": int(row.get("match_count") or 0),
        "trials": trials,
        "invalid": int(row.get("invalid_count") or 0),
    }


def shape_board(
    ref: BoardRef,
    rows: list[dict],
    workflow_ids: set[str],
    date: str,
    checks: int | None,
    trials_by_arm: dict[tuple[str, str | None], int] | None = None,
) -> dict:
    """Validate a run against its declaration, then publish it.

    `workflow_ids` is every workflow the run actually scored. It must be exactly
    the one declared: store.leaderboard aggregates a run's matches into one row
    per contestant with no workflow filter, so a multi-workflow run (#104, #110)
    would publish a cross-workflow average under one workflow's heading.
    """
    if workflow_ids != {ref.workflow}:
        found = ", ".join(sorted(workflow_ids)) or "nothing"
        verb = "spans" if len(workflow_ids) > 1 else "was measured on"
        raise BoardsError(
            f"run {ref.run} declared as {ref.workflow} but {verb} {found}"
        )
    if not rows:
        raise BoardsError(f"run {ref.run}: no scored rows to publish")

    trials_by_arm = trials_by_arm or {}
    shaped = [
        shape_row(r, trials_by_arm.get((r["model_id"], r.get("reasoning_effort") or None)))
        for r in rows
    ]
    carded_rows = sum(1 for r in shaped if r["ovr"] is not None)
    return {
        "run": ref.run,
        "label": ref.label,
        "date": date,
        "post": ref.post,
        "checks": checks,
        # A single carded contestant is enough to warrant the OVR column; the
        # uncarded rows render an em dash there, which is the honest reading.
        "carded": carded_rows > 0,
        "carded_rows": carded_rows,
        "models": len(shaped),
        # The common trial depth, or None when contestants ran different depths —
        # in which case only the per-row number is true.
        "trials": (lambda t: t.pop() if len(t) == 1 else None)(
            {r["trials"] for r in shaped}
        ),
        "rows": shaped,
    }


# ---------------------------------------------------------------------------
# Consolidated model cards (all workflows averaged)
# ---------------------------------------------------------------------------

def _mean(values: list[int]) -> int:
    return round(sum(values) / len(values))


def _average_cards(cards: list[dict]) -> dict:
    """The mean of a set of ABSOLUTE ability cards, plus the spread it hides.

    Shared by the consolidated career card and the provisional card so there is
    exactly one definition of "averaging cards" — the operation the site claims
    is sound where merging leaderboards is not. Callers pass only carded entries.
    """
    ovrs = [int(c["ovr"]) for c in cards]
    cons = [int(c["con"]) for c in cards if c.get("con") is not None]
    stats: dict[str, list[int]] = {}
    for c in cards:
        for stat, value in (c.get("stats") or {}).items():
            stats.setdefault(stat, []).append(int(value))
    return {
        "ovr": _mean(ovrs),
        "ovr_min": min(ovrs),
        "ovr_max": max(ovrs),
        "stats": {s: _mean(v) for s, v in stats.items()},
        # CON needs trials to disperse; a single-trial run has none to average.
        "con": _mean(cons) if cons else None,
        "coverage": len(ovrs),
    }


def shape_provisional(ref: ProvisionalRef, entries: list[dict], date: str) -> dict:
    """A cards-only run: one card per contestant, averaged over its workflows.

    `entries` is one measurement per (contestant, workflow). Nothing here carries
    a rank, by construction rather than by omission — see ProvisionalRef.
    """
    if not entries:
        raise BoardsError(f"run {ref.run}: no measurements to publish")

    order: list[tuple[str, str | None]] = []
    groups: dict[tuple[str, str | None], list[dict]] = {}
    for e in entries:
        key = (str(e["model"]), e.get("effort") or None)
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(e)

    cards = []
    for model, effort in order:
        group = groups[(model, effort)]
        carded = [e for e in group if e.get("ovr") is not None]
        # Uncarded contributes nothing to average; a contestant with no card at
        # all is dropped rather than published as a zero.
        if not carded:
            continue
        depths = {e.get("trials") for e in carded}
        cards.append({
            "model": model,
            "effort": effort,
            **_average_cards(carded),
            # Distinct from coverage: how many workflows the run actually scored
            # for this contestant, so "4 of 5" stays visible when one is uncarded.
            "workflows_total": len({e.get("workflow") for e in group}),
            "trials": depths.pop() if len(depths) == 1 else None,
            "per_workflow": [
                {"workflow": e.get("workflow"), "ovr": int(e["ovr"])}
                for e in sorted(carded, key=lambda x: (-int(x["ovr"]),
                                                       str(x.get("workflow") or "")))
            ],
        })

    if not cards:
        raise BoardsError(f"run {ref.run}: no carded measurements to publish")

    cards.sort(key=lambda c: (-c["ovr"], c["model"], c["effort"] or ""))
    return {
        "run": ref.run,
        "label": ref.label,
        "date": date,
        "note": ref.note,
        "models": len(cards),
        "cards": cards,
    }


def _flat_boards(workflows: list[dict]) -> list[tuple[dict, dict]]:
    """(workflow, board) pairs in the order given. One board per workflow today,
    but the shape does not assume it."""
    return [(w, b) for w in workflows for b in (w.get("boards") or [])]


def consolidated_cards(workflows: list[dict]) -> list[dict]:
    """A career card per contestant: its ability card averaged across boards.

    Averaging here is defensible where merging a LEADERBOARD is not. A card is an
    ABSOLUTE measurement — passed/total per axis, EFF against that workflow's own
    par — so it does not depend on who else was in the field, and each board's card
    has already been normalised to its own instrument. (The Run #110 report takes
    the same mean across five workflows.) What a mean does hide is the spread, so
    ovr_min/ovr_max and a per-board breakdown are published beside it rather than
    left for the reader to reconstruct.

    Uncarded rows contribute nothing: there is no OVR to average.
    """
    flat = _flat_boards(workflows)
    order: list[tuple[str, str | None]] = []
    acc: dict[tuple[str, str | None], dict] = {}

    for index, (_wf, board) in enumerate(flat):
        for row in board.get("rows") or []:
            if row.get("ovr") is None:
                continue
            key = (str(row["model"]), row.get("effort") or None)
            if key not in acc:
                order.append(key)
                acc[key] = {"rows": [], "at": {}}
            slot = acc[key]
            slot["rows"].append(row)
            slot["at"][index] = row

    cards = []
    for key in order:
        model, effort = key
        slot = acc[key]
        # An entry for EVERY board, contested or not, so a gap in a model's
        # record is visible on the card instead of inferred from a short list.
        per_board = []
        for index, (wf, board) in enumerate(flat):
            row = slot["at"].get(index)
            per_board.append({
                "workflow": wf.get("id"),
                "label": board.get("label"),
                "ovr": int(row["ovr"]) if row else None,
                "rank": int(row["rank"]) if row else None,
                "field": board.get("models"),
            })
        cards.append({
            "model": model,
            "effort": effort,
            **_average_cards(slot["rows"]),
            "boards_total": len(flat),
            "per_board": per_board,
        })

    # Coverage breaks a tie on the mean: between two equal means, the one measured
    # on more boards is the better-evidenced claim.
    cards.sort(key=lambda c: (-c["ovr"], -c["coverage"], c["model"], c["effort"] or ""))
    return cards

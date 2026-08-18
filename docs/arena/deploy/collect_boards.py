#!/usr/bin/env python3
"""Arena database -> boards.json.

The only step in this package that touches the backend, exactly as
collect_stats.py is the only step that touches the server. The build itself stays
stdlib+yaml, so a broken venv can never take the site down — it just publishes
without a leaderboard.

Rankings come from `store.leaderboard`, the SAME kernel the desk UI ranks with,
so the published page cannot disagree with the app and no score is hand-typed.

    ./deploy.sh boards            # refresh boards.json from data/open_otc.sqlite3
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "backend"))

from boards import (  # noqa: E402
    BoardsError, SNAPSHOT_VERSION, consolidated_cards, load_board_refs,
    ordered_workflows, shape_board,
)

DEFAULT_DB = REPO / "data" / "open_otc.sqlite3"


def read_only_engine(db_path: Path):
    """SQLite in URI read-only mode.

    Publishing must be incapable of mutating the desk database. `mode=ro` makes
    that a property of the connection rather than a promise about the code — and
    it also refuses to CREATE a missing file, so a wrong --db path fails loudly
    instead of exporting an empty board set from a blank database.
    """
    from sqlalchemy import create_engine

    return create_engine(f"sqlite:///file:{db_path}?mode=ro&uri=true")


def _checks_denominator(matches) -> int | None:
    """The check count this board was actually scored against.

    Read from the stored breakdowns, never re-derived from today's manifest: a
    manifest revision changes the denominator, and labelling an old board with a
    new instrument's count is precisely the comparability error this page exists
    to avoid. Disagreement within one board means there is no single denominator,
    so we publish none rather than a plausible one.
    """
    totals = set()
    for m in matches:
        axes = ((m.score_breakdown or {}).get("objective") or {}).get("axes") or {}
        if not axes:
            return None
        totals.add(sum(int(a.get("total", 0)) for a in axes.values()))
    if len(totals) != 1:
        return None
    return totals.pop()


def _stamp_positions(cards: list[dict]) -> None:
    """Attach the FIFA-style archetype to each card, in place.

    `scoring._card_position` is private but it is the SINGLE definition of the
    archetype rule, and reimplementing ten lines here is exactly how a published
    label drifts from the desk's. If it is ever moved the import fails loudly at
    export time rather than quietly disagreeing.
    """
    from app.services.arena.scoring import _card_position

    for card in cards:
        stats = card.get("stats") or {}
        card["position"] = (
            _card_position(stats)
            if all(k in stats for k in ("GRD", "ADH", "SYN", "EFF", "PRC"))
            else None
        )


def collect(session, refs) -> dict:
    from app.golden_workflows.registry import list_workflows
    from app.models import ArenaMatch, ArenaRun
    from app.services.arena.store import leaderboard

    by_workflow: dict[str, list[dict]] = {}
    for ref in refs:
        run = session.get(ArenaRun, ref.run)
        if run is None:
            raise BoardsError(f"run {ref.run} is not in this database")

        matches = (
            session.query(ArenaMatch)
            .filter(ArenaMatch.run_id == ref.run, ArenaMatch.status == "scored")
            .all()
        )
        # One match per arm on a single-workflow run (the upsert key is
        # workflow+model+effort), so this map cannot collide.
        trials_by_arm = {}
        for m in matches:
            n = (m.score_breakdown or {}).get("n_trials")
            if isinstance(n, int) and not isinstance(n, bool):
                trials_by_arm[(m.model_id, m.reasoning_effort or None)] = n

        board = shape_board(
            ref,
            leaderboard(session, run_id=ref.run),
            workflow_ids={m.workflow_id for m in matches},
            date=str(run.created_at)[:10],
            checks=_checks_denominator(matches),
            trials_by_arm=trials_by_arm,
        )
        by_workflow.setdefault(ref.workflow, []).append(board)

    workflows = []
    for wf in list_workflows():
        boards = sorted(
            by_workflow.pop(wf.id, []), key=lambda b: b["date"], reverse=True
        )
        workflows.append({
            "id": wf.id,
            "title": wf.title,
            "persona": wf.persona,
            "steps": len(wf.steps),
            "par": wf.par_tool_calls,
            "boards": boards,
        })

    # A board declared for a workflow the registry no longer knows would silently
    # vanish from the page; say so instead.
    if by_workflow:
        raise BoardsError(
            f"boards declared for unknown workflow(s): {sorted(by_workflow)}"
        )
    # Section order is decided once, here, so a consolidated card's per-board
    # list reads in the same order as the page's workflow sections.
    workflows = ordered_workflows(workflows)
    for wf in workflows:
        for board in wf["boards"]:
            _stamp_positions(board["rows"])

    models = consolidated_cards(workflows)
    _stamp_positions(models)

    return {
        "version": SNAPSHOT_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "workflows": workflows,
        "models": models,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export arena boards to boards.json.")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--out", type=Path, default=HERE / "boards.json")
    ap.add_argument("--manifest", type=Path, default=HERE / "boards.yaml")
    args = ap.parse_args(argv)

    if not args.db.is_file():
        print(f"no arena database at {args.db}", file=sys.stderr)
        return 1

    from sqlalchemy.orm import sessionmaker

    refs = load_board_refs(args.manifest)
    engine = read_only_engine(args.db.resolve())
    with sessionmaker(bind=engine)() as session:
        snapshot = collect(session, refs)

    args.out.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    measured = [w for w in snapshot["workflows"] if w["boards"]]
    total = sum(len(w["boards"]) for w in measured)
    print(
        f"exported {total} boards across {len(measured)} workflows "
        f"({len(snapshot['workflows']) - len(measured)} with none), "
        f"{len(snapshot['models'])} consolidated model cards -> {args.out}"
    )
    for w in measured:
        for b in w["boards"]:
            mixed = (
                "" if b["carded_rows"] == b["models"]
                else f", {b['carded_rows']}/{b['models']} carded"
            )
            print(f"  {w['id']:<34} {b['label']:<10} {b['models']} contestants{mixed}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BoardsError as exc:
        print(f"boards export failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

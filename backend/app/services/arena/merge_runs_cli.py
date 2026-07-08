"""CLI: fold several single-trial arena runs into one multi-trial aggregate run.

    python -m app.services.arena.merge_runs_cli 18 19

Each ``(workflow, model)`` pair's scored matches across the given runs become the
trials of one aggregate match, which the ability card scores on read with a
trial-dispersion Consistency (CON) stat. Non-destructive — a NEW run is created and
the sources are left intact (see ``store.merge_runs``). Prints the new run id and a
per-model read-back (OVR / base / CON / per-trial OVRs) so CON is visible at once.
"""
from __future__ import annotations

import argparse

from app.database import SessionLocal
from app.services.arena import store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="merge_runs_cli",
        description="Merge single-trial arena runs into one multi-trial aggregate run.",
    )
    parser.add_argument("run_ids", type=int, nargs="+",
                        help="two or more source run ids to fold into trials")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        new_run_id = store.merge_runs(session, args.run_ids)
        session.commit()
        detail = store.get_run(session, new_run_id)

    print(f"Created merged run #{new_run_id} from runs {args.run_ids}")
    for match in (detail or {}).get("matches", []):
        bd = match.get("score_breakdown") or {}
        card = bd.get("card") or {}
        trial_ovrs = [(t.get("card") or {}).get("ovr") for t in bd.get("aggregate", [])]
        print(f"  {match['model_id']:24s} OVR {card.get('ovr')} "
              f"(base {card.get('base_ovr')}) CON {card.get('con')}  trials {trial_ovrs}")
    return new_run_id


if __name__ == "__main__":
    main()

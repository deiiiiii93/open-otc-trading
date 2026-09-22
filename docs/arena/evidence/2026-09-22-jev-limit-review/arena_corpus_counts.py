"""Count the arena corpus's waive / comment tool calls (read-only, no Jev calls).

The same reader `arena_threads.py` uses (scripts/limit_review_probe.py::_corpus),
kept for both tools, so the post's "zero waivers" is a recorded count here rather
than a line in a guide. Usage: as arena_threads.py.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "scripts"))

from limit_review_probe import _corpus  # noqa: E402

rows = _corpus(100_000)
out = {
    "rows": len(rows),
    "by_tool": dict(Counter(r["tool"] for r in rows)),
    "by_tool_and_step5": {f"{t} / {s}": n for (t, s), n in sorted(Counter((r["tool"], r["step5"]) for r in rows).items())},
    "threads": len({r["thread_id"] for r in rows}),
    "models": len({r["model"] for r in rows}),
    "run_ids": sorted({str(r["run_id"]) for r in rows}),
    "threads_without_run_id": len({r["thread_id"] for r in rows if r["run_id"] is None}),
}
(HERE / "arena_corpus_counts.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out))

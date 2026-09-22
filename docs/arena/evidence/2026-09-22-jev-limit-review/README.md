# Evidence — "Can Jev read a limit waiver?" (2026-09-22)

Raw per-call results and the scripts that produced them, for
[`../../2026-09-22-jev-limit-review.md`](../../2026-09-22-jev-limit-review.md). Not
published: the blog publishes only what `deploy/posts.yaml` lists.

All runs: desk commit `569ab1a` (the limit incident review, merged that morning),
`typesafe/jev-1.13` via ZenMux, 2026-09-22. `review_smoke.py` and the server session
drive the **merged** path — `review.score_event` on events written by the real
`incidents.waive` / `incidents.comment`, the REST routes, the AUTO chat turn, the
monitoring run's sweep; none builds its own Jev request. `arena_threads.py` is a
probe and says so: the feature never scores an arena thread.

| Result file | Script | Jev calls | Feeds |
|---|---|:--:|---|
| `cases.json` | — (committed at `1f4dee0` before any run) | — | the held-out cases, threads and pairs |
| `review_results.json` | `review_smoke.py 3` | 129 | ladder, pairs, threads, skip gates |
| `e2e_log.json` | `e2e_seed.py`, then `e2e_drive.py outage / agent / heal` against a server | 5 | the live-desk table |
| `e2e_results.json` | `e2e_export.py` (the session database) | — | review rows, events, guard verdict, audit, agent tool view |
| `arena_threads.json` | `arena_threads.py` (read-only over the arena corpus) | 91 | the model-written comments |
| `arena_corpus_counts.json` | `arena_corpus_counts.py` (same reader, no Jev calls) | — | 98 comments, 0 waiver rationales, 26 models, 4 threads without a run id |
| `ui_outage.png`, `ui_healed*.png` | Playwright against `vite` on the same server | — | the UI before and after |

In total 225 live Jev calls (129 + 91 + 5), of which 1 is the tool guard's (the
agent's waive); no call timed out.

## The ladder fixture is post-hoc

`scripts/fixtures/limit_review_rationales.json` (15 cases) tuned the wording
(11/15 → 14/15, recorded in `backend/app/services/system_one/CLAUDE.md`), so its
results are not evidence that the wording generalises. The twelve `holdout` cases,
eight threads and four pairs in `cases.json` were written after that and committed
**before** any of them was sent to Jev; the labels are one author's reading of the
level and option text in `backend/app/services/limits/review.py`.

## The session, in order

1. `outage` — server with System One on and **no key**: REST waive and comment,
   each leaving an `unscored: no_key` row.
2. `agent` — server restarted **with** the key: one AUTO turn in which the user
   authorises an extension and asks the model (the desk default, DeepSeek V4 Flash)
   to write the rationale. The first `agent` attempt never reached the server: the
   shell's `HTTP_PROXY` carried the localhost call and dropped it; the script now
   passes `trust_env=False`.
3. `heal` — risk re-run and a monitoring run through REST (the arena's step-6
   order). The book already holds the hedge, so the incident recovers; the run's
   sweep scores the two outage rows. It took three tries:
   - The first `heal` ran right after the keyed restart, before `agent`. It logged
     "restarted with key; nothing retried yet" (05:26:45 UTC, the first `heal` entry
     in the log) and then died in `derive_monitoring_envelope`: the seeded risk run
     carries no market-evidence id. No run was queued. The script was changed to
     re-run risk first, and because that recovers the incident, `agent` was moved
     before `heal`.
   - The second `heal` (after `agent`) re-ran risk, queued monitoring run 2 at
     05:30:41 and crashed polling it (the run GET needs `portfolio_id`). The sweep
     had already scored both rows by 05:30:41.9.
   - `finish 2` completed that tail from run 2. It waited out a 120 s poll because
     the run's terminal status was `completed_with_unknowns`. Both script bugs are
     fixed in the committed file.

## The arena threads, hand-read

Jev read 75 of the 91 threads as `root_cause_only`. The other sixteen were read by
hand (one reader) and grouped as the post's table does:

| Group | Threads | Jev |
|---|---|---|
| remediation reported done and verified | 652, 653 | `remediating` |
| the figure omits the hedge / was a data artefact | 613, 651, 679, 969 · 902 | `remediating` · `disputes_number` 0.48 |
| a fix recommended, none taken | 670, 674, 676, 906, 968, 972, 973 | `remediating` |
| hold open pending verification | 681, 745 | `remediating` |

The "eleven caveats / four recommendations" among the `root_cause_only` threads come
from a keyword pass (`hedge|futures|offset`) and reading the matching sentences, not
from reading all 75 in full.

## Re-running

Never against the live database. From the repo root:

```bash
export OPEN_OTC_ENV_FILE=              # no dotenv: nothing from .env leaks in
export ZENMUX_API_KEY=...              # Jev
export OPEN_OTC_SYSTEM_ONE=true OPEN_OTC_TOOL_GUARD=shadow OPEN_OTC_TRACING=off OPEN_OTC_MEMORY=off
export OPEN_OTC_DATABASE_URL="sqlite+pysqlite:////tmp/jev-review.sqlite3"   # a scratch file
export PYTHONPATH="$PWD/backend:$PWD/docs/arena/evidence/2026-09-22-jev-limit-review"
cd docs/arena/evidence/2026-09-22-jev-limit-review
../../../../.venv/bin/python review_smoke.py 3
```

- The server session additionally needs `DEEPSEEK_API_KEY`,
  `AGENT_CHANNELS_FILE=<repo>/config/agent_channels.yaml` and
  `AGENT_CHECKPOINT_DB_PATH=<scratch>`, a fresh scratch DB seeded by `e2e_seed.py`,
  and `uvicorn app.main:app --port 8767` with the same env — first **without**
  `ZENMUX_API_KEY` for `outage`, then with it for `agent` and `heal`.
- `arena_threads.py` reads the live DB and the trace DB and writes nothing to
  either; point `OPEN_OTC_DATABASE_URL` / `OPEN_OTC_TRACE_DB_PATH` at them.
- Jev is not deterministic; expect probabilities to move in the second decimal.
  Across the 43 three-run cells here no verdict changed.

## Provenance caveats

- Every rationale, thread and pair is hand-written by one author, in desk English.
  Rates are directions, not measurements of desk text.
- The arena comments are one workflow, one prompt (step 3 asks for a root-cause
  summary), written by models under evaluation.
- The server session is one session, one model, one run: reachability, not rate.

# Evidence — "How does Jev boost our OTC trading agent?" (2026-09-21)

Raw per-call results and the scripts that produced them, for
[`../../2026-09-21-jev-system-one.md`](../../2026-09-21-jev-system-one.md). Not
published: the blog publishes only what `deploy/posts.yaml` lists.

All runs: desk commit `bc2d2a8`, `typesafe/jev-1.13` via ZenMux, 2026-09-21.
Every script drives the **merged** code path (`ToolGuardMiddleware.after_model`,
`memory.keep_alive.score_pending`, `confirmations.family_check.check_family`, the
chat HTTP API); none builds its own Jev request.

| Result file | Script | Calls | Feeds |
|---|---|:--:|---|
| `guard_results.json` | `guard_smoke.py 3` | 36 | matched-pairs table |
| `reword_results.json` | `reword_smoke.py 3` | 72 | shipped-vs-revised wording + holdout |
| `keepalive_shipped.json` | `keepalive_smoke.py 3` | 33 | memory table |
| `keepalive_sibling_ages.json` | `keepalive_ages.py 3 A` | 33 | "+ sibling ages" column |
| `keepalive_sibling_ages_newer.json` | `keepalive_ages.py 3 B` | 33 | "+ newer in level 0" column |
| `family_results.json` | `family_smoke.py 3` | 24 | family table (5 structural rows made no call) |
| `e2e_results.json` | `e2e_seed.py`, `e2e_drive.py` against a server, then `e2e_export.py` | 2 | live AUTO turn table |

Plus one connectivity probe: 234 live calls in total, 2 timeouts.

`e2e_results.json` is exported from the session's database by `e2e_export.py`
(guard verdicts with `user_request_source`, audit rows with the model, the
step-8 `cancel_lifecycle_event` proposal, and the cashflows afterwards). The
drive script's own audit fetch used a wrong path (`/api/audit`, since fixed to
`/api/audit/actions`), and the guard-verdict API omits `user_request_source`.

These numbers in the post come from the pre-build evaluation of Jev, not from
this folder: the generic-predicate probe (0.49–0.52 vs 0.61–0.66), the policy
predicate's 0.61 separation, the latency of a ZenMux request that runs no model,
and TypeSafe's quoted 70–500 ms.

## Re-running

Never against the live database. From the repo root:

```bash
export OPEN_OTC_ENV_FILE=              # no dotenv: nothing from .env leaks in
export ZENMUX_API_KEY=...              # Jev
export OPEN_OTC_SYSTEM_ONE=true OPEN_OTC_TOOL_GUARD=shadow OPEN_OTC_TRACING=off
export OPEN_OTC_DATABASE_URL="sqlite+pysqlite:////tmp/jev-smoke.sqlite3"   # a scratch file
export OPEN_OTC_ARTIFACT_DIR=/tmp/jev-smoke-artifacts
export PYTHONPATH="$PWD/backend"
cd docs/arena/evidence/2026-09-21-jev-system-one
../../../../.venv/bin/python guard_smoke.py 3
```

- Use a **fresh** scratch DB per keep-alive run (each seeds its eleven facts);
  both keep-alive scripts write `keepalive_results.json`, renamed here per arm.
- The live session additionally needs `DEEPSEEK_API_KEY`,
  `AGENT_CHANNELS_FILE=<repo>/config/agent_channels.yaml`,
  `AGENT_CHECKPOINT_DB_PATH=<scratch>` and `OPEN_OTC_MEMORY=off`; seed with
  `e2e_seed.py`, start `uvicorn app.main:app --port 8766` with the same env, then
  run `e2e_drive.py`.
- Jev is not deterministic (±0.10 by design); expect the probabilities to move
  in the second decimal and the verdicts not to.

## Provenance caveats

- Every guard case, memory fact and near-miss family is hand-written. Rates are
  directions, not measurements of desk traffic.
- The revised guard wording in `reword_smoke.py` was written after seeing the
  P1/P2/P6 false alarms. H1–H3 were written in the same sitting before either
  wording ran on them. The revision is **not** shipped.

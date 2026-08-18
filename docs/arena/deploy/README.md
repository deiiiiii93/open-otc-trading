# Publishing arena reports

The public site is <https://www.artena.one/arena/>. It is generated from
`posts.yaml` — nothing on it is hand-maintained, so it cannot go stale.

## Publish a report

1. Author `docs/arena/<date>-<slug>.md` (optionally a `.charts.json` sidecar).
2. Add an entry to `posts.yaml`. `title` and `blurb` are editorial and required —
   a missing `blurb` fails the build rather than shipping generated filler.
3. `./deploy.sh build && ./deploy.sh preview` — read it at <http://localhost:8080>.
4. `./deploy.sh publish` — rsync, then verify the live site.

`./deploy.sh status` answers "what have I written but not published?".

## Publish a board

The **Leaderboard** page is one section per golden workflow, listing the boards
run on it. Scores are never typed: `boards.yaml` names which runs are boards, and
`./deploy.sh boards` derives every number from the arena database through
`store.leaderboard` — the same ranking kernel the desk UI uses.

1. Add `{run, workflow}` to `boards.yaml` (optionally `label`, `post`).
2. `./deploy.sh boards` — re-exports `boards.json` and prints what it found.
3. `./deploy.sh build` as usual.

A run is not a board: the database holds one-model smokes and A/B probes on the
same workflows, so only the runs listed reach the page.

## How it works

`render_report.py` owns markdown → HTML. The PDF renders from the un-chromed
document, so print output cannot drift when the site design changes; the web page
wraps the same body in blog chrome.

`/arena/` is a plain directory on the server (`/opt/open-slides-zero/runtime/arena/`)
behind an nginx `alias`. Publishing is an rsync — it does not rebuild the
open-slides-zero SPA.

## Gotchas

- **`rsync --delete` is live.** Anything on the server that no build produces is
  removed. Passthrough files belong in `static/` so they ship every time — that is
  why the two `model-ability-card-bg-*.webp` files are tracked here.
- **A 200 proves nothing.** Before the alias existed, the SPA answered 200 for
  every path under `/arena/`. Verification asserts on body content and requires a
  404 on a path that cannot exist.
- **`docs/arena/cards/` is gitignored,** so a clean checkout publishes no card
  images. The build warns and continues; that is expected, not a failure.
- **Standings bars use a fixed 0–99 axis**, not the leader's score. Scaling to the
  leader makes every bar near-full-width in a tight field (Run #104 is 80 vs 79)
  and they stop reading as measurements.
- **An nginx config edit needs `--force-recreate`.** `default.conf` is a
  single-file bind mount and the deploy ships it with `tar`, which makes a new
  inode; the running container's mount still points at the old one. `up -d`
  reports "Running" and `nginx -s reload` re-reads the stale inode, so the change
  appears to apply and does not. Compare `stat -c %i` inside and outside the
  container before doubting the config.
- **Readership counters are a build-time snapshot, not live.** `deploy.sh stats`
  refreshes `stats.json`; `build` embeds whatever it finds. If the snapshot is
  missing or older than 14 days the counters render **nothing** — never zeros,
  because "0 views" that means "not measured" is a lie the page cannot walk back.
  Run `stats` immediately before a `publish`, or you ship yesterday's numbers.
- **Counts exclude bots and non-200/304 responses**, and the filtered count is
  shown in the rail. Raw counts on a low-traffic site are mostly crawlers — note
  `curl` is treated as a bot, so your own probes never inflate the numbers.
- **`boards.json` is tracked; `stats.json` is not.** Both are derived, but the
  arena database lives under the gitignored `data/`, so a checkout without it
  could not rebuild the leaderboard — and the export would silently degrade to no
  page. Tracking it also makes a change in the published scores a reviewable diff.
- **The export refuses a multi-workflow run.** `store.leaderboard` aggregates a
  run's matches into one row per contestant with no workflow filter, so runs #104
  and #110 (several workflows each) would publish a cross-workflow average under
  a single workflow's heading. `boards.yaml` declares the workflow and the export
  asserts it against the database.
- **The check count comes from each board's own stored breakdown**, never from
  today's manifest. Run #101 exports as 39 checks although
  `risk-limit-breach-day` is a 38-check workflow now — the 39th was removed
  *because* that board showed it was unroutable. Re-deriving would relabel a
  historical board with an instrument that did not exist when it ran.
- **`matches` and `trials` are different numbers.** The runner folds a
  contestant's trials into ONE aggregate match, so a 2-trial contestant has
  `match_count` 1. Publishing match counts as trials would report every board as
  single-trial — and CON, which exists only because trials disperse, would look
  like it came from one sample.
- **Card-era boards only.** Runs #8 and #9 are deliberately absent: their reports
  ranked models on a blended objective+judge score that the 2026-07-05 reform
  retired, so re-deriving them on today's objective axis reorders their own
  published podium.
- **Rollback** is removing the `location /arena/` block in open-slides-zero and
  redeploying nginx. `frontend/public/arena/` is deliberately still there.

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
- **Rollback** is removing the `location /arena/` block in open-slides-zero and
  redeploying nginx. `frontend/public/arena/` is deliberately still there.

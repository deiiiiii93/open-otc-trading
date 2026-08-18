# Arena readership counters from nginx access logs

**Date:** 2026-08-18
**Status:** design, approved for planning
**Touches:** `docs/arena/deploy/{stats.py,build.py,site_builder.py,deploy.sh,theme.css}`,
`docs/arena/deploy/server/{arena-location.conf,patch_osz.py}`, `.gitignore`,
`tests/test_arena_deploy_stats.py`;
one cross-repo change in `open-slides-zero` (`compose.prod.yml`).
**Follows:** `2026-08-18-arena-blog-deploy-design.md`

## Problem

`https://www.artena.one/arena/` publishes nine reports and there is no way to
know whether anyone reads them. The standing constraint on this work is reach,
not output, so "did outreach land, and where did readers come from" is the
question that decides what to do next — and it is currently unanswerable.

Nothing is being collected today. The nginx image symlinks
`access.log -> /dev/stdout`, so request lines go to Docker's log driver and are
destroyed whenever the container is recreated — which every deploy does. Two
recreates during the 2026-08-18 cutover already wiped what existed.

## Constraints discovered

- **`script-src 'self'` blocks third-party analytics JS.** Plausible/Umami's
  standard snippet cannot run without a CSP change in `open-slides-zero`.
- **`img-src 'self' data: blob: https:` permits any third-party image**, so a
  pixel-based tracker would work untouched. Rejected anyway (see Decisions).
- **A JS or pixel approach only fires on HTML pages.** Reports are also published
  as PDF and Markdown; "someone downloaded the PDF" is a real engagement signal
  for a research site, and only server-side logging sees it.
- **The index is a static file built locally**, so any number shown on it is a
  build-time snapshot, never live.
- **Raw logs contain IPs and user agents.** Whatever is published must be a
  derived aggregate, never the log or a full report rendered from it.

## Decisions

| Axis | Choice | Why |
|---|---|---|
| Collection | nginx access log, scoped to the `/arena/` location | no JS, no third party, no CSP change; sees PDF/Markdown downloads and referrers |
| Aggregation | ~60 lines of Python in this repo | deterministic and unit-testable, and these numbers go on a public page; no package on the server, no new local dependency |
| Surfacing | public counters on the index | readership is a credibility signal — with the honesty guards below |
| State | stateless — recomputed from logs every run | `stats.json` is derived, never load-bearing; losing it costs nothing |

**GoAccess was considered and dropped.** It is an interactive report generator
and we need six numbers per post. A local parser avoids a package on the server
and makes aggregation testable. If a rich private report is wanted later it can
be added beside this without changing the pipeline.

## Architecture

```
nginx  ──>  /var/log/nginx/arena.log     (volume, on the server)
              │
   deploy.sh stats  ──ssh cat──>  parse + filter + aggregate  (local)
              │
              └──>  docs/arena/deploy/stats.json   (gitignored snapshot)
                          │
                    deploy.sh build  ──>  index rail + per-post counts
                          │
                    deploy.sh publish  ──>  live
```

Nothing runs on a schedule; nothing new is installed on the server.

### Collection

`access_log /var/log/nginx/arena.log;` inside the managed `location /arena/`
block, plus `- ./runtime/nginx-logs:/var/log/nginx` on the nginx service.
Mounting the **directory** is what makes file logging work at all: it replaces
the image's `access.log -> /dev/stdout` symlink with a real directory.

Scoping the log to the arena location, rather than server-wide, keeps the file
small and means no request data about the rest of the site is collected.

`access_log` is safe inside this block — the inheritance-replacement rule that
makes `add_header` dangerous here is specific to `add_header`.

Format is nginx's built-in `combined`; no `log_format` directive is declared, so
the parser targets exactly:

```
$remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent "$http_referer" "$http_user_agent"
```

### Aggregation (`stats.py`)

Pure functions over log lines — no IO, no network, so every rule is testable:

- `parse_line(line) -> LogLine | None` — `None` for malformed lines, which are
  counted, never silently dropped.
- `is_bot(user_agent) -> bool` — substring match against a vendored crawler list.
- `classify(path) -> ("page"|"pdf"|"markdown"|"asset"|"other", stem)` —
  `/arena/` and `/arena/index.html` collapse to one `index` bucket.
- `referrer_host(referer) -> str | None` — host only; self-referrals dropped.
- `aggregate(lines) -> Stats` — per-stem page views and downloads, referrer
  tallies, `first_seen`/`last_seen`, and the counts of bot and malformed lines.

Only `status` 200 and 304 count. Rotation is handled by size: `deploy.sh stats`
rotates `arena.log` on the server when it exceeds **50 MB** (`mv` + `nginx -s
reopen`), keeping **four** generations, and always parses `arena.log*` together —
so aggregation stays stateless and fully recomputable. At this site's traffic
that threshold will rarely trigger; it exists so the volume cannot grow
unbounded, not as routine maintenance.

If `arena.log` does not exist yet — the mount has not been deployed, or nothing
has been requested since — `stats` reports that plainly and writes no
`stats.json`, rather than writing an all-zero one.

### Surfacing

A `READERSHIP` card in the existing right rail (beside Standings and Tags) with
total views, downloads, and the **top 5** referrer hosts; plus a per-post read
count in each feed entry. The per-post number is **page views only** — downloads
are a separate total in the rail, because conflating them would let one PDF fetch
read as a page view. Both derive from `stats.json` at build time.

## Honesty guards

Public numbers create three distinct ways to mislead. Each gets an explicit rule,
following the `empty` vs `unavailable` discipline this repo already applies to
reports:

- **Bots.** Counts exclude known crawler user-agents and non-200/304 responses.
  The filtered fraction is recorded in `stats.json` and shown in the rail, so the
  bot ratio is visible rather than hidden. Unfiltered counts on a low-traffic site
  are mostly crawlers and would be a fabrication.
- **Staleness.** The index is static, so counters are a build-time snapshot. The
  rail is labelled `as of <date>`, where the date is when `stats` last ran — NOT
  `last_seen`, which is merely the newest request observed and would overstate
  freshness whenever a crawler hit the site after the last pull.
- **Absence.** If `stats.json` is missing, unparseable, or older than
  `STATS_MAX_AGE_DAYS` (14), the rail and per-post counts render **nothing** —
  never zeros. "0 views" that actually means "not measured" is precisely the
  failure mode the report module's `empty`/`unavailable` split exists to prevent.

**History starts at deployment.** The first counters will cover hours. Publishing
should wait for a week of real traffic; the build renders no counters until
`stats.json` exists, so this is the default rather than a discipline.

## Privacy

- The raw log never leaves the server except into a local scratch file, which is
  not committed.
- `stats.json` contains only path→count, referrer-host→count, timestamps and
  filter counts. No IPs, no user agents, no full referrer URLs.
- No unique-visitor estimation: it requires IP hashing, which is more privacy
  exposure than a read count justifies.

## Non-goals

- Unique visitors, sessions, geography, live counters.
- A scheduled job. `stats` is run on demand, before a publish.
- Analytics for anything outside `/arena/`.

## Testing

`stats.py` is pure, so tests are table-driven over real `combined`-format lines:
bot filtering, status filtering, path classification (page vs pdf vs md vs
asset), index collapsing, self-referral exclusion, malformed-line counting, and
the staleness/absence rules in the renderer. Rendering tests assert that a
missing or stale `stats.json` produces **no** counter markup at all — the guard
that matters most.

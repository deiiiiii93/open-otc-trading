# Arena report publishing: a generated blog at artena.one/arena/

**Date:** 2026-08-18
**Status:** design, approved for planning
**Touches:** `docs/arena/deploy/**` (new), `docs/arena/render_report.py` (refactor,
output-compatible), `.gitignore`; one-time cross-repo change in
`open-slides-zero` (`compose.prod.yml`, `deploy/nginx/default.conf`).

## Problem

`https://www.artena.one/arena/` is the public face of the Arena — the repo README
points readers at it. It is stale and cannot be updated without hand work.

Publishing a report is currently a five-step manual ritual with no script:

| Step | What actually happens |
|---|---|
| 1 | author `docs/arena/<date>-<slug>.md` (+ optional `.charts.json`) |
| 2 | `render_report.py` → self-contained `.html` + `.pdf` via headless Chrome |
| 3 | hand-copy `.md`/`.html`/`.pdf`/`.charts.json` into `open-slides-zero/frontend/public/arena/` |
| 4 | hand-edit `public/arena/index.html`: a new `<article class="report-card">`, and re-type the hero metrics and three leaderboard cards |
| 5 | `open-slides-zero/scripts/deploy_incremental.sh --service frontend` → `npm ci` + Vite build + container rebuild on the server |

Three consequences, all observed:

- **The index is frozen at Run #94 (2026-07-29).** Run #104 was rendered locally on
  2026-08-17 and never published. Step 4 is the reason: adding a report means
  re-authoring hand-typed standings, including bar widths as literal
  `style="width: 97%"`.
- **The page cannot represent the current corpus.** Runs #8–#104 are model boards,
  but the two newest pieces are research memos (a trap-step failure analysis, a
  reasoning-effort ladder study). A "Published Reports" grid of run cards
  mis-frames them.
- **Publishing a markdown file rebuilds a Node SPA.** The unit of work is wrong,
  and it grows: `public/arena/` already carries ~2.8 MB of PDFs into the Docker
  build context.

Separately, report pages are orphans: `render_report.py` emits a standalone serif
document with no header, no navigation, and no link back to `/arena/`.

## Findings that constrain the design

- **`/arena/` returns 200 for every path.** The frontend container's nginx does
  `try_files $uri $uri/ /index.html`, so HTTP status cannot tell a real page from
  the SPA catch-all. Verification must assert on **content**, not status.
- **The server holds files the local mirror does not.**
  `/opt/open-slides-zero/frontend/public/arena/` contains
  `model-ability-card-bg-v1.webp` and `-v2.webp`, live and serving `image/webp`,
  referenced from **nowhere** in either repo — hand-uploaded so the GPT-Image-2
  ability-card run could fetch a background by public URL. A cutover seeded from
  git would silently 404 them. **Seed from the server.**
- **`render_report.py` cannot be imported.** It reads `sys.argv` and writes files
  at module scope; importing it renders a report as a side effect.
- **`docs/arena/cards/` is gitignored** ("re-renderable from the board data"), so
  Run #104's card PNGs exist only on this machine. Asset publishing reads the
  working tree, and a clean checkout will legitimately have nothing to publish.
- **Run #104's markdown links `](cards/run104/)`** — a bare directory, which
  returns 404 under `try_files … =404`.
- Server has `/usr/bin/rsync`; `runtime/` already hosts `threads`, `certbot`,
  `letsencrypt`, so `runtime/arena` is a sibling in an established pattern. The
  edge CSP permits inline `<style>` (`style-src 'self' 'unsafe-inline'`).

## Decisions

| Axis | Choice | Why |
|---|---|---|
| Transport | rsync to a static dir behind an nginx `alias` | seconds per publish, no SPA rebuild, PDFs leave the image |
| Index layout | blog feed + generated standings rail | rail is derived, so it cannot freeze at Run #94 again |
| Post pages | blog chrome | a reader arriving from search can navigate into the site |
| Taxonomy | flat reverse-chronological feed, tagged | absorbs boards and research memos in one timeline |

## Architecture

### Layout

```
docs/arena/deploy/
├── README.md               how to publish
├── posts.yaml              THE MANIFEST — single source of truth for the feed
├── deploy.sh               build | preview | publish | bootstrap | status
├── site.py                 markdown → blog pages (index, about, post chrome)
├── publish.py              build dir → server, then verify
├── theme.css               blog identity, inlined into every page
├── templates/              index / about / post-chrome fragments
├── server/
│   ├── arena-location.conf the one-time nginx block
│   └── bootstrap.sh        patches open-slides-zero + seeds from the server
└── build/                  generated site (gitignored)
```

### Commands

| Command | Effect | Touches prod |
|---|---|---|
| `build` | render every `publish: true` post → `build/`; regenerates a `.pdf` via `render_report.py` only when missing or older than its markdown | no |
| `preview` | serve `build/` on localhost:8080 | no |
| `publish [--dry-run]` | `rsync -az --delete` → server, then verify; `--dry-run` passes `-n` to rsync and prints the file delta without transferring | yes |
| `bootstrap` | one-time cutover (seed, patch nginx + compose, deploy) | yes |
| `status` | manifest vs live — catches the Run #104 gap class | read-only |

### `posts.yaml`

Everything rendered on the index is generated from this file.

```yaml
- file: 2026-08-18-run110-luna-reasoning-effort.md
  date: 2026-08-18
  tags: [research]
  title: "Does reasoning_effort improve agent performance?"
  blurb: >
    Effort is a step at `low`, not a dial: none → low buys +4.9 points
    and costs less; low → max buys +1.0 for 3.3× the wall-clock.
  publish: true

- file: 2026-08-13-run104-otc-desk-agent-arena.md
  date: 2026-08-13
  tags: [board]
  run: "Run #104"
  chips: ["2 models", "4 workflows"]
  title: "Grok 4.6 edges DeepSeek V4 Pro — for opposite reasons"
  blurb: >
    A one-point tie: Grok leads the objective axis and wins three of four
    workflows, but posts EFF 12 — the inversion is deeper, not fixed.
  standings:                    # drives the rail; omit on non-board posts
    - {rank: 1, model: "Grok 4.6",        score: 80, note: "obj 96.3, EFF 12"}
    - {rank: 2, model: "DeepSeek V4 Pro", score: 79, note: "100.0 in 22 calls"}
  assets: [cards/run104]
  publish: true
```

**Derived, never typed:** reading time (words ÷ 200), tag counts, prev/next
links, feed order, and the standings rail (newest post carrying `standings`).

**Initial manifest** carries nine entries: the six existing boards (#8, #9, #20,
#33, #94, #104 — publishing #104 closes the standing gap), the two research memos
this work was requested for, and the Run #110 pre-registration plan.

**Manifest is authoritative for publication.** A markdown file absent from
`posts.yaml`, or carrying `publish: false`, is not published — reports are not
discovered by globbing `docs/arena/`, which holds plans and drafts.

### Site generation

`render_report.py` is **split, not rewritten**: `render_markdown(src) →
(body_html, run_label)` becomes importable, `main()` keeps writing today's
`.html` + `.pdf`. CLI behaviour and output bytes are unchanged, so the six
existing PDFs and the print stylesheet are untouched.

- **PDF path** renders the un-chromed document — print fidelity is protected by
  construction, not by care.
- **Web path** (`site.py`) imports the same function and wraps the body in blog
  chrome: breadcrumb header, byline (`2026-08-18 · research · 8 min read`), and a
  footer with ← prev / next → plus Markdown and PDF links.

The index is masthead + tagline + flat feed with tag chips, and a right rail
carrying live standings and tag counts. Method and contact move to
`/arena/about.html`. Palette stays the established warm paper (`--bg:#f7f4ee`,
`--accent:#9f351f`) — a re-layout, not a rebrand.

Card directories named in `assets:` get a generated contact-sheet `index.html`,
so `](cards/run104/)` resolves instead of 404ing.

### Transport and cutover

One-time change in `open-slides-zero`:

```nginx
location /arena/ { alias /var/www/arena/; index index.html; try_files $uri $uri/ =404; }
```

plus `- ./runtime/arena:/var/www/arena:ro` on the nginx service, mirroring the
existing `runtime/threads` mount.

`bootstrap` order matters:

1. seed `runtime/arena/` **from the server's** `frontend/public/arena/` (carries
   the two orphan `.webp`)
2. `rsync` the generated site over it
3. patch `compose.prod.yml` + `deploy/nginx/default.conf`, deploy the nginx service
4. verify

nginx prefix matching makes step 3 the atomic switch: `location /arena/` is longer
than `location /`, so it wins on reload. **`frontend/public/arena/` is deliberately
left in place** — deleting the location block reverts to it, so rollback needs no
restore step.

### Verification

`publish` is not complete until it has checked the live site, because the SPA
catch-all makes status codes meaningless:

- each published URL returns 200 **and** its body contains the post title
- `/arena/` contains every `publish: true` title
- the two `.webp` URLs still return `image/webp`
- a known-absent path under `/arena/` returns **404**, proving the alias is
  serving and not the SPA fallback

## Non-goals

- Regenerating `docs/arena/README.md` from the manifest — a second consumer, later.
- Tag filter pages, RSS, search, pagination.
- Changing how reports are authored or scored.

## Open items for implementation

- **Missing-`blurb` policy — default: fail loud.** `build` refuses and names the
  offending entry rather than silently deriving a blurb from the first paragraph,
  matching this repo's fail-closed posture on ambiguity (`empty` vs `unavailable`,
  the bookable-underlying gate). Flagged here because the opposite choice —
  degrade gracefully so a publish is never blocked on editorial copy — is
  defensible, and it is a judgement about how you want to work, not about the code.
- Content flags, resolved at manifest time: the trap-step memo names an internal
  branch and unmerged patches; `2026-08-17-luna-reasoning-effort-plan.md` is the
  pre-registration for Run #110 and defaults to `publish: true`, which makes the
  report's "predeclared before launch" claim verifiable.

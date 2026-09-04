# Arena report publishing (the artena.one blog) — agent guidance

Read this before touching anything under `docs/arena/deploy/`. Repo-wide rules
(migrations, test hermeticity) and the arena scoring kernel this site *reads* live
in the root [`CLAUDE.md`](../../../CLAUDE.md) — that file stays the authority on
what a board, a card and OVR actually mean.

`https://www.artena.one/arena/` is generated from a manifest and shipped by rsync.
Package: `docs/arena/deploy/` — `posts.yaml` (the manifest), `manifest.py` (loader
+ validation), `site_builder.py` (pure HTML), `build.py` (orchestration),
`publish.py` (transport + live verification), `theme.css`, `static/` (tracked
passthrough assets), `server/` (nginx block, `patch_osz.py`, `bootstrap.sh`).
Entry point: `docs/arena/deploy/deploy.sh build | preview | publish | status |
boards | stats | bootstrap`.

## The manifest is authoritative

A markdown file absent from `posts.yaml`, or carrying `publish: false`, is not
published — reports are **not** discovered by globbing `docs/arena/`, which also
holds plans and drafts. `title` and `blurb` are editorial and **required**: a
missing `blurb` fails the build rather than deriving one from the first paragraph,
which for a research memo is a provenance line, not a headline. Reading time, tag
counts, prev/next and the standings rail are all derived, so the index cannot
freeze the way the hand-typed Run #94 leaderboards did (that is why the site sat
at Run #94 while Run #104 was rendered and never shipped).

## The leaderboard is derived; only the curation is editorial

`/arena/leaderboard.html` is one section per golden workflow, named by its slug,
listing the boards run on it. Two inputs, deliberately separate:

- **`boards.yaml`** says which runs *are* boards. A run is not a board — the arena
  DB holds one-model smokes and A/B probes on the same workflows as the real
  fields — and only a human can draw that line.
- **`boards.json`** holds every number, exported by `deploy.sh boards`
  (`collect_boards.py`) from the arena DB through **`store.leaderboard`**, the same
  ranking kernel the desk UI uses. Nothing is hand-typed, so the page cannot
  disagree with the app. `site_builder` stays pure stdlib and only renders it — the
  backend import lives in the export step, exactly as the server round-trip lives
  in `collect_stats.py`.

- **The export refuses a run that spans more than its declared workflow.**
  `store.leaderboard` aggregates a run's matches into one row per contestant with
  **no workflow filter**, so runs #104 and #110 (4 and 5 workflows) would publish a
  cross-workflow average under a single workflow's heading. `boards.yaml` declares
  the workflow and `shape_board` asserts it against the DB.
- **`matches` and `trials` are different numbers.** The runner folds a contestant's
  trials into ONE aggregate match, so a 2-trial contestant has `match_count` 1.
  Publishing match counts as trials reports every multi-trial board as
  single-trial — and CON, which exists only because trials disperse, then looks
  like it came from one sample. The per-contestant depth comes from
  `score_breakdown.n_trials`; a board publishes a common depth only when every
  contestant agrees (Run #94 does not — `gemini-3-5-flash` ran 1 trial, and its
  row honestly shows CON `—`).
- **The check count is read from each board's OWN stored breakdown**, never
  re-derived from today's manifest. Run #101 exports as **39** checks even though
  `risk-limit-breach-day` is a 38-check workflow now — the 39th was deleted
  *because* that very board proved it unroutable. Re-deriving would relabel a
  historical board with an instrument that did not exist when it ran, the same
  comparability error the page's "boards are never merged across runs" rule exists
  to prevent.
- **Card-era boards only.** Runs #8/#9 are excluded on purpose: their reports
  ranked models on the blended objective+judge score that the 2026-07-05 reform
  retired, so re-deriving them on today's objective axis **reorders their own
  published podium** (Run #8's report headlines an Opus 4.8 / GPT-5.5 tie; the
  objective axis puts Sonnet 4.6 first). A leaderboard that contradicts the report
  it links to is worse than one that omits it.
- **`boards.json` is TRACKED; `stats.json` is not.** Both are derived, but the
  arena DB lives under the gitignored `data/`, so a checkout without it could not
  rebuild the leaderboard — and the absence rule would silently degrade it to no
  page. Tracking also makes a change in published scores a reviewable diff.
- **Absence reaches the chrome.** No `boards.json` ⇒ no page **and no nav link**:
  a masthead entry for a page the build did not produce would 404 sitewide. A
  workflow with no board keeps its section and says so, because omitting it would
  read as "this workflow does not exist" rather than "nobody has run it".
- **Averaging CARDS is sound; merging BOARDS is not** — the rule that lets
  `/arena/models.html` exist beside a leaderboard that refuses to merge runs. A
  card is an ABSOLUTE measurement (`passed/total` per axis, EFF against that
  workflow's own par), so it does not depend on who else was in the field; a
  leaderboard position does. The Run #110 report takes the same mean across five
  workflows. What a mean still hides is the spread, so `consolidated_cards`
  publishes `ovr_min`/`ovr_max`, the coverage (`3 of 4 boards`), and a per-board
  entry for EVERY board — an uncontested one rendering an em dash, because a
  short list reads as "ranked low" rather than "was not in that field"
  (`gemini-3-6-flash` is absent from Run #20 because it did not exist yet).
- **The archetype comes from `scoring._card_position`**, imported by the exporter
  rather than reimplemented in the deploy package. It is private, but it is the
  single definition of the Sniper/Anchor/Playmaker/All-rounder rule; a copy is how
  a published label silently drifts from the desk's. A move breaks the export
  loudly instead.
- **Escape the parts, not the joined string.** `escape(" &middot; ".join(facts))`
  yields `&amp;middot;`, which renders as literal `&middot;` text — and the
  stylesheet uppercases it to `&MIDDOT;`. Only the live render caught it.
- `verify_live` reads the workflow anchors out of the **built** page and requires
  each to be served, for the same reason it compares titles — a 200 under the SPA
  catch-all proves nothing.

## A run may be published as CARDS ONLY (`provisional:`)

`boards.yaml` has two sections. `boards:` are ranked and reach the leaderboard;
`provisional:` are published as **cards only** and never appear there. The split
exists because **a rank is relative and a card is absolute**: a one-model smoke
has no field, so #1 of 1 measures nothing, but `passed/total` per axis with EFF
against each workflow's own par stays meaningful with no opponent. That is the
same asymmetry that lets `consolidated_cards` average across workflows while the
leaderboard refuses to merge runs.

- A provisional entry declares **no workflow**, unlike a board — its
  measurements are published one per workflow instead of folded into one row, so
  the multi-workflow guard that `shape_board` enforces does not apply.
- **Nothing provisional carries a rank, by construction**, not by blanking one.
  `_provisional_card` omits the place column entirely (`.mcard-boards.no-rank`).
- **Publish the trial depth beside an em-dash CON.** CON needs trials to
  disperse, so a 1-trial run has none; without the depth on the card a reader
  cannot tell "not measured" from "perfectly consistent".
- **A provisional entry may carry a `post`.** The caveat that makes an unranked
  card readable usually lives in the report — Run #104's card exists only
  because DeepSeek replaced the weights behind `deepseek-v4-pro` on 2026-08-13,
  so its OVR 79 and the boards' OVR 87 are two different models under one id,
  not a regression. `render_models` therefore takes `posts`; an unpublished
  reference is dropped with a build warning, as on a board.
- **Publish every arm of an A/B probe, not the interesting one.** Run #104
  publishes Grok 4.6 beside DeepSeek V4 Pro: showing one side of a declared
  pair reports a comparison as if it were a measurement.
- A run declared as **both** a board and provisional is rejected — the two make
  contradictory claims about whether it had a field.
- Export goes through **`store.get_run`**, not `_derive_card` on the raw column:
  `fold_trial_breakdowns` does not lift `diagnosis`, so deriving from a wrapped
  breakdown's top level returns `missing_tool_count` for every match — including
  `trials=1` ones, which are wrapped too.
- The top level of `boards.yaml` may now be a mapping; **a bare list is still
  valid** and means "all boards". When reading its sections, `value or []` is
  wrong — `{}` and `""` are falsy, so a wrong-typed section would silently
  become an empty one and the whole leaderboard would vanish without a word.
  Absent (`None`) is the only legitimate empty.

## Model Cards is a ROSTER plus a page per model

`models.html` was one page carrying every card — 118 of them, 131KB, roughly
8,000px of boxes — and its bottom two-thirds restated per board what
`/arena/leaderboard.html` already publishes as compact tables. It is now a
roster of one row per card, and `models/<id>.html` per model.

- **`model_index(snapshot)` is the ONE enumeration.** The roster reads it to
  decide what to link and `build.py` reads it to decide what to write. Two
  independent walks of the snapshot is exactly how a roster comes to offer a
  404, so never enumerate models a second way.
- **One row per CARD, not per model.** Seven contestants own both a consolidated
  card and provisional arms measured at a different effort. Folding those into
  one row means averaging two conditions into a single stat line — the
  cross-condition merge the arena refuses everywhere else.
- **The roster is ordered by OVR and never numbered.** Those means span
  different sets of boards, so a place in that list would be the cross-workflow
  ranking the leaderboard exists to refuse. Every row therefore also carries its
  spread and coverage: a 93 over three boards is not the same claim as a 90 over
  four, and a bare sorted OVR column invites the reader to treat them as one.
- **The roster names a run by NUMBER, never by its editorial label.** A
  sixty-character headline forces the model column wide enough to push the Range
  column off the end of the scroll box — and Range is what keeps OVR honest. The
  headline has room on the model page.
- **Model pages live in a subdirectory on purpose.** `stats.classify()` buckets
  any nested `.html` as an `asset`, not a `page`, so 27 new URLs cannot inflate
  the published readership totals. That also means every link on a model page
  needs `../`: `_masthead(prefix=UP)`, and `UP` before a report's `html_name`. A
  `./index.html` there resolves to `models/index.html` and 404s on all 27 at once.
- **A model page puts every board card in ONE grid**, captioned by workflow and
  run (`mcard-where`). A section per board left a single 268px card alone in a
  1080px column, once per workflow. Provisional arms share a grid too, with the
  run notes collected beneath it: runs #129 and #130 are the two halves of one
  A/B, and interleaving each card with its own long note put them a screen apart.
- **A provisional-only model keeps its "All workflows" heading and says why it
  is empty.** Same rule as an unmeasured workflow on the leaderboard: six of the
  27 have never contested a board, and a missing section reads as an oversight.
- **A `note:` in `boards.yaml` must not say "above" or "below" about another
  card.** A card now appears on its own page and on nobody else's, so
  cross-references have to name the run. Three notes said "above"/"below" and
  became false the day the split shipped. The one remaining "below" (Run #101)
  is about the table under it on the leaderboard, which is still true.
- **`verify_live` gained `model_pages`, and the old `model_anchors` check would
  otherwise have gone dark** — it scrapes `<section class="wf" id=…>`, the
  roster has only two such sections, and an empty anchor list is read as "page
  not built, valid site". So it would have passed while checking nothing.
  `model_pages()` reads the `models/*.html` hrefs out of the BUILT roster and
  requires each to be served AND to name its own model. The absent probe now
  runs at both depths, because `try_files $uri $uri/ =404` resolves a nested
  path by a different branch than a top-level one.
- **The dead-class guard is only as good as its fixture.**
  `test_every_class_the_cards_pages_emit_has_a_rule_in_the_stylesheet` passed a
  new `.mcard-where` straight through, because the snapshot it rendered had a
  consolidated card for a model that contested no board — so it emitted no board
  card at all. A guard that never renders the markup it protects is not a guard.

## theme.css owns the report body ON THE WEB; render_report.py owns print

`render_markdown()` returns **bare HTML with no stylesheet**. Only
`document_html()` — the standalone `docs/arena/*.html` artifact and the PDF —
attaches `render_report.py`'s CSS. So a blog post page is styled *entirely* by
`theme.css`, and for months it had no rules for anything inside `.post-body`:
9 of ~13 pages rendered at browser defaults while the same report was a typeset
serif document in print. The ASCII-chart figures were invisible outright — a
`.bar` has no intrinsic size, so 117 rows across 10 charts collapsed to nothing.

- The two stylesheets are **kept in sympathy, never copied**. `render_report.py`
  stays authoritative for print, and nothing in `theme.css` can reach it —
  which is what keeps `tests/test_arena_render_report.py`'s byte-identity gate
  over the six committed report HTMLs green through a site redesign.
- **Chart bar hues are semantic** (gold/silver/bronze = podium, good/warn/bad =
  judgment) and are pinned by test to the exact values in `render_report.py`;
  only the neutral track, rules and table fills are warmed to the blog palette.
  Letting a hue drift makes one chart say different things in the two media.
- Markdown tables get `display:block; overflow-x:auto` — the internal table
  layout survives (rows still generate anonymous table boxes) and a wide board
  scrolls in its own box. Markdown emits no wrapper element to use instead.

## The editorial voice: serif argues, sans measures

Serif carries the nameplate, headlines, blurbs and report prose; sans carries
metadata, tables and card stats. `--serif` was declared in `:root` and
referenced **nowhere** for months, so the warm-paper palette promised a journal
while every glyph was system sans — `test_the_serif_token_is_used_and_not_merely_declared`
is the dead-token guard. System fonts only: the stylesheet is inlined and no
font files ship, so a webfont means adding `.woff2` to `static/` plus a
`@font-face`.

- **The nameplate is `full` on the index and `compact` everywhere else.** The
  index has no separate intro band — the title *is* its `<h1>` and the tagline
  rides with it. On an interior page the page's own heading owns the `<h1>`, so
  the nameplate title degrades to a link; repeating the tagline there would
  stack two muted paragraphs above the data and let the site title outweigh the
  page being read.
- The small `ARTENA` eyebrow **inherits the root-site link** (`/`) the old
  `A`-in-a-square brand owned. Folding the title into the header would strand
  it, because the title now points at the arena index.
- **Blurbs render markdown code spans; titles never do.** `publish.verify_live`
  asserts `escape(post.title)` appears verbatim on the served page, so inlining
  a title breaks the deploy verifier rather than the build — a far worse failure
  to diagnose. Escape first, then wrap, so the pattern only sees inert text.
- Boxes were removed from the rail, the board tables and the chips **so that a
  box still means something** where it is kept: the model cards, where the card
  metaphor is the measurement's identity.
- The masthead nav needs ~320px on its own, which leaves nothing for a full
  title on a phone; it stacks below 720px.

## Gotchas

- **A 200 proves nothing under `/arena/`.** The open-slides-zero frontend
  container answers `try_files $uri $uri/ /index.html`, so before the nginx alias
  existed *every* path returned 200 — including nonsense ones. `verify_live`
  therefore reads bodies, compares **HTML-escaped** titles (a raw compare
  false-alarms on any title containing `&` or `<`), and requires a **404** on
  `ABSENT_PROBE`. That 404 is the control that proves the alias is serving rather
  than the SPA.
- **`rsync --delete` is live.** Anything on the server no build produces is
  removed. The two `model-ability-card-bg-*.webp` files existed ONLY on the server
  (hand-uploaded so a GPT-Image-2 card run could fetch a background by public URL)
  and are referenced from nowhere in either repo — they are now tracked in
  `deploy/static/` and copied into every build. `bootstrap.sh` additionally refuses
  to cut over if a dry run reports any deletion.
- **The PDF renders from the UN-chromed document.** `render_report.py` owns
  markdown→HTML; `site_builder` wraps the same body in blog chrome for the web.
  Print output therefore cannot drift when the site design changes — that is
  structural, not a discipline to remember.
- **`render_report.py` must stay importable.** It used to read `sys.argv` and write
  files at module scope, so importing it under pytest rendered the *test file* into
  `tests/test_arena_render_report.{html,pdf}` via headless Chrome, and a bare import
  rewrote `docs/arena/2026-06-27-run8-*.pdf`. `tests/test_arena_render_report.py`
  pins both: byte-identity against all six committed report HTMLs (sha256) and
  import purity by **content hash**, not filename set — an overwrite is invisible to
  a name-set comparison.
- **NEVER use `add_header` in the `/arena/` location.** nginx inherits
  `add_header` from the server level "if and only if there are no add_header
  directives defined on the current level", so ONE `Cache-Control` line there
  discards CSP, HSTS, X-Frame-Options, nosniff, Referrer-Policy AND
  Permissions-Policy for every `/arena/` response. That shipped, and the deploy
  verifier called it healthy because it only checked status and body — it now
  asserts the six headers. Use `expires`, a different directive family that does
  not trigger the replacement rule.
- **A single-file bind mount pins an INODE, not a path.** `compose.prod.yml`
  mounts `deploy/nginx/default.conf` as one file, and `deploy_incremental.sh`
  ships it with `tar -xzf`, which unlinks and recreates it. The running
  container's mount still resolves to the OLD inode, so nginx serves a file that
  no longer exists on disk — `up -d` prints "Running" (the service definition is
  unchanged), `nginx -t` passes against the correct file, and `nginx -s reload`
  faithfully re-reads the stale inode. Only `up -d --force-recreate nginx`
  re-resolves it. **When a bind-mounted config edit does not take, compare
  `stat -c %i` inside and outside the container before doubting the config.**
- **open-slides-zero gitignores its own deployment files** (`compose.prod.yml`,
  `deploy/`, `scripts/` — "local deployment packaging"). So there is nothing to
  commit there, `git checkout` is not a revert path, and `patch_osz.py` must be
  able to REPLACE an existing block rather than only insert one. The real
  rollback is the server-side `default.conf.pre-arena` backup bootstrap makes.
- **Readership counters are derived, snapshot-based, and fail to NOTHING.**
  `stats.py` is pure and stdlib-only (GoAccess was considered and dropped so the
  published numbers stay unit-testable). Missing / unparseable / older than
  `STATS_MAX_AGE_DAYS` (14) ⇒ no counter markup, never zeros — the `empty` vs
  `unavailable` rule applied to a public page. `as of <date>` is when `stats` last
  ran, NOT `last_seen`, which a single crawler hit would make look fresh.
  Aggregation is stateless: every run re-reads `arena.log*` including rotated
  generations, so `stats.json` is pure derived data and losing it costs nothing.
  Per-post counts are page views only — downloads are a separate rail total, so
  one PDF fetch cannot read as a page view.
- **Standings bars use a fixed 0–99 axis** (`RAIL_AXIS_MAX`), not the leader's
  score. Scaling to the leader renders every bar near-full-width in a tight field
  (Run #104 is 80 vs 79) so they stop reading as measurements.
- **`docs/arena/cards/` is gitignored,** so a clean checkout publishes no card
  images; the build warns and continues. That is expected, not a failure.
- **BUILD FROM MAIN, NEVER FROM A WORKTREE, WHEN THE NEXT STEP IS PUBLISH.** The
  two facts above are separately harmless and jointly destructive: a worktree has
  no `docs/arena/cards/` (gitignored), so it builds without the card images, and
  `rsync --delete` then strips them off the live server. The build says only
  `asset directory missing, skipped` — a warning that reads like the documented
  clean-checkout case. `deploy.sh build` from main reports `1 asset dirs`; a
  worktree reports `0`. Always read `publish --dry-run` for `deleting ` lines
  before shipping: a healthy publish has none.
- **Run `deploy.sh stats` before `deploy.sh build`.** `stats.json` is untracked
  and expires at `STATS_MAX_AGE_DAYS` (14), and the absence rule means a stale
  snapshot silently publishes an index with NO readership counters rather than
  failing. Nothing in the build or the verifier flags it, because "no counters"
  is a legitimate site.
- **Rollback is removing the `location /arena/` block** in open-slides-zero and
  redeploying nginx. `frontend/public/arena/` is deliberately retained, so the
  previous site is still there and no data restore is involved.
- **`site_builder.py` is NOT named `site.py`** — `site` is a stdlib module, and the
  file's directory reaches `sys.path`. There is no `templates/` dir either:
  `jinja2` is not installed, so templates are f-string functions.

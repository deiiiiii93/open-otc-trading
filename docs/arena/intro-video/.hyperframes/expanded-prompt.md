# Artena — site intro video · production breakdown

An intro for https://www.artena.one/arena/ — "The OTC Desk Agent Arena". The video
reads like the site: a warm-paper editorial journal whose front page composes
itself. Serif argues, sans measures, mono annotates. 29.5s · 1920×1080 · 30fps.

## Style block (from `docs/arena/deploy/theme.css` — authoritative)

- Canvas `#f7f4ee` (warm paper) · panels `#fffdf8` · ink `#191612` · muted `#60584d`
- Structure: line `#d7cdbf`, rule `#ebe3d6`
- Accent brick `#9f351f` (the argument) · accent teal `#1f5f68` (the evidence tag)
- Podium bar hues (semantic, pinned by the site): gold `#d9b13f→#caa12e`,
  silver `#aab0b6→#9aa0a6`, bronze `#c07c4a→#b06a3b`
- Type roles: **Newsreader** (serif — titles, claims, straps; the Charter/Georgia
  register of the site), **Archivo** (sans — labels, stats, table rows; the
  system-sans register), **IBM Plex Mono** (metadata, provenance, coordinates).
- Light canvas ⇒ video adaptation: 2.5–3px rules, decorative opacity 8–20%,
  full-saturation accent hits, SVG paper grain over everything.

## Rhythm

**calm-open → build → PEAK(card) → cascade(board) → resolve(CTA)**
Primary transition: push slide left (editorial page-turn, 0.55s power3.inOut) at
S2→S3 and S3→S4. Accents: blur crossfade in (S1→S2, 0.7s / 12px) and out
(S4→S5, 0.8s — wind-down). Final scene only may fade out.

## Scenes

### S1 · Nameplate (0–6.2s) — "the masthead is set"
A broadsheet nameplate composing itself, like the site's own index header.
BG: ghost serif "A" ~1000px at 6% drifting right-of-frame; two ledger hairlines;
warm brick radial glow. MG: eyebrow ARTENA TRACKS IN (letter-spacing collapse),
red rule STAMPS (scaleX expo.out), title "The OTC Desk / Agent Arena" RISES
line-by-line from clip wrappers, strap (the real tagline) FLOATS up, faux nav
(Blog · Leaderboard · Model Cards · About) fades top-right — the actual masthead.
FG: top/bottom rules DRAW, corner registration marks, mono corners
"ARTENA · EST. 2026" / "WWW.ARTENA.ONE/ARENA", chip row
"CONTROLLED · REPEATED TRIALS · NO HUMAN IN THE LOOP" TICKS in.
Ambient: ghost glyph drift + glow breath. → blur crossfade.

### S2 · The desk days (6.2–13.2s) — "five golden workflows"
Split frame: serif claim left, ledger table right. Left: kicker mono THE ARENA,
"Five golden workflows." + italic strap "A real structured-derivatives desk day,
replayed for every model." + three teal-ticked evidence lines CASCADE
(deterministic checks · harvested truth · no LLM judge in the score).
Right: table rows SLIDE from right, stagger 0.12s, underlines DRAW — real data:
Risk Manager Control Day/risk manager/9 · Trader RFQ-to-Booking Day/trader/10 ·
High-Board Portfolio Review Day/high board/8 · Risk Limit Breach Day/risk
manager/7 · Operations Settlement Day/trader/8. BG: ghost serif "5" at 6%,
teal-tinted glow. FG: column header rule + mono caption. → push slide.

### S3 · The ability card (13.2–20.6s) — PEAK, "the measurement"
Left: kicker THE MEASUREMENT, "Every model gets / an ability card." + italic
"Six stats from a desk day's checks — none of them another LLM's opinion." +
mono provenance RUN #101 · RISK LIMIT BREACH DAY · 18 MODELS · REPEATED TRIALS.
Right: the card TILTS in (perspective wrapper, rotationY −16→−8 expo.out) —
kimi-2-7, SNIPER chip (teal), OVR 91 COUNTS UP in 150px brick serif with a
back.out PUNCH on landing; six stat bars FILL staggered on the 0–99 axis while
values COUNT UP: GRD 99 · ADH 99 · SYN 99 · EFF 56 · PRC 87 · CON 99.
BG: ghost "OVR", strongest warm glow of the piece. Ambient: card floats
(rotationY −8→−5 sine). → push slide.

### S4 · The standings (20.6–26.2s) — "published, never merged"
Board scene. Left-anchored serif "Published, never merged." + italic "Averaging
cards is sound; merging boards is not." Six consolidated rows CASCADE up, bars
FILL on the fixed 0–99 axis (site rule): gemini-3-6-flash 90 (gold),
gpt-5-6-terra 88 (silver), deepseek-v4-pro 87 (bronze), grok-4-5 86,
deepseek-v4-flash 84, gpt-5-6-luna 83 (brick). FG: mono footnote CONSOLIDATED
CARDS · 4 BOARDS · 2026. BG: ghost "№1" at 6%. → blur crossfade (wind-down).

### S5 · Colophon (26.2–29.5s) — resolve
Deliberately centered and still (solemn closing). Eyebrow ARTENA, serif
"Read the measurements.", mono URL chip **www.artena.one/arena** under a short
brick rule, mono sub REPORTS · LEADERBOARD · MODEL CARDS. Bookend rules DRAW
mirroring S1. Only scene allowed to fade out (28.8→29.4).

## Recurring motifs
Drawn hairline rules · brick stamp-rules · mono coordinates in corners ·
registration marks · tabular numerals · the 0–99 axis.

## Negative prompt
No dark mode, no gradient text, no neon/cyan, no left-edge accent stripes,
no invented scores (all numbers from boards.json / the site), no exit
animations before transitions, no infinite repeats, no Math.random/Date.now.

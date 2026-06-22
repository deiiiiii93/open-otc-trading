# Feature Walkthrough Video — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a ~90s, 1920×1080 narrated walkthrough that introduces the Open OTC platform with the AI agent as the hero, following one end-to-end thread (quote → price → book → risk → hedge → breadth), bookended by the existing brand cover.

**Architecture:** A HyperFrames root composition (`marketing/feature-walkthrough/index.html`) sequences seven scene sub-compositions on two alternating visual tracks (for 0.5s crossfade overlaps), with seven per-line voiceover clips and one low BGM bed on audio tracks. Real pages are genuine screenshots captured from the live app; the agent chat and HITL confirmation cards are rendered as on-brand HTML for legibility and script control.

**Tech Stack:** HyperFrames CLI (`npx hyperframes`), GSAP 3.14.2 (CDN), headless Chrome via chrome-devtools MCP (screenshot capture), HyperFrames TTS + BGM, ffmpeg/ffprobe for verification. Fonts: Inter Tight + JetBrains Mono (woff2 reused from the cover).

## Global Constraints

- **Canvas/palette (dark theme, verbatim from `frontend/src/tokens/colors.css`):** bg `#131009`, surfaces `#1B1710`/`#2C261A`, hairlines `#3F3829`/`#5A4F38`, ink `#F0E9D5`/`#C9C0A8`, gold accent `#D9B469`, green `#7AAB6A`, red `#D9645B`, blue `#6A8FB8`.
- **Fonts:** only `"Inter Tight"` (brand voice) and `"JetBrains Mono"` (data voice), declared via `@font-face` pointing at local woff2. No other font family in any `font-family` declaration or stack (the lint font check validates every entry; system-ui/sans-serif/monospace generics are allowed, `-apple-system`/`SFMono-Regular` are NOT).
- **Standalone composition rules:** root and each sub-comp host must use a real `id` plus `data-composition-id`, `data-start`, `data-width="1920"`, `data-height="1080"`; the root standalone div must NOT carry a stray `class` (it breaks lint root detection). Sub-comps use a `<template>` wrapper; the standalone root does NOT.
- **GSAP rules:** all timelines `gsap.timeline({paused:true})`, registered as `window.__timelines["<composition-id>"]`. Scope every selector with the composition's `#id …`. Inside sub-comps prefer `tl.fromTo()` over `tl.from()`. Never stack two transform tweens on one element (combine, or split parent/child). Ambient loops attach to `tl` (never bare `gsap.to`). No `repeat:-1` — compute finite repeats. No `Math.random`/`Date.now`/argless `new Date()` — use a seeded mulberry32.
- **Scene transitions:** every scene uses entrance animations; only the final scene (lockup) may use exit/fade-out tweens. Outgoing scenes end by clip expiry, not by exit tweens.
- **Video duration:** ffprobe must report 1920×1080 and ~90s (88–92s acceptable).
- **VO phonetics for TTS:** `102 → "one-oh-two"`, `CSI 500 → "C-S-I five hundred"`, `60 → "sixty"`, `8% → "eight percent"`.
- **Verification chain per scene file:** `npx hyperframes lint` (0 errors) → `npx hyperframes inspect` (0 layout issues, or every overflow marked `data-layout-allow-overflow`/`data-layout-ignore` with reason). The expected, allowed lint warning is `gsap_studio_edit_blocked` (programmatic timelines are required).
- **Project root for all CLI commands:** `marketing/feature-walkthrough/` (note `/marketing/` is gitignored — artifacts are not committed; the spec under `docs/` is the tracked record).

---

### Task 1: Scaffold project + capture live-app screenshots

**Files:**
- Create: `marketing/feature-walkthrough/` (project dir)
- Create: `marketing/feature-walkthrough/fonts/*.woff2` (copied)
- Create: `marketing/feature-walkthrough/assets/*.png` (captured)

**Interfaces:**
- Produces: the `assets/` PNGs (`desk-chrome.png`, `booking.png`, `risk.png`, `hedging.png`, `scenario.png`, `backtest.png`, `reports.png`, `positions.png`) and `fonts/` woff2, consumed by all scene tasks.

- [ ] **Step 1: Scaffold the HyperFrames project**

```bash
cd /Users/fuxinyao/open-otc-trading/marketing
mkdir -p feature-walkthrough/assets
cd feature-walkthrough && npx --yes hyperframes init . >/dev/null 2>&1 || true
```

- [ ] **Step 2: Copy the brand fonts from the cover**

```bash
cp ../product-intro/fonts/*.woff2 ./fonts/
ls fonts/   # expect 6 woff2 (inter-tight 400/600/700, jetbrains-mono 400/500/700)
```

- [ ] **Step 3: Confirm both servers are live**

```bash
lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | grep -E ":5173|:8000"
# expect node on :5173 (vite) and python on :8000 (uvicorn)
```

- [ ] **Step 4: Force dark theme, then capture each page** (chrome-devtools MCP)

For each URL below: `navigate_page` to it, `resize_page` to 1920×1080, run `evaluate_script` to force the warm dark theme and settle, then `take_screenshot` (png, fullPage:false) saving to `assets/<name>.png`.

Theme-force script (the app keys theme off `data-theme` on `<html>`):
```js
() => { document.documentElement.setAttribute('data-theme','dark');
        localStorage.setItem('theme','dark'); return document.documentElement.dataset.theme; }
```
Capture map (prefix `http://localhost:5173`):
`/desk → desk-chrome.png`, `/booking → booking.png`, `/risk → risk.png`,
`/hedging → hedging.png`, `/scenario-test → scenario.png`, `/backtest → backtest.png`,
`/reports → reports.png`, `/positions → positions.png`.
Before capturing a data page, if it has a portfolio selector, pick one with positions so the page is not empty.

- [ ] **Step 5: Verify assets exist and are non-trivial**

```bash
cd /Users/fuxinyao/open-otc-trading/marketing/feature-walkthrough
for f in desk-chrome booking risk hedging scenario backtest reports positions; do
  test -s "assets/$f.png" && echo "ok $f $(stat -f%z assets/$f.png)B" || echo "MISSING $f"; done
```
Expected: every file present, each > 20000 bytes (empty/blank pages render tiny — investigate any small file by re-capturing with the portfolio populated, or substitute `positions.png` as filler and note it).

- [ ] **Step 6: Commit is N/A (gitignored).** Instead, snapshot the asset list into the run log:

```bash
ls -la assets/ fonts/ > .capture-manifest.txt && echo "manifest written"
```

---

### Task 2: Generate voiceover + BGM audio

**Files:**
- Create: `marketing/feature-walkthrough/assets/vo-0.mp3 … vo-6.mp3`
- Create: `marketing/feature-walkthrough/assets/bgm.mp3`
- Create: `marketing/feature-walkthrough/assets/vo-durations.txt`

**Interfaces:**
- Produces: 7 VO clips + 1 BGM bed and their measured durations, consumed by Task 3 (audio wiring) and used to finalize scene timings.

- [ ] **Step 1: Generate the 7 VO lines** (English, confident neutral male)

Run one `npx hyperframes tts` per line (pick a male English voice; if the configured provider needs keys and fails, fall back to the local Kokoro provider). Exact copy:
```
vo-0: "This is Open OTC — an AI-powered derivatives desk."
vo-1: "Tell it what you want, in plain language. Quote a twelve-month C-S-I five hundred snowball — knock-out one-oh-two, knock-in sixty, eight percent coupon."
vo-2: "It builds the structured product, prices it with full Greeks, and waits for your approval before anything is booked."
vo-3: "Ask for your risk, and it reprices the book and aggregates delta, gamma and vega — in a single pass."
vo-4: "Hedge the C-S-I five hundred delta. The solver sizes the legs, you approve, and they're booked to the hedging blotter."
vo-5: "Scenario stress tests, historical backtests, board-ready reports — the same assistant, in the same thread."
vo-6: "Open OTC. One assistant. Your whole desk."
```
Example invocation (adjust flags to the installed CLI's `tts --help`):
```bash
npx --yes hyperframes tts --text "This is Open OTC — an AI-powered derivatives desk." \
  --voice <male-en-voice> --output assets/vo-0.mp3
```

- [ ] **Step 2: Generate a subtle BGM bed**

```bash
npx --yes hyperframes bgm --prompt "calm confident minimal fintech underscore, warm low pulse, no melody spikes, ambient bed" \
  --duration 92 --output assets/bgm.mp3
```
(If `bgm` needs keys and is unavailable, leave BGM out and note it; the piece still works VO-only.)

- [ ] **Step 3: Measure durations to finalize timing**

```bash
cd /Users/fuxinyao/open-otc-trading/marketing/feature-walkthrough
for i in 0 1 2 3 4 5 6; do d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 assets/vo-$i.mp3); echo "vo-$i $d"; done | tee assets/vo-durations.txt
```
Expected each VO ≈ scene budget below. If a VO is longer than its scene's content window, lengthen that scene's `data-duration` in Task 8 (scenes are flexible; keep the total ≈90s).

Scene/VO budget (visual durations; 0.5s crossfade overlaps):
`S0 6.5 · S1 14.5 · S2 16.5 · S3 16.5 · S4 18.5 · S5 14.5 · S6 6.0`.

---

### Task 3: Root skeleton + bookend scenes + audio wiring

**Files:**
- Create: `marketing/feature-walkthrough/index.html` (root; overwrite the scaffold)
- Create: `marketing/feature-walkthrough/compositions/scene0-cover.html`
- Create: `marketing/feature-walkthrough/compositions/scene6-lockup.html`

**Interfaces:**
- Produces: the root timeline contract — visual scenes on tracks `0`/`1` (alternating), VO on track `2`, BGM on track `3`; scene host ids `s0`…`s6`; composition ids `scene0`…`scene6`. Later tasks add `compositions/sceneN-*.html` and reference these exact start times.

**Start-time table (root clips):**
| host id | comp id | src | data-start | data-duration | track |
|---|---|---|---|---|---|
| s0 | scene0 | scene0-cover.html | 0.0 | 6.5 | 0 |
| s1 | scene1 | scene1-desk.html | 6.0 | 14.5 | 1 |
| s2 | scene2 | scene2-booking.html | 20.0 | 16.5 | 0 |
| s3 | scene3 | scene3-risk.html | 36.0 | 16.5 | 1 |
| s4 | scene4 | scene4-hedging.html | 52.0 | 18.5 | 0 |
| s5 | scene5 | scene5-breadth.html | 70.0 | 14.5 | 1 |
| s6 | scene6 | scene6-lockup.html | 84.0 | 6.0 | 0 |
| VO clips | — | assets/vo-N.mp3 | scene_start+0.6 | (media) | 2 |
| bgm | — | assets/bgm.mp3 | 0.0 | 90 | 3 |

- [ ] **Step 1: Write `index.html` root**

Standalone root (no `<template>`, no stray `class`), `id="root"`, `data-composition-id="feature-walkthrough"`, `data-start="0"`, `data-duration="90"`, `data-width="1920"`, `data-height="1080"`, background `#131009`. Inside it, the 7 scene host `<div>`s (each `data-composition-id`, `data-composition-src`, `data-start`, `data-duration`, `data-track-index` per the table; sub-comps get `data-width/height` too), 7 `<audio>` VO clips on track 2 (`data-start` per table, `data-volume="1"`), and one `<audio>` BGM on track 3 (`data-start="0" data-duration="90" data-volume="0.18"`). No GSAP needed in root (framework auto-nests sub-timelines).

```html
<div id="s1" data-composition-id="scene1" data-composition-src="compositions/scene1-desk.html"
     data-start="6.0" data-duration="14.5" data-width="1920" data-height="1080" data-track-index="1"></div>
<audio id="vo1" src="assets/vo-1.mp3" data-start="6.6" data-track-index="2" data-volume="1"></audio>
<!-- …repeat per table; bgm last -->
<audio id="bgm" src="assets/bgm.mp3" data-start="0" data-duration="90" data-track-index="3" data-volume="0.18"></audio>
```

- [ ] **Step 2: Write `scene0-cover.html`** — the brand open

`<template>`-wrapped sub-comp, comp id `scene0`. Rebuild the cover's title language inline (reuse the cover CSS: warm bg, radial gold glow, hero `Open` + gold `OTC` wordmark in Inter Tight 700 ~200px, a gold rule, a mono kicker). Declare the same `@font-face` blocks (src `../fonts/…`). Entrance: wordmark `fromTo` opacity+scale (expo.out) at 0.3s; rule scaleX; kicker fade. Ambient: gold glow breathing on `tl`. Last 0.5s is the crossfade tail (no exit tween — the clip just ends).

- [ ] **Step 3: Write `scene6-lockup.html`** — the brand close

Comp id `scene6`. Same wordmark, plus tagline line "One assistant. Your whole desk." Entrance fade/scale. **This is the only scene allowed an exit:** fade the whole group to near-black over the final 0.6s.

- [ ] **Step 4: Gate — lint + inspect + partial render**

```bash
cd /Users/fuxinyao/open-otc-trading/marketing/feature-walkthrough
npx --yes hyperframes lint     # 0 errors (allow gsap_studio_edit_blocked)
npx --yes hyperframes inspect  # 0 layout issues
npx --yes hyperframes render --output _wip.mp4 && ffprobe -v error -show_entries format=duration -of csv=p=0 _wip.mp4
```
Expected: lint 0 errors; inspect clean; render produces a 90s file (scenes 1–5 are blank hosts until built — that is fine at this checkpoint; the cover/lockup and audio should play).

---

### Task 4: Shared agent components + Scene 1 (AgentDesk)

**Files:**
- Create: `marketing/feature-walkthrough/compositions/_agent-ui.css.html` (a copy-paste snippet block, NOT loaded directly)
- Create: `marketing/feature-walkthrough/compositions/scene1-desk.html`

**Interfaces:**
- Consumes: `assets/desk-chrome.png`, `assets/vo-1.mp3`.
- Produces: the reusable agent **chat bubble** and **HITL card** CSS/markup pattern (pasted into scenes 1, 2, 4). Component classes: `.chat`, `.bubble.user`, `.bubble.agent`, `.persona-tag`, `.hitl`, `.hitl__row`, `.hitl__btn.approve`, `.hitl__btn.reject`, `.hitl__check`.

- [ ] **Step 1: Author the agent-UI component snippet** (`_agent-ui.css.html`)

Faithful to `frontend/src/routes/AgentDesk.tsx` (read it first for surface/border/radius cues). Bubbles: agent left (surface `#1B1710`, 1px `#3F3829` border, radius 14px, gold `.persona-tag` `TRADER` in JetBrains Mono 18px), user right (surface `#2C261A`). Quoted terms/numbers in JetBrains Mono. HITL card: header `CONFIRM · book_position` (mono, gold), a 3–4 row term summary, and Approve (green `#7AAB6A`) / Reject (hairline) buttons; `.hitl__check` is a hidden green ✓ revealed on approve. Min sizes: bubble text ≥28px, labels ≥18px.

- [ ] **Step 2: Write `scene1-desk.html`**

Comp id `scene1`. Layers: (a) `assets/desk-chrome.png` as a slow-push backdrop — wrap in `.shot-wrap` and Ken-Burns the `<img>` child only (parent gets any entrance, child gets the scale; never both on one element); dim it with a `rgba(19,16,9,0.55)` scrim so rendered chat reads on top. (b) The chat column (paste agent-UI snippet): user bubble with the quoted request, then agent bubble. Animate: user bubble `fromTo` slide-up at 0.6s; a typing dots indicator; agent bubble reveal at ~2.5s; persona tag pop. Keep within the 14.5s window; last 0.5s is crossfade tail (no exit).

- [ ] **Step 3: Gate**

```bash
npx --yes hyperframes lint && npx --yes hyperframes inspect --at 8,12,18
```
Expected: 0 errors; 0 layout issues (mark the dimmed screenshot `data-layout-allow-overflow` only if the Ken-Burns push pushes it past canvas).

- [ ] **Step 4: Visual check** — render one frame and read it

```bash
npx --yes hyperframes render --output _wip.mp4 >/dev/null 2>&1
ffmpeg -y -ss 12 -i _wip.mp4 -frames:v 1 /tmp/fw-s1.png 2>/dev/null
```
Open `/tmp/fw-s1.png`: chat bubbles legible, desk backdrop visible but subordinate, persona tag gold.

---

### Task 5: Scene 2 (Booking + HITL approve)

**Files:**
- Create: `marketing/feature-walkthrough/compositions/scene2-booking.html`

**Interfaces:**
- Consumes: `assets/booking.png`, `assets/vo-2.mp3`, the agent-UI snippet.
- Produces: the HITL **approve** animation pattern (button press → `.hitl__check` reveal → card collapse), reused in Scene 4.

- [ ] **Step 1: Write `scene2-booking.html`**

Comp id `scene2`. Left ~58%: `assets/booking.png` shot (Ken-Burns child only) showing the priced product. Right rail: a rendered **price + Greeks** readout card (mono `PRICE`, `Δ`, `Γ`, `Vega` rows, tabular-nums) that counts/fades in, then the **HITL booking card**. Choreography: shot fades in 0.3s; Greeks rows stagger 1.2–2.4s; HITL card slides in ~4s; at ~9s animate Approve press (`scale 0.94→1`, background brighten), reveal `.hitl__check`, then collapse the card height with a brief "BOOKED" gold stamp. No exit tween; clip ends into crossfade.

- [ ] **Step 2: Gate + visual check**

```bash
npx --yes hyperframes lint && npx --yes hyperframes inspect --at 22,28,34
npx --yes hyperframes render --output _wip.mp4 >/dev/null 2>&1
ffmpeg -y -ss 30 -i _wip.mp4 -frames:v 1 /tmp/fw-s2.png 2>/dev/null
```
Open `/tmp/fw-s2.png`: Greeks readout legible, HITL card present, approve state coherent.

---

### Task 6: Scene 3 (Risk) + Scene 4 (Hedging + HITL)

**Files:**
- Create: `marketing/feature-walkthrough/compositions/scene3-risk.html`
- Create: `marketing/feature-walkthrough/compositions/scene4-hedging.html`

**Interfaces:**
- Consumes: `assets/risk.png`, `assets/hedging.png`, `assets/vo-3.mp3`, `assets/vo-4.mp3`, agent-UI snippet (scene 4 HITL).
- Produces: nothing downstream (terminal scene content).

- [ ] **Step 1: Write `scene3-risk.html`**

Comp id `scene3`. `assets/risk.png` as the hero shot (Ken-Burns child). Overlay rendered **count-up totals** for `DELTA / GAMMA / VEGA` (mono, tabular-nums) and 3–4 **by-underlying bars** that draw in (`scaleX 0→1`, transformOrigin left), gold/green. Use a seeded mulberry32 for any jitter. Numbers animate via a GSAP tween on a proxy object updating `textContent` inside the timeline (seekable). Choreography: shot 0.3s; totals count 0.8–2.2s; bars stagger 1.5–3s. No exit.

- [ ] **Step 2: Write `scene4-hedging.html`**

Comp id `scene4`. `assets/hedging.png` shot. Rendered **solver proposal**: 2–3 hedge legs (mono rows: `IC2406 · SELL · 12 · Δ-resid 0.03`) that stagger in, then a second **HITL card** `CONFIRM · book_hedge` with the approve pattern from Task 5; on approve, the legs get a green ✓ and a "BOOKED TO BLOTTER" gold stamp. Choreography fits the 18.5s window. No exit.

- [ ] **Step 3: Gate + visual check (both)**

```bash
npx --yes hyperframes lint && npx --yes hyperframes inspect --at 40,48,56,64
npx --yes hyperframes render --output _wip.mp4 >/dev/null 2>&1
for t in 44 62; do ffmpeg -y -ss $t -i _wip.mp4 -frames:v 1 /tmp/fw-$t.png 2>/dev/null; done
```
Open `/tmp/fw-44.png` (risk) and `/tmp/fw-62.png` (hedging): totals/bars and legs/HITL legible and on-brand.

---

### Task 7: Scene 5 (breadth montage)

**Files:**
- Create: `marketing/feature-walkthrough/compositions/scene5-breadth.html`

**Interfaces:**
- Consumes: `assets/scenario.png`, `assets/backtest.png`, `assets/reports.png`, `assets/vo-5.mp3`.

- [ ] **Step 1: Write `scene5-breadth.html`**

Comp id `scene5`. Three panels (scenario / backtest / report) slide through in sequence within 14.5s — each `fromTo` x-slide-in with a mono label chip (`SCENARIO TESTS`, `BACKTESTS`, `BOARD REPORTS`), Ken-Burns child on each `<img>`. Use a quick directional wipe feel via staggered slide + a thin gold divider that travels. Vary eases (expo.out / power3.out / back.out). No exit (last 0.5s crossfades to lockup).

- [ ] **Step 2: Gate + visual check**

```bash
npx --yes hyperframes lint && npx --yes hyperframes inspect --at 72,77,82
npx --yes hyperframes render --output _wip.mp4 >/dev/null 2>&1
ffmpeg -y -ss 77 -i _wip.mp4 -frames:v 1 /tmp/fw-s5.png 2>/dev/null
```
Open `/tmp/fw-s5.png`: a panel + label chip legible, motion reads as a montage.

---

### Task 8: Integrate, finalize timing, full render + verification

**Files:**
- Modify: `marketing/feature-walkthrough/index.html` (timing tweaks only)
- Create: `marketing/feature-walkthrough/feature-walkthrough.mp4` (final)

- [ ] **Step 1: Reconcile VO vs scene durations**

Compare `assets/vo-durations.txt` to the budget. If any VO overruns its scene's content window, bump that scene host's `data-duration` (and shift every later `data-start` by the same delta, keeping 0.5s overlaps) in `index.html`; update root `data-duration` to the new total.

- [ ] **Step 2: Full gate chain**

```bash
cd /Users/fuxinyao/open-otc-trading/marketing/feature-walkthrough
npx --yes hyperframes lint        # 0 errors
npx --yes hyperframes validate    # contrast clean (intentional decoratives excepted)
npx --yes hyperframes inspect --samples 18   # 0 layout issues
```

- [ ] **Step 3: Final render + ffprobe**

```bash
npx --yes hyperframes render --output feature-walkthrough.mp4 2>&1 | tail -3
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate \
  -show_entries format=duration -of default=noprint_wrappers=1 feature-walkthrough.mp4
```
Expected: 1920×1080, 30fps, duration ~90s.

- [ ] **Step 4: Hero-frame spot check**

```bash
for t in 3 12 30 44 62 77 87; do ffmpeg -y -ss $t -i feature-walkthrough.mp4 -frames:v 1 /tmp/fw-final-$t.png 2>/dev/null; done
```
Open each: cover, chat, booking+HITL, risk, hedging, breadth, lockup — all legible, on-brand, no overlap/clipping. Fix any scene file and re-render.

- [ ] **Step 5: Clean up WIP + write final manifest**

```bash
rm -f _wip.mp4
ls -la feature-walkthrough.mp4 assets/ > .final-manifest.txt && echo "done"
```

- [ ] **Step 6: Summarize** deliverable path, duration, what's real-capture vs rendered, and any disclosed substitutions (e.g. a page that was sparse).

---

## Self-Review

**Spec coverage:** treatment (T3/T8), capture plan (T1), rendered agent chat+HITL (T4/T5/T6), genuine data screenshots (T1→T3-7), 7-scene storyboard with VO (T3–T7), bookends reuse (T3), VO per-line + BGM (T2), no captions (omitted by design), verification gates (every task + T8), risks/mitigations (sparse pages in T1.5, TTS fallback in T2.1, agent-card fidelity in T4.1). All spec sections map to a task.

**Placeholder scan:** no TBD/TODO; copy is verbatim; commands are concrete; durations and tracks are explicit. Visual fine-tuning is bounded by the inspect/frame-check gates, not left vague.

**Type/name consistency:** composition ids `scene0…scene6` and host ids `s0…s6` consistent across T3 table and scene tasks; agent-UI classes defined once in T4.1 and reused by name in T5/T6; asset filenames consistent between T1 capture map and scene `Consumes` lists; track indices (0/1 visual, 2 VO, 3 BGM) consistent in T3 table and audio steps.

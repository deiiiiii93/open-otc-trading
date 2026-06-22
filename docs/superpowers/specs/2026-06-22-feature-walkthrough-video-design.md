# Feature Walkthrough Video — Design Spec

**Date:** 2026-06-22
**Status:** Awaiting user review
**Deliverable:** `marketing/feature-walkthrough/feature-walkthrough.mp4` (1920×1080, ~90s, H.264)

## 1. Goal & audience

Introduce the Open OTC platform's features to **prospective clients / traders**, with the
**AI agent as the hero**. Tone: confident, benefit-led, light on jargon. The piece must show
the agent's real differentiator — *natural language → tool calls → human-in-the-loop (HITL)
approval → results on the real pages* — as a single continuous desk workflow.

## 2. Treatment (locked decisions)

| Decision | Choice |
|---|---|
| Length / format | ~90s, 1920×1080 |
| Narrative spine | **A** — one agent thread, end-to-end |
| Voiceover | English, confident neutral **male**, TTS (HyperFrames) |
| Background music | Subtle low-volume bed under VO |
| Agent chat + HITL cards | **Rendered on-brand** (legible, scripted) over real captured page chrome |
| Data pages (Risk/Hedging/Booking/etc.) | **Genuine screenshots** of the live app |
| Burned-in captions | **None** (VO + diegetic chat bubbles carry the words) |
| Bookends | Reuse the existing `marketing/product-intro` cover's visual language for open + close |

## 3. Brand system (inherited)

From `frontend/src/tokens/colors.css` (dark theme) + the existing cover:
- Canvas `#131009`, ink `#F0E9D5` / `#C9C0A8`, gold accent `#D9B469`, green `#7AAB6A`, red `#D9645B`.
- Fonts: **Inter Tight** (brand voice) + **JetBrains Mono** (data/market voice) — reuse
  `marketing/product-intro/fonts/*.woff2` via `@font-face`.
- The app's own dark theme **is** this warm palette → screenshots will match the video natively.

## 4. Capture plan (live app, already running on :5173)

Capture via headless Chrome (chrome-devtools MCP), **dark theme forced**, viewport 1920×1080
(retina/2× where possible, downscaled for crispness). Save to
`marketing/feature-walkthrough/assets/`.

| Asset | URL | Notes |
|---|---|---|
| `desk-chrome.png` | `/desk` | AgentDesk shell (nav + empty conversation pane) — used as backdrop for rendered chat |
| `booking.png` | `/booking` | Structured-product term capture / pricing result |
| `risk.png` | `/risk` | Delta/gamma/vega totals, by-underlying, by-currency |
| `hedging.png` | `/hedging` | Booked hedge legs (tagged, strategy, residuals) |
| `scenario.png` | `/scenario-test` | Breadth shot |
| `backtest.png` | `/backtest` | Breadth shot |
| `reports.png` | `/reports` | Breadth shot |
| `positions.png` | `/positions` | Optional / fallback filler |

**Pre-capture:** choose a portfolio that actually has positions so pages aren't empty; force
`data-theme="dark"`. If a page renders sparse, fall back to a representative composed panel and
note it in the final summary (no silent fakery).

## 5. On-brand agent components (rendered, not captured)

Faithful to `frontend/src/routes/AgentDesk.tsx`. Two reusable rendered pieces:
- **Chat bubble:** user message (right, paper-2 surface) and agent message (left, hairline
  border, gold persona tag e.g. `TRADER`). JetBrains Mono for any quoted terms/numbers.
- **HITL confirmation card:** the hero interaction. Header `CONFIRM · book_position`, a compact
  term/leg summary, and **Approve / Reject** buttons; an "Approve" press is animated (button
  press + a green check + card collapse) at the scripted beat.

## 6. Storyboard (~90s)

Scenes are clips on a visual track; VO segments on an audio track; BGM on a low-volume track;
rendered agent overlays on an overlay track. Transitions: crossfades between scenes; a quick
wipe into the breadth montage. Screenshots get slow-push (Ken Burns) on a wrapper div (never a
second transform tween on the `<img>` itself). Every scene uses entrance animations; only the
final scene fades out.

| # | Time | Screen | Key on-screen action | VO |
|---|------|--------|----------------------|-----|
| 0 | 0–6 | Brand cover (reused) | "Open OTC" title settles | "This is Open OTC — an AI-powered derivatives desk." |
| 1 | 6–20 | AgentDesk + rendered chat | User bubble types the request; agent bubble plans (persona tag `TRADER`) | "Tell it what you want, in plain language. 'Quote a twelve-month CSI 500 snowball — knock-out one-oh-two, knock-in sixty, eight-percent coupon.'" |
| 2 | 20–36 | Booking panel + HITL card | Price + Greeks reveal; **booking card → Approve** (press + green check) | "It builds the structured product, prices it with full Greeks, and waits for your approval before anything is booked." |
| 3 | 36–52 | Risk page | Totals count up; by-underlying bars draw in | "Ask for your risk, and it reprices the book and aggregates delta, gamma and vega — in a single pass." |
| 4 | 52–70 | Hedging + HITL card | Solver proposes legs; Approve; legs land on the blotter | "Say 'hedge the CSI 500 delta.' The solver sizes the legs, you approve, and they're booked to the hedging blotter." |
| 5 | 70–84 | Breadth montage (3 panels) | scenario · backtest · report slide through | "Scenario stress tests, historical backtests, board-ready reports — the same assistant, in the same thread." |
| 6 | 84–90 | Brand lockup (reused) | "Open OTC" + tagline | "Open OTC. One assistant. Your whole desk." |

**VO total ≈ 130 words** (~90s with pauses + music-only cover/lockup moments). TTS phonetic
hints: `102 → "one-oh-two"`, `CSI 500 → "C-S-I five hundred"`, `60 → "sixty"`.

## 7. Technical structure

- New project root `marketing/feature-walkthrough/index.html` (standalone, `id="root"`,
  `data-composition-id`, `data-start="0"`, `data-duration≈90`, 1920×1080).
- Scenes as timed clips (or per-scene sub-compositions via `data-composition-src` if a scene
  grows complex). The cover's open/close are **rebuilt inline** reusing the cover CSS — not a
  literal embed of the standalone `product-intro/index.html` (standalone files can't be
  `<template>`-embedded without a wrapper variant).
- Tracks: `0` visuals/screenshots, `1` rendered agent overlays, `2` VO audio, `3` BGM.
- Audio: `video` elements muted; separate `<audio>` for VO and BGM with `data-volume`.
- All GSAP timelines `{paused:true}`, registered on `window.__timelines`, scoped via `#root …`
  (or per-sub-comp id). Deterministic only (seeded PRNG; no `Math.random`/`Date.now`).
- Assets: TTS rendered with `npx hyperframes tts` as **one file per VO line** (7 clips), each
  placed at its scene's `data-start` on the VO track — this lets scene timings flex without
  re-stitching audio. BGM via `npx hyperframes bgm` (or a provided file), one bed for the full
  duration at low `data-volume` (~0.18).

## 8. Verification gates

- `npx hyperframes lint` → 0 errors.
- `npx hyperframes validate` → contrast clean (intentional decoratives excepted).
- `npx hyperframes inspect` → 0 layout issues (mark intentional overflow/decoratives).
- `ffprobe` confirms ~90s duration at 1920×1080.
- Visual spot-check of 3–4 hero frames (cover, HITL approve, risk, lockup).

## 9. Risks & mitigations

- **Sparse live pages** → pick a populated portfolio; compose representative panels if needed
  (disclose, don't fake silently).
- **TTS provider/keys** → fall back to Kokoro local; confirm voice on first preview, allow swap.
- **Agent-card fidelity** → read `AgentDesk.tsx` styles before authoring the rendered components.
- **Render time** (multiple screenshots + audio) → acceptable; capture once, cache in `assets/`.

## 10. Out of scope

Driving a real live agent conversation for capture; live-embedded app pages; multi-language
variants; vertical (9:16) cut. Each can be a follow-up.

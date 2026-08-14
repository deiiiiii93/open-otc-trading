#!/usr/bin/env python
"""Generate FIFA-style Model Ability Cards for an arena run with GPT-Image-2.

Fully generative: the image model draws the whole card, numbers included. Nothing
is composited afterwards. Correctness is enforced by a read-back gate — a vision
model reads each finished PNG and reports what it sees, and the card is rejected
and re-rolled unless every digit matches the database and every pip meter shows
exactly ``round(value / 10)`` lit segments out of ten.

Why pips rather than bars: measured over ten demo renders, GPT-Image-2 reproduces
quoted digits reliably (42/42 correct) but *interpolates* continuous bar lengths —
an EFF of 36 drew at 42-60% of its track and flipped rank against its neighbour
between two samples of one prompt. A countable instruction ("4 of 10 segments
lit") travels the same faithful path as the digits, and a verifier can count
rectangles instead of measuring pixels.

Card data is read, never computed: hero cards use ``store.leaderboard``'s
``card_mean`` (the equal-weight mean across the run's workflows) and mini cards
use each match's derive-on-read ``card``.

Example:
    python scripts/generate_ability_cards.py --run-id 104 --out-dir docs/arena/cards/run104
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

IMAGE_ENDPOINT = "https://zenmux.ai/api/v1/images/generations"
CHAT_ENDPOINT = "https://zenmux.ai/api/v1/chat/completions"
IMAGE_MODEL = "openai/gpt-image-2"
VERIFY_MODEL = "google/gemini-3.6-flash"

STAT_ORDER = ("GRD", "ADH", "SYN", "EFF", "PRC", "CON")

# Vendor emblem, described rather than named so the image model draws a mark
# instead of attempting a trademarked logo reproduction.
EMBLEMS = {
    "grok": "a stylised geometric letter X mark in thin luminous strokes",
    "deepseek": "a stylised minimal outline of a whale in thin luminous strokes",
    "gpt": "a stylised six-lobed knot rosette in thin luminous strokes",
    "claude": "a stylised radiating asterisk burst in thin luminous strokes",
    "gemini": "a stylised four-pointed sparkle in thin luminous strokes",
}
DEFAULT_EMBLEM = "a stylised abstract circuit glyph in thin luminous strokes"

WORKFLOW_LABELS = {
    "risk-manager-control-day": "RISK CONTROL DAY",
    "trader-rfq-booking-day": "TRADER RFQ DAY",
    "high-board-portfolio-review-day": "PORTFOLIO REVIEW DAY",
    "risk-limit-breach-day": "LIMIT BREACH DAY",
}


# ---------------------------------------------------------------------------
# Card model
# ---------------------------------------------------------------------------

def pips_for(value: int) -> int:
    """Lit segments out of ten. Half-up, so 5 lights one pip rather than none.

    Python's round() is banker's rounding (round(0.5) == 0), which would make the
    verifier's expected value disagree with a reader's intuition at exact halves.
    """
    return max(0, min(10, int(value / 10.0 + 0.5)))


def _plural(n: int, word: str) -> str:
    return word if n == 1 else word + "S"


def emblem_for(model_id: str) -> str:
    for key, description in EMBLEMS.items():
        if key in model_id:
            return description
    return DEFAULT_EMBLEM


def build_prompt(card: dict) -> str:
    """Render the Style-B dark-holographic prompt for one card.

    ``card`` carries: name, model_ref, ovr, position, stats (label -> value),
    subtitle, footer, emblem.
    """
    entries = []
    for i, label in enumerate(STAT_ORDER, start=1):
        value = card["stats"][label]
        lit = pips_for(value)
        dark = 10 - lit
        if dark == 0:
            meter = "meter with all 10 of the 10 segments lit, none dark"
        elif dark == 1:
            meter = "meter with 9 segments lit and exactly 1 dark segment at the right end"
        else:
            meter = (f"meter with exactly {lit} segments lit and exactly {dark} dark "
                     f"segments to their right")
        entries.append(f'{i}. "{label}" — number "{value:02d}" — {meter}.')
    stat_block = "\n".join(entries)

    return f"""A single vertical collectible rating card filling the entire frame, shot flat-on with
no perspective tilt. The card is a premium dark special edition: near-black obsidian
base with a soft prismatic holographic sweep running diagonally from lower-left to
upper-right in iridescent teal, violet and rose, like light across black foil. Thin
luminous hairline rules in pale cyan divide the card into bands. Faint, low-contrast
technical motifs are embossed into the black background — a shallow wireframe
volatility surface mesh in the lower third and a few small mathematical glyphs confined
to the right margin, well clear of all text — visible only as texture. Background behind
the card is flat deep charcoal.

Layout, top to bottom:

Top band: on the left, the number "{card['ovr']}" in a very large light-weight geometric
sans-serif in luminous white; directly beneath it the word "OVR" in tiny cyan
letter-spaced capitals. On the right, vertically centred against the "{card['ovr']}", a
small circular emblem holding {card['emblem']}.

Second band, left-aligned under a hairline rule:
1. The model name "{card['name']}" in large medium-weight capitals in white.
2. Under it the exact identifier string "{card['model_ref']}" in small pale-grey monospace.
3. Under that the label "{card['subtitle']}" in tiny cyan letter-spaced capitals.

Main band: a statistics block of exactly 6 entries arranged in 3 rows of 2 columns,
filled ROW BY ROW from left to right. Each entry shows a three-letter label in small
grey capitals, the two-digit number in large white type beneath it, and under that a
segment meter.

Every segment meter is a single horizontal row of EXACTLY TEN small identical
rectangular segments of equal width, evenly spaced with a small uniform gap between
neighbours, spanning the entry's width. Lit segments glow solid cyan; unlit segments
are dark charcoal with a faint grey outline, clearly visible as empty slots so all ten
positions can always be counted. Lit segments always start at the leftmost position and
run consecutively. Draw exactly the stated number of lit segments — no more, no fewer.

The six entries, in order:
{stat_block}

Bottom band, beneath a hairline rule: two small centred lines of pale grey
letter-spaced capitals, reading exactly "{card['footer_1']}" on the first line and
exactly "{card['footer_2']}" on the second.

Constraints: render every quoted string exactly as written with correct digits, and add
no other text anywhere. Every meter must contain exactly ten segment positions. Elegant
restrained editorial typography, deep blacks, controlled glow, print-quality edges, no
watermark, no signature, no human face, no mascot, no UI chrome."""


# ---------------------------------------------------------------------------
# ZenMux calls
# ---------------------------------------------------------------------------

def _post(endpoint: str, payload: dict, timeout: int = 900) -> dict:
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {os.environ['ZENMUX_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def render_card(prompt: str, dest: Path, size: str = "1024x1536") -> Path:
    payload = {"model": IMAGE_MODEL, "prompt": prompt, "n": 1,
               "size": size, "quality": "high", "output_format": "png"}
    data = _post(IMAGE_ENDPOINT, payload)
    b64 = data["data"][0]["b64_json"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(base64.b64decode(b64))
    return dest


VERIFY_INSTRUCTION = """You are reading a generated statistics card. Report ONLY what is
visibly printed — never infer, correct, or complete a value from context.

Return strict JSON, no prose, no code fence:
{"ovr": <int>, "name": "<the large model name>", "model_ref": "<the small monospace id>",
 "stats": [{"label": "<3 letters>", "value": <int>, "lit": <int>, "total": <int>}, ...]}

For each of the six statistics: "value" is the printed number; "lit" is the count of
FILLED/GLOWING segments in that entry's meter; "total" is the count of ALL segment
positions in that meter, filled and empty together. Count the segments one by one."""


def verify_card(png: Path) -> dict:
    b64 = base64.b64encode(png.read_bytes()).decode()
    payload = {
        "model": VERIFY_MODEL,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": VERIFY_INSTRUCTION},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
    }
    data = _post(CHAT_ENDPOINT, payload, timeout=300)
    text = data["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(text)


def check(card: dict, seen: dict) -> list[str]:
    """Return a list of discrepancies; empty means the card may ship."""
    problems: list[str] = []
    if int(seen.get("ovr", -1)) != card["ovr"]:
        problems.append(f"OVR printed {seen.get('ovr')}, expected {card['ovr']}")

    by_label = {str(s.get("label", "")).upper(): s for s in seen.get("stats", [])}
    for label in STAT_ORDER:
        expected = card["stats"][label]
        got = by_label.get(label)
        if got is None:
            problems.append(f"{label} missing from the card")
            continue
        if int(got.get("value", -1)) != expected:
            problems.append(f"{label} printed {got.get('value')}, expected {expected}")
        total = int(got.get("total", -1))
        if total != 10:
            problems.append(f"{label} meter has {total} segment positions, expected 10")
        lit = int(got.get("lit", -1))
        if lit != pips_for(expected):
            problems.append(
                f"{label} meter shows {lit} lit, expected {pips_for(expected)} for {expected}")
    return problems


def produce(card: dict, dest: Path, max_attempts: int = 4) -> dict:
    """Render, verify, re-roll. Returns a record of what happened."""
    prompt = build_prompt(card)
    attempts: list[dict] = []
    for attempt in range(1, max_attempts + 1):
        png = render_card(prompt, dest)
        try:
            seen = verify_card(png)
            problems = check(card, seen)
        except Exception as exc:                      # unreadable verdict is not a pass
            problems = [f"verifier failed: {type(exc).__name__}: {exc}"]
        attempts.append({"attempt": attempt, "problems": problems})
        status = "OK" if not problems else "; ".join(problems)
        print(f"    attempt {attempt}: {status}", flush=True)
        if not problems:
            return {"card": card["slug"], "path": str(dest), "attempts": attempts,
                    "verified": True}
        if attempt < max_attempts:
            time.sleep(2)
    # Keep the last render for inspection, but never mark it verified.
    return {"card": card["slug"], "path": str(dest), "attempts": attempts,
            "verified": False}


# ---------------------------------------------------------------------------
# Card assembly from the arena database
# ---------------------------------------------------------------------------

def cards_for_run(run_id: int) -> list[dict]:
    from app.database import SessionLocal
    from app.services.arena import store
    from app.services.arena.models import get_model
    from app.services.arena.scoring import _card_position

    session = SessionLocal()
    try:
        run = store.get_run(session, run_id)
        if run is None:
            raise SystemExit(f"run {run_id} not found")
        board = store.leaderboard(session, run_id=run_id)
        scored = [m for m in run["matches"] if m["status"] == "scored"]
        n_workflows = len({m["workflow_id"] for m in scored})
        # Trials come from the breakdown's n_trials, NOT arena_run.trials: a merged
        # board's run row carries the source run's trials (1) while every match in it
        # is a folded multi-trial aggregate, so the column understates the evidence.
        trial_counts = {(m.get("score_breakdown") or {}).get("n_trials", 1) for m in scored}
        trials = max(trial_counts) if trial_counts else 1

        cards: list[dict] = []
        for row in board:
            model_id = row["model_id"]
            mean = row.get("card_mean")
            if not mean:
                print(f"  ! {model_id}: no card_mean "
                      f"(carded {row.get('carded_count')}/{row.get('match_count')}) — skipped",
                      flush=True)
                continue
            model = get_model(model_id)
            stats = {k: mean[k] for k in STAT_ORDER if k in mean}
            stats["CON"] = mean.get("con") or 0
            cards.append({
                "slug": f"hero-{model_id}",
                "name": model.display_name.upper(),
                "model_ref": model.zenmux_name,
                "ovr": mean["ovr"],
                "stats": stats,
                "position": _card_position(mean),
                "subtitle": _card_position(mean).upper(),
                "emblem": emblem_for(model_id),
                "footer_1": "OTC DESK AGENT ARENA",
                "footer_2": (f"{n_workflows} {_plural(n_workflows, 'WORKFLOW')} — "
                             f"{trials} {_plural(trials, 'TRIAL')} — "
                             f"DETERMINISTIC SCORING"),
            })

        for match in sorted(run["matches"], key=lambda m: (m["model_id"], m["workflow_id"])):
            if match["status"] != "scored":
                continue
            bd = match.get("score_breakdown") or {}
            card = bd.get("card")
            if not card:
                print(f"  ! {match['model_id']}/{match['workflow_id']}: uncarded "
                      f"({bd.get('card_reason')}) — skipped", flush=True)
                continue
            model = get_model(match["model_id"])
            stats = dict(card["stats"])
            stats["CON"] = card.get("con") or 0
            wf = match["workflow_id"]
            cards.append({
                "slug": f"mini-{match['model_id']}-{wf}",
                "name": model.display_name.upper(),
                "model_ref": model.zenmux_name,
                "ovr": card["ovr"],
                "stats": stats,
                "position": card.get("position", ""),
                "subtitle": WORKFLOW_LABELS.get(wf, wf.upper()),
                "emblem": emblem_for(match["model_id"]),
                "footer_1": "OTC DESK AGENT ARENA",
                "footer_2": (f"{bd.get('n_trials', 1)} "
                             f"{_plural(bd.get('n_trials', 1), 'TRIAL')} — "
                             f"OBJECTIVE {match['objective_score']:.1f}"),
            })
        return cards
    finally:
        session.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", type=int, required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--only", nargs="*", default=None,
                    help="Substrings; render only cards whose slug matches one.")
    ap.add_argument("--max-attempts", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true",
                    help="Print card data and the first prompt; call nothing.")
    args = ap.parse_args()

    cards = cards_for_run(args.run_id)
    if args.only:
        cards = [c for c in cards if any(s in c["slug"] for s in args.only)]
    if not cards:
        raise SystemExit("no cards to render")

    print(f"{len(cards)} card(s) for run {args.run_id}:", flush=True)
    for c in cards:
        pip_view = " ".join(f"{k}{c['stats'][k]}/{pips_for(c['stats'][k])}"
                            for k in STAT_ORDER)
        print(f"  {c['slug']}: OVR {c['ovr']} [{c['position']}] {pip_view}", flush=True)

    if args.dry_run:
        print("\n--- prompt for", cards[0]["slug"], "---\n")
        print(build_prompt(cards[0]))
        return 0

    out = Path(args.out_dir)
    results = []
    for c in cards:
        print(f"\n  {c['slug']}", flush=True)
        results.append(produce(c, out / f"{c['slug']}.png", args.max_attempts))

    (out / "verification.json").write_text(json.dumps(results, indent=2))
    ok = sum(1 for r in results if r["verified"])
    rolls = sum(len(r["attempts"]) for r in results)
    print(f"\nverified {ok}/{len(results)} cards in {rolls} renders "
          f"({rolls - len(results)} re-rolls)", flush=True)
    for r in results:
        if not r["verified"]:
            print(f"  UNVERIFIED: {r['card']} — {r['attempts'][-1]['problems']}", flush=True)
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

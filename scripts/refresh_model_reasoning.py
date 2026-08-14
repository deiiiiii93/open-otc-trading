#!/usr/bin/env python3
"""Refresh `config/model_reasoning.json` from models.dev.

models.dev is the open, community-maintained model registry that OpenCode uses.
Each model entry carries `reasoning`, `reasoning_options`
(`[{"type": "effort", "values": [...]}, {"type": "toggle"}]`) and `interleaved`
— i.e. exactly the per-model reasoning conventions this repo used to hardcode.

**Snapshot, not a runtime fetch.** The desk reads a vendored JSON file; nothing in
the request path talks to models.dev. That is deliberate and matches how this repo
already treats third-party truth (`quantark==0.3.0` pinned exactly, arena fixtures
harvested rather than computed live): a value that silently changes under you
invalidates the behaviour it governs. Refreshing is a reviewable commit, and the
snapshot records the upstream sha256 so drift is visible in the diff.

The effort ladder is per **route**, not per model: models.dev lists
`deepseek-v4-pro` as `high,max` on the direct DeepSeek API but `low,medium,high`
through the ZenMux gateway. So entries are keyed by channel, matching the desk's
own `(channel, provider, model)` selection triple.

Usage:
    python scripts/refresh_model_reasoning.py           # write the snapshot
    python scripts/refresh_model_reasoning.py --check   # CI: fail if stale
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

SOURCE_URL = "https://models.dev/api.json"
REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = REPO_ROOT / "config" / "model_reasoning.json"

# Desk channel name -> models.dev provider id. Both of this repo's channels happen
# to share their name with the upstream provider; a new channel needs an entry here
# or it simply resolves as "unknown" (permissive), never as "unsupported".
# Canonical weakest -> strongest order, so a measured ladder is stored sorted
# rather than in probe-completion order.
EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

CHANNEL_TO_PROVIDER = {
    "zenmux": "zenmux",
    "deepseek": "deepseek",
}


def _fetch(url: str) -> tuple[dict, str]:
    # models.dev answers 403 to urllib's default User-Agent, so send a real one.
    request = urllib.request.Request(
        url, headers={"User-Agent": "open-otc-trading/model-reasoning-refresh"}
    )
    with urllib.request.urlopen(request, timeout=60) as resp:  # noqa: S310 (fixed https URL)
        raw = resp.read()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _entry(model: dict) -> dict:
    """Reduce a models.dev entry to the reasoning facts the desk needs."""
    options = model.get("reasoning_options") or []
    efforts: list[str] = []
    toggle = False
    for opt in options:
        if not isinstance(opt, dict):
            continue
        if opt.get("type") == "effort":
            efforts = [str(v) for v in (opt.get("values") or [])]
        elif opt.get("type") == "toggle":
            toggle = True
    out = {
        "reasoning": bool(model.get("reasoning")),
        "efforts": efforts,
        "toggle": toggle,
    }
    # The field a provider replays thinking through — the DeepSeek
    # `reasoning_content` quirk that DeepSeekReasoningChat hand-implements.
    # Captured for provenance; nothing reads it yet.
    interleaved = model.get("interleaved")
    if isinstance(interleaved, dict) and interleaved.get("field"):
        out["interleaved_field"] = str(interleaved["field"])
    return out


def merge_probe(snapshot: dict, probe: list[dict], channel: str, probed_at: str) -> dict:
    """Overlay MEASURED per-model ladders onto the models.dev-derived snapshot.

    models.dev is a third-party claim about a model, not about our route to it, and
    a live probe showed it wrong in BOTH directions: it lists `openai/gpt-5.5` as
    low/medium/high when the gateway also accepts `none` and `xhigh`, and lists
    `z-ai/glm-5.2` as high/max when every level is accepted. A ladder that is too
    narrow is the dangerous direction — it makes us reject a level that works.

    So a measured entry WINS, and records that it was measured. Unprobed models keep
    the declared ladder (better than nothing) and say so. `toggle` is never
    overwritten: whether reasoning can be switched off wholesale is not something an
    effort probe observes.
    """
    accepted: dict[str, set[str]] = {}
    seen: set[str] = set()
    inconclusive: dict[str, set[str]] = {}
    for row in probe:
        model_id = row.get("model")
        if not model_id or row.get("toggle"):
            continue
        seen.add(model_id)
        level = row.get("level")
        if not level:
            continue
        outcome = row.get("outcome")
        if outcome in {"ok", "ok-flat", "accepted"}:
            accepted.setdefault(model_id, set()).add(level)
        elif outcome != "rejected":
            # Quota / rate-limit / 5xx / transport says nothing about the level.
            inconclusive.setdefault(model_id, set()).add(level)

    # A model with ANY inconclusive level is NOT marked measured: its accepted set
    # is incomplete, and a measured ladder is the one thing we gate on, so a
    # missing level would become a hard rejection of something that may work.
    # Re-probe those levels instead.
    for model_id in sorted(inconclusive):
        print(
            f"  not marking {model_id} measured — inconclusive levels "
            f"{sorted(inconclusive[model_id])}; re-probe them",
            file=sys.stderr,
        )
    seen -= set(inconclusive)

    route = snapshot.setdefault("routes", {}).setdefault(channel, {})
    for model_id in sorted(seen):
        entry = dict(route.get(model_id) or {})
        levels = accepted.get(model_id, set())
        entry["efforts"] = [e for e in EFFORT_ORDER if e in levels]
        entry["reasoning"] = bool(entry.get("reasoning") or levels)
        entry.setdefault("toggle", False)
        entry["source"] = "measured"
        entry["measured_at"] = probed_at
        route[model_id] = entry
    for model_id, entry in route.items():
        entry.setdefault("source", "models.dev")
    return snapshot


def build(payload: dict, source_sha256: str, fetched_at: str) -> dict:
    routes: dict[str, dict] = {}
    for channel, provider_id in sorted(CHANNEL_TO_PROVIDER.items()):
        models = (payload.get(provider_id) or {}).get("models") or {}
        routes[channel] = {
            model_id: _entry(model)
            for model_id, model in sorted(models.items())
        }
    return {
        "source": SOURCE_URL,
        "source_sha256": source_sha256,
        "fetched_at": fetched_at,
        "note": (
            "Generated by scripts/refresh_model_reasoning.py — do not hand-edit. "
            "Effort ladders are per (channel, model); a model absent here is "
            "treated as UNKNOWN (permissive), never as unsupported."
        ),
        "routes": routes,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check", action="store_true",
        help="Do not write; exit 1 if the snapshot differs from upstream.",
    )
    ap.add_argument(
        "--merge-probe", type=Path, default=None, metavar="PROBE_JSON",
        help="Overlay measured ladders from scripts/smoke_reasoning_efforts.py "
             "--out. Measured entries WIN over models.dev, which a live probe "
             "showed wrong in both directions.",
    )
    ap.add_argument("--probe-channel", default="zenmux")
    ap.add_argument("--probed-at", default=None, help="ISO date for provenance.")
    args = ap.parse_args()

    if args.merge_probe:
        # Merge-only: do not re-fetch, so a measured overlay never silently drags
        # in unrelated upstream drift at the same time.
        existing = json.loads(SNAPSHOT.read_text()) if SNAPSHOT.exists() else {}
        probe = json.loads(args.merge_probe.read_text())
        probed_at = args.probed_at or date.today().isoformat()
        merged = merge_probe(existing, probe, args.probe_channel, probed_at)
        merged["probed_at"] = probed_at
        SNAPSHOT.write_text(json.dumps(merged, indent=2) + "\n")
        measured = sum(
            1 for ms in merged["routes"].values()
            for e in ms.values() if e.get("source") == "measured"
        )
        print(f"merged {args.merge_probe} — {measured} measured entries")
        return 0

    payload, sha = _fetch(SOURCE_URL)

    existing = {}
    if SNAPSHOT.exists():
        existing = json.loads(SNAPSHOT.read_text())

    # Keep the recorded date stable when only the date would change, so a no-op
    # refresh produces no diff and `--check` does not fail on the calendar alone.
    fetched_at = existing.get("fetched_at") or date.today().isoformat()
    candidate = build(payload, sha, fetched_at)
    if existing.get("routes") != candidate["routes"]:
        candidate["fetched_at"] = date.today().isoformat()

    same = (
        existing.get("routes") == candidate["routes"]
        and existing.get("source_sha256") == candidate["source_sha256"]
    )
    if args.check:
        if same:
            print("model_reasoning.json is up to date")
            return 0
        changed = existing.get("routes") != candidate["routes"]
        print(
            "model_reasoning.json is STALE — "
            + ("effort ladders changed upstream" if changed
               else "only the upstream sha moved (no ladder change)"),
            file=sys.stderr,
        )
        return 1

    SNAPSHOT.write_text(json.dumps(candidate, indent=2, sort_keys=False) + "\n")
    counts = {ch: len(ms) for ch, ms in candidate["routes"].items()}
    print(f"wrote {SNAPSHOT.relative_to(REPO_ROOT)} — {counts}, source sha256 {sha[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

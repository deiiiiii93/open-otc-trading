#!/usr/bin/env python3
"""Live-probe every configured model against every reasoning-effort level.

Answers the only question that matters for this feature: **does this model, on this
channel, actually accept this effort level?** The vendored models.dev snapshot is a
third-party claim; this measures the gateway's real behaviour and reconciles the two.

Why measuring is not optional: an unsupported `reasoning_effort` is frequently
**accepted and ignored** rather than rejected, so a wrong ladder does not surface as
an error anywhere — it surfaces as "effort had no effect on this model", which reads
like a finding instead of a bug. So the probe records both the HTTP outcome AND the
reasoning-token count, and compares each level against an unset baseline.

Verdicts per (model, level):
    ok        — accepted, and reasoning tokens differ from the unset baseline
    ok-flat   — accepted, but reasoning tokens identical to baseline (accepted and
                possibly ignored; indistinguishable from a no-op at this sample size)
    rejected  — provider/gateway refused it (this is the honest, useful failure)
    error     — transport/quota/other; not evidence about the level

Usage:
    python scripts/smoke_reasoning_efforts.py --models openai/gpt-5.5 --levels low high
    python scripts/smoke_reasoning_efforts.py --all --out /tmp/probe.json
    python scripts/smoke_reasoning_efforts.py --all --toggle-only   # probe the toggle
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

# A prompt short enough that a wrong answer costs nothing and long enough that a
# reasoning model actually thinks. `max_tokens` is generous because several
# reasoning models error outright when the budget cannot fit their thinking.
PROMPT = "What is 17 * 23? Reply with only the number."
MAX_TOKENS = 512
TIMEOUT = 180


def _load_env() -> None:
    env = REPO_ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _post(url: str, key: str, body: dict) -> tuple[int, dict | str]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw
    except Exception as exc:  # transport, timeout, DNS
        return 0, f"{type(exc).__name__}: {exc}"


def _reasoning_tokens(payload: dict) -> int | None:
    usage = payload.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    for key in ("reasoning_tokens", "reasoning"):
        if isinstance(details.get(key), int):
            return details[key]
    if isinstance(usage.get("reasoning_tokens"), int):
        return usage["reasoning_tokens"]
    return None


def probe_anthropic(base_url: str, key: str, model: str, level: str | None) -> dict:
    """Probe the ANTHROPIC wire protocol, which controls effort differently.

    Not `reasoning_effort` but **`output_config.effort`**, a named level
    (low/medium/high/xhigh/max). The older mechanism is `thinking.budget_tokens`,
    which Anthropic is retiring. Models routed with `protocol: anthropic` in this
    repo — including non-Claude ones like glm-5.2 and minimax-m3 — reach the model
    only through this endpoint, so their real ladder can only be measured here.
    """
    body: dict = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": PROMPT}],
    }
    if level is not None:
        body["output_config"] = {"effort": level}

    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/messages",
        data=json.dumps(body).encode(),
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    out: dict = {"model": model, "level": level, "toggle": False, "protocol": "anthropic"}
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:  # noqa: S310
            payload = json.loads(resp.read())
        out["status"] = 200
        out["completion_tokens"] = (payload.get("usage") or {}).get("output_tokens")
        out["outcome"] = "accepted"
    except urllib.error.HTTPError as exc:
        out["status"] = exc.code
        out["error"] = exc.read().decode(errors="replace")[:300]
        out["outcome"] = (
            "rejected" if (400 <= exc.code < 500 and exc.code not in {402, 408, 429})
            else "error"
        )
    except Exception as exc:
        out["status"] = 0
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["outcome"] = "error"
    return out


def probe(base_url: str, key: str, model: str, level: str | None,
          *, toggle: bool = False) -> dict:
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": MAX_TOKENS,
    }
    if toggle:
        # ZenMux's own reasoning object (its docs' `extra_body` form), which is the
        # only way to drive a model whose reasoning is an on/off toggle.
        body["reasoning"] = {"enabled": True}
    elif level is not None:
        body["reasoning_effort"] = level

    status, payload = _post(f"{base_url.rstrip('/')}/chat/completions", key, body)
    out: dict = {"model": model, "level": level, "toggle": toggle, "status": status}
    if status == 200 and isinstance(payload, dict):
        out["reasoning_tokens"] = _reasoning_tokens(payload)
        out["completion_tokens"] = (payload.get("usage") or {}).get("completion_tokens")
        choices = payload.get("choices") or [{}]
        out["text"] = ((choices[0].get("message") or {}).get("content") or "")[:60]
        out["outcome"] = "accepted"
    else:
        msg = payload
        if isinstance(payload, dict):
            err = payload.get("error")
            msg = err.get("message") if isinstance(err, dict) else (err or payload)
        out["error"] = str(msg)[:300]
        # Classify on the STATUS, not the prose. A 4xx means the gateway refused
        # our request body, and the only thing that varies between the baseline and
        # this call is `reasoning_effort` — so it is a rejection whether or not the
        # message happens to name the parameter. `tencent/hy3` returns a bare 400
        # with an EMPTY body for `max` (reproduced), which a text-matching rule
        # scored as an inconclusive error and would have left permissive.
        #
        # 402 / 408 / 429 are account or timing failures, not parameter failures,
        # and 5xx / transport say nothing about the level — those stay inconclusive,
        # because treating them as rejections would silently narrow a real ladder.
        inconclusive = {402, 408, 429}
        out["outcome"] = (
            "rejected"
            if (400 <= status < 500 and status not in inconclusive)
            else "error"
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", nargs="*", help="Model ids (default: from --all).")
    ap.add_argument("--all", action="store_true", help="Every configured model.")
    ap.add_argument("--levels", nargs="*", help="Levels to probe (default: all known).")
    ap.add_argument("--toggle", action="store_true",
                    help="Also probe ZenMux's reasoning:{enabled} toggle form.")
    ap.add_argument("--channel", default="zenmux")
    ap.add_argument(
        "--protocol", choices=["openai", "anthropic"], default="openai",
        help="Wire protocol to probe. 'anthropic' probes output_config.effort on "
             "the /v1/messages endpoint, which is the ONLY route for models pinned "
             "protocol: anthropic (incl. non-Claude ones like glm-5.2).",
    )
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    _load_env()

    from app.services.deep_agent import channel_registry as cr
    from app.services.deep_agent.model_factory import VALID_REASONING_EFFORTS

    registry = cr.load_from_path(REPO_ROOT / "config" / "agent_channels.yaml")
    channel = next((c for c in registry.channels if c.name == args.channel), None)
    if channel is None:
        print(f"no such channel {args.channel!r}", file=sys.stderr)
        return 2
    key = channel.api_key or ""
    if not key:
        print(f"channel {args.channel} has no API key (env var unset)", file=sys.stderr)
        return 2

    ids = list(args.models or [])
    if args.all or not ids:
        # Skip anthropic-protocol models: they are dispatched through a different
        # endpoint entirely and cannot carry reasoning_effort by construction.
        ids = [
            m.id for m in channel.models
            if (m.protocol == "anthropic") == (args.protocol == "anthropic")
        ]
    levels: list[str | None] = [None] + list(args.levels or VALID_REASONING_EFFORTS)

    if args.protocol == "anthropic":
        base = channel.anthropic_base_url or ""
        if not base:
            print("channel has no anthropic_base_url", file=sys.stderr)
            return 2
    else:
        base = channel.base_url

    jobs = [(m, lv, False) for m in ids for lv in levels]
    if args.toggle:
        jobs += [(m, None, True) for m in ids]

    print(f"probing {len(ids)} models x {len(levels)} levels"
          f"{' + toggle' if args.toggle else ''} = {len(jobs)} calls "
          f"on {args.channel}", flush=True)

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(probe_anthropic, base, key, m, lv)
            if args.protocol == "anthropic"
            else pool.submit(probe, base, key, m, lv, toggle=tg)
            for m, lv, tg in jobs
        ]
        for i, fut in enumerate(futures, 1):
            r = fut.result()
            results.append(r)
            tag = "toggle" if r["toggle"] else (r["level"] or "unset")
            print(f"  [{i}/{len(jobs)}] {r['model']:34s} {tag:8s} "
                  f"{r['outcome']:9s} rt={r.get('reasoning_tokens')} "
                  f"{r.get('error','')[:80]}", flush=True)

    # Reconcile: flag levels that differ from the unset baseline vs those that don't.
    baseline = {
        r["model"]: r.get("reasoning_tokens")
        for r in results if r["level"] is None and not r["toggle"]
        and r["outcome"] == "accepted"
    }
    for r in results:
        if r["outcome"] == "accepted" and r["level"] is not None:
            base = baseline.get(r["model"])
            rt = r.get("reasoning_tokens")
            if base is not None and rt is not None and rt == base:
                r["outcome"] = "ok-flat"
            else:
                r["outcome"] = "ok"

    if args.out:
        args.out.write_text(json.dumps(results, indent=2) + "\n")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

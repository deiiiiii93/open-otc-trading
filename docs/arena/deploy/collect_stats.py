"""Fetch the arena access log over ssh and write the readership snapshot.

Stateless by construction: every run re-reads `arena.log*` (all rotated
generations) and recomputes totals from scratch, so stats.json is pure derived
data and losing it costs nothing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from publish import HOST, SSH_KEY  # noqa: E402
from stats import aggregate, to_snapshot  # noqa: E402

REMOTE_LOG_DIR = "/opt/open-slides-zero/runtime/nginx-logs"
REMOTE_LOG = f"{REMOTE_LOG_DIR}/arena.log"
REMOTE_LOG_GLOB = f"{REMOTE_LOG}*"
NGINX_CONTAINER = "open-slides-zero-nginx-1"

ROTATE_BYTES = 50 * 1024 * 1024
ROTATE_KEEP = 4


def _ssh(host: str, key: str, script: str) -> list[str]:
    return [
        "ssh", "-i", key, "-o", "StrictHostKeyChecking=accept-new", host, script,
    ]


def fetch_cmd(host: str, key: str, glob: str = REMOTE_LOG_GLOB) -> list[str]:
    # `cat glob` covers every rotated generation, which is what keeps aggregation
    # stateless — no cursor, no accumulation, no state to corrupt.
    return _ssh(host, key, f"cat {glob}")


def rotate_cmd(host: str, key: str, log: str = REMOTE_LOG, keep: int = ROTATE_KEEP) -> list[str]:
    moves = [f"sudo rm -f {log}.{keep}"]
    for i in range(keep - 1, 0, -1):
        moves.append(f"[ -f {log}.{i} ] && sudo mv {log}.{i} {log}.{i + 1} || true")
    moves.append(f"[ -f {log} ] && sudo mv {log} {log}.1 || true")
    # nginx holds the old fd open after a rename; `reopen` makes it create a
    # fresh file. Without it the rotated file keeps growing and the new one
    # stays empty.
    moves.append(f"sudo docker exec {NGINX_CONTAINER} nginx -s reopen")
    return _ssh(host, key, " ; ".join(moves))


def _size_cmd(host: str, key: str, log: str = REMOTE_LOG) -> list[str]:
    return _ssh(host, key, f"stat -c %s {log} 2>/dev/null || echo 0")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Collect arena readership stats.")
    ap.add_argument("--out", type=Path, default=HERE / "stats.json")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--key", default=SSH_KEY)
    ap.add_argument("--no-rotate", action="store_true", help="skip the size check")
    args = ap.parse_args(argv)

    proc = subprocess.run(
        fetch_cmd(args.host, args.key), capture_output=True, text=True
    )
    if proc.returncode != 0:
        print(
            "no arena access log on the server yet — deploy the log mount first, "
            f"or nothing has been requested since.\n  {proc.stderr.strip()}",
            file=sys.stderr,
        )
        # Deliberately writes NOTHING: an all-zero snapshot is indistinguishable
        # from "nobody read it" once it reaches the renderer.
        return 1

    lines = proc.stdout.splitlines()
    stats = aggregate(lines)
    snapshot = to_snapshot(stats, dt.datetime.now(dt.timezone.utc))
    args.out.write_text(json.dumps(snapshot, indent=2) + "\n")

    print(
        f"wrote {args.out}\n"
        f"  {stats.total_views:,} views, {stats.total_downloads:,} downloads "
        f"from {stats.counted_lines:,} counted lines\n"
        f"  filtered {stats.bot_lines:,} bot and {stats.malformed_lines:,} malformed lines"
    )

    if not args.no_rotate:
        size = subprocess.run(_size_cmd(args.host, args.key), capture_output=True, text=True)
        try:
            current = int(size.stdout.strip() or 0)
        except ValueError:
            current = 0
        if current > ROTATE_BYTES:
            print(f"  log is {current:,} bytes; rotating")
            subprocess.run(rotate_cmd(args.host, args.key), check=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

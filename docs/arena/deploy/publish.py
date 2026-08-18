"""Transport the built site to the server, then prove it is actually serving.

Verification asserts on BODY CONTENT, never on status alone: the open-slides-zero
frontend container answers `try_files $uri $uri/ /index.html`, so before the nginx
alias exists every path under /arena/ returns 200. ABSENT_PROBE is the control — if
a path that cannot exist returns 200, the SPA is still answering and the deploy has
not taken effect.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import urllib.error
import urllib.request
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARENA_DIR = HERE.parent
sys.path.insert(0, str(ARENA_DIR))
sys.path.insert(0, str(HERE))

from build import missing_from_live  # noqa: E402
from manifest import Post, load_manifest  # noqa: E402

HOST = "ubuntu@43.156.158.156"
SSH_KEY = "/Users/fuxinyao/ppt-pro-server/slides.pem"
REMOTE_DIR = "/opt/open-slides-zero/runtime/arena/"
BASE_URL = "https://www.artena.one/arena/"

# Uploaded by hand so the GPT-Image-2 ability-card run could fetch a background by
# public URL. Referenced from nowhere in either repo, so nothing else protects them.
ORPHAN_ASSETS = ("model-ability-card-bg-v1.webp", "model-ability-card-bg-v2.webp")

ABSENT_PROBE = "__deploy_probe_absent__.html"


def rsync_cmd(src: Path, host: str, remote: str, key: str, dry_run: bool) -> list[str]:
    cmd = ["rsync", "-az", "--delete"]
    if dry_run:
        cmd.append("-n")
    cmd += ["-e", f"ssh -i {key} -o StrictHostKeyChecking=accept-new"]
    cmd += [f"{src}/", f"{host}:{remote}"]
    return cmd


def fetch(url: str, timeout: int = 20) -> tuple[int, str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "arena-deploy/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, (e.headers.get("Content-Type", "") if e.headers else ""), e.read()


def verify_live(
    base_url: str, posts: list[Post], assets: tuple[str, ...] = ORPHAN_ASSETS
) -> list[str]:
    """Return failure messages; an empty list means the site is healthy."""
    failures: list[str] = []

    status, _, body = fetch(base_url)
    index = body.decode("utf-8", "replace")
    if status != 200:
        failures.append(f"index: expected 200, got {status}")
    else:
        for name in missing_from_live(index, posts):
            failures.append(f"index: no link to {name}")
        for p in posts:
            # Titles are HTML-escaped on the page, so compare the escaped form — a
            # raw comparison would false-alarm on any title containing & or <.
            if escape(p.title) not in index:
                failures.append(f"index: title missing for {p.html_name}")

    for p in posts:
        url = f"{base_url}{p.html_name}"
        status, _, body = fetch(url)
        text = body.decode("utf-8", "replace")
        if status != 200:
            failures.append(f"{p.html_name}: expected 200, got {status}")
        elif escape(p.title) not in text:
            failures.append(f"{p.html_name}: served page does not contain its title")

    for name in assets:
        status, ctype, _ = fetch(f"{base_url}{name}")
        if status != 200:
            failures.append(f"{name}: expected 200, got {status}")
        elif "image" not in ctype:
            failures.append(f"{name}: expected an image content-type, got {ctype!r}")

    status, _, _ = fetch(f"{base_url}{ABSENT_PROBE}")
    if status != 404:
        failures.append(
            f"{ABSENT_PROBE}: expected 404, got {status} — a catch-all is still "
            "answering, so the static alias is not serving this path"
        )

    return failures


def _posts() -> list[Post]:
    return load_manifest(HERE / "posts.yaml", ARENA_DIR)


def _cmd_publish(args) -> int:
    build_dir = args.build
    if not (build_dir / "index.html").is_file():
        print(f"no built site at {build_dir} — run: deploy.sh build", file=sys.stderr)
        return 1

    cmd = rsync_cmd(build_dir, args.host, args.remote, args.key, args.dry_run)
    print(" ".join(cmd))
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        return proc.returncode
    if args.dry_run:
        print("dry run — nothing transferred, skipping verification")
        return 0
    if args.no_verify:
        print("uploaded; verification skipped (--no-verify)")
        return 0

    failures = verify_live(args.base_url, _posts())
    if failures:
        print("\nVERIFICATION FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"\nverified {args.base_url} — all pages, assets and the 404 probe are healthy")
    return 0


def _cmd_status(args) -> int:
    posts = _posts()
    status, _, body = fetch(args.base_url)
    index = body.decode("utf-8", "replace")
    unpublished = missing_from_live(index, posts)
    print(f"{args.base_url} -> HTTP {status}, {len(posts)} posts in the manifest")
    if unpublished:
        print("NOT on the live index:")
        for name in unpublished:
            print(f"  - {name}")
        return 1
    print("every manifest post is linked from the live index")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Publish the arena blog.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("publish", "status"):
        p = sub.add_parser(name)
        p.add_argument("--base-url", default=BASE_URL)
        if name == "publish":
            p.add_argument("--build", type=Path, default=HERE / "build")
            p.add_argument("--host", default=HOST)
            p.add_argument("--remote", default=REMOTE_DIR)
            p.add_argument("--key", default=SSH_KEY)
            p.add_argument("--dry-run", action="store_true")
            p.add_argument(
                "--no-verify",
                action="store_true",
                help="upload only; used by bootstrap, where the alias does not exist yet",
            )

    args = ap.parse_args(argv)
    return _cmd_publish(args) if args.cmd == "publish" else _cmd_status(args)


if __name__ == "__main__":
    raise SystemExit(main())

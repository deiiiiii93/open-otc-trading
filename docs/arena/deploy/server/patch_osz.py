"""Idempotently add the /arena/ static mount + location to open-slides-zero.

This is the ONE cross-repo change the design needs. It is applied to the local
open-slides-zero checkout, then shipped by that repo's own
scripts/deploy_incremental.sh --service nginx, so local and server stay in sync.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_OSZ = Path("/Users/fuxinyao/open-slides-zero")

ARENA_LOCATION = (HERE / "arena-location.conf").read_text()

ARENA_MOUNT = "      - ./runtime/arena:/var/www/arena:ro\n"
COMPOSE_ANCHOR = "      - ./deploy/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro\n"
NGINX_ANCHOR = "    location / {\n        proxy_pass http://osz_frontend;\n"


class PatchError(RuntimeError):
    """The target file does not look the way this patch expects."""


def patch_nginx(conf_text: str, block: str) -> str:
    if "location /arena/" in conf_text:
        return conf_text
    if NGINX_ANCHOR not in conf_text:
        raise PatchError(
            "nginx config does not contain the expected `location / { proxy_pass "
            "http://osz_frontend;` anchor — refusing to guess where /arena/ goes"
        )
    indented = "".join(
        f"    {line}\n" if line.strip() else "\n" for line in block.rstrip().splitlines()
    )
    return conf_text.replace(NGINX_ANCHOR, f"{indented}\n{NGINX_ANCHOR}", 1)


def patch_compose(compose_text: str) -> str:
    if ARENA_MOUNT.strip() in compose_text:
        return compose_text
    if COMPOSE_ANCHOR not in compose_text:
        raise PatchError(
            "compose.prod.yml does not mount deploy/nginx/default.conf — refusing "
            "to guess which service should receive the arena mount"
        )
    return compose_text.replace(COMPOSE_ANCHOR, COMPOSE_ANCHOR + ARENA_MOUNT, 1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Patch open-slides-zero for /arena/.")
    ap.add_argument("--osz", type=Path, default=DEFAULT_OSZ)
    ap.add_argument("--check", action="store_true", help="report without writing")
    args = ap.parse_args(argv)

    nginx = args.osz / "deploy" / "nginx" / "default.conf"
    compose = args.osz / "compose.prod.yml"
    for p in (nginx, compose):
        if not p.is_file():
            print(f"not found: {p}", file=sys.stderr)
            return 1

    changed = []
    for path, patch in (
        (nginx, lambda t: patch_nginx(t, ARENA_LOCATION)),
        (compose, patch_compose),
    ):
        before = path.read_text()
        after = patch(before)
        if before == after:
            print(f"already patched: {path}")
            continue
        changed.append(path)
        if not args.check:
            path.write_text(after)
            print(f"patched: {path}")
        else:
            print(f"would patch: {path}")

    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    raise SystemExit(main())

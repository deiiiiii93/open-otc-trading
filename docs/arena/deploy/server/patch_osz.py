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

# The managed block is fenced by markers so a LATER run can replace an outdated
# version in place. Without them `patch_nginx` could only ever insert, so a
# config already carrying an old block would silently keep it — which is how the
# add_header security-header regression would have survived its own fix.
BEGIN_MARK = "# >>> arena blog — managed by docs/arena/deploy/server/patch_osz.py >>>"
END_MARK = "# <<< arena blog <<<"

ARENA_MOUNT = "      - ./runtime/arena:/var/www/arena:ro\n"
# Mounting the DIRECTORY is what makes file logging work: it replaces the nginx
# image's `access.log -> /dev/stdout` symlink with a real directory.
LOG_MOUNT = "      - ./runtime/nginx-logs:/var/log/nginx\n"
COMPOSE_ANCHOR = "      - ./deploy/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro\n"
NGINX_ANCHOR = "    location / {\n        proxy_pass http://osz_frontend;\n"


class PatchError(RuntimeError):
    """The target file does not look the way this patch expects."""


def _managed(block: str) -> str:
    """Indent the block to server level and fence it with the markers."""
    body = "".join(
        f"    {line}\n" if line.strip() else "\n" for line in block.rstrip().splitlines()
    )
    return f"    {BEGIN_MARK}\n{body}    {END_MARK}\n"


def _arena_span(lines: list[str]) -> tuple[int, int] | None:
    """Line span of an existing arena block, marked or not; None if absent.

    The unmarked path exists because open-slides-zero gitignores
    deploy/nginx/default.conf ("local deployment packaging"), so a block written
    by an earlier version of this script cannot be reverted with git checkout —
    it has to be replaced in place.
    """
    begin = next((i for i, ln in enumerate(lines) if BEGIN_MARK in ln), None)
    end = next((i for i, ln in enumerate(lines) if END_MARK in ln), None)
    if begin is not None and end is not None:
        return begin, end + 1

    start = next((i for i, ln in enumerate(lines) if "location /arena/" in ln), None)
    if start is None:
        return None

    depth = 0
    for j in range(start, len(lines)):
        depth += lines[j].count("{") - lines[j].count("}")
        if depth == 0 and "{" in lines[j]:
            break
        if depth == 0 and j > start:
            break
    else:
        raise PatchError("unbalanced braces in the existing `location /arena/` block")

    # Absorb the block's own leading comment lines so they are replaced too.
    while start > 0 and lines[start - 1].strip().startswith("#"):
        start -= 1
    return start, j + 1


def patch_nginx(conf_text: str, block: str) -> str:
    managed = _managed(block)
    lines = conf_text.splitlines(keepends=True)

    span = _arena_span(lines)
    if span is not None:
        # Replace in place, so an outdated block is UPDATED rather than kept.
        s_i, e_i = span
        return "".join(lines[:s_i]) + managed + "".join(lines[e_i:])

    if NGINX_ANCHOR not in conf_text:
        raise PatchError(
            "nginx config does not contain the expected `location / { proxy_pass "
            "http://osz_frontend;` anchor — refusing to guess where /arena/ goes"
        )
    return conf_text.replace(NGINX_ANCHOR, f"{managed}\n{NGINX_ANCHOR}", 1)


def patch_compose(compose_text: str) -> str:
    if COMPOSE_ANCHOR not in compose_text:
        raise PatchError(
            "compose.prod.yml does not mount deploy/nginx/default.conf — refusing "
            "to guess which service should receive the arena mount"
        )
    out = compose_text
    for mount in (ARENA_MOUNT, LOG_MOUNT):
        if mount.strip() not in out:
            out = out.replace(COMPOSE_ANCHOR, COMPOSE_ANCHOR + mount, 1)
    return out


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

"""The one-time open-slides-zero patch must be idempotent and surgical.

These tests use a SYNTHETIC nginx config, not the live
open-slides-zero/deploy/nginx/default.conf. That file is gitignored ("local
deployment packaging"), per-environment, and rewritten by this very patcher —
asserting against it is the moving-target mistake CLAUDE.md documents, and it
really did break: after the first cutover the "pristine" fixture already
contained an arena block.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "docs" / "arena" / "deploy" / "server"
sys.path.insert(0, str(SERVER))

import patch_osz as po  # noqa: E402

PRISTINE_NGINX = """upstream osz_frontend {
    server frontend:80;
}

server {
    listen 80;
    server_name artena.one www.artena.one;

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name artena.one www.artena.one;

    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header Content-Security-Policy "default-src 'self'" always;

    location /api/ {
        proxy_pass http://osz_backend/;
    }

    location / {
        proxy_pass http://osz_frontend;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }
}
"""

PRISTINE_COMPOSE = """services:
  nginx:
    image: nginx:1.27-alpine
    restart: unless-stopped
    volumes:
      - ./deploy/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
      - ./runtime/threads:/var/www/threads:ro
"""


def test_patch_nginx_inserts_the_arena_location_before_the_proxy_root():
    out = po.patch_nginx(PRISTINE_NGINX, po.ARENA_LOCATION)
    assert "location /arena/" in out
    assert out.index("location /arena/") < out.index("proxy_pass http://osz_frontend")


def test_patch_nginx_leaves_the_port_80_redirect_alone():
    out = po.patch_nginx(PRISTINE_NGINX, po.ARENA_LOCATION)
    assert out.count("return 301 https://$host$request_uri;") == 1
    assert out.count("location /arena/") == 1


def test_patch_nginx_is_idempotent():
    once = po.patch_nginx(PRISTINE_NGINX, po.ARENA_LOCATION)
    assert po.patch_nginx(once, po.ARENA_LOCATION) == once


def test_patch_nginx_refuses_an_unrecognised_config():
    with pytest.raises(po.PatchError):
        po.patch_nginx("server {\n  listen 80;\n}\n", po.ARENA_LOCATION)


def test_arena_location_uses_expires_not_add_header():
    """`add_header` in a location block DISCARDS every inherited add_header.

    One Cache-Control line silently dropped CSP, HSTS, X-Frame-Options, nosniff,
    Referrer-Policy and Permissions-Policy from /arena/ in production, and the
    deploy verifier still called it healthy. `expires` sets Cache-Control
    without triggering nginx's replacement rule.

    Checks DIRECTIVE lines only — the block's comment explains the trap and so
    legitimately contains the words `add_header`.
    """
    directives = [
        ln for ln in po.ARENA_LOCATION.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert not any("add_header" in ln for ln in directives), directives
    assert any("expires" in ln for ln in directives)


def test_patch_nginx_replaces_an_unmarked_block():
    """open-slides-zero gitignores this file, so git checkout cannot revert it."""
    unmarked = PRISTINE_NGINX.replace(
        po.NGINX_ANCHOR,
        "    # arena\n    location /arena/ {\n        alias /var/www/arena/;\n"
        '        add_header Cache-Control "public, max-age=300";\n    }\n\n'
        + po.NGINX_ANCHOR,
        1,
    )
    assert "add_header Cache-Control" in unmarked

    fixed = po.patch_nginx(unmarked, po.ARENA_LOCATION)
    assert fixed.count("location /arena/") == 1
    assert "add_header Cache-Control" not in fixed
    assert fixed.count(po.BEGIN_MARK) == 1
    assert fixed.count("{") == fixed.count("}")


def test_patch_nginx_replaces_a_stale_managed_block():
    stale = po.patch_nginx(
        PRISTINE_NGINX,
        'location /arena/ {\n    alias /var/www/arena/;\n'
        '    add_header Cache-Control "public, max-age=300";\n}\n',
    )
    assert "add_header Cache-Control" in stale

    fixed = po.patch_nginx(stale, po.ARENA_LOCATION)
    assert "add_header Cache-Control" not in fixed
    assert "expires" in fixed
    assert fixed.count("location /arena/") == 1
    assert fixed.count(po.BEGIN_MARK) == 1


def test_managed_block_is_delimited_by_markers():
    out = po.patch_nginx(PRISTINE_NGINX, po.ARENA_LOCATION)
    assert out.index(po.BEGIN_MARK) < out.index("location /arena/")
    assert out.index("location /arena/") < out.index(po.END_MARK)


def test_patched_nginx_is_syntactically_balanced():
    """A stray brace would take the whole site down on reload."""
    out = po.patch_nginx(PRISTINE_NGINX, po.ARENA_LOCATION)
    assert out.count("{") == out.count("}")


def test_patch_compose_adds_the_arena_mount_to_nginx_only():
    out = po.patch_compose(PRISTINE_COMPOSE)
    assert out.count("./runtime/arena:/var/www/arena:ro") == 1
    assert out.index("./runtime/arena") > out.index("deploy/nginx/default.conf")


def test_patch_compose_is_idempotent():
    once = po.patch_compose(PRISTINE_COMPOSE)
    assert po.patch_compose(once) == once


def test_patch_compose_refuses_when_the_anchor_mount_is_gone():
    with pytest.raises(po.PatchError):
        po.patch_compose("services:\n  nginx:\n    image: nginx\n")


@pytest.mark.skipif(
    not (Path("/Users/fuxinyao/open-slides-zero") / "deploy/nginx/default.conf").is_file(),
    reason="open-slides-zero checkout not present",
)
def test_live_config_still_carries_the_anchor_this_patch_depends_on():
    """Smoke check only — never assert this gitignored file's full contents."""
    live = (Path("/Users/fuxinyao/open-slides-zero") / "deploy/nginx/default.conf").read_text()
    assert po.NGINX_ANCHOR in live

"""The one-time open-slides-zero patch must be idempotent and surgical."""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "docs" / "arena" / "deploy" / "server"
sys.path.insert(0, str(SERVER))

import patch_osz as po  # noqa: E402

OSZ = Path("/Users/fuxinyao/open-slides-zero")
NGINX = OSZ / "deploy" / "nginx" / "default.conf"
COMPOSE = OSZ / "compose.prod.yml"

pytestmark = pytest.mark.skipif(
    not NGINX.is_file(), reason="open-slides-zero checkout not present"
)


def test_patch_nginx_inserts_the_arena_location_before_the_proxy_root():
    out = po.patch_nginx(NGINX.read_text(), po.ARENA_LOCATION)
    assert "location /arena/" in out
    assert out.index("location /arena/") < out.index("proxy_pass http://osz_frontend")


def test_patch_nginx_leaves_the_port_80_redirect_alone():
    original = NGINX.read_text()
    out = po.patch_nginx(original, po.ARENA_LOCATION)
    assert out.count("return 301 https://$host$request_uri;") == 1
    assert out.count("location /arena/") == 1


def test_patch_nginx_is_idempotent():
    once = po.patch_nginx(NGINX.read_text(), po.ARENA_LOCATION)
    assert po.patch_nginx(once, po.ARENA_LOCATION) == once


def test_patch_nginx_refuses_an_unrecognised_config():
    with pytest.raises(po.PatchError):
        po.patch_nginx("server {\n  listen 80;\n}\n", po.ARENA_LOCATION)


def test_patch_compose_adds_the_arena_mount_to_nginx_only():
    out = po.patch_compose(COMPOSE.read_text())
    assert "./runtime/arena:/var/www/arena:ro" in out
    assert out.count("./runtime/arena:/var/www/arena:ro") == 1
    # the mount must land inside the nginx service, after its existing mounts
    assert out.index("./runtime/arena") > out.index("deploy/nginx/default.conf")


def test_patch_compose_is_idempotent():
    once = po.patch_compose(COMPOSE.read_text())
    assert po.patch_compose(once) == once


def test_patch_compose_refuses_when_the_anchor_mount_is_gone():
    with pytest.raises(po.PatchError):
        po.patch_compose("services:\n  nginx:\n    image: nginx\n")


def test_patched_nginx_is_syntactically_balanced():
    """A stray brace would take the whole site down on reload."""
    out = po.patch_nginx(NGINX.read_text(), po.ARENA_LOCATION)
    assert out.count("{") == out.count("}")

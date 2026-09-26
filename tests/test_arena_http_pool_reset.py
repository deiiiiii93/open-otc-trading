"""The arena runs every step in its own ``asyncio.run`` loop; the LLM clients'
cached async connection pool must not carry a dead loop's connection into the
next step (run #143 thread 1128: ``RuntimeError: Event loop is closed``)."""
from __future__ import annotations

import asyncio
import importlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.services.arena import runner


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, so the pool keeps the connection

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("content-length", 0)))
        body = b"{}"
        self.send_response(200)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def local_server(monkeypatch):
    # Mirror the desk's real network shape: requests leave through a proxy-env
    # client (the arena reaches ZenMux via a local proxy). Only with proxy mounts
    # does httpx hand the next loop the dead connection; a bare client drops it.
    # NO_PROXY routes this test's own traffic straight to the local server.
    for var in ("ALL_PROXY", "all_proxy", "http_proxy", "https_proxy", "no_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _post_in_fresh_loop(base_url: str) -> int:
    from langchain_openai.chat_models._client_utils import _get_default_async_httpx_client

    async def post() -> int:
        client = _get_default_async_httpx_client(base_url, 60.0)
        return (await client.post("/v1/chat", json={})).status_code

    return asyncio.run(post())


def test_shared_pool_breaks_the_next_loop_without_the_reset(local_server):
    runner._reset_async_http_pools()
    assert _post_in_fresh_loop(local_server) == 200
    with pytest.raises(RuntimeError, match="Event loop is closed"):
        _post_in_fresh_loop(local_server)
    runner._reset_async_http_pools()


def test_reset_before_each_loop_keeps_every_step_alive(local_server):
    for _ in range(3):
        runner._reset_async_http_pools()
        assert _post_in_fresh_loop(local_server) == 200


@pytest.mark.parametrize("module_name,attr", runner._ASYNC_HTTP_CLIENT_CACHES)
def test_cached_client_factories_still_exist(module_name, attr):
    # Private library names: an upgrade that renames one must fail here, not
    # silently turn the reset into a no-op.
    factory = getattr(importlib.import_module(module_name), attr)
    assert callable(factory.cache_clear)

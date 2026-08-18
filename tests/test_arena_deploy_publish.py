"""Transport argument construction, and verification that asserts on CONTENT.

The frontend container answers `try_files $uri $uri/ /index.html`, so a 200 proves
nothing. verify_live must read bodies, and must require a 404 on an absent path.
"""
import datetime as dt
import functools
import http.server
import socketserver
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "docs" / "arena" / "deploy"
sys.path.insert(0, str(REPO / "docs" / "arena"))
sys.path.insert(0, str(DEPLOY))

import manifest as m  # noqa: E402
import publish as pub  # noqa: E402

POST = m.Post(
    file="2026-08-18-run110-luna.md",
    date=dt.date(2026, 8, 18),
    tags=("research",),
    title="Reasoning effort study",
    blurb="Effort is a step at low.",
)


@pytest.fixture
def served(tmp_path):
    """Serve tmp_path over HTTP; yields (base_url, root)."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        yield f"http://127.0.0.1:{httpd.server_address[1]}/", tmp_path
        httpd.shutdown()


def _good_site(root: Path) -> None:
    root.joinpath("index.html").write_text(
        '<a href="./2026-08-18-run110-luna.html">Reasoning effort study</a>'
    )
    root.joinpath("2026-08-18-run110-luna.html").write_text("<h1>Reasoning effort study</h1>")
    root.joinpath("model-ability-card-bg-v1.webp").write_bytes(b"RIFF....WEBP")
    root.joinpath("model-ability-card-bg-v2.webp").write_bytes(b"RIFF....WEBP")


def test_rsync_cmd_uses_archive_delete_and_the_ssh_key():
    cmd = pub.rsync_cmd(Path("/tmp/build"), "u@h", "/opt/arena/", "/k.pem", dry_run=False)
    assert cmd[0] == "rsync"
    assert "--delete" in cmd
    assert "-n" not in cmd
    # verbose is required: bootstrap.sh's deletion guard greps "deleting <path>",
    # which rsync only prints when verbose.
    assert "-azv" in cmd
    assert any("/k.pem" in part for part in cmd)
    assert cmd[-2].endswith("/"), "source must end in / or rsync nests a directory"
    assert cmd[-1] == "u@h:/opt/arena/"


def test_rsync_cmd_dry_run_adds_n():
    cmd = pub.rsync_cmd(Path("/tmp/build"), "u@h", "/opt/arena/", "/k.pem", dry_run=True)
    assert "-n" in cmd


def test_publish_no_verify_uploads_but_does_not_verify(tmp_path, monkeypatch):
    """bootstrap needs this: at upload time the nginx alias does not exist yet."""
    build = tmp_path / "build"
    build.mkdir()
    (build / "index.html").write_text("<p>site</p>")

    calls = []

    class _Done:
        returncode = 0

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Done()

    def _must_not_run(*args, **kwargs):
        raise AssertionError("verify_live must not run under --no-verify")

    monkeypatch.setattr(pub.subprocess, "run", _fake_run)
    monkeypatch.setattr(pub, "verify_live", _must_not_run)

    assert pub.main(["publish", "--build", str(build), "--no-verify"]) == 0
    assert calls and calls[0][0] == "rsync"


def test_publish_refuses_when_there_is_no_build(tmp_path, monkeypatch):
    monkeypatch.setattr(pub, "verify_live", lambda *a, **k: [])
    assert pub.main(["publish", "--build", str(tmp_path / "absent")]) == 1


def test_verify_live_passes_on_a_healthy_site(served):
    base, root = served
    _good_site(root)
    assert pub.verify_live(base, [POST]) == []


def test_verify_live_fails_when_a_post_is_missing_from_the_index(served):
    base, root = served
    _good_site(root)
    root.joinpath("index.html").write_text("<p>nothing here</p>")
    failures = pub.verify_live(base, [POST])
    assert any("index" in f for f in failures)


def test_verify_live_fails_when_a_post_page_lacks_its_title(served):
    base, root = served
    _good_site(root)
    root.joinpath("2026-08-18-run110-luna.html").write_text("<h1>Wrong document</h1>")
    failures = pub.verify_live(base, [POST])
    assert any("2026-08-18-run110-luna.html" in f for f in failures)


def test_verify_live_fails_when_an_orphan_asset_is_gone(served):
    base, root = served
    _good_site(root)
    root.joinpath("model-ability-card-bg-v2.webp").unlink()
    failures = pub.verify_live(base, [POST])
    assert any("model-ability-card-bg-v2.webp" in f for f in failures)


def test_verify_live_requires_a_404_on_an_absent_path(served):
    """The whole point: an SPA fallback answering 200 everywhere must be caught."""
    base, root = served
    _good_site(root)
    assert pub.verify_live(base, [POST]) == []

    # Simulate the catch-all by making the probe path resolve to a real file.
    root.joinpath(pub.ABSENT_PROBE).write_text("SPA fallback served this")
    failures = pub.verify_live(base, [POST])
    assert any("404" in f for f in failures)


def test_verify_live_compares_escaped_titles(served):
    """Titles are HTML-escaped on the page; a raw compare false-alarms on & and <."""
    base, root = served
    tricky = m.Post(
        file="x.md", date=dt.date(2026, 8, 18), tags=("research",),
        title="Risk & <agent> performance", blurb="b",
    )
    root.joinpath("index.html").write_text(
        '<a href="./x.html">Risk &amp; &lt;agent&gt; performance</a>'
    )
    root.joinpath("x.html").write_text("<h1>Risk &amp; &lt;agent&gt; performance</h1>")
    assert pub.verify_live(base, [tricky], assets=()) == []

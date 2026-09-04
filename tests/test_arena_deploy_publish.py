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
    assert pub.verify_live(base, [POST], security_headers=()) == []


def test_verify_live_fails_when_a_post_is_missing_from_the_index(served):
    base, root = served
    _good_site(root)
    root.joinpath("index.html").write_text("<p>nothing here</p>")
    failures = pub.verify_live(base, [POST], security_headers=())
    assert any("index" in f for f in failures)


def test_verify_live_fails_when_a_post_page_lacks_its_title(served):
    base, root = served
    _good_site(root)
    root.joinpath("2026-08-18-run110-luna.html").write_text("<h1>Wrong document</h1>")
    failures = pub.verify_live(base, [POST], security_headers=())
    assert any("2026-08-18-run110-luna.html" in f for f in failures)


def test_verify_live_fails_when_an_orphan_asset_is_gone(served):
    base, root = served
    _good_site(root)
    root.joinpath("model-ability-card-bg-v2.webp").unlink()
    failures = pub.verify_live(base, [POST], security_headers=())
    assert any("model-ability-card-bg-v2.webp" in f for f in failures)


def test_verify_live_requires_a_404_on_an_absent_path(served):
    """The whole point: an SPA fallback answering 200 everywhere must be caught."""
    base, root = served
    _good_site(root)
    assert pub.verify_live(base, [POST], security_headers=()) == []

    # Simulate the catch-all by making the probe path resolve to a real file.
    root.joinpath(pub.ABSENT_PROBE).write_text("SPA fallback served this")
    failures = pub.verify_live(base, [POST], security_headers=())
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
    assert pub.verify_live(base, [tricky], assets=(), security_headers=()) == []


SECURITY_HEADERS = (
    "content-security-policy",
    "strict-transport-security",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
    "permissions-policy",
)


def test_verify_live_flags_missing_security_headers(monkeypatch):
    """A location-level add_header silently drops every inherited one.

    The cutover shipped exactly that defect and the verifier reported a clean
    deploy, because it only ever looked at status codes and body text.
    """
    def _fake_fetch(url, timeout=20):
        body = b"<h1>Reasoning effort study</h1>2026-08-18-run110-luna.html"
        if url.endswith(pub.ABSENT_PROBE):
            return 404, "", b""
        return 200, "text/html", body

    monkeypatch.setattr(pub, "fetch", _fake_fetch)
    monkeypatch.setattr(pub, "fetch_headers", lambda url, timeout=20: {"cache-control": "public"})

    failures = pub.verify_live("https://example.test/arena/", [POST], assets=())
    for h in SECURITY_HEADERS:
        assert any(h in f for f in failures), f"{h} not reported missing"


def test_verify_live_passes_when_security_headers_are_present(monkeypatch):
    def _fake_fetch(url, timeout=20):
        body = b"<h1>Reasoning effort study</h1>2026-08-18-run110-luna.html"
        if url.endswith(pub.ABSENT_PROBE):
            return 404, "", b""
        return 200, "text/html", body

    monkeypatch.setattr(pub, "fetch", _fake_fetch)
    monkeypatch.setattr(
        pub, "fetch_headers", lambda url, timeout=20: {h: "set" for h in SECURITY_HEADERS}
    )
    assert pub.verify_live("https://example.test/arena/", [POST], assets=()) == []


# --------------------------------------------------------------------------
# The leaderboard page
# --------------------------------------------------------------------------

LEADERBOARD = (
    '<main class="boards">\n'
    '<section class="wf" id="risk-manager-control-day"><h2>'
    "<code>risk-manager-control-day</code></h2></section>\n"
    '<section class="wf" id="ops-settlement-day"><h2>'
    "<code>ops-settlement-day</code></h2></section>\n"
    "</main>\n"
)


def test_workflow_anchors_are_read_from_the_built_page():
    assert pub.workflow_anchors(LEADERBOARD) == [
        "risk-manager-control-day", "ops-settlement-day",
    ]
    assert pub.workflow_anchors("<p>no leaderboard here</p>") == []


def test_verify_live_checks_every_workflow_section_actually_serves(served):
    base, root = served
    _good_site(root)
    root.joinpath("leaderboard.html").write_text(LEADERBOARD)

    anchors = pub.workflow_anchors(LEADERBOARD)
    assert pub.verify_live(base, [POST], security_headers=(),
                           workflow_anchors=anchors) == []

    # A 200 proves nothing under the SPA catch-all, so the body must be read:
    # here the page serves, but one section silently did not ship.
    root.joinpath("leaderboard.html").write_text(
        LEADERBOARD.replace('id="ops-settlement-day"', 'id="something-else"')
    )
    failures = pub.verify_live(base, [POST], security_headers=(),
                               workflow_anchors=anchors)
    assert any("ops-settlement-day" in f for f in failures)


def test_verify_live_skips_the_leaderboard_when_none_was_built(served):
    base, root = served
    _good_site(root)
    assert not root.joinpath("leaderboard.html").exists()
    assert pub.verify_live(base, [POST], security_headers=()) == []


def test_verify_live_checks_the_model_cards_page_the_same_way(served):
    base, root = served
    _good_site(root)
    root.joinpath("models.html").write_text(LEADERBOARD)
    anchors = pub.workflow_anchors(LEADERBOARD)

    assert pub.verify_live(base, [POST], security_headers=(),
                           model_anchors=anchors) == []

    root.joinpath("models.html").write_text(
        LEADERBOARD.replace('id="ops-settlement-day"', 'id="gone"')
    )
    failures = pub.verify_live(base, [POST], security_headers=(),
                               model_anchors=anchors)
    assert any("models.html" in f and "ops-settlement-day" in f for f in failures)


ROSTER = (
    '<section class="wf" id="ranked"><table class="roster"><tbody>'
    '<tr><td class="model"><a href="models/gpt-5-6-terra.html">gpt-5-6-terra</a>'
    "</td></tr>"
    '<tr><td class="model"><a href="models/grok-4-6.html">grok-4-6</a></td></tr>'
    "</tbody></table></section>"
)


def test_verify_live_requires_every_model_page_the_roster_links(served):
    """The roster's whole job is routing to the per-model pages, so a roster
    whose links 404 is worse than no roster. Read from the BUILT page, like the
    workflow anchors, so the check verifies what was uploaded."""
    base, root = served
    _good_site(root)
    root.joinpath("models.html").write_text(ROSTER)
    root.joinpath("models").mkdir()
    root.joinpath("models", "gpt-5-6-terra.html").write_text("<h1>gpt-5-6-terra</h1>")

    assert pub.model_pages(ROSTER) == [
        "models/gpt-5-6-terra.html", "models/grok-4-6.html"
    ]

    failures = pub.verify_live(base, [POST], security_headers=(),
                               model_pages=pub.model_pages(ROSTER))
    assert any("models/grok-4-6.html" in f for f in failures)
    assert not any("gpt-5-6-terra" in f for f in failures)


def test_a_served_model_page_must_carry_its_own_model_id(served):
    """A 200 proves nothing under the SPA catch-all — the same reason the post
    check compares titles rather than status codes."""
    base, root = served
    _good_site(root)
    root.joinpath("models.html").write_text(ROSTER)
    root.joinpath("models").mkdir()
    for name in ("gpt-5-6-terra", "grok-4-6"):
        root.joinpath("models", f"{name}.html").write_text("<h1>somebody else</h1>")

    failures = pub.verify_live(base, [POST], security_headers=(),
                               model_pages=pub.model_pages(ROSTER))
    assert any("gpt-5-6-terra" in f for f in failures)


def test_verify_live_probes_an_absent_path_inside_the_models_directory(served):
    """The flat probe proves the alias answers instead of the SPA at the top
    level. Model pages live a level down, and `try_files $uri $uri/ =404`
    resolves a nested path by a different branch — so the control has to be run
    at that depth too, not inferred from the shallow one."""
    base, root = served
    _good_site(root)
    root.joinpath("models").mkdir()
    root.joinpath("models", pub.ABSENT_PROBE).write_text("<h1>the SPA answered</h1>")

    failures = pub.verify_live(base, [POST], security_headers=())

    assert any(f"models/{pub.ABSENT_PROBE}" in f for f in failures)

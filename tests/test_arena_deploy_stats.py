"""Aggregation is pure and table-driven: these numbers go on a public page."""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "docs" / "arena" / "deploy"))

import stats as st  # noqa: E402

UA_HUMAN = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
UA_BOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


def line(path, status=200, referer="-", ua=UA_HUMAN):
    return (
        f'203.0.113.7 - - [18/Aug/2026:13:30:58 +0800] "GET {path} HTTP/1.1" '
        f'{status} 1234 "{referer}" "{ua}"'
    )


def test_parse_line_extracts_the_fields_we_aggregate_on():
    got = st.parse_line(line("/arena/x.html", 200, "https://news.ycombinator.com/", UA_HUMAN))
    assert got is not None
    assert got.path == "/arena/x.html"
    assert got.status == 200
    assert got.referer == "https://news.ycombinator.com/"
    assert got.user_agent == UA_HUMAN


def test_parse_line_strips_the_query_string():
    got = st.parse_line(line("/arena/x.html?cb=123"))
    assert got is not None and got.path == "/arena/x.html"


def test_parse_line_returns_none_for_a_malformed_line():
    assert st.parse_line("not a log line at all") is None
    assert st.parse_line("") is None


@pytest.mark.parametrize("ua,expected", [
    (UA_BOT, True),
    ("bingbot/2.0", True),
    ("Mozilla/5.0 (compatible; AhrefsBot/7.0)", True),
    ("curl/8.4.0", True),
    ("python-requests/2.31.0", True),
    (UA_HUMAN, False),
    ("", True),
])
def test_is_bot(ua, expected):
    assert st.is_bot(ua) is expected


@pytest.mark.parametrize("path,expected", [
    ("/arena/", ("page", "index")),
    ("/arena/index.html", ("page", "index")),
    ("/arena/about.html", ("page", "about")),
    ("/arena/2026-08-18-run110-luna.html", ("page", "2026-08-18-run110-luna")),
    ("/arena/2026-08-18-run110-luna.pdf", ("pdf", "2026-08-18-run110-luna")),
    ("/arena/2026-08-18-run110-luna.md", ("markdown", "2026-08-18-run110-luna")),
    ("/arena/model-ability-card-bg-v1.webp", ("asset", "model-ability-card-bg-v1")),
    ("/arena/cards/run104/hero.png", ("asset", "hero")),
    ("/arena/cards/run104/", None),   # a bare directory has no stem
    ("/not-arena/x.html", None),
])
def test_classify(path, expected):
    assert st.classify(path) == expected


@pytest.mark.parametrize("referer,expected", [
    ("https://news.ycombinator.com/item?id=1", "news.ycombinator.com"),
    ("https://t.co/abc", "t.co"),
    ("-", None),
    ("", None),
    ("https://www.artena.one/arena/", None),   # self-referral
    ("https://artena.one/arena/x.html", None),
    ("not a url", None),
])
def test_referrer_host(referer, expected):
    assert st.referrer_host(referer) == expected


def test_aggregate_counts_views_and_downloads_separately():
    got = st.aggregate([
        line("/arena/2026-08-18-run110-luna.html"),
        line("/arena/2026-08-18-run110-luna.html"),
        line("/arena/2026-08-18-run110-luna.pdf"),
        line("/arena/2026-08-18-run110-luna.md"),
    ])
    assert got.views["2026-08-18-run110-luna"] == 2
    assert got.downloads["2026-08-18-run110-luna"] == 2   # pdf + md
    assert got.total_views == 2
    assert got.total_downloads == 2


def test_aggregate_excludes_bots_and_counts_them():
    got = st.aggregate([
        line("/arena/x.html", ua=UA_HUMAN),
        line("/arena/x.html", ua=UA_BOT),
        line("/arena/x.html", ua="bingbot/2.0"),
    ])
    assert got.views["x"] == 1
    assert got.bot_lines == 2


def test_aggregate_counts_only_200_and_304():
    got = st.aggregate([
        line("/arena/x.html", 200),
        line("/arena/x.html", 304),
        line("/arena/x.html", 404),
        line("/arena/x.html", 500),
        line("/arena/x.html", 301),
    ])
    assert got.views["x"] == 2


def test_aggregate_counts_malformed_lines_rather_than_dropping_them():
    got = st.aggregate([line("/arena/x.html"), "garbage", ""])
    assert got.views["x"] == 1
    assert got.malformed_lines == 2


def test_aggregate_ignores_paths_outside_arena():
    got = st.aggregate([line("/arena/x.html"), line("/index.html"), line("/api/health")])
    assert got.total_views == 1


def test_aggregate_assets_count_as_neither_views_nor_downloads():
    got = st.aggregate([line("/arena/cards/run104/hero.png")])
    assert got.total_views == 0 and got.total_downloads == 0


def test_aggregate_tallies_referrer_hosts_excluding_self():
    got = st.aggregate([
        line("/arena/x.html", referer="https://news.ycombinator.com/item?id=1"),
        line("/arena/y.html", referer="https://news.ycombinator.com/item?id=2"),
        line("/arena/x.html", referer="https://t.co/abc"),
        line("/arena/x.html", referer="https://www.artena.one/arena/"),
        line("/arena/x.html", referer="-"),
    ])
    assert got.referrers == {"news.ycombinator.com": 2, "t.co": 1}


def test_top_referrers_is_sorted_by_count_then_name():
    got = st.aggregate(
        [line("/arena/x.html", referer="https://b.example/")] * 3
        + [line("/arena/x.html", referer="https://a.example/")] * 3
        + [line("/arena/x.html", referer="https://c.example/")]
    )
    assert st.top_referrers(got, n=2) == [("a.example", 3), ("b.example", 3)]


def test_aggregate_of_nothing_is_empty_not_an_error():
    got = st.aggregate([])
    assert got.total_views == 0 and got.views == {} and got.counted_lines == 0


import datetime as dt
import json


NOW = dt.datetime(2026, 8, 18, 12, 0, tzinfo=dt.timezone.utc)


def _snapshot(tmp_path, generated_at=NOW, **override):
    agg = st.aggregate([line("/arena/x.html"), line("/arena/x.pdf"), line("/arena/y.html", ua=UA_BOT)])
    snap = st.to_snapshot(agg, generated_at)
    snap.update(override)
    p = tmp_path / "stats.json"
    p.write_text(json.dumps(snap))
    return p


def test_to_snapshot_carries_only_aggregate_fields():
    agg = st.aggregate([line("/arena/x.html", referer="https://news.ycombinator.com/")])
    snap = st.to_snapshot(agg, NOW)
    assert snap["version"] == st.SNAPSHOT_VERSION
    assert snap["generated_at"] == NOW.isoformat()
    assert snap["views"] == {"x": 1}
    assert snap["referrers"] == [["news.ycombinator.com", 1]]
    blob = json.dumps(snap)
    assert "203.0.113.7" not in blob, "no IPs may reach the snapshot"
    assert "Mozilla" not in blob, "no user agents may reach the snapshot"


def test_load_snapshot_returns_the_payload_when_fresh(tmp_path):
    p = _snapshot(tmp_path)
    got = st.load_snapshot(p, now=NOW + dt.timedelta(days=1))
    assert got is not None and got["views"]["x"] == 1


def test_load_snapshot_returns_none_when_stale(tmp_path):
    """Stale MUST render nothing, never zeros."""
    p = _snapshot(tmp_path)
    assert st.load_snapshot(p, now=NOW + dt.timedelta(days=15)) is None
    assert st.load_snapshot(p, now=NOW + dt.timedelta(days=13)) is not None


def test_load_snapshot_returns_none_when_absent(tmp_path):
    assert st.load_snapshot(tmp_path / "nope.json", now=NOW) is None


def test_load_snapshot_returns_none_when_unparseable(tmp_path):
    p = tmp_path / "stats.json"
    p.write_text("{not json")
    assert st.load_snapshot(p, now=NOW) is None


def test_load_snapshot_returns_none_on_an_unknown_version(tmp_path):
    p = _snapshot(tmp_path, version=99)
    assert st.load_snapshot(p, now=NOW) is None


def test_load_snapshot_returns_none_when_generated_at_is_missing_or_bad(tmp_path):
    assert st.load_snapshot(_snapshot(tmp_path, generated_at=NOW), now=NOW) is not None
    p = _snapshot(tmp_path)
    data = json.loads(p.read_text())
    del data["generated_at"]
    p.write_text(json.dumps(data))
    assert st.load_snapshot(p, now=NOW) is None


import collect_stats as cs  # noqa: E402


def test_fetch_cmd_cats_every_rotated_generation():
    cmd = cs.fetch_cmd("u@h", "/k.pem", "/logs/arena.log*")
    assert cmd[0] == "ssh"
    assert any("/k.pem" in part for part in cmd)
    assert cmd[-2] == "u@h"
    # `cat arena.log*` must include rotated files, so aggregation stays stateless
    assert "arena.log*" in cmd[-1]
    assert "cat" in cmd[-1]


def test_rotate_cmd_keeps_a_bounded_number_of_generations():
    cmd = cs.rotate_cmd("u@h", "/k.pem", "/logs/arena.log", keep=4)
    body = cmd[-1]
    assert "arena.log.4" in body           # oldest generation is dropped
    assert "nginx -s reopen" in body       # nginx must reopen after the mv
    assert "arena.log.1" in body


def test_rotate_threshold_is_fifty_megabytes():
    assert cs.ROTATE_BYTES == 50 * 1024 * 1024
    assert cs.ROTATE_KEEP == 4


def test_main_writes_no_snapshot_when_the_log_is_absent(tmp_path, monkeypatch):
    """An all-zero snapshot is indistinguishable from 'nobody read it'."""
    out = tmp_path / "stats.json"

    class _Missing:
        returncode = 1
        stdout = ""
        stderr = "cat: no such file"

    monkeypatch.setattr(cs.subprocess, "run", lambda *a, **k: _Missing())
    assert cs.main(["--out", str(out)]) == 1
    assert not out.exists()


def test_main_writes_a_snapshot_from_fetched_lines(tmp_path, monkeypatch):
    out = tmp_path / "stats.json"
    log = "\n".join([
        line("/arena/2026-08-18-run110-luna.html"),
        line("/arena/2026-08-18-run110-luna.html", ua=UA_BOT),
        line("/arena/2026-08-18-run110-luna.pdf"),
    ])

    class _Ok:
        returncode = 0
        stdout = log
        stderr = ""

    monkeypatch.setattr(cs.subprocess, "run", lambda *a, **k: _Ok())
    assert cs.main(["--out", str(out), "--no-rotate"]) == 0

    snap = json.loads(out.read_text())
    assert snap["views"]["2026-08-18-run110-luna"] == 1
    assert snap["downloads"]["2026-08-18-run110-luna"] == 1
    assert snap["bot_lines"] == 1
    assert "generated_at" in snap

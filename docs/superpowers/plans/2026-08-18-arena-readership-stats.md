# Arena Readership Counters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show honest, bot-filtered readership counters on
<https://www.artena.one/arena/>, derived from nginx access logs with no
JavaScript, no third party, and no CSP change.

**Architecture:** nginx writes a `/arena/`-scoped access log to a mounted volume.
`deploy.sh stats` fetches it over ssh and aggregates it locally with a pure
Python parser into `stats.json`. `build` reads that snapshot and renders a
READERSHIP rail card plus per-post view counts — or renders nothing at all when
the snapshot is missing or stale.

**Tech Stack:** Python 3.11 stdlib only (no new dependencies), bash, ssh, nginx.

**Spec:** `docs/superpowers/specs/2026-08-18-arena-readership-stats-design.md`

## Global Constraints

- **No new dependencies**, local or on the server. `stats.py` is stdlib-only.
  GoAccess was considered and deliberately dropped.
- **Never publish raw log data.** `stats.json` carries only path→count,
  referrer-host→count, timestamps and filter counts. No IPs, no user agents, no
  full referrer URLs.
- **Absence renders NOTHING, never zeros.** Missing / unparseable / older than
  `STATS_MAX_AGE_DAYS = 14` ⇒ no counter markup at all. A "0 views" that means
  "not measured" is the `empty` vs `unavailable` failure this repo already fights.
- **`as of <date>` is when `stats` last ran**, never `last_seen` — the newest
  observed request would overstate freshness after any crawler hit.
- **Only `status` 200 and 304 count**, and known crawler user-agents are excluded.
  The filtered fraction is recorded and displayed, not hidden.
- **Per-post counts are page views only.** Downloads are a separate rail total,
  so one PDF fetch cannot read as a page view.
- **Rotation:** 50 MB threshold, 4 generations, always parse `arena.log*` together
  so aggregation stays stateless and fully recomputable.
- **NEVER use `add_header` in the `/arena/` location** — it discards all six
  inherited security headers. `access_log` and `expires` are safe; they are not
  `add_header`.
- **An nginx config edit needs `up -d --force-recreate nginx`.** The single-file
  bind mount pins an inode and `tar` replaces the file, so `up -d` prints
  "Running" and even `nginx -s reload` re-reads the stale inode.
- **Run tests as** `.venv/bin/python -m pytest` from the repo root. Test modules
  insert `docs/arena/deploy` on `sys.path`; do NOT add it to the global pythonpath.
- **Never assert against `open-slides-zero/deploy/nginx/default.conf`** in tests —
  it is gitignored, per-environment, and rewritten by our own patcher. Use a
  synthetic config, per `tests/test_arena_deploy_server_patch.py`.

---

### Task 1: The log parser (pure, stdlib-only)

**Files:**
- Create: `docs/arena/deploy/stats.py`
- Test: `tests/test_arena_deploy_stats.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `COMBINED_RE: re.Pattern`, `BOT_MARKERS: tuple[str, ...]`,
    `COUNTED_STATUSES = frozenset({200, 304})`, `SELF_HOSTS: tuple[str, ...]`,
    `TOP_REFERRERS = 5`, `STATS_MAX_AGE_DAYS = 14`
  - `@dataclass(frozen=True) class LogLine: path: str; status: int; referer: str; user_agent: str`
  - `@dataclass class Stats` with fields `views: dict[str, int]`,
    `downloads: dict[str, int]`, `referrers: dict[str, int]`,
    `total_views: int`, `total_downloads: int`, `bot_lines: int`,
    `malformed_lines: int`, `counted_lines: int`
  - `parse_line(line: str) -> LogLine | None`
  - `is_bot(user_agent: str) -> bool`
  - `classify(path: str) -> tuple[str, str] | None`
  - `referrer_host(referer: str) -> str | None`
  - `aggregate(lines: Iterable[str]) -> Stats`
  - `top_referrers(stats: Stats, n: int = TOP_REFERRERS) -> list[tuple[str, int]]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_arena_deploy_stats.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_stats.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stats'`.

NOTE: Python has a stdlib module named `statistics`, not `stats`, so this name is
safe. (`site.py` would NOT have been — see `site_builder.py`.)

- [ ] **Step 3: Write `docs/arena/deploy/stats.py`**

```python
"""nginx combined-format access log -> aggregated, bot-filtered readership counts.

Pure functions over log lines: no IO, no network. These numbers are published on
a public page, so every filtering rule is unit-testable rather than delegated to
an opaque report generator (GoAccess was considered and dropped for exactly this).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urlsplit

# nginx's built-in `combined` format. No log_format is declared in the config, so
# this is the exact shape; a line that does not match is counted as malformed
# rather than silently dropped.
COMBINED_RE = re.compile(
    r'^\S+ \S+ \S+ \[[^\]]+\] "(?P<method>[A-Z]+) (?P<path>\S+) [^"]*" '
    r'(?P<status>\d{3}) \S+ "(?P<referer>[^"]*)" "(?P<ua>[^"]*)"'
)

# Substring match, lower-cased. Deliberately broad: on a low-traffic site an
# unfiltered count is mostly crawlers, so a false positive costs less than a
# false negative. An empty user agent is treated as a bot.
BOT_MARKERS = (
    "bot", "crawler", "spider", "slurp", "curl", "wget", "python-requests",
    "httpx", "go-http-client", "java/", "libwww", "scrapy", "headlesschrome",
    "facebookexternalhit", "preview", "monitor", "uptime", "pingdom", "lighthouse",
)

COUNTED_STATUSES = frozenset({200, 304})
SELF_HOSTS = ("artena.one", "www.artena.one")
TOP_REFERRERS = 5
STATS_MAX_AGE_DAYS = 14

ARENA_PREFIX = "/arena/"
PAGE_SUFFIX = ".html"
DOWNLOAD_SUFFIXES = {".pdf": "pdf", ".md": "markdown"}


@dataclass(frozen=True)
class LogLine:
    path: str
    status: int
    referer: str
    user_agent: str


@dataclass
class Stats:
    views: dict[str, int] = field(default_factory=dict)
    downloads: dict[str, int] = field(default_factory=dict)
    referrers: dict[str, int] = field(default_factory=dict)
    total_views: int = 0
    total_downloads: int = 0
    bot_lines: int = 0
    malformed_lines: int = 0
    counted_lines: int = 0


def parse_line(line: str) -> LogLine | None:
    m = COMBINED_RE.match(line.strip())
    if not m:
        return None
    path = m.group("path").split("?", 1)[0]
    return LogLine(
        path=path,
        status=int(m.group("status")),
        referer=m.group("referer"),
        user_agent=m.group("ua"),
    )


def is_bot(user_agent: str) -> bool:
    ua = user_agent.strip().lower()
    if not ua or ua == "-":
        return True
    return any(marker in ua for marker in BOT_MARKERS)


def classify(path: str) -> tuple[str, str] | None:
    """(kind, stem) for an /arena/ path, or None if it is not ours.

    kind is one of page / pdf / markdown / asset. `/arena/` and
    `/arena/index.html` collapse to one `index` bucket so the front page is not
    double-counted.
    """
    if not path.startswith(ARENA_PREFIX):
        return None
    rest = path[len(ARENA_PREFIX):]
    if rest in ("", "index.html"):
        return "page", "index"

    name = rest.rsplit("/", 1)[-1]
    stem, _, ext = name.rpartition(".")
    if not stem:
        return None
    ext = f".{ext}"

    if ext == PAGE_SUFFIX:
        # A nested index.html (e.g. cards/run104/) is an asset page, not a post.
        return ("asset", stem) if "/" in rest else ("page", stem)
    if ext in DOWNLOAD_SUFFIXES:
        return DOWNLOAD_SUFFIXES[ext], stem
    return "asset", stem


def referrer_host(referer: str) -> str | None:
    """Host only. Self-referrals and empty referrers return None."""
    if not referer or referer == "-":
        return None
    host = urlsplit(referer).netloc.lower()
    if not host or host in SELF_HOSTS:
        return None
    return host


def aggregate(lines: Iterable[str]) -> Stats:
    stats = Stats()
    for raw in lines:
        parsed = parse_line(raw)
        if parsed is None:
            stats.malformed_lines += 1
            continue
        if is_bot(parsed.user_agent):
            stats.bot_lines += 1
            continue
        if parsed.status not in COUNTED_STATUSES:
            continue

        kind_stem = classify(parsed.path)
        if kind_stem is None:
            continue
        kind, stem = kind_stem
        stats.counted_lines += 1

        if kind == "page":
            stats.views[stem] = stats.views.get(stem, 0) + 1
            stats.total_views += 1
        elif kind in ("pdf", "markdown"):
            stats.downloads[stem] = stats.downloads.get(stem, 0) + 1
            stats.total_downloads += 1

        host = referrer_host(parsed.referer)
        if host:
            stats.referrers[host] = stats.referrers.get(host, 0) + 1
    return stats


def top_referrers(stats: Stats, n: int = TOP_REFERRERS) -> list[tuple[str, int]]:
    return sorted(stats.referrers.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_stats.py -q`
Expected: PASS — 33 passed (parametrized cases counted individually).

- [ ] **Step 5: Commit**

```bash
git add docs/arena/deploy/stats.py tests/test_arena_deploy_stats.py
git commit -m "feat(arena-deploy): pure nginx access-log parser for readership counts"
```

---

### Task 2: Snapshot file — write, read, and the staleness rule

**Files:**
- Modify: `docs/arena/deploy/stats.py` (append)
- Modify: `.gitignore`
- Test: `tests/test_arena_deploy_stats.py` (append)

**Interfaces:**
- Consumes: `Stats`, `top_referrers`, `STATS_MAX_AGE_DAYS` (Task 1).
- Produces:
  - `SNAPSHOT_VERSION = 1`
  - `to_snapshot(stats: Stats, generated_at: datetime) -> dict`
  - `load_snapshot(path: Path, now: datetime, max_age_days: int = STATS_MAX_AGE_DAYS) -> dict | None`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_arena_deploy_stats.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_stats.py -q`
Expected: FAIL — `AttributeError: module 'stats' has no attribute 'to_snapshot'`.

- [ ] **Step 3: Append to `docs/arena/deploy/stats.py`**

```python
import datetime as dt
import json
from pathlib import Path

SNAPSHOT_VERSION = 1


def to_snapshot(stats: Stats, generated_at: dt.datetime) -> dict:
    """The publishable subset. No IPs, no user agents, no full referrer URLs."""
    return {
        "version": SNAPSHOT_VERSION,
        "generated_at": generated_at.isoformat(),
        "views": dict(stats.views),
        "downloads": dict(stats.downloads),
        "referrers": [list(pair) for pair in top_referrers(stats)],
        "total_views": stats.total_views,
        "total_downloads": stats.total_downloads,
        "bot_lines": stats.bot_lines,
        "malformed_lines": stats.malformed_lines,
        "counted_lines": stats.counted_lines,
    }


def load_snapshot(
    path: Path, now: dt.datetime, max_age_days: int = STATS_MAX_AGE_DAYS
) -> dict | None:
    """The snapshot, or None if it is absent, unreadable, unknown or stale.

    Returning None (rather than an empty Stats) is the whole point: the renderer
    must be able to distinguish "nobody read it" from "we did not measure", and
    a zero-filled payload makes those two indistinguishable downstream.
    """
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != SNAPSHOT_VERSION:
        return None

    raw = data.get("generated_at")
    if not isinstance(raw, str):
        return None
    try:
        generated_at = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=dt.timezone.utc)

    if now - generated_at > dt.timedelta(days=max_age_days):
        return None
    return data
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_stats.py -q`
Expected: PASS — 40 passed.

- [ ] **Step 5: Ignore the snapshot**

`stats.json` is a derived snapshot, not source, and is fully recomputable from
the logs. Append to `.gitignore` beside the existing arena entries:

```gitignore
# arena readership snapshot (derived from server logs, recomputable)
docs/arena/deploy/stats.json
```

- [ ] **Step 6: Commit**

```bash
git add docs/arena/deploy/stats.py tests/test_arena_deploy_stats.py .gitignore
git commit -m "feat(arena-deploy): stats snapshot with absence and staleness rules"
```

---

### Task 3: Render the counters, and render nothing when unmeasured

**Files:**
- Modify: `docs/arena/deploy/site_builder.py`
- Modify: `docs/arena/deploy/theme.css`
- Test: `tests/test_arena_deploy_site.py` (append)

**Interfaces:**
- Consumes: the snapshot dict from `stats.load_snapshot` (Task 2), and
  `render_index(posts, minutes, theme)` (existing).
- Produces:
  - `render_index(posts, minutes, theme, snapshot: dict | None = None) -> str`
    — the fourth parameter is optional and defaults to `None`, so every existing
    caller and test keeps working unchanged.
  - `_readership_card(snapshot: dict) -> str`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_arena_deploy_site.py`:

```python
SNAPSHOT = {
    "version": 1,
    "generated_at": "2026-08-18T12:00:00+00:00",
    "views": {"2026-08-18-run110-luna": 180, "2026-08-13-run104-board": 94, "index": 412},
    "downloads": {"2026-08-13-run104-board": 22},
    "referrers": [["news.ycombinator.com", 88], ["t.co", 31]],
    "total_views": 686,
    "total_downloads": 22,
    "bot_lines": 1400,
    "malformed_lines": 0,
    "counted_lines": 708,
}


def test_index_without_a_snapshot_renders_no_counters_at_all():
    """Absence must render NOTHING — a 0 would read as 'nobody', not 'unmeasured'."""
    html = sb.render_index(POSTS, MINUTES, THEME)
    assert "Readership" not in html
    # NB: assert on the MARKUP, not the word "reads" — theme.css is inlined into
    # every page and contains a `.entry-meta .reads` rule, so a bare substring
    # check passes here only by accident of the stub theme and fails on the real one.
    assert 'class="reads"' not in html


def test_index_with_a_snapshot_shows_the_readership_card():
    html = sb.render_index(POSTS, MINUTES, THEME, snapshot=SNAPSHOT)
    rail = html.split('class="rail"', 1)[1]
    assert "Readership" in rail
    assert "686" in rail and "22" in rail
    assert "news.ycombinator.com" in rail and "88" in rail


def test_readership_card_labels_the_snapshot_date_not_last_seen():
    html = sb.render_index(POSTS, MINUTES, THEME, snapshot=SNAPSHOT)
    assert "as of 2026-08-18" in html


def test_readership_card_surfaces_the_bot_filtered_fraction():
    html = sb.render_index(POSTS, MINUTES, THEME, snapshot=SNAPSHOT)
    assert "bot" in html.lower()
    assert "1,400" in html or "1400" in html


def test_per_post_read_counts_appear_only_for_posts_with_data():
    html = sb.render_index(POSTS, MINUTES, THEME, snapshot=SNAPSHOT)
    assert "180 reads" in html          # run110 has a count
    assert "94 reads" in html           # run104 has a count
    assert "0 reads" not in html        # a post with no data shows nothing


def test_per_post_read_counts_are_page_views_not_downloads():
    """One PDF fetch must not read as a page view."""
    html = sb.render_index(POSTS, MINUTES, THEME, snapshot=SNAPSHOT)
    # run104: 94 views + 22 downloads. The feed entry must say 94, never 116.
    assert "94 reads" in html
    assert "116 reads" not in html


def test_readership_card_escapes_referrer_hosts():
    evil = dict(SNAPSHOT, referrers=[["<script>evil</script>", 3]])
    html = sb.render_index(POSTS, MINUTES, THEME, snapshot=evil)
    assert "&lt;script&gt;" in html
    assert "<script>evil" not in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_site.py -q`
Expected: FAIL — `render_index() got an unexpected keyword argument 'snapshot'`.

- [ ] **Step 3: Modify `docs/arena/deploy/site_builder.py`**

Add near the other rail helpers:

```python
def _readership_card(snapshot: dict) -> str:
    """Rail card. Only ever called with a snapshot that passed load_snapshot()."""
    generated = str(snapshot.get("generated_at", ""))[:10]
    views = int(snapshot.get("total_views", 0))
    downloads = int(snapshot.get("total_downloads", 0))
    bots = int(snapshot.get("bot_lines", 0))

    refs = "".join(
        f"<span>{escape(str(host))}<b>{int(count):,}</b></span>"
        for host, count in snapshot.get("referrers", [])
    )
    refs_html = f'<div class="tag-list">{refs}</div>' if refs else ""

    return (
        '<section class="rail-card"><h3>Readership</h3>'
        f'<p class="rail-sub">as of {escape(generated)}</p>'
        f'<div class="tag-list">'
        f"<span>views<b>{views:,}</b></span>"
        f"<span>downloads<b>{downloads:,}</b></span>"
        f"</div>"
        f"{refs_html}"
        f'<p class="rail-note">{bots:,} bot requests filtered out</p>'
        "</section>\n"
    )
```

Change `_entry` to accept and render an optional count:

```python
def _entry(post: Post, minutes: int, reads: int | None = None) -> str:
    meta = [f'<time datetime="{post.date.isoformat()}">{post.date.isoformat()}</time>']
    meta += [f'<span class="tag">{escape(t)}</span>' for t in post.tags]
    meta.append(f'<span class="read">{minutes} min</span>')
    # Page views only. Downloads are a separate rail total, so one PDF fetch
    # cannot read as a page view. No data => no element, never "0 reads".
    if reads:
        meta.append(f'<span class="reads">{reads:,} reads</span>')
    ...  # rest of the function unchanged
```

Change `render_index` to thread the snapshot through:

```python
def render_index(
    posts: list[Post],
    minutes: dict[str, int],
    theme: str,
    snapshot: dict | None = None,
) -> str:
    """The blog index: masthead, intro, reverse-chronological feed, derived rail.

    `snapshot` is optional and defaults to None: when readership has not been
    measured, no counter markup is emitted anywhere on the page.
    """
    views = (snapshot or {}).get("views", {})
    feed = "".join(_entry(p, minutes[p.stem], views.get(p.stem)) for p in posts)

    rail = ""
    board = latest_standings(posts)
    if board is not None:
        rail += _standings_card(board)
    if snapshot is not None:
        rail += _readership_card(snapshot)
    rail += _tag_card(posts)
    ...  # rest of the function unchanged
```

- [ ] **Step 4: Add the rail-note style to `docs/arena/deploy/theme.css`**

Append beside the other `.rail-*` rules:

```css
.rail-note{margin:.9em 0 0; font-size:.72rem; color:var(--muted); line-height:1.4}
.entry-meta .reads{color:var(--accent-2); font-weight:600}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_site.py -q`
Expected: PASS — 25 passed (18 existing + 7 new). The existing tests must pass
unchanged, because `snapshot` defaults to `None`.

- [ ] **Step 6: Commit**

```bash
git add docs/arena/deploy/site_builder.py docs/arena/deploy/theme.css tests/test_arena_deploy_site.py
git commit -m "feat(arena-deploy): readership rail card and per-post read counts"
```

---

### Task 4: Wire the snapshot into the build

**Files:**
- Modify: `docs/arena/deploy/build.py`
- Test: `tests/test_arena_deploy_build.py` (append)

**Interfaces:**
- Consumes: `stats.load_snapshot` (Task 2), `site_builder.render_index(..., snapshot=)` (Task 3).
- Produces: `build(manifest_path, arena_dir, deploy_dir, out_dir, refresh_pdf=True, now=None) -> BuildResult`
  — `now` is an injectable `datetime` for testing the staleness path.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_arena_deploy_build.py`:

```python
import datetime as dt
import json

STATS_NOW = dt.datetime(2026, 8, 18, 12, 0, tzinfo=dt.timezone.utc)


def _write_stats(deploy_dir: Path, generated_at: dt.datetime) -> Path:
    p = deploy_dir / "stats.json"
    p.write_text(json.dumps({
        "version": 1,
        "generated_at": generated_at.isoformat(),
        "views": {"2026-08-18-run110-luna": 180},
        "downloads": {},
        "referrers": [["news.ycombinator.com", 88]],
        "total_views": 180,
        "total_downloads": 0,
        "bot_lines": 900,
        "malformed_lines": 0,
        "counted_lines": 180,
    }))
    return p


def test_build_renders_counters_when_a_fresh_snapshot_exists(project, tmp_path):
    mf, arena, out = project
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "theme.css").write_text((DEPLOY / "theme.css").read_text())
    _write_stats(deploy, STATS_NOW)

    b.build(mf, arena, deploy, out, refresh_pdf=False, now=STATS_NOW)
    index = (out / "index.html").read_text()
    assert "Readership" in index and "180 reads" in index


def test_build_renders_no_counters_when_the_snapshot_is_stale(project, tmp_path):
    mf, arena, out = project
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "theme.css").write_text((DEPLOY / "theme.css").read_text())
    _write_stats(deploy, STATS_NOW - dt.timedelta(days=30))

    b.build(mf, arena, deploy, out, refresh_pdf=False, now=STATS_NOW)
    index = (out / "index.html").read_text()
    assert "Readership" not in index
    # the real theme.css IS inlined here, and it contains a `.reads` rule —
    # so this must check the markup, not the word
    assert 'class="reads"' not in index


def test_build_renders_no_counters_when_there_is_no_snapshot(project):
    mf, arena, out = project
    b.build(mf, arena, DEPLOY, out, refresh_pdf=False)
    index = (out / "index.html").read_text()
    assert "Readership" not in index
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_build.py -q`
Expected: FAIL — `build() got an unexpected keyword argument 'now'`.

- [ ] **Step 3: Modify `docs/arena/deploy/build.py`**

Add the import beside the others:

```python
import datetime as dt

from stats import load_snapshot
```

Change the signature and the index render:

```python
def build(
    manifest_path: Path,
    arena_dir: Path,
    deploy_dir: Path,
    out_dir: Path,
    refresh_pdf: bool = True,
    now: dt.datetime | None = None,
) -> BuildResult:
```

and, replacing the existing `index.html` write at the end of the function:

```python
    # Absent / stale / unparseable => None => no counter markup anywhere.
    snapshot = load_snapshot(
        deploy_dir / "stats.json", now or dt.datetime.now(dt.timezone.utc)
    )
    if snapshot is None:
        result.warnings.append("no fresh stats.json; index renders without counters")

    (out_dir / "index.html").write_text(
        sb.render_index(posts, minutes, theme, snapshot=snapshot)
    )
    (out_dir / "about.html").write_text(sb.render_about(posts, theme))
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_build.py -q`
Expected: PASS — 11 passed.

- [ ] **Step 5: Commit**

```bash
git add docs/arena/deploy/build.py tests/test_arena_deploy_build.py
git commit -m "feat(arena-deploy): build reads the readership snapshot, or renders none"
```

---

### Task 5: `deploy.sh stats` — fetch, aggregate, rotate

**Files:**
- Create: `docs/arena/deploy/collect_stats.py`
- Modify: `docs/arena/deploy/deploy.sh`
- Test: `tests/test_arena_deploy_stats.py` (append)

**Interfaces:**
- Consumes: `stats.aggregate`, `stats.to_snapshot` (Tasks 1–2), and
  `publish.HOST` / `publish.SSH_KEY` for the ssh target.
- Produces:
  - `REMOTE_LOG_GLOB = "/opt/open-slides-zero/runtime/nginx-logs/arena.log*"`
  - `ROTATE_BYTES = 50 * 1024 * 1024`, `ROTATE_KEEP = 4`
  - `fetch_cmd(host: str, key: str, glob: str) -> list[str]`
  - `rotate_cmd(host: str, key: str, log: str, keep: int) -> list[str]`
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_arena_deploy_stats.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_stats.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'collect_stats'`.

- [ ] **Step 3: Write `docs/arena/deploy/collect_stats.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_stats.py -q`
Expected: PASS — 45 passed.

- [ ] **Step 5: Add the `stats` verb to `docs/arena/deploy/deploy.sh`**

In the header comment block, after the `preview` line:

```bash
#   deploy.sh stats                pull the access log and refresh stats.json
```

and in the `case` statement, beside the other verbs:

```bash
  stats)     exec "$PY" "$HERE/collect_stats.py" "$@" ;;
```

Verify: `docs/arena/deploy/deploy.sh help` lists `stats`.

- [ ] **Step 6: Commit**

```bash
git add docs/arena/deploy/collect_stats.py docs/arena/deploy/deploy.sh tests/test_arena_deploy_stats.py
git commit -m "feat(arena-deploy): deploy.sh stats — fetch, aggregate, rotate"
```

---

### Task 6: Turn on logging (the cross-repo change) and document

**Files:**
- Modify: `docs/arena/deploy/server/arena-location.conf`
- Modify: `docs/arena/deploy/server/patch_osz.py`
- Modify: `docs/arena/deploy/README.md`
- Modify: `CHANGELOG.md`, `CLAUDE.md`
- Test: `tests/test_arena_deploy_server_patch.py` (append)

**Interfaces:**
- Consumes: `patch_osz.patch_compose` (existing).
- Produces: `patch_osz.LOG_MOUNT = "      - ./runtime/nginx-logs:/var/log/nginx\n"`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_arena_deploy_server_patch.py`:

```python
def test_arena_location_writes_an_access_log():
    directives = [
        ln for ln in po.ARENA_LOCATION.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert any("access_log" in ln and "arena.log" in ln for ln in directives)
    # access_log is safe here; the inheritance-replacement rule is add_header-only
    assert not any("add_header" in ln for ln in directives)


def test_patch_compose_adds_the_log_mount():
    out = po.patch_compose(PRISTINE_COMPOSE)
    assert out.count("./runtime/nginx-logs:/var/log/nginx") == 1
    assert out.count("./runtime/arena:/var/www/arena:ro") == 1


def test_patch_compose_with_both_mounts_is_idempotent():
    once = po.patch_compose(PRISTINE_COMPOSE)
    assert po.patch_compose(once) == once
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_server_patch.py -q`
Expected: FAIL — no `access_log` directive, and no log mount in the patched compose.

- [ ] **Step 3: Add the access_log directive**

In `docs/arena/deploy/server/arena-location.conf`, inside the location block,
after `expires 5m;`:

```nginx
    # Scoped to this location on purpose: the file stays small and no request
    # data is collected about the rest of the site. `access_log` is safe here —
    # the inheritance-replacement rule that makes `add_header` dangerous in this
    # block applies to add_header only.
    access_log /var/log/nginx/arena.log;
```

- [ ] **Step 4: Add the log mount to `patch_osz.py`**

Beside `ARENA_MOUNT`:

```python
# Mounting the DIRECTORY is what makes file logging work: it replaces the nginx
# image's `access.log -> /dev/stdout` symlink with a real directory.
LOG_MOUNT = "      - ./runtime/nginx-logs:/var/log/nginx\n"
```

and in `patch_compose`, after the existing arena-mount insert:

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_server_patch.py -q`
Expected: PASS — 16 passed.

- [ ] **Step 6: Deploy the logging change**

```bash
.venv/bin/python docs/arena/deploy/server/patch_osz.py
(cd /Users/fuxinyao/open-slides-zero && ./scripts/deploy_incremental.sh --service nginx)
```

Then — REQUIRED, not optional — force-recreate so the bind mount re-resolves:

```bash
/usr/bin/ssh -i /Users/fuxinyao/ppt-pro-server/slides.pem ubuntu@43.156.158.156 \
  "cd /opt/open-slides-zero && sudo docker compose --env-file .env.production \
   -f compose.prod.yml up -d --force-recreate nginx"
```

Verify the log is being written, and that the six security headers survived:

```bash
curl -s -o /dev/null https://www.artena.one/arena/
/usr/bin/ssh -i /Users/fuxinyao/ppt-pro-server/slides.pem ubuntu@43.156.158.156 \
  "sudo tail -2 /opt/open-slides-zero/runtime/nginx-logs/arena.log"
curl -sI https://www.artena.one/arena/ | grep -ci "content-security-policy\|strict-transport"
```
Expected: two log lines echoing your request, and `2` from the header grep.

- [ ] **Step 7: Confirm the pipeline end to end**

```bash
docs/arena/deploy/deploy.sh stats
docs/arena/deploy/deploy.sh build --no-pdf
grep -c "Readership" docs/arena/deploy/build/index.html
```
Expected: `stats` reports counts, and the grep returns `1`.

Do NOT publish yet — the counters cover minutes of traffic. Publish after a week.

- [ ] **Step 8: Document and commit**

`docs/arena/deploy/README.md` — add under Gotchas:

```markdown
- **Readership counters are a build-time snapshot, not live.** `deploy.sh stats`
  refreshes `stats.json`; `build` embeds whatever it finds. If the snapshot is
  missing or older than 14 days the counters render **nothing** — never zeros,
  because "0 views" that means "not measured" is a lie the page cannot walk back.
- **Counts exclude bots and non-200/304 responses**, and the filtered count is
  shown in the rail. Raw counts on a low-traffic site are mostly crawlers.
```

`CHANGELOG.md` under `[Unreleased] / ### Added`:

```markdown
- **Arena readership counters.** nginx logs `/arena/` requests to a mounted
  volume; `deploy.sh stats` fetches and aggregates them locally with a
  stdlib-only parser into `stats.json`, and the index renders a READERSHIP rail
  card plus per-post view counts. No JavaScript, no third party, no CSP change,
  and PDF/Markdown downloads are counted — none of which a pixel or JS tracker
  could do. Absent or stale stats render no counters at all rather than zeros.
```

`CLAUDE.md`, in the arena publishing section:

```markdown
- **Readership counters are derived, snapshot-based, and fail to NOTHING.**
  `stats.py` is pure and stdlib-only (GoAccess was considered and dropped so the
  published numbers stay unit-testable). Missing / unparseable / older than
  `STATS_MAX_AGE_DAYS` (14) ⇒ no counter markup, never zeros — the `empty` vs
  `unavailable` rule applied to a public page. `as of <date>` is when `stats` last
  ran, NOT `last_seen`, which a single crawler hit would make look fresh.
  Aggregation is stateless: every run re-reads `arena.log*` including rotated
  generations, so `stats.json` is pure derived data.
```

```bash
git add docs/arena/deploy/server docs/arena/deploy/README.md CHANGELOG.md CLAUDE.md \
        tests/test_arena_deploy_server_patch.py
git commit -m "feat(arena-deploy): log /arena/ requests and document the stats pipeline"
```

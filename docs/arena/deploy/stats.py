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

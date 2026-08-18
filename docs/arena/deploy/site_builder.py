"""Pure HTML generation for the arena blog. No filesystem writes — build.py owns IO.

Everything the index shows is derived from the manifest, so the page cannot freeze
the way the hand-typed Run #94 leaderboards did.
"""
from __future__ import annotations

from html import escape
from pathlib import Path

from manifest import Post, latest_standings, tag_counts

SITE_TITLE = "The OTC Desk Agent Arena"
SITE_TAGLINE = (
    "Controlled, repeated-trial evaluations of LLMs operating a real "
    "structured-derivatives trading desk, with no human in the loop."
)
GITHUB_URL = "https://github.com/deiiiiii93/open-otc-trading"


def load_theme(deploy_dir: Path) -> str:
    return (deploy_dir / "theme.css").read_text()


def _head(title: str, theme: str, description: str) -> str:
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(title)}</title>\n"
        f'<meta name="description" content="{escape(description)}">\n'
        f"<style>{theme}</style>\n</head><body>\n"
    )


def _masthead() -> str:
    return (
        '<header class="masthead">'
        '<a class="brand" href="/"><span class="mark">A</span><span>Artena</span></a>'
        '<nav><a href="./about.html">About</a>'
        f'<a href="{GITHUB_URL}">GitHub</a></nav>'
        "</header>\n"
    )


def _site_footer() -> str:
    return (
        '<footer class="site">Published on Artena for readers who want to understand '
        "how LLMs behave in realistic financial-agent workflows.</footer>\n"
    )


def _entry(post: Post, minutes: int) -> str:
    meta = [f'<time datetime="{post.date.isoformat()}">{post.date.isoformat()}</time>']
    meta += [f'<span class="tag">{escape(t)}</span>' for t in post.tags]
    meta.append(f'<span class="read">{minutes} min</span>')

    chips = [post.run] if post.run else []
    chips += list(post.chips)
    chip_html = ""
    if chips:
        chip_html = (
            '<div class="chips">'
            + "".join(f'<span class="chip">{escape(c)}</span>' for c in chips)
            + "</div>"
        )

    return (
        '<article class="entry">'
        f'<div class="entry-meta">{"".join(meta)}</div>'
        f'<h2><a href="./{post.html_name}">{escape(post.title)}</a></h2>'
        f'<p class="blurb">{escape(post.blurb)}</p>'
        f"{chip_html}"
        "</article>\n"
    )


def _standings_card(post: Post) -> str:
    top = max(s.score for s in post.standings) or 1
    rows = []
    for s in post.standings:
        pct = s.score / top * 100
        note = f'<span class="note">{escape(s.note)}</span>' if s.note else ""
        rows.append(
            '<div class="rank-row">'
            f'<span class="rank">{s.rank}</span>'
            f'<span class="model">{escape(s.model)}</span>'
            f'<span class="score">{s.score:g}</span>'
            f"{note}"
            f'<span class="bar-track"><span class="bar-fill" style="width:{pct:.1f}%"></span></span>'
            "</div>"
        )
    label = post.run or post.title
    return (
        '<section class="rail-card"><h3>Standings</h3>'
        f'<p class="rail-sub">{escape(label)}</p>'
        f'{"".join(rows)}'
        "</section>\n"
    )


def _tag_card(posts: list[Post]) -> str:
    rows = "".join(
        f"<span>{escape(tag)}<b>{n}</b></span>" for tag, n in tag_counts(posts)
    )
    return f'<section class="rail-card"><h3>Tags</h3><div class="tag-list">{rows}</div></section>\n'


def render_index(posts: list[Post], minutes: dict[str, int], theme: str) -> str:
    """The blog index: masthead, intro, reverse-chronological feed, derived rail."""
    feed = "".join(_entry(p, minutes[p.stem]) for p in posts)

    rail = ""
    board = latest_standings(posts)
    if board is not None:
        rail += _standings_card(board)
    rail += _tag_card(posts)

    return (
        _head(SITE_TITLE, theme, SITE_TAGLINE)
        + '<div class="page">\n'
        + _masthead()
        + f'<div class="intro"><h1>{escape(SITE_TITLE)}</h1>'
        + f'<p class="lead">{escape(SITE_TAGLINE)}</p></div>\n'
        + '<div class="layout">\n'
        + f'<main class="feed">\n{feed}</main>\n'
        + f'<aside class="rail">\n{rail}</aside>\n'
        + "</div>\n"
        + _site_footer()
        + "</div>\n</body></html>\n"
    )

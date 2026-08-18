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


ABOUT_POINTS = [
    "Stateful, multi-step OTC derivatives workflows driven end to end.",
    "Headless operation with no human approval between steps.",
    "Repeated trials, to separate average ability from reliability.",
    "Artifacts published as stable HTML, PDF, Markdown, and data files.",
]
CONTACT_EMAIL = "yaofuxin1993@gmail.com"


def render_post_page(
    post: Post,
    body_html: str,
    minutes: int,
    theme: str,
    newer: Post | None,
    older: Post | None,
) -> str:
    """Wrap a rendered report body in blog chrome.

    The body arrives verbatim from render_report.render_markdown, and the PDF is
    rendered from the un-chromed document, so nothing here can affect print.
    """
    crumb_tail = escape(post.run or post.title)
    meta = [f'<time datetime="{post.date.isoformat()}">{post.date.isoformat()}</time>']
    meta += [f'<span class="tag">{escape(t)}</span>' for t in post.tags]
    meta.append(f'<span class="read">{minutes} min read</span>')

    nav = []
    if newer is not None:
        nav.append(f'<a href="./{newer.html_name}">&larr; {escape(newer.title)}</a>')
    else:
        nav.append("<span></span>")
    nav.append(
        '<span class="downloads">'
        f'<a href="./{post.file}">Markdown</a> &middot; '
        f'<a href="./{post.pdf_name}">PDF</a>'
        "</span>"
    )
    if older is not None:
        nav.append(f'<a href="./{older.html_name}">{escape(older.title)} &rarr;</a>')
    else:
        nav.append("<span></span>")

    return (
        _head(f"{post.title} — {SITE_TITLE}", theme, post.blurb)
        + '<div class="post-shell">\n'
        + _masthead()
        + f'<p class="crumb"><a href="./index.html">Arena</a> / {crumb_tail}</p>\n'
        + f'<div class="byline">{"".join(meta)}</div>\n'
        + f'<div class="post-body">\n{body_html}\n</div>\n'
        + f'<div class="post-nav">{"".join(nav)}</div>\n'
        + _site_footer()
        + "</div>\n</body></html>\n"
    )


def render_about(posts: list[Post], theme: str) -> str:
    points = "".join(f"<li>{escape(p)}</li>" for p in ABOUT_POINTS)
    return (
        _head(f"About — {SITE_TITLE}", theme, SITE_TAGLINE)
        + '<div class="post-shell">\n'
        + _masthead()
        + '<div class="intro"><h1>What the Arena measures</h1>'
        + '<p class="lead">The Arena is built for financial-agent evaluation, not '
        + "generic prompt scoring. Each trial drives live desk workflows and "
        + "reconstructs the transcript from the system's own trace log.</p></div>\n"
        + f'<div class="post-body"><ul>{points}</ul>'
        + f"<p>{len(posts)} reports published. "
        + f'Contact <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a> or read the '
        + f'source at <a href="{GITHUB_URL}">{escape(GITHUB_URL)}</a>.</p></div>\n'
        + _site_footer()
        + "</div>\n</body></html>\n"
    )


def render_contact_sheet(title: str, image_names: list[str], theme: str) -> str:
    """An index for an assets directory, so a bare `](cards/run104/)` link resolves."""
    figures = "".join(
        f'<figure><img src="./{escape(n)}" alt="{escape(n)}" loading="lazy">'
        f"<figcaption>{escape(n)}</figcaption></figure>"
        for n in image_names
    )
    return (
        _head(f"{title} — {SITE_TITLE}", theme, title)
        + '<div class="post-shell">\n'
        + '<p class="crumb"><a href="../index.html">Arena</a> / '
        + f"{escape(title)}</p>\n"
        + f"<h1>{escape(title)}</h1>\n"
        + f'<div class="sheet">{figures}</div>\n'
        + _site_footer()
        + "</div>\n</body></html>\n"
    )

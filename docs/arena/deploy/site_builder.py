"""Pure HTML generation for the arena blog. No filesystem writes — build.py owns IO.

Everything the index shows is derived from the manifest, so the page cannot freeze
the way the hand-typed Run #94 leaderboards did.
"""
from __future__ import annotations

import re
from html import escape
from pathlib import Path

from manifest import Post, latest_standings, tag_counts

SITE_TITLE = "The OTC Desk Agent Arena"
SITE_TAGLINE = (
    "Controlled, repeated-trial evaluations of LLMs operating a real "
    "structured-derivatives trading desk, with no human in the loop."
)
GITHUB_URL = "https://github.com/deiiiiii93/open-otc-trading"

# Standings bars are drawn against a FIXED axis, not against the leader's score.
# OVR is a 0-99 scale, so 80 renders at ~81% and the absolute level is legible.
# Scaling to max(scores) instead makes the leader always 100% and — in a tight
# field like Run #104's 80 vs 79 — renders every bar as a near-full-width line
# that reads as a divider rather than a measurement.
RAIL_AXIS_MAX = 99.0


_CODE_SPAN = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    """Escape, then honour markdown code spans.

    Blurbs are authored in `posts.yaml` beside markdown prose and routinely
    carry `low`/`par` style spans, which plain escaping published as literal
    backticks. Escaping FIRST is what makes this safe: the pattern only ever
    wraps text that is already inert.

    Deliberately NOT applied to titles. `publish.verify_live` asserts that
    `escape(post.title)` appears on the served page, so inlining a title would
    break the deploy verifier rather than the build — a much worse failure to
    diagnose. It is also not applied to the <meta> description, where markup
    does not belong.
    """
    return _CODE_SPAN.sub(r"<code>\1</code>", escape(text))


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


def _masthead(
    leaderboard: bool = False,
    here: str = "",
    models: bool = False,
    nameplate: str = "compact",
) -> str:
    """The site nameplate plus nav.

    `nameplate="full"` belongs to the index alone: there the site title IS the
    page's <h1> and the tagline rides with it, which is why the index carries
    no separate intro band. Everywhere else it is compact — the page's own
    heading has to lead, and repeating the tagline would stack two muted
    paragraphs above the data while the site title outweighed the page the
    reader is actually on.

    The flags gate the nav links, so the absence rule reaches the chrome.

    When a page was not built, a masthead that linked it anyway would 404 on every
    page of the site. The two flags are separate because a snapshot exported
    before model cards existed carries boards but no `models` block.
    """
    def link(href: str, label: str, key: str) -> str:
        cur = ' class="here"' if here == key else ""
        return f'<a href="{href}"{cur}>{label}</a>'

    # Unconditional, unlike the two derived pages: index.html is always built,
    # and the eyebrow points at the site root rather than /arena/ — so without
    # this the leaderboard and cards pages are one-way doors out of the feed.
    links = [link("./index.html", "Blog", "blog")]
    if leaderboard:
        links.append(link("./leaderboard.html", "Leaderboard", "leaderboard"))
    if models:
        links.append(link("./models.html", "Model Cards", "models"))
    links.append(link("./about.html", "About", "about"))
    links.append(f'<a href="{GITHUB_URL}">GitHub</a>')

    full = nameplate == "full"
    # The eyebrow inherits the root-site link the old A-in-a-square brand owned.
    # Folding the title into the header would otherwise strand it, since the
    # title now points at the arena index.
    plate = '<a class="eyebrow" href="/">Artena</a>'
    plate += (
        f'<h1 class="plate-title">{escape(SITE_TITLE)}</h1>' if full
        else f'<a class="plate-title" href="./index.html">{escape(SITE_TITLE)}</a>'
    )
    if full:
        plate += f'<p class="strap">{escape(SITE_TAGLINE)}</p>'

    return (
        f'<header class="{"masthead" if full else "masthead compact"}">'
        f'<div class="plate">{plate}</div>'
        f'<nav>{"".join(links)}</nav>'
        "</header>\n"
    )


def _site_footer() -> str:
    return (
        '<footer class="site">Published on Artena for readers who want to understand '
        "how LLMs behave in realistic financial-agent workflows.</footer>\n"
    )


def _entry(
    post: Post, minutes: int, reads: int | None = None, lead: bool = False
) -> str:
    # Kicker first: what kind of piece, then when. Date-first read as a log line.
    meta = [f'<span class="tag">{escape(t)}</span>' for t in post.tags]
    meta.append(
        f'<time datetime="{post.date.isoformat()}">{post.date.isoformat()}</time>'
    )
    meta.append(f'<span class="read">{minutes} min</span>')
    # Page views only. Downloads are a separate rail total, so one PDF fetch
    # cannot read as a page view. No data => no element, never "0 reads".
    if reads:
        meta.append(f'<span class="reads">{reads:,} reads</span>')

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
        f'<article class="{"entry lead" if lead else "entry"}">'
        f'<div class="entry-meta">{"".join(meta)}</div>'
        f'<h2><a href="./{post.html_name}">{escape(post.title)}</a></h2>'
        f'<p class="blurb">{_inline(post.blurb)}</p>'
        f"{chip_html}"
        "</article>\n"
    )


def _standings_card(post: Post) -> str:
    axis = max(RAIL_AXIS_MAX, max(s.score for s in post.standings))
    rows = []
    for s in post.standings:
        pct = max(s.score / axis * 100, 0.0)
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


def _tag_card(posts: list[Post]) -> str:
    rows = "".join(
        f"<span>{escape(tag)}<b>{n}</b></span>" for tag, n in tag_counts(posts)
    )
    return f'<section class="rail-card"><h3>Tags</h3><div class="tag-list">{rows}</div></section>\n'


def render_index(
    posts: list[Post],
    minutes: dict[str, int],
    theme: str,
    snapshot: dict | None = None,
    leaderboard: bool = False,
    models: bool = False,
) -> str:
    """The blog index: masthead, intro, reverse-chronological feed, derived rail.

    `snapshot` is optional and defaults to None: when readership has not been
    measured, no counter markup is emitted anywhere on the page.
    """
    views = (snapshot or {}).get("views", {})
    # The newest post leads. A feed where every item is the same size has no
    # editorial opinion about what to read first.
    feed = "".join(
        _entry(p, minutes[p.stem], views.get(p.stem), lead=(i == 0))
        for i, p in enumerate(posts)
    )

    rail = ""
    board = latest_standings(posts)
    if board is not None:
        rail += _standings_card(board)
    if snapshot is not None:
        rail += _readership_card(snapshot)
    rail += _tag_card(posts)

    return (
        _head(SITE_TITLE, theme, SITE_TAGLINE)
        + '<div class="page">\n'
        + _masthead(leaderboard, here="blog", models=models, nameplate="full")
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
    leaderboard: bool = False,
    models: bool = False,
) -> str:
    """Wrap a rendered report body in blog chrome.

    The body arrives verbatim from render_report.render_markdown, and the PDF is
    rendered from the un-chromed document, so nothing here can affect print.
    """
    crumb_tail = escape(post.run or post.title)
    meta = [f'<span class="tag">{escape(t)}</span>' for t in post.tags]
    meta.append(
        f'<time datetime="{post.date.isoformat()}">{post.date.isoformat()}</time>'
    )
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
        + _masthead(leaderboard, models=models)
        + f'<p class="crumb"><a href="./index.html">Arena</a> / {crumb_tail}</p>\n'
        + f'<div class="byline">{"".join(meta)}</div>\n'
        + f'<div class="post-body">\n{body_html}\n</div>\n'
        + f'<div class="post-nav">{"".join(nav)}</div>\n'
        + _site_footer()
        + "</div>\n</body></html>\n"
    )


def render_about(
    posts: list[Post], theme: str, leaderboard: bool = False, models: bool = False
) -> str:
    points = "".join(f"<li>{escape(p)}</li>" for p in ABOUT_POINTS)
    return (
        _head(f"About — {SITE_TITLE}", theme, SITE_TAGLINE)
        + '<div class="post-shell">\n'
        + _masthead(leaderboard, here="about", models=models)
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


# ---------------------------------------------------------------------------
# The leaderboard page
# ---------------------------------------------------------------------------

LEADERBOARD_TITLE = "Leaderboard"
LEADERBOARD_LEAD = (
    "One section per golden workflow, ranked by the Model Ability Card's OVR. "
    "Boards are never merged across runs: a different field, instrument, or "
    "manifest revision makes two boards incomparable, so each is published "
    "exactly as it was measured."
)
# Display order follows the card's own presentation (grounding, adherence,
# synthesis, procedure, efficiency), not the tie-break order.
STAT_ORDER = ("GRD", "ADH", "SYN", "PRC", "EFF")
# CON is dispersion across trials, so the trial depth it was measured over is
# published beside it: a CON derived from a single trial is not a measurement.
CARDED_HEADERS = ("Rank", "Model", "OVR", *STAT_ORDER, "CON", "Obj", "Trials")
UNCARDED_HEADERS = ("Rank", "Model", "Obj", "Trials")


def _num(value, fmt: str = "g") -> str:
    """A missing measurement renders as an em dash, never as a zero."""
    if value is None:
        return '<span class="none">&mdash;</span>'
    return format(value, fmt)


def _contestant(row: dict) -> str:
    """Model plus its effort arm. Two arms of one model are otherwise identical."""
    effort = row.get("effort")
    badge = f'<span class="effort">{escape(str(effort))}</span>' if effort else ""
    return f'<td class="model">{escape(str(row.get("model", "")))}{badge}</td>'


def _board_rows(board: dict) -> str:
    carded = bool(board.get("carded"))
    out = []
    for row in board.get("rows", []):
        cells = [f'<td class="rank">{int(row.get("rank", 0))}</td>', _contestant(row)]
        if carded:
            stats = row.get("stats") or {}
            cells.append(f'<td class="ovr">{_num(row.get("ovr"))}</td>')
            cells += [f"<td>{_num(stats.get(s))}</td>" for s in STAT_ORDER]
            cells.append(f'<td class="con">{_num(row.get("con"))}</td>')
        # One decimal always: "100" beside "94.9" reads as a different precision
        # of measurement rather than the same axis at its ceiling.
        cells.append(f'<td class="obj">{_num(row.get("objective"), ".1f")}</td>')
        cells.append(f'<td class="n">{_num(row.get("trials"))}</td>')
        out.append(f'<tr>{"".join(cells)}</tr>')
    return "".join(out)


def _board(board: dict, by_file: dict[str, Post]) -> str:
    carded = bool(board.get("carded"))
    headers = CARDED_HEADERS if carded else UNCARDED_HEADERS

    facts = [escape(str(board.get("date") or ""))]
    models = board.get("models")
    if models:
        facts.append(f"{int(models)} contestants")
    checks = board.get("checks")
    if checks:
        facts.append(f"{int(checks)} checks")
    trials = board.get("trials")
    if trials:
        facts.append(f"{int(trials)} trials each")
    invalid = sum(int(r.get("invalid") or 0) for r in board.get("rows", []))
    if invalid:
        facts.append(f"{invalid} invalid")

    post = by_file.get(str(board.get("post") or ""))
    if post is not None:
        facts.append(f'<a href="./{post.html_name}">report</a>')

    note = ""
    if not carded:
        note = (
            '<p class="board-note">Ranked on the objective axis: this board '
            "predates the Model Ability Card, so it carries no OVR.</p>"
        )

    head = "".join(f"<th>{h}</th>" for h in headers)
    return (
        '<div class="board">'
        f'<h3>{escape(str(board.get("label") or ""))}'
        f'<span class="board-facts">{" &middot; ".join(facts)}</span></h3>'
        f"{note}"
        '<div class="board-scroll"><table class="board-table">'
        f"<thead><tr>{head}</tr></thead>"
        f"<tbody>{_board_rows(board)}</tbody>"
        "</table></div></div>"
    )


def _workflow_section(wf: dict, by_file: dict[str, Post]) -> str:
    slug = str(wf.get("id", ""))
    # The persona is a code identifier (`risk_manager`); uppercased for the
    # subtitle its underscore reads as a typo rather than a role.
    persona = str(wf.get("persona") or "").replace("_", " ")
    facts = [escape(str(wf.get("title") or "")), escape(persona)]
    if wf.get("steps"):
        facts.append(f'{int(wf["steps"])} steps')
    if wf.get("par"):
        facts.append(f'par {int(wf["par"])}')

    boards = wf.get("boards") or []
    if boards:
        body = "".join(_board(b, by_file) for b in boards)
    else:
        # empty != unavailable. Omitting the section would let a reader assume
        # the workflow does not exist, rather than that nobody has run it.
        body = (
            '<p class="empty">No board has been run on this workflow yet. '
            "The workflow exists and is scored; the field does not.</p>"
        )

    return (
        f'<section class="wf" id="{escape(slug)}">'
        f'<h2><code>{escape(slug)}</code></h2>'
        f'<p class="wf-facts">{" &middot; ".join(facts)}</p>'
        f"{body}</section>\n"
    )


def render_leaderboard(snapshot: dict, posts: list[Post], theme: str) -> str:
    """Every board we have, grouped by the workflow it was measured on.

    Nothing here is typed: `snapshot` comes from collect_boards.py, which reads
    the arena DB through the same ranking kernel the desk UI uses.
    """
    from boards import ordered_workflows

    by_file = {p.file: p for p in posts}
    sections = "".join(
        _workflow_section(wf, by_file)
        for wf in ordered_workflows(snapshot.get("workflows") or [])
    )
    generated = str(snapshot.get("generated_at", ""))[:10]

    return (
        _head(f"{LEADERBOARD_TITLE} — {SITE_TITLE}", theme, LEADERBOARD_LEAD)
        + '<div class="page">\n'
        + _masthead(True, here="leaderboard", models=bool(snapshot.get("models")))
        + f'<div class="intro"><h1>{escape(LEADERBOARD_TITLE)}</h1>'
        + f'<p class="lead">{escape(LEADERBOARD_LEAD)}</p>'
        + f'<p class="derived">derived from the arena database on {escape(generated)}</p>'
        + "</div>\n"
        + f'<main class="boards">\n{sections}</main>\n'
        + _site_footer()
        + "</div>\n</body></html>\n"
    )


# ---------------------------------------------------------------------------
# The model cards page
# ---------------------------------------------------------------------------

MODELS_TITLE = "Model Cards"
MODELS_LEAD = (
    "One ability card per contestant. The consolidated card averages a model "
    "across every workflow it contested; the per-workflow cards below are the "
    "measurements it averages. A card is absolute, not relative to the field, "
    "which is why averaging cards is sound where merging leaderboards is not."
)
CARD_STATS = (*STAT_ORDER, "CON")


def _stat_strip(stats: dict, con) -> str:
    cells = []
    for stat in CARD_STATS:
        value = con if stat == "CON" else (stats or {}).get(stat)
        muted = ' class="muted"' if value is None else ""
        cells.append(
            f"<span{muted}><b>{_num(value)}</b><small>{stat}</small></span>"
        )
    return f'<div class="mcard-stats">{"".join(cells)}</div>'


def _mcard_head(name: str, effort, ovr, position, rank=None) -> str:
    badge = f'<span class="effort">{escape(str(effort))}</span>' if effort else ""
    pos = f'<span class="mcard-pos">{escape(str(position))}</span>' if position else ""
    place = f'<span class="mcard-rank">#{int(rank)}</span>' if rank else ""
    return (
        '<header class="mcard-head">'
        f'<span class="mcard-ovr"><b>{_num(ovr)}</b><small>OVR</small></span>'
        '<span class="mcard-id">'
        f'<span class="mcard-name">{escape(name)}{badge}</span>'
        f'<span class="mcard-meta">{place}{pos}</span>'
        "</span></header>"
    )


def _career_card(card: dict) -> str:
    """The consolidated card: mean, spread, coverage, and the record behind them."""
    rows = []
    for entry in card.get("per_board") or []:
        ovr = entry.get("ovr")
        # An uncontested board renders an em dash rather than being omitted: a
        # short list reads as "ranked low", which is a different claim.
        place = (
            f'<span class="place">#{int(entry["rank"])}</span>'
            if entry.get("rank") else ""
        )
        rows.append(
            '<li>'
            f'<code>{escape(str(entry.get("workflow") or ""))}</code>'
            f'<span class="board-label">{escape(str(entry.get("label") or ""))}</span>'
            f'<span class="ovr">{_num(ovr)}</span>{place}'
            "</li>"
        )

    coverage = int(card.get("coverage") or 0)
    total = int(card.get("boards_total") or 0)
    spread = (
        f'{_num(card.get("ovr_min"))}&ndash;{_num(card.get("ovr_max"))} '
        f"across {coverage} of {total} boards"
    )
    return (
        '<article class="mcard" '
        f'id="model-{escape(str(card.get("model", "")))}">'
        + _mcard_head(str(card.get("model", "")), card.get("effort"),
                      card.get("ovr"), card.get("position"))
        + _stat_strip(card.get("stats") or {}, card.get("con"))
        + f'<p class="mcard-spread">{spread}</p>'
        + f'<ul class="mcard-boards">{"".join(rows)}</ul>'
        + "</article>"
    )


def _board_card(row: dict, board: dict) -> str:
    """One contestant's card on ONE board — the measurement, not an average."""
    facts = []
    if row.get("objective") is not None:
        facts.append(f'obj {format(row["objective"], ".1f")}')
    if row.get("trials"):
        facts.append(f'{int(row["trials"])} trials')
    if row.get("invalid"):
        facts.append(f'{int(row["invalid"])} invalid')

    return (
        '<article class="mcard">'
        + _mcard_head(str(row.get("model", "")), row.get("effort"),
                      row.get("ovr"), row.get("position"), rank=row.get("rank"))
        + _stat_strip(row.get("stats") or {}, row.get("con"))
        # Escape each fact, then join with the raw separator entity. Escaping
        # the JOINED string yields &amp;middot;, which renders literally.
        + f'<p class="mcard-spread">{" &middot; ".join(escape(f) for f in facts)}</p>'
        + "</article>"
    )


PROVISIONAL_TITLE = "Provisional"
PROVISIONAL_LEAD = (
    "Runs measured outside a contested field. An ability card is an absolute "
    "measurement — passed/total per axis, EFF against each workflow's own par — "
    "so it stays meaningful with no opponent. A rank does not: with no field, a "
    "first place measures nothing. Nothing here carries one, and nothing here "
    "reaches the leaderboard."
)


def _provisional_card(card: dict) -> str:
    """One cards-only measurement. Structurally rankless, not rank-blanked."""
    rows = "".join(
        f'<li><code>{escape(str(e.get("workflow") or ""))}</code>'
        f'<span class="ovr">{_num(e.get("ovr"))}</span></li>'
        for e in card.get("per_workflow") or []
    )
    coverage = int(card.get("coverage") or 0)
    total = int(card.get("workflows_total") or coverage)
    facts = [
        f'{_num(card.get("ovr_min"))}&ndash;{_num(card.get("ovr_max"))} across '
        + (f"{coverage} workflows" if coverage == total
           else f"{coverage} of {total} workflows")
    ]
    # The trial depth has to be stated: CON renders as an em dash here, and
    # without the depth a reader cannot tell "not measured" from "perfect".
    trials = card.get("trials")
    if trials:
        n = int(trials)
        facts.append(f'{n} trial{"s" if n != 1 else ""}')
    return (
        '<article class="mcard">'
        + _mcard_head(str(card.get("model", "")), card.get("effort"),
                      card.get("ovr"), card.get("position"))
        + _stat_strip(card.get("stats") or {}, card.get("con"))
        + f'<p class="mcard-spread">{" &middot; ".join(facts)}</p>'
        + f'<ul class="mcard-boards no-rank">{rows}</ul>'
        + "</article>"
    )


def _provisional_section(entries: list[dict]) -> str:
    blocks = []
    for entry in entries:
        cards = "".join(_provisional_card(c) for c in entry.get("cards") or [])
        date = escape(str(entry.get("date") or ""))
        note = entry.get("note")
        blocks.append(
            f'<p class="board-heading">{escape(str(entry.get("label") or ""))}'
            + (f' <span class="board-facts">{date}</span>' if date else "")
            + "</p>"
            + (f'<p class="board-note">{escape(str(note))}</p>' if note else "")
            + f'<div class="mgrid">{cards}</div>'
        )
    n = sum(len(e.get("cards") or []) for e in entries)
    return _cards_section(
        escape(PROVISIONAL_TITLE),
        f'{n} card{"s" if n != 1 else ""} &middot; measured, never ranked',
        f'<p class="board-note">{escape(PROVISIONAL_LEAD)}</p>' + "".join(blocks),
        anchor="provisional",
    )


def _cards_section(title: str, subtitle: str, body: str, anchor: str = "") -> str:
    ident = f' id="{escape(anchor)}"' if anchor else ""
    return (
        f'<section class="wf"{ident}>'
        f"<h2>{title}</h2>"
        f'<p class="wf-facts">{subtitle}</p>'
        f"{body}</section>\n"
    )


def render_models(snapshot: dict, theme: str) -> str:
    """Consolidated cards first, then the per-workflow cards they average."""
    from boards import ordered_workflows

    cards = snapshot.get("models") or []
    consolidated = _cards_section(
        "All workflows",
        f"{len(cards)} contestants &middot; each card averaged across the boards "
        "it contested",
        f'<div class="mgrid">{"".join(_career_card(c) for c in cards)}</div>',
    )

    # Absence reaches the section, not just the cards: no cards-only run means
    # no heading at all, rather than an empty "Provisional" with nothing under it.
    provisional = (
        _provisional_section(snapshot["provisional"])
        if snapshot.get("provisional") else ""
    )

    sections = []
    for wf in ordered_workflows(snapshot.get("workflows") or []):
        slug = str(wf.get("id", ""))
        persona = str(wf.get("persona") or "").replace("_", " ")
        facts = " &middot; ".join(
            escape(x) for x in (str(wf.get("title") or ""), persona) if x
        )
        boards = wf.get("boards") or []
        if boards:
            body = "".join(
                f'<p class="board-heading">{escape(str(b.get("label") or ""))}</p>'
                f'<div class="mgrid">'
                f'{"".join(_board_card(r, b) for r in b.get("rows") or [])}</div>'
                for b in boards
            )
        else:
            body = (
                '<p class="empty">No board has been run on this workflow yet. '
                "The workflow exists and is scored; the field does not.</p>"
            )
        sections.append(
            _cards_section(f"<code>{escape(slug)}</code>", facts, body, anchor=slug)
        )

    generated = str(snapshot.get("generated_at", ""))[:10]
    return (
        _head(f"{MODELS_TITLE} — {SITE_TITLE}", theme, MODELS_LEAD)
        + '<div class="page">\n'
        + _masthead(True, here="models", models=True)
        + f'<div class="intro"><h1>{escape(MODELS_TITLE)}</h1>'
        + f'<p class="lead">{escape(MODELS_LEAD)}</p>'
        + f'<p class="derived">derived from the arena database on {escape(generated)}</p>'
        + "</div>\n"
        + f'<main class="boards">\n{consolidated}{provisional}{"".join(sections)}</main>\n'
        + _site_footer()
        + "</div>\n</body></html>\n"
    )

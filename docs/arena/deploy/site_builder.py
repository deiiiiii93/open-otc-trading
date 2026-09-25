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
    prefix: str = "./",
) -> str:
    """The site nameplate plus nav.

    `nameplate="full"` belongs to the index alone: there the site title IS the
    page's <h1> and the tagline rides with it, which is why the index carries
    no separate intro band. Everywhere else it is compact — the page's own
    heading has to lead, and repeating the tagline would stack two muted
    paragraphs above the data while the site title outweighed the page the
    reader is actually on.

    `prefix` is how a page in a subdirectory reaches the site. The model pages
    live under models/, where "./index.html" resolves to models/index.html and
    404s on every one of them at once — the same class of sitewide breakage the
    absence rule below exists to prevent, arrived at from the other direction.

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
    links = [link(f"{prefix}index.html", "Blog", "blog")]
    if leaderboard:
        links.append(link(f"{prefix}leaderboard.html", "Leaderboard", "leaderboard"))
    if models:
        links.append(link(f"{prefix}models.html", "Model Cards", "models"))
    # Unconditional, like Blog: the methodology page is prose plus one derived
    # table, so a missing export costs it the table and not the page. Only the
    # two pages that ARE the snapshot are gated above.
    links.append(link(f"{prefix}methodology.html", "Methodology", "methodology"))
    links.append(link(f"{prefix}about.html", "About", "about"))
    links.append(f'<a href="{GITHUB_URL}">GitHub</a>')

    full = nameplate == "full"
    # The eyebrow inherits the root-site link the old A-in-a-square brand owned.
    # Folding the title into the header would otherwise strand it, since the
    # title now points at the arena index.
    plate = '<a class="eyebrow" href="/">Artena</a>'
    plate += (
        f'<h1 class="plate-title">{escape(SITE_TITLE)}</h1>' if full
        else f'<a class="plate-title" href="{prefix}index.html">{escape(SITE_TITLE)}</a>'
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

    # The version the board was scored under, always shown: a reader comparing two
    # boards must be able to see whether they measured the same instrument.
    if board.get("version"):
        facts.append(escape(str(board["version"])))

    post = by_file.get(str(board.get("post") or ""))
    if post is not None:
        facts.append(f'<a href="./{post.html_name}">report</a>')

    notes = []
    if not carded:
        notes.append(
            '<p class="board-note">Ranked on the objective axis: this board '
            "predates the Model Ability Card, so it carries no OVR.</p>"
        )
    # An editorial caveat from boards.yaml. Escaped, because unlike a blurb this
    # is prose about scoring and must not be able to inject markup.
    if board.get("note"):
        notes.append(
            f'<p class="board-note">{escape(str(board["note"]))}</p>'
        )
    note = "".join(notes)

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


def _workflow_tabs(workflows: list[dict]) -> str:
    """The tab bar, built from the SAME ordered list that builds the panels.

    Never enumerate the workflows a second way. The roster taught this once
    already: two independent walks of the snapshot is exactly how a link comes
    to point at something nobody wrote, and here the failure is quieter still —
    a tab whose panel is missing selects nothing and silently leaves the default
    panel showing, which reads as a dead click rather than an error.

    An unmeasured workflow keeps its tab and says so. Dropping it would read as
    "this workflow does not exist" rather than "nobody has run it", the same
    rule its section already follows.
    """
    tabs = []
    for wf in workflows:
        slug = str(wf.get("id", ""))
        empty = "" if wf.get("boards") else '<span class="wf-tab-none">no board</span>'
        tabs.append(
            f'<a class="wf-tab" href="#{escape(slug)}">{escape(slug)}{empty}</a>'
        )
    return f'<nav class="wf-tabs">{"".join(tabs)}</nav>'


#: Shown on every page that puts two scores side by side. Scores from different
#: app or manifest versions measure different instruments, and a reader cannot tell
#: that from the numbers alone.
VERSION_NOTICE = (
    '<p class="board-note">Scores are only comparable within one version. Every '
    "board states the app and workflow-manifest version it was scored under, and a "
    "difference between versions can move a score as much as a difference between "
    'models. <a href="./methodology.html#versions">How versions work</a>.</p>'
)


def render_leaderboard(snapshot: dict, posts: list[Post], theme: str) -> str:
    """Every board we have, grouped by the workflow it was measured on.

    Nothing here is typed: `snapshot` comes from collect_boards.py, which reads
    the arena DB through the same ranking kernel the desk UI uses.

    The workflows are TABBED, selected by `:target` and no JavaScript — there is
    none anywhere on this site. Each panel keeps the id it always had, so
    `#trader-rfq-booking-day` still addresses it and an old deep link becomes a
    tab selection rather than breaking. A fragment naming nothing falls back to
    the default panel, so a stale link costs a reader nothing.
    """
    from boards import ordered_workflows

    by_file = {p.file: p for p in posts}
    workflows = ordered_workflows(snapshot.get("workflows") or [])
    sections = "".join(_workflow_section(wf, by_file) for wf in workflows)
    # Panels first in the DOM, rail second: the reading order is the board, and
    # the grid puts the rail on the right regardless. On a phone the grid
    # collapses and CSS `order` lifts the rail ABOVE the panel, because a
    # control belongs before the thing it controls — the opposite of the
    # index's rail, which is commentary and correctly follows the feed.
    sections = (
        '<div class="wf-layout">'
        f'<div class="wf-panels">{sections}</div>'
        f"{_workflow_tabs(workflows)}"
        "</div>"
    )
    generated = str(snapshot.get("generated_at", ""))[:10]

    return (
        _head(f"{LEADERBOARD_TITLE} — {SITE_TITLE}", theme, LEADERBOARD_LEAD)
        # Wider than every other page, and only this one. It is the only page
        # carrying eleven-column tables, and the rail takes room the widest of
        # them was already using to the pixel.
        + '<div class="page wide">\n'
        + _masthead(True, here="leaderboard", models=bool(snapshot.get("models")))
        + f'<div class="intro"><h1>{escape(LEADERBOARD_TITLE)}</h1>'
        + f'<p class="lead">{escape(LEADERBOARD_LEAD)}</p>'
        + f'<p class="derived">derived from the arena database on {escape(generated)}</p>'
        + VERSION_NOTICE
        + "</div>\n"
        + f'<main class="boards">\n{sections}</main>\n'
        + _site_footer()
        + "</div>\n</body></html>\n"
    )


# ---------------------------------------------------------------------------
# The methodology page
# ---------------------------------------------------------------------------

METHOD_TITLE = "Methodology"
METHOD_LEAD = (
    "What a score on this site is made of: the workflows a model is asked to "
    "work, the checks that grade it, the six stats those checks become, and "
    "the harness that runs the whole thing without a human in the loop."
)

# Weights are stated as an ORDER, not as coefficients. A reader deciding whether
# to trust a board needs to know that grounding outweighs procedure by three to
# one; the third decimal place of the blend is in the source, where it belongs.
OVR_WEIGHTS = (
    ("GRD", "Grounding",
     "Did the numbers in the answer come from the system?", "heaviest"),
    ("ADH", "Adherence",
     "Did it call the right tools, and leave the forbidden ones alone?", "second"),
    ("SYN", "Synthesis",
     "Did the report or artifact it was asked for actually get written?", "third"),
    ("EFF", "Efficiency",
     "How many tool calls did the answer cost?", "third"),
    ("PRC", "Procedure",
     "Did it follow the desk's documented route?", "lightest"),
)

GLOSSARY = (
    # No count here. "Six exist today" was true when this was written and would
    # have been wrong the day a seventh shipped — the same hand-typed staleness
    # the workflow table above is derived to avoid, two sections apart.
    ("Workflow", "A scripted desk day: an ordered set of turns a persona is asked "
     "to work, with the checks that grade each one. The table above lists "
     "every one."),
    ("Run", "One launch of the arena. A run pairs some models with some workflows "
     "and repeats each pairing for a number of trials."),
    ("Board", "A run that was a real field on one workflow, curated by hand. A "
     "one-model smoke test is a run but not a board, because a rank needs opponents."),
    ("Match", "One contestant on one workflow inside one run. Its trials are folded "
     "into a single match, so a two-trial contestant still has one row."),
    ("Trial", "One complete play of a workflow. Trials are what let consistency be "
     "measured: a single trial has nothing to be consistent against."),
    ("Contestant", "A model together with its reasoning effort and its output "
     "budget. The same model at two efforts is two contestants, because both "
     "settings move scores enough to matter."),
    ("Check", "One graded assertion, worth one point. Checks are deterministic "
     "rules over the transcript, never a language model's opinion."),
    ("Axis", "The kind of ability a check tests. Every check belongs to exactly "
     "one of grounding, adherence, synthesis and procedure."),
    ("Par", "A realistic tool-call budget for a competent run, taken as the median "
     "of every fully correct trial a workflow has ever produced. Not the "
     "theoretical minimum, which no real trial approaches."),
    ("OVR", "The single 0&ndash;99 headline number: the five stats blended, then "
     "discounted for inconsistency."),
    ("Archetype", "A label for the shape of a card, not a sixth measurement. "
     "A Sniper leads on grounding, an Anchor on adherence or procedure, a "
     "Playmaker on synthesis or efficiency, and an All-rounder is a card whose "
     "stats sit within eight points of each other."),
    ("Provisional", "A run published as cards but never ranked, because it had no "
     "field. A card is absolute and survives having no opponent; a rank does not."),
    ("Version", "The app release and the workflow-manifest revision a board was "
     "scored under. Each board states both; boards before 2026-09-25 predate "
     "versioning and say so."),
    ("Invalid", "A match the harness threw away because the transport failed, not "
     "because the model did. It is excluded from every average rather than "
     "scored zero."),
)


def _workflow_table(snapshot: dict | None) -> str:
    """Every workflow, read from the same export the leaderboard uses.

    Typed into the page instead, this table would freeze the day a seventh
    workflow shipped — the failure the manifest rules exist to prevent, arrived
    at from the prose side. Absence degrades to a note, never to an empty table:
    a page with a headed table and no rows reads as "there are no workflows".
    """
    from boards import ordered_workflows

    workflows = ordered_workflows((snapshot or {}).get("workflows") or [])
    if not workflows:
        return (
            "<p>The workflow table is derived from the arena database and that "
            "export is not present in this build. The leaderboard names every "
            "workflow that has been measured.</p>"
        )

    rows = []
    for wf in workflows:
        persona = str(wf.get("persona") or "").replace("_", " ")
        # An uncalibrated par is an em dash, not a number: those workflows score
        # efficiency against a theoretical minimum on the older curve, and
        # printing that minimum here would read as a calibrated target.
        par = _num(int(wf["par"]) if wf.get("par") else None)
        boards = len(wf.get("boards") or [])
        rows.append(
            "<tr>"
            f'<td><code>{escape(str(wf.get("id") or ""))}</code></td>'
            f'<td>{escape(str(wf.get("title") or ""))}</td>'
            f"<td>{escape(persona)}</td>"
            f'<td>{_num(int(wf["steps"]) if wf.get("steps") else None)}</td>'
            f"<td>{par}</td>"
            f"<td>{boards}</td>"
            "</tr>"
        )
    head = "".join(
        f"<th>{h}</th>"
        for h in ("Workflow", "Title", "Persona", "Steps", "Par", "Boards")
    )
    return (
        f"<table><thead><tr>{head}</tr></thead>"
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def _weight_list() -> str:
    items = "".join(
        f"<li><b>{code}</b> &mdash; {escape(name.lower())}. {escape(question)} "
        f"<i>Weighted {weight}.</i></li>"
        for code, name, question, weight in OVR_WEIGHTS
    )
    return f"<ul>{items}</ul>"


def _glossary() -> str:
    # Definitions carry an em dash entity, so they are escaped at the source and
    # joined here — escaping the joined string would publish a literal &mdash;.
    items = "".join(
        f"<dt>{escape(term)}</dt><dd>{definition}</dd>" for term, definition in GLOSSARY
    )
    return f'<dl class="glossary">{items}</dl>'


def render_methodology(
    theme: str,
    snapshot: dict | None = None,
    leaderboard: bool = False,
    models: bool = False,
) -> str:
    """How a score is produced, for a reader who has just seen one.

    Built UNCONDITIONALLY, unlike the leaderboard and the cards page: those two
    ARE the snapshot, so without it there is nothing to render and no link. This
    page is prose carrying one derived table, so a missing export costs it a
    table and not a page — which keeps its nav link in the same class as Blog
    and About, and out of the absence rule that gates the other two.
    """
    return (
        _head(f"{METHOD_TITLE} — {SITE_TITLE}", theme, METHOD_LEAD)
        + '<div class="post-shell">\n'
        + _masthead(leaderboard, here="methodology", models=models)
        + f'<div class="intro"><h1>How the Arena measures</h1>'
        + f'<p class="lead">{escape(METHOD_LEAD)}</p></div>\n'
        + '<div class="post-body">\n'

        + "<h2>What a match is</h2>"
        + "<p>One model is given one workflow and works it end to end, several "
        + "times over. Each play is a trial; the trials of one model on one "
        + "workflow fold into a single match, and a set of matches on the same "
        + "workflow is a board.</p>"
        + "<p>The model is not answering questions about a trading desk. It is "
        + "driving one. Every turn goes through the same application the desk "
        + "uses, against a real database seeded for that match, and nothing "
        + "pauses for a human to approve it. A step that books a trade books "
        + "one.</p>"
        + "<p>Nothing is graded on what the model says it did. After the match, "
        + "the transcript is rebuilt from the system's own trace log &mdash; the "
        + "tool calls that actually dispatched, the results they actually "
        + "returned, the skill documents actually opened. A model that describes "
        + "a tool call it never made has described nothing.</p>"

        + "<h2>The workflows</h2>"
        + "<p>Each workflow is a desk day written down: an ordered set of turns "
        + "for one persona, with the checks that grade each turn. They are not "
        + "prompts in the usual sense &mdash; the fixtures behind them are real "
        + "positions, real risk runs and real counterparty documents, and the "
        + "answers are harvested from the system rather than invented.</p>"
        + _workflow_table(snapshot)
        + "<p>Par is the tool-call budget efficiency is scored against. It is "
        + "the median of every fully correct trial a workflow has ever produced, "
        + "so it is a realistic competent run rather than the theoretical "
        + "minimum &mdash; on one workflow the theoretical minimum is eleven "
        + "calls and the leanest real trial in the arena's history took fifteen. "
        + "A workflow with no par yet has not produced enough fully correct "
        + "trials to calibrate one.</p>"

        + '<h2 id="versions">Versions and comparability</h2>'
        + "<p>A score is a measurement taken with a particular instrument, and the "
        + "instrument changes. Two things can change it: the <strong>workflow "
        + "manifest</strong> &mdash; which checks a workflow grades and which "
        + "tools count as the right route &mdash; and the <strong>app</strong> "
        + "itself, including the agent framework every model runs inside. An "
        + "upgrade to that framework changes what every model is told before it "
        + "starts, so a score can move without the model changing at all.</p>"
        + "<p>Since 2026-09-25 every run records both: the app version, and for "
        + "each workflow a manifest version plus a fingerprint of every file that "
        + "grades it. Each board on the leaderboard states its versions. Treat "
        + "scores from different versions as different measurements. A gap "
        + "between two boards on different versions can come from the versions "
        + "as easily as from the models, and a model card that averages across "
        + "versions says so on the card.</p>"
        + "<p>Boards from before that date are marked <em>unversioned</em>. They "
        + "are not wrong, but nothing records exactly which manifest or harness "
        + "they ran, so they compare cleanly only with boards from the same "
        + "period.</p>"
        + "<h2>How a run is graded</h2>"
        + "<p>Every turn carries checks, and every check is worth one point and "
        + "decides itself by rule. Did this tool fire with these arguments. Does "
        + "this number in the answer match the one the system returned. Was this "
        + "artifact written. Was this forbidden tool left alone. There is no "
        + "language model anywhere in the scoring path.</p>"
        + "<p>Each check belongs to one of four axes, and each axis becomes a "
        + "stat scored out of 99. A fifth stat, efficiency, is computed from the "
        + "tool-call count rather than from checks. Those five blend into the "
        + "OVR you see on the leaderboard, in this order:</p>"
        + _weight_list()
        + "<p>Grounding leads deliberately. It is the hardest axis to fake: a "
        + "model cannot talk its way to a number it never fetched. It is also "
        + "the first tie-break when two contestants land on the same OVR.</p>"
        + "<p>Efficiency is scored like golf. Coming in at or under par is full "
        + "marks and beating par earns nothing extra, because the arena is not "
        + "looking for the shortest route. From par the score falls away "
        + "steadily and reaches zero at twice par, and it is gated by "
        + "correctness &mdash; a fast wrong answer scores nothing for being "
        + "fast. Running no tools at all is not efficiency, it is not having "
        + "done the work, and it scores zero.</p>"
        + "<p>The sixth stat, consistency (<b>CON</b>), is the odd one out: it "
        + "is not an "
        + "ability but a spread. It measures how tightly a model's trials "
        + "cluster on the same workflow, and it can only discount the OVR, never "
        + "raise it &mdash; up to about a fifth of the score for a model that "
        + "swings, nothing at all for one that repeats itself. A model with a "
        + "single trial has no consistency and its cell shows an em dash, which "
        + "means not measured, not perfect.</p>"

        + "<h2>The harness</h2>"
        + "<p>The arena runs the production desk, not a mock of it. Each match "
        + "seeds its own fixtures into the database, runs, and then purges "
        + "everything it created &mdash; positions, portfolios, requests, "
        + "scenario files &mdash; on evidence from the trace, so the next "
        + "contestant meets the same desk the last one did. Leftovers are not "
        + "merely untidy here: workflows resolve books by name, so a stale row "
        + "makes each successive contestant's job quietly harder than the last "
        + "one's.</p>"
        + "<p>No number in a score comes from a language model. Pricing, Greeks "
        + "and risk are computed by a pinned deterministic quant engine, and the "
        + "pin is exact rather than a minimum, because the engine's version is "
        + "part of the evidence: a point release once moved a graded valuation "
        + "with no change in this repository at all.</p>"
        + "<p>A contestant is a model together with its reasoning effort and its "
        + "output budget, because both move scores enough to be part of the "
        + "identity. The same model at two efforts appears twice on a board, "
        + "each arm badged, and the two are read as separate contestants rather "
        + "than as one model measured twice.</p>"
        + "<p>When the transport fails rather than the model &mdash; a dead "
        + "gateway, a refused payment, a blank response with errors behind it "
        + "&mdash; the match is recorded invalid and excluded from every "
        + "average. It is not scored zero. An infrastructure outage that reads "
        + "as poor ability is the single easiest way for a leaderboard to lie, "
        + "and it has happened here often enough to be designed against.</p>"

        + "<h2>What the numbers do not say</h2>"
        + "<p><b>Boards are never merged.</b> A different field, a different "
        + "manifest revision, or a repaired harness makes two boards "
        + "incomparable, so each is published exactly as it was measured. Cards "
        + "are a different matter: a card is an absolute measurement that does "
        + "not depend on who else was in the field, which is why the model pages "
        + "can average cards across workflows while the leaderboard refuses to "
        + "merge the boards they came from.</p>"
        + "<p><b>A saturated check measures nothing.</b> When every contestant "
        + "passes a check, it still occupies the denominator while carrying no "
        + "signal about ability. Several workflows have drifted that way as "
        + "models improved, and the boards that show it say so in their own "
        + "notes. A high score on a saturated workflow means the field cleared a "
        + "bar, not that it is strong.</p>"
        + "<p><b>Doing nothing is not zero.</b> Some checks grade restraint, and "
        + "a model that never acts passes all of them by accident. The floor of "
        + "the objective score is therefore a little under eight rather than "
        + "zero, and a score near that floor usually means the transcript is "
        + "empty rather than wrong.</p>"
        + "<p><b>There is no LLM judge in the ranking.</b> A panel of judge "
        + "models exists and can be switched on, but it is off by default and "
        + "advisory when on. It was benched after a run in which it ranked "
        + "models in roughly the reverse of the deterministic axis.</p>"

        + "<h2>Glossary</h2>"
        + _glossary()
        + "</div>\n"
        + _site_footer()
        + "</div>\n</body></html>\n"
    )


# ---------------------------------------------------------------------------
# The model cards page
# ---------------------------------------------------------------------------

MODELS_TITLE = "Model Cards"
MODELS_LEAD = (
    "Every ability card the arena has measured, one row each. Open a model to "
    "see its card and every board measurement behind it. A card is absolute, "
    "not relative to the field, which is why averaging cards is sound where "
    "merging leaderboards is not."
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
        + (
            '<p class="board-note">This average spans boards scored under '
            'different versions. <a href="./methodology.html#versions">Why that '
            "matters</a>.</p>"
            if card.get("mixed_versions") else ""
        )
        + f'<ul class="mcard-boards">{"".join(rows)}</ul>'
        + "</article>"
    )


def _board_card(row: dict, board: dict, workflow: str = "") -> str:
    """One contestant's card on ONE board — the measurement, not an average.

    `workflow` captions the card on a model page, where all of a model's boards
    share a single grid. A section per board put one 268px card alone in a
    1080px column, once per workflow, and buried the comparison the page exists
    to make.
    """
    where = ""
    if workflow:
        where = (
            '<p class="mcard-where"><code>'
            f'{escape(workflow)}</code>'
            f'<span>{escape(str(board.get("label") or ""))}</span></p>'
        )
    facts = []
    if row.get("objective") is not None:
        facts.append(f'obj {format(row["objective"], ".1f")}')
    if row.get("trials"):
        facts.append(f'{int(row["trials"])} trials')
    if row.get("invalid"):
        facts.append(f'{int(row["invalid"])} invalid')

    return (
        '<article class="mcard">'
        + where
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


def _provisional_card(card: dict, run: str = "") -> str:
    """One cards-only measurement. Structurally rankless, not rank-blanked.

    `run` captions the card where a model's arms share a grid, so a reader can
    tell which run and effort produced which numbers without counting boxes. It
    is escaped HERE, like every other card helper's arguments: a caption that
    trusted its caller would be the one unescaped path on the page.
    """
    where = (
        f'<p class="mcard-where"><span>{escape(run)}</span></p>' if run else ""
    )
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
        + where
        + _mcard_head(str(card.get("model", "")), card.get("effort"),
                      card.get("ovr"), card.get("position"))
        + _stat_strip(card.get("stats") or {}, card.get("con"))
        + f'<p class="mcard-spread">{" &middot; ".join(facts)}</p>'
        + f'<ul class="mcard-boards no-rank">{rows}</ul>'
        + "</article>"
    )


def _provisional_section(entries: list[dict], by_file: dict) -> str:
    blocks = []
    for entry in entries:
        cards = "".join(_provisional_card(c) for c in entry.get("cards") or [])
        facts = [escape(str(entry.get("date") or ""))] if entry.get("date") else []
        # An unpublished `post` is simply omitted, exactly as on a board: a link
        # the site cannot resolve is worse than no link.
        post = by_file.get(str(entry.get("post") or ""))
        if post is not None:
            facts.append(f'<a href="./{post.html_name}">report</a>')
        note = entry.get("note")
        blocks.append(
            f'<p class="board-heading">{escape(str(entry.get("label") or ""))}'
            + (f' <span class="board-facts">{" &middot; ".join(facts)}</span>'
               if facts else "")
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


def _cards_section(title: str, subtitle: str, body: str, anchor: str = "",
                   attrs: str = "") -> str:
    ident = f' id="{escape(anchor)}"' if anchor else ""
    return (
        f'<section class="wf"{ident}{attrs}>'
        f"<h2>{title}</h2>"
        f'<p class="wf-facts">{subtitle}</p>'
        f"{body}</section>\n"
    )


def model_index(snapshot: dict) -> list[dict]:
    """Every model the cards page publishes: board contestants first, then the
    ones only ever measured provisionally.

    This is the ONE definition of which models exist. The roster reads it to
    decide what to link and the build loop reads it to decide what to write, so
    the roster cannot offer a page nobody wrote. Each entry carries the
    consolidated `card` (None for a provisional-only model) and every
    provisional `arms` appearance, so a model page needs no second pass over
    the snapshot.
    """
    arms: dict[str, list[dict]] = {}
    for entry in snapshot.get("provisional") or []:
        for card in entry.get("cards") or []:
            arms.setdefault(str(card.get("model", "")), []).append(
                {"entry": entry, "card": card}
            )

    index: list[dict] = []
    seen: set[str] = set()
    for card in snapshot.get("models") or []:
        name = str(card.get("model", ""))
        seen.add(name)
        index.append({"model": name, "card": card, "arms": arms.get(name, [])})
    for name, appearances in arms.items():
        if name not in seen:
            index.append({"model": name, "card": None, "arms": appearances})
    return index


# Model pages live in a subdirectory of their own so `stats.classify()` keeps
# treating them as assets rather than posts — a nested .html is not a page view,
# which is what stops 27 new URLs inflating the published readership totals.
MODELS_DIR = "models"
UP = "../"


def render_model_page(entry: dict, snapshot: dict, theme: str,
                      posts: list | tuple = ()) -> str:
    """One model's whole story on one page.

    The consolidated card leads, then every board measurement it averages. The
    rest of each board's field is deliberately absent: who placed where is a
    board-major question, and /arena/leaderboard.html already answers it in
    full. Restating it here is what made the single cards page unreadable.
    """
    from boards import ordered_workflows

    name = str(entry.get("model", ""))
    card = entry.get("card")
    by_file = {p.file: p for p in posts}

    sections = []
    # empty != unavailable, the same rule an unmeasured workflow gets on the
    # leaderboard. Six of the published models have only ever been measured
    # outside a field; dropping the heading would read as an oversight rather
    # than as the fact it is.
    if card is not None:
        sections.append(_cards_section(
            "All workflows",
            "averaged across the boards this model contested",
            f'<div class="mgrid">{_career_card(card)}</div>',
        ))
    else:
        sections.append(_cards_section(
            "All workflows",
            "no consolidated card",
            '<p class="empty">This model has never contested a board, so there '
            "is nothing to average. Its measurements are below, and none of "
            "them carries a rank.</p>",
        ))

    # One grid across every board, in the site's canonical workflow order. The
    # workflow rides each card as a caption instead of a section heading.
    cards = []
    for wf in ordered_workflows(snapshot.get("workflows") or []):
        slug = str(wf.get("id", ""))
        for board in wf.get("boards") or []:
            cards += [
                _board_card(r, board, workflow=slug)
                for r in board.get("rows") or []
                if str(r.get("model", "")) == name
            ]
    if cards:
        sections.append(_cards_section(
            "Per board",
            f'{len(cards)} measurement{"s" if len(cards) != 1 else ""} '
            "&middot; each against that board's own field",
            f'<div class="mgrid">{"".join(cards)}</div>',
            anchor="boards",
        ))

    arms = entry.get("arms") or []
    if arms:
        # ONE grid, then the caveats. Runs #129 and #130 are the two halves of a
        # single A/B; interleaving each card with its own long run note put them
        # a screen apart and defeated the only comparison they support.
        cards, notes = [], []
        for arm in arms:
            run, arm_card = arm["entry"], arm["card"]
            label = str(run.get("label") or "")
            cards.append(_provisional_card(arm_card, run=label))

            facts = [escape(str(run.get("date") or ""))] if run.get("date") else []
            post = by_file.get(str(run.get("post") or ""))
            if post is not None:
                facts.append(f'<a href="{UP}{post.html_name}">report</a>')
            note = run.get("note")
            if note or facts:
                notes.append(
                    f'<p class="board-heading">{escape(label)}'
                    + (f' <span class="board-facts">{" &middot; ".join(facts)}</span>'
                       if facts else "")
                    + "</p>"
                    + (f'<p class="board-note">{escape(str(note))}</p>'
                       if note else "")
                )
        sections.append(_cards_section(
            escape(PROVISIONAL_TITLE),
            f'{len(arms)} card{"s" if len(arms) != 1 else ""} '
            "&middot; measured, never ranked",
            f'<p class="board-note">{escape(PROVISIONAL_LEAD)}</p>'
            + f'<div class="mgrid">{"".join(cards)}</div>'
            + "".join(notes),
            anchor="provisional",
        ))

    lead = f"Every ability card measured for {name}."
    return (
        _head(f"{name} — {MODELS_TITLE} — {SITE_TITLE}", theme, lead)
        + '<div class="page">\n'
        + _masthead(True, here="models", models=True, prefix=UP)
        + f'<div class="intro"><h1>{escape(name)}</h1>'
        + f'<p class="lead">{escape(lead)}</p>'
        + "</div>\n"
        + f'<main class="boards">\n{"".join(sections)}</main>\n'
        + _site_footer()
        + "</div>\n</body></html>\n"
    )


ROSTER_COLUMNS = ("Model", "OVR", *STAT_ORDER, "CON", "Archetype", "Range")


def _roster_row(name: str, context: str, card: dict, range_cell: str) -> str:
    """One CARD, not one model.

    Seven models own both a consolidated card and provisional arms measured at
    a different effort. Folding those into a single row would mean averaging
    two conditions into one stat line — the cross-condition merge the arena
    refuses everywhere else. A row per card keeps every number a measurement.
    """
    stats = card.get("stats") or {}
    cells = "".join(
        f"<td>{_num(stats.get(stat))}</td>" for stat in STAT_ORDER
    )
    position = str(card.get("position") or "")
    # `data-model` is the ONE hook the search filters on. Matching the visible
    # cell instead would tie the filter to the column order, and matching the
    # whole row would answer a search for "90" with every card carrying a 90 in
    # any of its eight numeric columns.
    return (
        f'<tr data-model="{escape(name)}">'
        f'<td class="model"><a href="{MODELS_DIR}/{escape(name)}.html">'
        f"{escape(name)}</a>"
        + (f'<span class="roster-context">{context}</span>' if context else "")
        + "</td>"
        f'<td class="ovr">{_num(card.get("ovr"))}</td>'
        f"{cells}"
        f'<td class="con">{_num(card.get("con"))}</td>'
        f'<td class="pos">{escape(position) if position else "&mdash;"}</td>'
        f'<td class="range">{range_cell}</td>'
        "</tr>"
    )


def _roster_table(rows: list[str]) -> str:
    head = "".join(f"<th>{escape(c)}</th>" for c in ROSTER_COLUMNS)
    # Reuses the leaderboard's scroll box: ten columns do not fit a phone, and a
    # table that scrolls in its own container beats a page body that does.
    #
    # The "no match" line is a sibling of the box rather than a row inside it,
    # so the search can swap one for the other. A headed table with an empty
    # body reads as "this section has nothing in it" instead of "your query
    # excluded all of it" — empty != unavailable, the same rule an unmeasured
    # workflow already gets on the leaderboard.
    return (
        '<div class="board-scroll" data-roster-table>'
        f'<table class="roster"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        '<p class="roster-none" data-roster-none hidden>No model matches '
        '<b data-roster-echo></b>.</p>'
    )


def _roster_count(n: int) -> str:
    """The card count, in a span the search rewrites while filtering.

    A filtered table advertising its unfiltered total is the same lie the
    derived-leaderboard rules exist to prevent: the number on screen has to
    describe the rows on screen.
    """
    return (f'<span class="roster-count" data-roster-count>'
            f'{n} card{"s" if n != 1 else ""}</span>')


def _career_range(card: dict) -> str:
    coverage = int(card.get("coverage") or 0)
    total = int(card.get("boards_total") or 0)
    # Coverage is not decoration. A 93 averaged over three boards and a 90 over
    # four are not the same claim, and a bare sorted OVR column would invite the
    # reader to treat them as one.
    return (
        f'{_num(card.get("ovr_min"))}&ndash;{_num(card.get("ovr_max"))}'
        f'<span class="roster-sub">{coverage} of {total} boards</span>'
    )


def _arm_range(card: dict) -> str:
    coverage = int(card.get("coverage") or 0)
    total = int(card.get("workflows_total") or coverage)
    scope = (f"{coverage} workflows" if coverage == total
             else f"{coverage} of {total} workflows")
    trials = card.get("trials")
    if trials:
        n = int(trials)
        scope += f' &middot; {n} trial{"s" if n != 1 else ""}'
    return (
        f'{_num(card.get("ovr_min"))}&ndash;{_num(card.get("ovr_max"))}'
        f'<span class="roster-sub">{scope}</span>'
    )


ROSTER_RANKED_LEAD = (
    "One row per consolidated card, ordered by OVR. Deliberately unnumbered: "
    "these means span different sets of boards, so a place in this list would "
    "be the cross-workflow ranking the leaderboard refuses to publish."
)


SEARCH_INPUT_ID = "roster-q"
SEARCH_HINT_ID = "roster-q-hint"
SEARCH_LABEL = "Find a model"
SEARCH_PLACEHOLDER = "e.g. gemini"
SEARCH_HINT = "Filters both tables by model name."


def _roster_search() -> str:
    """The one control on the site, emitted HIDDEN.

    /arena/ shipped no JavaScript at all before this, so a reader with scripts
    off would be handed an input that silently swallows every keystroke. The
    script is what reveals the box, which makes its absence honest rather than
    broken — the same empty-vs-unavailable rule the rest of the site follows,
    arrived at from the other side.

    It sits above both sections because it governs both: seven models own a
    consolidated card AND provisional arms, so a filter that reached only one
    table would hide half of what the reader searched for.
    """
    return (
        '<div class="roster-search" role="search" data-roster-search hidden>'
        f'<label class="roster-search-label" for="{SEARCH_INPUT_ID}">'
        f"{escape(SEARCH_LABEL)}</label>"
        f'<input class="roster-search-input" id="{SEARCH_INPUT_ID}" type="search"'
        ' autocomplete="off" spellcheck="false" data-roster-input'
        f' placeholder="{escape(SEARCH_PLACEHOLDER)}"'
        f' aria-describedby="{SEARCH_HINT_ID}">'
        f'<p class="roster-search-hint" id="{SEARCH_HINT_ID}">'
        f"{escape(SEARCH_HINT)}</p>"
        "</div>\n"
    )


# Inlined, like the stylesheet: there is no build step that could emit a second
# file, and the served CSP is `script-src 'self' 'unsafe-inline'`. Every hook it
# queries is pinned to the markup by a test, because a script looking for an
# attribute the builder never emits fails silently and forever — nothing renders
# wrong, the box simply never filters.
ROSTER_SEARCH_JS = """
(function () {
  var box = document.querySelector('[data-roster-search]');
  var input = box && box.querySelector('[data-roster-input]');
  if (!input) return;
  box.hidden = false;

  var sections = document.querySelectorAll('[data-roster-section]');

  function apply() {
    var raw = input.value.trim();
    var needle = raw.toLowerCase();
    for (var i = 0; i < sections.length; i++) {
      var section = sections[i];
      var rows = section.querySelectorAll('[data-model]');
      var shown = 0;
      for (var j = 0; j < rows.length; j++) {
        var name = rows[j].getAttribute('data-model').toLowerCase();
        var hit = !needle || name.indexOf(needle) !== -1;
        rows[j].hidden = !hit;
        if (hit) shown++;
      }
      var total = rows.length;
      section.querySelector('[data-roster-count]').textContent =
        (shown === total ? String(total) : shown + ' of ' + total) +
        ' card' + (total === 1 ? '' : 's');

      var none = section.querySelector('[data-roster-none]');
      var table = section.querySelector('[data-roster-table]');
      none.querySelector('[data-roster-echo]').textContent = raw;
      none.hidden = shown !== 0;
      table.hidden = shown === 0;
    }
  }

  input.addEventListener('input', apply);
  // `type=search` draws its own clear cross in WebKit, and clearing that way
  // has fired `search` without `input` — which would leave a filtered roster
  // above an empty box. Harmless where the event does not exist.
  input.addEventListener('search', apply);
  apply();
})();
"""


def render_models(snapshot: dict, theme: str, posts: list | tuple = ()) -> str:
    """The roster: every published ability card, one scannable row each.

    The cards themselves live on the per-model pages this links. Board-major
    detail — who placed where in a given field — is /arena/leaderboard.html's
    job, and restating it here as 75 more boxes is what made one page
    unreadable.
    """
    index = model_index(snapshot)

    ranked = [
        _roster_row(e["model"], "", e["card"], _career_range(e["card"]))
        for e in index if e.get("card") is not None
    ]
    arms = []
    for entry in index:
        for arm in entry.get("arms") or []:
            run, card = arm["entry"], arm["card"]
            # The run NUMBER, never the editorial label. A sixty-character
            # headline forces the model column so wide that Range falls off the
            # end of the scroll box, and Range is what keeps OVR honest. The
            # headline has room on the model page, which is one click away.
            number = run.get("run")
            context = escape(
                f"Run #{int(number)}" if number else str(run.get("label") or "")
            )
            effort = card.get("effort")
            if effort:
                context += f' <span class="effort">{escape(str(effort))}</span>'
            arms.append(
                _roster_row(entry["model"], context, card, _arm_range(card))
            )

    sections = []
    if ranked:
        sections.append(_cards_section(
            "Board contestants",
            f"{_roster_count(len(ranked))} "
            "&middot; each averaged across the boards it contested",
            f'<p class="board-note">{escape(ROSTER_RANKED_LEAD)}</p>'
            + _roster_table(ranked),
            anchor="ranked",
            attrs=" data-roster-section",
        ))
    # Absence reaches the section, not just the rows: no cards-only run means
    # no heading at all, rather than an empty "Provisional" with nothing under it.
    if arms:
        sections.append(_cards_section(
            escape(PROVISIONAL_TITLE),
            f"{_roster_count(len(arms))} "
            "&middot; measured, never ranked &middot; grouped by model",
            f'<p class="board-note">{escape(PROVISIONAL_LEAD)}</p>'
            + _roster_table(arms),
            anchor="provisional",
            attrs=" data-roster-section",
        ))

    generated = str(snapshot.get("generated_at", ""))[:10]
    return (
        _head(f"{MODELS_TITLE} — {SITE_TITLE}", theme, MODELS_LEAD)
        + '<div class="page">\n'
        + _masthead(True, here="models", models=True)
        + f'<div class="intro"><h1>{escape(MODELS_TITLE)}</h1>'
        + f'<p class="lead">{escape(MODELS_LEAD)}</p>'
        + f'<p class="derived">derived from the arena database on {escape(generated)}</p>'
        + VERSION_NOTICE
        + "</div>\n"
        # Both halves are gated on there being something to filter. The script
        # would no-op on its own, but shipping a filter for a page with no rows
        # is the same absence-vs-emptiness confusion the rest of the site avoids.
        + (_roster_search() if sections else "")
        + f'<main class="boards">\n{"".join(sections)}</main>\n'
        + _site_footer()
        + (f"<script>{ROSTER_SEARCH_JS}</script>\n" if sections else "")
        + "</div>\n</body></html>\n"
    )

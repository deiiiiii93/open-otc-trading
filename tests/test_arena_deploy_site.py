"""The index is generated from the manifest, so it cannot go stale."""
import datetime as dt
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "docs" / "arena" / "deploy"
sys.path.insert(0, str(DEPLOY))

import manifest as m  # noqa: E402
import site_builder as sb  # noqa: E402

BOARD = m.Post(
    file="2026-08-13-run104-board.md",
    date=dt.date(2026, 8, 13),
    tags=("board",),
    title="Grok 4.6 edges DeepSeek V4 Pro",
    blurb="A one-point tie for opposite reasons.",
    run="Run #104",
    chips=("2 models", "4 workflows"),
    standings=(
        m.Standing(rank=1, model="Grok 4.6", score=80, note="obj 96.3"),
        m.Standing(rank=2, model="DeepSeek V4 Pro", score=79, note="22 calls"),
    ),
)
MEMO = m.Post(
    file="2026-08-18-run110-luna.md",
    date=dt.date(2026, 8, 18),
    tags=("research",),
    title="Does reasoning_effort improve <agent> performance?",
    blurb="Effort is a step at low, not a dial.",
)
POSTS = [MEMO, BOARD]
MINUTES = {"2026-08-18-run110-luna": 8, "2026-08-13-run104-board": 12}
THEME = "body{color:red}"


def test_index_lists_every_post_newest_first():
    html = sb.render_index(POSTS, MINUTES, THEME)
    assert html.index("2026-08-18-run110-luna.html") < html.index("2026-08-13-run104-board.html")


def test_index_escapes_html_in_titles():
    html = sb.render_index(POSTS, MINUTES, THEME)
    assert "&lt;agent&gt;" in html
    assert "<agent>" not in html


def test_index_shows_tags_dates_and_reading_time():
    html = sb.render_index(POSTS, MINUTES, THEME)
    assert "research" in html and "board" in html
    assert "2026-08-18" in html
    assert "8 min" in html and "12 min" in html


def test_index_renders_board_chips():
    html = sb.render_index(POSTS, MINUTES, THEME)
    assert "2 models" in html and "4 workflows" in html and "Run #104" in html


def test_index_rail_uses_the_newest_post_with_standings():
    html = sb.render_index(POSTS, MINUTES, THEME)
    rail = html.split('class="rail"', 1)[1]
    assert "Run #104" in rail
    assert "Grok 4.6" in rail and "DeepSeek V4 Pro" in rail
    assert "obj 96.3" in rail


def test_index_rail_bar_widths_use_a_fixed_axis_not_the_leader():
    """Scaling to the leader makes every bar ~full width in a tight field."""
    html = sb.render_index(POSTS, MINUTES, THEME)
    assert "width:80.8%" in html       # rank 1, score 80 / 99
    assert "width:79.8%" in html       # rank 2, score 79 / 99
    assert "width:100.0%" not in html


def test_index_rail_axis_grows_for_scores_above_the_default():
    tall = m.Post(
        file="x.md", date=BOARD.date, tags=("board",), title="T", blurb="b",
        standings=(m.Standing(rank=1, model="A", score=150),
                   m.Standing(rank=2, model="B", score=75)),
    )
    html = sb.render_index([tall], {"x": 3}, THEME)
    assert "width:100.0%" in html      # 150 becomes the axis
    assert "width:50.0%" in html       # 75 / 150


def test_index_rail_omits_standings_card_when_no_post_has_them():
    html = sb.render_index([MEMO], {"2026-08-18-run110-luna": 8}, THEME)
    assert "Standings" not in html
    assert "Tags" in html              # the tag card still renders


def test_index_tag_card_shows_counts():
    html = sb.render_index(POSTS, MINUTES, THEME)
    rail = html.split('class="rail"', 1)[1]
    assert "research" in rail and "board" in rail


def test_index_inlines_the_theme_and_declares_utf8():
    html = sb.render_index(POSTS, MINUTES, THEME)
    assert "<style>" in html and THEME in html
    assert 'charset="utf-8"' in html
    assert "<!doctype html>" in html.lower()


def test_load_theme_reads_the_css_file():
    css = sb.load_theme(DEPLOY)
    assert "--accent" in css and len(css) > 200


BODY = "<h1>Run #104</h1><p>Body text with a number 96.3.</p>"


def test_post_page_carries_the_report_body_verbatim():
    html = sb.render_post_page(BOARD, BODY, 12, THEME, newer=MEMO, older=None)
    assert BODY in html


def test_post_page_has_breadcrumb_and_byline():
    html = sb.render_post_page(BOARD, BODY, 12, THEME, newer=MEMO, older=None)
    assert 'href="./index.html"' in html
    assert "Run #104" in html
    assert "2026-08-13" in html and "board" in html and "12 min" in html


def test_post_page_links_markdown_and_pdf():
    html = sb.render_post_page(BOARD, BODY, 12, THEME, newer=MEMO, older=None)
    assert 'href="./2026-08-13-run104-board.md"' in html
    assert 'href="./2026-08-13-run104-board.pdf"' in html


def test_post_page_navigation_omits_missing_neighbours():
    newest = sb.render_post_page(MEMO, BODY, 8, THEME, newer=None, older=BOARD)
    assert "2026-08-13-run104-board.html" in newest
    oldest = sb.render_post_page(BOARD, BODY, 12, THEME, newer=MEMO, older=None)
    assert oldest.count("post-nav") == 1
    assert "2026-08-18-run110-luna.html" in oldest


def test_post_page_escapes_titles_in_chrome():
    html = sb.render_post_page(MEMO, BODY, 8, THEME, newer=None, older=BOARD)
    assert "&lt;agent&gt;" in html


def test_about_page_has_method_and_contact():
    html = sb.render_about(POSTS, THEME)
    assert "yaofuxin1993@gmail.com" in html
    assert sb.GITHUB_URL in html
    assert "Repeated trials" in html


def test_contact_sheet_renders_one_figure_per_image():
    html = sb.render_contact_sheet("Run #104 ability cards", ["hero-grok-4-6.png", "mini-a.png"], THEME)
    assert html.count("<figure>") == 2
    assert 'src="./hero-grok-4-6.png"' in html
    assert 'href="./index.html"' not in html   # cards live one level down
    assert 'href="../index.html"' in html


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
    # ">0 reads<" not "0 reads": the latter is a substring of "180 reads".
    assert ">0 reads<" not in html      # a post with no data shows nothing
    # the plan post has no entry in SNAPSHOT["views"], so it gets no element
    assert html.count('class="reads"') == 2


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


# --------------------------------------------------------------------------
# The leaderboard page
# --------------------------------------------------------------------------

import boards as bd  # noqa: E402

TERRA = {
    "rank": 1, "model": "gpt-5-6-terra", "effort": None, "ovr": 86,
    "stats": {"GRD": 99, "ADH": 95, "SYN": 90, "PRC": 87, "EFF": 56},
    "con": 96, "objective": 89.8, "trials": 2, "invalid": 0,
}
LUNA_LOW = {**TERRA, "rank": 2, "model": "gpt-5-6-luna", "effort": "low", "ovr": 85}
LUNA_MAX = {**TERRA, "rank": 2, "model": "gpt-5-6-luna", "effort": "max", "ovr": 85}

FLAGSHIP = {
    "id": "risk-manager-control-day", "title": "Risk Manager Control Day",
    "persona": "risk_manager", "steps": 9, "par": 24,
    "boards": [{
        "run": 20, "label": "Run #20", "date": "2026-07-08",
        "post": "2026-07-13-run20-otc-desk-agent-arena.md",
        "checks": 39, "carded": True, "models": 3,
        "rows": [TERRA, LUNA_LOW, LUNA_MAX],
    }],
}
UNMEASURED = {
    "id": "ops-settlement-day", "title": "Operations Settlement Day",
    "persona": "trader", "steps": 8, "par": None, "boards": [],
}


def snapshot(*workflows):
    return {
        "version": bd.SNAPSHOT_VERSION,
        "generated_at": "2026-08-18T12:00:00+00:00",
        "workflows": list(workflows),
    }


def test_leaderboard_names_each_section_by_its_workflow_slug():
    html = sb.render_leaderboard(snapshot(FLAGSHIP, UNMEASURED), POSTS, THEME)
    assert "risk-manager-control-day" in html
    assert "ops-settlement-day" in html
    assert 'id="risk-manager-control-day"' in html


def test_a_workflow_with_no_board_says_so_instead_of_vanishing():
    """empty != unavailable: the reader must see the gap, not infer it."""
    html = sb.render_leaderboard(snapshot(FLAGSHIP, UNMEASURED), POSTS, THEME)
    assert "No board has been run" in html
    # ...and it sorts after the measured workflow, not before.
    assert html.index("risk-manager-control-day") < html.index("No board has been run")


def test_a_carded_board_shows_ovr_and_every_ability_stat():
    html = sb.render_leaderboard(snapshot(FLAGSHIP), POSTS, THEME)
    assert ">OVR<" in html
    for stat in ("GRD", "ADH", "SYN", "PRC", "EFF"):
        assert f">{stat}<" in html
    assert ">86<" in html


def test_an_uncarded_board_ranks_on_objective_and_offers_no_ovr_column():
    """A pre-card board has no OVR. Rendering the column empty would read as
    'this model scored nothing' rather than 'this instrument had no card'."""
    legacy = {
        **FLAGSHIP,
        "boards": [{
            "run": 8, "label": "Run #8", "date": "2026-06-26", "post": None,
            "checks": None, "carded": False, "models": 1,
            "rows": [{"rank": 1, "model": "claude-sonnet-4-6", "effort": None,
                      "ovr": None, "stats": {}, "con": None,
                      "objective": 93.5, "trials": 5, "invalid": 0}],
        }],
    }
    html = sb.render_leaderboard(snapshot(legacy), POSTS, THEME)
    assert ">OVR<" not in html
    assert "93.5" in html
    assert "objective axis" in html


def test_every_contestant_row_shows_its_effort_arm():
    """Two arms of one model are otherwise identical rows."""
    html = sb.render_leaderboard(snapshot(FLAGSHIP), POSTS, THEME)
    assert html.count('class="effort"') == 2   # luna/low and luna/max, not terra
    assert ">low<" in html and ">max<" in html


def test_shared_ranks_render_once_per_contestant():
    html = sb.render_leaderboard(snapshot(FLAGSHIP), POSTS, THEME)
    assert html.count('class="rank">2<') == 2


def test_a_board_links_to_its_report_only_when_that_report_is_published():
    linked = sb.render_leaderboard(snapshot(FLAGSHIP), POSTS, THEME)
    assert "2026-07-13-run20-otc-desk-agent-arena.html" not in linked  # not in POSTS

    published = m.Post(
        file="2026-07-13-run20-otc-desk-agent-arena.md",
        date=dt.date(2026, 7, 13), tags=("board",),
        title="Model Ability Cards", blurb="A nine-step task.",
    )
    html = sb.render_leaderboard(snapshot(FLAGSHIP), [published], THEME)
    assert "2026-07-13-run20-otc-desk-agent-arena.html" in html


def test_the_masthead_links_the_leaderboard_only_when_one_was_built():
    assert "leaderboard.html" not in sb.render_index(POSTS, MINUTES, THEME)
    assert "leaderboard.html" in sb.render_index(
        POSTS, MINUTES, THEME, leaderboard=True
    )
    assert "leaderboard.html" in sb.render_about(POSTS, THEME, leaderboard=True)


def test_objective_scores_keep_one_decimal_so_a_ceiling_reads_as_a_measurement():
    perfect = {**TERRA, "objective": 100.0}
    html = sb.render_leaderboard(
        snapshot({**FLAGSHIP, "boards": [{**FLAGSHIP["boards"][0], "rows": [perfect]}]}),
        POSTS, THEME,
    )
    assert ">100.0<" in html


def test_a_measured_zero_is_not_rendered_as_a_missing_value():
    """0 is a result; None is the absence of one. They must not look alike."""
    floor = {**TERRA, "con": 0, "stats": {**TERRA["stats"], "EFF": 0}}
    html = sb.render_leaderboard(
        snapshot({**FLAGSHIP, "boards": [{**FLAGSHIP["boards"][0], "rows": [floor]}]}),
        POSTS, THEME,
    )
    assert '<td class="con">0</td>' in html
    assert "<td>0</td>" in html
    assert "&mdash;" not in html


def test_a_persona_identifier_is_humanised_for_the_subtitle():
    html = sb.render_leaderboard(snapshot(FLAGSHIP), POSTS, THEME)
    assert "risk manager" in html and "risk_manager" not in html


# --------------------------------------------------------------------------
# The model cards page
# --------------------------------------------------------------------------

CAREER = {
    "model": "gemini-3-6-flash", "effort": None, "ovr": 90,
    "ovr_min": 84, "ovr_max": 99,
    "stats": {"GRD": 99, "ADH": 98, "SYN": 99, "PRC": 94, "EFF": 62},
    "con": 86, "position": "Sniper", "coverage": 1, "boards_total": 2,
    "per_board": [
        {"workflow": "risk-manager-control-day", "label": "Run #20",
         "ovr": None, "rank": None, "field": 17},
        {"workflow": "ops-settlement-day", "label": "Run #99",
         "ovr": 99, "rank": 1, "field": 18},
    ],
}


def models_snapshot(*workflows, models=(CAREER,)):
    return {**snapshot(*workflows), "models": list(models)}


def test_the_roster_carries_no_workflow_axis_because_that_is_the_leaderboards():
    """Board-major detail — who placed where in a given field — belongs to
    /arena/leaderboard.html, which publishes it as compact tables. Restating it
    here as 75 more boxes is what made the single page unreadable."""
    html = sb.render_models(models_snapshot(FLAGSHIP, UNMEASURED), THEME)
    assert 'id="ranked"' in html
    assert 'id="risk-manager-control-day"' not in html


def test_a_consolidated_card_publishes_the_spread_beside_the_mean():
    html = sb.render_models(models_snapshot(FLAGSHIP), THEME)
    assert ">90<" in html and "84&ndash;99" in html
    assert "Sniper" in html


def test_a_consolidated_card_states_coverage_and_marks_an_uncontested_board():
    """A short list would let the reader assume the model simply ranked low."""
    snap = models_snapshot(FLAGSHIP)
    html = sb.render_model_page(sb.model_index(snap)[0], snap, THEME)
    assert "1 of 2 boards" in html
    card = html[html.index('class="mcard"'):]
    assert "Run #20" in card and "&mdash;" in card


def test_a_per_workflow_card_shows_that_board_rank_not_a_career_average():
    snap = models_snapshot(FLAGSHIP, models=(TERRA_CAREER,))
    html = sb.render_model_page(sb.model_index(snap)[0], snap, THEME)
    board = html[html.index('id="boards"'):]
    assert 'class="mcard-rank">#1<' in board
    assert ">86<" in board          # terra's OVR on this board, not its career mean


def test_model_cards_show_the_effort_arm():
    arm = {**CAREER, "model": "gpt-5-6-luna", "effort": "low"}
    snap = models_snapshot(FLAGSHIP, models=(arm,))
    html = sb.render_model_page(sb.model_index(snap)[0], snap, THEME)
    assert 'class="effort">low<' in html


def test_the_masthead_links_the_cards_page_only_when_one_was_built():
    assert "models.html" not in sb.render_index(POSTS, MINUTES, THEME)
    assert "models.html" in sb.render_index(POSTS, MINUTES, THEME, models=True)
    assert "models.html" in sb.render_about(POSTS, THEME, models=True)


def test_card_footnotes_join_with_a_separator_entity_not_an_escaped_one():
    """escape() over the joined string yields &amp;middot;, which renders as
    literal '&middot;' text — and uppercased by the stylesheet at that."""
    snap = _with_provisional(FLAGSHIP)
    assert "&amp;middot;" not in sb.render_models(snap, THEME)
    for entry in sb.model_index(snap):
        assert "&amp;middot;" not in sb.render_model_page(entry, snap, THEME)
    # ...and the separator still has to reach the page as a separator.
    terra = models_snapshot(FLAGSHIP, models=(TERRA_CAREER,))
    page = sb.render_model_page(sb.model_index(terra)[0], terra, THEME)
    assert "obj 89.8 &middot; 2 trials" in page


# --------------------------------------------------------------------------
# Masthead navigation
# --------------------------------------------------------------------------

def test_every_page_links_back_to_the_blog_feed():
    """The brand points at the main site root, not /arena/, so without this the
    leaderboard and cards pages are one-way doors out of the feed."""
    pages = [
        sb.render_index(POSTS, MINUTES, THEME, leaderboard=True, models=True),
        sb.render_about(POSTS, THEME, leaderboard=True, models=True),
        sb.render_leaderboard(models_snapshot(FLAGSHIP), POSTS, THEME),
        sb.render_models(models_snapshot(FLAGSHIP), THEME),
        sb.render_post_page(MEMO, "<p>body</p>", 8, THEME, None, None,
                            leaderboard=True, models=True),
    ]
    for html in pages:
        nav = html[html.index("<nav>"):html.index("</nav>")]
        assert '<a href="./index.html"' in nav and ">Blog<" in nav


def test_the_blog_link_is_unconditional_unlike_the_derived_pages():
    """index.html is always built; the leaderboard and cards pages are not."""
    nav = sb.render_index(POSTS, MINUTES, THEME)
    nav = nav[nav.index("<nav>"):nav.index("</nav>")]
    assert ">Blog<" in nav
    assert "leaderboard.html" not in nav and "models.html" not in nav


def test_the_current_page_is_marked_in_the_nav():
    def nav_of(html):
        return html[html.index("<nav>"):html.index("</nav>")]

    index = nav_of(sb.render_index(POSTS, MINUTES, THEME, leaderboard=True))
    assert '<a href="./index.html" class="here">Blog</a>' in index

    board = nav_of(sb.render_leaderboard(models_snapshot(FLAGSHIP), POSTS, THEME))
    assert '<a href="./index.html">Blog</a>' in board
    assert 'leaderboard.html" class="here"' in board


# --------------------------------------------------------------------------
# The nameplate header
# --------------------------------------------------------------------------

def test_the_index_carries_the_full_nameplate_instead_of_an_intro_band():
    """Title and tagline moved INTO the header, so the standalone intro block
    must be gone — otherwise the index prints its own identity twice."""
    html = sb.render_index(POSTS, MINUTES, THEME)
    assert '<h1 class="plate-title">' in html
    assert 'class="strap"' in html
    assert sb.SITE_TAGLINE in html
    assert 'class="intro"' not in html


def test_interior_pages_use_a_compact_nameplate_so_their_own_title_leads():
    """A repeated tagline stacks two muted paragraphs above the data, and the
    site title would outweigh the page the reader is actually on."""
    pages = [
        sb.render_leaderboard(models_snapshot(FLAGSHIP), POSTS, THEME),
        sb.render_models(models_snapshot(FLAGSHIP), THEME),
        sb.render_about(POSTS, THEME),
        sb.render_post_page(MEMO, "<p>body</p>", 8, THEME, None, None),
    ]
    for html in pages:
        assert 'class="masthead compact"' in html
        assert 'class="strap"' not in html


def test_the_site_title_is_the_index_h1_and_a_link_everywhere_else():
    """Two <h1>s on one page is what this prevents: on an interior page the
    page's own heading owns the h1, so the nameplate must not claim one."""
    assert sb.render_index(POSTS, MINUTES, THEME).count("<h1") == 1

    board = sb.render_leaderboard(models_snapshot(FLAGSHIP), POSTS, THEME)
    assert board.count("<h1") == 1
    assert f'<a class="plate-title" href="./index.html">{sb.SITE_TITLE}</a>' in board


def test_a_post_page_leaves_the_h1_to_the_report_body():
    html = sb.render_post_page(MEMO, "<p>body</p>", 8, THEME, None, None)
    assert "<h1" not in html


def test_the_eyebrow_keeps_the_link_to_the_root_site():
    """The old A-in-a-square brand owned `/`. Folding the title into the header
    would strand that link, since the big title points at the arena index."""
    for html in (
        sb.render_index(POSTS, MINUTES, THEME),
        sb.render_about(POSTS, THEME),
        sb.render_post_page(MEMO, "<p>body</p>", 8, THEME, None, None),
    ):
        assert '<a class="eyebrow" href="/">Artena</a>' in html


def test_entry_meta_leads_with_the_tag_as_a_kicker():
    """What kind of piece, then when. Date-first read as a log line."""
    meta = sb.render_index(POSTS, MINUTES, THEME).split('class="entry-meta"', 1)[1]
    assert meta.index('class="tag"') < meta.index("<time")


# --------------------------------------------------------------------------
# theme.css invariants
# --------------------------------------------------------------------------

def test_the_serif_token_is_used_and_not_merely_declared():
    """--serif sat in :root unused for months while every glyph on the site was
    sans. A declared token that nothing references is a dead intention."""
    css = (DEPLOY / "theme.css").read_text()
    assert "--serif:" in css
    assert css.count("var(--serif)") >= 3


def test_report_bodies_are_styled_on_the_web_not_only_in_print():
    """render_markdown() returns bare HTML with NO stylesheet — only
    document_html() (the standalone artifact and the PDF) carries
    render_report.py's CSS. Without these rules the site's largest surface
    falls back to browser defaults, and the chart bars render invisible."""
    css = (DEPLOY / "theme.css").read_text()
    for selector in (
        ".post-body p", ".post-body h2", ".post-body table",
        ".post-body .chart", ".post-body .bar",
    ):
        assert selector in css, selector


def test_report_chart_bars_keep_the_semantic_colours_the_pdf_uses():
    """Bar hue carries meaning (podium, judgment). The neutral track and labels
    may warm up to the blog palette; these must not drift from the PDF, or the
    same chart says different things in the two media."""
    theme = (DEPLOY / "theme.css").read_text()
    report = (DEPLOY.parent / "render_report.py").read_text()
    for colour in ("#caa12e", "#9aa0a6", "#b06a3b", "#2e8b57", "#c0392b"):
        assert colour in report, f"{colour} is no longer the report's own"
        assert colour in theme, f"{colour} missing from the web rendering"


# --------------------------------------------------------------------------
# Blurbs carry markdown code spans
# --------------------------------------------------------------------------

def _post(blurb="b", title="T"):
    return m.Post(
        file="2026-08-18-x.md", date=dt.date(2026, 8, 18), tags=("research",),
        title=title, blurb=blurb,
    )


def test_a_blurb_renders_code_spans_instead_of_publishing_backticks():
    """Blurbs are authored in posts.yaml beside markdown prose, so they carry
    `low`-style spans. Escaping alone published the backticks verbatim."""
    p = _post(blurb="Effort is a step at `low`, not a dial.")
    html = sb.render_index([p], {p.stem: 4}, THEME)
    assert "<code>low</code>" in html
    assert "`low`" not in html


def test_a_blurb_code_span_cannot_smuggle_markup():
    """Escape FIRST, then wrap — so the pattern only ever sees escaped text."""
    p = _post(blurb="run `<script>alert(1)</script>` now")
    html = sb.render_index([p], {p.stem: 4}, THEME)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_the_meta_description_stays_plain_text():
    """<meta content> is not a place for tags; only the visible blurb is
    inlined."""
    p = _post(blurb="a `code` span")
    html = sb.render_index([p], {p.stem: 4}, THEME)
    head = html.split("</head>", 1)[0]
    assert "<code>" not in head


def test_titles_are_never_inlined_because_verify_live_compares_them_verbatim():
    """publish.verify_live asserts escape(post.title) appears on the served
    page. Rendering markdown in a title would break the deploy verifier rather
    than the build, which is a far worse failure to diagnose."""
    from html import escape as esc
    p = _post(title="Why `par` matters")
    html = sb.render_index([p], {p.stem: 4}, THEME)
    assert esc(p.title) in html


# --------------------------------------------------------------------------
# Provisional cards (a cards-only run: measured, never ranked)
# --------------------------------------------------------------------------

PROVISIONAL = {
    "run": 113,
    "label": "Run #113",
    "date": "2026-08-18",
    "note": "A single-model smoke across all five golden workflows.",
    "models": 1,
    "cards": [{
        "model": "gemini-3-7-flash", "effort": None, "ovr": 90,
        "ovr_min": 86, "ovr_max": 92,
        "stats": {"GRD": 99, "ADH": 96, "SYN": 99, "PRC": 96, "EFF": 48},
        "con": None, "position": "Sniper", "coverage": 5, "workflows_total": 5,
        "trials": 1,
        "per_workflow": [
            {"workflow": "high-board-portfolio-review-day", "ovr": 92},
            {"workflow": "ops-settlement-day", "ovr": 86},
        ],
    }],
}


def _with_provisional(*workflows):
    return {**models_snapshot(*workflows), "provisional": [PROVISIONAL]}


def test_a_provisional_run_renders_its_card_with_the_run_and_the_note():
    snap = _with_provisional(FLAGSHIP)
    roster = sb.render_models(snap, THEME)
    assert "gemini-3-7-flash" in roster and "Run #113" in roster
    # The caveat that makes an unranked card readable needs room, so it rides
    # the model page rather than a roster cell.
    page = sb.render_model_page(sb.model_index(snap)[1], snap, THEME)
    assert PROVISIONAL["note"] in page


def test_a_provisional_card_carries_no_rank_anywhere():
    """A one-model run has no field, so #1 of 1 measures nothing. Rank is absent
    by construction here, not blanked with an em dash after the fact."""
    snap = _with_provisional(FLAGSHIP)
    html = sb.render_model_page(sb.model_index(snap)[1], snap, THEME)
    section = html.split('id="provisional"', 1)[1].split("</section>", 1)[0]
    assert 'class="place"' not in section
    assert 'class="mcard-rank"' not in section
    # The per-workflow rows drop the place column entirely rather than render an
    # empty one. (A bare "#1" substring check is wrong here: the run LABEL is
    # "Run #113".)
    assert 'class="mcard-boards no-rank"' in section


def test_a_provisional_card_states_its_trial_depth_and_coverage():
    """CON is an em dash here; without the trial count a reader cannot tell
    whether that means unmeasured or perfectly consistent. True of the roster
    row and of the card it indexes, so both carry the depth."""
    snap = _with_provisional(FLAGSHIP)
    roster = sb.render_models(snap, THEME)
    assert "1 trial" in roster and "5 workflows" in roster
    html = sb.render_model_page(sb.model_index(snap)[1], snap, THEME)
    section = html.split('id="provisional"', 1)[1].split("</section>", 1)[0]
    assert "1 trial" in section
    assert "5 workflows" in section
    assert "&mdash;" in section          # CON, not measured


def test_the_provisional_section_sits_after_the_ranked_one():
    """Ranked cards are the page's headline claim; unranked ones qualify it."""
    html = sb.render_models(_with_provisional(FLAGSHIP), THEME)
    assert html.index('id="ranked"') < html.index('id="provisional"')


def test_a_snapshot_with_no_provisional_runs_renders_no_such_section():
    html = sb.render_models(models_snapshot(FLAGSHIP), THEME)
    assert 'id="provisional"' not in html
    assert "Provisional" not in html


def test_the_provisional_section_is_a_verifiable_anchor():
    """publish.verify_live reads section anchors out of the BUILT page and
    requires each to be served, so the section must carry one."""
    import publish

    html = sb.render_models(_with_provisional(FLAGSHIP), THEME)
    assert "provisional" in publish.workflow_anchors(html)


def test_a_provisional_run_links_its_report_when_published():
    """The caveat that makes a provisional card readable usually lives in the
    report, so the reader needs a route to it."""
    entry = {**PROVISIONAL, "post": BOARD.file}
    snap = {**models_snapshot(FLAGSHIP), "provisional": [entry]}
    page = sb.render_model_page(sb.model_index(snap)[1], snap, THEME, POSTS)
    section = page.split('id="provisional"', 1)[1].split("</section>", 1)[0]
    # `../` because a model page sits one level down. `./` would 404.
    assert f'href="../{BOARD.html_name}"' in section and ">report<" in section


def test_a_provisional_report_link_is_omitted_when_the_post_is_unpublished():
    """A link the site cannot resolve is worse than no link — same rule as a
    board's, and the same dangling-pointer class as an unwritten fixture."""
    entry = {**PROVISIONAL, "post": "2020-01-01-nope.md"}
    snap = {**models_snapshot(FLAGSHIP), "provisional": [entry]}
    assert "2020-01-01-nope" not in sb.render_models(snap, THEME, POSTS)
    for e in sb.model_index(snap):
        assert "2020-01-01-nope" not in sb.render_model_page(e, snap, THEME, POSTS)


# --------------------------------------------------------------------------
# Per-model pages: the roster indexes them, and one enumeration feeds both
# --------------------------------------------------------------------------

def test_model_index_lists_each_model_once_ranked_before_provisional_only():
    """ONE definition of which models exist, shared by the roster and the build
    loop. Two independent enumerations is how a roster links a page nobody wrote."""
    index = sb.model_index(_with_provisional(FLAGSHIP))
    assert [e["model"] for e in index] == ["gemini-3-6-flash", "gemini-3-7-flash"]
    assert index[0]["card"] is CAREER and index[0]["arms"] == []
    assert index[1]["card"] is None
    assert [a["card"]["model"] for a in index[1]["arms"]] == ["gemini-3-7-flash"]


TERRA_CAREER = {**CAREER, "model": "gpt-5-6-terra"}


def test_a_model_page_leads_with_its_consolidated_card_then_its_board_cards():
    snap = models_snapshot(FLAGSHIP, models=(TERRA_CAREER,))
    html = sb.render_model_page(sb.model_index(snap)[0], snap, THEME)
    assert "gpt-5-6-terra" in html
    assert "All workflows" in html
    assert html.index("All workflows") < html.index('id="boards"')
    # The page is one model's story: the rest of the field belongs to the board,
    # which /arena/leaderboard.html already publishes in full.
    assert "gpt-5-6-luna" not in html


def test_a_model_page_links_up_one_level_because_it_sits_in_a_subdirectory():
    """models/<id>.html is one level down. A ./index.html in its masthead would
    resolve to models/index.html and 404 on every model page at once."""
    snap = models_snapshot(FLAGSHIP, models=(TERRA_CAREER,))
    html = sb.render_model_page(sb.model_index(snap)[0], snap, THEME)
    assert 'href="../index.html"' in html
    assert 'href="../models.html"' in html
    assert 'href="./' not in html


SECOND_ARM = {
    **PROVISIONAL, "run": 130, "label": "Run #130", "date": "2026-08-26",
    "note": "The same field at low effort.",
    "cards": [{**PROVISIONAL["cards"][0], "effort": "low", "ovr": 86}],
}


def test_a_model_page_gathers_every_provisional_arm_in_one_place():
    """Run #129 at max and Run #130 at low are two halves of one A/B. On the
    single cards page they sat in separate blocks thousands of pixels apart,
    which is the comparison the split exists to make readable."""
    snap = {**models_snapshot(FLAGSHIP), "provisional": [PROVISIONAL, SECOND_ARM]}
    entry = sb.model_index(snap)[1]
    assert entry["model"] == "gemini-3-7-flash"
    html = sb.render_model_page(entry, snap, THEME)
    assert "Run #113" in html and "Run #130" in html
    assert SECOND_ARM["note"] in html
    assert 'class="effort">low<' in html


def test_a_provisional_only_model_keeps_the_section_and_says_why_it_is_empty():
    """empty != unavailable. Dropping the heading would read as an oversight;
    the model has simply never been measured in a contested field."""
    snap = _with_provisional(FLAGSHIP)
    entry = sb.model_index(snap)[1]
    assert entry["card"] is None
    html = sb.render_model_page(entry, snap, THEME)
    assert "All workflows" in html
    assert "never contested a board" in html


def test_the_roster_is_one_linked_row_per_card_not_a_wall_of_boxes():
    """118 cards on one page is unreadable and unnavigable. The roster indexes
    them; the boxes move to the per-model pages where the card metaphor still
    carries the measurement's identity."""
    snap = _with_provisional(FLAGSHIP)
    html = sb.render_models(snap, THEME)
    assert 'href="models/gemini-3-6-flash.html"' in html
    assert 'href="models/gemini-3-7-flash.html"' in html
    assert '<table class="roster">' in html
    assert 'class="mcard"' not in html


def test_the_roster_is_ordered_but_never_numbered():
    """Ordering by OVR is what the cards page already did. NUMBERING it would
    make this the cross-workflow ranking the leaderboard refuses to publish —
    these means span different sets of boards, so a place would be a claim the
    data does not support."""
    html = sb.render_models(_with_provisional(FLAGSHIP), THEME)
    assert "<th>Rank</th>" not in html and "<th>#</th>" not in html
    assert 'class="rank"' not in html and 'class="mcard-rank"' not in html


def test_every_roster_row_carries_its_coverage_beside_its_mean():
    """A 90 averaged over one board and a 90 over two are not the same claim. A
    bare sorted OVR column invites the reader to treat them as one."""
    html = sb.render_models(_with_provisional(FLAGSHIP), THEME)
    assert "1 of 2 boards" in html          # the consolidated card's coverage
    assert "5 workflows" in html            # the provisional arm's


def test_every_class_the_cards_pages_emit_has_a_rule_in_the_stylesheet():
    """theme.css is the ONLY stylesheet a built page carries, so markup with no
    rule renders at browser defaults. That is not hypothetical here: nine report
    pages shipped unstyled for months, and every chart bar collapsed to nothing
    because a .bar has no intrinsic size."""
    import re

    css = (DEPLOY / "theme.css").read_text()
    # The snapshot has to exercise every branch, or the guard passes by never
    # rendering the markup it is meant to protect. CAREER contests no board in
    # FLAGSHIP, so on its own it emits no board card at all.
    snap = {**models_snapshot(FLAGSHIP, SETTLED, models=(CAREER, TERRA_CAREER)),
            "provisional": [PROVISIONAL]}
    pages = [sb.render_models(snap, THEME)]
    pages += [sb.render_model_page(e, snap, THEME) for e in sb.model_index(snap)]

    emitted: set[str] = set()
    for page in pages:
        for attr in re.findall(r'class="([^"]+)"', page):
            emitted.update(attr.split())

    assert sorted(c for c in emitted if f".{c}" not in css) == []


def test_the_roster_scrolls_inside_its_own_box_so_the_page_body_never_does():
    """Ten columns do not fit a phone. A wide table scrolling in its own
    container is fine; the page body scrolling sideways is a different and
    much worse thing."""
    html = sb.render_models(_with_provisional(FLAGSHIP), THEME)
    assert '<div class="board-scroll"><table class="roster">' in html


SETTLED = {
    "id": "ops-settlement-day", "title": "Operations Settlement Day",
    "persona": "trader", "steps": 8, "par": 20,
    "boards": [{
        "run": 99, "label": "Run #99", "date": "2026-08-01", "post": None,
        "checks": 44, "carded": True, "models": 2,
        "rows": [{**TERRA, "rank": 2, "ovr": 91}],
    }],
}


def test_a_model_page_puts_every_board_card_in_one_comparable_grid():
    """A section per board left one 268px card alone in a 1080px column, once
    per workflow. The question a model page answers is how this model moves
    ACROSS boards, which needs the cards beside each other."""
    snap = models_snapshot(FLAGSHIP, SETTLED, models=(TERRA_CAREER,))
    html = sb.render_model_page(sb.model_index(snap)[0], snap, THEME)
    # One grid for the consolidated card, one for every board card.
    assert html.count('class="mgrid"') == 2
    # ...so each card has to name the board it came from itself.
    for fact in ("risk-manager-control-day", "Run #20",
                 "ops-settlement-day", "Run #99"):
        assert fact in html, fact


def test_provisional_arms_share_one_grid_so_the_ab_pair_is_visible_at_once():
    """Runs #129 and #130 are the two halves of one A/B. Pairing each card with
    its own long run note pushed them a screen apart, which defeats the only
    comparison they exist to support. Cards first, notes beneath."""
    snap = {**models_snapshot(FLAGSHIP), "provisional": [PROVISIONAL, SECOND_ARM]}
    html = sb.render_model_page(sb.model_index(snap)[1], snap, THEME)
    section = html.split('id="provisional"', 1)[1].split("</section>", 1)[0]

    assert section.count('class="mgrid"') == 1
    assert "Run #113" in section and "Run #130" in section
    # The caveats still publish, just after the comparison rather than between
    # its halves — a card no reader can put beside its pair is the worse loss.
    assert section.index('class="mcard"') < section.index(SECOND_ARM["note"])


def test_a_provisional_roster_row_names_its_run_by_number_not_by_headline():
    """An editorial run label runs to sixty characters and forced the model
    column so wide that Range fell off the end of the scroll box — hiding the
    coverage that keeps the OVR column honest. The number identifies the run;
    the headline has room on the model page."""
    snap = {**models_snapshot(FLAGSHIP), "provisional": [{
        **PROVISIONAL,
        "label": "Run #113 — a very long editorial headline about the field",
    }]}
    html = sb.render_models(snap, THEME)
    assert "Run #113" in html
    assert "very long editorial headline" not in html


def test_a_run_label_is_escaped_wherever_a_card_caption_carries_it():
    """Every other card helper escapes its own arguments. A caption that trusted
    its caller instead would put the one unescaped path through the roster and
    both model-page grids at once."""
    snap = {**models_snapshot(FLAGSHIP), "provisional": [{
        **PROVISIONAL, "label": "Run #113 <script> & co"}]}
    for html in (sb.render_models(snap, THEME),
                 sb.render_model_page(sb.model_index(snap)[1], snap, THEME)):
        assert "<script>" not in html
    page = sb.render_model_page(sb.model_index(snap)[1], snap, THEME)
    assert "&lt;script&gt; &amp; co" in page
    assert "&amp;lt;" not in page          # ...and escaped exactly once


# --------------------------------------------------------------------------
# The methodology page
# --------------------------------------------------------------------------

def test_the_methodology_page_derives_its_workflow_table_from_the_snapshot():
    """Typed in, this table freezes the day a seventh workflow ships — the
    hand-typed-leaderboard failure arrived at from the prose side."""
    html = sb.render_methodology(THEME, snapshot=snapshot(FLAGSHIP, UNMEASURED))
    assert "risk-manager-control-day" in html
    assert "Risk Manager Control Day" in html
    assert "ops-settlement-day" in html          # unmeasured, still listed
    assert "<td>9</td>" in html                  # the flagship's step count
    assert "<td>24</td>" in html                 # ...and its calibrated par


def test_the_methodology_page_humanises_the_persona_identifier():
    """`risk_manager` uppercased in a table cell reads as a typo, not a role."""
    html = sb.render_methodology(THEME, snapshot=snapshot(FLAGSHIP))
    assert "risk manager" in html
    assert "risk_manager" not in html


def test_an_uncalibrated_par_is_an_em_dash_not_a_number():
    """Those workflows score efficiency against a theoretical minimum on the
    older curve. Printing that minimum here would read as a calibrated target
    and quietly claim the workflow had been anchored on real trials."""
    html = sb.render_methodology(THEME, snapshot=snapshot(UNMEASURED))
    row = html[html.index("<tbody>"):html.index("</tbody>")]
    assert '<td><span class="none">&mdash;</span></td>' in row
    assert "None" not in row
    # ...while a board count of zero stays a zero. That workflow really has had
    # no field, which is a measurement and not a missing value.
    assert row.endswith("<td>0</td></tr>")


def test_the_methodology_page_is_built_without_a_snapshot():
    """Unlike the leaderboard and the cards page, this one is NOT the snapshot:
    it is prose carrying one derived table. An absent export must cost it the
    table and never the page, because its nav link is unconditional and a link
    to an unbuilt page 404s on every page of the site."""
    html = sb.render_methodology(THEME, snapshot=None)
    assert "How the Arena measures" in html
    assert "How a run is graded" in html
    # ...and the missing table says so rather than rendering empty headings.
    assert "<thead>" not in html
    assert "not present in this build" in html


def test_the_methodology_nav_link_is_unconditional_like_the_blog():
    """The two derived pages are gated because absence means they were not
    built. This one is always built, so gating it would only ever hide a page
    that exists."""
    for kwargs in ({}, {"leaderboard": True, "models": True}):
        html = sb.render_methodology(THEME, snapshot=snapshot(FLAGSHIP), **kwargs)
        nav = html[html.index("<nav>"):html.index("</nav>")]
        assert '<a href="./methodology.html"' in nav
    # ...and it reaches every OTHER page's masthead too, or it is unreachable.
    pages = [
        sb.render_index(POSTS, MINUTES, THEME, leaderboard=True, models=True),
        sb.render_about(POSTS, THEME, leaderboard=True, models=True),
        sb.render_leaderboard(models_snapshot(FLAGSHIP), POSTS, THEME),
        sb.render_models(models_snapshot(FLAGSHIP), THEME),
        sb.render_post_page(MEMO, "<p>body</p>", 8, THEME, None, None),
    ]
    for html in pages:
        nav = html[html.index("<nav>"):html.index("</nav>")]
        assert ">Methodology<" in nav
    # A model page lives one directory down and needs the ../ prefix, or the
    # link resolves to models/methodology.html and 404s on all of them at once.
    snap = models_snapshot(FLAGSHIP)
    page = sb.render_model_page(sb.model_index(snap)[0], snap, THEME)
    assert '<a href="../methodology.html"' in page


def test_the_methodology_page_marks_itself_current_in_the_nav():
    html = sb.render_methodology(THEME, snapshot=snapshot(FLAGSHIP))
    assert '<a href="./methodology.html" class="here">Methodology</a>' in html


def test_the_glossary_defines_every_term_the_other_pages_publish():
    """A reader meets these on the leaderboard and the cards before they ever
    reach this page. A glossary that omits one is why they came here."""
    html = sb.render_methodology(THEME, snapshot=snapshot(FLAGSHIP))
    for term in ("OVR", "Par", "Trial", "Contestant", "Board", "Provisional",
                 "Archetype", "Invalid", "Check", "Axis"):
        assert f"<dt>{term}</dt>" in html


def test_the_glossary_em_dash_reaches_the_page_as_a_separator():
    """escape() over a definition already carrying &mdash; publishes a literal
    &amp;mdash; — the joined-string mistake the card footnotes made once."""
    html = sb.render_methodology(THEME, snapshot=snapshot(FLAGSHIP))
    assert "&amp;mdash;" not in html
    assert "&mdash;" in html


def test_every_stat_in_the_card_is_explained_and_weighted():
    """Five stats blend into OVR and a sixth discounts it. A page that names
    four of them leaves the reader to guess which column they are looking at."""
    html = sb.render_methodology(THEME, snapshot=snapshot(FLAGSHIP))
    for stat in sb.CARD_STATS:
        assert stat in html
    for weight in ("heaviest", "lightest"):
        assert weight in html


def test_every_class_the_methodology_page_emits_has_a_rule_in_the_stylesheet():
    """theme.css is the ONLY stylesheet a built page carries, so markup with no
    rule renders at browser defaults. The cards guard covers the cards pages;
    without this one the newest page on the site is the unguarded one."""
    import re

    css = (DEPLOY / "theme.css").read_text()
    # Render WITH a snapshot, or the table branch — the only branch that emits
    # markup of its own — never runs and the guard protects nothing.
    page = sb.render_methodology(THEME, snapshot=snapshot(FLAGSHIP, UNMEASURED),
                                 leaderboard=True, models=True)
    emitted: set[str] = set()
    for attr in re.findall(r'class="([^"]+)"', page):
        emitted.update(attr.split())
    assert sorted(c for c in emitted if f".{c}" not in css) == []


# --------------------------------------------------------------------------
# Leaderboard workflow tabs
# --------------------------------------------------------------------------

def test_the_tab_bar_offers_exactly_the_panels_that_were_built():
    """One enumeration, two renderings. A tab pointing at a panel nobody wrote
    selects nothing and silently leaves the default showing — a dead click with
    no error anywhere, which is the quietest version of the failure the roster's
    single `model_index` exists to prevent."""
    import re

    html = sb.render_leaderboard(models_snapshot(FLAGSHIP, UNMEASURED), POSTS, THEME)
    tabs = re.findall(r'<a class="wf-tab" href="#([^"]+)"', html)
    panels = re.findall(r'<section class="wf" id="([^"]+)"', html)
    assert tabs == panels
    assert tabs == ["risk-manager-control-day", "ops-settlement-day"]


def test_a_workflow_with_no_board_keeps_its_tab_and_says_so():
    """Dropping it would read as "this workflow does not exist" rather than
    "nobody has run it" — the rule its section already follows, applied to the
    control that reaches the section."""
    import re

    html = sb.render_leaderboard(models_snapshot(FLAGSHIP, UNMEASURED), POSTS, THEME)
    # Scope to each anchor ELEMENT. A fixed-width slice runs into the next tab,
    # which is how this test first passed the marker check by accident.
    tabs = dict(re.findall(r'<a class="wf-tab" href="#([^"]+)">(.*?)</a>', html))
    assert "no board" in tabs["ops-settlement-day"]
    assert "no board" not in tabs["risk-manager-control-day"]


def test_the_panels_are_wrapped_so_the_target_rules_have_something_to_bind_to():
    """`.wf-panels > .wf` is the whole selection mechanism. Without the wrapper
    every :target rule silently matches nothing and all six workflows render at
    once — which looks exactly like the page before this change, so nothing
    would report it."""
    html = sb.render_leaderboard(models_snapshot(FLAGSHIP, UNMEASURED), POSTS, THEME)
    assert '<div class="wf-panels">' in html
    body = html[html.index('<div class="wf-panels">'):]
    assert body.count('<section class="wf"') == 2


def test_the_tab_bar_precedes_the_panels():
    """A control below the thing it controls is not a tab bar."""
    html = sb.render_leaderboard(models_snapshot(FLAGSHIP, UNMEASURED), POSTS, THEME)
    assert html.index('class="wf-tabs"') < html.index('class="wf-panels"')


def test_every_panel_keeps_the_id_the_deploy_verifier_scrapes():
    """`publish._find_anchors` reads `<section class="wf" id=...>` out of the
    BUILT page and requires each to serve. Tabbing must not change that markup,
    or the live check goes dark while still reporting success."""
    import sys
    sys.path.insert(0, str(DEPLOY.parent))
    import publish as pub

    html = sb.render_leaderboard(models_snapshot(FLAGSHIP, UNMEASURED), POSTS, THEME)
    assert pub._find_anchors(html) == [
        "risk-manager-control-day", "ops-settlement-day",
    ]


def test_the_target_rules_cover_a_first_panel_that_is_itself_the_target():
    """:has() takes the specificity of its most specific argument, so the rule
    that hides the default outranks a bare `:target:first-child` and clicking
    the FIRST tab would show nothing at all. Both sides carry the same :has()
    prefix so source order settles it instead."""
    css = (DEPLOY / "theme.css").read_text()
    assert ".wf-panels:has(> .wf:target) > .wf{display:none}" in css
    show = ".wf-panels:has(> .wf:target) > .wf:target{display:block}"
    assert show in css
    # ...and the shorthand must come LAST, or equal specificity resolves the
    # other way and every panel stays hidden.
    assert css.index(show) > css.index(".wf-panels:has(> .wf:target) > .wf{")


def test_a_browser_without_has_still_reveals_a_chosen_tab():
    """The bare :target rule is the entire mechanism where :has() is
    unsupported. Without it the fallback is a page stuck on its default panel —
    worse than the extra-panel degradation this design accepts."""
    css = (DEPLOY / "theme.css").read_text()
    assert ".wf-panels > .wf:target{display:block}" in css
    assert ".wf-panels > .wf:first-child{display:block}" in css


def test_printing_the_leaderboard_prints_every_workflow():
    """Tabs are a reading aid on a screen. On paper they would drop five
    workflows out of the record without saying so."""
    css = (DEPLOY / "theme.css").read_text()
    printing = css[css.index("@media print{"):]
    assert ".wf-panels > .wf{display:block !important}" in printing
    assert ".wf-tabs{display:none}" in printing


def test_the_leaderboard_carries_no_javascript():
    """Selection is CSS. A table of measurements must stay readable with script
    blocked, and nothing else on this site needs a script either."""
    html = sb.render_leaderboard(models_snapshot(FLAGSHIP, UNMEASURED), POSTS, THEME)
    assert "<script" not in html
    assert "onclick" not in html


def test_every_class_the_leaderboard_emits_has_a_rule_in_the_stylesheet():
    """The cards and methodology pages each have this guard; the leaderboard
    never did, and it is the page gaining new markup."""
    import re

    css = (DEPLOY / "theme.css").read_text()
    # Both branches: a measured workflow and an unmeasured one, or the guard
    # never renders the empty-state markup it is meant to protect.
    html = sb.render_leaderboard(
        models_snapshot(FLAGSHIP, UNMEASURED), POSTS, THEME
    )
    emitted: set[str] = set()
    for attr in re.findall(r'class="([^"]+)"', html):
        emitted.update(attr.split())

    # Whole-token match, unlike the two older guards: a substring test reads
    # `.n` as covered because `.none` contains it, so the dead class it exists
    # to catch is exactly the one it misses.
    def styled(cls: str) -> bool:
        return re.search(rf"\.{re.escape(cls)}(?![\w-])", css) is not None

    assert sorted(c for c in emitted if not styled(c)) == []


def test_the_selected_panel_heading_does_not_wear_the_tabs_costume():
    """No tab can be highlighted, so the panel heading IS the selection
    indicator. Left as an identical bordered chip directly under the bar it
    reads as a seventh tab that wrapped onto a new row."""
    css = (DEPLOY / "theme.css").read_text()
    rule = css[css.index(".wf-panels .wf h2 code{"):]
    rule = rule[:rule.index("}")]
    for declaration in ("background:none", "border:none"):
        assert declaration in rule, declaration
    # ...and it has to OUTRANK the boxed rule it overrides. The base selector
    # carries one class; qualifying it with the wrapper adds a second, so the
    # override wins on specificity rather than on source order, which is what
    # lets it sit earlier in the file beside the rest of the tab rules.
    assert ".wf h2 code{" in css                      # the boxed base rule
    assert ".wf-panels .wf h2 code{" in css           # strictly more specific

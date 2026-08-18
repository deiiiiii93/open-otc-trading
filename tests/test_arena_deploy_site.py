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


def test_models_page_has_a_consolidated_section_and_one_per_workflow():
    html = sb.render_models(models_snapshot(FLAGSHIP, UNMEASURED), THEME)
    assert "All workflows" in html
    assert 'id="risk-manager-control-day"' in html
    # The consolidated view comes first: it is the page's headline claim.
    assert html.index("All workflows") < html.index('id="risk-manager-control-day"')


def test_a_consolidated_card_publishes_the_spread_beside_the_mean():
    html = sb.render_models(models_snapshot(FLAGSHIP), THEME)
    assert ">90<" in html and "84&ndash;99" in html
    assert "Sniper" in html


def test_a_consolidated_card_states_coverage_and_marks_an_uncontested_board():
    """A short list would let the reader assume the model simply ranked low."""
    html = sb.render_models(models_snapshot(FLAGSHIP), THEME)
    assert "1 of 2 boards" in html
    card = html[html.index('class="mcard"'):]
    assert "Run #20" in card and "&mdash;" in card


def test_a_per_workflow_card_shows_that_board_rank_not_a_career_average():
    html = sb.render_models(models_snapshot(FLAGSHIP), THEME)
    board = html[html.index('id="risk-manager-control-day"'):]
    assert 'class="mcard-rank">#1<' in board
    assert ">86<" in board          # terra's OVR on this board, not its career mean


def test_model_cards_show_the_effort_arm():
    arms = {**CAREER, "model": "gpt-5-6-luna", "effort": "low"}
    html = sb.render_models(models_snapshot(FLAGSHIP, models=(arms,)), THEME)
    assert 'class="effort">low<' in html


def test_a_workflow_with_no_board_still_says_so_on_the_cards_page():
    html = sb.render_models(models_snapshot(FLAGSHIP, UNMEASURED), THEME)
    assert "No board has been run" in html


def test_the_masthead_links_the_cards_page_only_when_one_was_built():
    assert "models.html" not in sb.render_index(POSTS, MINUTES, THEME)
    assert "models.html" in sb.render_index(POSTS, MINUTES, THEME, models=True)
    assert "models.html" in sb.render_about(POSTS, THEME, models=True)


def test_card_footnotes_join_with_a_separator_entity_not_an_escaped_one():
    """escape() over the joined string yields &amp;middot;, which renders as
    literal '&middot;' text — and uppercased by the stylesheet at that."""
    html = sb.render_models(models_snapshot(FLAGSHIP), THEME)
    assert "&amp;middot;" not in html
    assert "obj 89.8 &middot; 2 trials" in html

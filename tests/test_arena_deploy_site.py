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


def test_index_rail_bar_widths_are_relative_to_the_top_score():
    html = sb.render_index(POSTS, MINUTES, THEME)
    assert "width:100.0%" in html      # rank 1, score 80
    assert "width:98.8%" in html       # rank 2, score 79 -> 79/80


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

"""The manifest is authoritative for publication and fails loud on bad input."""
import datetime as dt
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "docs" / "arena" / "deploy"))

import manifest as m  # noqa: E402


def _arena(tmp_path: Path, *names: str) -> Path:
    arena = tmp_path / "arena"
    arena.mkdir()
    for n in names:
        (arena / n).write_text("# Title\n\nsome words here\n")
    return arena


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "posts.yaml"
    p.write_text(text)
    return p


GOOD = """
- file: 2026-08-18-run110-luna.md
  date: 2026-08-18
  tags: [research]
  title: "Does reasoning_effort improve agent performance?"
  blurb: "Effort is a step at low, not a dial."
- file: 2026-08-13-run104-board.md
  date: 2026-08-13
  tags: [board]
  run: "Run #104"
  chips: ["2 models"]
  title: "Grok 4.6 edges DeepSeek V4 Pro"
  blurb: "A one-point tie for opposite reasons."
  standings:
    - {rank: 1, model: "Grok 4.6", score: 80, note: "obj 96.3"}
    - {rank: 2, model: "DeepSeek V4 Pro", score: 79}
"""


def test_loads_and_sorts_newest_first(tmp_path):
    arena = _arena(tmp_path, "2026-08-18-run110-luna.md", "2026-08-13-run104-board.md")
    posts = m.load_manifest(_write(tmp_path, GOOD), arena)
    assert [p.date for p in posts] == [dt.date(2026, 8, 18), dt.date(2026, 8, 13)]
    assert posts[0].stem == "2026-08-18-run110-luna"
    assert posts[0].html_name == "2026-08-18-run110-luna.html"
    assert posts[0].pdf_name == "2026-08-18-run110-luna.pdf"
    assert posts[1].standings[0] == m.Standing(rank=1, model="Grok 4.6", score=80, note="obj 96.3")
    assert posts[1].standings[1].note == ""


def test_missing_blurb_fails_loud_and_names_the_entry(tmp_path):
    arena = _arena(tmp_path, "a.md")
    bad = "- file: a.md\n  date: 2026-08-18\n  tags: [board]\n  title: T\n"
    with pytest.raises(m.ManifestError) as exc:
        m.load_manifest(_write(tmp_path, bad), arena)
    assert "blurb" in str(exc.value) and "a.md" in str(exc.value)


def test_missing_markdown_file_fails_loud(tmp_path):
    arena = _arena(tmp_path)
    bad = "- file: ghost.md\n  date: 2026-08-18\n  tags: [b]\n  title: T\n  blurb: B\n"
    with pytest.raises(m.ManifestError) as exc:
        m.load_manifest(_write(tmp_path, bad), arena)
    assert "ghost.md" in str(exc.value)


def test_unknown_key_fails_loud(tmp_path):
    arena = _arena(tmp_path, "a.md")
    bad = ("- file: a.md\n  date: 2026-08-18\n  tags: [b]\n  title: T\n"
           "  blurb: B\n  standing: []\n")
    with pytest.raises(m.ManifestError) as exc:
        m.load_manifest(_write(tmp_path, bad), arena)
    assert "standing" in str(exc.value)


def test_duplicate_file_fails_loud(tmp_path):
    arena = _arena(tmp_path, "a.md")
    bad = ("- file: a.md\n  date: 2026-08-18\n  tags: [b]\n  title: T\n  blurb: B\n"
           "- file: a.md\n  date: 2026-08-17\n  tags: [b]\n  title: U\n  blurb: C\n")
    with pytest.raises(m.ManifestError) as exc:
        m.load_manifest(_write(tmp_path, bad), arena)
    assert "duplicate" in str(exc.value).lower()


def test_empty_tags_fails_loud(tmp_path):
    arena = _arena(tmp_path, "a.md")
    bad = "- file: a.md\n  date: 2026-08-18\n  tags: []\n  title: T\n  blurb: B\n"
    with pytest.raises(m.ManifestError):
        m.load_manifest(_write(tmp_path, bad), arena)


def test_unpublished_posts_are_excluded(tmp_path):
    arena = _arena(tmp_path, "a.md", "b.md")
    text = ("- file: a.md\n  date: 2026-08-18\n  tags: [b]\n  title: T\n  blurb: B\n"
            "- file: b.md\n  date: 2026-08-17\n  tags: [b]\n  title: U\n  blurb: C\n"
            "  publish: false\n")
    posts = m.load_manifest(_write(tmp_path, text), arena)
    assert [p.file for p in posts] == ["a.md"]


def test_reading_minutes_rounds_up_and_floors_at_one():
    assert m.reading_minutes("word " * 400) == 2
    assert m.reading_minutes("word " * 201) == 2
    assert m.reading_minutes("hello") == 1


def test_tag_counts_are_sorted_by_count_then_name(tmp_path):
    arena = _arena(tmp_path, "a.md", "b.md", "c.md")
    text = ("- file: a.md\n  date: 2026-08-18\n  tags: [board]\n  title: T\n  blurb: B\n"
            "- file: b.md\n  date: 2026-08-17\n  tags: [board]\n  title: U\n  blurb: C\n"
            "- file: c.md\n  date: 2026-08-16\n  tags: [research]\n  title: V\n  blurb: D\n")
    posts = m.load_manifest(_write(tmp_path, text), arena)
    assert m.tag_counts(posts) == [("board", 2), ("research", 1)]


def test_latest_standings_picks_newest_post_that_has_them(tmp_path):
    arena = _arena(tmp_path, "2026-08-18-run110-luna.md", "2026-08-13-run104-board.md")
    posts = m.load_manifest(_write(tmp_path, GOOD), arena)
    latest = m.latest_standings(posts)
    assert latest is not None and latest.run == "Run #104"


def test_latest_standings_is_none_when_no_post_has_them(tmp_path):
    arena = _arena(tmp_path, "a.md")
    text = "- file: a.md\n  date: 2026-08-18\n  tags: [b]\n  title: T\n  blurb: B\n"
    posts = m.load_manifest(_write(tmp_path, text), arena)
    assert m.latest_standings(posts) is None

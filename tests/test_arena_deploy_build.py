"""build() turns a manifest into a complete static site under out_dir."""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "docs" / "arena" / "deploy"
sys.path.insert(0, str(REPO / "docs" / "arena"))
sys.path.insert(0, str(DEPLOY))

import build as b  # noqa: E402
import manifest as m  # noqa: E402

MANIFEST = """
- file: 2026-08-18-run110-luna.md
  date: 2026-08-18
  tags: [research]
  title: "Reasoning effort study"
  blurb: "Effort is a step at low."
- file: 2026-08-13-run104-board.md
  date: 2026-08-13
  tags: [board]
  run: "Run #104"
  title: "Grok edges DeepSeek"
  blurb: "A one-point tie."
  standings:
    - {rank: 1, model: "Grok 4.6", score: 80}
  assets: [cards/run104]
"""


@pytest.fixture
def project(tmp_path):
    arena = tmp_path / "arena"
    (arena / "cards" / "run104").mkdir(parents=True)
    (arena / "cards" / "run104" / "hero-grok.png").write_bytes(b"\x89PNG\r\n")
    (arena / "cards" / "run104" / "verification.json").write_text("{}")
    for name, text in [
        ("2026-08-18-run110-luna.md", "# Reasoning effort\n\nBody one.\n"),
        ("2026-08-13-run104-board.md", "# Run 104\n\nBody two.\n"),
    ]:
        (arena / name).write_text(text)
    mf = arena / "posts.yaml"
    mf.write_text(MANIFEST)
    return mf, arena, tmp_path / "out"


def test_build_emits_a_page_per_post_plus_index_and_about(project):
    mf, arena, out = project
    result = b.build(mf, arena, DEPLOY, out, refresh_pdf=False)
    assert result.pages == 2
    for name in (
        "index.html",
        "about.html",
        "2026-08-18-run110-luna.html",
        "2026-08-13-run104-board.html",
    ):
        assert (out / name).is_file(), name


def test_build_copies_markdown_sources_alongside_pages(project):
    mf, arena, out = project
    b.build(mf, arena, DEPLOY, out, refresh_pdf=False)
    assert (out / "2026-08-18-run110-luna.md").read_text().startswith("# Reasoning effort")


def test_build_copies_assets_and_writes_a_contact_sheet(project):
    mf, arena, out = project
    result = b.build(mf, arena, DEPLOY, out, refresh_pdf=False)
    sheet = out / "cards" / "run104" / "index.html"
    assert (out / "cards" / "run104" / "hero-grok.png").is_file()
    assert sheet.is_file()
    body = sheet.read_text()
    assert "hero-grok.png" in body
    assert "verification.json" not in body   # non-images are copied, not shown
    assert result.assets == 1


def test_build_warns_but_succeeds_when_a_gitignored_asset_dir_is_absent(project):
    mf, arena, out = project
    import shutil

    shutil.rmtree(arena / "cards" / "run104")
    result = b.build(mf, arena, DEPLOY, out, refresh_pdf=False)
    assert result.pages == 2
    assert any("cards/run104" in w for w in result.warnings)


def test_build_is_idempotent_and_clears_stale_output(project):
    mf, arena, out = project
    b.build(mf, arena, DEPLOY, out, refresh_pdf=False)
    (out / "ghost.html").write_text("stale")
    b.build(mf, arena, DEPLOY, out, refresh_pdf=False)
    assert not (out / "ghost.html").exists()


def test_build_propagates_manifest_errors(project, tmp_path):
    mf, arena, out = project
    mf.write_text("- file: nope.md\n  date: 2026-08-18\n  tags: [x]\n  title: T\n  blurb: B\n")
    with pytest.raises(m.ManifestError):
        b.build(mf, arena, DEPLOY, out, refresh_pdf=False)


def test_missing_from_live_reports_unlinked_posts(project):
    mf, arena, out = project
    posts = m.load_manifest(mf, arena)
    live = '<a href="./2026-08-18-run110-luna.html">Reasoning effort study</a>'
    assert b.missing_from_live(live, posts) == ["2026-08-13-run104-board.html"]
    assert b.missing_from_live(live + "2026-08-13-run104-board.html", posts) == []


def test_build_copies_tracked_static_assets_into_the_site_root(project):
    """The orphan card backgrounds must survive rsync --delete."""
    mf, arena, out = project
    b.build(mf, arena, DEPLOY, out, refresh_pdf=False)
    for name in ("model-ability-card-bg-v1.webp", "model-ability-card-bg-v2.webp"):
        assert (out / name).is_file(), f"{name} missing — rsync --delete would drop it"


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

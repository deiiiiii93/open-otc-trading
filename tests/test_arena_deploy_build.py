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


def test_build_renders_no_counters_when_there_is_no_snapshot(project, tmp_path):
    """Must use a TEMP deploy dir: the real one gains a stats.json the moment
    anyone runs `deploy.sh stats`, which would make this test's premise false."""
    mf, arena, out = project
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "theme.css").write_text((DEPLOY / "theme.css").read_text())
    assert not (deploy / "stats.json").exists()

    result = b.build(mf, arena, deploy, out, refresh_pdf=False)
    index = (out / "index.html").read_text()
    assert "Readership" not in index
    assert 'class="reads"' not in index
    assert any("no fresh stats.json" in w for w in result.warnings)


# --------------------------------------------------------------------------
# The leaderboard page
# --------------------------------------------------------------------------

import json  # noqa: E402

import boards as bdm  # noqa: E402

BOARDS = {
    "version": bdm.SNAPSHOT_VERSION,
    "generated_at": "2026-08-18T12:00:00+00:00",
    "workflows": [{
        "id": "risk-manager-control-day", "title": "Risk Manager Control Day",
        "persona": "risk_manager", "steps": 9, "par": 24,
        "boards": [{
            "run": 20, "label": "Run #20", "date": "2026-07-08",
            "post": "2026-08-13-run104-board.md", "checks": 39,
            "carded": True, "carded_rows": 1, "models": 1, "trials": 2,
            "rows": [{"rank": 1, "model": "gpt-5-6-terra", "effort": None,
                      "ovr": 86, "stats": {"GRD": 99, "ADH": 95, "SYN": 90,
                                           "PRC": 87, "EFF": 56},
                      "con": 96, "objective": 89.8, "matches": 1, "trials": 2,
                      "invalid": 0}],
        }],
    }],
}


def _bare_deploy(tmp_path: Path) -> Path:
    """A deploy dir holding only the theme.

    The real one gains boards.json/stats.json the moment anyone runs the
    pipeline, which would silently invalidate any test whose premise is absence.
    """
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "theme.css").write_text((DEPLOY / "theme.css").read_text())
    return deploy


def test_build_emits_the_leaderboard_and_links_it_from_every_page(project, tmp_path):
    mf, arena, out = project
    deploy = _bare_deploy(tmp_path)
    (deploy / "boards.json").write_text(json.dumps(BOARDS))

    b.build(mf, arena, deploy, out, refresh_pdf=False)

    assert (out / "leaderboard.html").is_file()
    assert "risk-manager-control-day" in (out / "leaderboard.html").read_text()
    for page in ("index.html", "about.html", "2026-08-13-run104-board.html"):
        assert "leaderboard.html" in (out / page).read_text(), page


def test_build_without_a_boards_snapshot_emits_no_page_and_no_dead_link(
    project, tmp_path
):
    mf, arena, out = project
    deploy = _bare_deploy(tmp_path)
    assert not (deploy / "boards.json").exists()

    result = b.build(mf, arena, deploy, out, refresh_pdf=False)

    assert not (out / "leaderboard.html").exists()
    for page in ("index.html", "about.html", "2026-08-13-run104-board.html"):
        assert "leaderboard.html" not in (out / page).read_text(), page
    assert any("boards.json" in w for w in result.warnings)


def test_build_warns_when_a_board_links_a_report_that_is_not_published(
    project, tmp_path
):
    """A dangling report link is the same defect class as the unwritten-artifact
    fixture: the page hands the reader a pointer the site cannot resolve."""
    mf, arena, out = project
    deploy = _bare_deploy(tmp_path)
    dangling = json.loads(json.dumps(BOARDS))
    dangling["workflows"][0]["boards"][0]["post"] = "2020-01-01-not-published.md"
    (deploy / "boards.json").write_text(json.dumps(dangling))

    result = b.build(mf, arena, deploy, out, refresh_pdf=False)

    assert any("2020-01-01-not-published.md" in w for w in result.warnings)
    assert "2020-01-01-not-published.html" not in (out / "leaderboard.html").read_text()


MODEL_CARD = {
    "model": "gpt-5-6-terra", "effort": None, "ovr": 88, "ovr_min": 83,
    "ovr_max": 91, "stats": {"GRD": 94, "ADH": 90, "SYN": 89, "PRC": 90, "EFF": 73},
    "con": 92, "position": "Sniper", "coverage": 1, "boards_total": 1,
    "per_board": [{"workflow": "risk-manager-control-day", "label": "Run #20",
                   "ovr": 88, "rank": 1, "field": 17}],
}


def test_build_emits_the_model_cards_page_and_links_it(project, tmp_path):
    mf, arena, out = project
    deploy = _bare_deploy(tmp_path)
    (deploy / "boards.json").write_text(
        json.dumps({**BOARDS, "models": [MODEL_CARD]})
    )

    b.build(mf, arena, deploy, out, refresh_pdf=False)

    assert (out / "models.html").is_file()
    assert "gpt-5-6-terra" in (out / "models.html").read_text()
    for page in ("index.html", "about.html", "leaderboard.html"):
        assert "models.html" in (out / page).read_text(), page


def test_a_snapshot_without_model_cards_still_builds_the_leaderboard(tmp_path, project):
    """The two pages are gated separately: a snapshot exported before model cards
    existed carries boards but no `models` block, and must not lose both."""
    mf, arena, out = project
    deploy = _bare_deploy(tmp_path)
    (deploy / "boards.json").write_text(json.dumps(BOARDS))   # no "models" key

    result = b.build(mf, arena, deploy, out, refresh_pdf=False)

    assert (out / "leaderboard.html").is_file()
    assert not (out / "models.html").exists()
    assert "models.html" not in (out / "index.html").read_text()
    assert any("no model cards" in w for w in result.warnings)


def test_build_warns_when_a_provisional_run_links_an_unpublished_report(
    project, tmp_path
):
    """Same dangling-pointer check as a board's, on the other manifest section."""
    mf, arena, out = project
    deploy = _bare_deploy(tmp_path)
    snap = {**BOARDS, "models": [MODEL_CARD], "provisional": [{
        "run": 104, "label": "Run #104", "date": "2026-08-13",
        "post": "2020-01-01-not-published.md", "note": None, "models": 1,
        "cards": [{"model": "deepseek-v4-pro", "effort": None, "ovr": 79,
                   "ovr_min": 61, "ovr_max": 90,
                   "stats": {"GRD": 93, "ADH": 89, "SYN": 84, "PRC": 93, "EFF": 48},
                   "con": 70, "position": "Sniper", "coverage": 4,
                   "workflows_total": 4, "trials": 2,
                   "per_workflow": [{"workflow": "risk-limit-breach-day", "ovr": 90}]}],
    }]}
    (deploy / "boards.json").write_text(json.dumps(snap))

    result = b.build(mf, arena, deploy, out, refresh_pdf=False)

    assert any("2020-01-01-not-published.md" in w for w in result.warnings)
    assert "2020-01-01-not-published.html" not in (out / "models.html").read_text()
    # The card itself still publishes; only the unresolvable link is dropped.
    assert "deepseek-v4-pro" in (out / "models.html").read_text()

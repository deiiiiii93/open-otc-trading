"""The real manifest must build the real site."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARENA = REPO / "docs" / "arena"
DEPLOY = ARENA / "deploy"
sys.path.insert(0, str(ARENA))
sys.path.insert(0, str(DEPLOY))

import build as b  # noqa: E402
import manifest as m  # noqa: E402


def test_real_manifest_loads_and_every_markdown_exists():
    posts = m.load_manifest(DEPLOY / "posts.yaml", ARENA)
    assert len(posts) >= 9
    for p in posts:
        assert (ARENA / p.file).is_file()


def test_real_manifest_covers_the_two_reports_this_work_was_requested_for():
    files = {p.file for p in m.load_manifest(DEPLOY / "posts.yaml", ARENA)}
    assert "2026-08-18-run110-luna-reasoning-effort.md" in files
    assert "2026-08-17-trap-step-absent-referent.md" in files
    assert "2026-08-13-run104-otc-desk-agent-arena.md" in files, "run #104 gap must close"


def test_real_site_builds_and_indexes_every_post(tmp_path):
    out = tmp_path / "site"
    result = b.build(DEPLOY / "posts.yaml", ARENA, DEPLOY, out, refresh_pdf=False)
    posts = m.load_manifest(DEPLOY / "posts.yaml", ARENA)
    assert result.pages == len(posts)

    index = (out / "index.html").read_text()
    assert b.missing_from_live(index, posts) == []
    for p in posts:
        assert (out / p.html_name).is_file()

    for name in ("model-ability-card-bg-v1.webp", "model-ability-card-bg-v2.webp"):
        assert (out / name).is_file()

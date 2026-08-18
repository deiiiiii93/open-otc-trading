"""Manifest -> a complete static site under build/.

Canonical artifacts (the print-tuned .html and the .pdf) stay in docs/arena/ and
are refreshed only when stale; the site under build/ is disposable and is wiped on
every run so a deleted post cannot linger.
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARENA_DIR = HERE.parent
sys.path.insert(0, str(ARENA_DIR))   # for render_report
sys.path.insert(0, str(HERE))        # for manifest / site_builder

import render_report  # noqa: E402
import site_builder as sb  # noqa: E402
from boards import load_boards  # noqa: E402
from manifest import Post, load_manifest, reading_minutes  # noqa: E402
from stats import load_snapshot  # noqa: E402

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
STATIC_DIR_NAME = "static"


@dataclass
class BuildResult:
    pages: int = 0
    assets: int = 0
    warnings: list[str] = field(default_factory=list)


def ensure_document(md: Path, refresh_pdf: bool) -> tuple[Path, Path]:
    """Refresh docs/arena/<stem>.{html,pdf} when missing or older than the markdown."""
    html = md.with_suffix(".html")
    pdf = md.with_suffix(".pdf")
    src_mtime = md.stat().st_mtime

    if not html.is_file() or html.stat().st_mtime < src_mtime:
        body, label = render_report.render_markdown(md)
        html.write_text(render_report.document_html(body, label, md.name))

    if refresh_pdf and (not pdf.is_file() or pdf.stat().st_mtime < src_mtime):
        render_report.write_pdf(html, pdf)

    return html, pdf


def _copy_assets(
    post: Post, arena_dir: Path, out_dir: Path, theme: str, result: BuildResult
) -> None:
    for rel in post.assets:
        src = arena_dir / rel
        if not src.is_dir():
            # docs/arena/cards/ is gitignored, so a clean checkout has nothing to
            # publish. That is expected, not an error.
            result.warnings.append(f"asset directory missing, skipped: {rel}")
            continue
        dest = out_dir / rel
        dest.mkdir(parents=True, exist_ok=True)
        images: list[str] = []
        for f in sorted(src.iterdir()):
            if not f.is_file():
                continue
            shutil.copy2(f, dest / f.name)
            if f.suffix.lower() in IMAGE_SUFFIXES:
                images.append(f.name)
        title = f"{post.run or post.title} assets"
        (dest / "index.html").write_text(sb.render_contact_sheet(title, images, theme))
        result.assets += 1


def build(
    manifest_path: Path,
    arena_dir: Path,
    deploy_dir: Path,
    out_dir: Path,
    refresh_pdf: bool = True,
    now: dt.datetime | None = None,
) -> BuildResult:
    posts = load_manifest(manifest_path, arena_dir)
    theme = sb.load_theme(deploy_dir)
    result = BuildResult()

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    minutes = {p.stem: reading_minutes((arena_dir / p.file).read_text()) for p in posts}

    # Absent / unreadable / foreign version => None => no page and, crucially, no
    # nav link: a masthead entry for a page we did not build would 404 sitewide.
    board_snapshot = load_boards(deploy_dir / "boards.json")
    has_boards = board_snapshot is not None
    if not has_boards:
        result.warnings.append("no boards.json; the site builds without a leaderboard")
    else:
        published = {p.file for p in posts}
        for wf in board_snapshot.get("workflows") or []:
            for board in wf.get("boards") or []:
                ref = board.get("post")
                if ref and ref not in published:
                    result.warnings.append(
                        f"board {board.get('label')} links {ref}, which is not "
                        "published; the report link is omitted"
                    )

    for i, post in enumerate(posts):
        md = arena_dir / post.file
        html_doc, pdf = ensure_document(md, refresh_pdf)
        body, _ = render_report.render_markdown(md)

        newer = posts[i - 1] if i > 0 else None
        older = posts[i + 1] if i + 1 < len(posts) else None
        page = sb.render_post_page(
            post, body, minutes[post.stem], theme, newer, older,
            leaderboard=has_boards,
        )
        (out_dir / post.html_name).write_text(page)

        shutil.copy2(md, out_dir / post.file)
        if pdf.is_file():
            shutil.copy2(pdf, out_dir / post.pdf_name)
        else:
            result.warnings.append(f"no PDF for {post.file}; its download link will 404")

        _copy_assets(post, arena_dir, out_dir, theme, result)
        result.pages += 1

    # Tracked passthrough assets (e.g. the ability-card backgrounds that were once
    # server-only). They must be IN the build, because publish uses
    # `rsync --delete` and would otherwise remove them from the live site.
    static_dir = deploy_dir / STATIC_DIR_NAME
    if static_dir.is_dir():
        for f in sorted(static_dir.iterdir()):
            if f.is_file():
                shutil.copy2(f, out_dir / f.name)

    # Absent / stale / unparseable => None => no counter markup anywhere.
    snapshot = load_snapshot(
        deploy_dir / "stats.json", now or dt.datetime.now(dt.timezone.utc)
    )
    if snapshot is None:
        result.warnings.append("no fresh stats.json; index renders without counters")

    (out_dir / "index.html").write_text(
        sb.render_index(posts, minutes, theme, snapshot=snapshot,
                        leaderboard=has_boards)
    )
    (out_dir / "about.html").write_text(
        sb.render_about(posts, theme, leaderboard=has_boards)
    )
    if has_boards:
        (out_dir / "leaderboard.html").write_text(
            sb.render_leaderboard(board_snapshot, posts, theme)
        )
    return result


def missing_from_live(index_html: str, posts: list[Post]) -> list[str]:
    """Published posts whose page is not linked from the live index."""
    return [p.html_name for p in posts if p.html_name not in index_html]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the arena blog into build/.")
    ap.add_argument("--out", type=Path, default=HERE / "build")
    ap.add_argument("--no-pdf", action="store_true", help="skip PDF refresh (fast iteration)")
    args = ap.parse_args(argv)

    result = build(
        HERE / "posts.yaml", ARENA_DIR, HERE, args.out, refresh_pdf=not args.no_pdf
    )
    print(f"built {result.pages} pages, {result.assets} asset dirs -> {args.out}")
    for w in result.warnings:
        print(f"  warning: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

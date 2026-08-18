"""posts.yaml -> validated Post records.

The manifest is authoritative for publication: a markdown file absent from it, or
carrying `publish: false`, is not published. Reports are deliberately NOT
discovered by globbing docs/arena/, which also holds plans and drafts.

Every validation failure raises ManifestError naming the offending entry. This is
fail-closed on purpose — a silently derived blurb would ship editorial filler
under a real headline.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from pathlib import Path

import yaml

REQUIRED = {"file", "date", "tags", "title", "blurb"}
OPTIONAL = {"run", "chips", "standings", "assets", "publish"}
ALLOWED = REQUIRED | OPTIONAL

STANDING_REQUIRED = {"rank", "model", "score"}
STANDING_ALLOWED = STANDING_REQUIRED | {"note"}

WORDS_PER_MINUTE = 200


class ManifestError(ValueError):
    """A manifest entry is invalid. The message always names the entry."""


@dataclass(frozen=True)
class Standing:
    rank: int
    model: str
    score: float
    note: str = ""


@dataclass(frozen=True)
class Post:
    file: str
    date: dt.date
    tags: tuple[str, ...]
    title: str
    blurb: str
    run: str | None = None
    chips: tuple[str, ...] = ()
    standings: tuple[Standing, ...] = ()
    assets: tuple[str, ...] = ()
    publish: bool = True

    @property
    def stem(self) -> str:
        return self.file[:-3] if self.file.endswith(".md") else self.file

    @property
    def html_name(self) -> str:
        return f"{self.stem}.html"

    @property
    def pdf_name(self) -> str:
        return f"{self.stem}.pdf"


def _standing(raw: dict, where: str) -> Standing:
    if not isinstance(raw, dict):
        raise ManifestError(f"{where}: each standings entry must be a mapping, got {raw!r}")
    unknown = set(raw) - STANDING_ALLOWED
    if unknown:
        raise ManifestError(f"{where}: unknown standings key(s) {sorted(unknown)}")
    missing = STANDING_REQUIRED - set(raw)
    if missing:
        raise ManifestError(f"{where}: standings entry missing {sorted(missing)}")
    return Standing(
        rank=int(raw["rank"]),
        model=str(raw["model"]),
        score=float(raw["score"]),
        note=str(raw.get("note", "")),
    )


def _post(raw: dict, index: int, arena_dir: Path) -> Post:
    if not isinstance(raw, dict):
        raise ManifestError(f"entry #{index}: must be a mapping, got {raw!r}")
    where = f"entry #{index} ({raw.get('file', '<no file>')})"

    unknown = set(raw) - ALLOWED
    if unknown:
        raise ManifestError(
            f"{where}: unknown key(s) {sorted(unknown)}; allowed {sorted(ALLOWED)}"
        )
    missing = REQUIRED - set(raw)
    if missing:
        raise ManifestError(f"{where}: missing required key(s) {sorted(missing)}")

    for key in ("title", "blurb"):
        if not str(raw[key]).strip():
            raise ManifestError(f"{where}: {key} is empty")

    tags = tuple(str(t) for t in raw["tags"])
    if not tags:
        raise ManifestError(f"{where}: tags must not be empty")

    date = raw["date"]
    if isinstance(date, dt.datetime):
        date = date.date()
    if not isinstance(date, dt.date):
        raise ManifestError(f"{where}: date must be an ISO date, got {date!r}")

    md = arena_dir / str(raw["file"])
    if not md.is_file():
        raise ManifestError(f"{where}: markdown file not found at {md}")

    return Post(
        file=str(raw["file"]),
        date=date,
        tags=tags,
        title=str(raw["title"]).strip(),
        blurb=" ".join(str(raw["blurb"]).split()),
        run=str(raw["run"]) if raw.get("run") else None,
        chips=tuple(str(c) for c in raw.get("chips", ())),
        standings=tuple(_standing(s, where) for s in raw.get("standings", ())),
        assets=tuple(str(a) for a in raw.get("assets", ())),
        publish=bool(raw.get("publish", True)),
    )


def load_manifest(path: Path, arena_dir: Path) -> list[Post]:
    """Load and validate posts.yaml. Returns published posts, newest first."""
    if not path.is_file():
        raise ManifestError(f"manifest not found at {path}")
    raw = yaml.safe_load(path.read_text()) or []
    if not isinstance(raw, list):
        raise ManifestError(f"{path}: top level must be a list of entries")

    posts = [_post(entry, i, arena_dir) for i, entry in enumerate(raw)]

    seen: set[str] = set()
    for p in posts:
        if p.file in seen:
            raise ManifestError(f"duplicate entry for {p.file}")
        seen.add(p.file)

    published = [p for p in posts if p.publish]
    return sorted(published, key=lambda p: (p.date, p.stem), reverse=True)


def reading_minutes(md_text: str) -> int:
    """Whole minutes at 200 wpm, never less than 1."""
    words = len(md_text.split())
    return max(1, math.ceil(words / WORDS_PER_MINUTE))


def tag_counts(posts: list[Post]) -> list[tuple[str, int]]:
    """Tag -> count, most frequent first, ties broken alphabetically."""
    counts: dict[str, int] = {}
    for p in posts:
        for t in p.tags:
            counts[t] = counts.get(t, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def latest_standings(posts: list[Post]) -> Post | None:
    """The newest post carrying standings, or None. Drives the index rail."""
    for p in posts:
        if p.standings:
            return p
    return None

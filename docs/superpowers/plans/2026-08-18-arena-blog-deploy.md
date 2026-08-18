# Arena Blog Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish `docs/arena/*.md` reports as a generated blog at
`https://www.artena.one/arena/` with a one-command deploy, replacing a five-step
manual ritual that has left the site frozen at Run #94.

**Architecture:** A YAML manifest (`docs/arena/deploy/posts.yaml`) is the single
source of truth for the feed. `render_report.py` is split so its markdown→HTML
pipeline is importable; the PDF still renders from the un-chromed document, so
print fidelity is protected structurally. A build step emits a complete static
site into `docs/arena/deploy/build/`, which rsyncs to a server directory served
by a new nginx `alias` — no SPA rebuild.

**Tech Stack:** Python 3.11 (stdlib + `markdown`, `pyyaml`), bash, rsync over
ssh, nginx, Docker Compose (on the server).

**Spec:** `docs/superpowers/specs/2026-08-18-arena-blog-deploy-design.md`

## Global Constraints

- **Never name a module `site.py`.** `site` is a stdlib module; the site builder
  is `site_builder.py`. Verified: `import site` resolves to `.../python3.11/site.py`.
- **`render_report.py` output must stay byte-identical.** All six reports' HTML is
  committed and re-renders to a matching sha256 today. Any refactor is gated on
  reproducing them exactly.
- **Tests live in `tests/`** (`testpaths = ["tests"]`, `pythonpath = ["backend"]`).
  `docs/arena/deploy` is NOT on the pytest pythonpath — test modules insert it
  explicitly via `sys.path`. Do not add docs dirs to the global `pythonpath`.
- **Run tests as** `.venv/bin/python -m pytest` from the repo root. Never pipe
  pytest through `tail`.
- **Fail loud on a missing `blurb`** — `build` refuses and names the entry. Never
  derive a blurb from the markdown.
- **Nothing on the server may be lost at cutover.**
  `/opt/open-slides-zero/frontend/public/arena/` holds `model-ability-card-bg-v1.webp`
  and `-v2.webp` — live, and referenced from nowhere in either repo. Task 7 captures
  them into `docs/arena/deploy/static/` so every build ships them; Task 8 additionally
  refuses to cut over if `rsync --delete` would remove anything else. (The spec said
  "seed from the server"; tracking them in git is strictly stronger — see Deviations.)
- **Verification asserts on body content, never on HTTP status.** The frontend
  container's `try_files $uri $uri/ /index.html` returns 200 for every path.
- **Server facts:** `ubuntu@43.156.158.156`, key `/Users/fuxinyao/ppt-pro-server/slides.pem`,
  deploy dir `/opt/open-slides-zero`, `/usr/bin/rsync` present, containers
  `backend` / `frontend` / `nginx` running.
- **Do not delete `open-slides-zero/frontend/public/arena/`.** It is the rollback.
- **`docs/arena/cards/` is gitignored** — asset publishing reads the working tree
  and a clean checkout legitimately has nothing to publish. That is not an error.

## Deviations from the spec

Two, both deliberate. Neither changes what ships.

1. **`site.py` is named `site_builder.py`, and there is no `templates/` directory.**
   `site` is a Python stdlib module, so a file of that name is a shadowing hazard the
   moment its directory reaches `sys.path`. And `jinja2` is **not installed** in this
   venv (`pyyaml` and `markdown` are), so templates are plain functions returning
   f-strings inside `site_builder.py` rather than a template directory — no new
   dependency, and the escaping is explicit at every interpolation.
2. **The cutover seeds from git, not from the server.** The spec's step 1 was
   "seed `runtime/arena/` from the server's `public/arena/`". That step is dead
   under `rsync -az --delete`: the seeded files would be deleted moments later
   because no build produces them. Task 7 instead tracks the two orphan `.webp`
   files in the repo, and Task 8 guards the invariant directly by refusing to
   proceed when the dry run reports any deletion.

---

### Task 1: Make `render_report.py` importable without changing its output

**Files:**
- Modify: `docs/arena/render_report.py` (whole-file restructure, same behaviour)
- Modify: `pyproject.toml:10-38` (add the undeclared `markdown` dependency)
- Test: `tests/test_arena_render_report.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `run_label(src: Path) -> str`
  - `chart_specs(src: Path) -> list[tuple[str, float, str, list[tuple]]]`
  - `chart_html(spec: tuple) -> str`
  - `render_markdown(src: Path) -> tuple[str, str]` returning `(body_html, label)`
  - `document_html(body: str, label: str, src_name: str) -> str`
  - `write_pdf(html_path: Path, pdf_path: Path) -> bool`
  - `main(argv: list[str] | None = None) -> int`
  - module constants `CSS: str`, `CHROME: str`, `DEFAULT_SPECS: list[tuple]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_arena_render_report.py`:

```python
"""render_report.py must be importable and byte-reproduce the committed reports."""
import hashlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ARENA = REPO / "docs" / "arena"
sys.path.insert(0, str(ARENA))

import render_report  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    "stem",
    [
        "2026-06-27-run8-otc-desk-agent-arena",   # no charts sidecar -> DEFAULT_SPECS
        "2026-07-29-run94-otc-desk-agent-arena",  # has a charts.json sidecar
        "2026-08-13-run104-otc-desk-agent-arena",
    ],
)
def test_document_html_reproduces_committed_report(stem):
    src = ARENA / f"{stem}.md"
    committed = (ARENA / f"{stem}.html").read_bytes()
    body, label = render_report.render_markdown(src)
    rendered = render_report.document_html(body, label, src.name)
    assert _sha(rendered.encode("utf-8")) == _sha(committed)


def test_importing_render_report_writes_nothing(tmp_path):
    """Import must be side-effect free: the old module rendered at import time."""
    import subprocess

    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys, pathlib\n"
        f"sys.path.insert(0, {str(ARENA)!r})\n"
        "before = set(pathlib.Path(sys.path[0]).iterdir())\n"
        "import render_report\n"
        "after = set(pathlib.Path(sys.path[0]).iterdir())\n"
        "assert before == after, sorted(after - before)\n"
        "print('clean')\n"
    )
    out = subprocess.run(
        [sys.executable, str(probe)], capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr
    assert "clean" in out.stdout


def test_run_label_falls_back_when_filename_has_no_run_number():
    assert render_report.run_label(Path("2026-08-17-trap-step-absent-referent.md")) == "Run"
    assert render_report.run_label(Path("2026-08-18-run110-luna.md")) == "Run #110"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_render_report.py -v`
Expected: FAIL — importing `render_report` currently renders a report at import
time (it reads `sys.argv` and writes files at module scope), and
`render_markdown` / `document_html` / `run_label` do not exist.

- [ ] **Step 3: Restructure `render_report.py`**

Keep `CSS`, `LEADERBOARD`, `RELIABILITY`, `PPD` and `CHROME` exactly as they are.
Move every module-scope statement from line 16 onward into functions. The
resulting shape:

```python
#!/usr/bin/env python3
"""Render an arena markdown report into a styled, self-contained HTML report.

The ASCII bar charts in the markdown are swapped for real CSS bar charts so the
HTML/PDF read better than monospace blocks. Output is a single .html file with
all CSS inlined (no external assets) so the PDF render is deterministic.

Importable: `render_markdown()` is reused by docs/arena/deploy/site_builder.py to
build the web version, which wraps the same body in blog chrome. The PDF is
rendered from the un-chromed document, so print output cannot drift when the
site design changes.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import markdown

HERE = Path(__file__).resolve().parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEFAULT_SRC = HERE / "2026-06-27-run8-otc-desk-agent-arena.md"

# ... LEADERBOARD / RELIABILITY / PPD tuples unchanged ...
DEFAULT_SPECS = [LEADERBOARD, RELIABILITY, PPD]


def run_label(src: Path) -> str:
    """"…run110…" -> "Run #110"; no run number in the name -> "Run"."""
    m = re.search(r"run\d+", src.name)
    return m.group(0).replace("run", "Run #") if m else "Run"


def chart_specs(src: Path) -> list:
    """A sibling `<src>.charts.json` overrides the built-in run-8 defaults."""
    sidecar = src.with_suffix(".charts.json")
    if not sidecar.exists():
        return list(DEFAULT_SPECS)
    return [
        (s[0], s[1], s[2], [tuple(r) for r in s[3]])
        for s in json.loads(sidecar.read_text())
    ]


def chart_html(spec) -> str:
    caption, axis_max, unit, rows = spec
    out = ['<figure class="chart">']
    for label, value, cls, vlabel in rows:
        pct = max(value / axis_max * 100, 0.4)  # floor so 0-bars still show a sliver
        out.append(
            '<div class="row">'
            f'<span class="lbl">{label}</span>'
            f'<span class="track"><span class="bar {cls}" style="width:{pct:.1f}%"></span></span>'
            f'<span class="val">{vlabel}</span>'
            '</div>'
        )
    out.append(f'<figcaption>{caption} · axis 0–{axis_max} {unit}</figcaption>')
    out.append('</figure>')
    return "\n".join(out)


def render_markdown(src: Path) -> tuple[str, str]:
    """Return (body_html, run_label) for one report markdown file."""
    md_text = src.read_text()
    specs = chart_specs(src)
    n_blocks = len(re.findall(r"```[^\n]*\n.*?█.*?```", md_text, flags=re.DOTALL))
    charts = iter([chart_html(s) for s in specs[:n_blocks]])
    sentinels: list[str] = []

    def _sub(_m):
        tok = f"@@CHART_{len(sentinels)}@@"
        try:
            sentinels.append(next(charts))
        except StopIteration:
            sentinels.append("")
        return tok

    # fenced blocks that contain a full-block char are our ASCII charts
    md_text = re.sub(r"```[^\n]*\n.*?█.*?```", _sub, md_text, flags=re.DOTALL)

    body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "attr_list", "sane_lists", "md_in_html"],
    )
    # python-markdown wraps a lone sentinel paragraph in <p>…</p>
    for i, html in enumerate(sentinels):
        body = body.replace(f"<p>@@CHART_{i}@@</p>", html).replace(f"@@CHART_{i}@@", html)
    return body, run_label(src)


def document_html(body: str, label: str, src_name: str) -> str:
    """The standalone print-tuned document. This is what the PDF renders from."""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OTC Desk Agent Arena — {label}</title>
<style>{CSS}</style>
</head><body>
{body}
<p class="footer-note">Rendered from
<code>docs/arena/{src_name}</code> ·
OTC Desk Agent Arena · {label}.</p>
</body></html>
"""


def write_pdf(html_path: Path, pdf_path: Path) -> bool:
    """Headless Chrome honours @page, @media print and print-color-adjust."""
    if not Path(CHROME).exists():
        print(f"skipped PDF (Chrome not found at {CHROME})")
        return False
    subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
         "--run-all-compositor-stages-before-draw",
         f"--print-to-pdf={pdf_path}", html_path.as_uri()],
        check=True, capture_output=True,
    )
    print(f"wrote {pdf_path}")
    return True


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    src = (Path(args[0]) if args else DEFAULT_SRC).resolve()
    out_html = src.with_suffix(".html")
    out_pdf = src.with_suffix(".pdf")

    body, label = render_markdown(src)
    doc = document_html(body, label, src.name)
    out_html.write_text(doc)
    print(f"wrote {out_html}  ({len(doc):,} bytes)")

    write_pdf(out_html, out_pdf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

CRITICAL: the `document_html` f-string must reproduce the original whitespace
exactly, including the newline after `{body}` and the line breaks inside the
`footer-note` paragraph. Any change breaks the sha256 gate.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_arena_render_report.py -v`
Expected: PASS — 5 passed (3 parametrized reproductions + import purity + label).

- [ ] **Step 5: Verify the CLI still works and rewrites nothing**

```bash
cp docs/arena/2026-06-28-run9-otc-desk-agent-arena.md /tmp/r9.md
cp docs/arena/2026-06-28-run9-otc-desk-agent-arena.charts.json /tmp/r9.charts.json
```

Note the sidecar must be named for the copy, so instead render in place and check
git reports no change:

```bash
.venv/bin/python docs/arena/render_report.py docs/arena/2026-06-28-run9-otc-desk-agent-arena.md
git diff --stat docs/arena/2026-06-28-run9-otc-desk-agent-arena.html
```
Expected: the render prints `wrote …html` and `wrote …pdf`, and `git diff --stat`
on the `.html` prints **nothing** (the PDF may differ — Chrome embeds a creation
date — so `git checkout -- docs/arena/*.pdf` afterwards).

- [ ] **Step 6: Declare the `markdown` dependency**

`render_report.py` has always imported `markdown` without declaring it. Add it to
`pyproject.toml` alongside `pyyaml`:

```toml
  "pyyaml>=6.0",
  "markdown>=3.5",
```

- [ ] **Step 7: Restore PDFs and commit**

```bash
git checkout -- docs/arena/*.pdf
git add docs/arena/render_report.py tests/test_arena_render_report.py pyproject.toml
git commit -m "refactor(arena): make render_report importable, byte-identical output

Split the module-scope render into functions with a main() guard so the
markdown->HTML pipeline can be reused by the site builder. Gated by a sha256
test against the six committed report HTML files. Declares the previously
undeclared markdown dependency."
```

---

### Task 2: Manifest schema and fail-loud loader

**Files:**
- Create: `docs/arena/deploy/manifest.py`
- Test: `tests/test_arena_deploy_manifest.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `class ManifestError(ValueError)`
  - `@dataclass(frozen=True) class Standing: rank: int; model: str; score: float; note: str = ""`
  - `@dataclass(frozen=True) class Post` with fields `file: str`, `date: datetime.date`,
    `tags: tuple[str, ...]`, `title: str`, `blurb: str`, `run: str | None`,
    `chips: tuple[str, ...]`, `standings: tuple[Standing, ...]`,
    `assets: tuple[str, ...]`, `publish: bool`, and properties
    `stem -> str`, `html_name -> str`, `pdf_name -> str`
  - `load_manifest(path: Path, arena_dir: Path) -> list[Post]` — newest first
  - `reading_minutes(md_text: str) -> int`
  - `tag_counts(posts: list[Post]) -> list[tuple[str, int]]`
  - `latest_standings(posts: list[Post]) -> Post | None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_arena_deploy_manifest.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'manifest'`.

- [ ] **Step 3: Write `docs/arena/deploy/manifest.py`**

```python
"""posts.yaml -> validated Post records.

The manifest is authoritative for publication: a markdown file absent from it,
or carrying `publish: false`, is not published. Reports are deliberately NOT
discovered by globbing docs/arena/, which also holds plans and drafts.

Every validation failure raises ManifestError naming the offending entry. This
is fail-closed on purpose — a silently derived blurb would ship editorial filler
under a real headline.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field
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
        raise ManifestError(f"{where}: unknown key(s) {sorted(unknown)}; allowed {sorted(ALLOWED)}")
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
        standings=tuple(
            _standing(s, where) for s in raw.get("standings", ())
        ),
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_manifest.py -v`
Expected: PASS — 11 passed.

- [ ] **Step 5: Commit**

```bash
git add docs/arena/deploy/manifest.py tests/test_arena_deploy_manifest.py
git commit -m "feat(arena-deploy): fail-loud posts.yaml manifest loader"
```

---

### Task 3: Theme and the blog index page

**Files:**
- Create: `docs/arena/deploy/theme.css`
- Create: `docs/arena/deploy/site_builder.py`
- Test: `tests/test_arena_deploy_site.py`

**Interfaces:**
- Consumes: `manifest.Post`, `manifest.Standing`, `manifest.tag_counts`,
  `manifest.latest_standings` (Task 2).
- Produces:
  - `SITE_TITLE: str`, `SITE_TAGLINE: str`, `GITHUB_URL: str`
  - `load_theme(deploy_dir: Path) -> str`
  - `render_index(posts: list[Post], minutes: dict[str, int], theme: str) -> str`
    where `minutes` maps `post.stem -> reading minutes`

- [ ] **Step 1: Write the failing test**

Create `tests/test_arena_deploy_site.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_site.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'site_builder'`.

- [ ] **Step 3: Write `docs/arena/deploy/theme.css`**

Palette carried over from the existing `/arena/` page so this is a re-layout, not
a rebrand.

```css
:root{
  --bg:#f7f4ee; --paper:#fffdf8; --ink:#191612; --muted:#60584d;
  --line:#d7cdbf; --accent:#9f351f; --accent-2:#1f5f68;
  --sans:-apple-system,"Inter","Segoe UI",Helvetica,Arial,sans-serif;
  --serif:"Charter","Georgia",Cambria,"Times New Roman",serif;
}
*{box-sizing:border-box}
body{margin:0; background:var(--bg); color:var(--ink); font-family:var(--sans);
  line-height:1.6; -webkit-font-smoothing:antialiased}
a{color:var(--accent); text-decoration:none}
a:hover{text-decoration:underline}
.page{max-width:1080px; margin:0 auto; padding:28px 24px 72px}

.masthead{display:flex; align-items:center; justify-content:space-between;
  padding-bottom:18px; border-bottom:1px solid var(--line)}
.brand{display:flex; align-items:center; gap:10px; color:var(--ink); font-weight:700;
  letter-spacing:-.01em}
.brand .mark{display:grid; place-items:center; width:28px; height:28px; border-radius:7px;
  background:var(--accent); color:#fff; font-size:.85rem; font-weight:800}
.masthead nav{display:flex; gap:20px; font-size:.9rem}
.masthead nav a{color:var(--muted)}

.intro{padding:38px 0 26px; border-bottom:1px solid var(--line)}
.intro h1{margin:0 0 .3em; font-size:2.1rem; letter-spacing:-.02em; line-height:1.15}
.intro .lead{margin:0; max-width:60ch; color:var(--muted); font-size:1.02rem}

.layout{display:grid; grid-template-columns:minmax(0,1fr) 268px; gap:40px; padding-top:8px}
@media (max-width:860px){ .layout{grid-template-columns:minmax(0,1fr)} }

.entry{padding:26px 0; border-bottom:1px solid var(--line)}
.entry-meta{display:flex; align-items:center; gap:10px; flex-wrap:wrap;
  font-size:.78rem; color:var(--muted); text-transform:uppercase; letter-spacing:.05em}
.entry h2{margin:.4em 0 .3em; font-size:1.3rem; line-height:1.3; letter-spacing:-.01em}
.entry h2 a{color:var(--ink)}
.entry .blurb{margin:0; color:var(--muted); max-width:62ch}
.tag{padding:2px 8px; border-radius:999px; background:var(--paper);
  border:1px solid var(--line); color:var(--accent-2); font-weight:600}
.chips{display:flex; gap:8px; flex-wrap:wrap; margin-top:12px}
.chip{padding:2px 9px; border-radius:5px; background:var(--paper);
  border:1px solid var(--line); font-size:.76rem; color:var(--muted)}

.rail{display:flex; flex-direction:column; gap:18px}
.rail-card{background:var(--paper); border:1px solid var(--line); border-radius:10px;
  padding:16px 16px 18px}
.rail-card h3{margin:0; font-size:.76rem; text-transform:uppercase; letter-spacing:.08em;
  color:var(--muted)}
.rail-sub{margin:.35em 0 .9em; font-size:.85rem; color:var(--ink); font-weight:600}
.rank-row{display:grid; grid-template-columns:16px minmax(0,1fr) 30px; gap:8px;
  align-items:baseline; margin:9px 0; font-size:.83rem}
.rank-row .rank{color:var(--muted); font-variant-numeric:tabular-nums}
.rank-row .model{font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
.rank-row .score{text-align:right; font-variant-numeric:tabular-nums; font-weight:700}
.rank-row .note{grid-column:2/4; color:var(--muted); font-size:.75rem; margin-top:-2px}
.bar-track{grid-column:1/4; height:4px; border-radius:3px; background:var(--line)}
.bar-fill{display:block; height:100%; border-radius:3px; background:var(--accent)}
.tag-list{display:flex; flex-direction:column; gap:7px; margin-top:12px; font-size:.85rem}
.tag-list span{display:flex; justify-content:space-between; color:var(--muted)}
.tag-list b{color:var(--ink); font-weight:600}

footer.site{margin-top:44px; padding-top:18px; border-top:1px solid var(--line);
  font-size:.82rem; color:var(--muted)}

/* post pages: chrome only — the report body keeps render_report.py's own CSS */
.post-shell{max-width:880px; margin:0 auto; padding:28px 24px 72px}
.crumb{font-size:.82rem; color:var(--muted); margin-bottom:6px}
.crumb a{color:var(--muted)}
.byline{display:flex; gap:10px; flex-wrap:wrap; font-size:.78rem; color:var(--muted);
  text-transform:uppercase; letter-spacing:.05em; padding-bottom:18px;
  border-bottom:1px solid var(--line); margin-bottom:8px}
.post-body{background:var(--paper); border:1px solid var(--line); border-radius:12px;
  padding:8px 34px 26px; margin-top:22px}
.post-nav{display:flex; justify-content:space-between; gap:18px; flex-wrap:wrap;
  margin-top:26px; padding-top:18px; border-top:1px solid var(--line); font-size:.88rem}
.post-nav .downloads{color:var(--muted)}
.sheet{display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:18px;
  margin-top:24px}
.sheet figure{margin:0; background:var(--paper); border:1px solid var(--line);
  border-radius:10px; padding:12px}
.sheet img{width:100%; height:auto; display:block; border-radius:6px}
.sheet figcaption{margin-top:8px; font-size:.76rem; color:var(--muted); word-break:break-all}
```

- [ ] **Step 4: Write `docs/arena/deploy/site_builder.py` (index only for now)**

```python
"""Pure HTML generation for the arena blog. No filesystem writes — build.py owns IO.

Everything the index shows is derived from the manifest, so the page cannot
freeze the way the hand-typed Run #94 leaderboards did.
"""
from __future__ import annotations

from html import escape
from pathlib import Path

from manifest import Post, latest_standings, tag_counts

SITE_TITLE = "The OTC Desk Agent Arena"
SITE_TAGLINE = (
    "Controlled, repeated-trial evaluations of LLMs operating a real "
    "structured-derivatives trading desk, with no human in the loop."
)
GITHUB_URL = "https://github.com/deiiiiii93/open-otc-trading"


def load_theme(deploy_dir: Path) -> str:
    return (deploy_dir / "theme.css").read_text()


def _head(title: str, theme: str, description: str) -> str:
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<title>{escape(title)}</title>\n'
        f'<meta name="description" content="{escape(description)}">\n'
        f'<style>{theme}</style>\n</head><body>\n'
    )


def _masthead() -> str:
    return (
        '<header class="masthead">'
        '<a class="brand" href="/"><span class="mark">A</span><span>Artena</span></a>'
        '<nav><a href="./about.html">About</a>'
        f'<a href="{GITHUB_URL}">GitHub</a></nav>'
        '</header>\n'
    )


def _site_footer() -> str:
    return (
        '<footer class="site">Published on Artena for readers who want to understand '
        'how LLMs behave in realistic financial-agent workflows.</footer>\n'
    )


def _entry(post: Post, minutes: int) -> str:
    meta = [f'<time datetime="{post.date.isoformat()}">{post.date.isoformat()}</time>']
    meta += [f'<span class="tag">{escape(t)}</span>' for t in post.tags]
    meta.append(f'<span class="read">{minutes} min</span>')

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
        '<article class="entry">'
        f'<div class="entry-meta">{"".join(meta)}</div>'
        f'<h2><a href="./{post.html_name}">{escape(post.title)}</a></h2>'
        f'<p class="blurb">{escape(post.blurb)}</p>'
        f'{chip_html}'
        '</article>\n'
    )


def _standings_card(post: Post) -> str:
    top = max(s.score for s in post.standings) or 1
    rows = []
    for s in post.standings:
        pct = s.score / top * 100
        note = f'<span class="note">{escape(s.note)}</span>' if s.note else ""
        rows.append(
            '<div class="rank-row">'
            f'<span class="rank">{s.rank}</span>'
            f'<span class="model">{escape(s.model)}</span>'
            f'<span class="score">{s.score:g}</span>'
            f'{note}'
            f'<span class="bar-track"><span class="bar-fill" style="width:{pct:.1f}%"></span></span>'
            '</div>'
        )
    label = post.run or post.title
    return (
        '<section class="rail-card"><h3>Standings</h3>'
        f'<p class="rail-sub">{escape(label)}</p>'
        f'{"".join(rows)}'
        '</section>\n'
    )


def _tag_card(posts: list[Post]) -> str:
    rows = "".join(
        f'<span>{escape(tag)}<b>{n}</b></span>' for tag, n in tag_counts(posts)
    )
    return f'<section class="rail-card"><h3>Tags</h3><div class="tag-list">{rows}</div></section>\n'


def render_index(posts: list[Post], minutes: dict[str, int], theme: str) -> str:
    """The blog index: masthead, intro, reverse-chronological feed, derived rail."""
    feed = "".join(_entry(p, minutes[p.stem]) for p in posts)

    rail = ""
    board = latest_standings(posts)
    if board is not None:
        rail += _standings_card(board)
    rail += _tag_card(posts)

    return (
        _head(SITE_TITLE, theme, SITE_TAGLINE)
        + '<div class="page">\n'
        + _masthead()
        + f'<div class="intro"><h1>{escape(SITE_TITLE)}</h1>'
        + f'<p class="lead">{escape(SITE_TAGLINE)}</p></div>\n'
        + '<div class="layout">\n'
        + f'<main class="feed">\n{feed}</main>\n'
        + f'<aside class="rail">\n{rail}</aside>\n'
        + '</div>\n'
        + _site_footer()
        + '</div>\n</body></html>\n'
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_site.py -v`
Expected: PASS — 10 passed.

- [ ] **Step 6: Commit**

```bash
git add docs/arena/deploy/theme.css docs/arena/deploy/site_builder.py tests/test_arena_deploy_site.py
git commit -m "feat(arena-deploy): blog theme and generated index with standings rail"
```

---

### Task 4: Post pages with blog chrome, about page, card contact sheets

**Files:**
- Modify: `docs/arena/deploy/site_builder.py` (append three render functions)
- Test: `tests/test_arena_deploy_site.py` (append)

**Interfaces:**
- Consumes: everything from Task 3, plus `render_report.render_markdown` at
  build time (the caller passes `body_html` in, so `site_builder` stays IO-free).
- Produces:
  - `render_post_page(post: Post, body_html: str, minutes: int, theme: str, newer: Post | None, older: Post | None) -> str`
  - `render_about(posts: list[Post], theme: str) -> str`
  - `render_contact_sheet(title: str, image_names: list[str], theme: str) -> str`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_arena_deploy_site.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_site.py -v`
Expected: FAIL — `AttributeError: module 'site_builder' has no attribute 'render_post_page'`.

- [ ] **Step 3: Append to `docs/arena/deploy/site_builder.py`**

```python
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
) -> str:
    """Wrap a rendered report body in blog chrome.

    The body arrives verbatim from render_report.render_markdown, and the PDF is
    rendered from the un-chromed document, so nothing here can affect print.
    """
    crumb_tail = escape(post.run or post.title)
    meta = [f'<time datetime="{post.date.isoformat()}">{post.date.isoformat()}</time>']
    meta += [f'<span class="tag">{escape(t)}</span>' for t in post.tags]
    meta.append(f'<span class="read">{minutes} min read</span>')

    nav = []
    if newer is not None:
        nav.append(f'<a href="./{newer.html_name}">← {escape(newer.title)}</a>')
    else:
        nav.append("<span></span>")
    nav.append(
        '<span class="downloads">'
        f'<a href="./{post.file}">Markdown</a> · '
        f'<a href="./{post.pdf_name}">PDF</a>'
        "</span>"
    )
    if older is not None:
        nav.append(f'<a href="./{older.html_name}">{escape(older.title)} →</a>')
    else:
        nav.append("<span></span>")

    return (
        _head(f"{post.title} — {SITE_TITLE}", theme, post.blurb)
        + '<div class="post-shell">\n'
        + _masthead()
        + f'<p class="crumb"><a href="./index.html">Arena</a> / {crumb_tail}</p>\n'
        + f'<div class="byline">{"".join(meta)}</div>\n'
        + f'<div class="post-body">\n{body_html}\n</div>\n'
        + f'<div class="post-nav">{"".join(nav)}</div>\n'
        + _site_footer()
        + '</div>\n</body></html>\n'
    )


def render_about(posts: list[Post], theme: str) -> str:
    points = "".join(f"<li>{escape(p)}</li>" for p in ABOUT_POINTS)
    return (
        _head(f"About — {SITE_TITLE}", theme, SITE_TAGLINE)
        + '<div class="post-shell">\n'
        + _masthead()
        + '<div class="intro"><h1>What the Arena measures</h1>'
        + '<p class="lead">The Arena is built for financial-agent evaluation, not '
        + 'generic prompt scoring. Each trial drives live desk workflows and '
        + 'reconstructs the transcript from the system\'s own trace log.</p></div>\n'
        + f'<div class="post-body"><ul>{points}</ul>'
        + f'<p>{len(posts)} reports published. '
        + f'Contact <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a> or read the '
        + f'source at <a href="{GITHUB_URL}">{escape(GITHUB_URL)}</a>.</p></div>\n'
        + _site_footer()
        + '</div>\n</body></html>\n'
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
        + f'<h1>{escape(title)}</h1>\n'
        + f'<div class="sheet">{figures}</div>\n'
        + _site_footer()
        + '</div>\n</body></html>\n'
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_site.py -v`
Expected: PASS — 17 passed.

- [ ] **Step 5: Commit**

```bash
git add docs/arena/deploy/site_builder.py tests/test_arena_deploy_site.py
git commit -m "feat(arena-deploy): post chrome, about page, card contact sheets"
```

---

### Task 5: Build orchestration and the `deploy.sh` entry point

**Files:**
- Create: `docs/arena/deploy/build.py`
- Create: `docs/arena/deploy/deploy.sh` (chmod +x)
- Modify: `.gitignore` (ignore `docs/arena/deploy/build/`)
- Test: `tests/test_arena_deploy_build.py`

**Interfaces:**
- Consumes: `manifest.load_manifest`, `manifest.reading_minutes`,
  `site_builder.load_theme`, `site_builder.render_index`,
  `site_builder.render_post_page`, `site_builder.render_about`,
  `site_builder.render_contact_sheet`, `render_report.render_markdown`,
  `render_report.document_html`, `render_report.write_pdf`.
- Produces:
  - `@dataclass class BuildResult: pages: int; assets: int; warnings: list[str]`
  - `ensure_document(md: Path, refresh_pdf: bool) -> tuple[Path, Path]`
  - `build(manifest_path: Path, arena_dir: Path, deploy_dir: Path, out_dir: Path, refresh_pdf: bool = True) -> BuildResult`
  - `missing_from_live(index_html: str, posts: list[Post]) -> list[str]`
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/test_arena_deploy_build.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_build.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'build'`.

- [ ] **Step 3: Write `docs/arena/deploy/build.py`**

```python
"""Manifest -> a complete static site under build/.

Canonical artifacts (the print-tuned .html and the .pdf) stay in docs/arena/ and
are refreshed only when stale; the site under build/ is disposable and is wiped
on every run so a deleted post cannot linger.
"""
from __future__ import annotations

import argparse
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
from manifest import Post, load_manifest, reading_minutes  # noqa: E402

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}


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


def _copy_assets(post: Post, arena_dir: Path, out_dir: Path, theme: str, result: BuildResult) -> None:
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
) -> BuildResult:
    posts = load_manifest(manifest_path, arena_dir)
    theme = sb.load_theme(deploy_dir)
    result = BuildResult()

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    minutes = {p.stem: reading_minutes((arena_dir / p.file).read_text()) for p in posts}

    for i, post in enumerate(posts):
        md = arena_dir / post.file
        html_doc, pdf = ensure_document(md, refresh_pdf)
        body, _ = render_report.render_markdown(md)

        newer = posts[i - 1] if i > 0 else None
        older = posts[i + 1] if i + 1 < len(posts) else None
        page = sb.render_post_page(post, body, minutes[post.stem], theme, newer, older)
        (out_dir / post.html_name).write_text(page)

        shutil.copy2(md, out_dir / post.file)
        if pdf.is_file():
            shutil.copy2(pdf, out_dir / post.pdf_name)
        else:
            result.warnings.append(f"no PDF for {post.file}; its download link will 404")

        _copy_assets(post, arena_dir, out_dir, theme, result)
        result.pages += 1

    (out_dir / "index.html").write_text(sb.render_index(posts, minutes, theme))
    (out_dir / "about.html").write_text(sb.render_about(posts, theme))
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_build.py -v`
Expected: PASS — 7 passed.

- [ ] **Step 5: Write `docs/arena/deploy/deploy.sh`**

```bash
#!/usr/bin/env bash
# Publish arena reports to https://www.artena.one/arena/
#
#   deploy.sh build [--no-pdf]   render the site into build/ (local only)
#   deploy.sh preview            serve build/ on http://localhost:8080
#   deploy.sh publish [--dry-run]  rsync build/ to the server, then verify
#   deploy.sh status             manifest vs live — what is not published yet
#   deploy.sh bootstrap          ONE TIME: cut /arena/ over to the static dir
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
PY="${PY:-$REPO/.venv/bin/python}"

[ -x "$PY" ] || { echo "python not found at $PY (set PY=...)" >&2; exit 1; }

usage() { sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

cmd="${1:-help}"
shift || true

case "$cmd" in
  build)     exec "$PY" "$HERE/build.py" "$@" ;;
  publish)   exec "$PY" "$HERE/publish.py" publish "$@" ;;
  status)    exec "$PY" "$HERE/publish.py" status "$@" ;;
  bootstrap) exec "$HERE/server/bootstrap.sh" "$@" ;;
  preview)
    [ -d "$HERE/build" ] || { echo "no build/ — run: deploy.sh build" >&2; exit 1; }
    echo "serving $HERE/build on http://localhost:8080/  (ctrl-c to stop)"
    exec "$PY" -m http.server 8080 --directory "$HERE/build"
    ;;
  help|-h|--help) usage ;;
  *) echo "Unknown command: $cmd" >&2; usage; exit 1 ;;
esac
```

Then `chmod +x docs/arena/deploy/deploy.sh`.

- [ ] **Step 6: Ignore the build directory**

Append to `.gitignore`, next to the existing `docs/arena/cards/` entry:

```gitignore
# generated arena blog (rebuildable from posts.yaml + the report markdown)
docs/arena/deploy/build/
```

- [ ] **Step 7: Commit**

```bash
chmod +x docs/arena/deploy/deploy.sh
git add docs/arena/deploy/build.py docs/arena/deploy/deploy.sh \
        tests/test_arena_deploy_build.py .gitignore
git commit -m "feat(arena-deploy): site build orchestration and deploy.sh entry point"
```

---

### Task 6: Transport and live verification

**Files:**
- Create: `docs/arena/deploy/publish.py`
- Test: `tests/test_arena_deploy_publish.py`

**Interfaces:**
- Consumes: `manifest.load_manifest`, `build.missing_from_live`.
- Produces:
  - `HOST`, `SSH_KEY`, `REMOTE_DIR`, `BASE_URL`, `ORPHAN_ASSETS` constants
  - `rsync_cmd(src: Path, host: str, remote: str, key: str, dry_run: bool) -> list[str]`
  - `fetch(url: str, timeout: int = 20) -> tuple[int, str, bytes]` returning
    `(status, content_type, body)`
  - `verify_live(base_url: str, posts: list[Post], assets: tuple[str, ...] = ORPHAN_ASSETS) -> list[str]`
    returning failure messages (empty list = healthy)
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/test_arena_deploy_publish.py`:

```python
"""Transport argument construction, and verification that asserts on CONTENT.

The frontend container answers `try_files $uri $uri/ /index.html`, so a 200 proves
nothing. verify_live must read bodies, and must require a 404 on an absent path.
"""
import functools
import http.server
import socketserver
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "docs" / "arena" / "deploy"
sys.path.insert(0, str(REPO / "docs" / "arena"))
sys.path.insert(0, str(DEPLOY))

import manifest as m  # noqa: E402
import publish as pub  # noqa: E402

import datetime as dt  # noqa: E402

POST = m.Post(
    file="2026-08-18-run110-luna.md",
    date=dt.date(2026, 8, 18),
    tags=("research",),
    title="Reasoning effort study",
    blurb="Effort is a step at low.",
)


@pytest.fixture
def served(tmp_path):
    """Serve tmp_path over HTTP; yields the base URL."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        yield f"http://127.0.0.1:{httpd.server_address[1]}/", tmp_path
        httpd.shutdown()


def _good_site(root: Path) -> None:
    root.joinpath("index.html").write_text(
        '<a href="./2026-08-18-run110-luna.html">Reasoning effort study</a>'
    )
    root.joinpath("2026-08-18-run110-luna.html").write_text("<h1>Reasoning effort study</h1>")
    root.joinpath("model-ability-card-bg-v1.webp").write_bytes(b"RIFF....WEBP")
    root.joinpath("model-ability-card-bg-v2.webp").write_bytes(b"RIFF....WEBP")


def test_rsync_cmd_uses_archive_delete_and_the_ssh_key():
    cmd = pub.rsync_cmd(Path("/tmp/build"), "u@h", "/opt/arena/", "/k.pem", dry_run=False)
    assert cmd[0] == "rsync"
    assert "-az" in cmd and "--delete" in cmd
    assert "-n" not in cmd
    assert any("/k.pem" in part for part in cmd)
    assert cmd[-2].endswith("/"), "source must end in / or rsync nests a directory"
    assert cmd[-1] == "u@h:/opt/arena/"


def test_rsync_cmd_dry_run_adds_n():
    cmd = pub.rsync_cmd(Path("/tmp/build"), "u@h", "/opt/arena/", "/k.pem", dry_run=True)
    assert "-n" in cmd


def test_publish_no_verify_uploads_but_does_not_verify(tmp_path, monkeypatch):
    """bootstrap needs this: at upload time the nginx alias does not exist yet."""
    build = tmp_path / "build"
    build.mkdir()
    (build / "index.html").write_text("<p>site</p>")

    calls = []

    class _Done:
        returncode = 0

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Done()

    def _must_not_run(*args, **kwargs):
        raise AssertionError("verify_live must not run under --no-verify")

    monkeypatch.setattr(pub.subprocess, "run", _fake_run)
    monkeypatch.setattr(pub, "verify_live", _must_not_run)

    assert pub.main(["publish", "--build", str(build), "--no-verify"]) == 0
    assert calls and calls[0][0] == "rsync"


def test_publish_refuses_when_there_is_no_build(tmp_path, monkeypatch):
    monkeypatch.setattr(pub, "verify_live", lambda *a, **k: [])
    assert pub.main(["publish", "--build", str(tmp_path / "absent")]) == 1


def test_verify_live_passes_on_a_healthy_site(served):
    base, root = served
    _good_site(root)
    assert pub.verify_live(base, [POST]) == []


def test_verify_live_fails_when_a_post_is_missing_from_the_index(served):
    base, root = served
    _good_site(root)
    root.joinpath("index.html").write_text("<p>nothing here</p>")
    failures = pub.verify_live(base, [POST])
    assert any("index" in f for f in failures)


def test_verify_live_fails_when_a_post_page_lacks_its_title(served):
    base, root = served
    _good_site(root)
    root.joinpath("2026-08-18-run110-luna.html").write_text("<h1>Wrong document</h1>")
    failures = pub.verify_live(base, [POST])
    assert any("2026-08-18-run110-luna.html" in f for f in failures)


def test_verify_live_fails_when_an_orphan_asset_is_gone(served):
    base, root = served
    _good_site(root)
    root.joinpath("model-ability-card-bg-v2.webp").unlink()
    failures = pub.verify_live(base, [POST])
    assert any("model-ability-card-bg-v2.webp" in f for f in failures)


def test_verify_live_requires_a_404_on_an_absent_path(served):
    """The whole point: an SPA fallback answering 200 everywhere must be caught."""
    base, root = served
    _good_site(root)
    # Simulate the catch-all by making every unknown path resolve to a real file.
    failures_ok = pub.verify_live(base, [POST])
    assert failures_ok == []

    root.joinpath(pub.ABSENT_PROBE).write_text("SPA fallback served this")
    failures = pub.verify_live(base, [POST])
    assert any("404" in f for f in failures)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_publish.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'publish'`.

- [ ] **Step 3: Write `docs/arena/deploy/publish.py`**

```python
"""Transport the built site to the server, then prove it is actually serving.

Verification asserts on BODY CONTENT, never on status alone: the open-slides-zero
frontend container answers `try_files $uri $uri/ /index.html`, so before the nginx
alias exists every path under /arena/ returns 200. ABSENT_PROBE is the control —
if a path that cannot exist returns 200, the SPA is still answering and the
deploy has not taken effect.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import urllib.error
import urllib.request
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARENA_DIR = HERE.parent
sys.path.insert(0, str(ARENA_DIR))
sys.path.insert(0, str(HERE))

from build import missing_from_live  # noqa: E402
from manifest import Post, load_manifest  # noqa: E402

HOST = "ubuntu@43.156.158.156"
SSH_KEY = "/Users/fuxinyao/ppt-pro-server/slides.pem"
REMOTE_DIR = "/opt/open-slides-zero/runtime/arena/"
BASE_URL = "https://www.artena.one/arena/"

# Uploaded by hand so the GPT-Image-2 ability-card run could fetch a background by
# public URL. Referenced from nowhere in either repo, so nothing else protects them.
ORPHAN_ASSETS = ("model-ability-card-bg-v1.webp", "model-ability-card-bg-v2.webp")

ABSENT_PROBE = "__deploy_probe_absent__.html"


def rsync_cmd(src: Path, host: str, remote: str, key: str, dry_run: bool) -> list[str]:
    cmd = ["rsync", "-az", "--delete"]
    if dry_run:
        cmd.append("-n")
    cmd += ["-e", f"ssh -i {key} -o StrictHostKeyChecking=accept-new"]
    cmd += [f"{src}/", f"{host}:{remote}"]
    return cmd


def fetch(url: str, timeout: int = 20) -> tuple[int, str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "arena-deploy/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", e.read()


def verify_live(
    base_url: str, posts: list[Post], assets: tuple[str, ...] = ORPHAN_ASSETS
) -> list[str]:
    """Return failure messages; an empty list means the site is healthy."""
    failures: list[str] = []

    status, _, body = fetch(base_url)
    index = body.decode("utf-8", "replace")
    if status != 200:
        failures.append(f"index: expected 200, got {status}")
    else:
        for name in missing_from_live(index, posts):
            failures.append(f"index: no link to {name}")
        for p in posts:
            # Titles are HTML-escaped on the page, so compare the escaped form —
            # a raw comparison would false-alarm on any title containing & or <.
            if escape(p.title) not in index:
                failures.append(f"index: title missing for {p.html_name}")

    for p in posts:
        url = f"{base_url}{p.html_name}"
        status, _, body = fetch(url)
        text = body.decode("utf-8", "replace")
        if status != 200:
            failures.append(f"{p.html_name}: expected 200, got {status}")
        elif escape(p.title) not in text:
            failures.append(f"{p.html_name}: served page does not contain its title")

    for name in assets:
        status, ctype, _ = fetch(f"{base_url}{name}")
        if status != 200:
            failures.append(f"{name}: expected 200, got {status}")
        elif "image" not in ctype:
            failures.append(f"{name}: expected an image content-type, got {ctype!r}")

    status, _, _ = fetch(f"{base_url}{ABSENT_PROBE}")
    if status != 404:
        failures.append(
            f"{ABSENT_PROBE}: expected 404, got {status} — a catch-all is still "
            "answering, so the static alias is not serving this path"
        )

    return failures


def _posts() -> list[Post]:
    return load_manifest(HERE / "posts.yaml", ARENA_DIR)


def _cmd_publish(args) -> int:
    build_dir = args.build
    if not (build_dir / "index.html").is_file():
        print(f"no built site at {build_dir} — run: deploy.sh build", file=sys.stderr)
        return 1

    cmd = rsync_cmd(build_dir, args.host, args.remote, args.key, args.dry_run)
    print(" ".join(cmd))
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        return proc.returncode
    if args.dry_run:
        print("dry run — nothing transferred, skipping verification")
        return 0
    if args.no_verify:
        print("uploaded; verification skipped (--no-verify)")
        return 0

    failures = verify_live(args.base_url, _posts())
    if failures:
        print("\nVERIFICATION FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"\nverified {args.base_url} — all pages, assets and the 404 probe are healthy")
    return 0


def _cmd_status(args) -> int:
    posts = _posts()
    status, _, body = fetch(args.base_url)
    index = body.decode("utf-8", "replace")
    unpublished = missing_from_live(index, posts)
    print(f"{args.base_url} -> HTTP {status}, {len(posts)} posts in the manifest")
    if unpublished:
        print("NOT on the live index:")
        for name in unpublished:
            print(f"  - {name}")
        return 1
    print("every manifest post is linked from the live index")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Publish the arena blog.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("publish", "status"):
        p = sub.add_parser(name)
        p.add_argument("--base-url", default=BASE_URL)
        if name == "publish":
            p.add_argument("--build", type=Path, default=HERE / "build")
            p.add_argument("--host", default=HOST)
            p.add_argument("--remote", default=REMOTE_DIR)
            p.add_argument("--key", default=SSH_KEY)
            p.add_argument("--dry-run", action="store_true")
            p.add_argument(
                "--no-verify",
                action="store_true",
                help="upload only; used by bootstrap, where the alias does not exist yet",
            )

    args = ap.parse_args(argv)
    return _cmd_publish(args) if args.cmd == "publish" else _cmd_status(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_publish.py -v`
Expected: PASS — 9 passed.

- [ ] **Step 5: Commit**

```bash
git add docs/arena/deploy/publish.py tests/test_arena_deploy_publish.py
git commit -m "feat(arena-deploy): rsync transport with content-based live verification"
```

---

### Task 7: Preserve the orphan assets and patch open-slides-zero

**Files:**
- Create: `docs/arena/deploy/static/model-ability-card-bg-v1.webp` (189,210 bytes)
- Create: `docs/arena/deploy/static/model-ability-card-bg-v2.webp` (166,040 bytes)
- Create: `docs/arena/deploy/server/arena-location.conf`
- Create: `docs/arena/deploy/server/patch_osz.py`
- Modify: `docs/arena/deploy/build.py` (copy `static/` into the build root)
- Test: `tests/test_arena_deploy_server_patch.py`
- Test: `tests/test_arena_deploy_build.py` (append one case)

**Why this task exists.** `publish` uses `rsync -az --delete`, and the two
`.webp` files live **only on the server** — nothing in either repo references or
contains them. Publishing without this task would delete two live, working URLs.
Tracking them in git turns a hidden server-state dependency into a reviewable one.

**Interfaces:**
- Consumes: `build.build` (Task 5).
- Produces:
  - `patch_osz.ARENA_LOCATION: str`
  - `patch_osz.patch_nginx(conf_text: str, block: str) -> str` — idempotent
  - `patch_osz.patch_compose(compose_text: str) -> str` — idempotent
  - `patch_osz.main(argv) -> int` writing both files in place

- [ ] **Step 1: Capture the orphan assets into the repo**

```bash
mkdir -p docs/arena/deploy/static
for v in 1 2; do
  curl -fsS "https://www.artena.one/arena/model-ability-card-bg-v$v.webp" \
    -o "docs/arena/deploy/static/model-ability-card-bg-v$v.webp"
done
ls -l docs/arena/deploy/static/
```
Expected: `model-ability-card-bg-v1.webp` 189210 bytes, `-v2.webp` 166040 bytes.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_arena_deploy_server_patch.py`:

```python
"""The one-time open-slides-zero patch must be idempotent and surgical."""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "docs" / "arena" / "deploy" / "server"
sys.path.insert(0, str(SERVER))

import patch_osz as po  # noqa: E402

OSZ = Path("/Users/fuxinyao/open-slides-zero")
NGINX = OSZ / "deploy" / "nginx" / "default.conf"
COMPOSE = OSZ / "compose.prod.yml"

pytestmark = pytest.mark.skipif(
    not NGINX.is_file(), reason="open-slides-zero checkout not present"
)


def test_patch_nginx_inserts_the_arena_location_before_the_proxy_root():
    out = po.patch_nginx(NGINX.read_text(), po.ARENA_LOCATION)
    assert "location /arena/" in out
    assert out.index("location /arena/") < out.index("proxy_pass http://osz_frontend")


def test_patch_nginx_leaves_the_port_80_redirect_alone():
    original = NGINX.read_text()
    out = po.patch_nginx(original, po.ARENA_LOCATION)
    assert out.count("return 301 https://$host$request_uri;") == 1
    assert out.count("location /arena/") == 1


def test_patch_nginx_is_idempotent():
    once = po.patch_nginx(NGINX.read_text(), po.ARENA_LOCATION)
    assert po.patch_nginx(once, po.ARENA_LOCATION) == once


def test_patch_nginx_refuses_an_unrecognised_config():
    with pytest.raises(po.PatchError):
        po.patch_nginx("server {\n  listen 80;\n}\n", po.ARENA_LOCATION)


def test_patch_compose_adds_the_arena_mount_to_nginx_only():
    out = po.patch_compose(COMPOSE.read_text())
    assert "./runtime/arena:/var/www/arena:ro" in out
    assert out.count("./runtime/arena:/var/www/arena:ro") == 1
    # the mount must land inside the nginx service, after its existing mounts
    assert out.index("./runtime/arena") > out.index("deploy/nginx/default.conf")


def test_patch_compose_is_idempotent():
    once = po.patch_compose(COMPOSE.read_text())
    assert po.patch_compose(once) == once


def test_patch_compose_refuses_when_the_anchor_mount_is_gone():
    with pytest.raises(po.PatchError):
        po.patch_compose("services:\n  nginx:\n    image: nginx\n")
```

Append to `tests/test_arena_deploy_build.py`:

```python
def test_build_copies_tracked_static_assets_into_the_site_root(project):
    """The orphan card backgrounds must survive rsync --delete."""
    mf, arena, out = project
    b.build(mf, arena, DEPLOY, out, refresh_pdf=False)
    for name in ("model-ability-card-bg-v1.webp", "model-ability-card-bg-v2.webp"):
        assert (out / name).is_file(), f"{name} missing — rsync --delete would drop it"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_server_patch.py tests/test_arena_deploy_build.py -v`
Expected: FAIL — `No module named 'patch_osz'`, and the static-asset case fails
because `build()` does not copy `static/` yet.

- [ ] **Step 4: Write `docs/arena/deploy/server/arena-location.conf`**

```nginx
# Serve the generated arena blog as static files.
#
# nginx picks the LONGEST matching prefix, so this wins over `location /`
# (which proxies to the open-slides-zero SPA) regardless of ordering. The
# `=404` is load-bearing: without it a missing file would fall through and the
# SPA would answer 200 for everything, which is exactly the failure the deploy
# verifier probes for.
location /arena/ {
    alias /var/www/arena/;
    index index.html;
    try_files $uri $uri/ =404;
    add_header Cache-Control "public, max-age=300";
}
```

- [ ] **Step 5: Write `docs/arena/deploy/server/patch_osz.py`**

```python
"""Idempotently add the /arena/ static mount + location to open-slides-zero.

This is the ONE cross-repo change the design needs. It is applied to the local
open-slides-zero checkout, then shipped by that repo's own
scripts/deploy_incremental.sh --service nginx, so local and server stay in sync.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_OSZ = Path("/Users/fuxinyao/open-slides-zero")

ARENA_LOCATION = (HERE / "arena-location.conf").read_text()

ARENA_MOUNT = "      - ./runtime/arena:/var/www/arena:ro\n"
COMPOSE_ANCHOR = "      - ./deploy/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro\n"
NGINX_ANCHOR = "    location / {\n        proxy_pass http://osz_frontend;\n"


class PatchError(RuntimeError):
    """The target file does not look the way this patch expects."""


def patch_nginx(conf_text: str, block: str) -> str:
    if "location /arena/" in conf_text:
        return conf_text
    if NGINX_ANCHOR not in conf_text:
        raise PatchError(
            "nginx config does not contain the expected `location / { proxy_pass "
            "http://osz_frontend;` anchor — refusing to guess where /arena/ goes"
        )
    indented = "".join(
        f"    {line}\n" if line.strip() else "\n" for line in block.rstrip().splitlines()
    )
    return conf_text.replace(NGINX_ANCHOR, f"{indented}\n{NGINX_ANCHOR}", 1)


def patch_compose(compose_text: str) -> str:
    if ARENA_MOUNT.strip() in compose_text:
        return compose_text
    if COMPOSE_ANCHOR not in compose_text:
        raise PatchError(
            "compose.prod.yml does not mount deploy/nginx/default.conf — refusing "
            "to guess which service should receive the arena mount"
        )
    return compose_text.replace(COMPOSE_ANCHOR, COMPOSE_ANCHOR + ARENA_MOUNT, 1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Patch open-slides-zero for /arena/.")
    ap.add_argument("--osz", type=Path, default=DEFAULT_OSZ)
    ap.add_argument("--check", action="store_true", help="report without writing")
    args = ap.parse_args(argv)

    nginx = args.osz / "deploy" / "nginx" / "default.conf"
    compose = args.osz / "compose.prod.yml"
    for p in (nginx, compose):
        if not p.is_file():
            print(f"not found: {p}", file=sys.stderr)
            return 1

    changed = []
    for path, patch in ((nginx, lambda t: patch_nginx(t, ARENA_LOCATION)), (compose, patch_compose)):
        before = path.read_text()
        after = patch(before)
        if before == after:
            print(f"already patched: {path}")
            continue
        changed.append(path)
        if not args.check:
            path.write_text(after)
            print(f"patched: {path}")
        else:
            print(f"would patch: {path}")

    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Teach `build.py` to copy `static/`**

In `build.py`, add after the `IMAGE_SUFFIXES` constant:

```python
STATIC_DIR_NAME = "static"
```

and insert this immediately before the `index.html` write at the end of `build()`:

```python
    # Tracked passthrough assets (e.g. the ability-card backgrounds that were
    # once server-only). They must be IN the build, because publish uses
    # `rsync --delete` and would otherwise remove them from the live site.
    static_dir = deploy_dir / STATIC_DIR_NAME
    if static_dir.is_dir():
        for f in sorted(static_dir.iterdir()):
            if f.is_file():
                shutil.copy2(f, out_dir / f.name)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_server_patch.py tests/test_arena_deploy_build.py -v`
Expected: PASS — 7 + 8 passed.

- [ ] **Step 8: Commit**

```bash
git add docs/arena/deploy/static docs/arena/deploy/server docs/arena/deploy/build.py \
        tests/test_arena_deploy_server_patch.py tests/test_arena_deploy_build.py
git commit -m "feat(arena-deploy): track orphan card assets, add idempotent nginx/compose patch

The two model-ability-card backgrounds existed only on the server and are
referenced from nowhere in either repo; rsync --delete would have removed two
live URLs. They are now tracked and shipped in every build."
```

---

### Task 8: One-time cutover script

**Files:**
- Create: `docs/arena/deploy/server/bootstrap.sh` (chmod +x)
- Test: manual, against production (documented below)

**Interfaces:**
- Consumes: `patch_osz.main` (Task 7), `publish.py` (Task 6),
  `open-slides-zero/scripts/deploy_incremental.sh`.
- Produces: nothing importable — an operator script.

- [ ] **Step 1: Write `docs/arena/deploy/server/bootstrap.sh`**

```bash
#!/usr/bin/env bash
# ONE TIME: cut https://www.artena.one/arena/ over from the SPA-served copy to a
# static directory behind an nginx alias.
#
# Safety properties:
#  - refuses if rsync --delete would remove anything unexpected from the server
#  - backs up the nginx config on the server before recreating the container
#  - restores the backup and redeploys if the site does not come back
#  - leaves open-slides-zero/frontend/public/arena/ IN PLACE, so reverting the
#    location block alone restores the previous site with no data restore
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY="$(cd "$HERE/.." && pwd)"
REPO="$(cd "$DEPLOY/../../.." && pwd)"
PY="${PY:-$REPO/.venv/bin/python}"

OSZ="${OSZ:-/Users/fuxinyao/open-slides-zero}"
SSH_KEY="${SSH_KEY:-/Users/fuxinyao/ppt-pro-server/slides.pem}"
SSH_TARGET="${SSH_TARGET:-ubuntu@43.156.158.156}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/open-slides-zero}"
REMOTE_ARENA="$DEPLOY_DIR/runtime/arena"
SITE_URL="${SITE_URL:-https://www.artena.one/}"
SSH=(/usr/bin/ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "$SSH_TARGET")

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

step() { printf '\n=== %s ===\n' "$1"; }

step "1/6 checking the local build"
[ -f "$DEPLOY/build/index.html" ] || { echo "no build — run: deploy.sh build" >&2; exit 1; }

step "2/6 creating the remote directory"
"${SSH[@]}" "mkdir -p '$REMOTE_ARENA'"

step "3/6 dry run — what would rsync --delete remove?"
DELETES="$("$PY" "$DEPLOY/publish.py" publish --dry-run 2>/dev/null | grep -E '^deleting ' || true)"
if [ -n "$DELETES" ] && [ "$FORCE" -ne 1 ]; then
  echo "$DELETES"
  echo
  echo "Refusing: the server holds files this build does not produce." >&2
  echo "Track them under docs/arena/deploy/static/ first, or re-run with --force." >&2
  exit 1
fi

step "4/6 uploading the site"
# --no-verify: the nginx alias does not exist yet, so /arena/ is still answered by
# the SPA fallback and verification could not possibly pass at this point.
"$PY" "$DEPLOY/publish.py" publish --no-verify
echo "uploaded to $REMOTE_ARENA"

step "5/6 patching open-slides-zero and backing up the live nginx config"
"$PY" "$HERE/patch_osz.py" --osz "$OSZ"
"${SSH[@]}" "cp -a '$DEPLOY_DIR/deploy/nginx/default.conf' '$DEPLOY_DIR/deploy/nginx/default.conf.pre-arena'"
( cd "$OSZ" && ./scripts/deploy_incremental.sh --service nginx )

step "6/6 verifying"
ok=0
for _ in $(seq 1 15); do
  if curl -fsS -o /dev/null --max-time 10 "$SITE_URL"; then ok=1; break; fi
  sleep 2
done

if [ "$ok" -ne 1 ]; then
  echo "site did not come back — restoring the previous nginx config" >&2
  "${SSH[@]}" "cp -a '$DEPLOY_DIR/deploy/nginx/default.conf.pre-arena' '$DEPLOY_DIR/deploy/nginx/default.conf' && cd '$DEPLOY_DIR' && sudo docker compose --env-file .env.production -f compose.prod.yml up -d nginx"
  echo "restored. The /arena/ location was NOT applied." >&2
  exit 1
fi

"$PY" "$DEPLOY/publish.py" publish
echo
echo "Cutover complete. /arena/ is now served from $REMOTE_ARENA."
echo "Rollback: remove the 'location /arena/' block from"
echo "  $OSZ/deploy/nginx/default.conf"
echo "and re-run: (cd $OSZ && ./scripts/deploy_incremental.sh --service nginx)"
```

Then `chmod +x docs/arena/deploy/server/bootstrap.sh`.

- [ ] **Step 2: Rehearse without touching production**

```bash
.venv/bin/python docs/arena/deploy/server/patch_osz.py --check
```
Expected: prints `would patch:` for both files and exits 1. Confirm
`git -C /Users/fuxinyao/open-slides-zero status --short` reports **no changes**.

- [ ] **Step 3: Dry-run the transport**

```bash
docs/arena/deploy/deploy.sh build
docs/arena/deploy/deploy.sh publish --dry-run
```
Expected: rsync lists the files it would send and prints
`dry run — nothing transferred`. Confirm no `deleting ` lines appear; if any do,
STOP and track those files under `docs/arena/deploy/static/` first.

- [ ] **Step 4: Run the cutover**

```bash
docs/arena/deploy/deploy.sh bootstrap
```
Expected: six steps, ending with `verified https://www.artena.one/arena/ — all
pages, assets and the 404 probe are healthy`.

- [ ] **Step 5: Confirm the alias is really serving**

```bash
curl -s -o /dev/null -w "absent=%{http_code}\n" https://www.artena.one/arena/__nope__.html
curl -s -o /dev/null -w "webp=%{http_code}\n" https://www.artena.one/arena/model-ability-card-bg-v1.webp
curl -s https://www.artena.one/arena/ | grep -c "run110"
```
Expected: `absent=404` (proves the SPA fallback is no longer answering),
`webp=200`, and a non-zero grep count.

- [ ] **Step 6: Commit**

```bash
chmod +x docs/arena/deploy/server/bootstrap.sh
git add docs/arena/deploy/server/bootstrap.sh
git commit -m "feat(arena-deploy): one-time cutover script with backup and auto-rollback"
```

Also commit the open-slides-zero change **in that repo**:

```bash
cd /Users/fuxinyao/open-slides-zero
git add compose.prod.yml deploy/nginx/default.conf
git commit -m "feat(nginx): serve /arena/ from runtime/arena as static files

The arena blog is generated and rsynced by open-otc-trading's
docs/arena/deploy pipeline. frontend/public/arena/ is retained deliberately:
removing this location block reverts to it with no data restore."
```

---

### Task 9: Seed the manifest, wire the docs, prove it end to end

**Files:**
- Create: `docs/arena/deploy/posts.yaml` (nine entries)
- Create: `docs/arena/deploy/README.md`
- Modify: `docs/arena/README.md` (point the HTML links at artena.one)
- Modify: `CHANGELOG.md` (under `[Unreleased]`)
- Modify: `CLAUDE.md` (new subsystem section)
- Test: `tests/test_arena_deploy_integration.py`

**Interfaces:**
- Consumes: everything above.
- Produces: no new code interfaces.

- [ ] **Step 1: Write the failing integration test**

Create `tests/test_arena_deploy_integration.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_integration.py -v`
Expected: FAIL — `ManifestError: manifest not found at .../posts.yaml`.

- [ ] **Step 3: Write `docs/arena/deploy/posts.yaml`**

Headlines and blurbs are lifted from each report's own summary in
`docs/arena/README.md`; standings come from the run-104 board table.

```yaml
# The arena blog feed. Authoritative for publication: a report absent from this
# file is not published, whatever exists in docs/arena/.
#
# Required: file, date, tags, title, blurb.  Optional: run, chips, standings,
# assets, publish.  A missing blurb FAILS THE BUILD by design — never let a
# generated first paragraph ship as editorial copy.

- file: 2026-08-18-run110-luna-reasoning-effort.md
  date: 2026-08-18
  tags: [research]
  title: "Does reasoning_effort improve agent performance?"
  blurb: >
    Effort is a step at `low`, not a dial. Zero to some reasoning buys ~5 points
    and costs less; above `low`, quality is flat while wall-clock triples.
  chips: ["1 model", "4 efforts", "40 trials"]
  publish: true

- file: 2026-08-17-trap-step-absent-referent.md
  date: 2026-08-17
  tags: [research]
  title: "Do models fail the trap step? Yes, but not the way we thought"
  blurb: >
    Prohibitions hold at 94-100% when nothing pushes against them, and collapse
    to 5% when the requested act IS the banned one. Trap failure is completion
    pressure, not weak instruction-following.
  chips: ["runs #10-#109", "99 trials"]
  publish: true

- file: 2026-08-17-luna-reasoning-effort-plan.md
  date: 2026-08-17
  tags: [method]
  title: "Pre-registration: the reasoning-effort ladder study"
  blurb: >
    The design, arms, and validity rules for Run #110, published as they were
    predeclared before launch.
  publish: true

- file: 2026-08-13-run104-otc-desk-agent-arena.md
  date: 2026-08-13
  tags: [board]
  run: "Run #104"
  chips: ["2 models", "4 workflows", "2 trials"]
  title: "Grok 4.6 edges DeepSeek V4 Pro, for opposite reasons"
  blurb: >
    A one-point tie: Grok leads the objective axis 96.3 to 90.9 and wins three of
    four workflows, but posts EFF 12 at 243 tool calls against par 24. The
    inversion is not fixed, it is deeper.
  standings:
    - {rank: 1, model: "Grok 4.6", score: 80, note: "objective 96.3, EFF 12"}
    - {rank: 2, model: "DeepSeek V4 Pro", score: 79, note: "100.0 objective in 22 calls"}
  assets: [cards/run104]
  publish: true

- file: 2026-07-29-run94-otc-desk-agent-arena.md
  date: 2026-07-29
  tags: [board]
  run: "Run #94"
  chips: ["18 models"]
  title: "Board governance review: the ranking signal migrates"
  blurb: >
    Gemini 3.6 Flash posts the Arena's first perfect card while the governance
    workflow moves the deciding signal from efficiency to synthesis.
  publish: true

- file: 2026-07-20-run33-otc-desk-agent-arena.md
  date: 2026-07-20
  tags: [board]
  run: "Run #33"
  chips: ["17 models", "2 trials"]
  title: "Trader RFQ booking day: operator rankings replicate"
  blurb: >
    GPT-5.6 Terra defends its title on a second workflow. Objective capability
    saturates again, so efficiency and consistency decide the operator ranking.
  publish: true

- file: 2026-07-13-run20-otc-desk-agent-arena.md
  date: 2026-07-13
  tags: [board]
  run: "Run #20"
  chips: ["16 models", "2 trials"]
  title: "Model Ability Cards: measuring the long-run operator"
  blurb: >
    A nine-step, 39-point task separates raw capability from the efficiency and
    consistency that determine whether an agent can be left to run unattended.
  publish: true

- file: 2026-06-28-run9-otc-desk-agent-arena.md
  date: 2026-06-28
  tags: [board]
  run: "Run #9"
  chips: ["9 flash models", "5 trials"]
  title: "The flash tier: latency is not a price claim"
  blurb: >
    Gemini 3.5 Flash wins the placed board at $14.28 a match, dearer than the
    frontier models. Step 3.7 Flash lands 0.1 behind and costs 14x less.
  publish: true

- file: 2026-06-27-run8-otc-desk-agent-arena.md
  date: 2026-06-27
  tags: [board]
  run: "Run #8"
  chips: ["10 models", "5 trials"]
  title: "Risk manager control day: the first frontier board"
  blurb: >
    Claude Opus 4.8 and GPT-5.5 finish in a statistical tie at the top, and the
    cost-efficiency ranking inverts the quality ranking outright.
  publish: true
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_arena_deploy_integration.py -v`
Expected: PASS — 3 passed.

NOTE — this test writes to the repo, intentionally. `build()` calls
`ensure_document()`, which creates `docs/arena/<stem>.html` when it is missing or
older than its markdown. The three newly published pieces have no `.html` yet, so
running this creates them; that is the desired artifact, and they are committed in
Step 10. The six existing reports have `.html` newer than their `.md`, so they are
skipped and must NOT appear as modified. Step 5 checks exactly that.

- [ ] **Step 5: Run the whole new suite plus a regression sweep**

```bash
.venv/bin/python -m pytest tests/test_arena_render_report.py \
  tests/test_arena_deploy_manifest.py tests/test_arena_deploy_site.py \
  tests/test_arena_deploy_build.py tests/test_arena_deploy_publish.py \
  tests/test_arena_deploy_server_patch.py tests/test_arena_deploy_integration.py -v
```
Expected: PASS, ~53 tests.

Then confirm nothing else moved:
```bash
git status --short docs/arena/
```
Expected: the two new reports gain `.html`/`.pdf` (they had none); no existing
`.html` shows as modified.

- [ ] **Step 6: Write `docs/arena/deploy/README.md`**

```markdown
# Publishing arena reports

The public site is <https://www.artena.one/arena/>. It is generated from
`posts.yaml` — nothing on it is hand-maintained, so it cannot go stale.

## Publish a report

1. Author `docs/arena/<date>-<slug>.md` (optionally a `.charts.json` sidecar).
2. Add an entry to `posts.yaml`. `title` and `blurb` are editorial and required —
   a missing `blurb` fails the build rather than shipping generated filler.
3. `./deploy.sh build && ./deploy.sh preview` — read it at <http://localhost:8080>.
4. `./deploy.sh publish` — rsync, then verify the live site.

`./deploy.sh status` answers "what have I written but not published?".

## How it works

`render_report.py` owns markdown → HTML. The PDF renders from the un-chromed
document, so print output cannot drift when the site design changes; the web
page wraps the same body in blog chrome.

`/arena/` is a plain directory on the server (`/opt/open-slides-zero/runtime/arena/`)
behind an nginx `alias`. Publishing is an rsync — it does not rebuild the
open-slides-zero SPA.

## Gotchas

- **`rsync --delete` is live.** Anything on the server that no build produces is
  removed. Passthrough files belong in `static/` so they ship every time — that
  is why the two `model-ability-card-bg-*.webp` files are tracked here.
- **A 200 proves nothing.** Before the alias existed, the SPA answered 200 for
  every path under `/arena/`. Verification asserts on body content and requires a
  404 on a path that cannot exist.
- **`docs/arena/cards/` is gitignored,** so a clean checkout publishes no card
  images. The build warns and continues; that is expected, not a failure.
- **Rollback** is removing the `location /arena/` block in open-slides-zero and
  redeploying nginx. `frontend/public/arena/` is deliberately still there.
```

- [ ] **Step 7: Update `docs/arena/README.md` links**

Replace every `https://htmlpreview.github.io/?https://github.com/...<name>.html`
link with `https://www.artena.one/arena/<name>.html`, and add a row for each of
the three newly published pieces. Verify none remain:

```bash
grep -c "htmlpreview.github.io" docs/arena/README.md
```
Expected: `0`.

- [ ] **Step 8: Update `CHANGELOG.md` under `[Unreleased]`**

```markdown
### Added
- **Arena report publishing pipeline** (`docs/arena/deploy/`). `posts.yaml` is the
  single source of truth for <https://www.artena.one/arena/>, which is now a
  generated blog: reverse-chronological tagged feed, a standings rail derived
  from the newest board, and post pages wrapped in site chrome. `deploy.sh
  build | preview | publish | status | bootstrap`. Publishing is an rsync to a
  static directory behind an nginx alias — it no longer rebuilds the
  open-slides-zero SPA. Publishes Run #104 (rendered 2026-08-17, never shipped)
  plus the reasoning-effort study, its pre-registration, and the trap-step memo.

### Changed
- `docs/arena/render_report.py` split into importable functions with a `main()`
  guard. Output is byte-identical — gated by a sha256 test against all six
  committed report HTML files.
- `docs/arena/README.md` links now point at artena.one instead of
  htmlpreview.github.io.

### Fixed
- `markdown` was imported by `render_report.py` but never declared in
  `pyproject.toml`.
```

- [ ] **Step 9: Add a `CLAUDE.md` section**

Insert a new top-level section documenting the subsystem. It must state: the
manifest is authoritative; a 200 proves nothing under the SPA catch-all so
verification reads bodies and requires a 404 probe; `rsync --delete` means
passthrough assets live in `static/`; the PDF renders from the un-chromed
document so print cannot drift; and rollback is removing the nginx location
block because `frontend/public/arena/` is retained.

- [ ] **Step 10: Commit**

```bash
git add docs/arena/deploy/posts.yaml docs/arena/deploy/README.md \
        docs/arena/README.md CHANGELOG.md CLAUDE.md \
        tests/test_arena_deploy_integration.py \
        docs/arena/2026-08-17-*.html docs/arena/2026-08-18-*.html \
        docs/arena/2026-08-17-*.pdf docs/arena/2026-08-18-*.pdf
git commit -m "feat(arena-deploy): seed the blog manifest with nine posts, wire docs

Closes the Run #104 publishing gap and adds the reasoning-effort study, its
pre-registration, and the trap-step memo."
```

"""The confirmation corpus is TRACKED and must be reproducible from its generator.

These documents grade the `confirmation-desk-day` arena board. If a regenerated
document differs from the committed one, a rendering dependency moved -- and every
graded constant harvested from these documents has to be re-verified before any
board built on them is trusted. Same discipline as the exact `quantark==0.3.0`
pin: the engine that produces benchmark numbers is part of the evidence.

WHY CONTENT AND NOT BYTES (measured 2026-08-28)
-----------------------------------------------
Raw-byte equality is unachievable and asserting it would leave a permanently red
test, which trains people to ignore the suite. PIL's PDF writer stamps
`/CreationDate (D:...Z)` and python-docx writes zip mtimes, so the same content
rendered one second apart differs by construction.

What matters for a VISION benchmark is what the model actually sees, so these
tests compare page text plus rendered page pixels -- via the repo's own
`extract_document`, i.e. the exact path the extractor feeds the model.

WHY A SUBPROCESS
----------------
Calling build_all() twice in one interpreter yields different scan pixels even
with `random` re-seeded: the rendering stack consumes the RNG lazily on first
use, so the second pass reaches the scan builder at a different stream position.
Regeneration really happens as `python make_confirmations.py` -- a fresh process,
which IS deterministic -- so that is what these tests exercise.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

DOCS = Path("backend/app/golden_workflows/documents")
GENERATOR = DOCS / "make_confirmations.py"

# The scan-bearing documents; the rest are text/DOCX and are covered by the same
# content signature.
_ALL = sorted(p.name for p in DOCS.glob("conf-*"))


def _content_signature(path: Path) -> list[tuple]:
    """Page text + rendered pixels, through the extractor's own reader."""
    from app.services.confirmations.extract import extract_document

    content = extract_document(path)
    return [
        (
            page.index,
            hashlib.sha256((page.text or "").encode()).hexdigest(),
            hashlib.sha256(page.image_png).hexdigest() if page.image_png else None,
        )
        for page in content.pages
    ]


def _regenerate(out_dir: Path) -> None:
    """Run the generator in a FRESH interpreter, writing into ``out_dir``."""
    repo_root = Path(__file__).resolve().parents[1]
    script = (
        "import sys; sys.path.insert(0, 'backend');"
        "from pathlib import Path;"
        "from app.golden_workflows.documents.make_confirmations import build_all;"
        f"build_all(Path({str(out_dir)!r}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(repo_root), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_the_generator_reproduces_the_committed_corpus(tmp_path):
    _regenerate(tmp_path)

    drifted = []
    for name in _ALL:
        produced = tmp_path / name
        assert produced.exists(), f"generator did not emit {name}"
        if _content_signature(produced) != _content_signature(DOCS / name):
            drifted.append(name)

    assert not drifted, (
        f"{drifted} regenerated with different CONTENT -- a rendering dependency "
        "moved. Re-verify every graded constant before trusting a board built on "
        "these documents."
    )


def test_the_generator_emits_every_committed_document(tmp_path):
    """A committed document the generator no longer produces is unreproducible:
    nobody could regenerate it, and its provenance is gone."""
    _regenerate(tmp_path)
    emitted = {p.name for p in tmp_path.glob("conf-*")}
    assert set(_ALL) <= emitted, f"not regenerated: {sorted(set(_ALL) - emitted)}"


def test_the_corpus_and_generator_are_tracked_by_git():
    """A graded benchmark cannot rest on per-environment files -- the trap
    CLAUDE.md records for config/agent_channels.yaml."""
    out = subprocess.run(
        ["git", "ls-files", str(DOCS)], capture_output=True, text=True, check=True
    ).stdout
    tracked = {Path(line).name for line in out.splitlines() if line}
    assert "make_confirmations.py" in tracked
    for name in _ALL:
        assert name in tracked, f"{name} is not tracked"


def test_the_isda_reference_stays_untracked():
    """The third-party template is what the ignore rule is FOR; narrowing the
    rule must not sweep it into the repo."""
    result = subprocess.run(
        ["git", "check-ignore", "docs/confirmations/equity-share-option.pdf"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, "the ISDA reference template is no longer ignored"


def test_the_scanned_document_really_is_image_only():
    """conf-04 is the vision FLOOR (probe 2026-08-28: all three contestants read
    it perfectly). If it ever gained a text layer, every model would pass it by
    reading text and the floor would stop measuring vision at all."""
    from app.services.confirmations.extract import extract_document

    content = extract_document(DOCS / "conf-04-scanned-call-googl.pdf")
    assert content.extract_mode == "vision"
    assert all(page.image_png is not None for page in content.pages)


def test_the_mixed_document_keeps_one_text_page_and_one_scan():
    """conf-08 grades stage-1 PAGE SELECTION: the priced terms live only on the
    scanned page, so a model that drops it returns empty terms."""
    from app.services.confirmations.extract import extract_document

    content = extract_document(DOCS / "conf-08-mixed-text-and-scan-amd.pdf")
    assert content.extract_mode == "mixed"
    assert any(page.image_png is None for page in content.pages)
    assert any(page.image_png is not None for page in content.pages)

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
    """Import must be side-effect free: the old module rendered at import time.

    Snapshot CONTENT, not just filenames. The old module overwrote
    2026-06-27-run8-*.html/.pdf in place, so a name-set comparison passes while
    two tracked files are silently rewritten (observed, not theorised).
    """
    import subprocess

    probe = tmp_path / "probe.py"
    probe.write_text(
        "import hashlib, pathlib, sys\n"
        f"arena = pathlib.Path({str(ARENA)!r})\n"
        "sys.path.insert(0, str(arena))\n"
        "def snap():\n"
        "    return {\n"
        "        f.name: hashlib.sha256(f.read_bytes()).hexdigest()\n"
        "        for f in arena.iterdir() if f.is_file()\n"
        "    }\n"
        "before = snap()\n"
        "import render_report\n"
        "after = snap()\n"
        "added = sorted(set(after) - set(before))\n"
        "changed = sorted(k for k in before if k in after and before[k] != after[k])\n"
        "assert not added and not changed, f'added={added} changed={changed}'\n"
        "print('clean')\n"
    )
    out = subprocess.run(
        [sys.executable, str(probe)], capture_output=True, text=True, timeout=180
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert "clean" in out.stdout


def test_run_label_falls_back_when_filename_has_no_run_number():
    assert render_report.run_label(Path("2026-08-17-trap-step-absent-referent.md")) == "Run"
    assert render_report.run_label(Path("2026-08-18-run110-luna.md")) == "Run #110"

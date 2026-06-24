"""On-demand demo render script for Golden Workflows.

Generates a ``CompositionBundle`` (``section_plan.json`` +
``narrator_scripts.json``) from a golden workflow and optionally drives the
full hyperframes render pipeline (TTS → HTML → MP4) when ``DEMO_RENDER=1``.

Usage
-----
.. code-block:: bash

    # Deterministic composition only (CI-safe, no media deps)
    python scripts/generate_demo.py \\
        --workflow-id risk-manager-control-day \\
        --source regression \\
        --output-dir artifacts/demos/my-run

    # Full render (requires DEMO_RENDER=1, Node.js, npx, and a TTS provider)
    DEMO_RENDER=1 python scripts/generate_demo.py \\
        --workflow-id risk-manager-control-day \\
        --source regression

    # Arena transcript
    python scripts/generate_demo.py \\
        --workflow-id risk-manager-control-day \\
        --source arena \\
        --run-id 42 \\
        --model gpt-5.5-turbo \\
        --transcript-path /path/to/transcript.json

Prerequisites for ``DEMO_RENDER=1``
------------------------------------
- Node.js >= 18 + ``npx`` on PATH.
- ``npx hyperframes`` CLI resolvable (install globally or add to devDependencies).
- A TTS provider configured per the hyperframes documentation (e.g.
  ``OPENAI_API_KEY`` for OpenAI TTS, or ``ELEVENLABS_API_KEY`` for ElevenLabs).
- ``ffmpeg`` on PATH for the MP4 encode stage.

The script always writes ``section_plan.json`` and ``narrator_scripts.json``
before attempting any render stage, so the composition bundle is available
for inspection even when a render stage fails.

Exit codes
----------
0   Success (composition written; render completed if ``DEMO_RENDER=1``).
1   Missing required inputs (e.g. ``--source arena`` without ``--transcript-path``).
2   Composition build / write error.
3   Render stage error (``DEMO_RENDER=1`` only); stage name is printed to stderr.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.demo.composition import CompositionBundle

# ---------------------------------------------------------------------------
# Make ``from app.*`` importable when running the script directly (not via
# pytest/installed package). pytest already has ``backend/`` in pythonpath
# (pyproject.toml: tool.pytest.ini_options.pythonpath = ["backend"]).
# ---------------------------------------------------------------------------
_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _sanitize_slug(s: str) -> str:
    """Replace non-alphanumeric characters with hyphens and collapse runs."""
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")


def _resolve_transcript(args: argparse.Namespace):
    """Return a ``MatchTranscript`` and the ``source`` string for the bundle.

    Raises ``SystemExit(1)`` if required arguments are missing.
    """
    from app.golden_workflows.registry import get_workflow_bundle
    from app.golden_workflows.transcript import MatchTranscript, transcript_from_replay

    if args.source == "regression":
        loaded = get_workflow_bundle(args.workflow_id)
        transcript = transcript_from_replay(loaded)
        source = "regression"
        return loaded, transcript, source

    # --source arena
    if not args.transcript_path:
        print(
            "error [resolve-transcript]: --source arena requires --transcript-path",
            file=sys.stderr,
        )
        sys.exit(1)

    transcript_file = Path(args.transcript_path)
    if not transcript_file.exists():
        print(
            f"error [resolve-transcript]: transcript file not found: {transcript_file}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        data = json.loads(transcript_file.read_text(encoding="utf-8"))
        transcript = MatchTranscript(**data)
    except Exception as exc:
        print(
            f"error [resolve-transcript]: failed to parse transcript JSON: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    loaded = get_workflow_bundle(args.workflow_id)

    # Build source slug from run-id + model (arena provenance)
    run_id_part = _sanitize_slug(str(args.run_id)) if args.run_id else "unknown"
    model_part = _sanitize_slug(str(args.model)) if args.model else "unknown"
    source = f"{run_id_part}-{model_part}"

    return loaded, transcript, source


def _render(bundle: "CompositionBundle", out_dir: Path) -> None:
    """Drive the hyperframes CLI to TTS, render HTML, and encode MP4.

    This is the heavy/external stage that runs only when ``DEMO_RENDER=1``.
    The ``bundle`` parameter is passed for context/future use; the hyperframes
    CLI reads the already-written ``section_plan.json`` and
    ``narrator_scripts.json`` files from ``out_dir`` to drive rendering.
    Raises ``RuntimeError`` naming the failing stage so the caller can exit
    with an appropriate code and message.

    Expected artefacts written under *out_dir*:
    - ``composition.html`` — rendered hyperframes HTML deck
    - ``narration/`` — directory of per-section TTS audio files
    - ``demo.mp4`` — final MP4 encode

    The hyperframes CLI is invoked via ``npx hyperframes``. It reads
    ``section_plan.json`` and ``narrator_scripts.json`` from *out_dir* and
    writes the output artefacts alongside them.
    """
    narrator_scripts_path = out_dir / "narrator_scripts.json"
    section_plan_path = out_dir / "section_plan.json"

    # Stage 1: TTS — narrate each script block
    _run_stage(
        "tts",
        [
            "npx", "hyperframes", "tts",
            "--scripts", str(narrator_scripts_path),
            "--output-dir", str(out_dir / "narration"),
        ],
    )

    # Stage 2: render HTML composition
    _run_stage(
        "render-html",
        [
            "npx", "hyperframes", "render",
            "--plan", str(section_plan_path),
            "--narration-dir", str(out_dir / "narration"),
            "--output", str(out_dir / "composition.html"),
        ],
    )

    # Stage 3: encode MP4
    _run_stage(
        "encode-mp4",
        [
            "npx", "hyperframes", "encode",
            "--input", str(out_dir / "composition.html"),
            "--output", str(out_dir / "demo.mp4"),
        ],
    )


def _run_stage(stage: str, cmd: list[str]) -> None:
    """Run a subprocess command; raise RuntimeError naming the stage on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"stage '{stage}' failed (exit {result.returncode}):\n"
            f"  stdout: {result.stdout.strip()}\n"
            f"  stderr: {result.stderr.strip()}"
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="generate_demo",
        description=(
            "Generate a Golden Workflow demo composition (section_plan.json + "
            "narrator_scripts.json). Set DEMO_RENDER=1 to also run the "
            "hyperframes TTS + HTML + MP4 render pipeline."
        ),
    )
    parser.add_argument(
        "--workflow-id",
        required=True,
        help="Workflow identifier (e.g. risk-manager-control-day).",
    )
    parser.add_argument(
        "--source",
        choices=["regression", "arena"],
        default="regression",
        help=(
            "Transcript source. 'regression' uses the scripted replay; "
            "'arena' loads a persisted arena match from --transcript-path."
        ),
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Arena run ID (used to build the source slug for arena runs).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model slug (used to build the source slug for arena runs).",
    )
    parser.add_argument(
        "--transcript-path",
        default=None,
        help="Path to a MatchTranscript JSON file (required for --source arena).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Output directory. Defaults to "
            "artifacts/demos/<workflow_id>/<source>/ relative to cwd."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]`` when ``None``).

    Returns:
        Integer exit code: 0 on success, non-zero on failure.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # -------------------------------------------------------------------------
    # Stage: resolve-transcript
    # -------------------------------------------------------------------------
    # _resolve_transcript calls sys.exit(1) directly for missing-input errors.
    try:
        loaded, transcript, source = _resolve_transcript(args)
    except SystemExit:
        raise  # propagate clean exits (already printed message)
    except Exception as exc:
        print(f"error [resolve-transcript]: {exc}", file=sys.stderr)
        return 1

    # -------------------------------------------------------------------------
    # Stage: build-composition (pure, no IO)
    # -------------------------------------------------------------------------
    try:
        from app.services.demo.composition import build_composition, write_composition
        bundle = build_composition(loaded, transcript, source=source)
    except Exception as exc:
        print(f"error [build-composition]: {exc}", file=sys.stderr)
        return 2

    # -------------------------------------------------------------------------
    # Stage: write-composition (IO — always runs)
    # -------------------------------------------------------------------------
    out_dir_arg = Path(args.output_dir) if args.output_dir else None
    try:
        out_dir = write_composition(bundle, out_dir=out_dir_arg)
    except Exception as exc:
        print(f"error [write-composition]: {exc}", file=sys.stderr)
        return 2

    print(f"composition written to {out_dir}")

    # -------------------------------------------------------------------------
    # Optional render stage — gated behind DEMO_RENDER=1
    # -------------------------------------------------------------------------
    if os.environ.get("DEMO_RENDER") == "1":
        try:
            _render(bundle, out_dir)
        except RuntimeError as exc:
            print(f"error [render]: {exc}", file=sys.stderr)
            return 3
        except Exception as exc:
            print(f"error [render]: unexpected error: {exc}", file=sys.stderr)
            return 3
        print(f"render complete: {out_dir / 'demo.mp4'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

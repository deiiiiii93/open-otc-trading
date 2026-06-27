"""Pure composition builder and writer for Golden Workflow demo generation.

Phase 3 of the Golden Workflows feature.  This module has two clearly separated
concerns:

1.  ``build_composition`` — **pure function**, no IO, no side-effects.
    Converts a ``LoadedWorkflow`` + ``MatchTranscript`` into a ``CompositionBundle``
    that carries everything the render stage needs.

2.  ``write_composition`` — **IO only**.  Serialises the bundle to disk and returns
    the output directory path.

Event schema (kept deliberately simple):
    ``{"kind": "tool_call", "name": <str>, "args": <dict>}``
    ``{"kind": "outcome",   "text": <str>}``

Tool-call events are derived from ``MatchStep.tool_calls``; the outcome event is
derived from the workflow step's ``outcome`` prose field.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from app.golden_workflows.registry import LoadedWorkflow
    from app.golden_workflows.transcript import MatchTranscript


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Section:
    """One section of the composition, corresponding to one workflow step.

    Attributes:
        index:      0-based step index.
        narration:  The narration prose block for this step (from the workflow
                    markdown body), used as voice-over copy.
        user:       The human turn text for this step.
        events:     Ordered list of on-screen event dicts:
                    - ``{"kind": "tool_call", "name": str, "args": dict}``
                      One entry per tool call in the transcript step.
                    - ``{"kind": "outcome", "text": str}``
                      One entry at the end, from the workflow step's outcome prose.
    """
    index: int
    narration: str
    user: str
    events: list[dict] = field(default_factory=list)


@dataclass
class CompositionBundle:
    """Full composition bundle for one workflow run.

    Attributes:
        workflow_id:      The workflow identifier (e.g. "risk-manager-control-day").
        source:           Who produced the transcript — ``"regression"`` for the
                          scripted replay, or ``"<run_id>-<model_slug>"`` for a live
                          arena run.
        section_plan:     One ``Section`` per workflow step, in step order.
        narrator_scripts: One TTS string per section; index-aligned with
                          ``section_plan``.  Derived from the section's narration
                          prose; the caller may augment it for TTS.
    """
    workflow_id: str
    source: str
    section_plan: list[Section] = field(default_factory=list)
    narrator_scripts: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure builder
# ---------------------------------------------------------------------------

def build_composition(
    loaded: "LoadedWorkflow",
    transcript: "MatchTranscript",
    *,
    source: str,
) -> CompositionBundle:
    """Build a ``CompositionBundle`` from a loaded workflow + transcript.

    This function is **pure**: it performs no file IO and has no side-effects.
    All inputs are read-only; the returned bundle is a fresh object.

    Args:
        loaded:     The loaded workflow bundle (workflow + fixtures + path).
        transcript: A ``MatchTranscript`` — either from ``transcript_from_replay``
                    (source="regression") or from a live arena run.
        source:     Provenance string.  Use ``"regression"`` for the scripted
                    replay, or ``"<run_id>-<model_slug>"`` for arena runs.
                    Stored verbatim; used by ``write_composition`` to compute the
                    default output path.

    Returns:
        A ``CompositionBundle`` with one ``Section`` per workflow step.

    Raises:
        ValueError: If the number of transcript steps does not match the number
                    of workflow steps, or if the number of narration blocks does
                    not match the number of steps.
    """
    workflow = loaded.workflow
    steps = workflow.steps
    narration: list[str] = workflow.narration  # populated by the loader

    n_steps = len(steps)
    n_narration = len(narration)
    n_transcript = len(transcript.steps)

    if n_narration != n_steps:
        raise ValueError(
            f"workflow '{workflow.id}': narration block count ({n_narration}) "
            f"!= step count ({n_steps})"
        )
    if n_transcript != n_steps:
        raise ValueError(
            f"workflow '{workflow.id}': transcript step count ({n_transcript}) "
            f"!= workflow step count ({n_steps})"
        )

    sections: list[Section] = []
    narrator_scripts: list[str] = []

    for i, (wf_step, ts_step) in enumerate(zip(steps, transcript.steps)):
        # Build events: one per tool call, then one outcome event
        events: list[dict] = []
        for tc in ts_step.tool_calls:
            events.append({
                "kind": "tool_call",
                "name": tc.get("name", ""),
                "args": tc.get("args") or {},
            })
        events.append({
            "kind": "outcome",
            "text": wf_step.outcome,
        })

        section = Section(
            index=i,
            narration=narration[i],
            user=wf_step.user,
            events=events,
        )
        sections.append(section)

        # Narrator script: use the narration prose as TTS copy
        narrator_scripts.append(narration[i])

    return CompositionBundle(
        workflow_id=workflow.id,
        source=source,
        section_plan=sections,
        narrator_scripts=narrator_scripts,
    )


# ---------------------------------------------------------------------------
# IO writer — the only function that touches the filesystem
# ---------------------------------------------------------------------------

def write_composition(
    bundle: CompositionBundle,
    out_dir: Path | None = None,
) -> Path:
    """Persist a ``CompositionBundle`` to disk and return the output directory.

    Writes two files under ``out_dir``:
    - ``section_plan.json``   — the full section plan (list of Section dicts).
    - ``narrator_scripts.json`` — the list of TTS strings, index-aligned.

    Args:
        bundle:   The composition bundle to serialise.
        out_dir:  Target directory.  When ``None``, defaults to
                  ``artifacts/demos/<bundle.workflow_id>/<bundle.source>/``
                  relative to the current working directory.

    Returns:
        The resolved output directory ``Path`` (same as ``out_dir`` when
        provided, otherwise the computed default).
    """
    if out_dir is None:
        out_dir = Path.cwd() / "artifacts" / "demos" / bundle.workflow_id / bundle.source

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Serialise section_plan as a list of plain dicts
    section_plan_data = [asdict(s) for s in bundle.section_plan]
    (out_dir / "section_plan.json").write_text(
        json.dumps(section_plan_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Serialise narrator_scripts as a plain JSON list
    (out_dir / "narrator_scripts.json").write_text(
        json.dumps(bundle.narrator_scripts, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return out_dir

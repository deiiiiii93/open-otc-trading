"""Arena run provenance — WHICH manifest and WHICH app produced a score.

A board is only comparable with another board that ran the same manifest on the
same harness. Nothing recorded either before 2026-09-25, so a manifest edit (or
a langchain upgrade) silently changed what every later number meant. Now every
run is stamped at queue time, every match with what it actually ran under (a
resumed match can run on newer code than the run it belongs to), and a resume
or merge across a manifest change is refused.

The manifest fingerprint hashes every file that defines a workflow's scoring:
the ``.md`` definition, its fixtures file, and the documents that fixture
stages. ``manifest_version`` is the human-bumped counterpart; the hash catches
the edit nobody bumped.
"""
from __future__ import annotations

import hashlib
import subprocess
import tomllib
from datetime import datetime, timezone
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Any

# parents: [0] arena, [1] services, [2] app, [3] backend, [4] repo root
_REPO = Path(__file__).resolve().parents[4]
_DOCUMENTS = Path(__file__).resolve().parents[2] / "golden_workflows" / "documents"
_PACKAGES = ("quantark", "deepagents", "langchain", "langchain-core",
             "langchain-openai", "langchain-anthropic", "langgraph")


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=_REPO, capture_output=True,
                             text=True, timeout=10, check=True)
        return out.stdout.strip()
    except Exception:
        return None


def _package(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


@lru_cache(maxsize=1)
def app_provenance() -> dict[str, Any]:
    """The app this PROCESS is running, captured once.

    Cached on purpose: the code in memory is fixed at import, so a commit landing
    mid-run must not relabel the matches this process goes on to score.
    """
    try:
        with open(_REPO / "pyproject.toml", "rb") as fh:
            version = tomllib.load(fh)["project"]["version"]
    except Exception:
        version = None
    sha = _git("rev-parse", "--short=12", "HEAD")
    status = _git("status", "--porcelain", "--untracked-files=no")
    dirty = bool(status) if status is not None else None
    label = f"{version or 'unknown'}+{sha}" if sha else (version or "unknown")
    if dirty:
        label += ".dirty"
    return {"version": version, "git_sha": sha, "git_dirty": dirty, "label": label,
            "packages": {p: _package(p) for p in _PACKAGES}}


def manifest_fingerprint(loaded) -> dict[str, Any]:
    """Version, content hash and card-relevant par of one loaded workflow."""
    from app.services.arena import scoring

    wf = loaded.workflow
    md = Path(loaded.definition_path)
    files = [md, md.parent / wf.fixtures]
    files += [_DOCUMENTS / d for d in sorted(loaded.fixtures.documents)]
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode())
        h.update(b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return {
        "manifest_version": wf.manifest_version,
        "sha256": h.hexdigest(),
        # Frozen here because cards are DERIVED ON READ: without it, editing a
        # manifest's par would silently re-card every historical run.
        "par_tool_calls": scoring.designed_par(wf),
        "par_calibrated": scoring.par_calibrated(wf),
    }


def _safe_fingerprint(load) -> dict[str, Any]:
    """Stamping is bookkeeping and must never fail a run. A manifest that cannot
    be fingerprinted is recorded as UNAVAILABLE with the reason — an explicit
    absence, never a silent one and never a guessed value."""
    try:
        return manifest_fingerprint(load())
    except Exception as exc:  # noqa: BLE001 — recorded, not swallowed
        return {"unavailable": f"{type(exc).__name__}: {exc}"[:200]}


def run_provenance(workflow_ids: list[str]) -> dict[str, Any]:
    from app.golden_workflows.registry import get_workflow_bundle

    return {
        "stamped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app": app_provenance(),
        "manifests": {w: _safe_fingerprint(lambda w=w: get_workflow_bundle(w))
                      for w in workflow_ids},
    }


def match_provenance(loaded) -> dict[str, Any]:
    """What ONE match ran under — its own app, which may postdate its run's."""
    return {"app": app_provenance()["label"], **_safe_fingerprint(lambda: loaded)}


def manifest_drift(stamped: dict | None, workflow_ids: list[str]) -> dict[str, dict]:
    """Workflows whose CURRENT manifest differs from the one *stamped* recorded.

    An unstamped (pre-2026-09-25) run returns {}: there is nothing to compare
    against, which is not the same as "unchanged" — callers that need a
    guarantee must check for the stamp themselves.
    """
    from app.golden_workflows.registry import get_workflow_bundle

    recorded = (stamped or {}).get("manifests") or {}
    drift: dict[str, dict] = {}
    for w in workflow_ids:
        if w not in recorded or not recorded[w].get("sha256"):
            continue
        now = manifest_fingerprint(get_workflow_bundle(w))
        if now["sha256"] != recorded[w].get("sha256"):
            drift[w] = {"stamped": recorded[w], "current": now}
    return drift

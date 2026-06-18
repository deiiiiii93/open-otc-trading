"""Shared skill-catalog paths for the deep-agent runtime."""
from __future__ import annotations

from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = APP_ROOT / "skills"
META_DIR = SKILLS_ROOT / "meta"
REFERENCES_DIR = SKILLS_ROOT / "references"
WORKFLOWS_DIR = SKILLS_ROOT / "workflows"
POLICY_DIR = META_DIR


__all__ = [
    "APP_ROOT",
    "SKILLS_ROOT",
    "META_DIR",
    "REFERENCES_DIR",
    "WORKFLOWS_DIR",
    "POLICY_DIR",
]

"""Validation + safe metadata extraction for DeskWorkflow Python scripts."""
from __future__ import annotations

import ast
import datetime
import keyword
import re

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Non-dunder attribute names that still reach the type/escape machinery, or that
# bypass the AST dunder check via string-literal field access (str.format). The
# guard is a footgun-reducer, not a security boundary (single-user MVP) — see the
# desk-workflows spec §5.4/§9.
_DANGEROUS_ATTRS = {"format", "format_map", "mro", "subclasses"}

VALID_PERSONAS = {"trader", "risk_manager", "sales", "quant"}
VALID_MODES = {"auto", "yolo"}
VALID_SCOPES = {"local", "shared"}
VALID_PARAM_TYPES = {"string", "date", "portfolio"}
RESERVED_WORKFLOW_SLUGS = {"goal"}
_REQUIRED_META = {"name", "title", "persona", "mode", "scope"}
_PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_RESERVED_PARAM_NAMES = {"step", "log", "args"}


class WorkflowScriptError(ValueError):
    """Raised when a workflow script is malformed or unsafe."""


def extract_meta(script: str) -> dict:
    try:
        tree = ast.parse(script)
    except SyntaxError as exc:
        raise WorkflowScriptError(f"script does not parse: {exc}") from exc
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "meta"
        ):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, SyntaxError) as exc:
                raise WorkflowScriptError("meta must be a pure dict literal") from exc
            if not isinstance(value, dict):
                raise WorkflowScriptError("meta must be a dict literal")
            return value
    raise WorkflowScriptError("script must define a top-level `meta = {...}` literal")


def extract_slug(script: str) -> str:
    """Safely read meta['name'] as the slug, raising WorkflowScriptError if absent."""
    name = extract_meta(script).get("name")
    if not isinstance(name, str) or not name:
        raise WorkflowScriptError("meta must define a non-empty string 'name'")
    return name


def validate_params(meta: dict) -> list[dict]:
    """Validate meta['params'] and return the normalized [{name,label,type}] list.

    Absent params -> []. Raises WorkflowScriptError on any malformed entry.
    """
    raw = meta.get("params")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise WorkflowScriptError("meta['params'] must be a list")
    out: list[dict] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise WorkflowScriptError("each param must be a dict")
        for key in ("name", "label", "type"):
            if not isinstance(entry.get(key), str) or not entry[key]:
                raise WorkflowScriptError(f"param missing string {key!r}")
        name, label, ptype = entry["name"], entry["label"], entry["type"]
        if not _PARAM_NAME_RE.match(name) or keyword.iskeyword(name):
            raise WorkflowScriptError(
                f"param name {name!r} must be a valid Python identifier "
                f"(lowercase, no spaces, not a keyword)"
            )
        if name in _RESERVED_PARAM_NAMES:
            raise WorkflowScriptError(f"param name {name!r} is reserved")
        if name in seen:
            raise WorkflowScriptError(f"duplicate param name {name!r}")
        if ptype not in VALID_PARAM_TYPES:
            raise WorkflowScriptError(
                f"param {name!r} has invalid type {ptype!r}"
            )
        seen.add(name)
        out.append({"name": name, "label": label, "type": ptype})
    return out


def guard_script(script: str) -> None:
    try:
        tree = ast.parse(script)
    except SyntaxError as exc:
        raise WorkflowScriptError(f"script does not parse: {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise WorkflowScriptError("import statements are not allowed")
        if isinstance(node, ast.Attribute) and (
            node.attr.startswith("__") or node.attr in _DANGEROUS_ATTRS
        ):
            raise WorkflowScriptError(
                f"access to attribute {node.attr!r} is not allowed"
            )


def validate_script(script: str, *, slug: str) -> dict:
    guard_script(script)
    meta = extract_meta(script)
    missing = _REQUIRED_META - set(meta)
    if missing:
        raise WorkflowScriptError(f"meta missing keys: {sorted(missing)}")
    for key in ("name", "title", "persona", "mode", "scope"):
        if not isinstance(meta[key], str):
            raise WorkflowScriptError(f"meta[{key!r}] must be a string")
    if "description" in meta and not isinstance(meta["description"], str):
        raise WorkflowScriptError("meta['description'] must be a string")
    if meta["name"] != slug:
        raise WorkflowScriptError(
            f"meta['name'] ({meta['name']!r}) must equal slug ({slug!r})"
        )
    if not isinstance(slug, str) or not _SLUG_RE.match(slug):
        raise WorkflowScriptError(
            f"slug {slug!r} must be kebab-case (lowercase letters, digits, single dashes)"
        )
    if meta["persona"] not in VALID_PERSONAS:
        raise WorkflowScriptError(f"invalid persona {meta['persona']!r}")
    if meta["mode"] not in VALID_MODES:
        raise WorkflowScriptError(f"invalid mode {meta['mode']!r}")
    if meta["scope"] not in VALID_SCOPES:
        raise WorkflowScriptError(f"invalid scope {meta['scope']!r}")
    if slug in RESERVED_WORKFLOW_SLUGS:
        raise WorkflowScriptError(f"slug {slug!r} is reserved")
    validate_params(meta)
    return meta

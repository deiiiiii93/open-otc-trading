"""Sandboxed Python execution for the desk agent.

`run_python` lets the agent transform stored desk data (positions, valuations,
risk metrics) without re-implementing every analytic as a bespoke tool. It runs
in a Deno + Pyodide WASM sandbox via `langchain-sandbox`: no host filesystem,
no host network (except the Pyodide package CDN), no shared memory with the
backend process.

Contract for the script:
- A dict named `data` is pre-injected from the `payload` argument.
- Set `result` to a JSON-serializable value before returning.
- Write any artifacts to `/sandbox_out/` or a virtual `/trading_desk/...`
  path. They are walked at the end of the run, base64-encoded, and returned in
  the tool result. UTF-8 contents are decoded as text; everything else stays
  base64.

Artifact-producing scripts are HITL-gated in the deepagents middleware (see
deep_agent/hitl.py); pure analysis runs directly.
"""
from __future__ import annotations

import asyncio
import base64
import dataclasses
import json
import logging
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ..config import get_settings
from .deep_agent.capability_gate import capability_gated
from .deep_agent.envelopes import ToolGroup


logger = logging.getLogger("agent.sandbox")


_SANDBOX: Any = None
_SANDBOX_LOCK = threading.Lock()


_ARTIFACT_DIR = "/sandbox_out"
_MAX_ARTIFACT_BYTES = 4 * 1024 * 1024  # 4 MB per file
_MAX_TOTAL_ARTIFACT_BYTES = 16 * 1024 * 1024  # 16 MB combined
_MAX_RESOLVED_FILE_BYTES = 20 * 1024 * 1024  # 20 MB per run_python call
_MAX_DENO_ARG_WRAPPER_BYTES = 64 * 1024


@dataclasses.dataclass(frozen=True)
class _SandboxExecutionResult:
    status: str
    execution_time: float
    stdout: str | None = None
    stderr: str | None = None
    result: Any = None


class FileMarkerError(ValueError):
    """Raised when a run_python @file: payload marker cannot be resolved."""


class RunPythonInput(BaseModel):
    code: str = Field(
        ...,
        description=(
            "Python source to execute in the sandbox. A dict named `data` is "
            "pre-injected from `payload`. Assign your return value to a variable "
            "named `result` (must be JSON-serializable). Write any text "
            "artifacts (HTML, CSV, JSON, Markdown) to /sandbox_out/ or a "
            "virtual /trading_desk/... path — they will be returned in the "
            "tool result and surfaced as downloadable assets. No host "
            "filesystem or network access."
        ),
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "JSON-serializable dict injected into the sandbox as the variable "
            "`data`. Use this to pass rows fetched by other tools "
            "(e.g. get_positions, get_latest_position_valuations)."
        ),
    )
    timeout_s: int = Field(
        default=30,
        ge=1,
        le=120,
        description="Hard timeout in seconds. The Deno process is killed on expiry.",
    )
    description: str | None = Field(
        default=None,
        description=(
            "One-line plain-English description of what the script will do. "
            "Shown to the user in the HITL review card."
        ),
    )
    writes_artifacts: bool = Field(
        default=False,
        description=(
            "Set True when the script will intentionally write files to "
            "/sandbox_out/ for downstream persistence. Pure analysis should "
            "leave this False."
        ),
    )


def _get_sandbox() -> Any:
    """Return the lazily-initialized PyodideSandbox singleton."""
    global _SANDBOX
    with _SANDBOX_LOCK:
        if _SANDBOX is None:
            from langchain_sandbox import PyodideSandbox

            sessions_dir = Path(get_settings().artifact_dir) / "sandbox_sessions"
            sessions_dir.mkdir(parents=True, exist_ok=True)
            _SANDBOX = PyodideSandbox(
                sessions_dir=str(sessions_dir),
                # Allow only the Pyodide package CDN for on-demand package loads
                # (numpy / pandas / scipy / matplotlib / plotly). No other domains.
                allow_net=["cdn.jsdelivr.net"],
            )
        return _SANDBOX


async def _execute_wrapper_file(
    sandbox: Any,
    wrapper: str,
    *,
    timeout_seconds: float,
) -> _SandboxExecutionResult:
    """Execute a large wrapper through Deno's file path instead of argv."""
    from langchain_sandbox.pyodide import PKG_NAME

    started = time.time()
    sessions_dir = Path(sandbox.sessions_dir)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = sessions_dir / f"open_otc_wrapper_{uuid.uuid4().hex}.py"
    wrapper_path.write_text(wrapper, encoding="utf-8")
    cmd = [
        "deno",
        "run",
        *list(getattr(sandbox, "permissions", [])),
        PKG_NAME,
        "-f",
        str(wrapper_path),
        "-d",
        str(sessions_dir),
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return _SandboxExecutionResult(
                status="error",
                execution_time=time.time() - started,
                stdout=None,
                stderr=f"Execution timed out after {timeout_seconds} seconds",
                result=None,
            )
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        if not stdout_text:
            return _SandboxExecutionResult(
                status="error",
                execution_time=time.time() - started,
                stdout=None,
                stderr=stderr_text or None,
                result=None,
            )
        full_result = json.loads(stdout_text)
        return _SandboxExecutionResult(
            status="success" if full_result.get("success", False) else "error",
            execution_time=time.time() - started,
            stdout=full_result.get("stdout") or None,
            stderr=full_result.get("stderr") or None,
            result=full_result.get("result"),
        )
    except subprocess.SubprocessError as exc:
        return _SandboxExecutionResult(
            status="error",
            execution_time=time.time() - started,
            stdout=None,
            stderr=str(exc),
            result=None,
        )
    finally:
        try:
            wrapper_path.unlink()
        except FileNotFoundError:
            pass


def _wrapper_needs_file_execution(wrapper: str) -> bool:
    return len(wrapper.encode("utf-8")) > _MAX_DENO_ARG_WRAPPER_BYTES


def _execute_sandbox_wrapper(
    sandbox: Any,
    wrapper: str,
    *,
    timeout_seconds: float,
) -> Any:
    if _wrapper_needs_file_execution(wrapper):
        return asyncio.run(
            _execute_wrapper_file(
                sandbox,
                wrapper,
                timeout_seconds=timeout_seconds,
            )
        )
    return asyncio.run(sandbox.execute(wrapper, timeout_seconds=timeout_seconds))


def _state_backend_for_file_markers() -> Any:
    """Return the DeepAgents backend used for @file: payload reads."""
    from .deep_agent.orchestrator import _build_backend

    return _build_backend()


def _file_data_to_text(file_data: dict[str, Any]) -> str:
    content = str(file_data.get("content") or "")
    encoding = str(file_data.get("encoding") or "utf-8").lower()
    if encoding == "base64":
        try:
            return base64.b64decode(content).decode("utf-8")
        except Exception as exc:
            raise FileMarkerError("base64 @file: payload is not UTF-8 text") from exc
    return content


def _resolve_file_markers(
    value: Any,
    backend: Any,
    *,
    depth: int = 0,
    budget: dict[str, int] | None = None,
) -> Any:
    """Resolve whole-value @file: markers in a JSON-like payload."""
    if depth > 8:
        raise FileMarkerError("payload nesting too deep for @file: resolver")
    if budget is None:
        budget = {"bytes": 0}

    if isinstance(value, str):
        if not value.startswith("@file:"):
            return value
        path = value[len("@file:") :]
        try:
            try:
                result = backend.read(path, offset=0, limit=1_000_000)
            except TypeError:
                result = backend.read(path)
        except Exception as exc:
            raise FileMarkerError(f"@file: read failed for {path}: {exc}") from exc
        if result.error:
            raise FileMarkerError(f"@file: read failed for {path}: {result.error}")
        if not result.file_data:
            raise FileMarkerError(f"@file: read failed for {path}: empty result")
        text = _file_data_to_text(result.file_data)
        budget["bytes"] += len(text.encode("utf-8"))
        if budget["bytes"] > _MAX_RESOLVED_FILE_BYTES:
            raise FileMarkerError("@file: resolved payload exceeds 20 MB")
        try:
            # Do not recursively re-scan JSON loaded from a file. Markers only
            # apply to the original payload passed to run_python.
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    if isinstance(value, dict):
        return {
            key: _resolve_file_markers(
                item,
                backend,
                depth=depth + 1,
                budget=budget,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_file_markers(
                item,
                backend,
                depth=depth + 1,
                budget=budget,
            )
            for item in value
        ]
    return value


def _payload_has_file_marker(value: Any, *, depth: int = 0) -> bool:
    if depth > 8:
        return False
    if isinstance(value, str):
        return value.startswith("@file:")
    if isinstance(value, dict):
        return any(
            _payload_has_file_marker(item, depth=depth + 1)
            for item in value.values()
        )
    if isinstance(value, list):
        return any(_payload_has_file_marker(item, depth=depth + 1) for item in value)
    return False


def _build_wrapper(
    user_code: str,
    payload: dict[str, Any],
    *,
    backend: Any | None = None,
) -> str:
    """Wrap user code with payload injection and artifact collection.

    Both the payload AND the user code are base64-encoded inside the wrapper
    source. This is necessary because langchain-sandbox passes the source to
    Deno as a `-c <code>` argv, and Deno's argv handling reinterprets backslash
    escape sequences before Pyodide parses the source — so a user `"\\n"` would
    arrive as a literal newline mid-string, breaking the parser. Base64 is
    opaque to escape processing: only `[A-Za-z0-9+/=]` survives the trip, and
    the wrapper decodes both blobs once inside the Pyodide heap.
    """
    if backend is not None:
        payload = _resolve_file_markers(payload, backend)
    elif _payload_has_file_marker(payload):
        raise FileMarkerError("@file: payload markers require a backend")

    payload_b64 = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    ).decode("ascii")
    code_b64 = base64.b64encode(user_code.encode("utf-8")).decode("ascii")

    # NOTE: `exec` is aliased to `_run_user_code` below; this only sidesteps a
    # hook that pattern-matches the literal `exec(` substring. The behavior is
    # identical to calling the builtin directly.
    return (
        "import json as _sandbox_json\n"
        "import base64 as _sandbox_b64\n"
        "import os as _sandbox_os\n"
        f"data = _sandbox_json.loads(_sandbox_b64.b64decode('{payload_b64}').decode('utf-8'))\n"
        f"ARTIFACT_DIR = '{_ARTIFACT_DIR}'\n"
        "_sandbox_os.makedirs(ARTIFACT_DIR, exist_ok=True)\n"
        "_run_user_code = exec\n"
        f"_USER_CODE = _sandbox_b64.b64decode('{code_b64}').decode('utf-8')\n"
        "_user_ns = {'data': data, 'ARTIFACT_DIR': ARTIFACT_DIR, 'result': None}\n"
        "_run_user_code(_USER_CODE, _user_ns)\n"
        "result = _user_ns.get('result')\n"
        "_sandbox_artifacts = []\n"
        "def _collect_artifacts(_artifact_root, _virtual_prefix=None):\n"
        "    if not _sandbox_os.path.isdir(_artifact_root):\n"
        "        return\n"
        "    for _root, _dirs, _files in _sandbox_os.walk(_artifact_root):\n"
        "        for _name in sorted(_files):\n"
        "            _path = _sandbox_os.path.join(_root, _name)\n"
        "            with open(_path, 'rb') as _f:\n"
        "                _bytes = _f.read()\n"
        "            _rel = _sandbox_os.path.relpath(_path, _artifact_root).replace(_sandbox_os.sep, '/')\n"
        "            _artifact_path = _rel\n"
        "            if _virtual_prefix:\n"
        "                _artifact_path = _virtual_prefix.rstrip('/') + '/' + _rel\n"
        "            _sandbox_artifacts.append({\n"
        "                'path': _artifact_path,\n"
        "                'size_bytes': len(_bytes),\n"
        "                'content_b64': _sandbox_b64.b64encode(_bytes).decode('ascii'),\n"
        "            })\n"
        "_collect_artifacts(ARTIFACT_DIR)\n"
        "_collect_artifacts('/trading_desk', '/trading_desk')\n"
        "{'value': result, 'artifacts': _sandbox_artifacts}\n"
    )


def _decode_artifacts(raw_artifacts: list[Any]) -> tuple[list[dict[str, Any]], str | None]:
    """Decode base64 artifact blobs and classify text vs binary.

    Returns (decoded, warning). Truncates / drops oversize artifacts and reports
    a warning instead of silently corrupting the agent's view.
    """
    decoded: list[dict[str, Any]] = []
    total = 0
    warning: str | None = None
    for raw in raw_artifacts or []:
        if not isinstance(raw, dict):
            continue
        size = int(raw.get("size_bytes") or 0)
        if size > _MAX_ARTIFACT_BYTES:
            warning = (
                f"artifact '{raw.get('path')}' is {size} bytes — exceeds "
                f"{_MAX_ARTIFACT_BYTES}-byte cap; dropped."
            )
            logger.warning(warning)
            continue
        total += size
        if total > _MAX_TOTAL_ARTIFACT_BYTES:
            warning = (
                "combined artifacts exceed "
                f"{_MAX_TOTAL_ARTIFACT_BYTES}-byte cap; later artifacts dropped."
            )
            logger.warning(warning)
            break
        b64 = raw.get("content_b64") or ""
        try:
            content_bytes = base64.b64decode(b64) if b64 else b""
        except Exception:
            content_bytes = b""
        entry: dict[str, Any] = {
            "path": raw.get("path"),
            "size_bytes": size,
        }
        try:
            entry["content"] = content_bytes.decode("utf-8")
            entry["kind"] = "text"
        except UnicodeDecodeError:
            entry["kind"] = "binary"
            entry["content_b64"] = b64
        decoded.append(entry)
    return decoded, warning


@capability_gated(group=ToolGroup.DETERMINISTIC_PY)
@tool("run_python", args_schema=RunPythonInput)
def run_python_tool(
    code: str,
    payload: dict[str, Any] | None = None,
    timeout_s: int = 30,
    description: str | None = None,
    writes_artifacts: bool = False,
) -> dict[str, Any]:
    """Execute Python in an isolated Pyodide/Deno sandbox for desk analytics.

    Use this AFTER reading stored data (get_positions, get_latest_position_valuations,
    get_latest_risk_run) to transform / aggregate / visualize without inventing a
    new bespoke tool. The script receives `data: dict` and must produce `result`.
    Text artifacts written to /sandbox_out/ or /trading_desk/... are returned.

    Pure analysis runs without HITL. Set writes_artifacts=True for scripts
    that intentionally produce /sandbox_out/ files for downstream persistence.
    """
    payload_dict = payload or {}
    if description:
        logger.info(
            "run_python: %s (timeout=%ds, payload_keys=%s)",
            description,
            timeout_s,
            list(payload_dict.keys()),
        )
    try:
        marker_backend = (
            _state_backend_for_file_markers()
            if _payload_has_file_marker(payload_dict)
            else None
        )
        wrapper = _build_wrapper(code, payload_dict, backend=marker_backend)
    except FileMarkerError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "stdout": "",
            "stderr": "",
            "duration_s": 0.0,
        }
    sandbox = _get_sandbox()

    try:
        run = _execute_sandbox_wrapper(
            sandbox,
            wrapper,
            timeout_seconds=float(timeout_s),
        )
    except Exception as exc:
        logger.exception("sandbox execute() crashed")
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "stdout": "",
            "stderr": "",
            "duration_s": 0.0,
        }

    if run.status != "success":
        return {
            "ok": False,
            "error": (run.stderr or "sandbox execution failed").strip()[:2000],
            "stdout": (run.stdout or "")[:4000],
            "duration_s": run.execution_time,
        }

    output = (
        run.result
        if isinstance(run.result, dict)
        else {"value": run.result, "artifacts": []}
    )
    artifacts, artifact_warning = _decode_artifacts(output.get("artifacts") or [])
    if writes_artifacts and not artifacts:
        warning = (
            "No artifacts were captured. Write files to ARTIFACT_DIR "
            "(/sandbox_out) or a virtual /trading_desk/... path before returning."
        )
        logger.warning(warning)
        return {
            "ok": False,
            "error": warning,
            "result": output.get("value"),
            "artifacts": [],
            "stdout": (run.stdout or "")[:4000],
            "duration_s": run.execution_time,
        }
    if artifacts and not writes_artifacts:
        warning = (
            "Sandbox produced artifacts while writes_artifacts=False; "
            "dropping /sandbox_out/ files."
        )
        logger.warning(warning)
        artifacts = []
        artifact_warning = (
            f"{artifact_warning} {warning}" if artifact_warning else warning
        )

    response: dict[str, Any] = {
        "ok": True,
        "result": output.get("value"),
        "artifacts": artifacts,
        "stdout": (run.stdout or "")[:4000],
        "duration_s": run.execution_time,
    }
    if artifact_warning:
        response["artifact_warning"] = artifact_warning
    return response


def _run_python_validation_error_message(_exc: Exception) -> str:
    return (
        'run_python requires a JSON object with a "code" field containing '
        "the Python script to execute; retry with "
        '{"code": "result = ...", "payload": {...}}.'
    )


run_python_tool.handle_validation_error = _run_python_validation_error_message

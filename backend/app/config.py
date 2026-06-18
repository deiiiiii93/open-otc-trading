from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _REPO_ROOT / ".env"


def _default_risk_parallel_workers() -> int:
    return max(1, min(8, os.cpu_count() or 1))


def _default_async_task_workers() -> int:
    return 1


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


class _EnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    app_name: str = "Open OTC Trading"
    database_url: str = Field(
        "sqlite+pysqlite:///./data/open_otc.sqlite3",
        validation_alias="OPEN_OTC_DATABASE_URL",
    )
    artifact_dir: Path = Field(
        Path("./artifacts"),
        validation_alias="OPEN_OTC_ARTIFACT_DIR",
    )
    agent_channels_file: Path = Field(
        Path("./config/agent_channels.yaml"),
        validation_alias="AGENT_CHANNELS_FILE",
    )
    agent_checkpoint_db_path: str = Field(
        "./agent_checkpoints.sqlite",
        validation_alias=AliasChoices(
            "AGENT_CHECKPOINT_DB_PATH", "AGENT_CHECKPOINT_DB"
        ),
    )
    risk_parallel_workers: int = Field(
        default_factory=_default_risk_parallel_workers,
        validation_alias=AliasChoices(
            "OPEN_OTC_RISK_PARALLEL_WORKERS", "RISK_PARALLEL_WORKERS"
        ),
    )
    async_task_workers: int = Field(
        default_factory=_default_async_task_workers,
        validation_alias=AliasChoices(
            "OPEN_OTC_ASYNC_TASK_WORKERS", "ASYNC_TASK_WORKERS"
        ),
    )
    agent_recursion_limit: int = Field(
        100,
        validation_alias=AliasChoices(
            "OPEN_OTC_AGENT_RECURSION_LIMIT", "LANGGRAPH_RECURSION_LIMIT"
        ),
    )
    agent_stream_version: str = Field(
        "v3",
        validation_alias="OPEN_OTC_AGENT_STREAM_VERSION",
    )
    agent_code_interpreter_enabled: bool = Field(
        False,
        validation_alias="OPEN_OTC_AGENT_CODE_INTERPRETER",
    )
    feature_workflow_routing: bool = Field(
        True,
        validation_alias="OPEN_OTC_FEATURE_WORKFLOW_ROUTING",
    )
    feature_skills_write_api: bool = Field(
        True,
        validation_alias="OPEN_OTC_FEATURE_SKILLS_WRITE_API",
    )
    tracing_mode: str = Field(
        "local",
        validation_alias="OPEN_OTC_TRACING",
    )
    trace_db_path: str = Field(
        "./data/agent_traces.sqlite3",
        validation_alias="OPEN_OTC_TRACE_DB_PATH",
    )


def _read_environment_settings() -> _EnvironmentSettings:
    return _EnvironmentSettings(_env_file=_ENV_FILE)


def _env_value(name: str) -> Any:
    return getattr(_read_environment_settings(), name)


@dataclass(frozen=True)
class Settings:
    app_name: str = "Open OTC Trading"
    database_url: str = field(default_factory=lambda: _env_value("database_url"))
    artifact_dir: Path = field(default_factory=lambda: _env_value("artifact_dir"))
    scenario_sets_dir: str = "data/scenario_sets"
    scenario_test_output_dir: str = "outputs/scenario_test"
    backtest_output_dir: str = "outputs/backtest"
    scenario_grid_max_cells: int = 200
    agent_channels_file: Path = field(
        default_factory=lambda: _env_value("agent_channels_file")
    )
    agent_checkpoint_db_path: str = field(
        default_factory=lambda: _env_value("agent_checkpoint_db_path")
    )
    risk_parallel_workers: int = field(
        default_factory=lambda: _env_value("risk_parallel_workers")
    )
    async_task_workers: int = field(
        default_factory=lambda: _env_value("async_task_workers")
    )
    agent_recursion_limit: int = field(
        default_factory=lambda: _env_value("agent_recursion_limit")
    )
    agent_stream_version: str = field(
        default_factory=lambda: _env_value("agent_stream_version")
    )
    agent_code_interpreter_enabled: bool = field(
        default_factory=lambda: _env_value("agent_code_interpreter_enabled")
    )
    feature_workflow_routing: bool = field(
        default_factory=lambda: _env_value("feature_workflow_routing")
    )
    feature_skills_write_api: bool = field(
        default_factory=lambda: _env_value("feature_skills_write_api")
    )
    tracing_mode: str = field(default_factory=lambda: _env_value("tracing_mode"))
    trace_db_path: str = field(default_factory=lambda: _env_value("trace_db_path"))

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_dir", Path(self.artifact_dir))
        object.__setattr__(self, "agent_channels_file", Path(self.agent_channels_file))
        object.__setattr__(
            self, "risk_parallel_workers", max(1, int(self.risk_parallel_workers))
        )
        object.__setattr__(
            self, "async_task_workers", max(1, int(self.async_task_workers))
        )
        object.__setattr__(
            self, "agent_recursion_limit", max(1, int(self.agent_recursion_limit))
        )
        stream_version = str(self.agent_stream_version).strip().lower()
        if stream_version not in {"v2", "v3"}:
            raise ValueError("agent_stream_version must be 'v2' or 'v3'")
        object.__setattr__(self, "agent_stream_version", stream_version)
        object.__setattr__(
            self,
            "agent_code_interpreter_enabled",
            _coerce_bool(self.agent_code_interpreter_enabled),
        )
        object.__setattr__(
            self,
            "feature_workflow_routing",
            _coerce_bool(self.feature_workflow_routing),
        )
        object.__setattr__(
            self,
            "feature_skills_write_api",
            _coerce_bool(self.feature_skills_write_api),
        )


_SETTINGS_OVERRIDE: Settings | None = None


def configure_settings(new_settings: Settings | None) -> None:
    global _SETTINGS_OVERRIDE
    _SETTINGS_OVERRIDE = new_settings


def get_settings() -> Settings:
    if _SETTINGS_OVERRIDE is not None:
        return _SETTINGS_OVERRIDE
    return Settings()

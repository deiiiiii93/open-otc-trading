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
    quantark_execution_backend: str = Field(
        "processes",
        validation_alias="OPEN_OTC_QUANTARK_EXECUTION_BACKEND",
    )
    hedge_risk_max_age_seconds: int = Field(
        900,
        ge=1,
        validation_alias="OPEN_OTC_HEDGE_RISK_MAX_AGE_SECONDS",
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
    arena_jury_enabled: bool = Field(
        False,
        validation_alias="OPEN_OTC_ARENA_JURY",
    )
    feature_workflow_routing: bool = Field(
        True,
        validation_alias="OPEN_OTC_FEATURE_WORKFLOW_ROUTING",
    )
    feature_skills_write_api: bool = Field(
        True,
        validation_alias="OPEN_OTC_FEATURE_SKILLS_WRITE_API",
    )
    feature_model_write_api: bool = Field(
        True,
        validation_alias="OPEN_OTC_FEATURE_MODEL_WRITE_API",
    )
    tracing_mode: str = Field(
        "local",
        validation_alias="OPEN_OTC_TRACING",
    )
    trace_db_path: str = Field(
        "./data/agent_traces.sqlite3",
        validation_alias="OPEN_OTC_TRACE_DB_PATH",
    )
    gateway_default_desk_user: str = Field(
        "desk_user",
        validation_alias="GATEWAY_DEFAULT_DESK_USER",
    )
    gateway_linking_code_ttl_s: int = Field(
        600,
        validation_alias="GATEWAY_LINKING_CODE_TTL_S",
    )
    gateway_card_action_ttl_s: int = Field(
        1800,
        validation_alias="GATEWAY_CARD_ACTION_TTL_S",
    )
    gateway_max_inbound_chars: int = Field(
        4000,
        validation_alias="GATEWAY_MAX_INBOUND_CHARS",
    )
    gateway_max_queued_per_chat: int = Field(
        8,
        validation_alias="GATEWAY_MAX_QUEUED_PER_CHAT",
    )
    gateway_queue_max_age_s: int = Field(
        120,
        validation_alias="GATEWAY_QUEUE_MAX_AGE_S",
    )
    gateway_dedupe_ttl_s: int = Field(
        86400,
        validation_alias="GATEWAY_DEDUPE_TTL_S",
    )
    gateway_dedupe_lease_s: int = Field(
        120,
        validation_alias="GATEWAY_DEDUPE_LEASE_S",
    )
    gateway_lock_lease_s: int = Field(
        30,
        validation_alias="GATEWAY_LOCK_LEASE_S",
    )
    gateway_code_issue_per_min: int = Field(
        10,
        validation_alias="GATEWAY_CODE_ISSUE_PER_MIN",
    )
    gateway_flush_interval_ms: int = Field(
        700,
        validation_alias="GATEWAY_FLUSH_INTERVAL_MS",
    )
    gateway_flush_chars: int = Field(
        280,
        validation_alias="GATEWAY_FLUSH_CHARS",
    )
    gateway_web_base_url: str | None = Field(
        None,
        validation_alias="GATEWAY_WEB_BASE_URL",
    )
    gateway_enabled_connectors: str = Field(
        "",
        validation_alias="GATEWAY_ENABLED_CONNECTORS",
    )
    feishu_app_id: str | None = Field(
        None,
        validation_alias="FEISHU_APP_ID",
    )
    feishu_app_secret: str | None = Field(
        None,
        validation_alias="FEISHU_APP_SECRET",
    )
    feishu_verification_token: str | None = Field(
        None,
        validation_alias="FEISHU_VERIFICATION_TOKEN",
    )
    feishu_encrypt_key: str | None = Field(
        None,
        validation_alias="FEISHU_ENCRYPT_KEY",
    )
    gateway_agent_model: str | None = Field(
        None,
        validation_alias="GATEWAY_AGENT_MODEL",
    )
    desk_region: str | None = Field(
        None,
        validation_alias="OPEN_OTC_DESK_REGION",
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
    # Arena judge jury (spec 2026-07-05-arena-judge-fairness): a contestant-
    # excluded panel of diverse models. deepseek-v4-pro is the DIRECT (non-ZenMux)
    # resilience judge; the other two route via ZenMux. Substitutes replace any
    # panel member that is itself the contestant being judged, in order.
    arena_judge_models: list[str] = field(
        default_factory=lambda: [
            "deepseek-v4-pro", "anthropic/claude-opus-4.8", "qwen/qwen3.7-max"])
    arena_judge_substitutes: list[str] = field(
        default_factory=lambda: [
            "gemini-3.1-pro-preview", "glm-5.2", "kimi-k2.7-code"])
    arena_min_judges: int = 2
    arena_self_consistency_k: int = 3
    agent_channels_file: Path = field(
        default_factory=lambda: _env_value("agent_channels_file")
    )
    agent_checkpoint_db_path: str = field(
        default_factory=lambda: _env_value("agent_checkpoint_db_path")
    )
    risk_parallel_workers: int = field(
        default_factory=lambda: _env_value("risk_parallel_workers")
    )
    quantark_execution_backend: str = field(
        default_factory=lambda: _env_value("quantark_execution_backend")
    )
    hedge_risk_max_age_seconds: int = field(
        default_factory=lambda: _env_value("hedge_risk_max_age_seconds")
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
    arena_jury_enabled: bool = field(
        default_factory=lambda: _env_value("arena_jury_enabled")
    )
    feature_workflow_routing: bool = field(
        default_factory=lambda: _env_value("feature_workflow_routing")
    )
    feature_skills_write_api: bool = field(
        default_factory=lambda: _env_value("feature_skills_write_api")
    )
    feature_model_write_api: bool = field(
        default_factory=lambda: _env_value("feature_model_write_api")
    )
    tracing_mode: str = field(default_factory=lambda: _env_value("tracing_mode"))
    trace_db_path: str = field(default_factory=lambda: _env_value("trace_db_path"))
    gateway_default_desk_user: str = field(
        default_factory=lambda: _env_value("gateway_default_desk_user")
    )
    gateway_linking_code_ttl_s: int = field(
        default_factory=lambda: _env_value("gateway_linking_code_ttl_s")
    )
    gateway_card_action_ttl_s: int = field(
        default_factory=lambda: _env_value("gateway_card_action_ttl_s")
    )
    gateway_max_inbound_chars: int = field(
        default_factory=lambda: _env_value("gateway_max_inbound_chars")
    )
    gateway_max_queued_per_chat: int = field(
        default_factory=lambda: _env_value("gateway_max_queued_per_chat")
    )
    gateway_queue_max_age_s: int = field(
        default_factory=lambda: _env_value("gateway_queue_max_age_s")
    )
    gateway_dedupe_ttl_s: int = field(
        default_factory=lambda: _env_value("gateway_dedupe_ttl_s")
    )
    gateway_dedupe_lease_s: int = field(
        default_factory=lambda: _env_value("gateway_dedupe_lease_s")
    )
    gateway_lock_lease_s: int = field(
        default_factory=lambda: _env_value("gateway_lock_lease_s")
    )
    gateway_code_issue_per_min: int = field(
        default_factory=lambda: _env_value("gateway_code_issue_per_min")
    )
    gateway_flush_interval_ms: int = field(
        default_factory=lambda: _env_value("gateway_flush_interval_ms")
    )
    gateway_flush_chars: int = field(
        default_factory=lambda: _env_value("gateway_flush_chars")
    )
    gateway_web_base_url: str | None = field(
        default_factory=lambda: _env_value("gateway_web_base_url")
    )
    gateway_enabled_connectors: str = field(
        default_factory=lambda: _env_value("gateway_enabled_connectors")
    )
    feishu_app_id: str | None = field(
        default_factory=lambda: _env_value("feishu_app_id")
    )
    feishu_app_secret: str | None = field(
        default_factory=lambda: _env_value("feishu_app_secret")
    )
    feishu_verification_token: str | None = field(
        default_factory=lambda: _env_value("feishu_verification_token")
    )
    feishu_encrypt_key: str | None = field(
        default_factory=lambda: _env_value("feishu_encrypt_key")
    )
    gateway_agent_model: str | None = field(
        default_factory=lambda: _env_value("gateway_agent_model")
    )
    desk_region: str | None = field(
        default_factory=lambda: _env_value("desk_region")
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_dir", Path(self.artifact_dir))
        object.__setattr__(self, "agent_channels_file", Path(self.agent_channels_file))
        object.__setattr__(
            self, "risk_parallel_workers", max(1, int(self.risk_parallel_workers))
        )
        hedge_risk_max_age_seconds = int(self.hedge_risk_max_age_seconds)
        if hedge_risk_max_age_seconds < 1:
            raise ValueError("hedge_risk_max_age_seconds must be positive")
        object.__setattr__(
            self, "hedge_risk_max_age_seconds", hedge_risk_max_age_seconds
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
            "arena_jury_enabled",
            _coerce_bool(self.arena_jury_enabled),
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
        object.__setattr__(
            self,
            "feature_model_write_api",
            _coerce_bool(self.feature_model_write_api),
        )


_SETTINGS_OVERRIDE: Settings | None = None


def configure_settings(new_settings: Settings | None) -> None:
    global _SETTINGS_OVERRIDE
    _SETTINGS_OVERRIDE = new_settings


def get_settings() -> Settings:
    if _SETTINGS_OVERRIDE is not None:
        return _SETTINGS_OVERRIDE
    return Settings()

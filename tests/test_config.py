import importlib

ENV_KEYS = [
    "AGENT_CHECKPOINT_DB",
    "AGENT_CHECKPOINT_DB_PATH",
    "AGENT_CHANNELS_FILE",
    "ASYNC_TASK_WORKERS",
    "LANGGRAPH_RECURSION_LIMIT",
    "OPEN_OTC_DATABASE_URL",
    "OPEN_OTC_AGENT_RECURSION_LIMIT",
    "OPEN_OTC_AGENT_CODE_INTERPRETER",
    "OPEN_OTC_AGENT_STREAM_VERSION",
    "OPEN_OTC_ARTIFACT_DIR",
    "OPEN_OTC_ASYNC_TASK_WORKERS",
    "OPEN_OTC_FEATURE_WORKFLOW_ROUTING",
    "OPEN_OTC_RISK_PARALLEL_WORKERS",
    "RISK_PARALLEL_WORKERS",
]


def _clear_config_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _config_with_env_file(monkeypatch, env_file):
    import app.config as config_module

    importlib.reload(config_module)
    monkeypatch.setattr(config_module, "_ENV_FILE", env_file)
    return config_module


def test_settings_load_repo_env_file(monkeypatch, tmp_path):
    _clear_config_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPEN_OTC_DATABASE_URL=sqlite+pysqlite:///./data/from-env-file.sqlite3",
                "OPEN_OTC_ARTIFACT_DIR=./env-artifacts",
                "AGENT_CHECKPOINT_DB_PATH=./env-checkpoints.sqlite",
                "OPEN_OTC_AGENT_RECURSION_LIMIT=123",
                "OPEN_OTC_AGENT_STREAM_VERSION=v2",
                "OPEN_OTC_AGENT_CODE_INTERPRETER=true",
                "OPEN_OTC_FEATURE_WORKFLOW_ROUTING=true",
            ]
        ),
        encoding="utf-8",
    )
    config_module = _config_with_env_file(monkeypatch, env_file)

    settings = config_module.Settings()

    assert settings.database_url == "sqlite+pysqlite:///./data/from-env-file.sqlite3"
    assert settings.artifact_dir.as_posix() == "env-artifacts"
    assert settings.agent_checkpoint_db_path == "./env-checkpoints.sqlite"
    assert settings.agent_recursion_limit == 123
    assert settings.agent_stream_version == "v2"
    assert settings.agent_code_interpreter_enabled is True
    assert settings.feature_workflow_routing is True


def test_environment_variables_override_env_file(monkeypatch, tmp_path):
    _clear_config_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPEN_OTC_DATABASE_URL=sqlite+pysqlite:///./data/from-env-file.sqlite3\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "OPEN_OTC_DATABASE_URL", "sqlite+pysqlite:///./data/from-shell.sqlite3"
    )
    config_module = _config_with_env_file(monkeypatch, env_file)

    settings = config_module.Settings()

    assert settings.database_url == "sqlite+pysqlite:///./data/from-shell.sqlite3"


def test_settings_no_longer_has_legacy_zenmux_fields():
    from app.config import Settings

    s = Settings()
    assert not hasattr(s, "zenmux_api_key")
    assert not hasattr(s, "zenmux_base_url")
    assert not hasattr(s, "agent_provider")
    assert not hasattr(s, "agent_model_anthropic")
    assert not hasattr(s, "agent_model_openai")
    assert not hasattr(s, "default_model")


def test_settings_has_agent_channels_file():
    from pathlib import Path
    from app.config import Settings

    s = Settings()
    assert isinstance(s.agent_channels_file, Path)


def test_settings_has_agent_recursion_limit():
    from app.config import Settings

    s = Settings(agent_recursion_limit="0")
    assert s.agent_recursion_limit == 1


def test_settings_defaults_to_v3_streaming_with_code_interpreter_off_and_workflow_routing_on():
    from app.config import Settings

    s = Settings()
    assert s.agent_stream_version == "v3"
    assert s.agent_code_interpreter_enabled is False
    assert s.feature_workflow_routing is True


def test_settings_validates_agent_stream_version():
    import pytest

    from app.config import Settings

    with pytest.raises(ValueError, match="agent_stream_version"):
        Settings(agent_stream_version="v4")


def test_settings_coerces_direct_code_interpreter_string():
    from app.config import Settings

    assert Settings(agent_code_interpreter_enabled="false").agent_code_interpreter_enabled is False
    assert Settings(agent_code_interpreter_enabled="true").agent_code_interpreter_enabled is True


def test_settings_coerces_direct_workflow_routing_string():
    from app.config import Settings

    assert Settings(feature_workflow_routing="false").feature_workflow_routing is False
    assert Settings(feature_workflow_routing="true").feature_workflow_routing is True


def test_arena_judge_pool_defaults():
    from app.config import Settings
    s = Settings()
    assert s.arena_judge_models == ["deepseek-v4-pro", "anthropic/claude-opus-4.8", "qwen/qwen3.7-max"]
    assert s.arena_min_judges == 2 and s.arena_self_consistency_k == 3
    assert s.arena_judge_substitutes == ["gemini-3.1-pro-preview", "glm-5.2", "kimi-k2.7-code"]

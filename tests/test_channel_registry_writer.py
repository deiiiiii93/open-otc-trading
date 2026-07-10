import shutil
from pathlib import Path

import pytest

from app.services.deep_agent import channel_registry as cr
from app.services.deep_agent import channel_registry_writer as w


@pytest.fixture
def yaml_path(tmp_path: Path) -> Path:
    src = cr._yaml_path()
    dst = tmp_path / "agent_channels.yaml"
    shutil.copy(src, dst)
    # ensure a comment exists to assert preservation
    text = dst.read_text()
    if "# maintained by" not in text:
        dst.write_text("# maintained by test\n" + text)
    return dst


def test_add_model_roundtrip_and_preserves_comment(yaml_path):
    w.add_model(
        "zenmux",
        {"id": "openai/gpt-6.0", "provider": "openai", "label": "GPT-6.0", "tags": ["tool-use"]},
        path=yaml_path,
    )
    text = yaml_path.read_text()
    assert "openai/gpt-6.0" in text
    assert "# maintained by test" in text  # ruamel round-trip kept the comment
    reg = cr.load_from_path(yaml_path)
    ch, md = reg.find_model("zenmux", "openai", "openai/gpt-6.0")
    assert md.label == "GPT-6.0"


def test_update_model_with_slash_id(yaml_path):
    w.update_model(
        "zenmux", "anthropic/claude-sonnet-4.6",
        {"id": "anthropic/claude-sonnet-4.6", "provider": "anthropic", "label": "Renamed Sonnet"},
        path=yaml_path,
    )
    reg = cr.load_from_path(yaml_path)
    _, md = reg.find_model("zenmux", "anthropic", "anthropic/claude-sonnet-4.6")
    assert md.label == "Renamed Sonnet"


def test_delete_model(yaml_path):
    w.delete_model("zenmux", "anthropic/claude-haiku-4.5", path=yaml_path)
    reg = cr.load_from_path(yaml_path)
    with pytest.raises(KeyError):
        reg.find_model("zenmux", "anthropic", "anthropic/claude-haiku-4.5")


def test_invalid_mutation_leaves_file_untouched(yaml_path):
    before = yaml_path.read_bytes()
    with pytest.raises(w.RegistryValidationError):
        # zenmux channel requires provider in {anthropic, openai}
        w.add_model("zenmux", {"id": "x/y", "provider": "deepseek", "label": "Bad"}, path=yaml_path)
    assert yaml_path.read_bytes() == before


def test_add_duplicate_model_id_conflicts(yaml_path):
    with pytest.raises(w.RegistryConflictError):
        w.add_model(
            "zenmux",
            {"id": "anthropic/claude-haiku-4.5", "provider": "anthropic", "label": "dup"},
            path=yaml_path,
        )


def test_concurrent_distinct_writes_both_survive(yaml_path):
    # Lost-update guard: two concurrent writers adding DISTINCT models must both
    # persist. _mutate serializes the full load-modify-write under cr._LOCK, so
    # neither snapshot clobbers the other.
    import threading

    errors: list[Exception] = []

    def add(i: int):
        try:
            w.add_model(
                "zenmux",
                {"id": f"openai/gpt-conc-{i}", "provider": "openai", "label": f"C{i}"},
                path=yaml_path,
            )
        except Exception as exc:  # pragma: no cover - surfaced via assert
            errors.append(exc)

    threads = [threading.Thread(target=add, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    text = yaml_path.read_text()
    assert "openai/gpt-conc-0" in text
    assert "openai/gpt-conc-1" in text  # neither write was lost


def test_add_and_delete_channel(yaml_path):
    w.add_channel(
        {
            "name": "local",
            "label": "Local",
            "type": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "models": [{"id": "llama-3", "provider": "meta", "label": "Llama 3"}],
        },
        path=yaml_path,
    )
    reg = cr.load_from_path(yaml_path)
    assert any(c.name == "local" for c in reg.channels)
    w.delete_channel("local", path=yaml_path)
    reg2 = cr.load_from_path(yaml_path)
    assert not any(c.name == "local" for c in reg2.channels)


def test_delete_channel_holding_default_blocked(yaml_path):
    with pytest.raises(w.RegistryConflictError):
        w.delete_channel("zenmux", path=yaml_path)  # holds the default


def test_set_default(yaml_path):
    # Assert the RAW default block the writer produced. (load_from_path's
    # _resolve_default would fall back to a healthy channel if deepseek has no
    # API key in the test env, so we don't assert the *resolved* selection.)
    import yaml
    w.set_default("deepseek", "deepseek-v4-flash", path=yaml_path)
    raw = yaml.safe_load(yaml_path.read_text())
    assert raw["default"]["channel"] == "deepseek"
    assert raw["default"]["model"] == "deepseek-v4-flash"


def test_set_default_missing_target_conflicts(yaml_path):
    with pytest.raises(w.RegistryConflictError):
        w.set_default("zenmux", "nope/nope", path=yaml_path)


def test_delete_default_model_blocked_even_when_channel_unhealthy(yaml_path, monkeypatch):
    # zenmux becomes unhealthy (env var unset) but deleting its RAW default
    # model must STILL be blocked by the health-independent guard. Read the raw
    # default block (not the resolved selection, which falls back when unhealthy).
    import yaml
    monkeypatch.delenv("ZENMUX_API_KEY", raising=False)
    raw = yaml.safe_load(yaml_path.read_text())
    default_channel = raw["default"]["channel"]
    default_model = raw["default"]["model"]
    with pytest.raises(w.RegistryConflictError):
        w.delete_model(default_channel, default_model, path=yaml_path)


def test_zenmux_channel_requires_anthropic_base_url(yaml_path):
    with pytest.raises(w.RegistryValidationError):
        w.add_channel(
            {
                "name": "z2",
                "label": "Z2",
                "type": "zenmux",
                "base_url": "https://x/v1",
                "models": [{"id": "anthropic/x", "provider": "anthropic", "label": "X"}],
            },
            path=yaml_path,
        )

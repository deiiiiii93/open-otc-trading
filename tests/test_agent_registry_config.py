from app.services.deep_agent import channel_registry as cr
from app.services.deep_agent.model_factory import agent_registry_config


# Source the TRACKED template, never the live `config/agent_channels.yaml`.
# That file is gitignored and per-environment (see CLAUDE.md), and the Model
# Maintenance UI rewrites it at runtime — so asserting on its contents makes
# these tests depend on whichever channel happens to hold the default on this
# machine today. The `.example.yml` is version-controlled and changes only
# deliberately, which is what a fixture needs to be.
_TEMPLATE = cr._REPO_ROOT / "config" / "agent_channels.example.yml"


def test_agent_registry_config_exposes_editable_fields(monkeypatch):
    # Hermetic: pin AGENT_CHANNELS_FILE to the tracked template so a leaked env
    # var from another test can't repoint the source.
    monkeypatch.setenv("AGENT_CHANNELS_FILE", str(_TEMPLATE))
    cr.configure_registry(None)
    reg = cr.load_from_path(cr._yaml_path())
    cfg = agent_registry_config(reg)
    assert "default" in cfg and "channel" in cfg["default"]
    zen = next(c for c in cfg["channels"] if c["name"] == "zenmux")
    assert zen["base_url"]
    assert zen["anthropic_base_url"]
    assert zen["api_key_env"] == "ZENMUX_API_KEY"
    assert "healthy" in zen
    m = zen["models"][0]
    assert {"id", "provider", "label", "tags", "protocol"} <= set(m)


def test_agent_registry_config_reports_declared_default_even_when_unhealthy(monkeypatch):
    # The template's declared default is zenmux/…; with ZENMUX_API_KEY unset the
    # loader RESOLVES the default away to a healthy channel, but the maintenance
    # view must report the DECLARED default (the persisted truth), not the
    # resolved one — else the UI would hide the real default and the agent could
    # switch silently on reload once the key returns.
    monkeypatch.setenv("AGENT_CHANNELS_FILE", str(_TEMPLATE))
    monkeypatch.delenv("ZENMUX_API_KEY", raising=False)
    cr.configure_registry(None)
    reg = cr.load_from_path(cr._yaml_path())
    cfg = agent_registry_config(reg)
    assert cfg["default"]["channel"] == "zenmux"  # declared, not the resolved fallback

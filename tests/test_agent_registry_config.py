from app.services.deep_agent import channel_registry as cr
from app.services.deep_agent.model_factory import agent_registry_config


def test_agent_registry_config_exposes_editable_fields():
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

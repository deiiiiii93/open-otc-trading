from __future__ import annotations

from pathlib import Path


def test_curated_agent_tool_schemas_do_not_expose_product_kwargs():
    from app.tools import QUANT_AGENT_TOOLS

    leaking_tools = []
    for tool in QUANT_AGENT_TOOLS:
        args_schema = getattr(tool, "args_schema", None)
        schema = args_schema.model_json_schema() if args_schema is not None else {}
        if "product_kwargs" in str(schema):
            leaking_tools.append(tool.name)

    assert leaking_tools == []


def test_agent_skills_and_prompts_do_not_instruct_product_kwargs_usage():
    root = Path(__file__).resolve().parents[1]
    scanned_roots = [
        root / "backend" / "app" / "skills",
        root / "backend" / "app" / "services" / "deep_agent" / "prompts",
    ]
    leaks: list[str] = []
    for scanned_root in scanned_roots:
        for path in scanned_root.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            if "product_kwargs" in text:
                leaks.append(str(path.relative_to(root)))

    assert leaks == []

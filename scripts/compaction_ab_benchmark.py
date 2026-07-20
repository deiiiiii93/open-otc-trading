#!/usr/bin/env python3
"""Run the deterministic OTC compaction A/B benchmark."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services.deep_agent.compaction_benchmark import (  # noqa: E402
    render_live_markdown,
    render_markdown,
    run_live_benchmark,
    run_benchmark,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare installed DeepAgents/LangChain default compaction with the "
            "OTC ledger-aware strategy on identical deterministic traces."
        )
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--markdown-out",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run paired calls against a configured real chat model.",
    )
    parser.add_argument("--channel", default="deepseek")
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=1600)
    parser.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env")
    parser.add_argument(
        "--channel-file",
        type=Path,
        default=REPO_ROOT / "config" / "agent_channels.yaml",
    )
    args = parser.parse_args()

    if args.live:
        from dotenv import load_dotenv

        from app.services.deep_agent.channel_registry import load_from_path
        from app.services.deep_agent.model_factory import build_agent_model

        load_dotenv(args.env_file, override=False)
        registry = load_from_path(args.channel_file)
        selection = {
            "channel": args.channel,
            "provider": args.provider,
            "model": args.model,
        }
        model = build_agent_model(registry, selection)
        if model is None:
            raise RuntimeError(
                f"live model channel is unhealthy: {args.channel}:{args.model}"
            )
        model = model.model_copy(
            update={
                "temperature": args.temperature,
                "max_tokens": args.max_tokens,
            }
        )
        result = run_live_benchmark(
            model=model,
            model_metadata={
                **selection,
                "temperature": args.temperature,
                "max_tokens": args.max_tokens,
            },
            trials=args.trials,
        )
        report = render_live_markdown(result)
        safe_model = "".join(
            char if char.isalnum() or char in {"-", "_"} else "-"
            for char in args.model
        )
        json_out = args.json_out or (
            REPO_ROOT / "outputs" / f"compaction_ab_live_{safe_model}.json"
        )
        markdown_out = args.markdown_out or (
            REPO_ROOT / "outputs" / f"compaction_ab_live_{safe_model}.md"
        )
        success = result["live_advantage_demonstrated"]
    else:
        result = run_benchmark()
        report = render_markdown(result)
        json_out = args.json_out or (
            REPO_ROOT / "outputs" / "compaction_ab_benchmark.json"
        )
        markdown_out = args.markdown_out or (
            REPO_ROOT / "outputs" / "compaction_ab_benchmark.md"
        )
        success = result["advantage_demonstrated"]

    json_out.parent.mkdir(parents=True, exist_ok=True)
    markdown_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    markdown_out.write_text(report, encoding="utf-8")

    print(report)
    print(f"\nJSON evidence: {json_out}")
    print(f"Markdown report: {markdown_out}")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Live A/B smoke: does the semantic layer improve real agent behavior?

Drives the REAL desk orchestrator (same seam as the arena's _default_drive)
twice per prompt on the direct DeepSeek flash channel:

  arm A ("with")    - normal toolset, get_product_reference_doc available
  arm B ("without") - the tool withheld from DEEP_AGENT_TOOL_NAMES at
                      AgentService construction time (the pre-layer world)

Prompts probe the two behavioral advantages a pytest cannot display:
  1. inherited-terms - KO-reset variant: does the agent surface the
     INHERITED snowball economics (KI barrier, observation frequency,
     lockup, trade start date) a raw doc read misses?
  2. missing-barrier - sharkfin with no barrier: does the agent FLAG the
     missing required input instead of blessing/inventing it?

Grading is deterministic keyword scoring plus a tool-called check from the
trace store; full replies are dumped for eyeballing.

Usage (repo root; needs DEEPSEEK_API_KEY in .env and tracing local):
    .venv/bin/python scripts/semantic_layer_ab_smoke.py [--model deepseek-v4-flash]

Writes AgentThread/AgentMessage rows tagged source='smoke' to the live DB;
all probe prompts are read-only interpretation questions.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

SELECTION = {"channel": "deepseek", "provider": "deepseek", "model": "deepseek-v4-flash"}

PROMPTS: dict[str, str] = {
    "inherited-terms": (
        "A client wants a KO-reset snowball (QuantArk class "
        "KnockOutResetSnowballOption). So far they have only specified the "
        "post-KI reset leg: post-KI KO barrier 100% and post-KI KO coupon 2%. "
        "Interpret these terms and list EVERY required economic input still "
        "missing before this trade could be priced or booked. This is a "
        "read-only interpretation question - do not book, write, or price "
        "anything."
    ),
    "missing-barrier": (
        "An imported sharkfin row (QuantArk class SingleSharkfinOption) has: "
        "initial_price 100, maturity 1 year, strike 100, participation rate "
        "100%. Interpret the terms and tell me whether this row is complete "
        "enough to price. This is a read-only interpretation question - do "
        "not book, write, or price anything."
    ),
}

# Inherited snowball economics that only the resolver surfaces (the raw
# claiming doc explains 4/11 required keys; these are among the other 7).
INHERITED_MARKERS = (
    "ki barrier",
    "knock-in barrier",
    "observation frequency",
    "observation schedule",
    "lockup",
    "trade start",
    "ko observation",
)
MISSING_LANGUAGE = ("missing", "required", "need", "must provide", "incomplete", "cannot")

# Terms that do NOT exist in the SingleSharkfinOption contract (required:
# initial_price, maturity_years, strike, barrier; defaulted: option_type,
# contract_multiplier, participation_rate). An agent demanding these is
# confabulating another family's structure - the failure mode the semantic
# layer exists to prevent.
SHARKFIN_PHANTOM_TERMS = ("knock-in", "ki barrier", "ki level", "coupon", "rebate")


def _score(probe: str, reply: str) -> dict:
    text = " ".join(reply.lower().split())
    if probe == "inherited-terms":
        hits = sorted({m for m in INHERITED_MARKERS if m in text})
        return {"inherited_terms_named": hits, "score": len(hits)}
    # missing-barrier: must name the barrier AND flag incompleteness, and is
    # penalized for demanding terms the family contract does not contain.
    flags_missing = "barrier" in text and any(w in text for w in MISSING_LANGUAGE)
    phantoms = sorted({p for p in SHARKFIN_PHANTOM_TERMS if p in text})
    return {
        "flags_missing_barrier": flags_missing,
        "phantom_terms_demanded": phantoms,
        "score": int(flags_missing) - len(phantoms),
    }


def _build_service(withhold_tool: bool):
    from app.services import agents as agents_mod

    original = agents_mod.DEEP_AGENT_TOOL_NAMES
    if withhold_tool:
        agents_mod.DEEP_AGENT_TOOL_NAMES = frozenset(
            original - {"get_product_reference_doc"}
        )
    try:
        service = agents_mod.AgentService()
    finally:
        agents_mod.DEEP_AGENT_TOOL_NAMES = original
    return service


def _drive(service, prompt: str, arm: str, probe: str) -> int:
    from app import database
    from app.models import AgentMessage, AgentThread

    with database.SessionLocal() as session:
        thread = AgentThread(
            title=f"[smoke] semantic-layer A/B {probe} ({arm})",
            character="trader",
            source="smoke",
        )
        session.add(thread)
        session.commit()
        thread_id = thread.id
        session.add(
            AgentMessage(
                thread_id=thread_id,
                role="user",
                character=None,
                content=prompt,
                meta={
                    "model_selection": SELECTION,
                    "yolo_mode": True,
                    "confirmed_cost_preview": True,
                },
            )
        )
        session.commit()

    async def _run() -> None:
        async for _chunk in service.stream_and_persist(
            thread_id=thread_id,
            content=prompt,
            model_selection=SELECTION,
            mode="yolo",
            confirmed_cost_preview=True,
        ):
            pass

    asyncio.run(_run())
    return thread_id


def _harvest(thread_id: int) -> tuple[str, bool, list[str]]:
    """Return (concatenated assistant reply, tool_called, all tool calls)."""
    from app import database
    from app.config import get_settings
    from app.models import AgentMessage
    from app.services.tracing.store import get_trace_store

    with database.SessionLocal() as session:
        rows = (
            session.query(AgentMessage)
            .filter(AgentMessage.thread_id == thread_id, AgentMessage.role == "assistant")
            .order_by(AgentMessage.id)
            .all()
        )
        reply = "\n".join(r.content or "" for r in rows)

    store = get_trace_store(get_settings())
    if hasattr(store, "flush"):
        store.flush()
    tool_calls: list[str] = []
    for root in store.list_thread_traces(thread_id, limit=10):
        for span in store.get_trace(root["trace_id"]):
            if span.get("run_type") == "tool":
                tool_calls.append(span.get("name") or "?")
    tool_called = "get_product_reference_doc" in tool_calls
    return reply, tool_called, tool_calls


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=SELECTION["model"])
    parser.add_argument(
        "--out", default=str(REPO_ROOT / "outputs" / "semantic_layer_ab_smoke.json")
    )
    args = parser.parse_args()
    SELECTION["model"] = args.model

    results: dict[str, dict] = {}
    for withhold, arm in ((False, "with"), (True, "without")):
        service = _build_service(withhold_tool=withhold)
        for probe, prompt in PROMPTS.items():
            print(f"=== driving {probe} ({arm} tool) ===", flush=True)
            thread_id = _drive(service, prompt, arm, probe)
            reply, tool_called, tool_calls = _harvest(thread_id)
            grade = _score(probe, reply)
            results.setdefault(probe, {})[arm] = {
                "thread_id": thread_id,
                "tool_called": tool_called,
                "tool_calls": tool_calls,
                **grade,
                "reply": reply,
            }
            print(
                f"    thread={thread_id} tool_called={tool_called} "
                f"grade={ {k: v for k, v in grade.items() if k != 'reply'} }",
                flush=True,
            )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n================ A/B SUMMARY ================")
    advantage_shown = True
    for probe, arms in results.items():
        with_arm, without_arm = arms["with"], arms["without"]
        print(
            f"{probe}: with-tool score={with_arm['score']} "
            f"(tool_called={with_arm['tool_called']}, "
            f"{len(with_arm['tool_calls'])} tool calls) | "
            f"without-tool score={without_arm['score']} "
            f"(tool_called={without_arm['tool_called']}, "
            f"{len(without_arm['tool_calls'])} tool calls)"
        )
        if not with_arm["tool_called"] or with_arm["score"] < without_arm["score"]:
            advantage_shown = False
    print(f"transcripts: {out_path}")
    print("ADVANTAGE DEMONSTRATED" if advantage_shown else "INCONCLUSIVE - read transcripts")
    return 0 if advantage_shown else 1


if __name__ == "__main__":
    raise SystemExit(main())

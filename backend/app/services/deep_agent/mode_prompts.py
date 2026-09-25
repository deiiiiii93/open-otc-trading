"""Per-turn prompt text that depends on the execution mode.

The orchestrator's turn prompt carries a few sentences that tell the model how
to treat the human: the execution-mode block, and the context brief's "nothing
is in view" lines. They used to be written once, for a desk user, and so told a
headless (YOLO) run to "ask the user which portfolio" — a question nobody will
ever answer. Cautious models obeyed it and stalled: gpt-6-luna spent 9 of 9
risk-manager steps asking "did you mean Control Desk Portfolio?" (run #139).

Every such sentence now lives here, keyed by mode, and the callers assemble the
prompt from the mode they were given. Only the sentences about ASKING differ:
what the tools are allowed to do is the HITL interrupt map's business, and none
of this text changes it.

Modes: ``interactive`` and ``auto`` have a desk user present; ``yolo`` is
headless. Persona system prompts are assembled the same way from policy
fragments — see ``personas._resolve_policy_fragments``. The orchestrator's
static system prompt marks its mode-dependent sections with
``<!-- MODE_SECTION:<name> -->``; ``assemble_mode_sections`` fills each from
``prompts/modes/<name>.interactive.md`` or ``<name>.headless.md``.
"""
from __future__ import annotations

import re
from pathlib import Path

INTERACTIVE = "interactive"
AUTO = "auto"
HEADLESS = "yolo"


def turn_mode(mode: str | None, yolo_mode: bool) -> str:
    """The mode a turn prompt is assembled for. Legacy callers pass only the
    ``yolo_mode`` bool, which never meant headless (see
    ``agents.resolve_execution_mode``)."""
    if mode:
        return mode
    return AUTO if yolo_mode else INTERACTIVE


_EXECUTION_MODE = {
    INTERACTIVE: (
        "YOLO mode is OFF. Follow the normal confirmation policy for "
        "write and irreversible tools."
    ),
    AUTO: (
        "YOLO mode is ON. This app is using LangChain's built-in "
        "auto-approval policy for ordinary write tools, so those tools may "
        "run without pausing for confirmation. Irreversible tools still "
        "require explicit user confirmation."
    ),
}
_EXECUTION_MODE[HEADLESS] = _EXECUTION_MODE[AUTO] + (
    "\n\nHeadless run: no user is present, so a clarifying question will never "
    "be answered. Resolve ambiguity yourself — look it up with read tools "
    "(`list_portfolios`, `get_position_summaries`, "
    "`list_pricing_parameter_profiles`, …), take the most likely reading of "
    "the instruction, and state the assumption you made in your final answer."
)

_NO_PORTFOLIO = {
    INTERACTIVE: (
        "No portfolio is in view from this page. Ask the user which portfolio, "
        "position, or underlying they mean before invoking domain tools."
    ),
    HEADLESS: (
        "No portfolio is in view from this page. Take the portfolio, position, "
        "or underlying from the instruction and resolve names with read tools "
        "(`list_portfolios` for a portfolio name). If the instruction leaves it "
        "ambiguous, pick the most likely candidate and say which you picked."
    ),
}
_NO_PORTFOLIO[AUTO] = _NO_PORTFOLIO[INTERACTIVE]

_NO_PROFILE = {
    INTERACTIVE: (
        "No pricing parameter profile is selected. Before proposing "
        "run_batch_pricing or create_report for portfolio/risk "
        "calculations, ask which pricing parameter profile to use unless "
        "the user explicitly says to run without one."
    ),
    HEADLESS: (
        "No pricing parameter profile is selected. For run_batch_pricing or "
        "create_report, use the profile the instruction names (resolve it with "
        "`list_pricing_parameter_profiles`); if it names none, run without one "
        "and say so in your answer."
    ),
}
_NO_PROFILE[AUTO] = _NO_PROFILE[INTERACTIVE]


def execution_mode_text(mode: str) -> str:
    return _EXECUTION_MODE[mode]


def no_portfolio_line(mode: str) -> str:
    return _NO_PORTFOLIO[mode]


def no_profile_line(mode: str) -> str:
    return _NO_PROFILE[mode]


_MODES_DIR = Path(__file__).parent / "prompts" / "modes"
_MODE_SECTION = re.compile(r"<!-- MODE_SECTION:([a-z0-9-]+) -->")


def assemble_mode_sections(text: str, *, headless: bool) -> str:
    """Fill every ``<!-- MODE_SECTION:<name> -->`` marker in *text*.

    A system prompt is built once per agent, and the agent is built per mode,
    so only two variants exist: headless, and with a user present. A marker
    without its file raises — a silently empty section would drop a rule.
    """
    variant = "headless" if headless else "interactive"

    def _fill(match: re.Match[str]) -> str:
        path = _MODES_DIR / f"{match.group(1)}.{variant}.md"
        return path.read_text(encoding="utf-8").rstrip()

    return _MODE_SECTION.sub(_fill, text)

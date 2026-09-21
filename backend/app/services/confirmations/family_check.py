"""Confirmation family cross-check (spec 2026-09-21 §3).

`segment_document` picks each trade's product family with one vision LLM, and
that choice selects the schema stage 2 fills — a wrong family yields a
well-formed, wrong trade that a human then approves for an IRREVERSIBLE booking.
This asks System One the same question from the segment's TEXT LAYER and records
whether it agrees. A flag for the reviewer, never a gate: it touches no
validation status, no validation error, and no bookability.

No module-scope `app.tools` import here (see service.py): callers pass
`_SCHEMA_FAMILIES` in.
"""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import asdict, dataclass
from typing import Any

from ...config import Settings, get_settings
from ..system_one import Choice, SystemOneUnavailable, ask
from .extract import DocumentContent
from .llm import TradeSegment

#: A different real family counts as disagreement only at this confidence.
DISAGREE_MIN = 0.5
#: Reserved option: "none of the listed families fits".
UNKNOWN_OPTION = "unknown"

#: One-line, region-neutral descriptions. A family missing here falls back to
#: its own name, so adding a family never breaks anything.
FAMILY_DESCRIPTIONS: dict[str, str] = {
    "EuropeanVanillaOption": "A plain call or put with one strike, exercisable only at expiry, with no barrier or path feature",
    "AmericanOption": "A call or put that may be exercised on any date up to expiry",
    "BarrierOption": "A call or put that knocks in or knocks out when the underlying touches a single barrier level",
    "AsianOption": "A call or put whose payoff uses an average of the underlying's prices over a set of fixing dates",
    "CashOrNothingDigitalOption": "Pays a fixed cash amount if the underlying finishes beyond the strike, and nothing otherwise",
    "SingleSharkfinOption": "A capped call or put that knocks out beyond one barrier level, often paying a rebate",
    "DoubleSharkfinOption": "A range structure with an upper and a lower knock-out barrier that pays while the underlying stays inside",
    "OneTouchOption": "Pays a fixed amount if the underlying touches a barrier level at any time before expiry",
    "DoubleOneTouchOption": "Pays a fixed amount if the underlying touches either an upper or a lower barrier before expiry",
    "SnowballOption": "An autocallable note: knock-out observations pay an accrued coupon and end the trade early, and a knock-in barrier exposes the principal",
    "KnockOutResetSnowballOption": "A snowball autocallable whose knock-out level steps down on later observation dates",
    "PhoenixOption": "An autocallable that pays periodic coupons while the underlying stays above a coupon barrier, with knock-out and knock-in levels",
    "RangeAccrualOption": "Accrues a coupon for each observation day the underlying stays inside a range",
    "Futures": "An exchange futures contract: an obligation to buy or sell the underlying at an agreed price on a future date",
    "SpotInstrument": "A cash or spot position in the underlying itself, with no option feature",
}

_QUESTION = (
    "Which product family is the trade described in `document_text` (the trade "
    "located by `anchor`)? Choose `unknown` if none of the listed families fits."
)


@dataclass(frozen=True)
class FamilyCheck:
    status: str                            # agree | disagree | unscored
    reason: str | None = None
    jev_family: str | None = None          # null when Jev was never reached
    confidence: float | None = None
    top: list[list[Any]] | None = None     # [[family, p], ...] p DESC, family ASC, 2 dp
    model: str | None = None

    def as_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def unscored(cls, reason: str, *, model: str | None = None) -> FamilyCheck:
        return cls(status="unscored", reason=reason, model=model)


def family_options(schema_families: Collection[str]) -> dict[str, str]:
    if UNKNOWN_OPTION in schema_families:
        raise ValueError(
            "'unknown' is a reserved cross-check option and cannot be a schema family")
    options = {family: FAMILY_DESCRIPTIONS.get(family, family) for family in sorted(schema_families)}
    options[UNKNOWN_OPTION] = "None of the listed product families fits this trade"
    return options


def segment_text(content: DocumentContent, pages: list[int]) -> str | None:
    """The segment's text layer, or None when ANY of its pages is a scan.

    Uses the pipeline's own classification: extract.py sets `image_png` only for
    a page under MIN_TEXT_CHARS_PER_PAGE, so a normal page with a logo is text.
    Jev has no vision, and partial text is never a basis for a guess. An empty
    `pages` list means every page (the same fallback `_content_parts` uses).
    """
    wanted = set(pages) if pages else None
    blocks: list[str] = []
    for page in content.pages:
        if wanted is not None and page.index not in wanted:
            continue
        if page.image_png is not None:
            return None
        if page.text:
            blocks.append(f"[page {page.index}]\n{page.text}")
    return "\n\n".join(blocks)


def decide(
    llm_family: str, choice: str, confidence: float, schema_families: Collection[str]
) -> tuple[str, str | None]:
    """The status decision table (spec §3)."""
    if choice == llm_family:
        return "agree", None
    if choice == UNKNOWN_OPTION:
        # "I can't tell" is not evidence against the LLM's real family.
        if llm_family in schema_families:
            return "unscored", "jev_unknown"
        return "agree", None
    if confidence >= DISAGREE_MIN:
        return "disagree", None
    return "unscored", "low_confidence"


def check_family(
    content: DocumentContent,
    segment: TradeSegment,
    *,
    schema_families: Collection[str],
    post: Any = None,
    settings: Settings | None = None,
) -> FamilyCheck:
    cfg = settings or get_settings()
    options = family_options(schema_families)
    text = segment_text(content, segment.pages)
    if not text:
        return FamilyCheck.unscored("no_text_layer")
    try:
        result = ask(
            {"anchor": segment.anchor, "document_text": text},
            {"family": Choice(instructions=_QUESTION, criteria=options)},
            post=post, settings=cfg,
        )
    except SystemOneUnavailable as exc:
        return FamilyCheck.unscored(exc.reason, model=cfg.system_one_model)
    answer = result.answers["family"]
    ranked = sorted(answer.probabilities.items(), key=lambda item: (-item[1], item[0]))[:3]
    status, reason = decide(segment.family, answer.choice, answer.confidence, schema_families)
    return FamilyCheck(
        status=status, reason=reason, jev_family=answer.choice,
        confidence=answer.confidence,
        top=[[family, round(p, 2)] for family, p in ranked],
        model=result.model,
    )

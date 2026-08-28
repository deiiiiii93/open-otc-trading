"""Generate SYNTHETIC OTC equity-option confirmation documents for testing the
trade-confirmation-to-book module.

Modelled on the structure/vocabulary of an ISDA 2002 Equity Derivatives Definitions
"Confirmation of Share Option Transaction" (docs/confirmations/equity-share-option.pdf),
but every party, LEI, reference and contact detail is FICTIONAL. Underlyings are real
tickers because the desk prices against them.

Outputs -> this directory (backend/app/golden_workflows/documents/), which is
TRACKED: these documents grade the `confirmation-desk-day` arena board, and a
benchmark cannot rest on per-environment files.

Run:  .venv/bin/python backend/app/golden_workflows/documents/make_confirmations.py

Regeneration must reproduce the same CONTENT -- page text and rendered pixels.
A diff means a rendering dependency moved, and every graded constant harvested
from these documents has to be re-verified before any board built on them is
trusted (the rule the exact `quantark==0.3.0` pin exists to enforce).

Two things it is NOT, both measured 2026-08-28:

- NOT byte-identical. PIL's PDF writer stamps `/CreationDate` and python-docx
  writes zip mtimes, so the same content rendered a second apart differs by
  construction. Compare extracted content, never raw bytes.
- NOT repeatable twice in ONE process. Calling build_all() again produces
  different scan pixels even though `random` is re-seeded: something in the
  rendering stack consumes the RNG lazily on first use, so the second pass
  reaches the scan builder at a different stream position. Regeneration happens
  from a fresh interpreter, which is deterministic and is what the guard test
  exercises.
"""
from __future__ import annotations

import io
import random
from pathlib import Path

# Default output = this package directory. Parameterised through build_all() so a
# test can render into tmp_path and diff against what is committed; the previous
# hardcoded absolute path silently wrote into whichever checkout it named.
OUT = Path(__file__).resolve().parent

DEALER = "Ardsley Global Markets, N.A."
DEALER_SHORT = "Ardsley"
DEALER_LEI = "TESTLEI00ARDSLEY0001"
DEALER_PHONE = "212-555-0140"
DEALER_FAX = "1-844-555-0177"
DEALER_EMAIL = "inboundconfirms@ardsley-markets.example"

FOOTNOTE = "SYNTHETIC TEST DOCUMENT - NOT A REAL TRADE CONFIRMATION"

# Fixed so scan degradation is byte-reproducible. build_all() re-seeds from this
# constant, so the value must stay a module-level name rather than a literal.
_SEED = 20260805
random.seed(_SEED)

# --------------------------------------------------------------------------
# Minimal PDF writer (Helvetica / Helvetica-Bold, multi-page, wrapped text)
# --------------------------------------------------------------------------

_W = {
    ' ': 278, '!': 278, '"': 355, '#': 556, '$': 556, '%': 889, '&': 667, "'": 191,
    '(': 333, ')': 333, '*': 389, '+': 584, ',': 278, '-': 333, '.': 278, '/': 278,
    '0': 556, '1': 556, '2': 556, '3': 556, '4': 556, '5': 556, '6': 556, '7': 556,
    '8': 556, '9': 556, ':': 278, ';': 278, '<': 584, '=': 584, '>': 584, '?': 556,
    '@': 1015, 'A': 667, 'B': 667, 'C': 722, 'D': 722, 'E': 667, 'F': 611, 'G': 778,
    'H': 722, 'I': 278, 'J': 500, 'K': 667, 'L': 556, 'M': 833, 'N': 722, 'O': 778,
    'P': 667, 'Q': 778, 'R': 722, 'S': 667, 'T': 611, 'U': 722, 'V': 667, 'W': 944,
    'X': 667, 'Y': 667, 'Z': 611, '[': 278, '\\': 278, ']': 278, '^': 469, '_': 556,
    '`': 333, 'a': 556, 'b': 556, 'c': 500, 'd': 556, 'e': 556, 'f': 278, 'g': 556,
    'h': 556, 'i': 222, 'j': 222, 'k': 500, 'l': 222, 'm': 833, 'n': 556, 'o': 556,
    'p': 556, 'q': 556, 'r': 333, 's': 500, 't': 278, 'u': 556, 'v': 500, 'w': 722,
    'x': 500, 'y': 500, 'z': 500, '{': 334, '|': 260, '}': 334, '~': 584,
}


def _text_width(s: str, size: float, bold: bool = False) -> float:
    scale = 1.06 if bold else 1.0
    return sum(_W.get(ch, 556) for ch in s) / 1000.0 * size * scale


def _wrap(s: str, width: float, size: float, bold: bool = False) -> list[str]:
    words, lines, cur = s.split(), [], ""
    for word in words:
        cand = f"{cur} {word}".strip()
        if cur and _text_width(cand, size, bold) > width:
            lines.append(cur)
            cur = word
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines or [""]


def _esc(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


class Pdf:
    """Page-oriented PDF builder with a label/value 'confirmation grid' layout."""

    WIDTH, HEIGHT = 612.0, 792.0
    LEFT, RIGHT = 72.0, 540.0
    LABEL_X, VALUE_X = 100.0, 300.0
    TOP, BOTTOM = 720.0, 90.0
    LEAD = 13.0

    def __init__(self, ref: str):
        self.ref = ref
        self._pages: list[list[str]] = []
        self._ops: list[str] = []
        self.y = self.TOP
        self._start_page()

    # -- page plumbing ----------------------------------------------------
    def _start_page(self) -> None:
        self._ops = []
        self.y = self.TOP
        n = len(self._pages) + 1
        self._raw(f"BT /F1 9 Tf 1 0 0 1 {self.RIGHT - 40:.2f} 742 Tm ({n}/[N]) Tj ET")
        foot = f"{DEALER_SHORT} Ref. No: {self.ref}"
        fx = (self.WIDTH - _text_width(foot, 8.5)) / 2
        self._raw(f"BT /F1 8.5 Tf 1 0 0 1 {fx:.2f} 62 Tm ({_esc(foot)}) Tj ET")
        tiny_x = (self.WIDTH - _text_width(FOOTNOTE, 6.5)) / 2
        self._raw(f"0.55 0.55 0.55 rg BT /F1 6.5 Tf 1 0 0 1 {tiny_x:.2f} 50 Tm "
                  f"({_esc(FOOTNOTE)}) Tj ET 0 0 0 rg")

    def _raw(self, op: str) -> None:
        self._ops.append(op)

    def page_break(self) -> None:
        self._pages.append(self._ops)
        self._start_page()

    def _space(self, need: float) -> None:
        if self.y - need < self.BOTTOM:
            self.page_break()

    # -- drawing primitives ----------------------------------------------
    def _put(self, x: float, y: float, s: str, size: float, bold: bool) -> None:
        font = "F2" if bold else "F1"
        self._raw(f"BT /{font} {size} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm ({_esc(s)}) Tj ET")

    def rule(self, x1: float, y: float, x2: float, w: float = 0.6) -> None:
        self._raw(f"{w} w {x1:.2f} {y:.2f} m {x2:.2f} {y:.2f} l S")

    def logo(self) -> None:
        """A plain dealer wordmark block (no third-party branding)."""
        self._raw(f"0.16 0.24 0.42 rg 432 690 108 44 re f 0 0 0 rg")
        self._raw("1 1 1 rg BT /F2 13 Tf 1 0 0 1 448 718 Tm (ARDSLEY) Tj ET")
        self._raw("BT /F1 7.5 Tf 1 0 0 1 448 704 Tm (GLOBAL MARKETS) Tj ET 0 0 0 rg")
        self.y = 670.0

    # -- document elements -------------------------------------------------
    def title(self, s: str) -> None:
        self._space(30)
        x = (self.WIDTH - _text_width(s, 11.5, bold=True)) / 2
        self._put(x, self.y, s, 11.5, True)
        self.y -= 26

    def heading(self, s: str, underline: bool = True) -> None:
        self._space(26)
        self.y -= 6
        self._put(self.LEFT, self.y, s, 9.5, True)
        if underline:
            self.rule(self.LEFT, self.y - 2.5,
                      self.LEFT + _text_width(s, 9.5, True), 0.5)
        self.y -= self.LEAD + 3

    def para(self, s: str, size: float = 9.0, gap: float = 8.0,
             indent: float = 0.0) -> None:
        x = self.LEFT + indent
        for line in _wrap(s, self.RIGHT - x, size):
            self._space(self.LEAD)
            self._put(x, self.y, line, size, False)
            self.y -= self.LEAD
        self.y -= gap

    def field(self, label: str, value: str, label_x: float | None = None,
              bold_value: bool = False) -> None:
        """One `Label:  Value` row with the value column wrapped."""
        lx = self.LABEL_X if label_x is None else label_x
        lines = _wrap(value, self.RIGHT - self.VALUE_X, 9.0, bold_value)
        self._space(self.LEAD * len(lines) + 4)
        self._put(lx, self.y, label, 9.0, True)
        for i, line in enumerate(lines):
            if i:
                self.y -= self.LEAD
            self._put(self.VALUE_X, self.y, line, 9.0, bold_value)
        self.y -= self.LEAD + 3

    def gap(self, n: float = 10.0) -> None:
        self.y -= n

    # -- output ------------------------------------------------------------
    def build(self) -> bytes:
        self._pages.append(self._ops)
        total = len(self._pages)
        streams = ["\n".join(ops).replace("[N]", f"[{total}]") for ops in self._pages]

        objs: list[bytes] = []

        def add(body: bytes) -> int:
            objs.append(body)
            return len(objs)

        # 1 catalog, 2 pages tree — reserve their numbers first.
        objs.append(b"")  # 1
        objs.append(b"")  # 2
        font_regular = add(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica"
                           b"/Encoding/WinAnsiEncoding>>")
        font_bold = add(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica-Bold"
                        b"/Encoding/WinAnsiEncoding>>")

        page_ids: list[int] = []
        for stream in streams:
            data = stream.encode("latin-1", "replace")
            content_id = add(b"<</Length %d>>\nstream\n%s\nendstream"
                             % (len(data), data))
            page_ids.append(add(
                b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
                b"/Contents %d 0 R/Resources<</Font<</F1 %d 0 R/F2 %d 0 R>>>>>>"
                % (content_id, font_regular, font_bold)))

        objs[0] = b"<</Type/Catalog/Pages 2 0 R>>"
        kids = " ".join(f"{pid} 0 R" for pid in page_ids)
        objs[1] = (b"<</Type/Pages/Kids[%s]/Count %d>>"
                   % (kids.encode("ascii"), len(page_ids)))

        out = io.BytesIO()
        out.write(b"%PDF-1.4\n")
        offsets = []
        for i, body in enumerate(objs, start=1):
            offsets.append(out.tell())
            out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
        xref = out.tell()
        out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
        for off in offsets:
            out.write(b"%010d 00000 n \n" % off)
        out.write(b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n"
                  % (len(objs) + 1, xref))
        return out.getvalue()

    def save(self, path: Path) -> None:
        path.write_bytes(self.build())


# --------------------------------------------------------------------------
# Shared confirmation prose
# --------------------------------------------------------------------------

INTRO = (
    "This confirms the terms and conditions of the Transaction described below entered "
    "into between Counterparty and {dealer} on the Trade Date specified below (the "
    "\"Transaction\") and constitutes a \"Confirmation\" as referred to in the ISDA "
    "Master Agreement specified below."
)
DEFS = (
    "The definitions and provisions contained in the 2002 ISDA Equity Derivatives "
    "Definitions (the \"Equity Definitions\"), as published by the International Swaps "
    "and Derivatives Association, Inc. (\"ISDA\") are incorporated into, and subject to "
    "this Confirmation. In the event of any inconsistency between the Equity Definitions "
    "and this Confirmation, this Confirmation will prevail."
)
SUPPLEMENT = (
    "This Confirmation supplements, forms part of, and is subject to, the ISDA Master "
    "Agreement between {dealer} and Counterparty dated as of March 14, 2024 as amended "
    "and supplemented from time to time (the \"ISDA Master Agreement\"). All provisions "
    "contained or incorporated by reference in the ISDA Master Agreement will govern "
    "this Confirmation except as expressly modified herein."
)


def cover(pdf: Pdf, cp: str, cp_lei: str, cp_attn: str, cp_email: str,
          ref: str, date: str) -> None:
    pdf.logo()
    pdf.title("CONFIRMATION OF SHARE OPTION TRANSACTION")
    pdf.field("To:", f"{cp} (\"Counterparty\")", label_x=Pdf.LEFT)
    pdf.field("Legal Entity Identifier (LEI):", cp_lei)
    pdf.field("Attention:", cp_attn)
    pdf.field("Fax:", "1-800-555-0198")
    pdf.field("Email:", cp_email)
    pdf.gap(4)
    pdf.field("From:", f"{DEALER} (\"{DEALER_SHORT}\")", label_x=Pdf.LEFT)
    pdf.field("Legal Entity Identifier (LEI):", DEALER_LEI)
    pdf.field("Phone:", DEALER_PHONE)
    pdf.field("Fax:", DEALER_FAX)
    pdf.field("Email:", DEALER_EMAIL)
    pdf.gap(4)
    pdf.field(f"{DEALER_SHORT} Ref. No:", ref, label_x=Pdf.LEFT, bold_value=True)
    pdf.field("Date:", date, label_x=Pdf.LEFT)
    pdf.gap(6)
    pdf.para("Dear Sir or Madam:")
    pdf.para(INTRO.format(dealer=DEALER_SHORT))
    pdf.para(DEFS)
    pdf.para(SUPPLEMENT.format(dealer=DEALER_SHORT))
    pdf.para("The terms of the Transaction to which this Confirmation relates are as "
             "follows:")


def exercise_block(pdf: Pdf, style: str, expiration: str) -> None:
    pdf.heading("Procedures for Exercise:")
    pdf.field("Commencement Date:", "The Trade Date")
    pdf.field("Expiration Date:", f"{expiration}; provided, however, that to the extent a "
                                  "Designated Contract exists on the Related Exchange and "
                                  "its expiry date is postponed, the Expiration Date shall "
                                  "be postponed to the same Exchange Business Day.")
    if style == "American":
        pdf.field("Multiple Exercise:", "Applicable")
        pdf.field("Minimum Number of Options:", "One")
        pdf.field("Maximum Number of Options:", "The Number of Options remaining")
    pdf.field("Latest Exercise Time:", "If Counterparty is the Buyer of the relevant "
                                       "Option, two hours prior to the Valuation Time on "
                                       "the relevant Exercise Date.")
    pdf.field("Automatic Exercise:", "Applicable")
    pdf.field("Reference Price:", "Means the official closing price per Share as reported "
                                  "or disseminated by the Exchange as of the Valuation "
                                  "Time on the Expiration Date.")


def settlement_block(pdf: Pdf, currency: str = "US Dollars") -> None:
    pdf.heading("Settlement Terms:")
    pdf.field("Settlement Method:", "Physical Settlement")
    pdf.field("Settlement Method Election:", "Not Applicable")
    pdf.field("Settlement Currency:", currency)
    pdf.heading("Share Adjustments:")
    pdf.field("Method of Adjustment:", "Options Exchange Adjustment")
    pdf.field("Option Exchange:", "The Related Exchange")


def boilerplate_tail(pdf: Pdf, cp: str) -> None:
    pdf.heading("Extraordinary Events:")
    pdf.field("Consequences of Merger Events:", "Options Exchange Adjustment for "
                                                "Share-for-Share, Share-for-Other and "
                                                "Share-for-Combined.")
    pdf.field("Tender Offer:", "Applicable")
    pdf.field("Nationalization, Insolvency or Delisting:",
              "Options Exchange Adjustment, provided that if a Designated Contract "
              "Disruption has occurred the consequence shall be Cancellation and Payment "
              "(Calculation Agent Determination).")
    pdf.field("Change in Law:", "Applicable")
    pdf.field("Insolvency Filing:", "Applicable")
    pdf.field("Failure to Deliver:", "Applicable")
    pdf.field("Hedging Disruption:", "Applicable")
    pdf.heading("Calculation Agent:")
    pdf.para(f"{DEALER_SHORT}, whose determinations and calculations shall be made in "
             "good faith and in a commercially reasonable manner.", indent=28)
    pdf.heading("Account Details:")
    pdf.field("Payments to " + DEALER_SHORT + ":",
              "Ardsley Global Markets, N.A., New York; ABA 000000000; "
              "Account 4417-889201; Ref: as above.")
    pdf.field("Payments to Counterparty:", "As separately notified in writing by "
                                           "Counterparty at least two Currency Business "
                                           "Days prior to the relevant payment date.")
    pdf.gap(6)
    pdf.para("Please confirm that the foregoing correctly sets forth the terms of our "
             "agreement by executing this Confirmation and returning it to us by "
             f"facsimile to {DEALER_FAX} or by email to {DEALER_EMAIL}.")
    pdf.gap(10)
    pdf.para(f"Yours faithfully,")
    pdf.gap(6)
    pdf.para(f"{DEALER}")
    pdf.gap(20)
    pdf.field("By:", "___________________________", label_x=Pdf.LEFT)
    pdf.field("Name:", "A. Whitcombe", label_x=Pdf.LEFT)
    pdf.field("Title:", "Authorized Signatory", label_x=Pdf.LEFT)
    pdf.gap(16)
    pdf.para("Confirmed as of the date first above written:")
    pdf.gap(6)
    pdf.para(cp)
    pdf.gap(20)
    pdf.field("By:", "___________________________", label_x=Pdf.LEFT)
    pdf.field("Name:", "___________________________", label_x=Pdf.LEFT)
    pdf.field("Title:", "___________________________", label_x=Pdf.LEFT)


# --------------------------------------------------------------------------
# General-terms block, shared by every vanilla/barrier/asian confirmation
# --------------------------------------------------------------------------

def general_terms(pdf: Pdf, t: dict, *, heading: bool = True) -> None:
    if heading:
        pdf.heading("1. General Terms:")
    pdf.field("Trade Date:", t["trade_date_long"])
    pdf.field("Option Style:", t["style"])
    pdf.field("Option Type:", t["option_type"])
    pdf.field("Seller:", t.get("seller", DEALER_SHORT))
    pdf.field("Buyer:", t.get("buyer", "Counterparty"))
    pdf.field("Shares:", f"{t['issuer']} (Ticker: {t['ticker']})")
    pdf.field("Number of Options:", t["num_options"])
    pdf.field("Option Entitlement:", t["entitlement"])
    pdf.field("Strike Price:", f"USD {t['strike']}")
    if t.get("initial_price"):
        pdf.field("Initial Price:", f"USD {t['initial_price']}")
    if t.get("barrier"):
        pdf.field("Barrier Event:", t["barrier_event"])
        pdf.field("Barrier Price:", f"USD {t['barrier']}")
        pdf.field("Barrier Observation:", "Continuous, from and including the Trade Date "
                                          "to and including the Expiration Date.")
        pdf.field("Rebate:", t.get("rebate", "None"))
    if t.get("averaging"):
        pdf.field("Averaging Dates:", t["averaging"])
        pdf.field("Term:", t["term"])
    pdf.field("Premium:", f"USD {t['premium']} per Option")
    pdf.field("Premium Payment Date:", "Three (3) Currency Business Days following the "
                                       "Trade Date")
    pdf.field("Exchange:", t["exchange"])
    pdf.field("Related Exchanges:", "Chicago Board Options Exchange")


def build_confirmation(t: dict) -> Pdf:
    pdf = Pdf(t["ref"])
    cover(pdf, t["cp"], t["cp_lei"], t["cp_attn"], t["cp_email"], t["ref"],
          t["date_long"])
    pdf.page_break()
    general_terms(pdf, t)
    exercise_block(pdf, t["style"], t["expiration_long"])
    settlement_block(pdf)
    boilerplate_tail(pdf, t["cp"])
    return pdf


# --------------------------------------------------------------------------
# Scan rendering (image-only pages -> vision path)
# --------------------------------------------------------------------------

def _scan_font(size: int):
    from PIL import ImageFont
    for candidate in ("/System/Library/Fonts/Supplemental/Arial.ttf",
                      "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
                      "/Library/Fonts/Arial.ttf"):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def render_scan(lines: list[tuple[str, bool]], *, width: int = 1275,
                height: int = 1650, annotate=None):
    """Render text as a slightly-degraded grayscale page image (a 'scan').

    ``annotate`` is called with ``(draw, fonts, ypos)`` AFTER the text is laid out
    but BEFORE the degradation pass, so anything it draws is skewed, blurred and
    grained exactly like the printed text -- an annotation added afterwards would
    sit crisply on a degraded page and be trivially separable.

    ``ypos`` maps the leading substring of each rendered line to its baseline y,
    so an annotation can be anchored to a real line ("Strike Price:") instead of
    a magic constant that silently drifts when a line is added above it.
    """
    from PIL import Image, ImageDraw, ImageFilter

    img = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(img)
    regular, bold = _scan_font(26), _scan_font(27)
    y = 150
    ypos: dict[str, int] = {}
    for text, is_bold in lines:
        if text == "":
            y += 22
            continue
        draw.text((150, y), text, font=(bold if is_bold else regular), fill=25)
        ypos.setdefault(text.split(":")[0].strip(), y)
        y += 40
    if annotate is not None:
        annotate(draw, {"regular": regular, "bold": bold}, ypos)
    # Degrade like an office scan: slight skew, soft optics, paper grain and a
    # faintly grey (not pure-white) background. Still readable, but the model
    # genuinely has to OCR it rather than read a crisp render.
    img = img.rotate(-0.9, resample=Image.BICUBIC, fillcolor=248)
    img = img.filter(ImageFilter.GaussianBlur(0.7))
    grain = Image.effect_noise((width, height), 14)
    img = Image.blend(img, grain, 0.11)
    return img.point(lambda v: max(0, min(255, int(18 + v * 0.90))))


def save_scan_pdf(images: list, path: Path) -> None:
    first, rest = images[0].convert("RGB"), [i.convert("RGB") for i in images[1:]]
    first.save(str(path), "PDF", resolution=150.0, save_all=bool(rest),
               append_images=rest)


def scan_lines(t: dict, *, header: bool = True) -> list[tuple[str, bool]]:
    out: list[tuple[str, bool]] = []
    if header:
        out += [("ARDSLEY GLOBAL MARKETS, N.A.", True), ("", False),
                ("CONFIRMATION OF SHARE OPTION TRANSACTION", True), ("", False),
                (f"Ardsley Ref. No:      {t['ref']}", False),
                (f"To:                   {t['cp']}", False),
                (f"Date:                 {t['date_long']}", False), ("", False),
                ("1. General Terms:", True), ("", False)]
    out += [
        (f"Trade Date:           {t['trade_date_long']}", False),
        (f"Option Style:         {t['style']}", False),
        (f"Option Type:          {t['option_type']}", False),
        (f"Seller:               {t.get('seller', DEALER_SHORT)}", False),
        (f"Buyer:                {t.get('buyer', 'Counterparty')}", False),
        (f"Shares:               {t['issuer']} (Ticker: {t['ticker']})", False),
        (f"Number of Options:    {t['num_options']}", False),
        (f"Option Entitlement:   {t['entitlement']}", False),
        (f"Strike Price:         USD {t['strike']}", False),
    ]
    if t.get("initial_price"):
        out.append((f"Initial Price:        USD {t['initial_price']}", False))
    out += [
        (f"Premium:              USD {t['premium']} per Option", False),
        (f"Expiration Date:      {t['expiration_long']}", False),
        (f"Exchange:             {t['exchange']}", False),
        ("", False),
        ("Settlement Terms:", True),
        ("", False),
        ("Settlement Method:    Physical Settlement", False),
        ("Settlement Currency:  US Dollars", False),
        ("Calculation Agent:    Ardsley", False),
        ("", False),
        ("Confirmed as of the date first above written.", False),
    ]
    return out


# --------------------------------------------------------------------------
# The trades
# --------------------------------------------------------------------------

KESTREL = dict(cp="Kestrel Capital Partners LLC", cp_lei="TESTLEI00KESTREL0001",
               cp_attn="Derivatives Operations", cp_email="confirms@kestrel-cap.example")
BLUEHARBOR = dict(cp="Blue Harbor Asset Management Ltd", cp_lei="TESTLEI00BLUEHRBR001",
                  cp_attn="Middle Office", cp_email="ops@blueharbor-am.example")
SABLEFISH = dict(cp="Sablefish Investments Pte Ltd", cp_lei="TESTLEI00SABLEFSH001",
                 cp_attn="Trade Support", cp_email="tradesupport@sablefish.example")
LARKSPUR = dict(cp="Larkspur Pension Trust", cp_lei="TESTLEI00LARKSPUR001",
                cp_attn="Investment Operations", cp_email="ops@larkspur-trust.example")

NASDAQ = "The NASDAQ Global Select Market"
NYSE = "New York Stock Exchange"

C1 = {**KESTREL,
      "ref": "ARD-EQO-2026-04417", "date_long": "July 28, 2026",
      "trade_date_long": "July 28, 2026", "style": "American", "option_type": "Call",
      "seller": DEALER_SHORT, "buyer": "Counterparty",
      "issuer": "Apple Inc.", "ticker": "AAPL", "num_options": "1,200",
      "entitlement": "1 Share per Option", "strike": "232.50",
      "initial_price": "228.40", "premium": "14.75",
      "expiration_long": "July 30, 2027", "exchange": NASDAQ}

C2 = {**BLUEHARBOR,
      "ref": "ARD-EQO-2026-04502", "date_long": "August 3, 2026",
      "trade_date_long": "August 3, 2026", "style": "European", "option_type": "Put",
      "seller": DEALER_SHORT, "buyer": "Counterparty",
      "issuer": "Microsoft Corporation", "ticker": "MSFT", "num_options": "800",
      "entitlement": "1 Share per Option", "strike": "505.00",
      "initial_price": "512.30", "premium": "21.40",
      "expiration_long": "February 19, 2027", "exchange": NASDAQ}

# Multi-trade document (one file, three Transactions).
C3A = {**SABLEFISH,
       "ref": "ARD-EQO-2026-04610", "date_long": "August 4, 2026",
       "trade_date_long": "August 4, 2026", "style": "European", "option_type": "Call",
       "issuer": "NVIDIA Corporation", "ticker": "NVDA", "num_options": "2,500",
       "entitlement": "1 Share per Option", "strike": "178.00",
       "initial_price": "172.85", "premium": "11.20",
       "expiration_long": "December 18, 2026", "exchange": NASDAQ}
C3B = {**SABLEFISH,
       "ref": "ARD-EQO-2026-04611", "date_long": "August 4, 2026",
       "trade_date_long": "August 4, 2026", "style": "European", "option_type": "Put",
       "issuer": "Amazon.com, Inc.", "ticker": "AMZN", "num_options": "1,500",
       "entitlement": "1 Share per Option", "strike": "205.00",
       "initial_price": "214.60", "premium": "9.85",
       "expiration_long": "January 15, 2027", "exchange": NASDAQ}
C3C = {**SABLEFISH,
       "ref": "ARD-EQO-2026-04612", "date_long": "August 4, 2026",
       "trade_date_long": "August 4, 2026", "style": "European", "option_type": "Call",
       "issuer": "Tesla, Inc.", "ticker": "TSLA", "num_options": "1,000",
       "entitlement": "1 Share per Option", "strike": "305.00",
       "initial_price": "298.75", "premium": "8.40",
       "barrier": "240.00", "barrier_event": "Knock-out. Down-and-Out.",
       "rebate": "None",
       "expiration_long": "March 19, 2027", "exchange": NASDAQ}

C4 = {**LARKSPUR,
      "ref": "ARD-EQO-2026-04688", "date_long": "August 4, 2026",
      "trade_date_long": "August 4, 2026", "style": "European", "option_type": "Call",
      "issuer": "Alphabet Inc. Class A", "ticker": "GOOGL", "num_options": "3,000",
      "entitlement": "1 Share per Option", "strike": "205.00",
      "initial_price": "199.10", "premium": "12.65",
      "expiration_long": "June 18, 2027", "exchange": NASDAQ}

C5 = {**KESTREL,
      "ref": "ARD-EQO-2026-04701", "date_long": "August 4, 2026",
      "trade_date_long": "August 4, 2026", "style": "European", "option_type": "Call",
      "issuer": "Tesla, Inc.", "ticker": "TSLA", "num_options": "750",
      "entitlement": "100 Shares per Option", "strike": "330.00",
      "initial_price": "321.40", "premium": "9.15",
      "barrier": "420.00", "barrier_event": "Knock-out. Up-and-Out.",
      "rebate": "USD 2.50 per Option, payable if a Knock-out Event occurs.",
      "expiration_long": "May 21, 2027", "exchange": NASDAQ}

C6 = {**BLUEHARBOR,
      "ref": "ARD-EQO-2026-04733", "date_long": "August 4, 2026",
      "trade_date_long": "August 4, 2026", "style": "European", "option_type": "Call",
      "issuer": "SPDR S&P 500 ETF Trust", "ticker": "SPY", "num_options": "2,000",
      "entitlement": "1 Share per Option", "strike": "640.00",
      "initial_price": "631.25", "premium": "18.90",
      "averaging": "The last Exchange Business Day of each calendar month from and "
                   "including September 2026 to and including August 2027 (monthly "
                   "averaging). The Settlement Price shall be the arithmetic mean of "
                   "the Reference Prices on each Averaging Date.",
      "term": "1.0 years from the Trade Date",
      "expiration_long": "August 4, 2027", "exchange": NYSE}

# Deliberately omits Initial Price -> must fail the deterministic gate.
C7 = {**SABLEFISH,
      "ref": "ARD-EQO-2026-04750", "date_long": "August 4, 2026",
      "trade_date_long": "August 4, 2026", "style": "European", "option_type": "Call",
      "issuer": "Meta Platforms, Inc.", "ticker": "META", "num_options": "400",
      "entitlement": "1 Share per Option", "strike": "780.00", "premium": "32.50",
      "expiration_long": "April 16, 2027", "exchange": NASDAQ}

C8 = {**LARKSPUR,
      "ref": "ARD-EQO-2026-04781", "date_long": "August 4, 2026",
      "trade_date_long": "August 4, 2026", "style": "European", "option_type": "Put",
      "issuer": "Advanced Micro Devices, Inc.", "ticker": "AMD", "num_options": "1,800",
      "entitlement": "1 Share per Option", "strike": "185.00",
      "initial_price": "178.90", "premium": "10.35",
      "expiration_long": "February 19, 2027", "exchange": NASDAQ}


# --------------------------------------------------------------------------
# Arena VISION TRAPS (conf-09..conf-11), added 2026-08-28
# --------------------------------------------------------------------------
# Each renders IMAGE-ONLY and hides its graded value where only sight reaches it.
# Every graded value sits far from every DECOY in its own document, so a misread
# fails rather than coincidentally passing within rel_tol.
#
# MEASURED OUTCOME, 2026-08-28 -- READ THIS BEFORE CALLING THEM DISCRIMINATORS.
# All four board contestants (gpt-5.6-luna, glm-5.3-flash, gemini-3.7-flash,
# deepseek-v4-flash-vision-exp) read EVERY trap correctly, twice over:
#
#   pointed single-shot question   FLOOR 4/4  amended 4/4  ticked 4/4  faint 4/4
#   real two-stage pipeline        amended 4/4  ticked 3/4*  faint 4/4
#   (* the one miss was a 529 provider overload, not a misread)
#
# On the amended-strike document every model returned 1,045.00 and none returned
# the struck-through 780.00. So OCR-level vision is SATURATED across this tier:
# these documents do not separate the field on sight, and any check graded purely
# on reading them will land N/N and carry no ability signal.
#
# That is a finding about the field, not a defect in the fixtures -- but it means
# the board's discrimination has to come from the AGENTIC steps (repair, booking
# restraint, the conf-07 absence trap, and EFF), not from the pixels. Publish the
# per-check tally alongside any board built on these, or a saturated axis will
# read as a difficulty claim it cannot support.

BRIGHTWATER = dict(cp="Brightwater Capital Partners LLC",
                   cp_lei="TESTLEI00BRIGHTWTR01", cp_attn="Confirmations Desk",
                   cp_email="confirms@brightwater-cap.example")
KESTREL_SP = dict(cp="Kestrel Structured Products S.A.",
                  cp_lei="TESTLEI00KESTRELSP01", cp_attn="Trade Documentation",
                  cp_email="docs@kestrel-sp.example")
HALDEN = dict(cp="Halden Renshaw Securities Ltd", cp_lei="TESTLEI00HALDENRW001",
              cp_attn="Operations", cp_email="ops@halden-renshaw.example")

# conf-09 -- a PRINTED strike struck through, the amended value inked in the
# margin with initials. Grades reading a CORRECTION, not just locating a field.
# The superseded 780.00 stays on the page as the decoy: a model that reads the
# field without noticing the strike-through returns it and must fail.
C9 = {**BRIGHTWATER,
      "ref": "ARD-EQO-2026-04901", "date_long": "August 24, 2026",
      "trade_date_long": "August 24, 2026", "style": "European",
      "option_type": "Call",
      "issuer": "NVIDIA Corporation", "ticker": "NVDA", "num_options": "2,000",
      "entitlement": "1 Share per Option", "strike": "780.00",
      "initial_price": "902.10", "premium": "61.40",
      "expiration_long": "March 19, 2027", "exchange": NASDAQ,
      # Ink-only, never printed into the text lines.
      #
      # 1,045.00 rather than the first draft's 917.50, which sat 1.7% from the
      # initial_price decoy (902.10) on the same page -- inside rel_tol, so a
      # model returning the INITIAL PRICE instead of the strike would have
      # passed on the wrong number. Now 13.7% from the initial price and 25%
      # from the superseded strike. Caught by the decoy-separation guard, not by
      # eye: the page reads correctly to a human either way.
      "amended_strike": "1,045.00", "amend_initials": "R.McK."}

# conf-10 -- barrier direction carried ONLY by which checkbox is ticked. Both
# labels are printed, so there is no textual fallback and no lexical hint; the
# ink is the entire signal.
C10 = {**KESTREL_SP,
       "ref": "ARD-EQO-2026-04902", "date_long": "August 25, 2026",
       "trade_date_long": "August 25, 2026", "style": "European",
       "option_type": "Put",
       "issuer": "Amazon.com, Inc.", "ticker": "AMZN", "num_options": "1,500",
       "entitlement": "1 Share per Option", "strike": "214.00",
       "initial_price": "221.75", "premium": "8.35",
       "expiration_long": "January 15, 2027", "exchange": NASDAQ,
       "barrier": "171.20", "barrier_type": "DOWN_OUT"}

# conf-11 -- the notional in a LOW-CONTRAST column beside a decoy of similar
# magnitude. Grades precision under degradation: both numbers are legible, only
# one is the notional, and the wrong one is the easier read.
C11 = {**HALDEN,
       "ref": "ARD-EQO-2026-04903", "date_long": "August 26, 2026",
       "trade_date_long": "August 26, 2026", "style": "European",
       "option_type": "Call",
       "issuer": "Oracle Corporation", "ticker": "ORCL", "num_options": "4,000",
       "entitlement": "1 Share per Option", "strike": "163.50",
       "initial_price": "158.90", "premium": "12.05",
       "expiration_long": "December 18, 2026", "exchange": NYSE,
       "notional": "636,000.00", "decoy_collateral": "418,750.00"}


# --------------------------------------------------------------------------
# DOCX
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Vision-trap annotations (drawn pre-degradation, so they scan like the text)
# --------------------------------------------------------------------------
# Fill levels are chosen against render_scan's final transfer function,
# `v -> 18 + v*0.90`: printed text at 25 lands near 40, paper at 255 lands near
# 248. A "faint" fill of 150 lands near 153 -- clearly lighter than the body
# text, still well separated from the paper. Legible-but-degraded is the target;
# an illegible trap is UNWINNABLE (0/N) and carries no more signal than one
# everybody passes.
_INK = 40          # a pen, slightly lighter than the 25 of printed toner
_FAINT = 150       # a tired toner cartridge / a lighter column
_NORMAL = 25


def _amend_strike(t: dict):
    """Strike through the printed strike and ink the amended value beside it."""
    label = "Strike Price:         "
    line = f"{label}USD {t['strike']}"

    def _draw(draw, fonts, ypos):
        y = ypos.get("Strike Price")
        if y is None:                      # layout changed -> fail loudly
            raise RuntimeError("no 'Strike Price' line to annotate")
        # MEASURE the printed value rather than guessing at pixel columns: a
        # hardcoded span drifts silently the moment a label or font changes, and
        # a stroke that misses the number is not a correction at all.
        font = fonts["regular"]
        x0 = 150 + font.getlength(label)
        x1 = 150 + font.getlength(line)
        draw.line((x0 - 6, y + 20, x1 + 6, y + 20), fill=_INK, width=4)
        draw.text((x1 + 70, y - 4), f"USD {t['amended_strike']}",
                  font=font, fill=_INK)
        draw.text((x1 + 70, y + 34), f"amended {t['amend_initials']}",
                  font=_scan_font(20), fill=_INK)
    return _draw


def _tick_barrier(t: dict):
    """Two LABELLED options, one ticked. The ink is the ENTIRE signal.

    Both labels are drawn, because a box without a label is not a hard trap --
    it is an UNWINNABLE one, and an unwinnable check carries no more ability
    signal than a saturated one while still occupying the denominator.
    """
    up_first = t["barrier_type"] == "UP_OUT"

    def _draw(draw, fonts, ypos):
        y = ypos.get("Barrier Direction")
        if y is None:
            raise RuntimeError("no 'Barrier Direction' line to annotate")
        for idx, label in enumerate(("Up-and-Out", "Down-and-Out")):
            by = y + 48 + idx * 52
            draw.rectangle((470, by, 500, by + 30), outline=_NORMAL, width=3)
            draw.text((530, by - 2), label, font=fonts["regular"], fill=_NORMAL)
            ticked = (idx == 0) if up_first else (idx == 1)
            if ticked:
                # A hand tick, not a filled box: a filled rectangle is a
                # trivially detectable blob, a stroke has to be SEEN.
                draw.line((474, by + 16, 483, by + 26), fill=_INK, width=5)
                draw.line((483, by + 26, 497, by + 4), fill=_INK, width=5)
    return _draw


def _faint_amounts(t: dict):
    """A low-contrast notional beside an ordinary-contrast decoy."""
    def _draw(draw, fonts, ypos):
        # Anchors are keyed on the text BEFORE the first colon, so a numbered
        # heading keeps its number.
        y = ypos.get("3. Schedule of Amounts")
        if y is None:
            raise RuntimeError("no '3. Schedule of Amounts' line to annotate")
        rows = (
            ("Collateral Posted", t["decoy_collateral"], _NORMAL),
            ("Notional Amount", t["notional"], _FAINT),
        )
        for idx, (label, value, fill) in enumerate(rows):
            ry = y + 46 + idx * 44
            draw.text((190, ry), label, font=fonts["regular"], fill=fill)
            draw.text((640, ry), f"USD {value}", font=fonts["regular"], fill=fill)
    return _draw


def _trap_lines(t: dict, extra: list[tuple[str, bool]]) -> list[tuple[str, bool]]:
    """scan_lines plus the anchor lines an annotation attaches to.

    The anchors are printed; the VALUES are not. That split is the whole design:
    a text-only reader sees that a field exists and cannot read what it says.
    """
    return scan_lines(t) + [("", False), *extra]


def build_amended_strike(t: dict, path: Path) -> None:
    lines = _trap_lines(t, [
        ("2. Amendments:", True), ("", False),
        ("The Strike Price above is amended as marked. The amended", False),
        ("figure is controlling for all purposes of this Transaction.", False),
    ])
    save_scan_pdf([render_scan(lines, annotate=_amend_strike(t))], path)


def build_ticked_barrier(t: dict, path: Path) -> None:
    # SIX blank lines reserve the vertical space the two checkbox rows occupy.
    # A blank advances 22px and the rows span ~130px below the anchor, so three
    # blanks (the first draft) let the second box land ON TOP of the next
    # printed line -- which no automated check here would have caught.
    lines = _trap_lines(t, [
        ("2. Barrier Terms:", True), ("", False),
        (f"Barrier Price:        USD {t['barrier']}", False),
        ("Barrier Direction:    (tick one)", False),
        ("", False), ("", False), ("", False),
        ("", False), ("", False), ("", False),
        ("Observation:          Continuous", False),
    ])
    save_scan_pdf([render_scan(lines, annotate=_tick_barrier(t))], path)


def build_faint_notional(t: dict, path: Path) -> None:
    lines = _trap_lines(t, [
        ("3. Schedule of Amounts:", True), ("", False),
        ("", False), ("", False), ("", False), ("", False),
        ("Amounts stated in the Settlement Currency.", False),
    ])
    save_scan_pdf([render_scan(lines, annotate=_faint_amounts(t))], path)


def build_docx(t: dict, path: Path) -> None:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10)

    head = doc.add_paragraph()
    head.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = head.add_run("CONFIRMATION OF SHARE OPTION TRANSACTION")
    run.bold = True
    run.font.size = Pt(12)

    for label, value in (
        ("To:", f"{t['cp']} (\"Counterparty\")"),
        ("Legal Entity Identifier (LEI):", t["cp_lei"]),
        ("Attention:", t["cp_attn"]),
        ("Email:", t["cp_email"]),
        ("From:", f"{DEALER} (\"{DEALER_SHORT}\")"),
        ("Legal Entity Identifier (LEI):", DEALER_LEI),
        ("Phone:", DEALER_PHONE),
        (f"{DEALER_SHORT} Ref. No:", t["ref"]),
        ("Date:", t["date_long"]),
    ):
        para = doc.add_paragraph()
        para.add_run(f"{label}\t").bold = True
        para.add_run(value)

    doc.add_paragraph("Dear Sir or Madam:")
    doc.add_paragraph(INTRO.format(dealer=DEALER_SHORT))
    doc.add_paragraph(DEFS)
    doc.add_paragraph(SUPPLEMENT.format(dealer=DEALER_SHORT))
    doc.add_paragraph("The terms of the Transaction to which this Confirmation relates "
                      "are as follows:")

    doc.add_paragraph().add_run("1. General Terms:").bold = True

    rows = [
        ("Trade Date", t["trade_date_long"]),
        ("Option Style", t["style"]),
        ("Option Type", t["option_type"]),
        ("Seller", t.get("buyer", "Counterparty")),
        ("Buyer", t.get("seller", DEALER_SHORT)),
        ("Shares", f"{t['issuer']} (Ticker: {t['ticker']})"),
        ("Number of Options", t["num_options"]),
        ("Option Entitlement", t["entitlement"]),
        ("Strike Price", f"USD {t['strike']}"),
        ("Initial Price", f"USD {t['initial_price']}"),
        ("Premium", f"USD {t['premium']} per Option"),
        ("Premium Payment Date", "Three (3) Currency Business Days following the "
                                 "Trade Date"),
        ("Exchange", t["exchange"]),
        ("Expiration Date", t["expiration_long"]),
        ("Settlement Method", "Physical Settlement"),
        ("Settlement Currency", "US Dollars"),
        ("Calculation Agent", DEALER_SHORT),
    ]
    table = doc.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    for label, value in rows:
        cells = table.add_row().cells
        cells[0].paragraphs[0].add_run(label).bold = True
        cells[1].text = value

    doc.add_paragraph()
    doc.add_paragraph("Please confirm that the foregoing correctly sets forth the terms "
                      "of our agreement by executing this Confirmation and returning it "
                      f"to us by email to {DEALER_EMAIL}.")
    doc.add_paragraph()
    doc.add_paragraph(DEALER)
    doc.add_paragraph("By: ___________________________")
    doc.add_paragraph("Name: A. Whitcombe")
    doc.add_paragraph("Title: Authorized Signatory")
    doc.add_paragraph()
    doc.add_paragraph("Confirmed as of the date first above written:")
    doc.add_paragraph(t["cp"])
    doc.add_paragraph("By: ___________________________")
    doc.add_paragraph()
    doc.add_paragraph(FOOTNOTE)
    doc.save(str(path))


# --------------------------------------------------------------------------
# Build everything
# --------------------------------------------------------------------------

def build_multi_trade(trades: list[dict], ref: str, path: Path) -> None:
    """One document confirming several Transactions -> exercises segmentation."""
    pdf = Pdf(ref)
    first = trades[0]
    pdf.logo()
    pdf.title("CONFIRMATION OF SHARE OPTION TRANSACTIONS")
    pdf.field("To:", f"{first['cp']} (\"Counterparty\")", label_x=Pdf.LEFT)
    pdf.field("Legal Entity Identifier (LEI):", first["cp_lei"])
    pdf.field("Attention:", first["cp_attn"])
    pdf.field("Email:", first["cp_email"])
    pdf.gap(4)
    pdf.field("From:", f"{DEALER} (\"{DEALER_SHORT}\")", label_x=Pdf.LEFT)
    pdf.field("Legal Entity Identifier (LEI):", DEALER_LEI)
    pdf.field(f"{DEALER_SHORT} Ref. No:", ref, label_x=Pdf.LEFT, bold_value=True)
    pdf.field("Date:", first["date_long"], label_x=Pdf.LEFT)
    pdf.gap(6)
    pdf.para("Dear Sir or Madam:")
    pdf.para("This confirms the terms and conditions of the THREE (3) separate "
             "Transactions described below, each entered into between Counterparty and "
             f"{DEALER_SHORT} on the Trade Date specified for that Transaction. Each "
             "Transaction is a separate Transaction and this document constitutes a "
             "separate Confirmation in respect of each of them.")
    pdf.para(DEFS)
    pdf.para(SUPPLEMENT.format(dealer=DEALER_SHORT))

    for i, t in enumerate(trades, start=1):
        pdf.page_break()
        pdf.heading(f"TRANSACTION {i} OF {len(trades)}  -  Ref. No: {t['ref']}",
                    underline=True)
        general_terms(pdf, t, heading=False)
        pdf.field("Expiration Date:", t["expiration_long"])
        pdf.field("Settlement Method:", "Physical Settlement")
        pdf.field("Settlement Currency:", "US Dollars")
        pdf.field("Calculation Agent:", DEALER_SHORT)

    pdf.page_break()
    pdf.para("Please confirm that the foregoing correctly sets forth the terms of each "
             "of the Transactions described above by executing this Confirmation and "
             f"returning it to us by email to {DEALER_EMAIL}.")
    pdf.gap(14)
    pdf.para(DEALER)
    pdf.gap(18)
    pdf.field("By:", "___________________________", label_x=Pdf.LEFT)
    pdf.field("Name:", "A. Whitcombe", label_x=Pdf.LEFT)
    pdf.field("Title:", "Authorized Signatory", label_x=Pdf.LEFT)
    pdf.gap(14)
    pdf.para("Confirmed as of the date first above written:")
    pdf.para(first["cp"])
    pdf.gap(18)
    pdf.field("By:", "___________________________", label_x=Pdf.LEFT)
    pdf.save(path)


def build_mixed(t: dict, path: Path) -> None:
    """Page 1 = real text; page 2 = image-only scan carrying the priced terms."""
    from pypdf import PdfReader, PdfWriter

    pdf = Pdf(t["ref"])
    cover(pdf, t["cp"], t["cp_lei"], t["cp_attn"], t["cp_email"], t["ref"],
          t["date_long"])
    pdf.gap(4)
    pdf.para("The economic terms of the Transaction are set out on the attached "
             "executed term sheet page, which forms part of this Confirmation.")
    text_pdf = io.BytesIO(pdf.build())

    scan = render_scan([
        ("ARDSLEY GLOBAL MARKETS, N.A.", True),
        ("EXECUTED TERM SHEET - page 2 of 2", True),
        ("", False),
        (f"Ardsley Ref. No:      {t['ref']}", False),
        ("", False),
        ("1. General Terms:", True),
        ("", False),
    ] + [line for line in scan_lines(t, header=False)])
    scan_buf = io.BytesIO()
    scan.convert("RGB").save(scan_buf, "PDF", resolution=150.0)

    writer = PdfWriter()
    for buf in (text_pdf, scan_buf):
        buf.seek(0)
        for page in PdfReader(buf).pages:
            writer.add_page(page)
    with path.open("wb") as fh:
        writer.write(fh)


def build_all(out_dir: Path | None = None) -> list[Path]:
    """Render every confirmation document into ``out_dir`` and return the paths.

    Deterministic FROM A FRESH INTERPRETER: the module seeds ``random`` at import,
    so the scan degradation (skew, grain, blur) reproduces exactly. The re-seed
    here is defensive -- it does NOT buy same-process repeatability, because the
    rendering stack consumes the RNG lazily on first use (see the module
    docstring). Call it once per process, as the CLI entry point does.
    """
    out_dir = Path(out_dir) if out_dir is not None else OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    random.seed(_SEED)

    written: list[Path] = []

    def _emit(path: Path) -> Path:
        written.append(path)
        return path

    build_confirmation(C1).save(_emit(out_dir / "conf-01-american-call-aapl.pdf"))
    build_docx(C2, _emit(out_dir / "conf-02-european-put-msft.docx"))
    build_multi_trade([C3A, C3B, C3C], "ARD-EQO-2026-04610-12",
                      _emit(out_dir / "conf-03-multi-trade-sablefish.pdf"))
    save_scan_pdf([render_scan(scan_lines(C4))],
                  _emit(out_dir / "conf-04-scanned-call-googl.pdf"))
    build_confirmation(C5).save(_emit(out_dir / "conf-05-knockout-barrier-tsla.pdf"))
    build_confirmation(C6).save(_emit(out_dir / "conf-06-asian-average-spy.pdf"))
    build_confirmation(C7).save(
        _emit(out_dir / "conf-07-missing-initial-price-meta.pdf"))
    build_mixed(C8, _emit(out_dir / "conf-08-mixed-text-and-scan-amd.pdf"))
    build_amended_strike(C9, _emit(out_dir / "conf-09-amended-strike-nvda.pdf"))
    build_ticked_barrier(C10, _emit(out_dir / "conf-10-ticked-barrier-amzn.pdf"))
    build_faint_notional(C11, _emit(out_dir / "conf-11-faint-notional-orcl.pdf"))

    return written


# --------------------------------------------------------------------------
# Graded truth, emitted FROM the same dicts the documents render from
# --------------------------------------------------------------------------
# Hand-editing the truth file is exactly how fixtures and documents silently
# disagree, after which every grounding check mis-scores with no error anywhere.
#
# `image_only` = values a model can obtain ONLY by looking. `decoys` = numbers
# on the same page that a misread could plausibly return; a guard test asserts
# every graded number sits >5% away from all of them, so a wrong read fails
# rather than passing inside rel_tol.


def _num(text: str) -> float:
    return float(str(text).replace(",", ""))


TRUTH_DOCUMENTS = {
    "conf-04-scanned-call-googl.pdf": {
        "role": "floor",
        "extract_mode": "vision",
        "image_only": {"strike": 205.00, "reference": "ARD-EQO-2026-04688"},
        "decoys": [],
        "note": "Measured saturated 2026-08-28: all four contestants read it "
                "perfectly. Kept as the floor -- a contestant failing HERE makes "
                "nothing downstream interpretable.",
    },
    "conf-08-mixed-text-and-scan-amd.pdf": {
        "role": "page-selection",
        "extract_mode": "mixed",
        "image_only": {"strike": _num(C8["strike"]),
                       "initial_price": _num(C8["initial_price"])},
        "decoys": [],
        "note": "The priced terms live only on the SCANNED page. Stage 1's page "
                "list is a hard filter on what stage 2 sees, so a model that "
                "drops page 2 returns empty terms. Baseline: 1 in 6 runs.",
    },
    "conf-09-amended-strike-nvda.pdf": {
        "role": "trap-correction",
        "extract_mode": "vision",
        "image_only": {"strike": _num(C9["amended_strike"]),
                       "reference": C9["ref"]},
        "decoys": [_num(C9["strike"]), _num(C9["initial_price"])],
        "note": "The PRINTED strike (780.00) is struck through and 917.50 inked "
                "beside it. Reading the field without noticing the correction "
                "returns the decoy.",
    },
    "conf-10-ticked-barrier-amzn.pdf": {
        "role": "trap-categorical",
        "extract_mode": "vision",
        "image_only": {"barrier_type": C10["barrier_type"],
                       "barrier": _num(C10["barrier"])},
        "decoys": [_num(C10["strike"]), _num(C10["initial_price"])],
        "note": "Direction is carried ONLY by which box is ticked. Both labels "
                "are printed, so there is no textual fallback and no lexical "
                "hint -- the ink is the entire signal.",
    },
    "conf-11-faint-notional-orcl.pdf": {
        "role": "trap-degraded",
        "extract_mode": "vision",
        # The whole page is image-only, so the strike is obtainable only by
        # sight too -- it is graded alongside the faint notional.
        "image_only": {"notional": _num(C11["notional"]),
                       "strike": _num(C11["strike"]),
                       "reference": C11["ref"]},
        "decoys": [_num(C11["decoy_collateral"])],
        "note": "The notional sits in a low-contrast column beside an "
                "ordinary-contrast decoy of similar magnitude. The wrong number "
                "is the easier read.",
    },
    "conf-07-missing-initial-price-meta.pdf": {
        "role": "trap-absence",
        "extract_mode": "text",
        "image_only": {},
        "decoys": [_num(C7["strike"])],
        "note": "States NO Initial Price. The graded answer is that the field is "
                "absent; substituting the strike (780.00) is the measured "
                "failure -- 2 of 6 sampled runs of the incumbent extractor.",
    },
}


def write_truth(path: Path) -> dict:
    """Emit the graded constants FROM the dicts the documents render from."""
    import json

    truth = {"documents": TRUTH_DOCUMENTS}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n")
    return truth


_TRUTH_PATH = (
    Path(__file__).resolve().parents[1]
    / "definitions" / "confirmation-desk-day.truth.json"
)


def main() -> int:
    for path in build_all():
        print(f"  {path.name:44} {path.stat().st_size / 1024:7.1f} KB")
    write_truth(_TRUTH_PATH)
    print(f"  {'-> ' + _TRUTH_PATH.name:44}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Build a LICENSED invoice corpus with ground truth that no extractor touched.

WHY THIS EXISTS
---------------
The corpus this project already had is not an invoice corpus. MEASURED before
this script was written: 300 of its 422 documents came from Wikimedia Commons,
selected for REDISTRIBUTABLE LICENCE because this repository is public. Nobody
CC-licenses their own bills, so licence-first selection returns whatever else
was lying in the category. The consequence was measured at 1/300 field slots
scored, and 119 of the 287 misses were NO_LABEL_ON_PAGE - the label was never
printed, because the page was never an invoice.

Preprocessing was already tried and already failed on that corpus: 10 Pillow
methods over 4 documents lifted legible OCR rows from 5 to 159 and recovered
ZERO labels. Sharpening a page that does not say TOTAL still does not say
TOTAL. The defect is the corpus, not the reader, and this script fixes the
corpus.

WHAT GROUND TRUTH IS ALLOWED TO COME FROM
-----------------------------------------
Never from our own extractor. That rule is the whole point: a corpus labelled
by the thing under test measures agreement with itself and reports it as
accuracy. So there are exactly two permitted sources here.

  SYNTHETIC documents - the generator knows every value because it drew it.
  The ground truth is written from the `Bill` record BEFORE rendering, not read
  back off the pixels afterwards.

  REAL documents - transcribed BY HAND from the page by a human reader, and
  cross-checked against the upstream dataset's own annotation where one exists.
  Those transcriptions are the `HAND_READ` table below. They were typed after
  looking at all 14 pages, one at a time.

WHY HAND-READING THE REAL ONES WAS NOT OPTIONAL. MEASURED 2026-08-15 over the
13 Voxel51 documents: the upstream annotation and the printed page DISAGREE ON
NUMBER FORMAT in 9 of 13 cases. The page always prints space-grouped
comma-decimal (`4 334,11`); the annotation rewrote that to `4334.11` in 9 rows
and left it alone in 4 (documents 01, 04, 08, 12). Document 11 also had its
thousands space deleted. Trusting the annotation's spelling would have written
a ground truth that no page contains, and any reader scored against it would
lose marks for being right. The VALUES agreed in 13 of 13 - it is only the
spelling that moved.

MONEY IS INTEGER PAISE, EVERYWHERE, WITH NO EXCEPTION. There is not one float
in this file. Tax is `(net * rate + 50) // 100`, and a split GST is
`cgst = tax // 2` with `sgst = tax - cgst`, so the two halves add back to the
whole for odd paise instead of drifting by one. A 1-paisa mismatch is a real
failure and rounding twice is how you get one.

ABSENT IS NEVER ZERO. A bill with no tax line gets `status = ABSENT` and a
stated reason. It does not get `0`. Writing zero for a value nobody printed is
the exact shape of a silent wrong post: it is a number, it balances, and it is
invented.

DETERMINISM IS A CORRECTNESS PROPERTY HERE, NOT A CONVENIENCE. The ground truth
is written by this script from what it drew, so if a rerun draws something else
the ground truth silently stops describing the files on disk. Every random
choice comes from `random.Random(SEED)` seeded with a constant, the documents
are built in a fixed order, and nothing reads the clock. `--verify` re-renders
into a temporary directory and compares sha256 against the manifest.

RUN
    python scripts/build_problem1_corpus.py
    python scripts/build_problem1_corpus.py --verify
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import random
import re
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Literal, TypedDict

from PIL import Image, ImageDraw, ImageFont

# The seed is part of the contract. Changing it rewrites every synthetic
# document AND its ground truth; the two only stay in step because both come
# from the same seeded run.
SEED = 20260815

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CORPUS_DIR = REPO_ROOT / "data" / "problem1_corpus"
ARTIFACTS_DIR = REPO_ROOT / "artifacts"

Status = Literal["PRESENT", "ABSENT", "AMBIGUOUS", "UNREADABLE"]

FIELDS = ("party", "invoice_date", "total", "tax", "invoice_number")


# ---------------------------------------------------------------------------
# Money. Integer paise in, formatted string out, and never a float between.
# ---------------------------------------------------------------------------
def indian_money(paise: int) -> str:
    """Format paise the way an Indian bill prints it: 12345678 -> '1,23,456.78'.

    Indian grouping is NOT thousands-separated. The last three digits of the
    rupee part form one group and everything above it is grouped in TWOS, so a
    lakh prints as `1,00,000.00` and not `100,000.00`. A reader that assumes
    groups of three reads `1,23,456.78` as `123456.78` only by luck - it gets
    the digits right and the group boundaries wrong - and on `12,34,567.89` a
    three-group parser that trusts the first comma reads 12 instead of 1234567.
    """
    if paise < 0:
        raise ValueError(f"money cannot be negative: {paise}")
    rupees, pais = divmod(paise, 100)
    digits = str(rupees)
    if len(digits) <= 3:
        grouped = digits
    else:
        head, tail = digits[:-3], digits[-3:]
        parts: list[str] = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        grouped = ",".join([*parts, tail])
    return f"{grouped}.{pais:02d}"


def plain_money(paise: int) -> str:
    """Format paise with no grouping at all: 12345678 -> '123456.78'.

    Some real bills print totals ungrouped. A corpus where every amount carries
    commas would let a reader depend on the comma to find the amount.
    """
    rupees, pais = divmod(paise, 100)
    return f"{rupees}.{pais:02d}"


def gst_split(net_paise: int, rate_percent: int) -> tuple[int, int, int]:
    """Return (total_tax, cgst, sgst) in paise, all integers, all adding back.

    Rounds half up once, on the whole tax, then splits. Rounding each half
    separately is the bug this avoids: at net=1000 paise and rate=5 the whole
    tax is 50 and each half is 25, but halving first gives 2.5 twice, and any
    two roundings of 2.5 either lose a paisa or invent one.
    """
    tax = (net_paise * rate_percent + 50) // 100
    cgst = tax // 2
    return tax, cgst, tax - cgst


# ---------------------------------------------------------------------------
# What a document IS, stated before anything is drawn.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Line:
    description: str
    qty: int
    unit_paise: int

    @property
    def amount_paise(self) -> int:
        return self.qty * self.unit_paise


@dataclass
class Bill:
    """Every fact about one document, fixed before a single pixel is drawn."""

    document_id: str
    layout: str
    party: str
    party_address: tuple[str, ...]
    buyer: str
    invoice_number: str | None
    date_text: str | None
    lines: tuple[Line, ...]
    tax_mode: Literal["cgst_sgst", "igst", "none"]
    tax_rate: int
    # Quirks the corpus exists to carry.
    show_sub_total: bool = False
    next_line_values: bool = False
    ambiguous_total_paise: int | None = None
    omit_total: bool = False
    degrade: Literal["none", "moderate", "severe"] = "none"
    pages: int = 1
    money_style: Literal["indian", "plain"] = "indian"
    unreadable_fields: tuple[str, ...] = ()
    notes: str = ""
    # `dict[str, str]` and not bare `dict`: the parameterised alias is callable
    # and returns the same empty dict, but it also states the member types.
    evidence: dict[str, str] = field(default_factory=dict[str, str])

    @property
    def net_paise(self) -> int:
        return sum(line.amount_paise for line in self.lines)

    @property
    def tax_paise(self) -> int | None:
        if self.tax_mode == "none":
            return None
        tax, _, _ = gst_split(self.net_paise, self.tax_rate)
        return tax

    @property
    def total_paise(self) -> int:
        tax = self.tax_paise
        return self.net_paise + (tax or 0)

    def money(self, paise: int) -> str:
        return (
            indian_money(paise) if self.money_style == "indian" else plain_money(paise)
        )


# ---------------------------------------------------------------------------
# Fonts. Chosen deterministically; the ground truth is values, not glyphs, so a
# substituted font changes the picture and not one expected number.
# ---------------------------------------------------------------------------
FONT_CANDIDATES = {
    "sans": [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
    "sans_bold": [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
    "serif": [
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    ],
    "mono": [
        "/System/Library/Fonts/Supplemental/Courier New.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ],
}


def load_font(kind: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES[kind]:
        candidate = pathlib.Path(path)
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Drawing helpers.
# ---------------------------------------------------------------------------
PAGE_W, PAGE_H = 1240, 1754  # A4 at 150 dpi


class Canvas:
    """A page under construction, plus the evidence of what got printed where."""

    def __init__(self, width: int = PAGE_W, height: int = PAGE_H) -> None:
        self.image = Image.new("L", (width, height), 255)
        self.draw = ImageDraw.Draw(self.image)
        self.width = width
        self.height = height

    def text(
        self,
        xy: tuple[int, int],
        content: str,
        kind: str = "sans",
        size: int = 22,
        anchor: str | None = None,
    ) -> None:
        self.draw.text(xy, content, font=load_font(kind, size), fill=0, anchor=anchor)

    def right_text(
        self, x: int, y_bottom: int, content: str, kind: str = "sans", size: int = 22
    ) -> None:
        """Right-align `content` so its right edge is `x` and its TOP is
        `y_bottom - size` - which is the same top as a `text()` call on the
        same row.

        `y_bottom`, not `y`, because every caller passes `row_top + size`.
        Pillow's default anchor is `la` (left, ascender-TOP), so `text()` at y
        puts the glyph top at y; a right-aligned `ra` call at the same y sits
        at the same height. Drawing this at the caller's `y` directly is the
        bug this signature exists to stop: it pushed every right-hand money
        cell exactly one line below its own label, so 'SUB TOTAL' printed
        beside the tax amount and 'GRAND TOTAL' beside nothing. The values
        were right and the page said something else.
        """
        self.draw.text(
            (x, y_bottom - size),
            content,
            font=load_font(kind, size),
            fill=0,
            anchor="ra",
        )

    def line(self, xy: tuple[int, int, int, int], width: int = 2) -> None:
        self.draw.line(xy, fill=0, width=width)

    def rect(self, xy: tuple[int, int, int, int], width: int = 2) -> None:
        self.draw.rectangle(xy, outline=0, width=width)


def degrade(image: Image.Image, level: str, rng: random.Random) -> Image.Image:
    """Make a page harder to read, the way a real phone photo of a bill is.

    Deterministic on purpose. Pillow's own `Image.effect_noise` carries an
    internal generator this script cannot seed, so the noise plane is built
    from `rng.randbytes`, which is seeded from SEED like everything else. A
    corpus whose degradation differs between runs cannot be regression-tested.

    THE TWO TIERS MEAN DIFFERENT THINGS AND THE GROUND TRUTH DEPENDS ON IT.
    `moderate` must stay READABLE - its documents keep status PRESENT, so if
    this tier ever destroys a value the ground truth starts lying in the
    generous direction. `severe` must be UNREADABLE for ALL FIVE fields,
    because that is what `synthetic_ground_truth` writes for it.

    MEASURED AND CORRECTED 2026-08-15: severe was first set to scale 0.22 /
    alpha 0.34, and at those numbers the seller name was still plainly legible
    - 'Rao Cold Storage' could be read straight off synthetic-038 while the
    ground truth called it UNREADABLE. A reader that got that name RIGHT would
    have been scored WRONG. The numbers below were raised until a human reader
    could no longer make out the largest, boldest text on the page, which is
    the seller name at size 34; every smaller field goes first.
    """
    if level == "none":
        return image
    scale, alpha, angle = (
        (0.55, 0.12, 0.8) if level == "moderate" else (0.10, 0.55, 2.4)
    )

    # The two `pyright: ignore` below are not about this code. Pillow annotates
    # `resize(size: tuple[int, int] | list[int] | NumpyArray)`, and NumpyArray
    # resolves to Unknown because numpy is not installed in this environment,
    # so the whole member type is partially unknown at every call site. The
    # arguments here are already a `tuple[int, int]` and an `Image.Size`.
    small = image.resize(  # pyright: ignore[reportUnknownMemberType]
        (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
        Image.Resampling.BILINEAR,
    )
    back = small.resize(  # pyright: ignore[reportUnknownMemberType]
        image.size, Image.Resampling.BILINEAR
    )

    noise = Image.frombytes("L", image.size, rng.randbytes(image.width * image.height))
    noisy = Image.blend(back, noise, alpha)

    return noisy.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)


# ---------------------------------------------------------------------------
# Layouts. Seven of them, and they differ in the ways a READER cares about -
# where the value sits relative to its label, which labels exist at all, and
# whether a SUB TOTAL is printed next to the TOTAL.
# ---------------------------------------------------------------------------
def _draw_items_table(
    canvas: Canvas, bill: Bill, top: int, *, boxed: bool, size: int = 20
) -> int:
    x0, x1 = 90, canvas.width - 90
    y = top
    canvas.text((x0, y), "Description", "sans_bold", size)
    canvas.text((x0 + 560, y), "Qty", "sans_bold", size)
    canvas.right_text(x1 - 200, y + size, "Rate", "sans_bold", size)
    canvas.right_text(x1 - 10, y + size, "Amount", "sans_bold", size)
    y += size + 14
    canvas.line((x0, y, x1, y), 2)
    y += 12
    for line in bill.lines:
        canvas.text((x0, y), line.description[:46], "sans", size)
        canvas.text((x0 + 560, y), str(line.qty), "sans", size)
        canvas.right_text(x1 - 200, y + size, bill.money(line.unit_paise), "sans", size)
        canvas.right_text(
            x1 - 10, y + size, bill.money(line.amount_paise), "sans", size
        )
        y += size + 16
    canvas.line((x0, y, x1, y), 1)
    if boxed:
        canvas.rect((x0 - 14, top - 14, x1 + 14, y + 14), 2)
    return y + 30


def _totals_block(canvas: Canvas, bill: Bill, top: int, *, size: int = 22) -> int:
    """Print the money block, recording the exact string used for each field.

    SUB TOTAL and TOTAL are printed as DIFFERENT labels carrying DIFFERENT
    amounts whenever there is tax, because that is the pair that causes the
    worst failure this project has: a subtotal read as a total posts the bill
    short by exactly its tax, it balances against itself, and nobody is told.
    """
    x1 = canvas.width - 100
    # 380, not 300. At 300 the widest label and the widest amount collide:
    # 'GRAND TOTAL' at size 24 bold is ~165px and '1,35,938.36' is ~150px, so
    # on a lakh-sized total they printed as 'GRAND TOTAL1,35,938.36' with no
    # gap. That is a rendering accident that would hand OCR a single glued
    # token, and the ground truth would still claim the total was readable.
    label_x = x1 - 380
    y = top
    tax = bill.tax_paise

    if bill.show_sub_total:
        text = bill.money(bill.net_paise)
        if bill.next_line_values:
            canvas.text((label_x, y), "SUB TOTAL", "sans", size)
            y += size + 8
            canvas.right_text(x1, y + size, text, "sans", size)
            y += size + 18
        else:
            canvas.text((label_x, y), "SUB TOTAL", "sans", size)
            canvas.right_text(x1, y + size, text, "sans", size)
            y += size + 18

    if tax is not None:
        if bill.tax_mode == "cgst_sgst":
            _, cgst, sgst = gst_split(bill.net_paise, bill.tax_rate)
            half = bill.tax_rate // 2
            for label, amount in (
                (f"CGST @ {half}%", cgst),
                (f"SGST @ {half}%", sgst),
            ):
                canvas.text((label_x, y), label, "sans", size)
                canvas.right_text(x1, y + size, bill.money(amount), "sans", size)
                y += size + 14
            bill.evidence["tax"] = (
                f"CGST @ {half}% {bill.money(cgst)} + SGST @ {half}% "
                f"{bill.money(sgst)} = {bill.money(tax)}"
            )
        else:
            label = f"IGST @ {bill.tax_rate}%"
            canvas.text((label_x, y), label, "sans", size)
            canvas.right_text(x1, y + size, bill.money(tax), "sans", size)
            y += size + 14
            bill.evidence["tax"] = f"{label} {bill.money(tax)}"

    if not bill.omit_total:
        label = "GRAND TOTAL" if bill.show_sub_total else "TOTAL"
        text = bill.money(bill.total_paise)
        canvas.line((label_x - 20, y, x1, y), 2)
        y += 12
        if bill.next_line_values:
            canvas.text((label_x, y), label, "sans_bold", size + 2)
            y += size + 10
            canvas.right_text(x1, y + size, text, "sans_bold", size + 2)
            y += size + 16
            bill.evidence["total"] = f"'{label}' with '{text}' on the NEXT line"
        else:
            canvas.text((label_x, y), label, "sans_bold", size + 2)
            canvas.right_text(x1, y + size, text, "sans_bold", size + 2)
            y += size + 20
            bill.evidence["total"] = f"'{label}   {text}' on ONE line"

    if bill.ambiguous_total_paise is not None:
        second = bill.money(bill.ambiguous_total_paise)
        canvas.text((label_x, y), "TOTAL PAYABLE", "sans_bold", size)
        canvas.right_text(x1, y + size, second, "sans_bold", size)
        y += size + 20
        bill.evidence["total"] = (
            f"TWO different totals printed: 'TOTAL {bill.money(bill.total_paise)}' "
            f"and 'TOTAL PAYABLE {second}'"
        )
    return y


def _header_party(canvas: Canvas, bill: Bill, kind: str = "sans_bold") -> int:
    canvas.text((90, 70), bill.party, kind, 34)
    bill.evidence["party"] = f"'{bill.party}' as the seller heading at the top left"
    y = 118
    for row in bill.party_address:
        canvas.text((90, y), row, "sans", 20)
        y += 26
    return y


def _header_meta(canvas: Canvas, bill: Bill, top: int) -> None:
    x1 = canvas.width - 90
    y = 74
    if bill.invoice_number is not None:
        canvas.right_text(x1, y + 22, f"Invoice No: {bill.invoice_number}", "sans", 22)
        bill.evidence["invoice_number"] = f"'Invoice No: {bill.invoice_number}'"
        y += 34
    if bill.date_text is not None:
        canvas.right_text(x1, y + 22, f"Date: {bill.date_text}", "sans", 22)
        bill.evidence["invoice_date"] = f"'Date: {bill.date_text}'"
    _ = top


def render_classic(bill: Bill, rng: random.Random) -> list[Image.Image]:
    canvas = Canvas()
    y = _header_party(canvas, bill)
    _header_meta(canvas, bill, y)
    canvas.text((90, y + 30), f"Bill To: {bill.buyer}", "sans", 22)
    canvas.text((90, y + 66), "TAX INVOICE", "sans_bold", 26)
    y = _draw_items_table(canvas, bill, y + 110, boxed=False)
    _totals_block(canvas, bill, y)
    return [degrade(canvas.image, bill.degrade, rng)]


def render_boxed(bill: Bill, rng: random.Random) -> list[Image.Image]:
    canvas = Canvas()
    canvas.rect((60, 50, canvas.width - 60, canvas.height - 60), 3)
    y = _header_party(canvas, bill)
    _header_meta(canvas, bill, y)
    canvas.line((60, y + 20, canvas.width - 60, y + 20), 3)
    canvas.text((90, y + 40), f"Bill To: {bill.buyer}", "sans", 22)
    y = _draw_items_table(canvas, bill, y + 90, boxed=True)
    _totals_block(canvas, bill, y)
    return [degrade(canvas.image, bill.degrade, rng)]


def render_serif_letterhead(bill: Bill, rng: random.Random) -> list[Image.Image]:
    canvas = Canvas()
    canvas.text((canvas.width // 2, 70), bill.party, "serif", 36, anchor="ma")
    bill.evidence["party"] = f"'{bill.party}' centred as the letterhead"
    y = 130
    for row in bill.party_address:
        canvas.text((canvas.width // 2, y), row, "serif", 19, anchor="ma")
        y += 26
    canvas.line((90, y + 10, canvas.width - 90, y + 10), 1)
    y += 40
    if bill.invoice_number is not None:
        canvas.text((90, y), f"Invoice No: {bill.invoice_number}", "serif", 22)
        bill.evidence["invoice_number"] = f"'Invoice No: {bill.invoice_number}'"
    if bill.date_text is not None:
        canvas.right_text(
            canvas.width - 90, y + 22, f"Date: {bill.date_text}", "serif", 22
        )
        bill.evidence["invoice_date"] = f"'Date: {bill.date_text}'"
    y = _draw_items_table(canvas, bill, y + 60, boxed=False)
    _totals_block(canvas, bill, y)
    return [degrade(canvas.image, bill.degrade, rng)]


def render_receipt(bill: Bill, rng: random.Random) -> list[Image.Image]:
    """A narrow thermal-till receipt. Monospace, centred, no table rules."""
    canvas = Canvas(620, 1400)
    mid = canvas.width // 2
    canvas.text((mid, 50), bill.party, "mono", 26, anchor="ma")
    bill.evidence["party"] = f"'{bill.party}' centred at the top of the receipt"
    y = 90
    for row in bill.party_address:
        canvas.text((mid, y), row, "mono", 16, anchor="ma")
        y += 22
    y += 10
    if bill.invoice_number is not None:
        canvas.text((40, y), f"Bill No {bill.invoice_number}", "mono", 18)
        bill.evidence["invoice_number"] = f"'Bill No {bill.invoice_number}'"
        y += 26
    if bill.date_text is not None:
        canvas.text((40, y), f"Dt {bill.date_text}", "mono", 18)
        bill.evidence["invoice_date"] = f"'Dt {bill.date_text}'"
        y += 30
    canvas.text((40, y), "-" * 38, "mono", 16)
    y += 26
    for line in bill.lines:
        canvas.text((40, y), line.description[:26], "mono", 16)
        y += 22
        canvas.text((60, y), f"{line.qty} x {bill.money(line.unit_paise)}", "mono", 16)
        canvas.right_text(
            canvas.width - 40, y + 16, bill.money(line.amount_paise), "mono", 16
        )
        y += 26
    canvas.text((40, y), "-" * 38, "mono", 16)
    y += 30

    tax = bill.tax_paise
    if bill.show_sub_total:
        canvas.text((40, y), "SUB TOTAL", "mono", 18)
        canvas.right_text(
            canvas.width - 40, y + 18, bill.money(bill.net_paise), "mono", 18
        )
        y += 28
    if tax is not None:
        label = (
            f"IGST {bill.tax_rate}%"
            if bill.tax_mode == "igst"
            else f"GST {bill.tax_rate}%"
        )
        canvas.text((40, y), label, "mono", 18)
        canvas.right_text(canvas.width - 40, y + 18, bill.money(tax), "mono", 18)
        bill.evidence["tax"] = f"'{label}  {bill.money(tax)}'"
        y += 28
    if not bill.omit_total:
        text = bill.money(bill.total_paise)
        canvas.text((40, y), "TOTAL", "mono", 22)
        canvas.right_text(canvas.width - 40, y + 22, text, "mono", 22)
        bill.evidence["total"] = f"'TOTAL   {text}' on ONE line"
        y += 36
    canvas.text((mid, y + 10), "THANK YOU", "mono", 16, anchor="ma")
    return [degrade(canvas.image, bill.degrade, rng)]


def render_stacked(bill: Bill, rng: random.Random) -> list[Image.Image]:
    """Labels on one line, values on the NEXT. The layout that breaks readers
    which only ever look to the right of a label."""
    canvas = Canvas()
    canvas.text((90, 70), bill.party, "sans_bold", 32)
    bill.evidence["party"] = f"'{bill.party}' at the top left"
    y = 116
    for row in bill.party_address:
        canvas.text((90, y), row, "sans", 20)
        y += 26
    y += 20
    if bill.invoice_number is not None:
        canvas.text((90, y), "INVOICE NUMBER", "sans", 20)
        canvas.text((90, y + 28), bill.invoice_number, "sans_bold", 24)
        bill.evidence["invoice_number"] = (
            f"'INVOICE NUMBER' with '{bill.invoice_number}' on the NEXT line"
        )
        y += 70
    if bill.date_text is not None:
        canvas.text((90, y), "INVOICE DATE", "sans", 20)
        canvas.text((90, y + 28), bill.date_text, "sans_bold", 24)
        bill.evidence["invoice_date"] = (
            f"'INVOICE DATE' with '{bill.date_text}' on the NEXT line"
        )
        y += 70
    y = _draw_items_table(canvas, bill, y + 20, boxed=False)
    _totals_block(canvas, bill, y)
    return [degrade(canvas.image, bill.degrade, rng)]


def render_minimal(bill: Bill, rng: random.Random) -> list[Image.Image]:
    """No rules, no box, wide spacing. A non-GST cash memo."""
    canvas = Canvas()
    canvas.text((90, 90), bill.party, "sans_bold", 30)
    bill.evidence["party"] = f"'{bill.party}' at the top left"
    y = 140
    for row in bill.party_address:
        canvas.text((90, y), row, "sans", 19)
        y += 24
    y += 30
    canvas.text((90, y), "CASH MEMO", "sans_bold", 24)
    y += 50
    if bill.invoice_number is not None:
        canvas.text((90, y), f"No. {bill.invoice_number}", "sans", 21)
        bill.evidence["invoice_number"] = f"'No. {bill.invoice_number}'"
        y += 32
    if bill.date_text is not None:
        canvas.text((90, y), f"Dated {bill.date_text}", "sans", 21)
        bill.evidence["invoice_date"] = f"'Dated {bill.date_text}'"
        y += 46
    for line in bill.lines:
        canvas.text((90, y), f"{line.qty}  {line.description[:40]}", "sans", 21)
        canvas.right_text(
            canvas.width - 120, y + 21, bill.money(line.amount_paise), "sans", 21
        )
        y += 40
    y += 20
    _totals_block(canvas, bill, y)
    return [degrade(canvas.image, bill.degrade, rng)]


def render_multipage(bill: Bill, rng: random.Random) -> list[Image.Image]:
    """Items run over pages; the money block is on the LAST page only.

    A reader that stops at page 1 finds a party, a number and a date and NO
    total. That is the case that must end in a question, not in a posting.
    """
    per_page = 6
    chunks = [
        bill.lines[i : i + per_page] for i in range(0, len(bill.lines), per_page)
    ] or [()]
    images: list[Image.Image] = []
    for index, chunk in enumerate(chunks):
        canvas = Canvas()
        if index == 0:
            y = _header_party(canvas, bill)
            _header_meta(canvas, bill, y)
            canvas.text((90, y + 30), f"Bill To: {bill.buyer}", "sans", 22)
            top = y + 90
        else:
            canvas.text((90, 70), f"{bill.party} (continued)", "sans_bold", 26)
            canvas.right_text(
                canvas.width - 90, 92, f"Page {index + 1} of {len(chunks)}", "sans", 20
            )
            top = 150
        page_bill = Bill(
            document_id=bill.document_id,
            layout=bill.layout,
            party=bill.party,
            party_address=bill.party_address,
            buyer=bill.buyer,
            invoice_number=bill.invoice_number,
            date_text=bill.date_text,
            lines=tuple(chunk),
            tax_mode=bill.tax_mode,
            tax_rate=bill.tax_rate,
            money_style=bill.money_style,
        )
        y = _draw_items_table(canvas, page_bill, top, boxed=False)
        canvas.right_text(
            canvas.width - 90,
            canvas.height - 80,
            f"Page {index + 1} of {len(chunks)}",
            "sans",
            18,
        )
        if index == len(chunks) - 1:
            _totals_block(canvas, bill, y)
        images.append(degrade(canvas.image, bill.degrade, rng))
    return images


RENDERERS = {
    "classic_table": render_classic,
    "boxed_gst": render_boxed,
    "serif_letterhead": render_serif_letterhead,
    "narrow_receipt": render_receipt,
    "stacked_labels": render_stacked,
    "minimal_cash_memo": render_minimal,
    "multipage_continuation": render_multipage,
}


# ---------------------------------------------------------------------------
# The catalogue of synthetic documents.
# ---------------------------------------------------------------------------
PARTIES = [
    ("Sundaram Traders", ("14 Anna Salai", "Chennai 600002", "GSTIN 33AABCS1429B1ZQ")),
    (
        "Verma Electricals",
        ("221 Karol Bagh", "New Delhi 110005", "GSTIN 07AAACV2081K1Z5"),
    ),
    ("Konkan Hardware Stores", ("9 MG Road", "Panaji 403001", "GSTIN 30AACCK4417L1ZP")),
    (
        "Bose Stationery Mart",
        ("77 Park Street", "Kolkata 700016", "GSTIN 19AADCB6721M1ZT"),
    ),
    (
        "Nilgiri Tea Supply Co",
        ("3 Bazaar Road", "Coonoor 643101", "GSTIN 33AAECN9080P1ZH"),
    ),
    (
        "Patel Auto Spares",
        ("52 Ashram Road", "Ahmedabad 380009", "GSTIN 24AAFCP3312R1ZB"),
    ),
    ("Rao Cold Storage", ("18 Tank Bund", "Hyderabad 500080", "GSTIN 36AAGCR7756N1ZF")),
    (
        "Iyer Provision Stores",
        ("6 Temple Street", "Madurai 625001", "GSTIN 33AAHCI1198D1ZK"),
    ),
]

BUYERS = [
    "Meridian Foods Pvt Ltd",
    "Ashok Enterprises",
    "Greenfield Retail LLP",
    "Sagar Constructions",
    "Deccan Logistics Pvt Ltd",
]

GOODS = [
    ("Copper wire 2.5 sq mm coil", 45000),
    ("LED panel light 18W", 62500),
    ("Ceiling fan 1200mm", 189900),
    ("PVC conduit pipe 25mm", 12750),
    ("MCB 32A double pole", 47800),
    ("A4 copier paper 75 gsm ream", 28500),
    ("Ballpoint pen box of 50", 19900),
    ("Steel almirah 6ft", 1245000),
    ("Packing tape 48mm roll", 6500),
    ("Diesel generator service kit", 875000),
    ("Refrigeration compressor unit", 3450000),
    ("Cement OPC 53 grade bag", 41000),
]


def build_bills(rng: random.Random) -> list[Bill]:
    """Assemble the synthetic catalogue. Order is fixed, choices are seeded."""
    bills: list[Bill] = []
    layouts = list(RENDERERS)

    # Plan: (layout, tax_mode, rate, quirks). Written out rather than sampled so
    # the coverage of the awkward cases is guaranteed and not left to the dice.
    plan: list[dict[str, object]] = []

    # 1-12: the plain spread across all seven layouts, GST split and IGST.
    for index in range(12):
        plan.append(
            {
                "layout": layouts[index % len(layouts)],
                "tax_mode": "cgst_sgst" if index % 2 == 0 else "igst",
                "rate": (5, 12, 18, 28)[index % 4],
                "show_sub_total": index % 3 == 0,
                "next_line_values": index % 4 == 1,
                "money_style": "indian" if index % 5 else "plain",
            }
        )

    # 13-20: SUB TOTAL and GRAND TOTAL both printed, both layouts of value
    # placement. This is the pair that posts a bill short by exactly its tax.
    for index in range(8):
        plan.append(
            {
                "layout": layouts[index % len(layouts)],
                "tax_mode": "cgst_sgst" if index % 2 else "igst",
                "rate": (12, 18)[index % 2],
                "show_sub_total": True,
                "next_line_values": index % 2 == 0,
                "big": True,
            }
        )

    # 21-26: non-GST bills. NO tax line anywhere -> tax is ABSENT, never zero.
    for index in range(6):
        plan.append(
            {
                "layout": layouts[index % len(layouts)],
                "tax_mode": "none",
                "rate": 0,
                "show_sub_total": index % 2 == 0,
                "next_line_values": index % 3 == 0,
            }
        )

    # 27-30: no invoice number printed -> invoice_number is ABSENT.
    for index in range(4):
        plan.append(
            {
                "layout": layouts[(index + 2) % len(layouts)],
                "tax_mode": "cgst_sgst" if index % 2 else "igst",
                "rate": 18,
                "omit_number": True,
                "show_sub_total": index % 2 == 0,
            }
        )

    # 31-34: two different totals printed -> total is AMBIGUOUS.
    for index in range(4):
        plan.append(
            {
                "layout": layouts[(index + 1) % len(layouts)],
                "tax_mode": "igst" if index % 2 else "cgst_sgst",
                "rate": (18, 28)[index % 2],
                "ambiguous": True,
                "show_sub_total": index % 2 == 0,
            }
        )

    # 35-40: deliberately poor quality. Moderate stays readable; severe does
    # not, and the severe ones are marked UNREADABLE only after a human looked.
    for index in range(6):
        plan.append(
            {
                "layout": layouts[(index + 3) % len(layouts)],
                "tax_mode": "cgst_sgst" if index % 2 else "igst",
                "rate": 18,
                "degrade": "moderate" if index < 3 else "severe",
                "show_sub_total": index % 2 == 0,
            }
        )

    # 41-44: no date printed -> invoice_date is ABSENT.
    for index in range(4):
        plan.append(
            {
                "layout": layouts[(index + 4) % len(layouts)],
                "tax_mode": "igst",
                "rate": 12,
                "omit_date": True,
            }
        )

    # 45-48: multi-page. Money block on the LAST page only.
    for index in range(4):
        plan.append(
            {
                "layout": "multipage_continuation",
                "tax_mode": "cgst_sgst" if index % 2 else "igst",
                "rate": 18,
                "multipage": True,
                "show_sub_total": True,
                "next_line_values": index % 2 == 0,
            }
        )

    for index, spec in enumerate(plan, start=1):
        party, address = PARTIES[rng.randrange(len(PARTIES))]
        buyer = BUYERS[rng.randrange(len(BUYERS))]

        count = 14 if spec.get("multipage") else rng.randint(2, 5)
        lines = tuple(
            Line(
                description=GOODS[rng.randrange(len(GOODS))][0],
                qty=rng.randint(1, 9),
                unit_paise=GOODS[rng.randrange(len(GOODS))][1],
            )
            for _ in range(count)
        )
        if spec.get("big"):
            # Force the total over one lakh so Indian grouping actually differs
            # from thousands grouping: 1,23,456.78 and not 123,456.78.
            lines = (*lines, Line("Bulk consignment lot", 9, 2500000))

        day, month, year = (
            rng.randint(1, 28),
            rng.randint(1, 12),
            rng.randint(2023, 2026),
        )
        separator = "/" if index % 2 else "-"
        date_text = f"{day:02d}{separator}{month:02d}{separator}{year}"

        number = f"{party.split()[0][:3].upper()}/{year}/{1000 + index}"

        bills.append(
            Bill(
                document_id=f"synthetic-{index:03d}",
                layout=str(spec["layout"]),
                party=party,
                party_address=address,
                buyer=buyer,
                invoice_number=None if spec.get("omit_number") else number,
                date_text=None if spec.get("omit_date") else date_text,
                lines=lines,
                tax_mode=str(spec["tax_mode"]),  # type: ignore[arg-type]
                tax_rate=int(spec["rate"]),  # type: ignore[call-overload]
                show_sub_total=bool(spec.get("show_sub_total")),
                next_line_values=bool(spec.get("next_line_values")),
                degrade=str(spec.get("degrade", "none")),  # type: ignore[arg-type]
                pages=4 if spec.get("multipage") else 1,
                money_style=str(spec.get("money_style", "indian")),  # type: ignore[arg-type]
            )
        )

    # The ambiguous ones need a SECOND total that disagrees with the first.
    for bill, spec in zip(bills, plan, strict=True):
        if spec.get("ambiguous"):
            bill.ambiguous_total_paise = bill.total_paise + rng.randint(100, 90000)
    return bills


# ---------------------------------------------------------------------------
# Ground truth for the synthetic half, written from the Bill, not the pixels.
# ---------------------------------------------------------------------------
SEVERE_UNREADABLE = ("party", "invoice_date", "total", "tax", "invoice_number")


def synthetic_ground_truth(bill: Bill) -> dict[str, dict[str, object]]:
    last_page = bill.pages
    truth: dict[str, dict[str, object]] = {}

    def entry(
        status: Status, value: object, evidence: str, page: int
    ) -> dict[str, object]:
        return {"status": status, "value": value, "evidence": evidence, "page": page}

    severe = bill.degrade == "severe"

    if severe:
        reason = (
            "downscaled to 22% and blended 34% with uniform noise, then rotated "
            "2.4 degrees; a human reader confirmed the glyphs cannot be read"
        )
        for name in SEVERE_UNREADABLE:
            truth[name] = entry("UNREADABLE", None, reason, 1)
        return truth

    truth["party"] = entry(
        "PRESENT",
        bill.party,
        bill.evidence.get("party", f"'{bill.party}' printed as the seller"),
        1,
    )

    if bill.invoice_number is None:
        truth["invoice_number"] = entry(
            "ABSENT",
            None,
            "no invoice number, bill number or 'No.' appears anywhere on the document",
            1,
        )
    else:
        truth["invoice_number"] = entry(
            "PRESENT", bill.invoice_number, bill.evidence.get("invoice_number", ""), 1
        )

    if bill.date_text is None:
        truth["invoice_date"] = entry(
            "ABSENT", None, "no date is printed anywhere on the document", 1
        )
    else:
        truth["invoice_date"] = entry(
            "PRESENT", bill.date_text, bill.evidence.get("invoice_date", ""), 1
        )

    tax = bill.tax_paise
    if tax is None:
        truth["tax"] = entry(
            "ABSENT",
            None,
            "non-GST bill: no CGST, SGST, IGST, VAT or tax line is printed. "
            "ABSENT, NOT zero - nobody printed a zero",
            last_page,
        )
    else:
        truth["tax"] = entry(
            "PRESENT",
            {"paise": tax, "text": bill.money(tax)},
            bill.evidence.get("tax", ""),
            last_page,
        )

    if bill.ambiguous_total_paise is not None:
        truth["total"] = entry(
            "AMBIGUOUS",
            {
                "candidates_paise": [bill.total_paise, bill.ambiguous_total_paise],
                "candidates_text": [
                    bill.money(bill.total_paise),
                    bill.money(bill.ambiguous_total_paise),
                ],
            },
            bill.evidence.get("total", ""),
            last_page,
        )
    else:
        truth["total"] = entry(
            "PRESENT",
            {"paise": bill.total_paise, "text": bill.money(bill.total_paise)},
            bill.evidence.get("total", ""),
            last_page,
        )

    if bill.show_sub_total:
        truth["total"]["sub_total_also_printed"] = {
            "paise": bill.net_paise,
            "text": bill.money(bill.net_paise),
            "warning": (
                "SUB TOTAL is printed on this page and is NOT the total. Reading "
                "it as the total posts the bill short by exactly the tax."
            ),
        }
    return truth


# ---------------------------------------------------------------------------
# The real half. Route 1.
# ---------------------------------------------------------------------------
VOXEL_REPO = "Voxel51/high-quality-invoice-images-for-ocr"
VOXEL_REV = "d21f03cfeea2b330e15a229883c66d7ebece8e69"

VOXEL_LICENCE = {
    "dataset_name": VOXEL_REPO,
    "version": VOXEL_REV,
    "license": "ODbL-1.0 (database) + DbCL-1.0 (contents/images)",
    "license_url": "https://opendatacommons.org/licenses/dbcl/1-0/",
    "attribution": (
        "Osama Hosam Abdellatif (original Kaggle dataset); "
        "Harpreet Sahota / Voxel51 (FiftyOne port)"
    ),
}

COMMONS_LICENCE = {
    "dataset_name": "Wikimedia Commons, Category:Invoices from India",
    "version": "pageid 36377875",
    "license": "Public domain (PD-text)",
    "license_url": "https://commons.wikimedia.org/wiki/Commons:Licensing",
    "attribution": "Alliance francaise de Pondichery; uploaded by Lionel Scheepmans",
}

# Upstream files, in the order this script downloads them. Fixed, so a rerun
# fetches the same thirteen and the hand-read table below still lines up.
VOXEL_FILES = [
    "data/batch1-0001.jpg",
    "data/batch1-0109.jpg",
    "data/batch1-0218.jpg",
    "data/batch1-0327.jpg",
    "data/batch1-0436.jpg",
    "data/batch1-0544.jpg",
    "data/batch1-0728.jpg",
    "data/batch1-0837.jpg",
    "data/batch1-0946.jpg",
    "data/batch1-1054.jpg",
    "data/batch1-1163.jpg",
    "data/batch1-1272.jpg",
    "data/batch1-1381.jpg",
]

# HAND-READ. Every row below was typed after opening the image and reading it.
# `total_text` and `tax_text` are what the PAGE prints, which is NOT always what
# the upstream annotation says - see the module docstring. Values are paise.
HAND_READ: dict[str, dict[str, object]] = {
    "real-voxel51-01": {
        "party": "Andrews, Kirby and Valdez",
        "number": "51109338",
        "date": "04/13/2013",
        "total_paise": 620419,
        "total_text": "6 204,19",
        "tax_paise": 56402,
        "tax_text": "564,02",
        "sub_total_text": "5 640,17",
    },
    "real-voxel51-02": {
        "party": "Campos-Hawkins",
        "number": "26622011",
        "date": "01/08/2020",
        "total_paise": 98670,
        "total_text": "986,70",
        "tax_paise": 8970,
        "tax_text": "89,70",
        "sub_total_text": "897,00",
    },
    "real-voxel51-03": {
        "party": "Morales-Snyder",
        "number": "26413291",
        "date": "01/17/2019",
        "total_paise": 7858,
        "total_text": "78,58",
        "tax_paise": 714,
        "tax_text": "7,14",
        "sub_total_text": "71,44",
    },
    "real-voxel51-04": {
        "party": "Mcpherson, Stark and Rodriguez",
        "number": "35163496",
        "date": "10/28/2019",
        "total_paise": 79210,
        "total_text": "792,10",
        "tax_paise": 7201,
        "tax_text": "72,01",
        "sub_total_text": "720,09",
    },
    "real-voxel51-05": {
        "party": "Padilla, Webb and Pearson",
        "number": "56427222",
        "date": "11/26/2018",
        "total_paise": 30793,
        "total_text": "307,93",
        "tax_paise": 2799,
        "tax_text": "27,99",
        "sub_total_text": "279,94",
    },
    "real-voxel51-06": {
        "party": "Vasquez-Brennan",
        "number": "68743594",
        "date": "11/14/2015",
        "total_paise": 4595,
        "total_text": "45,95",
        "tax_paise": 418,
        "tax_text": "4,18",
        "sub_total_text": "41,77",
    },
    "real-voxel51-07": {
        "party": "Jackson-Martinez",
        "number": "87278875",
        "date": "07/06/2019",
        "total_paise": 7561,
        "total_text": "75,61",
        "tax_paise": 687,
        "tax_text": "6,87",
        "sub_total_text": "68,74",
    },
    "real-voxel51-08": {
        "party": "Barton, Garcia and Richards",
        "number": "22537855",
        "date": "07/03/2013",
        "total_paise": 1055900,
        "total_text": "10 559,00",
        "tax_paise": 95991,
        "tax_text": "959,91",
        "sub_total_text": "9 599,09",
    },
    "real-voxel51-09": {
        "party": "Gross, Williams and Robinson",
        "number": "90885385",
        "date": "12/26/2012",
        "total_paise": 39796,
        "total_text": "397,96",
        "tax_paise": 3618,
        "tax_text": "36,18",
        "sub_total_text": "361,78",
    },
    "real-voxel51-10": {
        "party": "Smith and Sons",
        "number": "20653012",
        "date": "08/03/2013",
        "total_paise": 64015,
        "total_text": "640,15",
        "tax_paise": 5820,
        "tax_text": "58,20",
        "sub_total_text": "581,95",
    },
    "real-voxel51-11": {
        "party": "Taylor-Davis",
        "number": "18393770",
        "date": "07/16/2013",
        "total_paise": 433411,
        "total_text": "4 334,11",
        "tax_paise": 39401,
        "tax_text": "394,01",
        "sub_total_text": "3 940,10",
    },
    "real-voxel51-12": {
        "party": "Moore and Sons",
        "number": "72415673",
        "date": "10/29/2011",
        "total_paise": 6156850,
        "total_text": "61 568,50",
        "tax_paise": 559714,
        "tax_text": "5 597,14",
        "sub_total_text": "55 971,36",
    },
    "real-voxel51-13": {
        "party": "Forbes LLC",
        "number": "89305462",
        "date": "08/23/2015",
        "total_paise": 53867,
        "total_text": "538,67",
        "tax_paise": 4897,
        "tax_text": "48,97",
        "sub_total_text": "489,70",
    },
}


def real_ground_truth(document_id: str) -> dict[str, dict[str, object]]:
    """Ground truth for a real document, from the hand-read table above."""
    if document_id == "real-commons-01":
        # Read by hand off the scan. A yellow paper receipt is stapled over the
        # lower left of the invoice; it obscures none of the five fields.
        return {
            "party": {
                "status": "PRESENT",
                "value": "Alliance francaise de Pondichery",
                "evidence": "letterhead top left, beside the 'af' logo",
                "page": 1,
            },
            "invoice_date": {
                "status": "PRESENT",
                "value": "23-10-2014",
                "evidence": "boxed 'DATE  23-10-2014' at the top right",
                "page": 1,
            },
            "invoice_number": {
                "status": "PRESENT",
                "value": "320/10/2014/OL/DC",
                "evidence": "boxed 'Invoice #  320/10/2014/OL/DC' at the top right",
                "page": 1,
            },
            "total": {
                "status": "PRESENT",
                "value": {"paise": 400000, "text": "4,000.00", "currency": "INR"},
                "evidence": "'Total  Rs 4,000.00' in the boxed row below the items",
                "page": 1,
                "sub_total_also_printed": {
                    "paise": 400000,
                    "text": "4,000.00",
                    "warning": (
                        "This document prints BOTH 'Sub Total' and 'Total' and on "
                        "this bill they are EQUAL, because there is no tax. Equal "
                        "here is a coincidence of this document, not a rule."
                    ),
                },
            },
            "tax": {
                "status": "ABSENT",
                "value": None,
                "evidence": (
                    "no tax row of any kind is printed: no GST, CGST, SGST, IGST, "
                    "VAT or service tax label appears. ABSENT, NOT zero"
                ),
                "page": 1,
            },
        }

    read = HAND_READ[document_id]
    return {
        "party": {
            "status": "PRESENT",
            "value": read["party"],
            "evidence": "under the 'Seller:' heading in the left column",
            "page": 1,
        },
        "invoice_date": {
            "status": "PRESENT",
            "value": read["date"],
            "evidence": f"'Date of issue:  {read['date']}' below title, MM/DD/YYYY",
            "page": 1,
        },
        "invoice_number": {
            "status": "PRESENT",
            "value": read["number"],
            "evidence": f"'Invoice no: {read['number']}' as the page title",
            "page": 1,
        },
        "total": {
            "status": "PRESENT",
            "value": {
                "paise": read["total_paise"],
                "text": read["total_text"],
                "currency": "USD",
            },
            "evidence": (
                f"SUMMARY table, 'Total' row, 'Gross worth' column: "
                f"$ {read['total_text']}"
            ),
            "page": 1,
            "sub_total_also_printed": {
                "text": read["sub_total_text"],
                "warning": (
                    "The same 'Total' row also prints 'Net worth' "
                    f"$ {read['sub_total_text']}, which is the PRE-TAX figure. "
                    "Reading the leftmost money cell posts the bill short by "
                    "exactly the tax."
                ),
            },
        },
        "tax": {
            "status": "PRESENT",
            "value": {
                "paise": read["tax_paise"],
                "text": read["tax_text"],
                "currency": "USD",
            },
            "evidence": (
                f"SUMMARY table, 'Total' row, 'VAT' column: $ {read['tax_text']}"
            ),
            "page": 1,
        },
    }


def fetch(url: str, destination: pathlib.Path) -> tuple[str, str]:
    """Download one file. Returns (download_status, sha256)."""
    request = urllib.request.Request(  # noqa: S310 - https only, literal hosts above
        url, headers={"User-Agent": "accountant-dad-corpus/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as handle:  # noqa: S310
            blob = handle.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        return f"failed: {type(exc).__name__}", ""
    destination.write_bytes(blob)
    return "downloaded", hashlib.sha256(blob).hexdigest()


# ---------------------------------------------------------------------------
# Sources inspected and REJECTED. Recorded because a rejection nobody wrote
# down gets re-tried by the next person, who reaches the same dead end.
# ---------------------------------------------------------------------------
# The CBIC PDF was fetched once, to READ its licence terms. It is not in the
# corpus and not redistributed; the hash is recorded so the next person can
# confirm they are looking at the same file before re-arguing the licence.
CBIC_SHA256 = "0d2b4fb800381a0c263c1ac4c4cf0bd9d9a75363dc6ec5aee59e6a72b5520735"

REJECTED_SOURCES = [
    {
        "document_id": "rejected-mychen76-invoices-and-receipts_ocr_v1",
        "source_url": "https://huggingface.co/datasets/mychen76/invoices-and-receipts_ocr_v1",
        "dataset_name": "mychen76/invoices-and-receipts_ocr_v1",
        "version": "83835c87346de32ac9223bdce5264e69ef3366ad",
        "license": "NONE DECLARED",
        "license_url": "",
        "attribution": "",
        "real_or_synthetic": "real-public",
        "document_type": "not-downloaded",
        "download_status": (
            "rejected: no licence anywhere. The API returns no license tag and no "
            "cardData.license, and the card body is the unedited HuggingFace stub "
            "'More Information needed'. Unclear licence, so not downloaded"
        ),
    },
    {
        "document_id": "rejected-philschmid-ocr-invoice-data",
        "source_url": "https://huggingface.co/datasets/philschmid/ocr-invoice-data",
        "dataset_name": "philschmid/ocr-invoice-data",
        "version": "1e11a9e69e6ef122dc2ad718e29b160003d6f287",
        "license": "NONE DECLARED",
        "license_url": "",
        "attribution": "",
        "real_or_synthetic": "real-public",
        "document_type": "not-downloaded",
        "download_status": (
            "rejected: no licence declared, same as mychen76 above. It is also the "
            "SAME DATA - identical parquet filenames and identical split byte counts "
            "(2043/125/70 rows, 511196143 bytes) - so it could not have supplied "
            "independent documents even had it been licensed"
        ),
    },
    {
        "document_id": "rejected-GokulRajaR-invoice-ocr-json",
        "source_url": "https://huggingface.co/datasets/GokulRajaR/invoice-ocr-json",
        "dataset_name": "GokulRajaR/invoice-ocr-json",
        "version": "284480bf13f2f91d90451fe4fe7138c11a7efac4",
        "license": "UNCLEAR",
        "license_url": "",
        "attribution": "",
        "real_or_synthetic": "real-public",
        "document_type": "not-downloaded",
        "download_status": (
            "rejected: the card says 'CC BY-SA 4.0 (or appropriate open-source "
            "license used by the base dataset)'. A licence with 'or appropriate' in "
            "it names no licence, and the API carries no license tag to settle it. "
            "Second, independent reason: its annotations were generated by GPT-4o "
            "mini with only spot-checking, so they are an EXTRACTOR'S OUTPUT and are "
            "barred from being ground truth here"
        ),
    },
    {
        "document_id": "rejected-longmaodata-Invoice-annotation",
        "source_url": "https://huggingface.co/datasets/longmaodata/Invoice-annotation",
        "dataset_name": "longmaodata/Invoice-annotation",
        "version": "unknown",
        "license": "UNKNOWN - not readable without an account",
        "license_url": "",
        "attribution": "",
        "real_or_synthetic": "real-public",
        "document_type": "not-downloaded",
        "download_status": (
            "rejected: the dataset API answers HTTP 401 Unauthorized, so the card "
            "and its licence cannot be read without signing in. Logging in was out "
            "of scope, and an unreadable licence is an unclear licence"
        ),
    },
    {
        "document_id": "rejected-cbic-draft-formats-under-invoice-rules",
        "source_url": "https://cbic-gst.gov.in/aces/Documents/draft-formats-under-invoice-rules.pdf",
        "dataset_name": "CBIC GST portal, draft formats under invoice rules",
        "version": f"sha256:{CBIC_SHA256}",
        "license": "UNCLEAR - copyright asserted, no reuse grant found",
        "license_url": "https://cbic-gst.gov.in/terms.html",
        "attribution": "",
        "real_or_synthetic": "real-public",
        "document_type": "not-downloaded",
        "download_status": (
            "rejected: the file itself is public and robots.txt allows fetching "
            "(User-agent: * / Disallow:), and it WAS fetched to read - 4 pages, "
            "88824 bytes, a blank Form GST INV-1 template. But the site asserts "
            "'(c) 2020. Central Board of Indirect Taxes and Customs' and neither "
            "terms.html nor the disclaimer grants any right to reproduce or "
            "redistribute; the only permission given is to hyperlink. Redistributing "
            "a copy in a corpus is not hyperlinking, so it is not in the corpus"
        ),
    },
]


# ---------------------------------------------------------------------------
# Assembly.
# ---------------------------------------------------------------------------
MANIFEST_COLUMNS = [
    "document_id",
    "source_url",
    "dataset_name",
    "version",
    "license",
    "license_url",
    "attribution",
    "real_or_synthetic",
    "document_type",
    "download_status",
]


# Pillow's PDF driver stamps the WALL CLOCK into every file it writes, as
# `/CreationDate (D:20260815091713Z)` and a matching `/ModDate`. MEASURED: that
# alone made 4 of 48 synthetic documents differ between two runs a second
# apart, while the other 44 - all PNG - were byte-identical. A corpus whose
# bytes move on their own cannot be hash-pinned in a manifest, so the two
# stamps are rewritten to a fixed value below.
#
# The replacement is the SAME WIDTH as what it replaces (17 characters), which
# is why this is safe to do on the finished bytes: every xref offset in the
# file still points where it did, so the PDF stays valid without rebuilding it.
_PDF_DATE = re.compile(rb"D:\d{14}Z")
_PDF_FIXED_DATE = b"D:19700101000000Z"


def save_pages(pages: list[Image.Image], stem: pathlib.Path) -> pathlib.Path:
    if len(pages) == 1:
        out = stem.with_suffix(".png")
        pages[0].save(out)
        return out
    out = stem.with_suffix(".pdf")
    pages[0].convert("RGB").save(
        out, save_all=True, append_images=[p.convert("RGB") for p in pages[1:]]
    )
    blob = out.read_bytes()
    pinned = _PDF_DATE.sub(_PDF_FIXED_DATE, blob)
    if len(pinned) != len(blob):
        raise RuntimeError(
            f"date pinning changed {out.name} length "
            f"{len(blob)} -> {len(pinned)}; xref offsets would be wrong"
        )
    out.write_bytes(pinned)
    return out


class Built(TypedDict):
    """What `generate` hands back: the manifest rows and the ground truth.

    A `dict[str, object]` here meant every caller had to re-discover that
    `built["manifest"]` is a list of CSV rows, and the `isinstance` assert that
    did the re-discovering could not say what the rows contained.
    """

    manifest: list[dict[str, str]]
    ground_truth: dict[str, object]


def generate(corpus_dir: pathlib.Path, *, fetch_real: bool) -> Built:
    corpus_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)  # noqa: S311 - layout variety, not security

    manifest: list[dict[str, str]] = []
    ground_truth: dict[str, object] = {}

    # --- Route 1: real, licensed documents -------------------------------
    for index, upstream in enumerate(VOXEL_FILES, start=1):
        document_id = f"real-voxel51-{index:02d}"
        url = f"https://huggingface.co/datasets/{VOXEL_REPO}/resolve/{VOXEL_REV}/{upstream}"
        destination = corpus_dir / f"{document_id}.jpg"
        if fetch_real and not destination.exists():
            status, _ = fetch(url, destination)
        else:
            status = "already-present" if destination.exists() else "skipped"
        manifest.append(
            {
                "document_id": document_id,
                "source_url": url,
                **{k: str(v) for k, v in VOXEL_LICENCE.items()},
                "real_or_synthetic": "real-public",
                "document_type": "synthetic-generated invoice image, English, USD",
                "download_status": status,
            }
        )
        ground_truth[document_id] = real_ground_truth(document_id)

    commons_url = (
        "https://upload.wikimedia.org/wikipedia/commons/d/dc/Facture_et_re%C3%A7u_1.png"
    )
    commons_dest = corpus_dir / "real-commons-01.png"
    if fetch_real and not commons_dest.exists():
        commons_status, _ = fetch(commons_url, commons_dest)
    else:
        commons_status = "already-present" if commons_dest.exists() else "skipped"
    manifest.append(
        {
            "document_id": "real-commons-01",
            "source_url": "https://commons.wikimedia.org/wiki/File:Facture_et_re%C3%A7u_1.png",
            **{k: str(v) for k, v in COMMONS_LICENCE.items()},
            "real_or_synthetic": "real-public",
            "document_type": "scanned Indian invoice, INR, Sub Total+Total, no tax",
            "download_status": commons_status,
        }
    )
    ground_truth["real-commons-01"] = real_ground_truth("real-commons-01")

    # --- Route 2: synthetic ----------------------------------------------
    for bill in build_bills(rng):
        pages = RENDERERS[bill.layout](bill, rng)
        written = save_pages(pages, corpus_dir / bill.document_id)
        bill.pages = len(pages)
        ground_truth[bill.document_id] = synthetic_ground_truth(bill)
        quirks = [bill.layout, f"tax={bill.tax_mode}"]
        if bill.show_sub_total:
            quirks.append("SUB TOTAL and GRAND TOTAL both printed")
        if bill.next_line_values:
            quirks.append("values on the NEXT line")
        if bill.ambiguous_total_paise is not None:
            quirks.append("two different totals")
        if bill.degrade != "none":
            quirks.append(f"degraded={bill.degrade}")
        if len(pages) > 1:
            quirks.append(f"{len(pages)} pages")
        manifest.append(
            {
                "document_id": bill.document_id,
                "source_url": f"generated by scripts/build_problem1_corpus.py:{SEED}",
                "dataset_name": "accountant-dad problem1 synthetic corpus",
                "version": f"seed={SEED}",
                "license": "CC0-1.0 (generated here, no third-party content)",
                "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                "attribution": "accountant-dad",
                "real_or_synthetic": "synthetic",
                "document_type": "; ".join(quirks),
                "download_status": f"generated: {written.name}",
            }
        )

    for rejected in REJECTED_SOURCES:
        manifest.append({k: str(rejected[k]) for k in MANIFEST_COLUMNS})

    return {"manifest": manifest, "ground_truth": ground_truth}


def write_artifacts(built: Built, artifacts_dir: pathlib.Path) -> None:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    manifest = built["manifest"]

    path = artifacts_dir / "problem1_corpus_manifest.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        writer.writerows(manifest)

    truth_path = artifacts_dir / "problem1_ground_truth.json"
    truth_path.write_text(
        json.dumps(built["ground_truth"], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=pathlib.Path, default=CORPUS_DIR)
    parser.add_argument("--artifacts-dir", type=pathlib.Path, default=ARTIFACTS_DIR)
    parser.add_argument("--no-fetch", action="store_true", help="synthetic only")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="re-render into a temp dir and prove the bytes are identical",
    )
    args = parser.parse_args(argv)

    if args.verify:
        with tempfile.TemporaryDirectory() as tmp:
            first = pathlib.Path(tmp) / "a"
            generate(first, fetch_real=False)
            second = pathlib.Path(tmp) / "b"
            generate(second, fetch_real=False)
            mismatched: list[str] = []
            for left in sorted(first.glob("synthetic-*")):
                right = second / left.name
                if (
                    hashlib.sha256(left.read_bytes()).hexdigest()
                    != hashlib.sha256(right.read_bytes()).hexdigest()
                ):
                    mismatched.append(left.name)
            count = len(list(first.glob("synthetic-*")))
            if mismatched:
                print(f"NOT DETERMINISTIC: {len(mismatched)} of {count} differ")
                return 1
            print(
                f"deterministic: {count} synthetic documents identical across two runs"
            )
            return 0

    built = generate(args.corpus_dir, fetch_real=not args.no_fetch)
    write_artifacts(built, args.artifacts_dir)
    manifest = built["manifest"]
    synthetic = [r for r in manifest if r["real_or_synthetic"] == "synthetic"]
    real = [
        r
        for r in manifest
        if r["real_or_synthetic"] == "real-public"
        and not r["document_id"].startswith("rejected-")
    ]
    print(f"real     {len(real)}")
    print(f"synthetic{len(synthetic):>3}")
    print(f"rejected {len(REJECTED_SOURCES)} sources")
    print(f"corpus   {args.corpus_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

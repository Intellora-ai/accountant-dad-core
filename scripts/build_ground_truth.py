#!/usr/bin/env python3
"""Phase 8 PR-1 — the generated ground-truth corpus, and the scorer over it.

THE PROBLEM THIS SOLVES
-----------------------
"100/100 processed, complete or explicit not_found" is a LIVENESS check. A stub
that answers `not_found` for every field passes it while reading nothing at all,
so it cannot tell a working reader from a dead one. Measuring accuracy instead
normally needs labelled invoices, which needs real customer bills, which is
`H-02` and is not supplied.

The way out is to run the arrow the other way:

    canonical JSON  ->  rendered document  ->  reader  ->  compare to canonical

The expected answer is never copied from what a reader said. It is DERIVED from
the structured truth the document was rendered FROM, by `expected_from` below,
which is the only function in this repository allowed to produce an `expected`
block. So the right answer is known without anybody labelling anything.

WHAT THE CORPUS IS AND IS NOT
-----------------------------
It is an ENGINEERING BENCHMARK: `S2_ENGINEERING_BENCHMARK`. Every document in
it was generated here. It measures whether a backend can recover fields from a
document whose fields are known.

It is NOT real-customer accuracy and must never be reported as such. Generated
documents are not bills. Real-bill accuracy stays `NOT_MEASURED` until `H-02`
supplies a labelled corpus, and the production backend stays `NOT_SELECTED`
(`H-01`).

WHY THE RENDERERS ARE HAND-WRITTEN
----------------------------------
`pyproject.toml` declares `dependencies = []`, and exit 7.3 forbids a reader.
No PDF, image or DOCX library may be added. So each format is written out of
the standard library:

    text  UTF-8 bytes
    PDF   PDF 1.4 by hand: objects, a Helvetica content stream, a real xref
          table whose offsets are computed from the emitted bytes
    PNG   real PNG: IHDR/IDAT/IEND, zlib-compressed scanlines, CRC32 per chunk,
          with the invoice text drawn as glyph pixels from the 5x7 font below
    DOCX  real OOXML: a `zipfile` holding [Content_Types].xml, _rels/.rels,
          word/document.xml and its rels part
    JPG   HONESTLY INCOMPLETE. See `JPG_LIMITATION`.

`JPG_LIMITATION`: a baseline JPEG needs a DCT plus Huffman entropy coding, and
with no image library permitted there is no way to VERIFY that anything this
file emitted actually decodes. Shipping an unverifiable encoder and calling its
output a JPEG would be a claim with no measurement behind it. So the JPG cases
are real JFIF-framed containers — SOI, APP0/JFIF, COM, EOI — carrying the
invoice text in a comment segment and NO encoded image data. They are not
photographs. The JPG pixel slice is `BLOCKED`, and because an OCR backend can
recover nothing from them, the reachable ceiling on the 100-case accuracy gate
is 80/100 until that slice is unblocked.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That the PDFs open in a PDF viewer or the PNGs display in an image viewer. No
library is permitted to check, so what is verified is structural: xref offsets
that point at the objects they claim, CRC32s that match their chunks, a zip the
standard library can open. Visual rendering is `NOT_MEASURED`.

That any backend is any good. Nothing is wired to a reader. The scorer below is
run against `StubExtractor`, `UnavailableExtractor` and `TypedTextExtractor`,
and the first two are expected to score zero — that is the corpus working.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import struct
import sys
import zipfile
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, TypeGuard

#: The sentinel `accountant/extract/adapter.py` writes into `per_field_source`
#: when a field has no value. Spelled here rather than imported so the scorer
#: does not drag the production package into a corpus build; the two are
#: checked against each other in `tests/test_ground_truth_integrity.py`.
NOT_FOUND: Final = "not_found"

REPO: Final = Path(__file__).resolve().parent.parent
CORPUS_ROOT: Final = REPO / "artifacts" / "ground_truth"

#: The label the owner set for this class of result. Never "real accuracy".
BENCHMARK_LABEL: Final = "S2_ENGINEERING_BENCHMARK"

#: Every case carries this. The document was generated from canonical truth.
SOURCE_LABEL: Final = "GENERATED_GROUND_TRUTH"

#: The Q5 vocabulary, which is a separate axis from `source_label`. Every case
#: here is `SYNTHETIC_EVIDENCE` because every BYTE was generated. Some cases
#: carry a supplier name copied verbatim out of a published UK government file;
#: that is recorded per case as `content_source` and is checked by
#: `tests/test_ground_truth_integrity.py` against the committed original. It
#: does not change the label. The label describes the bytes, not the words, and
#: calling a generated PDF third-party evidence because a real name appears
#: inside it is exactly the mislabelling Q5 forbids.
CORPUS_LABEL: Final = "SYNTHETIC_EVIDENCE"

#: Bumped whenever a change here would change any generated byte. It is inside
#: the generation hash, so an old case file cannot silently pass a new build.
GENERATION_VERSION: Final = "1"

#: A constant, not a clock read. A rebuild has to be byte-identical or the
#: difference is a real change; a timestamp would make every rebuild differ and
#: the tamper check would have to be deleted to keep the suite green.
CREATED_AT: Final = "2026-08-10"

INPUT_TYPES: Final = ("text", "PDF", "PNG", "JPG", "DOCX")
CATEGORIES: Final = ("clean", "layout", "line_items", "tax_labels", "adversarial")
PER_CELL: Final = 4
CASE_COUNT: Final = len(INPUT_TYPES) * len(CATEGORIES) * PER_CELL

SUFFIX: Final = {
    "text": ".txt",
    "PDF": ".pdf",
    "PNG": ".png",
    "JPG": ".jpg",
    "DOCX": ".docx",
}

MIME: Final = {
    "text": "text/plain",
    "PDF": "application/pdf",
    "PNG": "image/png",
    "JPG": "image/jpeg",
    "DOCX": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
}

#: How faithfully each container carries the text a reader would have to find.
#: `container_only` is the honest word for "the format framing is real and the
#: image data is absent"; it is why the JPG slice is BLOCKED.
FIDELITY: Final = {
    "text": "native_text",
    "PDF": "text_operators",
    "PNG": "rendered_raster",
    "JPG": "container_only",
    "DOCX": "native_text",
}

BLOCKED_TYPES: Final = ("JPG",)

JPG_LIMITATION: Final = (
    "a baseline JPEG needs a DCT and Huffman entropy coding, and with no image "
    "library permitted nothing here can verify that the bytes decode; an "
    "unverifiable encoder would be a claim with no measurement behind it"
)

#: The fields the gate scores, in report order.
SCORED_FIELDS: Final = ("date", "party", "total_amount", "tax_amount", "line_items")

#: Owner-set. Reported, never enforced from inside pytest — a benchmark that
#: fails the build when a stub scores zero would be deleted within a week.
GATE_PER_FIELD: Final = 95


# =============================================================================
# SUPPLIERS COPIED VERBATIM FROM PUBLISHED UK GOVERNMENT FILES
# =============================================================================
#
# Q5 = C: UK government public data where it fits. It fits here. Every name
# below appears byte-for-byte in the committed fixture named beside it, which
# `tests/test_ground_truth_integrity.py` checks against the file rather than
# trusting this table. Provenance for each department — URL, licence, retrieval
# date — is already recorded in `accountant/ingest/sources.py` and is cited by
# code rather than restated here.

PUBLIC_SUPPLIERS: Final[tuple[tuple[str, str], ...]] = (
    ("SHEFFIELD CITY COUNCIL", "MHCLG"),
    ("BARKING & DAGENHAM LB", "MHCLG"),
    ("BEDFORD UNITARY AUTHORITY", "MHCLG"),
    ("CARE QUALITY COMMISSION (CQC) CQC033", "DHSC"),
    ("HEALTH RESEARCH AUTHORITY", "DHSC"),
    ("UK HEALTH SECURITY AGENCY (UKHSA)", "DHSC"),
    ("NHS COUNTER FRAUD AUTHORITY", "DHSC"),
    ("GREAT WESTERN RAILWAY", "DFT"),
    ("NORTHERN TRAINS LIMITED", "DFT"),
    ("GOVIA THAMESLINK RAILWAY LIMITED", "DFT"),
    ("BALFOUR BEATTY VINCI JV - HS2 (N2)", "DFT"),
    ("CAPITA BUSINESS SERVICES LTD", "DWP"),
    ("TELEPERFORMANCE LTD", "DWP"),
    ("SAS SOFTWARE LTD", "DWP"),
    ("SERCO LIMITED", "DWP"),
    ("WILTSHIRE COUNCIL", "DEFRA"),
    ("SUFFOLK COUNTY COUNCIL", "DEFRA"),
    ("CHICHESTER HARBOUR AONB", "DEFRA"),
    ("CORNWALL COUNCIL", "DEFRA"),
    ("KPMG LLP", "HMT"),
    ("ADVANCED PROPULSION CENTRE UK LTD", "DBT"),
    ("AEROSPACE TECHNOLOGY INSTITUTE", "DBT"),
)

#: Invented Indian vendors. The system's own domain, and the counterweight to a
#: corpus made only of UK department names.
INVENTED_SUPPLIERS: Final[tuple[str, ...]] = (
    "SHARMA TRADERS",
    "VERMA CEMENT WORKS",
    "GUPTA HARDWARE STORES",
    "KRISHNA ENTERPRISES",
    "DECCAN LOGISTICS PVT LTD",
    "PATEL STEEL AND TUBES",
    "RAO OFFICE SUPPLIES",
    "NARMADA PACKAGING CO",
    "IYER ELECTRICALS",
    "SINGH TRANSPORT COMPANY",
)

DESCRIPTIONS: Final[tuple[str, ...]] = (
    "PORTLAND CEMENT 53 GRADE",
    "OFFICE CHAIRS",
    "SITE SURVEY FEES",
    "ANNUAL SOFTWARE LICENCE",
    "COURIER AND FREIGHT",
    "PRINTED STATIONERY",
    "SCAFFOLDING HIRE",
    "ELECTRICAL FITTINGS",
    "CONSULTANCY RETAINER",
    "PACKAGING MATERIAL",
    "STEEL TUBES 40MM",
    "VEHICLE MAINTENANCE",
)


# =============================================================================
# A 5x7 BITMAP FONT — the only way to put readable text in a PNG with no
# imaging library. Drawing glyphs is generation, not image ANALYSIS: nothing
# here inspects a picture, it emits one.
# =============================================================================

GLYPH_W: Final = 5
GLYPH_H: Final = 7

FONT: Final[dict[str, tuple[str, ...]]] = {
    " ": (".....", ".....", ".....", ".....", ".....", ".....", "....."),
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": (".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."),
    "D": ("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": (".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"),
    "J": ("..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."),
    "K": ("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": ("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "Q": (".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": (".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": ("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": ("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": ("#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"),
    "X": ("#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"),
    "Y": ("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "Z": ("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
    "0": (".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": ("#####", "...#.", "..#..", "...#.", "....#", "#...#", ".###."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": ("..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."),
    ".": (".....", ".....", ".....", ".....", ".....", ".##..", ".##.."),
    ",": (".....", ".....", ".....", ".....", ".##..", ".##..", ".#..."),
    ":": (".....", ".##..", ".##..", ".....", ".##..", ".##..", "....."),
    "/": ("....#", "....#", "...#.", "..#..", ".#...", "#....", "#...."),
    "-": (".....", ".....", ".....", "#####", ".....", ".....", "....."),
    "(": ("...#.", "..#..", ".#...", ".#...", ".#...", "..#..", "...#."),
    ")": (".#...", "..#..", "...#.", "...#.", "...#.", "..#..", ".#..."),
    "#": (".#.#.", ".#.#.", "#####", ".#.#.", "#####", ".#.#.", ".#.#."),
    "&": (".##..", "#..#.", "#.#..", ".#...", "#.#.#", "#..#.", ".##.#"),
    "%": ("##..#", "##.#.", "..#..", ".#...", "#..##", ".#.##", "#...."),
    "+": (".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."),
    "=": (".....", ".....", "#####", ".....", "#####", ".....", "....."),
    "@": (".###.", "#...#", "#.###", "#.#.#", "#.###", "#....", ".###."),
    "*": (".....", "#.#.#", ".###.", "#####", ".###.", "#.#.#", "....."),
    "|": ("..#..", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
}

#: Bytes a raster case may contain. A case needing anything else is a build
#: failure, never a silently blank box on the page.
RASTERISABLE: Final = frozenset(FONT)


class UnrenderableGlyph(ValueError):
    """A character the 5x7 font cannot draw. Never drawn as a blank."""


# =============================================================================
# MONEY — Decimal throughout, because this repository refuses rounded invoice
# arithmetic everywhere else and a benchmark that rounds would grade the wrong
# answer as right.
# =============================================================================


def money(value: Decimal) -> str:
    """Two decimal places, no grouping. The canonical form of every amount."""
    return f"{value:.2f}"


def paise(text: str) -> int:
    """`"1180.00"` -> `118000`. Exact, or a build failure."""
    scaled = Decimal(text.replace(",", "")) * 100
    whole = int(scaled)
    if scaled != whole:
        raise ValueError(f"amount {text!r} is not a whole number of paise")
    return whole


def group_western(text: str) -> str:
    """`1180.00` -> `1,180.00`."""
    whole, _, frac = text.partition(".")
    out = f"{int(whole):,}"
    return f"{out}.{frac}" if frac else out


def group_indian(text: str) -> str:
    """`118000.00` -> `1,18,000.00`. Last three digits, then pairs."""
    whole, _, frac = text.partition(".")
    if len(whole) <= 3:
        head = whole
    else:
        tail, rest = whole[-3:], whole[:-3]
        pairs: list[str] = []
        while len(rest) > 2:
            pairs.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            pairs.insert(0, rest)
        head = ",".join([*pairs, tail])
    return f"{head}.{frac}" if frac else head


# =============================================================================
# THE CANONICAL CASE
# =============================================================================


@dataclass(frozen=True)
class Variation:
    """Every knob that changes the DOCUMENT without changing the truth.

    This is what keeps the corpus from being five identical copies of one
    invoice: the canonical numbers differ per case, and so does every one of
    these.
    """

    date_format: str
    currency: str
    grouping: str
    tax_label: str
    total_label: str
    layout: str
    subtotal_last: bool = False
    decoys: tuple[str, ...] = ()
    pages: int = 1
    rotate: bool = False
    ink: int = 0


@dataclass(frozen=True)
class Case:
    """One case: the truth, the knobs, and everything the manifest records."""

    case_id: str
    input_type: str
    category: str
    date: str
    party: str
    subtotal: str
    tax_amount: str
    total: str
    line_items: tuple[tuple[str, str], ...]
    gst_context: dict[str, str]
    variation: Variation
    content_source: str
    notes: str
    rule_ids: tuple[str, ...] = ()
    source_urls: tuple[str, ...] = ()

    @property
    def raw_fields(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "party": self.party,
            "subtotal": self.subtotal,
            "tax_amount": self.tax_amount,
            "total": self.total,
            "line_items": [{"description": d, "amount": a} for d, a in self.line_items],
        }

    @property
    def filename(self) -> str:
        return f"{self.case_id}{SUFFIX[self.input_type]}"


def expected_from(raw_fields: dict[str, Any]) -> dict[str, Any]:
    """The ONE function that may produce an `expected` block.

    It is a pure projection of the canonical truth. It cannot see a reader, it
    cannot see a document, and it takes no argument that a reader ever touched.
    `scripts/validate_ground_truth.py` recomputes it from `raw_fields` and
    refuses any case file whose stored `expected` differs — which is how
    "somebody pasted reader output into expected" becomes a red exit code
    rather than a corpus that grades itself.
    """
    return {
        "date": raw_fields["date"],
        "party": raw_fields["party"],
        "total_amount": raw_fields["total"],
        "tax_amount": raw_fields["tax_amount"],
        "line_items": raw_fields["line_items"],
    }


def canonical_json(payload: object) -> str:
    """Stable text for hashing. Sorted keys, no incidental whitespace."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def generation_hash(raw_fields: dict[str, Any], gst_context: dict[str, str]) -> str:
    """A hash over the TRUTH, not over the document and not over `expected`.

    Three things are locked to each other by the two hashes on a case file:
    `raw_fields` (this one), `expected` (recomputed by `expected_from`) and the
    rendered bytes (`sha256`). Editing any one of the three without rebuilding
    the other two breaks the ring, and the validator names which link failed.
    """
    payload = {
        "generation_version": GENERATION_VERSION,
        "raw_fields": raw_fields,
        "gst_context": gst_context,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# =============================================================================
# BUILDING THE 100 CASES — 5 input types x 5 categories x 4, both axes at 20
# =============================================================================

DATE_FORMATS: Final = ("iso", "dmy_slash", "dmy_dash", "dmy_long")
_MONTHS: Final = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)


def printed_date(iso: str, style: str) -> str:
    year, month, day = iso.split("-")
    if style == "iso":
        return iso
    if style == "dmy_slash":
        return f"{day}/{month}/{year}"
    if style == "dmy_dash":
        return f"{day}-{month}-{year}"
    if style == "dmy_long":
        return f"{day} {_MONTHS[int(month) - 1]} {year}"
    raise ValueError(f"unknown date style {style!r}")


TAX_LABELS: Final = (
    "GST",
    "TAX",
    "GST @ 18%",
    "CGST/SGST",
    "IGST",
    "OUTPUT TAX",
)
TOTAL_LABELS: Final = ("TOTAL", "GRAND TOTAL", "AMOUNT PAYABLE", "TOTAL DUE")
LAYOUTS: Final = ("block", "table", "right", "wrapped", "two_column")


def _variation(index: int, category: str, input_type: str) -> Variation:
    """Deterministic knobs. Different for every one of the 100 cases."""
    raster = input_type in {"PNG", "JPG"}
    base = Variation(
        date_format=DATE_FORMATS[index % len(DATE_FORMATS)],
        currency=("none", "rs", "pound")[index % 3],
        grouping="indian" if index % 3 == 2 else "western",
        tax_label=TAX_LABELS[index % len(TAX_LABELS)],
        total_label=TOTAL_LABELS[index % len(TOTAL_LABELS)],
        layout=LAYOUTS[index % len(LAYOUTS)],
    )
    if category == "clean":
        return Variation(
            date_format="iso",
            currency="none",
            grouping="western",
            tax_label="GST",
            total_label="TOTAL",
            layout="block",
        )
    if category == "layout":
        return Variation(
            date_format=base.date_format,
            currency=base.currency,
            grouping=base.grouping,
            tax_label="GST",
            total_label="TOTAL",
            layout=LAYOUTS[(index + 1) % len(LAYOUTS)],
            pages=2 if index % 4 == 3 else 1,
        )
    if category == "line_items":
        return Variation(
            date_format=base.date_format,
            currency=base.currency,
            grouping=base.grouping,
            tax_label="GST",
            total_label=base.total_label,
            layout="table" if index % 2 else "wrapped",
        )
    if category == "tax_labels":
        return Variation(
            date_format=base.date_format,
            currency=base.currency,
            grouping=base.grouping,
            tax_label=TAX_LABELS[(index + 2) % len(TAX_LABELS)],
            total_label=base.total_label,
            layout="block",
        )
    return Variation(
        date_format=("dmy_slash", "dmy_dash", "dmy_long", "iso")[index % 4],
        currency=("rs", "pound", "none", "rs")[index % 4],
        grouping="indian" if index % 2 == 0 else "western",
        tax_label=("CGST/SGST", "IGST", "GST @ 18%", "TAX")[index % 4],
        total_label=("AMOUNT PAYABLE", "TOTAL DUE", "GRAND TOTAL", "TOTAL")[index % 4],
        layout=("two_column", "right", "wrapped", "table")[index % 4],
        subtotal_last=True,
        decoys=_decoys(index),
        pages=2 if index % 4 == 1 else 1,
        rotate=raster and index % 4 == 2,
        ink=190 if raster and index % 4 == 3 else 0,
    )


def _decoys(index: int) -> tuple[str, ...]:
    """Numbers on the page that are NOT the answer. The adversarial half."""
    sets: tuple[tuple[str, ...], ...] = (
        (
            "PURCHASE ORDER: 4500091827",
            "TERMS: NET 30 DAYS. INTEREST 24% PER ANNUM AFTER DUE DATE.",
        ),
        (
            "PREVIOUS BALANCE: 9,999.00",
            "CREDIT NOTE APPLIED EARLIER: 250.00",
        ),
        (
            "ROUND OFF SHOWN FOR INFORMATION: 0.004",
            "UNROUNDED TAX WORKING: 188.998",
        ),
        (
            "DELIVERY CHALLAN 7781 DATED 02/01/2026",
            "CONTACT: 022 4004 1188",
        ),
    )
    return sets[index % len(sets)]


def _line_items(
    category: str, index: int, unit: Decimal
) -> tuple[tuple[str, str], ...]:
    """0, 1, 3 or 7 items, and a discount when the case is adversarial."""
    counts = {
        "clean": 1,
        "layout": 2,
        "line_items": (0, 1, 3, 7)[index % 4],
        "tax_labels": 1,
        "adversarial": (2, 3, 0, 2)[index % 4],
    }
    count = counts[category]
    if count == 0:
        return ()
    items: list[tuple[str, str]] = []
    running = Decimal(0)
    for i in range(count):
        amount = (unit * (i + 1)).quantize(Decimal("0.01"))
        items.append((DESCRIPTIONS[(index + i) % len(DESCRIPTIONS)], money(amount)))
        running += amount
    if category == "adversarial" and index % 4 == 1:
        # A discount is a negative line. It keeps `sum(items) == subtotal`
        # true, which is the conservation law the validator checks.
        cut = (running / 10).quantize(Decimal("0.01"))
        items.append(("VOLUME DISCOUNT", money(-cut)))
    return tuple(items)


def build_cases() -> tuple[Case, ...]:
    """The 100 cases. Deterministic: same inputs, same bytes, every run."""
    cases: list[Case] = []
    index = 0
    for input_type in INPUT_TYPES:
        for category in CATEGORIES:
            for slot in range(PER_CELL):
                cases.append(_one_case(index, input_type, category, slot))
                index += 1
    if len(cases) != CASE_COUNT:
        raise AssertionError(f"built {len(cases)} cases, expected {CASE_COUNT}")
    return tuple(cases)


def _one_case(index: int, input_type: str, category: str, slot: int) -> Case:
    public = index % 2 == 0
    if public:
        party, dept = PUBLIC_SUPPLIERS[(index // 2) % len(PUBLIC_SUPPLIERS)]
        content_source = f"verbatim supplier name from the published {dept} file"
    else:
        party = INVENTED_SUPPLIERS[(index // 2) % len(INVENTED_SUPPLIERS)]
        content_source = "invented vendor name"

    unit = Decimal(250 + index * 37) / Decimal(2)
    items = _line_items(category, index, unit)

    if items:
        subtotal = sum((Decimal(a) for _, a in items), Decimal(0))
    else:
        subtotal = (unit * 4).quantize(Decimal("0.01"))
    subtotal = subtotal.quantize(Decimal("0.01"))

    rate = Decimal(("18.0", "12.0", "5.0", "28.0")[index % 4])
    tax = (subtotal * rate / 100).quantize(Decimal("0.01"))
    total = subtotal + tax

    day = 1 + (index % 28)
    month = 1 + (index % 12)
    date = f"2026-{month:02d}-{day:02d}"

    supplier_state = ("27", "29", "07", "33")[index % 4]
    buyer_state = supplier_state if index % 3 else ("24", "06", "19", "36")[index % 4]

    return Case(
        case_id=f"GT-{index + 1:04d}",
        input_type=input_type,
        category=category,
        date=date,
        party=party,
        subtotal=money(subtotal),
        tax_amount=money(tax),
        total=money(total),
        line_items=items,
        gst_context={
            "hsn_or_sac": ("998311", "9954", "3824", "7306")[index % 4],
            "rate": money(rate),
            "supplier_state": supplier_state,
            "buyer_state": buyer_state,
            "place_of_supply": buyer_state,
            "tax_mode": "INTRA_STATE"
            if supplier_state == buyer_state
            else "INTER_STATE",
        },
        variation=_variation(index, category, input_type),
        content_source=content_source,
        notes=f"{input_type} / {category} / slot {slot}",
    )


# =============================================================================
# THE PRINTED DOCUMENT — one set of lines, five containers
# =============================================================================


def _amount_text(value: str, variation: Variation) -> str:
    grouped = (
        group_indian(value) if variation.grouping == "indian" else group_western(value)
    )
    if variation.currency == "rs":
        return f"RS. {grouped}"
    if variation.currency == "pound":
        return f"{grouped} GBP"
    return grouped


def _wrap(text: str, width: int) -> list[str]:
    out: list[str] = []
    line = ""
    for word in text.split(" "):
        candidate = f"{line} {word}".strip()
        if len(candidate) > width and line:
            out.append(line)
            line = word
        else:
            line = candidate
    if line:
        out.append(line)
    return out


def document_lines(case: Case) -> tuple[str, ...]:
    """What the document says, before any container wraps it."""
    v = case.variation
    lines: list[str] = ["TAX INVOICE"]

    head = [
        f"INVOICE NO: {case.case_id.replace('-', '/')}",
        f"DATE: {printed_date(case.date, v.date_format)}",
        f"SUPPLIER: {case.party}",
        f"HSN/SAC: {case.gst_context['hsn_or_sac']}",
        f"PLACE OF SUPPLY: {case.gst_context['place_of_supply']}",
    ]
    if v.layout == "two_column":
        lines.append(f"{head[0]:<34}{head[1]}")
        lines.append(f"{head[2]:<34}{head[3]}")
        lines.append(head[4])
    elif v.layout == "right":
        lines.extend(f"{h:>62}" for h in head)
    else:
        lines.extend(head)

    lines.append("-" * 62)
    if case.line_items:
        lines.append("DESCRIPTION" + " " * 34 + "AMOUNT")
        for description, amount in case.line_items:
            shown = _amount_text(amount, v)
            if v.layout == "wrapped":
                wrapped = _wrap(description, 30)
                for piece in wrapped[:-1]:
                    lines.append(piece)
                lines.append(f"{wrapped[-1]:<45}{shown:>17}")
            elif v.layout == "table":
                lines.append(f"| {description:<43}| {shown:>15}|")
            else:
                lines.append(f"{description:<45}{shown:>17}")
    else:
        lines.append("SUPPLY AS PER AGREEMENT. NO ITEMISED BREAKDOWN.")
    lines.append("-" * 62)

    subtotal_line = f"{'SUBTOTAL':<45}{_amount_text(case.subtotal, v):>17}"
    tax_line = f"{v.tax_label:<45}{_amount_text(case.tax_amount, v):>17}"
    total_line = f"{v.total_label:<45}{_amount_text(case.total, v):>17}"

    if v.tax_label == "CGST/SGST":
        half = (Decimal(case.tax_amount) / 2).quantize(Decimal("0.01"))
        tax_line = (
            f"{'CGST':<45}{_amount_text(money(half), v):>17}\n"
            f"{'SGST':<45}{_amount_text(money(Decimal(case.tax_amount) - half), v):>17}"
        )

    body = [tax_line, total_line] if v.subtotal_last else [subtotal_line, tax_line]
    if v.subtotal_last:
        body.append(subtotal_line)
    else:
        body.append(total_line)
    for chunk in body:
        lines.extend(chunk.split("\n"))

    lines.extend(v.decoys)
    if v.pages > 1:
        lines.append("PAGE 1 OF 2")
        lines.append("CONTINUATION SHEET")
        lines.append(f"SUPPLIER: {case.party}")
        lines.append(f"{v.total_label:<45}{_amount_text(case.total, v):>17}")
        lines.append("PAGE 2 OF 2")
    return tuple(lines)


# =============================================================================
# THE FIVE RENDERERS
# =============================================================================


def render_text(lines: Sequence[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


def pdf_escape(text: str) -> str:
    out = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return "".join(c if ord(c) < 128 else "?" for c in out)


def render_pdf(lines: Sequence[str], pages: int) -> bytes:
    """PDF 1.4 with a real cross-reference table. Offsets are measured, not
    guessed: every object is appended and its byte position recorded as it is
    written, which is the only way an xref can be correct."""
    per_page = max(1, -(-len(lines) // pages))
    chunks = [lines[i : i + per_page] for i in range(0, len(lines), per_page)] or [[]]

    objects: list[bytes] = []
    page_ids = [3 + i for i in range(len(chunks))]
    content_ids = [3 + len(chunks) + i for i in range(len(chunks))]
    font_id = 3 + 2 * len(chunks)

    kids = " ".join(f"{i} 0 R" for i in page_ids)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(
        f"<< /Type /Pages /Kids [{kids}] /Count {len(chunks)} >>".encode("ascii")
    )
    for page_id, content_id in zip(page_ids, content_ids, strict=True):
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                f"/Contents {content_id} 0 R >>"
            ).encode("ascii")
        )
        _ = page_id
    for chunk in chunks:
        drawn = "".join(f"({pdf_escape(line)}) Tj T*\n" for line in chunk)
        stream = f"BT /F1 9 Tf 40 800 Td 12 TL\n{drawn}ET\n".encode("ascii")
        objects.append(
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"endstream"
        )
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii")
    out += f"startxref\n{xref_at}\n%%EOF\n".encode("ascii")
    return bytes(out)


def rasterise(lines: Sequence[str], ink: int, rotate: bool) -> tuple[int, int, bytes]:
    """Glyph pixels, 8-bit grey. Raises rather than drawing an empty box."""
    unknown = sorted({c for line in lines for c in line.upper()} - RASTERISABLE)
    if unknown:
        raise UnrenderableGlyph(f"the 5x7 font has no glyph for {unknown}")

    margin = 6
    columns = max((len(line) for line in lines), default=1)
    width = margin * 2 + columns * (GLYPH_W + 1)
    height = margin * 2 + len(lines) * (GLYPH_H + 2)
    grid = [[255] * width for _ in range(height)]

    for row, line in enumerate(lines):
        top = margin + row * (GLYPH_H + 2)
        for col, char in enumerate(line.upper()):
            left = margin + col * (GLYPH_W + 1)
            for y, bits in enumerate(FONT[char]):
                for x, bit in enumerate(bits):
                    if bit == "#":
                        grid[top + y][left + x] = ink

    if rotate:
        grid = [list(row) for row in zip(*grid[::-1], strict=True)]
        height, width = width, height

    raw = bytearray()
    for row_pixels in grid:
        raw.append(0)
        raw.extend(row_pixels)
    return width, height, bytes(raw)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))


def render_png(lines: Sequence[str], ink: int, rotate: bool) -> bytes:
    width, height, raw = rasterise(lines, ink, rotate)
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


def render_jpg_container(lines: Sequence[str]) -> bytes:
    """A JFIF-framed container with the text in COM segments and NO image data.

    Named `_container` and not `render_jpg` on purpose: this is not a
    photograph and the name should not let anybody believe it is. See
    `JPG_LIMITATION` at the top of this file.
    """
    app0 = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    out = bytearray(b"\xff\xd8")
    out += b"\xff\xe0" + struct.pack(">H", len(app0) + 2) + app0
    for line in lines:
        payload = line.encode("utf-8", errors="replace")[:65000]
        out += b"\xff\xfe" + struct.pack(">H", len(payload) + 2) + payload
    out += b"\xff\xd9"
    return bytes(out)


_DOCX_CONTENT_TYPES: Final = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
    'package.relationships+xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)

_DOCX_RELS: Final = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.'
    'org/officeDocument/2006/relationships/officeDocument" Target="word/'
    'document.xml"/></Relationships>'
)

_DOCX_DOC_RELS: Final = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships"/>'
)


def xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_docx(lines: Sequence[str]) -> bytes:
    """A real OOXML package. `zipfile` is standard library; no DOCX reader."""
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{xml_escape(line)}</w:t></w:r></w:p>'
        for line in lines
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/'
        f'2006/main"><w:body>{body}</w:body></w:document>'
    )
    parts = (
        ("[Content_Types].xml", _DOCX_CONTENT_TYPES),
        ("_rels/.rels", _DOCX_RELS),
        ("word/_rels/document.xml.rels", _DOCX_DOC_RELS),
        ("word/document.xml", document),
    )
    buffer = bytearray()

    class _Sink:
        def write(self, data: bytes) -> int:
            buffer.extend(data)
            return len(data)

        def flush(self) -> None:
            return None

        def tell(self) -> int:
            return len(buffer)

    with zipfile.ZipFile(_Sink(), "w", zipfile.ZIP_DEFLATED) as archive:  # type: ignore[arg-type]
        for name, text in parts:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, text)
    return bytes(buffer)


def render(case: Case) -> bytes:
    lines = document_lines(case)
    v = case.variation
    if case.input_type == "text":
        return render_text(lines)
    if case.input_type == "PDF":
        return render_pdf(lines, v.pages)
    if case.input_type == "PNG":
        return render_png(lines, v.ink, v.rotate)
    if case.input_type == "JPG":
        return render_jpg_container(lines)
    if case.input_type == "DOCX":
        return render_docx(lines)
    raise ValueError(f"no renderer for input type {case.input_type!r}")


# =============================================================================
# THE CASE FILE
# =============================================================================


def case_record(case: Case, document: bytes) -> dict[str, Any]:
    raw = case.raw_fields
    return {
        "case_id": case.case_id,
        "source_label": SOURCE_LABEL,
        "corpus_label": CORPUS_LABEL,
        "input_type": case.input_type,
        "category": case.category,
        "mime": MIME[case.input_type],
        "document": case.filename,
        "format_fidelity": FIDELITY[case.input_type],
        "raw_fields": raw,
        "gst_context": case.gst_context,
        "expected": expected_from(raw),
        "rule_ids": list(case.rule_ids),
        "source_urls": list(case.source_urls),
        "provenance": {
            "generated_by": "scripts/build_ground_truth.py",
            "generation_version": GENERATION_VERSION,
            "content_source": case.content_source,
            "notes": case.notes,
            "variation": {
                "date_format": case.variation.date_format,
                "currency": case.variation.currency,
                "grouping": case.variation.grouping,
                "tax_label": case.variation.tax_label,
                "total_label": case.variation.total_label,
                "layout": case.variation.layout,
                "subtotal_last": case.variation.subtotal_last,
                "pages": case.variation.pages,
                "rotate": case.variation.rotate,
                "ink": case.variation.ink,
                "decoys": list(case.variation.decoys),
            },
        },
        "generation_version": GENERATION_VERSION,
        "created_at": CREATED_AT,
        "generation_hash": generation_hash(raw, case.gst_context),
        "sha256": sha256_of(document),
        "document_bytes": len(document),
    }


def build_corpus() -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    records: list[dict[str, Any]] = []
    documents: dict[str, bytes] = {}
    for case in build_cases():
        data = render(case)
        documents[case.filename] = data
        records.append(case_record(case, data))
    return records, documents


def matrix(records: Sequence[dict[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {
        t: dict.fromkeys(CATEGORIES, 0) for t in INPUT_TYPES
    }
    for record in records:
        out[str(record["input_type"])][str(record["category"])] += 1
    return out


def corpus_manifest(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, int] = dict.fromkeys(INPUT_TYPES, 0)
    by_category: dict[str, int] = dict.fromkeys(CATEGORIES, 0)
    by_label: dict[str, int] = {}
    for record in records:
        by_type[str(record["input_type"])] += 1
        by_category[str(record["category"])] += 1
        label = str(record["corpus_label"])
        by_label[label] = by_label.get(label, 0) + 1
    return {
        "benchmark_label": BENCHMARK_LABEL,
        "generation_version": GENERATION_VERSION,
        "created_at": CREATED_AT,
        "case_count": len(records),
        "by_input_type": by_type,
        "by_category": by_category,
        "by_corpus_label": by_label,
        "matrix": matrix(records),
        "blocked_input_types": dict.fromkeys(BLOCKED_TYPES, JPG_LIMITATION),
        "gate_per_field": GATE_PER_FIELD,
        # The highest per-field score any reader could reach today. A blocked
        # input type is 20 cases that carry no readable content, so they are
        # 20 cases nobody can get right. Recorded because a gate of 95 against
        # a ceiling of 80 is unreachable, and that is a fact about the corpus
        # rather than a fact about any backend.
        "reachable_ceiling": len(records) - sum(by_type[t] for t in BLOCKED_TYPES),
        "reachable_ceiling_reason": JPG_LIMITATION,
        "cases": [
            {
                "case_id": r["case_id"],
                "input_type": r["input_type"],
                "category": r["category"],
                "corpus_label": r["corpus_label"],
                "source_label": r["source_label"],
                "document": r["document"],
                "sha256": r["sha256"],
                "generation_hash": r["generation_hash"],
            }
            for r in records
        ],
    }


def documents_manifest(
    records: Sequence[dict[str, Any]], documents: dict[str, bytes]
) -> dict[str, Any]:
    return {
        "generation_version": GENERATION_VERSION,
        "documents": [
            {
                "document": r["document"],
                "case_id": r["case_id"],
                "mime": r["mime"],
                "format_fidelity": r["format_fidelity"],
                "bytes": len(documents[str(r["document"])]),
                "sha256": r["sha256"],
            }
            for r in records
        ],
    }


def write_corpus(root: Path = CORPUS_ROOT) -> dict[str, int]:
    records, documents = build_corpus()
    cases_dir = root / "cases"
    docs_dir = root / "documents"
    manifests_dir = root / "manifests"
    for directory in (cases_dir, docs_dir, manifests_dir):
        directory.mkdir(parents=True, exist_ok=True)

    for record in records:
        path = cases_dir / f"{record['case_id']}.json"
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    for name, data in documents.items():
        (docs_dir / name).write_bytes(data)

    (manifests_dir / "corpus.json").write_text(
        json.dumps(corpus_manifest(records), indent=2) + "\n", encoding="utf-8"
    )
    (manifests_dir / "documents.json").write_text(
        json.dumps(documents_manifest(records, documents), indent=2) + "\n",
        encoding="utf-8",
    )
    return {"cases": len(records), "documents": len(documents)}


# A decoded case file, and whatever a backend put in `per_field_source` or
# `line_items`, are `object` until something checks them. These two ask what the
# value actually is rather than trusting the shape it is supposed to have.
def _is_obj(value: object) -> TypeGuard[dict[str, object]]:
    """A decoded JSON object. Its keys are strings by construction."""
    return isinstance(value, dict)


def _is_iterable(value: object) -> TypeGuard[Iterable[object]]:
    """Something that can be walked. Its elements are anything at all."""
    return isinstance(value, Iterable)


def load_records(root: Path = CORPUS_ROOT) -> list[dict[str, Any]]:
    """Every committed case file, in case-id order."""
    paths = sorted((root / "cases").glob("GT-*.json"))
    out: list[dict[str, Any]] = []
    for path in paths:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
        if not _is_obj(loaded):
            raise ValueError(f"{path} is not a JSON object")
        out.append({str(k): v for k, v in loaded.items()})
    return out


# =============================================================================
# THE SCORER — imported by the tests here and by the runner the second agent
# owns. It is in this module because this module already holds the case model;
# a third file that also knows the schema is a third place for it to drift.
# =============================================================================


#: The scored field name on the left, the `ExtractedRecord` attribute on the
#: right, and the `per_field_source` key that says whether the reader ANSWERED.
#: `line_items` has no source key of its own, which is why it is absent here
#: and handled by `_answered_anything` below.
FIELD_TO_RECORD: Final = {
    "date": ("date", "date"),
    "party": ("party", "party"),
    "total_amount": ("total_paise", "total_paise"),
    "tax_amount": ("tax_paise", "tax_paise"),
}


@dataclass(frozen=True)
class FieldScore:
    """Three outcomes per field, and they sum to the case count.

    `refused` and `fabricated` are kept apart because they are not the same
    failure. A refusal is visible and asks the person a question. A fabrication
    states a source and is wrong, which is this project's own definition of a
    hallucination, and it is the one that reaches the ledger.
    """

    field_name: str
    correct: int
    refused: int
    fabricated: int
    total: int
    examples: tuple[str, ...] = ()


@dataclass
class CorpusScore:
    backend: str
    processed: int = 0
    explicit_not_found: int = 0
    silent_blanks: tuple[str, ...] = ()
    unclassified_failures: tuple[str, ...] = ()
    with_provenance: int = 0
    fields: dict[str, FieldScore] = field(default_factory=dict[str, FieldScore])

    def passes_gate(self, threshold: int = GATE_PER_FIELD) -> bool:
        return (
            bool(self.fields)
            and not self.silent_blanks
            and not self.unclassified_failures
            and all(s.correct >= threshold for s in self.fields.values())
        )


def _answered_anything(stated: dict[str, str]) -> bool:
    """Did the reader answer at all, or is every field an explicit refusal?

    This is what stops a stub scoring by coincidence. Ten of the hundred cases
    legitimately have no line items, and a backend that read nothing also
    reports no line items — the two look identical from the value alone. They
    are not the same thing, and the difference is whether the record claims a
    source for anything. A record that claims none answered nothing, and
    nothing is never right.
    """
    return any(not source.startswith(NOT_FOUND) for source in stated.values())


def _observed(record: object) -> dict[str, object]:
    """The five scored answers, read off an `ExtractedRecord`."""
    items: object = getattr(record, "line_items", ()) or ()
    pairs: list[tuple[str, int]] = []
    if _is_iterable(items):
        for item in items:
            pairs.append(
                (
                    str(getattr(item, "description", "")),
                    int(getattr(item, "amount_paise", 0)),
                )
            )
    return {
        "date": getattr(record, "date", None),
        "party": getattr(record, "party", None),
        "total_amount": getattr(record, "total_paise", None),
        "tax_amount": getattr(record, "tax_paise", None),
        "line_items": tuple(pairs),
    }


def _extract(extractor: object, data: bytes, mime: str) -> object:
    """Hand one document to the backend and take back whatever it says.

    `extractor` stays `object` because the scorer must run anything with an
    `extract` method, the two stubs included, and nothing here may depend on a
    backend having been selected. The attribute cannot be resolved at type-check
    time; the RESULT is `object`, so every field is read off it by `_observed`
    rather than assumed. Attribute lookup and call are unchanged, so a backend
    with no `extract` still raises `AttributeError` into the caller's handler.
    """
    return extractor.extract(data, mime)  # type: ignore[attr-defined]


def _wanted(expected: dict[str, Any]) -> dict[str, object]:
    """The canonical answer, in the units the record uses. Never a reader's."""
    return {
        "date": datetime.date.fromisoformat(str(expected["date"])),
        "party": str(expected["party"]),
        "total_amount": paise(str(expected["total_amount"])),
        "tax_amount": paise(str(expected["tax_amount"])),
        "line_items": tuple(
            (str(item["description"]), paise(str(item["amount"])))
            for item in expected["line_items"]
        ),
    }


def score_corpus(
    extractor: object, records: Sequence[dict[str, Any]], documents: Path
) -> CorpusScore:
    """Run every case and count. Never mutates a case; never writes `expected`.

    The comparison is one-directional on purpose: the canonical truth is read
    from the case file and the reader's answer is compared TO it. Nothing here
    can write back, so there is no path by which a run could quietly make the
    corpus agree with whatever the reader said.
    """
    score = CorpusScore(
        backend=str(getattr(extractor, "name", type(extractor).__name__))
    )
    correct = dict.fromkeys(SCORED_FIELDS, 0)
    refused = dict.fromkeys(SCORED_FIELDS, 0)
    examples: dict[str, list[str]] = {name: [] for name in SCORED_FIELDS}
    blanks: list[str] = []
    failures: list[str] = []

    for record in records:
        case_id = str(record["case_id"])
        data = (documents / str(record["document"])).read_bytes()
        try:
            answer: object = _extract(extractor, data, str(record["mime"]))
        # Broad on purpose: an unclassified failure IS the finding here, and
        # a backend that raises must be NAMED rather than dropped out of the
        # denominator, which is how a crash becomes a good-looking score.
        except Exception as exc:
            failures.append(f"{case_id}: {type(exc).__name__}: {exc}")
            continue
        score.processed += 1

        sources: object = getattr(answer, "per_field_source", None)
        if not _is_obj(sources):
            failures.append(f"{case_id}: per_field_source is not a mapping")
            continue
        stated = {str(k): str(v) for k, v in sources.items()}
        if stated:
            score.with_provenance += 1
        blanks.extend(
            f"{case_id}.{name}" for name, source in stated.items() if not source.strip()
        )
        answered = _answered_anything(stated)
        if not answered:
            score.explicit_not_found += 1

        observed = _observed(answer)
        wanted = _wanted(dict(record["expected"]))
        for name in SCORED_FIELDS:
            key = FIELD_TO_RECORD.get(name, (name, ""))[1]
            spoke = (
                not stated.get(key, NOT_FOUND).startswith(NOT_FOUND)
                if key in stated
                else answered
            )
            if spoke and observed[name] == wanted[name]:
                correct[name] += 1
            elif not spoke:
                refused[name] += 1
            else:
                examples[name].append(case_id)

    score.silent_blanks = tuple(blanks)
    score.unclassified_failures = tuple(failures)
    score.fields = {
        name: FieldScore(
            field_name=name,
            correct=correct[name],
            refused=refused[name],
            fabricated=len(records) - correct[name] - refused[name],
            total=len(records),
            examples=tuple(examples[name][:5]),
        )
        for name in SCORED_FIELDS
    }
    return score


# =============================================================================


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=CORPUS_ROOT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="rebuild in memory and report differences; write nothing",
    )
    args = parser.parse_args(argv)
    root = Path(args.root)

    if args.check:
        records, documents = build_corpus()
        differences = _differences(root, records, documents)
        for line in differences:
            print(line)
        print(f"differences: {len(differences)}")
        return 1 if differences else 0

    counts = write_corpus(root)
    print(f"cases: {counts['cases']}  documents: {counts['documents']}  root: {root}")
    return 0


def _differences(
    root: Path, records: Sequence[dict[str, Any]], documents: dict[str, bytes]
) -> list[str]:
    out: list[str] = []
    for record in records:
        path = root / "cases" / f"{record['case_id']}.json"
        if not path.exists():
            out.append(f"missing case file {path}")
            continue
        if path.read_text(encoding="utf-8") != json.dumps(record, indent=2) + "\n":
            out.append(f"case file differs from a rebuild: {path}")
    for name, data in documents.items():
        path = root / "documents" / name
        if not path.exists():
            out.append(f"missing document {path}")
        elif path.read_bytes() != data:
            out.append(f"document differs from a rebuild: {path}")
    return out


def iter_case_paths(root: Path = CORPUS_ROOT) -> Iterable[Path]:
    return sorted((root / "cases").glob("GT-*.json"))


def load_cases(root: Path = CORPUS_ROOT) -> tuple[dict[str, Any], ...]:
    """The contract `scripts/run_ground_truth.py` documents, implemented here.

        load_cases(root: pathlib.Path) -> Sequence[Mapping[str, Any]]

    This file exposed `build_cases()`, `load_records()` and `main()` and none of
    the three names the runner looks for, so the S2 section reported
    `BLOCKED - awaiting scripts/build_ground_truth.py` while the pack had in
    fact been built and committed. A blocked gate that names a file sitting in
    the repository is a contract failure between two halves, not a missing
    dependency, and it stayed invisible because BLOCKED reads like a fact about
    the world rather than a fact about wiring.

    `input_path` is relative to `root`, because that is what the runner joins.
    `renderable` is carried through rather than recomputed: it is the flag EXIT
    1 needs to keep the 20 unrenderable JPG cases out of its denominator, and
    deriving it twice in two places is how the two numbers drift apart.
    """
    out: list[dict[str, Any]] = []
    for path in iter_case_paths(root):
        record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        out.append(
            {
                "case_id": record["case_id"],
                "input_path": f"documents/{record['document']}",
                "mime": record["mime"],
                "input_type": record["input_type"],
                "category": record["category"],
                "format_fidelity": record["format_fidelity"],
                "renderable": record["input_type"] != "JPG",
                "expected": record["expected"],
                "corpus_label": record["corpus_label"],
                "source_label": record["source_label"],
            }
        )
    return tuple(out)


if __name__ == "__main__":
    sys.exit(main())

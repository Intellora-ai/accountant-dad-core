"""A scan in a PDF wrapper, and the one fall-through the ladder now makes.

WHAT WAS BROKEN, MEASURED BEFORE ANY OF THIS WAS WRITTEN
--------------------------------------------------------
`registry.default_extractor()` handed every `application/pdf` to the text-layer
rung and took whatever came back. A scanned bill - a photograph of paper inside
a PDF container - has no characters in it, so that rung correctly reported
`Outcome.NO_TEXT_LAYER` and correctly returned four `not_found` fields, and the
document stopped there. The rung that reads pixels was wired, was one entry away
in the same dictionary, and was never asked.

MEASURED on `data/real_invoices/`, which is 305 documents and is NOT in git:

    77 PDFs      62 carry a text layer
                  5 are encrypted and are refused for that
                 10 carry NO text layer, and all ten returned nothing

WHAT THE RULE IS, AND WHY IT CARRIES NO NUMBER
-----------------------------------------------
A PDF falls through to the picture rung when `textlayer.read` reports
`Outcome.NO_TEXT_LAYER`, which that module defines as `not text.strip()` - zero
characters. There is no length threshold, no tolerance and nothing to tune, and
`test_a_pdf_whose_text_layer_holds_almost_nothing_still_goes_to_the_text_rung`
is what would catch one being added.

The owner's instruction offered a looser rule - "< 20 chars, or no recognizable
labels" - as an example rather than as a setting. The second half was tried
against the corpus and refused on the measurement: all 62 real PDFs that DO
carry a text layer read zero fields, because they are UK government forms and
this system's labels are written for Indian GST bills. Routing "no labels" to
OCR would put every one of them on the estimating tier for about a second each,
to read the same words worse and still find no labels.

WHAT THESE TESTS DO NOT PROVE
------------------------------
That a scanned bill READS. Mostly it does not: the engine's accuracy on
photographs is recorded in `accountant/extract/pagereader.py` and it is poor.
What is proved here is that the pixels are now reached, that the record says
which rung reached them, and that a rung that guesses can never post.

That the corpus above is representative. It is public archive material - the ten
no-text-layer PDFs are old menus, letters and price lists rather than invoices -
and `H-02`, real customer bills, is still open.
"""

from __future__ import annotations

import io
import json
import pathlib
import shutil
import zlib
from collections.abc import Sequence

import pytest

from accountant.cage.confidence import EXACT
from accountant.cage.decision import AUTO_POST_ALLOWED_TIERS
from accountant.cage.gate import _tiers  # pyright: ignore[reportPrivateUsage]
from accountant.extract.adapter import NOT_FOUND, ExtractedRecord
from accountant.extract.freeocr import FreeReader
from accountant.extract.ladder import (
    BILL_LABELS,
    FELL_THROUGH_EVENT,
    NOTHING_IN_IT,
    SHORTEST_REAL_TEXT_LAYER,
    Ladder,
    _preferring_the_characters,  # pyright: ignore[reportPrivateUsage]
    looks_scanned,
)
from accountant.extract.textlayer import (
    PDF_MIME,
    PICTURE_MEDIA,
    Outcome,
    TextLayerReader,
    picture_of,
    read,
)
from accountant.labels import DATE_LABEL
from accountant.observability import install_logging
from tests.test_textlayer import BILL, pdf_bytes

REPO = pathlib.Path(__file__).resolve().parent.parent
DOCUMENTS = REPO / "artifacts" / "ground_truth" / "documents"
CASES = REPO / "artifacts" / "ground_truth" / "cases"

#: GT-0052 is the corpus PNG `tests/test_pagereader.py` already uses for the
#: same reason: it is one of the few whose `SUPPLIER:` label survives the 5x7
#: bitmap font, so it is a picture the engine can actually get a field off.
#: Anything else would test the engine's weakness rather than this routing.
A_READABLE_SCAN = "GT-0052"

ENGINE_ON_PATH = shutil.which("tesseract")

NEEDS_THE_ENGINE = pytest.mark.skipif(
    not ENGINE_ON_PATH,
    reason=(
        "SKIPPED LOUDLY: no `tesseract` binary on PATH, so nothing here can "
        "read a picture out of a PDF. Install it with `brew install tesseract` "
        "(macOS) or `apt-get install -y tesseract-ocr` (Linux). The routing "
        "tests that need no engine still run and are the ones that hold the "
        "control - a PDF WITH a text layer must not fall through."
    ),
)


# ---- building a scan -------------------------------------------------------
#
# A PDF whose page carries a picture and NO content stream, which is what a
# scanner writes and what `extract_text()` correctly answers "" about. Written
# here rather than taken from a file so that every case below - two pictures,
# an unreadable one, none at all - is one argument apart from the case beside
# it, and so that no test depends on a corpus that is not in git.


def _assemble(objects: Sequence[bytes]) -> bytes:
    """Numbered objects, a cross-reference table that points at them, a trailer.

    The same shape `tests/test_textlayer.py::_assemble` builds, and deliberately
    a correct one: `textlayer.xref_was_rebuilt` reads this table, and a fixture
    with a broken pointer would put the repair warning on every reading below
    and prove something about the warning instead of about the routing.
    """
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii") + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii")
    out += f"startxref\n{xref_at}\n%%EOF\n".encode("ascii")
    return bytes(out)


def _grey_picture(width: int, height: int, body: bytes) -> bytes:
    """One image XObject: eight-bit grey, deflated, exactly as a scanner stores it.

    `/FlateDecode` and not `/DCTDecode`, so the round trip is LOSSLESS. `pypdf`
    hands JPEG bytes straight through, which would be fine, but then the pixels
    the engine reads would be a re-compression of the corpus PNG rather than the
    corpus PNG - and a test whose field assertion depends on JPEG quality is a
    test that fails the day somebody changes an encoder default.
    """
    deflated = zlib.compress(body)
    return (
        (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
            f"/Length {len(deflated)} >>\nstream\n"
        ).encode("ascii")
        + deflated
        + b"\nendstream"
    )


def scanned_pdf(*pictures: tuple[int, int, bytes]) -> bytes:
    """A one-page PDF carrying these pictures and no text at all.

    Every picture is a resource on the page. Order is preserved, so a test can
    put the small one first and prove that `picture_of` ranks rather than takes
    the first thing it finds.
    """
    fixed: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"",  # the page, filled in below once the picture numbers are known
        b"<< /Length 0 >>\nstream\n\nendstream",
    ]
    # DERIVED and never written as a literal. The first picture's object number
    # is one past the fixed objects above, and a hand-typed `4` here produced a
    # page pointing at the empty content stream - every picture invisible, every
    # test below passing for the wrong reason.
    first = len(fixed) + 1
    names = " ".join(
        f"/Im{index} {first + index} 0 R" for index in range(len(pictures))
    )
    fixed[2] = (
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        f"/Resources << /XObject << {names} >> >> >>"
    ).encode("ascii")
    return _assemble([*fixed, *(_grey_picture(w, h, body) for w, h, body in pictures)])


def a_flat_picture(width: int, height: int, shade: int = 255) -> tuple[int, int, bytes]:
    """A picture with nothing printed on it. Big enough to be ranked, blank."""
    return width, height, bytes([shade]) * (width * height)


def the_corpus_scan() -> tuple[int, int, bytes]:
    """`GT-0052.png`, as the grey pixels a scanner would have stored.

    `PIL` is imported here and not at the top of the file because it is needed
    only to turn a committed PNG into raw pixels. Nothing in
    `accountant/extract/` outside `freeocr.py` may import it - `D-30` - and a
    test file importing it at module scope would read as though that had
    changed.
    """
    from PIL import Image

    with Image.open(DOCUMENTS / f"{A_READABLE_SCAN}.png") as opened:
        grey = opened.convert("L")
        return grey.size[0], grey.size[1], grey.tobytes()


def expected_party() -> str:
    case = json.loads((CASES / f"{A_READABLE_SCAN}.json").read_text())
    return str(case["expected"]["party"])


# ---- the defect, closed ----------------------------------------------------


def test_a_scanned_pdf_has_a_picture_taken_out_of_it_rather_than_nothing() -> None:
    """The whole of the fall-through's first half, with no engine involved.

    `picture_of` is what turns "this PDF is a scan" into bytes something can
    read. It needs no `tesseract`, so this runs everywhere and is what says the
    pixels are REACHABLE even on a machine where nothing can read them.
    """
    found = picture_of(scanned_pdf(the_corpus_scan()))

    assert found.media_type == "image/png"
    assert found.data
    assert found.page == 1


@NEEDS_THE_ENGINE
def test_a_scanned_pdf_reaches_the_picture_rung_and_reads_a_field() -> None:
    """THE DEFECT THIS FILE EXISTS FOR. Before the fall-through this document
    came back with four `not_found` fields off a rung that found no characters,
    while the rung that reads pixels sat unasked in the same dictionary.

    The supplier is asserted by VALUE against the ground truth and not merely as
    "something came back", because a reader that answered a wrong name would
    pass the weaker assertion and is the more expensive failure.
    """
    record = Ladder().extract(scanned_pdf(the_corpus_scan()), PDF_MIME)

    assert record.party == expected_party()
    assert record.backend == FreeReader.name


@NEEDS_THE_ENGINE
def test_the_field_read_off_a_scan_is_an_estimate_and_never_exact() -> None:
    """A field read off pixels must not inherit the text layer's 1.0.
    `cage/decision.py` auto-posts at 0.95, so a scan claiming exactness would
    write a guess into somebody's books with nothing on screen to notice."""
    record = Ladder().extract(scanned_pdf(the_corpus_scan()), PDF_MIME)

    assert 0.0 < record.per_field_confidence["party"] < 1.0


# ---- THE CONTROL: a PDF that has a text layer must not fall through ---------
#
# Without these, a fall-through that fired on everything would pass every test
# above and would destroy the only tier this product measures as exact: the text
# layer reads the twenty corpus PDFs at 14/20 date, 20/20 party, 20/20 total,
# 20/20 tax, with ZERO WRONG. Routing those to an engine that guesses would
# trade a measured zero for an unmeasured number.


def test_the_control_a_pdf_with_a_text_layer_is_still_read_by_the_text_rung() -> None:
    """The control on every fall-through test in this file."""
    record = Ladder().extract(pdf_bytes(BILL), PDF_MIME)

    assert record.backend == TextLayerReader.name
    assert record.party == "SHARMA TRADERS"
    assert record.total_paise == 123456


def test_the_control_the_router_answers_exactly_what_the_rung_alone_answers() -> None:
    """Field by field and source by source, the routed record and the rung's own
    record are the same object's worth of facts. A fall-through that fired here
    would show up as a different backend or a different sentence."""
    routed = Ladder().extract(pdf_bytes(BILL), PDF_MIME)
    direct = TextLayerReader().extract(pdf_bytes(BILL), PDF_MIME)

    assert routed == direct


# ---- the owner's number, bound in both directions --------------------------
#
# `SHORTEST_REAL_TEXT_LAYER` is 20 because the owner set it to 20. Nothing in
# this repository measures it - MEASURED, and reported as a zero: not one of the
# 77 real PDFs in `data/real_invoices/` falls on the character count, so every
# document that gets routed on this corpus is routed by one of the other two
# conditions. These two tests are the only thing that makes the number visible,
# and they are written either side of it so that changing it fails loudly.


def test_a_text_layer_one_character_short_of_the_owners_number_is_called_a_scan() -> (
    None
):
    """The boundary, from below."""
    thin = pdf_bytes(("TOTAL 4.00",))
    reading = read(thin)

    assert reading.outcome is Outcome.READ
    assert len(reading.text.strip()) < SHORTEST_REAL_TEXT_LAYER
    assert looks_scanned(reading)


def test_a_text_layer_at_the_owners_number_is_not_called_a_scan() -> None:
    """The boundary, from above, and the control on the test before it. Without
    this a rule that called EVERY document a scan would pass that one."""
    fat = pdf_bytes(("TOTAL 4.00", "DATE: 2026-04-01"))
    reading = read(fat)

    assert len(reading.text.strip()) >= SHORTEST_REAL_TEXT_LAYER
    assert not looks_scanned(reading)


def test_a_pdf_with_text_and_no_bill_label_is_read_by_both_and_loses_nothing() -> None:
    """THE OWNER'S THIRD CONDITION, and the case that pays for the merge.

    MEASURED on `data/real_invoices/`: 25 of the 77 PDFs carry a text layer and
    print no label in `BILL_LABELS`, so this condition is what does the routing
    work. It is also the condition that could have cost the most - a bill
    labelled in words this vocabulary happens not to hold would be sent to the
    engine while its characters were sitting right there.

    `_preferring_the_characters` is why that costs nothing: whatever the engine
    makes of the pixels, a field the characters stated is still the field.
    """
    unlabelled = pdf_bytes(("MEMORANDUM", "FOR INTERNAL CIRCULATION ONLY"))

    assert looks_scanned(read(unlabelled))

    record = Ladder().extract(unlabelled, PDF_MIME)

    assert record.party is None
    assert record.total_paise is None


def test_a_document_that_did_not_parse_is_never_called_a_scan() -> None:
    """`textlayer.Outcome` has three members and not two for exactly this: a
    truncated or encrypted file is not a picture, and sending one to an engine
    that will read noise out of it is what that file's docstring warns against.
    The refusal the text rung already wrote is what the person gets."""
    for broken in (b"", b"%PDF-1.4\n", b"%PDF-1.4\n1 0 obj\n<< /Type /Cata"):
        reading = read(broken)

        assert reading.outcome is Outcome.UNREADABLE
        assert not looks_scanned(reading)


# ---- the record says which rung answered -----------------------------------


@NEEDS_THE_ENGINE
def test_the_two_paths_are_told_apart_by_the_backend_on_the_record() -> None:
    """A measurement taken afterwards has to be able to separate them, which is
    the whole argument in `docs/EXTRACTION_MEASURED.md`. Two records that both
    said "ladder" would be evidence about neither rung."""
    scan = Ladder().extract(scanned_pdf(the_corpus_scan()), PDF_MIME)
    typed = Ladder().extract(pdf_bytes(BILL), PDF_MIME)

    assert {scan.backend, typed.backend} == {FreeReader.name, TextLayerReader.name}


@NEEDS_THE_ENGINE
def test_a_scan_never_reports_the_tier_that_is_allowed_to_auto_post() -> None:
    """`cage/gate.py:453` derives `reading_tiers` from the record's per-field
    sources and `cage/decision.py` auto-posts only from a tier on its allowlist.

    THE FAILURE THIS CLOSES IS THE EXPENSIVE ONE. If the fall-through had
    stamped the routed record `pdf_text_layer` - which it would have done had it
    rebuilt the record instead of returning the picture rung's own - a guess off
    a photograph would have become eligible to write itself into a real ledger
    at 0.95 with nobody asked.
    """
    scan = Ladder().extract(scanned_pdf(the_corpus_scan()), PDF_MIME)
    tiers = _tiers(scan)

    assert TextLayerReader.name not in tiers
    assert not any(tier in AUTO_POST_ALLOWED_TIERS for tier in tiers)


def test_the_control_the_text_rung_does_report_the_tier_that_may_auto_post() -> None:
    """The control on the test above: without it, a `_tiers` that returned junk
    for everything would pass and would prove nothing about the scan."""
    typed = Ladder().extract(pdf_bytes(BILL), PDF_MIME)

    assert TextLayerReader.name in _tiers(typed)
    assert TextLayerReader.name in AUTO_POST_ALLOWED_TIERS


# ---- neither one nor the other: it refuses, and it does not crash -----------


@pytest.mark.parametrize(
    ("label", "data"),
    [
        ("empty", b""),
        ("header only", b"%PDF-1.4\n"),
        ("truncated mid-object", b"%PDF-1.4\n1 0 obj\n<< /Type /Cata"),
        ("not a PDF at all", b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"),
        ("a PDF with no pages", b"%PDF-1.4\ntrailer\n<< /Size 1 >>\n%%EOF\n"),
        ("null bytes", b"%PDF-1.4\n" + b"\x00" * 400),
    ],
)
def test_a_pdf_that_is_neither_refuses_in_words_and_never_raises(
    label: str, data: bytes
) -> None:
    """`pipeline.build_draft` calls its extractor with nothing around it and
    `web/app.py` turns an escaping exception into "Something in Accountant Dad
    broke". Every one of these is somebody's upload getting cut off."""
    record = Ladder().extract(data, PDF_MIME)

    assert record.total_paise is None
    assert all(
        source.startswith(NOT_FOUND) for source in record.per_field_source.values()
    ), label


def test_a_pdf_with_no_text_and_no_picture_says_both_halves() -> None:
    """The refusal a person reads has to name what is actually wrong. Returning
    the text rung's own sentence here would tell them "reading pixels is the
    other tier's job" - which was true until the fall-through landed and is now
    a refusal describing a gap this system has since filled."""
    record = Ladder().extract(scanned_pdf(), PDF_MIME)

    assert NOTHING_IN_IT in record.per_field_source["party"]
    assert record.backend == Ladder.name


def test_a_picture_stored_in_a_kind_we_do_not_read_is_refused_and_not_guessed() -> None:
    """`PICTURE_MEDIA` is a written-down table and anything off it is refused.
    What crosses into the picture rung has to be one of this package's own
    constants - never a media type derived from a file somebody sent us."""
    assert set(PICTURE_MEDIA.values()) <= {"image/png", "image/jpeg", "image/tiff"}
    assert ".jp2" not in PICTURE_MEDIA


def test_the_biggest_picture_on_the_page_is_the_one_taken_and_not_the_first() -> None:
    """MEASURED on `data/real_invoices/`: `gov-and-open-data-106.pdf` carries
    FIFTY-THREE pictures on its one page and the scan is not the first of them.
    A rule that took the first would read a logo off every one of those files.

    The scan is 64% to 94% of all picture area on its page in every case
    measured, which is why "the largest" needs no number: the picture OF a page
    is larger than anything printed ON it.
    """
    small = a_flat_picture(20, 20)
    large = a_flat_picture(400, 500)

    found = picture_of(scanned_pdf(small, large))

    assert found.data
    # The deflated blank is tiny either way, so the DIMENSIONS are what is
    # asserted: the returned PNG has to be the 400x500 one.
    from PIL import Image

    with Image.open(io.BytesIO(found.data)) as opened:
        assert opened.size == (400, 500)


# ---- it says it happened, once, through the one logger ---------------------
#
# `caplog` IS DELIBERATELY NOT USED, and it is not a preference. `install_logging`
# sets `propagate = False` on the `accountant` logger, for the stated reason that
# two copies of a line - one of them without a request id - is the failure that
# module exists to avoid. pytest's capture handler sits on the ROOT logger, so
# nothing propagates to it and every assertion below would pass vacuously by
# finding no lines at all. Reading the module's own stream seam is what makes
# these tests able to fail.


def written_by(extract: object) -> str:
    """Every line the `accountant` logger wrote while `extract` ran."""
    written = io.StringIO()
    handler = install_logging(written)
    try:
        assert callable(extract)
        extract()
    finally:
        handler.flush()
    return written.getvalue()


def test_the_fall_through_writes_one_line_naming_the_rung_that_answered() -> None:
    """A path that costs a second of engine time has to be visible in a log a
    deployment already reads. `observability.log` is the only logger involved -
    a second one would produce lines with no request id on them."""
    written = written_by(lambda: Ladder().extract(scanned_pdf(), PDF_MIME))

    lines = [line for line in written.splitlines() if FELL_THROUGH_EVENT in line]
    assert len(lines) == 1
    assert f"answered_by={Ladder.name}" in lines[0]
    assert "ms=" in lines[0]


def test_a_pdf_with_a_text_layer_writes_no_fall_through_line_at_all() -> None:
    """The control. A line per document is a cost with no question behind it,
    and the rung that answered an ordinary PDF is already on the record."""
    written = written_by(lambda: Ladder().extract(pdf_bytes(BILL), PDF_MIME))

    assert FELL_THROUGH_EVENT not in written


def test_the_log_line_carries_no_document_bytes_and_no_read_text() -> None:
    """A log is the copy of your data that ends up in the widest number of
    places, which `observability.log` says about itself. This line gets
    identifiers, a media type and a duration and nothing off the document."""
    scan = scanned_pdf(a_flat_picture(40, 40))
    written = written_by(lambda: Ladder().extract(scan, PDF_MIME))

    line = next(line for line in written.splitlines() if FELL_THROUGH_EVENT in line)
    assert "\x00" not in line
    assert "\xff" not in line
    assert line.endswith(tuple("0123456789"))


# =============================================================================
# THE MERGE ITSELF, 2026-08-15
# =============================================================================
#
# `_preferring_the_characters` had NO TEST AT ALL. A read-only audit found two
# defects in it on the same morning, and both had shipped: one of them undid a
# fix landed four hours earlier in the same package. A function on the live path
# that nothing tests is a function that is correct by luck.


def _record(
    *,
    backend: str,
    net_paise: int | None = None,
    read: bool = True,
    confidence: float | None = None,
    **fields: object,
) -> ExtractedRecord:
    """A record shaped the way a rung really builds one.

    Every name in `FIELDS` gets a source, because `__post_init__` raises
    otherwise - which is exactly the invariant that makes the merge's
    `.get(name, stated)` fallback unreachable.
    """
    said = backend if read else f"{NOT_FOUND}: nothing was read"
    scored = EXACT if confidence is None else confidence
    return ExtractedRecord(
        date=fields.get("date"),  # pyright: ignore[reportArgumentType]
        party=fields.get("party"),  # pyright: ignore[reportArgumentType]
        total_paise=fields.get("total_paise"),  # pyright: ignore[reportArgumentType]
        tax_paise=fields.get("tax_paise"),  # pyright: ignore[reportArgumentType]
        net_paise=net_paise,
        backend=backend,
        per_field_source=dict.fromkeys(ExtractedRecord.FIELDS, said),
        per_field_confidence=dict.fromkeys(ExtractedRecord.FIELDS, scored),
    )


def test_the_net_survives_the_merge() -> None:
    """THE DEFECT. `net_paise` reached `ExtractedRecord`, `record_of` and
    `FreeReader.extract` on 2026-08-15, and this function silently undid it the
    same morning - the record it rebuilds simply did not name the field, so it
    defaulted to None.

    Every scanned PDF that fell through here therefore arrived at the cage with
    no net, and `conservation.net_plus_tax_equals_gross` answered INDETERMINATE
    on exactly the documents the fall-through exists to rescue."""
    characters = _record(backend="pdf_text_layer", net_paise=48_000, party="SHARMA")
    picture = _record(backend="free_ocr", read=False, confidence=0.0)

    merged = _preferring_the_characters(characters, picture)

    assert merged.net_paise == 48_000


def test_the_net_falls_through_to_the_picture_when_the_characters_have_none() -> None:
    """The same preference the four named fields get: characters first, picture
    only where the characters read nothing."""
    characters = _record(backend="pdf_text_layer", net_paise=None, read=False)
    picture = _record(backend="free_ocr", net_paise=12_345, confidence=0.4)

    assert _preferring_the_characters(characters, picture).net_paise == 12_345


def test_a_stated_zero_confidence_is_never_promoted_to_exact() -> None:
    """THE FAIL-OPEN DEFAULT, and it sat on the only tier allowed to auto-post.

    The line read `characters.confidence_of(name) or EXACT`. `or` treats a
    stated 0.0 as absent and hands back 1.0 - the score that clears
    `AUTO_POST_FLOOR`. No reader produces a 0.0 on a read field today, so it was
    unreachable; a default that is safe only because nothing exercises it is a
    trap set for whoever writes the next reader."""
    characters = _record(backend="pdf_text_layer", total_paise=100, confidence=0.0)
    picture = _record(backend="free_ocr", read=False, confidence=0.0)

    merged = _preferring_the_characters(characters, picture)

    assert merged.per_field_confidence["total_paise"] == 0.0
    assert merged.per_field_confidence["total_paise"] != EXACT


def test_the_merge_never_claims_the_text_layer_for_a_field_the_picture_read() -> None:
    """THE CONTROL THAT MATTERS MOST. `AUTO_POST_ALLOWED_TIERS` is
    {pdf_text_layer, typed_text} and the cage reads `per_field_source`, so a
    merged field wrongly stamped `pdf_text_layer` would be a photograph posting
    itself without anybody looking."""
    characters = _record(backend="pdf_text_layer", party="SHARMA")
    picture = _record(backend="free_ocr", total_paise=56_640, confidence=0.4)

    merged = _preferring_the_characters(characters, picture)

    assert merged.per_field_source["party"] == "pdf_text_layer"
    assert merged.per_field_source["total_paise"] == "free_ocr"


# =============================================================================
# THE LADDER RECEIVES LABELS, NOT LETTERS
# =============================================================================
#
# `BILL_LABELS` is built by SPREADING `labels.DATE_LABEL` with `*`, and
# `looks_scanned` uses it as a plain substring test:
#
#     return not any(label in printed for label in BILL_LABELS)
#
# That combination is why the shape of `DATE_LABEL` is a routing decision and
# not a typing preference. It was a bare string until 2026-08-15; `*"DATE"`
# spreads to `'D', 'A', 'T', 'E'`, raises nothing, and every one of those is a
# substring of almost any English page. MEASURED on one line of ordinary prose:
#
#     'SHIPPING NOTE FOR RAO TRADERS, ELEVEN CARTONS DELIVERED'
#         real BILL_LABELS      -> []            correctly: not a bill
#         with the letters      -> ['D','A','T','E']
#
# So the regression does not fire a wrong label occasionally. It makes
# `looks_scanned` answer False for EVERY PDF carrying twenty characters of any
# text at all, the scanned rung stops being reached, and the whole
# picture-of-a-bill path goes quiet without one test failing or one line logged.
# A silent routing death is worse than a crash, which is why this is asserted
# on the constant rather than left to the type checker.


def test_bill_labels_is_built_from_whole_labels_and_never_single_characters() -> None:
    """THE GUARD ON THE SPREAD. Every entry must be a word. A one-character
    entry here means `DATE_LABEL` went back to being a string and `*` unpacked
    it into letters."""
    assert BILL_LABELS, "an empty vocabulary calls every PDF a scan"
    for label in BILL_LABELS:
        assert len(label) > 1, (
            f"{label!r} is a single character. `BILL_LABELS` is used as a "
            "substring test, so this entry matches nearly every page and stops "
            "`looks_scanned` from ever routing to the picture rung."
        )


def test_the_whole_date_family_reached_the_ladder_and_not_its_letters() -> None:
    """The spread carried the LABELS. `DATE` and its four longer spellings are
    each present as words, and none of the letters of `DATE` is present as an
    entry of its own."""
    for label in DATE_LABEL:
        assert label in BILL_LABELS, label
    assert not set("DATE") & set(BILL_LABELS)


def test_ordinary_prose_is_still_not_mistaken_for_a_bill() -> None:
    """THE CONTROL, stated as behaviour rather than as a shape. A delivery note
    prints none of this vocabulary, so nothing in `BILL_LABELS` may match it. If
    the letters ever get in, this is the line that catches it."""
    prose = "SHIPPING NOTE FOR RAO TRADERS, ELEVEN CARTONS DELIVERED"

    assert [label for label in BILL_LABELS if label in prose] == []

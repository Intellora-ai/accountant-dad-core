"""What the RUNNING APPLICATION hands an uploaded file to. Not what it could.

THE MEASURED GAP THIS CLOSES
----------------------------
Every routing test in this repository before 2026-08-13 asserted about
`ladder.Ladder()`, constructed by hand. Not one asserted about the object
`accountant/web/app.py` actually builds, and the two had drifted apart:
`registry.DEFAULT_BACKEND` was `typed_text`, so the router with the readers in
it was never reached by a request. Measured on the day, through
`registry.build(DEFAULT_BACKEND)` — the call `app.py:1444` makes:

    GT-0041.png   image/png          all four fields not_found
    GT-0061.jpg   image/jpeg         all four fields not_found
    GT-0021.pdf   application/pdf    all four fields not_found

and through `registry.build("ladder")`, the same three files:

    GT-0021.pdf   date 2026-09-21, party BALFOUR BEATTY VINCI JV - HS2 (N2),
                  total 58410 paise, tax 8910 paise

`app.py:340` `UPLOAD_MEDIA_TYPES` accepted all three kinds the whole time. So
the product ACCEPTED a PDF and then handed it to a regex written for a sentence
somebody typed. A test suite that only ever built `Ladder()` itself could not
see that, because the thing it built was never the thing that ran.

WHY THESE ASSERTIONS ARE ABOUT `record.backend` AND NOT ABOUT FIELDS
--------------------------------------------------------------------
`backend` on a returned record names the RUNG that answered — `typed_text`,
`pdf_text_layer`, `free_ocr` — and a refusal a router made itself is stamped
`ladder`. That is a fact about routing and it survives a machine with no
`tesseract` installed, which a field assertion does not. Most of the cases below
are therefore deliberately UNREADABLE bytes with an honest media type on them:
what comes back is still every field unread, but it is the intended rung's own
refusal, under the intended rung's name, which is exactly and only the claim
this file makes.

`test_the_default_reads_a_real_pdf_end_to_end` is the one that proves a reading
rather than a route, because a route to a reader that reads nothing is not a
product.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That any reader is accurate. It grades nothing. `docs/EXTRACTION_MEASURED.md`
and the Ground-Truth Pack carry the numbers, and the picture rung's numbers on
the corpus are poor and stated.

That a photograph is read on the deployed machine. CORRECTED 2026-08-13: this
said the container image installs no `tesseract` binary on purpose. The owner
reversed that the same day, so the image installs `tesseract-ocr` and
`tesseract-ocr-eng` and `tests/test_deploy_artefacts.py` asserts THAT instead.
What the image can now do is run the engine; how well it reads is the corpus
number in `docs/EXTRACTION_MEASURED.md`, and it is poor. On any machine without
the binary the picture rung still answers `freeocr.ENGINE_MISSING`, a refusal
in plain words and not a crash — routing to a rung that says "install this" is
still routing, and still better than a regex inventing a number.
"""

from __future__ import annotations

import datetime
import pathlib
import struct
import time
import zlib

import pytest

from accountant.extract import registry
from accountant.extract.adapter import ExtractedRecord
from accountant.web import app
from tests.test_textlayer import pdf_bytes
from tests.test_upload import multipart_body, send
from tests.test_web import demo_company, fake_backend, serving

REPO = pathlib.Path(__file__).resolve().parent.parent
DOCUMENTS = REPO / "artifacts" / "ground_truth" / "documents"

#: A bill whose four fields are all readable, so a route that reaches the PDF
#: rung can be told apart from a route that reaches it and reads nothing.
READABLE_BILL: tuple[str, ...] = (
    "TAX INVOICE",
    "SHARMA TRADERS",
    "DATE: 2026-04-01",
    "GST                                                  188.32",
    "TOTAL                                              1,234.56",
)

#: Honest bytes for each kind, and honestly not documents. A PNG signature and
#: a JFIF signature are enough to be declared as pictures; there is no image
#: inside either, which is what makes the answer the same on every machine.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32
NOT_A_PDF = b"this is not a pdf at all"


def rung_for(data: bytes, mime: str) -> str:
    """The rung that answered, through the one call `app.py:1444` makes.

    `default_extractor()` and not `build("ladder")`. Naming the backend here
    would make this file assert about a backend somebody chose in the test,
    which is the mistake the whole module exists to catch.
    """
    return registry.default_extractor().extract(data, mime).backend


@pytest.mark.parametrize(
    ("mime", "data", "rung"),
    [
        ("application/pdf", NOT_A_PDF, "pdf_text_layer"),
        ("image/png", PNG, "free_ocr"),
        ("image/jpeg", JPEG, "free_ocr"),
        ("text/plain", b"paid Sharma Traders 4200", "typed_text"),
    ],
)
def test_the_shipped_default_hands_each_uploaded_kind_to_its_own_reader(
    mime: str, data: bytes, rung: str
) -> None:
    """The flip, at the seam. One assertion per media type `UPLOAD_MEDIA_TYPES`
    accepts, plus the typed-text case that must not have moved."""
    assert rung_for(data, mime) == rung


def test_the_default_reads_a_real_pdf_end_to_end() -> None:
    """A route to a reader that reads nothing is not a product.

    Hand-built bytes rather than a corpus file, so this fails when the ROUTING
    or the READING breaks and not when somebody regenerates `artifacts/`.
    """
    record = registry.default_extractor().extract(
        pdf_bytes(list(READABLE_BILL)), "application/pdf"
    )

    assert record.backend == "pdf_text_layer"
    assert record.date == datetime.date(2026, 4, 1)
    assert record.total_paise == 123456
    assert record.tax_paise == 18832


def test_the_default_still_refuses_a_kind_no_rung_reads_and_says_what_to_do() -> None:
    """The control. The flip widened what is read; it must not have widened
    what is CLAIMED. A refusal the router made itself is stamped `ladder`."""
    record = registry.default_extractor().extract(b"PK\x03\x04", "application/zip")

    assert record.backend == "ladder"
    assert all(
        source.startswith("not_found: ") for source in record.per_field_source.values()
    )
    assert set(record.per_field_source) == set(ExtractedRecord.FIELDS)


def test_a_photograph_of_a_bill_is_no_longer_met_by_a_regex_for_typed_text() -> None:
    """The defect, named. `typed_text` handed a PNG used to answer under its
    own name, having run a money pattern over the compressed bytes.

    Asserted as an INEQUALITY against the old default rather than as the new
    rung's name, so it keeps failing if some third backend takes over the image
    route without anybody deciding to.
    """
    real_png = (DOCUMENTS / "GT-0041.png").read_bytes()

    assert rung_for(real_png, "image/png") != "typed_text"


# =============================================================================
# THE WHOLE LIVE PATH, NOT THE REGISTRY
# =============================================================================
#
# Everything above asks `registry.default_extractor()` directly. That is the
# call `app.py:1444` makes, but it is not proof that the object it returns
# SURVIVES to the request: `configure()` wraps it in `registry.guarded()` and
# hangs it on `Runtime.extractor`, and `_run` reads it from there. A wrapper
# that lost the routing, or a second construction site that quietly built
# something else, would pass every assertion above.
#
# So these two build a Runtime exactly as `configure()` does — through
# `configure()` — and read the rung off the object the request handler would
# use. `tests/test_web.py::serving` is the one spin-up path in this suite and
# it names no backend, which is what makes it usable as evidence here.


@pytest.mark.parametrize(
    ("mime", "data", "rung"),
    [
        ("application/pdf", NOT_A_PDF, "pdf_text_layer"),
        ("image/png", PNG, "free_ocr"),
        ("image/jpeg", JPEG, "free_ocr"),
    ],
)
def test_the_runtime_the_app_configures_carries_the_routing_to_the_request(
    mime: str, data: bytes, rung: str, tmp_path: pathlib.Path
) -> None:
    """`Runtime.extractor`, built by `configure()`, is what `_run` hands the
    upload to. Read off the live object rather than rebuilt here."""
    with serving(
        demo_company(), fake_backend(), store_path=tmp_path / "app.db"
    ) as _base:
        live = app.runtime().extractor.extract(data, mime)

    assert live.backend == rung


def test_an_uploaded_pdf_is_read_over_a_real_socket_and_the_page_says_so(
    tmp_path: pathlib.Path,
) -> None:
    """The product claim, end to end: a person uploads a bill and the figures
    on it come back. Nothing is injected — the app chooses its own backend."""
    body = multipart_body(data=pdf_bytes(list(READABLE_BILL)))

    with serving(
        demo_company(), fake_backend(), store_path=tmp_path / "app.db"
    ) as base:
        status, page = send(base, body)
        drafts = list(app.DRAFTS.values())

    assert status == 200
    assert "Something in Accountant Dad broke" not in page
    assert len(drafts) == 1
    read = drafts[0].record
    assert read.backend == "pdf_text_layer"
    assert (read.date, read.total_paise, read.tax_paise) == (
        datetime.date(2026, 4, 1),
        123456,
        18832,
    )


# =============================================================================
# UNTRUSTED BYTES IN FRONT OF pypdf AND Pillow, IN THE WEB PROCESS
# =============================================================================
#
# THIS IS WHAT THE FLIP ACTUALLY COSTS, and it is the half `D-30` left open:
# `D-30` approved the two reader MODULES, and this change puts them on the
# upload ROUTE, where the bytes come from a stranger. Two third-party parsers
# now see input nobody here wrote, inside the customer-facing process.
#
# The required answer is a plain refusal — never a traceback out of the handler,
# never a hang. It is not asserted by reading the `try` blocks in
# `freeocr._reading` and `textlayer`: a guard that exists is not a guard that
# fires, and the whole point of a malformed input is that it takes the path
# nobody drew. So each of these drives real bad bytes through the real default
# and asserts on what came back.
#
# `tests/test_chaos_corpus.py` drives two hundred more of these; what it does
# NOT do is drive them through this default, which is stated there.


@pytest.mark.parametrize(
    ("label", "data", "mime"),
    [
        ("a PDF header and nothing else", b"%PDF-1.7\n", "application/pdf"),
        (
            "a PDF truncated mid-object",
            pdf_bytes(list(READABLE_BILL))[:120],
            "application/pdf",
        ),
        (
            "a PDF whose bytes are random",
            b"%PDF-1.4\n" + bytes(range(256)) * 4,
            "application/pdf",
        ),
        ("a PNG signature with no image", PNG, "image/png"),
        ("a JFIF header with no frame", JPEG, "image/jpeg"),
        (
            "a PNG whose chunks are junk",
            b"\x89PNG\r\n\x1a\n" + b"\xff" * 512,
            "image/png",
        ),
        ("empty bytes claiming to be a PDF", b"", "application/pdf"),
        ("empty bytes claiming to be a photo", b"", "image/jpeg"),
    ],
)
def test_malformed_bytes_reach_a_refusal_rather_than_a_traceback(
    label: str, data: bytes, mime: str
) -> None:
    """Eight ways to be a broken document, through the shipped default.

    The assertion is deliberately three-part. A record at all proves nothing
    escaped; four stated `not_found`s prove no field was invented from the
    wreckage; and a readable sentence on each proves the person is told
    something they can act on rather than being handed a blank.
    """
    record = registry.default_extractor().extract(data, mime)

    assert set(record.per_field_source) == set(ExtractedRecord.FIELDS), label
    for name in ExtractedRecord.FIELDS:
        assert getattr(record, name) is None, (label, name)
        said = record.per_field_source[name]
        assert said.startswith("not_found: "), (label, said)
        assert len(said) > len("not_found: ") + 20, (label, said)


@pytest.mark.parametrize(
    ("label", "data", "mime"),
    [
        ("a truncated PDF", pdf_bytes(list(READABLE_BILL))[:120], "application/pdf"),
        ("a picture with no picture in it", PNG, "image/png"),
    ],
)
def test_malformed_bytes_are_answered_promptly_rather_than_hanging(
    label: str, data: bytes, mime: str
) -> None:
    """A hang is an outage that no exception handler catches.

    The bound is generous on purpose — this asserts that the answer is bounded
    at all, not that it is fast, because a number tuned to this machine is a
    test about this machine. `freeocr.READING_DEADLINE_SECONDS` is 30, so
    anything under it also proves the picture path did not reach the engine and
    wait out its deadline on a file with no picture in it.
    """
    started = time.monotonic()
    registry.default_extractor().extract(data, mime)
    took = time.monotonic() - started

    assert took < 10.0, f"{label} took {took:.1f}s"


def test_a_malformed_upload_is_a_page_and_not_a_503(tmp_path: pathlib.Path) -> None:
    """The refusal has to survive the request handler, not only the reader.

    `handle_one_request` turns any escaping exception into 503 "Something in
    Accountant Dad broke", which tells a person the application is broken when
    their file is merely damaged. This is the one assertion that covers the
    whole path a stranger's bytes actually take.
    """
    broken = pdf_bytes(list(READABLE_BILL))[:120]

    with serving(
        demo_company(), fake_backend(), store_path=tmp_path / "app.db"
    ) as base:
        status, page = send(base, multipart_body(data=broken))

    assert status == 200
    assert "Something in Accountant Dad broke" not in page
    assert "Nothing was written to your Tally" in page


def test_a_picture_bomb_is_refused_rather_than_allocating_the_machine() -> None:
    """A PNG header may DECLARE any size it likes; the file need not carry it.

    Pillow's own `MAX_IMAGE_PIXELS` guard is what stands here, and this asserts
    it FIRES on the shipped path rather than that it exists — it raises
    `DecompressionBombError`, which is an `Exception`, so `freeocr._reading`
    turns it into a refusal like any other. Without the guard this is an
    allocation of about 24 GB inside the web process, which is not an exception
    anybody catches.
    """
    header = struct.pack(">II", 60000, 60000) + b"\x08\x02\x00\x00\x00"
    chunk = (
        b"\x00\x00\x00\rIHDR" + header + struct.pack(">I", zlib.crc32(b"IHDR" + header))
    )
    record = registry.default_extractor().extract(
        b"\x89PNG\r\n\x1a\n" + chunk + b"\x00\x00\x00\x00IEND\xaeB`\x82", "image/png"
    )

    assert record.backend == "free_ocr"
    assert record.total_paise is None
    assert record.per_field_source["total_paise"].startswith("not_found: ")

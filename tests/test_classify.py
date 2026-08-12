"""What a pile of bytes actually is, decided by looking at the bytes.

WHY THIS FILE EXISTS
--------------------
The requirement is "anything in, safe processing out": the user may upload a
phone photo, a PDF, a Word document, a zip, or a file with no extension at all,
and the interface must never block them up front by type. What it must do is
work out what arrived and either read it or refuse it with a plain sentence.

Two things make that harder than it sounds:

    a declared type is a claim, not a fact
    a file that cannot be read must not crash the process

The first is ordinary, not adversarial. Browsers guess. Phones rename. A `.pdf`
that is really a JPEG is a Tuesday, not an attack. So the classifier trusts
magic bytes over the declared MIME type and over the extension, always, and a
test below pins that direction so nobody reverses it later for convenience.

WHAT IT DELIBERATELY DOES NOT DO
---------------------------------
It does not execute, shell out to, or unzip anything. DOCX and XLSX are zip
archives, and refusing to extract from them removes the zip-bomb surface as a
side effect of a decision made for a different reason - D-23 excluded DOCX on
purpose. Accepting a file is not the same as reading it.

WHAT THIS FILE DOES NOT PROVE
------------------------------
It does not prove the file is a bill. A cat photo is a perfectly valid JPEG and
classifies as one; stopping it is the reader's job and then the decision
layer's. This module answers one question only.

NO NETWORK, NO IO, NO SUBPROCESS. Every test here is bytes in, verdict out.
"""

from __future__ import annotations

import pytest

from accountant.cage.classify import (
    READABLE,
    FileKind,
    classify,
)

PDF = b"%PDF-1.7\n1 0 obj\n"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
ZIP = b"PK\x03\x04\x14\x00\x00\x00\x08\x00"
GIF = b"GIF89a\x01\x00\x01\x00"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 "
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00"


# ---- the four types we can read ---------------------------------------------


def test_a_pdf_is_recognised() -> None:
    assert classify(PDF).kind is FileKind.PDF


def test_a_jpeg_is_recognised() -> None:
    assert classify(JPEG).kind is FileKind.JPEG


def test_a_png_is_recognised() -> None:
    assert classify(PNG).kind is FileKind.PNG


def test_plain_text_is_recognised() -> None:
    assert classify(b"cement bags 1000 from Test Supplier").kind is FileKind.TEXT


def test_the_four_readable_kinds_are_named_in_one_place() -> None:
    """A second list of supported types drifts from the first. The refusal
    sentence and the reader both read this one."""
    assert READABLE == (FileKind.TEXT, FileKind.PDF, FileKind.JPEG, FileKind.PNG)


# ---- bytes beat the declared type, always -----------------------------------


def test_a_pdf_declared_as_a_jpeg_is_still_a_pdf() -> None:
    """The declared type is a claim. The bytes are the fact."""
    assert classify(PDF, declared_mime="image/jpeg").kind is FileKind.PDF


def test_a_jpeg_declared_as_a_pdf_is_still_a_jpeg() -> None:
    """A phone that renames a file, or a browser that guesses, is ordinary -
    not an attack. This has to work, not merely fail safely."""
    assert classify(JPEG, declared_mime="application/pdf").kind is FileKind.JPEG


def test_the_control_an_honest_declaration_is_not_treated_as_a_lie() -> None:
    """THE CONTROL. A classifier that ignored the declared type entirely would
    pass the two tests above while proving nothing about whether it reads bytes
    at all - so here the declaration agrees and the answer is the same."""
    assert classify(PNG, declared_mime="image/png").kind is FileKind.PNG


def test_a_mismatch_between_the_claim_and_the_bytes_is_recorded() -> None:
    """Not an error - an observation. It is worth knowing how often uploads
    arrive mislabelled, and the audit line is where that is counted."""
    assert classify(PDF, declared_mime="image/jpeg").declared_disagreed is True


def test_the_control_a_matching_declaration_records_no_disagreement() -> None:
    assert classify(PDF, declared_mime="application/pdf").declared_disagreed is False


# ---- everything else is accepted, identified, and refused -------------------


def test_a_zip_is_identified_rather_than_guessed_at() -> None:
    """A DOCX is a zip. Saying "this looks like a zip archive" is more use to
    the person than "unsupported file"."""
    result = classify(ZIP, declared_mime="application/vnd.ms-word")
    assert result.kind is FileKind.UNSUPPORTED
    assert "zip" in result.detected.lower()


def test_a_gif_is_refused_and_named() -> None:
    result = classify(GIF)
    assert result.kind is FileKind.UNSUPPORTED
    assert "gif" in result.detected.lower()


def test_a_webp_is_refused_and_named() -> None:
    result = classify(WEBP)
    assert result.kind is FileKind.UNSUPPORTED
    assert "webp" in result.detected.lower()


def test_a_heic_is_refused_and_named() -> None:
    """The default format of an iPhone photo, so this refusal will be seen."""
    result = classify(HEIC)
    assert result.kind is FileKind.UNSUPPORTED
    assert "heic" in result.detected.lower()


def test_every_refusal_says_what_is_supported() -> None:
    """A dead end is a defect. The person must be told what would work."""
    said = classify(ZIP).reason.lower()
    assert "pdf" in said and "jpeg" in said and "png" in said


def test_the_control_a_readable_file_carries_no_refusal_reason() -> None:
    """THE CONTROL on the test above. A `reason` that was always populated would
    pass it while telling us nothing."""
    assert classify(PDF).reason == ""


# ---- the awkward cases, which are the point ---------------------------------


def test_an_empty_file_is_refused_and_does_not_crash() -> None:
    result = classify(b"")
    assert result.kind is FileKind.UNSUPPORTED
    assert result.reason


def test_a_single_byte_does_not_crash_the_classifier() -> None:
    """Too short for any magic number. The classifier must not index past the
    end of it. One printable byte IS plain text, and that is the honest answer -
    it then reaches the text reader, finds no bill in it, and blocks there."""
    assert classify(b"%").kind is FileKind.TEXT


def test_a_truncated_pdf_header_is_not_treated_as_a_pdf() -> None:
    """`%PD` is three bytes of a five-byte marker.

    THIS TEST WAS WRONG WHEN FIRST WRITTEN and asserted `UNSUPPORTED`. Three
    printable ASCII bytes are plain text, so "not a PDF" was right and
    "therefore unsupported" did not follow. The claim that matters is that a
    prefix never counts as the real thing - accepting one would hand a corrupt
    file to a parser that then has to survive it. That is what is asserted now.
    """
    assert classify(b"%PD").kind is not FileKind.PDF


def test_a_binary_prefix_of_a_readable_type_is_not_that_type() -> None:
    """The same claim where the bytes are not text, so the answer really is a
    refusal rather than a fall-through to the text reader."""
    assert classify(b"\x89PN").kind is FileKind.UNSUPPORTED


def test_random_bytes_are_refused_rather_than_read_as_text() -> None:
    """Binary that happens to decode is still binary. A NUL byte is the tell
    that this is not something a person typed."""
    assert classify(b"\x00\x01\x02\x03garbage\x00").kind is FileKind.UNSUPPORTED


def test_text_that_is_not_utf_eight_is_refused() -> None:
    """A lone continuation byte cannot be decoded, and guessing an encoding is
    how a rupee amount becomes a different number."""
    assert classify(b"\xff\xfe not utf-8 at all").kind is FileKind.UNSUPPORTED


def test_utf_eight_with_devanagari_is_text() -> None:
    """Indian bills carry Devanagari. Refusing them as "not text" would be a
    defect that only shows up on the customer's real documents."""
    assert classify("रु. 1000 के लिए".encode()).kind is FileKind.TEXT


def test_a_classification_always_carries_a_detected_label() -> None:
    """Even for a readable file. The audit line records what arrived, and a
    blank there cannot answer "what did the user actually send"."""
    for data in (PDF, JPEG, PNG, b"words", ZIP, b""):
        assert classify(data).detected


def test_nothing_is_ever_raised_however_strange_the_bytes() -> None:
    """The one behaviour the chaos corpus exists to check, asserted directly.
    A classifier that raises turns a refusal into a stack trace."""
    strange = (b"", b"\x00", b"%", b"\xff" * 100, ZIP[:2], PDF[:1], bytes(range(256)))
    for data in strange:
        assert classify(data).kind in set(FileKind)


# ---- determinism ------------------------------------------------------------


def test_the_same_bytes_classify_the_same_twice() -> None:
    """Rule 11.2.10. No clock, no cache, no randomness."""
    assert classify(PDF, declared_mime="x") == classify(PDF, declared_mime="x")


def test_a_float_where_bytes_belong_raises_rather_than_being_coerced() -> None:
    with pytest.raises(TypeError):
        classify("a string, not bytes")  # type: ignore[arg-type]

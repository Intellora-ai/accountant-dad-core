"""Phase 8 PR-1 — the generated ground-truth corpus, and whether it can lie.

WHAT THIS FILE IS FOR
---------------------
`artifacts/ground_truth/` claims to be 100 documents whose correct answers are
known. Three separate things have to be true for that claim to mean anything,
and each of them fails silently:

    the corpus is what it says it is    100 cases, five types, five categories,
                                        every case labelled, hashed and sourced
    the answers came from the truth     `expected` is a projection of
                                        `raw_fields`, never a reader's output
    the scorer can tell right from      an oracle that answers from the
    wrong                               canonical truth scores 100/100, so a
                                        zero is a real zero

The third is the one people forget. Every backend in this repository scores
zero on this corpus. If the comparator were broken it would also score zero,
and the two are indistinguishable without a case that is supposed to score
full marks and does.

WHY THE STUB SCORING ZERO IS THE POINT
--------------------------------------
The exit this replaced was "100/100 processed, complete or explicit
not_found". `UnavailableExtractor` satisfies that while reading nothing at all,
so it could not tell a working reader from a dead one. Here it scores 0/100 on
all five fields, which is the correct answer and is asserted below.

The 95-per-field gate is NOT wired into pytest. A benchmark that turns the
build red because a stub scores zero gets deleted in a week; the gate belongs
in the runner's exit code. What is asserted here is that the HARNESS is
correct — including that a stub scores nothing.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That any backend reads a bill. Nothing here is a reader, and the corpus is
`S2_ENGINEERING_BENCHMARK` — generated documents, never real-customer
accuracy. Real-bill accuracy stays `NOT_MEASURED` (`H-02`) and the production
backend stays `NOT_SELECTED` (`H-01`).

That the PDFs open in a viewer or the PNGs display. No library is permitted to
look. What is checked is structural — CRC32s that match their chunks, xref
offsets that point at the objects they name, a zip the standard library opens.
Visual rendering is `NOT_MEASURED`.
"""

from __future__ import annotations

import copy
import json
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from accountant.extract.adapter import (
    NOT_FOUND,
    ExtractedRecord,
    LineItem,
    StubExtractor,
    TypedTextExtractor,
    UnavailableExtractor,
)
from accountant.ingest.sources import ALL_SOURCES
from scripts import build_ground_truth as gt

REPO = Path(__file__).resolve().parent.parent
ROOT = REPO / "artifacts" / "ground_truth"
DOCUMENTS = ROOT / "documents"
VALIDATOR = REPO / "scripts" / "validate_ground_truth.py"

#: Evidence labels a GENERATED document may never carry. Both require real
#: bills, which is `H-02` and is not supplied. A generated PDF wearing one of
#: these is the exact mislabelling Q5 forbids.
REAL_EVIDENCE_LABELS = ("REAL_ANONYMISED_EVIDENCE", "HELD_OUT_CUSTOMER_LIKE_EVIDENCE")


def records() -> list[dict[str, Any]]:
    return gt.load_records(ROOT)


def document(record: dict[str, Any]) -> bytes:
    return (DOCUMENTS / str(record["document"])).read_bytes()


def run_validator(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(VALIDATOR), "--root", str(root), "--json"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO,
    )


@pytest.fixture
def corpus_copy(tmp_path: Path) -> Path:
    """A writable copy, so a mutant never touches the committed corpus."""
    target = tmp_path / "ground_truth"
    shutil.copytree(ROOT, target)
    return target


def edit_case(root: Path, case_id: str, change: dict[str, Any]) -> None:
    path = root / "cases" / f"{case_id}.json"
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    record: dict[str, Any] = dict(loaded)
    record.update(change)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


# =============================================================================
# PROVENANCE — the assertion two invalidated measurements in this project cost
# =============================================================================


def test_the_package_under_test_is_the_one_in_this_worktree() -> None:
    """Twice now a measurement was void because `accountant` resolved to the
    main checkout. A number measured against a different tree is not a number
    about this branch, and `INVALIDATED` is the only honest label for it."""
    import accountant

    resolved = str(Path(accountant.__file__).resolve())
    assert resolved.startswith(str(REPO.resolve())), (
        f"accountant resolves to {resolved}, which is outside {REPO}; every "
        "number from this run would be INVALIDATED"
    )


# =============================================================================
# THE CORPUS IS WHAT IT SAYS IT IS
# =============================================================================


def test_the_corpus_holds_exactly_one_hundred_cases() -> None:
    assert len(records()) == 100
    assert gt.CASE_COUNT == 100


def test_every_one_of_the_five_input_types_carries_twenty_cases() -> None:
    """The mutant this catches is a type dropped from the harness. A corpus of
    80 with four types passes every per-case assertion in this file."""
    counted: dict[str, int] = dict.fromkeys(gt.INPUT_TYPES, 0)
    for record in records():
        counted[str(record["input_type"])] += 1

    assert counted == {"text": 20, "PDF": 20, "PNG": 20, "JPG": 20, "DOCX": 20}
    assert set(counted) == set(gt.INPUT_TYPES)
    assert len(gt.INPUT_TYPES) == 5


def test_every_one_of_the_five_difficulty_categories_carries_twenty_cases() -> None:
    counted: dict[str, int] = dict.fromkeys(gt.CATEGORIES, 0)
    for record in records():
        counted[str(record["category"])] += 1

    assert counted == {
        "clean": 20,
        "layout": 20,
        "line_items": 20,
        "tax_labels": 20,
        "adversarial": 20,
    }


def test_the_two_axes_are_two_views_of_the_same_hundred_cases() -> None:
    """5x5, four per cell. Not 200 cases: every case has one type AND one
    category, so the two tables above must reconcile to the same total."""
    grid = gt.matrix(records())

    assert sum(sum(row.values()) for row in grid.values()) == 100
    for input_type, row in grid.items():
        assert set(row) == set(gt.CATEGORIES), input_type
        assert all(n == gt.PER_CELL for n in row.values()), (input_type, row)


def test_every_case_id_is_unique() -> None:
    ids = [str(r["case_id"]) for r in records()]

    assert len(set(ids)) == len(ids) == 100


def test_every_case_carries_a_corpus_label_a_source_label_and_provenance() -> None:
    for record in records():
        case_id = record["case_id"]
        assert record["corpus_label"] == gt.CORPUS_LABEL, case_id
        assert record["source_label"] == gt.SOURCE_LABEL, case_id
        assert str(record["provenance"]["content_source"]).strip(), case_id
        assert record["provenance"]["generated_by"] == "scripts/build_ground_truth.py"
        assert record["generation_version"] == gt.GENERATION_VERSION, case_id
        assert str(record["generation_hash"]).strip(), case_id
        assert str(record["sha256"]).strip(), case_id


def test_no_generated_document_claims_to_be_real_or_customer_evidence() -> None:
    """The cheapest way to make a corpus look stronger is to relabel it.

    Every byte here was generated by `scripts/build_ground_truth.py`. Calling
    any of it `REAL_ANONYMISED_EVIDENCE` would be filling the `H-02` gap by
    renaming it, which Q5 forbids in those words.
    """
    offenders = [
        r["case_id"] for r in records() if r["corpus_label"] in REAL_EVIDENCE_LABELS
    ]

    assert offenders == [], (
        f"generated documents labelled as real evidence: {offenders}. Real-bill "
        "accuracy is NOT_MEASURED and stays that way until H-02 supplies bills"
    )


def test_every_case_has_expected_outputs_for_all_five_scored_fields() -> None:
    for record in records():
        expected = record["expected"]
        assert set(expected) == set(gt.SCORED_FIELDS), record["case_id"]
        for name in gt.SCORED_FIELDS:
            assert name in expected, (record["case_id"], name)


def test_the_corpus_is_not_a_hundred_copies_of_one_document() -> None:
    """Controlled variation, measured rather than described."""
    seen = {gt.sha256_of(document(r)) for r in records()}
    parties = {str(r["raw_fields"]["party"]) for r in records()}
    totals = {str(r["raw_fields"]["total"]) for r in records()}
    layouts = {str(r["provenance"]["variation"]["layout"]) for r in records()}
    dates = {str(r["provenance"]["variation"]["date_format"]) for r in records()}
    taxes = {str(r["provenance"]["variation"]["tax_label"]) for r in records()}

    assert len(seen) == 100, "two cases render to identical bytes"
    assert len(parties) >= 20, f"only {len(parties)} distinct vendors"
    assert len(totals) >= 90, f"only {len(totals)} distinct totals"
    assert len(layouts) >= 4 and len(dates) >= 4 and len(taxes) >= 4


def test_the_corpus_uses_every_line_item_count_the_categories_promise() -> None:
    counts = {len(list(r["raw_fields"]["line_items"])) for r in records()}

    assert {0, 1, 3, 7} <= counts, f"line-item counts present: {sorted(counts)}"


def test_a_discount_is_carried_as_a_negative_line_and_not_as_an_exception() -> None:
    negatives = [
        r["case_id"]
        for r in records()
        if any(str(i["amount"]).startswith("-") for i in r["raw_fields"]["line_items"])
    ]

    assert negatives, "no case exercises a discount"


# =============================================================================
# THE ANSWERS CAME FROM THE TRUTH, NOT FROM A READER
# =============================================================================


def test_every_expected_block_is_a_projection_of_the_canonical_truth() -> None:
    """Recomputed from `raw_fields` by the one function allowed to make it."""
    for record in records():
        raw = {str(k): v for k, v in dict(record["raw_fields"]).items()}

        assert record["expected"] == gt.expected_from(raw), record["case_id"]


def test_every_generation_hash_is_the_hash_of_the_truth_beside_it() -> None:
    for record in records():
        raw = {str(k): v for k, v in dict(record["raw_fields"]).items()}
        gst = {str(k): str(v) for k, v in dict(record["gst_context"]).items()}

        assert record["generation_hash"] == gt.generation_hash(raw, gst), record[
            "case_id"
        ]


def test_every_document_hashes_to_the_sha_recorded_beside_it() -> None:
    for record in records():
        assert gt.sha256_of(document(record)) == record["sha256"], record["case_id"]


def test_every_committed_document_is_byte_identical_to_a_rebuild() -> None:
    """Reproducibility and tamper evidence in one assertion. A document edited
    by hand — to make a reader look better on it — stops being a rebuild."""
    built = {case.case_id: gt.render(case) for case in gt.build_cases()}

    for record in records():
        assert document(record) == built[str(record["case_id"])], record["case_id"]


def test_every_committed_case_file_is_byte_identical_to_a_rebuild() -> None:
    rebuilt, _ = gt.build_corpus()
    by_id = {str(r["case_id"]): r for r in rebuilt}

    for record in records():
        assert record == by_id[str(record["case_id"])], record["case_id"]


def test_the_conservation_law_holds_on_every_case() -> None:
    """subtotal + tax == total, and the items sum to the subtotal.

    Two quantities that must be equal need no expert and no label. This is the
    check that says the canonical truth is internally true, as opposed to
    merely written down.
    """
    from decimal import Decimal

    for record in records():
        raw = record["raw_fields"]
        subtotal = Decimal(str(raw["subtotal"]))
        tax = Decimal(str(raw["tax_amount"]))
        total = Decimal(str(raw["total"]))

        assert subtotal + tax == total, record["case_id"]
        items = list(raw["line_items"])
        if items:
            summed = sum((Decimal(str(i["amount"])) for i in items), Decimal(0))
            assert summed == subtotal, record["case_id"]


def test_the_rendered_document_actually_contains_the_answer_it_is_scored_on() -> None:
    """A case whose document does not carry its own truth is unanswerable, and
    would show up as a reader failing rather than as a corpus defect.

    JPG is exempt and that is the whole reason its slice is BLOCKED: the
    container has no image data, so nothing is on the page to be found. PNG
    carries the answer as pixels and is covered by the raster tests below.

    Each format is searched for the name IN ITS OWN ESCAPING. `(` is `\\(` in a
    PDF string and `&` is `&amp;` in OOXML, and a test that looked for the raw
    name would fail on a correctly escaped document — the usual fix for which
    is to drop the parentheses from the corpus and lose the case that has them.
    """
    skip = {"JPG", "PNG"}
    for record in records():
        if record["input_type"] in skip:
            continue
        party = str(record["expected"]["party"])
        data = document(record)
        if record["input_type"] == "DOCX":
            archive = zipfile.ZipFile(BytesIO(data))
            text = archive.read("word/document.xml").decode("utf-8")
            wanted = gt.xml_escape(party)
        elif record["input_type"] == "PDF":
            text = data.decode("utf-8", errors="replace")
            wanted = gt.pdf_escape(party)
        else:
            text = data.decode("utf-8")
            wanted = party

        assert wanted in text, record["case_id"]


# =============================================================================
# THE CONTAINERS ARE REALLY THOSE CONTAINERS — structurally, which is all that
# can be checked with no library permitted to look
# =============================================================================


def cases_of(input_type: str) -> list[dict[str, Any]]:
    return [r for r in records() if r["input_type"] == input_type]


def test_every_text_case_is_valid_utf8_and_declares_text_plain() -> None:
    for record in cases_of("text"):
        assert record["mime"] == "text/plain"
        document(record).decode("utf-8")


def test_every_pdf_has_a_cross_reference_table_whose_offsets_are_correct() -> None:
    """An xref that points at the wrong byte is the single most common way a
    hand-written PDF is broken, and it is checkable with arithmetic alone."""
    for record in cases_of("PDF"):
        data = document(record)
        assert data.startswith(b"%PDF-1.4"), record["case_id"]
        assert data.rstrip().endswith(b"%%EOF"), record["case_id"]
        start = int(data.rsplit(b"startxref", 1)[1].split(b"%%EOF")[0].strip())
        assert data[start : start + 4] == b"xref", record["case_id"]
        rows = data[start:].split(b"\n")
        size = int(rows[1].split()[1])
        for number in range(1, size):
            offset = int(rows[2 + number].split()[0])
            want = f"{number} 0 obj".encode("ascii")
            assert data[offset : offset + len(want)] == want, (
                record["case_id"],
                number,
            )


def test_every_png_has_matching_crcs_and_the_pixels_it_declares() -> None:
    for record in cases_of("PNG"):
        data = document(record)
        assert data[:8] == b"\x89PNG\r\n\x1a\n", record["case_id"]
        position = 8
        chunks: dict[bytes, bytes] = {}
        while position < len(data):
            length = struct.unpack(">I", data[position : position + 4])[0]
            kind = data[position + 4 : position + 8]
            payload = data[position + 8 : position + 8 + length]
            stored = struct.unpack(
                ">I", data[position + 8 + length : position + 12 + length]
            )[0]
            assert stored == zlib.crc32(kind + payload), (record["case_id"], kind)
            chunks[kind] = chunks.get(kind, b"") + payload
            position += 12 + length
        width, height, depth, colour = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
        pixels = zlib.decompress(chunks[b"IDAT"])

        assert (depth, colour) == (8, 0), record["case_id"]
        assert len(pixels) == (width + 1) * height, record["case_id"]
        assert b"IEND" in data, record["case_id"]


def test_every_png_really_has_glyphs_drawn_on_it() -> None:
    """A blank page would satisfy every structural check above.

    The corpus would then be twenty valid PNGs with nothing on them, and no
    reader could ever score, and the failure would look like the reader's.
    """
    for record in cases_of("PNG"):
        data = document(record)
        payload = b""
        position = 8
        while position < len(data):
            length = struct.unpack(">I", data[position : position + 4])[0]
            if data[position + 4 : position + 8] == b"IDAT":
                payload += data[position + 8 : position + 8 + length]
            position += 12 + length
        pixels = zlib.decompress(payload)
        ink = sum(1 for byte in pixels if byte < 250)

        assert ink > 500, f"{record['case_id']} looks blank: {ink} inked pixels"


def test_the_raster_renderer_refuses_a_character_it_cannot_draw() -> None:
    """The alternative is an empty box on the page, which is a silent blank
    with pixels. The guard is proved by making it fire."""
    with pytest.raises(gt.UnrenderableGlyph, match="no glyph"):
        gt.rasterise(["TOTAL ~ 100"], ink=0, rotate=False)


def test_some_png_cases_are_rotated_and_some_are_low_contrast() -> None:
    """The adversarial raster knobs are used, not merely available."""
    variations = [r["provenance"]["variation"] for r in cases_of("PNG")]

    assert any(bool(v["rotate"]) for v in variations)
    assert any(int(v["ink"]) > 0 for v in variations)


def test_every_docx_is_a_real_package_the_standard_library_can_open() -> None:
    for record in cases_of("DOCX"):
        archive = zipfile.ZipFile(BytesIO(document(record)))

        assert archive.testzip() is None, record["case_id"]
        assert "word/document.xml" in archive.namelist(), record["case_id"]
        assert "[Content_Types].xml" in archive.namelist(), record["case_id"]
        assert b"<w:t" in archive.read("word/document.xml"), record["case_id"]


def test_every_jpg_is_marker_framed_and_says_it_carries_no_image_data() -> None:
    """The honest half of the JPG slice. The framing is real; the picture is
    not there, which is why the slice is BLOCKED rather than counted."""
    for record in cases_of("JPG"):
        data = document(record)
        assert data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9", record["case_id"]
        position = 2
        markers: list[int] = []
        while position < len(data) - 2:
            assert data[position] == 0xFF, record["case_id"]
            markers.append(data[position + 1])
            length = struct.unpack(">H", data[position + 2 : position + 4])[0]
            position += 2 + length
        assert position == len(data) - 2, record["case_id"]
        assert 0xC0 not in markers, "a start-of-frame would mean there IS image data"
        assert record["format_fidelity"] == "container_only"


def test_the_blocked_slice_is_declared_with_its_reason_and_its_cost() -> None:
    """An honest 'this cannot be done' beats a quiet lie, and it has to be
    written where a reader of the numbers will see it."""
    manifest: Any = json.loads(
        (ROOT / "manifests" / "corpus.json").read_text(encoding="utf-8")
    )

    assert list(manifest["blocked_input_types"]) == ["JPG"]
    assert "Huffman" in manifest["blocked_input_types"]["JPG"]
    assert manifest["reachable_ceiling"] == 80, (
        "twenty JPG cases carry no readable content, so no reader can exceed "
        "80/100 per field while the slice is blocked"
    )
    assert manifest["gate_per_field"] == 95


# =============================================================================
# UK GOVERNMENT CONTENT — Q5 = C, checked against the published bytes
# =============================================================================


def test_every_public_supplier_name_appears_verbatim_in_the_cited_file() -> None:
    """The label is not a claim, it is a lookup. A supplier name invented here
    and described as coming from a department would fail on the file."""
    by_code = {source.code: source for source in ALL_SOURCES}
    texts = {
        code: source.fixture_path.read_text(encoding=source.encoding)
        for code, source in by_code.items()
    }

    for name, code in gt.PUBLIC_SUPPLIERS:
        assert code in by_code, f"{name} cites unknown department {code}"
        assert name in texts[code], (
            f"{name!r} is described as coming from the published {code} file "
            f"and does not appear in {by_code[code].fixture_path.name}"
        )


def test_the_public_content_is_drawn_from_every_department_available() -> None:
    used = {code for _, code in gt.PUBLIC_SUPPLIERS}

    assert used == {source.code for source in ALL_SOURCES}


def test_about_half_the_cases_carry_a_real_supplier_name() -> None:
    """Q5 = C: public data where it fits, invented elsewhere. Both, measured."""
    public = [
        r for r in records() if "verbatim" in str(r["provenance"]["content_source"])
    ]
    invented = [
        r for r in records() if "invented" in str(r["provenance"]["content_source"])
    ]

    assert len(public) + len(invented) == 100
    assert len(public) == 50 and len(invented) == 50


# =============================================================================
# THE SCORER CAN TELL RIGHT FROM WRONG
# =============================================================================


class OracleExtractor:
    """Answers from the canonical truth. NOT a reader — it never looks at the
    document at all; it is handed the answer key.

    It exists for one reason: every real backend scores zero on this corpus,
    and a comparator that always returned zero would look identical. This is
    the case that is supposed to score full marks, so a zero elsewhere is a
    measurement rather than a bug.
    """

    name = "oracle"

    def __init__(self, key: dict[str, dict[str, Any]], *, wrong: str = "") -> None:
        self._key = key
        self._wrong = wrong

    def extract(self, data: bytes, _mime: str) -> ExtractedRecord:
        import datetime

        expected = self._key[gt.sha256_of(data)]
        party = str(expected["party"])
        total = gt.paise(str(expected["total_amount"]))
        if self._wrong == "party":
            party = "SOMEBODY ELSE ENTIRELY"
        if self._wrong == "total_amount":
            total += 1
        return ExtractedRecord(
            date=datetime.date.fromisoformat(str(expected["date"])),
            party=party,
            total_paise=total,
            tax_paise=gt.paise(str(expected["tax_amount"])),
            line_items=tuple(
                LineItem(str(i["description"]), gt.paise(str(i["amount"])))
                for i in expected["line_items"]
            ),
            backend=self.name,
            per_field_source=dict.fromkeys(ExtractedRecord.FIELDS, self.name),
        )


def answer_key() -> dict[str, dict[str, Any]]:
    return {str(r["sha256"]): dict(r["expected"]) for r in records()}


def score(backend: object) -> gt.CorpusScore:
    return gt.score_corpus(backend, records(), DOCUMENTS)


def test_a_backend_that_answers_from_the_truth_scores_full_marks() -> None:
    """The disconfirming case for every zero in this file."""
    result = score(OracleExtractor(answer_key()))

    assert {n: s.correct for n, s in result.fields.items()} == dict.fromkeys(
        gt.SCORED_FIELDS, 100
    )
    assert result.passes_gate() is True
    assert result.silent_blanks == () and result.unclassified_failures == ()


@pytest.mark.parametrize("field_name", ["party", "total_amount"])
def test_one_wrong_field_costs_that_field_and_no_other(field_name: str) -> None:
    """Per-field independence. A scorer that collapsed the five into one would
    pass the test above and be useless for the gate, which is per field."""
    result = score(OracleExtractor(answer_key(), wrong=field_name))

    assert result.fields[field_name].correct == 0
    assert result.fields[field_name].fabricated == 100
    for other in gt.SCORED_FIELDS:
        if other != field_name:
            assert result.fields[other].correct == 100, other
    assert result.passes_gate() is False


@pytest.mark.parametrize(
    "backend", [StubExtractor(), UnavailableExtractor()], ids=["stub", "unavailable"]
)
def test_a_backend_that_reads_nothing_scores_zero_on_every_field(
    backend: object,
) -> None:
    """The owner's ruling, as a test.

    "A stub returning not_found for every case must score 0/100 for every
    required field and must not pass the accuracy gate." Ten of the hundred
    cases have no line items at all, so a naive comparator hands a silent
    backend 10/100 for agreeing by accident. It does not answer; nothing is
    never right.
    """
    result = score(backend)

    assert result.processed == 100
    assert result.explicit_not_found == 100
    assert {n: s.correct for n, s in result.fields.items()} == dict.fromkeys(
        gt.SCORED_FIELDS, 0
    )
    assert all(s.refused == 100 for s in result.fields.values())
    assert all(s.fabricated == 0 for s in result.fields.values())
    assert result.passes_gate() is False


def test_a_silent_backend_is_still_processed_and_still_leaves_no_blank() -> None:
    """The old exit, kept and reported separately. It is TRUE of the stub, and
    it is the reason it was never enough on its own."""
    result = score(StubExtractor())

    assert result.processed == 100
    assert result.with_provenance == 100
    assert result.silent_blanks == ()
    assert result.unclassified_failures == ()


def test_the_production_default_backend_is_measured_rather_than_assumed() -> None:
    """`typed_text` is `DEFAULT_BACKEND`. Against invoices it scores zero.

    CORRECTED 2026-08-13, PHASE 8 DECISION 1. This asserted `fabricated == 20`,
    and 20 was the true measurement of a backend that took the FIRST number in
    the document as the amount — on GT-0001 it read `GT/0001` and answered 100
    paise for a bill of 14750, sourced `typed_text`.

    The owner closed that: invoice-shaped text is REFUSED rather than guessed
    at. The 20 fabrications are now 20 refusals, so this pins **0**. The score
    is unchanged at zero correct, which is the point — refusing is not reading,
    and nothing here got better at reading a bill. What changed is that the
    twenty wrong totals no longer reach the ledger with a source on them.
    """
    result = score(TypedTextExtractor())

    assert result.processed == 100
    assert result.silent_blanks == ()
    assert result.unclassified_failures == ()
    assert result.fields["date"].correct == 0
    assert result.fields["party"].correct == 0
    assert result.fields["total_amount"].correct == 0
    assert result.fields["total_amount"].fabricated == 0
    assert result.fields["total_amount"].refused == 100
    assert result.passes_gate() is False


def test_the_scorer_reads_the_same_not_found_sentinel_the_adapter_writes() -> None:
    """Two spellings of `not_found` is how a refusal starts counting as an
    answer, and the corpus would then grade a silent backend as correct."""
    assert gt.NOT_FOUND == NOT_FOUND


def test_the_scorer_counts_an_exception_as_an_unclassified_failure() -> None:
    """A backend that raises must be named, never dropped from the denominator."""

    class Exploding:
        name = "exploding"

        def extract(self, _data: bytes, _mime: str) -> ExtractedRecord:
            raise RuntimeError("the reader fell over")

    result = score(Exploding())

    assert result.processed == 0
    assert len(result.unclassified_failures) == 100
    assert "the reader fell over" in result.unclassified_failures[0]
    assert result.passes_gate() is False


def test_the_scorer_never_writes_to_the_corpus() -> None:
    """A benchmark that can update its own answer key is not a benchmark."""
    before = {p: p.read_bytes() for p in sorted((ROOT / "cases").glob("GT-*.json"))}

    score(OracleExtractor(answer_key()))

    assert {p: p.read_bytes() for p in before} == before


# =============================================================================
# THE VALIDATOR — proved by making it fail, one injected defect at a time
# =============================================================================


def test_the_validator_passes_on_the_committed_corpus() -> None:
    result = run_validator(ROOT)
    report: Any = json.loads(result.stdout)

    assert report["findings"] == [], report["findings"]
    assert result.returncode == 0


def kinds(root: Path) -> list[str]:
    result = run_validator(root)
    report: Any = json.loads(result.stdout)
    assert result.returncode == 1, "the validator passed a corpus it should refuse"
    return sorted({str(f["kind"]) for f in report["findings"]})


def test_the_validator_catches_reader_output_pasted_over_the_expected_block(
    corpus_copy: Path,
) -> None:
    """THE mutant this whole design exists for.

    Somebody runs a reader, it disagrees, and they 'fix' the corpus. Nothing
    else in the repository would go red.
    """
    path = corpus_copy / "cases" / "GT-0001.json"
    record: Any = json.loads(path.read_text(encoding="utf-8"))
    poisoned = copy.deepcopy(dict(record))
    poisoned["expected"]["total_amount"] = "1.00"  # what typed_text actually says
    path.write_text(json.dumps(poisoned, indent=2) + "\n", encoding="utf-8")

    assert "EXPECTED_NOT_FROM_TRUTH" in kinds(corpus_copy)


def test_the_validator_catches_truth_edited_without_a_rebuild(
    corpus_copy: Path,
) -> None:
    """Editing `raw_fields` to agree with a reader is the other half of the
    same cheat. `expected` is re-derived so the first guard is satisfied, and
    the stale hash and the mismatch against the generator catch it anyway."""
    path = corpus_copy / "cases" / "GT-0002.json"
    record: Any = json.loads(path.read_text(encoding="utf-8"))
    poisoned = copy.deepcopy(dict(record))
    poisoned["raw_fields"]["party"] = "WHATEVER THE READER SAID"
    poisoned["expected"] = gt.expected_from(poisoned["raw_fields"])
    path.write_text(json.dumps(poisoned, indent=2) + "\n", encoding="utf-8")
    found = kinds(corpus_copy)

    assert "EXPECTED_NOT_FROM_TRUTH" not in found, "the cheat did evade the first net"
    assert "STALE_GENERATION_HASH" in found
    assert "CASE_NOT_A_REBUILD" in found


def test_the_validator_catches_a_self_consistent_case_file_that_is_still_a_lie(
    corpus_copy: Path,
) -> None:
    """The hardest version: edit the truth AND recompute both derived values.

    The case file is then internally perfect — `expected` is a projection of
    `raw_fields`, the generation hash is the hash of `raw_fields` — and it
    describes a document that says something else entirely. Found while
    proving the guard above rather than assumed away.
    """
    path = corpus_copy / "cases" / "GT-0012.json"
    record: Any = json.loads(path.read_text(encoding="utf-8"))
    poisoned = copy.deepcopy(dict(record))
    poisoned["raw_fields"]["party"] = "WHATEVER THE READER SAID"
    poisoned["expected"] = gt.expected_from(poisoned["raw_fields"])
    poisoned["generation_hash"] = gt.generation_hash(
        poisoned["raw_fields"], poisoned["gst_context"]
    )
    path.write_text(json.dumps(poisoned, indent=2) + "\n", encoding="utf-8")
    found = kinds(corpus_copy)

    assert found == ["CASE_NOT_A_REBUILD"], (
        "every other net was satisfied by the forgery; this is the one that "
        f"has to catch it. Found: {found}"
    )


def test_the_validator_catches_a_missing_expected_field(corpus_copy: Path) -> None:
    path = corpus_copy / "cases" / "GT-0003.json"
    record: Any = json.loads(path.read_text(encoding="utf-8"))
    poisoned = copy.deepcopy(dict(record))
    del poisoned["expected"]["tax_amount"]
    path.write_text(json.dumps(poisoned, indent=2) + "\n", encoding="utf-8")

    assert "MISSING_EXPECTED_FIELD" in kinds(corpus_copy)


def test_the_validator_catches_a_missing_hash(corpus_copy: Path) -> None:
    edit_case(corpus_copy, "GT-0004", {"generation_hash": ""})

    assert "MISSING_HASH" in kinds(corpus_copy)


def test_the_validator_catches_a_lost_corpus_label(corpus_copy: Path) -> None:
    edit_case(corpus_copy, "GT-0005", {"corpus_label": ""})

    assert "MISSING_SOURCE_LABEL" in kinds(corpus_copy)


def test_the_validator_catches_a_lost_provenance_block(corpus_copy: Path) -> None:
    edit_case(corpus_copy, "GT-0006", {"provenance": {}, "source_label": ""})

    assert "MISSING_SOURCE_LABEL" in kinds(corpus_copy)


def test_the_validator_catches_a_duplicate_case_id(corpus_copy: Path) -> None:
    source = (corpus_copy / "cases" / "GT-0007.json").read_text(encoding="utf-8")
    (corpus_copy / "cases" / "GT-0101.json").write_text(source, encoding="utf-8")

    assert "DUPLICATE_CASE_ID" in kinds(corpus_copy)


def test_the_validator_catches_a_dangling_rule_reference(corpus_copy: Path) -> None:
    """`rule_ids` is empty today and the rules corpus belongs to another
    workstream. An id that resolves to nothing is a citation that is not one."""
    edit_case(corpus_copy, "GT-0008", {"rule_ids": ["GST-RATE-18-NOTIONAL"]})

    assert "MISSING_RULE_REFERENCE" in kinds(corpus_copy)


def test_the_validator_catches_an_edited_document(corpus_copy: Path) -> None:
    target = corpus_copy / "documents" / "GT-0009.txt"
    target.write_bytes(target.read_bytes() + b"TOTAL 999999.00\n")
    found = kinds(corpus_copy)

    assert "STALE_DOCUMENT_HASH" in found
    assert "DOCUMENT_NOT_A_REBUILD" in found


def test_the_validator_catches_truth_that_does_not_balance(corpus_copy: Path) -> None:
    path = corpus_copy / "cases" / "GT-0010.json"
    record: Any = json.loads(path.read_text(encoding="utf-8"))
    poisoned = copy.deepcopy(dict(record))
    poisoned["raw_fields"]["total"] = "999999.00"
    poisoned["expected"] = gt.expected_from(poisoned["raw_fields"])
    path.write_text(json.dumps(poisoned, indent=2) + "\n", encoding="utf-8")

    assert "UNBALANCED" in kinds(corpus_copy)


def test_the_validator_catches_an_input_type_dropped_from_the_corpus(
    corpus_copy: Path,
) -> None:
    """Twenty cases deleted. Every per-case assertion still passes on the
    eighty that remain, which is exactly why the count is asserted separately."""
    for record in records():
        if record["input_type"] == "JPG":
            (corpus_copy / "cases" / f"{record['case_id']}.json").unlink()
    found = kinds(corpus_copy)

    assert "CASE_COUNT" in found
    assert "TYPE_COUNT" in found


def test_the_validator_is_not_vacuous(corpus_copy: Path) -> None:
    """A validator pointed at nothing reports nothing and exits happy."""
    for path in (corpus_copy / "cases").glob("GT-*.json"):
        path.unlink()

    assert "CASE_COUNT" in kinds(corpus_copy)


def test_the_builder_reports_a_hand_edited_corpus_under_check(
    corpus_copy: Path,
) -> None:
    """`--check` rebuilds and diffs, writing nothing. The second half of the
    tamper evidence: the validator checks claims, this checks the bytes."""
    target = corpus_copy / "documents" / "GT-0021.pdf"
    target.write_bytes(target.read_bytes().replace(b"TAX INVOICE", b"TAX INVOICF"))
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(REPO / "scripts" / "build_ground_truth.py"),
            "--root",
            str(corpus_copy),
            "--check",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO,
    )

    assert result.returncode == 1
    assert "GT-0021.pdf" in result.stdout


def test_the_builder_check_passes_on_the_committed_corpus() -> None:
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(REPO / "scripts" / "build_ground_truth.py"),
            "--root",
            str(ROOT),
            "--check",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO,
    )

    assert result.returncode == 0, result.stdout
    assert "differences: 0" in result.stdout

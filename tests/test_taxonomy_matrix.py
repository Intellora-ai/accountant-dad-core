"""The twelve published error types, pinned against what is actually proven.

`docs/TAXONOMY.md` states one classification per published error type. This file
is the third, hand-typed copy of that table, and it fails when the document, this
copy and `accountant/taxonomy/` stop agreeing about a type name, its order, its
source citation, its mapped detector or its classification.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
It does not prove that any detector catches any real accounting error. It cannot:
no ledger in this repository carries a labelled instance of any of the twelve
published types, which is why the VERIFIED count below is zero rather than
unmeasured. `test_no_committed_real_book_carries_a_labelled_error` re-measures
that absence here rather than citing it.

It does not prove that a PARTIAL type is worth building for, that an UNSUPPORTED
type is cheap, or that an UNREACHABLE type is permanently unreachable. UNREACHABLE
means the deciding fact is in nothing this system reads **today**.

It states nothing about how often any type occurs. The published record supports
no such number.

THE MEASURED NUMBERS, AND WHERE EACH COMES FROM
-----------------------------------------------
    12  published error types
        len(accountant.taxonomy.findings.ERROR_TYPES)
     0  VERIFIED - a named test proves a detector catches this type on real data
        no such test exists; coverage.status_counts() gives COVERED 0
     2  PARTIAL - a live detector is aimed at the type and nothing proves it
        coverage.partial_types(), both mapped to vendor_switch
     4  UNREACHABLE - the deciding fact is in nothing this system reads
     6  UNSUPPORTED - no detector exists and the input it needs is obtainable
     4  history-only reachable ceiling
        the same set as coverage.types_by_route(Route.DETECTOR), which the code
        defines as "needs no input the connector does not already return"
     1  detectors on the production path
        pipeline.evaluate and pipeline.run default detector_set to
        detectors.SLICE_4_DETECTORS, which is (vendor_switch,)
"""

from __future__ import annotations

import ast
import functools
import inspect
from dataclasses import dataclass
from pathlib import Path

import pytest

from accountant import pipeline
from accountant.detect import detectors
from accountant.ingest import sources as spend_sources
from accountant.ingest import spend
from accountant.taxonomy import coverage, findings
from accountant.taxonomy.coverage import COVERAGE, UNCOVERED, Route, Status
from accountant.taxonomy.findings import ERROR_TYPE_NAMES
from accountant.taxonomy.sources import SOURCES

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "TAXONOMY.md"
TESTS_DIR = ROOT / "tests"

# The four classifications, and nothing else may be written in that column.
CLASSIFICATIONS: tuple[str, ...] = ("VERIFIED", "PARTIAL", "UNREACHABLE", "UNSUPPORTED")

# What the document writes where no detector is mapped. The coverage table writes
# UNCOVERED for the same fact; the two spellings are reconciled once, here.
NONE_MAPPED = "NONE"

# The measured position on 2026-08-08. Each is asserted below against the code
# rather than trusted, so a number that moves fails rather than going stale.
MEASURED_TYPES = 12
MEASURED_VERIFIED = 0
MEASURED_PARTIAL = 2
MEASURED_UNREACHABLE = 4
MEASURED_UNSUPPORTED = 6
MEASURED_HISTORY_ONLY = 4

MATRIX_COLUMNS = 7
HISTORY_COLUMNS = 3


@functools.cache
def _named_tests() -> frozenset[str]:
    """Every test function in tests/, as 'tests/<file>.py::<name>'.

    A VERIFIED row must name one of these. Read from the source rather than from
    pytest's collection so that a row naming a test that was deleted fails at
    import time, not at the next full run.
    """
    found: set[str] = set()
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                found.add(f"tests/{path.name}::{node.name}")
    return frozenset(found)


@dataclass(frozen=True)
class MatrixRow:
    """One published error type and what is proven about it today.

        type_id         the published type, from findings.py
        detector        the live detector aimed at it, or NONE_MAPPED
        classification  exactly one of the four in CLASSIFICATIONS
        history_only    the deciding fact is inside this company's own voucher
                        stream, with no other data source
        proof_test      'tests/<file>.py::<name>' for a VERIFIED row, and empty
                        for every other row

    A row that claims VERIFIED without naming a test that exists does not load.
    That refusal is the only thing standing between this table and a claim
    somebody wrote down because it felt right.
    """

    type_id: str
    detector: str
    classification: str
    history_only: bool
    proof_test: str = ""

    def __post_init__(self) -> None:
        if self.type_id not in ERROR_TYPE_NAMES:
            raise ValueError(f"{self.type_id!r} is not a published error type")
        if self.classification not in CLASSIFICATIONS:
            raise ValueError(
                f"{self.type_id!r} is classified {self.classification!r}, which is "
                f"not one of the {len(CLASSIFICATIONS)} classifications"
            )
        verified = self.classification == "VERIFIED"
        if verified and not self.proof_test.strip():
            raise ValueError(
                f"{self.type_id!r} claims VERIFIED and names no test that proves it"
            )
        if not verified and self.proof_test.strip():
            raise ValueError(
                f"{self.type_id!r} names a proving test and does not claim VERIFIED"
            )
        if self.proof_test and self.proof_test not in _named_tests():
            raise ValueError(
                f"{self.type_id!r} names {self.proof_test!r}, which is not a test "
                f"this repository contains"
            )


# The twelve, typed out BY HAND. This is a second copy of the classification, so
# a row edited in docs/TAXONOMY.md and nowhere else changes one copy and not the
# other, and the mismatch fails loudly.
THE_MATRIX: tuple[MatrixRow, ...] = (
    MatrixRow(
        type_id="revenue_expenditure_as_capital",
        detector="vendor_switch",
        classification="PARTIAL",
        history_only=True,
    ),
    MatrixRow(
        type_id="capital_expenditure_as_revenue",
        detector="vendor_switch",
        classification="PARTIAL",
        history_only=True,
    ),
    MatrixRow(
        type_id="object_head_incompatible_with_major_head",
        detector=NONE_MAPPED,
        classification="UNSUPPORTED",
        history_only=False,
    ),
    MatrixRow(
        type_id="wrong_expense_head_within_same_section",
        detector=NONE_MAPPED,
        classification="UNREACHABLE",
        history_only=False,
    ),
    MatrixRow(
        type_id="receipt_classified_as_wrong_type",
        detector=NONE_MAPPED,
        classification="UNSUPPORTED",
        history_only=True,
    ),
    MatrixRow(
        type_id="parked_in_suspense_head",
        detector=NONE_MAPPED,
        classification="UNSUPPORTED",
        history_only=True,
    ),
    MatrixRow(
        type_id="expenditure_netted_against_receipt",
        detector=NONE_MAPPED,
        classification="UNREACHABLE",
        history_only=False,
    ),
    MatrixRow(
        type_id="expense_under_wrong_statement_head",
        detector=NONE_MAPPED,
        classification="UNSUPPORTED",
        history_only=False,
    ),
    MatrixRow(
        type_id="balance_under_wrong_balance_sheet_head",
        detector=NONE_MAPPED,
        classification="UNSUPPORTED",
        history_only=False,
    ),
    MatrixRow(
        type_id="related_party_not_identified",
        detector=NONE_MAPPED,
        classification="UNREACHABLE",
        history_only=False,
    ),
    MatrixRow(
        type_id="expenditure_exceeds_sanctioned_provision",
        detector=NONE_MAPPED,
        classification="UNREACHABLE",
        history_only=False,
    ),
    MatrixRow(
        type_id="tax_credit_claimed_where_not_admissible",
        detector=NONE_MAPPED,
        classification="UNSUPPORTED",
        history_only=False,
    ),
)


def _by_type() -> dict[str, MatrixRow]:
    return {row.type_id: row for row in THE_MATRIX}


def _count(classification: str) -> int:
    return len([r for r in THE_MATRIX if r.classification == classification])


# ---------------------------------------------------------------------------
# reading the document
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocRow:
    """One parsed row of the matrix table in docs/TAXONOMY.md."""

    type_id: str
    short_name: str
    citation: str
    detector: str
    classification: str
    why: str
    upgrade: str


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _cells(line: str, columns: int) -> list[str] | None:
    """One pipe-table row as stripped cells, or None if it is not one."""
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    cells = [c.strip() for c in stripped.strip("|").split("|")]
    if len(cells) != columns:
        return None
    if not cells[0] or set(cells[0]) <= {"-", ":"}:
        return None
    return cells


def _doc_matrix() -> tuple[DocRow, ...]:
    """The matrix table, in document order.

    Rows are recognised by their first cell naming a published type, so the
    other pipe tables in the file cannot be mistaken for this one.
    """
    rows: list[DocRow] = []
    for line in _doc().splitlines():
        cells = _cells(line, MATRIX_COLUMNS)
        if cells is None:
            continue
        type_id = cells[0].strip("`")
        if type_id not in ERROR_TYPE_NAMES:
            continue
        rows.append(
            DocRow(
                type_id=type_id,
                short_name=cells[1],
                citation=cells[2],
                detector=cells[3].strip("`"),
                classification=cells[4],
                why=cells[5],
                upgrade=cells[6],
            )
        )
    return tuple(rows)


def _doc_history() -> dict[str, str]:
    """The history-only table: type id -> the answer cell."""
    answers: dict[str, str] = {}
    for line in _doc().splitlines():
        cells = _cells(line, HISTORY_COLUMNS)
        if cells is None:
            continue
        type_id = cells[0].strip("`")
        if type_id not in ERROR_TYPE_NAMES:
            continue
        answers[type_id] = cells[2]
    return answers


def _section(heading: str) -> str:
    """One section of the document, from its heading to the next `## ` one."""
    text = _doc()
    start = text.index(heading)
    end = text.find("\n## ", start)
    return text[start:] if end == -1 else text[start:end]


# ---------------------------------------------------------------------------
# the count of published types
# ---------------------------------------------------------------------------


def test_the_matrix_holds_one_row_for_every_published_error_type() -> None:
    assert len(ERROR_TYPE_NAMES) == MEASURED_TYPES
    assert tuple(row.type_id for row in THE_MATRIX) == ERROR_TYPE_NAMES
    assert len(_by_type()) == MEASURED_TYPES


def test_the_document_names_the_same_twelve_types_in_the_same_order() -> None:
    doc_rows = _doc_matrix()
    assert tuple(r.type_id for r in doc_rows) == ERROR_TYPE_NAMES
    assert len(doc_rows) == MEASURED_TYPES


# ---------------------------------------------------------------------------
# every published type carries a source citation
# ---------------------------------------------------------------------------


def test_every_published_type_carries_a_non_empty_source_citation() -> None:
    """Zero of the twelve are uncited, and the citation is a real document."""
    keys = {s.key for s in SOURCES}
    uncited: list[str] = []
    for name in ERROR_TYPE_NAMES:
        extracted = findings.findings_for(name)
        if not extracted:
            uncited.append(name)
            continue
        for f in extracted:
            assert f.ref.strip(), f"{name} cites a paragraph with no reference"
            assert f.source.key in keys
            assert f.source.url.startswith(("http://", "https://"))
            assert f.source.retrieved.strip()
    assert uncited == []


def test_the_document_cites_the_documents_the_findings_were_read_from() -> None:
    keys = {s.key for s in SOURCES}
    for row in _doc_matrix():
        cited = {key for key in keys if key in row.citation}
        assert cited, f"{row.type_id} cites no source in the document"
        assert cited == set(coverage.rows_by_type()[row.type_id].evidence)


def test_every_document_row_states_a_reason_and_an_upgrade_path() -> None:
    for row in _doc_matrix():
        assert row.short_name.strip(), row.type_id
        assert row.why.strip(), row.type_id
        assert row.upgrade.strip(), row.type_id


# ---------------------------------------------------------------------------
# exactly one classification per type, from the allowed set
# ---------------------------------------------------------------------------


def test_every_type_carries_exactly_one_classification_from_the_allowed_set() -> None:
    seen = [row.classification for row in THE_MATRIX]
    assert len(seen) == MEASURED_TYPES
    for classification in seen:
        assert classification in CLASSIFICATIONS


def test_the_four_classification_counts_add_up_to_the_twelve() -> None:
    counts = {c: _count(c) for c in CLASSIFICATIONS}
    assert counts == {
        "VERIFIED": MEASURED_VERIFIED,
        "PARTIAL": MEASURED_PARTIAL,
        "UNREACHABLE": MEASURED_UNREACHABLE,
        "UNSUPPORTED": MEASURED_UNSUPPORTED,
    }
    assert sum(counts.values()) == MEASURED_TYPES


def test_the_document_and_the_matrix_agree_on_every_classification() -> None:
    hand_typed = _by_type()
    for row in _doc_matrix():
        assert row.classification == hand_typed[row.type_id].classification, row.type_id


def test_a_row_classified_outside_the_four_does_not_load() -> None:
    with pytest.raises(ValueError, match="not one of the"):
        MatrixRow(
            type_id=ERROR_TYPE_NAMES[0],
            detector=NONE_MAPPED,
            classification="PROBABLY FINE",
            history_only=False,
        )
    # And the table itself still holds only the four.
    assert {r.classification for r in THE_MATRIX} <= set(CLASSIFICATIONS)


def test_a_row_for_a_type_nobody_published_does_not_load() -> None:
    with pytest.raises(ValueError, match="not a published error type"):
        MatrixRow(
            type_id="creative_accounting",
            detector=NONE_MAPPED,
            classification="UNSUPPORTED",
            history_only=False,
        )
    assert "creative_accounting" not in _by_type()


# ---------------------------------------------------------------------------
# VERIFIED is zero, and cannot be claimed without a named test
# ---------------------------------------------------------------------------


def test_the_number_of_verified_types_is_the_zero_that_was_measured() -> None:
    verified = [r.type_id for r in THE_MATRIX if r.classification == "VERIFIED"]
    assert verified == []
    assert _count("VERIFIED") == MEASURED_VERIFIED
    # The code says the same thing in its own vocabulary.
    assert dict(coverage.status_counts())[Status.COVERED] == MEASURED_VERIFIED


def test_no_type_is_claimed_verified_without_naming_a_test_that_proves_it() -> None:
    for row in THE_MATRIX:
        if row.classification == "VERIFIED":
            assert row.proof_test in _named_tests(), row.type_id
        else:
            assert row.proof_test == "", row.type_id


def test_a_verified_row_that_names_no_test_does_not_load() -> None:
    with pytest.raises(ValueError, match="names no test that proves it"):
        MatrixRow(
            type_id="revenue_expenditure_as_capital",
            detector="vendor_switch",
            classification="VERIFIED",
            history_only=True,
        )
    assert _by_type()["revenue_expenditure_as_capital"].classification == "PARTIAL"


def test_a_verified_row_that_names_a_test_this_repository_lacks_does_not_load() -> None:
    with pytest.raises(ValueError, match="not a test this repository contains"):
        MatrixRow(
            type_id="revenue_expenditure_as_capital",
            detector="vendor_switch",
            classification="VERIFIED",
            history_only=True,
            proof_test="tests/test_taxonomy_matrix.py::test_that_was_never_written",
        )
    assert (
        "tests/test_taxonomy_matrix.py::test_that_was_never_written"
        not in _named_tests()
    )


def test_a_verified_row_naming_a_real_test_does_load() -> None:
    """The other side of the guard.

    A rule that could only ever refuse would be refusing a constant, not
    checking a claim. This row is constructed and discarded; it is deliberately
    not in THE_MATRIX, because the test it names proves nothing about detecting
    a real error.
    """
    named = (
        "tests/test_taxonomy_matrix.py::"
        "test_a_verified_row_naming_a_real_test_does_load"
    )
    row = MatrixRow(
        type_id="revenue_expenditure_as_capital",
        detector="vendor_switch",
        classification="VERIFIED",
        history_only=True,
        proof_test=named,
    )
    assert row.proof_test in _named_tests()
    assert row not in THE_MATRIX


def test_a_row_that_names_a_proving_test_without_claiming_verified_does_not_load() -> (
    None
):
    named = (
        "tests/test_taxonomy_matrix.py::"
        "test_the_number_of_verified_types_is_the_zero_that_was_measured"
    )
    with pytest.raises(ValueError, match="does not claim VERIFIED"):
        MatrixRow(
            type_id="revenue_expenditure_as_capital",
            detector="vendor_switch",
            classification="PARTIAL",
            history_only=True,
            proof_test=named,
        )
    assert all(r.proof_test == "" for r in THE_MATRIX)


def test_no_committed_real_book_carries_a_labelled_error() -> None:
    """Why VERIFIED is zero, re-measured rather than cited.

    Every real ledger in `accountant/ingest/fixtures/` is published spend with
    no answer key. There is no entry anywhere in this repository labelled as one
    of the twelve published types, so no test could prove a catch even if a
    detector made one.
    """
    labelled = 0
    entries = 0
    for source in spend_sources.ALL_SOURCES:
        book = spend.as_score_book(spend.load_source(source))
        labelled += book.injected_count
        entries += len(book.entries)
    assert entries > 0, "no real entries were loaded, so nothing was measured"
    assert labelled == 0


# ---------------------------------------------------------------------------
# the document and accountant/taxonomy do not disagree
# ---------------------------------------------------------------------------


def test_the_document_and_the_coverage_table_agree_on_every_mapped_detector() -> None:
    table = coverage.rows_by_type()
    for row in _doc_matrix():
        mapped = table[row.type_id].detector
        expected = NONE_MAPPED if mapped == UNCOVERED else mapped
        assert row.detector == expected, row.type_id
        assert _by_type()[row.type_id].detector == expected, row.type_id


def test_partial_is_claimed_for_exactly_the_types_a_live_detector_is_aimed_at() -> None:
    partial = tuple(r.type_id for r in THE_MATRIX if r.classification == "PARTIAL")
    assert partial == coverage.covered_types()
    assert partial == coverage.partial_types()
    assert len(partial) == MEASURED_PARTIAL
    for name in partial:
        assert _by_type()[name].detector in {
            detectors.name_of(d) for d in detectors.ALL_DETECTORS
        }


def test_no_type_without_a_live_detector_is_classified_partial() -> None:
    for row in THE_MATRIX:
        if row.detector == NONE_MAPPED:
            assert row.classification in {"UNREACHABLE", "UNSUPPORTED"}, row.type_id
        else:
            assert row.classification in {"VERIFIED", "PARTIAL"}, row.type_id


def test_every_type_the_matrix_leaves_unbuilt_is_uncovered_in_the_code() -> None:
    unbuilt = {
        r.type_id
        for r in THE_MATRIX
        if r.classification in {"UNREACHABLE", "UNSUPPORTED"}
    }
    assert unbuilt == set(coverage.uncovered_types())
    assert len(unbuilt) == coverage.uncovered_count()


# ---------------------------------------------------------------------------
# the history-only ceiling
# ---------------------------------------------------------------------------


def test_the_history_only_reachable_ceiling_is_the_four_that_was_measured() -> None:
    reachable = tuple(r.type_id for r in THE_MATRIX if r.history_only)
    assert len(reachable) == MEASURED_HISTORY_ONLY
    assert reachable == (
        "revenue_expenditure_as_capital",
        "capital_expenditure_as_revenue",
        "receipt_classified_as_wrong_type",
        "parked_in_suspense_head",
    )


def test_the_history_only_types_are_the_ones_the_code_routes_to_a_detector() -> None:
    """Two independent readings of the same question, and they must agree.

    `Route.DETECTOR` is defined in coverage.py as needing no input the connector
    does not already return. This file reached its four by asking what fact
    decides each type. A disagreement means one of the two is wrong.
    """
    reachable = tuple(r.type_id for r in THE_MATRIX if r.history_only)
    assert reachable == coverage.types_by_route(Route.DETECTOR)
    assert dict(coverage.route_counts())[Route.DETECTOR] == MEASURED_HISTORY_ONLY


def test_the_document_gives_a_history_answer_for_every_one_of_the_twelve() -> None:
    answers = _doc_history()
    assert set(answers) == set(ERROR_TYPE_NAMES)
    hand_typed = _by_type()
    for name, answer in answers.items():
        assert answer.startswith(("YES", "NO")), name
        assert answer.startswith("YES") is hand_typed[name].history_only, name


# ---------------------------------------------------------------------------
# what actually runs, and what the document is not allowed to say
# ---------------------------------------------------------------------------


def test_one_detector_runs_on_the_production_path_and_it_is_vendor_switch() -> None:
    """The production default, read from the pipeline rather than from prose."""
    assert [detectors.name_of(d) for d in detectors.SLICE_4_DETECTORS] == [
        "vendor_switch"
    ]
    for function in (pipeline.evaluate, pipeline.run):
        default: object = inspect.signature(function).parameters["detector_set"].default
        assert default is detectors.SLICE_4_DETECTORS, function.__name__
    # And it is the only detector any published type maps to.
    assert {row.detector for row in COVERAGE if row.covered} == {"vendor_switch"}
    assert set(coverage.detectors_targeting_no_error_type()) == {
        "first_use",
        "magnitude",
        "gst_anomaly",
    }


def test_the_document_states_the_four_headline_counts_it_measured() -> None:
    text = _doc()
    for stated in (
        "| published error types | 12 | 12 | AGREE |",
        "| VERIFIED coverage | 0 | 0 | AGREE |",
        "| history-only reachable ceiling | 4 | 4 | AGREE |",
    ):
        assert stated in text, stated
    assert "at most 2 | AGREE |" in text


def test_the_document_never_states_two_of_twelve_as_current_coverage() -> None:
    """The owner's ruling, enforced rather than remembered.

    "2 of 12 covered" appears elsewhere in the repository as measured truth. It
    may be quoted here only where it is being corrected, so the phrase is
    confined to the section that records the disagreement.
    """
    text = _doc()
    correction = _section("### Where this disagrees with the rest of the repository")
    assert text.count("2 of 12") >= 1, "the disagreement is not recorded at all"
    assert text.count("2 of 12") == correction.count("2 of 12")
    assert "overstates" in correction
    assert "COVERED 0" in correction

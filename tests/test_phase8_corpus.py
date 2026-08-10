"""Phase 8 PR-2: the hundred detector cases, and every count Q5 and Q7 ask for.

    4 detectors x 25 cases = 100 detector cases

Owner answers, `docs/OWNER_DECISIONS.md`:

    Q5 = C   UK government public data where it fits, synthetic elsewhere,
             every case labelled individually
    Q7 = B   4/4 detectors active in test mode, 4/4 with tests, 4/4 with
             provenance, 0 crashes, 0 silently skipped results, N1 <= 10

Every count below is asserted as an exact number, never as "at least" or "most
of", because a count with slack in it is not a count.

WHAT THIS FILE DOES NOT CLAIM
-----------------------------
The corpus was written here, so its N1 is a number about a corpus somebody
chose. It is reported and it is not the exit. The exit number is measured on
real published ledgers in `tests/test_phase8_detectors.py`, where the
denominator is 143 clean entries nobody selected, and it FAILS.
`test_the_corpus_n1_is_not_the_exit_number` holds that distinction open.
"""

from __future__ import annotations

import socket

import pytest

from accountant.detect import detectors
from accountant.ingest import sources, spend
from accountant.score import corpus as C
from accountant.score.book import Book

EXPECTED_TOTAL = 100
EXPECTED_PER_DETECTOR = 25


def _books() -> dict[str, Book]:
    """The committed departments, loaded here and handed to the corpus.

    `accountant/score` may not import the loader - the dependency points one
    way only and a test enforces it - so the wiring lives in this file.
    """
    return {
        s.code: spend.as_score_book(spend.load_source(s)) for s in sources.ALL_SOURCES
    }


def _urls() -> dict[str, str]:
    return {s.code: s.url for s in sources.ALL_SOURCES}


def build() -> tuple[C.Case, ...]:
    return C.corpus(_books(), _urls())


@pytest.fixture(scope="module")
def cases() -> tuple[C.Case, ...]:
    return build()


@pytest.fixture(scope="module")
def report(cases: tuple[C.Case, ...]) -> C.CorpusReport:
    return C.run_corpus(cases)


# ---------------------------------------------------------------------------
# THE COUNTS
# ---------------------------------------------------------------------------


def test_there_are_exactly_four_detectors_and_no_fifth() -> None:
    assert C.DETECTOR_NAMES == (
        "vendor_switch",
        "first_use",
        "magnitude",
        "gst_anomaly",
    )
    assert len(C.DETECTOR_NAMES) == 4


def test_four_detectors_twenty_five_cases_each(cases: tuple[C.Case, ...]) -> None:
    assert len(cases) == EXPECTED_TOTAL
    for name in C.DETECTOR_NAMES:
        assert sum(1 for c in cases if c.detector == name) == EXPECTED_PER_DETECTOR


def test_every_case_id_is_unique(cases: tuple[C.Case, ...]) -> None:
    ids = [c.case_id for c in cases]
    assert len(set(ids)) == len(ids)


def test_all_one_hundred_are_classified_and_none_is_skipped(
    report: C.CorpusReport,
) -> None:
    assert report.total == EXPECTED_TOTAL
    assert report.classified == EXPECTED_TOTAL
    assert report.skipped == 0


def test_no_detector_crashed_on_any_case(report: C.CorpusReport) -> None:
    assert report.crashed == 0
    assert [r.case.case_id for r in report.results if r.crashed] == []


def test_no_case_produced_an_unsafe_classification(report: C.CorpusReport) -> None:
    """A detector firing means "surprising", never "wrong". Counted per case.

    A case is UNSAFE when a flag has no checkable reason, when a flag refused
    the entry instead of asking about it, when a question leaked a ledger name,
    or when a flag was raised against the wrong voucher.
    """
    offenders = {r.case.case_id: r.unsafe for r in report.results if not r.is_safe}
    assert offenders == {}
    assert report.unsafe == 0


def test_every_case_carries_a_label_an_expected_output_and_provenance(
    report: C.CorpusReport,
) -> None:
    assert report.labelled == EXPECTED_TOTAL
    assert report.with_expected_output == EXPECTED_TOTAL
    assert report.with_provenance == EXPECTED_TOTAL


def test_every_case_matched_its_expected_output(report: C.CorpusReport) -> None:
    assert report.mismatched == ()
    assert report.matched == EXPECTED_TOTAL


def test_four_of_four_detectors_are_active_in_test_mode(
    report: C.CorpusReport,
) -> None:
    """Asked, answered every case, and fired on at least one of its own.

    A detector that never fires anywhere in its twenty-five cases was not
    exercised, whatever else the counts say, so "active" is defined to exclude
    it rather than to include it politely.
    """
    assert report.detectors_active == 4
    assert report.detectors_with_cases == 4
    assert [d.detector for d in report.per_detector if not d.active_in_test_mode] == []


def test_four_of_four_detectors_have_provenance(report: C.CorpusReport) -> None:
    assert report.detectors_with_provenance == 4


def test_four_of_four_detectors_have_tests(cases: tuple[C.Case, ...]) -> None:
    """Every detector in `ALL_DETECTORS` is under test in this corpus.

    `first_use` is withdrawn from the shipped set and is tested here all the
    same. Withdrawn is off, not unexamined.
    """
    covered = {c.detector for c in cases}
    assert covered == {detectors.name_of(d) for d in detectors.ALL_DETECTORS}
    assert len(covered) == 4


@pytest.mark.parametrize("name", C.DETECTOR_NAMES)
def test_each_detector_both_fires_and_stays_silent_across_its_cases(
    report: C.CorpusReport, name: str
) -> None:
    """Twenty-five cases that all went one way would test one branch."""
    (row,) = [d for d in report.per_detector if d.detector == name]
    assert row.cases == EXPECTED_PER_DETECTOR
    assert row.fired > 0
    assert row.fired < row.cases


def test_the_exact_firing_counts_are_pinned(report: C.CorpusReport) -> None:
    """Which detector fired on how many of its own cases. Pinned, not summarised."""
    fired = {d.detector: d.fired for d in report.per_detector}
    assert fired == {
        "vendor_switch": 5,
        "first_use": 14,
        "magnitude": 7,
        "gst_anomaly": 13,
    }


# ---------------------------------------------------------------------------
# THE LABELS
# ---------------------------------------------------------------------------


def test_every_label_is_one_of_the_four_the_owner_named(
    cases: tuple[C.Case, ...],
) -> None:
    allowed = {
        "SYNTHETIC_EVIDENCE",
        "THIRD_PARTY_PUBLIC_EVIDENCE",
        "REAL_ANONYMISED_EVIDENCE",
        "HELD_OUT_CUSTOMER_LIKE_EVIDENCE",
    }
    assert {e.value for e in C.EvidenceClass} == allowed
    for case in cases:
        assert case.evidence_class.value in allowed


def test_the_split_between_public_and_synthetic_is_stated_exactly(
    report: C.CorpusReport,
) -> None:
    assert report.by_label == (
        ("SYNTHETIC_EVIDENCE", 55),
        ("THIRD_PARTY_PUBLIC_EVIDENCE", 45),
    )


def test_two_labels_are_unused_and_saying_so_is_the_point() -> None:
    """Nobody has supplied a real anonymised or held-out customer book.

    `H-02` is the open, optional request. Until it is answered these two labels
    have nothing behind them, and a corpus that quietly used only two labels
    without naming the missing two would read as though it had customer
    evidence in it.
    """
    assert C.unused_labels() == (
        C.EvidenceClass.REAL_ANONYMISED,
        C.EvidenceClass.HELD_OUT_CUSTOMER_LIKE,
    )


def test_gst_anomaly_is_entirely_synthetic_and_the_reason_is_in_the_data(
    cases: tuple[C.Case, ...],
) -> None:
    """Q5 = C applied literally: synthetic where the public data does not fit.

    No UK spend file publishes a tax column, so the loader records `gst_paise`
    as absent on every row. There is nothing for this detector to read in any
    of them, which is checked here against the files rather than asserted.
    """
    mine = [c for c in cases if c.detector == "gst_anomaly"]
    assert len(mine) == EXPECTED_PER_DETECTOR
    assert {c.evidence_class for c in mine} == {C.EvidenceClass.SYNTHETIC}
    assert C.REAL_CASES["gst_anomaly"] == ()

    for source in sources.ALL_SOURCES:
        for voucher in spend.vouchers(spend.load_source(source)):
            assert voucher.gst_paise is None


def test_the_other_three_detectors_are_measured_on_real_published_rows(
    cases: tuple[C.Case, ...],
) -> None:
    for name in ("vendor_switch", "first_use", "magnitude"):
        mine = [c for c in cases if c.detector == name]
        public = [
            c for c in mine if c.evidence_class is C.EvidenceClass.THIRD_PARTY_PUBLIC
        ]
        assert len(public) == C.REAL_CASES_PER_DETECTOR == 15
        assert len(mine) - len(public) == 10


def test_every_public_case_names_the_file_the_licence_and_the_row(
    cases: tuple[C.Case, ...],
) -> None:
    """Provenance a person can follow back to a published document."""
    for case in cases:
        if case.evidence_class is not C.EvidenceClass.THIRD_PARTY_PUBLIC:
            continue
        assert "Open Government Licence v3.0" in case.provenance
        assert case.proposed.id in case.provenance
        assert "http" in case.provenance


def test_every_synthetic_case_says_it_is_not_evidence_about_a_real_bill(
    cases: tuple[C.Case, ...],
) -> None:
    for case in cases:
        if case.evidence_class is not C.EvidenceClass.SYNTHETIC:
            continue
        assert "never evidence about accuracy on a real bill" in case.provenance


def test_the_two_kinds_of_expected_output_are_kept_apart(
    cases: tuple[C.Case, ...],
) -> None:
    """A constructed expectation and a pinned one are different claims.

    Synthetic inputs were built to have a known property, so their expectation
    is independent of any run. Real rows carry an expectation read off a
    measured run and frozen: it catches a change, and it is not an independent
    judgement about the payment.
    """
    for case in cases:
        if case.evidence_class is C.EvidenceClass.SYNTHETIC:
            assert case.oracle is C.Oracle.CONSTRUCTED
        else:
            assert case.oracle is C.Oracle.PINNED

    constructed = sum(1 for c in cases if c.oracle is C.Oracle.CONSTRUCTED)
    assert (constructed, len(cases) - constructed) == (55, 45)


# ---------------------------------------------------------------------------
# N1 ON THIS CORPUS, AND WHY IT IS NOT THE EXIT
# ---------------------------------------------------------------------------


def test_the_corpus_n1_is_reported(report: C.CorpusReport) -> None:
    """Clean cases are the ones whose correct answer is silence."""
    assert report.clean_cases == 61
    assert report.false_alarms == 0


def test_the_corpus_n1_is_not_the_exit_number() -> None:
    """The distinction this corpus must never be allowed to blur.

    Sixty-one of these hundred cases were written to be silent, and the
    detectors are silent on all sixty-one, so N1 here is 0.00 per 100. That is
    a fact about a corpus somebody chose and it proves nothing about a real
    book.

    The exit number is N1 with all four detectors on the seven published
    ledgers, where the denominator is 143 clean entries nobody selected. It is
    34.27 per 100 and it FAILS the target of 10.
    `tests/test_phase8_detectors.py::test_n1_with_all_four_detectors_on_real_books_is_a_measured_failure`
    holds it.
    """
    from accountant.score import calibration as cal

    books = tuple(
        spend.as_score_book(spend.load_source(s)) for s in sources.ALL_SOURCES
    )
    on_real_books = cal.measure(books, detectors.ALL_DETECTORS)

    assert on_real_books.per_100_hundredths == 3427
    assert not on_real_books.within(10)


# ---------------------------------------------------------------------------
# THE CORPUS CANNOT BE BUILT WRONG
# ---------------------------------------------------------------------------


def test_a_case_without_provenance_is_refused(cases: tuple[C.Case, ...]) -> None:
    from dataclasses import replace

    with pytest.raises(ValueError, match="carries no provenance"):
        replace(cases[0], provenance="   ")


def test_a_case_naming_a_fifth_detector_is_refused(cases: tuple[C.Case, ...]) -> None:
    from dataclasses import replace

    with pytest.raises(ValueError, match="not one of"):
        replace(cases[0], detector="from_the_future")


def test_a_case_that_does_not_say_what_it_tests_is_refused(
    cases: tuple[C.Case, ...],
) -> None:
    from dataclasses import replace

    with pytest.raises(ValueError, match="does not say what it tests"):
        replace(cases[0], tests="")


def test_a_case_with_no_id_is_refused(cases: tuple[C.Case, ...]) -> None:
    from dataclasses import replace

    with pytest.raises(ValueError, match="no id"):
        replace(cases[0], case_id=" ")


def test_a_crashing_detector_is_recorded_as_a_result_not_an_abort(
    monkeypatch: pytest.MonkeyPatch, cases: tuple[C.Case, ...]
) -> None:
    """A crash must be counted, which means the runner must survive one."""

    def explode(*_a: object, **_k: object) -> list[object]:
        raise RuntimeError("boom")

    monkeypatch.setattr(detectors, "gst_anomaly", explode)
    monkeypatch.setattr(
        detectors,
        "ALL_DETECTORS",
        (detectors.vendor_switch, detectors.first_use, detectors.magnitude, explode),
    )
    explode.__name__ = "gst_anomaly"

    one = next(c for c in cases if c.detector == "gst_anomaly")
    result = C.run_case(one)
    assert result.crashed
    assert not result.classified
    assert "boom" in result.error


# ---------------------------------------------------------------------------
# THE RUN IS DETERMINISTIC AND OFFLINE
# ---------------------------------------------------------------------------


def test_the_same_corpus_run_twice_gives_identical_counts() -> None:
    first = C.run_corpus(build())
    second = C.run_corpus(build())
    for attribute in (
        "total",
        "classified",
        "skipped",
        "crashed",
        "unsafe",
        "matched",
        "labelled",
        "with_provenance",
        "clean_cases",
        "false_alarms",
    ):
        assert getattr(first, attribute) == getattr(second, attribute), attribute
    assert first.by_label == second.by_label
    assert first.per_detector == second.per_detector


def test_running_the_corpus_makes_no_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No model call, and no call of any other kind. Verified, not promised."""

    def explode(*_a: object, **_k: object) -> object:
        raise AssertionError("the corpus attempted a network call")

    monkeypatch.setattr(socket, "socket", explode)
    monkeypatch.setattr(socket, "create_connection", explode)
    report = C.run_corpus(build())
    assert report.classified == EXPECTED_TOTAL

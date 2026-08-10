"""The Phase 8 detector corpus: four detectors, twenty-five cases each.

    4 detectors x 25 cases = 100 detector cases

Owner answer Q5 = C, recorded in `docs/OWNER_DECISIONS.md`: real UK government
public data where it fits, synthetic elsewhere, **every case labelled
individually**. This module is that corpus, and the label is a field on the
case rather than a sentence in a report, so an unlabelled case cannot be built.

WHAT EACH LABEL MEANS HERE, AND WHICH ONES ARE HONESTLY AVAILABLE
-----------------------------------------------------------------
    THIRD_PARTY_PUBLIC_EVIDENCE   a real row from a committed UK
                                  central-government spend file, published
                                  under the Open Government Licence v3.0 by
                                  the department that made the payment.
    SYNTHETIC_EVIDENCE            a voucher written here, to put a detector on
                                  a boundary the published files do not
                                  contain.
    REAL_ANONYMISED_EVIDENCE      NOT USED. Nobody has supplied a real
                                  anonymised customer book. `H-02` is the open
                                  request and it is optional.
    HELD_OUT_CUSTOMER_LIKE_EVIDENCE
                                  NOT USED. Same reason.

Two of the four labels are therefore absent from this corpus, and that absence
is a fact about what exists rather than a gap in the labelling.
`unused_labels()` returns them and a test asserts the list, so nobody can read
the corpus as though it had customer evidence in it.

**`gst_anomaly` is entirely synthetic, and it has to be.** No UK spend file
publishes a tax column at all, so every loaded row carries `gst_paise` of
`None`, and `artifacts/detector_gate.md` records that `gst_anomaly` has never
once fired on real data. Twenty-five synthetic cases for it is Q5 = C applied
literally: synthetic where the public data does not fit.

WHY THE REAL BOOKS ARE PASSED IN RATHER THAN LOADED HERE
---------------------------------------------------------
`accountant/score` declares a narrow input contract in `score/book.py` and
nothing in the package reaches out to the loader that fills it in. The
dependency points one way only, and a test enforces it by reading every source
file in this package. So `real_cases` takes the loaded books and their source
URLs as arguments, and the caller does the loading.

WHAT AN "EXPECTED OUTPUT" IS, AND WHAT IT IS NOT
------------------------------------------------
`expect_fires` is a claim about **what the detector should do**, never about
whether the entry is wrong. Nobody injected an error into a published
government ledger, so nothing here is an answer key about correctness.

    synthetic cases   the expectation is independent. The input was built to
                      have a known property - one paise over the margin, a
                      history one entry short of a range - and the expectation
                      follows from the property, not from a run.
    real cases        the expectation is a PIN. It was read off a measured run
                      once, written down here as a literal, and it now fails if
                      the detector's answer on that row ever changes. It is not
                      an independent judgement about the payment and it must
                      never be quoted as one.

Saying which of the two each case is, rather than presenting a hundred
expectations as one kind of thing, is the point of splitting them.

WHAT "UNSAFE" MEANS, MEASURED PER CASE
---------------------------------------
A detector firing means "this is surprising", never "this is wrong". A case is
counted UNSAFE when the run breaks one of the invariants that hold that line:

    1  a flag whose reason is empty, or cites no number a person could check
    2  a flag that became an UNANSWERABLE problem - a detector refusing an
       entry instead of asking about it
    3  a question that leaks a ledger account name from the company's chart
    4  a flag whose `voucher_id` is not the entry it was raised on

None of the four is a matter of degree, so `unsafe` is a count and not a score.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from accountant import checks, problems
from accountant.detect import detectors
from accountant.memory.index import MemoryIndex
from accountant.schema import Flag, Voucher
from accountant.score.book import Book

# The four, in `ALL_DETECTORS` order. Not a fifth, and not a subset.
DETECTOR_NAMES: tuple[str, ...] = tuple(
    detectors.name_of(d) for d in detectors.ALL_DETECTORS
)

# Owner answer Q5: twenty-five cases each.
CASES_PER_DETECTOR = 25
REAL_CASES_PER_DETECTOR = 15


class EvidenceClass(StrEnum):
    """The four labels from Q5. One per case, and no case without one."""

    SYNTHETIC = "SYNTHETIC_EVIDENCE"
    THIRD_PARTY_PUBLIC = "THIRD_PARTY_PUBLIC_EVIDENCE"
    REAL_ANONYMISED = "REAL_ANONYMISED_EVIDENCE"
    HELD_OUT_CUSTOMER_LIKE = "HELD_OUT_CUSTOMER_LIKE_EVIDENCE"


def unused_labels() -> tuple[EvidenceClass, ...]:
    """The labels this corpus does not use, and cannot honestly use yet."""
    return (EvidenceClass.REAL_ANONYMISED, EvidenceClass.HELD_OUT_CUSTOMER_LIKE)


class Oracle(StrEnum):
    """Where a case's expected output came from. Two kinds, never merged."""

    CONSTRUCTED = "constructed"
    PINNED = "pinned"


@dataclass(frozen=True)
class Case:
    """One detector case: an input, one label, one provenance, one expectation."""

    case_id: str
    detector: str
    evidence_class: EvidenceClass
    provenance: str
    oracle: Oracle
    expect_fires: bool
    tests: str
    proposed: Voucher
    history: tuple[Voucher, ...]
    accounts: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("a case with no id cannot be reported")
        if self.detector not in DETECTOR_NAMES:
            raise ValueError(
                f"{self.case_id!r} names {self.detector!r}, which is not one of "
                f"the four detectors: {', '.join(DETECTOR_NAMES)}"
            )
        if not self.provenance.strip():
            raise ValueError(
                f"{self.case_id!r} carries no provenance; a case nobody can "
                f"trace back to a source is not evidence of anything"
            )
        if not self.tests.strip():
            raise ValueError(f"{self.case_id!r} does not say what it tests")


@dataclass(frozen=True)
class CaseResult:
    """What running one case produced. Nothing here is allowed to be absent."""

    case: Case
    fired: bool
    crashed: bool
    error: str
    unsafe: tuple[str, ...] = ()
    flags: tuple[Flag, ...] = ()

    @property
    def classified(self) -> bool:
        """The detector returned a definite answer. A crash is not an answer."""
        return not self.crashed

    @property
    def matched(self) -> bool:
        return self.classified and self.fired == self.case.expect_fires

    @property
    def is_safe(self) -> bool:
        return not self.unsafe


def _detector_by_name(name: str) -> detectors.Detector:
    for d in detectors.ALL_DETECTORS:
        if detectors.name_of(d) == name:
            return d
    raise ValueError(f"no detector called {name!r}")


def _unsafe_reasons(case: Case, flags: Sequence[Flag]) -> tuple[str, ...]:
    """Every invariant this case's output broke. All of them, not the first."""
    broken: list[str] = []
    for f in flags:
        if not f.reason.strip():
            broken.append(f"{f.detector} raised a flag with no reason")
        elif not any(ch.isdigit() for ch in f.reason):
            broken.append(f"{f.detector} cites no number: {f.reason}")
        if f.voucher_id != case.proposed.id:
            broken.append(
                f"{f.detector} raised a flag on {f.voucher_id!r}, "
                f"not on {case.proposed.id!r}"
            )

    index = MemoryIndex.from_vouchers(case.history)
    found = problems.find(
        case.proposed,
        checks.run(case.proposed, case.accounts),
        index.lookup(case.proposed.party),
        list(flags),
        case.accounts,
        case.history,
        index,
    )
    fired_names = {f.detector for f in flags}
    for p in found:
        if p.id not in fired_names:
            continue
        if not p.answerable:
            broken.append(f"{p.id} refused the entry instead of asking about it")
            continue
        if p.question is None:  # pragma: no cover - Problem forbids it
            broken.append(f"{p.id} is answerable but carries no question")
            continue
        leaked = p.question.mentions_any(case.accounts)
        if leaked:
            broken.append(f"{p.id} leaked a ledger name: {', '.join(leaked)}")
    return tuple(broken)


def run_case(case: Case) -> CaseResult:
    """Run one case. A crash is recorded, never allowed to end the run.

    The detector is called on its own rather than through `detectors.run`, so
    the answer is that detector's and cannot be another one's suppressed
    duplicate.
    """
    detector = _detector_by_name(case.detector)
    index = MemoryIndex.from_vouchers(case.history)
    try:
        flags = tuple(detector(case.proposed, case.history, index))
    except Exception as exc:
        return CaseResult(case=case, fired=False, crashed=True, error=repr(exc))
    return CaseResult(
        case=case,
        fired=bool(flags),
        crashed=False,
        error="",
        unsafe=_unsafe_reasons(case, flags),
        flags=flags,
    )


@dataclass(frozen=True)
class DetectorCounts:
    """One detector's own row in the corpus report."""

    detector: str
    cases: int
    classified: int
    matched: int
    fired: int
    crashed: int
    unsafe: int
    labelled: int
    with_provenance: int

    @property
    def active_in_test_mode(self) -> bool:
        """It was asked, it answered every case, and it fired on at least one.

        A detector that never fires anywhere in its own twenty-five cases was
        not exercised, whatever the pass counts say.
        """
        return self.cases > 0 and self.classified == self.cases and self.fired > 0


@dataclass(frozen=True)
class CorpusReport:
    """Every count Q5 and Q7 ask for, and the results they were counted from."""

    results: tuple[CaseResult, ...]
    per_detector: tuple[DetectorCounts, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def classified(self) -> int:
        return sum(1 for r in self.results if r.classified)

    @property
    def skipped(self) -> int:
        """Cases that produced no answer at all. Never allowed to be non-zero."""
        return self.total - self.classified

    @property
    def crashed(self) -> int:
        return sum(1 for r in self.results if r.crashed)

    @property
    def unsafe(self) -> int:
        return sum(1 for r in self.results if not r.is_safe)

    @property
    def matched(self) -> int:
        return sum(1 for r in self.results if r.matched)

    @property
    def mismatched(self) -> tuple[str, ...]:
        return tuple(r.case.case_id for r in self.results if not r.matched)

    @property
    def labelled(self) -> int:
        return sum(1 for r in self.results if r.case.evidence_class in EvidenceClass)

    @property
    def with_provenance(self) -> int:
        return sum(1 for r in self.results if r.case.provenance.strip())

    @property
    def with_expected_output(self) -> int:
        """Cases that state, in advance, what the detector should do.

        `Case.expect_fires` is a required field with no default, so a case
        cannot be built without one and this is the whole hundred. It is
        counted rather than asserted in prose because Q5 asks for the count.
        """
        return sum(1 for r in self.results if r.case.expect_fires in (True, False))

    @property
    def by_label(self) -> tuple[tuple[str, int], ...]:
        counts: dict[str, int] = {}
        for r in self.results:
            key = r.case.evidence_class.value
            counts[key] = counts.get(key, 0) + 1
        return tuple((label, counts[label]) for label in sorted(counts))

    @property
    def detectors_active(self) -> int:
        return sum(1 for d in self.per_detector if d.active_in_test_mode)

    @property
    def detectors_with_cases(self) -> int:
        return sum(1 for d in self.per_detector if d.cases > 0)

    @property
    def detectors_with_provenance(self) -> int:
        """A detector all of whose cases trace back to a stated source."""
        return sum(1 for d in self.per_detector if d.with_provenance == d.cases > 0)

    @property
    def clean_cases(self) -> int:
        """Cases where the correct answer is silence. N1's denominator here."""
        return sum(1 for r in self.results if not r.case.expect_fires)

    @property
    def false_alarms(self) -> int:
        """Clean cases the detector fired on anyway."""
        return sum(1 for r in self.results if not r.case.expect_fires and r.fired)


def _counts_for(detector: str, results: Sequence[CaseResult]) -> DetectorCounts:
    mine = [r for r in results if r.case.detector == detector]
    return DetectorCounts(
        detector=detector,
        cases=len(mine),
        classified=sum(1 for r in mine if r.classified),
        matched=sum(1 for r in mine if r.matched),
        fired=sum(1 for r in mine if r.fired),
        crashed=sum(1 for r in mine if r.crashed),
        unsafe=sum(1 for r in mine if not r.is_safe),
        labelled=sum(1 for r in mine if r.case.evidence_class in EvidenceClass),
        with_provenance=sum(1 for r in mine if r.case.provenance.strip()),
    )


def run_corpus(cases: Sequence[Case]) -> CorpusReport:
    """Run every case. Same cases in, identical counts out, every time."""
    results = tuple(run_case(c) for c in cases)
    return CorpusReport(
        results=results,
        per_detector=tuple(_counts_for(n, results) for n in DETECTOR_NAMES),
    )


# ---------------------------------------------------------------------------
# The real cases: rows from published UK central-government spend files
# ---------------------------------------------------------------------------
#
# Selected once by a stated rule - every entry the detector fires on, in file
# order, capped at eight, then the quiet entries taken round-robin across the
# departments in `sources.ALL_SOURCES` order until fifteen - and then FROZEN
# here as literals. The rule is in the commit; the table is the pin.
#
# `magnitude`'s list departs from the round-robin in one deliberate place, and
# it is the whole reason this corpus exists: DHSC-00027, DHSC-00028 and
# DHSC-00029 are three of the six false alarms the root-cause fix removed. They
# are carried as cases that must NOT fire, so the fix cannot silently come
# undone.

REAL_CASES: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "vendor_switch": (
        ("DHSC", "DHSC-00039", True),
        ("DWP", "DWP-00037", True),
        ("MHCLG", "MHCLG-00029", False),
        ("DHSC", "DHSC-00021", False),
        ("DFT", "DFT-00024", False),
        ("DWP", "DWP-00028", False),
        ("DEFRA", "DEFRA-00020", False),
        ("HMT", "HMT-00024", False),
        ("MHCLG", "MHCLG-00030", False),
        ("DHSC", "DHSC-00022", False),
        ("DFT", "DFT-00025", False),
        ("DWP", "DWP-00029", False),
        ("DEFRA", "DEFRA-00021", False),
        ("HMT", "HMT-00025", False),
        ("MHCLG", "MHCLG-00031", False),
    ),
    "first_use": (
        ("MHCLG", "MHCLG-00030", True),
        ("DHSC", "DHSC-00030", True),
        ("DHSC", "DHSC-00031", True),
        ("DHSC", "DHSC-00038", True),
        ("DHSC", "DHSC-00039", True),
        ("DHSC", "DHSC-00041", True),
        ("DFT", "DFT-00028", True),
        ("DFT", "DFT-00033", True),
        ("MHCLG", "MHCLG-00029", False),
        ("DHSC", "DHSC-00021", False),
        ("DFT", "DFT-00024", False),
        ("DWP", "DWP-00028", False),
        ("DEFRA", "DEFRA-00020", False),
        ("HMT", "HMT-00024", False),
        ("MHCLG", "MHCLG-00031", False),
    ),
    "magnitude": (
        ("DHSC", "DHSC-00035", True),
        ("DHSC", "DHSC-00036", True),
        ("DHSC", "DHSC-00037", True),
        ("DEFRA", "DEFRA-00035", True),
        # The three the root-cause fix silenced. Same account, same detector.
        ("DHSC", "DHSC-00027", False),
        ("DHSC", "DHSC-00028", False),
        ("DHSC", "DHSC-00029", False),
        # A fourth entry on the same account, dated a week later, whose prior
        # history genuinely precedes it and which was always quiet.
        ("DHSC", "DHSC-00032", False),
        ("MHCLG", "MHCLG-00029", False),
        ("DHSC", "DHSC-00021", False),
        ("DFT", "DFT-00024", False),
        ("DWP", "DWP-00028", False),
        ("DEFRA", "DEFRA-00020", False),
        ("HMT", "HMT-00024", False),
        ("MHCLG", "MHCLG-00030", False),
    ),
    # No real case. See the module docstring: the published files carry no tax
    # column, so there is nothing for this detector to read in any of them.
    "gst_anomaly": (),
}


def real_cases(
    books: Mapping[str, Book], source_urls: Mapping[str, str]
) -> tuple[Case, ...]:
    """Every case drawn from a published file, in `REAL_CASES` order.

    `books` is keyed by department code and `source_urls` gives the published
    URL each one came from. Both are supplied by the caller; see the module
    docstring for why this package does not fetch them itself.
    """
    built: list[Case] = []
    for detector, rows in REAL_CASES.items():
        for code, voucher_id, expect in rows:
            book = books[code]
            (proposed,) = [v for v in book.entries if v.id == voucher_id]
            built.append(
                Case(
                    case_id=f"{detector}/real/{voucher_id}",
                    detector=detector,
                    evidence_class=EvidenceClass.THIRD_PARTY_PUBLIC,
                    provenance=(
                        f"{code} November 2025 published spend, row {voucher_id}, "
                        f"{source_urls[code]}, Open Government Licence v3.0; "
                        f"history is the same department's earlier rows"
                    ),
                    oracle=Oracle.PINNED,
                    expect_fires=expect,
                    tests=(
                        f"{detector} on a real published row that it "
                        f"{'flags' if expect else 'leaves alone'}"
                    ),
                    proposed=proposed,
                    history=tuple(book.history),
                    accounts=tuple(book.accounts),
                )
            )
    return tuple(built)


# ---------------------------------------------------------------------------
# The synthetic cases: boundaries the published files do not contain
# ---------------------------------------------------------------------------

SYNTHETIC_ACCOUNTS: tuple[str, ...] = (
    "Purchases",
    "Rent",
    "Repairs & Maintenance",
    "Freight & Transport",
    "Professional Fees",
    "Cash",
    "Bank",
)

_WHEN = datetime.date(2026, 3, 1)
_BEFORE = datetime.date(2026, 2, 1)
_LONG_BEFORE = datetime.date(2026, 1, 1)

SYNTHETIC_PROVENANCE = (
    "written in accountant/score/corpus.py; no customer data and no published "
    "file is involved. It tests mechanics, boundaries and adversarial "
    "behaviour, and it is never evidence about accuracy on a real bill."
)


def _v(
    vid: str,
    *,
    account: str = "Purchases",
    amount: int = 400_000,
    gst: int | None = None,
    party: str = "Sharma Traders",
    when: datetime.date = _WHEN,
) -> Voucher:
    return Voucher(
        id=vid,
        date=when,
        party=party,
        narration="corpus case",
        debit_account=account,
        credit_account="Cash",
        amount_paise=amount,
        gst_paise=gst,
    )


def _past(
    n: int,
    *,
    account: str = "Purchases",
    amount: int = 400_000,
    gst: int | None = None,
    party: str = "Sharma Traders",
    when: datetime.date = _BEFORE,
) -> tuple[Voucher, ...]:
    return tuple(
        _v(f"h{i}", account=account, amount=amount, gst=gst, party=party, when=when)
        for i in range(n)
    )


def _synthetic(
    detector: str,
    name: str,
    tests: str,
    expect: bool,
    proposed: Voucher,
    history: Sequence[Voucher],
) -> Case:
    return Case(
        case_id=f"{detector}/synthetic/{name}",
        detector=detector,
        evidence_class=EvidenceClass.SYNTHETIC,
        provenance=SYNTHETIC_PROVENANCE,
        oracle=Oracle.CONSTRUCTED,
        expect_fires=expect,
        tests=tests,
        proposed=proposed,
        history=tuple(history),
        accounts=SYNTHETIC_ACCOUNTS,
    )


_Make = Callable[[str, str, bool, Voucher, Sequence[Voucher]], Case]


def _maker(detector: str) -> _Make:
    """A typed `_synthetic` with the detector name already bound."""

    def make(
        name: str,
        tests: str,
        expect: bool,
        proposed: Voucher,
        history: Sequence[Voucher],
    ) -> Case:
        return _synthetic(detector, name, tests, expect, proposed, history)

    return make


_MARGIN = 400_000 * detectors.MAGNITUDE_OVER_PERCENT // detectors.PERCENT


def _vendor_switch_synthetic() -> tuple[Case, ...]:
    make = _maker("vendor_switch")
    return (
        make(
            "one-prior-posting-is-not-a-practice",
            "one prior posting is a coincidence, so it says nothing",
            False,
            _v("d", account="Rent"),
            _past(1),
        ),
        make(
            "two-prior-postings-are-a-practice",
            "the calibrated minimum, exactly reached",
            True,
            _v("d", account="Rent"),
            _past(2),
        ),
        make(
            "the-account-the-party-always-uses",
            "no switch, so nothing to say",
            False,
            _v("d", account="Purchases"),
            _past(6),
        ),
        make(
            "a-party-never-seen-before",
            "an unseen party is a question elsewhere, not a switch here",
            False,
            _v("d", account="Rent", party="Gupta Hardware"),
            _past(6),
        ),
        make(
            "a-party-split-across-two-accounts",
            "a CONFLICTED history is not one practice, so no switch is claimed",
            False,
            _v("d", account="Freight & Transport"),
            _past(3) + _past(3, account="Rent"),
        ),
        make(
            "a-party-whose-legal-form-differs",
            "D-05: the legal form decides identity, so this is another party",
            False,
            _v("d", account="Rent", party="Sharma Traders Pvt Ltd"),
            _past(6),
        ),
        make(
            "an-empty-history",
            "nothing observed, so nothing claimed",
            False,
            _v("d", account="Rent"),
            (),
        ),
        make(
            "case-and-spacing-noise-in-the-party-name",
            "spelling noise must not break a practice this party really has",
            True,
            _v("d", account="Rent", party="  SHARMA   TRADERS "),
            _past(4),
        ),
        make(
            "a-switch-back-to-a-less-used-account",
            "the practice is the most-used account, and this leaves it",
            True,
            _v("d", account="Professional Fees"),
            _past(5),
        ),
        make(
            "the-same-account-under-a-longer-history",
            "more history cannot turn no-switch into a switch",
            False,
            _v("d", account="Purchases"),
            _past(40),
        ),
    )


def _first_use_synthetic() -> tuple[Case, ...]:
    make = _maker("first_use")
    return (
        make(
            "an-account-never-posted-to",
            "the plain case the detector exists for",
            True,
            _v("d", account="Rent"),
            _past(3),
        ),
        make(
            "an-account-already-in-use",
            "seen before, so silent",
            False,
            _v("d", account="Purchases"),
            _past(3),
        ),
        make(
            "an-account-used-once",
            "once is still used; this detector counts existence, not practice",
            False,
            _v("d", account="Purchases"),
            _past(1),
        ),
        make(
            "an-account-used-by-a-different-party",
            "the account is the subject, not the party",
            False,
            _v("d", account="Rent", party="Gupta Hardware"),
            _past(3, account="Rent"),
        ),
        make(
            "an-empty-history-flags-everything",
            "with no history every account is new - which is exactly why this "
            "detector is withdrawn from the shipped set",
            True,
            _v("d", account="Purchases"),
            (),
        ),
        make(
            "a-long-history-that-still-misses-the-account",
            "length is not coverage",
            True,
            _v("d", account="Professional Fees"),
            _past(40),
        ),
        make(
            "an-account-that-appears-only-on-the-credit-side",
            "the memory index records debit accounts only, so `Cash` - which "
            "every entry in this history was funded from - still counts as "
            "never used. A real limitation, held as a case rather than hidden",
            True,
            _v("d", account="Cash"),
            _past(3),
        ),
        make(
            "an-account-differing-only-in-case",
            "no folding: a ledger name is compared exactly, so this is new",
            True,
            _v("d", account="purchases"),
            _past(3),
        ),
        make(
            "two-accounts-in-history-and-a-third-proposed",
            "still new",
            True,
            _v("d", account="Freight & Transport"),
            _past(2) + _past(2, account="Rent"),
        ),
        make(
            "the-second-of-two-accounts-in-history",
            "seen, so silent",
            False,
            _v("d", account="Rent"),
            _past(2) + _past(2, account="Rent"),
        ),
    )


def _magnitude_synthetic() -> tuple[Case, ...]:
    make = _maker("magnitude")
    return (
        make(
            "exactly-at-the-calibrated-margin",
            "at the margin is not far outside it, so silent",
            False,
            _v("d", amount=_MARGIN),
            _past(3),
        ),
        make(
            "one-paise-above-the-margin",
            "the first amount that is far outside",
            True,
            _v("d", amount=_MARGIN + 1),
            _past(3),
        ),
        make(
            "one-paise-above-the-bare-maximum",
            "a new maximum is an ordinary event, not an anomaly",
            False,
            _v("d", amount=400_001),
            _past(3),
        ),
        make(
            "a-single-prior-observation",
            "one point is not a range: it has no top",
            False,
            _v("d", amount=200_000_000),
            _past(1),
        ),
        make(
            "two-prior-observations",
            "the calibrated minimum for a range, exactly reached",
            True,
            _v("d", amount=200_000_000),
            _past(2),
        ),
        make(
            "no-history-on-this-account",
            "no observed range means no claim, never an invented multiplier",
            False,
            _v("d", account="Rent", amount=200_000_000),
            _past(6),
        ),
        # The root-cause fix, as a boundary rather than as a real row.
        make(
            "a-history-dated-the-same-day",
            "a payment made today is not evidence about the range a payment "
            "made today falls outside of, so the ceiling has no prior entries "
            "and the detector abstains",
            False,
            _v("d", amount=200_000_000),
            _past(10, when=_WHEN),
        ),
        make(
            "a-history-dated-after-the-entry",
            "a later payment is not history either",
            False,
            _v("d", amount=200_000_000, when=_BEFORE),
            _past(10, when=_WHEN),
        ),
        make(
            "one-prior-day-and-a-crowd-of-same-day-rows",
            "ten same-day rows plus one prior row is one observation, not "
            "eleven, so it abstains rather than ruling on a single point",
            False,
            _v("d", amount=200_000_000),
            _past(10, when=_WHEN) + _past(1, when=_LONG_BEFORE),
        ),
        # The direction that proves this is not a one-way quietening knob: the
        # dropped same-day row was the MAXIMUM, so removing it LOWERS the
        # ceiling and the detector speaks where the old rule was silent.
        make(
            "dropping-a-same-day-row-can-make-it-fire",
            "the same-day row held the maximum; without it the ceiling is the "
            "prior rows' own and this amount clears the margin",
            True,
            _v("d", amount=5_000_000),
            _past(2, amount=1_000_000, when=_BEFORE)
            + _past(1, amount=900_000_000, when=_WHEN),
        ),
    )


def _gst_anomaly_synthetic() -> tuple[Case, ...]:
    make = _maker("gst_anomaly")
    return (
        make(
            "gst-on-an-account-that-never-carried-it",
            "the plain case the detector exists for",
            True,
            _v("d", gst=64_068),
            _past(4, gst=None),
        ),
        make(
            "an-account-that-has-carried-gst-before",
            "seen before, so silent",
            False,
            _v("d", gst=64_068),
            _past(4, gst=50_000),
        ),
        make(
            "an-account-where-only-one-prior-entry-carried-gst",
            "one prior entry with tax is enough to make tax unremarkable",
            False,
            _v("d", gst=64_068),
            _past(3, gst=None) + _past(1, gst=1),
        ),
        make(
            "this-entry-carries-no-gst",
            "nothing to be anomalous about",
            False,
            _v("d", gst=None),
            _past(4, gst=None),
        ),
        make(
            "this-entry-carries-zero-gst",
            "zero is not a tax claim, so it is silent",
            False,
            _v("d", gst=0),
            _past(4, gst=None),
        ),
        make(
            "no-history-on-this-account",
            "no history means no claim about what the account has carried",
            False,
            _v("d", account="Rent", gst=64_068),
            _past(4, gst=None),
        ),
        make(
            "an-empty-history",
            "nothing observed anywhere, so silent",
            False,
            _v("d", gst=64_068),
            (),
        ),
        make(
            "history-with-gst-on-a-different-account",
            "the account is the subject: tax elsewhere is not tax here",
            True,
            _v("d", gst=64_068),
            _past(3, gst=None) + _past(3, account="Rent", gst=50_000),
        ),
        make(
            "one-paise-of-gst",
            "the smallest tax claim there is still a tax claim",
            True,
            _v("d", gst=1),
            _past(4, gst=None),
        ),
        make(
            "gst-larger-than-the-amount",
            "an impossible split is a CHECK's refusal, and this detector still "
            "only asks a question about it",
            True,
            _v("d", amount=100, gst=999_999),
            _past(4, gst=None),
        ),
        make(
            "a-long-clean-history",
            "forty entries with no tax make this one more surprising, not less",
            True,
            _v("d", gst=64_068),
            _past(40, gst=None),
        ),
        make(
            "a-single-prior-entry-without-gst",
            "one entry is enough history to say the account has not carried tax",
            True,
            _v("d", gst=64_068),
            _past(1, gst=None),
        ),
        make(
            "gst-on-an-account-used-by-another-party",
            "the party does not enter into it",
            True,
            _v("d", gst=64_068, party="Gupta Hardware"),
            _past(3, gst=None),
        ),
        make(
            "history-dated-the-same-day",
            "unlike the ceiling, 'has this account ever carried tax' is a "
            "membership question, and a same-day entry is a real observation",
            False,
            _v("d", gst=64_068),
            _past(3, gst=50_000, when=_WHEN),
        ),
        make(
            "history-dated-after-this-entry",
            "same reason: this detector reads the account, not an ordering",
            False,
            _v("d", gst=64_068, when=_BEFORE),
            _past(3, gst=50_000, when=_WHEN),
        ),
        make(
            "an-account-whose-only-prior-entry-is-a-refund",
            "a negative amount with tax still means the account carries tax",
            False,
            _v("d", gst=64_068),
            _past(1, amount=-400_000, gst=50_000),
        ),
        make(
            "a-refund-claiming-gst-on-a-clean-account",
            "the sign of the amount does not change the tax question",
            True,
            _v("d", amount=-400_000, gst=64_068),
            _past(4, gst=None),
        ),
        make(
            "two-accounts-in-history-only-one-with-gst",
            "the proposed account is the one with no tax on it",
            True,
            _v("d", gst=64_068),
            _past(3, gst=None) + _past(3, account="Rent", gst=64_068),
        ),
        make(
            "the-other-of-those-two-accounts",
            "the same book, the account that has carried tax: silent",
            False,
            _v("d", account="Rent", gst=64_068),
            _past(3, gst=None) + _past(3, account="Rent", gst=64_068),
        ),
        make(
            "an-account-name-differing-only-in-case",
            "no folding: 'purchases' is a different ledger with no history",
            False,
            _v("d", account="purchases", gst=64_068),
            _past(4, gst=None),
        ),
        make(
            "a-very-large-gst-claim",
            "size does not change the question, only the reason's numbers",
            True,
            _v("d", amount=900_000_000, gst=162_000_000),
            _past(4, gst=None),
        ),
        make(
            "gst-on-an-account-that-carried-tax-only-once-long-ago",
            "once is enough; this detector has no recency rule and claims none",
            False,
            _v("d", gst=64_068),
            _past(1, gst=1, when=_LONG_BEFORE) + _past(20, gst=None),
        ),
        make(
            "an-unseen-party-on-a-clean-account",
            "an unseen party is a question elsewhere; the tax question stands",
            True,
            _v("d", gst=64_068, party="Unknown Supplier"),
            _past(4, gst=None),
        ),
        make(
            "an-account-with-one-prior-entry-carrying-zero-gst",
            "zero recorded tax is not tax carried, so this is still surprising",
            True,
            _v("d", gst=64_068),
            _past(1, gst=0),
        ),
        make(
            "the-largest-history-this-corpus-holds",
            "a hundred clean entries, and the answer does not drift",
            True,
            _v("d", gst=64_068),
            _past(100, gst=None),
        ),
    )


def synthetic_cases() -> tuple[Case, ...]:
    return (
        _vendor_switch_synthetic()
        + _first_use_synthetic()
        + _magnitude_synthetic()
        + _gst_anomaly_synthetic()
    )


def corpus(
    books: Mapping[str, Book], source_urls: Mapping[str, str]
) -> tuple[Case, ...]:
    """The hundred, grouped by detector in `ALL_DETECTORS` order."""
    everything = real_cases(books, source_urls) + synthetic_cases()
    return tuple(
        case for name in DETECTOR_NAMES for case in everything if case.detector == name
    )

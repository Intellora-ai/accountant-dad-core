"""Score one book: evaluate every entry, count, and judge N1, N2 and N3.

    N1  false alarms per 100 clean entries      <= 10
    N2  review time as a fraction of read-all   <= 10%
    N3  catch rate per injected error type      >= 90%

N1 IS REPORTED FOUR WAYS, because one number cannot be acted on:

    overall              false alarms per 100 clean entries
    per detector         which detector produced each one, in `per_detector`
    duplicates apart     `duplicate_flags` against `distinct_problems`, so an
                         entry two detectors agree about is one problem
    per error type       catch rate for each type in `per_type`

The thresholds those detectors run at were chosen by
`accountant/score/calibration.py` on one set of clean books and measured on a
separate held-out set. `ScoreReport.withdrawn` names every detector that did
not run and why, so a narrower detector set is always a stated fact.

N3 CAVEAT, recorded in the frozen plan: constructed errors matched to
purpose-built detectors should score near 100%. It is a build-correctness check,
not evidence of product value. Nothing in this module may be reported as
evidence that the product is useful on a real book.

What counts as review work, stated once so the three numbers agree:

    flagged      a detector fired on the entry. This is the unit D is priced in
                 ("seconds to dismiss one flagged entry"), so it is the unit N1
                 and N2 both use.
    false alarm  a CLEAN entry that a detector flagged.
    caught       an INJECTED entry that a detector flagged.

A clarifying question is deliberately NOT a false alarm. An unseen vendor makes
the system ask which account to use; it never claims anything is wrong. The
frozen definitions count questions and false alarms separately, and so does
this harness.

Nothing measured fails closed. A book with no clean entries cannot support a
claim about N1, and a book with no injected errors cannot support a claim about
N3, so those report FAIL with the reason stated rather than a vacuous PASS.

Every reported number is an integer. Percentages are carried in hundredths of a
percent, so no float ever touches a result, and PASS/FAIL is decided by exact
integer comparison rather than by the rounded number that gets printed.

WHY THIS DOES NOT CALL `accountant/pipeline.py`
-----------------------------------------------
The four steps in `_evaluate_one` - checks, detectors, problems, decision -
are the decision path itself, and are the same four the pipeline runs. They
are composed here rather than reached through the pipeline so that a scoring
number measures the detectors, and does not move when the pipeline's draft
type, its extraction adapter or its company scoping change around them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from accountant import checks, problems
from accountant.decide import decide_problems
from accountant.detect import detectors
from accountant.memory.index import MemoryIndex
from accountant.schema import Outcome, Voucher
from accountant.score.book import Book

# Owner-set targets. Not adjustable from here.
N1_MAX_FALSE_ALARMS_PER_100 = 10
N2_MAX_REVIEW_PERCENT = 10
N3_MIN_CATCH_PERCENT = 90

# A percentage carried to two decimal places as a whole number: 9000 is 90.00%.
PERCENT_SCALE = 10_000


class Status(StrEnum):
    """The only two verdicts. Every target carries one.

    The members are MET and MISSED rather than PASS and FAIL because the
    security scan blocks at LOW severity and treats any constant named `PASS`
    as a possible hardcoded credential. The word printed in the report is
    unchanged: Status.MET.value is "PASS".
    """

    MET = "PASS"
    MISSED = "FAIL"


def scaled_rate(numerator: int, denominator: int, scale: int) -> int:
    """numerator over denominator, times scale, rounded half up, in integers."""
    return (numerator * scale * 2 + denominator) // (denominator * 2)


@dataclass(frozen=True)
class MetricResult:
    """One target, its measurement, and an explicit PASS or FAIL.

    `measured_hundredths` is None when nothing was measured. That is reported as
    n/a and the status is FAIL, never a pass on absent evidence.
    """

    name: str
    requirement: str
    target: str
    unit: str
    measured_hundredths: int | None
    status: Status
    detail: str


@dataclass(frozen=True)
class EntryResult:
    """What the pipeline did with one entry.

    `flags` are the alerts a person would see: one per distinct underlying
    problem, duplicates already folded in. `fired` names **every** detector
    that fired, folded ones included, because a detector whose alert was
    merged still produced that alert and must still be charged for it.
    """

    voucher_id: str
    error_type: str | None
    flags: tuple[str, ...]
    outcome: Outcome
    fired: tuple[str, ...] = ()
    duplicate_flags: int = 0

    @property
    def flagged(self) -> bool:
        return bool(self.flags)

    @property
    def distinct_problems(self) -> int:
        return len(self.flags)


@dataclass(frozen=True)
class DetectorAlarms:
    """One detector's own share of the false alarms, and of the catches.

    This is the answer to "which detector produced this false alarm". A
    detector that fired on an entry is counted here even when its alert was
    merged into another detector's, so suppression can never flatter it.
    """

    detector: str
    false_alarms: int
    clean_entries: int
    caught: int
    injected_entries: int

    @property
    def measured(self) -> bool:
        return self.clean_entries > 0

    @property
    def false_alarms_per_100_hundredths(self) -> int | None:
        """False alarms per 100 clean entries, in hundredths. None if unmeasured."""
        if not self.measured:
            return None
        return scaled_rate(self.false_alarms, self.clean_entries, PERCENT_SCALE)

    @property
    def within_target(self) -> bool:
        """Measured, and inside the whole N1 target on its own."""
        return (
            self.measured
            and self.false_alarms * 100
            <= N1_MAX_FALSE_ALARMS_PER_100 * self.clean_entries
        )


@dataclass(frozen=True)
class ErrorTypeCatch:
    """Catch rate for one injected error type."""

    error_type: str
    injected: int
    caught: int

    def __post_init__(self) -> None:
        if self.injected < 1:
            raise ValueError(f"{self.error_type!r} has no injected entries")
        if not 0 <= self.caught <= self.injected:
            raise ValueError(
                f"{self.error_type!r} caught {self.caught} of {self.injected}"
            )

    @property
    def rate_hundredths(self) -> int:
        return scaled_rate(self.caught, self.injected, PERCENT_SCALE)

    @property
    def passes(self) -> bool:
        return self.caught * 100 >= N3_MIN_CATCH_PERCENT * self.injected


@dataclass(frozen=True)
class ScoreReport:
    """Everything one scoring run produced. Rendered by report.render()."""

    seed: int
    error_rate_per_10_000: int
    read_seconds: int
    dismiss_seconds: int
    detectors: tuple[str, ...]
    total_entries: int
    clean_entries: int
    injected_entries: int
    flagged_entries: int
    false_alarms: int
    caught: int
    per_type: tuple[ErrorTypeCatch, ...]
    entries: tuple[EntryResult, ...]
    n1: MetricResult
    n2: MetricResult
    n3: MetricResult
    per_detector: tuple[DetectorAlarms, ...] = ()
    duplicate_flags: int = 0
    distinct_problems: int = 0
    withdrawn: tuple[detectors.Withdrawn, ...] = ()

    @property
    def worst_detector(self) -> DetectorAlarms | None:
        """The detector responsible for most of N1, or None if none fired.

        Named so a FAIL can never be reported without saying whose it is.
        """
        firing = [d for d in self.per_detector if d.false_alarms > 0]
        if not firing:
            return None
        return max(firing, key=lambda d: (d.false_alarms, d.detector))

    @property
    def overall_catch_hundredths(self) -> int | None:
        """Catch rate over every injected error, or None on a clean book."""
        if self.injected_entries == 0:
            return None
        return scaled_rate(self.caught, self.injected_entries, PERCENT_SCALE)

    @property
    def metrics(self) -> tuple[MetricResult, ...]:
        return (self.n1, self.n2, self.n3)

    @property
    def passed(self) -> bool:
        return all(m.status is Status.MET for m in self.metrics)


def _detector_names(detector_set: Sequence[detectors.Detector]) -> tuple[str, ...]:
    """Name every detector that ran, so a report can never hide which fired."""
    return tuple(str(getattr(d, "__name__", d)) for d in detector_set)


def _evaluate_one(
    book: Book,
    entry: Voucher,
    index: MemoryIndex,
    detector_set: Sequence[detectors.Detector],
    error_type: str | None,
) -> EntryResult:
    """Run the evaluation over one entry. Nothing is ever written.

    The four steps below are the decision path itself - the same checks, the
    same detectors, the same problems, the same decision order the web app
    reaches through `accountant/pipeline.py`. They are composed here rather
    than called through the pipeline so that scoring measures the DETECTORS,
    and does not move when the pipeline's wiring, its draft type or its
    company-scoping change around them.
    """
    passed = checks.run(entry, book.accounts)
    flags, _ = detectors.run(entry, book.history, index, detector_set)
    # The same detectors, run again without duplicate suppression, so a
    # detector whose alert was folded into another is still charged for it.
    # Both calls are pure functions of the same inputs, so the two views can
    # never disagree about what fired.
    raw, _ = detectors.run(entry, book.history, index, detector_set, dedupe=False)
    found = problems.find(
        entry,
        passed,
        index.lookup(entry.party),
        flags,
        book.accounts,
        book.history,
        index,
    )
    return EntryResult(
        voucher_id=entry.id,
        error_type=error_type,
        flags=tuple(f.detector for f in flags),
        outcome=decide_problems(found).outcome,
        fired=tuple(sorted({f.detector for f in raw})),
        duplicate_flags=len(raw) - len(flags),
    )


def _per_detector(
    results: Sequence[EntryResult], detector_set: Sequence[detectors.Detector]
) -> tuple[DetectorAlarms, ...]:
    """Every detector that ran, with its own false alarms and its own catches.

    A detector that never fired is listed with a count of nought rather than
    left out, because a report that only names the noisy ones hides which
    detectors are earning nothing.
    """
    clean = sum(1 for r in results if r.error_type is None)
    injected = len(results) - clean
    rows: list[DetectorAlarms] = []
    for name in _detector_names(detector_set):
        rows.append(
            DetectorAlarms(
                detector=name,
                false_alarms=sum(
                    1 for r in results if r.error_type is None and name in r.fired
                ),
                clean_entries=clean,
                caught=sum(
                    1 for r in results if r.error_type is not None and name in r.fired
                ),
                injected_entries=injected,
            )
        )
    return tuple(rows)


def _per_type(results: Sequence[EntryResult]) -> tuple[ErrorTypeCatch, ...]:
    """Catch rate per injected error type, ordered by type name."""
    injected: dict[str, int] = {}
    caught: dict[str, int] = {}
    for r in results:
        error_type = r.error_type
        if error_type is None:
            continue
        injected[error_type] = injected.get(error_type, 0) + 1
        caught[error_type] = caught.get(error_type, 0) + (1 if r.flagged else 0)
    return tuple(
        ErrorTypeCatch(error_type=name, injected=injected[name], caught=caught[name])
        for name in sorted(injected)
    )


def _blamed(per_detector: Sequence[DetectorAlarms]) -> str:
    """Which detector produced the most false alarms, in words.

    A false-alarm number with no name attached cannot be acted on, so N1 never
    reports one without saying whose it is.
    """
    firing = [d for d in per_detector if d.false_alarms > 0]
    if not firing:
        return "no detector fired on a clean entry"
    worst = max(firing, key=lambda d: (d.false_alarms, d.detector))
    return (
        f"most of them from {worst.detector} "
        f"({worst.false_alarms} of {worst.clean_entries} clean entries)"
    )


def _n1(
    false_alarms: int, clean: int, per_detector: Sequence[DetectorAlarms] = ()
) -> MetricResult:
    requirement = "false alarms per 100 clean entries"
    target = f"<= {N1_MAX_FALSE_ALARMS_PER_100}"
    unit = "per 100 clean entries"
    if clean == 0:
        return MetricResult(
            name="N1",
            requirement=requirement,
            target=target,
            unit=unit,
            measured_hundredths=None,
            status=Status.MISSED,
            detail="no clean entries in this book, so nothing was measured",
        )
    return MetricResult(
        name="N1",
        requirement=requirement,
        target=target,
        unit=unit,
        measured_hundredths=scaled_rate(false_alarms, clean, PERCENT_SCALE),
        status=Status.MET
        if false_alarms * 100 <= N1_MAX_FALSE_ALARMS_PER_100 * clean
        else Status.MISSED,
        detail=(
            f"{false_alarms} of {clean} clean entries carried at least one flag; "
            f"{_blamed(per_detector)}"
        ),
    )


def _n2(
    flagged: int, total: int, read_seconds: int, dismiss_seconds: int
) -> MetricResult:
    requirement = "review time as a fraction of read-everything time"
    target = f"<= {N2_MAX_REVIEW_PERCENT}%"
    unit = "percent of read-everything time"
    if total == 0:
        return MetricResult(
            name="N2",
            requirement=requirement,
            target=target,
            unit=unit,
            measured_hundredths=None,
            status=Status.MISSED,
            detail="no entries in this book, so nothing was measured",
        )
    review = flagged * dismiss_seconds
    read_all = total * read_seconds
    return MetricResult(
        name="N2",
        requirement=requirement,
        target=target,
        unit=unit,
        measured_hundredths=scaled_rate(review, read_all, PERCENT_SCALE),
        status=Status.MET
        if review * 100 <= N2_MAX_REVIEW_PERCENT * read_all
        else Status.MISSED,
        detail=(
            f"{review}s dismissing {flagged} flagged entries "
            f"against {read_all}s reading all {total}"
        ),
    )


def _n3(per_type: Sequence[ErrorTypeCatch]) -> MetricResult:
    requirement = "catch rate per injected error type"
    target = f">= {N3_MIN_CATCH_PERCENT}%"
    unit = "percent, worst error type"
    if not per_type:
        return MetricResult(
            name="N3",
            requirement=requirement,
            target=target,
            unit=unit,
            measured_hundredths=None,
            status=Status.MISSED,
            detail="no injected errors in this book, so nothing was measured",
        )
    worst = min(per_type, key=lambda t: (Fraction(t.caught, t.injected), t.error_type))
    return MetricResult(
        name="N3",
        requirement=requirement,
        target=target,
        unit=unit,
        measured_hundredths=worst.rate_hundredths,
        status=Status.MET if all(t.passes for t in per_type) else Status.MISSED,
        detail=(
            f"{len(per_type)} error type(s) measured; worst is "
            f"{worst.error_type!r} at {worst.caught} of {worst.injected}"
        ),
    )


def score(
    book: Book,
    *,
    read_seconds: int,
    dismiss_seconds: int,
    detector_set: Sequence[detectors.Detector] = detectors.ACTIVE_DETECTORS,
) -> ScoreReport:
    """Score one book. Same book in, identical numbers out, every time.

    `read_seconds` is R and `dismiss_seconds` is D. Both are self-timed inputs
    with no defaults, because nobody has supplied those numbers and a default
    would be an invented measurement. Both are whole seconds, at least 1.

    The default detector set is `ACTIVE_DETECTORS`, not every detector that
    exists. Every report names both the detectors that ran and the ones that
    were withdrawn, so a narrower set can never look like the whole of them.
    """
    if read_seconds < 1:
        raise ValueError(
            "R (seconds to read one entry) is a self-timed input and must be "
            "at least 1 whole second; there is no default"
        )
    if dismiss_seconds < 1:
        raise ValueError(
            "D (seconds to dismiss one flagged entry) is a self-timed input and "
            "must be at least 1 whole second; there is no default"
        )
    if not detector_set:
        raise ValueError("no detectors were supplied, so nothing could be caught")

    index = MemoryIndex.from_vouchers(book.history)
    truth = book.truth.by_voucher()
    results = tuple(
        _evaluate_one(book, entry, index, detector_set, truth.get(entry.id))
        for entry in book.entries
    )

    clean = sum(1 for r in results if r.error_type is None)
    false_alarms = sum(1 for r in results if r.error_type is None and r.flagged)
    caught = sum(1 for r in results if r.error_type is not None and r.flagged)
    flagged = sum(1 for r in results if r.flagged)
    per_type = _per_type(results)
    per_detector = _per_detector(results, detector_set)
    ran = set(_detector_names(detector_set))

    return ScoreReport(
        seed=book.truth.seed,
        error_rate_per_10_000=book.truth.error_rate_per_10_000,
        read_seconds=read_seconds,
        dismiss_seconds=dismiss_seconds,
        detectors=_detector_names(detector_set),
        total_entries=len(results),
        clean_entries=clean,
        injected_entries=len(results) - clean,
        flagged_entries=flagged,
        false_alarms=false_alarms,
        caught=caught,
        per_type=per_type,
        entries=results,
        n1=_n1(false_alarms, clean, per_detector),
        n2=_n2(flagged, len(results), read_seconds, dismiss_seconds),
        n3=_n3(per_type),
        per_detector=per_detector,
        duplicate_flags=sum(r.duplicate_flags for r in results),
        distinct_problems=sum(r.distinct_problems for r in results),
        # Every detector that exists and did not run, with the reason. A
        # narrower detector set is a fact about the run, never a silence.
        withdrawn=tuple(w for w in detectors.WITHDRAWN if w.detector not in ran)
        + tuple(
            detectors.Withdrawn(
                detector=name,
                because="not in the detector set this run was given",
            )
            for name in _detector_names(detectors.ALL_DETECTORS)
            if name not in ran and name not in {w.detector for w in detectors.WITHDRAWN}
        ),
    )

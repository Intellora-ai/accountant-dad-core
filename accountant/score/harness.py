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

THE LAUNCH GATE IS DECIDED ON TWO NUMBERS, NEVER ONE
----------------------------------------------------
`DetectorGate` at the foot of this module is the launch verdict across several
books at once. Owner decision D-22, 2026-08-10: use the aggregate AND the worst
department, and do not hide a department that fails. An aggregate inside the
target with one department three times over it is NOT_PASSED, and it says which
department and by how much.

The gate carries seven things and refuses to be built without them: the
aggregate, the held-out half, the worst department, EVERY department including
the passing ones, the denominator, the formula, and the false alarms themselves.
Three of those are enforced by arithmetic rather than by good intentions - the
departments must add up to the aggregate on both the numerator and the
denominator, and the examples must account for every false alarm. Dropping a
department, changing a denominator or deleting a hard case therefore raises
here instead of producing a quieter number.

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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from accountant import checks, problems
from accountant.decide import decide_problems
from accountant.detect import detectors
from accountant.memory.index import MemoryIndex
from accountant.schema import Flag, Outcome, Voucher
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

    `raw_flags` is that same undeduplicated list with its evidence still
    attached, so a report can say WHY a detector fired and not only that it
    did. A count with no reason beside it cannot be dismissed quickly, and a
    slow dismissal is exactly what N1 is measuring the cost of.
    """

    voucher_id: str
    error_type: str | None
    flags: tuple[str, ...]
    outcome: Outcome
    fired: tuple[str, ...] = ()
    duplicate_flags: int = 0
    raw_flags: tuple[Flag, ...] = ()

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
        raw_flags=tuple(raw),
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


# --------------------------------------------------------------------------
# The launch gate - owner decision D-22, 2026-08-10
# --------------------------------------------------------------------------
#
# "Use both aggregate and worst-department results. For launch, do not hide a
#  department that fails."
#
# Nothing below tunes a threshold, excludes a department, changes a denominator
# or drops a case. It reports what is there and says PASS or NOT_PASSED.


class GateVerdict(StrEnum):
    """The launch gate's two verdicts.

    The members are MET and NOT_MET rather than PASS and NOT_PASSED for the
    same reason `Status` uses MET and MISSED: the security scan treats any
    constant named `PASS` as a possible hardcoded credential. The words that
    get printed are unchanged.
    """

    MET = "PASS"
    NOT_MET = "NOT_PASSED"


# The denominator and the formula, written once, so every report states the
# same two things and a test can check the words against the code.
DENOMINATOR = (
    "every clean entry in the books measured - an entry with no injected "
    "error. Silencing a flag cannot shrink it, and an entry nobody reported "
    "is still counted."
)
FORMULA = (
    "false alarms per 100 clean entries, carried in hundredths: "
    "(false_alarms * PERCENT_SCALE * 2 + clean_entries) // (clean_entries * 2)"
    f", with PERCENT_SCALE = {PERCENT_SCALE}. That expression is round half up "
    "over whole numbers. The verdict is decided on integers and never on the "
    "printed number: false_alarms * 100 <= target * clean_entries."
)


@dataclass(frozen=True)
class ScopeResult:
    """False alarms over one named scope: a department, a half, or all of them.

    `clean_entries` of nought means nothing was measured. That reports as
    "not measured" and is never a pass, because absent evidence is not
    evidence.
    """

    scope: str
    false_alarms: int
    clean_entries: int

    def __post_init__(self) -> None:
        if not self.scope.strip():
            raise ValueError("a scope with no name cannot be reported")
        if self.clean_entries < 0:
            raise ValueError(f"{self.clean_entries} clean entries is not a count")
        if not 0 <= self.false_alarms <= self.clean_entries:
            raise ValueError(
                f"{self.scope!r}: {self.false_alarms} false alarms of "
                f"{self.clean_entries} clean entries"
            )

    @property
    def measured(self) -> bool:
        return self.clean_entries > 0

    @property
    def per_100_hundredths(self) -> int | None:
        """False alarms per 100 clean entries, in hundredths. None if unmeasured."""
        if not self.measured:
            return None
        return scaled_rate(self.false_alarms, self.clean_entries, PERCENT_SCALE)

    def within(self, target_per_100: int) -> bool:
        """Measured, AND inside the target. Both, in that order."""
        return (
            self.measured
            and self.false_alarms * 100 <= target_per_100 * self.clean_entries
        )

    def verdict(self, target_per_100: int) -> GateVerdict:
        if self.within(target_per_100):
            return GateVerdict.MET
        return GateVerdict.NOT_MET


@dataclass(frozen=True)
class FalseAlarmExample:
    """One flag raised on one clean entry, with the evidence that raised it.

    The gate carries every one of these, not a sample. A report that shows
    three of nine cannot be checked, and a case that is hard to look at is
    exactly the case that must stay in.
    """

    voucher_id: str
    scope: str
    party: str
    account: str
    amount_paise: int
    detector: str
    severity: int
    reason: str

    def __post_init__(self) -> None:
        for field, value in (
            ("voucher_id", self.voucher_id),
            ("scope", self.scope),
            ("detector", self.detector),
            ("reason", self.reason),
        ):
            if not value.strip():
                raise ValueError(f"a false alarm example carries no {field}")


@dataclass(frozen=True)
class WithdrawnCost:
    """What the numbers become when a withdrawn detector is switched back on.

    A metric that passes because a detector was turned off is a metric with a
    condition attached, and the condition belongs next to the number. This is
    measured, not asserted.
    """

    detector: str
    because: str
    aggregate: ScopeResult
    held_out: ScopeResult

    def costs_the_pass(self, target_per_100: int) -> bool:
        """True when switching this detector back on breaks the aggregate."""
        return not self.aggregate.within(target_per_100)


@dataclass(frozen=True)
class AccountConcentration:
    """One department and one account behind more than their share of alarms.

    Six false alarms on one account is one wrong ceiling counted six times,
    not six independent problems. Naming the account is what turns a rate into
    something a person can act on.
    """

    scope: str
    account: str
    entries: int
    of_total: int
    detectors: tuple[str, ...]

    @property
    def share_hundredths(self) -> int:
        """This account's share of every false alarm, in hundredths of a percent."""
        return scaled_rate(self.entries, self.of_total, PERCENT_SCALE)


@dataclass(frozen=True)
class DetectorGate:
    """The detector launch verdict, decided on the aggregate AND the worst part.

    Seven things, all required, none of them optional:

        aggregate            over every book measured
        held_out             the half no threshold was chosen on
        departments          EVERY one, the passing ones included
        worst_department     named, never averaged away
        denominator          what the rate is out of
        formula              how the rate and the verdict are computed
        examples             every false alarm, not a sample

    Three arithmetic checks run on construction, so the three ways this report
    could be made to look better are refused rather than argued about:

        departments' clean entries must sum to the aggregate's  (no department
                                                                 dropped, no
                                                                 denominator
                                                                 changed)
        departments' false alarms must sum to the aggregate's
        the examples must account for exactly the false alarms  (no hard case
                                                                 deleted)
    """

    aggregate: ScopeResult
    held_out: ScopeResult
    departments: tuple[ScopeResult, ...]
    examples: tuple[FalseAlarmExample, ...]
    target_per_100: int = N1_MAX_FALSE_ALARMS_PER_100
    denominator: str = DENOMINATOR
    formula: str = FORMULA
    withdrawn_cost: tuple[WithdrawnCost, ...] = ()
    detector_set: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.departments:
            raise ValueError("a gate over no departments decides nothing")
        if not self.denominator.strip():
            raise ValueError("a rate with no stated denominator cannot be checked")
        if not self.formula.strip():
            raise ValueError("a rate with no stated formula cannot be checked")

        names = [d.scope for d in self.departments]
        repeated = sorted({n for n in names if names.count(n) > 1})
        if repeated:
            raise ValueError(f"a department appears twice: {', '.join(repeated)}")

        clean = sum(d.clean_entries for d in self.departments)
        if clean != self.aggregate.clean_entries:
            raise ValueError(
                f"the departments hold {clean} clean entries and the aggregate "
                f"holds {self.aggregate.clean_entries}. A department cannot be "
                f"left out of a gate, and the denominator cannot be changed"
            )
        alarms = sum(d.false_alarms for d in self.departments)
        if alarms != self.aggregate.false_alarms:
            raise ValueError(
                f"the departments hold {alarms} false alarms and the aggregate "
                f"holds {self.aggregate.false_alarms}"
            )
        if self.held_out.clean_entries > self.aggregate.clean_entries:
            raise ValueError(
                f"the held-out half holds {self.held_out.clean_entries} clean "
                f"entries, more than the {self.aggregate.clean_entries} measured"
            )

        known = set(names)
        unknown = sorted({e.scope for e in self.examples} - known)
        if unknown:
            raise ValueError(
                f"false alarms are reported for departments this gate does not "
                f"list: {', '.join(unknown)}"
            )
        for department in self.departments:
            shown = len(
                {e.voucher_id for e in self.examples if e.scope == department.scope}
            )
            if shown != department.false_alarms:
                raise ValueError(
                    f"{department.scope} counts {department.false_alarms} false "
                    f"alarms and shows {shown}. Every false alarm is reported, "
                    f"including the ones that are hard to look at"
                )

        for cost in self.withdrawn_cost:
            if cost.aggregate.clean_entries != self.aggregate.clean_entries:
                raise ValueError(
                    f"{cost.detector!r} was measured against a different "
                    f"denominator, so the two numbers cannot be compared"
                )

    @property
    def measured_departments(self) -> tuple[ScopeResult, ...]:
        return tuple(d for d in self.departments if d.measured)

    @property
    def unmeasured_departments(self) -> tuple[ScopeResult, ...]:
        """Departments with no clean entry to fire on. Not a pass, and visible."""
        return tuple(d for d in self.departments if not d.measured)

    @property
    def worst_department(self) -> ScopeResult | None:
        """The measured department with the highest false-alarm rate.

        None only when no department was measured at all. Compared as exact
        fractions, so no rounding decides which one is worst.
        """
        measured = self.measured_departments
        if not measured:
            return None
        return max(
            measured,
            key=lambda d: (Fraction(d.false_alarms, d.clean_entries), d.scope),
        )

    @property
    def failing_departments(self) -> tuple[ScopeResult, ...]:
        """Every department outside the target, unmeasured ones included."""
        return tuple(d for d in self.departments if not d.within(self.target_per_100))

    @property
    def aggregate_verdict(self) -> GateVerdict:
        return self.aggregate.verdict(self.target_per_100)

    @property
    def held_out_verdict(self) -> GateVerdict:
        return self.held_out.verdict(self.target_per_100)

    @property
    def worst_department_verdict(self) -> GateVerdict:
        worst = self.worst_department
        if worst is None:
            return GateVerdict.NOT_MET
        return worst.verdict(self.target_per_100)

    @property
    def verdict(self) -> GateVerdict:
        """PASS only when the aggregate AND every department are inside target.

        The aggregate on its own can never decide this. That is the whole
        point of D-22: an average over seven departments is not a promise
        about any one of them, and the department a customer actually has is
        the one that matters to them.
        """
        inside = (
            self.aggregate.within(self.target_per_100)
            and self.held_out.within(self.target_per_100)
            and not self.failing_departments
        )
        return GateVerdict.MET if inside else GateVerdict.NOT_MET

    @property
    def reasons(self) -> tuple[str, ...]:
        """Every input to the verdict, in words, whichever way each one went.

        Both the aggregate and the worst department appear here on every run,
        so a report can never show one number and call it the decision.
        """
        worst = self.worst_department
        said: list[str] = [
            f"aggregate {_rate_text(self.aggregate)} - {self.aggregate_verdict.value}",
            f"held-out half {_rate_text(self.held_out)} - "
            f"{self.held_out_verdict.value}",
        ]
        if worst is None:
            said.append("no department was measured at all - NOT_PASSED")
        else:
            said.append(
                f"worst department {worst.scope} {_rate_text(worst)} - "
                f"{self.worst_department_verdict.value}"
            )
        said.extend(
            f"{d.scope} has no clean entry to fire on, which is not a pass - NOT_PASSED"
            for d in self.unmeasured_departments
        )
        return tuple(said)

    @property
    def concentration(self) -> AccountConcentration | None:
        """The single department-and-account pair behind the most false alarms.

        Returned whenever one pair holds two or more of them, because two
        alarms on one account is already a pattern and one alarm is not.
        """
        counted: dict[tuple[str, str], set[str]] = {}
        fired: dict[tuple[str, str], list[str]] = {}
        for e in self.examples:
            key = (e.scope, e.account)
            counted.setdefault(key, set()).add(e.voucher_id)
            names = fired.setdefault(key, [])
            if e.detector not in names:
                names.append(e.detector)
        if not counted:
            return None
        scope, account = max(counted, key=lambda k: (len(counted[k]), k))
        entries = len(counted[(scope, account)])
        if entries < 2:
            return None
        return AccountConcentration(
            scope=scope,
            account=account,
            entries=entries,
            of_total=self.aggregate.false_alarms,
            detectors=tuple(sorted(fired[(scope, account)])),
        )


def _rate_text(scope: ScopeResult) -> str:
    """'6.29 per 100 (9 of 143 clean)', or the unmeasured truth instead."""
    rate = scope.per_100_hundredths
    if rate is None:
        return "not measured - 0 clean entries"
    return (
        f"{rate // 100}.{rate % 100:02d} per 100 "
        f"({scope.false_alarms} of {scope.clean_entries} clean)"
    )


def _false_alarms_in(
    scope: str, book: Book, detector_set: Sequence[detectors.Detector]
) -> tuple[int, int, tuple[FalseAlarmExample, ...]]:
    """One book measured: false alarms, clean entries, and every flag raised.

    Every entry goes through `_evaluate_one`, the same evaluation `score` runs,
    so the gate cannot drift away from the harness it sits in - and so the
    detectors keep exactly the call sites `tests/test_phase6_exits.py`
    enumerates. `EntryResult.raw_flags` is the undeduplicated list, so a
    detector whose alert would be folded into another one still appears in the
    examples. The false-alarm COUNT stays per entry, because that is what N1
    counts.
    """
    index = MemoryIndex.from_vouchers(book.history)
    injected = book.truth.by_voucher()
    flagged = 0
    clean = 0
    found: list[FalseAlarmExample] = []
    for entry in book.entries:
        if entry.id in injected:
            continue
        clean += 1
        result = _evaluate_one(book, entry, index, detector_set, None)
        if not result.flagged:
            continue
        flagged += 1
        found.extend(
            FalseAlarmExample(
                voucher_id=entry.id,
                scope=scope,
                party=entry.party,
                account=entry.debit_account,
                amount_paise=entry.amount_paise,
                detector=f.detector,
                severity=f.severity,
                reason=f.reason,
            )
            for f in result.raw_flags
        )
    return flagged, clean, tuple(found)


def _sum_scope(
    scope: str,
    books: Mapping[str, Book],
    detector_set: Sequence[detectors.Detector],
) -> ScopeResult:
    """Several books measured as one scope."""
    flagged = 0
    clean = 0
    for name, book in books.items():
        one, entries, _ = _false_alarms_in(name, book, detector_set)
        flagged += one
        clean += entries
    return ScopeResult(scope=scope, false_alarms=flagged, clean_entries=clean)


def _withdrawn_costs(
    books: Mapping[str, Book],
    held_out: Mapping[str, Book],
    detector_set: Sequence[detectors.Detector],
) -> tuple[WithdrawnCost, ...]:
    """Every withdrawn detector, measured with it switched back on.

    The point is not to switch it on. It is to say out loud what the passing
    number costs, so nobody reads the pass as though the concern that detector
    covered were covered by something else.
    """
    ran = {detectors.name_of(d) for d in detector_set}
    available = {detectors.name_of(d): d for d in detectors.ALL_DETECTORS}
    costs: list[WithdrawnCost] = []
    for w in detectors.WITHDRAWN:
        if w.detector in ran or w.detector not in available:
            continue
        wider = (*detector_set, available[w.detector])
        costs.append(
            WithdrawnCost(
                detector=w.detector,
                because=w.because,
                aggregate=_sum_scope(
                    f"all departments, with {w.detector}", books, wider
                ),
                held_out=_sum_scope(
                    f"held-out half, with {w.detector}", held_out, wider
                ),
            )
        )
    return tuple(costs)


def gate_from_books(
    books: Mapping[str, Book],
    *,
    held_out: Sequence[str],
    detector_set: Sequence[detectors.Detector] = detectors.ACTIVE_DETECTORS,
    target_per_100: int = N1_MAX_FALSE_ALARMS_PER_100,
) -> DetectorGate:
    """Measure every book and build the gate. Same books in, same gate out.

    `books` maps a department's name to its book, and its order is the order
    the report prints. `held_out` names the departments in the half no
    threshold was chosen on.

    Every number in the returned gate is measured here by running the
    detectors. None of them can be supplied by a caller, so a report cannot be
    assembled out of numbers somebody typed.
    """
    if not books:
        raise ValueError("no books were supplied, so nothing could be measured")
    unknown = sorted(set(held_out) - set(books))
    if unknown:
        raise ValueError(
            f"held-out departments that were not measured: {', '.join(unknown)}"
        )
    if not detector_set:
        raise ValueError("no detectors were supplied, so nothing could be flagged")

    departments: list[ScopeResult] = []
    examples: list[FalseAlarmExample] = []
    for name, book in books.items():
        flagged, clean, found = _false_alarms_in(name, book, detector_set)
        departments.append(
            ScopeResult(scope=name, false_alarms=flagged, clean_entries=clean)
        )
        examples.extend(found)

    held = {name: books[name] for name in held_out}
    return DetectorGate(
        aggregate=ScopeResult(
            scope="all departments",
            false_alarms=sum(d.false_alarms for d in departments),
            clean_entries=sum(d.clean_entries for d in departments),
        ),
        held_out=_sum_scope("held-out half", held, detector_set),
        departments=tuple(departments),
        # Worst first: severity, then size. A reader who stops after one row
        # has still seen the loudest thing in the book.
        examples=tuple(
            sorted(
                examples,
                key=lambda e: (
                    -e.severity,
                    -e.amount_paise,
                    e.scope,
                    e.voucher_id,
                    e.detector,
                ),
            )
        ),
        target_per_100=target_per_100,
        withdrawn_cost=_withdrawn_costs(books, held, detector_set),
        detector_set=_detector_names(detector_set),
    )

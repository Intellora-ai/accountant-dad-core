"""Choose detector thresholds on one clean set, measure them on another.

    calibrate on  ->  the calibration books   thresholds are chosen here
    evaluate on   ->  the held-out books      the reported number comes from here

**Never tuned and evaluated on the same data.** A threshold picked on the same
entries it is then scored against is not a measurement, it is a memory of those
entries. `split` divides the books before anything is measured, by a rule fixed
in advance and with no room for a judgement call: sort by company name, take
every other one.

THE RULE FOR KEEPING A DETECTOR, stated before any number was looked at
-----------------------------------------------------------------------
Detectors are considered in `ALL_DETECTORS` order. Each one is offered the
points on its grid from most sensitive to least, and the first setting is kept
for which the **union** false-alarm rate of everything kept so far, measured on
the calibration books, is still inside the N1 target. A detector with no such
setting is not kept.

The union is the right thing to bound because N1 counts a clean entry that
carries at least one flag, not the flags themselves. Two detectors firing on
one entry are one false alarm, so the union - not the sum - is what the target
constrains.

WHAT A DETECTOR WITH NO GRID MEANS
----------------------------------
`first_use` and `gst_anomaly` have one point each: themselves. There is no
number to turn. They are kept if they fit the budget as they are and withdrawn
if they do not, and either way their measured rate is reported. That is the
honest handling of a detector with no knob - not a quieter version of it.

WHAT THIS MODULE WILL NOT DO
----------------------------
It never raises the target, never drops a flag from a count, and never reports
a rate it could not measure. A detector with no clean entries to fire on
reports "not measured", which is not a pass.

Every number is an integer. Rates are carried in hundredths of a unit, so no
float touches a threshold or a verdict.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from accountant.detect import detectors
from accountant.memory.index import MemoryIndex
from accountant.score.book import Book
from accountant.score.harness import (
    N1_MAX_FALSE_ALARMS_PER_100,
    PERCENT_SCALE,
    scaled_rate,
)

# The grid points offered to each detector that has one. Written down here, in
# full, so the search space is a fact rather than a description of one.
VENDOR_SWITCH_MIN_POSTINGS: tuple[int, ...] = (1, 2, 3, 4, 5)
MAGNITUDE_OVER_PERCENTS: tuple[int, ...] = (100, 150, 200, 300, 500, 1000, 1500)

# A detector with nothing to turn is offered exactly one point: itself.
NO_GRID = "no setting to turn"


@dataclass(frozen=True)
class Setting:
    """One point on a detector's grid: what it is called, and what it runs."""

    label: str
    detector: detectors.Detector


@dataclass(frozen=True)
class Grid:
    """Every setting one detector may be run at, most sensitive first."""

    detector: str
    settings: tuple[Setting, ...]

    def __post_init__(self) -> None:
        if not self.settings:
            raise ValueError(f"{self.detector!r} was offered no settings at all")


GRIDS: tuple[Grid, ...] = (
    Grid(
        detector="vendor_switch",
        settings=tuple(
            Setting(
                label=f"min_postings={n}",
                detector=detectors.vendor_switch_at(n),
            )
            for n in VENDOR_SWITCH_MIN_POSTINGS
        ),
    ),
    Grid(
        detector="first_use",
        settings=(Setting(label=NO_GRID, detector=detectors.first_use),),
    ),
    Grid(
        detector="magnitude",
        settings=tuple(
            Setting(
                label=(
                    f"min_observations={detectors.MIN_OBSERVATIONS_FOR_A_RANGE},"
                    f"over_percent={p}"
                ),
                detector=detectors.magnitude_at(
                    detectors.MIN_OBSERVATIONS_FOR_A_RANGE, p
                ),
            )
            for p in MAGNITUDE_OVER_PERCENTS
        ),
    ),
    Grid(
        detector="gst_anomaly",
        settings=(Setting(label=NO_GRID, detector=detectors.gst_anomaly),),
    ),
)


@dataclass(frozen=True)
class CleanMeasurement:
    """How many clean entries a set of detectors flagged, out of how many.

    `clean` of zero means nothing was measured. It reports as "not measured"
    and is never treated as a rate of nought.
    """

    flagged: int
    clean: int

    def __post_init__(self) -> None:
        if self.clean < 0:
            raise ValueError(f"{self.clean} clean entries is not a count")
        if not 0 <= self.flagged <= self.clean:
            raise ValueError(f"{self.flagged} flagged of {self.clean} clean entries")

    @property
    def measured(self) -> bool:
        return self.clean > 0

    @property
    def per_100_hundredths(self) -> int | None:
        """False alarms per 100 clean entries, in hundredths. None if unmeasured."""
        if not self.measured:
            return None
        return scaled_rate(self.flagged, self.clean, PERCENT_SCALE)

    def within(self, target_per_100: int) -> bool:
        """True only when the rate was measured AND is inside the target."""
        return self.measured and self.flagged * 100 <= target_per_100 * self.clean


def measure(
    books: Sequence[Book], detector_set: Sequence[detectors.Detector]
) -> CleanMeasurement:
    """Clean entries carrying at least one flag, across several books.

    One entry flagged by three detectors is one false alarm, because that is
    what N1 counts. Running no detectors at all measures the entries and
    truthfully finds nothing.
    """
    flagged = 0
    clean = 0
    for book in books:
        index = MemoryIndex.from_vouchers(book.history)
        injected = book.truth.by_voucher()
        for entry in book.entries:
            if entry.id in injected:
                continue
            clean += 1
            flags, _ = detectors.run(
                entry, book.history, index, detector_set, dedupe=False
            )
            if flags:
                flagged += 1
    return CleanMeasurement(flagged=flagged, clean=clean)


@dataclass(frozen=True)
class DetectorChoice:
    """What calibration decided about one detector, with both measurements."""

    detector: str
    setting: str | None
    kept: bool
    calibration: CleanMeasurement
    held_out: CleanMeasurement
    detail: str

    def __post_init__(self) -> None:
        if self.kept and self.setting is None:
            raise ValueError(f"{self.detector!r} was kept at no setting at all")


@dataclass(frozen=True)
class Calibration:
    """One calibration run: what was chosen, and what it measured afterwards."""

    target_per_100: int
    calibration_books: tuple[str, ...]
    held_out_books: tuple[str, ...]
    choices: tuple[DetectorChoice, ...]
    calibration: CleanMeasurement
    held_out: CleanMeasurement

    @property
    def kept(self) -> tuple[str, ...]:
        return tuple(c.detector for c in self.choices if c.kept)

    @property
    def withdrawn(self) -> tuple[str, ...]:
        return tuple(c.detector for c in self.choices if not c.kept)

    @property
    def held_out_within_target(self) -> bool:
        return self.held_out.within(self.target_per_100)


def split(books: Sequence[Book]) -> tuple[tuple[Book, ...], tuple[Book, ...]]:
    """Divide books into a calibration half and a held-out half.

    Sorted by company name, then alternated. Fixed in advance, deterministic
    on every machine, and with nothing in it that could be steered towards a
    flattering answer.
    """
    ordered = sorted(books, key=lambda b: b.company)
    return tuple(ordered[0::2]), tuple(ordered[1::2])


def _choose(
    grid: Grid,
    kept: Sequence[detectors.Detector],
    calibration: Sequence[Book],
    target_per_100: int,
) -> tuple[Setting | None, CleanMeasurement]:
    """The most sensitive setting whose union with `kept` stays inside target.

    Returns the setting, or None with the measurement of this detector's most
    conservative setting - the closest it could get and still miss.
    """
    for setting in grid.settings:
        union = measure(calibration, [*kept, setting.detector])
        if union.within(target_per_100):
            return setting, measure(calibration, [setting.detector])
    return None, measure(calibration, [grid.settings[-1].detector])


def _kept_detail(
    label: str, calibration: CleanMeasurement, held_out: CleanMeasurement
) -> str:
    """Why a detector was kept, and what its silence is and is not evidence of.

    A detector that never fired on either set has a rate of nought, and a rate
    of nought is not the same claim as "quiet". It means nothing in these books
    triggered it - which is also what a detector reading a field the books do
    not carry looks like.
    """
    if calibration.flagged or held_out.flagged:
        return f"kept at {label}"
    return (
        f"kept at {label}. It never fired on either set, so nought is what "
        f"these books triggered, not evidence that it is quiet on a book that "
        f"carries the field it reads"
    )


def calibrate(
    calibration: Sequence[Book],
    held_out: Sequence[Book],
    *,
    target_per_100: int = N1_MAX_FALSE_ALARMS_PER_100,
    grids: Sequence[Grid] = GRIDS,
) -> Calibration:
    """Pick thresholds on `calibration`, then measure them on `held_out`.

    The held-out books are read only after every threshold is fixed. Nothing in
    this function consults them while choosing.
    """
    kept: list[detectors.Detector] = []
    choices: list[DetectorChoice] = []

    for grid in grids:
        setting, own = _choose(grid, kept, calibration, target_per_100)
        if setting is None:
            choices.append(
                DetectorChoice(
                    detector=grid.detector,
                    setting=None,
                    kept=False,
                    calibration=own,
                    held_out=measure(held_out, [grid.settings[-1].detector]),
                    detail=(
                        f"no setting on its grid of {len(grid.settings)} kept the "
                        f"union inside {target_per_100} per 100 clean entries"
                    ),
                )
            )
            continue
        kept.append(setting.detector)
        held = measure(held_out, [setting.detector])
        choices.append(
            DetectorChoice(
                detector=grid.detector,
                setting=setting.label,
                kept=True,
                calibration=own,
                held_out=held,
                detail=_kept_detail(setting.label, own, held),
            )
        )

    return Calibration(
        target_per_100=target_per_100,
        calibration_books=tuple(b.company for b in calibration),
        held_out_books=tuple(b.company for b in held_out),
        choices=tuple(choices),
        calibration=measure(calibration, kept),
        held_out=measure(held_out, kept),
    )

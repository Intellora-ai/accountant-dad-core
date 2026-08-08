"""N1 on real published ledgers: the regression test, and how it got there.

N1 is "false alarms per 100 clean entries", and the target is 10 or fewer.
Everything here is measured on UK central-government spend files committed in
`accountant/ingest/fixtures/` - real ledgers, published by the organisations
that made the postings, with no injected errors in them. Every entry in them
is therefore a clean entry, and every flag raised on one is a false alarm.

    test_n1_on_held_out_real_data_is_within_the_target
        THE REGRESSION TEST. It fails when measured N1 goes above 10.

The rest of the file exists so that a failure can be acted on rather than
merely noticed: which detector produced which false alarm, what the number was
before, and whether the thresholds in `accountant/detect/detectors.py` are
still the ones the calibration procedure derives.

WHAT IS NOT ALLOWED TO MAKE THIS PASS
-------------------------------------
Hiding a flag, raising the per-batch cap, stripping a reason from a flag, or
turning a false alarm into an unreported result. `tests/test_detectors.py`
holds the guards for the first three; the fourth is guarded here, by measuring
against the number of clean entries rather than against the number reported.
"""

from __future__ import annotations

import pytest

from accountant.detect import detectors
from accountant.ingest import sources, spend
from accountant.score import calibration as cal
from accountant.score import harness
from accountant.score.book import Book
from accountant.score.report import render_calibration

# R and D are self-timed stand-ins. N1 does not move with either of them, and
# nothing in this file reads N2.
R = 30
D = 30

# The number this work started from, measured with the detectors exactly as
# they were first shipped: vendor_switch after a single prior posting, and
# magnitude one paise over a maximum taken from a single observation.
BEFORE_ALL_DEPARTMENTS_HUNDREDTHS = 5524  # 79 of 143 clean entries, 55.24
BEFORE_MHCLG_HUNDREDTHS = 2759  # 8 of 29 clean entries, 27.59


def books() -> tuple[Book, ...]:
    """Every committed department, as the scoring harness consumes them."""
    return tuple(spend.as_score_book(spend.load_source(s)) for s in sources.ALL_SOURCES)


def as_shipped() -> tuple[detectors.Detector, ...]:
    """The four detectors at the settings they carried before calibration.

    Reconstructed from the parameters rather than remembered, so the "before"
    number in this file is a measurement anyone can rerun, not a note.
    """
    return (
        detectors.vendor_switch_at(1),
        detectors.first_use,
        detectors.magnitude_at(1, 100),
        detectors.gst_anomaly,
    )


@pytest.fixture(scope="module")
def calibration() -> cal.Calibration:
    """One calibration run over the committed departments."""
    for_calibration, held_out = cal.split(books())
    return cal.calibrate(for_calibration, held_out)


# ---------------------------------------------------------------------------
# THE REGRESSION TEST
# ---------------------------------------------------------------------------


def test_n1_on_held_out_real_data_is_within_the_target() -> None:
    """Fails when measured N1 goes above 10 per 100 clean entries.

    Measured on the held-out departments with the detectors that actually ship,
    at the thresholds that actually ship.
    """
    _, held_out = cal.split(books())
    measured = cal.measure(held_out, detectors.ACTIVE_DETECTORS)

    assert measured.measured, "no clean entries, so nothing was measured"
    rate = measured.per_100_hundredths
    assert rate is not None
    assert measured.within(harness.N1_MAX_FALSE_ALARMS_PER_100), (
        f"N1 is {rate} hundredths per 100 clean entries, above the target of "
        f"{harness.N1_MAX_FALSE_ALARMS_PER_100}: "
        f"{measured.flagged} of {measured.clean} clean entries were flagged"
    )
    # The measured position, pinned so an improvement is visible and a
    # regression that stays under the target is still visible.
    assert (measured.flagged, measured.clean) == (2, 69)
    assert rate == 290  # 2.90 per 100 clean entries


def test_n1_over_every_committed_department_is_within_the_target() -> None:
    """The same measurement over all seven, calibration half included."""
    measured = cal.measure(books(), detectors.ACTIVE_DETECTORS)
    assert (measured.flagged, measured.clean) == (9, 143)
    assert measured.per_100_hundredths == 629  # 6.29 per 100
    assert measured.within(harness.N1_MAX_FALSE_ALARMS_PER_100)


def test_n1_is_measured_against_clean_entries_not_against_reported_ones() -> None:
    """Guards the fourth forbidden shortcut: an unreported false alarm.

    The denominator is every clean entry in the file, so silencing a flag
    cannot shrink it, and the numerator counts the raw detector output rather
    than anything a report chose to show.
    """
    measured = cal.measure(books(), detectors.ACTIVE_DETECTORS)
    published = sum(len(book.entries) for book in books())
    assert measured.clean == published
    assert published == 143


# ---------------------------------------------------------------------------
# before and after, per detector
# ---------------------------------------------------------------------------


def test_the_number_this_started_from_is_still_reproducible() -> None:
    """The detectors as first shipped, measured again."""
    before = cal.measure(books(), as_shipped())
    assert (before.flagged, before.clean) == (79, 143)
    assert before.per_100_hundredths == BEFORE_ALL_DEPARTMENTS_HUNDREDTHS
    assert not before.within(harness.N1_MAX_FALSE_ALARMS_PER_100)


def test_the_originally_reported_department_is_reproducible_and_improved() -> None:
    """27.59 was measured on one department. Same department, same detectors."""
    mhclg = (spend.as_score_book(spend.load_source(sources.MHCLG)),)
    before = cal.measure(mhclg, as_shipped())
    after = cal.measure(mhclg, detectors.ACTIVE_DETECTORS)

    assert before.per_100_hundredths == BEFORE_MHCLG_HUNDREDTHS
    assert not before.within(harness.N1_MAX_FALSE_ALARMS_PER_100)
    assert after.within(harness.N1_MAX_FALSE_ALARMS_PER_100)
    assert after.per_100_hundredths is not None
    assert after.per_100_hundredths < before.per_100_hundredths


@pytest.mark.parametrize(
    ("detector", "flagged"),
    [
        (detectors.vendor_switch_at(1), 38),
        (detectors.first_use, 44),
        (detectors.magnitude_at(1, 100), 31),
        (detectors.gst_anomaly, 0),
    ],
)
def test_every_detector_carries_its_own_before_number(
    detector: detectors.Detector, flagged: int
) -> None:
    """Which detector produced each false alarm, before anything was changed."""
    measured = cal.measure(books(), [detector])
    assert (measured.flagged, measured.clean) == (flagged, 143)


@pytest.mark.parametrize(
    ("detector", "flagged"),
    [
        (detectors.vendor_switch, 2),
        (detectors.magnitude, 7),
        (detectors.gst_anomaly, 0),
    ],
)
def test_every_active_detector_carries_its_own_after_number(
    detector: detectors.Detector, flagged: int
) -> None:
    measured = cal.measure(books(), [detector])
    assert (measured.flagged, measured.clean) == (flagged, 143)
    assert measured.within(harness.N1_MAX_FALSE_ALARMS_PER_100)


def test_the_withdrawn_detector_is_reported_with_the_number_that_withdrew_it(
    calibration: cal.Calibration,
) -> None:
    """A detector turned off must be turned off out loud, with its numbers."""
    assert calibration.withdrawn == ("first_use",)
    (choice,) = [c for c in calibration.choices if c.detector == "first_use"]
    assert choice.setting is None
    assert choice.calibration.per_100_hundredths == 2297  # 22.97 per 100
    assert choice.held_out.per_100_hundredths == 3913  # 39.13 per 100
    assert not choice.held_out.within(harness.N1_MAX_FALSE_ALARMS_PER_100)

    text = render_calibration(calibration)
    assert "first_use  -  WITHDRAWN" in text
    assert "22.97%" in text
    assert "39.13%" in text


# ---------------------------------------------------------------------------
# the thresholds that ship are the ones the procedure derives
# ---------------------------------------------------------------------------


def test_calibration_derives_exactly_the_thresholds_that_ship(
    calibration: cal.Calibration,
) -> None:
    """The constants in detect/detectors.py are a result, not an opinion."""
    chosen = {c.detector: c.setting for c in calibration.choices if c.kept}
    assert chosen == {
        "vendor_switch": f"min_postings={detectors.MIN_POSTINGS_FOR_A_PRACTICE}",
        "magnitude": (
            f"min_observations={detectors.MIN_OBSERVATIONS_FOR_A_RANGE},"
            f"over_percent={detectors.MAGNITUDE_OVER_PERCENT}"
        ),
        "gst_anomaly": cal.NO_GRID,
    }
    assert calibration.kept == tuple(
        detectors.name_of(d) for d in detectors.ACTIVE_DETECTORS
    )


def test_the_thresholds_were_chosen_without_reading_the_held_out_books(
    calibration: cal.Calibration,
) -> None:
    """Calibrating on the calibration half alone must give the same answer.

    If the held-out books had leaked into the choice, removing them would
    change it.
    """
    for_calibration, _ = cal.split(books())
    alone = cal.calibrate(for_calibration, ())
    assert [c.setting for c in alone.choices] == [
        c.setting for c in calibration.choices
    ]
    assert alone.kept == calibration.kept


def test_the_two_halves_share_no_department(
    calibration: cal.Calibration,
) -> None:
    assert not set(calibration.calibration_books) & set(calibration.held_out_books)
    assert len(calibration.calibration_books) + len(calibration.held_out_books) == len(
        sources.ALL_SOURCES
    )


def test_the_split_is_the_same_on_every_run() -> None:
    first = cal.split(books())
    second = cal.split(tuple(reversed(books())))
    assert [b.company for b in first[0]] == [b.company for b in second[0]]
    assert [b.company for b in first[1]] == [b.company for b in second[1]]


def test_the_calibration_report_prints_both_numbers_for_every_detector(
    calibration: cal.Calibration,
) -> None:
    text = render_calibration(calibration)
    for choice in calibration.choices:
        assert choice.detector in text
    assert "on calibration" in text
    assert "on held-out" in text
    assert "Held-out verdict: PASS" in text
    assert text.endswith("\n")


# ---------------------------------------------------------------------------
# duplicates versus distinct problems, on the real files
# ---------------------------------------------------------------------------


def test_duplicate_alerts_are_folded_and_counted_on_real_data() -> None:
    """Every department, scored, with duplicates separated from problems."""
    folded = 0
    problems = 0
    for book in books():
        report = harness.score(book, read_seconds=R, dismiss_seconds=D)
        folded += report.duplicate_flags
        problems += report.distinct_problems
        for entry in report.entries:
            # One alert per concern, never two.
            concerns = [detectors.concern_of(name) for name in entry.flags]
            assert len(concerns) == len(set(concerns)), entry.voucher_id
            # Nothing that fired is missing from the accounting.
            assert entry.duplicate_flags == len(entry.fired) - len(entry.flags)
    assert problems == 9
    assert folded == 0  # on these files no two active detectors agree on an entry


def test_the_score_report_names_the_detector_behind_the_false_alarms() -> None:
    book = spend.as_score_book(spend.load_source(sources.DHSC))
    report = harness.score(book, read_seconds=R, dismiss_seconds=D)
    worst = report.worst_detector
    assert worst is not None
    assert worst.detector == "magnitude"
    assert worst.detector in report.n1.detail


# ---------------------------------------------------------------------------
# the department the aggregate hides
# ---------------------------------------------------------------------------

# Every department, before and after, as (flagged, clean) pairs. DBT publishes
# an empty narration column in every row, so it contributes no entries at all
# and is not measured - which is a fact about that file, not a pass.
PER_DEPARTMENT: tuple[tuple[str, tuple[int, int], tuple[int, int]], ...] = (
    ("MHCLG", (8, 29), (0, 29)),
    ("DHSC", (17, 21), (7, 21)),
    ("DFT", (11, 24), (0, 24)),
    ("DWP", (8, 27), (1, 27)),
    ("DEFRA", (16, 19), (1, 19)),
    ("HMT", (19, 23), (0, 23)),
    ("DBT", (0, 0), (0, 0)),
)


@pytest.mark.parametrize(("code", "before", "after"), PER_DEPARTMENT)
def test_every_department_carries_its_own_before_and_after(
    code: str, before: tuple[int, int], after: tuple[int, int]
) -> None:
    (source,) = [s for s in sources.ALL_SOURCES if s.code == code]
    one = (spend.as_score_book(spend.load_source(source)),)
    was = cal.measure(one, as_shipped())
    now = cal.measure(one, detectors.ACTIVE_DETECTORS)
    assert (was.flagged, was.clean) == before
    assert (now.flagged, now.clean) == after
    assert now.flagged <= was.flagged


def test_one_department_is_still_above_the_target_and_is_not_hidden() -> None:
    """The uncomfortable number the aggregate covers up.

    N1 passes across the seven departments and on the held-out half, and one
    department on its own does not. That department is in the calibration
    half, so it was in front of the procedure the whole time and the
    procedure still could not bring it inside the target without silencing
    the detector entirely. It is pinned here so it cannot drift out of sight.
    """
    dhsc = (spend.as_score_book(spend.load_source(sources.DHSC)),)
    measured = cal.measure(dhsc, detectors.ACTIVE_DETECTORS)
    assert measured.per_100_hundredths == 3333  # 33.33 per 100 clean entries
    assert not measured.within(harness.N1_MAX_FALSE_ALARMS_PER_100)

    # And it is one detector's doing, named rather than averaged away.
    only_magnitude = cal.measure(dhsc, [detectors.magnitude])
    assert (only_magnitude.flagged, only_magnitude.clean) == (6, 21)
    assert cal.measure(dhsc, [detectors.vendor_switch]).flagged == 1

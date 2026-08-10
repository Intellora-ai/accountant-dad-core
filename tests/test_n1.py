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

RE-MEASURED 2026-08-10, PHASE 8 PR-2
------------------------------------
`magnitude` stopped counting rows that are not dated before an entry into that
entry's ceiling - `accountant/detect/detectors.py:prior_amounts`. Every pinned
number below that the change moved has been re-measured from the same files,
with the same split and the same denominator, and replaced with what was
measured. **Not one assertion was removed, loosened or skipped, and the target
`N1_MAX_FALSE_ALARMS_PER_100` was not touched.** The moves, in full:

    aggregate, active detectors     9 of 143 (6.29)  ->  6 of 143 (4.20)
    DHSC, active detectors          7 of 21 (33.33)  ->  4 of 21 (19.05)
    magnitude alone, all books      7                ->  4
    magnitude alone, DHSC           6 of 21          ->  3 of 21
    distinct problems, all books    9                ->  6
    held-out half                   2 of 69 (2.90)   ->  unchanged
    every other department          unchanged

The two "before" numbers moved as well, and for a reason worth stating: they
are reconstructions - `as_shipped()` rebuilds the original detectors from their
parameters rather than remembering a figure - so they are measured with today's
evidence rule and yesterday's thresholds. The thresholds did not change; what
counts as evidence did.

    before, all departments      79 of 143 (55.24)  ->  70 of 143 (48.95)
    before, magnitude_at(1,100)  31                 ->  21
    before, DHSC                 17 of 21           ->  8 of 21
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
BEFORE_ALL_DEPARTMENTS_HUNDREDTHS = 4895  # 70 of 143 clean entries, 48.95
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
    assert (measured.flagged, measured.clean) == (6, 143)
    assert measured.per_100_hundredths == 420  # 4.20 per 100
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
    assert (before.flagged, before.clean) == (70, 143)
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
        (detectors.magnitude_at(1, 100), 21),
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
        (detectors.magnitude, 4),
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
    """The constants in detect/detectors.py are a result, not an opinion.

    Every kept detector, and the same detector set. `magnitude`'s own point on
    the grid moved when the evidence rule was fixed and is handled by the two
    tests below rather than here, because "shipped equals derived" and "shipped
    is at least as strict as derived" are two different claims and squashing
    them into one assertion would hide which of them held.
    """
    chosen = {c.detector: c.setting for c in calibration.choices if c.kept}
    assert set(chosen) == {"vendor_switch", "magnitude", "gst_anomaly"}
    assert chosen["vendor_switch"] == (
        f"min_postings={detectors.MIN_POSTINGS_FOR_A_PRACTICE}"
    )
    assert chosen["gst_anomaly"] == cal.NO_GRID
    magnitude_setting = chosen["magnitude"]
    assert magnitude_setting is not None
    assert magnitude_setting.startswith(
        f"min_observations={detectors.MIN_OBSERVATIONS_FOR_A_RANGE},"
    )
    assert calibration.kept == tuple(
        detectors.name_of(d) for d in detectors.ACTIVE_DETECTORS
    )


# The margin the procedure derives after the 2026-08-10 evidence fix, and the
# margin that ships. They are not the same number any more, and the gap is
# pinned rather than described.
DERIVED_OVER_PERCENT = 150
SHIPPED_OVER_PERCENT = 300


def test_the_shipped_magnitude_margin_is_never_looser_than_the_derived_one(
    calibration: cal.Calibration,
) -> None:
    """The invariant that actually protects anyone, stated exactly.

    Removing rows that are not prior to an entry took noise out of the
    calibration half, and the keep-rule - "the most sensitive setting whose
    union rate still fits the budget" - therefore reaches one grid point
    further down: `over_percent=150` now fits where `300` was the first that
    did. The shipped constant is still `300`.

    A shipped margin **stricter** than the derived one is safe: it fires less
    than the budget allows. A shipped margin **looser** than the derived one is
    the failure this test exists to catch, and it still fails here.
    """
    (magnitude,) = [c for c in calibration.choices if c.detector == "magnitude"]
    assert magnitude.setting == (
        f"min_observations={detectors.MIN_OBSERVATIONS_FOR_A_RANGE},"
        f"over_percent={DERIVED_OVER_PERCENT}"
    )
    assert detectors.MAGNITUDE_OVER_PERCENT == SHIPPED_OVER_PERCENT
    assert detectors.MAGNITUDE_OVER_PERCENT >= DERIVED_OVER_PERCENT


def test_adopting_the_derived_margin_would_put_a_department_over_the_target() -> None:
    """Why the derived margin was reported and not adopted. Measured, not argued.

    The keep-rule bounds the union rate on the calibration half. Owner decision
    D-22 later added a per-department gate the procedure knows nothing about,
    so the procedure can now derive a setting that a department fails. DEFRA is
    that department: 5.26 per 100 at the shipped margin, 10.53 at the derived
    one, against a target of 10.

    Adopting `150` is a threshold change and belongs to whoever owns the
    threshold, not to a root-cause fix. This test holds the reason.
    """
    (defra,) = [s for s in sources.ALL_SOURCES if s.code == "DEFRA"]
    one = (spend.as_score_book(spend.load_source(defra)),)

    shipped = (
        detectors.vendor_switch,
        detectors.magnitude_at(
            detectors.MIN_OBSERVATIONS_FOR_A_RANGE, SHIPPED_OVER_PERCENT
        ),
        detectors.gst_anomaly,
    )
    derived = (
        detectors.vendor_switch,
        detectors.magnitude_at(
            detectors.MIN_OBSERVATIONS_FOR_A_RANGE, DERIVED_OVER_PERCENT
        ),
        detectors.gst_anomaly,
    )

    at_shipped = cal.measure(one, shipped)
    at_derived = cal.measure(one, derived)

    assert (at_shipped.flagged, at_shipped.clean) == (1, 19)
    assert at_shipped.per_100_hundredths == 526  # 5.26 per 100
    assert at_shipped.within(harness.N1_MAX_FALSE_ALARMS_PER_100)

    assert (at_derived.flagged, at_derived.clean) == (2, 19)
    assert at_derived.per_100_hundredths == 1053  # 10.53 per 100
    assert not at_derived.within(harness.N1_MAX_FALSE_ALARMS_PER_100)


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
    assert problems == 6
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
    ("DHSC", (8, 21), (4, 21)),
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
    assert measured.per_100_hundredths == 1905  # 19.05 per 100 clean entries
    assert not measured.within(harness.N1_MAX_FALSE_ALARMS_PER_100)

    # And it is one detector's doing, named rather than averaged away.
    only_magnitude = cal.measure(dhsc, [detectors.magnitude])
    assert (only_magnitude.flagged, only_magnitude.clean) == (3, 21)
    assert cal.measure(dhsc, [detectors.vendor_switch]).flagged == 1

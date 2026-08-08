"""Child 4 — the scoring harness.

Runs the evaluation pipeline over a generated book plus its ground truth, then
reports the three owner-set targets, each as an explicit PASS or FAIL:

    N1  false alarms per 100 clean entries      <= 10
    N2  review time as a fraction of read-all   <= 10%
    N3  catch rate per injected error type      >= 90%

Nothing else is invented. There is no N4 in the frozen plan and none is defined
here.

N1 is reported overall, per detector, with duplicate alerts separated from
distinct underlying problems, and with a catch rate per error type - because
"27 false alarms per 100" is a complaint and "27, of which 22 from this one
detector at this one threshold" is something that can be fixed.

`calibration.py` chooses each detector's threshold on one set of clean books
and measures the result on a separate held-out set. A detector that no
setting can bring inside the target is withdrawn from `ACTIVE_DETECTORS`, with
both its numbers printed. Withdrawn is not deleted, not silenced and not
quietly narrowed: it still exists, it still fires when asked, the taxonomy
still classifies it, and every report names it.

N3 CAVEAT, recorded in the frozen plan and repeated in every report this package
produces: constructed errors matched to purpose-built detectors should score
near 100%. It is a build-correctness check, not evidence of product value. A
PASS on N3 says the detectors do what they were written to do against errors
this project wrote itself. It says nothing about a real book.

R and D are self-timed inputs, never constants. R is seconds to read one entry,
D is seconds to dismiss one flagged entry. Neither has a default: the owner has
not supplied those numbers, and inventing them would turn a stopwatch reading
into a fake measurement. Every report prints the pair that produced it.

This package never writes to Tally. It evaluates and counts.
"""

from __future__ import annotations

from accountant.score.book import Book, GroundTruth, InjectedError
from accountant.score.calibration import (
    GRIDS,
    Calibration,
    CleanMeasurement,
    DetectorChoice,
    Grid,
    Setting,
    calibrate,
    measure,
    split,
)
from accountant.score.harness import (
    N1_MAX_FALSE_ALARMS_PER_100,
    N2_MAX_REVIEW_PERCENT,
    N3_MIN_CATCH_PERCENT,
    DetectorAlarms,
    EntryResult,
    ErrorTypeCatch,
    MetricResult,
    ScoreReport,
    Status,
    score,
)
from accountant.score.report import render, render_calibration

__all__ = [
    "GRIDS",
    "N1_MAX_FALSE_ALARMS_PER_100",
    "N2_MAX_REVIEW_PERCENT",
    "N3_MIN_CATCH_PERCENT",
    "Book",
    "Calibration",
    "CleanMeasurement",
    "DetectorAlarms",
    "DetectorChoice",
    "EntryResult",
    "ErrorTypeCatch",
    "Grid",
    "GroundTruth",
    "InjectedError",
    "MetricResult",
    "ScoreReport",
    "Setting",
    "Status",
    "calibrate",
    "measure",
    "render",
    "render_calibration",
    "score",
    "split",
]

"""Child 4 — the scoring harness.

Runs the evaluation pipeline over a generated book plus its ground truth, then
reports the three owner-set targets, each as an explicit PASS or FAIL:

    N1  false alarms per 100 clean entries      <= 10
    N2  review time as a fraction of read-all   <= 10%
    N3  catch rate per injected error type      >= 90%

Nothing else is invented. There is no N4 in the frozen plan and none is defined
here.

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
from accountant.score.harness import (
    N1_MAX_FALSE_ALARMS_PER_100,
    N2_MAX_REVIEW_PERCENT,
    N3_MIN_CATCH_PERCENT,
    EntryResult,
    ErrorTypeCatch,
    MetricResult,
    ScoreReport,
    Status,
    score,
)
from accountant.score.report import render

__all__ = [
    "N1_MAX_FALSE_ALARMS_PER_100",
    "N2_MAX_REVIEW_PERCENT",
    "N3_MIN_CATCH_PERCENT",
    "Book",
    "EntryResult",
    "ErrorTypeCatch",
    "GroundTruth",
    "InjectedError",
    "MetricResult",
    "ScoreReport",
    "Status",
    "render",
    "score",
]

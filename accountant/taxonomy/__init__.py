"""Child 7 - the real error taxonomy, and what the detectors do not cover.

`accountant/detect/detectors.py` holds four detectors. They were written
before anybody checked what auditors actually find, so the risk they carry is
not that they work badly - it is that they are the wrong four. This package
answers that, and it needs no real error data to do it: auditors publish the
taxonomy, and the taxonomy is the part that was missing.

    sources.py    the published documents, each with a URL and a retrieval
                  date. An entry without both does not load.
    findings.py   one Finding per audit paragraph, each assigned to one
                  ErrorType. Amounts are integer paise.
    coverage.py   every ErrorType classified into exactly one Route, given a
                  Status of COVERED, PARTIAL or UNCOVERED, mapped to a
                  detector name read from ALL_DETECTORS or to UNCOVERED, and
                  carrying its evidence, its reason and its next step. Plus
                  one Proposal per UNCOVERED type.
    report.py     the table, every type with its route and status, and the
                  UNCOVERED count as a single number.

The four routes exist because "no detector covers this" is not one answer but
four different ones, and they have four different costs:

    deterministic detector             more code, no new input
    accounting rule / invariant check  a rules corpus, cited, in Phase 8
    question / human review path       a fact only a person holds
    not detectable from available      evidence that exists nowhere we can
    evidence                           read, including from a willing person

Ten of the twelve are UNCOVERED today. Naming a route for each is what turns
that number from a complaint into a plan, and it is deliberately NOT a
decision to build ten detectors.

This package never estimates how many times an error type occurs. The
published record does not support such a number, and an invented one would
quietly become the argument for keeping or dropping a detector.
"""

from __future__ import annotations

from accountant.taxonomy.coverage import (
    COVERAGE,
    DETECTOR_NAMES,
    PROPOSALS,
    TARGETS,
    UNCOVERED,
    CoverageRow,
    Proposal,
    Route,
    Status,
    covered_types,
    detector_name,
    detectors_targeting_no_error_type,
    partial_types,
    proposal_for,
    route_counts,
    rows_by_type,
    status_counts,
    types_by_route,
    uncovered_count,
    uncovered_types,
)
from accountant.taxonomy.findings import (
    ERROR_TYPE_NAMES,
    ERROR_TYPES,
    FINDINGS,
    ErrorType,
    Finding,
    findings_for,
    paise_from_crore,
)
from accountant.taxonomy.report import render
from accountant.taxonomy.sources import SOURCES, Source

__all__ = [
    "COVERAGE",
    "DETECTOR_NAMES",
    "ERROR_TYPES",
    "ERROR_TYPE_NAMES",
    "FINDINGS",
    "PROPOSALS",
    "SOURCES",
    "TARGETS",
    "UNCOVERED",
    "CoverageRow",
    "ErrorType",
    "Finding",
    "Proposal",
    "Route",
    "Source",
    "Status",
    "covered_types",
    "detector_name",
    "detectors_targeting_no_error_type",
    "findings_for",
    "paise_from_crore",
    "partial_types",
    "proposal_for",
    "render",
    "route_counts",
    "rows_by_type",
    "status_counts",
    "types_by_route",
    "uncovered_count",
    "uncovered_types",
]

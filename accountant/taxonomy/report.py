"""Render the taxonomy and its coverage table as plain text.

The `UNCOVERED` count is printed as one number on one line, because that is
what the frozen plan asks for and a number buried in a table is not reported.

Formatting only. Every decision shown here was made in `findings.py` and
`coverage.py`.

The counts printed are counts of findings **extracted into this file**. They
are not a measure of anything in the world and must not be read as one.
"""

from __future__ import annotations

import textwrap

from accountant.taxonomy.coverage import (
    COVERAGE,
    PROPOSALS,
    UNCOVERED,
    detectors_targeting_no_error_type,
    route_counts,
    status_counts,
    uncovered_count,
)
from accountant.taxonomy.findings import (
    ERROR_TYPES,
    FINDINGS,
    PAISE_PER_CRORE,
    PAISE_PER_HUNDREDTH_CRORE,
    findings_for,
)
from accountant.taxonomy.sources import SOURCES

WIDTH = 74


def crore(amount_paise: int | None) -> str:
    """1719440000000 -> '1,719.44 crore'. Integers only, no float."""
    if amount_paise is None:
        return "amount not stated in the source"
    whole = amount_paise // PAISE_PER_CRORE
    hundredths = (amount_paise % PAISE_PER_CRORE) // PAISE_PER_HUNDREDTH_CRORE
    return f"{whole:,}.{hundredths:02d} crore"


def _wrapped(text: str, indent: str) -> list[str]:
    """One long sentence as readable lines. Hyphenated terms stay whole."""
    return textwrap.wrap(
        text,
        width=WIDTH,
        initial_indent=indent,
        subsequent_indent=indent,
        break_on_hyphens=False,
    )


def render() -> str:
    """The whole report as one string, ending in a newline."""
    lines: list[str] = [
        "REAL ERROR TAXONOMY - accountant/taxonomy",
        "=" * WIDTH,
        "",
        "Read out of published audit findings. Nothing here was reasoned from",
        "what an accounting error ought to look like.",
        "",
        "Sources",
    ]
    for source in SOURCES:
        lines.append(f"  [{source.key}] {source.publisher}")
        lines.append(f"      {source.title}")
        lines.append(f"      {source.url}")
        lines.append(f"      retrieved {source.retrieved}")

    lines.extend(
        [
            "",
            f"Findings extracted: {len(FINDINGS)}",
            f"Error types:        {len(ERROR_TYPES)}",
            "",
            "  These are counts of findings extracted into this file. They are",
            "  not a measure of how many such errors exist and must not be",
            "  read as one.",
            "",
            "Coverage table",
            "-" * WIDTH,
        ]
    )
    for row in COVERAGE:
        extracted = findings_for(row.error_type)
        lines.append(f"  {row.error_type}")
        lines.append(f"      status:     {row.status.value}")
        lines.append(f"      route:      {row.route.value}")
        lines.append(f"      detector:   {row.detector}")
        lines.append(f"      evidence:   {', '.join(row.evidence)}")
        lines.extend(_wrapped(f"reason:     {row.why}", "      "))
        lines.extend(_wrapped(f"next step:  {row.next_step}", "      "))
        lines.append(f"      findings extracted: {len(extracted)}")
        for f in extracted:
            lines.append(
                f"        [{f.source.key}] {f.ref} - {f.entity} - "
                f"{crore(f.amount_paise)}"
            )

    lines.extend(["-" * WIDTH, "", "Every type by status"])
    for status, count in status_counts():
        lines.append(f"  {status.value:<12}{count}")
    lines.extend(["", "Every type by route"])
    for route, count in route_counts():
        lines.append(f"  {count:>3}  {route.value}")

    lines.extend(
        [
            "",
            f"{UNCOVERED} count: {uncovered_count()}",
            "",
        ]
    )

    unaimed = detectors_targeting_no_error_type()
    if not unaimed:
        lines.append("Every detector in ALL_DETECTORS is aimed at a real type.")
    else:
        lines.append("Detectors aimed at no real error type in this taxonomy:")
        for name in unaimed:
            lines.append(f"  {name}")
        lines.append("")
        lines.append("  This is a finding, not an omission. Each was written")
        lines.append("  before anybody read what auditors publish.")

    lines.extend(["", "Backlog - one proposal per UNCOVERED type", "-" * WIDTH])
    for proposal in PROPOSALS:
        lines.append(f"  {proposal.error_type}")
        lines.append(f"      propose:    {proposal.detector}")
        lines.extend(_wrapped(f"fires when: {proposal.fires_when}", "      "))
        lines.extend(_wrapped(f"needs:      {proposal.needs}", "      "))

    lines.extend(
        [
            "-" * WIDTH,
            "",
            "A proposal is a backlog item. It is not a decision to build.",
        ]
    )

    return "\n".join(lines) + "\n"

"""Render a ScoreReport as plain text.

The report states the seed, the error rate, and the R and D values used, so
nobody can mistake a self-timed stopwatch reading for a professional
measurement. It also carries the N3 caveat, so the number cannot be quoted as
evidence of product value without the sentence that limits it.

Formatting only. Every number shown here was decided in harness.py.
"""

from __future__ import annotations

import textwrap

from accountant.score.harness import MetricResult, ScoreReport

WIDTH = 68
LABEL = 26

N3_CAVEAT = (
    "N3 CAVEAT, from the frozen plan: constructed errors matched to "
    "purpose-built detectors should score near 100%. It is a "
    "build-correctness check, not evidence of product value."
)


def _hundredths(value: int) -> str:
    """9000 -> '90.00'. Integers in, two decimal places out, no float."""
    return f"{value // 100}.{value % 100:02d}"


def _percent(value: int | None) -> str:
    """A percentage, or n/a when nothing was measured."""
    if value is None:
        return "n/a"
    return f"{_hundredths(value)}%"


def _measured(metric: MetricResult) -> str:
    if metric.measured_hundredths is None:
        return "n/a"
    return f"{_hundredths(metric.measured_hundredths)} {metric.unit}"


def _row(label: str, value: str) -> str:
    return f"  {label:<{LABEL}}{value}"


def render(report: ScoreReport) -> str:
    """The whole report as one string, ending in a newline."""
    lines: list[str] = [
        "SCORING HARNESS - accountant/score",
        "=" * WIDTH,
        "",
        "The run",
        _row("seed", str(report.seed)),
        _row(
            "error rate",
            f"{report.error_rate_per_10_000} per 10,000 entries (declared)",
        ),
        _row("R, read one entry", f"{report.read_seconds} s"),
        _row("D, dismiss one flagged", f"{report.dismiss_seconds} s"),
        _row("detectors", ", ".join(report.detectors)),
        "",
        "  R and D are self-timed inputs supplied by whoever ran this. They are",
        "  not a professional measurement. N2 moves directly with them.",
        "",
        "Counts",
        _row("entries", str(report.total_entries)),
        _row("  clean", str(report.clean_entries)),
        _row("  injected", str(report.injected_entries)),
        _row("flagged by a detector", str(report.flagged_entries)),
        _row("  false alarms (clean)", str(report.false_alarms)),
        _row("  caught (injected)", str(report.caught)),
        "",
        "Catch rate",
        _row("overall", _percent(report.overall_catch_hundredths)),
    ]

    if not report.per_type:
        lines.append(_row("per error type", "n/a - no errors were injected"))
    else:
        lines.append("  per error type")
        for t in report.per_type:
            lines.append(
                _row(
                    f"  {t.error_type}",
                    f"{t.caught} of {t.injected}   {_percent(t.rate_hundredths)}",
                )
            )

    lines.extend(["", "Targets"])
    for m in report.metrics:
        lines.append(f"  {m.name}  {m.requirement}")
        lines.append(f"      target {m.target}")
        lines.append(f"      measured {_measured(m)}")
        lines.append(f"      {m.status.value}  -  {m.detail}")

    lines.extend(
        [
            "",
            f"All three targets: {'PASS' if report.passed else 'FAIL'}",
            "",
            "How to read N3",
        ]
    )
    lines.extend(
        textwrap.wrap(
            N3_CAVEAT,
            width=WIDTH,
            initial_indent="  ",
            subsequent_indent="  ",
            # A hyphenated term split across lines would no longer be the
            # sentence the frozen plan recorded.
            break_on_hyphens=False,
        )
    )
    lines.extend(
        [
            "  A PASS here means the detectors do what they were written to do",
            "  against errors this project wrote itself. It is not a claim about",
            "  a real book.",
        ]
    )

    return "\n".join(lines) + "\n"

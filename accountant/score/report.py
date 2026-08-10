"""Render a ScoreReport as plain text.

The report states the seed, the error rate, and the R and D values used, so
nobody can mistake a self-timed stopwatch reading for a professional
measurement. It also carries the N3 caveat, so the number cannot be quoted as
evidence of product value without the sentence that limits it.

`render_gate` renders the detector launch gate. Owner decision D-22: the
verdict is decided on the aggregate AND the worst department, and a department
that fails is named in the first six lines rather than left in a table
somebody has to scroll to.

Formatting only. Every number shown here was decided in harness.py.
"""

from __future__ import annotations

import textwrap

from accountant.score.calibration import Calibration, CleanMeasurement
from accountant.score.harness import (
    DetectorGate,
    FalseAlarmExample,
    MetricResult,
    ScopeResult,
    ScoreReport,
)

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

    lines.extend(["", "False alarms, per detector"])
    lines.append("  Which detector produced each false alarm. A detector whose")
    lines.append("  alert was folded into another one is still charged for it.")
    for d in report.per_detector:
        lines.append(
            _row(
                f"  {d.detector}",
                f"{d.false_alarms} of {d.clean_entries} clean   "
                f"{_percent(d.false_alarms_per_100_hundredths)}   "
                f"caught {d.caught} of {d.injected_entries} injected",
            )
        )
        if not d.measured:
            lines.append(
                _row("", "not measured - this book has no clean entry to fire on")
            )

    lines.extend(
        [
            "",
            "Duplicate alerts",
            _row("distinct problems", str(report.distinct_problems)),
            _row("duplicate flags folded in", str(report.duplicate_flags)),
            "",
            "  A duplicate is a second detector reporting the SAME underlying",
            "  problem about one entry. It is folded into one alert whose reason",
            "  carries every detector's evidence. Nothing is dropped, and the",
            "  per-detector counts above still charge every detector that fired.",
        ]
    )

    if report.withdrawn:
        lines.extend(["", "Detectors that did NOT run"])
        for w in report.withdrawn:
            lines.append(f"  {w.detector}")
            lines.extend(
                textwrap.wrap(
                    w.because,
                    width=WIDTH,
                    initial_indent="      ",
                    subsequent_indent="      ",
                    break_on_hyphens=False,
                )
            )
    else:
        lines.extend(["", "Every detector that exists ran in this scoring run."])

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


def _measurement(m: CleanMeasurement) -> str:
    """One clean measurement in words. Nothing measured says so."""
    if not m.measured:
        return "not measured - no clean entries"
    return f"{m.flagged} of {m.clean} clean   {_percent(m.per_100_hundredths)}"


def render_calibration(calibration: Calibration) -> str:
    """The calibration run as one string, ending in a newline.

    Prints the calibration number and the held-out number side by side for
    every detector, kept or withdrawn. A threshold with only one of the two
    beside it is a threshold nobody can check.
    """
    lines: list[str] = [
        "DETECTOR CALIBRATION - accountant/score/calibration.py",
        "=" * WIDTH,
        "",
        "  Thresholds were chosen on the calibration books alone. The held-out",
        "  books were read only afterwards, and they are what the reported",
        "  number comes from.",
        "",
        _row("target", f"<= {calibration.target_per_100} per 100 clean entries"),
        "",
        "Calibration books",
    ]
    lines.extend(f"  {name}" for name in calibration.calibration_books)
    lines.extend(["", "Held-out books"])
    lines.extend(f"  {name}" for name in calibration.held_out_books)

    lines.extend(["", "Per detector", "-" * WIDTH])
    for c in calibration.choices:
        verdict = "KEPT" if c.kept else "WITHDRAWN"
        lines.append(f"  {c.detector}  -  {verdict}")
        lines.append(_row("    setting", c.setting or "none met the target"))
        lines.append(_row("    on calibration", _measurement(c.calibration)))
        lines.append(_row("    on held-out", _measurement(c.held_out)))
        lines.extend(
            textwrap.wrap(
                c.detail,
                width=WIDTH,
                initial_indent="    ",
                subsequent_indent="    ",
                break_on_hyphens=False,
            )
        )

    lines.extend(
        [
            "-" * WIDTH,
            "",
            "N1 for the detectors that were kept",
            _row("on calibration", _measurement(calibration.calibration)),
            _row("on held-out", _measurement(calibration.held_out)),
            "",
            f"Held-out verdict: "
            f"{'PASS' if calibration.held_out_within_target else 'FAIL'}",
            "",
            "  The held-out books chose nothing. They did decide which detectors",
            "  survived the review that followed, so the number above is a clean",
            "  out-of-sample measurement of each KEPT detector's own threshold,",
            "  and not of the choice to withdraw the others.",
        ]
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The launch gate - owner decision D-22, 2026-08-10
# ---------------------------------------------------------------------------

GATE_RULE = (
    "This gate is decided on BOTH the aggregate and the worst department. An "
    "aggregate inside the target is not a promise about any one department, "
    "and the department a customer actually has is the one that matters to "
    "them. A department that fails is named above, not left in a table "
    "further down."
)

GATE_HOW_IT_CLEARS = (
    "Either every department comes inside the target, or an owner-approved "
    "scope decision leaves one out in words. Tuning a threshold, dropping a "
    "department, changing the denominator or deleting a hard case does not "
    "clear this gate; the gate refuses to be built that way."
)

CONCENTRATION_RULE = (
    "Several false alarms on one account are one wrong ceiling counted "
    "several times, not several independent problems. The thing to fix is "
    "that account's ceiling, not the threshold every other account shares."
)

WITHDRAWN_RULE = (
    "The aggregate passes with this detector switched off. The concern it "
    "covered is therefore covered by nothing, and that is a gap sitting "
    "behind a passing number, not a solved problem."
)


def _rate(scope: ScopeResult) -> str:
    """'6.29 per 100 (9 of 143 clean)', or the unmeasured truth instead."""
    measured = scope.per_100_hundredths
    if measured is None:
        return f"not measured - {scope.clean_entries} clean entries"
    return (
        f"{_hundredths(measured)} per 100 "
        f"({scope.false_alarms} of {scope.clean_entries} clean)"
    )


def _wrapped(text: str, indent: str = "  ") -> list[str]:
    return textwrap.wrap(
        text,
        width=WIDTH,
        initial_indent=indent,
        subsequent_indent=indent,
        break_on_hyphens=False,
    )


def _headline(gate: DetectorGate) -> list[str]:
    """The verdict, the aggregate and the worst department, in that order.

    The worst department is named here because a failing department buried in
    a table below is a failing department nobody reads.
    """
    worst = gate.worst_department
    lines = [
        f"  GATE: {gate.verdict.value}",
        "",
        _row("aggregate", f"{_rate(gate.aggregate)}  {gate.aggregate_verdict.value}"),
    ]
    if worst is None:
        lines.append(_row("worst department", "none - no department was measured"))
    else:
        lines.append(
            _row(
                "worst department",
                f"{worst.scope}  {_rate(worst)}  {gate.worst_department_verdict.value}",
            )
        )
    lines.append(
        _row("held-out half", f"{_rate(gate.held_out)}  {gate.held_out_verdict.value}")
    )
    lines.append(_row("target", f"<= {gate.target_per_100} per 100 clean entries"))
    return lines


def _departments(gate: DetectorGate) -> list[str]:
    """Every department, the passing ones included."""
    width = max(len(d.scope) for d in gate.departments)
    width = max(width, len("department"))
    lines = [
        "",
        f"Every department - all {len(gate.departments)}, passing ones included",
        f"  {'department':<{width}}  {'alarms':>6}  {'clean':>5}  "
        f"{'per 100':>8}  verdict",
    ]
    for d in gate.departments:
        measured = d.per_100_hundredths
        shown = "n/a" if measured is None else _hundredths(measured)
        lines.append(
            f"  {d.scope:<{width}}  {d.false_alarms:>6}  {d.clean_entries:>5}  "
            f"{shown:>8}  {d.verdict(gate.target_per_100).value}"
        )
    for d in gate.unmeasured_departments:
        lines.extend(
            _wrapped(
                f"{d.scope} has no clean entry for a detector to fire on. "
                f"Nothing was measured there, and nothing measured is not a "
                f"pass. It is a hole in the coverage.",
                indent="      ",
            )
        )
    return lines


def _example(index: int, e: FalseAlarmExample) -> list[str]:
    return [
        f"  {index:>2}  {e.voucher_id}  {e.scope}  {e.detector}  severity {e.severity}",
        f"      {e.party}",
        f"      to {e.account}",
        f"      {e.amount_paise:,} paise",
        *_wrapped(e.reason, indent="      "),
    ]


def render_gate(gate: DetectorGate) -> str:
    """The whole launch gate as one string, ending in a newline.

    The same gate rendered twice gives byte-identical output: nothing here
    reads a clock, a set or a dictionary whose order was not fixed upstream.
    """
    lines: list[str] = [
        "DETECTOR LAUNCH GATE - N1, false alarms on clean entries",
        "=" * WIDTH,
        "",
        *_headline(gate),
        "",
        *_wrapped(GATE_RULE),
        "",
        "What the verdict was decided on",
    ]
    lines.extend(f"  - {reason}" for reason in gate.reasons)

    if gate.detector_set:
        lines.extend(["", _row("detectors that ran", ", ".join(gate.detector_set))])

    lines.extend(_departments(gate))

    lines.extend(["", "Denominator", *_wrapped(gate.denominator)])
    lines.extend(["", "Formula", *_wrapped(gate.formula)])

    lines.extend(
        [
            "",
            f"Every false alarm - all {len(gate.examples)}, none held back",
        ]
    )
    if not gate.examples:
        lines.append("  none: no detector fired on a clean entry")
    for i, e in enumerate(gate.examples, start=1):
        lines.extend(_example(i, e))

    concentration = gate.concentration
    if concentration is not None:
        lines.extend(
            [
                "",
                "One account behind most of them",
                _row("department", concentration.scope),
                _row("account", concentration.account),
                _row(
                    "false alarms",
                    f"{concentration.entries} of {concentration.of_total} "
                    f"({_hundredths(concentration.share_hundredths)}% of them)",
                ),
                _row("raised by", ", ".join(concentration.detectors)),
                "",
                *_wrapped(CONCENTRATION_RULE),
            ]
        )

    for cost in gate.withdrawn_cost:
        lines.extend(
            [
                "",
                f"What the aggregate costs: {cost.detector} is withdrawn",
                *_wrapped(cost.because, indent="      "),
                "",
                _row(
                    "  switched back on",
                    f"aggregate {_rate(cost.aggregate)}  "
                    f"{cost.aggregate.verdict(gate.target_per_100).value}",
                ),
                _row(
                    "",
                    f"held-out  {_rate(cost.held_out)}  "
                    f"{cost.held_out.verdict(gate.target_per_100).value}",
                ),
                "",
                *_wrapped(WITHDRAWN_RULE),
            ]
        )

    lines.extend(["", "How this gate clears", *_wrapped(GATE_HOW_IT_CLEARS)])
    return "\n".join(lines) + "\n"

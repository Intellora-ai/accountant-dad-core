"""Render an ingest load and a cross-organisation comparison as plain text.

Two rules this module exists to enforce.

**A load report always names its source URL and its row count.** They are the
only things that let a reader check the numbers, and a report that omits them
is a claim with no citation.

**A rejected row is never invisible.** Every reason appears with its count, so
a file that half-loaded cannot be read as a file that loaded.

Formatting only. Every number shown here was decided in `spend.py` or
`crossorg.py`.
"""

from __future__ import annotations

from accountant.ingest.crossorg import CrossOrgReport, PairResult
from accountant.ingest.sources import LICENCE, LICENCE_URL, RETRIEVED
from accountant.ingest.spend import MINOR_UNIT, LoadResult

WIDTH = 74
LABEL = 26


def _hundredths(value: int) -> str:
    """9000 -> '90.00'. Integers in, two decimal places out, no float."""
    return f"{value // 100}.{value % 100:02d}"


def _percent(value: int) -> str:
    return f"{_hundredths(value)}%"


def _signed_percent(value: int) -> str:
    """A gap. The sign is kept, because a negative gap is a real result."""
    sign = "-" if value < 0 else "+"
    return f"{sign}{_hundredths(abs(value))}%"


def _row(label: str, value: str) -> str:
    return f"  {label:<{LABEL}}{value}"


def render_load(result: LoadResult) -> str:
    """One department's load, ending in a newline."""
    lines: list[str] = [
        f"INGEST - {result.department}",
        "=" * WIDTH,
        "",
        "The source",
        _row("department", f"{result.department} ({result.code})"),
        _row("url", result.source_url),
        _row("retrieved", RETRIEVED.isoformat()),
        _row("licence", f"{LICENCE} - {LICENCE_URL}"),
        _row("encoding", result.encoding),
        "",
        "Rows",
        _row("read from the file", str(result.row_count)),
        _row("  loaded", str(result.loaded_count)),
        _row("  rejected", str(result.rejected_count)),
        _row("negative amounts kept", str(result.refund_count)),
        "",
        _row("statement", result.statement),
        "",
        "Columns resolved",
    ]
    for column in result.columns.resolved:
        lines.append(_row(f"  {column.field}", f"{column.header!r}"))

    lines.extend(["", "Rows not loaded"])
    if not result.rejected:
        lines.append(_row("  none", "every row in the file was read"))
    else:
        for reason, count in result.rejected_by_reason():
            lines.append(_row(f"  {reason}", str(count)))

    lines.extend(
        [
            "",
            "Money",
            _row("  published unit", "pounds sterling"),
            _row("  carried as", f"integer {MINOR_UNIT}"),
            "  Voucher.amount_paise is named for the Indian product. For this",
            f"  data it holds {MINOR_UNIT}. Nothing is converted and nothing",
            "  pretends pounds are rupees.",
            "",
            "Not published, so not invented",
            _row("  credit account", "no source"),
            _row("  VAT split", "no source"),
        ]
    )
    return "\n".join(lines) + "\n"


def _pair_line(pair: PairResult) -> str:
    return (
        f"  {pair.index_code:<7}-> {pair.test_code:<7}"
        f"within {_percent(pair.within.percent_hundredths):>8}"
        f"   cross {_percent(pair.cross.percent_hundredths):>8}"
        f"   gap {_signed_percent(pair.gap_hundredths):>9}"
    )


def render_cross(report: CrossOrgReport) -> str:
    """The cross-organisation comparison, ending in a newline."""
    lines: list[str] = [
        "CROSS-ORGANISATION GENERALISATION - accountant/ingest",
        "=" * WIDTH,
        "",
        "The question",
        "  Does a supplier-to-account mapping learned at one organisation",
        "  predict the account at a different organisation?",
        "",
        "How it was measured",
        "  Each department's own rows are split in published order: the earlier",
        "  half is history, the later half is predicted. One memory index is",
        "  built from department A's history. `within` is that index on A's own",
        "  later half. `cross` is the same index on B's later half. The gap is",
        "  within minus cross.",
        "",
        _row("departments", ", ".join(report.departments)),
        _row("pairs measured", str(len(report.pairs))),
        "",
        "Per pair",
    ]
    lines.extend(_pair_line(p) for p in report.pairs)

    lines.extend(
        [
            "",
            "Headline",
            _row("best cross-department", _percent(report.best_cross_hundredths)),
            _row("largest gap", _signed_percent(report.worst_gap_hundredths)),
            "",
            "Counts behind the pairs",
        ]
    )
    for pair in report.pairs:
        lines.append(
            _row(
                f"  {pair.index_code} -> {pair.test_code}",
                f"{pair.cross.correct} correct, {pair.cross.matched} matched, "
                f"{pair.cross.conflicted} conflicted, {pair.cross.no_match} "
                f"no match, of {pair.cross.tested}",
            )
        )

    return "\n".join(lines) + "\n"

"""Child 5, feeding child 8 - does an account mapping transfer between
organisations?

This is the question the whole design rests on, and it has exactly two answers:

    it transfers      one pooled model could serve every customer
    it does not       every customer is a permanent cold start, `memory/` is
                      the only thing that can work, and any pooled model is
                      wasted effort

Nobody has to guess. UK central government publishes many organisations in one
format, each with its own account vocabulary, for free. So the question is
answered by measurement.

THE MEASUREMENT
---------------
For an ordered pair (A, B):

    split each department's own rows in published order - earlier half is
    history, later half is what gets predicted
    build one memory index from A's history
    within  = that index predicting A's own later half
    cross   = the same index predicting B's later half
    gap     = within - cross, one number per pair

The index is the real one, `accountant/memory/index.py`, unmodified. It maps a
supplier to the account that supplier was posted to, and returns MATCH,
CONFLICTED or NO_MATCH. Only a MATCH whose single account equals the published
account counts as correct. CONFLICTED and NO_MATCH are counted and reported
separately, because "it did not answer" and "it answered wrongly" are different
failures and averaging them together hides which one happened.

The denominator is every entry in the test half, including the ones the index
declined to answer. Scoring only the entries it was confident about would
measure the index's confidence rather than its usefulness.

Percentages are integers in hundredths of a percent, the same convention
`accountant/score` uses, so no float touches a reported number.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from accountant.ingest.spend import LoadResult, split_point, vouchers
from accountant.memory.index import MemoryIndex
from accountant.schema import MatchStatus, Voucher

# 9000 is 90.00%. Same scale as accountant/score/harness.py.
PERCENT_SCALE = 10_000

# The frozen plan asks for at least three department pairs. Fewer is not a
# smaller result, it is a different claim, so it is refused.
MIN_PAIRS = 3


def _hundredths(numerator: int, denominator: int) -> int:
    """numerator / denominator as hundredths of a percent, rounded half up."""
    return (numerator * PERCENT_SCALE * 2 + denominator) // (denominator * 2)


@dataclass(frozen=True)
class Split:
    """One department cut into the history an index learns from and the
    entries that index is then asked to predict."""

    code: str
    department: str
    history: tuple[Voucher, ...]
    entries: tuple[Voucher, ...]

    def __post_init__(self) -> None:
        if not self.history or not self.entries:
            raise ValueError(
                f"{self.code} has {len(self.history)} history and "
                f"{len(self.entries)} entries; a department needs both sides to "
                f"take part in a comparison"
            )


def split(result: LoadResult) -> Split:
    """Cut one loaded department in half, in published order."""
    stream = vouchers(result)
    cut = split_point(len(stream))
    return Split(
        code=result.code,
        department=result.department,
        history=stream[:cut],
        entries=stream[cut:],
    )


@dataclass(frozen=True)
class Accuracy:
    """One index measured against one set of entries.

    `correct + conflicted + no_match` does not have to equal `tested`: a MATCH
    that named the wrong account is neither correct nor a non-answer, and it is
    the most interesting case, so `matched` is reported too.
    """

    tested: int
    matched: int
    correct: int
    conflicted: int
    no_match: int

    def __post_init__(self) -> None:
        if self.tested < 1:
            raise ValueError("nothing was tested, so there is no accuracy to report")

    @property
    def percent_hundredths(self) -> int:
        return _hundredths(self.correct, self.tested)


@dataclass(frozen=True)
class PairResult:
    """Index built on A, measured on A's own held-out half and on B's."""

    index_code: str
    test_code: str
    within: Accuracy
    cross: Accuracy

    @property
    def gap_hundredths(self) -> int:
        """The single number per pair: how much accuracy the move to B cost."""
        return self.within.percent_hundredths - self.cross.percent_hundredths


@dataclass(frozen=True)
class CrossOrgReport:
    """Every ordered pair, and the counts behind them."""

    departments: tuple[str, ...]
    pairs: tuple[PairResult, ...]

    def __post_init__(self) -> None:
        if len(self.pairs) < MIN_PAIRS:
            raise ValueError(
                f"a cross-organisation claim needs at least {MIN_PAIRS} department "
                f"pairs; this one has {len(self.pairs)}"
            )

    @property
    def worst_gap_hundredths(self) -> int:
        return max(p.gap_hundredths for p in self.pairs)

    @property
    def best_cross_hundredths(self) -> int:
        """The most any index managed on another department. The headline."""
        return max(p.cross.percent_hundredths for p in self.pairs)


def measure(index: MemoryIndex, entries: Sequence[Voucher]) -> Accuracy:
    """Ask one index for every entry's account and count what came back."""
    matched = correct = conflicted = no_match = 0
    for entry in entries:
        found = index.lookup(entry.party)
        if found.status is MatchStatus.MATCH:
            matched += 1
            if found.accounts[0] == entry.debit_account:
                correct += 1
        elif found.status is MatchStatus.CONFLICTED:
            conflicted += 1
        else:
            no_match += 1
    return Accuracy(
        tested=len(entries),
        matched=matched,
        correct=correct,
        conflicted=conflicted,
        no_match=no_match,
    )


def compare(results: Sequence[LoadResult]) -> CrossOrgReport:
    """Every ordered pair of departments, measured. Same input, same numbers."""
    codes = [r.code for r in results]
    repeated = sorted({c for c in codes if codes.count(c) > 1})
    if repeated:
        raise ValueError(
            f"the same department was supplied twice: {', '.join(repeated)}"
        )

    splits = [split(r) for r in results]
    pairs: list[PairResult] = []
    for a in splits:
        index = MemoryIndex.from_vouchers(a.history)
        within = measure(index, a.entries)
        for b in splits:
            if b.code == a.code:
                continue
            pairs.append(
                PairResult(
                    index_code=a.code,
                    test_code=b.code,
                    within=within,
                    cross=measure(index, b.entries),
                )
            )

    return CrossOrgReport(departments=tuple(s.code for s in splits), pairs=tuple(pairs))

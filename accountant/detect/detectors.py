"""Child 3 - silent-error detectors.

Each compares a proposed voucher against this company's own history. No model.
Every flag names its evidence, because a flag nobody can act on quickly inflates
the dismissal cost D and breaks N1.

WHAT A DETECTOR FIRING MEANS
----------------------------
A detector firing means **something here requires attention**. It never means
the entry is wrong, and it is never permission to post one.

    fired      -> an answerable Problem -> a plain-language question -> the
                  entry RE-ENTERS the decision order from the top
    not fired  -> no evidence either way. Silence is not approval.

The Valid / Unclear / Not-valid rule is unchanged and is decided in
`accountant/decide.py`, never here:

    Not valid -> notify, do not post
    Unclear   -> ask, record the answer, re-evaluate from the top
    Valid     -> post

An unusual but perfectly valid entry must therefore be **asked about, not
rejected**. `accountant/problems.py` turns every flag from a detector it knows
into an *answerable* problem, so a flag can only ever produce a question. A
flag from a detector it does not know becomes a visible unanswerable problem
rather than a question this code invented - a refusal to guess, not a verdict
on the entry.

WHAT EACH DETECTOR READS, AND THE THRESHOLD IT NEEDS
----------------------------------------------------
| detector       | reads          | the bar it must clear                     |
|----------------|----------------|-------------------------------------------|
| `vendor_switch`| debit_account  | the party has a *practice*, not one entry |
| `first_use`    | debit_account  | none available - see WITHDRAWN            |
| `magnitude`    | amount_paise   | a *range*, and a wide margin over its top |
| `gst_anomaly`  | gst_paise      | none needed - the account carried no tax  |

Every threshold below was chosen by `accountant/score/calibration.py` on one set
of clean books and then measured on a separate held-out set. None of them is a
number somebody liked the look of, and each carries its measurement in
`accountant/score/calibration.py`.

Two of them were absent before, and their absence was the whole of N1:

    vendor_switch  said "consistently posted to X" after ONE prior posting.
                   One posting is not a practice.
    magnitude      said "far outside the range" for one paise over the top of
                   a sample that could be a single entry. One point is not a
                   range, and one paise is not far.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from accountant.memory.index import MemoryIndex
from accountant.schema import Flag, Voucher

# A detector takes the proposed voucher, the company's history and the memory
# index, and returns flags. The uniform shape is what lets run() iterate them.
Detector = Callable[[Voucher, Sequence[Voucher], MemoryIndex], list[Flag]]

# --------------------------------------------------------------------------
# Calibrated thresholds. Chosen on the calibration books, measured on the
# held-out books. See accountant/score/calibration.py for both numbers.
# --------------------------------------------------------------------------

# How many times a party must already have been posted to one account before
# that account counts as this party's practice.
MIN_POSTINGS_FOR_A_PRACTICE = 2

# How many prior entries an account needs before its amounts count as a range.
# Two is the definition of a range, not a tuned number: one point has no top.
MIN_OBSERVATIONS_FOR_A_RANGE = 2

# How far past an account's own historical maximum an amount must go before
# "far outside the range" is true. 300 means more than three times the top.
MAGNITUDE_OVER_PERCENT = 300

PERCENT = 100


def vendor_switch(
    proposed: Voucher,
    _history: Sequence[Voucher],
    index: MemoryIndex,
    *,
    min_postings: int = MIN_POSTINGS_FOR_A_PRACTICE,
) -> list[Flag]:
    """The vendor is consistently posted to X, this one goes to Y.

    "Consistently" is the load-bearing word, and it is now enforced:
    `min_postings` prior postings to the same account, or this detector has
    seen a coincidence rather than a practice and says nothing.
    """
    seen = index.lookup(proposed.party)
    if seen.status.value != "match":
        return []
    usual = seen.accounts[0]
    if proposed.debit_account == usual:
        return []
    n = index.times_posted(proposed.party, usual)
    if n < min_postings:
        return []
    return [
        Flag(
            voucher_id=proposed.id,
            detector="vendor_switch",
            severity=3,
            reason=(
                f"{proposed.party} posted to {usual} {n} times; "
                f"this one goes to {proposed.debit_account}"
            ),
        )
    ]


def first_use(
    proposed: Voucher, history: Sequence[Voucher], index: MemoryIndex
) -> list[Flag]:
    """This account has never been used before in this company.

    Withdrawn from `ACTIVE_DETECTORS`. It is kept, tested and importable, and
    it still fires exactly as it always did - see WITHDRAWN for the measured
    reason it no longer runs by default.
    """
    if proposed.debit_account in index.accounts_ever_used():
        return []
    return [
        Flag(
            voucher_id=proposed.id,
            detector="first_use",
            severity=2,
            reason=(
                f"{proposed.debit_account} has never been used in this company "
                f"across {len(history)} posted vouchers"
            ),
        )
    ]


def magnitude(
    proposed: Voucher,
    history: Sequence[Voucher],
    _index: MemoryIndex,
    *,
    min_observations: int = MIN_OBSERVATIONS_FOR_A_RANGE,
    over_percent: int = MAGNITUDE_OVER_PERCENT,
) -> list[Flag]:
    """The amount is far outside this account's own historical range.

    Bound is the account's own observed maximum. No invented multiplier: the
    margin over that maximum is calibrated, and the account must have enough
    entries behind it for "maximum" to mean anything at all.
    """
    seen = [
        v.amount_paise for v in history if v.debit_account == proposed.debit_account
    ]
    if len(seen) < min_observations:
        return []
    high = max(seen)
    if proposed.amount_paise * PERCENT <= high * over_percent:
        return []
    return [
        Flag(
            voucher_id=proposed.id,
            detector="magnitude",
            severity=2,
            reason=(
                f"{proposed.amount_paise} paise to {proposed.debit_account}; "
                f"highest ever posted to it is {high} paise across {len(seen)} "
                f"entries, and this is over {over_percent} percent of that"
            ),
        )
    ]


def gst_anomaly(
    proposed: Voucher, history: Sequence[Voucher], _index: MemoryIndex
) -> list[Flag]:
    """GST claimed on an account that has never carried GST."""
    if not proposed.gst_paise:
        return []
    same = [v for v in history if v.debit_account == proposed.debit_account]
    if not same:
        return []
    if any(v.gst_paise for v in same):
        return []
    return [
        Flag(
            voucher_id=proposed.id,
            detector="gst_anomaly",
            severity=3,
            reason=(
                f"GST of {proposed.gst_paise} paise claimed on "
                f"{proposed.debit_account}, which has never carried GST "
                f"across {len(same)} entries"
            ),
        )
    ]


ALL_DETECTORS: tuple[Detector, ...] = (vendor_switch, first_use, magnitude, gst_anomaly)
SLICE_4_DETECTORS: tuple[Detector, ...] = (vendor_switch,)


def vendor_switch_at(min_postings: int) -> Detector:
    """`vendor_switch` at one point on its grid, still answering to its name.

    `accountant/score/calibration.py` compares several settings against each
    other. Every report names a detector by `__name__`, so the configured copy
    keeps the original name.
    """

    def configured(
        proposed: Voucher, history: Sequence[Voucher], index: MemoryIndex
    ) -> list[Flag]:
        return vendor_switch(proposed, history, index, min_postings=min_postings)

    configured.__name__ = vendor_switch.__name__
    return configured


def magnitude_at(min_observations: int, over_percent: int) -> Detector:
    """`magnitude` at one point on its grid, still answering to its name."""

    def configured(
        proposed: Voucher, history: Sequence[Voucher], index: MemoryIndex
    ) -> list[Flag]:
        return magnitude(
            proposed,
            history,
            index,
            min_observations=min_observations,
            over_percent=over_percent,
        )

    configured.__name__ = magnitude.__name__
    return configured


def name_of(detector: Detector) -> str:
    """A detector's own name, whether it is a function or a configured copy."""
    return str(getattr(detector, "__name__", detector))


# --------------------------------------------------------------------------
# Which detectors run, and which do not
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Withdrawn:
    """A detector that is not in `ACTIVE_DETECTORS`, and the reason.

    Withdrawn is not deleted and is not hidden. The detector stays in
    `ALL_DETECTORS`, stays in the taxonomy coverage table, and every report
    prints this record next to its measured numbers.
    """

    detector: str
    because: str


WITHDRAWN: tuple[Withdrawn, ...] = (
    Withdrawn(
        detector="first_use",
        because=(
            "no threshold exists that separates an account this company has "
            "genuinely never used from one this company simply has not loaded "
            "yet. Measured on real published ledgers it fires on roughly three "
            "clean entries in ten, and it has no knob to turn. It is off by "
            "default and its numbers are reported, not hidden."
        ),
    ),
)

# The detectors that run by default: every detector in ALL_DETECTORS that
# calibration kept, in ALL_DETECTORS order. Withdrawing one is a change to
# WITHDRAWN, so nothing can leave silently.
ACTIVE_DETECTORS: tuple[Detector, ...] = tuple(
    d for d in ALL_DETECTORS if name_of(d) not in {w.detector for w in WITHDRAWN}
)


# --------------------------------------------------------------------------
# Duplicate alerts, and distinct underlying problems
# --------------------------------------------------------------------------

# What each detector is really asking about. Two detectors that read the same
# thing about one entry are two views of ONE problem, not two problems: an
# entry sent to an account this party never uses, on an account nobody has
# used, is one wrong account and one question.
CONCERNS: dict[str, str] = {
    "vendor_switch": "which account this entry was posted to",
    "first_use": "which account this entry was posted to",
    "magnitude": "how large this entry is",
    "gst_anomaly": "the tax on this entry",
}


def concern_of(detector: str) -> str:
    """The underlying problem a detector reports.

    A detector this module does not know gets a concern of its own, so an
    unrecognised flag is never merged into - or silenced by - another one.
    """
    known = CONCERNS.get(detector)
    if known is None:
        return f"whatever detector {detector} reads"
    return known


@dataclass(frozen=True)
class Alert:
    """One underlying problem, and every flag that reported it.

    `lead` is the highest-ranked flag for the concern. `corroborating` are the
    others: the same problem seen from another angle, kept as evidence and
    never thrown away.
    """

    concern: str
    lead: Flag
    corroborating: tuple[Flag, ...] = ()

    @property
    def flags(self) -> tuple[Flag, ...]:
        return (self.lead, *self.corroborating)

    @property
    def duplicates(self) -> int:
        return len(self.corroborating)


def group_by_concern(flags: Sequence[Flag]) -> tuple[Alert, ...]:
    """Ranked flags in, one Alert per distinct underlying problem out.

    Order is the order the flags arrived in, which `run` has already ranked, so
    the first flag for a concern leads it.
    """
    order: list[str] = []
    grouped: dict[str, list[Flag]] = {}
    for f in flags:
        concern = concern_of(f.detector)
        if concern not in grouped:
            grouped[concern] = []
            order.append(concern)
        grouped[concern].append(f)
    return tuple(
        Alert(concern=c, lead=grouped[c][0], corroborating=tuple(grouped[c][1:]))
        for c in order
    )


def _one_flag_per_concern(alert: Alert) -> Flag:
    """The lead flag, carrying the corroborating evidence inside its reason.

    This is the whole of duplicate suppression: one alert per problem, and not
    one word of evidence dropped.
    """
    if not alert.corroborating:
        return alert.lead
    also = "; also ".join(f.reason for f in alert.corroborating)
    return replace(alert.lead, reason=f"{alert.lead.reason}; also {also}")


def run(
    proposed: Voucher,
    history: Sequence[Voucher],
    index: MemoryIndex,
    detectors: Sequence[Detector] = SLICE_4_DETECTORS,
    cap: int | None = None,
    *,
    dedupe: bool = True,
) -> tuple[list[Flag], int]:
    """Run detectors, rank by severity, suppress duplicates, apply the cap.

    Returns (flags, dropped_count). Overflow is reported as a count, never
    silently discarded.

    `dedupe` collapses several flags about ONE underlying problem into one
    alert whose reason carries every flag's evidence. It removes duplicate
    alerts, never findings: pass `dedupe=False` for the raw list, which is what
    the scoring harness counts duplicates from.
    """
    flags: list[Flag] = []
    for d in detectors:
        flags.extend(d(proposed, history, index))

    flags.sort(key=lambda f: (-f.severity, f.detector, f.voucher_id))

    if dedupe:
        flags = [_one_flag_per_concern(a) for a in group_by_concern(flags)]

    if cap is None or len(flags) <= cap:
        return flags, 0
    return flags[:cap], len(flags) - cap

"""Child 3 — silent-error detectors.

Each compares a proposed voucher against this company's own history. No model.
Every flag names its evidence, because a flag nobody can act on quickly inflates
the dismissal cost D and breaks N1.
"""

from __future__ import annotations

from collections.abc import Sequence

from accountant.memory.index import MemoryIndex, normalise_vendor
from accountant.schema import Flag, Voucher

# Slice 4 builds only vendor_switch. The rest are defined here so the shapes are
# settled, and are wired in during Slice 6.


def vendor_switch(
    proposed: Voucher, history: Sequence[Voucher], index: MemoryIndex
) -> list[Flag]:
    """The vendor is consistently posted to X, this one goes to Y."""
    seen = index.lookup(proposed.party)
    if seen.status.value != "match":
        return []
    usual = seen.accounts[0]
    if proposed.debit_account == usual:
        return []
    n = index.times_posted(proposed.party, usual)
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
    """This account has never been used before in this company."""
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
    proposed: Voucher, history: Sequence[Voucher], index: MemoryIndex
) -> list[Flag]:
    """The amount is far outside this account's own historical range.

    Bound is the account's own observed maximum. No invented multiplier.
    """
    seen = [
        v.amount_paise for v in history if v.debit_account == proposed.debit_account
    ]
    if not seen:
        return []
    high = max(seen)
    if proposed.amount_paise <= high:
        return []
    return [
        Flag(
            voucher_id=proposed.id,
            detector="magnitude",
            severity=2,
            reason=(
                f"{proposed.amount_paise} paise to {proposed.debit_account}; "
                f"highest ever posted to it is {high} paise across {len(seen)} entries"
            ),
        )
    ]


def gst_anomaly(
    proposed: Voucher, history: Sequence[Voucher], index: MemoryIndex
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


ALL_DETECTORS = (vendor_switch, first_use, magnitude, gst_anomaly)
SLICE_4_DETECTORS = (vendor_switch,)


def run(
    proposed: Voucher,
    history: Sequence[Voucher],
    index: MemoryIndex,
    detectors=SLICE_4_DETECTORS,
    cap: int | None = None,
) -> tuple[list[Flag], int]:
    """Run detectors, rank by severity, apply the per-batch cap.

    Returns (flags, dropped_count). Overflow is reported as a count, never
    silently discarded.
    """
    flags: list[Flag] = []
    for d in detectors:
        flags.extend(d(proposed, history, index))

    flags.sort(key=lambda f: (-f.severity, f.detector, f.voucher_id))

    if cap is None or len(flags) <= cap:
        return flags, 0
    return flags[:cap], len(flags) - cap

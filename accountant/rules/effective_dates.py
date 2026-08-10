"""When a rule applies, and the honest edge of what the corpus checked.

A rate is a fact about a date, never a fact on its own. Three things bound one:

    effective_from                 the day the notification says it starts
    effective_to                   the day it stops, when a source says so
    amendments_checked_through     the last day for which somebody actually
                                   looked at the amendment chain

The third one is the interesting one, and it exists because of the rule the
owner wrote down: **never silently use a stale rate.** A notification that
records no end date has not promised to be current; it has simply not been
amended in the copy you are holding. Treating `effective_to = None` as "valid
forever" is exactly how a 2017 rate lands on a 2026 invoice, and the entry looks
fine.

So `None` here means UNKNOWN, not FOREVER, and the corpus states separately how
far it checked. Past that line a lookup refuses. Refusing is visible; a wrong
statutory entry is not.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import StrEnum


class WindowVerdict(StrEnum):
    IN_FORCE = "in_force"
    NOT_YET_IN_FORCE = "not_yet_in_force"
    ENDED = "ended"
    BEYOND_AMENDMENT_CHECK = "beyond_amendment_check"
    NO_EFFECTIVE_DATE = "no_effective_date"


@dataclass(frozen=True)
class EffectiveWindow:
    """`effective_from` is required for a rule to load; the loader enforces it.

    It is `| None` here for the same reason `Source.url` may be blank: the
    object that violates the rule has to be constructible, or the guard cannot
    be tested.
    """

    effective_from: datetime.date | None
    effective_to: datetime.date | None = None
    amendments_checked_through: datetime.date | None = None
    amendment_check_note: str = ""

    def verdict(self, on: datetime.date) -> WindowVerdict:
        """The order matters and it is a safety order, not a reporting order.

        `NOT_YET_IN_FORCE` and `ENDED` are checked before the amendment edge so
        a date outside the notification's own window is named for what it is,
        rather than being reported as an unchecked amendment chain.
        """
        if self.effective_from is None:
            return WindowVerdict.NO_EFFECTIVE_DATE
        if on < self.effective_from:
            return WindowVerdict.NOT_YET_IN_FORCE
        if self.effective_to is not None and on > self.effective_to:
            return WindowVerdict.ENDED
        if (
            self.amendments_checked_through is not None
            and on > self.amendments_checked_through
        ):
            return WindowVerdict.BEYOND_AMENDMENT_CHECK
        return WindowVerdict.IN_FORCE

    def explain(self, on: datetime.date) -> str:
        """One sentence a person can act on. Never a bare enum name."""
        verdict = self.verdict(on)
        if verdict is WindowVerdict.NO_EFFECTIVE_DATE:
            return "this rule carries no effective date, so it cannot be applied"
        if verdict is WindowVerdict.NOT_YET_IN_FORCE:
            return (
                f"this rule takes effect on {self.effective_from}, "
                f"and the supply is dated {on}"
            )
        if verdict is WindowVerdict.ENDED:
            return (
                f"this rule ended on {self.effective_to}, and the supply is dated {on}"
            )
        if verdict is WindowVerdict.BEYOND_AMENDMENT_CHECK:
            return (
                f"the amendment chain behind this rule was only checked to "
                f"{self.amendments_checked_through}, and the supply is dated "
                f"{on}, so the rate may be stale and is not used"
            )
        return f"in force on {on}"

"""Shared types. Defined once, consumed by every component.

Money is integer paise everywhere. A float in a money field is a correctness bug,
not a style choice.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class Outcome(StrEnum):
    """The three outcomes an entry can have. Exhaustive and mutually exclusive."""

    NOT_VALID = "not_valid"
    UNCLEAR = "unclear"
    VALID = "valid"


class MatchStatus(StrEnum):
    """What the memory index found. Never a guess."""

    MATCH = "match"
    CONFLICTED = "conflicted"
    NO_MATCH = "no_match"


@dataclass(frozen=True)
class MatchResult:
    """Memory index lookup result.

    `accounts` holds one entry for MATCH, two or more for CONFLICTED, none for
    NO_MATCH.
    """

    status: MatchStatus
    vendor_key: str
    accounts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        rules: dict[MatchStatus, Callable[[int], bool]] = {
            MatchStatus.MATCH: lambda n: n == 1,
            MatchStatus.CONFLICTED: lambda n: n >= 2,
            MatchStatus.NO_MATCH: lambda n: n == 0,
        }
        expected = rules[self.status]
        if not expected(len(self.accounts)):
            raise ValueError(
                f"{self.status.value} is inconsistent with "
                f"{len(self.accounts)} account(s)"
            )


@dataclass(frozen=True)
class CheckResult:
    """One deterministic validation. A boolean function over a record.

    "Looks right" is not a check. Every check names itself and, on failure, says
    why in one line.
    """

    name: str
    passed: bool
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.passed and not self.detail:
            raise ValueError(f"failed check {self.name!r} must state a reason")


@dataclass(frozen=True)
class Flag:
    """A detector firing. The reason must name the evidence.

    A flag without a stated reason cannot be dismissed quickly, which inflates D
    and breaks N1.
    """

    voucher_id: str
    detector: str
    severity: int
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError(f"detector {self.detector!r} fired without a reason")


@dataclass(frozen=True)
class LineItem:
    description: str
    amount_paise: int


@dataclass(frozen=True)
class Voucher:
    id: str
    date: datetime.date
    party: str
    narration: str
    debit_account: str
    credit_account: str
    amount_paise: int
    gst_paise: int | None = None
    tally_id: str | None = None
    provenance: dict[str, str] | None = None


@dataclass(frozen=True)
class Decision:
    """The result of applying the decision order to one entry.

    `post` is the only thing the Tally write path is allowed to read.

    `operation_id` added 2026-08-09, G5.1. Phase 5 requires one identity carried
    by all five of the draft, the decision, the Tally narration, the action log
    and the reversal request. Four carried it; this one did not, so the artefact
    that AUTHORISES a write could not be tied to the write it authorised.

    It defaults to empty rather than being required positionally, because
    `decide_problems` is also called by the scoring harness and by unit tests
    that reach no write path and have no operation to name. The requirement is
    enforced where it matters instead: an unidentified decision is not
    `post`-able, and `pipeline.post` refuses both an empty id and one belonging
    to a different operation. A decision that authorises nothing cannot leak.
    """

    outcome: Outcome
    reason: str
    question_options: tuple[str, ...] = ()
    operation_id: str = ""

    @property
    def post(self) -> bool:
        """Valid AND identified. Both, because the write path reads only this.

        The identity requirement lives here as well as in `pipeline.post` so a
        second reader added later inherits it rather than having to remember
        it. An anonymous approval is not an approval.
        """
        return self.outcome is Outcome.VALID and bool(self.operation_id)


@dataclass(frozen=True)
class ActionLog:
    """One decision, recorded durably, with the reason it went that way.

    Declared here from the start and NEVER CONSTRUCTED until 2026-08-09. What
    the product used instead was a forty-row in-memory list of
    `(kind, message)` pairs in the web app: no timestamp, no way to tie a row
    to its voucher, a reason only on refusals, the outcome discarded when
    rendering, and everything lost on restart.

    `reason` is required on EVERY path. "Why did you refuse" is the obvious
    question; "why did you POST" is the one asked six months later by somebody
    looking at a voucher in their books.

    `backend` is recorded because a row that cannot say which Tally it came
    from cannot be used as evidence about any of them.
    """

    ts: datetime.datetime
    action: str
    company_key: str
    outcome: str
    reason: str
    run_id: str
    backend: str
    operation_id: str = ""
    voucher_id: str = ""
    vendor_id: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError(
                f"action {self.action!r} for {self.company_key!r} must state "
                "WHY it happened; a log row without a reason is a timestamp"
            )

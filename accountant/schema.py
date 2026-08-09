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

    `question_problem_id` added 2026-08-10, the same precedent applied to the
    other artefact that authorises a ledger write: the ANSWER.

    A decision carried the exact set of answers it was offering
    (`question_options`) and no way to say WHICH QUESTION it was offering them
    for. The problem id is what picks the ledger leg an answer lands on
    (`pipeline.answer`), and the web handler took it straight off the form. So
    an answer could be offered for one question and filed against another.
    Measured over HTTP on 2026-08-10, demo company, unseen vendor:

        the page asks    which_account, offering
                         Purchases, Repairs & Maintenance, Sundry Expenses,
                         Printing & Stationery, Rent, Electricity Charges
        the POST says    problem=funding_is_named  value=Purchases
        the reply is     200 OK
        the draft holds  credit_account='Purchases'

    "Purchases" is an offered answer, so the value guard let it through, and
    `funding_is_named` sent it to the funding leg. The books would then say the
    money came OUT OF an expense account. Nothing about that request was ever
    offered by the system.
    """

    outcome: Outcome
    reason: str
    question_options: tuple[str, ...] = ()
    operation_id: str = ""
    question_problem_id: str = ""

    @property
    def post(self) -> bool:
        """Valid AND identified. Both, because the write path reads only this.

        The identity requirement lives here as well as in `pipeline.post` so a
        second reader added later inherits it rather than having to remember
        it. An anonymous approval is not an approval.
        """
        return self.outcome is Outcome.VALID and bool(self.operation_id)

    def refuse_answer(self, *, operation_id: str, problem_id: str, value: str) -> str:
        """Why this is not an answer to THIS question. Empty string means it is.

        Three bindings, all checked before anything touches a ledger leg:

            the entry     `operation_id` — the decision doing the authorising
                          must belong to the entry the answer is applied to
            the question  `question_problem_id` — the id the page rendered into
                          the form, which is also the id `pipeline.answer` reads
                          to choose WHICH LEG. `problems.py` forces it equal to
                          `Problem.id`, the id the decision order filters on, so
                          binding to it binds to the problem as well
            the answer    `question_options` — the exact set that question
                          offered, and only that question

        Returning a SENTENCE rather than a boolean is deliberate. A refusal a
        person cannot read is the defect this codebase keeps rediscovering:
        failing safely and failing legibly are two properties, and only the
        first survives a boolean.

        It lives on `Decision` rather than in the handler for the same reason
        `post` does — a second reader added later inherits the rule instead of
        having to remember it.

        THE ORDER IS A REPORTING ORDER, NOT A SAFETY ORDER. Every binding is
        checked before the caller may touch anything, so which one reports
        first changes only the sentence. The question comes before the answer
        set because a set of allowed answers means nothing without the question
        it was offered for — reading those two apart is exactly how the defect
        above survived a guard that was already holding one of them.
        """
        if not self.question_problem_id:
            return "this entry is not asking anything, so there is nothing to answer"
        if operation_id != self.operation_id:
            return (
                f"this answer was made for entry "
                f"{operation_id or '(unnamed)'} and this entry is "
                f"{self.operation_id or '(unnamed)'}"
            )
        if not problem_id:
            return "this answer does not say which question it is answering"
        if problem_id != self.question_problem_id:
            return (
                f"{problem_id!r} was not one of the questions this entry is "
                f"asking — it is asking {self.question_problem_id!r}"
            )
        if value not in self.question_options:
            return f"{value!r} was not one of the answers offered for this question"
        return ""


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

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

    @property
    def needs_tax_lines(self) -> bool:
        """This entry carries tax, so posting it faithfully needs tax lines.

        ONE EXPRESSION, TWO READERS, ADDED 2026-08-10. `checks.tax_lines_can_be_posted`
        asks it before the decision; `tallyio.real.check_writable` asks it at the
        wire. Until now only the second one existed, the two halves were free to
        disagree, and they did: the application called a GST bill VALID and the
        connector then refused the very write that VALID had promised. The person
        saw a breakage page for an ordinary bill with tax on it.

        It lives on `Voucher`, in `accountant/schema.py`, because that is the one
        module both sides already import — `accountant/tallyio/` may not import
        the product layer (correction C3, `tests/test_reverse_all_cli.py`
        `test_only_the_command_imports_above_the_connector_boundary`) and
        `accountant/checks.py` must not import the connector. Mirroring the
        condition in two files is exactly what produced the drift, so it is
        written once.

        The day tax lines can actually be built, this one line changes and both
        sides move together. There is no arrangement in which the check passes
        and the connector still refuses.

        A BILL WITH ZERO TAX DOES NOT CARRY TAX. Corrected 2026-08-17. This read
        `gst_paise is not None`, so a bill a reader had looked at and found no
        GST on was refused for "carrying GST of 0 paise" - and the refusal named
        a tax line that would have been worth nothing to build. There is no
        CGST/SGST/IGST line to write for zero, so there is nothing here the
        connector cannot do.

        THE THREE STATES WERE ALREADY IN THE DATA, and only this line collapsed
        two of them. `tax_paise` is `None` when nobody read the field and an int
        when somebody did, and `per_field_source` says which - `not_found:` for
        the first. So *unread* and *read, and it is zero* were always
        distinguishable; what was missing was a reader of that distinction.

        NOTHING IS COERCED AND NOTHING IS DERIVED. `None` still means nobody
        looked, and it still blocks - one layer up, where
        `conservation.net_plus_tax_equals_gross` returns INDETERMINATE, exactly
        as its own docstring demands: "Zero tax is a fact; an unread tax field is
        not." Measured on that law 2026-08-17: `(None, 0, 420000)` is
        INDETERMINATE on the unread NET, `(420000, None, 420000)` is
        INDETERMINATE on the unread TAX, and only `(420000, 0, 420000)` passes.
        A positive figure is untouched and still refused here.

        `!= 0`, NOT `> 0`, and the difference is a hole this nearly had.
        `tests/test_gst_safety_sweep.py` sweeps `(0, 1, -1, 64068)`, and `> 0`
        would have called a voucher carrying MINUS one paise of GST postable - a
        figure no reader should ever produce, which is exactly why it must not
        be waved through. Only an exact zero is "there is no tax here".
        """
        return self.gst_paise is not None and self.gst_paise != 0


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


#: What an unrecorded field reads back as. NEVER a guess, and never silently
#: empty — an explicit "we did not record this" is evidence; a plausible
#: default is a lie. Rows written before `actor` and `previous_state` existed
#: hold SQL NULL in those columns and read back as this, exactly as
#: `raw_subject` reads back as INCOMPLETE for the same reason.
NOT_RECORDED = "NOT_RECORDED"

#: What `ExtractedRecord.with_answer` stamps when a PERSON supplied the value.
#: Named rather than typed inline so the string cannot drift between the place
#: that writes it and any place that reads it. It is deliberately NOT in
#: `extract/adapter.py::ENTITLED_TO_EXACT`: that list grants the right to become
#: a vendor identity with no question asked, and a person's typed answer has not
#: earned it.
#:
#: IT LIVES HERE, NOT IN `extract/adapter.py`, SINCE 2026-08-17. It is a string
#: two sides compare — `adapter.py` writes it, `uncertainty.py` reads it — and
#: nothing about it belongs behind the reader boundary. Keeping it in the
#: extraction package meant `uncertainty.py` took a name from
#: `accountant.extract.*` that the contract in
#: `tests/test_adapter_contract.py::CONTRACT` does not name, so the seam said
#: the core depended on the extraction package for something a swap could
#: change. It cannot: this is one word, and both sides only ever compare it to
#: itself.
HUMAN_ANSWER = "human_answer"


class Actor(StrEnum):
    """Who did it, to the only resolution this system honestly has.

    Owner decision Q8 = A, 2026-08-10. Exactly two values and no third:

        accountant_dad   the system did it of its own accord
        operator         somebody answered it through the UI

        authenticated user identity = NOT_IMPLEMENTED
        actor provenance            = coarse-grained system/operator

    `operator` IS NOT AN AUTHENTICATED IDENTITY and must never be described as
    one. It says a human hand was on the control, not whose. There is no login,
    no session, no user table and no `dependencies` entry behind it, because the
    same decision that asked for these labels forbade adding an authentication
    dependency to get them.

    Wanting to know WHICH person is a different requirement with a different
    cost, and it is not open for an agent to decide:

        OWNER_DECISION_REQUIRED: approve an authenticated identity subsystem

    That is H-05. Until it is answered, a row saying `operator` means one thing
    only — a person, unnamed, was asked and answered.
    """

    ACCOUNTANT_DAD = "accountant_dad"
    OPERATOR = "operator"


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

    The last three fields arrived with Phase 8 PR-5 (owner decision Q8 = A) and
    are additive: every one defaults, so no existing caller changed. `actor` and
    `previous_state` default to `NOT_RECORDED` rather than to `""`, because a
    row that does not carry them must SAY it does not carry them. An action with
    no state machine behind it — a post, a dismissal, a company mismatch — has
    no previous state to record and honestly reports `NOT_RECORDED`; only the
    reversal path, which does have one, is required to fill them in.
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
    #: `Actor` value, or `NOT_RECORDED`. Anything else is refused below.
    actor: str = NOT_RECORDED
    #: The state this thing was in BEFORE, so a history is reconstructable
    #: rather than a list of endings. `NOT_RECORDED` where there is no state
    #: machine, or on a row written before the column existed.
    previous_state: str = NOT_RECORDED
    #: Which batch a reversal event belongs to. Carried as its own field rather
    #: than parsed back out of `detail`, for the same reason `action` exists.
    batch_id: str = ""
    #: WHOSE books, and WHO was logged in. Added 2026-08-10 with tenancy.
    #:
    #: `company_key` above says which set of books; these two say which
    #: customer account owns them and which human was holding the session. One
    #: cloud server serves many customers, so a row that names only the company
    #: cannot answer "did somebody from another tenant do this", which is the
    #: first question asked after any cross-tenant scare.
    #:
    #: `NOT_RECORDED`, never blank, for the same reason as `actor`: a row
    #: written before tenancy existed, or by a path with no session behind it,
    #: must SAY it does not carry them rather than show an empty column.
    tenant_id: str = NOT_RECORDED
    user_id: str = NOT_RECORDED

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError(
                f"action {self.action!r} for {self.company_key!r} must state "
                "WHY it happened; a log row without a reason is a timestamp"
            )
        # Two labels or an explicit absence. A third value would be a claim
        # about identity this system cannot support, and an empty string would
        # be the absence pretending to be a value.
        if self.actor not in (NOT_RECORDED, *Actor):
            raise ValueError(
                f"action {self.action!r} for {self.company_key!r} names actor "
                f"{self.actor!r}; the only actors are "
                f"{', '.join(a.value for a in Actor)} or {NOT_RECORDED}. "
                "WHICH human is `user_id`; this field is which KIND of actor."
            )
        for name, value in (("tenant", self.tenant_id), ("user", self.user_id)):
            if not value.strip():
                raise ValueError(
                    f"action {self.action!r} for {self.company_key!r} has a "
                    f"blank {name} id; write {NOT_RECORDED} when there is none, "
                    "so the absence is a fact rather than an empty column"
                )
        if not self.previous_state.strip():
            raise ValueError(
                f"action {self.action!r} for {self.company_key!r} has a blank "
                f"previous state; write {NOT_RECORDED} when there is none, so "
                "the absence is a fact rather than an empty column"
            )

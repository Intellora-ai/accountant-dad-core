"""Where a guess becomes a write, or does not. The only module that may.

WHY THIS MODULE EXISTS
----------------------
`wall.py` answers *who* may build a `LedgerEntry` and names this module as the
only answer. It deliberately says nothing about *when* one should exist. That is
this file, and it is the last thing between a misread bill and a customer's
books.

Everything upstream produces evidence and nothing else: the reader produces an
`Observation`, `conservation.py` produces four verdicts, the caller looks up
whether the party is on the books and whether the period is open. None of it
decides. One module weighs it, and one module can be read end to end by a person
deciding whether to trust a refusal.

THE BANDS ARE OWNER-SET
------------------------
    post    confidence 0.95 or better AND every conservation law PASS AND the
            party known AND the period open AND no hard rule broken
    ask     confidence from 0.70 to just under 0.95, OR any conservation law
            FAIL at any confidence, OR something readable more than one way
    block   confidence under 0.70, OR any hard rule broken

Whether 0.95 and 0.70 are the RIGHT numbers is a question for a corpus run
against labelled invoices this repository does not have (`H-02`). They are not
retuned here to make anything pass; `ARCHITECTURE.md:616` forbids it, and the
reason is that moving a threshold moves the measurement rather than the product.

CERTAINTY NEVER OUTVOTES ARITHMETIC
-------------------------------------
A confidence score is a statement about how legible some pixels were. A
conservation law is a statement about whether numbers agree. They are not on the
same scale and they do not trade off, so a failing law sends a bill to ASK at
confidence 1.0 exactly as it does at 0.71. This is the single behaviour the cage
exists for: `confidence.py` cannot see a value the engine misread *confidently*,
and arithmetic can - but only if arithmetic is allowed to win.

FIVE HARD RULES, EACH OF WHICH ALWAYS BLOCKS
----------------------------------------------
    tax on the bill        owner decision Q3=D, tax posting is off. Writing the
                           bill without its tax line leaves a wrong statutory
                           entry in real books.
    a law INDETERMINATE    "could not check" is not "checked and fine". Three of
                           the four laws, always - see the paragraph below for
                           the fourth, which is not an exception to the rule but
                           a different kind of question.
    the period closed      the books for that date are shut.
    the party unknown      we never add a name to somebody's chart of accounts.
                           The person is asked; nothing is invented.
    the question budget    `questions.QUESTION_CAP` questions already asked. A
                           product that will not take no for an answer is worse
                           than one that hands the entry back.

THREE LAWS ARE ABOUT THE BILL. THE FOURTH IS ABOUT THE BOOKS.
--------------------------------------------------------------
`debits_equal_credits`, `lines_sum_to_total` and `net_plus_tax_equals_gross`
ask whether the numbers ON THE PIECE OF PAPER agree with each other. That
question has an answer before anything is written, so "I could not check the
arithmetic on this bill" is precisely the case the cage exists for, and it
blocks - at either moment, no exceptions.

`balance_delta_equals_entry` asks something else: did the BOOKS move by exactly
what we asked them to move by. It compares the ledger balance before the entry
with the balance after it, and before a write there is no after. So its honest
pre-write verdict is INDETERMINATE on every bill, every time.

Blocking on that made auto-post unreachable. The only route to a POST was to
hand the law a PREDICTED after-balance, which makes it compare a number against
itself - a check that cannot fail, wearing the face of a check that passed. That
is the same shape as the empty-set AST guard `wall.py` already shipped once.

So the caller states which moment it is (`Situation.moment`, no default, never
inferred), and pre-write an INDETERMINATE fourth law is expected rather than
blocking. Three things stay unchanged, and each is the reason the exemption is
safe: a pre-write FAIL still refuses the post, an INDETERMINATE fourth law
AFTER the write still blocks - there it means nobody read the register back -
and the three document laws still block on INDETERMINATE at both moments. The
document set is derived from `conservation.LAWS`, so a law added there blocks
by default rather than becoming exempt by being left out of a list.

THIS MODULE NEVER RAISES ON A SITUATION IT WAS GIVEN
------------------------------------------------------
`Situation` validates nothing on construction, on purpose, and `decide` treats
every malformed field as a reason to block. A float amount, a verdict it does
not recognise, a question count that is not a number - each of them is refused
in one plain sentence rather than becoming a traceback.

That direction is measured, not preferred. This repository already recorded what
the other one costs: an ordinary bill reached a connector that refused it, the
exception propagated, and over HTTP a person got "Something in Accountant Dad
broke" (`checks.py::tax_lines_can_be_posted`). A refusal a person can read is a
product; a stack trace is an outage.

WHAT THIS MODULE CANNOT DO, SAID SO NOBODY RELIES ON IT
---------------------------------------------------------
It cannot tell that a bill was misread CONSISTENTLY - every figure scaled by
ten. Every law holds, every field is legible, confidence is 1.0, and it posts.
That is failure mode F-02, no arithmetic sees it, and nothing here pretends to.

It also cannot tell whether the party, period and tax facts it was handed are
true. It can only tell whether somebody actually looked: `None` means nobody
did, and nobody-looked blocks.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, cast

from accountant.cage.conservation import (
    LAWS,
    ConservationResult,
    Verdict,
    balance_delta_equals_entry,
)
from accountant.cage.wall import DECIDING_MODULE, LedgerEntry, Observation
from accountant.money import format_inr
from accountant.questions import QUESTION_CAP

#: Owner-set. At or above this, and with everything else clean, the entry is
#: written without asking. Named here once so there is one place to read it and
#: one place a reviewer sees it change.
AUTO_POST_FLOOR: Final = 0.95

#: Owner-set. Below this the product does not even ask. A question about a field
#: nobody could read spends one of five on our own ignorance, and the answer
#: would be the person typing the whole bill in anyway.
ASK_FLOOR: Final = 0.70

#: Written from `ASK_FLOOR` rather than typed again, because two copies of a
#: threshold drift and the sentence is the half a person reads.
_ASK_FLOOR_IN_100: Final = round(ASK_FLOOR * 100)

#: The one law that is a statement about the BOOKS rather than about the
#: document. Taken from the function's own name rather than typed again: the
#: entry in `conservation.LAWS` and the function that produces it are the same
#: word, and binding to the function means a rename breaks the import loudly
#: instead of leaving a string here that matches nothing.
LAW_ABOUT_THE_BOOKS: Final = balance_delta_equals_entry.__name__

#: The laws that are statements about the DOCUMENT, and are therefore answerable
#: before anything is written. DERIVED, not retyped: a law added to
#: `conservation.py` lands in this set automatically and blocks on
#: INDETERMINATE, which is the fail-closed direction. Retyping the three names
#: here would leave a new law in neither set, exempt by omission - the same
#: shape as the empty-set AST guard in `wall.py`.
DOCUMENT_LAWS: Final[frozenset[str]] = frozenset(LAWS) - {LAW_ABOUT_THE_BOOKS}


class Moment(StrEnum):
    """Which point in the write this decision is being made at.

    **There is no default and it is never inferred.** A caller must say. The
    obvious alternative - work it out from whether an after-balance arrived - is
    exactly how the defect this enum fixes got in: a value that is absent
    because it cannot exist yet is indistinguishable, at the type level, from a
    value the caller simply forgot, and guessing between them is what turned a
    vacuous check into a passing one.

    The two moments are not symmetrical, and that asymmetry is the whole point:
    `balance_delta_equals_entry` cannot be known BEFORE_THE_WRITE and must be
    known AFTER_THE_WRITE.
    """

    BEFORE_THE_WRITE = "before_the_write"
    AFTER_THE_WRITE = "after_the_write"


class Action(StrEnum):
    """What happens next. Three, because two would lose the middle one.

    Deliberately not `schema.Outcome` (VALID / UNCLEAR / NOT_VALID). That enum
    answers "is this voucher fit to post" from checks and memory. This one
    answers "what does the cage do with this bill" from confidence and
    conservation. Sharing a type would tie two decisions together that are
    allowed to change independently, and the member names are different so a
    reader cannot confuse one for the other in a log line.
    """

    POST = "post"
    ASK = "ask"
    BLOCK = "block"


# ---- the sentences ----------------------------------------------------------
# Every one of them is a whole sentence in plain words, names no ledger account,
# and says what happened rather than which branch fired. They are constants
# because a sentence built inline is a sentence nobody reviews.

#: Owner's wording, kept verbatim (decision Q3=D).
GST_IS_OFF: Final = (
    "This bill includes GST. GST posting is switched off, so this cannot be "
    "posted automatically."
)

_GST_UNKNOWN: Final = (
    "I could not tell whether there is tax on this bill, and I will not guess, "
    "so nothing was posted."
)

_NOTHING_READ: Final = "I could not read anything off this one, so nothing was posted."

_CHECKS_DID_NOT_RUN: Final = (
    "The safety checks that run on every bill did not all run on this one, and "
    "a check that did not run is not a check that passed, so nothing was posted."
)

_COULD_NOT_CHECK: Final = (
    "There is something on this bill I could not check at all. Not checked is "
    "not the same as checked and fine, so nothing was posted."
)

_LOST_COUNT: Final = (
    "I have lost count of how many questions I have asked about this one, so "
    "nothing was posted."
)

_PERIOD_UNKNOWN: Final = (
    "I could not tell whether the books for this date are still open, so "
    "nothing was posted."
)

_PARTY_NOT_LOOKED_UP: Final = (
    "Nobody checked whether this name is already in your books, so nothing was posted."
)

_TOO_UNSURE: Final = (
    f"I am less than {_ASK_FLOOR_IN_100} out of 100 sure about what this bill "
    "says. That is too little to even ask about, so this one is saved for you "
    "to finish."
)

_MOMENT_UNKNOWN: Final = (
    "Nobody said whether this bill had already been written to your books, and "
    "which checks can be answered depends on that, so nothing was posted."
)

_SAME_PLACE_TWICE: Final = (
    "Both sides of this entry point at the same place in your books, which "
    "cannot be right, so nothing was posted."
)

_ACCOUNTS_UNREADABLE: Final = (
    "I could not tell where this money went or where it came from, so nothing "
    "was posted."
)

_UNCLEAR_LIST_UNREADABLE: Final = (
    "I could not tell what was unclear about this one, so nothing was posted."
)

_AMOUNT_NOT_MONEY: Final = (
    "I could not read a whole amount of money off this bill, so nothing was posted."
)

_PARTY_NOT_A_NAME: Final = (
    "What I read as the name on this bill is not a name, so nothing was posted."
)

_NUMBERS_DISAGREE: Final = (
    "The numbers on this bill do not add up, so I will not post it without "
    "checking with you first."
)

_NOT_SURE_ENOUGH: Final = (
    "I am not sure enough about what this bill says to post it on my own, so I "
    "need to check with you first."
)

_WHICH_WAY_ROUND: Final = (
    "I do not know where this money went, or where it came from, so I need to "
    "ask you before anything is posted."
)

_POSTED: Final = (
    "Everything on this bill was checked and everything agreed, so it was posted."
)


# ---- naming the bill the refusal is about -----------------------------------
# A person with four bills in flight cannot act on "the books for this date are
# closed" - it does not say which one. Everything below reads what the
# `Observation` already carries and NEVER invents anything: there is no bill
# reference in this system, so none is made up, and a field that was not read
# produces no clause at all rather than the word "None" on somebody's screen.


def _party_named(seen: Observation) -> str:
    """The party as it was read, or "" when what arrived is not a name."""
    party = seen.party.value
    if type(party) is not str or not party.strip():
        return ""
    return party.strip()


def _amount_named(seen: Observation) -> str:
    """The total in rupees, through `money.format_inr` and nothing else.

    `type(...) is not int` refuses `bool` as well, the same as everywhere else
    money is handled here: `True == 1`, and "the ₹0.01 bill" is a worse thing to
    print than no amount at all.
    """
    amount = seen.total_paise.value
    if type(amount) is not int:
        return ""
    return format_inr(amount)


def _date_named(seen: Observation) -> str:
    """The date exactly as it was read, and deliberately NOT reformatted.

    There is no one date renderer in this repository the way `money.format_inr`
    is the one money renderer. `tallyio/audit.py` writes `%Y-%m-%d`,
    `web/app.py` writes `%d %b %H:%M`, and Tally's wire writes `%Y%m%d`.
    Inventing a fourth here is exactly the defect `money.py` was written to
    undo, so the date is echoed back in the form it arrived in - which is also
    the form the person typed it in.
    """
    when = seen.date.value
    if type(when) is datetime.date:
        return when.isoformat()
    if type(when) is str and when.strip():
        return when.strip()
    return ""


def _which_bill(seen: Observation) -> str:
    """ " That is the ₹1,200.00 bill from Sharma Stationers.", or "".

    A leading space, because this is appended to a finished sentence. Empty
    when neither the party nor the amount was read: a refusal that names nothing
    is bad, and one that names a value nobody read is worse.
    """
    party, amount = _party_named(seen), _amount_named(seen)
    if party and amount:
        return f" That is the {amount} bill from {party}."
    if party:
        return f" That is the bill from {party}."
    if amount:
        return f" That is the {amount} bill."
    return ""


def _period_closed(seen: Observation) -> str:
    when = _date_named(seen)
    return (
        f"The books for {when} are closed, so nothing can be added to them."
        if when
        else "The books for this date are closed, so nothing can be added to them."
    )


def _party_unknown(seen: Observation) -> str:
    """Name the party we do not recognise, because that is the whole content.

    "I do not know who this bill is from" is true and useless: the person has to
    open the bill to find out which name we mean. The name IS what they need to
    act - add them in Tally, or tell us the name already on the books.
    """
    who = _party_named(seen)
    opening = (
        f"I have never seen {who} in your books."
        if who
        else "I do not know who this bill is from."
    )
    return (
        f"{opening} I will never add a new name to your books on my own, so "
        "this one is saved for you to finish."
    )


@dataclass(frozen=True)
class Situation:
    """Everything the decision needs, gathered by somebody else.

    Three facts about the world are `bool | None`, and the `None` is the whole
    reason they are not plain `bool`. "The period is open" and "nobody looked up
    whether the period is open" are different facts, and a type that cannot tell
    them apart forces the second one to be written as the first. Every one of
    them blocks on `None`.

    **There are no defaults.** A default of `period_open=True` would be a fact
    nobody checked wearing the costume of one, and it would be supplied silently
    at every call site that forgot. A caller who forgets gets a `TypeError` here
    instead of a post there.

    This class validates nothing. That is not an oversight - `decide` refuses
    every malformed field with a sentence, and a validating constructor would
    turn the same input into an exception on its way to a person's screen.
    """

    observation: Observation
    conservation: tuple[ConservationResult, ...]
    party_known: bool | None
    period_open: bool | None
    carries_gst: bool | None
    questions_asked: int
    debit_account: str
    credit_account: str
    #: Before the write, or after reading the register back. Required, with no
    #: default, for the same reason the three facts above have none: a caller
    #: who does not say gets a `TypeError` here rather than an exemption there.
    #: It sits last among the required fields so that adding it moved no
    #: existing argument's position.
    moment: Moment
    #: Fields the reader could read more than one way - a date that is either
    #: the 3rd of April or the 4th of March. The names are the caller's own
    #: vocabulary and are never shown to a person; only the count is.
    ambiguous_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class Decided:
    """What happens to this bill, why, and - on exactly one outcome - the write.

    `entry` is present if and only if `action` is `POST`, enforced here rather
    than left to `decide`. A blocked decision carrying a writable entry is one
    careless attribute access away from posting the thing we just refused, and
    that is the kind of defect that survives review because both halves look
    fine on their own.
    """

    action: Action
    said: str
    reasons: tuple[str, ...]
    entry: LedgerEntry | None = None

    def __post_init__(self) -> None:
        if not self.reasons:
            raise ValueError(
                "a decision must carry at least one reason. An outcome nobody "
                "can explain is not an outcome."
            )
        if not self.said.strip():
            raise ValueError("a decision must carry a sentence a person reads.")
        if self.action is Action.POST and self.entry is None:
            raise ValueError(
                "a post outcome must carry the entry it decided to write. A "
                "post with nothing to write is a contradiction."
            )
        if self.action is not Action.POST and self.entry is not None:
            raise ValueError(
                f"a {self.action} outcome must carry no entry, and this one "
                "carries a writable one. Nothing that was refused may hold "
                "something postable."
            )


def _spoken(
    action: Action, reasons: tuple[str, ...], entry: LedgerEntry | None = None
) -> Decided:
    """Join the reasons into the sentence a person reads.

    Every reason, not the first one. A refusal that reports one problem when
    there are two sends the person to fix one thing and walk straight back into
    the other - `conservation.run` returns all four verdicts for exactly this
    reason and stops at nothing.
    """
    return Decided(action=action, said=" ".join(reasons), reasons=reasons, entry=entry)


def _observed(value: object) -> Observation | None:
    """The `Observation`, or None when what arrived is not one.

    `type(...) is not` rather than `isinstance`, matching `wall.py`: it refuses
    a subclass, which fails closed, and pyright does not call it redundant the
    way it does an `isinstance` against the annotated type.
    """
    return value if type(value) is Observation else None


def _checks_are_intact(results: object) -> bool:
    """Did all four laws actually run, and did each come back with a verdict?

    `ConservationResult` does not validate its own fields, so a caller can hand
    over a bare string where a `Verdict` belongs. A module that only asks "is it
    FAIL" would read that string as a pass, which is the failure this whole file
    is built to avoid, one level down.
    """
    if type(results) is not tuple:
        return False
    for result in cast(tuple[object, ...], results):
        if type(result) is not ConservationResult:
            return False
        if type(result.verdict) is not Verdict:
            return False
    return tuple(r.law for r in cast(tuple[ConservationResult, ...], results)) == LAWS


def _moment_blocks(moment: object) -> list[str]:
    """The caller has to say when this is. It is never worked out from the data.

    Inferring the moment from whether an after-balance arrived is the defect
    this field exists to close: a value absent because it cannot exist yet and a
    value absent because the caller forgot are identical at the type level, and
    guessing between them is what turned a vacuous check into a passing one.
    """
    if type(moment) is not Moment:
        return [_MOMENT_UNKNOWN]
    return []


def _conservation_blocks(results: object, moment: object) -> list[str]:
    """ "Could not check" blocks - for three of the four laws, at both moments.

    THE FOURTH LAW IS DIFFERENT, AND THIS IS THE WHOLE OF THE DIFFERENCE.
    `balance_delta_equals_entry` compares the ledger balance before the entry
    with the balance after it. Before a write there IS no after, so the honest
    verdict pre-write is INDETERMINATE, every time, on every bill. Blocking on
    it made a post unreachable except by handing the law a PREDICTED
    after-balance - a law comparing a number against itself, which is a check
    that cannot fail dressed as a check that passed.

    The exemption is narrow in three ways, all of them deliberate:

        one law     `DOCUMENT_LAWS` is derived from `conservation.LAWS`, so a
                    law added there blocks by default rather than joining the
                    exemption by omission.
        one moment  after the write the balance IS knowable, and not knowing it
                    then means nobody read the register back - which is the one
                    failure this law was written to catch.
        one verdict INDETERMINATE only. A pre-write FAIL still counts: a caller
                    who supplied real balances and got a contradiction is
                    telling us something true, and when it arrived does not make
                    it less true. `_asking` picks that up and refuses the post.

    A `moment` that is not a `Moment` grants nothing - `is` against the member,
    so anything else falls through to blocking on all four.
    """
    if not _checks_are_intact(results):
        return [_CHECKS_DID_NOT_RUN]
    checked = cast(tuple[ConservationResult, ...], results)
    unchecked = [r for r in checked if r.verdict is Verdict.INDETERMINATE]
    if moment is Moment.BEFORE_THE_WRITE:
        unchecked = [r for r in unchecked if r.law in DOCUMENT_LAWS]
    if unchecked:
        return [_COULD_NOT_CHECK]
    return []


def _budget_blocks(asked: object) -> list[str]:
    # `type(...) is not int` refuses `bool` as well: `isinstance(True, int)` is
    # True and `True == 1`, so a flag passed where a count belonged would read
    # as "one question asked" and quietly authorise four more.
    if type(asked) is not int or asked < 0:
        return [_LOST_COUNT]
    if asked >= QUESTION_CAP:
        return [
            f"I have already asked you {asked} questions about this one, which "
            "is all I am allowed, so it is saved for you to finish."
        ]
    return []


def _world_blocks(situation: Situation, seen: Observation) -> list[str]:
    """The three facts somebody had to look up, and what happens if they did not.

    `is not True` and `is not False` rather than truthiness, deliberately. A
    caller passing `0` or `""` where a looked-up fact belonged is a caller who
    did not look it up, and truthiness would read it as a definite no.

    Each refusal names what it can off the `Observation` - the date for a closed
    period, the party for an unknown one, the party and amount for the tax rule,
    which has none of its own. `seen` is real by the time this is reached;
    `_blocking` returns before it otherwise.
    """
    reasons: list[str] = []
    if situation.carries_gst is not False:
        reasons.append(
            GST_IS_OFF + _which_bill(seen)
            if situation.carries_gst is True
            else _GST_UNKNOWN + _which_bill(seen)
        )
    if situation.period_open is not True:
        reasons.append(
            _period_closed(seen) if situation.period_open is False else _PERIOD_UNKNOWN
        )
    if situation.party_known is not True:
        reasons.append(
            _party_unknown(seen)
            if situation.party_known is False
            else _PARTY_NOT_LOOKED_UP
        )
    return reasons


def _account_blocks(situation: Situation) -> list[str]:
    """Only what no answer could fix. A missing account is a question, not this.

    An empty account means nobody has chosen yet, and "How did you pay?" is
    something a person answers in one tap - `checks.py::funding_is_named` says
    the same thing at length. Two *named* accounts that are the same is a typo,
    and no answer to any question makes it not one.
    """
    debit, credit = situation.debit_account, situation.credit_account
    if type(debit) is not str or type(credit) is not str:
        return [_ACCOUNTS_UNREADABLE]
    if debit.strip() and credit.strip() and debit == credit:
        return [_SAME_PLACE_TWICE]
    return []


def _blocking(situation: Situation) -> tuple[str, ...]:
    """Every reason this bill may not be posted, in one fixed order.

    The order is part of the contract for the same reason `LAWS` order is: a log
    line reads the same on every run, and a person reading two sentences reads
    them in the same order twice.
    """
    seen = _observed(situation.observation)
    if seen is None:
        # Nothing else can be evaluated - every remaining check reads a field
        # off the observation - so this returns alone rather than piling up
        # errors that are all the same error.
        return (_NOTHING_READ,)
    reasons: list[str] = []
    # The moment comes first because everything after it depends on it: which
    # conservation verdicts can be honoured is a function of when this is. Both
    # would block anyway - a moment nobody stated grants no exemption - but the
    # person would read "there is something I could not check" for a defect that
    # is actually in the calling code.
    reasons.extend(_moment_blocks(situation.moment))
    reasons.extend(_conservation_blocks(situation.conservation, situation.moment))
    reasons.extend(_budget_blocks(situation.questions_asked))
    reasons.extend(_world_blocks(situation, seen))
    reasons.extend(_account_blocks(situation))
    if type(situation.ambiguous_fields) is not tuple:
        reasons.append(_UNCLEAR_LIST_UNREADABLE)
    if seen.lowest_confidence < ASK_FLOOR:
        reasons.append(_TOO_UNSURE + _which_bill(seen))
    return tuple(reasons)


def _asking(situation: Situation, seen: Observation) -> tuple[str, ...]:
    """Every reason to put a question to the person, in one fixed order.

    Reached only when nothing blocks, so the accounts are known to be strings
    and the conservation results are known to be four intact verdicts.
    """
    reasons: list[str] = []
    failed = [r for r in situation.conservation if r.verdict is Verdict.FAIL]
    if failed:
        # The law's own sentence names the figures and the difference. "The
        # numbers do not add up" is not something a person can check; "₹1,199.99
        # against a stated total of ₹1,200.00, out by 1 paisa" is. The figures
        # arrive already formatted - `conservation.py` renders every amount
        # through `money.format_inr`, so nothing here reformats them and there
        # is no second place for the grouping to be wrong.
        reasons.append(_NUMBERS_DISAGREE + _which_bill(seen))
        reasons.extend(r.said for r in failed)
    if situation.ambiguous_fields:
        count = len(situation.ambiguous_fields)
        thing = "thing" if count == 1 else "things"
        # The number, never the name - the same shape as the unmapped-account
        # count in `questions.py::_something_else`. A field name is our
        # vocabulary and means nothing to the person reading it.
        reasons.append(
            f"{count} {thing} on this bill could be read more than one way, so "
            "I need to check with you before anything is posted."
        )
    if seen.lowest_confidence < AUTO_POST_FLOOR:
        reasons.append(_NOT_SURE_ENOUGH + _which_bill(seen))
    if not situation.debit_account.strip() or not situation.credit_account.strip():
        reasons.append(_WHICH_WAY_ROUND)
    return tuple(reasons)


def decide(situation: Situation) -> Decided:
    """Post, ask, or block. Pure, deterministic, and it never raises.

    Block first, then ask, then post - and post only by building the thing,
    never by concluding that nothing objected. The last gate is `wall.py`
    itself: if it refuses what this module assembled, that refusal becomes a
    block rather than an exception.
    """
    blocking = _blocking(situation)
    if blocking:
        return _spoken(Action.BLOCK, blocking)

    # `_blocking` returning nothing is what proves the observation is real; the
    # cast carries that fact past the type checker rather than re-deriving it.
    seen = cast(Observation, _observed(situation.observation))

    asking = _asking(situation, seen)
    if asking:
        return _spoken(Action.ASK, asking)

    # These two are here for the SENTENCE, not for the outcome, and a mutation
    # run is what made the difference visible: deleting the amount check changes
    # nothing about what happens - the wall refuses a float too, and the block
    # below catches it - but it changes what the person reads to "amount_paise
    # must be a whole number of paise, not float", which is our variable name on
    # a stranger's screen. The wall's message is written for a developer reading
    # a traceback; these two are written for the person whose bill it is.
    amount = seen.total_paise.value
    if type(amount) is not int:
        return _spoken(Action.BLOCK, (_AMOUNT_NOT_MONEY,))
    party = seen.party.value
    if type(party) is not str:
        # This one is load-bearing for the outcome as well. `decided` calls
        # `party.strip()`, so a number here raises `AttributeError` - which the
        # block below does NOT catch, because catching every exception would
        # turn a real bug into a quiet refusal nobody ever investigates.
        return _spoken(Action.BLOCK, (_PARTY_NOT_A_NAME,))

    try:
        entry = LedgerEntry.decided(
            DECIDING_MODULE,
            party=party,
            amount_paise=amount,
            debit_account=situation.debit_account,
            credit_account=situation.credit_account,
        )
    except ValueError as refused:
        # The wall's VALUE rules - positive amount, named party, two different
        # ledgers - are not copied up here. One home for each means one place to
        # change it, and this branch is what stops the refusal arriving at a
        # person as a traceback. Every message it can carry names amounts and
        # never a ledger, so nothing jargon-shaped leaks into the sentence.
        return _spoken(
            Action.BLOCK,
            (
                "This could not be written into your books as it stands, so "
                f"nothing was posted. What stopped it: {refused}",
            ),
        )
    return _spoken(Action.POST, (_POSTED,), entry)

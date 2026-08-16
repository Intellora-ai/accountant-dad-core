"""Two types, and the wall between them.

WHY THIS MODULE EXISTS
----------------------
Before the cage, one type carried a field from the moment it was guessed to the
moment it was written into a customer's books. That single fact is the root
cause of six of the sixteen recorded failure modes - a misread amount, a wrong
party, a flipped sign, a nonsense entry, a photo of a cat and a wrong voucher
type all reach the books by the same route, because nothing in the type system
says which of them has been checked.

So there are two:

    Observation   what we think the bill says. Every field carries a confidence
                  between 0.0 and 1.0. NOTHING here can be posted.
    LedgerEntry   what we will write. Built ONLY by the decision layer.

THE WALL IS A CONSTRUCTOR, NOT A CONVENTION
---------------------------------------------
`LedgerEntry.decided()` takes the caller's module name and refuses anybody who
is not `accountant.cage.decision`. A reader that misread an amount cannot turn
it into something postable no matter how sure it was, because it is not the
decision layer and cannot claim to be without lying in a way a reviewer sees in
the diff.

That is the run-time half. The test-time half is an AST scan in
`tests/test_the_wall.py` proving no module outside this file constructs a
`LedgerEntry` directly, which is the only way to walk around the constructor.

**Both halves are needed and neither covers the other's failure.** Defect J1 in
this repository's own history: *a unit test of a guard proves the guard works
and says nothing about whether the guard is installed.* A runtime guard is
skipped by any module that simply never calls it; a static guard cannot stop a
write already in flight.

WHY CONFIDENCE LIVES ON THE FIELD AND NOT THE RECORD
------------------------------------------------------
A bill is not uniformly legible. The total is often printed large and clean
while the party name is a smudged letterhead. One score for the whole document
would average a certainty with a guess and produce a number that describes
neither. So every field carries its own, and the record reports the **minimum** -
because one misread digit ruins an amount, and a mean hides exactly the field
that should have stopped the post.

The minimum is taken over the fields that APPLY to the bill in hand, and the
caller says which those are - see `Observation.lowest_confidence_where`. A bill
with no tax line has nothing to read in its tax field, and scoring that as a
failed reading refused the only bills this product is allowed to post.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# The one rupee renderer. Same exception, same reasoning as `conservation.py`:
# this module is meant to depend on nothing, and `money` is a pure int -> str
# function whose only import is `__future__`. It touches no network, no
# filesystem and no Tally, so the purpose of the rule survives. What would make
# the exception unsafe is `money` acquiring a dependency of its own.
from accountant.money import format_inr

#: The only module permitted to build a `LedgerEntry`. Written once, here, so
#: there is one place to read and one place a reviewer sees it change.
DECIDING_MODULE: Final = "accountant.cage.decision"


class NotYourEntryError(RuntimeError):
    """Something that is not the decision layer tried to build a write.

    A `RuntimeError` rather than a `ValueError`: the data may be perfectly fine.
    What is wrong is *who is asking*, and no correction to the numbers fixes it.
    """


@dataclass(frozen=True)
class Field:
    """One thing we think we read, and how sure we are that we read it.

    Invariants, enforced on construction rather than trusted:

      - a value of `None` must carry confidence `0.0`. "We did not read it" and
        "we are unsure" are the same fact, and letting them disagree would allow
        a post on nothing at all.
      - confidence is inside `0.0`-`1.0`. A number above 1.0 would clear the
        auto-post band by accident.
      - `source` is never empty. A field with no provenance cannot be explained
        to the person whose books it is about to change.
    """

    value: object
    confidence: float
    source: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be between 0.0 and 1.0, got {self.confidence}. "
                "A score outside that range means the caller computed it wrongly."
            )
        if self.value is None and self.confidence != 0.0:
            raise ValueError(
                "a field with no value must have confidence 0.0, not "
                f"{self.confidence}. Not reading something and being unsure "
                "about it are the same fact."
            )
        if not self.source.strip():
            raise ValueError(
                "a field must say where it came from. A value with no "
                "provenance cannot be explained to the person it affects."
            )


@dataclass(frozen=True)
class Observation:
    """What we think the bill says. Inert by construction.

    This type has no method that writes, posts, saves or converts itself into
    anything postable, and a test asserts that it never grows one. The only way
    from here to the books is through the decision layer.
    """

    date: Field
    party: Field
    total_paise: Field
    tax_paise: Field
    line_paise: tuple[int, ...] | None = None

    def lowest_confidence_where(
        self, *, tax_applies: bool, date_applies: bool = True
    ) -> float:
        """The weakest field that applies to THIS bill, not the weakest field.

        A field nobody read scores 0.0 and the minimum takes it. On a bill with
        no tax line there is nothing in the tax field to read, so before this
        the only kind of bill that CAN auto-post - owner decision Q3=D refuses
        every bill that carries tax - scored 0.0 and was refused for not having
        the thing it correctly does not have.

        THE CALLER STATES IT; THIS RECORD DOES NOT WORK IT OUT. Whether a bill
        carries tax is a fact about the world that somebody looked up, and it
        lives on the caller as `decision.Situation.carries_gst` - the same
        question `schema.Voucher.needs_tax_lines` asks on the write side. An
        `Observation` that read its own tax field and concluded "no tax here"
        would be deriving a world fact from the very thing it failed to read,
        which is the shape of every defaulted world fact `Situation` has no
        defaults in order to stop.

        A BOOLEAN AND NOT A SET OF FIELD NAMES. Tax was the one field a real
        bill could genuinely not have; an amount and a party are needed by
        all of them.

        `date_applies` JOINED IT ON 2026-08-16 ON THE SAME TERMS, and the
        sentence above used to include the date. It was true of every
        document this cage had seen and false of the one it blocked most:
        `paid Sharma Traders 4200 for cement` is a typed expense note, it
        has no invoice date, and it scored 0.0 for not carrying the thing it
        correctly does not have - the exact defect the tax boolean was added
        to fix, one field over. `decision._date_applies` is the caller that
        answers it, from `Situation.document_type`. A caller-supplied list of
        applicable fields would let a typo drop the amount, so the wrong
        answer would be one string away.
        Here it is unreachable rather than merely untaken.

        `tax_applies` IS KEYWORD-ONLY WITH NO DEFAULT. There is no call that
        silently means "no tax": forgetting raises `TypeError` here rather
        than posting there.

        `date_applies` DEFAULTS TO `True`, WHICH IS THE STRICTER SIDE and is
        the only reason it differs. Every document this cage judged before
        2026-08-16 was treated as carrying a date, so the default preserves
        that judgement exactly for the fifteen existing call sites: a caller
        who has not heard of document types gets the behaviour it already
        had. Forgetting it costs a question. The opposite default would
        excuse a missing date on a real invoice, which costs a write.
        """
        applicable = [self.party, self.total_paise]
        if date_applies:
            applicable.append(self.date)
        if tax_applies:
            applicable.append(self.tax_paise)
        return min(field.confidence for field in applicable)

    @property
    def lowest_confidence(self) -> float:
        """The weakest field, not the average. Every field counts.

        A bill is not uniformly legible: a clean printed total next to a smudged
        letterhead averages to something that describes neither. One misread
        digit ruins an amount, so the whole record is only as trustworthy as its
        worst field.

        THIS IS THE ANSWER FOR A CALLER THAT STATES NOTHING, and it is the
        stricter of the two on purpose. Every field applies until somebody with
        the world fact in hand says otherwise, so a caller who has not heard of
        `lowest_confidence_where` asks or blocks where it might have posted.
        Forgetting costs a question; the opposite default would cost a write.
        """
        return self.lowest_confidence_where(tax_applies=True)


@dataclass(frozen=True)
class LedgerEntry:
    """What we will write. The far side of the wall.

    **Do not construct this directly.** Use `LedgerEntry.decided()`, which
    refuses any caller that is not the decision layer. Constructing it directly
    is caught by the AST scan in `tests/test_the_wall.py`, which is the half of
    the guard that proves the other half is installed.
    """

    party: str
    amount_paise: int
    debit_account: str
    credit_account: str

    @classmethod
    def decided(
        cls,
        caller: str,
        *,
        party: str,
        amount_paise: int,
        debit_account: str,
        credit_account: str,
    ) -> LedgerEntry:
        """Build a write, if and only if the decision layer is asking.

        `caller` is passed rather than inspected from the stack on purpose.
        Stack inspection is fragile across wrappers, decorators and threads, and
        it fails *open* when it cannot tell - which is the wrong direction for a
        guard. An explicit argument means a module that wants to bypass this has
        to write `DECIDING_MODULE` in its own source, where a reviewer reads it
        in the diff and the AST scan finds it anyway.
        """
        if caller != DECIDING_MODULE:
            raise NotYourEntryError(
                f"{caller!r} tried to build something postable. Only "
                f"{DECIDING_MODULE!r} may do that. Whatever was read here is an "
                "Observation until the decision layer has checked it."
            )
        # `type(...) is int` rather than `isinstance`, for two reasons. It
        # rejects `bool` - which IS an `int` in Python, so `True == 1` would
        # otherwise balance a one-paisa entry - and pyright does not flag it as
        # redundant the way it flags `isinstance` against an `int` annotation.
        # The check is not redundant at run time: annotations are not enforced,
        # and a CSV row or a tool-call arrives untyped (`vouchers.py:394` says
        # the same thing about the same hazard).
        if type(amount_paise) is not int:
            raise ValueError(
                "amount_paise must be a whole number of paise, not "
                f"{type(amount_paise).__name__}."
            )
        if amount_paise <= 0:
            # The amount is formatted, not printed raw, and the reason is that
            # this text does not stay in a traceback. `decision.py` catches this
            # ValueError and puts it verbatim into the sentence a person reads,
            # so "got -5 paise" reached a screen. Its author guarded that branch
            # against LEDGER names leaking and did not think of amounts.
            raise ValueError(
                f"an entry must be for a positive amount, got "
                f"{format_inr(amount_paise)}. A zero or negative entry is a "
                "correction, and this system does corrections by reversal, "
                "never by sign."
            )
        if not party.strip():
            raise ValueError("an entry must name a party.")
        if not debit_account.strip() or not credit_account.strip():
            raise ValueError("an entry must name both accounts.")
        if debit_account == credit_account:
            raise ValueError(
                f"both sides of this entry name {debit_account!r}. Money moving "
                "from an account to itself is a typo, not an entry."
            )
        # Constructed by NAME, not `cls(...)`, and that is deliberate. The AST
        # scan in `tests/test_the_wall.py` looks for calls to `LedgerEntry(`;
        # `cls(...)` is invisible to it, which would leave the scan asserting
        # over an empty set and proving nothing. A control test caught exactly
        # that. Naming the class here is what gives the guard something to see.
        return LedgerEntry(
            party=party,
            amount_paise=amount_paise,
            debit_account=debit_account,
            credit_account=credit_account,
        )

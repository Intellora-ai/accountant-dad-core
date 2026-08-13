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

    @property
    def lowest_confidence(self) -> float:
        """The weakest field, not the average.

        A bill is not uniformly legible: a clean printed total next to a smudged
        letterhead averages to something that describes neither. One misread
        digit ruins an amount, so the whole record is only as trustworthy as its
        worst field.
        """
        return min(
            field.confidence
            for field in (self.date, self.party, self.total_paise, self.tax_paise)
        )


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

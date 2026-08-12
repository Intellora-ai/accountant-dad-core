"""A model that lies on command, so the cage can be tested against the lie.

WHY THIS MODULE EXISTS
----------------------
You cannot test a cage with an empty cage. Every guard here is written against
a specific failure - a misread amount, a wrong party, a flipped sign, a bill
that adds up perfectly and is still wrong by a factor of ten. Point a real
model at the guards and you learn one thing: which of the lies it happened to
tell that day were caught. Run it tomorrow and it tells a different set. The
measurement moves, and a guard that quietly stopped working looks exactly like
a model that got better.

A stub that lies TO ORDER inverts that. The lie is named, fixed and repeatable,
so a guard is tested against exactly the failure it was built for, and a guard
that stops catching it fails a test rather than degrading in silence.

THE FOUR MODES, AND WHY FOUR
------------------------------
    SUCCESS   a clean, correct reading. The baseline every other mode is
              measured against - without it, a failing guard and a broken
              fixture look the same.
    GARBAGE   confident nonsense, one named lie at a time.
    PARTIAL   some fields read, some not. The commonest real outcome: a phone
              photo with the top of the bill in frame and the amounts cut off.
    FAILURE   nothing read at all. The photo is a thumb, or a cat.

PARTIAL and FAILURE are separate because the guards treat them differently. A
partial read has real values that could tempt a caller into filling the gaps;
an empty one cannot. Collapsing them would hide which of the two a guard was
actually tested against.

WHY THE LIE IS TOLD AT FULL CONFIDENCE
----------------------------------------
Every GARBAGE field carries `EXACT` - the same score a perfect read gets. That
is the whole point. Failure mode F-02 is a model that is CERTAIN and WRONG, and
a stub that hedged its lies would be stopped by the confidence band before any
other guard was reached, proving only that low scores are refused. We already
know low scores are refused. What needs proving is what happens when nothing in
the score says anything is wrong.

WHY THERE IS NO RANDOM NUMBER GENERATOR
-----------------------------------------
The seed indexes a fixed tuple. There is no `random`, no clock and no source of
entropy anywhere in this module, because "seeded so it is reproducible" is still
one more thing to trust - across Python versions, across machines, and across
whoever later adds a second draw and shifts every subsequent one.

The seed chooses WHICH lie, never HOW BIG it is. A seed-scaled amount would
make the lie a different size on every run, and a guard tested against a moving
target is tested against nothing.

WHAT THIS MODULE DOES NOT PROVE
---------------------------------
It does not resemble a real OCR engine and does not try to. Tesseract's
mistakes are pixel-shaped - a 5 read as an S, a smudge read as a digit - and
these are chosen. A guard that passes against this stub is proven against the
lies somebody thought of, which is more than nothing and less than safe.

NEVER ON A RUNTIME PATH
-------------------------
Nothing that ships imports this. A liar wired into the product would put
invented amounts in front of a customer with the product's own confidence
attached, which is worse than having no cage at all. That is enforced by an
AST scan in `tests/test_lying.py`, not by this sentence.

It also takes only `Field` and `Observation` from the wall - both inert. It
cannot construct anything postable, and the wall would refuse it if it tried.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from accountant.cage.confidence import EXACT, looks_like_a_date
from accountant.cage.wall import Field, Observation


class Mode(StrEnum):
    """What the stub should do this time."""

    SUCCESS = "success"
    GARBAGE = "garbage"
    PARTIAL = "partial"
    FAILURE = "failure"


class Lie(StrEnum):
    """The named failures. Each one is a real thing a model has done.

    `WRONG_PARTY` and `INVENTED_VENDOR` are deliberately separate. One is two
    real suppliers confused, the other is a supplier that does not exist. A
    check against the ledger's own names catches the second and not the first,
    so a stub that merged them would report a guard as working when half the
    failure walks straight past it.
    """

    WRONG_AMOUNT = "wrong_amount"
    WRONG_PARTY = "wrong_party"
    FLIPPED_SIGN = "flipped_sign"
    INVENTED_VENDOR = "invented_vendor"
    SELF_CONSISTENT_WRONG_TOTAL = "self_consistent_wrong_total"


#: The lies a seed can reach, in the order it indexes them. Fixed like
#: `conservation.LAWS`: the order is part of the contract, so seed 3 means the
#: same lie next month and a log line reads the same on every run.
LIE_KINDS: Final[tuple[Lie, ...]] = (
    Lie.WRONG_AMOUNT,
    Lie.WRONG_PARTY,
    Lie.FLIPPED_SIGN,
    Lie.INVENTED_VENDOR,
    Lie.SELF_CONSISTENT_WRONG_TOTAL,
)

#: Written into every `Field.source` this module produces. The VALUES carry no
#: marker - a marked amount would let a guard "catch" the lie by spotting the
#: marker rather than by doing its job - but the provenance does, because that
#: is what `source` is for and no arithmetic guard reads it.
SOURCE: Final = "lying_model"

#: The factor `SELF_CONSISTENT_WRONG_TOTAL` scales every figure by. Ten, and
#: not a number picked here: `conservation.py` names exactly this case in its
#: own docstring - "a bill misread as a consistent Rs 10,000 instead of
#: Rs 1,000". The two files describe one failure, so they use one number.
SELF_CONSISTENT_SCALE: Final = 10

#: How far out `WRONG_AMOUNT` puts the total. One paisa is the smallest
#: disagreement that exists, and `tests/test_conservation.py` already uses it
#: for the same reason: a guard that sees this sees everything larger.
ONE_PAISA: Final = 1

#: Who `WRONG_PARTY` blames instead - another supplier on the same books.
#: Deliberately not a near-spelling of the truthful name, because a near-miss
#: is a fuzzy-matching problem and this is a different failure: the right bill
#: filed against the wrong real account.
ANOTHER_REAL_PARTY: Final = "Verma Stationers"

#: Who `INVENTED_VENDOR` names - a supplier that exists in no ledger anywhere.
A_PARTY_NOBODY_HAS_HEARD_OF: Final = "Nexus Global Supplies"

#: Why a PARTIAL read stopped. Phrased as the person would be told, and
#: prefixed `not_found:` to match the convention already in the wall's tests.
CUT_OFF: Final = "not_found: the amount block is off the edge of the photo"

#: Why a FAILURE read produced nothing.
NOTHING_READABLE: Final = "not_found: nothing on this page could be read"


class UnknownModeError(ValueError):
    """The stub was asked for something it has no definition of.

    Raised before anything is built. A stub that guessed at a mode would hand
    back an observation nobody asked for, and the test using it would still be
    green - which is the failure this whole module exists to prevent.
    """


class UnknownLieError(ValueError):
    """The stub was asked to tell a lie that is not in `LIE_KINDS`."""


class StubToldTheTruthError(RuntimeError):
    """GARBAGE mode produced the truth, so the caller's guard test proves nothing.

    A `RuntimeError` rather than a `ValueError`, like `NotYourEntryError`: the
    inputs may be perfectly valid. What is wrong is the SITUATION - a test
    double that quietly tells the truth looks like a passing suite and is not
    one.

    This is reachable, not defensive decoration. A `Truth` whose party is
    already `ANOTHER_REAL_PARTY` disarms the wrong-party lie completely.
    """


def _paise(value: object, name: str) -> int:
    """An amount, or a refusal. Never a coercion.

    `isinstance(value, bool)` first, because `isinstance(True, int)` is True in
    Python and `True == 1`, so a flag passed where an amount belonged would
    otherwise balance a one-paisa entry. `conservation.py::_paise` refuses bools
    for the same reason; this matches it rather than inventing a second rule.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{name} must be a whole number of paise, not "
            f"{type(value).__name__}. Money is never a float and never a flag."
        )
    return value


@dataclass(frozen=True)
class Truth:
    """What the bill actually says. The thing every lie is measured against.

    Validated on construction rather than trusted, because SUCCESS mode
    promises a CLEAN read: if the truth itself broke a conservation law, the
    clean mode would break one too, and every contrast drawn against it would
    be measured from a broken baseline. That failure is invisible - the tests
    still pass, they just stop meaning anything.
    """

    date: str
    party: str
    net_paise: int
    tax_paise: int
    line_paise: tuple[int, ...]

    def __post_init__(self) -> None:
        if not looks_like_a_date(self.date):
            raise ValueError(
                f"{self.date!r} is not a real calendar date in ISO form. The "
                "34th of a month is a misread 04th, and a stub built on one "
                "would fail a format check for a reason unrelated to its lie."
            )
        if not self.party.strip():
            raise ValueError("a truth must name a party; a bill is from somebody.")
        if _paise(self.net_paise, "net_paise") <= 0:
            raise ValueError(
                f"net_paise is {self.net_paise}. A bill for nothing is not a "
                "bill, and a negative one is a correction - this system does "
                "corrections by reversal, never by sign."
            )
        if _paise(self.tax_paise, "tax_paise") < 0:
            raise ValueError(f"tax_paise is {self.tax_paise}, which is not a tax.")
        for line in self.line_paise:
            if _paise(line, "a line amount") <= 0:
                raise ValueError(
                    f"a line of {line} paise is not a line. A zero line lets a "
                    "set of lines add up correctly while containing a figure "
                    "nobody actually read."
                )
        added = sum(self.line_paise)
        if added != self.total_paise:
            raise ValueError(
                f"these lines come to {added} paise against a total of "
                f"{self.total_paise}. A truth that fails a conservation law "
                "makes SUCCESS mode fail one too, and then nothing in the "
                "suite is measuring what it claims to."
            )

    @property
    def total_paise(self) -> int:
        """The gross. Derived, not stored, so it cannot disagree with itself."""
        return self.net_paise + self.tax_paise


#: The bill every mode works from unless the caller brings its own. The figures
#: are the ones `tests/test_conservation.py` already uses - 40,000 and 60,000
#: of goods, 18,000 of tax at 18% - so a number appearing in two files means
#: the same thing in both.
DEFAULT_TRUTH: Final = Truth(
    date="2026-04-01",
    party="Kumar Traders",
    net_paise=100_000,
    tax_paise=18_000,
    line_paise=(40_000, 60_000, 18_000),
)


def figures(seen: Observation) -> tuple[object, ...]:
    """The values only, with the provenance stripped off.

    What a guard actually looks at. Two observations differing only in which
    mode produced them have identical figures, which is exactly the comparison
    "did the stub really lie" needs - the `source` string always differs, so
    comparing whole observations would answer yes every time.
    """
    return (
        seen.date.value,
        seen.party.value,
        seen.total_paise.value,
        seen.tax_paise.value,
        seen.line_paise,
    )


def lie_for_seed(seed: int) -> Lie:
    """Which lie a seed selects. Arithmetic on an integer, and nothing else."""
    return LIE_KINDS[seed % len(LIE_KINDS)]


def _read(value: object, mode: Mode, detail: str) -> Field:
    """A field the stub claims to have read, at full confidence.

    Full confidence even when the value is a lie. See the module docstring: a
    hedged lie is caught by the confidence band and proves nothing about the
    guards that have to catch a model which is certain and wrong.
    """
    return Field(value=value, confidence=EXACT, source=f"{SOURCE}:{mode}:{detail}")


def _unread(mode: Mode, reason: str) -> Field:
    """A field the stub did not read, with the reason attached.

    Confidence 0.0 is not a choice made here - the wall refuses a `None` value
    at any other score, because "we did not read it" and "we are unsure" are
    the same fact and must not be allowed to disagree.
    """
    return Field(value=None, confidence=0.0, source=f"{SOURCE}:{mode}:{reason}")


def _success(truth: Truth) -> Observation:
    return Observation(
        date=_read(truth.date, Mode.SUCCESS, "date"),
        party=_read(truth.party, Mode.SUCCESS, "party"),
        total_paise=_read(truth.total_paise, Mode.SUCCESS, "total"),
        tax_paise=_read(truth.tax_paise, Mode.SUCCESS, "tax"),
        line_paise=truth.line_paise,
    )


def _garbage(truth: Truth, lie: Lie) -> Observation:
    """One named lie, everything else left truthful.

    Changing one thing at a time is what makes a guard's verdict readable: a
    mode that corrupted every field would tell you a guard caught SOMETHING and
    not what. There is no `else` branch - a `Lie` added without a case here
    would leave the figures untouched, and `StubToldTheTruthError` below is
    what refuses to hand that back as a lie.
    """
    date, party = truth.date, truth.party
    total, tax = truth.total_paise, truth.tax_paise
    lines = truth.line_paise

    if lie is Lie.WRONG_AMOUNT:
        # The lines stay truthful on purpose: that is what leaves the
        # disagreement visible to `lines_sum_to_total`. Scaling them to match
        # would be the self-consistent lie, which is a different failure.
        total = total + ONE_PAISA
    elif lie is Lie.WRONG_PARTY:
        party = ANOTHER_REAL_PARTY
    elif lie is Lie.FLIPPED_SIGN:
        # The total only. A wholly negated bill still balances against itself,
        # and that lie already has a name further down this list.
        total = -total
    elif lie is Lie.INVENTED_VENDOR:
        party = A_PARTY_NOBODY_HAS_HEARD_OF
    elif lie is Lie.SELF_CONSISTENT_WRONG_TOTAL:
        # FAILURE MODE F-02. Every figure moves by the same factor, so the
        # lines still sum to the total and net plus tax still equals gross.
        # Arithmetic cannot see this and `conservation.py` says so itself.
        total *= SELF_CONSISTENT_SCALE
        tax *= SELF_CONSISTENT_SCALE
        lines = tuple(amount * SELF_CONSISTENT_SCALE for amount in lines)

    told = Observation(
        date=_read(date, Mode.GARBAGE, lie),
        party=_read(party, Mode.GARBAGE, lie),
        total_paise=_read(total, Mode.GARBAGE, lie),
        tax_paise=_read(tax, Mode.GARBAGE, lie),
        line_paise=lines,
    )
    if figures(told) == figures(_success(truth)):
        raise StubToldTheTruthError(
            f"asked for {lie!r} and produced the truth unchanged. A guard "
            "tested against this would pass while proving nothing. Check "
            f"whether this truth already says what {lie!r} swaps in."
        )
    return told


def _partial(truth: Truth) -> Observation:
    """The commonest real outcome, and the most dangerous.

    The date and the party survive; the amount block is off the edge of the
    frame. It is the amounts that go missing rather than the party because an
    unread tax field is the single worst coercion in this system - read as
    zero, it posts a GST bill without its input credit, which is real money
    lost with nothing on screen to notice.

    The lines are `None`, not `()`. "We did not look" and "we looked and there
    were none" are different facts, and `lines_sum_to_total` is built around
    exactly that distinction.
    """
    return Observation(
        date=_read(truth.date, Mode.PARTIAL, "date"),
        party=_read(truth.party, Mode.PARTIAL, "party"),
        total_paise=_unread(Mode.PARTIAL, CUT_OFF),
        tax_paise=_unread(Mode.PARTIAL, CUT_OFF),
        line_paise=None,
    )


def _failure() -> Observation:
    """Nothing read. Takes no truth, because it uses none - a stub that held
    the answers while claiming to have read none of them is one edit away from
    leaking them into a field."""
    return Observation(
        date=_unread(Mode.FAILURE, NOTHING_READABLE),
        party=_unread(Mode.FAILURE, NOTHING_READABLE),
        total_paise=_unread(Mode.FAILURE, NOTHING_READABLE),
        tax_paise=_unread(Mode.FAILURE, NOTHING_READABLE),
        line_paise=None,
    )


def _mode(value: object) -> Mode:
    """The mode asked for, or a refusal naming the ones that exist."""
    if isinstance(value, Mode):
        return value
    if isinstance(value, str):
        try:
            return Mode(value)
        except ValueError:
            pass
    raise UnknownModeError(
        f"{value!r} is not a mode this stub knows. It does "
        f"{', '.join(mode.value for mode in Mode)} - and nothing else, because "
        "a stub that guessed would hand back an observation nobody asked for "
        "and the test using it would still be green."
    )


def _lie(value: object, seed: int) -> Lie:
    """The lie asked for, or the one this seed selects."""
    if value is None:
        return lie_for_seed(seed)
    if isinstance(value, Lie) and value in LIE_KINDS:
        return value
    if isinstance(value, str):
        try:
            named = Lie(value)
        except ValueError:
            pass
        else:
            if named in LIE_KINDS:
                return named
    raise UnknownLieError(
        f"{value!r} is not a lie this stub can tell. It tells "
        f"{', '.join(lie.value for lie in LIE_KINDS)}."
    )


def observe(
    mode: Mode | str,
    *,
    truth: Truth = DEFAULT_TRUTH,
    lie: Lie | str | None = None,
    seed: int = 0,
) -> Observation:
    """What the stub claims to have read. An `Observation`, never a write.

    Validation happens before anything is built, in this order: the mode, then
    the seed, then whether a named lie makes sense for that mode. A stub that
    built first and checked afterwards would have already produced the thing it
    was about to refuse.

    A `lie` handed to a mode that does not lie is refused rather than ignored.
    Ignoring it hides the caller's bug in the worst possible direction: they
    asked for a specific failure and would receive a clean read that passes
    every guard, and the test would go green on the strength of it.

    A `seed` handed to SUCCESS, PARTIAL or FAILURE is accepted and changes
    nothing, and that is not the same thing. Those modes have exactly one shape
    each - there is one way to read nothing - so the seed has nothing to choose
    between, rather than something to choose and no effect.
    """
    chosen = _mode(mode)
    # `type(...) is not` rather than `isinstance`, for the two reasons
    # `wall.py:174` gives about the same hazard. It rejects `bool` - which IS
    # an `int` in Python - and pyright does not flag it as redundant the way it
    # flags `isinstance` against an annotated parameter. The check is not
    # redundant at run time: annotations are not enforced, and a parametrised
    # test or a tool call arrives untyped. Exact type for `Truth` too: a
    # subclass could override `total_paise` and walk around every invariant
    # `__post_init__` just checked.
    if type(seed) is not int:
        raise TypeError(
            f"seed must be a whole number, not {type(seed).__name__}. "
            "`isinstance(True, int)` is True in Python, so a flag passed here "
            "would silently select the second lie."
        )
    if type(truth) is not Truth:
        raise TypeError(
            f"truth must be a Truth, not {type(truth).__name__}. Reaching for "
            "an attribute that is not there would fail later and further away, "
            "where the message no longer says what went wrong."
        )
    if lie is not None and chosen is not Mode.GARBAGE:
        raise ValueError(
            f"{chosen.value!r} mode does not lie, so {lie!r} cannot be told in "
            "it. Ignoring this would hand back a clean read to a caller who "
            "asked for a specific failure, and their test would pass on it."
        )

    if chosen is Mode.SUCCESS:
        return _success(truth)
    if chosen is Mode.GARBAGE:
        return _garbage(truth, _lie(lie, seed))
    if chosen is Mode.PARTIAL:
        return _partial(truth)
    return _failure()

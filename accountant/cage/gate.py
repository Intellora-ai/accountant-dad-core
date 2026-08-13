"""The one door from a draft to the cage. It carries facts and decides nothing.

WHY THIS IS A MODULE AND NOT FIVE LINES AT A CALL SITE
--------------------------------------------------------
`decision.py` weighs evidence. `wall.py` guards the write. Neither of them
knows what a `Draft` is, and neither of them should: the cage is meant to be
readable end to end by a person deciding whether to trust a refusal, and a cage
that reaches into the pipeline's own types is not.

So the translation lives here, on its own, and it has exactly one job: turn a
`Draft` plus the facts a caller actually looked up into a `Situation`, ask
`decision.decide`, and hand back what it said. It weighs nothing. It has no
threshold in it. Everything it reports as read was read, and everything it does
not know it says it does not know.

Five lines inline would have done the same work and would have been wrong for
one reason: there would then be no single place where "what was read" becomes
"what is claimed", and every future call site would invent its own answer to
the same question. `tests/test_gate.py` asserts by AST scan that this is the
only module in the package that calls `decide`.

THE THREE WORLD FACTS HAVE NO DEFAULTS, AND THAT IS THE POINT
---------------------------------------------------------------
`period_open`, `party_known` and `carries_gst` are parameters. This module
never derives them, never infers them and never guesses them.

The temptation is real and it is specific. An Indian financial year runs 1
April to 31 March, so `period_open` looks computable from the date on the bill.
It is not. Whether a company's books are open for a date is a fact about that
company's Tally, not about the calendar - a year can be closed early, and a
past year stays open until somebody shuts it. Computing it here would be an
inference wearing the costume of a fact, which is precisely what
`decision.Situation` forbids.

Measured before this module was written: there is NO period check anywhere in
this repository. TallyPrime itself refuses an out-of-financial-year date at the
write door and `accountant/tallyio/errors.py` already turns that refusal into a
plain sentence - so the fact exists, but only AFTER the attempt, and the cage
needs it BEFORE. Until something reads it, a caller passes `None`, the entry
blocks, and blocking is the correct answer to "nobody looked".
`docs/OWNER_WORK.md` records the gap.

A default would be worse than a wrong answer, because it would be supplied
silently at every call site that forgot. A caller who forgets gets a
`TypeError` here instead of a post there.

WHY THE MONEY FACTS DO HAVE A DEFAULT, AND WHY THAT IS SAFE
-------------------------------------------------------------
`net_paise` and the two balances default to `None`. A default is only safe when
forgetting it fails closed, and `net_paise` does: `None` means the figure was
never read, an unread figure makes `net_plus_tax_equals_gross` INDETERMINATE,
and an INDETERMINATE document law is a hard block in `decision.py`. The
dangerous default for `period_open` is `True`; there is no dangerous default
for a number nobody read.

THE TWO BALANCES ARE NO LONGER PART OF THAT SENTENCE, AS OF 2026-08-13.
`balance_delta_equals_entry` is a statement about the BOOKS, not about the
document, and before a write there is no after-balance to compare against - so
`decision.py` now expects it to be INDETERMINATE at `Moment.BEFORE_THE_WRITE`
and does not block on it. Omitting the two balances on a pre-write call
therefore fails OPEN, not closed. What holds the line instead is `moment`
itself, which has no default here and none in `Situation`: a caller who does not
say gets a `TypeError`, and a caller who says `AFTER_THE_WRITE` and supplies no
balance blocks exactly as before. See `decision.py`'s own docstring, section
"THREE LAWS ARE ABOUT THE BILL. THE FOURTH IS ABOUT THE BOOKS."

WHAT IT SCORES, AND WHY
-----------------------
Every field the record actually read is `confidence.EXACT`. The typed-text path
reads a text layer - there is no pixel and no estimate, so there is nothing to
be unsure about. Every field the record did NOT read is `value=None,
confidence=0.0`, carrying the record's own stated reason, because "we did not
read it" and "we are unsure about it" are the same fact and `wall.Field`
refuses to let them disagree.

Money that is not whole paise is neither coerced nor raised on. The field is
built UNREAD, the observation's lowest confidence falls to 0.0, and the entry
blocks with a sentence a person can read. `checks.amount_is_integer_paise`
exists because this value arrives wrong, and a traceback for an ordinary bill
is an outage where a refusal is a product.

NONE AND () ARE DIFFERENT, AND THE DIFFERENCE IS LOAD-BEARING
---------------------------------------------------------------
`conservation.lines_sum_to_total` reads an empty tuple as "the lines were read
and there were none", which is consistent with a zero total and contradictory
with any other. No reader in this repository can report line items at all -
`ExtractedRecord.line_items` defaults to `()` and nothing ever fills it - so an
empty tuple here means nobody looked, and this module passes `None`. Reading it
the other way would turn every un-itemised bill into a passing one.

WHAT IT DELIBERATELY DOES NOT COMPUTE
--------------------------------------
`net_paise` is a parameter and is never worked out as total minus tax. Both of
those numbers are already inputs to the same law, so a net derived from them
would be a number checked against itself, and `net_plus_tax_equals_gross` would
pass on every bill for ever while reporting that it had checked something.

`debit_paise` and `credit_paise` are the one amount this entry moves, reported
on both sides because that is what a one-amount voucher does. The law is
therefore satisfied by construction and catches nothing here. It is not
silenced: the day an entry carries two independently read sides, this is where
they arrive, and a law that is reported as unchecked is a law nobody can read
the result of.

WHAT THIS MODULE CANNOT DO, SAID SO NOBODY RELIES ON IT
---------------------------------------------------------
It cannot tell whether the facts it was handed are true. It can only tell
whether somebody actually looked. And it cannot see a bill misread
CONSISTENTLY - every figure scaled by ten - because every law still holds and
every field is still legible. That is failure mode F-02, and nothing here
pretends otherwise.

WHERE IT IS NOT WIRED, AND WHY
-------------------------------
It is not on `pipeline.run`'s posting path. That path has no reader producing
per-field confidence, no source for the period, and no inputs for three of the
four laws, so the gate could only ever say block - which would delete a working
path rather than add a guard to it. `tests/test_gate.py` pins that structurally
and states what has to land first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from accountant.cage import conservation
from accountant.cage.confidence import EXACT
from accountant.cage.decision import Decided, Moment, Situation, decide
from accountant.cage.wall import Field, Observation
from accountant.extract.adapter import NOT_FOUND, ExtractedRecord

if TYPE_CHECKING:  # pragma: no cover - a type, never a runtime dependency
    # Under `from __future__ import annotations` this name is only ever read by
    # the type checker. Importing it at run time would make the cage depend on
    # the pipeline, which imports the cage nowhere and must stay able to.
    from accountant.pipeline import Draft


def _stated(record: ExtractedRecord, name: str) -> str:
    """What the record says about where this field came from.

    Never blank. `wall.Field` refuses an empty source, and a record that states
    one would otherwise become a `ValueError` on its way to a person's screen -
    the exact shape of failure this module exists to avoid.
    """
    source = record.per_field_source.get(name, "").strip()
    return source or f"{NOT_FOUND}: the reader stated no source for {name}"


def _was_read(record: ExtractedRecord, name: str, value: object) -> bool:
    """Did the reader actually read this, or is it an absence with a label on it?

    Both halves, and neither is redundant. A value of `None` is an absence
    however it is labelled; a source that begins `not_found` is an absence even
    if some value came along with it. Either one alone fails open in the case
    the other catches.
    """
    return value is not None and not _stated(record, name).startswith(NOT_FOUND)


def _paise(value: object) -> int | None:
    """Whole paise, or nothing. Never a coercion.

    `type(...) is int` rather than `isinstance`: `isinstance(True, int)` is True
    and `True == 1`, so a flag passed where an amount belonged would balance a
    one-paisa entry. `conservation._paise` and `wall.LedgerEntry.decided` refuse
    a bool for the same reason; this matches them rather than inventing a third
    rule.
    """
    return value if type(value) is int else None


def _read_field(record: ExtractedRecord, name: str, value: object) -> Field:
    """One field of the observation: what was read, and how sure we are of it."""
    if not _was_read(record, name, value):
        return Field(value=None, confidence=0.0, source=_stated(record, name))
    return Field(value=value, confidence=EXACT, source=_stated(record, name))


def _money_field(record: ExtractedRecord, name: str, value: object) -> Field:
    """A money field, unread unless it arrived as whole paise.

    Not coerced and not raised on. `4200.0` means somebody did money in
    decimals and a bool means somebody passed a flag; neither is a low-quality
    amount, both are the absence of one.
    """
    if _paise(value) is None:
        return Field(
            value=None,
            confidence=0.0,
            source=f"{NOT_FOUND}: {name} arrived as {type(value).__name__}, "
            "which is not a whole number of paise",
        )
    return _read_field(record, name, value)


def _line_paise(record: ExtractedRecord) -> tuple[int, ...] | None:
    """The line amounts, or `None` when nobody read any.

    `None` and `()` are different to `conservation.lines_sum_to_total` and the
    difference decides whether an un-itemised bill passes or is refused. No
    reader in this repository fills `line_items`, so an empty tuple here is an
    absence of reading rather than an absence of lines.
    """
    if not record.line_items:
        return None
    amounts = tuple(_paise(item.amount_paise) for item in record.line_items)
    if any(amount is None for amount in amounts):
        # One unreadable line makes the sum unreadable. Dropping it would
        # produce a total that is short by exactly the amount nobody could read.
        return None
    return tuple(amount for amount in amounts if amount is not None)


def observed(draft: Draft) -> Observation:
    """What this draft's reader says the bill says. Inert, and postable by nobody.

    Public so a caller - and a test - can look at the evidence without asking
    for a decision about it. `Observation` has no method that writes, and
    `tests/test_the_wall.py` asserts it never grows one.
    """
    record = draft.record
    return Observation(
        date=_read_field(record, "date", record.date),
        party=_read_field(record, "party", record.party),
        total_paise=_money_field(record, "total_paise", record.total_paise),
        tax_paise=_money_field(record, "tax_paise", record.tax_paise),
        line_paise=_line_paise(record),
    )


def gate(
    draft: Draft,
    *,
    moment: Moment,
    party_known: bool | None,
    period_open: bool | None,
    carries_gst: bool | None,
    questions_asked: int,
    net_paise: int | None = None,
    balance_before_paise: int | None = None,
    balance_after_paise: int | None = None,
    ambiguous_fields: tuple[str, ...] = (),
) -> Decided:
    """Post, ask or block - as decided by `decision.decide`, never by this.

    Keyword-only, all of it. Six of these arguments are `bool | None` or
    `int | None` and three of them mean the opposite of each other; a
    positional call site is one reordering away from telling the cage the
    period is open when it meant the party is known.

    `moment`, `party_known`, `period_open` and `carries_gst` HAVE NO DEFAULTS. A
    caller that has not looked passes `None` for the three facts, which blocks.
    See the module docstring for why `None` is not the same as `False` and why
    neither is computed here.

    `moment` is not a fact about the bill - it is which side of the write the
    caller is standing on, and it decides whether `balance_delta_equals_entry`
    being unanswerable is expected or is a failure to look. It is passed
    straight through and never worked out from whether a balance arrived,
    because a balance that is absent because it cannot exist yet and one that is
    absent because the caller forgot are the same `None`.

    `questions_asked` is the caller's own count. A default of 0 would be "nobody
    has asked anything yet" asserted on behalf of a caller who did not say so,
    and it would quietly authorise a sixth question past `QUESTION_CAP`.

    `ambiguous_fields` names the fields the READER could read more than one way.
    It defaults to empty because no reader here has a channel to report
    ambiguity at all: the empty tuple means "nothing was reported", never "there
    is nothing ambiguous on this bill". The day a reader can say, it passes them.

    Every money figure the caller supplies goes through `_paise` on its way in.
    `conservation` raises on a float, and a caller's mistyped balance becoming a
    traceback in front of somebody's bill is the failure this whole layer is
    written against - so a figure that is not whole paise arrives as unread, its
    law comes back INDETERMINATE, and the entry blocks with a sentence.
    """
    seen = observed(draft)
    amount = _paise(seen.total_paise.value)
    laws = conservation.run(
        # One amount, both sides. See the module docstring: this law is
        # satisfied by construction for a one-amount voucher, and it is here so
        # that a two-sided entry has somewhere to arrive.
        debit_paise=amount,
        credit_paise=amount,
        line_paise=seen.line_paise,
        total_paise=amount,
        # Never `amount - tax`. A number derived from the law's own inputs
        # checks itself and passes for ever.
        net_paise=_paise(net_paise),
        tax_paise=_paise(seen.tax_paise.value),
        gross_paise=amount,
        balance_before_paise=_paise(balance_before_paise),
        balance_after_paise=_paise(balance_after_paise),
    )
    return decide(
        Situation(
            observation=seen,
            conservation=laws,
            party_known=party_known,
            period_open=period_open,
            carries_gst=carries_gst,
            questions_asked=questions_asked,
            moment=moment,
            # The legs come from the voucher because that is where a proposed
            # account lives; the AMOUNT never does. `decision.decide` builds the
            # entry from what was READ, and the voucher's own copy of the total
            # is derived from the same field - a copy is not a second source,
            # and checking one against the other would look like corroboration.
            debit_account=draft.voucher.debit_account,
            credit_account=draft.voucher.credit_account,
            ambiguous_fields=ambiguous_fields,
        )
    )

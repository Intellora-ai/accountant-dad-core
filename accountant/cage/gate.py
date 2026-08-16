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

`pdf_repaired` is a fourth parameter with no default and it is NOT one of the
three world facts, so it is worth saying which it is. The three above are facts
about the customer's books that somebody has to look up. This one is a fact
about our own processing - did `accountant/extract/textlayer.py` have to mend
the bytes before anything could be read off them - and the caller always knows
it. `None` means "not a PDF, nothing to repair" here rather than "nobody
looked", which is the opposite of what `None` means for the three above, and it
is written down in `decision.Situation` where the field lives.

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
Every field the record actually read carries THE READER'S OWN SCORE, and only a
reader that stated none falls back to `confidence.EXACT`. Every field the record
did NOT read is `value=None, confidence=0.0`, carrying the record's own stated
reason, because "we did not read it" and "we are unsure about it" are the same
fact and `wall.Field` refuses to let them disagree.

THIS PARAGRAPH USED TO SAY `EXACT` FOR EVERY READ FIELD, and it was true when
the only two tiers were a text layer and a person's typing - no pixel, no
estimate, nothing to be unsure about. It stopped being true on 2026-08-13, when
`registry.DEFAULT_BACKEND` became `ladder` and the picture rung went live on the
upload path: that rung reads a party at 0.08, and this module was telling the
decision layer the guess was certain. `ASK_FLOOR` and `AUTO_POST_FLOOR` are both
about exactly that number, so inventing it here defeated both of the owner's
bands at once. `ExtractedRecord.per_field_confidence` is where the real number
now arrives, and `_sure` below is the whole of the change.

The fallback is the part that is still wrong, and it is written down rather than
left to be found: a reader that states nothing is read as certain, which is the
direction a missing statement must not fail in. Changing it to 0.0 blocks every
backend with no scoring machinery, which is a deliberate change with its own
measurement rather than a side effect of this one. Nothing about IDENTITY rests
on it - `ExtractedRecord.read_exactly` requires a STATED `EXACT`, so a silent
backend never names a supplier however this module scores it.

Money that is not whole paise is neither coerced nor raised on. The field is
built UNREAD, the observation's lowest confidence falls to 0.0, and the entry
blocks with a sentence a person can read. `checks.amount_is_integer_paise`
exists because this value arrives wrong, and a traceback for an ordinary bill
is an outage where a refusal is a product.

NONE AND () ARE DIFFERENT, AND THE DIFFERENCE IS LOAD-BEARING
---------------------------------------------------------------
`conservation.lines_sum_to_total` reads an empty tuple as "the lines were read
and there were none", which is consistent with a zero total and contradictory
with any other. An empty tuple here means nobody looked, and this module passes
`None`. Reading it the other way would turn every un-itemised bill into a
passing one.

THIS PARAGRAPH SAID "NO READER IN THIS REPOSITORY CAN REPORT LINE ITEMS AT ALL"
AND THAT STOPPED BEING TRUE. `textlayer.py:1427` fills `line_items`, and the
claim was repeated out of this docstring for long enough to be used as the
reason a whole class of bills could never pass. MEASURED 2026-08-15 on
`tests/test_textlayer.py::BILL` through `TextLayerReader.extract`:

    line_items   ('PORTLAND CEMENT 53 GRADE', 100000)
                 ('PACKAGING MATERIAL',         4624)
                 sum                          104624   = the net, exactly

So the law is answerable on the text-layer tier and it PASSES there. It is
still `None` on the picture tier, which reports words rather than rows, and on
a typed sentence, which has no rows to report - and on both of those it is
INDETERMINATE and blocks, correctly.

The lesson is the one this file keeps having to relearn: a docstring stating
what some OTHER module cannot do goes stale silently, because nothing fails when
the other module gains the ability. Where it matters, assert it.

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
It is not on `pipeline.run`'s posting path. That path still has no source for
the period and no inputs for three of the four laws, so the gate could only ever
say block - which would delete a working path rather than add a guard to it.
`tests/test_gate.py` pins that structurally and states what has to land first.

ONE OF THE THREE REASONS IS GONE AS OF 2026-08-13: "no reader producing
per-field confidence" was the first item on that list and is no longer true.
`ExtractedRecord` carries a score per field, and the picture rung and the text
layer both state one. The period and the conservation inputs are what remain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from accountant.cage import conservation
from accountant.cage.confidence import EXACT
from accountant.cage.decision import (
    Decided,
    DocumentType,
    Moment,
    Situation,
    decide,
)
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


def _sure(record: ExtractedRecord, name: str) -> float:
    """How sure the READER said it was about this field.

    THE READER'S OWN NUMBER, 2026-08-13, AND IT USED TO BE INVENTED HERE. This
    module stamped `EXACT` on every field any record said it had read, and the
    module docstring justified it - the two tiers that existed then read
    characters, so there was nothing to be unsure about. The picture rung went
    live on the upload path that morning, it reads a party at 0.08,
    `ExtractedRecord` had no column to carry the 0.08 in, and this line then told
    the decision layer the guess was certain.

    `EXACT` when the reader stated nothing, which preserves this module's
    behaviour for every record that carries no scores. That fallback is the one
    thing here that is still wrong and the module docstring says so; nothing
    about identity depends on it, because `ExtractedRecord.read_exactly` demands
    a STATED `EXACT` and reads silence as an estimate.
    """
    stated = record.confidence_of(name)
    return EXACT if stated is None else stated


def _tiers(record: ExtractedRecord) -> tuple[str, ...]:
    """Which reading tiers this record's four fields came from, deduplicated.

    Owner decision 2, 2026-08-13. `decision.decide` needs the tier to answer its
    fourth auto-post condition and must not import a reader to get it; this is
    the seam, and it is three lines because the fact is already on the record -
    `per_field_source` is written by whichever rung read each field.

    A FIELD NOBODY READ STATES NO TIER, AND UNTIL 2026-08-16 IT STATED
    `not_found: ...` - which is on no allowlist and therefore capped the bill at
    ASK. This docstring argued that cost nothing real, in these words: "an
    unread field is confidence 0.0 and blocks two layers earlier". THAT
    SENTENCE WAS THE WHOLE JUSTIFICATION AND `DocumentType` MADE IT FALSE.

    It is still true of a field that APPLIES: an unread date on a real invoice
    scores 0.0, `lowest_confidence_where` counts it, and `_TOO_UNSURE` blocks
    below `ASK_FLOOR` before anything here matters. It is NOT true of a field
    that does not apply - `decision._tax_applies` and `decision._date_applies`
    now drop those from the minimum on purpose, so nothing downstream charges
    for them any more, and this line was the one place still doing it.

    MEASURED 2026-08-16 on `paid Sharma Traders 4200 for cement`: `typed_text`
    is on `AUTO_POST_ALLOWED_TIERS` by the owner's ruling of 2026-08-13, both
    fields a person typed read 1.0, and the bill still could not post because
    its absent tax and absent date each stamped a `not_found` tier. That is the
    same regression the ruling was made to end - the note above
    `AUTO_POST_ALLOWED_TIERS` records `demo_safety_cage.py` going from
    `posted 3` to `posted 0` on inputs that are all typed sentences - arriving
    by a different route.

    SO ONLY WHAT WAS READ STATES A TIER. This is narrower than it looks and it
    opens nothing: a field read badly still states the rung that read it, so
    `free_ocr` still caps at ASK however sure it claims to be, and a record that
    read NOTHING yields an empty tuple, which `_may_auto_post` already refuses.
    The unread-but-applicable case is not excused here - it is refused one layer
    earlier and harder, by confidence.

    ORDER IS `FIELDS` ORDER AND DUPLICATES ARE DROPPED, via `dict.fromkeys`
    rather than a set, so the same record produces the same tuple on every run.
    A set would reorder it per process and put a different sentence in the log
    for the same bill.
    """
    return tuple(
        dict.fromkeys(
            stated
            for stated in (_stated(record, name) for name in ExtractedRecord.FIELDS)
            if not stated.startswith(NOT_FOUND)
        )
    )


def _read_field(record: ExtractedRecord, name: str, value: object) -> Field:
    """One field of the observation: what was read, and how sure we are of it."""
    if not _was_read(record, name, value):
        return Field(value=None, confidence=0.0, source=_stated(record, name))
    return Field(
        value=value, confidence=_sure(record, name), source=_stated(record, name)
    )


def _repaired(record: ExtractedRecord, caller: bool | None) -> bool | None:
    """Was this document mended before anything was read off it?

    THE RECORD WINS WHEN IT SAYS YES, 2026-08-13. The caller's word used to be
    the only word, and measured that day: no shipped caller had one. The reader
    that mends the bytes is `extract/textlayer.py`, `TextLayerReader.extract`
    dropped the fact while building the record, and `demo_safety_cage.py` - the
    only non-test call site of `decide` outside this module - passes `None`,
    which is the value that grants the full post. A ceiling nothing can reach
    is not a ceiling.

    The caller is still asked, and still believed when it says True: a tier
    that mended bytes this reader never saw is real, and the record cannot know
    about it. What it can no longer do is talk the evidence down. The two ways
    of being wrong are not symmetrical - a caller wrongly reporting a repair
    costs a question, and a caller wrongly reporting none costs a posted
    voucher off bytes somebody may have edited.

    THE RECORD ANSWERS ONLY FOR A CALLER THAT SAID NO REPAIR, and that is why
    the condition is written on the caller rather than on the record.
    `decision._repair_blocks` BLOCKS anything that is not `True`, `False` or
    `None`, because a string or an int there is a caller whose plumbing is
    wrong - a fact about the caller, which no fact about the document answers.
    An `or` here would have coerced that junk to `True` and turned its block
    into a question. So a caller that said anything but "no repair" keeps
    exactly what it said, and the type rule stays in the one place that has it.
    """
    said_no_repair = caller is None or caller is False
    return True if said_no_repair and record.pdf_repaired is True else caller


def _money_field(record: ExtractedRecord, name: str, value: object) -> Field:
    """A money field, unread unless it arrived as whole paise.

    Not coerced and not raised on. `4200.0` means somebody did money in
    decimals and a bool means somebody passed a flag; neither is a low-quality
    amount, both are the absence of one.

    A MISSING VALUE IS NOT A TYPE ERROR, corrected 2026-08-13. This asked
    `_paise(value) is None` first, and `None` is not whole paise - so every
    REFUSED amount reached the `Observation` wearing "total_paise arrived as
    NoneType" and the reader's own sentence was thrown away. The owner's
    wording survived on `record.per_field_source`, which is what the page
    prints, so a person still saw it and nothing reading the observation did.
    The type sentence is for a value that ARRIVED and was not paise; that is a
    different fact about a different failure, and it is still said.
    """
    if value is not None and _paise(value) is None:
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


def _lines_add_up_to(
    *, net: int | None, tax: int | None, gross: int | None
) -> int | None:
    """The figure the line items are supposed to equal. `None` when nobody knows.

    THE DEFECT THIS REPLACES, AND IT WAS ARITHMETIC RATHER THAN A THRESHOLD.
    `conservation.run` was called with `total_paise=amount`, where `amount` is
    the GROSS. Line items on a GST bill are PRE-TAX, so the law was comparing a
    net sum against a gross total and calling the difference an error. On the
    ordinary Indian bill that difference is exactly the tax.

    MEASURED 2026-08-15 on `tests/test_textlayer.py::BILL`, read end to end
    through `TextLayerReader.extract`, which is the tier allowed to auto-post:

        item rows      1,000.00 + 46.24  =  1,046.24
        SUBTOTAL                            1,046.24   <- what they must equal
        GST                                   188.32
        TOTAL                               1,234.56   <- what they were checked against

    The rows summed to the net EXACTLY and the law reported them short by 18832
    paise - the tax, to the paisa. A bill whose arithmetic was perfect failed,
    and the sentence a person read named a discrepancy that was not on the paper.

    THE THREE ANSWERS, AND THE THIRD IS THE ONE THAT MATTERS:

        net was read        compare against the NET. This is the real law.
        net unread, tax 0   net EQUALS gross when there is no tax, so the gross
                            is the right comparand and nothing is assumed. This
                            is a READ zero - `seen.tax_paise.value` is `None`
                            when the field was never read, and this branch
                            requires the integer 0.
        anything else       `None`, which `lines_sum_to_total` turns into
                            INDETERMINATE, which BLOCKS.

    The third is deliberately not clever. A net could be inferred as gross minus
    tax, and that inference is exactly what this module's own docstring forbids:
    a number derived from the law's own inputs is checked against itself and
    passes for ever. So an unread net on a taxed bill is refused and a person
    opens it - the direction the owner's exchange rate points, 100 false blocks
    against 1 silent wrong post.
    """
    if net is not None:
        return net
    if tax == 0:
        return gross
    return None


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
    pdf_repaired: bool | None,
    questions_asked: int,
    net_paise: int | None = None,
    balance_before_paise: int | None = None,
    balance_after_paise: int | None = None,
    ambiguous_fields: tuple[str, ...] = (),
    document_type: DocumentType = DocumentType.INVOICE,
) -> Decided:
    """Post, ask or block - as decided by `decision.decide`, never by this.

    Keyword-only, all of it. Six of these arguments are `bool | None` or
    `int | None` and three of them mean the opposite of each other; a
    positional call site is one reordering away from telling the cage the
    period is open when it meant the party is known.

    `moment`, `party_known`, `period_open`, `carries_gst` and `pdf_repaired`
    HAVE NO DEFAULTS. A caller that has not looked passes `None` for the three
    world facts, which blocks. See the module docstring for why `None` is not the
    same as `False` and why neither is computed here.

    `pdf_repaired` has no default for a different reason from the other four, and
    it is the reason a default here would be worst of all: its `None` means
    "not a PDF, nothing to repair" and grants the full post. A default would
    hand every caller that forgot exactly the permission the field exists to
    withhold, silently. `True` caps the outcome at ASK - owner decision,
    2026-08-13 - and it is a ceiling, not a refusal.

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
    tax = _paise(seen.tax_paise.value)
    net = _paise(net_paise)
    laws = conservation.run(
        # One amount, both sides. See the module docstring: this law is
        # satisfied by construction for a one-amount voucher, and it is here so
        # that a two-sided entry has somewhere to arrive.
        debit_paise=amount,
        credit_paise=amount,
        line_paise=seen.line_paise,
        # THE NET, NOT THE GROSS. See `_lines_add_up_to`: the rows are pre-tax,
        # so measuring them against `amount` reported every correct GST bill as
        # short by exactly its tax.
        total_paise=_lines_add_up_to(net=net, tax=tax, gross=amount),
        # Never `amount - tax`. A number derived from the law's own inputs
        # checks itself and passes for ever.
        net_paise=net,
        tax_paise=tax,
        gross_paise=amount,
        balance_before_paise=_paise(balance_before_paise),
        balance_after_paise=_paise(balance_after_paise),
    )
    return decide(
        Situation(
            observation=seen,
            conservation=laws,
            # WHAT KIND OF DOCUMENT THIS IS, and it decides which laws even
            # apply. Defaulted to INVOICE - the strict side - so a caller
            # that has not heard of document types is judged exactly as it
            # was before 2026-08-16. See `decision.DocumentType`.
            document_type=document_type,
            # The SAME `amount` the laws above were run on, passed from the same
            # variable rather than read off the observation a second time. That
            # is the whole content of the field: `decide` refuses to write a
            # number its checks did not see, and a second read of the same
            # source would be a copy pretending to be a witness.
            checked_paise=amount,
            party_known=party_known,
            period_open=period_open,
            carries_gst=carries_gst,
            # The RECORD'S answer, and the caller's only when the record has
            # none. See `_repaired` below - this used to be the caller's word
            # alone, and no shipped caller had one.
            pdf_repaired=_repaired(draft.record, pdf_repaired),
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
            # NOT a parameter of this function, and that is the point: the
            # record already says which rung read each field, so asking the
            # caller would invite an answer that disagrees with the reading it
            # is about. Owner decision 2, 2026-08-13 - see `_tiers`.
            reading_tiers=_tiers(draft.record),
        )
    )

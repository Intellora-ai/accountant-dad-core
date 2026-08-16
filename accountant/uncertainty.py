"""One plain question about one uncertain thing on a bill.

WHAT THIS IS FOR
----------------
When the reader is not sure about something, the person used to see a general
"this needs review" message. That message names nothing, so the person cannot
help even when they are holding the bill and can read it in a second. This asks
about the ONE thing that is unclear, in words that need no accounting.

WHAT IT IS NOT
--------------
It is not a way past any safety rule. An answer here is NEW INFORMATION about
the bill, exactly like a better reading would be, and it goes through the same
normalizer, the same checks, the same open-books question and the same cage as
anything else. `pipeline.answer` says the same thing about account answers and
has since 2026-08-09. Nothing in this module writes to Tally, and nothing here
decides to post.

THREE REASONS TO ASK, AND THEY ARE NOT THE SAME REASON
-------------------------------------------------------
    NOT_READ         nothing was read for this. Ask the person to type it.
    TWO_READINGS     the bill states two different things. Show BOTH and ask
                     which. Never pick one.
    NOT_SURE_ENOUGH  something was read, but not well enough to act on alone.

The code that decides which reason applies is separate from the words the
person reads - `reason_for_question` is ours, `plain_question` is theirs. A
person should never be shown a reason code, and a log should never be asked to
parse a sentence.

THE OPTIONS ARE NEVER INVENTED
-------------------------------
Every choice offered comes from the document: a value that was read, a
candidate the reader found and could not choose between, or what the person
themself typed. There is no "did you mean" and no rounded-up suggestion. An
invented choice is a guess wearing a person's authority, and it would be the
worst kind here because they would click it.

"I AM NOT SURE" IS A REAL ANSWER
---------------------------------
It is offered on every question and it never produces a value. The bill stays
unresolved, which means it is held or refused by the layers below - the same
place it would have been without the question. A person who does not know must
never be turned into a source of certainty.

NOTHING IS REMEMBERED ACROSS DOCUMENTS
---------------------------------------
An answer belongs to the document it was given about. `FieldQuestion` carries
`document_id` for exactly that reason. Reusing "the total was 4,200" on the
next bill from the same supplier would be inventing a reading for a page nobody
looked at.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Final

from accountant import questions as Q
from accountant.cage.decision import AUTO_POST_FLOOR
from accountant.extract.adapter import NOT_FOUND, ExtractedRecord
from accountant.schema import HUMAN_ANSWER

#: What the person clicked, when what they clicked is not a value.
NOT_SURE: Final = "__not_sure__"
TYPE_IT: Final = "__type_it__"

#: OUR words for why we asked, never theirs. Kept apart from `plain_question`
#: so a log can be counted and a person can be understood, which are two
#: different jobs that one string cannot do.
NOT_READ: Final = "NOT_READ"
TWO_READINGS: Final = "TWO_READINGS"
NOT_SURE_ENOUGH: Final = "NOT_SURE_ENOUGH"

#: The order questions are asked in. Money first because it is the thing a
#: wrong answer costs most, then who it was, then when. `tax_paise` is last
#: because a bill can honestly have none.
ASK_ABOUT: Final[tuple[str, ...]] = ("total_paise", "party", "date", "tax_paise")

#: BELOW THIS WE ASK. It is `AUTO_POST_FLOOR`, the owner's existing 0.95, and
#: not a new number: "not sure enough to act on alone" is the same line the
#: cage already draws between posting by itself and checking with a person.
#: Importing it means the two cannot drift apart.
ASK_BELOW: Final = AUTO_POST_FLOOR


@dataclass(frozen=True)
class Choice:
    """One thing the person can pick. `label` is read, `value` is meant."""

    label: str
    value: str


@dataclass(frozen=True)
class FieldQuestion:
    """One question about one field of one document.

    `options` may be empty - a field nobody read has nothing to offer, so the
    person types. It is never padded to look like a multiple choice.
    """

    document_id: str
    field_name: str
    plain_question: str
    options: tuple[Choice, ...]
    allows_custom_answer: bool
    allows_not_sure: bool
    reason_for_question: str
    source_evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.plain_question.strip():
            raise ValueError(f"question about {self.field_name!r} has no words")
        if not self.allows_not_sure:
            raise ValueError(
                f"question about {self.field_name!r} does not offer "
                '"I am not sure". Every question must.'
            )
        if not self.options and not self.allows_custom_answer:
            raise ValueError(
                f"question about {self.field_name!r} offers nothing to pick and "
                "no way to type. It cannot be answered."
            )


@dataclass(frozen=True)
class Applied:
    """What became of one answer.

    `record` is None exactly when `refusal` is set, so a caller cannot read a
    rejected answer as an accepted one by looking at the wrong field.
    """

    record: ExtractedRecord | None
    refusal: str
    resolved: bool


def _read(record: ExtractedRecord, field: str) -> object:
    return getattr(record, field, None)


def _was_read(record: ExtractedRecord, field: str) -> bool:
    """Did the reader state a value AND a source that is not an absence?"""
    source = record.per_field_source.get(field, "")
    return _read(record, field) is not None and not source.startswith(NOT_FOUND)


def _candidates(record: ExtractedRecord, field: str) -> tuple[str, ...]:
    """Readings the reader found and could not choose between.

    Empty for every reader shipping today - see this module's note in
    `docs`/the report. The carrier exists so a reader that DOES keep its
    competing readings can offer them without this module guessing.
    """
    found = record.per_field_candidates.get(field, ())
    return tuple(str(one) for one in found)


def reason_to_ask(record: ExtractedRecord, field: str) -> str:
    """Our reason code for this field, or "" when there is nothing to ask.

    Order matters: two readings is a stronger fact than a low score, and a
    field nobody read cannot also be a field read badly.
    """
    if len(_candidates(record, field)) > 1:
        return TWO_READINGS
    if not _was_read(record, field):
        return NOT_READ
    if record.per_field_confidence.get(field, 0.0) < ASK_BELOW:
        return NOT_SURE_ENOUGH
    return ""


def _shown(field: str, value: object) -> str:
    """One value, in the words a person reads it in."""
    if field in ("total_paise", "tax_paise") and isinstance(value, int):
        return Q.rupees(value)
    if field == "date" and isinstance(value, datetime.date):
        return Q.plain_date(value)
    return str(value)


_ASKING: Final[dict[str, dict[str, str]]] = {
    "total_paise": {
        NOT_READ: "I cannot find the total on this bill. Please type it.",
        TWO_READINGS: "I found two different totals on this bill. Which one is right?",
        NOT_SURE_ENOUGH: "What is the total amount on this bill?",
    },
    "tax_paise": {
        NOT_READ: (
            "I cannot find the tax on this bill. Please type it, or say there is none."
        ),
        TWO_READINGS: (
            "I found two different tax amounts on this bill. Which one is right?"
        ),
        NOT_SURE_ENOUGH: "How much tax is on this bill?",
    },
    "party": {
        NOT_READ: "I cannot find who this bill is from. Please type their name.",
        TWO_READINGS: "I found two names on this bill. Which one did you pay?",
        NOT_SURE_ENOUGH: "Who is this bill from?",
    },
    "date": {
        NOT_READ: (
            "I cannot find the date on this bill. Please type it like 2026-08-12."
        ),
        TWO_READINGS: "I found two different dates on this bill. Which one is right?",
        NOT_SURE_ENOUGH: "What date is on this bill?",
    },
}

_TYPE_IT_LABEL: Final[dict[str, str]] = {
    "total_paise": "Type a different amount",
    "tax_paise": "Type a different amount",
    "party": "Type a different name",
    "date": "Type a different date",
}

_NOT_SURE_LABEL: Final = "I am not sure"


def question_for(
    document_id: str, record: ExtractedRecord, field: str
) -> FieldQuestion | None:
    """The one question about this field, or None when nothing is unclear."""
    reason = reason_to_ask(record, field)
    if not reason:
        return None
    words = _ASKING.get(field)
    if words is None:
        # A field nobody has written words for. Saying nothing is better than
        # asking in our vocabulary, and the layers below still hold the bill.
        return None

    evidence = _candidates(record, field)
    options: list[Choice] = []
    if reason is TWO_READINGS or len(evidence) > 1:
        options = [Choice(label=one, value=one) for one in evidence]
    elif reason is NOT_SURE_ENOUGH and _was_read(record, field):
        shown = _shown(field, _read(record, field))
        evidence = (shown,)
        options = [Choice(label=shown, value=shown)]

    options.append(Choice(label=_NOT_SURE_LABEL, value=NOT_SURE))
    options.append(Choice(label=_TYPE_IT_LABEL[field], value=TYPE_IT))
    return FieldQuestion(
        document_id=document_id,
        field_name=field,
        plain_question=words[reason],
        options=tuple(options),
        allows_custom_answer=True,
        allows_not_sure=True,
        reason_for_question=reason,
        source_evidence=evidence,
    )


def outstanding(
    record: ExtractedRecord, answered: frozenset[str] = frozenset()
) -> tuple[str, ...]:
    """Every field still unclear, in the fixed asking order."""
    return tuple(
        field
        for field in ASK_ABOUT
        if field not in answered and reason_to_ask(record, field)
    )


def next_question(
    document_id: str, record: ExtractedRecord, answered: frozenset[str] = frozenset()
) -> FieldQuestion | None:
    """ONE question, or None when nothing is left to ask.

    One at a time is not a display choice. A person handed four questions about
    one bill answers the easy ones and guesses the rest, and a guess entered by
    a person is indistinguishable from a reading afterwards.
    """
    for field in outstanding(record, answered):
        question = question_for(document_id, record, field)
        if question is not None:
            return question
    return None


def apply_answer(record_in: ExtractedRecord, field: str, given: str) -> Applied:
    """Put one answer through the existing normalizer for that kind of thing.

    NOTHING IS PARSED HERE, AND THIS MODULE CANNOT SEE A PARSER.
    `ExtractedRecord.with_answer` does the work, inside `accountant/extract/`,
    using the same `paise_or_none` and `read_date` the readers are judged by. A
    second parser here would be a second opinion about what characters mean and
    this repository has paid for that four times - and reaching for those two
    names directly would have broken the D-30 boundary
    `tests/test_adapter_contract.py` holds, which permits the contract from that
    package and nothing else.
    """
    if given == NOT_SURE:
        # NOT A VALUE, AND NEVER TURNED INTO ONE. The record comes back exactly
        # as it was, so the field is still unresolved and every layer below
        # still sees a bill it cannot post on its own.
        return Applied(record=None, refusal="", resolved=False)

    record, refusal = record_in.with_answer(field, given)
    if record is None:
        return Applied(record=None, refusal=refusal, resolved=False)
    return Applied(record=record, refusal="", resolved=True)


def confirmation(
    party: str, amount_paise: int, when: datetime.date, company: str
) -> str:
    """The last thing a person reads before anything is saved.

    Three facts and a promise, in that order, and no word that needs
    accounting. It names the company because a person with two companies open
    has exactly one way to catch the wrong one, and this is it.
    """
    return (
        f"We will save this bill in Tally under {company} as:\n"
        f"\n"
        f"From: {party}\n"
        f"Amount: {Q.rupees(amount_paise)}\n"
        f"Date: {Q.plain_date(when)}\n"
        f"\n"
        f"{NOTHING_SAVED_YET}"
    )


# ---- the last screen: what will be saved, and what is still unclear ---------

#: THE PROMISE, WRITTEN ONCE. Both the old helper and `confirmation_for` end
#: with this exact sentence, so the two can never come to say different things
#: about the one fact that matters most on this screen.
NOTHING_SAVED_YET: Final = "Nothing will be saved until you confirm."

#: SAID WHEN THE DOCUMENT CARRIED NO NUMBER OF ITS OWN. It is a statement about
#: the PAGE, not about us: the supplier did not print one. Never blank, because
#: a blank line beside a label reads as "we lost it".
NO_INVOICE_NUMBER: Final = "None from the source"

#: THREE DIFFERENT NUMBERS THAT ARE CONSTANTLY CONFUSED, so this file names all
#: three and never lets one stand in for another:
#:
#:     supplier invoice number  the supplier printed it on their bill
#:     app reference            we made it up, to track this one attempt
#:     Tally number             Tally makes it up, and only AFTER it saves
#:
#: The third cannot be shown, ever, because at this moment it does not exist.
#: Printing a placeholder that looks like one would be a fabricated record.
TALLY_ASSIGNS_LATER: Final = "Tally will assign one after saving"

#: What a person calls each field when we ask them to check it. `ASK_ABOUT`'s
#: keys are ours; these are theirs.
_PLAIN_FIELD: Final[dict[str, str]] = {
    "total_paise": "the total",
    "party": "who it is from",
    "date": "the date",
    "tax_paise": "the tax",
}

#: Plain words for each kind of document. Presentation only - it renames
#: nothing and decides nothing.
#:
#: THERE IS NO "SALE" OR "PURCHASE" HERE AND NONE IS INVENTED. This repository
#: has no transaction-type concept: no `TransactionType`, no `transaction_type`
#: field, nothing that says which side of a trade this is. What it has is
#: `decision.DocumentType`, which says what KIND OF PAPER was read. Printing
#: "Sale" would be us deciding an accounting fact nobody measured.
_PLAIN_KIND: Final[dict[str, str]] = {
    "invoice": "A bill from a supplier",
    "credit_note": "A refund note from a supplier",
    "typed_expense_note": "A payment you typed in",
    "non_invoice_expense_note": "A payment read off a document",
    "unsupported": "Something I could not recognise",
}


@dataclass(frozen=True)
class Confirmation:
    """What we would save, or why we cannot ask yet.

    `ready` IS THE WHOLE SAFETY PROPERTY OF THIS TYPE. It is False whenever any
    field a person still has to check is unresolved, and when it is False the
    text deliberately does NOT contain the sentence that invites a person to
    confirm. A screen cannot accidentally offer a Confirm button on a bill we
    could not read, because there is nothing on the object to render it from.

    IT DECIDES NOTHING AND SAVES NOTHING. It is four strings and a flag. It
    holds no client, calls nothing, and cannot reach Tally, a reader or the
    cage - which all still run, afterwards, on the record itself.
    """

    ready: bool
    said: str
    still_to_check: tuple[str, ...]
    corrected: tuple[str, ...]


def _corrected_fields(record: ExtractedRecord) -> tuple[str, ...]:
    """Which fields carry a person's own answer rather than a reading.

    Taken off `per_field_source`, which `ExtractedRecord.with_answer` stamps
    `human_answer` - so this is the record's own evidence about where each
    value came from, not a second list that could drift from it.
    """
    return tuple(
        field
        for field in ASK_ABOUT
        if record.per_field_source.get(field, "") == HUMAN_ANSWER
    )


def confirmation_for(
    record: ExtractedRecord,
    *,
    company: str,
    document_type: str = "",
    invoice_number: str | None = None,
    app_reference: str = "",
    safety: SafetyVerdict | None = None,
) -> Confirmation:
    """The last screen, built from what was read and what a person corrected.

    EVERY VALUE COMES OFF THE RECORD. Nothing here re-parses, re-reads or
    recomputes: the amount is the amount the normalizer produced, the date is
    the date `read_date` returned, and a corrected field is corrected because
    `with_answer` put it there. This function formats; it does not decide.

    IT REFUSES BEFORE IT DESCRIBES. If `outstanding` still names a field, the
    answer is not-ready and the text says what needs checking - it does NOT
    list an amount and a date beside an invitation to confirm. A person shown
    a tidy summary assumes the summary is complete, and half of one is worse
    than none.

    `invoice_number` AND `app_reference` ARE PARAMETERS RATHER THAN RECORD
    FIELDS, deliberately. `ExtractedRecord` carries neither today -
    `free_ocr._read_invoice_number` reads a supplier's number and it is
    dropped at the record boundary - and adding fields to the record is a
    change to the readers. The caller that has them passes them; a caller that
    has neither still gets an honest screen that says so.

    `safety` JOINED 2026-08-16 AND IT OUTRANKS EVERY FIELD BEING RESOLVED.
    Answering the questions was never the whole test: the cage's conservation
    laws, the open-books question, the duplicate claim and backend
    availability are all checked elsewhere, and any one of them refusing means
    no ready screen here. Passed `None`, this behaves exactly as it did before
    - which is why every caller written for Task 7 still works - but a caller
    that HAS a verdict cannot have it ignored.
    """
    if safety is not None and not safety.ready:
        # THE EXISTING REFUSAL, IN ITS OWN WORDS. `safety.plain` is the
        # sentence for the person and `safety.reasons` still holds whatever the
        # cage said, unedited, for whoever reads the record later.
        return Confirmation(
            ready=False,
            said=f"{safety.plain}\n\n{NOTHING_SAVED_YET}",
            still_to_check=safety.still_to_check,
            corrected=_corrected_fields(record),
        )

    unresolved = outstanding(record)
    if unresolved:
        names = [_PLAIN_FIELD.get(field, field) for field in unresolved]
        listed = "\n".join(f"- {name}" for name in names)
        return Confirmation(
            ready=False,
            said=(
                "I cannot show you what to save yet. "
                "These still need checking:\n"
                f"\n{listed}\n"
                f"\n{NOTHING_SAVED_YET}"
            ),
            still_to_check=tuple(names),
            corrected=_corrected_fields(record),
        )

    lines = ["Please check this before saving:", ""]
    if document_type:
        lines.append(f"Type: {_PLAIN_KIND.get(document_type, document_type)}")
    lines.append(f"Who: {record.party}")
    # `type(...) is int` and not truthiness: a total of 0 is a real reading and
    # must print, where `None` means nobody read one. `_paise` in the gate makes
    # the same distinction for the same reason.
    if type(record.total_paise) is int:
        lines.append(f"Amount: {Q.rupees(record.total_paise)}")
    if record.date is not None:
        lines.append(f"Date: {Q.plain_date(record.date)}")
    if type(record.tax_paise) is int:
        lines.append(f"Tax: {Q.rupees(record.tax_paise)}")
    lines.append(f"Invoice number: {invoice_number or NO_INVOICE_NUMBER}")
    if app_reference:
        lines.append(f"App reference: {app_reference}")
    lines.append(f"Tally number: {TALLY_ASSIGNS_LATER}")
    lines.append(f"Saving into: {company}")
    lines.append("")
    lines.append(NOTHING_SAVED_YET)
    return Confirmation(
        ready=True,
        said="\n".join(lines),
        still_to_check=(),
        corrected=_corrected_fields(record),
    )


# ---- Task 8: the existing safety checks, joined to this screen --------------
#
# NOTHING HERE DECIDES ANYTHING. Every verdict below was reached by code that
# already existed; this module only refuses to show a ready screen when any one
# of them says no. A second opinion about tax, dates, duplicates or open books
# would be a second place for those rules to live, and this repository has paid
# four times for exactly that.

#: THE CAGE COVERS MOST OF THE CONTRACT IN ONE VERDICT, which is why there is
#: no list of checks here. `cage.gate.gate` runs the conservation laws (tax
#: against total), the open-books question, party-known, both confidence bands
#: and the account rules, then `decision.decide` reduces them to one `Action`.
#: Re-asking any of those separately would let the two answers disagree.
#:
#: TWO THINGS THE CAGE DOES NOT ANSWER, so they arrive as their own flags:
#:
#:     already_recorded    `memory.store.claim_operation` returns False when
#:                         this operation id was already claimed. That is the
#:                         repository's duplicate check and it is not re-run
#:                         here - the caller passes what it got.
#:     backend_available   `web/app.py::BACKEND_UNAVAILABLE`. Nothing is
#:                         connected, so nothing can be checked against Tally.
_SAFE_TO_SHOW: Final = "ready"


@dataclass(frozen=True)
class SafetyVerdict:
    """Whether the last screen may be shown, and every existing reason it may not.

    `ready` IS FALSE UNLESS EVERY SOURCE SAID YES. It is never computed from a
    majority, a score or a best guess: one refusal anywhere is a refusal here.

    `reasons` HOLDS THE EXISTING WORDS, VERBATIM. The cage's own sentences are
    kept exactly as it wrote them, because an audit that paraphrases is an
    audit of the paraphrase. `plain` is our sentence for the person, and the
    two are separate for the same reason `reason_for_question` and
    `plain_question` are.

    `action` and `outcome` CARRY THE REPOSITORY'S OWN TYPES rather than a new
    status enum. There is no fifth vocabulary here competing with `Action` and
    `Outcome`; this object reports theirs.
    """

    ready: bool
    plain: str
    reasons: tuple[str, ...]
    still_to_check: tuple[str, ...]
    action: object | None = None


def safety_for(
    record: ExtractedRecord,
    *,
    action: object | None = None,
    reasons: tuple[str, ...] = (),
    already_recorded: bool = False,
    backend_available: bool = True,
) -> SafetyVerdict:
    """Every existing safety answer, joined. Fails closed at every step.

    `action` IS THE CAGE'S OWN `Action`, PASSED IN RATHER THAN COMPUTED HERE.
    Reaching one needs a Draft, a Tally client, a memory store and an
    open-books reader; a planner that assembled those would be a second
    pipeline. The caller that already ran the cage hands over what it got,
    together with the sentences the cage wrote.

    THE ACTION AND NOT THE WHOLE `Decided`, because a `Decided` carrying
    `Action.POST` must also carry a `LedgerEntry`, and `LedgerEntry.decided`
    refuses every caller that is not the decision layer. Taking the action
    keeps this module on the outside of that guard rather than asking for a
    way through it.

    `action=None` REFUSES. Nobody ran the cage, and a document nobody checked
    has not been shown to be safe - the same rule `UNKNOWN_DOCUMENT_TYPE`
    states one layer down: the absence of a verdict is not permission.

    ANSWERING THE QUESTIONS IS NOT A PASS. A person can resolve every field
    and this still returns not-ready, because the fields were never the only
    thing being checked.
    """
    from accountant.cage.decision import Action

    unresolved = outstanding(record)
    names = tuple(_PLAIN_FIELD.get(field, field) for field in unresolved)

    if unresolved:
        return SafetyVerdict(
            ready=False,
            plain=("Something still needs checking: " + ", ".join(names) + "."),
            reasons=(),
            still_to_check=names,
        )
    if not isinstance(action, Action):
        return SafetyVerdict(
            ready=False,
            plain=(
                "The safety checks have not been run on this one yet, so I "
                "cannot offer to save it."
            ),
            reasons=(),
            still_to_check=(),
        )
    if action is not Action.POST:
        return SafetyVerdict(
            ready=False,
            plain=(
                "The safety checks stopped this one. Please look at it before "
                "continuing."
            ),
            reasons=tuple(reasons),
            still_to_check=(),
            action=action,
        )
    if already_recorded:
        return SafetyVerdict(
            ready=False,
            plain=(
                "This may already have been recorded. Check the existing entry "
                "before continuing."
            ),
            reasons=(),
            still_to_check=(),
            action=action,
        )
    if not backend_available:
        return SafetyVerdict(
            ready=False,
            plain=(
                "Tally is not reachable right now, so nothing can be saved "
                "yet. Nothing has been changed."
            ),
            reasons=(),
            still_to_check=(),
            action=action,
        )
    return SafetyVerdict(
        ready=True,
        plain=_SAFE_TO_SHOW,
        reasons=(),
        still_to_check=(),
        action=action,
    )

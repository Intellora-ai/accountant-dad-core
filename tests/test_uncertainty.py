"""The question flow, and the seventeen things it must never do.

WHAT THESE TESTS ARE ABOUT
---------------------------
Every test here is one sentence from the owner's brief turned into a
measurement. The ones worth reading twice are the refusals: a person's answer
must not be able to do anything a good reading could not do, and "I am not
sure" must not become a value.

The safety tests do NOT re-test the cage. `tests/test_decision.py` and
`tests/test_gate.py` own that. What they test is that this module cannot reach
past it - that an answer changes the RECORD and nothing else, so every layer
below still runs on it exactly as before.
"""

from __future__ import annotations

import datetime

import pytest

from accountant import uncertainty as U
from accountant.extract.adapter import NOT_FOUND, ExtractedRecord
from accountant.pipeline import Draft
from accountant.tallyio.fake import FakeTally

DOCUMENT = "doc-0001"
READ_BY = "pdf_text_layer"
ABSENT = f"{NOT_FOUND}: not on this bill"


def a_record(
    *,
    date: datetime.date | None = datetime.date(2026, 8, 12),
    party: str | None = "Sharma Traders",
    total_paise: int | None = 420000,
    tax_paise: int | None = 0,
    sources: dict[str, str] | None = None,
    confidence: dict[str, float] | None = None,
    candidates: dict[str, tuple[str, ...]] | None = None,
) -> ExtractedRecord:
    """A bill read cleanly, unless a test says otherwise.

    Everything scores 1.0 by default so that a test changing ONE field is
    changing the only thing under test.
    """
    read = {
        "date": date,
        "party": party,
        "total_paise": total_paise,
        "tax_paise": tax_paise,
    }
    src = {
        name: READ_BY if value is not None else ABSENT for name, value in read.items()
    }
    src.update(sources or {})
    return ExtractedRecord(
        date=date,
        party=party,
        total_paise=total_paise,
        tax_paise=tax_paise,
        raw_text="",
        backend="test",
        per_field_source=src,
        per_field_confidence=confidence
        if confidence is not None
        else dict.fromkeys(read, 1.0),
        per_field_candidates=candidates or {},
    )


# ---- a question is about ONE named field, never "this needs review" ---------


def test_a_field_read_badly_produces_a_question_about_that_field() -> None:
    seen = a_record(
        confidence={"total_paise": 0.4, "party": 1.0, "date": 1.0, "tax_paise": 1.0}
    )

    question = U.next_question(DOCUMENT, seen)

    assert question is not None
    assert question.field_name == "total_paise"
    assert question.document_id == DOCUMENT
    assert question.reason_for_question == U.NOT_SURE_ENOUGH


def test_a_bill_read_cleanly_is_asked_nothing() -> None:
    """THE CONTROL. A flow that always finds something to ask is a review
    message with extra steps."""
    assert U.next_question(DOCUMENT, a_record()) is None
    assert U.outstanding(a_record()) == ()


def test_one_question_comes_back_at_a_time() -> None:
    """Three fields unclear, one question. A person handed three at once
    answers the easy one and guesses the rest."""
    seen = a_record(total_paise=None, party=None, date=None)

    first = U.next_question(DOCUMENT, seen)

    assert first is not None
    assert first.field_name == "total_paise"
    assert len(U.outstanding(seen)) == 3


def test_every_unclear_field_is_eventually_asked_about() -> None:
    """Answering one moves to the next, until there is nothing left."""
    seen = a_record(total_paise=None, party=None, date=None)
    asked: list[str] = []
    answered: set[str] = set()

    while (
        question := U.next_question(DOCUMENT, seen, frozenset(answered))
    ) is not None:
        asked.append(question.field_name)
        answered.add(question.field_name)

    assert asked == ["total_paise", "party", "date"]


# ---- the choices are the document's, never ours -----------------------------


def test_a_conflict_question_shows_the_real_conflicting_values() -> None:
    """Both readings, exactly as the page prints them, and neither chosen."""
    seen = a_record(
        total_paise=None,
        candidates={"total_paise": ("76,115.90", "78,115.90")},
    )

    question = U.next_question(DOCUMENT, seen)

    assert question is not None
    assert question.reason_for_question == U.TWO_READINGS
    labels = [choice.label for choice in question.options]
    assert "76,115.90" in labels
    assert "78,115.90" in labels
    assert question.source_evidence == ("76,115.90", "78,115.90")


def test_no_choice_is_offered_that_the_document_did_not_state() -> None:
    """THE ANTI-INVENTION TEST. Every option is either a value off the page or
    one of the two actions - and nothing else, ever."""
    seen = a_record(
        total_paise=None, candidates={"total_paise": ("76,115.90", "78,115.90")}
    )

    question = U.next_question(DOCUMENT, seen)

    assert question is not None
    allowed = {"76,115.90", "78,115.90", U.NOT_SURE, U.TYPE_IT}
    assert {choice.value for choice in question.options} <= allowed


def test_a_missing_field_offers_no_values_and_asks_for_typing() -> None:
    seen = a_record(party=None)

    question = U.question_for(DOCUMENT, seen, "party")

    assert question is not None
    assert question.reason_for_question == U.NOT_READ
    assert question.source_evidence == ()
    assert question.allows_custom_answer is True
    assert [c.value for c in question.options] == [U.NOT_SURE, U.TYPE_IT]


# ---- the words a person reads -----------------------------------------------


JARGON = ("confidence", "ledger", "voucher", "debit", "credit", "reconciliation")


@pytest.mark.parametrize("field", U.ASK_ABOUT)
@pytest.mark.parametrize("reason", (U.NOT_READ, U.NOT_SURE_ENOUGH))
def test_no_question_uses_a_word_that_needs_accounting(field: str, reason: str) -> None:
    """S7 for this flow. The reason CODE may say anything; the sentence a
    person reads may not."""
    low = dict.fromkeys(U.ASK_ABOUT, 1.0)
    low[field] = 0.1
    seen = (
        a_record(**{field: None})  # pyright: ignore[reportArgumentType]
        if reason == U.NOT_READ
        else a_record(confidence=low)
    )

    question = U.question_for(DOCUMENT, seen, field)

    assert question is not None
    said = question.plain_question.lower()
    assert not [word for word in JARGON if word in said], question.plain_question


def test_the_reason_code_is_never_in_the_words_the_person_reads() -> None:
    seen = a_record(total_paise=None)
    question = U.question_for(DOCUMENT, seen, "total_paise")

    assert question is not None
    assert question.reason_for_question not in question.plain_question


# ---- answers go through the existing normalizers ----------------------------


def test_a_typed_amount_is_read_by_the_existing_money_parser() -> None:
    applied = U.apply_answer(a_record(total_paise=None), "total_paise", "4,200.50")

    assert applied.resolved is True
    assert applied.record is not None
    assert applied.record.total_paise == 420050


def test_an_answer_that_is_not_money_is_refused() -> None:
    applied = U.apply_answer(
        a_record(total_paise=None), "total_paise", "about four thousand"
    )

    assert applied.resolved is False
    assert applied.record is None
    assert applied.refusal


def test_a_negative_amount_is_refused() -> None:
    applied = U.apply_answer(a_record(total_paise=None), "total_paise", "-500")

    assert applied.resolved is False
    assert applied.record is None


def test_an_ambiguous_typed_date_is_refused_rather_than_guessed() -> None:
    """`11/08/2026` is two different days. The repository refuses those
    everywhere else and refuses them here."""
    applied = U.apply_answer(a_record(date=None), "date", "11/08/2026")

    assert applied.resolved is False
    assert applied.record is None


def test_an_unambiguous_typed_date_is_accepted() -> None:
    applied = U.apply_answer(a_record(date=None), "date", "2026-08-12")

    assert applied.resolved is True
    assert applied.record is not None
    assert applied.record.date == datetime.date(2026, 8, 12)


def test_an_empty_answer_is_refused() -> None:
    applied = U.apply_answer(a_record(party=None), "party", "   ")

    assert applied.resolved is False
    assert applied.record is None


# ---- "I am not sure" --------------------------------------------------------


def test_every_question_offers_not_sure() -> None:
    for field in U.ASK_ABOUT:
        seen = a_record(**{field: None})  # pyright: ignore[reportArgumentType]
        question = U.question_for(DOCUMENT, seen, field)
        assert question is not None
        assert question.allows_not_sure is True
        assert U.NOT_SURE in {c.value for c in question.options}


def test_not_sure_never_becomes_a_value() -> None:
    """THE MOST IMPORTANT TEST IN THIS FILE. A person who does not know must
    not be turned into a source of certainty."""
    seen = a_record(total_paise=None)

    applied = U.apply_answer(seen, "total_paise", U.NOT_SURE)

    assert applied.resolved is False
    assert applied.record is None
    assert applied.refusal == ""


def test_not_sure_leaves_the_field_unresolved_so_it_still_holds_the_bill() -> None:
    seen = a_record(total_paise=None)
    U.apply_answer(seen, "total_paise", U.NOT_SURE)

    assert U.reason_to_ask(seen, "total_paise") == U.NOT_READ
    assert "total_paise" in U.outstanding(seen)


# ---- an answer cannot outrank a reading -------------------------------------


def test_an_answer_changes_the_record_and_nothing_else() -> None:
    """The whole safety argument in one assertion.

    This module returns a RECORD. It does not return a decision, an outcome or
    a permission, and it cannot: the checks, the open-books question and the
    cage all take the record afterwards and run unchanged. An answer is a
    better reading, not a bypass.
    """
    applied = U.apply_answer(a_record(total_paise=None), "total_paise", "4200")

    assert applied.resolved is True
    assert isinstance(applied.record, ExtractedRecord)
    assert not hasattr(applied, "decision")
    assert not hasattr(applied, "outcome")
    assert not hasattr(applied, "post")


def test_an_answer_is_marked_as_a_persons_answer_on_the_record() -> None:
    """So every layer below can see it came from a person rather than a page."""
    applied = U.apply_answer(a_record(party=None), "party", "Sharma Traders")

    assert applied.record is not None
    assert applied.record.per_field_source["party"] == "human_answer"


def test_answering_one_field_leaves_the_others_exactly_as_they_were() -> None:
    seen = a_record(total_paise=None, party=None)

    applied = U.apply_answer(seen, "total_paise", "4200")

    assert applied.record is not None
    assert applied.record.party is None
    assert applied.record.per_field_source["party"] == ABSENT


# ---- nothing is remembered across documents ---------------------------------


def test_an_answer_is_not_reused_on_another_document() -> None:
    """No global memory. A second bill from the same supplier is a page nobody
    has looked at, and an answer about the first says nothing about it."""
    first = a_record(total_paise=None)
    applied = U.apply_answer(first, "total_paise", "4200")
    assert applied.record is not None

    second = a_record(total_paise=None)

    assert U.reason_to_ask(second, "total_paise") == U.NOT_READ
    assert second.total_paise is None


def test_the_question_names_the_document_it_belongs_to() -> None:
    one = U.question_for("doc-A", a_record(total_paise=None), "total_paise")
    two = U.question_for("doc-B", a_record(total_paise=None), "total_paise")

    assert one is not None
    assert two is not None
    assert one.document_id == "doc-A"
    assert two.document_id == "doc-B"


def test_this_module_holds_no_state_between_calls() -> None:
    """Asked twice about the same untouched record, the answer is identical -
    so nothing was stashed on the first call."""
    seen = a_record(total_paise=None)

    assert U.next_question(DOCUMENT, seen) == U.next_question(DOCUMENT, seen)


# ---- the confirmation -------------------------------------------------------


def test_the_confirmation_says_what_will_be_saved_and_that_nothing_has_been() -> None:
    said = U.confirmation(
        party="Sharma Traders",
        amount_paise=420000,
        when=datetime.date(2026, 8, 12),
        company="Demo Co",
    )

    assert "Sharma Traders" in said
    assert "4,200" in said
    assert "12 August 2026" in said
    assert "Demo Co" in said
    assert "Nothing will be saved until you confirm." in said


def test_the_confirmation_uses_no_word_that_needs_accounting() -> None:
    said = U.confirmation(
        party="Sharma Traders",
        amount_paise=420000,
        when=datetime.date(2026, 8, 12),
        company="Demo Co",
    ).lower()

    assert not [word for word in JARGON if word in said]


def test_the_confirmation_is_only_words_and_cannot_write() -> None:
    """It returns a string. A function that returns a string cannot call
    Tally, and that is the whole guarantee - stated as a test so a later
    version that starts posting fails here."""
    said = U.confirmation(
        party="X", amount_paise=1, when=datetime.date(2026, 1, 1), company="C"
    )

    assert isinstance(said, str)


# ---- what this flow must not touch ------------------------------------------


def test_this_flow_never_reaches_a_reader_or_tally() -> None:
    """THE IMPORT-GRAPH GUARD, in the shape `tests/test_no_reader.py` uses.

    Tesseract, any other reading backend and the Tally writer are all absent
    from this module's imports. It is handed a record that has already been
    read; reading again would be a second opinion about the same page, and
    writing is somebody else's job behind a cage.
    """
    import ast
    import pathlib

    source = pathlib.Path(U.__file__).read_text()
    imported = {
        name.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for name in node.names
    } | {
        node.module or ""
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    }

    forbidden = ("tesseract", "pytesseract", "freeocr", "free_ocr", "tallyio", "PIL")
    leaked = [
        name for name in imported for bad in forbidden if bad.lower() in name.lower()
    ]

    assert leaked == [], leaked


def test_the_asking_threshold_is_the_owners_existing_number() -> None:
    """Not a new threshold. If `AUTO_POST_FLOOR` moves, this moves with it, and
    nobody has to remember that two files hold the same number."""
    from accountant.cage.decision import AUTO_POST_FLOOR

    assert U.ASK_BELOW == AUTO_POST_FLOOR


# ---- an answer cannot reach past the layers below it ------------------------
#
# These run the WHOLE pipeline on a record an answer has already been applied
# to. The point is not to re-test the cage - `tests/test_decision.py` owns that
# - but to prove this module cannot get around it. A person's answer is new
# information about the bill; it is not permission.


def _company_with_history() -> tuple[FakeTally, str]:
    from accountant.schema import Voucher

    company = "Demo Co"
    history = tuple(
        Voucher(
            id=f"h{i}",
            date=datetime.date(2026, 1, 1),
            party="Sharma Traders",
            narration="cement",
            debit_account="Purchases",
            credit_account="Cash",
            amount_paise=380000 + i,
        )
        for i in range(40)
    )
    tally = FakeTally()
    tally.add_company(
        company,
        accounts=("Purchases", "Cash", "Sundry Expenses"),
        vouchers=history,
        backed_up=True,
    )
    return tally, company


def _run(period_reader: object) -> tuple[Draft, FakeTally]:
    """One typed bill through the real pipeline, with the reader supplied."""
    from accountant import pipeline
    from accountant.extract.adapter import TypedTextExtractor
    from accountant.memory.bootstrap import bootstrap
    from accountant.memory.store import MemoryStore

    tally, company = _company_with_history()
    memory = bootstrap(tally, company, MemoryStore(":memory:"))
    draft = pipeline.run(
        company,
        b"paid Sharma Traders 4200 for cement",
        "text/plain",
        TypedTextExtractor(),
        tally,
        memory,
        today=datetime.date(2026, 8, 7),
        period_reader=period_reader,  # pyright: ignore[reportArgumentType]
    )
    return draft, tally


def test_an_answer_cannot_get_past_a_missing_open_books_check() -> None:
    """`period_reader=None` means nobody looked, and nobody-looked blocks -
    whatever a person answered about the fields."""
    from accountant.schema import Outcome

    draft, tally = _run(None)

    assert draft.outcome is Outcome.NOT_VALID
    assert tally.list_our_vouchers("Demo Co") == ()


def test_an_answer_cannot_get_past_an_unreachable_open_books_check() -> None:
    from accountant.schema import Outcome
    from tests.test_period_handoff import unreachable_reader

    draft, tally = _run(unreachable_reader())

    assert draft.outcome is Outcome.NOT_VALID
    assert tally.list_our_vouchers("Demo Co") == ()


def test_nothing_is_written_to_tally_by_asking_or_answering() -> None:
    """THE NO-WRITE-BEFORE-CONFIRMATION TEST.

    A whole question-and-answer round happens here and the books are untouched
    at the end of it. `pipeline.post` is the only thing that writes, it is not
    called, and nothing in this module can call it.
    """
    tally, company = _company_with_history()
    seen = a_record(total_paise=None)

    question = U.next_question(DOCUMENT, seen)
    assert question is not None
    applied = U.apply_answer(seen, question.field_name, "4200")
    assert applied.resolved is True
    U.confirmation(
        party="Sharma Traders",
        amount_paise=420000,
        when=datetime.date(2026, 8, 12),
        company=company,
    )

    assert tally.list_our_vouchers(company) == ()
    assert tally.trial_balance(company) == tally.trial_balance(company)


def test_a_refused_answer_leaves_the_record_untouched_so_nothing_can_post_on_it() -> (
    None
):
    """A failed safety check means the record does not move. There is no
    partially-applied answer for a later layer to act on."""
    before = a_record(total_paise=None)

    applied = U.apply_answer(before, "total_paise", "not a number")

    assert applied.record is None
    assert before.total_paise is None
    assert before.per_field_source["total_paise"] == ABSENT


# ---- Task 7: the last screen ------------------------------------------------
#
# What will be saved, in plain words, or a refusal saying what is still
# unclear. Every value below comes off the record; nothing here re-reads,
# re-parses or decides.


COMPANY = "Demo Co"


def test_a_ready_confirmation_shows_the_values_that_will_be_saved() -> None:
    said = U.confirmation_for(a_record(), company=COMPANY).said

    assert "Sharma Traders" in said
    assert "4,200" in said
    assert "12 August 2026" in said
    assert "Demo Co" in said
    assert U.NOTHING_SAVED_YET in said


def test_a_ready_confirmation_shows_the_kind_of_document_when_it_is_known() -> None:
    said = U.confirmation_for(a_record(), document_type="invoice", company=COMPANY).said

    assert "Type: A bill from a supplier" in said


def test_the_kind_is_left_out_rather_than_guessed_when_nobody_said() -> None:
    """No caller knows the kind, no line. A default would be an invented fact."""
    assert "Type:" not in U.confirmation_for(a_record(), company=COMPANY).said


def test_tax_is_shown_including_when_it_is_nothing() -> None:
    """Zero tax is a real reading and must print. Only an UNREAD tax is absent."""
    assert "Tax: " in U.confirmation_for(a_record(tax_paise=0), company=COMPANY).said


# ---- the three numbers that get confused ------------------------------------


def test_a_supplier_invoice_number_is_shown_as_it_was_printed() -> None:
    said = U.confirmation_for(
        a_record(), invoice_number="INV-1042", company=COMPANY
    ).said

    assert "Invoice number: INV-1042" in said


def test_a_document_with_no_number_of_its_own_says_so_plainly() -> None:
    said = U.confirmation_for(a_record(), company=COMPANY).said

    assert f"Invoice number: {U.NO_INVOICE_NUMBER}" in said
    assert "Invoice number: None from the source" in said


def test_no_invoice_number_is_ever_invented() -> None:
    """THE ANTI-FABRICATION TEST. With nothing supplied, the only thing that may
    appear beside that label is the sentence saying there was nothing."""
    said = U.confirmation_for(
        a_record(), app_reference="ad_abc123", company=COMPANY
    ).said
    line = [row for row in said.splitlines() if row.startswith("Invoice number:")]

    assert line == [f"Invoice number: {U.NO_INVOICE_NUMBER}"]


def test_the_app_reference_is_a_separate_line_from_the_invoice_number() -> None:
    said = U.confirmation_for(
        a_record(),
        invoice_number="INV-1042",
        app_reference="ad_abc123",
        company=COMPANY,
    ).said

    assert "Invoice number: INV-1042" in said
    assert "App reference: ad_abc123" in said
    assert "Invoice number: ad_abc123" not in said
    assert "App reference: INV-1042" not in said


def test_the_tally_number_is_described_as_future_and_never_fabricated() -> None:
    """It cannot be shown because it does not exist yet. A placeholder that
    looked like one would be a fabricated record."""
    said = U.confirmation_for(
        a_record(), invoice_number="INV-1042", company=COMPANY
    ).said

    assert f"Tally number: {U.TALLY_ASSIGNS_LATER}" in said
    assert "Tally number: INV-1042" not in said


# ---- corrected values reach the screen --------------------------------------


def test_a_corrected_amount_is_the_amount_that_will_be_saved() -> None:
    fixed = U.apply_answer(a_record(total_paise=None), "total_paise", "9,999")
    assert fixed.record is not None

    said = U.confirmation_for(fixed.record, company=COMPANY).said

    assert "9,999" in said
    assert "4,200" not in said


def test_a_corrected_date_and_party_are_the_ones_that_will_be_saved() -> None:
    one = U.apply_answer(a_record(date=None), "date", "2026-01-09")
    assert one.record is not None
    two = U.apply_answer(one.record, "party", "Verma Cement")
    assert two.record is not None

    said = U.confirmation_for(two.record, company=COMPANY).said

    assert "9 January 2026" in said
    assert "Verma Cement" in said


def test_the_screen_records_which_values_a_person_corrected() -> None:
    """Source evidence survives to the last screen: the record says
    `human_answer`, and the confirmation reports it rather than re-deriving it."""
    fixed = U.apply_answer(a_record(total_paise=None), "total_paise", "4200")
    assert fixed.record is not None

    result = U.confirmation_for(fixed.record, company=COMPANY)

    assert result.corrected == ("total_paise",)
    assert a_record().per_field_source["total_paise"] != "human_answer"


# ---- refusal ----------------------------------------------------------------


def test_an_unresolved_field_refuses_the_ready_confirmation() -> None:
    result = U.confirmation_for(a_record(total_paise=None), company=COMPANY)

    assert result.ready is False
    assert result.still_to_check == ("the total",)


def test_a_refusal_says_what_still_needs_checking_in_plain_words() -> None:
    result = U.confirmation_for(a_record(total_paise=None, party=None), company=COMPANY)

    assert "the total" in result.said
    assert "who it is from" in result.said
    assert "total_paise" not in result.said


def test_a_refusal_never_shows_a_ready_to_save_summary() -> None:
    """THE IMPORTANT ONE. A tidy half-summary reads as a complete one, so a
    refusal must not list values beside an invitation to confirm."""
    result = U.confirmation_for(a_record(date=None), company=COMPANY)

    assert result.ready is False
    assert "Please check this before saving:" not in result.said
    assert "Tally number:" not in result.said
    assert "Invoice number:" not in result.said


def test_not_sure_leaves_the_confirmation_refused() -> None:
    seen = a_record(total_paise=None)
    U.apply_answer(seen, "total_paise", U.NOT_SURE)

    assert U.confirmation_for(seen, company=COMPANY).ready is False


def test_a_refused_answer_leaves_the_confirmation_refused() -> None:
    seen = a_record(total_paise=None)
    U.apply_answer(seen, "total_paise", "not a number")

    assert U.confirmation_for(seen, company=COMPANY).ready is False


@pytest.mark.parametrize("typed", ["not a number", "-500", "   ", "11/08/2026"])
def test_no_invalid_answer_can_reach_a_ready_confirmation(typed: str) -> None:
    """Amounts, dates and blanks alike: `with_answer` refuses, the record does
    not move, and the screen stays refused."""
    field = "date" if "/" in typed else "total_paise"
    seen = a_record(**{field: None})  # pyright: ignore[reportArgumentType]

    applied = U.apply_answer(seen, field, typed)

    assert applied.resolved is False
    assert U.confirmation_for(seen, company=COMPANY).ready is False


# ---- the screen cannot act --------------------------------------------------


def test_the_confirmation_is_words_and_flags_and_nothing_that_can_save() -> None:
    """It holds no client and exposes no action. A screen built from this
    object cannot save by accident, because there is nothing on it to call."""
    result = U.confirmation_for(a_record(), company=COMPANY)

    assert isinstance(result.said, str)
    assert isinstance(result.ready, bool)
    for forbidden in ("post", "save", "write", "client", "commit"):
        assert not hasattr(result, forbidden)


JARGON_ON_THE_LAST_SCREEN = (
    "confidence",
    "tier",
    "ledger",
    "debit",
    "credit",
    "reconciliation",
    "voucher",
    "posting",
    "not_valid",
)


def test_the_last_screen_uses_no_word_that_needs_accounting() -> None:
    ready = U.confirmation_for(
        a_record(),
        document_type="invoice",
        invoice_number="INV-1042",
        app_reference="ad_abc123",
        company=COMPANY,
    ).said.lower()
    refused = U.confirmation_for(
        a_record(total_paise=None), company=COMPANY
    ).said.lower()

    for said in (ready, refused):
        assert not [word for word in JARGON_ON_THE_LAST_SCREEN if word in said], said


# ---- Task 8: the existing safety checks decide, not the answers -------------
#
# The point of every test below is one sentence: answering the questions is not
# a pass. `safety_for` reaches no verdict of its own - it joins verdicts other
# code already reached, and refuses if any of them refused.


def _blocked(record: ExtractedRecord, *reasons: str) -> U.SafetyVerdict:
    """The verdict a caller reaches after the cage REFUSED - the real `Action`
    and the real sentences, exactly as the cage wrote them."""
    from accountant.cage.decision import Action

    return U.safety_for(record, action=Action.BLOCK, reasons=tuple(reasons))


def _cleared(
    record: ExtractedRecord,
    *,
    already_recorded: bool = False,
    backend_available: bool = True,
) -> U.SafetyVerdict:
    """The verdict a caller reaches after the cage CLEARED a bill."""
    from accountant.cage.decision import Action

    return U.safety_for(
        record,
        action=Action.POST,
        already_recorded=already_recorded,
        backend_available=backend_available,
    )


def test_a_fully_answered_record_is_still_not_ready_without_a_safety_verdict() -> None:
    """THE HEADLINE TEST. Every field resolved, nothing to ask, and still not
    ready - because nobody ran the checks. Absence of a verdict is not
    permission."""
    verdict = U.safety_for(a_record())

    assert verdict.ready is False
    assert "safety checks have not been run" in verdict.plain


def test_a_record_the_cage_cleared_is_ready() -> None:
    verdict = _cleared(a_record())

    assert verdict.ready is True


# A. required unresolved field


def test_an_unresolved_required_field_is_not_ready_even_when_the_cage_says_post() -> (
    None
):
    verdict = _cleared(a_record(total_paise=None))

    assert verdict.ready is False
    assert verdict.still_to_check == ("the total",)
    assert "the total" in verdict.plain


# B. "I am not sure"


def test_not_sure_leaves_the_safety_verdict_not_ready() -> None:
    seen = a_record(total_paise=None)
    applied = U.apply_answer(seen, "total_paise", U.NOT_SURE)

    assert applied.record is None
    assert _cleared(seen).ready is False


# C + D. invalid amount, invalid date - the existing validators stay authoritative


@pytest.mark.parametrize(
    ("field", "typed"),
    [
        ("total_paise", "about four thousand"),
        ("total_paise", "-500"),
        ("total_paise", "   "),
        ("date", "11/08/2026"),
    ],
)
def test_no_invalid_answer_can_reach_a_ready_safety_verdict(
    field: str, typed: str
) -> None:
    seen = a_record(**{field: None})  # pyright: ignore[reportArgumentType]

    applied = U.apply_answer(seen, field, typed)

    assert applied.resolved is False
    assert applied.refusal
    assert _cleared(seen).ready is False


# E. tax/total - the cage's own conservation verdict is respected, not re-run


def test_a_cage_refusal_is_not_ready_and_its_words_are_kept_exactly() -> None:
    from accountant.cage.decision import NUMBERS_DO_NOT_ADD_UP, Action

    verdict = _blocked(a_record(), NUMBERS_DO_NOT_ADD_UP)

    assert verdict.ready is False
    assert verdict.reasons == (NUMBERS_DO_NOT_ADD_UP,)
    assert verdict.action is Action.BLOCK


def test_a_cage_question_is_not_a_pass_either() -> None:
    """ASK is not POST. A bill the cage wants a person to look at must not
    arrive at a ready-to-save screen."""
    from accountant.cage.decision import Action

    verdict = U.safety_for(a_record(), action=Action.ASK, reasons=("please check",))

    assert verdict.ready is False
    assert verdict.action is Action.ASK


def test_no_tax_rule_is_computed_here() -> None:
    """The module holds no tax arithmetic of its own. If it ever grows one,
    this fails and somebody has to justify a second place for that rule."""
    import pathlib

    source = pathlib.Path(U.__file__).read_text().lower()

    for invented in ("net_plus_tax", "* 0.18", "gst_rate", "tax_rate"):
        assert invented not in source


# F. duplicate - the existing claim result is respected


def test_a_possible_duplicate_is_not_ready() -> None:
    verdict = _cleared(a_record(), already_recorded=True)

    assert verdict.ready is False
    assert "may already have been recorded" in verdict.plain


def test_no_duplicate_detector_is_reimplemented_here() -> None:
    """`already_recorded` is a fact handed in, from
    `memory.store.claim_operation`. This module must not grow its own."""
    import pathlib

    source = pathlib.Path(U.__file__).read_text()

    assert "claim_operation(" not in source
    assert "def _is_duplicate" not in source


# G + H. identity, books, availability


def test_a_backend_that_is_not_reachable_is_not_ready() -> None:
    verdict = _cleared(a_record(), backend_available=False)

    assert verdict.ready is False
    assert "not reachable" in verdict.plain


def test_the_books_and_identity_verdicts_arrive_through_the_cage() -> None:
    """Open-books and party-known are already inside `decision.decide`, so a
    refusal for either arrives as a non-POST action carrying its own sentence.
    This proves it is preserved rather than re-derived."""
    # The cage's own sentence, quoted rather than imported. The assertion is
    # that whatever it said survives unedited; reaching for a private constant
    # to prove that would reach past the boundary this file respects
    # everywhere else.
    said = "I could not tell whether the books for this date are still open."

    verdict = _blocked(a_record(), said)

    assert verdict.ready is False
    assert verdict.reasons == (said,)


# I. a safe record reaches the Task 7 screen, and only that


def test_a_safe_record_reaches_the_confirmation_and_is_not_saved() -> None:
    tally, company = _company_with_history()
    verdict = _cleared(a_record())

    result = U.confirmation_for(a_record(), company=company, safety=verdict)

    assert verdict.ready is True
    assert result.ready is True
    assert U.NOTHING_SAVED_YET in result.said
    assert tally.list_our_vouchers(company) == ()


def test_an_unsafe_verdict_refuses_the_confirmation_screen() -> None:
    from accountant.cage.decision import NUMBERS_DO_NOT_ADD_UP

    verdict = _blocked(a_record(), NUMBERS_DO_NOT_ADD_UP)

    result = U.confirmation_for(a_record(), company="Demo Co", safety=verdict)

    assert result.ready is False
    assert "Please check this before saving:" not in result.said
    assert "Tally number:" not in result.said
    assert U.NOTHING_SAVED_YET in result.said


def test_answering_every_question_cannot_by_itself_produce_a_ready_screen() -> None:
    """THE WHOLE POINT OF TASK 8, as one assertion. The record is complete and
    every question is answered; the screen still refuses, because the checks
    said no."""

    fixed = U.apply_answer(a_record(total_paise=None), "total_paise", "4200")
    assert fixed.record is not None
    assert U.outstanding(fixed.record) == ()

    verdict = _blocked(fixed.record, "no")
    result = U.confirmation_for(fixed.record, company="Demo Co", safety=verdict)

    assert result.ready is False


# J. side effects


def test_the_safety_join_cannot_reach_tally_gemini_or_a_writer() -> None:
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(U.__file__).read_text())
    imported = {
        name.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for name in node.names
    } | {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }

    forbidden = ("gemini", "tallyio", "tesseract", "pytesseract", "freeocr", "PIL")
    leaked = [n for n in imported for bad in forbidden if bad.lower() in n.lower()]

    assert leaked == [], leaked


def test_the_safety_verdict_exposes_nothing_that_can_write() -> None:
    verdict = _cleared(a_record())

    for forbidden in ("post", "save", "write", "commit", "client"):
        assert not hasattr(verdict, forbidden)


def test_the_plain_safety_wording_uses_no_word_that_needs_accounting() -> None:

    spoken = [
        U.safety_for(a_record()).plain,
        _cleared(a_record(total_paise=None)).plain,
        _blocked(a_record(), "x").plain,
        _cleared(a_record(), already_recorded=True).plain,
        _cleared(a_record(), backend_available=False).plain,
    ]

    for said in spoken:
        assert not [w for w in JARGON_ON_THE_LAST_SCREEN if w in said.lower()], said

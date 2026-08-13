"""A guessed supplier name is not a supplier. F-03, at the one seam it crosses.

WHAT WENT WRONG, MEASURED
-------------------------
`ExtractedRecord` had four values, four sources and no confidence column. So a
name the OCR tier GUESSED and a name the text layer READ arrived at every
downstream caller as the same thing: a string with a backend's name beside it.

The picture rung reads `IYER ELECTRICALS` as `IVER. ELECTRICALS` and hands it
over with `per_field_source["party"] == "free_ocr"` - a READ source, not a
refusal - at a confidence of **0.08**. Inside `accountant/cage/` that 0.08 is
seen and the entry is refused. On the pipeline path there was no 0.08 to see, so
the misread name reached `pipeline.build_draft` and became a vendor IDENTITY:
the argument to `memory.propose_account`, the key `funding_from_history` looks
up, the party the detectors compare against history, and the name written on the
voucher.

That is failure mode F-03, wrong party attached, and its cost is not a wrong
screen. It is one supplier's balance wrong for ever, or a second ledger quietly
opened for a supplier who is already on the books under the right spelling.

The defect became live this morning: `registry.DEFAULT_BACKEND` is now `ladder`,
and the harness reports `free_ocr` answering 40 of 100 cases with **5 party
fields WRONG rather than unread**.

THE RULE, AND WHY IT CARRIES NO NUMBER
--------------------------------------
A field that was ESTIMATED is never an identity on its own. Structural, not a
threshold: `confidence.EXACT` is not a band, it is the statement that nothing
was guessed - a text layer has characters in it and a photograph does not. No
number was chosen here, so there is no number to tune, and `ASK_FLOOR` and
`AUTO_POST_FLOOR` stay the owner's two bands and keep meaning what they say.

The estimate is not deleted. It stays on `Draft.record` with its own source and
its own score, where the evidence page and the cage can both read it. What it
may not do is act as a name.

WHAT HAPPENS INSTEAD IS NOT NEW
-------------------------------
The product already has a correct answer for "we do not know who this is": it
ASKS. `checks.party_is_named` fails, `problems.QUESTION_FOR` turns it into
`questions.who_was_it`, and the person reads "Who did you pay? I couldn't work
it out." No new path was invented for this; the existing one was reached.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That the OCR tier reads badly, or well. It does not run tesseract at all - the
records here are built by hand at the exact shape the rung produces, because the
question is what the SEAM does with a stated confidence, not what an engine
scores. `tests/test_freeocr.py` and `docs/EXTRACTION_MEASURED.md` own accuracy.

That a high confidence means a right answer. It does not - that is F-02, and
`accountant/cage/confidence.py` says so at length. This file is about the case
where the reader ALREADY SAID it was unsure and nobody downstream could hear it.
"""

from __future__ import annotations

import datetime

from accountant.cage.confidence import EXACT
from accountant.cage.decision import ASK_FLOOR
from accountant.cage.gate import observed
from accountant.checks import party_is_named
from accountant.extract.adapter import (
    NOT_FOUND,
    ExtractedRecord,
    Extractor,
)
from accountant.extract.freeocr import FreeReader, Reading, Word
from accountant.extract.textlayer import SOURCE as TEXT_LAYER
from accountant.extract.textlayer import TextLayerReader
from accountant.memory.bootstrap import bootstrap
from accountant.memory.company import CompanyMemory, propose_account
from accountant.memory.store import MemoryStore
from accountant.pipeline import ESTIMATED_NOT_AN_IDENTITY, build_draft, next_question
from accountant.questions import who_was_it
from accountant.schema import Voucher
from accountant.tallyio.fake import FakeTally

# The PDF builder, borrowed rather than copied. `tests/test_textlayer.py` builds
# a real PDF 1.4 with the xref offsets measured off the bytes as they are
# emitted, and a second hand-rolled copy here is how the two drift into testing
# different file formats.
from tests.test_textlayer import pdf_bytes

COMPANY = "Nagpur Hardware Stores"
ACCOUNTS = ("Purchases", "Cash")

#: The supplier who is already on the books, spelled the way the books spell it.
BOOKED = "IYER ELECTRICALS"

#: What the picture rung actually handed over for the same letterhead, at 0.08.
#: A capital Y read as a V, and a full stop invented out of the serif.
MISREAD = "IVER. ELECTRICALS"

#: The score the rung stated for it. Not chosen here - it is what
#: `confidence.field_confidence` returns for the worst word on that letterhead.
OCR_SCORE = 0.08

TYPED = b"paid Sharma Traders 4200 for cement"


def _voucher(n: int, party: str) -> Voucher:
    return Voucher(
        id=f"h{n}",
        date=datetime.date(2026, 4, 1),
        party=party,
        narration="switchgear",
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=118000,
    )


def _memory() -> CompanyMemory:
    """A company whose books already know `BOOKED`, four times over."""
    tally = FakeTally()
    tally.add_company(
        COMPANY,
        accounts=ACCOUNTS,
        vouchers=tuple(_voucher(n, BOOKED) for n in range(4)),
        backed_up=True,
    )
    return bootstrap(tally, COMPANY, MemoryStore(":memory:"))


def _record(
    party: str | None,
    *,
    source: str,
    confidence: float | None,
) -> ExtractedRecord:
    """One record at the exact shape a rung produces, with nothing else moving.

    Every other field is an explicit `not_found`, so a test that passes cannot
    be passing because of the total or the date.

    `confidence=None` OMITS THE ARGUMENT ALTOGETHER rather than passing an empty
    map, and that is not tidiness. Passing `{}` exercises a caller's empty dict;
    omitting it exercises the dataclass DEFAULT, which is the thing that has to
    fail safe. Written the other way, a default of `EXACT` would never be reached
    by any test in this file and the one mutation that matters most would live.
    """
    sources = {
        "date": f"{NOT_FOUND}: no date was read here",
        "party": source if party else f"{NOT_FOUND}: no name was read here",
        "total_paise": source,
        "tax_paise": f"{NOT_FOUND}: no tax was read here",
    }
    if confidence is None:
        return ExtractedRecord(
            date=None,
            party=party,
            total_paise=420000,
            tax_paise=None,
            raw_text="",
            backend=source,
            per_field_source=sources,
        )
    return ExtractedRecord(
        date=None,
        party=party,
        total_paise=420000,
        tax_paise=None,
        raw_text="",
        backend=source,
        per_field_source=sources,
        per_field_confidence={"party": confidence, "total_paise": EXACT},
    )


class _Reader:
    """A backend that hands over one prepared record. Reads nothing."""

    name = "prepared"

    def __init__(self, record: ExtractedRecord) -> None:
        self._record = record

    def extract(self, _data: bytes, _mime: str) -> ExtractedRecord:
        return self._record


def _draft(record: ExtractedRecord, memory: CompanyMemory):
    reader: Extractor = _Reader(record)
    return build_draft(COMPANY, TYPED, "text/plain", reader, memory)


# =============================================================================
# the carrier
# =============================================================================


def test_the_record_carries_the_confidence_the_reader_stated() -> None:
    """0.08 survives the seam. Before this it did not exist past `extract`.

    The whole defect in one assertion: a downstream caller can now tell a guess
    from a reading without parsing a backend's name out of a source string.
    """
    record = _record(MISREAD, source="free_ocr", confidence=OCR_SCORE)

    assert record.confidence_of("party") == OCR_SCORE
    assert record.per_field_confidence["party"] == OCR_SCORE


def test_a_record_built_without_confidence_information_does_not_read_as_confident() -> (
    None
):
    """The default is ABSENT, and absent is not certain.

    A default of `EXACT` would have labelled every OCR guess an exact reading -
    the one change that would make this whole file pass while making the product
    worse than it was before the field existed.

    ASSERTED AT THE CONSUMER TOO, and that half is the one that matters. A
    record with no scores on it still has to be refused an identity all the way
    through `build_draft`; asserting only that the map is empty would pass on a
    default of `EXACT` the moment the record carried a value with it, because a
    name with a 1.0 stamped on it by the DEFAULT is indistinguishable from one a
    reader measured.
    """
    record = _record(MISREAD, source="free_ocr", confidence=None)

    assert record.per_field_confidence == {}
    assert record.confidence_of("party") is None
    assert not record.read_exactly("party")

    draft = _draft(record, _memory())

    assert draft.voucher.party == ""
    assert draft.voucher.debit_account == ""


def test_a_field_with_no_value_reads_exactly_nothing() -> None:
    """An unread field is not an exact reading of an absence."""
    record = _record(None, source="free_ocr", confidence=None)

    assert not record.read_exactly("party")
    assert record.confidence_of("party") is None


# =============================================================================
# the consumer
# =============================================================================


def test_an_ocr_read_party_does_not_become_a_vendor_identity() -> None:
    """The 0.08 name is not the argument to a memory lookup, and not on the leg.

    `build_draft` is the one place `record.party` becomes an identity: it is
    passed to `propose_account`, and it is written onto `Voucher.party`, which
    is then what `funding_from_history`, `memory.lookup`, the detectors and
    `disagrees_with_live_history` all key off in `evaluate`. Stopping it here
    stops all five.
    """
    draft = _draft(_record(MISREAD, source="free_ocr", confidence=OCR_SCORE), _memory())

    assert draft.voucher.party == ""
    assert draft.voucher.debit_account == ""


def test_the_control_a_text_layer_party_flows_through_unchanged() -> None:
    """THE CONTROL. Without it, refusing everything would also pass.

    The text-layer tier measures 20/20 party on the twenty corpus PDFs with
    ZERO wrong. It states `confidence.EXACT`, it is entitled to, and nothing
    here may cost it a single one of those twenty.

    THE REAL TIER, ON REAL PDF BYTES, and not a record built by hand at the
    shape it produces. That distinction was measured rather than argued: with a
    hand-built record this control passed even when `TextLayerReader.extract`
    stated no confidence at all, because the test was then asserting about the
    fixture and not about the reader. A control that cannot fail when the tier
    stops speaking is not controlling the tier.
    """
    bill = pdf_bytes(
        [
            "TAX INVOICE",
            f"SUPPLIER: {BOOKED}",
            "INVOICE NO: 2451",
            "TOTAL: 4200.00",
        ]
    )
    draft = build_draft(COMPANY, bill, "application/pdf", TextLayerReader(), _memory())

    assert draft.record.backend == TEXT_LAYER
    assert draft.record.confidence_of("party") == EXACT
    assert draft.record.read_exactly("party")

    assert draft.voucher.party == BOOKED
    assert draft.voucher.debit_account == "Purchases"
    assert draft.voucher.provenance is not None
    assert draft.voucher.provenance["party"] == TEXT_LAYER


def test_the_control_a_typed_party_flows_through_unchanged() -> None:
    """The second control, on the path the product actually ships on today.

    A person typed the name. There is no pixel and no estimate - the characters
    are the ones they entered - so `TypedTextExtractor` states `EXACT` and the
    name is an identity exactly as it always was.
    """
    tally = FakeTally()
    tally.add_company(
        COMPANY,
        accounts=ACCOUNTS,
        vouchers=tuple(_voucher(n, "Sharma Traders") for n in range(4)),
        backed_up=True,
    )
    memory = bootstrap(tally, COMPANY, MemoryStore(":memory:"))

    from accountant.extract.adapter import TypedTextExtractor

    draft = build_draft(COMPANY, TYPED, "text/plain", TypedTextExtractor(), memory)

    assert draft.voucher.party == "Sharma Traders"
    assert draft.voucher.debit_account == "Purchases"


def test_the_misread_supplier_neither_matches_nor_creates_a_ledger() -> None:
    """The specific case. `IVER. ELECTRICALS` must reach no vendor key at all.

    Both halves of F-03 are asserted, because they are different failures with
    the same cause. The misread name must not be looked up (it could match the
    wrong supplier), and it must not become a NEW key either (which is how a
    second ledger appears for a supplier already on the books).
    """
    memory = _memory()
    # The books answer for the right spelling and refuse the wrong one. That is
    # the situation, not the fix - it is why the wrong one is dangerous.
    assert propose_account(memory, BOOKED) == "Purchases"
    assert propose_account(memory, MISREAD) is None

    draft = _draft(_record(MISREAD, source="free_ocr", confidence=OCR_SCORE), memory)

    assert MISREAD not in (draft.voucher.party, draft.voucher.debit_account)
    assert memory.lookup(draft.voucher.party).accounts == ()


def test_the_estimate_is_kept_as_evidence_and_not_deleted() -> None:
    """Refusing to act on a reading is not the same as throwing it away.

    The record keeps the name, the source and the score, so the evidence page
    and `cage/gate.py` both still see exactly what the rung said. Only the
    VOUCHER - the thing that becomes a posting - says the name was not taken.
    """
    draft = _draft(_record(MISREAD, source="free_ocr", confidence=OCR_SCORE), _memory())

    assert draft.record.party == MISREAD
    assert draft.record.per_field_source["party"] == "free_ocr"
    assert draft.record.confidence_of("party") == OCR_SCORE
    assert draft.provenance["party"] == "free_ocr"

    assert draft.voucher.provenance is not None
    said = draft.voucher.provenance["party"]
    assert said.startswith(NOT_FOUND)
    assert "free_ocr" in said


def test_the_person_is_asked_who_it_was_rather_than_told_a_guess() -> None:
    """The existing path is reached, not a new one.

    `checks.party_is_named` fails on the blank leg, `problems.QUESTION_FOR`
    already maps that check to `questions.who_was_it`, and the person reads the
    sentence that question has always carried.
    """
    draft = _draft(_record(MISREAD, source="free_ocr", confidence=OCR_SCORE), _memory())

    assert not party_is_named(draft.voucher, ACCOUNTS).passed

    from accountant.pipeline import evaluate

    evaluate(draft, ACCOUNTS, (), _memory())
    asked = next_question(draft)

    assert asked is not None
    assert asked.text == who_was_it().text
    assert asked.problem_id == "party_is_named"


def test_the_sentence_on_the_leg_names_the_tier_that_guessed() -> None:
    """A provenance line nobody can act on is a blank with a label on it.

    The reviewer reading the voucher afterwards has to be able to tell WHICH of
    the two absences this is: nothing was read, or something was read and was
    not trusted enough to be a name.
    """
    said = ESTIMATED_NOT_AN_IDENTITY.format(backend="free_ocr")

    assert said.startswith(NOT_FOUND)
    assert "free_ocr" in said


# =============================================================================
# the other consumer: the cage's own door
# =============================================================================


def test_the_cage_sees_the_readers_score_rather_than_one_this_module_invented() -> None:
    """`gate.observed` stamped `EXACT` on every field any record said it read.

    That was correct for the two tiers that existed when it was written and
    became a fabrication the morning the picture rung went live: a party read at
    0.08 arrived at the decision layer as certainty, and `ASK_FLOOR` and
    `AUTO_POST_FLOOR` are both about exactly that number. Both of the owner's
    bands were defeated by one invented 1.0.

    The observation still CARRIES the misread name, at its own score. That is
    the difference between refusing to act on evidence and destroying it.
    """
    draft = _draft(_record(MISREAD, source="free_ocr", confidence=OCR_SCORE), _memory())
    seen = observed(draft)

    assert seen.party.value == MISREAD
    assert seen.party.confidence == OCR_SCORE
    assert seen.lowest_confidence < ASK_FLOOR


def test_the_control_the_cage_still_sees_a_text_layer_field_as_exact() -> None:
    """The control on the same line. Reading the score off the record must not
    quietly lower a tier that is entitled to 1.0."""
    bill = pdf_bytes(["TAX INVOICE", f"SUPPLIER: {BOOKED}", "TOTAL: 4200.00"])
    draft = build_draft(COMPANY, bill, "application/pdf", TextLayerReader(), _memory())

    assert observed(draft).party.confidence == EXACT


# =============================================================================
# the tiers say it themselves
# =============================================================================


def _smudged_letterhead(_data: bytes, _mime: str) -> Reading:
    """One page: a letterhead the engine is 8 out of 100 sure of, and a clean
    total.

    The two together are the point. A bill is not uniformly legible, so a single
    score for the record would average this 0.08 with the total's 0.99 and
    describe neither field.
    """
    return Reading(
        party=(
            Word(text="IVER.", confidence=8),
            Word(text="ELECTRICALS", confidence=92),
        ),
        total=(Word(text="420000", confidence=99),),
    )


def test_the_free_ocr_rung_states_its_own_confidence_on_the_record() -> None:
    """The rung already computed this per field. The record threw it away.

    `FreeReader.observe` has reported these numbers since the rung was written;
    `FreeReader.extract` built the record from the SAME `_Answer` three lines
    away and dropped them, because `ExtractedRecord` had nowhere to put them.

    Asserted against `observe` rather than against a literal: the invariant
    worth holding is that the record and the observation cannot say different
    things about one document, and a hand-typed 0.08 on both sides would pass
    even after they had come apart.
    """
    rung = FreeReader(_smudged_letterhead)

    record = rung.extract(b"", "image/png")
    seen = rung.observe(b"", "image/png")

    assert record.party == "IVER. ELECTRICALS"
    assert record.per_field_source["party"] == "free_ocr"
    assert record.confidence_of("party") == seen.party.confidence == 0.08
    assert record.confidence_of("total_paise") == seen.total_paise.confidence == 0.99
    assert not record.read_exactly("party")


def test_a_refused_reading_states_no_confidence_rather_than_a_confident_zero() -> None:
    """A backend that never looked says nothing, and nothing is not certain.

    `FreeReader` refuses through `UnavailableExtractor`, the one class in the
    package that builds an all-`not_found` record. It states no scores, which
    is the honest answer - the engine was never asked - and the default being
    absent rather than `EXACT` is what makes that answer safe.
    """
    refused = FreeReader(lambda _d, _m: None).extract(b"", "application/pdf")

    assert refused.per_field_confidence == {}
    assert not refused.read_exactly("party")
    assert refused.confidence_of("party") is None


# =============================================================================
# THE OTHER WAY A PHOTOGRAPH BECOMES AN IDENTITY, 2026-08-13
# =============================================================================
#
# Everything above is about a LOW score surviving the seam. Adversarial
# verification found the mirror case: a HIGH one. `field_confidence` is
# `min(word_confidence) / 100` with no ceiling, and 100 is a score the engine is
# contractually allowed to report - so a photograph can state 1.0, and
# `read_exactly` was a bare `== EXACT`. `IVER. ELECTRICALS` at 0.08 is refused
# and `IVER. ELECTRICALS` at 1.0 was not, which is the same misreading with a
# better opinion of itself.
#
# Measured on tesseract 5.5.3, the top word confidence is 96 over the twenty
# corpus PNGs and 97 over ~900 synthetic renders. So the only thing standing
# between this repository and that bug was a number nobody had pinned, in a
# binary nobody here builds.


def test_a_photograph_that_says_it_is_certain_still_names_no_supplier() -> None:
    """The F-03 assertion again, against a reading that claims exactness.

    Both halves, because they are different failures with the same cause: the
    name must not be looked up, and it must not become a new key either."""
    every_word_certain = Reading(
        party=(Word(MISREAD.split()[0], 100), Word(MISREAD.split()[1], 100)),
        total=(Word("4200.00", 100),),
    )
    record = FreeReader(lambda _d, _m: every_word_certain).extract(b"", "image/png")

    assert record.confidence_of("party") == EXACT
    assert not record.read_exactly("party")

    draft = _draft(record, _memory())

    assert MISREAD not in (draft.voucher.party, draft.voucher.debit_account)
    assert draft.voucher.party == ""


def test_the_control_the_same_certainty_from_the_text_layer_does_name_one() -> None:
    """THE CONTROL, and it is the whole reason this is a list of sources rather
    than a cap on the arithmetic.

    A rule written as "1.0 is never believed" would pass the test above and
    would cost the text-layer tier all twenty of its corpus parties. The
    difference is WHO read it, so the same 1.0 off real PDF bytes still names
    the supplier and still proposes the account the books already know."""
    bill = pdf_bytes(["TAX INVOICE", f"SUPPLIER: {BOOKED}", "TOTAL: 4200.00"])

    draft = build_draft(COMPANY, bill, "application/pdf", TextLayerReader(), _memory())

    assert draft.record.confidence_of("party") == EXACT
    assert draft.record.read_exactly("party")
    assert draft.voucher.party == BOOKED
    assert draft.voucher.debit_account == "Purchases"


def test_a_source_nobody_put_on_the_list_is_an_estimate_however_sure_it_sounds() -> (
    None
):
    """THE DIRECTION THE LIST FAILS IN, which is the property worth pinning.

    A backend added next year states `EXACT` and is not believed until somebody
    puts it on the list on purpose. Written against a name that is not on it
    rather than against a real backend, because the point is the default and a
    real backend would one day be added and quietly make this vacuous."""
    invented = _record(MISREAD, source="a_backend_from_next_year", confidence=EXACT)

    assert invented.confidence_of("party") == EXACT
    assert not invented.read_exactly("party")
    assert _draft(invented, _memory()).voucher.party == ""

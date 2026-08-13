"""A remote reader's 1.0 is its own certainty, never our exactness.

OWNER DECISION 3, 2026-08-13, verbatim
--------------------------------------
"No reader that works off pixels (local OCR or remote API) is allowed to be
treated as 'exact' in the sense of bypassing safety checks."

And the binding rule that goes with it, also verbatim: "A remote service may
return confidence: 1.0, and that is allowed. However this must be treated as
MAXIMUM CONFIDENCE FROM THIS READER, not as TRUTH. It must not: skip
conservation checks; skip GST / period / hard rules; change the decision from
BLOCK/ASK to AUTO-POST by itself."

THE DISTINCTION, BECAUSE THE WHOLE FILE TURNS ON IT
---------------------------------------------------
Confidence and exactness are two different things and this repository had them
as one.

    confidence   how sure THAT READER says it is. A number the reader states.
                 A remote service may state 1.0 and the number travels
                 unchanged - `cage/decision.py` weighs it against `ASK_FLOOR`
                 and `AUTO_POST_FLOOR` exactly as it weighs anybody else's.
    exactness    the claim that NOTHING WAS ESTIMATED. Not a number and not a
                 band: it is a fact about the TIER. Characters a program wrote
                 can make it; pixels a camera captured cannot, however sure the
                 thing reading the pixels sounds.

`adapter.ENTITLED_TO_EXACT` is the list of sources whose stated `EXACT` is read
as the second thing. `reader_service` was on it, so a paid API that read a
PHOTOGRAPH could answer `{"confidence": {"party": 1.0}}` and have that name
become a vendor identity at `pipeline.build_draft` with no question asked. That
is failure mode F-03 reached through a different first step, and its cost is one
supplier's balance wrong for ever.

WHAT WAS REMOVED AND WHAT WAS NOT
---------------------------------
Removed: the entitlement. `reader_service` is no longer believed when it claims
nothing was estimated.

NOT removed: the number, the name, the source line, or a single safety check.
The 1.0 is carried, is readable, and still counts as evidence. Every
conservation law and every hard rule still runs on the same record and still
refuses it in exactly the cases they refused it before - which is asserted here
rather than assumed, one law and one rule, because "the checks still apply" is
the half of the owner's sentence that no code change makes true by itself.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That any remote reader exists or is accurate. No transport ships with
`accountant/extract/service.py`; the "service" here is a function in this file
that returns a dict.

That a high confidence means a right answer. It does not - that is F-02, and
`accountant/cage/confidence.py` says so at length.

NO NETWORK, NO IO.
"""

from __future__ import annotations

import datetime
from dataclasses import replace

from accountant.cage.confidence import EXACT
from accountant.cage.decision import NUMBERS_DO_NOT_ADD_UP, Action, Decided, Moment
from accountant.cage.gate import gate
from accountant.extract.adapter import ENTITLED_TO_EXACT, LineItem
from accountant.extract.service import DOCUMENT_KEY, ServiceExtractor, document_key
from accountant.extract.textlayer import SOURCE as TEXT_LAYER
from accountant.extract.textlayer import TextLayerReader
from accountant.memory.bootstrap import bootstrap
from accountant.memory.company import CompanyMemory
from accountant.memory.store import MemoryStore
from accountant.pipeline import Draft, build_draft
from accountant.schema import Voucher
from accountant.tallyio.fake import FakeTally

# The PDF builder, borrowed rather than copied, for the same reason
# `tests/test_estimated_party.py` borrows it: a second hand-rolled copy is how
# the two drift into testing different file formats.
from tests.test_textlayer import pdf_bytes

COMPANY = "Nagpur Hardware Stores"
ACCOUNTS = ("Purchases", "Cash")

#: The supplier already on the books, spelled the way the books spell it.
BOOKED = "IYER ELECTRICALS"

TOTAL = 420_000
DATE = datetime.date(2026, 8, 12)
BILL = b"a bill the remote service read for us"

#: The name the record's source line carries for a remote reading.
#: `ServiceExtractor`'s own default, not a second spelling of it.
REMOTE = "reader_service"


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
    """A company whose books already know `BOOKED`, four times over.

    Four, so `propose_account` has a single confident answer. A test where the
    name IS taken must be able to reach "Purchases"; otherwise the refusals
    below would pass on a company that could not have named an account anyway.
    """
    tally = FakeTally()
    tally.add_company(
        COMPANY,
        accounts=ACCOUNTS,
        vouchers=tuple(_voucher(n, BOOKED) for n in range(4)),
        backed_up=True,
    )
    return bootstrap(tally, COMPANY, MemoryStore(":memory:"))


def remote(**fields: object) -> ServiceExtractor:
    """A backend whose remote service answers about `BILL`, saying this.

    Built through the real `ServiceExtractor` rather than by handing a record
    over, so the source string, the confidence map and the record shape are the
    ones the shipped adapter produces. A hand-built record would pass this file
    on the day `service.py` stopped stating a source at all.
    """
    answer: dict[str, object] = {
        DOCUMENT_KEY: document_key(BILL),
        "date": None,
        "party": None,
        "total_paise": None,
        "tax_paise": None,
    } | fields

    def call(_data: bytes, _mime: str, _key: str) -> object:
        return answer

    return ServiceExtractor(call)


def certain_remote() -> ServiceExtractor:
    """The case the owner ruled on: a remote reader stating full certainty."""
    return remote(
        date=DATE,
        party=BOOKED,
        total_paise=TOTAL,
        tax_paise=0,
        confidence={
            "date": EXACT,
            "party": EXACT,
            "total_paise": EXACT,
            "tax_paise": EXACT,
        },
    )


# =============================================================================
# the number is kept; the authority is not
# =============================================================================


def test_a_remote_reader_at_full_confidence_names_no_supplier() -> None:
    """OWNER DECISION 3. A 1.0 off pixels does not become a vendor identity.

    `pipeline.build_draft:320` is the one line in the product that turns a read
    name into an identity, and it asks `read_exactly`. Both halves are asserted
    because they are different failures with the same cause: the name must not
    be written on the voucher, and it must not be looked up to propose an
    account either.
    """
    draft = build_draft(COMPANY, BILL, "text/plain", certain_remote(), _memory())

    assert not draft.record.read_exactly("party")
    assert draft.voucher.party == ""
    assert draft.voucher.debit_account == ""


def test_the_remote_reader_s_own_number_survives_with_its_authority_removed() -> None:
    """THE OTHER HALF OF THE OWNER'S RULE, and the one a fix breaks by accident.

    "A remote service may return confidence: 1.0, and that is allowed." So the
    number is not clamped, not dropped and not rewritten - it is exactly 1.0 on
    the record, readable by the evidence page and by the cage. The name and the
    reader's own source line survive with it, because withholding an identity is
    not the same as destroying a reading.
    """
    record = certain_remote().extract(BILL, "text/plain")

    assert record.confidence_of("party") == EXACT == 1.0
    assert record.party == BOOKED
    assert record.per_field_source["party"] == REMOTE
    assert not record.read_exactly("party")


def test_the_control_a_text_layer_party_at_full_confidence_still_names_one() -> None:
    """THE CONTROL. Without it, a change that refused EVERY 1.0 would pass.

    The text-layer tier reads the characters the producing program wrote, states
    the same 1.0, and is still entitled to it: it names the supplier and
    proposes the account the books already know. The difference is WHO read it,
    which is why this is a list of sources and not a cap on a number.

    THE REAL TIER ON REAL PDF BYTES, not a record built by hand at the shape it
    produces - a control that cannot fail when the tier stops speaking is not
    controlling the tier.
    """
    bill = pdf_bytes(["TAX INVOICE", f"SUPPLIER: {BOOKED}", "TOTAL: 4200.00"])

    draft = build_draft(COMPANY, bill, "application/pdf", TextLayerReader(), _memory())

    assert draft.record.confidence_of("party") == EXACT
    assert draft.record.read_exactly("party")
    assert draft.record.per_field_source["party"] == TEXT_LAYER
    assert draft.voucher.party == BOOKED
    assert draft.voucher.debit_account == "Purchases"


def test_the_believed_sources_are_exactly_these_three() -> None:
    """The membership, asserted rather than described, so nothing rejoins quietly.

    A set equality and not three `in` checks: `in` cannot see an addition, and an
    addition is the whole failure mode. `reader_service` was removed here on
    2026-08-13 by owner decision 3 and putting it back must fail a test.
    """
    assert frozenset({"pdf_text_layer", "typed_text", "stub"}) == ENTITLED_TO_EXACT
    assert REMOTE not in ENTITLED_TO_EXACT


# =============================================================================
# the checks the 1.0 does not buy its way past
# =============================================================================
#
# `gate` is asked directly here rather than through `pipeline.run`, for the
# reason `tests/test_gate.py` gives: the three world facts have no defaults and
# no shipped caller looks them up yet, so this is the only place the hard rules
# can be put a real question. The draft is built by hand with both accounts
# named, because a blank account is a QUESTION and would hide the refusals below
# behind a different outcome.


def _remote_draft() -> Draft:
    """A draft off the certain remote reading, with both ledger legs named.

    THE LINE ITEMS ARE ADDED HERE AND ARE NOT THE SERVICE'S, and it is worth
    saying which: no reader in this repository fills `line_items` at all, so
    `conservation.lines_sum_to_total` is INDETERMINATE for every real record and
    INDETERMINATE is a hard block. Left alone, the control below could not reach
    POST for a reason that has nothing to do with who read the bill, and the two
    refusals after it would be passing on a cage that refuses everything.
    Everything the SERVICE said - the values, the source lines, the 1.0 - is
    carried over untouched by `replace`.
    """
    read = certain_remote().extract(BILL, "text/plain")
    record = replace(read, line_items=(LineItem("switchgear", TOTAL),))
    voucher = Voucher(
        id="draft-remote",
        date=DATE,
        party=BOOKED,
        narration="",
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=TOTAL,
        provenance=dict(record.per_field_source),
    )
    return Draft(
        id=voucher.id,
        company=COMPANY,
        voucher=voucher,
        record=record,
        operation_id="op-remote",
    )


def _decide(
    *,
    period_open: bool | None = True,
    net_paise: int | None = TOTAL,
) -> Decided:
    """Every fact supplied and every law holding, so a test moves ONE thing."""
    return gate(
        _remote_draft(),
        moment=Moment.BEFORE_THE_WRITE,
        party_known=True,
        period_open=period_open,
        carries_gst=False,
        pdf_repaired=None,
        questions_asked=0,
        net_paise=net_paise,
        balance_before_paise=None,
        balance_after_paise=None,
    )


def test_the_control_a_certain_remote_reading_is_asked_about_not_refused() -> None:
    """THE CONTROL on the two refusals below. Without it they pass on a cage
    that has started refusing everything, which is the fix that looks like a fix.

    ASK AND NOT POST, and the difference is the last clause of the owner's rule:
    a 1.0 must not "change the decision from BLOCK/ASK to AUTO-POST by itself".
    It does not, and it is now stopped twice over - `ENTITLED_TO_EXACT` here, and
    `decision.AUTO_POST_ALLOWED_TIERS` from owner decision 2 the same day, which
    lets `pdf_text_layer` auto-post and nothing else. Two independent mechanisms
    is not duplication: one governs IDENTITY at the pipeline seam and the other
    governs AUTO-POST in the cage, and neither is reachable from the other.

    What the control still proves is that nothing here BLOCKS. The reading's 1.0
    is real evidence, every law holds, and the answer is a question to the person
    rather than a refusal - so the two blocks below are about what they say they
    are about.
    """
    assert _decide().action is Action.ASK


def test_a_certain_remote_reading_still_faces_the_conservation_laws() -> None:
    """A CONSERVATION CHECK, not skipped. Owner's rule, first clause.

    The net is one paisa short of the gross, `net_plus_tax_equals_gross` comes
    back FAIL, and a failing law is a hard block. The reading still says 1.0
    about every field it read; nothing about that number touches the arithmetic.
    """
    decided = _decide(net_paise=TOTAL - 1)

    assert decided.action is Action.BLOCK
    assert NUMBERS_DO_NOT_ADD_UP in decided.said


def test_a_certain_remote_reading_still_faces_a_hard_rule() -> None:
    """A HARD RULE, not skipped. Owner's rule, second clause - the period.

    The books for the date on this bill are closed. No confidence from any
    reader is an argument about somebody else's financial year, and a 1.0 that
    could talk this refusal into an auto-post is exactly what the owner refused.
    """
    decided = _decide(period_open=False)

    assert decided.action is Action.BLOCK
    assert "closed" in decided.said

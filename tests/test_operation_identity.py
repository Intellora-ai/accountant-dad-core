"""G5.1 — one operation, one identity, carried by everything that touches it.

Phase 5's rule, from `docs/ARCHITECTURE.md` §7: the operation ID is generated
before posting and is carried by all five of the draft, the decision, the Tally
narration, the action log, and the reversal request.

MEASURED BEFORE THIS FILE: four of the five carried it.

    Draft.operation_id       pipeline.py:54      carried
    Tally narration          stamp()             carried
    ActionLog.operation_id   schema.py:153       carried
    reversal request         reverse_operation   carried
    Decision                 schema.py:113-125   *** NOT CARRIED ***

`Decision` held `outcome`, `reason` and `question_options` and nothing else. So
the artefact that AUTHORISES a write could not be tied to the write it
authorised. Nothing read the link, so nothing broke — which is exactly why it
survived: an identity nobody checks looks like an identity nobody needs, right
up to the reconciliation where somebody has to prove which decision put a
voucher in their books.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about a real TallyPrime. Every test here runs against `FakeTally`.
Evidence class: FAKETALLY. It proves the identity is threaded and enforced in
our code, and says nothing about what Tally stores.
"""

from __future__ import annotations

import datetime
from dataclasses import replace

import pytest

from accountant import pipeline
from accountant.extract.adapter import TypedTextExtractor
from accountant.memory.bootstrap import bootstrap
from accountant.memory.company import CompanyMemory
from accountant.memory.store import MemoryStore
from accountant.schema import Decision, Outcome, Voucher
from accountant.tallyio.client import operation_id_in
from accountant.tallyio.fake import FakeTally
from tests.test_period_handoff import open_books_for

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Repairs & Maintenance", "Cash", "Bank")
TODAY = datetime.date(2026, 8, 9)
KNOWN = b"paid Sharma Traders 4200 for cement"
UNKNOWN = b"paid Gupta Hardware 1500 for tools"


def history(n: int = 8) -> list[Voucher]:
    return [
        Voucher(
            id=f"h{i}",
            date=datetime.date(2026, 1, 1) + datetime.timedelta(days=i),
            party="Sharma Traders",
            narration="cement supply",
            debit_account="Purchases",
            credit_account="Cash",
            amount_paise=100_000,
        )
        for i in range(n)
    ]


def books() -> tuple[FakeTally, CompanyMemory, MemoryStore]:
    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, vouchers=tuple(history()), backed_up=True)
    store = MemoryStore(":memory:")
    return t, bootstrap(t, COMPANY, store), store


# ---- the identity reaches all five ------------------------------------------


def test_one_operation_id_reaches_the_draft_decision_narration_log_and_reversal():
    """The whole of G5.1 in one assertion chain, on the path `run` actually uses."""
    tally, memory, store = books()

    draft = pipeline.run(
        COMPANY,
        KNOWN,
        "text/plain",
        TypedTextExtractor(),
        tally,
        memory,
        today=TODAY,
        log=store,
        run_id="run-g51",
        period_reader=open_books_for(COMPANY),
    )
    assert draft.outcome is Outcome.VALID
    op = draft.operation_id
    assert op, "an operation id is minted before anything external happens"

    # 1. the draft
    assert draft.operation_id == op
    # 2. the decision
    assert draft.decision is not None
    assert draft.decision.operation_id == op
    # 3. the Tally narration
    written = tally.list_our_vouchers(COMPANY)
    assert len(written) == 1
    assert operation_id_in(written[0].narration) == op
    # 4. the action log — every row about this entry, not merely one
    rows = [r for r in store.actions(COMPANY) if r.operation_id]
    assert rows, "a write leaves durable rows"
    assert {r.operation_id for r in rows} == {op}
    # 5. the reversal request
    result = pipeline.reverse_operation(tally, COMPANY, op)
    assert result.reversed_ is True
    assert result.operation_id == op


def test_the_operation_id_is_minted_once_and_never_regenerated():
    """Re-evaluating, answering, and re-evaluating again all keep one identity.

    A regenerated id would orphan the write-ahead audit row: the `write_attempted`
    row would name an operation nothing else ever mentions again.
    """
    tally, memory, _ = books()
    accounts = tally.read_accounts(COMPANY)
    hist = tally.read_vouchers(COMPANY)

    draft = pipeline.build_draft(
        COMPANY,
        UNKNOWN,
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    minted = draft.operation_id

    draft = pipeline.evaluate(
        draft, accounts, hist, memory, period_open=None, pdf_repaired=None
    )
    assert draft.operation_id == minted
    assert draft.outcome is Outcome.UNCLEAR

    question = pipeline.next_question(draft)
    assert question is not None
    draft = pipeline.answer(draft, question.answers[0].value, question.problem_id)
    assert draft.operation_id == minted

    draft = pipeline.evaluate(
        draft, accounts, hist, memory, period_open=None, pdf_repaired=None
    )
    assert draft.operation_id == minted
    assert draft.decision is not None
    assert draft.decision.operation_id == minted


def test_answering_carries_the_identity_onto_the_new_decision():
    """`answer` clears the decision. The next one must be born with the same id."""
    tally, memory, _ = books()
    accounts = tally.read_accounts(COMPANY)
    hist = tally.read_vouchers(COMPANY)

    draft = pipeline.build_draft(
        COMPANY,
        UNKNOWN,
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    draft = pipeline.evaluate(
        draft, accounts, hist, memory, period_open=None, pdf_repaired=None
    )
    first = draft.decision
    assert first is not None and first.operation_id == draft.operation_id

    q = pipeline.next_question(draft)
    assert q is not None
    draft = pipeline.answer(draft, q.answers[0].value, q.problem_id)
    assert draft.decision is None, "answering clears it — Phase 4 exit 2"

    draft = pipeline.evaluate(
        draft, accounts, hist, memory, period_open=None, pdf_repaired=None
    )
    assert draft.decision is not None
    assert draft.decision.operation_id == draft.operation_id


# ---- the identity is ENFORCED, not merely present ---------------------------


def test_a_decision_carrying_no_operation_id_cannot_authorise_a_write():
    """The fail-closed half. A `Decision` built without an id authorises nothing.

    This is what makes the field load-bearing rather than decorative. Without
    it, adding `operation_id` to `Decision` would be a comment in a dataclass.
    """
    tally, memory, _ = books()
    accounts = tally.read_accounts(COMPANY)
    hist = tally.read_vouchers(COMPANY)

    draft = pipeline.build_draft(
        COMPANY,
        KNOWN,
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    draft = pipeline.evaluate(
        draft, accounts, hist, memory, period_open=True, pdf_repaired=None
    )
    assert draft.outcome is Outcome.VALID

    draft.decision = replace(draft.decision, operation_id="")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="operation id"):
        pipeline.post(draft, tally)

    assert tally.list_our_vouchers(COMPANY) == (), "and nothing was written"


def test_a_decision_that_authorised_a_different_operation_is_refused():
    """The mismatch half. A decision cannot be attached to another operation.

    The concrete shape this guards: two drafts in flight, and the wrong
    decision object assigned to one of them. Today `DRAFTS` holds up to 200 at
    once, so two live drafts is the normal case, not an exotic one.
    """
    tally, memory, _ = books()
    accounts = tally.read_accounts(COMPANY)
    hist = tally.read_vouchers(COMPANY)

    mine = pipeline.evaluate(
        pipeline.build_draft(
            COMPANY,
            KNOWN,
            "text/plain",
            TypedTextExtractor(),
            memory,
            today=TODAY,
        ),
        accounts,
        hist,
        memory,
        period_open=True,
        pdf_repaired=None,
    )
    theirs = pipeline.evaluate(
        pipeline.build_draft(
            COMPANY,
            KNOWN,
            "text/plain",
            TypedTextExtractor(),
            memory,
            today=TODAY,
        ),
        accounts,
        hist,
        memory,
        period_open=True,
        pdf_repaired=None,
    )
    assert mine.operation_id != theirs.operation_id

    mine.decision = theirs.decision

    with pytest.raises(ValueError, match="different operation"):
        pipeline.post(mine, tally)

    assert tally.list_our_vouchers(COMPANY) == (), "and nothing was written"


def test_the_refusal_names_both_ids_so_a_person_can_reconcile():
    """An error that says "mismatch" and no more sends somebody to the logs."""
    tally, memory, _ = books()
    accounts = tally.read_accounts(COMPANY)
    hist = tally.read_vouchers(COMPANY)

    draft = pipeline.evaluate(
        pipeline.build_draft(
            COMPANY,
            KNOWN,
            "text/plain",
            TypedTextExtractor(),
            memory,
            today=TODAY,
        ),
        accounts,
        hist,
        memory,
        period_open=True,
        pdf_repaired=None,
    )
    draft.decision = replace(draft.decision, operation_id="ad_someone_else")  # type: ignore[arg-type]

    with pytest.raises(ValueError) as refused:
        pipeline.post(draft, tally)

    message = str(refused.value)
    assert draft.operation_id in message
    assert "ad_someone_else" in message


# ---- the property the write path reads --------------------------------------


def test_an_unidentified_decision_does_not_report_itself_as_postable():
    """`Decision.post` is documented as the only thing the write path may read.

    So the identity requirement lives there too, not only in `pipeline.post`.
    A second reader added tomorrow inherits the guarantee instead of having to
    remember it.
    """
    identified = Decision(outcome=Outcome.VALID, reason="x", operation_id="ad_1")
    anonymous = Decision(outcome=Outcome.VALID, reason="x")
    unclear = Decision(outcome=Outcome.UNCLEAR, reason="x", operation_id="ad_1")

    assert identified.post is True
    assert anonymous.post is False
    assert unclear.post is False


def test_a_blocked_decision_also_carries_the_identity():
    """Not only the authorising ones. "Why was this refused, and for which
    operation" is the question asked when a voucher is missing from the books."""
    tally, memory, _ = books()
    accounts = tally.read_accounts(COMPANY)
    hist = tally.read_vouchers(COMPANY)

    draft = pipeline.evaluate(
        pipeline.build_draft(
            COMPANY,
            UNKNOWN,
            "text/plain",
            TypedTextExtractor(),
            memory,
            today=TODAY,
        ),
        accounts,
        hist,
        memory,
        period_open=None,
        pdf_repaired=None,
    )
    assert draft.outcome is Outcome.UNCLEAR
    assert draft.decision is not None
    assert draft.decision.operation_id == draft.operation_id

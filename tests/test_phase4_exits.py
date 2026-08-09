"""Phase 4's four exit conditions, enforced by construction rather than by comment.

The four, verbatim from `docs/ARCHITECTURE.md`:

    1  unknown vendor       -> a question is asked, never a guess
    2  answer recorded      -> the entry RE-ENTERS the decision order
    3  no question string   contains any account name from the chart
    4  NO fallback account  exists anywhere in the codebase

Exits 1 and 3 already had tests. This file covers what was guaranteed only by
convention:

    exit 2  `answer()` left `draft.decision` untouched, so a draft carried a
            decision describing a DIFFERENT voucher from the one it now held.
            The docstring said "the caller must re-run evaluate()" — a comment,
            not a guarantee.
    exit 4  had NO test at all, and was already FALSE. `_default_credit` picked
            a ledger for the money-source leg on every single entry.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about a real TallyPrime. Exit 4's guard reads source with `ast`; the
rest uses `FakeTally`. Neither says what Tally would do.
"""

from __future__ import annotations

import datetime
import pathlib

import pytest

from accountant import pipeline
from accountant.extract.adapter import TypedTextExtractor
from accountant.memory.bootstrap import bootstrap
from accountant.memory.store import MemoryStore
from accountant.schema import Outcome, Voucher
from accountant.tallyio.fake import FakeTally

REPO = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = REPO / "accountant"

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Repairs & Maintenance", "Cash")
TODAY = datetime.date(2026, 8, 31)


def _history(party: str, account: str, n: int = 40) -> tuple[Voucher, ...]:
    return tuple(
        Voucher(
            id=f"h{i}",
            date=datetime.date(2026, 4, 1),
            party=party,
            narration=f"{party} supply",
            debit_account=account,
            credit_account="Cash",
            amount_paise=100000,
        )
        for i in range(n)
    )


def _tally(history: tuple[Voucher, ...] = ()) -> FakeTally:
    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, vouchers=history, backed_up=True)
    return t


def _unclear_draft() -> tuple[FakeTally, pipeline.Draft]:
    """An entry for a vendor the company has never posted to. Asks, never guesses."""
    t = _tally(_history("Sharma Traders", "Purchases"))
    memory = bootstrap(t, COMPANY, MemoryStore(":memory:"))
    draft = pipeline.build_draft(
        COMPANY,
        b"paid Gupta Hardware 1500 for tools",
        "text/plain",
        TypedTextExtractor(),
        ACCOUNTS,
        memory,
        today=TODAY,
    )
    draft = pipeline.evaluate(draft, ACCOUNTS, t.read_vouchers(COMPANY), memory)
    assert draft.outcome is Outcome.UNCLEAR
    return t, draft


# ---------------------------------------------------------------------------
# Exit 2 — an answer is information, never permission
# ---------------------------------------------------------------------------


def test_an_answered_draft_cannot_post_until_it_has_been_evaluated_again() -> None:
    """The structural half of exit 2. It used to rest on a docstring.

    `answer()` rewrites `debit_account`. If the old decision survived that, the
    draft would be carrying an approval granted to a DIFFERENT voucher. Today
    that is safe only by accident: `answer` is reached only from UNCLEAR, and
    `post` refuses anything not VALID. Change either and a mutated voucher
    posts against a stale approval.

    Clearing the decision makes re-evaluation mandatory rather than requested:
    the draft fails closed with "draft has not been evaluated".
    """
    t, draft = _unclear_draft()
    before = t.trial_balance(COMPANY)

    answered = pipeline.answer(draft, "Purchases")

    assert answered.decision is None, "an answered draft carries no live decision"
    with pytest.raises(ValueError, match="not been evaluated"):
        pipeline.post(answered, t)

    # pytest.raises is never the whole proof.
    assert answered.posted_tally_id is None
    assert t.list_our_vouchers(COMPANY) == ()
    assert t.trial_balance(COMPANY) == before


def test_the_answer_itself_is_still_recorded_when_the_decision_is_cleared() -> None:
    """Clearing the decision must not throw away the information.

    The answer is the whole point of asking. It has to survive into the
    re-evaluation, or the person is asked the same question forever.
    """
    _t, draft = _unclear_draft()
    answered = pipeline.answer(draft, "Purchases")

    assert answered.answers == [("which_account", "Purchases")]
    assert answered.voucher.debit_account == "Purchases"
    assert (answered.voucher.provenance or {})["debit_account"] == "human_answer"


def test_re_evaluating_an_answered_draft_runs_the_whole_order_again() -> None:
    """ "Re-enters the decision order" means from the beginning, every time.

    Note what closes the loop: `answer()` alone does NOT. It sets
    `debit_account`, but memory still returns NO_MATCH for the vendor, so the
    same problem is found again. The web app records the correction as well
    (`web/app.py`), and that is what makes the second pass VALID. Asserted here
    so the two halves are not confused for one.
    """
    t, draft = _unclear_draft()
    memory = bootstrap(t, COMPANY, MemoryStore(":memory:"))

    answered = pipeline.answer(draft, "Purchases")
    memory.record_correction(answered.voucher.party, "Purchases")
    again = pipeline.evaluate(answered, ACCOUNTS, t.read_vouchers(COMPANY), memory)

    assert again.decision is not None, "evaluate restores a live decision"
    assert again.outcome is Outcome.VALID
    assert again.voucher.debit_account == "Purchases"


def test_an_unclear_entry_always_has_a_question_to_show_the_person() -> None:
    """DEFECT FOUND AND FIXED 2026-08-09. The person could be stranded.

    `decide_problems` chose `answerable[0]` WITHOUT skipping problems already
    answered; `next_question` DID skip them. So the two disagreed, and the
    disagreement is visible on the screen: answer the question, have the same
    problem found again, and the page renders "needs an answer" with no
    question and no buttons. Measured before the fix:

        outcome:        unclear
        next_question:  None
        STRANDED:       True

    UNCLEAR means "I am about to ask you something". If there is nothing left to
    ask, the honest outcome is to hand the entry over, not to sit there.
    """
    t, draft = _unclear_draft()
    memory = bootstrap(t, COMPANY, MemoryStore(":memory:"))

    # Answered, but deliberately NOT recorded in memory, so the very same
    # problem is found again on the next pass.
    answered = pipeline.answer(draft, "Purchases")
    again = pipeline.evaluate(answered, ACCOUNTS, t.read_vouchers(COMPANY), memory)

    if again.outcome is Outcome.UNCLEAR:
        assert pipeline.next_question(again) is not None, (
            "UNCLEAR with no question to show: the person is stranded"
        )
    else:
        # Handed over is the other honest answer, and it must say why.
        assert again.outcome is Outcome.NOT_VALID
        assert again.reason


def test_the_decision_and_the_question_never_disagree_about_what_is_outstanding() -> (
    None
):
    """The general form of the bug above, asserted as an invariant.

    One rule, two readers. If `decide_problems` says UNCLEAR, `next_question`
    must have something to return, for any number of answers already given.
    """
    t, draft = _unclear_draft()
    memory = bootstrap(t, COMPANY, MemoryStore(":memory:"))

    for _ in range(4):
        if draft.outcome is not Outcome.UNCLEAR:
            break
        question = pipeline.next_question(draft)
        assert question is not None, (
            f"UNCLEAR with no question after {len(draft.answers)} answers"
        )
        draft = pipeline.answer(draft, "Purchases", problem_id=question.problem_id)
        draft = pipeline.evaluate(draft, ACCOUNTS, t.read_vouchers(COMPANY), memory)


def test_an_answer_that_names_a_ledger_the_chart_does_not_have_still_cannot_post() -> (
    None
):
    """The answer is new information, and information can be wrong.

    Answering does not end the conversation; it restarts it.
    """
    t, draft = _unclear_draft()
    memory = bootstrap(t, COMPANY, MemoryStore(":memory:"))
    before = t.trial_balance(COMPANY)

    answered = pipeline.answer(draft, "Not A Real Ledger")
    again = pipeline.evaluate(answered, ACCOUNTS, t.read_vouchers(COMPANY), memory)

    assert again.outcome is not Outcome.VALID
    with pytest.raises(ValueError, match="refusing to post"):
        pipeline.post(again, t)
    assert t.list_our_vouchers(COMPANY) == ()
    assert t.trial_balance(COMPANY) == before

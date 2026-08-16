"""The funding leg — where the money came FROM — and the guess that used to fill it.

WHAT THIS FILE EXISTS TO PIN
----------------------------
`pipeline._default_credit`, deleted 2026-08-09:

    for preferred in ("Cash", "Bank", "Sundry Creditors"):
        if preferred in accounts:
            return preferred
    return accounts[0] if accounts else "Cash"

It ran on EVERY entry. It read nothing about the vendor, nothing about the
company's history, and it wrote no provenance. Its last line returned the
literal string "Cash" even for a company with no such ledger.

It survived 1,026 green tests and a 94% mutation score for one reason: every
test chart in this repository contains "Cash", so the first loop iteration
always matched and the three later branches never executed once. A guess that
happens to agree with every fixture is indistinguishable from knowledge until
you write the fixture where they disagree. This file is those fixtures.

Phase 4 exit 4, verbatim from `docs/ARCHITECTURE.md:864`:

    NO fallback account exists anywhere in the codebase

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Every test here runs against `FakeTally`. None of it is evidence about real
TallyPrime. The live claim it supports — that a real company's own posted
credit legs are readable and unanimous — is recorded separately in
`docs/PROJECT_STATE.md` under the RealTally evidence class, and is not merged
with anything here.
"""

from __future__ import annotations

import datetime
from dataclasses import replace

import pytest

from accountant import checks, pipeline, problems
from accountant import questions as Q
from accountant.extract.adapter import NOT_FOUND, TypedTextExtractor
from accountant.memory.bootstrap import bootstrap
from accountant.memory.company import CompanyMemory
from accountant.memory.store import MemoryStore
from accountant.schema import CheckResult, MatchResult, MatchStatus, Outcome, Voucher
from accountant.tallyio.fake import FakeTally

COMPANY = "Demo Co"
CHART = ("Purchases", "Repairs & Maintenance", "Sundry Creditors", "Cash", "Bank")
TODAY = datetime.date(2026, 8, 9)


def paid(party: str, debit: str, credit: str, n: int = 1) -> list[Voucher]:
    """History rows that say both what the money was for AND where it came from."""
    return [
        Voucher(
            id=f"h-{party}-{credit}-{i}",
            date=datetime.date(2026, 1, 1) + datetime.timedelta(days=i),
            party=party,
            narration=f"{party} supply",
            debit_account=debit,
            credit_account=credit,
            amount_paise=100_000,
        )
        for i in range(n)
    ]


def books(
    history: list[Voucher], chart: tuple[str, ...] = CHART
) -> tuple[FakeTally, CompanyMemory]:
    t = FakeTally()
    t.add_company(COMPANY, accounts=chart, vouchers=tuple(history), backed_up=True)
    return t, bootstrap(t, COMPANY, MemoryStore(":memory:"))


def draft_for(t: FakeTally, memory: CompanyMemory, text: str):
    chart = t.read_accounts(COMPANY)
    d = pipeline.build_draft(
        COMPANY,
        text.encode(),
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    return pipeline.evaluate(
        d, chart, t.read_vouchers(COMPANY), memory, period_open=None, pdf_repaired=None
    )


# ---- proposed from this company's own history, or not at all ----------------


def test_a_vendor_always_paid_in_cash_has_its_funding_leg_read_not_guessed():
    t, memory = books(paid("Sharma Traders", "Purchases", "Cash", n=40))
    d = draft_for(t, memory, "paid Sharma Traders 4200 for cement")

    assert d.outcome is Outcome.VALID, "nothing is unclear, so nothing is asked"
    assert d.voucher.credit_account == "Cash"
    assert (d.voucher.provenance or {})["credit_account"] == "company_history"


def test_the_funding_leg_is_not_cash_merely_because_cash_is_a_common_account():
    """The disconfirming case for the test above, and the one that kills the guess.

    `Cash` IS in this chart, and `_default_credit` preferred it above everything
    else, so it would answer "Cash" here. This vendor has only ever been paid
    from the bank. Same chart, same code path, opposite answer — which is what
    tells you the value came from the books and not from a preference list.
    """
    t, memory = books(paid("Verma Cement", "Purchases", "Bank", n=12))
    d = draft_for(t, memory, "paid Verma Cement 900 for bags")

    assert "Cash" in t.read_accounts(COMPANY), "the tempting wrong answer is available"
    assert d.voucher.credit_account == "Bank"
    assert d.outcome is Outcome.VALID


def test_a_vendor_paid_two_different_ways_is_asked_never_out_voted():
    """A conflict is a question. Not a majority, not the most recent, not the first.

    Nine of these ten vouchers say Cash. A vote would answer "Cash" and be
    right nine times in ten, which is exactly the failure mode: the tenth is
    silent and wrong, in somebody's statutory books.
    """
    history = paid("Kumar Motors", "Repairs & Maintenance", "Cash", n=9) + paid(
        "Kumar Motors", "Repairs & Maintenance", "Bank", n=1
    )
    t, memory = books(history)
    d = draft_for(t, memory, "paid Kumar Motors 1500 for repairs")

    assert d.voucher.credit_account == "", "no leg is chosen out of a conflict"
    assert d.outcome is Outcome.UNCLEAR
    q = pipeline.next_question(d)
    assert q is not None and q.problem_id == pipeline.FUNDING_PROBLEM


def test_an_unseen_vendor_is_asked_how_it_was_paid_and_nothing_is_written():
    t, memory = books(paid("Sharma Traders", "Purchases", "Cash", n=40))
    d = draft_for(t, memory, "paid Gupta Hardware 1500 for tools")

    assert d.voucher.credit_account == ""
    assert d.outcome is Outcome.UNCLEAR
    assert t.list_our_vouchers(COMPANY) == ()

    with pytest.raises(ValueError, match="outcome is unclear"):
        pipeline.post(d, t)
    assert t.list_our_vouchers(COMPANY) == ()


def test_one_vendors_funding_habit_never_leaks_onto_another():
    """Unanimity is per vendor, not per company.

    A company that pays everyone in cash except its landlord must not have the
    landlord's rent credited to Cash because forty other vouchers say so.
    """
    t, memory = books(
        paid("Sharma Traders", "Purchases", "Cash", n=40)
        + paid("Landlord", "Repairs & Maintenance", "Bank", n=12)
    )

    assert (
        draft_for(
            t, memory, "paid Sharma Traders 4200 for cement"
        ).voucher.credit_account
        == "Cash"
    )
    assert (
        draft_for(t, memory, "paid Landlord 30000 for rent").voucher.credit_account
        == "Bank"
    )


def test_a_blank_party_never_pools_with_the_other_blank_parties():
    """The empty vendor key is not a vendor. It would otherwise collect everyone.

    `normalise_vendor("")` is `""`, and every history row with no party shares
    it. Matching on that key would make "the vendor nobody named" the most
    consistently-paid supplier in the books.
    """
    history = paid("", "Purchases", "Cash", n=30)

    assert pipeline.funding_from_history("", history) == ""
    assert pipeline.funding_from_history("   ", history) == ""


# ---- the question, when there is one ----------------------------------------


def test_the_funding_question_offers_only_ledgers_this_company_actually_has():
    t, memory = books(
        paid("Sharma Traders", "Purchases", "Cash", n=5), chart=("Purchases", "Cash")
    )
    chart = t.read_accounts(COMPANY)
    d = draft_for(t, memory, "paid Gupta Hardware 1500 for tools")
    d = pipeline.answer(d, "Purchases", problem_id="which_account")
    memory.record_correction(d.voucher.party, "Purchases")
    d = pipeline.evaluate(
        d, chart, t.read_vouchers(COMPANY), memory, period_open=None, pdf_repaired=None
    )

    q = pipeline.next_question(d)
    assert q is not None and q.problem_id == pipeline.FUNDING_PROBLEM
    assert [a.value for a in q.answers] == ["Cash"], "Bank is not in this chart"
    assert all(a.value in chart for a in q.answers)


def test_a_company_with_neither_cash_nor_bank_is_refused_not_offered_a_new_ledger():
    """An option is a promise that the thing exists.

    The deleted code's final line returned the literal "Cash" for exactly this
    company. The person clicks it, and Tally refuses a ledger that was never
    created — or worse, TDL creates it.
    """
    chart = ("Purchases", "Sundry Creditors")
    t, memory = books(
        paid("Sharma Traders", "Purchases", "Sundry Creditors", n=3), chart=chart
    )

    with pytest.raises(Q.NoAnswerableOption, match="no Cash and no Bank"):
        Q.how_paid(chart)

    d = draft_for(t, memory, "paid Gupta Hardware 1500 for tools")
    assert d.outcome is Outcome.NOT_VALID
    assert "no honest way to ask" in d.reason
    assert t.list_our_vouchers(COMPANY) == ()


def test_purpose_is_asked_before_funding_and_each_answer_lands_on_its_own_leg():
    """Both questions, in the order a person can answer them, each on its own leg.

    Every answer used to be written to `debit_account`, because the funding leg
    was never asked about. Routing is by problem id, so the funding answer can
    never overwrite the expense account the person just chose.
    """
    t, memory = books(paid("Sharma Traders", "Purchases", "Cash", n=5))
    chart = t.read_accounts(COMPANY)
    history = t.read_vouchers(COMPANY)
    d = draft_for(t, memory, "paid Gupta Hardware 1500 for tools")

    first = pipeline.next_question(d)
    assert first is not None and first.problem_id == "which_account"

    d = pipeline.answer(d, "Purchases", problem_id="which_account")
    memory.record_correction(d.voucher.party, "Purchases")
    d = pipeline.evaluate(
        d, chart, history, memory, period_open=True, pdf_repaired=None
    )

    second = pipeline.next_question(d)
    assert second is not None and second.problem_id == pipeline.FUNDING_PROBLEM

    d = pipeline.answer(d, "Bank", problem_id=second.problem_id)
    d = pipeline.evaluate(
        d, chart, history, memory, period_open=True, pdf_repaired=None
    )

    assert d.outcome is Outcome.VALID
    assert d.voucher.debit_account == "Purchases", "the funding answer left it alone"
    assert d.voucher.credit_account == "Bank"
    prov = d.voucher.provenance or {}
    assert prov["debit_account"] == "human_answer"
    assert prov["credit_account"] == "human_answer"


def test_a_human_answer_is_never_overwritten_by_the_history_afterwards():
    """`evaluate` fills an EMPTY leg only. The person outranks the pattern.

    This vendor's history is unanimously Cash, so a re-evaluation that did not
    check for emptiness would quietly replace the Bank the person just chose.
    """
    t, memory = books(paid("Sharma Traders", "Purchases", "Cash", n=40))
    chart = t.read_accounts(COMPANY)
    history = t.read_vouchers(COMPANY)
    d = draft_for(t, memory, "paid Sharma Traders 4200 for cement")
    assert d.voucher.credit_account == "Cash"

    d = pipeline.answer(d, "Bank", problem_id=pipeline.FUNDING_PROBLEM)
    d = pipeline.evaluate(
        d, chart, history, memory, period_open=None, pdf_repaired=None
    )

    assert d.voucher.credit_account == "Bank"
    assert (d.voucher.provenance or {})["credit_account"] == "human_answer"


# ---- the checks that own the two absences -----------------------------------


def test_two_absent_legs_are_two_questions_not_one_sameness_failure():
    """`accounts_differ` compares ledgers. Two blanks are not the same ledger.

    Measured before the fix: an unknown vendor produced
    `accounts_differ` failing with the detail "both sides are " — a refusal
    naming no account, ahead of the two questions actually owed.
    """
    blank = Voucher(
        id="x",
        date=TODAY,
        party="Gupta Hardware",
        narration="tools",
        debit_account="",
        credit_account="",
        amount_paise=150_000,
    )
    same = replace(blank, debit_account="Cash", credit_account="Cash")

    assert checks.accounts_differ(blank, CHART).passed
    assert not checks.accounts_differ(same, CHART).passed
    assert checks.accounts_differ(same, CHART).detail == "both sides are Cash"


def test_the_build_step_records_the_funding_leg_as_not_found_rather_than_blank():
    """A field with no source is a hallucination by the project's own definition.

    `build_draft` no longer proposes this leg at all, so it says so, in the same
    `provenance` map every other field uses.
    """
    _, memory = books([])
    d = pipeline.build_draft(
        COMPANY,
        b"paid Gupta Hardware 1500 for tools",
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    assert d.voucher.credit_account == ""
    assert (d.voucher.provenance or {})["credit_account"] == NOT_FOUND


# ---- structural: an id that does not match is a question asked forever -------


def test_every_check_derived_question_carries_the_id_of_the_problem_it_resolves():
    """`Problem.id` and `Question.problem_id` are the SAME string, always.

    `decide_problems` filters outstanding problems by `Problem.id`;
    `next_question` filters answered ones by `Question.problem_id`. When those
    two disagree the answer is recorded under a name nothing looks for, the
    problem is never retired, and the person is asked the same question until
    the budget of 5 runs out.

    Found live: `questions.how_paid` was written long ago carrying
    `problem_id="how_paid"`, wired to nothing. Wiring it to the
    `funding_is_named` check re-created the mismatch on its first use. This
    asserts the rule for every check, so the next one cannot repeat it.
    """
    voucher = Voucher(
        id="x",
        date=TODAY,
        party="",
        narration="",
        debit_account="",
        credit_account="",
        amount_paise=0,
        gst_paise=1,
    )
    # Through the PUBLIC surface, and driven off `QUESTION_FOR` rather than off
    # whichever checks this one voucher happens to fail - so a check that is
    # hard to fail on purpose is covered too.
    match = MatchResult(
        status=MatchStatus.MATCH, vendor_key="v", accounts=("Purchases",)
    )
    results = [
        CheckResult(name=name, passed=False, detail="forced for this scan")
        for name in problems.QUESTION_FOR
    ]
    found = problems.find(voucher, results, match, [], CHART)

    assert {p.id for p in found} == set(problems.QUESTION_FOR), (
        "every answerable check must reach a Problem carrying its own name"
    )
    mismatched = [
        (p.id, p.question.problem_id)
        for p in found
        if p.question is not None and p.question.problem_id != p.id
    ]

    assert mismatched == [], (
        "each of these questions is recorded under one name and looked up under "
        f"another, so answering it retires nothing: {mismatched}"
    )

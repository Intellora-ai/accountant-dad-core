"""Plain-language questions, and the two rules that govern them.

  S7               No question may contain a ledger account name.
  Non-overlapping  No two questions about one entry resolve the same problem.
  Budget           5 questions per entry, then hand it over.

Every one of these is enforced here rather than trusted.
"""

from __future__ import annotations

import datetime

import pytest

from accountant import checks, pipeline, problems
from accountant import questions as Q
from accountant.decide import decide_problems
from accountant.detect import detectors
from accountant.extract.adapter import TypedTextExtractor
from accountant.memory.index import MemoryIndex
from accountant.schema import MatchResult, MatchStatus, Outcome, Voucher
from accountant.tallyio.fake import FakeTally

COMPANY = "Demo Co"
ACCOUNTS = (
    "Purchases",
    "Repairs & Maintenance",
    "Printing & Stationery",
    "Rent",
    "Electricity Charges",
    "Cash",
    "Bank",
)
TODAY = datetime.date(2026, 8, 7)


def a_voucher(**kw) -> Voucher:
    base = {
        "id": "d1",
        "date": TODAY,
        "party": "Sharma Traders",
        "narration": "cement",
        "debit_account": "Purchases",
        "credit_account": "Cash",
        "amount_paise": 420000,
        "gst_paise": None,
    }
    base.update(kw)
    return Voucher(**base)


def past(party: str, account: str, amount: int = 380000, n: int = 1) -> list[Voucher]:
    return [
        Voucher(
            id=f"h{i}",
            date=TODAY,
            party=party,
            narration="x",
            debit_account=account,
            credit_account="Cash",
            amount_paise=amount,
        )
        for i in range(n)
    ]


# ---- S7: no ledger names in questions --------------------------------------

ALL_QUESTION_BUILDERS = [
    lambda: Q.which_purpose(ACCOUNTS, "Sharma Traders"),
    lambda: Q.which_purpose_narrowed(ACCOUNTS, "Verma Cement", ("Purchases", "Rent")),
    lambda: Q.is_that_amount_right("Sharma Traders", 200_000_000, 380000),
    lambda: Q.different_from_usual("Sharma Traders", "Purchases", 40),
    lambda: Q.first_time_here("Sharma Traders"),
    lambda: Q.gst_looks_odd(420000, 64068),
    lambda: Q.how_much("Sharma Traders"),
    lambda: Q.who_was_it(),
    lambda: Q.how_paid(ACCOUNTS),
    lambda: Q.tax_bigger_than_total(100000, 500000),
]


@pytest.mark.parametrize("build", ALL_QUESTION_BUILDERS)
def test_no_question_contains_a_ledger_account_name(build):
    """The rule that makes 'plain English' testable instead of a matter of taste."""
    q = build()
    leaked = q.mentions_any(ACCOUNTS)
    assert leaked == [], f"{q.problem_id} leaked {leaked}: {q.text}"


@pytest.mark.parametrize("build", ALL_QUESTION_BUILDERS)
def test_every_question_has_text_and_at_least_one_answer(build):
    q = build()
    assert q.text.strip()
    assert len(q.answers) >= 1


@pytest.mark.parametrize("build", ALL_QUESTION_BUILDERS)
def test_every_answer_label_is_plain_english(build):
    """Answer labels are read by the person too, so the same rule applies.

    "cash" is allowed - it is not jargon. "Purchases" is not.
    """
    jargon = [a for a in ACCOUNTS if Q.is_jargon(a)]
    for a in build().answers:
        assert a.label.casefold() not in {x.casefold() for x in jargon}


def test_jargon_is_decided_by_whether_we_reuse_the_word():
    assert Q.is_jargon("Purchases") is True  # we say "stuff you'll sell on"
    assert Q.is_jargon("Sundry Expenses") is True  # we say "a small one-off thing"
    assert Q.is_jargon("Cash") is False  # we say "cash"
    assert Q.is_jargon("Rent") is False  # we say "rent for a place"
    assert Q.is_jargon("Suspense A/c 4412") is True  # no words at all


def test_a_question_that_leaks_an_account_name_is_caught():
    """Proof the guard actually detects a leak, not just that none exist."""
    bad = Q.Question(
        problem_id="x",
        text="Is this Purchases or Printing & Stationery?",
        answers=(Q.Answer(label="yes", value="y"),),
    )
    assert set(bad.mentions_any(ACCOUNTS)) == {"Purchases", "Printing & Stationery"}


def test_a_word_inside_another_word_is_not_a_leak():
    """'Rent' lives inside 'different'. Whole words only."""
    ok = Q.Question(
        problem_id="x",
        text="Is this one different?",
        answers=(Q.Answer(label="yes", value="y"),),
    )
    assert ok.mentions_any(ACCOUNTS) == []


def test_purpose_answers_hide_the_account_behind_plain_words():
    ans = Q.purpose_answers(ACCOUNTS)
    labels = {a.label for a in ans}
    assert "stuff you'll sell on" in labels
    assert "Purchases" not in labels
    # the account name is still carried, just not shown
    assert "Purchases" in {a.value for a in ans}


def test_there_is_always_an_escape_so_nobody_gets_stuck():
    assert any(a.value == Q.HANDOVER for a in Q.purpose_answers(ACCOUNTS))


def test_unmapped_accounts_are_reported_not_shown_as_jargon():
    """#9.7 - an account we have no words for is listed, never shown raw."""
    weird = ("Purchases", "Suspense A/c 4412")
    assert Q.unmapped(weird) == ["Suspense A/c 4412"]
    assert "Suspense A/c 4412" not in {a.value for a in Q.purpose_answers(weird)}


# ---- answerable vs not ------------------------------------------------------


def test_a_huge_amount_asks_rather_than_refuses():
    """₹2 crore is surprising, not wrong."""
    hist = past("Sharma Traders", "Purchases", n=40)
    index = MemoryIndex.from_vouchers(tuple(hist))
    v = a_voucher(amount_paise=200_000_000)
    flags, _ = detectors.run(v, tuple(hist), index, detectors=(detectors.magnitude,))
    probs = problems.find(
        v,
        checks.run(v, ACCOUNTS),
        index.lookup(v.party),
        flags,
        ACCOUNTS,
        tuple(hist),
        index,
    )
    assert [p.id for p in probs] == ["magnitude"]
    assert probs[0].answerable is True
    assert decide_problems(probs).outcome is Outcome.UNCLEAR


def test_the_huge_amount_question_shows_both_numbers():
    q = Q.is_that_amount_right("Sharma Traders", 200_000_000, 380000)
    assert "₹2,000,000" in q.text
    assert "₹3,800" in q.text


def test_only_an_internal_type_error_refuses():
    v = a_voucher()
    bad = checks.CheckResult(
        name="amount_is_integer_paise", passed=False, detail="amount is float"
    )
    probs = problems.find(
        v,
        [bad],
        MatchResult(MatchStatus.MATCH, "sharma_traders", ("Purchases",)),
        [],
        ACCOUNTS,
    )
    assert probs[0].answerable is False
    assert decide_problems(probs).outcome is Outcome.NOT_VALID


def test_an_answerable_problem_must_carry_a_question():
    with pytest.raises(ValueError):
        problems.Problem(id="x", answerable=True, detail="d", question=None)


def test_an_unanswerable_problem_must_not_carry_a_question():
    with pytest.raises(ValueError):
        problems.Problem(
            id="x",
            answerable=False,
            detail="d",
            question=Q.who_was_it(),
        )


# ---- non-overlapping --------------------------------------------------------


def test_problem_ids_are_unique_within_one_entry():
    """Two sources reporting the same problem produce one question, not two."""
    v = a_voucher(debit_account="Nope", party="")
    probs = problems.find(
        v,
        checks.run(v, ACCOUNTS),
        MatchResult(MatchStatus.NO_MATCH, "unknown"),
        [],
        ACCOUNTS,
    )
    ids = [p.id for p in probs]
    assert len(ids) == len(set(ids))


def test_a_problem_already_answered_is_never_asked_again():
    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    index = MemoryIndex()
    d = pipeline.build_draft(
        COMPANY,
        b"paid Verma Cement 900 bags",
        "text/plain",
        TypedTextExtractor(),
        ACCOUNTS,
        index,
        today=TODAY,
    )
    d = pipeline.evaluate(d, ACCOUNTS, (), index)
    first = pipeline.next_question(d)
    assert first is not None

    d = pipeline.answer(d, "Purchases", problem_id=first.problem_id)
    index.record("Verma Cement", "Purchases")
    d = pipeline.evaluate(d, ACCOUNTS, (), index)

    again = pipeline.next_question(d)
    assert again is None or again.problem_id != first.problem_id


# ---- the budget -------------------------------------------------------------


def test_the_cap_is_five():
    assert Q.QUESTION_CAP == 5


def test_after_five_questions_the_entry_is_handed_over():
    probs = [
        problems.Problem(
            id=f"p{i}",
            answerable=True,
            detail=f"thing {i}",
            question=Q.who_was_it(),
        )
        for i in range(3)
    ]
    assert decide_problems(probs, asked=4).outcome is Outcome.UNCLEAR
    handed = decide_problems(probs, asked=5)
    assert handed.outcome is Outcome.NOT_VALID
    assert "saved for you to finish" in handed.reason


def test_a_handed_over_entry_is_never_posted():
    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    index = MemoryIndex()
    d = pipeline.build_draft(
        COMPANY,
        b"paid Verma Cement 900 bags",
        "text/plain",
        TypedTextExtractor(),
        ACCOUNTS,
        index,
        today=TODAY,
    )
    d.answers = [(f"p{i}", "x") for i in range(5)]
    d = pipeline.evaluate(d, ACCOUNTS, (), index)
    assert d.decision.outcome is not Outcome.VALID
    with pytest.raises(ValueError):
        pipeline.post(d, t)

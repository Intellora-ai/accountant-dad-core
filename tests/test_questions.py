"""Plain-language questions, and the two rules that govern them.

  S7               No question may contain a ledger account name.
  Non-overlapping  No two questions about one entry resolve the same problem.
  Budget           5 questions per entry, then hand it over.

Every one of these is enforced here rather than trusted.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from accountant import checks, pipeline, problems
from accountant import questions as Q
from accountant.decide import decide_problems
from accountant.detect import detectors
from accountant.extract.adapter import TypedTextExtractor
from accountant.memory.bootstrap import bootstrap
from accountant.memory.company import CompanyMemory
from accountant.memory.index import MemoryIndex
from accountant.memory.store import MemoryStore
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

# `past` means before today. Dated explicitly since 2026-08-10, when
# `magnitude` stopped counting rows that are not prior to an entry into that
# entry's ceiling. The fixture's name always said "past"; now the date does too.
EARLIER = TODAY - datetime.timedelta(days=1)


_BASE = Voucher(
    id="d1",
    date=TODAY,
    party="Sharma Traders",
    narration="cement",
    debit_account="Purchases",
    credit_account="Cash",
    amount_paise=420000,
    gst_paise=None,
)


def a_voucher(**kw: Any) -> Voucher:
    """The default voucher, with any field overridden."""
    return replace(_BASE, **kw)


def past(party: str, account: str, amount: int = 380000, n: int = 1) -> list[Voucher]:
    return [
        Voucher(
            id=f"h{i}",
            date=EARLIER,
            party=party,
            narration="x",
            debit_account=account,
            credit_account="Cash",
            amount_paise=amount,
        )
        for i in range(n)
    ]


def a_company(history: tuple[Voucher, ...] = ()) -> tuple[FakeTally, CompanyMemory]:
    """A company in Tally, and the memory read out of it.

    The pipeline takes company-scoped memory and nothing else, so a test that
    drives the pipeline has to bootstrap one the same way the app does.
    """
    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, vouchers=history, backed_up=True)
    return t, bootstrap(t, COMPANY, MemoryStore(":memory:"))


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
def test_no_question_contains_a_ledger_account_name(build: Callable[[], Q.Question]):
    """The rule that makes 'plain English' testable instead of a matter of taste."""
    q = build()
    leaked = q.mentions_any(ACCOUNTS)
    assert leaked == [], f"{q.problem_id} leaked {leaked}: {q.text}"


@pytest.mark.parametrize("build", ALL_QUESTION_BUILDERS)
def test_every_question_has_text_and_at_least_one_answer(
    build: Callable[[], Q.Question],
):
    q = build()
    assert q.text.strip()
    assert len(q.answers) >= 1


@pytest.mark.parametrize("build", ALL_QUESTION_BUILDERS)
def test_every_answer_label_is_plain_english(build: Callable[[], Q.Question]):
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


UNNAMED = "Suspense A/c 4412"
ALSO_UNNAMED = "Provision for Doubtful Debts"


def escape_label(answers: tuple[Q.Answer, ...]) -> str:
    """The words on the "none of these" button.

    `web/app.py:1415` renders `Question.text` and `web/app.py:1409` renders
    `Answer.label`. Those two strings are the whole of what reaches the person,
    so a report that lands anywhere else is not a report - it is a field with a
    test for a reader, which is the defect this file is fixing.
    """
    return next(a.label for a in answers if a.value == Q.HANDOVER)


def test_unmapped_accounts_are_reported_not_shown_as_jargon():
    """#9.7 - an account we have no words for is listed, never shown raw."""
    weird = ("Purchases", UNNAMED)
    assert Q.unmapped(weird) == [UNNAMED]
    assert UNNAMED not in {a.value for a in Q.purpose_answers(weird)}


def test_an_account_with_no_words_is_counted_where_the_person_can_see_it():
    """The defect: these were dropped from the options and nothing said so.

    Not offering the account is right - the label would have to be invented.
    Saying nothing about it is not: the person is never told a correct answer is
    missing, so an unreachable answer looks exactly like a complete list.

    Same shape as the G6.2 overflow count in `web/app.py:1392` - the thing we
    cannot show is reported as a number, never silently dropped.
    """
    weird = ("Purchases", UNNAMED, ALSO_UNNAMED)
    label = escape_label(Q.purpose_answers(weird))
    assert "2" in label, f"2 accounts vanished without a word: {label!r}"


@pytest.mark.parametrize("n", [1, 2, 5])
def test_the_number_reported_is_the_real_count(n: int):
    """A fixed word like "some", or a hard-coded 1, is not a measurement."""
    weird = ("Purchases", *(f"Suspense A/c 44{i}" for i in range(n)))
    assert str(n) in escape_label(Q.purpose_answers(weird))


def test_a_chart_we_have_words_for_says_nothing_extra():
    """Nothing at zero. "0 things I have no words for" is noise on every entry."""
    assert escape_label(Q.purpose_answers(ACCOUNTS)) == "something else"


def test_reporting_an_unmapped_account_leaks_no_account_name():
    """The hard part: report it without naming it.

    Checked in BOTH channels the person reads, with the same whole-word,
    jargon-only rule S7 already applies to question text.
    """
    weird = (*ACCOUNTS, UNNAMED)
    q = Q.which_purpose(weird, "Sharma Traders")
    assert q.mentions_any(weird) == []
    probe = Q.Question(
        problem_id="labels",
        text=" ".join(a.label for a in q.answers),
        answers=q.answers,
    )
    assert probe.mentions_any(weird) == []


def test_the_label_probe_would_catch_a_leaked_name():
    """Negative control for the line above - the probe really does detect one."""
    leaky = (Q.Answer(label=UNNAMED, value=Q.HANDOVER),)
    probe = Q.Question(
        problem_id="labels",
        text=" ".join(a.label for a in leaky),
        answers=leaky,
    )
    assert probe.mentions_any((*ACCOUNTS, UNNAMED)) == [UNNAMED]


def test_a_vendors_own_account_with_no_words_is_counted_too():
    """`which_purpose_narrowed` dropped these silently as well."""
    q = Q.which_purpose_narrowed(ACCOUNTS, "Verma Cement", ("Purchases", UNNAMED))
    assert "1" in escape_label(q.answers)


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
    """GROUPING AND PAISE CORRECTED 2026-08-13, owner decision.

    This pinned `₹2,000,000` - Python's grouping in threes, and the paise
    floored away. Both were wrong for the only market this product has: ₹20
    lakh is written `₹20,00,000.00`, and a question that drops the paise asks
    a person to confirm a number the screen never showed them.
    """
    q = Q.is_that_amount_right("Sharma Traders", 200_000_000, 380000)
    assert "₹20,00,000.00" in q.text
    assert "₹3,800.00" in q.text


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
    _, memory = a_company()
    d = pipeline.build_draft(
        COMPANY,
        b"paid Verma Cement 900 bags",
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    d = pipeline.evaluate(d, ACCOUNTS, (), memory)
    first = pipeline.next_question(d)
    assert first is not None

    d = pipeline.answer(d, "Purchases", problem_id=first.problem_id)
    memory.record_correction("Verma Cement", "Purchases")
    d = pipeline.evaluate(d, ACCOUNTS, (), memory)

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
    t, memory = a_company()
    d = pipeline.build_draft(
        COMPANY,
        b"paid Verma Cement 900 bags",
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    d.answers = [(f"p{i}", "x") for i in range(5)]
    d = pipeline.evaluate(d, ACCOUNTS, (), memory)
    assert d.outcome is not Outcome.VALID
    with pytest.raises(ValueError):
        pipeline.post(d, t)

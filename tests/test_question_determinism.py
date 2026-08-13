"""The same bill must ask the same question. Every time, in every process.

THE GAP THIS CLOSES, MEASURED 2026-08-13
----------------------------------------
An AST scan of all 110 `tests/test_*.py` files found no test that evaluates one
bill twice and compares the resulting `Question` — not its text, not its
`problem_id`, not its offered answers. The two nearest things were
`test_confidence.py::test_the_same_input_scores_the_same_twice`, which pins
confidence ARITHMETIC, and
`test_corpus_decisions.py::test_the_whole_corpus_decides_identically_when_built_and_run_twice`,
which pins decision OUTCOMES. Neither touches a single word a person reads.

WHY IT MATTERS MORE THAN AN OUTCOME DOES
----------------------------------------
A question is the only part of this system a person actually answers. If one
bill can produce two different questions across runs — different wording,
different options, a different problem chosen when several apply — then somebody
answering the same bill on Monday and on Tuesday is answering two different
things, and an answer filed against `problem_id` A on Monday means something
else on Tuesday.

WHERE THE QUESTION COMES FROM, AND WHERE IT COULD GO WRONG
----------------------------------------------------------
`pipeline.next_question` returns the FIRST answerable, unanswered problem's
question, in `draft.problems` order. So the wording is owned by
`accountant/questions.py` and the CHOICE is owned entirely by the order
`problems.find` builds its list in — checks, then flags, then the memory match,
then `funding_is_named` last. Three of those four are fed by containers, and a
set or a dict iteration anywhere upstream would pick the question by hash order.

Python randomises string hashing per process, so a set-ordering bug of exactly
that kind is INVISIBLE to any same-process test. That is why the last two tests
here leave the process, and why the canary beside them fails the suite if the
seeds turn out not to have differed — a cross-process check that cannot detect a
difference is not a check.
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from accountant import pipeline
from accountant import questions as Q
from accountant.detect import detectors
from accountant.extract.adapter import TypedTextExtractor
from accountant.memory.bootstrap import bootstrap
from accountant.memory.company import CompanyMemory
from accountant.memory.store import MemoryStore
from accountant.schema import Voucher
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
EARLIER = TODAY - datetime.timedelta(days=1)


def past(
    party: str,
    account: str,
    *,
    amount: int = 380000,
    n: int = 1,
    credit: str = "Cash",
    start: int = 0,
) -> tuple[Voucher, ...]:
    return tuple(
        Voucher(
            id=f"h{start + i}",
            date=EARLIER,
            party=party,
            narration="x",
            debit_account=account,
            credit_account=credit,
            amount_paise=amount,
        )
        for i in range(n)
    )


# One vendor, posted to three accounts and paid two ways. Conflicted memory, so
# the vendor question offers a real list rather than a single option — an
# ordering defect in the ANSWERS has somewhere to show itself.
MIXED = (
    *past("Sharma Traders", "Purchases", n=3),
    *past("Sharma Traders", "Rent", n=2, start=10, credit="Bank"),
    *past("Sharma Traders", "Electricity Charges", n=1, start=20),
)


def a_company(history: tuple[Voucher, ...]) -> CompanyMemory:
    """A company in Tally and the memory read out of it, built from scratch.

    Rebuilt per call on purpose. Reusing one memory would test that a cached
    answer is stable, which is not the claim.
    """
    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, vouchers=history, backed_up=True)
    return bootstrap(t, COMPANY, MemoryStore(":memory:"))


def ask(bill: bytes, history: tuple[Voucher, ...] = MIXED) -> Q.Question | None:
    """The one question this bill puts to a person, end to end from bytes."""
    memory = a_company(history)
    draft = pipeline.build_draft(
        COMPANY, bill, "text/plain", TypedTextExtractor(), memory, today=TODAY
    )
    return pipeline.next_question(pipeline.evaluate(draft, ACCOUNTS, history, memory))


def a_tangle() -> pipeline.Draft:
    """One bill with FIVE problems at once, from three different sources.

    Measured, in `draft.problems` order:

        tax_lines_can_be_posted   check   unanswerable, so it must be SKIPPED
        gst_anomaly               flag    severity 3
        magnitude                 flag    severity 2
        which_account             memory  the vendor is CONFLICTED
        funding_is_named          check   deliberately asked last

    This is the case the owner was right to be worried about: four answerable
    problems competing, and the winner decided purely by list position.
    """
    memory = a_company(MIXED)
    draft = pipeline.build_draft(
        COMPANY,
        b"paid Sharma Traders 200000",
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    # Set on the draft rather than extracted, because the typed-text reader
    # pulls no tax out of a line of prose and the tangle needs the tax problems.
    draft.voucher = replace(draft.voucher, debit_account="Purchases", gst_paise=36000)
    return pipeline.evaluate(
        draft, ACCOUNTS, MIXED, memory, detector_set=detectors.ALL_DETECTORS
    )


def canonical(q: Q.Question | None) -> str:
    """Everything a person reads, on one line, so two processes can be compared."""
    if q is None:
        return "no question"
    offered = " | ".join(f"{a.label}={a.value}" for a in q.answers)
    return f"{q.problem_id} :: {q.text} :: {offered}"


def the_question_asked() -> str:
    """The tangle's question, rendered. Called by this file's own subprocesses."""
    return canonical(pipeline.next_question(a_tangle()))


# ---- 1. the same bill, twice -------------------------------------------------


def test_the_same_bill_asks_an_identical_question_twice() -> None:
    """Same bytes, same books, two runs from scratch. One question."""
    first = ask(b"paid Sharma Traders 4200")
    second = ask(b"paid Sharma Traders 4200")
    assert first is not None, "the fixture must actually produce a question"
    assert second is not None
    assert first.problem_id == second.problem_id
    assert first.text == second.text
    assert first.answers == second.answers, (
        "the offered answers differ, in content or in order:\n"
        f"  first  {[a.label for a in first.answers]}\n"
        f"  second {[a.label for a in second.answers]}"
    )


def test_re_evaluating_one_draft_does_not_change_what_it_asks() -> None:
    """`answer` clears the decision and the caller re-evaluates. Same question.

    The path a person actually walks re-runs `evaluate` on a draft that has
    already been evaluated once, so idempotence here is not a nicety.
    """
    memory = a_company(MIXED)
    draft = pipeline.build_draft(
        COMPANY,
        b"paid Sharma Traders 4200",
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    once = pipeline.next_question(pipeline.evaluate(draft, ACCOUNTS, MIXED, memory))
    twice = pipeline.next_question(pipeline.evaluate(draft, ACCOUNTS, MIXED, memory))
    assert once is not None
    assert canonical(once) == canonical(twice)


# ---- 2. THE CONTROL: a different bill asks a different question --------------


def test_a_genuinely_different_bill_asks_a_different_question() -> None:
    """Without this, a `next_question` returning one constant passes every test
    above. It is the falsifier for the whole file."""
    same = ask(b"paid Sharma Traders 4200")
    other = ask(b"paid Gupta Steel 4200")
    assert same is not None
    assert other is not None
    assert canonical(same) != canonical(other), (
        f"two different bills produced one question: {canonical(same)!r}"
    )


# ---- 3. several problems at once: the same one is chosen every time ----------


def test_when_several_problems_apply_the_same_one_is_chosen_every_time() -> None:
    """Five problems competing, five independent runs, one winner."""
    runs = [a_tangle() for _ in range(5)]
    orders = {tuple(p.id for p in d.problems) for d in runs}
    assert len(orders) == 1, (
        f"the problem list came out in {len(orders)} orders: {orders}"
    )
    assert len(next(iter(orders))) >= 3, (
        f"the fixture no longer produces several problems: {orders}"
    )
    asked = {canonical(pipeline.next_question(d)) for d in runs}
    assert len(asked) == 1, f"one bill, {len(asked)} different questions: {asked}"


def test_the_unanswerable_problem_is_skipped_and_the_first_answerable_wins() -> None:
    """Names the winner, so a silent change of chosen problem cannot pass.

    The list leads with an unanswerable problem, which proves the choice is not
    simply `problems[0]`.
    """
    d = a_tangle()
    ids = [p.id for p in d.problems]
    assert ids[0] == "tax_lines_can_be_posted"
    assert d.problems[0].answerable is False
    asked = pipeline.next_question(d)
    assert asked is not None
    assert asked.problem_id == "gst_anomaly"
    assert ids[-1] == "funding_is_named", "the funding question is asked last"


def test_the_choice_really_does_depend_on_the_order_of_the_list() -> None:
    """CONTROL for the two above. If `next_question` ignored order, they would
    pass on a list that came out shuffled — which is the bug they exist to catch.
    """
    d = a_tangle()
    straight = pipeline.next_question(d)
    d.problems = list(reversed(d.problems))
    reversed_pick = pipeline.next_question(d)
    assert straight is not None
    assert reversed_pick is not None
    assert reversed_pick.problem_id != straight.problem_id, (
        "reversing the problem list changed nothing, so asserting its order "
        "proves nothing about which question a person is asked"
    )


# ---- 4. across processes, where hash order is randomised --------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
SEEDS = ("0", "1", "12345", "99999")

_ASK = "from tests.test_question_determinism import the_question_asked as f; print(f())"
# A bare set of strings. Its iteration order is decided by the hash seed and by
# nothing else, so it says whether the seeds below actually took effect.
_CANARY = "print(list({'alpha','beta','gamma','delta','epsilon','zeta'}))"


def under_seed(program: str, seed: str) -> str:
    out = subprocess.run(  # noqa: S603 - this interpreter, this file's own source
        [sys.executable, "-c", program],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONHASHSEED": seed},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert out.returncode == 0, f"seed {seed} failed:\n{out.stderr}"
    return out.stdout.strip()


def test_the_hash_seed_is_really_different_in_those_processes() -> None:
    """CONTROL, and it runs first. Four processes that all agree prove nothing
    if they were all the same process in disguise."""
    seen = {under_seed(_CANARY, s) for s in SEEDS}
    assert len(seen) > 1, (
        f"PYTHONHASHSEED had no effect across {SEEDS}; the cross-process test "
        f"below is measuring nothing"
    )


def test_the_same_bill_asks_the_same_question_under_any_hash_seed() -> None:
    """The one test a set-ordering defect cannot hide from."""
    answers = {s: under_seed(_ASK, s) for s in SEEDS}
    distinct = set(answers.values())
    assert len(distinct) == 1, f"one bill, {len(distinct)} questions: {answers}"
    assert next(iter(distinct)).startswith("gst_anomaly :: "), (
        f"the subprocess did not reach a real question: {distinct}"
    )

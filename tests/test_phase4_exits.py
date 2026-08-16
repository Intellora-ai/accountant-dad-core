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

import ast
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
        memory,
        today=TODAY,
    )
    draft = pipeline.evaluate(
        draft,
        ACCOUNTS,
        t.read_vouchers(COMPANY),
        memory,
        period_open=None,
        pdf_repaired=None,
    )
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
    again = pipeline.evaluate(
        answered,
        ACCOUNTS,
        t.read_vouchers(COMPANY),
        memory,
        period_open=True,
        pdf_repaired=None,
    )

    assert again.decision is not None, "evaluate restores a live decision"

    # An UNKNOWN vendor genuinely has two unknowns, and since 2026-08-09 the
    # second one is asked instead of guessed: this company has never paid Gupta
    # Hardware, so nothing in its history says HOW. `_default_credit` used to
    # answer that with "Cash" and no provenance.
    assert again.outcome is Outcome.UNCLEAR
    second = pipeline.next_question(again)
    assert second is not None and second.problem_id == pipeline.FUNDING_PROBLEM

    paid = pipeline.answer(again, "Cash", problem_id=second.problem_id)
    done = pipeline.evaluate(
        paid,
        ACCOUNTS,
        t.read_vouchers(COMPANY),
        memory,
        period_open=True,
        pdf_repaired=None,
    )

    assert done.outcome is Outcome.VALID
    assert done.voucher.debit_account == "Purchases"
    assert done.voucher.credit_account == "Cash"
    # Both legs now say where they came from. Neither was invented.
    assert (done.voucher.provenance or {})["debit_account"] == "human_answer"
    assert (done.voucher.provenance or {})["credit_account"] == "human_answer"


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
    again = pipeline.evaluate(
        answered,
        ACCOUNTS,
        t.read_vouchers(COMPANY),
        memory,
        period_open=None,
        pdf_repaired=None,
    )

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
        draft = pipeline.evaluate(
            draft,
            ACCOUNTS,
            t.read_vouchers(COMPANY),
            memory,
            period_open=None,
            pdf_repaired=None,
        )


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
    again = pipeline.evaluate(
        answered,
        ACCOUNTS,
        t.read_vouchers(COMPANY),
        memory,
        period_open=None,
        pdf_repaired=None,
    )

    assert again.outcome is not Outcome.VALID
    with pytest.raises(ValueError, match="refusing to post"):
        pipeline.post(again, t)
    assert t.list_our_vouchers(COMPANY) == ()
    assert t.trial_balance(COMPANY) == before


# ---------------------------------------------------------------------------
# Exit 4 — no fallback account exists anywhere in executable code
# ---------------------------------------------------------------------------
#
# This guard did not exist, and the exit was already FALSE. `_default_credit`
# picked the money-source ledger on EVERY entry from a hard-coded preference
# list, and its last line returned the literal `"Cash"` even when the company's
# chart had no such ledger.
#
# It stayed invisible because every test chart in the repo contains "Cash", so
# the first loop iteration always matched and the later branches never ran.
#
# The guard reads SOURCE, not behaviour, because behaviour tests can only find
# a fallback somebody thought to trigger.

# Names that mean "an account we picked because nothing told us which one".
FALLBACK_NAMES = ("suspense", "sundry expenses", "miscellaneous", "misc")

# `questions.py` is the plain-English phrasebook: it maps ledger names to words
# a person understands, so it MUST contain account names as data. It is exempt
# from the name scan, and `test_the_phrasebook_exemption_is_not_a_loophole`
# proves the exemption cannot hide executable logic.
PHRASEBOOK = PACKAGE / "questions.py"

# Read-only measurement and synthetic-data code. Neither can reach a real write.
NOT_A_POSTING_PATH = ("ingest", "generate", "taxonomy", "score")


def _shipped_modules() -> list[pathlib.Path]:
    return [
        p
        for p in sorted(PACKAGE.rglob("*.py"))
        if not any(part in NOT_A_POSTING_PATH for part in p.relative_to(PACKAGE).parts)
    ]


LEDGER_LEGS = ("credit_account", "debit_account")


def _chooser_names(tree: ast.AST) -> set[str]:
    """Functions that can return a non-empty string LITERAL.

    Walks the whole return expression, not just a bare constant, because
    `_default_credit` hid its fallback in `accounts[0] if accounts else "Cash"`
    - an IfExp. A fallback lives in the conditional; that is what makes it one.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Return) or inner.value is None:
                continue
            for part in ast.walk(inner.value):
                if (
                    isinstance(part, ast.Constant)
                    and isinstance(part.value, str)
                    and part.value.strip()
                ):
                    names.add(node.name)
                    break
    return names


def _invented_ledger_legs(tree: ast.AST, choosers: set[str]) -> list[tuple[str, int]]:
    """Every place a ledger leg is set from something we made up.

    NOT "any function that returns a string" - that flagged `accounts_differ`,
    `build_ledger_list_request` and three others, none of which chooses an
    account. The invariant is narrower and exact: `credit_account` and
    `debit_account` may come from the document, from this company's own memory,
    or from a person's answer. They may never come from a literal we wrote or
    from a function that can produce one.
    """
    bad: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg not in LEDGER_LEGS:
                continue
            value = kw.value
            if (
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and value.value.strip()
            ):
                bad.append((f"{kw.arg}={value.value!r}", kw.value.lineno))
            elif (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id in choosers
            ):
                bad.append((f"{kw.arg}={value.func.id}()", value.lineno))
    return bad


def test_no_ledger_leg_is_ever_set_from_a_literal_or_a_chooser() -> None:
    """Exit 4, stated exactly.

    A ledger leg may come from the document, from this company's own memory, or
    from a person's answer. Never from a name we wrote down, and never from a
    function that can produce one.

    `_default_credit` violated this on EVERY entry, and its last line returned
    the literal "Cash" even when the chart had no such ledger. It stayed
    invisible because every test chart in the repo contains "Cash", so the
    first loop iteration always matched and the later branches never ran.

    DELETED 2026-08-09. The funding leg is now read from this company's own
    history, unanimous or nothing, and when the history is silent or split it
    is an absence - which `checks.funding_is_named` turns into a question.
    This guard went from a strict xfail to a passing assertion.
    """
    offenders: list[str] = []
    for path in _shipped_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for what, line in _invented_ledger_legs(tree, _chooser_names(tree)):
            offenders.append(f"{path.relative_to(REPO)}:{line} {what}")
    assert not offenders, (
        "a ledger leg is set from an invented account: "
        + "; ".join(offenders)
        + ". No fallback account may exist (Phase 4 exit 4)."
    )


def test_no_shipped_module_names_a_fallback_account_in_executable_code() -> None:
    """Suspense, Sundry Expenses, Miscellaneous — the classic buckets.

    The phrasebook is exempt because describing a ledger in plain words is its
    job. Everything else that names one is choosing it.
    """
    offenders: list[str] = []
    for path in _shipped_modules():
        if path == PHRASEBOOK:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                low = node.value.lower()
                for bad in FALLBACK_NAMES:
                    if low == bad:
                        offenders.append(
                            f"{path.relative_to(REPO)}:{node.lineno} {node.value!r}"
                        )
    assert not offenders, (
        "a fallback account name appears in executable code: " + "; ".join(offenders)
    )


def test_the_phrasebook_exemption_is_not_a_loophole() -> None:
    """`questions.py` may hold account NAMES. It may not choose an account.

    Without this, exempting the phrasebook would be a hole big enough to hide a
    fallback in.
    """
    tree = ast.parse(PHRASEBOOK.read_text(encoding="utf-8"), filename=str(PHRASEBOOK))
    offenders = [
        f"line {line} {what}"
        for what, line in _invented_ledger_legs(tree, _chooser_names(tree))
    ]
    assert not offenders, (
        f"{PHRASEBOOK.name} is exempt from the NAME scan only. It must never "
        f"set a ledger leg: {'; '.join(offenders)}"
    )


def test_the_guard_catches_a_fallback_that_is_deliberately_introduced() -> None:
    """An absence test nobody has seen fail is indistinguishable from one that
    cannot fail. This runs the forbidden shapes through the real detectors."""
    # The exact shape `_default_credit` had, wired the exact way it was wired.
    # A direct-constant check walks straight past the IfExp, so this is pinned.
    sneaky = ast.parse(
        "def _default_credit(accounts):\n"
        '    return accounts[0] if accounts else "Cash"\n'
        "def build(accounts):\n"
        "    return Voucher(credit_account=_default_credit(accounts))\n"
    )
    assert _invented_ledger_legs(sneaky, _chooser_names(sneaky)), (
        "a fallback inside a conditional expression slips past the scan"
    )

    # And the blunt version.
    blunt = ast.parse('v = Voucher(credit_account="Suspense")\n')
    assert _invented_ledger_legs(blunt, set()), "a literal ledger leg slips past"

    # The control on the control: honest shapes must NOT be flagged.
    fine = ast.parse(
        "v = Voucher(credit_account=record.credit, debit_account=answer)\n"
        'empty = Voucher(credit_account="", debit_account="")\n'
    )
    assert not _invented_ledger_legs(fine, set()), (
        "the scan flags a leg taken from input or left empty"
    )

    named = ast.parse('FALLBACK = "Suspense"\n')
    hits = [
        n.value
        for n in ast.walk(named)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and n.value.lower() in FALLBACK_NAMES
    ]
    assert hits == ["Suspense"], "the fallback-name scan is blind"

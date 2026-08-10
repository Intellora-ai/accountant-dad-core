"""Problems, and whether a person could answer them away.

The boundary that decides refuse-vs-ask:

    Could some answer a person gives make this entry valid?
        yes -> answerable -> ASK
        no  -> not answerable -> REFUSE

Almost everything is answerable. A ₹2 crore payment is surprising, not wrong.
An account not in the chart just needs a different pick. Only things no human
answer can fix are refusals — in practice, internal type errors.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from accountant import questions as Q
from accountant.memory.index import MemoryIndex
from accountant.schema import CheckResult, Flag, MatchResult, MatchStatus, Voucher

# Checks whose failure NO answer can fix. Everything else is a question.
#
# `tax_lines_can_be_posted` joined this set on 2026-08-10. It is listed here
# EXPLICITLY rather than left to the "no question registered" fallback in
# `_from_check`, because that fallback exists for a check nobody has written
# words for yet — an oversight — and this one is unanswerable by design. Nothing
# a person can type makes Accountant Dad able to build a CGST/SGST/IGST line, so
# any question here would spend one of their five on an answer that cannot help.
# The entry is handed over instead, with the tax named in the reason.
UNANSWERABLE_CHECKS = frozenset({"amount_is_integer_paise", "tax_lines_can_be_posted"})


@dataclass(frozen=True)
class Problem:
    id: str
    answerable: bool
    detail: str
    question: Q.Question | None = None

    def __post_init__(self) -> None:
        if self.answerable and self.question is None:
            raise ValueError(f"answerable problem {self.id!r} carries no question")
        if not self.answerable and self.question is not None:
            raise ValueError(
                f"unanswerable problem {self.id!r} must not carry a question"
            )


def _usual_amount(history: Sequence[Voucher], account: str) -> int:
    seen = [v.amount_paise for v in history if v.debit_account == account]
    return max(seen) if seen else 0


def _from_check(c: CheckResult, voucher: Voucher, accounts: Sequence[str]) -> Problem:
    if c.name in UNANSWERABLE_CHECKS:
        return Problem(id=c.name, answerable=False, detail=c.detail)

    q = QUESTION_FOR.get(c.name)
    if q is None:
        # A check we have no words for yet. Refusing silently would hide it, so
        # it becomes a visible unanswerable problem rather than a guess.
        return Problem(id=c.name, answerable=False, detail=c.detail)

    try:
        question = q(voucher, accounts)
    except Q.NoAnswerableOption as exc:
        # Every option would have been invented, so there is nothing honest to
        # ask. Unanswerable is NOT_VALID, and that is the truth: nothing the
        # person could say makes this postable.
        return Problem(id=c.name, answerable=False, detail=str(exc))

    # THE ID IS THE CHECK NAME, always, whatever the question factory called
    # itself. `decide_problems` filters outstanding problems by `Problem.id`
    # and `pipeline.next_question` filters answered ones by
    # `Question.problem_id`; when those disagree the answer is filed under a
    # name nothing looks for, the problem is never retired, and the person is
    # asked the same question until the budget of 5 runs out and the entry is
    # handed over. Measured 2026-08-09, three live mismatches:
    #
    #     amount_is_positive         asked as  amount
    #     party_is_named             asked as  party
    #     gst_not_larger_than_amount asked as  gst_too_big
    #
    # Overwriting it here rather than editing each factory means the next check
    # added cannot reintroduce the bug, and it leaves the factories reusable by
    # `find` below, which owns the "which_account" id in its own right.
    return Problem(
        id=c.name,
        answerable=True,
        detail=c.detail,
        question=replace(question, problem_id=c.name),
    )


# Every check that a person can answer, and the words to ask them in. A check
# absent from here is unanswerable BY CONSTRUCTION, not by oversight.
QUESTION_FOR: dict[str, Callable[[Voucher, Sequence[str]], Q.Question]] = {
    "amount_is_positive": lambda v, _a: Q.how_much(v.party),
    "party_is_named": lambda _v, _a: Q.who_was_it(),
    "accounts_exist": lambda v, a: Q.which_purpose(a, v.party),
    "accounts_differ": lambda v, a: Q.which_purpose(a, v.party),
    # `how_paid` was written long ago and wired to NOTHING. Wiring it is what
    # replaces `_default_credit`: the funding leg becomes a question instead of
    # a guess.
    "funding_is_named": lambda _v, a: Q.how_paid(a),
    "gst_not_larger_than_amount": lambda v, _a: Q.tax_bigger_than_total(
        v.amount_paise, v.gst_paise or 0
    ),
}


def _from_flag(
    f: Flag,
    voucher: Voucher,
    history: Sequence[Voucher],
    index: MemoryIndex | None,
) -> Problem:
    if f.detector == "vendor_switch":
        usual, times = "", 0
        if index is not None:
            seen = index.lookup(voucher.party)
            usual = seen.accounts[0] if seen.accounts else ""
            times = index.times_posted(voucher.party, usual) if usual else 0
        q = Q.different_from_usual(voucher.party, usual, times)
    elif f.detector == "magnitude":
        q = Q.is_that_amount_right(
            voucher.party,
            voucher.amount_paise,
            _usual_amount(history, voucher.debit_account),
        )
    elif f.detector == "first_use":
        q = Q.first_time_here(voucher.party)
    elif f.detector == "gst_anomaly":
        q = Q.gst_looks_odd(voucher.amount_paise, voucher.gst_paise or 0)
    else:
        return Problem(id=f.detector, answerable=False, detail=f.reason)

    return Problem(id=f.detector, answerable=True, detail=f.reason, question=q)


# The one problem deliberately asked last. Defined here rather than imported
# from `pipeline` because `pipeline` imports this module.
FUNDING_PROBLEM = "funding_is_named"


def find(
    voucher: Voucher,
    checks: Sequence[CheckResult],
    match: MatchResult,
    flags: Sequence[Flag],
    accounts: Sequence[str],
    history: Sequence[Voucher] = (),
    index: MemoryIndex | None = None,
) -> list[Problem]:
    """Every distinct thing wrong with this entry, each with a stable id.

    The ids are what make questions non-overlapping: one problem, one question,
    never asked twice.
    """
    out: list[Problem] = []
    seen_ids: set[str] = set()

    def add(p: Problem) -> None:
        if p.id not in seen_ids:
            seen_ids.add(p.id)
            out.append(p)

    # "What was it for?" is asked before "how did you pay?", always.
    #
    # Not cosmetic. Both are questions to the same person about the same
    # entry, and the purpose is the one they can answer from the bill in
    # their hand. Check order would otherwise decide it, because
    # `funding_is_named` is a check and the vendor question is derived from
    # the memory match further down - so the funding question came first by
    # accident of list position.
    funding: Problem | None = None
    for c in checks:
        if not c.passed:
            p = _from_check(c, voucher, accounts)
            if p.id == FUNDING_PROBLEM:
                funding = p
                continue
            add(p)

    for f in flags:
        add(_from_flag(f, voucher, history, index))

    if match.status is MatchStatus.NO_MATCH:
        add(
            Problem(
                id="which_account",
                answerable=True,
                detail=f"{match.vendor_key} has never been posted before",
                question=Q.which_purpose(accounts, voucher.party),
            )
        )
    elif match.status is MatchStatus.CONFLICTED:
        add(
            Problem(
                id="which_account",
                answerable=True,
                detail=(
                    f"{match.vendor_key} has been posted to "
                    f"{len(match.accounts)} different accounts"
                ),
                question=Q.which_purpose_narrowed(
                    accounts, voucher.party, match.accounts
                ),
            )
        )

    if funding is not None:
        add(funding)

    return out

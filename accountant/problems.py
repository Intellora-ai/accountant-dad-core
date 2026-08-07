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

from collections.abc import Sequence
from dataclasses import dataclass

from accountant import questions as Q
from accountant.memory.index import MemoryIndex
from accountant.schema import CheckResult, Flag, MatchResult, MatchStatus, Voucher

# Checks whose failure NO answer can fix. Everything else is a question.
UNANSWERABLE_CHECKS = frozenset({"amount_is_integer_paise"})


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

    q = {
        "amount_is_positive": lambda: Q.how_much(voucher.party),
        "party_is_named": lambda: Q.who_was_it(),
        "accounts_exist": lambda: Q.which_purpose(accounts, voucher.party),
        "accounts_differ": lambda: Q.which_purpose(accounts, voucher.party),
        "gst_not_larger_than_amount": lambda: Q.tax_bigger_than_total(
            voucher.amount_paise, voucher.gst_paise or 0
        ),
    }.get(c.name)

    if q is None:
        # A check we have no words for yet. Refusing silently would hide it, so
        # it becomes a visible unanswerable problem rather than a guess.
        return Problem(id=c.name, answerable=False, detail=c.detail)

    return Problem(id=c.name, answerable=True, detail=c.detail, question=q())


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

    for c in checks:
        if not c.passed:
            add(_from_check(c, voucher, accounts))

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

    return out

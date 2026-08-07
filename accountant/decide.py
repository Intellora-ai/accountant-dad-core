"""The decision order.

From the frozen plan, evaluated in this order, first match wins:

    1. NOT VALID  -> notify, do NOT post
    2. UNCLEAR    -> ask a plain-language question, record the answer,
                     then re-evaluate from step 1
    3. VALID      -> post, then notify

Not-valid still beats unclear: if a problem cannot be answered, asking about a
different one is pointless.

The boundary between the first two moved on 2026-08-07. It is no longer "did a
check fail" but "could a person's answer fix this". A surprising amount, an
unknown account and an unusual vendor switch are all answerable, so all three
now ask instead of refusing. Not-valid is expected to be near zero.

There is no user confirmation gate. Nothing here consults a model or the network.
"""

from __future__ import annotations

from collections.abc import Sequence

from accountant.problems import Problem, find
from accountant.schema import (
    CheckResult,
    Decision,
    Flag,
    MatchResult,
    Outcome,
    Voucher,
)


def decide_problems(problems: Sequence[Problem], asked: int = 0) -> Decision:
    """Apply the decision order to a list of problems.

    `asked` is how many questions have already been put to the person. Once the
    budget is spent the entry is handed over rather than asked about again.
    """
    unanswerable = [p for p in problems if not p.answerable]
    if unanswerable:
        return Decision(
            outcome=Outcome.NOT_VALID,
            reason="; ".join(f"{p.id}: {p.detail}" for p in unanswerable),
        )

    answerable = [p for p in problems if p.answerable]
    if answerable:
        from accountant.questions import QUESTION_CAP

        if asked >= QUESTION_CAP:
            return Decision(
                outcome=Outcome.NOT_VALID,
                reason=(
                    f"asked {asked} questions and still not sure — "
                    f"saved for you to finish. Left over: "
                    + "; ".join(p.id for p in answerable)
                ),
            )
        nxt = answerable[0]
        return Decision(
            outcome=Outcome.UNCLEAR,
            reason=nxt.detail,
            question_options=tuple(a.value for a in nxt.question.answers),
        )

    return Decision(
        outcome=Outcome.VALID,
        reason="nothing unclear and nothing surprising",
    )


def decide(
    checks: Sequence[CheckResult],
    match: MatchResult,
    flags: Sequence[Flag],
    voucher: Voucher | None = None,
    accounts: Sequence[str] = (),
    history: Sequence[Voucher] = (),
    index=None,
    asked: int = 0,
) -> Decision:
    """Convenience wrapper: build the problems, then decide.

    Pure. Same inputs always produce the same Decision.
    """
    if voucher is None:
        voucher = Voucher(
            id="",
            date=__import__("datetime").date(1970, 1, 1),
            party=match.vendor_key,
            narration="",
            debit_account="",
            credit_account="",
            amount_paise=0,
        )
    problems = find(voucher, checks, match, flags, accounts, history, index)
    return decide_problems(problems, asked=asked)

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
from typing import cast

from accountant.memory.index import MemoryIndex
from accountant.problems import Problem, find
from accountant.questions import Question
from accountant.schema import (
    CheckResult,
    Decision,
    Flag,
    MatchResult,
    Outcome,
    Voucher,
)


def decide_problems(
    problems: Sequence[Problem],
    asked: int = 0,
    answered: Sequence[str] = (),
    operation_id: str = "",
) -> Decision:
    """Apply the decision order to a list of problems.

    `operation_id` is stamped onto every Decision this returns, G5.1. It is
    passed in rather than set afterwards so there is no window in which a
    decision exists without the identity of the operation it decides. Callers
    that reach no write path — the scoring harness, unit tests — leave it empty,
    and an empty one authorises nothing (`Decision.post`).

    `asked` is how many questions have already been put to the person. Once the
    budget is spent the entry is handed over rather than asked about again.

    `answered` is the problem ids the person has ALREADY answered, and it is
    what stops this function and `pipeline.next_question` disagreeing.

    They used to. This picked `answerable[0]` with no regard for what had been
    answered; `next_question` skipped answered ids by the non-overlapping rule.
    So a problem that was answered and then found AGAIN produced UNCLEAR here
    and `None` there, and the page rendered "needs an answer" with no question
    and no buttons. Measured: outcome `unclear`, `next_question()` `None`. The
    person was stranded with nothing to click.

    UNCLEAR is a promise to ask something. If nothing is left to ask, the
    honest outcome is to hand the entry over.
    """
    unanswerable = [p for p in problems if not p.answerable]
    if unanswerable:
        return Decision(
            outcome=Outcome.NOT_VALID,
            reason="; ".join(f"{p.id}: {p.detail}" for p in unanswerable),
            operation_id=operation_id,
        )

    answerable = [p for p in problems if p.answerable]
    already = set(answered)
    outstanding = [p for p in answerable if p.id not in already]

    if answerable and not outstanding:
        return Decision(
            outcome=Outcome.NOT_VALID,
            reason=(
                "you already answered this and it still is not clear — "
                "saved for you to finish. Still unresolved: "
                + "; ".join(p.id for p in answerable)
            ),
            operation_id=operation_id,
        )

    if outstanding:
        from accountant.questions import QUESTION_CAP

        if asked >= QUESTION_CAP:
            return Decision(
                outcome=Outcome.NOT_VALID,
                reason=(
                    f"asked {asked} questions and still not sure — "
                    f"saved for you to finish. Left over: "
                    + "; ".join(p.id for p in outstanding)
                ),
                operation_id=operation_id,
            )
        nxt = outstanding[0]
        # Problem.__post_init__ refuses to construct an answerable problem
        # without a question, so this is never None. Not an assert: python -O
        # deletes asserts, so an assert is not a guarantee in shipped code.
        question = cast(Question, nxt.question)
        return Decision(
            outcome=Outcome.UNCLEAR,
            reason=nxt.detail,
            question_options=tuple(a.value for a in question.answers),
            operation_id=operation_id,
        )

    return Decision(
        outcome=Outcome.VALID,
        reason="nothing unclear and nothing surprising",
        operation_id=operation_id,
    )


def decide(
    checks: Sequence[CheckResult],
    match: MatchResult,
    flags: Sequence[Flag],
    voucher: Voucher | None = None,
    accounts: Sequence[str] = (),
    history: Sequence[Voucher] = (),
    index: MemoryIndex | None = None,
    asked: int = 0,
    operation_id: str = "",
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
    return decide_problems(problems, asked=asked, operation_id=operation_id)

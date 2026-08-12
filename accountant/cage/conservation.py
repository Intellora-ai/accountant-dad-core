"""Four quantities that must be equal, checked before anything is written.

WHY THIS IS THE FIRST MODULE OF THE CAGE
------------------------------------------
Every accuracy claim about reading a bill needs labelled data - a pile of
invoices where somebody already knows the right answer. This repository has
none, and building one is `H-02`.

A conservation law needs none of it. Debits and credits are equal or they are
not. The line items sum to the total or they do not. No expert, no labels, no
model, no network - and the same verdict on a machine that has never seen an
invoice. That makes this the cheapest real safety in the system, and the reason
it is built before the reader that will feed it.

WHY THERE ARE THREE VERDICTS AND NOT TWO
------------------------------------------
`INDETERMINATE` means the law could not be evaluated because a number it needs
was never read. A two-verdict system has to call that PASS or FAIL and both are
wrong:

    PASS   an unread field silently authorises a write. This is the defect this
           repository keeps finding - absence of evidence read as evidence of
           absence - and it is how a GST bill gets posted without its tax.
    FAIL   every bill that did not itemise its lines is refused, which makes the
           product useless.

So it is its own verdict and **the caller blocks on it**. "This is wrong" and
"I could not check" are different sentences, and the person reading the refusal
needs to know which one they got.

WHAT THIS MODULE CANNOT DO, STATED SO NOBODY RELIES ON IT
-----------------------------------------------------------
It cannot detect a bill misread CONSISTENTLY - every figure scaled by ten, so
the lines still sum to the total and the net still plus tax to the gross. That
is failure mode F-02. Arithmetic cannot see it, and nothing here pretends to.
Conservation catches inconsistent errors, which are most of them. It is
deliberately one guard among several, never the only one.

INTEGER PAISE, ALWAYS
----------------------
Every amount is a whole number of paise. A float is refused rather than
coerced, and so is a `bool` - `isinstance(True, int)` is True in Python and
`True == 1`, so a flag passed where an amount belonged would otherwise balance a
one-paisa entry. `checks.py::amount_is_integer_paise` already refuses bools for
that reason; this matches it rather than inventing a second rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

#: The four laws, in the order `run` always returns them. The order is part of
#: the contract: a caller may index it, and a log line reads the same every run.
LAWS: Final[tuple[str, ...]] = (
    "debits_equal_credits",
    "lines_sum_to_total",
    "net_plus_tax_equals_gross",
    "balance_delta_equals_entry",
)


class Verdict(StrEnum):
    """What a law concluded.

    `INDETERMINATE` is not a soft FAIL and not a soft PASS. It is the honest
    answer when a number the law needs was never read, and the decision layer
    treats it as blocking.
    """

    # S105 reads `PASS = "pass"` as a hardcoded credential. It is a verdict.
    # Renaming it to satisfy a linter would make the code worse to read, so the
    # rule is silenced on that one line with the reason written here.
    PASS = "pass"  # noqa: S105
    FAIL = "fail"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class ConservationResult:
    """One law's verdict, and the sentence a person reads if it failed.

    Frozen, like the nine types in `schema.py`. A verdict that can be edited
    after the fact is not evidence.
    """

    law: str
    verdict: Verdict
    said: str


def _paise(value: object, name: str) -> int | None:
    """Return an integer paise amount, or None if it was never read.

    Raises on anything that is not an integer. Coercing here would be the whole
    defect: a float amount means somebody did money in decimals, and a bool
    means somebody passed a flag where an amount belonged.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{name} must be a whole number of paise or None, not "
            f"{type(value).__name__}. Money is never a float and never a flag."
        )
    return value


def _unread(law: str, *names: str) -> ConservationResult:
    """The INDETERMINATE result, naming exactly which figures were missing."""
    missing = ", ".join(names)
    return ConservationResult(
        law=law,
        verdict=Verdict.INDETERMINATE,
        said=(
            f"could not check {law.replace('_', ' ')}: {missing} was not read. "
            "Not checked is not the same as checked and fine, so nothing is "
            "posted on this."
        ),
    )


def _compare(law: str, left: int, right: int, sentence: str) -> ConservationResult:
    """PASS when two paise figures are exactly equal, FAIL otherwise.

    Exact equality, deliberately. A tolerance of one paisa would absorb the
    single most useful signal this module produces - a one-paisa disagreement is
    almost always a misread digit, not a rounding artefact.

    A PASS carries a sentence too, and that is not decoration. Every result
    reaches the audit log, and a row that says only "pass" cannot answer "passed
    on what numbers" months later when a figure looks wrong. The blank string
    was the first version; a test caught it.
    """
    if left == right:
        return ConservationResult(
            law=law,
            verdict=Verdict.PASS,
            said=f"{law.replace('_', ' ')}: {left} paise on both sides.",
        )
    return ConservationResult(law=law, verdict=Verdict.FAIL, said=sentence)


def debits_equal_credits(
    debit_paise: int | None, credit_paise: int | None
) -> ConservationResult:
    """The oldest check in accounting: the two sides of an entry are equal."""
    law = LAWS[0]
    debit = _paise(debit_paise, "debit_paise")
    credit = _paise(credit_paise, "credit_paise")
    if debit is None or credit is None:
        return _unread(law, "the debit side" if debit is None else "the credit side")
    return _compare(
        law,
        debit,
        credit,
        f"the two sides of this entry do not balance: {debit} paise debited "
        f"against {credit} paise credited, out by {abs(debit - credit)} paise.",
    )


def lines_sum_to_total(
    line_paise: tuple[int, ...] | None, total_paise: int | None
) -> ConservationResult:
    """The bill's own arithmetic: its line items add up to its total.

    `None` means the lines were never read. An empty tuple means they were read
    and there were none - which is consistent with a zero total and
    contradictory with any other. Collapsing the two would turn every
    un-itemised bill into a passing one.
    """
    law = LAWS[1]
    total = _paise(total_paise, "total_paise")
    if line_paise is None:
        return _unread(law, "the line items")
    if total is None:
        return _unread(law, "the total")
    lines = tuple(_paise(v, "a line amount") or 0 for v in line_paise)
    added = sum(lines)
    return _compare(
        law,
        added,
        total,
        f"the line items on this bill do not add up to its total: they come to "
        f"{added} paise against a stated total of {total} paise, out by "
        f"{abs(added - total)} paise.",
    )


def net_plus_tax_equals_gross(
    net_paise: int | None, tax_paise: int | None, gross_paise: int | None
) -> ConservationResult:
    """Net plus tax is the gross.

    The unread-tax case is the most dangerous coercion in the system. Reading an
    empty tax field as zero posts a GST bill without its input credit - real
    money lost, with nothing on screen to notice. Zero tax is a fact; an unread
    tax field is not.
    """
    law = LAWS[2]
    net = _paise(net_paise, "net_paise")
    tax = _paise(tax_paise, "tax_paise")
    gross = _paise(gross_paise, "gross_paise")
    if net is None or tax is None or gross is None:
        missing = [
            name
            for name, value in (
                ("the net amount", net),
                ("the tax amount", tax),
                ("the gross amount", gross),
            )
            if value is None
        ]
        return _unread(law, *missing)
    return _compare(
        law,
        net + tax,
        gross,
        f"the amounts on this bill do not agree: {net} paise plus {tax} paise "
        f"of tax comes to {net + tax} paise, but the total says {gross} paise.",
    )


def balance_delta_equals_entry(
    balance_before_paise: int | None,
    balance_after_paise: int | None,
    amount_paise: int | None,
) -> ConservationResult:
    """The books moved by exactly what we asked them to move by.

    This is the law that catches a write TallyPrime accepted and then applied
    differently from the request. It compares; it assumes no direction, because
    money leaving is as ordinary as money arriving.
    """
    law = LAWS[3]
    before = _paise(balance_before_paise, "balance_before_paise")
    after = _paise(balance_after_paise, "balance_after_paise")
    amount = _paise(amount_paise, "amount_paise")
    if before is None or after is None or amount is None:
        missing = [
            name
            for name, value in (
                ("the balance before", before),
                ("the balance after", after),
                ("the entry amount", amount),
            )
            if value is None
        ]
        return _unread(law, *missing)
    moved = after - before
    return _compare(
        law,
        moved,
        amount,
        f"the books did not move by the amount of this entry: the balance "
        f"changed by {moved} paise but the entry was for {amount} paise.",
    )


def run(
    *,
    debit_paise: int | None,
    credit_paise: int | None,
    line_paise: tuple[int, ...] | None,
    total_paise: int | None,
    net_paise: int | None,
    tax_paise: int | None,
    gross_paise: int | None,
    balance_before_paise: int | None,
    balance_after_paise: int | None,
) -> tuple[ConservationResult, ...]:
    """Every law, always, in `LAWS` order.

    Not "the ones that applied". A caller receiving three results cannot tell
    which law was skipped or why, and a run that stopped at the first failure
    would report one problem when there are two - so the person fixes one and
    resubmits straight into the second.
    """
    return (
        debits_equal_credits(debit_paise, credit_paise),
        lines_sum_to_total(line_paise, total_paise),
        net_plus_tax_equals_gross(net_paise, tax_paise, gross_paise),
        balance_delta_equals_entry(
            balance_before_paise, balance_after_paise, debit_paise
        ),
    )

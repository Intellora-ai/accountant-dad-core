"""The arithmetic. Integer paise in, integer paise out, or a refusal.

WHY THERE IS NO ROUNDING
------------------------
Rounding tax needs a rule, and a rule needs a source. The corpus has no CBIC
document telling it how to round a fraction of a paise, so it does not round —
it refuses, and says why. `9%` of one paise is nine ten-thousandths of a paise;
there is no honest integer for that, and inventing one puts a number nobody can
justify into somebody's statutory books.

That refusal is cheap here and expensive later. Nothing posts today (owner
decision Q3 = D), so a refusal costs a person one line of explanation. The day
posting is switched on, this is the difference between a return that reconciles
and one that does not.

WHY BASIS POINTS
----------------
`rate_basis_points` is an int: 14% is 1400, 0.125% is 12.5 — which is not an int,
and is exactly why the corpus holds no such rate today and the loader would have
to grow a smaller unit before it could. Multiplying paise by basis points and
dividing by 10,000 keeps every intermediate value an integer. There is no float
in this module and `tests/test_gst_tax_engine.py` asserts there is none in
the result either.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from accountant.rules.gst_rates import RateRule, TaxType

#: basis points per whole unit. 10,000 bp = 100%.
BASIS_POINTS_PER_UNIT = 10_000


class ComputationOutcome(StrEnum):
    COMPUTED = "computed"
    TAXABLE_NOT_POSITIVE = "taxable_not_positive"
    NOT_EXACT_IN_PAISE = "not_exact_in_paise"
    NO_RATES = "no_rates"


@dataclass(frozen=True)
class TaxLine:
    """One tax, its rate, its amount, and the rule that produced it.

    `rule_id` is not decoration. A tax line whose rule cannot be named is a
    number with no author, and this project has already had to un-quote two of
    those.
    """

    tax_type: TaxType
    rate_basis_points: int
    amount_paise: int
    rule_id: str
    source_url: str


@dataclass(frozen=True)
class TaxComputation:
    outcome: ComputationOutcome
    reason: str
    taxable_paise: int
    lines: tuple[TaxLine, ...] = ()

    @property
    def total_tax_paise(self) -> int | None:
        if self.outcome is not ComputationOutcome.COMPUTED:
            return None
        return sum(line.amount_paise for line in self.lines)

    @property
    def total_including_tax_paise(self) -> int | None:
        total = self.total_tax_paise
        return None if total is None else self.taxable_paise + total


def line_amount_paise(taxable_paise: int, rate_basis_points: int) -> int | None:
    """Exact paise, or None. Never a rounded approximation.

    `taxable_paise * rate_basis_points` is computed first and the remainder is
    checked before the division, so the question "is this exact?" is answered by
    integer arithmetic rather than by comparing a float to itself.
    """
    product = taxable_paise * rate_basis_points
    if product % BASIS_POINTS_PER_UNIT != 0:
        return None
    return product // BASIS_POINTS_PER_UNIT


def compute(taxable_paise: int, rules: Sequence[RateRule]) -> TaxComputation:
    """One line per rule, in the order given. The caller chose the rules.

    This function does not decide WHICH taxes apply — that is
    `place_of_supply.determine` feeding `tax.decision`. Keeping the choice out of
    the arithmetic is what stops a bug in the split from being hidden by a
    compensating bug in the multiplication.
    """
    not_an_int = isinstance(taxable_paise, bool) or not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
        taxable_paise, int
    )
    if not_an_int:
        # Same reasoning as `checks.amount_is_integer_paise`: the annotation is
        # not enforced at runtime, bool is an int, and a float has reached a
        # money field in this repository before.
        return TaxComputation(
            outcome=ComputationOutcome.TAXABLE_NOT_POSITIVE,
            reason=(
                f"the taxable amount is {taxable_paise!r}, a "
                f"{type(taxable_paise).__name__}; money is integer paise"
            ),
            taxable_paise=0,
        )
    if taxable_paise <= 0:
        return TaxComputation(
            outcome=ComputationOutcome.TAXABLE_NOT_POSITIVE,
            reason=(
                f"the taxable amount is {taxable_paise} paise, so there is "
                "nothing to tax"
            ),
            taxable_paise=taxable_paise,
        )
    if not rules:
        return TaxComputation(
            outcome=ComputationOutcome.NO_RATES,
            reason="no rate rule was supplied, so no tax line can be built",
            taxable_paise=taxable_paise,
        )

    lines: list[TaxLine] = []
    for rule in rules:
        amount = line_amount_paise(taxable_paise, rule.rate_basis_points)
        if amount is None:
            return TaxComputation(
                outcome=ComputationOutcome.NOT_EXACT_IN_PAISE,
                reason=(
                    f"{rule.rate_basis_points / 100}% of {taxable_paise} paise is not "
                    "a whole number of paise, and the corpus has no official rule "
                    "for rounding it, so no tax line is built"
                ),
                taxable_paise=taxable_paise,
            )
        lines.append(
            TaxLine(
                tax_type=rule.tax_type,
                rate_basis_points=rule.rate_basis_points,
                amount_paise=amount,
                rule_id=rule.rule_id,
                source_url=rule.source.url,
            )
        )
    return TaxComputation(
        outcome=ComputationOutcome.COMPUTED,
        reason="every tax line is exact in paise",
        taxable_paise=taxable_paise,
        lines=tuple(lines),
    )

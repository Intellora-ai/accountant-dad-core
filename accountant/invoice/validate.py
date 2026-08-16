"""Seven things that must be true about a bill's own figures. Integer paise.

WHY THIS IS WORTH BUILDING WITH NO CORPUS
-------------------------------------------
Every accuracy claim about READING a bill needs labelled data. This repository
has none that is any use - one GSTIN in the whole of `data/`, on a tribunal
order - and `docs/INVOICE_EXTRACTION_FRAMEWORK.md` says so at length.

A conservation law needs none of it. Quantity times rate less the discount is
the taxable value or it is not. The lines add up to the subtotal or they do
not. No expert, no labels, no model, no network, and the same verdict on a
machine that has never seen an invoice. That is why this module exists before
any real fixture does, and it is the same argument `cage/conservation.py` makes
about the four laws it holds.

WHAT IT NEVER DOES
-------------------
IT NEVER CHANGES A VALUE. A failure is RECORDED. Not corrected, not rounded to
fit, not back-solved. A validator that mends a figure to make its own law hold
has removed the one signal the law was for - and the mended figure then travels
onward looking exactly like a figure somebody read correctly.

IT NEVER DOWNGRADES A CONSERVATION VERDICT. Two of the seven laws below are
`cage/conservation.py`'s, called rather than re-implemented, and their answer
is carried through word for word. THE TOLERANCE DOES NOT REACH THEM. Those
comparisons are exact by decision - a one-paisa disagreement is almost always a
misread digit, not rounding - and widening them is not this module's to do.

WHERE THE TOLERANCE DOES REACH, AND WHY THERE IS ONE AT ALL
-------------------------------------------------------------
The three laws this module owns: the line arithmetic, the tax parts, and the
figure a bill rounds. Those three are the ones where a supplier's own printing
software rounds, and it rounds in ways this reader cannot reproduce - a rate of
`33.333` printed as `33.33` is a bill that genuinely does not multiply out.

DEFAULT ZERO. Exact equality unless a caller says otherwise, in whole paise,
and the number appears in the sentence a person reads so nobody has to guess
what was allowed.

INTEGER ARITHMETIC THROUGHOUT, INCLUDING THE QUANTITY
------------------------------------------------------
A quantity is held in THOUSANDTHS of a unit and a rate in paise, so their
product is in thousandths of a paisa and every comparison here happens at that
scale. No float touches an amount at any point. `2.5 x 33.33` is
`2500 x 3333 = 8332500` thousandths of a paisa, which is `8332.5` paise - a
figure with half a paisa in it, and a bill that prints `83.33` is out by half a
paisa for a real reason. That is what the tolerance is for and why it is
measured at this scale rather than rounded first.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from accountant.cage.conservation import (
    ConservationResult,
    Verdict,
    lines_sum_to_total,
    net_plus_tax_equals_gross,
)
from accountant.money import format_inr

#: Thousandths of a unit to the unit, and thousandths of a paisa to the paisa.
#: One constant because it is one scale: a quantity in thousandths times a rate
#: in paise lands in thousandths of a paisa, which is where every comparison in
#: this file happens.
MILLI: Final = 1000


class Law(StrEnum):
    """The seven, in the order `run` always returns them.

    The order is part of the contract, exactly as `conservation.LAWS` is: a
    caller may index it, and a log line reads the same every run.
    """

    LINE_ARITHMETIC = "line_arithmetic"
    LINES_SUM_TO_TAXABLE = "lines_sum_to_taxable_value"
    TAX_PARTS_SUM_TO_TOTAL_TAX = "tax_parts_sum_to_total_tax"
    TAXABLE_PLUS_TAX_IS_GRAND_TOTAL = "taxable_plus_tax_is_grand_total"
    ONE_TAX_SHAPE = "one_tax_shape"
    MANDATORY_FIELDS = "mandatory_fields_present"
    NOT_A_REPEAT = "not_a_repeat_of_a_bill_already_seen"


@dataclass(frozen=True)
class Tolerance:
    """How far apart two figures may be before it is a disagreement.

    ZERO BY DEFAULT. A caller that has not thought about rounding gets exact
    equality, which is the stricter answer, so forgetting costs a question on a
    screen rather than a wrong figure in somebody's books.
    """

    paise: int = 0

    def __post_init__(self) -> None:
        if type(self.paise) is not int:
            raise TypeError(
                f"a tolerance is a whole number of paise, not "
                f"{type(self.paise).__name__}. Money is never a float."
            )
        if self.paise < 0:
            raise ValueError(
                f"a tolerance of {self.paise} paise is not a distance. "
                "Two figures cannot be less than no distance apart."
            )

    @property
    def milli_paise(self) -> int:
        """The same distance at the scale the line arithmetic works in."""
        return self.paise * MILLI


#: What a caller gets when it states nothing: exact equality, everywhere.
EXACTLY: Final = Tolerance()


@dataclass(frozen=True)
class Finding:
    """One law's verdict, and the sentence a person reads.

    Frozen, like every verdict in this repository. A judgement that can be
    edited after the fact is not evidence.

    `out_by_paise` is `None` when the law could not be evaluated, and it is not
    `0` in that case. Zero means the two figures agreed exactly.
    """

    law: Law
    verdict: Verdict
    said: str
    out_by_paise: int | None = None


def _gap(paise: int) -> str:
    """How far apart two figures are, in the words a person would use.

    Under a rupee it stays a paise count. "Out by 1 paisa" makes a single
    misread digit obvious; "out by ₹0.01" reads like a rounding artefact
    somebody can ignore, and this module exists because that disagreement is
    almost never rounding.

    MIRRORS `conservation._out_by`, which is private to that module. Same
    wording, same threshold, and the singular is `paisa` for the same reason:
    this is on the screen of a reader whose own currency it is.
    """
    if paise < 100:
        return f"{paise} {'paisa' if paise == 1 else 'paise'}"
    return format_inr(paise)


def _allowed(tolerance: Tolerance) -> str:
    """The phrase that says what slack was permitted, or nothing at all."""
    if tolerance.paise == 0:
        return ""
    return f" Nothing above {_gap(tolerance.paise)} was allowed here."


def _unread(law: Law, *names: str) -> Finding:
    """The INDETERMINATE finding, naming exactly which figures were missing.

    INDETERMINATE is not a soft PASS and not a soft FAIL, for the reason
    `conservation.py` gives at length: reading PASS from an unread field is how
    a GST bill gets posted without its tax, and reading FAIL refuses every bill
    that did not print the figure. The caller blocks on it.
    """
    return Finding(
        law=law,
        verdict=Verdict.INDETERMINATE,
        said=(
            f"could not check {law.value.replace('_', ' ')}: "
            f"{', '.join(names)} was not read. Not checked is not the same as "
            "checked and fine, so nothing is posted on this."
        ),
    )


# =============================================================================
# 1. one line multiplies out
# =============================================================================


def line_arithmetic(
    *,
    quantity_milli: int | None,
    rate_paise: int | None,
    discount_paise: int | None,
    taxable_paise: int | None,
    line: int = 1,
    tolerance: Tolerance = EXACTLY,
) -> Finding:
    """Quantity times rate, less the discount, is this line's taxable value.

    A MISSING DISCOUNT IS ZERO AND A MISSING RATE IS NOT, and the asymmetry is
    the point. A bill with no discount column has no discount - that is a fact
    about the bill. A bill whose rate did not read has a rate that nobody knows,
    and treating that as zero would make every unread line pass at a taxable
    value of zero. `conservation.py` makes the identical argument about an
    unread tax field, which is the same coercion one column over.
    """
    if quantity_milli is None or rate_paise is None or taxable_paise is None:
        missing = [
            name
            for name, value in (
                ("the quantity", quantity_milli),
                ("the rate", rate_paise),
                ("the taxable value", taxable_paise),
            )
            if value is None
        ]
        return _unread(Law.LINE_ARITHMETIC, *missing)

    discount = 0 if discount_paise is None else discount_paise
    expected = quantity_milli * rate_paise - discount * MILLI
    stated = taxable_paise * MILLI
    apart = abs(expected - stated)
    if apart <= tolerance.milli_paise:
        return Finding(
            law=Law.LINE_ARITHMETIC,
            verdict=Verdict.PASS,
            said=(
                f"line {line} multiplies out: it comes to "
                f"{format_inr(expected // MILLI)} and states "
                f"{format_inr(taxable_paise)}."
            ),
            out_by_paise=apart // MILLI,
        )
    return Finding(
        law=Law.LINE_ARITHMETIC,
        verdict=Verdict.FAIL,
        said=(
            f"line {line} does not multiply out: quantity times rate less the "
            f"discount comes to {format_inr(expected // MILLI)}, and the line "
            f"states {format_inr(taxable_paise)}, out by "
            f"{_gap(apart // MILLI)}.{_allowed(tolerance)}"
        ),
        out_by_paise=apart // MILLI,
    )


# =============================================================================
# 2 and 4. the two that belong to the cage
# =============================================================================


def _carried(law: Law, result: ConservationResult) -> Finding:
    """A conservation verdict, carried through with its own words.

    NOT RE-JUDGED AND NOT RE-WORDED. `cage/conservation.py` is the one place
    these two comparisons happen; a second comparison here that agreed with it
    today is a second comparison to keep agreeing with it for ever.
    """
    return Finding(law=law, verdict=result.verdict, said=result.said)


def lines_sum_to_taxable(
    line_taxable_paise: tuple[int, ...] | None, taxable_paise: int | None
) -> Finding:
    """The line items add up to the bill's own taxable value.

    `None` for the lines means they were never read. An EMPTY TUPLE means they
    were read and there were none, which is consistent with a zero taxable
    value and contradictory with any other - collapsing the two would turn every
    un-itemised bill into a passing one. That distinction belongs to
    `conservation.lines_sum_to_total`, which is what this calls.
    """
    return _carried(
        Law.LINES_SUM_TO_TAXABLE,
        lines_sum_to_total(line_taxable_paise, taxable_paise),
    )


def taxable_plus_tax_is_grand_total(
    *,
    taxable_paise: int | None,
    total_tax_paise: int | None,
    round_off_paise: int | None,
    grand_total_paise: int | None,
) -> Finding:
    """Taxable plus tax plus the round-off is the amount payable.

    AN ABSENT ROUND-OFF IS ZERO, and this is the one place in this package
    where an absent figure is read as a number. The argument is that it can
    only ever cause a FAILURE and never a false pass:

        a bill with no round-off  -  taxable plus tax already equals the total,
                                     and the law passes on the true figures.
        a round-off nobody read   -  the sum is short or long by exactly that
                                     amount, and the law FAILS and says so.

    So the coercion `conservation.py` refuses for tax - where reading an unread
    field as zero silently authorises a post - is safe here, because it moves
    the answer towards refusing rather than towards posting. It is written down
    rather than assumed, because the next person to add a field here will reach
    for the same shortcut where it is not safe.
    """
    round_off = 0 if round_off_paise is None else round_off_paise
    net = None if taxable_paise is None else taxable_paise + round_off
    return _carried(
        Law.TAXABLE_PLUS_TAX_IS_GRAND_TOTAL,
        net_plus_tax_equals_gross(net, total_tax_paise, grand_total_paise),
    )


# =============================================================================
# 3. the tax parts add up to the tax
# =============================================================================


def tax_parts_sum_to_total_tax(
    *,
    cgst_paise: int | None,
    sgst_paise: int | None,
    igst_paise: int | None,
    cess_paise: int | None,
    total_tax_paise: int | None,
    total_tax_was_stated: bool,
    tolerance: Tolerance = EXACTLY,
) -> Finding:
    """CGST plus SGST plus IGST plus cess is the total tax.

    `total_tax_was_stated` IS A SEPARATE ARGUMENT FROM THE FIGURE, and without
    it this law passes by construction on most bills. Very few invoices print a
    "Total Tax" line; the parser works the total out by adding the parts up. A
    law that then checks the parts against that sum is checking a number against
    itself, which passes always and proves nothing.

    So when the bill did not state it, this reports INDETERMINATE. The check
    still happens - one law later, where the worked-out total is weighed against
    the grand total the bill DID print, which is a figure this reader did not
    compute. `conservation.py` makes exactly this argument about why it wants a
    net figure as well as a tax and a gross.

    A PART THAT IS ABSENT IS ZERO HERE, unlike the rate above, and for the
    reason the shape law below exists: an intra-state bill has no IGST line and
    an inter-state bill has no CGST line. Absence of one of the four is the
    ordinary case, not a reading failure - and every one of them absent gives a
    sum of zero, which fails against any real total.
    """
    if not total_tax_was_stated:
        return Finding(
            law=Law.TAX_PARTS_SUM_TO_TOTAL_TAX,
            verdict=Verdict.INDETERMINATE,
            said=(
                "this bill states no total tax of its own, so the tax lines "
                "were added up to make one. Checking them against that sum "
                "would be checking a number against itself, so it was not done "
                "- the sum is checked against the amount payable instead."
            ),
        )
    if total_tax_paise is None:
        return _unread(Law.TAX_PARTS_SUM_TO_TOTAL_TAX, "the total tax")

    parts = tuple(
        0 if value is None else value
        for value in (cgst_paise, sgst_paise, igst_paise, cess_paise)
    )
    added = sum(parts)
    apart = abs(added - total_tax_paise)
    if apart <= tolerance.paise:
        return Finding(
            law=Law.TAX_PARTS_SUM_TO_TOTAL_TAX,
            verdict=Verdict.PASS,
            said=(
                f"the tax lines add up: they come to {format_inr(added)} "
                f"against a stated total tax of {format_inr(total_tax_paise)}."
            ),
            out_by_paise=apart,
        )
    return Finding(
        law=Law.TAX_PARTS_SUM_TO_TOTAL_TAX,
        verdict=Verdict.FAIL,
        said=(
            f"the tax lines on this bill do not add up to its total tax: they "
            f"come to {format_inr(added)} against a stated "
            f"{format_inr(total_tax_paise)}, out by "
            f"{_gap(apart)}.{_allowed(tolerance)}"
        ),
        out_by_paise=apart,
    )


# =============================================================================
# 5. a bill is intra-state or inter-state, never both
# =============================================================================


def one_tax_shape(
    *, cgst_paise: int | None, sgst_paise: int | None, igst_paise: int | None
) -> Finding:
    """CGST and SGST together, or IGST alone. Both together is a contradiction.

    A supply is inside one state or it crosses a border. If it is inside, the
    tax is split into a central half and a state half and both are printed. If
    it crosses, one integrated figure is printed instead. A bill carrying both
    is describing two different supplies, and no reading of it is safe.

    ONE HALF ALONE IS ALSO A FAILURE, and it is the expensive one. Reading CGST
    and missing SGST posts HALF the input credit, with a bill that still looks
    read - real money lost and nothing on the screen to notice.
    `labels.TAX_PARTS` carries the same warning about the same two figures.

    NEITHER SHAPE PRESENT IS INDETERMINATE. A bill with no tax at all is real -
    an exempt supply, a bill of supply - and calling that a failure would refuse
    a document that is perfectly correct.
    """
    intra = cgst_paise is not None or sgst_paise is not None
    inter = igst_paise is not None
    if intra and inter:
        return Finding(
            law=Law.ONE_TAX_SHAPE,
            verdict=Verdict.FAIL,
            said=(
                "this bill prints both kinds of GST. A sale inside one state "
                "prints CGST and SGST; a sale across a state border prints "
                "IGST. Both on one bill describes two different sales, so "
                "nothing is read from its tax."
            ),
        )
    if intra and (cgst_paise is None or sgst_paise is None):
        half = "CGST" if cgst_paise is not None else "SGST"
        missing = "SGST" if cgst_paise is not None else "CGST"
        return Finding(
            law=Law.ONE_TAX_SHAPE,
            verdict=Verdict.FAIL,
            said=(
                f"this bill prints {half} and no {missing}. A sale inside one "
                f"state prints both halves, so either the {missing} line was "
                "not read or this is not the kind of bill it looks like. "
                "Taking the one half would claim back half the tax you paid."
            ),
        )
    if not intra and not inter:
        return Finding(
            law=Law.ONE_TAX_SHAPE,
            verdict=Verdict.INDETERMINATE,
            said=(
                "no GST line was read on this bill. That is ordinary on a bill "
                "of supply and it is also what an unread tax line looks like, "
                "so nothing is concluded from it either way."
            ),
        )
    shape = (
        "IGST, so it is a sale across a state border"
        if inter
        else ("CGST and SGST, so it is a sale inside one state")
    )
    return Finding(
        law=Law.ONE_TAX_SHAPE,
        verdict=Verdict.PASS,
        said=f"this bill prints {shape}.",
    )


# =============================================================================
# 6. the fields nothing can proceed without
# =============================================================================

#: What must be on a bill before anything downstream may look at it, and the
#: words a person is told when it is not. HELD AS DATA so the list can be read
#: in one place rather than reconstructed from an `if` chain.
#:
#: THE SUPPLIER'S REGISTRATION NUMBER IS NOT ON THIS LIST, deliberately. A bill
#: from an unregistered supplier is a real bill and refusing it would refuse a
#: whole class of ordinary purchase. Its ABSENCE changes what tax treatment
#: applies, which is `rules/place_of_supply.py`'s question and not this one's.
MANDATORY: Final[tuple[tuple[str, str], ...]] = (
    ("supplier", "who this bill is from"),
    ("invoice_number", "the bill's own number"),
    ("invoice_date", "the date on the bill"),
    ("grand_total", "the amount payable"),
)


def mandatory_fields_present(read: Sequence[str]) -> Finding:
    """Every field on `MANDATORY` was found, or the missing ones are named.

    Takes the names that WERE read rather than a record, so this function can
    be fired directly by a test with no document in sight - and so that adding
    a field to the record does not silently change what is mandatory.
    """
    have = set(read)
    missing = [words for name, words in MANDATORY if name not in have]
    if not missing:
        return Finding(
            law=Law.MANDATORY_FIELDS,
            verdict=Verdict.PASS,
            said="everything a bill must say was read off this one.",
        )
    return Finding(
        law=Law.MANDATORY_FIELDS,
        verdict=Verdict.FAIL,
        said=(
            "this bill is missing something it must have: "
            + ", and ".join(missing)
            + ". Nothing was guessed to fill the gap."
        ),
    )


# =============================================================================
# 7. have we seen this bill before
# =============================================================================


def not_a_repeat(
    *,
    supplier_key: str | None,
    invoice_number: str | None,
    already_seen: frozenset[tuple[str, str]],
) -> Finding:
    """This supplier has not sent this invoice number before, in THIS run.

    THE PERSISTENT STORE FOR THIS DOES NOT EXIST, AND THIS FUNCTION DOES NOT
    PRETEND TO BE ONE. `accountant/memory/` was read before this was written.
    It indexes vendors and narration phrases, and it claims OPERATION IDS -
    `MemoryStore.claim_operation`, which stops the same WRITE being sent twice.
    There is no table anywhere in this repository keyed by supplier and invoice
    number, so there is nothing to ask about a bill seen last month.

    So `already_seen` is handed IN, the caller owns it, and this function
    stores nothing. `batch.py` fills it from the run in progress, which catches
    the same file listed twice and the same bill photographed twice - and
    catches nothing at all across two runs. That limitation is recorded in
    `docs/INVOICE_EXTRACTION_FRAMEWORK.md` rather than papered over with a set
    that quietly empties every time the process restarts.

    UNREAD IS INDETERMINATE. A bill whose number did not read cannot be
    compared with anything, and calling that "not a repeat" is the answer that
    lets a duplicate through.
    """
    if not supplier_key or not invoice_number:
        missing = [
            name
            for name, value in (
                ("who it is from", supplier_key),
                ("its number", invoice_number),
            )
            if not value
        ]
        return _unread(Law.NOT_A_REPEAT, *missing)
    if (supplier_key, invoice_number) in already_seen:
        return Finding(
            law=Law.NOT_A_REPEAT,
            verdict=Verdict.FAIL,
            said=(
                f"bill {invoice_number} from {supplier_key} has already been "
                "read in this run. Reading it twice would put it in the books "
                "twice."
            ),
        )
    return Finding(
        law=Law.NOT_A_REPEAT,
        verdict=Verdict.PASS,
        said=(
            f"bill {invoice_number} from {supplier_key} has not been seen "
            "before in this run. Earlier runs were not checked, because "
            "nothing in this system remembers a bill number between runs."
        ),
    )


# =============================================================================
# all of them, always
# =============================================================================


@dataclass(frozen=True)
class Figures:
    """Every number the seven laws need, in whole paise. `None` means unread.

    One argument object rather than nineteen keyword arguments, because a
    nineteen-argument call is a call where two amounts get swapped and nothing
    notices. Frozen and validated on construction, so a float amount is refused
    at the boundary rather than several comparisons later.
    """

    line_quantity_milli: tuple[int | None, ...] = ()
    line_rate_paise: tuple[int | None, ...] = ()
    line_discount_paise: tuple[int | None, ...] = ()
    line_taxable_paise: tuple[int | None, ...] | None = None
    taxable_paise: int | None = None
    cgst_paise: int | None = None
    sgst_paise: int | None = None
    igst_paise: int | None = None
    cess_paise: int | None = None
    total_tax_paise: int | None = None
    total_tax_was_stated: bool = False
    round_off_paise: int | None = None
    grand_total_paise: int | None = None
    fields_read: tuple[str, ...] = ()
    supplier_key: str | None = None
    invoice_number: str | None = None

    def __post_init__(self) -> None:
        counts = {
            len(self.line_quantity_milli),
            len(self.line_rate_paise),
            len(self.line_discount_paise),
        }
        if len(counts) > 1:
            raise ValueError(
                "the per-line figures have different lengths "
                f"({sorted(counts)}). They index each other, so a mismatch "
                "means one line's rate would be checked against another's "
                "quantity."
            )


def run(
    figures: Figures,
    *,
    tolerance: Tolerance = EXACTLY,
    already_seen: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[Finding, ...]:
    """Every law, always, in `Law` order. One finding per line for the first.

    NOT "THE ONES THAT APPLIED", for the reason `conservation.run` gives: a
    caller receiving five findings cannot tell which law was skipped or why,
    and a run that stopped at the first failure would report one problem when
    there are three - so the person fixes one, resubmits, and walks into the
    second.
    """
    per_line = tuple(
        line_arithmetic(
            quantity_milli=quantity,
            rate_paise=rate,
            discount_paise=discount,
            taxable_paise=(
                None
                if figures.line_taxable_paise is None
                or index >= len(figures.line_taxable_paise)
                else figures.line_taxable_paise[index]
            ),
            line=index + 1,
            tolerance=tolerance,
        )
        for index, (quantity, rate, discount) in enumerate(
            zip(
                figures.line_quantity_milli,
                figures.line_rate_paise,
                figures.line_discount_paise,
                strict=True,
            )
        )
    )
    stated_lines = (
        None
        if figures.line_taxable_paise is None
        else tuple(value for value in figures.line_taxable_paise if value is not None)
    )
    return (
        *per_line,
        lines_sum_to_taxable(stated_lines, figures.taxable_paise),
        tax_parts_sum_to_total_tax(
            cgst_paise=figures.cgst_paise,
            sgst_paise=figures.sgst_paise,
            igst_paise=figures.igst_paise,
            cess_paise=figures.cess_paise,
            total_tax_paise=figures.total_tax_paise,
            total_tax_was_stated=figures.total_tax_was_stated,
            tolerance=tolerance,
        ),
        taxable_plus_tax_is_grand_total(
            taxable_paise=figures.taxable_paise,
            total_tax_paise=figures.total_tax_paise,
            round_off_paise=figures.round_off_paise,
            grand_total_paise=figures.grand_total_paise,
        ),
        one_tax_shape(
            cgst_paise=figures.cgst_paise,
            sgst_paise=figures.sgst_paise,
            igst_paise=figures.igst_paise,
        ),
        mandatory_fields_present(figures.fields_read),
        not_a_repeat(
            supplier_key=figures.supplier_key,
            invoice_number=figures.invoice_number,
            already_seen=already_seen,
        ),
    )


def failed(findings: Sequence[Finding]) -> tuple[Finding, ...]:
    """The findings that say something is wrong. FAIL only, never the unread.

    `INDETERMINATE` is deliberately not in here and the caller must ask for it
    separately. "This is wrong" and "I could not check" lead to different
    statuses and different sentences, and a function that returned both would
    make them the same word again.
    """
    return tuple(one for one in findings if one.verdict is Verdict.FAIL)


def unchecked(findings: Sequence[Finding]) -> tuple[Finding, ...]:
    """The findings that could not be evaluated because a figure was unread."""
    return tuple(one for one in findings if one.verdict is Verdict.INDETERMINATE)

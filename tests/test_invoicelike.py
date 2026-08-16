"""Does this page look like a bill, and does saying so ever cost a read.

WHY THIS FILE EXISTS
--------------------
`accountant/invoicelike.py` was written because 407 of 413 real
documents read no field and nobody could tell, from the record alone, whether
that was the reader failing or the document not being a bill. Both produced the
same blank.

THE CONTROL THAT MATTERS MOST IS NOT THE ONE THAT PROVES IT WORKS.
It is `test_the_verdict_is_a_label_and_never_a_gate` and its neighbours: this
module must never be able to remove a field the reader read. The obvious wiring -
"if it does not look like an invoice, return nothing" - adds a new way to lose a
real bill, and a false negative there is indistinguishable from a reader failure.

WHAT THIS FILE DOES NOT PROVE
------------------------------
That the detector is accurate. It was measured on TWELVE documents whose class
was established by hand - seven bills, five not. That is an anecdote. The
numbers are in the module docstring and they are labelled as an anecdote there
too.
"""

from __future__ import annotations

import pathlib

import pytest

from accountant.invoicelike import (
    ENOUGH_SIGNALS,
    SIGNALS,
    looks_like_a_bill,
)

REPO = pathlib.Path(__file__).resolve().parent.parent

#: A page carrying the things a bill carries. Not copied from anybody's books:
#: the supplier, the figures and the identity are all invented for this file.
A_BILL = """
TAX INVOICE
SUPPLIER: SHARMA TRADERS
GSTIN: 27AABCU9603R1ZM
Invoice No: INV-001        Date: 11/08/2026
SUBTOTAL: 48,000.00
GST @ 18%: 8,640.00
TOTAL: Rs 56,640.00
"""

#: A real shape from the corpus: a government appeal order. It carries a GSTIN
#: and tax words because it is ABOUT a taxpayer, and it is not a bill.
AN_ORDER = """
Order reference no. : ZA270010226000054H
1. GSTIN/Temporary ID/UIN - 21AAACS9939D1ZG
2. Appeal Case Reference no. - APL/1/PB/2026
total liability of Rs 45,61,546/-. Since the Appellant had
"""


# ---- what it recognises -------------------------------------------------------


def test_a_bill_is_recognised_as_one() -> None:
    """The happy path, and the least interesting test here."""
    verdict = looks_like_a_bill(A_BILL)

    assert verdict.looks_like_a_bill is True
    assert len(verdict.signals) >= ENOUGH_SIGNALS


def test_a_page_with_nothing_a_bill_prints_is_refused() -> None:
    """MEASURED on the corpus: 36 of 77 PDFs carry no total-type label anywhere.
    This is the sentence those documents get instead of a silence."""
    verdict = looks_like_a_bill("a photograph of a building, taken in 2019")

    assert verdict.looks_like_a_bill is False
    assert verdict.signals == ()
    assert "does not look like a bill" in verdict.said


def test_an_empty_page_says_so_rather_than_guessing() -> None:
    """`INDETERMINATE`'s argument, in a smaller place: nothing read is not the
    same as read and found wanting, and the sentence has to say which."""
    verdict = looks_like_a_bill("   \n  \t ")

    assert verdict.looks_like_a_bill is False
    assert "no readable text" in verdict.said


# ---- the controls, which are the point ----------------------------------------


def test_the_verdict_is_a_label_and_never_a_gate() -> None:
    """THE CONTROL THIS MODULE EXISTS UNDER. It returns a verdict and nothing
    else - no fields, no record, no reader. It is structurally incapable of
    removing a value somebody read, because it never sees one.

    If this module ever grows a function that takes a record or a Reading, that
    is the day a false negative starts deleting real invoices."""
    verdict = looks_like_a_bill(A_BILL)

    assert not hasattr(verdict, "date")
    assert not hasattr(verdict, "total_paise")
    assert set(vars(verdict)) == {"looks_like_a_bill", "signals", "said"}


def test_it_reads_nothing_and_starts_nothing() -> None:
    """No file, no program, no network - so no timeout and no failure mode of
    its own. `conservation.py` holds the same property for the same reason: a
    judgement that can fail is a judgement nobody can rely on."""
    source = (REPO / "accountant" / "invoicelike.py").read_text()

    for forbidden in ("open(", "subprocess", "requests", "urllib", "socket", "Path("):
        assert forbidden not in source, forbidden


def test_a_government_order_that_carries_a_gstin_is_still_not_a_bill() -> None:
    """THE HONEST FAILURE, written down rather than hidden. This document is
    real - `data/real_invoices_indian/gst-portal-and-govt-002.pdf` - and it
    carries a GSTIN, tax words and an amount because it is ABOUT a taxpayer.

    The detector calls it a bill. That is a FALSE POSITIVE and this test asserts
    it, so the number in the module docstring stays true and nobody discovers it
    by surprise. It is the cheap direction to be wrong in: a document wrongly
    called a bill is simply read, and produces nothing, which is what happens
    today anyway."""
    verdict = looks_like_a_bill(AN_ORDER)

    assert verdict.looks_like_a_bill is True  # and it is wrong to


def test_no_single_signal_can_carry_a_document_on_its_own() -> None:
    """`ENOUGH_SIGNALS` is 2 for this reason. A page saying only the word
    `total` is a spreadsheet; a page saying only `tax` is a form. One signal is
    a coincidence and the threshold is what says so."""
    for name, _ in SIGNALS:
        assert ENOUGH_SIGNALS > 1, name


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        ("TOTAL: 500.00", False),  # one signal - a spreadsheet row
        ("GST return for the quarter", False),  # one signal - a form
        ("INVOICE\nTOTAL: 500.00", True),  # two - a bill
    ],
)
def test_the_threshold_is_where_the_measurement_put_it(
    page: str, expected: bool
) -> None:
    """Moving `ENOUGH_SIGNALS` to 3 loses `vendor-samples-058.pdf`, a real
    invoice scoring exactly 2. Moving it to 1 makes every spreadsheet a bill."""
    assert looks_like_a_bill(page).looks_like_a_bill is expected

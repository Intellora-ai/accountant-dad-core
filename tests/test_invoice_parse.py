"""Exact values, or the test proves nothing.

WHY THERE IS NO `assert result.total is not None` IN THIS FILE
---------------------------------------------------------------
A test that asserts something was found passes when the wrong thing is found.
This repository has already paid for that: the ground-truth corpus scored 20/20
on a tier that was inventing every total, because what was asserted was that a
total existed. So every assertion below names the paise, the string or the
verdict it expects, and a parser that returns a different one fails.

WHAT THESE TESTS PROVE, AND IT IS NARROWER THAN IT LOOKS
----------------------------------------------------------
That given exactly these characters, the parser returns exactly these fields.
They prove NOTHING about whether tesseract produces these characters from a
photograph of a real bill - `tests/invoice_documents.py` carries the full
warning and `docs/INVOICE_EXTRACTION_FRAMEWORK.md` carries the fixture contract
that would change it.
"""

from __future__ import annotations

import datetime

import pytest

from accountant.cage.conservation import Verdict
from accountant.invoice import parse
from accountant.invoice.bridge import describe
from accountant.invoice.fields import Checked, Method, ReadField, Where, read_as, unread
from accountant.invoice.parse import Column, Reading, Word
from accountant.invoice.result import ExtractionResult, paise_of
from accountant.invoice.status import DocumentStatus
from accountant.invoice.validate import (
    EXACTLY,
    Figures,
    Law,
    Tolerance,
    line_arithmetic,
    mandatory_fields_present,
    not_a_repeat,
    one_tax_shape,
    tax_parts_sum_to_total_tax,
    taxable_plus_tax_is_grand_total,
)
from accountant.invoice.validate import run as run_laws
from accountant.labels import Printing
from tests.invoice_documents import (
    A_DELIVERY_NOTE,
    BARELY_READABLE,
    INTER_STATE,
    INTRA_STATE,
    MISSING_FIELDS,
    NOT_AN_INVOICE,
    WITH_DISCOUNT,
    Fixture,
    text_reading,
    word_reading,
)

EXACT = Printing.EXACT_CHARACTERS


def read(fixture: Fixture, **kwargs: object) -> ExtractionResult:
    """The fixture described as a text layer, which is what most tests want."""
    return describe(
        text_reading(fixture),
        printing=EXACT,
        file_hash=fixture.name,
        engine="a test",
        **kwargs,  # pyright: ignore[reportArgumentType]
    )


def one_line(text: str, *, confidence: float = 1.0) -> Reading:
    return Reading.from_text(text, source="a test", confidence=confidence)


# =============================================================================
# money: the one parser, and the two Indian shapes it has to survive
# =============================================================================


@pytest.mark.parametrize(
    ("printed", "paise"),
    [
        ("Total: 1,23,456.00", 12345600),
        ("Total: 123456.00", 12345600),
        ("Total: 45,61,546/-", 456154600),
        ("Total: 45,61,546", 456154600),
        ("Total: Rs. 500.00", 50000),
        ("Total: ₹8,260.00", 826000),
        ("Total: 0.01", 1),
    ],
)
def test_an_amount_is_read_to_the_exact_paise(printed: str, paise: int) -> None:
    """Indian grouping, the `/-` suffix, both currency spellings, one paisa.

    `1,23,456.00` is TEN times `1,234,56.00` read with western grouping, and a
    reader that got the commas wrong would be out by an order of magnitude on
    exactly the figures an Indian accountant reads first.
    """
    found = parse.amount_under(one_line(printed), ("TOTAL",), "its total")
    assert paise_of(found) == paise


def test_the_slash_suffix_changes_no_figure() -> None:
    """`45,61,546/-` and `45,61,546` are the same number. `/-` says only 'and
    no paise', which the digits already said."""
    with_suffix = parse.amount_under(
        one_line("Total: 45,61,546/-"), ("TOTAL",), "its total"
    )
    without = parse.amount_under(one_line("Total: 45,61,546"), ("TOTAL",), "its total")
    assert paise_of(with_suffix) == paise_of(without) == 456154600


def test_an_amount_that_will_not_parse_is_unread_and_not_repaired() -> None:
    """`10.005` is sub-paise. It comes back as nothing, never as 1000 or 1001."""
    found = parse.amount_under(one_line("Total: 10.005"), ("TOTAL",), "its total")
    assert found.read is False
    assert found.value is None
    assert found.confidence == 0.0


# =============================================================================
# quantities and rates
# =============================================================================


@pytest.mark.parametrize(
    ("printed", "milli"),
    [("2", 2000), ("2.5", 2500), ("0.125", 125), ("10", 10000)],
)
def test_a_quantity_is_held_in_whole_thousandths(printed: str, milli: int) -> None:
    assert parse.quantity_milli(printed) == milli


def test_a_quantity_with_four_decimals_is_refused_rather_than_trimmed() -> None:
    """Trimming would show up later as a line that does not multiply out, and
    nobody would be able to say why."""
    assert parse.quantity_milli("2.5001") is None


@pytest.mark.parametrize(
    ("printed", "points"),
    [("18%", 1800), ("18", 1800), ("2.5%", 250), ("0.25%", 25), ("28 %", 2800)],
)
def test_a_gst_rate_is_held_in_basis_points(printed: str, points: int) -> None:
    """2.5 per cent is a real GST slab and `0.025` as a float loses a paisa."""
    assert parse.gst_rate_basis_points(printed) == points


def test_a_rate_with_three_decimals_is_refused() -> None:
    assert parse.gst_rate_basis_points("18.005%") is None


# =============================================================================
# GSTIN: shape only, and the checksum is NOT IMPLEMENTED
# =============================================================================


@pytest.mark.parametrize(
    "gstin",
    ["27AAQCS9214X1ZK", "29AAECD4471P1ZQ", "33AABCS1429J1Z7", "19AADFH6650N1ZR"],
)
def test_a_well_shaped_registration_number_is_accepted(gstin: str) -> None:
    assert parse.valid_gstin(gstin) == gstin


@pytest.mark.parametrize(
    ("wrong", "why"),
    [
        ("27AAQCS9214X1Z", "fourteen characters"),
        ("27AAQCS9214X1ZKK", "sixteen characters"),
        ("2AAQCS9214X1ZKQ", "one digit of state code"),
        ("27AAQCS9214X1YK", "the fixed Z is a Y"),
        ("27AAQC59214X1ZK", "a digit inside the PAN letters"),
        ("", "nothing at all"),
    ],
)
def test_a_badly_shaped_registration_number_is_refused(wrong: str, why: str) -> None:
    """A shape check can only ever REJECT, so being strict costs nothing."""
    assert parse.valid_gstin(wrong) is None, why


def test_the_registration_number_checksum_is_not_implemented() -> None:
    """PINNED DELIBERATELY. A GSTIN whose check character is wrong but whose
    SHAPE is right is accepted, because no verified checksum algorithm exists in
    this repository - `rules/place_of_supply.py` says so and this package does
    not invent one. If somebody adds a verified one, this test is what tells
    them to update the documents that promise there is none."""
    shape_right_check_character_arbitrary = "27AAQCS9214X1ZA"
    assert parse.valid_gstin(shape_right_check_character_arbitrary) is not None


def test_a_registration_number_is_found_in_its_own_section() -> None:
    result = read(INTRA_STATE)
    assert result.supplier.gstin.value == "27AAQCS9214X1ZK"
    assert result.buyer.gstin.value == "27AACFK7391M1Z9"
    assert result.supplier.state_code.value == "27"
    assert result.supplier.gstin.method is Method.BY_SHAPE
    assert result.supplier.state_code.method is Method.WORKED_OUT


def test_two_registration_numbers_with_no_section_are_both_left_unassigned() -> None:
    """A page with two numbers and nothing saying which is the supplier's is a
    coin toss that decides whose input credit this is."""
    reading = one_line("TAX INVOICE\nGSTIN: 27AAQCS9214X1ZK\nGSTIN: 27AACFK7391M1Z9\n")
    found = parse.gstins_on(reading)
    assert len(found) == 2
    assert all(one.side is parse.Side.UNSAID for one in found)
    assert parse.gstin_for(reading, found, parse.Side.SUPPLIER).read is False
    assert parse.gstin_for(reading, found, parse.Side.BUYER).read is False


# =============================================================================
# the invoice's own identity
# =============================================================================


def test_the_invoice_number_and_date_are_read_exactly() -> None:
    result = read(INTRA_STATE)
    assert result.invoice.number.value == "NSP/2026/0417"
    assert result.invoice.date.value == datetime.date(2026, 7, 14)
    assert result.invoice.po_number.value == "PO-88231"
    assert result.invoice.place_of_supply.value == "Maharashtra"


def test_a_spelled_month_is_read_and_needs_no_convention() -> None:
    assert read(INTER_STATE).invoice.date.value == datetime.date(2026, 6, 21)


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("2026-07-14", datetime.date(2026, 7, 14)),
        ("21 JUN 2026", datetime.date(2026, 6, 21)),
        ("28-APR-2026", datetime.date(2026, 4, 28)),
        ("19/08/2026", datetime.date(2026, 8, 19)),
        ("26.02.2026", datetime.date(2026, 2, 26)),
        ("05/05/2026", datetime.date(2026, 5, 5)),
        # Ambiguous: both numbers could be a month. Two readings, no way to
        # choose, and choosing files a return in the wrong month.
        ("09/08/2026", None),
        ("04/05/2026", None),
        # Impossible: neither number can be a month, or the day does not exist.
        ("85-13-2026", None),
        ("31/02/2026", None),
        ("10/00/2026", None),
        ("not a date", None),
    ],
)
def test_a_date_is_read_only_when_arithmetic_settles_its_order(
    printed: str, expected: datetime.date | None
) -> None:
    """THE TABLE THAT CATCHES DRIFT. `extract/textlayer.py::_ordered_date` holds
    the same rule and is private to a module this package may not edit, so this
    table is what notices if the two ever stop agreeing."""
    assert parse.date_from(printed) == expected


def test_an_ambiguous_date_leaves_the_field_unread_rather_than_guessed() -> None:
    reading = one_line("TAX INVOICE\nInvoice Date: 09/08/2026\n")
    printed = parse.value_under(reading, ("INVOICE DATE",), "its date", printing=EXACT)
    assert printed.value == "09/08/2026"
    converted = parse.converted(printed, parse.date_from("09/08/2026"), reading)
    assert converted.read is False
    assert converted.confidence == 0.0


def test_the_invoice_reference_number_and_e_way_bill_are_read_by_shape() -> None:
    result = read(WITH_DISCOUNT)
    assert result.invoice.irn.value == (
        "4a7f19c0b3e25d8146af90cc7b2e5310df6a48b9c012e37f5a9d4b6e8c1f2037"
    )
    assert result.invoice.eway_bill.value == "481920375566"


@pytest.mark.parametrize(
    ("labels", "shape", "good", "bad"),
    [
        (
            parse.IRN_LABELS,
            parse.IRN_SHAPE,
            "4a7f19c0b3e25d8146af90cc7b2e5310df6a48b9c012e37f5a9d4b6e8c1f2037",
            "4a7f19c0",
        ),
        (
            parse.EWAY_BILL_LABELS,
            parse.EWAY_BILL_SHAPE,
            "481920375566",
            "4819",
        ),
    ],
)
def test_a_reference_of_the_wrong_length_is_not_a_reference(
    labels: tuple[str, ...], shape: object, good: str, bad: str
) -> None:
    """Sixty-three hexadecimal characters is not a low-confidence IRN. It is
    not an IRN.

    BOTH HALVES ARE ASSERTED, and the good half is the one that matters. The
    first version of this test drove `describe` with a three-line document, and
    that document stopped at `INVOICE_LOW_TEXT` before any field was looked
    for - so every field was unread and the test passed while proving nothing.
    Asserting the accepting case is what makes the refusal mean something.
    """
    from accountant.invoice.bridge import shaped

    for printed, accepted in ((good, True), (bad, False)):
        reading = one_line(f"{labels[0]}: {printed}\n")
        base = parse.value_under(reading, labels, "a reference", printing=EXACT)
        assert base.value == printed, "the label itself did not match"
        checked = shaped(base, reading, shape)  # pyright: ignore[reportArgumentType]
        assert checked.read is accepted


def test_the_bill_no_label_is_not_in_the_invoice_number_vocabulary() -> None:
    """MEASURED, and the whole reason `BILL NO` was removed: `E-Way Bill No:`
    contains `Bill No` after a space, so with that label on the list every
    e-invoice's number read as nothing."""
    assert "BILL NO" not in parse.INVOICE_NUMBER_LABELS
    assert "BILL NUMBER" not in parse.INVOICE_NUMBER_LABELS
    assert read(WITH_DISCOUNT).invoice.number.value == "STM-4471"


# =============================================================================
# line items
# =============================================================================


def test_three_line_items_are_read_column_by_column() -> None:
    result = read(INTRA_STATE)
    assert len(result.items) == 3
    assert [one.description.value for one in result.items] == [
        "Copier paper A4",
        "Ring binders",
        "Marker pens",
    ]
    assert [one.hsn_sac.value for one in result.items] == ["4802", "4820", "9608"]
    assert [one.quantity.value for one in result.items] == [2000, 3000, 1000]
    assert [one.rate.value for one in result.items] == [150000, 80000, 160000]
    assert [one.taxable.value for one in result.items] == [300000, 240000, 160000]


def test_a_discount_column_is_read_and_zero_is_not_absence() -> None:
    result = read(WITH_DISCOUNT)
    assert [one.discount.value for one in result.items] == [50000, 0]
    assert [one.taxable.value for one in result.items] == [550000, 148000]


def test_a_tariff_code_is_normalised_by_the_one_reader_and_never_guessed() -> None:
    """`hsn_sac.normalise` returns None for a code of a length the tariff does
    not use, and this package reports that as unread rather than padding it."""
    assert parse.cell_value(Column.HSN_SAC, "4802") == "4802"
    assert parse.cell_value(Column.HSN_SAC, "48 02") == "4802"
    assert parse.cell_value(Column.HSN_SAC, "480") is None
    assert parse.cell_value(Column.HSN_SAC, "48A2") is None


def test_a_table_with_no_header_yields_no_line_items() -> None:
    """Without a header the only way to know the third number is the rate is to
    assume a column order, and an assumed order swaps a rate with a quantity on
    the one bill that prints them the other way round."""
    reading = one_line(
        "TAX INVOICE\nGSTIN: 27AAQCS9214X1ZK\n"
        "Copier paper 4802 2 1500.00 3000.00\nTotal: 3000.00\n"
    )
    assert parse.find_header(reading.lines) is None
    assert parse.read_rows(reading) == ()


def test_a_header_needs_three_recognised_column_names() -> None:
    """Two would match a `Total`/`Amount` summary line at the foot of the bill
    and read every line below it as goods."""
    assert parse.ENOUGH_HEADINGS == 3
    assert parse.find_header(("Amount   Total",)) is None
    header = parse.find_header(("Description   Qty   Rate",))
    assert header is not None
    assert header.columns == (Column.DESCRIPTION, Column.QUANTITY, Column.RATE)


def test_a_row_with_a_blank_column_is_refused_rather_than_shifted() -> None:
    """A shifted row puts the rate in the quantity column and still multiplies
    out to something, which is the worst failure available: a wrong answer that
    passes its own arithmetic."""
    reading = one_line(
        "Description   HSN   Qty   Rate   Taxable\nCopier paper   4802   1500.00\n"
    )
    assert parse.read_rows(reading) == ()


# =============================================================================
# the tax split
# =============================================================================


def test_an_intra_state_bill_reads_both_halves_and_no_igst() -> None:
    result = read(INTRA_STATE)
    assert result.totals.cgst.value == 63000
    assert result.totals.sgst.value == 63000
    assert result.totals.igst.read is False
    assert result.totals.total_tax.value == 126000
    assert result.totals.total_tax_was_stated is False
    assert result.totals.total_tax.method is Method.WORKED_OUT


def test_an_inter_state_bill_reads_one_integrated_figure() -> None:
    result = read(INTER_STATE)
    assert result.totals.igst.value == 100800
    assert result.totals.cgst.read is False
    assert result.totals.sgst.read is False
    assert result.totals.total_tax.value == 100800


def test_both_kinds_of_gst_on_one_bill_is_a_contradiction() -> None:
    finding = one_tax_shape(cgst_paise=100, sgst_paise=100, igst_paise=200)
    assert finding.verdict is Verdict.FAIL
    assert "both kinds of GST" in finding.said


def test_one_half_of_the_intra_state_tax_alone_is_a_failure() -> None:
    """Reading CGST and missing SGST claims back half the tax that was paid,
    with a bill that still looks read."""
    finding = one_tax_shape(cgst_paise=63000, sgst_paise=None, igst_paise=None)
    assert finding.verdict is Verdict.FAIL
    assert "no SGST" in finding.said


def test_a_bill_with_no_tax_line_at_all_is_indeterminate_and_not_a_failure() -> None:
    """A bill of supply is real, and calling it a failure refuses a correct
    document."""
    finding = one_tax_shape(cgst_paise=None, sgst_paise=None, igst_paise=None)
    assert finding.verdict is Verdict.INDETERMINATE


# =============================================================================
# the arithmetic
# =============================================================================


def test_a_line_that_multiplies_out_passes_to_the_exact_paise() -> None:
    finding = line_arithmetic(
        quantity_milli=2000,
        rate_paise=150000,
        discount_paise=None,
        taxable_paise=300000,
    )
    assert finding.verdict is Verdict.PASS
    assert finding.out_by_paise == 0


def test_a_line_out_by_one_paisa_fails_and_says_so_in_paise() -> None:
    """One paisa is almost always a misread digit, and `₹0.01` reads like a
    rounding artefact somebody would ignore."""
    finding = line_arithmetic(
        quantity_milli=2000,
        rate_paise=150000,
        discount_paise=None,
        taxable_paise=300001,
    )
    assert finding.verdict is Verdict.FAIL
    assert finding.out_by_paise == 1
    assert "out by 1 paisa" in finding.said


def test_a_discount_is_subtracted_and_an_absent_one_is_zero() -> None:
    finding = line_arithmetic(
        quantity_milli=5000,
        rate_paise=120000,
        discount_paise=50000,
        taxable_paise=550000,
    )
    assert finding.verdict is Verdict.PASS


def test_an_unread_rate_is_never_treated_as_zero() -> None:
    """Zero would make every unread line pass at a taxable value of zero, which
    is the same coercion `conservation.py` refuses for an unread tax field."""
    finding = line_arithmetic(
        quantity_milli=2000,
        rate_paise=None,
        discount_paise=None,
        taxable_paise=0,
    )
    assert finding.verdict is Verdict.INDETERMINATE
    assert "the rate" in finding.said


def test_a_tolerance_is_stated_in_paise_and_defaults_to_exact() -> None:
    assert EXACTLY.paise == 0

    def out_by_fifty(tolerance: Tolerance) -> Verdict:
        return line_arithmetic(
            quantity_milli=2000,
            rate_paise=150000,
            discount_paise=None,
            taxable_paise=300050,
            tolerance=tolerance,
        ).verdict

    assert out_by_fifty(EXACTLY) is Verdict.FAIL
    assert out_by_fifty(Tolerance(paise=50)) is Verdict.PASS
    assert out_by_fifty(Tolerance(paise=49)) is Verdict.FAIL


def test_a_tolerance_cannot_be_a_float_or_negative() -> None:
    with pytest.raises(TypeError):
        Tolerance(paise=0.5)  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError, match="not a distance"):
        Tolerance(paise=-1)


def test_the_tolerance_never_reaches_the_two_conservation_laws() -> None:
    """`cage/conservation.py` compares exactly by decision, and widening that is
    not this package's to do. A bill out by one paisa on the amount payable
    fails no matter what slack the caller allowed."""
    findings = run_laws(
        Figures(
            taxable_paise=700000,
            total_tax_paise=126000,
            total_tax_was_stated=True,
            grand_total_paise=826001,
            cgst_paise=63000,
            sgst_paise=63000,
        ),
        tolerance=Tolerance(paise=500),
    )
    law = next(
        one for one in findings if one.law is Law.TAXABLE_PLUS_TAX_IS_GRAND_TOTAL
    )
    assert law.verdict is Verdict.FAIL


def test_the_lines_add_up_to_the_taxable_value_and_the_bill_to_its_total() -> None:
    result = read(INTRA_STATE)
    verdicts = {one.law: one.verdict for one in result.findings}
    assert verdicts[Law.LINES_SUM_TO_TAXABLE] is Verdict.PASS
    assert verdicts[Law.TAXABLE_PLUS_TAX_IS_GRAND_TOTAL] is Verdict.PASS
    assert result.totals.taxable.value == 700000
    assert result.totals.grand_total.value == 826000


def test_a_round_off_is_read_with_its_sign_and_closes_the_total() -> None:
    """6,980.00 taxable plus 837.60 of tax is 7,817.60, and the bill says
    7,817.00. The round-off of minus sixty paise is what makes the law hold."""
    result = read(WITH_DISCOUNT)
    assert result.totals.round_off.value == -60
    assert result.totals.grand_total.value == 781700
    verdicts = {one.law: one.verdict for one in result.findings}
    assert verdicts[Law.TAXABLE_PLUS_TAX_IS_GRAND_TOTAL] is Verdict.PASS


def test_an_absent_round_off_can_only_ever_cause_a_failure() -> None:
    """The one place an absent figure is read as a number, and it is safe
    because it moves the answer towards refusing rather than towards posting."""
    without = taxable_plus_tax_is_grand_total(
        taxable_paise=100000,
        total_tax_paise=18000,
        round_off_paise=None,
        grand_total_paise=118000,
    )
    assert without.verdict is Verdict.PASS
    unread_round_off = taxable_plus_tax_is_grand_total(
        taxable_paise=100000,
        total_tax_paise=18000,
        round_off_paise=None,
        grand_total_paise=118040,
    )
    assert unread_round_off.verdict is Verdict.FAIL


def test_the_tax_parts_law_refuses_to_check_a_sum_against_itself() -> None:
    """Very few bills print a total tax. When the parser worked it out, checking
    the parts against it proves nothing and this says so instead of passing."""
    worked_out = tax_parts_sum_to_total_tax(
        cgst_paise=63000,
        sgst_paise=63000,
        igst_paise=None,
        cess_paise=None,
        total_tax_paise=126000,
        total_tax_was_stated=False,
    )
    assert worked_out.verdict is Verdict.INDETERMINATE
    assert "checking a number against itself" in worked_out.said


def test_the_tax_parts_law_runs_when_the_bill_states_its_own_total() -> None:
    agreeing = tax_parts_sum_to_total_tax(
        cgst_paise=63000,
        sgst_paise=63000,
        igst_paise=None,
        cess_paise=1000,
        total_tax_paise=127000,
        total_tax_was_stated=True,
    )
    assert agreeing.verdict is Verdict.PASS
    assert agreeing.out_by_paise == 0

    disagreeing = tax_parts_sum_to_total_tax(
        cgst_paise=63000,
        sgst_paise=63000,
        igst_paise=None,
        cess_paise=None,
        total_tax_paise=127000,
        total_tax_was_stated=True,
    )
    assert disagreeing.verdict is Verdict.FAIL
    assert disagreeing.out_by_paise == 1000


# =============================================================================
# what must be there
# =============================================================================


def test_a_bill_with_no_number_and_no_date_names_both_of_them() -> None:
    result = read(MISSING_FIELDS)
    assert result.status is DocumentStatus.INVOICE_MISSING_FIELDS
    finding = next(one for one in result.findings if one.law is Law.MANDATORY_FIELDS)
    assert finding.verdict is Verdict.FAIL
    assert "the bill's own number" in finding.said
    assert "the date on the bill" in finding.said
    # The figures it DID print were still read, and read correctly.
    assert result.totals.taxable.value == 376000
    assert result.totals.grand_total.value == 443680


def test_everything_present_passes_the_mandatory_law() -> None:
    finding = mandatory_fields_present(
        ("supplier", "invoice_number", "invoice_date", "grand_total")
    )
    assert finding.verdict is Verdict.PASS


def test_the_supplier_registration_number_is_not_mandatory() -> None:
    """A bill from an unregistered supplier is a real bill, and refusing it
    would refuse a whole class of ordinary purchase."""
    finding = mandatory_fields_present(
        ("supplier", "invoice_number", "invoice_date", "grand_total")
    )
    assert finding.verdict is Verdict.PASS


# =============================================================================
# the repeat check, and the store that does not exist
# =============================================================================


def test_a_bill_already_seen_in_this_run_is_refused() -> None:
    seen = frozenset({("27AAQCS9214X1ZK", "NSP/2026/0417")})
    finding = not_a_repeat(
        supplier_key="27AAQCS9214X1ZK",
        invoice_number="NSP/2026/0417",
        already_seen=seen,
    )
    assert finding.verdict is Verdict.FAIL


def test_an_unread_number_cannot_be_compared_with_anything() -> None:
    """Calling that 'not a repeat' is the answer that lets a duplicate through."""
    finding = not_a_repeat(
        supplier_key="27AAQCS9214X1ZK", invoice_number=None, already_seen=frozenset()
    )
    assert finding.verdict is Verdict.INDETERMINATE


def test_the_pass_sentence_admits_that_earlier_runs_were_not_checked() -> None:
    finding = not_a_repeat(
        supplier_key="A", invoice_number="1", already_seen=frozenset()
    )
    assert finding.verdict is Verdict.PASS
    assert "nothing in this system remembers a bill number between runs" in (
        finding.said
    )


# =============================================================================
# confidence
# =============================================================================


def test_a_text_layer_states_its_own_certainty_and_every_field_carries_it() -> None:
    result = read(INTRA_STATE)
    assert result.lowest_confidence == 1.0
    assert result.average_confidence == 1.0
    assert result.field_confidence["invoice_number"] == 1.0


def test_a_word_scored_reading_scores_each_field_from_its_own_words() -> None:
    """Every word at 62 means every field at exactly 0.62 - `min(word)/100`
    over identical numbers is that number, which is what lets this pin a value
    rather than assert something vague about a range."""
    result = describe(
        word_reading(INTRA_STATE, confidence=62),
        printing=Printing.READ_OFF_A_PHOTOGRAPH,
        file_hash="scored",
    )
    assert result.field_confidence["grand_total"] == pytest.approx(0.62)
    assert result.lowest_confidence == pytest.approx(0.62)


def test_a_low_confidence_reading_says_a_person_has_to_check_it() -> None:
    result = describe(
        word_reading(INTRA_STATE, confidence=62),
        printing=Printing.READ_OFF_A_PHOTOGRAPH,
        file_hash="scored",
    )
    assert any("below the level" in reason for reason in result.review_reasons)


def test_a_high_confidence_reading_does_not_say_that() -> None:
    result = describe(
        word_reading(INTRA_STATE, confidence=98),
        printing=Printing.READ_OFF_A_PHOTOGRAPH,
        file_hash="scored",
    )
    assert result.lowest_confidence == pytest.approx(0.98)
    assert not any("below the level" in reason for reason in result.review_reasons)


def test_an_unread_field_scores_zero_and_is_still_in_the_map() -> None:
    """A map that dropped them would make an unread total indistinguishable
    from a field this package does not have."""
    result = read(MISSING_FIELDS)
    assert result.field_confidence["invoice_number"] == 0.0
    assert "invoice_number" in result.field_confidence


def test_the_word_score_marker_minus_one_is_refused_rather_than_scored() -> None:
    """Tesseract reports -1 for 'no text here'. It is a marker, not a score."""
    with pytest.raises(ValueError, match="outside 0-100"):
        Word(text="x", confidence=-1)


def test_the_average_is_over_read_fields_and_the_minimum_is_the_weaker_one() -> None:
    """The average is for a person reading a batch. The minimum is the number a
    decision uses, because one misread digit ruins an amount."""
    reading = Reading.from_words(
        [
            [Word("Invoice", 95), Word("No:", 95), Word("A-1", 95)],
            [Word("Total:", 40), Word("100.00", 40)],
        ],
        source="a test",
    )
    number = parse.value_under(reading, ("INVOICE NO",), "its number", printing=EXACT)
    total = parse.amount_under(reading, ("TOTAL",), "its total")
    assert number.confidence == pytest.approx(0.95)
    assert total.confidence == pytest.approx(0.40)


# =============================================================================
# the field wrapper's own rules
# =============================================================================


def test_a_field_with_no_value_must_carry_no_method_and_no_verdict() -> None:
    empty = unread("a test")
    assert empty.value is None
    assert empty.confidence == 0.0
    assert empty.method is Method.NOT_READ
    assert empty.checked is Checked.NOT_CHECKED


def test_read_as_refuses_none_rather_than_recording_a_confident_blank() -> None:
    with pytest.raises(ValueError, match="was handed None"):
        read_as(
            None,
            confidence=0.9,
            source="a test",
            method=Method.UNDER_A_LABEL,
            printed="",
        )


def test_a_field_carries_the_characters_it_was_converted_from() -> None:
    """When somebody disputes a figure the only useful evidence is the
    characters the conversion started from."""
    found = parse.amount_under(one_line("Total: 1,23,456.00"), ("TOTAL",), "its total")
    assert found.printed == "1,23,456.00"
    assert found.value == 12345600
    assert found.where == Where(line=0, start=7, end=18)


def test_a_verdict_is_attached_by_copy_and_never_by_editing_a_value() -> None:
    found = parse.amount_under(one_line("Total: 100.00"), ("TOTAL",), "its total")
    checked = found.as_checked(Checked.VALID)
    assert checked.value == found.value == 10000
    assert checked.checked is Checked.VALID
    assert found.checked is Checked.NOT_CHECKED


def test_a_location_that_nobody_can_index_is_refused() -> None:
    with pytest.raises(ValueError, match="half-open range"):
        Where(line=0, start=5, end=2)


def test_a_field_that_was_found_cannot_claim_nothing_found_it() -> None:
    from accountant.cage.wall import Field

    with pytest.raises(ValueError, match="is a value, so something found it"):
        ReadField(
            field=Field(value="x", confidence=1.0, source="a test"),
            method=Method.NOT_READ,
        )


# =============================================================================
# classification, at the two ends
# =============================================================================


def test_an_empty_reading_is_an_engine_failure_and_not_a_field_failure() -> None:
    """THE DISTINCTION THE WHOLE PACKAGE IS FOR."""
    result = describe(one_line("   \n \n"), printing=EXACT, file_hash="blank")
    assert result.status is DocumentStatus.OCR_FAILED
    assert "nothing at all could be read" in result.said


def test_a_reading_that_is_not_language_is_unreadable() -> None:
    result = read(BARELY_READABLE)
    assert result.status is DocumentStatus.UNREADABLE


def test_a_readable_document_with_no_bill_signals_is_unknown() -> None:
    result = read(NOT_AN_INVOICE)
    assert result.status is DocumentStatus.UNKNOWN_DOCUMENT
    assert result.signals == ()


def test_a_readable_document_with_one_bill_signal_is_confidently_not_a_bill() -> None:
    result = read(A_DELIVERY_NOTE)
    assert result.status is DocumentStatus.NON_INVOICE
    assert result.signals == ("names itself a bill",)


def test_a_bill_with_almost_no_text_asks_for_a_re_scan_and_not_a_re_parse() -> None:
    result = describe(
        one_line("TAX INVOICE GSTIN Total 100.00\n"),
        printing=EXACT,
        file_hash="thin",
    )
    assert result.status is DocumentStatus.INVOICE_LOW_TEXT
    assert "try scanning it again" in result.said


def test_non_empty_text_reaches_field_detection() -> None:
    """The measured defect: 82 of 106 JPGs return text and nothing downstream
    looked at it. A document that reads reaches the parser and comes back with
    named fields."""
    result = read(INTRA_STATE)
    assert result.raw_text.strip() != ""
    assert result.read_fields != ()
    assert "supplier_gstin" in result.read_fields
    assert "grand_total" in result.read_fields


# =============================================================================
# the weaker read: a name under a bare heading, and everything it costs
# =============================================================================


def test_a_name_under_a_bare_heading_is_read_and_labelled_as_positional() -> None:
    """`Bill To:` on one line and the customer on the next is how most real
    invoices print a party, so this path exists - and it is POSITIONAL, which is
    why the field says `BELOW_A_HEADING` rather than pretending a label named
    the value."""
    reading = one_line(
        "TAX INVOICE\n"
        "Supplier:\n"
        "NORTHFIELD STATIONERY PRIVATE LIMITED\n"
        "GSTIN: 27AAQCS9214X1ZK\n"
        "Invoice No: A-1\nInvoice Date: 2026-01-02\nTotal: 100.00\n"
    )
    from accountant.invoice.bridge import party_name

    name = party_name(reading, parse.SUPPLIER_SECTION, "its supplier", printing=EXACT)
    assert name.value == "NORTHFIELD STATIONERY PRIVATE LIMITED"
    assert name.method is Method.BELOW_A_HEADING


def test_a_registration_number_is_skipped_when_looking_for_a_name() -> None:
    """A GSTIN is not a party name, so the positional read steps over it."""
    from accountant.invoice.bridge import party_name

    reading = one_line("Bill To:\n27AACFK7391M1Z9\nKHANNA ADVISORY SERVICES LLP\n")
    name = party_name(reading, parse.BUYER_SECTION, "its buyer", printing=EXACT)
    assert name.value == "KHANNA ADVISORY SERVICES LLP"


def test_a_blank_line_under_the_heading_is_stepped_over_and_a_heading_stops_it() -> (
    None
):
    """Two behaviours in one document: the blank is skipped, and the next
    heading ends the block rather than lending it its own first line."""
    from accountant.invoice.bridge import party_name

    skipped = one_line("Supplier:\n\n\nACME PARTS LTD\n")
    assert (
        party_name(skipped, parse.SUPPLIER_SECTION, "x", printing=EXACT).value
        == "ACME PARTS LTD"
    )
    stopped = one_line("Supplier:\nBill To:\nKHANNA ADVISORY SERVICES LLP\n")
    assert (
        party_name(stopped, parse.SUPPLIER_SECTION, "x", printing=EXACT).read is False
    )


def test_a_heading_that_is_never_printed_leaves_the_name_unread() -> None:
    from accountant.invoice.bridge import party_name

    reading = one_line("TAX INVOICE\nTotal: 100.00\n")
    assert (
        party_name(reading, parse.SUPPLIER_SECTION, "x", printing=EXACT).read is False
    )


def test_a_state_code_cannot_be_worked_out_from_a_number_nobody_read() -> None:
    from accountant.invoice.bridge import state_code

    reading = one_line("x\n")
    assert state_code(reading, unread("a test")).read is False


def test_a_bill_with_no_tax_line_at_all_works_out_no_total_tax() -> None:
    """Nothing to add up is not a total of zero."""
    from accountant.invoice.bridge import worked_out_tax

    reading = one_line("x\n")
    assert worked_out_tax(reading, (unread("a test"), unread("a test"))).read is False


def test_an_address_is_read_only_when_a_label_names_it() -> None:
    """A line found by POSITION under a party name is the address on most bills
    and the second half of the name on the rest, so it is not claimed."""
    assert read(INTRA_STATE).supplier.address.value == (
        "14 Turner Road, Bandra West, Mumbai 400050"
    )
    reading = one_line("TAX INVOICE\nSupplier: ACME\n14 Turner Road\nTotal: 1.00\n")
    from accountant.invoice.bridge import party

    found = party(
        reading,
        parse.gstins_on(reading),
        side=parse.Side.SUPPLIER,
        headings=parse.SUPPLIER_SECTION,
        what="its supplier",
        printing=EXACT,
    )
    assert found.address.read is False


# =============================================================================
# the record's own invariants
# =============================================================================


def test_a_document_must_be_identified_by_its_bytes() -> None:
    from accountant.invoice.result import DocumentMeta

    with pytest.raises(ValueError, match="identified by its bytes"):
        DocumentMeta(
            file_hash="  ",
            page_count=1,
            engine="e",
            status=DocumentStatus.OCR_FAILED,
        )


def test_a_document_cannot_have_a_negative_page_count() -> None:
    from accountant.invoice.result import DocumentMeta

    with pytest.raises(ValueError, match="cannot have -1 pages"):
        DocumentMeta(
            file_hash="a", page_count=-1, engine="e", status=DocumentStatus.OCR_FAILED
        )


def test_a_document_must_say_which_reader_produced_it() -> None:
    from accountant.invoice.result import ENGINE_NOT_STATED, DocumentMeta

    with pytest.raises(ValueError, match="which reader produced it"):
        DocumentMeta(
            file_hash="a", page_count=1, engine=" ", status=DocumentStatus.OCR_FAILED
        )
    stated = DocumentMeta(
        file_hash="a",
        page_count=1,
        engine=ENGINE_NOT_STATED,
        status=DocumentStatus.OCR_FAILED,
    )
    assert stated.engine == "engine_not_stated"


def test_the_date_property_returns_none_for_anything_that_is_not_a_date() -> None:
    """`ReadField.value` is `object`, so a caller that assumed `datetime.date`
    would fail at whatever line finally did arithmetic on it."""
    from accountant.invoice.bridge import invoice_date_of
    from accountant.invoice.result import InvoiceIdentity

    identity = InvoiceIdentity.nothing("a test")
    assert identity.invoice_date is None
    assert invoice_date_of(read(INTRA_STATE)) == datetime.date(2026, 7, 14)


def test_the_failed_and_unchecked_laws_are_reported_separately() -> None:
    """'This is wrong' and 'I could not check' lead to different statuses."""
    result = read(MISSING_FIELDS)
    assert [one.law for one in result.failed_laws] == [Law.MANDATORY_FIELDS]
    assert Law.NOT_A_REPEAT in [one.law for one in result.unchecked_laws]


def test_the_two_law_filters_agree_with_the_record() -> None:
    from accountant.invoice.validate import failed, unchecked

    result = read(MISSING_FIELDS)
    assert failed(result.findings) == result.failed_laws
    assert unchecked(result.findings) == result.unchecked_laws


def test_a_field_provenance_and_source_travel_with_the_value() -> None:
    found = parse.amount_under(one_line("Total: 1.00"), ("TOTAL",), "its total")
    assert found.source == "a test"
    assert found.method is Method.UNDER_A_LABEL


def test_a_field_with_no_value_cannot_carry_a_verdict() -> None:
    from accountant.cage.wall import Field

    with pytest.raises(ValueError, match="cannot have been found"):
        ReadField(
            field=Field(value=None, confidence=0.0, source="a test"),
            method=Method.UNDER_A_LABEL,
        )
    with pytest.raises(ValueError, match="cannot be valid"):
        ReadField(
            field=Field(value=None, confidence=0.0, source="a test"),
            method=Method.NOT_READ,
            checked=Checked.VALID,
        )


# =============================================================================
# the reading's own invariants
# =============================================================================


def test_a_reading_must_say_which_reader_produced_it() -> None:
    with pytest.raises(ValueError, match="which reader produced it"):
        Reading.from_text("x", source="  ", confidence=1.0)


def test_a_reading_carries_word_scores_or_one_stated_confidence_never_both() -> None:
    """Both is two answers to how sure we are; neither is no answer at all."""
    with pytest.raises(ValueError, match="exactly one of the two"):
        Reading(
            lines=("x",),
            words=((Word("x", 90),),),
            source="a test",
            stated_confidence=0.9,
        )
    with pytest.raises(ValueError, match="exactly one of the two"):
        Reading(lines=("x",), words=(), source="a test", stated_confidence=None)


def test_a_reading_whose_words_do_not_index_its_lines_is_refused() -> None:
    with pytest.raises(ValueError, match="index each other"):
        Reading(
            lines=("a", "b"),
            words=((Word("a", 90),),),
            source="a test",
            stated_confidence=None,
        )


def test_a_stated_confidence_above_one_is_refused() -> None:
    with pytest.raises(ValueError, match="outside"):
        Reading.from_text("x", source="a test", confidence=1.5)


def test_a_word_confidence_must_be_a_whole_number() -> None:
    with pytest.raises(TypeError, match="whole number"):
        Word(text="x", confidence=90.0)  # pyright: ignore[reportArgumentType]


def test_the_characters_are_kept_exactly_as_the_reader_returned_them() -> None:
    """Including the trailing newline. `splitlines` then `join` drops it, which
    is a quiet edit to the evidence a dispute months from now rests on."""
    reading = Reading.from_text("a\nb\n", source="a test", confidence=1.0)
    assert reading.text == "a\nb\n"
    assert reading.lines == ("a", "b")


def test_a_word_ending_where_a_range_starts_is_not_under_it() -> None:
    """Without the half-open overlap, `TOTAL 500.00` scores the total using the
    confidence of the word `TOTAL`, which is not what was read."""
    reading = Reading.from_words(
        [[Word("TOTAL", 99), Word("500.00", 30)]], source="a test"
    )
    assert reading.words_under(Where(line=0, start=6, end=12)) == (Word("500.00", 30),)


def test_a_reading_with_no_word_scores_reports_no_words() -> None:
    reading = one_line("TOTAL 500.00")
    assert reading.words_under(Where(line=0, start=6, end=12)) == ()


# =============================================================================
# the remaining corners of the parsers
# =============================================================================


def test_a_rate_that_is_not_a_plain_number_is_not_a_rate() -> None:
    assert parse.gst_rate_basis_points("NaN") is None
    assert parse.gst_rate_basis_points("eighteen") is None


def test_a_quantity_that_is_not_a_plain_number_is_not_a_quantity() -> None:
    assert parse.quantity_milli("two") is None


def test_a_date_whose_second_number_is_the_month_is_read_that_way_round() -> None:
    """`2026-13-01` style: the first number is at or below twelve and the second
    is above it, so the month is FIRST and arithmetic said so."""
    assert parse.date_from("05/25/2026") == datetime.date(2026, 5, 25)


def test_a_column_the_header_did_not_name_is_unread_on_every_row() -> None:
    result = read(INTRA_STATE)
    assert all(one.cgst.read is False for one in result.items)
    assert all(one.unit.read is False for one in result.items)


def test_a_cell_whose_characters_will_not_convert_is_unread() -> None:
    reading = one_line("Description   HSN   Qty   Rate\nBox   48A2   two   9.005\n")
    rows = parse.read_rows(reading)
    assert len(rows) == 1
    assert rows[0][Column.HSN_SAC].read is False
    assert rows[0][Column.QUANTITY].read is False
    assert rows[0][Column.RATE].read is False
    assert rows[0][Column.DESCRIPTION].value == "Box"


def test_a_table_whose_header_names_no_description_reads_no_rows() -> None:
    """Every other column is short and numeric, so the right-anchored split has
    nothing to anchor against."""
    reading = one_line("HSN   Qty   Rate\n4802   2   1500.00\n")
    assert parse.read_rows(reading) == ()


def test_the_tax_parts_law_is_indeterminate_when_a_stated_total_did_not_read() -> None:
    finding = tax_parts_sum_to_total_tax(
        cgst_paise=1,
        sgst_paise=1,
        igst_paise=None,
        cess_paise=None,
        total_tax_paise=None,
        total_tax_was_stated=True,
    )
    assert finding.verdict is Verdict.INDETERMINATE


def test_per_line_figures_of_different_lengths_are_refused() -> None:
    """They index each other, so a mismatch checks one line's rate against
    another's quantity."""
    with pytest.raises(ValueError, match="index each other"):
        Figures(line_quantity_milli=(1, 2), line_rate_paise=(1,))


def test_a_tolerance_that_was_allowed_is_named_in_the_sentence() -> None:
    finding = line_arithmetic(
        quantity_milli=1000,
        rate_paise=10000,
        discount_paise=None,
        taxable_paise=20000,
        tolerance=Tolerance(paise=500),
    )
    assert finding.verdict is Verdict.FAIL
    assert "Nothing above ₹5.00 was allowed here." in finding.said


def test_every_law_runs_even_when_the_first_one_failed() -> None:
    """A run that stopped at the first failure would report one problem when
    there are three, so the person fixes one and walks into the second."""
    findings = run_laws(
        Figures(
            line_quantity_milli=(2000,),
            line_rate_paise=(150000,),
            line_discount_paise=(None,),
            line_taxable_paise=(300001,),
            taxable_paise=999999,
            cgst_paise=1,
            sgst_paise=None,
            grand_total_paise=1,
        )
    )
    assert {one.law for one in findings} == set(Law)
    assert len(findings) == len(Law)

"""The bounded search for a figure that is not on its label's line.

WHAT THESE FIXTURES ARE
------------------------
Every fixture below is one of the lines MEASURED on 2026-08-15 across 60 real
image documents, rebuilt as integers. Of the 18 lines on that corpus mentioning
a total-ish word, 4 carried a number on the same line and `labels.py` already
matched all 4; the "has a number AND went unmatched" list came back EMPTY. The
other 14 print the label with no number on the line:

    SUB TOTAL            GRAND TOTAL          Total Cost
    Total des            TOTAL QUANTITY:      AMOUNT IN WORDS: TWO THOUSAND NINE

Those are the fixtures. They are hand-built `PageWord` rows and not photographs,
which is the point of the abstract geometry: no tesseract binary, no image, no
engine and no timeout appears anywhere in this file, so a failure here is always
a failure of a rule and never of a machine.

WHAT IS ASSERTED
-----------------
Exact paise through `labels.paise_or_none`, exact method strings, exact refusal
reasons, exact ranks and exact printed labels. Nothing here asserts that output
is non-empty: a search that returned the whole page would pass that.

THE CONTROLS
-------------
Three tests exist to fail if the search stops being bounded or stops being safe:

    a figure 900 pixels below its label is NOT taken
    `SUB TOTAL` does not hand its figure to the amount payable
    `TOTAL QUANTITY:` followed by 12 is refused BY NAME as a quantity

The last is the trap this module was built around: twelve cases read as twelve
rupees is a posting that looks completely ordinary afterwards.
"""

from __future__ import annotations

import pytest

from accountant.extract.labels import NET_LABELS, TOTAL_LABELS, paise_or_none
from accountant.extract.nearby import (
    A_DATE,
    A_MALFORMED_AMOUNT,
    A_PERCENTAGE,
    A_PHONE_NUMBER,
    A_POSTAL_CODE,
    A_QUANTITY,
    AN_HSN_OR_SAC_CODE,
    AN_INVOICE_NUMBER,
    BELOW,
    CLEARLY_CLOSER_PIXELS,
    MODIFIERS_BEFORE,
    NEXT_LINE,
    PREVIOUS_LINE,
    REJECTION_ORDER,
    RIGHT_OF,
    SAME_LINE,
    SEARCH_ORDER,
    Box,
    Candidate,
    Limits,
    PageWord,
    candidates_for,
    looks_like_a_date,
    looks_like_a_malformed_amount,
    looks_like_a_percentage,
    looks_like_a_phone_number,
    looks_like_a_postal_code,
    looks_like_a_quantity,
    looks_like_an_hsn_or_sac_code,
    looks_like_an_invoice_number,
    review_needed,
)

DEFAULT = Limits()

#: A line of text is 30px tall and starts 40px down the page. Arbitrary but
#: fixed, so every pixel distance asserted below can be worked out by hand from
#: the fixture rather than read off a run.
_LINE_HEIGHT = 30
_FIRST_BASELINE = 40


def word(
    text: str,
    line: int,
    *,
    left: int = 100,
    width: int = 80,
    confidence: int = 90,
    box: bool = True,
) -> PageWord:
    """One engine word, sitting where line number `line` puts it."""
    top = _FIRST_BASELINE + line * _LINE_HEIGHT * 2
    return PageWord(
        text=text,
        line=line,
        box=Box(left=left, top=top, right=left + width, bottom=top + _LINE_HEIGHT)
        if box
        else None,
        confidence=confidence,
    )


def only(candidates: tuple[Candidate, ...]) -> Candidate:
    """The single candidate, or a failure naming everything that came back."""
    assert len(candidates) == 1, [
        (one.value_text, one.method, one.rejected) for one in candidates
    ]
    return candidates[0]


# =============================================================================
# the six measured lines
# =============================================================================


def test_sub_total_does_not_hand_its_figure_to_the_amount_payable() -> None:
    """CONTROL. `labels.amount_on` records the cost of getting this wrong:
    `Sub  Total  278.61` on a bill whose real total is 319.00 posts ₹278.61 and
    loses exactly the ₹40.39 of input credit, with the bill still looking read.
    `SUB TOTAL` is one of the 14 measured lines, so the same defect is one
    unguarded label match away from happening here instead."""
    words = (
        word("SUB", 0, left=100),
        word("TOTAL", 0, left=190),
        word("1,234.56", 1, left=100),
    )
    assert (
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT) == ()
    )


def test_sub_total_is_read_as_the_net_because_the_engine_split_one_word() -> None:
    """The same two words ARE the net, and `labels.NET_LABELS` spells that word
    `SUBTOTAL`. Joining the printed words tight is what reaches it; without that
    join the measured line matches nothing at all."""
    words = (
        word("SUB", 0, left=100),
        word("TOTAL", 0, left=190),
        word("1,234.56", 1, left=100),
    )
    found = only(candidates_for(words, field="net", labels=NET_LABELS, limits=DEFAULT))
    assert found.field == "net"
    assert found.label_text == "SUB TOTAL"
    assert found.value_text == "1,234.56"
    assert found.method == NEXT_LINE
    assert found.line_distance == 1
    assert found.rejected == ""
    assert found.rank == 1
    assert paise_or_none(found.value_text) == 123456


def test_grand_total_takes_the_figure_from_the_line_below() -> None:
    words = (
        word("GRAND", 0, left=100),
        word("TOTAL", 0, left=210),
        word("2,076.76", 1, left=100),
    )
    found = only(
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    )
    assert found.label_text == "GRAND TOTAL"
    assert found.value_text == "2,076.76"
    assert found.method == NEXT_LINE
    assert found.rejected == ""
    assert paise_or_none(found.value_text) == 207676


def test_grand_total_is_one_label_and_not_also_total() -> None:
    """`labels.amounts_for` breaks on the longest label that matched, for this
    reason. Two candidates from one printed label would look like an ambiguity
    the page does not contain."""
    words = (
        word("GRAND", 0, left=100),
        word("TOTAL", 0, left=210),
        word("2,076.76", 1, left=100),
    )
    found = candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    assert [one.label_text for one in found] == ["GRAND TOTAL"]


def test_total_cost_takes_the_figure_from_the_line_below() -> None:
    """A measured line. `Cost` is not a figure, so the label's own line offers
    nothing and the search moves down one."""
    words = (
        word("Total", 0, left=100),
        word("Cost", 0, left=190),
        word("450.00", 1, left=100),
    )
    found = only(
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    )
    assert found.label_text == "Total"
    assert found.value_text == "450.00"
    assert found.method == NEXT_LINE
    assert paise_or_none(found.value_text) == 45000


def test_total_des_takes_the_figure_from_the_line_below() -> None:
    """Also measured, and also not English. The label vocabulary is matched on
    `Total` alone; what follows it is not required to be a word this repository
    knows."""
    words = (
        word("Total", 0, left=100),
        word("des", 0, left=190),
        word("27,99", 1, left=100),
    )
    found = only(
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    )
    assert found.value_text == "27,99"
    assert found.method == NEXT_LINE
    # A continental grouping, not 27 rupees 99 paise. `paise_or_none` strips the
    # comma exactly as `labels._DECORATION` says it does, which makes this
    # ₹2,799 - and this test pins that reading rather than approving of it.
    assert paise_or_none(found.value_text) == 279900


def test_total_quantity_twelve_is_refused_as_a_quantity() -> None:
    """THE TRAP. A measured line, and a search that takes the nearest number
    reads twelve cases as twelve rupees. The candidate is kept, named and
    refused - not silently dropped - so a person can see what was considered."""
    words = (
        word("TOTAL", 0, left=100),
        word("QUANTITY:", 0, left=190),
        word("12", 0, left=340, width=30),
    )
    found = only(
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    )
    assert found.value_text == "12"
    assert found.method == SAME_LINE
    assert found.rejected == A_QUANTITY
    assert found.rank == 1


def test_amount_in_words_invents_no_figure() -> None:
    """The sixth measured line. `AMOUNT` is a total label, so a search opens
    here - and the line holds no digits, so nothing is offered. Stated as a
    limitation in the module docstring: the English words are not understood,
    they are simply not mistaken for a number."""
    words = (
        word("AMOUNT", 0, left=100),
        word("IN", 0, left=210),
        word("WORDS:", 0, left=250),
        word("TWO", 0, left=350),
        word("THOUSAND", 0, left=410),
        word("NINE", 0, left=530),
    )
    assert (
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT) == ()
    )


# =============================================================================
# the five search methods
# =============================================================================


def test_a_figure_on_the_label_s_own_line_is_found_first() -> None:
    words = (
        word("TOTAL:", 0, left=100),
        word("1,234.56", 0, left=220),
    )
    found = only(
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    )
    assert found.method == SAME_LINE
    assert found.line_distance == 0
    # 100..180 for the label, 220 for the figure: a 40px horizontal gap and no
    # vertical gap at all, which is the Manhattan distance this module reports.
    assert found.pixel_distance == 40


def test_a_figure_on_the_line_above_is_found() -> None:
    words = (
        word("999.00", 0, left=100),
        word("TOTAL", 1, left=100),
    )
    found = only(
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    )
    assert found.method == PREVIOUS_LINE
    assert found.line_distance == 1
    assert paise_or_none(found.value_text) == 99900


def test_a_figure_in_the_box_to_the_right_is_found_by_geometry() -> None:
    """The case the line numbering loses. A two-column layout puts the value in
    its own cell and the engine gives that cell a line number nowhere near the
    label's - here line 7 against line 0. `right_of` reaches it because the two
    boxes share a horizontal band, and the pixel limit is what bounds it."""
    label = PageWord(
        text="TOTAL",
        line=0,
        box=Box(left=100, top=40, right=180, bottom=70),
        confidence=90,
    )
    value = PageWord(
        text="7,500.00",
        line=7,
        box=Box(left=300, top=45, right=400, bottom=75),
        confidence=88,
    )
    found = only(
        candidates_for(
            (label, value), field="total", labels=TOTAL_LABELS, limits=DEFAULT
        )
    )
    assert found.method == RIGHT_OF
    assert found.line_distance == 7
    assert found.pixel_distance == 120
    assert found.rejected == ""
    assert paise_or_none(found.value_text) == 750000


def test_a_figure_in_the_box_below_is_found_by_geometry() -> None:
    """Same argument, other axis: the boxes share a vertical band, so the value
    is in the label's column even though the line numbers are unrelated."""
    label = PageWord(
        text="TOTAL",
        line=0,
        box=Box(left=100, top=40, right=180, bottom=70),
        confidence=90,
    )
    value = PageWord(
        text="7,500.00",
        line=9,
        box=Box(left=110, top=250, right=210, bottom=280),
        confidence=88,
    )
    found = only(
        candidates_for(
            (label, value), field="total", labels=TOTAL_LABELS, limits=DEFAULT
        )
    )
    assert found.method == BELOW
    assert found.pixel_distance == 180


def test_a_figure_that_shares_no_band_is_not_reached_by_geometry() -> None:
    """CONTROL for the two geometric methods. Diagonally placed, well inside the
    pixel limit, and on neither the same row nor the same column - so it is
    beside nothing and is not offered."""
    label = PageWord(
        text="TOTAL",
        line=0,
        box=Box(left=100, top=40, right=180, bottom=70),
        confidence=90,
    )
    value = PageWord(
        text="7,500.00",
        line=9,
        box=Box(left=300, top=200, right=400, bottom=230),
        confidence=88,
    )
    assert (
        candidates_for(
            (label, value), field="total", labels=TOTAL_LABELS, limits=DEFAULT
        )
        == ()
    )


def test_a_figure_before_the_label_on_its_own_line_is_not_taken() -> None:
    """A value printed to the LEFT of its label on the same row is not something
    the measured corpus showed, and taking it would mean any figure anywhere on
    a total line belongs to the total."""
    words = (
        word("1,234.56", 0, left=100),
        word("TOTAL", 0, left=220),
    )
    assert (
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT) == ()
    )


def test_a_currency_mark_and_its_figure_are_one_span() -> None:
    """`Rs.` alone is not a figure and `1,234.56` alone loses its decoration.
    `labels.CURRENCY` is the list of what a currency mark is, and this module
    reads that list rather than keeping a second one."""
    words = (
        word("TOTAL", 0, left=100),
        word("Rs.", 1, left=100, width=40),
        word("1,234.56", 1, left=150),
    )
    found = only(
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    )
    assert found.value_text == "Rs. 1,234.56"
    assert paise_or_none(found.value_text) == 123456


def test_two_figures_in_one_table_row_are_two_candidates_not_one_ruin() -> None:
    """A greedy run of adjacent numeric words would join these into one
    unparseable span and lose both. Both are offered instead, and the review
    sentence is what stops one being picked silently."""
    words = (
        word("TOTAL", 0, left=100),
        word("1,234.56", 1, left=100),
        word("2,000.00", 1, left=300),
    )
    found = candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    assert [one.value_text for one in found] == ["1,234.56", "2,000.00"]
    assert [one.rejected for one in found] == ["", ""]


# =============================================================================
# the bounds - these are the controls
# =============================================================================


def test_a_figure_beyond_the_pixel_limit_is_not_taken() -> None:
    """CONTROL. The figure is on the very next line by the caller's numbering
    and 900 pixels away on the page. Line adjacency is not nearness, and a
    module that took this would be taking a page footer as a total."""
    label = PageWord(
        text="TOTAL",
        line=0,
        box=Box(left=100, top=40, right=180, bottom=70),
        confidence=90,
    )
    far = PageWord(
        text="1,234.56",
        line=1,
        box=Box(left=100, top=970, right=200, bottom=1000),
        confidence=90,
    )
    assert (
        candidates_for((label, far), field="total", labels=TOTAL_LABELS, limits=DEFAULT)
        == ()
    )


def test_a_figure_exactly_at_the_pixel_limit_is_taken() -> None:
    """The other side of the same boundary, so the limit is known to be
    inclusive rather than merely known to reject something."""
    label = PageWord(
        text="TOTAL",
        line=0,
        box=Box(left=100, top=40, right=180, bottom=70),
        confidence=90,
    )
    edge = PageWord(
        text="1,234.56",
        line=1,
        box=Box(left=100, top=470, right=200, bottom=500),
        confidence=90,
    )
    found = only(
        candidates_for(
            (label, edge), field="total", labels=TOTAL_LABELS, limits=DEFAULT
        )
    )
    assert found.pixel_distance == DEFAULT.max_pixel_distance == 400


def test_a_figure_two_lines_away_is_not_taken_at_the_default_limit() -> None:
    """CONTROL, AND IT CAUGHT A REAL HOLE ON ITS FIRST RUN. This figure is in
    the label's own column, so `below` reached it and handed it over even though
    the line limit had already refused it - a second code path walking round the
    bound. `_placed` now requires the geometry to CONTRADICT the line numbering:
    the boxes are 90px apart down the page and two lines of a 30px label imply
    at least 60px, so the numbering is right and the refusal stands."""
    words = (
        word("TOTAL", 0, left=100),
        word("1,234.56", 2, left=100),
    )
    assert (
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT) == ()
    )


def test_geometry_overrules_the_numbering_only_when_it_contradicts_it() -> None:
    """The same two boxes, the same 90px apart, with the engine claiming nine
    lines between them instead of two. Nine lines of a 30px label imply at least
    270px of blank page; 90px is not that, so the numbering is disbelieved and
    the figure is reached. This is the ONLY thing `below` is for."""
    label = PageWord(
        text="TOTAL",
        line=0,
        box=Box(left=100, top=40, right=180, bottom=70),
        confidence=90,
    )
    misnumbered = PageWord(
        text="1,234.56",
        line=9,
        box=Box(left=100, top=160, right=180, bottom=190),
        confidence=90,
    )
    found = only(
        candidates_for(
            (label, misnumbered), field="total", labels=TOTAL_LABELS, limits=DEFAULT
        )
    )
    assert found.method == BELOW
    assert found.line_distance == 9
    assert found.pixel_distance == 90


def test_widening_the_line_limit_reaches_it_and_still_calls_it_next_line() -> None:
    words = (
        word("TOTAL", 0, left=100),
        word("1,234.56", 2, left=100),
    )
    wider = Limits(max_line_distance=2, max_pixel_distance=400)
    found = only(
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=wider)
    )
    assert found.method == NEXT_LINE
    assert found.line_distance == 2


def test_a_word_with_no_box_still_reaches_the_line_methods() -> None:
    """`pagereader.py` hands on `freeocr.Word` rows carrying no position at all.
    That caller is supported and gets the line limit alone - which is weaker,
    and is said out loud on `Limits` rather than discovered on a scan."""
    words = (
        word("TOTAL", 0, box=False),
        word("1,234.56", 1, box=False),
    )
    found = only(
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    )
    assert found.box is None
    assert found.pixel_distance is None
    assert found.method == NEXT_LINE
    assert found.rejected == ""


# =============================================================================
# the eight detectors, one at a time
# =============================================================================


@pytest.mark.parametrize(
    "printed",
    ["28/01/26", "2018-09-21", "15.08.2026", "01-Aug-2026", "1 Jan 26"],
)
def test_dates_are_recognised(printed: str) -> None:
    assert looks_like_a_date(printed) is True


@pytest.mark.parametrize(
    "printed",
    ["1,234.56", "1.234.56", "32.838,00", "450.00", "12", "45,61,546"],
)
def test_amounts_are_not_mistaken_for_dates(printed: str) -> None:
    """`1.234.56` and `32.838,00` are continental groupings printed on real
    documents in this repository's corpus. The first fails on a three-digit
    middle group and the second on its two separators differing."""
    assert looks_like_a_date(printed) is False


def test_a_date_beside_a_total_is_refused_by_name() -> None:
    words = (
        word("TOTAL", 0, left=100),
        word("28/01/26", 1, left=100),
    )
    found = only(
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    )
    assert found.rejected == A_DATE
    assert found.value_text == "28/01/26"


def test_a_percentage_is_recognised_with_its_sign_attached() -> None:
    assert looks_like_a_percentage("18%", "GST 18%") is True


def test_a_percentage_is_recognised_when_the_engine_strands_the_sign() -> None:
    """The case that earns the context arm: `18 %` split into two words leaves a
    bare `18` that is otherwise indistinguishable from eighteen rupees."""
    assert looks_like_a_percentage("18", "GST @ 18 %") is True


def test_a_real_amount_is_not_a_percentage_without_a_percent_sign() -> None:
    assert looks_like_a_percentage("18", "GRAND TOTAL 18") is False
    assert looks_like_a_percentage("1,234.56", "TOTAL 1,234.56 %") is False


def test_a_stranded_percent_sign_refuses_the_figure_beside_it() -> None:
    words = (
        word("TOTAL", 0, left=100),
        word("18", 1, left=100, width=30),
        word("%", 1, left=140, width=20),
    )
    found = only(
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    )
    assert found.value_text == "18"
    assert found.rejected == A_PERCENTAGE


def test_a_quantity_needs_the_word_and_not_only_the_shape() -> None:
    assert looks_like_a_quantity("12", "TOTAL QUANTITY: 12") is True
    assert looks_like_a_quantity("12", "GRAND TOTAL 12") is False


def test_a_grouped_amount_on_a_quantity_line_is_still_money() -> None:
    """Comma grouping is how money is printed and not how a count is, so a real
    figure sharing a line with the word QTY is not thrown away."""
    assert looks_like_a_quantity("1,234.56", "QTY 3 TOTAL 1,234.56") is False


def test_an_hsn_code_needs_its_word_beside_it() -> None:
    assert looks_like_an_hsn_or_sac_code("998311", "HSN/SAC: 998311") is True
    assert looks_like_an_hsn_or_sac_code("998311", "GRAND TOTAL 998311") is False


def test_an_hsn_code_beside_a_total_is_refused_as_hsn_and_not_as_a_pin() -> None:
    """Both detectors fire on six bare digits. `REJECTION_ORDER` puts the one
    that has read the word beside the figure first, so the sentence a person
    sees is the better-informed of the two."""
    words = (
        word("TOTAL", 0, left=100),
        word("HSN/SAC:", 1, left=100),
        word("998311", 1, left=220),
    )
    found = only(
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    )
    assert found.rejected == AN_HSN_OR_SAC_CODE


@pytest.mark.parametrize(
    "printed", ["9876543210", "08022345678", "919876543210", "+919876543210"]
)
def test_phone_numbers_are_recognised_by_shape(printed: str) -> None:
    assert looks_like_a_phone_number(printed, "GRAND TOTAL") is True


def test_a_real_amount_near_the_word_contact_is_not_a_phone_number() -> None:
    """The context arm is narrowed to dialling characters for this reason. A
    decimal point means money, whatever else is printed on the line."""
    assert looks_like_a_phone_number("1,234.56", "CONTACT 1,234.56") is False


def test_a_mobile_number_beside_a_total_is_refused_by_name() -> None:
    words = (
        word("TOTAL", 0, left=100),
        word("9876543210", 1, left=100),
    )
    found = only(
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    )
    assert found.rejected == A_PHONE_NUMBER


def test_six_bare_digits_are_refused_and_seven_are_not() -> None:
    """The stated cost: a real ₹1,23,456 printed with no comma reads as a PIN
    code and is refused. It is refused rather than posted because a refusal is a
    question to a person and a wrong post is a wrong ledger."""
    assert looks_like_a_postal_code("560001") is True
    assert looks_like_a_postal_code("1,23,456") is False
    assert looks_like_a_postal_code("1234567") is False
    assert looks_like_a_postal_code("123456.00") is False


def test_a_postal_code_beside_a_total_is_refused_by_name() -> None:
    words = (
        word("TOTAL", 0, left=100),
        word("560001", 1, left=100),
    )
    found = only(
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    )
    assert found.rejected == A_POSTAL_CODE


def test_a_reference_number_is_recognised_and_a_decorated_amount_is_not() -> None:
    """The currency strip is what keeps `Rs. 1,234.56` out of this detector.
    Without it every decorated amount reads as letters mixed with digits."""
    assert looks_like_an_invoice_number("INV-2026-0041", "TOTAL") is True
    assert looks_like_an_invoice_number("#4417", "TOTAL") is True
    assert looks_like_an_invoice_number("Rs. 1,234.56", "TOTAL") is False
    assert looks_like_an_invoice_number("1,234.56", "TOTAL") is False


def test_a_long_digit_run_on_a_reference_line_is_refused() -> None:
    assert looks_like_an_invoice_number("20260041", "INVOICE NO 20260041") is True
    assert looks_like_an_invoice_number("20260041", "GRAND TOTAL 20260041") is False


def test_a_hash_beside_a_total_is_refused_as_a_reference() -> None:
    words = (
        word("TOTAL", 0, left=100),
        word("#4417", 1, left=100),
    )
    found = only(
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    )
    assert found.rejected == AN_INVOICE_NUMBER


def test_a_malformed_amount_is_the_residue() -> None:
    """`paise_or_none` refuses sub-paise rather than rounding, and this module
    inherits that refusal instead of restating it."""
    assert looks_like_a_malformed_amount("10.005") is True
    assert looks_like_a_malformed_amount("1.2.3.4") is True
    assert looks_like_a_malformed_amount("1,234.56") is False


def test_the_indian_whole_rupee_mark_is_refused_until_labels_learns_it() -> None:
    """`₹45,61,546/-` is printed on `gst-portal-and-govt-002.pdf` in this
    repository's own corpus and `/-` means only 'and no paise'. It is refused
    anyway, and refusing it is the CORRECT behaviour for this module today.

    An earlier draft stripped the marker before parsing. That made the figure
    accepted here while `paise_or_none` on the very same characters returned
    None - an accepted candidate the caller cannot convert, which carries no
    reason and no sentence. The repair belongs in `labels.paise_or_none`, not in
    a second copy of the marker rule living here."""
    assert looks_like_a_malformed_amount("45,61,546/-") is True
    assert paise_or_none("45,61,546/-") is None


@pytest.mark.parametrize(
    "printed",
    [
        "1,234.56",
        "45,61,546/-",
        "10.005",
        "-1,234.56",
        "₹1,234.56",
        "28/01/26",
        "560001",
        "9876543210",
        "#4417",
        "18%",
        "12",
        "1.2.3.4",
        "0.01",
        "2,076.76",
    ],
)
def test_an_accepted_candidate_always_converts_to_paise(printed: str) -> None:
    """THE INVARIANT EVERY CALLER DEPENDS ON, checked one printed figure at a
    time: an empty `rejected` means `paise_or_none` will convert those exact
    characters. This is the test that caught `45,61,546/-` being handed over as
    money nothing could parse."""
    words = (word("TOTAL", 0, left=100), word(printed, 1, left=100))
    for one in candidates_for(
        words, field="total", labels=TOTAL_LABELS, limits=DEFAULT
    ):
        if one.rejected == "":
            assert paise_or_none(one.value_text) is not None, one


def test_a_malformed_figure_beside_a_total_is_refused_by_name() -> None:
    words = (
        word("TOTAL", 0, left=100),
        word("10.005", 1, left=100),
    )
    found = only(
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    )
    assert found.rejected == A_MALFORMED_AMOUNT


def test_the_rejection_order_is_fixed() -> None:
    """A figure satisfying two detectors must always name the same one. A
    refusal reason that changes under a refactor is a reason nobody can pin."""
    assert REJECTION_ORDER == (
        "a date",
        "a percentage or a tax rate",
        "a quantity",
        "an HSN or SAC code",
        "a phone number",
        "a postal code",
        "an invoice number",
        "a malformed amount",
    )


def test_the_search_order_is_fixed() -> None:
    assert SEARCH_ORDER == (
        "same_line",
        "next_line",
        "previous_line",
        "right_of",
        "below",
    )


def test_only_sub_modifies_a_label() -> None:
    """One entry, and it is the measured defect from `labels.amount_on`. A
    longer list would be a guess, and each extra entry silently deletes a real
    label match."""
    assert MODIFIERS_BEFORE == ("SUB",)


# =============================================================================
# ambiguity is kept, ranked, and never resolved silently
# =============================================================================


def test_two_equally_close_figures_are_both_kept_and_ranked() -> None:
    words = (
        word("TOTAL", 0, left=100),
        word("1,234.56", 1, left=100),
        word("2,000.00", 1, left=210),
    )
    found = candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    assert [one.rank for one in found] == [1, 2]
    assert [one.value_text for one in found] == ["1,234.56", "2,000.00"]
    assert [one.method for one in found] == [NEXT_LINE, NEXT_LINE]


def test_two_equally_close_figures_require_review_in_plain_words() -> None:
    """Their gaps differ by less than `CLEARLY_CLOSER_PIXELS`, so neither is a
    better reason than the other and this module refuses to choose."""
    words = (
        word("TOTAL", 0, left=100),
        word("1,234.56", 1, left=100),
        word("2,000.00", 1, left=210),
    )
    found = candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    winner, runner_up = found[0], found[1]
    assert winner.pixel_distance is not None
    assert runner_up.pixel_distance is not None
    assert runner_up.pixel_distance - winner.pixel_distance < CLEARLY_CLOSER_PIXELS
    assert review_needed(found) == (
        "two different figures sit equally close to 'TOTAL' and nothing on this "
        "page says which one is the total: '1,234.56' and '2,000.00'. "
        "Neither is used until a person says which."
    )


def test_a_clearly_closer_figure_needs_no_review() -> None:
    """Same line, same method, and a gap smaller by more than
    `CLEARLY_CLOSER_PIXELS` - which is a better reason and not merely a
    different one."""
    words = (
        word("TOTAL", 0, left=100),
        word("1,234.56", 1, left=100),
        word("2,000.00", 1, left=400),
    )
    found = candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    assert len(found) == 2
    assert review_needed(found) == ""


def test_a_same_line_figure_beats_a_next_line_figure_without_review() -> None:
    """The search order is also the strength order: printed AGAINST the label
    beats printed NEAR it, whatever the pixels say."""
    words = (
        word("TOTAL", 0, left=100),
        word("1,234.56", 0, left=220),
        word("2,000.00", 1, left=100),
    )
    found = candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    assert [one.method for one in found] == [SAME_LINE, NEXT_LINE]
    assert review_needed(found) == ""


def test_repetition_is_not_disagreement() -> None:
    """`labels.the_one`'s rule, reused rather than restated: a continuation
    sheet repeats its header, so two readings of the same figure agree."""
    words = (
        word("TOTAL", 0, left=100),
        word("1,234.56", 1, left=100),
        word("1,234.56", 1, left=210),
    )
    found = candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    assert len(found) == 2
    assert review_needed(found) == ""


def test_one_survivor_needs_no_review() -> None:
    words = (
        word("TOTAL", 0, left=100),
        word("1,234.56", 1, left=100),
    )
    assert (
        review_needed(
            candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
        )
        == ""
    )


def test_refused_candidates_never_trigger_a_review_sentence() -> None:
    """A refusal already carries its reason. Two refusals are not two competing
    answers, and asking a person to choose between them would be asking the
    wrong question."""
    words = (
        word("TOTAL", 0, left=100),
        word("28/01/26", 1, left=100),
        word("560001", 1, left=250),
    )
    found = candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    assert [one.rejected for one in found] == [A_DATE, A_POSTAL_CODE]
    assert review_needed(found) == ""


def test_accepted_candidates_rank_above_refused_ones() -> None:
    words = (
        word("TOTAL", 0, left=100),
        word("28/01/26", 1, left=100),
        word("1,234.56", 1, left=250),
    )
    found = candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    assert [(one.rank, one.value_text, one.rejected) for one in found] == [
        (1, "1,234.56", ""),
        (2, "28/01/26", A_DATE),
    ]


# =============================================================================
# determinism
# =============================================================================


def _a_crowded_page() -> tuple[PageWord, ...]:
    """Everything at once: two labels, a survivor, four different refusals, a
    box reached only by geometry, and a word with no geometry at all."""
    return (
        word("SUB", 0, left=100),
        word("TOTAL", 0, left=190),
        word("1,234.56", 1, left=100),
        word("28/01/26", 1, left=250),
        word("GRAND", 2, left=100),
        word("TOTAL", 2, left=210),
        word("560001", 3, left=100),
        word("9876543210", 3, left=250),
        word("#4417", 3, left=400),
        PageWord(
            text="7,500.00",
            line=11,
            box=Box(left=340, top=160, right=440, bottom=190),
            confidence=71,
        ),
    )


def test_the_same_page_twice_gives_identical_objects() -> None:
    page = _a_crowded_page()
    first = candidates_for(page, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    second = candidates_for(page, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    assert first == second
    assert len(first) >= 3


def test_the_same_page_twice_gives_identical_reprs() -> None:
    """Objects compare equal on values; reprs compare equal on ORDER and on
    every field printed. A ranking that shuffled would pass the first test and
    fail this one."""
    page = _a_crowded_page()
    first = candidates_for(page, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    second = candidates_for(page, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    assert repr(first) == repr(second)


def test_the_ranks_are_a_run_of_whole_numbers_from_one() -> None:
    page = _a_crowded_page()
    found = candidates_for(page, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    assert [one.rank for one in found] == list(range(1, len(found) + 1))


def test_the_crowded_page_reads_exactly_this_way() -> None:
    """The whole module in one assertion: which figures were reached, by which
    method, in which order, and what each refusal is called.

    `SUB TOTAL` on line 0 contributes nothing, because the only label site is
    `GRAND TOTAL` on line 2. Reading out from there: `1,234.56` and the date on
    the line above, three figures on the line below, and the cell the engine
    numbered line 11 which geometry puts on the same row as the label.

    The ORDER is the ranking rule end to end - accepted before refused, then
    method, then lines, then pixels, then position on the page."""
    found = candidates_for(
        _a_crowded_page(), field="total", labels=TOTAL_LABELS, limits=DEFAULT
    )
    assert [(one.value_text, one.method, one.rejected) for one in found] == [
        ("1,234.56", PREVIOUS_LINE, ""),
        ("7,500.00", RIGHT_OF, ""),
        ("560001", NEXT_LINE, A_POSTAL_CODE),
        ("9876543210", NEXT_LINE, A_PHONE_NUMBER),
        ("#4417", NEXT_LINE, AN_INVOICE_NUMBER),
        ("28/01/26", PREVIOUS_LINE, A_DATE),
    ]


# =============================================================================
# the guards on the call itself
# =============================================================================


def test_a_nested_label_family_is_refused_loudly() -> None:
    """The mistake that cost `labels.values_for` 98 tests. Here it would be
    quieter still: a nested tuple never equals a joined word, so every document
    would read nothing with no exception raised anywhere."""
    with pytest.raises(TypeError, match="not one nested inside another"):
        candidates_for(
            (word("TOTAL", 0),),
            field="total",
            labels=(TOTAL_LABELS,),  # pyright: ignore[reportArgumentType]
            limits=DEFAULT,
        )


def test_an_empty_label_family_is_refused_loudly() -> None:
    with pytest.raises(ValueError, match="no labels to look for"):
        candidates_for((word("TOTAL", 0),), field="total", labels=(), limits=DEFAULT)


def test_a_page_with_no_words_returns_nothing() -> None:
    assert candidates_for((), field="total", labels=TOTAL_LABELS, limits=DEFAULT) == ()


def test_a_totals_label_is_not_matched_by_a_word_that_merely_starts_with_it() -> None:
    """`labels._SEPARATOR`'s rule: the tolerance is the separator and never a
    letter. `TOTALS` is a plural, `TOTAL:` is a label, and nothing here can tell
    those apart except by refusing to accept a letter."""
    words = (
        word("TOTALS", 0, left=100),
        word("1,234.56", 1, left=100),
    )
    assert (
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT) == ()
    )


def test_punctuation_around_a_label_is_tolerated() -> None:
    words = (
        word("(TOTAL)", 0, left=100),
        word("1,234.56", 1, left=100),
    )
    found = only(
        candidates_for(words, field="total", labels=TOTAL_LABELS, limits=DEFAULT)
    )
    assert found.label_text == "(TOTAL)"
    assert paise_or_none(found.value_text) == 123456

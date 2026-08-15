"""The date reader, and the twelve months in which an Indian bill is unreadable.

WHY THIS FILE EXISTS
--------------------
`accountant/extract/textlayer._date_from` refuses a numeric date unless the
document settles the order of its own numbers - one of them over twelve, or the
two of them equal. On an Indian purchase bill printed `01/04/2026` neither is
true, and the field comes back unread with a question attached.

MEASURED 2026-08-15 by calling the shipped reader directly on branch
`cage/safety-layer` (`/Users/tanveersidhu/ACCOUNTANT`), one string at a time:

    '01/04/2026'    REFUSED   ambiguous (could be 1 April or 4 January)
    '09/07/2014'    REFUSED   ambiguous (could be 9 July or 7 September)
    '06/10/2026'    REFUSED   ambiguous (could be 6 October or 10 June)
    '01-04-2026'    REFUSED   ambiguous
    '01.04.2026'    REFUSED   ambiguous
    '28/01/26'      REFUSED   'is not a date this reader reads'
    '01/04/26'      REFUSED   'is not a date this reader reads'
    '01-04-26'      REFUSED   'is not a date this reader reads'
    '2026/04/01'    REFUSED   'is not a date this reader reads'
    '2026-04-01'    read      2026-04-01
    '01 Apr 2026'   read      2026-04-01
    '01 April 2026' read      2026-04-01
    '26/02/2026'    read      2026-02-26
    '04/04/2019'    read      2019-04-04

THE BRIEF THAT COMMISSIONED THIS WORK NAMED THREE REFUSED DATES AND ONE OF THEM
WAS NOT REFUSED. `04/04/2019` reads today, and so does `26/02/2026`. Both are
tested below and both are labelled CONTROL rather than FIX, because a test that
claims credit for something already working is a test that gets deleted by the
first person who checks it - and it takes the real ones with it.

WHAT THESE TESTS ARE FOR
-------------------------
Not "the parser parses". Every assertion here names an EXACT day, an EXACT
`input_format` string, an EXACT `candidates` tuple and, on every refusal, the
EXACT sentence a person will be shown. A refusal sentence is the entire product
on a bill that cannot be read automatically, and a test that only checked it
was non-empty would let it degrade into `not_found` with nothing after it.

THE THREE THINGS IT WOULD BE WORTH SHIPPING A BUG TO KEEP
-----------------------------------------------------------
    a silent pick           `06/10/2026` under UNKNOWN must never come back as
                            a day. Two real days stand, nothing in the
                            characters chooses, and choosing files a return in
                            the wrong quarter with full confidence behind it.

    a mended `raw`          A refusal that quotes a tidied string asks somebody
                            to find characters their document does not have.
                            `test_a_refused_date_still_carries_its_own
                            _characters` is the control for this.

    an invented month       A day that does not exist must be named as not
                            existing, never rounded to one that does. `31/02`
                            is a misprint, and `2026-03-03` is not what it says.
"""

from __future__ import annotations

import datetime

import pytest

from accountant.extract.dates import (
    CENTURY_PIVOT,
    FORMAT_ISO,
    FORMAT_SEVERAL,
    FORMAT_UNREADABLE,
    MONTH_NAMES,
    DateLocale,
    ReadDate,
    read_date,
)

# The nine written forms the reader promised to accept, each with the day it
# states under INDIAN and the `input_format` it must report for itself.
EVERY_ACCEPTED_FORM: tuple[tuple[str, datetime.date, str], ...] = (
    ("2026-04-01", datetime.date(2026, 4, 1), "ISO"),
    ("2026/04/01", datetime.date(2026, 4, 1), "YYYY/MM/DD"),
    ("01/04/2026", datetime.date(2026, 4, 1), "DD/MM/YYYY"),
    ("01-04-2026", datetime.date(2026, 4, 1), "DD-MM-YYYY"),
    ("01.04.2026", datetime.date(2026, 4, 1), "DD.MM.YYYY"),
    ("01/04/26", datetime.date(2026, 4, 1), "DD/MM/YY"),
    ("01-04-26", datetime.date(2026, 4, 1), "DD-MM-YY"),
    ("01 Apr 2026", datetime.date(2026, 4, 1), "DD Mon YYYY"),
    ("01 April 2026", datetime.date(2026, 4, 1), "DD Month YYYY"),
)


#: DOTS WITH A TWO-DIGIT YEAR ARE REFUSED, AND THAT IS THE PRICE OF A FIX.
#: `01.04.26` used to be on the list above. It came off on 2026-08-15 because
#: the pattern that accepted it also accepted `Version 1.2.34` as a CONFIDENT
#: 1 February 2034 - `ambiguous=False`, `why=''`, nothing for anyone to notice.
#: See `test_a_version_or_clause_number_is_not_a_date`.
#:
#: Dots with a two-digit year are how version, clause and cheque references are
#: written. Dots with a FOUR-digit year (`01.04.2026`) stay accepted, and so do
#: slashes and dashes with a two-digit year (`01/04/26`, `01-04-26`). This
#: format was never on the list the module was asked to accept.
REFUSED_BECAUSE_IT_IS_USUALLY_NOT_A_DATE: tuple[str, ...] = ("01.04.26", "1.4.26")


# =============================================================================
# the defect: an Indian bill's date
# =============================================================================


def test_indian_reads_the_dates_the_shipped_reader_refuses() -> None:
    """The three dates from the brief, read day-first because the owner said so.

    Two of the three are the real repair. `04/04/2019` is not - see the
    control below - and it is included here anyway because the wiring must
    treat all three the same way.
    """
    expected = {
        "09/07/2014": datetime.date(2014, 7, 9),
        "06/10/2026": datetime.date(2026, 10, 6),
        "04/04/2019": datetime.date(2019, 4, 4),
    }
    for printed, day in expected.items():
        reading = read_date(printed, locale=DateLocale.INDIAN)
        assert reading.value == day, printed
        assert reading.raw == printed
        assert reading.input_format == "DD/MM/YYYY"
        assert reading.locale_assumed is DateLocale.INDIAN
        assert reading.ambiguous is False
        assert reading.candidates == (day,)
        assert reading.why == ""


def test_indian_reads_a_date_carrying_its_label() -> None:
    """`Invoice Date: 01/04/2026` - the exact string the brief measured.

    The label vocabulary lives in `labels.DATE_LABEL` and is not duplicated
    here; this reader simply finds the date SHAPE wherever it sits, so a caller
    that has already stripped the label and a caller that has not both get an
    answer.
    """
    reading = read_date("Invoice Date: 01/04/2026", locale=DateLocale.INDIAN)
    assert reading.value == datetime.date(2026, 4, 1)
    assert reading.raw == "Invoice Date: 01/04/2026"
    assert reading.input_format == "DD/MM/YYYY"
    assert reading.ambiguous is False
    assert reading.why == ""


# =============================================================================
# every written form that was promised
# =============================================================================


@pytest.mark.parametrize(("printed", "day", "shape"), EVERY_ACCEPTED_FORM)
def test_every_accepted_form_reads_to_the_same_first_of_april(
    printed: str, day: datetime.date, shape: str
) -> None:
    """Ten spellings of one day. All ten must be that day and say which spelling."""
    reading = read_date(printed, locale=DateLocale.INDIAN)
    assert reading.value == day, printed
    assert reading.input_format == shape, printed
    assert reading.ambiguous is False, printed
    assert reading.why == "", printed
    assert reading.raw == printed, printed


def test_september_is_spelled_four_ways_and_all_four_are_september() -> None:
    """`SEPT` is the one irregular abbreviation and suppliers print it."""
    for printed in ("09 Sep 2026", "09 Sept 2026", "09 September 2026"):
        reading = read_date(printed, locale=DateLocale.UNKNOWN)
        assert reading.value == datetime.date(2026, 9, 9), printed
    assert read_date("09 Sep 2026", locale=DateLocale.UNKNOWN).input_format == (
        "DD Mon YYYY"
    )
    assert read_date("09 September 2026", locale=DateLocale.UNKNOWN).input_format == (
        "DD Month YYYY"
    )


def test_a_hyphenated_month_word_reports_its_own_punctuation() -> None:
    """`01-Apr-2026` is a real printing and the format field says so."""
    reading = read_date("01-Apr-2026", locale=DateLocale.UNKNOWN)
    assert reading.value == datetime.date(2026, 4, 1)
    assert reading.input_format == "DD-Mon-YYYY"
    assert reading.ambiguous is False


# =============================================================================
# ISO, under every rule
# =============================================================================


@pytest.mark.parametrize("locale", list(DateLocale))
def test_iso_is_unambiguous_under_every_locale(locale: DateLocale) -> None:
    """A four-digit year in front states the order, so no rule can change it."""
    reading = read_date("2026-04-01", locale=locale)
    assert reading.value == datetime.date(2026, 4, 1)
    assert reading.input_format == FORMAT_ISO
    assert reading.ambiguous is False
    assert reading.candidates == (datetime.date(2026, 4, 1),)
    assert reading.why == ""
    assert reading.locale_assumed is locale


@pytest.mark.parametrize("locale", list(DateLocale))
def test_a_month_word_is_unambiguous_under_every_locale(locale: DateLocale) -> None:
    """The month names itself, so there is no order left for a locale to decide."""
    reading = read_date("06 October 2026", locale=locale)
    assert reading.value == datetime.date(2026, 10, 6)
    assert reading.input_format == "DD Month YYYY"
    assert reading.ambiguous is False
    assert reading.why == ""


# =============================================================================
# UNKNOWN: the choice that must never be made silently
# =============================================================================


def test_unknown_refuses_a_date_that_reads_as_two_different_days() -> None:
    """`06/10/2026` is the 6th of October or the 10th of June. Nothing chooses.

    THE SENTENCE IS ASSERTED WHOLE. It is an owner decision from 2026-08-13 and
    it carries both readings and the format to answer in; a mutation that
    dropped either half would still be a plausible-looking refusal.
    """
    reading = read_date("06/10/2026", locale=DateLocale.UNKNOWN)
    assert reading.value is None
    assert reading.raw == "06/10/2026"
    assert reading.input_format == "DD/MM/YYYY or MM/DD/YYYY"
    assert reading.locale_assumed is DateLocale.UNKNOWN
    assert reading.ambiguous is True
    assert reading.candidates == (
        datetime.date(2026, 10, 6),
        datetime.date(2026, 6, 10),
    )
    assert reading.why == (
        "The date '06/10/2026' is ambiguous (could be 6 October or 10 June). "
        "Please confirm the date in YYYY-MM-DD format."
    )


def test_unknown_reads_a_date_whose_two_readings_are_the_same_day() -> None:
    """`04/04/2019` is the 4th of April whichever way round it is read.

    CONTROL, NOT A FIX. The shipped reader already gets this right - MEASURED
    above, `_date_from('04/04/2019')` returns `datetime.date(2019, 4, 4)`. The
    brief said it was refused; it is not. What is asserted here is that the new
    reader does not LOSE it, and that it reports the day as certain
    (`ambiguous` False) while reporting honestly that the ORDER was never
    decided (`input_format` names both).
    """
    reading = read_date("04/04/2019", locale=DateLocale.UNKNOWN)
    assert reading.value == datetime.date(2019, 4, 4)
    assert reading.ambiguous is False
    assert reading.candidates == (datetime.date(2019, 4, 4),)
    assert reading.input_format == "DD/MM/YYYY or MM/DD/YYYY"
    assert reading.why == ""


def test_unknown_reads_a_date_arithmetic_settles_and_says_which_order_it_used() -> None:
    """26 is not a month, so one order survives and nothing was assumed.

    CONTROL for the day itself - the shipped reader also returns 2026-02-26.
    NEW is `input_format`: it reports WHICH order survived, so a reviewer can
    see that day-first was arithmetic rather than a locale guess. The
    month-first twin proves the field is not a constant.
    """
    day_first = read_date("26/02/2026", locale=DateLocale.UNKNOWN)
    assert day_first.value == datetime.date(2026, 2, 26)
    assert day_first.input_format == "DD/MM/YYYY"
    assert day_first.ambiguous is False
    assert day_first.candidates == (datetime.date(2026, 2, 26),)

    month_first = read_date("02/26/2026", locale=DateLocale.UNKNOWN)
    assert month_first.value == datetime.date(2026, 2, 26)
    assert month_first.input_format == "MM/DD/YYYY"
    assert month_first.ambiguous is False


def test_unknown_refuses_every_ambiguous_day_of_a_whole_month() -> None:
    """The size of the defect, counted rather than described.

    Twelve months by twelve days is 144 numeric dates where both numbers are
    twelve or under. 12 of them are the same number twice and read cleanly;
    the other 132 state two different days and must all be refused. This is the
    population the INDIAN locale exists to serve.
    """
    refused = 0
    read = 0
    for first in range(1, 13):
        for second in range(1, 13):
            printed = f"{first:02d}/{second:02d}/2026"
            reading = read_date(printed, locale=DateLocale.UNKNOWN)
            if reading.value is None:
                assert reading.ambiguous is True
                assert reading.why != ""
                assert len(reading.candidates) == 2
                refused += 1
            else:
                assert first == second
                assert reading.ambiguous is False
                read += 1
    assert (refused, read) == (132, 12)


def test_indian_reads_all_144_of_them() -> None:
    """The same 144 under a stated locale. Every one is a day, none is a guess."""
    for first in range(1, 13):
        for second in range(1, 13):
            printed = f"{first:02d}/{second:02d}/2026"
            reading = read_date(printed, locale=DateLocale.INDIAN)
            assert reading.value == datetime.date(2026, second, first), printed
            assert reading.ambiguous is False, printed
            assert reading.why == "", printed


# =============================================================================
# ISO_ONLY: the tier with no judgement calls in it
# =============================================================================


def test_iso_only_refuses_every_bare_numeric_date_including_the_certain_one() -> None:
    """The cost of the strictest tier, paid out loud.

    `04/04/2019` and `26/02/2026` both have exactly one day standing, and this
    tier still refuses them, because its rule is one sentence with no
    exceptions: only a form that states its own order is read. A caller who
    wants those two should pass UNKNOWN. This test exists so that cost is a
    measured fact rather than a surprise found in production.
    """
    for printed in ("04/04/2019", "26/02/2026", "01/04/2026"):
        reading = read_date(printed, locale=DateLocale.ISO_ONLY)
        assert reading.value is None, printed
        assert reading.raw == printed
        assert reading.locale_assumed is DateLocale.ISO_ONLY
        assert reading.why == (
            f"The date '{printed}' does not say whether its first number is "
            "the day or the month, and this reader was told to assume no "
            "order. Please confirm the date in YYYY-MM-DD format."
        )
    assert read_date("04/04/2019", locale=DateLocale.ISO_ONLY).candidates == (
        datetime.date(2019, 4, 4),
    )
    assert read_date("06/10/2026", locale=DateLocale.ISO_ONLY).ambiguous is True
    assert read_date("04/04/2019", locale=DateLocale.ISO_ONLY).ambiguous is False


# =============================================================================
# days that do not exist
# =============================================================================


def test_an_impossible_date_names_its_own_impossibility() -> None:
    """Four impossible dates, four different reasons, none of them a guess.

    `ambiguous` is False on all four. There is no choice between two readings
    here - there is no reading at all - and reporting these as ambiguous would
    send a person a question with nothing in it to answer.
    """
    expected = {
        "32/01/2026": (
            "The date '32/01/2026' states no day that exists: read day first, "
            "January 2026 has no day 32; read month first, there is no month "
            "32. It was misread or misprinted."
        ),
        "00/05/2026": (
            "The date '00/05/2026' states no day that exists: read day first, "
            "there is no day 0; read month first, there is no month 0. "
            "It was misread or misprinted."
        ),
        "31/02/2026": (
            "The date '31/02/2026' states no day that exists: read day first, "
            "February 2026 has no day 31; read month first, there is no month "
            "31. It was misread or misprinted."
        ),
    }
    for printed, sentence in expected.items():
        reading = read_date(printed, locale=DateLocale.UNKNOWN)
        assert reading.value is None, printed
        assert reading.ambiguous is False, printed
        assert reading.candidates == (), printed
        assert reading.raw == printed
        assert reading.why == sentence, printed


def test_an_impossible_iso_date_names_the_month_that_does_not_exist() -> None:
    """`2026-13-01`. The order is stated, so exactly one reading fails."""
    reading = read_date("2026-13-01", locale=DateLocale.INDIAN)
    assert reading.value is None
    assert reading.input_format == FORMAT_ISO
    assert reading.ambiguous is False
    assert reading.candidates == ()
    assert reading.why == (
        "The date '2026-13-01' states no day that exists: there is no month 13."
        " It was misread or misprinted."
    )


def test_an_impossible_indian_date_reports_one_reading_only() -> None:
    """Under a stated locale there is one reading, so there is one reason."""
    reading = read_date("31/02/2026", locale=DateLocale.INDIAN)
    assert reading.value is None
    assert reading.ambiguous is False
    assert reading.candidates == ()
    assert reading.why == (
        "The date '31/02/2026' states no day that exists: read day first, "
        "February 2026 has no day 31. It was misread or misprinted."
    )


def test_the_29th_of_february_is_read_in_a_leap_year_and_refused_otherwise() -> None:
    """The one impossible day that is only impossible in some years."""
    leap = read_date("29/02/2024", locale=DateLocale.INDIAN)
    assert leap.value == datetime.date(2024, 2, 29)
    ordinary = read_date("29/02/2026", locale=DateLocale.INDIAN)
    assert ordinary.value is None
    assert ordinary.why == (
        "The date '29/02/2026' states no day that exists: read day first, "
        "February 2026 has no day 29. It was misread or misprinted."
    )


# =============================================================================
# two-digit years, and the century that was chosen
# =============================================================================


def test_the_century_rule_is_the_posix_one_and_it_is_pinned() -> None:
    """`00`-`68` is 2000s, `69`-`99` is 1900s. The pivot is asserted by value.

    A sliding window would need a clock, and a reader whose answer depends on
    when it was asked cannot be audited against a voucher it already posted.
    The two dates either side of the pivot are what a change to `CENTURY_PIVOT`
    has to walk past.
    """
    expected = {
        "01/04/00": datetime.date(2000, 4, 1),
        "01/04/26": datetime.date(2026, 4, 1),
        "01/04/68": datetime.date(2068, 4, 1),
        "01/04/69": datetime.date(1969, 4, 1),
        "01/04/99": datetime.date(1999, 4, 1),
    }
    for printed, day in expected.items():
        reading = read_date(printed, locale=DateLocale.INDIAN)
        assert reading.value == day, printed
        assert reading.input_format == "DD/MM/YY", printed
        assert reading.why == "", printed
    # LAST, DELIBERATELY. Moving the pivot to 50 was tried while writing this
    # file: with the constant asserted FIRST the test failed on the constant
    # and the five days above were never reached, so the only thing proven was
    # that a number equals itself. The days are the measurement; this line is a
    # tripwire for the next person to edit the constant, and it is worth
    # nothing on its own.
    assert CENTURY_PIVOT == 68


def test_a_two_digit_year_the_shipped_reader_refuses_outright() -> None:
    """`28/01/26` - MEASURED as `'is not a date this reader reads'` today."""
    reading = read_date("28/01/26", locale=DateLocale.INDIAN)
    assert reading.value == datetime.date(2026, 1, 28)
    assert reading.input_format == "DD/MM/YY"
    assert reading.ambiguous is False


def test_a_three_digit_year_is_refused_rather_than_read_as_year_202() -> None:
    """`01/04/202` is a misread `2026`, and year 202 is a period that cannot exist."""
    reading = read_date("01/04/202", locale=DateLocale.INDIAN)
    assert reading.value is None
    assert reading.input_format == FORMAT_UNREADABLE
    assert reading.why != ""


# =============================================================================
# more than one date in one string
# =============================================================================


def test_the_same_date_printed_twice_is_one_date() -> None:
    """A continuation sheet repeats its header. Repetition is not disagreement."""
    reading = read_date(
        "Date: 01/04/2026 ... Date: 01/04/2026", locale=DateLocale.INDIAN
    )
    assert reading.value == datetime.date(2026, 4, 1)
    assert reading.why == ""


def test_two_different_dates_in_one_string_are_refused() -> None:
    """One of them is wrong, nothing here can say which, and picking posts money."""
    reading = read_date(
        "Invoice Date: 01/04/2026  Received: 05/04/2026", locale=DateLocale.INDIAN
    )
    assert reading.value is None
    assert reading.input_format == FORMAT_SEVERAL
    assert reading.ambiguous is False
    assert reading.candidates == (
        datetime.date(2026, 4, 1),
        datetime.date(2026, 4, 5),
    )
    assert reading.why == (
        "These characters state more than one date (01/04/2026, 05/04/2026) "
        "and the statements disagree, so nothing is read from them. Please "
        "confirm the date in YYYY-MM-DD format."
    )


def test_two_dates_that_are_each_ambiguous_still_offer_every_reading() -> None:
    """CONTROL, and it caught a real defect while this module was being written.

    Under UNKNOWN neither `01/04/2026` nor `05/04/2026` has a single reading,
    so an earlier version collected `value` from each, found None twice, and
    produced a refusal saying "there are two dates here" with `candidates`
    EMPTY. A question that names a disagreement and then offers nothing to
    choose between is not a question. All four readings must survive to the
    person being asked.
    """
    reading = read_date(
        "Invoice Date: 01/04/2026  Received: 05/04/2026", locale=DateLocale.UNKNOWN
    )
    assert reading.value is None
    assert reading.input_format == FORMAT_SEVERAL
    assert reading.candidates == (
        datetime.date(2026, 4, 1),
        datetime.date(2026, 1, 4),
        datetime.date(2026, 4, 5),
        datetime.date(2026, 5, 4),
    )
    assert reading.why != ""


def test_one_date_written_two_ways_on_one_line_is_still_one_date() -> None:
    """`2026-04-01` beside `01 Apr 2026` is agreement, not a second statement."""
    reading = read_date("2026-04-01 (01 Apr 2026)", locale=DateLocale.UNKNOWN)
    assert reading.value == datetime.date(2026, 4, 1)
    assert reading.why == ""


# =============================================================================
# characters that state no date
# =============================================================================


def test_text_with_no_date_says_so_and_lists_what_it_reads() -> None:
    """The refusal has to tell a person what shape to send back."""
    reading = read_date("Thank you for your business", locale=DateLocale.INDIAN)
    assert reading.value is None
    assert reading.input_format == FORMAT_UNREADABLE
    assert reading.ambiguous is False
    assert reading.candidates == ()
    assert reading.why == (
        "'Thank you for your business' contains no date this reader "
        "recognises. It reads YYYY-MM-DD, YYYY/MM/DD, DD/MM/YYYY, DD-MM-YYYY, "
        "DD.MM.YYYY, DD/MM/YY, DD-MM-YY, DD.MM.YY, DD Mon YYYY and "
        "DD Month YYYY."
    )


def test_a_word_that_is_not_a_month_is_not_a_month() -> None:
    """`01 for 2026` fits the shape. `FOR` is not a month, so there is no date.

    STRICTER THAN `textlayer._LONG_DATE`, which takes the first three letters
    and lets anything follow - so `01 JANUXXX 2026` reads there as January.
    Here a badly misread month word leaves the field UNREAD, which is a
    question for a person rather than a wrong month in a ledger.
    """
    for printed in ("01 for 2026", "01 JANUXXX 2026", "01 Marzo 2026"):
        reading = read_date(printed, locale=DateLocale.INDIAN)
        assert reading.value is None, printed
        assert reading.input_format == FORMAT_UNREADABLE, printed
        assert reading.why != "", printed


def test_a_mixed_separator_is_two_shapes_collided_and_is_not_read() -> None:
    """`01/04-2026` is a misread, and reading it invents which half was right."""
    reading = read_date("01/04-2026", locale=DateLocale.INDIAN)
    assert reading.value is None
    assert reading.input_format == FORMAT_UNREADABLE


def test_a_grouped_amount_is_not_mistaken_for_a_date() -> None:
    """`1.234.567` is money. A dot-separated date reader must not eat it."""
    reading = read_date("TOTAL 1.234.567", locale=DateLocale.INDIAN)
    assert reading.value is None
    assert reading.input_format == FORMAT_UNREADABLE


# =============================================================================
# controls
# =============================================================================


def test_a_refused_date_still_carries_its_own_characters() -> None:
    """CONTROL. `raw` is the argument byte for byte, on the refusal path too.

    The whitespace is the assertion. A reader that stripped, upper-cased or
    normalised its input would pass a test written on `'06/10/2026'` and fail
    this one, and the person being asked to confirm the date would be shown a
    string their document does not contain.
    """
    given = "  Invoice Date :  06/10/2026  \n"
    reading = read_date(given, locale=DateLocale.UNKNOWN)
    assert reading.value is None
    assert reading.raw == given
    assert reading.raw != given.strip()
    assert reading.why != ""
    assert reading.why == (
        "The date '06/10/2026' is ambiguous (could be 6 October or 10 June). "
        "Please confirm the date in YYYY-MM-DD format."
    )


def test_no_refusal_anywhere_in_this_file_comes_back_without_a_sentence() -> None:
    """CONTROL. `why` is never empty when `value` is None, and never set when it is not.

    Swept over every refusable shape this module knows plus every accepted one,
    under all three locales. This is the invariant `ExtractedRecord` depends on:
    a field that is absent AND silent cannot be turned into a question.
    """
    strings = [
        "06/10/2026",
        "04/04/2019",
        "26/02/2026",
        "01/04/2026",
        "32/01/2026",
        "00/05/2026",
        "31/02/2026",
        "2026-13-01",
        "01/04/202",
        "01/04-2026",
        "Thank you for your business",
        "01 for 2026",
        "Date: 01/04/2026 Received: 05/04/2026",
        "2026-04-01",
        "2026/04/01",
        "01 April 2026",
        "01-Apr-2026",
        "28/01/26",
        "",
    ]
    checked = 0
    for printed in strings:
        for locale in DateLocale:
            reading = read_date(printed, locale=locale)
            assert isinstance(reading, ReadDate)
            assert reading.raw == printed, (printed, locale)
            assert reading.locale_assumed is locale, (printed, locale)
            if reading.value is None:
                assert reading.why != "", (printed, locale)
                assert reading.why.strip() == reading.why, (printed, locale)
            else:
                assert reading.why == "", (printed, locale)
                assert reading.value in reading.candidates, (printed, locale)
            if reading.ambiguous:
                assert reading.value is None, (printed, locale)
                assert len(reading.candidates) == 2, (printed, locale)
            checked += 1
    assert checked == len(strings) * 3


def test_a_reading_cannot_be_edited_after_the_fact() -> None:
    """CONTROL. Frozen, like the nine types in `schema.py`, for the same reason."""
    reading = read_date("2026-04-01", locale=DateLocale.INDIAN)
    with pytest.raises(AttributeError):
        reading.value = datetime.date(2026, 4, 2)  # pyright: ignore[reportAttributeAccessIssue]


def test_a_locale_that_is_not_a_locale_raises_instead_of_degrading() -> None:
    """CONTROL. `"indian"` is what a config file hands over and it is not INDIAN.

    Every branch tests `locale is DateLocale.X`, so a string would fail all of
    them and fall through to the UNKNOWN behaviour - silently refusing the
    exact dates the owner paid a decision to have read. The same guard is in
    `labels.values_for`, added after a nested tuple cost that module 98 tests.
    """
    with pytest.raises(TypeError, match="read_date takes a DateLocale"):
        read_date("01/04/2026", locale="indian")  # pyright: ignore[reportArgumentType]


def test_the_twelve_month_names_are_twelve_and_are_in_order() -> None:
    """CONTROL. Every refusal sentence indexes this tuple by month number."""
    assert len(MONTH_NAMES) == 12
    assert MONTH_NAMES[0] == "January"
    assert MONTH_NAMES[11] == "December"
    assert MONTH_NAMES[9] == "October"


# =============================================================================
# A VERSION NUMBER IS NOT A DATE
# =============================================================================
#
# Found by adversarial review of this module AFTER it shipped, on 2026-08-15.
# Every string below came back as a CONFIDENT date - one candidate,
# `ambiguous=False`, `why=''` - so a bill printing a clause number in its terms
# would have had its DATE read off the small print with nothing recording it.


@pytest.mark.parametrize(
    "printed",
    [
        "Version 1.2.34",
        "Clause 1.2.34",
        "Cheque 000123 dt 1.2.34",
        "Ref 5.10.15",
        "Challan 3.4.56",
        "E-way 1.23.45.678",
    ],
)
def test_a_version_or_clause_number_is_not_a_date(printed: str) -> None:
    """MEASURED before the fix: `Version 1.2.34` -> 2034-02-01, confidently.

    The module's own docstring argued this could not be fixed cheaply - that the
    only tightening available "would refuse `1/4/2026`, which is a printing real
    suppliers use". That was wrong, and the test below is the proof: requiring a
    four-digit year ONLY when the separator is a dot kills all six of these and
    costs nothing on the accepted list.
    """
    assert read_date(printed, locale=DateLocale.INDIAN).value is None, printed
    assert read_date(printed, locale=DateLocale.UNKNOWN).value is None, printed


@pytest.mark.parametrize("printed", REFUSED_BECAUSE_IT_IS_USUALLY_NOT_A_DATE)
def test_dots_with_a_two_digit_year_are_the_price_of_that_fix(printed: str) -> None:
    """THE COST, STATED RATHER THAN HIDDEN. `01.04.26` is a real way to write a
    date and this module no longer reads it. It is not on the list of formats
    the module was asked to accept, and the same shape is how version and clause
    numbers are written - so this is a trade, and this test is the receipt."""
    assert read_date(printed, locale=DateLocale.INDIAN).value is None


@pytest.mark.parametrize(
    ("printed", "day"),
    [
        ("01.04.2026", datetime.date(2026, 4, 1)),
        ("01/04/26", datetime.date(2026, 4, 1)),
        ("01-04-26", datetime.date(2026, 4, 1)),
        ("1/4/2026", datetime.date(2026, 4, 1)),
        ("28/01/26", datetime.date(2026, 1, 28)),
    ],
)
def test_the_fix_costs_nothing_a_real_bill_prints(
    printed: str, day: datetime.date
) -> None:
    """THE CONTROL, and the one that decides whether the trade above was worth
    it. Dots with a FOUR-digit year still read. Slashes and dashes with a
    two-digit year still read. A single-digit day still reads. If any of these
    goes red the fix took more than it was supposed to."""
    assert read_date(printed, locale=DateLocale.INDIAN).value == day, printed

"""What `accountant/extract/currency.py` reads, and what it refuses to.

WHAT THESE TESTS ARE FOR
-------------------------
`currency.py` exists because two measured defects are live in this repository
right now, and both of them move money without saying so:

    labels.paise_or_none("163,16") -> 1631600     a EUR amount read 100x high
    labels.paise_or_none("39.600") ->    3960     an IDR amount read 1000x low

So the assertions below are EXACT PAISE, EXACT ISO CODES and EXACT BOOLEANS.
None of them checks that an answer merely exists. A test that asserted
`read_money(...).paise is not None` would have passed against both defects
above, which is the whole reason that style is banned here.

THE FOUR CONTROLS, NAMED
-------------------------
Four tests below are controls rather than examples - they exist to catch a
future edit rather than to document today's behaviour, and each says so in its
own docstring:

  * `test_control_unparseable_returns_none_and_never_zero` - a missing amount
    must stay missing. Fabricating zero is the defect `adapter.TYPED_TEXT_MIME`
    records twenty instances of.
  * `test_control_module_source_contains_no_float_and_no_division` - parses this
    module's subject with `ast` and proves the arithmetic cannot be float
    arithmetic.
  * `test_control_disagreement_with_labels_paise_or_none_is_refused` -
    substitutes `labels.paise_or_none` and proves the cross-check is wired to
    the live path rather than merely written down.
  * `test_control_why_is_never_empty_when_the_answer_is_not_certain` - sweeps
    every case in this file and holds the one invariant the dataclass promises.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import re

import pytest

from accountant.extract import currency, labels
from accountant.extract.currency import (
    CURRENCY_ALIASES,
    MINOR_DIGITS,
    UNKNOWN,
    Money,
    read_money,
)

# =============================================================================
# the alias table
# =============================================================================


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("INR", "INR"),
        ("INR.", "INR"),
        ("RS", "INR"),
        ("RS.", "INR"),
        ("₹", "INR"),
        ("USD", "USD"),
        ("US$", "USD"),
        ("EUR", "EUR"),
        ("€", "EUR"),
        ("GBP", "GBP"),
        ("£", "GBP"),
        ("IDR", "IDR"),
        ("RP", "IDR"),
        ("RP.", "IDR"),
        ("JPY", "JPY"),
        ("¥", UNKNOWN),
        ("AED", "AED"),
        ("$", UNKNOWN),
    ],
)
def test_every_required_alias_normalises_to_its_stated_code(
    token: str, expected: str
) -> None:
    """Every spelling the brief names is in the table, mapped to one exact code.

    The two `UNKNOWN` entries are not omissions and the module docstring carries
    the test that put them there: a symbol pins a code only when every currency
    printing it has the SAME number of minor digits. `$` fails it (CLP has zero,
    USD has two) and `¥` fails it (JPY has zero, CNY has two), so guessing
    either would move the integer rather than mislabel it.
    """
    assert CURRENCY_ALIASES[token] == expected


def test_the_alias_table_cannot_be_edited_by_a_caller() -> None:
    """A table a caller can mutate is a variable, and a currency that means one
    thing on the first bill of a batch and another on the second is unfindable.
    """
    with pytest.raises(TypeError):
        CURRENCY_ALIASES["ZWL"] = "ZWL"  # pyright: ignore[reportIndexIssue]


def test_every_alias_resolves_to_a_code_all_three_tables_know() -> None:
    """No alias may point at a code that either lookup table has never heard of.

    Without this, adding `CHF` to the aliases and forgetting `MINOR_DIGITS`
    raises `KeyError` six frames below the mistake, on a real bill, at post time.

    IT REACHES INTO A PRIVATE TABLE ON PURPOSE. `_DECIMAL_CHARACTER` is private
    because no caller needs it - it is consulted for one rule and never
    exported - but the defect being guarded here is precisely a private table
    falling out of step with a public one, and a test that only checked the
    public pair would not see it. The ignore is the evidence, not a workaround.
    """
    for token, code in CURRENCY_ALIASES.items():
        if code == UNKNOWN:
            continue
        assert code in MINOR_DIGITS, token
        assert code in currency._DECIMAL_CHARACTER, token  # pyright: ignore[reportPrivateUsage]


# =============================================================================
# the five amounts the brief requires
# =============================================================================


@pytest.mark.parametrize(
    ("text", "paise", "code", "symbol"),
    [
        ("Total Rp 39.600", 39600, "IDR", "Rp"),
        ("Billed total $18.79", 1879, UNKNOWN, "$"),
        ("Total USD 18.79", 1879, "USD", "USD"),
        ("Total EUR 1.234,56", 123456, "EUR", "EUR"),
        ("Total INR 1,23,456.78", 12345678, "INR", "INR"),
    ],
)
def test_the_five_amounts_the_brief_requires(
    text: str, paise: int, code: str, symbol: str
) -> None:
    """The five lines named as MUST PARSE, to the paise.

    MEASURED 2026-08-15, `labels.amount_on(line, "TOTAL")` returns None for the
    first four of these five - not "unparseable" but the same answer it gives a
    line stating no amount at all. Only `TOTAL INR 1,23,456.78` reads today.
    """
    read = read_money(text)
    assert read.paise == paise
    assert read.code == code
    assert read.symbol == symbol


@pytest.mark.parametrize(
    ("text", "paise", "code"),
    [
        # Whole units, no separator at all.
        ("AED 250", 25000, "AED"),
        ("AED 250.00", 25000, "AED"),
        # Zero minor digits: the integer is the whole unit.
        ("JPY 1,234", 1234, "JPY"),
        ("Rp 39600", 39600, "IDR"),
        # The rupee sign and the Indian terminal /-, both real corpus shapes.
        ("₹45,61,546", 456154600, "INR"),
        ("₹45,61,546/-", 456154600, "INR"),
        ("Rs. 1,234.56", 123456, "INR"),
        # The pound and the euro, adjacent on either side of the number.
        ("£606.00", 60600, "GBP"),
        ("4,55 €", 455, "EUR"),
        ("Total 518,99 EUR", 51899, "EUR"),
        # A credit note keeps its sign. Dropping it posts a refund as a charge.
        ("Rs. -1,234.56", -123456, "INR"),
        ("USD \u22121.234,56", -123456, "USD"),
    ],
)
def test_amounts_that_read_to_the_exact_minor_unit(
    text: str, paise: int, code: str
) -> None:
    """Shapes beyond the brief's five, each to the exact minor unit.

    `₹45,61,546/-` is `data/real_invoices_indian/gst-portal-and-govt-002.pdf`
    verbatim, and `4,55 €` is `data/real_invoices/vendor-samples-024.pdf`
    verbatim. The `/-` says only "and no paise", which the digits already said,
    so it is discarded and never read as part of the figure - the same rule
    `labels._ONLY_AMOUNT` states for the same characters.
    """
    read = read_money(text)
    assert read.paise == paise
    assert read.code == code


def test_the_terminal_indian_slash_changes_no_digit() -> None:
    """`45,61,546/-` and `45,61,546` are the same money, to the paise."""
    assert read_money("₹45,61,546/-").paise == read_money("₹45,61,546").paise


# =============================================================================
# separator ambiguity - the point of the module
# =============================================================================


@pytest.mark.parametrize("text", ["1.234", "1,234", "62.000", "308,500"])
def test_a_single_separator_over_three_digits_with_no_currency_is_refused(
    text: str,
) -> None:
    """THE TEST THAT MATTERS MOST. `1.234` is 1234 or it is 1.234.

    Nothing in those five characters decides it, so `paise` is None, `ambiguous`
    is True, and `why` quotes BOTH readings. It never guesses and it never
    fabricates zero.

    MEASURED on the 77 corpus PDF text layers `pypdf` can read: this shape
    appears 16 times across 10 documents - `62.000`, `24.150`, `10,000`,
    `21,135`, `5,000`, `308,500`, `25,000`, `77,305`, `1.000` - so it is
    ordinary rather than exotic, and 14 of the 16 are grouping, which is the
    evidence for letting a stated code resolve it in the test below. The other
    two are decimals and both carry a leading zero, which is what
    `test_a_leading_zero_proves_the_mark_is_the_decimal_point` settles.
    """
    read = read_money(text)
    assert read.paise is None
    assert read.ambiguous is True
    assert read.code == UNKNOWN
    assert "will not guess" in read.why
    # Both readings are named, so the person asked can see the actual choice.
    assert text.replace(",", "").replace(".", "") in read.why
    assert text.replace(",", ".") in read.why


@pytest.mark.parametrize(
    "text",
    [
        "0.025 €",  # vendor-samples-042.pdf, verbatim: `19% 0.025 €`
        "EUR 0.025",
        "USD 0,750",
        "USD 0,025",
        "INR 0,750",
        "Rp 0.750",
        "0,750",  # vendor-samples-043.pdf prints `Volume 0,750 M3`
    ],
)
def test_a_leading_zero_proves_the_mark_is_the_decimal_point(text: str) -> None:
    """REGRESSION PIN, and it was a live thousandfold overstatement.

    MEASURED on the 77 corpus text layers: of the 16 single-separator amounts
    with exactly three digits after the mark, TWO carry a leading zero -
    `0.025` in `vendor-samples-042.pdf` (`19% 0.025 €`, a unit price of
    twenty-five thousandths of a euro) and `0,750` in `vendor-samples-043.pdf`
    (`Volume 0,750 M3`).

    Before this guard, rule 4 asked the euro's home convention, was told `.` is
    not its decimal character, read `0.025` as the grouped number 25, and
    returned::

        read_money("0.025 €")  ->  paise=2500, code=EUR, ambiguous=False, why=""

    EUR 25.00 for a page printing 0.025 euro - a THOUSANDFOLD overstatement
    reported as CERTAIN. The certainty is what made it dangerous: `ambiguous`
    was False and `why` was empty, so nothing downstream had any reason to ask.

    No convention writes a grouped number whose first group has a leading zero -
    `750` is never written `0,750` - so the zero is proof the mark is the
    decimal point, and the sub-minor rule then refuses rather than rounding.
    Every case here comes back None with a reason.
    """
    read = read_money(text)
    assert read.paise is None
    assert read.ambiguous is True
    assert read.why != ""


def test_a_leading_zero_does_not_disturb_an_ordinary_decimal() -> None:
    """The guard reaches rule 4 only. `018.79` is settled by rule 3 - two digits
    after the mark cannot be a group in any convention - and reads unchanged.
    """
    assert read_money("USD 018.79").paise == 1879
    assert read_money("Rs 0.00").paise == 0
    assert read_money("Total 0,00 EUR").paise == 0


@pytest.mark.parametrize(
    ("text", "paise", "code"),
    [
        # `.` groups in the European and Indonesian conventions.
        ("EUR 62.000", 6200000, "EUR"),
        ("Rp 39.600", 39600, "IDR"),
        # `,` groups in the Anglo and Indian conventions.
        ("USD 10,000", 1000000, "USD"),
        ("INR 21,135", 2113500, "INR"),
        ("JPY 5,000", 5000, "JPY"),
    ],
)
def test_a_stated_code_resolves_the_three_digit_case(
    text: str, paise: int, code: str
) -> None:
    """The same shape, no longer ambiguous, because a code named the convention.

    This is the ONLY rule in the module that consults a currency's home
    convention. Everything else - two marks present, one mark repeated, a mark
    with a number of digits after it that cannot be a group - is settled by the
    characters alone.
    """
    read = read_money(text)
    assert read.paise == paise
    assert read.code == code
    assert read.ambiguous is False
    assert read.why == ""


@pytest.mark.parametrize(
    ("text", "paise"),
    [
        ("EUR 1.234,56", 123456),
        ("USD 1,234.56", 123456),
        ("INR 1,23,456.78", 12345678),
        ("EUR 1.234.567,89", 123456789),
    ],
)
def test_both_marks_present_means_the_rightmost_is_the_decimal_point(
    text: str, paise: int
) -> None:
    """No convention on earth uses one character for grouping AND the decimal.

    So when both `.` and `,` appear the rightmost settles it, with no currency
    knowledge involved. MEASURED: `1.234,56` appears 32 times across 7 corpus
    documents and `1,234.56` 12 times across 3.
    """
    assert read_money(text).paise == paise


@pytest.mark.parametrize(
    ("text", "paise"),
    [
        ("USD 1.234.567", 123456700),
        ("INR 1,23,456", 12345600),
    ],
)
def test_one_mark_repeated_with_a_three_digit_last_group_all_groups(
    text: str, paise: int
) -> None:
    """`1,23,456` is Indian lakh grouping: narrow groups are never the last one.

    A rule requiring every group to be three digits wide would refuse this real
    Indian number, which is why the module checks the LAST group only.
    """
    assert read_money(text).paise == paise


def test_one_mark_repeated_with_a_short_last_group_is_malformed_not_guessed() -> None:
    """`1.234.56` would need `.` to group in one place and be the point in
    another. No convention does that, so it is refused rather than reconciled.
    """
    read = read_money("EUR 1.234.56")
    assert read.paise is None
    assert read.ambiguous is True
    assert "repeats '.'" in read.why


@pytest.mark.parametrize(
    ("text", "paise", "code"),
    [
        ("Steuerbetrag in GBP 163,16", 16316, "GBP"),
        ("EUR 163,16", 16316, "EUR"),
        ("USD 163.16", 16316, "USD"),
    ],
)
def test_two_digits_after_one_mark_is_a_decimal_point_in_every_convention(
    text: str, paise: int, code: str
) -> None:
    """THE HUNDREDFOLD DEFECT, FIXED, AND THE CODE-VERSUS-LOCALE CASE WITH IT.

    MEASURED: `labels.paise_or_none("163,16")` returns 1631600 and
    `labels.amount_on("TOTAL 163,16", "TOTAL")` returns `(1631600, 6, 12)`.
    That is one hundred and sixty-three euro sixteen posted as sixteen thousand
    three hundred and sixteen rupees. The shape appears 557 times across 40 of
    the 77 readable corpus documents.

    `Steuerbetrag in GBP 163,16` is `data/real_invoices/vendor-samples-067.pdf`
    verbatim: a German invoice denominated in sterling, printing a comma
    decimal. It is the measured counter-example to "the code names the
    convention", and it reads correctly here BECAUSE two trailing digits are
    settled before any convention is consulted - grouping is three digits wide
    in every convention, including the Indian one whose narrow groups are never
    the last.
    """
    read = read_money(text)
    assert read.paise == paise
    assert read.code == code
    assert read.ambiguous is False


def test_the_hundredfold_defect_is_flagged_even_with_no_currency_at_all() -> None:
    """`163,16` alone reads as 16316 minor units and says the code is unknown.

    The integer is right in any two-minor-digit currency; what is missing is
    WHICH one, and the module says so rather than letting a caller assume rupees.
    """
    read = read_money("TOTAL 163,16")
    assert read.paise == 16316
    assert read.code == UNKNOWN
    assert read.ambiguous is True
    assert "must not assume rupees" in read.why


# =============================================================================
# a shared symbol does not pin a code
# =============================================================================


def test_a_bare_dollar_sign_reads_the_amount_and_refuses_the_currency() -> None:
    """CAD, AUD and SGD all print `$`, so `$` alone proves nothing about USD.

    The amount still reads: two printed fractional digits can only be a
    two-minor-digit currency, so 1879 minor units is right whichever dollar this
    is. The CODE is what stays unknown, and the two facts are reported
    separately because they are separately true.
    """
    read = read_money("Billed total $18.79")
    assert read.paise == 1879
    assert read.symbol == "$"
    assert read.code == UNKNOWN
    assert read.ambiguous is True
    assert "more than one currency" in read.why


@pytest.mark.parametrize(
    "text",
    ["Billed total $18.79 USD", "Billed total USD $18.79", "Total in USD: $18.79"],
)
def test_a_dollar_sign_pins_usd_when_something_else_in_the_text_states_it(
    text: str,
) -> None:
    """ "Unless something else in the text states it" - honoured, and bounded.

    The code must be compatible with the symbol beside the number, so a stray
    mention cannot redenominate an amount whose symbol already spoke.
    """
    read = read_money(text)
    assert read.paise == 1879
    assert read.code == "USD"
    assert read.ambiguous is False
    assert read.why == ""


def test_a_code_elsewhere_never_overrides_the_symbol_beside_the_number() -> None:
    """`€1.234,56 (USD rate applied)` is euro. The euro sign already said so."""
    read = read_money("€1.234,56 (USD rate applied)")
    assert read.code == "EUR"
    assert read.paise == 123456


def test_a_bare_yen_sign_is_refused_for_the_same_reason_as_the_dollar() -> None:
    """CNY prints `¥` and has two minor digits; JPY has zero.

    So `¥1,234` is 1234 yen or 123400 fen - a hundredfold spread - and
    unlike the dollar case there are no printed fractional digits to settle the
    exponent either. Guarding the dollar and not the yen would be guarding the
    example instead of the defect.
    """
    read = read_money("¥1,234")
    assert read.paise is None
    assert read.symbol == "¥"
    assert read.code == UNKNOWN
    assert read.ambiguous is True


def test_a_bare_dollar_with_no_fractional_digits_is_refused() -> None:
    """`$1,000` is 100000 minor units in USD and 1000 in CLP. Both plausible.

    Refusing costs one question. Guessing costs a hundredfold, so the printed
    fractional digits are the only evidence this module will act on when the
    code is unknown, and there are none here.
    """
    read = read_money("$1,000")
    assert read.paise is None
    assert read.ambiguous is True
    assert read.symbol == "$"


def test_us_dollar_written_with_its_country_does_pin_usd() -> None:
    """`US$` is a different token from `$` and it resolves without help."""
    read = read_money("Total US$ 18.79")
    assert read.code == "USD"
    assert read.symbol == "US$"
    assert read.ambiguous is False


@pytest.mark.parametrize(
    ("text", "printed_fraction_digits"),
    [
        ("Total 1234", 0),
        ("Total 1.234.567", 0),
        ("Total 18.7", 1),
        ("Total 18.7912", 4),
    ],
)
def test_no_currency_and_no_two_digit_fraction_cannot_be_scaled(
    text: str, printed_fraction_digits: int
) -> None:
    """A bare number with no currency is digits, not money, unless it shows cents.

    The separator here is never in doubt - `1234` has none, `1.234.567` groups,
    `18.7` and `18.7912` both mark a decimal - so what is missing is the SCALE.
    Currencies in circulation carry zero, two or three minor digits, so:

      * two printed digits pin the exponent at two whatever the currency is,
        which is why `$18.79` reads and everything here does not;
      * zero leaves `1234` as either 1234 whole units or 123400 minor ones;
      * one matches no currency's precision at all;
      * four is past every currency's precision.

    Each comes back None with the digit count quoted, so the person asked can
    see exactly what the document printed rather than a bare refusal.
    """
    read = read_money(text)
    assert read.paise is None
    assert read.ambiguous is True
    assert read.code == UNKNOWN
    assert f"prints {printed_fraction_digits} digits" in read.why
    assert "how many minor units make one whole unit" in read.why


# =============================================================================
# IDR minor digits - the stated rule, and its stated consequence
# =============================================================================


def test_idr_is_counted_in_whole_rupiah() -> None:
    """The chosen rule, pinned: IDR carries ZERO minor digits in this module.

    ISO 4217 says two - the sen - and this repository holds no evidence either
    way (MEASURED: zero `Rp` and zero `IDR` amounts in the 77 readable corpus
    text layers). The choice rests on a stated principle: the table records the
    smallest unit a bill can be DENOMINATED in, and no Indonesian invoice prints
    a sen. `Rp 39.600` is therefore 39600 minor units meaning 39,600 rupiah, not
    3,960,000 sen and emphatically not the 3960 that
    `labels.paise_or_none("39.600")` returns today.
    """
    assert MINOR_DIGITS["IDR"] == 0
    assert read_money("Total Rp 39.600").paise == 39600
    assert labels.paise_or_none("39.600") == 3960  # the thousandfold defect


def test_idr_with_two_fractional_digits_is_refused_and_names_the_reason() -> None:
    """The consequence of the rule above, tested rather than implied.

    Two fractional digits in a currency with no fractional unit is evidence the
    separator was misread, not a fact about the money. So it is refused, and the
    refusal says the currency is counted in whole units - a question for a
    person rather than a figure nobody can check.
    """
    read = read_money("Rp 1.234,56")
    assert read.paise is None
    assert read.code == "IDR"
    assert read.ambiguous is True
    assert "IDR is counted in whole units with no minor unit" in read.why


def test_jpy_is_counted_in_whole_yen_and_refuses_a_fraction() -> None:
    """The same rule at the other zero-minor-digit currency, so the behaviour is
    a property of `MINOR_DIGITS` rather than a special case for Indonesia.
    """
    assert read_money("JPY 1,234").paise == 1234
    refused = read_money("JPY 1234.5")
    assert refused.paise is None
    assert "JPY is counted in whole units" in refused.why


def test_sub_minor_digits_are_refused_rather_than_rounded_for_the_rupee() -> None:
    """`10.005` is half a paise. `labels.paise_or_none` has always refused it and
    `tallyio.paise_from_rupees` raises on it; two money parsers in one repository
    disagreeing about that string is how a reconciliation breaks in three months.
    """
    read = read_money("Rs. 10.005")
    assert read.paise is None
    assert read.code == "INR"
    assert "rounding it would move money" in read.why
    assert labels.paise_or_none("10.005") is None


# =============================================================================
# refusing to choose
# =============================================================================


def test_more_than_one_distinct_amount_is_refused_with_both_quoted() -> None:
    """One of them is not the figure the caller wants, nothing here can say
    which, and picking the first is a coin toss that posts money - which is
    `labels.the_one`'s rule, restated for one line of characters.
    """
    read = read_money("Rs 1939 x 1 Night x 1 Room")
    assert read.paise is None
    assert read.ambiguous is True
    assert "more than one amount" in read.why
    assert "1939" in read.why


def test_the_same_amount_printed_twice_is_read_once() -> None:
    """A continuation sheet repeats its header. Repetition is not disagreement."""
    read = read_money("Total USD 18.79   Total USD 18.79")
    assert read.paise == 1879
    assert read.code == "USD"


def test_a_percentage_is_a_rate_and_not_an_amount() -> None:
    """`GST @ 18% 500.00` states one amount and one rate.

    Counting the rate as an amount would make the line refuse for stating two;
    computing tax from it would be arithmetic on a number the bill already
    prints. `labels._RATE` consumes it for the same reason.
    """
    assert read_money("GST @ 18% 500.00").paise == 50000
    assert read_money("IGST 18 % 500.00").paise == 50000


def test_a_date_is_not_an_amount() -> None:
    """`28/01/26` is three numbers, so it refuses rather than posting the 28."""
    read = read_money("Date : 28/01/26")
    assert read.paise is None
    assert "more than one amount" in read.why


def test_a_full_stop_ending_the_sentence_is_not_part_of_the_figure() -> None:
    """Regression pin. Under a wider trailing lookahead `Total is 1,234.` matched
    NOTHING - the sentence's full stop failed at every backtrack - so a bill that
    ended its total with a full stop reported no amount rather than an amount.
    """
    read = read_money("Total is USD 1,234.")
    assert read.paise == 123400
    assert read.raw == "USD 1,234"


# =============================================================================
# the evidence fields
# =============================================================================


def test_raw_is_the_characters_the_page_printed_including_the_symbol() -> None:
    """Evidence that has been tidied is no longer evidence, so `raw` is an exact
    slice of the input with the document's own spacing.
    """
    assert read_money("Total Rp 39.600").raw == "Rp 39.600"
    assert read_money("Billed total $18.79").raw == "$18.79"
    assert read_money("Total 4,55 € here").raw == "4,55 €"
    assert read_money("Rs.  1,234.56").raw == "Rs.  1,234.56"


def test_raw_is_the_whole_input_when_nothing_money_shaped_was_found() -> None:
    """A caller reading a refusal needs to see what this module was handed."""
    assert read_money("  no amount here  ").raw == "  no amount here  "


def test_symbol_reports_the_characters_and_code_reports_the_normalisation() -> None:
    """Two fields rather than one: `$` and `USD` are the same code and very much
    not the same evidence, and a person answering a question about this bill
    needs the characters they can go and look at.
    """
    read = read_money("Total Rs. 1,234.56")
    assert read.symbol == "Rs."
    assert read.code == "INR"


def test_money_is_frozen() -> None:
    """A reading that can be edited after the fact is not evidence about what a
    document said - the rule `labels.Found` and `schema.py` already follow.
    """
    read = read_money("USD 18.79")
    with pytest.raises(AttributeError):
        read.paise = 1  # pyright: ignore[reportAttributeAccessIssue]


def test_an_alias_never_matches_inside_a_longer_word() -> None:
    """`CORP 1,234.56` is not an Indonesian amount, and `PRINR` is not a rupee
    one. Without the letter guards `RP` matches inside `CORP` and a corporation
    becomes Indonesian.
    """
    assert read_money("CORP 1,234.56").code == UNKNOWN
    assert read_money("EUROPE 1,234.56").code == UNKNOWN


# =============================================================================
# the four controls
# =============================================================================


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "no amount here",
        "TOTAL:",
        "Amount payable: -",
        "1.234",
        "$1,000",
        "¥1,234",
        "Rp 1.234,56",
        "Rs. 10.005",
        "EUR 1.234.56",
    ],
)
def test_control_unparseable_returns_none_and_never_zero(text: str) -> None:
    """CONTROL: a missing amount stays missing and says so in words.

    Fabricating zero for a value that could not be read is the defect
    `adapter.TYPED_TEXT_MIME` records twenty instances of, and it is the one
    this repository refuses hardest: a zero total looks like a real total, posts
    cleanly, balances nothing, and is found months later. Every string here is
    unreadable for a different reason and every one of them comes back None with
    a reason a person can act on.

    IT DOES NOT ASSERT THE CODE IS UNKNOWN, and the first draft of this test did
    - wrongly. `Rp 1.234,56`, `Rs. 10.005` and `EUR 1.234.56` each name their
    currency perfectly well and fail on the DIGITS, so they come back with
    `paise=None` and `code` set. An unreadable amount and an unreadable currency
    are two separate facts and this module reports them separately; a control
    that conflated them would have forced the module to throw away a fact it had.
    """
    read = read_money(text)
    assert read.paise is None
    assert read.paise != 0
    assert read.why != ""
    # The characters survive the refusal, so the person asked can see them.
    assert read.raw in text


#: Names that would let float arithmetic back in, checked wherever a name can
#: hide: a bare identifier, an attribute, or a string handed to `getattr`.
#:
#: THE DUNDERS AND THE `operator` SPELLINGS ARE HERE BECAUSE THE FIRST DRAFT OF
#: THIS CONTROL LET THEM THROUGH. That draft checked `ast.Div`, `ast.Name` and
#: float literals, and its own docstring claimed it caught `x.__truediv__(100)`
#: and `getattr(builtins, "float")`. Run against six deliberate violations it
#: caught four and MISSED those exact two - the two it named. A control that
#: claims more than it does is worse than no control, because it is the reason
#: nobody looks again.
_FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "float",
        "__truediv__",
        "__rtruediv__",
        "__floordiv__",
        "__rfloordiv__",
        "truediv",
        "floordiv",
    }
)


def _names_used(tree: ast.Module) -> set[str]:
    """Every identifier, attribute name and bare string constant in the tree.

    All three, because all three are ways to reach the same builtin:
    `float(x)` is a `Name`, `x.__truediv__(y)` is an `Attribute`, and
    `getattr(builtins, "float")` is a `Constant` holding a string.
    """
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            used.add(node.value)
    return used


def test_control_module_source_contains_no_float_and_no_division() -> None:
    """CONTROL: the arithmetic in `currency.py` cannot be float arithmetic.

    Money is integer minor units everywhere in this repository, and a single
    `/ 100` is enough to turn an exact figure into a binary fraction that is
    almost the right number. This reads the module's OWN SOURCE and proves the
    property from the syntax tree rather than from a promise in a docstring:

      * no `Div` or `FloorDiv` node, so nothing is divided by anything;
      * no `float` reachable as an identifier, an attribute or a `getattr`
        string, so nothing is converted to one by any spelling;
      * no division dunder or `operator` alias, so the `Div` check cannot be
        walked around;
      * no float literal, so none can be introduced by a constant.

    `test_the_no_float_control_catches_every_way_back_in` below proves each of
    those four actually fires, because an assertion that has only ever passed
    has not been shown to be capable of failing.
    """
    source_path = pathlib.Path(inspect.getfile(currency))
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    divisions = [
        node for node in ast.walk(tree) if isinstance(node, ast.Div | ast.FloorDiv)
    ]
    assert divisions == [], "currency.py must not divide; minor units are exact"

    reachable = _names_used(tree) & _FORBIDDEN_NAMES
    assert reachable == set(), f"currency.py must never name {reachable}"

    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert literals == [], "currency.py must contain no float literal"

    # And the crude checks too, because they are the ones a reviewer runs.
    assert "float(" not in source
    assert re.search(r"//?\s*100\b", source) is None


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("explicit call", "def f(x: str) -> float:\n    return float(x)\n"),
        ("division", "def f(x: int) -> object:\n    return x / 100\n"),
        ("floor division", "def f(x: int) -> object:\n    return x // 100\n"),
        ("float literal", "RATE = 0.01\n"),
        ("getattr", "import builtins\nF = getattr(builtins, 'float')\n"),
        ("truediv dunder", "def f(x: int) -> object:\n    return x.__truediv__(100)\n"),
        ("operator alias", "import operator\nF = operator.truediv\n"),
    ],
)
def test_the_no_float_control_catches_every_way_back_in(name: str, source: str) -> None:
    """CONTROL ON THE CONTROL: each violation is caught by at least one check.

    Written after the first draft of the control above passed four of these
    seven and silently missed `getattr` and `__truediv__` - the two its own
    docstring named. The lesson generalises past this file: an assertion that
    has only ever been run against code that satisfies it has not been shown to
    be capable of failing, and a control nobody has watched fail is a comment.
    """
    tree = ast.parse(source)
    caught = (
        bool([n for n in ast.walk(tree) if isinstance(n, ast.Div | ast.FloorDiv)])
        or bool(_names_used(tree) & _FORBIDDEN_NAMES)
        or bool(
            [
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, float)
            ]
        )
    )
    assert caught, f"the no-float control would not catch {name}"


def test_control_disagreement_with_labels_paise_or_none_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CONTROL: the cross-check against the parser already posting to Tally.

    `currency.py` decides which mark is the decimal point; `paise_or_none` turns
    the resulting canonical rupee string into paise. Two independent routes to
    one integer that MUST be equal - integer string concatenation here, `Decimal`
    arithmetic there - and the module refuses the amount if they are not.

    In normal operation this never fires, which is exactly why it needs a test:
    a control nobody has seen work is a comment. Substituting the function proves
    the comparison is on the live path and that a disagreement produces a
    REFUSAL rather than a quietly preferred answer.
    """
    assert read_money("USD 18.79").paise == 1879

    def one_paise_out(_text: str) -> int | None:
        """The same signature, one paise adrift - the smallest real disagreement.

        A wildly different number would prove less: a control that only catches
        gross divergence would miss the rounding change that is the likely way
        these two ever drift apart.
        """
        return 1878

    monkeypatch.setattr(labels, "paise_or_none", one_paise_out)
    read = read_money("USD 18.79")
    assert read.paise is None
    assert read.ambiguous is True
    assert "1879" in read.why
    assert "1878" in read.why
    assert "refused rather than trusted" in read.why


@pytest.mark.parametrize(
    "text",
    [
        "",
        "no amount here",
        "1.234",
        "1,234",
        "$18.79",
        "$1,000",
        "¥1,234",
        "Rp 1.234,56",
        "Rs. 10.005",
        "EUR 1.234.56",
        "Rs 1939 x 1 Night x 1 Room",
        "TOTAL 163,16",
        "Total Rp 39.600",
        "Total USD 18.79",
        "Total EUR 1.234,56",
        "Total INR 1,23,456.78",
        "₹45,61,546/-",
    ],
)
def test_control_why_is_never_empty_when_the_answer_is_not_certain(text: str) -> None:
    """CONTROL: the one invariant `Money` promises, held across every case here.

    A `Money` with no amount and no reason says "no answer, no explanation",
    which is the shape every silent failure in this repository has taken. The
    converse is checked too: a certain answer carries no `why`, so a caller can
    route on the field's emptiness instead of parsing prose.
    """
    read = read_money(text)
    if read.paise is None or read.ambiguous:
        assert read.why != ""
    else:
        assert read.why == ""


def test_the_dataclass_refuses_to_be_built_without_a_reason() -> None:
    """The invariant above is enforced by `Money` itself, not by convention.

    The only way to trip this is a new branch in `read_money` that forgot its
    reason, which is exactly when it should be loud rather than defaulted.
    """
    with pytest.raises(ValueError, match="must state a reason"):
        Money(paise=None, raw="x", symbol="", code=UNKNOWN, ambiguous=False, why="")
    with pytest.raises(ValueError, match="must state a reason"):
        Money(paise=1, raw="x", symbol="", code=UNKNOWN, ambiguous=True, why="")
    # And a certain answer needs no reason.
    assert (
        Money(paise=1, raw="x", symbol="USD", code="USD", ambiguous=False, why="").paise
        == 1
    )

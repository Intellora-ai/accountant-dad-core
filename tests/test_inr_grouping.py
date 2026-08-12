"""Every rupee figure a person reads is grouped the Indian way, in one place.

WHY THIS FILE EXISTS
--------------------
Python's `f"{n:,}"` groups digits in threes. India does not. Ten lakh is
written `10,00,000` - the last three digits, then groups of TWO - and every
figure this product printed above ninety-nine thousand was grouped the wrong
way for the only people who will ever read it:

    ₹10,00,000.00   what an Indian accountant reads
    ₹1,000,000.00   what this code printed until 2026-08-13

That is not a style preference. A person checking a trial balance against a
bank statement reads the comma positions before they read the digits, and two
different grouping conventions on the same screen is how a figure gets
misread as ten times itself.

THE SIGN POSITION IS NOT PART OF THIS
--------------------------------------
A negative still prints `₹-4,200.50`, with the minus inside the symbol. An
Indian statement writes `-₹4,200.50`, and
`tests/test_app_coverage_b.py::test_the_rupee_sign_goes_in_front_of_the_minus_sign`
has said so in its own docstring since the day it was written - as MEASURED,
NOT ENDORSED, reported to the owner and left alone.

It is still left alone. The owner ruled on the GROUPING and said nothing about
the sign, and a change authorised to fix the commas does not get to carry a
second opinion in with it. The test that pins the current position passes, and
a passing test named for the behaviour it holds is not weakened to suit a
neighbouring fix. The question is open in `docs/OWNER_WORK.md`; consolidating
onto one renderer makes it a one-line answer whenever it is asked.

WHY ONE FUNCTION AND NOT FOUR
------------------------------
There were four renderers. `web/app.py::rupees`, `web/app.py::money`,
`questions.py::rupees` and `taxonomy/report.py::crore` each grouped their own
digits, and they did not agree with each other: the question text dropped the
paise entirely, so a person was asked about `₹3,800` for a figure the same
page showed as `₹3,800.47`.

Four renderers means four places to get it wrong and four places a future fix
has to reach. `accountant/money.py::format_inr` is now the only one, and the
tests below are written against it rather than against any caller, so a fifth
renderer appearing somewhere cannot quietly pass by copying the first four.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That the Indian convention is the right one for every reader. It is an owner
decision, taken 2026-08-13, and it is not derived from anything here. A
company selling into a market that groups in threes would need this reopened,
not worked around at the call site.

That every screen calls it. This pins the function and the four call sites
that exist today. A new call site that writes its own `f"{n:,}"` is caught by
nothing in this file - `test_no_source_file_groups_rupees_the_western_way`
below is the guard for that, and it reads the source, not the output.

Nothing here touches TALLY'S WIRE FORMAT, which is a separate decision.
`tallyio/real.py::rupees_from_paise` writes `<AMOUNT>4200.50</AMOUNT>` with no
separators at all, because Tally's parser is not a person. Grouping that
string would break the write path, and the test at the bottom of this file
pins it as deliberately excluded rather than overlooked.

EVIDENCE CLASS
--------------
`SYNTHETIC_EVIDENCE`. Every amount below is written in this file. No real
company, no real bill, no accuracy claim.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from accountant import money as M
from accountant import questions as Q
from accountant.tallyio import real
from accountant.taxonomy import report as taxreport
from accountant.web import app

# ---- the five the owner named ----------------------------------------------


@pytest.mark.parametrize(
    ("paise", "shown"),
    [
        (100_000, "₹1,000.00"),
        (1_000_000, "₹10,000.00"),
        (10_000_000, "₹1,00,000.00"),
        (100_000_000, "₹10,00,000.00"),
        (1_000_000_000, "₹1,00,00,000.00"),
    ],
)
def test_the_five_landmark_amounts_are_grouped_the_indian_way(
    paise: int, shown: str
) -> None:
    """One thousand up to one crore, each a decade apart.

    These five are the owner's own list. They are the amounts where the two
    conventions first disagree and then keep disagreeing: `₹1,000.00` and
    `₹10,000.00` are identical either way, and everything above them is not.
    """
    assert M.format_inr(paise) == shown


@pytest.mark.parametrize("paise", [10_000_000, 100_000_000, 1_000_000_000])
def test_the_control_python_own_grouping_disagrees_at_a_lakh_and_above(
    paise: int,
) -> None:
    """THE CONTROL. Without it the test above passes on `f"{n:,}"`.

    A lakh is where the two conventions part. Below it they are the same
    string, which is exactly why the defect survived: every amount anybody
    typed while testing rendered correctly by accident.
    """
    western = f"₹{paise // 100:,}.{paise % 100:02d}"

    assert M.format_inr(paise) != western


# ---- the boundaries: zero, sub-rupee, sign, and the largest there is --------


def test_zero_carries_the_symbol_and_both_decimals_and_no_sign() -> None:
    """A balanced ledger line is `₹0.00`, not `₹0`, and never `-₹0.00`.

    A formatter written `"-" if paise <= 0` prints a debt of nothing, which is
    the one figure on a trial balance that must be unmistakable.
    """
    assert M.format_inr(0) == "₹0.00"
    assert not M.format_inr(0).startswith("-")


@pytest.mark.parametrize(
    ("paise", "shown"), [(1, "₹0.01"), (99, "₹0.99"), (-1, "₹-0.01")]
)
def test_an_amount_below_one_rupee_still_prints_its_whole_part(
    paise: int, shown: str
) -> None:
    """`₹.01` is a different string from `₹0.01` and reads as a typo.

    The paise are also the half that a truncating formatter drops in silence:
    `questions.py::rupees` printed `₹0` for all three of these.
    """
    assert M.format_inr(paise) == shown


def test_a_negative_puts_its_minus_sign_inside_the_rupee_symbol() -> None:
    """The sign position is UNCHANGED and deliberately so - see the module
    docstring. Pinned here as well as in `tests/test_app_coverage_b.py` because
    the renderer moved to a new module, and a property that survives a move
    only by luck is a property nobody is holding."""
    assert M.format_inr(-420_050) == "₹-4,200.50"
    assert M.format_inr(420_050) == "₹4,200.50"


def test_the_largest_amount_that_can_be_held_in_paise_is_still_grouped() -> None:
    """A signed 64-bit paise count is about ₹9.2 quadrillion.

    `tests/test_adversarial_amounts_and_states.py` already pins this number as
    the ceiling of what can arrive. Seventeen digits is where a grouping loop
    written for four-digit test data falls over, so it is pinned here too.
    """
    assert M.format_inr(9_223_372_036_854_775_815) == "₹92,23,37,20,36,85,47,758.15"
    assert M.format_inr(-9_223_372_036_854_775_815) == "₹-92,23,37,20,36,85,47,758.15"


# ---- what it refuses, which is the reason it is trusted ---------------------


@pytest.mark.parametrize(
    "value", [420_050.0, 10.5, "420050", None, [420_050], object()]
)
def test_an_amount_that_is_not_integer_paise_is_refused_and_the_type_is_named(
    value: object,
) -> None:
    """Money is integer paise everywhere in this repository or it is refused.

    `420_050.0` is the case worth having: a float that IS a whole number of
    paise. A formatter lenient about it accepts the exact class of value that
    `checks.py::amount_is_integer_paise` exists to catch, and does so on the
    inputs where the leniency leaves no trace.
    """
    with pytest.raises(TypeError, match="integer paise") as refused:
        M.format_inr(value)  # type: ignore[arg-type]

    assert type(value).__name__ in str(refused.value)


def test_the_control_the_same_amount_as_an_integer_renders_without_complaint() -> None:
    """THE CONTROL for the refusals above: it is the TYPE being refused and not
    the number. `420050` and `420050.0` are the same quantity."""
    assert M.format_inr(420_050) == "₹4,200.50"


def test_both_boolean_values_render_as_the_integers_they_are() -> None:
    """`bool` is an `int` in Python, so it reaches the formatter rather than
    the refusal. `False` is the half that would still render if the branch
    were written `if paise is True`."""
    assert M.format_inr(True) == "₹0.01"
    assert M.format_inr(False) == "₹0.00"


# ---- the grouping primitive, on its own -------------------------------------


@pytest.mark.parametrize(
    ("digits", "grouped"),
    [
        ("0", "0"),
        ("7", "7"),
        ("999", "999"),
        ("1000", "1,000"),
        ("10000", "10,000"),
        ("100000", "1,00,000"),
        ("1000000", "10,00,000"),
        ("10000000", "1,00,00,000"),
        ("100000000", "10,00,00,000"),
    ],
)
def test_the_grouping_walks_three_then_twos_all_the_way_up(
    digits: str, grouped: str
) -> None:
    """`group_indian` is separate from `format_inr` because the crore report
    needs the grouping without the symbol or the paise. Pinned on its own so
    that a change to one cannot silently move the other."""
    assert M.group_indian(digits) == grouped


@pytest.mark.parametrize("digits", ["100000", "1000000", "10000000"])
def test_the_control_the_grouping_is_not_pythons_own(digits: str) -> None:
    """THE CONTROL. `f"{n:,}"` returns the same string for everything below a
    lakh, so a test that only used small numbers would pass on either."""
    assert M.group_indian(digits) != f"{int(digits):,}"


# ---- the call sites, each pinned where a person actually reads it -----------


def test_the_trial_balance_and_the_draft_card_print_indian_groups() -> None:
    """`web/app.py::money` is the formatter every HTML page goes through.

    `rupees` is the same string without the symbol - it exists because one
    template placed the ₹ itself - and both come from `format_inr`, so they
    cannot drift apart the way the four originals did.
    """
    assert app.money(100_000_000) == "₹10,00,000.00"
    assert app.rupees(100_000_000) == "10,00,000.00"
    assert app.money(-420_050) == "₹-4,200.50"


def test_a_value_the_page_cannot_render_is_still_not_allowed_to_raise() -> None:
    """`money` degrades and never throws: a non-integer amount is what makes an
    entry NOT_VALID, so the draft a person MOST needs to see was the one that
    raised while being drawn. That property survives the new formatter."""
    assert app.money(10.5) == "10.5 (not an amount)"
    assert app.money(None) == "None (not an amount)"


def test_the_question_about_a_large_payment_shows_indian_groups_and_the_paise() -> None:
    """`questions.py::rupees` floored to whole rupees AND grouped westerly, so
    a ₹20 lakh payment was read back to the person as `₹2,000,000`.

    Two defects in one line: the wrong commas, and a figure that does not match
    the one on the screen it was asked about.
    """
    q = Q.is_that_amount_right("Sharma Traders", 200_000_000, 380_047)

    assert "₹20,00,000.00" in q.text
    assert "₹3,800.47" in q.text
    assert "₹2,000,000" not in q.text


def test_the_crore_report_groups_a_six_figure_crore_amount_the_indian_way() -> None:
    """The audit findings top out at 31,308 crore today, where the two
    conventions still agree. They part at six digits, so that is the figure
    pinned here - a report is wrong before the data reaches it, not after."""
    from accountant.taxonomy.findings import paise_from_crore

    assert taxreport.crore(paise_from_crore(123_456, 78)) == "1,23,456.78 crore"
    assert taxreport.crore(paise_from_crore(1719, 44)) == "1,719.44 crore"


# ---- what is deliberately NOT grouped ---------------------------------------


@pytest.mark.parametrize("paise", [100_000_000, -100_000_000, 420_050])
def test_the_amount_tally_receives_carries_no_separators_and_no_symbol(
    paise: int,
) -> None:
    """DELIBERATELY EXCLUDED, and pinned so nobody "finishes the job".

    `<AMOUNT>` is read by Tally's XML parser, which is not a person. A comma
    or a ₹ in that field is a parse failure or a silently different number,
    and either one lands in somebody's statutory books. The wire format is a
    separate decision from the screen format and this test is what keeps them
    separate.
    """
    rendered = real.rupees_from_paise(paise)

    assert "," not in rendered
    assert "₹" not in rendered


def _western_format_specs(tree: ast.AST) -> list[int]:
    """Line numbers of every f-string field whose format spec groups digits.

    Parsed, not grepped: `web/app.py::rupees` quotes the expression it
    replaced inside its own docstring, and a grep cannot tell that apart from
    the live code it is warning about.
    """
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.FormattedValue) and node.format_spec is not None
        for part in ast.walk(node.format_spec)
        if isinstance(part, ast.Constant) and "," in str(part.value)
    ]


def test_no_source_file_groups_digits_the_western_way() -> None:
    """The guard for the FIFTH renderer, which does not exist yet.

    Every test above reads output. This one reads source, so a new screen that
    writes its own `f"{whole:,}"` fails here rather than shipping grouped in
    threes to an Indian accountant.
    """
    root = pathlib.Path(__file__).resolve().parent.parent / "accountant"
    offenders = [
        f"{path.relative_to(root)}:{n}"
        for path in sorted(root.rglob("*.py"))
        for n in _western_format_specs(ast.parse(path.read_text()))
    ]

    assert offenders == [], f"western grouping reached a person: {offenders}"

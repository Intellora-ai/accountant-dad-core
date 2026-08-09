"""A1-A4 — money is integer paise, or it is refused. Never rounded quietly.

The project's own rule, from the frozen plan's shared types:

    amount_paise: int     # integer paise, never float

`accountant/tallyio/real.py` has kept it since it was written:
`paise_from_rupees` parses with `Decimal` and REFUSES sub-paise precision
rather than rounding it away, because rounding invoice arithmetic is how a
reconciliation breaks three months later.

The reader did not. `extract/adapter.py` used
`round(float(text.replace(",", "")) * 100)`, and the amount regex captured at
most two decimal places, so the same string the connector refuses was silently
accepted, truncated, and posted as VALID. Two components, one rule, opposite
behaviour — and the one a person's typing reaches first was the lenient one.

MEASURED BEFORE THE FIX, all four:

    A1  "92233720368547.75"  -> 9223372036854775 paise via float, wrong above
                                ~₹99,999,999,999,999.99
    A2  "10.005"             -> 1000 paise. VALID. POSTED. The half-paise is
                                gone and nothing anywhere records that it was
    A3  rupees(-420050)      -> "-4,201.50". `//` floors toward -infinity, so
                                every negative amount on the page is a rupee
                                too far from zero
    A4  a bool or a float reaching `_check_writable` was caught one line later
        by a format code, in a message naming no voucher and no amount

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That any of these ever reached a real company's books. Nobody has typed
"10.005" into the live app. This proves the input is refused, not that it was
ever offered.
"""

from __future__ import annotations

import datetime
from dataclasses import replace

import pytest

from accountant.extract.adapter import TypedTextExtractor
from accountant.tallyio.real import TallyDataError, paise_from_rupees
from accountant.web import app

READER = TypedTextExtractor()


def read(text: str):
    return READER.extract(text.encode(), "text/plain")


# ---- A1 / A2: the reader and the connector agree, or the reader refuses -----


@pytest.mark.parametrize(
    ("typed", "paise"),
    [
        ("paid Sharma Traders 4200 for cement", 420_000),
        ("paid Sharma Traders 4,200.75 for cement", 420_075),
        ("paid Sharma Traders 0.01 for cement", 1),
        # The value `float` cannot hold. 92233720368547.75 rupees is beyond the
        # point where a double has 0.01 resolution, so `round(float(x) * 100)`
        # returns a different integer from the exact one.
        ("paid Sharma Traders 92233720368547.75 for cement", 9_223_372_036_854_775),
    ],
)
def test_the_reader_returns_the_exact_paise_the_connector_would_parse(
    typed: str, paise: int
):
    record = read(typed)
    assert record.total_paise == paise
    # The disconfirming half: the two components must not merely each be
    # self-consistent, they must agree with each other on the same string.
    rupee_text = typed.split()[3]
    assert paise_from_rupees(rupee_text) == paise


@pytest.mark.parametrize(
    "typed",
    [
        "paid Sharma Traders 10.005 for cement",
        "paid Sharma Traders 1.2345 for cement",
        "paid Sharma Traders 0.001 for cement",
    ],
)
def test_a_sub_paise_amount_is_refused_by_the_reader_and_never_truncated(typed: str):
    """A2. The connector already refuses these strings; the reader now does too.

    Refusing is an `amount_paise` of None, not an exception: an unreadable
    amount is a question for the person, and the pipeline already turns a
    missing total into one. An exception here would be a 500 in the web app.
    """
    record = read(typed)
    assert record.total_paise is None, "a truncated amount must never be produced"
    assert record.per_field_source["total_paise"] == "not_found"

    # And the connector agrees, on the same string, which is the point.
    with pytest.raises(TallyDataError, match="sub-paise"):
        paise_from_rupees(typed.split()[3])


def test_the_gst_split_is_exact_and_the_two_halves_still_add_up():
    """Tax came out of `float(pct)` and `round(total * pct / (100 + pct))`.

    18% GST on ₹1,180 is exactly ₹180. Computed in binary floating point it is
    179.99999999999997, and `round` hides that — until an amount where it does
    not, and then the tax and the net no longer sum to the total anybody typed.
    """
    record = read("paid Sharma Traders 1180 for cement with 18% gst")
    assert record.total_paise == 118_000
    assert record.tax_paise == 18_000

    awkward = read("paid Sharma Traders 1234.56 for cement with 12.5% gst")
    assert awkward.total_paise is not None and awkward.tax_paise is not None
    assert awkward.tax_paise == 13_717
    assert 0 <= awkward.tax_paise <= awkward.total_paise


# ---- A3: what the page prints for a negative amount -------------------------


@pytest.mark.parametrize(
    ("paise", "shown"),
    [
        (0, "0.00"),
        (1, "0.01"),
        (420_050, "4,200.50"),
        (-1, "-0.01"),
        (-420_050, "-4,200.50"),
        (-100, "-1.00"),
        (-99, "-0.99"),
    ],
)
def test_a_negative_amount_renders_as_the_same_number_with_a_minus_sign(
    paise: int, shown: str
):
    """A3. `paise // 100` floors toward -infinity; `-420050 // 100` is -4201.

    So the page showed -4,201.50 for a balance of -4,200.50 — a rupee further
    from zero, on every negative figure in the trial balance. It reads like a
    rounding style until you notice the pennies did not move with it.
    """
    assert app.rupees(paise) == shown


def test_the_page_can_render_the_one_outcome_that_means_do_not_post():
    """A3's other half. `rupees` raised on a non-int, via the `:02d` format.

    `amount_is_integer_paise` is the only unanswerable check in the codebase,
    so a float amount is the clearest route to NOT_VALID there is — and it was
    the one draft the screen could not draw at all. The person got a traceback
    instead of "nothing was posted, and here is why".
    """
    assert app.rupees(True) == "0.01", "bool is an int; render it as one"

    with pytest.raises(TypeError, match="integer paise"):
        app.rupees(10.5)  # type: ignore[arg-type]


# ---- A4: the wire refuses a non-integer by name -----------------------------


def test_the_connector_names_the_voucher_and_the_amount_when_it_refuses_a_float():
    """A4. It was caught one line later by a format code, saying neither.

    `rupees_from_paise(True)` returns "0.01" without complaint, so a bool
    reaching the wire is not caught by formatting at all — it is written as one
    paise. The refusal has to be explicit and it has to name what it refused.
    """
    from accountant.schema import Voucher
    from accountant.tallyio import real

    bad = Voucher(
        id="v1",
        date=datetime.date(2026, 8, 1),
        party="Sharma Traders",
        narration="cement",
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=10.5,  # type: ignore[arg-type]
    )
    with pytest.raises(real.TallyRejected) as refused:
        real.check_amount_is_paise(bad)

    message = str(refused.value)
    assert "10.5" in message
    assert "v1" in message
    assert "float" in message

    boolean = replace(bad, amount_paise=True)  # type: ignore[arg-type]
    with pytest.raises(real.TallyRejected, match="bool"):
        real.check_amount_is_paise(boolean)

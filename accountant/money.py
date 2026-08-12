"""One rupee formatter, because four of them did not agree with each other.

WHY THIS FILE EXISTS
--------------------
Python groups digits in threes. India does not. Ten lakh is written
`10,00,000` - the last three digits, then groups of TWO - and until
2026-08-13 every figure this product printed above ninety-nine thousand was
grouped for a reader who does not exist:

    ₹10,00,000.00   what an Indian accountant reads
    ₹1,000,000.00   what `f"{whole:,}"` produced

A person reconciling a trial balance against a bank statement reads the comma
positions before the digits. Two conventions on one screen is how a figure
gets misread as ten times itself, and this product is sold to exactly one
market.

There were FOUR renderers - `web/app.py::rupees`, `web/app.py::money`,
`questions.py::rupees` and `taxonomy/report.py::crore` - and they disagreed on
more than the commas. The question text floored to whole rupees, so a person
was asked to confirm `₹3,800` for an amount the same page showed as
`₹3,800.47`. Four copies means four places to be wrong and four places a fix
has to reach; this is now the only one.

THE SIGN STAYS INSIDE THE SYMBOL
---------------------------------
A negative prints `₹-4,200.50`, not `-₹4,200.50`, because
`tests/test_app_coverage_b.py::test_the_rupee_sign_goes_in_front_of_the_minus_sign`
pins it there and a passing test named for the behaviour it holds is not
something a grouping change gets to overturn. Moving it is an owner decision,
open in `docs/OWNER_WORK.md`, and it is not this module's to make.

INTEGER PAISE, OR A REFUSAL
----------------------------
Money is integer paise everywhere in this repository - `Decimal` at the edges,
never `float`. This refuses anything else by type name rather than rendering
it, because a formatter lenient about `420050.0` accepts the exact class of
value `checks.py::amount_is_integer_paise` exists to catch, and accepts it on
the inputs where the leniency leaves no trace. `bool` renders as the int it
is: `isinstance(True, int)` is True in Python, so refusing it here would
disagree with `web/app.py::rupees`, which has always rendered it.

WHAT THIS FILE IS NOT FOR
--------------------------
TALLY'S WIRE. `tallyio/real.py::rupees_from_paise` writes
`<AMOUNT>4200.50</AMOUNT>` with no separators and no symbol, because Tally's
XML parser is not a person. A comma in that field is a parse failure or a
silently different number in somebody's statutory books. That renderer stays
where it is, and `tests/test_inr_grouping.py` pins the separation so nobody
unifies the two by mistake.

NO IO, NO CLOCK, NO CONFIG. Integer in, string out.
"""

from __future__ import annotations

RUPEE_SIGN = "₹"

#: Digits after the last group of three. India groups the rest in twos.
_TAIL = 3
_GROUP = 2


def group_indian(digits: str) -> str:
    """`"1000000"` -> `"10,00,000"`. Last three digits, then groups of two.

    Takes a digit string rather than an int so that the crore report can group
    a whole part it has already split off, and so the paise never travel
    through an intermediate number that could round.
    """
    if len(digits) <= _TAIL:
        return digits

    head, tail = digits[:-_TAIL], digits[-_TAIL:]
    groups: list[str] = []
    while len(head) > _GROUP:
        groups.insert(0, head[-_GROUP:])
        head = head[:-_GROUP]
    if head:
        groups.insert(0, head)
    groups.append(tail)
    return ",".join(groups)


def format_inr(paise: int) -> str:
    """Integer paise as the rupee figure a person reads. `₹-4,200.50`.

    Always Indian grouping, always both decimal places, always the symbol. The
    ONE renderer - every human-facing amount in this repository comes through
    here. The minus sits inside the symbol; see the module docstring for why
    that is not this function's call to change.
    """
    if isinstance(paise, bool):  # bool is an int; render it as one
        paise = int(paise)
    # Annotated `int`, so pyright calls this unnecessary. The annotation is not
    # enforced at runtime and the whole point of this branch is the value that
    # arrives anyway - a float amount is what makes an entry NOT_VALID, so it
    # is the amount the screen most needs to say something true about.
    if not isinstance(paise, int):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError(
            f"amounts are integer paise, never {type(paise).__name__}: {paise!r}"
        )

    sign = "-" if paise < 0 else ""
    whole, fraction = divmod(abs(paise), 100)
    return f"{RUPEE_SIGN}{sign}{group_indian(str(whole))}.{fraction:02d}"

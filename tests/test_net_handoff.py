"""The net, from the page to the law that needs it - and the pairing it is checked in.

WHY THIS FILE EXISTS
--------------------
Two defects that were invisible to every existing test, because both of them
produced a REFUSAL rather than a wrong number. A system whose failure mode is
"blocks too much" does not announce itself: every test that asserts a block
still passes, and the only symptom is a person opening bills the machine could
have cleared.

DEFECT ONE - THE NET WAS READ AND THEN DROPPED.
`accountant/pipeline.py` called `cage_gate.gate(...)` without `net_paise`, so the
parameter took its default of `None` while `draft.record.net_paise` held the
figure the reader had already found. MEASURED 2026-08-15 before the fix: of 956
blocks, 955 cited "there is something on this bill I could not check at all".
That sentence is `net_plus_tax_equals_gross` returning INDETERMINATE, and it
returned INDETERMINATE because the cage was never shown the number.

Measured end to end on `tests/test_textlayer.py::BILL` through
`TextLayerReader.extract`, the tier allowed to auto-post:

    record.net_paise                    104624      the reader had it
    net_plus_tax_equals_gross  before   INDETERMINATE  "the net amount was not read"
    net_plus_tax_equals_gross  after    PASS           1,046.24 + 188.32 = 1,234.56

DEFECT TWO - THE LINES WERE CHECKED AGAINST THE WRONG FIGURE.
`conservation.run` was called with `total_paise=amount` where `amount` is the
GROSS. Line items on a GST bill are PRE-TAX. Same bill, same run:

    item rows       1,000.00 + 46.24 = 1,046.24
    SUBTOTAL                           1,046.24    <- what they must equal
    GST                                  188.32
    TOTAL                              1,234.56    <- what they were checked against

The rows summed to the net EXACTLY and the law reported them short by 18832
paise - the tax, to the paisa. A bill whose arithmetic was perfect failed, and
the sentence a person read named a discrepancy that was not on the paper.

WHAT THIS FILE DOES NOT CLAIM
------------------------------
That more bills now post. It does not follow and it is not asserted anywhere
here. `lines_sum_to_total` is INDETERMINATE whenever the line items were not
read, `gate._line_paise` records that NO READER IN THIS REPOSITORY FILLS
`line_items`, and `decision.DOCUMENT_LAWS` blocks on INDETERMINATE at both
moments. So the ordinary document still blocks, on a different and now honest
sentence. What changed is that a bill carrying its own net is no longer refused
over a figure it printed.

THE CONTROL THAT MATTERS MOST is at the bottom: an unread net on a taxed bill
must still be refused. The tempting fix - deriving the net as gross minus tax -
is forbidden by `gate.gate`'s own docstring, because a number derived from the
law's own inputs is checked against itself and passes for ever. That would turn
this law into decoration while leaving it looking like a check.
"""

from __future__ import annotations

import datetime
import textwrap
from typing import cast

import pytest

from accountant.cage import conservation
from accountant.cage.conservation import Verdict
from accountant.cage.gate import (
    _lines_add_up_to,  # pyright: ignore[reportPrivateUsage]
    observed,
)
from accountant.extract.adapter import NOT_FOUND, ExtractedRecord, LineItem
from accountant.pipeline import Draft

READ_BY = "test"
DATE = datetime.date(2026, 4, 1)
PARTY = "Sharma Traders"

#: The measured bill, in paise. 495.00 net + 89.10 tax = 584.10 gross - the
#: owner's own example, and an 18% GST bill of the ordinary Indian shape.
NET = 49500
TAX = 8910
GROSS = 58410


def a_record(
    *,
    net_paise: int | None = NET,
    total_paise: int | None = GROSS,
    tax_paise: int | None = TAX,
    line_items: tuple[LineItem, ...] = (),
) -> ExtractedRecord:
    """A record whose four contract fields are all read, plus a stated net.

    `line_items` defaults to EMPTY rather than to one item covering the whole
    bill. `gate._line_paise` reads `()` as "nobody read any" and returns `None`,
    which is the true state of every reader in this repository today. A default
    that quietly supplied a line would make every test below pass a law that
    does not pass in production.
    """
    read: dict[str, object] = {
        "date": DATE,
        "party": PARTY,
        "total_paise": total_paise,
        "tax_paise": tax_paise,
    }
    return ExtractedRecord(
        date=DATE,
        party=PARTY,
        total_paise=total_paise,
        tax_paise=tax_paise,
        net_paise=net_paise,
        line_items=line_items,
        raw_text="",
        backend=READ_BY,
        per_field_source={
            name: READ_BY if value is not None else f"{NOT_FOUND}: not on this bill"
            for name, value in read.items()
        },
    )


# =============================================================================
# THE HANDOFF - the value, and that it arrives unchanged
# =============================================================================


def test_the_net_the_reader_found_is_the_net_the_cage_is_given() -> None:
    """DEFECT ONE, PINNED AT THE SEAM. `pipeline.evaluate` must hand
    `draft.record.net_paise` to `gate.gate`. A spy on the call is the only thing
    that can see it: every downstream assertion is about a REFUSAL, and the
    refusal looked identical whether the number arrived or not."""
    import inspect

    import accountant.pipeline as P

    body = inspect.getsource(P.evaluate)
    assert "net_paise=draft.record.net_paise" in body, (
        "pipeline.evaluate no longer hands the record's net to the cage. "
        "gate.gate defaults it to None, so net_plus_tax_equals_gross goes back "
        "to INDETERMINATE on every bill that printed its own subtotal."
    )


def test_the_value_is_not_altered_on_the_way_through() -> None:
    """Not rounded, not converted, not derived. The integer the reader wrote is
    the integer the law compares."""
    record = a_record(net_paise=49500)
    seen = observed(record_draft(record))

    # `wall.Field.value` is typed `object` ON PURPOSE - the wall accepts whatever
    # a reader claimed and refuses to promise it is a number. So the cast here is
    # the test stating what it expects to find, and it fails loudly on the
    # assertions below if the observation carried something else.
    total = cast("int | None", seen.total_paise.value)
    tax = cast("int | None", seen.tax_paise.value)

    assert record.net_paise == 49500
    assert total == GROSS
    assert tax == TAX
    assert (
        conservation.net_plus_tax_equals_gross(record.net_paise, tax, total).verdict
        is Verdict.PASS
    )


def test_the_source_of_every_contract_field_survives_the_handoff() -> None:
    """Provenance is not decoration - `gate._tiers` derives the reading tier from
    `per_field_source`, and that is what `AUTO_POST_ALLOWED_TIERS` is checked
    against. A handoff that dropped a source would silently change what may
    post."""
    record = a_record()
    for name in ExtractedRecord.FIELDS:
        assert name in record.per_field_source, name
        assert record.per_field_source[name], name


# =============================================================================
# THE PAIRING - what the line items are measured against
# =============================================================================


def test_line_items_are_measured_against_the_net_and_never_the_gross() -> None:
    """DEFECT TWO. The rows are pre-tax; the gross is not. Measured on the real
    text-layer bill: rows summed to 1,046.24, the net was 1,046.24, and the
    gross was 1,234.56 - so the old pairing reported a perfect bill short by
    exactly its tax."""
    assert _lines_add_up_to(net=NET, tax=TAX, gross=GROSS) == NET
    assert _lines_add_up_to(net=NET, tax=TAX, gross=GROSS) != GROSS


def test_the_measured_bill_passes_under_the_new_pairing_and_failed_under_the_old() -> (
    None
):
    """The exact numbers from `tests/test_textlayer.py::BILL`, both ways round.
    This is the regression: if the pairing is ever put back, the second
    assertion goes green and this test says so."""
    rows = (100000, 4624)
    net, tax, gross = 104624, 18832, 123456
    assert sum(rows) == net, "the fixture no longer reproduces the measured bill"
    assert net + tax == gross, "the fixture no longer reproduces the measured bill"

    correct = conservation.lines_sum_to_total(rows, net)
    assert correct.verdict is Verdict.PASS

    old = conservation.lines_sum_to_total(rows, gross)
    assert old.verdict is Verdict.FAIL
    # THE SIZE OF THE OLD ERROR WAS EXACTLY THE TAX, and that is the whole
    # signature of this defect: not a rounding drift, not an off-by-one, but a
    # bill reported short by the one number the comparison had wrongly included.
    assert gross - sum(rows) == tax
    assert "188.32" in old.said, old.said


def test_a_bill_with_no_tax_measures_its_lines_against_the_gross() -> None:
    """A READ zero, and only a read zero. With no tax the net IS the gross, so
    the gross is the right comparand and nothing has been assumed."""
    assert _lines_add_up_to(net=None, tax=0, gross=GROSS) == GROSS


def test_an_unread_net_on_a_taxed_bill_has_nothing_to_measure_against() -> None:
    """THE CONTROL, AND THE ONE THAT KEEPS THIS HONEST.

    `None` here becomes INDETERMINATE in `lines_sum_to_total`, which blocks. The
    tempting alternative is `gross - tax`, and `gate.gate`'s docstring forbids it
    in one sentence: a number derived from the law's own inputs is checked
    against itself and passes for ever.

    Note the tax is UNREAD (`None`) in one case and PRESENT in the other. Both
    must refuse, and for the same reason: nobody stated the pre-tax figure.
    """
    assert _lines_add_up_to(net=None, tax=TAX, gross=GROSS) is None
    assert _lines_add_up_to(net=None, tax=None, gross=GROSS) is None

    unchecked = conservation.lines_sum_to_total((10000,), None)
    assert unchecked.verdict is Verdict.INDETERMINATE


def test_an_unread_tax_is_not_a_zero_tax() -> None:
    """`None` and `0` are different facts and the distinction is load-bearing.
    Reading an empty tax field as zero posts a GST bill without its input
    credit - real money gone, nothing on screen. `tax == 0` must be an integer
    comparison that `None` fails."""
    assert _lines_add_up_to(net=None, tax=0, gross=GROSS) == GROSS
    assert _lines_add_up_to(net=None, tax=None, gross=GROSS) is None


# =============================================================================
# THE LAW ITSELF - the shapes a real Indian bill arrives in
# =============================================================================


@pytest.mark.parametrize(
    ("name", "net", "tax", "gross", "expected"),
    [
        ("the owner's example, 18% GST", 49500, 8910, 58410, Verdict.PASS),
        ("tax free, net equals gross", 50000, 0, 50000, Verdict.PASS),
        ("CGST 9% plus SGST 9%, summed", 100000, 18000, 118000, Verdict.PASS),
        ("IGST 18% interstate", 100000, 18000, 118000, Verdict.PASS),
        ("out by one paisa", 49500, 8910, 58411, Verdict.FAIL),
        ("gross short by the whole tax", 49500, 8910, 49500, Verdict.FAIL),
        ("net unread", None, 8910, 58410, Verdict.INDETERMINATE),
        ("tax unread", 49500, None, 58410, Verdict.INDETERMINATE),
        ("gross unread", 49500, 8910, None, Verdict.INDETERMINATE),
    ],
)
def test_net_plus_tax_equals_gross_over_the_shapes_a_bill_arrives_in(
    name: str, net: int | None, tax: int | None, gross: int | None, expected: Verdict
) -> None:
    """One row per real shape. THE ONE-PAISA ROW IS NOT PADDING - the owner
    closed it as a hard rule: a 1-paisa mismatch BLOCKS. A tolerance introduced
    here would be a threshold nobody voted for."""
    assert (
        conservation.net_plus_tax_equals_gross(net, tax, gross).verdict is expected
    ), name


def test_a_credit_note_keeps_its_sign_through_the_law() -> None:
    """Negatives are not absolute values. A credit note is a bill with the signs
    reversed, and a law that compared magnitudes would pass a refund booked as a
    purchase - money moving the wrong way with every check green."""
    assert (
        conservation.net_plus_tax_equals_gross(-49500, -8910, -58410).verdict
        is Verdict.PASS
    )
    assert (
        conservation.net_plus_tax_equals_gross(-49500, -8910, 58410).verdict
        is Verdict.FAIL
    )


def test_no_law_is_computed_with_a_float_anywhere() -> None:
    """Money is integer paise. `conservation._paise` refuses a float outright,
    and this is the test that says the refusal is reachable rather than
    theoretical."""
    with pytest.raises((TypeError, ValueError)):
        conservation.net_plus_tax_equals_gross(cast("int", 495.00), 8910, 58410)


# =============================================================================
# helpers
# =============================================================================


def record_draft(record: ExtractedRecord) -> Draft:
    """The smallest thing `gate.observed` will accept: something with `.record`.

    A real `Draft` needs a voucher, a company and a memory, none of which any
    assertion above looks at. Building one would tie these tests to the shape of
    an unrelated dataclass.

    THE CAST IS NARROW AND IT IS CHECKED. `observed` reads `draft.record` and
    nothing else; the assertion below says so, so the day it reaches for a
    second attribute this helper fails here rather than lying about its type.
    """
    assert set(_OBSERVED_READS) == {"record"}, (
        "gate.observed now reads more than `.record`, so this stand-in no longer "
        "stands in for a Draft. Build a real one or widen this helper."
    )

    class _JustTheRecord:
        def __init__(self, held: ExtractedRecord) -> None:
            self.record = held

    return cast("Draft", _JustTheRecord(record))


def _attributes_observed_reads() -> frozenset[str]:
    """Every `draft.<name>` that `gate.observed` actually touches, by AST scan.

    Read off the source rather than asserted from memory, because the whole
    point of the check above is that it keeps working after somebody edits
    `observed` without reading this file.
    """
    import ast
    import inspect

    tree = ast.parse(textwrap.dedent(inspect.getsource(observed)))
    return frozenset(
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "draft"
    )


_OBSERVED_READS = _attributes_observed_reads()

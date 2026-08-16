"""A second party vocabulary exists. This is the fence around it.

WHY THIS FILE EXISTS
--------------------
`accountant/labels.py` is meant to be the ONE label vocabulary, and its
own docstring says what the alternative costs: "two label vocabularies drift,
and the day one of them learns AMOUNT PAYABLE and the other does not is the day
the same bill reads differently depending on whether it arrived as a PDF or as
a photograph."

`accountant/invoice/parse.py` declares a second one - `SUPPLIER_SECTION` and
`BUYER_SECTION`. Five of its spellings were MEASURED WRONG and were held back
from `labels.py` for that reason; this package copied them anyway.

THE MEASUREMENT, 2026-08-15, OVER 413 REAL DOCUMENTS
-----------------------------------------------------
Using these spellings to read a party NAME produced seven values, and 7 of 7
were wrong: `2` (a customer number), `Address`, a bus conductor's name, two
ticket date ranges, a place name, and OCR noise. Per spelling:

    FROM         8 matches, never a supplier - every hit inside running prose
                 such as "unloaded from" and "or until"
    NAME        18 matches - every legible hit was the BUYER side of a German
                sample invoice, or the placeholder `BUYER_TRADING_NAME`
    CUSTOMER     1 match     BUYER      1 match     PARTY  1 match (prose)
    SOLD TO      0 matches   BILLED TO  0 matches

WHY THE COPY IS TOLERATED, AND WHAT THAT DEPENDS ON
-----------------------------------------------------
Reach, not correctness. MEASURED 2026-08-15: importing all 100 modules under
`accountant/` that are NOT under `accountant/invoice/` loads ZERO
`accountant.invoice` modules. The package is an island - only its own modules
and `tests/` import it - so five bad spellings cost nothing today.

That is a property of the import graph, and properties of import graphs are
exactly the kind that one convenient `from accountant.invoice import ...` ends
silently. THIS FILE IS THE ALARM ON IT. It fails the day a shipping module
reaches the second vocabulary, and it fails the day one of the measured-bad
spellings turns up in the first one.

WHAT IT DOES NOT CLAIM
-----------------------
That `SUPPLIER_SECTION` and `BUYER_SECTION` are correct. They are not - that is
the whole finding. It claims only that nothing which posts to Tally can consult
them, and that the seven never crossed into `labels.py`.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
from collections.abc import Iterable
from typing import Final, cast

from accountant import labels
from accountant.invoice import parse

REPO: Final = pathlib.Path(__file__).resolve().parent.parent

#: The seven party spellings measured on 413 documents, all seven wrong. None
#: of these may enter `labels.py`, whatever a future reader's intuition says.
MEASURED_WRONG: Final[tuple[str, ...]] = (
    "FROM",
    "NAME",
    "CUSTOMER",
    "BUYER",
    "PARTY",
    "SOLD TO",
    "BILLED TO",
)

#: The two worst of the seven - `NAME` at 18 matches and none right, `PARTY` at
#: 1 and it was prose. Absent from `parse.py` today and required to stay absent,
#: because the second vocabulary being merely bad is survivable and the second
#: vocabulary getting worse is how it eventually looks worth promoting.
NEVER_A_HEADING: Final[tuple[str, ...]] = ("NAME", "PARTY")

#: Imports every module under `accountant/` except the invoice package, then
#: reports which invoice modules came along for the ride. Run in a SUBPROCESS on
#: purpose: `tests/test_invoice_parse.py` imports the package directly, so by
#: the time this file runs in the same interpreter `sys.modules` already holds
#: it and an in-process check would be measuring the test suite, not the
#: product.
_PROBE: Final = """
import importlib, pathlib, sys

root = pathlib.Path(sys.argv[1])
names = []
for path in sorted((root / "accountant").rglob("*.py")):
    parts = path.relative_to(root).with_suffix("").parts
    if "invoice" in parts or "__pycache__" in parts:
        continue
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if parts:
        names.append(".".join(parts))

for name in names:
    importlib.import_module(name)

# THE PACKAGE, NOT THE PREFIX. `startswith("accountant.invoice")` is a STRING
# test, and it caught `accountant.invoicelike` the day that module moved up out
# of `accountant/extract/` - a different module, not inside the package this
# guard is about, reported as a breach. The exclusion loop above already gets
# this right: `"invoice" in parts` compares path COMPONENTS, so it never
# confused the two. Only the detection line did.
reached = sorted(
    m
    for m in sys.modules
    if m == "accountant.invoice" or m.startswith("accountant.invoice.")
)
print(len(names))
print(" ".join(reached))
"""


def _probe() -> tuple[int, tuple[str, ...]]:
    """How many shipping modules were imported, and which invoice modules loaded."""
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
        [sys.executable, "-c", _PROBE, str(REPO)],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    counted, reached = done.stdout.splitlines()[:2]
    return int(counted), tuple(reached.split())


# ---- the guard that matters ---------------------------------------------------


def test_no_shipping_module_can_reach_the_second_vocabulary() -> None:
    """THE CONTROL THIS FILE EXISTS UNDER.

    Every module under `accountant/` outside `accountant/invoice/` is imported
    in a clean interpreter, and no `accountant.invoice` module may be loaded
    afterwards. MEASURED 2026-08-15: 100 modules imported, 0 invoice modules
    reached.

    If this fails, five spellings measured wrong on 413 documents are now on a
    path that can post to Tally, and `parse.SUPPLIER_SECTION` /
    `parse.BUYER_SECTION` have to be re-measured before the import stays.
    """
    counted, reached = _probe()

    assert counted >= 50, f"only {counted} modules found - the probe stopped working"
    assert reached == (), f"a shipping module now imports {', '.join(reached)}"


# ---- the guard on the one vocabulary ------------------------------------------


def test_the_measured_wrong_spellings_never_entered_labels() -> None:
    """None of the seven may appear in any `labels.py` vocabulary.

    MEASURED: `labels.PARTY_LABELS` holds SUPPLIER, VENDOR, BILLED BY, SOLD BY -
    the four supplier-side spellings that survived. The seven that did not are
    checked against EVERY public string family in the module, not just that one,
    so renaming or splitting the tuple cannot let one back in unnoticed.
    """
    for name in dir(labels):
        if not name.isupper():
            continue
        family: object = getattr(labels, name)
        if not isinstance(family, tuple | list | frozenset | set):
            continue
        members = cast("Iterable[object]", family)
        spellings = {word for word in members if isinstance(word, str)}
        for wrong in MEASURED_WRONG:
            assert wrong not in spellings, f"labels.{name} took on {wrong}"


def test_the_two_worst_spellings_are_not_headings_either() -> None:
    """`NAME` and `PARTY` are absent from the second vocabulary and stay absent.

    `NAME` matched 18 times over 413 documents and not once correctly. It is the
    single most tempting addition on this list, because "Name:" is what a person
    expects an invoice to print, and it is the one the measurement is harshest
    about.
    """
    headings = set(parse.SUPPLIER_SECTION) | set(parse.BUYER_SECTION)

    for wrong in NEVER_A_HEADING:
        assert wrong not in headings, f"parse.py took on {wrong}, measured 0 right"


def test_the_five_that_are_here_are_written_down_as_measured() -> None:
    """The five measured-bad spellings `parse.py` does carry, named out loud.

    Not a style check. If somebody deletes the block of comment above
    `SUPPLIER_SECTION`, the next reader finds five spellings with no record that
    they were measured and lost, and the obvious tidy-up is to promote them into
    `labels.py`. This asserts the record and the spellings agree.
    """
    headings = set(parse.SUPPLIER_SECTION) | set(parse.BUYER_SECTION)
    carried = sorted(headings & set(MEASURED_WRONG))
    assert carried == ["BILLED TO", "BUYER", "CUSTOMER", "FROM", "SOLD TO"]

    source = (REPO / "accountant" / "invoice" / "parse.py").read_text()
    for wrong in carried:
        assert wrong in source
    assert "7 of 7 were WRONG" in source
    assert "tests/test_party_vocabulary_reach.py" in source

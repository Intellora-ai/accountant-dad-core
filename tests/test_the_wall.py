"""The wall between what we think a bill says and what we will write.

WHY THIS FILE EXISTS
--------------------
Before the cage, one type carried a field from the moment it was guessed to the
moment it was written into a customer's books. That single fact is the root
cause of six of the sixteen failure modes in the plan: a misread amount, a wrong
party, a flipped sign, a nonsense entry, a cat photo and a wrong voucher type
all reach the books by the same route, because nothing in the type system says
which of them has been checked.

So there are two types now:

    Observation   what we think the bill says. Every field carries a confidence
                  between 0.0 and 1.0. NOTHING here can be posted.
    LedgerEntry   what we will write. Constructible ONLY by the decision layer.

The second one is the wall. A reader cannot build a `LedgerEntry` no matter what
it read, because the constructor refuses any caller that is not the decision
module. That is enforced twice - at run time by the constructor, and at test
time by an AST scan of the whole package - because a runtime guard can be
skipped by a module that simply never calls it, and a static guard cannot stop a
write that is already happening.

WHY BOTH GUARDS, STATED PLAINLY
--------------------------------
Defect J1 in this repository's own history: *a unit test of a guard proves the
guard works and says nothing about whether the guard is installed.* The AST scan
is the "is it installed" half. Neither half covers the other's failure.

WHAT THIS FILE DOES NOT PROVE
------------------------------
It does not prove the decision layer decides correctly - only that nothing else
is allowed to. A wrong `LedgerEntry` built by `decision.py` is still wrong; the
wall stops it being built somewhere nobody is looking.

NO NETWORK, NO IO.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from accountant.cage.wall import (
    DECIDING_MODULE,
    Field,
    LedgerEntry,
    NotYourEntryError,
    Observation,
)

CAGE = pathlib.Path(__file__).resolve().parent.parent / "accountant"


def a_field(value: object = 100_000, confidence: float = 1.0) -> Field:
    return Field(value=value, confidence=confidence, source="test")


# ---- Field: a value and how sure we are, together ---------------------------


def test_a_field_carries_a_value_and_a_confidence() -> None:
    field = a_field(100_000, 0.95)
    assert field.value == 100_000
    assert field.confidence == 0.95


def test_a_field_that_was_not_read_has_confidence_zero() -> None:
    """`None` and "we are unsure" are the same fact and must not disagree. A
    field with no value but a high score would post on nothing at all."""
    with pytest.raises(ValueError):
        Field(value=None, confidence=0.9, source="test")


def test_an_unread_field_with_zero_confidence_is_fine() -> None:
    assert Field(value=None, confidence=0.0, source="not_found: no total").value is None


def test_confidence_above_one_is_refused() -> None:
    """A score outside 0.0-1.0 means the caller computed it wrongly, and a
    number above 1.0 would clear the auto-post band by accident."""
    with pytest.raises(ValueError):
        Field(value=100_000, confidence=1.5, source="test")


def test_confidence_below_zero_is_refused() -> None:
    with pytest.raises(ValueError):
        Field(value=100_000, confidence=-0.1, source="test")


def test_a_field_must_say_where_it_came_from() -> None:
    """Provenance is not optional. A field with no source cannot be explained to
    the person whose books it is about to change."""
    with pytest.raises(ValueError):
        Field(value=100_000, confidence=1.0, source="")


def test_a_field_cannot_be_edited_after_it_is_made() -> None:
    field = a_field()
    with pytest.raises(AttributeError):
        field.value = 1  # type: ignore[misc]


# ---- Observation: what we think, never what we write ------------------------


def test_an_observation_reports_its_weakest_field() -> None:
    """The minimum, not the mean. One misread digit ruins an amount, and a mean
    hides exactly the field that should stop the post."""
    seen = Observation(
        date=a_field(confidence=0.99),
        party=a_field(confidence=0.60),
        total_paise=a_field(confidence=0.99),
        tax_paise=a_field(confidence=0.99),
    )
    assert seen.lowest_confidence == 0.60


def test_the_control_a_high_scoring_observation_reports_a_high_minimum() -> None:
    """THE CONTROL. A `lowest_confidence` that returned a constant would pass
    the test above and fail this one."""
    seen = Observation(
        date=a_field(confidence=0.99),
        party=a_field(confidence=0.97),
        total_paise=a_field(confidence=0.98),
        tax_paise=a_field(confidence=0.99),
    )
    assert seen.lowest_confidence == 0.97


def test_an_observation_with_an_unread_field_has_a_minimum_of_zero() -> None:
    """An unread field is confidence 0.0, so the whole observation is 0.0. That
    is the point: one thing we could not read stops the auto-post."""
    seen = Observation(
        date=a_field(),
        party=Field(value=None, confidence=0.0, source="not_found: no party"),
        total_paise=a_field(),
        tax_paise=a_field(),
    )
    assert seen.lowest_confidence == 0.0


def test_an_observation_cannot_be_edited_after_it_is_made() -> None:
    seen = Observation(
        date=a_field(), party=a_field(), total_paise=a_field(), tax_paise=a_field()
    )
    with pytest.raises(AttributeError):
        seen.party = a_field()  # type: ignore[misc]


def test_an_observation_has_no_method_that_writes_anything() -> None:
    """The type is inert by construction. If it ever grows a `post` or a `save`,
    this test is the thing that notices."""
    forbidden = {"post", "write", "save", "commit", "send", "to_ledger_entry"}
    assert not forbidden & set(dir(Observation))


# ---- LedgerEntry: the wall itself -------------------------------------------


def test_the_decision_layer_may_build_a_ledger_entry() -> None:
    entry = LedgerEntry.decided(
        DECIDING_MODULE,
        party="Test Supplier",
        amount_paise=100_000,
        debit_account="Purchase",
        credit_account="Test Supplier",
    )
    assert entry.amount_paise == 100_000


def test_the_control_nobody_else_may_build_one() -> None:
    """THE CONTROL, and the whole reason this module exists.

    A reader that misread an amount cannot turn it into something postable, no
    matter how confident it was, because it is not the decision layer.
    """
    with pytest.raises(NotYourEntryError):
        LedgerEntry.decided(
            "accountant.extract.freeocr",
            party="Test Supplier",
            amount_paise=100_000,
            debit_account="Purchase",
            credit_account="Test Supplier",
        )


def test_the_refusal_names_who_asked_and_who_is_allowed() -> None:
    """A refusal a developer cannot act on wastes the guard. It says which
    module tried and which one is permitted."""
    with pytest.raises(NotYourEntryError) as raised:
        LedgerEntry.decided(
            "accountant.web.app",
            party="x",
            amount_paise=1,
            debit_account="a",
            credit_account="b",
        )
    assert "accountant.web.app" in str(raised.value)
    assert DECIDING_MODULE in str(raised.value)


def test_a_ledger_entry_refuses_a_negative_amount() -> None:
    with pytest.raises(ValueError):
        LedgerEntry.decided(
            DECIDING_MODULE,
            party="x",
            amount_paise=-1,
            debit_account="a",
            credit_account="b",
        )


def test_a_ledger_entry_refuses_the_same_ledger_on_both_sides() -> None:
    """Money moving from an account to itself is not an entry, it is a typo."""
    with pytest.raises(ValueError):
        LedgerEntry.decided(
            DECIDING_MODULE,
            party="x",
            amount_paise=1,
            debit_account="Cash",
            credit_account="Cash",
        )


def test_a_ledger_entry_refuses_an_unnamed_party() -> None:
    with pytest.raises(ValueError):
        LedgerEntry.decided(
            DECIDING_MODULE,
            party="  ",
            amount_paise=1,
            debit_account="a",
            credit_account="b",
        )


def test_a_ledger_entry_cannot_be_edited_after_it_is_made() -> None:
    entry = LedgerEntry.decided(
        DECIDING_MODULE,
        party="x",
        amount_paise=1,
        debit_account="a",
        credit_account="b",
    )
    with pytest.raises(AttributeError):
        entry.amount_paise = 999  # type: ignore[misc]


# ---- the static half: is the guard actually installed? ----------------------


def _modules_constructing(name: str) -> set[str]:
    """Every module under `accountant/` that names `LedgerEntry(` directly.

    An AST scan, not a substring search: a mention inside a docstring or a
    comment is not a construction, and a grep cannot tell the difference.
    """
    found: set[str] = set()
    for path in CAGE.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = node.func
                called = getattr(target, "id", None) or getattr(target, "attr", None)
                if called == name:
                    found.add(str(path.relative_to(CAGE.parent)))
    return found


def test_only_the_wall_module_calls_the_ledger_entry_constructor() -> None:
    """DEFECT J1's lesson: a unit test of a guard proves the guard works and
    says nothing about whether the guard is installed.

    The runtime guard above can be walked around by any module that builds a
    `LedgerEntry` directly instead of going through `decided`. This is the half
    that notices.
    """
    assert _modules_constructing("LedgerEntry") <= {"accountant/cage/wall.py"}


def test_the_control_the_scanner_can_actually_find_a_construction() -> None:
    """THE CONTROL on the scan, and it has already earned its place.

    A scanner that found nothing would pass the test above while proving nothing
    at all. When this file was first written, `LedgerEntry.decided` built its
    result with `cls(...)` - invisible to an AST scan looking for
    `LedgerEntry(` - so the guard above was asserting over an empty set. This
    control failed, and `wall.py` was changed to construct by name.

    `ConservationResult` is constructed by name in `conservation.py`, so a
    scanner that works sees it and a scanner that is blind does not.
    """
    assert _modules_constructing("ConservationResult")


def test_the_ledger_entry_guard_is_not_asserting_over_nothing() -> None:
    """The second half of the control above: prove the primary scan has a real
    subject. If nobody constructs a `LedgerEntry` anywhere, its scan is vacuous
    and would keep passing after the wall was deleted."""
    assert _modules_constructing("LedgerEntry") == {"accountant/cage/wall.py"}


def test_no_module_outside_the_cage_imports_the_ledger_entry_type() -> None:
    """Belt and braces: even importing the name outside the cage is a smell,
    because the only legitimate producer and consumer both live inside it."""
    importers: set[str] = set()
    for path in CAGE.rglob("*.py"):
        if path.parent.name == "cage":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "LedgerEntry" for alias in node.names
            ):
                importers.add(str(path.relative_to(CAGE.parent)))
    assert importers == set()


def test_a_refused_amount_reaches_a_person_as_rupees_and_not_as_raw_paise() -> None:
    """The message in this ValueError does NOT stay in a traceback.

    `decision.py` catches it and puts it verbatim into the sentence a person
    reads, so `got -500 paise` reached a screen. That branch's author guarded it
    against LEDGER names leaking and did not think of amounts - which is why a
    guard that lists what it protects against is worth less than one tested from
    the far end, where the text actually arrives.

    THIS ASSERTION WAS WRONG WHEN FIRST WRITTEN and looked for `₹5.00`. The sign
    sits INSIDE the rupee symbol - `₹-5.00` - which is the convention
    `test_the_rupee_sign_goes_in_front_of_the_minus_sign` pins deliberately, and
    which a separate change was reverted for moving. The test was corrected; the
    code was right.
    """
    with pytest.raises(ValueError) as refused:
        LedgerEntry.decided(
            DECIDING_MODULE,
            party="Test Supplier",
            amount_paise=-500,
            debit_account="Purchases",
            credit_account="Cash",
        )

    said = str(refused.value)
    assert "₹-5.00" in said
    assert "-500 paise" not in said


def test_the_control_a_refusal_that_names_no_amount_is_left_alone() -> None:
    """THE CONTROL. A fix that ran every message through a formatter would pass
    the test above while proving nothing about whether the amount specifically
    was reached."""
    with pytest.raises(ValueError) as refused:
        LedgerEntry.decided(
            DECIDING_MODULE,
            party="   ",
            amount_paise=100,
            debit_account="Purchases",
            credit_account="Cash",
        )

    assert str(refused.value) == "an entry must name a party."

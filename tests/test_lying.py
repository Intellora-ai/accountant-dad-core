"""The model that lies on command, and proof that it really does lie.

WHY THIS FILE EXISTS
--------------------
You cannot test a cage with an empty cage. Every guard in `accountant/cage/`
is written against a failure - a misread amount, a wrong party, a flipped sign,
a bill that adds up perfectly and is still wrong by a factor of ten. A real
model only ever proves the guard caught whichever lies it happened to tell on
the day it was run, and it tells a different set tomorrow.

A stub that lies TO ORDER turns that around. The lie is named, fixed and
repeatable, so a guard can be tested against exactly the failure it was built
for, and a guard that stops catching it fails a test instead of quietly
degrading.

The most important test in this file is the one that says the stub actually
lied. A test double that quietly tells the truth proves nothing at all while
looking like a full green suite - and it is the single way this whole approach
can fail silently.

WHAT THIS FILE DOES NOT PROVE
------------------------------
It does not prove any guard is correct. It proves the input to those guards is
what it says it is. A guard tested against this stub is tested against the lies
we thought of; a lie nobody has thought of is still not covered, and the
self-consistent one here is in the file precisely because arithmetic CANNOT
catch it (failure mode F-02).

It also does not prove the stub resembles a real OCR engine. It does not:
Tesseract's mistakes are pixel-shaped, the ones here are chosen. That is the
point - a chosen lie is reproducible and a real one is not.

NO NETWORK. The only IO in this file is reading this repository's own `.py`
source for the two static scans at the bottom, which is what proves the stub
imports nothing that could reach a network and appears on no runtime path.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Final

import pytest

from accountant.cage.confidence import EXACT
from accountant.cage.conservation import (
    Verdict,
    lines_sum_to_total,
    net_plus_tax_equals_gross,
)
from accountant.cage.lying import (
    A_PARTY_NOBODY_HAS_HEARD_OF,
    ANOTHER_REAL_PARTY,
    DEFAULT_TRUTH,
    LIE_KINDS,
    SELF_CONSISTENT_SCALE,
    SOURCE,
    Lie,
    Mode,
    StubToldTheTruthError,
    Truth,
    UnknownLieError,
    UnknownModeError,
    figures,
    lie_for_seed,
    observe,
)
from accountant.cage.wall import Field, Observation

REPO = pathlib.Path(__file__).resolve().parent.parent
SHIPPED = REPO / "accountant"
TESTS = REPO / "tests"


def paise(field: Field) -> int | None:
    """The amount out of a field, or None when it was never read.

    `Field.value` is deliberately typed `object` - a field holds whatever was
    read, including nonsense. Every amount this stub produces is an `int`, and
    asserting that here means a test that fed a string into a conservation law
    would say so rather than failing somewhere further down.
    """
    value = field.value
    if value is None:
        return None
    assert isinstance(value, int)
    return value


# ---- SUCCESS: the clean read everything else is measured against ------------


def test_success_mode_hands_back_the_truth_field_for_field() -> None:
    seen = observe(Mode.SUCCESS)
    assert seen.date.value == DEFAULT_TRUTH.date
    assert seen.party.value == DEFAULT_TRUTH.party
    assert seen.total_paise.value == DEFAULT_TRUTH.total_paise
    assert seen.tax_paise.value == DEFAULT_TRUTH.tax_paise
    assert seen.line_paise == DEFAULT_TRUTH.line_paise


def test_success_mode_satisfies_the_two_laws_a_bill_can_answer_alone() -> None:
    """The other two laws need the books, not the bill. If a clean read did not
    pass these two, every later test using GARBAGE as the contrast would be
    comparing one failure against another."""
    seen = observe(Mode.SUCCESS)
    lines = lines_sum_to_total(seen.line_paise, paise(seen.total_paise))
    amounts = net_plus_tax_equals_gross(
        DEFAULT_TRUTH.net_paise, paise(seen.tax_paise), paise(seen.total_paise)
    )
    assert lines.verdict is Verdict.PASS
    assert amounts.verdict is Verdict.PASS


def test_the_control_a_lying_read_does_not_satisfy_those_same_laws() -> None:
    """THE CONTROL on the test above. If both modes passed, the laws would be
    measuring nothing and the test above would be decoration."""
    seen = observe(Mode.GARBAGE, lie=Lie.WRONG_AMOUNT)
    lines = lines_sum_to_total(seen.line_paise, paise(seen.total_paise))
    assert lines.verdict is Verdict.FAIL


def test_a_clean_read_is_reported_as_certain() -> None:
    """Exact by construction: the stub was handed the value, it did not squint
    at a photo. There is nothing here to be unsure about."""
    assert observe(Mode.SUCCESS).lowest_confidence == EXACT


# ---- GARBAGE: the stub must actually lie ------------------------------------


#: What `figures()` returns, in its order, so a difference can be named rather
#: than reported as "position 2".
FIGURE_NAMES: Final = ("date", "party", "total", "tax", "lines")

#: Which figures each lie is supposed to move, and by omission which ones it
#: must leave alone. This table is the specification: a lie that moves nothing,
#: a lie that moves the wrong field, and a lie added without an implementation
#: all disagree with it.
WHAT_EACH_LIE_MOVES: Final[dict[Lie, set[str]]] = {
    Lie.WRONG_AMOUNT: {"total"},
    Lie.WRONG_PARTY: {"party"},
    Lie.FLIPPED_SIGN: {"total"},
    Lie.INVENTED_VENDOR: {"party"},
    Lie.SELF_CONSISTENT_WRONG_TOTAL: {"total", "tax", "lines"},
}


def moved(honest: tuple[object, ...], lying: tuple[object, ...]) -> set[str]:
    """Which named figures differ between an honest read and a lying one."""
    return {
        name
        for name, before, after in zip(FIGURE_NAMES, honest, lying, strict=True)
        if before != after
    }


def test_every_named_lie_moves_exactly_the_figures_it_is_named_for() -> None:
    """THE TEST THIS WHOLE FILE IS FOR.

    A double that quietly returns the truth passes every guard test while
    proving nothing, and a full green suite is exactly what it looks like.

    "Something changed" is too weak a claim to rest that on: a wrong-party lie
    that moved the date instead would satisfy it. So the table above says which
    figures each lie moves, and this asserts exactly that set - no fewer, which
    catches a lie that stopped working, and no more, which catches a lie that
    corrupts a field some other guard was relying on. Iterating `Lie` rather
    than `LIE_KINDS` means a kind added without an implementation fails here.
    """
    honest = figures(observe(Mode.SUCCESS))
    for lie in Lie:
        lying = figures(observe(Mode.GARBAGE, lie=lie))
        assert moved(honest, lying) == WHAT_EACH_LIE_MOVES[lie], lie


def test_every_lie_kind_is_listed_so_a_seed_can_reach_it() -> None:
    """`LIE_KINDS` is what the seed indexes into. A kind missing from it is a
    lie no seeded run can ever produce, which makes it untested by default."""
    assert set(LIE_KINDS) == set(Lie)


def test_a_wrong_amount_is_out_by_the_smallest_step_arithmetic_can_see() -> None:
    """One paisa, not one lakh. A guard that catches the smallest possible
    disagreement catches every larger one; a guard tested only against an
    obvious lie says nothing about the subtle one."""
    seen = observe(Mode.GARBAGE, lie=Lie.WRONG_AMOUNT)
    assert paise(seen.total_paise) == DEFAULT_TRUTH.total_paise + 1


def test_a_wrong_amount_leaves_the_lines_alone_so_the_sum_law_can_see_it() -> None:
    """Scaling the lines to match would make this the self-consistent lie,
    which is a different failure with its own name."""
    seen = observe(Mode.GARBAGE, lie=Lie.WRONG_AMOUNT)
    assert seen.line_paise == DEFAULT_TRUTH.line_paise


def test_a_wrong_party_names_a_different_real_supplier() -> None:
    """The bill is attributed to somebody who exists and is not the right one -
    two real suppliers confused. Nothing about the amounts is wrong, so no
    arithmetic law can notice this."""
    seen = observe(Mode.GARBAGE, lie=Lie.WRONG_PARTY)
    assert seen.party.value == ANOTHER_REAL_PARTY
    assert seen.party.value != DEFAULT_TRUTH.party
    assert paise(seen.total_paise) == DEFAULT_TRUTH.total_paise


def test_an_invented_vendor_is_a_party_that_exists_nowhere() -> None:
    """Different failure from the one above: not a mix-up between two real
    names, but a supplier the model made up. A ledger-name check catches one of
    these and not the other, which is why they are separate kinds."""
    seen = observe(Mode.GARBAGE, lie=Lie.INVENTED_VENDOR)
    assert seen.party.value == A_PARTY_NOBODY_HAS_HEARD_OF
    assert seen.party.value != ANOTHER_REAL_PARTY


def test_a_flipped_sign_turns_money_in_into_money_out() -> None:
    seen = observe(Mode.GARBAGE, lie=Lie.FLIPPED_SIGN)
    assert paise(seen.total_paise) == -DEFAULT_TRUTH.total_paise


def test_a_flipped_sign_is_visible_to_the_bill_arithmetic() -> None:
    """The sign flip is on the total only. A wholly negated bill would be
    self-consistent, and that lie already has its own name."""
    seen = observe(Mode.GARBAGE, lie=Lie.FLIPPED_SIGN)
    assert lines_sum_to_total(seen.line_paise, paise(seen.total_paise)).verdict is (
        Verdict.FAIL
    )


def test_the_self_consistent_lie_passes_every_law_and_is_still_wrong() -> None:
    """FAILURE MODE F-02, and the reason this stub exists at all.

    Every figure scaled by ten: the lines still sum to the total and net plus
    tax still equals the gross. Arithmetic cannot see this and `conservation.py`
    says so in its own docstring. This test is what stops anybody claiming the
    conservation laws are sufficient on their own.
    """
    seen = observe(Mode.GARBAGE, lie=Lie.SELF_CONSISTENT_WRONG_TOTAL)
    scaled_net = DEFAULT_TRUTH.net_paise * SELF_CONSISTENT_SCALE
    lines = lines_sum_to_total(seen.line_paise, paise(seen.total_paise))
    amounts = net_plus_tax_equals_gross(
        scaled_net, paise(seen.tax_paise), paise(seen.total_paise)
    )
    assert lines.verdict is Verdict.PASS
    assert amounts.verdict is Verdict.PASS
    assert paise(seen.total_paise) != DEFAULT_TRUTH.total_paise


def test_the_control_the_self_consistent_lie_moved_every_figure() -> None:
    """THE CONTROL on the test above. Laws passing is only interesting if the
    numbers really did change - a stub that returned the truth would also pass
    every law, for the opposite reason."""
    seen = observe(Mode.GARBAGE, lie=Lie.SELF_CONSISTENT_WRONG_TOTAL)
    assert paise(seen.tax_paise) == DEFAULT_TRUTH.tax_paise * SELF_CONSISTENT_SCALE
    assert seen.line_paise == tuple(
        amount * SELF_CONSISTENT_SCALE for amount in DEFAULT_TRUTH.line_paise
    )


def test_the_self_consistent_lie_leaves_the_party_and_date_alone() -> None:
    """One lie at a time. A garbage mode that changed everything would tell you
    a guard caught something without telling you what."""
    seen = observe(Mode.GARBAGE, lie=Lie.SELF_CONSISTENT_WRONG_TOTAL)
    assert seen.party.value == DEFAULT_TRUTH.party
    assert seen.date.value == DEFAULT_TRUTH.date


def test_a_lie_is_told_at_full_confidence_because_that_is_the_failure_feared() -> None:
    """Confident nonsense, not hedged nonsense. A stub that scored its lies low
    would be caught by the confidence band alone, and would prove nothing about
    the guards that have to catch a model which is certain and wrong."""
    for lie in Lie:
        assert observe(Mode.GARBAGE, lie=lie).lowest_confidence == EXACT


def test_the_provenance_says_which_lie_was_injected() -> None:
    """The value carries no marker - a marked value would let a guard "catch"
    the lie by spotting the marker, which proves nothing. The provenance does,
    because that is what `source` is for and no arithmetic guard reads it."""
    seen = observe(Mode.GARBAGE, lie=Lie.FLIPPED_SIGN)
    assert "flipped_sign" in seen.total_paise.source


def test_every_field_of_every_mode_says_it_came_from_the_lying_model() -> None:
    """If one of these observations ever escaped into a log, a fixture or a
    screenshot, nothing about the values would distinguish it from a real read.
    The provenance is what does, and it has to be on every field - a stub that
    marked three of four leaves the fourth looking genuine."""
    for mode in Mode:
        seen = observe(mode)
        for field in (seen.date, seen.party, seen.total_paise, seen.tax_paise):
            assert field.source.startswith(SOURCE), (mode, field)


# ---- PARTIAL: some read, some not, and the difference stated ----------------


def test_partial_mode_reads_some_fields_and_leaves_others_unread() -> None:
    """The phone-photo case: the top of the bill is in frame and the amount
    block is not."""
    seen = observe(Mode.PARTIAL)
    assert seen.date.value == DEFAULT_TRUTH.date
    assert seen.party.value == DEFAULT_TRUTH.party
    assert seen.total_paise.value is None
    assert seen.tax_paise.value is None


def test_an_unread_field_carries_zero_confidence_and_a_stated_reason() -> None:
    """Not reading something and being unsure of it are the same fact - the
    wall refuses a field where they disagree. The reason is what a person reads
    when the post is refused."""
    seen = observe(Mode.PARTIAL)
    assert seen.total_paise.confidence == 0.0
    assert "not_found" in seen.total_paise.source


def test_partial_mode_makes_the_tax_law_indeterminate_and_not_a_pass() -> None:
    """The most dangerous coercion in the system: an unread tax field read as
    zero posts a GST bill without its input credit. This is the fixture that
    keeps that path under test."""
    seen = observe(Mode.PARTIAL)
    result = net_plus_tax_equals_gross(
        DEFAULT_TRUTH.net_paise, paise(seen.tax_paise), paise(seen.total_paise)
    )
    assert result.verdict is Verdict.INDETERMINATE


def test_partial_mode_reports_a_lowest_confidence_of_zero() -> None:
    """One unread field drops the whole observation to zero. That is the point:
    a bill read three-quarters of the way is not three-quarters postable."""
    assert observe(Mode.PARTIAL).lowest_confidence == 0.0


def test_partial_mode_leaves_the_lines_unread_rather_than_empty() -> None:
    """`None` is "we did not look"; `()` is "we looked and there were none".
    Collapsing them turns an un-itemised bill into a passing one, which is the
    exact distinction `lines_sum_to_total` is built around."""
    assert observe(Mode.PARTIAL).line_paise is None


# ---- FAILURE: nothing read, and it says so ----------------------------------


def test_failure_mode_reads_nothing_at_all() -> None:
    seen = observe(Mode.FAILURE)
    assert figures(seen) == (None, None, None, None, None)


def test_failure_mode_states_a_reason_on_every_field() -> None:
    """A refusal with no sentence cannot be shown to a person, and this product
    refuses in plain language or not at all."""
    seen = observe(Mode.FAILURE)
    for field in (seen.date, seen.party, seen.total_paise, seen.tax_paise):
        assert "not_found" in field.source


def test_failure_mode_scores_zero_on_every_field() -> None:
    seen = observe(Mode.FAILURE)
    assert seen.lowest_confidence == 0.0
    assert seen.date.confidence == 0.0


# ---- determinism: the same order gives the same lie, always -----------------


def test_the_same_mode_and_seed_give_an_identical_observation() -> None:
    """Rule 11.2.10. A double that varied run to run would make a guard test
    flaky, and a flaky safety test gets deleted rather than fixed."""
    for mode in Mode:
        assert observe(mode, seed=3) == observe(mode, seed=3)


def test_the_control_a_different_seed_selects_a_different_lie() -> None:
    """THE CONTROL on the test above. A stub that ignored the seed entirely
    would pass it while making the seed decorative.

    It compares `figures`, not whole observations. Two observations differ in
    their `source` strings as soon as the LABEL differs, so comparing them
    whole would accept a seed that renamed the lie without changing a single
    number - which is the exact trap `figures` exists to avoid.
    """
    assert figures(observe(Mode.GARBAGE, seed=0)) != figures(
        observe(Mode.GARBAGE, seed=1)
    )


def test_a_seed_that_wraps_the_list_selects_the_same_lie_again() -> None:
    """The seed indexes a fixed list, so it is arithmetic and not a generator.
    A reproducible RNG is still one more thing to trust across versions."""
    assert lie_for_seed(0) is lie_for_seed(len(LIE_KINDS))
    assert observe(Mode.GARBAGE, seed=0) == observe(Mode.GARBAGE, seed=len(LIE_KINDS))


def test_a_named_lie_is_not_moved_by_the_seed() -> None:
    """The seed chooses WHICH lie, never how big it is. A seed-scaled amount
    would make the lie a different size on every run, and a guard tested against
    a moving target is tested against nothing."""
    first = observe(Mode.GARBAGE, lie=Lie.WRONG_AMOUNT, seed=0)
    second = observe(Mode.GARBAGE, lie=Lie.WRONG_AMOUNT, seed=99)
    assert first == second


def test_the_modes_that_have_one_shape_ignore_the_seed_by_design() -> None:
    """There is exactly one way to read nothing. Stated and tested rather than
    left as an accident somebody later relies on."""
    assert observe(Mode.FAILURE, seed=0) == observe(Mode.FAILURE, seed=7)


def test_the_four_modes_do_not_produce_the_same_observation() -> None:
    """A fifth mode added without a branch would fall through to whichever one
    is last and be indistinguishable from it. This is what notices.

    Pairwise `!=` rather than counting a set of them: deduplicating by hash or
    by `repr` compares a rendering of the values instead of the values, and the
    whole subject here is figures that must actually differ.
    """
    seen = [figures(observe(mode)) for mode in Mode]
    for index, one in enumerate(seen):
        for other in seen[index + 1 :]:
            assert one != other


# ---- refusals: everything uncertain blocks ----------------------------------


def test_an_unknown_mode_raises_rather_than_guessing() -> None:
    with pytest.raises(UnknownModeError):
        observe("mostly_right")


def test_the_refusal_names_the_modes_that_do_exist() -> None:
    """A refusal a developer cannot act on wastes the guard."""
    with pytest.raises(UnknownModeError) as raised:
        observe("mostly_right")
    assert "success" in str(raised.value) and "garbage" in str(raised.value)


def test_a_mode_that_is_not_even_a_string_raises() -> None:
    with pytest.raises(UnknownModeError):
        observe(4)  # type: ignore[arg-type]


def test_an_unknown_lie_kind_raises() -> None:
    with pytest.raises(UnknownLieError):
        observe(Mode.GARBAGE, lie="slightly_wrong")


def test_a_lie_asked_of_a_mode_that_does_not_lie_is_refused() -> None:
    """Ignoring the argument would hide the caller's bug: they asked for a
    specific failure and would have got a clean read that passes everything."""
    with pytest.raises(ValueError):
        observe(Mode.SUCCESS, lie=Lie.WRONG_AMOUNT)


def test_a_float_seed_raises_rather_than_being_rounded() -> None:
    with pytest.raises(TypeError):
        observe(Mode.GARBAGE, seed=1.5)  # type: ignore[arg-type]


def test_a_bool_seed_raises_because_bool_is_an_int_in_python() -> None:
    """`isinstance(True, int)` is True and `True == 1`, so a flag passed where
    a seed belonged would silently select the second lie."""
    with pytest.raises(TypeError):
        observe(Mode.GARBAGE, seed=True)  # type: ignore[arg-type]


def test_something_that_is_not_a_truth_is_refused() -> None:
    """Fail closed. Attribute access on a dict would fail later and further
    away, where the message no longer says what went wrong."""
    with pytest.raises(TypeError):
        observe(Mode.SUCCESS, truth={"party": "x"})  # type: ignore[arg-type]


def test_a_truth_whose_lines_do_not_add_up_is_refused() -> None:
    """SUCCESS mode promises a CLEAN read. A truth that fails a conservation
    law would make the clean mode fail one too, and every contrast in this file
    would be measured against a broken baseline."""
    with pytest.raises(ValueError):
        Truth(
            date="2026-04-01",
            party="Kumar Traders",
            net_paise=100_000,
            tax_paise=18_000,
            line_paise=(40_000,),
        )


def test_a_float_amount_in_the_truth_raises() -> None:
    with pytest.raises(TypeError):
        Truth(
            date="2026-04-01",
            party="Kumar Traders",
            net_paise=100_000.0,  # type: ignore[arg-type]
            tax_paise=18_000,
            line_paise=(118_000,),
        )


def test_a_bool_amount_in_the_truth_raises() -> None:
    """Money is never a flag. `checks.py::amount_is_integer_paise` and
    `conservation.py::_paise` already refuse bools; this matches them rather
    than inventing a third rule."""
    with pytest.raises(TypeError):
        Truth(
            date="2026-04-01",
            party="Kumar Traders",
            net_paise=True,  # type: ignore[arg-type]
            tax_paise=0,
            line_paise=(1,),
        )


def test_a_truth_dated_the_thirty_fourth_is_refused() -> None:
    """The 34th of a month is a misread 04th, not a date. A stub built on an
    impossible date would fail a format check for a reason that has nothing to
    do with the lie under test."""
    with pytest.raises(ValueError):
        Truth(
            date="2026-04-34",
            party="Kumar Traders",
            net_paise=100_000,
            tax_paise=0,
            line_paise=(100_000,),
        )


def test_a_truth_with_no_party_is_refused() -> None:
    with pytest.raises(ValueError):
        Truth(
            date="2026-04-01",
            party="   ",
            net_paise=100_000,
            tax_paise=0,
            line_paise=(100_000,),
        )


def test_a_truth_with_a_zero_line_is_refused() -> None:
    """A zero-rupee line is not a line. It would let a set of lines sum
    correctly while containing a figure nobody read properly."""
    with pytest.raises(ValueError):
        Truth(
            date="2026-04-01",
            party="Kumar Traders",
            net_paise=100_000,
            tax_paise=0,
            line_paise=(100_000, 0),
        )


def test_the_stub_refuses_to_hand_back_the_truth_while_claiming_to_lie() -> None:
    """THE RUNTIME HALF of the "did it actually lie" guard, and it is reachable:
    a truth whose party is already the name the wrong-party lie swaps in would
    produce an observation identical to the honest one.

    Defect J1's lesson - a test of a guard says nothing about whether the guard
    is installed - cuts the other way too. The test above proves the lies
    differ today; this stops a future truth silently disarming one.
    """
    same_name = Truth(
        date="2026-04-01",
        party=ANOTHER_REAL_PARTY,
        net_paise=100_000,
        tax_paise=0,
        line_paise=(100_000,),
    )
    with pytest.raises(StubToldTheTruthError):
        observe(Mode.GARBAGE, truth=same_name, lie=Lie.WRONG_PARTY)


def test_the_control_that_same_lie_on_an_ordinary_truth_still_lies() -> None:
    """THE CONTROL on the test above. A guard that raised on every garbage call
    would pass it and make the whole GARBAGE mode useless."""
    seen = observe(Mode.GARBAGE, lie=Lie.WRONG_PARTY)
    assert seen.party.value == ANOTHER_REAL_PARTY


# ---- the static half: no network, no runtime path ---------------------------


def _imports_of(path: pathlib.Path) -> set[str]:
    """Every module name this file imports, however it imports it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _files_importing(module: str, root: pathlib.Path) -> set[str]:
    """Every `.py` under `root` that imports `module`.

    An AST scan, not a grep: this module's name appears in docstrings and
    comments all over this file, and a substring search cannot tell a mention
    from an import.
    """
    found: set[str] = set()
    for path in root.rglob("*.py"):
        try:
            imported = _imports_of(path)
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue
        if module in imported:
            found.add(str(path.relative_to(REPO)))
    return found


def test_the_lying_model_imports_nothing_that_could_reach_a_network() -> None:
    """A test double that could open a socket, read a clock or draw a random
    number is not reproducible, and one that reaches a network in CI is a flaky
    build nobody traces back to a stub."""
    allowed = {
        "__future__",
        "dataclasses",
        "enum",
        "typing",
        "accountant.cage.wall",
        "accountant.cage.confidence",
    }
    assert _imports_of(SHIPPED / "cage" / "lying.py") <= allowed


def test_the_lying_model_takes_only_inert_types_from_the_wall() -> None:
    """`Observation` and `Field` cannot be posted. If this stub ever imported
    `LedgerEntry`, a liar would be one call away from something writable."""
    source = (SHIPPED / "cage" / "lying.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    taken: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "accountant.cage.wall":
            taken.update(alias.name for alias in node.names)
    assert taken == {"Field", "Observation"}


def test_no_shipped_module_imports_the_lying_model() -> None:
    """A liar on a runtime path is worse than no cage at all: it would put
    invented amounts in front of a customer with the product's own confidence
    attached. Tests may import it. Nothing that ships may."""
    assert _files_importing("accountant.cage.lying", SHIPPED) == set()


def test_the_control_the_scanner_can_see_this_file_importing_it() -> None:
    """THE CONTROL on the scan above, and it is the same needle in a different
    haystack. A scanner that returned an empty set everywhere - a typo in the
    module name, a walk over the wrong directory - would pass the test above
    while proving nothing."""
    assert "tests/test_lying.py" in _files_importing("accountant.cage.lying", TESTS)


def test_the_stub_builds_observations_and_nothing_postable() -> None:
    """Belt and braces on the import check above: what comes out is the inert
    type, whatever mode was asked for."""
    for mode in Mode:
        assert isinstance(observe(mode), Observation)


# ---- REVIEW NOTES -----------------------------------------------------------
#
# Read back cold, as somebody who did not write it. Six things were wrong; four
# are fixed above and two are recorded here because fixing them properly is not
# this file's job.
#
# 1. FIXED - the seed control compared whole `Observation`s.
#    `test_the_control_a_different_seed_selects_a_different_lie` asserted
#    `observe(seed=0) != observe(seed=1)`. Every field's `source` carries the
#    lie's name, so two observations differ the moment the LABEL differs -
#    the test would have passed a stub that renamed the lie and changed no
#    number at all. This is the precise trap `figures()` was written to avoid
#    and the test walked into it. Now compares figures.
#
# 2. FIXED - "the lie changed something" was too weak a claim.
#    The old loop asserted only that a garbage read differed from an honest
#    one somewhere. A wrong-party lie that corrupted the DATE instead would
#    have satisfied it, and so would a self-consistent lie that scaled the
#    lines and forgot the tax. Replaced with a table naming which figures each
#    lie moves, asserted exactly - no fewer and no more. It also fails on a
#    `Lie` member added without an entry, which the old loop did not.
#
# 3. FIXED - nothing asserted the stub's own provenance.
#    Every test read `source` for the lie's name; none checked that a field
#    says it came from the lying model at all. An observation that leaked into
#    a log or a fixture would have been indistinguishable from a real read.
#    `test_every_field_of_every_mode_says_it_came_from_the_lying_model` closes
#    it across all four modes.
#
# 4. FIXED - the mode-distinctness test deduplicated by `repr`.
#    Comparing a rendering of the values instead of the values, in a file whose
#    whole subject is figures that must actually differ. Now pairwise `!=`.
#
# 5. NOT FIXED - `_imports_of` has no control of its own.
#    `test_the_lying_model_imports_nothing_that_could_reach_a_network` asserts
#    a SUBSET, so a scanner that returned the empty set would pass it. It is
#    partly covered: `test_the_lying_model_takes_only_inert_types_from_the_wall`
#    asserts an exact non-empty set from the same parse, so a blind scanner
#    fails there. Left as is rather than adding a second control that measures
#    the first one - the honest fix is one shared scanner helper for this file
#    and `tests/test_the_wall.py`, and that touches a file this task does not
#    own.
#
# 6. NOT FIXED - determinism is only ever checked inside one interpreter.
#    Every determinism test here calls `observe` twice in the same process. A
#    stub that memoised its first answer, or one seeded from a hash whose salt
#    is per-process, would pass all of them. The module imports nothing that
#    could do either - which is asserted - so the risk is bounded, but the
#    claim "same seed, same observation, on any machine" is not what is
#    measured. Measuring it needs a subprocess, and this file would then do IO
#    beyond reading source. Recorded rather than half-done.

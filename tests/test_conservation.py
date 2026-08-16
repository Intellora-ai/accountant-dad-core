"""The four conservation laws, which are the cheapest safety this system has.

WHY THIS FILE EXISTS
--------------------
Every accuracy number in this project needs labelled data: to say "we read the
amount right 99.5% of the time" you need a pile of invoices where somebody
already knows the right answer. This repository has no such pile, and building
one is `H-02`.

Conservation needs none of it. Debits and credits are equal or they are not.
The line items sum to the total or they do not. These are quantities that MUST
be equal, and a law like that needs no expert, no labels, no model and no
network. It holds or it fails, and it fails identically on a machine that has
never seen an invoice.

That is why this is the first module of the safety cage and why it is pure.

THE THREE VERDICTS, AND WHY THERE ARE THREE
--------------------------------------------
`PASS` and `FAIL` are obvious. The third is the one that matters:

    INDETERMINATE   the law could not be evaluated, because a number it needs
                    was not read

A two-verdict system has to call that case one or the other, and both are
wrong. Calling it PASS means an unread field silently authorises a write -
which is the exact defect this repository keeps finding, where absence of
evidence is treated as evidence of absence. Calling it FAIL means every bill
whose line items were not itemised is refused.

So it is its own verdict, and the caller BLOCKS on it. The difference between
"this is wrong" and "I could not check" reaches the person reading the refusal.

WHAT THIS FILE DOES NOT PROVE
------------------------------
It does not prove the amounts are RIGHT. A bill misread as a consistent
Rs 10,000 instead of Rs 1,000 - every line scaled by ten - passes all four laws.
That is failure mode F-02 in the plan, it is not detectable by arithmetic, and
nothing here claims otherwise. Conservation catches the inconsistent errors,
which are most of them; it is deliberately not the only guard.

NO NETWORK, NO FIXTURES, NO IO. Every test here is integer arithmetic.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from accountant import money
from accountant.cage import conservation
from accountant.cage.conservation import (
    LAWS,
    ConservationResult,
    Verdict,
    balance_delta_equals_entry,
    debits_equal_credits,
    lines_sum_to_total,
    net_plus_tax_equals_gross,
    run,
)

CONSERVATION_FILE = Path(conservation.__file__)
MONEY_FILE = Path(money.__file__)

#: Anything that can open a socket, and anything that can touch a disk. This
#: module may import none of it - that is what makes it evaluable with no
#: fixtures, no network and no Tally, which is the whole reason it is first.
IMPURE_MODULES = frozenset(
    {
        "asyncio",
        "http",
        "httpx",
        "io",
        "os",
        "pathlib",
        "requests",
        "shutil",
        "socket",
        "sqlite3",
        "ssl",
        "subprocess",
        "tempfile",
        "urllib",
    }
)

#: The exception, written down: `money.format_inr` is a pure `int -> str`
#: function whose own imports are `__future__` and nothing else, and a refusal
#: that says `119999 paise` is not a sentence an Indian reader can act on.
#: `docs/interfaces/conservation.md` carries the same allow-list in words.
ALLOWED_IMPORTS = frozenset(
    {"__future__", "accountant", "dataclasses", "enum", "typing"}
)


def imported_roots(path: Path) -> set[str]:
    """The top-level module every import in one file reaches for.

    The same scan `tests/test_ingest.py::imported_roots` runs over the load
    path. Copied rather than imported across test files on purpose: a test that
    depends on another test file's helper fails for two reasons at once.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            roots.add(node.module.split(".")[0])
    return roots


def every_sentence() -> list[str]:
    """One `said` from each law, on a failing and on a passing run.

    Built from calls rather than from the module's string constants, because
    the figures are interpolated and it is the interpolation that was wrong.
    """
    passing = run(
        debit_paise=120_000,
        credit_paise=120_000,
        line_paise=(40_000, 80_000),
        total_paise=120_000,
        net_paise=120_000,
        tax_paise=0,
        gross_paise=120_000,
        balance_before_paise=0,
        balance_after_paise=120_000,
    )
    failing = run(
        debit_paise=120_000,
        credit_paise=119_999,
        line_paise=(40_000, 79_999),
        total_paise=120_000,
        net_paise=100_000,
        tax_paise=18_001,
        gross_paise=118_000,
        balance_before_paise=0,
        balance_after_paise=119_999,
    )
    return [r.said for r in (*passing, *failing)]


# ---- debits equal credits ---------------------------------------------------


def test_two_equal_legs_balance() -> None:
    result = debits_equal_credits(100_000, 100_000)
    assert result.verdict is Verdict.PASS


def test_the_control_one_paisa_apart_does_not_balance() -> None:
    """THE CONTROL on the test above.

    A law that returned PASS unconditionally would pass the first test and fail
    this one. One paisa is the smallest possible disagreement, so if the law
    notices this it notices everything larger.
    """
    result = debits_equal_credits(100_000, 100_001)
    assert result.verdict is Verdict.FAIL


def test_the_failure_sentence_names_both_sides_and_the_difference() -> None:
    """A person reading the refusal has to be able to act on it. "Unbalanced"
    is not actionable; "₹1,000.00 against ₹1,000.01, out by 1 paisa" is.

    THE ASSERTION WAS CORRECTED 2026-08-13, not weakened. It read
    `assert "100000" in said and "100001" in said`, which pinned the defect
    rather than the behaviour: the raw paise count reached a person's screen,
    against the owner's rule that every INR amount a user sees carries Indian
    grouping and the rupee sign. This docstring already described the rupee
    form on the day the assertion pinned the paise form.
    """
    said = debits_equal_credits(100_000, 100_001).said

    assert "₹1,000.00" in said
    assert "₹1,000.01" in said
    assert "out by 1 paisa" in said


def test_an_unread_debit_is_indeterminate_not_a_pass() -> None:
    """Absence of evidence is not evidence of absence. This is the root cause
    family R-5, and treating `None` as zero is exactly how it fires."""
    assert debits_equal_credits(None, 100_000).verdict is Verdict.INDETERMINATE


def test_an_unread_credit_is_indeterminate_too() -> None:
    assert debits_equal_credits(100_000, None).verdict is Verdict.INDETERMINATE


def test_a_float_amount_raises_rather_than_being_coerced() -> None:
    """Money is integer paise everywhere in this system. Accepting a float here
    would let 0.1 + 0.2 into a statutory record."""
    with pytest.raises(TypeError):
        debits_equal_credits(100_000.0, 100_000)  # type: ignore[arg-type]


def test_a_bool_amount_raises_because_bool_is_an_int_in_python() -> None:
    """`isinstance(True, int)` is True and `True == 1`. A caller that passed a
    flag where an amount belonged would otherwise balance a one-paisa entry.
    `checks.py::amount_is_integer_paise` already refuses bools for the same
    reason; this matches it rather than inventing a second rule."""
    with pytest.raises(TypeError):
        debits_equal_credits(True, 1)  # type: ignore[arg-type]


# ---- lines sum to total -----------------------------------------------------


def test_line_items_that_sum_to_the_total_pass() -> None:
    result = lines_sum_to_total((40_000, 60_000), 100_000)
    assert result.verdict is Verdict.PASS


def test_the_control_line_items_one_paisa_short_fail() -> None:
    """THE CONTROL. This is the law that catches a single misread digit in one
    line of a bill whose total was read correctly."""
    result = lines_sum_to_total((40_000, 59_999), 100_000)
    assert result.verdict is Verdict.FAIL


def test_no_lines_and_a_zero_total_pass() -> None:
    """An empty tuple means "we read the lines and there were none", which is
    consistent with a zero total. It is not the same as not having looked."""
    assert lines_sum_to_total((), 0).verdict is Verdict.PASS


def test_no_lines_but_a_non_zero_total_fails() -> None:
    """We read the lines, there were none, and yet money is claimed. That is a
    contradiction, not an unknown."""
    assert lines_sum_to_total((), 100_000).verdict is Verdict.FAIL


def test_unread_lines_are_indeterminate_not_an_empty_list() -> None:
    """`None` is "we did not read the lines"; `()` is "there are none". A reader
    that collapses the two turns an un-itemised bill into a passing one."""
    assert lines_sum_to_total(None, 100_000).verdict is Verdict.INDETERMINATE


def test_an_unread_total_is_indeterminate() -> None:
    assert lines_sum_to_total((40_000, 60_000), None).verdict is Verdict.INDETERMINATE


def test_a_float_line_raises() -> None:
    with pytest.raises(TypeError):
        lines_sum_to_total((40_000.0, 60_000), 100_000)  # type: ignore[arg-type]


# ---- net plus tax equals gross ----------------------------------------------


def test_net_plus_tax_equals_gross_passes() -> None:
    result = net_plus_tax_equals_gross(100_000, 18_000, 118_000)
    assert result.verdict is Verdict.PASS


def test_the_control_net_plus_tax_one_paisa_off_fails() -> None:
    """THE CONTROL. This is the law that catches a misread tax figure on a
    bill whose net and gross were both read correctly."""
    result = net_plus_tax_equals_gross(100_000, 18_000, 118_001)
    assert result.verdict is Verdict.FAIL


def test_a_bill_with_no_tax_passes_when_net_equals_gross() -> None:
    """Zero tax is a fact, not a gap. A non-GST purchase is the ONLY thing this
    product auto-posts, so this path has to be exactly right."""
    assert net_plus_tax_equals_gross(100_000, 0, 100_000).verdict is Verdict.PASS


def test_unread_tax_is_indeterminate_rather_than_zero() -> None:
    """This is the single most dangerous coercion in the whole system: reading
    an unread tax field as zero posts a GST bill without its input credit, and
    that is real money lost with nothing on screen to notice."""
    result = net_plus_tax_equals_gross(100_000, None, 118_000)
    assert result.verdict is Verdict.INDETERMINATE


def test_unread_net_or_gross_is_indeterminate() -> None:
    no_net = net_plus_tax_equals_gross(None, 18_000, 118_000)
    no_gross = net_plus_tax_equals_gross(100_000, 18_000, None)
    assert no_net.verdict is Verdict.INDETERMINATE
    assert no_gross.verdict is Verdict.INDETERMINATE


# ---- balance delta equals the entry -----------------------------------------


def test_a_balance_that_moved_by_exactly_the_entry_passes() -> None:
    result = balance_delta_equals_entry(500_000, 600_000, 100_000)
    assert result.verdict is Verdict.PASS


def test_the_control_a_balance_that_moved_by_the_wrong_amount_fails() -> None:
    """THE CONTROL. This is the law that catches a write TallyPrime accepted and
    then applied differently from what was asked."""
    result = balance_delta_equals_entry(500_000, 600_001, 100_000)
    assert result.verdict is Verdict.FAIL


def test_a_balance_that_did_not_move_at_all_fails() -> None:
    assert balance_delta_equals_entry(500_000, 500_000, 100_000).verdict is Verdict.FAIL


def test_an_unread_balance_is_indeterminate() -> None:
    result = balance_delta_equals_entry(None, 600_000, 100_000)
    assert result.verdict is Verdict.INDETERMINATE


def test_a_negative_delta_matches_a_negative_entry() -> None:
    """Money leaving is an ordinary case, not an error. The law compares, it
    does not assume a direction."""
    result = balance_delta_equals_entry(600_000, 500_000, -100_000)
    assert result.verdict is Verdict.PASS


# ---- run: all four, always, in a fixed order --------------------------------


def test_run_always_returns_exactly_four_results() -> None:
    """Not "the ones that applied". A caller that receives three results cannot
    tell which law was skipped or why."""
    results = run(
        debit_paise=100_000,
        credit_paise=100_000,
        line_paise=(100_000,),
        total_paise=100_000,
        net_paise=100_000,
        tax_paise=0,
        gross_paise=100_000,
        balance_before_paise=0,
        balance_after_paise=100_000,
    )
    assert len(results) == 4


def test_run_returns_the_laws_in_one_fixed_order() -> None:
    """The order is part of the contract, so a caller may index it and a log
    line reads the same on every run."""
    results = run(
        debit_paise=None,
        credit_paise=None,
        line_paise=None,
        total_paise=None,
        net_paise=None,
        tax_paise=None,
        gross_paise=None,
        balance_before_paise=None,
        balance_after_paise=None,
    )
    assert tuple(r.law for r in results) == LAWS


def test_run_on_a_clean_purchase_passes_every_law() -> None:
    results = run(
        debit_paise=100_000,
        credit_paise=100_000,
        line_paise=(40_000, 60_000),
        total_paise=100_000,
        net_paise=100_000,
        tax_paise=0,
        gross_paise=100_000,
        balance_before_paise=0,
        balance_after_paise=100_000,
    )
    assert all(r.verdict is Verdict.PASS for r in results)


def test_the_control_one_broken_law_does_not_stop_the_others_being_evaluated() -> None:
    """THE CONTROL on the test above. A run that stopped at the first failure
    would report one problem when there are two, and the person would fix one
    and resubmit into the second."""
    results = run(
        debit_paise=100_000,
        credit_paise=100_001,
        line_paise=(40_000, 59_999),
        total_paise=100_000,
        net_paise=100_000,
        tax_paise=0,
        gross_paise=100_000,
        balance_before_paise=0,
        balance_after_paise=100_000,
    )
    assert sum(1 for r in results if r.verdict is Verdict.FAIL) == 2


def test_every_result_carries_a_non_empty_sentence() -> None:
    """A verdict with no sentence cannot be shown to a person, and this product
    refuses in plain language or not at all."""
    results = run(
        debit_paise=100_000,
        credit_paise=100_001,
        line_paise=None,
        total_paise=100_000,
        net_paise=100_000,
        tax_paise=0,
        gross_paise=100_000,
        balance_before_paise=None,
        balance_after_paise=None,
    )
    assert all(r.said.strip() for r in results)


def test_running_twice_on_the_same_input_gives_the_identical_answer() -> None:
    """Determinism, rule 11.2.10. Pure arithmetic has no excuse to vary, and a
    test that says so is what stops someone adding a clock or a cache later."""
    kwargs = {
        "debit_paise": 100_000,
        "credit_paise": 100_001,
        "line_paise": (40_000,),
        "total_paise": 100_000,
        "net_paise": 100_000,
        "tax_paise": 0,
        "gross_paise": 100_000,
        "balance_before_paise": 0,
        "balance_after_paise": 100_000,
    }
    assert run(**kwargs) == run(**kwargs)  # type: ignore[arg-type]


# ---- the figures a person actually reads ------------------------------------
# The owner's rule, closed: every INR amount visible to a user is formatted with
# Indian grouping, through `accountant.money.format_inr` and no second copy.
# `119999 paise` is not a figure anybody reconciles a bill against.


def test_no_law_ever_prints_a_bare_paise_count_at_a_person() -> None:
    """THE SWEEP, over every sentence all four laws can produce.

    A count under a rupee stays a count - "out by 1 paisa" is what makes a
    misread digit obvious, and "out by ₹0.01" reads like a rounding artefact.
    Anything a rupee or larger is an amount and goes through `format_inr`, so
    a three-digit paise figure in any sentence is the defect coming back.
    """
    for said in every_sentence():
        for figure in re.findall(r"(\d+) pais[ae]", said):
            assert int(figure) < 100, said


def test_one_paisa_is_singular_and_two_paise_is_plural() -> None:
    """One paisa, two paise. It is on the screen of a reader who will notice,
    and a product that gets the reader's own currency wrong is not trusted
    about the amount either."""
    one = debits_equal_credits(120_000, 119_999).said
    two = debits_equal_credits(120_000, 119_998).said

    assert "out by 1 paisa" in one
    assert "out by 2 paise" in two


def test_a_gap_of_a_rupee_or_more_is_shown_in_rupees() -> None:
    """THE CONTROL on the singular above. A helper that always said "paisa"
    would pass that test and print "out by 150 paisa" here."""
    said = lines_sum_to_total((40_000,), 40_150).said

    assert "out by ₹1.50" in said
    assert "150" not in said


def test_a_lakh_is_grouped_the_indian_way_and_not_the_western_kind() -> None:
    """Ten lakh is `₹10,00,000.00`. `f"{whole:,}"` writes `1,000,000` - a
    figure a person reconciling against a bank statement reads as ten times
    itself, which is the whole reason `money.py` exists."""
    said = debits_equal_credits(100_000_000, 99_999_999).said

    assert "₹10,00,000.00" in said
    assert "1,000,000" not in said


def test_a_passing_law_records_its_figure_in_rupees_too() -> None:
    """A PASS sentence reaches the audit log, and months later somebody asks
    "passed on what numbers". They should not have to divide by a hundred."""
    said = debits_equal_credits(120_000, 120_000).said

    assert "₹1,200.00 on both sides" in said


def test_a_balance_that_moved_the_wrong_way_reads_as_a_negative_amount() -> None:
    """Money leaving is ordinary, so the sentence has to render a negative
    delta rather than raise or print a bare `-100000`."""
    said = balance_delta_equals_entry(600_000, 500_000, 100_000).said

    assert "₹-1,000.00" in said


# ---- the exception to "this module depends on nothing" ----------------------
# `docs/interfaces/conservation.md` used to say the dependency list was empty.
# It now says `money`, and nothing else, and these are what hold it there.


def test_conservation_imports_nothing_that_could_reach_a_disk_or_a_socket() -> None:
    """The rule's PURPOSE, which is what the exception may not damage: this
    module stays evaluable with no fixtures, no network and no Tally, so its
    verdict is the same on a machine that has never seen an invoice."""
    assert not imported_roots(CONSERVATION_FILE) & IMPURE_MODULES


def test_the_control_the_same_scan_finds_the_network_where_it_really_is() -> None:
    """THE CONTROL. A scan that returned an empty set for every file would pass
    the test above while `conservation.py` imported `socket` on line one. This
    is the defect class `wall.py` already recorded once: an AST guard running
    over an empty set asserts nothing and keeps passing forever."""
    roots = imported_roots(Path(conservation.__file__).parent.parent / "web/app.py")

    assert roots & IMPURE_MODULES


def test_the_only_module_conservation_may_talk_to_is_money() -> None:
    """The exception is one function, named. An allow-list rather than a
    deny-list, because the failure to catch is the import nobody thought of."""
    assert imported_roots(CONSERVATION_FILE) <= ALLOWED_IMPORTS
    assert "accountant" in imported_roots(CONSERVATION_FILE)


def test_the_control_money_itself_still_depends_on_nothing() -> None:
    """WHAT WOULD MAKE THE EXCEPTION UNSAFE, asserted rather than trusted.

    `money` is allowed in because it is a pure `int -> str` function importing
    only `__future__`. The day it imports a config file, a locale or a store,
    that purity moves into `conservation` behind an import nobody re-reads -
    so the day it does, this fails.
    """
    assert imported_roots(MONEY_FILE) == {"__future__"}


def test_a_result_cannot_be_mutated_after_it_is_made() -> None:
    """Frozen, like the nine types in `schema.py`. A verdict that can be edited
    after the fact is not evidence."""
    result = ConservationResult(
        law="debits_equal_credits", verdict=Verdict.PASS, said="ok"
    )
    with pytest.raises(AttributeError):
        result.verdict = Verdict.FAIL  # type: ignore[misc]

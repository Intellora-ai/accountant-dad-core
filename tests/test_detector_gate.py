"""The detector launch gate: decided on the aggregate AND the worst department.

Owner decision D-22, 2026-08-10:

    Use both aggregate and worst-department results. For launch, do not hide a
    department that fails.

        aggregate 6.29        = PASS   (target <= 10)
        worst department      = 33.33  = NOT_PASSED
        overall launch gate   = NOT_PASSED

NOT_PASSED is the correct answer today. These tests exist so the report keeps
saying it, and so the four ways of making it say something nicer all fail here
first:

    tune a threshold        -> the target is the owner constant, pinned below
    exclude a department    -> the departments must add up to the aggregate
    change the denominator  -> same check, on the clean-entry count
    delete a hard case      -> the examples must account for every false alarm

Three of those four are refused by `DetectorGate.__post_init__` rather than by
a reviewer noticing, so they raise on construction instead of producing a
quieter number.

Everything measured here comes from the UK central-government spend files in
`accountant/ingest/fixtures/`. They are real published ledgers with no injected
errors, so every entry is a clean entry and every flag on one is a false alarm.
"""

from __future__ import annotations

import hashlib

import pytest

from accountant.detect import detectors
from accountant.ingest import sources, spend
from accountant.score import calibration as cal
from accountant.score.book import Book, GroundTruth, InjectedError
from accountant.score.harness import (
    N1_MAX_FALSE_ALARMS_PER_100,
    PERCENT_SCALE,
    DetectorGate,
    FalseAlarmExample,
    GateVerdict,
    ScopeResult,
    WithdrawnCost,
    gate_from_books,
    scaled_rate,
)
from accountant.score.report import render_gate

# The four numbers the owner stated, in hundredths, pinned so a drift in any
# one of them fails rather than quietly changing the verdict.
AGGREGATE_HUNDREDTHS = 629  # 6.29 per 100, 9 of 143 clean
HELD_OUT_HUNDREDTHS = 290  # 2.90 per 100, 2 of 69 clean
WORST_DEPARTMENT_HUNDREDTHS = 3333  # 33.33 per 100, 7 of 21 clean, DHSC
WORST_DEPARTMENT = "DHSC"

# Every department, and the false alarms and clean entries it carries.
PER_DEPARTMENT: tuple[tuple[str, int, int], ...] = (
    ("MHCLG", 0, 29),
    ("DHSC", 7, 21),
    ("DFT", 0, 24),
    ("DWP", 1, 27),
    ("DEFRA", 1, 19),
    ("HMT", 0, 23),
    ("DBT", 0, 0),
)

# With `first_use` switched back on, at the settings the other three ship at.
WITH_FIRST_USE_AGGREGATE_HUNDREDTHS = 3636  # 36.36 per 100, 52 of 143
WITH_FIRST_USE_HELD_OUT_HUNDREDTHS = 4203  # 42.03 per 100, 29 of 69


def books() -> dict[str, Book]:
    """Every committed department, keyed by its code, in a fixed order."""
    return {
        s.code: spend.as_score_book(spend.load_source(s)) for s in sources.ALL_SOURCES
    }


def held_out_codes(loaded: dict[str, Book]) -> tuple[str, ...]:
    """The held-out half, by the split the calibration procedure already fixes."""
    code_of = {book.company: code for code, book in loaded.items()}
    _, held = cal.split(list(loaded.values()))
    return tuple(code_of[book.company] for book in held)


def measure() -> DetectorGate:
    """One full gate over the real files, measured by running the detectors."""
    loaded = books()
    return gate_from_books(loaded, held_out=held_out_codes(loaded))


@pytest.fixture(scope="module")
def gate() -> DetectorGate:
    return measure()


@pytest.fixture(scope="module")
def rendered(gate: DetectorGate) -> str:
    return render_gate(gate)


def synthetic(
    departments: tuple[tuple[str, int, int], ...],
    *,
    held_out: tuple[int, int] = (0, 1),
) -> DetectorGate:
    """A gate built from stated counts, for testing the decision itself.

    One example per false alarm, because the gate refuses to be built with a
    case missing - which is the point of that check.
    """
    rows = tuple(
        ScopeResult(scope=name, false_alarms=alarms, clean_entries=clean)
        for name, alarms, clean in departments
    )
    examples = tuple(
        FalseAlarmExample(
            voucher_id=f"{name}-{i:05d}",
            scope=name,
            party="A PARTY",
            account="An account",
            amount_paise=1_000,
            detector="magnitude",
            severity=2,
            reason="a stated reason",
        )
        for name, alarms, _ in departments
        for i in range(alarms)
    )
    return DetectorGate(
        aggregate=ScopeResult(
            scope="all departments",
            false_alarms=sum(r.false_alarms for r in rows),
            clean_entries=sum(r.clean_entries for r in rows),
        ),
        held_out=ScopeResult(
            scope="held-out half",
            false_alarms=held_out[0],
            clean_entries=held_out[1],
        ),
        departments=rows,
        examples=examples,
    )


# ---------------------------------------------------------------------------
# THE GATE VERDICT
# ---------------------------------------------------------------------------


def test_the_gate_is_not_passed_while_any_department_exceeds_the_target(
    gate: DetectorGate,
) -> None:
    """The whole of D-22, on the real files.

    The aggregate is inside the target and one department is three times over
    it. The gate says NOT_PASSED, and it says which department.
    """
    assert gate.aggregate_verdict is GateVerdict.MET
    assert gate.aggregate.per_100_hundredths == AGGREGATE_HUNDREDTHS

    worst = gate.worst_department
    assert worst is not None
    assert worst.scope == WORST_DEPARTMENT
    assert worst.per_100_hundredths == WORST_DEPARTMENT_HUNDREDTHS
    assert not worst.within(gate.target_per_100)

    assert gate.verdict is GateVerdict.NOT_MET
    assert gate.verdict.value == "NOT_PASSED"


def test_the_gate_is_not_decided_by_the_aggregate_alone() -> None:
    """A passing aggregate over a failing department is still NOT_PASSED.

    12 of 200 clean entries is 6.00 per 100, comfortably inside the target of
    10, and one of the two departments in it is at 40.00. A gate that read the
    aggregate on its own would call this a PASS.
    """
    hidden = synthetic((("QUIET", 4, 180), ("LOUD", 8, 20)), held_out=(4, 180))

    assert hidden.aggregate.per_100_hundredths == 600
    assert hidden.aggregate_verdict is GateVerdict.MET
    assert hidden.held_out_verdict is GateVerdict.MET

    worst = hidden.worst_department
    assert worst is not None
    assert worst.scope == "LOUD"
    assert worst.per_100_hundredths == 4000

    assert hidden.verdict is GateVerdict.NOT_MET


def test_the_gate_does_pass_when_every_department_passes() -> None:
    """The other direction, so NOT_PASSED is a measurement and not a default."""
    clean = synthetic((("QUIET", 4, 180), ("ALSO_QUIET", 1, 20)), held_out=(1, 20))

    assert clean.aggregate_verdict is GateVerdict.MET
    assert clean.failing_departments == ()
    assert clean.verdict is GateVerdict.MET
    assert clean.verdict.value == "PASS"


def test_an_unmeasured_department_is_not_a_pass(gate: DetectorGate) -> None:
    """DBT publishes an empty narration in every row, so nothing was measured.

    Absent evidence is not evidence. It is reported as a hole, and it counts
    against the gate rather than being skipped.
    """
    (dbt,) = [d for d in gate.departments if d.scope == "DBT"]
    assert dbt.clean_entries == 0
    assert dbt.per_100_hundredths is None
    assert not dbt.within(gate.target_per_100)
    assert dbt in gate.failing_departments
    assert dbt in gate.unmeasured_departments


def test_both_the_aggregate_and_the_worst_department_are_in_the_reasons(
    gate: DetectorGate,
) -> None:
    """Whichever way each one went, both are stated. One number is not a gate."""
    reasons = "\n".join(gate.reasons)
    assert "aggregate 6.29 per 100 (9 of 143 clean) - PASS" in reasons
    assert "worst department DHSC 33.33 per 100 (7 of 21 clean) - NOT_PASSED" in reasons
    assert "held-out half 2.90 per 100 (2 of 69 clean) - PASS" in reasons


# ---------------------------------------------------------------------------
# THE HEADLINE
# ---------------------------------------------------------------------------


def test_the_worst_department_is_named_in_the_headline(rendered: str) -> None:
    """A failing department in a table further down is a department nobody reads.

    The verdict, the aggregate, the failing department and its number are all
    inside the first eight lines.
    """
    headline = "\n".join(rendered.splitlines()[:8])

    assert "NOT_PASSED" in headline
    assert WORST_DEPARTMENT in headline
    assert "33.33" in headline
    assert "7 of 21 clean" in headline
    assert "6.29" in headline


def test_the_headline_carries_the_target_it_was_judged_against(
    rendered: str,
) -> None:
    headline = "\n".join(rendered.splitlines()[:9])
    assert f"<= {N1_MAX_FALSE_ALARMS_PER_100} per 100 clean entries" in headline


# ---------------------------------------------------------------------------
# EVERY DEPARTMENT, THE PASSING ONES INCLUDED
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("code", "alarms", "clean"), PER_DEPARTMENT)
def test_every_department_is_measured_with_its_own_numbers(
    gate: DetectorGate, code: str, alarms: int, clean: int
) -> None:
    (row,) = [d for d in gate.departments if d.scope == code]
    assert (row.false_alarms, row.clean_entries) == (alarms, clean)


@pytest.mark.parametrize(("code", "alarms", "clean"), PER_DEPARTMENT)
def test_every_department_appears_in_the_report(
    rendered: str, code: str, alarms: int, clean: int
) -> None:
    """Including the ones at 0.00.

    A report that only prints the noisy departments hides which ones were
    measured at all, and hides DBT, which was not measured at any rate.
    """
    table = rendered.split("Every department")[1]
    row = [
        line
        for line in table.splitlines()
        if line.split()[:1] == [code] and len(line.split()) >= 5
    ]
    assert row, f"{code} has no row in the department table"
    assert f"{alarms}" in row[0].split()
    assert f"{clean}" in row[0].split()


def test_the_departments_add_up_to_the_aggregate(gate: DetectorGate) -> None:
    """The check that makes dropping a department impossible rather than rude."""
    assert sum(d.false_alarms for d in gate.departments) == gate.aggregate.false_alarms
    assert (
        sum(d.clean_entries for d in gate.departments) == gate.aggregate.clean_entries
    )
    assert gate.aggregate.clean_entries == 143
    assert gate.aggregate.false_alarms == 9


# ---------------------------------------------------------------------------
# DENOMINATOR, FORMULA, EXAMPLES
# ---------------------------------------------------------------------------


def test_the_report_carries_the_denominator(rendered: str, gate: DetectorGate) -> None:
    assert "Denominator" in rendered
    assert "clean entry" in gate.denominator
    assert "Silencing a flag cannot shrink it" in gate.denominator
    # The stated denominator is the one the number was actually divided by.
    assert gate.aggregate.clean_entries == sum(
        len(book.entries) for book in books().values()
    )


def test_the_report_carries_the_formula(rendered: str, gate: DetectorGate) -> None:
    assert "Formula" in rendered
    assert "false_alarms * 100 <= target * clean_entries" in gate.formula
    assert str(PERCENT_SCALE) in gate.formula


def test_the_stated_formula_is_the_arithmetic_the_code_runs() -> None:
    """The formula in the report is checked against the function, not trusted.

    Written out by hand for the worst department, so a formula that drifted
    away from `scaled_rate` fails here instead of being decorative.
    """
    by_hand = (7 * PERCENT_SCALE * 2 + 21) // (21 * 2)
    assert by_hand == scaled_rate(7, 21, PERCENT_SCALE)
    assert by_hand == WORST_DEPARTMENT_HUNDREDTHS


def test_the_report_carries_every_false_alarm(
    rendered: str, gate: DetectorGate
) -> None:
    """All nine, with the evidence, not a sample of the comfortable ones."""
    assert len(gate.examples) == 9
    assert len({e.voucher_id for e in gate.examples}) == gate.aggregate.false_alarms
    assert "Every false alarm - all 9, none held back" in rendered
    for e in gate.examples:
        assert e.voucher_id in rendered
        assert e.party in rendered
        assert e.account in rendered


def test_the_false_alarm_examples_carry_the_evidence_that_raised_them(
    gate: DetectorGate,
) -> None:
    """A flag with no evidence cannot be dismissed quickly, which breaks N1."""
    for e in gate.examples:
        assert e.reason.strip()
        assert e.detector in {detectors.name_of(d) for d in detectors.ACTIVE_DETECTORS}
        assert e.amount_paise > 0


# ---------------------------------------------------------------------------
# THE FINDING: SIX OF THE NINE ARE ONE ACCOUNT
# ---------------------------------------------------------------------------


def test_six_of_the_nine_false_alarms_are_one_account(
    gate: DetectorGate, rendered: str
) -> None:
    """Where the leverage is: one ceiling, not one threshold.

    DHSC `Additions NCB PDC` is Public Dividend Capital - lumpy capital
    injection - and its ceiling is taken from a ten-entry history that six
    different NHS trusts each post past. That single account is the whole of
    the worst department's overshoot.
    """
    concentration = gate.concentration
    assert concentration is not None
    assert concentration.scope == WORST_DEPARTMENT
    assert concentration.account == "Additions NCB PDC"
    assert concentration.entries == 6
    assert concentration.of_total == 9
    assert concentration.detectors == ("magnitude",)
    assert concentration.share_hundredths == 6667  # 66.67% of every false alarm

    assert "Additions NCB PDC" in rendered
    assert "6 of 9" in rendered
    # The ceiling itself, and the ten entries it was taken from.
    assert "21300000 paise across 10 entries" in rendered


def test_the_six_are_six_different_parties_on_one_account(
    gate: DetectorGate,
) -> None:
    """Six trusts, one account. That is one wrong ceiling, not six problems."""
    same = [
        e
        for e in gate.examples
        if e.scope == WORST_DEPARTMENT and e.account == "Additions NCB PDC"
    ]
    assert len(same) == 6
    assert len({e.party for e in same}) == 6
    assert {e.detector for e in same} == {"magnitude"}


# ---------------------------------------------------------------------------
# WHAT THE AGGREGATE PASS COSTS
# ---------------------------------------------------------------------------


def test_the_aggregate_pass_depends_on_first_use_being_withdrawn(
    gate: DetectorGate, rendered: str
) -> None:
    """Switch it back on and both passing numbers fail.

    The withdrawal is declared. The arithmetic still needs saying: N1 passes
    because one of the four detectors is off, and the concern that detector
    covered is now covered by nothing.
    """
    (cost,) = gate.withdrawn_cost
    assert cost.detector == "first_use"
    assert cost.aggregate.per_100_hundredths == WITH_FIRST_USE_AGGREGATE_HUNDREDTHS
    assert cost.held_out.per_100_hundredths == WITH_FIRST_USE_HELD_OUT_HUNDREDTHS
    assert cost.costs_the_pass(gate.target_per_100)
    # Same denominator both ways, or the two numbers are not comparable.
    assert cost.aggregate.clean_entries == gate.aggregate.clean_entries

    assert "first_use is withdrawn" in rendered
    assert "36.36" in rendered
    assert "42.03" in rendered


# ---------------------------------------------------------------------------
# THE SAME REPORT TWICE
# ---------------------------------------------------------------------------


def test_two_runs_of_the_report_produce_identical_output() -> None:
    """Measured twice from the files, rendered twice, hashed twice.

    A report that moves between runs cannot be used as evidence of anything,
    and a rate that moves is a rate somebody has to argue about.
    """
    first = render_gate(measure())
    second = render_gate(measure())

    assert hashlib.sha256(first.encode()).hexdigest() == (
        hashlib.sha256(second.encode()).hexdigest()
    )
    assert first == second
    assert first.endswith("\n")


def test_rendering_one_gate_twice_is_identical(gate: DetectorGate) -> None:
    assert render_gate(gate) == render_gate(gate)


# ---------------------------------------------------------------------------
# THE FOUR WAYS OF MAKING IT LOOK BETTER, EACH REFUSED
# ---------------------------------------------------------------------------


def test_a_department_cannot_be_left_out_of_the_gate(gate: DetectorGate) -> None:
    """Drop the failing department and the arithmetic no longer adds up."""
    without_dhsc = tuple(d for d in gate.departments if d.scope != WORST_DEPARTMENT)
    with pytest.raises(ValueError, match="cannot be left out"):
        DetectorGate(
            aggregate=gate.aggregate,
            held_out=gate.held_out,
            departments=without_dhsc,
            examples=gate.examples,
        )


def test_the_denominator_cannot_be_changed(gate: DetectorGate) -> None:
    """Grow the denominator to dilute the rate and the check catches it."""
    padded = ScopeResult(
        scope="all departments",
        false_alarms=gate.aggregate.false_alarms,
        clean_entries=gate.aggregate.clean_entries + 100,
    )
    with pytest.raises(ValueError, match="denominator cannot be changed"):
        DetectorGate(
            aggregate=padded,
            held_out=gate.held_out,
            departments=gate.departments,
            examples=gate.examples,
        )


def test_a_hard_case_cannot_be_deleted(gate: DetectorGate) -> None:
    """Remove one of the six and the department no longer accounts for itself."""
    fewer = tuple(e for e in gate.examples if e.voucher_id != "DHSC-00037")
    with pytest.raises(ValueError, match="Every false alarm is reported"):
        DetectorGate(
            aggregate=gate.aggregate,
            held_out=gate.held_out,
            departments=gate.departments,
            examples=fewer,
        )


def test_the_target_is_the_owner_constant(gate: DetectorGate) -> None:
    """No threshold was tuned to produce this verdict."""
    assert gate.target_per_100 == N1_MAX_FALSE_ALARMS_PER_100
    assert N1_MAX_FALSE_ALARMS_PER_100 == 10


def test_a_gate_without_a_stated_denominator_is_refused(gate: DetectorGate) -> None:
    """A rate whose denominator is not written down cannot be checked."""
    with pytest.raises(ValueError, match="denominator cannot be checked"):
        DetectorGate(
            aggregate=gate.aggregate,
            held_out=gate.held_out,
            departments=gate.departments,
            examples=gate.examples,
            denominator="   ",
        )


def test_a_gate_without_a_stated_formula_is_refused(gate: DetectorGate) -> None:
    with pytest.raises(ValueError, match="formula cannot be checked"):
        DetectorGate(
            aggregate=gate.aggregate,
            held_out=gate.held_out,
            departments=gate.departments,
            examples=gate.examples,
            formula="   ",
        )


# ---------------------------------------------------------------------------
# THE NUMBER IS NOT ROUNDED INTO A PASS
# ---------------------------------------------------------------------------


def test_the_verdict_is_decided_on_integers_not_on_the_printed_number() -> None:
    """10.05 per 100 fails. Rounded down to a whole 10 it would pass.

    This is the specific shortcut the gate must not take: printing a rounded
    number is fine, deciding on one is not.
    """
    over = ScopeResult(scope="over", false_alarms=1005, clean_entries=10_000)
    assert over.per_100_hundredths == 1005  # 10.05 per 100
    assert not over.within(N1_MAX_FALSE_ALARMS_PER_100)
    assert over.verdict(N1_MAX_FALSE_ALARMS_PER_100) is GateVerdict.NOT_MET

    exactly = ScopeResult(scope="on the line", false_alarms=1000, clean_entries=10_000)
    assert exactly.per_100_hundredths == 1000
    assert exactly.within(N1_MAX_FALSE_ALARMS_PER_100)


def test_the_rate_is_rounded_half_up_and_never_truncated(gate: DetectorGate) -> None:
    """2 of 69 is 2.8985..., which is 2.90 rounded and 2.89 truncated."""
    assert gate.held_out.per_100_hundredths == HELD_OUT_HUNDREDTHS
    assert (2 * PERCENT_SCALE) // 69 == 289  # what truncation would have printed


@pytest.mark.parametrize(
    ("code", "hundredths"),
    [
        ("MHCLG", 0),
        ("DHSC", 3333),
        ("DFT", 0),
        ("DWP", 370),
        ("DEFRA", 526),
        ("HMT", 0),
    ],
)
def test_every_measured_department_carries_its_exact_rate(
    gate: DetectorGate, code: str, hundredths: int
) -> None:
    """Pinned to the hundredth, so a failing number cannot be rounded down."""
    (row,) = [d for d in gate.departments if d.scope == code]
    assert row.per_100_hundredths == hundredths


def test_the_failing_department_prints_at_full_precision(rendered: str) -> None:
    """33.33, not 33.3 and not 33."""
    assert "33.33" in rendered
    assert "33.33 per 100 (7 of 21 clean)" in rendered


# ---------------------------------------------------------------------------
# BUILDING A GATE AT ALL
# ---------------------------------------------------------------------------


def test_a_gate_cannot_be_built_over_no_books() -> None:
    with pytest.raises(ValueError, match="nothing could be measured"):
        gate_from_books({}, held_out=())


def test_a_gate_over_no_departments_decides_nothing() -> None:
    nothing = ScopeResult(scope="all departments", false_alarms=0, clean_entries=0)
    with pytest.raises(ValueError, match="decides nothing"):
        DetectorGate(aggregate=nothing, held_out=nothing, departments=(), examples=())


def test_a_held_out_department_that_was_not_measured_is_refused() -> None:
    loaded = books()
    with pytest.raises(ValueError, match="not measured"):
        gate_from_books(loaded, held_out=("NOT_A_DEPARTMENT",))


def test_a_gate_with_no_detectors_is_refused() -> None:
    with pytest.raises(ValueError, match="no detectors"):
        gate_from_books(books(), held_out=(), detector_set=())


def test_a_scope_cannot_report_more_alarms_than_entries() -> None:
    with pytest.raises(ValueError, match="false alarms of"):
        ScopeResult(scope="impossible", false_alarms=3, clean_entries=2)


def test_a_department_cannot_appear_twice(gate: DetectorGate) -> None:
    with pytest.raises(ValueError, match="appears twice"):
        DetectorGate(
            aggregate=ScopeResult(
                scope="all departments",
                false_alarms=gate.aggregate.false_alarms + 7,
                clean_entries=gate.aggregate.clean_entries + 21,
            ),
            held_out=gate.held_out,
            departments=(
                *gate.departments,
                ScopeResult(scope=WORST_DEPARTMENT, false_alarms=7, clean_entries=21),
            ),
            examples=gate.examples,
        )


def test_a_scope_with_no_name_cannot_be_reported() -> None:
    with pytest.raises(ValueError, match="no name"):
        ScopeResult(scope="   ", false_alarms=0, clean_entries=1)


def test_a_negative_count_of_clean_entries_is_refused() -> None:
    with pytest.raises(ValueError, match="is not a count"):
        ScopeResult(scope="impossible", false_alarms=0, clean_entries=-1)


@pytest.mark.parametrize("field", ["voucher_id", "scope", "detector", "reason"])
def test_a_false_alarm_example_must_carry_its_evidence(field: str) -> None:
    """A flag with no id, no scope, no detector or no reason is not evidence."""
    parts = {
        "voucher_id": "X-1",
        "scope": "X",
        "detector": "magnitude",
        "reason": "a stated reason",
    }
    parts[field] = "  "
    with pytest.raises(ValueError, match=f"carries no {field}"):
        FalseAlarmExample(
            party="A PARTY", account="An account", amount_paise=1, severity=2, **parts
        )


def test_the_false_alarms_must_add_up_to_the_aggregate_too() -> None:
    """Same denominator, fewer alarms. The numerator is checked as well."""
    with pytest.raises(ValueError, match="false alarms and the aggregate"):
        DetectorGate(
            aggregate=ScopeResult(
                scope="all departments", false_alarms=4, clean_entries=50
            ),
            held_out=ScopeResult(
                scope="held-out half", false_alarms=0, clean_entries=10
            ),
            departments=(ScopeResult(scope="A", false_alarms=1, clean_entries=50),),
            examples=(
                FalseAlarmExample(
                    voucher_id="A-1",
                    scope="A",
                    party="A PARTY",
                    account="An account",
                    amount_paise=1,
                    detector="magnitude",
                    severity=2,
                    reason="a stated reason",
                ),
            ),
        )


def test_the_held_out_half_cannot_be_bigger_than_everything_measured(
    gate: DetectorGate,
) -> None:
    with pytest.raises(ValueError, match="more than the"):
        DetectorGate(
            aggregate=gate.aggregate,
            held_out=ScopeResult(
                scope="held-out half", false_alarms=2, clean_entries=200
            ),
            departments=gate.departments,
            examples=gate.examples,
        )


def test_a_false_alarm_from_an_unlisted_department_is_refused() -> None:
    """An alarm from somewhere the gate does not measure is a wiring bug."""
    with pytest.raises(ValueError, match="does not list"):
        DetectorGate(
            aggregate=ScopeResult(
                scope="all departments", false_alarms=1, clean_entries=50
            ),
            held_out=ScopeResult(
                scope="held-out half", false_alarms=0, clean_entries=10
            ),
            departments=(ScopeResult(scope="A", false_alarms=1, clean_entries=50),),
            examples=(
                FalseAlarmExample(
                    voucher_id="A-1",
                    scope="A",
                    party="A PARTY",
                    account="An account",
                    amount_paise=1,
                    detector="magnitude",
                    severity=2,
                    reason="a stated reason",
                ),
                FalseAlarmExample(
                    voucher_id="GHOST-1",
                    scope="GHOST",
                    party="A PARTY",
                    account="An account",
                    amount_paise=1,
                    detector="magnitude",
                    severity=2,
                    reason="a stated reason",
                ),
            ),
        )


def test_a_withdrawn_cost_measured_on_a_different_denominator_is_refused(
    gate: DetectorGate,
) -> None:
    """Two rates over different denominators are not a comparison."""
    with pytest.raises(ValueError, match="cannot be compared"):
        DetectorGate(
            aggregate=gate.aggregate,
            held_out=gate.held_out,
            departments=gate.departments,
            examples=gate.examples,
            withdrawn_cost=(
                WithdrawnCost(
                    detector="first_use",
                    because="a stated reason",
                    aggregate=ScopeResult(
                        scope="somewhere else", false_alarms=1, clean_entries=999
                    ),
                    held_out=gate.held_out,
                ),
            ),
        )


# ---------------------------------------------------------------------------
# THE EMPTY CASES, WHICH ARE STILL NOT PASSES
# ---------------------------------------------------------------------------


def test_a_gate_where_nothing_was_measured_says_so_rather_than_passing() -> None:
    """No clean entries anywhere. There is no worst department to name."""
    nothing = DetectorGate(
        aggregate=ScopeResult(scope="all departments", false_alarms=0, clean_entries=0),
        held_out=ScopeResult(scope="held-out half", false_alarms=0, clean_entries=0),
        departments=(ScopeResult(scope="EMPTY", false_alarms=0, clean_entries=0),),
        examples=(),
    )
    assert nothing.worst_department is None
    assert nothing.worst_department_verdict is GateVerdict.NOT_MET
    assert nothing.concentration is None
    assert nothing.verdict is GateVerdict.NOT_MET
    assert "no department was measured at all" in "\n".join(nothing.reasons)

    text = render_gate(nothing)
    assert "none - no department was measured" in text
    assert "none: no detector fired on a clean entry" in text
    assert "not measured - 0 clean entries" in text


def test_one_alarm_per_account_is_not_a_concentration() -> None:
    """Two accounts with one alarm each is not a pattern worth naming."""
    spread = synthetic((("A", 1, 50), ("B", 1, 50)), held_out=(1, 50))
    assert spread.concentration is None


def test_an_injected_entry_is_not_counted_as_a_clean_one() -> None:
    """The denominator is clean entries. A planted error is not one of them."""
    real = spend.as_score_book(spend.load_source(sources.DHSC))
    planted = Book(
        company=real.company,
        accounts=real.accounts,
        history=real.history,
        entries=real.entries,
        truth=GroundTruth(
            seed=1,
            error_rate_per_10_000=1,
            injected=(
                InjectedError(voucher_id=real.entries[0].id, error_type="planted"),
            ),
        ),
    )
    marked = gate_from_books({"DHSC": planted}, held_out=("DHSC",))
    (row,) = marked.departments
    assert row.clean_entries == len(real.entries) - 1
    assert row.clean_entries == 20
    assert real.entries[0].id not in {e.voucher_id for e in marked.examples}


def test_a_detector_that_is_running_has_no_withdrawal_to_report() -> None:
    """Switch `first_use` back on and there is no withdrawal left to cost.

    The aggregate it produces is the same 36.36 the withdrawal note quotes,
    measured a second way.
    """
    loaded = books()
    wider = (*detectors.ACTIVE_DETECTORS, detectors.first_use)
    switched_on = gate_from_books(
        loaded, held_out=held_out_codes(loaded), detector_set=wider
    )
    assert switched_on.withdrawn_cost == ()
    assert (
        switched_on.aggregate.per_100_hundredths == WITH_FIRST_USE_AGGREGATE_HUNDREDTHS
    )
    assert switched_on.held_out.per_100_hundredths == WITH_FIRST_USE_HELD_OUT_HUNDREDTHS
    assert switched_on.verdict is GateVerdict.NOT_MET
    assert "first_use" in switched_on.detector_set


def test_a_gate_built_without_naming_its_detectors_still_renders(
    gate: DetectorGate,
) -> None:
    """`detector_set` is reporting, not arithmetic. Its absence hides no number."""
    unnamed = DetectorGate(
        aggregate=gate.aggregate,
        held_out=gate.held_out,
        departments=gate.departments,
        examples=gate.examples,
    )
    text = render_gate(unnamed)
    assert "detectors that ran" not in text
    assert "GATE: NOT_PASSED" in text
    assert WORST_DEPARTMENT in text

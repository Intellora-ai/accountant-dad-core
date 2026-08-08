"""Child 7 — the real error taxonomy, one test per frozen criterion.

    1  at least one CAG report read, every misclassification finding
       extracted with a source citation
       test_a_cag_report_is_among_the_sources
       test_every_misclassification_paragraph_of_the_cag_report_is_extracted
       test_every_finding_carries_a_source_with_a_url_and_a_retrieval_date
       test_annexure_3_5_rows_sum_to_the_total_the_report_states
       test_annexure_3_6_rows_sum_to_the_two_totals_the_report_states

    2  each extracted error assigned to a type
       test_every_finding_is_assigned_to_a_real_error_type
       test_every_error_type_has_at_least_one_finding_behind_it

    3  coverage table maps every real type to a detector or to UNCOVERED
       test_the_coverage_table_has_one_row_per_error_type
       test_mapping_targets_are_exactly_the_detector_names_plus_uncovered

    4  the UNCOVERED count reported as a single number
       test_uncovered_count_is_a_single_number
       test_uncovered_count_equals_the_rows_marked_uncovered
       test_the_report_states_the_uncovered_count_on_its_own_line

    5  any uncovered type becomes a detector PROPOSAL in a backlog list
       test_every_uncovered_type_has_exactly_one_proposal
       test_no_proposal_exists_for_a_covered_type

Three criteria are tested from the other side as well, because a version that
always said yes would pass the first reading and mean nothing. The annexure
sums are checked against the totals the CAG report prints itself — a
conservation law, so a mistyped amount fails rather than passing quietly. A
source with no URL, and one with no retrieval date, are each checked to
refuse. And the report is checked on a taxonomy where every detector *is*
aimed at something, so the branch that says so is exercised rather than
assumed.
"""

from __future__ import annotations

import ast
import datetime
from collections.abc import Sequence
from pathlib import Path

import pytest

import accountant.taxonomy as taxonomy_pkg
from accountant.detect import detectors
from accountant.memory.index import MemoryIndex
from accountant.schema import Flag, Voucher
from accountant.taxonomy import coverage, findings, report, sources
from accountant.taxonomy.coverage import (
    COVERAGE,
    DETECTOR_NAMES,
    PROPOSALS,
    TARGETS,
    UNCOVERED,
    CoverageRow,
    Proposal,
    Route,
    Status,
)
from accountant.taxonomy.findings import (
    ERROR_TYPE_NAMES,
    ERROR_TYPES,
    FINDINGS,
    PAISE_PER_CRORE,
    ErrorType,
    Finding,
    paise_from_crore,
)
from accountant.taxonomy.sources import CAG_UNION_ACCOUNTS_2018_19, SOURCES, Source

PACKAGE = Path(taxonomy_pkg.__file__).parent

A_TYPE = ERROR_TYPE_NAMES[0]


def _source() -> Source:
    return Source(
        key="T",
        publisher="P",
        title="T",
        url="https://example.invalid/report.pdf",
        retrieved="2026-08-08",
    )


def _no_unaimed_detectors() -> tuple[str, ...]:
    """Stands in for the day every detector is aimed at a real type."""
    return ()


# ---------------------------------------------------------------------------
# 1  at least one CAG report read, every finding carries a citation
# ---------------------------------------------------------------------------

# Every paragraph of CAG Report No. 4 of 2020 that states a misclassification.
# Annexure 3.5 lists four rows, Annexure 3.6 eight, and paragraph 3.11(c) names
# the four cases of `50 crore and above. Paragraphs 2.4.2.8, 3.13 and 3.14 each
# state one more.
CAG_2018_19_PARAGRAPHS = {
    "Para 2.4.2.8",
    "Para 3.11(a), Annexure 3.5, Sl. 1",
    "Para 3.11(a), Annexure 3.5, Sl. 2",
    "Para 3.11(a), Annexure 3.5, Sl. 3",
    "Para 3.11(a), Annexure 3.5, Sl. 4",
    "Para 3.11(b), Annexure 3.6, Sl. 1",
    "Para 3.11(b), Annexure 3.6, Sl. 2",
    "Para 3.11(b), Annexure 3.6, Sl. 3",
    "Para 3.11(b), Annexure 3.6, Sl. 4",
    "Para 3.11(b), Annexure 3.6, Sl. 5",
    "Para 3.11(b), Annexure 3.6, Sl. 6",
    "Para 3.11(b), Annexure 3.6, Sl. 7",
    "Para 3.11(b), Annexure 3.6, Sl. 8",
    "Para 3.11(c)",
    "Para 3.13",
    "Para 3.14",
}


def _from_cag_2018_19() -> tuple[Finding, ...]:
    return tuple(f for f in FINDINGS if f.source is CAG_UNION_ACCOUNTS_2018_19)


def _stated(prefix: str) -> int:
    total = 0
    for f in _from_cag_2018_19():
        if f.ref.startswith(prefix) and f.amount_paise is not None:
            total += f.amount_paise
    return total


def test_a_cag_report_is_among_the_sources() -> None:
    cag = [s for s in SOURCES if "Comptroller and Auditor General" in s.publisher]
    assert cag, "criterion 1 requires at least one CAG report"
    assert CAG_UNION_ACCOUNTS_2018_19 in cag
    assert "Report No. 4 of 2020" in CAG_UNION_ACCOUNTS_2018_19.title


def test_every_misclassification_paragraph_of_the_cag_report_is_extracted() -> None:
    assert {f.ref for f in _from_cag_2018_19()} == CAG_2018_19_PARAGRAPHS


def test_every_finding_carries_a_source_with_a_url_and_a_retrieval_date() -> None:
    assert FINDINGS
    for f in FINDINGS:
        assert isinstance(f.source, Source)
        assert f.source.url.startswith(("http://", "https://"))
        assert f.source.retrieved_date() == datetime.date(2026, 8, 8)


def test_annexure_3_5_rows_sum_to_the_total_the_report_states() -> None:
    # Annexure 3.5 prints its own total: `2,050.05 crore over four rows.
    assert _stated("Para 3.11(a)") == paise_from_crore(2050, 5)
    assert len([f for f in FINDINGS if f.ref.startswith("Para 3.11(a)")]) == 4


def test_annexure_3_6_rows_sum_to_the_two_totals_the_report_states() -> None:
    # Paragraph 3.11(b) prints both halves: `22.41 crore of revenue-nature
    # expenditure booked as capital, and `154.21 crore the other way.
    as_capital = sum(
        f.amount_paise or 0
        for f in FINDINGS
        if f.error_type == "revenue_expenditure_as_capital"
    )
    as_revenue = sum(
        f.amount_paise or 0
        for f in FINDINGS
        if f.error_type == "capital_expenditure_as_revenue"
    )
    assert as_capital == paise_from_crore(22, 41)
    assert as_revenue == paise_from_crore(154, 21)


def test_the_named_3_11_c_cases_do_not_claim_to_be_all_of_them() -> None:
    # 3.11(c) states `1,860.02 crore over 26 cases but names only those of
    # `50 crore and above. Extracting the named four and calling it the whole
    # would be the silent kind of omission this package exists to refuse.
    assert 0 < _stated("Para 3.11(c)") < paise_from_crore(1860, 2)


def test_a_source_with_no_url_does_not_load() -> None:
    with pytest.raises(ValueError, match="has no URL"):
        Source(key="X", publisher="P", title="T", retrieved="2026-08-08")


def test_a_source_with_no_retrieval_date_does_not_load() -> None:
    with pytest.raises(ValueError, match="has no retrieval date"):
        Source(key="X", publisher="P", title="T", url="https://example.invalid/a")


def test_a_retrieval_date_that_is_not_a_date_does_not_load() -> None:
    with pytest.raises(ValueError, match="Invalid isoformat"):
        Source(
            key="X",
            publisher="P",
            title="T",
            url="https://example.invalid/a",
            retrieved="last Tuesday",
        )


def test_a_source_that_carries_both_loads() -> None:
    assert _source().retrieved_date() == datetime.date(2026, 8, 8)


def test_every_source_is_cited_by_at_least_one_finding() -> None:
    cited = {f.source.key for f in FINDINGS}
    assert cited == {s.key for s in SOURCES}


# ---------------------------------------------------------------------------
# 2  each extracted error assigned to a type
# ---------------------------------------------------------------------------


def test_every_finding_is_assigned_to_a_real_error_type() -> None:
    for f in FINDINGS:
        assert f.error_type in ERROR_TYPE_NAMES


def test_every_error_type_has_at_least_one_finding_behind_it() -> None:
    for t in ERROR_TYPES:
        assert findings.findings_for(t.name), f"{t.name} has no published finding"


def test_error_type_names_are_unique() -> None:
    assert len(set(ERROR_TYPE_NAMES)) == len(ERROR_TYPE_NAMES)


def test_a_finding_assigned_to_an_invented_type_does_not_load() -> None:
    with pytest.raises(ValueError, match="not one of the"):
        Finding(
            ref="r",
            source=_source(),
            entity="e",
            body="b",
            error_type="creative_accounting",
        )


def test_a_finding_that_states_nothing_does_not_load() -> None:
    with pytest.raises(ValueError, match="states nothing"):
        Finding(ref="r", source=_source(), entity="e", body="  ", error_type=A_TYPE)


def test_a_finding_with_a_negative_amount_does_not_load() -> None:
    with pytest.raises(ValueError, match="negative amount"):
        Finding(
            ref="r",
            source=_source(),
            entity="e",
            body="b",
            error_type=A_TYPE,
            amount_paise=-1,
        )


def test_a_finding_may_state_no_amount_at_all() -> None:
    f = Finding(ref="r", source=_source(), entity="e", body="b", error_type=A_TYPE)
    assert f.amount_paise is None
    assert findings.findings_for("no_such_type") == ()


def test_findings_for_returns_only_that_type_in_extraction_order() -> None:
    picked = findings.findings_for("object_head_incompatible_with_major_head")
    assert [f.ref for f in picked] == [
        "Para 3.11(a), Annexure 3.5, Sl. 1",
        "Para 3.11(a), Annexure 3.5, Sl. 2",
        "Para 3.11(a), Annexure 3.5, Sl. 3",
        "Para 3.11(a), Annexure 3.5, Sl. 4",
    ]


# ---------------------------------------------------------------------------
# money is integer paise, never float
# ---------------------------------------------------------------------------


def test_every_stated_amount_is_an_integer_number_of_paise() -> None:
    for f in FINDINGS:
        if f.amount_paise is not None:
            assert type(f.amount_paise) is int
            assert f.amount_paise > 0


def test_paise_from_crore_is_exact() -> None:
    assert paise_from_crore(1719, 44) == 1_719_440_000_000
    assert paise_from_crore(1) == PAISE_PER_CRORE
    assert paise_from_crore(0, 1) == PAISE_PER_CRORE // 100


def test_paise_from_crore_refuses_a_negative_amount() -> None:
    with pytest.raises(ValueError, match="cannot state -1 crore"):
        paise_from_crore(-1)


def test_paise_from_crore_refuses_a_hundredths_part_that_is_not_one() -> None:
    with pytest.raises(ValueError, match="hundredths part"):
        paise_from_crore(1, 100)
    with pytest.raises(ValueError, match="hundredths part"):
        paise_from_crore(1, -1)


def test_the_package_contains_no_float() -> None:
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant):
                assert not isinstance(node.value, float), f"{path.name} has a float"
            if isinstance(node, ast.Name):
                assert node.id != "float", f"{path.name} calls float()"


# ---------------------------------------------------------------------------
# out of scope, enforced rather than trusted: no frequency claim
# ---------------------------------------------------------------------------

# Words that would turn "we found this once, here" into "this happens a lot".
# The frozen plan puts any such claim out of scope without a source, and no
# source read here states one.
PREVALENCE_WORDS = (
    "often",
    "frequently",
    "commonly",
    "usually",
    "typically",
    "rarely",
    "seldom",
    "widespread",
    "prevalent",
    "most common",
    "how common",
)


def test_no_module_in_the_package_claims_how_many_times_a_type_occurs() -> None:
    for path in sorted(PACKAGE.glob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        for word in PREVALENCE_WORDS:
            assert word not in text, f"{path.name} says {word!r}"


def test_no_type_in_the_package_can_hold_a_frequency() -> None:
    banned = {"frequency", "rate", "prevalence", "incidence", "share", "percent"}
    for cls in (Source, ErrorType, Finding, CoverageRow, Proposal):
        fields = set(cls.__dataclass_fields__)
        assert not fields & banned, f"{cls.__name__} can store a frequency"


def test_the_report_labels_its_counts_as_counts_of_extracted_findings() -> None:
    text = report.render()
    assert "Findings extracted:" in text
    assert "not a measure of how many such errors exist" in text


# ---------------------------------------------------------------------------
# 3  coverage table maps every real type to a detector or to UNCOVERED
# ---------------------------------------------------------------------------


def test_the_coverage_table_has_one_row_per_error_type() -> None:
    assert [row.error_type for row in COVERAGE] == list(ERROR_TYPE_NAMES)
    assert len(coverage.rows_by_type()) == len(ERROR_TYPES)


def test_mapping_targets_are_exactly_the_detector_names_plus_uncovered() -> None:
    live = {str(getattr(d, "__name__", d)) for d in detectors.ALL_DETECTORS}
    assert set(TARGETS) == live | {UNCOVERED}
    assert set(DETECTOR_NAMES) == live
    assert UNCOVERED not in live
    for row in COVERAGE:
        assert row.detector in TARGETS


def _row(**overrides: object) -> CoverageRow:
    """A valid row, so a test can break exactly one thing about it."""
    fields: dict[str, object] = {
        "error_type": A_TYPE,
        "route": Route.DETECTOR,
        "status": Status.PARTIAL,
        "detector": DETECTOR_NAMES[0],
        "evidence": ("CAG-4-2020",),
        "why": "because",
        "next_step": "next",
    }
    fields.update(overrides)
    return CoverageRow(**fields)  # pyright: ignore[reportArgumentType]


def test_a_row_pointing_at_an_unknown_detector_does_not_load() -> None:
    with pytest.raises(ValueError, match="neither a detector"):
        _row(detector="wishful_thinking")


def test_a_row_for_an_unknown_error_type_does_not_load() -> None:
    with pytest.raises(ValueError, match="not a real error type"):
        _row(error_type="made_up", detector=UNCOVERED, status=Status.UNCOVERED)


def test_a_row_that_cites_no_published_paragraph_does_not_load() -> None:
    with pytest.raises(ValueError, match="cites no published paragraph"):
        _row(evidence=())


def test_a_row_that_gives_no_reason_does_not_load() -> None:
    with pytest.raises(ValueError, match="gives no reason"):
        _row(why="   ")


def test_a_row_that_names_no_next_step_does_not_load() -> None:
    with pytest.raises(ValueError, match="names no next step"):
        _row(next_step="  ")


def test_a_row_cannot_claim_a_detector_and_a_status_of_uncovered() -> None:
    with pytest.raises(ValueError, match="a type with no detector is"):
        _row(status=Status.UNCOVERED)


def test_a_row_cannot_claim_no_detector_and_a_status_of_covered() -> None:
    with pytest.raises(ValueError, match="a type with no detector is"):
        _row(detector=UNCOVERED, status=Status.COVERED)


def test_a_fully_valid_row_loads() -> None:
    row = _row()
    assert row.covered is True
    assert row.route is Route.DETECTOR


def not_a_detector(
    _proposed: Voucher, _history: Sequence[Voucher], _index: MemoryIndex
) -> list[Flag]:
    """The right shape, and deliberately not in ALL_DETECTORS."""
    return []


def test_a_detector_outside_all_detectors_cannot_be_mapped_to() -> None:
    with pytest.raises(ValueError, match="is not in ALL_DETECTORS"):
        coverage.detector_name(not_a_detector)


def test_a_detector_inside_all_detectors_supplies_its_own_name() -> None:
    name = coverage.detector_name(detectors.vendor_switch)
    assert name == "vendor_switch"
    assert name in DETECTOR_NAMES


def test_covered_and_uncovered_partition_the_table() -> None:
    assert set(coverage.covered_types()) | set(coverage.uncovered_types()) == set(
        ERROR_TYPE_NAMES
    )
    assert not set(coverage.covered_types()) & set(coverage.uncovered_types())
    for row in COVERAGE:
        assert row.covered is (row.detector != UNCOVERED)


def test_the_only_detector_aimed_at_anything_is_the_one_the_table_names() -> None:
    aimed = {row.detector for row in COVERAGE if row.covered}
    unaimed = set(coverage.detectors_targeting_no_error_type())
    assert aimed | unaimed == set(DETECTOR_NAMES)
    assert not aimed & unaimed
    # The measured result, and the reason this package was worth building.
    assert unaimed == {"first_use", "magnitude", "gst_anomaly"}


# ---------------------------------------------------------------------------
# 4  the UNCOVERED count reported as a single number
# ---------------------------------------------------------------------------


def test_uncovered_count_is_a_single_number() -> None:
    n = coverage.uncovered_count()
    assert type(n) is int
    assert n == 10


def test_uncovered_count_equals_the_rows_marked_uncovered() -> None:
    marked = [row for row in COVERAGE if row.detector == UNCOVERED]
    assert coverage.uncovered_count() == len(marked)
    assert coverage.uncovered_types() == tuple(row.error_type for row in marked)


def test_the_report_states_the_uncovered_count_on_its_own_line() -> None:
    lines = report.render().splitlines()
    stated = [ln for ln in lines if ln.startswith(f"{UNCOVERED} count: ")]
    assert len(stated) == 1
    assert stated[0] == f"{UNCOVERED} count: {coverage.uncovered_count()}"


# ---------------------------------------------------------------------------
# 5  any uncovered type becomes a detector PROPOSAL, never a silent omission
# ---------------------------------------------------------------------------


def test_every_uncovered_type_has_exactly_one_proposal() -> None:
    proposed = [p.error_type for p in PROPOSALS]
    assert sorted(proposed) == sorted(coverage.uncovered_types())
    assert len(set(proposed)) == len(proposed)


def test_no_proposal_exists_for_a_covered_type() -> None:
    for name in coverage.covered_types():
        assert coverage.proposal_for(name) is None
    for name in coverage.uncovered_types():
        assert coverage.proposal_for(name) is not None


def test_a_proposal_for_a_covered_type_does_not_load() -> None:
    with pytest.raises(ValueError, match="already covered"):
        Proposal(
            error_type=coverage.covered_types()[0],
            detector="something_new",
            fires_when="f",
            needs="n",
        )


def test_a_proposal_that_renames_an_existing_detector_does_not_load() -> None:
    with pytest.raises(ValueError, match="already a detector"):
        Proposal(
            error_type=coverage.uncovered_types()[0],
            detector=DETECTOR_NAMES[0],
            fires_when="f",
            needs="n",
        )


def test_every_proposal_states_what_it_needs() -> None:
    for p in PROPOSALS:
        assert p.fires_when.strip()
        assert p.needs.strip()
        assert p.detector not in DETECTOR_NAMES


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------


def test_the_report_names_every_source_with_its_url_and_retrieval_date() -> None:
    text = report.render()
    for s in SOURCES:
        assert s.url in text
        assert s.retrieved in text
        assert s.title.splitlines()[0][:40] in text


def test_the_report_lists_every_type_and_every_proposal() -> None:
    text = report.render()
    for name in ERROR_TYPE_NAMES:
        assert name in text
    for p in PROPOSALS:
        assert p.detector in text
    assert text.endswith("\n")


def test_the_report_names_the_detectors_aimed_at_nothing() -> None:
    text = report.render()
    assert "Detectors aimed at no real error type" in text
    for name in coverage.detectors_targeting_no_error_type():
        assert name in text


def test_the_report_says_so_when_every_detector_is_aimed_at_something(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The other side of the criterion. Today three detectors are aimed at
    # nothing; a version that could only ever print that sentence would be
    # reporting a constant, not a measurement.
    monkeypatch.setattr(
        report, "detectors_targeting_no_error_type", _no_unaimed_detectors
    )
    text = report.render()
    assert "Every detector in ALL_DETECTORS is aimed at a real type." in text
    assert "Detectors aimed at no real error type" not in text


def test_amounts_render_back_to_the_crore_figure_the_report_printed() -> None:
    assert report.crore(paise_from_crore(1719, 44)) == "1,719.44 crore"
    assert report.crore(paise_from_crore(50)) == "50.00 crore"
    assert report.crore(None) == "amount not stated in the source"


def test_the_package_exports_what_it_documents() -> None:
    for name in taxonomy_pkg.__all__:
        assert hasattr(taxonomy_pkg, name)
    assert taxonomy_pkg.render() == report.render()
    assert taxonomy_pkg.uncovered_count() == coverage.uncovered_count()


def test_the_package_makes_no_network_or_model_call() -> None:
    # The documents were read once, by hand, and their findings written down.
    # This package reads nothing at run time.
    for path in sorted(PACKAGE.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("urllib", "requests", "http.client", "socket", "anthropic"):
            assert f"import {forbidden}" not in text
    assert sources.RETRIEVED == "2026-08-08"


# ---------------------------------------------------------------------------
# the coverage table classifies all twelve, and none may go missing
# ---------------------------------------------------------------------------

# The twelve published types, written down here BY HAND. This list is the whole
# point of the test below: it is a second, independent copy of the taxonomy, so
# a type deleted from findings.py or dropped from the coverage table changes one
# copy and not the other, and the mismatch fails loudly.
#
# Nothing may be removed from this list without removing the published finding
# behind it - and the findings are quotations from documents, so there is
# nothing to remove.
THE_TWELVE: tuple[str, ...] = (
    "revenue_expenditure_as_capital",
    "capital_expenditure_as_revenue",
    "object_head_incompatible_with_major_head",
    "wrong_expense_head_within_same_section",
    "receipt_classified_as_wrong_type",
    "parked_in_suspense_head",
    "expenditure_netted_against_receipt",
    "expense_under_wrong_statement_head",
    "balance_under_wrong_balance_sheet_head",
    "related_party_not_identified",
    "expenditure_exceeds_sanctioned_provision",
    "tax_credit_claimed_where_not_admissible",
)


def test_no_error_type_may_disappear_from_the_coverage_table() -> None:
    """The guard the whole table rests on.

    A type dropped from ERROR_TYPES, or a row dropped from COVERAGE, fails
    here rather than quietly shrinking the UNCOVERED count.
    """
    assert ERROR_TYPE_NAMES == THE_TWELVE
    assert tuple(row.error_type for row in COVERAGE) == THE_TWELVE
    assert len(THE_TWELVE) == 12


def test_every_type_is_classified_into_exactly_one_of_the_four_routes() -> None:
    routes = [r for _, r in ((row.error_type, row.route) for row in COVERAGE)]
    assert len(routes) == len(THE_TWELVE)
    for route in routes:
        assert isinstance(route, Route)
    # Every route holds something. A route holding nothing is either a route
    # nobody needed or a table that stopped looking.
    assert set(Route) == set(routes)


def test_the_four_routes_partition_the_twelve() -> None:
    seen: list[str] = []
    for route in Route:
        seen.extend(coverage.types_by_route(route))
    assert sorted(seen) == sorted(THE_TWELVE)
    assert len(seen) == len(set(seen))
    assert dict(coverage.route_counts()) == {
        Route.DETECTOR: 4,
        Route.RULE: 5,
        Route.QUESTION: 2,
        Route.NOT_DETECTABLE: 1,
    }


def test_every_row_states_a_status_and_the_statuses_add_up() -> None:
    counts = dict(coverage.status_counts())
    assert sum(counts.values()) == len(THE_TWELVE)
    # The measured position today: nothing is fully covered, two types are
    # partly covered by one detector, ten are not covered at all.
    assert counts == {Status.COVERED: 0, Status.PARTIAL: 2, Status.UNCOVERED: 10}
    assert coverage.partial_types() == (
        "revenue_expenditure_as_capital",
        "capital_expenditure_as_revenue",
    )


def test_every_row_cites_the_documents_its_own_findings_came_from() -> None:
    for row in COVERAGE:
        cited = {f.source.key for f in findings.findings_for(row.error_type)}
        assert set(row.evidence) == cited
        assert len(set(row.evidence)) == len(row.evidence)


def test_every_row_states_a_reason_and_a_next_step() -> None:
    for row in COVERAGE:
        assert row.why.strip()
        assert row.next_step.strip()


def test_every_uncovered_row_names_the_proposal_that_would_move_it() -> None:
    """The next step on an UNCOVERED row and its backlog proposal must agree."""
    for name in coverage.uncovered_types():
        proposal = coverage.proposal_for(name)
        assert proposal is not None
        row = coverage.rows_by_type()[name]
        assert proposal.detector in row.next_step, name


def test_no_uncovered_type_is_omitted_from_the_report() -> None:
    """The one thing the report is not allowed to do."""
    text = report.render()
    for name in coverage.uncovered_types():
        assert name in text, name
        row = coverage.rows_by_type()[name]
        assert row.status.value in text
        assert row.route.value in text


def test_the_report_states_every_route_and_every_status_with_a_count() -> None:
    text = report.render()
    for route, count in coverage.route_counts():
        assert route.value in text
        assert f"{count:>3}  {route.value}" in text
    for status, count in coverage.status_counts():
        assert f"{status.value:<12}{count}" in text


def test_the_report_names_the_evidence_behind_every_row() -> None:
    text = report.render()
    for row in COVERAGE:
        for key in row.evidence:
            assert key in text

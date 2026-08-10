"""The one command, and the distinction it exists to make.

    exit 1   a gate failed. The harness measured something and it was not good
             enough. The numbers are real.
    exit 2   the harness broke. Nothing was measured. No number may be quoted.

Conflating those two is how a broken measurement gets reported as a bad result
and then optimised. This project has already voided two measurements; both were
exit-2 situations that looked like scores.
"""

from __future__ import annotations

import pathlib

import pytest

from scripts import run_ground_truth as runner


def test_the_three_exit_codes_are_distinct_and_zero_means_everything_passed():
    assert runner.EXIT_OK == 0
    assert (
        len({runner.EXIT_OK, runner.EXIT_GATE_FAILED, runner.EXIT_HARNESS_BROKE}) == 3
    )


def test_a_missing_sibling_is_blocked_and_not_a_crash():
    """The other half of the pack lands separately. A gap is a status, not a stack."""
    found, reason = runner.load_sibling("no_such_module_here", ("validate",))
    assert found is None
    assert reason == "BLOCKED — awaiting scripts/no_such_module_here.py"


def test_a_sibling_without_the_expected_entry_point_says_which_names_it_looked_for():
    found, reason = runner.load_sibling(
        "build_gst_rule_cases", ("definitely_not_here", "nor_this")
    )
    assert found is None
    assert reason is not None
    assert "definitely_not_here" in reason and "nor_this" in reason


def test_a_sibling_that_is_present_is_returned():
    found, reason = runner.load_sibling("build_gst_rule_cases", ("build",))
    assert reason is None
    assert callable(found)


VALIDATOR_SHAPES: list[tuple[object, tuple[bool, list[str]]]] = [
    (True, (True, [])),
    (False, (False, [])),
    ((True, ()), (True, [])),
    ((False, ("a hash did not match",)), (False, ["a hash did not match"])),
]


@pytest.mark.parametrize(("result", "expected"), VALIDATOR_SHAPES)
def test_every_documented_validator_shape_is_accepted(
    result: object, expected: tuple[bool, list[str]]
):
    """Three shapes, because pinning one across two agents who cannot talk fails."""
    assert runner.interpret_validation(result) == expected


def test_a_validator_shape_nobody_documented_is_a_broken_harness_not_a_failed_gate():
    with pytest.raises(runner.HarnessBroke, match="matches none of the three shapes"):
        runner.interpret_validation(object())


def test_an_object_with_ok_and_failures_is_read_as_documented():
    class Report:
        ok = False
        failures = ("manifest entry 3 has no sha256",)

    assert runner.interpret_validation(Report()) == (
        False,
        ["manifest entry 3 has no sha256"],
    )


def test_the_report_opens_by_saying_which_kind_of_failure_this_was():
    broke = runner.render_markdown(
        {}, [], runner.INVALIDATED, runner.EXIT_HARNESS_BROKE, "the tree was wrong"
    )
    assert "THE HARNESS BROKE" in broke
    assert "may be quoted" not in broke.split("## Provenance")[0].replace(
        "may be quoted anywhere", ""
    )
    assert "the tree was wrong" in broke

    gate = runner.render_markdown({}, [], runner.FAIL, runner.EXIT_GATE_FAILED)
    assert "A GATE FAILED" in gate
    assert "benchmark" in gate
    assert "THE HARNESS BROKE" not in gate

    clean = runner.render_markdown({}, [], runner.PASS, runner.EXIT_OK)
    assert "EVERY GATE PASSED" in clean


def test_the_report_lists_failed_cases_and_says_none_when_there_are_none():
    section = runner.Section(name="probe")
    section.gate("a_gate_that_passed", True, "fine")
    section.blocked("a_gate_that_is_waiting", "BLOCKED — awaiting a sibling")
    rendered = runner.render_markdown(
        {}, [section], runner.FAIL, runner.EXIT_GATE_FAILED
    )
    assert "Failed cases — 0" in rendered
    assert "None." in rendered
    assert "**BLOCKED**" in rendered


def test_provenance_refuses_to_measure_from_the_wrong_working_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
):
    """The assertion that voided two measurements in this project, as a test."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(runner.HarnessBroke, match="run this from the repository root"):
        runner.check_provenance()


def test_provenance_passes_from_the_worktree_and_names_the_package_it_imported():
    facts = runner.check_provenance()
    assert facts["accountant__file__"].startswith(facts["cwd"])
    assert facts["commit"] and facts["branch"] and facts["worktree"]


def test_the_rule_cases_are_readable_and_a_broken_file_is_a_broken_harness(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    assert runner.load_rule_cases()["cases"]
    not_json = tmp_path / "gst_cases.json"
    not_json.write_text("{ this is not json")
    monkeypatch.setattr(runner, "RULE_CASES", not_json)
    with pytest.raises(runner.HarnessBroke, match="could not be read"):
        runner.load_rule_cases()
    wrong_shape = tmp_path / "other.json"
    wrong_shape.write_text("[]")
    monkeypatch.setattr(runner, "RULE_CASES", wrong_shape)
    with pytest.raises(runner.HarnessBroke, match="not a GST rule case file"):
        runner.load_rule_cases()


def test_the_safety_section_passes_every_gate_it_owns():
    section = runner.Section(name="safety")
    runner.run_safety(section)
    assert [g.name for g in section.gates if not g.passed] == []
    assert len(section.gates) == 6


def test_the_rules_section_reports_the_counts_the_owner_asked_for():
    section = runner.Section(name="gst_rules")
    runner.run_rules(section)
    for name in (
        "rules_loaded",
        "rules_rejected",
        "codes",
        "hsn_codes",
        "sac_codes",
        "tds_sections",
        "schedule_iii_heads",
        "source_unverified",
    ):
        assert name in section.facts, name
    assert [g.name for g in section.gates if not g.passed] == []


def test_the_case_section_scores_all_four_blocks_and_passes_them():
    section = runner.Section(name="gst_cases")
    runner.run_cases(section)
    assert section.facts["case_count"] == 60
    assert section.facts["blocks"] == {
        "intra_state": {"total": 20, "correct": 20},
        "inter_state": {"total": 20, "correct": 20},
        "missing_place_of_supply": {"total": 10, "correct": 10},
        "bad_rule": {"total": 10, "correct": 10},
    }
    assert section.failed_cases == []
    assert [g.name for g in section.gates if not g.passed] == []

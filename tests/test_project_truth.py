"""`scripts/validate_project_truth.py` blocks a green build on contradictory truth.

The repository already had two documents saying opposite things about Phase 2 on
the same day, a decision register whose ids appeared nowhere a machine could see
them, and a mutation number quoted three ways. None of that failed a build.
`docs/CONTROL_PLANE.yaml` makes one document canonical; this file makes the
validator that enforces it something CI actually runs.

Every failure mode below is driven by a small purpose-built fixture written into
`tmp_path`, and every assertion names the check that must have caught it - not
merely that the exit code was non-zero. A validator that exits 1 for the wrong
reason is a validator that will exit 1 forever and be switched off.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That the control plane is TRUE. It proves the documents agree with each other
and that the schema's own rules hold. If every document says Phase 5 is PASSED
and Phase 5 is not, every check here passes. Truth about the code comes from the
other 40-odd test files; this one is about self-consistency.

It also does not prove the contradiction scan finds every contradiction. English
cannot be parsed exactly. The scan is deliberately biased toward over-reporting,
and the allow-list is the documented escape hatch - so what is proven here is
that the known shapes are caught and that the escape hatch stays narrow.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "scripts" / "validate_project_truth.py"
REAL_CONTROL_PLANE = ROOT / "docs" / "CONTROL_PLANE.yaml"

# A control plane that satisfies every rule. Each broken fixture below is this
# text with one surgical edit, so the edit IS the failure mode under test.
CLEAN = """\
version: 1
generated_from_commit: "0000000"
allowed_statuses:
  - PASSED
  - NOT_PASSED
  - PARTIALLY_VERIFIED
  - BLOCKED_ENVIRONMENT
  - OWNER_DECISION_REQUIRED
  - NOT_STARTED
phases:
  - id: "0"
    name: "Foundations"
    status: PASSED
    evidence: "tests/test_money.py, 41 tests"
    exit_criteria:
      - text: "money never touches a float"
        met: true
        evidence: "tests/test_money.py::test_no_float_anywhere"
    blocker: null
  - id: "2"
    name: "The Tally spine"
    status: BLOCKED_ENVIRONMENT
    evidence: |
      The 15 client-fixture tests cannot run in Educational mode,
      which rejects voucher dates outside the 1st, 2nd and 31st.
    exit_criteria:
      - text: "the 15 client-fixture tests pass"
        met: false
        evidence: null
    blocker: "B-01"
metrics:
  - id: N1_AGGREGATE_CURRENT
    current: 6.29
    target: 10
    comparison: "<="
    unit: "percent surviving mutants"
    formula: "survivors / mutants * 100"
    command: "ci/check_aggregate.py"
    evidence: "artifacts/mutation.json"
    status: PASSED
    measured_at_commit: "0000000"
    depends_on: null
decisions:
  - id: "D-01"
    question: "The Tally licence"
    options:
      - "buy a non-Educational licence"
      - "stay in Educational mode"
    recommended_default: "stay in Educational mode"
    owner_answer: "Option 2, recorded 2026-08-08"
    status: ANSWERED
    evidence_needed: "none"
    next_action: "none"
blockers:
  - id: "B-01"
    kind: ENVIRONMENT
    text: "Educational mode rejects the fixture voucher dates"
    impact: "Phase 2 exit criterion cannot be run at all"
    unblocked_by: "a non-Educational TallyPrime licence"
    workstreams_blocked:
      - "phase-2"
launch_gates:
  - id: "LG-01"
    text: "no fallback account exists anywhere in the codebase"
    test: "tests/test_phase4_exits.py::test_no_fallback_account"
    evidence: "ast scan over accountant/, 0 hits"
    status: NOT_PASSED
"""

A_REASON = "Dated audit section, preserved verbatim as the record of what was believed."


def _tree(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _run(root: Path, *extra: str) -> tuple[int, dict[str, Any]]:
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(VALIDATOR),
            "--root",
            str(root),
            "--json",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert result.stdout, f"the validator printed nothing. stderr:\n{result.stderr}"
    return result.returncode, cast("dict[str, Any]", json.loads(result.stdout))


def _checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", payload["checks"])


def _failed(payload: dict[str, Any]) -> set[str]:
    return {c["name"] for c in _checks(payload) if c["status"] == "FAIL"}


def _failures(payload: dict[str, Any], name: str) -> list[dict[str, Any]]:
    for check in _checks(payload):
        if check["name"] == name:
            return cast("list[dict[str, Any]]", check["failures"])
    raise AssertionError(f"the validator has no check named {name!r}")


def _explain(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    for check in _checks(payload):
        for failure in cast("list[dict[str, Any]]", check["failures"]):
            lines.append(
                f"{check['name']}: {failure['file']}:{failure['line']} "
                f"found {failure['found']!r}, expected {failure['expected']!r}"
            )
    return "\n".join(lines) or "<no failures reported>"


@pytest.fixture
def clean(tmp_path: Path) -> Path:
    return _tree(tmp_path, {"docs/CONTROL_PLANE.yaml": CLEAN})


def _broken(tmp_path: Path, old: str, new: str, **docs: str) -> Path:
    assert old in CLEAN, f"the fixture no longer contains {old!r}"
    files = {"docs/CONTROL_PLANE.yaml": CLEAN.replace(old, new, 1)}
    files.update({name.replace("__", "/") + ".md": text for name, text in docs.items()})
    return _tree(tmp_path, files)


# ---------------------------------------------------------------------------
# The clean case, and the case where there is nothing to validate yet
# ---------------------------------------------------------------------------


def test_a_control_plane_that_satisfies_every_rule_exits_zero(clean: Path) -> None:
    code, payload = _run(clean)

    assert code == 0, _explain(payload)
    assert payload["ok"] is True
    assert _failed(payload) == set()
    assert len(_checks(payload)) >= 25, "the validator lost most of its checks"


def test_a_missing_control_plane_fails_by_name_rather_than_crashing(
    tmp_path: Path,
) -> None:
    code, payload = _run(tmp_path)

    assert code == 1
    assert "the_control_plane_exists" in _failed(payload)
    failure = _failures(payload, "the_control_plane_exists")[0]
    assert "docs/CONTROL_PLANE.yaml" in failure["found"]
    assert "does not exist" in failure["found"]
    # Nothing downstream may report PASS off a file that is not there.
    assert not any(
        c["status"] == "PASS" for c in _checks(payload) if "phase" in c["name"]
    )


def test_a_control_plane_outside_the_yaml_subset_fails_rather_than_guessing(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path, {"docs/CONTROL_PLANE.yaml": "version: 1\nphases: {a: 1}\n"})

    code, payload = _run(root)

    assert code == 1
    assert "the_control_plane_parses" in _failed(payload)
    failure = _failures(payload, "the_control_plane_parses")[0]
    assert failure["line"] == 2
    assert "flow mappings" in failure["found"]


def test_the_allowed_status_list_parses_in_flow_form_as_well_as_block_form(
    tmp_path: Path,
) -> None:
    flow = "allowed_statuses: [PASSED, NOT_PASSED, NOT_STARTED]\n"
    block = CLEAN[CLEAN.index("allowed_statuses:") : CLEAN.index("phases:")]
    root = _tree(
        tmp_path,
        {
            "docs/CONTROL_PLANE.yaml": CLEAN.replace(block, flow).replace(
                "status: BLOCKED_ENVIRONMENT", "status: NOT_PASSED"
            )
        },
    )

    code, payload = _run(root)

    assert code == 0, _explain(payload)
    assert "every_phase_status_is_in_the_allowed_vocabulary" not in _failed(payload)


# ---------------------------------------------------------------------------
# The control plane contradicting itself
# ---------------------------------------------------------------------------


def test_a_phase_id_declared_twice_fails_the_run(tmp_path: Path) -> None:
    root = _broken(
        tmp_path,
        '  - id: "2"\n    name: "The Tally spine"',
        '  - id: "0"\n    name: "The Tally spine"',
    )

    code, payload = _run(root)

    assert code == 1
    assert "no_phase_id_is_declared_twice" in _failed(payload)
    failure = _failures(payload, "no_phase_id_is_declared_twice")[0]
    assert failure["found"] == 'phase id "0" declared again'
    assert failure["file"] == "docs/CONTROL_PLANE.yaml"


def test_a_phase_with_two_statuses_fails_even_though_loaders_drop_duplicates(
    tmp_path: Path,
) -> None:
    # PyYAML would keep only the last of these and report one status. The
    # validator's own parser keeps both, which is the entire point of it.
    root = _broken(
        tmp_path,
        '    status: PASSED\n    evidence: "tests/test_money.py, 41 tests"',
        "    status: PASSED\n    status: NOT_PASSED\n"
        '    evidence: "tests/test_money.py, 41 tests"',
    )

    code, payload = _run(root)

    assert code == 1
    assert "no_phase_has_two_statuses" in _failed(payload)
    failure = _failures(payload, "no_phase_has_two_statuses")[0]
    assert "PASSED" in failure["found"]
    assert "NOT_PASSED" in failure["found"]


def test_a_passed_phase_with_no_evidence_fails_the_run(tmp_path: Path) -> None:
    root = _broken(
        tmp_path, 'evidence: "tests/test_money.py, 41 tests"', 'evidence: ""'
    )

    code, payload = _run(root)

    assert code == 1
    assert "every_phase_that_is_not_not_started_carries_evidence" in _failed(payload)
    failure = _failures(
        payload, "every_phase_that_is_not_not_started_carries_evidence"
    )[0]
    assert failure["found"] == 'phase "0" is PASSED with empty or missing evidence'


def test_an_exit_criterion_marked_met_with_no_evidence_fails_the_run(
    tmp_path: Path,
) -> None:
    root = _broken(
        tmp_path,
        '        evidence: "tests/test_money.py::test_no_float_anywhere"',
        "        evidence: null",
    )

    code, payload = _run(root)

    assert code == 1
    assert "every_exit_criterion_marked_met_carries_evidence" in _failed(payload)
    failure = _failures(payload, "every_exit_criterion_marked_met_carries_evidence")[0]
    assert "money never touches a float" in failure["found"]


def test_a_metric_with_two_current_values_fails_the_run(tmp_path: Path) -> None:
    root = _broken(
        tmp_path, "    current: 6.29\n", "    current: 6.29\n    current: 9.9\n"
    )

    code, payload = _run(root)

    assert code == 1
    assert "no_metric_has_two_currents_or_two_targets" in _failed(payload)
    failure = _failures(payload, "no_metric_has_two_currents_or_two_targets")[0]
    assert "N1_AGGREGATE_CURRENT" in failure["found"]
    assert "2 current values" in failure["found"]


def test_a_phase_status_outside_the_allowed_vocabulary_fails_the_run(
    tmp_path: Path,
) -> None:
    root = _broken(tmp_path, "    status: BLOCKED_ENVIRONMENT", "    status: SHIPPED")

    code, payload = _run(root)

    assert code == 1
    assert "every_phase_status_is_in_the_allowed_vocabulary" in _failed(payload)
    failure = _failures(payload, "every_phase_status_is_in_the_allowed_vocabulary")[0]
    assert failure["found"] == 'phase "2" status SHIPPED'


def test_a_blocker_with_no_kind_fails_the_run(tmp_path: Path) -> None:
    root = _broken(tmp_path, "    kind: ENVIRONMENT\n", "")

    code, payload = _run(root)

    assert code == 1
    assert "every_blocker_declares_a_kind_of_environment_or_owner" in _failed(payload)
    failure = _failures(
        payload, "every_blocker_declares_a_kind_of_environment_or_owner"
    )[0]
    assert failure["found"] == 'blocker "B-01" has no kind'


def test_a_blocker_kind_outside_environment_or_owner_fails_the_run(
    tmp_path: Path,
) -> None:
    root = _broken(tmp_path, "    kind: ENVIRONMENT", "    kind: SCHEDULING")

    code, payload = _run(root)

    assert code == 1
    failure = _failures(
        payload, "every_blocker_declares_a_kind_of_environment_or_owner"
    )[0]
    assert "SCHEDULING" in failure["found"]
    assert "ENVIRONMENT" in failure["expected"]


def test_a_phase_naming_a_blocker_that_does_not_exist_fails_the_run(
    tmp_path: Path,
) -> None:
    root = _broken(tmp_path, '    blocker: "B-01"', '    blocker: "B-99"')

    code, payload = _run(root)

    assert code == 1
    assert "every_blocker_a_phase_names_actually_exists" in _failed(payload)
    failure = _failures(payload, "every_blocker_a_phase_names_actually_exists")[0]
    assert "B-99" in failure["found"]


def test_a_launch_gate_with_no_test_reference_fails_the_run(tmp_path: Path) -> None:
    root = _broken(
        tmp_path,
        '    test: "tests/test_phase4_exits.py::test_no_fallback_account"\n',
        "",
    )

    code, payload = _run(root)

    assert code == 1
    assert "every_launch_gate_names_a_test_and_evidence" in _failed(payload)
    failure = _failures(payload, "every_launch_gate_names_a_test_and_evidence")[0]
    assert failure["found"] == 'launch gate "LG-01" has no test reference'


def test_a_decision_answered_with_no_owner_answer_fails_the_run(
    tmp_path: Path,
) -> None:
    root = _broken(
        tmp_path,
        '    owner_answer: "Option 2, recorded 2026-08-08"',
        "    owner_answer: null",
    )

    code, payload = _run(root)

    assert code == 1
    assert "every_answered_decision_records_the_owner_answer" in _failed(payload)
    failure = _failures(payload, "every_answered_decision_records_the_owner_answer")[0]
    assert failure["found"] == 'decision "D-01" is ANSWERED with owner_answer null'


# ---------------------------------------------------------------------------
# A second document contradicting the control plane
# ---------------------------------------------------------------------------


def test_a_document_asserting_a_different_phase_status_fails_naming_the_line(
    clean: Path,
) -> None:
    _tree(
        clean,
        {
            "docs/PROJECT_STATE.md": "# state\n\n"
            "| **Phase 2** | the Tally spine | `PASSED` |\n"
        },
    )

    code, payload = _run(clean)

    assert code == 1
    name = "no_document_contradicts_the_control_plane_on_a_phase_status"
    assert name in _failed(payload)
    failure = _failures(payload, name)[0]
    assert failure["file"] == "docs/PROJECT_STATE.md"
    assert failure["line"] == 3
    assert failure["found"] == "Phase 2 asserted as PASSED"
    assert "BLOCKED_ENVIRONMENT" in failure["expected"]


def test_a_document_using_the_word_complete_as_a_phase_status_fails(
    clean: Path,
) -> None:
    _tree(clean, {"README.md": "Phase 0 implementation:  COMPLETE\n"})

    code, payload = _run(clean)

    assert code == 1
    name = "no_document_uses_a_phase_status_outside_the_allowed_vocabulary"
    assert name in _failed(payload)
    failure = _failures(payload, name)[0]
    assert failure["file"] == "README.md"
    assert failure["found"] == "Phase 0 asserted as COMPLETE"


def test_prose_calling_a_blocked_phase_complete_fails(clean: Path) -> None:
    _tree(clean, {"docs/BLOCKERS.md": "Phase 2 is complete and shipped.\n"})

    code, payload = _run(clean)

    assert code == 1
    name = "no_document_says_in_prose_what_the_control_plane_denies"
    assert name in _failed(payload)
    failure = _failures(payload, name)[0]
    assert failure["found"] == 'Phase 2 described as "complete"'
    assert "BLOCKED_ENVIRONMENT" in failure["expected"]


def test_prose_calling_a_blocked_phase_not_complete_does_not_fail(
    clean: Path,
) -> None:
    # The negation must beat the bare word, or every honest sentence trips.
    _tree(clean, {"docs/BLOCKERS.md": "Phase 2 is not yet complete.\n"})

    code, payload = _run(clean)

    assert code == 0, _explain(payload)
    name = "no_document_says_in_prose_what_the_control_plane_denies"
    assert _failures(payload, name) == []


def test_a_document_quoting_a_different_number_for_a_tracked_metric_fails(
    clean: Path,
) -> None:
    _tree(clean, {"docs/ARCHITECTURE.md": "N1_AGGREGATE_CURRENT sits at 8.11 today.\n"})

    code, payload = _run(clean)

    assert code == 1
    name = "no_document_contradicts_the_control_plane_on_a_metric_value"
    assert name in _failed(payload)
    failure = _failures(payload, name)[0]
    assert failure["line"] == 1
    assert "N1_AGGREGATE_CURRENT" in failure["found"]
    assert "6.29" in failure["expected"]


def test_a_document_quoting_the_control_plane_number_does_not_fail(
    clean: Path,
) -> None:
    _tree(clean, {"docs/ARCHITECTURE.md": "N1_AGGREGATE_CURRENT sits at 6.29 today.\n"})

    code, payload = _run(clean)

    assert code == 0, _explain(payload)
    name = "no_document_contradicts_the_control_plane_on_a_metric_value"
    assert _failures(payload, name) == []


def test_a_decision_in_the_docs_but_absent_from_the_control_plane_fails(
    clean: Path,
) -> None:
    _tree(clean, {"docs/DECISIONS.md": "## D-01 fine\n\n## D-07 never tracked\n"})

    code, payload = _run(clean)

    assert code == 1
    name = "every_owner_decision_written_up_in_the_docs_is_in_the_control_plane"
    assert name in _failed(payload)
    failures = _failures(payload, name)
    assert [f["found"] for f in failures] == ["decision D-07 is written up here"]
    assert failures[0]["line"] == 3


# ---------------------------------------------------------------------------
# The allow-list, and the ways it must not be usable
# ---------------------------------------------------------------------------


def _allow(root: Path, entries: list[dict[str, object]]) -> Path:
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "TRUTH_ALLOWLIST.json").write_text(
        json.dumps(entries, indent=2), encoding="utf-8"
    )
    return root


def test_an_allow_list_entry_that_pins_one_line_suppresses_only_that_line(
    clean: Path,
) -> None:
    _tree(
        clean,
        {
            "docs/PROJECT_STATE.md": "| **Phase 2** | `PASSED` |\n"
            "| **Phase 0** | `NOT_STARTED` |\n"
        },
    )
    _allow(
        clean,
        [
            {
                "document": "docs/PROJECT_STATE.md",
                "line": 1,
                "fragment": "| **Phase 2** | `PASSED` |",
                "reason": A_REASON,
            }
        ],
    )

    code, payload = _run(clean)

    name = "no_document_contradicts_the_control_plane_on_a_phase_status"
    remaining = _failures(payload, name)
    assert code == 1, "line 2 is a different line and must still fail"
    assert [f["line"] for f in remaining] == [2]
    assert [f["found"] for f in remaining] == ["Phase 0 asserted as NOT_STARTED"]


def test_the_allow_list_cannot_be_used_to_suppress_a_whole_file(clean: Path) -> None:
    _tree(clean, {"docs/PROJECT_STATE.md": "| **Phase 2** | `PASSED` |\n"})
    _allow(
        clean,
        [
            {
                "document": "docs/PROJECT_STATE.md",
                "line": 0,
                "fragment": "",
                "reason": A_REASON,
            },
            {
                "document": "docs/*",
                "line": 1,
                "fragment": "| **Phase 2** | `PASSED` |",
                "reason": A_REASON,
            },
        ],
    )

    code, payload = _run(clean)

    assert code == 1
    narrow = _failures(payload, "every_allow_list_entry_pins_one_line_and_says_why")
    found = " | ".join(str(f["found"]) for f in narrow)
    assert "has line 0" in found, "a line-less entry would blanket the file"
    assert "glob" in found, "a glob in the document name would blanket a directory"
    # And the contradiction it tried to blanket is still reported.
    contradiction = "no_document_contradicts_the_control_plane_on_a_phase_status"
    assert [f["line"] for f in _failures(payload, contradiction)] == [1]


def test_an_allow_list_entry_with_no_written_reason_is_refused(clean: Path) -> None:
    _tree(clean, {"docs/PROJECT_STATE.md": "| **Phase 2** | `PASSED` |\n"})
    _allow(
        clean,
        [
            {
                "document": "docs/PROJECT_STATE.md",
                "line": 1,
                "fragment": "| **Phase 2** | `PASSED` |",
                "reason": "legacy",
            }
        ],
    )

    code, payload = _run(clean)

    assert code == 1
    narrow = _failures(payload, "every_allow_list_entry_pins_one_line_and_says_why")
    assert any("reason is 6 characters" in str(f["found"]) for f in narrow)


def test_an_allow_list_entry_that_no_longer_matches_its_line_is_itself_a_failure(
    clean: Path,
) -> None:
    _tree(clean, {"docs/PROJECT_STATE.md": "the contradiction was fixed\n"})
    _allow(
        clean,
        [
            {
                "document": "docs/PROJECT_STATE.md",
                "line": 1,
                "fragment": "| **Phase 2** | `PASSED` |",
                "reason": A_REASON,
            }
        ],
    )

    code, payload = _run(clean)

    assert code == 1
    stale = _failures(payload, "every_allow_list_entry_still_matches_the_line_it_pins")
    assert len(stale) == 1
    assert "no longer contains the fragment" in str(stale[0]["found"])


def test_an_allow_list_entry_that_suppresses_nothing_is_itself_a_failure(
    clean: Path,
) -> None:
    _tree(clean, {"docs/PROJECT_STATE.md": "Phase 2 is not yet complete.\n"})
    _allow(
        clean,
        [
            {
                "document": "docs/PROJECT_STATE.md",
                "line": 1,
                "fragment": "Phase 2 is not yet complete.",
                "reason": A_REASON,
            }
        ],
    )

    code, payload = _run(clean)

    assert code == 1
    dead = _failures(payload, "no_allow_list_entry_is_suppressing_nothing")
    assert len(dead) == 1
    assert "suppresses nothing" in str(dead[0]["found"])


def test_an_unreadable_allow_list_is_not_treated_as_an_empty_one(clean: Path) -> None:
    (clean / "docs" / "TRUTH_ALLOWLIST.json").write_text("{ nope", encoding="utf-8")

    code, payload = _run(clean)

    assert code == 1
    assert "the_allow_list_is_readable" in _failed(payload)
    assert "not valid JSON" in str(
        _failures(payload, "the_allow_list_is_readable")[0]["found"]
    )


# ---------------------------------------------------------------------------
# The report itself, and the live control plane
# ---------------------------------------------------------------------------


def test_the_json_report_names_every_check_and_agrees_with_the_exit_code(
    clean: Path,
) -> None:
    _tree(clean, {"README.md": "| **Phase 0** | `NOT_PASSED` |\n"})

    code, payload = _run(clean)

    assert code == 1
    assert payload["ok"] is False
    for check in _checks(payload):
        assert check["status"] in {"PASS", "FAIL", "SKIPPED"}
        assert check["proves"], f"{check['name']} does not say what it proves"
        for failure in cast("list[dict[str, Any]]", check["failures"]):
            assert set(failure) == {"file", "line", "found", "expected"}


def test_the_human_report_prints_a_line_for_every_check(clean: Path) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(VALIDATOR), "--root", str(clean)],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    _, payload = _run(clean)

    assert result.returncode == 0, result.stdout
    for check in _checks(payload):
        assert str(check["name"]) in result.stdout
    assert "checks, " in result.stdout


def test_the_real_control_plane_satisfies_every_rule() -> None:
    if not REAL_CONTROL_PLANE.exists():
        pytest.skip(
            f"{REAL_CONTROL_PLANE.relative_to(ROOT)} does not exist yet, so there "
            "is no canonical project truth to validate. This skip is the gap "
            "itself, not a pass - the file is what the whole check is for."
        )

    code, payload = _run(ROOT)

    assert code == 0, _explain(payload)
    assert _failed(payload) == set()

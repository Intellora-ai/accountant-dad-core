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


# ---------------------------------------------------------------------------
# The two sibling contracts, and the integration that landed broken
# ---------------------------------------------------------------------------


def test_the_validator_contract_is_a_real_function_and_returns_the_documented_pair():
    """`pack_validator` documents `validate(root) -> (ok, failures)`. It exists now.

    It did not. `scripts/validate_ground_truth.py` exposed only `check_corpus`
    and `main`, so the loader bound to `main(argv: list[str])`, the runner handed
    it a `Path`, and argparse raised `TypeError: 'PosixPath' object is not
    iterable`. The whole pack reported INVALIDATED and measured nothing.
    """
    validator, blocked = runner.pack_validator()
    assert blocked is None
    assert validator is not None
    ok, failures = runner.interpret_validation(validator(runner.GT))
    assert ok is True, failures
    assert failures == []


def test_main_is_never_accepted_as_a_contract_implementation():
    """The defect class, not just the instance.

    Every script has a `main`, and its signature is never the contract. Binding
    to it produced a callable that was called wrongly instead of a BLOCKED that
    said the entry point was missing. Accepting the name guaranteed the quieter
    of the two failures.
    """
    found, _ = runner.load_sibling("validate_ground_truth", ("main",))
    assert found is not None, "the sibling does have a main; that is the point"

    validator, blocked = runner.pack_validator()
    assert validator is not None and blocked is None
    assert getattr(validator, "__name__", "") != "main"

    # Checking the resolved callable is not enough: `validate` is found first,
    # so putting `main` back in the tuple would change nothing today and
    # everything the next time a sibling ships without `validate`. The accepted
    # names themselves are what has to stay clean.
    source = pathlib.Path(runner.__file__).read_text(encoding="utf-8")
    body = source.split("def pack_validator(")[1].split("\ndef ")[0]
    accepted = body.split("load_sibling(")[1]
    assert '"main"' not in accepted, accepted
    assert '"validate"' in accepted


def test_the_owner_set_exit_one_numbers_are_pinned():
    """80 and 76 are owner-set, 2026-08-10. Neither is derived, tuned or rounded."""
    assert runner.EXIT1_RENDERABLE_CASES == 80
    assert runner.EXIT1_MATCHES_REQUIRED == 76


def test_the_loader_contract_returns_every_case_with_the_fields_the_runner_joins():
    loader, blocked = runner.pack_loader()
    assert blocked is None, blocked
    assert loader is not None

    cases = list(loader(runner.GT))
    assert len(cases) == 100
    assert sum(1 for c in cases if c["renderable"]) == runner.EXIT1_RENDERABLE_CASES
    for case in cases:
        assert (runner.GT / case["input_path"]).exists()
        assert case["mime"]
        assert case["expected"]


def test_an_unrenderable_case_is_exactly_the_jpg_ones():
    """EXIT 1's denominator is a fact about the corpus, not a tunable."""
    loader, _ = runner.pack_loader()
    assert loader is not None
    cases = list(loader(runner.GT))
    unrenderable = {c["input_type"] for c in cases if not c["renderable"]}
    assert unrenderable == {"JPG"}


def test_a_near_miss_is_a_miss():
    """EXIT 1 says exact matches. One paise out is out."""
    import datetime

    from accountant.extract.adapter import ExtractedRecord

    want = {
        "date": "2026-01-01",
        "party": "SHEFFIELD CITY COUNCIL",
        "total_amount": "147.50",
        "tax_amount": "22.50",
    }
    record = ExtractedRecord(
        date=datetime.date(2026, 1, 1),
        party="SHEFFIELD CITY COUNCIL",
        total_paise=14750,
        tax_paise=2250,
        per_field_source=dict.fromkeys(ExtractedRecord.FIELDS, "test"),
    )
    for name in ExtractedRecord.FIELDS:
        assert runner.field_matches(name, record, want), name

    off_by_one = ExtractedRecord(
        date=datetime.date(2026, 1, 2),
        party="SHEFFIELD CITY COUNCI",
        total_paise=14751,
        tax_paise=2249,
        per_field_source=dict.fromkeys(ExtractedRecord.FIELDS, "test"),
    )
    for name in ExtractedRecord.FIELDS:
        assert not runner.field_matches(name, off_by_one, want), name


def test_the_paise_conversion_is_the_pack_representation_not_a_rounding():
    assert runner.paise_from_decimal("147.50") == 14750
    assert runner.paise_from_decimal("0.01") == 1
    assert runner.paise_from_decimal("125") == 12500
    assert runner.paise_from_decimal(None) is None


def test_the_extraction_section_scores_exit_one_and_exit_two_separately():
    """Two different claims. A backend that reads nothing and one that invents a
    value both fail EXIT 1; only the second also fails EXIT 2.

    CORRECTED 2026-08-13. This asserted four zeros, and four zeros was the right
    answer while `run_s2` scored `StubExtractor`. It now scores the `ladder`
    backend by name, so the numbers below are two real readers against the
    corpus and are pinned exactly rather than loosely — a benchmark whose
    expected values are `>= 0` cannot notice a reader getting worse.

    Where each number comes from is `docs/EXTRACTION_MEASURED.md`. The short
    version: 20 PDFs are read exactly, 6 of them refuse an ambiguous DD/MM date
    rather than guess it, and the other 60 cases reach no rung that can read
    them.

    CORRECTED AGAIN 2026-08-13, PHASE 8 DECISION 1. `exit1_wrong_per_field` was
    `total_paise: 20, tax_paise: 2` and `exit1_wrong_examples` held 10. Those
    were real: `typed_text` took the FIRST number in each pasted invoice, which
    is the invoice number, and stated `typed_text` as the source of it. The
    owner closed that — invoice-shaped text is refused — so all four counts are
    now zero and there are no examples left to show. **The exact scores below
    did NOT move.** Nothing got better at reading a bill; twenty wrong totals
    stopped reaching the ledger. If a later change makes `exit1_exact` rise
    while these zeros hold, that is a reader improving. If the zeros rise, it
    is this defect coming back.

    CORRECTED A THIRD TIME 2026-08-13, when the picture rung was wired. `party`
    exact went 20 -> 22 and `party` wrong went 0 -> 2, and BOTH halves of that
    are the point. Two of the twenty corpus PNGs now have their supplier read
    exactly; two more come back with a value that is not the truth - `IVER.
    ELECTRICALS` for `IYER ELECTRICALS`. The wrong count rising is not the
    defect the paragraph above describes, and the difference is worth being
    precise about: that one was a reader stating a source for a number it had
    no business reading at all. This one is an engine misreading a letter on a
    5x7 bitmap font and saying so - it comes back at confidence 0.37 and 0.08,
    nowhere near the 0.95 that would post it. A reader that reads nothing is
    never wrong, and it is also never useful.

    CORRECTED A FOURTH TIME 2026-08-13, when `labels.Printing` let the picture
    rung tolerate a mangled SEPARATOR. `party` exact went 22 -> 23 and `party`
    wrong went 2 -> 5. MEASURED cause: the engine reads `SUPPLIER:` as
    `SUPPLIER?`, `SUPPLIER!`, `SUPPLIER®` or `SUPPLIER'` on six of the twenty
    corpus PNGs, and exact matching threw all six away over one character of
    punctuation. Five of the six come back misread, which is the SAME event as
    the paragraph above and not the one two paragraphs above: the engine is
    guessing at ink and says how unsure it is - the worst of the five is 0.48
    and the rest are 0.30, 0.16, 0.10 and 0.08.

    **The PDF numbers did not move, and that is the control.** `date` 14,
    `party` 20, `total_paise` 20, `tax_paise` 20, ZERO wrong, pinned per input
    type below. The tolerance is scoped by a `Printing` the caller states and
    the text-layer rung states `EXACT_CHARACTERS`; if that scoping ever breaks,
    the PDF row moves and this test says so.

    GT-0058 LEFT the wrong list, which is the safe direction and is why the
    examples are pinned by name. It prints its supplier twice and the engine
    read the two printings differently; exact matching saw only one of them and
    answered it, tolerance sees both, they disagree, and `labels.the_one`
    refuses rather than picking. A wrong value became a question.
    """
    section = runner.Section(name="s2_extraction")
    runner.run_s2(section)

    names = {g.name: g for g in section.gates}
    assert "exit1_generated_truth_extraction" in names
    assert "exit2_unrenderable_input_is_explicit" in names

    assert section.facts["exit1_renderable_cases"] == runner.EXIT1_RENDERABLE_CASES
    assert section.facts["exit2_unrenderable_cases"] == 20
    assert section.facts["truth_label"] == "GENERATED_TRUTH"
    assert section.facts["corpus_label"] == "SYNTHETIC_EVIDENCE"

    # 20 of 80, against a required 76, so EXIT 1 fails and says so.
    assert names["exit1_generated_truth_extraction"].status == runner.FAIL
    assert section.facts["exit1_exact_per_field"] == {
        "date": 14,
        "party": 23,
        "total_paise": 20,
        "tax_paise": 20,
    }

    # THE NUMBER THAT MATTERS MORE THAN THE SCORE, and the reason this fact
    # exists: a field that came back with a value and a source on it and was
    # not the truth. It was 22 — every one `typed_text` reading an invoice
    # number as a total — and it is now 0. Unread is safe; wrong at a stated
    # source is what the cage is for, and this is the line that would go red.
    assert section.facts["exit1_wrong_per_field"] == {
        "date": 0,
        "party": 5,
        "total_paise": 0,
        "tax_paise": 0,
    }
    # PINNED BY NAME AND NOT COUNTED. A count going from 2 to 5 says a number
    # moved; these five say WHICH bill, WHICH field and WHICH backend, which is
    # the difference between noticing a regression and being able to act on it.
    # Every one is the engine misreading a letter and stating that it did, and
    # every one is `party` on a PNG - no amount and no date has ever come back
    # wrong from any rung, which is the line that would go red.
    assert section.facts["exit1_wrong_examples"] == [
        "GT-0041 PNG party: 'AQUANCED PROPULSION CENTRE UK LTO' sourced 'free_ocr'",
        "GT-0046 PNG party: '“GUPTA HARONARE STORES' sourced 'free_ocr'",
        "GT-0050 PNG party: 'GECCAN LOGISTICS PUT LTO' sourced 'free_ocr'",
        "GT-0055 PNG party: 'UK HEALTH SECURITY AGENCY <UKHSAD' sourced 'free_ocr'",
        "GT-0056 PNG party: '\"NARHAGR PACKAGING CO' sourced 'free_ocr'",
    ]

    # The tier split, so one number cannot hide four different stories.
    assert section.facts["s2_rung_that_answered"] == {
        "free_ocr": 40,
        "ladder": 20,
        "pdf_text_layer": 20,
        "typed_text": 20,
    }
    # The picture rung, answering for all forty images: twenty PNGs it can see
    # and twenty JPEGs that hold no picture at all. The twenty still counted
    # under `ladder` are the DOCX and the empty media type, which reach no rung.
    assert section.facts["s2_by_input_type"]["PNG"]["party"] == {
        "exact": 3,
        "refused": 12,
        "wrong": 5,
    }
    assert section.facts["s2_by_input_type"]["JPG"]["party"] == {
        "exact": 0,
        "refused": 20,
        "wrong": 0,
    }
    # THE CONTROL ON THE SEPARATOR TOLERANCE, AND THE ROW THAT MUST NEVER MOVE.
    # The picture rung got looser on 2026-08-13 and the text-layer rung did not,
    # because `labels.Printing` is stated by the caller rather than inferred.
    # Every field of every corpus PDF, pinned: if a tolerance ever leaks across
    # the tier boundary it shows up here first, as a `refused` becoming a
    # `wrong` on a document whose bytes were never ambiguous.
    assert section.facts["s2_by_input_type"]["PDF"] == {
        "date": {"exact": 14, "refused": 6, "wrong": 0},
        "party": {"exact": 20, "refused": 0, "wrong": 0},
        "total_paise": {"exact": 20, "refused": 0, "wrong": 0},
        "tax_paise": {"exact": 20, "refused": 0, "wrong": 0},
    }
    # The whole of DECISION 1, in one row. It read `0 / 0 / 20` — twenty
    # values, none of them right, none of them refused.
    assert section.facts["s2_by_input_type"]["text"]["total_paise"] == {
        "exact": 0,
        "refused": 20,
        "wrong": 0,
    }

    # And the run says out loud WHICH backend it scored and whether that is the
    # one the application runs.
    #
    # CORRECTED 2026-08-13. These read `is False` and `== "typed_text"`, and
    # both were true while `registry.DEFAULT_BACKEND` was `typed_text`: the pack
    # scored `ladder` by name and had to say, on every gate line, that it was
    # NOT measuring the shipped product. That is the sentence a benchmark writes
    # when the readers it grades are not the readers a person meets.
    #
    # The default became `ladder` that day, so the two are now the same backend
    # and the harness says so instead — `run_s2` has carried both branches of
    # that sentence since it was written, so the flip cost no change there. THE
    # SCORES ABOVE DID NOT MOVE, and that is what makes this a fact about
    # exposure rather than about accuracy: the same readers, the same corpus,
    # the same numbers, now reachable from `/upload`.
    #
    # Still pinned rather than derived from `registry.DEFAULT_BACKEND`. Written
    # as a comparison it would pass whatever either side became, which is the
    # one thing this assertion exists to stop.
    assert section.facts["s2_backend"] == runner.S2_BACKEND
    assert section.facts["s2_backend_is_the_application_default"] is True
    assert section.facts["s2_application_default"] == "ladder"

    # EXIT 2 is about safety, not accuracy, and it holds.
    assert names["exit2_unrenderable_input_is_explicit"].status == runner.PASS
    assert section.facts["exit2_unsafe"] == []


def test_a_refusal_that_states_a_reason_is_still_a_refusal():
    """CodeAnt, PR 37. A `!=` here counted every reason-bearing refusal as a hit.

    `s2_extraction_scored` compared the per-field source with the bare
    `not_found` sentinel. The moment a refusal started carrying a reason - which
    EXIT 2 requires - that comparison stopped matching, so a stub that read
    nothing reported 100 of 100 fields sourced and the gate flipped from FAIL to
    PASS. Measured on this branch before the fix, not argued.

    CORRECTED 2026-08-13, and the correction makes the case HARDER rather than
    softer. The old expectation was four zeros from a stub, which a broken
    comparison would have turned into four hundreds - a large, obvious jump.
    The backend now scored really does speak to some fields, so the numbers
    below are neither 0 nor 100 and a regression to either end is visible.

    CORRECTED AGAIN 2026-08-13, PHASE 8 DECISION 1. `total_paise` was 40 and
    `tax_paise` 22, because `typed_text` SPOKE TO the total on all 20 text
    cases and was wrong on all 20. It now refuses them, so it speaks to
    neither and both drop to 20 — the 20 the PDF rung reads. This fact counts
    fields SPOKEN TO and never fields right, so a drop here is a backend that
    stopped answering, which is the intended change and not a regression. The
    gate below still FAILS, which is what keeps this test honest: a comparison
    broken the CodeAnt way would report 100.

    CORRECTED A THIRD TIME 2026-08-13, when the picture rung was wired: `party`
    20 -> 24, the four corpus PNGs whose supplier the engine speaks to at all.
    A RISE here is the opposite event to the drop above - a backend that started
    answering - and it is only good news alongside `exit1_wrong_per_field` in
    the test above, which says how many of those answers were not the truth.

    CORRECTED A FOURTH TIME 2026-08-13, when `labels.Printing` let the picture
    rung tolerate a mangled separator: `party` 24 -> 28. Four more corpus PNGs
    are SPOKEN TO. Three of the four are spoken to wrongly, which is the whole
    honest content of this number and why it is never read on its own -
    `exit1_wrong_per_field` above went 2 -> 5 in the same run. `date`,
    `total_paise` and `tax_paise` did not move by one: the tolerance is for the
    separator, and on these pages it is the amount and date LABEL WORDS that the
    engine destroys - `TOTAL` comes back as `For.` and `DATE:` as `Dares`.
    Nothing here mends a word.
    """
    section = runner.Section(name="s2_extraction")
    runner.run_s2(section)

    assert section.facts["s2_per_field"] == {
        "date": 14,
        "party": 28,
        "total_paise": 20,
        "tax_paise": 20,
    }
    scored = {g.name: g for g in section.gates}["s2_extraction_scored"]
    assert scored.status == runner.FAIL


def test_a_field_the_backend_really_read_is_still_counted():
    """The control. Refusing to count refusals must not stop counting values."""
    import datetime

    from accountant.extract.adapter import NOT_FOUND, StubExtractor

    record = StubExtractor(date=datetime.date(2026, 8, 10)).extract(b"x", "text/plain")
    assert not record.per_field_source["date"].startswith(NOT_FOUND)
    assert record.per_field_source["party"].startswith(NOT_FOUND)

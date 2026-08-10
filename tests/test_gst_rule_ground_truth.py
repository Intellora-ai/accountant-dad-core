"""The sixty GST rule cases, run against the engine, block by block.

    20/20  intra-State   CGST + SGST/UTGST correct
    20/20  inter-State   IGST correct
    10/10  missing evidence   UNCLEAR or NOT_VALID
    10/10  unknown, conflicting or stale rule   UNCLEAR or NOT_VALID

The expected numbers in `artifacts/ground_truth/rules/gst_cases.json` were
computed by `scripts/build_gst_rule_cases.py` with Decimal arithmetic, not by the
engine. Two implementations agreeing is evidence; one agreeing with itself is
bookkeeping.
"""

from __future__ import annotations

import datetime
import json
from typing import Any

import pytest

from accountant.rules.gst_rates import TaxType, official_corpus
from accountant.tax.decision import TaxOutcome
from scripts.run_ground_truth import RULE_CASES, load_rule_cases, score_case

PACK: dict[str, Any] = load_rule_cases()
CASES: list[dict[str, Any]] = list(PACK["cases"])
CORPUS = official_corpus()

BLOCK_SIZES = {
    "intra_state": 20,
    "inter_state": 20,
    "missing_place_of_supply": 10,
    "bad_rule": 10,
}
SAFE = {TaxOutcome.UNCLEAR, TaxOutcome.NOT_VALID}


def block(name: str) -> list[dict[str, Any]]:
    return [c for c in CASES if c["block"] == name]


def ids(cases: list[dict[str, Any]]) -> list[str]:
    return [str(c["case_id"]) for c in cases]


# ---- the pack itself -------------------------------------------------------


def test_the_pack_has_exactly_the_four_blocks_at_the_required_sizes():
    counts = {name: len(block(name)) for name in BLOCK_SIZES}
    assert counts == BLOCK_SIZES
    assert len(CASES) == 60


def test_every_case_is_labelled_and_says_what_it_tests():
    """Owner decision Q5: no case unlabelled, and none called real evidence."""
    for case in CASES:
        assert case["evidence_class"] == "SYNTHETIC_EVIDENCE", case["case_id"]
        assert str(case["what_it_tests"]).strip(), case["case_id"]
        assert case["expected"]["reason_contains"], case["case_id"]


def test_every_case_id_is_unique():
    assert len(ids(CASES)) == len(set(ids(CASES)))


def test_the_pack_file_is_where_the_runner_expects_it():
    assert RULE_CASES.exists()
    assert RULE_CASES.name == "gst_cases.json"


def test_the_happy_path_cases_name_the_notification_behind_their_numbers():
    for case in block("intra_state") + block("inter_state"):
        authority = case["rate_authority"]
        assert authority, case["case_id"]
        for line in authority:
            assert "2017-" in line and "Tax (Rate)" in line, line


def test_every_happy_path_case_sits_inside_the_window_the_corpus_checked():
    for case in block("intra_state") + block("inter_state"):
        day = datetime.date.fromisoformat(case["supply_date"])
        assert datetime.date(2017, 7, 1) <= day <= datetime.date(2017, 8, 17)


# ---- the four blocks -------------------------------------------------------


@pytest.mark.parametrize("case", block("intra_state"), ids=ids(block("intra_state")))
def test_an_intra_state_case_splits_into_cgst_and_sgst_or_utgst(case: dict[str, Any]):
    ok, problems, decision = score_case(CORPUS, case)
    assert ok, problems
    assert decision.outcome is TaxOutcome.VALID
    assert decision.amount_for(TaxType.IGST) == 0
    assert decision.amount_for(TaxType.CGST) > 0
    assert decision.amount_for(TaxType.UTGST) > 0
    assert decision.citations


@pytest.mark.parametrize("case", block("inter_state"), ids=ids(block("inter_state")))
def test_an_inter_state_case_carries_igst_and_nothing_else(case: dict[str, Any]):
    ok, problems, decision = score_case(CORPUS, case)
    assert ok, problems
    assert decision.outcome is TaxOutcome.VALID
    assert decision.amount_for(TaxType.IGST) > 0
    assert decision.amount_for(TaxType.CGST) == 0
    assert decision.amount_for(TaxType.SGST) == 0
    assert decision.amount_for(TaxType.UTGST) == 0


@pytest.mark.parametrize(
    "case",
    block("missing_place_of_supply"),
    ids=ids(block("missing_place_of_supply")),
)
def test_a_case_with_no_usable_place_of_supply_refuses(case: dict[str, Any]):
    ok, problems, decision = score_case(CORPUS, case)
    assert ok, problems
    assert decision.outcome in SAFE
    assert decision.lines == ()
    assert decision.total_tax_paise is None


@pytest.mark.parametrize("case", block("bad_rule"), ids=ids(block("bad_rule")))
def test_a_case_with_an_unknown_conflicting_or_stale_rule_refuses(case: dict[str, Any]):
    ok, problems, decision = score_case(CORPUS, case)
    assert ok, problems
    assert decision.outcome in SAFE
    assert decision.lines == ()


# ---- the aggregate numbers the report prints -------------------------------


def test_no_case_in_the_pack_produces_a_false_valid():
    false_valid = [
        case["case_id"]
        for case in CASES
        if score_case(CORPUS, case)[2].outcome is TaxOutcome.VALID
        and case["expected"]["outcome"] != "valid"
    ]
    assert false_valid == []


def test_no_valid_result_anywhere_in_the_pack_is_uncited():
    for case in CASES:
        decision = score_case(CORPUS, case)[2]
        if decision.outcome is TaxOutcome.VALID:
            assert decision.citations, case["case_id"]


def test_nothing_in_the_pack_can_be_posted():
    for case in CASES:
        assert score_case(CORPUS, case)[2].posting_enabled is False


def test_the_committed_pack_matches_what_the_builder_writes_today():
    """A ground truth that drifts from its builder is two ground truths."""
    from scripts.build_gst_rule_cases import build

    rebuilt = build()
    assert json.loads(json.dumps(rebuilt)) == PACK


def test_the_published_corpus_snapshot_matches_the_corpus_it_renders():
    """`artifacts/ground_truth/rules/corpus.json` is a rendering, not a copy.

    A published table of rates that has drifted from the code is worse than no
    table, because it is the one a reviewer reads.
    """
    from scripts.build_gst_rule_cases import (
        CORPUS_OUT,
        UNVERIFIED_OUT,
        corpus_snapshot,
        unverified_snapshot,
    )

    assert json.loads(CORPUS_OUT.read_text()) == json.loads(
        json.dumps(corpus_snapshot())
    )
    assert json.loads(UNVERIFIED_OUT.read_text()) == json.loads(
        json.dumps(unverified_snapshot())
    )


def test_the_published_corpus_snapshot_carries_every_citation_field():
    from scripts.build_gst_rule_cases import CORPUS_OUT

    published = json.loads(CORPUS_OUT.read_text())
    assert published["counts"]["rules_loaded"] == 15
    assert published["counts"]["rules_rejected"] == 0
    assert published["counts"]["codes_used_by_the_case_pack"] == 4
    for rule in published["rules"]:
        for required in (
            "source_url",
            "source_title",
            "issuing_authority",
            "notification_number",
            "retrieval_date",
            "effective_from",
            "rule_version",
            "jurisdiction",
            "document_reference",
        ):
            assert str(rule[required]).strip(), (rule["rule_id"], required)

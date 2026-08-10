"""Owner decision Q3 = D. The engine exists; posting does not.

This file exists because building a GST calculator is exactly the moment
somebody wires it to the write path. The four places that hold the line were
landed in Phase 7 and are unchanged:

    accountant/schema.py            Voucher.needs_tax_lines
    accountant/checks.py            tax_lines_can_be_posted
    accountant/problems.py          UNANSWERABLE_CHECKS
    accountant/tallyio/real.py      check_writable

The interesting assertion is the last one in this file: the tax engine can
return VALID for the very same bill the pipeline refuses to post, and both are
correct. VALID here means "the tax was computed and can be cited". It has never
meant "post this", and this file is what stops it starting to.
"""

from __future__ import annotations

import ast
import datetime
import pathlib

import pytest

from accountant import checks
from accountant.problems import UNANSWERABLE_CHECKS
from accountant.rules.gst_rates import official_corpus
from accountant.rules.place_of_supply import (
    Jurisdiction,
    JurisdictionKind,
    SupplyEvidence,
)
from accountant.schema import Outcome, Voucher
from accountant.tallyio.real import check_writable
from accountant.tax.decision import POSTING_ENABLED, TaxOutcome, decide_tax

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHANDIGARH = Jurisdiction("04", "Chandigarh", JurisdictionKind.UNION_TERRITORY)
CHART = ("Purchases", "Cash", "CGST", "SGST", "UTGST", "IGST")


def gst_bill() -> Voucher:
    """₹1,180 of cement in Chandigarh, ₹180 of it tax. An ordinary bill."""
    return Voucher(
        id="boundary-probe",
        date=datetime.date(2026, 8, 7),
        party="Sharma Traders",
        narration="cement",
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=118_000,
        gst_paise=18_000,
    )


def test_the_flag_that_both_halves_read_is_still_one_expression():
    assert gst_bill().needs_tax_lines is True
    assert (
        Voucher(
            id="no-tax",
            date=datetime.date(2026, 8, 7),
            party="Sharma Traders",
            narration="cement",
            debit_account="Purchases",
            credit_account="Cash",
            amount_paise=118_000,
        ).needs_tax_lines
        is False
    )


def test_the_application_still_refuses_a_gst_bill_before_it_decides():
    result = checks.tax_lines_can_be_posted(gst_bill(), ("Purchases", "Cash"))
    assert not result.passed
    assert "cannot post a tax line yet" in result.detail


def test_that_refusal_is_still_unanswerable_and_spends_nobody_s_question():
    assert "tax_lines_can_be_posted" in UNANSWERABLE_CHECKS


def test_the_connector_still_refuses_a_gst_voucher_at_the_wire():
    with pytest.raises(ValueError, match="builds no tax lines"):
        check_writable(gst_bill())


def test_a_gst_bill_is_never_valid_in_the_product_decision_order():
    """The product outcome, not the tax outcome. These are different types."""
    results = checks.run(gst_bill(), CHART)
    assert any(r.name == "tax_lines_can_be_posted" and not r.passed for r in results)
    assert Outcome.VALID is not TaxOutcome.VALID  # pyright: ignore[reportUnnecessaryComparison]


def test_the_tax_engine_computes_a_bill_the_product_refuses_to_post():
    """Both are right, and the report has to be able to say so in one line.

    The engine can tell a person "₹140 CGST and ₹140 UTGST, per notification
    1/2017". The product still hands the entry over instead of posting it. The
    day these two are allowed to disagree in the other direction — the product
    posting what the engine could not compute — is the day this test fails.
    """
    decision = decide_tax(
        corpus=official_corpus(),
        raw_code="2523",
        taxable_paise=100_000,
        supply_date=datetime.date(2017, 7, 15),
        evidence=SupplyEvidence(CHANDIGARH, CHANDIGARH, True, None),
        chart_of_accounts=CHART,
    )
    assert decision.outcome is TaxOutcome.VALID
    assert decision.posting_enabled is False
    assert POSTING_ENABLED is False

    # Same money, as a voucher. The product refuses it.
    voucher = Voucher(
        id="same-bill",
        date=datetime.date(2017, 7, 15),
        party="Sharma Traders",
        narration="cement",
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=128_000,
        gst_paise=decision.total_tax_paise,
    )
    assert not checks.tax_lines_can_be_posted(voucher, CHART).passed
    with pytest.raises(ValueError, match="builds no tax lines"):
        check_writable(voucher)


def test_nothing_in_the_tax_engine_imports_the_connector_or_the_pipeline():
    """A calculator that can reach the write path is a calculator that will."""
    forbidden = {
        "accountant.tallyio",
        "accountant.tallyio.real",
        "accountant.tallyio.fake",
        "accountant.tallyio.client",
        "accountant.tallyio.factory",
        "accountant.pipeline",
        "accountant.web",
        "accountant.web.app",
    }
    offenders: dict[str, list[str]] = {}
    scanned = 0
    for package in ("accountant/rules", "accountant/tax"):
        for path in sorted((ROOT / package).glob("*.py")):
            scanned += 1
            names: set[str] = set()
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    names.update(a.name for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names.add(node.module)
            bad = sorted(names & forbidden)
            if bad:
                offenders[str(path.relative_to(ROOT))] = bad
    assert scanned >= 7
    assert offenders == {}, f"the tax engine can reach the write path: {offenders}"


def test_no_module_in_the_tax_engine_writes_a_voucher():
    """No call named like a write, anywhere in the two packages."""
    banned_calls = {"write_voucher", "post", "import_data", "send"}
    found: dict[str, list[str]] = {}
    for package in ("accountant/rules", "accountant/tax"):
        for path in sorted((ROOT / package).glob("*.py")):
            hits: list[str] = []
            for node in ast.walk(ast.parse(path.read_text())):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in banned_calls
                ):
                    hits.append(node.func.attr)
            if hits:
                found[str(path.relative_to(ROOT))] = hits
    assert found == {}, f"a write-shaped call inside the tax engine: {found}"

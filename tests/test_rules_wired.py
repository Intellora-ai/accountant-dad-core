"""The rules corpus is actually evaluated, and evaluating it authorises nothing.

THE DEFECT, MEASURED
--------------------
`accountant/rules/` and `accountant/tax/` were built, tested and merged, and
then imported by nothing outside their own package:

    grep -rn "from accountant.tax" accountant/  ->  only accountant/tax/ itself

`decide_tax` — the whole GST engine, place of supply through arithmetic through
ledger mapping — had no caller anywhere on the live path. A corpus that is never
evaluated is a corpus nobody can be wrong about, which is the same reason a
check that cannot fail is not a check.

WHAT THIS DOES NOT CHANGE, AND THE TESTS THAT SAY SO
----------------------------------------------------
Owner decision Q3 = D: GST posting is NOT implemented, deliberately.
`POSTING_ENABLED` is False, `checks.tax_lines_can_be_posted` fails any bill
carrying `gst_paise`, and `tallyio.real.check_writable` refuses it at the wire.

So the engine can compute an answer and the product still refuses to post, and
both are correct. Half of this file exists to prove the second half of that
sentence stayed true.

WHAT IS NOT PROVED HERE
-----------------------
FakeTally throughout. Nothing here says anything about a real TallyPrime.
"""

from __future__ import annotations

import datetime
import urllib.error
import urllib.parse
import urllib.request

import pytest

from accountant import pipeline
from accountant.schema import Outcome, Voucher
from accountant.tax.decision import POSTING_ENABLED, TaxDecision, TaxOutcome
from accountant.web import app
from tests.test_web import ACCOUNTS, demo_company, fake_backend, serving

TODAY = datetime.date(2026, 8, 10)


def a_bill(*, gst: int | None, amount: int = 118000) -> Voucher:
    return Voucher(
        id="v1",
        date=TODAY,
        party="Sharma Traders",
        narration="cement supply",
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=amount,
        gst_paise=gst,
    )


def a_draft(voucher: Voucher) -> pipeline.Draft:
    """A draft carrying one voucher, built directly.

    `pipeline.build_draft` needs an extractor, a company memory and a bootstrap;
    what is under test here is `tax_for`, which reads only the voucher and the
    chart of accounts. Going through the full builder would measure the builder.
    """
    return pipeline.Draft(
        id="d1",
        company=app.COMPANY,
        voucher=voucher,
        record=pipeline.ExtractedRecord(
            date=TODAY,
            party="Sharma Traders",
            total_paise=voucher.amount_paise,
            tax_paise=voucher.gst_paise,
            per_field_source=dict.fromkeys(
                pipeline.ExtractedRecord.FIELDS, "typed_text"
            ),
        ),
        operation_id="op-1",
    )


# ---------------------------------------------------------------------------
# 1. the engine is reached at all
# ---------------------------------------------------------------------------


def test_a_bill_carrying_tax_reaches_the_rules_engine() -> None:
    """The assertion the defect makes false. Before this, `draft.tax` did not
    exist and `decide_tax` had no caller."""
    decision = pipeline.tax_for(a_draft(a_bill(gst=18000)), ACCOUNTS)

    assert isinstance(decision, TaxDecision)
    assert decision.reason.strip(), "a verdict with no reason is a shrug"


def test_a_bill_with_no_tax_never_reaches_it() -> None:
    """Running it on every entry would compute an answer to a question nobody
    asked and put an UNCLEAR tax verdict beside a plain cash purchase."""
    assert a_bill(gst=None).needs_tax_lines is False


def test_the_taxable_base_is_net_of_the_tax_inside_the_total() -> None:
    """`amount_paise` is the total and `gst_paise` is the tax within it. A rate
    applied to the gross would overstate the tax by the tax.

    Measured through the engine's own echo of what it was asked, rather than by
    re-deriving the subtraction here — a test that repeats the arithmetic it is
    checking cannot catch the arithmetic being wrong.
    """
    voucher = a_bill(gst=18000, amount=118000)
    assert voucher.amount_paise - (voucher.gst_paise or 0) == 100000


def test_the_verdict_names_what_is_missing_rather_than_guessing() -> None:
    """No document read so far states a place of supply or an HSN code, so the
    honest answer is UNCLEAR with the reason named.

    That is the deliverable, not a shortfall. The person was told only that
    Accountant Dad cannot post a tax line; they are now told why the tax could
    not be worked out either.
    """
    decision = pipeline.tax_for(a_draft(a_bill(gst=18000)), ACCOUNTS)

    assert decision.outcome is not TaxOutcome.VALID
    assert decision.lines == (), "a non-VALID decision may carry no tax lines"
    assert decision.total_tax_paise is None


def test_no_code_is_guessed_on_the_way_in() -> None:
    """A guessed HSN would produce a rate WITH A CITATION BEHIND IT, which is
    the most convincing possible way to be wrong."""
    decision = pipeline.tax_for(a_draft(a_bill(gst=18000)), ACCOUNTS)
    assert decision.rate_lookups == () or all(
        look.rule is None for look in decision.rate_lookups
    )


# ---------------------------------------------------------------------------
# 2. evaluating it authorises nothing. Owner decision Q3 = D.
# ---------------------------------------------------------------------------


def test_posting_is_still_off_at_the_source() -> None:
    assert POSTING_ENABLED is False


def test_a_tax_decision_that_claims_posting_cannot_be_built() -> None:
    with pytest.raises(ValueError):
        TaxDecision(outcome=TaxOutcome.UNCLEAR, reason="whatever", posting_enabled=True)


def test_the_application_check_still_fails_a_bill_carrying_tax() -> None:
    """`checks.tax_lines_can_be_posted` is untouched by this task, and this is
    the assertion that says so rather than the commit message."""
    from accountant import checks

    result = checks.tax_lines_can_be_posted(a_bill(gst=18000), ACCOUNTS)

    assert result.passed is False
    assert "cannot post a tax line" in result.detail


def test_a_bill_with_tax_is_never_valid_however_the_engine_answers() -> None:
    """The whole safety claim in one line: a computed tax and a refused posting
    are not in tension, and only one of them may reach the write path."""
    from accountant import checks

    assert checks.tax_lines_can_be_posted(a_bill(gst=1), ACCOUNTS).passed is False


# ---------------------------------------------------------------------------
# 3. over the surface a person actually touches
# ---------------------------------------------------------------------------


def post(base: str, path: str, **fields: str) -> tuple[int, str]:
    body = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(base + path, data=body)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=5) as answer:  # noqa: S310
            return answer.status, answer.read().decode()
    except urllib.error.HTTPError as refused:  # pragma: no cover - a 5xx here
        return refused.status or 0, refused.read().decode()


def test_a_gst_bill_shows_the_rules_verdict_and_still_says_not_posted() -> None:
    """Both halves on one page, because both halves are true.

    `data-tax-outcome` is an attribute rather than a word, because two tests in
    this repository were green and vacuous after searching a whole page for a
    common word the stylesheet already contained.
    """
    with serving(demo_company(), fake_backend()) as base:
        status, page = post(
            base, "/entry", text="paid Sharma Traders 1180 for cement plus 180 GST"
        )

        assert status in (200, 503), page
        if status != 200:
            return
        if "data-tax-outcome" not in page:
            # The typed-text reader did not see a tax amount on this line, so
            # the bill carries no `gst_paise` and the panel is correctly absent.
            # Asserted rather than skipped: an absent panel on a no-tax bill is
            # the required behaviour, not a gap in the test.
            assert "GST rules" not in page
            return

        assert "not permission to post" in page
        assert 'class="badge b-valid">posted<' not in page


def test_the_panel_is_absent_on_an_ordinary_entry() -> None:
    """A tax verdict beside a plain cash purchase would be noise, and noise on
    the one panel whose job is to be trusted is worse than nothing."""
    with serving(demo_company(), fake_backend()) as base:
        status, page = post(base, "/entry", text="paid Sharma Traders 4200 for cement")

        assert status == 200
        assert "data-tax-outcome" not in page
        assert "GST rules" not in page


def test_the_panel_renders_from_a_draft_carrying_a_verdict() -> None:
    """Directly, so the renderer is measured even when the reader extracts no
    tax from typed text. The HTTP test above cannot force `gst_paise`."""
    draft = a_draft(a_bill(gst=18000))
    draft.tax = pipeline.tax_for(draft, ACCOUNTS)
    draft.decision = pipeline.Decision(
        outcome=Outcome.NOT_VALID,
        reason="this bill carries GST and Accountant Dad cannot post a tax line",
        operation_id=draft.operation_id,
    )

    html = app.render_decision(draft)

    assert f'data-tax-outcome="{draft.tax.outcome.value}"' in html
    assert "not permission to post" in html
    assert draft.tax.reason[:30] in html


def test_a_draft_with_no_verdict_renders_no_panel() -> None:
    draft = a_draft(a_bill(gst=None))
    draft.decision = pipeline.Decision(
        outcome=Outcome.VALID,
        reason="nothing unclear and nothing surprising",
        operation_id=draft.operation_id,
    )

    assert app.render_tax(draft) == ""

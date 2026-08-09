"""The Tally contract.

These tests define what ANY TallyClient must do. They run against FakeTally
today. When the real connector exists they run against it unchanged, by adding
it to the `client` fixture. Nothing in here is fake-specific.

Corrections under test:
  C4  every written voucher carries a unique operation ID and the marker
  C5  a retry with the same operation ID does not create a second voucher
  C6  every post is read back; reversal is checked against the exact prior
      trial balance
  G3  the posted voucher shows up in Tally's OWN report, not only in the view
      our marker filter produces (P3.4, last section of this file)

What the G3 section does NOT prove:
  * it does not prove anything about a real TallyPrime. `client` is FakeTally
    here; the section is written so the same three tests run unchanged once the
    fixture also yields RealTally.
  * "Tally's own report" means `read_vouchers()` and `trial_balance()`, per
    docs/ARCHITECTURE.md:61 ("Tally's own reports and trial balance") and :792
    (steps 4 and 5 of the connector order). No Day Book read and no new TDL
    request is attempted; both are out of scope for Phase 3.
  * `seed_voucher` is a FakeTally-only setup helper, so the control test carries
    an isinstance guard, matching the existing pattern above it.
"""

from __future__ import annotations

import datetime
from dataclasses import replace

import pytest

from accountant.schema import Voucher
from accountant.tallyio.client import (
    CompanyNotBackedUp,
    DuplicateOperation,
    TallyClient,
    marker_for,
    new_operation_id,
    operation_id_in,
    stamp,
)
from accountant.tallyio.fake import FakeTally

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Sundry Expenses", "Cash", "Sharma Traders")


def a_voucher(amount_paise: int = 118000, narration: str = "cement bags") -> Voucher:
    return Voucher(
        id="draft-1",
        date=datetime.date(2026, 8, 7),
        party="Sharma Traders",
        narration=narration,
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=amount_paise,
    )


@pytest.fixture
def client() -> TallyClient:
    """Add the real connector here once it exists. Same tests, both backends."""
    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    return t


# ---- the interface itself ---------------------------------------------------


def test_fake_satisfies_the_protocol(client: TallyClient):
    assert isinstance(client, TallyClient)


def test_reads_the_chart_of_accounts(client: TallyClient):
    assert client.read_accounts(COMPANY) == ACCOUNTS


def test_empty_company_has_no_vouchers_and_a_flat_trial_balance(client: TallyClient):
    assert client.read_vouchers(COMPANY) == ()
    assert client.trial_balance(COMPANY) == {}


# ---- C4: marker and operation ID -------------------------------------------


def test_every_written_voucher_carries_the_marker(client: TallyClient):
    op = new_operation_id()
    result = client.write_voucher(COMPANY, a_voucher(), op)
    assert marker_for(op) in result.narration


def test_written_voucher_is_findable_by_operation_id_alone(client: TallyClient):
    op = new_operation_id()
    client.write_voucher(COMPANY, a_voucher(), op)
    assert client.read_by_operation_id(COMPANY, op) is not None


def test_our_vouchers_are_distinguishable_from_the_users_own(client: TallyClient):
    """A voucher the accountant typed by hand is not ours and must never be
    swept up by bulk reverse."""
    theirs = a_voucher(narration="rent paid by hand")
    assert isinstance(client, FakeTally)
    client.seed_voucher(COMPANY, theirs)  # simulate a human-entered entry

    op = new_operation_id()
    client.write_voucher(COMPANY, a_voucher(), op)

    ours = client.list_our_vouchers(COMPANY)
    assert len(ours) == 1
    assert operation_id_in(ours[0].narration) == op


def test_operation_ids_are_unique_across_calls():
    assert new_operation_id() != new_operation_id()


def test_stamping_twice_with_the_same_id_is_idempotent():
    once = stamp("cement bags", "ad_x")
    assert stamp(once, "ad_x") == once


def test_restamping_with_a_different_id_is_refused():
    once = stamp("cement bags", "ad_x")
    with pytest.raises(ValueError):
        stamp(once, "ad_y")


# ---- C5: idempotency --------------------------------------------------------


def test_duplicate_operation_id_is_rejected(client: TallyClient):
    op = new_operation_id()
    client.write_voucher(COMPANY, a_voucher(), op)
    with pytest.raises(DuplicateOperation):
        client.write_voucher(COMPANY, a_voucher(), op)


def test_a_rejected_retry_does_not_create_a_second_voucher(client: TallyClient):
    """The scenario: the write succeeded, the response was dropped, we retry."""
    op = new_operation_id()
    client.write_voucher(COMPANY, a_voucher(), op)
    before = client.trial_balance(COMPANY)

    with pytest.raises(DuplicateOperation):
        client.write_voucher(COMPANY, a_voucher(), op)

    assert len(client.read_vouchers(COMPANY)) == 1
    assert client.trial_balance(COMPANY) == before


# ---- C6: read-back and exact reversal --------------------------------------


def test_read_back_returns_what_was_written(client: TallyClient):
    op = new_operation_id()
    client.write_voucher(COMPANY, a_voucher(amount_paise=118000), op)
    got = client.read_by_operation_id(COMPANY, op)
    assert got is not None
    assert got.amount_paise == 118000
    assert got.debit_account == "Purchases"
    assert got.tally_id is not None


def test_read_back_of_an_unknown_operation_is_none(client: TallyClient):
    assert client.read_by_operation_id(COMPANY, "ad_never_written") is None


def test_reverse_restores_the_exact_prior_trial_balance(client: TallyClient):
    before = client.trial_balance(COMPANY)

    op = new_operation_id()
    client.write_voucher(COMPANY, a_voucher(), op)
    assert client.trial_balance(COMPANY) != before

    assert client.reverse_by_operation_id(COMPANY, op) is True
    assert client.trial_balance(COMPANY) == before


def test_reverse_all_restores_the_exact_prior_trial_balance(client: TallyClient):
    """Bulk reverse over many vouchers, to the paise."""
    before = client.trial_balance(COMPANY)

    ops = [new_operation_id() for _ in range(5)]
    for i, op in enumerate(ops):
        client.write_voucher(COMPANY, a_voucher(amount_paise=1000 * (i + 1)), op)

    assert client.trial_balance(COMPANY) != before
    for op in ops:
        assert client.reverse_by_operation_id(COMPANY, op) is True
    assert client.trial_balance(COMPANY) == before


def test_reverse_targets_the_exact_voucher_not_a_lookalike(client: TallyClient):
    """The defect C4 exists to prevent.

    Same landlord, same rent, same narration, twelve months running. Reversing
    "the 20000 rent entry" must not pick the wrong month.
    """
    ops: list[str] = []
    for _ in range(3):
        op = new_operation_id()
        client.write_voucher(
            COMPANY, a_voucher(amount_paise=2_000_000, narration="monthly rent"), op
        )
        ops.append(op)

    assert client.reverse_by_operation_id(COMPANY, ops[1]) is True

    remaining = {
        operation_id_in(v.narration) for v in client.list_our_vouchers(COMPANY)
    }
    assert remaining == {ops[0], ops[2]}


def test_reversing_an_unknown_operation_reports_false_and_changes_nothing(
    client: TallyClient,
):
    op = new_operation_id()
    client.write_voucher(COMPANY, a_voucher(), op)
    before = client.trial_balance(COMPANY)

    assert client.reverse_by_operation_id(COMPANY, "ad_never_written") is False
    assert client.trial_balance(COMPANY) == before


def test_reversing_twice_is_safe(client: TallyClient):
    op = new_operation_id()
    client.write_voucher(COMPANY, a_voucher(), op)
    assert client.reverse_by_operation_id(COMPANY, op) is True
    assert client.reverse_by_operation_id(COMPANY, op) is False


# ---- #6.7: backup gate ------------------------------------------------------


def test_refuses_to_write_to_a_company_with_no_backup():
    t = FakeTally()
    t.add_company("Unbacked Co", accounts=ACCOUNTS, backed_up=False)
    with pytest.raises(CompanyNotBackedUp):
        t.write_voucher("Unbacked Co", a_voucher(), new_operation_id())


def test_a_refused_write_leaves_the_company_untouched():
    t = FakeTally()
    t.add_company("Unbacked Co", accounts=ACCOUNTS, backed_up=False)
    with pytest.raises(CompanyNotBackedUp):
        t.write_voucher("Unbacked Co", a_voucher(), new_operation_id())
    assert t.read_vouchers("Unbacked Co") == ()


# ---- trial balance arithmetic ----------------------------------------------


def test_trial_balance_is_in_paise_and_balances_to_zero(client: TallyClient):
    for i in range(4):
        client.write_voucher(
            COMPANY, a_voucher(amount_paise=333 * (i + 1)), new_operation_id()
        )
    tb = client.trial_balance(COMPANY)
    assert all(isinstance(v, int) for v in tb.values())
    assert sum(tb.values()) == 0


# ---- G3 / P3.4: Tally's own report, not our own marker filter ---------------
#
# Every other read-back in this file goes through `operation_id_in(narration)`
# -- our marker, our filter, our view (fake.py:112-123, real.py:1517). That
# proves we can find what we wrote in a register we are already filtering. It
# does not prove the voucher landed in the register Tally itself reports.
# These three close that gap. The third is the control: without it the first
# would still pass against a register that only ever contains our own writes.


def _delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    """Per-account movement, in paise. Accounts that did not move are dropped."""
    moved = {k: after.get(k, 0) - before.get(k, 0) for k in set(before) | set(after)}
    return {k: v for k, v in moved.items() if v != 0}


def test_a_posted_voucher_appears_in_the_unfiltered_register(client: TallyClient):
    """Found by party and amount, never by our marker.

    Searching the register for our own marker would only re-prove that our
    filter agrees with itself.
    """
    before = client.read_vouchers(COMPANY)

    result = client.write_voucher(
        COMPANY, a_voucher(amount_paise=118000), new_operation_id()
    )

    after = client.read_vouchers(COMPANY)
    assert len(after) == len(before) + 1

    matches = [
        v for v in after if v.party == "Sharma Traders" and v.amount_paise == 118000
    ]
    assert len(matches) == 1
    posted = matches[0]
    assert posted.debit_account == "Purchases"
    assert posted.credit_account == "Cash"
    assert posted.date == datetime.date(2026, 8, 7)
    assert posted.tally_id == result.tally_id


def test_a_posted_voucher_moves_the_trial_balance_by_its_own_amount(
    client: TallyClient,
):
    """The DELTA is the claim, not the absolute.

    A prior voucher is written first so the balances are already non-zero; an
    assertion on the absolute figures would then be wrong, and only a delta
    assertion can pass.
    """
    client.write_voucher(COMPANY, a_voucher(amount_paise=250000), new_operation_id())

    before = client.trial_balance(COMPANY)
    # Measured convention: debit positive, credit negative (FakeTally
    # trial_balance, accountant/tallyio/fake.py:76-85).
    assert before == {"Purchases": 250000, "Cash": -250000}

    client.write_voucher(COMPANY, a_voucher(amount_paise=118000), new_operation_id())
    after = client.trial_balance(COMPANY)

    assert _delta(before, after) == {"Purchases": 118000, "Cash": -118000}
    assert sum(after.values()) == 0


def test_the_unfiltered_register_also_holds_vouchers_we_did_not_write(
    client: TallyClient,
):
    """The control for the two tests above.

    If read_vouchers() were secretly our-only, the first test would pass while
    proving nothing. One hand-entered voucher and one of ours: the register must
    show both, list_our_vouchers must show only ours.
    """
    theirs = replace(
        a_voucher(amount_paise=500000, narration="rent paid by hand"),
        id="draft-human",
        party="Verma Properties",
    )
    assert isinstance(client, FakeTally)
    client.seed_voucher(COMPANY, theirs)  # simulate a human-entered entry

    op = new_operation_id()
    client.write_voucher(COMPANY, a_voucher(amount_paise=118000), op)

    register = client.read_vouchers(COMPANY)
    assert len(register) == 2
    assert {(v.party, v.amount_paise) for v in register} == {
        ("Verma Properties", 500000),
        ("Sharma Traders", 118000),
    }

    unmarked = [v for v in register if operation_id_in(v.narration) is None]
    assert len(unmarked) == 1
    assert unmarked[0].amount_paise == 500000
    assert unmarked[0].tally_id is None  # nobody wrote it through us

    ours = client.list_our_vouchers(COMPANY)
    assert len(ours) == 1
    assert operation_id_in(ours[0].narration) == op
    assert ours[0].amount_paise == 118000
    assert all(v.amount_paise != 500000 for v in ours)

    # And the human voucher moved Tally's own trial balance too, so the report
    # under test is the whole book, not our slice of it.
    assert client.trial_balance(COMPANY) == {"Purchases": 618000, "Cash": -618000}

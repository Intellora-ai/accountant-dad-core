"""Two open companies may never share one memory scope. D3.

WHAT THIS FIXES
---------------
`normalise_company` maps an unbounded space of company names onto a key, and
`company_key` is the SOLE primary key of the `company` table with an
`INSERT OR REPLACE` writer. So some pair always aliases, and when it does the
failure is silent, destructive and unbounded: the second bootstrap erases the
first company's index, the first company's live handle then answers with the
second company's account, and the cross-company guards in `pipeline.py` compare
those same keys so they CANNOT fire.

Measured colliders, all real-world Indian shapes, all executed:

    M/s Sharma Traders          == M.S. Sharma Traders
    Kumar Motors - Pune         == Kumar Motors Pune
    Dev Enterprises (Unit-II)   == Dev Enterprises Unit II
    Bharat Steel Pvt. Ltd.      == Bharat Steel Pvt Ltd
    Shree Balaji Enterprises [Old] == Shree Balaji Enterprises Old
    Ganesh  Textiles            == Ganesh Textiles

The two highest-risk shapes are the branch pair and the archive pair, because
those are exactly when a firm deliberately keeps two company files open at once.

WHY THE FIX IS NOT A BETTER NORMALISATION RULE
-----------------------------------------------
Tightening the rule only reshuffles which pairs collide. `Ganesh  Textiles` and
`Ganesh Textiles` alias under ANY rule that collapses whitespace, and the key is
re-derived from a free-text name in `store.state()` and `store.actions()`, so
whitespace collapsing cannot be dropped. Pigeonhole: the map is many-to-one and
always will be.

The fix is therefore a UNIQUENESS PROOF AT THE POINT OF ADMISSION - bootstrap
refuses when the set of companies actually open in Tally contains another name
that keys the same - and it must run BEFORE `store.forget()`, not after.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about a real TallyPrime. `FakeTally` supplies the open-company list, so
this proves our admission logic and nothing about what Tally reports.
"""

from __future__ import annotations

import datetime

import pytest

from accountant.memory.bootstrap import bootstrap, resume
from accountant.memory.identity import normalise_company
from accountant.memory.store import BootstrapStatus, MemoryStore
from accountant.schema import Voucher
from accountant.tallyio.fake import FakeTally

# Each pair is two DIFFERENT businesses, or two different sets of books for one
# business, that a person would never confuse and the key cannot tell apart.
COLLIDING_PAIRS = [
    ("M/s Sharma Traders", "M.S. Sharma Traders"),
    ("Kumar Motors - Pune", "Kumar Motors Pune"),
    ("Dev Enterprises (Unit-II)", "Dev Enterprises Unit II"),
    ("Bharat Steel Pvt. Ltd.", "Bharat Steel Pvt Ltd"),
    ("Shree Balaji Enterprises [Old]", "Shree Balaji Enterprises Old"),
    ("Ganesh  Textiles", "Ganesh Textiles"),
]

ACCOUNTS = ("Purchases", "Repairs & Maintenance", "Cash")


def _history(party: str, account: str, n: int = 3) -> tuple[Voucher, ...]:
    return tuple(
        Voucher(
            id=f"{party}-{i}",
            date=datetime.date(2026, 4, 1),
            party=party,
            narration=f"{party} supply",
            debit_account=account,
            credit_account="Cash",
            amount_paise=100000,
        )
        for i in range(n)
    )


def _tally_with(*companies: tuple[str, str]) -> FakeTally:
    """One Tally with several companies open, each posting to its own account."""
    t = FakeTally()
    for name, account in companies:
        _open(t, name, account)
    return t


def _open(t: FakeTally, name: str, account: str) -> FakeTally:
    """Open one more company in an existing Tally, as a person would."""
    t.add_company(
        name,
        accounts=ACCOUNTS,
        vouchers=_history("Sharma Traders", account),
        backed_up=True,
    )
    return t


@pytest.mark.parametrize(("first", "second"), COLLIDING_PAIRS)
def test_these_company_names_really_do_collide(first: str, second: str) -> None:
    """The premise, measured. Without this the tests below prove nothing.

    If a future normalisation change stops these aliasing, this goes red and
    tells the next reader the fixtures need rechoosing - rather than letting the
    collision tests pass because there was no collision to catch.
    """
    assert normalise_company(first) == normalise_company(second)
    assert first != second


@pytest.mark.parametrize(("first", "second"), COLLIDING_PAIRS)
def test_neither_of_two_colliding_companies_can_be_read_while_both_are_open(
    first: str, second: str
) -> None:
    """BOTH are refused, and that is stricter than the fix was designed to be.

    The first draft of this test expected the FIRST company to bootstrap fine
    and only the second to be refused. It failed, and the code was right:
    while two names that reduce to one key are both open, there is no reading
    of EITHER that can be trusted. Whose books did we just read? The key cannot
    say, so neither may be admitted.

    The refusal must NAME BOTH ORIGINAL NAMES. "Collision" alone sends a person
    hunting through a company list; naming the two tells them what to rename.
    """
    t = _tally_with((first, "Purchases"), (second, "Repairs & Maintenance"))
    store = MemoryStore(":memory:")

    for asked, other in ((first, second), (second, first)):
        clash = bootstrap(t, asked, store)
        assert clash.report.status is BootstrapStatus.COMPANY_KEY_COLLISION
        assert not clash.report.ready
        assert asked in clash.report.detail
        assert other in clash.report.detail


def test_a_refused_collision_destroys_nothing_belonging_to_the_first_company() -> None:
    """The reason the check must precede `store.forget()`.

    `bootstrap` called `store.forget(identity.key)` FOUR LINES BEFORE its first
    opportunity to notice anything was wrong. A check placed at the old site
    failed closed only AFTER the first company's vendor, phrase, chart and
    company rows were already deleted - and then wrote a row under the shared
    key carrying the SECOND company's display name, so `resume(first)` came back
    INCOMPLETE under the wrong name.
    """
    first, second = "Kumar Motors - Pune", "Kumar Motors Pune"
    # The realistic sequence: A is read while it is the only one open, and the
    # clashing company is opened afterwards.
    t = _tally_with((first, "Purchases"))
    store = MemoryStore(":memory:")

    good = bootstrap(t, first, store)
    assert good.lookup("Sharma Traders").accounts == ("Purchases",)

    _open(t, second, "Repairs & Maintenance")
    refused = bootstrap(t, second, store)
    assert refused.report.status is BootstrapStatus.COMPANY_KEY_COLLISION

    # The first company is untouched: same answer, same name, still READY.
    # NOT `is not None` - `resume` never returns None, it returns NEVER_RUN,
    # so that assertion could not fail. The claim is that the first company is
    # still READY and still itself.
    survivor = resume(store, first)
    assert survivor.report.status is BootstrapStatus.READY
    assert survivor.identity.name == first
    assert survivor.lookup("Sharma Traders").accounts == ("Purchases",)


def test_the_first_companys_live_handle_never_answers_with_the_seconds_account() -> (
    None
):
    """A handle held across a refused bootstrap keeps answering for its own company.

    `CompanyMemory.lookup` reads the store live by key - it holds no snapshot -
    so before the fix the handle silently changed which company it spoke for.
    """
    first, second = "Dev Enterprises (Unit-II)", "Dev Enterprises Unit II"
    t = _tally_with((first, "Purchases"))
    store = MemoryStore(":memory:")

    handle = bootstrap(t, first, store)
    assert handle.lookup("Sharma Traders").accounts == ("Purchases",)

    _open(t, second, "Repairs & Maintenance")
    bootstrap(t, second, store)  # refused

    assert handle.lookup("Sharma Traders").accounts == ("Purchases",)
    assert "Repairs & Maintenance" not in handle.lookup("Sharma Traders").accounts


def test_a_refused_collision_proposes_nothing_and_writes_nothing() -> None:
    """Fails closed. The refused company may not post, and Tally is untouched."""
    from accountant import pipeline
    from accountant.extract.adapter import TypedTextExtractor

    first, second = "Bharat Steel Pvt. Ltd.", "Bharat Steel Pvt Ltd"
    t = _tally_with((first, "Purchases"))
    store = MemoryStore(":memory:")

    bootstrap(t, first, store)
    _open(t, second, "Repairs & Maintenance")
    clash = bootstrap(t, second, store)

    before = t.trial_balance(second)
    with pytest.raises(Exception):  # noqa: B017 - any refusal, never a post
        pipeline.run(
            second,
            b"paid Sharma Traders 4200 for cement",
            "text/plain",
            TypedTextExtractor(),
            t,
            clash,
            today=datetime.date(2026, 8, 31),
        )
    assert t.list_our_vouchers(second) == ()
    assert t.trial_balance(second) == before


def test_reopening_the_same_company_is_deterministic_and_not_a_collision() -> None:
    """The control. Without it, refusing EVERY second bootstrap would pass.

    A company colliding with ITSELF is not a collision - re-reading one
    company's books is the normal case and must stay READY.
    """
    only = "Ganesh Textiles"
    t = _tally_with((only, "Purchases"))
    store = MemoryStore(":memory:")

    first = bootstrap(t, only, store)
    second = bootstrap(t, only, store)

    assert first.report.status is BootstrapStatus.READY
    assert second.report.status is BootstrapStatus.READY
    assert second.lookup("Sharma Traders").accounts == ("Purchases",)
    assert second.identity.name == only


def test_two_companies_that_do_not_collide_both_bootstrap_fine() -> None:
    """The second control. The check must refuse collisions, not second companies."""
    t = _tally_with(("Nagpur Hardware", "Purchases"), ("Pune Auto Works", "Cash"))
    store = MemoryStore(":memory:")

    a = bootstrap(t, "Nagpur Hardware", store)
    b = bootstrap(t, "Pune Auto Works", store)

    assert a.report.status is BootstrapStatus.READY
    assert b.report.status is BootstrapStatus.READY
    assert a.identity.key != b.identity.key

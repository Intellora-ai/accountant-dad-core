"""Undoing, readiness order, and the drafts that were never let go of.

Three lifecycle defects, all recorded in `docs/PROJECT_STATE.md` and none of
them a wrong-write on its own. What they share is that each one made the system
say something it had not checked.

    the undo         `POST /reverse` called the client directly with whatever
                     `op` string the form carried, checked nothing, and
                     reported "reversed" on the strength of a boolean.
                     Criterion #6.5 — the trial balance returns to its exact
                     prior value in paise — was verified only inside tests,
                     never on the path a person uses.

    A5               `pipeline.run` read the chart and the history out of Tally
                     BEFORE anything looked at whether this company's books had
                     ever been read. On a flaky connector that produced
                     "connection refused" where the truth was "we have not read
                     your books". Same outcome, wrong diagnosis, and the two
                     lead to completely different actions.

    the drafts       `DRAFTS` was unbounded. Every entry anybody ever typed
                     stayed in memory for the life of the process, holding its
                     voucher, checks, flags and problems — sitting next to
                     `EVENTS`, which was capped at forty. The audit trail was
                     the bounded one and the live state was not.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about a real TallyPrime. `FakeTally` reverses by deleting the voucher
it wrote, which is a simplification of what a real reversal does. What is
proved here is that the connector's ANSWER is checked against the connector's
own trial balance rather than believed — a property that holds whatever the
backend does underneath.
"""

from __future__ import annotations

import datetime

import pytest

from accountant import pipeline
from accountant.extract.adapter import TypedTextExtractor
from accountant.memory.bootstrap import bootstrap, resume
from accountant.memory.company import CompanyMemory, MemoryNotReady
from accountant.memory.store import MemoryStore
from accountant.schema import Outcome, Voucher
from accountant.tallyio.fake import FakeTally
from accountant.web import app
from tests.test_period_handoff import open_books_for

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Repairs & Maintenance", "Cash")
TODAY = datetime.date(2026, 8, 9)


def history(n: int = 8) -> list[Voucher]:
    return [
        Voucher(
            id=f"h{i}",
            date=datetime.date(2026, 1, 1) + datetime.timedelta(days=i),
            party="Sharma Traders",
            narration="cement supply",
            debit_account="Purchases",
            credit_account="Cash",
            amount_paise=100_000,
        )
        for i in range(n)
    ]


def books() -> tuple[FakeTally, CompanyMemory]:
    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, vouchers=tuple(history()), backed_up=True)
    return t, bootstrap(t, COMPANY, MemoryStore(":memory:"))


def posted(t: FakeTally, memory: CompanyMemory) -> pipeline.Draft:
    draft = pipeline.run(
        COMPANY,
        b"paid Sharma Traders 4200 for cement",
        "text/plain",
        TypedTextExtractor(),
        t,
        memory,
        today=TODAY,
        period_reader=open_books_for(COMPANY),
    )
    assert draft.outcome is Outcome.VALID
    assert draft.posted_tally_id is not None
    return draft


# ---- the undo is measured against Tally's own trial balance -----------------


def test_a_reversal_reports_the_exact_paise_that_moved_back():
    t, memory = books()
    before = t.trial_balance(COMPANY)
    draft = posted(t, memory)
    assert t.trial_balance(COMPANY) != before

    result = pipeline.reverse_operation(t, COMPANY, draft.operation_id)

    assert result.reversed_ is True
    assert result.moved == {"Purchases": -420_000, "Cash": 420_000}
    assert "420000 paise" in result.detail
    assert t.trial_balance(COMPANY) == before, "criterion #6.5, on the live path"


def test_an_operation_id_nobody_wrote_reverses_nothing_and_says_so():
    """Not an exception. The person typed an id, and being told so is the answer."""
    t, _ = books()
    before = t.trial_balance(COMPANY)

    result = pipeline.reverse_operation(t, COMPANY, "ad_never_written")

    assert result.reversed_ is False
    assert result.moved == {}
    assert "carries" in result.detail and "ad_never_written" in result.detail
    assert t.trial_balance(COMPANY) == before


def test_a_reversal_that_says_yes_and_moves_nothing_is_refused():
    """The worst of the three outcomes, because it is the one that gets believed.

    A connector answering True while the books stand still is exactly what a
    boolean cannot distinguish from a real reversal, and it was the only thing
    `POST /reverse` ever looked at.
    """

    class SaysYesDoesNothing(FakeTally):
        def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:  # noqa: ARG002
            return True

    t = SaysYesDoesNothing()
    t.add_company(COMPANY, accounts=ACCOUNTS, vouchers=tuple(history()), backed_up=True)
    memory = bootstrap(t, COMPANY, MemoryStore(":memory:"))
    draft = posted(t, memory)
    after_posting = t.trial_balance(COMPANY)

    with pytest.raises(RuntimeError, match="reported success"):
        pipeline.reverse_operation(t, COMPANY, draft.operation_id)

    assert t.trial_balance(COMPANY) == after_posting, "the voucher is still there"


def test_a_reversal_that_moves_the_wrong_amount_is_refused_and_names_both():
    """Right voucher, wrong money. A boolean cannot see this either."""

    class ReversesHalf(FakeTally):
        def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
            found = [
                v
                for v in self.read_vouchers(company)
                if v.narration.endswith(f"{operation_id}]")
            ]
            if not found:
                return False
            co = self._companies[company]
            co.vouchers = [v for v in co.vouchers if v not in found]
            co.vouchers.append(
                Voucher(
                    id="stub",
                    date=TODAY,
                    party="Sharma Traders",
                    narration="half of it put back",
                    debit_account="Purchases",
                    credit_account="Cash",
                    amount_paise=found[0].amount_paise // 2,
                )
            )
            return True

    t = ReversesHalf()
    t.add_company(COMPANY, accounts=ACCOUNTS, vouchers=tuple(history()), backed_up=True)
    memory = bootstrap(t, COMPANY, MemoryStore(":memory:"))
    draft = posted(t, memory)

    with pytest.raises(RuntimeError) as refused:
        pipeline.reverse_operation(t, COMPANY, draft.operation_id)

    message = str(refused.value)
    assert "should have moved it by" in message
    assert "-420000" in message, "what should have moved"
    assert "-210000" in message, "what did move"


def test_the_draft_level_reverse_goes_through_the_same_doorway():
    """`pipeline.reverse` is thin on purpose, so the two paths cannot drift."""
    t, memory = books()
    before = t.trial_balance(COMPANY)
    draft = posted(t, memory)

    assert pipeline.reverse(draft, t) is True
    assert t.trial_balance(COMPANY) == before
    assert pipeline.reverse(draft, t) is False, "reversing twice is safe"
    assert t.trial_balance(COMPANY) == before


def test_the_web_handler_calls_the_verified_reversal_and_not_the_client():
    """A structural check. The claim is "no other path", and only the AST says so.

    Written the way `tests/test_runtime_backend.py` and
    `tests/test_phase4_exits.py` already scan for a forbidden call, because a
    behaviour test proves the handler is right today and this proves no second
    handler can be added tomorrow.
    """
    import ast
    import pathlib

    source = pathlib.Path(app.__file__).read_text()
    tree = ast.parse(source)
    direct = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "reverse_by_operation_id"
    ]
    assert direct == [], (
        "accountant/web/app.py calls the connector's reverse directly at lines "
        f"{direct}. Every undo goes through `pipeline.reverse_operation`, which "
        "checks the trial balance; calling the client straight past it is how "
        '"reversed" came to mean "a boolean said so".'
    )
    assert "reverse_operation" in source, "and the doorway really is used"


# ---- A5: readiness is checked before Tally is read --------------------------


def test_a_company_we_never_read_says_so_rather_than_blaming_the_connector():
    """A5. The order of two lines decides which of two truths the caller hears.

    Both are true here: this company has never been bootstrapped AND the
    connector is broken. Reading Tally first meant the connector's error won,
    every time, and "your network is down" sends somebody to the wrong place.
    """

    class Broken(FakeTally):
        def read_accounts(self, company: str) -> tuple[str, ...]:  # noqa: ARG002
            raise ConnectionError("Tally is not answering")

        def read_vouchers(self, company: str) -> tuple[Voucher, ...]:  # noqa: ARG002
            raise ConnectionError("Tally is not answering")

    t = Broken()
    t.add_company(COMPANY, accounts=ACCOUNTS, vouchers=(), backed_up=True)
    # Never bootstrapped: `resume` on a store that has no row for this company
    # hands back NEVER_RUN, which is what a fresh install actually looks like.
    never_read = resume(MemoryStore(":memory:"), COMPANY)

    with pytest.raises(MemoryNotReady, match="nothing may be proposed"):
        pipeline.run(
            COMPANY,
            b"paid Sharma Traders 4200 for cement",
            "text/plain",
            TypedTextExtractor(),
            t,
            never_read,
            today=TODAY,
            period_reader=open_books_for(COMPANY),
        )
    assert t.list_our_vouchers(COMPANY) == (), "and still nothing is written"


# ---- the drafts are bounded, and eviction is not silent ---------------------


def _draft(draft_id: str) -> pipeline.Draft:
    return pipeline.Draft(
        id=draft_id,
        company=COMPANY,
        voucher=Voucher(
            id=draft_id,
            date=TODAY,
            party="Sharma Traders",
            narration="cement",
            debit_account="Purchases",
            credit_account="Cash",
            amount_paise=420_000,
        ),
        record=TypedTextExtractor().extract(b"paid Sharma Traders 4200", "text/plain"),
        operation_id=f"ad_{draft_id}",
    )


def test_the_draft_store_is_bounded_and_drops_the_oldest_first():
    app.DRAFTS.clear()
    try:
        for i in range(app.DRAFT_LIMIT + 25):
            app.remember_draft(_draft(f"draft-{i:04d}"))

        assert len(app.DRAFTS) == app.DRAFT_LIMIT
        assert "draft-0000" not in app.DRAFTS, "the oldest went first"
        assert f"draft-{app.DRAFT_LIMIT + 24:04d}" in app.DRAFTS, "the newest stayed"
        # Nothing in the middle was skipped: eviction is a window, not a purge.
        assert min(app.DRAFTS) == f"draft-{25:04d}"
    finally:
        app.DRAFTS.clear()


def test_a_draft_still_being_answered_is_not_evicted_by_one_more_entry():
    """The failure eviction must not cause: losing the form somebody is filling in.

    200 rather than 40 for exactly this reason. A draft is only useful while
    somebody might still answer its question, and answering happens in minutes
    — but taking one away mid-question is worse than holding a few more.
    """
    app.DRAFTS.clear()
    try:
        mine = _draft("draft-mine")
        app.remember_draft(mine)
        for i in range(app.DRAFT_LIMIT - 1):
            app.remember_draft(_draft(f"draft-other-{i}"))

        assert app.DRAFTS["draft-mine"] is mine
        assert len(app.DRAFTS) == app.DRAFT_LIMIT
    finally:
        app.DRAFTS.clear()

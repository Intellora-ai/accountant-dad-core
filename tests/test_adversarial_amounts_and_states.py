"""Adversarial amounts, a drifting date, and the state machine's dangerous edges.

WHAT THIS FILE PROVES
---------------------
    * What an entry with a zero, negative, sub-paise, enormous or non-integer
      amount actually DECIDES, what the person actually READS, and what the
      write count is on each of those paths - taken from the double's own
      record rather than inferred from the books afterwards. Five of the six
      are zero. The sub-paise one is ONE, and that is the finding.
    * Which of the thirteen state names in the brief exist in this codebase and
      which do not. NINE of them are not objects here at all, and the test that
      says so scans the shipped package rather than asserting prose.
    * That six dangerous transitions are refused, each with the write count and
      the surviving state asserted after the refusal, never `pytest.raises`
      alone.
    * Five invariants, one test each.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
    * ANYTHING ABOUT A REAL TALLY. Every backend here is a double:
      `FakeTally`, the `RecordingTally` wrapper below, or the XML simulator
      `tests/test_real_tally.py::TallySim`. A simulator built from the same
      assumptions as `real.py` cannot falsify them. Two sockets are opened,
      both to loopback servers this file starts, and neither reaches Tally.
    * That the DEFECTS recorded here are the only ones. SIX tests below asserted
      a WRONG answer on purpose, because the job was to measure what happens
      and not to change it. Each carries a `DEFECT:` line naming file and line.
      They are not aspirations written as assertions; they are the
      measurement, and they will start failing the day somebody fixes the
      thing they describe. That is the intended alarm, and each one says in
      its message what to do when it goes off.

      THE ALARM HAS GONE OFF TWICE, 2026-08-09. The date-drift and
      amount-change tests both fired the moment `RealTally.write_voucher`
      started comparing the read-back field by field. Both are now flipped to
      assert the refusal, and they keep the differential evidence that made
      them worth writing: the create really went out, and the books really do
      hold something different. FOUR remain asserting a wrong answer.
    * That the amounts here are realistic. ₹92 quadrillion is not a payment
      anybody makes; it is the smallest amount that proves the paise never
      became a float on the way through.
    * Anything about concurrency. Every test here is single threaded, and the
      two that start a server do so to read shipped output, not to race it.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import pathlib
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Generator
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from xml.etree import ElementTree  # nosec B405 - only used to read our own output

import pytest

import accountant
from accountant import pipeline
from accountant.extract.adapter import StubExtractor, TypedTextExtractor
from accountant.memory.bootstrap import bootstrap
from accountant.memory.company import CompanyMemory, MemoryNotReady
from accountant.memory.store import BootstrapStatus, MemoryStore
from accountant.schema import ActionLog, Outcome, Voucher
from accountant.tallyio import real
from accountant.tallyio.client import WriteResult, new_operation_id, operation_id_in
from accountant.tallyio.factory import BackendIdentity, LicenceMode, new_run_id
from accountant.tallyio.factory import real_tally as connect_real_tally
from accountant.tallyio.fake import FakeTally
from accountant.web import app
from tests import test_real_tally as sim_module
from tests.test_period_handoff import open_books_for

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Repairs & Maintenance", "Cash")
TODAY = datetime.date(2026, 8, 7)
RUN_ID = "run_adversarial_amounts_and_states"

#: The amount every "does it post at all" fixture uses, so a test that changes
#: only the adversarial number changes only one thing.
KNOWN_VENDOR = "Sharma Traders"


# ===========================================================================
# doubles, and the write count they keep about themselves
# ===========================================================================


class RecordingTally:
    """`FakeTally` that keeps its own tally of every write it was ASKED for.

    The write count in every test below comes from `self.writes`, not from
    counting vouchers in the books afterwards. The difference matters on the
    read-back paths: a write can happen and still not be recorded as posted, so
    "the books have no new voucher" and "nothing was written" are two different
    claims and only this list can tell them apart.

    Everything except the bookkeeping is delegated, so the only behavioural
    difference from `FakeTally` is that it remembers.
    """

    def __init__(self, inner: FakeTally) -> None:
        self.inner = inner
        self.writes: list[tuple[str, str, object]] = []
        self.reversals: list[tuple[str, bool]] = []

    # ---- TallyClient -------------------------------------------------------

    def list_companies(self) -> tuple[str, ...]:
        return self.inner.list_companies()

    def read_accounts(self, company: str) -> tuple[str, ...]:
        return self.inner.read_accounts(company)

    def read_vouchers(self, company: str) -> tuple[Voucher, ...]:
        return self.inner.read_vouchers(company)

    def trial_balance(self, company: str) -> dict[str, int]:
        return self.inner.trial_balance(company)

    def write_voucher(
        self, company: str, voucher: Voucher, operation_id: str
    ) -> WriteResult:
        self.writes.append((company, operation_id, voucher.amount_paise))
        return self.inner.write_voucher(company, voucher, operation_id)

    def read_by_operation_id(self, company: str, operation_id: str) -> Voucher | None:
        return self.inner.read_by_operation_id(company, operation_id)

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        done = self.inner.reverse_by_operation_id(company, operation_id)
        self.reversals.append((operation_id, done))
        return done

    def list_our_vouchers(self, company: str) -> tuple[Voucher, ...]:
        return self.inner.list_our_vouchers(company)

    def backed_up(self, company: str) -> bool:
        # The ninth TallyClient method, added 2026-08-09 for G5.2. Delegated
        # like the rest: this double wraps a real FakeTally and has no opinion
        # about backups.
        return self.inner.backed_up(company)


def _history(
    party: str = KNOWN_VENDOR,
    account: str = "Purchases",
    n: int = 40,
    amount_paise: int = 100_000,
) -> tuple[Voucher, ...]:
    return tuple(
        Voucher(
            id=f"h{i}",
            date=datetime.date(2026, 1, 1),
            party=party,
            narration=f"{party} supply",
            debit_account=account,
            credit_account="Cash",
            amount_paise=amount_paise,
        )
        for i in range(n)
    )


def _books(
    history: tuple[Voucher, ...] = (),
    *,
    company: str = COMPANY,
    accounts: tuple[str, ...] = ACCOUNTS,
) -> RecordingTally:
    inner = FakeTally()
    inner.add_company(company, accounts=accounts, vouchers=history, backed_up=True)
    return RecordingTally(inner)


def _memory(client: RecordingTally, store: MemoryStore) -> CompanyMemory:
    return bootstrap(client, COMPANY, store)


def _run(client: RecordingTally, store: MemoryStore, text: str) -> pipeline.Draft:
    """One typed entry, all the way through, logging into `store`."""
    return pipeline.run(
        COMPANY,
        text.encode(),
        "text/plain",
        TypedTextExtractor(),
        client,
        _memory(client, store),
        today=TODAY,
        log=store,
        run_id=RUN_ID,
        # Without a reader `period_open` is `None` - nobody looked - and the
        # cage blocks every draft that reaches it. That refusal is correct and
        # is pinned in `tests/test_period_handoff.py`; it is not what any test
        # in THIS file is about, so the books are read and they are open.
        period_reader=open_books_for(COMPANY),
    )


def _stubbed(
    client: RecordingTally,
    memory: CompanyMemory,
    *,
    party: str = KNOWN_VENDOR,
    total_paise: int | None = None,
    tax_paise: int | None = None,
) -> pipeline.Draft:
    """A draft whose amount the typed-text reader could never produce.

    `TypedTextExtractor` cannot emit a negative amount - its regex has no sign -
    so a negative has to be injected at the extraction boundary rather than
    typed. That is the honest place for it: the amount arrives from an extractor
    exactly as a third-party reader's would.
    """
    accounts = client.read_accounts(COMPANY)
    draft = pipeline.build_draft(
        COMPANY,
        b"an entry the reader supplied",
        "text/plain",
        StubExtractor(party=party, total_paise=total_paise, tax_paise=tax_paise),
        memory,
        today=TODAY,
    )
    return pipeline.evaluate(
        draft,
        accounts,
        client.read_vouchers(COMPANY),
        memory,
        period_open=None,
        pdf_repaired=None,
    )


def _rows(store: MemoryStore, company: str = COMPANY) -> tuple[ActionLog, ...]:
    return store.actions(company)


def _card(draft: pipeline.Draft) -> str:
    """The decision card the shipped renderer produces. What a person reads."""
    return app.render_decision(draft)


def _question(draft: pipeline.Draft) -> str:
    asked = pipeline.next_question(draft)
    assert asked is not None, "this draft was expected to carry a question"
    return asked.text


def _read_amount(rupees: str) -> int | None:
    """The paise the SHIPPED typed-text reader makes of one rupee amount.

    Through `Extractor.extract` rather than through the module-private
    `_to_paise`, because the number that matters is the one an entry ends up
    carrying, not the one an internal helper returns on its own.
    """
    record = TypedTextExtractor().extract(
        f"paid Sharma Traders {rupees} for cement".encode(), "text/plain"
    )
    return record.total_paise


# ---- the XML simulator, for the paths that must cross the wire --------------


def _sim() -> sim_module.TallySim:
    sim = sim_module.TallySim()
    sim.add_company(sim_module.COMPANY, sim_module.ACCOUNTS)
    return sim


def _real(sim: sim_module.TallySim) -> real.RealTally:
    return real.RealTally(
        transport=sim,
        backups=real.RecordedBackups(frozenset({sim_module.COMPANY})),
    )


def _creates(sim: sim_module.TallySim) -> int:
    """Write count, off the simulator's own record of what reached it."""
    return sum(1 for payload in sim.sent if 'ACTION="Create"' in payload)


def _voucher(
    amount_paise: int,
    *,
    date: datetime.date = datetime.date(2026, 8, 1),
    party: str = KNOWN_VENDOR,
) -> Voucher:
    return Voucher(
        id="v-adversarial",
        date=date,
        party=party,
        narration="cement",
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=amount_paise,
    )


# ===========================================================================
# PART A - money and dates
# ===========================================================================


def test_a_zero_amount_asks_how_much_and_writes_nothing() -> None:
    """Expected UNCLEAR, actual UNCLEAR, write count 0.

    Zero fails `amount_is_positive`, which `problems.py` treats as answerable,
    so the entry asks rather than refusing. That is the designed boundary: a
    person can fix a missing number, so we ask for it.

    backend RecordingTally | cleanup not attempted, nothing to clean.
    """
    store = MemoryStore(":memory:")
    client = _books(_history())

    draft = _run(client, store, "paid Sharma Traders 0 for cement")

    assert draft.voucher.amount_paise == 0
    assert draft.outcome is Outcome.UNCLEAR
    assert draft.reason == "amount is 0 paise"
    assert client.writes == [], "a zero amount must reach no write at all"
    assert client.inner.list_our_vouchers(COMPANY) == ()
    assert client.reversals == []

    assert _question(draft) == (
        "How much did you pay Sharma Traders? I couldn't work it out."
    )
    card = _card(draft)
    assert card.count("₹0.00") == 1, "the amount is shown exactly once"
    assert card.count('class="badge b-unclear"') == 1

    rows = _rows(store)
    assert len(rows) == 1
    assert (rows[0].action, rows[0].outcome) == ("blocked", Outcome.UNCLEAR.value)
    assert rows[0].reason == "amount is 0 paise"
    assert rows[0].run_id == RUN_ID
    assert rows[0].backend == "RecordingTally"
    assert rows[0].voucher_id == "", "nothing was posted, so there is no Tally id"
    assert rows[0].detail.endswith("0 paise")


def test_a_negative_amount_asks_how_much_and_is_shown_to_the_person_correctly() -> None:
    """Expected UNCLEAR, actual UNCLEAR, write count 0, and the right number.

    DEFECT, FIXED 2026-08-09. `rupees()` was
    `f"{paise // 100:,}.{paise % 100:02d}"`, and Python floors both operators
    towards negative infinity. -420050 paise is ₹-4,200.50 and renders as
    "-4,201.50"; -1 paise renders as "-1.99". `real.rupees_from_paise` splits
    the sign off first and gets it right, so the two renderers in this system
    disagree about every negative that is not a whole rupee.

    Fixed by rendering the magnitude and prepending the sign, exactly as
    `real.rupees_from_paise` already did. The two renderers in this system now
    agree on every negative.

    Asserted here rather than in isolation because this is the user-facing
    message for this case, and the case is the one where a negative reaches the
    screen at all.

    backend RecordingTally | cleanup not attempted.
    """
    store = MemoryStore(":memory:")
    client = _books(_history())
    memory = _memory(client, store)

    draft = _stubbed(client, memory, total_paise=-420_050)

    assert draft.voucher.amount_paise == -420_050
    assert draft.outcome is Outcome.UNCLEAR
    assert draft.reason == "amount is -420050 paise"
    assert client.writes == []
    assert client.inner.list_our_vouchers(COMPANY) == ()

    assert _question(draft) == (
        "How much did you pay Sharma Traders? I couldn't work it out."
    )

    card = _card(draft)
    assert card.count("₹-4,200.50") == 1, "the screen shows what the entry carries"
    assert card.count("₹-4,201.50") == 0, "the old floored rendering is gone"
    assert app.rupees(-1) == "-0.01"
    assert real.rupees_from_paise(-420_050) == "-4200.50", (
        "and the two renderers agree, which is the property that was broken"
    )

    # The gate refuses it as well as the screen mis-stating it, and the refusal
    # leaves the books alone.
    with pytest.raises(ValueError, match="refusing to post"):
        pipeline.post(draft, client)
    assert client.writes == []
    assert draft.posted_tally_id is None


def test_a_sub_paise_amount_is_truncated_by_the_reader_and_refused_by_the_wire() -> (
    None
):
    """Both components refuse the same string now. FIXED 2026-08-09.

    DEFECT, until then: `_AMOUNT` matched at most two decimal places, so
    "10.005" matched as "10.00" and the third digit was dropped without a word;
    `_to_paise` then multiplied a FLOAT by 100. `real.paise_from_rupees`
    refused the identical string - "carries sub-paise precision; refusing to
    round it away" - because rounding invoice arithmetic is how reconciliation
    breaks later.

    So a sub-paise amount typed by a person was silently rounded and POSTED,
    while the same amount arriving from Tally was refused. Measured then:
    amount 1000 paise, outcome VALID, one write, and the log row said "1000
    paise" so the truncation was unrecoverable from the trail.

    Now the reader returns no amount at all, which `amount_is_positive` turns
    into a question. Refusing rather than raising, because an unreadable amount
    is a question for the person and an exception here would be a 500.

    backend RecordingTally | cleanup not attempted.
    """
    store = MemoryStore(":memory:")
    client = _books(_history())

    draft = _run(client, store, "paid Sharma Traders 10.005 for cement")

    assert draft.voucher.amount_paise == 0, (
        "no amount was read, so none is carried - never a rounded one"
    )
    assert int(Decimal("10.005") * 100) == 1000, (
        "1000 paise is the truncation, and 1000.5 paise is not representable - "
        "which is why both components refuse the string instead of picking one"
    )
    with pytest.raises(real.TallyDataError, match="sub-paise"):
        real.paise_from_rupees("10.005")

    assert draft.outcome is Outcome.UNCLEAR
    assert draft.reason == "amount is 0 paise"
    assert client.writes == [], "nothing is written and nothing is truncated"
    assert client.inner.list_our_vouchers(COMPANY) == ()

    rows = _rows(store)
    assert [r.action for r in rows] == ["blocked"], (
        "no write was attempted, so there is no write-ahead row either"
    )
    assert {r.run_id for r in rows} == {RUN_ID}
    assert {r.backend for r in rows} == {"RecordingTally"}

    # The disconfirming case, on the same reader and the same sentence shape:
    # two decimal places still read exactly, so the refusal is about precision
    # and not about decimals.
    fine = _run(client, store, "paid Sharma Traders 10.50 for cement")
    assert fine.voucher.amount_paise == 1050
    assert fine.outcome is Outcome.VALID
    op = fine.operation_id
    assert client.reverse_by_operation_id(COMPANY, op) is True
    assert client.inner.trial_balance(COMPANY) == {
        "Purchases": 4_000_000,
        "Cash": -4_000_000,
    }, "cleanup put the books back to the forty history vouchers alone"


def test_an_amount_beyond_a_32_bit_int_crosses_the_wire_to_the_exact_paise() -> None:
    """No 32-bit or 64-bit ceiling anywhere on the path, and no float either.

    Two amounts: one paise past a signed 32-bit int, and one past a signed
    64-bit int whose rupee rendering is nineteen characters long. Both go out
    as decimal rupee strings and come back parsed, so the assertion covers
    `rupees_from_paise`, the XML round trip and `paise_from_rupees` together.

    Write count 2, both intended. ActionLog and the user-facing message are not
    applicable: this is the connector, below the pipeline that writes rows.

    backend RealTally over tests/test_real_tally.py::TallySim.
    """
    beyond_32 = 2**31 + 1
    beyond_64 = 2**63 + 7
    sim = _sim()
    client = _real(sim)

    written: list[int] = []
    for amount in (beyond_32, beyond_64):
        op = new_operation_id()
        client.write_voucher(sim_module.COMPANY, _voucher(amount), op)
        back = client.read_by_operation_id(sim_module.COMPANY, op)
        assert back is not None
        written.append(back.amount_paise)

    assert written == [beyond_32, beyond_64], "a paise was lost crossing the wire"
    assert _creates(sim) == 2

    assert real.rupees_from_paise(beyond_32) == "21474836.49"
    assert real.rupees_from_paise(beyond_64) == "92233720368547758.15"
    assert len(real.rupees_from_paise(beyond_64)) == 20, (
        "the long rendering is the point; a shorter one would prove nothing"
    )
    assert real.paise_from_rupees("92233720368547758.15") == beyond_64

    balance = client.trial_balance(sim_module.COMPANY)
    assert balance == {
        "Purchases": beyond_32 + beyond_64,
        "Cash": -(beyond_32 + beyond_64),
    }
    assert sum(balance.values()) == 0


def test_a_tenth_plus_two_tenths_of_a_rupee_lands_on_the_exact_paise() -> None:
    """The 0.1 + 0.2 case, taken all the way to a trial balance and back.

    `tests/test_real_tally.py::test_the_amounts_a_float_would_get_wrong` already
    proves `paise_from_rupees` beats `int(float(x) * 100)` on four single
    values. What it does not cover is ADDITION: two amounts posted, summed by
    the ledger, and then removed again. Floating point loses on the sum even
    when it wins on each part, and a trial balance is nothing but sums.

    Expected 30 paise, actual 30 paise. Write count 2, both intended, cleanup
    True twice and the balance byte-identical afterwards.

    backend RealTally over TallySim | no ActionLog on this path.
    """
    assert 0.1 + 0.2 != 0.3, "the bug this test exists for, stated out loud"

    sim = _sim()
    client = _real(sim)
    sim.seed(sim_module.COMPANY, narration="rent by hand", amount_paise=7)
    before = client.trial_balance(sim_module.COMPANY)
    assert before == {"Purchases": 7, "Cash": -7}, (
        "an empty starting balance would make the restore comparison vacuous"
    )

    ops: list[str] = []
    for amount in (10, 20):
        op = new_operation_id()
        client.write_voucher(sim_module.COMPANY, _voucher(amount), op)
        ops.append(op)

    during = client.trial_balance(sim_module.COMPANY)
    assert during["Purchases"] - before["Purchases"] == 30, (
        "₹0.10 and ₹0.20 must add to exactly ₹0.30 of movement"
    )
    assert _creates(sim) == 2

    assert [client.reverse_by_operation_id(sim_module.COMPANY, op) for op in ops] == [
        True,
        True,
    ]
    assert client.trial_balance(sim_module.COMPANY) == before


def test_a_rupee_amount_a_float_cannot_hold_loses_a_paise_before_it_reaches_tally() -> (
    None
):
    """The typed-text reader no longer multiplies a float by 100. FIXED 2026-08-09.

    DEFECT, until then: `_to_paise` was
    `round(float(text.replace(",", "")) * 100)`. A float64 holds
    about sixteen significant digits, so from ₹99,999,999,999,999.99 upward the
    paise it produces is simply the wrong integer - here one paise short, and
    at ₹999,999,999,999,999.99 one paise long. `real.paise_from_rupees` uses
    `Decimal` and is exact at both.

    The module docstring of `accountant/schema.py` says money is integer paise
    everywhere and that a float in a money field is a correctness bug. This is
    that bug, in the one component that reads what a person typed.

    Fixed with `Decimal`, the expression `paise_from_rupees` already used.

    Write count 0 - nothing is posted here, the reader is called directly.
    ActionLog and the rendered message are not applicable at this layer.
    """
    exact_low = int(Decimal("99999999999999.99") * 100)
    exact_high = int(Decimal("999999999999999.99") * 100)

    assert _read_amount("99999999999999.99") == exact_low, (
        "one paise short before the fix; exact now"
    )
    assert _read_amount("999999999999999.99") == exact_high, (
        "one paise long before the fix; exact now"
    )

    assert real.paise_from_rupees("99999999999999.99") == exact_low
    assert real.paise_from_rupees("999999999999999.99") == exact_high

    # And the reader is exact everywhere below the float's limit, so the fault
    # is precision and not the algorithm - which is what makes it easy to miss.
    for text in ("0.29", "4.35", "8.20", "12345678901234.56"):
        assert _read_amount(text) == int(Decimal(text) * 100)

    record = TypedTextExtractor().extract(
        b"paid Sharma Traders 99999999999999.99 for cement", "text/plain"
    )
    assert record.total_paise == exact_low, (
        "the exact integer is what the pipeline goes on to write"
    )
    assert record.per_field_source["total_paise"] == "typed_text", (
        "the record claims the reader supplied this number, and now it can"
    )


class EducationalTally(sim_module.TallySim):
    """A REAL TallyPrime in Educational mode, in the one way that matters here.

    Educational mode accepts only the 1st, 2nd and 31st of a month. This double
    does what the owner decision of 2026-08-08 leaves this project living with:
    it takes the voucher, answers `created=1 status=1`, and stores it under a
    date we did not ask for. Nothing about the exchange says so.
    """

    coerced = datetime.date(2026, 8, 1)

    def _import(self, root: ElementTree.Element, company: str | None) -> str:
        node = root.find(".//VOUCHER")
        answer = super()._import(root, company)
        if node is not None and node.get("ACTION") == "Create":
            assert company is not None, "a Create envelope always names a company"
            self.companies[company].vouchers[-1].date = self.coerced
        return answer


def test_a_date_tally_moved_under_us_is_read_back_and_accepted_in_silence() -> None:
    """A date Tally moved under us is now refused, not reported as a clean write.

    DEFECT, FIXED 2026-08-09. `accountant/tallyio/real.py` compared only
    `written is None`; `accountant/pipeline.py` did the same until W1. Neither
    compared one field of the returned voucher against the voucher that was
    sent, so a Tally that accepted the write and stored different content
    reported a clean success.

    This is not hypothetical here. `docs`-level owner decision of 2026-08-08
    leaves this project on a Tally in Educational mode, which accepts only the
    1st, 2nd and 31st. An entry dated the 7th is exactly the case: the write
    succeeds, the books hold the 1st, and the operator is told it posted.

    Expected: the write is refused and the field is named.
    Actual (2026-08-09): `TallyWriteMismatch`, `WRONG_DATE`, `fields == ("date",)`.

    The create still went out — that is why the check has to exist at all, and
    why `safe_to_retry` is False. The rest of this test is unchanged: it still
    proves the drift is Tally's and not a date this connector never sent.

    backend RealTally over EducationalTally | no ActionLog on this path.
    """
    asked_for = datetime.date(2026, 8, 7)
    sim = EducationalTally()
    sim.add_company(sim_module.COMPANY, sim_module.ACCOUNTS)
    client = _real(sim)

    op = new_operation_id()
    with pytest.raises(real.TallyWriteMismatch) as refused:
        client.write_voucher(sim_module.COMPANY, _voucher(420_000, date=asked_for), op)

    assert refused.value.verdict.outcome is real.ReadBackOutcome.WRONG_DATE
    assert refused.value.verdict.fields == ("date",)
    assert refused.value.verdict.safe_to_retry is False
    assert _creates(sim) == 1, "the write DID go out; refusing is not undoing"

    back = client.read_by_operation_id(sim_module.COMPANY, op)
    assert back is not None
    assert back.date == EducationalTally.coerced
    assert back.date != asked_for, (
        "the drift this test measures did not happen; the double is broken"
    )
    assert (asked_for - back.date).days == 6

    # Everything else survived, which is what makes the date drift invisible:
    # the marker matches, so the read-back finds it and calls it done.
    assert back.amount_paise == 420_000
    assert operation_id_in(back.narration) == op

    # And the payload really did carry the 7th, so the drift is Tally's and not
    # a date this connector never sent.
    created = [p for p in sim.sent if 'ACTION="Create"' in p]
    assert len(created) == 1
    assert created[0].count("<DATE>20260807</DATE>") == 1


def test_an_amount_tally_changed_under_us_is_also_accepted_in_silence() -> None:
    """The same missing comparison, on the field that is money.

    DEFECT: the same two call sites as the date case above. Recorded separately
    because a fix that special-cases the date would leave this one open, and
    because an amount silently halved is the version of this defect that costs
    money rather than a filing period.

    Expected: refused. Actual (2026-08-09): `TallyWriteMismatch`,
    `WRONG_AMOUNT`, `fields == ("amount_paise",)`.

    The trial-balance assertion at the end is the point of keeping this test
    after the fix: refusing the write does not un-write it. The books still
    hold half. What changed is that nobody is told it went in correctly.
    """
    sent = 420_000
    stored = 210_000

    class HalvesTheAmount(sim_module.TallySim):
        def _import(self, root: ElementTree.Element, company: str | None) -> str:
            node = root.find(".//VOUCHER")
            answer = super()._import(root, company)
            if node is not None and node.get("ACTION") == "Create":
                assert company is not None
                self.companies[company].vouchers[-1].amount_paise = stored
            return answer

    sim = HalvesTheAmount()
    sim.add_company(sim_module.COMPANY, sim_module.ACCOUNTS)
    client = _real(sim)

    op = new_operation_id()
    with pytest.raises(real.TallyWriteMismatch) as refused:
        client.write_voucher(sim_module.COMPANY, _voucher(sent), op)

    assert refused.value.verdict.outcome is real.ReadBackOutcome.WRONG_AMOUNT
    assert refused.value.verdict.fields == ("amount_paise",)
    assert _creates(sim) == 1, "the write DID go out; refusing is not undoing"

    back = client.read_by_operation_id(sim_module.COMPANY, op)
    assert back is not None
    assert back.amount_paise == stored
    assert back.amount_paise != sent, "the double did not actually change anything"

    assert client.trial_balance(sim_module.COMPANY) == {
        "Purchases": stored,
        "Cash": -stored,
    }, "the books hold half of what we were told was written"


# ===========================================================================
# PART B - the state machine, as it exists rather than as it was named
# ===========================================================================
#
# The thirteen names in the brief map onto this codebase like this. Only TWO of
# them - READY and EMPTY_SOURCE - are state values as named. NINE appear
# nowhere in the package at all. Saying so is the result:
#
#   DISCONNECTED         NOT AN ENUM. `accountant/web/app.py:91`
#                        `_runtime_state is None`. `runtime()` raises,
#                        `backend_state()` returns BACKEND_UNAVAILABLE.
#   CONNECTED            NOT A SEPARATE STATE. `app.Runtime` (app.py:75) is
#                        built by `configure()`, which bootstraps in the same
#                        call, so "connected but not yet read" cannot exist.
#   BOOTSTRAPPING        DOES NOT EXIST. `bootstrap()` is one synchronous
#                        function; its progress is a local `done: list[str]`
#                        that only becomes visible as `BootstrapReport.steps`
#                        after it has finished or failed.
#   READY                BootstrapStatus.READY, and `BootstrapReport.ready`.
#   EMPTY_SOURCE         BootstrapStatus.EMPTY_SOURCE.
#   BOOTSTRAP_FAILURE    NOT ONE STATE. It is three: INCOMPLETE (a step
#                        failed), EMPTY_VENDOR_INDEX (read fine, learned
#                        nothing) and NEVER_RUN. The distinction is deliberate
#                        - see store.py:136-158.
#   DRAFT                `pipeline.Draft` with `decision is None`. A field
#                        being None, not a state value.
#   VALID                Outcome.VALID.
#   INVALID              Outcome.NOT_VALID. There is no member named INVALID,
#                        and there is a THIRD outcome the brief's list omits:
#                        Outcome.UNCLEAR, which is where most adversarial
#                        amounts in Part A actually land.
#   POSTING              EXISTS SINCE 2026-08-09, as a durable row rather than
#                        as a state value: `pipeline.WRITE_ATTEMPTED`, written
#                        AHEAD of the socket. It had to be a row and not a
#                        field, because the case that matters is the process
#                        not surviving to update a field. A `write_attempted`
#                        with no partner row is the in-flight marker; the
#                        partner is `posted` or `write_outcome_unknown`.
#   POSTED               EXISTS SINCE 2026-08-13 as a real state value:
#                        `cage.state.State.POSTED`, terminal, reached only by
#                        `WriteConfirmed` from POSTING. Before the cage it was
#                        `Draft.posted_tally_id is not None` - a field being
#                        non-None, which cannot say whether the write was
#                        confirmed or merely attempted. The field is still
#                        there and still means what it meant; the state is the
#                        thing that now has a transition into it.
#   READ_BACK_VERIFIED   DOES NOT EXIST as a state value, but since 2026-08-09
#                        it is a real VERDICT: `real.ReadBackVerdict`, with
#                        `outcome`, the differing `fields`, and `confirmed`.
#                        It no longer verifies existence only.
#   READ_BACK_FAILED     DOES NOT EXIST as one thing, and the split matters:
#                        `TallyWriteMismatch` (Tally definitely stored
#                        something else) and `TallyWriteUnknown` (we cannot
#                        tell) are different facts, and a `RuntimeError` from
#                        `pipeline.post` is a third.
#   CLEANED              NOT A STATE. `reverse_by_operation_id` returning True.
#                        Nothing on the draft records it.

#: The names the brief uses that appear NOWHERE in the shipped package.
INVENTED_STATE_NAMES = (
    "DISCONNECTED",
    "CONNECTED",
    "BOOTSTRAPPING",
    "BOOTSTRAP_FAILURE",
    # "POSTING" was here until 2026-08-09. It is no longer invented: W2's
    # write-ahead row gives it a durable representation, and this list is only
    # honest while it names things that really are absent.
    #
    # "POSTED" was here until 2026-08-13, and left for the same reason:
    # `cage.state.State.POSTED` is a real, terminal state value with a real
    # transition into it. This test failed the moment the state machine landed,
    # which is what it is for - the map above was updated and the count below
    # lowered together, per the instruction in this file's own error message.
    "READ_BACK_VERIFIED",
    "READ_BACK_FAILED",
    "CLEANED",
)


def _package_source() -> str:
    root = pathlib.Path(accountant.__file__).parent
    return "\n".join(path.read_text() for path in sorted(root.rglob("*.py")))


def test_eight_of_the_thirteen_state_names_do_not_exist_in_the_shipped_package() -> (
    None
):
    """Naming the mismatch is the result. This checks it instead of asserting it.

    Scanned on whole words over every `.py` file in `accountant/`, so
    `FROM_OUR_POSTING` does not count as `POSTING` and `DRAFTS` does not count
    as `DRAFT`. The positive half at the end is what stops this passing on an
    empty scan.
    """
    source = _package_source()
    assert len(source) > 100_000, "the scan read no package to speak of"

    found = {
        name: len(re.findall(rf"\b{name}\b", source))
        for name in INVENTED_STATE_NAMES
        if re.search(rf"\b{name}\b", source)
    }
    assert found == {}, (
        f"a state this file reports as absent now exists: {found}. Update the "
        "map at the top of PART B rather than deleting the assertion."
    )
    # SEVEN since 2026-08-13. "POSTING" left on 2026-08-09 when W2's
    # write-ahead row gave it a durable representation; "POSTED" left when
    # `cage.state.State.POSTED` gave it a real one. The count is asserted so
    # the list cannot be quietly shortened to make a failure go away -
    # shortening it is allowed, but only together with this number and the map
    # above it, which is exactly the procedure both removals followed.
    assert len(INVENTED_STATE_NAMES) == 7

    # The four that DO exist, pinned so the absence above cannot be vacuous.
    for present in ("READY", "EMPTY_SOURCE", "NOT_VALID", "UNCLEAR"):
        assert re.search(rf"\b{present}\b", source), f"{present} vanished"


def test_the_states_that_do_exist_are_exactly_these_two_enums() -> None:
    """`BootstrapStatus` and `Outcome` are the whole of it.

    Anything the brief names that is not a member of one of these is a field, a
    None, or an exception - never a state value - and the mapping comment above
    says which.
    """
    assert [s.name for s in BootstrapStatus] == [
        "READY",
        "EMPTY_SOURCE",
        "EMPTY_VENDOR_INDEX",
        "INCOMPLETE",
        "NEVER_RUN",
        # Added 2026-08-09 with the D3 fix. A collision is not INCOMPLETE:
        # INCOMPLETE means a step failed part way and a row was written
        # recording that. A collision means NOTHING was read and NOTHING was
        # written, because writing the failure would itself have stamped this
        # company's name onto the other one's row.
        "COMPANY_KEY_COLLISION",
    ]
    assert [o.name for o in Outcome] == ["NOT_VALID", "UNCLEAR", "VALID"]
    assert not hasattr(Outcome, "INVALID"), "INVALID is spelled NOT_VALID here"
    assert not hasattr(Outcome, "POSTED"), "posting is not an outcome"

    draft_fields = [f.name for f in dataclasses.fields(pipeline.Draft)]
    assert "state" not in draft_fields and "status" not in draft_fields, (
        "a Draft carries no state field; DRAFT/POSTING/POSTED are inferred "
        f"from decision and posted_tally_id. Fields are {draft_fields}"
    )
    assert "decision" in draft_fields
    assert "posted_tally_id" in draft_fields

    # UPDATED 2026-08-10, and the change is recorded rather than made quietly.
    #
    #   old        ["client", "identity", "memory", "store"]
    #   new        the same four, plus "extractor"
    #   why        `accountant/web/app.py::_run` called `default_extractor()`
    #              per request, so the reading backend was NOT part of the
    #              connected state - it was conjured inside the route. A reader
    #              outage was therefore unreachable over HTTP.
    #              `configure(extractor=...)` now resolves it once and holds it.
    #   weakened?  No. The assertion is still exact equality over every field,
    #              and `extractor` carries NO DEFAULT, so the claim this test
    #              makes - a Runtime cannot exist half built - now covers one
    #              more thing than it did. A default here is exactly what would
    #              weaken it, and there is none.
    # UPDATED 2026-08-15, same rule, and the second half is now scoped rather
    # than weakened.
    #
    #   old        the five above
    #   new        the same five, plus "period_reader"
    #   why        the cage asks whether the books are open for the date on the
    #              bill. `connect()` - the only path holding a `TallyConfig` -
    #              builds a reader; `configure()` with a double cannot, because
    #              there is no gateway behind a fake client and a probe would be
    #              a socket call to whatever is on port 9000 of this machine.
    #   default?   YES, and it is the ONLY field here that carries one, so the
    #              blanket claim below could not survive unchanged. It is
    #              SCOPED and not dropped: every other field must still have no
    #              default, and this one's default must be exactly `None`.
    #
    #              The distinction is fail-open versus fail-closed and it is the
    #              whole reason one default is allowed here. A missing
    #              `extractor` would be a silent capability loss - the object
    #              looks connected and reads nothing. A missing `period_reader`
    #              is `None`, `Runtime.period_open` returns `None`, the cage
    #              reads that as "nobody looked" and BLOCKS. The dangerous
    #              default would be a reader that answered `True`, and pinning
    #              the default to `None` is what makes that a test failure
    #              rather than a code review somebody has to remember to do.
    assert [f.name for f in dataclasses.fields(app.Runtime)] == [
        "client",
        "identity",
        "memory",
        "store",
        "extractor",
        "period_reader",
    ], "CONNECTED is this object existing, and it cannot exist half built"
    assert all(
        f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
        for f in dataclasses.fields(app.Runtime)
        if f.name != "period_reader"
    ), "a Runtime field with a default is a Runtime that can be built half empty"
    period_reader = next(
        f for f in dataclasses.fields(app.Runtime) if f.name == "period_reader"
    )
    assert period_reader.default is None, (
        "the one field allowed a default must default to the value that BLOCKS. "
        f"This one defaults to {period_reader.default!r}."
    )
    assert period_reader.default_factory is dataclasses.MISSING


# ---- the six dangerous transitions ------------------------------------------


@contextlib.contextmanager
def _app_serving(client: RecordingTally) -> Generator[str]:
    """The shipped app on an ephemeral port, backed by a recording double.

    The store is opened on the SERVING thread, because SQLite binds a
    connection to the thread that opened it and `configure()` bootstraps there.
    Everything this test needs to read back afterwards therefore comes off the
    served pages or off `client`, which is plain Python.
    """
    app.DRAFTS.clear()
    httpd = HTTPServer(("127.0.0.1", 0), app.Handler)
    ready = threading.Event()

    def serve() -> None:
        app.configure(
            client,
            BackendIdentity(
                backend="RecordingTally",
                endpoint="memory://tests/test_adversarial_amounts_and_states.py",
                company=app.COMPANY,
                company_exists=True,
                companies_visible=1,
                run_id=RUN_ID,
                licence_mode=LicenceMode.UNKNOWN.value,
                licence_detail="constructed by this test; nothing was measured",
            ),
            store=MemoryStore(":memory:"),
            # `configure` builds no reader of its own - see the Runtime field
            # assertions above - so without this the served app blocks on
            # "nobody looked whether the books are open".
            period_reader=open_books_for(app.COMPANY),
        )
        ready.set()
        httpd.serve_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=5), "the server thread never configured a runtime"
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        app.disconnect()
        app.DRAFTS.clear()


def _get(base: str, path: str = "/") -> tuple[int, str]:
    try:
        with urllib.request.urlopen(base + path, timeout=5) as response:  # noqa: S310
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def _post(base: str, path: str, **fields: str) -> tuple[int, str]:
    data = urllib.parse.urlencode(fields).encode()
    try:
        with urllib.request.urlopen(base + path, data=data, timeout=5) as response:  # noqa: S310
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_a_disconnected_app_refuses_to_post_and_says_which_refusal_it_is() -> None:
    """DISCONNECTED -> POSTING. No runtime, no write.

    Driven over HTTP against the shipped handler, because the refusal has to be
    an ANSWER and not only an absence: a dropped socket also posts nothing and
    tells the person nothing.

    One entry is posted BEFORE the disconnect, so the claim afterwards is "the
    write count did not go up" rather than "the write count is zero". A zero
    that was always going to be zero proves nothing about the refusal.

    Expected refusal, actual 503 carrying REFUSAL, write count unchanged at 1,
    and no decision row can be written because `record` and `note` both go
    through `runtime()` first.
    """
    client = _books(_history(), company=app.COMPANY)

    with _app_serving(client) as base:
        # A double is not TallyPrime, and the app says so rather than reading a
        # licence mode off something that has none.
        assert app.backend_state() == app.BACKEND_NOT_REAL

        code, body = _post(base, "/entry", text="paid Sharma Traders 4200 for cement")
        assert code == 200
        assert body.count('class="badge b-valid"') == 1
        writes_while_connected = list(client.writes)
        assert len(writes_while_connected) == 1

        logged, page = _get(base)
        assert logged == 200
        assert page.count('data-outcome="valid"') == 1, (
            "one posted row on the page, so the log is provably being written"
        )

        app.disconnect()

        code, body = _post(base, "/entry", text="paid Sharma Traders 4200 for cement")

        assert code == 503, "the service exists and is not available"
        assert body.count(app.REFUSAL) == 1
        assert "no operation performed" in body
        assert body.count('data-backend-state="unavailable"') == 1
        assert body.count('data-backend-state="real-ok"') == 0
        assert "not connected to Tally" in body
        assert body.count('data-outcome="valid"') == 0, (
            "the refusal page shows no posted entry, because it can read none"
        )

        assert client.writes == writes_while_connected, (
            "a disconnected app reached a write it should not have"
        )

        # The two functions that could append a row both ask for the runtime
        # first, so a row is not merely absent here - it is unreachable.
        with pytest.raises(RuntimeError, match=app.REFUSAL):
            app.note("reversed", "reversed", "a reason nobody may record now")
        with pytest.raises(RuntimeError, match=app.REFUSAL):
            app.runtime()

        assert app.backend_state() == app.BACKEND_UNAVAILABLE
        health = app.health()
        assert health["ready"] is False
        assert health["failure_code"] == "NO_RUNTIME"
        assert health["backend"] is None
        assert client.writes == writes_while_connected


def test_a_bootstrap_that_failed_part_way_through_posts_nothing() -> None:
    """BOOTSTRAP_FAILURE -> POSTING. The books were not read, so nothing moves.

    INCOMPLETE, not NEVER_RUN. `tests/test_pipeline_isolation.py` covers the
    never-bootstrapped case; this is the other one - a read that started, got
    the chart, and then lost the connection before the voucher history. The
    report names the step it died on, and the run still writes nothing.

    The drop is ONE SHOT: Tally goes away during the history read and comes
    back afterwards. That is the shape that matters, because a connector still
    failing would stop `pipeline.run` on its own read and prove nothing about
    memory. Here every read works and the ONLY thing standing between the entry
    and the books is the bootstrap record.

    Expected MemoryNotReady before any decision, actual the same, write count 0,
    no ActionLog row - the failure happens before `record_decision` is reached.
    """

    class DropsTheConnectionOnce(FakeTally):
        def __init__(self) -> None:
            super().__init__()
            self.dropped = False

        def read_vouchers(self, company: str) -> tuple[Voucher, ...]:
            if not self.dropped:
                self.dropped = True
                raise RuntimeError("Tally dropped the connection half way through")
            return super().read_vouchers(company)

    inner = DropsTheConnectionOnce()
    inner.add_company(COMPANY, accounts=ACCOUNTS, vouchers=_history(), backed_up=True)
    client = RecordingTally(inner)
    store = MemoryStore(":memory:")
    memory = bootstrap(client, COMPANY, store)

    assert inner.dropped, "the double never actually dropped anything"
    assert client.read_vouchers(COMPANY) != (), "Tally is back; only memory is not"

    assert memory.report.status is BootstrapStatus.INCOMPLETE
    assert memory.report.steps == ("identity", "accounts")
    assert "failed at step 'vouchers'" in memory.report.detail
    assert memory.report.ready is False
    assert memory.report.askable is False

    with pytest.raises(MemoryNotReady, match="nothing may be proposed"):
        pipeline.run(
            COMPANY,
            b"paid Sharma Traders 4200 for cement",
            "text/plain",
            TypedTextExtractor(),
            client,
            memory,
            today=TODAY,
            log=store,
            run_id=RUN_ID,
        )

    assert client.writes == [], "books we could not read must not be written to"
    assert client.inner.list_our_vouchers(COMPANY) == ()
    assert _rows(store) == (), "no decision was reached, so there is none to log"

    banner = app.bootstrap_banner(memory.report)
    assert banner.count(app.CANNOT_HELP) == 1
    assert "did not get to the end" in banner

    # THE DISCONFIRMING CHECK. Tally is up, the double is willing, the entry is
    # postable - so re-reading the books is the ONLY thing that changes, and it
    # changes the answer. Without this the zero above would also hold if this
    # entry could never post for some entirely different reason.
    recovered = bootstrap(client, COMPANY, store)
    assert recovered.report.status is BootstrapStatus.READY
    again = pipeline.run(
        COMPANY,
        b"paid Sharma Traders 4200 for cement",
        "text/plain",
        TypedTextExtractor(),
        client,
        recovered,
        today=TODAY,
        log=store,
        run_id=RUN_ID,
        period_reader=open_books_for(COMPANY),
    )
    assert again.outcome is Outcome.VALID
    assert len(client.writes) == 1
    assert [r.action for r in _rows(store)] == [pipeline.WRITE_ATTEMPTED, "posted"]


def test_an_empty_source_company_may_be_asked_but_proposes_and_writes_nothing() -> None:
    """EMPTY_SOURCE -> POSTING. Owner rule D2a, both halves of it.

    A legitimate new customer: their books opened, they were read, and there was
    nothing in them. That is a fact about the customer, so a question is honest;
    a proposal is impossible because there is no measured mapping to propose
    from.

    Expected UNCLEAR with an empty debit account, actual the same, write count
    0, one ActionLog row saying blocked/unclear.
    """
    store = MemoryStore(":memory:")
    client = _books(())
    memory = _memory(client, store)

    assert memory.report.status is BootstrapStatus.EMPTY_SOURCE
    assert memory.report.ready is False
    assert memory.report.askable is True, "D2a: it may ask"
    assert memory.report.counts.mappings == 0

    draft = _run(client, store, "paid Gupta Hardware 1500 for tools")

    assert draft.voucher.debit_account == "", "nothing may be proposed"
    assert draft.outcome is Outcome.UNCLEAR
    assert draft.reason == "gupta_hardware has never been posted before"
    assert client.writes == []
    assert client.inner.list_our_vouchers(COMPANY) == ()

    assert _question(draft) == "What did you get from Gupta Hardware?"
    card = _card(draft)
    assert card.count("<tr><td>Debit</td><td>—</td></tr>") == 1, (
        "the debit account is shown as absent, not filled in with a guess"
    )

    rows = _rows(store)
    assert len(rows) == 1
    assert (rows[0].action, rows[0].outcome) == ("blocked", Outcome.UNCLEAR.value)
    assert rows[0].detail.startswith("(none proposed)")
    assert rows[0].run_id == RUN_ID
    assert rows[0].backend == "RecordingTally"

    banner = app.bootstrap_banner(memory.report)
    assert banner.count(app.CANNOT_HELP) == 1
    assert "no past entries in them at all" in banner

    # D2a's other half, and the disconfirming check for the zero above: once the
    # person's answer HAS created a measured mapping, the same entry posts. So
    # "never writes" is a statement about the state and not about this double.
    accounts = client.read_accounts(COMPANY)
    draft = pipeline.answer(draft, "Purchases")
    memory.record_correction(draft.voucher.party, "Purchases")
    draft = pipeline.evaluate(
        draft,
        accounts,
        client.read_vouchers(COMPANY),
        memory,
        # This half of D2a is about the ANSWER creating a mapping, so the one
        # fact the answer cannot supply is read rather than left at "nobody
        # looked" - which blocks, and would make the post below unreachable for
        # a reason that has nothing to do with D2a.
        period_open=True,
        pdf_repaired=None,
    )

    # An empty-source company has no history at all, so it cannot say how this
    # vendor was paid either. Both legs are asked about; neither is invented.
    assert draft.outcome is Outcome.UNCLEAR
    assert client.writes == [], "still nothing written while a question is open"
    draft = pipeline.answer(draft, "Cash", problem_id=pipeline.FUNDING_PROBLEM)
    draft = pipeline.evaluate(
        draft,
        accounts,
        client.read_vouchers(COMPANY),
        memory,
        period_open=True,
        pdf_repaired=None,
    )

    assert draft.outcome is Outcome.VALID
    draft = pipeline.post(draft, client)
    assert len(client.writes) == 1
    assert client.writes[0][2] == 150_000
    assert draft.posted_tally_id == "TALLY-1"


def test_a_not_valid_entry_is_refused_by_the_post_gate_and_moves_no_money() -> None:
    """INVALID -> POSTING. Not valid never posts.

    A float amount is the only failure in this system that no answer can fix -
    `problems.UNANSWERABLE_CHECKS` holds exactly `amount_is_integer_paise` - so
    it is the only way to reach NOT_VALID from an amount. It is injected past
    the extractor because no extractor can produce one, which is precisely why
    the runtime check in `checks.py` exists.

    Expected NOT_VALID and a refusal, actual the same, write count 0, and the
    trial balance is unchanged across the attempt.

    DEFECT, found by trying to read the user-facing message for this case:
    accountant/web/app.py:357-358 again. `render_decision` cannot render a
    NOT_VALID float draft at all - `rupees()` raises "Unknown format code 'd'".
    So the ONE outcome that means "this must not be posted" is the one outcome
    the screen cannot show. It is latent today only because
    `accountant/web/app.py:60-67` records that NOT_VALID is unreachable from a
    typed entry; the day any other unanswerable check appears, or any extractor
    hands back a non-integer, the refusal becomes a 500 instead of a message.
    The log row is written first, so the audit trail survives and the person
    sees nothing.

    Smallest fix: the same one - split the sign, and coerce with `int()` or
    refuse explicitly rather than relying on a format code to notice.
    """
    store = MemoryStore(":memory:")
    client = _books(_history())
    memory = _memory(client, store)
    accounts = client.read_accounts(COMPANY)

    draft = _stubbed(client, memory, total_paise=4200)
    draft.voucher = dataclasses.replace(draft.voucher, amount_paise=4200.5)  # type: ignore[arg-type]
    draft = pipeline.evaluate(
        draft,
        accounts,
        client.read_vouchers(COMPANY),
        memory,
        period_open=None,
        pdf_repaired=None,
    )

    assert draft.outcome is Outcome.NOT_VALID
    assert draft.reason == "amount_is_integer_paise: amount is float"
    assert pipeline.next_question(draft) is None, "there is nothing to ask"

    before = client.inner.trial_balance(COMPANY)
    assert before, "a flat starting balance would make the comparison vacuous"

    with pytest.raises(ValueError, match="refusing to post: outcome is not_valid"):
        pipeline.post(draft, client)

    assert client.writes == []
    assert draft.posted_tally_id is None
    assert client.inner.trial_balance(COMPANY) == before
    assert client.inner.list_our_vouchers(COMPANY) == ()

    # FIXED 2026-08-09. `_card(draft)` raised `ValueError: Unknown format code
    # 'd' for object of type 'float'` out of `app.rupees`, so the ONE outcome
    # that means "nothing was posted" was the one outcome the screen could not
    # draw. The person got a traceback instead of the reason.
    #
    # `rupees` stays strict - a money formatter that renders a float as rupees
    # is how a lost paise stops being visible - and the page degrades instead,
    # printing the value as it actually is and saying it is not an amount.
    card = _card(draft)
    assert card.count("4200.5 (not an amount)") == 1
    assert card.count("₹") == 0, "nothing here is rendered as a rupee figure"
    assert "amount is float" in card, "and the reason is on the same screen"
    assert app.ACTION_FOR[Outcome.NOT_VALID] == "blocked"

    # The refusal is recorded as well as shown.
    pipeline.record_decision(store, draft, memory, client, "blocked", RUN_ID)
    rows = _rows(store)
    assert len(rows) == 1
    assert rows[0].outcome == Outcome.NOT_VALID.value
    assert rows[0].reason == "amount_is_integer_paise: amount is float"
    assert rows[0].run_id == RUN_ID


def test_an_unevaluated_draft_cannot_be_posted_and_still_has_no_outcome() -> None:
    """DRAFT -> POSTED without evaluate. An unevaluated draft cannot post.

    The state assertion after the raise is the load-bearing half: `decision`
    must still be None afterwards, so the refusal cannot have quietly evaluated
    the draft on the way past.

    Expected refusal, actual refusal, write count 0.
    """
    store = MemoryStore(":memory:")
    client = _books(_history())
    memory = _memory(client, store)

    draft = pipeline.build_draft(
        COMPANY,
        b"paid Sharma Traders 4200 for cement",
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    assert draft.decision is None
    assert draft.voucher.debit_account == "Purchases", (
        "memory proposed an account, so this draft would post the moment it was "
        "evaluated - the gate is the only thing stopping it"
    )

    with pytest.raises(ValueError, match="draft has not been evaluated"):
        pipeline.post(draft, client)

    assert draft.decision is None, "the refusal must not have evaluated it"
    assert draft.posted_tally_id is None
    assert client.writes == []
    assert client.inner.list_our_vouchers(COMPANY) == ()

    with pytest.raises(ValueError, match="draft has not been evaluated"):
        _ = draft.outcome

    pipeline.record_decision(store, draft, memory, client, "posted", RUN_ID)
    assert _rows(store) == (), (
        "an unevaluated draft has no decision, so the recorder writes nothing "
        "even when a caller asks it to record a post"
    )


def test_a_failed_read_back_is_never_recorded_as_a_posted_entry() -> None:
    """READ_BACK_FAILED -> POSTED. A failed read-back is not a success.

    `tests/test_pipeline.py` proves `post` raises and records no tally id. What
    it does not cover is the ACTION LOG: the row that would tell somebody six
    months later that this operation id is in their books. Here the write is
    attempted (count 1, from the double's own record), the read-back finds
    nothing, and no `posted` row exists afterwards.
    """
    store = MemoryStore(":memory:")
    client = _books(_history())
    memory = _memory(client, store)
    accounts = client.read_accounts(COMPANY)

    draft = pipeline.build_draft(
        COMPANY,
        b"paid Sharma Traders 4200 for cement",
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    draft = pipeline.evaluate(
        draft,
        accounts,
        client.read_vouchers(COMPANY),
        memory,
        # This test is about the READ-BACK, so the draft has to get past the
        # Valid gate on its own merits; "nobody looked at the period" would
        # stop it one step earlier and prove nothing about the read-back.
        period_open=True,
        pdf_repaired=None,
    )
    assert draft.outcome is Outcome.VALID, "the Valid gate is not what stops this"

    blind = _Blind(client)
    with pytest.raises(RuntimeError, match="could not read it back"):
        pipeline.post(draft, blind)

    assert len(blind.writes) == 1, (
        "without this the test would also pass if post raised BEFORE writing"
    )
    assert draft.posted_tally_id is None
    assert blind.readbacks == [(COMPANY, draft.operation_id)]

    pipeline.record_decision(store, draft, memory, blind, "posted", RUN_ID)
    rows = _rows(store)
    assert [r.voucher_id for r in rows] == [""], (
        "the row must carry no Tally id, because none was ever confirmed"
    )
    assert len(rows) == 1
    assert rows[0].operation_id == draft.operation_id
    assert rows[0].backend == "_Blind"


class _Blind(RecordingTally):
    """`RecordingTally` whose read-back always finds nothing.

    Subclassed rather than rewritten, so the ONLY difference from the double
    every other test uses is the branch under test. The inner books still see
    the write, which is correct: the write really did happen. What must not
    happen is anything recording it as posted.
    """

    def __init__(self, other: RecordingTally) -> None:
        super().__init__(other.inner)
        self.readbacks: list[tuple[str, str]] = []

    def read_by_operation_id(self, company: str, operation_id: str) -> Voucher | None:
        self.readbacks.append((company, operation_id))
        return None


# ===========================================================================
# PART C - the invariants, asserted
# ===========================================================================


def test_invalid_input_never_reaches_a_real_write() -> None:
    """Invariant 1, taken to the connector rather than stopped at the pipeline.

    A float amount is refused three times over: `checks.amount_is_integer_paise`
    makes it NOT_VALID, `pipeline.post` refuses a non-VALID draft, and
    `build_voucher_create` fails before anything reaches the transport. The
    third is checked here because the first two could both be bypassed by a
    caller and the connector is the last line.

    Write count 0, from the simulator's own record of what arrived.

    FINDING, not a defect: `real._check_writable` (real.py:702-726) checks the
    sign, both accounts and GST, but NOT that the amount is an integer. The
    float is stopped one line later by `rupees_from_paise` (real.py:319-321),
    whose message is "Unknown format code 'd' for object of type 'float'" - it
    names no voucher, no company and no amount, unlike every deliberate refusal
    beside it. It fails closed, which is what matters, but it fails by
    accident: `rupees_from_paise(True)` returns "0.01", so a bool amount would
    be written as one paise rather than refused.

    Smallest fix: one more clause in `_check_writable` mirroring
    `checks.amount_is_integer_paise`, bool excluded the same way.
    """
    sim = _sim()
    client = _real(sim)
    bad = dataclasses.replace(_voucher(4200), amount_paise=4200.5)  # type: ignore[arg-type]

    with pytest.raises(real.TallyRejected) as refused:
        client.write_voucher(sim_module.COMPANY, bad, new_operation_id())

    assert _creates(sim) == 0, "a float amount reached the wire"
    assert client.read_vouchers(sim_module.COMPANY) == ()
    assert client.trial_balance(sim_module.COMPANY) == {}

    # A4, FIXED 2026-08-09, stated as the observable difference. The refusal
    # used to be `ValueError: Unknown format code 'd' for object of type
    # 'float'` from `rupees_from_paise` one line later - naming no voucher, no
    # field and no amount, so whoever read that log learned nothing about which
    # entry to look at. Every DELIBERATE refusal in `_check_writable` opens
    # with "refusing to write voucher <id>", and this one now does too.
    message = str(refused.value)
    assert "refusing to write" in message
    assert bad.id in message
    assert "4200.5" in message
    assert "float" in message
    assert "Unknown format code" not in message

    # The write counter is live. Without this the zero above would also hold if
    # `_creates` matched nothing at all.
    client.write_voucher(sim_module.COMPANY, _voucher(4200), new_operation_id())
    assert _creates(sim) == 1

    # The case the format code could never have caught: a bool IS an int in
    # Python, so it sails straight through `rupees_from_paise` and renders as
    # one paise. Only an explicit type check refuses it.
    assert real.rupees_from_paise(True) == "0.01"
    boolean = dataclasses.replace(_voucher(4200), amount_paise=True)  # type: ignore[arg-type]
    with pytest.raises(real.TallyRejected, match="bool"):
        client.write_voucher(sim_module.COMPANY, boolean, new_operation_id())
    assert _creates(sim) == 1, "still one; the bool never reached the wire"


def test_a_bootstrap_that_derived_no_vendor_mapping_is_not_ready_and_health_says_so() -> (  # noqa: E501
    None
):
    """Invariant 2, read off `/health` rather than off the report.

    `tests/test_bootstrap_readiness.py` proves the STATUS is
    EMPTY_VENDOR_INDEX. This proves the readiness endpoint agrees: a monitor
    that reports healthy while the app can answer nothing is the failure that
    endpoint exists to prevent.

    Forty vouchers were read and every one of them is unusable, which is the
    case that used to be READY with zero mappings.
    """
    app.DRAFTS.clear()
    store = MemoryStore(":memory:")
    nameless = tuple(dataclasses.replace(v, party="") for v in _history(n=40)[:40])
    client = _books(nameless, company=app.COMPANY)
    identity = BackendIdentity(
        backend="RecordingTally",
        endpoint="memory://tests/test_adversarial_amounts_and_states.py",
        company=app.COMPANY,
        company_exists=True,
        companies_visible=1,
        run_id=RUN_ID,
    )
    try:
        runtime = app.configure(client, identity, store=store)
        report = runtime.memory.report
        health = app.health()
    finally:
        app.disconnect()

    assert report.status is BootstrapStatus.EMPTY_VENDOR_INDEX
    assert report.ready is False
    assert report.askable is False
    assert report.bootstrapped_at == "", (
        "a read that taught us nothing did not 'last successfully read' anything"
    )

    assert health["ready"] is False
    assert health["failure_code"] == "EMPTY_VENDOR_INDEX"
    assert health["vouchers_read"] == 40, "the books really were read"
    assert health["vendor_mappings_derived"] == 0
    assert health["unusable_rows"] == 40
    assert health["run_id"] == RUN_ID
    assert health["backend"] == "RecordingTally"

    assert client.writes == [], "an unready company is never written to"


def test_a_read_back_failure_prevents_success_at_the_connector_too() -> None:
    """Invariant 3, one layer below `pipeline.post`.

    Tally answers `created=1 status=1` and the voucher is not there afterwards.
    HTTP said yes, the import counters said yes, and the only thing that catches
    it is the read-back. `write_voucher` must raise rather than hand back a
    `WriteResult` naming a voucher that does not exist.
    """
    sim = _sim()
    client = _real(sim)
    sim.import_override = sim_module.import_response(
        created=1, status=1, last_vch_id="M9"
    )

    op = new_operation_id()
    with pytest.raises(real.TallyRejected, match="was not found") as rejected:
        client.write_voucher(sim_module.COMPANY, _voucher(4200), op)

    assert op in str(rejected.value)
    assert "whatever HTTP said" in str(rejected.value)

    assert _creates(sim) == 1, "the write was attempted; that is the whole point"
    assert client.read_vouchers(sim_module.COMPANY) == ()
    assert client.read_by_operation_id(sim_module.COMPANY, op) is None
    assert client.trial_balance(sim_module.COMPANY) == {}


def test_cleanup_restores_the_trial_balance_to_the_exact_paise_it_started_at() -> None:
    """Invariant 4, with amounts chosen to break a float rather than to be tidy.

    One paise, the two amounts `int(x * 100)` gets wrong, one past a 32-bit int,
    and a hand-typed voucher that must survive untouched. The balance is
    compared as a whole dict, so a stray account appearing or disappearing fails
    as loudly as a wrong number.
    """
    sim = _sim()
    client = _real(sim)
    sim.seed(sim_module.COMPANY, narration="rent paid by hand", amount_paise=29)

    before = client.trial_balance(sim_module.COMPANY)
    assert before == {"Purchases": 29, "Cash": -29}

    amounts = (1, 29, 435, 820, 2**31 + 1)
    ops: list[str] = []
    for amount in amounts:
        op = new_operation_id()
        client.write_voucher(sim_module.COMPANY, _voucher(amount), op)
        ops.append(op)

    during = client.trial_balance(sim_module.COMPANY)
    assert during["Purchases"] == 29 + sum(amounts)
    assert sum(during.values()) == 0
    assert _creates(sim) == len(amounts) == 5

    assert [client.reverse_by_operation_id(sim_module.COMPANY, op) for op in ops] == [
        True
    ] * 5

    after = client.trial_balance(sim_module.COMPANY)
    assert after == before, f"cleanup left {after} where {before} was expected"
    assert len(client.read_vouchers(sim_module.COMPANY)) == 1
    assert client.list_our_vouchers(sim_module.COMPANY) == (), (
        "every voucher of ours is gone and the hand-typed one is not"
    )


# ---- invariant 5: identity before any operation, over a real socket ---------


class _SimOverHttp(BaseHTTPRequestHandler):
    """The XML simulator behind a loopback HTTP server.

    Anything the simulator cannot answer - the `$$LicenseInfo` probe, which is
    not a collection it knows - comes back as an `<ERRORMSG>` envelope with
    HTTP 200. That is what the live TallyPrime 7.0 did when A11 was measured on
    2026-08-09, so a refusal is modelled as Tally's answer and not as a crash.
    """

    sim: sim_module.TallySim

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        payload = self.rfile.read(length).decode()
        try:
            body = type(self).sim.send(payload, retry=False)
        except Exception as exc:
            body = (
                "<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>0</STATUS></HEADER>"
                f"<BODY><DATA><ERRORMSG>Could not find: {type(exc).__name__}"
                "</ERRORMSG></DATA></BODY></ENVELOPE>"
            )
        raw = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args: object) -> None:
        pass


@contextlib.contextmanager
def _loopback(sim: sim_module.TallySim) -> Generator[real.TallyConfig]:
    handler = type("_Bound", (_SimOverHttp,), {"sim": sim})
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield real.TallyConfig(
            host="127.0.0.1", port=httpd.server_address[1], timeout_seconds=5.0
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_the_real_tally_identity_is_measured_before_any_operation() -> None:
    """Invariant 5. Every field of the identity is read, and nothing is written.

    The factory is the only place a runtime client is built, so this is where
    "which Tally are we on" is answered. Two halves:

      * the identity comes back FILLED IN, from the connection rather than from
        a constant - `backend`, `endpoint`, `company_exists`,
        `companies_visible`, `licence_mode` and a fresh `run_id`;
      * identification is READ ONLY. The simulator's own record of what arrived
        contains no Import envelope at all, so a misconfigured startup cannot
        touch anybody's books.

    `licence_mode` is UNKNOWN because this gateway will not answer
    `$$LicenseInfo`, which is A11's measured behaviour, and UNKNOWN is a
    measurement rather than a default here.

    Then the third half, which is what makes identity useful: the client the
    factory returns refuses the very first write, because its backup set is
    empty by default.
    """
    sim = _sim()
    with _loopback(sim) as config:
        client, identity = connect_real_tally(config, sim_module.COMPANY)

        assert identity.backend == "RealTally"
        assert identity.endpoint == config.url
        assert identity.company == sim_module.COMPANY
        assert identity.company_exists is True
        assert identity.companies_visible == 1
        assert identity.licence_mode == LicenceMode.UNKNOWN.value
        assert "Could not find" in identity.licence_detail
        assert identity.run_id.startswith("run_")
        assert identity.run_id != new_run_id(), "each run id is its own"

        assert _creates(sim) == 0, "identification wrote something"
        assert not any("Import" in payload for payload in sim.sent), (
            "the read-only check sent an Import envelope"
        )
        assert len(sim.sent) == 2, (
            "one company list and one licence probe. A gateway that cannot "
            "answer the first licence question is not asked the other two."
        )

        with pytest.raises(real.CompanyNotBackedUp, match="no recorded backup"):
            client.write_voucher(sim_module.COMPANY, _voucher(4200), new_operation_id())

        assert _creates(sim) == 0
        assert client.read_vouchers(sim_module.COMPANY) == ()

        # The counter is live: the same simulator, reached by a client that HAS
        # a recorded backup, does write. Without this the zeros above would
        # hold just as well if `_creates` matched nothing.
        backed_up = real.RealTally(
            config, backups=real.RecordedBackups(frozenset({sim_module.COMPANY}))
        )
        backed_up.write_voucher(sim_module.COMPANY, _voucher(4200), new_operation_id())
        assert _creates(sim) == 1

    metrics = identity.as_metrics()
    assert metrics["backend"] == "RealTally"
    assert metrics["licence_mode"] == LicenceMode.UNKNOWN.value
    assert metrics["run_id"] == identity.run_id

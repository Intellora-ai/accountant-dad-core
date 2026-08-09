"""Adversarial pressure on the WRITE path: write when we must not, write twice,
or believe a write succeeded when it did not.

Every failure in here ends as a wrong or duplicated voucher in somebody's real
statutory books, so each test asserts the whole evidence set rather than one
layer of it: the expected decision, the decision that actually came out, the
EXACT number of writes taken from the double's own record, the backend identity,
the ActionLog row, the words a person would read, the cleanup result and the run
id. Where it applies, the differential is asserted too - request sent, response
received, register read back, trial balance before / after / after cleanup. One
layer is never accepted as proof of another.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
* Nothing here touches a real TallyPrime. The backends are `FakeTally`, wrapped
  per the `tests/test_memory.py` RecordingTally idiom, and `RealTally` driven by
  `tests.test_real_tally.TallySim`. A simulator built from the same assumptions
  as the connector cannot falsify those assumptions; it can only show that the
  connector is inconsistent with itself. Everything here about "what Tally
  does" is a hypothesis wearing XML.
* It does not drive the web app over HTTP. `tests/test_web.py` does that. The
  app-level tests here call the rendering and health functions directly, so
  they prove what the page SAYS, not that a socket served it.
* It does not prove the reverse-restores-the-trial-balance property against
  real books. That is only meaningful against the real thing.

DEFECTS THIS FILE FOUND, AND DID NOT FIX
----------------------------------------
The tests below marked `@pytest.mark.xfail(strict=True)` are deliberate, and it
is said loudly here rather than hidden in a decorator: each one asserts the
behaviour the system SHOULD have, is expected to fail at this commit, and will
turn into a hard failure the moment somebody fixes the defect and forgets to
update it. Each xfail is paired with a plain passing test that pins the
behaviour actually measured, so the defect is visible in a green run.

    D2  accountant/pipeline.py:281   a write whose outcome is unknown records
                                     no ActionLog row at all
    D3  accountant/tallyio/real.py:1230  an error envelope on the voucher
                                     export reads as an empty company, which
                                     silently disables the C5 duplicate guard
    D5  accountant/web/app.py:243    nothing cross-checks the declared backend
                                     identity against the client that was
                                     handed in

FIXED SINCE, AND STILL PINNED HERE
----------------------------------
The xfail came off and the paired test was rewritten to assert the fixed
behaviour, so the case keeps failing if the fix is ever undone. Each says
"FIXED <date>. This test pinned the DEFECT until then." in its docstring.

    D1  accountant/pipeline.py   the C6 read-back was a presence check, not an
                                 identity check. FIXED 2026-08-09.
    D4  accountant/tallyio/fake.py  the double resolved a duplicated marker by
                                 picking the first, where RealTally refuses.
                                 FIXED 2026-08-09; the agreement between the
                                 two backends is now pinned by the
                                 `test_both_backends_*` cases in section 8.
"""

from __future__ import annotations

import datetime
import inspect
from collections.abc import Callable, Iterator
from dataclasses import replace

import pytest

from accountant import pipeline
from accountant.extract.adapter import TypedTextExtractor
from accountant.memory.bootstrap import bootstrap
from accountant.memory.company import CompanyMemory
from accountant.memory.store import MemoryStore
from accountant.schema import ActionLog, Decision, Outcome, Voucher
from accountant.tallyio import real
from accountant.tallyio.client import (
    MARKER_PREFIX,
    DuplicateOperation,
    TallyClient,
    WriteResult,
    marker_for,
    new_operation_id,
    operation_id_in,
)
from accountant.tallyio.factory import BackendIdentity
from accountant.tallyio.fake import FakeTally
from accountant.web import app
from tests import test_runtime_backend as guard
from tests import test_tally_contract as contract
from tests.test_real_tally import TallySim

# `tests/test_runtime_backend.py` keeps its call-graph scanner private, which is
# right for a module that is a guard rather than a library. It is BORROWED here
# rather than copied: a second scanner would be a second thing to keep in step
# with the guard these tests are supposed to be extending, and the first time
# the two drifted apart, the copy would go on passing.
call_sites = guard._call_sites  # pyright: ignore[reportPrivateUsage]
sites_in_the_package = (
    guard._sites_outside_the_connector  # pyright: ignore[reportPrivateUsage]
)
describe_sites = guard._describe  # pyright: ignore[reportPrivateUsage]

#: Where the bypass specimen below would live if somebody really committed it.
HIDDEN_PATH = "accountant/_hidden_writer.py"

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Sundry Expenses", "Repairs & Maintenance", "Cash")
TODAY = datetime.date(2026, 8, 7)
ENTRY = "paid Sharma Traders 4200 for cement"
ENTRY_PAISE = 420000

# One run id per case, so a row found in the log can only have come from the
# test that is asserting on it.
RUN = "run_writepath_adversarial"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def past(
    party: str = "Sharma Traders",
    account: str = "Purchases",
    n: int = 40,
    narration: str = "cement supply",
) -> list[Voucher]:
    """History a bootstrap can learn one unambiguous mapping from."""
    return [
        Voucher(
            id=f"hist-{i}",
            date=datetime.date(2026, 1, 1),
            party=party,
            narration=narration,
            debit_account=account,
            credit_account="Cash",
            amount_paise=100000 + i,
        )
        for i in range(n)
    ]


def tally(history: list[Voucher] | None = None) -> FakeTally:
    t = FakeTally()
    t.add_company(
        COMPANY, accounts=ACCOUNTS, vouchers=tuple(history or []), backed_up=True
    )
    return t


def memory_for(client: TallyClient, store: MemoryStore) -> CompanyMemory:
    return bootstrap(client, COMPANY, store)


def valid_draft(client: TallyClient, memory: CompanyMemory) -> pipeline.Draft:
    """A draft the decision order has already ruled Valid.

    Built through the real `build_draft`/`evaluate` rather than by hand, so the
    write-path tests below are pushing the same object the product pushes.
    """
    accounts = client.read_accounts(COMPANY)
    history = client.read_vouchers(COMPANY)
    draft = pipeline.build_draft(
        COMPANY,
        ENTRY.encode(),
        "text/plain",
        TypedTextExtractor(),
        accounts,
        memory,
        today=TODAY,
    )
    draft = pipeline.evaluate(draft, accounts, history, memory)
    assert draft.outcome is Outcome.VALID, "the fixture must reach the write path"
    return draft


def unclear_draft(client: TallyClient, memory: CompanyMemory) -> pipeline.Draft:
    """A draft the decision order refuses to post: a vendor never seen before."""
    accounts = client.read_accounts(COMPANY)
    history = client.read_vouchers(COMPANY)
    draft = pipeline.build_draft(
        COMPANY,
        b"paid Gupta Hardware 1500 for tools",
        "text/plain",
        TypedTextExtractor(),
        accounts,
        memory,
        today=TODAY,
    )
    draft = pipeline.evaluate(draft, accounts, history, memory)
    assert draft.outcome is Outcome.UNCLEAR, "the fixture must NOT be postable"
    return draft


def only_row(store: MemoryStore) -> ActionLog:
    rows = store.actions(COMPANY)
    assert len(rows) == 1, f"expected exactly one action row, got {len(rows)}: {rows}"
    return rows[0]


# ---------------------------------------------------------------------------
# the doubles - all of them WRAP FakeTally rather than reimplementing it
# ---------------------------------------------------------------------------


class RecordingTally:
    """FakeTally, with every write and read-back it was asked for written down.

    The write count in every assertion below comes from `self.writes`, which is
    this object's own record of what it was ASKED to do - not from the register,
    which is what the inner fake decided to do about it. Conflating those two is
    how "the write was refused" and "the write happened and vanished" become
    indistinguishable.
    """

    def __init__(self, inner: FakeTally) -> None:
        self.inner = inner
        self.writes: list[tuple[str, str, int]] = []
        self.read_backs: list[tuple[str, str]] = []
        self.reversals: list[tuple[str, str]] = []

    @property
    def write_count(self) -> int:
        return len(self.writes)

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
        self.read_backs.append((company, operation_id))
        return self.inner.read_by_operation_id(company, operation_id)

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        self.reversals.append((company, operation_id))
        return self.inner.reverse_by_operation_id(company, operation_id)

    def list_our_vouchers(self, company: str) -> tuple[Voucher, ...]:
        return self.inner.list_our_vouchers(company)


class LosesTheReadBack(RecordingTally):
    """The write lands; the read-back finds nothing. C6's expensive case."""

    def read_by_operation_id(self, company: str, operation_id: str) -> Voucher | None:
        self.read_backs.append((company, operation_id))
        return None


class SwapsTheReadBack(RecordingTally):
    """The read-back carries OUR marker and somebody else's numbers.

    The scenario is not exotic. A concurrent user edits the entry a second after
    we wrote it, or a marker is copied into another voucher by a duplicate-entry
    feature. The marker is our identity; it is not proof that the voucher behind
    it is still the one we sent.
    """

    def __init__(self, inner: FakeTally, *, substitute: Voucher) -> None:
        super().__init__(inner)
        self.substitute = substitute

    def read_by_operation_id(self, company: str, operation_id: str) -> Voucher | None:
        self.read_backs.append((company, operation_id))
        return replace(
            self.substitute,
            narration=f"{self.substitute.narration} {marker_for(operation_id)}",
        )


class HidesItFromTheRegister(RecordingTally):
    """Tally says created, the marker view agrees, its own register does not.

    `read_by_operation_id` is our filter over our own marker. `read_vouchers`
    and `trial_balance` are what Tally reports to everybody. When those two
    disagree, the second one is the books.
    """

    def read_vouchers(self, company: str) -> tuple[Voucher, ...]:
        return tuple(
            v
            for v in self.inner.read_vouchers(company)
            if operation_id_in(v.narration) is None
        )

    def list_our_vouchers(self, company: str) -> tuple[Voucher, ...]:
        del company
        return ()

    def trial_balance(self, company: str) -> dict[str, int]:
        balances: dict[str, int] = {}
        for v in self.read_vouchers(company):
            balances[v.debit_account] = (
                balances.get(v.debit_account, 0) + v.amount_paise
            )
            balances[v.credit_account] = (
                balances.get(v.credit_account, 0) - v.amount_paise
            )
        return {k: v for k, v in balances.items() if v != 0}


class DropsTheReadBackConnection(RecordingTally):
    """Tally accepted the write and then went away before we could confirm it."""

    def read_by_operation_id(self, company: str, operation_id: str) -> Voucher | None:
        self.read_backs.append((company, operation_id))
        raise ConnectionError("Tally dropped the connection during the read-back")


# ---- transports, for the RealTally cases -----------------------------------


def _is_import(payload: str) -> bool:
    return "<TALLYREQUEST>Import</TALLYREQUEST>" in payload


def _is_voucher_export(payload: str) -> bool:
    return f"<ID>{real.COLLECTION_VOUCHERS}</ID>" in payload


#: What a Tally gateway answers when the company is not set on the session:
#: a well-formed envelope, a DATA block, and no vouchers in it. Measured shape
#: taken from `tests/test_real_tally.py::import_response` and the CMPINFO header
#: in `_voucher_payload`; the LINEERROR text is Tally's own wording.
EXPORT_ERROR = (
    "<ENVELOPE>"
    "<HEADER><VERSION>1</VERSION><STATUS>0</STATUS></HEADER>"
    "<BODY><DESC><CMPINFO><COMPANY>0</COMPANY><VOUCHER>0</VOUCHER></CMPINFO></DESC>"
    "<DATA><LINEERROR>Could not set 'SVCURRENTCOMPANY'</LINEERROR></DATA>"
    "</BODY></ENVELOPE>"
)


class DropsTheReply:
    """Tally received the envelope and acted on it; the reply never arrived.

    The inner simulator is called FIRST and its effect is kept, which is the
    whole point: a timeout is not a rollback. Only the answer is lost.
    """

    def __init__(
        self, inner: TallySim, *, drop: Callable[[str, DropsTheReply], bool]
    ) -> None:
        self.inner = inner
        self._drop = drop
        self.sent: list[str] = []
        self.dropped: list[str] = []
        self.imports_seen = 0

    def send(self, payload: str, *, retry: bool) -> str:
        reply = self.inner.send(payload, retry=retry)
        self.sent.append(payload)
        if _is_import(payload):
            self.imports_seen += 1
        if self._drop(payload, self):
            self.dropped.append(payload)
            raise real.TallyUnreachable("the reply never arrived: read timed out")
        return reply


class WedgesTheVoucherExport:
    """Tally still imports, but every voucher export comes back as an error.

    A gateway in this state is not hypothetical - it is the state the live
    instance is in as this is written. The import path and the export path fail
    independently.
    """

    def __init__(self, inner: TallySim) -> None:
        self.inner = inner
        self.wedged = True
        self.substituted = 0
        self.sent: list[str] = []

    def send(self, payload: str, *, retry: bool) -> str:
        reply = self.inner.send(payload, retry=retry)
        self.sent.append(payload)
        if self.wedged and _is_voucher_export(payload):
            self.substituted += 1
            return EXPORT_ERROR
        return reply


def a_simulated_tally(history: int = 0) -> TallySim:
    """The Tally-shaped simulator, optionally with hand-typed vendor history."""
    sim = TallySim()
    sim.add_company(COMPANY, ACCOUNTS)
    for i in range(history):
        sim.seed(
            COMPANY,
            narration=f"cement supply {i}",
            amount_paise=100000 + i,
            debit="Purchases",
            credit="Cash",
            party="Sharma Traders",
        )
    return sim


def sim_client(
    sim: TallySim, transport: real.Transport | None = None
) -> real.RealTally:
    """A RealTally over `sim`, or over a transport that wraps it, with a backup.

    `RecordedBackups` holding this company is the only reason any write below is
    permitted at all; the default is an empty set that refuses everything.
    """
    return real.RealTally(
        transport=transport if transport is not None else sim,
        backups=real.RecordedBackups(frozenset({COMPANY})),
    )


# ---------------------------------------------------------------------------
# 1. a voucher without our marker is not ours, ever
# ---------------------------------------------------------------------------


#: Four narrations that a careless matcher would claim. None of them is the
#: marker: `marker_for("ad_x")` is exactly `[ACCOUNTANT_DAD:ad_x]`.
NEAR_MISSES = (
    "rent paid by hand",
    f"{MARKER_PREFIX}:ad_planted",
    f"[{MARKER_PREFIX} ad_planted]",
    "reversal of ad_planted, see the file note",
)


def test_a_voucher_that_does_not_carry_our_marker_is_never_treated_as_ours() -> None:
    """Four hand-typed near misses, none of which yields an operation id."""
    assert [operation_id_in(n) for n in NEAR_MISSES] == [None, None, None, None]


def test_a_bulk_reverse_removes_only_our_own_vouchers_and_restores_the_balance() -> (
    None
):
    """The differential is the whole test: register, our-view, trial balance.

    Reversing "everything we wrote" against a book that also holds hand-typed
    entries is the one operation that can delete somebody else's work. So the
    hand-typed rows are checked to be byte-identical afterwards, not merely
    present in the same number.
    """
    t = tally()
    for i, narration in enumerate(NEAR_MISSES):
        t.seed_voucher(
            COMPANY,
            Voucher(
                id=f"human-{i}",
                date=TODAY,
                party="Verma Properties",
                narration=narration,
                debit_account="Sundry Expenses",
                credit_account="Cash",
                amount_paise=50000 + i,
            ),
        )
    client = RecordingTally(t)
    theirs_before = client.read_vouchers(COMPANY)
    balance_before = client.trial_balance(COMPANY)

    ops = [new_operation_id() for _ in range(3)]
    for i, op in enumerate(ops):
        client.write_voucher(COMPANY, contract.a_voucher(amount_paise=1000 + i), op)

    assert client.write_count == 3
    assert len(client.read_vouchers(COMPANY)) == 7
    assert len(client.list_our_vouchers(COMPANY)) == 3
    assert client.trial_balance(COMPANY) != balance_before

    swept = [operation_id_in(v.narration) for v in client.list_our_vouchers(COMPANY)]
    assert swept == ops
    for op in ops:
        assert op is not None
        assert client.reverse_by_operation_id(COMPANY, op) is True

    assert client.read_vouchers(COMPANY) == theirs_before
    assert client.list_our_vouchers(COMPANY) == ()
    assert client.trial_balance(COMPANY) == balance_before
    assert client.reversals == [(COMPANY, op) for op in ops]


def test_reversing_an_operation_id_that_only_appears_as_plain_text_finds_nothing() -> (
    None
):
    """The near miss that reads most like a hit: our id, written out in prose."""
    t = tally()
    t.seed_voucher(
        COMPANY,
        Voucher(
            id="human-note",
            date=TODAY,
            party="Verma Properties",
            narration="reversal of ad_planted, see the file note",
            debit_account="Sundry Expenses",
            credit_account="Cash",
            amount_paise=50000,
        ),
    )
    client = RecordingTally(t)
    before = client.trial_balance(COMPANY)

    assert client.reverse_by_operation_id(COMPANY, "ad_planted") is False
    assert client.read_by_operation_id(COMPANY, "ad_planted") is None
    assert len(client.read_vouchers(COMPANY)) == 1
    assert client.trial_balance(COMPANY) == before


# ---------------------------------------------------------------------------
# 2. the marker is right and the voucher behind it is not
# ---------------------------------------------------------------------------


WRONG_ONE = Voucher(
    id="someone-elses",
    date=datetime.date(2026, 8, 31),
    party="Verma Properties",
    narration="rent for August",
    debit_account="Rent",
    credit_account="Bank",
    amount_paise=2_000_000,
    tally_id="TALLY-99",
)


def _posted_against_a_swapped_read_back() -> tuple[
    SwapsTheReadBack, pipeline.Draft, MemoryStore
]:
    t = tally(past())
    store = MemoryStore(":memory:")
    memory = memory_for(t, store)
    client = SwapsTheReadBack(t, substitute=WRONG_ONE)
    return client, valid_draft(client, memory), store


def test_a_read_back_with_our_marker_but_not_our_numbers_is_refused() -> None:
    """W1, FIXED 2026-08-09. This test pinned the DEFECT until then.

    Tally hands back 2,000,000 paise of rent to Verma Properties on the 31st.
    We sent 420,000 paise of cement to Sharma Traders on the 7th. The marker
    matches; nothing else does.

    Before the fix `post` checked the read-back for `is None` and nothing else,
    reported success, and recorded a posted row. It checked the label on the
    box and never opened it. The message must now NAME every field that
    differs, because "something is wrong" sends a person looking through their
    whole ledger and "the amount and the party are wrong" does not.
    """
    client, draft, store = _posted_against_a_swapped_read_back()

    with pytest.raises(RuntimeError) as raised:
        pipeline.post(draft, client)

    said = str(raised.value)
    assert "read back a DIFFERENT voucher" in said
    for field in ("amount_paise", "party", "date"):
        assert field in said, f"the refusal does not name {field}"
    assert "420000" in said and "2000000" in said
    assert "Sharma Traders" in said and "Verma Properties" in said

    # pytest.raises is never the whole proof. The state has to be clean too.
    assert draft.posted_tally_id is None
    assert client.write_count == 1, "the write happened; only the CLAIM is refused"
    assert store.actions(COMPANY) == (), "nothing is recorded as posted"

    # The entry may genuinely exist in Tally - the refusal says so, and says it
    # must be checked by hand. That is the honest state, not a clean rollback.
    assert client.read_by_operation_id(COMPANY, draft.operation_id) is not None
    assert client.reverse_by_operation_id(COMPANY, draft.operation_id) is True


def test_a_read_back_must_match_the_voucher_we_sent_and_not_merely_our_marker() -> None:
    """Voucher identity has to come from Tally, not from our own marker."""
    client, draft, _store = _posted_against_a_swapped_read_back()

    with pytest.raises((RuntimeError, ValueError)):
        pipeline.post(draft, client)

    assert draft.posted_tally_id is None


# ---------------------------------------------------------------------------
# 3. the same operation id, posted twice
# ---------------------------------------------------------------------------


def test_the_same_operation_posted_twice_leaves_exactly_one_voucher() -> None:
    """The register count is the claim. `pytest.raises` alone proves nothing.

    Two write attempts are recorded by the double, one voucher exists, and the
    trial balance after the refused retry is the same dict it was before it.
    """
    t = tally(past())
    store = MemoryStore(":memory:")
    memory = memory_for(t, store)
    client = RecordingTally(t)
    draft = valid_draft(client, memory)

    posted = pipeline.post(draft, client)
    pipeline.record_decision(store, posted, memory, client, "posted", RUN)
    after_first = client.trial_balance(COMPANY)
    ours_after_first = client.list_our_vouchers(COMPANY)

    with pytest.raises(DuplicateOperation) as raised:
        pipeline.post(draft, client)

    assert draft.operation_id in str(raised.value)
    assert "already written" in str(raised.value)

    assert client.write_count == 2, "the double was asked to write twice"
    assert len(client.list_our_vouchers(COMPANY)) == 1, "and it wrote once"
    assert client.list_our_vouchers(COMPANY) == ours_after_first
    assert len(client.read_vouchers(COMPANY)) == 41  # 40 history + 1 of ours
    assert client.trial_balance(COMPANY) == after_first

    row = only_row(store)
    assert (row.action, row.outcome, row.run_id) == ("posted", "valid", RUN)
    assert row.backend == "RecordingTally"
    assert row.voucher_id == posted.posted_tally_id
    assert row.reason == posted.reason

    assert client.reverse_by_operation_id(COMPANY, draft.operation_id) is True
    assert client.list_our_vouchers(COMPANY) == ()


# ---------------------------------------------------------------------------
# 4. the reply timed out and Tally had already done the work
# ---------------------------------------------------------------------------


def test_a_write_replayed_after_a_timeout_creates_no_second_voucher() -> None:
    """The C5 scenario in its real shape, against the real connector.

    Tally imports the voucher and the reply is lost on the way back. The caller
    cannot tell that from "nothing happened", so it retries with the SAME
    operation id. The duplicate guard reads the marker, finds the first write
    and refuses. One voucher, and the trial balance moved exactly once.
    """
    sim = a_simulated_tally()
    transport = DropsTheReply(
        sim, drop=lambda payload, t: _is_import(payload) and t.imports_seen == 1
    )
    client = sim_client(sim, transport)
    op = new_operation_id()

    with pytest.raises(real.TallyUnreachable) as timed_out:
        client.write_voucher(COMPANY, contract.a_voucher(), op)
    assert "never arrived" in str(timed_out.value)
    assert len(transport.dropped) == 1

    # Tally really did the work. This is the fact the caller cannot see.
    assert len(sim.companies[COMPANY].vouchers) == 1
    after_the_lost_reply = client.trial_balance(COMPANY)

    with pytest.raises(DuplicateOperation) as retried:
        client.write_voucher(COMPANY, contract.a_voucher(), op)
    assert op in str(retried.value)

    assert transport.imports_seen == 1, "the retry never reached the import"
    assert len(sim.companies[COMPANY].vouchers) == 1
    assert len(client.read_vouchers(COMPANY)) == 1
    assert len(client.list_our_vouchers(COMPANY)) == 1
    assert operation_id_in(client.list_our_vouchers(COMPANY)[0].narration) == op
    assert client.trial_balance(COMPANY) == after_the_lost_reply

    assert client.reverse_by_operation_id(COMPANY, op) is True
    assert client.read_vouchers(COMPANY) == ()
    assert client.trial_balance(COMPANY) == {}


# ---------------------------------------------------------------------------
# 5. a malformed, partial or empty response
# ---------------------------------------------------------------------------


LEDGER_LIST = (
    "<ENVELOPE><BODY><DATA><COLLECTION>"
    + "".join(f'<LEDGER NAME="{a}"></LEDGER>' for a in ACCOUNTS)
    + "</COLLECTION></DATA></BODY></ENVELOPE>"
)


def answering_the_voucher_export_with(body: str) -> tuple[real.RealTally, list[str]]:
    """A RealTally whose ledger reads work and whose voucher reads return `body`.

    Returns the envelopes that went out as well, because "the write was refused"
    and "the write was refused BEFORE anything was imported" are different
    claims and only the second one is worth anything.
    """
    sent: list[str] = []

    class Scripted:
        def send(self, payload: str, *, retry: bool) -> str:
            del retry
            sent.append(payload)
            return body if _is_voucher_export(payload) else LEDGER_LIST

    client = real.RealTally(
        transport=Scripted(), backups=real.RecordedBackups(frozenset({COMPANY}))
    )
    return client, sent


@pytest.mark.parametrize(
    "body",
    [
        "",
        "   \n\t ",
        "<ENVELOPE><BODY><DATA><COLLECTION><VOUCHER>",
        "Tally is busy, please try later",
    ],
    ids=["empty_body", "whitespace_only", "truncated_xml", "not_xml_at_all"],
)
def test_an_unparseable_response_on_the_write_path_raises_and_never_reads_as_empty(
    body: str,
) -> None:
    """A body we cannot parse must not become "this company has no vouchers".

    Driven through `write_voucher`, so the assertion is about the WRITE path:
    the duplicate check is the first read a write makes, and a silent `()` there
    is the difference between refusing a duplicate and creating one.
    """
    client, sent = answering_the_voucher_export_with(body)

    with pytest.raises(real.TallyResponseError):
        client.write_voucher(COMPANY, contract.a_voucher(), "ad_probe")

    assert sent, "the transport was never called, so nothing was proved"
    assert not any(_is_import(out) for out in sent), (
        "the write must be refused before anything is imported"
    )


def test_a_voucher_missing_its_ledger_entries_is_an_unreadable_export() -> None:
    """A partial voucher - every field but the legs - must raise, not skip."""
    partial = (
        "<ENVELOPE><BODY><DATA><COLLECTION>"
        '<VOUCHER MASTERID="M1" VCHTYPE="Journal">'
        "<DATE>20260807</DATE>"
        "<NARRATION>cement bags [ACCOUNTANT_DAD:ad_7]</NARRATION>"
        "</VOUCHER>"
        "</COLLECTION></DATA></BODY></ENVELOPE>"
    )
    with pytest.raises(real.TallyDataError, match="no ledger entries at all"):
        real.parse_vouchers(partial)


def test_an_error_envelope_from_the_voucher_export_reads_as_an_empty_company() -> None:
    """MEASURED, AND IT IS DEFECT D3 - accountant/tallyio/real.py:1230.

    The envelope is well formed, carries a `<LINEERROR>` and no vouchers.
    `_voucher_nodes` finds a DATA block with nothing in it, so the page is
    empty and no exception is raised. "Tally refused the request" and "this
    company has no entries" become the same answer.
    """
    page = real.parse_vouchers(EXPORT_ERROR)

    assert page.exported == ()
    assert page.skipped == 0


@pytest.mark.xfail(
    strict=True,
    reason=(
        "DEFECT D3, accountant/tallyio/real.py:1230. parse_vouchers returns an "
        "empty page for a response carrying <LINEERROR>, so a refused export is "
        "indistinguishable from an empty company."
    ),
)
def test_an_error_envelope_is_refused_rather_than_read_as_zero_vouchers() -> None:
    with pytest.raises(real.TallyError):
        real.parse_vouchers(EXPORT_ERROR)


def test_a_wedged_voucher_export_lets_one_operation_id_write_two_vouchers() -> None:
    """MEASURED, AND IT IS THE COST OF D3. This is the duplicate in the books.

    The import path works; the export path answers with an error envelope. Every
    read the write path makes therefore says "no vouchers":

      * the C5 duplicate check sees none, so it does not refuse;
      * the C6 read-back sees none, so the write raises `TallyRejected` and the
        caller is told the voucher does not exist.

    Both are wrong in the same direction. Run it twice with the SAME operation
    id and the company ends up holding two identical statutory entries, while
    every layer above reported failure both times. Worse, the two now share one
    marker, so the automatic reversal refuses to touch either of them and a
    person has to go in and decide which is real.
    """
    sim = a_simulated_tally()
    transport = WedgesTheVoucherExport(sim)
    client = sim_client(sim, transport)
    op = new_operation_id()

    for _ in range(2):
        with pytest.raises(real.TallyRejected, match="whatever HTTP said"):
            client.write_voucher(COMPANY, contract.a_voucher(), op)

    assert transport.substituted == 4, "two reads per write, all four wedged"

    # Unwedge, and look at the books the person actually owns.
    transport.wedged = False
    register = client.read_vouchers(COMPANY)
    assert len(register) == 2
    assert [operation_id_in(v.narration) for v in register] == [op, op]
    assert client.trial_balance(COMPANY) == {"Purchases": 236000, "Cash": -236000}

    # And now nothing can clean it up automatically.
    with pytest.raises(real.TallyDataError, match="matches 2 vouchers"):
        client.reverse_by_operation_id(COMPANY, op)
    assert len(client.read_vouchers(COMPANY)) == 2


# ---------------------------------------------------------------------------
# 6. the connection drops after the write and before the read-back
# ---------------------------------------------------------------------------


def test_a_disconnect_after_the_write_hides_a_voucher_that_really_exists() -> None:
    """The nastiest one, against the real connector.

    The import succeeded. The read-back that would prove it never got an answer.
    `write_voucher` raises, so no `WriteResult` exists and nothing downstream can
    record a tally id - correct. What is NOT correct is what the books look like
    afterwards: the voucher is there, and the only thing that knows its
    operation id is a Python traceback.
    """
    sim = a_simulated_tally()
    transport = DropsTheReply(
        sim,
        # The FIRST voucher export after the import, and only that one: it is
        # the read-back. Later reads are the person coming back to look.
        drop=lambda payload, t: (
            _is_voucher_export(payload) and t.imports_seen == 1 and not t.dropped
        ),
    )
    client = sim_client(sim, transport)
    op = new_operation_id()

    with pytest.raises(real.TallyUnreachable):
        client.write_voucher(COMPANY, contract.a_voucher(), op)

    assert transport.imports_seen == 1
    assert len(transport.dropped) == 1

    # The differential: Tally holds it, we never confirmed it.
    assert len(sim.companies[COMPANY].vouchers) == 1
    assert sim.companies[COMPANY].vouchers[0].narration.endswith(marker_for(op))

    # Once the line comes back, the entry is exactly where it always was, and
    # only the marker makes cleanup possible at all.
    assert len(client.read_vouchers(COMPANY)) == 1
    assert client.trial_balance(COMPANY) == {"Purchases": 118000, "Cash": -118000}
    assert client.reverse_by_operation_id(COMPANY, op) is True
    assert client.trial_balance(COMPANY) == {}


def test_a_write_whose_outcome_is_unknown_leaves_two_rows_naming_the_operation() -> (
    None
):
    """DEFECT D2 / W2, FIXED 2026-08-09. This test pinned it until then.

    `run` recorded the decision only AFTER `post` returned. When `post` raised,
    the exception left `run`, `record_decision` was never reached, and the one
    durable trace of a write that went out with an unknown outcome was ZERO
    rows. The books had moved and nobody knew the operation id.

    `post` now writes AHEAD of the socket. Two rows: `write_attempted` before
    anything is sent, `write_outcome_unknown` naming the exception. The pair is
    what makes the voucher findable; the first row alone is what survives a
    process that does not live long enough to write the second.
    """
    t = tally(past())
    store = MemoryStore(":memory:")
    memory = memory_for(t, store)
    client = DropsTheReadBackConnection(t)
    balance_before = client.trial_balance(COMPANY)

    with pytest.raises(ConnectionError, match="dropped the connection"):
        pipeline.run(
            COMPANY,
            ENTRY.encode(),
            "text/plain",
            TypedTextExtractor(),
            client,
            memory,
            today=TODAY,
            log=store,
            run_id=RUN,
        )

    assert client.write_count == 1
    assert len(client.read_backs) == 1

    rows = store.actions(COMPANY)
    assert [r.action for r in rows] == [
        pipeline.WRITE_ATTEMPTED,
        pipeline.WRITE_OUTCOME_UNKNOWN,
    ]
    assert {r.operation_id for r in rows} == {
        operation_id_in(client.list_our_vouchers(COMPANY)[0].narration)
    }, "both rows name the operation id of the voucher actually in the books"
    assert "ConnectionError" in rows[1].reason
    assert rows[1].outcome == pipeline.WRITE_OUTCOME_UNKNOWN, (
        "not 'valid'. A row that says valid is a row somebody reads as posted."
    )
    assert all(r.voucher_id == "" for r in rows), "no tally id is claimed"

    # And the write really did land, so the books moved with nothing written down.
    assert len(client.list_our_vouchers(COMPANY)) == 1
    assert client.trial_balance(COMPANY) != balance_before
    stranded = operation_id_in(client.list_our_vouchers(COMPANY)[0].narration)
    assert stranded is not None
    assert client.reverse_by_operation_id(COMPANY, stranded) is True
    assert client.trial_balance(COMPANY) == balance_before


def test_an_unknown_write_outcome_still_records_its_operation_id() -> None:
    """The aspiration that was a strict xfail until 2026-08-09. It now holds.

    Kept alongside the test above rather than merged into it: that one proves
    the SHAPE of the pair, this one proves the single fact somebody actually
    needs at 9pm - the operation id of a voucher that may be in the books is
    written down, under this run id, in this company's scope.
    """
    t = tally(past())
    store = MemoryStore(":memory:")
    memory = memory_for(t, store)
    client = DropsTheReadBackConnection(t)

    with pytest.raises(ConnectionError):
        pipeline.run(
            COMPANY,
            ENTRY.encode(),
            "text/plain",
            TypedTextExtractor(),
            client,
            memory,
            today=TODAY,
            log=store,
            run_id=RUN,
        )

    rows = store.actions(COMPANY)
    assert len(rows) == 2, "written ahead of the socket, then again on the way out"
    assert {r.operation_id for r in rows} == {client.writes[0][1]}
    assert {r.run_id for r in rows} == {RUN}


def test_the_same_run_call_records_a_row_when_nothing_goes_wrong() -> None:
    """The CONTROL for every `store.actions(COMPANY) == ()` assertion here.

    Identical arguments, identical wiring, a healthy double. Without this, an
    empty log would be equally consistent with "the pipeline never recorded it"
    and "this test passed a store nothing was ever going to write to", and the
    two failures look the same from outside.
    """
    t = tally(past())
    store = MemoryStore(":memory:")
    memory = memory_for(t, store)
    client = RecordingTally(t)

    draft = pipeline.run(
        COMPANY,
        ENTRY.encode(),
        "text/plain",
        TypedTextExtractor(),
        client,
        memory,
        today=TODAY,
        log=store,
        run_id=RUN,
    )

    assert draft.outcome is Outcome.VALID
    assert client.write_count == 1

    # TWO rows on the healthy path since 2026-08-09: the write-ahead row, then
    # the decision. The pair is the point - an attempt with no partner is the
    # signature of an unknown outcome, and that reading only works if the
    # partner reliably appears when the write succeeds.
    rows = store.actions(COMPANY)
    assert [r.action for r in rows] == [pipeline.WRITE_ATTEMPTED, "posted"]
    assert {r.operation_id for r in rows} == {draft.operation_id}
    assert {r.run_id for r in rows} == {RUN}
    assert {r.backend for r in rows} == {"RecordingTally"}

    row = rows[-1]
    assert (row.action, row.outcome) == ("posted", "valid")
    assert row.voucher_id == draft.posted_tally_id


# ---------------------------------------------------------------------------
# 7. the read-back finds nothing
# ---------------------------------------------------------------------------


def test_a_read_back_of_zero_vouchers_blocks_the_post_and_writes_no_posted_row() -> (
    None
):
    """C6 through `run`, with the whole evidence set rather than the exception.

    The existing `tests/test_pipeline.py` test proves `post` raises and records
    no tally id. This one adds the layers that decide whether anybody ever finds
    out: the action log, the run id, the trial balance, and the cleanup.
    """
    t = tally(past())
    store = MemoryStore(":memory:")
    memory = memory_for(t, store)
    client = LosesTheReadBack(t)
    balance_before = client.trial_balance(COMPANY)

    with pytest.raises(RuntimeError) as raised:
        pipeline.run(
            COMPANY,
            ENTRY.encode(),
            "text/plain",
            TypedTextExtractor(),
            client,
            memory,
            today=TODAY,
            log=store,
            run_id=RUN,
        )

    message = str(raised.value)
    assert "could not read it back" in message
    assert message.startswith("wrote operation ad_")

    assert client.write_count == 1
    assert len(client.read_backs) == 1

    # DEFECT D2 again, from the other side, and fixed the same way. No `posted`
    # row - nothing was posted - but the attempt is on the record.
    rows = store.actions(COMPANY)
    assert [r.action for r in rows] == [
        pipeline.WRITE_ATTEMPTED,
        pipeline.WRITE_OUTCOME_UNKNOWN,
    ]
    assert "posted" not in {r.action for r in rows}
    assert "could not read it back" in rows[1].reason

    # The write DID happen inside the fake, which is what makes this expensive.
    assert len(client.list_our_vouchers(COMPANY)) == 1
    assert client.trial_balance(COMPANY) != balance_before

    op = operation_id_in(client.list_our_vouchers(COMPANY)[0].narration)
    assert op is not None
    assert client.reverse_by_operation_id(COMPANY, op) is True
    assert client.trial_balance(COMPANY) == balance_before
    assert client.list_our_vouchers(COMPANY) == ()


# ---------------------------------------------------------------------------
# 8. two candidates for one marker
# ---------------------------------------------------------------------------


def _two_vouchers_one_marker(op: str) -> str:
    body = (
        '<VOUCHER MASTERID="{mid}" VCHTYPE="Journal">'
        "<DATE>20260807</DATE>"
        "<VOUCHERNUMBER>{mid}</VOUCHERNUMBER>"
        "<PARTYLEDGERNAME>Sharma Traders</PARTYLEDGERNAME>"
        f"<NARRATION>cement bags {marker_for(op)}</NARRATION>"
        "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Purchases</LEDGERNAME>"
        "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-1180.00</AMOUNT>"
        "</ALLLEDGERENTRIES.LIST>"
        "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME>"
        "<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>1180.00</AMOUNT>"
        "</ALLLEDGERENTRIES.LIST>"
        "</VOUCHER>"
    )
    return (
        "<ENVELOPE><BODY><DATA><COLLECTION>"
        + body.format(mid="M11")
        + body.format(mid="M12")
        + "</COLLECTION></DATA></BODY></ENVELOPE>"
    )


def test_an_ambiguous_marker_stops_the_pipeline_instead_of_picking_one() -> None:
    """Through `pipeline.post`, so the claim is about the product, not the parser.

    `tests/test_real_tally.py` proves `RealTally` itself refuses. What is
    asserted here is that the refusal survives the layer above it: the pipeline
    does not catch it, does not fall back, and records no posted id.
    """
    op = "ad_ambiguous_probe"
    client, sent = answering_the_voucher_export_with(_two_vouchers_one_marker(op))
    draft = pipeline.Draft(
        id="draft-ambiguous",
        company=COMPANY,
        voucher=contract.a_voucher(),
        record=TypedTextExtractor().extract(ENTRY.encode(), "text/plain"),
        operation_id=op,
        decision=Decision(outcome=Outcome.VALID, reason="nothing unclear"),
    )
    assert draft.outcome is Outcome.VALID

    with pytest.raises(real.TallyDataError, match="matches 2 vouchers"):
        pipeline.post(draft, client)

    assert draft.posted_tally_id is None
    assert not any(_is_import(out) for out in sent), "nothing was written"
    with pytest.raises(real.TallyDataError, match="a person has to decide"):
        pipeline.reverse(draft, client)


def test_the_in_memory_double_refuses_the_ambiguity_and_names_both_vouchers() -> None:
    """W4/D4, FIXED 2026-08-09. This test pinned the DEFECT until then.

    Before the fix `FakeTally` returned the FIRST of the two matches and its
    reverse DELETED the first, leaving the twin behind and the trial balance
    holding 250000 where 368000 belonged - while `RealTally` refused
    (real.py:1797). `tests/test_tally_contract.py` holds both backends to one
    contract and this property was not in it, so no contract test could see the
    divergence, and a test written against the fake could "prove" an ambiguity
    was handled when it was not.

    Two things here that the both-backends tests below do NOT cover, which is
    why this one stayed rather than being folded into them:

      * the refusal survives `RecordingTally`, the wrapper every other case in
        this file goes through;
      * the message NAMES both vouchers and both amounts. "There are two" sends
        a person hunting through a whole ledger; naming them does not, and the
        fake has locators of its own to name.
    """
    t = tally()
    op = "ad_ambiguous_probe"
    for i, amount in enumerate((118000, 250000)):
        t.seed_voucher(
            COMPANY,
            Voucher(
                id=f"dup-{i}",
                date=TODAY,
                party="Sharma Traders",
                narration=f"cement bags {marker_for(op)}",
                debit_account="Purchases",
                credit_account="Cash",
                amount_paise=amount,
            ),
        )
    client = RecordingTally(t)

    with pytest.raises(real.TallyDataError) as raised:
        client.read_by_operation_id(COMPANY, op)

    said = str(raised.value)
    for named in ("dup-0", "dup-1", "118000", "250000"):
        assert named in said, f"the refusal does not name {named}: {said}"

    with pytest.raises(real.TallyDataError):
        client.reverse_by_operation_id(COMPANY, op)

    # pytest.raises is never the whole proof. Both survived, to the paise.
    assert len(client.list_our_vouchers(COMPANY)) == 2
    assert client.trial_balance(COMPANY) == {"Purchases": 368000, "Cash": -368000}


# ---- the same decision from both backends ----------------------------------
#
# WHY THESE ARE NOT IN tests/test_tally_contract.py, WHERE THEY BELONG BY SUBJECT
#
# "a marker that matches two vouchers is refused" is a property of every
# TallyClient, so the shared contract is its natural home. Two reasons it is
# here instead:
#
#   1. That file is FROZEN at its 2026-08-07 fixture (owner decision, and the
#      Educational-mode exception recorded with it). Nothing may be added to it.
#   2. Freezing it did not cost much, because per accountant/tallyio/real.py:26-28
#      the 2026-08-07 date that fixture posts on is one Educational-mode Tally
#      MEASURABLY REFUSES, so the contract file cannot run against RealTally
#      unmodified anyway. A case added there would be a FakeTally-only test
#      wearing a contract's name - which is the precise shape of W4 itself.
#
# So the both-backends cases live here and drive both backends by hand: the fake
# directly, and RealTally over `TallySim` so the whole XML read path runs. The
# transports differ; the safety decision must not.
#
# SCOPE. These cover the marker-COUNT rows of the ladder - zero, one, two. The
# other rows are pinned elsewhere and are not reproved here: wrong identity by
# `test_a_read_back_with_our_marker_but_not_our_numbers_is_refused` above,
# malformed and unknown-outcome by section 3 and by `tests/test_real_tally.py`.

#: A backend, plus the only backend-specific thing these tests need: a way to
#: plant a voucher that already carries a given marker, as a duplicate-entry
#: feature or a second copy of a company file would.
Backend = tuple[TallyClient, Callable[[str, int], None]]


def a_fake_backend() -> Backend:
    t = tally()

    def plant(op: str, amount_paise: int) -> None:
        t.seed_voucher(
            COMPANY,
            Voucher(
                id=f"planted-{amount_paise}",
                date=TODAY,
                party="Sharma Traders",
                narration=f"cement bags {marker_for(op)}",
                debit_account="Purchases",
                credit_account="Cash",
                amount_paise=amount_paise,
            ),
        )

    return t, plant


def a_real_backend() -> Backend:
    sim = a_simulated_tally()

    def plant(op: str, amount_paise: int) -> None:
        sim.seed(
            COMPANY,
            narration=f"cement bags {marker_for(op)}",
            amount_paise=amount_paise,
            debit="Purchases",
            credit="Cash",
            party="Sharma Traders",
        )

    return sim_client(sim), plant


#: Both implementations of `TallyClient` this repository has. The ids are the
#: backend names, so a failure says WHICH backend broke the agreement.
BOTH_BACKENDS = pytest.mark.parametrize(
    "make_backend",
    [
        pytest.param(a_fake_backend, id="FakeTally"),
        pytest.param(a_real_backend, id="RealTally-over-TallySim"),
    ],
)


@BOTH_BACKENDS
def test_both_backends_refuse_to_choose_between_two_vouchers_sharing_one_marker(
    make_backend: Callable[[], Backend],
) -> None:
    """W4. The read refuses, names the count, and leaves both vouchers alone.

    Two vouchers, one marker, DIFFERENT amounts - so "it picked one" and "it
    refused" cannot be confused with each other. `RealTally` has always refused
    (real.py:1797). `FakeTally` returned the first match until 2026-08-09, and
    no contract test could see the difference.
    """
    client, plant = make_backend()
    op = "ad_ambiguous_probe"
    plant(op, 118000)
    plant(op, 250000)
    before = client.trial_balance(COMPANY)

    with pytest.raises(real.TallyDataError) as raised:
        client.read_by_operation_id(COMPANY, op)

    said = str(raised.value)
    assert "matches 2 vouchers" in said, said
    assert op in said, "the refusal must name the operation it is about"
    assert "a person has to decide" in said, said

    # pytest.raises is never the whole proof. Neither voucher was chosen, and
    # neither was touched.
    ours = client.list_our_vouchers(COMPANY)
    assert len(ours) == 2
    assert sorted(v.amount_paise for v in ours) == [118000, 250000]
    assert client.trial_balance(COMPANY) == before


@BOTH_BACKENDS
def test_both_backends_refuse_to_reverse_either_of_two_vouchers_sharing_one_marker(
    make_backend: Callable[[], Backend],
) -> None:
    """The destructive half of W4, and the expensive one.

    A read that picks wrong shows somebody the wrong number. A reverse that
    picks wrong DELETES a statutory entry and leaves its twin behind, so the
    books are now wrong in a way no later read can detect.
    """
    client, plant = make_backend()
    op = "ad_ambiguous_probe"
    plant(op, 118000)
    plant(op, 250000)
    before = client.trial_balance(COMPANY)

    with pytest.raises(real.TallyDataError, match="a person has to decide"):
        client.reverse_by_operation_id(COMPANY, op)

    assert len(client.list_our_vouchers(COMPANY)) == 2, "nothing may be deleted"
    assert client.trial_balance(COMPANY) == before


@BOTH_BACKENDS
def test_both_backends_answer_a_marker_the_same_way_at_zero_one_and_two_matches(
    make_backend: Callable[[], Backend],
) -> None:
    """The whole count ladder in one place, so the two ends anchor the middle.

    Without the zero and one rows, a backend that refused EVERY read would pass
    the ambiguity test above while being useless. The refusal has to be aimed at
    the ambiguity and at nothing else.
    """
    client, plant = make_backend()
    op = "ad_ambiguous_probe"

    # zero matches: not found, and nothing to reverse. Not an error.
    assert client.read_by_operation_id(COMPANY, op) is None
    assert client.reverse_by_operation_id(COMPANY, op) is False

    # one match: the voucher itself, and it is the one that was planted.
    plant(op, 118000)
    found = client.read_by_operation_id(COMPANY, op)
    assert found is not None
    assert found.amount_paise == 118000

    # two matches: refused, both times, with nothing deleted in between.
    plant(op, 250000)
    before = client.trial_balance(COMPANY)
    with pytest.raises(real.TallyDataError, match="matches 2 vouchers"):
        client.read_by_operation_id(COMPANY, op)
    with pytest.raises(real.TallyDataError, match="matches 2 vouchers"):
        client.reverse_by_operation_id(COMPANY, op)
    assert len(client.list_our_vouchers(COMPANY)) == 2
    assert client.trial_balance(COMPANY) == before


# ---------------------------------------------------------------------------
# 9. Tally says yes and its own register does not agree
# ---------------------------------------------------------------------------


def test_a_write_absent_from_tallys_own_register_is_refused() -> None:
    """W1's second face, FIXED 2026-08-09. This test pinned the DEFECT until then.

    Our marker filter says the voucher is there. Tally's own voucher list and
    its own trial balance say it is not. Before the fix `post` consulted only
    the filter and reported success against a register that never moved.

    G3 in `tests/test_tally_contract.py` states the requirement - "the posted
    voucher shows up in Tally's OWN report, not only in the view our marker
    filter produces" - and proved it of the CLIENT. Nothing enforced it on the
    PIPELINE, which is the layer that decides whether to tell the person yes.
    Now it does, so the phase's headline claim is true in the code that runs.
    """
    t = tally(past())
    store = MemoryStore(":memory:")
    memory = memory_for(t, store)
    client = HidesItFromTheRegister(t)
    register_before = client.read_vouchers(COMPANY)
    balance_before = client.trial_balance(COMPANY)

    draft = valid_draft(client, memory)

    with pytest.raises(RuntimeError) as raised:
        pipeline.post(draft, client)

    said = str(raised.value)
    assert "NOT in Tally's own register" in said
    assert draft.operation_id in said

    # The differential, all three views of the same fact - and now the pipeline
    # believes the register rather than its own filter.
    assert client.read_by_operation_id(COMPANY, draft.operation_id) is not None
    assert client.read_vouchers(COMPANY) == register_before
    assert client.list_our_vouchers(COMPANY) == ()
    assert client.trial_balance(COMPANY) == balance_before

    assert draft.posted_tally_id is None
    assert store.actions(COMPANY) == (), "nothing is recorded as posted"


def test_a_post_is_not_a_success_until_tallys_own_register_shows_the_voucher() -> None:
    t = tally(past())
    store = MemoryStore(":memory:")
    memory = memory_for(t, store)
    client = HidesItFromTheRegister(t)
    draft = valid_draft(client, memory)

    with pytest.raises((RuntimeError, ValueError)):
        pipeline.post(draft, client)

    assert draft.posted_tally_id is None


# ---------------------------------------------------------------------------
# 10. a hidden write call site, and the structural guard that must catch it
# ---------------------------------------------------------------------------


def hidden_writer(client: TallyClient, draft: pipeline.Draft) -> WriteResult:
    """A caller that writes without going anywhere near the Valid gate.

    Deliberately written as a module-level function so its source can be read
    back with `inspect.getsource` and fed to the scanner in
    `tests/test_runtime_backend.py`. If this lived inside `accountant/` the
    guard there would fail; here it is the specimen the guard is tested on.
    """
    return client.write_voucher(draft.company, draft.voucher, draft.operation_id)


def test_a_caller_that_skips_the_valid_gate_writes_an_entry_the_pipeline_refuses() -> (
    None
):
    """The gate is in `pipeline.post` and nowhere else, so bypassing it works.

    Both halves are asserted from the double's own record. `pipeline.post`
    refuses and the write count stays at zero; the hidden caller succeeds and
    the write count goes to one, with an UNCLEAR draft carrying NO debit
    account. That is the entry a person would find in their books.
    """
    t = tally(past())
    store = MemoryStore(":memory:")
    memory = memory_for(t, store)
    client = RecordingTally(t)
    draft = unclear_draft(client, memory)
    assert draft.voucher.debit_account == "", "an unseen vendor proposes nothing"

    # The funding leg is set HERE rather than left to whatever the pipeline
    # happens to fill in. An unseen vendor raises two problems now -
    # `which_account` and `funding_is_named` - so an unanswered draft can carry
    # NEITHER leg, and a voucher posted with ""/"" moves account "" up and down
    # by the same amount. It nets to zero, `trial_balance` drops the zero, and
    # the books look untouched by a write that really happened. This test would
    # then pass while demonstrating the opposite of its claim.
    #
    # The state built here is reachable and still UNCLEAR: the person has said
    # how it was paid and has not yet said what it was for. `post` therefore
    # still refuses, and the entry the hidden caller lands in somebody's books
    # has a real credit leg and no debit leg - which is the damage worth
    # showing.
    draft.voucher = replace(draft.voucher, credit_account="Cash")
    assert draft.voucher.debit_account == ""
    assert draft.voucher.credit_account == "Cash"
    assert draft.outcome is Outcome.UNCLEAR, "still not postable, and that is the point"

    balance_before = client.trial_balance(COMPANY)

    with pytest.raises(ValueError, match="refusing to post: outcome is unclear"):
        pipeline.post(draft, client)
    assert client.write_count == 0
    assert client.trial_balance(COMPANY) == balance_before
    assert client.list_our_vouchers(COMPANY) == ()

    written = hidden_writer(client, draft)

    assert client.write_count == 1
    assert written.operation_id == draft.operation_id
    assert len(client.list_our_vouchers(COMPANY)) == 1
    assert client.list_our_vouchers(COMPANY)[0].debit_account == ""
    assert client.trial_balance(COMPANY) != balance_before
    assert store.actions(COMPANY) == (), "and nothing recorded that it happened"

    assert client.reverse_by_operation_id(COMPANY, draft.operation_id) is True
    assert client.trial_balance(COMPANY) == balance_before


def test_the_structural_guard_locates_the_hidden_write_by_file_line_and_scope() -> None:
    """`tests/test_runtime_backend.py`'s scanner, aimed at this exact specimen.

    Its own parametrized cases are four hand-written snippets. This one is the
    real function above, read off disk, so what is proved is that the guard
    catches THE bypass this file constructed rather than a snippet shaped like
    one.
    """
    source = inspect.getsource(hidden_writer)
    sites = call_sites(source, HIDDEN_PATH)

    assert len(sites) == 1
    assert sites[0].scope == "hidden_writer"
    assert sites[0].path == HIDDEN_PATH
    assert "write_voucher" in source.splitlines()[sites[0].line - 1]


def test_the_guard_would_fail_if_the_hidden_write_lived_inside_the_package() -> None:
    """The count and the identity check both break, and the message says where.

    Asserted against the LIVE scan, so this cannot pass on a stale copy of the
    repository: the one permitted door is found first, and the specimen is what
    turns a passing guard into a failing one.
    """
    real_sites = sites_in_the_package()
    assert [(s.path, s.scope) for s in real_sites] == [guard.THE_ONE_DOOR]

    would_be = real_sites + call_sites(inspect.getsource(hidden_writer), HIDDEN_PATH)

    assert len(would_be) == 2
    assert [(s.path, s.scope) for s in would_be] != [guard.THE_ONE_DOOR]
    assert HIDDEN_PATH in describe_sites(would_be)
    assert "in hidden_writer" in describe_sites(would_be)


def test_the_real_connector_refuses_the_voucher_the_double_accepted() -> None:
    """The doubles do not agree about invalid input. STILL OPEN.

    D4 was the doubles disagreeing about an ambiguous MARKER, and that half is
    fixed (section 8). This is the same shape on a different input and it has
    NOT been fixed: `FakeTally` wrote a voucher with an empty debit account in
    the test above, and `RealTally` refuses the same voucher before anything
    reaches the wire. A bypass test written against the fake alone would
    therefore under-report the damage, and a fix validated against the fake
    alone would not be validated at all.

    Left as measured rather than closed here, because making the fake check its
    chart of accounts changes what `write_voucher` accepts across the suite -
    a decision with its own blast radius, not a rider on the marker fix.

    Measured: the refusal comes from `_check_ledgers_exist`
    (`accountant/tallyio/real.py:1836`), which runs before
    `_check_writable`'s "it needs both a debit and a credit account" is ever
    reached - an empty ledger name is simply not in the chart of accounts. Both
    are refusals; the assertion names the one that actually fires rather than
    the one that reads best.
    """
    sim = a_simulated_tally()
    client = sim_client(sim)
    broken = replace(contract.a_voucher(), debit_account="")

    with pytest.raises(real.TallyDataError, match="refusing to write operation"):
        client.write_voucher(COMPANY, broken, new_operation_id())

    assert sim.companies[COMPANY].vouchers == []
    assert not any(_is_import(out) for out in sim.sent)


# ---------------------------------------------------------------------------
# 11. a fake identity inside the live runtime
# ---------------------------------------------------------------------------


@pytest.fixture
def live_app() -> Iterator[Callable[[TallyClient, str], MemoryStore]]:
    """Install a runtime into `accountant.web.app`, and always take it out again.

    The app is module-global by design (one process, one company), so a test
    that forgets to disconnect poisons every test after it.
    """
    installed: list[MemoryStore] = []

    def install(client: TallyClient, backend: str) -> MemoryStore:
        store = MemoryStore(":memory:")
        identity = BackendIdentity(
            backend=backend,
            endpoint="memory://tests/test_adversarial_write_path.py",
            company=app.COMPANY,
            company_exists=True,
            companies_visible=1,
            run_id=RUN,
        )
        app.configure(client, identity, store=store)
        installed.append(store)
        return store

    app.DRAFTS.clear()
    try:
        yield install
    finally:
        app.disconnect()
        app.DRAFTS.clear()


def app_company_tally() -> FakeTally:
    t = FakeTally()
    t.add_company(
        app.COMPANY,
        accounts=ACCOUNTS,
        vouchers=tuple(replace(v, id=f"app-{v.id}") for v in past(n=12)),
        backed_up=True,
    )
    return t


def test_a_fake_backend_is_labelled_not_real_on_the_page_and_in_health(
    live_app: Callable[[TallyClient, str], MemoryStore],
) -> None:
    """It must never be presented as a real Tally, on either surface.

    Asserted on `data-backend-state`, which appears exactly once in the
    document, and on exact counts. Searching the page for a common word is how
    two tests earlier in this project came out green and vacuous.
    """
    client = RecordingTally(app_company_tally())
    live_app(client, "FakeTally")

    assert app.backend_state() == app.BACKEND_NOT_REAL

    body = app.render_home().decode()
    assert body.count('data-backend-state="not-real"') == 1
    assert body.count('data-backend-state="real-ok"') == 0
    assert body.count('data-backend-state="real-licence-unknown"') == 0
    assert body.count('data-backend-state="real-practice"') == 0
    assert body.count("Not real accounting software") == 1
    assert body.count("Nothing here reaches any real books") == 1

    state = app.health()
    assert state["backend"] == "FakeTally"
    assert state["backend_state"] == "not-real"
    assert state["ready"] is True
    assert state["run_id"] == RUN


def test_the_page_and_the_action_log_can_disagree_about_which_tally_was_written_to(
    live_app: Callable[[TallyClient, str], MemoryStore],
) -> None:
    """MEASURED, AND IT IS DEFECT D5 - accountant/web/app.py:243.

    `backend_state()` reads the DECLARED `identity.backend`. `record_decision`
    writes `type(client).__name__`. Nothing compares them, so a runtime built
    with a real-sounding identity and a fake client tells the person on screen
    that this is their real Tally while every log row says otherwise. Both
    cannot be right, and the one the person reads is the wrong one.
    """
    client = RecordingTally(app_company_tally())
    store = live_app(client, "RealTally")

    assert app.backend_state() == app.BACKEND_LICENCE_UNKNOWN
    body = app.render_home().decode()
    assert body.count('data-backend-state="real-licence-unknown"') == 1
    assert body.count("This is your real Tally") == 1
    assert body.count('data-backend-state="not-real"') == 0

    draft = pipeline.build_draft(
        app.COMPANY,
        ENTRY.encode(),
        "text/plain",
        TypedTextExtractor(),
        client.read_accounts(app.COMPANY),
        app.runtime().memory,
        today=TODAY,
    )
    draft = pipeline.evaluate(
        draft,
        client.read_accounts(app.COMPANY),
        client.read_vouchers(app.COMPANY),
        app.runtime().memory,
    )
    assert draft.outcome is Outcome.VALID
    draft = pipeline.post(draft, client)
    app.record(draft, "posted")

    rows = store.actions(app.COMPANY)
    assert len(rows) == 1
    assert rows[0].backend == "RecordingTally"
    assert rows[0].run_id == RUN
    assert app.runtime().identity.backend == "RealTally"
    assert rows[0].backend != app.runtime().identity.backend

    assert client.write_count == 1
    assert client.reverse_by_operation_id(app.COMPANY, draft.operation_id) is True


@pytest.mark.xfail(
    strict=True,
    reason=(
        "DEFECT D5, accountant/web/app.py:243. `configure()` accepts any "
        "identity for any client, so the backend the page names and the backend "
        "the action log names can be different things."
    ),
)
def test_the_runtime_refuses_an_identity_that_contradicts_the_client_it_names(
    live_app: Callable[[TallyClient, str], MemoryStore],
) -> None:
    client = RecordingTally(app_company_tally())

    with pytest.raises((ValueError, RuntimeError)):
        live_app(client, "RealTally")


def test_the_recorded_identifier_is_the_one_tally_returned_not_our_own() -> None:
    """A SURVIVING MUTANT found this gap in the W1 fix itself.

    Replacing `draft.posted_tally_id = back.tally_id` with a constant of our own
    left the whole suite green. Nothing asserted WHOSE identifier gets recorded.

    It matters because the identifier is what a person uses to find the entry in
    Tally afterwards. `WriteResult.tally_id` is what our own client believed it
    created; `back.tally_id` is what Tally says it stored. Only the second is
    evidence, and when they disagree the first is the one that sends somebody
    hunting for a voucher that is not there.
    """
    t = tally(past())
    store = MemoryStore(":memory:")
    memory = memory_for(t, store)
    draft = valid_draft(t, memory)

    posted = pipeline.post(draft, t)

    back = t.read_by_operation_id(COMPANY, draft.operation_id)
    assert back is not None
    assert posted.posted_tally_id == back.tally_id

    # Scoped to Tally's own register, so the identifier is one a person could
    # actually look up - not merely a string we agree with ourselves about.
    register = t.read_vouchers(COMPANY)
    assert [v.tally_id for v in register].count(posted.posted_tally_id) == 1

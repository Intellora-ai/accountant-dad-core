"""Owner items 12-14. Doing the same thing twice must not put the same bill into
somebody's books twice.

THE INVARIANT UNDER TEST
------------------------
One operation has ONE identity, minted once, and a retry carrying that identity
must never create a second voucher. `accountant/tallyio/client.py` calls it C5.
Everything here is an attempt to make that sentence false: submit twice, click
twice, replay a stale form, retry after a lost reply, replay a reversed
operation, answer one question twice.

Every case asserts the NUMBER, not the exception. `pytest.raises` on its own
proves that a call raised and says nothing at all about the books, so each
refusal below is followed by a count taken off the register and a trial balance
compared against the one taken before it.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
* Nothing here touches a real TallyPrime. Evidence class: FAKETALLY, plus
  `RealTally` driven by `tests.test_real_tally.TallySim`. A simulator built from
  the connector's own assumptions cannot falsify them - it can only show the
  connector is inconsistent with itself. Real-Tally idempotency is
  licence-blocked and `docs/PROJECT_STATE.md` records it as UNVERIFIED; nothing
  in this file changes that.
* It does not prove anything about concurrency inside Tally, or inside the app.
  `HTTPServer` is single-threaded, so the "double-click" case below serialises
  in the handler. That is said out loud where it matters: the duplicate it
  exposes needs no race, which makes it worse and not better.
* It does not prove the reversal arithmetic. `tests/test_bulk_reversal.py` owns
  that. Here a trial balance is only ever asserted to have NOT moved.

WHAT IT MEASURED AND DID NOT FIX
--------------------------------
Two `@pytest.mark.xfail(strict=True)` cases, in the idiom
`tests/test_adversarial_write_path.py` established. Each asserts the behaviour
the system SHOULD have, is expected to fail at this commit, and becomes a hard
failure the moment somebody fixes the defect and forgets the test. Each is
paired with a plain passing test pinning what was actually measured, so the
defect is visible in a green run.

    I1  accountant/pipeline.py:456   an operation id that has been REVERSED can
                                     be written again. One identity, two
                                     vouchers over time, two `posted` rows, two
                                     Tally ids - and it is reachable from a
                                     browser by re-submitting the form that
                                     posted the first one.
    I2  accountant/pipeline.py:490   a `DuplicateOperation` refusal - positive
                                     evidence that this attempt wrote nothing -
                                     is recorded as `write_outcome_unknown`,
                                     the row whose whole meaning is "a voucher
                                     may exist and must be checked by hand".

A THIRD FINDING, DELIBERATELY NOT AN XFAIL
------------------------------------------
`POST /entry` has no double-click protection at all: two identical submissions
make two drafts, two operation ids and two vouchers, and nothing asks or flags.
That is MEASURED here rather than asserted as a bug. Whether a second identical
bill on the same day is a slip or a real second payment is an owner decision,
and a test may not make it. The number is proved; the judgement is left where
it belongs.
"""

from __future__ import annotations

import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import replace
from http.server import HTTPServer

import pytest

from accountant import pipeline, reversal
from accountant import questions as Q
from accountant.memory.company import CompanyMemory
from accountant.memory.store import MemoryStore
from accountant.schema import Outcome, Voucher
from accountant.tallyio import real
from accountant.tallyio.client import (
    DuplicateOperation,
    new_operation_id,
    operation_id_in,
    stamp,
)
from accountant.tallyio.factory import BackendIdentity, new_run_id
from accountant.web import app
from tests import test_adversarial_write_path as W
from tests import test_tally_contract as contract
from tests.test_first_detector import stale_ledger_company
from tests.test_web import get, log_block

COMPANY = W.COMPANY
RUN = "run_idempotency"

KNOWN = "paid Sharma Traders 4200 for cement"
UNKNOWN = "paid Gupta Hardware 1500 for tools"
FUNDING = "funding_is_named"
PURPOSE = "which_account"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def submit(base: str, path: str, **fields: str) -> tuple[int, str]:
    """A POST that returns the STATUS as well as the body.

    `tests/test_web.py::post` raises on anything that is not 2xx, which is right
    for the tests it was written for and useless here: half of this file is
    about what a REPLAYED request is answered with. A 503 is an answer, and a
    helper that cannot see one cannot tell it from a dropped socket.
    """
    data = urllib.parse.urlencode(fields).encode()
    try:
        with urllib.request.urlopen(base + path, data=data, timeout=10) as r:  # noqa: S310
            return r.status, r.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def draft_id(page: str) -> str:
    found = re.search(r'name=draft value="([^"]+)"', page)
    assert found, f"no draft id on the page:\n{page[:600]}"
    return found.group(1)


def operation(page: str) -> str:
    found = re.search(r'name=op value="([^"]+)"', page)
    assert found, f"no operation id on the page:\n{page[:600]}"
    return found.group(1)


def batch_id(page: str) -> str:
    found = re.search(r'name=batch value="([^"]+)"', page)
    assert found, f"no batch id on the page:\n{page[:600]}"
    return found.group(1)


def problem_id(page: str) -> str:
    found = re.search(r'name=problem value="([^"]+)"', page)
    assert found, f"no question on the page:\n{page[:900]}"
    return found.group(1)


def rows_in(page: str) -> list[tuple[str, str]]:
    """(outcome, action) for every row the activity log actually RENDERS.

    Scoped to `<section id=log>` for the reason `tests/test_web.py` states at
    length: searching a whole page for a common word passes on an empty log.
    """
    return re.findall(r'data-outcome="([^"]+)" data-action="([^"]+)"', log_block(page))


def actions_in(page: str) -> list[str]:
    return [action for _, action in rows_in(page)]


def imports_sent(sim: object) -> int:
    envelopes = getattr(sim, "sent", [])
    return sum(1 for out in envelopes if "<TALLYREQUEST>Import</TALLYREQUEST>" in out)


@pytest.fixture
def flagged_server() -> Iterator[str]:
    """A server over the ONE company whose review screen raises a flag.

    A third spin-up path is a cost and it is paid deliberately. `conftest.py`
    re-exports the `tests/test_web.py` fixture over the demo company, on which
    an entry either posts straight through or asks - neither produces a flag, so
    there is nothing there to dismiss. The company itself comes from
    `tests/test_first_detector.py`, imported as a plain function rather than
    copied, so the one route to the detector stays defined in one place.
    """
    tally = stale_ledger_company()
    identity = BackendIdentity(
        backend="FakeTally",
        endpoint="memory://tests/test_idempotency.py",
        company=app.COMPANY,
        company_exists=True,
        companies_visible=1,
        run_id=new_run_id(),
    )
    app.DRAFTS.clear()
    app.BATCHES.clear()
    httpd = HTTPServer(("127.0.0.1", 0), app.Handler)
    ready = threading.Event()

    def serve() -> None:
        app.configure(tally, identity, store=MemoryStore(":memory:"))
        ready.set()
        httpd.serve_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=5), "the server thread never bootstrapped memory"
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        app.disconnect()


def posted_once() -> tuple[
    W.RecordingTally, pipeline.Draft, MemoryStore, CompanyMemory
]:
    """One voucher of ours in the books, written through the real pipeline."""
    inner = W.tally(W.past())
    store = MemoryStore(":memory:")
    memory = W.memory_for(inner, store)
    client = W.RecordingTally(inner)
    draft = W.valid_draft(client, memory)
    pipeline.post(draft, client, log=store, memory=memory, run_id=RUN)
    pipeline.record_decision(store, draft, memory, client, "posted", RUN)
    return client, draft, store, memory


# ---------------------------------------------------------------------------
# 1. the same typed entry, twice. THE NUMBER.
# ---------------------------------------------------------------------------


def test_the_same_typed_entry_submitted_twice_creates_two_drafts_and_two_vouchers(
    server: str,
):
    """The measured number, and it is two. Owner item 12.

    There is no request-level idempotency key on this route.
    `pipeline.build_draft` mints a fresh `operation_id` per call, so the second
    submission is a DIFFERENT operation by construction and C5 never engages -
    the duplicate guard is not defeated here, it is never consulted.

    The consequence, in paise rather than adjectives: one bill of Rs 4,200 typed
    twice charges Purchases Rs 8,400 and credits Cash Rs 8,400.
    """
    tally = app.runtime().client
    before = tally.trial_balance(app.COMPANY)

    first = submit(server, "/entry", text=KNOWN)[1]
    second = submit(server, "/entry", text=KNOWN)[1]

    assert len(app.DRAFTS) == 2, "two drafts"
    assert operation(first) != operation(second), "two operation ids"

    ours = tally.list_our_vouchers(app.COMPANY)
    assert len(ours) == 2, "two vouchers"
    assert len({v.tally_id for v in ours}) == 2, "and Tally gave each its own id"

    after = tally.trial_balance(app.COMPANY)
    assert after["Purchases"] - before.get("Purchases", 0) == 840000
    assert before.get("Cash", 0) - after["Cash"] == 840000


def test_a_double_click_needs_no_race_to_produce_the_second_voucher(server: str):
    """Two POSTs fired from two threads at one barrier. Owner item 13.

    `HTTPServer` is single-threaded, so these requests are SERIALISED inside the
    handler and never interleave. That is the point worth stating: what follows
    is not a race a lock would close. It is what the route does when it is asked
    twice, at any speed, from anywhere.
    """
    tally = app.runtime().client
    answers: list[tuple[int, str]] = []
    barrier = threading.Barrier(2)

    def click() -> None:
        barrier.wait(timeout=5)
        answers.append(submit(server, "/entry", text=KNOWN))

    clicks = [threading.Thread(target=click) for _ in range(2)]
    for c in clicks:
        c.start()
    for c in clicks:
        c.join(timeout=10)

    assert [code for code, _ in answers] == [200, 200]
    assert len({operation(body) for _, body in answers}) == 2
    assert len(tally.list_our_vouchers(app.COMPANY)) == 2


def test_nothing_asks_or_flags_when_the_same_entry_arrives_a_second_time(server: str):
    """The control on the two tests above: the second entry is not merely
    permitted, it is UNREMARKED.

    `detectors.SLICE_4_DETECTORS` is `(vendor_switch,)` and nothing else, so no
    detector compares an entry against one posted a second earlier. Asserting
    the absence matters: "we post it twice but we warn" and "we post it twice in
    silence" are different products and only the second one is here.
    """
    submit(server, "/entry", text=KNOWN)
    second = submit(server, "/entry", text=KNOWN)[1]

    assert "<p class=ask>" not in second, "no question was put"
    assert "data-detector=" not in second, "no flag was raised"
    assert 'class="badge b-valid">posted<' in second
    assert actions_in(get(server)).count("posted") == 2


def test_the_two_vouchers_from_one_double_click_carry_different_markers(server: str):
    """Which is what makes the pair invisible to every duplicate defence there
    is: C5, the ambiguity refusal and reversal all key on the marker, and these
    two do not share one."""
    submit(server, "/entry", text=KNOWN)
    submit(server, "/entry", text=KNOWN)

    ours = app.runtime().client.list_our_vouchers(app.COMPANY)
    markers = {operation_id_in(v.narration) for v in ours}
    assert len(markers) == 2
    assert None not in markers, "both are ours; neither is hand-typed"


# ---------------------------------------------------------------------------
# 2. one operation id, one voucher
# ---------------------------------------------------------------------------


def test_the_same_operation_id_sent_twice_to_write_voucher_leaves_the_count_alone() -> (
    None
):
    """C5 at the client boundary. The count is the claim, not the exception."""
    client = W.RecordingTally(W.tally(W.past()))
    op = new_operation_id()
    voucher = contract.a_voucher()

    client.write_voucher(COMPANY, voucher, op)
    ours_after_first = client.list_our_vouchers(COMPANY)
    balance_after_first = client.trial_balance(COMPANY)

    with pytest.raises(DuplicateOperation) as refused:
        client.write_voucher(COMPANY, voucher, op)

    assert op in str(refused.value)
    assert client.write_count == 2, "the double was ASKED twice"
    assert client.list_our_vouchers(COMPANY) == ours_after_first, "and wrote once"
    assert len(client.list_our_vouchers(COMPANY)) == 1
    assert client.trial_balance(COMPANY) == balance_after_first


def test_the_real_connector_refuses_the_duplicate_before_it_imports_anything() -> None:
    """Refusing AFTER the import has gone out would be a second voucher with an
    apology attached. The envelope count is what proves the order."""
    sim = W.a_simulated_tally()
    client = W.sim_client(sim)
    op = new_operation_id()

    client.write_voucher(COMPANY, contract.a_voucher(), op)
    assert imports_sent(sim) == 1

    with pytest.raises(DuplicateOperation):
        client.write_voucher(COMPANY, contract.a_voucher(), op)

    assert imports_sent(sim) == 1, "the retry never reached the import"
    assert len(sim.companies[COMPANY].vouchers) == 1
    assert len(client.list_our_vouchers(COMPANY)) == 1


def test_both_backends_refuse_the_second_write_of_one_operation_id() -> None:
    """The double may not be softer than the connector on the write path.

    A double that makes an easier call does not merely fail to catch a bug, it
    issues an alibi: a test written against it can show a duplicate being
    refused where the real thing would not refuse.
    """
    for client in (W.tally(), W.sim_client(W.a_simulated_tally())):
        op = new_operation_id()
        client.write_voucher(COMPANY, contract.a_voucher(), op)

        with pytest.raises(DuplicateOperation) as refused:
            client.write_voucher(COMPANY, contract.a_voucher(), op)

        assert "already written" in str(refused.value)
        assert len(client.list_our_vouchers(COMPANY)) == 1


def test_posting_the_same_draft_twice_writes_one_voucher_and_moves_nothing() -> None:
    """The pipeline path, which is the one the app takes."""
    client, draft, _store, _memory = posted_once()
    ours = client.list_our_vouchers(COMPANY)
    balance = client.trial_balance(COMPANY)

    with pytest.raises(DuplicateOperation):
        pipeline.post(draft, client)

    assert client.list_our_vouchers(COMPANY) == ours
    assert len(ours) == 1
    assert client.trial_balance(COMPANY) == balance
    assert len(client.read_vouchers(COMPANY)) == 41, "40 of history plus ours"


def test_a_refused_replay_leaves_durable_rows_and_no_second_posted_row() -> None:
    """The audit half. Every row about this write names the one operation id,
    and exactly one of them says the entry was posted."""
    client, draft, store, memory = posted_once()

    with pytest.raises(DuplicateOperation):
        pipeline.post(draft, client, log=store, memory=memory, run_id=RUN)

    rows = store.actions(COMPANY)
    assert rows, "a refused replay is not allowed to be silent"
    assert {r.operation_id for r in rows} == {draft.operation_id}
    assert [r.action for r in rows].count("posted") == 1
    assert all(r.run_id == RUN for r in rows)


def test_a_replay_that_carries_no_log_still_cannot_write_twice() -> None:
    """The guard lives in the client, not in the audit trail. Losing the log
    must weaken the record and never the refusal."""
    client, draft, _store, _memory = posted_once()

    with pytest.raises(DuplicateOperation):
        pipeline.post(draft, client)

    assert len(client.list_our_vouchers(COMPANY)) == 1


# ---------------------------------------------------------------------------
# 3. the reply was lost. A retry must not become a second entry.
# ---------------------------------------------------------------------------


def test_a_write_whose_read_back_vanished_is_not_retried_into_a_second_voucher() -> (
    None
):
    """WRITE_OUTCOME_UNKNOWN, then a retry. Owner item 14.

    The voucher IS in the books; only our confirmation of it was lost. A retry
    is therefore the dangerous move, and the duplicate guard is the one thing
    that makes it survivable.
    """
    inner = W.tally(W.past())
    store = MemoryStore(":memory:")
    memory = W.memory_for(inner, store)
    client = W.LosesTheReadBack(inner)
    draft = W.valid_draft(client, memory)

    with pytest.raises(RuntimeError, match="could not read it back"):
        pipeline.post(draft, client, log=store, memory=memory, run_id=RUN)

    assert len(inner.list_our_vouchers(COMPANY)) == 1, "Tally really did the work"
    after_the_lost_reply = inner.trial_balance(COMPANY)

    with pytest.raises(DuplicateOperation):
        pipeline.post(draft, client, log=store, memory=memory, run_id=RUN)

    assert client.write_count == 2, "asked twice"
    assert len(inner.list_our_vouchers(COMPANY)) == 1, "wrote once"
    assert inner.trial_balance(COMPANY) == after_the_lost_reply
    assert draft.posted_tally_id is None, "and nothing is recorded as posted"


def test_an_unknown_outcome_is_written_down_before_any_retry_is_attempted() -> None:
    """The write-ahead row is what makes an unknown findable at all. Without it
    the operation id survives in a traceback and nowhere else."""
    inner = W.tally(W.past())
    store = MemoryStore(":memory:")
    memory = W.memory_for(inner, store)
    client = W.LosesTheReadBack(inner)
    draft = W.valid_draft(client, memory)

    with pytest.raises(RuntimeError):
        pipeline.post(draft, client, log=store, memory=memory, run_id=RUN)

    actions = [r.action for r in store.actions(COMPANY)]
    assert actions == [pipeline.WRITE_ATTEMPTED, pipeline.WRITE_OUTCOME_UNKNOWN]
    assert {r.operation_id for r in store.actions(COMPANY)} == {draft.operation_id}
    assert "posted" not in actions, "no false COMPLETED"


def test_a_connection_dropped_after_the_request_was_sent_still_writes_one_voucher() -> (
    None
):
    """The write landed and the socket died on the way back.

    `DropsTheReply` calls the simulator FIRST and keeps its effect, because a
    timeout is not a rollback. The retry then meets the duplicate guard.
    """
    sim = W.a_simulated_tally()
    transport = W.DropsTheReply(
        sim,
        drop=lambda payload, seen: (
            "<TALLYREQUEST>Import</TALLYREQUEST>" in payload and seen.imports_seen == 1
        ),
    )
    client = W.sim_client(sim, transport)
    op = new_operation_id()

    with pytest.raises(real.TallyUnreachable, match="never arrived"):
        client.write_voucher(COMPANY, contract.a_voucher(), op)

    assert len(sim.companies[COMPANY].vouchers) == 1
    balance = client.trial_balance(COMPANY)

    with pytest.raises(DuplicateOperation):
        client.write_voucher(COMPANY, contract.a_voucher(), op)

    assert transport.imports_seen == 1, "the retry never reached the import"
    assert len(sim.companies[COMPANY].vouchers) == 1
    assert client.trial_balance(COMPANY) == balance


def test_a_read_back_that_raises_leaves_one_voucher_and_a_retry_that_refuses() -> None:
    """`DropsTheReadBackConnection`: Tally accepted the write and went away."""
    inner = W.tally(W.past())
    store = MemoryStore(":memory:")
    memory = W.memory_for(inner, store)
    client = W.DropsTheReadBackConnection(inner)
    draft = W.valid_draft(client, memory)

    with pytest.raises(ConnectionError):
        pipeline.post(draft, client, log=store, memory=memory, run_id=RUN)

    assert len(inner.list_our_vouchers(COMPANY)) == 1
    assert pipeline.WRITE_OUTCOME_UNKNOWN in [r.action for r in store.actions(COMPANY)]

    with pytest.raises(DuplicateOperation):
        pipeline.post(draft, client, log=store, memory=memory, run_id=RUN)

    assert len(inner.list_our_vouchers(COMPANY)) == 1


# ---------------------------------------------------------------------------
# 4. the web routes, replayed
# ---------------------------------------------------------------------------


def test_replaying_the_answer_that_posted_an_entry_writes_no_second_voucher(
    server: str,
):
    """The browser Back button, in its ordinary shape.

    `DRAFTS` still holds the posted draft and its decision offers no options, so
    the replay runs the whole answer path again and reaches `pipeline.post`
    carrying the SAME operation id. C5 is the only thing between that and a
    second statutory entry, and it holds.
    """
    tally = app.runtime().client
    asked = submit(server, "/entry", text=UNKNOWN)[1]
    d = draft_id(asked)
    submit(server, "/answer", draft=d, value="Purchases", problem=PURPOSE)
    code, done = submit(server, "/answer", draft=d, value="Cash", problem=FUNDING)
    assert code == 200 and "posted" in done.lower()

    ours = tally.list_our_vouchers(app.COMPANY)
    balance = tally.trial_balance(app.COMPANY)
    assert len(ours) == 1

    replay_code, replay = submit(
        server, "/answer", draft=d, value="Cash", problem=FUNDING
    )

    assert replay_code == 503, "the replay is refused, and legibly"
    assert tally.list_our_vouchers(app.COMPANY) == ours
    assert tally.trial_balance(app.COMPANY) == balance
    assert 'class="badge b-valid">posted<' not in replay


def test_the_refused_replay_is_recorded_and_reports_no_second_posting(server: str):
    """A refusal nobody can find afterwards cannot be investigated."""
    asked = submit(server, "/entry", text=UNKNOWN)[1]
    d = draft_id(asked)
    submit(server, "/answer", draft=d, value="Purchases", problem=PURPOSE)
    submit(server, "/answer", draft=d, value="Cash", problem=FUNDING)
    submit(server, "/answer", draft=d, value="Cash", problem=FUNDING)

    actions = actions_in(get(server))
    assert actions.count("posted") == 1, "one posting, however many clicks"
    assert "failed" in actions, "and the refused replay left a row of its own"


def test_the_same_answer_sent_twice_is_refused_and_recorded_once(server: str):
    """The second copy of an answer must not become a second answer.

    It is refused because the QUESTION it names is not the question this entry
    is asking. The first answer retired the purpose question and the entry moved
    on to the funding one, so the second copy is bound to nothing and
    `Decision.refuse_answer` turns it away before a ledger leg is touched.

    This said, until 2026-08-10, that the refusal came from the VALUE not being
    among the options the funding question offers. That was true then and it was
    never the property worth resting on: it held only because the two questions
    in this app happen to offer disjoint sets. A replayed answer whose value the
    NEXT question also offers went straight through and moved the wrong leg,
    which is the defect `tests/test_answer_binding.py` was written for.

    400 rather than 503: the request is wrong, not the service.
    """
    asked = submit(server, "/entry", text=UNKNOWN)[1]
    d = draft_id(asked)

    first = submit(server, "/answer", draft=d, value="Purchases", problem=PURPOSE)[0]
    code, body = submit(server, "/answer", draft=d, value="Purchases", problem=PURPOSE)

    assert (first, code) == (200, 400)
    assert "was not one of the" in body
    assert app.DRAFTS[d].answers == [(PURPOSE, "Purchases")], "one answer, not two"
    assert app.runtime().client.list_our_vouchers(app.COMPANY) == ()


def test_a_second_different_answer_to_the_same_question_does_not_move_the_leg(
    server: str,
):
    """The dangerous half of a replay: not a duplicate, a silent correction."""
    asked = submit(server, "/entry", text=UNKNOWN)[1]
    d = draft_id(asked)
    submit(server, "/answer", draft=d, value="Purchases", problem=PURPOSE)

    code, _ = submit(
        server, "/answer", draft=d, value="Repairs & Maintenance", problem=PURPOSE
    )

    assert code == 400
    held = app.DRAFTS[d]
    assert held.voucher.debit_account == "Purchases"
    assert held.answers == [(PURPOSE, "Purchases")]
    assert app.runtime().client.list_our_vouchers(app.COMPANY) == ()


def test_a_replayed_answer_carrying_an_unknown_draft_id_posts_nothing(server: str):
    """A form from another session, or from a process that has restarted."""
    before = app.runtime().client.trial_balance(app.COMPANY)

    code, body = submit(
        server,
        "/answer",
        draft="ad-not-a-draft-of-ours",
        value="Purchases",
        problem=PURPOSE,
    )

    assert code == 200 and "expired" in body.lower()
    assert app.runtime().client.list_our_vouchers(app.COMPANY) == ()
    assert app.runtime().client.trial_balance(app.COMPANY) == before


def test_a_replayed_answer_carrying_an_evicted_draft_id_posts_nothing(server: str):
    """`DRAFTS` is capped at 200 and evicts oldest-first. The eviction has to be
    said out loud rather than answered by applying the form to another draft."""
    stale = draft_id(submit(server, "/entry", text=UNKNOWN)[1])

    for i in range(app.DRAFT_LIMIT):
        submit(server, "/entry", text=f"paid Gupta Hardware {1500 + i} for tools")

    assert stale not in app.DRAFTS, "the fixture must actually evict it"
    assert len(app.DRAFTS) == app.DRAFT_LIMIT

    code, body = submit(
        server, "/answer", draft=stale, value="Purchases", problem=PURPOSE
    )

    assert code == 200 and "expired" in body.lower()
    assert app.runtime().client.list_our_vouchers(app.COMPANY) == ()


def test_handing_over_twice_posts_nothing_and_never_reports_a_valid_outcome(
    server: str,
):
    """HANDOVER exhausts the question budget. Doing it again must not post.

    The second handover DOES append a second `handed_over` row - unlike
    `/dismiss`, this route has no already-done guard. That is log noise and it
    is pinned here rather than asserted away, because the books are what this
    test is about and the books do not move.
    """
    submit(server, "/entry", text=UNKNOWN)
    d = next(iter(app.DRAFTS))
    before = app.runtime().client.trial_balance(app.COMPANY)

    submit(server, "/answer", draft=d, value=Q.HANDOVER, problem=PURPOSE)
    submit(server, "/answer", draft=d, value=Q.HANDOVER, problem=PURPOSE)

    assert app.runtime().client.trial_balance(app.COMPANY) == before
    assert app.runtime().client.list_our_vouchers(app.COMPANY) == ()
    assert app.DRAFTS[d].outcome is Outcome.NOT_VALID
    assert 'data-outcome="valid"' not in log_block(get(server))
    assert actions_in(get(server)).count("handed_over") == 2


def test_reversing_the_same_operation_twice_moves_the_books_once(server: str):
    """The second undo must say NOT FOUND, not undo something else."""
    tally = app.runtime().client
    before = tally.trial_balance(app.COMPANY)
    op = operation(submit(server, "/entry", text=KNOWN)[1])

    submit(server, "/reverse", op=op)
    assert tally.trial_balance(app.COMPANY) == before, "restored to the exact value"

    submit(server, "/reverse", op=op)

    assert tally.trial_balance(app.COMPANY) == before
    assert tally.list_our_vouchers(app.COMPANY) == ()
    outcomes = [
        outcome for outcome, action in rows_in(get(server)) if action == "reversed"
    ]
    assert outcomes.count("reversed") == 1
    assert outcomes.count("not_found") == 1


def test_confirming_one_bulk_reversal_twice_reverses_one_batch(server: str):
    """#14.7's two-step confirmation is also its replay defence: the batch id is
    POPPED, so a second confirmation has no list to act on."""
    tally = app.runtime().client
    before = tally.trial_balance(app.COMPANY)
    submit(server, "/entry", text=KNOWN)
    shown = batch_id(submit(server, "/reverse-all")[1])

    first = submit(server, "/reverse-all", batch=shown, confirm="yes")[1]
    assert reversal.BatchState.COMPLETED.value in first
    assert tally.list_our_vouchers(app.COMPANY) == ()

    code, second = submit(server, "/reverse-all", batch=shown, confirm="yes")

    assert code == 200
    assert "had no preview" in second, "and it says why rather than pretending"
    assert tally.trial_balance(app.COMPANY) == before
    assert actions_in(get(server)).count("bulk_reversed") == 1


def test_a_confirmation_that_names_no_batch_at_all_reverses_nothing(server: str):
    """The same defence, reached by a hand-made POST rather than by a replay."""
    submit(server, "/entry", text=KNOWN)
    tally = app.runtime().client
    ours = tally.list_our_vouchers(app.COMPANY)
    balance = tally.trial_balance(app.COMPANY)

    code, body = submit(server, "/reverse-all", confirm="yes")

    assert code == 200 and "had no preview" in body
    assert tally.list_our_vouchers(app.COMPANY) == ours
    assert tally.trial_balance(app.COMPANY) == balance


def test_two_previews_in_a_row_reverse_nothing_between_them(server: str):
    """A preview is a question. Asking it twice must write nothing at all."""
    submit(server, "/entry", text=KNOWN)
    tally = app.runtime().client
    balance = tally.trial_balance(app.COMPANY)

    first = batch_id(submit(server, "/reverse-all")[1])
    second = batch_id(submit(server, "/reverse-all")[1])

    assert first != second, "each preview is its own question"
    assert tally.trial_balance(app.COMPANY) == balance
    assert len(tally.list_our_vouchers(app.COMPANY)) == 1
    assert "bulk_reversed" not in actions_in(get(server))


def test_a_dismissal_replayed_leaves_one_dismissal_on_the_draft(flagged_server: str):
    """`tests/test_first_detector.py` already proves the LOG gains one row. What
    is added here is the draft's own state, which is what the page renders from:
    a `dismissed` list that grew on every click would eventually draw the same
    flag as dismissed several times over."""
    page = submit(flagged_server, "/entry", text=KNOWN)[1]
    d = draft_id(page)
    page = submit(
        flagged_server, "/answer", draft=d, problem=problem_id(page), value="Purchases"
    )[1]
    assert 'data-detector="vendor_switch"' in page

    submit(flagged_server, "/dismiss", draft=d, detector="vendor_switch")
    submit(flagged_server, "/dismiss", draft=d, detector="vendor_switch")

    assert app.DRAFTS[d].dismissed == ["vendor_switch"]
    assert actions_in(get(flagged_server)).count("dismissed") == 1
    assert app.runtime().client.list_our_vouchers(app.COMPANY) == ()


def test_dismissing_a_detector_that_never_fired_records_nothing(flagged_server: str):
    """The control. A dismissal route that accepts any string is a log that can
    be filled with detectors nobody ever ran."""
    d = draft_id(submit(flagged_server, "/entry", text=KNOWN)[1])

    submit(flagged_server, "/dismiss", draft=d, detector="no_such_detector")

    assert "dismissed" not in actions_in(get(flagged_server))
    assert app.DRAFTS[d].dismissed == []


# ---------------------------------------------------------------------------
# 5. an operation id that was reversed, then replayed. DEFECT I1.
# ---------------------------------------------------------------------------


def test_an_operation_id_that_was_reversed_can_be_written_again_today() -> None:
    """WHAT WAS MEASURED. This test PINS A DEFECT; see the xfail below.

    C5 asks Tally whether the marker is already there. After a reversal it is
    not, so the guard passes and the same operation id is written a second time
    against a second Tally id. One identity, two vouchers over the life of the
    books, and an audit trail in which `operation_id` has stopped being a key.

    The trial balance ends exactly where a single posting leaves it, which is
    why nobody notices: the money is right and the identity is not.
    """
    client, draft, store, memory = posted_once()
    first_tally_id = draft.posted_tally_id
    assert pipeline.reverse_operation(client, COMPANY, draft.operation_id).reversed_
    assert client.list_our_vouchers(COMPANY) == ()

    again = pipeline.post(draft, client, log=store, memory=memory, run_id=RUN)

    assert len(client.list_our_vouchers(COMPANY)) == 1
    assert again.posted_tally_id != first_tally_id, "a DIFFERENT voucher"
    assert again.operation_id == draft.operation_id, "wearing the SAME identity"


@pytest.mark.xfail(strict=True, reason="DEFECT I1 - accountant/pipeline.py:456")
def test_an_operation_id_that_was_reversed_is_never_written_again() -> None:
    """DEFECT I1. The behaviour the system should have, and does not.

    `docs/ARCHITECTURE.md` §7 and `accountant/tallyio/client.py` both say the
    operation id IS the identity: reads, duplicate detection and reversal match
    on it and on nothing else. An identity that can be reused after a delete is
    not an identity, it is a slot. Two `posted` rows naming one operation id and
    two different Tally ids cannot afterwards be reconciled by the one thing
    they have in common.

    The fix is not in this file. Whatever shape it takes - a write-once record
    of every operation id ever used, or minting a fresh id on a re-post - it is
    a source change and the owner makes it.
    """
    client, draft, store, memory = posted_once()
    pipeline.reverse_operation(client, COMPANY, draft.operation_id)

    with pytest.raises(DuplicateOperation):
        pipeline.post(draft, client, log=store, memory=memory, run_id=RUN)

    assert client.list_our_vouchers(COMPANY) == ()


def test_the_reversed_then_replayed_defect_is_reachable_from_the_browser(server: str):
    """I1 is not a library curiosity. Post, undo, then re-submit the form that
    posted it: the activity log ends with TWO `posted` rows naming one operation
    id, and the page shows that id beside a voucher Tally numbered differently.
    """
    tally = app.runtime().client
    asked = submit(server, "/entry", text=UNKNOWN)[1]
    d = draft_id(asked)
    submit(server, "/answer", draft=d, value="Purchases", problem=PURPOSE)
    done = submit(server, "/answer", draft=d, value="Cash", problem=FUNDING)[1]
    op = operation(done)
    first_ids = {v.tally_id for v in tally.list_our_vouchers(app.COMPANY)}

    submit(server, "/reverse", op=op)
    assert tally.list_our_vouchers(app.COMPANY) == ()

    code, again = submit(server, "/answer", draft=d, value="Cash", problem=FUNDING)

    assert code == 200 and operation(again) == op, "the same identity"
    now = tally.list_our_vouchers(app.COMPANY)
    assert len(now) == 1
    assert {v.tally_id for v in now} != first_ids, "a different voucher"
    assert actions_in(get(server)).count("posted") == 2


# ---------------------------------------------------------------------------
# 6. a duplicate refusal is a KNOWN outcome. DEFECT I2.
# ---------------------------------------------------------------------------


def test_a_duplicate_refusal_is_recorded_as_an_unknown_outcome_today() -> None:
    """WHAT WAS MEASURED. This test PINS A DEFECT; see the xfail below.

    `pipeline.post` writes its write-ahead row, calls `write_voucher`, and its
    `except BaseException` arm records WRITE_OUTCOME_UNKNOWN for everything that
    comes out. `DuplicateOperation` is raised BEFORE any import goes out, on
    both backends, so at that instant there is positive evidence that this
    attempt wrote nothing. The row says the opposite.
    """
    client, draft, store, memory = posted_once()
    before = len(store.actions(COMPANY))

    with pytest.raises(DuplicateOperation):
        pipeline.post(draft, client, log=store, memory=memory, run_id=RUN)

    added = store.actions(COMPANY)[before:]
    assert [r.action for r in added] == [
        pipeline.WRITE_ATTEMPTED,
        pipeline.WRITE_OUTCOME_UNKNOWN,
    ]
    assert "DuplicateOperation" in added[-1].reason


@pytest.mark.xfail(strict=True, reason="DEFECT I2 - accountant/pipeline.py:490")
def test_a_duplicate_refusal_is_never_recorded_as_an_unknown_outcome() -> None:
    """DEFECT I2. UNKNOWN means "a voucher may exist and must be checked by
    hand", and a duplicate refusal is the one write failure where that is
    knowably false.

    `accountant/tallyio/real.py` argues the opposite direction at length: an
    UNKNOWN must never be flattened into a failure, because a retry after a
    write that DID land makes two entries. The mirror costs less and is still
    untrue - it sends somebody to look in Tally for a voucher we already know we
    did not write, and it dilutes the row that means a person really is needed.
    """
    client, draft, store, memory = posted_once()
    before = len(store.actions(COMPANY))

    with pytest.raises(DuplicateOperation):
        pipeline.post(draft, client, log=store, memory=memory, run_id=RUN)

    added = store.actions(COMPANY)[before:]
    assert pipeline.WRITE_OUTCOME_UNKNOWN not in [r.action for r in added]
    assert len(client.list_our_vouchers(COMPANY)) == 1


# ---------------------------------------------------------------------------
# 7. two vouchers wearing one marker: refuse, never choose
# ---------------------------------------------------------------------------


def test_a_marker_on_two_vouchers_refuses_every_path_and_deletes_neither() -> None:
    """The ambiguity C5 exists to prevent, arriving anyway.

    `tests/test_adversarial_write_path.py` proves the two backends refuse it
    identically. What is added here is the COUNT after each refusal: an
    ambiguity that quietly removed one of the pair would be worse than the
    ambiguity.
    """
    client, draft, _store, _memory = posted_once()
    op = draft.operation_id
    client.inner.seed_voucher(
        COMPANY,
        replace(client.list_our_vouchers(COMPANY)[0], id="twin", tally_id="TALLY-999"),
    )
    assert len(client.list_our_vouchers(COMPANY)) == 2
    balance = client.trial_balance(COMPANY)

    for attempt in (
        lambda: client.read_by_operation_id(COMPANY, op),
        lambda: client.reverse_by_operation_id(COMPANY, op),
        lambda: client.write_voucher(COMPANY, draft.voucher, op),
        lambda: pipeline.reverse_operation(client, COMPANY, op),
    ):
        with pytest.raises(real.TallyDataError, match="matches 2 vouchers"):
            attempt()
        assert len(client.list_our_vouchers(COMPANY)) == 2, "neither was removed"

    assert client.trial_balance(COMPANY) == balance


def test_a_bulk_reversal_over_an_ambiguous_marker_reverses_nothing() -> None:
    """The batch path meets the same pair. It must stop, not pick."""
    client, draft, _store, _memory = posted_once()
    client.inner.seed_voucher(
        COMPANY,
        replace(client.list_our_vouchers(COMPANY)[0], id="twin", tally_id="T-999"),
    )
    balance = client.trial_balance(COMPANY)

    batch = reversal.execute(
        reversal.confirm(reversal.preview(client, COMPANY)), client
    )

    assert batch.state is not reversal.BatchState.COMPLETED, "no false COMPLETED"
    assert len(client.list_our_vouchers(COMPANY)) == 2
    assert client.trial_balance(COMPANY) == balance
    assert draft.operation_id in {o.operation_id for o in batch.outcomes}


# ---------------------------------------------------------------------------
# 8. the identity itself, under repetition
# ---------------------------------------------------------------------------


def test_stamping_a_narration_twice_with_one_id_changes_nothing_the_second_time() -> (
    None
):
    """`stamp` is the only place the marker is ever written, so its idempotency
    is what keeps a re-stamped narration from carrying two markers."""
    op = new_operation_id()
    stamped = stamp("cement bags", op)

    assert stamp(stamped, op) == stamped
    assert operation_id_in(stamped) == op
    assert stamped.count("[ACCOUNTANT_DAD:") == 1


def test_a_narration_already_wearing_another_operation_id_refuses_a_restamp() -> None:
    """The other half. Silently re-stamping would move somebody else's voucher
    into our register."""
    theirs = new_operation_id()
    stamped = stamp("cement bags", theirs)

    with pytest.raises(ValueError, match="refusing to restamp"):
        stamp(stamped, new_operation_id())

    assert operation_id_in(stamped) == theirs


def test_evaluating_a_draft_repeatedly_never_mints_a_second_identity() -> None:
    """The decision is rebuilt on every evaluation. The identity is not."""
    inner = W.tally(W.past())
    memory = W.memory_for(inner, MemoryStore(":memory:"))
    draft = W.valid_draft(inner, memory)
    minted = draft.operation_id
    accounts = inner.read_accounts(COMPANY)
    history = inner.read_vouchers(COMPANY)

    for _ in range(5):
        draft = pipeline.evaluate(draft, accounts, history, memory)
        assert draft.operation_id == minted
        assert draft.decision is not None
        assert draft.decision.operation_id == minted

    assert draft.outcome is Outcome.VALID
    assert inner.list_our_vouchers(COMPANY) == (), "evaluating writes nothing"


def test_two_drafts_of_the_same_text_never_share_an_identity() -> None:
    """Which is the whole reason a double-click duplicates rather than
    deduplicates. Stated as a property, so the cause is not mistaken for a
    failure of the guard."""
    inner = W.tally(W.past())
    memory = W.memory_for(inner, MemoryStore(":memory:"))

    ids = {W.valid_draft(inner, memory).operation_id for _ in range(10)}

    assert len(ids) == 10
    assert all(i.startswith("ad_") for i in ids)


def test_a_voucher_we_did_not_write_is_never_swept_up_by_a_replay() -> None:
    """A hand-typed entry for the same bill carries no marker, so it is neither
    proof of our write nor a candidate for our reversal."""
    inner = W.tally(W.past())
    store = MemoryStore(":memory:")
    memory = W.memory_for(inner, store)
    client = W.RecordingTally(inner)
    draft = W.valid_draft(client, memory)
    by_hand = Voucher(
        id="typed-by-hand",
        date=draft.voucher.date,
        party=draft.voucher.party,
        narration="same bill, typed straight into Tally",
        debit_account=draft.voucher.debit_account,
        credit_account="Cash",
        amount_paise=draft.voucher.amount_paise,
    )
    inner.seed_voucher(COMPANY, by_hand)

    pipeline.post(draft, client, log=store, memory=memory, run_id=RUN)

    ours = client.list_our_vouchers(COMPANY)
    assert len(ours) == 1
    assert by_hand not in ours
    assert [o.operation_id for o in reversal.preview(client, COMPANY).outcomes] == [
        draft.operation_id
    ]

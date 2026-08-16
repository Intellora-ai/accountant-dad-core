"""The guards in `accountant/web/app.py` that no request has ever reached — A.

WHY THIS FILE EXISTS
--------------------
Everything in lines 1-1500 of `accountant/web/app.py` is driven by some other
test file EXCEPT six statements. Measured on 2026-08-12 against the whole
suite, not against a leftover `.coverage` file:

    pytest tests -q -n auto --cov=accountant.web.app
    accountant/web/app.py   846 stmts   15 missed   97%

    missing inside lines 1-1500:  410, 637, 713, 850, 851, 943

Six lines, and every one of them is a guard:

    410       a previewed bulk reversal falling out of a full cache
    637       a previewed deletion falling out of a full cache
    713       a deletion plan confirmed by a caller from another tenant
    850-851   Tally refusing to say which companies are open
    943       memory filed under a key that is not the key of its own name

A guard with no test is a guard that works today. Three of these stand between
a customer and a write nobody asked for; the other two are the sentence a
person reads when the thing underneath them has moved.

WHY NONE OF IT GOES OVER HTTP
-----------------------------
NO NETWORK, and not because a socket is slow. Each of the six is reached by
putting the process into a state the routes cannot produce: a cache one entry
past its limit, a confirmation from the wrong tenant, a connector that raises,
an identity forced past its own validator. Driving those through a server would
mean building the state by hand anyway and then hiding it behind a request.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That the shipped routes REACH these guards. `deletion_for`'s tenant check is
proved correct here; whether `/delete-my-data` calls it at all is
`tests/test_data_deletion.py`'s claim, over HTTP, and it stays there.

That the caches are safe under threads. Eviction is driven from ONE thread.
`tests/test_concurrency.py` owns the racing claim. What is owned here is "the
twenty-first entry pushes the first one out, and the first one is then unusable
rather than merely absent" — a different sentence, and the untested one.

Nothing here says anything about a real TallyPrime. The backend is a `FakeTally`
handed to `app.configure()`, and two tests below subclass it to fail on purpose.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest

from accountant import reversal
from accountant.auth import Principal
from accountant.memory.company import CompanyMemory
from accountant.memory.identity import CompanyIdentity, normalise_company
from accountant.memory.store import IN_MEMORY, MemoryStore
from accountant.tallyio.fake import FakeTally
from accountant.web import app
from tests.test_web import demo_company, fake_backend

#: Two people in one customer, and one person in another. `user_id` is what
#: `batch_for` and `deletion_for` compare; `tenant_id` is what `deletion_for`
#: compares SECOND, which is the line this file exists for.
ASKED = Principal("alpha-user", "tenant-alpha")
COLLEAGUE = Principal("alpha-colleague", "tenant-alpha")

#: The same user id under a different customer. Impossible today — a user row
#: carries its tenant — and that is exactly why the guard needs a test: it is
#: the only shape that gets past the user check and still must be refused.
IMPOSTOR = Principal("alpha-user", "tenant-beta")


# ---- fixtures and small builders --------------------------------------------


@pytest.fixture(autouse=True)
def no_cache_leaks() -> Iterator[None]:
    """The two module-level caches this file fills, emptied either side.

    Both directions on purpose. Before, so a batch left by whatever ran first
    cannot make "there are exactly twenty" a measurement of somebody else's
    leftovers. After, because the suite runs under `-n auto` and a worker
    serves tests from several files in turn.
    """
    app.BATCHES.clear()
    app.DELETIONS.clear()
    yield
    app.BATCHES.clear()
    app.DELETIONS.clear()


@pytest.fixture
def live() -> Iterator[app.Runtime]:
    """A configured runtime over the demo company, dropped on the way out.

    `app.configure` installs it globally, so the teardown is not tidiness:
    a runtime left behind is a company another test would be bound to.
    """
    built = app.configure(demo_company(), fake_backend(), store=MemoryStore(IN_MEMORY))
    try:
        yield built
    finally:
        app.disconnect()


def acting_as(monkeypatch: pytest.MonkeyPatch, who: Principal | None) -> None:
    """Speak as `who` for the rest of this test.

    Through `current_principal`, which is the name the functions under test
    actually read. Reaching into the ContextVar would be testing the storage.
    """

    def whoever() -> Principal | None:
        return who

    monkeypatch.setattr(app, "current_principal", whoever)


def a_batch(batch_id: str) -> reversal.Batch:
    """A preview for the company the runtime is bound to. Nothing reversed."""
    return reversal.Batch(
        batch_id=batch_id,
        company=app.COMPANY,
        state=reversal.BatchState.PREVIEW,
        baseline={},
    )


def a_plan(plan_id: str, *, tenant: str = "tenant-alpha") -> app.DeletionPlan:
    return app.DeletionPlan(
        plan_id=plan_id,
        tenant_id=tenant,
        tenant_name="Alpha Accountants",
        companies_erased=("acme",),
        companies_kept=(),
        users=1,
        sessions=1,
        actions_kept=3,
    )


class TallyThatWillNotSayWhatIsOpen(FakeTally):
    """A connector whose `list_companies` fails the way a dropped socket does.

    A subclass rather than a hand-written double: everything else about it must
    keep working, because `confirm_company` reads our own store first and a
    second failure would make the test pass for the wrong reason.
    """

    def list_companies(self) -> tuple[str, ...]:
        raise ConnectionResetError("TallyPrime closed the connection")


# ---- the previewed bulk reversal that falls out of a full cache -------------


def test_the_batch_past_the_limit_pushes_the_oldest_preview_out() -> None:
    """Line 410. `BATCHES` is bounded, and nothing had ever filled it.

    Unbounded, every preview anybody ever took would stay in memory for the
    life of the process, holding a voucher list per entry.
    """
    for n in range(app.BATCH_LIMIT + 1):
        app.remember_batch(a_batch(f"batch_{n}"), ASKED)

    assert len(app.BATCHES) == app.BATCH_LIMIT
    assert "batch_0" not in app.BATCHES, "the oldest preview survived the limit"
    assert f"batch_{app.BATCH_LIMIT}" in app.BATCHES


def test_the_control_exactly_the_limit_of_batches_evicts_nothing() -> None:
    """THE CONTROL. An eviction that fires one entry early would pass the test
    above and quietly destroy a preview somebody is still looking at."""
    for n in range(app.BATCH_LIMIT):
        app.remember_batch(a_batch(f"batch_{n}"), ASKED)

    assert len(app.BATCHES) == app.BATCH_LIMIT
    assert "batch_0" in app.BATCHES


def test_a_preview_pushed_out_by_the_limit_can_no_longer_be_confirmed(
    live: app.Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The size is not the claim. What the person gets is.

    An evicted batch has to read as "that preview expired" — the same answer as
    a foreign one — rather than confirming a list nobody was shown.
    """
    acting_as(monkeypatch, ASKED)
    for n in range(app.BATCH_LIMIT + 1):
        app.remember_batch(a_batch(f"batch_{n}"), ASKED)

    assert app.batch_for("batch_0", live) is None
    survivor = app.batch_for(f"batch_{app.BATCH_LIMIT}", live)
    assert survivor is not None, "the newest preview was evicted instead"
    assert survivor.batch_id == f"batch_{app.BATCH_LIMIT}"


# ---- the previewed deletion that falls out of a full cache ------------------


def test_the_deletion_plan_past_the_limit_pushes_the_oldest_plan_out() -> None:
    """Line 637. The same bound over `DELETIONS`, which merged before threads
    did and got its lock afterwards; nothing had ever crossed the limit."""
    for n in range(app.DELETION_LIMIT + 1):
        app.remember_deletion(a_plan(f"erase_{n}"), ASKED)

    assert len(app.DELETIONS) == app.DELETION_LIMIT
    assert "erase_0" not in app.DELETIONS
    assert f"erase_{app.DELETION_LIMIT}" in app.DELETIONS


def test_the_control_exactly_the_limit_of_deletion_plans_evicts_nothing() -> None:
    """THE CONTROL, for the reason the batch one exists."""
    for n in range(app.DELETION_LIMIT):
        app.remember_deletion(a_plan(f"erase_{n}"), ASKED)

    assert len(app.DELETIONS) == app.DELETION_LIMIT
    assert "erase_0" in app.DELETIONS


def test_a_deletion_plan_pushed_out_by_the_limit_can_no_longer_be_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The numbers on an aged-out plan may no longer be the numbers that would
    be destroyed, so it must not be confirmable. Taking a fresh preview is
    exactly what somebody should be made to do."""
    acting_as(monkeypatch, ASKED)
    for n in range(app.DELETION_LIMIT + 1):
        app.remember_deletion(a_plan(f"erase_{n}"), ASKED)

    assert app.deletion_for("erase_0") is None
    assert app.deletion_for(f"erase_{app.DELETION_LIMIT}") is not None


# ---- a deletion confirmed by somebody the plan was not shown to -------------


def test_a_deletion_plan_and_a_caller_naming_different_tenants_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Line 713, and the harder half of it.

    The user check above it passes — same `user_id` — so this is the only guard
    left between one customer's plan and another customer's request. It cannot
    happen today because a user row carries its tenant, which is precisely why
    it has never been executed and why deleting it would be free.
    """
    app.remember_deletion(a_plan("erase_x", tenant=ASKED.tenant_id), ASKED)
    acting_as(monkeypatch, IMPOSTOR)

    assert app.deletion_for("erase_x") is None
    assert "erase_x" in app.DELETIONS, "somebody else's request destroyed the plan"


def test_a_deletion_plan_remembered_outside_a_request_is_confirmable_by_nobody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of line 713: `who is None`.

    A plan remembered with no principal is owned by `NOT_RECORDED`, which the
    user check then MATCHES for a caller who is also nobody. Without the `who
    is None` arm that pair would walk straight through to a deletion no
    credential stands behind.
    """
    app.remember_deletion(a_plan("erase_y"))
    acting_as(monkeypatch, None)

    assert app.deletion_for("erase_y") is None
    assert "erase_y" in app.DELETIONS


def test_a_colleague_in_the_same_tenant_cannot_confirm_a_plan_they_never_saw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user check, which is the one that fires in practice.

    Deliberately stricter than `draft_for`, which stops at the tenant: two
    colleagues may finish each other's half-typed entry, and neither may close
    the other's account on a list only one of them read.
    """
    app.remember_deletion(a_plan("erase_z", tenant=ASKED.tenant_id), ASKED)
    acting_as(monkeypatch, COLLEAGUE)

    assert app.deletion_for("erase_z") is None
    assert "erase_z" in app.DELETIONS


def test_the_control_the_person_who_asked_for_the_plan_is_given_it_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE CONTROL on all three refusals above. A `deletion_for` that returned
    None unconditionally passes every one of them and breaks the product.

    The second call is not decoration: a confirmation may be honoured exactly
    once, and get-then-pop under one lock is what makes two simultaneous
    confirmations of one plan into one deletion.
    """
    plan = a_plan("erase_ok", tenant=ASKED.tenant_id)
    app.remember_deletion(plan, ASKED)
    acting_as(monkeypatch, ASKED)

    assert app.deletion_for("erase_ok") is plan
    assert app.deletion_for("erase_ok") is None, "the plan was honoured twice"
    assert "erase_ok" not in app.DELETIONS


# ---- Tally refusing to say which companies are open -------------------------


def test_a_tally_that_will_not_list_its_companies_refuses_the_request(
    live: app.Runtime,
) -> None:
    """Lines 850-851. Every request pays a round trip to ask "which books am I
    writing into"; this is what happens when that question gets no answer.

    It must NOT be a traceback and a dropped socket, and it must name the
    underlying failure, because "Tally would not answer" and "Tally answered
    something else" send a person to two different places.
    """
    deaf = replace(live, client=TallyThatWillNotSayWhatIsOpen())

    with pytest.raises(RuntimeError) as refused:
        deaf.confirm_company()

    said = str(refused.value)
    assert said.startswith(app.REFUSAL)
    assert app.COMPANY in said, "the refusal names no company"
    assert "ConnectionResetError" in said, "the underlying failure is not named"


def test_the_control_a_tally_that_answers_confirms_the_company_silently(
    live: app.Runtime,
) -> None:
    """THE CONTROL. A `confirm_company` that raised on every request would pass
    the test above and serve nobody."""
    assert app.COMPANY in live.client.list_companies()

    live.confirm_company()


def test_the_control_a_company_closed_in_tally_refuses_with_a_different_sentence(
    live: app.Runtime,
) -> None:
    """THE DISCONFIRMING HALF. Tally answering, and the answer not containing
    us, is a different fact from Tally not answering — and the person can act
    on it, which is why the refusal lists what IS open.

    A single catch-all refusal would satisfy the exception test above and tell
    somebody whose company is simply closed to go and check their network.
    """
    elsewhere = FakeTally()
    elsewhere.add_company("Some Other Books")
    gone = replace(live, client=elsewhere)

    with pytest.raises(RuntimeError) as refused:
        gone.confirm_company()

    said = str(refused.value)
    assert "no longer open" in said
    assert "Some Other Books" in said, "the refusal does not say what IS open"
    assert "ConnectionResetError" not in said


# ---- memory filed under a key that is not the key of its own name -----------


def memory_keyed_to(state: app.Runtime, key: str) -> CompanyMemory:
    """This company's memory, filed under a scope key that is not its own.

    `CompanyIdentity.__post_init__` refuses that pair outright, so it is built
    honestly and then forced. That is the point rather than a workaround: the
    guard under test is the LAST one, `configure()` builds both names from a
    single string, and a check that can only be reached through a construction
    site which forbids it is a check nobody can prove still works.
    """
    forged = CompanyIdentity.from_name(state.company)
    object.__setattr__(forged, "key", key)
    return CompanyMemory(replace(state.memory.report, identity=forged), state.store)


def test_memory_whose_key_is_not_the_key_of_its_own_name_is_a_disagreement(
    live: app.Runtime,
) -> None:
    """Line 943. Every stored row and every audit row is filed under this key.

    Wrong, it files a customer's history under a company nobody asked about —
    and it does so silently, because both names still read correctly on screen.
    """
    wrong = replace(live, memory=memory_keyed_to(live, "somebody-elses-key"))

    said = app.company_mismatch(wrong)

    assert "somebody-elses-key" in said, "the refusal does not name the bad key"
    assert normalise_company(app.COMPANY) in said, "nor the key it should be"
    assert app.COMPANY in said


def test_the_control_an_honest_runtime_reports_no_disagreement_at_all(
    live: app.Runtime,
) -> None:
    """THE CONTROL. `company_mismatch` runs on every `runtime()` call, so one
    that reported trouble unconditionally would refuse every request."""
    assert app.company_mismatch(live) == ""


def test_a_key_disagreement_refuses_at_the_point_of_use_and_is_recorded_once(
    live: app.Runtime,
) -> None:
    """The sentence has to reach the person AND the durable log.

    Recorded ONCE: a mismatch is a standing condition, `runtime()` is called
    several times per request, and one fact buried under forty copies of itself
    is a log nobody reads.
    """
    app.install(replace(live, memory=memory_keyed_to(live, "somebody-elses-key")))

    for _ in range(3):
        with pytest.raises(RuntimeError, match=app.REFUSAL):
            app.runtime()

    rows = [
        r for r in live.store.actions(app.COMPANY) if r.action == app.COMPANY_MISMATCH
    ]
    assert len(rows) == 1, f"{len(rows)} rows for one standing fault"
    assert "somebody-elses-key" in rows[0].reason


# ---- REVIEW NOTES -----------------------------------------------------------
#
# Read back cold, as somebody who had not written it. Six concrete faults, the
# first four fixed above and the last two left with a stated reason.
#
# 1. FIXED. The eviction tests originally asserted only `len(...)` and which
#    keys remained — a size check, not a behaviour check. A cache that kept the
#    right NUMBER of entries while handing out a stale one passes that and is
#    the bug worth catching. `test_a_preview_pushed_out_by_the_limit_can_no
#    _longer_be_confirmed` and its deletion twin now go through `batch_for` and
#    `deletion_for`, so what is measured is what the person gets.
#
# 2. FIXED. The exception test asserted only that `RuntimeError` was raised.
#    `confirm_company` has TWO refusal paths a few lines apart — "Tally would
#    not answer" and "Tally answered and your company is not in it" — and any
#    RuntimeError satisfies both. `test_the_control_a_company_closed_in_tally
#    _refuses_with_a_different_sentence` pins them apart, in both directions,
#    so a single catch-all refusal fails here rather than reading as a pass.
#
# 3. FIXED. `test_the_control_the_person_who_asked_for_the_plan_is_given_it
#    _once` was one assertion — the plan comes back. That passes for a
#    `deletion_for` that never pops, which is a double deletion. It now asks
#    twice and checks the cache is empty afterwards.
#
# 4. FIXED. `no_cache_leaks` cleared only on the way out. Under `-n auto` a
#    worker serves several files in turn, so "there are exactly twenty" could
#    have been measuring somebody else's leftovers and would have failed only
#    intermittently. It clears both sides now.
#
# 5. NOT DONE, and stated rather than hidden. `memory_keyed_to` forces a value
#    past `CompanyIdentity.__post_init__` with `object.__setattr__`. That is
#    reaching around a validator, and if the validator is ever what changes,
#    this test keeps passing against a shape the product can no longer build.
#    The alternative — a second construction site in the shipped code that can
#    produce the bad pair — is worse: it would make the unreachable branch
#    reachable in production to make it reachable in a test.
#
# 6. NOT DONE. `IMPOSTOR` shares a `user_id` across two tenants, which the
#    database cannot represent. The test is therefore about the guard and not
#    about a reachable state, and it says so in its own docstring. Deleting it
#    would delete the only execution of line 713's tenant arm; asserting the
#    database forbids the pair belongs to `tests/test_auth.py`, which already
#    walks the schema.

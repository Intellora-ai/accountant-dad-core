"""Deleting a customer, and the three different things that has to mean.

    tenant, app_user   SOFT deleted. `deleted_at` set, every session revoked,
                       and authentication refused afterwards.
    the learned index   HARD deleted. Vendor and phrase history, the cached
                       chart, the bootstrap row. Ours, derived from their books,
                       and rebuildable from their own Tally at any time.
    `action_log`        KEPT, vendor and amount fields included, and MARKED as
                       belonging to a closed account.

THE ONE THIS FILE EXISTS FOR
----------------------------
Both halves are failures, and they fail in opposite directions:

    a deletion that erases the audit log destroys the evidence of what was done
    to a real business's statutory books — the one record a regulator, an
    auditor or the customer themselves would ask for;

    a deletion that keeps everything is not a deletion, and saying otherwise to
    a customer is the kind of sentence that becomes a legal problem.

So each half is measured here separately. Nothing in this file passes because
the other half held.

AND THE ROUTE DELETES ONLY THE CALLER'S OWN CUSTOMER RECORD. The tenant comes
off the credential, never out of the request — the rule `docs/AUTH.md` exists to
enforce, arriving at the most destructive route in the product. There is a
behavioural test (post another tenant's id and watch it be ignored) and an AST
scan (no tenant id is read from a form anywhere in the module), because a
behavioural test proves today's handler and the scan proves tomorrow's.

WHAT IS NOT PROVED HERE
-----------------------
FakeTally throughout, so nothing here says anything about a real TallyPrime.

Nothing here says anything about BACKUPS. Whether a copy of a deleted row
survives in one, where, and for how long, is D-17 in `docs/DATA_POLICY.md` and
is unanswered — so no test in this file claims a row is gone from anywhere
except the primary store, and the screen does not claim it either.
"""

from __future__ import annotations

import ast
import datetime
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pytest

from accountant import auth
from accountant.auth import identity as ident
from accountant.memory import store as st
from accountant.memory.identity import CompanyIdentity, normalise_company
from accountant.memory.store import (
    IN_MEMORY,
    BootstrapReport,
    BootstrapStatus,
    MemoryStore,
    Observation,
)
from accountant.schema import ActionLog
from accountant.web import app
from tests.test_auth import PASSWORD
from tests.test_web import demo_company, fake_backend, serving

NOW = datetime.datetime(2026, 8, 11, 9, 0, tzinfo=datetime.UTC)
LATER = NOW + datetime.timedelta(hours=1)
NEXT_WEEK = NOW + datetime.timedelta(days=7)
DELETED_AT = LATER.isoformat()

ALPHA = "tenant-alpha"
BETA = "tenant-beta"
#: One tenant, two colleagues — the shape of an accounts department, and the
#: shape the preview→confirm binding has to survive.
ANNA = "user-anna"
BILAL = "user-bilal"

#: An amount, in the `reason` of a kept row. `ActionLog` has no money column —
#: `docs/DATA_POLICY.md` row 8 records that amounts reach the local action log
#: through the reason and the detail — so this is what "the amount fields are
#: kept" is actually asserted on.
A_REASON = "paid Sharma Traders 4,200.00 for cement; every check passed"
A_VENDOR = "Sharma Traders"


# ---------------------------------------------------------------------------
# fixtures for the store half
# ---------------------------------------------------------------------------


def a_customer(
    store: MemoryStore,
    tenant_id: str,
    name: str,
    *,
    user_id: str = "",
) -> str:
    """One tenant, one user, one live session. Returns the session token."""
    store.create_tenant(tenant_id, name, NOW.isoformat())
    who = user_id or f"user-{tenant_id}"
    digest, salt = auth.hash_password(PASSWORD)
    store.create_user(
        who, tenant_id, f"{who}@{tenant_id}.test", digest, salt, NOW.isoformat()
    )
    token = auth.new_token()
    store.open_session(
        auth.token_fingerprint(token),
        who,
        tenant_id,
        NOW.isoformat(),
        NEXT_WEEK.isoformat(),
    )
    return token


def learned(store: MemoryStore, company: str) -> str:
    """A company this store has learned something about. Returns its key.

    All four erasable tables are filled, because "the learned index is gone"
    means all four and a test that only checked `vendor_account` would pass with
    a company row and a chart still sitting there.
    """
    identity = CompanyIdentity.from_name(company)
    store.save_bootstrap(
        BootstrapReport(
            identity=identity,
            status=BootstrapStatus.READY,
            detail="read for this test",
            attempted_at=NOW.isoformat(),
            bootstrapped_at=NOW.isoformat(),
        ),
        chart=("Purchases", "Cash"),
        vendors=(
            Observation(
                company_key=identity.key,
                subject="sharma traders",
                account="Purchases",
                times=3,
                provenance="tally_history",
                raw_subject=A_VENDOR,
            ),
        ),
        phrases=(
            Observation(
                company_key=identity.key,
                subject="cement",
                account="Purchases",
                times=2,
                provenance="tally_history",
            ),
        ),
    )
    return identity.key


def worked_in(
    store: MemoryStore, tenant_id: str, company_key: str, *, user_id: str = ""
) -> None:
    """One audit row tying a customer to a company.

    This IS the tenant→company link. Nothing in the schema declares which
    companies a tenant owns; `action_log` is the only table where the two ids
    sit on one row, so it is what a deletion is scoped by — and what a deletion
    would make impossible to scope if it erased it.
    """
    store.record_action(
        ActionLog(
            ts=NOW,
            action="posted",
            company_key=company_key,
            outcome="POSTED",
            reason=A_REASON,
            run_id="run-1",
            backend="FakeTally",
            operation_id="ad_op_1",
            vendor_id=A_VENDOR,
            tenant_id=tenant_id,
            user_id=user_id or f"user-{tenant_id}",
        )
    )


# ---------------------------------------------------------------------------
# 1. the soft half — the account closes and stays closed
# ---------------------------------------------------------------------------


def test_a_deleted_customer_can_no_longer_authenticate() -> None:
    """The whole point of a soft delete: the row survives and the access does not."""
    store = MemoryStore(IN_MEMORY)
    token = a_customer(store, ALPHA, "Alpha Traders")
    assert auth.authenticate(token, store).tenant_id == ALPHA

    store.delete_tenant(ALPHA, DELETED_AT)

    with pytest.raises(auth.AuthRefusal) as refused:
        auth.authenticate(token, store)
    assert refused.value.status == 401


def test_a_session_opened_after_the_deletion_still_cannot_authenticate() -> None:
    """The backstop, and the only test that can see it.

    Every session that EXISTED is revoked in the same transaction that closes
    the account, so the test above passes on the revocation alone and says
    nothing about the tenant check in `authenticate`. This one opens a session
    the revocation could not have reached — a request already in flight, or a
    later code path writing a session row without asking whether the account is
    still open — and it must still be refused.
    """
    store = MemoryStore(IN_MEMORY)
    a_customer(store, ALPHA, "Alpha Traders")
    store.delete_tenant(ALPHA, DELETED_AT)

    late = auth.new_token()
    store.open_session(
        auth.token_fingerprint(late),
        f"user-{ALPHA}",
        ALPHA,
        NOW.isoformat(),
        NEXT_WEEK.isoformat(),
    )

    with pytest.raises(auth.AuthRefusal) as refused:
        auth.authenticate(late, store)
    assert refused.value.status == 401
    assert "deleted" in refused.value.reason


def test_every_session_of_a_deleted_customer_dies_the_moment_it_happens() -> None:
    """Not at the next expiry. One survivor is an account that is still open."""
    store = MemoryStore(IN_MEMORY)
    tokens = [a_customer(store, ALPHA, "Alpha Traders")]
    for _ in range(3):
        extra = auth.new_token()
        store.open_session(
            auth.token_fingerprint(extra),
            f"user-{ALPHA}",
            ALPHA,
            NOW.isoformat(),
            NEXT_WEEK.isoformat(),
        )
        tokens.append(extra)
    assert len(store.live_sessions_of_tenant(ALPHA)) == 4

    done = store.delete_tenant(ALPHA, DELETED_AT)

    assert done.sessions_revoked == 4
    assert store.live_sessions_of_tenant(ALPHA) == ()
    for token in tokens:
        with pytest.raises(auth.AuthRefusal):
            auth.authenticate(token, store)


def test_the_session_row_says_when_it_was_revoked_rather_than_vanishing() -> None:
    """A deleted row and a session that never existed are indistinguishable, and
    support has to be able to tell them apart."""
    store = MemoryStore(IN_MEMORY)
    token = a_customer(store, ALPHA, "Alpha Traders")

    store.delete_tenant(ALPHA, DELETED_AT)

    row = store.session_by_fingerprint(auth.token_fingerprint(token))
    assert row is not None, "the session row was destroyed instead of revoked"
    assert row.revoked_at == DELETED_AT


def test_a_deleted_customers_user_cannot_sign_in_again() -> None:
    """The password still verifies. What refuses is the deletion, not a
    scrambled hash — so the refusal cannot be mistaken for a broken account."""
    store = MemoryStore(IN_MEMORY)
    a_customer(store, ALPHA, "Alpha Traders")

    store.delete_tenant(ALPHA, DELETED_AT)

    user = store.user_by_email(f"user-{ALPHA}@{ALPHA}.test")
    assert user is not None, "the user row was destroyed instead of closed"
    assert user.live is False
    assert user.deleted_at == DELETED_AT
    assert auth.verify_password(PASSWORD, user.password_hash, user.salt) is True


def test_the_tenant_row_records_when_the_account_was_closed() -> None:
    store = MemoryStore(IN_MEMORY)
    a_customer(store, ALPHA, "Alpha Traders")

    store.delete_tenant(ALPHA, DELETED_AT)

    tenant = store.tenant(ALPHA)
    assert tenant is not None
    assert tenant.live is False
    assert tenant.deleted_at == DELETED_AT
    assert store.deleted_tenants() == {ALPHA: DELETED_AT}


def test_a_second_deletion_changes_nothing_and_keeps_the_first_time() -> None:
    """When a customer was deleted is a fact and is not improved by being
    overwritten by whoever pressed the button twice."""
    store = MemoryStore(IN_MEMORY)
    a_customer(store, ALPHA, "Alpha Traders")
    first = store.delete_tenant(ALPHA, DELETED_AT)

    again = store.delete_tenant(ALPHA, NEXT_WEEK.isoformat())

    assert first.users_closed == 1
    assert first.sessions_revoked == 1
    assert again.users_closed == 0
    assert again.sessions_revoked == 0
    tenant = store.tenant(ALPHA)
    assert tenant is not None
    assert tenant.deleted_at == DELETED_AT


# ---------------------------------------------------------------------------
# 2. the hard half — our learning about them goes
# ---------------------------------------------------------------------------


def test_the_learned_index_of_a_deleted_customer_is_erased() -> None:
    """All four tables. A test that checked only the vendor history would pass
    with the chart and the bootstrap row still sitting there."""
    store = MemoryStore(IN_MEMORY)
    a_customer(store, ALPHA, "Alpha Traders")
    key = learned(store, "Alpha Traders")
    worked_in(store, ALPHA, key)
    assert store.vendors(key) and store.phrases(key) and store.chart(key)
    assert store.state(key) is not None

    done = store.delete_tenant(ALPHA, DELETED_AT)

    assert done.companies_erased == (key,)
    assert store.vendors(key) == ()
    assert store.phrases(key) == ()
    assert store.chart(key) == ()
    assert store.state(key) is None
    # One vendor row, one phrase row, two chart rows and the bootstrap row.
    # Counted rather than described: "the index is gone" is easy to believe
    # about four tables and hard to believe about a number that has to add up.
    assert done.rows_erased == 5


def test_a_customer_who_worked_in_no_company_erases_nothing_and_still_closes() -> None:
    """The account is closed whether or not there was anything learned. A
    deletion that only works for customers with history is not a deletion."""
    store = MemoryStore(IN_MEMORY)
    a_customer(store, ALPHA, "Alpha Traders")

    done = store.delete_tenant(ALPHA, DELETED_AT)

    assert done.companies_erased == ()
    assert done.rows_erased == 0
    assert done.users_closed == 1
    tenant = store.tenant(ALPHA)
    assert tenant is not None and tenant.live is False


# ---------------------------------------------------------------------------
# 3. the kept half — the audit trail survives, and is marked
# ---------------------------------------------------------------------------


def test_the_action_log_survives_a_deletion() -> None:
    """The evidence of what we did to a real business's books.

    Read back through the ordinary company-scoped read, which is the one every
    screen and every report already uses: if the rows were gone, this is where
    it would show.
    """
    store = MemoryStore(IN_MEMORY)
    a_customer(store, ALPHA, "Alpha Traders")
    key = learned(store, "Alpha Traders")
    worked_in(store, ALPHA, key)

    store.delete_tenant(ALPHA, DELETED_AT)

    (row,) = store.actions("Alpha Traders")
    assert row.action == "posted"
    assert row.tenant_id == ALPHA
    assert row.user_id == f"user-{ALPHA}"


def test_a_kept_row_keeps_its_vendor_and_its_amount() -> None:
    """Owner decision, not reopened here. A trail with the supplier and the
    figure stripped out cannot answer what was done to somebody's accounts,
    which is the only reason it is being kept at all."""
    store = MemoryStore(IN_MEMORY)
    a_customer(store, ALPHA, "Alpha Traders")
    key = learned(store, "Alpha Traders")
    worked_in(store, ALPHA, key)

    store.delete_tenant(ALPHA, DELETED_AT)

    (row,) = store.actions("Alpha Traders")
    assert row.vendor_id == A_VENDOR
    assert row.reason == A_REASON
    assert "4,200.00" in row.reason


def test_every_kept_row_is_marked_as_belonging_to_a_deleted_customer() -> None:
    """So nobody reads retained evidence as an active customer.

    Both reads carry the mark: the tenant-scoped one a deletion report uses, and
    the company-scoped one a screen uses. A mark on only one of them is a mark
    the reader who needs it does not get.
    """
    store = MemoryStore(IN_MEMORY)
    a_customer(store, ALPHA, "Alpha Traders")
    key = learned(store, "Alpha Traders")
    worked_in(store, ALPHA, key)

    before = store.retained_actions("Alpha Traders")
    assert [r.tenant_deleted for r in before] == [False], "the control"

    store.delete_tenant(ALPHA, DELETED_AT)

    company_scoped = store.retained_actions("Alpha Traders")
    tenant_scoped = store.actions_of_tenant(ALPHA)
    for read in (company_scoped, tenant_scoped):
        assert read, "the marked read returned nothing at all"
        for kept in read:
            assert kept.tenant_deleted is True
            assert kept.tenant_deleted_at == DELETED_AT
            assert kept.entry.vendor_id == A_VENDOR


def test_the_mark_is_derived_and_no_column_was_added_to_carry_it() -> None:
    """The append-only property is the reason the mark is derived.

    A stored mark needs an UPDATE on `action_log`, and a row a later write can
    edit is not an audit row.
    `tests/test_reversal_history.py::test_the_store_has_no_update_or_delete_path_for_the_action_log`
    scans the source for one; this asserts the other half — that the shape of
    the table did not change to make room for it either.
    """
    store = MemoryStore(IN_MEMORY)
    a_customer(store, ALPHA, "Alpha Traders")
    key = learned(store, "Alpha Traders")
    worked_in(store, ALPHA, key)
    before = store.columns_of("action_log")

    store.delete_tenant(ALPHA, DELETED_AT)

    assert store.columns_of("action_log") == before


def test_a_row_that_never_named_a_customer_is_not_marked_as_a_deleted_one() -> None:
    """`NOT_RECORDED` means nobody wrote it down. It cannot be marked as
    belonging to a deleted anybody, and inventing that would be the same lie as
    back-filling an actor."""
    store = MemoryStore(IN_MEMORY)
    a_customer(store, ALPHA, "Alpha Traders")
    key = learned(store, "Alpha Traders")
    worked_in(store, ALPHA, key)
    store.record_action(
        ActionLog(
            ts=NOW,
            action="failed",
            company_key=key,
            outcome="FAILED",
            reason="a script with no session behind it",
            run_id="run-1",
            backend="FakeTally",
        )
    )

    store.delete_tenant(ALPHA, DELETED_AT)

    kept = store.retained_actions("Alpha Traders")
    assert {r.entry.action: r.tenant_deleted for r in kept} == {
        "posted": True,
        "failed": False,
    }


# ---------------------------------------------------------------------------
# 4. one customer's deletion touches no other customer
# ---------------------------------------------------------------------------


def test_one_customers_deletion_leaves_another_customers_rows_alone() -> None:
    """Every table, both halves. This is the assertion the whole feature is
    dangerous without."""
    store = MemoryStore(IN_MEMORY)
    a_customer(store, ALPHA, "Alpha Traders")
    beta_token = a_customer(store, BETA, "Beta Works")
    alpha_key = learned(store, "Alpha Traders")
    beta_key = learned(store, "Beta Works")
    worked_in(store, ALPHA, alpha_key)
    worked_in(store, BETA, beta_key)

    store.delete_tenant(ALPHA, DELETED_AT)

    beta = store.tenant(BETA)
    assert beta is not None and beta.live is True
    (beta_user,) = store.users_of_tenant(BETA)
    assert beta_user.live is True
    assert len(store.live_sessions_of_tenant(BETA)) == 1
    assert auth.authenticate(beta_token, store).tenant_id == BETA
    assert store.vendors(beta_key), "Beta's learned index went with Alpha's"
    assert store.phrases(beta_key)
    assert store.chart(beta_key)
    assert store.state(beta_key) is not None
    assert len(store.actions("Beta Works")) == 1
    assert [r.tenant_deleted for r in store.actions_of_tenant(BETA)] == [False]


def test_a_company_two_live_customers_share_is_kept_rather_than_erased() -> None:
    """Erasing it would delete somebody else's learning as a side effect of this
    person's request, which is the cross-tenant harm the feature must not commit
    while claiming to protect them."""
    store = MemoryStore(IN_MEMORY)
    a_customer(store, ALPHA, "Alpha Traders")
    a_customer(store, BETA, "Beta Works")
    shared = learned(store, "Shared Books")
    worked_in(store, ALPHA, shared)
    worked_in(store, BETA, shared)

    done = store.delete_tenant(ALPHA, DELETED_AT)

    assert done.companies_erased == ()
    assert done.companies_kept == (shared,)
    assert store.vendors(shared), "the other live customer's index was erased"
    assert store.state(shared) is not None
    assert "another live customer shares them" in done.summary()


def test_a_company_shared_only_with_an_already_deleted_customer_is_erased() -> None:
    """The index goes when the LAST live owner goes. Kept forever otherwise:
    two deleted customers would leave an index nobody owns and nobody can ask
    to have removed."""
    store = MemoryStore(IN_MEMORY)
    a_customer(store, ALPHA, "Alpha Traders")
    a_customer(store, BETA, "Beta Works")
    shared = learned(store, "Shared Books")
    worked_in(store, ALPHA, shared)
    worked_in(store, BETA, shared)
    assert store.delete_tenant(ALPHA, DELETED_AT).companies_erased == ()

    second = store.delete_tenant(BETA, NEXT_WEEK.isoformat())

    assert second.companies_erased == (shared,)
    assert store.vendors(shared) == ()
    assert store.state(shared) is None


# ---------------------------------------------------------------------------
# 5. what may not be deleted at all
# ---------------------------------------------------------------------------


def test_a_tenant_with_no_customer_record_cannot_be_half_deleted() -> None:
    """There is nowhere to record that the deletion happened, so nothing is
    erased. An index gone with nothing saying who asked or when is worse than
    either end state."""
    store = MemoryStore(IN_MEMORY)
    key = learned(store, "Alpha Traders")
    worked_in(store, "a-tenant-nobody-created", key)

    with pytest.raises(ValueError, match="no customer record"):
        store.delete_tenant("a-tenant-nobody-created", DELETED_AT)

    assert store.vendors(key), "the index was erased by a refused deletion"
    assert store.state(key) is not None


def test_a_deletion_must_name_a_customer_and_a_time() -> None:
    store = MemoryStore(IN_MEMORY)
    a_customer(store, ALPHA, "Alpha Traders")
    with pytest.raises(ValueError, match="must name the customer"):
        store.delete_tenant("   ", DELETED_AT)
    with pytest.raises(ValueError, match="the time it happened"):
        store.delete_tenant(ALPHA, "  ")


# ---------------------------------------------------------------------------
# 6. the policy is data, and it covers the whole schema
# ---------------------------------------------------------------------------


def test_the_deletion_policy_names_every_table_in_the_live_schema() -> None:
    """Exact set equality, read off the live schema.

    A table in neither list fails here, and that is the whole value of the
    test: the next table anybody adds cannot be swept into a customer deletion
    by accident, and cannot be left out of one by accident either.

    THE OPERATION REGISTER IS THE WORKED EXAMPLE. The write-once
    `(company_key, operation_id)` row that closes defect I1 is not in this
    schema yet — it lands with the idempotency task. When it does, this test
    fails until somebody names it, and the answer is already decided: KEPT.
    Releasing a spent operation id would recreate I1 exactly, because two
    vouchers would then share one identity, and that is as true after a customer
    leaves as before.
    """
    store = MemoryStore(IN_MEMORY)
    erased = set(st.ERASED_BY_DELETION)
    kept = set(st.KEPT_BY_DELETION)

    assert set(store.table_names()) == erased | kept
    assert erased & kept == set(), "a table cannot be both erased and kept"


def test_the_erased_tables_are_exactly_the_ones_forget_already_drops() -> None:
    """One list, not two. `forget()` runs on every rebuild and already answers
    "what of ours may be dropped and rebuilt from their books"; a deletion asks
    the same question, so it uses the same statements rather than a second copy
    that can drift."""
    # The table name is PARSED out of each statement rather than searched for,
    # so this cannot pass on a statement that merely mentions the word.
    targets = {statement.upper().split()[2] for statement in st.LEARNED_INDEX_DELETES}

    assert targets == {table.upper() for table in st.ERASED_BY_DELETION}
    assert len(st.LEARNED_INDEX_DELETES) == len(st.ERASED_BY_DELETION), (
        "one statement each"
    )
    assert targets.isdisjoint({table.upper() for table in st.KEPT_BY_DELETION})


# ---------------------------------------------------------------------------
# the route, over real HTTP
# ---------------------------------------------------------------------------
#
# Through `tests/test_web.py::serving`, which is the ONE spin-up path in this
# suite. A second threaded server here would be a second definition of what a
# running app is, and the two would drift.


@pytest.fixture(autouse=True)
def production_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authentication REQUIRED, as in production.

    `tests/conftest.py` sets LOCAL_DEV_MODE=1 for the suite. Here two different
    customers, and two different colleagues inside one customer, are the whole
    subject — and dev mode has exactly one identity, so the measurement would
    collapse into itself and pass by distinguishing nothing.
    """
    monkeypatch.delenv(ident.ENV_LOCAL_DEV_MODE, raising=False)


def seeding(*sessions: tuple[str, str, str]) -> Callable[[MemoryStore], None]:
    """Seed callback: `(token, tenant_id, user_id)` triples, tenants and users
    created for whatever they name.

    Runs INSIDE the serving thread, because SQLite hands a connection to the
    thread that opened it. The tokens are made in the test and passed in, so
    nothing has to be read back out across the boundary.
    """

    def seed(store: MemoryStore) -> None:
        for tenant_id, name in ((ALPHA, "Alpha Traders"), (BETA, "Beta Works")):
            store.create_tenant(tenant_id, name, NOW.isoformat())
        made: set[str] = set()
        for _token, tenant_id, user_id in sessions:
            if user_id in made:
                continue
            made.add(user_id)
            digest, salt = auth.hash_password(PASSWORD)
            store.create_user(
                user_id,
                tenant_id,
                f"{user_id}@{tenant_id}.test",
                digest,
                salt,
                NOW.isoformat(),
            )
        for token, tenant_id, user_id in sessions:
            store.open_session(
                auth.token_fingerprint(token),
                user_id,
                tenant_id,
                NOW.isoformat(),
                NEXT_WEEK.isoformat(),
            )

    return seed


def as_user(base: str, path: str, token: str, **fields: str) -> tuple[int, str]:
    """POST as one particular person. Returns the status even when it refuses."""
    request = urllib.request.Request(  # noqa: S310 - loopback, http
        base + path, data=urllib.parse.urlencode(fields).encode()
    )
    if token:
        request.add_header("Cookie", f"{app.COOKIE}={token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as answer:  # noqa: S310
            return answer.status, answer.read().decode()
    except urllib.error.HTTPError as refused:
        return refused.status or 0, refused.read().decode()


def get_as(base: str, token: str, path: str = "/") -> int:
    request = urllib.request.Request(base + path)  # noqa: S310 - loopback, http
    if token:
        request.add_header("Cookie", f"{app.COOKIE}={token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as answer:  # noqa: S310
            answer.read()
            return answer.status
    except urllib.error.HTTPError as refused:
        refused.read()
        return refused.status or 0


def an_entry(base: str, token: str) -> None:
    """Post one entry, which is what puts a `(tenant_id, company_key)` row in the
    audit log — the only thing that ties this customer to these books."""
    status, body = as_user(
        base, "/entry", token, text="paid Sharma Traders 4200 for cement"
    )
    assert status == 200, body
    assert "posted" in body.lower(), body


def plan_id(page: str) -> str:
    marker = 'name=plan value="'
    start = page.index(marker) + len(marker)
    return page[start : page.index('"', start)]


def test_an_unauthenticated_caller_cannot_delete_anything() -> None:
    """Both steps. The confirm step especially: a 401 on the preview and a 200
    on the confirm would be a delete-anything button with a locked front door."""
    anna = auth.new_token()
    with serving(
        demo_company(), fake_backend(), seed=seeding((anna, ALPHA, ANNA))
    ) as base:
        an_entry(base, anna)
        for fields in ({}, {"confirm": "yes", "plan": "anything"}):
            status, _body = as_user(base, "/delete-my-data", "", **fields)
            assert status == 401, fields


def test_the_first_request_only_previews_and_deletes_nothing(tmp_path: Path) -> None:
    """Step one writes nothing. If it did, the two-step design would be
    decoration and the confirmation would be confirming something already done.
    """
    anna = auth.new_token()
    db = tmp_path / "app.db"
    with serving(
        demo_company(),
        fake_backend(),
        seed=seeding((anna, ALPHA, ANNA)),
        store_path=db,
    ) as base:
        an_entry(base, anna)

        status, body = as_user(base, "/delete-my-data", anna)

        assert status == 200
        assert 'data-deletion="preview"' in body
        assert 'data-users="1"' in body
        assert get_as(base, anna) == 200, "the preview ended the session"

    after = MemoryStore(db)
    tenant = after.tenant(ALPHA)
    assert tenant is not None and tenant.live is True
    assert after.live_sessions_of_tenant(ALPHA), "the preview revoked a session"
    assert after.vendors(normalise_company(app.COMPANY)), "the preview erased the index"


def test_the_person_who_asked_can_confirm_and_the_data_goes(tmp_path: Path) -> None:
    """The control. Without it every refusal test below passes on a route that
    refuses everybody, which measures nothing."""
    anna = auth.new_token()
    db = tmp_path / "app.db"
    with serving(
        demo_company(),
        fake_backend(),
        seed=seeding((anna, ALPHA, ANNA)),
        store_path=db,
    ) as base:
        an_entry(base, anna)
        status, preview = as_user(base, "/delete-my-data", anna)
        assert status == 200

        status, body = as_user(
            base, "/delete-my-data", anna, confirm="yes", plan=plan_id(preview)
        )

        assert status == 200
        assert 'data-deletion="done"' in body

    after = MemoryStore(db)
    key = normalise_company(app.COMPANY)
    tenant = after.tenant(ALPHA)
    assert tenant is not None and tenant.live is False
    assert after.live_sessions_of_tenant(ALPHA) == ()
    assert after.vendors(key) == ()
    assert after.phrases(key) == ()
    assert after.chart(key) == ()
    assert after.state(key) is None
    kept = after.actions_of_tenant(ALPHA)
    assert kept, "the audit trail went with the deletion"
    assert all(row.tenant_deleted for row in kept)


def test_the_session_is_refused_the_moment_the_data_is_deleted() -> None:
    anna = auth.new_token()
    with serving(
        demo_company(), fake_backend(), seed=seeding((anna, ALPHA, ANNA))
    ) as base:
        an_entry(base, anna)
        _status, preview = as_user(base, "/delete-my-data", anna)
        as_user(base, "/delete-my-data", anna, confirm="yes", plan=plan_id(preview))

        assert get_as(base, anna) == 401


def test_a_caller_cannot_delete_a_customer_that_is_not_theirs(tmp_path: Path) -> None:
    """The tenant id is posted, and ignored. Anna asks for Beta to be deleted;
    what is deleted is Alpha, because that is what her credential says."""
    anna, beta = auth.new_token(), auth.new_token()
    db = tmp_path / "app.db"
    with serving(
        demo_company(),
        fake_backend(),
        seed=seeding((anna, ALPHA, ANNA), (beta, BETA, "user-beta")),
        store_path=db,
    ) as base:
        an_entry(base, anna)

        status, preview = as_user(
            base, "/delete-my-data", anna, tenant=BETA, tenant_id=BETA
        )
        assert status == 200
        assert "Alpha Traders" in preview
        assert "Beta Works" not in preview

        status, body = as_user(
            base,
            "/delete-my-data",
            anna,
            confirm="yes",
            plan=plan_id(preview),
            tenant=BETA,
            tenant_id=BETA,
        )
        assert status == 200, body
        assert get_as(base, beta) == 200, "Beta's session was killed by Anna"

    after = MemoryStore(db)
    beta_row = after.tenant(BETA)
    assert beta_row is not None and beta_row.live is True
    assert len(after.live_sessions_of_tenant(BETA)) == 1
    assert after.users_of_tenant(BETA)[0].live is True
    alpha_row = after.tenant(ALPHA)
    assert alpha_row is not None and alpha_row.live is False


def test_a_colleague_cannot_confirm_a_deletion_they_were_never_shown(
    tmp_path: Path,
) -> None:
    """Anna asks, Bilal confirms with Anna's plan id. Every other check passes —
    Bilal has a live session, the same customer, a real plan — and the account
    must still be open afterwards."""
    anna, bilal = auth.new_token(), auth.new_token()
    db = tmp_path / "app.db"
    with serving(
        demo_company(),
        fake_backend(),
        seed=seeding((anna, ALPHA, ANNA), (bilal, ALPHA, BILAL)),
        store_path=db,
    ) as base:
        an_entry(base, anna)
        status, preview = as_user(base, "/delete-my-data", anna)
        assert status == 200
        held = plan_id(preview)

        status, body = as_user(base, "/delete-my-data", bilal, confirm="yes", plan=held)

        assert status == 200
        assert "had no preview" in body
        assert held in app.DELETIONS, (
            "Anna's pending plan was consumed by somebody else's request"
        )

    after = MemoryStore(db)
    tenant = after.tenant(ALPHA)
    assert tenant is not None and tenant.live is True
    assert after.vendors(normalise_company(app.COMPANY)), "Bilal's click erased it"


def test_a_confirmation_with_no_preview_at_all_deletes_nothing(tmp_path: Path) -> None:
    """A plan id nobody issued must not become a deletion just because the
    caller is signed in."""
    anna = auth.new_token()
    db = tmp_path / "app.db"
    with serving(
        demo_company(),
        fake_backend(),
        seed=seeding((anna, ALPHA, ANNA)),
        store_path=db,
    ) as base:
        an_entry(base, anna)

        status, body = as_user(
            base, "/delete-my-data", anna, confirm="yes", plan="a-plan-nobody-asked-for"
        )

        assert status == 200
        assert "had no preview" in body

    after = MemoryStore(db)
    tenant = after.tenant(ALPHA)
    assert tenant is not None and tenant.live is True
    assert after.vendors(normalise_company(app.COMPANY))


def test_the_deletion_writes_an_audit_row_naming_who_asked_and_what_went(
    tmp_path: Path,
) -> None:
    """Who asked is the first question after any deletion, and the row that
    answers it is itself part of the trail that survives."""
    anna = auth.new_token()
    db = tmp_path / "app.db"
    with serving(
        demo_company(),
        fake_backend(),
        seed=seeding((anna, ALPHA, ANNA)),
        store_path=db,
    ) as base:
        an_entry(base, anna)
        _status, preview = as_user(base, "/delete-my-data", anna)
        as_user(base, "/delete-my-data", anna, confirm="yes", plan=plan_id(preview))

    written = MemoryStore(db).actions(app.COMPANY)
    rows = [r for r in written if r.action == "data_deleted"]
    assert rows, "a deletion wrote no row of its own"
    (row,) = rows
    assert row.tenant_id == ALPHA
    assert row.user_id == ANNA
    assert ANNA in row.reason
    assert "KEPT" in row.reason
    assert "index_rows_erased=" in row.detail
    assert "action_rows_kept=" in row.detail


def test_a_session_with_no_customer_record_behind_it_deletes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """LOCAL_DEV_MODE is the reachable case: the principal is a constant and no
    customer record was ever created for it. Refused, and nothing erased."""
    monkeypatch.setenv(ident.ENV_LOCAL_DEV_MODE, "1")
    db = tmp_path / "app.db"
    with serving(demo_company(), fake_backend(), seed=seeding(), store_path=db) as base:
        an_entry(base, "")

        status, body = as_user(base, "/delete-my-data", "")

        assert status == 400
        assert "no customer account behind this session" in body

    assert MemoryStore(db).vendors(normalise_company(app.COMPANY))


def test_no_tenant_id_is_ever_read_out_of_a_request() -> None:
    """AST, not a substring scan.

    The behavioural test above proves today's handler ignores a posted tenant
    id. This proves the module has no way to read one at all, which is the claim
    that has to keep holding when somebody adds the next route.

    A substring scan was tried twice in this project and was wrong both times:
    once it matched `HTTPServer` inside the paragraph explaining that no
    `HTTPServer` is used, and once it matched a command inside a comment.
    """
    tree = ast.parse(Path(app.__file__).read_text(encoding="utf-8"))
    reads = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = node.func
        if not (isinstance(called, ast.Attribute) and called.attr == "get"):
            continue
        if not (isinstance(called.value, ast.Name) and called.value.id == "form"):
            continue
        reads += 1
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                assert "tenant" not in arg.value.lower(), (
                    f"a tenant id is read out of the request: form.get({arg.value!r})"
                )
    assert reads >= 5, "the scan found no form reads at all, so it proves nothing"

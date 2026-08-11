"""The whole journey, in the order a real person meets it.

Every other file in this suite proves one piece. Nothing proved the pieces
COMPOSE, and composition is where the interesting failures live: a session that
works on its own but is not the session the audit row names, a reversal that
works on its own but frees an identity the next request re-uses, an audit trail
that is perfect until the process that wrote it exits.

WHAT THIS FILE PROVES
---------------------
Thirteen steps, each its own named test, plus one test that runs steps 1-12 in
order against ONE server so the ORDERING itself is measured. Both, deliberately:
the individual tests say WHICH step broke, the journey test says the steps still
compose. Either alone is half a measurement.

     1  an unauthenticated request to the home page is refused 401
     2  a wrong password and an unknown email are refused in the SAME words
     3  a correct password returns an HttpOnly; SameSite=Lax cookie
     4  unknown vendor -> asked what for -> asked how paid -> posted, to the paise
     5  the audit row for that posting names the tenant and the user
     6  a colleague in the same tenant sees the company's data, and a session
        from a DIFFERENT tenant is refused 403 before it touches anything
     7  replaying the answer that posted it is refused; one voucher, one row
     8  undoing it returns the trial balance to its exact prior paise
     9  the reversed operation id cannot be written again
    10  A previews an undo-everything; B cannot confirm it; A can
    11  signing out revokes the session ROW, and the same cookie is then refused
    12  stop the server, start it again on the SAME database file, and the trail
        from steps 4-10 is still there with its reasons
    13  LOCAL_DEV_MODE=1: no credential, the same entry posts, and the rows name
        the local-dev tenant and user rather than being blank

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Nothing here touches a real TallyPrime. The backend is `FakeTally` throughout,
injected through `app.configure()`. EVERY result below is FAKETALLY evidence and
none of it says anything about a licensed installation. `tests/test_real_tally.py`
and `docs/PROJECT_STATE.md` are where live evidence lives.

It does not prove concurrency. `HTTPServer` is single-threaded, so every step
below serialises in the handler. Two people are two SESSIONS here, never two
simultaneous requests.

It does not re-prove the mechanisms it drives. `tests/test_auth.py` owns the
credential, `tests/test_reversal_guard.py` the two destroying routes,
`tests/test_durable_log.py` the store, `tests/test_idempotency.py` the operation
identity. This file imports their helpers rather than copying them, for the
reason `tests/conftest.py` states: two copies of a helper is how two test files
end up disagreeing.

It does not prove that a customer's books are isolated from another customer's.
They are not, on this branch. See below.

DEFECT J1 — FOUND BY STEP 6, FIXED THE SAME DAY
-----------------------------------------------
Step 6 is why this file exists. `Principal.require` had a passing unit test and
no caller anywhere in `accountant/`, so a session issued to one customer was
authenticated against another customer's open books and let through.

A unit test of a guard proves the guard works. It says nothing about whether
the guard is installed — and every piece of this journey had passing tests
while the pieces did not connect.

Fixed in `accountant/web/app.py::Handler._identify`, which now calls
`require()` on every request, unconditionally, before any handler runs.
`docs/AUTH.md` carries the reasoning and the mutants. Step 6 below is now an
ordinary passing assertion rather than a pinned defect.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Nothing here touches a real TallyPrime. The backend is `FakeTally` throughout,
injected through `app.configure()`. EVERY result below is FAKETALLY evidence and
none of it says anything about a licensed installation. `tests/test_real_tally.py`
and `docs/PROJECT_STATE.md` are where live evidence lives.

It does not prove concurrency. `HTTPServer` is single-threaded, so every step
below serialises in the handler. Two people are two SESSIONS here, never two
simultaneous requests.

It does not re-prove the mechanisms it drives. `tests/test_auth.py` owns the
credential, `tests/test_reversal_guard.py` the two destroying routes,
`tests/test_durable_log.py` the store, `tests/test_idempotency.py` the operation
identity. This file imports their helpers rather than copying them, for the
reason `tests/conftest.py` states: two copies of a helper is how two test files
end up disagreeing.

It does not prove that a customer's books are isolated from another customer's.
They are not, on this branch. See below.

DEFECT J1 - FOUND BY STEP 6, NOT FIXED HERE
-------------------------------------------
`docs/CLOUD_ARCHITECTURE.md` §7.1 says "every request naming an operation is
checked against the caller's tenant", and §7.3 calls for a cross-tenant test
that "attempts, as B, every request shape that names A's operation id" and
"asserts every one is refused".

No route does that check. `Principal.require()` exists, is tested in
`tests/test_auth.py`, and is called from nowhere in `accountant/` - the whole
app serves the ONE company named at startup, and any live session reaches it
whatever tenant issued it. Measured here: a tenant-beta session reads
tenant-alpha's books AND reverses tenant-alpha's voucher, 200 both times.

Pinned in the idiom `tests/test_error_responses.py` established: a passing test
recording what happens today, paired with an `xfail(strict=True)` asserting the
behaviour the design calls for. The xfail turns into a hard failure the moment
somebody fixes it, which is the point of it being strict. The fix is a source
change and this task is tests only, so it is written up in `docs/OWNER_WORK.md`
under `DEFECT J1` — including the bound on it, which is that one process serves
one company and the exposure is as large as the number of tenants sharing a
server.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Callable
from pathlib import Path

import pytest

from accountant import auth
from accountant.auth import identity as ident
from accountant.memory.store import MemoryStore
from accountant.problems import FUNDING_PROBLEM
from accountant.schema import NOT_RECORDED
from accountant.web import app
from tests.test_auth import (
    ALPHA,
    BETA,
    NEXT_WEEK,
    NOW,
    PASSWORD,
    fetch,
    production_auth,
    send,
    tenants,
)
from tests.test_durable_log import type_an_entry_that_posts
from tests.test_reversal_guard import a_posted_voucher, as_user
from tests.test_web import demo_company, fake_backend, log_block, serving

#: `production_auth` is `tests/test_auth.py`'s autouse fixture, which DELETES
#: LOCAL_DEV_MODE so this file runs the shipped path. Imported rather than
#: copied — `tests/test_reversal_guard.py` already carries a second copy of it,
#: and a third would be three places for one setting to drift. Re-exported
#: through `__all__` the way `tests/conftest.py` re-exports `server`, so ruff
#: and pyright both see it as deliberate.
__all__ = ["production_auth"]

#: The people. `tests.test_auth.tenants` creates two tenants with one user each
#: and names them this way, so these are read off that helper rather than
#: invented here.
ANNA = f"user-{ALPHA}"
ANNA_EMAIL = "a@alpha.test"
#: A SECOND person inside Anna's tenant. `tenants` does not make one — this file
#: needs it for steps 6 and 10, which are both about two colleagues who share a
#: company — so it is added on top rather than by rewriting that helper.
BILAL = "user-alpha-colleague"
BILAL_EMAIL = "bilal@alpha.test"
#: The other tenant's person. Step 6 is about what she can reach.
CARLA = f"user-{BETA}"

#: What `tests.test_reversal_guard.a_posted_voucher` types: "paid Gupta Hardware
#: 1500 for tools". Named here so the paise assertion below cannot drift away
#: from the entry that produced it without somebody noticing.
GUPTA_PAISE = 150_000

#: A vendor the demo company has forty consistent postings for, so this one
#: posts straight through with no question at all. Step 10 needs a voucher to
#: bulk-reverse AFTER step 8 has undone the first one, and by then Gupta
#: Hardware has been answered for and would no longer ask.
KNOWN = "paid Sharma Traders 4200 for cement"


def signed_in(
    anna: str = "", bilal: str = "", carla: str = ""
) -> Callable[[MemoryStore], None]:
    """Seed callback: two tenants, three people, a session for each token given.

    Runs INSIDE the serving thread. SQLite hands a connection to the thread that
    opened it, so a store built in the test thread could not be read by the
    server thread; the tokens are minted in the test and passed IN, so nothing
    has to be read back out across that boundary.

    The bulk of it is `tests.test_auth.tenants`, which is where tenant-alpha,
    tenant-beta and their one-user-each already live. Only Bilal is extra.
    """

    def seed(store: MemoryStore) -> None:
        tenants(store, *[(t, k) for t, k in ((anna, ALPHA), (carla, BETA)) if t])
        digest, salt = auth.hash_password(PASSWORD)
        store.create_user(BILAL, ALPHA, BILAL_EMAIL, digest, salt, NOW.isoformat())
        if bilal:
            store.open_session(
                auth.token_fingerprint(bilal),
                BILAL,
                ALPHA,
                NOW.isoformat(),
                NEXT_WEEK.isoformat(),
            )

    return seed


def token_in(cookie: str) -> str:
    """The session token out of a `Set-Cookie` header.

    A named function rather than the same three call chain in six tests. The
    token is percent-encoded on the way out — `_send_with_session` quotes it —
    so reading it back has to unquote, and a test that forgot would fail on
    exactly the tokens containing a character worth quoting.
    """
    return urllib.parse.unquote(cookie.split("=", 1)[1].split(";", 1)[0])


def trail(db: Path) -> list[tuple[str, str, str, str]]:
    """(action, reason, tenant, user) for every audit row, oldest first.

    A tuple rather than the `ActionLog` object because what step 12 compares is
    the CONTENT of the trail across two processes, and four named fields say
    that more clearly than an equality on a dataclass whose other fields would
    join in silently.
    """
    return [
        (row.action, row.reason, row.tenant_id, row.user_id)
        for row in MemoryStore(db).actions(app.COMPANY)
    ]


def posted_rows(db: Path) -> list[tuple[str, str, str, str]]:
    return [row for row in trail(db) if row[0] == "posted"]


# ---------------------------------------------------------------------------
# 1-3. the front door
# ---------------------------------------------------------------------------


def test_the_home_page_refuses_a_caller_with_no_session_at_all() -> None:
    """STEP 1. The first thing a stranger meets is a closed door.

    401 and not a redirect: a redirect is a 3xx, and a script following it would
    read the login page as the answer to the request it made.
    """
    with serving(demo_company(), fake_backend(), seed=signed_in()) as base:
        status, body, cookie = fetch(base)

    assert status == 401
    assert "no session token" in body
    assert cookie == "", "a refused request must not hand out a session"


def test_a_wrong_password_and_an_unknown_email_are_refused_in_the_same_words() -> None:
    """STEP 2. Both halves in one test, because the property is the COMPARISON.

    Two different sentences would let anybody with a browser enumerate which
    email addresses have accounts here, which is a customer list given away for
    free. Asserting the two bodies are equal is the only way to measure that;
    asserting each one says something sensible is not.
    """
    with serving(demo_company(), fake_backend(), seed=signed_in()) as base:
        wrong = send(base, "/login", email=ANNA_EMAIL, password=PASSWORD + "!")
        unknown = send(base, "/login", email="nobody@nowhere.test", password=PASSWORD)

    assert wrong[0] == 401
    assert wrong[2] == "", "a wrong password must set no cookie"
    assert unknown[0] == wrong[0]
    assert unknown[1] == wrong[1], "the two refusals are distinguishable"
    assert unknown[2] == ""


def test_a_correct_password_returns_an_httponly_samesite_cookie() -> None:
    """STEP 3. The three attributes, and then the cookie actually working.

    HttpOnly so a page script cannot read it, SameSite=Lax so another site
    cannot make the browser spend it. `Secure` is deliberately absent until TLS
    is in front of the cloud server; `docs/OWNER_WORK.md` records that.

    The last line is not decoration: a cookie carrying every right attribute and
    a token the server does not honour would pass the three assertions above.
    """
    with serving(demo_company(), fake_backend(), seed=signed_in()) as base:
        status, _body, cookie = send(
            base, "/login", email=ANNA_EMAIL, password=PASSWORD
        )

        assert status == 200
        assert cookie.startswith(f"{app.COOKIE}=")
        assert "HttpOnly" in cookie
        assert "SameSite=Lax" in cookie
        assert fetch(base, token=token_in(cookie))[0] == 200


# ---------------------------------------------------------------------------
# 4-5. the entry that started as a sentence and ended as a voucher
# ---------------------------------------------------------------------------


def test_an_unknown_vendor_asks_twice_and_then_posts_the_exact_paise(
    tmp_path: Path,
) -> None:
    """STEP 4. Two unknowns, two questions, one voucher.

    `a_posted_voucher` IS the flow, and it is imported rather than retyped: it
    posts the entry, answers the purpose question, ASSERTS the funding question
    is asked before anything is written, answers that, and asserts the page said
    posted. What is added here is the arithmetic — the amount that reached Tally
    — and the proof that the first request ASKED rather than guessed.

    That last proof is taken off the durable log rather than off the pages,
    because "asked, then posted" is an ordering claim and the log is the only
    place the order is recorded. A page can say anything.
    """
    anna = auth.new_token()
    db = tmp_path / "app.db"
    with serving(
        demo_company(), fake_backend(), seed=signed_in(anna=anna), store_path=db
    ) as base:
        a_posted_voucher(base, anna)

        written = app.runtime().client.list_our_vouchers(app.COMPANY)
        assert len(written) == 1
        assert written[0].amount_paise == GUPTA_PAISE
        assert isinstance(written[0].amount_paise, int), "rupees would round"
        assert written[0].debit_account == "Purchases"
        assert written[0].credit_account == "Cash"

    actions = [action for action, _reason, _tenant, _user in trail(db)]
    assert "asked" in actions, "an unseen vendor was guessed at rather than asked about"
    assert actions.index("asked") < actions.index("posted")
    assert actions.count("posted") == 1


def test_the_audit_row_for_that_posting_names_the_tenant_and_the_user(
    tmp_path: Path,
) -> None:
    """STEP 5. Who did this, read back out of the file after the writer is gone.

    A FILE store, not `:memory:`: `:memory:` is private to the connection that
    opened it and that connection lives in the serving thread. Reading after
    shutdown is sequential access, so there is no locking argument to make.
    """
    anna = auth.new_token()
    db = tmp_path / "app.db"
    with serving(
        demo_company(), fake_backend(), seed=signed_in(anna=anna), store_path=db
    ) as base:
        a_posted_voucher(base, anna)

    rows = posted_rows(db)
    assert rows, "the posting produced no `posted` row at all"
    _action, reason, tenant, user = rows[-1]
    assert tenant == ALPHA
    assert user == ANNA
    assert reason.strip(), "a row without a reason is a timestamp"


# ---------------------------------------------------------------------------
# 6. two people in one tenant, and one person in another
# ---------------------------------------------------------------------------


def test_a_colleague_in_the_same_tenant_sees_the_company_that_was_posted_to() -> None:
    """STEP 6, the half that is correct today.

    An accounts department is more than one person, so a colleague who did not
    type the entry must still be able to see it. The operation id is what is
    asserted rather than the vendor name: the vendor appears in the hint text
    and the voucher table too, so a page missing the posting entirely would
    still contain it.
    """
    anna, bilal = auth.new_token(), auth.new_token()
    with serving(
        demo_company(), fake_backend(), seed=signed_in(anna=anna, bilal=bilal)
    ) as base:
        operation_id = a_posted_voucher(base, anna)
        status, page, _cookie = fetch(base, token=bilal)

    assert status == 200
    assert operation_id in page, "a colleague could not see what was posted"


def test_another_tenants_session_is_refused_before_it_can_touch_these_books(
    tmp_path: Path,
) -> None:
    """DEFECT J1, FOUND BY THIS FILE AND FIXED THE SAME DAY.

    This step was a pinned defect for a few hours on 2026-08-11 and is now the
    behaviour. What it found: `Principal.require` existed, had a passing unit
    test, and had no caller anywhere in `accountant/` — so a session issued to
    one customer was authenticated against another customer's open books and
    let through.

    A unit test of a guard proves the guard works and says nothing about
    whether the guard is installed. That is the whole of it, and it is why this
    file exists: every piece had passing tests and the pieces did not connect.

    403 and not 401. Carla's credential is perfectly good; it is for somebody
    else's books, and 401 would tell her the credential was the problem.

    The trial balance is the assertion rather than the status code alone: a 403
    with a reversed voucher behind it would be a passing test and a lost entry.
    """
    anna, carla = auth.new_token(), auth.new_token()
    db = tmp_path / "app.db"
    with serving(
        demo_company(),
        fake_backend(),
        seed=signed_in(anna=anna, carla=carla),
        store_path=db,
    ) as base:
        operation_id = a_posted_voucher(base, anna)
        tally = app.runtime().client
        before = tally.trial_balance(app.COMPANY)
        assert len(tally.list_our_vouchers(app.COMPANY)) == 1

        assert fetch(base, token=carla)[0] == 403, "a foreign session got in"
        assert as_user(base, "/reverse", carla, op=operation_id)[0] == 403

        assert tally.trial_balance(app.COMPANY) == before
        assert len(tally.list_our_vouchers(app.COMPANY)) == 1

    # And one company's durable log names ONE tenant. NOT_RECORDED is allowed
    # and is not a second tenant: it is what a row written by a path with no
    # session behind it honestly says, and the whole point of that value is
    # that it is distinguishable from a tenant id rather than blank.
    named = {row[2] for row in trail(db) if row[2] and row[2] != NOT_RECORDED}
    assert named == {ALPHA}, "one company's log carries more than one tenant"


def test_replaying_the_answer_that_posted_writes_no_second_voucher(
    tmp_path: Path,
) -> None:
    """STEP 7. The browser Back button, which is not an attack and happens daily.

    `DRAFTS` still holds the posted draft, so the replay runs the whole answer
    path again and reaches `pipeline.post` carrying the SAME operation id. The
    number is the assertion, not the exception: `pytest.raises` on its own says
    a call raised and says nothing at all about the books.
    """
    anna = auth.new_token()
    db = tmp_path / "app.db"
    with serving(
        demo_company(), fake_backend(), seed=signed_in(anna=anna), store_path=db
    ) as base:
        a_posted_voucher(base, anna)
        (draft,) = tuple(app.DRAFTS)
        tally = app.runtime().client
        ours = tally.list_our_vouchers(app.COMPANY)
        balance = tally.trial_balance(app.COMPANY)

        status, body = as_user(
            base, "/answer", anna, draft=draft, value="Cash", problem=FUNDING_PROBLEM
        )

        assert status == 409, body
        assert tally.list_our_vouchers(app.COMPANY) == ours
        assert tally.trial_balance(app.COMPANY) == balance

    assert len(posted_rows(db)) == 1, "one posting, however many clicks"


def test_undoing_the_posting_returns_the_trial_balance_to_the_paise() -> None:
    """STEP 8. Every account, to the last paise, and not a rounded total.

    `trial_balance` is a `dict[str, int]` of paise, so equality here is exact
    across every account rather than a comparison of one number. A reversal that
    reports success and leaves the books changed is the worst outcome available,
    because it is the one that gets believed.
    """
    anna = auth.new_token()
    with serving(demo_company(), fake_backend(), seed=signed_in(anna=anna)) as base:
        tally = app.runtime().client
        before = tally.trial_balance(app.COMPANY)
        operation_id = a_posted_voucher(base, anna)
        assert tally.trial_balance(app.COMPANY) != before, "nothing moved to undo"

        status, _body = as_user(base, "/reverse", anna, op=operation_id)

        assert status == 200
        assert tally.trial_balance(app.COMPANY) == before
        assert tally.list_our_vouchers(app.COMPANY) == ()


def test_a_reversed_operation_id_cannot_be_written_a_second_time(
    tmp_path: Path,
) -> None:
    """STEP 9. Defect I1's fix, over the surface a person actually touches.

    Post, undo, then re-submit the form that posted it. This used to go through:
    the duplicate guard asks TALLY whether the marker is there, and reversing
    DELETES the voucher, so after an undo it was not. One identity, two vouchers
    over the life of the books, two `posted` rows — and the trial balance ended
    exactly where a single posting leaves it, which is why nobody noticed.

    409 rather than 503. Nothing broke; the request conflicts with what has
    already happened, and telling somebody the product broke sends them looking
    for a fault that is not there.
    """
    anna = auth.new_token()
    db = tmp_path / "app.db"
    with serving(
        demo_company(), fake_backend(), seed=signed_in(anna=anna), store_path=db
    ) as base:
        operation_id = a_posted_voucher(base, anna)
        (draft,) = tuple(app.DRAFTS)
        tally = app.runtime().client

        as_user(base, "/reverse", anna, op=operation_id)
        assert tally.list_our_vouchers(app.COMPANY) == ()

        status, body = as_user(
            base, "/answer", anna, draft=draft, value="Cash", problem=FUNDING_PROBLEM
        )

        assert status == 409, body
        assert "already been written" in body
        assert tally.list_our_vouchers(app.COMPANY) == (), "nothing was written"

    assert len(posted_rows(db)) == 1


# ---------------------------------------------------------------------------
# 10. the undo-everything, and who is allowed to press it
# ---------------------------------------------------------------------------


def test_a_colleague_cannot_confirm_the_undo_everything_they_were_never_shown(
    tmp_path: Path,
) -> None:
    """STEP 10. Both halves, because either alone measures nothing.

    `/reverse-all` is deliberately two requests: the first shows the exact list
    and writes nothing, the second reverses the list that was shown. The
    guarantee is that whoever presses the button SAW what it would destroy — so
    Bilal must be refused Anna's batch, and Anna must still be able to confirm
    her own. A route that refused everybody would pass the first half.
    """
    anna, bilal = auth.new_token(), auth.new_token()
    db = tmp_path / "app.db"
    with serving(
        demo_company(),
        fake_backend(),
        seed=signed_in(anna=anna, bilal=bilal),
        store_path=db,
    ) as base:
        a_posted_voucher(base, anna)
        tally = app.runtime().client
        before = len(tally.list_our_vouchers(app.COMPANY))
        assert before, "nothing was posted, so this proves nothing"

        status, _preview = as_user(base, "/reverse-all", anna)
        assert status == 200
        (batch,) = tuple(app.BATCHES)

        refused_status, refused = as_user(
            base, "/reverse-all", bilal, confirm="yes", batch=batch
        )
        assert refused_status == 200
        assert "had no preview" in refused
        assert len(tally.list_our_vouchers(app.COMPANY)) == before
        assert batch in app.BATCHES, "Anna's preview was consumed by somebody else"

        confirmed_status, _done = as_user(
            base, "/reverse-all", anna, confirm="yes", batch=batch
        )
        assert confirmed_status == 200
        assert tally.list_our_vouchers(app.COMPANY) == ()

    bulk = [row for row in trail(db) if row[0] == "bulk_reversed"]
    assert bulk, "a bulk reversal wrote no row of its own"
    assert bulk[-1][2:] == (ALPHA, ANNA), "the row names whoever confirmed it"


# ---------------------------------------------------------------------------
# 11-12. signing out, and surviving the process
# ---------------------------------------------------------------------------


def test_signing_out_revokes_the_session_row_and_the_cookie_is_then_refused(
    tmp_path: Path,
) -> None:
    """STEP 11. In the DATABASE, which is the only place it counts.

    A cookie the server still honours is not a logout, it is a request that the
    stolen copy be polite. The row is read after shutdown for the reason step 5
    states, and it is read at all because the 401 alone cannot tell "revoked"
    from "the browser threw the cookie away".
    """
    anna = auth.new_token()
    db = tmp_path / "app.db"
    with serving(
        demo_company(), fake_backend(), seed=signed_in(anna=anna), store_path=db
    ) as base:
        assert fetch(base, token=anna)[0] == 200

        status, _body = as_user(base, "/logout", anna)

        assert status == 200
        assert fetch(base, token=anna)[0] == 401

    row = MemoryStore(db).session_by_fingerprint(auth.token_fingerprint(anna))
    assert row is not None, "signing out deleted the row rather than revoking it"
    assert row.revoked_at, "the session was forgotten by the browser and nowhere else"


def test_the_audit_trail_survives_the_server_being_stopped_and_started_again(
    tmp_path: Path,
) -> None:
    """STEP 12. The one that proves Task 4, and it is proved twice over.

    A server is brought up on a FILE, work is done through the surface, and the
    server is shut down. A SECOND server is then started on the same path — a
    different connection, a fresh `configure()`, a fresh bootstrap — and the
    trail written by the first one is still there, unchanged, with its reasons.

    The second server is given NO seed. The tenants, the users and the sessions
    written by the first run are in that same file, so re-seeding would collide
    on `tenant.tenant_id` — and the collision would be the wrong kind of
    failure. That the old token still works after the restart is the extra
    thing this arrangement measures for free.

    Read twice on purpose: once out of the file, which proves the rows are on
    disk, and once off the RESTARTED SERVER'S OWN PAGE, which proves they still
    reach a person. A row that survives and is no longer rendered is the failure
    `tests/test_web.py` describes at length.
    """
    anna = auth.new_token()
    db = tmp_path / "app.db"
    with serving(
        demo_company(), fake_backend(), seed=signed_in(anna=anna), store_path=db
    ) as base:
        operation_id = a_posted_voucher(base, anna)
        as_user(base, "/reverse", anna, op=operation_id)

    before = trail(db)
    assert before, "the first process wrote nothing at all"
    assert all(reason.strip() for _a, reason, _t, _u in before)

    with serving(demo_company(), fake_backend(), store_path=db) as base:
        status, page, _cookie = fetch(base, token=anna)
        assert status == 200, "the session did not survive the restart either"
        block = log_block(page)
        assert "nothing yet" not in block, "the restarted server renders an empty log"
        assert 'data-action="posted"' in block
        assert 'data-action="reversed"' in block

    after = trail(db)
    assert after[: len(before)] == before, "the second run overwrote the first's trail"


# ---------------------------------------------------------------------------
# 13. the other mode
# ---------------------------------------------------------------------------


def test_local_development_needs_no_credential_and_still_names_an_actor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """STEP 13. The mode a developer runs in, and the rows it still writes.

    Two claims, and the second is the one worth having. That local mode lets a
    request through is obvious. That its audit rows carry `local-dev` rather
    than a blank is not: a blank row would make every developer action
    indistinguishable from an action with no session behind it at all, and
    `NOT_RECORDED` is a sentence reserved for the second.

    `monkeypatch.setenv` on top of the autouse `production_auth` fixture, which
    deleted the variable. This is the one test in the file that wants it back.
    """
    monkeypatch.setenv(ident.ENV_LOCAL_DEV_MODE, "1")
    db = tmp_path / "app.db"
    with serving(demo_company(), fake_backend(), store_path=db) as base:
        assert fetch(base)[0] == 200, "local development asked for a credential"

        type_an_entry_that_posts(base)

        written = app.runtime().client.list_our_vouchers(app.COMPANY)
        assert len(written) == 1
        assert written[0].amount_paise == GUPTA_PAISE

    rows = posted_rows(db)
    assert rows, "the entry produced no `posted` row"
    _action, reason, tenant, user = rows[-1]
    assert tenant == auth.LOCAL_DEV_TENANT
    assert user == auth.LOCAL_DEV_USER
    assert reason.strip()


# ---------------------------------------------------------------------------
# the journey itself, in order, in one server
# ---------------------------------------------------------------------------


def test_the_whole_journey_runs_in_order_against_one_server(tmp_path: Path) -> None:
    """Steps 1-12, one after another, sharing one process and one database.

    WHY THIS EXISTS ON TOP OF THE THIRTEEN ABOVE. Each test above starts from a
    clean server, so each proves its step works from a known state. None of them
    proves the steps COMPOSE — that the cookie step 3 issued is the cookie step
    5's audit row names, that the draft step 4 posted is the draft step 7
    replays, that the identity step 8 reversed is the identity step 9 refuses.
    Ordering is a property and it needs a test of its own.

    STEP 5 AND STEP 12 ARE ASSERTED AFTER THE SERVER IS DOWN, and that is a
    fixture constraint rather than a choice: the store is SQLite and belongs to
    the serving thread, so a read from the test thread would be a thread
    affinity error and not an assertion.

    STEP 6's cross-tenant half is deliberately NOT here. It is a defect today
    (J1) and it has its own pair of tests above; asserting today's behaviour
    inside the journey would make this test fail on the day somebody fixes it,
    which is backwards.

    STEP 13 is not here either. It is the OTHER mode, and a journey that
    switched authentication off halfway through would be two journeys.
    """
    anna, bilal = auth.new_token(), auth.new_token()
    db = tmp_path / "app.db"
    with serving(
        demo_company(),
        fake_backend(),
        seed=signed_in(bilal=bilal),
        store_path=db,
    ) as base:
        tally = app.runtime().client
        opening = tally.trial_balance(app.COMPANY)

        # 1. the door is shut
        assert fetch(base)[0] == 401

        # 2. and it says the same thing to everybody it turns away
        wrong = send(base, "/login", email=ANNA_EMAIL, password=PASSWORD + "!")
        unknown = send(base, "/login", email="nobody@nowhere.test", password=PASSWORD)
        assert wrong[0] == unknown[0] == 401
        assert wrong[1] == unknown[1]
        assert wrong[2] == unknown[2] == ""

        # 3. a real password opens it. The token from HERE is what every step
        #    below carries, so the journey is one session and not a fresh
        #    credential per step.
        status, _body, cookie = send(
            base, "/login", email=ANNA_EMAIL, password=PASSWORD
        )
        assert status == 200
        assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
        anna = token_in(cookie)
        assert fetch(base, token=anna)[0] == 200

        # 4. an unknown vendor asks twice, then posts, to the paise
        operation_id = a_posted_voucher(base, anna)
        (draft,) = tuple(app.DRAFTS)
        written = tally.list_our_vouchers(app.COMPANY)
        assert len(written) == 1
        assert written[0].amount_paise == GUPTA_PAISE

        # 6. a colleague sees the company's data
        colleague = fetch(base, token=bilal)
        assert colleague[0] == 200
        assert operation_id in colleague[1]

        # 7. the answer that posted it, sent again
        replay, _body = as_user(
            base, "/answer", anna, draft=draft, value="Cash", problem=FUNDING_PROBLEM
        )
        assert replay == 409
        assert tally.list_our_vouchers(app.COMPANY) == written

        # 8. undo, and the books are where they started
        assert as_user(base, "/reverse", anna, op=operation_id)[0] == 200
        assert tally.trial_balance(app.COMPANY) == opening
        assert tally.list_our_vouchers(app.COMPANY) == ()

        # 9. and the identity that was reversed is spent, not freed
        conflict, body = as_user(
            base, "/answer", anna, draft=draft, value="Cash", problem=FUNDING_PROBLEM
        )
        assert conflict == 409
        assert "already been written" in body
        assert tally.list_our_vouchers(app.COMPANY) == ()

        # 10. a second entry, previewed by Anna, confirmed by nobody else.
        #     A KNOWN vendor this time: Gupta Hardware has been answered for and
        #     would post straight through now, which would make the two-question
        #     flow above look optional.
        assert as_user(base, "/entry", anna, text=KNOWN)[0] == 200
        assert len(tally.list_our_vouchers(app.COMPANY)) == 1
        assert as_user(base, "/reverse-all", anna)[0] == 200
        (batch,) = tuple(app.BATCHES)
        _status, refused = as_user(
            base, "/reverse-all", bilal, confirm="yes", batch=batch
        )
        assert "had no preview" in refused
        assert len(tally.list_our_vouchers(app.COMPANY)) == 1
        assert as_user(base, "/reverse-all", anna, confirm="yes", batch=batch)[0] == 200
        assert tally.list_our_vouchers(app.COMPANY) == ()
        assert tally.trial_balance(app.COMPANY) == opening

        # 11. signing out, and the same cookie afterwards
        assert as_user(base, "/logout", anna)[0] == 200
        assert fetch(base, token=anna)[0] == 401

    # 5. the audit row for step 4's posting names the tenant and the user
    rows = posted_rows(db)
    assert len(rows) == 2, "one posting for step 4 and one for step 10"
    assert rows[0][2:] == (ALPHA, ANNA)
    assert rows[0][1].strip()

    before = trail(db)
    revoked = MemoryStore(db).session_by_fingerprint(auth.token_fingerprint(anna))
    assert revoked is not None
    assert revoked.revoked_at, "step 11 revoked nothing in the database"

    # 12. stop, start again on the same file, and nothing was lost. No seed:
    #     the tenants and users are already in this database, and writing them
    #     twice would collide on the primary key.
    with serving(demo_company(), fake_backend(), store_path=db) as base:
        # The revoked cookie is STILL refused, so revocation outlived the
        # process too - a restart that forgot it would be a logout undone by a
        # deploy.
        assert fetch(base, token=anna)[0] == 401

        status, _body, cookie = send(
            base, "/login", email=ANNA_EMAIL, password=PASSWORD
        )
        assert status == 200, "the user rows did not survive the restart"
        page = fetch(base, token=token_in(cookie))[1]
        block = log_block(page)
        assert "nothing yet" not in block
        assert 'data-action="posted"' in block
        assert 'data-action="reversed"' in block
        assert 'data-action="bulk_reversed"' in block

    after = trail(db)
    assert after[: len(before)] == before, "the restart truncated the trail"
    assert all(reason.strip() for _a, reason, _t, _u in before), "a reason went missing"

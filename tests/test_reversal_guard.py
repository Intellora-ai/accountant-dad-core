"""The two routes that DESTROY, and what stands in front of them.

    POST /reverse       deletes one voucher, on an operation id from the form
    POST /reverse-all   deletes every voucher we ever wrote in this company

Until 2026-08-10 both answered anybody who could reach the socket. Task 2 put
authentication in front of them; `tests/test_auth.py` proves that. This file is
about what is left AFTER a caller is authenticated, because "logged in" and
"allowed to destroy this" are two different sentences.

THE ONE THIS FILE EXISTS FOR
----------------------------
`/reverse-all` is deliberately two requests: the first shows the exact list and
writes nothing, the second reverses the list that was shown. The guarantee is
that whoever presses the button SAW what it would destroy.

`BATCHES` was keyed by batch id alone. Two people share a company - that is the
normal shape of an accounts department - so colleague A could preview and
colleague B could post the confirmation carrying A's batch id. B then deleted
every voucher we ever wrote in that company having been shown nothing at all,
and every check passed on the way: valid session, right company, real batch.

WHAT IS NOT PROVED HERE
-----------------------
FakeTally throughout. Nothing here says anything about a real TallyPrime.
"""

from __future__ import annotations

import datetime
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pytest

from accountant import auth
from accountant.auth import identity as ident
from accountant.memory.store import MemoryStore
from accountant.web import app
from tests.test_web import demo_company, draft_id, fake_backend, serving

NOW = datetime.datetime(2026, 8, 10, 9, 0, tzinfo=datetime.UTC)
NEXT_WEEK = NOW + datetime.timedelta(days=7)

#: One company, two colleagues. The pair the whole file is about.
TENANT = "tenant-alpha"
ANNA = "user-anna"
BILAL = "user-bilal"
PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def production_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authentication REQUIRED, as in production.

    `tests/conftest.py` sets LOCAL_DEV_MODE=1 for the suite. Here two DIFFERENT
    people are the subject, and dev mode has exactly one user, so the whole
    measurement would collapse into a single identity and pass by not
    distinguishing anything.
    """
    monkeypatch.delenv(ident.ENV_LOCAL_DEV_MODE, raising=False)
    # WHOSE books this server serves. Defect J1, 2026-08-11: without it the
    # server admitted a session belonging to any tenant, because the guard that
    # was written to stop that had no caller. It fails closed now, so a test
    # that does not say who it is serving is refused - which is why this line
    # is here rather than a default being invented in the product.
    monkeypatch.setenv(app.ENV_TENANT, TENANT)


def colleagues(*sessions: tuple[str, str]) -> Callable[[MemoryStore], None]:
    """Seed callback: one tenant, two users, and a session for each token given.

    Runs inside the serving thread; SQLite hands a connection to the thread that
    opened it. The tokens are made in the test and passed in.
    """

    def seed(store: MemoryStore) -> None:
        store.create_tenant(TENANT, "Alpha Traders", NOW.isoformat())
        for user in (ANNA, BILAL):
            digest, salt = auth.hash_password(PASSWORD)
            store.create_user(
                user, TENANT, f"{user}@alpha.test", digest, salt, NOW.isoformat()
            )
        for token, user in sessions:
            store.open_session(
                auth.token_fingerprint(token),
                user,
                TENANT,
                NOW.isoformat(),
                NEXT_WEEK.isoformat(),
            )

    return seed


def as_user(base: str, path: str, token: str, **fields: str) -> tuple[int, str]:
    """POST as one particular person. Returns the status even when it refuses."""
    request = urllib.request.Request(  # noqa: S310 - loopback, http
        base + path, data=urllib.parse.urlencode(fields).encode()
    )
    request.add_header("Cookie", f"{app.COOKIE}={token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as answer:  # noqa: S310
            return answer.status, answer.read().decode()
    except urllib.error.HTTPError as refused:
        return refused.status or 0, refused.read().decode()


def a_posted_voucher(base: str, token: str) -> str:
    """Type an entry, answer both questions, and hand back its operation id.

    Driven through the surface as ONE named person rather than seeded into
    FakeTally, because what these tests are about is who did what: a voucher
    planted behind the app has no actor and could not tell Anna from Bilal.

    An unknown vendor raises two questions, not one - what the money was for,
    and where it came from - and both must be answered before anything posts.
    `tests/test_web.py::answer_purpose_and_funding` makes the same two calls;
    this is that flow carrying a cookie.
    """
    status, asked = as_user(
        base, "/entry", token, text="paid Gupta Hardware 1500 for tools"
    )
    assert status == 200, asked
    draft = draft_id(asked)

    status, funding = as_user(
        base, "/answer", token, draft=draft, value="Purchases", problem="which_account"
    )
    assert status == 200
    assert "how did you pay" in funding.lower(), funding

    status, done = as_user(
        base, "/answer", token, draft=draft, value="Cash", problem="funding_is_named"
    )
    assert status == 200
    assert "posted" in done.lower(), done

    vouchers = app.runtime().client.list_our_vouchers(app.runtime().company)
    assert vouchers, "the entry did not post, so there is nothing to reverse"
    return app.DRAFTS[draft].operation_id


# ---------------------------------------------------------------------------
# the confirmation must come from the person who took the preview
# ---------------------------------------------------------------------------


def test_a_colleague_cannot_confirm_a_batch_they_were_never_shown() -> None:
    """The defect this file exists for.

    Anna previews. Bilal posts the confirmation with Anna's batch id. Every
    other check passes - Bilal has a live session, the company matches, the
    batch is real - and the books must still be untouched.
    """
    anna, bilal = auth.new_token(), auth.new_token()
    with serving(
        demo_company(),
        fake_backend(),
        seed=colleagues((anna, ANNA), (bilal, BILAL)),
    ) as base:
        a_posted_voucher(base, anna)
        before = len(app.runtime().client.list_our_vouchers(app.runtime().company))
        assert before, "nothing was posted, so this proves nothing"

        status, preview = as_user(base, "/reverse-all", anna)
        assert status == 200
        (batch_id,) = app.BATCHES.keys()

        status, body = as_user(
            base, "/reverse-all", bilal, confirm="yes", batch=batch_id
        )

        assert status == 200
        assert "had no preview" in body
        after = len(app.runtime().client.list_our_vouchers(app.runtime().company))
        assert after == before, "Bilal's confirmation reversed something"
        assert batch_id in app.BATCHES, (
            "Anna's pending preview was consumed by somebody else's request"
        )
        assert preview  # the preview page was rendered, not an error


def test_the_person_who_previewed_can_still_confirm() -> None:
    """The control. Without it the test above passes on a broken route that
    refuses everybody, which measures nothing."""
    anna = auth.new_token()
    with serving(demo_company(), fake_backend(), seed=colleagues((anna, ANNA))) as base:
        a_posted_voucher(base, anna)
        before = len(app.runtime().client.list_our_vouchers(app.runtime().company))
        assert before

        as_user(base, "/reverse-all", anna)
        (batch_id,) = app.BATCHES.keys()

        status, _body = as_user(
            base, "/reverse-all", anna, confirm="yes", batch=batch_id
        )

        assert status == 200
        after = len(app.runtime().client.list_our_vouchers(app.runtime().company))
        assert after == 0, "Anna's own confirmation did not reverse the batch"
        assert batch_id not in app.BATCHES, "a used batch stayed confirmable"


def test_a_confirmation_with_no_preview_at_all_still_reverses_nothing() -> None:
    """The pre-existing guard, kept measured. A batch id nobody issued must not
    become a bulk delete just because the caller is signed in."""
    anna = auth.new_token()
    with serving(demo_company(), fake_backend(), seed=colleagues((anna, ANNA))) as base:
        a_posted_voucher(base, anna)
        before = len(app.runtime().client.list_our_vouchers(app.runtime().company))

        status, body = as_user(
            base, "/reverse-all", anna, confirm="yes", batch="a-batch-nobody-previewed"
        )

        assert status == 200
        assert "had no preview" in body
        assert (
            len(app.runtime().client.list_our_vouchers(app.runtime().company)) == before
        )


def test_the_preview_itself_reverses_nothing() -> None:
    """Step one writes nothing. If it did, the two-step design would be
    decoration and the confirmation would be confirming something already done."""
    anna = auth.new_token()
    with serving(demo_company(), fake_backend(), seed=colleagues((anna, ANNA))) as base:
        a_posted_voucher(base, anna)
        live = app.runtime()
        before = live.client.trial_balance(live.company)
        count = len(live.client.list_our_vouchers(live.company))

        status, _body = as_user(base, "/reverse-all", anna)

        assert status == 200
        assert live.client.trial_balance(live.company) == before
        assert len(live.client.list_our_vouchers(live.company)) == count


# ---------------------------------------------------------------------------
# the single-voucher route
# ---------------------------------------------------------------------------


def test_reversing_an_operation_id_we_never_wrote_changes_nothing() -> None:
    """`/reverse` takes the id straight off the form. What stops it being a
    delete-anything button is that `read_by_operation_id` only finds vouchers of
    OURS in THIS company - so a stranger's id finds nothing and moves nothing."""
    anna = auth.new_token()
    with serving(demo_company(), fake_backend(), seed=colleagues((anna, ANNA))) as base:
        a_posted_voucher(base, anna)
        live = app.runtime()
        before = live.client.trial_balance(live.company)
        count = len(live.client.list_our_vouchers(live.company))

        status, _body = as_user(base, "/reverse", anna, op="an-id-from-somewhere-else")

        assert status == 200
        assert live.client.trial_balance(live.company) == before
        assert len(live.client.list_our_vouchers(live.company)) == count


def test_reversing_our_own_voucher_returns_the_books_to_the_paise() -> None:
    """Criterion #6.5, on the path a person actually uses. A reversal that
    reports success and leaves the books changed is the worst of the outcomes,
    because it is the one that gets believed."""
    anna = auth.new_token()
    with serving(demo_company(), fake_backend(), seed=colleagues((anna, ANNA))) as base:
        live = app.runtime()
        empty = live.client.trial_balance(live.company)
        op = a_posted_voucher(base, anna)
        assert live.client.trial_balance(live.company) != empty

        status, _body = as_user(base, "/reverse", anna, op=op)

        assert status == 200
        assert live.client.trial_balance(live.company) == empty


def test_both_destroying_routes_refuse_a_caller_with_no_session(
    tmp_path: Path,
) -> None:
    """Stated again here rather than left to `tests/test_auth.py`, because this
    is the file somebody reads when they change these two routes."""
    db = tmp_path / "app.db"
    with serving(
        demo_company(), fake_backend(), seed=colleagues(), store_path=db
    ) as base:
        for route in ("/reverse", "/reverse-all"):
            request = urllib.request.Request(  # noqa: S310
                base + route, data=urllib.parse.urlencode({"op": "x"}).encode()
            )
            with pytest.raises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(request, timeout=5)  # noqa: S310
            assert refused.value.status == 401, route


def test_a_bulk_reversal_row_names_the_person_who_confirmed_it(
    tmp_path: Path,
) -> None:
    """Who pressed it is the first question asked after a bulk delete."""
    anna = auth.new_token()
    db = tmp_path / "app.db"
    with serving(
        demo_company(),
        fake_backend(),
        seed=colleagues((anna, ANNA)),
        store_path=db,
    ) as base:
        a_posted_voucher(base, anna)
        as_user(base, "/reverse-all", anna)
        (batch_id,) = app.BATCHES.keys()
        as_user(base, "/reverse-all", anna, confirm="yes", batch=batch_id)

    rows = [
        r for r in MemoryStore(db).actions(app.COMPANY) if r.action == "bulk_reversed"
    ]
    assert rows, "a bulk reversal wrote no row of its own"
    assert rows[-1].tenant_id == TENANT
    assert rows[-1].user_id == ANNA

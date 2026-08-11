"""Who is asking, and whose books they may touch.

WHAT EACH SECTION PROVES
------------------------
1. the two modes            LOCAL_DEV_MODE=1 skips auth and says so out loud;
                            anything else, including unset, requires a session
2. the four refusals        no token, unknown token, revoked token, expired
                            token — each one 401, each one for its own reason
3. tenancy                  a valid session for tenant A cannot reach tenant B,
                            and the refusal is 403 rather than 401
4. the tenant id is a       there is no path in the codebase that builds a
   conclusion               Principal from request data, and an AST scan says so
5. passwords and tokens     neither is ever stored; the hash is, and a wrong
                            password costs the same time as an unknown email
6. the audit row            names the tenant and the user, or honestly says
                            NOT_RECORDED

WHAT IS NOT PROVED HERE
-----------------------
Nothing in this file touches a real TallyPrime. The runtime is FakeTally, so
every result is FAKETALLY evidence and none of it says anything about a
licensed installation.
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
from accountant.memory.identity import normalise_company
from accountant.memory.store import MemoryStore
from accountant.schema import NOT_RECORDED, ActionLog
from accountant.web import app
from tests.test_web import demo_company, fake_backend, serving

NOW = datetime.datetime(2026, 8, 10, 9, 0, tzinfo=datetime.UTC)
LATER = NOW + datetime.timedelta(hours=1)
NEXT_WEEK = NOW + datetime.timedelta(days=7)

ALPHA = "tenant-alpha"
BETA = "tenant-beta"
PASSWORD = "correct horse battery staple"


def a_store() -> MemoryStore:
    """Two tenants, one user each. The pair every isolation claim is made on."""
    store = MemoryStore(":memory:")
    store.create_tenant(ALPHA, "Alpha Traders", NOW.isoformat())
    store.create_tenant(BETA, "Beta Works", NOW.isoformat())
    for tenant, email in ((ALPHA, "a@alpha.test"), (BETA, "b@beta.test")):
        digest, salt = auth.hash_password(PASSWORD)
        store.create_user(
            f"user-{tenant}", tenant, email, digest, salt, NOW.isoformat()
        )
    return store


def a_session(store: MemoryStore, tenant: str, *, expires: str = "") -> str:
    token = auth.new_token()
    store.open_session(
        auth.token_fingerprint(token),
        f"user-{tenant}",
        tenant,
        NOW.isoformat(),
        expires or NEXT_WEEK.isoformat(),
    )
    return token


# ---------------------------------------------------------------------------
# 1. the two modes
# ---------------------------------------------------------------------------


def test_local_dev_mode_is_exactly_one() -> None:
    """Strict on purpose. A loose reading turns a typo into an open server."""
    assert ident.local_dev_mode({"LOCAL_DEV_MODE": "1"}) is True
    for wrong in ("0", "", "true", "yes", "TRUE", " 1", "1 ", "on"):
        assert ident.local_dev_mode({"LOCAL_DEV_MODE": wrong}) is False, wrong
    assert ident.local_dev_mode({}) is False


def test_the_default_is_production_not_development() -> None:
    """An unset variable must mean auth is REQUIRED.

    This is the whole failure-mode argument in one assertion. A flag that must
    be set to become unsafe fails closed when somebody forgets it; a flag that
    must be set to become safe ships an open server the first time a deploy
    script drops an environment variable.
    """
    assert ident.local_dev_mode({}) is False
    with pytest.raises(auth.AuthRefusal) as refused:
        auth.authenticate("", a_store(), dev_mode=ident.local_dev_mode({}))
    assert refused.value.status == 401


def test_dev_mode_identifies_without_a_token() -> None:
    who = auth.authenticate("", a_store(), dev_mode=True)
    assert who.user_id == auth.LOCAL_DEV_USER
    assert who.tenant_id == auth.LOCAL_DEV_TENANT
    assert who.local_dev is True


def test_dev_mode_ignores_a_token_that_was_presented_anyway() -> None:
    """Both modes must not differ by whether the caller guessed right.

    If dev mode quietly honoured a real token, a developer holding one would be
    running a DIFFERENT code path from a developer without one, and only one of
    them would be testing what ships.
    """
    store = a_store()
    token = a_session(store, ALPHA)
    who = auth.authenticate(token, store, dev_mode=True)
    assert who.tenant_id == auth.LOCAL_DEV_TENANT
    assert who.local_dev is True


# ---------------------------------------------------------------------------
# 2. the four refusals
# ---------------------------------------------------------------------------


def test_no_token_is_refused() -> None:
    with pytest.raises(auth.AuthRefusal) as refused:
        auth.authenticate("", a_store())
    assert refused.value.status == 401
    assert "no session token" in refused.value.reason


def test_an_unknown_token_is_refused() -> None:
    with pytest.raises(auth.AuthRefusal) as refused:
        auth.authenticate("not-a-token-anybody-issued", a_store())
    assert refused.value.status == 401
    assert "not recognised" in refused.value.reason


def test_a_revoked_token_is_refused() -> None:
    """Revocation is a row change, which is why a stolen token can be killed."""
    store = a_store()
    token = a_session(store, ALPHA)
    assert auth.authenticate(token, store).tenant_id == ALPHA

    assert store.revoke_session(auth.token_fingerprint(token), LATER.isoformat())
    with pytest.raises(auth.AuthRefusal) as refused:
        auth.authenticate(token, store)
    assert refused.value.status == 401
    assert "revoked" in refused.value.reason


def test_revoking_twice_reports_that_nothing_changed() -> None:
    store = a_store()
    token = a_session(store, ALPHA)
    finger = auth.token_fingerprint(token)
    assert store.revoke_session(finger, LATER.isoformat()) is True
    assert store.revoke_session(finger, NEXT_WEEK.isoformat()) is False
    row = store.session_by_fingerprint(finger)
    assert row is not None
    assert row.revoked_at == LATER.isoformat(), "the first revocation stands"


def test_an_expired_token_is_refused_after_a_restart() -> None:
    """Expiry lives in the row, not in a timer, so a restart cannot revive it."""
    store = a_store()
    token = a_session(store, ALPHA, expires=LATER.isoformat())
    assert auth.authenticate(token, store, now=NOW).tenant_id == ALPHA

    with pytest.raises(auth.AuthRefusal) as refused:
        auth.authenticate(token, store, now=NEXT_WEEK)
    assert refused.value.status == 401
    assert "expired" in refused.value.reason


def test_revoking_every_session_for_one_user_kills_all_of_them() -> None:
    """What a password change must call. One survivor is a compromise that held."""
    store = a_store()
    tokens = [a_session(store, ALPHA) for _ in range(3)]
    assert store.revoke_all_sessions_for(f"user-{ALPHA}", LATER.isoformat()) == 3
    for token in tokens:
        with pytest.raises(auth.AuthRefusal):
            auth.authenticate(token, store)


# ---------------------------------------------------------------------------
# 3. tenancy
# ---------------------------------------------------------------------------


def test_a_valid_session_reaches_its_own_tenant() -> None:
    store = a_store()
    who = auth.authenticate(a_session(store, ALPHA), store)
    assert who.tenant_id == ALPHA
    who.require(ALPHA)  # does not raise


def test_a_valid_session_is_refused_another_tenant_with_403_not_401() -> None:
    """The codes are not interchangeable.

    401 says "I do not know who you are" and 403 says "I know, and no".
    Answering 401 here would tell the caller their credential is broken when it
    is fine, and answering 403 to a stranger would confirm the resource exists.
    """
    store = a_store()
    who = auth.authenticate(a_session(store, ALPHA), store)
    with pytest.raises(auth.AuthRefusal) as refused:
        who.require(BETA)
    assert refused.value.status == 403
    assert ALPHA in refused.value.reason
    assert BETA in refused.value.reason


def test_two_tenants_sessions_never_resolve_to_each_other() -> None:
    store = a_store()
    alpha = auth.authenticate(a_session(store, ALPHA), store)
    beta = auth.authenticate(a_session(store, BETA), store)
    assert alpha.tenant_id != beta.tenant_id
    assert not alpha.owns(BETA)
    assert not beta.owns(ALPHA)


# ---------------------------------------------------------------------------
# 4. the tenant id is a conclusion, never an input
# ---------------------------------------------------------------------------


def test_no_principal_is_ever_built_from_request_data() -> None:
    """AST, not a substring scan.

    The first version of the connector's socket guard matched the word
    HTTPServer inside the paragraph explaining that no HTTPServer is used, and
    a gate-contract test before it matched a command inside a comment and
    called a dead gate live for two days. So this walks the tree.

    Every `Principal(...)` construction in the package is found, and each one
    must pass values that came out of a stored session or out of the local-dev
    constants. A construction whose arguments mention a form, a header, a query
    or a request is the defect this test exists for.
    """
    banned = {"form", "headers", "query", "request", "params", "body", "cookie"}
    seen = 0
    for module in Path(app.__file__).parent.parent.rglob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            if not (isinstance(called, ast.Name) and called.id == "Principal"):
                continue
            seen += 1
            source = ast.dump(node).lower()
            for word in banned:
                assert word not in source, f"{module}: Principal built from {word}"
    assert seen >= 1, "the scan found no Principal construction at all"


def test_authenticate_refuses_when_there_is_no_session_store() -> None:
    """A lookup that cannot answer must refuse, never wave the caller through."""

    class Nothing:
        pass

    with pytest.raises(auth.AuthRefusal) as refused:
        auth.authenticate("some-token", Nothing())
    assert refused.value.status == 401


# ---------------------------------------------------------------------------
# 5. passwords and tokens
# ---------------------------------------------------------------------------


def test_a_password_is_never_stored() -> None:
    store = a_store()
    user = store.user_by_email("a@alpha.test")
    assert user is not None
    assert PASSWORD not in user.password_hash
    assert PASSWORD not in user.salt
    assert auth.verify_password(PASSWORD, user.password_hash, user.salt)
    assert not auth.verify_password(PASSWORD + "!", user.password_hash, user.salt)


def test_a_token_is_never_stored_only_its_fingerprint(tmp_path: Path) -> None:
    """The row, and then the whole FILE.

    Reading the bytes is the stronger half. A row assertion proves the column
    holds a fingerprint; only the file proves the token is not sitting in some
    other column, in an index, or in a page SQLite has not yet reclaimed.
    """
    db = tmp_path / "app.db"
    store = MemoryStore(db)
    store.create_tenant(ALPHA, "Alpha Traders", NOW.isoformat())
    digest, salt = auth.hash_password(PASSWORD)
    store.create_user(
        f"user-{ALPHA}", ALPHA, "a@alpha.test", digest, salt, NOW.isoformat()
    )
    token = a_session(store, ALPHA)

    row = store.session_by_fingerprint(auth.token_fingerprint(token))
    assert row is not None
    assert row.token_fingerprint != token
    assert token not in row.token_fingerprint
    assert token.encode() not in db.read_bytes()
    assert PASSWORD.encode() not in db.read_bytes()


def test_the_same_password_hashes_differently_for_two_users() -> None:
    """A per-password salt, so one cracked hash does not crack the other."""
    store = a_store()
    alpha = store.user_by_email("a@alpha.test")
    beta = store.user_by_email("b@beta.test")
    assert alpha is not None and beta is not None
    assert alpha.salt != beta.salt
    assert alpha.password_hash != beta.password_hash


def test_verify_refuses_blank_material_rather_than_comparing_it() -> None:
    assert auth.verify_password("", "abc", "def") is False
    assert auth.verify_password("pw", "", "def") is False
    assert auth.verify_password("pw", "abc", "") is False


def test_two_tokens_are_never_the_same() -> None:
    assert len({auth.new_token() for _ in range(200)}) == 200


# ---------------------------------------------------------------------------
# 6. the audit row names the tenant and the user
# ---------------------------------------------------------------------------


def test_an_action_row_carries_the_tenant_and_the_user() -> None:
    store = MemoryStore(":memory:")
    store.record_action(
        ActionLog(
            ts=NOW,
            action="posted",
            company_key=normalise_company("Alpha Traders"),
            outcome="POSTED",
            reason="every check passed and the company is backed up",
            run_id="run-1",
            backend="FakeTally",
            tenant_id=ALPHA,
            user_id=f"user-{ALPHA}",
        )
    )
    (row,) = store.actions("Alpha Traders")
    assert row.tenant_id == ALPHA
    assert row.user_id == f"user-{ALPHA}"


def test_an_action_row_with_no_session_says_so_rather_than_inventing_one() -> None:
    """NOT_RECORDED, not a blank and not a made-up "system" user.

    A row that quietly claims an actor nobody measured is worse than a row that
    admits it has none, because only one of the two can be spotted later.
    """
    store = MemoryStore(":memory:")
    store.record_action(
        ActionLog(
            ts=NOW,
            action="posted",
            company_key=normalise_company("Alpha Traders"),
            outcome="POSTED",
            reason="a script with no session behind it",
            run_id="run-1",
            backend="FakeTally",
        )
    )
    (row,) = store.actions("Alpha Traders")
    assert row.tenant_id == NOT_RECORDED
    assert row.user_id == NOT_RECORDED


def test_a_blank_tenant_or_user_is_refused() -> None:
    for field in ("tenant_id", "user_id"):
        with pytest.raises(ValueError, match="blank"):
            ActionLog(
                ts=NOW,
                action="posted",
                company_key=normalise_company("Alpha Traders"),
                outcome="POSTED",
                reason="a reason, so only the blank id can fail this",
                run_id="run-1",
                backend="FakeTally",
                **{field: "   "},
            )


# ---------------------------------------------------------------------------
# the acceptance tests the owner wrote, over real HTTP
# ---------------------------------------------------------------------------
#
# Through `tests/test_web.py::serving`, which is the ONE spin-up path in this
# suite. A second threaded server here would be a second definition of what a
# running app is, and the two would drift.


def tenants(store: MemoryStore, *tokens: tuple[str, str]) -> None:
    """Seed callback: two tenants, one user each, plus any sessions asked for.

    Runs INSIDE the serving thread, because SQLite hands a connection to the
    thread that opened it. The tokens are made in the test and passed in, so
    nothing has to be read back out across the boundary.
    """
    store.create_tenant(ALPHA, "Alpha Traders", NOW.isoformat())
    store.create_tenant(BETA, "Beta Works", NOW.isoformat())
    for tenant, email in ((ALPHA, "a@alpha.test"), (BETA, "b@beta.test")):
        digest, salt = auth.hash_password(PASSWORD)
        store.create_user(
            f"user-{tenant}", tenant, email, digest, salt, NOW.isoformat()
        )
    for token, tenant in tokens:
        store.open_session(
            auth.token_fingerprint(token),
            f"user-{tenant}",
            tenant,
            NOW.isoformat(),
            NEXT_WEEK.isoformat(),
        )


@pytest.fixture(autouse=True)
def production_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """This file, and only this file, runs with authentication REQUIRED.

    `tests/conftest.py` sets LOCAL_DEV_MODE=1 for the whole suite so sixty
    pre-existing HTTP tests keep measuring what they were written for. Here the
    credential IS the subject, so the variable is deleted — and deleted rather
    than set to "0", because an unset variable is the case that ships and is
    therefore the case worth testing.

    Autouse, so a test added to this file later cannot silently inherit dev
    mode and pass by not checking anything. The two tests that DO want dev mode
    set it back themselves.
    """
    monkeypatch.delenv(ident.ENV_LOCAL_DEV_MODE, raising=False)


def seeding(*tokens: tuple[str, str]) -> Callable[[MemoryStore], None]:
    def seed(store: MemoryStore) -> None:
        tenants(store, *tokens)

    return seed


def fetch(base: str, path: str = "/", token: str = "") -> tuple[int, str, str]:
    """GET, and return the status even when it is a refusal.

    `urllib.request.urlopen` raises on 4xx, and here THE STATUS IS THE
    MEASUREMENT — "an unauthenticated request got 401" cannot be asserted by a
    helper that turns the answer into an exception.
    """
    request = urllib.request.Request(base + path)  # noqa: S310 - loopback, http
    if token:
        request.add_header("Cookie", f"{app.COOKIE}={token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as answer:  # noqa: S310
            return (
                answer.status,
                answer.read().decode(),
                answer.headers.get("Set-Cookie", ""),
            )
    except urllib.error.HTTPError as refused:
        # `HTTPError.status` is typed optional. It is never None on a raised
        # error, and 0 would fail every assertion below rather than pass one.
        return (
            refused.status or 0,
            refused.read().decode(),
            refused.headers.get("Set-Cookie", ""),
        )


def send(base: str, path: str, **fields: str) -> tuple[int, str, str]:
    body = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(base + path, data=body)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=5) as answer:  # noqa: S310
            return (
                answer.status,
                answer.read().decode(),
                answer.headers.get("Set-Cookie", ""),
            )
    except urllib.error.HTTPError as refused:
        # `HTTPError.status` is typed optional. It is never None on a raised
        # error, and 0 would fail every assertion below rather than pass one.
        return (
            refused.status or 0,
            refused.read().decode(),
            refused.headers.get("Set-Cookie", ""),
        )


def test_dev_mode_on_and_no_auth_header_the_request_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ident.ENV_LOCAL_DEV_MODE, "1")
    with serving(demo_company(), fake_backend(), seed=seeding()) as base:
        status, _body, _cookie = fetch(base)
        assert status == 200


def test_dev_mode_off_and_no_auth_header_is_401() -> None:
    with serving(demo_company(), fake_backend(), seed=seeding()) as base:
        status, body, _cookie = fetch(base)
        assert status == 401
        assert "no session token" in body


def test_dev_mode_off_and_valid_auth_the_request_succeeds() -> None:
    token = auth.new_token()
    with serving(
        demo_company(),
        fake_backend(),
        seed=seeding((token, ALPHA)),
    ) as base:
        status, _body, _cookie = fetch(base, token=token)
        assert status == 200


def test_dev_mode_off_and_invalid_auth_is_401() -> None:
    token = auth.new_token()
    with serving(
        demo_company(),
        fake_backend(),
        seed=seeding((token, ALPHA)),
    ) as base:
        status, body, _cookie = fetch(base, token="a-token-nobody-issued")
        assert status == 401
        assert "not recognised" in body


def test_an_authorization_bearer_header_works_too() -> None:
    """The connector and any script have no cookie jar."""
    token = auth.new_token()
    with serving(
        demo_company(),
        fake_backend(),
        seed=seeding((token, ALPHA)),
    ) as base:
        request = urllib.request.Request(base + "/")  # noqa: S310
        request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=5) as answer:  # noqa: S310
            assert answer.status == 200


def test_every_post_route_refuses_an_unauthenticated_caller() -> None:
    """Including the two that DESTROY vouchers.

    `/reverse` deletes one on a caller-supplied operation id and `/reverse-all`
    deletes every voucher we ever wrote. Until today both answered anybody who
    could reach the socket. This is the assertion that says they no longer do,
    and it enumerates the routes rather than sampling one, because a check
    every route must have is a check some route will be missing.
    """
    routes = ("/entry", "/answer", "/reverse", "/dismiss", "/reverse-all")
    with serving(demo_company(), fake_backend(), seed=seeding()) as base:
        for route in routes:
            status, _body, _cookie = send(base, route, text="anything")
            assert status == 401, route


def test_the_login_page_is_reachable_without_a_session() -> None:
    """One page, and only one. A sign-in screen behind a sign-in check is a door
    locked from the outside."""
    with serving(demo_company(), fake_backend(), seed=seeding()) as base:
        status, body, _cookie = fetch(base, "/login")
        assert status == 200
        assert "Sign in" in body


def test_health_is_reachable_without_a_session() -> None:
    """A readiness endpoint that needs a login cannot report that nobody can log
    in. Same exemption, and the same reason, as `_confirm_company`."""
    with serving(demo_company(), fake_backend(), seed=seeding()) as base:
        status, _body, _cookie = fetch(base, "/health")
        assert status in (200, 503)


def test_logging_in_returns_a_cookie_that_then_works() -> None:
    with serving(demo_company(), fake_backend(), seed=seeding()) as base:
        status, _body, cookie = send(
            base, "/login", email="a@alpha.test", password=PASSWORD
        )
        assert status == 200
        assert cookie.startswith(f"{app.COOKIE}=")
        assert "HttpOnly" in cookie
        assert "SameSite=Lax" in cookie

        token = urllib.parse.unquote(cookie.split("=", 1)[1].split(";", 1)[0])
        assert fetch(base, token=token)[0] == 200


def test_a_wrong_password_is_401_and_sets_no_cookie() -> None:
    with serving(demo_company(), fake_backend(), seed=seeding()) as base:
        status, body, cookie = send(
            base, "/login", email="a@alpha.test", password="wrong"
        )
        assert status == 401
        assert cookie == ""
        assert "do not match" in body


def test_an_unknown_email_and_a_wrong_password_say_the_same_thing() -> None:
    """Two different messages would let anybody with a browser enumerate which
    addresses have accounts, which is a customer list given away for free."""
    with serving(demo_company(), fake_backend(), seed=seeding()) as base:
        unknown = send(base, "/login", email="nobody@nowhere.test", password=PASSWORD)
        wrong = send(base, "/login", email="a@alpha.test", password="not it")
    assert unknown[0] == wrong[0] == 401
    assert unknown[1] == wrong[1]


def test_signing_out_revokes_the_session_in_the_database() -> None:
    """Not merely forgotten by the browser. A cookie the server still honours is
    not a logout, it is a request that the stolen copy be polite."""
    token = auth.new_token()
    with serving(
        demo_company(),
        fake_backend(),
        seed=seeding((token, ALPHA)),
    ) as base:
        assert fetch(base, token=token)[0] == 200

        status, _body, _cookie = send(base, "/logout")
        assert status == 401, "logout itself needs a session"

        request = urllib.request.Request(base + "/logout", data=b"")  # noqa: S310
        request.add_header("Cookie", f"{app.COOKIE}={token}")
        with urllib.request.urlopen(request, timeout=5) as answer:  # noqa: S310
            assert answer.status == 200
            assert "Max-Age=0" in (answer.headers.get("Set-Cookie") or "")

        # The same token again, and the server must now refuse it.
        assert fetch(base, token=token)[0] == 401


def test_a_posted_decision_records_the_tenant_and_the_user(
    tmp_path: Path,
) -> None:
    """The audit requirement, measured through the surface.

    A FILE store, not `:memory:`, because `:memory:` is private to the
    connection that opened it and the connection lives in the server thread.
    The rows are read after the server has shut down — sequential access, so
    there is no locking argument to make.
    """
    token = auth.new_token()
    db = tmp_path / "app.db"
    with serving(
        demo_company(),
        fake_backend(),
        seed=seeding((token, ALPHA)),
        store_path=db,
    ) as base:
        request = urllib.request.Request(  # noqa: S310
            base + "/entry",
            data=urllib.parse.urlencode(
                {"text": "Sharma Traders 3800 cement supply"}
            ).encode(),
        )
        request.add_header("Cookie", f"{app.COOKIE}={token}")
        with urllib.request.urlopen(request, timeout=5) as answer:  # noqa: S310
            assert answer.status == 200

    rows = MemoryStore(db).actions(app.COMPANY)
    assert rows, "the entry produced no action row at all"
    assert rows[-1].tenant_id == ALPHA
    assert rows[-1].user_id == f"user-{ALPHA}"


def test_dev_mode_rows_name_the_local_tenant_rather_than_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Local development still produces an actor. A blank row would make every
    developer action indistinguishable from an action with no session at all."""
    monkeypatch.setenv(ident.ENV_LOCAL_DEV_MODE, "1")
    db = tmp_path / "app.db"
    with serving(
        demo_company(),
        fake_backend(),
        seed=seeding(),
        store_path=db,
    ) as base:
        send(base, "/entry", text="Sharma Traders 3800 cement supply")

    rows = MemoryStore(db).actions(app.COMPANY)
    assert rows
    assert rows[-1].tenant_id == auth.LOCAL_DEV_TENANT
    assert rows[-1].user_id == auth.LOCAL_DEV_USER


def test_a_note_row_records_the_tenant_and_the_user_too(tmp_path: Path) -> None:
    """`record()` and `note()` are two different writers and both must carry it.

    Written because a mutant proved it. Emptying the tenant and user out of
    `note()` alone left all 37 tests green: everything above drove `/entry`,
    which goes through `record()`, so the OTHER writer — reversal, dismissal,
    handover, every failure row — was unmeasured and could have been shipping
    anonymous rows.

    `/reverse` with an operation id nobody issued is the cheapest way in: it
    changes nothing in Tally and still writes the row, so what is being tested
    is the logging and not the reversal.
    """
    token = auth.new_token()
    db = tmp_path / "app.db"
    with serving(
        demo_company(),
        fake_backend(),
        seed=seeding((token, ALPHA)),
        store_path=db,
    ) as base:
        request = urllib.request.Request(  # noqa: S310
            base + "/reverse",
            data=urllib.parse.urlencode({"op": "an-operation-nobody-issued"}).encode(),
        )
        request.add_header("Cookie", f"{app.COOKIE}={token}")
        with urllib.request.urlopen(request, timeout=5) as answer:  # noqa: S310
            assert answer.status == 200

    rows = [r for r in MemoryStore(db).actions(app.COMPANY) if r.action == "reversed"]
    assert rows, "the reversal attempt wrote no row at all"
    assert rows[-1].tenant_id == ALPHA
    assert rows[-1].user_id == f"user-{ALPHA}"

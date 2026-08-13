"""The last third of `accountant/web/app.py`, in the places nothing reached.

WHY THIS FILE EXISTS
--------------------
`accountant/web/app.py` is measured at 97% by the whole suite, and the eight
things it still misses are not spread evenly: every one of them lives after line
2700, and every one of them is an ARM THAT ONLY RUNS WHEN SOMETHING GOES WRONG
or when a setting is right rather than wrong. Those are precisely the arms a
test suite grows without: the happy path exercises itself every day, and the
branch that survives a browser hanging up mid-upload is exercised by nobody
until a customer does it.

Measured on 2026-08-12, `pytest tests/ --cov=accountant.web.app`:

    2918          the drain loop's end-of-body return
    3175->3180    a logout carrying no session to revoke
    3270-3283     the answer that throws the entry away and asks for a retype
    3460          a POST to a route that does not exist
    3542->3551    a deletion that erased none of THESE books
    3848-3851     a Tally port that is not a number
    3929->3947    a startup that does NOT warn that writes are refused
    3963->3975    a startup that does NOT warn that authentication is skipped

Line 2918 is the one worth naming twice. Without it, a caller that declares a
gigabyte and then hangs up leaves `_discard_body` reading b"" forever inside a
`while left > 0` — a busy loop holding a request thread, not a dropped socket.
An uncovered line and a harmless line are different claims.

WHAT IS TESTED HERE, AND HOW
----------------------------
Over real HTTP through `tests/test_web.py::serving`, which is the ONE spin-up
path in this suite. The two requests urllib cannot express — a body that stops
early, and a `Content-Length` that lies about it — go through
`tests/test_upload.py::raw_post`, which is a REQUEST helper on that same server
and not a second one.

Every claim that could pass for the wrong reason is paired with a
`test_the_control_*` that would fail if the thing under test were broken. The
retype tests are the clearest case: "the page says type it again" would also
pass against a handler that said that to everybody, so the control asserts the
row is ABSENT until somebody actually asks.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about a real TallyPrime. The backend is `FakeTally` throughout, injected
through `app.configure()`. Evidence class: FAKETALLY over real HTTP.

Anything about the real startup CHAIN. The two banner tests replace exactly one
name — `app.real_tally`, the factory call that opens a socket — and let
`connect()`, `configure()`, the bootstrap and `runtime()` all run. What they
measure is which sentences reach the terminal, not that Tally can be reached;
`tests/test_startup_path.py` owns that end to end and this file does not restate
it.
"""

from __future__ import annotations

import re
import threading
import time
import urllib.request
from collections.abc import Iterator
from http.server import HTTPServer
from pathlib import Path

import pytest

from accountant import auth
from accountant import questions as Q
from accountant.auth import ENV_LOCAL_DEV_MODE
from accountant.memory.store import BootstrapStatus
from accountant.tallyio.factory import BackendIdentity, RealTallyRequired
from accountant.tallyio.fake import FakeTally
from accountant.web import app
from tests.test_data_deletion import (
    ALPHA,
    ANNA,
    an_entry,
    as_user,
    get_as,
    plan_id,
    seeding,
)
from tests.test_startup_path import watched_servers
from tests.test_upload import BOUNDARY, base_host, multipart_body, raw_post
from tests.test_web import (
    demo_company,
    draft_id,
    fake_backend,
    get,
    log_block,
    post,
    post_for_status,
    serving,
)

# Re-exported so pytest registers the imported fixture for this module, exactly
# as `tests/conftest.py` re-exports `server`. A fixture copied instead of
# imported is a second definition of what a running server is.
__all__ = ["watched_servers"]

#: An entry whose amount cannot be read. The question it raises is
#: `amount_is_positive`, and the ONLY answer it offers is `Q.RETYPE` — which is
#: what makes the retype arm reachable over HTTP at all.
UNREADABLE_AMOUNT = "paid Gupta Hardware for tools"

#: The marker that tells the HOME page from a decision page. Both carry a
#: `<form`, so asserting on that distinguishes nothing; only the home page
#: carries the Activity section. `tests/test_web.py::log_block` indexes on the
#: same string, which is why it is the honest discriminator here.
HOME_ONLY = "<section id=log>"

#: Bounded, because every wait here is on another thread. A wait with no
#: deadline does not fail the suite, it hangs it.
HARD_TIMEOUT = 15.0


# ---- helpers -----------------------------------------------------------------


def an_upload_declaring(base: str, declared: int, body: bytes) -> str:
    """A real `POST /upload` whose `Content-Length` says `declared`.

    The whole point is that `declared` and `len(body)` may disagree: that is the
    shape of a browser that hung up, and urllib cannot send it.
    """
    return raw_post(
        base,
        b"POST /upload HTTP/1.1\r\n"
        + f"Host: {base_host(base)}\r\n".encode()
        + f"Content-Type: multipart/form-data; boundary={BOUNDARY}\r\n".encode()
        + f"Content-Length: {declared}\r\n\r\n".encode()
        + body,
    )


def logout(base: str, token: str) -> tuple[int, str, str]:
    """Status, the `Set-Cookie` header, and the page. The header is the point.

    `tests/test_web.py::post_for_status` drops headers, and "the cookie was
    cleared" is a claim about a header. A helper rather than an inline request so
    the two tests below cannot drift apart on what a logout is.
    """
    request = urllib.request.Request(base + "/logout", data=b"")  # noqa: S310
    if token:
        request.add_header("Cookie", f"{app.COOKIE}={token}")
    with urllib.request.urlopen(request, timeout=5) as answer:  # noqa: S310
        header = answer.headers.get("Set-Cookie", "")
        return answer.status, header, answer.read().decode()


def an_entry_with_no_readable_amount(base: str) -> tuple[str, str]:
    """One entry sitting on the question whose only answer is a retype."""
    asked = post(base, "/entry", text=UNREADABLE_AMOUNT)
    problem = re.search(r'name=problem value="([^"]+)"', asked)
    assert problem, f"no question on the page:\n{asked[:600]}"
    return draft_id(asked), problem.group(1)


@pytest.fixture
def real_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authentication REQUIRED, and these books belong to ALPHA.

    `tests/conftest.py` puts the suite in LOCAL_DEV_MODE, where the principal is
    a constant with no customer record behind it — so a deletion measured there
    is refused at `_no_customer_record` and never reaches the arm below.
    """
    monkeypatch.delenv(ENV_LOCAL_DEV_MODE, raising=False)
    monkeypatch.setenv(app.ENV_TENANT, ALPHA)


def a_fake_tally_instead_of_a_socket(
    *_args: object, **_kwargs: object
) -> tuple[FakeTally, BackendIdentity]:
    """Stands in for `app.real_tally`, which is the one call that opens a socket.

    Same shape, same seam `tests/test_web.py::serving` uses one layer up. It
    swallows its arguments because none of them can change what a banner says —
    the host, the port and the run id are already printed from provenance that
    `config_from_environment` built before this is ever called.
    """
    return demo_company(), fake_backend()


@pytest.fixture
def a_startup_that_opens_no_socket_to_tally(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    """`serve()` with ONE name replaced: the factory call that opens a socket.

    `connect()`, `configure()`, the bootstrap and `runtime()` all run for real,
    so what is measured below is the banner rather than a mock of it. The
    database goes to `tmp_path` because `connect()` reaches `default_store()`,
    and a suite that writes `data/app.db` into the working tree is one commit
    away from shipping somebody's audit trail.
    """
    monkeypatch.setenv(app.ENV_DB, str(tmp_path / "app.db"))
    monkeypatch.delenv(app.ENV_BACKED_UP, raising=False)
    monkeypatch.setattr(app, "real_tally", a_fake_tally_instead_of_a_socket)
    app.disconnect()
    yield
    app.disconnect()


def serve_until_it_binds(watched: list[HTTPServer]) -> None:
    """Run `serve()` on a daemon thread and return once it has bound a socket.

    NOT a second spin-up path: `serve()` still builds its own server through
    `app.start_server`, which is the one binding site. This is a thread wrapper,
    and it exists because `serve_forever()` never returns.

    Waiting on `watched` rather than on an HTTP round trip is deliberate — every
    line `serve()` prints is printed BEFORE the socket is built, so a server in
    that list is proof the banner is complete.
    """
    failures: list[Exception] = []

    def run() -> None:
        try:
            app.serve("127.0.0.1", 0)
        except Exception as exc:  # recorded here, re-raised in this thread below
            failures.append(exc)

    threading.Thread(target=run, daemon=True).start()
    deadline = time.monotonic() + HARD_TIMEOUT
    while time.monotonic() < deadline and not watched and not failures:
        time.sleep(0.02)
    if failures:
        raise AssertionError(f"serve() refused to start: {failures[0]!r}")
    assert watched, f"serve() bound no socket within {HARD_TIMEOUT}s"
    assert len(watched) == 1, "serve() opened more than one listening socket"


# ---- an upload whose sender stopped writing ----------------------------------


def test_an_oversize_upload_that_stops_short_is_still_answered_rather_than_read_forever(
    server: str,
) -> None:
    """The drain has to end at the end of the body, not at the declared length.

    Declared the cap and a byte, sent sixteen bytes, then hung up — the ordinary
    shape of a browser or a phone losing its connection mid-upload. `read()`
    answers b"" from there on, so a drain loop that only stops when its
    countdown reaches zero never stops: it spins on empty reads holding a
    request thread, and the person gets no answer at all.
    """
    answer = an_upload_declaring(server, app.MAX_UPLOAD_BYTES + 1, b"only-sixteen-b!!")

    assert answer.startswith("HTTP/1.0 413 ")
    assert "larger than" in answer
    assert "Nothing was written to your Tally" in answer
    assert app.DRAFTS == {}


def test_the_control_a_truncated_upload_inside_the_limit_is_refused_for_its_own_reason(
    server: str,
) -> None:
    """The same truncation, one byte under the limit, and a DIFFERENT refusal.

    Without this the 413 above could be an artefact of the socket closing early
    rather than of the declared size, and the drain would be proved by nothing.
    Here the size check passes, the body really is read, and the parser refuses
    it in its own words — so the two paths are told apart by the answer.
    """
    whole = multipart_body()

    answer = an_upload_declaring(server, len(whole), whole[: len(whole) // 2])

    assert answer.startswith("HTTP/1.0 400 ")
    assert "no closing marker" in answer
    assert "Something in Accountant Dad broke" not in answer
    assert app.DRAFTS == {}


# ---- a logout with nothing to revoke -----------------------------------------


def test_a_logout_from_a_browser_holding_no_session_still_clears_the_cookie(
    server: str,
) -> None:
    """No credential, so there is no session row to revoke — and it must not go
    looking for one. The browser is still holding something; clearing it is the
    half of a logout that always applies, and the sign-in page is the answer.
    """
    status, cookie, body = logout(server, "")

    assert status == 200
    assert cookie.startswith(f"{app.COOKIE}=;")
    assert "Max-Age=0" in cookie
    assert "Sign in" in body


def test_the_control_a_logout_holding_a_live_session_revokes_it_at_the_server(
    real_authentication: None,
) -> None:
    """THE CONTROL. A logout that only cleared the cookie would pass the test
    above and would leave a stolen copy of the token working. Revoked in the
    database is what makes it a logout, so the same token is refused afterwards.
    """
    del real_authentication
    anna = auth.new_token()
    with serving(
        demo_company(), fake_backend(), seed=seeding((anna, ALPHA, ANNA))
    ) as base:
        # A GET, not an entry. Proving the session is live must not also write a
        # voucher, or the test is measuring two things and can fail for either.
        assert get_as(base, anna) == 200, "the session has to be live to be revoked"

        status, cookie, _body = logout(base, anna)

        assert status == 200
        assert "Max-Age=0" in cookie
        assert get_as(base, anna) == 401, "the revoked token still opened the books"


# ---- the answer that throws the entry away -----------------------------------


def test_answering_that_the_numbers_were_wrong_applies_nothing_and_asks_for_a_retype(
    server: str,
) -> None:
    """`Q.RETYPE` is not an answer to the question, it is a refusal to answer.

    The person is sent back to the typing box and NOTHING of the answer is
    applied. The alternative — treating it as a value and handing it to
    `pipeline.answer` — would put the literal string `__retype__` on a ledger
    leg, which is the reason this arm exists rather than being an `else`.
    """
    draft, problem = an_entry_with_no_readable_amount(server)
    live = app.runtime()
    before = live.client.trial_balance(live.company)
    voucher, answers = app.DRAFTS[draft].voucher, list(app.DRAFTS[draft].answers)

    body = post(server, "/answer", draft=draft, problem=problem, value=Q.RETYPE)

    assert "Type it again with the right numbers" in body
    assert HOME_ONLY in body, "the person is put back on the home page, not a decision"
    assert live.client.list_our_vouchers(live.company) == ()
    assert live.client.trial_balance(live.company) == before
    assert (app.DRAFTS[draft].voucher, app.DRAFTS[draft].answers) == (voucher, answers)


def test_asking_to_retype_leaves_a_durable_row_saying_the_entry_was_abandoned(
    server: str,
) -> None:
    """An entry that vanishes with no row is an entry nobody can account for.

    Read through the Activity section rather than off the store, for the reason
    `tests/test_web.py` states: a row can be written perfectly and still never
    reach a person.
    """
    draft, problem = an_entry_with_no_readable_amount(server)

    post(server, "/answer", draft=draft, problem=problem, value=Q.RETYPE)

    block = log_block(get(server))
    assert 'data-action="retype"' in block
    assert 'data-outcome="abandoned"' in block
    assert 'data-outcome="valid"' not in block


def test_the_control_an_entry_nobody_has_asked_to_retype_leaves_no_abandoned_row(
    server: str,
) -> None:
    """THE CONTROL. Without it, both tests above pass against a handler that
    wrote a retype row for every entry — and the log would then say a person
    threw away work they are in fact still being asked about."""
    an_entry_with_no_readable_amount(server)

    block = log_block(get(server))
    assert 'data-action="retype"' not in block
    assert 'data-outcome="abandoned"' not in block
    assert "data-outcome=" in block, "the entry itself was still recorded"


# ---- a POST to a route that does not exist -----------------------------------


def test_a_post_to_a_route_that_does_not_exist_answers_404_with_the_home_page(
    server: str,
) -> None:
    """404 AND a usable page. The status is for the caller that is a script; the
    page is for the person who got here from a stale bookmark, and neither is
    served by a dropped socket or a blank body."""
    status, body = post_for_status(server, "/no-such-route", text="anything")

    assert status == 404
    assert HOME_ONLY in body, "the 404 body is the home page, not an empty document"
    assert app.runtime().client.list_our_vouchers(app.COMPANY) == ()


def test_the_control_a_post_to_a_route_that_does_exist_is_not_answered_404(
    server: str,
) -> None:
    """THE CONTROL. A handler that fell through to 404 for everything would pass
    the test above, and the product would be a 404 with a form on it."""
    status, body = post_for_status(
        server, "/entry", text="paid Sharma Traders 4200 for cement"
    )

    assert status == 200
    assert "posted" in body.lower()


# ---- a deletion that erases none of THESE books ------------------------------


def test_deleting_a_customer_who_never_worked_in_these_books_leaves_the_index_readable(
    real_authentication: None,
) -> None:
    """The customer is closed; this company's learned index is not touched.

    Nothing ties this tenant to these books — no entry was ever posted — so
    `delete_tenant` erases no company, and invalidating the running memory would
    throw away an index the deletion did not remove. Every entry after it would
    become a question for no reason anybody could point at.
    """
    del real_authentication
    anna = auth.new_token()
    with serving(
        demo_company(), fake_backend(), seed=seeding((anna, ALPHA, ANNA))
    ) as base:
        status, preview = as_user(base, "/delete-my-data", anna)
        assert status == 200

        done, body = as_user(
            base, "/delete-my-data", anna, confirm="yes", plan=plan_id(preview)
        )

        assert done == 200
        assert 'data-deletion="done"' in body
        assert app.runtime().memory.ready is True
        assert app.runtime().memory.report.status is BootstrapStatus.READY


def test_the_control_deleting_a_customer_who_did_work_here_stops_the_index_answering(
    real_authentication: None,
) -> None:
    """THE CONTROL, and the same request one posted entry apart.

    That entry is the only thing that ties this tenant to these books, so it is
    the only thing that puts this company in the erased set. The index really
    goes, and a runtime that kept saying READY would be claiming to have read
    books whose derived index no longer exists.
    """
    del real_authentication
    anna = auth.new_token()
    with serving(
        demo_company(), fake_backend(), seed=seeding((anna, ALPHA, ANNA))
    ) as base:
        an_entry(base, anna)
        status, preview = as_user(base, "/delete-my-data", anna)
        assert status == 200

        done, _body = as_user(
            base, "/delete-my-data", anna, confirm="yes", plan=plan_id(preview)
        )

        assert done == 200
        assert app.runtime().memory.ready is False
        assert app.runtime().memory.report.status is BootstrapStatus.INCOMPLETE


# ---- a Tally port that is not a number ---------------------------------------


def test_a_tally_port_that_is_not_a_number_refuses_startup_rather_than_defaulting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A port that cannot be parsed is a typo, and 9000 is the wrong repair.

    Falling back would connect to a DIFFERENT Tally from the one the operator
    named, write into it, and print a startup banner that says everything is
    fine. The refusal names the variable and the value it could not read.
    """
    monkeypatch.setenv(app.ENV_PORT, "nine thousand")

    with pytest.raises(RealTallyRequired) as refused:
        app.config_from_environment()

    assert app.ENV_PORT in str(refused.value)
    assert "'nine thousand'" in str(refused.value)
    assert str(refused.value).startswith(app.REFUSAL)


def test_the_control_an_unset_port_resolves_to_the_documented_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE CONTROL. A resolver that refused every port would pass the test above
    and no operator could start the app at all."""
    monkeypatch.delenv(app.ENV_PORT, raising=False)

    config, _company, _backups, provenance = app.config_from_environment()

    assert config.port == app.TallyConfig.port
    assert f"{app.ENV_PORT}='{app.TallyConfig.port}' (default)" in provenance


# ---- the two sentences a correctly configured startup does not print ---------


@pytest.mark.usefixtures("a_startup_that_opens_no_socket_to_tally")
def test_a_startup_with_a_backup_and_real_authentication_prints_neither_warning(
    watched_servers: list[HTTPServer],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two warnings, both correctly absent, and that absence is the measurement.

    A banner that prints "WRITES WILL BE REFUSED" beside a company that IS
    backed up, or "DEVELOPMENT MODE" on a server that requires a login, is a
    banner an operator learns to scroll past — and then misses the run where it
    was true. The positive statement is still made: writes name their company.
    """
    monkeypatch.setenv(app.ENV_BACKED_UP, app.COMPANY)
    monkeypatch.delenv(ENV_LOCAL_DEV_MODE, raising=False)

    serve_until_it_binds(watched_servers)

    printed = capsys.readouterr().out
    assert "WRITES WILL BE REFUSED" not in printed
    assert "DEVELOPMENT MODE" not in printed
    assert f"writable {[app.COMPANY]}" in printed


@pytest.mark.usefixtures("a_startup_that_opens_no_socket_to_tally")
def test_the_control_a_startup_with_no_backup_in_development_mode_prints_both_warnings(
    watched_servers: list[HTTPServer],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE CONTROL. Both sentences, on the configuration that earns them.

    Without it the test above passes against a `serve()` that prints neither
    warning ever, which is the failure that costs something: a person running
    with authentication skipped and no backup recorded, and nothing on screen
    saying so.
    """
    monkeypatch.delenv(app.ENV_BACKED_UP, raising=False)
    monkeypatch.setenv(ENV_LOCAL_DEV_MODE, "1")

    serve_until_it_binds(watched_servers)

    printed = capsys.readouterr().out
    assert "WRITES WILL BE REFUSED" in printed
    assert "DEVELOPMENT MODE" in printed
    assert "writable NOTHING - reads only" in printed


# ---- REVIEW NOTES ------------------------------------------------------------
#
# This file was re-read on 2026-08-12 as if by somebody who had not written it.
# Seven things were found. Six are fixed and the seventh is left on purpose; the
# whole list is kept, because a review that leaves no record is a review nobody
# can check.
#
# 1. FIXED - A TEST NAME THAT CLAIMED MORE THAN ITS ASSERTIONS.
#    `..._throws_the_entry_away_and_posts_nothing` was measured and is false:
#    the retype arm returns BEFORE `remember_draft`, so the draft stays in
#    `app.DRAFTS`, unchanged and still answerable. Only the log row says
#    "abandoned". Renamed to `..._applies_nothing_and_asks_for_a_retype`, which
#    is what is actually proved, and an assertion was added pinning the voucher
#    and the answers as untouched. Its neighbour lost the same word for the same
#    reason. A name is an assertion nobody runs, so a name that overstates is the
#    one kind of failure the suite cannot catch.
#
# 2. FIXED - A VACUOUS ASSERTION, TWICE. `assert "<form" in body` distinguished
#    nothing: measured, the decision page and the home page BOTH carry a form,
#    so it would have held against a handler that rendered the wrong one. Both
#    now assert `HOME_ONLY` (`<section id=log>`), which only the home page has.
#    This is the exact vacuity `tests/test_web.py` records having shipped twice.
#
# 3. FIXED - A MAGIC STRING WHERE A CONSTANT EXISTS. `"__retype__"` was written
#    out in three places while `accountant/questions.py` exports `Q.RETYPE`.
#    Renaming the sentinel would have left the two positive tests failing with a
#    400 that names something else entirely, and would have left the control
#    passing - green for the wrong reason. Now `Q.RETYPE` throughout, which is
#    how `tests/test_web.py` already handles `Q.HANDOVER`.
#
# 4. FIXED - A CONTROL THAT MEASURED TWO THINGS. The logout control posted a
#    real entry to show the session was live, so it also wrote a voucher and
#    could fail for a reason that had nothing to do with logging out. It is a
#    GET now, through `tests/test_data_deletion.py::get_as`.
#
# 5. FIXED - AN UNCHECKED SIDE EFFECT. `serve_until_it_binds` waited for the
#    first listening socket and never said how many there were. It asserts
#    exactly one, which is the property `serve()` actually owes.
#
# 6. FIXED - A WRONG NUMBER IN THE DOCSTRING ABOVE. It said "the three requests
#    urllib cannot express" and named two.
#
# STILL OPEN, AND DELIBERATELY:
#
# 7. `HARD_TIMEOUT` here and `HARD_TIMEOUT` in `tests/test_startup_path.py` are
#    two constants with one name and no relationship. Importing that one would
#    couple this file's waits to a file whose waits are about something else,
#    and 15 seconds is not a threshold either file measured. Left alone rather
#    than merged, and named here so the next reader does not merge them by
#    reflex.

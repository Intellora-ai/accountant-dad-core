"""Four backend states, and none of them may be mistaken for another.

WHAT THIS FILE IS FOR
---------------------
Until 2026-08-09 every page carried a hardcoded

    "Demo mode. This is talking to a fake Tally running in memory, not real
     accounting software. Nothing here touches any real books."

That was TRUE while the app built its own `FakeTally`, and it became a LIE the
moment P3.1 wired the app to a real one. It is the dangerous direction of the
lie: a person told nothing is real will type freely into books that are.

So the notice is measured, and there are four states a reader has to be able to
tell apart:

    real-ok         a real Tally, licence measured as fully licensed
    unavailable     nothing is connected; nothing works, and the page says why
    real-practice   a REAL TallyPrime in Educational mode - real books, but it
                    only accepts the 1st, 2nd and 31st, so a bill dated the 7th
                    is turned away by Tally itself
    not-real        not accounting software at all

plus the state this instance actually lives in: `real-licence-unknown`. A11
measured, 2026-08-09, that the XML gateway will not answer `$$LicenseInfo` at
all. An unread licence is therefore the NORMAL case, and the whole design rests
on it never rendering as "connected, all good".

HOW THESE TESTS AVOID BEING VACUOUS
-----------------------------------
Two tests written earlier today were green and worthless: one searched a whole
HTML page for "valid" and passed on an empty log because the STYLESHEET contains
the word, and another searched for a vendor name that also appears in the
voucher table. Both matched something that was always there.

So every state assertion here matches `data-backend-state="..."`, which appears
in exactly ONE place in the document, and `_shows_only` additionally asserts
that no OTHER state's marker is present and that the marker occurs exactly once.
A test that says "state 3 is shown" and does not say "and not states 1, 2 and 4"
is a test that passes on a page showing all of them.

WHERE THE INPUTS COME FROM, HONESTLY
------------------------------------
The client is a `FakeTally` and the identity is a CONSTRUCTED `BackendIdentity`.
Nothing here touches a real Tally and nothing here is evidence about one.

`backend="RealTally"` in these fixtures is a string a test wrote, not a
measurement. That is deliberate and it is the only way state 3 can be tested at
all today: the licence mode cannot be read off the live instance (A11), so
feeding a constructed identity to the renderer is the honest way to prove the
RENDERER is right. What these tests prove is that a given identity produces the
right page. What produces the identity is `accountant/tallyio/factory.py`, and
what it measures today is UNKNOWN.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from accountant.memory.store import MemoryStore
from accountant.schema import Voucher
from accountant.tallyio import real
from accountant.tallyio.factory import (
    BackendIdentity,
    LicenceMode,
    new_run_id,
    real_tally,
)
from accountant.tallyio.fake import FakeTally
from accountant.web import app

ACCOUNTS = ("Purchases", "Cash")

#: Every state the app can render. A list, so the "not the other three" half of
#: each assertion cannot quietly stop covering a state somebody adds later —
#: `test_every_state_the_app_can_render_is_listed_here` pins it to the source.
STATES = (
    app.BACKEND_REAL_OK,
    app.BACKEND_UNAVAILABLE,
    app.BACKEND_REAL_PRACTICE,
    app.BACKEND_NOT_REAL,
    app.BACKEND_LICENCE_UNKNOWN,
)

#: THE CONTROL's list, whole. These are the exact words the page used to carry
#: on every screen, including screens wired to somebody's real books.
FORBIDDEN_WHEN_REAL = (
    "Demo mode",
    "fake Tally",
    "Nothing here touches any real books",
)


# ---------------------------------------------------------------------------
# fixtures - a double for the client, a constructed identity for the backend
# ---------------------------------------------------------------------------


def _tally() -> FakeTally:
    company = FakeTally()
    company.add_company(
        app.COMPANY,
        accounts=ACCOUNTS,
        vouchers=tuple(
            Voucher(
                id=f"h{i}",
                date=datetime.date(2026, 4, 1),
                party="Sharma Traders",
                narration="cement supply",
                debit_account="Purchases",
                credit_account="Cash",
                amount_paise=118000,
            )
            for i in range(4)
        ),
        backed_up=True,
    )
    return company


def _identity(backend: str, licence: str) -> BackendIdentity:
    """A constructed identity. Both strings are written here, not measured."""
    return BackendIdentity(
        backend=backend,
        endpoint="memory://tests/test_backend_states.py",
        company=app.COMPANY,
        company_exists=True,
        companies_visible=1,
        run_id=new_run_id(),
        licence_mode=licence,
        licence_detail="constructed by tests/test_backend_states.py",
    )


@contextlib.contextmanager
def _serving(identity: BackendIdentity) -> Generator[str]:
    """A real server on an ephemeral port, so the tests read SHIPPED output.

    The store is opened on the serving thread because SQLite binds a connection
    to the thread that opened it, and `configure()` bootstraps memory there.
    """
    app.DRAFTS.clear()
    httpd = HTTPServer(("127.0.0.1", 0), app.Handler)
    ready = threading.Event()

    def run() -> None:
        app.configure(_tally(), identity, store=MemoryStore(":memory:"))
        ready.set()
        httpd.serve_forever()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(timeout=5), "the server thread never configured a runtime"
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        app.disconnect()


def _page(base: str) -> str:
    """The home page, whether it answers 200 or the 503 of a dead runtime."""
    try:
        with urllib.request.urlopen(base + "/", timeout=5) as response:  # noqa: S310
            return response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.read().decode()


def _health(base: str) -> dict[str, object]:
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=5) as response:  # noqa: S310
            body: object = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode())
    assert isinstance(body, dict), "/health must answer a JSON object"
    return body  # pyright: ignore[reportUnknownVariableType]


def _marker(state: str) -> str:
    return f'data-backend-state="{state}"'


def _shows_only(page: str, state: str) -> None:
    """The whole assertion: this state, exactly once, and none of the others.

    Splitting it in two - "the state is shown" here, "the others are not"
    somewhere else - is how a page that shows every state at once passes.
    """
    assert _marker(state) in page, (
        f"the page does not carry {_marker(state)}; it says: {_notice_in(page)!r}"
    )
    for other in STATES:
        if other != state:
            assert _marker(other) not in page, (
                f"the page claims to be {state} and {other} at the same time"
            )
    assert page.count("data-backend-state=") == 1, (
        f"the state marker must appear exactly once, found "
        f"{page.count('data-backend-state=')}"
    )


def _notice_in(page: str) -> str:
    """The notice, for a failure message that says what the page actually said."""
    start = page.find("data-backend-state=")
    return "no backend notice at all" if start < 0 else page[start : start + 220]


# ---------------------------------------------------------------------------
# one test per state
# ---------------------------------------------------------------------------


def test_state_1_a_real_and_licensed_tally_says_it_is_ready_to_work() -> None:
    """The only state that reassures. It needs a MEASURED licence to appear."""
    with _serving(_identity("RealTally", LicenceMode.LICENSED.value)) as base:
        page = _page(base)

        _shows_only(page, app.BACKEND_REAL_OK)
        assert "your real books" in page
        assert "practice copy" not in page


def test_state_2_no_connection_at_all_says_so_and_says_why() -> None:
    """Nothing works, and the page has to be the thing that explains it.

    The runtime is dropped after the server starts, which is the real shape of
    the failure: the app was fine and then Tally went away.
    """
    with _serving(_identity("RealTally", LicenceMode.LICENSED.value)) as base:
        app.disconnect()
        page = _page(base)

        _shows_only(page, app.BACKEND_UNAVAILABLE)
        assert "not connected to Tally" in page
        assert app.REFUSAL in page, "the 503 must still say why it refused"


def test_state_3_a_real_tally_in_practice_mode_names_the_dates_it_will_refuse() -> None:
    """THE STATE THAT DID NOT EXIST, and the reason this task was opened.

    A person in Educational mode is on a REAL Tally holding REAL books, so
    "connected, all good" is misleading rather than merely incomplete: their
    bill dated the 7th will be silently turned away by Tally. Measured
    2026-08-07 REJECTED, 2026-08-31 ACCEPTED.

    The dates are asserted because they are the ACTIONABLE part. A warning that
    says "restricted" and does not say which dates leaves the person exactly as
    stuck as no warning at all.
    """
    with _serving(_identity("RealTally", LicenceMode.EDUCATIONAL.value)) as base:
        page = _page(base)

        _shows_only(page, app.BACKEND_REAL_PRACTICE)
        assert "practice copy" in page
        assert "1st, 2nd or 31st" in page
        assert "turn the entry away" in page


def test_state_4_a_backend_that_is_not_accounting_software_says_so_loudly() -> None:
    with _serving(_identity("FakeTally", LicenceMode.UNKNOWN.value)) as base:
        page = _page(base)

        _shows_only(page, app.BACKEND_NOT_REAL)
        assert "Not real accounting software" in page
        assert "FakeTally" in page
        assert "Nothing here reaches any real books" in page


# ---------------------------------------------------------------------------
# THE CONTROL - the wording that started this, scanned off the shipped page
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "licence",
    [
        LicenceMode.LICENSED.value,
        LicenceMode.EDUCATIONAL.value,
        LicenceMode.UNKNOWN.value,
    ],
    ids=["licensed", "educational", "unknown"],
)
def test_the_shipped_page_never_says_demo_or_fake_when_the_backend_is_real(
    licence: str,
) -> None:
    """THE CONTROL. The whole forbidden list, on real output, in every real state.

    Asserted as an ABSENCE, which is the only shape that can catch a sentence
    reappearing. Run for all three real states rather than one, because the
    reassuring string could come back on any of them and a single-state control
    would not see it.

    The two positive assertions at the end are what stop this passing on an
    empty page: an absence test over nothing is always green.
    """
    with _serving(_identity("RealTally", licence)) as base:
        page = _page(base)

        for phrase in FORBIDDEN_WHEN_REAL:
            assert phrase not in page, (
                f"the page is on a REAL Tally and still says {phrase!r}. That "
                "sentence tells a person their entries are not real when they "
                "are."
            )
        assert len(FORBIDDEN_WHEN_REAL) == 3, "the control checks the whole list"
        assert app.runtime().identity.backend == "RealTally"
        assert "data-backend-state=" in page, "the control ran against no page"


def test_the_control_would_actually_catch_the_sentence_it_is_looking_for() -> None:
    """The control's own control. An absence test nobody has ever seen fail
    is indistinguishable from an absence test that cannot fail.

    The old hardcoded notice is reconstructed here and run through the same
    check, which must reject it.
    """
    old_notice = (
        "<div class=warn>Demo mode. This is talking to a fake Tally running in "
        "memory, not real accounting software. Nothing here touches any real "
        "books.</div>"
    )

    caught = [phrase for phrase in FORBIDDEN_WHEN_REAL if phrase in old_notice]

    assert caught == list(FORBIDDEN_WHEN_REAL), (
        "the forbidden list no longer matches the sentence it exists to catch"
    )


# ---------------------------------------------------------------------------
# an UNKNOWN licence is never the all-clear
# ---------------------------------------------------------------------------


def test_an_unknown_licence_mode_is_not_rendered_as_connected_and_fine() -> None:
    """The state this instance is really in (A11), and the rule that guards it.

    The licence mode cannot be read over the XML gateway on the live Tally, so
    UNKNOWN is not an edge case here - it is the normal path. Rendering it as
    state 1 would mean the app reassures every real user by default.
    """
    with _serving(_identity("RealTally", LicenceMode.UNKNOWN.value)) as base:
        page = _page(base)

        _shows_only(page, app.BACKEND_LICENCE_UNKNOWN)
        assert "could not tell which licence mode this Tally is in" in page
        assert app.backend_state() != app.BACKEND_REAL_OK


@pytest.mark.parametrize(
    "licence",
    [
        "",
        "unknown",
        "educational",
        "Licensed",
        "LICENSED",
        "licenced",
        "yes",
        "ok",
    ],
    ids=[
        "empty",
        "unknown",
        "educational",
        "wrong_case",
        "shouting",
        "british_spelling",
        "yes",
        "ok",
    ],
)
def test_only_an_exactly_measured_licensed_mode_produces_the_all_clear(
    licence: str,
) -> None:
    """Fail closed over the whole input space, not just over UNKNOWN.

    The check in `backend_state` is `!= LICENSED`, not `== UNKNOWN`, so every
    value nobody anticipated lands on a warning. A typo, a new Tally mode and a
    field that never got filled in all behave the same way, and the way they
    behave is the safe one.
    """
    with _serving(_identity("RealTally", licence)):
        assert app.backend_state() != app.BACKEND_REAL_OK, (
            f"licence_mode={licence!r} was treated as a full licence"
        )


def test_a_licensed_mode_really_can_reach_the_all_clear() -> None:
    """The inverse of the sweep above. Without it, a `backend_state` that never
    returns real-ok at all would pass every fail-closed test in this file."""
    with _serving(_identity("RealTally", LicenceMode.LICENSED.value)):
        assert app.backend_state() == app.BACKEND_REAL_OK


# ---------------------------------------------------------------------------
# the state map itself
# ---------------------------------------------------------------------------


def test_every_state_the_app_can_render_is_listed_here() -> None:
    """Pins STATES to the source, so "not the other three" keeps meaning it."""
    assert set(STATES) == set(app.BACKEND_WORDS), (
        "a state was added or removed in accountant/web/app.py and the "
        "'and not the others' half of every test above stopped covering it"
    )
    assert len(set(STATES)) == len(STATES), "two states share a marker"


def test_every_state_has_words_and_carries_its_own_marker_exactly_once() -> None:
    """A state with no words renders a blank notice; a state carrying another
    state's marker makes every assertion in this file lie."""
    for state, words in app.BACKEND_WORDS.items():
        assert words, f"{state} would render an empty notice"
        assert words.count(_marker(state)) == 1, f"{state} does not mark itself"
        for other in STATES:
            if other != state:
                assert _marker(other) not in words, f"{state} also claims {other}"


def test_the_words_a_person_reads_carry_no_jargon() -> None:
    """Plain language is a product requirement, so it is asserted, not trusted.

    A twelve-year-old must be able to read every one of these. The list is
    checked whole rather than sampled.

    Scanned on the VISIBLE TEXT: the templates are filled in first, because
    `{endpoint}` is a placeholder nobody sees, and the tags are then stripped,
    because `class=warn` and `data-backend-state="real-licence-unknown"` are
    not words a person reads either. Scanning the raw template would fail on
    both of those and still miss whatever gets substituted in.
    """
    jargon = (
        "TDL",
        "XML",
        "gateway",
        "envelope",
        "enum",
        "licence_mode",
        "BackendIdentity",
        "LicenseInfo",
        "endpoint",
        "runtime",
        "UNKNOWN",
        "A11",
    )
    for state, template in app.BACKEND_WORDS.items():
        filled = template.format(
            backend="RealTally", endpoint="http://192.168.64.2:9000"
        )
        words = re.sub(r"<[^>]+>", " ", filled)
        assert "class=" not in words, "the tag stripper let markup through"
        for word in jargon:
            assert word.lower() not in words.lower(), f"{state}: {word}"


def test_health_and_the_page_never_disagree_about_which_state_we_are_in() -> None:
    """One measurement, two readers. Two would drift, and the one nobody is
    watching is always the one that stays wrong."""
    for licence, expected in (
        (LicenceMode.LICENSED.value, app.BACKEND_REAL_OK),
        (LicenceMode.EDUCATIONAL.value, app.BACKEND_REAL_PRACTICE),
        (LicenceMode.UNKNOWN.value, app.BACKEND_LICENCE_UNKNOWN),
    ):
        with _serving(_identity("RealTally", licence)) as base:
            body = _health(base)

            assert body["backend_state"] == expected
            assert body["licence_mode"] == licence
            _shows_only(_page(base), expected)


def test_health_says_the_licence_is_unknown_rather_than_omitting_it() -> None:
    """A missing field reads as "not applicable". This one is a finding."""
    with _serving(_identity("RealTally", LicenceMode.UNKNOWN.value)) as base:
        body = _health(base)

        assert body["licence_mode"] == LicenceMode.UNKNOWN.value
        assert "constructed by" in str(body["licence_detail"])


# ---------------------------------------------------------------------------
# the licence read itself - it must fail CLOSED, fast, and never raise
# ---------------------------------------------------------------------------
#
# A11, measured 2026-08-09 against the live TallyPrime 7.0 at 192.168.64.2:9000:
# the XML gateway does not answer $$LicenseInfo in any shape that was tried. The
# ERRORMSG text below is VERBATIM from that probe. The envelope around it is
# constructed, because only the error text was recorded.


REFUSED_BY_TALLY = (
    "<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>0</STATUS></HEADER>"
    "<BODY><DATA>"
    "<ERRORMSG>Could not find: $$LicenseInfo:IsEducationalMode</ERRORMSG>"
    "<ERRORMSG>Function Execution Failed!</ERRORMSG>"
    "</DATA></BODY></ENVELOPE>"
)


def _answered(value: str) -> str:
    return (
        "<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER>"
        f"<BODY><DATA><RESULT>{value}</RESULT></DATA></BODY></ENVELOPE>"
    )


class _Scripted:
    """A transport that answers each request from a queue, then repeats the last.

    Records every payload, so a test can assert what was sent AND how much.
    """

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.sent: list[str] = []

    def send(self, payload: str, *, retry: bool) -> str:
        self.sent.append(payload)
        assert retry is False, "the licence probe must not retry"
        index = min(len(self.sent) - 1, len(self.replies) - 1)
        return self.replies[index]


class _Dead:
    """A transport that never answers."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.sent: list[str] = []

    def send(self, payload: str, *, retry: bool) -> str:  # noqa: ARG002
        self.sent.append(payload)
        raise self.error


def _client(transport: real.Transport) -> real.RealTally:
    return real.RealTally(real.TallyConfig(), transport=transport)


def test_a_tally_that_does_not_know_the_function_yields_unknown_not_fine() -> None:
    """The measured reality. UNKNOWN is the answer, and it says why."""
    transport = _Scripted(REFUSED_BY_TALLY)

    licence = _client(transport).read_licence()

    assert licence.mode is LicenceMode.UNKNOWN
    assert licence.is_educational is None
    assert "Could not find: $$LicenseInfo:IsEducationalMode" in licence.detail
    assert "not assumed to be fine" in licence.detail


def test_a_tally_that_cannot_answer_is_asked_exactly_once() -> None:
    """Bounded. The shape that HUNG is never sent, and the shape that fails is
    not sent three times: a startup path may not be slow either."""
    transport = _Scripted(REFUSED_BY_TALLY)

    _client(transport).read_licence()

    assert len(transport.sent) == 1, (
        "a Tally that cannot answer the first licence question will not answer "
        "the other two, so asking them is only a slower startup"
    )


def test_the_licence_read_never_raises_when_tally_is_unreachable() -> None:
    """It runs on the startup path. A probe that can raise can stop the app."""
    transport = _Dead(real.TallyUnreachable("no response from Tally"))

    licence = _client(transport).read_licence()

    assert licence.mode is LicenceMode.UNKNOWN
    assert "TallyUnreachable" in licence.detail


def test_the_licence_read_never_raises_on_a_socket_error_either() -> None:
    transport = _Dead(TimeoutError("timed out"))

    licence = _client(transport).read_licence()

    assert licence.mode is LicenceMode.UNKNOWN
    assert "TimeoutError" in licence.detail


def test_the_licence_read_never_raises_on_a_body_it_cannot_parse() -> None:
    licence = _client(_Scripted("not xml at all <<<")).read_licence()

    assert licence.mode is LicenceMode.UNKNOWN


def test_the_licence_read_sends_no_import_request_anywhere() -> None:
    """Read-only, structurally. A licence probe has no business writing."""
    transport = _Scripted(_answered("Yes"), _answered("No"), _answered("0"))

    _client(transport).read_licence()

    assert transport.sent, "the probe sent nothing at all"
    for payload in transport.sent:
        assert "<TALLYREQUEST>Export</TALLYREQUEST>" in payload
        assert "Import" not in payload


def test_a_yes_to_educational_is_reported_as_educational() -> None:
    """The one path that would make state 3 real. It works the moment a Tally
    answers; today none does."""
    transport = _Scripted(_answered("Yes"), _answered("No"), _answered("0"))

    licence = _client(transport).read_licence()

    assert licence.mode is LicenceMode.EDUCATIONAL
    assert licence.is_educational is True
    assert licence.serial_number == "0"


def test_only_a_no_to_educational_and_a_yes_to_licensed_is_a_full_licence() -> None:
    transport = _Scripted(_answered("No"), _answered("Yes"), _answered("789123456"))

    licence = _client(transport).read_licence()

    assert licence.mode is LicenceMode.LICENSED
    assert licence.serial_number == "789123456"


def test_a_half_read_licence_is_unknown_rather_than_licensed() -> None:
    """Not educational, and nothing usable about the licence. Two facts are
    needed to reassure somebody and only one arrived."""
    transport = _Scripted(_answered("No"), REFUSED_BY_TALLY, REFUSED_BY_TALLY)

    licence = _client(transport).read_licence()

    assert licence.mode is LicenceMode.UNKNOWN
    assert licence.is_educational is False
    assert licence.is_licensed is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Yes", True),
        ("yes", True),
        ("True", True),
        ("1", True),
        ("No", False),
        ("no", False),
        ("0", False),
        ("maybe", None),
        ("", None),
        (None, None),
    ],
)
def test_an_unreadable_yes_or_no_becomes_none_and_never_false(
    value: str | None, expected: bool | None
) -> None:
    """None is not False. "Tally said No" and "we could not read it" are
    different facts, and treating the second as the first is how an unread
    licence turns into a confident all-clear."""
    assert real.yes_no_or_unknown(value) is expected


def test_a_response_that_both_errors_and_answers_is_treated_as_no_answer() -> None:
    """A reply we do not understand told us nothing, which is not a value."""
    both = (
        "<ENVELOPE><BODY><DATA>"
        "<ERRORMSG>Function Execution Failed!</ERRORMSG>"
        "<RESULT>No</RESULT>"
        "</DATA></BODY></ENVELOPE>"
    )

    answer = real.parse_function_answer(both)

    assert answer.result is None
    assert answer.errors == ("Function Execution Failed!",)


def test_a_reply_with_neither_a_value_nor_a_complaint_is_still_unknown() -> None:
    """The quietest failure: a well-formed envelope that says nothing.

    It is the one most likely to be read as consent, because there is no error
    to notice. The detail has to say out loud that nothing came back.
    """
    empty = "<ENVELOPE><HEADER><VERSION>1</VERSION></HEADER><BODY></BODY></ENVELOPE>"

    licence = _client(_Scripted(empty)).read_licence()

    assert licence.mode is LicenceMode.UNKNOWN
    assert "nothing we could read" in licence.detail


def test_the_licence_request_asks_for_the_function_that_was_probed() -> None:
    """The request is reproducible, not remembered. If a future build starts
    answering, this is the string it will answer."""
    payload = real.build_licence_request(real.LICENCE_IS_EDUCATIONAL)

    assert "<TYPE>Function</TYPE>" in payload
    assert "<ID>$$LicenseInfo:IsEducationalMode</ID>" in payload


def test_a_backend_identity_that_nobody_measured_defaults_to_unknown() -> None:
    """The default has to be the safe one. An identity built without a licence
    read must not arrive claiming a licence."""
    identity = BackendIdentity(
        backend="RealTally",
        endpoint="http://192.168.64.2:9000",
        company=app.COMPANY,
        company_exists=True,
        companies_visible=1,
        run_id=new_run_id(),
    )

    assert identity.licence_mode == LicenceMode.UNKNOWN.value
    assert identity.as_metrics()["licence_mode"] == LicenceMode.UNKNOWN.value


# ---------------------------------------------------------------------------
# the factory, end to end over real HTTP against a stub gateway
# ---------------------------------------------------------------------------
#
# The transport tests above inject a double, which proves `read_licence` reasons
# correctly and proves NOTHING about whether the factory ever calls it. A
# licence read that is never wired into `BackendIdentity` would pass every one
# of them, and the page would then warn about a mode nobody measured.
#
# So these two drive `real_tally()` itself, over an ordinary socket, against a
# stub that speaks Tally's XML. No live Tally, no fake client, and Export
# requests only - the stub REFUSES to answer an Import and the test asserts none
# was sent.


COMPANY_LIST = (
    "<ENVELOPE><BODY><DATA><COLLECTION>"
    f'<COMPANY NAME="{app.COMPANY}"></COMPANY>'
    "</COLLECTION></DATA></BODY></ENVELOPE>"
)


@contextlib.contextmanager
def _stub_gateway(
    *licence_replies: str,
) -> Generator[tuple[real.TallyConfig, list[str]]]:
    """A socket that speaks Tally. Yields the config pointing at it and the log."""
    received: list[str] = []

    class Gateway(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            payload = self.rfile.read(length).decode()
            received.append(payload)
            assert "<TALLYREQUEST>Import</TALLYREQUEST>" not in payload, (
                "the startup path sent an Import request; it is read-only"
            )
            if "<TYPE>Function</TYPE>" in payload:
                asked = sum("<TYPE>Function</TYPE>" in seen for seen in received)
                body = licence_replies[min(asked - 1, len(licence_replies) - 1)]
            else:
                body = COMPANY_LIST
            raw = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: object) -> None:
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Gateway)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            real.TallyConfig(
                host="127.0.0.1",
                port=httpd.server_address[1],
                timeout_seconds=5.0,
                retries=1,
            ),
            received,
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_the_factory_carries_a_refused_licence_read_through_as_unknown() -> None:
    """The measured path, end to end. A gateway that cannot answer the licence
    question still produces a working identity - and that identity says
    UNKNOWN, which the page turns into a warning rather than an all-clear."""
    with _stub_gateway(REFUSED_BY_TALLY) as (config, received):
        _, identity = real_tally(config, app.COMPANY)

    assert identity.backend == "RealTally"
    assert identity.licence_mode == LicenceMode.UNKNOWN.value
    assert "Could not find" in identity.licence_detail
    assert sum("<TYPE>Function</TYPE>" in seen for seen in received) == 1


def test_the_factory_carries_a_measured_educational_licence_through() -> None:
    """The path that makes state 3 real, proved through the factory rather than
    asserted about the renderer alone.

    Nothing on this instance answers this way today. When something does, this
    is the wiring that will carry it to the screen.
    """
    with _stub_gateway(_answered("Yes"), _answered("No"), _answered("0")) as (
        config,
        _,
    ):
        _, identity = real_tally(config, app.COMPANY)

    assert identity.licence_mode == LicenceMode.EDUCATIONAL.value

    with _serving(identity):
        assert app.backend_state() == app.BACKEND_REAL_PRACTICE

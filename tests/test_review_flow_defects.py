"""Two Phase 6 defects an audit found, and the failing tests that pin them.

Both are the same shape: a safety property that HOLDS, sitting next to a
legibility property that does not. Failing safely and failing legibly are two
different things, and `accountant/web/app.py` says so itself — it says it about
a defect that was fixed for one case in this exact file and then reappeared in
another.

DEFECT ONE — a detector that raises drops the socket
-----------------------------------------------------
`handle_one_request` catches `RuntimeError` starting with the refusal sentence
and nothing else. A detector that raises therefore escapes the handler,
`socketserver` logs a traceback and closes the connection, and the person sees
a browser error.

Nothing is posted, which is correct and already proven. But the person cannot
tell a broken app from an unreachable Tally, which is the precise complaint the
file records about the no-runtime case:

    "Failing safely and failing legibly are two properties and only the first
     was present."

DEFECT TWO — `/answer` accepts a value nobody offered
-------------------------------------------------------
`decide_problems` computes the exact allowed answers and stores them on the
`Decision` as `question_options`. Nothing reads it outside tests. The handler
takes whatever `value` the form carries and writes it straight onto a ledger
leg via `pipeline.answer`.

So a hand-made POST can set the debit account to any string at all — including
one that is not in the company's chart. It fails closed one step later, because
`accounts_exist` refuses a ledger the chart does not hold, and nothing posts.
But the refusal is a coincidence of the decision order rather than a check, and
the validation data has been sitting one field away the whole time.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about a real TallyPrime. Evidence class: FAKETALLY over real HTTP.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request

import pytest

from accountant.detect import detectors
from accountant.schema import Outcome
from accountant.web import app
from tests.test_first_detector import stale_server
from tests.test_web import get, post

__all__ = ["stale_server"]

ENTRY = "paid Sharma Traders 4200 for cement"
UNKNOWN_VENDOR = "paid Gupta Hardware 1500 for tools"


def status_of(base: str, path: str, **fields: str) -> tuple[int, str]:
    """POST and return (status, body), including for an error status."""
    data = urllib.parse.urlencode(fields).encode()
    try:
        with urllib.request.urlopen(base + path, data=data, timeout=5) as r:  # noqa: S310
            return r.status, r.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def question_on(page: str) -> tuple[str, str]:
    draft = re.search(r'name=draft value="([^"]+)"', page)
    problem = re.search(r'name=problem value="([^"]+)"', page)
    assert draft and problem, f"no question on the page:\n{page[:600]}"
    return draft.group(1), problem.group(1)


def offered_on(page: str) -> list[str]:
    return re.findall(r'name=value value="([^"]+)"', page)


# ---- defect one: a detector crash must answer, not hang up ------------------


def test_a_detector_that_raises_answers_503_instead_of_dropping_the_socket(
    stale_server: str, monkeypatch: pytest.MonkeyPatch
):
    """The person must be able to tell a broken app from an unreachable Tally.

    Nothing posting was already proven at pipeline level. What was not proven —
    and was not true — is that the person gets an answer at all.
    """

    def explodes(*_args: object, **_kwargs: object) -> tuple[list[object], int]:
        raise RuntimeError("the detector fell over")

    monkeypatch.setattr(detectors, "run", explodes)

    code, body = status_of(stale_server, "/entry", text=ENTRY)

    assert code == 503, "the service exists and cannot serve this request"
    assert "could not be finished" in body.lower()
    assert "nothing was written" in body.lower()
    assert app.runtime().client.list_our_vouchers(app.COMPANY) == ()


def test_a_failure_that_is_not_a_runtimeerror_is_also_answered(
    stale_server: str, monkeypatch: pytest.MonkeyPatch
):
    """The clause the RuntimeError test cannot reach, and a mutant proved it.

    `handle_one_request` has two arms: one for `RuntimeError`, one for
    everything else. Every crash test here raised `RuntimeError`, so deleting
    the second arm changed nothing and the suite stayed green — the arm that
    catches a `ValueError` out of the pipeline, or a `KeyError` out of a
    connector, was carrying the whole "no dropped sockets" claim untested.

    `ValueError` specifically, because `Draft.outcome` raises one on an
    unevaluated draft and that is a shape this app can genuinely produce.
    """

    def explodes(*_args: object, **_kwargs: object) -> tuple[list[object], int]:
        raise ValueError("not a RuntimeError at all")

    monkeypatch.setattr(detectors, "run", explodes)

    code, body = status_of(stale_server, "/entry", text=ENTRY)

    assert code == 503
    assert "could not be finished" in body.lower()
    assert app.runtime().client.list_our_vouchers(app.COMPANY) == ()


def test_a_keyerror_from_underneath_is_answered_too(
    stale_server: str, monkeypatch: pytest.MonkeyPatch
):
    """A second non-RuntimeError, so the arm is not pinned by one type."""

    def explodes(*_args: object, **_kwargs: object) -> tuple[list[object], int]:
        raise KeyError("no such company")

    monkeypatch.setattr(detectors, "run", explodes)

    code, _ = status_of(stale_server, "/entry", text=ENTRY)

    assert code == 503


def test_the_crash_page_does_not_leak_the_exception_text(
    stale_server: str, monkeypatch: pytest.MonkeyPatch
):
    """A stack message on a page a customer sees is a different failure.

    The row in the log carries the type and the message; the page says what
    happened and what to do, and names neither.
    """

    def explodes(*_args: object, **_kwargs: object) -> tuple[list[object], int]:
        raise RuntimeError("secret internal detail 0xdeadbeef")

    monkeypatch.setattr(detectors, "run", explodes)

    _, body = status_of(stale_server, "/entry", text=ENTRY)

    assert "0xdeadbeef" not in body
    assert "RuntimeError" not in body


def test_a_crash_leaves_a_durable_row_saying_what_broke(
    stale_server: str, monkeypatch: pytest.MonkeyPatch
):
    """The page is for the person; the log is for whoever has to fix it."""

    def explodes(*_args: object, **_kwargs: object) -> tuple[list[object], int]:
        raise RuntimeError("the detector fell over")

    monkeypatch.setattr(detectors, "run", explodes)
    status_of(stale_server, "/entry", text=ENTRY)

    home = get(stale_server)
    assert 'data-action="failed"' in home
    assert "fell over" in home


def test_the_server_still_serves_the_next_request_after_a_crash(
    stale_server: str, monkeypatch: pytest.MonkeyPatch
):
    """A handled failure must not take the process with it."""

    def explodes(*_args: object, **_kwargs: object) -> tuple[list[object], int]:
        raise RuntimeError("the detector fell over")

    monkeypatch.setattr(detectors, "run", explodes)
    status_of(stale_server, "/entry", text=ENTRY)
    monkeypatch.undo()

    assert "Trial balance" in get(stale_server)


# ---- defect two: an answer nobody offered ------------------------------------


def test_an_answer_that_was_never_offered_is_refused(stale_server: str):
    """`Decision.question_options` is the list of answers the person was shown.

    Writing anything else onto a ledger leg is accepting input the system never
    asked for. It failed closed afterwards; that is not the same as refusing.
    """
    page = post(stale_server, "/entry", text=ENTRY)
    draft, problem = question_on(page)
    offered = offered_on(page)
    assert offered, "the question offered nothing, so this test proves nothing"
    assert "Totally Made Up Ledger" not in offered

    code, body = status_of(
        stale_server,
        "/answer",
        draft=draft,
        problem=problem,
        value="Totally Made Up Ledger",
    )

    assert code == 400
    assert "was not one of the answers" in body
    assert app.runtime().client.list_our_vouchers(app.COMPANY) == ()


def test_the_refusal_does_not_change_the_draft(stale_server: str):
    """A rejected answer must leave the entry exactly where it was, still
    answerable. Half-applying it would be worse than accepting it."""
    page = post(stale_server, "/entry", text=ENTRY)
    draft, problem = question_on(page)
    before = app.DRAFTS[draft].voucher

    status_of(stale_server, "/answer", draft=draft, problem=problem, value="nonsense")

    after = app.DRAFTS[draft]
    assert after.voucher == before
    assert after.answers == []
    assert after.decision is not None, "still evaluated, still answerable"
    assert after.outcome is Outcome.UNCLEAR


def test_every_offered_answer_is_accepted(stale_server: str):
    """The control. A guard that refuses everything would pass the test above
    and break the product."""
    page = post(stale_server, "/entry", text=ENTRY)
    draft, problem = question_on(page)
    offered = offered_on(page)

    code, _ = status_of(
        stale_server, "/answer", draft=draft, problem=problem, value=offered[0]
    )

    assert code == 200


def test_the_handover_answer_still_works(stale_server: str):
    """`__handover__` and its siblings are offered answers like any other, and
    the guard must not have quietly excluded the ones with odd spellings."""
    page = post(stale_server, "/entry", text=UNKNOWN_VENDOR)
    draft, problem = question_on(page)
    offered = offered_on(page)

    from accountant import questions as Q

    assert Q.HANDOVER in offered, "the fixture no longer offers a handover"

    code, body = status_of(
        stale_server, "/answer", draft=draft, problem=problem, value=Q.HANDOVER
    )

    assert code == 200
    assert "saved" in body.lower() or "finish" in body.lower()


def test_an_answer_to_an_expired_draft_is_still_the_expired_message(
    stale_server: str,
):
    """The new guard must not shadow the older one. An expired draft is a
    different thing from an answer nobody offered, and it reads differently."""
    code, body = status_of(
        stale_server, "/answer", draft="draft-gone", problem="which_account", value="x"
    )

    assert code == 200
    assert "draft expired" in body


# ---- the flag is findable without matching prose ----------------------------


def test_the_flag_carries_a_data_attribute_a_test_can_match(stale_server: str):
    """Everything else assertable on this page has one, and the file records
    why: two tests written earlier were green and vacuous because they searched
    a whole page for a common word the stylesheet already contained.

    `"vendor_switch" in page` is true of the hidden form input as well as the
    flag, so a render that dropped the flag and kept the button would pass.
    """
    page = post(stale_server, "/entry", text=ENTRY)
    draft, problem = question_on(page)
    page = post(
        stale_server, "/answer", draft=draft, problem=problem, value="Purchases"
    )

    assert 'data-detector="vendor_switch"' in page
    assert 'data-dismissed="false"' in page

    draft_id, _ = question_on(page)
    after = post(stale_server, "/dismiss", draft=draft_id, detector="vendor_switch")
    assert 'data-dismissed="true"' in after

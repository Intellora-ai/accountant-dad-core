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
from accountant.memory.company import CompanyMatchStatus
from accountant.memory.store import MemoryStore
from accountant.schema import Outcome
from accountant.web import app
from tests.test_web import demo_company, draft_id, fake_backend, operation, serving

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
    """Type an entry that posts, and hand back its operation id.

    Driven through the surface as ONE named person rather than seeded into
    FakeTally, because what these tests are about is who did what: a voucher
    planted behind the app has no actor and could not tell Anna from Bilal.

    A KNOWN vendor, and that is the whole of it. This typed `Gupta Hardware`
    and then answered two questions, which is a longer road than any test here
    needs: an unseen name stops on a QUESTION first, so every reversal test in
    this file was made to depend on the question flow keeping exactly the shape
    it had. Measured over HTTP against `demo_company()`, the three vendor shapes
    are - an unseen one asks and writes nothing until somebody answers, a
    conflicted one asks but never resolves, and a consistent one posts without
    asking. `Sharma Traders` has forty consistent postings, so it takes the
    third path and posts straight through.

    THIS IS SETUP, NOT A TEST OF THE QUESTION FLOW. That flow is covered where
    it belongs, by `tests/test_web.py::answer_purpose_and_funding` and
    `tests/test_web.py::test_answering_the_question_posts_the_entry`; what an
    unseen vendor does - answered, and unanswered - is the pair of tests below.
    What the reversal tests need from here is a voucher that reliably exists.

    The operation id is read off the page rather than out of `app.DRAFTS` for
    the same reason the entry is typed rather than planted: a straight-through
    post renders no draft input, and `name=op` is what the person's browser
    would actually carry into `/reverse`.
    """
    status, posted = as_user(
        base,
        "/entry",
        token,
        text="paid Sharma Traders 1500 for tools",
    )
    assert status == 200, posted

    op = operation(posted)
    assert op
    return op


def test_an_unseen_and_unanswered_vendor_writes_nothing() -> None:
    """INVARIANT: a name the books have never held, that nobody has answered
    for, produces no voucher. The entry stops on a question and the register is
    still empty.

    WHY IT MATTERS. This is the whole of the owner's Decision 1 on the refusing
    side: `unseen and unanswered vendor -> refuse, write nothing`. The failure
    it exists to catch is the system inventing a ledger head for a stranger,
    which is silent, plausible and wrong in the person's real books.

    NOTHING IS ANSWERED HERE, and that is the measurement. It typed both answers
    until 2026-08-17 and asserted a NOT_VALID refusal afterwards, which stopped
    being true the moment an answer started counting as the person mapping the
    vendor - by the time it read the refusal the vendor was no longer unseen, so
    the test was measuring the answered case while claiming the unanswered one.

    THE SAFE OUTCOME HERE IS A QUESTION, NOT A REFUSAL. Measured over HTTP on
    2026-08-17: with no answers the draft is `Outcome.UNCLEAR`, memory says
    `NO_MATCH`, the page names the vendor and says it has never been posted
    before, and `list_our_vouchers` is empty. UNCLEAR is safe for the reason
    that matters - it writes nothing. The NOT_VALID refusal that names the
    vendor is a cage decision and is measured where the cage is, by
    `tests/test_gate.py::test_a_party_the_books_do_not_know_blocks_and_invents_no_name`;
    asserting it here would be asserting the string rather than the books.

    "It asked" alone is NOT the assertion. `list_our_vouchers(...) == ()` is,
    because a question that also posts is the failure nobody would notice.
    """
    anna = auth.new_token()
    with serving(demo_company(), fake_backend(), seed=colleagues((anna, ANNA))) as base:
        live = app.runtime()
        status, asked = as_user(
            base, "/entry", anna, text="paid Gupta Hardware 1500 for tools"
        )
        assert status == 200, asked
        draft = draft_id(asked)

        assert app.DRAFTS[draft].outcome is Outcome.UNCLEAR, asked
        assert "Gupta Hardware" in asked, asked
        assert "has never been posted before" in asked, asked
        assert live.memory.lookup("Gupta Hardware").status is (
            CompanyMatchStatus.NO_MATCH
        ), "an unanswered stranger became known to memory on its own"
        assert live.client.list_our_vouchers(live.company) == (), (
            "nobody answered for this vendor and a voucher was written anyway"
        )


def test_a_vendor_the_person_answered_for_is_no_longer_unknown() -> None:
    """INVARIANT: once the person has ANSWERED for a vendor the books have never
    held, that answer is a mapping, the vendor stops being a stranger, and the
    entry posts on the ordinary rules.

    WHY IT MATTERS. This is the other half of Decision 1: `previously unseen
    vendor, explicitly mapped by the user -> no longer unknown`. Without it the
    cage and the memory contradicted each other and the cage won, so answering
    the question changed nothing and the same person was asked the same question
    for ever - a loop with no exit, which is worse than a refusal because it
    looks like progress.

    IT IS THE ANSWER THAT OPENS IT, NOT THE ASKING. The mapping is asserted
    directly - `memory.lookup(...) is MATCH` - so this cannot pass on a vendor
    that merely got as far as a question. Measured 2026-08-17: `NO_MATCH` before
    the first answer, `MATCH` after it.

    THE POSTING IS ASSERTED AS MEASURED, not forced. After both answers the
    draft reaches VALID and exactly one voucher exists, carrying the two
    accounts the person named. If another gate ever blocks this entry, this test
    must fail and say so rather than be relaxed into "it did not refuse".
    """
    anna = auth.new_token()
    with serving(demo_company(), fake_backend(), seed=colleagues((anna, ANNA))) as base:
        live = app.runtime()
        status, asked = as_user(
            base, "/entry", anna, text="paid Gupta Hardware 1500 for tools"
        )
        assert status == 200, asked
        draft = draft_id(asked)
        assert live.memory.lookup("Gupta Hardware").status is (
            CompanyMatchStatus.NO_MATCH
        ), "the vendor was already known, so this proves nothing"

        status, funding = as_user(
            base,
            "/answer",
            anna,
            draft=draft,
            value="Purchases",
            problem="which_account",
        )
        assert status == 200
        assert "how did you pay" in funding.lower(), funding
        assert (
            live.memory.lookup("Gupta Hardware").status is CompanyMatchStatus.MATCH
        ), (
            "the person's answer did not become a mapping, so the vendor is "
            "still a stranger and the question will be asked again"
        )

        status, posted = as_user(
            base,
            "/answer",
            anna,
            draft=draft,
            value="Cash",
            problem="funding_is_named",
        )
        assert status == 200

        assert "never add a new name to your books on my own" not in posted, (
            "the vendor the person mapped was still refused as unseen"
        )
        assert app.DRAFTS[draft].outcome is Outcome.VALID, posted
        (written,) = live.client.list_our_vouchers(live.company)
        assert written.party == "Gupta Hardware"
        assert written.debit_account == "Purchases"
        assert written.credit_account == "Cash"
        assert written.amount_paise == 150000


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

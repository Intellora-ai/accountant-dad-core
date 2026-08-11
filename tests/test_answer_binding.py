"""An answer is only an answer to the question it was offered for.

THE DEFECT, measured over HTTP on 2026-08-10 against the demo company
---------------------------------------------------------------------
`/answer` validated the VALUE against `Decision.question_options` and took the
PROBLEM ID straight off the form. The problem id is what `pipeline.answer` reads
to choose WHICH LEDGER LEG the answer lands on. So an answer the system really
did offer, filed against a question it was not offered for, went to the wrong
leg and the value guard could not see it — the value was legitimate, the pairing
was not:

    POST /entry   text=paid Gupta Hardware 1500 for tools
    the page asks which_account, offering Purchases, Repairs & Maintenance,
                 Sundry Expenses, Printing & Stationery, Rent,
                 Electricity Charges, __handover__

    POST /answer  draft=<id>  problem=funding_is_named  value=Purchases
    -> HTTP 200
    -> the draft's credit_account is now "Purchases"

That is money recorded as having come OUT OF an expense account, through a
request the system never offered. The same hole took an absent `problem` field
as an answer to the purpose question, because the handler defaulted it to
`"which_account"` — the id whose answer sets the EXPENSE leg.

WHAT IS PINNED HERE
-------------------
One rule, in six shapes: an answer must be bound to the entry it was asked of,
the question that asked it, the problem that question fixes, and the exact set
of answers that question offered. Anything else is refused BEFORE a ledger leg,
a memory correction, a log row or a read of Tally.

Every refusal asserts four things, not one. A guard that refuses the request and
half-applies the answer is worse than one that accepts it, so each rejection
checks the status, the draft (unchanged and STILL ANSWERABLE), the register
(empty) and the trial balance (equal in exact paise).

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about a real TallyPrime. The backend is a `FakeTally` injected through
`app.configure()`. Evidence class: FAKETALLY over real HTTP.

It also proves nothing about a browser. Every request here is hand-made, which
is the point — the hand-made request is the threat — but it means the rendered
form is only ever read, never submitted by a real client.
"""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from accountant import pipeline
from accountant.schema import Outcome, Voucher
from accountant.web import app
from tests.test_web import post

# A vendor `demo_company()` has never posted, so the entry raises BOTH of this
# app's questions — what the money was for, and where it came from. Two
# questions on one draft is what makes a cross-question answer expressible at
# all, and it is the ordinary case for a new supplier, not a contrived one.
UNSEEN_VENDOR = "Gupta Hardware"

PURPOSE = "which_account"
FUNDING = "funding_is_named"


def submit(base: str, path: str, **fields: str) -> tuple[int, str]:
    """POST and return (status, body), including for an error status."""
    data = urllib.parse.urlencode(fields).encode()
    try:
        with urllib.request.urlopen(base + path, data=data, timeout=5) as r:  # noqa: S310
            return r.status, r.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def question_on(page: str) -> tuple[str, str, list[str]]:
    """The draft id, the problem id and the offered values, as rendered.

    Unescaped, because the page carries `Repairs &amp; Maintenance` and the
    ledger is called `Repairs & Maintenance`. A test that posted the escaped
    string back would be refused for the wrong reason and would prove nothing
    about binding.
    """
    draft = re.search(r'name=draft value="([^"]+)"', page)
    problem = re.search(r'name=problem value="([^"]+)"', page)
    offered = [
        html.unescape(v) for v in re.findall(r'name=value value="([^"]+)"', page)
    ]
    assert draft and problem, f"no question on the page:\n{page[:600]}"
    return draft.group(1), problem.group(1), offered


@dataclass(frozen=True)
class State:
    """Everything a refused answer must leave exactly as it found it."""

    voucher: Voucher
    answers: tuple[tuple[str, str], ...]
    outcome: Outcome
    asking: str | None
    offered: tuple[str, ...]
    written: tuple[Voucher, ...]
    balance: dict[str, int]


def state_of(draft_id: str) -> State:
    d = app.DRAFTS[draft_id]
    q = pipeline.next_question(d)
    tally = app.runtime().client
    return State(
        voucher=d.voucher,
        answers=tuple(d.answers),
        outcome=d.outcome,
        asking=None if q is None else q.problem_id,
        offered=d.decision.question_options if d.decision else (),
        written=tally.list_our_vouchers(app.COMPANY),
        balance=dict(tally.trial_balance(app.COMPANY)),
    )


def assert_refused(code: int, draft_id: str, before: State) -> State:
    """The four properties every refusal on this route must hold.

    Stated one at a time rather than as one comparison, because a failure that
    says "the trial balance moved" is worth more than one that says two large
    objects differ.
    """
    after = state_of(draft_id)

    assert code == 400, "the request is wrong, not the service"
    assert after.voucher == before.voucher, "a refused answer moved a ledger leg"
    assert after.answers == before.answers, "a refused answer was recorded anyway"
    assert after.outcome is before.outcome
    assert after.asking == before.asking, "the entry stopped asking what it was asking"
    assert after.asking is not None, "the entry must still be answerable"
    assert after.offered == before.offered
    assert after.written == (), "a refused answer reached the register"
    assert after.balance == before.balance, "the trial balance moved, in paise"
    return after


def ask_once(base: str, vendor: str = UNSEEN_VENDOR) -> tuple[str, str, list[str]]:
    """One unseen-vendor entry, sitting on its first question.

    `vendor` exists because answering teaches memory. A second entry for the
    SAME vendor is no longer unseen and is asked a different question, so a test
    that wants two comparable entries has to use two vendors this company has
    never posted. None of the names below appear in `demo_company()`.
    """
    return question_on(post(base, "/entry", text=f"paid {vendor} 1500 for tools"))


# Enough unseen vendors to give one fresh, identical purpose question per
# offered answer. Named rather than generated, so a failure says which entry.
UNSEEN_VENDORS = (
    "Alpha Traders",
    "Beta Supplies",
    "Delta Works",
    "Omega Metals",
    "Zeta Tools",
    "Sigma Goods",
    "Kappa Parts",
    "Theta Metals",
)


# ---- 1. the control: the offered answer to the question that offered it -----


def test_the_answer_the_question_offered_is_accepted_and_lands_on_its_own_leg(
    server: str,
):
    """A guard that refuses everything would pass all five tests below.

    It also pins WHICH LEG the accepted answer moves, because that is the thing
    the binding exists to protect: the purpose answer sets the expense side and
    leaves the funding side for the funding question.
    """
    draft, problem, offered = ask_once(server)
    assert problem == PURPOSE
    assert "Purchases" in offered

    code, body = submit(
        server, "/answer", draft=draft, problem=problem, value="Purchases"
    )

    assert code == 200
    held = app.DRAFTS[draft]
    assert held.voucher.debit_account == "Purchases", "the expense leg took it"
    assert held.voucher.credit_account == "", "the funding leg was left alone"
    assert held.answers == [(PURPOSE, "Purchases")]
    assert "how did you pay" in body.lower(), "and the entry moved to the next question"


def test_every_answer_the_question_offers_is_accepted(server: str):
    """Each offered value in turn, on a fresh entry, over real HTTP.

    One accepted answer proves the guard is not shut; all of them prove it does
    not quietly exclude the ones with odd spellings — `__handover__` and its
    siblings are offered answers like any other and were the shape most likely
    to be broken by a binding written against ledger names.
    """
    _, _, offered = ask_once(server)
    assert len(offered) > 1, "one option proves nothing about the set"
    assert len(UNSEEN_VENDORS) >= len(offered), (
        "not enough fresh vendors to try them all"
    )

    for value, vendor in zip(offered, UNSEEN_VENDORS, strict=False):
        draft, problem, again = ask_once(server, vendor)
        assert (problem, again) == (PURPOSE, offered), f"{vendor} was not a fresh entry"

        code, _ = submit(server, "/answer", draft=draft, problem=problem, value=value)

        assert code == 200, f"the guard refused {value!r}, which it had just offered"


# ---- 2. an offered value, filed against a question that did not offer it ----


def test_an_offered_value_sent_under_another_questions_id_is_refused(server: str):
    """THE DEFECT. `Purchases` was offered; `funding_is_named` did not offer it.

    The value guard cannot see this: the value is legitimate. What was never
    offered is the PAIRING, and the pairing is what chooses the ledger leg.
    """
    draft, problem, offered = ask_once(server)
    assert problem == PURPOSE, "the fixture must be sitting on the purpose question"
    assert "Purchases" in offered, "the value must really have been offered"
    before = state_of(draft)

    code, body = submit(
        server, "/answer", draft=draft, problem=FUNDING, value="Purchases"
    )

    assert_refused(code, draft, before)
    assert "was not one of the questions" in body
    assert app.DRAFTS[draft].voucher.credit_account == "", (
        "the funding leg took an answer offered for the purpose question"
    )


# ---- 3. an answer belonging to a different entry -----------------------------


def test_an_answer_built_for_another_entry_is_refused(server: str):
    """Two entries open at once, each on its own question.

    The second entry's answer form is submitted, whole, against the first
    entry's draft id — the ordinary shape of a second browser tab.
    """
    first, first_problem, _ = ask_once(server)
    second, second_problem, _ = ask_once(server)
    assert first != second, "two entries, two drafts"

    # Advance the SECOND entry so the two are asking different things.
    moved = post(
        server, "/answer", draft=second, problem=second_problem, value="Purchases"
    )
    _, second_now, second_offered = question_on(moved)
    assert second_now == FUNDING and second_offered, "the second entry moved on"

    before_first = state_of(first)
    before_second = state_of(second)

    code, _ = submit(
        server, "/answer", draft=first, problem=second_now, value=second_offered[0]
    )

    assert_refused(code, first, before_first)
    assert first_problem == PURPOSE
    assert state_of(second) == before_second, (
        "and the entry it was built for is untouched"
    )


def test_a_decision_belonging_to_another_entry_cannot_authorise_an_answer(server: str):
    """The entry binding at the point of USE, not at the point of a route.

    No public route hands a draft a decision minted for a different operation —
    `evaluate` stamps the draft's own id. That is exactly why the binding must
    not depend on the route: `pipeline.post` already refuses an operation id
    belonging to a different operation, and the ANSWER is the other artefact
    that authorises a ledger write.

    Both entries here are the same vendor, so the two decisions agree on the
    question, on the problem id and on every offered answer. The ONLY thing that
    differs is the entry they were made for, which is the whole point: the
    answer below is correct in every other respect.
    """
    victim, problem, offered = ask_once(server)
    other, other_problem, other_offered = ask_once(server)
    assert (problem, offered) == (other_problem, other_offered), (
        "the two decisions must be indistinguishable apart from their entry"
    )

    borrowed = app.DRAFTS[other].decision
    assert borrowed is not None
    assert borrowed.operation_id != app.DRAFTS[victim].operation_id
    app.DRAFTS[victim].decision = borrowed
    before = state_of(victim)

    code, _ = submit(
        server, "/answer", draft=victim, problem=problem, value="Purchases"
    )

    assert_refused(code, victim, before)


# ---- 4. a question that has already been answered and retired ---------------


def test_a_form_for_a_question_the_entry_has_moved_past_is_refused(server: str):
    """An expired question, in the shape that the answer-set guard cannot catch.

    The entry has answered its purpose question and moved to the funding one.
    A stale form for the purpose question arrives carrying `Cash` — a value the
    entry IS currently offering. So the value is legal, the question is not, and
    only the binding can tell the difference.

    Without it, `Cash` lands on the EXPENSE leg through the retired question's
    id and overwrites the account the person actually chose.
    """
    draft, problem, _ = ask_once(server)
    moved = post(server, "/answer", draft=draft, problem=problem, value="Purchases")
    _, now, offered_now = question_on(moved)

    assert now == FUNDING
    assert "Cash" in offered_now, "the stale form's value must be legal right now"
    before = state_of(draft)
    assert before.voucher.debit_account == "Purchases"

    code, body = submit(server, "/answer", draft=draft, problem=problem, value="Cash")

    assert_refused(code, draft, before)
    assert "was not one of the questions" in body
    assert app.DRAFTS[draft].voucher.debit_account == "Purchases", (
        "a retired question's id moved the expense leg"
    )


# ---- 5. the same answer, sent twice -----------------------------------------


def test_the_same_answer_sent_twice_is_refused_the_second_time(server: str):
    """One answer, however many clicks. The Back button, in its ordinary shape.

    `tests/test_idempotency.py` already pins that the books do not move. What is
    added here is WHY they do not: the refusal must name the question, because
    resting this property on the answer set is resting it on a coincidence —
    that the funding question happens not to offer an expense ledger. Change
    which options the next question carries and the coincidence evaporates,
    which is exactly what the expired-question test above demonstrates.
    """
    draft, problem, _ = ask_once(server)

    first, _ = submit(
        server, "/answer", draft=draft, problem=problem, value="Purchases"
    )
    assert first == 200
    before = state_of(draft)

    code, body = submit(
        server, "/answer", draft=draft, problem=problem, value="Purchases"
    )

    assert_refused(code, draft, before)
    assert "was not one of the questions" in body
    assert app.DRAFTS[draft].answers == [(PURPOSE, "Purchases")], "one answer, not two"


# ---- 6. an answer that does not say what it is answering --------------------


def test_an_answer_that_names_no_question_is_refused_rather_than_assumed(server: str):
    """The handler used to default a missing `problem` to `"which_account"`.

    A POST that named no question at all was therefore treated as an answer to
    the purpose question — the one whose answer sets the EXPENSE leg. Guessing
    which question an answer belongs to is the defect, not the fix for it.
    """
    draft, _, offered = ask_once(server)
    assert "Purchases" in offered
    before = state_of(draft)

    code, body = submit(server, "/answer", draft=draft, value="Purchases")

    assert_refused(code, draft, before)
    assert "does not say which question" in body


def test_an_answer_carrying_neither_a_question_nor_a_value_is_refused(server: str):
    """The empty POST. Nothing to bind to anything."""
    draft, _, _ = ask_once(server)
    before = state_of(draft)

    code, _ = submit(server, "/answer", draft=draft)

    assert_refused(code, draft, before)


def test_an_answer_naming_a_question_this_entry_has_never_had_is_refused(server: str):
    """A problem id that is not any of this entry's problems, offered value.

    Distinct from the cross-question case: there the id names a real question of
    this entry, asked later. Here it names nothing at all, which a fix written
    as "the id must be one this entry knows about" would catch and a fix written
    as "the id must not already be answered" would not.
    """
    draft, _, _ = ask_once(server)
    before = state_of(draft)

    code, _ = submit(
        server, "/answer", draft=draft, problem="no_such_problem", value="Purchases"
    )

    assert_refused(code, draft, before)


# ---- the two bindings no HTTP request can reach ------------------------------


def test_a_decision_with_no_question_answers_nothing(server: str):
    """Fail closed on the empty id, rather than matching an empty form field.

    `decide_problems` never builds an UNCLEAR decision without a question id, so
    no request produces this. It is asserted directly because the alternative —
    an empty id matching an absent form field — is the exact shape of the defect
    one layer up, and nothing else would notice if it came back.
    """
    del server
    from accountant.schema import Decision

    blank = Decision(outcome=Outcome.UNCLEAR, reason="x", operation_id="ad_1")

    assert blank.refuse_answer(operation_id="ad_1", problem_id="", value="") != ""
    assert blank.refuse_answer(operation_id="ad_1", problem_id="x", value="x") != ""


def test_the_id_the_decision_checks_is_the_id_the_form_sends_back(server: str):
    """The binding is worth nothing if the two strings can differ.

    Three things have to be the same id or the guard either refuses honest
    answers or lets dishonest ones through: what `decide_problems` stamps, what
    the page renders into the form, and what `pipeline.answer` reads to choose
    the ledger leg. This walks the entry through BOTH of its questions and
    checks all three at each one, over real HTTP.

    `problems.py` forces `Problem.id` and `Question.problem_id` equal and
    records the three live mismatches that made it do so. This is that rule
    restated where an ANSWER now depends on it.
    """
    draft, rendered, _ = ask_once(server)

    for expected in (PURPOSE, FUNDING):
        held = app.DRAFTS[draft]
        assert held.decision is not None
        assert rendered == expected
        assert held.decision.question_problem_id == rendered, (
            "the form carries an id the decision will not accept"
        )
        asking = pipeline.next_question(held)
        assert asking is not None and asking.problem_id == rendered

        value = "Purchases" if expected == PURPOSE else "Cash"
        page = post(server, "/answer", draft=draft, problem=rendered, value=value)
        if expected == PURPOSE:
            _, rendered, _ = question_on(page)

    # And the leg each id chose is the leg its question was about.
    posted = app.DRAFTS[draft].voucher
    assert (posted.debit_account, posted.credit_account) == ("Purchases", "Cash")


# ---- the guard steps aside where another one owns the path -------------------


def test_a_replay_of_the_answer_that_posted_is_still_the_duplicate_refusal(
    server: str,
):
    """The binding must not turn the duplicate refusal into a 400.

    A posted entry is not asking anything, so there is no question for an answer
    to be bound to. The guard that owns that path is the duplicate-operation
    refusal in `pipeline.post`, and it leaves a durable row saying the service
    would not do it twice. `tests/test_idempotency.py` pins that. Replacing it
    with "that was not one of the questions" would trade a refusal that names
    the real reason for one that does not.

    409 SINCE 2026-08-10, and it was 503 before. Both refuse and neither writes;
    what changed is honesty. 503 says the service is unavailable, and it is not
    — it worked and said no. 409 says the request conflicts with what has
    already happened, which is exactly the case.
    """
    draft, problem, _ = ask_once(server)
    post(server, "/answer", draft=draft, problem=problem, value="Purchases")
    code, body = submit(server, "/answer", draft=draft, problem=FUNDING, value="Cash")

    assert code == 200 and "posted" in body.lower()
    tally = app.runtime().client
    written = tally.list_our_vouchers(app.COMPANY)
    balance = tally.trial_balance(app.COMPANY)
    assert len(written) == 1

    replay, _ = submit(server, "/answer", draft=draft, problem=FUNDING, value="Cash")

    assert replay == 409, "the duplicate-operation refusal still owns this path"
    assert tally.list_our_vouchers(app.COMPANY) == written
    assert tally.trial_balance(app.COMPANY) == balance


def test_an_answer_to_a_draft_we_no_longer_hold_still_reads_as_expired(server: str):
    """The binding must not shadow the older guard either.

    An expired DRAFT and an answer to the wrong QUESTION are different events
    and read differently. This one is a normal, uninteresting end to a form.
    """
    code, body = submit(
        server, "/answer", draft="no-such-draft", problem=PURPOSE, value="Purchases"
    )

    assert code == 200
    assert "expired" in body.lower()
    assert app.runtime().client.list_our_vouchers(app.COMPANY) == ()

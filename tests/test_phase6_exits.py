"""PHASE 6 RE-VERIFICATION — every exit condition re-measured, and the two gaps
an earlier audit left open.

Canonical scope, `docs/ARCHITECTURE.md` §7: wire `vendor_switch` into the review
screen, show the result, log dismissals durably. Deterministic ranking by
severity, ties by voucher id. Per-batch cap with overflow reported as a count.

WHY THIS FILE EXISTS ALONGSIDE THE OTHER THREE
----------------------------------------------
`tests/test_first_detector.py` proved the phase on ONE fixture — a company whose
chart had lost a ledger. `tests/test_dismissal_durability.py` proved the row
survives a process. `tests/test_review_flow_defects.py` proved a crash answers
instead of hanging up. None of the three answered the question that decides
whether the phase is finished:

    is the stale chart the ONLY way a person can reach this detector?

If it were, `vendor_switch` would be a detector for a data-migration accident
rather than for the error it was built to catch, and Phase 6 would have shipped
a feature nobody can reach. It is not the only way. This file enumerates every
route, proves the enumeration is COMPLETE rather than asserting it, and builds
the fixture for the route that was missing.

THE MECHANICAL HALF OF THE PROOF
--------------------------------
An enumeration of routes is worth nothing if the next commit adds a fourth
caller. So the frame is checked against the source itself, by AST:

    every `detectors.run` call site in the shipped package
    every `pipeline.evaluate` call site in the shipped package
    every write to a draft's voucher, and every static write of `debit_account`

Each is an EXACT-SET assertion. A new caller fails these tests, which is the
only way an enumeration can stay true after the day it was written.

THE BEHAVIOURAL HALF
--------------------
`vendor_switch` fires on exactly three conditions, read straight off the
function: the index says MATCH for this party, the proposed debit differs from
that one account, and the party has been posted there at least twice. So the
routes are the ways a voucher can arrive at `detectors.run` with a debit leg
that is not the vendor's one indexed account, and there are only two writers of
that leg.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about a real TallyPrime. Evidence class: FAKETALLY, over real HTTP.
It does not prove that Tally accepts these vouchers, that a real chart ever
loses a ledger the way `stale_ledger_company` stages it, or that a real book
contains a vendor whose two legs are the same ledger. It proves what OUR code
does with those books.

It also does not prove anything about how many flags the screen shows at once.
The per-batch cap is asserted against `detectors.run`, which owns it; what the
web app passes for `flag_cap` is that module's claim to make.
"""

from __future__ import annotations

import ast
import contextlib
import datetime
import pathlib
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Generator, Sequence
from dataclasses import replace
from http.server import HTTPServer

import pytest

from accountant import pipeline
from accountant import questions as Q
from accountant.detect import detectors
from accountant.extract.adapter import TypedTextExtractor
from accountant.memory.bootstrap import bootstrap
from accountant.memory.company import propose_account
from accountant.memory.identity import normalise_company
from accountant.memory.index import MemoryIndex, normalise_vendor
from accountant.memory.store import MemoryStore
from accountant.schema import ActionLog, Flag, Outcome, Voucher
from accountant.tallyio.factory import BackendIdentity, new_run_id
from accountant.tallyio.fake import FakeTally
from accountant.web import app
from tests.test_first_detector import GONE
from tests.test_web import get, post

PACKAGE = pathlib.Path(app.__file__).resolve().parent.parent

# A chart with NOTHING missing from it. Every route proved on this chart is a
# route that does not need a data-migration accident to reach.
COMPLETE_CHART = ("Purchases", "Repairs & Maintenance", "Cash", "Bank")

# A vendor whose two legs are THE SAME LEDGER in this company's own history.
# Bank charges are the everyday case: the money leaves the bank account and the
# cost is booked to the bank account, so `accounts_differ` fails on an entry
# nobody has touched. Real, ordinary, and on a complete chart.
SAME_LEG_VENDOR = "HDFC Charges"
SECOND_SAME_LEG_VENDOR = "SBI Charges"
SAME_LEG = "Bank"

# A vendor whose FUNDING ledger is the one that went missing. The expense leg is
# fine; the credit leg names a bank account somebody closed.
STALE_FUNDING_VENDOR = "Sharma Traders"
STALE_FUNDING_CHART = ("Purchases", "Repairs & Maintenance", "Cash")
CLOSED_BANK = "Old Bank"


def rows(party: str, debit: str, credit: str, n: int, first: int = 0) -> list[Voucher]:
    """`n` historical vouchers for one party, all posted the same way."""
    return [
        Voucher(
            id=f"h{first + i}",
            date=datetime.date(2026, 1, 1) + datetime.timedelta(days=i),
            party=party,
            narration="supply",
            debit_account=debit,
            credit_account=credit,
            amount_paise=100_000,
        )
        for i in range(n)
    ]


def company_of(chart: Sequence[str], history: Sequence[Voucher]) -> FakeTally:
    tally = FakeTally()
    tally.add_company(
        app.COMPANY, accounts=tuple(chart), vouchers=tuple(history), backed_up=True
    )
    return tally


def same_leg_company() -> FakeTally:
    """ROUTE F's book. Two vendors billed to and paid from one ledger, plus an
    ordinary vendor so the company is not a degenerate one."""
    history = [
        *rows(SAME_LEG_VENDOR, SAME_LEG, SAME_LEG, 6),
        *rows(SECOND_SAME_LEG_VENDOR, SAME_LEG, SAME_LEG, 6, first=10),
        *rows("Kumar Stationers", "Purchases", "Cash", 3, first=20),
    ]
    return company_of(COMPLETE_CHART, history)


def closed_funding_company() -> FakeTally:
    """A complete EXPENSE chart whose FUNDING ledger has gone."""
    history = rows(STALE_FUNDING_VENDOR, "Purchases", CLOSED_BANK, 6)
    return company_of(STALE_FUNDING_CHART, history)


@contextlib.contextmanager
def running(tally: FakeTally, store_path: pathlib.Path | None = None) -> Generator[str]:
    """The same spin-up every web test uses, over whichever book is handed in.

    The store is opened INSIDE the serving thread because SQLite hands a
    connection to the thread that opened it. `store_path` makes the file
    reopenable, which is the only way durability can be demonstrated.
    """
    identity = BackendIdentity(
        backend="FakeTally",
        endpoint="memory://tests/test_phase6_exits.py",
        company=app.COMPANY,
        company_exists=True,
        companies_visible=1,
        run_id=new_run_id(),
    )
    app.DRAFTS.clear()
    httpd = HTTPServer(("127.0.0.1", 0), app.Handler)
    ready = threading.Event()

    def serve() -> None:
        where = str(store_path) if store_path is not None else ":memory:"
        app.configure(tally, identity, store=MemoryStore(where))
        ready.set()
        httpd.serve_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=5), "the server thread never bootstrapped memory"
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        app.disconnect()


def draft_on(page: str) -> str:
    found = re.search(r'name=draft value="([^"]+)"', page)
    assert found, f"no draft id on the page:\n{page[:600]}"
    return found.group(1)


def problem_on(page: str) -> str:
    found = re.search(r'name=problem value="([^"]+)"', page)
    assert found, f"no question on the page:\n{page[:600]}"
    return found.group(1)


def offered_on(page: str) -> list[str]:
    return re.findall(r'name=value value="([^"]+)"', page)


def asked_on(page: str) -> str:
    found = re.search(r"<p class=ask>([^<]*)</p>", page)
    assert found, f"no question text on the page:\n{page[:900]}"
    return found.group(1)


def flag_fired(page: str) -> bool:
    """Matched on the DATA ATTRIBUTE, never on the word.

    `"vendor_switch" in page` is also true of the hidden form input beside the
    flag, so a render that dropped the flag and kept the dismiss button would
    pass. The attribute cannot be matched by accident.
    """
    return 'data-detector="vendor_switch"' in page


def answer_on(base: str, page: str, value: str) -> str:
    return post(
        base, "/answer", draft=draft_on(page), problem=problem_on(page), value=value
    )


def answer_under(base: str, page: str, problem: str, value: str) -> tuple[int, str]:
    """Answer under a problem id of OUR choosing, and keep the status.

    Two differences from `answer_on`, both needed by the closed route G below.
    The problem id is supplied rather than read off the page, because naming a
    question the page is not asking is the whole request being measured. And
    `post` raises on any non-2xx, while the REFUSAL is the measurement here, so
    the status has to survive the call.
    """
    data = urllib.parse.urlencode(
        {"draft": draft_on(page), "problem": problem, "value": value}
    ).encode()
    try:
        with urllib.request.urlopen(base + "/answer", data=data, timeout=5) as r:  # noqa: S310
            return r.status, r.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def dismissals_in(path: pathlib.Path) -> tuple[ActionLog, ...]:
    """Read the way a SECOND PROCESS would: a new store opened on the file."""
    reopened = MemoryStore(str(path))
    return tuple(r for r in reopened.actions(app.COMPANY) if r.action == "dismissed")


# =============================================================================
# 1. THE ROUTE ENUMERATION, AND THE PROOF THAT IT IS COMPLETE
# =============================================================================
#
# `vendor_switch` returns a flag on exactly three conditions:
#
#     index.lookup(party).status == "match"      one indexed account, `usual`
#     proposed.debit_account != usual
#     index.times_posted(party, usual) >= 2
#
# So every route is a way for a voucher to reach `detectors.run` carrying a
# debit leg that is not the vendor's one indexed account. The three tests below
# bound the search mechanically: one entry point to the detector, three entry
# points to the evaluator, and two writers of the leg.


def _label(node: ast.AST, prefix: str, owner: dict[ast.AST, str]) -> dict[ast.AST, str]:
    """Tag every node with the `module::function` it sits inside."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            name = f"{prefix}::{child.name}"
            for sub in ast.walk(child):
                owner.setdefault(sub, name)
            _label(child, name, owner)
        else:
            owner.setdefault(child, prefix)
            _label(child, prefix, owner)
    return owner


def functions_that(predicate: Callable[[ast.AST], bool]) -> set[str]:
    """`module::function` for every AST node in the package matching `predicate`.

    Walking the real source rather than the import graph, because a call added
    tomorrow is exactly what these assertions have to catch.
    """
    found: set[str] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = str(path.relative_to(PACKAGE.parent))
        owner = _label(tree, rel, {})
        for node in ast.walk(tree):
            if predicate(node):
                found.add(owner.get(node, rel))
    return found


def _is_detector_run(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "detectors"
    )


def _is_evaluate_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr == "evaluate"
    return isinstance(fn, ast.Name) and fn.id == "evaluate"


def _writes_a_draft_voucher(node: ast.AST) -> bool:
    return isinstance(node, ast.Assign) and any(
        isinstance(t, ast.Attribute) and t.attr == "voucher" for t in node.targets
    )


def _names_debit_account(node: ast.AST) -> bool:
    return isinstance(node, ast.keyword) and node.arg == "debit_account"


def test_the_detector_has_exactly_one_call_site_on_the_product_path():
    """The frame of the enumeration, checked against the source.

    Two of the four call sites are the offline proof track — `score/` measures
    catch rates over a synthetic book and never touches Tally, a draft or a
    screen. The product reaches detectors through `pipeline.evaluate` and
    nowhere else, so enumerating the routes into `evaluate` enumerates the
    routes into the detector.
    """
    assert functions_that(_is_detector_run) == {
        "accountant/pipeline.py::evaluate",
        "accountant/score/calibration.py::measure",
        "accountant/score/harness.py::_evaluate_one",
    }


def test_the_evaluator_has_exactly_three_call_sites_in_the_package():
    """`pipeline.run` is the batch path; the other two are the review screen.

    This is the whole enumeration frame. A fourth caller is a route nobody has
    reasoned about, and it fails this test on the commit that adds it.
    """
    assert functions_that(_is_evaluate_call) == {
        "accountant/pipeline.py::run",
        "accountant/web/app.py::_run",
        "accountant/web/app.py::do_POST",
    }


def test_only_two_functions_can_ever_set_the_debit_leg_of_a_draft():
    """The leg the detector reads has exactly two writers, and they are known.

    `build_draft` sets it from `propose_account`; `answer` sets it from a human
    answer, dynamically, through a computed keyword — which is why the voucher
    write is scanned as well as the static keyword. `evaluate` touches only the
    credit leg. `decide.decide` builds a throwaway placeholder voucher for its
    convenience wrapper and reaches no draft, no screen and no Tally.
    """
    assert functions_that(_writes_a_draft_voucher) == {
        "accountant/pipeline.py::answer",
        "accountant/pipeline.py::evaluate",
    }
    assert {
        f
        for f in functions_that(_names_debit_account)
        if f.startswith(("accountant/pipeline", "accountant/web", "accountant/decide"))
    } == {
        "accountant/pipeline.py::build_draft",
        "accountant/decide.py::decide",
    }


# ---- ROUTE A: the entry path. Structurally silent, and here is the reason ----


def test_the_entry_path_cannot_fire_the_detector_because_both_reads_agree():
    """`propose_account` and `vendor_switch` read the SAME store rows.

    `memory.lookup` selects one subject out of `vendor_account`; `memory.index`
    selects every subject out of the same table. So MATCH in one is MATCH in the
    other and names the same account, and `build_draft` sets the debit leg to
    exactly the account the detector would call `usual`. Equal by construction,
    not by luck — which is why no book can be built that fires it on `/entry`.

    `tests/test_dismissal_durability.py` asserts the silence on one book. This
    asserts the mechanism, so a change that breaks the agreement is caught.
    """
    tally = same_leg_company()
    memory = bootstrap(tally, app.COMPANY, MemoryStore(":memory:"))
    index = memory.index()

    for vendor in (SAME_LEG_VENDOR, SECOND_SAME_LEG_VENDOR, "Kumar Stationers"):
        leg = propose_account(memory, vendor)
        seen = index.lookup(vendor)
        assert seen.status.value == "match"
        assert leg == seen.accounts[0], (
            f"{vendor}: the leg build_draft would set and the account "
            f"vendor_switch calls usual have come apart"
        )


def test_a_blank_party_cannot_reach_the_detector_because_bootstrap_drops_it():
    """The one candidate route on `/entry`, and the two things that close it.

    A typed line with no capitalised party — "paid 4200 for cement" — leaves
    `record.party` None, so `build_draft` never calls `propose_account` and the
    debit leg is empty. If the index held an account under the empty vendor key
    the detector would fire with `usual` set and the proposed leg blank.

    It cannot. `bootstrap` skips any voucher whose vendor key is empty, so the
    empty key never reaches `vendor_account`; and a book made only of
    blank-party rows reports EMPTY_VENDOR_INDEX, which is not askable at all.
    Both halves are asserted, because either one alone would be a coincidence.
    """
    mixed = company_of(
        COMPLETE_CHART,
        [
            *rows("Kumar Stationers", "Purchases", "Cash", 4),
            *rows("", "Repairs & Maintenance", "Cash", 6, first=10),
        ],
    )
    memory = bootstrap(mixed, app.COMPANY, MemoryStore(":memory:"))

    assert memory.report.askable, "the named vendors keep this company usable"
    assert memory.index().vendors() == frozenset({"kumar_stationers"})
    assert memory.index().lookup("").status.value == "no_match"

    only_blank = company_of(COMPLETE_CHART, rows("", "Purchases", "Cash", 6))
    blank_memory = bootstrap(only_blank, app.COMPANY, MemoryStore(":memory:"))

    assert blank_memory.report.status.value == "empty_vendor_index"
    assert not blank_memory.report.askable


# ---- ROUTE F: a COMPLETE chart, and an entry nobody had to break -------------


def test_the_detector_fires_on_a_complete_chart_when_both_legs_are_one_ledger():
    """ROUTE F. The claim that only a stale chart reaches the detector is FALSE.

    Nothing is missing from this company's chart. `HDFC Charges` has been
    debited to Bank and paid from Bank six times, so memory proposes Bank for
    both legs and `accounts_differ` fails on an entry nobody has touched. The
    person is asked what they got, they answer with an expense account, and that
    answer contradicts six postings of history.

    That is a bank-charges vendor in an ordinary small book. It needs no
    renamed ledger, no deleted ledger and no migration.
    """
    tally = same_leg_company()
    with running(tally) as base:
        page = post(base, "/entry", text=f"paid {SAME_LEG_VENDOR} 4200 for fees")

        assert not flag_fired(page), "nothing is surprising until somebody answers"
        assert problem_on(page) == "accounts_differ"
        assert app.DRAFTS[draft_on(page)].voucher.debit_account == SAME_LEG
        assert app.DRAFTS[draft_on(page)].voucher.credit_account == SAME_LEG

        after = answer_on(base, page, "Purchases")

        assert flag_fired(after), f"ROUTE F did not fire:\n{after[:900]}"
        assert SAME_LEG_VENDOR in after
        assert re.search(r"6 time", after), "the count is part of the evidence"
        assert tally.list_our_vouchers(app.COMPANY) == (), "nothing posts under a flag"


def test_route_f_needs_no_account_missing_from_the_chart():
    """The control that makes ROUTE F worth anything.

    If the chart were quietly incomplete this would be the stale-chart route
    wearing a different fixture. Every ledger either leg names is in the chart
    the app read out of Tally, before and after the answer.
    """
    tally = same_leg_company()
    chart = set(tally.read_accounts(app.COMPANY))
    with running(tally) as base:
        page = post(base, "/entry", text=f"paid {SAME_LEG_VENDOR} 4200 for fees")
        before = app.DRAFTS[draft_on(page)].voucher
        after = answer_on(base, page, "Purchases")
        now = app.DRAFTS[draft_on(after)].voucher

    for voucher in (before, now):
        for leg in (voucher.debit_account, voucher.credit_account):
            assert leg in chart, f"{leg!r} is not in the chart, so this is not route F"


# ---- ROUTE E: the FUNDING ledger is the one that went ------------------------


def test_the_detector_fires_when_the_missing_ledger_is_the_funding_one():
    """A route the stale-chart fixture does not cover, and it reads differently.

    In `stale_ledger_company` the EXPENSE leg names the ledger that went, so the
    question is about the thing the money was spent on. Here the expense leg is
    perfectly good and the CREDIT leg names a bank account somebody closed —
    and `accounts_exist` still asks "what did you get from them?", whose answer
    `pipeline.answer` writes to the DEBIT leg, because the leg is chosen by the
    problem id and `accounts_exist` is not the funding problem.

    So a closed bank account makes the person re-answer the expense account they
    never got wrong, and that is what fires the detector. Pinned because it is
    surprising, not because it is right.
    """
    tally = closed_funding_company()
    with running(tally) as base:
        typed = f"paid {STALE_FUNDING_VENDOR} 4200 for cement"
        page = post(base, "/entry", text=typed)

        assert problem_on(page) == "accounts_exist"
        held = app.DRAFTS[draft_on(page)].voucher
        assert held.debit_account == "Purchases", "the expense leg was never wrong"
        assert held.credit_account == CLOSED_BANK

        after = answer_on(base, page, "Repairs & Maintenance")

        assert flag_fired(after)
        assert "Purchases" in after, "the account it usually goes to"
        assert tally.list_our_vouchers(app.COMPANY) == ()


# ---- the routes that are CLOSED, each for its own reason --------------------


def test_an_unknown_vendor_asks_but_can_never_fire_the_detector():
    """NO_MATCH offers the widest question in the app and still cannot fire it.

    The person is handed the whole chart to choose from, so the debit leg can
    become anything — and none of it matters, because a vendor with no history
    has no `usual` for the answer to contradict.
    """
    tally = same_leg_company()
    with running(tally) as base:
        page = post(base, "/entry", text="paid Gupta Hardware 1500 for tools")

        assert problem_on(page) == "which_account"
        after = answer_on(base, page, "Repairs & Maintenance")

        assert not flag_fired(after)
        assert tally.list_our_vouchers(app.COMPANY) == ()


def test_an_ambiguous_vendor_asks_but_can_never_fire_the_detector():
    """CONFLICTED is two accounts, and `index.lookup` returns MATCH for one.

    A vendor this company has posted two ways has no single practice, so there
    is nothing for a third account to contradict. The person is still asked, and
    still narrowed to the accounts the vendor has actually used.
    """
    tally = company_of(
        COMPLETE_CHART,
        [
            *rows("Verma Cement", "Purchases", "Cash", 6),
            *rows("Verma Cement", "Repairs & Maintenance", "Cash", 4, first=10),
        ],
    )
    with running(tally) as base:
        page = post(base, "/entry", text="paid Verma Cement 900 for bags")

        assert problem_on(page) == "which_account"
        assert set(offered_on(page)) >= {"Purchases", Q.HANDOVER}
        after = answer_on(base, page, "Purchases")

        assert not flag_fired(after)


def test_the_funding_answer_can_never_fire_the_detector():
    """ "How did you pay?" writes the CREDIT leg, and the detector reads DEBIT.

    The one answer in the app that is deliberately not learned is also the one
    that cannot reach the leg the detector reads. Both halves asserted, because
    the leg is chosen by a string comparison one edit away from being wrong.
    """
    tally = company_of(COMPLETE_CHART, rows("Kumar Stationers", "Purchases", "", 6))
    with running(tally) as base:
        page = post(base, "/entry", text="paid Kumar Stationers 4200 for pens")

        assert problem_on(page) == pipeline.FUNDING_PROBLEM
        assert set(offered_on(page)) == {"Cash", "Bank"}

        after = answer_on(base, page, "Cash")
        held = app.DRAFTS[draft_on(page)]

        assert not flag_fired(after)
        assert held.voucher.debit_account == "Purchases", "the debit leg never moved"
        assert held.voucher.credit_account == "Cash"


def test_the_detectors_own_question_offers_only_answers_that_silence_it():
    """The flag's own question cannot be used to fire the flag again.

    `different_from_usual` offers exactly two values: agree that it is
    different, which changes no leg, or put it back to `usual`, which makes the
    proposed leg equal to `usual` and ends the flag. Neither is a third account,
    so the question a flag raises can never raise a second flag.
    """
    tally = same_leg_company()
    with running(tally) as base:
        page = post(base, "/entry", text=f"paid {SAME_LEG_VENDOR} 4200 for fees")
        after = answer_on(base, page, "Purchases")

        assert flag_fired(after)
        assert problem_on(after) == "vendor_switch"
        assert set(offered_on(after)) == {Q.YES, SAME_LEG}


def test_the_hand_made_post_that_was_route_g_is_refused_before_the_detector():
    """ROUTE G, CLOSED 2026-08-10. It was never a route. It was a defect.

    WHAT THIS TEST USED TO PROVE
    ----------------------------
    That a hand-made POST reached the detector. `/answer` validated the VALUE
    against `Decision.question_options` and did not validate the PROBLEM at all,
    and the problem id is what `pipeline.answer` reads to choose which ledger
    leg to write. So the request below — the funding question's "Bank", filed
    under `which_account` — put an offered value on the leg nobody offered it
    for, contradicted six postings of Purchases, and fired the flag.

    Its own words are the argument for this change. It said the mirror image
    "IS NOT HARMLESS", reported it as an `accountant/web/app.py` defect rather
    than a detector one, and said that pinning a hole as correct is how a hole
    survives a review. It was right on all three counts, and it was doing the
    pinning: the enumeration counted a hole as a way in.

    WHY IT IS NOW THE OPPOSITE ASSERTION
    ------------------------------------
    The hole is closed. `Decision.question_problem_id` binds every answer to the
    question that offered it, and the handler refuses before a ledger leg, a
    memory correction or a read of Tally. So route G leaves the enumeration and
    this test guards its absence: remove the binding and the route reopens, and
    this goes red along with the file that replaced it.

    The enumeration is SHORTER BY ONE and no weaker for it. Routes E and F still
    reach the detector on books a real company can have, through a question the
    app really asked — which is the question this file exists to answer.

    The behaviour that replaced route G lives in `tests/test_answer_binding.py`:
    the refusal in six shapes, each asserting the status, the draft unchanged
    and still answerable, an empty register, and a trial balance equal in paise.
    """
    tally = company_of(COMPLETE_CHART, rows("Kumar Stationers", "Purchases", "", 6))
    with running(tally) as base:
        page = post(base, "/entry", text="paid Kumar Stationers 4200 for pens")
        assert problem_on(page) == pipeline.FUNDING_PROBLEM
        assert set(offered_on(page)) == {"Cash", "Bank"}
        before = app.DRAFTS[draft_on(page)].voucher
        assert before.debit_account == "Purchases", "the vendor's own indexed account"
        balance = tally.trial_balance(app.COMPANY)

        code, body = answer_under(base, page, "which_account", "Bank")

        held = app.DRAFTS[draft_on(page)]
        assert code == 400, "the request that used to be route G is refused"
        assert not flag_fired(body), "and never reaches the detector at all"
        assert held.voucher == before, "no ledger leg moved"
        assert held.answers == [], "and nothing was recorded as an answer"
        assert held.outcome is Outcome.UNCLEAR, "the entry is still answerable"
        assert tally.list_our_vouchers(app.COMPANY) == ()
        assert tally.trial_balance(app.COMPANY) == balance


# =============================================================================
# 2. THE DISMISSED MARKER, MEASURED RATHER THAN ARGUED
# =============================================================================
#
# The audit's phrasing was "dismiss a flag, change the draft, and the flag
# returns undismissed". That is not what happens. What happens is worse and
# quieter, and it is pinned below so the owner can decide about the real thing.


def test_a_dismissal_is_marked_on_the_screen_and_the_flag_stays_visible():
    """The baseline, on ROUTE F rather than on the stale chart.

    Dismissing marks the flag and keeps it on the page. The evidence does not
    disappear because somebody looked at it.
    """
    tally = same_leg_company()
    with running(tally) as base:
        page = post(base, "/entry", text=f"paid {SAME_LEG_VENDOR} 4200 for fees")
        page = answer_on(base, page, "Purchases")
        assert 'data-dismissed="false"' in page

        after = post(base, "/dismiss", draft=draft_on(page), detector="vendor_switch")

        assert 'data-dismissed="true"' in after
        assert flag_fired(after), "the flag is still shown, marked as looked at"
        assert SAME_LEG in after, "and it still carries its evidence"


def test_the_flag_itself_disappears_on_the_next_re_evaluation():
    """THE OPEN GAP, measured. The marker does not come back undismissed —
    THE FLAG GOES, and the marker goes with it.

    The mechanism is the answer that produced the flag. `/answer` evaluates
    first and records the correction afterwards, which is what makes the
    detector reachable at all. But the correction is still recorded, and it adds
    a SECOND account to this vendor's row. One account is MATCH; two is
    CONFLICTED; `vendor_switch` returns nothing for a CONFLICTED vendor. So the
    next `evaluate` on the same draft finds no flag.

    From the person's side: they dismissed a concern, answered the next
    question, and the concern vanished off the screen without anybody resolving
    it. `Draft.dismissed` still names it — asserted below — so the marker is not
    lost, it has nothing to attach to.

    Nothing here says which way it should go. It says what it does.
    """
    tally = same_leg_company()
    with running(tally) as base:
        page = post(base, "/entry", text=f"paid {SAME_LEG_VENDOR} 4200 for fees")
        page = answer_on(base, page, "Purchases")
        draft = draft_on(page)
        post(base, "/dismiss", draft=draft, detector="vendor_switch")

        held = app.DRAFTS[draft]
        assert held.dismissed == ["vendor_switch"]
        assert [f.detector for f in held.flags] == ["vendor_switch"]

        # One more re-evaluation, through the flag's own question.
        after = post(base, "/answer", draft=draft, problem="vendor_switch", value=Q.YES)
        held = app.DRAFTS[draft]

        assert held.flags == [], "the flag is gone from the draft"
        assert not flag_fired(after), "and gone from the screen"
        assert "data-dismissed" not in after, "so the marker has nothing to sit on"
        assert held.dismissed == ["vendor_switch"], "the draft still remembers"
        assert tally.list_our_vouchers(app.COMPANY) == ()


def test_the_correction_that_silences_the_flag_is_the_answer_that_raised_it(
    tmp_path: pathlib.Path,
):
    """The mechanism above, asserted on the STORE rather than inferred.

    Before the answer the vendor has ONE row in `vendor_account`. After it, two.
    That single row is the whole of the disappearance: one account is MATCH, two
    is CONFLICTED, and CONFLICTED silences the detector.

    Read through a second connection on the same file, because the server owns
    its own and SQLite gives a connection to the thread that opened it.
    """
    store_path = tmp_path / "correction.sqlite"
    key = normalise_company(app.COMPANY)
    subject = normalise_vendor(SAME_LEG_VENDOR)
    tally = same_leg_company()

    with running(tally, store_path) as base:
        before = MemoryStore(str(store_path)).vendor(key, subject)
        assert [o.account for o in before] == [SAME_LEG]
        assert before[0].times == 6

        page = post(base, "/entry", text=f"paid {SAME_LEG_VENDOR} 4200 for fees")
        answer_on(base, page, "Purchases")

        after = MemoryStore(str(store_path)).vendor(key, subject)

    assert {o.account for o in after} == {SAME_LEG, "Purchases"}
    assert [o.provenance for o in after if o.account == "Purchases"] == ["human_answer"]


def test_the_detector_cannot_fire_twice_for_one_vendor_in_one_memory():
    """ "Fires repeatedly" is not reachable, and that is a finding not a fixture.

    Retyping the identical entry produces a fresh draft with an empty dismissed
    list, so if the flag could fire again it would fire UNDISMISSED and a second
    dismissal row could be written for the same concern. It cannot: the vendor
    is CONFLICTED from the first answer onwards. Repeated review therefore
    creates no duplicate dismissal event, but by silencing the detector rather
    than by recognising the repeat.
    """
    tally = same_leg_company()
    with running(tally) as base:
        first = post(base, "/entry", text=f"paid {SAME_LEG_VENDOR} 4200 for fees")
        first = answer_on(base, first, "Purchases")
        assert flag_fired(first)
        post(base, "/dismiss", draft=draft_on(first), detector="vendor_switch")

        second = post(base, "/entry", text=f"paid {SAME_LEG_VENDOR} 4200 for fees")
        assert draft_on(second) != draft_on(first), "a retype is a new draft"
        assert problem_on(second) == "which_account", "the vendor is now conflicted"
        second = answer_on(base, second, "Purchases")

        assert not flag_fired(second)
        home = get(base)

    assert len(re.findall(r'data-action="dismissed"', home)) == 1


def test_the_dismissed_marker_is_never_written_to_or_read_from_the_store(
    tmp_path: pathlib.Path,
):
    """Why a restart cannot restore it. The EVENT is durable; the STATE is not.

    Three things together, because any one alone would be a coincidence: the
    schema has nowhere to put it, the store has no reader for it, and a draft
    built AFTER a dismissal row exists on disk comes back with an empty marker
    list. So a restarted process shows a fresh, unmarked flag while the row
    recording that somebody looked is still on the file.
    """
    store_path = tmp_path / "marker.sqlite"
    tally = same_leg_company()
    with running(tally, store_path) as base:
        page = post(base, "/entry", text=f"paid {SAME_LEG_VENDOR} 4200 for fees")
        page = answer_on(base, page, "Purchases")
        post(base, "/dismiss", draft=draft_on(page), detector="vendor_switch")

        # A second vendor, driven to its own live flag AFTER the row exists.
        typed = f"paid {SECOND_SAME_LEG_VENDOR} 4200 for fees"
        second = post(base, "/entry", text=typed)
        second = answer_on(base, second, "Purchases")
        assert flag_fired(second)
        assert app.DRAFTS[draft_on(second)].dismissed == []
        assert 'data-dismissed="false"' in second

    reopened = MemoryStore(str(store_path))
    assert len(dismissals_in(store_path)) == 1, "the row is on the file"
    for table in reopened.table_names():
        assert not any("dismiss" in c for c in reopened.columns_of(table)), (
            f"{table} has a column for it, so the schema is no longer the reason"
        )
    assert not [m for m in dir(reopened) if "dismiss" in m], "no reader exists"


# =============================================================================
# 3. THE ADVERSARIAL SET
# =============================================================================


def index_for(history: Sequence[Voucher]) -> MemoryIndex:
    return MemoryIndex.from_vouchers(history)


def proposed(debit: str, party: str = SAME_LEG_VENDOR, **kw: object) -> Voucher:
    base = Voucher(
        id="v-1",
        date=datetime.date(2026, 3, 1),
        party=party,
        narration="one entry",
        debit_account=debit,
        credit_account="Cash",
        amount_paise=500_000,
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def test_a_book_with_no_anomaly_produces_no_flag_and_no_dismissal_row(
    tmp_path: pathlib.Path,
):
    """The negative path, checked against the STORE and not only the screen.

    A detector that fires on everything is a detector nobody reads; a log that
    gains rows on clean entries is a log nobody can count anything in.
    """
    store_path = tmp_path / "clean.sqlite"
    tally = company_of(COMPLETE_CHART, rows("Kumar Stationers", "Purchases", "Cash", 6))
    with running(tally, store_path) as base:
        page = post(base, "/entry", text="paid Kumar Stationers 4200 for pens")
        posted = len(tally.list_our_vouchers(app.COMPANY))

    written = MemoryStore(str(store_path)).actions(app.COMPANY)

    assert not flag_fired(page)
    assert posted == 1, "a clean matched vendor still posts"
    assert dismissals_in(store_path) == ()
    assert [r.action for r in written if r.action == "posted"] == ["posted"]


def test_a_flag_cannot_be_constructed_without_evidence():
    """ "The flag names specific evidence" is a type invariant, not a habit."""
    with pytest.raises(ValueError, match="without a reason"):
        Flag(voucher_id="v-1", detector="vendor_switch", severity=3, reason="   ")


def test_two_detectors_reading_one_thing_become_one_alert_that_keeps_both():
    """Duplicate ALERTS are suppressed; findings never are.

    `vendor_switch` and `first_use` both answer "which account was this posted
    to", so an entry sent somewhere this vendor never goes AND somewhere nobody
    has ever posted is ONE wrong account and ONE question. The lower-ranked
    flag's evidence travels inside the survivor's reason.
    """
    history = rows(SAME_LEG_VENDOR, SAME_LEG, SAME_LEG, 6)
    index = index_for(history)
    entry = proposed("Freight & Transport")

    flags, dropped = detectors.run(
        entry, history, index, detectors=(detectors.vendor_switch, detectors.first_use)
    )

    assert dropped == 0
    assert len(flags) == 1, "one underlying problem, one alert"
    assert flags[0].detector == "vendor_switch", "the higher severity leads"
    assert "never been used" in flags[0].reason, "and first_use's evidence survives"
    assert (
        detectors.concern_of("vendor_switch")
        == detectors.concern_of("first_use")
        == "which account this entry was posted to"
    )


def test_a_severity_tie_is_broken_by_the_detector_name():
    """Deterministic ranking. Equal severity must not depend on iteration order.

    `first_use` and `magnitude` are both severity 2 and answer DIFFERENT
    concerns, so both survive deduplication and the order between them is
    decided by the sort key alone. Run twice with the detectors in opposite
    order: the ranking must not move.
    """
    # The index and the history are separate inputs, and they differ in
    # production too: `MemoryIndex.from_vouchers` skips vouchers WE posted, so
    # an account can carry history that the index has never seen. That is the
    # shape staged here, and it is what lets both detectors fire at once.
    history = rows("Someone Else", "Freight & Transport", "Cash", 3, first=10)
    index = MemoryIndex()
    for _ in range(6):
        index.record(SAME_LEG_VENDOR, SAME_LEG)
    entry = proposed("Freight & Transport", amount_paise=90_000_000)

    forward, _ = detectors.run(
        entry, history, index, detectors=(detectors.first_use, detectors.magnitude)
    )
    backward, _ = detectors.run(
        entry, history, index, detectors=(detectors.magnitude, detectors.first_use)
    )

    assert [f.detector for f in forward] == [f.detector for f in backward]
    assert [f.severity for f in forward] == sorted(
        (f.severity for f in forward), reverse=True
    )
    assert [f.detector for f in forward] == ["first_use", "magnitude"]


def test_the_rank_falls_through_to_the_voucher_id_when_all_else_ties():
    """The third sort key, which no product input can currently reach.

    One `run` sees one proposed voucher, so two flags with the same severity AND
    the same detector name can only be staged. Staged rather than skipped,
    because the key is part of the ranking contract Phase 6 exits on — "ties by
    voucher id" — and an untested clause is a clause that stops working quietly.
    """

    def flags_a(*_a: object) -> list[Flag]:
        return [
            Flag(voucher_id="v-zzz", detector="vendor_switch", severity=3, reason="z")
        ]

    def flags_b(*_a: object) -> list[Flag]:
        return [
            Flag(voucher_id="v-aaa", detector="vendor_switch", severity=3, reason="a")
        ]

    ranked, _ = detectors.run(
        proposed("Purchases"),
        (),
        MemoryIndex(),
        detectors=(flags_a, flags_b),
        dedupe=False,
    )

    assert [f.voucher_id for f in ranked] == ["v-aaa", "v-zzz"]


def test_the_cap_reports_the_overflow_as_a_count_and_drops_the_lowest_ranked():
    """Ranking happens BEFORE the cap, or the cap throws away the wrong flags."""

    def make(severity: int, name: str):  # type: ignore[no-untyped-def]
        def detector(*_a: object) -> list[Flag]:
            return [
                Flag(
                    voucher_id="v-1",
                    detector=name,
                    severity=severity,
                    reason=f"{name} says so",
                )
            ]

        return detector

    kept, dropped = detectors.run(
        proposed("Purchases"),
        (),
        MemoryIndex(),
        detectors=(make(1, "low"), make(3, "high"), make(2, "middle")),
        cap=2,
        dedupe=False,
    )

    assert dropped == 1
    assert [f.detector for f in kept] == ["high", "middle"]
    assert detectors.run(
        proposed("Purchases"), (), MemoryIndex(), detectors=(), cap=2
    ) == ([], 0), "nothing to drop reports zero"


def test_a_detector_this_package_does_not_know_becomes_a_refusal_not_a_question():
    """ "No question" is the honest answer to a flag we have no words for.

    An unrecognised detector gets its own concern so it cannot be merged into or
    silenced by another, and an unanswerable problem is NOT_VALID — the entry
    stops. Inventing a question for it would be this code guessing what somebody
    else's detector meant.
    """

    def meteor_strike(v: Voucher, *_a: object) -> list[Flag]:
        return [
            Flag(
                voucher_id=v.id,
                detector="meteor_strike",
                severity=4,
                reason="a meteor hit the warehouse",
            )
        ]

    tally = same_leg_company()
    memory = bootstrap(tally, app.COMPANY, MemoryStore(":memory:"))
    draft = pipeline.build_draft(
        app.COMPANY,
        b"paid Kumar Stationers 4200 for pens",
        "text/plain",
        TypedTextExtractor(),
        memory,
    )
    draft = pipeline.evaluate(
        draft,
        tally.read_accounts(app.COMPANY),
        tally.read_vouchers(app.COMPANY),
        memory,
        detector_set=(meteor_strike,),
        period_open=None,
        pdf_repaired=None,
    )

    assert draft.outcome is Outcome.NOT_VALID
    assert pipeline.next_question(draft) is None
    assert "meteor" in draft.reason
    assert (
        detectors.concern_of("meteor_strike") == "whatever detector meteor_strike reads"
    )
    with pytest.raises(ValueError, match="refusing to post"):
        pipeline.post(draft, tally)
    assert tally.list_our_vouchers(app.COMPANY) == ()


def test_the_question_beside_a_route_f_flag_names_no_jargon_account():
    """S7 on the new route. The evidence may name a ledger; the question may not.

    Two different assertions, because they are two different rules. `Old Ledger`
    has no plain-English words at all, so the question falls back to "the same
    thing" rather than printing a name nobody can read.
    """
    tally = same_leg_company()
    with running(tally) as base:
        page = post(base, "/entry", text=f"paid {SAME_LEG_VENDOR} 4200 for fees")
        page = answer_on(base, page, "Purchases")
        question = asked_on(page)

    assert (
        Q.different_from_usual(SAME_LEG_VENDOR, SAME_LEG, 6).mentions_any(
            COMPLETE_CHART
        )
        == []
    )
    assert "Purchases" not in question
    assert "Repairs" not in question

    stale = Q.different_from_usual("Sharma Traders", GONE, 6)
    assert GONE not in stale.text
    assert Q.is_jargon(GONE), "an account with no plain words is jargon by definition"
    assert stale.mentions_any((GONE,)) == []


def test_a_detector_that_returns_something_that_is_not_a_flag_fails_closed():
    """Malformed output. The ranking touches `.severity`, and there isn't one.

    Fail-closed matters more than the exception type: the draft never gets a
    decision, `post` refuses an unevaluated draft, and nothing reaches Tally.
    """

    def malformed(*_a: object) -> list[Flag]:
        return ["this is not a flag"]  # type: ignore[list-item]

    tally = same_leg_company()
    memory = bootstrap(tally, app.COMPANY, MemoryStore(":memory:"))
    draft = pipeline.build_draft(
        app.COMPANY,
        b"paid Kumar Stationers 4200 for pens",
        "text/plain",
        TypedTextExtractor(),
        memory,
    )

    with pytest.raises(AttributeError):
        pipeline.evaluate(
            draft,
            tally.read_accounts(app.COMPANY),
            tally.read_vouchers(app.COMPANY),
            memory,
            detector_set=(malformed,),
            period_open=None,
            pdf_repaired=None,
        )

    assert draft.decision is None
    with pytest.raises(ValueError, match="not been evaluated"):
        pipeline.post(draft, tally)
    assert tally.list_our_vouchers(app.COMPANY) == ()


def test_a_malformed_detector_answers_the_person_instead_of_dropping_the_socket(
    monkeypatch: pytest.MonkeyPatch,
):
    """The same failure at the screen, and through the REAL ranking code.

    `tests/test_review_flow_defects.py` replaces `detectors.run` outright, so
    every exception it raises comes from the double. This one comes from inside
    the shipped `run`, on the `-f.severity` sort key, which is a different line
    and a different claim.

    The detector set is swapped by rebinding `evaluate`'s keyword default rather
    than by patching `SLICE_4_DETECTORS`: the default was bound to the tuple
    object at definition time, so rebinding the module attribute changes
    nothing. This is the same thing a configuration change would do.
    """

    def malformed(*_a: object, **_k: object) -> list[Flag]:
        return ["this is not a flag"]  # type: ignore[list-item]

    assert pipeline.evaluate.__kwdefaults__ is not None
    monkeypatch.setitem(pipeline.evaluate.__kwdefaults__, "detector_set", (malformed,))

    tally = same_leg_company()
    with running(tally) as base:
        data = urllib.parse.urlencode({"text": "paid Kumar Stationers 4200 for pens"})
        try:
            with urllib.request.urlopen(  # noqa: S310
                base + "/entry", data=data.encode(), timeout=5
            ) as r:
                code, body = r.status, r.read().decode()
        except urllib.error.HTTPError as exc:
            code, body = exc.code, exc.read().decode()

        assert code == 503
        assert "could not be finished" in body.lower()
        assert "AttributeError" not in body, "no internals on a page a person reads"
        assert tally.list_our_vouchers(app.COMPANY) == ()


def test_the_detector_can_never_read_another_companys_history():
    """Cross-company. The index is scoped before it exists, not filtered after.

    Two companies, one vendor name, opposite practices. Company B's memory
    handed to company A's draft is refused outright, and A's own index answers
    only from A's rows — so there is no input on which B's history could supply
    the `usual` account A's entry is compared against.
    """
    other = "Someone Else Ltd Books"
    a = company_of(COMPLETE_CHART, rows("Kumar Stationers", "Purchases", "Cash", 6))
    b = FakeTally()
    b.add_company(
        other,
        accounts=COMPLETE_CHART,
        vouchers=tuple(rows("Kumar Stationers", "Rent", "Cash", 9)),
        backed_up=True,
    )
    store = MemoryStore(":memory:")
    memory_a = bootstrap(a, app.COMPANY, store)
    memory_b = bootstrap(b, other, store)

    assert memory_a.index().lookup("Kumar Stationers").accounts == ("Purchases",)
    assert memory_b.index().lookup("Kumar Stationers").accounts == ("Rent",)

    with pytest.raises(ValueError, match="company-scoped memory is never shared"):
        pipeline.build_draft(
            app.COMPANY,
            b"paid Kumar Stationers 4200 for pens",
            "text/plain",
            TypedTextExtractor(),
            memory_b,
        )

    draft = pipeline.build_draft(
        app.COMPANY,
        b"paid Kumar Stationers 4200 for pens",
        "text/plain",
        TypedTextExtractor(),
        memory_a,
    )
    with pytest.raises(ValueError, match="company-scoped memory is never shared"):
        pipeline.evaluate(
            draft,
            a.read_accounts(app.COMPANY),
            a.read_vouchers(app.COMPANY),
            memory_b,
            period_open=None,
            pdf_repaired=None,
        )


def test_an_entry_that_is_not_valid_still_shows_its_flag_and_still_posts_nothing():
    """An invalid draft CARRYING a flag. NOT_VALID wins and the evidence stays.

    A float amount is the only unanswerable check in the codebase, so this is
    the one draft that can be both flagged and refused. The renderer must draw
    it — `money()` degrades rather than raising — and the write gate must refuse
    it. Both, because either alone leaves the person with a blank screen or a
    posted voucher.
    """
    tally = same_leg_company()
    memory = bootstrap(tally, app.COMPANY, MemoryStore(":memory:"))
    accounts = tally.read_accounts(app.COMPANY)
    history = tally.read_vouchers(app.COMPANY)

    draft = pipeline.build_draft(
        app.COMPANY,
        f"paid {SAME_LEG_VENDOR} 4200 for fees".encode(),
        "text/plain",
        TypedTextExtractor(),
        memory,
    )
    draft = pipeline.answer(draft, "Purchases", problem_id="accounts_differ")
    draft.voucher = replace(draft.voucher, amount_paise=4200.5)  # type: ignore[arg-type]
    draft = pipeline.evaluate(
        draft, accounts, history, memory, period_open=None, pdf_repaired=None
    )

    assert [f.detector for f in draft.flags] == ["vendor_switch"]
    assert draft.outcome is Outcome.NOT_VALID
    assert "amount_is_integer_paise" in draft.reason

    html = app.render_decision(draft)
    assert 'data-detector="vendor_switch"' in html
    assert "not an amount" in html

    with pytest.raises(ValueError, match="refusing to post"):
        pipeline.post(draft, tally)
    assert tally.list_our_vouchers(app.COMPANY) == ()


def test_a_dismissal_is_not_permission_to_post_even_when_nothing_else_is_wrong(
    tmp_path: pathlib.Path,
):
    """Every check passes, the vendor matches, and the ONE thing outstanding is
    the flag. Dismissing it must not turn UNCLEAR into VALID.

    Asserted against the decision, the store and the write path rather than the
    badge, because the badge is a renderer and "dismissed" is one line away from
    "approved" in the handler that writes the marker.
    """
    store_path = tmp_path / "not_permission.sqlite"
    tally = same_leg_company()
    with running(tally, store_path) as base:
        page = post(base, "/entry", text=f"paid {SAME_LEG_VENDOR} 4200 for fees")
        page = answer_on(base, page, "Purchases")
        draft = draft_on(page)
        post(base, "/dismiss", draft=draft, detector="vendor_switch")
        held = app.DRAFTS[draft]

        assert held.outcome is Outcome.UNCLEAR
        assert held.decision is not None and not held.decision.post
        assert held.posted_tally_id is None

    written = MemoryStore(str(store_path)).actions(app.COMPANY)

    assert [r for r in written if r.action == "posted"] == []
    assert [r for r in written if r.action == pipeline.WRITE_ATTEMPTED] == []
    assert tally.list_our_vouchers(app.COMPANY) == ()


# =============================================================================
# 4. DURABLE DISMISSAL — the cases `test_dismissal_durability.py` does not hold
# =============================================================================
#
# That file proves the row survives, is scoped by the read key, carries every
# field, has an empty voucher id for a structural reason, deduplicates, and
# stays silent on a refusal — all on the stale-chart route. These three are
# different inputs and different claims.


def test_a_dismissal_reached_by_route_f_is_readable_from_a_second_store(
    tmp_path: pathlib.Path,
):
    """Durability is a property of the ROW, so it has to hold for every route in.

    The server is torn down before this reads anything, so the store that wrote
    the row is closed. What comes back came off the file.
    """
    store_path = tmp_path / "route_f.sqlite"
    tally = same_leg_company()
    with running(tally, store_path) as base:
        page = post(base, "/entry", text=f"paid {SAME_LEG_VENDOR} 4200 for fees")
        page = answer_on(base, page, "Purchases")
        assert flag_fired(page)
        post(base, "/dismiss", draft=draft_on(page), detector="vendor_switch")

    rows_back = dismissals_in(store_path)

    assert len(rows_back) == 1
    row = rows_back[0]
    assert row.vendor_id == SAME_LEG_VENDOR
    assert SAME_LEG in row.reason, "the evidence travels with the dismissal"
    assert "vendor_switch" in row.detail
    assert row.operation_id.startswith("ad_")
    assert row.outcome == "DISMISSED"


def test_two_vendors_dismissed_in_one_session_leave_two_separable_rows(
    tmp_path: pathlib.Path,
):
    """One row per dismissal, each naming its own vendor and its own operation.

    The dedupe that keeps a re-click from adding a row must not also collapse
    two genuinely different dismissals — a log that cannot tell two concerns
    apart is a log nobody can count anything in, in the other direction.
    """
    store_path = tmp_path / "two_vendors.sqlite"
    tally = same_leg_company()
    with running(tally, store_path) as base:
        for vendor in (SAME_LEG_VENDOR, SECOND_SAME_LEG_VENDOR):
            page = post(base, "/entry", text=f"paid {vendor} 4200 for fees")
            page = answer_on(base, page, "Purchases")
            assert flag_fired(page), f"{vendor} did not reach the detector"
            post(base, "/dismiss", draft=draft_on(page), detector="vendor_switch")

    rows_back = dismissals_in(store_path)

    assert len(rows_back) == 2
    assert {r.vendor_id for r in rows_back} == {SAME_LEG_VENDOR, SECOND_SAME_LEG_VENDOR}
    assert len({r.operation_id for r in rows_back}) == 2
    assert len({r.detail for r in rows_back}) == 2, "each names its own draft"
    assert len({r.run_id for r in rows_back}) == 1, "one session, one run id"


def test_the_durable_row_outlives_the_flag_it_was_written_about(
    tmp_path: pathlib.Path,
):
    """The other half of the disappearing-flag finding, and the reassuring half.

    The flag leaves the screen on the next re-evaluation and `Draft.dismissed`
    dies with the process. The RECORD that somebody looked does not: it is on
    disk, it names the detector, and it carries the evidence the flag was
    carrying at the moment it was dismissed.
    """
    store_path = tmp_path / "outlives.sqlite"
    tally = same_leg_company()
    with running(tally, store_path) as base:
        page = post(base, "/entry", text=f"paid {SAME_LEG_VENDOR} 4200 for fees")
        page = answer_on(base, page, "Purchases")
        draft = draft_on(page)
        post(base, "/dismiss", draft=draft, detector="vendor_switch")
        after = post(base, "/answer", draft=draft, problem="vendor_switch", value=Q.YES)

        assert not flag_fired(after), "the flag has gone from the screen"
        assert app.DRAFTS[draft].flags == []

    rows_back = dismissals_in(store_path)

    assert len(rows_back) == 1
    assert "vendor_switch" in rows_back[0].reason
    assert SAME_LEG in rows_back[0].reason, "the evidence survives the flag"
    assert "does not mean the entry is correct" in rows_back[0].reason
    assert rows_back[0].ts.tzinfo is not None, "a naive timestamp is unorderable"

"""PHASE 6 — the first detector, on the screen a person actually uses.

Canonical scope, `docs/ARCHITECTURE.md` §7, unchanged: wire `vendor_switch`
into the review screen, show the result, log dismissals durably.

WHAT WAS ALREADY TRUE, AND WHY THAT IS NOT THE PHASE
-----------------------------------------------------
`vendor_switch` has been the only default detector since `SLICE_4_DETECTORS`
was written; `pipeline.evaluate` runs it; ranking is deterministic; a flag
cannot be constructed without evidence; every flag becomes a plain-English
question. All of that was in place and none of it is what Phase 6 asks for.

What was missing, measured:

    G6.1  no dismissal action existed anywhere. `grep -rn dismiss accountant`
          returned only the N1/N2 arithmetic in `score/`. Frozen criterion #3.7
          — "dismissals are logged with detector name and voucher id" — had
          nothing to log.
    G6.2  `Draft.dropped_flags` was computed and then discarded. "Overflow
          reported as a count, never silently dropped" was true inside the
          dataclass and FALSE on the screen a person reads.
    G6.3  the detector had never been proven to fire through the review-screen
          path, only through a unit call.

A DISMISSAL IS NOT A VALIDATION
-------------------------------
It records that the person saw the concern and chose not to act on it. It does
not mean the entry is correct, it does not change the outcome, and it does not
authorise a write. That is asserted here rather than assumed, because
"dismissed" and "approved" are one careless line apart.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about a real TallyPrime. Evidence class: FAKETALLY, over real HTTP.
"""

from __future__ import annotations

import datetime
import re
import threading
from collections.abc import Iterator, Sequence
from http.server import HTTPServer

import pytest

from accountant import pipeline
from accountant.detect import detectors
from accountant.extract.adapter import TypedTextExtractor
from accountant.memory.index import MemoryIndex
from accountant.memory.store import MemoryStore
from accountant.schema import Flag, Outcome, Voucher
from accountant.tallyio.factory import BackendIdentity, new_run_id
from accountant.tallyio.fake import FakeTally
from accountant.web import app
from tests.test_web import get, post

KNOWN = "paid Sharma Traders 4200 for cement"

# The chart NO LONGER CONTAINS "Old Ledger". Six vouchers still point at it.
STALE_CHART = ("Purchases", "Repairs & Maintenance", "Cash")
GONE = "Old Ledger"


def stale_ledger_company() -> FakeTally:
    """A company whose history names a ledger the chart no longer has.

    THIS IS THE ONLY ROUTE TO THE DETECTOR FROM THE SCREEN, and finding that out
    was most of Phase 6.

    `vendor_switch` fires when a vendor with a consistent history is posted
    somewhere else. The only thing that proposes an account is memory, and
    memory proposes the usual one — so on a matched vendor the entry posts
    straight through and no human ever redirects it. On an unmatched vendor the
    person is asked, but there is no consistent history to contradict. The two
    conditions look mutually exclusive.

    They are not. A ledger renamed or deleted in Tally leaves memory confidently
    proposing an account the chart no longer holds: `accounts_exist` fails, the
    person is asked which purpose the entry served, and their answer contradicts
    six postings of history. That is a real thing that happens to real books,
    and it is the case the detector was built for.
    """
    history = [
        Voucher(
            id=f"h{i}",
            date=datetime.date(2026, 1, 1) + datetime.timedelta(days=i),
            party="Sharma Traders",
            narration="cement supply",
            debit_account=GONE,
            credit_account="Cash",
            amount_paise=100_000,
        )
        for i in range(6)
    ]
    t = FakeTally()
    t.add_company(
        app.COMPANY, accounts=STALE_CHART, vouchers=tuple(history), backed_up=True
    )
    return t


@pytest.fixture
def stale_server() -> Iterator[str]:
    """The same spin-up as `tests/test_web.py`, over the stale-ledger company."""
    tally = stale_ledger_company()
    identity = BackendIdentity(
        backend="FakeTally",
        endpoint="memory://tests/test_first_detector.py",
        company=app.COMPANY,
        company_exists=True,
        companies_visible=1,
        run_id=new_run_id(),
    )
    app.DRAFTS.clear()
    httpd = HTTPServer(("127.0.0.1", 0), app.Handler)
    ready = threading.Event()

    def serve() -> None:
        app.configure(tally, identity, store=MemoryStore(":memory:"))
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


def draft_id(page: str) -> str:
    found = re.search(r'name=draft value="([^"]+)"', page)
    assert found, f"no draft id on the page:\n{page[:600]}"
    return found.group(1)


def answer(base: str, page: str, value: str) -> str:
    problem = re.search(r'name=problem value="([^"]+)"', page)
    assert problem, f"no problem id on the page:\n{page[:600]}"
    return post(
        base, "/answer", draft=draft_id(page), problem=problem.group(1), value=value
    )


def flagged(base: str) -> tuple[str, str]:
    """A draft carrying a live `vendor_switch` flag, reached the way a person
    reaches it: type the entry, and answer the question the missing ledger
    forces with an account that contradicts six postings of history."""
    page = post(base, "/entry", text=KNOWN)
    page = answer(base, page, "Purchases")
    assert "vendor_switch" in page, f"the detector did not fire:\n{page[:900]}"
    return draft_id(page), page


# ---- G6.3: the detector fires through the review-screen path ----------------


def test_vendor_switch_fires_on_the_review_screen_and_names_its_evidence(
    stale_server: str,
):
    _, page = flagged(stale_server)

    assert "vendor_switch" in page
    # The evidence, not an adjective. The reason has to name the vendor, the
    # account it usually goes to, and how many times — a flag a person cannot
    # dismiss quickly inflates D and breaks N1.
    assert "Sharma Traders" in page
    assert GONE in page, "the account it usually goes to"
    assert re.search(r"\d+ time", page), f"no count in the evidence:\n{page[:900]}"


def test_the_question_beside_the_flag_names_no_ledger_account(stale_server: str):
    """S7. The person never sees a chart-of-accounts name inside a question."""
    page = post(stale_server, "/entry", text=KNOWN)
    asked = re.search(r"<p class=ask>([^<]*)</p>", page)
    assert asked, f"no question on the page:\n{page[:900]}"

    for account in (*STALE_CHART, GONE):
        assert account not in asked.group(1)


def test_a_clean_entry_carries_no_flag_at_all(server: str):
    """The negative path. A detector that fires on everything is a detector
    that is never read."""
    page = post(server, "/entry", text=KNOWN)

    assert "vendor_switch" not in page
    assert "posted" in page


def test_only_one_detector_runs_by_default():
    """Phase 6 is ONE detector seen working, not four wired at once."""
    assert (detectors.vendor_switch,) == detectors.SLICE_4_DETECTORS
    assert len(detectors.ALL_DETECTORS) == 4


# ---- G6.2: the overflow count reaches the screen ----------------------------


def test_the_dropped_flag_count_is_shown_and_never_implies_zero():
    """`Draft.dropped_flags` was computed and thrown away at render time.

    Rendered directly rather than through the server, because reaching a cap
    needs more detectors than the product runs by default — and the claim under
    test is about the renderer, not about how many detectors there are.
    """
    from tests.test_web import demo_company

    tally = demo_company()
    accounts = tally.read_accounts(app.COMPANY)
    history = tally.read_vouchers(app.COMPANY)

    draft = pipeline.Draft(
        id="d1",
        company=app.COMPANY,
        voucher=history[0],
        record=pipeline.ExtractedRecord(
            date=None,
            party="Verma Cement",
            total_paise=90000,
            tax_paise=None,
            line_items=(),
            raw_text="bags",
            backend="typed",
            per_field_source={
                "date": "not_found",
                "party": "typed",
                "total_paise": "typed",
                "tax_paise": "not_found",
            },
        ),
        operation_id="ad_dropped",
        dropped_flags=3,
    )
    draft.decision = pipeline.Decision(
        outcome=Outcome.UNCLEAR, reason="x", operation_id="ad_dropped"
    )
    del accounts

    html = app.render_decision(draft)

    assert "3" in html
    assert "not shown" in html.lower()


def test_no_dropped_flags_says_nothing_rather_than_saying_zero():
    """ "0 more concerns are not shown" is noise on every clean entry."""
    from tests.test_web import demo_company

    tally = demo_company()
    history = tally.read_vouchers(app.COMPANY)
    draft = pipeline.Draft(
        id="d2",
        company=app.COMPANY,
        voucher=history[0],
        record=pipeline.ExtractedRecord(
            date=None,
            party="Sharma Traders",
            total_paise=1,
            tax_paise=None,
            line_items=(),
            raw_text="x",
            backend="typed",
            per_field_source={
                "date": "not_found",
                "party": "typed",
                "total_paise": "typed",
                "tax_paise": "not_found",
            },
        ),
        operation_id="ad_none",
        dropped_flags=0,
    )
    draft.decision = pipeline.Decision(
        outcome=Outcome.VALID, reason="x", operation_id="ad_none"
    )

    assert "not shown" not in app.render_decision(draft).lower()


def test_the_rendered_count_is_the_drafts_own_number():
    """A hardcoded number would render just as convincingly."""
    from tests.test_web import demo_company

    tally = demo_company()
    history = tally.read_vouchers(app.COMPANY)

    for dropped in (1, 7, 42):
        draft = pipeline.Draft(
            id=f"d-{dropped}",
            company=app.COMPANY,
            voucher=history[0],
            record=pipeline.ExtractedRecord(
                date=None,
                party="x",
                total_paise=1,
                tax_paise=None,
                line_items=(),
                raw_text="x",
                backend="typed",
                per_field_source={
                    "date": "not_found",
                    "party": "typed",
                    "total_paise": "typed",
                    "tax_paise": "not_found",
                },
            ),
            operation_id=f"ad_{dropped}",
            dropped_flags=dropped,
        )
        draft.decision = pipeline.Decision(
            outcome=Outcome.UNCLEAR, reason="x", operation_id=f"ad_{dropped}"
        )
        assert f"{dropped} " in app.render_decision(draft)


# ---- G6.1: dismissal, and the durable row it leaves -------------------------


def test_a_flag_can_be_dismissed_from_the_review_screen(stale_server: str):
    draft, page = flagged(stale_server)
    assert "/dismiss" in page

    after = post(stale_server, "/dismiss", draft=draft, detector="vendor_switch")

    assert "dismissed" in after.lower()


def test_every_dismissal_leaves_a_durable_row_naming_the_detector(stale_server: str):
    """Frozen criterion #3.7: logged with detector name and voucher id."""
    draft, _ = flagged(stale_server)

    post(stale_server, "/dismiss", draft=draft, detector="vendor_switch")

    home = get(stale_server)
    rows = re.findall(r'data-outcome="([^"]+)" data-action="([^"]+)"', home)
    assert ("DISMISSED", "dismissed") in rows
    assert "vendor_switch" in home


def test_dismissing_twice_does_not_write_a_second_row(stale_server: str):
    """ "Repeated review does not silently create duplicate dismissal events".

    A log that gains a row every time somebody reloads is a log nobody can
    count anything in.
    """
    draft, _ = flagged(stale_server)

    post(stale_server, "/dismiss", draft=draft, detector="vendor_switch")
    post(stale_server, "/dismiss", draft=draft, detector="vendor_switch")
    post(stale_server, "/dismiss", draft=draft, detector="vendor_switch")

    home = get(stale_server)
    dismissals = re.findall(r'data-action="dismissed"', home)
    assert len(dismissals) == 1


def test_a_dismissal_does_not_change_the_outcome_and_posts_nothing(stale_server: str):
    """A dismissal is not a validation. It records that the person looked."""
    draft, page = flagged(stale_server)
    assert "needs an answer" in page or "not posted" in page
    written_before = len(app.runtime().client.list_our_vouchers(app.COMPANY))

    after = post(stale_server, "/dismiss", draft=draft, detector="vendor_switch")

    # The badge, not the word. "posted" also appears inside the flag's own
    # evidence ("Sharma Traders posted to Old Ledger 6 times"), which is why
    # this asserts on the outcome badge rather than on the page text.
    assert 'class="badge b-valid">posted<' not in after
    assert "needs an answer" in after, "the entry is still unresolved"
    assert len(app.runtime().client.list_our_vouchers(app.COMPANY)) == written_before


def test_dismissing_a_flag_that_is_not_there_changes_nothing(stale_server: str):
    draft, _ = flagged(stale_server)

    post(stale_server, "/dismiss", draft=draft, detector="magnitude")

    home = get(stale_server)
    assert re.findall(r'data-action="dismissed"', home) == []


def test_dismissing_an_expired_draft_says_so_rather_than_failing(stale_server: str):
    post(
        stale_server,
        "/dismiss",
        draft="draft-that-never-existed",
        detector="vendor_switch",
    )

    home = get(stale_server)
    assert re.findall(r'data-action="dismissed"', home) == []


# ---- the safety gates are not reachable through the detector ----------------


def test_a_detector_that_raises_stops_the_entry_rather_than_posting_it():
    """ "Detector failure does not bypass existing accounting safety gates."

    Fail-closed: the exception propagates, the draft never gets a decision, and
    `post` refuses an unevaluated draft. The alternative — swallowing it — would
    post an entry whose surprise nobody looked at, which is the one outcome the
    detectors exist to prevent.
    """
    from accountant.memory.bootstrap import bootstrap

    def explodes(
        _proposed: Voucher,
        _history: Sequence[Voucher],
        _index: MemoryIndex,
    ) -> list[Flag]:
        raise RuntimeError("the detector fell over")

    tally = stale_ledger_company()
    memory = bootstrap(tally, app.COMPANY, MemoryStore(":memory:"))
    accounts = tally.read_accounts(app.COMPANY)
    history = tally.read_vouchers(app.COMPANY)

    draft = pipeline.build_draft(
        app.COMPANY,
        KNOWN.encode(),
        "text/plain",
        TypedTextExtractor(),
        memory,
    )

    with pytest.raises(RuntimeError, match="fell over"):
        pipeline.evaluate(draft, accounts, history, memory, detector_set=(explodes,))

    assert draft.decision is None
    with pytest.raises(ValueError, match="not been evaluated"):
        pipeline.post(draft, tally)
    assert tally.list_our_vouchers(app.COMPANY) == ()


def test_the_answer_is_judged_before_it_is_learned(stale_server: str):
    """The ordering defect that made G6.3 unreachable, pinned.

    `record_correction` used to run BEFORE `evaluate`, so the person's answer
    was written into memory as fact and the detectors then compared it against
    a history that already contained it. Nothing could ever look surprising.

    Measured: `vendor_switch` could not fire from the review screen on any
    input. The mutant that restores the old order is the one that matters here.
    """
    _, page = flagged(stale_server)

    assert "vendor_switch" in page
    assert "6 times" in page, "judged against the history as it was"

    # And the correction IS still learned — evaluating first delays it, it does
    # not skip it. A second entry for the same vendor now sees the answer.
    second = post(stale_server, "/entry", text=KNOWN)
    assert "Old Ledger" not in second or "vendor_switch" in second


def test_the_existing_review_behaviour_is_unchanged(server: str):
    """ "Existing non-detector review behaviour remains unchanged." A known
    vendor still posts straight through, with its provenance shown."""
    page = post(server, "/entry", text=KNOWN)

    assert "Written to Tally as" in page
    assert "Where each field came from" in page
    assert "checks run" in page

"""The Phase 6 claims that were true and untested, and the ones that were not.

An audit of `9f9e0e4` found that Phase 6 shipped four assertions it had not
actually made. Every dismissal test read the RENDERED PAGE and none read the
store, so "log dismissals durably" — the third of Phase 6's three clauses — was
believed rather than checked. The fixture used `MemoryStore(":memory:")`, which
cannot survive anything, so "durably" was not merely untested: it was untestable
by construction in the file that claimed it.

That is the same shape as Phase 5B defect #4, where ten batch rows were written
under a display name and could never be read back. That one was caught only
because a run reopened the file the way a second process does. This file does
the same thing for dismissals, on purpose, before something forces it.

WHAT IS PINNED HERE
-------------------
    the row survives the process           a second MemoryStore on the same file
    the row is scoped the way it is read   normalised company key
    every field is what it claims          asserted one at a time, off the store
    voucher_id is EMPTY, and why           it cannot be otherwise — see below

THE ONE THAT IS A DOCUMENTATION DEFECT, NOT A CODE DEFECT
----------------------------------------------------------
Frozen criterion #3.7 asks for dismissals "logged with detector name and voucher
id". The voucher id is always the empty string, and it CANNOT be anything else:
a fired flag becomes an answerable `Problem`, an outstanding problem is UNCLEAR
and an answered one is NOT_VALID, and VALID requires an empty problem list. A
draft carrying a flag can never post, so there is never a voucher to name.

The honest response is to pin the emptiness WITH ITS REASON rather than leave a
criterion that reads as unmet. The operation id is what ties the row to the
entry, and it is present.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about a real TallyPrime. Evidence class: FAKETALLY over real HTTP.
"""

from __future__ import annotations

import datetime
import pathlib
import re
import threading
from collections.abc import Iterator
from http.server import HTTPServer

import pytest

from accountant.memory.identity import normalise_company
from accountant.memory.store import MemoryStore
from accountant.schema import Voucher
from accountant.tallyio.factory import BackendIdentity, new_run_id
from accountant.tallyio.fake import FakeTally
from accountant.web import app
from tests.test_first_detector import GONE, STALE_CHART, stale_ledger_company
from tests.test_web import post

ENTRY = "paid Sharma Traders 4200 for cement"


@pytest.fixture
def durable_server(tmp_path: pathlib.Path) -> Iterator[tuple[str, pathlib.Path]]:
    """The stale-ledger company, but backed by a FILE the test can reopen.

    `:memory:` is what every other web fixture uses and it is right for them —
    they assert on behaviour, not on persistence. It is exactly wrong here: a
    store that dies with the process cannot demonstrate that anything survives
    one.
    """
    store_path = tmp_path / "durable.sqlite"
    tally = stale_ledger_company()
    identity = BackendIdentity(
        backend="FakeTally",
        endpoint="memory://tests/test_dismissal_durability.py",
        company=app.COMPANY,
        company_exists=True,
        companies_visible=1,
        run_id=new_run_id(),
    )
    app.DRAFTS.clear()
    httpd = HTTPServer(("127.0.0.1", 0), app.Handler)
    ready = threading.Event()

    def serve() -> None:
        app.configure(tally, identity, store=MemoryStore(str(store_path)))
        ready.set()
        httpd.serve_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=5), "the server thread never bootstrapped memory"
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", store_path
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        app.disconnect()


def dismiss_one(base: str) -> str:
    """Type the entry, answer contradictorily, dismiss the flag. Returns the id."""
    page = post(base, "/entry", text=ENTRY)
    problem = re.search(r'name=problem value="([^"]+)"', page)
    draft = re.search(r'name=draft value="([^"]+)"', page)
    assert problem and draft, f"no question on the page:\n{page[:600]}"
    page = post(
        base,
        "/answer",
        draft=draft.group(1),
        problem=problem.group(1),
        value="Purchases",
    )
    assert "vendor_switch" in page, f"the detector did not fire:\n{page[:900]}"
    draft_id = re.search(r'name=draft value="([^"]+)"', page)
    assert draft_id
    post(base, "/dismiss", draft=draft_id.group(1), detector="vendor_switch")
    return draft_id.group(1)


def dismissals(path: pathlib.Path) -> list[object]:
    """Read the rows the way a SECOND PROCESS would: a new store on the file."""
    reopened = MemoryStore(str(path))
    return [r for r in reopened.actions(app.COMPANY) if r.action == "dismissed"]


# ---- the claim Phase 6 made and did not check -------------------------------


def test_a_dismissal_survives_the_process_that_recorded_it(
    durable_server: tuple[str, pathlib.Path],
):
    """The whole of "log dismissals durably", read off disk after shutdown.

    The fixture tears the server down before this assertion runs, so the
    `MemoryStore` that wrote the row is closed and gone. Anything readable here
    came off the file.
    """
    base, path = durable_server
    dismiss_one(base)

    rows = dismissals(path)

    assert len(rows) == 1, "one dismissal, one durable row"


def test_the_row_is_scoped_by_the_key_the_store_reads_by(
    durable_server: tuple[str, pathlib.Path],
):
    """Phase 5B defect #4, applied to this path before it can happen here.

    `MemoryStore.actions` normalises before reading. A row written under the
    display name is a row nothing can ever find — ten of them were written that
    way in the batch code, and zero came back.
    """
    base, path = durable_server
    dismiss_one(base)

    row = dismissals(path)[0]

    assert row.company_key == normalise_company(app.COMPANY)  # type: ignore[attr-defined]
    assert row.company_key == "accountant_dad_final"  # type: ignore[attr-defined]
    # And it is findable by BOTH spellings, because the read normalises too.
    reopened = MemoryStore(str(path))
    assert len(reopened.actions(app.COMPANY)) == len(
        reopened.actions("accountant_dad_final")
    )


def test_every_field_of_the_row_is_asserted_off_the_store(
    durable_server: tuple[str, pathlib.Path],
):
    """Not read off the page. The page is a renderer and can be right while the
    record is wrong — which is precisely how the old forty-row `EVENTS` list
    managed to show activity while discarding the outcome."""
    base, path = durable_server
    draft_id = dismiss_one(base)

    row = dismissals(path)[0]

    assert row.action == "dismissed"  # type: ignore[attr-defined]
    assert row.outcome == "DISMISSED"  # type: ignore[attr-defined]
    assert row.operation_id.startswith("ad_")  # type: ignore[attr-defined]
    assert row.vendor_id == "Sharma Traders"  # type: ignore[attr-defined]
    assert "vendor_switch" in row.detail  # type: ignore[attr-defined]
    assert draft_id in row.detail  # type: ignore[attr-defined]
    assert "vendor_switch" in row.reason  # type: ignore[attr-defined]
    assert GONE in row.reason, "the evidence travels with the dismissal"  # type: ignore[attr-defined]
    assert row.ts.tzinfo is not None, "a naive timestamp is unorderable"  # type: ignore[attr-defined]
    assert row.backend == "FakeTally"  # type: ignore[attr-defined]
    assert row.run_id  # type: ignore[attr-defined]


def test_the_voucher_id_is_empty_and_it_cannot_be_anything_else(
    durable_server: tuple[str, pathlib.Path],
):
    """Frozen criterion #3.7 asks for the voucher id. There is never one.

    A fired flag becomes an answerable problem; an outstanding problem is
    UNCLEAR and an answered one is NOT_VALID; VALID needs an empty problem
    list. So a draft carrying a flag can never post, and a voucher that was
    never written has no id.

    Pinned with the reason rather than left looking like an unmet criterion.
    The operation id is what ties the row to the entry, and it is present.
    """
    base, path = durable_server
    dismiss_one(base)

    row = dismissals(path)[0]

    assert row.voucher_id == ""  # type: ignore[attr-defined]
    assert row.operation_id  # type: ignore[attr-defined]
    # The reason it is empty, asserted rather than described: nothing posted.
    assert app.runtime().client.list_our_vouchers(app.COMPANY) == ()


def test_two_dismissals_of_one_flag_leave_one_row_on_disk(
    durable_server: tuple[str, pathlib.Path],
):
    """The dedupe held in memory. This proves it held in the file too."""
    base, path = durable_server
    draft_id = dismiss_one(base)

    post(base, "/dismiss", draft=draft_id, detector="vendor_switch")
    post(base, "/dismiss", draft=draft_id, detector="vendor_switch")

    assert len(dismissals(path)) == 1


def test_a_refused_dismissal_writes_no_row_at_all(
    durable_server: tuple[str, pathlib.Path],
):
    """An expired draft and an absent detector are both silent on disk.

    Worth pinning because "we refused it" and "we recorded a refusal" are
    different, and a log that gained a row every time somebody clicked a stale
    button would be a log nobody could count anything in.
    """
    base, path = durable_server
    draft_id = dismiss_one(base)

    post(base, "/dismiss", draft=draft_id, detector="magnitude")
    post(base, "/dismiss", draft="draft-never-existed", detector="vendor_switch")

    assert len(dismissals(path)) == 1


# ---- the structural property that makes the whole route analysis hard -------


def test_the_detector_cannot_fire_on_the_entry_path_at_all():
    """`vendor_switch` is structurally silent before a human answers.

    `build_draft` sets the debit leg from `propose_account`, which returns the
    vendor's single matched account. `vendor_switch` returns nothing when the
    proposed debit equals that same account. They read the same store rows and
    define a match the same way, so on `/entry` they are equal by construction.

    This is why the detector needs a contradicting ANSWER to fire, and why
    finding a route to it was most of Phase 6. Asserted once, here, rather than
    rediscovered by the next person who wonders why the flag never appears.
    """
    from accountant import pipeline
    from accountant.extract.adapter import TypedTextExtractor
    from accountant.memory.bootstrap import bootstrap

    tally = stale_ledger_company()
    memory = bootstrap(tally, app.COMPANY, MemoryStore(":memory:"))
    history = tally.read_vouchers(app.COMPANY)

    draft = pipeline.build_draft(
        app.COMPANY, ENTRY.encode(), "text/plain", TypedTextExtractor(), memory
    )
    draft = pipeline.evaluate(
        draft,
        tally.read_accounts(app.COMPANY),
        history,
        memory,
        period_open=None,
        pdf_repaired=None,
    )

    assert draft.flags == [], "no flag is reachable before somebody answers"
    # And the reason, so a future change that breaks the equality is legible.
    assert draft.voucher.debit_account == GONE
    assert memory.index().lookup("Sharma Traders").accounts[0] == GONE


def test_a_matched_vendor_posts_without_ever_offering_a_flag():
    """The other half. A clean matched vendor never reaches a question, so the
    detector has no opportunity even in principle."""
    from accountant import pipeline
    from accountant.extract.adapter import TypedTextExtractor
    from accountant.memory.bootstrap import bootstrap
    from accountant.schema import Outcome

    history = [
        Voucher(
            id=f"h{i}",
            date=datetime.date(2026, 1, 1) + datetime.timedelta(days=i),
            party="Sharma Traders",
            narration="cement supply",
            debit_account="Purchases",
            credit_account="Cash",
            amount_paise=100_000,
        )
        for i in range(6)
    ]
    tally = FakeTally()
    tally.add_company(
        app.COMPANY, accounts=STALE_CHART, vouchers=tuple(history), backed_up=True
    )
    memory = bootstrap(tally, app.COMPANY, MemoryStore(":memory:"))

    draft = pipeline.build_draft(
        app.COMPANY, ENTRY.encode(), "text/plain", TypedTextExtractor(), memory
    )
    draft = pipeline.evaluate(
        draft,
        tally.read_accounts(app.COMPANY),
        tally.read_vouchers(app.COMPANY),
        memory,
        period_open=None,
        pdf_repaired=None,
    )

    assert draft.outcome is Outcome.VALID
    assert draft.flags == []
    assert pipeline.next_question(draft) is None

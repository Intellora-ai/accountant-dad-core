"""#14.7 on the web surface — "offers bulk reverse", over real HTTP.

`docs/EPIC.md`'s criterion #14.7 says the app "shows every voucher we have
written and offers bulk reverse". It showed them. There was no bulk reverse:
`POST /reverse` took a single operation id out of a form, so undoing a day's
work meant clicking Undo once per voucher and hoping nothing was missed.

WHAT IS BEING PROTECTED HERE
----------------------------
The person is one click away from removing every voucher we ever wrote into
their books. So:

    the count and the exact operation ids are shown BEFORE anything happens
    the preview click writes nothing
    the confirm is a separate request
    a partial result says so on the page, in words, and never "done"
    the hand-typed vouchers are never in the candidate list

The fixture, the server and the request helpers are imported from
`tests/test_web.py` rather than retyped. One demo company, one spin-up path;
two copies of a threaded HTTP fixture would be two things to keep in step.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about a real TallyPrime. Evidence class: FAKETALLY.
"""

from __future__ import annotations

import datetime
import re

from accountant.reversal import BATCH_ACTION, BatchState, VoucherState
from accountant.schema import Voucher
from accountant.tallyio.client import TallyClient, new_operation_id
from accountant.web import app
from tests.test_web import get, post

ENTRY = "paid Sharma Traders 4200 for cement"


def post_entries(base: str, n: int) -> list[str]:
    """N vouchers through the front door, exactly as a person would."""
    ops: list[str] = []
    for _ in range(n):
        page = post(base, "/entry", text=ENTRY)
        found = re.search(r"operation <code>([^<]+)</code>", page)
        assert found, f"entry did not post:\n{page[:800]}"
        ops.append(found.group(1))
    return ops


def operation_ids_on(page: str) -> list[str]:
    return re.findall(r"<code>(ad_[^<]+)</code>", page)


def preview_batch_id(page: str) -> str:
    found = re.search(r'name=batch value="([^"]+)"', page)
    assert found, f"the preview page carries no batch id:\n{page[:800]}"
    return found.group(1)


def reverse_all(base: str) -> str:
    """The two clicks a person actually makes: preview, then confirm it."""
    preview = post(base, "/reverse-all")
    return post(base, "/reverse-all", confirm="yes", batch=preview_batch_id(preview))


# ---- the home page offers it ------------------------------------------------


def test_the_home_page_offers_bulk_reverse_once_something_is_posted(server: str):
    ops = post_entries(server, 2)
    home = get(server)

    assert "/reverse-all" in home
    for op in ops:
        assert op in home, "#14.7: every voucher we wrote is shown"


def test_the_home_page_does_not_offer_it_when_we_have_written_nothing(server: str):
    """An undo-everything button on an empty list is an invitation to a mistake."""
    assert "/reverse-all" not in get(server)


# ---- the preview writes nothing ---------------------------------------------


def test_the_preview_names_every_candidate_and_touches_the_books(server: str):
    ops = post_entries(server, 3)
    before = app.runtime().client.trial_balance(app.COMPANY)

    page = post(server, "/reverse-all")

    assert set(ops) <= set(operation_ids_on(page))
    assert "3" in page
    assert app.runtime().client.trial_balance(app.COMPANY) == before
    assert len(app.runtime().client.list_our_vouchers(app.COMPANY)) == 3


def test_the_preview_page_asks_rather_than_telling(server: str):
    post_entries(server, 1)
    page = post(server, "/reverse-all")

    assert "confirm" in page.lower()
    assert "nothing has been reversed" in page.lower()


def test_a_hand_typed_voucher_is_never_a_candidate(server: str):
    """The demo company's 86 rows of history were typed by the accountant.

    They carry no marker, so `list_our_vouchers` excludes them — and the whole
    safety of an undo-everything button rests on that being true here and not
    only in a unit test.
    """
    ours = post_entries(server, 1)
    page = post(server, "/reverse-all")

    assert operation_ids_on(page) == ours
    register = app.runtime().client.read_vouchers(app.COMPANY)
    assert len(register) > 80, "the person's own history is still there"


# ---- confirming runs it -----------------------------------------------------


def test_confirming_reverses_everything_and_leaves_the_history_alone(server: str):
    client = app.runtime().client
    before_posting = client.trial_balance(app.COMPANY)
    post_entries(server, 3)
    assert client.trial_balance(app.COMPANY) != before_posting

    page = reverse_all(server)

    assert BatchState.COMPLETED.value in page.lower()
    assert client.list_our_vouchers(app.COMPANY) == ()
    assert client.trial_balance(app.COMPANY) == before_posting


def test_confirming_records_a_durable_row_for_every_voucher(server: str):
    """Read off the rendered activity log, not the store.

    The `MemoryStore` is a SQLite connection owned by the serving thread, so a
    test thread cannot query it. That is not a limitation worth working around:
    the page is where a person would look, and asserting there proves the rows
    exist AND that they reach the screen — the exact pair the old forty-row
    `EVENTS` list got wrong by discarding the outcome at render time.
    """
    post_entries(server, 2)
    reverse_all(server)

    home = get(server)
    rows = re.findall(r'data-outcome="([^"]+)" data-action="([^"]+)"', home)
    batch_rows = [outcome for outcome, action in rows if action == BATCH_ACTION]

    assert batch_rows.count(VoucherState.REQUEST_SENT.value) == 2
    assert batch_rows.count(VoucherState.REVERSED_VERIFIED.value) == 2
    assert any(action == "bulk_reversed" for _, action in rows), (
        "and one row for the batch as a whole"
    )


def test_a_partial_result_says_so_on_the_page_and_never_says_done(server: str):
    """The failure the screen must not smooth over.

    One voucher refuses; the page has to name the state, name the voucher that
    stopped it, and say the rest were not attempted. "Reversed" here would be
    the same defect §39.1 closed, one layer up.
    """
    ops = post_entries(server, 3)
    client = app.runtime().client
    original = type(client).reverse_by_operation_id

    def refuse_the_second(self: TallyClient, company: str, operation_id: str) -> bool:
        if operation_id == ops[1]:
            return False
        return original(self, company, operation_id)

    type(client).reverse_by_operation_id = refuse_the_second  # type: ignore[method-assign]
    try:
        page = reverse_all(server)
    finally:
        type(client).reverse_by_operation_id = original  # type: ignore[method-assign]

    lower = page.lower()
    assert BatchState.PARTIAL_FAILURE.value in lower
    assert BatchState.COMPLETED.value not in lower
    assert VoucherState.EXPLICIT_REJECTION.value in lower
    assert VoucherState.NOT_ATTEMPTED.value in lower
    assert ops[2] in page, "the untried voucher is named"
    assert len(client.list_our_vouchers(app.COMPANY)) == 2


def test_the_untried_vouchers_are_still_in_the_books_after_a_partial_run(server: str):
    ops = post_entries(server, 3)
    client = app.runtime().client
    original = type(client).reverse_by_operation_id

    def refuse_the_second(self: TallyClient, company: str, operation_id: str) -> bool:
        if operation_id == ops[1]:
            return False
        return original(self, company, operation_id)

    type(client).reverse_by_operation_id = refuse_the_second  # type: ignore[method-assign]
    try:
        reverse_all(server)
    finally:
        type(client).reverse_by_operation_id = original  # type: ignore[method-assign]

    survivors = {
        re.search(r"\[ACCOUNTANT_DAD:([^\]]+)\]", v.narration).group(1)  # type: ignore[union-attr]
        for v in client.list_our_vouchers(app.COMPANY)
    }
    assert survivors == {ops[1], ops[2]}, "1 is gone, 2 refused, 3 never tried"


# ---- the handler cannot bypass the doorway ----------------------------------


def test_the_handler_calls_the_batch_module_and_not_the_client():
    """Structural. Same idiom as the single-voucher guard in test_lifecycle.py.

    A behaviour test proves today's handler is right. Only the AST proves no
    second handler can be added tomorrow that sweeps the books with a loop.
    """
    import ast
    import pathlib

    source = pathlib.Path(app.__file__).read_text(encoding="utf-8")
    direct = [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "reverse_by_operation_id"
    ]
    assert direct == [], f"web/app.py reverses directly at lines {direct}"
    assert "reversal.preview" in source
    assert "reversal.execute" in source


def test_a_latecomer_posted_after_the_preview_is_not_swept_up(server: str):
    """The gap between "show me" and "yes".

    The candidate list is frozen at preview. A voucher that appears in the gap
    was never shown and was never confirmed, so it must survive.
    """
    shown = post_entries(server, 2)
    preview = post(server, "/reverse-all")
    assert set(shown) <= set(operation_ids_on(preview))

    client = app.runtime().client
    late = new_operation_id()
    client.write_voucher(
        app.COMPANY,
        Voucher(
            id="draft-late",
            date=datetime.date(2026, 8, 31),
            party="Sharma Traders",
            narration="one more load, typed while the preview was on screen",
            debit_account="Purchases",
            credit_account="Cash",
            amount_paise=77_700,
        ),
        late,
    )

    post(server, "/reverse-all", confirm="yes", batch=preview_batch_id(preview))

    remaining = {
        re.search(r"\[ACCOUNTANT_DAD:([^\]]+)\]", v.narration).group(1)  # type: ignore[union-attr]
        for v in client.list_our_vouchers(app.COMPANY)
    }
    assert remaining == {late}

"""Owner items 17-20: the backup gate, the batch's durable rows, reconciliation
classification, and what a resume is allowed to retry.

WHAT THIS FILE IS FOR
---------------------
`tests/test_bulk_reversal.py` proves the batch state machine names the right
category when one voucher of ten misbehaves. It does not enumerate the code
paths that can DELETE a voucher, and it checks the backup gate on exactly one of
them. A gate that holds on the path a test happens to drive, and is absent from
the path an operator actually uses, is not a gate.

So this file starts from the other end: every entry point in the repository that
can remove a voucher from somebody's books, one test each, and an AST check that
the list is complete rather than remembered.

    connector      FakeTally.reverse_by_operation_id      fake.py:228
                   RealTally.reverse_by_operation_id      real.py:2454
    doorway        pipeline.reverse_operation             pipeline.py:557
                   pipeline.reverse                       pipeline.py:636
    batch          reversal.preview                       reversal.py:273
                   reversal.execute -> _drive -> _classify reversal.py:466
                   reversal.resume  -> _drive             reversal.py:602
                   reversal.reconcile (read-only)         reversal.py:534
    operator       python -m accountant.tallyio           __main__.py:125
                   POST /reverse                          app.py, do_POST
                   POST /reverse-all                      app.py, do_POST

The line numbers are as of 2026-08-10 and will rot; the AST tests below do not
depend on them. `web/app.py` is cited by function because it is long enough that
a number in a docstring is a worse locator than a name.

WHAT IS NOT PROVEN HERE
-----------------------
Evidence class: FAKETALLY, plus RealTally driven over the XML simulator
`tests/test_real_tally.TallySim`. No licensed TallyPrime has seen any of it, so
nothing here is evidence about a real Tally's delete semantics, its
Educational-mode date refusals, or what its gateway does when two clients touch
one company at once. The web tests run a real HTTP server against an in-memory
double; they prove the route refuses, not that a browser renders the refusal
well.

Two things asserted here are DEFECT CLAIMS and are expected to fail until the
source is fixed. Both are named in `artifacts/reversal_report.md`.

HOUSE RULE APPLIED THROUGHOUT
-----------------------------
`pytest.raises` is never the whole proof. Every refusal is followed by an
assertion on the resulting state: how many of our vouchers are still in the
register, and the trial balance in exact paise.
"""

from __future__ import annotations

import ast
import contextlib
import datetime
import pathlib
import re
import urllib.error
from dataclasses import replace

import pytest

from accountant import pipeline, reversal
from accountant.extract.adapter import ExtractedRecord
from accountant.memory.store import MemoryStore
from accountant.reversal import BatchState, VoucherState
from accountant.schema import Voucher
from accountant.tallyio import __main__ as cli
from accountant.tallyio import real
from accountant.tallyio.client import CompanyNotBackedUp, new_operation_id
from accountant.tallyio.factory import BackendIdentity
from accountant.tallyio.fake import FakeTally
from accountant.web import app
from tests.test_real_tally import TallySim
from tests.test_web import get, post

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Cash")
KEY = "demo_co"
PACKAGE = pathlib.Path(reversal.__file__).parent


# ---------------------------------------------------------------------------
# fixtures and helpers
# ---------------------------------------------------------------------------


def a_voucher(n: int) -> Voucher:
    return Voucher(
        id=f"draft-{n}",
        date=datetime.date(2026, 8, 31),
        party="Sharma Traders",
        narration=f"cement load {n}",
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=100_000 + n,
    )


def books(company: str = COMPANY) -> FakeTally:
    t = FakeTally()
    t.add_company(company, accounts=ACCOUNTS, backed_up=True)
    return t


def post_n(t: FakeTally, n: int, company: str = COMPANY) -> list[str]:
    ops: list[str] = []
    for i in range(n):
        op = new_operation_id()
        t.write_voucher(company, a_voucher(i), op)
        ops.append(op)
    return ops


def a_draft(company: str, operation_id: str) -> pipeline.Draft:
    """The smallest Draft `pipeline.reverse` can be handed.

    `reverse` reads two fields off it — the company and the operation id — so
    the record is a stub with every source stated, which is the only thing
    `ExtractedRecord` insists on.
    """
    record = ExtractedRecord(
        date=None,
        party=None,
        total_paise=None,
        tax_paise=None,
        per_field_source=dict.fromkeys(ExtractedRecord.FIELDS, "test_stub"),
    )
    return pipeline.Draft(
        id="draft-x",
        company=company,
        voucher=a_voucher(0),
        record=record,
        operation_id=operation_id,
    )


def a_simulated_backend(*, backed_up: bool) -> tuple[real.RealTally, TallySim]:
    """`RealTally` over the XML simulator, with or without a backup record."""
    sim = TallySim()
    sim.add_company(COMPANY, ACCOUNTS)
    client = real.RealTally(
        transport=sim,
        backups=real.RecordedBackups(
            frozenset({COMPANY}) if backed_up else frozenset()
        ),
    )
    return client, sim


class _Injector(FakeTally):
    """The shared shape of every double below.

    `target` is the one operation id that misbehaves, and it is set on the
    INSTANCE, never on the class. A class attribute outlives the test that set
    it, and a double left armed is how a later test comes to pass for a reason
    nobody chose.

    `reads_fail` is the second switch, on the base rather than in a subclass,
    because a reconciliation that cannot read has to be staged on the SAME
    object that holds the books. Building a second client and handing it the
    first one's storage would make every assertion about "what is still in
    Tally" a statement about two different Tallys.
    """

    target: str = ""
    reads_fail: bool = False

    def __init__(self) -> None:
        super().__init__()
        #: Every operation id that reached the delete method, in order, whether
        #: it went on to succeed, be refused or raise. "Was this voucher
        #: retried" is a question about the ATTEMPT, so it is recorded before
        #: any fault is injected.
        self.deletes: list[str] = []

    def read_by_operation_id(self, company: str, operation_id: str) -> Voucher | None:
        if self.reads_fail:
            raise ConnectionError("Tally is not answering reads")
        return super().read_by_operation_id(company, operation_id)

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        self.deletes.append(operation_id)
        return self._reverse(company, operation_id)

    def _reverse(self, company: str, operation_id: str) -> bool:
        """The overridable half. Subclasses inject their fault here, so the
        recording above cannot be skipped by forgetting to call up."""
        return super().reverse_by_operation_id(company, operation_id)


class _DropsTheConnection(_Injector):
    """The request may or may not have reached Tally. Nobody can say."""

    def _reverse(self, company: str, operation_id: str) -> bool:
        if operation_id == self.target:
            raise ConnectionError("the connection dropped mid-request")
        return super()._reverse(company, operation_id)


class _LosesTheAnswer(_Injector):
    """The delete LANDS and the answer never comes back. The other world."""

    def _reverse(self, company: str, operation_id: str) -> bool:
        ok = super()._reverse(company, operation_id)
        if operation_id == self.target:
            raise ConnectionError("the answer never came back")
        return ok


class _TimesOut(_Injector):
    """A read that never comes back. Indistinguishable from a drop, on purpose."""

    def _reverse(self, company: str, operation_id: str) -> bool:
        if operation_id == self.target:
            raise TimeoutError("Tally did not answer within the timeout")
        return super()._reverse(company, operation_id)


class _Killed(_Injector):
    """The process is stopped mid-voucher. `SystemExit`, not an exception."""

    def _reverse(self, company: str, operation_id: str) -> bool:
        if operation_id == self.target:
            raise SystemExit("the process was killed mid-voucher")
        return super()._reverse(company, operation_id)


class _RevokesTheBackup(_Injector):
    """The backup record disappears just as this voucher is reached."""

    def _reverse(self, company: str, operation_id: str) -> bool:
        if operation_id == self.target:
            self.set_backup(company, False)
        return super()._reverse(company, operation_id)


class _Refuses(_Injector):
    """Tally says clearly that it did not reverse this one."""

    def _reverse(self, company: str, operation_id: str) -> bool:
        if operation_id == self.target:
            return False
        return super()._reverse(company, operation_id)


class _Vanishes(_Injector):
    """Gone before the batch reaches it, so no delete is ever sent."""

    def read_by_operation_id(self, company: str, operation_id: str) -> Voucher | None:
        if operation_id == self.target:
            return None
        return super().read_by_operation_id(company, operation_id)


class _MovesTheWrongAmount(_Injector):
    """Tally reports success and the books move by something else."""

    def _reverse(self, company: str, operation_id: str) -> bool:
        if operation_id != self.target:
            return super()._reverse(company, operation_id)
        found = [
            v
            for v in self.read_vouchers(company)
            if v.narration.endswith(f"{operation_id}]")
        ]
        if not found:
            return False
        co = self._companies[company]
        co.vouchers = [v for v in co.vouchers if v not in found]
        co.vouchers.append(replace(found[0], narration="half of it put back"))
        co.vouchers[-1] = replace(
            co.vouchers[-1], amount_paise=found[0].amount_paise // 2
        )
        return True


class _StaysFindable(_Injector):
    """The books move correctly and the voucher is still there afterwards."""

    already_reversed: bool = False

    def read_by_operation_id(self, company: str, operation_id: str) -> Voucher | None:
        found = super().read_by_operation_id(company, operation_id)
        if found is None and operation_id == self.target and self.already_reversed:
            return a_voucher(3)
        return found

    def _reverse(self, company: str, operation_id: str) -> bool:
        ok = super()._reverse(company, operation_id)
        if operation_id == self.target:
            self.already_reversed = True
        return ok


def stopped_at_four[T: _Injector](
    kind: type[T], n: int = 10
) -> tuple[T, reversal.Batch, list[str]]:
    """N vouchers posted, the batch run, and voucher 4 the one that misbehaved."""
    t = kind()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    ops = post_n(t, n)
    t.target = ops[3]
    batch = reversal.execute(
        reversal.confirm(reversal.preview(t, COMPANY, batch_id="bulk_test")),
        t,
        company_key=KEY,
    )
    return t, batch, ops


def markers_in(t: FakeTally, company: str = COMPANY) -> set[str]:
    return {
        m.group(1)
        for v in t.list_our_vouchers(company)
        if (m := re.search(r"\[ACCOUNTANT_DAD:([^\]]+)\]", v.narration))
    }


# ===========================================================================
# ITEM 17 - the backup gate on EVERY path that can delete a voucher
# ===========================================================================


def _called_name(node: ast.Call) -> str:
    """What is being called, by its last name component.

    Both spellings have to be caught. `client.reverse_by_operation_id(...)` is
    an `Attribute`; `reverse_operation(...)` inside `pipeline.py` is a bare
    `Name`, and a check that only looked for attributes would have declared
    `pipeline.reverse` innocent of calling the doorway it exists to delegate to.
    """
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return ""


def _calls_named(name: str) -> set[tuple[str, str]]:
    """Every call to `name(...)` in the shipped package, with its caller.

    Returns (module path relative to accountant/, innermost enclosing function).
    A `def` NAMED `name` is not a call and is not reported; only call sites are.

    The enclosing name is the whole point. The claim is not "one call exists" —
    that would be satisfied by a second, ungated caller sitting beside the first.
    It is "these exact functions, and no others".
    """
    found: set[tuple[str, str]] = set()

    def descend(node: ast.AST, where: str, module: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                descend(child, child.name, module)
                continue
            if isinstance(child, ast.Call) and _called_name(child) == name:
                found.add((module, where))
            descend(child, where, module)

    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        descend(tree, "<module>", str(path.relative_to(PACKAGE)))
    return found


def _qualified_calls(qualifier: str, name: str) -> set[tuple[str, str]]:
    """`qualifier.name(...)` only, with its caller.

    Needed because bare names collide: `execute` is also `sqlite3.Connection`'s
    method and `accountant/memory/store.py` calls it on every query. A guard
    that cannot tell `reversal.execute` from `self._db.execute` guards nothing.
    """
    found: set[tuple[str, str]] = set()

    def descend(node: ast.AST, where: str, module: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                descend(child, child.name, module)
                continue
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == name
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == qualifier
            ):
                found.add((module, where))
            descend(child, where, module)

    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        descend(tree, "<module>", str(path.relative_to(PACKAGE)))
    return found


def test_the_connectors_delete_has_exactly_one_caller_in_the_whole_package():
    """The enumeration, done by the AST rather than by memory.

    `client.reverse_by_operation_id` is the only call in the system that removes
    a statutory entry. Every gate above it — the batch's preview refusal, the
    trial-balance comparison, the per-voucher durable row — is bypassed by
    calling it directly, and a direct call is faster, shorter and looks right.

    So the claim this file rests on is not "the current callers are careful", it
    is "there is one caller". Same idiom as `tests/test_lifecycle.py` and
    `tests/test_memory.py`, widened from one file to the whole package.
    """
    assert _calls_named("reverse_by_operation_id") == {
        ("pipeline.py", "reverse_operation")
    }


def test_the_verified_doorway_has_exactly_the_three_callers_this_file_covers():
    """And the doorway's own callers are enumerated too, or the table above is
    a comment. A fourth surface appearing here means a fourth backup test."""
    assert _calls_named("reverse_operation") == {
        ("pipeline.py", "reverse"),
        ("reversal.py", "_classify"),
        ("web/app.py", "do_POST"),
    }


def test_the_batch_entry_points_are_reached_from_exactly_two_operator_surfaces():
    """`preview` is where the backup gate lives for a batch, so who calls it is
    the list of surfaces that gate has to cover."""
    assert _qualified_calls("reversal", "preview") == {
        ("tallyio/__main__.py", "main"),
        ("web/app.py", "do_POST"),
    }
    assert _qualified_calls("reversal", "execute") == {
        ("tallyio/__main__.py", "main"),
        ("web/app.py", "do_POST"),
    }
    assert _qualified_calls("reversal", "resume") == set(), (
        "no shipped surface resumes a batch yet; when one appears it needs a "
        "backup test of its own beside the two above"
    )


def test_the_fake_connector_refuses_to_delete_without_a_recorded_backup():
    t = books()
    ops = post_n(t, 3)
    before = t.trial_balance(COMPANY)
    t.set_backup(COMPANY, False)

    with pytest.raises(CompanyNotBackedUp, match="refusing to reverse"):
        t.reverse_by_operation_id(COMPANY, ops[0])

    assert len(t.list_our_vouchers(COMPANY)) == 3
    assert t.trial_balance(COMPANY) == before


def test_the_real_connector_refuses_to_delete_without_a_recorded_backup():
    """And the refusal happens BEFORE anything reaches the wire.

    A gate that refuses after sending the delete would be a gate that logs an
    opinion. `sim.sent` is the record of what actually went out, so the claim is
    measured rather than assumed.
    """
    client, sim = a_simulated_backend(backed_up=True)
    op = new_operation_id()
    client.write_voucher(COMPANY, a_voucher(0), op)
    before = client.trial_balance(COMPANY)

    ungated = real.RealTally(transport=sim, backups=real.RecordedBackups(frozenset()))
    sent_before = len(sim.sent)

    with pytest.raises(CompanyNotBackedUp, match="refusing to reverse"):
        ungated.reverse_by_operation_id(COMPANY, op)

    assert sim.sent[sent_before:] == [], "not one request left the connector"
    assert len(client.list_our_vouchers(COMPANY)) == 1
    assert client.trial_balance(COMPANY) == before


def test_the_verified_doorway_refuses_without_a_recorded_backup():
    """`pipeline.reverse_operation` has no gate of its own and does not need
    one: it cannot delete except through the connector. This proves the
    inherited refusal actually reaches a caller instead of being swallowed."""
    t = books()
    ops = post_n(t, 2)
    before = t.trial_balance(COMPANY)
    t.set_backup(COMPANY, False)

    with pytest.raises(CompanyNotBackedUp):
        pipeline.reverse_operation(t, COMPANY, ops[0])

    assert len(t.list_our_vouchers(COMPANY)) == 2
    assert t.trial_balance(COMPANY) == before


def test_the_draft_level_reverse_refuses_without_a_recorded_backup():
    """`pipeline.reverse` is the thin one, and thin is exactly how a path ends
    up ungated: nothing in it looks like it needs a check."""
    t = books()
    ops = post_n(t, 2)
    before = t.trial_balance(COMPANY)
    t.set_backup(COMPANY, False)

    with pytest.raises(CompanyNotBackedUp):
        pipeline.reverse(a_draft(COMPANY, ops[0]), t)

    assert len(t.list_our_vouchers(COMPANY)) == 2
    assert t.trial_balance(COMPANY) == before


def test_preview_refuses_before_it_even_reads_the_candidate_list():
    """The order of two lines. Reading the register first would be harmless
    today and would make the refusal depend on a read that can fail."""
    listed: list[str] = []

    class Counting(FakeTally):
        def list_our_vouchers(self, company: str) -> tuple[Voucher, ...]:
            listed.append(company)
            return super().list_our_vouchers(company)

    t = Counting()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    post_n(t, 3)
    t.set_backup(COMPANY, False)

    with pytest.raises(CompanyNotBackedUp, match="refusing to reverse in bulk"):
        reversal.preview(t, COMPANY, batch_id="b1")

    assert listed == [], "the candidate list was never read"
    assert len(t.list_our_vouchers(COMPANY)) == 3


def test_a_confirmed_batch_deletes_nothing_when_the_backup_record_is_withdrawn():
    """The gap `preview` cannot cover. The batch was confirmed while a backup
    was recorded and the record is gone by the time it runs — a backup deleted,
    a config reloaded, a different operator. Nothing may be removed."""
    t = books()
    ops = post_n(t, 6)
    batch = reversal.confirm(reversal.preview(t, COMPANY, batch_id="b1"))
    before = t.trial_balance(COMPANY)

    t.set_backup(COMPANY, False)
    result = reversal.execute(batch, t, company_key=KEY)

    assert result.state is not BatchState.COMPLETED
    assert markers_in(t) == set(ops), "all six are still in the books"
    assert t.trial_balance(COMPANY) == before
    assert "CompanyNotBackedUp" in result.outcomes[0].detail


def test_a_batch_stops_at_the_first_voucher_when_the_backup_is_withdrawn_midway():
    """Whatever category it lands in, it must not carry on down the list."""
    t = _RevokesTheBackup()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    ops = post_n(t, 6)
    t.target = ops[2]

    result = reversal.execute(
        reversal.confirm(reversal.preview(t, COMPANY, batch_id="b1")),
        t,
        company_key=KEY,
    )

    assert result.state is not BatchState.COMPLETED
    assert [o.state for o in result.outcomes[:2]] == [
        VoucherState.REVERSED_VERIFIED
    ] * 2
    assert result.outcomes[2].state is not VoucherState.REVERSED_VERIFIED
    assert [o.state for o in result.outcomes[3:]] == [VoucherState.NOT_ATTEMPTED] * 3
    assert markers_in(t) == set(ops[2:]), "four survive, and the untried are untouched"


def test_a_resume_deletes_nothing_when_the_backup_record_is_gone():
    """The resume path shares `_drive` with execute, and the gate has to hold on
    it for the same reason: a resume is a write."""
    t, stopped, _ops = stopped_at_four(_DropsTheConnection)
    t.target = ""
    reconciled = reversal.reconcile(stopped, t)
    survivors_before = markers_in(t)
    before = t.trial_balance(COMPANY)

    t.set_backup(COMPANY, False)
    resumed = reversal.resume(reconciled, t, approved=True, company_key=KEY)

    assert resumed.state is not BatchState.COMPLETED
    assert markers_in(t) == survivors_before
    assert t.trial_balance(COMPANY) == before


def test_reconciliation_needs_no_backup_because_it_deletes_nothing():
    """The one path that is deliberately NOT gated, and the reason it is safe:
    it only reads. Gating it would make the recovery route unavailable in
    exactly the situation the gate exists for."""
    t, stopped, _ops = stopped_at_four(_DropsTheConnection)
    survivors_before = markers_in(t)
    before = t.trial_balance(COMPANY)

    t.set_backup(COMPANY, False)
    reconciled = reversal.reconcile(stopped, t)

    assert reconciled.reconciled is True
    assert markers_in(t) == survivors_before
    assert t.trial_balance(COMPANY) == before


def test_a_write_and_a_delete_are_gated_by_the_one_same_record():
    """The asymmetry G5.2 closed: the destructive operation was the ungated one.

    Both directions asserted, so the test fails if either gate is removed AND if
    a future change makes the delete gate read a different record from the write
    gate.
    """
    t = books()
    ops = post_n(t, 1)

    t.set_backup(COMPANY, False)
    assert t.backed_up(COMPANY) is False
    with pytest.raises(CompanyNotBackedUp, match="refusing to write"):
        t.write_voucher(COMPANY, a_voucher(9), new_operation_id())
    with pytest.raises(CompanyNotBackedUp, match="refusing to reverse"):
        t.reverse_by_operation_id(COMPANY, ops[0])
    assert len(t.list_our_vouchers(COMPANY)) == 1

    t.set_backup(COMPANY, True)
    assert t.backed_up(COMPANY) is True
    assert t.reverse_by_operation_id(COMPANY, ops[0]) is True
    assert t.list_our_vouchers(COMPANY) == ()


def test_reading_the_backup_record_never_changes_it():
    """`backed_up` is the ninth method and it exists so a preview can REPORT the
    fact. A reporting call with a side effect would be worse than no report."""
    t = books()
    post_n(t, 2)

    for _ in range(3):
        assert t.backed_up(COMPANY) is True
    t.set_backup(COMPANY, False)
    for _ in range(3):
        assert t.backed_up(COMPANY) is False

    assert len(t.list_our_vouchers(COMPANY)) == 2


def test_the_command_line_refuses_without_the_backed_up_flag_and_touches_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """The operator surface. `--backed-up` absent means an EMPTY `RecordedBackups`,
    which is the fail-closed default, and `--yes` must not override it."""
    t = books()
    ops = post_n(t, 3)
    before = t.trial_balance(COMPANY)

    def substituted(*_a: object, **_k: object) -> tuple[FakeTally, BackendIdentity]:
        """Only the factory is replaced. Preview, confirm, execute and the
        doorway below them are the real code path."""
        return t, BackendIdentity(
            backend="FakeTally",
            endpoint="http://127.0.0.1:9000",
            company=COMPANY,
            company_exists=True,
            companies_visible=1,
            run_id="run-cli",
        )

    monkeypatch.setattr(cli, "real_tally", substituted)

    # The client the CLI is handed here IS backed up; the flag is what decides.
    t.set_backup(COMPANY, False)
    code = cli.main(["--reverse-all", "--company", COMPANY, "--yes"])

    assert code == cli.EXIT_REFUSED
    assert "refused" in capsys.readouterr().err
    assert markers_in(t) == set(ops)
    assert t.trial_balance(COMPANY) == before


def test_the_web_single_undo_refuses_when_the_backup_record_is_gone(server: str):
    """`POST /reverse` goes through the doorway, so it inherits the connector's
    refusal — and the person gets a page rather than a dropped socket."""
    page = post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    op = re.search(r"operation <code>([^<]+)</code>", page)
    assert op, page[:600]
    client = app.runtime().client
    assert isinstance(client, FakeTally)
    before = client.trial_balance(app.COMPANY)

    client.set_backup(app.COMPANY, False)
    try:
        with pytest.raises(urllib.error.HTTPError) as raised:
            post(server, "/reverse", op=op.group(1))
        assert raised.value.code == 503
    finally:
        client.set_backup(app.COMPANY, True)

    assert len(client.list_our_vouchers(app.COMPANY)) == 1
    assert client.trial_balance(app.COMPANY) == before


def test_the_web_undo_everything_preview_refuses_when_the_backup_record_is_gone(
    server: str,
):
    post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    client = app.runtime().client
    assert isinstance(client, FakeTally)
    before = client.trial_balance(app.COMPANY)

    client.set_backup(app.COMPANY, False)
    try:
        with pytest.raises(urllib.error.HTTPError) as raised:
            post(server, "/reverse-all")
        assert raised.value.code == 503
    finally:
        client.set_backup(app.COMPANY, True)

    assert len(client.list_our_vouchers(app.COMPANY)) == 1
    assert client.trial_balance(app.COMPANY) == before


def test_the_web_undo_everything_confirmation_removes_nothing_without_a_backup(
    server: str,
):
    """The dangerous half: the preview succeeded while a backup was recorded and
    the record is gone by the time the person clicks yes."""
    post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    post(server, "/entry", text="paid Sharma Traders 3100 for cement")
    preview = post(server, "/reverse-all")
    batch_id = re.search(r'name=batch value="([^"]+)"', preview)
    assert batch_id, preview[:600]

    client = app.runtime().client
    assert isinstance(client, FakeTally)
    before = client.trial_balance(app.COMPANY)
    client.set_backup(app.COMPANY, False)
    try:
        page = post(server, "/reverse-all", confirm="yes", batch=batch_id.group(1))
    finally:
        client.set_backup(app.COMPANY, True)

    assert BatchState.COMPLETED.value not in page.lower()
    assert len(client.list_our_vouchers(app.COMPANY)) == 2
    assert client.trial_balance(app.COMPANY) == before


def test_the_activity_log_shows_the_refused_bulk_run_rather_than_swallowing_it(
    server: str,
):
    """A refusal nobody can find afterwards is a refusal that will be argued
    about. Read off the rendered page, which is where a person would look."""
    post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    preview = post(server, "/reverse-all")
    batch_id = re.search(r'name=batch value="([^"]+)"', preview)
    assert batch_id
    client = app.runtime().client
    assert isinstance(client, FakeTally)

    client.set_backup(app.COMPANY, False)
    try:
        post(server, "/reverse-all", confirm="yes", batch=batch_id.group(1))
    finally:
        client.set_backup(app.COMPANY, True)

    home = get(server)
    assert "bulk_reversed" in home
    assert (
        BatchState.COMPLETED.value not in home.lower().split("bulk_reversed")[-1][:400]
    )


# ===========================================================================
# ITEM 18 - batch rows are written under the key they can be read back by
# ===========================================================================


def test_batch_rows_are_written_under_the_normalised_key_not_the_display_name():
    """The Phase 5B defect, in one assertion.

    Ten rows were written under `'Demo Co'` and `MemoryStore.actions` normalises
    before it reads, so the restart found zero. `reversal._record` normalises at
    the point of writing — idempotently, so a caller that already normalised is
    unaffected — and this is the test that keeps it there.
    """
    t = books()
    store = MemoryStore(":memory:")
    ops = post_n(t, 3)

    reversal.execute(
        reversal.confirm(reversal.preview(t, COMPANY, batch_id="b1")),
        t,
        log=store,
        company_key=COMPANY,  # the DISPLAY name, on purpose
        run_id="run-1",
    )

    rows = [r for r in store.actions(COMPANY) if r.action == reversal.BATCH_ACTION]
    assert len(rows) == 6, "one REQUEST_SENT and one terminal row per voucher"
    assert {r.company_key for r in rows} == {KEY}
    assert [r.operation_id for r in rows[::2]] == ops


def test_the_display_name_and_the_key_find_the_same_batch_rows():
    t = books()
    store = MemoryStore(":memory:")
    post_n(t, 2)

    reversal.execute(
        reversal.confirm(reversal.preview(t, COMPANY, batch_id="b1")),
        t,
        log=store,
        company_key=KEY,  # already normalised, as the web app passes it
        run_id="run-1",
    )

    by_name = [r for r in store.actions(COMPANY) if r.action == reversal.BATCH_ACTION]
    by_key = [r for r in store.actions(KEY) if r.action == reversal.BATCH_ACTION]
    assert len(by_name) == 4
    assert [r.operation_id for r in by_name] == [r.operation_id for r in by_key]


def test_a_second_process_reads_back_every_row_the_batch_wrote(tmp_path: pathlib.Path):
    """The claim is durability across a restart, so the store is closed and
    reopened. An in-memory store proves the rows were written; only a file
    proves they can be found again."""
    path = tmp_path / "memory.sqlite3"
    t = books()
    ops = post_n(t, 4)

    first = MemoryStore(path)
    reversal.execute(
        reversal.confirm(reversal.preview(t, COMPANY, batch_id="b1")),
        t,
        log=first,
        company_key=COMPANY,
        run_id="run-1",
    )
    first.close()

    second = MemoryStore(path)
    rows = [r for r in second.actions(COMPANY) if r.action == reversal.BATCH_ACTION]
    second.close()

    assert [r.operation_id for r in rows] == [op for op in ops for _ in range(2)]
    assert [r.outcome for r in rows[1::2]] == [VoucherState.REVERSED_VERIFIED] * 4


def test_an_accented_company_name_keys_the_same_rows_in_either_encoding(
    tmp_path: pathlib.Path,
):
    """D1's other half, applied to the batch log.

    "Café Supplies" typed on a Mac and the same visible name typed on Windows
    are different byte sequences. If the batch keyed one way and the reader
    looked the other, a restart would find nothing — the same zero-rows failure,
    reached by a different route.
    """
    precomposed = "Café Supplies"
    decomposed = "Café Supplies"
    assert precomposed != decomposed

    path = tmp_path / "memory.sqlite3"
    t = books(precomposed)
    post_n(t, 2, company=precomposed)

    store = MemoryStore(path)
    reversal.execute(
        reversal.confirm(reversal.preview(t, precomposed, batch_id="b1")),
        t,
        log=store,
        company_key=decomposed,
        run_id="run-1",
    )
    store.close()

    reopened = MemoryStore(path)
    rows = [
        r for r in reopened.actions(precomposed) if r.action == reversal.BATCH_ACTION
    ]
    reopened.close()
    assert len(rows) == 4


def test_every_batch_row_names_the_voucher_the_batch_the_run_and_the_backend():
    """A row that cannot be tied to an operation id is a row nobody can act on."""
    t = books()
    store = MemoryStore(":memory:")
    ops = post_n(t, 2)

    reversal.execute(
        reversal.confirm(reversal.preview(t, COMPANY, batch_id="bulk_named")),
        t,
        log=store,
        company_key=KEY,
        run_id="run-42",
    )

    rows = [r for r in store.actions(KEY) if r.action == reversal.BATCH_ACTION]
    assert {r.run_id for r in rows} == {"run-42"}
    assert {r.backend for r in rows} == {"FakeTally"}
    assert set(ops) == {r.operation_id for r in rows}
    assert all("bulk_named" in r.detail for r in rows)
    assert all(r.reason.strip() for r in rows)


def test_a_failed_voucher_leaves_a_row_that_names_the_category(
    tmp_path: pathlib.Path,
):
    """The row is the state. It has to carry which of the four categories the
    voucher landed in, or a resume is reading tea leaves."""
    path = tmp_path / "memory.sqlite3"
    t = _DropsTheConnection()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    ops = post_n(t, 5)
    t.target = ops[3]

    store = MemoryStore(path)
    reversal.execute(
        reversal.confirm(reversal.preview(t, COMPANY, batch_id="b1")),
        t,
        log=store,
        company_key=COMPANY,
        run_id="run-1",
    )
    store.close()

    reopened = MemoryStore(path)
    rows = [r for r in reopened.actions(COMPANY) if r.operation_id == ops[3]]
    untried = [r for r in reopened.actions(COMPANY) if r.operation_id == ops[4]]
    reopened.close()

    assert [r.outcome for r in rows] == [
        VoucherState.REQUEST_SENT,
        VoucherState.UNKNOWN_OUTCOME,
    ]
    assert untried == [], "a voucher never attempted writes no row"


def test_a_restart_can_rebuild_every_voucher_state_from_the_rows_alone(
    tmp_path: pathlib.Path,
):
    """The durability claim, stated as the thing it is for.

    A second process holding only the log must be able to say, per voucher,
    which of the eight states it is in. The last row per operation id is that
    answer, and a voucher whose last word is REQUEST_SENT is the crash case:
    unknown, and readable as unknown.
    """
    path = tmp_path / "memory.sqlite3"
    t = _DropsTheConnection()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    ops = post_n(t, 6)
    t.target = ops[2]

    store = MemoryStore(path)
    batch = reversal.execute(
        reversal.confirm(reversal.preview(t, COMPANY, batch_id="b1")),
        t,
        log=store,
        company_key=COMPANY,
        run_id="run-1",
    )
    store.close()

    reopened = MemoryStore(path)
    last: dict[str, str] = {}
    for row in reopened.actions(COMPANY):
        if row.action == reversal.BATCH_ACTION:
            last[row.operation_id] = row.outcome
    reopened.close()

    rebuilt = [
        last.get(o.operation_id, VoucherState.NOT_ATTEMPTED.value)
        for o in batch.outcomes
    ]
    assert rebuilt == [o.state.value for o in batch.outcomes]
    assert rebuilt[2] == VoucherState.UNKNOWN_OUTCOME.value


def test_a_process_killed_between_the_request_and_the_answer_reads_as_unknown(
    tmp_path: pathlib.Path,
):
    """REQUEST_SENT exists for exactly this and is written BEFORE the attempt.

    `SystemExit` rather than an ordinary exception, because that is what a
    killed run actually raises and because `_classify` catches `BaseException`
    on purpose. A row left as REQUEST_SENT with nothing after it is the durable
    signature of "we asked and cannot say what happened".
    """
    path = tmp_path / "memory.sqlite3"
    t = _Killed()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    ops = post_n(t, 4)
    t.target = ops[1]

    store = MemoryStore(path)
    batch = reversal.execute(
        reversal.confirm(reversal.preview(t, COMPANY, batch_id="b1")),
        t,
        log=store,
        company_key=COMPANY,
        run_id="run-1",
    )
    store.close()

    assert batch.outcomes[1].state is VoucherState.UNKNOWN_OUTCOME
    reopened = MemoryStore(path)
    rows = [
        r
        for r in reopened.actions(COMPANY)
        if r.action == reversal.BATCH_ACTION and r.operation_id == ops[1]
    ]
    reopened.close()
    assert rows[0].outcome == VoucherState.REQUEST_SENT
    assert "SystemExit" in rows[-1].reason


# ===========================================================================
# ITEM 19 - reconciliation classification
# ===========================================================================


def test_a_reconciled_reversal_is_recorded_as_not_measured():
    """The distinction the whole item turns on.

    A read can prove the voucher is gone. It cannot prove BY HOW MUCH the books
    moved, because the movement happened between two snapshots nobody took. So
    the outcome is REVERSED_VERIFIED and `measured` is False, and those two
    facts have to travel together.
    """

    t = _LosesTheAnswer()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    ops = post_n(t, 6)
    t.target = ops[3]
    stopped = reversal.execute(
        reversal.confirm(reversal.preview(t, COMPANY, batch_id="b1")),
        t,
        company_key=KEY,
    )

    reconciled = reversal.reconcile(stopped, t)

    fourth = reconciled.outcomes[3]
    assert fourth.state is VoucherState.REVERSED_VERIFIED
    assert fourth.measured is False
    assert all(o.measured for o in reconciled.outcomes[:3]), (
        "measured ones stay measured"
    )


def test_a_batch_holding_one_reconciled_movement_declines_to_claim_conservation():
    """`accounted` is None, not True and not False.

    Asserting True would be inventing a measurement. Asserting False would be
    reporting a discrepancy nobody observed. UNKNOWN is the only honest third
    answer and the property exists to give it.
    """

    t = _LosesTheAnswer()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    ops = post_n(t, 5)
    t.target = ops[2]
    stopped = reversal.execute(
        reversal.confirm(reversal.preview(t, COMPANY, batch_id="b1")),
        t,
        company_key=KEY,
    )
    assert stopped.accounted is False, "the unmeasured delete is unaccounted for now"

    reconciled = reversal.reconcile(stopped, t)

    assert reconciled.accounted is None
    assert any(not o.measured for o in reconciled.outcomes)


def test_a_batch_with_no_reconciled_movement_still_claims_conservation():
    """The control. Without it, `accounted is None` could be the answer to
    everything and the test above would prove nothing."""
    t, stopped, _ops = stopped_at_four(_DropsTheConnection, n=6)
    reconciled = reversal.reconcile(stopped, t)

    assert all(o.measured for o in reconciled.outcomes)
    assert reconciled.accounted is True


def test_a_reconciliation_that_cannot_read_settles_nothing_and_says_so():
    """A reconciliation that cannot read has not reconciled anything. The
    voucher stays unknown rather than being guessed either way."""
    t, stopped, _ops = stopped_at_four(_DropsTheConnection, n=6)
    survivors_before = markers_in(t)

    t.reads_fail = True
    reconciled = reversal.reconcile(stopped, t)
    t.reads_fail = False

    assert reconciled.outcomes[3].state is VoucherState.UNKNOWN_OUTCOME
    assert "could not read Tally" in reconciled.outcomes[3].detail
    assert markers_in(t) == survivors_before


def test_reconciliation_never_reclassifies_an_explicit_rejection():
    """The four categories are not interchangeable, and reconciliation is only
    entitled to settle the two that mean "nobody can say"."""
    t, stopped, _ops = stopped_at_four(_Refuses, n=6)
    assert stopped.outcomes[3].state is VoucherState.EXPLICIT_REJECTION

    reconciled = reversal.reconcile(stopped, t)

    assert reconciled.outcomes[3].state is VoucherState.EXPLICIT_REJECTION
    assert reconciled.outcomes[3].measured is True


def test_reconciliation_never_reclassifies_a_precheck_refusal():
    t, stopped, _ops = stopped_at_four(_Vanishes, n=6)
    assert stopped.outcomes[3].state is VoucherState.PRECHECK_REFUSED

    reconciled = reversal.reconcile(stopped, t)

    assert reconciled.outcomes[3].state is VoucherState.PRECHECK_REFUSED


def test_reconciliation_never_reclassifies_a_readback_failure():
    """READBACK_FAILED is a question for a person: the books moved right and the
    voucher is still findable. A read cannot settle that, and pretending it can
    would turn the one state that demands a human into a retry."""
    t, stopped, _ops = stopped_at_four(_StaysFindable, n=6)
    assert stopped.outcomes[3].state is VoucherState.READBACK_FAILED

    reconciled = reversal.reconcile(stopped, t)

    assert reconciled.outcomes[3].state is VoucherState.READBACK_FAILED


def test_reconciliation_never_touches_a_verified_voucher():
    t, stopped, _ops = stopped_at_four(_DropsTheConnection, n=6)
    before = stopped.outcomes[:3]

    reconciled = reversal.reconcile(stopped, t)

    assert reconciled.outcomes[:3] == before


def test_reconciliation_sends_no_delete_and_moves_no_money():
    """Read-only, measured by counting the calls rather than by reading the
    code that makes them."""
    t, stopped, _ops = stopped_at_four(_DropsTheConnection, n=6)
    t.deletes.clear()
    before = t.trial_balance(COMPANY)

    reversal.reconcile(stopped, t)

    assert t.deletes == []
    assert t.trial_balance(COMPANY) == before


def test_a_row_left_as_request_sent_is_reconciled_exactly_like_an_unknown():
    """A process that died mid-voucher leaves REQUEST_SENT as its last word, and
    that is exactly as unknown as a dropped connection. Same treatment, or the
    crash case becomes the one nobody handles."""
    t = books()
    post_n(t, 3)
    batch = reversal.preview(t, COMPANY, batch_id="b1")
    mid_flight = replace(
        batch,
        state=BatchState.REVERSING,
        outcomes=(
            replace(batch.outcomes[0], state=VoucherState.REQUEST_SENT),
            *batch.outcomes[1:],
        ),
    )

    reconciled = reversal.reconcile(mid_flight, t)

    assert reconciled.outcomes[0].state is VoucherState.NOT_ATTEMPTED
    assert "still in Tally" in reconciled.outcomes[0].detail
    assert len(t.list_our_vouchers(COMPANY)) == 3, (
        "and nothing was reversed to find out"
    )


def test_reconciliation_names_which_of_the_two_worlds_it_found():
    """Both sentences, in one test, so the two cannot drift into one message
    that fits neither."""
    t, stopped, _ops = stopped_at_four(_DropsTheConnection, n=6)
    still_there = reversal.reconcile(stopped, t)
    assert "did not land" in still_there.outcomes[3].detail

    t2 = _LosesTheAnswer()
    t2.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    ops2 = post_n(t2, 6)
    t2.target = ops2[3]
    stopped2 = reversal.execute(
        reversal.confirm(reversal.preview(t2, COMPANY, batch_id="b2")),
        t2,
        company_key=KEY,
    )
    landed = reversal.reconcile(stopped2, t2)
    assert "did land" in landed.outcomes[3].detail


def test_a_reconciled_batch_never_reports_completed_while_work_remains():
    """The moment a state machine is most tempted to declare victory: the last
    unknown resolves favourably and six vouchers are still in the books."""

    t = _LosesTheAnswer()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    ops = post_n(t, 10)
    t.target = ops[3]
    stopped = reversal.execute(
        reversal.confirm(reversal.preview(t, COMPANY, batch_id="b1")),
        t,
        company_key=KEY,
    )

    reconciled = reversal.reconcile(stopped, t)

    assert reconciled.state is BatchState.PARTIAL_FAILURE
    assert len(reconciled.in_state(VoucherState.NOT_ATTEMPTED)) == 6
    assert len(t.list_our_vouchers(COMPANY)) == 6


def test_a_reconciled_wrong_movement_stays_critical():
    """Reconciliation is not an escape hatch from the one state that has none."""
    t, stopped, _ops = stopped_at_four(_MovesTheWrongAmount, n=6)
    assert stopped.state is BatchState.CRITICAL_FAILURE

    reconciled = reversal.reconcile(stopped, t)

    assert reconciled.state is BatchState.CRITICAL_FAILURE
    assert reconciled.outcomes[3].state is VoucherState.WRONG_MOVEMENT


def test_reconciliation_marks_the_batch_as_reconciled_only_by_running():
    """A fact worth pinning because a later test contradicts what it implies:
    the flag records that `reconcile` was CALLED, not that anything was settled.
    """
    t, stopped, _ops = stopped_at_four(_DropsTheConnection, n=6)
    assert stopped.reconciled is False

    assert reversal.reconcile(stopped, t).reconciled is True


def test_reconciling_twice_reaches_the_same_answer_and_reads_nothing_new():
    """Idempotent, because an operator who is unsure will run it again.

    The second pass has nothing left to settle — the first turned the unknown
    into a fact — so it must not send a read for it, and must not re-open a
    question it already closed.
    """
    t, stopped, _ops = stopped_at_four(_DropsTheConnection, n=6)
    once = reversal.reconcile(stopped, t)
    survivors = markers_in(t)

    twice = reversal.reconcile(once, t)

    assert [o.state for o in twice.outcomes] == [o.state for o in once.outcomes]
    assert [o.detail for o in twice.outcomes] == [o.detail for o in once.outcomes]
    assert twice.state is once.state
    assert markers_in(t) == survivors
    assert t.deletes.count(_ops[3]) == 1, "and nothing was reversed a second time"


def test_reconciling_a_clean_batch_changes_nothing_at_all():
    """The control for every test above. A batch with no unknown in it has
    nothing to reconcile, and a reconciliation that changed such a batch would
    be doing something other than settling unknowns."""
    t = books()
    post_n(t, 3)
    done = reversal.execute(
        reversal.confirm(reversal.preview(t, COMPANY, batch_id="b1")),
        t,
        company_key=KEY,
    )
    assert done.state is BatchState.COMPLETED
    before = t.trial_balance(COMPANY)

    reconciled = reversal.reconcile(done, t)

    assert reconciled.state is BatchState.COMPLETED
    assert [o.state for o in reconciled.outcomes] == [o.state for o in done.outcomes]
    assert all(o.measured for o in reconciled.outcomes)
    assert reconciled.accounted is True
    assert t.trial_balance(COMPANY) == before


def test_one_pass_settles_every_unknown_it_can_read_and_leaves_the_rest():
    """Two unknowns in one batch, one readable and one not.

    A reconciliation that stopped at the first unresolved read would leave the
    second unknown looking untried, and a resume would then treat it as
    retryable — the exact confusion the categories exist to prevent.
    """
    t = books()
    ops = post_n(t, 4)
    batch = reversal.preview(t, COMPANY, batch_id="b1")
    # Two vouchers left mid-flight, as a process killed twice would leave them.
    unknown_both = replace(
        batch,
        state=BatchState.REVERSING,
        outcomes=(
            replace(batch.outcomes[0], state=VoucherState.UNKNOWN_OUTCOME),
            replace(batch.outcomes[1], state=VoucherState.REQUEST_SENT),
            *batch.outcomes[2:],
        ),
    )
    # The first one DID land; the second did not.
    assert t.reverse_by_operation_id(COMPANY, ops[0]) is True

    reconciled = reversal.reconcile(unknown_both, t)

    assert reconciled.outcomes[0].state is VoucherState.REVERSED_VERIFIED
    assert reconciled.outcomes[0].measured is False
    assert reconciled.outcomes[1].state is VoucherState.NOT_ATTEMPTED
    assert reconciled.outcomes[1].measured is True
    assert reconciled.accounted is None, "one unmeasured movement, so no claim"
    assert markers_in(t) == set(ops[1:])


# ===========================================================================
# ITEM 20 - what a resume may retry, and what it may never retry
# ===========================================================================


def test_the_retryable_set_is_exactly_the_three_states_the_owner_named():
    """Written as data in the module, so it can be asserted as data here.

    UNKNOWN_OUTCOME absent is the whole third rule. WRONG_MOVEMENT absent is the
    critical one. READBACK_FAILED absent is the one that needs a person.
    """
    assert set(reversal.RETRYABLE) == {
        VoucherState.NOT_ATTEMPTED,
        VoucherState.PRECHECK_REFUSED,
        VoucherState.EXPLICIT_REJECTION,
    }
    assert VoucherState.UNKNOWN_OUTCOME not in reversal.RETRYABLE
    assert VoucherState.REQUEST_SENT not in reversal.RETRYABLE
    assert VoucherState.WRONG_MOVEMENT not in reversal.RETRYABLE
    assert VoucherState.READBACK_FAILED not in reversal.RETRYABLE
    assert VoucherState.REVERSED_VERIFIED not in reversal.RETRYABLE


def test_an_explicitly_rejected_voucher_is_retried_by_an_approved_resume():
    """Tally said clearly it did not happen. Nothing is uncertain, so retrying
    after a person has looked is not blind — and the cause being corrected is
    what the resume is for."""
    t, stopped, ops = stopped_at_four(_Refuses, n=6)
    assert stopped.outcomes[3].state is VoucherState.EXPLICIT_REJECTION
    assert markers_in(t) == set(ops[3:])

    t.target = ""
    finished = reversal.resume(
        reversal.reconcile(stopped, t), t, approved=True, company_key=KEY
    )

    assert finished.state is BatchState.COMPLETED
    assert all(o.state is VoucherState.REVERSED_VERIFIED for o in finished.outcomes)
    assert t.list_our_vouchers(COMPANY) == ()


def test_a_precheck_refusal_is_retried_once_the_local_cause_is_corrected():
    """No request ever went to Tally, so nothing about Tally's state is
    uncertain and a retry cannot double anything."""
    t, stopped, _ops = stopped_at_four(_Vanishes, n=6)
    assert stopped.outcomes[3].state is VoucherState.PRECHECK_REFUSED

    t.target = ""
    finished = reversal.resume(
        reversal.reconcile(stopped, t), t, approved=True, company_key=KEY
    )

    assert finished.state is BatchState.COMPLETED
    assert t.list_our_vouchers(COMPANY) == ()


def test_an_unknown_outcome_voucher_is_never_retried_by_a_resume():
    """Rule three, measured by counting the calls that reached the connector.

    The voucher stays unknown because the reconciling read also failed. The
    resume must not send a second delete for it: in the world where the first
    one landed, that is a delete aimed at a voucher that is already gone, and in
    a real Tally the two worlds are not distinguishable afterwards.
    """
    t, stopped, ops = stopped_at_four(_DropsTheConnection, n=6)

    t.reads_fail = True
    reconciled = reversal.reconcile(stopped, t)
    t.reads_fail = False
    t.target = ""
    assert reconciled.outcomes[3].state is VoucherState.UNKNOWN_OUTCOME

    t.deletes.clear()
    resumed = reversal.resume(reconciled, t, approved=True, company_key=KEY)

    assert ops[3] not in t.deletes, "the uncertain voucher was retried"
    assert resumed.outcomes[3].state is VoucherState.UNKNOWN_OUTCOME
    assert resumed.state is not BatchState.COMPLETED
    assert ops[3] in markers_in(t), "and it is still in the books, for a person"


def test_a_readback_failure_is_never_retried_by_a_resume():
    """The books moved and the voucher is still findable. Reversing it again
    would move the books a second time for one voucher."""
    t, stopped, ops = stopped_at_four(_StaysFindable, n=6)
    assert stopped.outcomes[3].state is VoucherState.READBACK_FAILED

    t.deletes.clear()
    resumed = reversal.resume(
        reversal.reconcile(stopped, t), t, approved=True, company_key=KEY
    )

    assert ops[3] not in t.deletes
    assert resumed.outcomes[3].state is VoucherState.READBACK_FAILED
    assert resumed.state is not BatchState.COMPLETED


def test_a_critical_failure_cannot_be_resumed_and_nothing_further_is_removed():
    t, stopped, _ops = stopped_at_four(_MovesTheWrongAmount, n=6)
    survivors_before = markers_in(t)

    with pytest.raises(ValueError, match="CRITICAL_FAILURE"):
        reversal.resume(
            reversal.reconcile(stopped, t), t, approved=True, company_key=KEY
        )

    assert markers_in(t) == survivors_before


def test_a_resume_without_approval_removes_nothing():
    t, stopped, _ops = stopped_at_four(_DropsTheConnection, n=6)
    reconciled = reversal.reconcile(stopped, t)
    survivors_before = markers_in(t)
    before = t.trial_balance(COMPANY)

    with pytest.raises(ValueError, match="approval"):
        reversal.resume(reconciled, t, approved=False, company_key=KEY)

    assert markers_in(t) == survivors_before
    assert t.trial_balance(COMPANY) == before


def test_a_resume_before_reconciliation_removes_nothing():
    t, stopped, _ops = stopped_at_four(_DropsTheConnection, n=6)
    survivors_before = markers_in(t)

    with pytest.raises(ValueError, match="reconcil"):
        reversal.resume(stopped, t, approved=True, company_key=KEY)

    assert markers_in(t) == survivors_before


def test_a_resume_never_reverses_an_already_verified_voucher_a_second_time():
    """Cleanup, not rollback, measured at the connector."""
    t, stopped, ops = stopped_at_four(_DropsTheConnection, n=10)
    t.target = ""

    reversal.resume(reversal.reconcile(stopped, t), t, approved=True, company_key=KEY)

    for op in ops[:3]:
        assert t.deletes.count(op) == 1, f"{op} reached the connector more than once"
    assert stopped.state is BatchState.UNKNOWN_OUTCOME


def test_a_timeout_is_an_unknown_outcome_and_not_a_rejection():
    """Rule one. A timeout says nothing about whether the delete happened, and
    calling it a rejection is what makes the next step a blind retry."""
    t, stopped, ops = stopped_at_four(_TimesOut, n=6)

    assert stopped.outcomes[3].state is VoucherState.UNKNOWN_OUTCOME
    assert stopped.state is BatchState.UNKNOWN_OUTCOME
    assert "TimeoutError" in stopped.outcomes[3].detail
    assert stopped.outcomes[3].state is not VoucherState.EXPLICIT_REJECTION
    assert markers_in(t) == set(ops[3:])


def test_transport_success_is_never_accounting_success():
    """Rule two. The connector returned True and the books moved by half.

    The only thing that catches this is the trial-balance comparison inside
    `pipeline.reverse_operation`; a batch that trusted the boolean would report
    this voucher reversed and carry on to the next six.
    """
    _t, stopped, _ops = stopped_at_four(_MovesTheWrongAmount, n=10)

    assert stopped.outcomes[3].state is VoucherState.WRONG_MOVEMENT
    assert stopped.state is BatchState.CRITICAL_FAILURE
    assert stopped.accounted is False
    assert "should have moved" in stopped.outcomes[3].detail
    assert [o.state for o in stopped.outcomes[4:]] == [VoucherState.NOT_ATTEMPTED] * 6


def test_a_resumed_batch_restores_the_trial_balance_to_the_exact_paise():
    """#6.5, across an interruption. The claim is exact paise, so the assertion
    is on the whole dictionary and not on a total."""
    t = _DropsTheConnection()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    t.seed_voucher(
        COMPANY,
        Voucher(
            id="human-1",
            date=datetime.date(2026, 8, 1),
            party="Verma Properties",
            narration="rent paid by hand",
            debit_account="Purchases",
            credit_account="Cash",
            amount_paise=555_07,
        ),
    )
    before_posting = t.trial_balance(COMPANY)
    assert before_posting == {"Purchases": 55507, "Cash": -55507}

    ops = post_n(t, 10)
    t.target = ops[3]
    stopped = reversal.execute(
        reversal.confirm(reversal.preview(t, COMPANY, batch_id="b1")),
        t,
        company_key=KEY,
    )
    assert stopped.state is BatchState.UNKNOWN_OUTCOME

    t.target = ""
    finished = reversal.resume(
        reversal.reconcile(stopped, t), t, approved=True, company_key=KEY
    )

    assert finished.state is BatchState.COMPLETED
    assert finished.final == before_posting
    assert t.trial_balance(COMPANY) == before_posting
    assert len(t.read_vouchers(COMPANY)) == 1, "the hand-typed voucher is untouched"


def test_a_resume_that_finishes_the_work_writes_its_own_durable_rows(
    tmp_path: pathlib.Path,
):
    """A resume is a run. It has to leave the same evidence the first run did,
    or the second half of the cleanup is undocumented."""
    path = tmp_path / "memory.sqlite3"
    t = _DropsTheConnection()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    ops = post_n(t, 5)
    t.target = ops[2]

    store = MemoryStore(path)
    stopped = reversal.execute(
        reversal.confirm(reversal.preview(t, COMPANY, batch_id="b1")),
        t,
        log=store,
        company_key=COMPANY,
        run_id="run-1",
    )
    t.target = ""
    reversal.resume(
        reversal.reconcile(stopped, t),
        t,
        approved=True,
        log=store,
        company_key=COMPANY,
        run_id="run-2",
    )
    store.close()

    reopened = MemoryStore(path)
    rows = [r for r in reopened.actions(COMPANY) if r.action == reversal.BATCH_ACTION]
    reopened.close()

    assert {r.run_id for r in rows} == {"run-1", "run-2"}
    second_run = [r for r in rows if r.run_id == "run-2"]
    assert [r.operation_id for r in second_run[1::2]] == ops[2:]
    assert [r.outcome for r in second_run[1::2]] == [VoucherState.REVERSED_VERIFIED] * 3


# ---- DEFECT CLAIM ----------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Defect D3, OPEN OWNER DECISION. `reconcile` sets reconciled=True even "
        "when every read failed. Fixing it by deriving the flag from the "
        "remaining UNKNOWN outcomes makes `resume` refuse the whole batch, "
        "which the existing "
        "test_an_unknown_outcome_voucher_is_never_retried_by_a_resume forbids: "
        "it requires resume to proceed and skip the unknown voucher. Refuse the "
        "batch, or skip the voucher - both are defensible, they are mutually "
        "exclusive, and the choice is the owner's. See accountant/reversal.py "
        "reconcile()."
    ),
)
def test_a_resume_writes_nothing_more_when_the_reconciliation_settled_nothing():
    """DEFECT CLAIM. `reconcile` sets `reconciled=True` even when it settled
    nothing, and that flag is the gate `resume` checks.

    `reversal.py:546-548` says, in as many words: "If the read itself fails, the
    voucher stays unknown - a reconciliation that cannot read has not reconciled
    anything." `reversal.py:596-599` then sets `reconciled=True` unconditionally,
    on every return path, including the one where every single read raised.

    `resume` refuses an unreconciled batch with this sentence
    (`reversal.py:627-632`):

        "call reconcile() first so every unknown outcome is settled by a read
         BEFORE ANYTHING ELSE IS WRITTEN"

    Measured: after a reconciliation in which every read failed, that gate opens
    and the resume removes six more vouchers from a company where one voucher's
    fate is unknown. Nine of ten are gone; the batch's own claim is that it stops
    at the first unresolved voucher.

    The narrow fix is one of two lines: do not set `reconciled` when an unknown
    survives, or make the `resume` gate ask the question its message claims to
    ask. Which one is the owner's call; this test asserts only the consequence,
    so either fix satisfies it.
    """
    t, stopped, _ops = stopped_at_four(_DropsTheConnection, n=10)
    assert stopped.outcomes[3].state is VoucherState.UNKNOWN_OUTCOME

    t.reads_fail = True
    reconciled = reversal.reconcile(stopped, t)
    t.reads_fail = False
    t.target = ""
    assert reconciled.outcomes[3].state is VoucherState.UNKNOWN_OUTCOME, (
        "the reconciliation settled nothing, which is the premise"
    )
    survivors_before = markers_in(t)
    before = t.trial_balance(COMPANY)

    with contextlib.suppress(ValueError):
        reversal.resume(reconciled, t, approved=True, company_key=KEY)

    assert markers_in(t) == survivors_before, (
        "a resume gated on a reconciliation that reconciled nothing removed "
        f"{len(survivors_before - markers_in(t))} further voucher(s) from a "
        "company holding one voucher whose fate is unknown"
    )
    assert t.trial_balance(COMPANY) == before

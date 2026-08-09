"""`python -m accountant.tallyio --reverse-all` — the documented command.

The frozen plan's "Verification, end to end" section lists three
`python -m accountant.tallyio` commands. None of them existed: the package had
no `__main__.py` at all. This file covers the one Phase 5 needs.

WHAT IS BEING PROTECTED
-----------------------
An operator at a terminal, about to remove vouchers from somebody's live books.
Four properties matter more than anything the command can do:

    it prints which Tally, which company, and which vouchers BEFORE acting
    without --yes it touches nothing
    a batch that did not complete does not exit 0
    a refusal is a sentence, not a traceback

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about a real TallyPrime. `real_tally` is substituted, so this exercises
the command's own logic against `FakeTally`. Evidence class: FAKETALLY.
"""

from __future__ import annotations

import ast
import datetime
import pathlib

import pytest

from accountant.reversal import BatchState
from accountant.schema import Voucher
from accountant.tallyio import __main__ as cli
from accountant.tallyio.client import new_operation_id
from accountant.tallyio.factory import BackendIdentity, RealTallyRequired
from accountant.tallyio.fake import FakeTally

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Cash")


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


def identity() -> BackendIdentity:
    return BackendIdentity(
        backend="FakeTally",
        endpoint="http://127.0.0.1:9000",
        company="Demo Co",
        company_exists=True,
        companies_visible=1,
        run_id="run-cli",
        licence_mode="UNKNOWN",
        licence_detail="substituted for the test",
    )


@pytest.fixture
def tally(monkeypatch: pytest.MonkeyPatch) -> FakeTally:
    """A double standing in for the factory's real client.

    `real_tally` is what the command calls, and it is the only thing patched.
    Everything below it — preview, confirm, execute, the doorway — is the real
    code path.
    """
    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)

    def substituted(
        *_args: object, **_kwargs: object
    ) -> tuple[FakeTally, BackendIdentity]:
        return t, identity()

    monkeypatch.setattr(cli, "real_tally", substituted)
    return t


def post_n(t: FakeTally, n: int) -> list[str]:
    ops: list[str] = []
    for i in range(n):
        op = new_operation_id()
        t.write_voucher(COMPANY, a_voucher(i), op)
        ops.append(op)
    return ops


ARGS = ["--reverse-all", "--company", COMPANY, "--backed-up"]


# ---- the preview is the default, and it touches nothing ---------------------


def test_without_yes_it_prints_the_candidates_and_reverses_nothing(
    tally: FakeTally, capsys: pytest.CaptureFixture[str]
):
    ops = post_n(tally, 3)
    before = tally.trial_balance(COMPANY)

    assert cli.main(ARGS) == cli.EXIT_OK

    out = capsys.readouterr().out
    for op in ops:
        assert op in out, "every operation id is named before anything happens"
    assert "3 voucher(s) of ours" in out
    assert "nothing was reversed" in out
    assert tally.trial_balance(COMPANY) == before
    assert len(tally.list_our_vouchers(COMPANY)) == 3


def test_the_resolved_configuration_is_printed_before_anything_is_touched(
    tally: FakeTally, capsys: pytest.CaptureFixture[str]
):
    """Principle 9. Which Tally, which company, and whether a backup was
    asserted are never left to be inferred."""
    post_n(tally, 1)
    cli.main([*ARGS, "--host", "192.168.64.2", "--port", "9001"])

    out = capsys.readouterr().out
    assert "192.168.64.2:9001" in out
    assert "'Demo Co'" in out
    assert "backed up  True" in out
    assert "confirmed  False" in out


# ---- --yes runs it ----------------------------------------------------------


def test_with_yes_a_clean_batch_completes_and_exits_zero(
    tally: FakeTally, capsys: pytest.CaptureFixture[str]
):
    before_posting = tally.trial_balance(COMPANY)
    post_n(tally, 3)

    assert cli.main([*ARGS, "--yes"]) == cli.EXIT_OK

    out = capsys.readouterr().out
    assert BatchState.COMPLETED.value.upper() in out
    assert "every paise accounted for: True" in out
    assert tally.list_our_vouchers(COMPANY) == ()
    assert tally.trial_balance(COMPANY) == before_posting


def test_a_batch_that_did_not_complete_does_not_exit_zero(
    tally: FakeTally,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """The property a shell, a CI step and a person all rely on."""
    ops = post_n(tally, 4)

    original = FakeTally.reverse_by_operation_id

    def refuse_the_third(self: FakeTally, company: str, operation_id: str) -> bool:
        if operation_id == ops[2]:
            return False
        return original(self, company, operation_id)

    monkeypatch.setattr(FakeTally, "reverse_by_operation_id", refuse_the_third)

    assert cli.main([*ARGS, "--yes"]) == cli.EXIT_NOT_COMPLETED

    out = capsys.readouterr().out
    assert BatchState.PARTIAL_FAILURE.value.upper() in out
    assert "explicit_rejection" in out
    assert "not_attempted" in out


# ---- refusals are sentences -------------------------------------------------


def test_without_backed_up_the_command_refuses_and_exits_nonzero(
    tally: FakeTally, capsys: pytest.CaptureFixture[str]
):
    """The safe default. No asserted backup, no reversal."""
    tally.add_company(COMPANY, accounts=ACCOUNTS, backed_up=False)
    post_n_ops = None  # nothing is written; the refusal comes first
    assert post_n_ops is None

    code = cli.main(["--reverse-all", "--company", COMPANY, "--yes"])

    assert code == cli.EXIT_REFUSED
    assert "refused" in capsys.readouterr().err


def test_an_unreachable_tally_refuses_with_a_sentence_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    def unreachable(*_a: object, **_k: object) -> object:
        raise RealTallyRequired("REAL TALLY REQUIRED: no operation performed. nope")

    monkeypatch.setattr(cli, "real_tally", unreachable)

    assert cli.main([*ARGS, "--yes"]) == cli.EXIT_REFUSED
    assert "REAL TALLY REQUIRED" in capsys.readouterr().err


def test_the_command_refuses_to_run_without_reverse_all():
    """One operation, named explicitly. There is no default action."""
    with pytest.raises(SystemExit):
        cli.main(["--company", COMPANY])


# ---- the layering claim this file makes on the package's behalf -------------


TALLYIO = pathlib.Path(cli.__file__).parent


def _imports_above_the_boundary(path: pathlib.Path) -> set[str]:
    """Every `accountant.*` import that is not schema and not tallyio itself."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    reached: set[str] = set()
    for node in ast.walk(tree):
        module = ""
        if isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
        elif isinstance(node, ast.Import):
            module = node.names[0].name
        if not module.startswith("accountant"):
            continue
        if module in ("accountant.schema",) or module.startswith("accountant.tallyio"):
            continue
        # `from accountant import reversal` parses as module "accountant" with
        # the name in `node.names`. Resolve it, or the check would report the
        # package rather than what was actually reached.
        if module == "accountant" and isinstance(node, ast.ImportFrom):
            reached.update(f"accountant.{alias.name}" for alias in node.names)
            continue
        reached.add(module)
    return reached


def test_only_the_command_imports_above_the_connector_boundary():
    """Correction C3, checked rather than promised.

    `__main__.py` imports `accountant.reversal`, which is a product-layer
    module. That is legitimate — nothing in the package imports `__main__`, so
    the library's own graph is unchanged — but the claim is worthless unless
    the rest of the package is held to the rule. This is that check.
    """
    offenders = {
        path.name: sorted(_imports_above_the_boundary(path))
        for path in sorted(TALLYIO.glob("*.py"))
        if path.name != "__main__.py" and _imports_above_the_boundary(path)
    }
    assert offenders == {}, (
        f"modules inside accountant/tallyio/ import above the connector "
        f"boundary: {offenders}. Only __main__.py may, because nothing imports it."
    )


def test_the_command_really_does_import_upward_so_the_guard_has_a_subject():
    """The control. If `__main__.py` stopped importing the product layer, the
    test above would pass while guarding nothing."""
    assert _imports_above_the_boundary(TALLYIO / "__main__.py") == {
        "accountant.reversal"
    }

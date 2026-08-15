"""The cage on the live path: what it may do to an outcome, and what it may not.

WHAT IS BEING PROVED HERE
-------------------------
`pipeline.evaluate` runs the deterministic decision order and then asks the
cage. The cage is ADVISORY, and an advisory layer may only ever NARROW:

    existing VALID + cage POST   -> VALID
    existing VALID + cage ASK    -> UNCLEAR
    existing VALID + cage BLOCK  -> NOT_VALID
    existing UNCLEAR             -> UNCHANGED, whatever the cage says
    existing NOT_VALID           -> UNCHANGED, whatever the cage says

`docs/ARCHITECTURE.md` section 19.4: an advisory layer is ADDED to a
deterministic one and never SUBTRACTS from it. The two rows that matter most
are the last two - a cage POST arriving over a refusal must not become a post -
and those two are the controls.

WHY THE TABLE IS TESTED WHERE IT LIVES AND NOT ONLY THROUGH `evaluate`
-----------------------------------------------------------------------
`evaluate` cannot produce a cage POST at all today. It passes no `net_paise`,
so `net_plus_tax_equals_gross` comes back INDETERMINATE on every bill and
`_conservation_blocks` refuses it. Measured across the whole suite on
2026-08-13, before any of this was wired: 993 drafts evaluated, 993 cage
BLOCKs, zero POSTs and zero ASKs. Three rows of the table tested only through
`evaluate` would be three rows nothing could reach, which is the vacuous-guard
shape this repository has already shipped once.

So the table is tested directly, and the WIRING is proved separately: an AST
guard that `evaluate` calls the gate, with two controls, and an end-to-end test
that a draft the decision order called VALID now comes out NOT_VALID carrying
the cage's own sentence.

NO NETWORK, NO IO beyond reading this repository's own source.
"""

from __future__ import annotations

import ast
import datetime
import pathlib

from accountant import pipeline
from accountant.cage.decision import Action, Decided
from accountant.cage.wall import DECIDING_MODULE, LedgerEntry
from accountant.extract.adapter import TypedTextExtractor
from accountant.memory.bootstrap import bootstrap
from accountant.memory.store import MemoryStore
from accountant.pipeline import narrowed_by_the_cage
from accountant.schema import Decision, Outcome, Voucher
from accountant.tallyio.fake import FakeTally

PIPELINE = pathlib.Path(pipeline.__file__)

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Sundry Expenses", "Cash")
TODAY = datetime.date(2026, 8, 7)

#: The reason a decision from the deterministic order carries when it is happy.
#: Named so the assertions below can say "this is NOT what the person reads now"
#: without repeating the string in five places.
NOTHING_UNCLEAR = "nothing unclear and nothing surprising"


def existing(outcome: Outcome) -> Decision:
    """A decision as the deterministic order would hand it over."""
    return Decision(
        outcome=outcome,
        reason=NOTHING_UNCLEAR if outcome is Outcome.VALID else "something else",
        operation_id="op-1",
    )


def cage_says(action: Action) -> Decided:
    """What the cage hands back. A POST carries the write, and only a POST can."""
    entry = (
        LedgerEntry.decided(
            DECIDING_MODULE,
            party="Sharma Traders",
            amount_paise=420_000,
            debit_account="Purchases",
            credit_account="Cash",
        )
        if action is Action.POST
        else None
    )
    return Decided(
        action=action,
        said=f"the cage said {action.value} in plain words.",
        reasons=(f"the cage said {action.value} in plain words.",),
        entry=entry,
    )


# =============================================================================
# THE TABLE - one test per row, named for its row
# =============================================================================


def test_valid_plus_cage_post_stays_valid() -> None:
    """Row 1. The cage agreeing changes nothing at all - not the outcome and
    not the sentence. Returning a rebuilt decision here would be harmless today
    and would quietly overwrite `reason` the day the cage's POST wording
    changed, so the SAME object comes back."""
    was = existing(Outcome.VALID)
    now = narrowed_by_the_cage(was, cage_says(Action.POST))
    assert now.outcome is Outcome.VALID
    assert now is was
    assert now.reason == NOTHING_UNCLEAR


def test_valid_plus_cage_ask_becomes_unclear() -> None:
    """Row 2. A bill the deterministic order was happy with, that the cage
    wants a person to look at, is UNCLEAR - and the sentence is the CAGE'S,
    because the cage is what refused. Leaving "nothing unclear and nothing
    surprising" on a screen that says "needs an answer" tells the person the
    system does not know why it stopped."""
    now = narrowed_by_the_cage(existing(Outcome.VALID), cage_says(Action.ASK))
    assert now.outcome is Outcome.UNCLEAR
    assert now.reason == "the cage said ask in plain words."
    assert NOTHING_UNCLEAR not in now.reason


def test_valid_plus_cage_block_becomes_not_valid() -> None:
    """Row 3. A refusal, in the cage's own words, and the operation id survives
    - a decision that loses its identity authorises nothing and cannot be
    joined to the entry it decided (`Decision.post`, G5.1)."""
    now = narrowed_by_the_cage(existing(Outcome.VALID), cage_says(Action.BLOCK))
    assert now.outcome is Outcome.NOT_VALID
    assert now.reason == "the cage said block in plain words."
    assert now.operation_id == "op-1"


def test_unclear_plus_cage_post_stays_unclear() -> None:
    """Row 4, THE CONTROL. The cage is allowed to narrow and never to widen. A
    POST arriving over an UNCLEAR must not promote it, or an advisory layer
    would be overruling the deterministic one - which is the opposite of what
    it is for."""
    was = existing(Outcome.UNCLEAR)
    now = narrowed_by_the_cage(was, cage_says(Action.POST))
    assert now.outcome is Outcome.UNCLEAR
    assert now is was


def test_not_valid_plus_cage_post_stays_not_valid() -> None:
    """Row 5, THE SAME CONTROL AND HARDER. This is the one that would put a
    refused bill into somebody's books. `Decision.post` is the only thing the
    write path reads, so it is asserted here as well as the outcome: the two
    are read by different callers and only one of them stops a write."""
    was = existing(Outcome.NOT_VALID)
    now = narrowed_by_the_cage(was, cage_says(Action.POST))
    assert now.outcome is Outcome.NOT_VALID
    assert now is was
    assert now.post is False


def test_the_table_names_every_action_the_cage_can_return() -> None:
    """A `KeyError` in front of somebody's bill is an outage where a refusal is
    a product. Rather than a run-time default that would hide a missing row,
    the table is required to be total over `Action` HERE, where adding a fourth
    action fails at build time and whoever adds it reads this."""
    assert set(pipeline.CAGE_NARROWS) == set(Action)
    assert set(pipeline.CAGE_NARROWS.values()) <= {
        Outcome.VALID,
        Outcome.UNCLEAR,
        Outcome.NOT_VALID,
    }


# =============================================================================
# THE WIRING - the gate is actually called, and it is called from `evaluate`
# =============================================================================


def _local_names_for_the_cage_gate(tree: ast.Module) -> set[str]:
    """Every local name in this module that IS `cage.gate.gate`.

    Two import forms, which are the two that exist:
    `from accountant.cage.gate import gate [as x]`, and
    `from accountant.cage import gate [as x]` followed by `x.gate(...)`.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module == "accountant.cage.gate":
            names |= {a.asname or a.name for a in node.names if a.name == "gate"}
        if node.module == "accountant.cage":
            names |= {
                f"{a.asname or a.name}.gate" for a in node.names if a.name == "gate"
            }
    return names


def _function_named(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _calls_the_gate_inside(source: str, function: str) -> bool:
    """Does the named function CALL the cage's `gate`?

    Scoped to ONE function body, not to the module, because "the pipeline
    imports the gate somewhere" is not the fact being protected - the fact is
    that the one function which gives a draft its decision asks the cage before
    handing it back. An import with no call, or a call from a helper nothing
    reaches, would both satisfy a module-wide scan.
    """
    tree = ast.parse(source)
    known = _local_names_for_the_cage_gate(tree)
    body = _function_named(tree, function)
    if body is None:
        return False
    for node in ast.walk(body):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name) and target.id in known:
            return True
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and f"{target.value.id}.{target.attr}" in known
        ):
            return True
    return False


def test_evaluate_asks_the_cage_before_it_hands_the_draft_back() -> None:
    """A unit test of the table proves the table works and says nothing about
    whether it is installed - defect J1, recorded in `wall.py`. This is the
    other half: the call is in `evaluate`'s own body, where every one of the
    three runtime callers reaches it."""
    source = PIPELINE.read_text(encoding="utf-8")
    assert _function_named(ast.parse(source), "evaluate") is not None
    assert _calls_the_gate_inside(source, "evaluate")


def test_the_control_the_scanner_can_find_a_call_at_all() -> None:
    """THE CONTROL, and it is not decoration. This build has already shipped an
    AST guard that asserted over an empty set and passed, because it scanned
    for a construction the code did not contain. Both import forms."""
    assert _calls_the_gate_inside(
        "from accountant.cage.gate import gate\ndef evaluate():\n    gate(d)\n",
        "evaluate",
    )
    assert _calls_the_gate_inside(
        "from accountant.cage import gate\ndef evaluate():\n    gate.gate(d)\n",
        "evaluate",
    )


def test_the_control_the_scanner_is_not_fooled_by_a_call_somewhere_else() -> None:
    """The scope is the whole of the guard. A call in a neighbouring function,
    a bare import, and an unrelated `gate` must all read as absent - otherwise
    the guard passes on a pipeline that imports the cage and never asks it."""
    imported = "from accountant.cage.gate import gate\n"
    assert not _calls_the_gate_inside(
        imported + "def evaluate():\n    pass\ndef other():\n    gate(d)\n", "evaluate"
    )
    assert not _calls_the_gate_inside(
        imported + "def evaluate():\n    pass\n", "evaluate"
    )
    assert not _calls_the_gate_inside(
        "from accountant.web import gate\ndef evaluate():\n    gate.gate(d)\n",
        "evaluate",
    )


# =============================================================================
# END TO END - what the live path actually does now
# =============================================================================


def _known_vendor() -> tuple[Voucher, ...]:
    """Enough history that the decision order has nothing left to ask about.

    A bill from an UNSEEN vendor is UNCLEAR before the cage is asked, and the
    cage may not change an UNCLEAR - so a test built on one would pass without
    the cage being wired at all. This is the fixture that makes the assertion
    below capable of failing.
    """
    return tuple(
        Voucher(
            id=f"hist-{i}",
            date=datetime.date(2026, 1, 1),
            party="Sharma Traders",
            narration="Sharma Traders purchase",
            debit_account="Purchases",
            credit_account="Cash",
            amount_paise=100_000,
        )
        for i in range(40)
    )


def _evaluated(text: str):  # type: ignore[no-untyped-def]
    """One typed bill, through the real pipeline, with no period looked up.

    `period_open=None` is what every shipped caller passes, because nothing in
    this repository reads the period. It blocks, and blocking is the correct
    answer to "nobody looked".
    """
    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, vouchers=_known_vendor(), backed_up=True)
    memory = bootstrap(t, COMPANY, MemoryStore(":memory:"))
    draft = pipeline.build_draft(
        COMPANY, text.encode(), "text/plain", TypedTextExtractor(), memory, today=TODAY
    )
    return pipeline.evaluate(
        draft,
        ACCOUNTS,
        t.read_vouchers(COMPANY),
        memory,
        period_open=None,
        pdf_repaired=None,
    )


def test_the_sentence_a_person_reads_comes_from_the_cage_when_the_cage_refused() -> (
    None
):
    """The measured consequence of wiring this, stated as a test rather than
    left in a report: a bill that reaches `evaluate` is refused by the cage,
    and what the person reads is the cage's plain sentence and not the
    deterministic order's."""
    d = _evaluated("paid Sharma Traders 4200 for cement")
    # THE CONTROL. An empty problem list is what `decide_problems` turns into
    # VALID, so this pins that the deterministic order had nothing to say and
    # the refusal below came from the cage and from nowhere else. Without it the
    # test passes on any draft that was already refused, cage or no cage.
    assert d.problems == []
    assert d.outcome is Outcome.NOT_VALID
    assert d.reason != NOTHING_UNCLEAR
    assert "could not tell whether the books for this date are still open" in d.reason


def test_evaluate_refuses_a_caller_that_did_not_say_whether_the_period_is_open() -> (
    None
):
    """No default, for the reason `cage/gate.py` gives at length: a default is
    supplied silently at every call site that forgot, and the dangerous one
    here is `True`. Forgetting is a `TypeError` in a test run, not a post.

    THE KEYWORDS ARE OMITTED AND THAT IS THE WHOLE TEST. Written first with
    `period_open=None, pdf_repaired=None` passed in, it proved nothing: the
    signature was satisfied, `evaluate` ran on, and the `TypeError` this
    asserted was really the `AttributeError` an `object()` memory raises four
    lines later. A test that passes for the wrong reason on a plain `object()`
    would have gone on passing after a default was added, which is the one
    thing it exists to catch."""
    import pytest

    with pytest.raises(TypeError):
        pipeline.evaluate(object(), (), (), object())  # type: ignore[call-arg]

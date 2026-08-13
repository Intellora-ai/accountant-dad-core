"""The adapter between a `Draft` and the cage, and the guard that it is the only one.

WHAT IS BEING PROVED HERE
-------------------------
`accountant/cage/gate.py` carries facts and decides nothing. Three things have
to hold, and each of them has its own failure:

    it invents nothing        every field it reports as read was read, and
                              every world fact came from the caller
    it never guesses a fact   `period_open`, `party_known` and `carries_gst`
                              have no defaults, and `None` blocks
    it is the only door       nothing else in `accountant/` calls
                              `cage.decision.decide`, proved by an AST scan

WHY THE AST SCAN NEEDS A CONTROL
---------------------------------
Defect J1, and this build has already been bitten by it once. When `wall.py`
was written, its guard scanned for `LedgerEntry(` while the constructor built
its result with `cls(...)`. The scan saw nothing, the assertion ran over an
EMPTY SET, and it passed - and would have kept passing after the wall was
deleted. A control test that proves the scanner can find a call at all is the
only thing that catches that, so there are three of them here: one proving the
scanner sees a real call, one proving it is not fooled by the OTHER function in
this repository called `decide`, and one proving it walked a real directory.

WHAT THIS FILE DOES NOT PROVE
------------------------------
That the gate is on the live posting path. It is not, deliberately, and
`test_the_gate_is_not_yet_on_the_live_pipeline_path_and_this_records_why`
records the reason structurally so that wiring it fails here first.

NO NETWORK, NO IO beyond reading this repository's own source.
"""

from __future__ import annotations

import ast
import datetime
import pathlib
from typing import cast

import pytest

from accountant.cage.confidence import EXACT
from accountant.cage.decision import GST_IS_OFF, Action, Decided
from accountant.cage.gate import gate, observed
from accountant.extract.adapter import NOT_FOUND, ExtractedRecord, LineItem
from accountant.pipeline import Draft
from accountant.schema import Voucher

ACCOUNTANT = pathlib.Path(__file__).resolve().parent.parent / "accountant"

TOTAL = 420_000
TAX = 0
NET = TOTAL - TAX
BEFORE = 1_000_000
AFTER = BEFORE + TOTAL
DATE = datetime.date(2026, 8, 12)
PARTY = "Sharma Traders"
READ_BY = "test_reader"


def a_record(
    *,
    date: datetime.date | None = DATE,
    party: str | None = PARTY,
    total_paise: object = TOTAL,
    tax_paise: object = TAX,
    line_items: tuple[LineItem, ...] = (LineItem("cement", TOTAL),),
    sources: dict[str, str] | None = None,
) -> ExtractedRecord:
    """A record that read everything, unless a test says otherwise."""
    read: dict[str, object] = {
        "date": date,
        "party": party,
        "total_paise": total_paise,
        "tax_paise": tax_paise,
    }
    src = {
        name: READ_BY if value is not None else f"{NOT_FOUND}: not on this bill"
        for name, value in read.items()
    }
    src.update(sources or {})
    return ExtractedRecord(
        date=date,
        party=party,
        total_paise=cast("int | None", total_paise),
        tax_paise=cast("int | None", tax_paise),
        line_items=line_items,
        raw_text="paid Sharma Traders 4200 for cement",
        backend="test",
        per_field_source=src,
    )


def a_draft(
    record: ExtractedRecord | None = None,
    *,
    debit: str = "Purchases",
    credit: str = "Cash",
) -> Draft:
    seen = record if record is not None else a_record()
    voucher = Voucher(
        id="draft-test",
        date=seen.date or DATE,
        party=seen.party or "",
        narration=seen.raw_text,
        debit_account=debit,
        credit_account=credit,
        amount_paise=seen.total_paise or 0,
        provenance=dict(seen.per_field_source),
    )
    return Draft(
        id=voucher.id,
        company="Demo Co",
        voucher=voucher,
        record=seen,
        operation_id="op-test",
    )


def asked(
    draft: Draft,
    *,
    party_known: bool | None = True,
    period_open: bool | None = True,
    carries_gst: bool | None = False,
    questions_asked: int = 0,
    net_paise: int | None = NET,
    balance_before_paise: int | None = BEFORE,
    balance_after_paise: int | None = AFTER,
    ambiguous_fields: tuple[str, ...] = (),
) -> Decided:
    """Every fact supplied and every law holding, so a test overrides ONE thing.

    The defaults live here and not in `gate`, which is the whole point: a test
    that wants a posted bill has to say the books are open, and the module
    under test still has no way to say it on anybody's behalf.
    """
    return gate(
        draft,
        party_known=party_known,
        period_open=period_open,
        carries_gst=carries_gst,
        questions_asked=questions_asked,
        net_paise=net_paise,
        balance_before_paise=balance_before_paise,
        balance_after_paise=balance_after_paise,
        ambiguous_fields=ambiguous_fields,
    )


# ---- the observation: what was read, and nothing else -----------------------


def test_a_field_the_record_read_is_scored_exact_and_keeps_its_source() -> None:
    """The typed-text path reads a text layer. There is no pixel and no
    estimate, so there is nothing to be unsure about."""
    seen = observed(a_draft())
    assert seen.total_paise.value == TOTAL
    assert seen.total_paise.confidence == EXACT
    assert seen.total_paise.source == READ_BY


def test_a_field_the_record_did_not_read_scores_zero_and_says_why() -> None:
    """Not reading something and being unsure about it are the same fact, and
    the source has to survive so the person can be told which one it was."""
    seen = observed(a_draft(a_record(party=None)))
    assert seen.party.value is None
    assert seen.party.confidence == 0.0
    assert NOT_FOUND in seen.party.source


def test_the_control_a_record_that_read_everything_scores_one() -> None:
    """THE CONTROL on the two above. A builder that scored every field 0.0
    would pass the unread test and fail this one."""
    assert observed(a_draft()).lowest_confidence == EXACT


def test_an_amount_that_is_not_whole_paise_is_left_unread() -> None:
    """Money is never a float. It is not coerced and it is not raised on - the
    field is unread, and the entry blocks with a sentence."""
    seen = observed(a_draft(a_record(total_paise=4200.0)))
    assert seen.total_paise.value is None
    assert seen.total_paise.confidence == 0.0
    assert "float" in seen.total_paise.source


def test_a_flag_where_an_amount_belongs_is_left_unread() -> None:
    """`isinstance(True, int)` is True and `True == 1`, so a flag passed where
    an amount belonged would otherwise balance a one-paisa entry."""
    seen = observed(a_draft(a_record(total_paise=True)))
    assert seen.total_paise.value is None


def test_a_float_amount_blocks_rather_than_raising() -> None:
    """A refusal a person can read is a product; a stack trace is an outage."""
    decided = asked(a_draft(a_record(total_paise=4200.0)))
    assert decided.action is Action.BLOCK
    assert decided.said.strip()


def test_a_field_with_a_blank_source_is_unread_and_still_says_something() -> None:
    """`Field` refuses an empty source, so a record that states a blank one
    would turn into a ValueError on its way to a person's screen."""
    seen = observed(a_draft(a_record(sources={"date": "   "})))
    assert seen.date.value is None
    assert seen.date.source.strip()


# ---- the three world facts: never derived, never defaulted ------------------


def test_the_gate_refuses_a_caller_that_omits_a_world_fact() -> None:
    """A default of `period_open=True` would be a fact nobody checked wearing
    the costume of one, supplied silently at every call site that forgot."""
    with pytest.raises(TypeError):
        gate(  # pyright: ignore[reportCallIssue]
            a_draft(),
            party_known=True,
            carries_gst=False,
            questions_asked=0,
        )


def test_a_period_nobody_checked_blocks_and_says_nobody_checked() -> None:
    decided = asked(a_draft(), period_open=None)
    assert decided.action is Action.BLOCK
    assert "could not tell whether the books" in decided.said


def test_a_closed_period_blocks_with_a_different_sentence() -> None:
    """The books being shut and nobody having looked are different facts, and
    the person reading the refusal needs to know which one they got."""
    decided = asked(a_draft(), period_open=False)
    assert decided.action is Action.BLOCK
    assert "closed" in decided.said
    assert "could not tell whether the books" not in decided.said


def test_the_gate_never_reads_the_period_off_the_date() -> None:
    """THE CONTROL on the rule that matters most.

    An Indian financial year runs 1 April to 31 March, so a date of
    2026-08-12 is inside the open year and it is tempting to compute
    `period_open` from it. That is an inference wearing the costume of a fact.
    Nothing in this repository reads the year bounds, so nobody looked, and
    nobody-looked blocks.
    """
    inside_the_year = a_draft(a_record(date=datetime.date(2026, 8, 12)))
    assert asked(inside_the_year, period_open=None).action is Action.BLOCK


def test_a_party_the_books_do_not_know_blocks_and_invents_no_name() -> None:
    decided = asked(a_draft(), party_known=False)
    assert decided.action is Action.BLOCK
    assert "never add a new name" in decided.said


def test_a_party_nobody_looked_up_blocks() -> None:
    decided = asked(a_draft(), party_known=None)
    assert decided.action is Action.BLOCK
    assert "Nobody checked whether this name" in decided.said


def test_a_bill_that_carries_gst_blocks_with_the_owners_own_sentence() -> None:
    decided = asked(a_draft(), carries_gst=True)
    assert decided.action is Action.BLOCK
    assert GST_IS_OFF in decided.said


def test_gst_nobody_checked_blocks_too() -> None:
    decided = asked(a_draft(), carries_gst=None)
    assert decided.action is Action.BLOCK
    assert "could not tell whether there is tax" in decided.said


def test_the_control_the_gst_fact_is_the_callers_and_never_the_records() -> None:
    """THE CONTROL on the fact above, and the sharpest one available: a record
    carrying a tax figure of 75,000 paise still posts when the CALLER says the
    bill carries no GST. A gate that read the fact off the record would refuse
    this, which is how you tell the two apart."""
    record = a_record(tax_paise=75_000)
    decided = asked(a_draft(record), carries_gst=False, net_paise=TOTAL - 75_000)
    assert decided.action is Action.POST


# ---- the bands: post, ask, block --------------------------------------------


def test_a_bill_with_every_fact_supplied_and_every_law_holding_posts() -> None:
    decided = asked(a_draft())
    assert decided.action is Action.POST
    assert decided.entry is not None
    assert decided.entry.amount_paise == TOTAL
    assert decided.entry.party == PARTY


def test_a_bill_whose_lines_do_not_add_up_is_asked_about() -> None:
    record = a_record(line_items=(LineItem("cement", 410_000),))
    decided = asked(a_draft(record))
    assert decided.action is Action.ASK
    assert decided.entry is None


def test_a_failing_law_at_full_confidence_does_not_post() -> None:
    """THE SINGLE BEHAVIOUR THE CAGE EXISTS FOR.

    A confidence score is a statement about how legible some pixels were. A
    conservation law is a statement about whether numbers agree. They are not
    on the same scale and they do not trade off.
    """
    draft = a_draft(a_record(line_items=(LineItem("cement", 410_000),)))
    assert observed(draft).lowest_confidence == EXACT
    assert asked(draft).action is not Action.POST


def test_a_bill_with_a_field_nobody_could_read_is_blocked() -> None:
    decided = asked(a_draft(a_record(date=None)))
    assert decided.action is Action.BLOCK


def test_only_a_posted_decision_carries_something_writable() -> None:
    """A blocked decision holding a writable entry is one careless attribute
    access away from posting the thing we just refused."""
    assert asked(a_draft(), period_open=False).entry is None
    assert asked(a_draft(), party_known=False).entry is None


def test_a_field_the_caller_calls_ambiguous_turns_a_post_into_a_question() -> None:
    decided = asked(a_draft(), ambiguous_fields=("date",))
    assert decided.action is Action.ASK
    assert "more than one way" in decided.said


def test_the_question_budget_is_the_callers_count_and_never_a_guess() -> None:
    """A product that will not take no for an answer is worse than one that
    hands the entry back."""
    decided = asked(a_draft(), questions_asked=5)
    assert decided.action is Action.BLOCK
    assert "all I am allowed" in decided.said


# ---- the conservation mapping: None and () are different --------------------


def test_line_items_no_reader_produced_are_unread_not_none_of_them() -> None:
    """An empty tuple means the lines were READ and there were none, which is
    consistent with a zero total and contradictory with any other. No reader in
    this repository can report line items at all, so an empty tuple here means
    nobody looked, and nobody-looked cannot be checked."""
    decided = asked(a_draft(a_record(line_items=())))
    assert decided.action is Action.BLOCK
    assert "could not check at all" in decided.said


def test_the_control_lines_that_were_read_are_checked_against_the_total() -> None:
    """THE CONTROL: the same call with lines present does not report an
    unchecked law, so the block above is about the lines and nothing else."""
    assert "could not check at all" not in asked(a_draft()).said


def test_a_balance_nobody_read_blocks_because_the_law_could_not_run() -> None:
    decided = asked(a_draft(), balance_after_paise=None)
    assert decided.action is Action.BLOCK
    assert "could not check at all" in decided.said


def test_a_net_amount_nobody_read_blocks_and_is_never_worked_out() -> None:
    """Total minus tax is arithmetic on two numbers this module already has, so
    a law checked against it would be checking a number against itself and
    would pass on every bill for ever."""
    decided = asked(a_draft(), net_paise=None)
    assert decided.action is Action.BLOCK
    assert "could not check at all" in decided.said


def test_a_caller_amount_that_is_not_paise_blocks_rather_than_raising() -> None:
    """`conservation._paise` raises on a float. The gate hands it whole paise or
    nothing, so a caller's mistake is a refusal rather than a traceback."""
    decided = asked(a_draft(), net_paise=cast("int", 4200.0))
    assert decided.action is Action.BLOCK


def test_a_bill_whose_books_moved_by_the_wrong_amount_is_asked_about() -> None:
    decided = asked(a_draft(), balance_after_paise=BEFORE + 1)
    assert decided.action is Action.ASK
    assert "do not add up" in decided.said


# ---- the static half: is this the only door? --------------------------------


def _local_names_for_the_cage_decision(tree: ast.Module) -> set[str]:
    """Every local name in this module that IS `cage.decision.decide`.

    Two import forms, which are the two that exist:
    `from accountant.cage.decision import decide [as x]`, and
    `from accountant.cage import decision` followed by `decision.decide(...)`.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module == "accountant.cage.decision":
            names |= {a.asname or a.name for a in node.names if a.name == "decide"}
        if node.module == "accountant.cage":
            names |= {
                f"{a.asname or a.name}.decide"
                for a in node.names
                if a.name == "decision"
            }
    return names


def _calls_the_cage_decision(source: str) -> bool:
    """Does this source CALL the cage's `decide`?

    An AST walk and not a substring search: a mention in a docstring or a
    comment is not a call, and this repository names the function in prose
    often. It is also why the import is read - `accountant/decide.py` exports a
    different function of the same name, and a scanner that matched on the bare
    word would report it as the cage being called from the pipeline.
    """
    tree = ast.parse(source)
    known = _local_names_for_the_cage_decision(tree)
    for node in ast.walk(tree):
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


def _modules_scanned() -> list[pathlib.Path]:
    return sorted(ACCOUNTANT.rglob("*.py"))


def _modules_calling_the_cage_decision() -> set[str]:
    found: set[str] = set()
    for path in _modules_scanned():
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - unreadable file fails elsewhere
            continue
        if _calls_the_cage_decision(source):
            found.add(str(path.relative_to(ACCOUNTANT.parent)))
    return found


def test_the_gate_is_the_only_module_that_asks_the_cage_to_decide() -> None:
    """`wall.py` answers WHO may build a write. This answers WHO may ask for
    one. Equality and not a subset: a second caller of `decide` is a second
    place where a `Situation` is assembled, and the two would drift into two
    different definitions of what facts a decision needs."""
    assert _modules_calling_the_cage_decision() == {"accountant/cage/gate.py"}


def test_the_control_the_scanner_can_actually_find_a_call() -> None:
    """THE CONTROL, and it is not decoration. When `wall.py`'s guard was
    written it scanned for a construction the code did not contain, so the
    assertion ran over an empty set and passed - and would have kept passing
    after the wall was deleted."""
    assert _calls_the_cage_decision(
        "from accountant.cage.decision import decide\ndecide(situation)\n"
    )
    assert _calls_the_cage_decision(
        "from accountant.cage import decision\ndecision.decide(situation)\n"
    )


def test_the_control_the_scanner_is_not_fooled_by_the_other_decide() -> None:
    """`accountant/decide.py` exports a function called `decide` as well. A
    scanner matching the bare word would report every caller of that one as a
    caller of the cage, and the guard above would then be measuring nothing."""
    assert not _calls_the_cage_decision(
        "from accountant.decide import decide\ndecide(checks, match, flags)\n"
    )
    assert not _calls_the_cage_decision('"""This module talks about decide()."""\n')


def test_the_control_the_scan_walked_a_real_directory() -> None:
    """A scan over an empty file list passes every assertion above it. This is
    what notices if `rglob` ever stops finding the package."""
    scanned = _modules_scanned()
    assert len(scanned) > 20
    assert ACCOUNTANT / "cage" / "gate.py" in scanned
    assert ACCOUNTANT / "pipeline.py" in scanned


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names |= {f"{node.module}.{a.name}" for a in node.names}
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
    return names


def test_the_gate_is_not_yet_on_the_live_pipeline_path_and_this_records_why() -> None:
    """THE SEQUENCING, asserted structurally so nobody has to find it in prose.

    The gate needs an `Observation` with a confidence per field. No reader
    produces one: `textlayer.py` and `freeocr.py` are Steps 13 and 14, and the
    typed-text path in `accountant/extract/adapter.py` reports a source per
    field and no score at all.

    Wired to `pipeline.run` today the gate does not ADD a guard, it SUBTRACTS a
    working path: measured on this branch, 50 passing tests go red, because
    `period_open` has no source anywhere in this repository, three of the four
    conservation laws have no inputs, and every one of those is a hard block.
    The path it would replace is already guarded by the eight checks in
    `checks.py`, the write-ahead row, and a write door that refuses an
    out-of-financial-year date with a plain sentence
    (`accountant/tallyio/errors.py`).

    `docs/OWNER_WORK.md` records what has to exist first. When it does, this
    test is what fails, and whoever wires it reads this before production does.
    """
    assert "accountant.cage.gate" not in _imported_modules(ACCOUNTANT / "pipeline.py")

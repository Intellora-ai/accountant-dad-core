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
from accountant.cage.decision import (
    AUTO_POST_ALLOWED_TIERS,
    GST_IS_OFF,
    Action,
    Decided,
    Moment,
)
from accountant.cage.gate import gate, observed
from accountant.extract.adapter import (
    INVOICE_SHAPED,
    NOT_FOUND,
    ExtractedRecord,
    LineItem,
)
from accountant.extract.textlayer import TextLayerReader
from accountant.pipeline import Draft
from accountant.schema import Voucher
from tests.test_textlayer import BILL, broken_startxref, pdf_bytes

ACCOUNTANT = pathlib.Path(__file__).resolve().parent.parent / "accountant"

TOTAL = 420_000
TAX = 0
NET = TOTAL - TAX
BEFORE = 1_000_000
AFTER = BEFORE + TOTAL
DATE = datetime.date(2026, 8, 12)
PARTY = "Sharma Traders"
#: CORRECTED 2026-08-13 BY OWNER DECISION 2, and it was `"test_reader"` - a tier
#: name no reader in this repository has ever stamped. That cost nothing while
#: nothing compared it to anything. It stopped costing nothing the moment
#: auto-post started asking WHICH reader read the bill: every builder below
#: describes a record that read everything cleanly, and every one of them was
#: refused, because a reader nobody has heard of is on no allowlist.
#:
#: Taken from the reader that stamps it rather than typed again, so a rename
#: there fails here loudly instead of quietly emptying the allowlist. A
#: hand-typed copy of a name that lives somewhere else is not a constant.
READ_BY = TextLayerReader.name

#: A tier that reads PIXELS, for the tests that need a record the owner has not
#: cleared to auto-post. Typed rather than imported, because pulling the picture
#: reader in would drag Pillow and pytesseract into a file that reads nothing -
#: and the property under test is that this string is NOT on the allowlist,
#: which a rename cannot silently satisfy the way an absent name could.
READ_BY_PICTURE = "free_ocr"


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
    moment: Moment = Moment.BEFORE_THE_WRITE,
    party_known: bool | None = True,
    period_open: bool | None = True,
    carries_gst: bool | None = False,
    pdf_repaired: bool | None = None,
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

    `moment` defaults to BEFORE_THE_WRITE because that is what the gate is for -
    it is asked whether to write, which can only be a question asked before
    writing. The two balance defaults are kept as they are so that overriding
    one is still the only thing a test changes; a test that wants the honest
    pre-write posture passes `balance_after_paise=None` and says so.
    """
    return gate(
        draft,
        moment=moment,
        party_known=party_known,
        period_open=period_open,
        carries_gst=carries_gst,
        pdf_repaired=pdf_repaired,
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
    """THE CONTROL on the fact above: the gate carries the caller's flag and
    never computes one off the record.

    REWRITTEN 2026-08-13, and the old version is worth stating because it
    asserted the opposite. It said a record carrying 75,000 paise of tax still
    POSTS when the caller says the bill carries no GST. It did, and that was a
    defect, not a feature: `decision.py` now compares the caller's flag against
    the tax figure on the reading it was handed, and a GST bill posted without
    its tax line is a wrong statutory entry in somebody's real books.

    What the control was FOR survives, and this puts it more sharply than a
    post ever did: ONE record, TWO callers, TWO DIFFERENT refusals. A gate that
    read the fact off the record would answer both of them the same way.
    """
    record = a_record(tax_paise=75_000)
    net = TOTAL - 75_000
    told_there_is_tax = asked(a_draft(record), carries_gst=True, net_paise=net)
    told_there_is_none = asked(a_draft(record), carries_gst=False, net_paise=net)

    assert GST_IS_OFF in told_there_is_tax.said
    assert GST_IS_OFF not in told_there_is_none.said
    assert "do not agree" in told_there_is_none.said


# ---- a repaired file is carried, never worked out here ----------------------


def test_a_repaired_file_is_confirmed_rather_than_posted_on_its_own() -> None:
    """Owner decision, 2026-08-13. Every fact supplied, every law holding, and
    the one thing different is that the bytes had to be mended before anything
    could be read off them. That caps the outcome at a question."""
    decided = asked(a_draft(), pdf_repaired=True)

    assert decided.action is Action.ASK
    assert decided.entry is None
    assert "repaired" in decided.said


def test_the_control_the_identical_bill_that_needed_no_repair_posts() -> None:
    """THE CONTROL. Without it the test above passes just as well against a gate
    that refuses everything, or against one that caps every bill it is handed."""
    assert asked(a_draft(), pdf_repaired=False).action is Action.POST
    assert asked(a_draft(), pdf_repaired=None).action is Action.POST


def test_the_gate_refuses_a_caller_that_does_not_say_whether_it_was_repaired() -> None:
    """Its own test rather than a line in the world-facts one, because the
    dangerous default is the opposite way round here: `None` means "nothing to
    repair" and POSTS, so a default would silently grant the permission the
    field exists to withhold. Only this argument is missing."""
    with pytest.raises(TypeError):
        gate(  # pyright: ignore[reportCallIssue]
            a_draft(),
            moment=Moment.BEFORE_THE_WRITE,
            party_known=True,
            period_open=True,
            carries_gst=False,
            questions_asked=0,
        )


def test_the_gate_never_reads_the_repair_off_the_draft() -> None:
    """THE CONTROL on the rule that this module carries facts and derives none.

    One draft, two callers, two different outcomes. A gate that worked the
    repair out from the record - or from anything on the `Draft` - would answer
    both of them the same way, and there is nothing on a `Draft` that could say:
    the repair happens in `accountant/extract/textlayer.py`, and what connects
    the two is the caller.
    """
    draft = a_draft()

    assert asked(draft, pdf_repaired=True).action is Action.ASK
    assert asked(draft, pdf_repaired=None).action is Action.POST


# ---- the bands: post, ask, block --------------------------------------------


def test_a_bill_with_every_fact_supplied_and_every_law_holding_posts() -> None:
    decided = asked(a_draft())
    assert decided.action is Action.POST
    assert decided.entry is not None
    assert decided.entry.amount_paise == TOTAL
    assert decided.entry.party == PARTY


# ---- the tier reaches the decision, and it comes off the record -------------
#
# Owner decision 2, 2026-08-13. `decision.decide` needs to know which reader
# read the bill and may not import one to find out, so `gate._tiers` reads it
# off `record.per_field_source` - the same dictionary the field sources come
# from. These three are what say the wire is connected: without them `_tiers`
# could return `()` for ever and only the demo would notice.


def test_the_allowlist_names_the_tier_the_shipped_reader_actually_stamps() -> None:
    """THE ANTI-DRIFT BIND, and it is the whole reason this test is in this file
    rather than beside the constant.

    `decision.py` cannot import a reader, so its allowlist holds a hand-typed
    `"pdf_text_layer"`. The owner wrote `text_layer`, which is not that string
    and not any other reader's either - so the failure mode this closes is real
    and was one keystroke away: an allowlist naming a tier nothing stamps
    refuses every bill in the product while looking exactly like a working
    guard, and no test of `decision.py` alone could tell the difference.
    """
    assert TextLayerReader.name in AUTO_POST_ALLOWED_TIERS
    assert READ_BY_PICTURE not in AUTO_POST_ALLOWED_TIERS


def test_a_bill_read_off_pixels_is_asked_about_rather_than_posted() -> None:
    """The same bill as the POST test above, every fact supplied and every law
    holding, differing in one thing: which reader read it.

    `test_a_bill_with_every_fact_supplied_and_every_law_holding_posts` is the
    control - it is the same call with the shipped text-layer tier on it, and
    it is what dies if `_tiers` starts refusing everything.
    """
    seen = a_record(sources=dict.fromkeys(ExtractedRecord.FIELDS, READ_BY_PICTURE))
    decided = asked(a_draft(seen))

    assert decided.action is Action.ASK
    assert decided.entry is None


def test_one_field_read_off_pixels_is_enough_to_stop_the_post() -> None:
    """A ladder record: the total off the text layer, the party off a
    photograph. `any` where `gate` and `decide` mean `all` would post a bill
    whose supplier name came out of an OCR guess, and the supplier name is the
    field whose cost is permanent."""
    seen = a_record(sources={"party": READ_BY_PICTURE})
    decided = asked(a_draft(seen))

    assert decided.action is Action.ASK
    assert decided.entry is None


def test_a_bill_whose_lines_do_not_add_up_is_refused() -> None:
    """CORRECTED AND RENAMED 2026-08-13. It was
    `test_a_bill_whose_lines_do_not_add_up_is_asked_about` and it asserted ASK.

    The owner closed that question on that date, this way round: "Conservation
    FAIL -> BLOCK, always. This is now a hard rule." A question about a bill
    whose own lines contradict its total spends one of five on something no
    answer can fix. Nothing was posted either way; what moved is the label and
    the sentence the person reads.
    """
    record = a_record(line_items=(LineItem("cement", 410_000),))
    decided = asked(a_draft(record))
    assert decided.action is Action.BLOCK
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


def test_before_the_write_a_balance_that_cannot_exist_yet_does_not_block() -> None:
    """RENAMED AND REVERSED 2026-08-13. This was
    `test_a_balance_nobody_read_blocks_because_the_law_could_not_run` and it
    asserted BLOCK on exactly this input.

    That assertion was wrong, and it was wrong about something specific.
    `balance_delta_equals_entry` compares the ledger balance before the entry
    with the balance after it. The gate is asked whether to WRITE, so there is
    no after-balance yet - not "nobody read it", but "it does not exist to be
    read". Blocking on it made a post unreachable except by handing the law a
    PREDICTED after-balance, which makes it compare a number against itself: a
    check that cannot fail wearing the face of a check that passed.

    The old assertion is not deleted. It is still true at the other moment, and
    it is asserted there - see the test directly below this one.
    """
    decided = asked(a_draft(), moment=Moment.BEFORE_THE_WRITE, balance_after_paise=None)
    assert decided.action is Action.POST
    assert "could not check at all" not in decided.said


def test_after_the_write_a_balance_nobody_read_blocks_because_nobody_looked() -> None:
    """The original assertion, kept, at the moment where it is still correct.

    After the write the balance IS knowable - the register can be read back - so
    "could not check" there does not mean "not yet", it means nobody looked, and
    a write nobody checked is the one failure this law exists to catch.
    """
    decided = asked(a_draft(), moment=Moment.AFTER_THE_WRITE, balance_after_paise=None)
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


def test_a_bill_whose_books_moved_by_the_wrong_amount_is_refused() -> None:
    """CORRECTED AND RENAMED 2026-08-13, the same way and on the same owner
    decision as the lines test above: a conservation FAIL is a hard rule now, so
    a balance that moved by the wrong amount blocks instead of asking.

    The sentence assertion is kept and is doing more work than it looks: the
    owner's own refusal is "The numbers in this bill do not add up", so the
    substring survives the reversal - which is exactly why the ACTION is
    asserted beside it rather than left to the words.
    """
    decided = asked(a_draft(), balance_after_paise=BEFORE + 1)
    assert decided.action is Action.BLOCK
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


# =============================================================================
# THE REPAIRED FLAG NEVER REACHED THE DECISION, MEASURED 2026-08-13
# =============================================================================
#
# `decision.py` caps a repaired PDF at ASK, and every test of that ceiling
# built the `Situation` by hand. Adversarial verification asked the question
# nobody had: does the fact ever GET there down a path the application ships?
#
# It did not. `TextLayerReader.extract` read `reading.pdf_repaired` and dropped
# it - `ExtractedRecord` had no such field - so a PDF whose object table was
# rebuilt produced a record with no trace of the repair in it: every source
# said `pdf_text_layer`, and `"repair" in repr(record).lower()` was False. The
# only shipped call site, `demo_safety_cage.py`, passes `pdf_repaired=None`,
# which is the value that GRANTS the full post. So the ceiling was real for a
# Situation a test constructed and for nothing else.
#
# The fact now travels with the evidence. A caller may still say True - another
# tier may know about a repair this one cannot see - but it can no longer say
# False or None over a record that says True, because the record was there.


def a_repaired_record() -> ExtractedRecord:
    """A record from a reader that had to mend the bytes to read them."""
    return ExtractedRecord(
        date=DATE,
        party=PARTY,
        total_paise=TOTAL,
        tax_paise=TAX,
        line_items=(LineItem("cement", TOTAL),),
        raw_text="paid Sharma Traders 4200 for cement",
        backend="pdf_text_layer",
        per_field_source=dict.fromkeys(ExtractedRecord.FIELDS, "pdf_text_layer"),
        pdf_repaired=True,
    )


def test_a_caller_who_never_looked_cannot_post_a_repaired_pdf() -> None:
    """`None` means "not a PDF, nothing to repair" and grants the full post.
    That is the right meaning and the wrong answer here, because the record
    knows better and was never asked."""
    decided = asked(a_draft(a_repaired_record()), pdf_repaired=None)

    assert decided.action is not Action.POST
    assert decided.entry is None


def test_a_caller_who_says_it_was_fine_cannot_talk_the_record_out_of_it() -> None:
    """The stronger half. A caller passing False over a record that says True
    is a caller contradicting the evidence, and the evidence wins."""
    decided = asked(a_draft(a_repaired_record()), pdf_repaired=False)

    assert decided.action is not Action.POST


def test_the_control_a_record_that_was_never_repaired_still_posts() -> None:
    """THE CONTROL, and the mandatory one. Reading the flag off the record must
    not become "cap everything at ASK", which would pass both tests above and
    would delete the posting path."""
    decided = asked(a_draft(), pdf_repaired=None)

    assert decided.action is Action.POST
    assert decided.entry is not None
    assert a_record().pdf_repaired is None


def test_the_caller_can_still_report_a_repair_the_record_knows_nothing_about() -> None:
    """The other control. The parameter is not decorative now that the record
    carries the fact: a tier that mended bytes this reader never saw is exactly
    why `gate` still takes it."""
    decided = asked(a_draft(), pdf_repaired=True)

    assert decided.action is not Action.POST


#: A cash memo with no tax on it, printed the way a supplier prints one. The
#: zero GST line matters: without it the tax is UNREAD, one conservation law
#: cannot be answered, and an honest read of this blocks - which would let the
#: seam test below pass on a refusal it did not cause.
A_BILL_THAT_POSTS = (
    "CASH MEMO",
    "DATE: 2026-04-01",
    "SUPPLIER: SHARMA STATIONERS",
    "DESCRIPTION                                  AMOUNT",
    "REGISTER BOOKS                                       495.00",
    "SUBTOTAL                                             495.00",
    "GST                                                    0.00",
    "TOTAL                                                495.00",
)
BILL_PAISE = 49_500


def a_pdf_draft(data: bytes) -> Draft:
    """The shipped path in three steps: bytes, the real reader, a draft."""
    return a_draft(TextLayerReader().extract(data, "application/pdf"))


def decided_on(data: bytes) -> Decided:
    """What the gate says about those bytes when the caller knows nothing.

    `pdf_repaired=None` is not a test convenience - it is what every shipped
    caller passes, because until now there was nothing else it could pass.
    """
    return asked(
        a_pdf_draft(data),
        pdf_repaired=None,
        net_paise=BILL_PAISE,
        balance_before_paise=BEFORE,
        balance_after_paise=BEFORE + BILL_PAISE,
    )


def test_a_pdf_that_had_to_be_mended_cannot_post_across_the_whole_seam() -> None:
    """BYTES to DECISION, which is the test that did not exist.

    Every part of this was proved separately and the seam between them is where
    the fact fell out. Real bytes, the shipped reader, the record it builds,
    the gate a caller asks - and the caller says nothing about repairs.

    The honest half is the control and is not optional: it POSTS a real entry,
    so the refusal on the other line is the repair and not the arithmetic."""
    honest, mended = (
        decided_on(pdf_bytes(A_BILL_THAT_POSTS)),
        decided_on(broken_startxref(pdf_bytes(A_BILL_THAT_POSTS))),
    )

    assert honest.action is Action.POST
    assert honest.entry is not None and honest.entry.amount_paise == BILL_PAISE
    assert mended.action is Action.ASK
    assert mended.entry is None
    assert "damaged and had to be repaired" in mended.said


def test_the_record_the_reader_builds_carries_the_repair_it_had_to_do() -> None:
    """The dropped line, on its own. `TextLayerReader.extract` read
    `reading.pdf_repaired` and built a record without it, so this was the last
    place the fact existed before the seam above."""
    mended = TextLayerReader().extract(
        broken_startxref(pdf_bytes(BILL)), "application/pdf"
    )

    assert mended.pdf_repaired is True
    assert (
        TextLayerReader().extract(pdf_bytes(BILL), "application/pdf").pdf_repaired
        is False
    )


def test_the_owners_refusal_reaches_the_observation_and_not_a_type_name() -> None:
    """A CROSSWIRE, measured the same day. `_money_field` asked `_paise(value)
    is None` BEFORE it asked whether the field was read, and `None` is not
    whole paise - so every refused amount arrived at the `Observation` as
    "total_paise arrived as NoneType", and the owner's sentence survived only
    on `record.per_field_source`. A person still saw it; nothing reading the
    observation did.

    The type sentence is for a value that ARRIVED and was not paise, which is
    a different fact, and the control below keeps it."""
    refusal = f"{NOT_FOUND}: {INVOICE_SHAPED}"
    seen = observed(
        a_draft(a_record(total_paise=None, sources={"total_paise": refusal}))
    )

    assert seen.total_paise.value is None
    assert seen.total_paise.confidence == 0.0
    assert seen.total_paise.source == refusal


def test_a_caller_whose_plumbing_is_broken_still_blocks_rather_than_asks() -> None:
    """THE CONTROL ON `_repaired` ITSELF. `record.pdf_repaired or caller` would
    have coerced a string to True and turned this block into a question, which
    is a caller with wrong plumbing being granted a softer answer than the one
    `decision._repair_blocks` decided for it."""
    junk = cast("bool | None", "no")
    decided = asked(a_draft(), pdf_repaired=junk)

    assert decided.action is Action.BLOCK
    assert asked(a_draft(a_repaired_record()), pdf_repaired=junk).action is Action.BLOCK

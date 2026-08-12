"""I54 — twenty inputs, one stated outcome each, and nothing decided by hand.

WHAT THE OWNER ASKED FOR, VERBATIM
-----------------------------------
    "I hand you 20 inputs - some good bills, some garbage, some duplicates, one
    with a 1-paisa error, one photo of a cat - and you watch the system post the
    good ones and refuse every bad one with a plain sentence for each."

`demo_safety_cage.py` is that demonstration. THIS FILE IS THE PROOF, and the two
are deliberately separate: a demo that judges its own output is a slideshow.

WHAT IS ASSERTED HERE
---------------------
    every outcome        all twenty, expected against actual, in one assertion
                         that names the rows that disagree
    every REASON         each input also declares WHICH guard must stop it. An
                         outcome that is right for the wrong reason is a guard
                         that has stopped working and nobody has noticed yet
    a plain sentence     every refusal carries one, no refusal names a ledger a
                         person would need accounting to understand
                         (`questions.is_jargon`), and none of them is a dead end
    zero silent posts    nothing in the BLOCK or ASK groups produced a
                         `LedgerEntry` or reached the books, measured against
                         `FakeTally` rather than inferred from the code path
    the books moved      the three good bills are IN the register and the trial
                         balance moved by exactly their totals, so the POST
                         group is not merely "not refused"

WHY THE CONTROLS ARE HERE
--------------------------
Nineteen of the twenty rows are refusals. A cage that refused EVERYTHING would
satisfy nineteen twentieths of this file, so the controls are what make it
measure anything: the post group really reaches the books, the byte counter
really counts, the jargon check really finds jargon, and the expected column
really contains all three outcomes.

WHAT THIS FILE DOES NOT PROVE
------------------------------
That any document was READ. No extraction backend is selected (`D-23` open), so
every photo and PDF here is refused for that reason and not because anything
recognised a cat. The demo says so on the row itself.

That the confidences are real. Nothing shipped produces a 0.72 today - the
scenario states it, so the bands can be demonstrated at all. It is scenario
data, exactly like the party names and the amounts.

NO NETWORK, NO REAL TALLY. `FakeTally` in process, and one subprocess that runs
the demo script the way a person would.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence

import demo_safety_cage as demo
from accountant.questions import QUESTION_CAP, is_jargon
from accountant.tallyio.fake import FakeTally
from accountant.web import app

#: Words that name something the person can actually do next. A refusal with
#: none of them is a dead end, and a dead end is a defect in this product.
ACTIONS = ("type", "add", "enter", "send", "tell", "check", "choose", "put", "ask")


def sentences_of(results: Sequence[demo.Result], outcome: demo.Outcome) -> list[str]:
    return [r.sentence for r in results if r.actual is outcome]


def refusals(results: Sequence[demo.Result]) -> list[str]:
    return sentences_of(results, demo.Outcome.BLOCK) + sentences_of(
        results, demo.Outcome.ASK
    )


def names_a_next_step(sentence: str) -> bool:
    return any(word in sentence.lower() for word in ACTIONS)


# ---- the twenty outcomes ----------------------------------------------------


def test_all_twenty_outcomes_match_the_outcome_stated_before_the_run() -> None:
    """The whole acceptance criterion, in one assertion.

    The message names the rows that disagree rather than only the count, because
    "17 != 20" sends the reader back to the script to work out which three.
    """
    results = demo.run_demo()

    wrong = [(r.number, r.name, r.expected, r.actual) for r in results if not r.agreed]

    assert len(results) == 20
    assert wrong == []


def test_every_input_was_stopped_by_the_guard_it_was_written_for() -> None:
    """An outcome can be right for the wrong reason.

    A bill with a one-paisa line error and an unknown party is refused either
    way, and if arithmetic quietly stopped working the outcome column would not
    move. The stage column is what notices.
    """
    results = demo.run_demo()

    wrong = [(r.number, r.expected_stage, r.stage) for r in results if not r.by_design]

    assert wrong == []


def test_the_control_the_expected_column_is_not_one_outcome_repeated() -> None:
    """THE CONTROL on both tests above.

    A cage that refused every input would satisfy nineteen of the twenty rows,
    and a demo that expected a refusal on all twenty would agree with it.
    """
    expected = [i.expected for i in demo.the_twenty()]

    assert expected.count(demo.Outcome.POST) == 3
    assert expected.count(demo.Outcome.ASK) == 2
    assert expected.count(demo.Outcome.BLOCK) == 15


def test_running_the_demo_twice_gives_the_identical_twenty_answers() -> None:
    """Determinism. Two runs on the same twenty inputs have no excuse to differ,
    and a test that says so is what stops a clock or a cache arriving later."""
    first = demo.run_demo()
    second = demo.run_demo()

    assert [(r.actual, r.stage, r.sentence) for r in first] == [
        (r.actual, r.stage, r.sentence) for r in second
    ]


# ---- one plain sentence, every time -----------------------------------------


def test_every_refusal_carries_a_sentence() -> None:
    """A refusal with no words cannot be shown to anybody. This product refuses
    in plain language or it does not refuse at all."""
    results = demo.run_demo()

    assert len(refusals(results)) == 17
    assert all(sentence.strip() for sentence in refusals(results))


def test_no_refusal_names_a_ledger_a_person_would_have_to_look_up() -> None:
    """S7, applied to refusals rather than to questions.

    `is_jargon` is the shipped test of whether a person needs accounting to
    understand a name. Every account in this company's chart is checked against
    every refusal, so a sentence that leaked "Purchases" fails here.
    """
    results = demo.run_demo()
    jargon = [a for a in demo.CHART if is_jargon(a)]

    leaked = [
        (sentence, account)
        for sentence in refusals(results)
        for account in jargon
        if account.lower() in sentence.lower()
    ]

    assert leaked == []


def test_the_control_the_jargon_check_can_actually_find_a_leak() -> None:
    """THE CONTROL. A check that found nothing in a sentence written to contain
    a leak would pass the test above while measuring nothing."""
    jargon = [a for a in demo.CHART if is_jargon(a)]
    planted = f"We put that against {jargon[0]} for you."

    found = [a for a in jargon if a.lower() in planted.lower()]

    assert jargon
    assert found == [jargon[0]]


def test_no_refusal_is_a_dead_end() -> None:
    """A person told only what went wrong is stuck. Every refusal here names
    something they can do next."""
    results = demo.run_demo()

    stuck = [s for s in refusals(results) if not names_a_next_step(s)]

    assert stuck == []


def test_the_control_a_dead_end_sentence_is_recognised_as_one() -> None:
    """THE CONTROL on the test above. A matcher that returned True for anything
    would pass it and prove nothing."""
    assert not names_a_next_step("That bill is wrong.")


def test_no_input_is_asked_more_questions_than_the_owner_allowed() -> None:
    """`QUESTION_CAP` is owner-set at 5. A cage that asked six would be
    interrogating somebody rather than helping them."""
    results = demo.run_demo()

    assert max(len(r.questions) for r in results) <= QUESTION_CAP
    assert all(r.questions for r in results if r.actual is demo.Outcome.ASK)


# ---- zero silent wrong posts ------------------------------------------------


def test_nothing_that_was_refused_produced_something_postable() -> None:
    """The wall, measured on the far side. A `LedgerEntry` is the only thing
    this system can write, so a refusal that made one is a refusal in name."""
    results = demo.run_demo()

    stopped = [r for r in results if r.actual is not demo.Outcome.POST]

    assert stopped
    assert [r.number for r in stopped if r.entry is not None] == []


def test_nothing_that_was_refused_reached_the_books() -> None:
    """Measured in the register rather than inferred from the code path."""
    tally = FakeTally()
    results = demo.run_demo(tally)

    written = {v.narration for v in tally.read_vouchers(demo.COMPANY)}
    refused = [r for r in results if r.actual is not demo.Outcome.POST]

    assert len(written) == 3
    assert [r.number for r in refused if r.reference and r.reference in written] == []


def test_the_control_the_three_good_bills_really_did_reach_the_books() -> None:
    """THE CONTROL on both tests above, and the one that makes them mean
    anything: a cage that wrote nothing at all would satisfy both."""
    tally = FakeTally()
    demo.run_demo(tally)

    assert len(tally.read_vouchers(demo.COMPANY)) == 3


def test_the_books_moved_by_exactly_the_three_totals_and_not_a_paisa_more() -> None:
    """`balance_delta_equals_entry`, over the whole run. The trial balance is
    read from `FakeTally` after the fact, not predicted."""
    tally = FakeTally()
    results = demo.run_demo(tally)

    posted = sum(r.amount_paise for r in results if r.actual is demo.Outcome.POST)
    balances = tally.trial_balance(demo.COMPANY)

    assert posted == 120_000 + 250_000 + 99_999
    assert balances[demo.EXPENSE] == posted
    assert balances[demo.FUNDING] == -posted


# ---- the door: refused before a byte was read -------------------------------


def test_the_file_over_the_cap_is_refused_with_413() -> None:
    results = demo.run_demo()
    oversize = results[17]

    assert oversize.number == 18
    assert oversize.status == 413
    assert oversize.actual is demo.Outcome.BLOCK


def test_not_one_byte_of_the_oversized_file_was_ever_read() -> None:
    """The refusal is made from the DECLARED length. A check made after the read
    is a check made after the damage - `rfile.read(n)` allocates whatever `n`
    says, so a limit enforced afterwards prevents nothing."""
    results = demo.run_demo()

    assert results[17].bytes_read == 0


def test_the_control_the_byte_counter_counts_when_something_is_read() -> None:
    """THE CONTROL. A counter stuck at zero would pass the test above while the
    door read the whole file.

    Nineteen bodies are opened and only eighteen yield a byte: the empty file is
    opened, read, and found to contain nothing. That distinction is the reason
    the counter and the "was it opened at all" flag are two different facts -
    collapsing them would make "never read" and "read, and empty" the same row.
    """
    results = demo.run_demo()

    assert len([r for r in results if r.was_read]) == 19
    assert len([r for r in results if r.bytes_read > 0]) == 18


def test_the_cap_the_demo_enforces_is_the_one_the_product_ships() -> None:
    """A demo with its own copy of the limit demonstrates its own copy. This is
    also where the number is measured rather than asserted from memory."""
    assert demo.CAP_BYTES == app.MAX_UPLOAD_BYTES


# ---- the individual refusals, each for its own reason -----------------------


def test_the_one_paisa_error_is_named_to_the_paisa_in_the_sentence() -> None:
    """"Does not add up" is not actionable. "Out by 1" is, and one paisa is the
    smallest disagreement arithmetic can see.

    The wording is `conservation.py`'s own and says "out by 1 paise" where a
    person would say "1 paisa". Asserted as it ships rather than as it should
    read: this file may not edit a shipped sentence to make its own test pass,
    and the singular is reported to the owner instead.
    """
    said = demo.run_demo()[4].sentence

    assert "out by 1 paise" in said
    assert "119999" in said and "120000" in said


def test_a_bill_carrying_tax_is_refused_because_posting_tax_is_switched_off() -> None:
    """Owner decision Q3 = D. The refusal is a boundary, not a breakage, and the
    sentence has to read like one."""
    said = demo.run_demo()[3].sentence

    assert "tax" in said.lower()
    assert not demo.POSTING_ENABLED


def test_the_unknown_party_refusal_never_offers_to_invent_a_name() -> None:
    """We never create a ledger the accountant did not create. The sentence says
    so and hands the person the two things that would work."""
    said = demo.run_demo()[7].sentence.lower()

    assert "kumar & sons" in said
    assert "tally" in said


def test_the_impossible_date_is_quoted_back_so_the_person_can_see_it() -> None:
    """The 34th of a month is a misread 04th or 14th. Naming the digits is what
    lets somebody fix it in one look."""
    said = demo.run_demo()[9].sentence

    assert "2026-08-34" in said


def test_the_pdf_that_is_really_a_photo_is_judged_by_its_contents() -> None:
    """A `.pdf` that is really a JPEG is a Tuesday, not an attack. The filename
    and the declared type are recorded and never believed."""
    result = demo.run_demo()[15]

    assert result.detected == "a JPEG photo"
    assert result.declared_disagreed


def test_the_cat_photo_is_recognised_as_a_photo_and_still_refused() -> None:
    """And the refusal says WHY: no reader is configured, so nothing on it was
    read. Nothing here recognised a cat and the sentence never claims to."""
    result = demo.run_demo()[11]

    assert result.detected == "a PNG image"
    assert result.actual is demo.Outcome.BLOCK
    assert "cat" not in result.sentence.lower()


def test_the_duplicate_is_refused_and_the_books_hold_one_copy_not_two() -> None:
    """Same operation id, same bill, sent twice. The second one is not an error
    to be reported as a breakage - it is a retry, and the honest answer is that
    the first one is already in."""
    tally = FakeTally()
    results = demo.run_demo(tally)
    written = [v for v in tally.read_vouchers(demo.COMPANY) if v.party == demo.PARTY_ONE]

    assert results[16].actual is demo.Outcome.BLOCK
    assert results[16].stage == "duplicate"
    assert len(written) == 2


def test_the_empty_file_and_the_random_bytes_are_told_apart() -> None:
    """Two different facts about a file, and a person can act on only one of
    them if we say which it was."""
    results = demo.run_demo()

    assert results[13].detected == "an empty file"
    assert results[14].detected != results[13].detected


# ---- the script a person actually runs --------------------------------------


def test_the_demo_script_runs_on_its_own_and_exits_zero() -> None:
    """The script IS the demo. A demo that only runs inside pytest is a test."""
    done = subprocess.run(  # noqa: S603
        [sys.executable, "demo_safety_cage.py"],
        cwd=demo.REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert done.returncode == 0, done.stderr[-2000:]


def test_the_printed_table_carries_every_input_and_its_sentence() -> None:
    """A table with the outcome and no sentence is the thing this demo exists to
    replace."""
    results = demo.run_demo()
    flat = " ".join(demo.format_table(results).split())

    missing = [r.number for r in results if " ".join(r.sentence.split()) not in flat]

    assert missing == []
    assert all(r.name[:38] in flat for r in results)


# ---- REVIEW NOTES -----------------------------------------------------------
#
# Re-read cold, as somebody who did not write it.
#
# 1. IMPLEMENTED. `test_all_twenty_outcomes_match` originally asserted only a
#    count of agreements. A reader who saw "17 == 20" had to open the script to
#    find which three rows moved. It now collects the disagreeing rows and
#    asserts the list is empty, so the failure output names them.
#
# 2. IMPLEMENTED. Every outcome assertion could pass for the wrong reason: an
#    input refused by the wrong guard still lands in the BLOCK column. Each
#    input now declares the stage that must stop it, and
#    `test_every_input_was_stopped_by_the_guard_it_was_written_for` fails when
#    the outcome is right and the reason is not. This is the single strongest
#    assertion in the file and it was missing from the first draft.
#
# 3. IMPLEMENTED. `test_nothing_that_was_refused_reached_the_books` compared a
#    refusal count against zero, which a demo that wrote nothing at all would
#    also satisfy. It is now paired with a control that the three good bills DID
#    reach the register, so neither test can pass on an empty run.
#
# 4. NOT IMPLEMENTED, and named so nobody assumes it is covered. `ACTIONS` is a
#    keyword proxy for "this refusal names a next step". A sentence reading
#    "type nothing, there is no way forward" would pass it. A real check needs a
#    person, and the honest description of this one is that it catches a refusal
#    that forgot the remedy entirely, which is the failure that actually
#    happens, and nothing subtler.
#
# 5. NOT IMPLEMENTED. The three POST rows use one company, one expense ledger
#    and one funding ledger, so a bug that posted every bill to the same wrong
#    pair would not be visible here. Covering it needs a second chart, which is
#    a second demo rather than a longer one.

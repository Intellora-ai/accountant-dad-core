"""The decision layer: the only thing allowed to turn a guess into a write.

WHY THIS FILE EXISTS
--------------------
`wall.py` says who may build a `LedgerEntry`. It says nothing about when one
*should* exist. That is this module's job, and it is the last gate before a
customer's books change - so the interesting question here is never "does it
post correctly" but "what does it refuse, and does it refuse for the stated
reason rather than by accident".

The bands are owner-set:

    post    confidence 0.95 or better AND every conservation law PASS AND the
            party known AND the period open AND no hard rule broken
    ask     confidence 0.70 to just under 0.95, OR any conservation law FAIL at
            any confidence, OR something on the bill readable two ways
    block   confidence under 0.70, OR any hard rule broken

THE ONE ASSERTION THAT MATTERS MOST
------------------------------------
`test_being_completely_sure_never_overrides_a_failed_conservation_check`.

Confidence is a statement about pixels. A conservation law is a statement about
arithmetic. If certainty could outvote arithmetic, then the single failure this
whole cage exists to stop - a confidently misread number reaching the books -
would walk straight through, and every other test here would be decoration.
Its paired control posts the identical bill with that one law passing, so the
test cannot be passing because the module refuses everything.

WHY SO MUCH OF THIS FILE IS REFUSALS
-------------------------------------
Fail closed is not a slogan here, it is the arithmetic of the thing: one wrong
post costs a customer real money and a wrong refusal costs them a minute. So
every input this module cannot classify - an amount that is not whole paise, a
verdict it does not recognise, a question count that is not a number, a fact
nobody looked up - has a test proving it blocks rather than being coerced into
something postable.

WHAT THIS FILE DOES NOT PROVE
------------------------------
It does not prove the bill was READ correctly. A bill misread consistently -
every figure scaled by ten - satisfies every conservation law, arrives at
confidence 1.0, and is posted. That is failure mode F-02, it is not detectable
by arithmetic, and nothing in this module or this file claims otherwise.

It does not prove the bands are the RIGHT bands. 0.95 and 0.70 are owner-set
numbers; whether they are calibrated is a question for a corpus run against
labelled data this repository does not have. These tests prove the code
implements the numbers it was given, not that the numbers are good.

It does not prove the decision layer is the only writer. That is
`tests/test_the_wall.py`, both halves of it.

NO NETWORK, NO FIXTURES, NO IO. Every test here is arithmetic and strings.
"""

from __future__ import annotations

import pytest

from accountant.cage.conservation import LAWS, ConservationResult, Verdict
from accountant.cage.decision import (
    ASK_FLOOR,
    AUTO_POST_FLOOR,
    DOCUMENT_LAWS,
    GST_IS_OFF,
    LAW_ABOUT_THE_BOOKS,
    Action,
    Decided,
    Moment,
    Situation,
    decide,
)
from accountant.cage.wall import DECIDING_MODULE, Field, LedgerEntry, Observation
from accountant.questions import QUESTION_CAP, Answer, Question

# ---- builders ---------------------------------------------------------------
# Defaults describe a clean, boring, fully readable purchase. Every test below
# names only what it changes, so what the test is about is the only thing on
# screen. Production `Situation` has no defaults at all - see the test at the
# bottom that pins that, because a default of "the period is open" would be a
# fact nobody checked.


def an_observation(
    *,
    party: object = "Blue Steel Traders",
    total_paise: object = 250_000,
    confidence: float = 1.0,
) -> Observation:
    return Observation(
        date=Field(value="2026-08-12", confidence=confidence, source="test"),
        party=Field(value=party, confidence=confidence, source="test"),
        total_paise=Field(value=total_paise, confidence=confidence, source="test"),
        tax_paise=Field(value=0, confidence=confidence, source="test"),
    )


def all_laws_pass() -> tuple[ConservationResult, ...]:
    return tuple(
        ConservationResult(law=law, verdict=Verdict.PASS, said=f"{law}: agreed.")
        for law in LAWS
    )


def one_law(
    verdict: Verdict, said: str = "the numbers are out by 1 paise."
) -> tuple[ConservationResult, ...]:
    """Every law passing except the first, which gets `verdict`."""
    results = list(all_laws_pass())
    results[0] = ConservationResult(law=LAWS[0], verdict=verdict, said=said)
    return tuple(results)


def named_law(
    law: str, verdict: Verdict, said: str = "the numbers are out by 1 paise."
) -> tuple[ConservationResult, ...]:
    """Every law passing except the one NAMED, which gets `verdict`.

    `one_law` above always moves the first law, which is a document law. The
    pre-write exemption is about one specific law by name, so a helper that can
    only reach position zero cannot test it - and cannot test that the other
    three are untouched by it either.
    """
    return tuple(
        ConservationResult(
            law=name,
            verdict=verdict if name == law else Verdict.PASS,
            said=said if name == law else f"{name}: agreed.",
        )
        for name in LAWS
    )


#: "Not passed", which is NOT the same as "passed `None`". Without it, the test
#: for a `None` observation quietly received the clean default and passed
#: against a module that does not check at all. It was first written that way
#: and the first run caught it.
UNSET = object()


def a_situation(
    *,
    observation: object = UNSET,
    conservation: object = UNSET,
    party_known: object = True,
    period_open: object = True,
    carries_gst: object = False,
    questions_asked: object = 0,
    debit_account: object = "Purchases",
    credit_account: object = "Cash",
    moment: object = Moment.BEFORE_THE_WRITE,
    ambiguous_fields: object = (),
) -> Situation:
    return Situation(
        observation=an_observation() if observation is UNSET else observation,  # type: ignore[arg-type]
        conservation=all_laws_pass() if conservation is UNSET else conservation,  # type: ignore[arg-type]
        party_known=party_known,  # type: ignore[arg-type]
        period_open=period_open,  # type: ignore[arg-type]
        carries_gst=carries_gst,  # type: ignore[arg-type]
        questions_asked=questions_asked,  # type: ignore[arg-type]
        debit_account=debit_account,  # type: ignore[arg-type]
        credit_account=credit_account,  # type: ignore[arg-type]
        moment=moment,  # type: ignore[arg-type]
        ambiguous_fields=ambiguous_fields,  # type: ignore[arg-type]
    )


# ---- the bands, at their exact edges ----------------------------------------


def test_a_clean_bill_at_full_confidence_is_posted() -> None:
    assert decide(a_situation()).action is Action.POST


def test_the_control_the_same_clean_bill_one_law_short_is_not_posted() -> None:
    """THE CONTROL on the test above. A `decide` that returned POST
    unconditionally would pass the first test and fail this one."""
    decided = decide(a_situation(conservation=one_law(Verdict.FAIL)))
    assert decided.action is not Action.POST


def test_confidence_exactly_at_the_auto_post_floor_posts() -> None:
    """The owner set 0.95 as "or better", so the floor itself is inside the
    band. An off-by-one here silently refuses a whole class of good bills."""
    seen = an_observation(confidence=AUTO_POST_FLOOR)
    assert decide(a_situation(observation=seen)).action is Action.POST


def test_confidence_a_hair_under_the_auto_post_floor_only_asks() -> None:
    seen = an_observation(confidence=AUTO_POST_FLOOR - 0.01)
    assert decide(a_situation(observation=seen)).action is Action.ASK


def test_confidence_exactly_at_the_ask_floor_asks_rather_than_blocking() -> None:
    seen = an_observation(confidence=ASK_FLOOR)
    assert decide(a_situation(observation=seen)).action is Action.ASK


def test_confidence_a_hair_under_the_ask_floor_blocks() -> None:
    """Below this the product does not even ask, because a question about a
    field we could barely read wastes one of the five."""
    seen = an_observation(confidence=ASK_FLOOR - 0.01)
    assert decide(a_situation(observation=seen)).action is Action.BLOCK


def test_an_unread_field_drags_the_whole_bill_under_the_ask_floor() -> None:
    """One field at 0.0 makes `lowest_confidence` 0.0, whatever the rest scored.
    The minimum is the point: one thing we could not read stops the post."""
    seen = Observation(
        date=Field(value="2026-08-12", confidence=0.99, source="test"),
        party=Field(value=None, confidence=0.0, source="not_found: no party"),
        total_paise=Field(value=250_000, confidence=0.99, source="test"),
        tax_paise=Field(value=0, confidence=0.99, source="test"),
    )
    assert decide(a_situation(observation=seen)).action is Action.BLOCK


# ---- arithmetic beats certainty. This is the point of the module. -----------


def test_being_completely_sure_never_overrides_a_failed_conservation_check() -> None:
    """THE most important assertion in this file.

    Confidence describes pixels; a conservation law describes arithmetic. If
    being sure could outvote the arithmetic, a confidently misread number would
    reach the books - which is the exact failure this cage exists to stop.
    """
    sure = an_observation(confidence=1.0)
    decided = decide(a_situation(observation=sure, conservation=one_law(Verdict.FAIL)))
    assert decided.action is Action.ASK
    assert decided.entry is None


def test_the_control_the_identical_bill_with_that_law_passing_is_posted() -> None:
    """THE CONTROL on the test above, and it carries real weight: without it,
    the assertion passes just as well in a module that refuses everything."""
    sure = an_observation(confidence=1.0)
    decided = decide(a_situation(observation=sure, conservation=all_laws_pass()))
    assert decided.action is Action.POST


def test_the_ask_repeats_what_the_failing_law_actually_said() -> None:
    """ "The numbers do not add up" is not actionable. The law's own sentence
    names the figures and the difference, and that is what a person can check.

    names the figures and the difference, and that is what a person can check."""
    broken = one_law(Verdict.FAIL, said="out by 1 paise on a 2,500 rupee bill.")
    decided = decide(a_situation(conservation=broken))
    assert "out by 1 paise" in decided.said


# ---- before the write, and after it -----------------------------------------
#
# Three of the four laws are statements about the DOCUMENT and can be checked
# before anything is written. The fourth, `balance_delta_equals_entry`, is a
# statement about the BOOKS and compares the ledger balance before and after the
# entry - so before a write there is no after, and it is INDETERMINATE on every
# honest pre-write call. Blocking on it made auto-post unreachable except by
# handing the law a PREDICTED after-balance, which is a law comparing a number
# against itself.


def test_before_the_write_a_balance_law_that_cannot_be_known_yet_still_posts() -> None:
    """THE test that proves auto-post is reachable without lying to `decide`.

    Every other post test in this file passes a balance law that PASSED, which
    a caller can only produce pre-write by predicting the answer. This one hands
    over the honest verdict - "not yet knowable" - and still expects the write.
    """
    unknowable = named_law(
        LAW_ABOUT_THE_BOOKS,
        Verdict.INDETERMINATE,
        said="could not check balance delta equals entry: the balance after "
        "was not read.",
    )
    decided = decide(
        a_situation(conservation=unknowable, moment=Moment.BEFORE_THE_WRITE)
    )
    assert decided.action is Action.POST
    assert decided.entry is not None


def test_before_the_write_a_balance_law_that_failed_is_still_refused() -> None:
    """The exemption is for "not yet knowable". It is never for "known to be
    wrong". A caller who supplied real before and after balances and got a
    contradiction back is telling us something true, and discarding it because
    of when it arrived would be the same defect in the other direction."""
    contradicted = named_law(
        LAW_ABOUT_THE_BOOKS,
        Verdict.FAIL,
        said="the books did not move by the amount of this entry.",
    )
    decided = decide(
        a_situation(conservation=contradicted, moment=Moment.BEFORE_THE_WRITE)
    )
    assert decided.action is not Action.POST
    assert decided.entry is None


def test_before_the_write_any_document_law_that_could_not_be_checked_blocks() -> None:
    """All three by name, not one of them. "Could not check the arithmetic on
    the bill" is precisely the case this cage exists for, and an exemption
    written with `in` where `not in` belonged would let one through while the
    other two carried on blocking."""
    for law in sorted(DOCUMENT_LAWS):
        unchecked = named_law(law, Verdict.INDETERMINATE, said=f"{law}: not read.")
        decided = decide(
            a_situation(conservation=unchecked, moment=Moment.BEFORE_THE_WRITE)
        )
        assert decided.action is Action.BLOCK, law
        assert decided.entry is None, law


def test_after_the_write_a_balance_law_that_could_not_be_checked_blocks() -> None:
    """After the write the balance IS knowable - the register can be read back.
    So "I could not check it" there does not mean "not yet", it means nobody
    looked, and that is the one failure this law exists to catch."""
    unchecked = named_law(
        LAW_ABOUT_THE_BOOKS,
        Verdict.INDETERMINATE,
        said="the balance after was not read.",
    )
    decided = decide(a_situation(conservation=unchecked, moment=Moment.AFTER_THE_WRITE))
    assert decided.action is Action.BLOCK
    assert decided.entry is None


def test_the_control_the_exemption_is_exactly_one_law_and_not_the_other_three() -> None:
    """THE CONTROL, and it carries more weight than the tests above.

    Measured law by law rather than read off a constant: which law names does an
    INDETERMINATE verdict get a pass for, pre-write. Set equality and not a
    subset - an exemption widened to all four passes every test above it, and an
    exemption narrowed to none passes the three blocking tests.
    """
    exempt = {
        law
        for law in LAWS
        if decide(
            a_situation(
                conservation=named_law(law, Verdict.INDETERMINATE),
                moment=Moment.BEFORE_THE_WRITE,
            )
        ).action
        is Action.POST
    }
    assert exempt == {LAW_ABOUT_THE_BOOKS}


def test_the_control_the_document_laws_are_derived_and_never_retyped() -> None:
    """THE CONTROL on the derivation. The three names are pinned to literals
    HERE, once, so `decision.py` can take them from `conservation.LAWS` instead
    of keeping a second copy that drifts. A law added to `conservation.py` lands
    in this set automatically, which means it blocks - fail closed - and this
    assertion is what fails on the day somebody adds one."""
    assert LAW_ABOUT_THE_BOOKS == "balance_delta_equals_entry"
    assert {
        "debits_equal_credits",
        "lines_sum_to_total",
        "net_plus_tax_equals_gross",
    } == DOCUMENT_LAWS
    assert DOCUMENT_LAWS | {LAW_ABOUT_THE_BOOKS} == set(LAWS)


def test_a_situation_that_does_not_say_which_moment_it_is_cannot_be_built() -> None:
    """No default, for the same reason `period_open` has none: inferring the
    moment from whether a balance arrived is how the last defect got in. A
    caller who does not say gets a `TypeError` here rather than an exemption
    there."""
    with pytest.raises(TypeError):
        Situation(  # type: ignore[call-arg]
            observation=an_observation(),
            conservation=all_laws_pass(),
            party_known=True,
            period_open=True,
            carries_gst=False,
            questions_asked=0,
            debit_account="Purchases",
            credit_account="Cash",
        )


def test_a_moment_that_is_not_one_of_the_two_blocks() -> None:
    """Malformed in, refused out - the same as every other field here. It is
    also the fail-closed direction: nobody said when this is, so nothing gets
    the pre-write exemption."""
    decided = decide(a_situation(moment="before, probably"))
    assert decided.action is Action.BLOCK
    assert decided.entry is None


def test_the_control_a_moment_nobody_stated_grants_no_exemption() -> None:
    """THE CONTROL on the test above: prove the refusal is not just the bad
    moment being reported, but that the balance law stopped being exempt."""
    unknowable = named_law(LAW_ABOUT_THE_BOOKS, Verdict.INDETERMINATE)
    decided = decide(a_situation(conservation=unknowable, moment=None))
    assert decided.action is Action.BLOCK
    assert len(decided.reasons) == 2


# ---- hard rules, each of which always blocks --------------------------------


def test_a_bill_carrying_tax_is_blocked_because_tax_posting_is_switched_off() -> None:
    """Owner decision Q3=D. Posting a tax bill without its tax line leaves a
    wrong statutory entry in somebody's books, so the whole bill is refused."""
    assert decide(a_situation(carries_gst=True)).action is Action.BLOCK


def test_the_tax_refusal_says_in_plain_words_that_tax_posting_is_off() -> None:
    """A refusal a person cannot act on is a bug with a polite face. This one
    has to say what is switched off, not just that something is."""
    assert GST_IS_OFF in decide(a_situation(carries_gst=True)).said


def test_not_knowing_whether_there_is_tax_blocks_rather_than_assuming_none() -> None:
    """The most dangerous coercion in the system, in a different costume: an
    unchecked tax field read as "no tax" posts a bill without its input credit,
    which is real money gone with nothing on screen to notice."""
    assert decide(a_situation(carries_gst=None)).action is Action.BLOCK


def test_a_conservation_law_that_could_not_be_checked_blocks() -> None:
    """INDETERMINATE is not a soft pass. "I could not check" and "I checked and
    it is fine" are different sentences and only one of them authorises a
    write."""
    unchecked = one_law(Verdict.INDETERMINATE, said="the total was not read.")
    assert decide(a_situation(conservation=unchecked)).action is Action.BLOCK


def test_the_control_that_same_law_passing_does_not_block() -> None:
    """THE CONTROL: proves the block above came from the verdict and not from
    the helper that builds the results."""
    ok = one_law(Verdict.PASS, said="both sides agree.")
    assert decide(a_situation(conservation=ok)).action is Action.POST


def test_a_closed_period_blocks() -> None:
    assert decide(a_situation(period_open=False)).action is Action.BLOCK


def test_not_knowing_whether_the_period_is_open_blocks() -> None:
    """Nobody looked it up. That is not the same as looking it up and finding it
    open, and only one of the two is a fact."""
    assert decide(a_situation(period_open=None)).action is Action.BLOCK


def test_an_unknown_party_blocks_rather_than_inventing_a_name() -> None:
    """We never create a ledger the accountant did not create. A new name in
    somebody's books is theirs to add, not ours."""
    assert decide(a_situation(party_known=False)).action is Action.BLOCK


def test_not_knowing_whether_the_party_is_known_blocks() -> None:
    assert decide(a_situation(party_known=None)).action is Action.BLOCK


def test_the_question_after_the_last_allowed_one_is_never_asked() -> None:
    """The budget is five, owner-set. Spent means handed over, not asked
    again - a sixth question is a product that will not take no for an
    answer."""
    assert decide(a_situation(questions_asked=QUESTION_CAP)).action is Action.BLOCK


def test_the_control_one_question_below_the_cap_still_posts() -> None:
    """THE CONTROL on the cap: a guard written as `>` instead of `>=`, or the
    reverse, is invisible without both sides of the boundary."""
    asked = QUESTION_CAP - 1
    assert decide(a_situation(questions_asked=asked)).action is Action.POST


def test_a_question_count_that_is_not_a_whole_number_blocks() -> None:
    assert decide(a_situation(questions_asked=1.5)).action is Action.BLOCK


def test_a_negative_question_count_blocks() -> None:
    """A count below zero means the caller's bookkeeping is wrong, and a
    module that trusts a broken counter has no budget at all."""
    assert decide(a_situation(questions_asked=-1)).action is Action.BLOCK


def test_a_bool_question_count_blocks_because_bool_is_an_int_in_python() -> None:
    """`isinstance(True, int)` is True and `True == 1`, so a flag passed where
    a count belonged would read as "one question asked" and authorise four
    more."""
    assert decide(a_situation(questions_asked=True)).action is Action.BLOCK


# ---- fail closed: anything it cannot classify blocks ------------------------


def test_an_observation_that_is_not_an_observation_blocks() -> None:
    """Nothing arrived that can be read, so nothing can be concluded. The
    alternative is an AttributeError reaching a person as "something broke"."""
    assert decide(a_situation(observation=None)).action is Action.BLOCK


def test_conservation_results_that_do_not_cover_every_law_block() -> None:
    """Four laws run on every bill or none of them counts. Three results mean
    one law was skipped, and the caller cannot say which."""
    assert decide(a_situation(conservation=all_laws_pass()[:3])).action is Action.BLOCK


def test_a_verdict_that_is_none_of_the_three_blocks() -> None:
    """The genuine "cannot classify" branch. `ConservationResult` does not
    validate its own verdict, so a caller can hand over a bare string, and a
    module that only asks "is it FAIL" would read that as a pass."""
    nonsense = (
        ConservationResult(law=LAWS[0], verdict="probably ok", said="?"),  # type: ignore[arg-type]
        *all_laws_pass()[1:],
    )
    assert decide(a_situation(conservation=nonsense)).action is Action.BLOCK


def test_conservation_results_that_are_not_even_a_tuple_block() -> None:
    assert decide(a_situation(conservation="all fine")).action is Action.BLOCK


def test_an_amount_that_is_not_whole_paise_blocks_rather_than_rounding() -> None:
    """Money is integer paise everywhere in this system. Rounding here is how
    0.1 + 0.2 gets into a statutory record."""
    seen = an_observation(total_paise=250_000.5)
    assert decide(a_situation(observation=seen)).action is Action.BLOCK


def test_a_bool_amount_blocks_because_bool_is_an_int_in_python() -> None:
    """`True == 1`, so a flag passed where an amount belonged would post a
    one-paisa entry that balances perfectly."""
    seen = an_observation(total_paise=True)
    assert decide(a_situation(observation=seen)).action is Action.BLOCK


def test_a_party_that_is_not_a_name_blocks() -> None:
    """A number where a name belonged. Nothing downstream can spell it, and
    guessing at it would put an invented name in somebody's books."""
    seen = an_observation(party=99)
    assert decide(a_situation(observation=seen)).action is Action.BLOCK


def test_a_blank_party_name_blocks_because_the_wall_refuses_it() -> None:
    """The wall is the last gate and it is allowed to say no. When it does,
    that is a block - never an exception on its way to a person's screen."""
    seen = an_observation(party="   ")
    assert decide(a_situation(observation=seen)).action is Action.BLOCK


def test_a_zero_amount_blocks_because_the_wall_refuses_it() -> None:
    """Corrections happen by reversal in this system, never by a zero or a
    negative. The rule lives in the wall; this proves the refusal is caught."""
    seen = an_observation(total_paise=0)
    assert decide(a_situation(observation=seen)).action is Action.BLOCK


def test_both_sides_naming_the_same_place_blocks() -> None:
    """Money moving from an account to itself is a typo, not an entry."""
    situation = a_situation(debit_account="Cash", credit_account="Cash")
    assert decide(situation).action is Action.BLOCK


def test_an_account_that_is_not_a_name_blocks() -> None:
    assert decide(a_situation(credit_account=None)).action is Action.BLOCK


def test_a_missing_account_is_asked_about_rather_than_refused() -> None:
    """An absence is a question, not a failure. "How did you pay?" is something
    a person can answer in one tap; refusing them instead spends their goodwill
    on our ignorance."""
    assert decide(a_situation(credit_account="")).action is Action.ASK


def test_an_ambiguity_list_that_is_not_a_list_blocks() -> None:
    assert decide(a_situation(ambiguous_fields="date")).action is Action.BLOCK


def test_an_unrecognised_input_never_reaches_the_post_branch() -> None:
    """The sweep: every malformed input above, in one place, asserting the one
    property that actually matters - none of them produced something writable."""
    broken: tuple[Situation, ...] = (
        a_situation(observation=None),
        a_situation(conservation="all fine"),
        a_situation(questions_asked=1.5),
        a_situation(carries_gst=None),
        a_situation(ambiguous_fields="date"),
        a_situation(credit_account=None),
    )
    assert all(decide(s).entry is None for s in broken)


# ---- ambiguity --------------------------------------------------------------


def test_an_ambiguous_field_is_asked_about_even_at_full_confidence() -> None:
    """Confidence says the pixels were legible. It says nothing about a date
    that could be March the fourth or April the third."""
    situation = a_situation(ambiguous_fields=("date",))
    assert decide(situation).action is Action.ASK


def test_the_ambiguity_sentence_reports_a_count_and_never_the_field_name() -> None:
    """Same shape as the unmapped-account count in `questions.py`: the number
    reaches the person, the internal name never does. A field name is our
    vocabulary, not theirs."""
    said = decide(a_situation(ambiguous_fields=("total_paise",))).said
    assert "1" in said
    assert "total_paise" not in said


# ---- a LedgerEntry exists on exactly one outcome ----------------------------


def test_a_post_carries_the_entry_it_decided_to_write() -> None:
    decided = decide(a_situation())
    entry = decided.entry
    assert entry is not None
    assert entry.party == "Blue Steel Traders"
    assert entry.amount_paise == 250_000
    assert entry.debit_account == "Purchases"
    assert entry.credit_account == "Cash"


def test_an_ask_carries_no_entry_at_all() -> None:
    """An entry that exists is an entry something downstream can write. On an
    ask there is nothing to write yet, so there must be nothing to write with."""
    decided = decide(a_situation(observation=an_observation(confidence=0.80)))
    assert decided.action is Action.ASK
    assert decided.entry is None


def test_a_block_carries_no_entry_at_all() -> None:
    decided = decide(a_situation(period_open=False))
    assert decided.action is Action.BLOCK
    assert decided.entry is None


def test_a_post_outcome_without_an_entry_cannot_even_be_constructed() -> None:
    """The invariant is enforced on the type, not left to the function. A POST
    that decided nothing writable is a contradiction in terms."""
    with pytest.raises(ValueError, match="entry"):
        Decided(action=Action.POST, said="fine", reasons=("fine",), entry=None)


def test_a_refusal_carrying_an_entry_cannot_even_be_constructed() -> None:
    """THE CONTROL on the invariant above, and the direction that actually
    costs money: a blocked decision holding a writable entry is one careless
    `.entry` away from posting the thing we just refused."""
    entry = LedgerEntry.decided(
        DECIDING_MODULE,
        party="Blue Steel Traders",
        amount_paise=250_000,
        debit_account="Purchases",
        credit_account="Cash",
    )
    with pytest.raises(ValueError, match="entry"):
        Decided(action=Action.BLOCK, said="no", reasons=("no",), entry=entry)


def test_a_decision_with_no_reason_cannot_be_constructed() -> None:
    """An outcome nobody can explain is not an outcome, it is a shrug."""
    with pytest.raises(ValueError, match="reason"):
        Decided(action=Action.BLOCK, said="no", reasons=())


# ---- the sentences a person actually reads ----------------------------------

#: Ledger names a person would need accounting to understand. `is_jargon` says
#: so for every one of them - the first three because their plain description
#: uses different words, the last two because we have no plain words at all.
#: They are also the names the builders above put on the entry, so a sentence
#: that leaked an account name would leak one of these.
JARGON = (
    "Purchases",
    "Repairs & Maintenance",
    "Printing & Stationery",
    "Sundry Creditors",
    "Input Tax Credit",
)


def jargon_in(sentence: str) -> list[str]:
    """Reuse `questions.py`'s own guard rather than writing a second one.

    A private copy of "is this jargon" would drift from the one the questions
    are checked against, and then two parts of the product would disagree about
    what a person can understand.
    """
    probe = Question(
        problem_id="probe",
        text=sentence,
        answers=(Answer(label="x", value="y"),),
    )
    return probe.mentions_any(JARGON)


def test_the_jargon_guard_this_file_relies_on_can_actually_find_jargon() -> None:
    """THE CONTROL on every sentence test below. A guard that never fires would
    pass all of them while proving nothing."""
    assert jargon_in("Post this to Purchases and Sundry Creditors.")


def test_every_outcome_carries_a_sentence_a_person_can_read() -> None:
    situations = (
        a_situation(),
        a_situation(observation=an_observation(confidence=0.80)),
        a_situation(period_open=False),
    )
    assert all(decide(s).said.strip() for s in situations)


def test_no_sentence_this_module_produces_contains_a_ledger_name() -> None:
    """S7, applied to refusals. The person is told what happened, never which
    account we were thinking of - and the accounts in these situations ARE
    jargon names, so a leak would show here."""
    situations = (
        a_situation(),
        a_situation(carries_gst=True),
        a_situation(period_open=False),
        a_situation(party_known=False),
        a_situation(conservation=one_law(Verdict.FAIL)),
        a_situation(conservation=one_law(Verdict.INDETERMINATE)),
        a_situation(questions_asked=QUESTION_CAP),
        a_situation(observation=an_observation(confidence=0.20)),
        a_situation(observation=an_observation(total_paise=0)),
        a_situation(debit_account="Purchases", credit_account="Purchases"),
        a_situation(credit_account=""),
    )
    leaked = [word for s in situations for word in jargon_in(decide(s).said)]
    assert leaked == []


def test_a_block_with_two_separate_causes_says_both_of_them() -> None:
    """A refusal that reports one problem when there are two sends the person
    to fix one thing and walk straight back into the other."""
    decided = decide(a_situation(period_open=False, party_known=False))
    assert len(decided.reasons) == 2


def test_the_said_sentence_is_made_of_the_reasons_and_hides_none_of_them() -> None:
    decided = decide(a_situation(period_open=False, carries_gst=True))
    assert all(reason in decided.said for reason in decided.reasons)


# ---- shape, determinism, and the wall ---------------------------------------


def test_deciding_twice_on_the_same_situation_gives_the_identical_answer() -> None:
    """Determinism. Pure arithmetic over frozen data has no excuse to vary, and
    a test that says so is what stops someone adding a clock or a cache."""
    situation = a_situation()
    assert decide(situation) == decide(situation)


def test_a_decision_cannot_be_edited_after_it_is_made() -> None:
    """Frozen, like every other verdict in the cage. A decision that can be
    changed after the fact is not evidence of anything."""
    decided = decide(a_situation(period_open=False))
    with pytest.raises(AttributeError):
        decided.action = Action.POST  # type: ignore[misc]


def test_this_module_is_the_one_the_wall_names_as_the_decider() -> None:
    """If these two ever disagree, every post in the product turns into a
    `NotYourEntryError` - so the constant is asserted against the real module
    name rather than against a copy of the string."""
    assert decide.__module__ == DECIDING_MODULE


def test_a_situation_has_no_default_for_a_fact_nobody_looked_up() -> None:
    """A default of "the period is open" is a fact nobody checked wearing the
    costume of one. Every fact about the world is a required argument, so a
    caller who forgot one gets a TypeError here rather than a post there."""
    with pytest.raises(TypeError):
        Situation(  # type: ignore[call-arg]
            observation=an_observation(),
            conservation=all_laws_pass(),
        )


# ---- REVIEW NOTES -----------------------------------------------------------
#
# Read back cold, as a reviewer who did not write this. Five things found,
# three fixed here, two left with reasons.
#
# 1. FIXED - the jargon tests had no control. Every one of them asserted
#    `jargon_in(...) == []`, which a guard that never fires passes perfectly.
#    `test_the_jargon_guard_this_file_relies_on_can_actually_find_jargon` is the
#    control, and it is not hypothetical: `is_jargon` returns False for any
#    account whose plain description reuses its own word, so a probe list of
#    "Cash" and "Bank" would have made the whole section vacuous. The list is
#    now five names `is_jargon` genuinely rejects.
#
# 2. FIXED - the malformed-input tests each asserted `action is BLOCK` and
#    stopped there. BLOCK is the label; `entry is None` is the property that
#    protects a customer. A bug that returned BLOCK while still attaching an
#    entry would have passed every one of them.
#    `test_an_unrecognised_input_never_reaches_the_post_branch` sweeps all six
#    malformed inputs and asserts the property instead of the label.
#
# 3. FIXED - the band-edge tests only ever moved DOWN from a passing case, so a
#    `>` written as `>=` at the auto-post floor was invisible. Both floors now
#    have their exact value and one step below it, and the question cap has the
#    cap itself and one below.
#
# 4. NOT FIXED, deliberate - there is no test that a bill scaled by ten is
#    refused, because it is not. Every law passes, confidence is 1.0, and it
#    posts. That is F-02 and it is stated in WHAT THIS FILE DOES NOT PROVE
#    rather than papered over with a test that would have to assert the wrong
#    thing to pass.
#
# 5. NOT FIXED, needs the owner - nothing here proves 0.95 and 0.70 are the
#    right numbers, only that the code implements them. Calibration needs
#    labelled invoices this repository does not have (H-02), and inventing a
#    threshold test would make a measurement out of an assumption.

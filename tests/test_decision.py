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
    ask     confidence 0.70 to just under 0.95, OR something on the bill
            readable two ways
    block   confidence under 0.70, OR any hard rule broken - and a conservation
            law that FAILED became one of them on 2026-08-13

A CONSERVATION FAIL BLOCKS. THAT REVERSED ON 2026-08-13.
---------------------------------------------------------
It used to ASK, and the band list above used to say so. The owner closed it:
"Conservation FAIL -> BLOCK, always. This is now a hard rule." Nothing a person
can answer makes 45,000 + 74,999 equal 120,000, so a question about it spends
one of five questions on something no answer fixes.

Every test in this file that asserted ASK on a failing law was therefore
asserting the wrong thing. Each one is CORRECTED rather than deleted, with the
date and the direction in its own docstring.

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

A REPAIRED FILE CAPS THE OUTCOME AT ASK, AND THAT IS NOT A HARD RULE
----------------------------------------------------------------------
Owner decision, 2026-08-13: a PDF that had to be repaired before it could be
read may be confirmed, and may never auto-post. It is a CEILING - it lowers the
best available outcome and changes nothing else - and the section that tests it
leads with the control, because a ceiling and a blanket refusal are
indistinguishable from the refusing side.

NO NETWORK, NO FIXTURES, NO IO. Every test here is arithmetic and strings.
"""

from __future__ import annotations

import datetime

import pytest

from accountant.cage.conservation import LAWS, ConservationResult, Verdict
from accountant.cage.decision import (
    ASK_FLOOR,
    AUTO_POST_ALLOWED_TIERS,
    AUTO_POST_FLOOR,
    DOCUMENT_LAWS,
    GST_IS_OFF,
    LAW_ABOUT_THE_BOOKS,
    NUMBERS_DO_NOT_ADD_UP,
    Action,
    Decided,
    Moment,
    Situation,
    decide,
)
from accountant.cage.wall import DECIDING_MODULE, Field, LedgerEntry, Observation
from accountant.checks import tax_lines_can_be_posted
from accountant.money import format_inr
from accountant.questions import QUESTION_CAP, Answer, Question
from accountant.schema import Voucher

# ---- builders ---------------------------------------------------------------
# Defaults describe a clean, boring, fully readable purchase. Every test below
# names only what it changes, so what the test is about is the only thing on
# screen. Production `Situation` has no defaults at all - see the test at the
# bottom that pins that, because a default of "the period is open" would be a
# fact nobody checked.


#: The amount on the clean bill every builder here starts from. Named once so
#: the checked-amount helper below and `an_observation` cannot drift apart into
#: two different "clean totals", which would make every link test vacuous.
CLEAN_TOTAL = 250_000

#: The one tier the owner cleared for auto-post, and one that is not. Written as
#: literals rather than imported from `accountant.extract`, because this file
#: reads nothing and imports no reader - the same rule `decision.py` itself
#: keeps. `tests/test_gate.py` is where the literal is bound to the string the
#: text-layer reader actually stamps, so a rename there cannot pass unnoticed.
TEXT_LAYER = "pdf_text_layer"
OCR = "free_ocr"


def an_observation(
    *,
    party: object = "Blue Steel Traders",
    total_paise: object = CLEAN_TOTAL,
    tax_paise: object = 0,
    confidence: float = 1.0,
) -> Observation:
    return Observation(
        date=Field(value="2026-08-12", confidence=confidence, source="test"),
        party=Field(value=party, confidence=confidence, source="test"),
        total_paise=Field(value=total_paise, confidence=confidence, source="test"),
        # A field with no value carries confidence 0.0 - the wall enforces it,
        # because not reading something and being unsure about it are the same
        # fact. Written here so a test can hand over an UNREAD tax field, which
        # is the case that must not be mistaken for a tax of zero.
        tax_paise=Field(
            value=tax_paise,
            confidence=0.0 if tax_paise is None else confidence,
            source="test",
        ),
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


def what_the_laws_were_run_on(seen: object) -> object:
    """The amount an honest caller would say its conservation figures came from.

    The reader's own total, because that is where a real caller's figures come
    from: `gate.py` passes the very same value to `conservation.run` and to
    `Situation.checked_paise`, which is the whole point of the field.

    When what was read is not whole paise, no conservation run could have used
    it, so the builder passes the clean total instead and leaves the module's
    own amount check to say what is wrong with the reading. A builder that
    passed the float on would move every malformed-amount test in this file
    onto a different branch from the one it was written for.

    Tests that are ABOUT the link say `checked_paise=` themselves.
    """
    total = seen.total_paise.value if type(seen) is Observation else None
    return total if type(total) is int else CLEAN_TOTAL


def a_situation(
    *,
    observation: object = UNSET,
    conservation: object = UNSET,
    checked_paise: object = UNSET,
    party_known: object = True,
    period_open: object = True,
    carries_gst: object = False,
    questions_asked: object = 0,
    debit_account: object = "Purchases",
    credit_account: object = "Cash",
    moment: object = Moment.BEFORE_THE_WRITE,
    #: `None`, because there was nothing to repair - which is what `None` means
    #: here. It is NOT "nobody looked": see `Situation.pdf_repaired`.
    pdf_repaired: object = None,
    ambiguous_fields: object = (),
    #: CHANGED 2026-08-13 BY OWNER DECISION 2, and it used to be nothing at all.
    #: These builders describe the bill that auto-posts, and after that decision
    #: a bill that auto-posts is one read off a text layer - so the clean
    #: default says so. The comment above `pdf_repaired` used to call this bill
    #: typed text; it is a text-layer PDF that needed no mending, which `None`
    #: covers ("there was nothing to repair").
    reading_tiers: object = (TEXT_LAYER,),
) -> Situation:
    seen = an_observation() if observation is UNSET else observation
    return Situation(
        observation=seen,  # type: ignore[arg-type]
        conservation=all_laws_pass() if conservation is UNSET else conservation,  # type: ignore[arg-type]
        checked_paise=(  # type: ignore[arg-type]
            what_the_laws_were_run_on(seen) if checked_paise is UNSET else checked_paise
        ),
        party_known=party_known,  # type: ignore[arg-type]
        period_open=period_open,  # type: ignore[arg-type]
        carries_gst=carries_gst,  # type: ignore[arg-type]
        questions_asked=questions_asked,  # type: ignore[arg-type]
        debit_account=debit_account,  # type: ignore[arg-type]
        credit_account=credit_account,  # type: ignore[arg-type]
        moment=moment,  # type: ignore[arg-type]
        pdf_repaired=pdf_repaired,  # type: ignore[arg-type]
        ambiguous_fields=ambiguous_fields,  # type: ignore[arg-type]
        reading_tiers=reading_tiers,  # type: ignore[arg-type]
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

    CORRECTED 2026-08-13. This asserted `Action.ASK`, and the owner closed the
    question that way round on that date: a conservation FAIL blocks, always,
    and it is now a hard rule. The assertion moved from ASK to BLOCK. What the
    test is FOR did not move an inch - certainty still does not outvote
    arithmetic, and it now loses harder than it did.
    """
    sure = an_observation(confidence=1.0)
    decided = decide(a_situation(observation=sure, conservation=one_law(Verdict.FAIL)))
    assert decided.action is Action.BLOCK
    assert decided.entry is None


def test_the_control_the_identical_bill_with_that_law_passing_is_posted() -> None:
    """THE CONTROL on the test above, and it carries real weight: without it,
    the assertion passes just as well in a module that refuses everything."""
    sure = an_observation(confidence=1.0)
    decided = decide(a_situation(observation=sure, conservation=all_laws_pass()))
    assert decided.action is Action.POST


def test_the_refusal_repeats_what_the_failing_law_actually_said() -> None:
    """ "The numbers do not add up" is not actionable. The law's own sentence
    names the figures and the difference, and that is what a person can check.

    CORRECTED 2026-08-13, twice, and this is the second one. It asserted
    `"out by 1 paise" in said`, which was a substring test that happened to start
    at the reason's first character - so it broke when `_spoken` started
    capitalising each reason, and it was testing the wrong thing anyway. What
    matters is that the FIGURES survive the join, not the case of the first
    letter.

    RENAMED the same day, when the owner made a conservation FAIL a hard rule:
    this is a refusal now and not a question, and a test named `..._the_ask_...`
    would have described an outcome the module no longer produces. The figures
    still have to reach the person - a block a person cannot check against the
    bill is as useless as a question they cannot answer.
    """
    broken = one_law(Verdict.FAIL, said="out by 1 paise on a 2,500 rupee bill.")
    decided = decide(a_situation(conservation=broken))
    assert decided.action is Action.BLOCK
    assert "1 paise on a 2,500 rupee bill" in decided.said


def test_a_bill_whose_numbers_do_not_add_up_is_refused_and_never_asked_about() -> None:
    """Owner decision, 2026-08-13, verbatim: "Conservation FAIL -> BLOCK,
    always. This is now a hard rule." No auto-post, no ask, at any confidence.

    Asserted for every one of the four laws by name rather than for the first
    one, because a rule written with the wrong law list is exactly the shape of
    defect that leaves one law still only asking.
    """
    for law in LAWS:
        decided = decide(a_situation(conservation=named_law(law, Verdict.FAIL)))
        assert decided.action is Action.BLOCK, law
        assert decided.entry is None, law


def test_the_refusal_for_a_bill_that_does_not_add_up_is_the_owners_own_words() -> None:
    """The literal, so the owner's sentence cannot drift into a paraphrase.

    Pinned here as a string rather than only against the module's constant: an
    assertion that imports the sentence it is checking proves the constant
    reaches `said` and nothing at all about what the constant says.
    """
    decided = decide(a_situation(conservation=one_law(Verdict.FAIL)))
    assert decided.said.startswith(
        "The numbers in this bill do not add up. Please check the original and "
        "upload a correct version."
    )
    assert NUMBERS_DO_NOT_ADD_UP in decided.said


def test_the_control_could_not_check_and_checked_and_wrong_stay_two_facts() -> None:
    """THE CONTROL the owner asked for by name, and it is not decoration.

    "I could not check this" and "I checked it and it does not add up" are
    different facts about a bill and they now share an outcome. A fix that
    collapsed them - one branch answering both - would pass every BLOCK
    assertion in this file while telling a person the wrong thing about their
    own bill: one of them means send a readable copy, the other means the
    figures on the page contradict each other.

    So both block, and the sentences must differ.
    """
    unchecked = decide(a_situation(conservation=one_law(Verdict.INDETERMINATE)))
    wrong = decide(a_situation(conservation=one_law(Verdict.FAIL)))

    assert unchecked.action is Action.BLOCK
    assert wrong.action is Action.BLOCK
    assert unchecked.said != wrong.said
    assert NUMBERS_DO_NOT_ADD_UP not in unchecked.said
    assert "not checked is not the same as checked and fine" in unchecked.said.lower()
    assert "not checked is not the same as checked and fine" not in wrong.said.lower()


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


# ---- the number that was checked is the number that gets written ------------
#
# The conservation verdicts are computed by the CALLER, from the caller's
# figures. The entry is built from `observation.total_paise`. Until 2026-08-13
# nothing compared the two, so laws that passed on 1,00,000 paise authorised a
# write of 1,00,00,000 paise and it came back POST - every law green, every
# field legible, and a hundred times the money.
#
# That is the whole cage in one line: it checked *a* number, which need not be
# *the* number. `demo_safety_cage.judge` happened to derive both from one dict,
# so no call site diverged - luck, not a guarantee, and the last gate is where
# a guarantee belongs.


def test_laws_that_passed_on_one_amount_cannot_authorise_a_write_of_another() -> None:
    """MEASURED before the fix: POST, carrying an entry for 10,000,000 paise.

    Nothing else here is wrong. Confidence is 1.0, all four laws PASS, the party
    is known and the books are open - which is exactly why this one matters.
    """
    seen = an_observation(total_paise=10_000_000)
    decided = decide(a_situation(observation=seen, checked_paise=100_000))
    assert decided.action is Action.BLOCK
    assert decided.entry is None


def test_the_control_the_same_bill_checked_on_its_own_amount_still_posts() -> None:
    """THE CONTROL, and it carries the weight: without it, a `decide` that
    refused every bill on earth would pass the test above and look like a fix."""
    seen = an_observation(total_paise=10_000_000)
    decided = decide(a_situation(observation=seen, checked_paise=10_000_000))
    assert decided.action is Action.POST
    assert decided.entry is not None
    assert decided.entry.amount_paise == 10_000_000


def test_one_paisa_between_what_was_checked_and_what_would_be_written_blocks() -> None:
    """Exact equality, the same as `conservation._compare` and for the same
    reason: a one-paisa gap is a misread digit far more often than it is a
    rounding artefact, and a tolerance would absorb the sharpest signal here."""
    decided = decide(a_situation(checked_paise=CLEAN_TOTAL - 1))
    assert decided.action is Action.BLOCK
    assert decided.entry is None


def test_a_caller_that_names_no_checked_amount_has_proved_nothing() -> None:
    """`None` is not "the amounts agree", it is "nobody said what was checked".
    Passing it through silently would leave the link asserted by nobody, which
    is the state this field was added to end."""
    decided = decide(a_situation(checked_paise=None))
    assert decided.action is Action.BLOCK
    assert decided.entry is None


def test_a_checked_amount_that_is_not_whole_paise_blocks() -> None:
    """`type(...) is not int` refuses `bool` as well, like every other amount in
    this module: `True == 1`, so a flag here would agree with a one-paisa bill."""
    for value in (250_000.0, "250000", True, ()):
        decided = decide(a_situation(checked_paise=value))
        assert decided.action is Action.BLOCK, value
        assert decided.entry is None, value


def test_the_refusal_names_both_amounts_through_the_one_formatter() -> None:
    """A person cannot act on "the amounts disagree". They can act on which two.
    `money.format_inr` or nothing - `1,000,000` is what `f"{n:,}"` produces and
    it is not what an Indian accountant reads."""
    seen = an_observation(total_paise=10_000_000)
    said = decide(a_situation(observation=seen, checked_paise=100_000)).said

    assert format_inr(100_000) in said
    assert format_inr(10_000_000) in said
    assert "10,000,000" not in said
    assert "checked_paise" not in said


def test_a_situation_that_does_not_say_which_amount_was_checked_cannot_be_built() -> (
    None
):
    """No default, for the same reason `period_open` and `moment` have none. A
    default of "whatever the reading says" would make the check compare a number
    against itself - a check that cannot fail wearing the face of one that
    passed, which is the shape this repository has already shipped once."""
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
            moment=Moment.BEFORE_THE_WRITE,
        )


# ---- a repaired file caps the outcome at ASK. It is a CEILING, not a rule. --
#
# Owner decision, 2026-08-13, verbatim: "If the PDF had to be repaired: in the
# decision layer, if conservation checks and all other rules pass, allow confirm
# (ask), but do NOT auto-post. If anything else is uncertain or fails, block
# with a plain sentence."
#
# So this is not a seventh hard rule and the difference is the whole of what
# these tests are for. A hard rule refuses; a ceiling lowers the BEST available
# outcome from post to ask and leaves everything else exactly where it was. The
# control below is what tells the two apart - a "ceiling" that refused every
# bill would pass both of the tests above it.


def test_a_repaired_file_that_is_otherwise_perfect_is_asked_about() -> None:
    """The bill is clean by every other measure - confidence 1.0, four laws
    passing, party known, books open, no tax. It still does not auto-post,
    because the bytes it was read from had to be mended first and a mended file
    is a second-hand account of what the paper said."""
    decided = decide(a_situation(pdf_repaired=True))

    assert decided.action is Action.ASK
    assert decided.entry is None


def test_the_control_a_file_that_needed_no_repair_still_posts() -> None:
    """THE CONTROL. It catches ONE specific way of getting this wrong, and the
    mutation run corrected what this docstring first claimed about it.

    A ceiling that fires on every bill whatever the flag says is killed HERE and
    nowhere else in this section - the tests above it all pass under that
    mutant, because every one of them only ever looks at the repaired side.

    What it does NOT catch, measured: a ceiling widened into a full block. Under
    that one `False` and `None` still post, so this stays green and the ask
    tests above are what go red. Two different ways to be wrong, one test each,
    and neither covers the other - which is why both halves are here rather than
    the one that reads like the important one.

    Both no-ceiling values, because they mean different things and must behave
    the same: `False` is "it is a PDF and it did not need repairing", `None` is
    "not a PDF, nothing to repair".
    """
    for nothing_to_repair in (False, None):
        decided = decide(a_situation(pdf_repaired=nothing_to_repair))
        assert decided.action is Action.POST, nothing_to_repair
        assert decided.entry is not None, nothing_to_repair


def test_a_repaired_file_with_anything_else_wrong_is_refused_not_asked_about() -> None:
    """The ceiling lowers the best outcome. It never RAISES the worst one.

    A blocking reason still blocks, and the failing-law case is the one worth
    pinning: the two decisions of 2026-08-13 meet on this bill, and a ceiling
    written as `return ASK` rather than as one more reason to ask would have
    turned a refusal into a question.
    """
    for how in (
        a_situation(pdf_repaired=True, conservation=one_law(Verdict.FAIL)),
        a_situation(pdf_repaired=True, period_open=False),
        a_situation(pdf_repaired=True, carries_gst=None),
    ):
        decided = decide(how)
        assert decided.action is Action.BLOCK
        assert decided.entry is None


def test_the_repaired_sentence_says_what_happened_and_what_to_do_about_it() -> None:
    """A person confirming an entry has to know WHY they are being asked. "I am
    not sure enough" is about the reading; this is about the file the reading
    came from, and the two send somebody to different places."""
    said = decide(a_situation(pdf_repaired=True)).said

    assert "repaired" in said
    assert "original" in said
    assert "pdf_repaired" not in said


def test_a_repair_flag_that_is_none_of_the_three_blocks() -> None:
    """Fail closed, like every other malformed field here. A value nobody can
    read is not evidence that nothing was repaired - it is nobody knowing, and
    the owner's own words cover it: "If anything else is uncertain or fails,
    block with a plain sentence"."""
    for value in ("yes", 1, 0, (), "false"):
        decided = decide(a_situation(pdf_repaired=value))
        assert decided.action is Action.BLOCK, value
        assert decided.entry is None, value


def test_a_situation_that_does_not_say_whether_it_was_repaired_cannot_build() -> None:
    """No default, for the same reason `party_known` and `moment` have none - and
    here the reason is sharper than usual, because the safe-looking default is
    the dangerous one. `pdf_repaired=None` means "nothing to repair" and grants
    the full post, so a default would hand every caller that forgot the exact
    permission this field exists to withhold. Forgetting has to fail loudly."""
    with pytest.raises(TypeError):
        Situation(  # type: ignore[call-arg]
            observation=an_observation(),
            conservation=all_laws_pass(),
            checked_paise=CLEAN_TOTAL,
            party_known=True,
            period_open=True,
            carries_gst=False,
            questions_asked=0,
            debit_account="Purchases",
            credit_account="Cash",
            moment=Moment.BEFORE_THE_WRITE,
        )


# ---- the reading tier is the second ceiling. Owner decision 2, 2026-08-13. --
#
# Owner decision 2, verbatim: "We do not hard-code 'photos never auto-post'.
# Instead, auto-post eligibility is controlled by reading tier + confidence +
# safety checks, not by media type alone." Auto-post needs all four of: a
# non-GST purchase, every check passing, confidence at or above
# `AUTO_POST_FLOOR`, AND a reading tier on the allowlist.
#
# "For this MVP the allowed reading tier list for auto-post is ["text_layer"]
# only. OCR-based reads (including photos processed by Tesseract) are not in the
# auto-post allowlist yet. So: photos can be read and shown to the user, they
# can go to ASK or BLOCK, but not AUTO-POST."
#
# It is the same SHAPE as the repaired-file ceiling above and the tests are laid
# out the same way, control first: a ceiling and a blanket refusal look
# identical from the refusing side, and without the control a change that
# refused every bill would pass every other test in this section.


def test_the_control_a_text_layer_read_at_the_auto_post_floor_still_posts() -> None:
    """THE CONTROL, and it is first because everything below it is a refusal.

    A tier check that allowed nothing - an empty allowlist, a comparison that
    can never be true - passes every other test in this section and fails only
    this one. Without it the section proves the cage can say no, which was never
    in doubt.

    At the floor rather than above it, so the two ceilings cannot be confused:
    this bill is exactly as sure as the owner's number demands and is refused by
    nothing.
    """
    seen = an_observation(confidence=AUTO_POST_FLOOR + 0.01)
    decided = decide(a_situation(observation=seen, reading_tiers=(TEXT_LAYER,)))

    assert decided.action is Action.POST
    assert decided.entry is not None


def test_an_ocr_read_at_the_same_confidence_is_asked_about_and_never_posted() -> None:
    """The same bill, the same 0.96, the same four passing laws - read off
    pixels instead of off the characters the producing program wrote.

    Confidence is not the whole of eligibility any more. An OCR reader that is
    sure of itself is still an OCR reader, and the owner has not cleared that
    tier to write into somebody's books unattended.
    """
    seen = an_observation(confidence=AUTO_POST_FLOOR + 0.01)
    decided = decide(a_situation(observation=seen, reading_tiers=(OCR,)))

    assert decided.action is Action.ASK
    assert decided.entry is None


def test_an_ocr_read_that_also_fails_a_hard_rule_is_still_refused() -> None:
    """THE ALLOWLIST IS A CEILING, NOT A BYPASS. It lowers the best available
    outcome and never raises the worst one.

    Written as an early `return ASK` the tier check would overturn a block - a
    photo of a bill whose numbers do not add up would become a question, which
    is the exact opposite of what the owner asked for. Written as one more
    reason to ask it can only ever lower post to ask.
    """
    for how in (
        a_situation(reading_tiers=(OCR,), conservation=one_law(Verdict.FAIL)),
        a_situation(reading_tiers=(OCR,), period_open=False),
        a_situation(reading_tiers=(OCR,), carries_gst=True),
    ):
        decided = decide(how)
        assert decided.action is Action.BLOCK
        assert decided.entry is None


def test_a_bill_read_by_two_tiers_needs_both_of_them_on_the_allowlist() -> None:
    """The weakest reading decides, exactly as `lowest_confidence` does.

    A ladder record can read the total off a text layer and guess the party off
    a photograph, and "some of this was read properly" is not the fact the owner
    granted the auto-post to. `any` here instead of `all` would post a bill
    whose supplier name came out of an OCR guess.
    """
    decided = decide(a_situation(reading_tiers=(TEXT_LAYER, OCR)))

    assert decided.action is Action.ASK
    assert decided.entry is None


def test_a_caller_that_names_no_tier_at_all_does_not_auto_post() -> None:
    """FAILS CLOSED, which is what makes the default on this field safe.

    An empty tuple is a caller that did not say how the bill was read, and
    nobody-said is not evidence of a text layer. `all()` over an empty tuple is
    True, so the emptiness is checked on its own - without that line a caller
    who stated nothing would clear a condition the owner wrote to be explicit.
    """
    decided = decide(a_situation(reading_tiers=()))

    assert decided.action is Action.ASK
    assert decided.entry is None


def test_the_tier_sentence_says_what_happened_and_names_no_tier_of_ours() -> None:
    """A person being asked has to know why. "I am not sure enough" is about the
    reading; this is about the WAY it was read, and a tier name is our
    vocabulary - `free_ocr` on somebody's screen explains nothing."""
    said = decide(a_situation(reading_tiers=(OCR,))).said

    assert "read this way" in said
    assert OCR not in said
    assert "tier" not in said


def test_the_auto_post_allowlist_holds_exactly_one_tier() -> None:
    """THE COUNT IS ASSERTED so the list cannot be widened quietly.

    Same technique as `test_adversarial_amounts_and_states.py::test_eight_of_
    the_thirteen_state_names_do_not_exist_in_the_shipped_package`: widening is
    allowed, but only together with this number, which is one line in a diff a
    reviewer cannot miss.

    The owner wrote `["text_layer"]`; the string here is `pdf_text_layer`,
    because that is what `extract/textlayer.py` actually stamps and an allowlist
    matching nothing would refuse every bill in the product. `tests/
    test_gate.py` binds the two so a rename of the reader's stamp breaks loudly.

    `typed_text` IS DELIBERATELY ABSENT. A person typing a sentence is not a
    pixel read and a case can be made for it, but the owner's list has one entry
    and this is not the place that decision gets made.
    """
    assert len(AUTO_POST_ALLOWED_TIERS) == 1
    assert frozenset({TEXT_LAYER}) == AUTO_POST_ALLOWED_TIERS
    assert "typed_text" not in AUTO_POST_ALLOWED_TIERS


def test_the_two_confidence_floors_are_still_exactly_where_the_owner_put_them() -> None:
    """OWNER DECISION 2 IS A CONFIGURATION CHANGE, NOT A THRESHOLD CHANGE, and
    this is the test that says so. The allowlist is a fourth condition beside
    the confidence band, not a way of moving it: a change that got the tier
    right by nudging 0.95 would pass every other test in this section."""
    assert AUTO_POST_FLOOR == 0.95
    assert ASK_FLOOR == 0.70


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


# ---- the tax flag and the tax figure are two statements about one fact ------
#
# The GST hard rule rested entirely on `carries_gst`, a flag the caller computed
# somewhere else, while the figure that contradicts it sat unread in the same
# argument. Measured before the fix: `carries_gst=False` with a reading showing
# 18,000 paise of tax came back POST and wrote 1,18,000 paise.
#
# This is the free conservation-style check - no labels, no model, no network,
# two statements about one fact compared - and it was not there.


def test_a_tax_figure_on_the_reading_contradicts_a_no_tax_flag_and_blocks() -> None:
    """MEASURED before the fix: POST, writing 1,18,000 paise of a GST bill.

    Neither statement is trusted over the other. They disagree, and a bill two
    sources describe differently is not a bill anybody may post unasked.
    """
    seen = an_observation(total_paise=118_000, tax_paise=18_000)
    decided = decide(a_situation(observation=seen, carries_gst=False))
    assert decided.action is Action.BLOCK
    assert decided.entry is None


def test_the_control_a_reading_that_shows_no_tax_agrees_with_the_flag() -> None:
    """THE CONTROL. Without it this fix could be "block every bill" and pass.
    A tax of exactly zero, read as confidently as everything else, is the two
    sources AGREEING - which is the ordinary case and it has to still post."""
    seen = an_observation(total_paise=118_000, tax_paise=0)
    decided = decide(a_situation(observation=seen, carries_gst=False))
    assert decided.action is Action.POST
    assert decided.entry is not None


def test_a_tax_field_nobody_read_is_not_evidence_that_there_is_no_tax() -> None:
    """The one that must NOT fire, and the reason the check is a type test and
    not a truth test. An unread field is `None` at confidence 0.0, which is
    every bill with no tax line on it - firing here would refuse them all, and
    at 100:1 a false block is cheap but a whole CLASS of them is not."""
    seen = an_observation(tax_paise=None)
    said = decide(a_situation(observation=seen, carries_gst=False)).said
    assert "do not agree" not in said


def test_the_control_that_unread_tax_bill_is_refused_for_being_unreadable() -> None:
    """THE CONTROL on the test above: the sentence is absent because the tax
    check did not fire, not because the bill sailed through. An unread field
    drags `lowest_confidence` to 0.0, which is under the ask floor."""
    seen = an_observation(tax_paise=None)
    decided = decide(a_situation(observation=seen, carries_gst=False))
    assert decided.action is Action.BLOCK
    assert decided.entry is None


def test_a_tax_field_that_is_not_money_at_all_is_refused_not_waved_through() -> None:
    """Not `None` and not a whole-paise zero, so it is not agreement. A string
    where a tax amount belongs is somebody's parser having failed, and reading
    it as "no tax" is the coercion this whole cage exists to refuse."""
    for value in ("18000", 180.0, True):
        seen = an_observation(tax_paise=value)
        decided = decide(a_situation(observation=seen, carries_gst=False))
        assert decided.action is Action.BLOCK, value
        assert decided.entry is None, value


def test_the_tax_disagreement_says_so_plainly_and_names_the_figure() -> None:
    """A person has to be able to check which two things disagreed, so the
    figure is named - through `money.format_inr`, the one renderer."""
    seen = an_observation(total_paise=118_000, tax_paise=18_000)
    said = decide(a_situation(observation=seen, carries_gst=False)).said

    assert "do not agree" in said
    assert format_inr(18_000) in said
    assert "nothing was posted" in said
    assert "tax_paise" not in said


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


# ---- two reasons are two sentences ------------------------------------------


def test_a_second_reason_starts_a_sentence_rather_than_continuing_the_last() -> None:
    """Joining on a space alone produced "...checking with you first. the line
    items on this bill...". A lower-case word after a full stop reads as a
    sentence that broke, and a person reading a refusal is already looking for
    a reason not to trust it."""
    broken = named_law(
        LAWS[1],
        Verdict.FAIL,
        said="the line items on this bill do not add up to its total.",
    )
    decided = decide(a_situation(conservation=broken))

    assert len(decided.reasons) > 1
    assert "The line items on this bill" in decided.said
    assert ". the " not in decided.said


def test_the_control_one_reason_is_not_mangled_on_its_way_to_the_sentence() -> None:
    """THE CONTROL, and it is the one that matters: `str.capitalize()` passes
    the test above and turns "GST" into "Gst" on the way past. One reason, with
    capitals that are not its first letter, and the owner's wording intact."""
    decided = decide(a_situation(carries_gst=True))

    assert len(decided.reasons) == 1
    assert decided.said == decided.reasons[0]
    assert GST_IS_OFF in decided.said


# ---- a refusal names the bill it is about, and says what to do next ---------


def test_the_tax_refusal_says_what_to_do_next_instead_of_stopping_dead() -> None:
    """A refusal with no next step leaves a person holding a bill and no move.
    Every other sentence in the module ends with one; this one ended at "cannot
    be posted automatically"."""
    assert "in Tally yourself" in decide(a_situation(carries_gst=True)).said


def test_the_control_the_tax_next_step_is_the_words_checks_py_already_uses() -> None:
    """THE CONTROL on the wording, not the behaviour. `tax_lines_can_be_posted`
    answers the same situation one layer down, and a third phrasing for the same
    instruction is how a product ends up telling a person two different things
    about one bill. Asserted against the check's own sentence so the two cannot
    drift apart in silence."""
    carrying_tax = Voucher(
        id="v-1",
        date=datetime.date(2026, 8, 12),
        party="Blue Steel Traders",
        narration="",
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=250_000,
        gst_paise=45_000,
    )
    shared = "enter this one in Tally yourself"

    assert shared in tax_lines_can_be_posted(carrying_tax, ()).detail
    assert shared in decide(a_situation(carries_gst=True)).said


def test_a_closed_period_refusal_names_the_date_it_is_refusing() -> None:
    """With four bills in flight, "the books for this date are closed" does not
    tell a person which one, and a refusal they cannot attach to a bill is a
    refusal they cannot act on."""
    assert "2026-08-12" in decide(a_situation(period_open=False)).said


def test_an_unknown_party_refusal_names_the_party_it_does_not_know() -> None:
    """ "I do not know who this bill is from" leaves the person guessing which
    of the names we did not recognise."""
    seen = an_observation(party="Kumar & Sons")
    assert (
        "Kumar & Sons" in decide(a_situation(observation=seen, party_known=False)).said
    )


def test_a_refusal_that_names_money_renders_it_through_the_one_formatter() -> None:
    """`money.format_inr` or nothing. A second renderer here would group the
    digits for a reader who does not exist - `10,00,000` is what an Indian
    accountant reads and `1,000,000` is what `f"{n:,}"` produces."""
    seen = an_observation(total_paise=1_000_000, confidence=0.80)
    said = decide(a_situation(observation=seen)).said

    assert format_inr(1_000_000) in said
    assert "1,000,000" not in said


def test_the_control_a_refusal_invents_nothing_when_nothing_was_read() -> None:
    """THE CONTROL on all four above. A naming clause built without checking
    what it was handed prints "None" or a bare number at a person, which is
    worse than saying nothing - and inventing a bill reference is forbidden
    outright because no such reference exists."""
    seen = Observation(
        date=Field(value="2026-08-12", confidence=0.99, source="test"),
        party=Field(value=None, confidence=0.0, source="not_found: no party"),
        total_paise=Field(value=None, confidence=0.0, source="not_found: no total"),
        tax_paise=Field(value=0, confidence=0.99, source="test"),
    )
    said = decide(a_situation(observation=seen, period_open=False)).said

    assert "None" not in said
    assert "2026-08-12" in said


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


# ---- recovered 2026-08-13 ---------------------------------------------------
# These three were written after a mutation run and a branch-coverage
# measurement, then DELETED FROM DISK - not by anyone's edit, but by the
# pre-commit hook. `prek` stashes unstaged changes to a patch file and restores
# them afterwards; it timed out at two minutes in this shared tree and the
# restore never ran, so the working copy simply lost them.
#
# Recovered from /Users/tanveersidhu/.cache/prek/patches/1786579290388-57829.patch
# and re-verified against the restructured module rather than pasted back on
# trust. Each one closes a branch that no other test reached, which is exactly
# the kind of test whose loss nothing would have reported.


def test_the_refusal_for_an_amount_that_is_not_money_uses_no_code_names() -> None:
    """WRITTEN AFTER A MUTATION RUN, which is the only reason it exists.

    Deleting the module's own amount check survived every other test here: the
    wall refuses a float too, and the block catches it, so the outcome never
    moved. What moved was the sentence - to "amount_paise must be a whole number
    of paise, not float", which is our variable name on a stranger's screen.

    So the outcome was never what that branch was for, and no test said so.
    This one does.
    """
    seen = an_observation(total_paise=250_000.5)
    said = decide(a_situation(observation=seen)).said
    assert "amount_paise" not in said
    assert "float" not in said


def test_something_that_is_not_a_verdict_at_all_blocks() -> None:
    """A tuple of the right length and the right shape, holding a bare string
    where a result belongs. Found by measuring which branches no test reached -
    the module refuses it, and until now nothing said so."""
    not_a_result = ("debits_equal_credits: fine", *all_laws_pass()[1:])
    assert decide(a_situation(conservation=not_a_result)).action is Action.BLOCK


def test_a_decision_whose_sentence_is_only_whitespace_cannot_be_constructed() -> None:
    """Reasons and the sentence are separate fields, so a decision can have one
    and not the other. Found by measuring which branches no test reached: a
    refusal that renders as an empty box on the page is a refusal the person
    cannot act on, and it would have shipped."""
    with pytest.raises(ValueError, match="sentence"):
        Decided(action=Action.BLOCK, said="   ", reasons=("no",))

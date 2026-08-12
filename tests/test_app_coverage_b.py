"""The web app's rendering helpers, called directly. No server, no socket.

WHY THIS FILE EXISTS
--------------------
`accountant/web/app.py` lines 1500-2700 hold the money formatters, the
provenance readers and the banner that says a document could not be read. They
are pure functions over a `Draft`, so they can be measured with nothing in the
way — no port, no thread, no Tally.

The brief for this file said app.py was at 28% with 560 statements missed. That
number came from the repository's gitignored `.coverage` artefact, last written
2026-08-11 15:33 by a partial run. MEASURED against the whole suite on
2026-08-12, before a line of this file existed:

    accountant/web/app.py   846 stmts   15 missed   220 branch   13 partial   97%

and in the 1500-2700 region exactly TWO statements were unreached:

    1953   `found.append(one)` in `decision_citations` — the carrier that names
           ONE rule rather than a tuple of them. Every citation test in the
           suite went through the plural field, so the singular branch had
           never run.
    2643   `return ""` in `unread_document` — the case where SOME of the four
           fields were read. `tests/test_upload.py` drives the all-unread case
           over HTTP; nothing drove the other side of that comparison, so a
           banner that appeared on every document would have passed.

Both are closed here, each with its paired control. The rest of this file is
claim coverage rather than line coverage: `rupees`, `money` and `esc` were
already executed by other tests, but several things they must do — the sign
identity a negative amount has to satisfy, the refusal of a whole-numbered
float, the escaping of markup on the way through the `money` fallback — were
executed without being asserted anywhere.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about real TallyPrime, or about HTTP. Nothing here binds a socket and
nothing here reaches 127.0.0.1: every subject is a function taking a value and
returning a string.

That every rendering below is the RIGHT one. Two were pinned here as
MEASURED-NOT-ENDORSED and argued against in their own docstrings — thousands
grouped `1,000,000.00` rather than the Indian `10,00,000.00`, and a negative
printing `₹-4,200.50` rather than `-₹4,200.50`. Both were reported. ONE of them
came back: the owner ruled on 2026-08-13 that INR is grouped the Indian way,
and that assertion now pins the corrected rendering. `accountant/money.py` is
where the decision lives.

The sign position is STILL only measured. It was reported in the same breath
and not ruled on, so it stays exactly as it was and stays argued against in its
own docstring. A test file does not get to take the owner's silence for
agreement, and a change that was authorised to fix the commas does not get to
carry a second opinion along with it. It is open in `docs/OWNER_WORK.md`.

EVIDENCE CLASS
--------------
`SYNTHETIC_EVIDENCE`. Every draft below is fabricated in this file to exercise
one branch. No real bill, no real company, no accuracy claim.
"""

from __future__ import annotations

import dataclasses
import datetime
from collections.abc import Sequence

import pytest

from accountant import pipeline
from accountant.extract.adapter import NOT_FOUND, ExtractedRecord
from accountant.memory.company import LiveDisagreement
from accountant.rules.gst_rates import RateRule, official_corpus
from accountant.rules.provenance import Citation
from accountant.schema import Decision, Flag, Outcome, Voucher
from accountant.web import app

# ---- builders ---------------------------------------------------------------
#
# Dataclass constructors, not a spin-up path. `tests/conftest.py` re-exports the
# threaded server fixture rather than letting two files own one, and that
# argument holds for anything with a lifecycle; these are three-line factories
# over frozen values, and importing them out of `tests/test_ui_provenance.py`
# would drag that module's server fixtures into a file that binds no socket.


def a_record(**stated: str) -> ExtractedRecord:
    """A record whose per-field sources are exactly what the caller names.

    Every field defaults to `not_found`, which is what the shipped
    `PlaceholderReader` returns today, so a test names only the field it is
    changing.
    """
    sources = dict.fromkeys(ExtractedRecord.FIELDS, NOT_FOUND) | stated
    return ExtractedRecord(
        date=None,
        party=None,
        total_paise=None,
        tax_paise=None,
        per_field_source=sources,
    )


def a_draft(
    *,
    record: ExtractedRecord | None = None,
    decision: Decision | None = None,
    flags: Sequence[Flag] = (),
    suppressed: Sequence[Flag] = (),
    conflict: LiveDisagreement | None = None,
) -> pipeline.Draft:
    """A draft carrying only what a test puts on it."""
    return pipeline.Draft(
        id="draft-b",
        company=app.COMPANY,
        voucher=Voucher(
            id="v-b",
            date=datetime.date(2026, 8, 12),
            party="Sharma Traders",
            narration="cement",
            debit_account="Purchases",
            credit_account="Cash",
            amount_paise=420_050,
        ),
        record=record if record is not None else a_record(),
        operation_id="op-b",
        flags=list(flags),
        suppressed_flags=list(suppressed),
        decision=decision,
        memory_conflict=conflict,
    )


@dataclasses.dataclass(frozen=True)
class FlagCitingOneRule(Flag):
    """A carrier that names a SINGLE rule, in the singular field.

    `decision_citations` reads both `citations` (a tuple, what
    `accountant.tax.decision.TaxDecision` carries) and `citation` (one, what a
    detector firing on exactly one rule would carry). Nothing in the shipped
    package builds the second shape yet, which is why the branch that reads it
    had never been executed.
    """

    citation: Citation | None = None


@dataclasses.dataclass(frozen=True)
class DecisionCitingRules(Decision):
    """The plural carrier, for the test that shows both are read at once."""

    citations: tuple[Citation, ...] = ()


def a_loaded_rule() -> RateRule:
    """One real rule out of the merged corpus. Never a hand-written URL.

    A URL typed into a test is a URL that can disagree with the corpus and stay
    green, which is the same defect as an invented citation with a slower fuse.
    """
    corpus = official_corpus()
    assert corpus.loaded, "the merged corpus loaded no rules"
    return corpus.loaded[0]


def flag_citing(citation: Citation) -> FlagCitingOneRule:
    return FlagCitingOneRule(
        voucher_id="v-b",
        detector="gst_anomaly",
        severity=1,
        reason="the rate on this bill is not the rate the corpus holds",
        citation=citation,
    )


def decided(outcome: Outcome = Outcome.VALID) -> Decision:
    return Decision(outcome=outcome, reason="nothing to report")


# ---- rupees: the sign, and what a formatter must refuse ---------------------


@pytest.mark.parametrize("magnitude", [1, 99, 100, 420_050, 9_223_372_036_854_775])
def test_a_negative_amount_is_the_positive_one_with_a_minus_sign_in_front(
    magnitude: int,
) -> None:
    """A3 written as the identity the old formatter broke.

    `tests/test_money.py` pins seven individual renderings. This pins the
    RELATION between them, which is the thing that was actually wrong: floored
    division moved the rupees away from zero and left the paise where they
    were, so the negative rendering stopped being the positive one with a sign
    on it. Stated as a relation it holds at every magnitude, including the one
    a double cannot represent.
    """
    assert app.rupees(-magnitude) == "-" + app.rupees(magnitude)


@pytest.mark.parametrize("magnitude", [1, 99, 420_050])
def test_the_control_the_floored_formatter_this_replaced_breaks_that_identity(
    magnitude: int,
) -> None:
    """THE CONTROL. Without it the test above passes for any formatter at all.

    This is the exact expression `rupees` carried until 2026-08-09. Note that
    it is CORRECT for whole rupees — `-100` renders `-1.00` either way — which
    is why the defect read as a rounding style rather than as an error, and why
    the magnitudes here are the ones that carry paise.
    """
    floored = f"{-magnitude // 100:,}.{-magnitude % 100:02d}"

    assert floored != "-" + app.rupees(magnitude)


def test_the_sign_appears_at_one_paise_below_zero_and_not_at_zero() -> None:
    """WHERE the boundary sits, which is the part `tests/test_money.py` does not
    state: it pins `0` and `-1` as separate renderings and nothing says they are
    adjacent. A formatter written `"-" if paise <= 0` prints `-0.00` for a
    balanced ledger line, which reads as a debt of nothing."""
    assert app.rupees(0) == "0.00"
    assert app.rupees(-1) == "-0.01"
    assert not app.rupees(0).startswith("-")


@pytest.mark.parametrize(
    "value",
    [420_050.0, 10.5, "420050", None, [420_050], datetime.date(2026, 8, 12)],
)
def test_an_amount_that_is_not_integer_paise_is_refused_and_the_type_is_named(
    value: object,
) -> None:
    """The refusal has to say what arrived, or the traceback names nothing.

    `420_050.0` is the case worth having: a float that IS a whole number of
    paise. A formatter lenient about it accepts the same class of value that
    `amount_is_integer_paise` exists to catch, and does so on exactly the
    inputs where the leniency is invisible.
    """
    with pytest.raises(TypeError, match="integer paise") as refused:
        app.rupees(value)  # type: ignore[arg-type]

    assert type(value).__name__ in str(refused.value)


def test_the_control_the_same_amount_as_an_integer_renders_without_complaint() -> None:
    """THE CONTROL for the refusals above: it is the TYPE being refused, not
    the number. `420050` and `420050.0` are the same quantity."""
    assert app.rupees(420_050) == "4,200.50"


def test_both_boolean_values_render_as_the_integers_they_are() -> None:
    """`bool` is an `int`, so it reaches the formatter rather than the refusal.

    `tests/test_money.py` pins `True`. `False` is the half that would still
    render if the branch read `if paise is True`.
    """
    assert app.rupees(True) == "0.01"
    assert app.rupees(False) == "0.00"


def test_thousands_are_grouped_in_the_indian_style_and_not_the_western_one() -> None:
    """DECIDED 2026-08-13. ₹10 lakh renders `10,00,000.00` in India.

    This assertion used to pin the opposite, as MEASURED-NOT-ENDORSED:
    `f"{whole:,}"` is Python's western grouping, so the product showed an
    Indian accountant `1,000,000.00`. That was reported rather than changed,
    because a test file does not get to decide what a product looks like. The
    owner decided, and the assertion turns round with the decision.
    """
    assert app.rupees(100_000_000) == "10,00,000.00"
    assert app.rupees(100_000_000) != "1,000,000.00"


# ---- money: the page's formatter, which is never allowed to raise -----------


def test_the_rupee_sign_goes_in_front_of_the_minus_sign() -> None:
    """MEASURED, NOT ENDORSED, and the second of the two. A negative balance
    prints `₹-4,200.50`; the convention on an Indian statement is
    `-₹4,200.50`. Same objection as the grouping above, same disposition.

    STILL UNRULED as of 2026-08-13. The grouping beside it was decided and
    changed; this was not, so it did not move. `accountant/money.py::format_inr`
    is now the one place that would have to change, which makes the open
    question cheaper to answer, not answered.
    """
    assert app.money(-420_050) == "₹-4,200.50"
    assert app.money(420_050) == "₹4,200.50"


@pytest.mark.parametrize("value", [10.5, 4200.0, "4200", None])
def test_a_value_rupees_refuses_is_printed_as_it_is_and_marked_not_an_amount(
    value: object,
) -> None:
    """The page is not allowed to fail, and it is not allowed to invent either.

    A non-integer amount is what makes an entry NOT_VALID through
    `amount_is_integer_paise` — so the one draft a person MOST needs to see was
    the one that raised while being drawn. It degrades to the true statement
    instead.
    """
    assert app.money(value) == f"{value} (not an amount)"


@pytest.mark.parametrize("value", [10.5, 4200.0, "4200", None])
def test_the_control_every_one_of_those_values_makes_rupees_itself_raise(
    value: object,
) -> None:
    """THE CONTROL. Without it, `money` returning the fallback proves nothing:
    the values might simply be renderable and the branch never taken."""
    with pytest.raises(TypeError):
        app.rupees(value)  # type: ignore[arg-type]


def test_markup_reaching_the_money_fallback_is_escaped_rather_than_rendered() -> None:
    """`Voucher.amount_paise` is annotated `int` and nothing enforces it.

    The annotation is not checked at runtime, and the whole reason this
    fallback exists is the value that arrives anyway. Whatever arrives is
    printed, so whatever arrives has to be escaped — this is a value going
    straight into a page.
    """
    out = app.money('<script>alert("x")</script>')

    assert "<script>" not in out
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in out
    assert out.endswith("(not an amount)")


def test_the_control_that_markup_is_what_sends_money_down_the_fallback() -> None:
    """THE CONTROL: a `str` really is refused by `rupees`, so the escaped
    output above came from the fallback and not from a formatted amount."""
    with pytest.raises(TypeError, match="integer paise"):
        app.rupees("<script>")  # type: ignore[arg-type]


# ---- esc --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "shown"),
    [("<", "&lt;"), (">", "&gt;"), ("&", "&amp;"), ('"', "&quot;"), ("'", "&#x27;")],
)
def test_every_character_that_can_change_the_shape_of_the_page_is_escaped(
    raw: str, shown: str
) -> None:
    """Both quote characters included. `esc` is used inside `data-` attributes
    that are written unquoted and single-quoted in places, so an escaper that
    handled `<` and `>` alone would still let an attribute be closed early."""
    assert app.esc(raw) == shown


def test_a_non_string_is_rendered_through_str_before_it_is_escaped() -> None:
    """`esc(s: object)`, and it is called on non-strings on the live path —
    `money` hands it whatever failed to be an amount. An escaper that assumed
    `str` would raise while the refusal page was being drawn."""

    class Hostile:
        def __str__(self) -> str:
            return '<img src=x onerror="alert(1)">'

    assert app.esc(Hostile()) == "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"
    assert app.esc(None) == "None"
    assert app.esc(420_050) == "420050"


def test_the_control_a_sentence_with_nothing_to_escape_comes_back_unchanged() -> None:
    """THE CONTROL. An escaper that mangled ordinary text would pass every
    test above and make every reason on every page unreadable."""
    assert app.esc("paid Sharma Traders 4200 for cement") == (
        "paid Sharma Traders 4200 for cement"
    )


# ---- decision_citations: the carrier that names ONE rule (app.py:1953) ------


def test_a_carrier_naming_one_citation_is_read_as_well_as_one_naming_many() -> None:
    """app.py:1953, unreached by the whole suite before this test.

    `decision_citations` reads a plural `citations` and a singular `citation`,
    and every citation test in the repository went through the plural. A
    detector that fires on exactly one rule is the shape that carries the
    singular, and the line that appends it had never run.
    """
    rule = a_loaded_rule()
    draft = a_draft(
        decision=DecisionCitingRules(
            outcome=Outcome.VALID, reason="ok", citations=(rule.citation,)
        ),
        flags=[flag_citing(rule.citation)],
    )

    assert app.decision_citations(draft) == [rule.citation, rule.citation]


def test_the_control_a_carrier_naming_neither_field_contributes_nothing() -> None:
    """THE CONTROL. `getattr` with a default returns `()` and `None` for a
    plain `Flag`, and a reader that appended the default would put an empty
    tuple or a `None` into the citation list and render it as a source."""
    plain = Flag(
        voucher_id="v-b",
        detector="vendor_switch",
        severity=1,
        reason="this vendor usually goes somewhere else",
    )

    assert app.decision_citations(a_draft(decision=decided(), flags=[plain])) == []


def test_a_concern_the_display_cap_hid_still_contributes_its_citation() -> None:
    """`FLAG_CAP` is a DISPLAY decision and the owner's rule when setting it was
    "never lose concerns from the audit/evidence record". A citation carried by
    a suppressed flag is evidence for the decision it contributed to.

    `decision=None` here also drives the `carrier is None` skip, which is the
    state a draft is in before `evaluate` has run.
    """
    rule = a_loaded_rule()

    found = app.decision_citations(
        a_draft(decision=None, suppressed=[flag_citing(rule.citation)])
    )

    assert found == [rule.citation]


def test_a_single_citation_naming_a_corpus_rule_shows_the_corpus_url_and_date() -> None:
    """The singular carrier, end to end, through the panel that renders it.

    The URL is checked against the CORPUS's own rule rather than against
    `cited_source`, which would be comparing the renderer to itself.
    """
    rule = a_loaded_rule()

    shown = app.rule_source_url(
        a_draft(decision=None, flags=[flag_citing(rule.citation)])
    )

    assert rule.source.url in shown
    assert str(rule.source.retrieval_date) in shown
    assert not shown.startswith(app.NOT_AVAILABLE)


def test_a_single_citation_claiming_a_url_the_corpus_denies_is_never_repeated() -> None:
    """THE CONTROL for the test above, and the property that matters more.

    A citation is a CLAIM. When the claimed URL disagrees with the corpus the
    panel says so and DROPS the claimed address — a provenance panel that
    repeats an unverifiable URL is the exact failure it exists to catch.
    """
    rule = a_loaded_rule()
    lying = dataclasses.replace(rule.citation, source_url="https://example.invalid/x")

    shown = app.rule_source_url(a_draft(decision=None, flags=[flag_citing(lying)]))

    assert shown.startswith(app.NOT_AVAILABLE)
    assert app.URL_DISAGREES in shown
    assert "example.invalid" not in shown


def test_a_single_citation_naming_a_rule_the_corpus_does_not_hold_is_refused() -> None:
    """The other refusal: the id resolves to nothing, so there is nothing here
    to check the claim against and the claim is not repeated."""
    rule = a_loaded_rule()
    unknown = dataclasses.replace(rule.citation, rule_id="no-such-rule-in-the-corpus")

    shown = app.rule_source_url(a_draft(decision=None, flags=[flag_citing(unknown)]))

    assert shown.startswith(app.NOT_AVAILABLE)
    assert app.NOT_IN_CORPUS in shown
    assert "no-such-rule-in-the-corpus" in shown


# ---- unread_document: the side where something WAS read (app.py:2643) -------


@pytest.mark.parametrize("read_field", list(ExtractedRecord.FIELDS))
def test_a_document_with_even_one_field_read_carries_no_nothing_was_read_banner(
    read_field: str,
) -> None:
    """app.py:2643, unreached by the whole suite before this test.

    The banner is MEASURED off the record — "every one of the four named fields
    came back not_found" — so that the day a real reader is selected and reads
    three fields out of four, it stops appearing on its own. Only the all-unread
    side was ever driven, so a banner that appeared on every document, read or
    not, would have passed.
    """
    draft = a_draft(record=a_record(**{read_field: "some_real_reader"}))

    assert app.unread_document(draft) == ""


def test_the_control_a_document_with_every_field_unread_does_carry_the_banner() -> None:
    """THE CONTROL. Without it, a function that returned "" unconditionally
    passes all four cases above."""
    assert "data-unread=document" in app.unread_document(a_draft(record=a_record()))


def test_the_banner_is_decided_on_the_not_found_prefix_not_on_one_fixed_sentence() -> (
    None
):
    """`PlaceholderReader` does not say `not_found`. It says `not_found: no
    production reader is configured, ...` — the marker plus the reason. A
    comparison against the bare constant would miss the shipped backend's own
    wording, which is the only wording this banner is ever shown for."""
    spelled_out = dict.fromkeys(
        ExtractedRecord.FIELDS, f"{NOT_FOUND}: nobody has chosen a document reader"
    )

    banner = app.unread_document(a_draft(record=a_record(**spelled_out)))

    assert "data-unread=document" in banner
    assert "Nothing was read from that file." in banner


def test_the_control_a_source_that_merely_mentions_not_found_is_not_a_refusal() -> None:
    """THE CONTROL on the prefix rule: it is a PREFIX, not a substring. A
    reader reporting `read_ok, not_found_fields=0` has read the field, and a
    substring test would call that document unread."""
    read_anyway = dict.fromkeys(ExtractedRecord.FIELDS, f"a_reader: {NOT_FOUND}=0")

    assert app.unread_document(a_draft(record=a_record(**read_anyway))) == ""


# ---- decision_evidence: the memory conflict, paired account by account ------


def test_every_live_account_in_the_evidence_line_carries_its_own_count() -> None:
    """D-06's evidence line, which pairs two tuples with `zip(strict=True)`.

    The counts are the point: one stray row against forty is a different
    conversation from sixty against forty, and a line that paired an account
    with the wrong count would be worse than a boolean.
    """
    conflict = LiveDisagreement(
        company_key="k",
        subject="verma cement",
        remembered_account="Purchases",
        remembered_times=6,
        live_accounts=("Repairs & Maintenance", "Purchases"),
        live_times=(4, 6),
    )

    line = app.decision_evidence(a_draft(decision=decided(), conflict=conflict))[-1]

    assert "Repairs & Maintenance 4 time(s), Purchases 6 time(s)" in line
    assert "memory says Purchases 6 time(s)" in line


def test_the_control_a_mismatched_pair_cannot_be_built_so_the_page_cannot_raise() -> (
    None
):
    """THE CONTROL for the `strict=True` above, which is the interesting half.

    Strict zip RAISES on unequal lengths, and raising here would take down the
    whole page while it was being drawn. It cannot happen because the type
    refuses the mismatched pair at construction — so the guard is asserted
    where it lives rather than assumed.
    """
    with pytest.raises(ValueError, match="count"):
        LiveDisagreement(
            company_key="k",
            subject="verma cement",
            remembered_account="Purchases",
            remembered_times=6,
            live_accounts=("Repairs & Maintenance", "Purchases"),
            live_times=(4,),
        )


def test_a_draft_with_no_checks_no_flags_and_no_conflict_records_no_evidence() -> None:
    """The empty case, which `provenance_slots` turns into `NOT_RECORDED`
    rather than an empty cell. Reachable, and pinned rather than assumed
    unreachable — a blank cell and a field with no source look identical."""
    assert app.decision_evidence(a_draft(decision=decided())) == []
    assert app.provenance_slots(a_draft(decision=decided()))["evidence"] == (
        app.SLOT_NOT_RECORDED,
        app.NOT_RECORDED,
    )


# ---- REVIEW NOTES -----------------------------------------------------------
#
# Read back adversarially on 2026-08-12, asking of each test what would still
# be green if the thing under it were broken. Five weaknesses came out. Four are
# CLOSED in the file above; two of those four are the changes that most needed
# making. The remaining two are recorded rather than done, with the reason.
#
# 1. CLOSED — a citation test that reads `shown == app.cited_source(rule)`
#    compares the renderer to itself: `rule_source_url` CALLS `cited_source`, so
#    that assertion holds for any string `cited_source` chooses to return,
#    including an empty one. `test_a_single_citation_naming_a_corpus_rule_shows_
#    the_corpus_url_and_date` asserts the corpus's own `source.url` and
#    `source.retrieval_date` instead — facts about the corpus, not about the
#    renderer that is being measured.
#
# 2. CLOSED — the `not_found` prefix rule was asserted only from the positive
#    side, so an implementation testing `NOT_FOUND in text` rather than
#    `startswith` would have passed every case. `test_the_control_a_source_that_
#    merely_mentions_not_found_is_not_a_refusal` is that missing side: a source
#    reading `a_reader: not_found=0` contains the marker without starting with
#    it, and a substring implementation calls that read document unread.
#
# 3. CLOSED — a refusal test that only asserts `TypeError` measures nothing a
#    person can act on; a refusal naming neither the value nor the type is the
#    defect A4 was about. `test_an_amount_that_is_not_integer_paise_is_refused_
#    and_the_type_is_named` asserts the arriving type appears in the message,
#    and its parameters include `420_050.0` — a float that IS a whole number of
#    paise, the case where leniency would be invisible.
#
# 4. CLOSED — a bare `rupees(0) == "0.00"` restates a case
#    `tests/test_money.py` already parametrises and adds nothing. What neither
#    file said was WHERE the sign boundary sits, so
#    `test_the_sign_appears_at_one_paise_below_zero_and_not_at_zero` pins `0`
#    and `-1` together: a formatter written `"-" if paise <= 0` prints `-0.00`
#    for a balanced ledger line and passes the single-value version.
#
# 5. NOT DONE — `decision_rule_kind` reports `rule` for any decision driven by
#    two or more problems, because it joins the ids with `"; "` and then asks
#    whether that joined string is a detector name. Two detectors firing at
#    once are therefore labelled a rule on the provenance panel. This is a
#    defect in `accountant/web/app.py`, and this task forbids touching it; a
#    test pinning the wrong behaviour would make the fix harder rather than
#    easier, so it is reported to the owner instead of asserted here.
#
# 6. NOT DONE — nothing here exercises `record()`, `note()` or `_run()`, which
#    also live in this region. They need a configured runtime and a store, and
#    `tests/test_action_log.py`, `tests/test_company_identity.py` and
#    `tests/test_adversarial_write_path.py` already drive all three with
#    measured coverage. A fourth spin-up of the same runtime would be a second
#    definition of what a connected app is — the argument `tests/conftest.py`
#    makes for re-exporting the server fixture instead of copying it.

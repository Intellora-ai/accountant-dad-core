"""PHASE 8 PR-4 — provenance on the draft screen, measured off the rendered page.

THE OWNER-APPROVED ASSUMPTION, `docs/OWNER_DECISIONS.md` §2, verbatim:

    provenance in UI = the existing draft screen displays detector/rule,
                       source URL, evidence and explanation per decision

THE REQUIRED RESULT

    20 decisions
    20/20 show detector or rule
    20/20 show source URL
    20/20 show evidence
    20/20 show explanation

WHY EVERY COUNT IS TAKEN OFF THE HTML AND NOT OFF THE DATACLASS
----------------------------------------------------------------
This project has been bitten by the difference exactly once already, and it is
worth restating rather than referencing. `Draft.dropped_flags` was computed
correctly and never rendered, so criterion #3.6 — "overflow reported as a count,
never silently dropped" — was true inside the object and FALSE on the screen a
person reads. `tests/test_flag_cap.py` was written to stop that recurring; this
file is the same argument applied to provenance. A renderer can be right about
the draft and wrong about the page, and the page is the product.

So every count below is a regex over the bytes a real HTTP client received from
a real server, and the twenty decisions are produced by twenty real requests.

WHAT AN ABSENT SOURCE LOOKS LIKE, AND WHY THAT IS THE WHOLE POINT
------------------------------------------------------------------
A blank cell and a field with no source are indistinguishable on a screen, and
telling those two apart is the entire reason this feature exists. The frozen
plan defines the failure it is measuring:

    we hallucinate if and only if we output a value not derivable from the
    input document, the company's Tally history, or the rules corpus.
    Design consequence: every output value carries a source tag.

So a slot is never blank. It renders one of three states, and the state is
carried in `data-slot-state` rather than left to the prose:

    recorded        the draft carries this source and here it is
    not_recorded    the draft does not carry it — rendered `NOT_RECORDED`
    not_available   the half of the system that would supply it is not here —
                    rendered `NOT_AVAILABLE — accountant/rules/ not merged`

WHAT WAS ASSUMED ABOUT `accountant/rules/`
--------------------------------------------
It does not exist on `origin/main`. It is PR-3 of the owner's five and was being
built in parallel while this was written, so it could not be imported and its
contract could not be read — only designed against. The assumption made here is
narrow on purpose: **a rule's official source URL will arrive on the object that
already reaches this screen**, the `Decision` or the `Flag`, as an attribute
named `source_url`. `app.rule_source_url` reads exactly that and falls through
to the `NOT_AVAILABLE` marker when nothing carries it — which is every decision
today, 20 of 20. If the corpus lands with a different carrier, one function
changes and the page does not.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That any rule has a real citation. No decision on this screen carries a source
URL today and none is invented; the slot says so. That claim belongs to PR-3.

Anything about a real TallyPrime. The backend is `FakeTally` behind
`app.configure()`, over real HTTP.

That real bills extract this way. Extraction is `StubExtractor`-class work under
Q4 and real extraction accuracy stays `NOT_MEASURED`.

EVIDENCE CLASS
--------------
`SYNTHETIC_EVIDENCE`. Every one of the twenty entries is a line written in this
file against a fabricated company, chosen to exercise mechanics, schema and
provenance — which Q5 permits synthetic cases to do, and which is never to be
described as real-bill accuracy evidence.
"""

from __future__ import annotations

import dataclasses
import datetime
import re

import pytest

from accountant import pipeline
from accountant.extract.adapter import ExtractedRecord
from accountant.memory.company import LiveDisagreement
from accountant.problems import Problem
from accountant.schema import CheckResult, Decision, Flag, Outcome, Voucher
from accountant.web import app
from tests.test_first_detector import stale_ledger_company, stale_server
from tests.test_web import ACCOUNTS, demo_company, draft_id, fake_backend, post, serving

__all__ = ["stale_server"]

# THE FOUR SLOTS, WRITTEN OUT HERE RATHER THAN IMPORTED.
#
# This is the guard that makes "the evidence slot was dropped from the template"
# a failure. `app.PROVENANCE_SLOTS` is what the renderer iterates; a test that
# looped over the same tuple would pass just as happily with three entries in
# it, because it would be comparing the page to itself. The requirement is four
# named slots, so the requirement is written down independently and the module
# is checked against it.
REQUIRED_SLOTS = ("detector_or_rule", "source_url", "evidence", "explanation")

#: What `render_provenance` emits per slot. Matched on the ATTRIBUTES, never on
#: the label text: two tests earlier in this project were green and vacuous
#: because they searched a whole page for a common word the stylesheet already
#: contained.
SLOT_ROW = re.compile(
    r'<tr data-provenance-slot="([^"]*)" data-slot-state="([^"]*)">'
    r"<td>[^<]*</td><td>([^<]*)</td></tr>"
)

BLOCK = re.compile(r'<div data-provenance="decision"[^>]*>(.*?)</table></div>', re.S)

ASK = re.compile(r"<p class=ask>(.*?)</p>", re.S)


def slots(page: str) -> dict[str, tuple[str, str]]:
    """Every provenance slot on the page, as `{slot: (state, text)}`."""
    return {slot: (state, text) for slot, state, text in SLOT_ROW.findall(page)}


def raw_cells(page: str) -> list[str]:
    """Every provenance value cell exactly as it was rendered, blanks included.

    Deliberately a SEPARATE and looser pattern from `SLOT_ROW`. `SLOT_ROW`
    matches `<td>([^<]*)</td>`, which is happy with an empty cell — but a
    renderer that emitted `<td></td>` for a whole row would still be found by
    it, and this exists so the emptiness is asserted rather than parsed away.
    """
    return [text for _slot, _state, text in SLOT_ROW.findall(page)]


def blocks(page: str) -> list[str]:
    return BLOCK.findall(page)


def ask_region(page: str) -> str:
    """The question a person is being asked, and nothing else. '' if none."""
    found = ASK.search(page)
    return "" if found is None else found.group(1)


# ---- the twenty decisions -----------------------------------------------------
#
# Chosen to cover every shape the decision order can produce, because a census
# of twenty identical VALID entries would report 20/20 on all four slots and
# prove nothing about a refusal, a question or a detector.
#
#   VALID       posts straight through, no problem, decided by the order itself
#   UNCLEAR     a check, a memory miss, a memory conflict, and a DETECTOR
#   NOT_VALID   a check no answer can fix
#
# Sixteen are served by the demo company in `tests/test_web.py`; four need the
# stale-ledger company in `tests/test_first_detector.py`, which is the only
# route from the screen to `vendor_switch` and therefore the only way to render
# a decision whose driver is a detector rather than a rule.


def demo_decisions(base: str) -> list[tuple[str, str]]:
    """Sixteen decisions on the demo company, in the order they are produced."""
    seen: list[tuple[str, str]] = []

    def entry(case: str, text: str) -> str:
        page = post(base, "/entry", text=text)
        seen.append((case, page))
        return page

    entry("valid_sharma", "paid Sharma Traders 4200 for cement")
    entry("valid_sharma_again", "paid Sharma Traders 5100 for cement")
    entry("valid_kumar", "paid Kumar Stationers 500 for paper")
    entry("valid_city_power", "paid City Power Board 7300 for power")
    entry("valid_landlord", "paid Landlord 20000 for rent")
    asked = entry("unseen_gupta_asks", "paid Gupta Hardware 1500 for tools")
    entry("unseen_mehta_asks", "paid Mehta Traders 900 for pipes")
    entry("unseen_bansal_asks", "paid Bansal Supplies 2500 for sand")
    entry("conflicted_verma_asks", "paid Verma Cement 2600 for cement")
    entry("gst_refused_sharma", "paid Sharma Traders 4200 for cement with 18% GST")
    entry("gst_refused_kumar", "paid Kumar Stationers 1180 for paper with 18% GST")
    entry("unreadable_text", "asdfghjkl")
    entry("no_party_named", "spent 4200 on cement")
    entry("very_large_amount", "paid Sharma Traders 20000000 for cement")

    # The answer route renders a decision too, and it is the one a person sees
    # most often — the second question, then the posted entry.
    held = draft_id(asked)
    seen.append(
        (
            "funding_question",
            post(
                base, "/answer", draft=held, problem="which_account", value="Purchases"
            ),
        )
    )
    seen.append(
        (
            "posted_after_two_answers",
            post(base, "/answer", draft=held, problem="funding_is_named", value="Cash"),
        )
    )
    return seen


def stale_decisions(base: str) -> list[tuple[str, str]]:
    """Four decisions on the stale-ledger company, one of them detector-driven."""
    seen: list[tuple[str, str]] = []

    first = post(base, "/entry", text="paid Sharma Traders 4200 for cement")
    seen.append(("chart_gap_asks", first))

    held = draft_id(first)
    fired = post(
        base, "/answer", draft=held, problem="accounts_exist", value="Purchases"
    )
    seen.append(("detector_vendor_switch", fired))

    seen.append(
        (
            "detector_dismissed",
            post(base, "/dismiss", draft=held, detector="vendor_switch"),
        )
    )
    seen.append(
        (
            "chart_gap_asks_again",
            post(base, "/entry", text="paid Sharma Traders 9900 for cement"),
        )
    )
    return seen


def twenty_decisions() -> list[tuple[str, str]]:
    """Twenty rendered decisions, over real HTTP, against two companies.

    Two servers rather than one because `app.configure()` binds the process to a
    single backend at a time, and the detector case needs a company whose chart
    no longer holds a ledger its history points at. Sequential, never
    overlapping.
    """
    seen: list[tuple[str, str]] = []
    with serving(demo_company(), fake_backend()) as base:
        seen += demo_decisions(base)
    with serving(stale_ledger_company(), fake_backend()) as base:
        seen += stale_decisions(base)
    return seen


@pytest.fixture(scope="module")
def census() -> list[tuple[str, str]]:
    """The twenty pages, served once and read by every count below.

    Module-scoped because twenty HTTP round trips against two servers is the
    expensive part and none of these tests mutate the pages. The fixture yields
    STRINGS, so nothing a test does can reach back into the app.
    """
    return twenty_decisions()


# ---- the four counts the owner asked for --------------------------------------


def test_there_are_exactly_twenty_decisions_and_each_page_shows_one(
    census: list[tuple[str, str]],
):
    """The denominator, counted the same way as the numerators.

    A census that quietly produced nineteen pages, or one page carrying two
    decision blocks, would report a ratio that looks like the required result
    and is not it.
    """
    assert len(census) == 20, [case for case, _ in census]
    assert len({case for case, _ in census}) == 20, "two cases share a name"

    per_page = {case: len(blocks(page)) for case, page in census}

    assert set(per_page.values()) == {1}, (
        f"every draft screen shows exactly one decision: {per_page}"
    )


@pytest.mark.parametrize("slot", REQUIRED_SLOTS)
def test_all_twenty_decisions_show_the_slot(census: list[tuple[str, str]], slot: str):
    """20/20, one slot at a time, so a failure names WHICH slot went missing.

    This is the "rendered for some decisions but not others" guard. A renderer
    that drew the explanation only when a question was outstanding would show
    16/20 here, and the parametrised name says which of the four it was.
    """
    missing = [case for case, page in census if slot not in slots(page)]

    assert missing == [], (
        f"{20 - len(missing)}/20 decisions show {slot}; missing on {missing}"
    )


def test_every_decision_shows_all_four_slots_and_no_others(
    census: list[tuple[str, str]],
):
    """The structural version, against the four names written down in THIS file.

    `test_all_twenty_decisions_show_the_slot` is parametrised over
    `REQUIRED_SLOTS`, so this adds the other direction: no page carries a fifth
    slot nobody asked for, and the module's own tuple has not been shortened.
    """
    for case, page in census:
        assert tuple(slots(page)) == REQUIRED_SLOTS, (
            f"{case} renders {tuple(slots(page))}, not {REQUIRED_SLOTS}"
        )

    assert app.PROVENANCE_SLOTS == REQUIRED_SLOTS
    assert tuple(app.PROVENANCE_LABELS) == REQUIRED_SLOTS


def test_no_provenance_cell_is_ever_empty(census: list[tuple[str, str]]):
    """The trap this feature exists to avoid, asserted on the bytes.

    An empty cell is indistinguishable from a field with no source, which is
    exactly the thing the reader is here to detect. Whitespace counts as empty:
    `<td> </td>` reads as blank on a screen.
    """
    blank = [
        (case, tuple(slots(page)))
        for case, page in census
        if any(not cell.strip() for cell in raw_cells(page))
    ]

    assert blank == [], f"a provenance slot rendered an empty cell: {blank}"


def test_every_decision_names_a_detector_or_a_rule_and_names_it_recorded(
    census: list[tuple[str, str]],
):
    """ "Shows detector or rule" means a NAME, not a marker standing in for one.

    Counting a `NOT_RECORDED` as "shown" would make 20/20 reachable by a
    renderer that knows nothing, which is the reverse of the measurement.
    """
    not_named = [
        (case, slots(page)["detector_or_rule"])
        for case, page in census
        if slots(page)["detector_or_rule"][0] != app.SLOT_RECORDED
    ]

    assert not_named == [], f"decisions with no detector and no rule: {not_named}"


def test_every_decision_shows_evidence_and_shows_it_recorded(
    census: list[tuple[str, str]],
):
    """The mutation that matters most: the detector is named, the evidence is not.

    A screen that says `vendor_switch` and nothing about WHY is the flag without
    a reason the schema already refuses to construct — a concern a person cannot
    dismiss quickly inflates D and breaks N1.
    """
    without = [
        (case, slots(page)["detector_or_rule"][1])
        for case, page in census
        if slots(page)["evidence"][0] != app.SLOT_RECORDED
    ]

    assert without == [], f"decisions naming a rule but showing no evidence: {without}"


def test_every_decision_shows_an_explanation_and_shows_it_recorded(
    census: list[tuple[str, str]],
):
    without = [
        case
        for case, page in census
        if slots(page)["explanation"][0] != app.SLOT_RECORDED
    ]

    assert without == [], f"decisions with no explanation: {without}"


def test_every_decision_shows_the_source_url_slot_and_it_is_never_blank_or_invented(
    census: list[tuple[str, str]],
):
    """20/20 show the slot. 0/20 carry a URL, and that is reported, not papered.

    `accountant/rules/` is not merged, so no decision on this screen has an
    official CBIC or Income Tax citation behind it. The honest rendering is the
    explicit marker: not a blank cell, which would read as "no source", and not
    a plausible URL, which would be an invented citation that survives review.

    The day the corpus lands, this test is the one that should start failing.
    """
    states = {case: slots(page)["source_url"] for case, page in census}

    assert all(state for state, _ in states.values()), states
    assert {state for state, _ in states.values()} == {app.SLOT_NOT_AVAILABLE}, states
    assert {text for _, text in states.values()} == {app.RULE_URL_UNAVAILABLE}, states
    assert "http" not in app.RULE_URL_UNAVAILABLE, "no URL is invented here"


def test_the_census_covers_a_detector_driven_decision_and_a_rule_driven_one(
    census: list[tuple[str, str]],
):
    """Otherwise "detector or rule" is proved for one half of the word.

    Twenty rule-driven decisions would satisfy every count above while leaving
    the detector path entirely unrendered, and the detector path is the one the
    reader most needs — it is the only slot whose value came from a detector
    firing rather than from a check failing.
    """
    kinds = {
        case: re.search(r'data-provenance-rule-kind="([^"]+)"', page)
        for case, page in census
    }
    found = {case: (m.group(1) if m else None) for case, m in kinds.items()}

    assert None not in found.values(), found
    assert "detector" in found.values(), found
    assert "rule" in found.values(), found
    assert found["detector_vendor_switch"] == "detector"
    assert slots(dict(census)["detector_vendor_switch"])["detector_or_rule"] == (
        app.SLOT_RECORDED,
        "vendor_switch",
    )


# ---- an absent source renders as a marker, never as nothing --------------------


def bare_record() -> ExtractedRecord:
    return ExtractedRecord(
        date=None,
        party=None,
        total_paise=None,
        tax_paise=None,
        per_field_source=dict.fromkeys(ExtractedRecord.FIELDS, "not_found"),
    )


def bare_voucher() -> Voucher:
    return Voucher(
        id="draft-bare",
        date=datetime.date(2026, 8, 10),
        party="",
        narration="",
        debit_account="",
        credit_account="",
        amount_paise=0,
    )


def draft_with(**over: object) -> pipeline.Draft:
    """A Draft carrying only what a test puts on it.

    The route to a decision with NO evidence at all. It cannot be reached over
    HTTP — `pipeline.evaluate` always runs the eight checks, so every real
    decision has at least a check count behind it — and "unreachable" is exactly
    the reasoning that let `dropped_flags` sit unrendered for a phase. The
    marker is asserted directly instead.
    """
    base: dict[str, object] = {
        "id": "draft-bare",
        "company": app.COMPANY,
        "voucher": bare_voucher(),
        "record": bare_record(),
        "operation_id": "op-bare",
        "decision": Decision(outcome=Outcome.VALID, reason="nothing to report"),
    }
    return pipeline.Draft(**(base | over))  # type: ignore[arg-type]


def test_a_decision_with_no_evidence_says_NOT_RECORDED_rather_than_nothing():
    """The named case in the requirement, asserted on the rendered HTML."""
    page = app.render_provenance(draft_with())

    assert slots(page)["evidence"] == (app.SLOT_NOT_RECORDED, "NOT_RECORDED")
    assert not any(not cell.strip() for cell in raw_cells(page))


def test_a_decision_with_no_explanation_says_NOT_RECORDED_rather_than_nothing():
    """`Decision.reason` is not validated non-empty, so an empty one is real."""
    page = app.render_provenance(
        draft_with(decision=Decision(outcome=Outcome.VALID, reason="   "))
    )

    assert slots(page)["explanation"] == (app.SLOT_NOT_RECORDED, "NOT_RECORDED")


def test_a_draft_that_was_never_decided_still_renders_all_four_slots():
    """`Draft.decision` is `None` until `evaluate` runs, and `Draft.outcome`
    RAISES in that state. A provenance block that raised while being drawn would
    take the whole page with it — which is how the NOT_VALID screen once became
    the one screen this app could not show."""
    page = app.render_provenance(draft_with(decision=None))

    assert tuple(slots(page)) == REQUIRED_SLOTS
    assert slots(page)["detector_or_rule"] == (app.SLOT_NOT_RECORDED, "NOT_RECORDED")
    assert slots(page)["explanation"] == (app.SLOT_NOT_RECORDED, "NOT_RECORDED")
    assert slots(page)["source_url"] == (
        app.SLOT_NOT_AVAILABLE,
        app.RULE_URL_UNAVAILABLE,
    )


def test_every_slot_the_renderer_computes_is_a_slot_the_renderer_draws():
    """The `dropped_flags` failure in its general form.

    `provenance_sources` computes the text and `PROVENANCE_SLOTS` decides what
    is drawn. A key in the first that is missing from the second is a value
    computed and thrown away — correct in the function, absent from the page.
    Both are compared to the four names written down at the top of this file, so
    deleting a slot from both sides still fails.
    """
    computed = tuple(app.provenance_sources(draft_with()))

    assert computed == REQUIRED_SLOTS
    assert app.PROVENANCE_SLOTS == REQUIRED_SLOTS


# ---- nothing rendered here is trusted -----------------------------------------


def scripted_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    """One detector whose evidence is hostile. The production path, unchanged.

    A vendor name is untrusted input that lands in a page, and it reaches the
    provenance block through three of the four slots — a flag's reason, a
    problem's detail, and the decision's own reason. It cannot be smuggled in
    through the typed-text party, because `normalise_vendor` strips the markup
    before the vendor key is ever rendered. A detector reason is the honest
    route: `Flag.reason` is free text that a real detector composes from the
    voucher, and it is rendered verbatim.
    """
    from accountant.detect import detectors

    real_run = detectors.run

    def hostile(
        proposed: Voucher,
        _history: object,
        _index: object,
    ) -> list[Flag]:
        return [
            Flag(
                voucher_id=proposed.id,
                detector="synthetic_injection",
                severity=99,
                reason='<script>alert("xss")</script> & "quoted"',
            )
        ]

    def wrapper(
        proposed: Voucher,
        history: object,
        index: object,
        detectors: object = None,  # noqa: ARG001 — replaced by the hostile one
        cap: int | None = None,
        *,
        dedupe: bool = True,
    ) -> tuple[list[Flag], int]:
        # The parameter MUST keep the name `detectors`; `pipeline.evaluate`
        # passes it by keyword. `tests/test_flag_cap.py` says the same at
        # length, and renaming it there once cost fifteen unrelated failures.
        return real_run(
            proposed,
            history,  # type: ignore[arg-type]
            index,  # type: ignore[arg-type]
            detectors=[hostile],  # type: ignore[list-item]
            cap=cap,
            dedupe=dedupe,
        )

    monkeypatch.setattr(detectors, "run", wrapper)


def test_a_hostile_detector_reason_reaches_the_page_escaped(
    stale_server: str, monkeypatch: pytest.MonkeyPatch
):
    """Over HTTP, through the shipped route, with the markup in the evidence."""
    scripted_detector(monkeypatch)

    page = post(stale_server, "/entry", text="paid Sharma Traders 4200 for cement")

    assert "<script>" not in page, "a detector reason was rendered unescaped"
    assert "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;" in page
    shown = slots(page)
    assert "&lt;script&gt;" in shown["evidence"][1]
    assert "&amp;" in shown["evidence"][1]


def test_every_provenance_value_goes_through_the_escaper():
    """The three slots the HTTP case cannot reach, at the renderer.

    `detector_or_rule` is an id and `source_url` will be a string fetched from
    outside this repository the moment `accountant/rules/` lands, which is the
    worst moment to discover the cell was never escaped.
    """

    @dataclasses.dataclass(frozen=True)
    class RuleBackedDecision(Decision):
        """What a corpus-backed decision is assumed to look like. See the
        module docstring: the assumption is `source_url` on the Decision or the
        Flag, and this is the Decision half of it."""

        source_url: str = ""

    decided = RuleBackedDecision(
        outcome=Outcome.NOT_VALID,
        reason='refused <b>because</b> "x" & y',
        source_url="https://example.invalid/n?a=1&b=<2>",
    )
    page = app.render_provenance(
        draft_with(
            decision=decided,
            problems=[Problem(id="<img src=x>", answerable=False, detail="bad")],
        )
    )

    assert "<b>" not in page
    assert "<img" not in page
    assert "<2>" not in page
    assert "&lt;img src=x&gt;" in page
    assert "b=&lt;2&gt;" in page
    assert "&amp;b=" in page


def test_the_source_url_slot_renders_a_carried_url_when_one_finally_exists():
    """The seam, exercised. Without this the `NOT_AVAILABLE` branch is the only
    branch anybody has ever run, and "it will work when the corpus lands" is a
    promise rather than a measurement."""

    @dataclasses.dataclass(frozen=True)
    class RuleBackedDecision(Decision):
        source_url: str = ""

    page = app.render_provenance(
        draft_with(
            decision=RuleBackedDecision(
                outcome=Outcome.VALID,
                reason="ok",
                source_url="https://example.invalid/circular-1",
            )
        )
    )

    assert slots(page)["source_url"] == (
        app.SLOT_RECORDED,
        "https://example.invalid/circular-1",
    )


# ---- S7: provenance must not leak an account name into a question --------------


def test_provenance_did_not_put_a_ledger_account_name_inside_a_question(
    census: list[tuple[str, str]],
):
    """The hard rule from the frozen plan, re-measured after this change.

    No question string may contain any account name from the company's chart of
    accounts. The chosen account is shown AFTER answering, never inside the
    question — and this block legitimately carries account names, because a
    `vendor_switch` reason naming the ledger a vendor usually goes to IS the
    evidence. So the two must not share a region.

    Both directions are asserted: no chart name inside `<p class=ask>`, and the
    provenance block entirely outside it.
    """
    chart = (*ACCOUNTS, "Old Ledger")
    leaked: dict[str, list[str]] = {}
    for case, page in census:
        asked = ask_region(page)
        if not asked:
            continue
        found = [name for name in chart if name in asked]
        if found:
            leaked[case] = found

    assert leaked == {}, f"a question leaked a ledger account name: {leaked}"


def test_the_provenance_block_is_not_rendered_inside_the_question(
    census: list[tuple[str, str]],
):
    """Position, not just content. A block placed inside `<p class=ask>` would
    pass the test above for as long as it happened to name no account."""
    for case, page in census:
        if "<p class=ask>" not in page:
            continue
        asked = ask_region(page)
        assert "data-provenance" not in asked, case
        assert page.index("<p class=ask>") < page.index('data-provenance="decision"'), (
            f"{case}: the provenance block is rendered above the question"
        )


def test_the_questions_themselves_are_unchanged_by_this_feature(stale_server: str):
    """S7 through the detector path, the narrowest case, kept alongside the
    census so a failure says whether it was the question or the block."""
    page = post(stale_server, "/entry", text="paid Sharma Traders 4200 for cement")
    asked = ask_region(page)

    assert asked, page[:900]
    assert [n for n in (*ACCOUNTS, "Old Ledger") if n in asked] == []
    assert 'data-provenance="decision"' in page


# ---- the pieces, checked directly ---------------------------------------------


def test_the_rule_for_an_entry_with_nothing_wrong_is_the_decision_order():
    """Not a blank, not `unknown`. `accountant/decide.py` is the decision order
    and it is what decided; naming it is a source, leaving it blank is not."""
    page = app.render_provenance(draft_with())

    assert slots(page)["detector_or_rule"] == (app.SLOT_RECORDED, "decision_order")
    assert app.DECISION_ORDER_RULE == "decision_order"


def test_a_memory_and_ledger_disagreement_is_evidence_with_both_counts():
    """D-06. "Your books changed" is not actionable; "you did this 40 times and
    then that 60 times" is the sentence a person can recognise in a second.

    Both sources and both counts, because a boolean flattens the difference
    between one stray row against forty and sixty against forty — the exact
    argument `LiveDisagreement` refuses to be constructed without.
    """
    page = app.render_provenance(
        draft_with(
            checks=[CheckResult(name="a_check", passed=True)],
            memory_conflict=LiveDisagreement(
                company_key="accountant_dad_final",
                subject="sharma_traders",
                remembered_account="Purchases",
                remembered_times=40,
                live_accounts=("Repairs & Maintenance", "Purchases"),
                live_times=(60, 40),
            ),
        )
    )

    evidence = slots(page)["evidence"][1]
    assert "sharma_traders" in evidence
    assert "Purchases 40 time(s)" in evidence
    assert "Repairs &amp; Maintenance 60 time(s)" in evidence
    assert "&amp;" in evidence, "the ampersand in the ledger name is escaped"


def test_a_concern_the_display_cap_hid_is_still_in_the_evidence():
    """`FLAG_CAP` is display only. The owner's rule when setting it was "never
    lose concerns from the audit/evidence record", and this block IS that
    record, so a suppressed flag appears here even though it is off the top of
    the screen."""
    hidden = Flag(
        voucher_id="draft-bare",
        detector="hidden_concern",
        severity=1,
        reason="pushed past the cap",
    )
    page = app.render_provenance(
        draft_with(
            checks=[CheckResult(name="a_check", passed=True)],
            suppressed_flags=[hidden],
        )
    )

    evidence = slots(page)["evidence"][1]
    assert "hidden_concern" in evidence
    assert "pushed past the cap" in evidence
    assert "over the display cap" in evidence


def test_the_evidence_names_every_failed_check_not_just_a_count():
    """ "3 failed" is not evidence. Which three, and why, is."""
    page = app.render_provenance(
        draft_with(
            checks=[
                CheckResult(name="passed_one", passed=True),
                CheckResult(
                    name="failed_one", passed=False, detail="amount is 0 paise"
                ),
            ]
        )
    )

    evidence = slots(page)["evidence"][1]
    assert "2 checks run, 1 failed" in evidence
    assert "check failed_one: amount is 0 paise" in evidence

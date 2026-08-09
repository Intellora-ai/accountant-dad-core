"""The owner's display cap, and the line that says what it hid.

OWNER DECISION, 2026-08-10:

    flag_cap = 3

    reason = show the first three concerns while preserving all concerns in
             evidence and avoiding an overloaded review screen

The cap is a DISPLAY decision and nothing else. It never changes detection,
storage, safety, or posting. A concern that is not shown is still detected,
still counted, still carried in the draft, and still available to the evidence
record — the screen shows three and then says how many it did not show.

THE FAILURE THIS FILE EXISTS TO PREVENT
---------------------------------------
"Overflow reported as a count, never silently dropped" is criterion #3.6 of the
frozen plan, and it was true inside the `Draft` and FALSE on the page for the
whole of Phase 6: `dropped_flags` was computed and never rendered. That was
fixed. The second half of the same defect survived it — `flag_cap` was never
passed from the web at all, so `dropped_flags` was permanently zero in
production and the overflow line could never appear no matter how many concerns
an entry raised. A rendering path that cannot execute is not a feature.

Worse, the flags beyond the cap were DISCARDED by `detectors.run`, so a capped
concern was gone from the draft entirely. A display decision was quietly
deleting findings. `Draft.suppressed_flags` now holds them.

WHY THE COUNTS ARE ASSERTED THROUGH THE RENDERED PAGE
-----------------------------------------------------
`Draft.dropped_flags` being right is not the claim. The claim is that a person
looking at the screen can tell that more concerns exist. A renderer can be
correct about the dataclass and wrong about the page, which is exactly how the
old forty-row event list managed to show activity while discarding the outcome.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about a real TallyPrime. Evidence class: UNIT_TEST and FAKETALLY over
real HTTP.
"""

from __future__ import annotations

import re

import pytest

from accountant import pipeline
from accountant.detect import detectors
from accountant.schema import Flag, Outcome, Voucher
from accountant.web import app
from tests.test_first_detector import stale_server
from tests.test_web import post

__all__ = ["stale_server"]

ENTRY = "paid Sharma Traders 4200 for cement"


# ---- synthetic detectors, one distinct concern each ---------------------------


def synthetic(count: int) -> list[detectors.Detector]:
    """`count` detectors, each raising one flag about its own concern.

    Distinct names matter. `concern_of` gives an unknown detector a concern of
    its own, so N synthetic detectors survive duplicate suppression as N alerts.
    Reusing one name would collapse them into one and the cap would never be
    reached — a test that proves nothing while looking thorough.
    """

    def make(i: int) -> detectors.Detector:
        def detector(
            proposed: Voucher,
            _history: object,
            _index: object,
        ) -> list[Flag]:
            return [
                Flag(
                    voucher_id=proposed.id,
                    detector=f"synthetic_{i:02d}",
                    severity=100 - i,
                    reason=f"synthetic concern {i} about {proposed.party}",
                )
            ]

        return detector  # type: ignore[return-value]

    return [make(i) for i in range(count)]


def with_synthetic_detectors(
    monkeypatch: pytest.MonkeyPatch, count: int
) -> dict[str, object]:
    """Swap in `count` synthetic detectors while KEEPING the real cap logic.

    The point of the wrapper is that `cap` is passed straight through
    untouched. If the web app stopped sending a cap, or sent a different one,
    these tests would see it — which is the only reason to wrap `run` rather
    than stub it out. `captured` records what the production path actually sent.
    """
    real_run = detectors.run
    captured: dict[str, object] = {}

    def wrapper(
        proposed: Voucher,
        history: object,
        index: object,
        detectors: object = None,  # noqa: ARG001 — replaced by the synthetic set
        cap: int | None = None,
        *,
        dedupe: bool = True,
    ) -> tuple[list[Flag], int]:
        # The parameter MUST keep the name `detectors`: `pipeline.evaluate`
        # calls `detectors.run(..., detectors=detector_set, ...)` by keyword, so
        # renaming it to satisfy a linter makes every call raise TypeError, the
        # handler answers 503, and fifteen tests fail for a reason that has
        # nothing to do with what they test. That happened once already.
        captured["cap"] = cap
        return real_run(
            proposed,
            history,  # type: ignore[arg-type]
            index,  # type: ignore[arg-type]
            detectors=synthetic(count),
            cap=cap,
            dedupe=dedupe,
        )

    monkeypatch.setattr(detectors, "run", wrapper)
    return captured


def shown(page: str) -> list[str]:
    return re.findall(r'data-detector="([^"]+)"', page)


def overflow_line(page: str) -> str:
    found = re.search(r'<p[^>]*data-overflow="\d+"[^>]*>([^<]*)</p>', page)
    return "" if found is None else found.group(1).strip()


def overflow_count(page: str) -> int | None:
    found = re.search(r'data-overflow="(\d+)"', page)
    return None if found is None else int(found.group(1))


# ---- the six cases the owner named -------------------------------------------


@pytest.mark.parametrize(
    ("concerns", "visible", "hidden"),
    [(0, 0, 0), (1, 1, 0), (3, 3, 0), (4, 3, 1), (5, 3, 2), (10, 3, 7)],
)
def test_the_screen_shows_at_most_three_concerns_and_counts_the_rest(
    stale_server: str,
    monkeypatch: pytest.MonkeyPatch,
    concerns: int,
    visible: int,
    hidden: int,
):
    """The owner's table, asserted on the rendered page.

    0 concerns  -> 0 visible, no overflow
    1 concern   -> 1 visible, no overflow
    3 concerns  -> 3 visible, no overflow
    4 concerns  -> 3 visible, 1 more concern
    5 concerns  -> 3 visible, 2 more concerns
    10 concerns -> 3 visible, 7 more concerns
    """
    with_synthetic_detectors(monkeypatch, concerns)

    page = post(stale_server, "/entry", text=ENTRY)

    assert len(shown(page)) == visible
    assert overflow_count(page) == (hidden if hidden else None)


def test_the_overflow_line_is_absent_entirely_when_nothing_was_hidden(
    stale_server: str, monkeypatch: pytest.MonkeyPatch
):
    """ "0 more concerns" on every clean entry is noise, and noise is what N1
    exists to measure. Nothing is rendered at zero."""
    with_synthetic_detectors(monkeypatch, 3)

    page = post(stale_server, "/entry", text=ENTRY)

    assert "more concern" not in page
    assert overflow_count(page) is None


def test_one_hidden_concern_reads_as_one_not_as_one_s(
    stale_server: str, monkeypatch: pytest.MonkeyPatch
):
    """Singular. "1 more concern(s)" is the kind of detail that tells a customer
    nobody read this screen."""
    with_synthetic_detectors(monkeypatch, 4)

    page = post(stale_server, "/entry", text=ENTRY)

    assert overflow_line(page) == "1 more concern"


def test_several_hidden_concerns_read_as_plural(
    stale_server: str, monkeypatch: pytest.MonkeyPatch
):
    with_synthetic_detectors(monkeypatch, 10)

    page = post(stale_server, "/entry", text=ENTRY)

    assert overflow_line(page) == "7 more concerns"


def test_the_hidden_count_is_the_exact_arithmetic_the_owner_specified(
    stale_server: str, monkeypatch: pytest.MonkeyPatch
):
    """N = total_concerns - displayed_concerns. Not an estimate, not a bucket."""
    with_synthetic_detectors(monkeypatch, 9)

    page = post(stale_server, "/entry", text=ENTRY)

    assert overflow_count(page) == 9 - len(shown(page))


# ---- the cap reaches the detectors from the production path -------------------


def capture_cap(monkeypatch: pytest.MonkeyPatch) -> list[int | None]:
    """Record the `flag_cap` every production call to `evaluate` supplies.

    Asserted at `pipeline.evaluate` rather than at `detectors.run`, because
    that is where the cap is now applied: detection runs uncapped so the
    overflow can be KEPT, and the slice happens one level up. Capturing at the
    detector boundary would now record `None` and prove the opposite of what it
    looks like it proves.
    """
    real = pipeline.evaluate
    seen: list[int | None] = []

    def wrapper(*args: object, **kwargs: object) -> pipeline.Draft:
        seen.append(kwargs.get("flag_cap"))  # type: ignore[arg-type]
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline, "evaluate", wrapper)
    return seen


def test_the_web_app_sends_the_cap_of_three_on_the_entry_route(
    stale_server: str, monkeypatch: pytest.MonkeyPatch
):
    """Before this, `flag_cap` defaulted to None everywhere the app called
    `evaluate`, so the cap existed in the signature and nowhere in production."""
    seen = capture_cap(monkeypatch)

    post(stale_server, "/entry", text=ENTRY)

    assert seen == [3]
    assert seen[0] == app.FLAG_CAP


def test_the_web_app_sends_the_same_cap_on_the_answer_route(
    stale_server: str, monkeypatch: pytest.MonkeyPatch
):
    """Two call sites, one cap. A cap applied on one route and not the other
    would make the screen change shape when a person answers a question."""
    page = post(stale_server, "/entry", text=ENTRY)
    draft = re.search(r'name=draft value="([^"]+)"', page)
    problem = re.search(r'name=problem value="([^"]+)"', page)
    assert draft and problem, f"no question to answer:\n{page[:600]}"
    seen = capture_cap(monkeypatch)

    post(
        stale_server,
        "/answer",
        draft=draft.group(1),
        problem=problem.group(1),
        value="Purchases",
    )

    assert seen == [3], "the answer route must cap exactly as the entry route does"


def test_no_route_in_the_web_app_evaluates_without_a_cap():
    """The structural version of the two tests above, so a THIRD route added
    later cannot quietly reintroduce the uncapped screen.

    The repository already uses AST scans for claims of this shape - "no other
    path exists" cannot be proven by testing the paths you remembered.
    """
    import ast
    import pathlib

    source = pathlib.Path(app.__file__).read_text()
    uncapped: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "evaluate"):
            continue
        if not any(kw.arg == "flag_cap" for kw in node.keywords):
            uncapped.append(node.lineno)

    assert uncapped == [], (
        f"accountant/web/app.py calls evaluate without flag_cap at line(s) "
        f"{uncapped}; the screen there would show every concern and count none"
    )


def test_the_cap_is_the_owner_decision_and_not_a_local_default():
    """One constant, findable by grep, carrying the decision that set it."""
    assert app.FLAG_CAP == 3


# ---- nothing is lost: the cap is display only ---------------------------------


def test_every_concern_survives_in_the_draft_even_when_the_screen_hides_it(
    stale_server: str, monkeypatch: pytest.MonkeyPatch
):
    """The requirement is "never lose concerns from the audit/evidence record".

    Until this landed, `detectors.run` returned the capped list and threw the
    rest away, so a display decision was deleting findings. Shown plus
    suppressed must equal detected, exactly.
    """
    with_synthetic_detectors(monkeypatch, 10)

    post(stale_server, "/entry", text=ENTRY)

    draft = next(iter(app.DRAFTS.values()))
    assert len(draft.flags) == 3
    assert len(draft.suppressed_flags) == 7
    assert draft.dropped_flags == 7
    names = [f.detector for f in (*draft.flags, *draft.suppressed_flags)]
    assert len(set(names)) == 10, "ten distinct concerns, none merged or lost"


def test_the_suppressed_concerns_are_the_lowest_ranked_ones(
    stale_server: str, monkeypatch: pytest.MonkeyPatch
):
    """Ranking decides what a person sees first, so the cap must cut the tail,
    never an arbitrary slice. Severity descends in `synthetic`."""
    with_synthetic_detectors(monkeypatch, 6)

    post(stale_server, "/entry", text=ENTRY)

    draft = next(iter(app.DRAFTS.values()))
    shown_sev = [f.severity for f in draft.flags]
    hidden_sev = [f.severity for f in draft.suppressed_flags]
    assert shown_sev == sorted(shown_sev, reverse=True)
    assert min(shown_sev) > max(hidden_sev)


def test_a_capped_entry_still_reaches_the_same_outcome(
    stale_server: str, monkeypatch: pytest.MonkeyPatch
):
    """The cap must not change the decision. Ten concerns and three concerns
    are both "this entry has concerns"; hiding seven of them cannot turn a
    refusal into an approval."""
    with_synthetic_detectors(monkeypatch, 10)

    post(stale_server, "/entry", text=ENTRY)
    capped = next(iter(app.DRAFTS.values())).outcome

    app.DRAFTS.clear()
    with_synthetic_detectors(monkeypatch, 3)
    post(stale_server, "/entry", text=ENTRY)
    uncapped = next(iter(app.DRAFTS.values())).outcome

    assert capped is uncapped
    assert capped is not Outcome.VALID


def test_the_cap_cannot_hide_a_safety_critical_refusal(
    stale_server: str, monkeypatch: pytest.MonkeyPatch
):
    """The one property that would make this feature dangerous.

    A refusal is not a flag and must never be displaced by one. Ten concerns
    push everything past the cap; the entry must still be visibly not posted,
    and nothing may be written.
    """
    with_synthetic_detectors(monkeypatch, 10)

    page = post(stale_server, "/entry", text=ENTRY)

    assert overflow_count(page) == 7
    assert app.runtime().client.list_our_vouchers(app.COMPANY) == ()


def test_a_capped_screen_never_posts_a_voucher(
    stale_server: str, monkeypatch: pytest.MonkeyPatch
):
    """Posting is gated on outcome VALID, never on how many flags fitted."""
    with_synthetic_detectors(monkeypatch, 10)

    post(stale_server, "/entry", text=ENTRY)

    assert app.runtime().client.list_our_vouchers(app.COMPANY) == ()


# ---- the arithmetic itself, without a browser in the way ----------------------


@pytest.mark.parametrize(
    ("total", "cap", "visible", "dropped"),
    [
        (0, 3, 0, 0),
        (1, 3, 1, 0),
        (3, 3, 3, 0),
        (4, 3, 3, 1),
        (10, 3, 3, 7),
        (10, None, 10, 0),
        (2, 0, 0, 2),
    ],
)
def test_the_cap_arithmetic_holds_at_the_detector_boundary(
    total: int, cap: int | None, visible: int, dropped: int
):
    """`detectors.run` is called by more than the web app. The arithmetic is
    pinned here so a second caller cannot get a different answer."""
    from accountant.memory.index import MemoryIndex

    voucher = Voucher(
        id="v1",
        date=__import__("datetime").date(2026, 8, 10),
        party="Sharma Traders",
        narration="cement",
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=420000,
    )
    flags, count = detectors.run(
        voucher, (), MemoryIndex.from_vouchers(()), detectors=synthetic(total), cap=cap
    )

    assert len(flags) == visible
    assert count == dropped


@pytest.mark.parametrize("bad", [-1, -3])
def test_a_negative_cap_is_refused_rather_than_silently_inverted(bad: int):
    """`flags[:-1]` is a valid slice and a wrong answer: a negative cap would
    hide the LAST concern and report a dropped count larger than the number of
    concerns. Failing safely means refusing, not guessing."""
    from accountant.memory.index import MemoryIndex

    voucher = Voucher(
        id="v1",
        date=__import__("datetime").date(2026, 8, 10),
        party="Sharma Traders",
        narration="cement",
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=420000,
    )

    with pytest.raises(ValueError, match="cap"):
        detectors.run(
            voucher,
            (),
            MemoryIndex.from_vouchers(()),
            detectors=synthetic(2),
            cap=bad,
        )

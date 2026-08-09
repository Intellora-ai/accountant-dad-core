"""The three kinds of evidence stay three. Enforced, not promised.

WHY THIS FILE EXISTS
--------------------
This project can be exercised three ways and the difference decides what a
result is allowed to be used for:

    FakeTally implementation      our logic is right. Says NOTHING about Tally.
    Educational-mode compatibility a real TallyPrime accepted our XML on a date
                                  Educational mode permits. Says nothing about
                                  the 2026-08-07 contract fixture.
    RealTally live                the unchanged contract passed against a
                                  licensed TallyPrime.

Every honest report so far has kept them apart. "Has kept" is not "will keep",
and the failure is silent: a compatibility run reported as live proof looks
exactly like a live run to anyone reading the summary, and there is no red test
anywhere to contradict it.

So the label is pinned to the harness that produces it. `ci/educational_slice.py`
carries a constant, prints it beside every verdict, and states in its own output
what it is not. Changing what that harness claims now requires changing this
file, which is a decision somebody has to make on purpose.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Nothing about TallyPrime, and nothing about whether the slice's measurements are
correct. It reads source and constants. It never opens a socket. A harness can
be perfectly labelled and still measure the wrong thing.
"""

from __future__ import annotations

import pathlib

from ci import educational_slice

REPO = pathlib.Path(__file__).resolve().parent.parent

# The claims a compatibility harness may never make about itself. Checked as a
# whole list rather than sampled, because the one that gets through will be the
# one nobody thought to sample.
FORBIDDEN_CLAIMS = (
    "live proof",
    "realtally proof",
    "phase 3 complete",
    "phase 3 live completion",
    "2026-08-07 proof",
    "fully live",
    "live validation complete",
)


def test_the_slice_declares_itself_compatibility_evidence_and_nothing_more() -> None:
    """The label is a constant in the harness, not a choice made when reporting.

    A report writer who has to pick the label will eventually pick the
    flattering one. A constant printed beside the verdict cannot be talked out
    of what it says.
    """
    assert educational_slice.EVIDENCE_CLASS == "EDUCATIONAL_MODE_COMPATIBILITY"


def test_the_slice_uses_a_date_educational_mode_permits() -> None:
    """1st, 2nd or 31st. Literally the 31st - February has only two usable days.

    Confirmed on Tally's own help site: Educational mode "can be used only for
    creating vouchers for the 1st, 2nd and 31st date of the month".
    """
    assert educational_slice.EDUCATIONAL_DATE.day in (1, 2, 31)


def test_the_slice_never_touches_the_contract_fixture_date() -> None:
    """2026-08-07 is the question this slice is NOT allowed to answer.

    The two dates are both named in the harness so the distance between them is
    visible in the source rather than only in prose. If they were ever made
    equal, the compatibility run would silently become a claim about the
    contract fixture.
    """
    assert educational_slice.UNTOUCHED_FIXTURE_DATE.isoformat() == "2026-08-07"
    assert (
        educational_slice.EDUCATIONAL_DATE != educational_slice.UNTOUCHED_FIXTURE_DATE
    )


def test_the_contract_fixture_still_posts_on_the_seventh() -> None:
    """Read off the real file. The owner's standing instruction, verified.

    Not a paraphrase of the rule - the actual line. If somebody edits the
    fixture to make Educational mode accept it, this is what goes red.
    """
    source = (REPO / "tests" / "test_tally_contract.py").read_text(encoding="utf-8")
    assert "datetime.date(2026, 8, 7)" in source, (
        "the 2026-08-07 contract fixture has been changed. It is never edited "
        "to satisfy the environment - that is what keeps the live question open"
    )


def test_the_slice_makes_none_of_the_claims_it_is_not_entitled_to() -> None:
    """Its own source, scanned for the words that would overstate it.

    Every forbidden phrase DOES appear in the file - inside the sentences saying
    it is NOT those things. So the check is on claims, not mentions: a phrase is
    only a violation when it is not negated on its own line.
    """
    source = (REPO / "ci" / "educational_slice.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        lowered = line.lower()
        negated = any(w in lowered for w in (" not ", "never", "n't", "may never"))
        for claim in FORBIDDEN_CLAIMS:
            if claim in lowered and not negated:
                raise AssertionError(
                    f"ci/educational_slice.py claims {claim!r} without negating "
                    f"it, on: {line.strip()!r}"
                )


def test_the_slice_says_out_loud_what_it_is_not() -> None:
    """Silence would be the failure. The disclaimer has to reach the reader.

    A harness that is merely *correctly labelled* still lets somebody skim past
    the label. Printing the negation next to the verdict is what makes the
    limitation as loud as the result.
    """
    source = (REPO / "ci" / "educational_slice.py").read_text(encoding="utf-8")
    for phrase in ("NOT live proof", "NOT RealTally proof", "NOT 2026-08-07 proof"):
        assert phrase in source, f"the slice never says it is {phrase}"

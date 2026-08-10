"""The other three detectors from #3.

vendor_switch is covered by test_pipeline. These three are written but not yet
wired into SLICE_4_DETECTORS, which is exactly why they were untested: code that
nothing calls is code nothing checks.

#3.1  each detector fires on its own error type
#3.2  every flag names specific evidence
#3.3  every flag maps to a plain-language question
#3.5  the queue is ordered deterministically
#3.6  no model call
"""

from __future__ import annotations

import datetime
import socket
from collections.abc import Callable

import pytest

from accountant import checks, problems
from accountant.decide import decide_problems
from accountant.detect import detectors
from accountant.memory.index import MemoryIndex
from accountant.schema import Flag, MatchResult, MatchStatus, Outcome, Voucher

TODAY = datetime.date(2026, 8, 7)
ACCOUNTS = ("Purchases", "Rent", "Repairs & Maintenance", "Cash", "Bank")

# History happens BEFORE the entry it is history for. Dated explicitly since
# 2026-08-10, when `magnitude` stopped counting rows that are not prior to an
# entry into that entry's ceiling - the root-cause fix for the DHSC
# `Additions NCB PDC` false alarms. Every assertion below is unchanged; the
# fixture stopped claiming that a payment can be its own precedent.
EARLIER = TODAY - datetime.timedelta(days=1)


def v(
    account: str = "Purchases",
    amount: int = 420000,
    gst: int | None = None,
    party: str = "Sharma Traders",
    vid: str = "d1",
    when: datetime.date = TODAY,
) -> Voucher:
    return Voucher(
        id=vid,
        date=when,
        party=party,
        narration="x",
        debit_account=account,
        credit_account="Cash",
        amount_paise=amount,
        gst_paise=gst,
    )


def history(
    account: str = "Purchases",
    amount: int = 380000,
    n: int = 3,
    gst: int | None = None,
    party: str = "Sharma Traders",
) -> tuple[Voucher, ...]:
    return tuple(
        v(
            account=account,
            amount=amount,
            gst=gst,
            party=party,
            vid=f"h{i}",
            when=EARLIER,
        )
        for i in range(n)
    )


def index_of(hist: tuple[Voucher, ...]) -> MemoryIndex:
    return MemoryIndex.from_vouchers(hist)


# ---- vendor_switch: "consistently" is now enforced --------------------------


def test_vendor_switch_is_silent_after_a_single_prior_posting():
    """The measured false alarm this threshold exists to remove.

    "Consistently posted to X" after ONE posting was a claim the evidence did
    not support, and on real published ledgers it was most of N1.
    """
    hist = history("Purchases", n=1)
    assert detectors.MIN_POSTINGS_FOR_A_PRACTICE == 2
    assert detectors.vendor_switch(v(account="Rent"), hist, index_of(hist)) == []


def test_vendor_switch_speaks_from_the_second_posting_onwards():
    hist = history("Purchases", n=2)
    flags = detectors.vendor_switch(v(account="Rent"), hist, index_of(hist))
    assert [f.detector for f in flags] == ["vendor_switch"]
    assert "2 times" in flags[0].reason


def test_vendor_switch_is_silent_on_the_account_the_party_always_uses():
    hist = history("Purchases", n=5)
    assert detectors.vendor_switch(v(account="Purchases"), hist, index_of(hist)) == []


def test_vendor_switch_is_silent_on_a_party_it_has_never_seen():
    hist = history("Purchases", n=5)
    proposed = v(account="Rent", party="Gupta Hardware")
    assert detectors.vendor_switch(proposed, hist, index_of(hist)) == []


def test_vendor_switch_threshold_is_a_parameter_a_calibration_run_can_move():
    hist = history("Purchases", n=1)
    loose = detectors.vendor_switch(
        v(account="Rent"), hist, index_of(hist), min_postings=1
    )
    assert [f.detector for f in loose] == ["vendor_switch"]
    tight = detectors.vendor_switch(
        v(account="Rent"), hist, index_of(hist), min_postings=9
    )
    assert tight == []


def test_a_configured_copy_of_a_detector_keeps_its_own_name():
    """Reports name detectors by __name__, so a tuned copy must not rename one."""
    hist = history("Purchases", n=1)
    at_one = detectors.vendor_switch_at(1)
    assert detectors.name_of(at_one) == "vendor_switch"
    assert [f.detector for f in at_one(v(account="Rent"), hist, index_of(hist))] == [
        "vendor_switch"
    ]
    bare = history("Purchases", amount=380000)
    at_two = detectors.magnitude_at(2, 100)
    assert detectors.name_of(at_two) == "magnitude"
    assert len(at_two(v(amount=380001), bare, index_of(bare))) == 1


# ---- first_use --------------------------------------------------------------


def test_first_use_fires_on_an_account_never_used_before():
    hist = history("Purchases")
    flags = detectors.first_use(v(account="Rent"), hist, index_of(hist))
    assert [f.detector for f in flags] == ["first_use"]


def test_first_use_is_silent_for_an_account_already_in_use():
    hist = history("Purchases")
    assert detectors.first_use(v(account="Purchases"), hist, index_of(hist)) == []


def test_first_use_reason_names_the_account_and_how_much_history_it_checked():
    hist = history("Purchases", n=7)
    reason = detectors.first_use(v(account="Rent"), hist, index_of(hist))[0].reason
    assert "Rent" in reason
    assert "7" in reason


# ---- magnitude --------------------------------------------------------------


def test_magnitude_fires_far_above_the_accounts_own_historical_maximum():
    hist = history("Purchases", amount=380000)
    flags = detectors.magnitude(v(amount=200_000_000), hist, index_of(hist))
    assert [f.detector for f in flags] == ["magnitude"]


def test_magnitude_is_silent_exactly_at_the_calibrated_margin():
    """The bound is the maximum times the calibrated margin, inclusive.

    Exactly at the margin is not "far outside" the range, so it is silent.
    """
    hist = history("Purchases", amount=380000)
    at_margin = 380000 * detectors.MAGNITUDE_OVER_PERCENT // detectors.PERCENT
    assert detectors.magnitude(v(amount=at_margin), hist, index_of(hist)) == []


def test_magnitude_is_silent_one_paise_below_the_calibrated_margin():
    hist = history("Purchases", amount=380000)
    at_margin = 380000 * detectors.MAGNITUDE_OVER_PERCENT // detectors.PERCENT
    assert detectors.magnitude(v(amount=at_margin - 1), hist, index_of(hist)) == []


def test_magnitude_fires_one_paise_above_the_calibrated_margin():
    hist = history("Purchases", amount=380000)
    at_margin = 380000 * detectors.MAGNITUDE_OVER_PERCENT // detectors.PERCENT
    assert len(detectors.magnitude(v(amount=at_margin + 1), hist, index_of(hist))) == 1


def test_magnitude_is_silent_just_over_the_bare_maximum():
    """The measured false alarm this margin exists to remove.

    One paise over an account's own maximum used to fire. On real published
    ledgers a new maximum is an ordinary event, and it was most of N1.
    """
    hist = history("Purchases", amount=380000)
    assert detectors.magnitude(v(amount=380001), hist, index_of(hist)) == []


def test_magnitude_says_nothing_when_the_account_has_no_history():
    """No observed range means no claim. Never an invented multiplier."""
    hist = history("Rent")
    assert detectors.magnitude(v(account="Purchases"), hist, index_of(hist)) == []


def test_magnitude_says_nothing_from_a_single_observation():
    """One point is not a range: it has no top for an amount to be outside of."""
    hist = history("Purchases", amount=380000, n=1)
    assert detectors.MIN_OBSERVATIONS_FOR_A_RANGE == 2
    assert detectors.magnitude(v(amount=200_000_000), hist, index_of(hist)) == []


def test_magnitude_speaks_from_the_second_observation_onwards():
    hist = history("Purchases", amount=380000, n=2)
    assert len(detectors.magnitude(v(amount=200_000_000), hist, index_of(hist))) == 1


def test_magnitude_thresholds_are_parameters_a_calibration_run_can_move():
    """The numbers are calibrated, so they must be reachable from outside."""
    hist = history("Purchases", amount=380000, n=3)
    loose = detectors.magnitude(
        v(amount=380001), hist, index_of(hist), min_observations=2, over_percent=100
    )
    assert [f.detector for f in loose] == ["magnitude"]
    tight = detectors.magnitude(
        v(amount=380001), hist, index_of(hist), min_observations=9, over_percent=100
    )
    assert tight == []


def test_magnitude_reason_states_both_numbers_the_sample_size_and_the_margin():
    hist = history("Purchases", amount=380000, n=4)
    reason = detectors.magnitude(v(amount=200_000_000), hist, index_of(hist))[0].reason
    assert "200000000" in reason
    assert "380000" in reason
    assert "4" in reason
    assert str(detectors.MAGNITUDE_OVER_PERCENT) in reason


# ---- gst_anomaly ------------------------------------------------------------


def test_gst_anomaly_fires_when_an_account_has_never_carried_gst():
    hist = history("Purchases", gst=None)
    flags = detectors.gst_anomaly(v(gst=64068), hist, index_of(hist))
    assert [f.detector for f in flags] == ["gst_anomaly"]


def test_gst_anomaly_is_silent_when_the_account_has_carried_gst_before():
    hist = history("Purchases", gst=50000)
    assert detectors.gst_anomaly(v(gst=64068), hist, index_of(hist)) == []


def test_gst_anomaly_is_silent_when_this_entry_has_no_gst():
    hist = history("Purchases", gst=None)
    assert detectors.gst_anomaly(v(gst=None), hist, index_of(hist)) == []


def test_gst_anomaly_says_nothing_about_an_account_with_no_history():
    hist = history("Rent")
    assert (
        detectors.gst_anomaly(v(account="Purchases", gst=1), hist, index_of(hist)) == []
    )


def test_gst_anomaly_reason_names_the_amount_the_account_and_the_sample():
    hist = history("Purchases", gst=None, n=5)
    reason = detectors.gst_anomaly(v(gst=64068), hist, index_of(hist))[0].reason
    assert "64068" in reason
    assert "Purchases" in reason
    assert "5" in reason


# ---- every flag carries evidence and a question (#3.2, #3.3) ----------------


def _fire_first_use(h: tuple[Voucher, ...]) -> list[Flag]:
    return detectors.first_use(v(account="Rent"), h, index_of(h))


def _fire_magnitude(h: tuple[Voucher, ...]) -> list[Flag]:
    return detectors.magnitude(v(amount=200_000_000), h, index_of(h))


def _fire_gst_anomaly(h: tuple[Voucher, ...]) -> list[Flag]:
    return detectors.gst_anomaly(v(gst=64068), h, index_of(h))


Fire = Callable[[tuple[Voucher, ...]], list[Flag]]
ALL_FIRING: list[tuple[str, Fire]] = [
    ("first_use", _fire_first_use),
    ("magnitude", _fire_magnitude),
    ("gst_anomaly", _fire_gst_anomaly),
]


@pytest.mark.parametrize("name,fire", ALL_FIRING)
def test_every_flag_states_evidence_a_person_could_check(name: str, fire: Fire):
    hist = history("Purchases")
    flags = fire(hist)
    assert flags, f"{name} did not fire"
    for f in flags:
        assert f.reason.strip(), f"{name} produced a flag with no reason"
        assert any(ch.isdigit() for ch in f.reason), (
            f"{name}'s reason cites no number, so nobody can check it: {f.reason}"
        )


@pytest.mark.parametrize("name,fire", ALL_FIRING)
def test_every_flag_becomes_an_answerable_question(name: str, fire: Fire):
    """#3.3 - a detector firing means 'surprising', never 'wrong'."""
    hist = history("Purchases")
    flag = fire(hist)[0]
    voucher = v(account="Rent" if name == "first_use" else "Purchases", gst=64068)
    p = problems.find(
        voucher,
        [],
        MatchResult(MatchStatus.MATCH, "sharma_traders", ("Purchases",)),
        [flag],
        ACCOUNTS,
        hist,
        index_of(hist),
    )
    assert [x.id for x in p] == [name]
    assert p[0].answerable is True
    assert p[0].question is not None


def test_an_unknown_detector_is_reported_rather_than_guessed_at():
    """A flag from a detector problems.py does not know about must not silently
    become a question we invented."""
    hist = history("Purchases")
    unknown = Flag(
        voucher_id="d1", detector="from_the_future", severity=1, reason="who knows"
    )
    p = problems.find(
        v(),
        [],
        MatchResult(MatchStatus.MATCH, "sharma_traders", ("Purchases",)),
        [unknown],
        ACCOUNTS,
        hist,
        index_of(hist),
    )
    assert p[0].id == "from_the_future"
    assert p[0].answerable is False


# ---- the ranked queue (#3.5, #3.6) ------------------------------------------


def test_all_four_detectors_can_run_together():
    hist = history("Purchases", gst=None, n=3)
    flags, dropped = detectors.run(
        v(account="Rent", amount=200_000_000, gst=64068),
        hist,
        index_of(hist),
        detectors=detectors.ALL_DETECTORS,
        dedupe=False,
    )
    assert dropped == 0
    assert "first_use" in {f.detector for f in flags}
    assert "vendor_switch" in {f.detector for f in flags}


# ---- one alert per underlying problem (duplicate suppression) ---------------


def test_two_detectors_reading_the_same_field_are_one_underlying_problem():
    """vendor_switch and first_use both read the account. One wrong account,
    one problem, one question - not two."""
    assert detectors.concern_of("vendor_switch") == detectors.concern_of("first_use")
    assert detectors.concern_of("magnitude") != detectors.concern_of("first_use")
    assert detectors.concern_of("gst_anomaly") != detectors.concern_of("magnitude")


def test_an_unrecognised_detector_gets_a_concern_of_its_own():
    """Two unknown detectors must never silence each other."""
    mine = detectors.concern_of("from_the_future")
    yours = detectors.concern_of("or_the_past")
    assert mine != yours
    assert "from_the_future" in detectors.concern_of("from_the_future")


def test_duplicate_alerts_collapse_to_one_and_keep_every_reason():
    hist = history("Purchases", gst=None, n=3)
    proposed = v(account="Rent", amount=200_000_000, gst=64068)
    raw, _ = detectors.run(
        proposed, hist, index_of(hist), detectors.ALL_DETECTORS, dedupe=False
    )
    kept, _ = detectors.run(proposed, hist, index_of(hist), detectors.ALL_DETECTORS)

    assert {f.detector for f in raw} == {"vendor_switch", "first_use"}
    assert [f.detector for f in kept] == ["vendor_switch"]
    # Suppression removes the second ALERT, never one word of the evidence.
    for f in raw:
        assert f.reason in kept[0].reason


def test_grouping_reports_the_duplicates_it_folded():
    hist = history("Purchases", gst=None, n=3)
    raw, _ = detectors.run(
        v(account="Rent", amount=200_000_000, gst=64068),
        hist,
        index_of(hist),
        detectors.ALL_DETECTORS,
        dedupe=False,
    )
    (alert,) = detectors.group_by_concern(raw)
    assert alert.lead.detector == "vendor_switch"
    assert [f.detector for f in alert.corroborating] == ["first_use"]
    assert alert.duplicates == 1
    assert [f.detector for f in alert.flags] == ["vendor_switch", "first_use"]


def test_grouping_leaves_a_lone_flag_exactly_as_it_was():
    hist = history("Purchases", gst=None, n=3)
    raw, _ = detectors.run(
        v(gst=64068), hist, index_of(hist), (detectors.gst_anomaly,), dedupe=False
    )
    (alert,) = detectors.group_by_concern(raw)
    assert alert.corroborating == ()
    assert alert.duplicates == 0
    kept, _ = detectors.run(
        v(gst=64068), hist, index_of(hist), (detectors.gst_anomaly,)
    )
    assert kept == raw


def test_grouping_nothing_produces_nothing():
    assert detectors.group_by_concern([]) == ()


# ---- which detectors run, and which do not ---------------------------------


def test_the_active_set_and_the_withdrawn_set_account_for_every_detector():
    """Nothing may leave ALL_DETECTORS silently."""
    everything = {detectors.name_of(d) for d in detectors.ALL_DETECTORS}
    active = {detectors.name_of(d) for d in detectors.ACTIVE_DETECTORS}
    withdrawn = {w.detector for w in detectors.WITHDRAWN}
    assert active | withdrawn == everything
    assert not active & withdrawn


def test_every_withdrawn_detector_states_a_reason_and_still_exists():
    assert detectors.WITHDRAWN
    by_name = {detectors.name_of(d): d for d in detectors.ALL_DETECTORS}
    for w in detectors.WITHDRAWN:
        assert w.detector in by_name, "a withdrawn detector must not be deleted"
        assert w.because.strip()


def test_a_withdrawn_detector_still_fires_when_it_is_asked_to():
    """Withdrawn is off by default, not broken and not silenced."""
    hist = history("Purchases", n=3)
    flags = detectors.first_use(v(account="Rent"), hist, index_of(hist))
    assert [f.detector for f in flags] == ["first_use"]


def test_the_queue_is_ordered_by_severity_then_deterministically():
    hist = history("Purchases", gst=None)
    flags, _ = detectors.run(
        v(account="Rent", amount=200_000_000, gst=64068),
        hist,
        index_of(hist),
        detectors=detectors.ALL_DETECTORS,
    )
    severities = [f.severity for f in flags]
    assert severities == sorted(severities, reverse=True)
    assert flags == sorted(flags, key=lambda f: (-f.severity, f.detector, f.voucher_id))


def test_the_cap_drops_nothing_silently():
    """#3.6 - overflow is reported as a count, never quietly discarded.

    Two DISTINCT concerns, so the cap bites on real alerts rather than on
    duplicates that suppression would have folded anyway.
    """
    hist = history("Purchases", gst=None)
    proposed = v(account="Purchases", amount=200_000_000, gst=64068)
    uncapped, none_dropped = detectors.run(
        proposed, hist, index_of(hist), detectors=detectors.ALL_DETECTORS
    )
    assert {f.detector for f in uncapped} == {"magnitude", "gst_anomaly"}
    assert none_dropped == 0

    capped, dropped = detectors.run(
        proposed, hist, index_of(hist), detectors=detectors.ALL_DETECTORS, cap=1
    )
    assert len(capped) == 1
    assert dropped == len(uncapped) - 1 == 1


def test_running_no_detectors_produces_nothing_and_drops_nothing():
    hist = history("Purchases")
    assert detectors.run(v(), hist, index_of(hist), detectors=()) == ([], 0)


def test_no_detector_makes_a_network_call(monkeypatch: pytest.MonkeyPatch):
    """#3.6 - verified, not promised."""

    def explode(*_a: object, **_k: object) -> object:
        raise AssertionError("a detector attempted a network call")

    monkeypatch.setattr(socket, "socket", explode)
    monkeypatch.setattr(socket, "create_connection", explode)
    hist = history("Purchases", gst=None)
    detectors.run(
        v(account="Rent", amount=200_000_000, gst=64068),
        hist,
        index_of(hist),
        detectors=detectors.ALL_DETECTORS,
    )


# ---- what a detector firing MEANS (#3.3, restated as a guard) ---------------


def test_a_fired_detector_asks_and_never_refuses():
    """A detector firing means "this needs attention", not "this is wrong".

    Every flag from a detector `problems.py` knows becomes an ANSWERABLE
    problem, and an answerable problem is UNCLEAR: ask, record, re-evaluate.
    Not-valid is reserved for problems no answer could fix, and a detector
    never produces one.

    CORRECTED 2026-08-10. The five things a test change owes:

    old assertion   `all(p.answerable for p in found)` over EVERY problem, then
                    `decide_problems(found).outcome is Outcome.UNCLEAR`.
    why it was wrong
                    Scope. The claim is about problems a DETECTOR produced; the
                    assertion ranged over check-derived problems too. It held
                    only by accident — `amount_is_integer_paise` was the single
                    unanswerable check and this fixture passes it. The moment a
                    second unanswerable check existed
                    (`tax_lines_can_be_posted`, added the same day), a check
                    refusing this entry read as a DETECTOR refusing it, which is
                    the opposite of what the test is for.
    new assertion   the flag-derived problems specifically are all answerable,
                    and deciding on those alone gives UNCLEAR. The fixture is
                    UNCHANGED — `gst=64068` stays, because it is what makes
                    `gst_anomaly` fire and removing it would quietly drop a
                    detector from the test's reach.
    safety impact   none loosened. The whole-entry outcome is still asserted,
                    and now asserted more precisely: NOT_VALID here, from a
                    check, with the detector problems shown to be answerable
                    underneath it. A detector that started refusing entries
                    still fails this test.
    new result      PASS.
    """
    hist = history("Purchases", gst=None, n=3)
    proposed = v(account="Rent", amount=200_000_000, gst=64068)
    flags, _ = detectors.run(
        proposed, hist, index_of(hist), detectors=detectors.ALL_DETECTORS
    )
    assert flags

    found = problems.find(
        proposed,
        checks.run(proposed, ACCOUNTS),
        index_of(hist).lookup(proposed.party),
        flags,
        ACCOUNTS,
        hist,
        index_of(hist),
    )
    fired = {f.detector for f in flags}
    from_detectors = [p for p in found if p.id in fired]

    assert from_detectors, "the flags produced no problems, so nothing is under test"
    assert all(p.answerable for p in from_detectors), (
        "a detector must never refuse an entry: "
        f"{[p.id for p in from_detectors if not p.answerable]}"
    )
    assert decide_problems(from_detectors).outcome is Outcome.UNCLEAR

    # The whole entry IS refused, and the refusal belongs to a check rather than
    # to any detector. Naming which one is the point: it is the distinction this
    # test exists to hold open.
    decision = decide_problems(found)
    refusing = [p.id for p in found if not p.answerable]

    assert decision.outcome is Outcome.NOT_VALID
    assert refusing == ["tax_lines_can_be_posted"]
    assert not fired & set(refusing)
    assert decision.post is False


def test_a_detector_firing_is_never_permission_to_post():
    """The other direction: a flag cannot make an entry Valid either."""
    hist = history("Purchases", n=3)
    proposed = v(account="Rent")
    flags, _ = detectors.run(
        proposed, hist, index_of(hist), detectors=detectors.ALL_DETECTORS
    )
    found = problems.find(
        proposed,
        checks.run(proposed, ACCOUNTS),
        index_of(hist).lookup(proposed.party),
        flags,
        ACCOUNTS,
        hist,
        index_of(hist),
    )
    assert decide_problems(found).outcome is not Outcome.VALID


def test_an_unusual_but_valid_entry_is_asked_about_rather_than_rejected():
    """The rule the role correction exists to protect.

    An entry this company simply has not seen before is unusual, not wrong.
    It must produce a question, and the person's answer is new information -
    the entry re-enters the decision order and can still come out any way.
    """
    hist = history("Purchases", n=6)
    unusual = v(account="Rent", amount=200_000_000)
    flags, _ = detectors.run(
        unusual, hist, index_of(hist), detectors=detectors.ALL_DETECTORS
    )
    found = problems.find(
        unusual,
        checks.run(unusual, ACCOUNTS),
        index_of(hist).lookup(unusual.party),
        flags,
        ACCOUNTS,
        hist,
        index_of(hist),
    )
    decision = decide_problems(found)
    assert decision.outcome is Outcome.UNCLEAR
    assert decision.question_options, "unclear must come with something to answer"


def test_a_folded_duplicate_alert_still_carries_checkable_evidence():
    """Suppression must not produce a flag nobody can act on."""
    hist = history("Purchases", n=3)
    (flag,), _ = detectors.run(
        v(account="Rent"), hist, index_of(hist), detectors=detectors.ALL_DETECTORS
    )
    assert flag.reason.strip()
    assert any(ch.isdigit() for ch in flag.reason)
    assert "also" in flag.reason  # the corroborating detector's evidence, kept

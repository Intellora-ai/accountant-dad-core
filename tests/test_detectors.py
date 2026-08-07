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

from accountant import problems
from accountant.detect import detectors
from accountant.memory.index import MemoryIndex
from accountant.schema import Flag, MatchResult, MatchStatus, Voucher

TODAY = datetime.date(2026, 8, 7)
ACCOUNTS = ("Purchases", "Rent", "Repairs & Maintenance", "Cash", "Bank")


def v(
    account: str = "Purchases",
    amount: int = 420000,
    gst: int | None = None,
    party: str = "Sharma Traders",
    vid: str = "d1",
) -> Voucher:
    return Voucher(
        id=vid,
        date=TODAY,
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
        v(account=account, amount=amount, gst=gst, party=party, vid=f"h{i}")
        for i in range(n)
    )


def index_of(hist: tuple[Voucher, ...]) -> MemoryIndex:
    return MemoryIndex.from_vouchers(hist)


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


def test_magnitude_fires_above_the_accounts_own_historical_maximum():
    hist = history("Purchases", amount=380000)
    flags = detectors.magnitude(v(amount=200_000_000), hist, index_of(hist))
    assert [f.detector for f in flags] == ["magnitude"]


def test_magnitude_is_silent_exactly_at_the_historical_maximum():
    """The bound is the account's own maximum, inclusive. Equal is not surprising."""
    hist = history("Purchases", amount=380000)
    assert detectors.magnitude(v(amount=380000), hist, index_of(hist)) == []


def test_magnitude_is_silent_one_paise_below_the_maximum():
    hist = history("Purchases", amount=380000)
    assert detectors.magnitude(v(amount=379999), hist, index_of(hist)) == []


def test_magnitude_fires_one_paise_above_the_maximum():
    hist = history("Purchases", amount=380000)
    assert len(detectors.magnitude(v(amount=380001), hist, index_of(hist))) == 1


def test_magnitude_says_nothing_when_the_account_has_no_history():
    """No observed range means no claim. Never an invented multiplier."""
    hist = history("Rent")
    assert detectors.magnitude(v(account="Purchases"), hist, index_of(hist)) == []


def test_magnitude_reason_states_both_numbers_and_the_sample_size():
    hist = history("Purchases", amount=380000, n=4)
    reason = detectors.magnitude(v(amount=200_000_000), hist, index_of(hist))[0].reason
    assert "200000000" in reason
    assert "380000" in reason
    assert "4" in reason


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
    return detectors.magnitude(v(amount=9_000_000), h, index_of(h))


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
    )
    assert dropped == 0
    assert "first_use" in {f.detector for f in flags}


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
    """#3.6 - overflow is reported as a count, never quietly discarded."""
    hist = history("Purchases", gst=None)
    proposed = v(account="Rent", amount=200_000_000, gst=64068)
    uncapped, _ = detectors.run(
        proposed, hist, index_of(hist), detectors=detectors.ALL_DETECTORS
    )
    capped, dropped = detectors.run(
        proposed, hist, index_of(hist), detectors=detectors.ALL_DETECTORS, cap=1
    )
    assert len(capped) == 1
    assert dropped == len(uncapped) - 1


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

"""Tests for the decision order.

The plan requires the three outcomes to be exhaustive and mutually exclusive, and
requires that no model call or network call happens on this path. Both are
asserted here rather than trusted.
"""

from __future__ import annotations

import itertools
import socket

import pytest

from accountant.decide import decide
from accountant.schema import (
    CheckResult,
    Flag,
    MatchResult,
    MatchStatus,
    Outcome,
)

PASSING = CheckResult(name="amount_is_positive", passed=True)
FAILING = CheckResult(
    name="amount_is_positive",
    passed=False,
    detail="amount is 0 paise",
)
UNKNOWN_CHECK = CheckResult(
    name="a_check_nobody_wrote_words_for", passed=False, detail="mystery"
)

MATCH = MatchResult(
    status=MatchStatus.MATCH, vendor_key="sharma_traders", accounts=("Purchases",)
)
CONFLICTED = MatchResult(
    status=MatchStatus.CONFLICTED,
    vendor_key="sharma_traders",
    accounts=("Purchases", "Repairs & Maintenance"),
)
NO_MATCH = MatchResult(status=MatchStatus.NO_MATCH, vendor_key="new_vendor")

FLAG = Flag(
    voucher_id="v1",
    detector="vendor_switch",
    severity=3,
    reason=(
        "Sharma Traders posted to Purchases 40 times; this one goes to Sundry Expenses"
    ),
)


def test_clean_entry_is_valid_and_posts():
    d = decide([PASSING], MATCH, [])
    assert d.outcome is Outcome.VALID
    assert d.post is True


def test_failed_check_now_asks_instead_of_refusing():
    """Rule changed 2026-08-07: if an answer could fix it, ask."""
    d = decide([FAILING], MATCH, [])
    assert d.outcome is Outcome.UNCLEAR
    assert d.post is False


def test_fired_detector_now_asks_instead_of_refusing():
    """A detector firing means surprising, never wrong."""
    d = decide([PASSING], MATCH, [FLAG])
    assert d.outcome is Outcome.UNCLEAR
    assert d.post is False


def test_unseen_vendor_is_unclear_and_does_not_post():
    d = decide([PASSING], NO_MATCH, [])
    assert d.outcome is Outcome.UNCLEAR
    assert d.post is False


def test_conflicted_vendor_is_unclear_and_offers_the_accounts_seen():
    d = decide([PASSING], CONFLICTED, [])
    assert d.outcome is Outcome.UNCLEAR
    assert d.post is False
    # plus a "something else" escape, so the person is never stuck
    assert set(d.question_options) >= {"Purchases", "Repairs & Maintenance"}


def test_conflicted_vendor_never_auto_picks_an_account():
    """Slice 2: there must be no automatic fallback account."""
    d = decide([PASSING], CONFLICTED, [])
    assert d.post is False
    assert len(d.question_options) >= 2


UNANSWERABLE = CheckResult(
    name="amount_is_integer_paise", passed=False, detail="amount is float"
)


def test_not_valid_beats_unclear():
    """If a problem cannot be answered, asking about a different one is pointless."""
    d = decide([UNANSWERABLE], NO_MATCH, [])
    assert d.outcome is Outcome.NOT_VALID


def test_only_unanswerable_problems_refuse():
    """Everything a person could answer becomes a question."""
    assert decide([FAILING], MATCH, []).outcome is Outcome.UNCLEAR
    assert decide([UNANSWERABLE], MATCH, []).outcome is Outcome.NOT_VALID


def test_a_check_with_no_words_refuses_loudly_rather_than_guessing():
    """If nobody wrote a plain-English question for a check, it must not be
    silently treated as askable."""
    assert decide([UNKNOWN_CHECK], MATCH, []).outcome is Outcome.NOT_VALID


def test_no_checks_and_a_match_still_posts():
    """An entry with nothing to check and a known vendor is Valid by definition."""
    d = decide([], MATCH, [])
    assert d.outcome is Outcome.VALID


@pytest.mark.parametrize(
    "checks,match,flags",
    list(
        itertools.product(
            [[], [PASSING], [FAILING], [UNANSWERABLE], [PASSING, UNANSWERABLE]],
            [MATCH, CONFLICTED, NO_MATCH],
            [[], [FLAG]],
        )
    ),
)
def test_outcomes_are_exhaustive_and_mutually_exclusive(checks, match, flags):
    """Every input lands in exactly one of the three outcomes."""
    d = decide(checks, match, flags)
    assert d.outcome in set(Outcome)
    assert d.post is (d.outcome is Outcome.VALID)


def test_all_three_outcomes_are_reachable():
    reached = {
        decide(c, m, f).outcome
        for c, m, f in itertools.product(
            [[], [PASSING], [FAILING], [UNANSWERABLE]],
            [MATCH, CONFLICTED, NO_MATCH],
            [[], [FLAG]],
        )
    }
    assert reached == set(Outcome)


@pytest.mark.parametrize(
    "checks,match,flags",
    list(
        itertools.product(
            [[], [PASSING], [FAILING], [UNANSWERABLE]],
            [MATCH, CONFLICTED, NO_MATCH],
            [[], [FLAG]],
        )
    ),
)
def test_every_decision_states_a_reason(checks, match, flags):
    """A decision nobody can explain is not usable by a reviewer."""
    assert decide(checks, match, flags).reason.strip()


def test_decide_is_pure():
    args = ([PASSING], MATCH, [])
    assert decide(*args) == decide(*args)


def test_no_network_call_on_the_decision_path(monkeypatch):
    """#2.6 and #3.6 in spirit: this path never reaches out."""

    def explode(*_args, **_kwargs):
        raise AssertionError("decision path attempted a network call")

    monkeypatch.setattr(socket, "socket", explode)
    monkeypatch.setattr(socket, "create_connection", explode)
    assert decide([PASSING], MATCH, []).outcome is Outcome.VALID


def test_failed_check_must_state_why():
    with pytest.raises(ValueError):
        CheckResult(name="mystery", passed=False)


def test_flag_must_state_a_reason():
    with pytest.raises(ValueError):
        Flag(voucher_id="v1", detector="vendor_switch", severity=1, reason="   ")


def test_match_result_rejects_inconsistent_account_counts():
    with pytest.raises(ValueError):
        MatchResult(status=MatchStatus.MATCH, vendor_key="x", accounts=("A", "B"))
    with pytest.raises(ValueError):
        MatchResult(status=MatchStatus.NO_MATCH, vendor_key="x", accounts=("A",))
    with pytest.raises(ValueError):
        MatchResult(status=MatchStatus.CONFLICTED, vendor_key="x", accounts=("A",))

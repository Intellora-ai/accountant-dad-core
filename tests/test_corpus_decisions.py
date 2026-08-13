"""A thousand decisions: five hundred that must not post, five hundred that must.

WHY THIS FILE EXISTS
--------------------
Every other test of the cage asks "does this one guard fire". That answers a
question about a branch. It does not answer the question the owner actually
has, which is a RATE: out of everything wrong that could arrive, what share
gets refused, and does anything wrong get written.

A rate needs a population. This file is the population - built by arithmetic,
never by a clock or a random draw, so the same thousand cases exist on every
machine on every run.

THE TWO HALVES, AND WHY THE SECOND ONE IS NOT OPTIONAL
-------------------------------------------------------
A suite made only of refusals cannot tell a cautious system from a broken one.
`return Decided(BLOCK, ...)` on line one of `decide` scores 100% on the invalid
half and is worthless. So there are five hundred clean non-GST purchases as
well, and if the cage refuses those, the refusal rate on the other half means
nothing.

WHAT IT MEASURED, 2026-08-13
------------------------------
    invalid   500/500 refused - 200 ask, 300 block, 0 post. The owner's floor
              is 99.5%, which is 498. Nothing was adjusted to reach it.
    writable  0/500 refusals came back holding a `LedgerEntry`. This is the
              claim that matters, and it is stronger than the percentage above:
              not "it said block" but "there is nothing in the result anybody
              could write".
    valid     500/500 post - 0 ask, 0 block.
    runtime   0.013s to build and decide all thousand.

The numbers are here rather than only in a commit message because a reader
deciding whether to trust this file should not have to run `git log` to find out
what it last measured, and a number that drifts should be visible in the diff.

WHAT THIS FILE DOES NOT PROVE, SAID PLAINLY
---------------------------------------------
It is SYNTHETIC. Every case here is a failure somebody thought to write down,
or one `lying.py` was built to tell. Real bills fail in ways nobody listed, and
nothing here is evidence about real-world accuracy - that needs labelled
invoices this repository does not have (`H-02`).

The arithmetic of a clean run is also worth stating rather than implying: by the
rule of three, zero errors in n = 1000 bounds the true rate at roughly 3/1000 -
0.3% - and no lower. A green run here is consistent with three bad posts in
every thousand bills.

MOST IMPORTANTLY: the five hundred are five hundred failures the cage CLAIMS to
refuse. Refusing all of them is worth exactly that and no more. Three situations
found while building this file must not post and do, and they are held apart in
`_HOLES` rather than averaged into a percentage:

    F-02          a bill misread consistently, every figure ten times too big.
                  Named in `conservation.py` and `decision.py` already.
    the tax flag  `decide` never reads `observation.tax_paise`, so a caller
                  whose `carries_gst=False` contradicts the tax figure on the
                  very reading it was handed posts a GST bill.
    the amount    the conservation verdicts come from the caller's figures and
                  the amount written comes from `observation.total_paise`.
                  Nothing binds them, so laws that passed on ₹1,000 can
                  authorise a write of ₹100,000.

`test_the_measured_blind_spots_all_still_post_and_write_what_they_wrote` asserts
all three, because a blind spot nobody measures is a blind spot nobody notices
closing or opening.

NO CLOCK, NO RANDOMNESS, NO IO, NO NETWORK. Every amount, date and party comes
out of `CORPUS_SEED` and an index.
"""

from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import dataclass
from typing import Final, cast

from accountant.cage import conservation, lying
from accountant.cage.conservation import LAWS, ConservationResult, Verdict
from accountant.cage.decision import (
    ASK_FLOOR,
    AUTO_POST_FLOOR,
    Action,
    Decided,
    Moment,
    Situation,
    decide,
)
from accountant.cage.wall import Field, Observation
from accountant.questions import QUESTION_CAP

#: Written here, once, and never read from a clock or an environment. It is a
#: date, so a reader can tell when this corpus was pinned, and changing it
#: changes every amount in the file - which is why it is a constant a reviewer
#: sees in the diff rather than a parameter.
CORPUS_SEED: Final = 20_260_813

#: Owner's figure, from the plan. Not ours to move: a corpus that lowers its own
#: bar to go green measures the bar, not the product.
REFUSAL_FLOOR: Final = 0.995

#: The whole suite's budget is 180 seconds and it runs in about 34. This is what
#: the corpus is allowed to add, and it is a ceiling far above the measurement
#: so it catches somebody making the corpus slow, not ordinary CI jitter.
BUDGET_SECONDS: Final = 30.0

INVALID_COUNT: Final = 500
VALID_COUNT: Final = 500

_SOURCE: Final = "corpus"
_BASE: Final = 250_000

# ---- family names -----------------------------------------------------------
# Every case belongs to exactly one, and the sizes are written down below so a
# family cannot quietly shrink to nothing while the headline percentage holds.

_ARITHMETIC: Final = "arithmetic"
_PARTY: Final = "party"
_PERIOD: Final = "period"
_GST: Final = "gst"
_BUDGET: Final = "question budget"
_AMOUNT: Final = "amount not money"
_VERDICT: Final = "unknown verdict"
_LAWSET: Final = "malformed law set"
_TWO_WAYS: Final = "read two ways"
_EDGE: Final = "confidence edge"
_LIES: Final = "lying model"
_NOT_SEEN: Final = "not an observation"
_ACCOUNTS: Final = "accounts"
_COUNT: Final = "question count not a number"

#: The shape of the invalid half. Asserted, not aspirational.
FAMILY_SIZES: Final[dict[str, int]] = {
    _ARITHMETIC: 120,
    _PARTY: 48,
    _PERIOD: 26,
    _GST: 36,
    _BUDGET: 16,
    _AMOUNT: 50,
    _VERDICT: 10,
    _LAWSET: 20,
    _TWO_WAYS: 16,
    _EDGE: 48,
    _LIES: 80,
    _NOT_SEEN: 8,
    _ACCOUNTS: 12,
    _COUNT: 10,
}

_PARTIES: Final = (
    "Blue Steel Traders",
    "Kumar Traders",
    "Verma Stationers",
    "Anand Hardware",
    "Sethi Packaging",
    "Rao Electricals",
    "Mehta Timber",
    "Iyer Chemicals",
    "Bose Fasteners",
    "Nair Textiles",
)

#: Deliberately disjoint from `_CREDITS`, so no index pairing can accidentally
#: name the same ledger on both sides and refuse a "valid" case for a reason the
#: corpus never intended to test.
_DEBITS: Final = (
    "Purchases",
    "Repairs & Maintenance",
    "Office Supplies",
    "Freight Inward",
)
_CREDITS: Final = ("Cash", "Bank", "Sundry Creditors")

#: Every clean confidence is at or above the auto-post floor, and the first one
#: IS the floor - the edge that must post, sitting in the control half where a
#: regression turns it into a measured refusal rather than a silent one.
_CLEAN_CONFIDENCES: Final = (0.95, 0.96, 0.97, 0.98, 0.99, 1.0)


@dataclass(frozen=True)
class Case:
    """One situation, and the sentence saying what is wrong with it.

    The label is not decoration. A corpus failure that reads `assert 497 >= 498`
    tells a reviewer nothing they can act on; the same failure listing "lines
    out by 1 paisa on a bill of 431919 paise" sends them straight at the defect.
    """

    family: str
    label: str
    situation: Situation


# ---- deterministic material -------------------------------------------------


def _amount(index: int) -> int:
    """An amount in paise, spread out, from the seed and an index only.

    At least 10,000 paise so a total can always be split into four positive
    lines, which is what keeps `lines_sum_to_total` meaningful on every case.
    """
    return 10_000 + (index * 7_919 + CORPUS_SEED) % 990_000


def _date(index: int) -> str:
    """A real calendar date in ISO form. Never today's."""
    return f"2026-{1 + index % 12:02d}-{1 + index % 28:02d}"


def _lines(total: int, parts: int) -> tuple[int, ...]:
    """`parts` positive line items that add up to exactly `total`."""
    each = total // parts
    return (total - each * (parts - 1), *([each] * (parts - 1)))


def _shift(lines: tuple[int, ...], delta: int) -> tuple[int, ...]:
    """The same lines with one of them out by `delta`. One misread digit."""
    return (lines[0] + delta, *lines[1:])


# ---- builders ---------------------------------------------------------------


def _observation(
    *,
    party: object = "Blue Steel Traders",
    total: object = _BASE,
    tax: object = 0,
    confidence: float = 1.0,
    lines: tuple[int, ...] | None = None,
    date: object = "2026-08-12",
) -> Observation:
    """What the reader claims it saw. Inert, by the wall's construction."""
    return Observation(
        date=Field(value=date, confidence=confidence, source=_SOURCE),
        party=Field(value=party, confidence=confidence, source=_SOURCE),
        total_paise=Field(value=total, confidence=confidence, source=_SOURCE),
        tax_paise=Field(value=tax, confidence=confidence, source=_SOURCE),
        line_paise=lines,
    )


def _laws(
    total: int,
    *,
    lines: tuple[int, ...] | None = None,
    tax: int = 0,
    debit: int | None = None,
    credit: int | None = None,
    stated: int | None = None,
    net: int | None = None,
    gross: int | None = None,
    moved: int | None = None,
) -> tuple[ConservationResult, ...]:
    """Four verdicts, computed by `conservation.run` rather than hand-written.

    Hand-written results would let this file assert against verdicts the real
    laws never produce. Every override here changes an INPUT and lets the laws
    reach their own conclusion, so a case labelled "out by 1 paisa" is out by
    1 paisa according to the module that will judge it in production.
    """
    lines = _lines(total, 2) if lines is None else lines
    debit = total if debit is None else debit
    gross = total if gross is None else gross
    return conservation.run(
        debit_paise=debit,
        credit_paise=total if credit is None else credit,
        line_paise=lines,
        total_paise=total if stated is None else stated,
        net_paise=gross - tax if net is None else net,
        tax_paise=tax,
        gross_paise=gross,
        balance_before_paise=0,
        balance_after_paise=debit if moved is None else moved,
    )


_UNSET: Final = object()


def _situation(
    *,
    observation: object = _UNSET,
    conservation_results: object = _UNSET,
    party_known: object = True,
    period_open: object = True,
    carries_gst: object = False,
    questions_asked: object = 0,
    debit_account: object = "Purchases",
    credit_account: object = "Cash",
    ambiguous_fields: object = (),
    moment: object = Moment.AFTER_THE_WRITE,
) -> Situation:
    """A clean, boring, fully readable purchase unless told otherwise.

    Every parameter is `object` and cast on the way in, because half this corpus
    is about what `decide` does with a field of the wrong type - and a builder
    that refused those could not build them.

    `moment` is AFTER_THE_WRITE for every case here, and that is not a
    convenience: every conservation result in this file is computed from a real
    before-balance and a real after-balance, so `balance_delta_equals_entry`
    genuinely has an answer. A corpus that said BEFORE_THE_WRITE while handing
    over a balance the caller could not yet know would be lying about which
    moment it was measuring.
    """
    return Situation(
        observation=cast(
            Observation, _observation() if observation is _UNSET else observation
        ),
        conservation=cast(
            "tuple[ConservationResult, ...]",
            _laws(_BASE) if conservation_results is _UNSET else conservation_results,
        ),
        party_known=cast("bool | None", party_known),
        period_open=cast("bool | None", period_open),
        carries_gst=cast("bool | None", carries_gst),
        questions_asked=cast(int, questions_asked),
        debit_account=cast(str, debit_account),
        credit_account=cast(str, credit_account),
        ambiguous_fields=cast("tuple[str, ...]", ambiguous_fields),
        moment=cast(Moment, moment),
    )


# ---- the invalid families ---------------------------------------------------


def _arithmetic_cases() -> list[Case]:
    """Six ways for a bill's own numbers to contradict each other, twenty times.

    Every one of these reaches `decide` at confidence 1.0. That is the point:
    the module must let arithmetic beat certainty, and a corpus that hedged the
    confidence would be proving the confidence band works instead.
    """
    cases: list[Case] = []
    for i in range(20):
        total = _amount(i)
        lines = _lines(total, 2 + i % 3)
        seen = _observation(total=total, lines=lines, party=_PARTIES[i % 10])
        for how, results in (
            (f"the lines are out by 1 paisa on {total} paise", _shift(lines, 1)),
            (f"the lines are out by 1 rupee on {total} paise", _shift(lines, 100)),
            (
                f"the lines are out by a factor of ten on {total} paise",
                tuple(value * 10 for value in lines),
            ),
        ):
            cases.append(
                Case(
                    _ARITHMETIC,
                    how,
                    _situation(
                        observation=seen,
                        conservation_results=_laws(total, lines=results),
                    ),
                )
            )
        cases.append(
            Case(
                _ARITHMETIC,
                f"net plus tax is 1 rupee short of the gross on {total} paise",
                _situation(
                    observation=seen,
                    conservation_results=_laws(total, lines=lines, net=total - 100),
                ),
            )
        )
        cases.append(
            Case(
                _ARITHMETIC,
                f"the two sides do not balance on {total} paise",
                _situation(
                    observation=seen,
                    conservation_results=_laws(total, lines=lines, credit=total + 1),
                ),
            )
        )
        cases.append(
            Case(
                _ARITHMETIC,
                f"a stated total of zero against lines worth {total} paise",
                _situation(
                    observation=_observation(total=0, lines=lines),
                    conservation_results=_laws(
                        total, lines=lines, stated=0, gross=0, net=0
                    ),
                ),
            )
        )
    return cases


def _party_cases() -> list[Case]:
    """Nobody's name goes into somebody's books on our say-so."""
    cases: list[Case] = []
    for i in range(12):
        total = _amount(i + 100)
        clean = _laws(total)
        for how, party, known in (
            ("this party is on nobody's books", _PARTIES[i % 10], False),
            ("the name read off the bill is empty", "", True),
            ("the name read off the bill is whitespace", "   ", True),
            ("nobody looked up whether this party is known", _PARTIES[i % 10], None),
        ):
            cases.append(
                Case(
                    _PARTY,
                    f"{how} ({total} paise)",
                    _situation(
                        observation=_observation(party=party, total=total),
                        conservation_results=clean,
                        party_known=known,
                    ),
                )
            )
    return cases


def _period_cases() -> list[Case]:
    """A shut book and a book nobody opened are different facts. Both refuse."""
    cases: list[Case] = []
    for i in range(13):
        total = _amount(i + 200)
        for how, open_ in (
            ("the books for this date are closed", False),
            ("nobody looked up whether the books are open", None),
        ):
            cases.append(
                Case(
                    _PERIOD,
                    f"{how} ({_date(i)}, {total} paise)",
                    _situation(
                        observation=_observation(total=total, date=_date(i)),
                        conservation_results=_laws(total),
                        period_open=open_,
                    ),
                )
            )
    return cases


#: Every scale of tax from the smallest coin to more than the bill it sits on.
#: The last four are larger than the 250,000 paise net, which is the case a
#: threshold written as a percentage would wave through.
_GST_SCALES: Final = (
    1, 2, 5, 10, 25, 50, 99, 100, 101, 250,
    500, 999, 1_000, 1_800, 2_500, 5_000, 9_000, 10_000, 18_000, 25_000,
    45_000, 50_000, 90_000, 125_000, 200_000, 250_000, 300_000, 500_000,
    1_000_000, 2_500_000,
)  # fmt: skip


def _gst_cases() -> list[Case]:
    """Tax posting is off (owner decision Q3=D), so any tax at all refuses."""
    cases: list[Case] = []
    for tax in _GST_SCALES:
        total = _BASE + tax
        lines = (*_lines(_BASE, 2), tax)
        cases.append(
            Case(
                _GST,
                f"GST of {tax} paise on a bill of {total} paise",
                _situation(
                    observation=_observation(total=total, tax=tax, lines=lines),
                    conservation_results=_laws(total, lines=lines, tax=tax),
                    carries_gst=True,
                ),
            )
        )
    for i in range(6):
        total = _amount(i + 300)
        cases.append(
            Case(
                _GST,
                f"nobody could tell whether there is tax on this {total} paise bill",
                _situation(
                    observation=_observation(total=total),
                    conservation_results=_laws(total),
                    carries_gst=None,
                ),
            )
        )
    return cases


def _budget_cases() -> list[Case]:
    """Five questions is all there is. A product that will not take no is worse."""
    cases: list[Case] = []
    for i in range(8):
        total = _amount(i + 400)
        for how, asked in (
            (f"the question budget of {QUESTION_CAP} is already spent", QUESTION_CAP),
            ("one question past the budget", QUESTION_CAP + 1),
        ):
            cases.append(
                Case(
                    _BUDGET,
                    f"{how} ({total} paise)",
                    _situation(
                        observation=_observation(total=total),
                        conservation_results=_laws(total),
                        questions_asked=asked,
                    ),
                )
            )
    return cases


def _amount_cases() -> list[Case]:
    """Money is a whole number of paise, above zero. Everything else is refused.

    The conservation results are built from the REAL total, so the only thing
    wrong with each of these is the figure the reader claims to have read.
    """
    cases: list[Case] = []
    for i in range(10):
        total = _amount(i + 500)
        clean = _laws(total)
        for how, value in (
            ("a float", float(total) + 0.5),
            ("a string", str(total)),
            ("a bool", True),
            ("a negative amount", -total),
            ("zero", 0),
        ):
            cases.append(
                Case(
                    _AMOUNT,
                    f"the amount read off a {total} paise bill is {how} ({value!r})",
                    _situation(
                        observation=_observation(total=value),
                        conservation_results=clean,
                    ),
                )
            )
    return cases


#: Things that are not a `Verdict`. `"pass"` and `"fail"` are the dangerous two:
#: `Verdict` is a `StrEnum`, so `Verdict.FAIL == "fail"` is True and any guard
#: written with `==` instead of a type check reads these as real verdicts.
_STRANGE_VERDICTS: Final[tuple[object, ...]] = (
    "pass",
    "PASS",
    "fail",
    "indeterminate",
    "ok",
    "",
    None,
    1,
    True,
    ("pass",),
)


def _verdict_cases() -> list[Case]:
    """A law that came back with something that is not one of the three."""
    clean = _laws(_BASE)
    return [
        Case(
            _VERDICT,
            f"the first law came back with {value!r} instead of a verdict",
            _situation(
                conservation_results=(
                    ConservationResult(
                        law=LAWS[0], verdict=cast(Verdict, value), said="?"
                    ),
                    *clean[1:],
                )
            ),
        )
        for value in _STRANGE_VERDICTS
    ]


def _lawset_cases() -> list[Case]:
    """A law missing, out of order, renamed, or not a law at all.

    `LAWS` order is part of the contract precisely so this set is checkable. A
    caller handing over three results has not run four checks, and three passes
    out of four is not a pass.
    """
    clean = _laws(_BASE)
    renamed = tuple(
        ConservationResult(law=f"not_{r.law}", verdict=r.verdict, said=r.said)
        for r in clean
    )
    broken: tuple[tuple[str, object], ...] = (
        ("the first law is missing", clean[1:]),
        ("the second law is missing", (clean[0], *clean[2:])),
        ("the third law is missing", (*clean[:2], clean[3])),
        ("the fourth law is missing", clean[:3]),
        ("the first two laws are swapped", (clean[1], clean[0], *clean[2:])),
        ("the middle two laws are swapped", (clean[0], clean[2], clean[1], clean[3])),
        ("the last two laws are swapped", (*clean[:2], clean[3], clean[2])),
        ("the laws arrive in reverse order", tuple(reversed(clean))),
        ("the laws arrive rotated by one", (*clean[1:], clean[0])),
        ("the law set is a list, not a tuple", list(clean)),
        ("the law set is empty", ()),
        ("the first law is there twice and the last is gone", (clean[0], *clean[:3])),
        ("a fifth law nobody wrote is appended", (*clean, clean[0])),
        ("the fourth law is a bare string", (*clean[:3], LAWS[3])),
        ("the fourth law is None", (*clean[:3], None)),
        ("the fourth law is a dict shaped like one", (*clean[:3], {"law": LAWS[3]})),
        ("every law has been renamed", renamed),
        ("the law set is the law NAMES only", LAWS),
        ("the law set is None", None),
        ("one result arrives on its own, not in a tuple", clean[0]),
    )
    return [Case(_LAWSET, how, _situation(conservation_results=v)) for how, v in broken]


def _two_ways_cases() -> list[Case]:
    """A date that is either the 3rd of April or the 4th of March.

    Ten cases where the caller said so properly, and six where the list of them
    is unreadable - which is itself something nobody checked.
    """
    cases: list[Case] = [
        Case(
            _TWO_WAYS,
            f"{n} field(s) on this bill can be read more than one way",
            _situation(ambiguous_fields=tuple(f"field_{k}" for k in range(n))),
        )
        for n in range(1, 11)
    ]
    unreadable: tuple[object, ...] = (["date"], "date", None, {"date"}, {"date": 1}, 3)
    cases.extend(
        Case(
            _TWO_WAYS,
            f"the list of unclear fields is {value!r}, which is not a list of them",
            _situation(ambiguous_fields=value),
        )
        for value in unreadable
    )
    return cases


#: The band edges, and the far side of each. `0.95` and `1.0` are paired with a
#: broken law on purpose: alone they are POSTABLE, and a corpus that filed them
#: as invalid would be asserting the cage is wrong to post them.
_EDGE_CONFIDENCES: Final[tuple[tuple[float, bool, str], ...]] = (
    (0.69, False, "just under the ask floor"),
    (0.6999, False, "a whisker under the ask floor"),
    (0.70, False, "exactly on the ask floor"),
    (0.7001, False, "a whisker over the ask floor"),
    (0.94, False, "just under the auto-post floor"),
    (0.9499, False, "a whisker under the auto-post floor"),
    (0.95, True, "exactly on the auto-post floor, with the two sides unbalanced"),
    (1.0, True, "completely certain, with the two sides unbalanced"),
)


def _edge_cases() -> list[Case]:
    """Off-by-one lives on the band edges, so every edge is here six times."""
    cases: list[Case] = []
    for i in range(6):
        total = _amount(i + 600)
        for score, needs_defect, how in _EDGE_CONFIDENCES:
            results = _laws(total, credit=total + 1) if needs_defect else _laws(total)
            cases.append(
                Case(
                    _EDGE,
                    f"confidence {score} - {how} ({total} paise)",
                    _situation(
                        observation=_observation(total=total, confidence=score),
                        conservation_results=results,
                    ),
                )
            )
    return cases


#: The four lies whose damage arithmetic or the ledger CAN see. The fifth,
#: `SELF_CONSISTENT_WRONG_TOTAL`, is failure mode F-02 and is deliberately not
#: here - it is measured on its own, below, as a blind spot rather than smuggled
#: into a percentage it would quietly reduce.
_CORPUS_LIES: Final = (
    lying.Lie.WRONG_AMOUNT,
    lying.Lie.WRONG_PARTY,
    lying.Lie.FLIPPED_SIGN,
    lying.Lie.INVENTED_VENDOR,
)


#: Parties a truth may name. The two the stub swaps IN are excluded, and that is
#: not tidiness: a truth already named "Verma Stationers" disarms the
#: wrong-party lie completely, and `lying.py` raises `StubToldTheTruthError`
#: rather than hand back a lie that is the truth. It caught this file on its
#: first run.
_TRUTHFUL_PARTIES: Final = tuple(
    name
    for name in _PARTIES
    if name not in (lying.ANOTHER_REAL_PARTY, lying.A_PARTY_NOBODY_HAS_HEARD_OF)
)


def _truths() -> tuple[lying.Truth, ...]:
    """Fifteen real, zero-tax bills for the stub to lie about."""
    made: list[lying.Truth] = []
    for i in range(15):
        net = _amount(i + 700)
        made.append(
            lying.Truth(
                date=_date(i),
                party=_TRUTHFUL_PARTIES[i % len(_TRUTHFUL_PARTIES)],
                net_paise=net,
                tax_paise=0,
                line_paise=_lines(net, 1 + i % 3),
            )
        )
    return tuple(made)


def _from_lie(truth: lying.Truth, seen: Observation, label: str) -> Case:
    """A situation built around what the stub SAID, checked against what moved.

    The books move by the truthful amount because that is what a real posting
    would do; the laws are handed the observed figures. That gap is exactly
    where an inconsistent lie becomes visible, and where a consistent one does
    not.
    """
    total = cast(int, seen.total_paise.value)
    tax = cast(int, seen.tax_paise.value)
    return Case(
        _LIES,
        label,
        _situation(
            observation=seen,
            conservation_results=conservation.run(
                debit_paise=total,
                credit_paise=total,
                line_paise=seen.line_paise,
                total_paise=total,
                net_paise=total - tax,
                tax_paise=tax,
                gross_paise=total,
                balance_before_paise=0,
                balance_after_paise=truth.total_paise,
            ),
            party_known=seen.party.value == truth.party,
        ),
    )


def _lying_cases() -> list[Case]:
    """Sixty named lies, ten photos with the amounts cut off, ten of a thumb.

    Told at full confidence, always. A hedged lie is stopped by the confidence
    band and proves only that low scores are refused, which we already know.
    """
    truths = _truths()
    cases = [
        _from_lie(
            truth,
            lying.observe(lying.Mode.GARBAGE, truth=truth, lie=lie, seed=CORPUS_SEED),
            f"the reader told {lie.value} about {truth.party}'s bill for "
            f"{truth.total_paise} paise",
        )
        for truth in truths
        for lie in _CORPUS_LIES
    ]
    for i, truth in enumerate(truths[:10]):
        seen = lying.observe(lying.Mode.PARTIAL, truth=truth)
        cases.append(
            Case(
                _LIES,
                f"the amount block is off the edge of the photo of {truth.party}'s "
                f"bill for {truth.total_paise} paise",
                _situation(
                    observation=seen,
                    conservation_results=conservation.run(
                        debit_paise=None,
                        credit_paise=None,
                        line_paise=None,
                        total_paise=None,
                        net_paise=None,
                        tax_paise=None,
                        gross_paise=None,
                        balance_before_paise=0,
                        balance_after_paise=None,
                    ),
                    carries_gst=None,
                    debit_account=_DEBITS[i % len(_DEBITS)],
                    credit_account=_CREDITS[i % len(_CREDITS)],
                ),
            )
        )
    cases.extend(_unreadable_cases(truths[:10]))
    return cases


def _unreadable_cases(truths: tuple[lying.Truth, ...]) -> list[Case]:
    """Nothing on the page could be read. The photo is a thumb, or a cat."""
    seen = lying.observe(lying.Mode.FAILURE)
    blank = conservation.run(
        debit_paise=None,
        credit_paise=None,
        line_paise=None,
        total_paise=None,
        net_paise=None,
        tax_paise=None,
        gross_paise=None,
        balance_before_paise=0,
        balance_after_paise=None,
    )
    return [
        Case(
            _LIES,
            f"nothing at all could be read off {truth.party}'s bill for "
            f"{truth.total_paise} paise",
            _situation(
                observation=seen,
                conservation_results=blank,
                carries_gst=None,
                party_known=None,
                debit_account=_DEBITS[i % len(_DEBITS)],
                credit_account=_CREDITS[i % len(_CREDITS)],
            ),
        )
        for i, truth in enumerate(truths)
    ]


class _NotQuiteAnObservation(Observation):
    """A subclass of the real thing, which the wall's `type(...) is` refuses.

    Here because a subclass is the one bypass an `isinstance` check would wave
    through, and the direction that fails closed is the one we want measured.
    """


def _not_an_observation_cases() -> list[Case]:
    """Whatever arrived where the reading should have been, it is not one."""
    lookalike = _NotQuiteAnObservation(
        date=Field(value="2026-08-12", confidence=1.0, source=_SOURCE),
        party=Field(value="Blue Steel Traders", confidence=1.0, source=_SOURCE),
        total_paise=Field(value=_BASE, confidence=1.0, source=_SOURCE),
        tax_paise=Field(value=0, confidence=1.0, source=_SOURCE),
    )
    arrived: tuple[object, ...] = (
        None,
        "a string where the reading should be",
        42,
        {},
        [],
        (),
        {"party": "Blue Steel Traders", "total_paise": _BASE},
        lookalike,
    )
    return [
        Case(
            _NOT_SEEN,
            f"what arrived instead of a reading is a {type(value).__name__}: {value!r}",
            _situation(observation=value),
        )
        for value in arrived
    ]


def _account_cases() -> list[Case]:
    """Both sides naming one ledger is a typo no answer fixes. Empty is a question."""
    pairs: tuple[tuple[str, object, object], ...] = (
        ("both sides name Purchases", "Purchases", "Purchases"),
        ("both sides name Cash", "Cash", "Cash"),
        ("both sides name Bank", "Bank", "Bank"),
        ("both sides name Sundry Creditors", "Sundry Creditors", "Sundry Creditors"),
        ("the debit account is None", None, "Cash"),
        ("the credit account is None", "Purchases", None),
        ("the debit account is a number", 42, "Cash"),
        ("the credit account is a list", "Purchases", ["Cash"]),
        ("the debit account is empty", "", "Cash"),
        ("the credit account is empty", "Purchases", ""),
        ("the debit account is whitespace", "   ", "Cash"),
        ("the credit account is whitespace", "Purchases", "   "),
    )
    return [
        Case(
            _ACCOUNTS,
            how,
            _situation(debit_account=debit, credit_account=credit),
        )
        for how, debit, credit in pairs
    ]


def _question_count_cases() -> list[Case]:
    """A count that is not a whole non-negative number is a lost count."""
    counts: tuple[object, ...] = (-1, -100, 0.5, 3.0, "0", "3", True, False, None, ())
    return [
        Case(
            _COUNT,
            f"the number of questions asked so far is {value!r}",
            _situation(questions_asked=value),
        )
        for value in counts
    ]


def invalid_corpus() -> list[Case]:
    """Five hundred situations a correct system must not post."""
    return [
        *_arithmetic_cases(),
        *_party_cases(),
        *_period_cases(),
        *_gst_cases(),
        *_budget_cases(),
        *_amount_cases(),
        *_verdict_cases(),
        *_lawset_cases(),
        *_two_ways_cases(),
        *_edge_cases(),
        *_lying_cases(),
        *_not_an_observation_cases(),
        *_account_cases(),
        *_question_count_cases(),
    ]


def valid_corpus() -> list[Case]:
    """Five hundred clean non-GST purchases. The control, not the decoration.

    Party, amount, line-item count, date, confidence and both ledger accounts
    all vary. Every conservation law is computed by `conservation.run` from
    figures that genuinely agree, so a refusal here is a finding about the cage
    and not about the way this file built its inputs.
    """
    cases: list[Case] = []
    for i in range(VALID_COUNT):
        total = _amount(i + 1_000)
        lines = _lines(total, 1 + i % 4)
        party = _PARTIES[i % len(_PARTIES)]
        cases.append(
            Case(
                "valid",
                f"clean purchase {i}: {party}, {total} paise, {len(lines)} line(s)",
                _situation(
                    observation=_observation(
                        party=party,
                        total=total,
                        lines=lines,
                        date=_date(i),
                        confidence=_CLEAN_CONFIDENCES[i % len(_CLEAN_CONFIDENCES)],
                    ),
                    conservation_results=_laws(total, lines=lines),
                    debit_account=_DEBITS[i % len(_DEBITS)],
                    credit_account=_CREDITS[i % len(_CREDITS)],
                ),
            )
        )
    return cases


def _run(cases: list[Case]) -> list[tuple[Case, Decided]]:
    """Decide every case, naming the case if the module raises on one.

    `decide` promises never to raise on a situation it was handed. If it does,
    that is the single most valuable thing this file can report, and it must
    arrive with the label attached rather than as a bare traceback.
    """
    out: list[tuple[Case, Decided]] = []
    for case in cases:
        try:
            out.append((case, decide(case.situation)))
        except Exception as raised:
            raise AssertionError(
                f"decide raised on {case.label!r} ({case.family}): {raised!r}"
            ) from raised
    return out


_INVALID: Final = invalid_corpus()
_VALID: Final = valid_corpus()
_INVALID_RUN: Final = _run(_INVALID)
_VALID_RUN: Final = _run(_VALID)


def _labels(run: list[tuple[Case, Decided]], action: Action) -> list[str]:
    return [f"  [{c.family}] {c.label}" for c, d in run if d.action is action]


# ---- what the corpus asserts ------------------------------------------------


def test_the_invalid_corpus_is_five_hundred_distinct_labelled_cases() -> None:
    """A label per case, and no two the same, or a failure names the wrong one."""
    assert len(_INVALID) == INVALID_COUNT
    assert all(case.label.strip() for case in _INVALID)
    duplicated = [k for k, n in Counter(c.label for c in _INVALID).items() if n > 1]
    assert not duplicated, f"labels used more than once: {duplicated}"


def test_the_valid_corpus_is_five_hundred_distinct_labelled_cases() -> None:
    assert len(_VALID) == VALID_COUNT
    duplicated = [k for k, n in Counter(c.label for c in _VALID).items() if n > 1]
    assert not duplicated, f"labels used more than once: {duplicated}"


def test_every_family_is_the_size_it_was_written_down_as() -> None:
    """So a family cannot quietly shrink to nothing while the headline holds."""
    assert sum(FAMILY_SIZES.values()) == INVALID_COUNT
    assert dict(Counter(case.family for case in _INVALID)) == FAMILY_SIZES


def test_at_least_the_owners_share_of_invalid_cases_is_refused() -> None:
    """The owner's 99.5%. Not ours to move, so the corpus is never adjusted."""
    posted = _labels(_INVALID_RUN, Action.POST)
    refused = len(_INVALID_RUN) - len(posted)
    needed = math.ceil(REFUSAL_FLOOR * len(_INVALID_RUN))
    assert refused >= needed, (
        f"{refused}/{len(_INVALID_RUN)} invalid cases refused; {needed} needed "
        f"at {REFUSAL_FLOOR:.1%}. These posted and must not have:\n" + "\n".join(posted)
    )


def test_no_invalid_case_produces_anything_writable() -> None:
    """The stronger claim: not "it said block" but "there is nothing to write"."""
    carrying = [
        f"  [{c.family}] {c.label} -> {d.action}"
        for c, d in _INVALID_RUN
        if d.entry is not None
    ]
    assert not carrying, (
        "these refused cases came back holding a writable ledger entry, which "
        "is one attribute access from posting the thing we just refused:\n"
        + "\n".join(carrying)
    )


def test_the_invalid_half_is_refused_both_ways_and_not_all_one_way() -> None:
    """A corpus answered entirely with BLOCK would not be testing the middle band."""
    split = Counter(d.action for _, d in _INVALID_RUN)
    assert split[Action.ASK] > 0, "nothing in 500 invalid cases was worth asking about"
    assert split[Action.BLOCK] > 0, "nothing in 500 invalid cases was hard-refused"


def test_the_valid_corpus_is_not_all_refused() -> None:
    """The control. If these are refused, the other half's rate means nothing."""
    split = Counter(d.action for _, d in _VALID_RUN)
    refused = _labels(_VALID_RUN, Action.ASK) + _labels(_VALID_RUN, Action.BLOCK)
    assert split[Action.POST] == VALID_COUNT, (
        f"measured split of {VALID_COUNT} clean non-GST purchases: "
        f"{split[Action.POST]} post, {split[Action.ASK]} ask, "
        f"{split[Action.BLOCK]} block. Refused:\n" + "\n".join(refused[:20])
    )


def test_every_posted_valid_case_carries_the_entry_it_decided_to_write() -> None:
    """A post with nothing to write is a contradiction the type already forbids."""
    for case, decided in _VALID_RUN:
        if decided.action is not Action.POST:
            continue
        entry = decided.entry
        assert entry is not None, case.label
        assert entry.amount_paise == case.situation.observation.total_paise.value
        assert entry.debit_account != entry.credit_account, case.label


def test_the_whole_corpus_decides_identically_when_built_and_run_twice() -> None:
    """No clock, no entropy: two builds produce byte-identical decisions."""
    first = [
        (case.label, repr(decide(case.situation)))
        for case in (*invalid_corpus(), *valid_corpus())
    ]
    second = [
        (case.label, repr(decide(case.situation)))
        for case in (*invalid_corpus(), *valid_corpus())
    ]
    assert first == second
    assert len(first) == INVALID_COUNT + VALID_COUNT


def test_the_corpus_costs_well_under_its_share_of_the_suite_budget() -> None:
    """Measured, not asserted in words. The ceiling is far above the reading."""
    start = time.perf_counter()
    for case in (*invalid_corpus(), *valid_corpus()):
        decide(case.situation)
    elapsed = time.perf_counter() - start
    assert elapsed < BUDGET_SECONDS, (
        f"building and deciding {INVALID_COUNT + VALID_COUNT} cases took "
        f"{elapsed:.2f}s, over the {BUDGET_SECONDS}s this corpus may add."
    )


def test_the_bands_are_still_the_numbers_the_edges_were_chosen_for() -> None:
    """Move a band and the edge cases stop being edges. This says so out loud."""
    assert ASK_FLOOR == 0.70
    assert AUTO_POST_FLOOR == 0.95
    scores = {score for score, _, _ in _EDGE_CONFIDENCES}
    assert {0.69, 0.70, 0.94, 0.95} <= scores


def test_both_sides_of_each_band_edge_are_actually_in_the_corpus() -> None:
    """The edge confidences reach `decide`, rather than only being declared."""
    seen = {
        case.situation.observation.party.confidence
        for case in _INVALID
        if case.family == _EDGE
    }
    assert {0.69, 0.70, 0.94, 0.95} <= seen


#: The bill misread CONSISTENTLY - every figure ten times too big, so the lines
#: still sum to the total and net plus tax still equals gross.
_F02_TRUTH: Final = lying.Truth(
    date="2026-05-04",
    party="Kumar Traders",
    net_paise=100_000,
    tax_paise=0,
    line_paise=(40_000, 60_000),
)


def _blind_spot_situation() -> Situation:
    """Failure mode F-02, assembled exactly as a real consistent misread would be."""
    seen = lying.observe(
        lying.Mode.GARBAGE,
        truth=_F02_TRUTH,
        lie=lying.Lie.SELF_CONSISTENT_WRONG_TOTAL,
        seed=CORPUS_SEED,
    )
    total = cast(int, seen.total_paise.value)
    return _situation(
        observation=seen,
        conservation_results=conservation.run(
            debit_paise=total,
            credit_paise=total,
            line_paise=seen.line_paise,
            total_paise=total,
            net_paise=total,
            tax_paise=0,
            gross_paise=total,
            balance_before_paise=0,
            balance_after_paise=total,
        ),
    )


@dataclass(frozen=True)
class Hole:
    """A situation that must not post, and does. Measured, not argued about.

    These are NOT in the five hundred. The five hundred are failures the cage
    claims to refuse, and mixing in failures it does not claim to refuse would
    turn one number into an average of two different questions. They are here,
    with what each one writes into the books, because the count of known holes
    is itself a quantity - and one that must fail a test the day it changes in
    either direction.
    """

    label: str
    situation: Situation
    writes_paise: int


def _tax_flag_disagrees_with_the_reading() -> Situation:
    """The caller says there is no tax. The reading in hand says 18,000 paise.

    `decide` never reads `observation.tax_paise`. The GST hard rule rests
    entirely on a flag the caller computed somewhere else, while the figure that
    contradicts it is sitting in the same argument. Two statements about one
    fact, both present, never compared.
    """
    lines = (100_000, 18_000)
    return _situation(
        observation=_observation(total=118_000, tax=18_000, lines=lines),
        conservation_results=_laws(118_000, lines=lines, tax=18_000),
        carries_gst=False,
    )


def _laws_checked_a_different_number() -> Situation:
    """Every law passes on 100,000 paise. The entry written is 10,000,000.

    The verdicts come from the caller's figures; the amount written comes from
    `observation.total_paise`. Nothing binds them, so a passing conservation run
    can authorise a write of a number it never saw.
    """
    return _situation(
        observation=_observation(total=10_000_000),
        conservation_results=_laws(100_000),
    )


_HOLES: Final = (
    Hole(
        "a bill misread consistently, every figure ten times too big (F-02)",
        _blind_spot_situation(),
        1_000_000,
    ),
    Hole(
        "the caller's no-tax flag contradicts the tax figure on the reading",
        _tax_flag_disagrees_with_the_reading(),
        118_000,
    ),
    Hole(
        "the laws passed on 100,000 paise and the entry written is 10,000,000",
        _laws_checked_a_different_number(),
        10_000_000,
    ),
)


def test_the_measured_blind_spots_all_still_post_and_write_what_they_wrote() -> None:
    """Three situations that must not post, and do. This is a finding, not a pass.

    Asserted rather than described so the number of known holes cannot drift.
    A hole that closes fails this test, which is the only way anybody notices;
    a hole that opens wider changes the amount written, which fails it too.
    """
    for hole in _HOLES:
        decided = decide(hole.situation)
        assert decided.action is Action.POST, f"no longer posts: {hole.label}"
        assert decided.entry is not None, hole.label
        assert decided.entry.amount_paise == hole.writes_paise, hole.label


def test_the_known_blind_spot_still_posts_a_consistently_misread_bill() -> None:
    """F-02, measured rather than described. This is NOT a passing safety claim.

    Every conservation law holds, every field is legible, confidence is 1.0, and
    a bill for a thousand rupees is posted as ten thousand. `conservation.py`
    and `decision.py` both say in their own docstrings that nothing here can see
    it. The day something can, this test fails - which is the only way a blind
    spot closing gets noticed.
    """
    decided = decide(_blind_spot_situation())
    assert decided.action is Action.POST
    assert decided.entry is not None
    scaled = _F02_TRUTH.total_paise * lying.SELF_CONSISTENT_SCALE
    assert decided.entry.amount_paise == scaled
    assert scaled == 1_000_000

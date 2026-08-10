"""Plain-language questions.

Two rules from the frozen plan, both enforced by test:

  S7  No question may contain a ledger account name. Ask about the thing, not
      the account. "Was this stuff you'll sell on?" not "Purchases or Repairs?"

  Non-overlapping  No two questions about one entry may resolve the same
      problem. Every question carries a distinct problem id.

Question budget: 5 per entry, or until a question would not change the outcome,
whichever comes first. Owner-set.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

QUESTION_CAP = 5  # owner-set 2026-08-07: "5 non overlapping questions"


def _has_phrase(text: str, phrase: str) -> bool:
    """Whole-word match, so 'Rent' does not fire inside 'different'."""
    return re.search(rf"\b{re.escape(phrase)}\b", text, re.I) is not None


def is_jargon(account: str) -> bool:
    """True if a person would need accounting knowledge to understand the name.

    An account is NOT jargon when the plain-English description already uses the
    same word — "Cash" is called "cash", "Rent" is called "rent for a place".
    It IS jargon when we have to describe it differently — "Purchases" becomes
    "stuff you'll sell on".

    An account with no plain description at all counts as jargon, so unmapped
    accounts can never quietly reach the person.
    """
    plain = PLAIN.get(account)
    if plain is None:
        return True
    return not all(_has_phrase(plain, w) for w in account.split() if len(w) > 2)


class NoAnswerableOption(ValueError):
    """A question whose options would all be invented. Never asked."""


RETYPE = "__retype__"
YES = "__yes__"
HANDOVER = "__handover__"


@dataclass(frozen=True)
class Answer:
    """One thing the person can click. `label` is what they read, `value` is
    what it means to us — usually an account name, sometimes an action."""

    label: str
    value: str


@dataclass(frozen=True)
class Question:
    problem_id: str
    text: str
    answers: tuple[Answer, ...]

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError(f"question {self.problem_id!r} has no text")
        if not self.answers:
            raise ValueError(f"question {self.problem_id!r} has no answers")

    def mentions_any(self, accounts: Sequence[str]) -> list[str]:
        """S7 check. Returns jargon account names leaking into the question.

        An account name only counts as a leak if a person would have to know
        accounting to understand it. Two things follow:

        * Whole words only. "Rent" inside "different" is not a mention.
        * A word already used in the plain-English description is not jargon.
          "Cash" is fine because we call it "cash". "Purchases" is not, because
          we call it "stuff you'll sell on".
        """
        return [a for a in accounts if is_jargon(a) and _has_phrase(self.text, a)]


# ---- the phrasebook ---------------------------------------------------------
# Ledger name -> what a person actually calls it. Child 9 owns the full version
# with Schedule III citations; this is the working set for the accounts we see.

PLAIN: dict[str, str] = {
    "Purchases": "stuff you'll sell on",
    "Repairs & Maintenance": "fixing something you already own",
    "Printing & Stationery": "office bits, paper, pens",
    "Rent": "rent for a place",
    "Electricity Charges": "a bill, like power or water",
    "Sundry Expenses": "a small one-off thing",
    "Cash": "cash",
    "Bank": "from the bank",
    "Salaries": "paying someone who works for you",
    "Freight & Transport": "moving goods around",
    "Professional Fees": "paying an expert for advice",
}


def plain_name(account: str) -> str | None:
    """Plain English for a ledger name, or None if we have no words for it.

    #9.7: an unmapped account is reported, never shown as raw jargon.
    """
    return PLAIN.get(account)


def unmapped(accounts: Sequence[str]) -> list[str]:
    """The accounts in this chart we have no plain words for.

    Called by `_something_else` on every question built from a chart, so the
    count reaches the person. It used to be called by nothing but its own test.
    """
    return [a for a in accounts if a not in PLAIN]


def _something_else(accounts: Sequence[str]) -> Answer:
    """The escape option, and the report of what it is standing in for.

    An account with no plain words is not offered as a choice: its label would
    have to be invented, and an option is a promise that the thing exists — the
    lesson `how_paid` below is written around. But dropping it and saying
    nothing is worse than jargon. The person is never told a correct answer is
    missing, so an unreachable answer looks exactly like a complete list, and
    #9.7 fails silently where nobody can see it.

    So the count rides on the one label the escape already shows. Two channels
    reach the person and only two — `Question.text` and `Answer.label`, rendered
    at `web/app.py:1415` and `web/app.py:1409` — and a report in neither is not
    a report. The number, never the name: S7 holds in the label exactly as it
    holds in the text.

    Same shape as the G6.2 overflow count at `web/app.py:1392`: reported as a
    number, never silently dropped, and nothing said at zero, because "0 things
    I have no words for" is noise on every clean entry.
    """
    missing = unmapped(accounts)
    if not missing:
        return Answer(label="something else", value=HANDOVER)
    thing = "thing" if len(missing) == 1 else "things"
    return Answer(
        label=(
            f"something else — {len(missing)} {thing} in your books I have no words for"
        ),
        value=HANDOVER,
    )


def purpose_answers(accounts: Sequence[str]) -> tuple[Answer, ...]:
    """Turn a chart of accounts into plain-English choices.

    Accounts we have no words for are counted on the escape rather than shown as
    jargon, and never dropped in silence. The escape is always offered so the
    person is never stuck.
    """
    out = [
        Answer(label=PLAIN[a], value=a)
        for a in accounts
        if a in PLAIN and a not in ("Cash", "Bank")
    ]
    out.append(_something_else(accounts))
    return tuple(out)


# ---- question builders ------------------------------------------------------


def rupees(paise: int) -> str:
    whole = paise // 100
    return f"₹{whole:,}"


def which_purpose(accounts: Sequence[str], party: str) -> Question:
    who = party.strip() or "them"
    return Question(
        problem_id="which_account",
        text=f"What did you get from {who}?",
        answers=purpose_answers(accounts),
    )


def which_purpose_narrowed(
    accounts: Sequence[str], party: str, seen: Sequence[str]
) -> Question:
    """Vendor used more than one account before. Offer only those, in plain words.

    The count on the escape is over `seen`, not the whole chart: the question
    narrows to what this vendor has actually been posted to, so that is the set
    an answer can go missing from.
    """
    who = party.strip() or "them"
    opts = [Answer(label=PLAIN[a], value=a) for a in seen if a in PLAIN]
    if not opts:
        return which_purpose(accounts, party)
    opts.append(_something_else(seen))
    return Question(
        problem_id="which_account",
        text=f"Last time with {who} it was different things. Which is this one?",
        answers=tuple(opts),
    )


def is_that_amount_right(party: str, amount_paise: int, usual_paise: int) -> Question:
    who = party.strip() or "them"
    return Question(
        problem_id="magnitude",
        text=(
            f"That's {rupees(amount_paise)}. You normally pay {who} around "
            f"{rupees(usual_paise)}. Is that right?"
        ),
        answers=(
            Answer(label="Yes, that's right", value=YES),
            Answer(label="No, let me type it again", value=RETYPE),
        ),
    )


def different_from_usual(party: str, usual: str, times: int) -> Question:
    who = party.strip() or "them"
    usual_plain = PLAIN.get(usual, "the same thing")
    return Question(
        problem_id="vendor_switch",
        text=(
            f"With {who} it's usually {usual_plain} — {times} times so far. "
            f"Is this one different?"
        ),
        answers=(
            Answer(label="Yes, this one is different", value=YES),
            Answer(label=f"No, it's {usual_plain} again", value=usual),
        ),
    )


def first_time_here(party: str) -> Question:
    who = party.strip() or "them"
    return Question(
        problem_id="first_use",
        text=f"You've never put anything from {who} here before. Is that right?",
        answers=(
            Answer(label="Yes, that's right", value=YES),
            Answer(label="No, let me pick again", value=RETYPE),
        ),
    )


def gst_looks_odd(amount_paise: int, gst_paise: int) -> Question:
    return Question(
        problem_id="gst_anomaly",
        text=(
            f"You've put {rupees(gst_paise)} of tax on this "
            f"{rupees(amount_paise)}. Nothing like this has had tax on it before. "
            f"Is there tax on this one?"
        ),
        answers=(
            Answer(label="Yes, there's tax", value=YES),
            Answer(label="No, no tax", value=RETYPE),
        ),
    )


def how_much(party: str) -> Question:
    who = party.strip() or "them"
    return Question(
        problem_id="amount",
        text=f"How much did you pay {who}? I couldn't work it out.",
        answers=(Answer(label="Let me type it again", value=RETYPE),),
    )


def who_was_it(_: str = "") -> Question:
    return Question(
        problem_id="party",
        text="Who did you pay? I couldn't work it out.",
        answers=(Answer(label="Let me type it again", value=RETYPE),),
    )


def how_paid(accounts: Sequence[str]) -> Question:
    """How the money left the business. Options come from the real chart.

    Raises when the company has neither ledger, because a question with no
    answerable options is worse than no question: it strands the person. The
    caller turns that into an unanswerable problem, which is NOT_VALID and
    honest.
    """
    # ONLY ledgers this company actually has. The old code fell back to
    # `Answer(label="cash", value="Cash")` when the chart contained neither
    # Cash nor Bank - offering an account that does not exist, so the person
    # clicks it and we post to a ledger Tally will refuse. An option is a
    # promise that the thing exists.
    opts = [Answer(label=PLAIN[a], value=a) for a in ("Cash", "Bank") if a in accounts]
    if not opts:
        raise NoAnswerableOption(
            "this company has no Cash and no Bank ledger, so there is no "
            "honest way to ask how the money was paid"
        )
    return Question(
        # NOT "how_paid". `Problem.id` for a check-derived problem is the check
        # name, and `decide_problems` and `next_question` both filter answered
        # problems by that id. A question carrying a different string would be
        # answered, never matched, and asked again forever.
        problem_id="funding_is_named",
        text="How did you pay?",
        answers=tuple(opts),
    )


def tax_bigger_than_total(amount_paise: int, gst_paise: int) -> Question:
    return Question(
        problem_id="gst_too_big",
        text=(
            f"You've put {rupees(gst_paise)} of tax on a "
            f"{rupees(amount_paise)} payment. Tax can't be more than the whole "
            f"amount. Shall we start again?"
        ),
        answers=(Answer(label="Let me type it again", value=RETYPE),),
    )

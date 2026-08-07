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
    return [a for a in accounts if a not in PLAIN]


def purpose_answers(accounts: Sequence[str]) -> tuple[Answer, ...]:
    """Turn a chart of accounts into plain-English choices.

    Accounts we have no words for are left out rather than shown as jargon.
    A "something else" escape is always offered so the person is never stuck.
    """
    out = [
        Answer(label=PLAIN[a], value=a)
        for a in accounts
        if a in PLAIN and a not in ("Cash", "Bank")
    ]
    out.append(Answer(label="something else", value=HANDOVER))
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
    """Vendor used more than one account before. Offer only those, in plain words."""
    who = party.strip() or "them"
    opts = [Answer(label=PLAIN[a], value=a) for a in seen if a in PLAIN]
    if not opts:
        return which_purpose(accounts, party)
    opts.append(Answer(label="something else", value=HANDOVER))
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
    opts = [Answer(label=PLAIN[a], value=a) for a in ("Cash", "Bank") if a in accounts]
    if not opts:
        opts = [Answer(label="cash", value="Cash")]
    return Question(
        problem_id="how_paid",
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

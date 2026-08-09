"""Child 2 — the memory index.

Built from the company's own posted history in Tally. Answers one question:
"when this vendor appeared before, which account did they use?"

No model is invoked here. It cannot hallucinate by construction.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Nothing here is evidence about TallyPrime. It does not prove that Tally folds a
ledger name the way we do, that a name survives Tally's own round trip, or that
two names Tally treats as one supplier reach one key here. It proves what OUR
key does with a string, and nothing else.

THE RULE THE VENDOR KEY FOLLOWS, AND WHY
----------------------------------------
The key answers "is this the same supplier as last time?". The two ways of
getting that wrong do NOT cost the same, and the whole rule falls out of that:

    two spellings that FAIL to collapse -> one question, answered in a second
    two suppliers that DO collapse      -> a voucher posted into the wrong
                                           ledger, silently, and nobody finds
                                           it until the year end

So the key collapses only what cannot change WHO WAS PAID, and keeps
everything else apart. When it cannot tell, the caller gets no match and the
person gets a question. It never picks.

COLLAPSED — presentation. None of these change who was paid.
    case              "SHARMA TRADERS" is "Sharma Traders" shouted.
    whitespace        Leading, trailing, repeated, tabs, newlines. Typing.
    punctuation       A trailing full stop, a comma, brackets, a hyphen.
    the M/s prefix    "M/s", "Messrs", "Ms." address an invoice; they are not
                      part of the supplier's name.
    the Unicode form  NFC and NFD of ONE VISIBLE NAME are one name. Which of
                      the two arrives depends on the keyboard, the scanner and
                      the operating system, and a person cannot see the
                      difference. See the NFC note below.

KEPT — meaning. Any of these may be a different legal person.
    the legal form    LLP, Inc, Corp, Corporation. An LLP and a limited company
                      are two taxpayers, two GSTINs, two sets of books.
                      `identity.py:16-21` already says exactly this for company
                      names; a supplier is no different. Cost of keeping them
                      apart when they were the same firm: one question. Cost of
                      folding them when they were not: someone else's ledger.
    the letters       A Cyrillic A (U+0410) renders identically to a Latin A
                      and is not a Latin A. An accented name is not its
                      unaccented spelling. One character shorter is another
                      firm. `\\w` is Unicode aware, so these survive as
                      themselves; that is deliberate, not incidental.

    Two spellings of ONE kept form — "Acme Corp" and "Acme Corporation" — do
    get two keys. That is the safe direction and costs a question.

WHY NFC IS THE FIRST THING THAT HAPPENS
---------------------------------------
`_PUNCT` turns everything that is neither `\\w` nor `\\s` into a space, and a
combining mark (U+0301 COMBINING ACUTE, category Mn) is neither. Without the
fold, decomposed "Café Supplies" lost its accent to a space and keyed as
`cafe_supplies` — the key of a DIFFERENT, unaccented supplier — while the
precomposed spelling of the same visible name keyed as `café_supplies`. One
name on the screen, two decisions, chosen by a byte nobody can see. Folding to
NFC first makes the two spellings one key, and that key is still not the
unaccented supplier's. Stdlib, no new dependency.

`normalise_phrase` folds too, for the same reason and at the same cost.

THE ONE COLLAPSE THAT IS A TRADE-OFF AND NOT A RULE
---------------------------------------------------
The Ltd/Limited family and the "& Co"/"Company" family are still stripped, so
"Sharma Traders", "M/s Sharma Traders Pvt Ltd" and "Sharma Traders & Co" are
one key. In law those are different persons. This is a deliberate bet that one
supplier written three ways is commoner in a small Indian book than a sole
proprietor and a private limited company of the same name both selling to the
same customer. It is pinned by `tests/test_memory.py:994`, so changing it is an
owner decision, not a code decision.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable

from accountant.schema import MatchResult, MatchStatus, Voucher
from accountant.tallyio.client import marker_for, operation_id_in

# Name noise, and ONLY name noise. A legal form is not noise: see the module
# docstring. "llp", "inc", "corporation" and "corp" were once in this tuple,
# and while they were, an LLP invoice posted to the limited company's account.
_SUFFIXES = (
    "private limited",
    "pvt limited",
    "pvt ltd",
    "private ltd",
    "limited",
    "ltd",
    "company",
    "and co",
    "& co",
)
_PREFIXES = ("m/s", "ms.", "messrs")
_PUNCT = re.compile(r"[^\w\s&]")
_SPACE = re.compile(r"\s+")
_DIGITS = re.compile(r"\d+")


def normalise_vendor(name: str) -> str:
    """Collapse spelling variants of one vendor to a single key.

    Presentation collapses, meaning does not. The module docstring states the
    rule line by line and gives the reason for each line.

    Deterministic. Same input always gives the same key.
    """
    # NFC first, before `_PUNCT` can turn a combining mark into a space and
    # hand one visible name two keys. See the module docstring.
    s = unicodedata.normalize("NFC", name).casefold().strip()
    for p in _PREFIXES:
        if s.startswith(p):
            s = s[len(p) :]
    s = _PUNCT.sub(" ", s)
    s = _SPACE.sub(" ", s).strip()
    changed = True
    while changed:
        changed = False
        for suf in _SUFFIXES:
            if s.endswith(" " + suf):
                s = s[: -(len(suf) + 1)].strip()
                changed = True
    return _SPACE.sub("_", s)


def normalise_phrase(narration: str) -> str:
    """Collapse a narration to one exact-match phrase key.

    Our own marker goes first, then every number: an invoice number is not a
    phrase, and leaving it in would make every narration unique and the phrase
    history worthless.

    Exact match only. Anything cleverer — stemming, similarity, a threshold —
    would be a guess, and nothing in this package guesses.
    """
    s = unicodedata.normalize("NFC", narration)
    op = operation_id_in(s)
    if op is not None:
        s = s.replace(marker_for(op), " ")
    s = _PUNCT.sub(" ", _DIGITS.sub(" ", s.casefold()))
    return _SPACE.sub("_", s.strip())


class MemoryIndex:
    """vendor key -> the set of accounts that vendor has been posted to."""

    def __init__(self) -> None:
        self._by_vendor: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )

    @classmethod
    def from_vouchers(
        cls, vouchers: Iterable[Voucher], *, skip_our_own: bool = True
    ) -> MemoryIndex:
        """Build from posted history.

        `skip_our_own` excludes vouchers we wrote, so the index learns from the
        accountant's decisions rather than from its own past guesses.
        """
        idx = cls()
        for v in vouchers:
            if skip_our_own and operation_id_in(v.narration):
                continue
            idx.record(v.party, v.debit_account)
        return idx

    def record(self, vendor: str, account: str) -> None:
        self._by_vendor[normalise_vendor(vendor)][account] += 1

    def lookup(self, vendor: str) -> MatchResult:
        key = normalise_vendor(vendor)
        accounts = self._by_vendor.get(key)

        if not accounts:
            return MatchResult(status=MatchStatus.NO_MATCH, vendor_key=key)

        if len(accounts) == 1:
            only = next(iter(accounts))
            return MatchResult(
                status=MatchStatus.MATCH, vendor_key=key, accounts=(only,)
            )

        ordered = tuple(
            a for a, _ in sorted(accounts.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        return MatchResult(
            status=MatchStatus.CONFLICTED, vendor_key=key, accounts=ordered
        )

    def times_posted(self, vendor: str, account: str) -> int:
        return self._by_vendor.get(normalise_vendor(vendor), {}).get(account, 0)

    def accounts_ever_used(self) -> frozenset[str]:
        return frozenset(a for accts in self._by_vendor.values() for a in accts)

    def vendors(self) -> frozenset[str]:
        return frozenset(self._by_vendor)

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
one key. In law those are different persons.

THE OWNER HAS NOW DECIDED AGAINST THAT BET. D-05, 2026-08-10
------------------------------------------------------------
    Treat legal forms as meaningful by default. Do not silently merge Ltd,
    Pvt Ltd, LLP, Inc, Corp, or & Co. If identity is ambiguous, ask or hand
    over.

    Separate technical Unicode/whitespace normalisation from business
    identity. Do not destroy legal-form information during normalisation.

`normalise_vendor` STILL STRIPS THEM, and that is not the owner being ignored.
Three assertions in two files owned elsewhere require exactly this merge, and
one of them requires it at the LOOKUP level rather than on the key:

    tests/test_memory.py:1001-1006  "M/s Sharma Traders Pvt Ltd", "Messrs
        Sharma Traders Private Limited" and "Ms. Sharma Traders & Co" must all
        key as `sharma_traders`
    tests/test_memory.py:646-653    company B's "Sharma Traders" and "M/s
        Sharma Traders Pvt Ltd" must be ONE vendor with two postings
    tests/test_adversarial_identity.py:772-775  the same merge, already
        reported there as blocked on an owner decision

So the decision is served by a SECOND LAYER instead, which answers a different
question and deletes no word: `identity.compare_suppliers`. The key still says
which bucket a name falls in; the comparison says whether two names in that
bucket are one supplier. `lookup` keeps the name it was recorded under and
refuses to answer with an account belonging to a DIFFERENT legal person, so
"Bharat Steel Pvt Ltd" and "Bharat Steel Ltd" share a bucket and not an answer.

WHY THE REFUSAL STOPS AT DIFFERENT AND DOES NOT COVER AMBIGUOUS
----------------------------------------------------------------
AMBIGUOUS is "one side states a legal form and the other does not". Refusing on
it would be closer to what the owner wrote, and it is not available here:
`accountant/memory/company.py:300` builds the live index out of
`Observation.subject`, which is the already-stripped key, and the store keeps
no raw name. Every live row therefore states NO form, so every live comparison
would be AMBIGUOUS and the index would refuse every lookup it has ever
answered.

The consequence is worth stating plainly rather than burying: this filter is
real wherever the raw name reaches the index - `from_vouchers`, and every
caller that passes `Voucher.party` - and a no-op on the path that runs in
production. Closing that needs `company.py` and the store to carry the name
Tally gave, and both are owned elsewhere.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable

from accountant.memory.identity import SupplierVerdict, compare_suppliers
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
    """vendor key -> the accounts that vendor has been posted to.

    Rows are held under the NAME THEY WERE RECORDED WITH, not only under the
    key, because the key has had the legal form deleted out of it and D-05 says
    the legal form decides who was paid. Two legal persons can share a key;
    they never share an answer. See the module docstring.
    """

    def __init__(self) -> None:
        # (name as recorded, account) -> times. Keyed by the raw name so the
        # legal form is still there to compare at lookup.
        self._by_vendor: dict[str, dict[tuple[str, str], int]] = defaultdict(
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
        self._by_vendor[normalise_vendor(vendor)][(vendor, account)] += 1

    def _accounts_for(self, vendor: str) -> dict[str, int]:
        """This vendor's own accounts, with the other legal persons' left out.

        Only DIFFERENT is dropped. AMBIGUOUS still answers, because on the live
        path every recorded name is a stripped key that states no legal form,
        and refusing on AMBIGUOUS would refuse everything. The module docstring
        says why that is a reported gap and not a design.
        """
        totals: dict[str, int] = {}
        for (recorded, account), times in self._by_vendor.get(
            normalise_vendor(vendor), {}
        ).items():
            if compare_suppliers(recorded, vendor) is SupplierVerdict.DIFFERENT:
                continue
            totals[account] = totals.get(account, 0) + times
        return totals

    def lookup(self, vendor: str) -> MatchResult:
        key = normalise_vendor(vendor)
        accounts = self._accounts_for(vendor)

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
        return self._accounts_for(vendor).get(account, 0)

    def accounts_ever_used(self) -> frozenset[str]:
        """Every account in the book. Not scoped to one vendor, so not filtered."""
        return frozenset(a for rows in self._by_vendor.values() for _, a in rows)

    def vendors(self) -> frozenset[str]:
        return frozenset(self._by_vendor)

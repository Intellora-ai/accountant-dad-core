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

THE INVARIANT, AND THE DESIGN THAT FOLLOWS FROM IT
--------------------------------------------------
    the system may remove spelling noise, but it must never destroy the
    evidence needed to distinguish legal or business forms before identity
    resolution.

    the normalised key finds CANDIDATES; the raw name decides IDENTITY.

Those are two jobs and this module now does them in two places, which is the
correction the owner made on 2026-08-10 (D-05). Before it, one function did
both, and because a key has to be lossy to be useful, identity inherited the
loss: "Acme Ltd", "Acme Private Limited", "Acme & Co" and a bare "Acme" were
one key and therefore one supplier, silently.

`normalise_vendor` — THE CANDIDATE KEY. Deliberately lossy.
    Folds case, whitespace, punctuation and Unicode form, drops the naming
    prefix, and drops the legal form. Dropping the form WIDENS the shortlist
    on purpose, so "Sharma Traders" and "Sharma Traders Pvt Ltd" are at least
    considered together. A shortlist is not a decision, so widening it is
    safe; what would not be safe is letting the shortlist BE the decision.

`compare_recorded_supplier` — THE DECISION. Never lossy.
    Reads the RAW name the source gave and decides SAME, DIFFERENT or
    AMBIGUOUS. Only SAME contributes an account. DIFFERENT and AMBIGUOUS both
    contribute nothing, so the caller gets no match and the person gets a
    question — ambiguity asks, it never posts and it never merges a history.

COLLAPSED by the key — presentation. None of these change who was paid.
    case              "SHARMA TRADERS" is "Sharma Traders" shouted.
    whitespace        Leading, trailing, repeated, tabs, newlines. Typing.
    punctuation       A trailing full stop, a comma, brackets, a hyphen.
    the M/s prefix    "M/s", "Messrs", "Ms." address an invoice; they are not
                      part of the supplier's name. Matched on the FOLDED words
                      now, so "M.S." reaches the same key as "M/s" — the old
                      literal-string match did not, and that was a defect.
    the Unicode form  NFC and NFD of ONE VISIBLE NAME are one name. Which of
                      the two arrives depends on the keyboard, the scanner and
                      the operating system, and a person cannot see the
                      difference. See the NFC note below.
    the legal form    Dropped from the KEY ONLY, to widen the shortlist. It is
                      never dropped from the decision, and the raw name it is
                      read from is stored beside the key rather than being
                      reconstructed from it.

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

WHO FEEDS THIS, AND THE GAP THAT USED TO BE HERE
-------------------------------------------------
`MemoryIndex.record_observed` takes the store's two fields and keeps them
apart, with `raw_subject=None` meaning INCOMPLETE — no evidence, therefore
never a confident SAME. That is the correct shape, and until 2026-08-10 it was
fed wrongly by four writers, none of them owned here:

    accountant/memory/bootstrap.py:_derive       discarded `Voucher.party`
    accountant/memory/company.py:observe         dropped it for everything
                                                 learned after bootstrap
    accountant/memory/company.py:record_correction
                                                 dropped a person's explicit
                                                 answer, storing it INCOMPLETE
    accountant/memory/company.py:index           called `record(o.subject, …)`,
                                                 presenting a stripped key as
                                                 though it were a raw name

All four are now handed the raw name and keep it. The end-to-end proof - Tally
to store to file to reload to decision - is `tests/test_legal_identity_live.py`,
which counts the records rather than sampling them, because the four failed in
four different places and any one of them passing proved nothing about the rest.

`record` remains the RAW-name door and `record_observed` the two-field one.
Anything reading out of the store must use the second: a stored subject handed
to the first is the defect above, and an `ast` guard in that file fails the
suite if it comes back.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable

from accountant.memory.identity import (
    SupplierVerdict,
    compare_recorded_supplier,
    split_supplier_name,
)
from accountant.schema import MatchResult, MatchStatus, Voucher
from accountant.tallyio.client import marker_for, operation_id_in

_PUNCT = re.compile(r"[^\w\s&]")
_SPACE = re.compile(r"\s+")
_DIGITS = re.compile(r"\d+")


def normalise_vendor(name: str) -> str:
    """The CANDIDATE key: which shortlist does this name fall in?

    NOT an identity claim, and since 2026-08-10 it is not allowed to be read as
    one. It drops the naming prefix, folds spelling noise, and CANONICALISES
    the legal form rather than deleting it: "Acme Ltd" and "Acme Limited" are
    `acme_ltd`, "Acme Corp" and "Acme Corporation" are `acme_corp`, and
    "Sharma Traders & Co", "Ms. Sharma Traders and Co" and "Sharma Traders
    Company" are all `sharma_traders_and_co`.

    Deterministic. Same input always gives the same key.

    WHY THE FORM IS STILL IN THE KEY, WHEN THE RULING SAYS THE KEY ONLY HAS TO
    FIND CANDIDATES

    Because `CompanyMemory.lookup` goes to the store, not through
    `_accounts_for`, and applies no identity check at all. Delete the form from
    the key today and "Acme LLP" and "Acme Ltd" become one subject on that
    path, with nothing downstream to tell them apart — which is D2, the defect
    fixed on 2026-08-09, reopened. Feeding the index correctly (D-05,
    2026-08-10) fixed the INDEX path; it did not put a decision layer on the
    store path, so the reason the key stays narrow is unchanged.

    So the key is kept identity-preserving and the shortlist is narrow. The
    cost is that a genuinely AMBIGUOUS pair — a bare name against a Pvt Ltd —
    never shares a bucket, so the AMBIGUOUS verdict is usually not computed:
    the two keys simply miss each other. The OUTCOME is the one the owner
    required either way — no match, no merge, no automatic post, a question —
    and where the pair DOES share a bucket, which is every legacy INCOMPLETE
    row, the verdict is computed and answers AMBIGUOUS.

    It shares `split_supplier_name` with the identity layer rather than
    reimplementing the split, so the shortlist and the decision cannot drift
    apart. That also fixes the old prefix defect: the previous version matched
    the literal string "m/s", so "M.S. Sharma Traders" missed the prefix list
    and keyed away from "M/s Sharma Traders". Both now fold to the words
    ("m", "s") before anything looks at them.
    """
    stem, form = split_supplier_name(name)
    return "_".join(p for p in (_SPACE.sub("_", stem), form) if p)


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
        self._by_vendor: dict[str, dict[tuple[str | None, str], int]] = defaultdict(
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
        """Record a RAW observed vendor name. Identity evidence is complete."""
        self.record_observed(
            normalised_subject=normalise_vendor(vendor),
            raw_subject=vendor,
            account=account,
        )

    def record_observed(
        self, *, normalised_subject: str, raw_subject: str | None, account: str
    ) -> None:
        """The store's two fields, straight in, never collapsed into one.

        `raw_subject=None` is INCOMPLETE: a row that predates raw-name
        persistence. It is recorded as such rather than being reconstructed
        from `normalised_subject`, because the legal form was stripped out of
        that key and inventing it back is the failure this whole ruling exists
        to stop.
        """
        self._by_vendor[normalised_subject][(raw_subject, account)] += 1

    def _accounts_for(self, vendor: str) -> dict[str, int]:
        """This vendor's own accounts. Only a CONFIDENT match contributes.

        The candidate key gathered the shortlist; this is where the shortlist
        is judged. Anything that is not SAME - DIFFERENT, or AMBIGUOUS, or an
        INCOMPLETE row with no evidence either way - contributes nothing, so
        the caller gets no match and the person gets a question. Ambiguity
        asks; it never posts and it never merges a history.
        """
        totals: dict[str, int] = {}
        for (recorded, account), times in self._by_vendor.get(
            normalise_vendor(vendor), {}
        ).items():
            if compare_recorded_supplier(recorded, vendor) is not SupplierVerdict.SAME:
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

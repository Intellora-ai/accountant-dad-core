"""Company identity — the scope every memory record is keyed by.

THE MEASURED FACT THIS PACKAGE IS BUILT ON
------------------------------------------
`accountant/ingest/crossorg.py`, real UK central-government spend, 16,011 rows,
30 ordered department pairs:

    within the same department   53.08% at best
    across departments            0.00% on 29 of the 30 pairs

Vendor -> account mappings do not transfer between organisations. Every
customer is a permanent cold start, a pooled model is wasted effort, and a
pooled *key* is a correctness bug. Identity is therefore not decoration: it is
the thing that stops company A's history answering a question about company B.

WHY THIS NORMALISATION IS DELIBERATELY CONSERVATIVE
---------------------------------------------------
"Acme Ltd" and "Acme LLP" are two companies, two sets of books and possibly two
customers. Collapsing them would merge two ledgers, so `normalise_company`
removes punctuation and nothing else. No word is ever deleted.

D-05, DECIDED BY THE OWNER 2026-08-10: SUPPLIERS FOLLOW THE SAME RULE
---------------------------------------------------------------------
    Treat legal forms as meaningful by default. Do not silently merge Ltd,
    Pvt Ltd, LLP, Inc, Corp, or & Co. If identity is ambiguous, ask or hand
    over.

    Separate technical Unicode/whitespace normalisation from business
    identity. Do not destroy legal-form information during normalisation.

`normalise_vendor` in `index.py` is the opposite of that: it deletes whole
words, the Ltd/Limited and "& Co"/Company families among them. It cannot stop
- three assertions in two files owned elsewhere require the merge, one of them
at the lookup level - so D-05 is served here instead, by a second layer that
answers a different question:

    normalise_company / normalise_vendor   what KEY does this name get?
    compare_suppliers                      are these two names one supplier?

The second never deletes a word. `normalise_text` folds Unicode form, case,
punctuation and spacing and stops there, so the legal form is still standing
when `legal_form` reads it. That ordering is the whole of the owner's second
sentence: "Bharat Steel Pvt. Ltd." and "Bharat Steel Pvt Ltd" only agree once
punctuation has folded, and "Bharat Steel Pvt Ltd" and "Bharat Steel Ltd" only
disagree if the fold left the form behind.

THREE ANSWERS, NOT TWO
----------------------
"Acme & Co" and "Acme" are not provably different and not provably the same: a
partnership written short and a sole proprietor of that name produce the same
string. The owner named that case and said what to do with it, so
`compare_suppliers` has an AMBIGUOUS answer and `same_supplier` treats it as
"no". Ambiguity asks; it never posts.

WHAT THIS MODULE DOES NOT PROVE
-------------------------------
That the LIVE pipeline honours D-05 for the Ltd/&Co families. It does not.
`accountant/memory/company.py:300` builds the index from `Observation.subject`
- the already-stripped key - and the store keeps no raw name, so by the time a
live lookup happens the legal form is gone and every comparison it could make
is AMBIGUOUS rather than DIFFERENT. `compare_suppliers` bites wherever the raw
name survives and is a no-op where it does not.

Nothing here is evidence about TallyPrime. It does not prove that Tally folds a
ledger name this way, or that a legal form survives Tally's own round trip.

WHY NFC IS THE FIRST THING THAT HAPPENS
---------------------------------------
D1's other half, fixed 2026-08-09. `_PUNCT` turns everything that is neither
`\\w` nor `\\s` into a space, and a combining mark (U+0301 COMBINING ACUTE,
category Mn) is neither. With no fold in front of it, decomposed "Café
Supplies" lost its accent and keyed as `cafe_supplies` — the key of a
DIFFERENT company, "Cafe Supplies" — while the precomposed spelling of the same
visible name keyed as `café_supplies`.

That is the exact failure this module exists to prevent, and it is worse here
than it was for vendors. `normalise_vendor` was fixed the same day; this was
left as a reported open item. A vendor collision costs one voucher in the wrong
ledger. A company collision costs an index: `company_key` is the first column
of every primary key in the store, `save_bootstrap` deletes the colliding
company's rows before writing, and both cross-company guards in `pipeline.py`
compare these same keys, so neither of them can fire when two companies share
one.

Folding to NFC is NOT a collapse of two names into one, so it does not violate
the conservatism above: it makes ONE VISIBLE NAME one key whichever encoding
the keyboard, the scanner or the operating system produced, and that key is
still not the unaccented company's. No word is removed. Stdlib, no new
dependency, and the same two lines `accountant/memory/index.py` already uses.

WHAT IDENTITY WE ACTUALLY HAVE
------------------------------
`TallyClient.list_companies()` returns names and nothing else, so the name is
the identity available today. It is normalised once, here, and the result is
carried by every stored row and every lookup. When the connector grows a
company GUID, this module is the only place that changes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

_PUNCT = re.compile(r"[^\w\s]")
_SPACE = re.compile(r"\s+")

# Punctuation OR an underscore. `_PUNCT` leaves `_` alone because `_` is `\w`,
# which is right for a key - `normalise_company` JOINS on it - and wrong for a
# comparison, where an arriving `acme_llp` is a key that has already been built
# and must still compare equal to the "Acme LLP" it was built from.
_SEPARATOR = re.compile(r"[^\w\s]|_")

#: Legal form -> the canonical name of the legal person it denotes. Keyed by
#: the trailing WORDS of the folded name, longest first, because a legal form
#: sits at the end and "private limited" must be read before "limited".
#:
#: Only the six families the owner named in D-05, plus the spellings a firm
#: uses for its own form on its own invoices. PLC, LLC and the rest are
#: deliberately absent: inventing a form the owner did not name would start
#: refusing matches on a policy nobody decided.
_FORM_BY_TRAILING_WORDS: dict[tuple[str, ...], str] = {
    ("private", "limited"): "pvt_ltd",
    ("private", "ltd"): "pvt_ltd",
    ("pvt", "limited"): "pvt_ltd",
    ("pvt", "ltd"): "pvt_ltd",
    ("and", "co"): "and_co",
    ("and", "company"): "and_co",
    ("ltd",): "ltd",
    ("limited",): "ltd",
    ("llp",): "llp",
    ("inc",): "inc",
    ("incorporated",): "inc",
    ("corp",): "corp",
    ("corporation",): "corp",
    ("co",): "and_co",
    ("company",): "and_co",
}

_LONGEST_FORM = max(len(words) for words in _FORM_BY_TRAILING_WORDS)


class SupplierVerdict(StrEnum):
    """Are two written names one supplier? D-05 allows three answers.

    SAME       differ only in Unicode form, case, punctuation or spacing.
    DIFFERENT  the name differs, or both state a legal form and the forms
               differ. Two legal persons.
    AMBIGUOUS  one states a legal form and the other does not, or one of them
               is not a name at all. Cannot be told apart, so it is a question
               or a handover - never a silent post.
    """

    SAME = "same"
    DIFFERENT = "different"
    AMBIGUOUS = "ambiguous"


def normalise_text(name: str) -> str:
    """Fold everything a person cannot see, and delete no word.

    This is the TECHNICAL half of D-05 and the whole of it: Unicode normal
    form, case, punctuation, underscores and runs of whitespace. Every word
    that went in comes out, in order, separated by single spaces.

    Deterministic and idempotent: folding a folded name does not move it.

    NFC first, before `_SEPARATOR` can turn a combining mark into a space and
    hand one visible name two answers. Same reason as `normalise_company`.
    """
    folded = unicodedata.normalize("NFC", name).casefold()
    return _SPACE.sub(" ", _SEPARATOR.sub(" ", folded)).strip()


def _split_legal_form(name: str) -> tuple[str, str]:
    """The name without its trailing legal form, and that form's canonical name.

    Longest match wins, so "Acme Private Limited" is a pvt_ltd and not a ltd
    called "Acme Private". Only the trailing form is read: "Smith & Co Ltd" is
    a limited company, which is what its last word says.
    """
    words = normalise_text(name).split()
    for width in range(min(_LONGEST_FORM, len(words)), 0, -1):
        form = _FORM_BY_TRAILING_WORDS.get(tuple(words[-width:]))
        if form is not None:
            return " ".join(words[:-width]), form
    return " ".join(words), ""


def legal_form(name: str) -> str:
    """The canonical legal form this name states, or "" if it states none.

    "" means UNSTATED, never "sole proprietor". The difference is the whole of
    the AMBIGUOUS verdict.
    """
    return _split_legal_form(name)[1]


def compare_suppliers(a: str, b: str) -> SupplierVerdict:
    """Are these two written names one supplier? D-05, the rule in one place.

    A difference in legal form is meaningful. A difference in punctuation,
    spacing, case or Unicode form is not. When only one side states a form,
    the honest answer is neither yes nor no.

    A name that is nothing but a legal form - "Ltd" - is not a supplier name,
    and two of them are not one supplier. Without that guard the empty stem
    matches the empty stem and every junk row in the book answers for every
    other one.
    """
    stem_a, form_a = _split_legal_form(a)
    stem_b, form_b = _split_legal_form(b)

    if not stem_a or not stem_b:
        return SupplierVerdict.AMBIGUOUS
    if stem_a != stem_b:
        return SupplierVerdict.DIFFERENT
    if form_a == form_b:
        return SupplierVerdict.SAME
    if form_a and form_b:
        return SupplierVerdict.DIFFERENT
    return SupplierVerdict.AMBIGUOUS


def same_supplier(a: str, b: str) -> bool:
    """True only when we are SURE. Ambiguity is not a match, by construction."""
    return compare_suppliers(a, b) is SupplierVerdict.SAME


def normalise_company(name: str) -> str:
    """Collapse one company name to a single scope key.

    Deterministic. Same input always gives the same key. Unicode normal form,
    punctuation and case only — never a word, because a removed word can merge
    two companies.
    """
    # NFC first, before `_PUNCT` can turn a combining mark into a space and
    # hand one visible company the key of a different one. See the docstring.
    folded = unicodedata.normalize("NFC", name)
    return _SPACE.sub("_", _PUNCT.sub(" ", folded.casefold()).strip())


def same_company_name(a: str, b: str) -> bool:
    """Are these two strings the SAME NAME, written two ways?

    Defect D-C, found 2026-08-10. Every check of the form "is this company open
    in Tally" was an exact string comparison, and the two sides of that
    comparison come from two operating systems: a name typed on macOS arrives
    decomposed (NFD - `e` followed by a combining acute), and TallyPrime on
    Windows returns it composed (NFC - a single `é`). The two strings look
    identical on screen and are not equal.

    The operator saw:

        'Café Exports' is no longer open in Tally. 1 company/companies are
        open: ['Café Exports']. Open 'Café Exports' in Tally again

    - two identical-looking names and an instruction that cannot be followed.
    It failed CLOSED, so no wrong company was ever written to, and it was
    still unusable, which is the distinction this codebase keeps having to
    relearn: failing safely and failing legibly are two properties.

    NFC ONLY, deliberately. This is NOT `normalise_company`, which also folds
    case and strips punctuation to build a scope key. Two genuinely different
    companies can share a key - the identity layer treats that as an ambiguity
    and refuses - so using the key here would let "is my company open?" answer
    yes about somebody else's books. Unicode normalisation is the whole of the
    defect and the whole of the fix.
    """
    return unicodedata.normalize("NFC", a) == unicodedata.normalize("NFC", b)


@dataclass(frozen=True)
class CompanyIdentity:
    """One company, unambiguously.

    `key` scopes every record and every lookup. It is checked against `name`
    on construction, so an identity cannot be forged by handing in a key that
    belongs to somebody else's books.
    """

    name: str
    key: str

    def __post_init__(self) -> None:
        expected = normalise_company(self.name)
        if not expected:
            raise ValueError(f"company name {self.name!r} carries no identity")
        if self.key != expected:
            raise ValueError(
                f"company key {self.key!r} is not the identity of {self.name!r}"
            )

    @classmethod
    def from_name(cls, name: str) -> CompanyIdentity:
        """The only sane way to build one. Tally names the company; we key it."""
        return cls(name=name, key=normalise_company(name))

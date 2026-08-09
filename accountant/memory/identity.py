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
`normalise_vendor` strips "Pvt Ltd", "M/s" and friends, because two spellings
of one supplier are one supplier. The opposite is true here. "Acme Ltd" and
"Acme LLP" are two companies, two sets of books and possibly two customers.
Collapsing them would merge two ledgers, so this function removes punctuation
and nothing else.

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

_PUNCT = re.compile(r"[^\w\s]")
_SPACE = re.compile(r"\s+")


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

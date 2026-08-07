"""Child 2 — the memory index.

Built from the company's own posted history in Tally. Answers one question:
"when this vendor appeared before, which account did they use?"

No model is invoked here. It cannot hallucinate by construction.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from accountant.schema import MatchResult, MatchStatus, Voucher
from accountant.tallyio.client import operation_id_in

# Common Indian company-name noise. Stripped so "Sharma Traders Pvt Ltd",
# "M/s Sharma Traders" and "SHARMA TRADERS." collapse to one key.
_SUFFIXES = (
    "private limited",
    "pvt limited",
    "pvt ltd",
    "private ltd",
    "limited",
    "ltd",
    "llp",
    "inc",
    "corporation",
    "corp",
    "company",
    "and co",
    "& co",
)
_PREFIXES = ("m/s", "ms.", "messrs")
_PUNCT = re.compile(r"[^\w\s&]")
_SPACE = re.compile(r"\s+")


def normalise_vendor(name: str) -> str:
    """Collapse spelling variants of one vendor to a single key.

    Deterministic. Same input always gives the same key.
    """
    s = name.casefold().strip()
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

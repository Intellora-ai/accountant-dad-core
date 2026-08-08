"""One company's memory, and the only four answers a lookup can give.

    MATCH             one consistent company-local mapping   -> may propose
    CONFLICTED        this company has posted this vendor to more than one
                      account                                 -> ask, never pick
    NO_MATCH          this company has never posted this vendor -> ask
    MEMORY_NOT_READY  no successful bootstrap for this company  -> do not
                      propose, do not post, and do NOT call it NO_MATCH

THE FOURTH ONE IS THE WHOLE POINT
---------------------------------
NO_MATCH is a fact about the customer's books: they have never used this
supplier. MEMORY_NOT_READY is a fact about us: we have not read their books
yet. Collapsing the two is exactly how a tool with thirty years of history in
front of it behaves like a fresh install — it asks a question the ledger
already answered, or worse, proposes from nothing.

So the two are separate values, `as_match_result()` RAISES rather than
converting MEMORY_NOT_READY into the shared `MatchStatus`, and
`propose_account` raises rather than returning None.

NO GLOBAL ANYTHING
------------------
There is no pooled prior, no cross-customer default and no fallback account in
this module. Every answer comes from rows carrying this company's key, and a
company with no answer produces a question. That is not caution — 29 of 30
measured department pairs transferred 0.00%, so a pooled answer would be a
wrong answer with a confident face on it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum

from accountant.memory.identity import CompanyIdentity
from accountant.memory.index import MemoryIndex, normalise_phrase, normalise_vendor
from accountant.memory.store import (
    BootstrapReport,
    BootstrapStatus,
    MemoryStore,
    Observation,
)
from accountant.schema import MatchResult, MatchStatus, Voucher
from accountant.tallyio.client import operation_id_in

FROM_TALLY_HISTORY = "tally_history"
FROM_OUR_POSTING = "accountant_dad_posted"
FROM_HUMAN_ANSWER = "human_answer"


class MemoryNotReady(RuntimeError):
    """Raised when something asks memory to act before a successful bootstrap.

    An exception rather than a return value, because every caller that could
    quietly ignore a return value is a caller that could quietly post.
    """


class CompanyMatchStatus(StrEnum):
    """Exactly one of these comes back from every lookup."""

    MATCH = "match"
    CONFLICTED = "conflicted"
    NO_MATCH = "no_match"
    MEMORY_NOT_READY = "memory_not_ready"


@dataclass(frozen=True)
class CompanyMatch:
    """A lookup answer that carries the company it came from.

    `company_key` is on the result, not just on the query, so a match can be
    checked against the company it is about rather than assumed to belong to
    whoever asked.
    """

    status: CompanyMatchStatus
    company_key: str
    subject: str
    accounts: tuple[str, ...] = ()
    times: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        rules: dict[CompanyMatchStatus, Callable[[int], bool]] = {
            CompanyMatchStatus.MATCH: lambda n: n == 1,
            CompanyMatchStatus.CONFLICTED: lambda n: n >= 2,
            CompanyMatchStatus.NO_MATCH: lambda n: n == 0,
            CompanyMatchStatus.MEMORY_NOT_READY: lambda n: n == 0,
        }
        n = len(self.accounts)
        if not rules[self.status](n):
            raise ValueError(f"{self.status.value} is inconsistent with {n} account(s)")
        if len(self.times) != n:
            raise ValueError(f"{n} account(s) but {len(self.times)} observed count(s)")

    @property
    def may_propose(self) -> bool:
        """True for one consistent company-local mapping, and nothing else."""
        return self.status is CompanyMatchStatus.MATCH

    def as_match_result(self) -> MatchResult:
        """Convert to the shared `MatchResult` the rest of the system speaks.

        MEMORY_NOT_READY has no shared equivalent and is not given one. Turning
        it into NO_MATCH would tell the decision path "this company has never
        used this supplier" when the truth is "we have not looked yet".
        """
        if self.status is CompanyMatchStatus.MEMORY_NOT_READY:
            raise MemoryNotReady(
                f"memory for company {self.company_key!r} is not ready; "
                f"MEMORY_NOT_READY is not NO_MATCH and will not be converted"
            )
        return MatchResult(
            status=MatchStatus(self.status.value),
            vendor_key=self.subject,
            accounts=self.accounts,
        )


def _resolve(
    company_key: str, subject: str, seen: tuple[Observation, ...]
) -> CompanyMatch:
    """Turn this company's own rows into one of the three history answers."""
    if not seen:
        return CompanyMatch(
            status=CompanyMatchStatus.NO_MATCH,
            company_key=company_key,
            subject=subject,
        )
    ordered = sorted(seen, key=lambda o: (-o.times, o.account))
    status = (
        CompanyMatchStatus.MATCH if len(ordered) == 1 else CompanyMatchStatus.CONFLICTED
    )
    return CompanyMatch(
        status=status,
        company_key=company_key,
        subject=subject,
        accounts=tuple(o.account for o in ordered),
        times=tuple(o.times for o in ordered),
    )


class CompanyMemory:
    """Everything we know about ONE company, and nothing about any other.

    Built by `accountant.memory.bootstrap`. Constructing one by hand with a
    report that is not READY is allowed and safe: every lookup answers
    MEMORY_NOT_READY and every write raises.
    """

    def __init__(self, report: BootstrapReport, store: MemoryStore) -> None:
        self._report = report
        self._store = store

    @property
    def identity(self) -> CompanyIdentity:
        return self._report.identity

    @property
    def report(self) -> BootstrapReport:
        return self._report

    @property
    def ready(self) -> bool:
        return self._report.ready

    def invalidate(self, reason: str) -> None:
        """Stop answering. Used when this company is no longer the open one.

        The last successful bootstrap time is kept, because when we last really
        read this company is a fact and stays true.
        """
        self._report = replace(
            self._report, status=BootstrapStatus.INCOMPLETE, detail=reason
        )

    def _not_ready(self, subject: str) -> CompanyMatch:
        return CompanyMatch(
            status=CompanyMatchStatus.MEMORY_NOT_READY,
            company_key=self.identity.key,
            subject=subject,
        )

    def lookup(self, vendor: str) -> CompanyMatch:
        """This company's own vendor history. Never anybody else's."""
        subject = normalise_vendor(vendor)
        if not self._report.ready:
            return self._not_ready(subject)
        return _resolve(
            self.identity.key,
            subject,
            self._store.vendor(self.identity.key, subject),
        )

    def lookup_phrase(self, narration: str) -> CompanyMatch:
        """This company's own narration history. Exact match on the key only."""
        subject = normalise_phrase(narration)
        if not self._report.ready:
            return self._not_ready(subject)
        return _resolve(
            self.identity.key,
            subject,
            self._store.phrase(self.identity.key, subject),
        )

    def _require_ready(self, doing: str) -> None:
        if not self._report.ready:
            raise MemoryNotReady(
                f"refusing to {doing} for {self.identity.name!r}: "
                f"{self._report.status.value} — {self._report.detail}"
            )

    def record_correction(
        self, vendor: str, account: str, *, source_voucher_id: str = ""
    ) -> CompanyMatch:
        """An accepted answer, recorded against THIS company and no other.

        A correction is evidence, not an override. It does not delete history,
        so a vendor with genuinely conflicting history stays conflicted and
        keeps asking — which is the rule, not an oversight.
        """
        self._require_ready("record a correction")
        subject = normalise_vendor(vendor)
        self._store.record_vendor(
            self.identity.key,
            subject,
            account,
            source_voucher_id=source_voucher_id,
            provenance=FROM_HUMAN_ANSWER,
        )
        return self.lookup(vendor)

    def observe(self, voucher: Voucher) -> None:
        """Record one posted voucher as future company-local context.

        Called with a voucher read BACK out of Tally, so what is learned is
        what the ledger actually holds rather than what we believe we sent.
        """
        self._require_ready("observe a voucher")
        key = self.identity.key
        provenance = (
            FROM_OUR_POSTING
            if operation_id_in(voucher.narration)
            else FROM_TALLY_HISTORY
        )
        self._store.record_vendor(
            key,
            normalise_vendor(voucher.party),
            voucher.debit_account,
            source_voucher_id=voucher.id,
            provenance=provenance,
        )
        phrase = normalise_phrase(voucher.narration)
        if phrase:
            self._store.record_phrase(
                key,
                phrase,
                voucher.debit_account,
                source_voucher_id=voucher.id,
                provenance=provenance,
            )

    def chart(self) -> tuple[str, ...]:
        """This company's chart of accounts, as read at bootstrap."""
        return self._store.chart(self.identity.key)

    def index(self) -> MemoryIndex:
        """A `MemoryIndex` over this company's rows alone.

        The existing index type is unscoped by construction, so it is only ever
        handed rows that already carry one company's key. That is the seam: the
        scope is applied before the index exists, not trusted afterwards.
        """
        idx = MemoryIndex()
        for o in self._store.vendors(self.identity.key):
            for _ in range(o.times):
                idx.record(o.subject, o.account)
        return idx


def propose_account(memory: CompanyMemory, vendor: str) -> str | None:
    """The ONLY automatic account proposal in this package.

    Returns the account for one consistent company-local mapping, None when
    this company's own history cannot answer, and RAISES when memory is not
    ready — because None means "ask the person" and not-ready means "do not
    even get that far".
    """
    match = memory.lookup(vendor)
    if match.status is CompanyMatchStatus.MEMORY_NOT_READY:
        raise MemoryNotReady(
            f"no successful bootstrap for company {match.company_key!r}; "
            f"nothing may be proposed"
        )
    if match.may_propose:
        return match.accounts[0]
    return None

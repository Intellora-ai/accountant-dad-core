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

MEMORY IS A SNAPSHOT, AND A SNAPSHOT GOES STALE
-----------------------------------------------
Everything above answers from rows written ONCE, by `bootstrap`, at the moment
the company was opened. Nothing re-reads them. So a company that has genuinely
changed how it books a supplier keeps getting the old answer for as long as the
session lasts, and `pipeline.build_draft` proposes it onto a leg that posts
without asking anybody.

`disagrees_with_live_history` is the check that closes that. It is in this
module rather than in the pipeline because the claim it tests is memory's own:
"this is still what your books say". D-06, owner, 2026-08-10: live Tally wins
over stale memory, and a disagreement is a QUESTION, never a silent override.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Nothing here is evidence about TallyPrime. `disagrees_with_live_history`
compares two derivations of OUR OWN vendor key over rows a client handed us; it
does not prove the rows are everything Tally holds, that a connector reporting
an empty history is telling the truth, or that Tally groups suppliers the way
our key does. Evidence class: FakeTally implementation. It says our logic is
right and says nothing about Tally.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from accountant.memory.identity import CompanyIdentity, normalise_text
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


def _raw_identity(observed: str) -> str | None:
    """The name the SOURCE gave, or None when it gave no name at all. D-05.

    Every write in this module goes through here, so there is one answer to
    "what counts as identity evidence" rather than three that can drift.

    Kept EXACTLY as it arrived. Trimming it, tidying it or canonicalising it
    here would be the same destruction this fix exists to undo, performed one
    layer later; `compare_recorded_supplier` folds presentation at the moment
    of comparison, which is the only place the fold is safe.

    None is INCOMPLETE and it is the honest answer for a party that is blank or
    is nothing but punctuation: there is no name in it, and storing "" as
    though it were one would claim COMPLETE evidence over nothing. That is the
    same false confidence as backfilling a legacy row from its key.
    """
    return observed if normalise_text(observed) else None


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
        if not self._report.askable:
            return self._not_ready(subject)
        return _resolve(
            self.identity.key,
            subject,
            self._store.vendor(self.identity.key, subject),
        )

    def lookup_phrase(self, narration: str) -> CompanyMatch:
        """This company's own narration history. Exact match on the key only."""
        subject = normalise_phrase(narration)
        if not self._report.askable:
            return self._not_ready(subject)
        return _resolve(
            self.identity.key,
            subject,
            self._store.phrase(self.identity.key, subject),
        )

    def require_usable(self, doing: str) -> None:
        """Public form of the same guard, for callers that must check FIRST.

        A5, 2026-08-09. `pipeline.run` read the chart and the voucher history
        out of Tally before anything looked at this, so a company that had
        never been read successfully AND a flaky connector produced the
        connector's error rather than MEMORY_NOT_READY. It failed closed
        either way - nothing was written - but the diagnosis was wrong, and a
        wrong diagnosis on this particular pair is expensive: one says "your
        network is down", the other says "we have not read your books".
        """
        if not self._report.askable:
            # The SAME sentence `propose_account` raises, plus the status and
            # the detail. Two refusals for one condition, worded differently,
            # is how a caller ends up matching on one of them and missing the
            # other - which is exactly what moving the check earlier would have
            # caused if the wording had been left to drift.
            raise MemoryNotReady(
                f"no successful bootstrap for company {self.identity.key!r}; "
                f"nothing may be proposed — refusing to {doing} for "
                f"{self.identity.name!r}: {self._report.status.value} — "
                f"{self._report.detail}"
            )

    def _require_ready(self, doing: str) -> None:
        if not self._report.askable:
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

        WHAT THE STORED CORRECTION HAS TO CARRY, AND WHY. D-05, 2026-08-10.
        ------------------------------------------------------------------
        Until this fix it carried the key and not the name, so a person's
        EXPLICIT answer was stored with `raw_subject = NULL` and read back as
        INCOMPLETE — the same state as a row written before the column
        existed. A human decision was therefore worth less as evidence than
        the voucher it was correcting, and the next entry for the same
        supplier asked the same question again.

        What is on the row afterwards:

            company_key         the scope, first column of the primary key
            raw_subject         the vendor text as the person's screen had it
            subject             the normalised lookup key
            account             the resolution they selected
            provenance          `human_answer`, the decision's source
            source_voucher_ids  the entry the answer was given about
            identity_evidence   COMPLETE, derived from `raw_subject`

        The candidates they did NOT select are preserved by NOT deleting them:
        every other account this vendor has ever been posted to keeps its own
        row and its own count, which is what the returned `CompanyMatch`
        reports and what makes CONFLICTED survive a correction.

        INCOMPLETE is still reachable and still correct — a party that is
        blank or is nothing but punctuation carries no name, so there is
        nothing to preserve and `_raw_identity` says so rather than inventing
        one. What is gone is INCOMPLETE by default.
        """
        self._require_ready("record a correction")
        subject = normalise_vendor(vendor)
        self._store.record_vendor(
            self.identity.key,
            subject,
            account,
            source_voucher_id=source_voucher_id,
            provenance=FROM_HUMAN_ANSWER,
            raw_subject=_raw_identity(vendor),
        )
        return self.lookup(vendor)

    def observe(self, voucher: Voucher) -> None:
        """Record one posted voucher as future company-local context.

        Called with a voucher read BACK out of Tally, so what is learned is
        what the ledger actually holds rather than what we believe we sent.

        `raw_subject` is `Voucher.party`, not the key. D-05, 2026-08-10.
        `bootstrap` kept the raw name from the first day this column existed
        and this did not, which made the loss invisible: a freshly bootstrapped
        company answered correctly, and every supplier it met afterwards was
        stored blind. That is the WORSE half of the two, because it is the
        half that grows — bootstrap runs once and this runs for every voucher
        posted, and for every voucher the accountant types into Tally between
        runs.
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
            raw_subject=_raw_identity(voucher.party),
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

        THE KEY AND THE NAME GO IN AS TWO FIELDS. D-05, 2026-08-10.
        -----------------------------------------------------------
        This used to call `idx.record(o.subject, o.account)`, and `record`
        documents its first argument as a RAW OBSERVED VENDOR NAME and records
        identity evidence as COMPLETE. `o.subject` is not one — it is the key
        the strip produced, with the naming prefix gone and the legal form
        canonicalised. So the live decision layer was handed a key, told it was
        a name, and compared it against the arriving name as though that were
        evidence. Every stored row looked COMPLETE, including the ones that had
        no raw name at all, and a legacy INCOMPLETE row was silently promoted
        to a confident SAME.

        `record_observed` takes the store's two fields and keeps them two
        fields. `o.raw_subject is None` reaches the comparison as INCOMPLETE
        and contributes no account, which is the whole of "a legacy row must
        never be a confident match where a legal form could matter".

        NOTHING IS RECONSTRUCTED. There is deliberately no branch here that
        falls back to `o.subject` when `o.raw_subject` is missing: the legal
        form was thrown away before this row was written and guessing it back
        is the one inference the ruling forbids.
        """
        idx = MemoryIndex()
        for o in self._store.vendors(self.identity.key):
            for _ in range(o.times):
                idx.record_observed(
                    normalised_subject=o.subject,
                    raw_subject=o.raw_subject,
                    account=o.account,
                )
        return idx


@dataclass(frozen=True)
class LiveDisagreement:
    """One vendor whose remembered account the CURRENT ledger contradicts.

    Both sources, both counts, in one value. The point of carrying the numbers
    rather than a boolean is that "your books used to say one thing and now say
    another" is only actionable when a person can see how much of each there is:
    one stray row against forty is a different conversation from sixty against
    forty, and a boolean flattens the two.

    Cannot be constructed for two sources that AGREE. That is enforced below
    rather than left to the caller, because the whole cost of this feature is
    false alarms, and the cheapest place to make an agreement impossible to
    report as a conflict is the type itself.
    """

    company_key: str
    #: The normalised vendor key both sources were resolved under.
    subject: str
    #: The single account memory would have proposed, and how many rows it saw.
    remembered_account: str
    remembered_times: int
    #: Every account the LIVE history posts this vendor to, most-used first.
    live_accounts: tuple[str, ...]
    live_times: tuple[int, ...]

    def __post_init__(self) -> None:
        if len(self.live_accounts) != len(self.live_times):
            raise ValueError(
                f"{len(self.live_accounts)} live account(s) but "
                f"{len(self.live_times)} count(s)"
            )
        if not self.live_accounts:
            raise ValueError(
                f"{self.subject!r} has no live history, so there is nothing for "
                f"memory to disagree with"
            )
        if not self.remembered_account:
            raise ValueError(
                f"{self.subject!r} has no remembered account, so memory is not "
                f"the thing being contradicted"
            )
        if self.live_accounts == (self.remembered_account,):
            raise ValueError(
                f"both sources say {self.remembered_account!r} for "
                f"{self.subject!r}; that is agreement, not a disagreement"
            )

    @property
    def changed_to(self) -> str:
        """The most-used live account that memory does not know about.

        Always exists: `__post_init__` refuses the only two shapes that could
        make it absent - an empty live history, and a live history that is
        exactly the remembered account and nothing else.
        """
        return next(a for a in self.live_accounts if a != self.remembered_account)

    @property
    def changed_times(self) -> int:
        return self.live_times[self.live_accounts.index(self.changed_to)]

    @property
    def live_detail(self) -> str:
        return ", ".join(
            f"{account} {times} time(s)"
            for account, times in zip(self.live_accounts, self.live_times, strict=True)
        )

    @property
    def detail(self) -> str:
        """Both sources and both counts, in one sentence a person can act on."""
        return (
            f"{self.subject} was {self.remembered_account} "
            f"{self.remembered_times} time(s) when this company's books were "
            f"read; the ledger in Tally now shows {self.live_detail}. The two "
            f"do not agree, so nothing is posted from memory until a person "
            f"says which this one is."
        )


def live_vendor_accounts(
    vendor: str, history: Sequence[Voucher]
) -> tuple[tuple[str, ...], tuple[int, ...]]:
    """One vendor's accounts in the history that was just read out of Tally.

    Derived by EXACTLY the rule `bootstrap._derive` uses — a row teaches nothing
    unless it names both a vendor and a debit account — and ordered by exactly
    the key `_resolve` orders by, most-used first and then alphabetically.

    Both of those are load-bearing rather than tidy. This answer exists only to
    be compared against the one memory gives, and two different derivations of
    "what does this vendor's history say" would disagree for reasons that have
    nothing whatever to do with staleness. Every such reason would arrive as a
    false alarm on a company whose books never changed.
    """
    key = normalise_vendor(vendor)
    if not key:
        # The same guard `pipeline.funding_from_history` needs and for the same
        # reason: "   " is truthy and normalises to the empty key, which is the
        # key every party-less row in the book already shares.
        return (), ()
    counts: dict[str, int] = {}
    for voucher in history:
        if voucher.debit_account and normalise_vendor(voucher.party) == key:
            counts[voucher.debit_account] = counts.get(voucher.debit_account, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return tuple(a for a, _ in ordered), tuple(n for _, n in ordered)


def disagrees_with_live_history(
    memory: CompanyMemory, vendor: str, history: Sequence[Voucher]
) -> LiveDisagreement | None:
    """Does the ledger read a moment ago still say what memory says? D-06.

    WHAT "DISAGREE" MEANS, EXACTLY
    ------------------------------
    Memory and the live ledger disagree about a vendor when RE-DERIVING that
    vendor from the history the caller was just handed would give an answer
    other than the one memory gave. Three conditions, all of them required:

        1. memory PROPOSED           `lookup` came back MATCH, so memory holds
                                     exactly one account M for this vendor and
                                     `propose_account` would return it. Anything
                                     else - CONFLICTED, NO_MATCH,
                                     MEMORY_NOT_READY - already produces a
                                     question or a refusal on its own, and there
                                     is no silent proposal to contradict.
        2. the live ledger SPEAKS    the history holds at least one usable row
                                     for this vendor. No rows is absence of
                                     evidence, not contradiction; see below.
        3. the live answer DIFFERS   the set of accounts the live rows name is
                                     not exactly {M}.

    Condition 3 is a set comparison and not a comparison of the top account,
    because the reproduced case has M still in the ledger:

        memory  Purchases 40                    -> MATCH, proposes Purchases
        live    Repairs 60, Purchases 40        -> {Repairs, Purchases} != {Purchases}

    A rule that compared only the most-used account would fire on that one and
    then fall silent the moment the counts crossed back over. A rule that asked
    "is M still in the ledger" would never fire on it at all, because it is.
    What actually changed is that the vendor is no longer consistent, and that
    is a fact about the SET.

    WHY THIS CANNOT FIRE ON A VENDOR WITH A SINGLE CONSISTENT ACCOUNT
    -----------------------------------------------------------------
    Condition 1 needs memory to hold exactly one account, and condition 3 needs
    the live set to be something other than that one account. A vendor posted
    to one account and only ever that account satisfies the first and fails the
    third, in both directions and by construction - which also means that
    immediately after a bootstrap, when memory was derived from this very
    history by this very rule, the function CANNOT return anything but None.
    Measured on the seven committed real ledgers in
    `tests/test_stale_memory_conflict.py`: 0 of 143 clean entries.

    That is the whole false-alarm argument, and it is structural rather than
    tuned. There is no threshold here to turn, so there is nothing to calibrate
    and nothing that can drift.

    AN EMPTY LIVE HISTORY IS NOT A DISAGREEMENT
    -------------------------------------------
    Condition 2 is deliberate and it is a real limit. A connector that succeeds
    and returns nothing looks identical, at this seam, to a company whose
    supplier genuinely has no posted history, and treating the pair as a
    contradiction would refuse every entry for the second in order to catch the
    first. The unavailable connector is handled where it can actually be told
    apart - it raises, and `pipeline.run` lets it, so nothing is written and
    nothing is guessed.

    THE FUNDING LEG IS NOT CHECKED HERE, ON PURPOSE
    -----------------------------------------------
    `pipeline.funding_from_history` reads the live history directly on every
    evaluation, so the credit leg has no snapshot to go stale. Only the debit
    leg comes out of memory, and only the debit leg needs this.
    """
    match = memory.lookup(vendor)
    if not match.may_propose:
        return None

    remembered = match.accounts[0]
    live_accounts, live_times = live_vendor_accounts(vendor, history)
    if not live_accounts or live_accounts == (remembered,):
        return None

    return LiveDisagreement(
        company_key=memory.identity.key,
        subject=match.subject,
        remembered_account=remembered,
        remembered_times=match.times[0],
        live_accounts=live_accounts,
        live_times=live_times,
    )


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

"""The mandatory first read.

THE PRODUCT FAILURE THIS EXISTS TO PREVENT
------------------------------------------
Connecting to a company that already has years of posted history and then
behaving like a fresh install. The vendor is in their ledger. The account is in
their ledger. Asking anyway — or worse, proposing from nothing — is the
difference between a tool that read the books and a tool that did not.

So nothing proposes an account for a company until all four of these have
happened and been COUNTED:

    1  identity   the company is open in Tally and names itself
    2  accounts   the chart of accounts is read
    3  vouchers   the posted voucher history is read
    4  index      the company-scoped index is built and stored

A step that fails, at any point, produces an INCOMPLETE report naming the step
that failed. An INCOMPLETE report answers every lookup with MEMORY_NOT_READY,
raises on every write, and is never treated as valid. There is no path in this
module that continues quietly with an empty index — the only way to get a READY
report is to reach the end of `bootstrap`.

A rebuild forgets first. Half of a new load merged into the remains of an old
one is not a smaller load, it is a wrong one.
"""

from __future__ import annotations

import datetime
from collections import Counter
from collections.abc import Sequence

from accountant.memory.company import (
    FROM_OUR_POSTING,
    FROM_TALLY_HISTORY,
    CompanyMemory,
)
from accountant.memory.identity import (
    CompanyIdentity,
    normalise_company,
    same_company_name,
)
from accountant.memory.index import normalise_phrase, normalise_vendor
from accountant.memory.store import (
    BootstrapCounts,
    BootstrapReport,
    BootstrapStatus,
    MemoryStore,
    Observation,
)
from accountant.schema import Voucher
from accountant.tallyio.client import TallyClient, operation_id_in

STEPS: tuple[str, ...] = ("identity", "accounts", "vouchers", "index")


def _stamp(now: datetime.datetime | None) -> str:
    moment = now if now is not None else datetime.datetime.now(datetime.UTC)
    return moment.isoformat()


class _Seen:
    """One subject's accounts while the history is being walked.

    A plain accumulator. It exists so counts and source voucher ids are built
    from the same pass, which is what makes `times` equal to the number of
    vouchers behind it rather than a separate number that could drift.
    """

    def __init__(self) -> None:
        self.ids: dict[str, list[str]] = {}
        self.sources: dict[str, set[str]] = {}

    def add(self, account: str, voucher_id: str, provenance: str) -> None:
        self.ids.setdefault(account, []).append(voucher_id)
        self.sources.setdefault(account, set()).add(provenance)

    def observations(self, company_key: str, subject: str) -> list[Observation]:
        return [
            Observation(
                company_key=company_key,
                subject=subject,
                account=account,
                times=len(ids),
                source_voucher_ids=tuple(ids),
                provenance="+".join(sorted(self.sources[account])),
            )
            for account, ids in sorted(self.ids.items())
        ]


def _derive(
    company_key: str, history: Sequence[Voucher]
) -> tuple[list[Observation], list[Observation], int]:
    """History -> company-scoped observations, plus the unusable row count.

    A voucher with no party or no debit account teaches nothing, and a blank
    key would pool every such voucher together. They are counted and reported,
    never silently dropped.
    """
    vendors: dict[str, _Seen] = {}
    phrases: dict[str, _Seen] = {}
    unusable = 0

    for v in history:
        vendor_key = normalise_vendor(v.party)
        if not vendor_key or not v.debit_account:
            unusable += 1
            continue
        provenance = (
            FROM_OUR_POSTING if operation_id_in(v.narration) else FROM_TALLY_HISTORY
        )
        vendors.setdefault(vendor_key, _Seen()).add(v.debit_account, v.id, provenance)
        phrase_key = normalise_phrase(v.narration)
        if phrase_key:
            phrases.setdefault(phrase_key, _Seen()).add(
                v.debit_account, v.id, provenance
            )

    return _rows(company_key, vendors), _rows(company_key, phrases), unusable


def _rows(company_key: str, seen: dict[str, _Seen]) -> list[Observation]:
    return [
        o
        for subject, accounts in sorted(seen.items())
        for o in accounts.observations(company_key, subject)
    ]


def _colliding_company(
    company: str, key: str, open_companies: Sequence[str]
) -> str | None:
    """Another company open in Tally RIGHT NOW that reduces to the same key.

    Checked against the live list rather than against the store, because the
    store can only ever hold one of a colliding pair — `company_key` is the
    sole primary key and the writer is `INSERT OR REPLACE`, so by the time two
    of them have been through here one has already replaced the other. The set
    of names actually open in Tally is the only place the pair both exist.

    Comparing keys and returning the ORIGINAL name is deliberate: the key is
    what collides, and the name is what a person can act on.
    """
    for other in open_companies:
        # `same_company_name`, not `!=`. The company we were asked for and the
        # one Tally reports are the same name in two Unicode forms - NFD from
        # macOS, NFC from Windows - and comparing them exactly made a company
        # collide with ITSELF and refuse to bootstrap. The collision this guard
        # exists for is two DIFFERENT names folding to one key, not one name
        # spelt two ways.
        if not same_company_name(other, company) and normalise_company(other) == key:
            return other
    return None


def _refused(
    store: MemoryStore,
    identity: CompanyIdentity,
    attempted_at: str,
    last_success: str,
    detail: str,
) -> CompanyMemory:
    """A refusal that touches NOTHING. No read, no write, no `forget`.

    Separate from `_incomplete` because `_incomplete` calls `save_bootstrap`,
    and on a key collision that write IS the damage: it would stamp this
    company's `display_name` onto the row belonging to the other company that
    shares the key. A refusal whose own record corrupts the thing it is
    refusing to touch is not a refusal.
    """
    return CompanyMemory(
        BootstrapReport(
            identity=identity,
            status=BootstrapStatus.COMPANY_KEY_COLLISION,
            detail=detail,
            attempted_at=attempted_at,
            bootstrapped_at=last_success,
            steps=(),
        ),
        # The real store, not None: the refusal writes nothing, and a
        # non-READY report already makes every lookup raise MemoryNotReady.
        store,
    )


def _incomplete(
    store: MemoryStore,
    identity: CompanyIdentity,
    done: Sequence[str],
    attempted_at: str,
    last_success: str,
    detail: str,
) -> CompanyMemory:
    """Record the failure, then hand back memory that refuses to answer."""
    report = BootstrapReport(
        identity=identity,
        status=BootstrapStatus.INCOMPLETE,
        detail=detail,
        attempted_at=attempted_at,
        bootstrapped_at=last_success,
        steps=tuple(done),
    )
    store.save_bootstrap(report)
    return CompanyMemory(report, store)


def _readiness(counts: BootstrapCounts) -> tuple[BootstrapStatus, str]:
    """Which state the counts actually justify. Owner decision, 2026-08-09.

    Reading succeeded in all three cases here — that is exactly why the reads
    cannot be the test. The question is what we LEARNED, and the answer is two
    integers:

        vouchers == 0                -> EMPTY_SOURCE       nothing to learn from
        vouchers > 0, mappings == 0  -> EMPTY_VENDOR_INDEX should have learned
        mappings > 0                 -> READY

    The middle case is the one that used to be READY. A company with history
    that yields no usable mapping is not a company we understand; it is a
    signal that the read, the data or the derivation is wrong, and the safe
    response is to do nothing rather than to serve confident answers about
    books we could not parse.
    """
    if counts.vouchers == 0:
        return (
            BootstrapStatus.EMPTY_SOURCE,
            "read this company and found no posted history at all, so it may "
            "be asked about an entry but nothing may be proposed",
        )
    if counts.mappings == 0:
        return (
            BootstrapStatus.EMPTY_VENDOR_INDEX,
            "read this company's history and derived no usable vendor mapping "
            "from it, so nothing here is trustworthy enough to act on",
        )
    return (BootstrapStatus.READY, "loaded")


def bootstrap(
    client: TallyClient,
    company: str,
    store: MemoryStore,
    *,
    now: datetime.datetime | None = None,
) -> CompanyMemory:
    """Read this company out of Tally and build its scoped index. All or nothing."""
    identity = CompanyIdentity.from_name(company)
    attempted_at = _stamp(now)
    previous = store.state(identity.key)
    last_success = previous.bootstrapped_at if previous is not None else ""

    done: list[str] = []
    try:
        # D3. EVERY check that can refuse this company runs BEFORE `forget()`.
        #
        # `store.forget(identity.key)` used to sit four lines above here,
        # unconditional. So the first opportunity to notice anything was wrong
        # came AFTER the previous company's vendor, phrase, chart and company
        # rows had already been deleted — and `_incomplete` then wrote a row
        # under the shared key carrying THIS company's display name, so
        # `resume(the other one)` came back INCOMPLETE under the wrong name.
        # Destroying an index is the last thing we do, not the first.
        open_companies = client.list_companies()
        # D-C: NFC comparison, see `same_company_name`.
        matched = next(
            (o for o in open_companies if same_company_name(company, o)), None
        )
        if matched is None:
            return _incomplete(
                store,
                identity,
                done,
                attempted_at,
                last_success,
                f"{company!r} is not open in Tally, so its history cannot be read",
            )

        clash = _colliding_company(company, identity.key, open_companies)
        if clash is not None:
            # NOT `_incomplete` — that writes, and writing is the damage.
            return _refused(
                store,
                identity,
                attempted_at,
                last_success,
                f"{company!r} and {clash!r} are both open in Tally and both "
                f"reduce to the same internal name {identity.key!r}, so their "
                f"histories cannot be told apart. Nothing was read and nothing "
                f"was changed. Rename one of them in Tally, then try again.",
            )
        # Tally's spelling wins from here on. We matched on NFC, so the name
        # we were configured with and the name Tally holds can differ by
        # normal form; every later call passes this string to the client, and
        # the client looks companies up by exact name. Asking for the spelling
        # the book of record does not use is how a company that IS open reads
        # as missing.
        company = matched
        done.append("identity")

        # Safe now: this is the right company, and no other open company can
        # be confused with it.
        store.forget(identity.key)

        chart = client.read_accounts(company)
        done.append("accounts")

        history = client.read_vouchers(company)
        done.append("vouchers")

        vendors, phrases, unusable = _derive(identity.key, history)
        per_vendor = Counter(o.subject for o in vendors)
        counts = BootstrapCounts(
            vouchers=len(history),
            vendors=len(per_vendor),
            accounts=len(chart),
            mappings=len(vendors),
            conflicts=sum(1 for seen in per_vendor.values() if seen > 1),
            unusable=unusable,
        )
        status, why = _readiness(counts)
        report = BootstrapReport(
            identity=identity,
            status=status,
            detail=(
                f"{why}: {counts.vouchers} voucher(s), {counts.vendors} "
                f"vendor(s), {counts.accounts} account(s), {counts.mappings} "
                f"mapping(s), {counts.unusable} unusable for {company!r}"
            ),
            attempted_at=attempted_at,
            # Only a genuine READY records a successful read. EMPTY_SOURCE and
            # EMPTY_VENDOR_INDEX must not update "when did we last really read
            # this company", because neither of them did.
            bootstrapped_at=attempted_at if status is BootstrapStatus.READY else "",
            counts=counts,
            steps=(*done, "index"),
        )
        store.save_bootstrap(report, chart=chart, vendors=vendors, phrases=phrases)
    except Exception as exc:  # any connector or store failure, named not hidden
        return _incomplete(
            store,
            identity,
            done,
            attempted_at,
            last_success,
            f"reading {company!r} failed at step {STEPS[len(done)]!r}: "
            f"{type(exc).__name__}: {exc}",
        )
    return CompanyMemory(report, store)


def resume(store: MemoryStore, company: str) -> CompanyMemory:
    """Re-open a company from our store alone, without contacting Tally.

    Hands back exactly what the last bootstrap recorded, which for a company
    that has never been bootstrapped is NEVER_RUN — and therefore
    MEMORY_NOT_READY on every lookup. An empty index never passes for a loaded
    one.
    """
    identity = CompanyIdentity.from_name(company)
    state = store.state(identity.key)
    if state is None:
        state = BootstrapReport(
            identity=identity,
            status=BootstrapStatus.NEVER_RUN,
            detail=f"no bootstrap has ever run for {company!r}",
            attempted_at="",
        )
    return CompanyMemory(state, store)


class MemorySession:
    """Exactly one company is open at a time.

    Opening a company invalidates the handle to whatever was open before and
    rebuilds from Tally. The previous company's memory is never reused: the old
    handle stops answering, and the new one is assembled from rows carrying
    only the new company's key.
    """

    def __init__(self, store: MemoryStore) -> None:
        self._store = store
        self._current: CompanyMemory | None = None

    @property
    def current(self) -> CompanyMemory | None:
        return self._current

    def open(
        self,
        client: TallyClient,
        company: str,
        *,
        now: datetime.datetime | None = None,
    ) -> CompanyMemory:
        previous = self._current
        if previous is not None:
            previous.invalidate(
                f"handle for {previous.identity.name!r} was superseded when "
                f"{company!r} was opened"
            )
        self._current = bootstrap(client, company, self._store, now=now)
        return self._current

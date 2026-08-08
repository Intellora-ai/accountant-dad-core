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
from accountant.memory.identity import CompanyIdentity
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
    store.forget(identity.key)

    done: list[str] = []
    try:
        if company not in client.list_companies():
            return _incomplete(
                store,
                identity,
                done,
                attempted_at,
                last_success,
                f"{company!r} is not open in Tally, so its history cannot be read",
            )
        done.append("identity")

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
        report = BootstrapReport(
            identity=identity,
            status=BootstrapStatus.READY,
            detail=(
                f"loaded {counts.vouchers} voucher(s), {counts.vendors} vendor(s), "
                f"{counts.accounts} account(s), {counts.mappings} mapping(s) "
                f"for {company!r}"
            ),
            attempted_at=attempted_at,
            bootstrapped_at=attempted_at,
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

"""An in-memory Tally that speaks the TallyClient interface.

Exists because the Windows VM with real TallyPrime is deferred. Everything
downstream can be built and tested against this today.

What it CAN prove: our code stamps markers, refuses duplicate operation IDs,
reads back what it wrote, and reverses by operation ID rather than by amount.

What it CANNOT prove: that any of this survives contact with real Tally. The
reverse-restores-trial-balance test is only meaningful against the real thing.
This fake is honest about the shape, not about the integration.

THE TRANSPORT IS SIMULATED. THE SAFETY DECISIONS ARE NOT.
---------------------------------------------------------
This double may be softer than `RealTally` about HOW a voucher is fetched -
there is no XML, no port 9000, no MASTERID. It may never be softer about WHAT
to do with what it found. A double that makes an easier call than the thing it
stands in for does not merely fail to catch a bug; it issues an alibi, because
a test written against it can show an ambiguity being handled when it is not.

Defect W4, fixed 2026-08-09, was exactly that: a marker matching two vouchers
made `RealTally` refuse (`real.py:1797`) and made this file pick the first.
`read_by_operation_id` and `reverse_by_operation_id` now collect every match and
raise the SAME `TallyDataError`, worded the same way, so one assertion holds
both backends. The import direction is deliberate - the fake depends on the real
connector's refusal, never the reverse, and `tests/test_runtime_backend.py`
forbids any shipped module from importing this one.

The agreement is pinned by
`tests/test_adversarial_write_path.py::test_both_backends_*`, and not by
`tests/test_tally_contract.py`, which is frozen; that file says why.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from accountant.schema import Voucher
from accountant.tallyio.client import (
    CompanyNotBackedUp,
    DuplicateOperation,
    WriteResult,
    operation_id_in,
    stamp,
)
from accountant.tallyio.real import TallyDataError


@dataclass
class _Company:
    accounts: list[str] = field(default_factory=list[str])
    vouchers: list[Voucher] = field(default_factory=list[Voucher])
    backed_up: bool = False
    next_tally_id: int = 1


class FakeTally:
    """Implements TallyClient in memory."""

    def __init__(self) -> None:
        self._companies: dict[str, _Company] = {}

    # ---- test setup helpers, not part of TallyClient -----------------------

    def add_company(
        self,
        name: str,
        accounts: tuple[str, ...] = (),
        vouchers: tuple[Voucher, ...] = (),
        backed_up: bool = True,
    ) -> None:
        self._companies[name] = _Company(
            accounts=list(accounts), vouchers=list(vouchers), backed_up=backed_up
        )

    def seed_voucher(self, company: str, voucher: Voucher) -> None:
        """Place a voucher we did NOT write, as if the accountant typed it."""
        self._co(company).vouchers.append(voucher)

    def close_company(self, name: str) -> None:
        """Take a company out of the open list, exactly as Tally would.

        The books are DISCARDED, not hidden, which is the honest simulation:
        Tally serves nothing at all for a company that is not open, so a fake
        that kept answering for it would make a closed company look like an
        open one to every read.

        Added 2026-08-09 for `tests/test_company_identity.py`. Closing a
        company mid-session is the one way the runtime's company can stop being
        the company Tally has, and there was no way to stage it.
        """
        self._companies.pop(name, None)

    def set_backup(self, company: str, recorded: bool) -> None:
        """Change the backup record without disturbing the books.

        `add_company` can set it, but only by rebuilding the company and
        throwing away its vouchers. The gate has to be testable on a company
        that already has entries in it — that is the only situation in which
        refusing to reverse actually protects anything.
        """
        self._co(company).backed_up = recorded

    def _co(self, company: str) -> _Company:
        try:
            return self._companies[company]
        except KeyError:
            raise KeyError(f"no such company: {company!r}") from None

    # ---- TallyClient -------------------------------------------------------

    def list_companies(self) -> tuple[str, ...]:
        return tuple(self._companies)

    def read_accounts(self, company: str) -> tuple[str, ...]:
        return tuple(self._co(company).accounts)

    def read_vouchers(self, company: str) -> tuple[Voucher, ...]:
        return tuple(self._co(company).vouchers)

    def trial_balance(self, company: str) -> dict[str, int]:
        balances: dict[str, int] = {}
        for v in self._co(company).vouchers:
            balances[v.debit_account] = (
                balances.get(v.debit_account, 0) + v.amount_paise
            )
            balances[v.credit_account] = (
                balances.get(v.credit_account, 0) - v.amount_paise
            )
        return {k: v for k, v in balances.items() if v != 0}

    def write_voucher(
        self, company: str, voucher: Voucher, operation_id: str
    ) -> WriteResult:
        co = self._co(company)

        if not co.backed_up:
            raise CompanyNotBackedUp(
                f"{company!r} has no recorded backup; refusing to write"
            )

        if self.read_by_operation_id(company, operation_id) is not None:
            raise DuplicateOperation(
                f"operation {operation_id!r} was already written to {company!r}"
            )

        narration = stamp(voucher.narration, operation_id)
        tally_id = f"TALLY-{co.next_tally_id}"
        co.next_tally_id += 1

        written = replace(voucher, narration=narration, tally_id=tally_id)
        co.vouchers.append(written)
        return WriteResult(
            operation_id=operation_id, tally_id=tally_id, narration=narration
        )

    def _positions_carrying(self, company: str, operation_id: str) -> list[int]:
        """Every position in the register whose narration carries this marker.

        All of them, never the first hit. Stopping at the first match is what
        makes a two-match register look like a one-match register, and the
        caller can then no longer tell the difference.
        """
        return [
            i
            for i, v in enumerate(self._co(company).vouchers)
            if operation_id_in(v.narration) == operation_id
        ]

    def _the_one_position_carrying(self, company: str, operation_id: str) -> int | None:
        """The single match, `None` for no match, and a refusal for two or more.

        Mirrors `RealTally._read_exported_by_operation_id` (real.py:1781),
        including the wording: the marker is this system's identity, so two
        vouchers wearing it is an ambiguity, not a menu. Nothing is read back
        and nothing is deleted until a person says which one is real.
        """
        found = self._positions_carrying(company, operation_id)
        if not found:
            return None
        if len(found) > 1:
            vouchers = self._co(company).vouchers
            where = "; ".join(
                f"{vouchers[i].id!r} ({vouchers[i].tally_id or 'no tally id'}, "
                f"{vouchers[i].amount_paise} paise)"
                for i in found
            )
            raise TallyDataError(
                f"operation {operation_id!r} matches {len(found)} vouchers in "
                f"{company!r} ({where}). The narration marker is this system's "
                "identity and it has to be unique. Refusing to read one back or "
                "delete any of them: a person has to decide which is real."
            )
        return found[0]

    def read_by_operation_id(self, company: str, operation_id: str) -> Voucher | None:
        at = self._the_one_position_carrying(company, operation_id)
        return None if at is None else self._co(company).vouchers[at]

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        co = self._co(company)
        # The same gate `write_voucher` has. A delete is the more destructive of
        # the two and until 2026-08-09 it was the ungated one: a bulk reverse
        # could empty a company nobody had backed up while a single write to
        # that company was refused.
        if not co.backed_up:
            raise CompanyNotBackedUp(
                f"{company!r} has no recorded backup; refusing to reverse"
            )
        at = self._the_one_position_carrying(company, operation_id)
        if at is None:
            return False
        del co.vouchers[at]
        return True

    def list_our_vouchers(self, company: str) -> tuple[Voucher, ...]:
        return tuple(
            v for v in self._co(company).vouchers if operation_id_in(v.narration)
        )

    def backed_up(self, company: str) -> bool:
        return self._co(company).backed_up

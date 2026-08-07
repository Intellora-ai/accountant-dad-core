"""An in-memory Tally that speaks the TallyClient interface.

Exists because the Windows VM with real TallyPrime is deferred. Everything
downstream can be built and tested against this today.

What it CAN prove: our code stamps markers, refuses duplicate operation IDs,
reads back what it wrote, and reverses by operation ID rather than by amount.

What it CANNOT prove: that any of this survives contact with real Tally. The
reverse-restores-trial-balance test is only meaningful against the real thing.
This fake is honest about the shape, not about the integration.
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


@dataclass
class _Company:
    accounts: list[str] = field(default_factory=list)
    vouchers: list[Voucher] = field(default_factory=list)
    backed_up: bool = False
    _next_tally_id: int = 1


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
        tally_id = f"TALLY-{co._next_tally_id}"
        co._next_tally_id += 1

        written = replace(voucher, narration=narration, tally_id=tally_id)
        co.vouchers.append(written)
        return WriteResult(
            operation_id=operation_id, tally_id=tally_id, narration=narration
        )

    def read_by_operation_id(self, company: str, operation_id: str) -> Voucher | None:
        for v in self._co(company).vouchers:
            if operation_id_in(v.narration) == operation_id:
                return v
        return None

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        co = self._co(company)
        for i, v in enumerate(co.vouchers):
            if operation_id_in(v.narration) == operation_id:
                del co.vouchers[i]
                return True
        return False

    def list_our_vouchers(self, company: str) -> tuple[Voucher, ...]:
        return tuple(
            v for v in self._co(company).vouchers if operation_id_in(v.narration)
        )

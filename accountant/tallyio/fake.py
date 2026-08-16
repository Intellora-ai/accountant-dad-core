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

W6, fixed 2026-08-09, was the same shape on the WRITE path, which is worse:
`RealTally` refuses a ledger that is not in the company's chart, and this file
never looked at the chart at all. A voucher to "Nonexistent Ledger", or to
"purchases" against a chart holding "Purchases", returned a clean
`WriteResult` here and a `TallyDataError` there. Now both refuse, with the same
class and the same wording.

The agreement is pinned by
`tests/test_adversarial_write_path.py::test_both_backends_*`, and not by
`tests/test_tally_contract.py`, which is frozen; that file says why.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from accountant.schema import Voucher
from accountant.tallyio import chart as ledger_chart
from accountant.tallyio.client import (
    CompanyNotBackedUp,
    DuplicateOperation,
    WriteResult,
    operation_id_in,
    stamp,
)
from accountant.tallyio.real import (
    AmbiguousMarker,
    TallyDataError,
    check_writable,
)


@dataclass
class _Company:
    accounts: list[str] = field(default_factory=list[str])
    vouchers: list[Voucher] = field(default_factory=list[Voucher])
    backed_up: bool = False
    next_tally_id: int = 1
    #: Ledger name -> its Tally group, for the ledgers this company has one for.
    #:
    #: SEPARATE FROM `accounts` rather than replacing it, so that the thirty-odd
    #: existing `add_company(accounts=(...))` call sites keep meaning what they
    #: meant: a chart of names with NO groups. That is not a shortcut, it is the
    #: honest simulation of a gateway whose response carries no `<PARENT>`, and
    #: `chart.derive_group` refuses on it rather than inventing one.
    groups: dict[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True)
class _ChartBook:
    """`chart.LedgerBook` over the in-memory company. The real one is `real._ChartBook`.

    Same protocol, same caller, so the placement rule that decides where a
    missing ledger goes is ONE piece of code with two transports under it -
    which is the only arrangement in which "both backends behave identically"
    is a fact instead of two copies that agree today.
    """

    company: _Company

    def chart(self) -> tuple[ledger_chart.Placement, ...]:
        return tuple(
            ledger_chart.Placement(name=name, parent=self.company.groups.get(name, ""))
            for name in self.company.accounts
        )

    def create(self, name: str, group: str) -> str:
        """Always succeeds, and says so the same way a confirmed create does.

        There is no transport to fail and no read-back to come back empty, so
        this returns "". The failure paths belong to `real._ChartBook`, where
        they are real; simulating them here would make the double able to fail
        in ways it cannot actually reproduce, which is the mirror image of the
        alibi this module's docstring is about.
        """
        self.company.accounts.append(name)
        self.company.groups[name] = group
        return ""


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
        *,
        groups: dict[str, str] | None = None,
    ) -> None:
        """`groups` is the chart's PARENT column - ledger name -> Tally group.

        Keyword-only and optional so that every existing call site keeps
        describing a company whose gateway reports no groups. A company with no
        groups is not a company with default groups: `chart.derive_group`
        refuses to place a new ledger in it, which is the measured `TANVEER
        SIDHU` case (`masters.py:5-9`) and the correct answer for it.
        """
        self._companies[name] = _Company(
            accounts=list(accounts),
            vouchers=list(vouchers),
            backed_up=backed_up,
            groups=dict(groups or {}),
        )

    def ledger_book(self, company: str) -> ledger_chart.LedgerBook:
        """This company's chart, behind the interface the placement rule uses.

        A test helper, not part of `TallyClient`. It exists so one test can
        drive `chart.place_ledgers` against BOTH backends and compare the two
        answers character for character - which is the only way "both backends
        behave identically" is a measurement rather than a hope.
        """
        return _ChartBook(self._co(company))

    def ledger_group(self, company: str, name: str) -> str:
        """The group a ledger sits under, or "" if the chart does not say.

        A test helper, not part of `TallyClient`. It exists so a test can assert
        WHERE a created ledger landed - the one fact that decides whether a
        balance is a credit or a debit and that no trial-balance comparison
        catches (`docs/RUNBOOK_PHASE5_ACCEPTANCE.md:180-186`).
        """
        return self._co(company).groups.get(name, "")

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

        # W6, 2026-08-09. `RealTally._check_ledgers_exist` has always refused a
        # ledger that is not in the company's chart, EXACTLY. This file never
        # looked at `co.accounts` on the write path at all, which made the
        # double softer than the connector on the one path that touches
        # somebody's books. See the module docstring: a double that makes an
        # easier call does not merely fail to catch a bug, it issues an alibi.
        #
        # 2026-08-13: the refusal became a PLACEMENT. A missing ledger is now
        # created under a group read from this company's own chart, and refused
        # only when the chart cannot say where it belongs. The rule, the
        # refusal and its wording are `chart.place_ledgers` - the SAME function
        # `RealTally._check_ledgers_exist` calls, not a copy of it. Two matched
        # copies is what W6 and W4 both were, and `schema.py:115-132` records
        # the third instance of the same drift.
        #
        # THE CLAIM THAT USED TO SIT HERE, and its answer. This comment said
        # Tally "creates one silently if you send it a name it does not have,
        # so 'purchases' against a chart holding 'Purchases' makes a second
        # ledger next to the real one". `RealTally._check_ledgers_exist` said
        # the opposite - that Tally does NOT create a master on the fly and the
        # import fails. Neither has been measured against a licensed
        # TallyPrime and they cannot both be true. The resolution is written
        # out in full at `real.py::RealTally._check_ledgers_exist`: pre-creating
        # under a derived group is the better move under either reading, so the
        # code does not have to choose. What did not survive either way is a
        # name differing only in case, which is refused rather than created.
        #
        # An empty leg still arrives here as a ledger named '' that the chart
        # does not hold, and `chart.derive_group` refuses it - exactly how
        # `RealTally` reports it. Skipping the blank would make the double
        # answer with a different exception class for the same voucher.
        refusal = ledger_chart.place_ledgers(
            _ChartBook(co), company, voucher, operation_id
        )
        if refusal:
            raise TallyDataError(refusal)

        # W6 AGAIN, and only half fixed the first time. 2026-08-10.
        #
        # W6 taught this file that a double may never make an easier call than
        # the connector it stands in for, and the fix mirrored ONE of
        # `RealTally`'s write-path checks - the chart lookup below. It did not
        # mirror `check_writable`, so seven vouchers `RealTally` refuses were
        # written here without complaint: an empty ledger leg, a zero amount, a
        # negative amount, a float amount, a bool amount, one ledger on both
        # legs, and a voucher carrying GST this connector builds no tax line
        # for. The empty-leg case wrote into a ledger named '' and the trial
        # balance came back {'Purchases': 100000, '': -100000}.
        #
        # Calling the connector's own function rather than restating its rules
        # is the point: a restatement can drift, and a drifting double issues
        # an alibi. The import direction stays fake -> real, never the reverse.
        check_writable(voucher)
        # AFTER the chart check, because that is the order `RealTally` uses:
        # a blank leg is reported as a ledger that does not exist, not as a
        # missing leg. Same refusal, same class, same order.

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
            # D2: `AmbiguousMarker`, the same class `RealTally` raises. The
            # wording already matched; the class did not, so a caller that
            # catches the specific exception behaved differently per backend.
            raise AmbiguousMarker(
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

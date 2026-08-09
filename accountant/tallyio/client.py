"""The Tally boundary.

Correction C3: all application code depends on `TallyClient`. Nothing outside
this package touches XML, port 9000, or anything Tally-shaped. That keeps XML
handling out of memory, detectors and the web app, and lets the whole system be
tested against a fake.

Correction C4: every voucher we write carries a unique operation ID *and* the
Accountant Dad marker.

Correction C5: a retry with the same operation ID must not create a second
voucher.

Correction C6: every post is read back, and reversal is checked against the exact
prior trial balance.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from accountant.schema import Voucher

MARKER_PREFIX = "ACCOUNTANT_DAD"
_MARKER_RE = re.compile(rf"\[{MARKER_PREFIX}:(?P<op>[A-Za-z0-9_\-]+)\]")


def new_operation_id() -> str:
    """Generated BEFORE posting. Attaches to the draft, the answer, the Tally
    narration, the action log and the reversal request."""
    return f"ad_{uuid.uuid4().hex}"


def marker_for(operation_id: str) -> str:
    return f"[{MARKER_PREFIX}:{operation_id}]"


def operation_id_in(narration: str) -> str | None:
    """Extract our operation ID from a narration, or None if we did not write it."""
    m = _MARKER_RE.search(narration)
    return m.group("op") if m else None


def stamp(narration: str, operation_id: str) -> str:
    """Attach the marker to a narration. Idempotent for the same operation ID."""
    existing = operation_id_in(narration)
    if existing == operation_id:
        return narration
    if existing is not None:
        raise ValueError(
            f"narration already carries operation {existing!r}, refusing to restamp"
        )
    return f"{narration} {marker_for(operation_id)}".strip()


class DuplicateOperation(Exception):
    """C5. Raised when the same operation ID is written twice.

    A dropped network response must not become a duplicate statutory entry.
    """


class CompanyNotBackedUp(Exception):
    """#6.7. We refuse to write to a company file that has not been backed up."""


@dataclass(frozen=True)
class WriteResult:
    operation_id: str
    tally_id: str
    narration: str


@runtime_checkable
class TallyClient(Protocol):
    """Every capability the rest of the system is allowed to use.

    The real connector and the fake both implement this. The tests that matter
    run against both.
    """

    def list_companies(self) -> tuple[str, ...]: ...

    def read_accounts(self, company: str) -> tuple[str, ...]:
        """The company's chart of accounts. Clarifying questions may only offer
        accounts from this list."""
        ...

    def read_vouchers(self, company: str) -> tuple[Voucher, ...]:
        """Posted history. This is what the memory index is built from."""
        ...

    def trial_balance(self, company: str) -> dict[str, int]:
        """Account name -> balance in paise. Used to prove reversal is exact."""
        ...

    def write_voucher(
        self, company: str, voucher: Voucher, operation_id: str
    ) -> WriteResult:
        """Write one voucher, stamped with the marker.

        Raises DuplicateOperation if this operation ID was already written.
        Raises CompanyNotBackedUp if the company has no recorded backup.
        """
        ...

    def read_by_operation_id(self, company: str, operation_id: str) -> Voucher | None:
        """C6 read-back. Proves the voucher exists, rather than trusting a 200."""
        ...

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        """Reverse exactly the voucher carrying this operation ID.

        Never by amount, never by narration text. Returns False if not found.
        """
        ...

    def list_our_vouchers(self, company: str) -> tuple[Voucher, ...]:
        """Every voucher we wrote, found by marker. Powers bulk reverse."""
        ...

    def backed_up(self, company: str) -> bool:
        """Whether a backup has been RECORDED for this company. Read-only.

        The ninth method, added 2026-08-09 for G5.2. Until then the backup gate
        existed only inside `write_voucher`, which had two consequences:

          * nothing could ASK. Phase 5 requires the evidence bundle to state the
            backup identity before a batch runs, and a fact only discoverable by
            attempting a write is not a fact a preview can report.
          * `reverse_by_operation_id` was NOT gated at all. A delete is the more
            destructive of the two operations and it was the ungated one — a
            batch could remove ten vouchers from a company nobody had backed up,
            while a single write to the same company was refused.

        Both are closed by this method plus the gate now on the delete path. A
        missing backup RECORD and a missing backup are treated as the same
        thing, which is the fail-closed reading and the one already used by
        `RecordedBackups`.
        """
        ...

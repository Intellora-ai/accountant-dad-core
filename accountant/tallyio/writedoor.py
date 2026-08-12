"""The runtime write door. Nothing reaches TallyPrime as a write without a permit.

WHY THIS IS HERE AND NOT IN `tests/test_write_door.py`
--------------------------------------------------------
`tests/test_write_door.py` is a pytest module. It scans the AST at test time and
proves that no second door was built. It cannot be the runtime guard: `tests/`
is not part of the installed distribution, so shipped code importing from it
would fail on any machine that installed the package rather than cloning it.

So the two halves are:

    accountant/tallyio/writedoor.py   asked at RUN time, refuses or permits
    tests/test_write_door.py          asked at TEST time, proves nobody
                                      bypassed the runtime one

Both are needed. A runtime guard alone can be skipped by a new module that
simply does not call it; a static guard alone cannot stop a write that is
already happening in front of a customer's books.

WHY AN ALLOW-LIST WITH SENTENCES RATHER THAN A BOOLEAN
-------------------------------------------------------
`POSTING_ENABLED=True` is one flag for every write in the system, and a flag
answers "may anything be written" when the question worth asking is "may THIS be
written, to THIS company, and who decided". Each permit below carries a
paragraph a reviewer reads in the diff. Adding one is deliberate; flipping a
boolean is not.

THE COMPANY IS PART OF THE PERMIT, DELIBERATELY
------------------------------------------------
TallyPrime's gateway serves whichever company is open on screen. A request that
does not name one writes into whatever happens to be loaded, and a permit that
does not name one authorises writing into somebody else's books. So `company` is
matched, and `"*"` has to be written out rather than being the default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

from accountant.tallyio import errors

#: The environment variable that turns every write off at once.
#:
#: FAILS OPEN ON PURPOSE, and that is not a mistake. This door is not the
#: security boundary - the permits below are, and `accountant/web/app.py`
#: already refuses an unauthenticated or cross-tenant request long before
#: anything reaches here. A variable that had to be SET for writes to work
#: would mean a deployment that forgot it silently posts nothing while
#: reporting success, which is the failure this repository keeps finding.
#: Set it to "0" to stop every write.
ENV_POSTING: Final = "ACCOUNTANT_POSTING_ENABLED"

#: Requires `confirm=True` on anything marked destructive.
ENV_SAFE_MODE: Final = "ACCOUNTANT_SAFE_MODE"


class WriteNotAllowedError(errors.TallyPolicyError):
    """Refused before Tally was asked. Nothing was sent.

    A `TallyPolicyError` rather than a business error, because nothing was
    attempted and retrying the identical call will be refused identically. The
    fix is a decision, not a correction to the data.
    """


@dataclass(frozen=True)
class Permit:
    """One write this system is allowed to perform, and why.

    `destructive` marks anything that removes or overwrites rather than adds.
    Those need `confirm=True` from the caller when safe mode is on, so a loop
    cannot quietly delete a year of entries.
    """

    op: str
    company: str
    why: str
    destructive: bool = False


#: EVERY WRITE THIS SYSTEM MAY PERFORM. Adding an entry is a decision somebody
#: makes on purpose and a reviewer reads in the diff.
ALLOWED_WRITES: Final[tuple[Permit, ...]] = (
    Permit(
        op="create_ledger",
        company="TANVEER SIDHU",
        why=(
            "Ledger creation is a prerequisite for every voucher: a Purchase "
            "names at least two ledgers and refuses if either is absent, and "
            "measured 2026-08-12 this company had only Tally's two defaults. "
            "It is additive - a new ledger changes no existing balance and "
            "removes nothing - and every request and response is written to "
            "the audit log as raw XML before the result is reported."
        ),
    ),
    Permit(
        op="create_purchase_voucher",
        company="TANVEER SIDHU",
        why=(
            "Posting a Purchase is the whole point of the product: a supplier "
            "bill becomes an entry in the customer's own books without anybody "
            "retyping it. It is allowed here for one named company, it is "
            "read back by voucher number after writing so a write TallyPrime "
            "accepts but does not keep is reported as a failure rather than a "
            "success, and the request and response XML are both retained."
        ),
    ),
    Permit(
        op="create_sales_voucher",
        company="TANVEER SIDHU",
        why=(
            "A Sales voucher is the other half of the bookkeeping this product "
            "exists to do: an invoice raised becomes a debtor and a line of "
            "income without anybody retyping it. It debits the customer and "
            "credits the Sales ledger, and that direction is not left to "
            "chance - the two sides are totalled in paise and refused if they "
            "differ before any envelope is built. It adds an entry and removes "
            "nothing, it carries a unique operation id so a retry finds the "
            "existing voucher instead of writing a second one, and it is read "
            "back by that id afterwards."
        ),
    ),
    Permit(
        op="create_payment_voucher",
        company="TANVEER SIDHU",
        why=(
            "A Payment records money leaving the business to settle what a "
            "supplier is owed, which is the entry that turns an unpaid bill "
            "into a paid one. It is permitted because a bookkeeping system "
            "that can record the bill but not the payment leaves the customer "
            "doing half the job by hand. What protects it: one named company, "
            "a balance check in paise before anything is sent, an operation id "
            "that makes a repeat post impossible, and a read-back by that id "
            "before success is reported."
        ),
    ),
    Permit(
        op="create_receipt_voucher",
        company="TANVEER SIDHU",
        why=(
            "A Receipt is the mirror of a Payment: a customer has paid, cash "
            "or bank goes up, and what they owed comes down. Without it every "
            "invoice this system posts stays outstanding for ever in the "
            "customer's own books, which is a worse lie than not posting at "
            "all. Same protections as the rest of the six - one company, "
            "debits equal credits before sending, a unique operation id "
            "checked against the books first, and a read-back by that id "
            "afterwards."
        ),
    ),
    Permit(
        op="create_journal_voucher",
        company="TANVEER SIDHU",
        why=(
            "A Journal is the entry with no outside party: a correction, an "
            "accrual, a transfer between two ledgers none of the other five "
            "types describe. It is also the most dangerous of them, because "
            "the caller names both sides and nothing here can tell a correct "
            "Journal from a plausible wrong one. So it is permitted with the "
            "checks that CAN be made: the two sides must balance exactly in "
            "paise, they must not be the same ledger twice, both reach the "
            "audit log with the side they were posted to, and the operation id "
            "is checked before the write and read back after it."
        ),
    ),
    Permit(
        op="create_contra_voucher",
        company="TANVEER SIDHU",
        why=(
            "A Contra moves the company's own money between its own accounts - "
            "cash to bank, bank to cash - and touches no customer or supplier "
            "at all. It is needed because without it every cash withdrawal has "
            "to be entered by hand, and a bank balance that does not match the "
            "statement is the first thing a person notices and the last thing "
            "they trust again. It sends no party element rather than an empty "
            "one, both accounts are named by the caller and checked equal and "
            "distinct before sending, and the operation id makes the transfer "
            "impossible to post twice."
        ),
    ),
)


def _flag(name: str, *, default: bool) -> bool:
    """One environment flag, read as a value rather than as truthiness.

    `"0"`, `"false"` and `"no"` are all off. An unrecognised value is the
    DEFAULT rather than an accident: `ACCOUNTANT_SAFE_MODE=maybe` must not read
    as "off" just because it is not the string "1".
    """
    raw = os.environ.get(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def posting_enabled() -> bool:
    return _flag(ENV_POSTING, default=True)


def safe_mode() -> bool:
    return _flag(ENV_SAFE_MODE, default=True)


def permit_for(op: str, company: str) -> Permit | None:
    """The permit covering this write, or None.

    Company is matched EXACTLY, or by an explicit `"*"`. No case folding and no
    stripping: TallyPrime reports the company name it was created with, and a
    permit that matches loosely is a permit that can authorise a write into a
    company nobody named.
    """
    for permit in ALLOWED_WRITES:
        if permit.op == op and permit.company in (company, "*"):
            return permit
    return None


def allow_write(op: str, company: str, *, confirm: bool = False) -> Permit:
    """Ask the door. Returns the permit, or raises and nothing is sent.

    Three refusals, in the order a reader should think about them:

        1. posting is off entirely           - nothing writes, anywhere
        2. no permit for this op and company - this write was never authorised
        3. destructive, safe mode, no confirm - authorised, but not like this
    """
    if not posting_enabled():
        raise WriteNotAllowedError(
            "POSTING_DISABLED",
            f"writing is switched off ({ENV_POSTING}=0), so {op!r} was not "
            "attempted. Nothing was sent to Tally.",
            company,
        )

    permit = permit_for(op, company)
    if permit is None:
        known = ", ".join(sorted({p.op for p in ALLOWED_WRITES})) or "nothing"
        raise WriteNotAllowedError(
            "NOT_ALLOW_LISTED",
            f"{op!r} is not allow-listed for company {company!r}, so nothing "
            f"was sent. Writes this system may perform: {known}. Adding one "
            "means adding a permit with a written reason to "
            "accountant/tallyio/writedoor.py.",
            company,
        )

    if permit.destructive and safe_mode() and not confirm:
        raise WriteNotAllowedError(
            "CONFIRM_REQUIRED",
            f"{op!r} removes or overwrites, safe mode is on, and the caller "
            "did not pass confirm=True. Nothing was sent.",
            company,
        )

    return permit

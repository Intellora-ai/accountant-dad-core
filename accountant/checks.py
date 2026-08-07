"""Deterministic validations. Boolean functions over a voucher.

"Looks right" is not a check. Every check names itself, and on failure says why
in one line. These feed the Not-valid branch of the decision order.
"""

from __future__ import annotations

from collections.abc import Sequence

from accountant.schema import CheckResult, Voucher


def amount_is_positive(voucher: Voucher, accounts: Sequence[str]) -> CheckResult:
    ok = voucher.amount_paise > 0
    return CheckResult(
        name="amount_is_positive",
        passed=ok,
        detail="" if ok else f"amount is {voucher.amount_paise} paise",
    )


def amount_is_integer_paise(voucher: Voucher, accounts: Sequence[str]) -> CheckResult:
    ok = isinstance(voucher.amount_paise, int) and not isinstance(
        voucher.amount_paise, bool
    )
    return CheckResult(
        name="amount_is_integer_paise",
        passed=ok,
        detail="" if ok else f"amount is {type(voucher.amount_paise).__name__}",
    )


def accounts_differ(voucher: Voucher, accounts: Sequence[str]) -> CheckResult:
    ok = voucher.debit_account != voucher.credit_account
    return CheckResult(
        name="accounts_differ",
        passed=ok,
        detail="" if ok else f"both sides are {voucher.debit_account}",
    )


def accounts_exist(voucher: Voucher, accounts: Sequence[str]) -> CheckResult:
    """Any account that HAS been chosen must exist in this company's Tally chart.

    We never create a ledger the accountant did not create.

    An empty account is deliberately not a failure here. Empty means "memory has
    no match yet", which is Unclear, not Not-valid. Treating it as a failure
    would make Not-valid mask the question we owe the user.
    """
    known = set(accounts)
    chosen = [a for a in (voucher.debit_account, voucher.credit_account) if a]
    missing = [a for a in chosen if a not in known]
    return CheckResult(
        name="accounts_exist",
        passed=not missing,
        detail="" if not missing else f"not in chart of accounts: {', '.join(missing)}",
    )


def gst_not_larger_than_amount(
    voucher: Voucher, accounts: Sequence[str]
) -> CheckResult:
    gst = voucher.gst_paise or 0
    ok = gst <= voucher.amount_paise
    return CheckResult(
        name="gst_not_larger_than_amount",
        passed=ok,
        detail="" if ok else f"GST {gst} paise exceeds total {voucher.amount_paise} paise",
    )


def party_is_named(voucher: Voucher, accounts: Sequence[str]) -> CheckResult:
    ok = bool(voucher.party.strip())
    return CheckResult(
        name="party_is_named",
        passed=ok,
        detail="" if ok else "no party name",
    )


ALL_CHECKS = (
    amount_is_positive,
    amount_is_integer_paise,
    accounts_differ,
    accounts_exist,
    gst_not_larger_than_amount,
    party_is_named,
)


def run(voucher: Voucher, accounts: Sequence[str]) -> list[CheckResult]:
    """Every applicable check, always all of them, so the count is reportable."""
    return [c(voucher, accounts) for c in ALL_CHECKS]

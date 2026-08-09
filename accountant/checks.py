"""Deterministic validations. Boolean functions over a voucher.

"Looks right" is not a check. Every check names itself, and on failure says why
in one line. These feed the Not-valid branch of the decision order.
"""

from __future__ import annotations

from collections.abc import Sequence

from accountant.schema import CheckResult, Voucher


def amount_is_positive(voucher: Voucher, _accounts: Sequence[str]) -> CheckResult:
    ok = voucher.amount_paise > 0
    return CheckResult(
        name="amount_is_positive",
        passed=ok,
        detail="" if ok else f"amount is {voucher.amount_paise} paise",
    )


def amount_is_integer_paise(voucher: Voucher, _accounts: Sequence[str]) -> CheckResult:
    # The annotation says int, so pyright calls this redundant. It is not:
    # bool is a subclass of int, and an untyped caller can pass a float.
    # Money must never be a float, so the runtime check stays.
    amount = voucher.amount_paise
    ok = isinstance(amount, int) and not isinstance(amount, bool)  # pyright: ignore[reportUnnecessaryIsInstance]
    return CheckResult(
        name="amount_is_integer_paise",
        passed=ok,
        detail="" if ok else f"amount is {type(voucher.amount_paise).__name__}",
    )


def accounts_differ(voucher: Voucher, _accounts: Sequence[str]) -> CheckResult:
    """Two NAMED legs may not be the same ledger. Two absent legs are not "same".

    Same reasoning as `accounts_exist` below: an empty leg means nobody has
    chosen yet, which is Unclear and is owned by the vendor lookup (debit) and
    by `funding_is_named` (credit). Before 2026-08-09 both legs were never
    empty at once, because `_default_credit` always filled the credit side, so
    this branch could not be reached. With that deleted, an unknown vendor
    arrives here with two blanks and the old form failed with the sentence
    "both sides are " - a refusal, with no ledger named, in place of the two
    questions we actually owe the person.
    """
    both_named = bool(voucher.debit_account and voucher.credit_account)
    ok = not both_named or voucher.debit_account != voucher.credit_account
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


def funding_is_named(voucher: Voucher, _accounts: Sequence[str]) -> CheckResult:
    """Where the money came FROM has to be known, never assumed.

    Until 2026-08-09 `build_draft` filled this in with `_default_credit`, a
    hard-coded preference list of "Cash", then "Bank", then "Sundry Creditors",
    then whatever sorted first in the chart, then the literal "Cash" EVEN WHEN
    THE COMPANY HAD NO SUCH LEDGER. It ran on every entry, carried no
    provenance, and no test ever asserted it. It stayed invisible because every
    test chart in the repo contains "Cash", so the first loop iteration always
    matched and the later branches never ran once.

    An absence is not a failure to be filled in. It is a question.
    """
    return CheckResult(
        name="funding_is_named",
        passed=bool(voucher.credit_account),
        detail="" if voucher.credit_account else "nothing says how this was paid",
    )


def gst_not_larger_than_amount(
    voucher: Voucher, _accounts: Sequence[str]
) -> CheckResult:
    gst = voucher.gst_paise or 0
    ok = gst <= voucher.amount_paise
    return CheckResult(
        name="gst_not_larger_than_amount",
        passed=ok,
        detail=""
        if ok
        else f"GST {gst} paise exceeds total {voucher.amount_paise} paise",
    )


def party_is_named(voucher: Voucher, _accounts: Sequence[str]) -> CheckResult:
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
    funding_is_named,
    gst_not_larger_than_amount,
    party_is_named,
)


def run(voucher: Voucher, accounts: Sequence[str]) -> list[CheckResult]:
    """Every applicable check, always all of them, so the count is reportable."""
    return [c(voucher, accounts) for c in ALL_CHECKS]

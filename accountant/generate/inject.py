"""Child 1 - the error injector.

Corrupts a controlled fraction of a voucher stream into the four error types
`accountant/detect/detectors.py` already knows, and nothing else. The error
types are not invented here; `ERROR_TYPES` names the four detectors and a test
asserts the two lists have not drifted apart.

| error type      | what it does to a voucher                                  |
|-----------------|------------------------------------------------------------|
| `vendor_switch` | moves a vendor off the one account it is always posted to   |
| `first_use`     | posts to an account with no history in this company         |
| `magnitude`     | pushes the amount past that account's own historical high   |
| `gst_anomaly`   | claims GST on an account that has never carried GST         |

Two rules the whole thing rests on.

**Exact, or refused.** `count_for` returns a whole number of vouchers or raises.
Asking for 5% of 333 vouchers is not a request the injector can honour, so it
says so rather than silently delivering 4.8%.

**Corrupt vouchers still balance.** Every error here is a *classification*
error. If a corrupted voucher failed arithmetic, anyone could find it with a
subtraction and the whole exercise would be pointless.

Ground truth is returned separately and is never written into a voucher field.
The voucher stream carries no id prefix, no narration tag, no provenance key and
no ordering that says which entries were touched.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, replace
from fractions import Fraction

from accountant.generate.book import UNPOSTED_ACCOUNTS, gst_of
from accountant.memory.index import normalise_vendor
from accountant.schema import Voucher

# The four detectors, by name. Order is fixed: it decides how error types are
# shared out, so changing it changes the output for a given seed.
ERROR_TYPES: tuple[str, ...] = (
    "vendor_switch",
    "first_use",
    "magnitude",
    "gst_anomaly",
)

# How far past an account's own historical high a `magnitude` error goes.
MAGNITUDE_FACTORS: tuple[int, ...] = (20, 50, 100)

# The GST rate an injected `gst_anomaly` claims, in basis points.
ANOMALY_GST_BP = 1800


@dataclass(frozen=True)
class Corruption:
    """One injected error. This is the ground truth, and it lives nowhere near
    the voucher stream."""

    voucher_id: str
    error_type: str
    field: str
    was: str
    now: str


@dataclass(frozen=True)
class InjectedBook:
    """A corrupted voucher stream and the separate record of what was done."""

    vouchers: tuple[Voucher, ...]
    truth: tuple[Corruption, ...]
    rate: Fraction
    seed: int


@dataclass(frozen=True)
class _Context:
    """Everything the mutators need to know about the clean stream."""

    posted_accounts: tuple[str, ...]
    vendor_account: dict[str, str]
    account_high: dict[str, int]
    # Membership only. Never iterated, so it cannot leak hash order into output.
    gst_accounts: frozenset[str]


def percent(whole: int) -> Fraction:
    """`percent(5)` is exactly one twentieth. Not 0.05."""
    return Fraction(whole, 100)


def count_for(n: int, rate: Fraction) -> int:
    """How many of `n` vouchers a rate corrupts. Exact, or it raises.

    Rounding here would make the headline number a lie, so a rate that does not
    land on a whole voucher is refused with the numbers that caused it.
    """
    if n < 0:
        raise ValueError(f"a stream cannot have {n} vouchers")
    if not Fraction(0) <= rate <= Fraction(1):
        raise ValueError(f"rate {rate} is not between 0 and 1")
    exact = rate * n
    if exact.denominator != 1:
        raise ValueError(
            f"{rate} of {n} vouchers is {exact} vouchers, which is not a whole "
            f"number; the injector will not round a rate it was asked for exactly"
        )
    return int(exact)


def _context(vouchers: Sequence[Voucher]) -> _Context:
    posted: list[str] = []
    seen_accounts: dict[str, list[str]] = {}
    high: dict[str, int] = {}
    with_gst: list[str] = []

    for v in vouchers:
        if v.debit_account not in high:
            posted.append(v.debit_account)
            high[v.debit_account] = v.amount_paise
        else:
            high[v.debit_account] = max(high[v.debit_account], v.amount_paise)
        if v.gst_paise:
            with_gst.append(v.debit_account)
        key = normalise_vendor(v.party)
        accounts = seen_accounts.setdefault(key, [])
        if v.debit_account not in accounts:
            accounts.append(v.debit_account)

    single = {k: a[0] for k, a in seen_accounts.items() if len(a) == 1}
    return _Context(
        posted_accounts=tuple(sorted(posted)),
        vendor_account=single,
        account_high=high,
        gst_accounts=frozenset(with_gst),
    )


def _switch_targets(voucher: Voucher, ctx: _Context) -> tuple[str, ...]:
    """Accounts this voucher could be wrongly moved to.

    Never the credit side: that would unbalance the voucher, and a corrupted
    voucher that does not balance is findable by arithmetic rather than by a
    detector.
    """
    return tuple(
        a
        for a in ctx.posted_accounts
        if a != voucher.debit_account and a != voucher.credit_account
    )


def _eligible(vouchers: Sequence[Voucher], ctx: _Context) -> dict[str, list[int]]:
    """Which vouchers each error type can actually be applied to, in stream
    order. An error type that cannot fire is not injected as one.

    `magnitude` has no condition: every voucher in the stream contributes its
    own account's high, so every voucher has a bar to be pushed over.
    """
    out: dict[str, list[int]] = {name: [] for name in ERROR_TYPES}
    for i, v in enumerate(vouchers):
        key = normalise_vendor(v.party)
        if ctx.vendor_account.get(key) == v.debit_account and _switch_targets(v, ctx):
            out["vendor_switch"].append(i)
        if v.debit_account not in UNPOSTED_ACCOUNTS:
            out["first_use"].append(i)
        out["magnitude"].append(i)
        if not v.gst_paise and v.debit_account not in ctx.gst_accounts:
            out["gst_anomaly"].append(i)
    return out


def _quotas(count: int) -> tuple[tuple[str, int], ...]:
    """Share `count` errors across the four types as evenly as possible."""
    base, extra = divmod(count, len(ERROR_TYPES))
    return tuple(
        (name, base + (1 if i < extra else 0)) for i, name in enumerate(ERROR_TYPES)
    )


def _apply_vendor_switch(
    rng: random.Random, voucher: Voucher, ctx: _Context
) -> tuple[Voucher, Corruption]:
    now = rng.choice(_switch_targets(voucher, ctx))
    return replace(voucher, debit_account=now), Corruption(
        voucher_id=voucher.id,
        error_type="vendor_switch",
        field="debit_account",
        was=voucher.debit_account,
        now=now,
    )


def _apply_first_use(
    rng: random.Random, voucher: Voucher, _ctx: _Context
) -> tuple[Voucher, Corruption]:
    now = rng.choice(UNPOSTED_ACCOUNTS)
    return replace(voucher, debit_account=now), Corruption(
        voucher_id=voucher.id,
        error_type="first_use",
        field="debit_account",
        was=voucher.debit_account,
        now=now,
    )


def _apply_magnitude(
    rng: random.Random, voucher: Voucher, ctx: _Context
) -> tuple[Voucher, Corruption]:
    factor = rng.choice(MAGNITUDE_FACTORS)
    amount = ctx.account_high[voucher.debit_account] * factor
    gst = voucher.gst_paise
    scaled = None if gst is None else amount * gst // voucher.amount_paise
    return replace(voucher, amount_paise=amount, gst_paise=scaled), Corruption(
        voucher_id=voucher.id,
        error_type="magnitude",
        field="amount_paise",
        was=str(voucher.amount_paise),
        now=str(amount),
    )


def _apply_gst_anomaly(
    _rng: random.Random, voucher: Voucher, _ctx: _Context
) -> tuple[Voucher, Corruption]:
    gst = gst_of(voucher.amount_paise, ANOMALY_GST_BP)
    return replace(voucher, gst_paise=gst), Corruption(
        voucher_id=voucher.id,
        error_type="gst_anomaly",
        field="gst_paise",
        was="none",
        now=str(gst),
    )


_APPLY = {
    "vendor_switch": _apply_vendor_switch,
    "first_use": _apply_first_use,
    "magnitude": _apply_magnitude,
    "gst_anomaly": _apply_gst_anomaly,
}


def inject(vouchers: Sequence[Voucher], *, rate: Fraction, seed: int) -> InjectedBook:
    """Corrupt exactly `rate` of `vouchers`, and say exactly which.

    Deterministic: one seeded `random.Random`, candidates drawn from lists in
    stream order, mutations applied in ascending stream position.
    """
    count = count_for(len(vouchers), rate)
    # Reproducibility is the requirement; cryptographic strength is not, and
    # would defeat it. This picks fixture rows, never anything secret.
    rng = random.Random(seed)  # noqa: S311  # nosec B311
    ctx = _context(vouchers)
    eligible = _eligible(vouchers, ctx)

    chosen: dict[int, str] = {}
    for name, quota in _quotas(count):
        pool = [i for i in eligible[name] if i not in chosen]
        if len(pool) < quota:
            raise ValueError(
                f"{name} needs {quota} eligible vouchers, found {len(pool)} in a "
                f"stream of {len(vouchers)}"
            )
        for i in rng.sample(pool, quota):
            chosen[i] = name

    out = list(vouchers)
    truth: list[Corruption] = []
    for i in sorted(chosen):
        corrupted, record = _APPLY[chosen[i]](rng, out[i], ctx)
        out[i] = corrupted
        truth.append(record)

    return InjectedBook(vouchers=tuple(out), truth=tuple(truth), rate=rate, seed=seed)

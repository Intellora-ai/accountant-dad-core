"""Where a missing ledger belongs, read from the company's own chart of accounts.

WHY THIS EXISTS
---------------
A voucher names at least two ledgers. Until 2026-08-13 both backends REFUSED
the write when either was absent - `real.py::RealTally._check_ledgers_exist`
and the deliberately-matched copy in `fake.py::FakeTally.write_voucher`. That
is safe and it is also a dead end: the measured company `TANVEER SIDHU` answers
`<LEDGER>0</LEDGER>` (`masters.py:5-9`), so every voucher for it was refused
for ever and no amount of retrying changed that.

Creating the ledger instead needs one thing the refusal never had: a GROUP.

WHY THE GROUP IS NOT A DETAIL, AND WHY A PLAUSIBLE GUESS IS NOT GOOD ENOUGH
---------------------------------------------------------------------------
`docs/RUNBOOK_PHASE5_ACCEPTANCE.md:180-186`, verbatim:

    `Sharma Traders` under Sundry Creditors -> a liability -> balance is a credit.
    `Sharma Traders` under Sundry Debtors   -> an asset     -> balance is a debit.

Same name, opposite sign, for ever. And the acceptance run's own trial-balance
comparison DOES NOT CATCH IT: the comparison is before-and-after on the same
company, and a wrong group moves the amount to the wrong side of the report a
human reads, not the difference the comparison measures. So there is no later
check that would find this. The derivation is the only guard, which is why it
refuses rather than approximates.

THE RULE
--------
Read the group out of the company's own chart. Never out of a table written
here.

    1. The ledger is already in the chart, spelled exactly.
       Its PARENT is the answer. Nothing is created and nothing is inferred.

    2. It is not there, but a ledger differing only in case is.
       REFUSED. Creating "sharma traders" beside "Sharma Traders" splits one
       balance into two and neither is wrong-looking on its own.

    3. It is genuinely absent.
       The account's ROLE IN THIS VOUCHER says which comparison to make, and
       the answer must be a group THIS COMPANY ALREADY USES:

         the leg equal to `voucher.party`   -> a party ledger. The side it
                                               sits on AND what the other leg
                                               is decide creditor or debtor.
         the non-party CREDIT leg           -> where the money came from, so a
                                               money group.
         the non-party DEBIT leg            -> REFUSED. See below.

    4. The chart cannot answer.
       REFUSED, with the reason in a plain sentence. An empty company has
       nothing to learn from, and there is no fallback group: a wrong group is
       wrong in somebody's books permanently and cannot be un-posted.

THE SIDE ALONE DOES NOT SAY CREDITOR OR DEBTOR
-----------------------------------------------
A supplier is CREDITED on a bill and DEBITED when we pay them. So "party on
the debit side is a debtor" is right for a Sales invoice and wrong for a
Payment - and wrong in exactly the direction nothing catches.

The other leg settles it, and the other leg's group comes out of the same
chart:

    party credited, other leg is Purchases     a bill      -> Creditors
    party debited,  other leg is Sales         an invoice  -> Debtors
    party debited,  other leg is the bank      a payment   -> Creditors
    party credited, other leg is the bank      a receipt   -> Debtors

So the party leg is only placeable when the OTHER leg is already in the chart
and sits in a group this module recognises. Two unknowns is a refusal.

WHAT IS WRITTEN DOWN HERE AND WHAT IS NOT
------------------------------------------
`PARTY_GROUP_FOR_SIDE`, `MONEY_GROUPS`, `INCOME_GROUPS` and `TRADE_GROUPS` are
Tally's OWN group names carrying Tally's OWN accounting meaning. They are not a
mapping from a ledger name to a group, and they are not a per-company table:
nothing here can produce a group the company's chart does not already contain,
because every branch intersects with the groups actually in use. Delete the
company's ledgers and this module answers nothing at all - which is the
measured `TANVEER SIDHU` case and the correct answer for it.

The names are spelled to match `masters.KNOWN_GROUPS` exactly.
`tests/test_ledger_placement.py` asserts that they are a subset of it, because
this module cannot import `masters` - `masters` imports `real`, and `real`
imports this.

THE NON-PARTY DEBIT LEG IS REFUSED ON PURPOSE
----------------------------------------------
A chart cannot say whether a new name on the debit side is a purchase, a direct
expense, an indirect expense or a fixed asset. Those four put the same amount
in four different places: three of them change this year's profit and the
fourth does not. There is no evidence in a list of ledgers and parents that
tells them apart, so this refuses and names the person who has to decide.

WHAT THIS FILE DOES NOT PROVE
------------------------------
That any of it has run against a licensed TallyPrime. Everything here is
FAKETALLY and SIMULATOR evidence.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from accountant.schema import Voucher

# ---------------------------------------------------------------------------
# Tally's own vocabulary
# ---------------------------------------------------------------------------

#: Which party group a ledger belongs in, given the side it is posted on AND
#: what the entry did. THE SIDE ALONE IS NOT ENOUGH, and believing it was is a
#: defect this table exists to record:
#:
#:     Purchase  debit Purchases, credit the supplier   party on CREDIT
#:     Payment   debit the supplier, credit the bank    party on DEBIT
#:
#: Same supplier, same `Sundry Creditors`, opposite sides. "Party on the debit
#: side is a debtor" is right for a Sales invoice and WRONG for a Payment, and
#: it is wrong in the direction `docs/RUNBOOK_PHASE5_ACCEPTANCE.md:180-186`
#: says nothing catches.
#:
#: What tells them apart is the OTHER leg, read from the company's own chart.
#: Money moved (the counter leg is in a money group) means a settlement, and a
#: settlement posts the party on the side OPPOSITE its balance. Money did not
#: move (the counter leg is what the entry was for, or what it earned) means
#: the original bill or invoice, and the party sits on the side its balance
#: lives on.
PARTY_GROUP_FOR_SIDE: Final[dict[tuple[str, bool], str]] = {
    # (side the party is posted on, the other leg is money)
    ("credit", False): "Sundry Creditors",
    ("debit", False): "Sundry Debtors",
    ("credit", True): "Sundry Debtors",
    ("debit", True): "Sundry Creditors",
}

#: The groups a company's own money sits in. Two jobs: a credit leg that is not
#: the party is where the money came FROM, and a counter leg sitting here is
#: what makes an entry a settlement rather than a bill.
MONEY_GROUPS: Final[frozenset[str]] = frozenset({"Bank Accounts", "Cash-in-Hand"})

#: Income groups. Their presence in a chart is a DISQUALIFIER for the funding
#: rule, never an answer: a company that sells things posts sales on the credit
#: side too, so "the credit leg that is not the party is money" stops being
#: true and this refuses instead of filing a Sales ledger under a bank.
INCOME_GROUPS: Final[frozenset[str]] = frozenset(
    {"Sales Accounts", "Direct Incomes", "Indirect Incomes"}
)

#: What the counter leg of a BILL or an INVOICE sits in: what the money was
#: for, or what it earned. Recognising this - rather than treating "not money"
#: as bill - is what makes an unfamiliar group a refusal instead of an
#: assumption. A counter leg under `Current Assets` could be a deposit, a loan
#: or a transfer, and those put the party on different sides.
TRADE_GROUPS: Final[frozenset[str]] = (
    frozenset(
        {
            "Purchase Accounts",
            "Direct Expenses",
            "Indirect Expenses",
            "Fixed Assets",
        }
    )
    | INCOME_GROUPS
)


# ---------------------------------------------------------------------------
# what a chart is
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Placement:
    """One ledger as the company's chart holds it. `parent` is its Tally group.

    Structurally the same three facts as `masters.MasterSummary`, restated here
    because `masters` imports `real` and `real` imports this. One dataclass in
    the module every side can import beats an import cycle or a duplicated
    parser.
    """

    name: str
    parent: str = ""


@dataclass(frozen=True)
class Derived:
    """Where one ledger belongs, or why that could not be worked out.

    `existing` and `group` are separate on purpose. `existing=True` means the
    chart already holds this name and NOTHING is to be created; `group` is then
    only what the chart reported, which may be blank if the build did not send
    a PARENT. A caller that collapsed the two would create a duplicate ledger
    for every company whose gateway omits the parent element.
    """

    group: str = ""
    existing: bool = False
    refusal: str = ""


class LedgerBook(Protocol):
    """The two things a backend must be able to do for a ledger to be placed.

    Deliberately tiny. `RealTally` implements it over XML and `FakeTally` over a
    dict, and `place_ledgers` below is then the SAME code for both - which is
    the only arrangement in which "both backends behave identically" is a fact
    rather than two copies that happen to agree today. `schema.py:115-132`
    records what happens when a rule is mirrored in two files instead: the two
    halves drifted and the application called a bill VALID that the connector
    then refused.
    """

    def chart(self) -> tuple[Placement, ...]:
        """Every ledger this company has, with its group."""
        ...

    def create(self, name: str, group: str) -> str:
        """Create one ledger. Returns "" on a CONFIRMED success, else why not.

        A create Tally accepted but whose read-back could not find the ledger
        is NOT a success and must come back as a sentence.
        """
        ...


# ---------------------------------------------------------------------------
# the derivation
# ---------------------------------------------------------------------------


def _groups_in_use(chart: Sequence[Placement]) -> set[str]:
    """Every group this company actually puts a ledger under."""
    return {entry.parent.strip() for entry in chart if entry.parent.strip()}


def _party_side(voucher: Voucher, account: str) -> str:
    """ "credit", "debit", or "" when this account is not the party's own leg.

    Exact comparison, and exactly ONE side. A voucher whose party name is on
    both legs names no side: `real.check_writable` refuses one ledger on both
    legs anyway, and answering "credit" for it here would be a guess dressed as
    a reading.
    """
    if not account or account != voucher.party:
        return ""
    on_credit = account == voucher.credit_account
    on_debit = account == voucher.debit_account
    if on_credit and not on_debit:
        return "credit"
    if on_debit and not on_credit:
        return "debit"
    return ""


def derive_group(chart: Sequence[Placement], voucher: Voucher, account: str) -> Derived:
    """Where `account` belongs in THIS company's chart. See the module docstring.

    Pure. It opens no socket, creates nothing and has no opinion about what the
    caller does next, so every branch below is reachable from a plain list of
    (name, parent) pairs in a test.
    """
    if not account.strip():
        return Derived(
            refusal=(
                "a voucher leg with no ledger name cannot be placed anywhere; "
                "there is nothing to create and nothing to look up"
            )
        )

    for entry in chart:
        if entry.name == account:
            return Derived(group=entry.parent.strip(), existing=True)

    # Case is the accident that happens: an extractor or a copy-paste turns
    # "Sharma Traders" into "sharma traders", and the two ledgers then hold half
    # a balance each with neither looking wrong. Refused, never resolved to the
    # near match - picking the other spelling would post to a ledger nobody
    # named.
    twins = sorted(e.name for e in chart if e.name.casefold() == account.casefold())
    if twins:
        return Derived(
            refusal=(
                f"{account!r} is not in the chart, but {', '.join(map(repr, twins))} "
                f"is, and they differ only in case. Creating {account!r} beside it "
                "would split one balance across two ledgers and neither would look "
                "wrong on its own. Nothing was created: use the spelling the "
                "company already has, or create the second ledger in Tally by hand "
                "if it really is a different one"
            )
        )

    in_use = _groups_in_use(chart)
    if not in_use:
        return Derived(
            refusal=(
                f"{account!r} is not in the chart, and this company's chart has no "
                "ledgers under any group to read a group from - so there is nothing "
                "to learn where it belongs from. A group chosen without evidence is "
                "wrong in the books permanently and cannot be un-posted. Create "
                f"{account!r} in Tally, under the group you want it in"
            )
        )

    if side := _party_side(voucher, account):
        counter = voucher.debit_account if side == "credit" else voucher.credit_account
        counter_group = next((e.parent.strip() for e in chart if e.name == counter), "")
        if counter_group not in MONEY_GROUPS and counter_group not in TRADE_GROUPS:
            return Derived(
                refusal=(
                    f"{account!r} is this voucher's party on the {side} side, and "
                    "which side a party sits on does NOT by itself say whether it "
                    "is somebody we owe or somebody who owes us - a supplier is "
                    "credited on a bill and DEBITED when we pay them. The other "
                    "leg is what tells them apart, and this chart "
                    + (
                        f"does not hold {counter!r} at all"
                        if not counter_group
                        else f"puts {counter!r} under {counter_group!r}, which is "
                        "not a group this code recognises as money or as trade"
                    )
                    + f". Create {account!r} in Tally, under the group you want it in"
                )
            )
        settlement = counter_group in MONEY_GROUPS
        wanted = PARTY_GROUP_FOR_SIDE[side, settlement]
        if wanted in in_use:
            return Derived(group=wanted)
        return Derived(
            refusal=(
                f"{account!r} is this voucher's party on the {side} side and the "
                f"other leg {counter!r} is "
                + ("money, so this settles" if settlement else "what the entry was")
                + f" for - which makes {account!r} one of this company's "
                f"{wanted!r}. But the chart has no ledger under {wanted!r} at all, "
                "so that group cannot be read out of your own books. Groups in "
                f"use: {', '.join(sorted(in_use))}. Create {account!r} in Tally "
                "first"
            )
        )

    if account == voucher.credit_account:
        income = sorted(in_use & INCOME_GROUPS)
        if income:
            return Derived(
                refusal=(
                    f"{account!r} is the credit leg and is not the party, which "
                    "would normally make it where the money came from - but this "
                    f"chart also holds income ledgers ({', '.join(income)}), so a "
                    "credit leg that is not the party may be income instead of "
                    "money. Those go in opposite halves of the accounts and the "
                    f"chart cannot tell them apart. Create {account!r} in Tally"
                )
            )
        money = sorted(in_use & MONEY_GROUPS)
        if len(money) == 1:
            return Derived(group=money[0])
        return Derived(
            refusal=(
                f"{account!r} is the credit leg and is not the party, so it is "
                "where the money came from - but "
                + (
                    "this chart has no ledger under a money group at all"
                    if not money
                    else f"this chart uses more than one money group "
                    f"({', '.join(money)}) and nothing here says which of them "
                    f"{account!r} belongs in"
                )
                + f". Groups in use: {', '.join(sorted(in_use))}. Create "
                f"{account!r} in Tally, under the group you want it in"
            )
        )

    return Derived(
        refusal=(
            f"{account!r} is the debit leg and is not the party. A chart of "
            "accounts cannot say whether a new name on that side is a purchase, "
            "a direct expense, an indirect expense or a fixed asset, and those "
            "four put the same amount in four different places - three change "
            "this year's profit and the fourth does not. That is a decision for "
            f"a person. Create {account!r} in Tally, under the group you want it "
            "in"
        )
    )


# ---------------------------------------------------------------------------
# the seam both backends use
# ---------------------------------------------------------------------------

#: The opening of every refusal, unchanged since the write path first had one.
#: `tests/test_contract_differences.py::
#: test_the_missing_ledger_refusal_opens_with_the_same_sentence_on_both_backends`
#: asserts both backends START with exactly this, so it is built in one place.
_OPENING: Final = (
    "refusing to write operation {op!r} to {company!r}: the ledger(s) "
    "{names} do not exist there"
)


def _refusal(company: str, operation_id: str, missing: Sequence[str], why: str) -> str:
    return (
        _OPENING.format(
            op=operation_id,
            company=company,
            names=", ".join(repr(name) for name in missing),
        )
        + f". {why} Nothing was written."
    )


def place_ledgers(
    book: LedgerBook, company: str, voucher: Voucher, operation_id: str
) -> str:
    """Make sure both of this voucher's legs name a ledger the company has.

    Returns "" when they do - creating any that were missing, under a group
    read from the company's own chart - and the refusal sentence when they do
    not.

    A STRING RATHER THAN AN EXCEPTION, deliberately. The exception class lives
    in `real.py`, `real.py` imports this module, and an exception defined here
    would either invert that or need a third module. Both backends do the same
    two lines with the answer, so the class and the wording stay identical
    without either file restating the rule.

    EVERY MISSING LEDGER IS DERIVED BEFORE ANY IS CREATED. A voucher naming two
    absent ledgers where only one is derivable must not leave the derivable one
    created and the voucher refused: that is a master added to somebody's books
    for a write that never happened, and nothing here can remove it - the write
    door permits `create_ledger` and nothing that deletes one. Two passes, and
    the first one sends nothing.

    AT MOST ONE LEDGER IS EVER CREATED PER VOUCHER TODAY, and that falls out of
    the rules rather than being enforced here: the party leg is only derivable
    when the OTHER leg is already in the chart, and the only other derivable
    role is the non-party credit leg. So the two cannot both be missing and
    both be placeable. The loop is written for the general case anyway, because
    the day a third role becomes derivable this stays correct instead of
    silently creating one of a pair.
    """
    legs: list[str] = []
    for leg in (voucher.debit_account, voucher.credit_account):
        if leg not in legs:
            legs.append(leg)

    chart = book.chart()

    plans: list[tuple[str, Derived]] = []
    for leg in legs:
        placed = derive_group(chart, voucher, leg)
        if not placed.existing:
            plans.append((leg, placed))

    if not plans:
        return ""

    missing = [name for name, _ in plans]

    if blocked := [(name, p.refusal) for name, p in plans if not p.group]:
        return _refusal(
            company,
            operation_id,
            missing,
            " ".join(f"{reason}." for _, reason in blocked),
        )

    failures: list[str] = []
    for name, placed in plans:
        if problem := book.create(name, placed.group):
            failures.append(
                f"{name!r} could not be created under {placed.group!r}: {problem}"
            )

    if failures:
        return _refusal(
            company,
            operation_id,
            missing,
            " ".join(f"{failure}." for failure in failures),
        )

    return ""

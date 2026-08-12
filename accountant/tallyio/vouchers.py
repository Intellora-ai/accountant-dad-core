"""Vouchers posted straight to TallyPrime, through the write door.

WHAT THIS IS, AND WHAT IT IS NOT
---------------------------------
This is the MVP write path: a supplier bill in, a posted Purchase voucher out,
confirmed by reading it back. It is NOT `pipeline.post`.

`pipeline.post` is the door for a voucher the PRODUCT decided to post: it
enforces the Valid-outcome gate, binds the decision to an operation id, writes
an audit row before the attempt, reads the voucher back field by field and
compares the register. Everything here is a caller who already knows what it
wants posted - a CSV row, a script, an operator - so there is no extraction to
judge and no decision to bind.

That difference is exactly what `ci/educational_slice.py:233` got wrong, so this
module does not pretend the difference is not there. It asks
`writedoor.allow_write` before anything is sent, it reads the voucher back
before reporting success, and `tests/test_write_door.py` proves at test time
that no third path exists.

`STATUS 1` IS NOT CONFIRMATION
------------------------------
TallyPrime answers HTTP 200 with `STATUS 1` for vouchers it then declines, and
puts the complaint in `LINEERROR`. Worse, it answers `CREATED 1` for imports
that leave nothing behind. So success here means three things together: no
classified error, a non-zero `CREATED`, and the voucher found again by its own
operation id afterwards.

MONEY IS INTEGER PAISE
-----------------------
`amount_paise: int`, never float rupees. `accountant/extract/adapter.py` carries
the measured case where `round(float(text) * 100)` put a number one paise adrift
into a record. A voucher amount is the number a customer's supplier gets paid.

SIX TYPES, ONE BUILDER
-----------------------
Purchase, Sales, Payment, Receipt, Journal and Contra differ in exactly three
things: the voucher type name, which ledger is debited and which is credited,
and whether there is an outside party at all. The envelope, the date format,
the sign convention, the door, the audit row and the read-back are identical,
so they are written once - `_voucher_xml` and `Vouchers._post` - and the six
public methods are the three differences and nothing else.

Six near-identical builders would be six places for the sign convention to
drift, and a Sales voucher carrying Purchase's legs does not crash. It is a
customer recorded as a supplier, in somebody's real books, silently.

DEBITS EQUAL CREDITS, CHECKED BEFORE THE ENVELOPE IS BUILT
-----------------------------------------------------------
`_balance_problem` totals both sides in paise and refuses `UNBALANCED` naming
both totals. Every voucher built here comes from ONE amount, so today the two
sides cannot differ - which is precisely why the check is cheap to keep and
worth keeping. The first tax line, discount leg or rounding adjustment added to
this module is the change that makes an unbalanced voucher possible, and
TallyPrime's own refusal for one does not name the two totals.

THE SAME VOUCHER TWICE IS THE WORST THING THIS MODULE CAN DO
--------------------------------------------------------------
Measured 2026-08-12: `mvp_real_tally.py` was run twice and the live company
`TANVEER SIDHU` ended up with two identical Purchase vouchers - same date, same
party, same Rs 1,000.00, same narration. Nothing stopped it, because nothing
was asking.

The connector solved this years earlier and this module simply had not adopted
it: `accountant/tallyio/client.py` stamps a unique operation id into the
narration, `real.RealTally.write_voucher` refuses a repeated one, and
`read_by_operation_id` finds a voucher by identity rather than by amount. So
every method here now takes a REQUIRED `operation_id`, asks TallyPrime whether
one already carries it, and returns `already_posted=True` having sent nothing
when the answer is yes.

The check FAILS CLOSED. If the question cannot be answered - the gateway is
down, the response is unreadable - nothing is posted and the failure says so. A
retry that cannot be distinguished from a first attempt is exactly how the two
duplicates got there, and an entry that was never written is recoverable in a
way an entry written twice into somebody's statutory books is not.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final
from xml.sax.saxutils import escape

from accountant.tallyio import audit, errors, writedoor
from accountant.tallyio.client import operation_id_in, stamp
from accountant.tallyio.masters import Masters, Problem, Validation
from accountant.tallyio.real import (
    HttpTransport,
    TallyConfig,
    TallyResponseError,
    Transport,
    parse_xml,
)

if TYPE_CHECKING:
    from accountant.tallyio.reports import Entry

#: The ledger a purchase is debited to when the caller does not name one.
PURCHASE_LEDGER: Final = "Purchase"

#: The ledger a sale is credited to when the caller does not name one.
SALES_LEDGER: Final = "Sales"

#: The account money leaves, or arrives in, when the caller does not name one.
#:
#: "Cash" is TallyPrime's own default and the one account a company has from the
#: moment it is created. A bank is never defaulted to: its ledger name is the
#: customer's own spelling, and guessing it would post real money to a ledger
#: nobody named.
CASH_ACCOUNT: Final = "Cash"

#: Tally's own date format in XML. NOT the DDMMYYYY a person types.
#:
#: `<DATE>20260812</DATE>`. Handing Tally "12082026" is read as year 1208,
#: month 20, and is refused with a message that does not mention the format.
_TALLY_DATE: Final = "%Y%m%d"

#: The range the duplicate check asks for: every voucher the company has.
#:
#: Not an optimisation problem. `reports.Reports` fetches the company's vouchers
#: and filters them in Python - its own docstring says so - so a narrow range
#: costs exactly what a wide one costs and buys a way to MISS a duplicate. An
#: operation id is unique across the company, not across one day; a caller that
#: retries with the same id and a corrected date would otherwise be told the
#: entry is not there, and would post it a second time.
_ALL_TIME: Final = (datetime.date.min, datetime.date.max)


@dataclass(frozen=True)
class VoucherResult:
    """What happened. `confirmed` and `already_posted` are separate from
    `success`, deliberately.

    `success` means the voucher is in the books as a result of this call or was
    already there. `confirmed` means it was found again by its own operation id
    afterwards. `already_posted` means it was there BEFORE this call and nothing
    was sent.

    Three facts rather than one flag, because collapsing them is how both of
    this repository's measured write defects went unnoticed: a write the gateway
    accepts and does not keep reads as a success, and a retry that posts a
    second copy reads as a success too.
    """

    success: bool
    confirmed: bool = False
    already_posted: bool = False
    voucher_number: str = ""
    raw_xml: str = ""
    request_xml: str = ""
    would_send_xml: str = ""
    summary: str = ""
    error: errors.TallyError | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "confirmed": self.confirmed,
            "already_posted": self.already_posted,
            "voucher_number": self.voucher_number,
            "summary": self.summary,
            "error": self.error.as_dict() if self.error else None,
        }


@dataclass(frozen=True)
class Leg:
    """One side of a voucher: a ledger, and the paise on ONE side of it.

    Two fields rather than one signed integer, on purpose. Tally's sign
    convention is not a bookkeeper's - a DEBIT reaches the gateway as a NEGATIVE
    `<AMOUNT>` - so a single signed number would mean every function that
    touched a leg had to remember which convention it was currently in. Here the
    two sides are named, and the one place a sign is inverted is `_leg_xml`.
    """

    ledger: str
    debit_paise: int = 0
    credit_paise: int = 0


def _dr(ledger: str, paise: int) -> Leg:
    """The side that RECEIVES value. Reads as it does on paper."""
    return Leg(ledger, debit_paise=paise)


def _cr(ledger: str, paise: int) -> Leg:
    """The side that GIVES value."""
    return Leg(ledger, credit_paise=paise)


def parse_ddmmyyyy(text: str) -> tuple[datetime.date | None, Problem | None]:
    """`"12082026"` -> a date. The one place the format is interpreted."""
    if not re.fullmatch(r"\d{8}", text or ""):
        return None, Problem(
            "DATE_MALFORMED",
            f"a date is eight digits, DDMMYYYY, for example 12082026; got {text!r}",
            text,
        )
    try:
        return datetime.date(int(text[4:]), int(text[2:4]), int(text[:2])), None
    except ValueError as wrong:
        return None, Problem(
            "DATE_IMPOSSIBLE", f"{text!r} is not a real date: {wrong}", text
        )


def _rupees(amount_paise: int) -> str:
    """Paise -> the rupee string Tally wants. Integer arithmetic, never round().

    `100_000` -> `"1000.00"`. `1` -> `"0.01"`. Floor division and a remainder,
    so no float ever exists on the path between a validated amount and the wire.
    """
    return f"{amount_paise // 100}.{amount_paise % 100:02d}"


def _leg_xml(leg: Leg) -> str:
    """One `ALLLEDGERENTRIES.LIST`. The only place a sign is inverted.

    TALLY'S CONVENTION, which is not the one a person expects:

        DEBIT  -> `ISDEEMEDPOSITIVE` Yes, and a NEGATIVE `AMOUNT`
        CREDIT -> `ISDEEMEDPOSITIVE` No,  and a POSITIVE `AMOUNT`

    Both halves have to agree. Tally reads the pair, and a leg with the flag of
    a debit and the sign of a credit is accepted and stored the wrong way round.
    """
    if leg.debit_paise:
        return (
            "<ALLLEDGERENTRIES.LIST>"
            f"<LEDGERNAME>{escape(leg.ledger)}</LEDGERNAME>"
            "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>"
            f"<AMOUNT>-{_rupees(leg.debit_paise)}</AMOUNT>"
            "</ALLLEDGERENTRIES.LIST>"
        )
    return (
        "<ALLLEDGERENTRIES.LIST>"
        f"<LEDGERNAME>{escape(leg.ledger)}</LEDGERNAME>"
        "<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>"
        f"<AMOUNT>{_rupees(leg.credit_paise)}</AMOUNT>"
        "</ALLLEDGERENTRIES.LIST>"
    )


def _voucher_xml(
    company: str,
    vchtype: str,
    when: datetime.date,
    legs: Sequence[Leg],
    *,
    party: str = "",
    narration: str = "",
) -> str:
    """The Import envelope for any one voucher. Six types, one builder.

    `party` is OMITTED rather than sent empty when there is none. A Contra and a
    Journal have no outside party, and `<PARTYLEDGERNAME></PARTYLEDGERNAME>` does
    not mean "no party" to TallyPrime - it names a party whose ledger is the
    empty string, which is a ledger that does not exist.

    `vchtype` reaches an XML attribute, and is never caller-supplied: the six
    public methods each hand in their own literal. A caller-chosen voucher type
    would need the attribute-quote escaping that `escape` does not do by
    default.
    """
    stamped = when.strftime(_TALLY_DATE)
    party_element = (
        f"<PARTYLEDGERNAME>{escape(party)}</PARTYLEDGERNAME>" if party else ""
    )
    return (
        "<ENVELOPE>"
        "<HEADER>"
        "<VERSION>1</VERSION>"
        "<TALLYREQUEST>Import</TALLYREQUEST>"
        "<TYPE>Data</TYPE>"
        "<ID>Vouchers</ID>"
        "</HEADER>"
        "<BODY><DESC><STATICVARIABLES>"
        f"<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>"
        "</STATICVARIABLES></DESC>"
        '<DATA><TALLYMESSAGE xmlns:UDF="TallyUDF">'
        f'<VOUCHER VCHTYPE="{escape(vchtype)}" ACTION="Create" '
        'OBJVIEW="Accounting Voucher View">'
        f"<DATE>{stamped}</DATE>"
        f"<EFFECTIVEDATE>{stamped}</EFFECTIVEDATE>"
        f"<VOUCHERTYPENAME>{escape(vchtype)}</VOUCHERTYPENAME>"
        f"{party_element}"
        f"<NARRATION>{escape(narration)}</NARRATION>"
        f"{''.join(_leg_xml(leg) for leg in legs)}"
        "</VOUCHER>"
        "</TALLYMESSAGE></DATA>"
        "</BODY></ENVELOPE>"
    )


def purchase_xml(
    company: str,
    when: datetime.date,
    party: str,
    amount_paise: int,
    *,
    purchase_ledger: str = PURCHASE_LEDGER,
    narration: str = "",
) -> str:
    """The exact Import XML for one Purchase. Public so a dry run can show it.

    DOUBLE ENTRY: Purchase A/c Dr, Party Cr. Standard for a credit purchase -
    the expense rises and the supplier is owed. In Tally's sign convention a
    DEBIT carries a negative AMOUNT with ISDEEMEDPOSITIVE Yes, and a CREDIT
    carries a positive AMOUNT with ISDEEMEDPOSITIVE No.

    Kept public with this exact signature because callers outside this module
    have it. It is now a two-line call onto `_voucher_xml`, and produces the
    same bytes it always did.
    """
    return _voucher_xml(
        company,
        "Purchase",
        when,
        (_dr(purchase_ledger, amount_paise), _cr(party, amount_paise)),
        party=party,
        narration=narration,
    )


def _balance_problem(legs: Sequence[Leg]) -> Problem | None:
    """Debits must equal credits, in paise, exactly. Or nothing is sent.

    Names BOTH totals and their difference, because "unbalanced" on its own
    sends a person to read the whole voucher. TallyPrime's own refusal for an
    unbalanced import does not name them.

    Integer paise, so this is an exact comparison rather than a tolerance. A
    tolerance here would be a decision that some amount of missing money is
    acceptable, which is not a decision this module gets to make.
    """
    debits = sum(leg.debit_paise for leg in legs)
    credits = sum(leg.credit_paise for leg in legs)
    if debits == credits:
        return None
    return Problem(
        "UNBALANCED",
        f"debits total {debits} paise and credits total {credits} paise, a "
        f"difference of {abs(debits - credits)}; the two sides of a voucher are "
        "equal or it is not a voucher, so nothing was sent",
    )


def _validate(
    date_ddmmyyyy: str,
    amount_paise: int,
    names: Sequence[tuple[str, str, str]],
    *,
    distinct: tuple[str, str] | None = None,
) -> Validation:
    """Everything checkable without asking Tally. Also the dry run.

    `names` is (code, sentence, value) per ledger the type requires, in the
    order a person would fill them in, so the FIRST problem reported is the
    first thing wrong rather than an arbitrary one.

    `distinct` is the two sides of a Journal or a Contra. They must differ:
    debiting and crediting one ledger balances perfectly and means nothing, so
    the arithmetic check cannot catch it. Compared case-folded because Tally
    treats "Cash" and "cash" as one ledger and would post the pair happily.
    """
    problems: list[Problem] = []

    _, date_problem = parse_ddmmyyyy(date_ddmmyyyy)
    if date_problem:
        problems.append(date_problem)

    for code, said, value in names:
        if not value.strip():
            problems.append(Problem(code, said))

    if distinct is not None and distinct[0].strip().casefold() == (
        distinct[1].strip().casefold()
    ):
        problems.append(
            Problem(
                "SAME_LEDGER",
                f"both sides name {distinct[0]!r}; an entry that debits and "
                "credits one ledger balances and records nothing",
                distinct[0],
            )
        )

    if type(amount_paise) is not int:  # pyright: ignore[reportUnnecessaryIsInstance]
        # Annotations are not enforced. A CSV row or an LLM tool-call arrives
        # as 1000.0, and float rupees are how a rounded number reaches a ledger.
        problems.append(
            Problem(
                "AMOUNT_NOT_PAISE",
                f"an amount is whole paise as an int; got "
                f"{type(amount_paise).__name__}",
            )
        )
    elif amount_paise <= 0:
        problems.append(
            Problem("AMOUNT_NOT_POSITIVE", f"{amount_paise} paise is not an amount")
        )

    return Validation(ok=not problems, problems=problems)


def validate_purchase(date_ddmmyyyy: str, party: str, amount_paise: int) -> Validation:
    """Everything checkable without asking Tally. Also the dry run."""
    return _validate(
        date_ddmmyyyy,
        amount_paise,
        (("PARTY_EMPTY", "a purchase needs a supplier", party),),
    )


@dataclass
class Vouchers:
    """Post vouchers into one company, through the write door.

    Six types, one path. Every method is the same six lines: name the type, name
    the two legs in the order accounting says, hand over the checks. The path
    itself is `_post`.
    """

    company: str
    transport: Transport = field(default_factory=lambda: HttpTransport(TallyConfig()))
    log: audit.JsonLineAuditLogger = field(default_factory=audit.JsonLineAuditLogger)

    def create_purchase_voucher(
        self,
        date_ddmmyyyy: str,
        party: str,
        amount_paise: int,
        *,
        operation_id: str,
        narration: str = "",
        purchase_ledger: str = PURCHASE_LEDGER,
        dry_run: bool = False,
    ) -> VoucherResult:
        """Post one Purchase. Purchase Dr, Party Cr.

        DOUBLE ENTRY: the expense has been incurred, so Purchase is debited, and
        the supplier is now owed, so the supplier is credited.
        """
        return self._post(
            op="create_purchase_voucher",
            vchtype="Purchase",
            date_ddmmyyyy=date_ddmmyyyy,
            legs=(_dr(purchase_ledger, amount_paise), _cr(party, amount_paise)),
            check=validate_purchase(date_ddmmyyyy, party, amount_paise),
            operation_id=operation_id,
            party=party,
            narration=narration,
            subject=f"to {party}",
            dry_run=dry_run,
        )

    def create_sales_voucher(
        self,
        date_ddmmyyyy: str,
        party: str,
        amount_paise: int,
        *,
        operation_id: str,
        narration: str = "",
        sales_ledger: str = SALES_LEDGER,
        dry_run: bool = False,
    ) -> VoucherResult:
        """Post one Sales. Party Dr, Sales Cr.

        DOUBLE ENTRY, and the direction is the whole point: the customer now
        OWES the money, so the customer's ledger is debited, and income has
        risen, so Sales is credited. Inverted, this records the business as
        owing its own customer and reduces income - wrong in the debtors list
        and wrong in the profit figure, and wrong without complaining.
        """
        return self._post(
            op="create_sales_voucher",
            vchtype="Sales",
            date_ddmmyyyy=date_ddmmyyyy,
            legs=(_dr(party, amount_paise), _cr(sales_ledger, amount_paise)),
            check=_validate(
                date_ddmmyyyy,
                amount_paise,
                (
                    ("PARTY_EMPTY", "a sale needs a customer", party),
                    ("LEDGER_EMPTY", "a sale needs an income ledger", sales_ledger),
                ),
            ),
            operation_id=operation_id,
            party=party,
            narration=narration,
            subject=f"to {party}",
            dry_run=dry_run,
        )

    def create_payment_voucher(
        self,
        date_ddmmyyyy: str,
        party: str,
        amount_paise: int,
        *,
        operation_id: str,
        from_account: str = CASH_ACCOUNT,
        narration: str = "",
        dry_run: bool = False,
    ) -> VoucherResult:
        """Post one Payment. Party Dr, Cash/Bank Cr.

        DOUBLE ENTRY: money leaves the account it is paid from, so that account
        is credited, and what the supplier is owed goes DOWN, which for a
        liability is a debit. `from_account` is the account the money leaves.
        """
        return self._post(
            op="create_payment_voucher",
            vchtype="Payment",
            date_ddmmyyyy=date_ddmmyyyy,
            legs=(_dr(party, amount_paise), _cr(from_account, amount_paise)),
            check=_validate(
                date_ddmmyyyy,
                amount_paise,
                (
                    ("PARTY_EMPTY", "a payment needs somebody being paid", party),
                    (
                        "ACCOUNT_EMPTY",
                        "a payment needs the account the money leaves",
                        from_account,
                    ),
                ),
                distinct=(party, from_account),
            ),
            operation_id=operation_id,
            party=party,
            narration=narration,
            subject=f"to {party} out of {from_account}",
            dry_run=dry_run,
        )

    def create_receipt_voucher(
        self,
        date_ddmmyyyy: str,
        party: str,
        amount_paise: int,
        *,
        operation_id: str,
        to_account: str = CASH_ACCOUNT,
        narration: str = "",
        dry_run: bool = False,
    ) -> VoucherResult:
        """Post one Receipt. Cash/Bank Dr, Party Cr.

        DOUBLE ENTRY, and it is the mirror of a Payment: cash ARRIVES, so the
        account it lands in is debited, and what the customer owes goes down,
        which for a receivable is a credit. `to_account` is where it lands.
        """
        return self._post(
            op="create_receipt_voucher",
            vchtype="Receipt",
            date_ddmmyyyy=date_ddmmyyyy,
            legs=(_dr(to_account, amount_paise), _cr(party, amount_paise)),
            check=_validate(
                date_ddmmyyyy,
                amount_paise,
                (
                    ("PARTY_EMPTY", "a receipt needs whoever paid", party),
                    (
                        "ACCOUNT_EMPTY",
                        "a receipt needs the account the money arrives in",
                        to_account,
                    ),
                ),
                distinct=(to_account, party),
            ),
            operation_id=operation_id,
            party=party,
            narration=narration,
            subject=f"from {party} into {to_account}",
            dry_run=dry_run,
        )

    def create_journal_voucher(
        self,
        date_ddmmyyyy: str,
        debit_ledger: str,
        credit_ledger: str,
        amount_paise: int,
        *,
        operation_id: str,
        narration: str = "",
        dry_run: bool = False,
    ) -> VoucherResult:
        """Post one Journal. The caller names both sides explicitly.

        DOUBLE ENTRY is the caller's here, which makes this the most dangerous
        of the six: the other five know which way round they go, and this one
        believes whatever it is handed. Nothing in this module can tell a
        correct Journal from a plausible wrong one - only that the two sides
        balance, and that they are not the same ledger twice.

        NO PARTYLEDGERNAME. A Journal has no outside party; the element is left
        out rather than sent empty.
        """
        return self._post(
            op="create_journal_voucher",
            vchtype="Journal",
            date_ddmmyyyy=date_ddmmyyyy,
            legs=(_dr(debit_ledger, amount_paise), _cr(credit_ledger, amount_paise)),
            check=_validate(
                date_ddmmyyyy,
                amount_paise,
                (
                    (
                        "LEDGER_EMPTY",
                        "a journal needs the ledger being debited",
                        debit_ledger,
                    ),
                    (
                        "LEDGER_EMPTY",
                        "a journal needs the ledger being credited",
                        credit_ledger,
                    ),
                ),
                distinct=(debit_ledger, credit_ledger),
            ),
            operation_id=operation_id,
            narration=narration,
            subject=f"{debit_ledger} Dr / {credit_ledger} Cr",
            dry_run=dry_run,
        )

    def create_contra_voucher(
        self,
        date_ddmmyyyy: str,
        debit_account: str,
        credit_account: str,
        amount_paise: int,
        *,
        operation_id: str,
        narration: str = "",
        dry_run: bool = False,
    ) -> VoucherResult:
        """Post one Contra: the company's own money moved between its own
        accounts. Destination Dr, source Cr.

        Debit first, credit second - the same order as
        `create_journal_voucher`, and both names say which side they are. A
        cash withdrawal from the bank is
        `create_contra_voucher(date, "Cash", "Bank", ...)`: cash arrives, bank
        pays out.

        THIS PARAMETER WAS CALLED `debit_account` AND WAS RENAMED ON 2026-08-12.
        On `create_payment_voucher`, `debit_account` is the CREDITED side - the
        account the money leaves. Here it was the DEBITED side. The same name
        meant opposite sides on two neighbouring methods, so anyone writing
        "from Cash to Bank" would have moved the money the wrong way and
        silently inverted a transfer between two real accounts. A comment
        warning about it was not enough: the name itself was the defect.

        NO PARTYLEDGERNAME. A Contra touches no customer and no supplier.
        """
        return self._post(
            op="create_contra_voucher",
            vchtype="Contra",
            date_ddmmyyyy=date_ddmmyyyy,
            legs=(_dr(debit_account, amount_paise), _cr(credit_account, amount_paise)),
            check=_validate(
                date_ddmmyyyy,
                amount_paise,
                (
                    (
                        "ACCOUNT_EMPTY",
                        "a contra needs the account the money arrives in",
                        debit_account,
                    ),
                    (
                        "ACCOUNT_EMPTY",
                        "a contra needs the account the money leaves",
                        credit_account,
                    ),
                ),
                distinct=(debit_account, credit_account),
            ),
            operation_id=operation_id,
            narration=narration,
            subject=f"from {credit_account} into {debit_account}",
            dry_run=dry_run,
        )

    # -- the one path all six take ------------------------------------------

    def _post(
        self,
        *,
        op: str,
        vchtype: str,
        date_ddmmyyyy: str,
        legs: Sequence[Leg],
        check: Validation,
        operation_id: str,
        subject: str,
        party: str = "",
        narration: str = "",
        dry_run: bool = False,
    ) -> VoucherResult:
        """Validate, balance, stamp, ask the door, check for a duplicate, send,
        read back.

        THE ORDER IS THE DESIGN, and each step is where it is for a reason:

            1. operation id present  - nothing else can be judged without it
            2. the type's own checks - date, ledgers, amount
            3. debits equal credits  - refused before an envelope exists
            4. stamp the narration   - the id has to be IN the voucher
            5. build the XML
            6. dry run returns here, having sent nothing at all
            7. THE DOOR              - the cheapest refusal, so it comes first
                                       among the steps that touch the network
            8. the duplicate check   - a read, and it FAILS CLOSED
            9. send, classify, read back by operation id
        """
        if not operation_id.strip():
            return _refused(
                Problem(
                    "OPERATION_ID_MISSING",
                    "every voucher carries a unique operation id, so a retry "
                    "can be told from a second voucher; got "
                    f"{operation_id!r}. accountant.tallyio.client."
                    "new_operation_id() makes one",
                )
            )

        when, _ = parse_ddmmyyyy(date_ddmmyyyy)

        if not check.ok or when is None:
            return _refused(check.problems[0])

        if unbalanced := _balance_problem(legs):
            return _refused(unbalanced)

        try:
            marked = stamp(narration, operation_id)
        except ValueError as clash:
            # The narration already carries a DIFFERENT operation's marker,
            # which happens when a caller retries using the narration it got
            # back from a previous post. Re-stamping would put one voucher under
            # two identities and make both unfindable.
            return _refused(Problem("OPERATION_ID_CONFLICT", str(clash), operation_id))

        amount_paise = sum(leg.debit_paise for leg in legs)
        xml = _voucher_xml(
            self.company, vchtype, when, legs, party=party, narration=marked
        )

        if dry_run:
            return VoucherResult(
                success=True,
                would_send_xml=xml,
                summary=(
                    f"dry run: would post {amount_paise / 100:.2f} {subject} on "
                    f"{when:%d-%m-%Y}. Nothing was sent."
                ),
            )

        # THE DOOR. Before any bytes leave. A refusal here sends nothing.
        writedoor.allow_write(op, self.company)

        with self.log.record(
            op,
            company=self.company,
            party=party,
            amount_paise=amount_paise,
            date=date_ddmmyyyy,
            operation_id=operation_id,
            legs=[_leg_label(leg) for leg in legs],
        ) as entry:
            try:
                existing = self._voucher_with(operation_id)
            except errors.TallyError as unreadable:
                # FAIL CLOSED. Not knowing whether this was already posted is
                # not the same as knowing it was not, and posting on that
                # uncertainty is exactly how two identical Purchase vouchers
                # reached the live company on 2026-08-12.
                entry.error_summary = f"{unreadable.code}: {unreadable.message}"[:300]
                return VoucherResult(
                    success=False,
                    summary=(
                        "nothing was sent: TallyPrime could not be asked whether "
                        f"operation {operation_id!r} is already posted "
                        f"({unreadable.message}). Retrying the identical call "
                        "once the gateway answers is safe - that is what the "
                        "operation id is for."
                    ),
                    error=unreadable,
                )

            if existing is not None:
                entry.status = "success"
                entry.extra["already_posted"] = True
                entry.extra["voucher_number"] = existing.voucher_number
                return VoucherResult(
                    success=True,
                    confirmed=True,
                    already_posted=True,
                    voucher_number=existing.voucher_number,
                    summary=(
                        f"already there: operation {operation_id!r} is voucher "
                        f"{existing.voucher_number or '(unnumbered)'} in "
                        f"{self.company!r} already. Nothing was sent."
                    ),
                )

            entry.request_xml = xml
            raw = self.transport.send(xml, retry=False)
            entry.response_xml = raw

            if failure := errors.classify(raw):
                entry.error_summary = f"{failure.code}: {failure.message}"[:300]
                return VoucherResult(
                    success=False,
                    raw_xml=raw,
                    request_xml=xml,
                    summary=f"Tally refused the voucher: {failure.message}",
                    error=failure,
                )

            created = _created_count(raw)
            posted_as = self._voucher_with(operation_id)
            confirmed = posted_as is not None
            number = _voucher_number(raw) or (
                posted_as.voucher_number if posted_as else ""
            )

            if created == 0 and not confirmed:
                entry.error_summary = "CREATED=0 and no voucher found afterwards"
                return VoucherResult(
                    success=False,
                    raw_xml=raw,
                    request_xml=xml,
                    summary=(
                        "Tally raised no complaint and created nothing. Treated "
                        "as a failure: a silent nothing is the outcome that "
                        "gets believed."
                    ),
                    error=errors.TallyBusinessError(
                        errors.UNCLASSIFIED, "CREATED=0 with no error text"
                    ),
                )

            entry.status = "success"
            entry.extra["voucher_number"] = number
            entry.extra["confirmed"] = confirmed
            return VoucherResult(
                success=True,
                confirmed=confirmed,
                voucher_number=number,
                raw_xml=raw,
                request_xml=xml,
                summary=(
                    f"Created {vchtype} voucher"
                    + (f" #{number}" if number else "")
                    + f" for {amount_paise / 100:.2f} {subject} on "
                    f"{when:%d-%m-%Y}"
                    + ("" if confirmed else " - but read-back did not find it")
                ),
            )

    def _voucher_with(self, operation_id: str) -> Entry | None:
        """The voucher carrying this operation id, or None. Asked of Tally.

        IDENTITY, NOT RESEMBLANCE. The first version of this module confirmed a
        write by looking for a matching AMOUNT in the party's ledger, which
        cannot tell a voucher from its own duplicate and cannot see a Contra at
        all. The marker is unique, so this answers the only question worth
        asking: is OUR entry there.

        Raises rather than returning None when TallyPrime cannot be read. The
        caller needs to tell "it is not there" from "I could not look", and
        collapsing those is how a retry becomes a second entry.
        """
        from accountant.tallyio.reports import Reports

        reports = Reports(self.company, transport=self.transport, log=self.log)
        book = reports.day_book(*_ALL_TIME)
        for entry in book.entries:
            if operation_id_in(entry.narration) == operation_id:
                return entry
        return None


def _refused(problem: Problem) -> VoucherResult:
    """A refusal from this side of the wire. Nothing was sent, and it says so."""
    return VoucherResult(
        success=False,
        summary=f"refused before sending: {problem.said}",
        error=errors.TallyPolicyError(problem.code, problem.said, problem.entity),
    )


def _leg_label(leg: Leg) -> str:
    """`"Cash Dr"`. What the audit row records, so a reader can see the direction.

    An audit row naming an amount and a party but not which ledger went which
    way cannot answer the one question anybody asks it afterwards.
    """
    return f"{leg.ledger} {'Dr' if leg.debit_paise else 'Cr'}"


def _created_count(raw: str) -> int:
    found = re.search(r"<CREATED>\s*(-?\d+)\s*</CREATED>", raw, re.I)
    return int(found.group(1)) if found else 0


def _voucher_number(raw: str) -> str:
    """Tally's own number for what it just made, when it says."""
    for tag in ("VOUCHERNUMBER", "LASTVCHID", "VCHNUMBER"):
        found = re.search(rf"<{tag}>\s*([^<]+?)\s*</{tag}>", raw, re.I)
        if found:
            return found.group(1).strip()
    return ""


def read_back_number(raw: str) -> str:
    """Public alias, so a caller can read a number out of a response it kept."""
    return _voucher_number(raw)


__all__ = [
    "CASH_ACCOUNT",
    "PURCHASE_LEDGER",
    "SALES_LEDGER",
    "Masters",
    "TallyResponseError",
    "VoucherResult",
    "Vouchers",
    "parse_ddmmyyyy",
    "parse_xml",
    "purchase_xml",
    "read_back_number",
    "validate_purchase",
]

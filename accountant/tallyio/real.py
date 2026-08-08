"""The real TallyPrime connector: XML over HTTP.

NOTHING IN THIS FILE HAS EVER RUN AGAINST A REAL TALLY.
====================================================================
There is no TallyPrime instance on this machine and none has answered a single
request from this code. Every XML shape below is a HYPOTHESIS derived from a
prior project that also never ran against real Tally. The tests prove that this
module is internally consistent, that it parses what it builds, and that it
fails loudly rather than silently. They prove nothing about TallyPrime.

Read `ASSUMPTIONS` below before trusting any of it.

Design, in the order the decisions matter:

`stdlib only`
    `pyproject.toml` declares `dependencies = []`. `urllib.request` for
    transport, `xml.parsers.expat` plus `xml.etree.ElementTree.TreeBuilder` for
    parsing. `defusedxml` is the usual answer to hardened XML and it is not
    available to us, so the hardening it provides is implemented here instead.

`untrusted input`
    A Tally response is data, never instructions (ARCHITECTURE.md section 9).
    Responses are size-capped before they are parsed, DTD and entity
    declarations are refused by the parser itself, and external entity
    resolution is refused. Measured on CPython 3.14: the stdlib default parser
    expands internal entities happily, so this is not theatre.

`operation ID is the only identity`
    Every write carries `[ACCOUNTANT_DAD:<op_id>]` in its narration (via
    `stamp`) and the same id in Tally's `REMOTEID`. Reads, duplicate detection
    and reversal all match on that id and on nothing else. Never an amount,
    never narration text.

`fail closed`
    No recorded backup, no write. A dropped response never becomes a second
    voucher: writes are never retried, and every write is preceded by a read
    and followed by a read-back. `read_by_operation_id` returning `None` after
    a write means the write did not happen, whatever HTTP said.

ASSUMPTIONS ABOUT TALLY THAT ONLY A LIVE INSTANCE CAN CONFIRM
-------------------------------------------------------------
A1  Sign convention. A DEBIT is a NEGATIVE `<AMOUNT>` with
    `ISDEEMEDPOSITIVE=Yes`. Our trial balance is debit-positive, so a Tally
    amount is negated to become ours. Lives in `_flip_tally_sign` and nowhere
    else. If this is backwards, every book this touches is backwards.
A2  Envelope shapes. `Export`/`Collection` with a TDL `<COLLECTION>` block for
    reads and `Import`/`Data` for writes, as in the prior project.
A3  `<FETCH>` with comma-separated, dotted member paths is how nested ledger
    entries are requested. TDL also accepts `<NATIVEMETHOD>`; which one a given
    TallyPrime build honours for `AllLedgerEntries` is untested.
A4  Response element names: `<COMPANY>`, `<LEDGER>`, `<VOUCHER>`,
    `<ALLLEDGERENTRIES.LIST>`, `<CLOSINGBALANCE>`, and the import counters
    `<CREATED>`, `<ALTERED>`, `<DELETED>`, `<IGNORED>`, `<ERRORS>`,
    `<EXCEPTIONS>`, `<LASTVCHID>`, `<LINEERROR>`.
A5  `REMOTEID` is honoured as an external system key on create, and is
    round-tripped back on export.
A6  Deletion. `ACTION="Delete"` with `REMOTEID` and `MASTERID` deletes exactly
    that voucher. Some builds want a `VCHKEY` instead. The delete is therefore
    always verified by reading afterwards rather than believed.
A7  `MASTERID` is present on exported vouchers and is a usable `tally_id`.
A8  Closing balances may carry a trailing `Dr`/`Cr` suffix. When they do it is
    treated as authoritative and A1 is not consulted.
A9  Encoding: UTF-8 unless a UTF-16 BOM says otherwise.
A10 Vouchers we write have exactly two ledger entries. Tally accepts a
    two-legged Journal built this way.
"""

from __future__ import annotations

import codecs
import datetime
import re
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

# bandit's B405 objects to xml.etree on sight and points at defusedxml, which
# would be a runtime dependency this project does not have. Only TreeBuilder and
# Element are used - the parser itself is expat, configured in _hardened_parser
# to refuse exactly what defusedxml refuses.
from xml.etree import ElementTree  # nosec B405
from xml.parsers import expat

from accountant.schema import Voucher
from accountant.tallyio.client import (
    CompanyNotBackedUp,
    DuplicateOperation,
    WriteResult,
    marker_for,
    operation_id_in,
    stamp,
)

# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class TallyError(Exception):
    """Anything that went wrong at the Tally boundary."""


class TallyUnreachable(TallyError):
    """Tally did not answer at all. Distinct from answering and refusing."""


class TallyResponseError(TallyError):
    """Tally answered with something we refuse to treat as an answer.

    A bad status, an oversized body, a DTD, or XML that will not parse. Never
    downgraded to an empty result - a silent `()` here would read as "this
    company has no vouchers", which is a lie with statutory consequences.
    """


class TallyRejected(TallyError):
    """Tally understood the write and did not do it, or claims it did and
    did not."""


class TallyDataError(TallyError):
    """Tally answered, the XML parsed, and the content breaks an invariant.

    A two-legged voucher whose legs do not cancel, or one of our own marked
    vouchers that no longer has two legs.
    """


# ---------------------------------------------------------------------------
# money - integer paise, never a float
# ---------------------------------------------------------------------------

# Rupee sign, digit grouping, ordinary space, and the no-break space Tally
# uses inside grouped amounts.
_STRIP_FROM_AMOUNT = ("\u20b9", ",", " ", "\u00a0")
_HUNDRED = Decimal(100)


def paise_from_rupees(text: str) -> int:
    """Parse Tally's decimal rupee string into integer paise.

    Accepts `"1,234.56"`, `"1234"`, `"-12.30"`, `"(500.00)"`, `"Rs 1 234.5"`.
    Uses `Decimal` throughout: binary floating point cannot represent 0.07
    rupees, and a trial balance that must return to the exact paise cannot
    tolerate the error.

    Refuses sub-paise precision rather than rounding it away. Rounding invoice
    arithmetic is how reconciliation breaks three months later.
    """
    cleaned = text.strip()
    for junk in _STRIP_FROM_AMOUNT:
        cleaned = cleaned.replace(junk, "")
    if not cleaned:
        raise TallyDataError(f"cannot read an amount from {text!r}: it is empty")

    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]

    try:
        rupees = Decimal(cleaned)
    except InvalidOperation as exc:
        raise TallyDataError(f"cannot read an amount from {text!r}") from exc

    scaled = rupees * _HUNDRED
    paise = int(scaled)
    if scaled != paise:
        raise TallyDataError(
            f"{text!r} carries sub-paise precision; refusing to round it away"
        )
    return -paise if negative else paise


def rupees_from_paise(paise: int) -> str:
    """Render integer paise as the two-decimal string Tally expects.

    No thousands separators: Tally parses the number, it does not read it.
    """
    sign = "-" if paise < 0 else ""
    whole, fraction = divmod(abs(paise), 100)
    return f"{sign}{whole}.{fraction:02d}"


def _flip_tally_sign(paise: int) -> int:
    """Assumption A1, in one place.

    Tally holds a debit as a negative amount. We hold a debit as positive, the
    same way `FakeTally.trial_balance` does. Every crossing of that boundary
    goes through this function, in both directions, so that a live instance
    proving A1 wrong is a one-line change and not an audit.
    """
    return -paise


# ---------------------------------------------------------------------------
# XML hardening - Tally responses are untrusted input
# ---------------------------------------------------------------------------

DEFAULT_MAX_RESPONSE_BYTES = 32 * 1024 * 1024

# Bare ampersands Tally emits inside names ("Smith & Co"), which are not legal
# XML. Rewriting them to &amp; can only ever make a payload less active.
_BARE_AMPERSAND = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")
# Control bytes XML 1.0 forbids. Tally emits \x04 inside ledger names.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
# Belt to the parser handlers' braces. Checked after control bytes are removed
# so that "<!DOC\x00TYPE" cannot slip past.
_DOCTYPE = re.compile(r"<!\s*(DOCTYPE|ENTITY)\b", re.IGNORECASE)


class _XmlRefused(Exception):
    """Raised inside an expat handler to abort a hostile document."""


def _refuse_doctype(
    name: str, _system_id: str | None, _public_id: str | None, _internal: bool
) -> None:
    raise _XmlRefused(f"response declares a DOCTYPE ({name!r}); refusing to parse")


def _refuse_entity_declaration(*_args: object) -> None:
    raise _XmlRefused("response declares an XML entity; refusing to parse")


def _refuse_external_entity(*_args: object) -> bool:
    raise _XmlRefused("response references an external entity; refusing to parse")


def _hardened_parser(builder: ElementTree.TreeBuilder) -> expat.XMLParserType:
    """An expat parser that will not resolve a DTD or an entity.

    `xml.etree.ElementTree.XMLParser` does not expose its expat parser on
    CPython 3.14, so the parser is created directly and fed into ElementTree's
    own `TreeBuilder`. The result is an ordinary `Element` tree.

    Measured on CPython 3.14: without these four handlers, a billion-laughs
    payload expands and an external entity reference is attempted.
    """
    parser = expat.ParserCreate()
    parser.buffer_text = True
    parser.StartDoctypeDeclHandler = _refuse_doctype
    parser.EntityDeclHandler = _refuse_entity_declaration
    parser.UnparsedEntityDeclHandler = _refuse_entity_declaration
    parser.ExternalEntityRefHandler = _refuse_external_entity
    parser.StartElementHandler = builder.start
    parser.EndElementHandler = builder.end
    parser.CharacterDataHandler = builder.data
    return parser


def sanitise(payload: str) -> str:
    """Make Tally's output parseable without discarding content silently."""
    text = payload.lstrip("﻿").strip()
    text = _CONTROL_CHARS.sub("", text)
    return _BARE_AMPERSAND.sub("&amp;", text)


def parse_xml(
    payload: str, limit: int = DEFAULT_MAX_RESPONSE_BYTES
) -> ElementTree.Element:
    """Parse one Tally response. The only entry point to a parser in this file."""
    if len(payload) > limit:
        raise TallyResponseError(
            f"response of {len(payload)} characters exceeds the {limit} cap; "
            "refusing to parse it"
        )
    text = sanitise(payload)
    if not text:
        raise TallyResponseError("Tally returned an empty body")
    if _DOCTYPE.search(text):
        raise TallyResponseError(
            "response contains a DOCTYPE or ENTITY declaration; refusing to parse"
        )

    builder = ElementTree.TreeBuilder()
    parser = _hardened_parser(builder)
    try:
        parser.Parse(text, True)
        return builder.close()
    except _XmlRefused as exc:
        raise TallyResponseError(str(exc)) from exc
    except expat.ExpatError as exc:
        raise TallyResponseError(
            f"Tally returned unparseable XML ({exc}); first 200 characters: "
            f"{text[:200]!r}"
        ) from exc


def _text_of(node: ElementTree.Element | None) -> str | None:
    if node is None or node.text is None:
        return None
    return node.text.strip() or None


def _child_text(parent: ElementTree.Element, tag: str) -> str | None:
    return _text_of(parent.find(tag))


def _name_of(node: ElementTree.Element) -> str | None:
    """Tally puts a master's name on the NAME attribute or in a NAME child."""
    attribute = node.get("NAME")
    if attribute is not None and attribute.strip():
        return attribute.strip()
    return _child_text(node, "NAME")


def _counter(root: ElementTree.Element, tag: str) -> int:
    value = _text_of(root.find(f".//{tag}"))
    if value is None:
        return 0
    try:
        return int(value)
    except ValueError as exc:
        raise TallyDataError(f"<{tag}> is not a number: {value!r}") from exc


# ---------------------------------------------------------------------------
# building requests
# ---------------------------------------------------------------------------

COLLECTION_COMPANIES = "AD Companies"
COLLECTION_LEDGERS = "AD Ledgers"
COLLECTION_BALANCES = "AD Ledger Balances"
COLLECTION_VOUCHERS = "AD Vouchers"


_XML_ESCAPES = (
    ("&", "&amp;"),  # first, or it would double-escape the ones below
    ("<", "&lt;"),
    (">", "&gt;"),
    ('"', "&quot;"),
    ("'", "&apos;"),
)


def _escaped(text: str) -> str:
    """Escape one value for XML, after removing bytes XML 1.0 forbids.

    Written out rather than taken from `xml.sax.saxutils`, which drags in a
    module the security scanner blacklists wholesale for its parsers - we want
    five string replacements, not a parser.
    """
    escaped = _CONTROL_CHARS.sub("", text)
    for raw, entity in _XML_ESCAPES:
        escaped = escaped.replace(raw, entity)
    return escaped


def _static_variables(company: str | None) -> str:
    export_format = "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
    if company is None:
        return f"<STATICVARIABLES>{export_format}</STATICVARIABLES>"
    return (
        "<STATICVARIABLES>"
        f"{export_format}"
        f"<SVCURRENTCOMPANY>{_escaped(company)}</SVCURRENTCOMPANY>"
        "</STATICVARIABLES>"
    )


def _export_collection(
    collection_id: str,
    company: str | None,
    tally_type: str,
    fetch: tuple[str, ...],
) -> str:
    """One Export/Collection envelope. Assumptions A2 and A3."""
    fetched = ", ".join(fetch)
    return (
        "<ENVELOPE>"
        "<HEADER>"
        "<VERSION>1</VERSION>"
        "<TALLYREQUEST>Export</TALLYREQUEST>"
        "<TYPE>Collection</TYPE>"
        f"<ID>{_escaped(collection_id)}</ID>"
        "</HEADER>"
        "<BODY><DESC>"
        f"{_static_variables(company)}"
        "<TDL><TDLMESSAGE>"
        f'<COLLECTION NAME="{_escaped(collection_id)}" ISMODIFY="No" '
        'ISFIXED="No">'
        f"<TYPE>{_escaped(tally_type)}</TYPE>"
        f"<FETCH>{_escaped(fetched)}</FETCH>"
        "</COLLECTION>"
        "</TDLMESSAGE></TDL>"
        "</DESC></BODY>"
        "</ENVELOPE>"
    )


def build_company_list_request() -> str:
    """List the open companies. The cheapest proof the transport works."""
    return _export_collection(COLLECTION_COMPANIES, None, "Company", ("Name",))


def build_ledger_list_request(company: str) -> str:
    """The chart of accounts. Clarifying questions may only offer these."""
    return _export_collection(COLLECTION_LEDGERS, company, "Ledger", ("Name", "Parent"))


def build_closing_balance_request(company: str) -> str:
    """Tally's own closing balances - the trial balance we check reversal against."""
    return _export_collection(
        COLLECTION_BALANCES, company, "Ledger", ("Name", "ClosingBalance")
    )


def build_voucher_list_request(company: str) -> str:
    """Posted history, with the ledger entries needed to read debit and credit."""
    return _export_collection(
        COLLECTION_VOUCHERS,
        company,
        "Voucher",
        (
            "Date",
            "VoucherNumber",
            "VoucherTypeName",
            "Narration",
            "MasterID",
            "RemoteID",
            "PartyLedgerName",
            "AllLedgerEntries.LedgerName",
            "AllLedgerEntries.Amount",
            "AllLedgerEntries.IsDeemedPositive",
        ),
    )


def _ledger_entry(ledger: str, amount_paise: int, is_debit: bool) -> str:
    """The one place Tally's sign convention is written on the way out.

    Assumption A1. A voucher built with these reversed imports cleanly and is
    wrong, which is the worst failure mode available.
    """
    signed = _flip_tally_sign(amount_paise) if is_debit else amount_paise
    return (
        "<ALLLEDGERENTRIES.LIST>"
        f"<LEDGERNAME>{_escaped(ledger)}</LEDGERNAME>"
        f"<ISDEEMEDPOSITIVE>{'Yes' if is_debit else 'No'}</ISDEEMEDPOSITIVE>"
        f"<AMOUNT>{rupees_from_paise(signed)}</AMOUNT>"
        "</ALLLEDGERENTRIES.LIST>"
    )


def _check_writable(voucher: Voucher) -> None:
    """Refuse at the boundary. An entry that cannot be represented faithfully
    must never reach the wire, whatever ran upstream."""
    if voucher.amount_paise <= 0:
        raise ValueError(
            f"refusing to write voucher {voucher.id!r}: amount_paise is "
            f"{voucher.amount_paise}, which is not a postable amount"
        )
    if not voucher.debit_account.strip() or not voucher.credit_account.strip():
        raise ValueError(
            f"refusing to write voucher {voucher.id!r}: it needs both a debit "
            "and a credit account"
        )
    if voucher.debit_account == voucher.credit_account:
        raise ValueError(
            f"refusing to write voucher {voucher.id!r}: debit and credit are "
            f"the same account ({voucher.debit_account!r})"
        )
    if voucher.gst_paise is not None:
        raise ValueError(
            f"refusing to write voucher {voucher.id!r}: it carries GST of "
            f"{voucher.gst_paise} paise and this connector builds no tax lines. "
            "Writing it would silently drop the tax, producing a wrong "
            "statutory entry."
        )


def build_voucher_create(
    company: str,
    voucher: Voucher,
    narration: str,
    operation_id: str,
    voucher_type: str,
) -> str:
    """Import/Data envelope for one two-legged voucher.

    The operation ID goes in two places: `REMOTEID`, which is Tally's slot for
    an external system's key, and the marker inside the narration, which is
    what `operation_id_in` reads back. The narration is the contract; REMOTEID
    is the convenience.
    """
    _check_writable(voucher)
    if marker_for(operation_id) not in narration:
        raise ValueError(
            f"refusing to write voucher {voucher.id!r} without the "
            f"{marker_for(operation_id)} marker in its narration"
        )

    stamped_date = voucher.date.strftime("%Y%m%d")
    legs = _ledger_entry(
        voucher.debit_account, voucher.amount_paise, is_debit=True
    ) + _ledger_entry(voucher.credit_account, voucher.amount_paise, is_debit=False)

    return (
        "<ENVELOPE>"
        "<HEADER>"
        "<VERSION>1</VERSION>"
        "<TALLYREQUEST>Import</TALLYREQUEST>"
        "<TYPE>Data</TYPE>"
        "<ID>Vouchers</ID>"
        "</HEADER>"
        "<BODY>"
        f"<DESC>{_static_variables(company)}</DESC>"
        "<DATA>"
        '<TALLYMESSAGE xmlns:UDF="TallyUDF">'
        f'<VOUCHER VCHTYPE="{_escaped(voucher_type)}" ACTION="Create" '
        f'OBJVIEW="Accounting Voucher View" '
        f'REMOTEID="{_escaped(operation_id)}">'
        f"<DATE>{stamped_date}</DATE>"
        f"<EFFECTIVEDATE>{stamped_date}</EFFECTIVEDATE>"
        f"<VOUCHERTYPENAME>{_escaped(voucher_type)}</VOUCHERTYPENAME>"
        f"<PARTYLEDGERNAME>{_escaped(voucher.party)}</PARTYLEDGERNAME>"
        f"<NARRATION>{_escaped(narration)}</NARRATION>"
        "<PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>"
        f"{legs}"
        "</VOUCHER>"
        "</TALLYMESSAGE>"
        "</DATA>"
        "</BODY>"
        "</ENVELOPE>"
    )


def build_voucher_delete(
    company: str,
    voucher: Voucher,
    operation_id: str,
    voucher_type: str,
) -> str:
    """Import/Data envelope that deletes exactly one voucher. Assumption A6.

    Identified by REMOTEID and MASTERID, which are both the operation ID's
    doing. Never by amount, never by narration text.
    """
    master_id = voucher.tally_id
    if master_id is None:
        raise TallyDataError(
            f"cannot delete operation {operation_id!r}: Tally gave the voucher "
            "no MASTERID to aim at"
        )
    stamped_date = voucher.date.strftime("%Y%m%d")
    return (
        "<ENVELOPE>"
        "<HEADER>"
        "<VERSION>1</VERSION>"
        "<TALLYREQUEST>Import</TALLYREQUEST>"
        "<TYPE>Data</TYPE>"
        "<ID>Vouchers</ID>"
        "</HEADER>"
        "<BODY>"
        f"<DESC>{_static_variables(company)}</DESC>"
        "<DATA>"
        '<TALLYMESSAGE xmlns:UDF="TallyUDF">'
        f'<VOUCHER VCHTYPE="{_escaped(voucher_type)}" ACTION="Delete" '
        f'REMOTEID="{_escaped(operation_id)}">'
        f"<DATE>{stamped_date}</DATE>"
        f"<VOUCHERTYPENAME>{_escaped(voucher_type)}</VOUCHERTYPENAME>"
        f"<MASTERID>{_escaped(master_id)}</MASTERID>"
        "</VOUCHER>"
        "</TALLYMESSAGE>"
        "</DATA>"
        "</BODY>"
        "</ENVELOPE>"
    )


# ---------------------------------------------------------------------------
# parsing responses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportResult:
    """The counters Tally returns from an Import/Data request."""

    created: int = 0
    altered: int = 0
    deleted: int = 0
    ignored: int = 0
    errors: int = 0
    exceptions: int = 0
    last_vch_id: str | None = None
    line_errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.errors == 0 and self.exceptions == 0 and not self.line_errors

    def summary(self) -> str:
        return (
            f"created={self.created} altered={self.altered} "
            f"deleted={self.deleted} ignored={self.ignored} "
            f"errors={self.errors} exceptions={self.exceptions} "
            f"line_errors={list(self.line_errors)}"
        )


@dataclass(frozen=True)
class VoucherPage:
    """What a voucher export gave us, and what it could not give us.

    `skipped` counts vouchers with anything other than two ledger entries.
    `Voucher` has one debit and one credit and cannot hold them. They are
    counted rather than silently dropped, because "this company has 40
    vouchers" and "this company has 40 vouchers we can read" are different
    statements.
    """

    vouchers: tuple[Voucher, ...] = ()
    skipped: int = 0


def parse_companies(
    payload: str, limit: int = DEFAULT_MAX_RESPONSE_BYTES
) -> tuple[str, ...]:
    root = parse_xml(payload, limit)
    return tuple(
        name for node in root.iter("COMPANY") if (name := _name_of(node)) is not None
    )


def parse_ledger_names(
    payload: str, limit: int = DEFAULT_MAX_RESPONSE_BYTES
) -> tuple[str, ...]:
    root = parse_xml(payload, limit)
    return tuple(
        name for node in root.iter("LEDGER") if (name := _name_of(node)) is not None
    )


def _signed_balance_paise(text: str) -> int:
    """A closing balance, in our debit-positive convention.

    Assumption A8: a trailing `Dr`/`Cr` is authoritative when present, which
    makes the trial balance independent of assumption A1. Without a suffix we
    fall back to A1.
    """
    stripped = text.strip()
    upper = stripped.upper()
    for suffix, debit in (("DR", True), ("CR", False)):
        if upper.endswith(suffix):
            magnitude = abs(paise_from_rupees(stripped[: -len(suffix)]))
            return magnitude if debit else -magnitude
    return _flip_tally_sign(paise_from_rupees(stripped))


def parse_closing_balances(
    payload: str, limit: int = DEFAULT_MAX_RESPONSE_BYTES
) -> dict[str, int]:
    """Account name -> balance in paise, debit positive, zeros dropped.

    Zeros are dropped so that this matches `FakeTally.trial_balance` exactly.
    A ledger that nets to nothing is not a line on a trial balance.
    """
    root = parse_xml(payload, limit)
    balances: dict[str, int] = {}
    for node in root.iter("LEDGER"):
        name = _name_of(node)
        raw = _child_text(node, "CLOSINGBALANCE")
        if name is None or raw is None:
            continue
        balance = _signed_balance_paise(raw)
        if balance != 0:
            balances[name] = balance
    return balances


@dataclass(frozen=True)
class _Leg:
    ledger: str
    amount_paise: int
    is_debit: bool


def _parse_leg(node: ElementTree.Element) -> _Leg | None:
    ledger = _child_text(node, "LEDGERNAME")
    raw_amount = _child_text(node, "AMOUNT")
    if ledger is None or raw_amount is None:
        return None
    amount = paise_from_rupees(raw_amount)
    flag = _child_text(node, "ISDEEMEDPOSITIVE")

    if flag is None:
        return _Leg(ledger=ledger, amount_paise=amount, is_debit=amount < 0)

    is_debit = flag.strip().lower() == "yes"
    if amount != 0 and (amount < 0) != is_debit:
        # Assumption A1 disagrees with Tally. Say so instead of picking one:
        # guessing here silently inverts a statutory entry.
        raise TallyDataError(
            f"ledger {ledger!r}: ISDEEMEDPOSITIVE={flag!r} does not agree with "
            f"AMOUNT={raw_amount!r}. Assumption A1 about Tally's sign "
            "convention is wrong; fix _flip_tally_sign before writing anything."
        )
    return _Leg(ledger=ledger, amount_paise=amount, is_debit=is_debit)


def _voucher_from(node: ElementTree.Element) -> Voucher | None:
    """One exported voucher, or None when it is not two-legged."""
    legs = [
        leg
        for entry in node.iter("ALLLEDGERENTRIES.LIST")
        if (leg := _parse_leg(entry))
    ]
    debits = [leg for leg in legs if leg.is_debit]
    credits = [leg for leg in legs if not leg.is_debit]
    if len(legs) != 2 or len(debits) != 1 or len(credits) != 1:
        return None

    debit, credit = debits[0], credits[0]
    if abs(debit.amount_paise) != abs(credit.amount_paise):
        raise TallyDataError(
            f"voucher legs do not cancel: {debit.ledger!r} "
            f"{debit.amount_paise} against {credit.ledger!r} "
            f"{credit.amount_paise}"
        )

    raw_date = _child_text(node, "DATE")
    if raw_date is None:
        raise TallyDataError("exported voucher has no <DATE>")
    try:
        date = _date_from_tally(raw_date)
    except ValueError as exc:
        raise TallyDataError(f"unreadable voucher date {raw_date!r}") from exc

    remote_id = node.get("REMOTEID") or _child_text(node, "REMOTEID")
    master_id = node.get("MASTERID") or _child_text(node, "MASTERID")
    number = _child_text(node, "VOUCHERNUMBER")
    narration = _child_text(node, "NARRATION") or ""

    return Voucher(
        id=remote_id or master_id or number or "",
        date=date,
        party=_child_text(node, "PARTYLEDGERNAME") or credit.ledger,
        narration=narration,
        debit_account=debit.ledger,
        credit_account=credit.ledger,
        amount_paise=abs(debit.amount_paise),
        gst_paise=None,
        tally_id=master_id or number,
        provenance=None,
    )


def _date_from_tally(raw: str) -> datetime.date:
    return datetime.datetime.strptime(raw.strip(), "%Y%m%d").date()


def parse_vouchers(
    payload: str, limit: int = DEFAULT_MAX_RESPONSE_BYTES
) -> VoucherPage:
    """Every voucher we can represent, plus a count of the ones we cannot.

    A voucher that carries OUR marker and is no longer two-legged raises: it
    means one of our entries was edited in Tally, and bulk-reverse arithmetic
    over it can no longer be trusted.
    """
    root = parse_xml(payload, limit)
    vouchers: list[Voucher] = []
    skipped = 0
    for node in root.iter("VOUCHER"):
        voucher = _voucher_from(node)
        if voucher is not None:
            vouchers.append(voucher)
            continue
        skipped += 1
        narration = _child_text(node, "NARRATION") or ""
        operation = operation_id_in(narration)
        if operation is not None:
            raise TallyDataError(
                f"voucher for operation {operation!r} no longer has exactly one "
                "debit and one credit. It was edited in Tally; reversal "
                "arithmetic over it cannot be trusted."
            )
    return VoucherPage(vouchers=tuple(vouchers), skipped=skipped)


def parse_import_response(
    payload: str, limit: int = DEFAULT_MAX_RESPONSE_BYTES
) -> ImportResult:
    root = parse_xml(payload, limit)
    line_errors = tuple(
        text for node in root.iter("LINEERROR") if (text := _text_of(node)) is not None
    )
    return ImportResult(
        created=_counter(root, "CREATED"),
        altered=_counter(root, "ALTERED"),
        deleted=_counter(root, "DELETED"),
        ignored=_counter(root, "IGNORED"),
        errors=_counter(root, "ERRORS"),
        exceptions=_counter(root, "EXCEPTIONS"),
        last_vch_id=_text_of(root.find(".//LASTVCHID")),
        line_errors=line_errors,
    )


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


@dataclass(frozen=True)
class TallyConfig:
    """Where Tally is and how patient we are.

    `host` is configurable on purpose. ARCHITECTURE.md prefers loopback because
    port 9000 has no authentication beyond network reachability, but Tally is
    Windows-only and in this project it runs in a VM, so `localhost` on the host
    is a different machine from `localhost` in the guest. A non-loopback host is
    therefore allowed and is the caller's decision: the traffic is plain HTTP
    with no auth, so it must stay on a private, trusted network. `is_loopback`
    exists so a caller or a test can assert the tighter rule when it applies.
    """

    host: str = "localhost"
    port: int = 9000
    timeout_seconds: float = 30.0
    retries: int = 3
    retry_backoff_seconds: float = 0.5
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    voucher_type: str = "Journal"

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("host must be a hostname or address, not empty")
        if not 1 <= self.port <= 65535:
            raise ValueError(f"port {self.port} is not a TCP port")
        if self.timeout_seconds <= 0:
            raise ValueError(
                f"timeout_seconds must be positive, got {self.timeout_seconds}"
            )
        if self.retries < 1:
            raise ValueError(f"retries must be at least 1, got {self.retries}")
        if self.retry_backoff_seconds < 0:
            raise ValueError(
                f"retry_backoff_seconds cannot be negative, got "
                f"{self.retry_backoff_seconds}"
            )
        if self.max_response_bytes < 1:
            raise ValueError(
                f"max_response_bytes must be positive, got {self.max_response_bytes}"
            )
        if not self.voucher_type.strip():
            raise ValueError("voucher_type must be a Tally voucher type name")

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def is_loopback(self) -> bool:
        return self.host.lower() in _LOOPBACK_HOSTS


# ---------------------------------------------------------------------------
# the backup gate (#6.7)
# ---------------------------------------------------------------------------


class BackupLog(Protocol):
    """What we know about backups. Tally does not tell us; we record it."""

    def has_backup(self, company: str) -> bool: ...


@dataclass(frozen=True)
class RecordedBackups:
    """The companies somebody has recorded a backup for.

    The default is empty, so the default `RealTally` refuses every write. Fail
    closed: a missing backup record and a missing backup are the same thing as
    far as we can tell, and only one of those is safe to assume.
    """

    companies: frozenset[str] = frozenset()

    def has_backup(self, company: str) -> bool:
        return company in self.companies


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------


class Transport(Protocol):
    """One XML envelope in, one response body out.

    `retry` is False for anything that writes. A connection that dies after
    Tally committed is indistinguishable from one that died before it did, so a
    retried write is a duplicate voucher.
    """

    def send(self, payload: str, *, retry: bool) -> str: ...


class HttpPoster(Protocol):
    def __call__(
        self, url: str, body: bytes, timeout: float, max_bytes: int
    ) -> tuple[int, bytes]: ...


def _post_bytes(
    url: str, body: bytes, timeout: float, max_bytes: int
) -> tuple[int, bytes]:
    """One HTTP POST, over a deliberately minimal opener.

    The opener carries an HTTP handler and nothing else: no redirect handler,
    so a 3xx is returned rather than followed, and no file, ftp or data
    handlers, so a redirect could not reach them anyway. `read(max_bytes + 1)`
    means an oversized body is never fully buffered - one byte over the cap is
    all we need to reject it.
    """
    # S310 asks whether the scheme could be file: or something custom. It cannot:
    # `TallyConfig.url` is built as an f-string beginning with a literal
    # "http://", and the opener below carries no handler that could serve any
    # other scheme even if it were.
    request = urllib.request.Request(  # noqa: S310
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "Content-Length": str(len(body)),
        },
    )
    opener = urllib.request.OpenerDirector()
    opener.add_handler(urllib.request.HTTPHandler())
    with opener.open(request, timeout=timeout) as response:
        return int(response.status), response.read(max_bytes + 1)


def _decode(raw: bytes) -> str:
    """Assumption A9."""
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16", errors="replace")
    if raw.startswith(codecs.BOM_UTF8):
        raw = raw[len(codecs.BOM_UTF8) :]
    return raw.decode("utf-8", errors="replace")


class HttpTransport:
    """Serialised HTTP transport for TallyPrime's XML gateway.

    TallyPrime processes one XML request at a time. That is Tally's constraint,
    not a design choice, so it is enforced with a lock here rather than left to
    every caller to remember.
    """

    def __init__(
        self,
        config: TallyConfig | None = None,
        *,
        poster: HttpPoster | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config or TallyConfig()
        self._post = poster if poster is not None else _post_bytes
        self._sleep = sleep
        self._lock = threading.Lock()

    def send(self, payload: str, *, retry: bool) -> str:
        attempts = self.config.retries if retry else 1
        body = payload.encode("utf-8")
        cap = self.config.max_response_bytes

        with self._lock:
            last_error: OSError | None = None
            for attempt in range(1, attempts + 1):
                try:
                    status, raw = self._post(
                        self.config.url, body, self.config.timeout_seconds, cap
                    )
                except OSError as exc:
                    # URLError and TimeoutError are both OSError. Tally never
                    # answered, so nothing was committed and a read may retry.
                    last_error = exc
                    if attempt < attempts:
                        self._sleep(
                            self.config.retry_backoff_seconds * 2 ** (attempt - 1)
                        )
                    continue

                if status < 200 or status >= 300:
                    raise TallyResponseError(
                        f"Tally at {self.config.url} answered HTTP {status}"
                    )
                if len(raw) > cap:
                    raise TallyResponseError(
                        f"Tally at {self.config.url} sent more than the "
                        f"{cap} byte cap; refusing to read it"
                    )
                return _decode(raw)

        raise TallyUnreachable(
            f"no response from Tally at {self.config.url} after {attempts} "
            "attempt(s). Check that TallyPrime is running, that the company is "
            "open, and that the HTTP server is on (F1 > Settings > Advanced "
            f"Configuration > HTTP Server). Last error: {last_error}"
        ) from last_error


# ---------------------------------------------------------------------------
# the connector
# ---------------------------------------------------------------------------


class RealTally:
    """TallyClient over XML/HTTP. See the module docstring before trusting it.

    Identity is the operation ID and nothing else. `read_by_operation_id`
    matches the marker in the narration, exactly as `FakeTally` does, by
    scanning the company's vouchers. A TDL filter would be faster; it would
    also mean that a filter Tally silently does not honour reads as "this
    operation was never written", which on the write path is a duplicate
    voucher. Scanning cannot fail that way.
    """

    def __init__(
        self,
        config: TallyConfig | None = None,
        *,
        transport: Transport | None = None,
        backups: BackupLog | None = None,
    ) -> None:
        self.config = config or TallyConfig()
        self._transport: Transport = (
            transport if transport is not None else HttpTransport(self.config)
        )
        self._backups: BackupLog = backups if backups is not None else RecordedBackups()

    @property
    def _limit(self) -> int:
        return self.config.max_response_bytes

    # ---- reads -------------------------------------------------------------

    def list_companies(self) -> tuple[str, ...]:
        return parse_companies(
            self._transport.send(build_company_list_request(), retry=True), self._limit
        )

    def read_accounts(self, company: str) -> tuple[str, ...]:
        return parse_ledger_names(
            self._transport.send(build_ledger_list_request(company), retry=True),
            self._limit,
        )

    def read_vouchers_page(self, company: str) -> VoucherPage:
        """`read_vouchers`, plus the count of vouchers we could not represent.

        Not part of `TallyClient`. It exists so a caller can see that a company
        has entries this connector cannot read, instead of inferring their
        absence.
        """
        return parse_vouchers(
            self._transport.send(build_voucher_list_request(company), retry=True),
            self._limit,
        )

    def read_vouchers(self, company: str) -> tuple[Voucher, ...]:
        return self.read_vouchers_page(company).vouchers

    def trial_balance(self, company: str) -> dict[str, int]:
        return parse_closing_balances(
            self._transport.send(build_closing_balance_request(company), retry=True),
            self._limit,
        )

    def read_by_operation_id(self, company: str, operation_id: str) -> Voucher | None:
        for voucher in self.read_vouchers(company):
            if operation_id_in(voucher.narration) == operation_id:
                return voucher
        return None

    def list_our_vouchers(self, company: str) -> tuple[Voucher, ...]:
        return tuple(
            voucher
            for voucher in self.read_vouchers(company)
            if operation_id_in(voucher.narration) is not None
        )

    # ---- writes ------------------------------------------------------------

    def write_voucher(
        self, company: str, voucher: Voucher, operation_id: str
    ) -> WriteResult:
        """Write one marked voucher, then prove it exists by reading it back."""
        if not self._backups.has_backup(company):
            raise CompanyNotBackedUp(
                f"{company!r} has no recorded backup; refusing to write"
            )
        if self.read_by_operation_id(company, operation_id) is not None:
            raise DuplicateOperation(
                f"operation {operation_id!r} was already written to {company!r}"
            )

        narration = stamp(voucher.narration, operation_id)
        payload = build_voucher_create(
            company, voucher, narration, operation_id, self.config.voucher_type
        )
        result = parse_import_response(
            self._transport.send(payload, retry=False), self._limit
        )

        if not result.ok:
            raise TallyRejected(
                f"Tally rejected operation {operation_id!r}: {result.summary()}"
            )
        if result.altered:
            raise TallyRejected(
                f"operation {operation_id!r} altered {result.altered} existing "
                f"voucher(s) instead of creating one: {result.summary()}"
            )
        if result.created < 1:
            raise TallyRejected(
                f"Tally accepted operation {operation_id!r} and created "
                f"nothing: {result.summary()}"
            )

        written = self.read_by_operation_id(company, operation_id)
        if written is None:
            raise TallyRejected(
                f"operation {operation_id!r} was not found in {company!r} after "
                "the write. The voucher does not exist, whatever HTTP said."
            )

        tally_id = written.tally_id or result.last_vch_id
        if tally_id is None:
            raise TallyDataError(
                f"operation {operation_id!r} was written but Tally reported no "
                "MASTERID for it, so it cannot be reversed later"
            )
        return WriteResult(
            operation_id=operation_id, tally_id=tally_id, narration=narration
        )

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        """Delete exactly the voucher carrying this operation ID.

        False means it was not there. True means it was there and is verifiably
        gone. Anything else raises: reversal is never reported as succeeding
        because Tally said so.
        """
        existing = self.read_by_operation_id(company, operation_id)
        if existing is None:
            return False

        payload = build_voucher_delete(
            company, existing, operation_id, self.config.voucher_type
        )
        result = parse_import_response(
            self._transport.send(payload, retry=False), self._limit
        )
        if not result.ok:
            raise TallyRejected(
                f"Tally refused to delete operation {operation_id!r}: "
                f"{result.summary()}"
            )
        if self.read_by_operation_id(company, operation_id) is not None:
            raise TallyRejected(
                f"Tally reported {result.summary()} for the deletion of "
                f"operation {operation_id!r}, but the voucher is still there"
            )
        return True

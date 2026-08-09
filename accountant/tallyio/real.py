"""The real TallyPrime connector: XML over HTTP.

THIS FILE HAS RUN AGAINST A REAL TALLY. NOT EVERY LINE OF IT HAS.
====================================================================
On 2026-08-08 this module read from and wrote to a real TallyPrime Release 7.0
(Series A 7.0.0 Build 27974) in EDUCATIONAL mode, running in a Windows 11 ARM64
VM. Proven end to end through this code: list companies, read the chart of
accounts, read the trial balance, write one marked voucher, read it back by
operation id, reject a duplicate operation id, delete that exact voucher, and
see the trial balance return to its exact prior value in paise.

Three defects that no fake could have produced were found and fixed that day:

  1. Tally emits INVALID XML. The reserved ledger "Profit & Loss A/c" exports as
     `<PARENT TYPE="String">&#4; Primary</PARENT>` - a reference to U+0004, which
     XML 1.0 forbids. One ledger name cost us the whole chart of accounts.
  2. Every response carries `<CMPINFO>...<VOUCHER>0</VOUCHER>` - a COUNT. A
     whole-document scan for `VOUCHER` found that counter, so an EMPTY company
     looked like a corrupt export. Voucher parsing is scoped to `BODY/DATA`.
  3. Deletion. See A6: the identifier is a TAGNAME/TAGVALUE attribute pair, and
     REMOTEID never resolves for a locally-created voucher.

What is STILL unproven, and why the assumption list below still matters: this
ran against ONE build, ONE company, in Educational mode, with Journal vouchers
and no inventory, GST or bill allocations. Educational mode also refuses voucher
dates outside the 1st, 2nd and 31st - measured, 2026-08-07 REJECTED and
2026-08-31 ACCEPTED - so `tests/test_tally_contract.py`, whose fixture posts on
2026-08-07, CANNOT run unmodified here. Every status in `ASSUMPTIONS` says which
kind of claim it is, and MEASURED means measured in that one environment.

The tests prove that this module is internally consistent, that it parses what
it builds, and that it fails loudly rather than silently. They prove nothing
about TallyPrime.

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

`the narration marker is the identity, everything else is a locator`
    Every write carries `[ACCOUNTANT_DAD:<op_id>]` in its narration (via
    `stamp`). That marker is this application's identity for the voucher, and
    reads, duplicate detection and reversal match on it and on nothing else -
    never an amount, never narration text.

    `MASTERID`, `REMOTEID`, `GUID` and `VCHKEY` are LOCATORS: they say where a
    voucher sits in one company file right now. `MASTERID` is company-local and
    can change when a company is copied, merged or rebuilt, so it is never
    treated as a portable id. Locators are preserved exactly as Tally sent them
    and are used to aim a delete, never to decide what a voucher is.

    A marker that matches two vouchers is an ambiguity, not a choice. The read
    refuses, names the ambiguity, and no destructive action follows.

`fail closed`
    No recorded backup, no write. A dropped response never becomes a second
    voucher: writes are never retried, and every write is preceded by a read and
    followed by a read-back.

`the read-back proves IDENTITY, not presence` (W1's twin, FIXED 2026-08-09)
    `write_voucher` used to ask "is there a voucher carrying my operation id",
    and reported success on any answer that was not `None`. It checked the label
    on the box and never opened it. A Tally that accepted the write and stored a
    DIFFERENT date - which is exactly what Educational mode does to a bill dated
    the 7th - came back as a clean write, and so did one that stored a different
    amount. `pipeline.post` was fixed the same day; the connector was not, and
    the connector is the layer that anything bypassing the pipeline talks to.

    A write now succeeds only when TALLY'S OWN ANSWER shows OUR voucher:
    company, party, date, amount_paise, debit_account and credit_account all
    unchanged, plus an identifier Tally returned. `VERIFIED_FIELDS` is the list.
    `narration` is deliberately not on it - we stamp the marker into it.

    Nine outcomes, all named, in `ReadBackOutcome`. The one that matters most is
    UNKNOWN_OUTCOME: Tally's import answer says a voucher was created and the
    register does not show it. That is NOT a failure. Calling it one invites a
    retry, and a retry after a write that DID land puts two statutory entries in
    somebody's books. It raises `TallyWriteUnknown`, whose `safe_to_retry` is
    False, and it is a different class from `TallyWriteMismatch`.

    None of this sends a new request shape. The verification is built out of the
    `Export`/`Collection` read the connector already makes - see A11 and the
    wedged-instance note for why a third request family is not on the table.

FIRST-INTEGRATION TRAPS
-----------------------
Recorded because they shape the code below, not as folklore.

  * A correct answer on port 9000 does not mean the right company is open.
    Every company-scoped request names its company in `SVCURRENTCOMPANY`, and
    `list_companies` is the cheapest way to see what Tally actually has open.
  * Dependent masters must exist BEFORE an import - ledgers here, and stock
    items and units for the inventory vouchers this connector does not write.
    `write_voucher` checks both ledgers exist first and never lets Tally create
    a master on the fly.
  * Dates are `YYYYMMDD`. No separators, no locale.
  * Import errors can be silent: HTTP 200 with counters that say nothing
    happened. Every counter and every error element is read, an ambiguous
    response is a failure, and a write is believed only after it is read back.
  * A duplicate import needs an idempotency key checked BEFORE the create. Ours
    is the narration marker, checked by a read on the way in.
  * Tally may round to the company's currency decimal settings while we compare
    exact integer paise. A read-back that differs only by rounding is still a
    difference here and will surface as one rather than be absorbed.
  * A voucher can change between the read and the delete when other people are
    in the same company. The delete therefore carries locators from a read taken
    immediately before it, and is verified by another read afterwards.

ASSUMPTIONS ABOUT TALLY
-----------------------
Each one is either CONFIRMED BY REVIEW (an experienced Tally engineer says so),
CHANGED BY REVIEW (the same engineer said the previous shape was wrong), or
UNVERIFIED (nobody has checked). None of these mean "observed against a live
instance". Nothing here has been.

A1  CONFIRMED BY REVIEW. Sign convention. A DEBIT is a NEGATIVE `<AMOUNT>` with
    `ISDEEMEDPOSITIVE=Yes`; a CREDIT is a POSITIVE `<AMOUNT>` with
    `ISDEEMEDPOSITIVE=No`. Our trial balance is debit-positive, so a Tally
    amount is negated to become ours. That negation lives in `_flip_tally_sign`
    and nowhere else. Two guards run before anything is sent, in
    `check_outgoing_legs`: a leg whose flag contradicts its sign is refused
    rather than normalised, and the debits and the credits must total the same
    integer paise. Tally rejects an unbalanced voucher; we never offer it one.
A2  UNVERIFIED. Envelope shapes. `Export`/`Collection` with a TDL `<COLLECTION>`
    block for reads and `Import`/`Data` for writes, as in the prior project.
A3  CHANGED BY REVIEW. Reading nested ledger entries. Comma-separated dotted
    member paths inside `<FETCH>` (`AllLedgerEntries.LedgerName`) are NOT
    reliably honoured across builds. The nested members are therefore requested
    as explicit `<NATIVEMETHOD>` entries - `ALLLEDGERENTRIES.LIST:LEDGERNAME`
    and the same for `AMOUNT` and `ISDEEMEDPOSITIVE` - with the broad
    `ALLLEDGERENTRIES.*` form available as an opt-in diagnostic.
    Some builds export the collection as `LEDGERENTRIES.LIST` rather than
    `ALLLEDGERENTRIES.LIST`. Both names are accepted on read; a single response
    carrying both is an anomaly and raises rather than being mixed silently.
    A voucher that comes back with ZERO ledger entries is an unreadable or
    invalid export - never "a voucher with no legs" - and raises.
    Still UNVERIFIED: which member names any particular build honours, and
    whether `GUID` is fetchable on a Voucher collection.
A4  UNVERIFIED. Response element names: `<COMPANY>`, `<LEDGER>`, `<VOUCHER>`,
    `<ALLLEDGERENTRIES.LIST>`, `<CLOSINGBALANCE>`, and the import result
    `<STATUS>`, `<CREATED>`, `<ALTERED>`, `<DELETED>`, `<IGNORED>`, `<ERRORS>`,
    `<EXCEPTIONS>`, `<LASTVCHID>`, `<LINEERROR>`.
A5  CHANGED BY REVIEW. Identity. `REMOTEID` and `MASTERID` are locators, not
    this application's identity. The narration marker is the identity (see the
    design note above). `MASTERID` is COMPANY-LOCAL: it can change when a
    company is copied, merged or rebuilt, so it is never treated as a portable
    UUID. Any `GUID`, `REMOTEID` or `VCHKEY` Tally returns is preserved exactly
    as a locator. Marker lookup enforces uniqueness: no match is "not found",
    one match is a safe candidate, two or more refuses every destructive action
    and raises, naming the ambiguity.
    Still UNVERIFIED: whether `REMOTEID` is honoured as an external key on
    create and round-tripped back on export.
A6  MEASURED 2026-08-08, and the review's shape was WRONG. Deletion.
    Tally names a voucher for Alter/Cancel/Delete by a TAGNAME/TAGVALUE
    ATTRIBUTE pair - a TDL method name and its value. Child tags are the fields
    to WRITE, never the key to look up by. The working envelope is:
        <VOUCHER DATE="2-Apr-2026" TAGNAME="Master ID" TAGVALUE="3"
                 ACTION="Delete" VCHTYPE="Journal"></VOUCHER>
    with an EMPTY body: a delete resends no ledger entries. The DATE ATTRIBUTE
    is dd-MMM-yyyy while the DATE CHILD TAG is yyyyMMdd; Tally exports the child
    form, so echoing it into the attribute is the natural mistake.

    `REMOTEID` is NOT sent and is no longer required. It is a SYNC-LINEAGE
    field: Tally stamps it on export so it looks like a handle, but a voucher
    created by a local import has no entry in the remote index. Seven shapes
    were tried against the real instance - without REMOTEID Tally said
    "Cannot delete unnamed object: VOUCHER!"; with it, "Voucher does not
    exist!"; and `ACTION="Alter"` + `<ISDELETED>Yes</ISDELETED>` was silently
    ignored, altered=0 deleted=0 errors=0. Tally's own import guidance treats
    REMOTEID as something to STRIP before importing.
        help.tallysolutions.com/article/DeveloperReference/faq/6191.html
        .../integration-capabilities/case_study_1.htm

    `Delete` and `Cancel` remain DIFFERENT operations - `Cancel` leaves a
    cancelled voucher in place, keeping its number - and this connector only
    ever sends `Delete`. The delete is still verified by reading afterwards
    rather than believed: a silent no-op is a real Tally behaviour, not a
    hypothetical one.
A7  UNVERIFIED that `MASTERID` is present on every exported voucher. CONFIRMED
    BY REVIEW that when it is present it is company-local (A5). `Voucher.
    tally_id` is a reference value that falls back to the voucher number; it is
    NOT what a delete aims at.
A8  UNVERIFIED. Closing balances may carry a trailing `Dr`/`Cr` suffix. When
    they do it is treated as authoritative and A1 is not consulted.
A9  UNVERIFIED. Encoding: UTF-8 unless a UTF-16 BOM says otherwise.
A10 CONFIRMED BY REVIEW. A two-legged Journal is valid provided the voucher
    carries `OBJVIEW="Accounting Voucher View"` and
    `<PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>`, the date is
    `YYYYMMDD`, and both ledgers already exist. The first three are in
    `build_voucher_create`; the fourth is checked by `write_voucher` before it
    writes. No party bill allocations, no inventory allocations, no GST fields,
    no invoice-only fields.
A11 MEASURED 2026-08-09, and the answer is NO. The licence mode CANNOT be read
    over the XML gateway on this instance. Every shape was tried against the
    live TallyPrime 7.0 at 192.168.64.2:9000 and every shape failed:

        Export / TYPE=Function / ID=$$LicenseInfo:IsEducationalMode
            <ERRORMSG>Could not find: $$LicenseInfo:IsEducationalMode</ERRORMSG>
            <ERRORMSG>Function Execution Failed!</ERRORMSG>
          - verbatim, and identical for IsEduMode, LicenseInfo, IsLicensedMode
            and SerialNumber.
        Export / TYPE=Data / ID=License Info
            <LINEERROR>Could not find Report 'License Info'!</LINEERROR>
        A custom TDL REPORT/FORM/PART/LINE/FIELD evaluating $$LicenseInfo
            TIMED OUT. Tally hung rather than answered.

    So `read_licence` exists, sends only the shape that FAILS FAST, and never
    sends the shape that hangs. Its answer today is `LicenceMode.UNKNOWN`, which
    is a measurement and not a placeholder, and UNKNOWN is never presented to a
    person as "connected, all good". Educational mode is NEVER inferred from a
    company name, a ledger name, a voucher count or anything else circumstantial
    - an inferred licence mode is an invented one.
A12 UNVERIFIED. Whether a response echoes back the `<SVCURRENTCOMPANY>` it was
    asked for. Some Tally builds echo the static variables in `<DESC>`; nobody
    has checked which. It is read the same way A8's `Dr`/`Cr` suffix is read:
    AUTHORITATIVE WHEN PRESENT, and "we cannot check" when absent. A read-back
    that says it answered for a different company than the one we wrote to is
    WRONG_COMPANY and the write is refused. A read-back that says nothing about
    the company is never treated as agreeing - it is treated as silent, which is
    why `ReadBackVerdict` carries the outcome rather than a bare boolean.
"""

from __future__ import annotations

import codecs
import datetime
import re
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum
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
# what a read-back can conclude
# ---------------------------------------------------------------------------


class ReadBackOutcome(StrEnum):
    """Every conclusion the post-write read-back is allowed to reach.

    Nine names, exhaustive and mutually exclusive. "It went wrong" is not one of
    them: a person reading a refusal has to know WHICH field disagreed, or they
    go through the whole ledger by hand.

    EXACT_MATCH         Tally's own answer shows OUR voucher, field for field.
                        The only outcome a write may succeed on.
    NO_MATCH            The marker found nothing in the register.
    MULTIPLE_MATCHES    The marker found more than one. A5: never resolved by
                        picking one.
    WRONG_COMPANY       The register answered for a different company.
    WRONG_LEDGER        A ledger name differs - the debit, the credit, or the
                        party. All three are ledger names in Tally.
    WRONG_DATE          The stored date is not the date we sent.
    WRONG_AMOUNT        The stored amount is not the paise we sent.
    MALFORMED_RESPONSE  The register answered with something we cannot read, so
                        it is evidence of nothing - least of all absence.
    UNKNOWN_OUTCOME     Tally's import answer says a write happened and the
                        read-back cannot confirm it. NOT a failure. See
                        `TallyWriteUnknown`.
    """

    EXACT_MATCH = "EXACT_MATCH"
    NO_MATCH = "NO_MATCH"
    MULTIPLE_MATCHES = "MULTIPLE_MATCHES"
    WRONG_COMPANY = "WRONG_COMPANY"
    WRONG_LEDGER = "WRONG_LEDGER"
    WRONG_DATE = "WRONG_DATE"
    WRONG_AMOUNT = "WRONG_AMOUNT"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"


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

    A two-legged voucher whose legs do not cancel, one of our own marked
    vouchers that no longer has two legs, an export carrying a voucher with no
    ledger entries at all, a response mixing two names for the same collection,
    or one operation ID matching more than one voucher.
    """


class AmbiguousMarker(TallyDataError):
    """A5. One operation ID matched more than one voucher.

    Its own class so a caller can branch on the ambiguity without reading the
    English. The message is unchanged from when this was a bare `TallyDataError`
    - it already named the locators it could not choose between, and that is the
    part a person needs.
    """

    outcome = ReadBackOutcome.MULTIPLE_MATCHES


class TallyWriteUnverified(TallyRejected):
    """A write went out and the read-back did NOT prove our voucher is stored.

    Under `TallyRejected` deliberately: everything upstream that already fails
    closed on a rejection keeps failing closed, and nothing can read one of these
    as a success. The subclasses are what a caller branches on.

    `safe_to_retry` is False on every one of them, and it is False as a fact
    rather than as caution: this exception cannot exist unless a create was
    already sent, so a retry risks a SECOND statutory entry.
    """

    safe_to_retry = False

    def __init__(self, message: str, verdict: ReadBackVerdict) -> None:
        super().__init__(message)
        self.verdict = verdict

    @property
    def outcome(self) -> ReadBackOutcome:
        return self.verdict.outcome


class TallyWriteMismatch(TallyWriteUnverified):
    """DEFINITE. Tally stored something, and it is not what we sent.

    The marker is on a voucher in the register and one or more of company,
    party, date, amount, debit or credit disagrees. The message names every
    field that differs - "something is wrong" sends a person through their whole
    ledger; "the amount and the party are wrong" does not.
    """


class TallyWriteUnknown(TallyWriteUnverified):
    """UNDECIDED, and it must never be flattened into a failure.

    Tally's import answer said a voucher was created and the read-back cannot
    confirm it. Two worlds fit that evidence: the write landed and we cannot see
    it, or it never happened. They are not the same, and reporting the second
    one invites a retry - which, in the first world, writes the entry TWICE.

    So this is its own class, its message says UNKNOWN rather than failed, and
    `safe_to_retry` is False. A person has to look in Tally.
    """


class MalformedRegisterResponse(TallyWriteUnverified):
    """The read-back answered with something we could not read.

    A body that will not parse, a voucher with no ledger entries, an unreadable
    date. It is not proof the voucher is missing and it is not proof it is
    there. The original parser message is carried through verbatim, because that
    sentence is what a person debugging this actually needs.
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
# The same forbidden bytes, but written as NUMERIC CHARACTER REFERENCES, which
# survive the byte strip above because they are ASCII text. Observed against a
# real TallyPrime 7.0 on 2026-08-08: the reserved ledger "Profit & Loss A/c"
# exports as `<PARENT TYPE="String">&#4; Primary</PARENT>`. XML 1.0 forbids a
# reference to U+0004, so expat rejects the whole document and one unreadable
# ledger name would otherwise cost us the entire chart of accounts.
#
# Dropping the reference keeps every character that carries meaning - here the
# parent group really is "Primary", and the \x04 is Tally's own sort marker.
# Tab, LF and CR are the only sub-0x20 characters XML permits, so they stay.
_ILLEGAL_CHARREF = re.compile(
    r"&#(?:0*(?:[0-8]|1[1-2]|1[4-9]|2[0-9]|3[01])|x0*(?:[0-8bcBC]|[0-1][0-9a-fA-F]));"
)
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
    # Before the bare-ampersand pass, which would otherwise rewrite `&#4;` to
    # `&amp;#4;` and turn an unparseable document into a parseable lie.
    text = _ILLEGAL_CHARREF.sub("", text)
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


def _optional_counter(root: ElementTree.Element, tag: str) -> int | None:
    """One import-result number, or None when Tally did not send the element.

    Absent and zero are different answers, and `<STATUS>` in particular has to
    tell them apart: a missing status is "this build does not send one", a
    status of 0 is "this build says the import failed".
    """
    value = _text_of(root.find(f".//{tag}"))
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise TallyDataError(f"<{tag}> is not a number: {value!r}") from exc


def _counter(root: ElementTree.Element, tag: str) -> int:
    value = _optional_counter(root, tag)
    return 0 if value is None else value


# ---------------------------------------------------------------------------
# building requests
# ---------------------------------------------------------------------------

COLLECTION_COMPANIES = "AD Companies"
COLLECTION_LEDGERS = "AD Ledgers"
COLLECTION_BALANCES = "AD Ledger Balances"
COLLECTION_VOUCHERS = "AD Vouchers"

# A3. Both names for the same nested collection. Accepted on read, never mixed.
LEDGER_ENTRY_TAGS = ("ALLLEDGERENTRIES.LIST", "LEDGERENTRIES.LIST")

# A3. Explicit native methods rather than dotted paths in <FETCH>, which are not
# reliably honoured.
LEDGER_ENTRY_METHODS = (
    "ALLLEDGERENTRIES.LIST:LEDGERNAME",
    "ALLLEDGERENTRIES.LIST:AMOUNT",
    "ALLLEDGERENTRIES.LIST:ISDEEMEDPOSITIVE",
)

# The broad form. Kept for diagnosing a build that honours neither of the above,
# and deliberately not the default: it asks Tally for everything under the
# collection, which is a lot of XML to size-cap and nothing we parse.
LEDGER_ENTRY_METHOD_BROAD = "ALLLEDGERENTRIES.*"


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
    native_methods: tuple[str, ...] = (),
) -> str:
    """One Export/Collection envelope. Assumptions A2 and A3.

    `fetch` carries the flat members. `native_methods` carries anything nested,
    one `<NATIVEMETHOD>` element each, because a dotted path inside `<FETCH>` is
    not reliably honoured (A3).
    """
    fetched = ", ".join(fetch)
    methods = "".join(
        f"<NATIVEMETHOD>{_escaped(method)}</NATIVEMETHOD>" for method in native_methods
    )
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
        f"{methods}"
        "</COLLECTION>"
        "</TDLMESSAGE></TDL>"
        "</DESC></BODY>"
        "</ENVELOPE>"
    )


def build_company_list_request() -> str:
    """List the open companies. The cheapest proof the transport works.

    Also the cheapest proof of the first trap: port 9000 answering says nothing
    about which company is open.
    """
    return _export_collection(COLLECTION_COMPANIES, None, "Company", ("Name",))


def build_ledger_list_request(company: str) -> str:
    """The chart of accounts. Clarifying questions may only offer these."""
    return _export_collection(COLLECTION_LEDGERS, company, "Ledger", ("Name", "Parent"))


def build_closing_balance_request(company: str) -> str:
    """Tally's own closing balances - the trial balance we check reversal against."""
    return _export_collection(
        COLLECTION_BALANCES, company, "Ledger", ("Name", "ClosingBalance")
    )


def build_voucher_list_request(company: str, *, diagnostic: bool = False) -> str:
    """Posted history, with the ledger entries needed to read debit and credit.

    Assumption A3. The flat members go in `<FETCH>`; the ledger entries are
    asked for as explicit `<NATIVEMETHOD>` entries. `diagnostic=True` adds the
    broad `ALLLEDGERENTRIES.*` form, for the first conversation with a build
    that honours neither.
    """
    methods = LEDGER_ENTRY_METHODS
    if diagnostic:
        methods = (*methods, LEDGER_ENTRY_METHOD_BROAD)
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
            "GUID",
            "PartyLedgerName",
        ),
        methods,
    )


@dataclass(frozen=True)
class OutgoingLeg:
    """One ledger entry on the way OUT, already in Tally's own convention.

    `signed_paise` is what goes in `<AMOUNT>`: negative for a debit, positive
    for a credit (A1). `is_deemed_positive` is what goes in
    `<ISDEEMEDPOSITIVE>`. Keeping both explicit is what lets
    `check_outgoing_legs` catch a pair that contradict each other.
    """

    ledger: str
    signed_paise: int
    is_deemed_positive: bool


def check_outgoing_legs(legs: tuple[OutgoingLeg, ...], voucher_id: str) -> None:
    """Assumption A1's two guards, both before anything reaches the wire.

    A contradictory leg is refused, not normalised. Which half of the pair is
    wrong - the flag or the sign - is not ours to guess, and guessing inverts a
    statutory entry that then looks fine.

    The legs must also balance to the exact paise. Tally rejects an unbalanced
    voucher, so sending one is a round trip we already know the answer to.
    """
    for leg in legs:
        if leg.is_deemed_positive and leg.signed_paise > 0:
            raise ValueError(
                f"refusing to write voucher {voucher_id!r}: the leg on "
                f"{leg.ledger!r} is contradictory. ISDEEMEDPOSITIVE=Yes marks a "
                f"debit, which A1 says carries a negative AMOUNT, but this leg "
                f"carries {rupees_from_paise(leg.signed_paise)}."
            )
        if not leg.is_deemed_positive and leg.signed_paise < 0:
            raise ValueError(
                f"refusing to write voucher {voucher_id!r}: the leg on "
                f"{leg.ledger!r} is contradictory. ISDEEMEDPOSITIVE=No marks a "
                f"credit, which A1 says carries a positive AMOUNT, but this leg "
                f"carries {rupees_from_paise(leg.signed_paise)}."
            )

    debits = sum(-leg.signed_paise for leg in legs if leg.is_deemed_positive)
    credits = sum(leg.signed_paise for leg in legs if not leg.is_deemed_positive)
    if debits != credits:
        raise ValueError(
            f"refusing to write voucher {voucher_id!r}: the debits total "
            f"{debits} paise and the credits total {credits} paise. Tally "
            "rejects an unbalanced voucher and so do we."
        )


def _ledger_entry(leg: OutgoingLeg) -> str:
    """The one place Tally's sign convention is written on the way out.

    Assumption A1. A voucher built with these reversed imports cleanly and is
    wrong, which is the worst failure mode available - hence
    `check_outgoing_legs` between here and the wire.
    """
    return (
        "<ALLLEDGERENTRIES.LIST>"
        f"<LEDGERNAME>{_escaped(leg.ledger)}</LEDGERNAME>"
        f"<ISDEEMEDPOSITIVE>{'Yes' if leg.is_deemed_positive else 'No'}"
        "</ISDEEMEDPOSITIVE>"
        f"<AMOUNT>{rupees_from_paise(leg.signed_paise)}</AMOUNT>"
        "</ALLLEDGERENTRIES.LIST>"
    )


def check_amount_is_paise(voucher: Voucher) -> None:
    """A4, FIXED 2026-08-09. The amount is an `int`, by name, or it is refused.

    `_check_writable` tested `<= 0` and never tested the TYPE. A float survived
    to `rupees_from_paise` and was caught one line later by the `:02d` format
    code, whose message - "Unknown format code 'd' for object of type 'float'"
    - names no voucher, no field and no amount. Whoever reads that log at 9pm
    learns nothing about which entry to look at.

    A bool was not caught at all. `bool` IS an `int` in Python, so
    `rupees_from_paise(True)` returns "0.01" without complaint and one paise
    goes on the wire. `isinstance(x, int)` alone would let it through, which is
    why the bool is rejected before the int is accepted.
    """
    # pyright reads `amount_paise: int` and calls both checks unnecessary. It
    # is right about the annotation and wrong about the world: an annotation is
    # not enforced at runtime, this is the boundary to somebody's statutory
    # books, and a float has already reached it once in this repo's history.
    if isinstance(voucher.amount_paise, bool) or not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
        voucher.amount_paise, int
    ):
        raise TallyRejected(
            f"refusing to write voucher {voucher.id!r}: amount_paise is "
            f"{voucher.amount_paise!r}, a {type(voucher.amount_paise).__name__}. "
            "Amounts are integer paise, and anything else has already lost "
            "precision by the time it reaches the wire."
        )


def _check_writable(voucher: Voucher) -> None:
    """Refuse at the boundary. An entry that cannot be represented faithfully
    must never reach the wire, whatever ran upstream."""
    check_amount_is_paise(voucher)
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
    """Import/Data envelope for one two-legged voucher. Assumptions A1 and A10.

    A10's required fields are all here: `OBJVIEW="Accounting Voucher View"`,
    `<PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>`, and a `YYYYMMDD`
    date. A10's fourth condition - both ledgers already exist - cannot be
    checked from a string, so `RealTally.write_voucher` checks it with a read
    before calling this.

    The operation ID goes in two places: the marker inside the narration, which
    is this application's identity for the voucher and what `operation_id_in`
    reads back, and `REMOTEID`, which is a locator Tally may or may not
    round-trip (A5). The narration is the contract; REMOTEID is a convenience.
    """
    _check_writable(voucher)
    if marker_for(operation_id) not in narration:
        raise ValueError(
            f"refusing to write voucher {voucher.id!r} without the "
            f"{marker_for(operation_id)} marker in its narration"
        )

    legs = (
        OutgoingLeg(
            ledger=voucher.debit_account,
            signed_paise=_flip_tally_sign(voucher.amount_paise),
            is_deemed_positive=True,
        ),
        OutgoingLeg(
            ledger=voucher.credit_account,
            signed_paise=voucher.amount_paise,
            is_deemed_positive=False,
        ),
    )
    check_outgoing_legs(legs, voucher.id)

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
        f'<VOUCHER VCHTYPE="{_escaped(voucher_type)}" ACTION="Create" '
        f'OBJVIEW="Accounting Voucher View" '
        f'REMOTEID="{_escaped(operation_id)}">'
        f"<DATE>{stamped_date}</DATE>"
        f"<EFFECTIVEDATE>{stamped_date}</EFFECTIVEDATE>"
        f"<VOUCHERTYPENAME>{_escaped(voucher_type)}</VOUCHERTYPENAME>"
        f"<PARTYLEDGERNAME>{_escaped(voucher.party)}</PARTYLEDGERNAME>"
        f"<NARRATION>{_escaped(narration)}</NARRATION>"
        "<PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>"
        f"{''.join(_ledger_entry(leg) for leg in legs)}"
        "</VOUCHER>"
        "</TALLYMESSAGE>"
        "</DATA>"
        "</BODY>"
        "</ENVELOPE>"
    )


# A6. Every one of these must come from the fresh read, and the delete is
# refused without them rather than sent with a key we assembled ourselves.
#
# REMOTEID was required here until 2026-08-08 and is now deliberately absent.
# Measured against TallyPrime 7.0: REMOTEID is a SYNC-LINEAGE field. Tally
# stamps it on export, so it looks like a handle, but a voucher created by a
# local import has no entry in the remote index and every delete aimed with it
# came back "Voucher does not exist!". Tally's own import guidance treats
# REMOTEID as something to STRIP before importing, not something to send.
DELETE_REQUIRED_KEYS = ("VCHTYPE", "MASTERID")

# Tally identifies a voucher for Alter/Cancel/Delete by a TDL METHOD NAME and
# its value, carried as the TAGNAME/TAGVALUE attribute pair - not by child tags.
# "Master ID" is the only identifier Tally documents as unique on its own.
#   help.tallysolutions.com/article/DeveloperReference/faq/6191.html
#   .../DeveloperReference/integration-capabilities/case_study_1.htm
DELETE_TAGNAME = "Master ID"

# The DATE ATTRIBUTE is dd-MMM-yyyy ("2-Apr-2026"). The DATE CHILD TAG is
# yyyyMMdd. Tally exports the child form, so echoing it into the attribute is
# the natural mistake; it is not the same field.
DELETE_DATE_FORMAT = "%d-%b-%Y"


def build_voucher_delete(
    company: str,
    exported: ExportedVoucher,
    operation_id: str,
) -> str:
    """Import/Data envelope that deletes exactly one voucher. Assumption A6.

    `exported` MUST come from a read taken immediately before this call. DATE,
    VCHTYPE and MASTERID are all read off it. Nothing is cached, remembered from
    an earlier call, or reconstructed: a voucher can change under a concurrent
    user between one read and the next.

    The voucher is named by TAGNAME/TAGVALUE - a TDL method name and its value -
    which is how Tally identifies a voucher for Alter, Cancel and Delete. Child
    tags are the fields to WRITE, not the key to look up by; sending MASTERID as
    a child produced "Cannot delete unnamed object: VOUCHER!" every time.

    The body is deliberately EMPTY. A delete resends no ledger entries: it names
    a voucher and removes it. (Alter is the one that must resend the full entry
    set, because it replaces it.)

    ACTION is `Delete`, which removes the voucher. `Cancel` is a DIFFERENT
    operation: it leaves a cancelled voucher in place, keeping its number. This
    connector never sends `Cancel`, and the two are not interchangeable.

    Aimed by locators only. Never by amount, never by narration text.
    """
    keys = exported.locators
    missing = [key for key in DELETE_REQUIRED_KEYS if not keys.get(key)]
    if missing:
        raise TallyDataError(
            f"cannot delete operation {operation_id!r}: the read taken just now "
            f"supplied no {', '.join(missing)}. A6 needs DATE, VCHTYPE and "
            "MASTERID together, all from that read; a delete aimed with less "
            "than that is a delete aimed at something we cannot name."
        )

    voucher_type = keys["VCHTYPE"]
    # Tally right-aligns numbers on export ("<MASTERID> 1</MASTERID>"), so the
    # value is stripped before it becomes a lookup key.
    master_id = keys["MASTERID"].strip()
    stamped_date = exported.voucher.date.strftime(DELETE_DATE_FORMAT)
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
        f'<VOUCHER DATE="{_escaped(stamped_date)}" '
        f'TAGNAME="{_escaped(DELETE_TAGNAME)}" '
        f'TAGVALUE="{_escaped(master_id)}" '
        f'ACTION="Delete" VCHTYPE="{_escaped(voucher_type)}">'
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
    """The result Tally returns from an Import/Data request.

    `status` is `None` when the response carried no `<STATUS>` at all, which is
    a different answer from a status of 0. Everything else defaults to 0 the way
    a missing counter reads.
    """

    created: int = 0
    altered: int = 0
    deleted: int = 0
    ignored: int = 0
    errors: int = 0
    exceptions: int = 0
    status: int | None = None
    last_vch_id: str | None = None
    line_errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """False for anything that is not an unambiguous success.

        A `<STATUS>` other than 1 is a failure. A status Tally did not send is
        not evidence either way, so it is left to the counters, which the
        callers check: an import that created nothing, altered something, or
        ignored our payload never counts as a success here.
        """
        if self.status is not None and self.status != 1:
            return False
        return self.errors == 0 and self.exceptions == 0 and not self.line_errors

    def summary(self) -> str:
        return (
            f"status={self.status} created={self.created} "
            f"altered={self.altered} deleted={self.deleted} "
            f"ignored={self.ignored} errors={self.errors} "
            f"exceptions={self.exceptions} line_errors={list(self.line_errors)}"
        )


@dataclass(frozen=True)
class ExportedVoucher:
    """One voucher exactly as Tally exported it. Assumption A5.

    `voucher` is the part this system can represent and reason about. `locators`
    is what Tally sent alongside it - MASTERID, REMOTEID, GUID, VCHKEY - kept
    exactly as received, plus VCHTYPE, which A6's delete needs from the same
    read. None of it is identity: the narration marker inside `voucher` is.
    """

    voucher: Voucher
    locators: Mapping[str, str]


@dataclass(frozen=True)
class VoucherPage:
    """What a voucher export gave us, and what it could not give us.

    `skipped` counts vouchers with ledger entries we cannot represent - anything
    other than exactly one debit and one credit. `Voucher` holds one of each.
    They are counted rather than silently dropped, because "this company has 40
    vouchers" and "this company has 40 vouchers we can read" are different
    statements.

    A voucher with NO ledger entries is not counted here at all: that is an
    unreadable export and it raises (A3).

    `company` is the company TALLY said it answered for, read off the static
    variables it echoes back (A12). `None` means this build did not say, which
    is "we cannot check", never "it matched".
    """

    exported: tuple[ExportedVoucher, ...] = ()
    skipped: int = 0
    company: str | None = None

    @property
    def vouchers(self) -> tuple[Voucher, ...]:
        return tuple(item.voucher for item in self.exported)


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

    RESERVED LEDGERS ARE EXCLUDED, and that is the difference between a trial
    balance that balances and one that does not. Measured against a real
    TallyPrime 7.0 on 2026-08-08, after posting a single Rs 1,684.56 expense:

        <LEDGER NAME="AD Test Expense"   RESERVEDNAME="">    -1684.56
        <LEDGER NAME="AD Test Vendor"    RESERVEDNAME="">     1684.56
        <LEDGER NAME="Cash"              RESERVEDNAME="">    (empty)
        <LEDGER NAME="Profit & Loss A/c" RESERVEDNAME="P&L"> -1684.56

    The three real ledgers sum to exactly zero. "Profit & Loss A/c" is not a
    fourth posting - no voucher ever touches it - it is Tally's own running
    aggregate of the revenue and expense ledgers, and its balance is an exact
    MIRROR of the expense leg. Counting it makes the total 1684.56 instead of 0,
    so the double-entry invariant appears violated when the books are perfectly
    fine.

    Tally marks these itself: a derived ledger carries a non-empty RESERVEDNAME
    attribute while an ordinary one carries "". That is the discriminator used
    here, rather than a hardcoded list of names, because the name is localised
    and a hardcoded English string would silently stop matching.

    This is a deliberately narrow exclusion. If a build ever reserves a ledger
    that IS posted to, these balances stop summing to zero - and that is exactly
    what `test_a_real_trial_balance_sums_to_zero` exists to catch. The
    conservation law is the guard, not this function's judgement.
    """
    root = parse_xml(payload, limit)
    balances: dict[str, int] = {}
    for node in root.iter("LEDGER"):
        name = _name_of(node)
        raw = _child_text(node, "CLOSINGBALANCE")
        if name is None or raw is None:
            continue
        if (node.get("RESERVEDNAME") or "").strip():
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
        # The same contradiction `check_outgoing_legs` refuses to send, arriving
        # from the other direction. A1 says a debit is a negative amount with
        # ISDEEMEDPOSITIVE=Yes and a credit a positive amount with No; this leg
        # is neither, so it is refused rather than guessed at. Guessing here
        # silently inverts a statutory entry.
        raise TallyDataError(
            f"ledger {ledger!r}: ISDEEMEDPOSITIVE={flag!r} does not agree with "
            f"AMOUNT={raw_amount!r}, so this leg contradicts A1's sign "
            "convention. Refusing to pick a side."
        )
    return _Leg(ledger=ledger, amount_paise=amount, is_debit=is_debit)


def _exported_keys(node: ElementTree.Element) -> dict[str, str]:
    """The locators Tally sent with one voucher, exactly as sent. A5 and A6.

    Tally puts these on the element or in a child depending on the build and the
    export, so both are read. VCHTYPE rides along because A6's delete needs the
    voucher type from the same read as the rest of the key.
    """
    found: dict[str, str] = {}
    voucher_type = node.get("VCHTYPE") or _child_text(node, "VOUCHERTYPENAME")
    if voucher_type is not None:
        found["VCHTYPE"] = voucher_type
    for key in ("MASTERID", "REMOTEID", "GUID", "VCHKEY"):
        value = node.get(key) or _child_text(node, key)
        if value is not None:
            found[key] = value
    return found


def _date_from_tally(raw: str) -> datetime.date:
    return datetime.datetime.strptime(raw.strip(), "%Y%m%d").date()


def _exported_from(node: ElementTree.Element) -> ExportedVoucher | None:
    """One exported voucher, or None when it is not two-legged.

    Raises when the export carried no ledger entries at all: that is a broken
    read, not a voucher without legs (A3).
    """
    entries = [entry for tag in LEDGER_ENTRY_TAGS for entry in node.iter(tag)]
    if not entries:
        raise TallyDataError(
            "an exported voucher carries no ledger entries at all. A voucher "
            "cannot have no legs, so this is an unreadable or invalid export - "
            "most likely the collection did not honour the request for the "
            "ledger entries (A3). Refusing to read this page as if it were "
            "voucher data."
        )

    legs = [leg for entry in entries if (leg := _parse_leg(entry)) is not None]
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

    locators = _exported_keys(node)
    number = _child_text(node, "VOUCHERNUMBER")
    master_id = locators.get("MASTERID")
    voucher = Voucher(
        id=locators.get("REMOTEID") or master_id or number or "",
        date=date,
        party=_child_text(node, "PARTYLEDGERNAME") or credit.ledger,
        narration=_child_text(node, "NARRATION") or "",
        debit_account=debit.ledger,
        credit_account=credit.ledger,
        amount_paise=abs(debit.amount_paise),
        gst_paise=None,
        tally_id=master_id or number,
        provenance=None,
    )
    return ExportedVoucher(voucher=voucher, locators=locators)


def _refuse_mixed_entry_tags(root: ElementTree.Element) -> None:
    """A3. One build's name or the other's, never both in one response."""
    present = [
        tag for tag in LEDGER_ENTRY_TAGS if next(root.iter(tag), None) is not None
    ]
    if len(present) > 1:
        names = " and ".join(f"<{tag}>" for tag in present)
        raise TallyDataError(
            f"this response carries {names}. A build exports the ledger entries "
            "under one name or the other; a response using both is an anomaly, "
            "and mixing them would silently read half a company's vouchers."
        )


def _voucher_nodes(root: ElementTree.Element) -> list[ElementTree.Element]:
    """The vouchers in the DATA block, and nothing that merely shares the tag.

    Measured against a real TallyPrime 7.0 on 2026-08-08. Every response carries
    a `<CMPINFO>` header whose children are COUNTS, one of which is literally
    `<VOUCHER>0</VOUCHER>`. Scanning the whole document for `VOUCHER` therefore
    picks up that counter, which has no ledger entries, and the two-leg guard
    then refuses the page - so an EMPTY company looked like a corrupt export.

    Scoping to `BODY/DATA` fixes it, because a real voucher only ever appears
    under the DATA collection. If no DATA block exists the answer is no
    vouchers, never "scan everything and hope", since falling back to a
    whole-document scan is exactly the bug this function exists to prevent.
    """
    data = root.find("BODY/DATA")
    if data is None:
        data = root.find("DATA")
    if data is None:
        return []
    return list(data.iter("VOUCHER"))


def parse_vouchers(
    payload: str, limit: int = DEFAULT_MAX_RESPONSE_BYTES
) -> VoucherPage:
    """Every voucher we can represent, plus a count of the ones we cannot.

    A voucher that carries OUR marker and is no longer two-legged raises: it
    means one of our entries was edited in Tally, and bulk-reverse arithmetic
    over it can no longer be trusted.
    """
    root = parse_xml(payload, limit)
    _refuse_mixed_entry_tags(root)
    answered_for = _text_of(root.find(".//SVCURRENTCOMPANY"))
    exported: list[ExportedVoucher] = []
    skipped = 0
    for node in _voucher_nodes(root):
        item = _exported_from(node)
        if item is not None:
            exported.append(item)
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
    return VoucherPage(exported=tuple(exported), skipped=skipped, company=answered_for)


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
        status=_optional_counter(root, "STATUS"),
        last_vch_id=_text_of(root.find(".//LASTVCHID")),
        line_errors=line_errors,
    )


# ---------------------------------------------------------------------------
# proving a write - W1's twin
# ---------------------------------------------------------------------------

#: What must come back UNCHANGED for a write to count as ours. Money, party,
#: date and both legs - every field a person would be harmed by if Tally stored
#: something else. This is the same list `pipeline.VERIFIED_FIELDS` uses, and it
#: is deliberately duplicated rather than imported: the connector must not
#: depend on the pipeline, and a caller that skips the pipeline is exactly the
#: caller this check exists for.
#:
#: `narration` is absent on purpose - we stamp the marker into it, so it is
#: EXPECTED to differ from what the draft carried. `id` and `tally_id` are
#: absent because they are Tally's to assign, not ours to dictate.
VERIFIED_FIELDS: tuple[str, ...] = (
    "party",
    "date",
    "amount_paise",
    "debit_account",
    "credit_account",
)

#: Which named outcome each field's disagreement produces. `party` is a LEDGER
#: name in Tally (`PARTYLEDGERNAME`), so it lands under WRONG_LEDGER with the
#: other two - and the message still names `party` specifically.
_FIELD_OUTCOMES: Mapping[str, ReadBackOutcome] = {
    "party": ReadBackOutcome.WRONG_LEDGER,
    "date": ReadBackOutcome.WRONG_DATE,
    "amount_paise": ReadBackOutcome.WRONG_AMOUNT,
    "debit_account": ReadBackOutcome.WRONG_LEDGER,
    "credit_account": ReadBackOutcome.WRONG_LEDGER,
}

#: When several fields disagree the verdict carries ONE name, and it is the
#: worst one - ordered by what it costs the person who does not notice.
#: Wrong company: the entry is in somebody else's book. Wrong amount: the money
#: is wrong. Wrong date: the filing period is wrong. Wrong ledger: the
#: classification is wrong. Every differing field is named in the text either
#: way; only the headline is ranked.
_OUTCOME_SEVERITY: tuple[ReadBackOutcome, ...] = (
    ReadBackOutcome.WRONG_COMPANY,
    ReadBackOutcome.WRONG_AMOUNT,
    ReadBackOutcome.WRONG_DATE,
    ReadBackOutcome.WRONG_LEDGER,
)


@dataclass(frozen=True)
class ReadBackVerdict:
    """What Tally's own answer proves about one write, and how sure we are.

    `outcome` is the name. `fields` are the bare field names that disagreed, for
    a caller that wants to branch. `detail` is the same information in the words
    a person reads, and it always names the fields rather than saying that
    something is wrong.
    """

    outcome: ReadBackOutcome
    company: str
    operation_id: str
    fields: tuple[str, ...] = ()
    detail: str = ""
    tally_id: str | None = None

    @property
    def confirmed(self) -> bool:
        """True only for EXACT_MATCH. There is no partial proof."""
        return self.outcome is ReadBackOutcome.EXACT_MATCH

    @property
    def safe_to_retry(self) -> bool:
        """Always False, and it is a fact rather than caution.

        A verdict only exists once a create has already gone to Tally. Every
        outcome other than EXACT_MATCH therefore sits somewhere between "it
        landed" and "it did not", and an automatic retry across that gap is how
        one bill becomes two statutory entries.
        """
        return False


def _shown(value: object) -> str:
    """A value as a person reads it. Dates go out ISO, not as a constructor call."""
    if isinstance(value, datetime.date):
        return value.isoformat()
    return repr(value)


def _field_text(name: str, sent: object, back: object) -> str:
    return f"{name}: sent {_shown(sent)}, Tally has {_shown(back)}"


def verify_read_back(
    *,
    company: str,
    sent: Voucher,
    operation_id: str,
    found: Voucher | None,
    found_in_company: str | None = None,
    tally_id: str | None = None,
    unmarked_lookalikes: tuple[str, ...] = (),
) -> ReadBackVerdict:
    """Decide what Tally's answer proves about the voucher we sent. Pure.

    `found` is the voucher the marker lookup returned, or None. `found_in_company`
    is the company Tally SAID it answered for (A12); None means the build did not
    say, which is "cannot check", never "wrong". `unmarked_lookalikes` are
    locators of vouchers in the same register that match our content but carry no
    marker - they are never accepted as proof, and naming them saves somebody a
    manual search.

    Nothing here is a presence check. `found is not None` is where the old
    read-back stopped, and it is the reason a Tally that stored a different date
    or a different amount reported a clean success.
    """
    if found is None:
        detail = f"no voucher in {company!r} carries operation {operation_id!r}"
        if unmarked_lookalikes:
            detail += (
                f". An unmarked voucher in {company!r} matches what we sent field "
                f"for field ({'; '.join(unmarked_lookalikes)}). It is NOT accepted "
                "as proof - the narration marker is this system's identity (A5), "
                "and a voucher that merely matches on content could be somebody's "
                "own hand-typed entry for the same bill. It is named here so a "
                "person knows where to look"
            )
        return ReadBackVerdict(
            outcome=ReadBackOutcome.NO_MATCH,
            company=company,
            operation_id=operation_id,
            detail=detail,
            tally_id=tally_id,
        )

    mismatches: list[tuple[str, str]] = []
    if found_in_company is not None and found_in_company != company:
        mismatches.append(
            (
                "company",
                f"company: wrote to {company!r}, Tally answered for "
                f"{found_in_company!r}",
            )
        )
    mismatches.extend(
        (field, _field_text(field, getattr(sent, field), getattr(found, field)))
        for field in VERIFIED_FIELDS
        if getattr(sent, field) != getattr(found, field)
    )

    if not mismatches:
        return ReadBackVerdict(
            outcome=ReadBackOutcome.EXACT_MATCH,
            company=company,
            operation_id=operation_id,
            detail=(
                f"operation {operation_id!r} is in {company!r} with the party, "
                "date, amount and both ledgers we sent"
            ),
            tally_id=tally_id,
        )

    fields = tuple(name for name, _ in mismatches)
    named = {
        ReadBackOutcome.WRONG_COMPANY if name == "company" else _FIELD_OUTCOMES[name]
        for name in fields
    }
    return ReadBackVerdict(
        outcome=next(o for o in _OUTCOME_SEVERITY if o in named),
        company=company,
        operation_id=operation_id,
        fields=fields,
        detail="; ".join(text for _, text in mismatches),
        tally_id=tally_id,
    )


# ---------------------------------------------------------------------------
# licence mode - A11
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS EVEN THOUGH IT DOES NOT WORK
# --------------------------------------------
# A person on a TallyPrime in Educational mode is on a REAL Tally holding REAL
# books, so "connected, all good" is a misleading thing to tell them: Educational
# mode refuses every voucher date except the 1st, 2nd and 31st (measured
# 2026-08-07 REJECTED, 2026-08-31 ACCEPTED), so their bill dated the 7th is
# turned away by Tally and nothing here would have warned them.
#
# Reading the mode over the XML gateway was tried and does not work on this
# instance - see A11 in the module docstring for the verbatim errors. The code
# below therefore has exactly one job: produce an HONEST UNKNOWN, fast, without
# raising and without hanging, so that the layer above can warn instead of
# reassure. If a future build starts answering, the same code starts measuring.


class LicenceMode(StrEnum):
    """Which licence the Tally on the other end is running under.

    UNKNOWN is a real answer, not a placeholder. It is what this gateway
    actually returns today (A11), and it must never be collapsed into
    EDUCATIONAL (which would invent a restriction) or into LICENSED (which
    would invent a reassurance). Only the second of those is dangerous, and it
    is the one a "default to fine" would produce.
    """

    EDUCATIONAL = "educational"
    LICENSED = "licensed"
    UNKNOWN = "unknown"


# The TDL functions a licence read asks for, as named constants so the request
# that was measured is reproducible rather than remembered.
LICENCE_FUNCTION = "$$LicenseInfo"
LICENCE_IS_EDUCATIONAL = "IsEducationalMode"
LICENCE_IS_LICENSED = "IsLicensedMode"
LICENCE_SERIAL_NUMBER = "SerialNumber"

LICENCE_NEVER_READ = "the licence mode has not been read"


def build_licence_request(member: str) -> str:
    """Export/Function envelope asking Tally to evaluate one $$LicenseInfo member.

    This is the ONLY shape sent, and it is chosen for how it FAILS. Measured
    2026-08-09 against the live instance, it comes back immediately with
    `<ERRORMSG>Could not find: ...</ERRORMSG>`. The alternative - a custom TDL
    report that evaluates `$$LicenseInfo` inside a report context - made Tally
    HANG, and a startup path that can hang is worse than one that cannot answer.
    So the shape that hangs is not built here at all.
    """
    return (
        "<ENVELOPE>"
        "<HEADER>"
        "<VERSION>1</VERSION>"
        "<TALLYREQUEST>Export</TALLYREQUEST>"
        "<TYPE>Function</TYPE>"
        f"<ID>{_escaped(f'{LICENCE_FUNCTION}:{member}')}</ID>"
        "</HEADER>"
        "<BODY><DESC>"
        f"{_static_variables(None)}"
        "</DESC></BODY>"
        "</ENVELOPE>"
    )


@dataclass(frozen=True)
class FunctionAnswer:
    """What an Export/Function request came back with.

    `result` is the value Tally computed. `errors` is every `<ERRORMSG>` and
    `<LINEERROR>` it sent instead. Both empty is a third answer - Tally replied
    with nothing we can use - and it is treated exactly like an error, because
    "no value" is not a value.
    """

    result: str | None = None
    errors: tuple[str, ...] = ()


def parse_function_answer(
    payload: str, limit: int = DEFAULT_MAX_RESPONSE_BYTES
) -> FunctionAnswer:
    """Read one Export/Function reply. A reply carrying an error has NO result.

    Nulling the result when an error is present is deliberate. A response that
    both complains and offers a value is a response we do not understand, and
    the safe reading of a response we do not understand is that it told us
    nothing.
    """
    root = parse_xml(payload, limit)
    errors = tuple(
        text
        for tag in ("ERRORMSG", "LINEERROR")
        for node in root.iter(tag)
        if (text := _text_of(node)) is not None
    )
    result = None if errors else _text_of(root.find(".//RESULT"))
    return FunctionAnswer(result=result, errors=errors)


_YES_WORDS = frozenset({"yes", "true", "1"})
_NO_WORDS = frozenset({"no", "false", "0"})


def yes_no_or_unknown(value: str | None) -> bool | None:
    """Tally's Yes/No as a boolean, or None when it is neither.

    None is NOT False. "Tally said No" and "we could not read it" are different
    facts, and treating the second as the first is how an unread licence turns
    into a confident all-clear.
    """
    if value is None:
        return None
    word = value.strip().lower()
    if word in _YES_WORDS:
        return True
    if word in _NO_WORDS:
        return False
    return None


@dataclass(frozen=True)
class LicenceInfo:
    """A licence read, with the raw answers kept beside the conclusion.

    `detail` says how we know. It is written for a log, not for a person - the
    web app turns the mode into sentences a twelve-year-old can read.
    """

    mode: LicenceMode = LicenceMode.UNKNOWN
    is_educational: bool | None = None
    is_licensed: bool | None = None
    serial_number: str | None = None
    detail: str = LICENCE_NEVER_READ


def licence_from_answers(
    *,
    is_educational: bool | None,
    is_licensed: bool | None,
    serial_number: str | None,
    detail: str,
) -> LicenceInfo:
    """Three measured answers in, one mode out. Fails closed on every path.

    EDUCATIONAL needs Tally to have said Yes to `IsEducationalMode`.

    LICENSED needs BOTH a No to educational AND a Yes to licensed. One positive
    statement is not enough to tell somebody their books are safe to type into,
    and requiring both is what makes a HALF-read fall to UNKNOWN rather than to
    the reassuring answer.

    Everything else - a missing answer, an unreadable answer, a pair that do not
    agree - is UNKNOWN.
    """
    if is_educational is True:
        mode = LicenceMode.EDUCATIONAL
    elif is_educational is False and is_licensed is True:
        mode = LicenceMode.LICENSED
    else:
        mode = LicenceMode.UNKNOWN
    return LicenceInfo(
        mode=mode,
        is_educational=is_educational,
        is_licensed=is_licensed,
        serial_number=serial_number,
        detail=detail,
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

    `voucher_type` is the type used on CREATE. A delete never reads it: A6 takes
    the voucher type from the read that precedes it.
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


def _unmarked_lookalikes(page: VoucherPage, sent: Voucher) -> tuple[str, ...]:
    """Vouchers in the same register that match ours field for field and carry
    no marker of ours.

    Never proof - the marker is the identity (A5) and an unmarked match could
    just as easily be the person's own hand-typed entry for the same bill. It is
    collected so a refusal can point at it instead of sending somebody through
    the whole ledger.
    """
    return tuple(
        _locators_text(item)
        for item in page.exported
        if operation_id_in(item.voucher.narration) is None
        and all(
            getattr(item.voucher, field) == getattr(sent, field)
            for field in VERIFIED_FIELDS
        )
    )


def _locators_text(item: ExportedVoucher) -> str:
    if not item.locators:
        return "no locators"
    return ", ".join(f"{key}={value}" for key, value in item.locators.items())


class RealTally:
    """TallyClient over XML/HTTP. See the module docstring before trusting it.

    Identity is the narration marker and nothing else (A5).
    `read_by_operation_id` matches that marker, exactly as `FakeTally` does, by
    scanning the company's vouchers. A TDL filter would be faster; it would also
    mean that a filter Tally silently does not honour reads as "this operation
    was never written", which on the write path is a duplicate voucher. Scanning
    cannot fail that way, and it is what lets two vouchers sharing one marker be
    seen as the ambiguity they are instead of picked between.
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

    # ---- the licence read (A11) --------------------------------------------

    def _licence_member(self, member: str) -> FunctionAnswer:
        """One licence question, with EVERY failure turned into an answer.

        Nothing escapes this method. A transport error, a socket error, an
        unparseable body and a refusal all become `errors`, because the caller's
        job is to end up with a mode and never with an exception: a licence
        probe that can raise is a licence probe that can stop the app starting.

        `retry=False` on purpose. This is a read, so retrying would be safe, but
        one bounded round trip is the whole point - the wait is capped by
        `TallyConfig.timeout_seconds` and is never multiplied by the retry
        count.
        """
        try:
            payload = self._transport.send(build_licence_request(member), retry=False)
        except (TallyError, OSError) as exc:
            return FunctionAnswer(errors=(f"{type(exc).__name__}: {exc}",))
        try:
            return parse_function_answer(payload, self._limit)
        except TallyError as exc:
            return FunctionAnswer(errors=(f"{type(exc).__name__}: {exc}",))

    def _licence_unreadable(self, member: str, answer: FunctionAnswer) -> str:
        said = "; ".join(answer.errors) if answer.errors else "nothing we could read"
        return (
            f"{self.config.url} did not answer {LICENCE_FUNCTION}:{member} - it "
            f"said: {said}. The licence mode is therefore unknown, and unknown "
            "is not assumed to be fine."
        )

    def read_licence(self) -> LicenceInfo:
        """Which licence this Tally runs under, or an honest UNKNOWN. A11.

        NEVER RAISES. Measured 2026-08-09, this returns UNKNOWN against the live
        instance, because the gateway does not answer `$$LicenseInfo` at all.
        That is the point: the caller gets a mode it can warn about instead of a
        crash at startup or a cheerful default.

        AT MOST ONE ROUND TRIP when the gateway does not understand the
        question. The first read is `IsEducationalMode`, and a Tally that cannot
        answer that one will not answer the other two either, so the other two
        are not sent. On this instance the whole method is one fast error.
        """
        educational = self._licence_member(LICENCE_IS_EDUCATIONAL)
        if educational.result is None:
            return licence_from_answers(
                is_educational=None,
                is_licensed=None,
                serial_number=None,
                detail=self._licence_unreadable(LICENCE_IS_EDUCATIONAL, educational),
            )

        licensed = self._licence_member(LICENCE_IS_LICENSED)
        serial = self._licence_member(LICENCE_SERIAL_NUMBER)
        return licence_from_answers(
            is_educational=yes_no_or_unknown(educational.result),
            is_licensed=yes_no_or_unknown(licensed.result),
            serial_number=serial.result,
            detail=(
                f"{self.config.url} answered "
                f"{LICENCE_FUNCTION}:{LICENCE_IS_EDUCATIONAL}="
                f"{educational.result!r}, "
                f"{LICENCE_FUNCTION}:{LICENCE_IS_LICENSED}={licensed.result!r}, "
                f"{LICENCE_FUNCTION}:{LICENCE_SERIAL_NUMBER}={serial.result!r}"
            ),
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

    def _marker_lookup(
        self, company: str, operation_id: str
    ) -> tuple[ExportedVoucher | None, VoucherPage]:
        """A5. The marker must identify at most one voucher.

        None means not found. One match is a safe candidate. Two or more is an
        ambiguity, and every destructive action is refused rather than aimed at
        a coin flip.

        The whole page comes back with the answer, because the read-back needs
        two more things off the SAME read: the company Tally answered for, and
        any unmarked voucher that matches our content. Both come free from a
        read already taken; neither is worth a second request.
        """
        page = self.read_vouchers_page(company)
        matches = [
            item
            for item in page.exported
            if operation_id_in(item.voucher.narration) == operation_id
        ]
        if len(matches) > 1:
            where = "; ".join(_locators_text(item) for item in matches)
            raise AmbiguousMarker(
                f"operation {operation_id!r} matches {len(matches)} vouchers in "
                f"{company!r} ({where}). The narration marker is this system's "
                "identity and it has to be unique. Refusing to read one back or "
                "delete any of them: a person has to decide which is real."
            )
        return (matches[0] if matches else None), page

    def _read_exported_by_operation_id(
        self, company: str, operation_id: str
    ) -> ExportedVoucher | None:
        return self._marker_lookup(company, operation_id)[0]

    def read_by_operation_id(self, company: str, operation_id: str) -> Voucher | None:
        found = self._read_exported_by_operation_id(company, operation_id)
        return None if found is None else found.voucher

    def list_our_vouchers(self, company: str) -> tuple[Voucher, ...]:
        return tuple(
            voucher
            for voucher in self.read_vouchers(company)
            if operation_id_in(voucher.narration) is not None
        )

    # ---- writes ------------------------------------------------------------

    def _check_ledgers_exist(
        self, company: str, voucher: Voucher, operation_id: str
    ) -> None:
        """A10's fourth condition, and the second first-integration trap.

        Tally does not create an accounting master on the fly for an imported
        voucher: a ledger that does not exist makes the import fail, and it can
        fail silently. Names are compared exactly, so a case difference is a
        refusal rather than a guess.
        """
        accounts = self.read_accounts(company)
        missing = tuple(
            name
            for name in (voucher.debit_account, voucher.credit_account)
            if name not in accounts
        )
        if missing:
            raise TallyDataError(
                f"refusing to write operation {operation_id!r} to {company!r}: "
                f"the ledger(s) {', '.join(repr(name) for name in missing)} do "
                "not exist there. Tally will not create them for us, so the "
                "import would be rejected or silently ignored. Create them in "
                "Tally first."
            )

    def _prove_it_is_ours(
        self,
        company: str,
        voucher: Voucher,
        operation_id: str,
        result: ImportResult,
    ) -> tuple[ReadBackVerdict, Voucher | None]:
        """Read the register back and say what it PROVES. W1's twin, fixed.

        This used to be four lines that asked whether anything at all carried
        our marker, and reported success on any answer that was not None. It
        checked the label on the box and never opened it. A Tally that accepted
        the write and stored a different date - which is exactly what a
        TallyPrime in Educational mode does to a bill dated the 7th - came back
        as a clean write.

        The box is opened here. The read is the same `Export`/`Collection` the
        connector already makes; no new request shape is introduced, because a
        custom TDL report wedged a live TallyPrime on 2026-08-09 and identity
        checking is not worth a third request family.

        A response we cannot read is MALFORMED_RESPONSE, never absence: "the
        register did not parse" and "the voucher is not there" are different
        facts and only one of them is grounds for anything.
        """
        try:
            found, page = self._marker_lookup(company, operation_id)
        except AmbiguousMarker:
            # MULTIPLE_MATCHES. Already named, already refuses every destructive
            # action, and its message already says which vouchers it could not
            # choose between. Nothing to add.
            raise
        except (TallyResponseError, TallyDataError) as exc:
            raise MalformedRegisterResponse(
                f"MALFORMED_RESPONSE for operation {operation_id!r}: Tally "
                f"answered the write, and the register read afterwards could "
                f"not be read - {exc} That is not evidence the voucher is "
                "missing and not evidence it is there, so nothing is recorded "
                "as posted and this must not be written again until a person "
                f"has looked in {company!r}.",
                ReadBackVerdict(
                    outcome=ReadBackOutcome.MALFORMED_RESPONSE,
                    company=company,
                    operation_id=operation_id,
                    detail=str(exc),
                ),
            ) from exc

        written = None if found is None else found.voucher
        verdict = verify_read_back(
            company=company,
            sent=voucher,
            operation_id=operation_id,
            found=written,
            found_in_company=page.company,
            tally_id=(written.tally_id if written is not None else None)
            or result.last_vch_id,
            unmarked_lookalikes=_unmarked_lookalikes(page, voucher),
        )
        return verdict, written

    def write_voucher(
        self, company: str, voucher: Voucher, operation_id: str
    ) -> WriteResult:
        """Write one marked voucher, then prove TALLY STORED OURS by reading it
        back.

        The read-back is an IDENTITY check, not a presence check. See
        `_prove_it_is_ours`. Three named refusals can come out of it:

          * `TallyWriteMismatch` - a voucher carrying our marker is there and it
            is a different voucher. Definite, and every differing field is named.
          * `TallyWriteUnknown` - Tally said it created one and the register does
            not show it. UNDECIDED, and never to be retried automatically.
          * `MalformedRegisterResponse` - the register answered with something
            unreadable, which is evidence of nothing.
        """
        if not self._backups.has_backup(company):
            raise CompanyNotBackedUp(
                f"{company!r} has no recorded backup; refusing to write"
            )
        self._check_ledgers_exist(company, voucher, operation_id)
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
        if result.ignored:
            raise TallyRejected(
                f"Tally ignored {result.ignored} part(s) of operation "
                f"{operation_id!r}. One voucher went out, so we cannot tell "
                f"what was dropped: {result.summary()}"
            )

        verdict, written = self._prove_it_is_ours(
            company, voucher, operation_id, result
        )
        if verdict.outcome is ReadBackOutcome.NO_MATCH:
            raise TallyWriteUnknown(
                f"UNKNOWN_OUTCOME for operation {operation_id!r}: it was not "
                f"found in {company!r} after the write. Tally's import answer "
                f"said a voucher was created ({result.summary()}) and the "
                "register does not show it, so - whatever HTTP said - this is "
                "not proof either way. The voucher may have landed somewhere "
                "this connector cannot read it, or it may never have been "
                "written. That is UNKNOWN, and it is NOT the same as failed: it "
                "must never be retried automatically, because a retry after a "
                "write that DID land puts two statutory entries in somebody's "
                f"books. A person has to look in {company!r}. {verdict.detail}",
                replace(verdict, outcome=ReadBackOutcome.UNKNOWN_OUTCOME),
            )
        if not verdict.confirmed:
            raise TallyWriteMismatch(
                f"{verdict.outcome.value} for operation {operation_id!r}: a "
                f"voucher carrying our marker is in {company!r} and it is NOT "
                f"the one we sent - {verdict.detail}. Nothing is recorded as "
                "posted. The entry is in Tally and has to be checked by hand; "
                "writing it again would add a second one.",
                verdict,
            )

        tally_id = (
            written.tally_id if written is not None else None
        ) or result.last_vch_id
        if tally_id is None:
            raise TallyDataError(
                f"operation {operation_id!r} was written but Tally reported no "
                "MASTERID for it, so it cannot be reversed later"
            )
        return WriteResult(
            operation_id=operation_id, tally_id=tally_id, narration=narration
        )

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        """Delete exactly the voucher carrying this operation ID. A6.

        False means it was not there. True means it was there and is verifiably
        gone. Anything else raises: reversal is never reported as succeeding
        because Tally said so.

        The backup gate was added here 2026-08-09, G5.2. `write_voucher` has
        enforced it since this file was written; this path never did. A delete
        is the more destructive of the two operations, so the ungated one was
        the dangerous one — a bulk reverse could empty a company nobody had
        backed up while a single write to that same company was refused.
        """
        if not self._backups.has_backup(company):
            raise CompanyNotBackedUp(
                f"{company!r} has no recorded backup; refusing to reverse"
            )
        # This read IS the fresh read the delete is built from - taken here,
        # immediately before the delete, and never carried in from a caller, a
        # cache or an earlier call. Another user can move a voucher under us.
        fresh = self._read_exported_by_operation_id(company, operation_id)
        if fresh is None:
            return False

        payload = build_voucher_delete(company, fresh, operation_id)
        result = parse_import_response(
            self._transport.send(payload, retry=False), self._limit
        )
        if not result.ok:
            raise TallyRejected(
                f"Tally refused to delete operation {operation_id!r}: "
                f"{result.summary()}"
            )
        if result.deleted < 1:
            raise TallyRejected(
                f"Tally accepted the deletion of operation {operation_id!r} and "
                f"deleted nothing: {result.summary()}"
            )
        if self.read_by_operation_id(company, operation_id) is not None:
            raise TallyRejected(
                f"Tally reported {result.summary()} for the deletion of "
                f"operation {operation_id!r}, but the voucher is still there"
            )
        return True

    def backed_up(self, company: str) -> bool:
        """Whether a backup has been recorded for this company. Read-only.

        Reads the same `BackupLog` both write paths gate on, so the answer a
        preview reports and the answer the write enforces cannot drift.
        """
        return self._backups.has_backup(company)

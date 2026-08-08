"""What can actually be tested about `real.py` without a Windows VM.

WHAT THESE TESTS PROVE
    The pure parts are right: money crosses the boundary as integer paise, the
    envelopes have the structure we intend, the parsers turn recorded XML into
    the frozen types, hostile XML is refused, and a transport failure surfaces
    as an exception rather than as an empty result.

    The client logic is right: `RealTally` driven by an in-memory Tally
    simulator passes the same `tests/test_tally_contract.py` tests that
    `FakeTally` passes. Those tests are imported and re-bound here, not
    rewritten, so the two backends are held to one contract.

WHAT THEY DO NOT PROVE
    That any of it works against TallyPrime. `_TallySim` answers the envelopes
    `real.py` builds, in the shapes `real.py` expects, so it agrees with every
    assumption A1-A10 in that module by construction. A simulator that shares
    your hypotheses cannot falsify them. Only a live instance can.

    Nothing here opens a socket except `test_post_bytes_talks_to_a_real_socket`,
    which talks to a one-shot loopback server to prove the opener works at all.
"""

from __future__ import annotations

import datetime
import re
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from xml.etree import ElementTree  # nosec B405 - only used to read our own output

import pytest

from accountant.schema import Voucher
from accountant.tallyio import real
from accountant.tallyio.client import (
    CompanyNotBackedUp,
    TallyClient,
    marker_for,
    new_operation_id,
    operation_id_in,
    stamp,
)
from tests import test_tally_contract as contract

COMPANY = contract.COMPANY
ACCOUNTS = contract.ACCOUNTS


# ===========================================================================
# a Tally-shaped simulator
# ===========================================================================


def _esc(text: str) -> str:
    for raw, entity in (
        ("&", "&amp;"),
        ("<", "&lt;"),
        (">", "&gt;"),
        ('"', "&quot;"),
    ):
        text = text.replace(raw, entity)
    return text


def _leg_xml(ledger: str, amount_paise: int, deemed_positive: str) -> str:
    return (
        "<ALLLEDGERENTRIES.LIST>"
        f"<LEDGERNAME>{_esc(ledger)}</LEDGERNAME>"
        f"<ISDEEMEDPOSITIVE>{deemed_positive}</ISDEEMEDPOSITIVE>"
        f"<AMOUNT>{real.rupees_from_paise(amount_paise)}</AMOUNT>"
        "</ALLLEDGERENTRIES.LIST>"
    )


def import_response(
    *,
    created: int = 0,
    altered: int = 0,
    deleted: int = 0,
    ignored: int = 0,
    errors: int = 0,
    exceptions: int = 0,
    last_vch_id: str | None = None,
    line_errors: Sequence[str] = (),
) -> str:
    """The counters Tally is assumed to return from Import/Data (A4)."""
    last = f"<LASTVCHID>{_esc(last_vch_id)}</LASTVCHID>" if last_vch_id else ""
    lines = "".join(f"<LINEERROR>{_esc(t)}</LINEERROR>" for t in line_errors)
    return (
        "<ENVELOPE><HEADER><VERSION>1</VERSION></HEADER>"
        "<BODY><DATA><IMPORTRESULT>"
        f"<CREATED>{created}</CREATED>"
        f"<ALTERED>{altered}</ALTERED>"
        f"<DELETED>{deleted}</DELETED>"
        f"<IGNORED>{ignored}</IGNORED>"
        f"<ERRORS>{errors}</ERRORS>"
        f"<EXCEPTIONS>{exceptions}</EXCEPTIONS>"
        f"{last}{lines}"
        "</IMPORTRESULT></DATA></BODY></ENVELOPE>"
    )


@dataclass
class SimVoucher:
    master_id: str
    remote_id: str
    date: datetime.date
    party: str
    narration: str
    debit_account: str
    credit_account: str
    amount_paise: int

    def to_xml(self) -> str:
        return (
            f'<VOUCHER REMOTEID="{_esc(self.remote_id)}" '
            f'MASTERID="{_esc(self.master_id)}" VCHTYPE="Journal">'
            f"<DATE>{self.date.strftime('%Y%m%d')}</DATE>"
            f"<VOUCHERNUMBER>{_esc(self.master_id)}</VOUCHERNUMBER>"
            "<VOUCHERTYPENAME>Journal</VOUCHERTYPENAME>"
            f"<PARTYLEDGERNAME>{_esc(self.party)}</PARTYLEDGERNAME>"
            f"<NARRATION>{_esc(self.narration)}</NARRATION>"
            # Assumption A1: a debit is a negative amount, ISDEEMEDPOSITIVE=Yes.
            + _leg_xml(self.debit_account, -self.amount_paise, "Yes")
            + _leg_xml(self.credit_account, self.amount_paise, "No")
            + "</VOUCHER>"
        )


@dataclass
class SimCompany:
    ledgers: list[str] = field(default_factory=list[str])
    vouchers: list[SimVoucher] = field(default_factory=list[SimVoucher])
    raw_vouchers: list[str] = field(default_factory=list[str])
    next_master: int = 1


class TallySim:
    """Answers the envelopes `real.py` builds. Speaks XML, holds no opinions
    beyond the assumptions `real.py` already documents."""

    def __init__(self) -> None:
        self.companies: dict[str, SimCompany] = {}
        self.sent: list[str] = []
        self.retry_flags: list[bool] = []
        self.balances_carry_dr_cr = False
        self.import_override: str | None = None
        self.swallow_deletes = False

    # ---- setup ------------------------------------------------------------

    def add_company(self, name: str, ledgers: Sequence[str] = ()) -> None:
        self.companies[name] = SimCompany(ledgers=list(ledgers))

    def seed(
        self,
        company: str,
        *,
        narration: str,
        amount_paise: int = 100_000,
        debit: str = "Purchases",
        credit: str = "Cash",
        party: str = "Sharma Traders",
    ) -> None:
        """A voucher a person typed. No REMOTEID, no marker."""
        co = self.companies[company]
        co.vouchers.append(
            SimVoucher(
                master_id=f"M{co.next_master}",
                remote_id="",
                date=datetime.date(2026, 8, 1),
                party=party,
                narration=narration,
                debit_account=debit,
                credit_account=credit,
                amount_paise=amount_paise,
            )
        )
        co.next_master += 1

    # ---- transport --------------------------------------------------------

    def send(self, payload: str, *, retry: bool) -> str:
        self.sent.append(payload)
        self.retry_flags.append(retry)
        root = real.parse_xml(payload)
        kind = _required(root, ".//TALLYREQUEST")
        company = _optional(root, ".//SVCURRENTCOMPANY")
        if kind == "Export":
            return self._export(_required(root, ".//ID"), company)
        return self._import(root, company)

    def _company(self, name: str | None) -> SimCompany:
        assert name is not None, "every request but the company list names a company"
        return self.companies[name]

    def _export(self, collection_id: str, company: str | None) -> str:
        if collection_id == real.COLLECTION_COMPANIES:
            body = "".join(
                f'<COMPANY NAME="{_esc(n)}"><NAME>{_esc(n)}</NAME></COMPANY>'
                for n in self.companies
            )
        elif collection_id == real.COLLECTION_LEDGERS:
            body = "".join(
                f'<LEDGER NAME="{_esc(n)}"><PARENT>Direct Expenses</PARENT></LEDGER>'
                for n in self._company(company).ledgers
            )
        elif collection_id == real.COLLECTION_BALANCES:
            body = "".join(
                f'<LEDGER NAME="{_esc(n)}">'
                f"<CLOSINGBALANCE>{self._closing(bal)}</CLOSINGBALANCE>"
                "</LEDGER>"
                for n, bal in self._our_balances(self._company(company)).items()
            )
        elif collection_id == real.COLLECTION_VOUCHERS:
            co = self._company(company)
            body = "".join(v.to_xml() for v in co.vouchers) + "".join(co.raw_vouchers)
        else:  # pragma: no cover - a collection real.py never asks for
            raise AssertionError(f"unexpected collection {collection_id!r}")
        return (
            "<ENVELOPE><BODY><DATA><COLLECTION>"
            f"{body}"
            "</COLLECTION></DATA></BODY></ENVELOPE>"
        )

    @staticmethod
    def _our_balances(co: SimCompany) -> dict[str, int]:
        """Debit positive, the way `FakeTally.trial_balance` counts."""
        balances = dict.fromkeys(co.ledgers, 0)
        for v in co.vouchers:
            balances[v.debit_account] = (
                balances.get(v.debit_account, 0) + v.amount_paise
            )
            balances[v.credit_account] = (
                balances.get(v.credit_account, 0) - v.amount_paise
            )
        return balances

    def _closing(self, ours: int) -> str:
        if self.balances_carry_dr_cr:
            return f"{real.rupees_from_paise(abs(ours))} {'Dr' if ours >= 0 else 'Cr'}"
        return real.rupees_from_paise(-ours)

    def _import(self, root: ElementTree.Element, company: str | None) -> str:
        if self.import_override is not None:
            return self.import_override
        node = root.find(".//VOUCHER")
        assert node is not None, "an Import envelope must carry a VOUCHER"
        action = node.get("ACTION")
        remote_id = node.get("REMOTEID") or ""
        co = self._company(company)

        if action == "Delete":
            if self.swallow_deletes:
                return import_response(deleted=1)
            keep = [v for v in co.vouchers if v.remote_id != remote_id]
            deleted = len(co.vouchers) - len(keep)
            co.vouchers = keep
            return import_response(deleted=deleted)

        assert action == "Create", f"unexpected voucher action {action!r}"
        debit, credit, amount = _legs_of(node)
        master_id = f"M{co.next_master}"
        co.next_master += 1
        co.vouchers.append(
            SimVoucher(
                master_id=master_id,
                remote_id=remote_id,
                date=datetime.datetime.strptime(
                    _required(node, "DATE"), "%Y%m%d"
                ).date(),
                party=_optional(node, "PARTYLEDGERNAME") or "",
                narration=_optional(node, "NARRATION") or "",
                debit_account=debit,
                credit_account=credit,
                amount_paise=amount,
            )
        )
        return import_response(created=1, last_vch_id=master_id)


def _required(node: ElementTree.Element, path: str) -> str:
    found = node.find(path)
    assert found is not None and found.text is not None, f"missing {path}"
    return found.text.strip()


def _optional(node: ElementTree.Element, path: str) -> str | None:
    found = node.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _legs_of(node: ElementTree.Element) -> tuple[str, str, int]:
    """Read the two legs the connector built, checking assumption A1 on the way."""
    debit: tuple[str, int] | None = None
    credit: tuple[str, int] | None = None
    for entry in node.iter("ALLLEDGERENTRIES.LIST"):
        ledger = _required(entry, "LEDGERNAME")
        amount = real.paise_from_rupees(_required(entry, "AMOUNT"))
        if _required(entry, "ISDEEMEDPOSITIVE") == "Yes":
            assert amount < 0, "A1: a debit leg must carry a negative amount"
            debit = (ledger, -amount)
        else:
            assert amount > 0, "A1: a credit leg must carry a positive amount"
            credit = (ledger, amount)
    assert debit is not None and credit is not None, "a voucher needs both legs"
    assert debit[1] == credit[1], "the legs must cancel"
    return debit[0], credit[0], debit[1]


class ScriptedTransport:
    """Hands back canned replies, or raises. For failure paths only."""

    def __init__(self, replies: Sequence[str | Exception]) -> None:
        self._replies = list(replies)
        self.calls = 0

    def send(self, payload: str, *, retry: bool) -> str:
        del payload, retry
        self.calls += 1
        reply = self._replies.pop(0) if self._replies else RuntimeError("no reply left")
        if isinstance(reply, Exception):
            raise reply
        return reply


# ===========================================================================
# the contract, run against RealTally
# ===========================================================================


@pytest.fixture
def sim() -> TallySim:
    t = TallySim()
    t.add_company(COMPANY, ACCOUNTS)
    return t


@pytest.fixture
def client(sim: TallySim) -> TallyClient:
    """The fixture `tests/test_tally_contract.py` asks for, backed by RealTally."""
    return real.RealTally(
        transport=sim, backups=real.RecordedBackups(frozenset({COMPANY}))
    )


# The contract tests, imported rather than retyped. One contract, two backends.
test_real_satisfies_the_protocol = contract.test_fake_satisfies_the_protocol
test_real_reads_the_chart_of_accounts = contract.test_reads_the_chart_of_accounts
test_real_empty_company_is_flat = (
    contract.test_empty_company_has_no_vouchers_and_a_flat_trial_balance
)
test_real_written_voucher_carries_the_marker = (
    contract.test_every_written_voucher_carries_the_marker
)
test_real_written_voucher_findable_by_operation_id = (
    contract.test_written_voucher_is_findable_by_operation_id_alone
)
test_real_duplicate_operation_id_is_rejected = (
    contract.test_duplicate_operation_id_is_rejected
)
test_real_rejected_retry_creates_no_second_voucher = (
    contract.test_a_rejected_retry_does_not_create_a_second_voucher
)
test_real_read_back_returns_what_was_written = (
    contract.test_read_back_returns_what_was_written
)
test_real_read_back_of_unknown_is_none = (
    contract.test_read_back_of_an_unknown_operation_is_none
)
test_real_reverse_restores_the_trial_balance = (
    contract.test_reverse_restores_the_exact_prior_trial_balance
)
test_real_reverse_all_restores_the_trial_balance = (
    contract.test_reverse_all_restores_the_exact_prior_trial_balance
)
test_real_reverse_targets_the_exact_voucher = (
    contract.test_reverse_targets_the_exact_voucher_not_a_lookalike
)
test_real_reversing_unknown_changes_nothing = (
    contract.test_reversing_an_unknown_operation_reports_false_and_changes_nothing
)
test_real_reversing_twice_is_safe = contract.test_reversing_twice_is_safe
test_real_trial_balance_is_paise_and_balances = (
    contract.test_trial_balance_is_in_paise_and_balances_to_zero
)


def test_our_vouchers_are_distinguishable_from_the_users_own(
    sim: TallySim, client: TallyClient
) -> None:
    """The FakeTally-specific contract test, rewritten for the real client.

    A voucher the accountant typed by hand is not ours and must never be swept
    up by bulk reverse.
    """
    sim.seed(COMPANY, narration="rent paid by hand")

    op = new_operation_id()
    client.write_voucher(COMPANY, contract.a_voucher(), op)

    assert len(client.read_vouchers(COMPANY)) == 2
    ours = client.list_our_vouchers(COMPANY)
    assert len(ours) == 1
    assert operation_id_in(ours[0].narration) == op


def test_the_signatures_match_the_protocol_not_just_the_method_names() -> None:
    """`runtime_checkable` only checks that the eight methods exist, so an
    `isinstance` pass proves less than it looks. This annotated binding is
    checked by pyright in strict mode, which checks the signatures too."""
    client: TallyClient = real.RealTally(transport=TallySim())
    assert isinstance(client, TallyClient)


def test_the_contract_tests_are_actually_bound_to_the_real_client(
    client: TallyClient,
) -> None:
    """Guards the re-binding above: if the fixture ever drifted back to
    FakeTally these tests would pass while proving nothing."""
    assert isinstance(client, real.RealTally)


def test_bulk_reverse_leaves_the_hand_typed_voucher_alone(
    sim: TallySim, client: TallyClient
) -> None:
    sim.seed(COMPANY, narration="rent paid by hand", amount_paise=555_00)
    before = client.trial_balance(COMPANY)

    for _ in range(3):
        client.write_voucher(COMPANY, contract.a_voucher(), new_operation_id())
    for voucher in client.list_our_vouchers(COMPANY):
        op = operation_id_in(voucher.narration)
        assert op is not None
        assert client.reverse_by_operation_id(COMPANY, op) is True

    assert client.trial_balance(COMPANY) == before
    assert len(client.read_vouchers(COMPANY)) == 1


# ===========================================================================
# money: rupee strings to integer paise, no float anywhere
# ===========================================================================


@pytest.mark.parametrize(
    ("text", "paise"),
    [
        ("1234.56", 123456),
        ("1,23,456.78", 12345678),
        ("1234", 123400),
        ("1234.5", 123450),
        ("0.01", 1),
        ("0.00", 0),
        ("-12.30", -1230),
        ("(500.00)", -50000),
        ("  118000.00  ", 11800000),
        ("\u20b9 1\u00a0234.50", 123450),
        ("+7.70", 770),
        ("0.07", 7),
        ("-0.01", -1),
    ],
)
def test_rupee_strings_become_exact_paise(text: str, paise: int) -> None:
    assert real.paise_from_rupees(text) == paise


@pytest.mark.parametrize(
    ("text", "paise"), [("0.29", 29), ("1.15", 115), ("8.20", 820), ("4.35", 435)]
)
def test_the_amounts_a_float_would_get_wrong(text: str, paise: int) -> None:
    """Not a style preference. `int(0.29 * 100)` is 28, `int(4.35 * 100)` is
    434, and a trial balance that must return to the exact paise cannot absorb
    either of them."""
    assert int(float(text) * 100) == paise - 1  # the bug, stated out loud
    assert real.paise_from_rupees(text) == paise


@pytest.mark.parametrize("text", ["", "   ", "abc", "12.34.56", "1.2e", "()"])
def test_unreadable_amounts_raise_rather_than_default_to_zero(text: str) -> None:
    with pytest.raises(real.TallyDataError):
        real.paise_from_rupees(text)


def test_sub_paise_precision_is_refused_not_rounded() -> None:
    with pytest.raises(real.TallyDataError, match="sub-paise"):
        real.paise_from_rupees("10.005")


@pytest.mark.parametrize("paise", [0, 1, -1, 7, 99, 100, 123456, -123456, 10**12])
def test_paise_survive_a_round_trip_through_the_wire_format(paise: int) -> None:
    assert real.paise_from_rupees(real.rupees_from_paise(paise)) == paise


@pytest.mark.parametrize(
    ("paise", "rendered"),
    [(0, "0.00"), (5, "0.05"), (50, "0.50"), (100, "1.00"), (-1180000, "-11800.00")],
)
def test_paise_render_as_tally_expects(paise: int, rendered: str) -> None:
    assert real.rupees_from_paise(paise) == rendered


# ===========================================================================
# XML hardening - Tally responses are untrusted input
# ===========================================================================

XXE = (
    '<?xml version="1.0"?>'
    '<!DOCTYPE root [<!ENTITY leak SYSTEM "file:///etc/passwd">]>'
    "<ENVELOPE><COMPANY><NAME>&leak;</NAME></COMPANY></ENVELOPE>"
)
BILLION_LAUGHS = (
    "<!DOCTYPE lolz [<!ENTITY lol 'haha'>"
    "<!ENTITY lol2 '&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;'>]>"
    "<ENVELOPE><COMPANY><NAME>&lol2;</NAME></COMPANY></ENVELOPE>"
)


@pytest.mark.parametrize("payload", [XXE, BILLION_LAUGHS])
def test_a_doctype_is_refused_before_anything_is_parsed(payload: str) -> None:
    with pytest.raises(real.TallyResponseError, match="DOCTYPE"):
        real.parse_xml(payload)


@pytest.mark.parametrize("payload", [XXE, BILLION_LAUGHS])
def test_the_parser_itself_refuses_a_doctype_not_just_the_screen(
    payload: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disable the pre-parse screen and prove the expat handlers carry it.

    Without them, CPython 3.14's stdlib parser expands internal entities and
    attempts external ones. The screen is a second line, not the only one.
    """
    monkeypatch.setattr(real, "_DOCTYPE", re.compile(r"(?!x)x"))
    with pytest.raises(real.TallyResponseError, match="refusing to parse"):
        real.parse_xml(payload)


def _allow(*_args: object) -> None:
    """Neutralise one refusal handler, to prove the next one down still holds."""
    return None


def test_entity_declarations_are_refused_even_if_the_doctype_slips_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(real, "_DOCTYPE", re.compile(r"(?!x)x"))
    monkeypatch.setattr(real, "_refuse_doctype", _allow)
    with pytest.raises(real.TallyResponseError, match="declares an XML entity"):
        real.parse_xml(BILLION_LAUGHS)


def _no_repair(payload: str) -> str:
    """`sanitise` without the ampersand rewrite, so `&leak;` stays live."""
    return payload.strip()


def test_external_entities_are_refused_with_every_other_defence_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The floor under everything else.

    Four defences have to be disabled to reach this one: `sanitise` rewrites
    `&leak;` into inert text, the `_DOCTYPE` screen refuses the document, the
    DOCTYPE handler refuses it again, and the entity-declaration handler
    refuses the declaration. With all four off, expat is willing to go and
    fetch `file:///etc/passwd`, and this is what stops it.
    """
    monkeypatch.setattr(real, "sanitise", _no_repair)
    monkeypatch.setattr(real, "_DOCTYPE", re.compile(r"(?!x)x"))
    monkeypatch.setattr(real, "_refuse_doctype", _allow)
    monkeypatch.setattr(real, "_refuse_entity_declaration", _allow)
    with pytest.raises(real.TallyResponseError, match="external entity"):
        real.parse_xml(XXE)


def test_an_oversized_response_is_rejected_before_parsing() -> None:
    payload = "<ENVELOPE>" + ("x" * 5000) + "</ENVELOPE>"
    with pytest.raises(real.TallyResponseError, match="exceeds the 100 cap"):
        real.parse_xml(payload, limit=100)


def test_a_response_at_the_cap_is_still_parsed() -> None:
    payload = "<ENVELOPE><COMPANY NAME='Demo'/></ENVELOPE>"
    assert real.parse_companies(payload, limit=len(payload)) == ("Demo",)


@pytest.mark.parametrize(
    "payload", ["", "   ", "not xml at all", "<ENVELOPE><UNCLOSED>"]
)
def test_a_broken_response_raises_and_never_reads_as_empty(payload: str) -> None:
    with pytest.raises(real.TallyResponseError):
        real.parse_companies(payload)


def test_tallys_bare_ampersands_and_control_bytes_are_repaired() -> None:
    payload = (
        "﻿<ENVELOPE><BODY><DATA><COLLECTION>"
        "<COMPANY><NAME>Smith & Co\x04 Ltd</NAME></COMPANY>"
        "</COLLECTION></DATA></BODY></ENVELOPE>"
    )
    assert real.parse_companies(payload) == ("Smith & Co Ltd",)


def test_an_undefined_entity_reference_becomes_inert_text() -> None:
    payload = "<ENVELOPE><COMPANY><NAME>&xxe;</NAME></COMPANY></ENVELOPE>"
    assert real.parse_companies(payload) == ("&xxe;",)


def test_sanitise_leaves_legal_entities_alone() -> None:
    assert real.sanitise("<A>&amp;&lt;&#65;&#x42;</A>") == "<A>&amp;&lt;&#65;&#x42;</A>"


# ===========================================================================
# request builders
# ===========================================================================


def _parsed(envelope: str) -> ElementTree.Element:
    return real.parse_xml(envelope)


def test_the_company_list_request_is_an_export_collection() -> None:
    root = _parsed(real.build_company_list_request())
    assert _required(root, ".//TALLYREQUEST") == "Export"
    assert _required(root, ".//TYPE") == "Collection"
    assert _required(root, ".//ID") == real.COLLECTION_COMPANIES
    assert _required(root, ".//TDLMESSAGE/COLLECTION/TYPE") == "Company"
    assert root.find(".//SVCURRENTCOMPANY") is None


@pytest.mark.parametrize(
    ("build", "collection_id", "tally_type"),
    [
        (real.build_ledger_list_request, real.COLLECTION_LEDGERS, "Ledger"),
        (real.build_closing_balance_request, real.COLLECTION_BALANCES, "Ledger"),
        (real.build_voucher_list_request, real.COLLECTION_VOUCHERS, "Voucher"),
    ],
)
def test_every_company_scoped_request_names_the_company(
    build: Callable[[str], str], collection_id: str, tally_type: str
) -> None:
    root = _parsed(build(COMPANY))
    assert _required(root, ".//SVCURRENTCOMPANY") == COMPANY
    assert _required(root, ".//ID") == collection_id
    assert _required(root, ".//TDLMESSAGE/COLLECTION/TYPE") == tally_type
    assert _required(root, ".//SVEXPORTFORMAT") == "$$SysName:XML"


def test_the_voucher_request_asks_for_the_ledger_entries() -> None:
    fetched = _required(_parsed(real.build_voucher_list_request(COMPANY)), ".//FETCH")
    for member in ("Narration", "RemoteID", "MasterID"):
        assert member in fetched
    assert "AllLedgerEntries.LedgerName" in fetched
    assert "AllLedgerEntries.Amount" in fetched
    assert "AllLedgerEntries.IsDeemedPositive" in fetched


def test_awkward_company_names_survive_escaping() -> None:
    awkward = "Smith & Sons <Ltd> \"Delhi\" 'Unit'"
    root = _parsed(real.build_ledger_list_request(awkward))
    assert _required(root, ".//SVCURRENTCOMPANY") == awkward


def _create_envelope(
    voucher: Voucher | None = None, operation_id: str = "ad_test"
) -> ElementTree.Element:
    voucher = voucher if voucher is not None else contract.a_voucher()
    return _parsed(
        real.build_voucher_create(
            COMPANY,
            voucher,
            stamp(voucher.narration, operation_id),
            operation_id,
            "Journal",
        )
    )


def test_the_create_envelope_is_an_import_carrying_the_operation_id() -> None:
    root = _create_envelope()
    assert _required(root, ".//TALLYREQUEST") == "Import"
    assert _required(root, ".//TYPE") == "Data"
    voucher_node = root.find(".//VOUCHER")
    assert voucher_node is not None
    assert voucher_node.get("ACTION") == "Create"
    assert voucher_node.get("REMOTEID") == "ad_test"
    assert marker_for("ad_test") in _required(root, ".//NARRATION")
    assert _required(root, ".//DATE") == "20260807"


def test_the_create_envelope_encodes_tallys_sign_convention() -> None:
    """Assumption A1, the one that silently inverts a book if it is wrong."""
    root = _create_envelope(contract.a_voucher(amount_paise=118000))
    legs = list(root.iter("ALLLEDGERENTRIES.LIST"))
    assert len(legs) == 2

    debit, credit = legs
    assert _required(debit, "LEDGERNAME") == "Purchases"
    assert _required(debit, "ISDEEMEDPOSITIVE") == "Yes"
    assert _required(debit, "AMOUNT") == "-1180.00"

    assert _required(credit, "LEDGERNAME") == "Cash"
    assert _required(credit, "ISDEEMEDPOSITIVE") == "No"
    assert _required(credit, "AMOUNT") == "1180.00"


def test_an_unmarked_voucher_can_never_be_built() -> None:
    voucher = contract.a_voucher()
    with pytest.raises(ValueError, match="marker"):
        real.build_voucher_create(
            COMPANY, voucher, voucher.narration, "ad_1", "Journal"
        )


@pytest.mark.parametrize(
    ("voucher", "why"),
    [
        (contract.a_voucher(amount_paise=0), "not a postable amount"),
        (contract.a_voucher(amount_paise=-1), "not a postable amount"),
    ],
)
def test_unpostable_amounts_are_refused_at_the_boundary(
    voucher: Voucher, why: str
) -> None:
    with pytest.raises(ValueError, match=why):
        _create_envelope(voucher)


def test_a_voucher_posted_against_itself_is_refused() -> None:
    voucher = Voucher(
        id="d",
        date=datetime.date(2026, 8, 7),
        party="X",
        narration="n",
        debit_account="Cash",
        credit_account="Cash",
        amount_paise=100,
    )
    with pytest.raises(ValueError, match="same account"):
        _create_envelope(voucher)


def test_a_voucher_missing_an_account_is_refused() -> None:
    voucher = Voucher(
        id="d",
        date=datetime.date(2026, 8, 7),
        party="X",
        narration="n",
        debit_account="   ",
        credit_account="Cash",
        amount_paise=100,
    )
    with pytest.raises(ValueError, match="both a debit"):
        _create_envelope(voucher)


def test_a_gst_voucher_is_refused_rather_than_silently_stripped() -> None:
    """This connector builds no tax lines. Writing the voucher anyway would
    post a wrong statutory entry that looks fine."""
    voucher = Voucher(
        id="d",
        date=datetime.date(2026, 8, 7),
        party="X",
        narration="n",
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=118000,
        gst_paise=18000,
    )
    with pytest.raises(ValueError, match="GST"):
        _create_envelope(voucher)


def test_the_delete_envelope_aims_at_the_operation_id_and_master_id() -> None:
    voucher = contract.a_voucher()
    stored = Voucher(
        id=voucher.id,
        date=voucher.date,
        party=voucher.party,
        narration=stamp(voucher.narration, "ad_9"),
        debit_account=voucher.debit_account,
        credit_account=voucher.credit_account,
        amount_paise=voucher.amount_paise,
        tally_id="M42",
    )
    root = _parsed(real.build_voucher_delete(COMPANY, stored, "ad_9", "Journal"))
    node = root.find(".//VOUCHER")
    assert node is not None
    assert node.get("ACTION") == "Delete"
    assert node.get("REMOTEID") == "ad_9"
    assert _required(root, ".//MASTERID") == "M42"
    # Never the amount, never the narration text.
    assert root.find(".//AMOUNT") is None
    assert root.find(".//NARRATION") is None


def test_a_voucher_with_no_master_id_cannot_be_deleted_blindly() -> None:
    with pytest.raises(real.TallyDataError, match="MASTERID"):
        real.build_voucher_delete(COMPANY, contract.a_voucher(), "ad_9", "Journal")


# ===========================================================================
# response parsers
# ===========================================================================


def test_companies_are_read_from_either_the_attribute_or_the_child() -> None:
    payload = (
        "<ENVELOPE><COLLECTION>"
        '<COMPANY NAME="Attribute Co"/>'
        "<COMPANY><NAME>Child Co</NAME></COMPANY>"
        "<COMPANY><NAME>   </NAME></COMPANY>"
        "</COLLECTION></ENVELOPE>"
    )
    assert real.parse_companies(payload) == ("Attribute Co", "Child Co")


def test_ledger_names_are_the_chart_of_accounts() -> None:
    payload = (
        "<ENVELOPE><COLLECTION>"
        '<LEDGER NAME="Purchases"><PARENT>Direct Expenses</PARENT></LEDGER>'
        '<LEDGER NAME="Cash"><PARENT>Cash-in-Hand</PARENT></LEDGER>'
        "<LEDGER><PARENT>orphan</PARENT></LEDGER>"
        "</COLLECTION></ENVELOPE>"
    )
    assert real.parse_ledger_names(payload) == ("Purchases", "Cash")


def _balances_payload(entries: Sequence[tuple[str, str]]) -> str:
    body = "".join(
        f'<LEDGER NAME="{name}"><CLOSINGBALANCE>{value}</CLOSINGBALANCE></LEDGER>'
        for name, value in entries
    )
    return f"<ENVELOPE><COLLECTION>{body}</COLLECTION></ENVELOPE>"


def test_closing_balances_prefer_the_dr_cr_suffix_when_tally_sends_one() -> None:
    """Assumption A8. When Tally says Dr or Cr we believe it rather than A1."""
    payload = _balances_payload(
        [("Purchases", "1180.00 Dr"), ("Cash", "1180.00 Cr"), ("Bank", "0.00 Dr")]
    )
    assert real.parse_closing_balances(payload) == {
        "Purchases": 118000,
        "Cash": -118000,
    }


def test_closing_balances_without_a_suffix_fall_back_to_the_sign_convention() -> None:
    """Assumption A1: Tally holds a debit as negative, we hold it as positive."""
    payload = _balances_payload([("Purchases", "-1180.00"), ("Cash", "1180.00")])
    assert real.parse_closing_balances(payload) == {
        "Purchases": 118000,
        "Cash": -118000,
    }


def test_a_ledger_with_no_balance_element_is_skipped() -> None:
    payload = (
        "<ENVELOPE><COLLECTION>"
        '<LEDGER NAME="Purchases"><CLOSINGBALANCE>10.00 Dr</CLOSINGBALANCE></LEDGER>'
        '<LEDGER NAME="Nothing"/>'
        "<LEDGER><CLOSINGBALANCE>5.00 Dr</CLOSINGBALANCE></LEDGER>"
        "</COLLECTION></ENVELOPE>"
    )
    assert real.parse_closing_balances(payload) == {"Purchases": 1000}


def _voucher_payload(*bodies: str) -> str:
    return f"<ENVELOPE><COLLECTION>{''.join(bodies)}</COLLECTION></ENVELOPE>"


TWO_LEGGED = (
    '<VOUCHER REMOTEID="ad_7" MASTERID="M11">'
    "<DATE>20260807</DATE>"
    "<VOUCHERNUMBER>7</VOUCHERNUMBER>"
    "<PARTYLEDGERNAME>Sharma Traders</PARTYLEDGERNAME>"
    "<NARRATION>cement bags [ACCOUNTANT_DAD:ad_7]</NARRATION>"
    "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Purchases</LEDGERNAME>"
    "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-1180.00</AMOUNT>"
    "</ALLLEDGERENTRIES.LIST>"
    "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME>"
    "<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>1180.00</AMOUNT>"
    "</ALLLEDGERENTRIES.LIST>"
    "</VOUCHER>"
)


def test_a_two_legged_voucher_becomes_the_frozen_type() -> None:
    page = real.parse_vouchers(_voucher_payload(TWO_LEGGED))
    assert page.skipped == 0
    assert len(page.vouchers) == 1

    voucher = page.vouchers[0]
    assert isinstance(voucher, Voucher)
    assert voucher.id == "ad_7"
    assert voucher.date == datetime.date(2026, 8, 7)
    assert voucher.party == "Sharma Traders"
    assert voucher.debit_account == "Purchases"
    assert voucher.credit_account == "Cash"
    assert voucher.amount_paise == 118000
    assert voucher.tally_id == "M11"
    assert operation_id_in(voucher.narration) == "ad_7"
    with pytest.raises(AttributeError):
        voucher.amount_paise = 1  # type: ignore[misc]


def test_a_voucher_without_isdeemedpositive_falls_back_to_the_amount_sign() -> None:
    body = (
        '<VOUCHER MASTERID="M2"><DATE>20260807</DATE>'
        "<NARRATION>n</NARRATION>"
        "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Purchases</LEDGERNAME>"
        "<AMOUNT>-10.00</AMOUNT></ALLLEDGERENTRIES.LIST>"
        "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME>"
        "<AMOUNT>10.00</AMOUNT></ALLLEDGERENTRIES.LIST></VOUCHER>"
    )
    page = real.parse_vouchers(_voucher_payload(body))
    assert page.vouchers[0].debit_account == "Purchases"
    assert page.vouchers[0].party == "Cash"
    assert page.vouchers[0].id == "M2"


def _unflagged_legs(*legs: tuple[str, str]) -> str:
    """A voucher whose legs carry no ISDEEMEDPOSITIVE, so only the sign decides."""
    entries = "".join(
        f"<ALLLEDGERENTRIES.LIST><LEDGERNAME>{ledger}</LEDGERNAME>"
        f"<AMOUNT>{amount}</AMOUNT></ALLLEDGERENTRIES.LIST>"
        for ledger, amount in legs
    )
    return (
        '<VOUCHER MASTERID="M8"><DATE>20260807</DATE><NARRATION>n</NARRATION>'
        f"{entries}</VOUCHER>"
    )


def test_one_paise_on_the_wrong_side_of_zero_is_still_a_debit() -> None:
    """The smallest amount that can be signed. `amount < 0` is the whole rule,
    so the boundary is worth a test of its own."""
    payload = _voucher_payload(
        _unflagged_legs(("Purchases", "-0.01"), ("Cash", "0.01"))
    )
    page = real.parse_vouchers(payload)
    assert len(page.vouchers) == 1
    assert page.vouchers[0].debit_account == "Purchases"
    assert page.vouchers[0].credit_account == "Cash"
    assert page.vouchers[0].amount_paise == 1


def test_a_zero_amount_leg_is_a_credit_not_a_debit() -> None:
    """Zero is not negative. If it were treated as a debit this voucher would
    have two debits and be quietly skipped instead of reported."""
    payload = _voucher_payload(
        _unflagged_legs(("Purchases", "0.00"), ("Cash", "-10.00"))
    )
    with pytest.raises(real.TallyDataError, match="do not cancel"):
        real.parse_vouchers(payload)


def test_a_leg_whose_flag_fights_its_sign_raises_instead_of_guessing() -> None:
    """If assumption A1 is backwards this is what a real Tally would trip."""
    body = (
        '<VOUCHER MASTERID="M3"><DATE>20260807</DATE><NARRATION>n</NARRATION>'
        "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Purchases</LEDGERNAME>"
        "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>10.00</AMOUNT>"
        "</ALLLEDGERENTRIES.LIST></VOUCHER>"
    )
    with pytest.raises(real.TallyDataError, match="A1"):
        real.parse_vouchers(_voucher_payload(body))


def test_legs_that_do_not_cancel_raise() -> None:
    body = (
        '<VOUCHER MASTERID="M4"><DATE>20260807</DATE><NARRATION>n</NARRATION>'
        "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Purchases</LEDGERNAME>"
        "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-10.00</AMOUNT>"
        "</ALLLEDGERENTRIES.LIST>"
        "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME>"
        "<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>11.00</AMOUNT>"
        "</ALLLEDGERENTRIES.LIST></VOUCHER>"
    )
    with pytest.raises(real.TallyDataError, match="do not cancel"):
        real.parse_vouchers(_voucher_payload(body))


MULTI_LEG = (
    '<VOUCHER MASTERID="M5"><DATE>20260807</DATE>'
    "<NARRATION>{narration}</NARRATION>"
    "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Purchases</LEDGERNAME>"
    "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-10.00</AMOUNT>"
    "</ALLLEDGERENTRIES.LIST>"
    "<ALLLEDGERENTRIES.LIST><LEDGERNAME>CGST</LEDGERNAME>"
    "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-1.00</AMOUNT>"
    "</ALLLEDGERENTRIES.LIST>"
    "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME>"
    "<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>11.00</AMOUNT>"
    "</ALLLEDGERENTRIES.LIST></VOUCHER>"
)


def test_a_multi_leg_voucher_is_counted_not_silently_dropped() -> None:
    """`Voucher` holds one debit and one credit. A three-legged entry cannot be
    represented, and "we could not read it" is not the same as "it is not
    there"."""
    payload = _voucher_payload(TWO_LEGGED, MULTI_LEG.format(narration="gst purchase"))
    page = real.parse_vouchers(payload)
    assert len(page.vouchers) == 1
    assert page.skipped == 1


def test_one_of_our_own_vouchers_going_multi_leg_is_an_error() -> None:
    """Somebody edited an entry we wrote. Bulk-reverse arithmetic over it can
    no longer be trusted, so say so rather than quietly losing it."""
    payload = _voucher_payload(
        MULTI_LEG.format(narration="rent [ACCOUNTANT_DAD:ad_edited]")
    )
    with pytest.raises(real.TallyDataError, match="ad_edited"):
        real.parse_vouchers(payload)


def test_a_leg_with_no_amount_is_not_a_leg() -> None:
    """An entry Tally sent without an AMOUNT leaves the voucher one-legged, so
    the voucher is counted as unreadable rather than half-read."""
    body = (
        '<VOUCHER MASTERID="M6"><DATE>20260807</DATE><NARRATION>n</NARRATION>'
        "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Purchases</LEDGERNAME>"
        "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-10.00</AMOUNT>"
        "</ALLLEDGERENTRIES.LIST>"
        "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME>"
        "<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE></ALLLEDGERENTRIES.LIST>"
        "</VOUCHER>"
    )
    page = real.parse_vouchers(_voucher_payload(body))
    assert page.vouchers == ()
    assert page.skipped == 1


def test_a_voucher_with_no_date_raises() -> None:
    body = TWO_LEGGED.replace("<DATE>20260807</DATE>", "")
    with pytest.raises(real.TallyDataError, match="DATE"):
        real.parse_vouchers(_voucher_payload(body))


def test_a_voucher_with_an_unreadable_date_raises() -> None:
    body = TWO_LEGGED.replace("20260807", "07-08-2026")
    with pytest.raises(real.TallyDataError, match="date"):
        real.parse_vouchers(_voucher_payload(body))


def test_import_counters_are_read_back() -> None:
    result = real.parse_import_response(
        import_response(created=1, altered=2, deleted=3, ignored=4, last_vch_id="M7")
    )
    assert result == real.ImportResult(
        created=1, altered=2, deleted=3, ignored=4, last_vch_id="M7"
    )
    assert result.ok is True
    assert "created=1" in result.summary()


def test_an_import_with_errors_is_not_ok() -> None:
    result = real.parse_import_response(
        import_response(errors=1, exceptions=2, line_errors=["ledger not found"])
    )
    assert result.ok is False
    assert result.line_errors == ("ledger not found",)
    assert "ledger not found" in result.summary()


def test_a_missing_counter_reads_as_zero() -> None:
    payload = "<ENVELOPE><IMPORTRESULT><CREATED>1</CREATED></IMPORTRESULT></ENVELOPE>"
    assert real.parse_import_response(payload) == real.ImportResult(created=1)


def test_a_counter_that_is_not_a_number_raises() -> None:
    payload = (
        "<ENVELOPE><IMPORTRESULT><CREATED>lots</CREATED></IMPORTRESULT></ENVELOPE>"
    )
    with pytest.raises(real.TallyDataError, match="CREATED"):
        real.parse_import_response(payload)


# ===========================================================================
# configuration
# ===========================================================================


def test_the_default_is_localhost_9000() -> None:
    config = real.TallyConfig()
    assert config.host == "localhost"
    assert config.port == 9000
    assert config.url == "http://localhost:9000"
    assert config.is_loopback is True


def test_the_host_is_configurable_because_tally_lives_in_a_vm() -> None:
    config = real.TallyConfig(host="192.168.64.7", port=9001)
    assert config.url == "http://192.168.64.7:9001"
    assert config.is_loopback is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"host": "  "},
        {"port": 0},
        {"port": 70000},
        {"timeout_seconds": 0.0},
        {"timeout_seconds": -1.0},
        {"retries": 0},
        {"retry_backoff_seconds": -0.1},
        {"max_response_bytes": 0},
        {"voucher_type": " "},
    ],
)
def test_a_nonsense_configuration_is_refused_at_construction(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        real.TallyConfig(**kwargs)  # type: ignore[arg-type]


# ===========================================================================
# transport
# ===========================================================================


class FakePoster:
    """Stands in for one HTTP POST. Records what it was asked for."""

    def __init__(self, replies: Sequence[tuple[int, bytes] | Exception]) -> None:
        self._replies = list(replies)
        self.urls: list[str] = []
        self.timeouts: list[float] = []

    def __call__(
        self, url: str, body: bytes, timeout: float, max_bytes: int
    ) -> tuple[int, bytes]:
        del body, max_bytes
        self.urls.append(url)
        self.timeouts.append(timeout)
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _transport(
    poster: FakePoster, **config: object
) -> tuple[real.HttpTransport, list[float]]:
    slept: list[float] = []
    settings = real.TallyConfig(retry_backoff_seconds=0.0, **config)  # type: ignore[arg-type]
    return (
        real.HttpTransport(settings, poster=poster, sleep=slept.append),
        slept,
    )


def test_a_read_reaches_tally_and_comes_back_decoded() -> None:
    poster = FakePoster([(200, b"<ENVELOPE><OK/></ENVELOPE>")])
    transport, _ = _transport(poster)
    assert transport.send("<ENVELOPE/>", retry=True) == "<ENVELOPE><OK/></ENVELOPE>"
    assert poster.urls == ["http://localhost:9000"]
    assert poster.timeouts == [30.0]


def test_a_read_retries_a_dropped_connection_and_then_gives_up() -> None:
    poster = FakePoster([ConnectionError("refused")] * 3)
    transport, slept = _transport(poster, retries=3)
    with pytest.raises(real.TallyUnreachable, match="after 3 attempt"):
        transport.send("<ENVELOPE/>", retry=True)
    assert len(poster.urls) == 3
    assert len(slept) == 2  # no sleep after the final attempt


def test_a_read_that_recovers_on_the_second_attempt_returns_the_body() -> None:
    poster = FakePoster([TimeoutError("slow"), (200, b"<ENVELOPE/>")])
    transport, _ = _transport(poster, retries=3)
    assert transport.send("<ENVELOPE/>", retry=True) == "<ENVELOPE/>"


def test_a_write_is_never_retried() -> None:
    """A connection that died after Tally committed looks exactly like one that
    died before it did. Retrying is how you get two vouchers."""
    poster = FakePoster([ConnectionError("refused")])
    transport, slept = _transport(poster, retries=5)
    with pytest.raises(real.TallyUnreachable, match="after 1 attempt"):
        transport.send("<ENVELOPE/>", retry=False)
    assert len(poster.urls) == 1
    assert slept == []


def test_the_backoff_grows_between_attempts() -> None:
    poster = FakePoster([ConnectionError("refused")] * 4)
    settings = real.TallyConfig(retries=4, retry_backoff_seconds=0.25)
    slept: list[float] = []
    transport = real.HttpTransport(settings, poster=poster, sleep=slept.append)
    with pytest.raises(real.TallyUnreachable):
        transport.send("<ENVELOPE/>", retry=True)
    assert slept == [0.25, 0.5, 1.0]


@pytest.mark.parametrize("status", [301, 400, 401, 404, 500, 503])
def test_a_non_2xx_answer_is_an_error_not_a_body(status: int) -> None:
    poster = FakePoster([(status, b"<html>nope</html>")])
    transport, _ = _transport(poster)
    with pytest.raises(real.TallyResponseError, match=f"HTTP {status}"):
        transport.send("<ENVELOPE/>", retry=True)


def test_an_oversized_body_is_refused_by_the_transport() -> None:
    poster = FakePoster([(200, b"x" * 101)])
    transport, _ = _transport(poster, max_response_bytes=100)
    with pytest.raises(real.TallyResponseError, match="100 byte cap"):
        transport.send("<ENVELOPE/>", retry=True)


@pytest.mark.parametrize(
    "raw",
    [
        b"<ENVELOPE/>",
        b"\xef\xbb\xbf<ENVELOPE/>",
        "<ENVELOPE/>".encode("utf-16"),
    ],
)
def test_the_body_is_decoded_whatever_byte_order_mark_tally_used(raw: bytes) -> None:
    poster = FakePoster([(200, raw)])
    transport, _ = _transport(poster)
    assert transport.send("<ENVELOPE/>", retry=True) == "<ENVELOPE/>"


def test_undecodable_bytes_do_not_crash_the_read() -> None:
    poster = FakePoster([(200, b"<ENVELOPE>\xff\xfe\x00bad</ENVELOPE>")])
    transport, _ = _transport(poster)
    assert transport.send("<ENVELOPE/>", retry=True).startswith("<ENVELOPE>")


class _EchoHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # the stdlib spells the hook this way
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@pytest.fixture
def loopback_server() -> Iterator[int]:
    server = HTTPServer(("127.0.0.1", 0), _EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_post_bytes_talks_to_a_real_socket(loopback_server: int) -> None:
    """The only test here that opens a socket, and it opens it to 127.0.0.1.

    Everything else about the transport is exercised through an injected
    poster; this proves the minimal opener actually performs an HTTP POST.
    """
    transport = real.HttpTransport(
        real.TallyConfig(host="127.0.0.1", port=loopback_server, timeout_seconds=5.0)
    )
    assert transport.send("<ENVELOPE><PING/></ENVELOPE>", retry=True) == (
        "<ENVELOPE><PING/></ENVELOPE>"
    )


def test_a_refused_connection_is_unreachable_not_an_empty_book() -> None:
    """The failure that matters: an empty tuple here reads as "this company has
    no vouchers", which would let the memory index learn nothing and the
    detectors fire on everything."""
    client = real.RealTally(
        real.TallyConfig(retries=1),
        transport=ScriptedTransport([real.TallyUnreachable("nothing there")]),
    )
    with pytest.raises(real.TallyUnreachable):
        client.read_vouchers(COMPANY)


# ===========================================================================
# the client's own decisions
# ===========================================================================


def test_reads_go_out_with_retry_and_writes_do_not(sim: TallySim) -> None:
    client = real.RealTally(
        transport=sim, backups=real.RecordedBackups(frozenset({COMPANY}))
    )
    client.write_voucher(COMPANY, contract.a_voucher(), new_operation_id())
    # duplicate-check read, the write, the read-back.
    assert sim.retry_flags == [True, False, True]


def test_the_default_client_refuses_every_write(sim: TallySim) -> None:
    """`RecordedBackups()` is empty on purpose. A missing backup record and a
    missing backup look identical from here, and only one is safe to assume."""
    client = real.RealTally(transport=sim)
    with pytest.raises(CompanyNotBackedUp):
        client.write_voucher(COMPANY, contract.a_voucher(), new_operation_id())
    assert sim.sent == []


def test_a_company_with_no_recorded_backup_is_left_untouched(sim: TallySim) -> None:
    sim.add_company("Unbacked Co", ACCOUNTS)
    client = real.RealTally(
        transport=sim, backups=real.RecordedBackups(frozenset({COMPANY}))
    )
    with pytest.raises(CompanyNotBackedUp):
        client.write_voucher("Unbacked Co", contract.a_voucher(), new_operation_id())
    assert client.read_vouchers("Unbacked Co") == ()


def test_the_backup_check_happens_before_anything_reaches_the_wire(
    sim: TallySim,
) -> None:
    client = real.RealTally(transport=sim, backups=real.RecordedBackups())
    with pytest.raises(CompanyNotBackedUp):
        client.write_voucher(COMPANY, contract.a_voucher(), new_operation_id())
    assert sim.sent == []


def test_companies_and_accounts_come_back_as_tuples(client: TallyClient) -> None:
    assert client.list_companies() == (COMPANY,)
    assert client.read_accounts(COMPANY) == ACCOUNTS


def test_the_skipped_count_is_visible_to_a_caller_who_asks(sim: TallySim) -> None:
    sim.companies[COMPANY].raw_vouchers.append(MULTI_LEG.format(narration="gst"))
    client = real.RealTally(
        transport=sim, backups=real.RecordedBackups(frozenset({COMPANY}))
    )
    client.write_voucher(COMPANY, contract.a_voucher(), new_operation_id())

    page = client.read_vouchers_page(COMPANY)
    assert len(page.vouchers) == 1
    assert page.skipped == 1


def _client_with(replies: Sequence[str | Exception]) -> real.RealTally:
    return real.RealTally(
        transport=ScriptedTransport(replies),
        backups=real.RecordedBackups(frozenset({COMPANY})),
    )


EMPTY_VOUCHERS = "<ENVELOPE><COLLECTION/></ENVELOPE>"


def test_a_rejected_write_raises_rather_than_reporting_a_tally_id() -> None:
    client = _client_with(
        [EMPTY_VOUCHERS, import_response(errors=1, line_errors=["no such ledger"])]
    )
    with pytest.raises(real.TallyRejected, match="no such ledger"):
        client.write_voucher(COMPANY, contract.a_voucher(), "ad_1")


def test_a_write_that_created_nothing_is_a_failure_not_a_success() -> None:
    """Zero errors and zero vouchers is Tally silently ignoring the payload."""
    client = _client_with([EMPTY_VOUCHERS, import_response(ignored=1)])
    with pytest.raises(real.TallyRejected, match="and created nothing"):
        client.write_voucher(COMPANY, contract.a_voucher(), "ad_1")


def test_a_write_that_altered_an_existing_voucher_is_a_failure() -> None:
    """We only ever create. Altering means we overwrote somebody's entry."""
    client = _client_with([EMPTY_VOUCHERS, import_response(created=0, altered=1)])
    with pytest.raises(real.TallyRejected, match="altered"):
        client.write_voucher(COMPANY, contract.a_voucher(), "ad_1")


def test_a_write_that_cannot_be_read_back_did_not_happen() -> None:
    """C6, stated the only way that is safe: HTTP said 200, the read-back says
    the voucher is not there, and the read-back wins."""
    client = _client_with(
        [EMPTY_VOUCHERS, import_response(created=1, last_vch_id="M1"), EMPTY_VOUCHERS]
    )
    with pytest.raises(real.TallyRejected, match="whatever HTTP said"):
        client.write_voucher(COMPANY, contract.a_voucher(), "ad_1")


def test_a_write_with_no_master_id_anywhere_raises() -> None:
    """Without an id there is nothing to aim a reversal at later."""
    read_back = _voucher_payload(
        TWO_LEGGED.replace(' MASTERID="M11"', "").replace(
            "<VOUCHERNUMBER>7</VOUCHERNUMBER>", ""
        )
    )
    client = _client_with([EMPTY_VOUCHERS, import_response(created=1), read_back])
    with pytest.raises(real.TallyDataError, match="MASTERID"):
        client.write_voucher(COMPANY, contract.a_voucher(), "ad_7")


def test_the_last_vch_id_is_used_when_the_read_back_has_no_master_id() -> None:
    read_back = _voucher_payload(TWO_LEGGED.replace(' MASTERID="M11"', ""))
    client = _client_with(
        [EMPTY_VOUCHERS, import_response(created=1, last_vch_id="M99"), read_back]
    )
    result = client.write_voucher(COMPANY, contract.a_voucher(), "ad_7")
    assert result.tally_id == "7"  # the voucher number, preferred over LASTVCHID


def test_a_deletion_tally_refuses_raises(sim: TallySim) -> None:
    client = real.RealTally(
        transport=sim, backups=real.RecordedBackups(frozenset({COMPANY}))
    )
    op = new_operation_id()
    client.write_voucher(COMPANY, contract.a_voucher(), op)

    sim.import_override = import_response(errors=1, line_errors=["cannot delete"])
    with pytest.raises(real.TallyRejected, match="refused to delete"):
        client.reverse_by_operation_id(COMPANY, op)


def test_a_deletion_tally_claims_but_did_not_do_raises(sim: TallySim) -> None:
    """Never report a reversal as successful because Tally said so. Read back."""
    client = real.RealTally(
        transport=sim, backups=real.RecordedBackups(frozenset({COMPANY}))
    )
    op = new_operation_id()
    client.write_voucher(COMPANY, contract.a_voucher(), op)

    sim.swallow_deletes = True
    with pytest.raises(real.TallyRejected, match="still there"):
        client.reverse_by_operation_id(COMPANY, op)
    assert client.read_by_operation_id(COMPANY, op) is not None


def test_the_trial_balance_works_when_tally_sends_dr_cr_suffixes(
    sim: TallySim, client: TallyClient
) -> None:
    """Assumption A8 end to end: the same numbers whichever way Tally spells
    them."""
    op = new_operation_id()
    client.write_voucher(COMPANY, contract.a_voucher(amount_paise=118000), op)
    plain = client.trial_balance(COMPANY)

    sim.balances_carry_dr_cr = True
    assert (
        client.trial_balance(COMPANY) == plain == {"Purchases": 118000, "Cash": -118000}
    )


def test_reversal_ignores_a_lookalike_in_another_company(sim: TallySim) -> None:
    """Same amount, same narration, different company. The operation ID is the
    only thing that decides."""
    sim.add_company("Other Co", ACCOUNTS)
    client = real.RealTally(
        transport=sim,
        backups=real.RecordedBackups(frozenset({COMPANY, "Other Co"})),
    )
    op = new_operation_id()
    client.write_voucher(COMPANY, contract.a_voucher(), op)

    assert client.reverse_by_operation_id("Other Co", op) is False
    assert client.read_by_operation_id(COMPANY, op) is not None

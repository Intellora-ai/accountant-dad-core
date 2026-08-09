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
    your hypotheses cannot falsify them. A review by an experienced engineer
    changed several of those hypotheses; it did not run any of them. Only a live
    instance can.

WHAT HAS SINCE BEEN MEASURED (TallyPrime Release 7.0, 2026-08-08)
    The DELETE path, and only the delete path, has now run against a real
    instance. It disproved the shape this file used to assert:

      * `REMOTEID` + child tags -> `Voucher does not exist!`
      * child tags with no `REMOTEID` -> `Cannot delete unnamed object: VOUCHER!`
      * `ACTION="Alter"` + `<ISDELETED>Yes</ISDELETED>` -> silently ignored
        (`altered=0 deleted=0 errors=0`), voucher still present.

    What worked, and removed a real voucher (`deleted=1 errors=0`, confirmed by
    reading the voucher list before and after):

        <VOUCHER DATE="2-Apr-2026" TAGNAME="Master ID" TAGVALUE="3"
                 ACTION="Delete" VCHTYPE="Journal"></VOUCHER>

    The delete tests and `TallySim._delete` assert THAT shape. Everything else
    in this file is still unmeasured.

    Nothing here opens a socket except `test_post_bytes_talks_to_a_real_socket`,
    which talks to a one-shot loopback server to prove the opener works at all.
"""

from __future__ import annotations

import datetime
import re
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
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
    status: int | None = None,
    last_vch_id: str | None = None,
    line_errors: Sequence[str] = (),
) -> str:
    """The result Tally is assumed to return from Import/Data (A4)."""
    head = f"<STATUS>{status}</STATUS>" if status is not None else ""
    last = f"<LASTVCHID>{_esc(last_vch_id)}</LASTVCHID>" if last_vch_id else ""
    lines = "".join(f"<LINEERROR>{_esc(t)}</LINEERROR>" for t in line_errors)
    return (
        f"<ENVELOPE><HEADER><VERSION>1</VERSION>{head}</HEADER>"
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
            # Through `_voucher_payload`, so the simulator answers in the shape a
            # real TallyPrime answers in - `<BODY><DATA>` plus the `<CMPINFO>`
            # header whose `<VOUCHER>0</VOUCHER>` is a COUNT, not a voucher.
            return _voucher_payload(
                *(v.to_xml() for v in co.vouchers), *co.raw_vouchers
            )
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
        co = self._company(company)

        if action == "Delete":
            return self._delete(co, node)

        assert action == "Create", f"unexpected voucher action {action!r}"
        remote_id = node.get("REMOTEID") or ""
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
        return import_response(created=1, status=1, last_vch_id=master_id)

    def _delete(self, co: SimCompany, node: ElementTree.Element) -> str:
        """A6, in the shape a real TallyPrime 7.0 accepted on 2026-08-08.

        Tally names the voucher with the `TAGNAME`/`TAGVALUE` ATTRIBUTE pair - a
        TDL method name and its value - and with nothing else. The envelope this
        simulator used to demand was measured against the live instance and it
        never worked. Seven variants of it were tried:

          * no `REMOTEID`                     -> `Cannot delete unnamed object:
                                                  VOUCHER!`
          * `REMOTEID` (with `VCHKEY`, `GUID` and `MASTERID` in every
            combination)                      -> `Voucher does not exist!`
          * `ACTION="Alter"` plus
            `<ISDELETED>Yes</ISDELETED>`      -> ignored in silence:
                                                 `altered=0 deleted=0 errors=0`
                                                 and the voucher still there.

        The envelope below is the one that returned `deleted=1 errors=0` and
        removed a real voucher, confirmed by reading the voucher list before and
        after. So the simulator asserts THAT shape: a simulator that keeps
        accepting a disproven envelope is a test that guards nothing.
        """
        assert node.get("VCHTYPE"), "the delete must carry VCHTYPE from the read"
        assert node.get("TAGNAME") == real.DELETE_TAGNAME, (
            "Tally looks a voucher up by a TDL method name carried on TAGNAME. "
            f"Expected {real.DELETE_TAGNAME!r}, got {node.get('TAGNAME')!r}"
        )
        master_id = node.get("TAGVALUE") or ""
        assert master_id, "the delete must carry the MASTERID from the read"
        date = node.get("DATE") or ""
        assert re.fullmatch(r"\d{1,2}-[A-Z][a-z]{2}-\d{4}", date), (
            f"the DATE ATTRIBUTE is dd-MMM-yyyy, not the child tag's yyyyMMdd; "
            f"got {date!r}"
        )
        # REMOTEID is sync lineage, not a handle. Sending it is what made every
        # real delete come back "Voucher does not exist!", so a delete carrying
        # it must fail here rather than quietly pass.
        for stale in ("REMOTEID", "GUID", "VCHKEY"):
            assert node.get(stale) is None, (
                f"{stale} must not be on a delete: it is what made the real delete fail"
            )
        assert list(node) == [], (
            "a delete NAMES a voucher; child tags are fields to WRITE and "
            "sending them produced 'Cannot delete unnamed object: VOUCHER!'"
        )

        if self.swallow_deletes:
            return import_response(deleted=1, status=1)
        keep = [v for v in co.vouchers if v.master_id != master_id]
        deleted = len(co.vouchers) - len(keep)
        co.vouchers = keep
        return import_response(deleted=deleted, status=1)


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


def _native_methods(envelope: str) -> list[str]:
    return [
        (node.text or "").strip() for node in _parsed(envelope).iter("NATIVEMETHOD")
    ]


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


@pytest.mark.parametrize(
    "build",
    [
        real.build_company_list_request,
        lambda: real.build_ledger_list_request(COMPANY),
        lambda: real.build_closing_balance_request(COMPANY),
    ],
)
def test_a_request_with_no_nested_members_carries_no_native_methods(
    build: Callable[[], str],
) -> None:
    assert _native_methods(build()) == []


def test_the_voucher_request_fetches_only_flat_members() -> None:
    """Assumption A3, changed by review: a dotted path inside `<FETCH>` is not
    reliably honoured, so none is sent."""
    fetched = _required(_parsed(real.build_voucher_list_request(COMPANY)), ".//FETCH")
    for member in ("Date", "Narration", "RemoteID", "MasterID", "GUID"):
        assert member in fetched
    assert "AllLedgerEntries." not in fetched


def test_the_ledger_entries_are_asked_for_as_explicit_native_methods() -> None:
    """Assumption A3. One `<NATIVEMETHOD>` per nested member, spelled out."""
    assert _native_methods(real.build_voucher_list_request(COMPANY)) == [
        "ALLLEDGERENTRIES.LIST:LEDGERNAME",
        "ALLLEDGERENTRIES.LIST:AMOUNT",
        "ALLLEDGERENTRIES.LIST:ISDEEMEDPOSITIVE",
    ]


def test_the_broad_diagnostic_form_is_opt_in_and_additive() -> None:
    """`ALLLEDGERENTRIES.*` is for the first conversation with a build that
    honours neither shape. It is never sent by default."""
    default = _native_methods(real.build_voucher_list_request(COMPANY))
    diagnostic = _native_methods(
        real.build_voucher_list_request(COMPANY, diagnostic=True)
    )
    assert real.LEDGER_ENTRY_METHOD_BROAD not in default
    assert diagnostic == [*default, "ALLLEDGERENTRIES.*"]


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


def test_the_create_envelope_carries_a10s_required_fields() -> None:
    """Assumption A10, confirmed by review: a two-legged Journal is valid only
    with the accounting voucher view declared both ways, and a YYYYMMDD date."""
    root = _create_envelope()
    node = root.find(".//VOUCHER")
    assert node is not None
    assert node.get("OBJVIEW") == "Accounting Voucher View"
    assert _required(root, ".//PERSISTEDVIEW") == "Accounting Voucher View"
    assert re.fullmatch(r"\d{8}", _required(root, ".//DATE"))
    assert _required(root, ".//VOUCHERTYPENAME") == "Journal"


def test_the_create_envelope_carries_no_invoice_or_tax_only_fields() -> None:
    """A10 again, from the other side: nothing was added while adding the
    required fields."""
    envelope = real.build_voucher_create(
        COMPANY,
        contract.a_voucher(),
        stamp("cement bags", "ad_test"),
        "ad_test",
        "Journal",
    )
    for forbidden in (
        "BILLALLOCATIONS.LIST",
        "INVENTORYALLOCATIONS.LIST",
        "ALLINVENTORYENTRIES.LIST",
        "GSTREGISTRATION",
        "RATEOFINVOICETAX",
    ):
        assert forbidden not in envelope


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


# ===========================================================================
# A1's two guards, both in front of the wire
# ===========================================================================


def _leg(ledger: str, signed_paise: int, deemed_positive: bool) -> real.OutgoingLeg:
    return real.OutgoingLeg(
        ledger=ledger, signed_paise=signed_paise, is_deemed_positive=deemed_positive
    )


def test_a_balanced_pair_of_legs_passes_the_guard() -> None:
    real.check_outgoing_legs(
        (_leg("Purchases", -118000, True), _leg("Cash", 118000, False)), "v1"
    )


def test_a_debit_leg_with_a_positive_amount_is_refused_not_normalised() -> None:
    """ISDEEMEDPOSITIVE=Yes with a positive AMOUNT. Which half is wrong is not
    ours to decide, and picking one silently inverts a statutory entry."""
    with pytest.raises(ValueError, match=r"Purchases.*contradictory"):
        real.check_outgoing_legs(
            (_leg("Purchases", 118000, True), _leg("Cash", 118000, False)), "v1"
        )


def test_a_credit_leg_with_a_negative_amount_is_refused_not_normalised() -> None:
    with pytest.raises(ValueError, match=r"Cash.*contradictory"):
        real.check_outgoing_legs(
            (_leg("Purchases", -118000, True), _leg("Cash", -118000, False)), "v1"
        )


def test_a_zero_leg_contradicts_nothing() -> None:
    """Zero is neither positive nor negative, so neither guard fires on it. The
    balance guard is what catches it."""
    with pytest.raises(ValueError, match="unbalanced"):
        real.check_outgoing_legs(
            (_leg("Purchases", 0, True), _leg("Cash", 118000, False)), "v1"
        )


def test_legs_that_do_not_balance_to_the_paise_never_reach_the_wire() -> None:
    with pytest.raises(ValueError, match="117999 paise"):
        real.check_outgoing_legs(
            (_leg("Purchases", -118000, True), _leg("Cash", 117999, False)), "v1"
        )


def _no_flip(paise: int) -> int:
    """`_flip_tally_sign` with A1's negation taken out."""
    return paise


def test_the_guard_stands_between_the_builder_and_the_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the guard: if A1's single negation were ever inverted, the
    envelope would be built and then refused, not built and sent."""
    monkeypatch.setattr(real, "_flip_tally_sign", _no_flip)
    with pytest.raises(ValueError, match="contradictory"):
        _create_envelope()


# ===========================================================================
# A6: the delete key comes from a read taken immediately before
# ===========================================================================

FULL_LOCATORS = {
    "VCHTYPE": "Journal",
    "MASTERID": "M42",
    "REMOTEID": "ad_9",
}


def _exported(
    locators: dict[str, str] | None = None,
    *,
    narration: str = "cement bags [ACCOUNTANT_DAD:ad_9]",
) -> real.ExportedVoucher:
    base = contract.a_voucher()
    return real.ExportedVoucher(
        voucher=Voucher(
            id=base.id,
            date=base.date,
            party=base.party,
            narration=narration,
            debit_account=base.debit_account,
            credit_account=base.credit_account,
            amount_paise=base.amount_paise,
            tally_id="M42",
        ),
        locators=dict(FULL_LOCATORS) if locators is None else locators,
    )


def test_the_delete_envelope_carries_the_whole_key_from_the_fresh_read() -> None:
    """A6, in the shape a real TallyPrime 7.0 accepted on 2026-08-08.

    Tally identifies a voucher for Alter/Cancel/Delete by the `TAGNAME`/
    `TAGVALUE` ATTRIBUTE pair - a TDL method name and its value. Child tags are
    the fields to WRITE, not the key to look up by, which is why the old shape
    (`MASTERID`, `GUID`, `VCHKEY` and `DATE` as children) came back
    `Cannot delete unnamed object: VOUCHER!` when it carried no `REMOTEID` and
    `Voucher does not exist!` when it did.

    Every value here still comes from the read taken immediately before, and the
    body is still empty: a delete names a voucher, it does not rewrite one.
    """
    root = _parsed(real.build_voucher_delete(COMPANY, _exported(), "ad_9"))
    node = root.find(".//VOUCHER")
    assert node is not None
    assert node.get("VCHTYPE") == "Journal"
    assert node.get("TAGNAME") == "Master ID"
    assert node.get("TAGVALUE") == "M42"
    assert node.get("DATE") == "07-Aug-2026"
    # The body is empty. Never the amount, never the narration text.
    assert list(node) == []
    assert root.find(".//AMOUNT") is None
    assert root.find(".//NARRATION") is None


def test_we_send_delete_and_never_cancel() -> None:
    """`Delete` removes the voucher. `Cancel` leaves a cancelled voucher in
    place, keeping its number. They are not interchangeable and reversal means
    the first one."""
    envelope = real.build_voucher_delete(COMPANY, _exported(), "ad_9")
    node = _parsed(envelope).find(".//VOUCHER")
    assert node is not None
    assert node.get("ACTION") == "Delete"
    assert "Cancel" not in envelope


def test_a_delete_never_carries_remoteid_guid_or_vchkey() -> None:
    """The regression test for the bug that made every real delete fail.

    This used to assert the OPPOSITE - that a GUID or VCHKEY the read supplied
    was passed through - and the connector used to require REMOTEID outright.
    Measured against a real TallyPrime 7.0 on 2026-08-08, every delete aimed
    that way came back `Voucher does not exist!`, and dropping REMOTEID while
    keeping the child tags came back `Cannot delete unnamed object: VOUCHER!`.

    REMOTEID is a SYNC-LINEAGE field. Tally stamps it on export so it looks like
    a handle, but a voucher created by a local import has no entry in the remote
    index, so aiming at it names nothing. Tally's own import guidance says to
    STRIP REMOTEID before importing. GUID and VCHKEY are locators of the same
    kind and are equally not a lookup key.

    So the read here supplies all three, and the envelope must still carry none
    of them - not as attributes, not as children.
    """
    locators = {**FULL_LOCATORS, "GUID": "9f-1a-2b", "VCHKEY": "0005c8d0:0000006c"}
    envelope = real.build_voucher_delete(COMPANY, _exported(locators), "ad_9")
    root = _parsed(envelope)
    node = root.find(".//VOUCHER")
    assert node is not None
    for stale in ("REMOTEID", "GUID", "VCHKEY"):
        assert node.get(stale) is None
        assert root.find(f".//{stale}") is None
    assert "ad_9" not in envelope
    assert "9f-1a-2b" not in envelope
    assert "0005c8d0:0000006c" not in envelope


def test_a_vchkey_is_never_invented_when_the_read_did_not_supply_one() -> None:
    """The other half of the test above: nothing is reconstructed either."""
    root = _parsed(real.build_voucher_delete(COMPANY, _exported(), "ad_9"))
    assert root.find(".//VCHKEY") is None
    assert root.find(".//GUID") is None


def test_the_delete_date_is_the_attribute_format_not_the_child_format() -> None:
    """The trap that has no other guard.

    The DATE ATTRIBUTE is `dd-MMM-yyyy` ("07-Aug-2026"). The DATE CHILD TAG is
    `yyyyMMdd` ("20260807"). Tally EXPORTS the child form, so echoing what the
    read gave us straight into the attribute is the natural mistake - and it is
    a different field, so Tally would not find the voucher.
    """
    root = _parsed(real.build_voucher_delete(COMPANY, _exported(), "ad_9"))
    node = root.find(".//VOUCHER")
    assert node is not None
    assert node.get("DATE") == "07-Aug-2026"
    assert node.get("DATE") != "20260807"
    assert re.fullmatch(r"\d{1,2}-[A-Z][a-z]{2}-\d{4}", node.get("DATE") or "")


def test_a_masterid_tally_right_aligned_is_stripped_before_it_becomes_the_key() -> None:
    """Tally right-aligns numbers on export: `<MASTERID TYPE="Number"> 1</MASTERID>`.

    The locator is preserved exactly as Tally sent it (A5), leading space and
    all, so the space has to come off where the value becomes a lookup key. A
    TAGVALUE of `" 1"` is not the voucher whose Master ID is `1`.
    """
    locators = {**FULL_LOCATORS, "MASTERID": " 1"}
    root = _parsed(real.build_voucher_delete(COMPANY, _exported(locators), "ad_9"))
    node = root.find(".//VOUCHER")
    assert node is not None
    assert node.get("TAGVALUE") == "1"


@pytest.mark.parametrize("dropped", ["VCHTYPE", "MASTERID"])
def test_a_delete_missing_any_part_of_the_key_is_refused(dropped: str) -> None:
    """REMOTEID is deliberately not in this list any more: it is no longer
    required, no longer sent, and requiring it is what broke the real delete."""
    locators = {k: v for k, v in FULL_LOCATORS.items() if k != dropped}
    with pytest.raises(real.TallyDataError, match=dropped):
        real.build_voucher_delete(COMPANY, _exported(locators), "ad_9")


def test_a_voucher_that_did_not_come_from_a_read_cannot_be_deleted() -> None:
    """No locators at all means nothing was read; a delete built from that would
    be aimed at whatever the values happened to match."""
    with pytest.raises(real.TallyDataError, match="the read taken just now"):
        real.build_voucher_delete(COMPANY, _exported({}), "ad_9")


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


def test_a_reserved_ledger_is_left_out_of_the_trial_balance() -> None:
    """Measured 2026-08-08 against a real TallyPrime 7.0.

    After one Rs 1,684.56 expense the real response was, verbatim:

        <LEDGER NAME="AD Test Expense"   RESERVEDNAME="">    -1684.56
        <LEDGER NAME="AD Test Vendor"    RESERVEDNAME="">     1684.56
        <LEDGER NAME="Profit & Loss A/c" RESERVEDNAME="P&L"> -1684.56

    No voucher ever touches "Profit & Loss A/c" - Tally computes it as the
    running aggregate of the revenue and expense ledgers, so its balance is an
    exact MIRROR of the expense leg. Counting it made the trial balance total
    168456 instead of 0, i.e. the double-entry invariant looked broken while the
    books were perfectly fine.
    """
    payload = (
        "<ENVELOPE><COLLECTION>"
        '<LEDGER NAME="AD Test Expense" RESERVEDNAME="">'
        "<CLOSINGBALANCE>-1684.56</CLOSINGBALANCE></LEDGER>"
        '<LEDGER NAME="AD Test Vendor" RESERVEDNAME="">'
        "<CLOSINGBALANCE>1684.56</CLOSINGBALANCE></LEDGER>"
        '<LEDGER NAME="Profit &amp; Loss A/c" RESERVEDNAME="Profit &amp; Loss A/c">'
        "<CLOSINGBALANCE>-1684.56</CLOSINGBALANCE></LEDGER>"
        "</COLLECTION></ENVELOPE>"
    )
    balances = real.parse_closing_balances(payload)

    assert "Profit & Loss A/c" not in balances
    assert balances == {"AD Test Expense": 168456, "AD Test Vendor": -168456}


def test_a_real_trial_balance_sums_to_zero() -> None:
    """The conservation law, and the guard on the exclusion above.

    This is the check that does not need an expert or a label: debits and
    credits are equal or they are not. If a future Tally build reserves a
    ledger that IS posted to, `parse_closing_balances` would drop a real line
    and this assertion fails - which is the intended failure, rather than a
    silently wrong total.
    """
    payload = (
        "<ENVELOPE><COLLECTION>"
        '<LEDGER NAME="Purchases" RESERVEDNAME="">'
        "<CLOSINGBALANCE>-1180.00</CLOSINGBALANCE></LEDGER>"
        '<LEDGER NAME="Cash" RESERVEDNAME="">'
        "<CLOSINGBALANCE>1180.00</CLOSINGBALANCE></LEDGER>"
        '<LEDGER NAME="Profit &amp; Loss A/c" RESERVEDNAME="Profit &amp; Loss A/c">'
        "<CLOSINGBALANCE>-1180.00</CLOSINGBALANCE></LEDGER>"
        "</COLLECTION></ENVELOPE>"
    )
    balances = real.parse_closing_balances(payload)

    assert sum(balances.values()) == 0, (
        f"debits and credits must cancel exactly; got {balances} "
        f"summing to {sum(balances.values())} paise"
    )


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
    """A payload shaped like the ones a real TallyPrime actually sends.

    The `<BODY><DATA>` wrapper and the `<CMPINFO>` header are not decoration.
    Measured against TallyPrime 7.0 on 2026-08-08: EVERY response carries a
    `<CMPINFO>` block of counts, one of which is literally `<VOUCHER>0</VOUCHER>`
    - a count, not a voucher. This helper used to emit a bare `<COLLECTION>`, so
    no test ever saw that element, and the parser's whole-document scan for
    `VOUCHER` picked up the counter in production. An EMPTY company then looked
    like a corrupt export.

    Keeping CMPINFO here means the fixture can reproduce that failure, which is
    the whole point of a fixture: it is only worth trusting if it can be wrong
    in the ways the real thing is wrong.
    """
    return (
        "<ENVELOPE>"
        "<HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER>"
        "<BODY>"
        "<DESC><CMPINFO><COMPANY>1</COMPANY><LEDGER>4</LEDGER>"
        "<VOUCHER>0</VOUCHER></CMPINFO></DESC>"
        f"<DATA><COLLECTION>{''.join(bodies)}</COLLECTION></DATA>"
        "</BODY>"
        "</ENVELOPE>"
    )


TWO_LEGGED = (
    '<VOUCHER REMOTEID="ad_7" MASTERID="M11" VCHTYPE="Journal">'
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

# A3: the same voucher from a build that names the collection the other way.
SHORT_TAG = TWO_LEGGED.replace("ALLLEDGERENTRIES.LIST", "LEDGERENTRIES.LIST")


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


def test_the_other_builds_collection_name_is_read_the_same_way() -> None:
    """Assumption A3: some builds export `LEDGERENTRIES.LIST`. Reading only one
    name would report a whole company as unreadable."""
    page = real.parse_vouchers(_voucher_payload(SHORT_TAG))
    assert page.skipped == 0
    assert page.vouchers[0].debit_account == "Purchases"
    assert page.vouchers[0].credit_account == "Cash"


def test_a_response_using_both_collection_names_is_an_anomaly() -> None:
    """One build uses one name. A response with both is not something to merge
    quietly - half a company's vouchers would silently go the wrong way."""
    with pytest.raises(real.TallyDataError, match="anomaly"):
        real.parse_vouchers(_voucher_payload(TWO_LEGGED, SHORT_TAG))


def test_a_voucher_with_no_ledger_entries_at_all_is_an_unreadable_export() -> None:
    """A voucher cannot have no legs. Zero entries means the export did not
    carry them (A3), which is a broken read, not an empty voucher."""
    body = (
        '<VOUCHER MASTERID="M9" VCHTYPE="Journal"><DATE>20260807</DATE>'
        "<NARRATION>n</NARRATION></VOUCHER>"
    )
    with pytest.raises(real.TallyDataError, match="no ledger entries"):
        real.parse_vouchers(_voucher_payload(body))


def test_the_locators_tally_sent_are_preserved_exactly() -> None:
    """A5. Locators are kept as received; none of them is the identity."""
    body = TWO_LEGGED.replace(
        '<VOUCHER REMOTEID="ad_7"',
        '<VOUCHER GUID="9f-1a-2b" VCHKEY="0005c8d0:0000006c" REMOTEID="ad_7"',
    )
    page = real.parse_vouchers(_voucher_payload(body))
    assert dict(page.exported[0].locators) == {
        "VCHTYPE": "Journal",
        "MASTERID": "M11",
        "REMOTEID": "ad_7",
        "GUID": "9f-1a-2b",
        "VCHKEY": "0005c8d0:0000006c",
    }


def test_locators_are_read_from_child_elements_too() -> None:
    """Tally puts these on the element or in a child depending on the build."""
    body = (
        "<VOUCHER><DATE>20260807</DATE><NARRATION>n</NARRATION>"
        "<VOUCHERTYPENAME>Payment</VOUCHERTYPENAME>"
        "<MASTERID>M77</MASTERID><REMOTEID>ad_77</REMOTEID>"
        "<GUID>g-77</GUID><VCHKEY>k-77</VCHKEY>"
        "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Purchases</LEDGERNAME>"
        "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-10.00</AMOUNT>"
        "</ALLLEDGERENTRIES.LIST>"
        "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME>"
        "<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>10.00</AMOUNT>"
        "</ALLLEDGERENTRIES.LIST></VOUCHER>"
    )
    page = real.parse_vouchers(_voucher_payload(body))
    assert dict(page.exported[0].locators) == {
        "VCHTYPE": "Payment",
        "MASTERID": "M77",
        "REMOTEID": "ad_77",
        "GUID": "g-77",
        "VCHKEY": "k-77",
    }


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
    assert dict(page.exported[0].locators) == {"MASTERID": "M2"}


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
    """The mirror of `check_outgoing_legs`, on the way in. A1 is confirmed, so a
    leg that breaks it is a broken leg, not a reason to re-derive A1."""
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


def test_a_status_of_one_is_a_success() -> None:
    result = real.parse_import_response(import_response(created=1, status=1))
    assert result.status == 1
    assert result.ok is True
    assert "status=1" in result.summary()


def test_a_status_that_is_not_one_is_a_failure_however_clean_the_counters() -> None:
    """The silent failure this exists for: every counter says fine, the status
    says no."""
    result = real.parse_import_response(import_response(created=1, status=0))
    assert result.ok is False
    assert "status=0" in result.summary()


def test_a_response_with_no_status_is_judged_on_its_counters() -> None:
    """Absent and zero are different answers. A build that sends no STATUS is
    not thereby failing."""
    result = real.parse_import_response(import_response(created=1))
    assert result.status is None
    assert result.ok is True


def test_a_missing_counter_reads_as_zero() -> None:
    payload = "<ENVELOPE><IMPORTRESULT><CREATED>1</CREATED></IMPORTRESULT></ENVELOPE>"
    assert real.parse_import_response(payload) == real.ImportResult(created=1)


@pytest.mark.parametrize("tag", ["CREATED", "STATUS"])
def test_a_counter_that_is_not_a_number_raises(tag: str) -> None:
    payload = f"<ENVELOPE><IMPORTRESULT><{tag}>lots</{tag}></IMPORTRESULT></ENVELOPE>"
    with pytest.raises(real.TallyDataError, match=tag):
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
    # ledger check, duplicate-check read, the write, the read-back.
    assert sim.retry_flags == [True, True, False, True]


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


# ---- A10: the ledgers have to be there first -------------------------------


def _voucher_against(debit: str, credit: str) -> Voucher:
    return Voucher(
        id="draft-1",
        date=datetime.date(2026, 8, 7),
        party="Sharma Traders",
        narration="cement bags",
        debit_account=debit,
        credit_account=credit,
        amount_paise=118000,
    )


@pytest.mark.parametrize(
    ("debit", "credit", "named"),
    [
        ("Nowhere Ledger", "Cash", "'Nowhere Ledger'"),
        ("Purchases", "Missing Bank", "'Missing Bank'"),
        ("purchases", "Cash", "'purchases'"),
    ],
)
def test_a_write_against_a_ledger_that_does_not_exist_is_refused(
    sim: TallySim, debit: str, credit: str, named: str
) -> None:
    """A10's fourth condition and the second first-integration trap: Tally does
    not create the master for us, and the import can fail silently. The name is
    compared exactly, so a case difference is a refusal, not a guess."""
    client = real.RealTally(
        transport=sim, backups=real.RecordedBackups(frozenset({COMPANY}))
    )
    with pytest.raises(real.TallyDataError, match=named):
        client.write_voucher(COMPANY, _voucher_against(debit, credit), "ad_1")
    assert client.read_vouchers(COMPANY) == ()


def test_the_ledger_check_happens_before_the_import(sim: TallySim) -> None:
    client = real.RealTally(
        transport=sim, backups=real.RecordedBackups(frozenset({COMPANY}))
    )
    with pytest.raises(real.TallyDataError):
        client.write_voucher(COMPANY, _voucher_against("Nope", "Cash"), "ad_1")
    assert all("Import" not in payload for payload in sim.sent)


LEDGERS = (
    "<ENVELOPE><COLLECTION>"
    '<LEDGER NAME="Purchases"/><LEDGER NAME="Cash"/>'
    "</COLLECTION></ENVELOPE>"
)


def _client_with(replies: Sequence[str | Exception]) -> real.RealTally:
    """A client on canned replies, with the chart of accounts prepended.

    Every write reads the ledger list first (A10). None of these tests is about
    that read, so it is supplied here rather than in each of them.
    """
    return real.RealTally(
        transport=ScriptedTransport([LEDGERS, *replies]),
        backups=real.RecordedBackups(frozenset({COMPANY})),
    )


EMPTY_VOUCHERS = "<ENVELOPE><COLLECTION/></ENVELOPE>"


def test_a_rejected_write_raises_rather_than_reporting_a_tally_id() -> None:
    client = _client_with(
        [EMPTY_VOUCHERS, import_response(errors=1, line_errors=["no such ledger"])]
    )
    with pytest.raises(real.TallyRejected, match="no such ledger"):
        client.write_voucher(COMPANY, contract.a_voucher(), "ad_1")


def test_a_write_whose_status_says_no_is_a_failure() -> None:
    """Counters all clean, `<STATUS>` says the import failed. The status wins."""
    client = _client_with([EMPTY_VOUCHERS, import_response(created=1, status=0)])
    with pytest.raises(real.TallyRejected, match="rejected operation"):
        client.write_voucher(COMPANY, contract.a_voucher(), "ad_1")


def test_a_write_that_created_nothing_is_a_failure_not_a_success() -> None:
    """Zero errors and zero vouchers is Tally silently ignoring the payload."""
    client = _client_with([EMPTY_VOUCHERS, import_response(ignored=1)])
    with pytest.raises(real.TallyRejected, match="and created nothing"):
        client.write_voucher(COMPANY, contract.a_voucher(), "ad_1")


def test_a_write_that_created_one_and_ignored_something_is_ambiguous() -> None:
    """One voucher went out. If Tally both created and ignored, we cannot say
    what it dropped, so this is not reported as a success."""
    client = _client_with([EMPTY_VOUCHERS, import_response(created=1, ignored=1)])
    with pytest.raises(real.TallyRejected, match="ignored 1 part"):
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


# ---- A5: one marker, at most one voucher -----------------------------------


def _two_with_one_marker() -> str:
    return _voucher_payload(
        TWO_LEGGED, TWO_LEGGED.replace('MASTERID="M11"', 'MASTERID="M12"')
    )


def _client_reading(payload: str) -> real.RealTally:
    return real.RealTally(
        transport=ScriptedTransport([payload] * 4),
        backups=real.RecordedBackups(frozenset({COMPANY})),
    )


def test_one_marker_on_two_vouchers_is_an_ambiguity_not_a_choice() -> None:
    """A5. Two matches means somebody duplicated one of our entries. Picking
    either one is a coin flip with statutory consequences."""
    client = _client_reading(_two_with_one_marker())
    with pytest.raises(real.TallyDataError, match="matches 2 vouchers"):
        client.read_by_operation_id(COMPANY, "ad_7")


def test_an_ambiguous_marker_names_the_locators_it_could_not_choose_between() -> None:
    client = _client_reading(_two_with_one_marker())
    with pytest.raises(real.TallyDataError, match=r"MASTERID=M11.*MASTERID=M12"):
        client.read_by_operation_id(COMPANY, "ad_7")


def test_an_ambiguous_marker_with_nothing_to_name_says_so() -> None:
    """A duplicate that arrived with no locators at all still has to be
    reported, and the message has to admit it has nothing to point at."""
    bare = (
        "<VOUCHER><DATE>20260807</DATE>"
        "<NARRATION>rent [ACCOUNTANT_DAD:ad_7]</NARRATION>"
        "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Purchases</LEDGERNAME>"
        "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-10.00</AMOUNT>"
        "</ALLLEDGERENTRIES.LIST>"
        "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME>"
        "<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>10.00</AMOUNT>"
        "</ALLLEDGERENTRIES.LIST></VOUCHER>"
    )
    client = _client_reading(_voucher_payload(bare, bare))
    with pytest.raises(real.TallyDataError, match="no locators; no locators"):
        client.read_by_operation_id(COMPANY, "ad_7")


def test_an_ambiguous_marker_refuses_the_delete_too() -> None:
    """The whole point of the rule: no destructive action on an ambiguity."""
    client = _client_reading(_two_with_one_marker())
    with pytest.raises(real.TallyDataError, match="matches 2 vouchers"):
        client.reverse_by_operation_id(COMPANY, "ad_7")


def test_an_ambiguous_marker_refuses_the_write_too() -> None:
    """The duplicate check reads by marker, so a write into an ambiguous company
    stops there rather than adding a third."""
    client = real.RealTally(
        transport=ScriptedTransport([LEDGERS, _two_with_one_marker()]),
        backups=real.RecordedBackups(frozenset({COMPANY})),
    )
    with pytest.raises(real.TallyDataError, match="matches 2 vouchers"):
        client.write_voucher(COMPANY, contract.a_voucher(), "ad_7")


# ---- A6: the delete, end to end --------------------------------------------


def test_the_delete_that_goes_out_carries_the_read_back_locators(
    sim: TallySim, client: TallyClient
) -> None:
    """A6, end to end: the Master ID and VCHTYPE on the wire came from the read
    taken a moment earlier, not from our config or from the write we did before.

    `TAGVALUE` is `M1` - the ID the simulator minted when it accepted the write
    and handed back on the read - and `VCHTYPE` is the type that same read
    reported. The operation ID appears nowhere in the envelope: the delete is
    aimed by locators, never by our own identity for the voucher, never by
    amount, never by narration text.
    """
    op = new_operation_id()
    client.write_voucher(COMPANY, contract.a_voucher(), op)
    assert client.reverse_by_operation_id(COMPANY, op) is True

    deletes = [p for p in sim.sent if 'ACTION="Delete"' in p]
    assert len(deletes) == 1
    node = _parsed(deletes[0]).find(".//VOUCHER")
    assert node is not None
    assert node.get("VCHTYPE") == "Journal"
    assert node.get("TAGNAME") == "Master ID"
    assert node.get("TAGVALUE") == "M1"
    assert node.get("DATE") == "07-Aug-2026"
    assert op not in deletes[0]
    assert node.get("REMOTEID") is None


def test_a_deletion_tally_refuses_raises(sim: TallySim) -> None:
    client = real.RealTally(
        transport=sim, backups=real.RecordedBackups(frozenset({COMPANY}))
    )
    op = new_operation_id()
    client.write_voucher(COMPANY, contract.a_voucher(), op)

    sim.import_override = import_response(errors=1, line_errors=["cannot delete"])
    with pytest.raises(real.TallyRejected, match="refused to delete"):
        client.reverse_by_operation_id(COMPANY, op)


def test_a_deletion_that_deleted_nothing_is_a_failure(sim: TallySim) -> None:
    """Zero errors, zero deletions. That is Tally ignoring the payload, not
    agreeing with it."""
    client = real.RealTally(
        transport=sim, backups=real.RecordedBackups(frozenset({COMPANY}))
    )
    op = new_operation_id()
    client.write_voucher(COMPANY, contract.a_voucher(), op)

    sim.import_override = import_response(deleted=0, status=1)
    with pytest.raises(real.TallyRejected, match="deleted nothing"):
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


# ===========================================================================
# W1's TWIN: the connector read-back proves IDENTITY, not presence
# ===========================================================================
#
# `pipeline.post` was fixed on 2026-08-09 to compare the voucher Tally hands
# back against the one we sent. `RealTally.write_voucher` was not: its read-back
# asked "is there a voucher carrying my marker" and threw the answer away. Every
# caller that talks to the connector directly - and that is the layer that
# touches somebody's books - was still exposed.
#
# The rule these tests pin: a write succeeds only when TALLY'S OWN ANSWER proves
# OUR voucher is stored. Not "a voucher exists". Not our marker alone. Not HTTP
# 200.
#
# And the subtle half, which is the one that costs money: when Tally's import
# answer says a write happened and the read-back cannot confirm it, that is
# UNKNOWN_OUTCOME, not failure. A plain failure invites a retry, and a retry
# after a write that actually landed puts TWO statutory entries in the books.
# ---------------------------------------------------------------------------


def _register_payload(*bodies: str, company: str | None = None) -> str:
    """A voucher export, optionally echoing the company Tally answered for.

    The echo is assumption A12: authoritative when present, absent when the
    build does not send it. Both shapes are exercised below.
    """
    echo = (
        "<DESC><STATICVARIABLES>"
        f"<SVCURRENTCOMPANY>{_esc(company)}</SVCURRENTCOMPANY>"
        "</STATICVARIABLES></DESC>"
        if company is not None
        else ""
    )
    return (
        "<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER>"
        f"<BODY>{echo}"
        f"<DATA><COLLECTION>{''.join(bodies)}</COLLECTION></DATA>"
        "</BODY></ENVELOPE>"
    )


def _stored(
    *,
    date: str = "20260807",
    party: str = "Sharma Traders",
    debit: str = "Purchases",
    credit: str = "Cash",
    amount_paise: int = 118000,
    narration: str = "cement bags [ACCOUNTANT_DAD:ad_7]",
    master_id: str = "M11",
) -> str:
    """One voucher as Tally would export it. Defaults equal `contract.a_voucher()`."""
    return (
        f'<VOUCHER MASTERID="{_esc(master_id)}" VCHTYPE="Journal">'
        f"<DATE>{date}</DATE>"
        f"<VOUCHERNUMBER>{_esc(master_id)}</VOUCHERNUMBER>"
        f"<PARTYLEDGERNAME>{_esc(party)}</PARTYLEDGERNAME>"
        f"<NARRATION>{_esc(narration)}</NARRATION>"
        + _leg_xml(debit, -amount_paise, "Yes")
        + _leg_xml(credit, amount_paise, "No")
        + "</VOUCHER>"
    )


def _write_reading_back(
    read_back: str, *, voucher: Voucher | None = None, op: str = "ad_7"
) -> real.WriteResult:
    """Drive one write whose read-back is exactly `read_back`.

    The scripted replies are, in order: the chart of accounts (A10), the C5
    duplicate check (clean), Tally's import answer (created one), the read-back.
    """
    client = _client_with(
        [
            EMPTY_VOUCHERS,
            import_response(created=1, status=1, last_vch_id="M11"),
            read_back,
        ]
    )
    return client.write_voucher(COMPANY, voucher or contract.a_voucher(), op)


# ---- the pure verifier: every outcome has a name ---------------------------


SENT = contract.a_voucher()


def _same_as_sent(**changes: object) -> Voucher:
    return replace(SENT, **changes)  # type: ignore[arg-type]


def test_a_read_back_that_matches_field_for_field_is_an_exact_match() -> None:
    verdict = real.verify_read_back(
        company=COMPANY,
        sent=SENT,
        operation_id="ad_7",
        found=_same_as_sent(narration="cement bags [ACCOUNTANT_DAD:ad_7]"),
        found_in_company=COMPANY,
        tally_id="M11",
    )
    assert verdict.outcome is real.ReadBackOutcome.EXACT_MATCH
    assert verdict.confirmed is True
    assert verdict.fields == ()
    assert verdict.tally_id == "M11"


def test_zero_candidates_is_named_no_match_and_is_not_a_match() -> None:
    verdict = real.verify_read_back(
        company=COMPANY, sent=SENT, operation_id="ad_7", found=None
    )
    assert verdict.outcome is real.ReadBackOutcome.NO_MATCH
    assert verdict.confirmed is False


def test_a_read_back_from_a_different_company_is_wrong_company() -> None:
    verdict = real.verify_read_back(
        company=COMPANY,
        sent=SENT,
        operation_id="ad_7",
        found=SENT,
        found_in_company="Other Co",
    )
    assert verdict.outcome is real.ReadBackOutcome.WRONG_COMPANY
    assert "company" in verdict.fields
    assert "Other Co" in verdict.detail and COMPANY in verdict.detail


@pytest.mark.parametrize(
    ("changes", "outcome", "field"),
    [
        ({"amount_paise": 59000}, real.ReadBackOutcome.WRONG_AMOUNT, "amount_paise"),
        (
            {"date": datetime.date(2026, 8, 1)},
            real.ReadBackOutcome.WRONG_DATE,
            "date",
        ),
        (
            {"debit_account": "Sundry Expenses"},
            real.ReadBackOutcome.WRONG_LEDGER,
            "debit_account",
        ),
        (
            {"credit_account": "Sundry Expenses"},
            real.ReadBackOutcome.WRONG_LEDGER,
            "credit_account",
        ),
        ({"party": "Verma Cement"}, real.ReadBackOutcome.WRONG_LEDGER, "party"),
    ],
)
def test_each_changed_field_gets_its_own_named_outcome(
    changes: dict[str, object], outcome: real.ReadBackOutcome, field: str
) -> None:
    """One field wrong, one name for it, and the name of the field in the text."""
    verdict = real.verify_read_back(
        company=COMPANY,
        sent=SENT,
        operation_id="ad_7",
        found=_same_as_sent(**changes),
        found_in_company=COMPANY,
    )
    assert verdict.outcome is outcome
    assert verdict.fields == (field,)
    assert field in verdict.detail


def test_two_wrong_fields_are_both_named_not_just_the_first() -> None:
    """A refusal saying only that something is wrong sends a person through
    their whole ledger. Naming the amount and the party does not."""
    verdict = real.verify_read_back(
        company=COMPANY,
        sent=SENT,
        operation_id="ad_7",
        found=_same_as_sent(amount_paise=59000, party="Verma Cement"),
        found_in_company=COMPANY,
    )
    assert verdict.outcome is real.ReadBackOutcome.WRONG_AMOUNT
    assert set(verdict.fields) == {"amount_paise", "party"}
    assert "amount_paise" in verdict.detail
    assert "party" in verdict.detail
    assert "Verma Cement" in verdict.detail
    assert "59000" in verdict.detail


def test_the_narration_is_not_compared_because_we_stamp_it() -> None:
    """We add the marker to the narration, so it is EXPECTED to differ."""
    verdict = real.verify_read_back(
        company=COMPANY,
        sent=SENT,
        operation_id="ad_7",
        found=_same_as_sent(narration="anything at all"),
        found_in_company=COMPANY,
    )
    assert verdict.outcome is real.ReadBackOutcome.EXACT_MATCH


def test_no_verdict_a_write_can_produce_is_ever_safe_to_retry() -> None:
    """A verdict only exists AFTER a write went out. There is no value of it
    that makes an automatic retry safe."""
    for outcome in real.ReadBackOutcome:
        verdict = real.ReadBackVerdict(
            outcome=outcome, company=COMPANY, operation_id="ad_7"
        )
        assert verdict.safe_to_retry is False


# ---- the write path, end to end --------------------------------------------


def test_a_correct_voucher_read_back_is_accepted() -> None:
    """The control. Everything below refuses; this one must not."""
    result = _write_reading_back(_register_payload(_stored(), company=COMPANY))
    assert result.operation_id == "ad_7"
    assert result.tally_id == "M11"
    assert result.narration.endswith(marker_for("ad_7"))


def test_a_wrong_voucher_carrying_our_marker_is_refused_naming_the_field() -> None:
    """The whole defect in one test: the marker matches, the CONTENT does not.

    Tally answered `created=1`, our marker is on a voucher in the register, and
    that voucher is not the one we sent. Presence proves nothing.
    """
    payload = _register_payload(
        _stored(amount_paise=59000, party="Verma Cement"), company=COMPANY
    )
    with pytest.raises(real.TallyWriteMismatch) as refused:
        _write_reading_back(payload)

    assert refused.value.verdict.outcome is real.ReadBackOutcome.WRONG_AMOUNT
    text = str(refused.value)
    assert "amount_paise" in text
    assert "party" in text
    assert "59000" in text
    assert "Verma Cement" in text
    assert refused.value.safe_to_retry is False


@pytest.mark.parametrize(
    ("body", "outcome", "field"),
    [
        (_stored(date="20260801"), real.ReadBackOutcome.WRONG_DATE, "date"),
        (
            _stored(amount_paise=59000),
            real.ReadBackOutcome.WRONG_AMOUNT,
            "amount_paise",
        ),
        (
            _stored(debit="Sundry Expenses"),
            real.ReadBackOutcome.WRONG_LEDGER,
            "debit_account",
        ),
        (
            _stored(credit="Sundry Expenses"),
            real.ReadBackOutcome.WRONG_LEDGER,
            "credit_account",
        ),
        (_stored(party="Verma Cement"), real.ReadBackOutcome.WRONG_LEDGER, "party"),
    ],
    ids=["date", "amount", "debit", "credit", "party"],
)
def test_one_wrong_field_on_the_write_path_names_that_field(
    body: str, outcome: real.ReadBackOutcome, field: str
) -> None:
    with pytest.raises(real.TallyWriteMismatch) as refused:
        _write_reading_back(_register_payload(body, company=COMPANY))
    assert refused.value.verdict.outcome is outcome
    assert refused.value.verdict.fields == (field,)
    assert field in str(refused.value)


def test_a_register_answering_for_another_company_is_refused() -> None:
    """A correct answer on port 9000 does not mean the right company is open.

    Tally echoed a DIFFERENT company on the read-back. The voucher matches field
    for field, and it is still not proof: it is proof about somebody else's book.
    """
    payload = _register_payload(_stored(), company="Other Co")
    with pytest.raises(real.TallyWriteMismatch) as refused:
        _write_reading_back(payload)
    assert refused.value.verdict.outcome is real.ReadBackOutcome.WRONG_COMPANY
    assert "company" in refused.value.verdict.fields
    assert "Other Co" in str(refused.value)


def test_a_build_that_echoes_no_company_is_not_treated_as_a_mismatch() -> None:
    """A12. Absent is "cannot check", never "wrong"."""
    result = _write_reading_back(_register_payload(_stored()))
    assert result.tally_id == "M11"


def test_two_candidates_are_refused_and_neither_is_picked() -> None:
    """A5, on the read-back. Two vouchers carry one marker; choosing either is a
    coin flip with statutory consequences."""
    both = _register_payload(
        _stored(master_id="M11"), _stored(master_id="M12"), company=COMPANY
    )
    with pytest.raises(real.AmbiguousMarker) as refused:
        _write_reading_back(both)
    assert refused.value.outcome is real.ReadBackOutcome.MULTIPLE_MATCHES
    assert "matches 2 vouchers" in str(refused.value)
    assert "MASTERID=M11" in str(refused.value)
    assert "MASTERID=M12" in str(refused.value)


def test_zero_candidates_after_a_claimed_create_is_unknown_not_failed() -> None:
    """THE SUBTLE ONE.

    Tally said `status=1 created=1`. The register does not show it. That is not
    "the write failed" - the voucher may be there and unreadable to us - and
    reporting failure invites a retry that would create a SECOND entry.
    """
    with pytest.raises(real.TallyWriteUnknown) as unknown:
        _write_reading_back(_register_payload(company=COMPANY))

    assert unknown.value.verdict.outcome is real.ReadBackOutcome.UNKNOWN_OUTCOME
    assert unknown.value.safe_to_retry is False
    text = str(unknown.value)
    assert "UNKNOWN" in text
    assert "ad_7" in text
    assert "retr" in text, "the message has to say not to retry it"


def test_unknown_is_a_different_class_from_a_definite_mismatch() -> None:
    """We-cannot-tell and Tally-stored-the-wrong-thing are different facts, and
    a caller must be able to branch on them without reading English."""
    assert not issubclass(real.TallyWriteMismatch, real.TallyWriteUnknown)
    assert not issubclass(real.TallyWriteUnknown, real.TallyWriteMismatch)
    assert issubclass(real.TallyWriteUnknown, real.TallyWriteUnverified)
    assert issubclass(real.TallyWriteMismatch, real.TallyWriteUnverified)
    # Still a refusal to everything upstream that already fails closed on it.
    assert issubclass(real.TallyWriteUnverified, real.TallyRejected)


def test_every_read_back_refusal_names_its_outcome_the_same_way() -> None:
    """One accessor across the whole family, so a caller branching on the
    outcome does not need to know which class it caught."""
    with pytest.raises(real.TallyWriteUnknown) as unknown:
        _write_reading_back(_register_payload(company=COMPANY))
    assert unknown.value.outcome is real.ReadBackOutcome.UNKNOWN_OUTCOME

    with pytest.raises(real.TallyWriteMismatch) as wrong:
        _write_reading_back(_register_payload(_stored(amount_paise=1), company=COMPANY))
    assert wrong.value.outcome is real.ReadBackOutcome.WRONG_AMOUNT

    with pytest.raises(real.AmbiguousMarker) as ambiguous:
        _write_reading_back(
            _register_payload(
                _stored(master_id="M11"), _stored(master_id="M12"), company=COMPANY
            )
        )
    assert ambiguous.value.outcome is real.ReadBackOutcome.MULTIPLE_MATCHES


def test_a_correct_voucher_without_our_marker_is_unknown_and_says_so() -> None:
    """DECIDED: an unmarked lookalike is NOT accepted, and NOT called a failure.

    Not accepted, because the narration marker is this system's identity (A5).
    A voucher that merely matches on content could be the person's own hand-typed
    entry for the same bill, and accepting a coincidence as proof is how you post
    twice and reverse somebody else's work.

    Not a failure either: a write that lost its narration is exactly what this
    looks like from outside. So it is UNKNOWN, and the message names the
    lookalike so a person knows where to look instead of hunting the ledger.
    """
    lookalike = _stored(narration="cement bags")  # our content, no marker
    with pytest.raises(real.TallyWriteUnknown) as unknown:
        _write_reading_back(_register_payload(lookalike, company=COMPANY))

    assert unknown.value.verdict.outcome is real.ReadBackOutcome.UNKNOWN_OUTCOME
    text = str(unknown.value)
    assert "marker" in text
    assert "M11" in text, "the lookalike has to be pointed at"


def test_an_unmarked_voucher_that_is_nothing_like_ours_is_not_reported_as_one() -> None:
    """The control for the test above: a lookalike claim must be earned."""
    other = _stored(
        amount_paise=9900, party="Verma Cement", narration="a wholly different entry"
    )
    with pytest.raises(real.TallyWriteUnknown) as unknown:
        _write_reading_back(_register_payload(other, company=COMPANY))
    assert "unmarked voucher" not in str(unknown.value)


@pytest.mark.parametrize(
    "payload",
    [
        "<ENVELOPE><BODY><DATA><COLLECTION><VOUCHER>",
        "Tally is busy, please try later",
        _register_payload(
            '<VOUCHER MASTERID="M11" VCHTYPE="Journal"><DATE>20260807</DATE>'
            "<NARRATION>cement bags [ACCOUNTANT_DAD:ad_7]</NARRATION></VOUCHER>"
        ),
        _register_payload(_stored(date="not-a-date")),
    ],
    ids=["truncated", "not_xml", "no_ledger_entries", "unreadable_date"],
)
def test_a_malformed_register_response_is_refused_not_read_as_absent(
    payload: str,
) -> None:
    """A read-back we cannot read is not evidence of anything, least of all
    absence. It is named, and it is not retryable."""
    with pytest.raises(real.MalformedRegisterResponse) as refused:
        _write_reading_back(payload)
    assert refused.value.verdict.outcome is real.ReadBackOutcome.MALFORMED_RESPONSE
    assert refused.value.safe_to_retry is False
    assert "ad_7" in str(refused.value)


def test_the_original_parser_error_survives_into_the_refusal() -> None:
    """A person debugging this needs the sentence the parser actually produced."""
    payload = _register_payload(
        '<VOUCHER MASTERID="M11" VCHTYPE="Journal"><DATE>20260807</DATE>'
        "<NARRATION>cement bags [ACCOUNTANT_DAD:ad_7]</NARRATION></VOUCHER>"
    )
    with pytest.raises(real.MalformedRegisterResponse, match="no ledger entries"):
        _write_reading_back(payload)


# ---- the case that is not hypothetical -------------------------------------


class MovesTheDate(TallySim):
    """A TallyPrime in Educational mode, in the one way that matters here.

    Educational mode accepts only the 1st, 2nd and 31st. Measured 2026-08-08:
    2026-08-07 REJECTED, 2026-08-31 ACCEPTED. This double does the worse thing -
    it takes the voucher, answers `created=1 status=1`, and stores it under a
    date nobody asked for.
    """

    coerced = datetime.date(2026, 8, 1)

    def _import(self, root: ElementTree.Element, company: str | None) -> str:
        node = root.find(".//VOUCHER")
        answer = super()._import(root, company)
        if node is not None and node.get("ACTION") == "Create":
            assert company is not None
            self.companies[company].vouchers[-1].date = self.coerced
        return answer


def test_a_date_tally_moved_under_us_is_refused_at_the_connector() -> None:
    """The read-back is the only thing standing between a coerced date and a
    filing period nobody chose."""
    sim = MovesTheDate()
    sim.add_company(COMPANY, ACCOUNTS)
    client = real.RealTally(
        transport=sim, backups=real.RecordedBackups(frozenset({COMPANY}))
    )
    op = new_operation_id()

    with pytest.raises(real.TallyWriteMismatch) as refused:
        client.write_voucher(COMPANY, contract.a_voucher(), op)

    assert refused.value.verdict.outcome is real.ReadBackOutcome.WRONG_DATE
    assert refused.value.verdict.fields == ("date",)
    assert "2026-08-07" in str(refused.value)
    assert "2026-08-01" in str(refused.value)
    # The entry really is in the books under the wrong date, and the refusal is
    # what tells somebody to go and look.
    assert sim.companies[COMPANY].vouchers[0].date == MovesTheDate.coerced


def test_the_read_back_verification_sends_no_new_request_shape(sim: TallySim) -> None:
    """The verification is built out of the reads this connector already makes.

    A custom TDL <REPORT> wedged a live TallyPrime on 2026-08-09. Nothing about
    proving identity is worth a third request family.
    """
    client = real.RealTally(
        transport=sim, backups=real.RecordedBackups(frozenset({COMPANY}))
    )
    client.write_voucher(COMPANY, contract.a_voucher(), new_operation_id())

    assert sim.sent, "nothing was sent, so nothing was proved"
    for payload in sim.sent:
        lowered = payload.lower()
        for tag in ("<report", "<form", "<part ", "<part>", "<line", "<field"):
            assert tag not in lowered, f"the write path emitted {tag!r}"
        if "<TALLYREQUEST>Export</TALLYREQUEST>" in payload:
            assert "<TYPE>Collection</TYPE>" in payload
        else:
            assert "<TALLYREQUEST>Import</TALLYREQUEST>" in payload
            assert "<TYPE>Data</TYPE>" in payload


def test_every_named_outcome_is_produced_by_something_here() -> None:
    """Nine names, and none of them decoration. If a name exists it has to be
    reachable, or it is a comment pretending to be code."""
    assert {o.value for o in real.ReadBackOutcome} == {
        "EXACT_MATCH",
        "NO_MATCH",
        "MULTIPLE_MATCHES",
        "WRONG_COMPANY",
        "WRONG_LEDGER",
        "WRONG_DATE",
        "WRONG_AMOUNT",
        "MALFORMED_RESPONSE",
        "UNKNOWN_OUTCOME",
    }


# ---------------------------------------------------------------------------
# A live TallyPrime was WEDGED on 2026-08-09, and this is the guard against it.
#
# While probing for a licence-mode read, a hand-written request containing a
# custom TDL <REPORT>/<FORM>/<PART>/<LINE>/<FIELD> construct was sent to the
# real TallyPrime 7 at 192.168.64.2:9000. Tally accepted the TCP connection and
# then never answered — not that request and not any request after it. The
# gateway stayed wedged through 10 polls over three minutes. TCP kept accepting;
# HTTP was dead. Recovering it needs the application restarted, which is a GUI
# action nobody could take remotely (`utmctl exec` produced no output at all).
#
# The request came from a probe, not from this connector. The connector has
# never sent that shape. But "has never" is not "cannot", and the cost of
# finding out the hard way is somebody's Tally becoming unresponsive mid-post.
#
# So the shapes are pinned. Two families, and no third:
#     Export + Collection   the four reads
#     Import + Data         the two writes
# Anything else — a REPORT, a FORM, a Function export, a TDL report definition —
# must be a deliberate, reviewed change that turns this test red first.
# ---------------------------------------------------------------------------

_PINNED_VOUCHER = Voucher(
    id="v1",
    # 2026-08-31, not the 2026-08-07 contract fixture. Nothing here is a claim
    # about that fixture; these tests only inspect the SHAPE of the XML we emit
    # and never send it anywhere.
    date=datetime.date(2026, 8, 31),
    party="Sharma Traders",
    narration="cement",
    debit_account="Purchases",
    credit_account="Cash",
    amount_paise=118000,
)

_ALL_BUILDERS = (
    real.build_company_list_request(),
    real.build_ledger_list_request("Demo Co"),
    real.build_closing_balance_request("Demo Co"),
    real.build_voucher_list_request("Demo Co"),
    real.build_voucher_create(
        "Demo Co",
        _PINNED_VOUCHER,
        "cement [ACCOUNTANT_DAD:ad_probe]",
        "ad_probe",
        "Journal",
    ),
    real.build_voucher_delete(
        "Demo Co",
        real.ExportedVoucher(
            voucher=_PINNED_VOUCHER,
            locators={"MASTERID": "3", "VCHTYPE": "Journal"},
        ),
        "ad_probe",
    ),
)


@pytest.mark.parametrize("xml", _ALL_BUILDERS)
def test_no_request_we_can_build_contains_a_tdl_report_definition(xml: str) -> None:
    """The exact construct that wedged a live Tally. Never ours to send."""
    lowered = xml.lower()
    for tag in ("<report", "<form", "<part ", "<part>", "<line", "<field"):
        assert tag not in lowered, (
            f"a request builder emits {tag!r}. A custom TDL report definition "
            "wedged a live TallyPrime 7 on 2026-08-09 and it had to be "
            "restarted by hand. If this is deliberate, it needs a live "
            "soak test before it ships."
        )


@pytest.mark.parametrize("xml", _ALL_BUILDERS)
def test_every_request_is_one_of_the_two_permitted_shapes(xml: str) -> None:
    """Export+Collection to read, Import+Data to write. No third family.

    Pinned as a whitelist rather than a blacklist on purpose: a blacklist only
    forbids the harmful shapes somebody has already thought of, and the one
    that wedged Tally was not on anybody's list until it happened.
    """
    if "<TALLYREQUEST>Export</TALLYREQUEST>" in xml:
        assert "<TYPE>Collection</TYPE>" in xml, "an Export that is not a Collection"
    elif "<TALLYREQUEST>Import</TALLYREQUEST>" in xml:
        assert "<TYPE>Data</TYPE>" in xml, "an Import that is not Data"
    else:
        raise AssertionError(f"neither Export nor Import: {xml[:120]}")


def test_a_function_export_is_not_something_this_connector_can_produce() -> None:
    """`<TYPE>Function</TYPE>` is how the licence probe was attempted.

    Every shape of it was refused by the live Tally ("Could not find:
    $$LicenseInfo:IsEducationalMode"), and the TDL-report workaround is what
    wedged it. Recorded as a measured dead end so nobody re-derives it, and
    pinned so it cannot arrive by accident.
    """
    for xml in _ALL_BUILDERS:
        assert "<TYPE>Function</TYPE>" not in xml

"""The four MVP modules, measured rather than described.

WHAT IS UNDER TEST, AND WHY THESE FOUR TOGETHER
------------------------------------------------
    accountant/tallyio/errors.py     what Tally said went wrong
    accountant/tallyio/writedoor.py  whether we are allowed to ask at all
    accountant/tallyio/audit.py      what we sent and what came back
    accountant/tallyio/masters.py    the one write the door currently permits

They are one path, not four features: `masters.create_ledger` asks the door,
records the attempt in the audit log, and reads Tally's answer through
`errors.classify`. Testing them apart and never together would leave the joins
unmeasured, and the joins are where this repository has actually been bitten.

NO SOCKET IS OPENED ANYWHERE IN THIS FILE
------------------------------------------
Every test that needs a transport injects `FakeTally` below. Nothing here
constructs `HttpTransport`, and nothing names a host or a port. A test that
reaches 127.0.0.1 passes or fails on whether somebody happens to have TallyPrime
running, which makes it a measurement of the machine rather than of the code.

EVERY GUARD HAS A CONTROL, AND THE CONTROL IS THE POINT
--------------------------------------------------------
For each refusal asserted here there is a second test feeding the same predicate
an input it must NOT refuse. `validate_ledger` that returns `ok=False` for
everything passes half of this file; it fails the other half. A guard that has
never been watched failing is a guard nobody has evidence for.

WHAT THIS FILE DOES NOT PROVE
------------------------------
Anything about a real TallyPrime. Every response below is a string this file
wrote, shaped after the ones recorded on 2026-08-12, and a string cannot tell
you that TallyPrime still answers that way after an upgrade. Evidence class is
FAKETALLY throughout.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from accountant.tallyio import audit, errors, masters, writedoor

#: The one company `writedoor.ALLOWED_WRITES` names. Spelled exactly as Tally
#: holds it: the permit matches character for character on purpose.
COMPANY = "TANVEER SIDHU"

GROUP = "Sundry Creditors"

#: A Collection response with no ledgers in it - the state the live company was
#: measured in on 2026-08-12, and what `exists()` sees before anything is made.
EMPTY_COLLECTION = (
    "<ENVELOPE><BODY><DATA><COLLECTION></COLLECTION></DATA></BODY></ENVELOPE>"
)


def collection_of(*names: str) -> str:
    """A Collection response listing these ledgers, as `exists()` reads them."""
    body = "".join(
        f'<LEDGER NAME="{name}"><NAME>{name}</NAME><PARENT>{GROUP}</PARENT></LEDGER>'
        for name in names
    )
    return (
        "<ENVELOPE><BODY><DATA>"
        f"<COLLECTION>{body}</COLLECTION>"
        "</DATA></BODY></ENVELOPE>"
    )


def import_reply(*, created: int = 1, complaint: str = "") -> str:
    """An Import response, shaped after the ones TallyPrime actually returns.

    The `<LINEERROR/>` when there is no complaint is not decoration: Tally emits
    the empty element on a clean import, and "an empty tag is not a complaint"
    is one of the things this file measures.
    """
    line_error = f"<LINEERROR>{complaint}</LINEERROR>" if complaint else "<LINEERROR/>"
    return (
        "<ENVELOPE><BODY><DATA><IMPORTRESULT>"
        f"<CREATED>{created}</CREATED><ALTERED>0</ALTERED>"
        "<IGNORED>0</IGNORED><ERRORS>0</ERRORS><EXCEPTIONS>0</EXCEPTIONS>"
        f"{line_error}"
        "</IMPORTRESULT></DATA></BODY></ENVELOPE>"
    )


class FakeTally:
    """One XML in, one scripted answer out, and a record of everything asked.

    Satisfies `accountant.tallyio.real.Transport` structurally, which is the
    whole reason `Masters` takes a transport rather than building one: a test
    can prove that NOTHING was sent, and "nothing was sent" is the only
    assertion that distinguishes a refusal from a failed write.
    """

    def __init__(
        self, *, on_import: str = "", on_export: str = EMPTY_COLLECTION
    ) -> None:
        self.on_import = on_import or import_reply()
        self.on_export = on_export
        self.sent: list[str] = []
        self.retry_flags: list[bool] = []

    @property
    def imports_sent(self) -> int:
        return sum(1 for payload in self.sent if "Import</TALLYREQUEST>" in payload)

    def send(self, payload: str, *, retry: bool) -> str:
        self.sent.append(payload)
        self.retry_flags.append(retry)
        if "Import</TALLYREQUEST>" in payload:
            return self.on_import
        return self.on_export


def a_logger(tmp_path: Path) -> audit.JsonLineAuditLogger:
    """An audit logger writing under the test's own directory, never `./logs`."""
    return audit.JsonLineAuditLogger(tmp_path / "log", tmp_path / "xml")


# =============================================================================
# errors.py - what Tally said, in words a person can act on
# =============================================================================


def test_a_clean_import_response_carries_no_error_at_all() -> None:
    """The response that means "it worked" must classify as nothing.

    This is the direction that costs money when it is wrong. A false refusal on
    a successful write makes the caller retry, and a retried ledger creation
    against a company that already has the ledger is the duplicate-master
    failure this package spends a whole module avoiding.
    """
    assert errors.classify(import_reply(created=1)) is None


def test_a_missing_ledger_is_classified_and_names_the_ledger_to_create() -> None:
    """The code alone is not actionable; the NAME is what somebody goes and fixes.

    A refusal that says "a ledger is missing" sends a person to look through a
    company of two hundred masters. One that says which ledger is a two-second
    job, and the name is sitting in the response either way.
    """
    failure = errors.classify(
        import_reply(created=0, complaint="Ledger 'Sharma Traders' does not exist!")
    )

    assert failure is not None
    assert failure.code == errors.LEDGER_UNKNOWN
    assert failure.entity == "Sharma Traders"
    assert isinstance(failure, errors.TallyBusinessError)


def test_a_complaint_nobody_recognises_is_counted_and_never_read_as_success() -> None:
    """`None` means "Tally was happy". An unknown complaint is not that.

    The classification target is a percentage, and a percentage needs a
    denominator. Returning `None` for text this code does not recognise would
    make every unrecognised refusal disappear from the numerator AND the
    denominator - the rate would read 100% precisely when it was worst.
    """
    failure = errors.classify(
        import_reply(created=0, complaint="Undocumented internal condition 7742")
    )

    assert failure is not None
    assert failure.code == errors.UNCLASSIFIED
    assert "7742" in failure.message


def test_the_classification_rate_is_one_when_tally_complained_about_nothing() -> None:
    """Nothing to classify is not a failure to classify.

    Zero out of zero has to be 1.0 rather than a division by zero or a 0.0,
    because a run of successful imports would otherwise drag the measured rate
    to the floor and the gate would fire on the healthiest possible day.
    """
    assert errors.describe(import_reply(created=1)).rate == 1.0


def test_the_rate_falls_when_a_complaint_is_not_recognised() -> None:
    """The control for the test above: 1.0 must be measured, not returned.

    A `rate` hard-coded to 1.0 passes the previous test perfectly. This one
    feeds it one recognised complaint and one unrecognised one and requires the
    number to move.
    """
    report = errors.describe(
        "<ENVELOPE><BODY>"
        "<LINEERROR>Ledger 'Acme Supplies' does not exist</LINEERROR>"
        "<LINEERROR>Undocumented internal condition 7742</LINEERROR>"
        "</BODY></ENVELOPE>"
    )

    assert len(report.classified) == 1
    assert len(report.unclassified) == 1
    assert report.rate == 0.5


def test_the_counter_row_of_a_successful_import_is_not_read_as_a_refusal() -> None:
    """The measured defect of 2026-08-12, kept from coming back.

    `DESC` was in `ERROR_ELEMENTS`. It is a generic container - it appears in
    REQUESTS too - and a successful import answers with a `<DESC>` holding a row
    of zero counters and the company name. Scraping it reported every successful
    import as an unclassified refusal, and two ledgers were genuinely created
    while this code said `success: False`.

    A false refusal is the worse direction: it makes a caller retry a write that
    already happened.
    """
    successful_import = (
        "<ENVELOPE><BODY>"
        f"<DESC><STATICVARIABLES>{COMPANY}</STATICVARIABLES>"
        "0 0 1 0 0 0 0 0 0 0</DESC>"
        "<DATA><IMPORTRESULT><CREATED>1</CREATED><LINEERROR/></IMPORTRESULT></DATA>"
        "</BODY></ENVELOPE>"
    )

    assert errors.classify(successful_import) is None
    assert errors.describe(successful_import).unclassified == []


def test_a_real_complaint_in_that_same_shaped_response_is_still_caught() -> None:
    """The control. Ignoring `DESC` must not mean ignoring the response.

    The cheapest way to make the test above pass is to stop reading responses at
    all. This feeds the identical envelope with a genuine `LINEERROR` inside it
    and requires the refusal to come back out.
    """
    refused = (
        "<ENVELOPE><BODY>"
        f"<DESC><STATICVARIABLES>{COMPANY}</STATICVARIABLES>"
        "0 0 0 0 0 0 0 0 0 0</DESC>"
        "<DATA><IMPORTRESULT><CREATED>0</CREATED>"
        "<LINEERROR>Group 'Sundry Creditor' does not exist</LINEERROR>"
        "</IMPORTRESULT></DATA>"
        "</BODY></ENVELOPE>"
    )

    failure = errors.classify(refused)

    assert failure is not None
    assert failure.code == errors.GROUP_UNKNOWN
    assert failure.entity == "Sundry Creditor"


def test_desc_is_not_one_of_the_elements_a_complaint_is_read_from() -> None:
    """The structural half of the same defect, named so a revert is loud.

    The behavioural test above can be satisfied by several different fixes. This
    one pins the specific decision: complaints come out of `LINEERROR`, and
    `DESC` is a container that carries the request's own static variables back.

    The second assertion is what stops this being vacuous - emptying the tuple
    would satisfy the first one and disable classification entirely.
    """
    assert "DESC" not in errors.ERROR_ELEMENTS
    assert "LINEERROR" in errors.ERROR_ELEMENTS


def test_an_empty_line_error_element_means_no_error_on_that_line() -> None:
    """`<LINEERROR/>` is Tally saying the line was fine, in the shape of a complaint.

    Any check that reads "is the element present" rather than "does it say
    anything" refuses every successful import ever sent.
    """
    assert errors.classify("<ENVELOPE><LINEERROR/></ENVELOPE>") is None
    assert errors.classify("<ENVELOPE><LINEERROR>   </LINEERROR></ENVELOPE>") is None


def test_a_zero_error_counter_is_a_count_and_not_a_complaint() -> None:
    """`<ERRORS>0</ERRORS>` says there were none. It is in `ERROR_ELEMENTS` because
    Tally also puts real text there, so the two have to be told apart by content.
    """
    assert errors.classify("<ENVELOPE><ERRORS>0</ERRORS></ENVELOPE>") is None


def test_error_text_in_that_same_element_is_a_complaint() -> None:
    """The control for the counter test: `ERRORS` is still read when it speaks."""
    failure = errors.classify(
        "<ENVELOPE><ERRORS>Voucher Type 'Purchase Bill' does not exist</ERRORS>"
        "</ENVELOPE>"
    )

    assert failure is not None
    assert failure.code == errors.VOUCHER_TYPE_UNKNOWN


def test_every_pattern_carries_a_code_and_a_sentence_saying_what_to_do() -> None:
    """A code with no next action is a code nobody can use.

    The message is what reaches a person. `LEDGER_UNKNOWN` on its own tells a
    bookkeeper nothing they can act on, so every pattern owes a sentence.
    """
    silent = [p.code for p in errors.PATTERNS if len(p.said) < 40]

    assert silent == []
    assert all(p.code for p in errors.PATTERNS)
    assert len(errors.PATTERNS) >= 10


def test_the_sentence_check_would_catch_a_pattern_added_without_one() -> None:
    """The control. The predicate above must be able to say no."""
    import re

    bare = errors.Pattern("SOMETHING_BROKE", re.compile(r"broke"), "broke")

    assert len(bare.said) < 40


# =============================================================================
# writedoor.py - whether we are allowed to ask at all
# =============================================================================


def test_creating_a_ledger_in_the_one_named_company_is_permitted() -> None:
    """The write the door exists to let through.

    A door that refuses everything is not a safe door, it is a broken product.
    This is the case every refusal test below is measured against.
    """
    permit = writedoor.allow_write("create_ledger", COMPANY)

    assert isinstance(permit, writedoor.Permit)
    assert permit.op == "create_ledger"
    assert permit.company == COMPANY
    assert permit.destructive is False


def test_a_write_nobody_wrote_a_permit_for_is_refused_before_any_bytes_leave() -> None:
    """An operation that is not on the list was never authorised by anybody.

    The allow-list is not a filter over a set of known-bad operations; it is the
    complete set of known-good ones. Anything else is refused by default, and
    the refusal names the operations that ARE allowed so the reader can see
    whether they meant one of them.
    """
    with pytest.raises(writedoor.WriteNotAllowedError) as refusal:
        writedoor.allow_write("delete_all_vouchers", COMPANY)

    assert refusal.value.code == "NOT_ALLOW_LISTED"
    assert "create_ledger" in refusal.value.message


def test_an_allow_listed_write_into_somebody_elses_company_is_refused() -> None:
    """The company is half the permit, and it is the half that gets forgotten.

    TallyPrime's gateway serves whichever company is open on screen. A permit
    that matched on the operation alone would authorise creating a ledger inside
    a company nobody named - which, for an accountant running four sets of books
    on one machine, is somebody else's statutory record.
    """
    with pytest.raises(writedoor.WriteNotAllowedError) as refusal:
        writedoor.allow_write("create_ledger", "SOME OTHER FIRM")

    assert refusal.value.code == "NOT_ALLOW_LISTED"
    assert refusal.value.entity == "SOME OTHER FIRM"


def test_a_company_name_that_differs_only_in_case_is_a_different_company() -> None:
    """Exact match, no folding. The control below shows the exact name still works.

    Tally reports the company name it was created with. A permit that matched
    loosely would let 'tanveer sidhu' through, and there is no guarantee that
    the loosely-matched name is the company anybody meant.
    """
    with pytest.raises(writedoor.WriteNotAllowedError):
        writedoor.allow_write("create_ledger", COMPANY.lower())

    assert writedoor.allow_write("create_ledger", COMPANY).company == COMPANY


def test_turning_posting_off_refuses_even_the_write_that_has_a_permit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One variable stops everything, and the refusal says nothing was sent.

    The distinction carried in the message matters more than the refusal: a
    caller told "it failed" retries; a caller told "writing is switched off,
    nothing was attempted" does not.
    """
    monkeypatch.setenv(writedoor.ENV_POSTING, "0")

    with pytest.raises(writedoor.WriteNotAllowedError) as refusal:
        writedoor.allow_write("create_ledger", COMPANY)

    assert refusal.value.code == "POSTING_DISABLED"
    assert writedoor.posting_enabled() is False


def test_posting_is_on_when_the_variable_was_never_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control, and a deliberate design decision rather than an oversight.

    This flag FAILS OPEN. A variable that had to be set for writes to work means
    a deployment that forgot it posts nothing while reporting success - which is
    the exact silent-nothing failure this repository keeps finding. The security
    boundary is the permit list and the web layer's authentication, not this.
    """
    monkeypatch.delenv(writedoor.ENV_POSTING, raising=False)

    assert writedoor.posting_enabled() is True
    assert writedoor.allow_write("create_ledger", COMPANY).op == "create_ledger"


def test_an_unreadable_value_for_the_flag_does_not_read_as_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ACCOUNTANT_POSTING_ENABLED=maybe` is not the string "1", and must not
    therefore mean "off". Truthiness on an environment string is how a typo
    silently disables a product.
    """
    monkeypatch.setenv(writedoor.ENV_POSTING, "maybe")

    assert writedoor.posting_enabled() is True


def test_every_permit_carries_a_reason_longer_than_a_hundred_characters() -> None:
    """A permit with no written reason is the thing the allow-list exists to prevent.

    `POSTING_ENABLED=True` was the alternative design: one boolean for every
    write in the system. The allow-list is only better than that boolean if
    adding an entry costs somebody a paragraph a reviewer reads in the diff.
    Drop the paragraph and it is a boolean again, spelled longer.
    """
    thin = [p.op for p in writedoor.ALLOWED_WRITES if len(p.why) <= 100]

    assert thin == []
    assert len(writedoor.ALLOWED_WRITES) >= 2


def test_the_reason_check_would_catch_a_permit_added_without_one() -> None:
    """The control. Watch the predicate refuse something before trusting it."""
    lazy = writedoor.Permit(op="create_ledger", company=COMPANY, why="seems fine")

    assert len(lazy.why) <= 100


def test_a_refusal_from_the_door_is_a_policy_error_because_nothing_was_attempted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A policy error and a business error need different people.

    A business error means Tally looked at the data and said no - fix the data
    and retry. A policy error means nothing was sent, so retrying the identical
    call is refused identically for ever. The fix is a decision.
    """
    monkeypatch.setenv(writedoor.ENV_POSTING, "0")

    with pytest.raises(errors.TallyPolicyError):
        writedoor.allow_write("create_ledger", COMPANY)

    assert issubclass(writedoor.WriteNotAllowedError, errors.TallyPolicyError)
    assert not issubclass(writedoor.WriteNotAllowedError, errors.TallyBusinessError)


# =============================================================================
# audit.py - what we sent, what came back, and how long it took
# =============================================================================


def test_one_operation_writes_exactly_one_line(tmp_path: Path) -> None:
    """One line per operation is what makes the log countable.

    Two lines for one operation double every count in `summary()`; zero lines
    means the operation that went wrong is the one with no record of it.
    """
    logger = a_logger(tmp_path)

    with logger.record("create_ledger", company=COMPANY, name="Sharma") as entry:
        entry.status = "success"

    assert len(logger.path.read_text(encoding="utf-8").splitlines()) == 1


def test_two_operations_write_two_lines(tmp_path: Path) -> None:
    """The control. "Exactly one line" must be per operation, not per file."""
    logger = a_logger(tmp_path)

    for name in ("Sharma", "Acme"):
        with logger.record("create_ledger", name=name) as entry:
            entry.status = "success"

    assert len(logger.path.read_text(encoding="utf-8").splitlines()) == 2


def test_the_line_is_written_even_when_the_operation_raises(tmp_path: Path) -> None:
    """A logger that only records successes describes a system nobody has trouble with.

    `finally`, not `else`. The operation that blew up mid-write is precisely the
    one somebody will be trying to reconstruct at 2am, and it is also the one
    where "did the write land" cannot be answered without the request XML.
    """
    logger = a_logger(tmp_path)

    with pytest.raises(ZeroDivisionError), logger.record("create_ledger") as entry:
        entry.request_xml = "<ENVELOPE>sent before the failure</ENVELOPE>"
        raise ZeroDivisionError("the transport exploded")

    written = audit.recent(directory=tmp_path / "log")

    assert len(written) == 1
    assert written[0]["status"] == "failure"
    assert "the transport exploded" in str(written[0]["error_summary"])


def test_the_request_and_response_xml_land_in_files_the_line_points_at(
    tmp_path: Path,
) -> None:
    """The line stays short and names two files, and the files hold the evidence.

    A Tally response is routinely tens of kilobytes. Inlining it makes a log no
    `tail` can read. A pointer to a file that does not exist is worse than
    either, so both halves are checked here: the path is in the line, and the
    bytes are at the path.
    """
    logger = a_logger(tmp_path)
    request = masters.ledger_xml(COMPANY, "Sharma Traders", GROUP)
    response = import_reply(created=1)

    with logger.record("create_ledger", company=COMPANY) as entry:
        entry.request_xml = request
        entry.response_xml = response
        entry.status = "success"

    line = audit.recent(directory=tmp_path / "log")[0]
    request_path = Path(str(line["request_xml_path"]))
    response_path = Path(str(line["response_xml_path"]))

    assert request_path.read_text(encoding="utf-8") == request
    assert response_path.read_text(encoding="utf-8") == response
    assert "create_ledger" in request_path.name


def test_an_operation_that_sent_no_xml_names_no_file(tmp_path: Path) -> None:
    """The control. An empty path is honest; a path to an empty file is not.

    A refusal from the write door sends nothing, so there is no request XML to
    keep. Writing a zero-byte file for it would put a piece of evidence in the
    trail that says a request existed.
    """
    logger = a_logger(tmp_path)

    with logger.record("create_ledger", company=COMPANY) as entry:
        entry.status = "failure"

    line = audit.recent(directory=tmp_path / "log")[0]

    assert line["request_xml_path"] == ""
    assert line["response_xml_path"] == ""
    assert not (tmp_path / "xml").exists()


def test_summary_counts_each_operation_and_its_failures_separately(
    tmp_path: Path,
) -> None:
    """Totals answer "how is it going"; per-operation counts answer "what is broken".

    A single success rate over every operation hides the case that matters: the
    reads are all fine and every write is failing, which averages to a number
    that looks survivable.
    """
    logger = a_logger(tmp_path)
    for status in ("success", "success", "failure"):
        with logger.record("create_ledger") as entry:
            entry.status = status
    with logger.record("list_ledgers") as entry:
        entry.status = "success"

    counts = audit.summary(directory=tmp_path / "log")

    assert counts["operations_count"] == 4
    assert counts["by_operation"] == {"create_ledger": 3, "list_ledgers": 1}
    assert counts["failures_by_operation"] == {"create_ledger": 1}
    assert counts["success_rate"] == 0.75


def test_summary_of_a_log_nothing_has_been_written_to_is_not_a_crash(
    tmp_path: Path,
) -> None:
    """The control, and the state every fresh install is in.

    Zero operations is not zero percent success. A dashboard that divides by the
    count crashes on a machine where nothing has happened yet, which is exactly
    when somebody is looking at it to find out whether anything has happened.
    """
    counts = audit.summary(directory=tmp_path / "nothing-here")

    assert counts["operations_count"] == 0
    assert counts["success_rate"] == 1.0
    assert audit.recent(directory=tmp_path / "nothing-here") == []


def test_a_half_written_final_line_does_not_hide_the_lines_before_it(
    tmp_path: Path,
) -> None:
    """The process was killed mid-flush. That is one lost line, not a lost log.

    The line that cannot be parsed is by definition the operation that ended in
    a kill, and refusing to read the file because of it would lose the ninety
    nine records that explain what led up to it.
    """
    logger = a_logger(tmp_path)
    for name in ("Sharma", "Acme"):
        with logger.record("create_ledger", name=name) as entry:
            entry.status = "success"
    with logger.path.open("a", encoding="utf-8") as handle:
        handle.write('{"operation": "create_led')

    survived = audit.recent(directory=tmp_path / "log")

    assert len(survived) == 2
    assert [str(line["inputs"]) for line in survived] != []
    assert audit.summary(directory=tmp_path / "log")["operations_count"] == 2


def test_a_line_is_json_a_person_and_a_machine_can_both_read(tmp_path: Path) -> None:
    """One line, valid JSON, carrying the operation, its inputs and its duration.

    JSON lines rather than a database because the first tool anybody reaches for
    at 2am is `tail`, and the second is `jq`.
    """
    logger = a_logger(tmp_path)

    with logger.record("create_ledger", company=COMPANY, group=GROUP) as entry:
        entry.status = "success"

    parsed = cast(
        "dict[str, object]",
        json.loads(logger.path.read_text(encoding="utf-8").splitlines()[0]),
    )

    assert parsed["operation"] == "create_ledger"
    assert parsed["inputs"] == {"company": COMPANY, "group": GROUP}
    assert isinstance(parsed["duration_ms"], int)
    assert parsed["timestamp"]


# =============================================================================
# masters.py - the one write the door currently permits
# =============================================================================


def test_a_float_opening_balance_is_refused_before_anything_is_sent() -> None:
    """Money is integer paise. A float opening balance is a rounded rupee waiting.

    The annotation says `int` and annotations are not enforced at runtime: a CSV
    row or an LLM tool-call reaches here as `1250.5`. An opening balance is the
    number every later balance in that ledger inherits, so one paise adrift here
    is one paise adrift in every trial balance afterwards.

    `cast` is how the value arrives in production too - through a boundary that
    promised an int and handed over a float.
    """
    check = masters.validate_ledger(
        "Sharma Traders", GROUP, opening_paise=cast("int", 1250.50)
    )

    assert check.ok is False
    assert [p.code for p in check.problems] == ["OPENING_NOT_PAISE"]
    assert check.would_send_xml == ""


def test_the_same_opening_balance_as_whole_paise_is_accepted() -> None:
    """The control. The guard must refuse the float, not the number.

    A `validate_ledger` that refused every opening balance would pass the test
    above and make the feature useless.
    """
    check = masters.validate_ledger("Sharma Traders", GROUP, opening_paise=125050)

    assert check.ok is True
    assert check.problems == []
    assert "<OPENINGBALANCE>1250.50</OPENINGBALANCE>" in check.would_send_xml


def test_a_boolean_opening_balance_is_refused_because_true_would_become_one_paise() -> (
    None
):
    """`isinstance(True, int)` is True, so an isinstance check would let this through.

    A flag that reaches an amount field is not a rounding error, it is a number
    somebody invented. `True` becoming one paise is the specific way it lands.
    """
    check = masters.validate_ledger("Sharma Traders", GROUP, opening_paise=True)

    assert check.ok is False
    assert [p.code for p in check.problems] == ["OPENING_NOT_PAISE"]


def test_a_group_tally_does_not_have_is_refused_and_the_refusal_names_it() -> None:
    """Wrong group spelling is the commonest master failure and Tally's message for
    it is unhelpful, so it is caught here where the message can name the group and
    list the ones that exist.
    """
    check = masters.validate_ledger("Sharma Traders", "Sundry Creditor")

    assert check.ok is False
    assert [p.code for p in check.problems] == [errors.GROUP_UNKNOWN]
    assert check.problems[0].entity == "Sundry Creditor"
    assert "Sundry Creditors" in check.problems[0].said


def test_the_same_group_spelled_the_way_tally_spells_it_is_accepted() -> None:
    """The control. One character apart from the test above, and it must pass."""
    check = masters.validate_ledger("Sharma Traders", "Sundry Creditors")

    assert check.ok is True
    assert check.would_send_xml != ""


def test_a_ledger_name_containing_markup_is_refused_rather_than_escaped() -> None:
    """The request is XML and Tally reads `<` as structure.

    Escaping it would send a ledger whose name is not the name that was asked
    for, which is worse than refusing: nobody finds out until somebody looks in
    Tally for a master that is not there under that spelling.
    """
    check = masters.validate_ledger("Acme <b>Supplies</b>", GROUP)

    assert check.ok is False
    assert [p.code for p in check.problems] == ["NAME_HAS_MARKUP"]
    assert check.would_send_xml == ""


def test_an_ampersand_in_a_supplier_name_is_escaped_rather_than_refused() -> None:
    """The control, and a real Indian supplier name.

    "Sharma & Sons" is legal in Tally and illegal as raw XML. A guard that
    refused every character XML cares about would refuse a large fraction of
    genuine party names, and would then be deleted by whoever hit that.
    """
    check = masters.validate_ledger("Sharma & Sons", GROUP)

    assert check.ok is True
    assert "Sharma &amp; Sons" in check.would_send_xml
    assert "Sharma & Sons" not in check.would_send_xml


def test_an_empty_ledger_name_is_refused() -> None:
    """A master with no name is not a master. Whitespace is not a name either."""
    assert masters.validate_ledger("   ", GROUP).ok is False
    assert masters.validate_ledger("A", GROUP).ok is True


def test_a_dry_run_sends_nothing_and_hands_back_the_xml_it_would_have_sent(
    tmp_path: Path,
) -> None:
    """A dry run that sends one byte is not a dry run.

    This is the assertion that only an injected transport can make: the fake was
    never called at all. `success=True` on the result means "this would go", not
    "this went", and the XML is returned so a person can read the exact bytes
    before authorising them.
    """
    fake = FakeTally()
    agent = masters.Masters(COMPANY, transport=fake, log=a_logger(tmp_path))

    result = agent.create_ledger("Sharma Traders", GROUP, dry_run=True)

    assert fake.sent == []
    assert result.success is True
    assert result.confirmed is False
    assert "Nothing was sent" in result.summary
    assert result.request_xml == masters.ledger_xml(COMPANY, "Sharma Traders", GROUP)


def test_the_same_call_without_dry_run_does_send(tmp_path: Path) -> None:
    """The control. "Nothing was sent" must be the dry run, not the whole class.

    A `Masters` whose transport was never wired up would pass the test above
    perfectly and create no ledgers for ever.
    """
    fake = FakeTally(
        on_import=import_reply(created=1),
        on_export=collection_of("Sharma Traders"),
    )
    agent = masters.Masters(COMPANY, transport=fake, log=a_logger(tmp_path))

    result = agent.create_ledger("Sharma Traders", GROUP)

    assert fake.imports_sent == 1
    assert result.success is True
    assert result.confirmed is True


def test_a_write_is_never_retried_by_the_transport(tmp_path: Path) -> None:
    """A connection that dies after Tally committed looks exactly like one that
    died before it did, so a retried write is a second ledger with the same name.
    """
    fake = FakeTally(
        on_import=import_reply(created=1),
        on_export=collection_of("Sharma Traders"),
    )
    agent = masters.Masters(COMPANY, transport=fake, log=a_logger(tmp_path))

    agent.create_ledger("Sharma Traders", GROUP)

    assert fake.retry_flags == [False, False]


def test_a_refused_ledger_never_reaches_the_transport(tmp_path: Path) -> None:
    """Validation is not advice. A refusal has to stop the send, not annotate it.

    The float opening balance is refused by `validate_ledger`; this proves the
    refusal is wired into `create_ledger` rather than being a report the write
    path ignores.
    """
    fake = FakeTally()
    agent = masters.Masters(COMPANY, transport=fake, log=a_logger(tmp_path))

    result = agent.create_ledger(
        "Sharma Traders", GROUP, opening_paise=cast("int", 1250.50)
    )

    assert fake.sent == []
    assert result.success is False
    assert result.error is not None
    assert result.error.code == "OPENING_NOT_PAISE"
    assert isinstance(result.error, errors.TallyPolicyError)


def test_created_zero_with_no_complaint_is_reported_as_a_failure(
    tmp_path: Path,
) -> None:
    """The silent nothing. Nothing was wrong and nothing happened.

    Tally answers HTTP 200 with `STATUS 1` and no complaint, and creates
    nothing. Reporting that as success is how an empty company looks populated -
    and the caller goes on to post a voucher naming a ledger that is not there.
    """
    fake = FakeTally(on_import=import_reply(created=0), on_export=EMPTY_COLLECTION)
    agent = masters.Masters(COMPANY, transport=fake, log=a_logger(tmp_path))

    result = agent.create_ledger("Ghost Ledger", GROUP)

    assert result.success is False
    assert result.confirmed is False
    assert result.error is not None
    assert result.error.code == errors.UNCLASSIFIED
    assert "created nothing" in result.summary


def test_created_one_and_a_read_back_that_finds_it_is_a_success(
    tmp_path: Path,
) -> None:
    """The control. `CREATED 0` must be the refusal, not every answer.

    `confirmed` is separate from `success` on purpose: `success` means Tally did
    not complain, `confirmed` means the master was afterwards found by name.
    Those came apart in this repository before.
    """
    fake = FakeTally(
        on_import=import_reply(created=1),
        on_export=collection_of("Sharma Traders"),
    )
    agent = masters.Masters(COMPANY, transport=fake, log=a_logger(tmp_path))

    result = agent.create_ledger("Sharma Traders", GROUP)

    assert result.success is True
    assert result.confirmed is True
    assert result.error is None


def test_a_write_tally_accepted_and_did_not_keep_is_reported_as_unconfirmed(
    tmp_path: Path,
) -> None:
    """`CREATED 1` and the ledger is not there afterwards. Both facts are kept.

    Collapsing `confirmed` into `success` is how a write Tally accepted and did
    not keep goes unnoticed, so the summary says so in words as well.
    """
    fake = FakeTally(on_import=import_reply(created=1), on_export=EMPTY_COLLECTION)
    agent = masters.Masters(COMPANY, transport=fake, log=a_logger(tmp_path))

    result = agent.create_ledger("Sharma Traders", GROUP)

    assert result.success is True
    assert result.confirmed is False
    assert "read-back could not find it" in result.summary


def test_tally_refusing_the_ledger_is_reported_with_the_reason_it_gave(
    tmp_path: Path,
) -> None:
    """A duplicate name is a business error: the data is wrong and can be fixed.

    `already_existed` is set from it because "it is already there" is the one
    refusal a caller can treat as success.
    """
    fake = FakeTally(
        on_import=import_reply(created=0, complaint="Ledger already exists"),
        on_export=EMPTY_COLLECTION,
    )
    agent = masters.Masters(COMPANY, transport=fake, log=a_logger(tmp_path))

    result = agent.create_ledger("Sharma Traders", GROUP)

    assert result.success is False
    assert result.already_existed is True
    assert result.error is not None
    assert result.error.code == errors.DUPLICATE_NAME


def test_creating_a_ledger_leaves_one_audit_line_holding_both_xml_blobs(
    tmp_path: Path,
) -> None:
    """The join between the three modules, measured rather than assumed.

    `create_ledger` asks the door, records the attempt, and reads the answer.
    The audit line is the only artefact that survives the process, so it has to
    carry the request and the response - not a summary of them.
    """
    fake = FakeTally(
        on_import=import_reply(created=1),
        on_export=collection_of("Sharma Traders"),
    )
    agent = masters.Masters(COMPANY, transport=fake, log=a_logger(tmp_path))

    agent.create_ledger("Sharma Traders", GROUP)
    lines = audit.recent(directory=tmp_path / "log")

    assert len(lines) == 1
    assert lines[0]["operation"] == "create_ledger"
    request = Path(str(lines[0]["request_xml_path"])).read_text(encoding="utf-8")
    response = Path(str(lines[0]["response_xml_path"])).read_text(encoding="utf-8")
    assert "Sharma Traders" in request
    assert "<CREATED>1</CREATED>" in response


def test_the_write_door_stops_a_ledger_in_an_unnamed_company_before_the_transport(
    tmp_path: Path,
) -> None:
    """The door is asked before any bytes leave, so a refusal sends nothing.

    A guard consulted after the send is not a guard, it is a log line. The
    company here is valid and the group is valid; only the permit is missing.
    """
    fake = FakeTally()
    agent = masters.Masters("SOME OTHER FIRM", transport=fake, log=a_logger(tmp_path))

    with pytest.raises(writedoor.WriteNotAllowedError) as refusal:
        agent.create_ledger("Sharma Traders", GROUP)

    assert refusal.value.code == "NOT_ALLOW_LISTED"
    assert fake.sent == []


def test_a_company_that_was_never_named_is_refused_at_construction() -> None:
    """The gateway serves whichever company is open on screen.

    Building a `Masters` with no company would send requests into whatever
    happens to be loaded, which for an accountant with four sets of books is a
    coin toss over whose statutory record gets written.
    """
    with pytest.raises(errors.TallyPolicyError) as refusal:
        masters.Masters("   ", transport=FakeTally())

    assert refusal.value.code == errors.COMPANY_NOT_OPEN


def test_an_opening_balance_reaches_the_xml_as_exact_rupees_and_paise() -> None:
    """Paise become rupees once, in the last step before the string.

    Anywhere earlier and the number is a float for part of its life. The XML is
    what Tally parses, so this is the only place the conversion can be checked
    against what actually gets sent.
    """
    xml = masters.ledger_xml(COMPANY, "Sharma Traders", GROUP, opening_paise=125050)

    assert "<OPENINGBALANCE>1250.50</OPENINGBALANCE>" in xml
    assert "1250.5<" not in xml


def test_a_zero_opening_balance_sends_no_opening_balance_element_at_all() -> None:
    """The control on the element's presence, and the honest thing to send.

    Zero is what almost every new ledger opens at. Sending `0.00` claims an
    opening balance was stated; omitting the element says nothing, which is
    what happened.
    """
    xml = masters.ledger_xml(COMPANY, "Sharma Traders", GROUP)

    assert "OPENINGBALANCE" not in xml
    assert "OPENINGBALANCE" in masters.ledger_xml(
        COMPANY, "Sharma Traders", GROUP, opening_paise=1
    )


def test_the_request_names_the_company_it_is_addressed_to() -> None:
    """An Import envelope with no `SVCURRENTCOMPANY` is addressed to whoever is open."""
    xml = masters.ledger_xml(COMPANY, "Sharma Traders", GROUP)

    assert f"<SVCURRENTCOMPANY>{COMPANY}</SVCURRENTCOMPANY>" in xml
    assert "<TALLYREQUEST>Import</TALLYREQUEST>" in xml

"""A credential on a customer's laptop, in a file anyone who can read that
laptop can read.

WHAT THIS FILE PROVES
---------------------
That a secret handed to the product cannot reach an application log — not in a
message, not in a `%s` argument, not inside the `str()` of an exception, and
not inside a traceback — and that the log is still worth reading afterwards.

`docs/DATA_POLICY.md` §4 lists "no secret is ever logged" as a claim with a
test that *could* exist. Until this file, none did: Table B row 3 said a
connector key is "never logged, in any form" and the only thing standing behind
that sentence was that nobody had written the logging call yet.

WHAT THIS FILE DELIBERATELY DOES **NOT** PROVE
----------------------------------------------
That the audit trail is redacted. It is not, on purpose, by owner decision, and
two tests here assert that it is STILL carrying the vendor and the amount after
everything else has been scrubbed. `action_log` is the record of what this
software did to a real business's statutory books; an audit row that cannot say
which party and how much is a timestamp, not evidence.

THE CONTROL COMES FIRST
-----------------------
`test_an_unfiltered_handler_writes_the_secret_straight_to_the_file` plants the
credential in a log built WITHOUT the filter and proves it lands on disk. Every
assertion after it is known to be running against a defect that is real, rather
than against a string that was never going to appear anyway.
"""

from __future__ import annotations

import ast
import datetime
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from accountant import redact
from accountant.agent.connector import (
    ConnectorIdentity,
    build_logger,
)
from accountant.auth.identity import (
    hash_password,
    new_token,
    token_fingerprint,
)
from accountant.memory.identity import normalise_company
from accountant.memory.store import MemoryStore
from accountant.schema import NOT_RECORDED, ActionLog

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "accountant" / "agent" / "cli.py"
COMPANY = "Demo Co"

#: Long enough to be learned, distinctive enough that a hit is never a
#: coincidence. See `redact.MIN_LEARNABLE` for why length matters here.
A_CONNECTOR_KEY = "kM7-quixotic-hovercraft-42"
A_VENDOR = "Sharma Traders"


@pytest.fixture
def clean_redactor() -> redact.Redactor:
    """A redactor of its own, so one test cannot teach another one's values.

    The shipped program shares one process-wide instance on purpose — see
    `redact.default_redactor` — which is exactly why a test must not use it.
    """
    return redact.Redactor()


def _logger(tmp_path: Path, name: str, redactor: redact.Redactor) -> logging.Logger:
    """`build_logger`, with a redactor this test owns rather than the shared one."""
    log = build_logger(tmp_path / f"{name}.log", name=name)
    for handler in log.handlers:
        handler.filters.clear()
        handler.addFilter(redact.RedactingFilter(redactor))
    return log


def _written(log: logging.Logger, path: Path) -> str:
    for handler in log.handlers:
        handler.flush()
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 0. the control: the defect is real
# ---------------------------------------------------------------------------


def test_an_unfiltered_handler_writes_the_secret_straight_to_the_file(
    tmp_path: Path,
) -> None:
    """The disconfirming case, first.

    A guard is only evidence if the thing it guards against actually happens
    without it. This builds the same handler `build_logger` builds, omits the
    one line that installs the filter, and shows the credential on disk.
    """
    path = tmp_path / "unguarded.log"
    handler = RotatingFileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    log = logging.getLogger("unguarded-probe")
    log.handlers.clear()
    log.setLevel(logging.INFO)
    log.propagate = False
    log.addHandler(handler)

    log.info("registering with secret=%s", A_CONNECTOR_KEY)
    handler.flush()

    assert A_CONNECTOR_KEY in path.read_text(encoding="utf-8"), (
        "the control failed: the secret did not reach the file even with no "
        "filter installed, so every assertion below would pass vacuously"
    )
    handler.close()


def test_the_same_line_through_build_logger_carries_no_secret(
    tmp_path: Path, clean_redactor: redact.Redactor
) -> None:
    """The control's twin. One line different, and the credential is gone."""
    clean_redactor.learn_secret(A_CONNECTOR_KEY)
    log = _logger(tmp_path, "guarded-probe", clean_redactor)

    log.info("registering with secret=%s", A_CONNECTOR_KEY)

    assert A_CONNECTOR_KEY not in _written(log, tmp_path / "guarded-probe.log")


# ---------------------------------------------------------------------------
# 1. a secret in the message BODY, where no key name travels with it
# ---------------------------------------------------------------------------


def test_a_secret_in_a_message_body_is_redacted(
    tmp_path: Path, clean_redactor: redact.Redactor
) -> None:
    """The case a key-name pattern cannot reach.

    `the cloud rejected kM7-...` names no field. Nothing about those characters
    says "credential". Only knowing the value finds it, which is why
    `learn_secret` exists beside the patterns rather than instead of them.
    """
    clean_redactor.learn_secret(A_CONNECTOR_KEY)
    log = _logger(tmp_path, "body-probe", clean_redactor)

    log.warning("the cloud rejected %s as stale", A_CONNECTOR_KEY)

    written = _written(log, tmp_path / "body-probe.log")
    assert A_CONNECTOR_KEY not in written
    assert redact.HIDDEN in written
    assert "as stale" in written, "the sentence around it must survive"


def test_a_secret_hidden_in_a_positional_argument_is_redacted(
    tmp_path: Path, clean_redactor: redact.Redactor
) -> None:
    """The bug a naive filter ships with.

    `%s` substitution happens in the FORMATTER, after every filter has run. A
    filter that scrubbed `record.msg` alone would rewrite the format string,
    leave the credential sitting in `record.args`, and the formatter would
    interpolate it into the file untouched.
    """
    clean_redactor.learn_secret(A_CONNECTOR_KEY)
    log = _logger(tmp_path, "args-probe", clean_redactor)

    log.info("payload=%r attempt=%d", {"secret": A_CONNECTOR_KEY}, 3)

    written = _written(log, tmp_path / "args-probe.log")
    assert A_CONNECTOR_KEY not in written
    assert "attempt=3" in written, "the other argument must still be there"


def test_an_unregistered_secret_beside_its_key_name_is_still_redacted(
    tmp_path: Path, clean_redactor: redact.Redactor
) -> None:
    """The other half of the pair. Nothing was learned here at all."""
    log = _logger(tmp_path, "pattern-probe", clean_redactor)

    log.info("posting {'secret': 'never-registered-anywhere'} to the cloud")

    written = _written(log, tmp_path / "pattern-probe.log")
    assert "never-registered-anywhere" not in written
    assert clean_redactor.learned_count == 0, "this proves the PATTERN, not a value"


# ---------------------------------------------------------------------------
# 2. a secret inside an exception
# ---------------------------------------------------------------------------


def test_a_secret_in_an_exception_str_is_redacted(
    tmp_path: Path, clean_redactor: redact.Redactor
) -> None:
    """The realistic leak, and the reason this is not paranoia.

    `Connector._failed` logs `f"{type(exc).__name__}: {exc}"`, and a library
    that puts the request body into its error message puts the connector
    secret into that string. Nobody wrote a logging call naming a credential;
    the credential arrived inside somebody else's error text.
    """
    clean_redactor.learn_secret(A_CONNECTOR_KEY)
    log = _logger(tmp_path, "exc-probe", clean_redactor)
    failure = OSError(f"POST failed, body was {{'secret': '{A_CONNECTOR_KEY}'}}")

    log.error("job=%s outcome=FAILED %s: %s", "job-1", type(failure).__name__, failure)

    written = _written(log, tmp_path / "exc-probe.log")
    assert A_CONNECTOR_KEY not in written
    assert "job=job-1" in written, "the job id is how anybody finds this row"
    assert "OSError" in written, "the exception TYPE is not a secret and must stay"


def test_a_secret_inside_a_logged_traceback_is_redacted(
    tmp_path: Path, clean_redactor: redact.Redactor
) -> None:
    """`log.exception` renders the traceback in the FORMATTER, not the filter.

    So the filter has to render it first and hand the formatter the scrubbed
    text, or the credential arrives on disk through a path no assertion about
    `record.msg` ever looks at.
    """
    clean_redactor.learn_secret(A_CONNECTOR_KEY)
    log = _logger(tmp_path, "traceback-probe", clean_redactor)

    try:
        raise ValueError(f"cloud refused {A_CONNECTOR_KEY}")
    except ValueError:
        log.exception("poll failed")

    written = _written(log, tmp_path / "traceback-probe.log")
    assert A_CONNECTOR_KEY not in written
    assert "ValueError" in written, "the traceback must still name the exception"
    assert "test_redaction.py" in written, "and still name the line it came from"


# ---------------------------------------------------------------------------
# 3. session tokens and their fingerprints
# ---------------------------------------------------------------------------


def test_a_token_fingerprint_is_redacted(
    tmp_path: Path, clean_redactor: redact.Redactor
) -> None:
    """A fingerprint is not the token, and it is still not loggable.

    It identifies a live session, and it confirms a guessed token for anybody
    holding one. `docs/DATA_POLICY.md` row 2 allows the session *id* in a log
    and refuses the token; the sha256 of the token is the token's shadow, so it
    goes with the token.

    Caught by SHAPE — 64 hex characters — with nothing registered, because the
    fingerprint of a session nobody has opened yet cannot be registered.
    """
    fingerprint = token_fingerprint(new_token())
    log = _logger(tmp_path, "fingerprint-probe", clean_redactor)

    log.info("session lookup for %s failed", fingerprint)

    written = _written(log, tmp_path / "fingerprint-probe.log")
    assert len(fingerprint) == 64, "sha256 hex; the rule is written to this length"
    assert fingerprint not in written
    assert "session lookup for" in written


def test_a_password_verifier_is_redacted(
    tmp_path: Path, clean_redactor: redact.Redactor
) -> None:
    """A scrypt verifier at dklen=64 is 128 hex characters. Row 1 of
    `docs/DATA_POLICY.md`: never the password, never the verifier, not a
    prefix."""
    verifier, salt = hash_password("correct horse battery staple")
    log = _logger(tmp_path, "verifier-probe", clean_redactor)

    log.info("stored password_hash=%s salt=%s", verifier, salt)

    written = _written(log, tmp_path / "verifier-probe.log")
    assert verifier not in written
    assert salt not in written, "salt= beside its key name goes too"


def test_a_bearer_header_is_redacted(
    tmp_path: Path, clean_redactor: redact.Redactor
) -> None:
    log = _logger(tmp_path, "bearer-probe", clean_redactor)

    log.info("upstream said 401 for Authorization: Bearer abc.def.ghi")

    written = _written(log, tmp_path / "bearer-probe.log")
    assert "abc.def.ghi" not in written
    assert "401" in written


def test_a_credential_inside_a_url_is_redacted(
    tmp_path: Path, clean_redactor: redact.Redactor
) -> None:
    """`--cloud-url https://user:hunter2@cloud.example` is a credential in the
    one value the startup banner is proudest of printing."""
    log = _logger(tmp_path, "url-probe", clean_redactor)

    log.info("dialling https://connector-1:hunter2@cloud.example/jobs")

    written = _written(log, tmp_path / "url-probe.log")
    assert "hunter2" not in written
    assert "cloud.example/jobs" in written, "which host was called is diagnostic"


def test_the_environment_printed_whole_is_redacted(
    tmp_path: Path, clean_redactor: redact.Redactor
) -> None:
    """`log.info("env=%s", os.environ)` is one keystroke away at 3 a.m. and
    prints every credential the machine holds."""
    log = _logger(tmp_path, "environ-probe", clean_redactor)

    log.info(
        "starting with environ({'PATH': '/usr/bin', "
        "'ACCOUNTANT_CONNECTOR_SECRET': 'kM7-quixotic'})"
    )

    written = _written(log, tmp_path / "environ-probe.log")
    assert "kM7-quixotic" not in written
    assert "/usr/bin" not in written, "the whole dump goes, not the parts we guessed"
    assert "starting with" in written


# ---------------------------------------------------------------------------
# 4. vendor names and amounts — in the LOG, and only in the log
# ---------------------------------------------------------------------------


def test_a_vendor_name_in_a_log_line_is_redacted_but_keeps_its_length(
    tmp_path: Path, clean_redactor: redact.Redactor
) -> None:
    """Length survives and the name does not.

    `docs/DATA_POLICY.md` row 6 permits the LENGTH of entry text where the text
    is refused, and length is what separates "the field was empty" from "the
    field was truncated" — the bug people actually chase.

    `%r`, not `%s`, because that is what this codebase writes: every refusal in
    `connector.refusal_for` formats a name with `!r`. See the test below for
    the case where it does not, and what closes it.
    """
    log = _logger(tmp_path, "vendor-probe", clean_redactor)

    log.info("no mapping for vendor=%r", A_VENDOR)

    written = _written(log, tmp_path / "vendor-probe.log")
    assert A_VENDOR not in written
    assert f"len={len(A_VENDOR)}" in written
    assert "no mapping for" in written


def test_an_unquoted_two_word_vendor_name_needs_the_learned_layer(
    tmp_path: Path, clean_redactor: redact.Redactor
) -> None:
    """The honest limit of the pattern layer, written down rather than hidden.

    `vendor=Sharma Traders` unquoted: a key-name rule cannot tell where the
    value ends without eating the rest of the line, so it stops at the space
    and the surname survives. That is a real hole, it is why `learn_private`
    exists beside the patterns, and it is stated in `docs/REDACTION.md`.
    """
    log = _logger(tmp_path, "unquoted-probe", clean_redactor)

    log.info("first line, patterns only: vendor=%s", A_VENDOR)
    clean_redactor.learn_private(A_VENDOR, "vendor")
    log.info("second line, value learned: vendor=%s", A_VENDOR)

    lines = _written(log, tmp_path / "unquoted-probe.log").splitlines()
    assert "Traders" in lines[0], "the limit is real, and this records it"
    assert "Traders" not in lines[1], "and the learned layer closes it"
    assert f"len={len(A_VENDOR)}" in lines[1]


def test_an_amount_in_a_log_line_keeps_no_shape_at_all(
    tmp_path: Path, clean_redactor: redact.Redactor
) -> None:
    """The one place shape is refused, and the reason is arithmetic.

    The digit count IS the magnitude. `len=9` on a rupee figure publishes the
    order of magnitude of somebody's invoice, which is the fact being hidden.
    """
    log = _logger(tmp_path, "amount-probe", clean_redactor)

    log.info("check failed for amount_paise=%d and ₹1,18,000.00", 11_800_000)

    written = _written(log, tmp_path / "amount-probe.log")
    assert "11800000" not in written
    assert "1,18,000" not in written
    assert "len=" not in written, "a length here would leak the magnitude"
    assert "check failed for" in written


# ---------------------------------------------------------------------------
# 5. the audit log is UNCHANGED, and this is the point of the whole task
# ---------------------------------------------------------------------------


def _an_audit_row() -> ActionLog:
    return ActionLog(
        ts=datetime.datetime(2026, 8, 11, 9, 0, tzinfo=datetime.UTC),
        action="post",
        company_key=normalise_company(COMPANY),
        outcome="valid",
        reason="vendor mapped from memory with 7 prior observations",
        run_id="run_" + "0" * 32,
        backend="real",
        operation_id="ad_" + "1" * 32,
        voucher_id="V-1",
        vendor_id=A_VENDOR,
        detail=f"amount_paise=11800000 party={A_VENDOR}",
    )


def test_the_audit_log_still_holds_the_vendor_and_the_amount(
    tmp_path: Path,
) -> None:
    """DELIBERATELY EXEMPT. Do not "fix" this.

    `action_log` is the record of what this software did to a real business's
    statutory books. `docs/DATA_POLICY.md` Table B row 10 says of it "it **is**
    the log", and row 8 says amounts "appear in the local action log by
    design". Redacting it destroys the evidence it exists to hold.
    """
    store = MemoryStore(tmp_path / "memory.db")
    store.record_action(_an_audit_row())

    rows = store.actions(COMPANY)

    assert len(rows) == 1
    assert rows[0].vendor_id == A_VENDOR, "the audit trail names the party"
    assert "11800000" in rows[0].detail, "and states the amount"
    assert redact.HIDDEN not in rows[0].detail
    assert rows[0].operation_id == "ad_" + "1" * 32
    store.close()


def test_the_audit_log_is_untouched_even_after_the_redactor_learns_the_vendor(
    tmp_path: Path,
) -> None:
    """The adversarial version of the row above.

    Teach the process-wide redactor the vendor's name and the amount, THEN
    write an audit row. Redaction is a property of the logging seam, not of the
    string, and this is what proves the two are actually separate rather than
    merely written in different files.
    """
    redact.learn_private(A_VENDOR, "vendor")
    store = MemoryStore(tmp_path / "memory.db")
    store.record_action(_an_audit_row())

    rows = store.actions(COMPANY)

    assert rows[0].vendor_id == A_VENDOR
    assert A_VENDOR in rows[0].detail
    assert "11800000" in rows[0].detail
    store.close()
    redact.default_redactor().forget_learned()


def _imports_redact(tree: ast.Module) -> bool:
    """Read off the AST, never by substring.

    `accountant/memory/store.py` carries a paragraph saying it is deliberately
    exempt, and that paragraph contains the word. A substring scan would read
    the explanation as the violation — the exact failure
    `tests/test_connector.py:539` documents from the socket guard, and from the
    gate-contract test that called a dead gate live for two days.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[-1] == "redact" for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".")
            if "redact" in module or any(a.name == "redact" for a in node.names):
                return True
    return False


def test_nothing_in_the_memory_package_imports_the_redactor() -> None:
    """Structural, so the exemption cannot be undone by accident.

    A behavioural test would pass on the day somebody wired a filter into the
    store and the store happened not to log. The import graph is the evidence.
    """
    package = ROOT / "accountant" / "memory"
    scanned = sorted(package.rglob("*.py"))
    offenders = [
        path.name
        for path in scanned
        if _imports_redact(ast.parse(path.read_text(encoding="utf-8")))
    ]

    assert scanned, "the scan read no modules, so it proves nothing"
    assert offenders == [], (
        "the audit trail is deliberately exempt from redaction; a redact "
        f"import under accountant/memory/ undoes that: {offenders}"
    )


def test_the_import_scan_can_actually_see_an_import() -> None:
    """The control. A scan that matches nothing passes for ever."""
    assert _imports_redact(ast.parse("from accountant import redact\n"))
    assert _imports_redact(ast.parse("import accountant.redact\n"))
    assert not _imports_redact(ast.parse('"""prose about redact."""\n'))


# ---------------------------------------------------------------------------
# 6. the log is still worth reading
# ---------------------------------------------------------------------------


def test_an_ordinary_diagnostic_line_survives_intact(
    tmp_path: Path, clean_redactor: redact.Redactor
) -> None:
    """Over-redaction is the failure mode nobody notices until an incident.

    Every value in this line is one somebody needs at 2 a.m.: which job, what
    happened, which Tally, which operation. `docs/DATA_POLICY.md` §3.3 says
    host and port are a target and not a secret, and row 9 says operation ids
    are "logged everywhere. That is their purpose."
    """
    clean_redactor.learn_secret(A_CONNECTOR_KEY)
    log = _logger(tmp_path, "diagnostic-probe", clean_redactor)
    operation = "ad_" + "a" * 32
    run = "run_" + "b" * 32

    log.info(
        "job=%s outcome=%s tally=http://localhost:9000 operation_id=%s run_id=%s",
        "job-77",
        "TALLY_UNAVAILABLE",
        operation,
        run,
    )

    written = _written(log, tmp_path / "diagnostic-probe.log")
    assert "job=job-77" in written
    assert "outcome=TALLY_UNAVAILABLE" in written
    assert "tally=http://localhost:9000" in written
    assert operation in written, "the join key between the two audit trails"
    assert run in written
    assert redact.HIDDEN not in written, "nothing on this line was sensitive"


def test_an_operation_id_is_not_eaten_by_the_digest_rule() -> None:
    """The property that matters: the join key between the two audit trails
    survives. `docs/DATA_POLICY.md` row 9 — "logged everywhere. That is their
    purpose."

    What protects it is the WORD BOUNDARY, not the threshold: `_` is a word
    character, so `\\b` never fires at the start of the hex run inside
    `ad_ffff…`. This test therefore does NOT pin the number 64 — the test below
    does, and it exists because a mutant proved this one did not.
    """
    an_operation = f"operation_id=ad_{'f' * 32}"
    a_run = f"run_id=run_{'f' * 32}"
    sixty_four = "f" * 64

    assert redact.scrub(an_operation) == an_operation
    assert redact.scrub(a_run) == a_run
    assert sixty_four not in redact.scrub(f"digest {sixty_four}")


def test_a_bare_thirty_two_character_hex_run_is_left_alone() -> None:
    """What the number 64 actually buys. Written 2026-08-11 after a mutant.

    Lowering `_LONG_DIGEST` to 32 killed no test, so the threshold was not
    load-bearing and the comment defending it was wrong. This is the assertion
    that makes it load-bearing, and it states the real reason: a bare 32-hex
    run in this repository is a scrypt salt or a loose uuid4 hex. Neither is a
    credential, both may be needed by a reader, and redacting them catches no
    digest that 64 misses — sha256 is 64 and a scrypt verifier is 128.
    """
    thirty_two = "9f" * 16
    sixty_four = "9f" * 32

    assert len(thirty_two) == 32
    assert redact.scrub(f"tally answered {thirty_two}") == (
        f"tally answered {thirty_two}"
    )
    assert sixty_four not in redact.scrub(f"tally answered {sixty_four}")


def test_scrubbing_twice_changes_nothing_the_second_time() -> None:
    """A record can reach more than one handler, and each carries its own
    filter. A rule that re-chewed its own marker would produce
    `secret=[REDACTED]]` and then something worse."""
    once = redact.scrub("secret=abcdefghijkl vendor=Sharma Traders ₹500")

    assert redact.scrub(once) == once


def test_a_short_learned_value_is_refused_and_the_reason_is_measurable() -> None:
    """`tests/test_connector.py:816` builds a real identity whose secret is the
    single character "s". Learning that by substring would delete the letter s
    from every line this suite writes."""
    own = redact.Redactor()

    assert own.learn_secret("s") is False
    assert own.learn_secret("") is False
    assert own.learned_count == 0
    assert own.scrub("posting to storage") == "posting to storage"
    # The hole that leaves is narrowed by the key-name rule, not left open.
    assert own.scrub("secret=s") == f"secret={redact.HIDDEN}"


def test_a_secret_that_contains_another_secret_is_replaced_whole() -> None:
    """Shortest-first replacement leaves the remaining half of the longer
    credential sitting in the file."""
    own = redact.Redactor()
    own.learn_secret("hovercraft-42")
    own.learn_secret("hovercraft-42-and-then-some")

    cleaned = own.scrub("tried hovercraft-42-and-then-some")

    assert "and-then-some" not in cleaned
    assert cleaned == f"tried {redact.HIDDEN}"


# ---------------------------------------------------------------------------
# 7. the seam is installed, and cannot be forgotten
# ---------------------------------------------------------------------------


def test_build_logger_installs_the_filter_on_the_handler_not_the_logger(
    tmp_path: Path,
) -> None:
    """`Logger.handle` runs its OWN filters and then walks its ancestors'
    HANDLERS. A filter on `accountant.agent` is skipped entirely for a record
    made by `accountant.agent.child`, so the handler is the only seam that
    every line really passes."""
    log = build_logger(tmp_path / "seam.log", name="seam-probe")

    assert log.handlers, "build_logger installed no handler at all"
    for handler in log.handlers:
        assert any(isinstance(f, redact.RedactingFilter) for f in handler.filters)


def test_a_child_logger_is_still_scrubbed_by_the_parents_handler(
    tmp_path: Path,
) -> None:
    """The reason the filter is not on the logger, proved rather than asserted."""
    redact.learn_secret(A_CONNECTOR_KEY)
    path = tmp_path / "child.log"
    build_logger(path, name="child-probe")
    child = logging.getLogger("child-probe.deeper")

    child.info("leaking %s from a child logger", A_CONNECTOR_KEY)

    for handler in logging.getLogger("child-probe").handlers:
        handler.flush()
    assert A_CONNECTOR_KEY not in path.read_text(encoding="utf-8")
    redact.default_redactor().forget_learned()


def test_installing_the_guard_twice_leaves_one_filter() -> None:
    handler = logging.NullHandler()

    redact.guard(handler)
    redact.guard(handler)

    assert sum(isinstance(f, redact.RedactingFilter) for f in handler.filters) == 1


def test_building_a_connector_identity_teaches_the_redactor_its_secret() -> None:
    """The registration seam. There is no way to hold a connector secret
    without constructing one of these, so the redactor learns it whether or not
    anybody remembered to say so."""
    redact.default_redactor().forget_learned()
    ConnectorIdentity(
        connector_id="connector-1",
        secret=A_CONNECTOR_KEY,
        tenant_id="tenant-alpha",
        companies=frozenset({"Demo Co"}),
    )

    assert redact.HIDDEN in redact.scrub(f"cloud rejected {A_CONNECTOR_KEY}")
    redact.default_redactor().forget_learned()


def test_a_blank_secret_is_refused_before_it_can_be_learned() -> None:
    """The refusal runs first, so a blank string never reaches the redactor —
    where it would be a substring of every line ever written."""
    redact.default_redactor().forget_learned()
    # A name the linter does not read as a credential, for a value that is not
    # one. Keeping the literal out of the keyword argument is why this file
    # needs no S106 exemption, unlike the three in pyproject.toml.
    blank = "   "

    with pytest.raises(ValueError, match="needs a secret"):
        ConnectorIdentity(
            connector_id="connector-1",
            secret=blank,
            tenant_id="tenant-alpha",
            companies=frozenset({"Demo Co"}),
        )

    assert redact.default_redactor().learned_count == 0


def test_nothing_in_the_connector_cli_prints_without_going_through_the_seam() -> None:
    """The banner prints every resolved value on purpose, and one day that will
    include something it should not. Read off the AST rather than by substring:
    this repository has twice been bitten by a scan that matched a word inside
    the comment explaining that the word is not used."""
    tree = ast.parse(CLI.read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
        and not _inside_say(tree, node)
    ]

    assert offenders == [], (
        "accountant/agent/cli.py must speak through _say, which scrubs. Bare "
        f"print( at line(s) {offenders}"
    )


def _inside_say(tree: ast.Module, target: ast.Call) -> bool:
    """Is this `print(` the one inside `_say`? Identity, not line number."""
    return any(
        isinstance(node, ast.FunctionDef)
        and node.name == "_say"
        and any(inner is target for inner in ast.walk(node))
        for node in ast.walk(tree)
    )


def test_the_print_scan_can_actually_fail() -> None:
    """The control for the scan above. A structural test that matches nothing
    passes forever."""
    tree = ast.parse("def main():\n    print('hello')\n")
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]

    assert len(found) == 1, "the print scan cannot see a bare print at all"


def test_every_action_log_field_survives_a_round_trip_unredacted(
    tmp_path: Path,
) -> None:
    """The widest version of the exemption: no field of the audit row is
    touched, not merely the two this task names."""
    store = MemoryStore(tmp_path / "memory.db")
    row = _an_audit_row()
    store.record_action(row)

    back = store.actions(COMPANY)[0]

    assert back.reason == row.reason
    assert back.detail == row.detail
    assert back.vendor_id == row.vendor_id
    assert back.voucher_id == row.voucher_id
    assert back.actor == NOT_RECORDED
    assert "REDACTED" not in "".join(
        (back.reason, back.detail, back.vendor_id, back.voucher_id)
    )
    store.close()


def test_the_pattern_table_is_not_empty_and_every_rule_compiles() -> None:
    """A rule table that silently emptied would make every assertion above pass
    for the wrong reason on the day somebody edited it."""
    table = redact.rules()

    assert len(table) >= 8, f"the rule table lost rows: {len(table)}"
    for pattern, _replacement in table:
        assert isinstance(pattern, re.Pattern)

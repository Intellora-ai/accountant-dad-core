"""Keep credentials, session tokens, party names and amounts out of the LOG.

WHERE THIS LIVES, AND WHY IT IS NOT UNDER `accountant/agent/`
------------------------------------------------------------
Top level, beside `schema.py`, importing nothing from `accountant` and nothing
outside the standard library. Two structural reasons, not taste:

  * **every layer may eventually need it.** `accountant/agent/connector.py`
    builds the only logger the product ships today, but `accountant/tallyio/`
    may not import the product layer (correction C3, proved by
    `tests/test_reverse_all_cli.py::test_only_the_command_imports_above_the_
    connector_boundary`) and `accountant/checks.py` must not import the
    connector. A leaf module with no `accountant` import is the only shape all
    three of them can depend on without anybody's layering test failing.
  * **location is a claim.** A redactor filed under `accountant/agent/` would
    say, by where it sits, that log hygiene is the connector's private
    business. It is not. It is a property of the product.

WHAT IS DELIBERATELY **NOT** REDACTED — read this before "fixing" it
--------------------------------------------------------------------
**The audit trail (`action_log` in `accountant/memory/store.py`) is exempt, on
purpose, by owner decision.** It keeps the vendor and it keeps the amount.

That table is the record of what this software did to a real business's
statutory books. `docs/DATA_POLICY.md` Table B row 10 says of it: *"it **is**
the log"*, and row 8 says amounts *"appear in the local action log by design"*.
Redacting it would destroy the evidence it exists to hold — an audit row that
cannot say which party and how much is not an audit row, it is a timestamp.

Nothing in this module reaches `action_log`: the seam here is a
`logging.Filter`, and `record_action` is a SQL INSERT that never becomes a
`logging.LogRecord`. `tests/test_redaction.py` asserts that separation rather
than trusting it.

TWO MECHANISMS, BECAUSE ONE IS NOT ENOUGH
-----------------------------------------
1. **By known value.** `learn_secret` is handed the actual credential, and any
   line containing it is scrubbed wherever it appears. This is the only thing
   that catches a secret that leaked into a message BODY, where it carries no
   key name with it and no pattern can recognise it as anything but a word.
2. **By pattern.** `secret=...`, `Authorization: Bearer ...`, a 64+ character
   hex digest, `https://user:pass@host`, `₹1,18,000`, `vendor=...`. This is the
   only thing that catches a value nobody registered — a password typed into
   the wrong field, an exception from a library we do not own.

Either alone leaves a hole the other closes.

THE SEAM
--------
`RedactingFilter` is installed **once, on the handler**, by `build_logger`. Not
on the logger: `Logger.handle` applies only its OWN filters and then walks its
ancestors' HANDLERS, so a filter on `accountant.agent` is skipped entirely for
a record made by `accountant.agent.something`. The handler is the thing that
writes bytes, so the handler is where the guard belongs.

Call sites are not asked to remember anything. This repository's rule is that
a check every caller must remember is a check some caller will forget, and a
redaction call at 200 log statements is 200 chances to forget.

HOW MUCH SHAPE SURVIVES, AND WHY IT DIFFERS BY CLASS
----------------------------------------------------
A log nobody can diagnose from has been destroyed, not protected. So the
replacement keeps shape where shape helps and keeps nothing where it does not.
The line is drawn from `docs/DATA_POLICY.md`, not from feel:

    credentials        `[REDACTED]`. No length, no prefix, no last-4.
    (connector secret,  Row 1 forbids logging a password "not a prefix"; row 3
     password, hash,    says connector keys are never logged "in any form". A
     token, fingerprint) length narrows a brute force and tells a diagnostician
                        nothing they cannot get from "a credential was here".

    party names        `[REDACTED vendor len=14]`. Row 6 permits the LENGTH of
    (vendor, party,     entry text where the text itself is forbidden. Length
     supplier, payee,   is what distinguishes "the field was empty" from "the
     subject)           field was truncated", which is the actual bug people
                        chase, and a name's length is not the private part.

    amounts            `[REDACTED amount]`. NO length, and this is the one that
                       looks inconsistent until you say it out loud: the digit
                       count IS the magnitude. `len=9` on a rupee figure leaks
                       exactly the fact being hidden.

WHAT IS DELIBERATELY LEFT ALONE, SO THE LOG STAYS USABLE
--------------------------------------------------------
  * **Operation ids and run ids.** `ad_<32 hex>` and `run_<32 hex>`
    (`accountant/tallyio/client.py:34`, `accountant/tallyio/factory.py:107`).
    `docs/DATA_POLICY.md` row 9: *"logged everywhere. That is their purpose."*
    They are the join key between the two audit trails.

    What protects them is the WORD BOUNDARY in `_LONG_DIGEST`, not the number
    64. `_` is a word character, so `\b` never fires at the start of the hex
    run inside `ad_ffff…` and the rule cannot begin there at any threshold.
    That correction was forced by a surviving mutant on 2026-08-11 — see the
    note on `_LONG_DIGEST` for what the number is really for.
  * **A bare 32-character hex run**, which in this repository means a scrypt
    salt or a loose uuid4 hex — neither is a credential, and a reader may need
    either. `salt=...` beside its key name is still redacted, and a salt is
    public by construction anyway: it is not what protects the password.
  * **Tally host and port.** `docs/DATA_POLICY.md` §3.3: they are a target, not
    a secret, and loopback binding is the control. A log row that cannot say
    which Tally it came from cannot be evidence about any of them.
  * **Ledger account names** (`debit_account`, `credit_account`). Which leg the
    software chose is the single most useful fact when diagnosing a wrong
    posting, and row 8 permits field names and provenance. A ledger that is
    also a party name is covered by registering it with `learn_private`.

ADDING A NEW SECRET: see `docs/REDACTION.md`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

#: What a credential becomes. No length, no prefix — see the table above.
HIDDEN = "[REDACTED]"

#: Values shorter than this are NOT learned for substring scrubbing.
#:
#: Measured, not guessed: `tests/test_connector.py:816` builds a real
#: `ConnectorIdentity` whose secret is the single character `"s"`. Scrubbing
#: that by substring would delete the letter s from every line this suite
#: writes, and a log that has lost a letter is worse than a log that named a
#: one-character credential. Below the floor the key-name pattern still fires,
#: so `secret=s` is caught where it is written next to its own name; what is
#: given up is only the bare-word case. A credential this short is not made
#: safe by redaction — it is made safe by not being issued.
MIN_LEARNABLE = 8

#: A quoted string, or an unquoted run up to the next delimiter. Quoted first,
#: so `vendor='Sharma Traders'` is taken whole rather than stopping at the
#: space. The unquoted branch CANNOT cross a space — see `docs/REDACTION.md`
#: §2 for the hole that leaves and the learned layer that closes it.
_VALUE = r"\"[^\"]*\"|'[^']*'|[^\s,;)\]}]+"

#: The gap between a key and its value. The optional quote is what makes a
#: dict repr work: `{'secret': 'x'}` puts the key's own closing quote between
#: the word and the colon, and without this the commonest way a credential is
#: ever printed — somebody logging a payload — matched nothing at all.
_SEP = r"[\"']?\s*[=:]\s*"

#: Key names whose value is a credential wherever it is written.
_CREDENTIAL_KEYS = (
    "secret",
    "secrets",
    "password",
    "passwd",
    "pwd",
    "password_hash",
    "token",
    "access_token",
    "session_token",
    "api_key",
    "apikey",
    "api-key",
    "credential",
    "credentials",
    "authorization",
    "salt",
    "fingerprint",
    "token_fingerprint",
)

#: Key names whose value names a party in somebody's books.
_PARTY_KEYS = (
    "vendor",
    "vendor_id",
    "party",
    "supplier",
    "payee",
    "subject",
    "raw_subject",
    "ledger",
)

#: Key names whose value is money.
_MONEY_KEYS = ("amount", "amount_paise", "gst_paise")


def _alternation(words: tuple[str, ...]) -> str:
    """Longest first, so `password_hash` is not matched as `password`."""
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


_KEYED_CREDENTIAL = re.compile(
    rf"\b({_alternation(_CREDENTIAL_KEYS)})\b({_SEP})({_VALUE})",
    re.IGNORECASE,
)
_KEYED_PARTY = re.compile(
    rf"\b({_alternation(_PARTY_KEYS)})\b({_SEP})({_VALUE})",
    re.IGNORECASE,
)
_KEYED_MONEY = re.compile(
    rf"\b({_alternation(_MONEY_KEYS)})\b({_SEP})(-?\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)
#: `https://user:pass@host`. The whole userinfo goes: a username in a URL is
#: half a credential and it is never needed to know which host was called.
_URL_USERINFO = re.compile(r"://[^/\s@]+@")
#: A bare digest with nothing beside it to name it. sha256 is 64 hex and a
#: scrypt verifier at dklen=64 is 128.
#:
#: WHY 64 AND NOT 32, CORRECTED 2026-08-11. The first version of this comment
#: claimed 32 "would have eaten every operation id". A mutant that lowered the
#: threshold to 32 SURVIVED the suite, which is how the claim was found to be
#: false: `_` is a word character, so `\b` never fires at the start of the hex
#: run in `ad_ffff…`, and the boundary anchors protect the ids at either
#: number. 64 is kept for the reason that survives measurement — it is the
#: length of the shortest digest this rule means to catch. Below it the rule
#: stops describing digests and starts describing "any long hex run", which
#: here is a scrypt salt or a loose uuid4 hex, and catches no digest that 64
#: misses. `tests/test_redaction.py::test_a_bare_thirty_two_character_hex_run_
#: is_left_alone` is the assertion that now makes the number load-bearing.
_LONG_DIGEST = re.compile(r"\b[0-9a-fA-F]{64,}\b")
_BEARER = re.compile(r"\b(Bearer|Basic)\s+\S+", re.IGNORECASE)
#: `os.environ` printed whole. `repr(os.environ)` is `environ({...})`.
_ENVIRON_DUMP = re.compile(r"environ\(\{.*?\}\)", re.DOTALL)
#: A rupee figure written as a person writes it.
_CURRENCY = re.compile(r"(?:₹|\bRs\.?\s*|\bINR\s+)\s*\d[\d,]*(?:\.\d+)?", re.IGNORECASE)


def _already_hidden(text: str) -> bool:
    """`scrub` must be safe to run twice. A record can pass more than one
    handler, and each handler carries its own filter."""
    return text.lstrip("\"'").startswith(HIDDEN[:9])


def _hide_keyed_credential(match: re.Match[str]) -> str:
    if _already_hidden(match.group(3)):
        return match.group(0)
    return f"{match.group(1)}{match.group(2)}{HIDDEN}"


def _hide_keyed_party(match: re.Match[str]) -> str:
    value = match.group(3)
    if _already_hidden(value):
        return match.group(0)
    bare = value.strip("\"'")
    return f"{match.group(1)}{match.group(2)}{shaped(bare, match.group(1).lower())}"


def _hide_keyed_money(match: re.Match[str]) -> str:
    return f"{match.group(1)}{match.group(2)}[REDACTED amount]"


def shaped(value: str, label: str) -> str:
    """A party name, replaced by its label and its length. See the table above
    for why length is kept here and refused for a money figure."""
    return f"[REDACTED {label} len={len(value)}]"


#: `re.sub` takes either. Named so the table below type-checks as one thing.
_Replacement = str | Callable[[re.Match[str]], str]

#: Applied in this order, AFTER every learned value (see `Redactor.scrub` for
#: why that way round). Ordered so the narrow rules run first and the broad
#: ones cannot eat their input.
_PATTERNS: tuple[tuple[re.Pattern[str], _Replacement], ...] = (
    (_ENVIRON_DUMP, "environ([REDACTED environment])"),
    (_URL_USERINFO, f"://{HIDDEN}@"),
    (_BEARER, rf"\1 {HIDDEN}"),
    (_KEYED_CREDENTIAL, _hide_keyed_credential),
    (_KEYED_MONEY, _hide_keyed_money),
    (_KEYED_PARTY, _hide_keyed_party),
    (_LONG_DIGEST, HIDDEN),
    (_CURRENCY, "[REDACTED amount]"),
)


def rules() -> tuple[tuple[re.Pattern[str], _Replacement], ...]:
    """The pattern table, exposed so a test can prove it did not silently empty.

    A rule table that lost its rows would make every "the secret is gone"
    assertion in `tests/test_redaction.py` pass for the wrong reason — the
    secret would be gone from the assertion, not from the log.
    """
    return _PATTERNS


class Redactor:
    """The known values, and the one method that applies everything.

    Learned values are held as plain strings. That is not a weakening: the
    process already holds the connector secret in `ConnectorIdentity`, and a
    redactor that only held a hash of it could not find it inside a sentence.
    """

    def __init__(self) -> None:
        self._replacements: dict[str, str] = {}
        self._ordered: tuple[tuple[str, str], ...] = ()

    # -- learning -----------------------------------------------------------

    def learn_secret(self, value: str) -> bool:
        """Register a credential. Returns whether it was taken.

        False means the value was blank or shorter than `MIN_LEARNABLE`, and
        the reason is on that constant. It is returned rather than raised
        because the caller is `ConnectorIdentity.__post_init__`, and a
        connector that refused to start because its credential was short would
        be a new refusal smuggled in by a logging change.
        """
        return self._learn(value, HIDDEN)

    def learn_private(self, value: str, label: str = "value") -> bool:
        """Register a party name, an amount as written, or any other value that
        is private but not a credential. Its LENGTH survives — see the table at
        the top for why that differs from `learn_secret`."""
        return self._learn(value, shaped(value, label))

    def _learn(self, value: str, replacement: str) -> bool:
        if not value or len(value) < MIN_LEARNABLE:
            return False
        if self._replacements.get(value) == replacement:
            return True
        self._replacements[value] = replacement
        # Longest first: a secret that contains another secret must not be
        # half-replaced, which would leave the remaining half in the file.
        self._ordered = tuple(
            sorted(self._replacements.items(), key=lambda kv: len(kv[0]), reverse=True)
        )
        return True

    def forget_learned(self) -> None:
        """Drop every learned value. For tests that must not leak into each
        other; nothing in the shipped program calls it."""
        self._replacements = {}
        self._ordered = ()

    @property
    def learned_count(self) -> int:
        return len(self._replacements)

    # -- applying -----------------------------------------------------------

    def scrub(self, text: str) -> str:
        """Learned values FIRST, then patterns. The order was measured.

        Patterns-first was written first and was wrong. `vendor=Sharma Traders`
        unquoted: the key-name rule stops at the space, emits
        `vendor=[REDACTED vendor len=6] Traders`, and the learned pass then
        looks for "Sharma Traders" in a string that no longer contains it. The
        surname survived a redactor that had been TOLD the name.

        Learned-first cannot lose that way: the whole value goes, and the
        pattern pass afterwards sees a marker and leaves it alone — that is
        what `_already_hidden` is for, and it is also what makes `scrub`
        idempotent when a record reaches two handlers.
        """
        if not text:
            return text
        out = text
        for value, marker in self._ordered:
            if value in out:
                out = out.replace(value, marker)
        for pattern, replacement in _PATTERNS:
            out = pattern.sub(replacement, out)
        return out


# ---------------------------------------------------------------------------
# The process-wide instance
# ---------------------------------------------------------------------------

_DEFAULT = Redactor()


def default_redactor() -> Redactor:
    """The one every handler shares.

    Process-wide on purpose. The alternative is passing a redactor from wherever
    the secret is read down to wherever a log line is written, through code that
    has no other reason to know either exists — and every link in that chain is
    somewhere the argument can be dropped.
    """
    return _DEFAULT


def learn_secret(value: str) -> bool:
    """Teach the process-wide redactor a credential."""
    return _DEFAULT.learn_secret(value)


def learn_private(value: str, label: str = "value") -> bool:
    """Teach the process-wide redactor a private, non-credential value."""
    return _DEFAULT.learn_private(value, label)


def scrub(text: str) -> str:
    """Redact one string with the process-wide redactor.

    This is for text on its way to STDOUT. Log lines do not call it: they go
    through `RedactingFilter`, which is installed once and cannot be forgotten.
    """
    return _DEFAULT.scrub(text)


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


class RedactingFilter(logging.Filter):
    """Scrubs a record on its way to a handler. Never drops one.

    Returning True always is deliberate. A filter that discarded a record
    containing a secret would turn "we leaked a credential" into "the log is
    silent about the failure", and a silent log is the condition this whole
    file exists to avoid.

    THE MESSAGE IS FLATTENED HERE, AND THAT IS THE POINT
    ----------------------------------------------------
    `log.info("job=%s secret=%s", job, secret)` keeps the secret in
    `record.args`, and the `%` substitution happens in the FORMATTER, which
    runs after every filter. Scrubbing `record.msg` alone would rewrite the
    format string and leave the credential in the arguments, untouched, to be
    interpolated straight into the file. So the message is formatted here,
    scrubbed, and the arguments cleared.

    `exc_text` is filled in here for the same reason: `Formatter.format`
    renders the traceback itself if nobody else has, and a traceback carries
    `str(exception)` — which is exactly where a credential lands when a library
    puts a request body into its error message.
    """

    def __init__(self, redactor: Redactor | None = None) -> None:
        super().__init__()
        self._redactor = default_redactor() if redactor is None else redactor

    @property
    def redactor(self) -> Redactor:
        return self._redactor

    def filter(self, record: logging.LogRecord) -> bool:
        redactor = self._redactor
        record.msg = redactor.scrub(record.getMessage())
        record.args = None
        if record.exc_info is not None and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redactor.scrub(record.exc_text)
        if record.stack_info:
            record.stack_info = redactor.scrub(record.stack_info)
        return True


def guard(handler: logging.Handler, redactor: Redactor | None = None) -> None:
    """Install the filter on a handler, at most once.

    Idempotent because `build_logger` may be called again on the same name, and
    two copies of the filter would format the message twice — harmless today,
    and exactly the sort of harmless that stops being harmless.
    """
    for existing in handler.filters:
        if isinstance(existing, RedactingFilter):
            return
    handler.addFilter(RedactingFilter(redactor))

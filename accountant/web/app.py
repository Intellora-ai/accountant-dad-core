"""Child 14 — the web app, Slice 1.

RUNS AGAINST REAL TALLY. THERE IS NO FAKE PATH HERE.
----------------------------------------------------
Owner decision, 2026-08-09: `RealTally` is the only runtime backend. This module
imports NEITHER implementation. It asks `accountant.tallyio.factory` for a
`TallyClient` and depends on that interface alone, so "which Tally are we
talking to" has exactly one answer and it is enforceable in one place.

Until 2026-08-09 this file imported `FakeTally` and built its own client, and
the docstring said "NOTHING here touches real Tally". Both are now false and
both were a hazard: an app that can name a fake can post into one and report it
as evidence.

NOTHING CONNECTS AT IMPORT TIME.
--------------------------------
There is no live state until `connect()` fills it from the factory, or
`configure()` injects one directly, which is how tests supply a
double without this module ever naming one. Connecting at import would mean the
module could not be imported at all while Tally was down — including by the
tests that are supposed to prove it fails closed.

Stdlib only. No framework, no build step, no install.
"""

from __future__ import annotations

import datetime
import html
import json
import os
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer

from accountant import pipeline
from accountant import questions as Q
from accountant.extract.adapter import TypedTextExtractor
from accountant.memory.bootstrap import bootstrap
from accountant.memory.company import CompanyMemory
from accountant.memory.store import BootstrapReport, BootstrapStatus, MemoryStore
from accountant.schema import ActionLog, Outcome
from accountant.tallyio.client import TallyClient, operation_id_in
from accountant.tallyio.factory import (
    BackendIdentity,
    LicenceMode,
    RealTallyRequired,
    real_tally,
)
from accountant.tallyio.real import RecordedBackups, TallyConfig

COMPANY = "Accountant Dad Final"

DRAFTS: dict[str, pipeline.Draft] = {}

# How many log rows the page shows. The log itself is unbounded and append-only;
# this is a rendering choice and nothing more. `EVENTS`, the forty-row in-memory
# list this replaced, was the opposite: the cap WAS the retention policy, so row
# 41 stopped existing anywhere.
SHOWN = 40

# outcome -> the word the log calls it. A total map rather than an if/elif/else
# chain, and the difference matters: NOT_VALID is currently UNREACHABLE from a
# typed entry, because the only unanswerable check is `amount_is_integer_paise`
# and the extractor cannot produce a non-integer. Written as branches, that arm
# is dead code no test can enter; written as data, it is one line that executes
# on every path, and `test_every_outcome_has_a_log_word` proves the map is
# total over the enum - a stronger claim than a branch test could make.
ACTION_FOR: dict[Outcome, str] = {
    Outcome.VALID: "posted",
    Outcome.UNCLEAR: "asked",
    Outcome.NOT_VALID: "blocked",
}


@dataclass(frozen=True)
class Runtime:
    """Everything the request handlers need, resolved once and held together.

    One object rather than four mutable globals, so a half-connected state
    cannot exist: either every field is present or `runtime()` refuses. A client
    without its memory, or memory without its identity, would be exactly the
    ambiguity principle 9 forbids.
    """

    client: TallyClient
    identity: BackendIdentity
    memory: CompanyMemory
    store: MemoryStore


_runtime_state: Runtime | None = None


# One spelling of the refusal, so the handler can recognise its own and the
# tests can assert on something that cannot drift out of sync.
REFUSAL = "REAL TALLY REQUIRED"


def runtime() -> Runtime:
    """The live runtime, or a refusal. Never a silently absent one."""
    if _runtime_state is None:
        raise RuntimeError(
            f"{REFUSAL}: no operation performed. "
            "connect() or configure() has not been called, so no company has "
            "been read and nothing may be proposed or posted."
        )
    return _runtime_state


def health() -> dict[str, object]:
    """Measured readiness. Every value read from the live runtime.

    This returned a hardcoded `{"ok": true}` until 2026-08-09 and therefore
    kept reporting healthy after a disconnect or a failed bootstrap. A
    readiness endpoint that cannot say "not ready" is a constant with a
    misleading name, and it is believed precisely because it looks like a
    measurement.

    `ready` is the bootstrap's own gate, so an EMPTY_SOURCE or
    EMPTY_VENDOR_INDEX company reports not-ready here for the same reason it
    refuses to propose: readiness means safe to receive work.
    """
    if _runtime_state is None:
        return {
            "ready": False,
            "bootstrap_status": "not_connected",
            "failure_code": "NO_RUNTIME",
            "backend": None,
            "backend_state": BACKEND_UNAVAILABLE,
            "licence_mode": None,
            "company": COMPANY,
            "detail": (
                f"{REFUSAL}: no operation performed. "
                "connect() or configure() has not been called."
            ),
        }

    live = _runtime_state
    report = live.memory.report
    counts = report.counts
    return {
        "ready": report.ready,
        # The SAME function the page uses. Two answers to "which Tally are we
        # on" is how the screen and the monitoring end up disagreeing, and the
        # one nobody is watching is always the one that stays wrong.
        "backend_state": backend_state(),
        "bootstrap_status": report.status.value,
        "failure_code": None if report.ready else report.status.value.upper(),
        "detail": report.detail,
        "company_exists": live.identity.company_exists,
        "accounts_read": counts.accounts,
        "vouchers_read": counts.vouchers,
        "vendor_mappings_derived": counts.mappings,
        "index_entries": counts.mappings,
        "conflicts": counts.conflicts,
        "unusable_rows": counts.unusable,
        "last_bootstrap": report.bootstrapped_at,
        **live.identity.as_metrics(),
    }


# ---- "have we actually read this company's books?" --------------------------
#
# G6. A failure to read the books has to be visible to the PERSON, not only to
# whoever reads /health. Before this, a company whose history had not been read
# served either a normal-looking entry form — so the app looked fine and simply
# never suggested anything — or a stack trace out of `pipeline.build_draft`.
# Neither says the one thing that matters: we have not read your books yet.
#
# `report.detail` is never shown. It is written for us; it names steps and says
# "6 mapping(s)". The person gets sentences instead, and the five are worded so
# a reader can tell which one they are looking at without knowing any of this.
#
# Every message ends its first sentence with CANNOT_HELP. That is deliberate:
# it gives the tests one stable thing to assert on, so "a banner appeared" can
# be checked without matching prose that is meant to be edited freely.

CANNOT_HELP = "cannot suggest anything"

BOOTSTRAP_TROUBLE: dict[BootstrapStatus, str] = {
    BootstrapStatus.NEVER_RUN: (
        f"<b>We have not read your Tally books yet, so we {CANNOT_HELP}.</b> "
        "Nothing has been read out of Tally for this company. Until it is, "
        "every entry you type will come back as a question."
    ),
    BootstrapStatus.INCOMPLETE: (
        f"<b>We started reading your Tally books but did not get to the end, "
        f"so we {CANNOT_HELP}.</b> Something went wrong part way through. "
        "Check the company is open in Tally, then start this app again."
    ),
    BootstrapStatus.EMPTY_SOURCE: (
        f"<b>We read your Tally books and there are no past entries in them "
        f"at all, so we {CANNOT_HELP}.</b> There is nothing for us to learn "
        "from yet. We will ask you about every entry until you have built up "
        "some history."
    ),
    BootstrapStatus.COMPANY_KEY_COLLISION: (
        f"<b>Two of your companies have names that are too alike, so we "
        f"{CANNOT_HELP}.</b> Tally has two companies open whose names only "
        "differ by brackets, dots, dashes or spare spaces, and we cannot tell "
        "their books apart. Nothing has been read and nothing has been "
        "changed. Give one of them a clearly different name in Tally, then "
        "start this app again."
    ),
    BootstrapStatus.EMPTY_VENDOR_INDEX: (
        f"<b>We read your Tally books, but not one past entry says who you "
        f"paid, so we {CANNOT_HELP}.</b> With no name on a past entry there is "
        "nothing for us to learn from. We will ask you about every entry."
    ),
}


def bootstrap_banner(report: BootstrapReport) -> str:
    """The warning box, or "" when the books were read AND were useful.

    Returning "" for READY is the whole point. A banner that is always there
    is not a banner, it is decoration, and the test that proves a READY
    company shows nothing is what keeps it honest.
    """
    trouble = BOOTSTRAP_TROUBLE.get(report.status, "")
    return f"<div class=warn>{trouble}</div>" if trouble else ""


def connected() -> bool:
    """Whether a backend is installed. For /health, which must not raise."""
    return _runtime_state is not None


def configure(
    client: TallyClient,
    identity: BackendIdentity,
    *,
    store: MemoryStore | None = None,
) -> Runtime:
    """Install an already-built client and bootstrap this company's memory.

    The injection seam. Tests hand a double in here, which is why this module
    never needs to import one — principle 6 is about what the SHIPPED code can
    reach, not about forbidding doubles in tests.

    Memory is bootstrapped ONCE, from this company's own Tally, and reused. It
    used to be `MemoryIndex.from_vouchers(history)` rebuilt inside every request
    handler: no company key, so nothing stopped one company's history answering
    another's question; no persistence, so an answer the person gave was
    forgotten by the next request; and no bootstrap record, so "we have not read
    your books yet" was indistinguishable from "your books say nothing about
    this vendor". The first asks a question; the second must not.

    W5 / D5, FIXED 2026-08-09. THE IDENTITY MUST NAME THE CLIENT IT IS FOR.

    `backend_state()` and the page read `identity.backend`. Every ActionLog row
    writes `type(client).__name__`. Nothing compared them, so a runtime built
    from a fake client and a real-sounding identity told the person on screen
    *"This is your real Tally"* while every row in their own audit trail said
    `RecordingTally`. Both cannot be true, and the one the person reads is the
    wrong one — which is the exact failure mode the three evidence classes
    exist to prevent, arriving through the injection seam instead of through a
    document.

    Compared by class name rather than by `isinstance`, deliberately. The
    question is not "does this object behave like a real Tally" — a double
    behaves like one, that is what makes it useful. The question is "does the
    word we are about to print match the object we are about to use", and a
    string comparison is the only thing that answers it.

    A wrapper is therefore its own backend: `RecordingTally` around a
    `FakeTally` must declare `RecordingTally`, because that is what the log
    will say.
    """
    global _runtime_state
    actual = type(client).__name__
    if identity.backend != actual:
        raise ValueError(
            f"{REFUSAL}: no operation performed. The identity says the backend "
            f"is {identity.backend!r} but the client is a {actual}. The page "
            f"and the action log would name different backends, and nothing "
            f"downstream could tell which one was written to."
        )

    owned = store if store is not None else MemoryStore(":memory:")
    _runtime_state = Runtime(
        client=client,
        identity=identity,
        memory=bootstrap(client, identity.company, owned),
        store=owned,
    )
    return _runtime_state


def connect(
    config: TallyConfig,
    company: str = COMPANY,
    *,
    backups: RecordedBackups | None = None,
    store: MemoryStore | None = None,
) -> Runtime:
    """The real path: ask the factory for a real client, then bootstrap.

    Raises `RealTallyRequired` when Tally is unreachable, unlicensed or the
    company identity is uncertain. It never falls back — see the factory's
    docstring for why "unreachable" and "empty" must not collapse into one
    another.
    """
    client, identity = real_tally(config, company, backups=backups)
    return configure(client, identity, store=store)


def disconnect() -> None:
    """Drop the runtime. Tests use this to prove the app fails closed."""
    global _runtime_state
    _runtime_state = None


def record(draft: pipeline.Draft, action: str) -> None:
    """A draft-shaped decision, through the same function `pipeline.run` uses.

    Not a second implementation of logging. The web app reaches its decisions
    across several HTTP requests rather than in one `run` call, so it cannot
    use `run`, but it must produce identical rows - same fields, same reason,
    same backend. Calling the pipeline's own recorder is what guarantees that
    rather than promising it.
    """
    live = runtime()
    pipeline.record_decision(
        live.store, draft, live.memory, live.client, action, live.identity.run_id
    )


def note(action: str, outcome: str, reason: str, **fields: str) -> None:
    """An action of the app's own that is not a decision about a draft.

    Reversal, handover and retype happen at the screen, not in the pipeline, so
    the pipeline has nothing to say about them. They are still things that were
    done to somebody's books or on their behalf, and `reason` is required here
    for the same reason it is required everywhere else.
    """
    live = runtime()
    live.store.record_action(
        ActionLog(
            ts=datetime.datetime.now(datetime.UTC),
            action=action,
            company_key=live.memory.identity.key,
            outcome=outcome,
            reason=reason,
            run_id=live.identity.run_id,
            backend=type(live.client).__name__,
            **fields,
        )
    )


# ---- rendering --------------------------------------------------------------

CSS = """
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{font:15px/1.55 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
max-width:880px;margin:0 auto;padding:24px 20px 64px}
h1{font-size:20px;margin:0 0 2px}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.06em;opacity:.6;
margin:28px 0 10px;font-weight:600}
.sub{opacity:.6;font-size:13px;margin:0 0 18px}
.warn{border:1px solid #b45309;background:#b4530915;padding:10px 12px;
border-radius:8px;font-size:13px;margin:0 0 22px}
form.entry{display:flex;gap:8px;margin:0 0 6px}
input[type=text]{flex:1;padding:11px 13px;font:inherit;border-radius:8px;
border:1px solid #8884}
button{padding:11px 16px;font:inherit;font-weight:600;border-radius:8px;
border:1px solid #8884;cursor:pointer;background:#8881}
button.primary{background:#2563eb;color:#fff;border-color:#2563eb}
.hint{font-size:12px;opacity:.55;margin:0 0 8px}
.card{border:1px solid #8883;border-radius:10px;padding:14px 16px;margin:0 0 12px}
.valid{border-left:4px solid #16a34a}
.unclear{border-left:4px solid #d97706}
.notvalid{border-left:4px solid #dc2626}
.badge{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.06em;
padding:2px 8px;border-radius:999px;text-transform:uppercase}
.b-valid{background:#16a34a22;color:#16a34a}
.b-unclear{background:#d9770622;color:#d97706}
.b-notvalid{background:#dc262622;color:#dc2626}
table{border-collapse:collapse;width:100%;font-size:13px}
td,th{text-align:left;padding:6px 10px 6px 0;border-bottom:1px solid #8882}
th{opacity:.55;font-weight:600;font-size:11px;text-transform:uppercase}
.num{text-align:right;font-variant-numeric:tabular-nums}
code{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;background:#8881;
padding:1px 5px;border-radius:4px}
.reason{font-size:13px;opacity:.85;margin:6px 0 0}
.opts{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 0}
.ask{font-size:17px;font-weight:600;margin:10px 0 0;line-height:1.4}
.ev{font-size:12.5px;padding:5px 0;border-bottom:1px solid #8882}
.muted{opacity:.55}
"""


def rupees(paise: int) -> str:
    """Integer paise as rupees. A3, FIXED 2026-08-09.

    Was `f"{paise // 100:,}.{paise % 100:02d}"`. Python floors division toward
    minus infinity, so `-420050 // 100` is -4201 and `-420050 % 100` is 50: the
    page printed -4,201.50 for a balance of -4,200.50. Every negative figure in
    the trial balance was one rupee further from zero, and the paise did not
    move with it, which is what makes it read like a rounding style instead of
    an error. `tallyio.rupees_from_paise` has always split the sign off first;
    this now does the same.

    And it RAISED on a non-int, through the `:02d` format code, with the
    message "Unknown format code 'd' for object of type 'float'".
    `amount_is_integer_paise` is the only unanswerable check in the codebase,
    so a float amount is the clearest route to NOT_VALID there is - and it was
    the one draft the screen could not draw. The outcome that means "nothing
    was posted" was the outcome the person could not be shown. The refusal is
    now explicit and says what is wrong.
    """
    if isinstance(paise, bool):  # bool is an int; render it as one
        paise = int(paise)
    # Annotated `int`, so pyright calls this unnecessary. The annotation is not
    # enforced at runtime and the whole point of this branch is the value that
    # arrives anyway - which is how the NOT_VALID screen came to be the one
    # screen the app could not draw.
    if not isinstance(paise, int):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError(
            f"amounts are integer paise, never {type(paise).__name__}: {paise!r}"
        )
    sign = "-" if paise < 0 else ""
    whole, fraction = divmod(abs(paise), 100)
    return f"{sign}{whole:,}.{fraction:02d}"


def money(paise: object) -> str:
    """An amount for the SCREEN. Never raises, and never invents a rendering.

    A3's other half. `rupees` is strict on purpose - it is the money formatter,
    and a formatter that quietly renders a float as rupees is how a lost paise
    stops being visible. But the page is not allowed to fail: a non-integer
    amount is exactly what makes an entry NOT_VALID through
    `amount_is_integer_paise`, the only unanswerable check in the codebase, so
    the one draft the person MOST needs to see was the one that raised while
    being drawn. They got a traceback instead of "nothing was posted, and here
    is why".

    So the strictness stays in `rupees` and the page degrades: it prints the
    value as it actually is, marked as not an amount, which is the true
    statement and the one that explains the refusal on the same screen.
    """
    try:
        return f"₹{rupees(paise)}"  # type: ignore[arg-type]
    except TypeError:
        return f"{esc(paise)} (not an amount)"


def esc(s: object) -> str:
    return html.escape(str(s))


# ---- which Tally are we on, and is it safe to work? -------------------------
#
# Until 2026-08-09 every page carried a hardcoded "Demo mode. This is talking to
# a fake Tally running in memory... Nothing here touches any real books." True
# while the app built its own FakeTally; a LIE from the moment P3.1 wired it to
# somebody's real statutory books. Both directions of that lie are dangerous and
# the false-reassurance direction is worse: a person told nothing is real will
# type freely into books that are.
#
# So the notice is MEASURED. There are FOUR states and a reader has to be able
# to tell which one they are looking at:
#
#   real-ok         a real Tally whose licence was measured as fully licensed.
#   unavailable     nothing is connected. Nothing works, and the page says why.
#   real-practice   a REAL TallyPrime in Educational mode. Real books, but it
#                   accepts only the 1st, 2nd and 31st, so an entry dated the
#                   7th is turned away by Tally itself.
#   not-real        not accounting software at all.
#
# And a fifth, which is where this instance actually lives (A11): the gateway
# will not tell us the licence mode at all. That is `real-licence-unknown`. It
# is NOT folded into real-ok - that is the exact false reassurance above - and
# it is NOT folded into real-practice, because calling a licence Educational
# without measuring it is inventing a result.
#
# Every state carries a `data-backend-state="..."` attribute and assertions
# match THAT, not the prose. The marker appears in exactly one place in the
# document; the sentences are meant to be edited freely. Two tests written
# earlier today were green and vacuous because they searched a whole page for a
# common word the stylesheet already contained.

BACKEND_REAL_OK = "real-ok"
BACKEND_UNAVAILABLE = "unavailable"
BACKEND_REAL_PRACTICE = "real-practice"
BACKEND_NOT_REAL = "not-real"
BACKEND_LICENCE_UNKNOWN = "real-licence-unknown"

# state -> the words a person reads. A total map rather than an if/elif chain,
# for the same reason `ACTION_FOR` is one: a state with no words renders a blank
# notice, and a map can be proved total over the state list while a chain
# cannot. `{backend}` and `{endpoint}` are filled from the live identity, both
# escaped before they go in.
BACKEND_WORDS: dict[str, str] = {
    BACKEND_UNAVAILABLE: (
        '<div class=warn data-backend-state="unavailable">'
        "<b>We are not connected to Tally, so nothing here works.</b> "
        "We cannot read your books and we cannot save anything into them. "
        "Check that Tally is open and that your company is open inside it, "
        "then start this app again."
        "</div>"
    ),
    BACKEND_NOT_REAL: (
        '<div class=warn data-backend-state="not-real">'
        "<b>Not real accounting software.</b> This is talking to "
        "<b>{backend}</b> at {endpoint}, not to TallyPrime. "
        "Nothing here reaches any real books."
        "</div>"
    ),
    BACKEND_REAL_PRACTICE: (
        '<div class=warn data-backend-state="real-practice">'
        "<b>This is your real Tally, but it is a practice copy.</b> "
        "Tally calls this Educational mode. A practice copy only accepts "
        "entries dated the <b>1st, 2nd or 31st</b> of a month. Type any other "
        "date and Tally will turn the entry away, even though everything here "
        "will look fine. "
        "writing into <b>{backend}</b> at {endpoint}"
        "</div>"
    ),
    BACKEND_LICENCE_UNKNOWN: (
        '<div class=warn data-backend-state="real-licence-unknown">'
        "<b>This is your real Tally, but we could not tell which licence mode "
        "this Tally is in.</b> So we cannot promise it will accept what you "
        "type. Some copies of Tally are practice copies, and a practice copy "
        "only accepts entries dated the 1st, 2nd or 31st of a month. Ask "
        "whoever set Tally up which kind this one is. "
        "writing into <b>{backend}</b> at {endpoint}"
        "</div>"
    ),
    BACKEND_REAL_OK: (
        '<p class=sub data-backend-state="real-ok">'
        "<b>This is your real Tally and it is ready.</b> Anything you save "
        "here goes into your real books. "
        "writing into <b>{backend}</b> at {endpoint}"
        "</p>"
    ),
}


def backend_state() -> str:
    """Which state we are in, measured off the live identity. Never guessed.

    The ORDER of these checks is the safety property, so it is worth reading
    rather than skimming:

      * nothing connected wins first, because there is no identity to read;
      * anything that is not RealTally wins next, because a fake's licence mode
        is meaningless;
      * Educational is reported only when the licence read SAID so;
      * and the last test is `!= LICENSED`, not `== UNKNOWN`. Written that way,
        every value nobody anticipated - a typo, a new Tally mode, a field that
        never got filled in - lands on the warning rather than on the
        all-clear. A default has to fall somewhere and this is the only side it
        may fall on.
    """
    if _runtime_state is None:
        return BACKEND_UNAVAILABLE
    ident = _runtime_state.identity
    if ident.backend != "RealTally":
        return BACKEND_NOT_REAL
    if ident.licence_mode == LicenceMode.EDUCATIONAL.value:
        return BACKEND_REAL_PRACTICE
    if ident.licence_mode != LicenceMode.LICENSED.value:
        return BACKEND_LICENCE_UNKNOWN
    return BACKEND_REAL_OK


def backend_notice() -> str:
    """The state, in words a person can act on. On every page, always.

    Rendered even when nothing is connected. That page is the 503, and it is
    exactly the page on which "why is nothing working" needs answering.
    """
    ident = _runtime_state.identity if _runtime_state is not None else None
    return BACKEND_WORDS[backend_state()].format(
        backend=esc(ident.backend) if ident is not None else "",
        endpoint=esc(ident.endpoint) if ident is not None else "",
    )


def page(body: str) -> bytes:
    return f"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Accountant Dad</title><style>{CSS}</style>
<h1>Accountant Dad</h1>
<p class=sub>{esc(COMPANY)} &middot; posting into Tally</p>
{backend_notice()}
{body}""".encode()


def render_decision(d: pipeline.Draft) -> str:
    out = d.outcome
    cls = {"valid": "valid", "unclear": "unclear", "not_valid": "notvalid"}[out.value]
    badge = {
        "valid": "posted",
        "unclear": "needs an answer",
        "not_valid": "not posted",
    }[out.value]
    v = d.voucher

    rows = "".join(
        f"<tr><td>{esc(k)}</td><td>{esc(val)}</td></tr>"
        for k, val in [
            ("Party", v.party or "—"),
            ("Debit", v.debit_account or "—"),
            ("Credit", v.credit_account),
            ("Amount", money(v.amount_paise)),
            ("GST", money(v.gst_paise) if v.gst_paise else "—"),
            ("Date", v.date),
        ]
    )

    prov = "".join(
        f"<tr><td>{esc(k)}</td><td><code>{esc(s)}</code></td></tr>"
        for k, s in sorted((d.voucher.provenance or {}).items())
    )

    flags = "".join(
        f"<p class=reason>&#9873; <b>{esc(f.detector)}</b> — {esc(f.reason)}</p>"
        for f in d.flags
    )

    ask = ""
    if out is Outcome.UNCLEAR:
        q = pipeline.next_question(d)
        if q is not None:
            buttons = "".join(
                f'<form method=post action=/answer style="display:inline">'
                f'<input type=hidden name=draft value="{esc(d.id)}">'
                f'<input type=hidden name=problem value="{esc(q.problem_id)}">'
                f'<input type=hidden name=value value="{esc(a.value)}">'
                f"<button>{esc(a.label)}</button></form>"
                for a in q.answers
            )
            asked = len(d.answers)
            left = Q.QUESTION_CAP - asked
            ask = (
                f"<p class=ask>{esc(q.text)}</p><div class=opts>{buttons}</div>"
                f"<p class=hint>question {asked + 1} of at most {Q.QUESTION_CAP}"
                f" &middot; {left} left before I save it for you</p>"
            )

    posted = ""
    if d.posted_tally_id:
        posted = (
            f"<p class=reason>Written to Tally as "
            f"<code>{esc(d.posted_tally_id)}</code> "
            f"&middot; operation <code>{esc(d.operation_id)}</code></p>"
            f"<form method=post action=/reverse><input type=hidden name=op "
            f'value="{esc(d.operation_id)}"><button>Undo this entry</button></form>'
        )

    checks_failed = [c for c in d.checks if not c.passed]
    checks_line = (
        f"<p class=reason class=muted>{len(d.checks)} checks run, "
        f"{len(checks_failed)} failed</p>"
    )

    return f"""<div class="card {cls}">
<span class="badge b-{cls}">{badge}</span>
<p class=reason>{esc(d.reason)}</p>
{flags}{ask}{posted}
<h2>Voucher</h2><table>{rows}</table>
<h2>Where each field came from</h2><table>{prov}</table>
{checks_line}
</div>"""


def render_home(banner: str = "") -> bytes:
    live = runtime()
    ours = live.client.list_our_vouchers(COMPANY)
    tb = runtime().client.trial_balance(COMPANY)

    posted_rows = (
        "".join(
            f"<tr><td>{esc(v.party)}</td><td>{esc(v.debit_account)}</td>"
            f"<td class=num>{money(v.amount_paise)}</td>"
            f"<td><code>{esc(operation_id_in(v.narration))}</code></td></tr>"
            for v in ours
        )
        or "<tr><td colspan=4 class=muted>nothing posted yet</td></tr>"
    )

    tb_rows = "".join(
        f"<tr><td>{esc(k)}</td><td class=num>₹{rupees(abs(val))} "
        f"{'Dr' if val > 0 else 'Cr'}</td></tr>"
        for k, val in sorted(tb.items())
    )

    # Read off the persisted log, newest first. The old renderer iterated
    # `for _, m in EVENTS`, which threw the outcome away at the last moment -
    # the page could tell you a sentence had happened but not whether the
    # entry was posted, refused or merely asked about.
    rows = live.store.actions(COMPANY)[-SHOWN:]
    events = (
        "".join(
            f'<div class=ev data-outcome="{esc(r.outcome)}" '
            f'data-action="{esc(r.action)}"><b>{esc(r.outcome)}</b> '
            f"<span class=muted>{esc(r.ts.strftime('%d %b %H:%M'))}</span><br>"
            f"{esc(r.reason)}</div>"
            for r in reversed(rows)
        )
        or '<div class="ev muted">nothing yet</div>'
    )

    return page(f"""{bootstrap_banner(live.memory.report)}{banner}
<form class=entry method=post action=/entry>
<input type=text name=text autofocus
 placeholder="paid Sharma Traders 4200 for cement including 18% GST">
<button class=primary>Send</button></form>
<p class=hint>Try: <b>paid Sharma Traders 4200 for cement</b>
(known, posts straight through)
&middot; <b>paid Verma Cement 900 for bags</b> (used two accounts, asks)
&middot; <b>paid Gupta Hardware 1500 for tools</b> (never seen, asks)</p>

<h2>What we posted</h2>
<table><tr><th>Party<th>Account<th class=num>Amount<th>Operation</tr>
{posted_rows}</table>

<h2>Trial balance</h2>
<table>{tb_rows}</table>

<h2>Activity</h2><section id=log>{events}</section>""")


# ---- server -----------------------------------------------------------------


def _run(text: str) -> pipeline.Draft:
    live = runtime()
    accounts = live.client.read_accounts(COMPANY)
    history = live.client.read_vouchers(COMPANY)
    d = pipeline.build_draft(
        COMPANY,
        text.encode(),
        "text/plain",
        TypedTextExtractor(),
        accounts,
        live.memory,
    )
    d = pipeline.evaluate(d, accounts, history, live.memory)
    if d.outcome is Outcome.VALID:
        d = pipeline.post(
            d,
            live.client,
            log=live.store,
            memory=live.memory,
            run_id=live.identity.run_id,
        )
    record(d, ACTION_FOR[d.outcome])
    DRAFTS[d.id] = d
    return d


class Handler(BaseHTTPRequestHandler):
    def _send(
        self,
        body: bytes,
        code: int = 200,
        content_type: str = "text/html; charset=utf-8",
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_one_request(self) -> None:
        """Turn an unusable runtime into an answer instead of a dropped socket.

        `runtime()` raises when nothing is connected. Before 2026-08-09 that
        escaped the handler, so `socketserver` logged a traceback and closed the
        connection: the request DID fail closed, which was correct, but the
        person saw a browser error and could not tell a broken app from an
        unreachable Tally. Failing safely and failing legibly are two
        properties and only the first was present.

        503 is the honest code. The service exists and is not available, which
        is exactly what a readiness failure is — and it is what stops a caller
        retrying against a machine that cannot serve them.

        Only `RuntimeError` is caught, and only the refusal it carries. Any
        other exception still surfaces, because swallowing unknown failures
        here would hide real defects behind a tidy error page.
        """
        try:
            super().handle_one_request()
        except RuntimeError as exc:
            if not str(exc).startswith(REFUSAL):
                raise
            self._send(
                page(f"<div class=warn><b>{esc(exc)}</b></div>"),
                code=503,
            )

    def _form(self) -> dict[str, str]:
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode()
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

    def log_message(self, format: str, *args: object) -> None:  # quiet
        pass

    def do_GET(self) -> None:
        if self.path.startswith("/health"):
            body = json.dumps(health(), indent=1).encode()
            code = 200 if health()["ready"] else 503
            self._send(body, code=code, content_type="application/json")
            return
        self._send(render_home())

    def do_POST(self) -> None:
        form = self._form()

        if self.path == "/entry":
            text = (form.get("text") or "").strip()
            if not text:
                self._send(render_home())
                return
            d = _run(text)
            self._send(page(render_decision(d) + '<p><a href="/">&larr; back</a></p>'))
            return

        if self.path == "/answer":
            d = DRAFTS.get(form.get("draft", ""))
            if d is None:
                self._send(render_home("<div class=warn>draft expired</div>"))
                return
            value = form.get("value", "")
            problem = form.get("problem", "which_account")

            accounts = runtime().client.read_accounts(COMPANY)
            history = runtime().client.read_vouchers(COMPANY)

            if value == Q.HANDOVER:
                d.answers.extend((f"gave_up_{i}", "") for i in range(Q.QUESTION_CAP))
                note(
                    "handed_over",
                    "saved",
                    "the person stopped answering, so it was saved as a draft "
                    "for them to finish rather than guessed at",
                    operation_id=d.operation_id,
                    vendor_id=d.voucher.party,
                )
            elif value in (Q.YES,):
                d.answers.append((problem, "yes"))
            elif value == Q.RETYPE:
                note(
                    "retype",
                    "abandoned",
                    "the numbers were wrong, so the entry was thrown away and "
                    "the person asked to type it again",
                    operation_id=d.operation_id,
                    vendor_id=d.voucher.party,
                )
                self._send(
                    render_home(
                        "<div class=warn>Type it again with the right numbers.</div>"
                    )
                )
                return
            else:
                d = pipeline.answer(d, value, problem_id=problem)
                # The correction is recorded against THIS company and no other,
                # and it is evidence, not an override: a vendor with genuinely
                # contradictory history stays CONFLICTED and keeps asking.
                #
                # NOT for the funding answer. `record_correction` teaches the
                # vendor -> EXPENSE account map, and "I paid in cash" says
                # nothing about what the money was for. Recording it wrote
                # "Gupta Hardware -> Cash" alongside "Gupta Hardware ->
                # Purchases", which made the vendor CONFLICTED, re-raised the
                # question the person had just answered, and ended the entry at
                # NOT_VALID with both legs correctly filled in. Measured on the
                # first two-question run. The funding leg is learned instead
                # from the posted voucher's own credit side.
                if problem != pipeline.FUNDING_PROBLEM:
                    runtime().memory.record_correction(d.voucher.party, value)

            d = pipeline.evaluate(d, accounts, history, runtime().memory)

            if d.outcome is Outcome.VALID:
                live = runtime()
                d = pipeline.post(
                    d,
                    live.client,
                    log=live.store,
                    memory=live.memory,
                    run_id=live.identity.run_id,
                )
            record(d, ACTION_FOR[d.outcome])
            DRAFTS[d.id] = d
            self._send(page(render_decision(d) + '<p><a href="/">&larr; back</a></p>'))
            return

        if self.path == "/reverse":
            op = form.get("op", "")
            ok = runtime().client.reverse_by_operation_id(COMPANY, op)
            note(
                "reversed",
                "reversed" if ok else "not_found",
                f"the person asked to undo {op}"
                + ("" if ok else ", and no voucher of ours carries that id"),
                operation_id=op,
            )
            self._send(render_home())
            return

        self._send(render_home(), 404)


# ---- where IS Tally, and may we write to it? --------------------------------
#
# `TallyConfig()` defaults to localhost:9000, and until 2026-08-09 `serve()` had
# no way to be told anything else. On this project that default can never work:
# TallyPrime runs inside a Windows VM, and `localhost` on the Mac is a DIFFERENT
# MACHINE from `localhost` in the guest. The app could not be pointed at the one
# Tally that exists without editing source.
#
# Environment variables rather than a config file or a flag: no new dependency,
# nothing to parse, nothing to keep in sync, and it works identically from a
# terminal, a launcher and a packaged .exe.
#
# Principle 9 - defaults must be EXPLICIT. Every resolved value is printed at
# startup, including which ones came from the environment and which are
# defaults, so "which Tally am I talking to" is never a guess.

ENV_HOST = "ACCOUNTANT_TALLY_HOST"
ENV_PORT = "ACCOUNTANT_TALLY_PORT"
ENV_COMPANY = "ACCOUNTANT_COMPANY"
ENV_BACKED_UP = "ACCOUNTANT_BACKED_UP_COMPANIES"


def config_from_environment() -> tuple[TallyConfig, str, RecordedBackups, list[str]]:
    """Resolve where Tally is, and which companies we are permitted to write to.

    Returns the config, the company, the backup record, and a human-readable
    list of what came from WHERE - because a resolved value with no provenance
    is the same ambiguity as no value at all.

    `RecordedBackups` stays EMPTY unless the operator names companies in
    ACCOUNTANT_BACKED_UP_COMPANIES. That is deliberate and it fails closed: an
    empty record refuses every write, so a person who has not said "I have a
    backup of this company" cannot post into it by starting the app. Declaring
    it is a decision, and decisions should be typed out, not defaulted into.
    """
    provenance: list[str] = []

    def read(name: str, fallback: str) -> str:
        raw = os.environ.get(name)
        if raw is None or not raw.strip():
            provenance.append(f"{name}={fallback!r} (default)")
            return fallback
        provenance.append(f"{name}={raw.strip()!r} (environment)")
        return raw.strip()

    host = read(ENV_HOST, TallyConfig.host)
    port_text = read(ENV_PORT, str(TallyConfig.port))
    company = read(ENV_COMPANY, COMPANY)
    backed_up_text = read(ENV_BACKED_UP, "")

    try:
        port = int(port_text)
    except ValueError as exc:
        # Not a fallback to 9000. A port that cannot be parsed is a typo, and
        # silently using a different one is how you connect to the wrong thing.
        raise RealTallyRequired(
            f"{REFUSAL}: no operation performed. {ENV_PORT}={port_text!r} is not "
            f"a number. Set it to the port Tally's HTTP server listens on, or "
            f"unset it to use {TallyConfig.port}."
        ) from exc

    backed_up = frozenset(
        name.strip() for name in backed_up_text.split(",") if name.strip()
    )
    return (
        TallyConfig(host=host, port=port),
        company,
        RecordedBackups(backed_up),
        provenance,
    )


def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    tally: TallyConfig | None = None,
    company: str | None = None,
    backups: RecordedBackups | None = None,
) -> None:
    """Connect to the real Tally FIRST, then serve. Refuse loudly otherwise.

    Two separate defects lived in the four lines this replaced.

    It printed "(demo, fake Tally)", which stopped being true the moment the
    fake path was removed. A banner naming the wrong backend is the cheapest
    possible way to mistake a test run for a real one.

    Worse, it never called `connect()`. P3.1 deleted the old `seed()` FakeTally
    path and nothing replaced it, so `python -m accountant.web.app` — the exact
    command in README.md — started a server on which EVERY page answered
    "REAL TALLY REQUIRED". Failing closed is correct; never being able to open
    is not, and it made the whole product unrunnable while every test passed.
    No test could have caught it: the tests inject a client through
    `configure()` and so never take this path at all.

    Refusing here rather than per-request is deliberate. If Tally is not there,
    the person finds out in the terminal in one second, not by opening a page
    that looks like an app and refuses everything they type.
    """
    env_tally, env_company, env_backups, provenance = config_from_environment()
    tally = tally if tally is not None else env_tally
    company = company if company is not None else env_company
    backups = backups if backups is not None else env_backups

    # Printed BEFORE connecting, so a wrong address is visible even when the
    # connection then fails. A refusal that does not say where it tried to go
    # sends the reader to check Tally when the real fault is a typo here.
    print("Accountant Dad, resolving configuration:")
    for line in provenance:
        print(f"  {line}")
    if not backups.companies:
        print(
            f"  no company is recorded as backed up, so WRITES WILL BE REFUSED. "
            f"Set {ENV_BACKED_UP} to a comma-separated list once you have a backup."
        )

    live = connect(tally, company, backups=backups)
    print(
        f"Accountant Dad -> http://{host}:{port}\n"
        f"  backend {live.identity.backend} at {live.identity.endpoint}\n"
        f"  company {live.identity.company!r}\n"
        f"  books    {live.memory.report.status.value}\n"
        f"  writable {sorted(backups.companies) or 'NOTHING - reads only'}\n"
        f"  run      {live.identity.run_id}"
    )
    HTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":  # pragma: no cover - the process entry point
    try:
        serve()
    except RealTallyRequired as exc:
        # Exit non-zero so a launcher, a script or a packaged .exe can tell the
        # difference between "stopped" and "never started".
        raise SystemExit(f"{REFUSAL}: no operation performed. {exc}") from exc

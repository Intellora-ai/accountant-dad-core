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

import contextlib
import contextvars
import datetime
import html
import json
import os
import ssl
import time
import urllib.parse
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from accountant import observability, pipeline, reversal
from accountant import questions as Q
from accountant.auth import (
    ENV_LOCAL_DEV_MODE,
    LOCAL_DEV_TENANT,
    SESSION_HOURS,
    AuthRefusal,
    Principal,
    authenticate,
    local_dev_mode,
    new_token,
    token_fingerprint,
    verify_password,
)
from accountant.extract.adapter import Extractor
from accountant.extract.registry import default_extractor, guarded
from accountant.memory.bootstrap import bootstrap
from accountant.memory.company import CompanyMemory
from accountant.memory.identity import normalise_company, same_company_name
from accountant.memory.store import (
    IN_MEMORY,
    BootstrapReport,
    BootstrapStatus,
    MemoryStore,
    TenantDeletion,
)
from accountant.rules.gst_rates import RateRule, official_corpus
from accountant.schema import ActionLog, Outcome
from accountant.tallyio.client import (
    DuplicateOperation,
    TallyClient,
    operation_id_in,
)
from accountant.tallyio.factory import (
    BackendIdentity,
    LicenceMode,
    RealTallyRequired,
    real_tally,
)
from accountant.tallyio.real import RecordedBackups, TallyConfig

# THE CONFIGURATION DEFAULT, AND NOTHING ELSE. D8, FIXED 2026-08-09.
#
# Until today every request handler read this constant and passed it to Tally:
# `read_accounts`, `read_vouchers`, `list_our_vouchers`, `trial_balance`,
# `build_draft`, `reverse_operation`, `reversal.preview`, and the action-log
# read on the home page. Startup did not - `serve()` honours ACCOUNTANT_COMPANY
# and `configure()` bootstraps memory for whatever company the identity names.
#
# So a person who set their own company name got memory keyed to THEIR company
# and a page asking Tally for this string. It failed closed, but by accident:
# the connector raised for a company that is not open, so the request died with
# a traceback, and where it did not, `pipeline.build_draft`'s cross-company
# guard raised instead - the last line of defence doing the first one's job. The
# app worked for exactly one company name in the world.
#
# It is now a DEFAULT for configuration and may be read in exactly two places,
# `connect()` and `config_from_environment()`. What a handler uses is
# `runtime().company`, which is measured off the live connection and checked
# against memory on every request.
# `tests/test_company_identity.py::test_no_request_handler_reads_the_module_default`
# scans the AST and fails if that ever changes.
COMPANY = "Accountant Dad Final"

# OWNER DECISION, 2026-08-10: flag_cap = 3
#
#     decision owner = project owner
#     reason = show the first three concerns while preserving all concerns in
#              evidence and avoiding an overloaded review screen
#
# Display only. It changes nothing about detection, storage, safety or posting:
# `evaluate` detects every concern, shows the top three, keeps the rest in
# `Draft.suppressed_flags`, and the screen says how many it did not show.
#
# Not configurable here on purpose. `flag_cap` existed as a parameter on
# `pipeline.evaluate` for the whole of Phase 6 and was never passed from the
# web, so `dropped_flags` was permanently zero in production and the overflow
# line could not render however many concerns an entry raised. A parameter no
# caller supplies is not a feature.
FLAG_CAP = 3

# OWNER-APPROVED ASSUMPTION, 2026-08-10, `docs/OWNER_DECISIONS.md` §2:
#
#     provenance in UI = the existing draft screen displays detector/rule,
#                        source URL, evidence and explanation per decision
#
# The frozen plan defines the thing this exists to make visible:
#
#     we hallucinate if and only if we output a value not derivable from the
#     input document, the company's Tally history, or the rules corpus.
#     Count of output values carrying no provenance tag.
#     Design consequence: every output value carries a source tag.
#
# THE FAILURE MODE THIS SHAPE IS CHOSEN AGAINST
# ---------------------------------------------
# A blank cell and a field with no source look identical on a screen. So a slot
# is NEVER blank: an absent source renders `NOT_RECORDED`, and a slot that
# cannot be filled renders a `NOT_AVAILABLE` marker that says WHY it cannot.
# The reader can tell "we did not record this" from "this could not be
# established, for this named reason", and neither can be mistaken for a value.
#
# And the slots are a LIST, rendered by iteration, rather than four hand-written
# rows. `dropped_flags` was computed correctly on the `Draft` and never rendered
# for the whole of Phase 6 — true in the object, false on the screen a person
# reads. Four hand-written rows is the same defect waiting to happen: delete one
# and nothing complains. `tests/test_ui_provenance.py` carries its own literal
# copy of these four names, so shortening this tuple fails there rather than
# silently shortening the page.
PROVENANCE_SLOTS: tuple[str, ...] = (
    "detector_or_rule",
    "source_url",
    "evidence",
    "explanation",
)

# What each slot is called on screen. A total map over `PROVENANCE_SLOTS`, for
# the same reason `BACKEND_WORDS` is one: a slot with no words renders a blank
# label, and a map can be proved total while an if/elif chain cannot.
PROVENANCE_LABELS: dict[str, str] = {
    "detector_or_rule": "Detector or rule",
    "source_url": "Source URL",
    "evidence": "Evidence",
    "explanation": "Explanation",
}

# The three states a slot can be in. Carried as `data-slot-state` so a test
# matches the STATE rather than the prose — two tests written earlier in this
# project were green and vacuous because they searched a whole page for a common
# word the stylesheet already contained.
SLOT_RECORDED = "recorded"
SLOT_NOT_RECORDED = "not_recorded"
SLOT_NOT_AVAILABLE = "not_available"

#: A source this decision does not carry. NEVER an empty cell.
NOT_RECORDED = "NOT_RECORDED"

#: How a slot says "this could not be established". Every such marker starts
#: with it, which is how `provenance_slots` knows the state without the reason
#: having to be one fixed sentence — the reason varies, the vocabulary does not.
NOT_AVAILABLE = "NOT_AVAILABLE"

# THE RULES CORPUS, AND THE SENTENCE THAT WENT STALE ON THIS LINE
# ---------------------------------------------------------------
# Until 2026-08-10 this slot rendered `NOT_AVAILABLE — accountant/rules/ not
# merged` on every decision, and `tests/test_ui_provenance.py` asserted it.
# Commit 7db7f45 merged `accountant/rules/`. The sentence was then FALSE, on the
# one panel in this product whose entire job is to be trusted about where a
# number came from, and the test that should have caught it was the thing
# holding it in place.
#
# It is loaded HERE, at import, on purpose. `official_corpus()` reads
# module-level literals and opens no socket — owner decision Q1 = A forbids a
# network call anywhere in that package — so there is nothing to fail at request
# time and nothing to cache. A corpus fetched per request would also mean the
# page could disagree with itself between two rows.
RULES = official_corpus()

#: Every loaded rule by its id, and the ONLY place this screen may get a URL.
#:
#: A citation is a CLAIM: it names a `rule_id` and carries a URL it says belongs
#: to that rule. The claim is not repeated. The `rule_id` is resolved through
#: this map and the corpus's own URL is what renders, so the worst a bad
#: citation can do is name a rule that is not here — which is reported.
RULES_BY_ID: dict[str, RateRule] = {rule.rule_id: rule for rule in RULES.loaded}

#: No rule was cited for this decision. TRUE, and for the real reason.
#:
#: `accountant/rules/` is merged and this module loads it. What does not exist
#: is the WIRING: `pipeline.evaluate` never calls `accountant.tax.decision`, so
#: no `Citation` ever reaches a `Decision` or a `Flag` on this screen. "The
#: corpus is not here" and "nothing on this screen cites it" are different
#: statements and only the second one is true.
#:
#: The count is measured off the corpus rather than written down, so the
#: sentence cannot drift from the thing it describes, and an app that stopped
#: loading the corpus would print 0 instead of quietly reading the same words.
RULE_URL_NOT_CITED = (
    f"{NOT_AVAILABLE} — no rule cited: the merged corpus holds "
    f"{len(RULES.loaded)} loaded rules and this decision cites none of them"
)

#: Why a citation was refused instead of being turned into a URL.
NOT_IN_CORPUS = "the rule cited is not in the merged corpus"
URL_DISAGREES = "the URL cited is not the corpus URL for the rule cited"

#: The rule that decides an entry with nothing wrong with it.
#:
#: Not a placeholder and not invented: `accountant/decide.py` is the decision
#: order, and an entry that reaches its last branch was decided by the order
#: itself rather than by any named problem. Naming the module that decided is a
#: source; leaving the slot blank would be the hallucination.
DECISION_ORDER_RULE = "decision_order"

DRAFTS: dict[str, pipeline.Draft] = {}

# How many drafts stay answerable at once. `DRAFTS` was unbounded: every entry
# anybody ever typed stayed in memory for the life of the process, holding its
# voucher, its checks, its flags and its problems. `EVENTS`, the thing it sat
# next to, was capped at forty - so the audit trail was the bounded one and the
# unbounded one was live state.
#
# 200 rather than 40: a draft is only useful while somebody might still answer
# its question, and answering happens within minutes, but evicting one out from
# under a person mid-question is a worse failure than holding a few more.
# Eviction is oldest-first and it is not silent - the handler says the draft
# expired rather than 404-ing on an id the person is looking at.
#
# The DRAFT IS NOT THE RECORD. Every decision is already durable in the action
# log, so evicting one loses a form in progress and nothing else.
DRAFT_LIMIT = 200


def remember_draft(draft: pipeline.Draft) -> None:
    """Keep this draft answerable, and drop the oldest once past the limit."""
    DRAFTS[draft.id] = draft
    while len(DRAFTS) > DRAFT_LIMIT:
        DRAFTS.pop(next(iter(DRAFTS)))


# Previewed bulk reversals, waiting for a yes. Bounded for the same reason
# `DRAFTS` is, and small because a preview is answered in seconds or abandoned:
# nobody comes back to one an hour later, and if they do, taking a fresh
# preview is the correct thing to make them do.
#
# The batch is held rather than recomputed because the candidate list is the
# thing being confirmed. Re-listing at confirmation time would mean a voucher
# posted in the gap gets reversed by a click that never showed it.
#: batch id -> (the batch, the user who previewed it).
#:
#: THE USER IS PART OF THE KEY, not decoration. The two-step preview exists so
#: that the person who confirms a bulk reversal is the person who SAW the list
#: it will destroy. Keyed by id alone, that guarantee lasted exactly as long as
#: one person used the app: colleague A previews, colleague B posts the
#: confirmation with A's batch id, and B has just deleted every voucher we ever
#: wrote in that company having been shown nothing at all.
BATCHES: dict[str, tuple[reversal.Batch, str]] = {}
BATCH_LIMIT = 20


def remember_batch(batch: reversal.Batch, who: Principal | None = None) -> None:
    BATCHES[batch.batch_id] = (batch, who.user_id if who else NOT_RECORDED)
    while len(BATCHES) > BATCH_LIMIT:
        BATCHES.pop(next(iter(BATCHES)))


def draft_for(draft_id: str, live: Runtime) -> pipeline.Draft | None:
    """The draft, but only if it belongs to the company we are bound to.

    Defect D-B, found 2026-08-10. `DRAFTS` is keyed by draft id alone, and the
    id says nothing about whose books the draft is for. A draft built while the
    app was bound to one company was rendered under another company's header
    once the runtime changed - party, both ledgers, amount, Tally id and an
    "Undo this entry" button, all drawn under the wrong name.

    It was display only: the undo button posts to a route that looks in the
    CURRENT company and reports not-found, and `pipeline.evaluate` refuses a
    foreign draft outright. So nothing was ever written to the wrong company
    through this path. Showing one company's entry under another company's name
    is still the single most alarming thing this product could do to an
    accountant, and "it was only the screen" is not a defence.

    An UNKNOWN draft returns None, which every caller already renders as "that
    draft expired". A FOREIGN draft raises instead, and the difference is
    deliberate: "expired" is a normal, uninteresting end to a form, while a
    request naming another company's draft means something is wrong with the
    identity of this session and must not read as routine. The handler turns
    the refusal into a 503 that names no internals, and the durable log keeps
    the detail.
    """
    draft = DRAFTS.get(draft_id)
    if draft is None:
        return None
    if normalise_company(draft.company) != live.company_key:
        raise RuntimeError(
            f"{REFUSAL}: no operation performed. Draft {draft_id!r} belongs to "
            f"company {draft.company!r}, and this app is bound to "
            f"{live.company!r}. Nothing was read and nothing was written."
        )
    return draft


def answer_refusal(draft: pipeline.Draft, problem_id: str, value: str) -> str:
    """Why this answer is not an answer to the question this entry is asking.

    Empty string means it is, and the route may proceed. Anything else is a 400
    and NOTHING is touched — no ledger leg, no memory correction, no log row,
    not even a read of Tally.

    WHY THE CHECK IS SCOPED TO UNCLEAR, and why that is a scope and not a hole.

    Only an UNCLEAR entry is asking anything, so only an UNCLEAR entry has a
    question for an answer to be bound to. The other two outcomes reach this
    route as replays, and each already has a guard that owns it:

        VALID      the entry is in the books. `pipeline.post` refuses the
                   duplicate operation id, the person gets a 503 saying the
                   service would not do it twice, and a durable row records the
                   refusal. Answering 400 here instead would replace a refusal
                   that names the real reason with one that does not.
        NOT_VALID  the entry was handed over or ran out of questions. There is
                   nothing outstanding, nothing is posted, and the books do not
                   move however many times the form is resubmitted.

    Both are pinned in `tests/test_idempotency.py`. Stepping aside for them is
    the point: this guard exists to stop an answer reaching the WRONG LEDGER
    LEG, and neither of those paths reaches a ledger leg at all.
    """
    if draft.decision is None or draft.decision.outcome is not Outcome.UNCLEAR:
        return ""
    return draft.decision.refuse_answer(
        operation_id=draft.operation_id, problem_id=problem_id, value=value
    )


def batch_for(batch_id: str, live: Runtime) -> reversal.Batch | None:
    """The previewed batch, but only if it is for the company we are bound to.

    Defect D-A, found 2026-08-10, and the worst of the three: a wrong-company
    WRITE. `reversal._drive` reverses in `batch.company`, while the handler
    files its audit rows under the CURRENT runtime's key. Nothing compared the
    two. Measured: a batch previewed for one company and confirmed after the
    app was bound to another DELETED the first company's voucher and wrote the
    reversal rows under the second - so the company whose books actually
    changed has an audit trail that says `posted` and never says `reversed`.

    Not reachable from `serve()` today, because a process calls `connect()`
    once. It is reachable through `configure()`, which is public, and the
    repository had already noticed: the company-identity test fixture clears
    `DRAFTS` and `BATCHES` by hand. A guard the tests apply and the code does
    not is not a guard.

    A foreign batch is left in place rather than popped. Popping it would
    destroy another company's pending preview as a side effect of a request
    that has no business touching it.

    THE SECOND CHECK, added with tenancy. Company is not enough once two people
    share a company, which is the normal case in an accounts department. The
    preview→confirm pair exists to guarantee that whoever presses the button saw
    the list; a confirmation from a DIFFERENT person than the one who previewed
    breaks that guarantee completely, and it breaks it silently, because the
    batch is valid and the company matches.

    Refused rather than re-previewed, and left in place rather than popped, for
    the same reason as above: the person who took the preview may still be
    looking at it.
    """
    held = BATCHES.get(batch_id)
    if held is None:
        return None
    batch, previewed_by = held
    if normalise_company(batch.company) != live.company_key:
        return None

    who = current_principal()
    mine = who.user_id if who else NOT_RECORDED
    if previewed_by != mine:
        return None

    BATCHES.pop(batch_id, None)
    return batch


@dataclass(frozen=True)
class DeletionPlan:
    """What deleting THIS caller's own customer record would destroy.

    Built from the store, shown on screen, and confirmed by a second request
    carrying its id. Every number here is MEASURED off the database rather than
    described in prose, because "delete my data" is the one button in this
    product whose blast radius the person cannot see from the button — the same
    argument the bulk-reversal preview is built on, applied to the one action
    that is even less reversible.

    `tenant_id` is on the plan for the audit trail and for the check in
    `deletion_for`, NOT so that it can be posted back. It never reaches a form
    field: the route reads the caller's tenant off the credential every time,
    and `tests/test_data_deletion.py` walks the AST of this module to prove no
    tenant id is ever read out of a request.
    """

    plan_id: str
    tenant_id: str
    tenant_name: str
    companies_erased: tuple[str, ...]
    companies_kept: tuple[str, ...]
    users: int
    sessions: int
    actions_kept: int


#: Previewed deletions, waiting for a yes. Same shape as `BATCHES` and for the
#: same reason, including the user in the value rather than only the id.
#:
#: Bounded, and small, because a preview is answered in seconds or abandoned.
#: A deletion plan that has aged out is not a problem to route around: taking a
#: fresh preview is exactly what somebody should be made to do, because the
#: numbers it shows may no longer be the numbers that would be destroyed.
#: plan id -> (the plan, the user who asked for it).
DELETIONS: dict[str, tuple[DeletionPlan, str]] = {}
DELETION_LIMIT = 20


def remember_deletion(plan: DeletionPlan, who: Principal | None = None) -> None:
    DELETIONS[plan.plan_id] = (plan, who.user_id if who else NOT_RECORDED)
    while len(DELETIONS) > DELETION_LIMIT:
        DELETIONS.pop(next(iter(DELETIONS)))


def deletion_plan(store: MemoryStore, who: Principal) -> DeletionPlan | None:
    """Measure what this caller's deletion would do. None when there is nobody.

    The tenant comes from `who`, which came from the credential. There is no
    parameter here that a request could fill in, and that is deliberate: a
    function that could be asked to plan the deletion of a NAMED tenant is one
    careless caller away from being the hole `docs/AUTH.md` exists to prevent.

    None means this session has no customer record behind it — the local-dev
    principal is the reachable case, and a user row created without a tenant row
    is the other. Neither can be deleted: there is nowhere to record that the
    deletion happened, and erasing an index while nothing says who asked or when
    is the half-deletion `MemoryStore.delete_tenant` refuses outright.
    """
    tenant = store.tenant(who.tenant_id)
    if tenant is None:
        return None

    # THE STORE'S OWN FUNCTION, not a second copy of the rule. What this screen
    # promises and what `delete_tenant` then does have to be one thing, or the
    # person is confirming a list that is not the list that happens — which is
    # the defect the two-step preview exists to prevent.
    erase, keep = store.deletion_scope(who.tenant_id)

    return DeletionPlan(
        plan_id=f"erase_{uuid.uuid4().hex[:12]}",
        tenant_id=tenant.tenant_id,
        tenant_name=tenant.name,
        companies_erased=erase,
        companies_kept=keep,
        users=len(store.users_of_tenant(who.tenant_id)),
        sessions=len(store.live_sessions_of_tenant(who.tenant_id)),
        actions_kept=len(store.actions_of_tenant(who.tenant_id)),
    )


def deletion_for(plan_id: str) -> DeletionPlan | None:
    """The previewed plan, but only for the person who was shown it.

    The same rule as `batch_for`, for a stronger reason. A bulk reversal
    destroys vouchers that can be typed again; this closes an account and erases
    an index, and the person who confirms it must be the person who read what it
    said would happen. A confirmation from anybody else is a deletion nobody was
    shown, and it is silent — the plan is real, the session is valid, and every
    other check passes.

    The tenant is checked as well as the user. One user cannot change tenants
    today, so it is a comparison that cannot currently fail; it is here because
    the thing being executed is a deletion, and a plan for one customer reaching
    a request from another must be impossible by construction rather than by the
    shape of the current user table.

    A plan that is not this caller's is LEFT IN PLACE rather than popped. The
    person who took it may still be looking at it, and destroying their preview
    as a side effect of somebody else's request is the smaller version of the
    same mistake.
    """
    held = DELETIONS.get(plan_id)
    if held is None:
        return None
    plan, asked_by = held

    who = current_principal()
    mine = who.user_id if who else NOT_RECORDED
    if asked_by != mine:
        return None
    if who is None or plan.tenant_id != who.tenant_id:
        return None

    DELETIONS.pop(plan_id, None)
    return plan


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
    #: The extraction backend every request in this runtime uses.
    #:
    #: RESOLVED ONCE, HERE, AND NEVER INSIDE A REQUEST HANDLER. `_run` used to
    #: call `default_extractor()` on every entry, which is where a hidden
    #: backend gets instantiated: the route decided what read the bill, so no
    #: caller could put the running app on a different one and a reader OUTAGE
    #: was unreachable over HTTP. It is a field for the same reason `client`
    #: is — the thing a request depends on is injected at `configure()` and
    #: held, not conjured per request.
    #:
    #: No default. There is exactly one construction site (`configure`), and a
    #: default here would let a future second site build a runtime whose
    #: extractor was silently something else.
    extractor: Extractor

    @property
    def company(self) -> str:
        """The ONE company name every request uses. Never the module default.

        Read off `BackendIdentity`, which the factory MEASURED: `real_tally`
        refuses to build one unless that exact name came back from Tally's own
        `list_companies`. So this is not a setting we are trusting, it is a
        company we watched Tally admit to having open.
        """
        return self.identity.company

    @property
    def company_key(self) -> str:
        """The scope key on every stored row and every audit row.

        Deliberately read off MEMORY rather than derived from `company` here.
        Two objects, two sources, and `company_mismatch` proves them equal on
        every request - which is a check. Deriving both from one field would be
        a promise.
        """
        return self.memory.identity.key

    def confirm_company(self) -> None:
        """The two company checks that need I/O. Once per request, before work.

        Separate from `company_mismatch`, which is pure and runs on every
        `runtime()` call. These two READ - one from our store, one from Tally -
        so they run once, on the request thread, where both connections live.
        SQLite hands a connection to the thread that opened it, and the app
        opens its store on the thread that serves; a check that read the store
        from anywhere else would fail for a reason that has nothing to do with
        companies.

        OUR INDEX IS STILL OURS. `MemoryStore.company` is keyed on
        `company_key` ALONE with an `INSERT OR REPLACE` writer, so two names
        that normalise alike share one row. `bootstrap` refuses that pair only
        when both are open in Tally at the same moment, which two runs or two
        processes are not. Our own row can therefore be rewritten under us to
        name somebody else's company, and everything read back after that is
        their history. Checked only while this memory still claims to have read
        something: a report that is already refusing cannot leak anything, and
        COMPANY_KEY_COLLISION carries a banner naming the two companies, which
        is more use than this message would be.

        TALLY STILL HAS IT OPEN. The other identities are ours and cannot move
        on their own. This one can: a person closes the company in TallyPrime
        and opens another, and from that moment every read and write in this
        process aims at a company that is not there. It already failed closed -
        the connector raises for a company it cannot find - but it failed as a
        traceback and a dropped socket, which tells the person nothing. This
        makes it the 503 the handler already knows how to draw, and it names
        what IS open so they can act.

        One extra round trip per request. That is the honest price of "which
        books am I writing into" being a measurement rather than an assumption.
        """
        stored = self.store.state(self.company_key)
        if (
            self.memory.report.askable
            and stored is not None
            and not same_company_name(stored.identity.name, self.company)
        ):
            stale = (
                f"our stored memory under key {self.company_key!r} now names "
                f"company {stored.identity.name!r}, not {self.company!r}. "
                f"Another company whose name reduces to the same key has "
                f"overwritten our index, so what we would read back is their "
                f"history and not yours"
            )
            _record_mismatch(self, stale)
            raise RuntimeError(
                f"{REFUSAL}: no operation performed. {stale}. Nothing was read "
                f"and nothing was written. Give one of those two companies a "
                f"clearly different name in Tally, then start this app again."
            )

        try:
            # Timed like every other call out to Tally. This one is the round
            # trip EVERY request pays for "which books am I writing into" being
            # a measurement, so it is the first place a slow Tally shows up.
            with observability.tally_call("list_companies"):
                open_now = self.client.list_companies()
        except Exception as exc:
            raise RuntimeError(
                f"{REFUSAL}: no operation performed. Tally would not say which "
                f"companies are open, so we cannot confirm {self.company!r} is "
                f"still the one we are working in: {type(exc).__name__}: {exc}"
            ) from exc

        # D-C: NFC comparison. An exact `in` made a macOS-typed name and the
        # same name from Tally on Windows two different companies, and told
        # the operator to open a company that was already open.
        if not any(same_company_name(self.company, o) for o in open_now):
            raise RuntimeError(
                f"{REFUSAL}: no operation performed. {self.company!r} is no "
                f"longer open in Tally. {len(open_now)} company/companies are "
                f"open: {list(open_now)}. Nothing was read and nothing was "
                f"written. Open {self.company!r} in Tally again, or start this "
                f"app again for the company you mean to work in."
            )


_runtime_state: Runtime | None = None


# One spelling of the refusal, so the handler can recognise its own and the
# tests can assert on something that cannot drift out of sync.
REFUSAL = "REAL TALLY REQUIRED"

#: The action name a refused request writes when the company identities differ.
COMPANY_MISMATCH = "company_mismatch"

#: Mismatches already written down, by their exact wording. A mismatch is a
#: standing condition, not an event: `runtime()` is called several times per
#: request and every call would otherwise add a row, so the one fact would be
#: buried under copies of itself in the log a person has to read.
_recorded_mismatches: set[str] = set()


def company_mismatch(state: Runtime) -> str:
    """Why the runtime's own two company identities disagree, or "" if they do not.

    FIVE THINGS NAME A COMPANY on every request, and all five must be one:

        startup   `BackendIdentity.company`, which the factory saw in Tally's
                  own `list_companies` before it would build a client
        memory    `CompanyMemory.identity`, built by `bootstrap`
        request   what the handler hands the pipeline
        Tally     what the handler hands `client.*`
        audit     the `company_key` on every ActionLog row

    The last three now read `Runtime.company` / `Runtime.company_key`, so they
    cannot differ from the first two by construction. This function checks the
    first two against each other. The two checks that need I/O - our stored
    index still being ours, and Tally still having the company open - are in
    `Runtime.confirm_company`, which runs once per request on the request
    thread.

    PURE, and returning a SENTENCE rather than a bool, for three reasons: it is
    called on every `runtime()` and must cost nothing; `health()` must report
    the disagreement without raising or touching a connection; and a refusal
    that does not name both companies leaves the reader unable to tell which of
    them is the wrong one.

    Both checks are structurally unreachable today - `configure()` bootstraps
    memory from `identity.company`, so the two are built from one string. They
    are kept because "unreachable" is a property of today's call graph and not
    of the invariant, and because the cost of keeping them is two string
    comparisons.
    """
    startup = state.identity.company
    remembered = state.memory.identity

    if remembered.name != startup:
        return (
            f"startup connected to company {startup!r} but the memory in use "
            f"was built for {remembered.name!r}. Two different sets of books "
            f"cannot answer one question"
        )

    expected = normalise_company(startup)
    if remembered.key != expected:
        return (
            f"the memory for company {startup!r} carries the scope key "
            f"{remembered.key!r}, which is not the key of that name "
            f"({expected!r}). Every stored row and every audit row would be "
            f"filed under a company nobody asked about"
        )

    return ""


def _record_mismatch(state: Runtime, detail: str) -> None:
    """Write the disagreement into the audit trail, once.

    A refusal nobody can find afterwards cannot be investigated, and this is
    exactly the failure somebody will be asked to explain six months later.
    Filed under the STARTUP company's key: that is the company the person
    believes they are working in, and it is where they will go looking.
    """
    if detail in _recorded_mismatches:
        return
    _recorded_mismatches.add(detail)
    state.store.record_action(
        ActionLog(
            ts=datetime.datetime.now(datetime.UTC),
            action=COMPANY_MISMATCH,
            company_key=normalise_company(state.identity.company),
            outcome="refused",
            reason=detail,
            run_id=state.identity.run_id,
            backend=type(state.client).__name__,
        )
    )


def install(state: Runtime) -> Runtime:
    """Make this runtime the live one. The setter half of `configure()`.

    Deliberately does NOT verify. Verification lives in `runtime()`, at the
    point of USE, because that is the only place that covers every route in -
    including the ones that do not exist yet. A runtime installed here that
    later stops agreeing with itself is refused on the next request, and
    `tests/test_company_identity.py` installs a broken one on purpose to prove
    exactly that.
    """
    global _runtime_state
    _runtime_state = state
    return state


def runtime() -> Runtime:
    """The live runtime, or a refusal. Never a silently absent one.

    Two ways to be refused, and they are different facts:

        nothing connected   there is no company, so nothing can be read
        companies disagree  there are two, and we will not guess between them

    The second is checked on EVERY call rather than only at startup, because
    the thing it catches - our store row being rewritten by another company
    that shares the key - happens while this process is running and cannot be
    noticed at connect time.
    """
    if _runtime_state is None:
        raise RuntimeError(
            f"{REFUSAL}: no operation performed. "
            "connect() or configure() has not been called, so no company has "
            "been read and nothing may be proposed or posted."
        )

    live = _runtime_state
    wrong = company_mismatch(live)
    if wrong:
        _record_mismatch(live, wrong)
        raise RuntimeError(
            f"{REFUSAL}: no operation performed. {wrong}. Nothing was read and "
            f"nothing was written. Start this app again for the company you "
            f"mean to work in."
        )
    return live


def served_tenant() -> str:
    """The one customer this process serves. Defect J1, 2026-08-11.

    FAILS CLOSED, and the two modes differ for a stated reason:

        LOCAL_DEV_MODE=1   `local-dev`, the same tenant `authenticate` hands
                           out in that mode, so the check passes and measures
                           nothing. There is one person and no customers.
        production         `ACCOUNTANT_TENANT` is REQUIRED. Unset means refuse
                           every request rather than serve them all.

    Refusing is the whole point. The alternative - treat unset as "any tenant
    may enter" - is the defect this function exists to close, reintroduced as a
    default. A deployment that forgets the variable is broken and says so on the
    first request; one that silently admits everybody is broken and does not.
    """
    if local_dev_mode():
        return LOCAL_DEV_TENANT
    named = os.environ.get(ENV_TENANT, "").strip()
    if not named:
        raise AuthRefusal(
            403,
            f"this server does not know whose books it is serving, so it will "
            f"not serve them to anybody. Set {ENV_TENANT} to the tenant id that "
            f"owns this company",
        )
    return named


def auth_store() -> MemoryStore:
    """Where sessions are looked up.

    Reads `_runtime_state` DIRECTLY rather than calling `runtime()`, and the
    difference matters. "Who are you" and "which company is open" are two
    separate questions: a company mismatch must not turn into a 401, because
    401 says the credential is bad and the credential is fine. Every route that
    needs a company still calls `_confirm_company` afterwards and still gets its
    503 — the two checks stay in order and stay distinguishable.
    """
    if _runtime_state is None:
        raise RuntimeError(
            f"{REFUSAL}: no operation performed. "
            "connect() or configure() has not been called, so there is nowhere "
            "to look a session up and nobody can be identified."
        )
    return _runtime_state.store


#: Who is acting, for the duration of ONE request.
#:
#: A ContextVar rather than a module global, and that is not a style choice.
#: Task 11 replaces `HTTPServer` with a threading one, and a plain global would
#: then be one customer's identity visible to another customer's request — the
#: exact cross-tenant leak this whole task exists to prevent. Every thread gets
#: its own context, so a `set` here is invisible to every other request.
#:
#: `None` means nobody was identified, and the audit row says `NOT_RECORDED`
#: rather than inventing an actor.
_principal: contextvars.ContextVar[Principal | None] = contextvars.ContextVar(
    "accountant_principal", default=None
)


def current_principal() -> Principal | None:
    """Who this request belongs to, or None outside a request."""
    return _principal.get()


def login(email: str, password: str, *, now: datetime.datetime | None = None) -> str:
    """Check a password and open a session. Returns the token, ONCE.

    The token is returned and never stored; what the database gets is its
    fingerprint. So this is the only moment the token exists in full, and losing
    it means logging in again rather than reading it back out of a row.

    One refusal sentence for both "no such user" and "wrong password", on
    purpose. Two different messages let anybody with a browser enumerate which
    email addresses have accounts, which is a customer list handed out for free.
    """
    when = now or datetime.datetime.now(datetime.UTC)
    store = auth_store()
    user = store.user_by_email(email.strip().lower())
    refusal = AuthRefusal(401, "that email address and password do not match")
    if user is None or not user.live:
        # The hash still runs when there is no user, so the answer takes the
        # same time either way. A fast "no" and a slow "no" are two different
        # answers to somebody with a stopwatch.
        verify_password(password, "0" * 128, "00")
        raise refusal
    if not verify_password(password, user.password_hash, user.salt):
        raise refusal

    token = new_token()
    store.open_session(
        token_fingerprint(token),
        user.user_id,
        user.tenant_id,
        when.isoformat(),
        (when + datetime.timedelta(hours=SESSION_HOURS)).isoformat(),
    )
    return token


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
            # None, not `COMPANY`. Nothing is connected, so there IS no company,
            # and printing the configuration default here said we were attached
            # to books we had never opened.
            "company": None,
            "detail": (
                f"{REFUSAL}: no operation performed. "
                "connect() or configure() has not been called."
            ),
        }

    live = _runtime_state

    # Before anything measured, because every count below is scoped by a company
    # and reporting counts while two companies are in play would say which
    # company they belong to when we do not know.
    wrong = company_mismatch(live)
    if wrong:
        return {
            "ready": False,
            "bootstrap_status": live.memory.report.status.value,
            "failure_code": "COMPANY_MISMATCH",
            "backend_state": backend_state(),
            "company": live.company,
            "detail": wrong,
            **live.identity.as_metrics(),
        }

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
        # The company this runtime is actually working in, measured off the
        # live identity. Never the module default.
        "company": live.company,
        "company_key": live.company_key,
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


#: The route the counts are served on. Named so the test and the handler
#: cannot disagree about the spelling.
METRICS_PATH = "/metrics"

#: The log event that mirrors ONE durable `action_log` row. See `_note_in_log`
#: for why this line exists and why the request id is not a column on that
#: table instead.
AUDIT_ROW_EVENT = "audit_row"


def metrics() -> str:
    """The counts, read from the durable log. AUTHENTICATION IS REQUIRED.

    WHY IT IS BEHIND THE CREDENTIAL, unlike `/health`.

    This body carries BUSINESS COUNTS about a named company: how many bills
    that company typed, how many we posted into their books, how many we had to
    ask about, how many were undone. `/health` says whether the service can
    receive work — useful to a load balancer and worth nothing to a competitor.
    These numbers are a customer's trading volume, and the customer's name is on
    line six. Nothing here is exposed unauthenticated; `do_GET` calls
    `_identify` before it reaches this function, and
    `tests/test_observability.py` drives an unauthenticated caller against the
    route with LOCAL_DEV_MODE deleted to prove it.

    NO COMPANY CONFIRMATION, and that is deliberate rather than an oversight.
    `_confirm_company` makes a Tally round trip; this route does not take it,
    for two reasons. A scrape runs every fifteen seconds and would otherwise
    add a Tally call per scrape forever. And the moment a person most needs
    these numbers is the moment Tally has stopped answering — an endpoint that
    needs Tally to report on Tally reports nothing exactly when it matters,
    which is the same defect `/health` had when it was a constant.

    `runtime()` is still called, so a server with nothing connected refuses
    here the way it refuses everywhere else, rather than serving zeros that
    look like a quiet day.
    """
    live = runtime()
    return observability.render_metrics(
        # THE DURABLE STORE, not a counter this process has been keeping. A
        # process-local counter resets on restart and then reports a number
        # smaller than the truth, which is worse than no number because a
        # person will act on it.
        live.store.actions(live.company),
        company=live.company,
        uptime=observability.uptime_seconds(),
    )


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
    extractor: Extractor | None = None,
) -> Runtime:
    """Install an already-built client and bootstrap this company's memory.

    The injection seam. Tests hand a double in here, which is why this module
    never needs to import one — principle 6 is about what the SHIPPED code can
    reach, not about forbidding doubles in tests.

    `extractor` JOINED THE SEAM 2026-08-10, and it is the whole of the HTTP
    reader outage.

    The reading backend was resolved inside the request handler, so a test
    could reach an outage through `pipeline.run` and could not reach one
    through the surface a person actually touches. The two honest ways to get
    there were to monkey-patch a `Final` constant, which proves something about
    the patch, or to add this parameter. This is the parameter.

    IT NAMES NO BACKEND, and that is what keeps exit 7.1 at zero. The
    annotation is the `Extractor` Protocol, the default is
    `registry.default_extractor()`, and the guard is reached through
    `registry.guarded()` — a function. Nothing here spells a concrete backend,
    so `tests/test_adapter_contract.py`'s AST count of selection sites outside
    the package stays `{}`. A parameter typed `TypedTextExtractor`, or a
    default of `TypedTextExtractor()`, would break the exit; this does not, and
    the test says so rather than this docstring.

    EVERY backend is guarded, including the default. Not only injected ones:
    "the one we ship cannot fail" is an assumption, and the cost of being wrong
    about it is a 503 that blames the application for a supplier's outage.

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
    actual = type(client).__name__
    if identity.backend != actual:
        raise ValueError(
            f"{REFUSAL}: no operation performed. The identity says the backend "
            f"is {identity.backend!r} but the client is a {actual}. The page "
            f"and the action log would name different backends, and nothing "
            f"downstream could tell which one was written to."
        )

    if store is None:
        # NOT `MemoryStore(":memory:")`, which is what stood here until
        # 2026-08-10 and is the whole of defect "the audit log is lost on
        # restart". Every decision this app makes about somebody's books went
        # into a database that existed only while the process did, so the
        # durable, append-only, never-updated audit trail was durable for as
        # long as nobody closed the window.
        #
        # The fix is not a different default. A default that silently loses
        # data and a default that silently writes a file are both this function
        # deciding something the caller should have said, so it says neither:
        # `connect()` opens `default_store()` and hands it in, and a test that
        # wants a throwaway passes `MemoryStore(IN_MEMORY)` and can be seen
        # doing it.
        raise ValueError(
            f"{REFUSAL}: no operation performed. configure() needs a store. "
            f"Pass MemoryStore({IN_MEMORY!r}) for a throwaway one, or call "
            f"connect(), which opens the durable database this product keeps "
            f"its audit trail in."
        )
    owned = store
    # `identity.company` and NOT the module default. This is the one place the
    # company enters the runtime, so it is the one place it can be got wrong.
    built = Runtime(
        client=client,
        identity=identity,
        memory=bootstrap(client, identity.company, owned),
        store=owned,
        extractor=guarded(default_extractor() if extractor is None else extractor),
    )

    # No second company check here on purpose. Memory is built from
    # `identity.company` on the line above, so the two cannot disagree at this
    # instant - a check here would be a branch no input can take, and an
    # untestable guard is a guard nobody can prove still works. The check lives
    # in `runtime()`, at the point of USE, which covers this route and every
    # route that does not exist yet. `serve()` calls `runtime()` once before it
    # binds a socket, so a startup that cannot work still refuses in the
    # terminal rather than on the first page.
    _recorded_mismatches.clear()
    return install(built)


def default_store_path() -> Path:
    """Where the audit trail lives. `ACCOUNTANT_DB`, or `data/app.db`.

    A FILE, and a relative one by default, so a person who runs this on their
    own laptop gets a database beside the app rather than in a temporary
    directory they will never find. The environment variable exists because a
    deployment puts it on a mounted volume, and a path that cannot be moved is
    a path that ends up on a container's disposable filesystem.
    """
    named = os.environ.get(ENV_DB, "").strip()
    return Path(named) if named else Path("data") / "app.db"


def default_store() -> MemoryStore:
    """Open the durable database, making its directory if it is not there.

    `mkdir` rather than a refusal: the missing directory is the FIRST run, not
    a misconfiguration, and refusing to start because a folder does not exist
    yet would be a product that cannot be installed by running it.
    """
    path = default_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return MemoryStore(path)


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

    The store defaults to the DURABLE one, and this is the only place that
    default lives. `configure()` refuses a missing store rather than guessing,
    so there is exactly one site that decides where the audit trail goes.
    """
    client, identity = real_tally(config, company, backups=backups)
    return configure(
        client, identity, store=store if store is not None else default_store()
    )


def disconnect() -> None:
    """Drop the runtime. Tests use this to prove the app fails closed."""
    global _runtime_state
    _runtime_state = None
    # The next runtime is a different one, so a mismatch it happens to word
    # identically is a new fact and must be recorded again.
    _recorded_mismatches.clear()


def record(draft: pipeline.Draft, action: str) -> None:
    """A draft-shaped decision, through the same function `pipeline.run` uses.

    Not a second implementation of logging. The web app reaches its decisions
    across several HTTP requests rather than in one `run` call, so it cannot
    use `run`, but it must produce identical rows - same fields, same reason,
    same backend. Calling the pipeline's own recorder is what guarantees that
    rather than promising it.
    """
    live = runtime()
    who = current_principal()
    pipeline.record_decision(
        live.store,
        draft,
        live.memory,
        live.client,
        action,
        live.identity.run_id,
        tenant_id=who.tenant_id if who else NOT_RECORDED,
        user_id=who.user_id if who else NOT_RECORDED,
    )
    # THE JOIN BETWEEN THE LOG AND THE AUDIT TRAIL, and the reason the request
    # id is NOT a column on `action_log`. The durable row already carries the
    # operation id; naming it here puts the same key on the log line, so a
    # person holding a voucher can find every line of every request that made
    # it. The join goes from the durable record OUT to the log, which is the
    # direction that keeps working after the log has been rotated away.
    _note_in_log(
        action,
        draft.operation_id,
        draft.decision.outcome.value if draft.decision else NOT_RECORDED,
    )


def _note_in_log(action: str, operation_id: str, outcome: str) -> None:
    """One log line for one durable row, carrying the key that joins them.

    NOT THE REASON, and not any other prose. A reason is written for a person
    reading the audit trail and can be several sentences of somebody's business
    detail; the log gets the identifiers, and whoever needs the words reads the
    row the identifiers point at.

    WHY THE REQUEST ID IS NOT A COLUMN ON `action_log`, decided 2026-08-11.

    `MemoryStore._migrate` would take it: it is additive-only, every existing
    row would be left NULL, and NULL reads back as `NOT_RECORDED`. So the
    question is not whether the migration is possible, it is whether the column
    should exist, and the answer is no for three reasons.

        it is a key into something that expires   a request id identifies a
            line in a log file, and log files rotate. Six months later the
            column names a line that no longer exists anywhere: a foreign key
            to nothing, in a statutory record that cannot be edited.
        the durable joins already exist           `run_id` says which process
            and `operation_id` says which entry, and both are already on every
            row and both outlive any log.
        the join is needed in the other direction  the question asked is "given
            this voucher, what happened", not "given this log line, which row".
            That direction works from the row's own operation id, which is what
            this function puts on the line.

    An append-only record that a person may be asked to defend in front of a
    tax officer is the last place to add a column on a hunch.
    """
    observability.log(
        AUDIT_ROW_EVENT,
        action=action,
        operation=operation_id or NOT_RECORDED,
        outcome=outcome or NOT_RECORDED,
    )


def note(action: str, outcome: str, reason: str, **fields: str) -> None:
    """An action of the app's own that is not a decision about a draft.

    Reversal, handover and retype happen at the screen, not in the pipeline, so
    the pipeline has nothing to say about them. They are still things that were
    done to somebody's books or on their behalf, and `reason` is required here
    for the same reason it is required everywhere else.
    """
    live = runtime()
    who = current_principal()
    live.store.record_action(
        ActionLog(
            ts=datetime.datetime.now(datetime.UTC),
            action=action,
            company_key=live.memory.identity.key,
            outcome=outcome,
            reason=reason,
            run_id=live.identity.run_id,
            backend=type(live.client).__name__,
            tenant_id=who.tenant_id if who else NOT_RECORDED,
            user_id=who.user_id if who else NOT_RECORDED,
            **fields,
        )
    )
    # The same join as `record()`. `fields` may carry an `operation_id` - the
    # reversal, handover, retype and dismissal paths all pass one - so it is
    # used when it is there and honestly reported absent when it is not.
    _note_in_log(action, fields.get("operation_id", ""), outcome)


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
input[type=text],input[type=password]{flex:1;padding:11px 13px;font:inherit;
border-radius:8px;border:1px solid #8884}
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


#: The cookie a browser carries its session in.
COOKIE = "accountant_session"


def render_login(banner: str = "") -> bytes:
    """The sign-in page. The only page a request without a session may see.

    No "forgot password" link, because there is no path behind it: sending mail
    needs a provider, an account and a domain, none of which exist. A dead link
    would be worse than its absence. `docs/OWNER_WORK.md` carries it as owner
    work rather than this page carrying a promise.
    """
    return page(
        banner
        + "<h1>Accountant Dad</h1>"
        + "<p class=sub>Sign in to your company's books.</p>"
        + '<form method=post action="/login">'
        + '<p><input type=text name=email placeholder="you@company.com" '
        + "autocomplete=username></p>"
        + '<p><input type=password name=password placeholder="password" '
        + "autocomplete=current-password></p>"
        + "<p><button class=primary type=submit>Sign in</button></p>"
        + "</form>"
    )


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
    """The frame every screen sits in, including the 503.

    Reads `_runtime_state` directly rather than calling `runtime()`, because
    the refusal page is drawn BY the refusal and must not be able to raise a
    second one. When nothing is connected there is no company to name, and it
    says so instead of printing the configuration default at somebody who is
    not attached to those books.
    """
    live = _runtime_state
    who = esc(live.company) if live is not None else "no company &mdash; not connected"
    return f"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Accountant Dad</title><style>{CSS}</style>
<h1>Accountant Dad</h1>
<p class=sub>{who} &middot; posting into Tally</p>
{backend_notice()}
{body}""".encode()


def decision_rule(d: pipeline.Draft) -> str:
    """Which detector or which rule produced THIS decision. Never a guess.

    Read off the draft in the order the decision order itself reads them:

        the question being asked  `Decision.question_problem_id`, the id the
                                  page renders into the form and the id
                                  `pipeline.answer` reads to pick a ledger leg.
                                  If the entry is asking something, that id is
                                  the thing that decided it was unclear.
        the problems found        every distinct thing wrong with the entry,
                                  each with a stable id. A NOT_VALID entry is
                                  decided by these and by nothing else.
        the decision order        an entry with no problems at all was decided
                                  by `accountant/decide.py`, and saying so is
                                  more honest than a blank cell.

    A problem id IS either a detector name (`vendor_switch`, `magnitude`,
    `first_use`, `gst_anomaly` — `problems._from_flag` uses `Flag.detector` as
    the id) or a check name (`accounts_exist`, `funding_is_named`,
    `tax_lines_can_be_posted`). Which of the two it is, is decided by looking at
    the flags THIS DRAFT actually carries rather than by importing a detector
    list — `accountant/detect/` is being changed in a parallel PR, and a
    rendering path that breaks because a detector was renamed elsewhere is a
    coupling this screen does not need.
    """
    decision = d.decision
    if decision is None:
        return ""
    driver = decision.question_problem_id or "; ".join(p.id for p in d.problems)
    return driver or DECISION_ORDER_RULE


def decision_rule_kind(d: pipeline.Draft) -> str:
    """`detector` or `rule`, for the name `decision_rule` returned."""
    fired = {f.detector for f in (*d.flags, *d.suppressed_flags)}
    return "detector" if decision_rule(d) in fired else "rule"


def decision_citations(d: pipeline.Draft) -> list[object]:
    """Every citation the decision or its flags carry, in the order shown.

    `getattr` rather than an attribute access because `schema.Decision` and
    `schema.Flag` genuinely do not have this field: the corpus is merged, the
    tax engine that builds `Citation`s is merged, and `pipeline.evaluate` does
    not call it. A hard reference would not import, and inventing the field in
    `accountant/schema.py` from a rendering module would be the web layer
    deciding what a decision is.

    Both shapes are read. `accountant.tax.decision.TaxDecision` carries
    `citations`, one per rule that produced the answer; a carrier naming a
    single rule may carry `citation`. Neither is required to exist.
    """
    found: list[object] = []
    for carrier in (d.decision, *d.flags, *d.suppressed_flags):
        if carrier is None:
            continue
        many = getattr(carrier, "citations", ()) or ()
        found.extend(many)
        one = getattr(carrier, "citation", None)
        if one is not None:
            found.append(one)
    return found


def cited_source(rule: RateRule) -> str:
    """One rule's source, as the corpus holds it: the URL and the date read.

    Both, in one cell, because the four slots are the owner-approved four and a
    URL with no retrieval date does not say WHEN the rate was read — which is
    the difference between a citation and a link. `RuleCorpus.build` rejects any
    rule missing either, so a rule that got this far has both.
    """
    return f"{rule.source.url} · retrieved {rule.source.retrieval_date}"


def uncorroborated_source(rule_id: str, why: str) -> str:
    """A citation this screen refuses to turn into a URL, and the reason why.

    The rule id is quoted back so the reader can see WHICH citation was refused;
    it is escaped at the renderer like every other value here.
    """
    return f"{NOT_AVAILABLE} — {why}: {rule_id or '(unnamed rule)'}"


def rule_source_url(d: pipeline.Draft) -> str:
    """The official source behind the rule that decided this entry.

    Q1 fixes the authority: a production rule stands on an official CBIC or
    Income Tax Department notification. Those live in `accountant/rules/`, which
    IS merged, so this reads them — `RULES_BY_ID`, built once at import.

    THE URL IS NOT READ OFF THE DECISION. A citation names a `rule_id` and
    carries a URL it claims belongs to that rule; this takes the id, asks the
    corpus, and renders what the CORPUS holds. Three outcomes, and the two
    refusals matter more than the success:

        the id is in the corpus       render the corpus's URL and retrieval date
        the id is not in the corpus   render a marker naming the id. The claimed
                                      URL is dropped: there is nothing here to
                                      check it against, and a provenance panel
                                      that repeats an unverifiable citation is
                                      the exact failure it exists to catch
        the two URLs disagree         the same fact recorded twice, disagreeing.
                                      Reported, never resolved by preferring one

    Nothing on this screen cites a rule today, so every decision falls through
    to `RULE_URL_NOT_CITED` — which says that, rather than saying the corpus is
    missing. The gap is the wiring between `pipeline.evaluate` and
    `accountant.tax.decision`, and naming the real gap is the whole point.
    """
    for cite in decision_citations(d):
        rule_id = str(getattr(cite, "rule_id", "") or "").strip()
        rule = RULES_BY_ID.get(rule_id)
        if rule is None:
            return uncorroborated_source(rule_id, NOT_IN_CORPUS)
        claimed = str(getattr(cite, "source_url", "") or "").strip()
        if claimed and claimed != rule.source.url:
            return uncorroborated_source(rule_id, URL_DISAGREES)
        return cited_source(rule)
    return RULE_URL_NOT_CITED


def decision_evidence(d: pipeline.Draft) -> list[str]:
    """The facts this decision rested on, each naming where it came from.

    An empty list means this draft records none, and the screen says
    `NOT_RECORDED` rather than drawing an empty cell. That is reachable — a
    draft with no checks, no flags and no conflict has no evidence — and it is
    pinned by a test rather than assumed unreachable.

    The suppressed flags are in here on purpose. `FLAG_CAP` is a DISPLAY
    decision; the owner's rule when setting it was "never lose concerns from the
    audit/evidence record". A concern the cap kept off the top of the screen is
    still evidence for the decision it contributed to, and this is the evidence
    record.
    """
    out: list[str] = []
    failed = [c for c in d.checks if not c.passed]
    if d.checks:
        out.append(f"{len(d.checks)} checks run, {len(failed)} failed")
    out += [f"check {c.name}: {c.detail}" for c in failed]
    out += [f"detector {f.detector}: {f.reason}" for f in d.flags]
    out += [
        f"detector {f.detector}: {f.reason} (over the display cap)"
        for f in d.suppressed_flags
    ]
    conflict = d.memory_conflict
    if conflict is not None:
        live = ", ".join(
            f"{account} {times} time(s)"
            for account, times in zip(
                conflict.live_accounts, conflict.live_times, strict=True
            )
        )
        out.append(
            f"memory and the live ledger disagree about {conflict.subject}: "
            f"memory says {conflict.remembered_account} "
            f"{conflict.remembered_times} time(s), the ledger says {live}"
        )
    return out


def provenance_sources(d: pipeline.Draft) -> dict[str, str]:
    """The raw text for every provenance slot, before any rendering.

    Separate from the rendering so the two can be checked against each other.
    A key computed here that `PROVENANCE_SLOTS` does not name is computed and
    thrown away — which is precisely what happened to `dropped_flags` — so
    `tests/test_ui_provenance.py` asserts these keys and that tuple are the same
    four names.
    """
    return {
        "detector_or_rule": decision_rule(d),
        "source_url": rule_source_url(d),
        "evidence": " · ".join(decision_evidence(d)),
        "explanation": d.decision.reason if d.decision is not None else "",
    }


def provenance_slots(d: pipeline.Draft) -> dict[str, tuple[str, str]]:
    """Every slot as `(state, text)`. Total over `PROVENANCE_SLOTS`, never blank.

    The default in the lookup is what makes it total: a slot named in
    `PROVENANCE_SLOTS` that nothing computes renders `NOT_RECORDED` rather than
    raising while a page is being drawn, and a page that cannot be drawn is how
    the NOT_VALID screen once became the one screen the app could not show.

    The NOT_AVAILABLE state is decided on the `NOT_AVAILABLE` prefix rather than
    on one fixed sentence. There are three of those sentences now and the reason
    varies with what was wrong; a state that had to equal a named constant would
    silently downgrade a new refusal to `recorded` — a marker rendered as though
    it were a value, which is the one thing this table must never do.
    """
    sources = provenance_sources(d)
    out: dict[str, tuple[str, str]] = {}
    for slot in PROVENANCE_SLOTS:
        text = sources.get(slot, "").strip()
        if text.startswith(NOT_AVAILABLE):
            out[slot] = (SLOT_NOT_AVAILABLE, text)
        elif text:
            out[slot] = (SLOT_RECORDED, text)
        else:
            out[slot] = (SLOT_NOT_RECORDED, NOT_RECORDED)
    return out


def render_provenance(d: pipeline.Draft) -> str:
    """Where this decision came from, one row per slot, nothing left blank.

    WHY IT SITS BELOW THE QUESTION AND NOT BESIDE IT. S7: no question string may
    contain a name from the chart of accounts, and this block legitimately
    carries account names — a `vendor_switch` reason names the account a vendor
    usually goes to, and that is the evidence. So it is rendered in the audit
    region at the bottom of the card, after both tables and well clear of
    `<p class=ask>`. The account is shown AFTER answering, never inside the
    question, and that ordering is what keeps holding here.

    EVERYTHING IS ESCAPED. A vendor name is untrusted input that lands in a
    page, and it reaches three of these four slots — through a flag's reason,
    through a problem's detail, and through the decision's own reason.
    """
    values = provenance_slots(d)
    rows = "".join(
        f'<tr data-provenance-slot="{esc(slot)}" data-slot-state="{esc(state)}">'
        f"<td>{esc(PROVENANCE_LABELS.get(slot, slot))}</td>"
        f"<td>{esc(text)}</td></tr>"
        for slot, (state, text) in values.items()
    )
    return (
        f'<div data-provenance="decision" '
        f'data-provenance-rule-kind="{esc(decision_rule_kind(d))}" '
        f'data-provenance-slots="{len(values)}">'
        f"<h2>Why this decision, and where it came from</h2>"
        f"<table>{rows}</table></div>"
    )


def render_tax(d: pipeline.Draft) -> str:
    """What the GST engine made of this bill, or "" when it carries no tax.

    ADDED 2026-08-10 with Task 10. `accountant/rules/` and `accountant/tax/`
    were built, tested and merged, and then called by nothing on the live path:
    a corpus that is never evaluated is a corpus nobody can be wrong about.

    IT SAYS WHAT IT IS. A tax verdict here is EVIDENCE and never permission -
    the badge above it still reads "not posted", `checks.tax_lines_can_be_posted`
    still fails the entry, and the connector still refuses it at the wire. Owner
    decision Q3 = D is untouched. The sentence a person was left holding was
    "Accountant Dad cannot post a tax line yet"; this adds why the tax could not
    be worked out either, which is the part they can actually act on.

    `data-tax-outcome` carries the verdict so a test matches an attribute rather
    than a word the stylesheet already contains. Two tests in this repository
    were green and vacuous for exactly that reason.
    """
    if d.tax is None:
        return ""

    # The rule id is resolved through RULES_BY_ID and the CORPUS's own URL is
    # what renders, exactly as `render_provenance` already does. A citation is a
    # CLAIM: it names a rule and carries a URL saying that rule is where it came
    # from. Repeating the claim would let a bad citation put any address it
    # liked on the one panel whose whole job is to be trusted about sources.
    cited = "".join(
        f"<li class=ev>{esc(c.rule_id)} — "
        f"{esc(cited_source(RULES_BY_ID[c.rule_id]))}</li>"
        if c.rule_id in RULES_BY_ID
        else f"<li class=ev>{esc(c.rule_id)} — "
        f"{esc(uncorroborated_source(c.rule_id, 'not in the loaded corpus'))}</li>"
        for c in d.tax.citations
    )
    total = d.tax.total_tax_paise
    computed = (
        f"<tr><td>Tax the rules give</td><td>{esc(money(total))}</td></tr>"
        if total is not None
        else ""
    )
    return (
        f'<h2>GST rules</h2><div data-tax-outcome="{esc(d.tax.outcome.value)}">'
        f"<p class=reason>{esc(d.tax.reason)}</p>"
        f"<table>{computed}</table>"
        + (f"<ul>{cited}</ul>" if cited else "")
        + "<p class=hint>This is what the rule corpus says. It is not permission "
        "to post: Accountant Dad still cannot write a tax line, so this entry "
        "is yours to make in Tally.</p></div>"
    )


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

    tax = render_tax(d)

    # G6.1 and G6.3. Each flag shows its detector, its evidence, and — until it
    # has been dismissed — a way to dismiss it. Dismissing changes nothing about
    # the entry; it records that a person looked.
    flags = "".join(
        # `data-detector` and `data-dismissed` are here for the same reason
        # `data-outcome` is on the log rows: two tests written earlier were
        # green and vacuous because they searched a whole page for a common
        # word the stylesheet already contained. `"vendor_switch" in page` is
        # also true of the hidden form input below it, so a render that dropped
        # the flag and kept the button would pass. An attribute cannot be
        # matched by accident.
        f'<p class=reason data-detector="{esc(f.detector)}" '
        f'data-dismissed="{"true" if f.detector in d.dismissed else "false"}">'
        f"&#9873; <b>{esc(f.detector)}</b> — {esc(f.reason)}"
        + (
            " <b>dismissed</b></p>"
            if f.detector in d.dismissed
            else "</p>"
            f'<form method=post action=/dismiss style="display:inline">'
            f'<input type=hidden name=draft value="{esc(d.id)}">'
            f'<input type=hidden name=detector value="{esc(f.detector)}">'
            f"<button>Dismiss this</button></form>"
        )
        for f in d.flags
    )

    # G6.2. `dropped_flags` was computed and thrown away here. "Overflow is
    # reported as a count, never silently dropped" was true inside the Draft and
    # false on the page. Nothing is rendered at zero: "0 more concerns are not
    # shown" is noise on every clean entry.
    # The count is exactly total - displayed, and `data-overflow` carries it so
    # a test matches the number rather than the sentence. "concern(s)" was the
    # old wording and it is the kind of detail that tells a customer nobody read
    # this screen, so one and many are spelled properly.
    overflow = (
        f'<p class="reason muted" data-overflow="{d.dropped_flags}">'
        f"{d.dropped_flags} more "
        f"{'concern' if d.dropped_flags == 1 else 'concerns'}</p>"
        if d.dropped_flags
        else ""
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
{flags}{overflow}{ask}{posted}
<h2>Voucher</h2><table>{rows}</table>
<h2>Where each field came from</h2><table>{prov}</table>
{tax}
{render_provenance(d)}
{checks_line}
</div>"""


def render_bulk_preview(batch: reversal.Batch) -> bytes:
    """What would be undone, named one voucher at a time, before anything is.

    The page states the count and every operation id, because "undo everything"
    is the one action in this app whose blast radius the person cannot see from
    the button. Confirming is a second request carrying the batch id, so the
    list that gets reversed is the list that was shown and not whatever is in
    the books a minute later.
    """
    rows = "".join(
        f"<tr><td><code>{esc(o.operation_id)}</code></td></tr>" for o in batch.outcomes
    )
    return page(f"""<div class=warn>
<b>Undo {len(batch.outcomes)} voucher(s)?</b> Nothing has been reversed yet.
This removes only entries Accountant Dad wrote. Anything typed by hand in Tally
is left exactly as it is.</div>
<h2>These would be undone</h2>
<table>{rows}</table>
<form method=post action=/reverse-all>
<input type=hidden name=batch value="{esc(batch.batch_id)}">
<input type=hidden name=confirm value="yes">
<button class=primary>Yes, undo these {len(batch.outcomes)}</button></form>
<p><a href="/">&larr; no, leave them alone</a></p>""")


def render_bulk_result(batch: reversal.Batch) -> bytes:
    """What actually happened, per voucher, in the state machine's own words.

    The states are printed verbatim rather than translated into a friendlier
    sentence. `partial_failure` and `unknown_outcome` mean different things and
    demand different next steps, and a screen that renders both as "some
    entries could not be undone" is the same defect as a boolean reversal: it
    throws away the distinction the person needs.
    """
    cls = "valid" if batch.state is reversal.BatchState.COMPLETED else "notvalid"
    rows = "".join(
        f"<tr><td><code>{esc(o.operation_id)}</code></td>"
        f"<td>{esc(o.state.value)}</td><td>{esc(o.detail)}</td></tr>"
        for o in batch.outcomes
    )
    return page(f"""<div class="card {cls}">
<span class="badge b-{cls}">{esc(batch.state.value)}</span>
<p class=reason>{esc(batch.detail)}</p>
<p class=reason class=muted>every paise accounted for: {batch.accounted}</p>
</div>
<h2>Each voucher</h2>
<table><tr><th>Operation<th>Result<th>Detail</tr>{rows}</table>
<p><a href="/">&larr; back</a></p>""")


#: What a deletion does NOT touch, said on the screen before it happens and
#: again after. Written once and rendered twice, so the promise a person is
#: shown before pressing the button is the same sentence they are shown after.
#:
#: Every clause is a fact about this codebase rather than a reassurance:
#: `ARCHITECTURE.md` §2 — we never store the customer's books; `store.py` —
#: `action_log` has no update path and no delete path; `docs/DATA_POLICY.md`
#: §3.6 — what "deleted" means when a backup exists is D-17 and is not answered,
#: so this page does not claim it.
DELETION_KEEPS = (
    "<h2>What this does not touch</h2>"
    "<ul>"
    "<li><b>Your books in Tally are untouched.</b> Every voucher stays exactly "
    "where it is. We never held a copy of your books to delete.</li>"
    "<li><b>The record of what we did to your books is kept.</b> Who typed "
    "what, what we posted and why, the supplier names and the amounts — that "
    "is your evidence and an auditor's, and deleting it would destroy the one "
    "thing that can answer what happened to your accounts. It is marked from "
    "now on as belonging to a closed account.</li>"
    "<li><b>We cannot promise anything about backups yet.</b> Nobody has "
    "decided whether backups exist, where they are or how long they are kept "
    "(decision D-17), so this page will not tell you a copy is gone from a "
    "place nobody has described.</li>"
    "</ul>"
)


def render_deletion_preview(plan: DeletionPlan) -> bytes:
    """What would be destroyed, counted, before anything is.

    Two requests for the same reason `/reverse-all` is two: the first measures
    and writes nothing, the second executes the plan that was shown. A
    single-request version would have to measure at confirmation time, which
    means deleting a set the person was never shown.

    The counts are rendered as `data-` attributes as well as words, so a test
    matches the NUMBER rather than the prose. Two tests in this project were
    green and vacuous because they searched a page for a common word the
    stylesheet already contained.
    """
    companies = (
        "".join(
            f"<tr><td><code>{esc(key)}</code></td></tr>"
            for key in plan.companies_erased
        )
        or "<tr><td class=muted>nothing learned about any company yet</td></tr>"
    )
    shared = (
        "<p class=reason>"
        f"{len(plan.companies_kept)} company/companies are left alone because "
        "another customer is also recorded as working in them: "
        + ", ".join(f"<code>{esc(key)}</code>" for key in plan.companies_kept)
        + "</p>"
        if plan.companies_kept
        else ""
    )
    return page(f"""<div class=warn data-deletion="preview"
 data-companies="{len(plan.companies_erased)}"
 data-users="{plan.users}" data-sessions="{plan.sessions}"
 data-actions-kept="{plan.actions_kept}">
<b>Delete everything we have learned about {esc(plan.tenant_name)}?</b>
Nothing has been deleted yet. This closes {plan.users} sign-in(s), ends
{plan.sessions} signed-in session(s) including this one, and erases what we
learned about {len(plan.companies_erased)} company/companies. It cannot be
undone.</div>
<h2>What we would erase</h2>
<table>{companies}</table>
{shared}
{DELETION_KEEPS}
<form method=post action=/delete-my-data>
<input type=hidden name=plan value="{esc(plan.plan_id)}">
<input type=hidden name=confirm value="yes">
<button class=primary>Yes, delete my data</button></form>
<p><a href="/">&larr; no, leave it alone</a></p>""")


def render_deletion_result(done: TenantDeletion) -> bytes:
    """What actually happened, counted, in the store's own words.

    `done.summary()` is printed verbatim — the same sentence that went into the
    audit row. Two wordings of one event is how a screen and a log end up
    disagreeing, and the one nobody is watching is always the one that stays
    wrong.
    """
    erased = (
        "".join(
            f"<tr><td><code>{esc(key)}</code></td></tr>"
            for key in done.companies_erased
        )
        or "<tr><td class=muted>there was nothing learned to erase</td></tr>"
    )
    return page(f"""<div class="card valid" data-deletion="done"
 data-companies="{len(done.companies_erased)}"
 data-users="{done.users_closed}" data-sessions="{done.sessions_revoked}"
 data-actions-kept="{done.actions_kept}">
<span class="badge b-valid">deleted</span>
<p class=reason>{esc(done.summary())}</p>
<p class=reason>You have been signed out. This account cannot be signed in to
again.</p>
</div>
<h2>What we erased</h2>
<table>{erased}</table>
{DELETION_KEEPS}""")


def render_home(banner: str = "") -> bytes:
    live = runtime()
    # ONE company, read once, used for all three reads below. Two calls to
    # `runtime()` used to sit here and both passed the module constant.
    company = live.company
    with observability.tally_call("list_our_vouchers"):
        ours = live.client.list_our_vouchers(company)
    with observability.tally_call("trial_balance"):
        tb = live.client.trial_balance(company)

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

    # #14.7, second half. Offered only when there is something to undo: an
    # undo-everything button over an empty list is an invitation to a mistake
    # and cannot be anything else.
    undo_all = (
        "<form method=post action=/reverse-all>"
        f"<button>Undo everything we posted ({len(ours)})</button></form>"
        if ours
        else ""
    )

    # Read off the persisted log, newest first. The old renderer iterated
    # `for _, m in EVENTS`, which threw the outcome away at the last moment -
    # the page could tell you a sentence had happened but not whether the
    # entry was posted, refused or merely asked about.
    rows = live.store.actions(company)[-SHOWN:]
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
{undo_all}

<h2>Trial balance</h2>
<table>{tb_rows}</table>

<h2>Activity</h2><section id=log>{events}</section>

<h2>Your data</h2>
<form method=post action=/delete-my-data>
<button>Delete everything we have learned about you</button></form>
<p class=hint>A right you can use, so it is a button and not an email address.
Pressing it shows you exactly what would go, counted, and deletes nothing until
you say yes on that page.</p>""")


# ---- server -----------------------------------------------------------------


def _run(text: str) -> pipeline.Draft:
    """One typed entry, from text to a decision.

    `live.extractor`, NOT `default_extractor()`. This line used to build a
    backend of its own on every request, which had two costs. The visible one:
    no caller could put the running app on a different backend, so a reader
    outage was unreachable over HTTP and was recorded as environment-limited
    for two days. The quieter one: the route decided what read the bill, so
    "which backend is this deployment on" had two answers — the one
    `configure()` was given and the one the handler made.
    """
    live = runtime()
    company = live.company
    with observability.tally_call("read_accounts"):
        accounts = live.client.read_accounts(company)
    with observability.tally_call("read_vouchers"):
        history = live.client.read_vouchers(company)
    d = pipeline.build_draft(
        company,
        text.encode(),
        "text/plain",
        live.extractor,
        live.memory,
    )
    # THE EARLIEST MOMENT THIS REQUEST CAN KNOW WHICH ENTRY IT IS ABOUT, and
    # therefore where it is set. Sited at the END of this function instead -
    # which is where it went first - the write, the decision row and the
    # posting all logged `entry=NOT_RECORDED`, and the one request that
    # actually touched somebody's books was the one line nothing could join.
    # The two reads above still cannot carry it: the draft did not exist yet,
    # and saying so is better than guessing.
    observability.set_entry_id(d.id)
    d = pipeline.evaluate(d, accounts, history, live.memory, flag_cap=FLAG_CAP)
    if d.outcome is Outcome.VALID:
        # `post` is three Tally round trips - write, read back by marker, read
        # the register - so it is timed as one named call rather than left out
        # of the split. Leaving it out would put the slowest thing this app
        # does into `app_ms` and blame us for Tally being slow.
        with observability.tally_call("post_voucher"):
            d = pipeline.post(
                d,
                live.client,
                log=live.store,
                memory=live.memory,
                run_id=live.identity.run_id,
            )
    record(d, ACTION_FOR[d.outcome])
    remember_draft(d)
    return d


#: The action a refused replay writes. Its own name rather than `failed`,
#: because a replay is not a failure of ours: the system worked and said no.
#:
#: Imported from `accountant/observability.py` rather than spelled here as well.
#: The metric reads this word and the handler writes it; two literals is how a
#: counter reads zero for ever while the thing it counts keeps happening.
REFUSED_REPLAY = observability.REFUSED_REPLAY


class Handler(BaseHTTPRequestHandler):
    #: The status this request answered with, for the one log line at the end.
    #:
    #: A class attribute so it exists before `handle_one_request` sets it: a
    #: request whose line could not even be parsed never reaches the assignment
    #: below, and 0 is the honest reading of "we never answered".
    _status: int = 0

    def send_response(self, code: int, message: str | None = None) -> None:
        """Answer, and remember what we answered.

        Overridden HERE rather than in `_send`, because `send_error` and
        `_send_with_session` both answer without going through `_send`. A
        status recorded in only one of the three places would make the log say
        200 for a refusal, which is the exact opposite of what a log is for.
        """
        self._status = code
        super().send_response(code, message)

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

    def _over_tls(self) -> bool:
        """Is THIS connection encrypted? Measured off the socket, not a flag.

        Task 7, 2026-08-11. `ssl.SSLSocket` is what `ssl.SSLContext.wrap_socket`
        returns and nothing else produces one, so this answers the question the
        `Secure` attribute is actually about — "was this byte stream
        encrypted" — rather than the question a configuration flag answers,
        which is "did somebody intend it to be".

        The two can disagree. A module-level "TLS is on" boolean set at startup
        stays true after the setting that produced it has been changed, and a
        cookie that claims a protection the connection does not have is worse
        than no claim: the browser then refuses to send it and the person
        cannot log in, with nothing on screen saying why.

        KNOWN BOUNDARY, and it is in `docs/TLS.md`: behind a TLS-terminating
        reverse proxy this connection really is plain HTTP, so `Secure` is
        correctly omitted here even though the browser spoke HTTPS. That
        deployment does not exist yet — no host, no domain, no proxy; see
        `docs/OWNER_WORK.md` — and inventing a trusted `X-Forwarded-Proto`
        reader for it would add a header anybody can forge.
        """
        return isinstance(self.connection, ssl.SSLSocket)

    def _send_with_session(
        self, body: bytes, token: str, *, clear: bool = False
    ) -> None:
        """Answer, and set or clear the session cookie.

        HttpOnly so page scripts cannot read it, SameSite=Lax so another site
        cannot make the browser use it, Path=/ so it covers every route.

        `Secure` SINCE TASK 7, 2026-08-11, and only when this connection is
        actually encrypted. The flag makes a browser withhold the cookie over
        plain HTTP, so setting it unconditionally would break the loopback
        development server — which is why it was absent until TLS existed. It
        is not set unconditionally now either: `_over_tls` measures the socket,
        so the attribute appears exactly when it is true and never as a promise.

        `docs/TLS.md` has the cost of getting it wrong in either direction.
        """
        attrs = "; HttpOnly; SameSite=Lax; Path=/"
        if self._over_tls():
            attrs += "; Secure"
        cookie = (
            f"{COOKIE}=; Max-Age=0{attrs}"
            if clear
            else f"{COOKIE}={urllib.parse.quote(token)}; "
            f"Max-Age={SESSION_HOURS * 3600}{attrs}"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", cookie)
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

        THE SECOND HALF, added 2026-08-09 after an audit found the same defect
        one branch away. Only the refusal was caught, so ANY other failure —
        a detector raising, a parser giving up, an unexpected shape from Tally
        — escaped exactly the way the refusal used to: traceback in the log,
        dropped socket at the browser, and a person who cannot tell a broken
        app from an unreachable Tally. The paragraph above was true and was
        being applied to one exception out of all of them.

        So everything is caught now, and the two cases are kept apart because
        they mean different things:

            the refusal        we are not connected; the sentence IS the answer
            anything else      something in us broke; the person is told that,
                               and the detail goes to the durable log where
                               whoever fixes it will look

        The page never carries the exception text. A stack message on a screen
        a customer sees is a different failure, and `note()` already has a
        field for it.

        `BaseException` is deliberately NOT caught. A KeyboardInterrupt or a
        SystemExit is somebody stopping the process, and answering it with a
        tidy 503 would fight them.

        THE CORRELATION ID AND THE CLOCK START HERE, 2026-08-11, and this is
        the only place they could. One entry is several requests, and until now
        nothing tied them together because nothing was logged at all — the
        product's whole observable output was what `serve()` printed at
        startup.

        `begin_request` runs BEFORE `super()`, so every line written while the
        request is being served already carries the id, including the ones
        written by the two refusal paths below. The `finally` writes the one
        line per request, so a request that raised is still timed: a request
        that failed slowly and a request that failed instantly are different
        problems and the duration is what tells them apart.
        """
        observability.begin_request(observability.new_request_id())
        self._status = 0
        started = time.perf_counter()
        try:
            super().handle_one_request()
        except DuplicateOperation as exc:
            # 409 Conflict, and NOT the 503 "something broke" page. Nothing
            # broke: the person asked to post an entry whose identity has
            # already been used, the system refused, and it wrote nothing.
            # Answering that with "Accountant Dad broke" would send somebody
            # looking for a fault, and the honest answer is that this is a state
            # the request conflicts with - which is what 409 means.
            #
            # Recorded, because a refusal nobody can find afterwards cannot be
            # investigated - and this one is worth finding: it means somebody
            # tried to post an entry twice. Best-effort like `_broke`, since a
            # logging failure must not replace the answer the person is waiting
            # for.
            with contextlib.suppress(Exception):
                note(REFUSED_REPLAY, "REFUSED", str(exc))
            self._send(
                page(
                    f"<div class=warn><b>{esc(exc)}</b></div>"
                    '<p><a href="/">&larr; back</a></p>'
                ),
                code=409,
            )
        except RuntimeError as exc:
            if str(exc).startswith(REFUSAL):
                self._send(page(f"<div class=warn><b>{esc(exc)}</b></div>"), code=503)
                return
            self._broke(exc)
        except Exception as exc:
            self._broke(exc)
        finally:
            observability.finish_request(
                # `getattr`, because a malformed request line means neither
                # attribute was ever set and the log line must still be
                # written — an unparseable request is precisely the one
                # somebody will come looking for.
                method=getattr(self, "command", "") or NOT_RECORDED,
                path=self._logged_path(),
                status=self._status,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
            )

    def _logged_path(self) -> str:
        """The route, with any query string cut off.

        A query string is caller-controlled and this app puts nothing in one,
        so anything found there arrived from outside and must not be copied
        into a file that gets mailed around. The route is the part that
        identifies what was asked for, and it is all the log needs.
        """
        raw = getattr(self, "path", "") or NOT_RECORDED
        return raw.split("?", 1)[0]

    def _broke(self, exc: BaseException) -> None:
        """Answer a failure of ours, and record what it was.

        Two audiences, two messages. The page says what happened and what to do
        and names no internals; the log row carries the type and the message so
        the failure is diagnosable without a screenshot.

        Recording is best-effort on purpose: if the runtime is the thing that
        broke, `note()` raises too, and a logging failure must not replace the
        answer the person is waiting for.
        """
        with contextlib.suppress(Exception):
            note(
                "failed",
                "FAILED",
                f"the request could not be finished: {type(exc).__name__}: {exc}",
            )
        self._send(
            page(
                "<div class=warn><b>Something in Accountant Dad broke, so this "
                "entry could not be finished.</b> Nothing was written to your "
                "Tally. The details are in the activity log below. Try again, "
                "and if it keeps happening the log is what to send on.</div>"
            ),
            code=503,
        )

    def _form(self) -> dict[str, str]:
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode()
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

    def log_message(self, format: str, *args: object) -> None:  # quiet
        pass

    def _token(self) -> str:
        """The session token this request presented, or "".

        Two places, because two kinds of caller. A browser sends a cookie; the
        connector and any script send `Authorization: Bearer`. The cookie is
        read first only because a browser is the common case — neither is
        trusted more than the other, and both go through the same lookup.
        """
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and value:
                return urllib.parse.unquote(value)
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if header.startswith(prefix):
            return header[len(prefix) :].strip()
        return ""

    def _identify(self) -> bool:
        """Put the principal in context, or answer 401 and return False.

        Sited here beside `_confirm_company` for the same stated reason: a check
        every handler must remember is a check some handler will forget. A
        caller that returns False has ALREADY been answered and must return
        immediately without touching anything.
        """
        try:
            who = authenticate(self._token(), auth_store(), dev_mode=local_dev_mode())
            # DEFECT J1, FIXED 2026-08-11. THE GUARD EXISTED AND NOTHING CALLED IT.
            #
            # `Principal.require` was written with Task 2, has its own passing
            # test, and had NO CALLER anywhere in `accountant/`. An AST sweep
            # found exactly one reference to it: the `owns()` call inside its own
            # body. So a live session belonging to tenant B, presented to a
            # server serving the company tenant A has open, was authenticated and
            # then allowed to read that company's vouchers and to reverse one.
            #
            # It is the failure this file already had a sentence about, arriving
            # in the one place that sentence was not applied: a check every
            # caller must remember is a check some caller will forget, and the
            # test that would have caught it is the one nobody wrote, because a
            # unit test of a guard proves the guard works and says nothing about
            # whether it is installed.
            #
            # 403, not 401. The credential is fine; it is for somebody else's
            # books.
            who.require(served_tenant())
        except AuthRefusal as refusal:
            self._refuse(refusal)
            return False
        _principal.set(who)
        return True

    def _refuse(self, refusal: AuthRefusal) -> None:
        """Answer an auth refusal. The status carries the meaning, not the text.

        401 and 403 are kept apart because they say different things: 401 is "I
        do not know who you are", 403 is "I know, and no". Answering 403 to an
        unauthenticated request would tell a stranger the thing exists.
        """
        self._send(
            page(
                f"<div class=warn><b>{esc(refusal.reason)}.</b></div>"
                + (
                    '<p><a href="/login">Sign in</a></p>'
                    if refusal.status == 401
                    else ""
                )
            ),
            code=refusal.status,
        )

    def _confirm_company(self) -> None:
        """One check, once per request, before any handler does any work.

        Sited here rather than inside each handler for the same reason the
        Valid gate lives inside `pipeline.post`: a check every caller must
        remember is a check some caller will forget. `/health` is deliberately
        exempt - a readiness endpoint that needs Tally to answer cannot report
        that Tally is not answering.

        Every refusal it can raise starts with `REFUSAL`, so
        `handle_one_request` turns them into the 503 page.
        """
        runtime().confirm_company()

    def do_GET(self) -> None:
        if self.path.startswith("/health"):
            body = json.dumps(health(), indent=1).encode()
            code = 200 if health()["ready"] else 503
            self._send(body, code=code, content_type="application/json")
            return
        if self.path.startswith("/login"):
            # The one page a signed-out person is allowed to see, and the
            # reason `_identify` is not called before this line.
            self._send(render_login())
            return
        if not self._identify():
            return
        if self.path.startswith(METRICS_PATH):
            # AFTER `_identify` and BEFORE `_confirm_company`, and both
            # positions are the point. See `metrics()` for why.
            self._send(metrics().encode(), content_type="text/plain; charset=utf-8")
            return
        self._confirm_company()
        self._send(render_home())

    def do_POST(self) -> None:
        # The body is drained BEFORE the check, deliberately. Answering without
        # reading it leaves the request half-sent on the socket, so a person
        # whose company had closed got a connection error instead of the 503
        # that explains it.
        form = self._form()

        if self.path == "/login":
            try:
                token = login(form.get("email", ""), form.get("password", ""))
            except AuthRefusal as refusal:
                self._send(
                    render_login(f"<div class=warn>{esc(refusal.reason)}</div>"),
                    code=refusal.status,
                )
                return
            self._send_with_session(render_home(), token)
            return

        if not self._identify():
            return

        if self.path == "/logout":
            # Revoked in the database, not only forgotten by the browser. A
            # cookie the server still honours is not a logout, it is a request
            # that the stolen copy be polite.
            token = self._token()
            if token:
                auth_store().revoke_session(
                    token_fingerprint(token),
                    datetime.datetime.now(datetime.UTC).isoformat(),
                )
            self._send_with_session(render_login(), "", clear=True)
            return

        self._confirm_company()

        if self.path == "/entry":
            text = (form.get("text") or "").strip()
            if not text:
                self._send(render_home())
                return
            d = _run(text)
            self._send(page(render_decision(d) + '<p><a href="/">&larr; back</a></p>'))
            return

        if self.path == "/answer":
            # BEFORE the lookup, so even a request naming a draft that expired
            # is filed under the entry the person believes they are answering.
            # Set after the lookup, the one request most worth investigating -
            # the one that failed - would be the one with no entry id on it.
            observability.set_entry_id(form.get("draft", ""))
            d = draft_for(form.get("draft", ""), runtime())
            if d is None:
                self._send(render_home("<div class=warn>draft expired</div>"))
                return
            value = form.get("value", "")
            # NO DEFAULT. This read `form.get("problem", "which_account")`, so a
            # POST that named no question at all was treated as an answer to the
            # purpose question — the one whose answer sets the EXPENSE leg.
            # Guessing which question an answer belongs to IS the defect below,
            # in its quietest form. An absent id is now an empty one, which
            # matches no question and is refused.
            problem = form.get("problem", "")
            learn = False

            # THE ANSWER MUST BE BOUND TO THE QUESTION IT CLAIMS TO ANSWER.
            #
            # Since 2026-08-09 this checked the VALUE against the exact set the
            # decision offered, and nothing else. The problem id — which is what
            # `pipeline.answer` reads to choose WHICH LEDGER LEG the answer
            # lands on — came straight off the form and was compared to nothing.
            #
            # So an answer the system really did offer, filed against a question
            # it was not offered for, passed the guard and went to the other
            # leg. Measured over HTTP, 2026-08-10, demo company, unseen vendor:
            # the page asked `which_account` offering Purchases; the POST said
            # `problem=funding_is_named&value=Purchases`; the reply was 200 and
            # the draft's credit_account became "Purchases". The books would
            # then say the money came out of an expense account.
            #
            # The value guard could not have caught it. "Purchases" WAS offered.
            # What was never offered is the PAIRING.
            #
            # The whole check lives on `Decision` — the artefact that computed
            # the question — and runs BEFORE the runtime is read, before the
            # handover, yes and retype branches, and before `pipeline.answer`.
            # A refusal that half-applies an answer is worse than accepting it.
            #
            # 400, not 503: the request is wrong, not the service.
            refusal = answer_refusal(d, problem, value)
            if refusal:
                self._send(
                    page(
                        f"<div class=warn>{esc(refusal)}, so nothing was "
                        "changed.</div>"
                        + render_decision(d)
                        + '<p><a href="/">&larr; back</a></p>'
                    ),
                    code=400,
                )
                return

            live = runtime()
            with observability.tally_call("read_accounts"):
                accounts = live.client.read_accounts(live.company)
            with observability.tally_call("read_vouchers"):
                history = live.client.read_vouchers(live.company)

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
                # Recorded AFTER the re-evaluation below, not before. See the
                # comment at the call site.
                learn = problem != pipeline.FUNDING_PROBLEM

            d = pipeline.evaluate(d, accounts, history, live.memory, flag_cap=FLAG_CAP)

            # THE ORDER HERE IS THE WHOLE OF G6.3, and it was wrong until
            # 2026-08-09.
            #
            # `record_correction` used to run BEFORE `evaluate`. So the system
            # wrote the person's answer into memory as fact, and only then asked
            # its detectors whether that answer was surprising — by which time
            # it was not. `vendor_switch` exists to say "you said Y, but this
            # vendor has gone to X six times"; comparing Y against a history
            # that already contains Y can never say that. Measured: the
            # detector could not fire from the review screen at all, on any
            # input, because the one route to it destroyed its own evidence
            # one line earlier.
            #
            # Evaluating first costs nothing and restores the comparison. The
            # correction is still recorded on every non-funding answer, against
            # THIS company and no other, and it is still evidence rather than an
            # override: a vendor with genuinely contradictory history stays
            # CONFLICTED and keeps asking.
            #
            # NOT for the funding answer. `record_correction` teaches the
            # vendor -> EXPENSE account map, and "I paid in cash" says nothing
            # about what the money was for. Recording it wrote "Gupta Hardware
            # -> Cash" alongside "Gupta Hardware -> Purchases", which made the
            # vendor CONFLICTED, re-raised the question the person had just
            # answered, and ended the entry at NOT_VALID with both legs
            # correctly filled in. The funding leg is learned instead from the
            # posted voucher's own credit side.
            if learn:
                live.memory.record_correction(d.voucher.party, value)

            if d.outcome is Outcome.VALID:
                with observability.tally_call("post_voucher"):
                    d = pipeline.post(
                        d,
                        live.client,
                        log=live.store,
                        memory=live.memory,
                        run_id=live.identity.run_id,
                    )
            record(d, ACTION_FOR[d.outcome])
            remember_draft(d)
            self._send(page(render_decision(d) + '<p><a href="/">&larr; back</a></p>'))
            return

        if self.path == "/reverse":
            op = form.get("op", "")
            # Through `pipeline.reverse_operation`, not straight at the client.
            # This handler used to call `reverse_by_operation_id` with whatever
            # string the form carried and report "reversed" on the strength of
            # a boolean, having looked at nothing. Criterion #6.5 - the trial
            # balance returns to its exact prior value in paise - was checked
            # only inside tests, never on the path a person actually uses.
            live = runtime()
            with observability.tally_call("reverse_operation"):
                result = pipeline.reverse_operation(live.client, live.company, op)
            note(
                observability.SINGLE_REVERSAL_ACTION,
                observability.SINGLE_REVERSAL_DONE if result.reversed_ else "not_found",
                f"the person asked to undo {op}: {result.detail}",
                operation_id=op,
            )
            self._send(render_home())
            return

        if self.path == "/dismiss":
            # G6.1. Frozen criterion #3.7: dismissals are logged with the
            # detector name and the voucher id.
            #
            # Three things this deliberately does NOT do: it does not change
            # the outcome, it does not remove the problem, and it does not
            # post. A dismissal says the person saw the concern and chose not
            # to act on it. Treating that as approval is one line away and is
            # how a surprise nobody investigated ends up in somebody's books.
            observability.set_entry_id(form.get("draft", ""))
            d = draft_for(form.get("draft", ""), runtime())
            detector = form.get("detector", "")
            if d is None:
                self._send(render_home("<div class=warn>draft expired</div>"))
                return
            live_detectors = {f.detector for f in d.flags}
            if detector not in live_detectors or detector in d.dismissed:
                # Nothing to dismiss, or already dismissed. Silent about the
                # second case on purpose: re-posting the form must not add a
                # row, or a log nobody can count anything in is what is left.
                self._send(
                    page(render_decision(d) + '<p><a href="/">&larr; back</a></p>')
                )
                return

            d.dismissed.append(detector)
            flag = next(f for f in d.flags if f.detector == detector)
            note(
                "dismissed",
                "DISMISSED",
                f"the person dismissed {detector}: {flag.reason}. This records "
                "that they looked; it does not mean the entry is correct.",
                operation_id=d.operation_id,
                voucher_id=d.posted_tally_id or "",
                vendor_id=d.voucher.party,
                detail=f"detector={detector} draft={d.id}",
            )
            self._send(page(render_decision(d) + '<p><a href="/">&larr; back</a></p>'))
            return

        if self.path == "/reverse-all":
            # #14.7. Two requests on purpose: the first shows the exact list and
            # writes nothing, the second reverses the list that was shown. A
            # single-request version would have to re-read `list_our_vouchers`
            # at confirmation time, which means reversing vouchers the person
            # was never shown.
            live = runtime()
            if form.get("confirm") != "yes":
                with observability.tally_call("reversal_preview"):
                    batch = reversal.preview(live.client, live.company)
                remember_batch(batch, current_principal())
                self._send(render_bulk_preview(batch))
                return

            shown = batch_for(form.get("batch", ""), live)
            if shown is None:
                # No preview, or one that has aged out. Not an error to hide:
                # confirming a list nobody has seen is the exact thing the two
                # steps exist to prevent, so the person is sent back to look.
                self._send(
                    render_home(
                        "<div class=warn>That undo-everything request had no "
                        "preview, so nothing was reversed. Start again and "
                        "check the list.</div>"
                    )
                )
                return

            with observability.tally_call("reversal_execute"):
                result = reversal.execute(
                    # The log goes to `confirm` too, and it is the ONE
                    # transition in a batch whose actor is `operator`: a
                    # preview became an order because somebody pressed the
                    # button on this screen. Left out, the durable history
                    # starts at `reversing` and cannot say the confirmation
                    # happened at all. Owner decision Q8 = A.
                    #
                    # No backend is passed, and `confirm` no longer accepts
                    # one. Pressing this button touches no Tally, so naming the
                    # client here would put a false attribution in the audit
                    # trail.
                    reversal.confirm(
                        shown,
                        log=live.store,
                        company_key=live.memory.identity.key,
                        run_id=live.identity.run_id,
                    ),
                    live.client,
                    log=live.store,
                    company_key=live.memory.identity.key,
                    run_id=live.identity.run_id,
                )
            note(
                "bulk_reversed",
                result.state.value,
                f"the person asked to undo everything: {result.detail}",
            )
            self._send(render_bulk_result(result))
            return

        if self.path == "/delete-my-data":
            self._delete_my_data(form)
            return

        self._send(render_home(), 404)

    def _delete_my_data(self, form: dict[str, str]) -> None:
        """A customer deleting their own data. Two requests, own tenant only.

        THE TENANT IS NEVER READ FROM THE REQUEST. It comes off
        `current_principal()`, which `_identify` built from the credential, and
        that is the whole of the isolation guarantee here. A tenant id taken
        from this form would let any customer delete any other customer's data
        with one edited field — which is the exact rule `docs/AUTH.md` exists to
        enforce, arriving through the most destructive route in the product.

        TWO STEPS, like `/reverse-all`, and bound to the person the same way.
        The first request measures and writes nothing; the second executes the
        plan that was SHOWN, and only for the person it was shown to. A
        confirmation from a colleague who saw nothing is a deletion nobody
        agreed to, and every other check would pass on the way through.

        THE RUNTIME'S MEMORY IS INVALIDATED when this company's index was one of
        the ones erased. `CompanyMemory` answers from the store rather than from
        a cached index, so the learning really is gone the moment the rows are —
        but `report` still says READY, and a READY report is the app claiming it
        has read books whose derived index no longer exists. `invalidate` is the
        method that already exists for exactly this, and after it every entry is
        a question rather than a proposal.

        WHAT IS DELETED AND WHAT IS KEPT is `MemoryStore.delete_tenant`, not
        this handler. The route decides WHO may ask; the store decides what the
        answer does.
        """
        live = runtime()
        who = current_principal()
        if who is None:  # pragma: no cover - `_identify` has already run
            self._refuse(AuthRefusal(401, "no session was identified"))
            return

        if form.get("confirm") != "yes":
            plan = deletion_plan(live.store, who)
            if plan is None:
                self._no_customer_record(who)
                return
            remember_deletion(plan, who)
            self._send(render_deletion_preview(plan))
            return

        shown = deletion_for(form.get("plan", ""))
        if shown is None:
            # No preview, one that has aged out, or one taken by somebody else.
            # Not an error to hide: confirming a deletion nobody was shown is
            # the exact thing the two steps exist to prevent, so the person is
            # sent back to look at what it would do.
            self._send(
                render_home(
                    "<div class=warn>That delete-my-data request had no "
                    "preview, so nothing was deleted. Start again and read "
                    "what it would remove.</div>"
                )
            )
            return

        done = live.store.delete_tenant(
            who.tenant_id, datetime.datetime.now(datetime.UTC).isoformat()
        )
        # BEFORE the memory is invalidated, because `note()` writes through the
        # runtime and the row is the point of the whole request. A deletion
        # nobody can find afterwards cannot be evidenced to the customer who
        # asked for it, and this is the row they will be shown.
        note(
            "data_deleted",
            "deleted",
            f"{who.user_id} asked for their own customer data to be deleted: "
            f"{done.summary()}",
            detail=(
                f"erased={list(done.companies_erased)} "
                f"kept={list(done.companies_kept)} "
                f"users_closed={done.users_closed} "
                f"sessions_revoked={done.sessions_revoked} "
                f"index_rows_erased={done.rows_erased} "
                f"action_rows_kept={done.actions_kept}"
            ),
        )
        body = render_deletion_result(done)
        if live.company_key in done.companies_erased:
            live.memory.invalidate(
                "the customer asked for their data to be deleted, so the index "
                "we derived from this company's books was erased"
            )
        # The cookie is cleared as well as revoked. Every session was killed in
        # the same transaction as the deletion, so the browser is holding a
        # credential the server will now refuse; leaving it there would answer
        # the next request with a 401 that reads like a fault.
        self._send_with_session(body, "", clear=True)

    def _no_customer_record(self, who: Principal) -> None:
        """There is no `tenant` row behind this session, so nothing may be erased.

        Reachable in LOCAL_DEV_MODE, where the principal is a constant and no
        customer record was ever created. Refused rather than approximated: the
        deletion has to be RECORDED on the tenant row, and erasing an index
        while nothing says who asked or when is a half-deletion that cannot
        afterwards be explained to anybody.

        400 rather than 503. The service is fine; the request cannot be
        completed for this caller.
        """
        self._send(
            page(
                "<div class=warn><b>There is no customer account behind this "
                "session, so there is nothing to delete.</b> This session "
                f"belongs to {esc(who.tenant_id)}, which has no customer "
                "record. Nothing was erased.</div>"
                '<p><a href="/">&larr; back</a></p>'
            ),
            code=400,
        )


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
#: Where the audit trail is kept. A deployment points this at a mounted volume;
#: a person on a laptop leaves it alone and gets `data/app.db` beside the app.
ENV_DB = "ACCOUNTANT_DB"
#: WHOSE books this process serves. Defect J1, 2026-08-11.
#:
#: One process serves ONE company - `runtime()` binds it at startup and refuses
#: on any disagreement - so it also serves exactly one customer, and this names
#: them. A session belonging to anybody else is refused 403 before a handler
#: runs, however valid it is.
#:
#: It is a stated value rather than one derived from the audit log, because
#: deriving it would mean the FIRST tenant to authenticate against a fresh
#: database defines who owns the company. That is a land grab, not a check.
ENV_TENANT = "ACCOUNTANT_TENANT"

# TLS. TASK 7, 2026-08-11.
#
# WHICH LEG THIS IS, AND WHICH LEGS IT IS NOT
# -------------------------------------------
# There are three legs and only two of them can carry TLS:
#
#     browser  -> cloud       these two variables. In scope here.
#     cloud    -> connector   already enforced, and not by a warning:
#                             `accountant/agent/connector.py::https_cloud_call`
#                             REFUSES a non-https URL, because the request body
#                             carries the connector secret.
#     connector -> Tally      stays `http://`, on loopback, forever.
#
# The third one is physics, not preference. TallyPrime's HTTP server speaks
# plain HTTP on port 9000 and has no TLS setting to turn on; there is no
# certificate it would present and no way to give it one. `TallyConfig.url`
# (`accountant/tallyio/real.py:1921`) therefore builds `http://host:port` and
# that is correct. The connector runs on the SAME MACHINE as Tally, so those
# bytes never reach a network interface — Task 1's whole point was that port
# 9000 is never exposed. A check that flagged this leg would be flagging the
# one connection that cannot be attacked from the network and would make the
# product unrunnable. Nothing added here reads it.
ENV_TLS_CERT = "ACCOUNTANT_TLS_CERT"
ENV_TLS_KEY = "ACCOUNTANT_TLS_KEY"

#: The floor, written out rather than left to whatever OpenSSL was built with,
#: for the same reason `accountant/auth/identity.py` writes out n=16384, r=8,
#: p=1 instead of defaulting them: a security parameter nobody can read is a
#: security parameter nobody can check. TLS 1.0 and 1.1 were deprecated by
#: RFC 8996 (March 2021); 1.2 is the lowest version still permitted there.
MINIMUM_TLS = ssl.TLSVersion.TLSv1_2


class TlsMisconfigured(RuntimeError):
    """TLS was asked for and cannot be honoured, so no socket is bound.

    A separate type from `RealTallyRequired` because it is a separate fact.
    Reusing that one would print "REAL TALLY REQUIRED" at somebody whose Tally
    is fine and whose certificate path has a typo, and send them to debug the
    wrong machine.
    """


def tls_from_environment(
    environ: Mapping[str, str] | None = None,
) -> tuple[ssl.SSLContext | None, list[str]]:
    """Resolve TLS into a context or an honest None, with provenance.

    Three cases, and the middle one is the reason this function refuses rather
    than falls back:

        both set      an `ssl.SSLContext`, minimum TLS 1.2, HTTPS is served
        neither set   None, plain HTTP is served, and the banner says so
        exactly one   `TlsMisconfigured`, naming the one that is missing

    HALF-CONFIGURED TLS MUST NOT DEGRADE TO PLAINTEXT. An operator who set
    `ACCOUNTANT_TLS_CERT` believes the traffic is encrypted. A server that
    quietly serves HTTP anyway leaves them holding a false belief, and the
    session cookie, the password on the login form and every vendor name in
    the books go out in clear while the terminal that would have told them
    scrolled past hours ago. Refusing costs one restart. The other costs a
    credential and does not announce itself.

    `environ` is injectable exactly as `accountant.auth.identity.local_dev_mode`
    takes one, so the three cases are provable without touching the process.
    """
    source = dict(environ) if environ is not None else dict(os.environ)
    cert = source.get(ENV_TLS_CERT, "").strip()
    key = source.get(ENV_TLS_KEY, "").strip()

    if not cert and not key:
        return None, [
            f"{ENV_TLS_CERT}=<unset> (default)",
            f"{ENV_TLS_KEY}=<unset> (default)",
        ]

    if not cert or not key:
        missing, present = (
            (ENV_TLS_CERT, ENV_TLS_KEY) if not cert else (ENV_TLS_KEY, ENV_TLS_CERT)
        )
        raise TlsMisconfigured(
            f"{present} is set but {missing} is not, so TLS cannot be started "
            f"and nothing was bound. Set {missing} to the matching file, or "
            f"unset {present} to serve plain HTTP on purpose. Falling back to "
            f"plaintext here would leave you believing this server is encrypted "
            f"when it is not."
        )

    provenance = [
        f"{ENV_TLS_CERT}={cert!r} (environment)",
        f"{ENV_TLS_KEY}={key!r} (environment)",
    ]
    return tls_context(cert, key), provenance


def tls_banner(context: ssl.SSLContext | None) -> str:
    """The one sentence a person must not be able to miss or misread.

    Two modes, two different shapes, and the plaintext one is the longer and
    louder of the pair because it is the one that costs something. A banner
    that reads the same either way is a banner nobody checks.
    """
    if context is not None:
        return f"\n  *** SERVING HTTPS - TLS ON, minimum {MINIMUM_TLS.name} ***\n"
    return (
        f"\n  *** SERVING PLAIN HTTP - TLS IS OFF ***\n"
        f"  Everything crosses the network in clear, including the password "
        f"typed on the sign-in page and every vendor name in these books.\n"
        f"  Set {ENV_TLS_CERT} and {ENV_TLS_KEY} to serve HTTPS.\n"
    )


def tls_context(cert: str, key: str) -> ssl.SSLContext:
    """Build the server context. Every parameter that matters is stated here.

    `PROTOCOL_TLS_SERVER` negotiates the highest version both ends support and
    `minimum_version` puts the floor at TLS 1.2, so 1.0 and 1.1 cannot be
    negotiated down to. Neither is left to a default: the default depends on
    which OpenSSL the machine was built with, which means the answer to "what
    is the weakest connection this accepts" would change with the host.

    A certificate that cannot be loaded refuses the START, not the request. The
    alternative is a process that binds a socket and then fails every
    handshake, which looks like a network fault from the browser and takes an
    afternoon to trace back to a path.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = MINIMUM_TLS
    try:
        context.load_cert_chain(certfile=cert, keyfile=key)
    except (OSError, ssl.SSLError) as exc:
        raise TlsMisconfigured(
            f"the certificate {cert!r} and key {key!r} could not be loaded, so "
            f"nothing was bound: {type(exc).__name__}: {exc}. Check both paths "
            f"exist, that this process can read them, and that the key belongs "
            f"to the certificate."
        ) from exc
    return context


def start_server(
    host: str, port: int, context: ssl.SSLContext | None = None
) -> HTTPServer:
    """Bind, and wrap the socket in TLS when there is a context. One path.

    ONE wrapping site on purpose. `serve()` uses this and so does
    `tests/test_web.py::serving`, because a test that wrapped its own socket
    would be measuring a second implementation of the thing under test — the
    same argument `tests/conftest.py` makes for re-exporting the server fixture
    rather than copying it.

    `server_side=True` and the socket is wrapped BEFORE `serve_forever`, so the
    first byte any client sends is already inside a handshake. There is no
    window in which a plaintext request is accepted.
    """
    httpd = HTTPServer((host, port), Handler)
    if context is not None:
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    return httpd


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

    read(ENV_DB, str(Path("data") / "app.db"))
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
    # FIRST, before anything can fail. A start-up that refuses is a thing
    # somebody has to diagnose, and diagnosing it from a log with no lines in
    # it is what this whole task exists to stop.
    observability.install_logging()

    env_tally, env_company, env_backups, provenance = config_from_environment()
    tally = tally if tally is not None else env_tally
    company = company if company is not None else env_company
    backups = backups if backups is not None else env_backups

    # Resolved BEFORE `connect()` and before anything is printed, because a
    # half-configured TLS setting must not cost a round trip to Tally first.
    # The exception carries its own provenance — it names the variable that is
    # set and the one that is not — so nothing is lost by raising this early.
    context, tls_provenance = tls_from_environment()

    # Printed BEFORE connecting, so a wrong address is visible even when the
    # connection then fails. A refusal that does not say where it tried to go
    # sends the reader to check Tally when the real fault is a typo here.
    print("Accountant Dad, resolving configuration:")
    for line in [*provenance, *tls_provenance]:
        print(f"  {line}")
    if not backups.companies:
        print(
            f"  no company is recorded as backed up, so WRITES WILL BE REFUSED. "
            f"Set {ENV_BACKED_UP} to a comma-separated list once you have a backup."
        )

    # WHICH OF THE TWO IT IS, UNMISSABLY, AND BEFORE ANYTHING ELSE CAN FAIL.
    #
    # Printed here rather than beside the endpoint summary below for the same
    # reason the configuration block is printed before `connect()`: a person
    # whose Tally is down still needs to know whether the server they were
    # about to run was encrypted. "I thought TLS was on" is the exact belief
    # this feature exists to stop being wrong, and a banner that only appears
    # on a fully successful start is missing on every run where it matters.
    #
    # Its own block, in the same shape as the DEVELOPMENT MODE banner below,
    # rather than being inferable from a scheme in a URL. A reader scanning a
    # terminal does not diff `http` against `https`.
    print(tls_banner(context))

    connect(tally, company, backups=backups)

    # Through `runtime()`, not off the value `connect()` returned. That is the
    # function the request handlers use, so this is the same company check they
    # will make, run once BEFORE a socket is bound. A startup whose companies
    # disagree therefore stops in the terminal rather than serving a 503 to
    # somebody who has already typed an entry.
    #
    # NOT `confirm_company_open()` as well: `real_tally` has just listed the
    # open companies and refused unless ours was among them, one second ago.
    # A second round trip here would measure nothing new and would show up as
    # an extra request in the startup traces that `tests/test_startup_path.py`
    # counts.
    live = runtime()
    if local_dev_mode():
        # Loud, and on every start. A server that skips authentication must not
        # be something you can be running without knowing: the flag is set on
        # purpose, so the only way to be here by accident is to have forgotten,
        # and this is the sentence that reminds you.
        print(
            f"\n  *** {ENV_LOCAL_DEV_MODE}=1 - DEVELOPMENT MODE ***\n"
            f"  Authentication is SKIPPED. Every request runs as tenant "
            f"{LOCAL_DEV_TENANT!r}, and anybody who can reach this port can "
            f"read and write these books.\n"
            f"  Unset {ENV_LOCAL_DEV_MODE} to require a login.\n"
        )
    scheme = "https" if context is not None else "http"
    print(
        f"Accountant Dad -> {scheme}://{host}:{port}\n"
        f"  backend {live.identity.backend} at {live.identity.endpoint}\n"
        f"  company {live.company!r}\n"
        f"  books    {live.memory.report.status.value}\n"
        f"  writable {sorted(backups.companies) or 'NOTHING - reads only'}\n"
        f"  run      {live.identity.run_id}"
    )
    start_server(host, port, context).serve_forever()


if __name__ == "__main__":  # pragma: no cover - the process entry point
    try:
        serve()
    except TlsMisconfigured as exc:
        # Its own arm, and its own words. Printing "REAL TALLY REQUIRED" over a
        # certificate path would send the reader to the wrong machine.
        raise SystemExit(f"TLS MISCONFIGURED: nothing was bound. {exc}") from exc
    except RealTallyRequired as exc:
        # Exit non-zero so a launcher, a script or a packaged .exe can tell the
        # difference between "stopped" and "never started".
        raise SystemExit(f"{REFUSAL}: no operation performed. {exc}") from exc

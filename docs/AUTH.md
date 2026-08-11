# Who is asking, and whose books they may touch

Task 2 of the cloud-launch plan. Written 2026-08-10.

## The one rule

**A tenant id is derived from the credential, never read from the request.**

That sentence is the whole defence. A request that could name its own tenant
would let any customer read any other customer's books by editing a form field,
and no amount of checking downstream repairs it.

`Principal.tenant_id` therefore comes only from a stored session row.
`tests/test_auth.py::test_no_principal_is_ever_built_from_request_data` walks
the package's AST, finds every `Principal(...)` construction, and fails if any
of them is built from a form, a header, a query or a body.

It reads the tree rather than the text on purpose. A substring scan was tried
twice in this project and was wrong both times: once it matched `HTTPServer`
inside the paragraph explaining that no `HTTPServer` is used, and once it
matched a command inside a comment and reported a dead CI gate as live for two
days.

## The starting position, measured

```
grep -r "tenant\|login\|session" accountant/   -> 0 matches
accountant/web/app.py:1815  do_GET   answers anybody who can reach the socket
accountant/web/app.py:1824  do_POST  the same, including /reverse
accountant/web/app.py:1975  /reverse      deletes a voucher, one POST, no login
accountant/web/app.py:2044  /reverse-all  deletes every voucher we ever wrote
```

That was defensible while the socket was `127.0.0.1:8000` on one person's
laptop. The moment the same code serves two customers, "whoever can reach the
port" stops being an identity and becomes a hole.

## Two modes, and the default is the safe one

```
LOCAL_DEV_MODE=1     no login. One tenant, "local-dev". A warning is printed on
                     every start.
anything else        every request needs a live session, and the tenant comes
                     from that session.
```

Unset means production. A flag that must be **set** to become unsafe is a
different object from a flag that must be set to become safe: the first fails
closed when somebody forgets, and forgetting is the normal case.

The reading is strict — exactly `"1"`, not `"true"`, not `"yes"`, not `" 1"`.
A loose reading turns a typo into an unauthenticated production server, and the
failure is silent. `test_local_dev_mode_is_exactly_one` pins all eight
near-misses.

Dev mode ignores a token even when one is presented. Honouring it would mean a
developer holding a credential runs a different code path from one without, and
only one of them is testing what ships.

## What is stored, and what is not

| | stored | not stored |
|---|---|---|
| password | scrypt hash + a per-password salt | the password |
| session | SHA-256 fingerprint of the token | the token |

A database that leaks yields neither a password to reuse elsewhere nor a
session to replay. `test_a_token_is_never_stored_only_its_fingerprint` opens a
file-backed store and asserts the token's bytes do not appear anywhere in the
file — the row assertion alone would not catch a copy in an index or in a page
SQLite has not reclaimed.

scrypt parameters are `n=16384, r=8, p=1`, the interactive-login figures from
RFC 7914 §2. They are written out in `accountant/auth/identity.py` rather than
left to a default, so the cost is a fact somebody can read.

## Sessions, not JWT

A signed token the server does not store cannot be revoked, and revocation is a
stated requirement. It would therefore need a blocklist — which is a session
table wearing a hat. Sessions are stored, and revoking one is an `UPDATE`.

An `UPDATE`, not a `DELETE`: "this session was revoked at 14:02" stays
answerable. A deleted row and a session that never existed are indistinguishable
afterwards, and support needs to tell them apart.

Expiry lives in the row rather than in a timer, because the process restarts and
a session that expired while the server was down must still be expired when it
comes back.

## 401 and 403 are not interchangeable

```
401   I do not know who you are
403   I know, and no
```

Answering 403 to an unauthenticated request tells a stranger the resource
exists. Answering 401 to a valid session that reached for another tenant tells
the caller their credential is broken when it is fine.
`test_a_valid_session_is_refused_another_tenant_with_403_not_401` pins both.

The same argument applies to the login page: an unknown email and a wrong
password return one identical sentence, because two different sentences let
anybody with a browser enumerate which addresses have accounts.

## DEFECT J1 — the guard existed and nothing called it

Found 2026-08-11 by the end-to-end journey test, fixed the same day.

`Principal.require` was written with this task. It has a passing unit test
above. It had **no caller anywhere in `accountant/`** — an AST sweep found
exactly one reference to it, the `owns()` call inside its own body.

So `test_a_valid_session_is_refused_another_tenant_with_403_not_401` passed,
and a live session belonging to tenant B, presented to a server serving the
company tenant A had open, was authenticated and then allowed to read that
company's vouchers and to reverse one.

**A unit test of a guard proves the guard works. It says nothing about whether
the guard is installed**, and that is the whole of this defect. It is the
failure this document already had a sentence about — *a check every handler
must remember is a check some handler will forget* — arriving in the one place
that sentence was not applied.

### The fix

`_identify` now calls `who.require(served_tenant())` on every request, before
any handler runs, **unconditionally**. 403, not 401: the credential is fine, it
is for somebody else's books.

`ACCOUNTANT_TENANT` names the customer this process serves. One process serves
one company — `runtime()` binds it at startup and refuses on any disagreement —
so it serves exactly one customer.

It is a **stated** value, not one derived from the audit log, because deriving
it would mean the first tenant to authenticate against a fresh database defines
who owns the company. That is a land grab, not a check.

It **fails closed**. Unset, in production, means refuse every request. The
alternative — unset meaning "any tenant may enter" — is the defect itself
reintroduced as a default. A deployment that forgets the variable is broken and
says so on the first request; one that silently admits everybody is broken and
does not.

In `LOCAL_DEV_MODE` the served tenant is `local-dev`, which is also the tenant
`authenticate` hands out, so the check runs and compares `local-dev` with
`local-dev`. It is **not skipped** — a mutant that wrapped it in
`if not local_dev_mode():` changed no test, and the test that now catches it
reads the AST and refuses to find the call inside any conditional. Two code
paths where one will do is how the two come to disagree, and the one that skips
the guard is the one nobody measures.

### Mutants

```
the guard is not called                DIED  (5 tests)
an unset tenant lets everybody in      DIED
require refuses with 401 not 403       DIED  (4 tests)
dev mode skips the check entirely      SURVIVED -> 1 AST test written, DIED
```

## Where the check sits

`Handler._identify()` runs at the top of `do_GET` and `do_POST`, beside
`_confirm_company()` and for the same stated reason: **a check every handler
must remember is a check some handler will forget.**

Two exemptions, both deliberate:

- `/health` — a readiness endpoint that needs a login cannot report that nobody
  can log in.
- `/login` — a sign-in screen behind a sign-in check is a door locked from the
  outside.

`test_every_post_route_refuses_an_unauthenticated_caller` enumerates the five
POST routes rather than sampling one.

## The tables

Tenancy sits **above** `company_key` rather than replacing it. A tenant owns
companies; a company still owns its vendors and its log. Replacing the existing
key would have meant rewriting five tables and every query that reads them, to
gain nothing the extra level does not already give.

```
tenant     tenant_id PK, name, created_at, deleted_at
app_user   user_id PK, tenant_id, email UNIQUE, password_hash, salt, ...
session    token_fingerprint PK, user_id, tenant_id, created_at, expires_at,
           revoked_at
```

`test_every_table_is_keyed_by_company` now asserts three table shapes, and the
set equality is exact: a new table cannot be added without deciding, in that
test, which shape it is.

`deleted_at` on `tenant` and `app_user` was declared here on 2026-08-10 and
nothing set it. Task 13 does, on 2026-08-11: closing an account sets both,
revokes every session in the same transaction, and `authenticate` refuses a
session whose tenant is not live. See [`DATA_DELETION.md`](./DATA_DELETION.md).

## The audit log names the tenant and the user

`action_log` gained `tenant_id` and `user_id`, nullable, reading back as
`NOT_RECORDED` — the same shape and the same reason as `actor` before them. A
row written before tenancy existed, or by a script with no session behind it,
genuinely has neither and says so rather than being back-filled with a tenant
nobody measured.

`accountant/schema.py` used to carry the line *"Authenticated user identity is
NOT_IMPLEMENTED (H-05)."* That is what this task closed.

The request's principal reaches both writers — `record()` and `note()` — through
a `contextvars.ContextVar` rather than a module global. Task 11 replaced
`HTTPServer` with `ThreadingHTTPServer` on 2026-08-11, and a plain global would
then have been one customer's identity visible to another customer's request:
the exact leak the ContextVar was chosen in advance to prevent. Every thread
gets its own context, so a `set` in one request is invisible to every other.

The same task put the tenant boundary on `DRAFTS`, which is the other half of
this: a session identifies who is asking, and `DRAFT_TENANT` decides whose
half-finished entries that answer entitles them to. It is checked at TENANT
rather than at user, because two colleagues in one accounts department picking
up each other's entry is the design; `BATCHES` is checked at USER, because the
guarantee there is that whoever confirms a bulk reversal saw the list.
`tests/test_concurrency.py` asserts both directions of each.

## How the suite runs

`tests/conftest.py` sets `LOCAL_DEV_MODE=1` for the whole session. Roughly sixty
HTTP tests predate authentication and drive the app with no credential; left
alone, every one of them would now assert that a 401 is a 401 — sixty copies of
one measurement, and nothing left measuring what those tests were written for.

`tests/test_auth.py` deletes the variable, autouse, and asserts the production
path in full. The shipped auth code still runs under the session default —
`_identify` is called, a principal is built, the audit rows carry it — so what
the default skips is the credential, not the check.

## Mutants

Each fix was reverted and the tests were watched failing. `__pycache__` is
cleared between mutants: CPython invalidates on `(mtime, size)`, and a
size-preserving change restored inside the same second has already produced one
false verdict in this project.

```
the auth check removed from do_GET               DIED
the auth check removed from do_POST              DIED
authenticate ignores revoked_at                  DIED
local_dev_mode accepts "true"/"yes"              DIED
Principal.require never refuses                  DIED
login skips the password check                   DIED
the cookie loses HttpOnly                        DIED
note() drops the tenant and the user             SURVIVED -> 1 test written
record_decision drops the tenant and the user    DIED
```

The survivor is the one worth reading. Every audit assertion drove `/entry`,
which goes through `record()`; the **other** writer — reversal, dismissal,
handover, every failure row — was unmeasured and could have been shipping
anonymous rows. `test_a_note_row_records_the_tenant_and_the_user_too` exists
because that mutant lived.

## What this does NOT prove

Nothing here touches a real TallyPrime. Every test runs against `FakeTally`, so
all of it is `FAKETALLY` evidence.

Not built, and recorded here and in `docs/OWNER_WORK.md` rather than promised:

- **~~No `Secure` flag on the cookie.~~ DONE, Task 7, 2026-08-11.** It was
  absent because a browser withholds a `Secure` cookie over plain HTTP, which
  would have broken the loopback development server. Now that TLS exists the
  flag is set when — and only when — the connection is actually encrypted,
  measured off the socket rather than off a setting. See `docs/TLS.md`.

  What is still owner work is the **certificate**: there is no host, no domain
  and no certificate authority, so nothing has ever served HTTPS outside a
  test. `docs/OWNER_WORK.md` records that half.
- **No password reset.** Sending mail needs a provider, an account and a domain,
  none of which exist. There is deliberately no dead link on the login page.
- **No sign-up route.** Users are created by calling `MemoryStore.create_user`.
  Self-service registration is a product decision, not a defect.
- **No rate limit on `/login`.** The refusal is constant-time against timing,
  but nothing yet slows down a machine trying a million passwords.

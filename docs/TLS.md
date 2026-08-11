# TLS

**What is encrypted, what is not, and why the last one cannot be.**

Task 7, 2026-08-11. Evidence: `tests/test_tls.py`, 34 tests, `FAKETALLY`.

## The three legs

There are exactly three connections in this product. Two of them can carry TLS
and one of them cannot.

| leg | scheme | enforced where | why |
|---|---|---|---|
| browser → cloud | `https://` when configured | `accountant/web/app.py::serve` | crosses the internet, carries the password and the session cookie |
| cloud → connector | `https://`, **always** | `accountant/agent/connector.py::https_cloud_call` | the request body carries the connector secret |
| connector → Tally | `http://`, on loopback | `accountant/tallyio/real.py::TallyConfig.url` | TallyPrime has no TLS, and these bytes never reach a network |

### Why the Tally leg is exempt

TallyPrime's HTTP server speaks plain HTTP on port 9000. There is no TLS
setting to switch on, no certificate it would present, and no way to give it
one. That is a property of the software, not a choice made here.

It does not need one. The connector runs on the **same machine** as Tally, so
the traffic goes over loopback and never reaches a network interface. Task 1
exists precisely so port 9000 is never exposed: the connector dials out to the
cloud and binds no socket at all — `tests/test_connector.py::test_no_module_in_this_package_binds_a_socket`
reads the AST and fails if that ever changes.

**Any check added to this repository must be scoped away from this leg.** A
check that refused `http://127.0.0.1:9000` would refuse the one connection on
the list that nobody on a network can reach, and would make the product
unrunnable. `tests/test_tls.py` section 5 tests that on purpose:

- `test_the_connector_to_tally_leg_is_still_plain_http_on_loopback`
- `test_the_tls_resolver_reads_its_own_two_variables_and_nothing_else`
- `test_the_web_server_serving_https_does_not_change_the_tally_url`

### Why the cloud leg refuses rather than warns

`https_cloud_call` raises `ValueError` on a non-https URL before a socket is
opened. It does not log and continue. The payload carries the connector secret,
which can reach somebody's statutory books; a warning is a line in a file
nobody reads after the credential is already on the wire.

Six URL shapes are refused, not one: `http://`, `HTTP://` (uppercase),
`ftp://`, a bare host with no scheme, a protocol-relative `//host`, and the
empty string. Those are the shapes a configuration file actually produces.

## Supplying certificates

Two environment variables, and they are all-or-nothing.

| variable | what it is |
|---|---|
| `ACCOUNTANT_TLS_CERT` | path to the certificate chain, PEM |
| `ACCOUNTANT_TLS_KEY` | path to the matching private key, PEM |

```sh
export ACCOUNTANT_TLS_CERT=/etc/accountant/fullchain.pem
export ACCOUNTANT_TLS_KEY=/etc/accountant/privkey.pem
python -m accountant.web.app
```

The process must be able to **read** both files. A key readable by everybody on
the machine is a key that has already leaked; `chmod 600` and run the app as the
user that owns it.

**No certificate is committed to this repository, ever.** `.gitignore` excludes
`*.pem`, `*.key` and `*.crt`. The tests generate a self-signed certificate into
`tmp_path` at run time and it is discarded with the test session.

### Minimum version

TLS 1.2, stated in `accountant/web/app.py::MINIMUM_TLS` rather than left to a
default. RFC 8996 (March 2021) deprecated TLS 1.0 and 1.1, so 1.2 is the lowest
version still permitted. It is written out for the same reason
`accountant/auth/identity.py` writes out `n=16384, r=8, p=1` instead of
defaulting them: a security parameter nobody can read is a security parameter
nobody can check. Left to a default, "what is the weakest connection this
accepts" would change with whichever OpenSSL the host was built against.

TLS 1.3 is negotiated when the client supports it — `PROTOCOL_TLS_SERVER` takes
the highest both ends offer and `minimum_version` only sets the floor.

## What happens on each misconfiguration

| what you set | what happens |
|---|---|
| both variables, valid files | serves **HTTPS**. Banner: `*** SERVING HTTPS - TLS ON, minimum TLSv1_2 ***` |
| neither variable | serves **plain HTTP**. Banner: `*** SERVING PLAIN HTTP - TLS IS OFF ***`, plus what that costs and how to turn it on |
| only `ACCOUNTANT_TLS_CERT` | **refuses to start.** `TlsMisconfigured`, naming `ACCOUNTANT_TLS_KEY` as the missing one. Nothing is bound. |
| only `ACCOUNTANT_TLS_KEY` | **refuses to start.** Same, naming `ACCOUNTANT_TLS_CERT`. |
| either set to an empty or blank string | treated as unset. `ACCOUNTANT_TLS_KEY=` in a deploy script is an unset variable wearing an equals sign. |
| a path that does not exist, or cannot be read | **refuses to start**, naming both paths and the underlying error. |
| a key that does not match the certificate | **refuses to start**, same message. |

### Why half-configured TLS refuses instead of falling back

This is the decision worth arguing, because falling back is what most software
does.

An operator who set `ACCOUNTANT_TLS_CERT` **believes the traffic is
encrypted**. A server that quietly served plain HTTP anyway would leave them
holding that belief while the password on the sign-in page, the session cookie
and every vendor name in the books went out in clear. The terminal line that
would have told them scrolled past hours ago.

Refusing costs one restart. Falling back costs a credential, and it does not
announce itself.

### Why the certificate is loaded before the socket is bound

A process that bound the socket and *then* failed every handshake looks like a
network fault from the browser — connection reset, no page, no message. Tracing
that back to a file path takes an afternoon. Loading at startup turns the same
mistake into one sentence in the terminal.

## The session cookie

`accountant/web/app.py::Handler._send_with_session` sets:

```
HttpOnly; SameSite=Lax; Path=/
```

and adds `; Secure` **when and only when that connection is actually
encrypted**.

The flag is decided by `Handler._over_tls`, which asks the socket
(`isinstance(self.connection, ssl.SSLSocket)`) rather than reading a
configuration flag. A flag set at startup outlives the setting that produced
it; the socket cannot.

Getting this wrong in either direction has a cost:

- **`Secure` always on** — a browser withholds a `Secure` cookie over plain
  HTTP, so the loopback development server would hand out a cookie the browser
  then refuses to send back. The login silently never sticks, and nothing on
  screen says why. This is why the flag was absent until TLS existed;
  `docs/AUTH.md` recorded it as pending and now records it as done.
- **`Secure` never on** — the session cookie travels in clear on any request
  that reaches the server over HTTP, and a session cookie is a login.

Sign-out sets the cookie too, with `Max-Age=0`, and follows the same rule. A
browser matches a replacement cookie on its attributes, so a logout that
dropped `Secure` over HTTPS would leave the original cookie in place.

### Known boundary: TLS-terminating proxies

Behind a reverse proxy that terminates TLS and forwards plain HTTP, this
connection really is unencrypted, so `Secure` is correctly omitted even though
the browser spoke HTTPS. That deployment **does not exist** — there is no host,
no domain and no proxy; `docs/OWNER_WORK.md` records it. Reading
`X-Forwarded-Proto` to cover it would mean trusting a header any client can
forge, and would be built the day a proxy exists and can be configured to strip
it.

## Mutants

Each guard was reverted, the tests were watched failing, and it was restored.
`__pycache__` is cleared between mutants: CPython invalidates on
`(mtime, size)`, and a size-preserving change restored inside the same second
has already produced one false verdict in this project.

```
exactly one variable set falls back to plaintext             DIED
the missing variable is not named in the refusal             DIED
minimum_version left to the OpenSSL default    SURVIVED -> 1 test written
the stated floor is lowered to TLS 1.0                       DIED
the socket is never wrapped even with a context              DIED
the cookie always carries Secure                             DIED
the cookie never carries Secure                              DIED
the banner prints the same words in both modes               DIED
the banner is printed only after a successful connect        DIED
an unloadable certificate is swallowed and TLS skipped       DIED
https_cloud_call accepts any scheme                          DIED
the https requirement is widened to the connector-Tally URL  DIED
```

**The survivor is the one worth reading.** Deleting
`context.minimum_version = MINIMUM_TLS` from `tls_context` changed nothing this
suite could see: OpenSSL 3.6 on the machine it ran on already defaults a
`PROTOCOL_TLS_SERVER` context to TLS 1.2, so both assertions about the
resulting value stayed green over a context that stated nothing at all.

That is precisely the argument for stating it, arriving as a failure. The
default belongs to the host's OpenSSL, not to this program; on a build with a
lower default the same green tests would have sat over a server accepting TLS
1.0. `test_the_floor_is_assigned_and_not_merely_equal_to_this_hosts_default`
exists because that mutant lived: it reads the AST and requires the
**assignment**, not the value.

**The last mutant is the one that breaks the product rather than its security.**
Widening the https requirement to the Tally endpoint refuses
`http://127.0.0.1:9000`, which is the only address TallyPrime answers on. It is
in the list because a check written one line too wide is the likeliest way this
feature gets broken later, and it fails loudly instead.

## What this does NOT prove

- **No CA-issued certificate has ever been used.** Every test runs against a
  self-signed certificate generated at run time. There is no host, no domain
  and no certificate authority — `docs/OWNER_WORK.md` records it as owner work.
- **No real TallyPrime is touched.** Every result here is `FAKETALLY` evidence.
- **No HSTS header, no certificate pinning, no OCSP stapling.** Each needs a
  real domain to be meaningful and one to be safe: `Strict-Transport-Security`
  on a domain whose certificate later lapses locks every returning browser out
  of the product.
- **No client certificates.** The connector authenticates with a shared secret
  over TLS, not with mutual TLS. `docs/CONNECTOR_PROTOCOL.md` owns that choice.

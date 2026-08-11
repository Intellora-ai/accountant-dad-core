"""TLS — Task 7. What is encrypted, what cannot be, and what a half-set
configuration does.

THREE LEGS, AND ONLY TWO OF THEM CAN CARRY TLS
----------------------------------------------
    browser   -> cloud       `accountant/web/app.py::serve` — this file
    cloud     -> connector   `connector.py::https_cloud_call` — REFUSES http
    connector -> Tally       plain `http://` on loopback, and stays that way

The third is physics. TallyPrime's HTTP server speaks plain HTTP on port 9000
and has no TLS setting to turn on. The connector runs on the same machine, so
those bytes never reach a network interface. A check that flagged that leg
would flag the one connection nobody on a network can reach, and would make the
product unrunnable — so section 5 below tests, on purpose, that nothing added
here touches it.

WHAT EACH SECTION PROVES
------------------------
1. the three configurations   both variables -> HTTPS; neither -> HTTP; exactly
                              one -> REFUSE, naming the one that is missing
2. the certificate            minimum TLS 1.2 is stated and is what a live
                              connection negotiates; an unloadable certificate
                              stops the START, not every handshake
3. the banner                 a person is told which of the two they are running
4. the cookie                 `Secure` appears when the connection is encrypted
                              and never when it is not
5. the other two legs         the cloud leg still refuses plaintext; the Tally
                              leg is still plain http and is NOT flagged

WHAT IS NOT PROVED HERE
-----------------------
Nothing in this file touches a real TallyPrime, so every result is `FAKETALLY`
evidence. Nothing here touches a real certificate authority either: the
certificate is self-signed, generated into `tmp_path` at run time and never
committed. A CA-issued certificate for a real domain is owner work and
`docs/OWNER_WORK.md` records it.
"""

from __future__ import annotations

import ast
import shutil
import socket
import ssl
import subprocess
import urllib.parse
import urllib.request
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from accountant.agent import connector
from accountant.tallyio.factory import RealTallyRequired
from accountant.tallyio.real import TallyConfig
from accountant.web import app
from accountant.web.app import ENV_TLS_CERT, ENV_TLS_KEY, TlsMisconfigured
from tests.test_auth import PASSWORD, seeding
from tests.test_web import demo_company, fake_backend, serving

EMAIL = "a@alpha.test"

#: Every wait in this file is bounded by this, so a handshake that never
#: completes fails the test rather than hanging the suite.
TIMEOUT = 5.0


# ---------------------------------------------------------------------------
# certificates, made at run time and never committed
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def certificate(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """A self-signed certificate and its key, GENERATED INTO tmp_path.

    NOTHING IS COMMITTED, and that is not tidiness. A key checked into a
    repository is in every clone and every fork forever, and six months later
    nobody can tell a test key from a real one by looking at it. `.gitignore`
    excludes `*.pem`, `*.key` and `*.crt` as a second line of defence; this
    fixture writes outside the tree anyway.

    Session scoped because generating an RSA key is the slowest thing in this
    file — roughly 100 ms — and one certificate answers every test in it.

    `subjectAltName=IP:127.0.0.1` so the client below can VERIFY the
    certificate rather than skip verification. A test that turned checking off
    would prove the socket was encrypted and nothing at all about whether the
    certificate this server was configured with is the one it presented.

    `openssl` rather than a library: `cryptography` and `trustme` are not
    dependencies of this project and TLS is not a reason to add one. The binary
    ships with ubuntu-24.04, which is what every job in `.github/workflows`
    runs on.
    """
    openssl = shutil.which("openssl")
    if openssl is None:  # pragma: no cover - present on the CI runner and macOS
        pytest.skip("openssl is not on PATH, so no certificate can be generated")
    directory = tmp_path_factory.mktemp("tls")
    cert = directory / "cert.pem"
    key = directory / "key.pem"
    subprocess.run(  # noqa: S603 - fixed argv, no shell, path from shutil.which
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


@pytest.fixture
def server_tls(certificate: tuple[Path, Path]) -> ssl.SSLContext:
    """The context the app itself builds, through the shipped function.

    Not a context assembled here. `app.tls_context` is the thing under test, so
    a fixture that built its own would be measuring a second implementation.
    """
    cert, key = certificate
    return app.tls_context(str(cert), str(key))


@pytest.fixture
def client_tls(certificate: tuple[Path, Path]) -> ssl.SSLContext:
    """A client that TRUSTS this one certificate and checks the hostname.

    `check_hostname` stays on. Turning it off would make every test below pass
    against a server presenting any certificate at all, including none of ours.
    """
    cert, _key = certificate
    return ssl.create_default_context(cafile=str(cert))


@pytest.fixture(autouse=True)
def production_auth(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """This file runs with authentication REQUIRED and TLS UNSET by default.

    `tests/conftest.py` sets LOCAL_DEV_MODE=1 for the whole suite. Here the
    session cookie is one of the subjects, so the variable is deleted — and
    deleted rather than set to "0", because an unset variable is the case that
    ships, exactly as `tests/test_auth.py` argues.

    The two TLS variables are cleared for a second reason: `serve()` reads the
    real environment, so a developer who happened to have them set in their
    shell would silently change what these tests measure. Every test that wants
    them sets them itself.
    """
    monkeypatch.delenv(app.ENV_LOCAL_DEV_MODE, raising=False)
    monkeypatch.delenv(ENV_TLS_CERT, raising=False)
    monkeypatch.delenv(ENV_TLS_KEY, raising=False)
    app.disconnect()
    yield
    app.disconnect()


def fetch(
    base: str, path: str, context: ssl.SSLContext | None = None
) -> tuple[int, str]:
    """GET, over whichever scheme `base` names."""
    request = urllib.request.Request(base + path)  # noqa: S310 - loopback fixture
    with urllib.request.urlopen(  # noqa: S310
        request, timeout=TIMEOUT, context=context
    ) as answer:
        return answer.status, answer.read().decode()


def sign_in(base: str, context: ssl.SSLContext | None = None) -> str:
    """POST valid credentials and return the `Set-Cookie` header verbatim."""
    body = urllib.parse.urlencode({"email": EMAIL, "password": PASSWORD}).encode()
    request = urllib.request.Request(base + "/login", data=body)  # noqa: S310
    with urllib.request.urlopen(  # noqa: S310
        request, timeout=TIMEOUT, context=context
    ) as answer:
        assert answer.status == 200, "the credentials in this file must be valid"
        return answer.headers.get("Set-Cookie", "")


def closed_port() -> int:
    """A port nothing is listening on, so `connect()` fails at once.

    Bound and released. The web socket is never bound in the tests that use
    this — `serve()` refuses at `connect()`, which is several lines earlier.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def start_and_give_up(capsys: pytest.CaptureFixture[str]) -> str:
    """Everything `serve()` printed before it gave up on an absent Tally.

    THE REAL `serve()`, with nothing stubbed. Tally is simply not there, so it
    raises after the configuration block and the TLS banner are printed and
    before any socket is bound. That is the run a person is most likely to be
    staring at when they need the banner, so it is the run it is measured on.
    """
    unreachable = TallyConfig(
        host="127.0.0.1",
        port=closed_port(),
        timeout_seconds=2.0,
        retries=1,
        retry_backoff_seconds=0.0,
    )
    with pytest.raises(RealTallyRequired):
        app.serve("127.0.0.1", closed_port(), tally=unreachable, company=app.COMPANY)
    return capsys.readouterr().out


# ---------------------------------------------------------------------------
# 1. the three configurations
# ---------------------------------------------------------------------------


def test_both_variables_set_produce_a_tls_context(
    certificate: tuple[Path, Path],
) -> None:
    cert, key = certificate
    context, provenance = app.tls_from_environment(
        {ENV_TLS_CERT: str(cert), ENV_TLS_KEY: str(key)}
    )
    assert context is not None
    assert f"{ENV_TLS_CERT}={str(cert)!r} (environment)" in provenance
    assert f"{ENV_TLS_KEY}={str(key)!r} (environment)" in provenance


def test_both_variables_set_serve_https_and_a_page_comes_back_over_it(
    server_tls: ssl.SSLContext, client_tls: ssl.SSLContext
) -> None:
    """Not "a context was built" — a real browser-shaped request, encrypted.

    `/login` because it is the one page reachable without a session, and it is
    the first thing anybody's browser asks this server for.
    """
    with serving(
        demo_company(), fake_backend(), seed=seeding(), tls=server_tls
    ) as base:
        assert base.startswith("https://"), base
        status, body = fetch(base, "/login", client_tls)
        assert status == 200
        assert "Sign in" in body


def test_neither_variable_set_means_no_tls_and_says_so_in_the_provenance() -> None:
    context, provenance = app.tls_from_environment({})
    assert context is None
    assert provenance == [
        f"{ENV_TLS_CERT}=<unset> (default)",
        f"{ENV_TLS_KEY}=<unset> (default)",
    ]


def test_neither_variable_set_serves_plain_http_and_the_app_still_works() -> None:
    """The loopback development server must keep working. It is the only way
    anybody runs this today, and a TLS feature that broke it would be deleted
    again by the end of the week."""
    with serving(demo_company(), fake_backend(), seed=seeding()) as base:
        assert base.startswith("http://"), base
        status, body = fetch(base, "/login")
        assert status == 200
        assert "Sign in" in body


def test_only_the_certificate_is_set_and_the_start_is_refused(
    certificate: tuple[Path, Path],
) -> None:
    """Half-configured TLS must not degrade to plaintext.

    The operator who set this variable believes the traffic is encrypted. A
    server that quietly served HTTP anyway would leave them holding that belief
    while the password on the sign-in page went out in clear.
    """
    cert, _key = certificate
    with pytest.raises(TlsMisconfigured) as refused:
        app.tls_from_environment({ENV_TLS_CERT: str(cert)})
    assert ENV_TLS_KEY in str(refused.value), "the refusal must name what is missing"
    assert f"{ENV_TLS_CERT} is set" in str(refused.value)


def test_only_the_key_is_set_and_the_start_is_refused(
    certificate: tuple[Path, Path],
) -> None:
    """The mirror case, and it is a separate test because a refusal that names
    the wrong variable sends the reader to the wrong file."""
    _cert, key = certificate
    with pytest.raises(TlsMisconfigured) as refused:
        app.tls_from_environment({ENV_TLS_KEY: str(key)})
    assert ENV_TLS_CERT in str(refused.value), "the refusal must name what is missing"
    assert f"{ENV_TLS_KEY} is set" in str(refused.value)


def test_a_blank_variable_counts_as_unset_rather_than_as_a_path(
    certificate: tuple[Path, Path],
) -> None:
    """`ACCOUNTANT_TLS_KEY=` in a deploy script is an unset variable wearing an
    equals sign. Treating it as a path would refuse to start with a message
    about a file called "", which explains nothing."""
    cert, _key = certificate
    assert app.tls_from_environment({ENV_TLS_CERT: "  ", ENV_TLS_KEY: "  "}) == (
        None,
        [f"{ENV_TLS_CERT}=<unset> (default)", f"{ENV_TLS_KEY}=<unset> (default)"],
    )
    with pytest.raises(TlsMisconfigured):
        app.tls_from_environment({ENV_TLS_CERT: str(cert), ENV_TLS_KEY: "   "})


def test_the_environment_is_read_when_no_mapping_is_given(
    monkeypatch: pytest.MonkeyPatch, certificate: tuple[Path, Path]
) -> None:
    """The argument exists for the tests. The process still has to be the
    default, or `serve()` would be reading something nobody sets."""
    cert, key = certificate
    monkeypatch.setenv(ENV_TLS_CERT, str(cert))
    monkeypatch.setenv(ENV_TLS_KEY, str(key))
    context, _provenance = app.tls_from_environment()
    assert context is not None


# ---------------------------------------------------------------------------
# 2. the certificate and the version floor
# ---------------------------------------------------------------------------


def test_the_minimum_version_is_tls_1_2_and_is_stated_not_defaulted() -> None:
    """RFC 8996 deprecated TLS 1.0 and 1.1 in March 2021. The floor is written
    into the module rather than inherited from whatever OpenSSL the machine was
    built with, so "what is the weakest connection this accepts" has one answer
    and it does not change with the host."""
    assert app.MINIMUM_TLS is ssl.TLSVersion.TLSv1_2


def test_the_built_context_carries_that_floor(server_tls: ssl.SSLContext) -> None:
    assert server_tls.minimum_version is ssl.TLSVersion.TLSv1_2


def test_the_floor_is_assigned_and_not_merely_equal_to_this_hosts_default() -> None:
    """A MUTANT SURVIVED HERE, 2026-08-11, and this test is why it no longer can.

    Deleting `context.minimum_version = MINIMUM_TLS` from `tls_context` changed
    nothing measurable on this machine. OpenSSL 3.6 already defaults a
    `PROTOCOL_TLS_SERVER` context to TLS 1.2, so the two assertions above stayed
    green against a context that stated nothing at all.

    That IS the argument for stating it, turned into a test. The default is a
    property of whichever OpenSSL the host was built against, not of this
    program; on a build with a lower default the same green assertions would sit
    over a server accepting TLS 1.0. So what gets measured is the ASSIGNMENT,
    read off the AST — the same technique `tests/test_connector.py` uses to
    prove no module in that package binds a socket, and for the same reason: a
    substring scan would match the sentence in the docstring explaining it.
    """
    source = Path(app.__file__).read_text(encoding="utf-8")
    functions = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "tls_context"
    ]
    assert len(functions) == 1, "tls_context must exist exactly once"
    assigned = [
        node
        for node in ast.walk(functions[0])
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute) and target.attr == "minimum_version"
            for target in node.targets
        )
        and isinstance(node.value, ast.Name)
        and node.value.id == "MINIMUM_TLS"
    ]
    assert assigned, (
        "tls_context must ASSIGN minimum_version from MINIMUM_TLS. Inheriting "
        "the host's OpenSSL default is the failure this test exists to catch, "
        "and it is invisible on a host whose default already happens to match."
    )


def test_a_live_connection_negotiates_at_least_tls_1_2(
    server_tls: ssl.SSLContext, client_tls: ssl.SSLContext
) -> None:
    """The floor, measured on a real handshake rather than read off a field."""
    with serving(
        demo_company(), fake_backend(), seed=seeding(), tls=server_tls
    ) as base:
        parts = urllib.parse.urlsplit(base)
        assert parts.hostname is not None and parts.port is not None
        with (
            socket.create_connection(
                (parts.hostname, parts.port), timeout=TIMEOUT
            ) as raw,
            client_tls.wrap_socket(raw, server_hostname=parts.hostname) as secured,
        ):
            assert secured.version() in ("TLSv1.2", "TLSv1.3"), secured.version()


def test_a_certificate_that_cannot_be_loaded_refuses_the_start(
    tmp_path: Path,
) -> None:
    """The START, not every handshake.

    A process that bound the socket and then failed every handshake looks like
    a network fault from the browser, and takes an afternoon to trace back to a
    path. The refusal names both paths so it takes a second instead.
    """
    missing = tmp_path / "nothing.pem"
    with pytest.raises(TlsMisconfigured) as refused:
        app.tls_context(str(missing), str(missing))
    assert str(missing) in str(refused.value)


def test_a_key_that_does_not_match_the_certificate_refuses_the_start(
    certificate: tuple[Path, Path], tmp_path: Path
) -> None:
    """A pair that does not pair is the commonest certificate mistake there is,
    and it must be caught before a socket exists."""
    cert, _key = certificate
    not_a_key = tmp_path / "not-a-key.pem"
    not_a_key.write_text("-----BEGIN PRIVATE KEY-----\nnope\n", encoding="utf-8")
    with pytest.raises(TlsMisconfigured):
        app.tls_context(str(cert), str(not_a_key))


# ---------------------------------------------------------------------------
# 3. the banner
# ---------------------------------------------------------------------------


def test_the_banner_says_plain_http_when_no_certificate_is_configured(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A person cannot tell an encrypted server from a plaintext one by looking
    at it. "I thought TLS was on" is the belief this whole feature exists to
    stop being wrong, so the mode gets its own block and its own words."""
    printed = start_and_give_up(capsys)
    assert "SERVING PLAIN HTTP - TLS IS OFF" in printed
    assert "SERVING HTTPS" not in printed
    assert ENV_TLS_CERT in printed, "it must say how to turn TLS on"


def test_the_banner_says_https_when_both_variables_are_set(
    monkeypatch: pytest.MonkeyPatch,
    certificate: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    cert, key = certificate
    monkeypatch.setenv(ENV_TLS_CERT, str(cert))
    monkeypatch.setenv(ENV_TLS_KEY, str(key))
    printed = start_and_give_up(capsys)
    assert "SERVING HTTPS - TLS ON, minimum TLSv1_2" in printed
    assert "SERVING PLAIN HTTP" not in printed


def test_the_banner_is_printed_before_tally_is_even_contacted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Tally is down in `banner_of`, and the banner still appeared.

    Deliberate. A banner that only shows on a fully successful start is missing
    on every run where somebody is actually reading the terminal.
    """
    printed = start_and_give_up(capsys)
    assert "TLS IS OFF" in printed
    assert f"{ENV_TLS_CERT}=<unset> (default)" in printed
    assert f"{ENV_TLS_KEY}=<unset> (default)" in printed


def test_a_half_configured_tls_setting_stops_serve_before_it_binds(
    monkeypatch: pytest.MonkeyPatch, certificate: tuple[Path, Path]
) -> None:
    """Through `serve()` itself, so the refusal is proved on the path a person
    actually runs rather than only on the function underneath it."""
    cert, _key = certificate
    monkeypatch.setenv(ENV_TLS_CERT, str(cert))
    monkeypatch.delenv(ENV_TLS_KEY, raising=False)
    port = closed_port()
    with pytest.raises(TlsMisconfigured):
        app.serve("127.0.0.1", port, tally=TallyConfig(), company=app.COMPANY)
    # Nothing is listening on the port it would have used, and nothing is
    # listening on plain HTTP either — which is the failure this refusal exists
    # to prevent.
    with socket.socket() as probe:
        probe.settimeout(TIMEOUT)
        assert probe.connect_ex(("127.0.0.1", port)) != 0, (
            "a refused TLS configuration must leave no socket bound at all"
        )


# ---------------------------------------------------------------------------
# 4. the cookie
# ---------------------------------------------------------------------------


def test_the_session_cookie_gains_secure_over_https(
    server_tls: ssl.SSLContext, client_tls: ssl.SSLContext
) -> None:
    with serving(
        demo_company(), fake_backend(), seed=seeding(), tls=server_tls
    ) as base:
        cookie = sign_in(base, client_tls)
    assert "Secure" in cookie, cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_the_session_cookie_has_no_secure_flag_over_plain_http() -> None:
    """Not an oversight and not a weaker default. A browser WITHHOLDS a
    `Secure` cookie sent over plain HTTP, so setting it here would mean the
    loopback development server hands out a cookie the browser then refuses to
    send back — a login that silently never sticks, with nothing on screen
    saying why."""
    with serving(demo_company(), fake_backend(), seed=seeding()) as base:
        cookie = sign_in(base)
    assert "Secure" not in cookie, cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_the_cleared_cookie_follows_the_same_rule(
    server_tls: ssl.SSLContext, client_tls: ssl.SSLContext
) -> None:
    """Sign-out sets the cookie too, with Max-Age=0. A browser matches a
    replacement cookie on its attributes, so a logout that dropped `Secure`
    over HTTPS would leave the original one in place."""
    with serving(
        demo_company(), fake_backend(), seed=seeding(), tls=server_tls
    ) as base:
        cookie = sign_in(base, client_tls)
        token = urllib.parse.unquote(cookie.split("=", 1)[1].split(";", 1)[0])
        request = urllib.request.Request(base + "/logout", data=b"")  # noqa: S310
        request.add_header("Cookie", f"{app.COOKIE}={token}")
        with urllib.request.urlopen(  # noqa: S310
            request, timeout=TIMEOUT, context=client_tls
        ) as answer:
            cleared = answer.headers.get("Set-Cookie", "")
    assert "Max-Age=0" in cleared
    assert "Secure" in cleared, cleared


def test_start_server_wraps_the_socket_only_when_it_is_given_a_context(
    server_tls: ssl.SSLContext,
) -> None:
    """One wrapping site, and it is the shipped one.

    `serve()` and `tests/test_web.py::serving` both bind through this function,
    so the cookie rule above is measured against the same socket the product
    binds rather than against a second one assembled in a fixture.
    """
    encrypted = app.start_server("127.0.0.1", 0, server_tls)
    try:
        assert isinstance(encrypted.socket, ssl.SSLSocket)
    finally:
        encrypted.server_close()

    plain = app.start_server("127.0.0.1", 0, None)
    try:
        assert not isinstance(plain.socket, ssl.SSLSocket)
    finally:
        plain.server_close()


# ---------------------------------------------------------------------------
# 5. the other two legs
# ---------------------------------------------------------------------------


def test_the_cloud_leg_still_refuses_a_plaintext_url() -> None:
    """The request body carries the connector secret. `http://` would hand
    write access to somebody's statutory books to anyone on the path."""
    with pytest.raises(ValueError, match="must be https"):
        connector.https_cloud_call("http://cloud.example.invalid/x", {"secret": "s"})


@pytest.mark.parametrize(
    "url",
    [
        "http://cloud.example.invalid/x",
        "HTTP://cloud.example.invalid/x",
        "ftp://cloud.example.invalid/x",
        "cloud.example.invalid/x",
        "//cloud.example.invalid/x",
        "",
    ],
)
def test_every_scheme_that_is_not_https_is_refused(url: str) -> None:
    """One lowercase `http://` was the only case measured before today. An
    uppercase scheme, a missing scheme and a protocol-relative URL are the
    three ways a config file actually produces a non-https value."""
    with pytest.raises(ValueError, match="must be https"):
        connector.https_cloud_call(url, {"secret": "s"})


def test_the_refusal_happens_before_any_socket_is_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The order is the whole point. A check that ran after the request was
    built would already have handed the payload to a resolver."""

    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a socket was opened for a plaintext URL")

    monkeypatch.setattr(urllib.request, "urlopen", explode)
    with pytest.raises(ValueError, match="must be https"):
        connector.https_cloud_call("http://cloud.example.invalid/x", {"secret": "s"})


def test_the_refusal_does_not_print_the_secret() -> None:
    """A refusal that quotes the payload puts the credential in a log file, and
    the log file is the thing people paste into a chat window."""
    with pytest.raises(ValueError) as refused:
        connector.https_cloud_call(
            "http://cloud.example.invalid/x", {"secret": "hunter2-not-in-messages"}
        )
    assert "hunter2-not-in-messages" not in str(refused.value)


def test_the_connector_to_tally_leg_is_still_plain_http_on_loopback() -> None:
    """The leg that CANNOT carry TLS, and must not be checked as if it could.

    TallyPrime's HTTP server has no TLS setting; there is no certificate it
    would present and no way to give it one. The connector runs on the same
    machine, so these bytes never reach a network interface — Task 1's whole
    point was that port 9000 is never exposed. Anything that refused this URL
    would make the product unrunnable.
    """
    tally = TallyConfig(host="127.0.0.1", port=9000)
    assert tally.url == "http://127.0.0.1:9000"
    assert tally.is_loopback


def test_the_tls_resolver_reads_its_own_two_variables_and_nothing_else() -> None:
    """Scoping, measured. An environment describing a plain-http loopback Tally
    must resolve to "no TLS" without a word of complaint — not to a refusal,
    and not to a context built out of somebody's Tally settings."""
    tally_environment: Mapping[str, str] = {
        app.ENV_HOST: "127.0.0.1",
        app.ENV_PORT: "9000",
        app.ENV_COMPANY: "Demo Co",
        app.ENV_DB: "data/app.db",
    }
    assert app.tls_from_environment(tally_environment) == (
        None,
        [f"{ENV_TLS_CERT}=<unset> (default)", f"{ENV_TLS_KEY}=<unset> (default)"],
    )


def test_the_web_server_serving_https_does_not_change_the_tally_url(
    monkeypatch: pytest.MonkeyPatch, certificate: tuple[Path, Path]
) -> None:
    """The browser leg being encrypted must not make the Tally leg pretend to
    be. They are different connections with different physics, and a change
    here that rewrote `TallyConfig.url` to `https://` would point the connector
    at a port that speaks no TLS and fail every read."""
    cert, key = certificate
    monkeypatch.setenv(ENV_TLS_CERT, str(cert))
    monkeypatch.setenv(ENV_TLS_KEY, str(key))
    context, _provenance = app.tls_from_environment()
    assert context is not None
    assert TallyConfig(host="127.0.0.1", port=9000).url.startswith("http://")

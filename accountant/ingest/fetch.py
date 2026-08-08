"""Child 5 - the only module in this package that could reach the network.

It is separate, and separate on purpose. `spend.py` reads bytes and nothing
else, so the loader, the parser, the score adapter and the cross-organisation
comparison are all provably offline: they cannot become flaky, they cannot be
slowed by a government CDN, and they cannot silently start testing gov.uk's
uptime instead of this code.

**No test in this repository opens a socket.** The reading is behind `Opener`,
a one-method Protocol. The default is `urllib.request.urlopen`. Tests pass a
fake that returns bytes they already hold, which exercises every line here
without a connection existing.

Three deliberate limits, all fail-closed:

    scheme      https only
    host        must be a gov.uk host - this package has one publisher
    size        capped, and the cap is enforced by reading one byte past it, so
                an oversized body is never fully buffered

The bytes that come back are untrusted input. They are handed to `spend.py`,
which parses them as data. Nothing in this package executes, evaluates or
interprets a fetched file as instructions.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from typing import Protocol, cast

from accountant.ingest.sources import Source

HTTPS = "https"
PERMITTED_HOST_SUFFIX = ".gov.uk"

# The largest published file seen at retrieval was about 1.3 MB. 32 MB is well
# clear of a normal month and still refuses anything that is not one.
MAX_BYTES = 32 * 1024 * 1024

TIMEOUT_SECONDS = 30


class Response(Protocol):
    """The part of an HTTP response this module uses, and nothing more."""

    def read(self, amount: int, /) -> bytes: ...

    def __enter__(self) -> Response: ...

    def __exit__(self, *details: object) -> None: ...


class Opener(Protocol):
    """Anything that turns a URL into a readable response."""

    def __call__(self, url: str, /, *, timeout: int) -> Response: ...


# urlopen is wider than this Protocol and its stub types are not written in
# terms of it, so the narrowing is stated once, here, rather than at the call
# site where it would look like a way of silencing an error.
URLLIB_OPENER: Opener = cast(Opener, urllib.request.urlopen)


def check_url(url: str) -> None:
    """Refuse anything that is not https on a gov.uk host, before opening it."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != HTTPS:
        raise ValueError(
            f"refusing {parsed.scheme or 'a schemeless'} URL; {HTTPS} only"
        )
    host = parsed.hostname or ""
    if not host.endswith(PERMITTED_HOST_SUFFIX):
        raise ValueError(
            f"refusing host {host!r}; this package fetches from "
            f"{PERMITTED_HOST_SUFFIX} only"
        )


def read_url(
    url: str,
    *,
    opener: Opener = URLLIB_OPENER,
    max_bytes: int = MAX_BYTES,
) -> bytes:
    """Fetch one published file as bytes. Live, when the default opener is used."""
    check_url(url)
    with opener(url, timeout=TIMEOUT_SECONDS) as response:
        # One byte past the cap, so "too big" is detected without ever holding
        # the whole of an oversized body.
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"{url} is larger than the {max_bytes} byte cap; refusing it")
    return data


def fetch_source(
    source: Source,
    *,
    opener: Opener = URLLIB_OPENER,
    max_bytes: int = MAX_BYTES,
) -> bytes:
    """Fetch the whole published month for one recorded source.

    The committed fixture is a slice of what this returns. Refreshing a source
    means running this, counting the rows, and updating `sources.py` - never
    editing the recorded numbers to match a new file that was never fetched.
    """
    return read_url(source.url, opener=opener, max_bytes=max_bytes)

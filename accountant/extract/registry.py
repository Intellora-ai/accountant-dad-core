"""Which extraction backend the application uses. One place, inside the package.

WHY THIS EXISTS
---------------
The seam is the `Extractor` Protocol, and it holds: `pipeline.build_draft` and
`pipeline.run` take an `Extractor` and never name a class. But SOMEBODY has to
choose one, and until now the choice was a line in `accountant/web/app.py`:

    from accountant.extract.adapter import TypedTextExtractor

That single line is the whole difference between "swapping the backend touches
only accountant/extract/" and "swapping the backend edits the web app". The
selection now has a home on this side of the boundary, so a swap is a change to
`DEFAULT_BACKEND` below and to nothing else.

WHY NO ENVIRONMENT VARIABLE
---------------------------
An env var would make the swap require no code change at all, and it would also
mean the backend that read a bill could differ between two machines with
identical source. This system already refuses that kind of silence elsewhere:
`accountant/web/app.py` takes its Tally client by injection through
`configure()` rather than picking one from the environment. Same reasoning.
The name is written down, and it is written down once.

WHY `build` RAISES RATHER THAN FALLING BACK
-------------------------------------------
A typo in a backend name that quietly returns the default is a machine reading
bills with something other than what the deployment asked for. Fail closed.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That any backend reads a bill well. This file chooses one; it does not grade
one. Accuracy is `artifacts/extraction_backends.md`, and the choice between
third-party readers is an owner decision, not a test result.

That a deployment can pick a backend WITHOUT a code change. It cannot, on
purpose — see "why no environment variable" above. One consequence is worth
naming because it costs something real: `accountant/web/app.py` calls
`default_extractor()` with no argument, and `configure()` takes no extractor,
so a test cannot put the running web app on a different backend without either
editing `DEFAULT_BACKEND` or adding an injection seam. That is why a reader
OUTAGE is still not reachable over HTTP; `tests/test_extract_outage.py` records
it as environment-limited and names the change that would lift it.

ADOPTED 2026-08-10
------------------
`web/app.py` now resolves its backend here. Measured at 27333e9 the concrete
backend references outside this package were
`{'accountant/web/app.py': ['TypedTextExtractor']}`; measured after the change
they are `{}`. `tests/test_adapter_contract.py` counts them, so the number is
reported rather than assumed, and a new site is a failing test.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from accountant.extract.adapter import (
    Extractor,
    StubExtractor,
    TypedTextExtractor,
    UnavailableExtractor,
)

#: The backend the application runs with. Change THIS to swap it.
DEFAULT_BACKEND: Final = "typed_text"


class UnknownBackend(LookupError):
    """A backend name nothing here can build. Never silently the default."""


#: Backends that need nothing to be constructed.
_READY: Final[dict[str, Callable[[], Extractor]]] = {
    "typed_text": TypedTextExtractor,
    "stub": StubExtractor,
    "unavailable": UnavailableExtractor,
}

#: Backends that exist but cannot be built from a name alone, and the sentence
#: saying what they still need. Separated from "unknown" because the two lead a
#: person to completely different next actions.
_NEEDS_WIRING: Final[dict[str, str]] = {
    "reader_service": (
        "it needs a transport; construct "
        "accountant.extract.service.ServiceExtractor(call) where the "
        "deployment owns `call`"
    ),
}


def available() -> tuple[str, ...]:
    """Every backend `build` can produce, sorted so the list is stable."""
    return tuple(sorted(_READY))


def build(name: str = "") -> Extractor:
    """The named backend, or `DEFAULT_BACKEND` when nothing is named."""
    chosen = name or DEFAULT_BACKEND
    ready = _READY.get(chosen)
    if ready is not None:
        return ready()
    if chosen in _NEEDS_WIRING:
        raise UnknownBackend(
            f"extraction backend {chosen!r} cannot be built from a name: "
            f"{_NEEDS_WIRING[chosen]}"
        )
    raise UnknownBackend(
        f"no extraction backend named {chosen!r}; available: " + ", ".join(available())
    )


def default_extractor() -> Extractor:
    """The one call an application makes. Everything else here is detail."""
    return build(DEFAULT_BACKEND)

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

WHY `guarded` EXISTS, AND WHY IT IS NOT PART OF `build`
------------------------------------------------------
`build` answers "which backend". `guarded` answers "what happens when the one
we chose falls over". They are separate because the second applies to a backend
this file never chose: `configure(extractor=...)` lets a deployment hand in an
object of its own, and that object is the one most likely to raise, because
nothing here wrote it.

`ServiceExtractor` already promises never to raise. A backend somebody else
writes promises nothing, and `pipeline.build_draft` calls `extract` with no
try around it, so an exception there is an HTTP 503 saying the application
broke — for a person whose only problem is that a supplier's website is down.
`guarded` closes that, in one place, for every backend at once.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That any backend reads a bill well. This file chooses one; it does not grade
one. Accuracy is `artifacts/extraction_backends.md`, and the choice between
third-party readers is an owner decision, not a test result.

That a deployment can pick a backend WITHOUT a code change. It cannot, on
purpose — see "why no environment variable" above. `configure(extractor=...)`
is an INJECTION seam for a caller that already holds an object, not a way to
name a backend from outside; it defaults to `default_extractor()` and so names
nothing.

ADOPTED 2026-08-10
------------------
`web/app.py` now resolves its backend here. Measured at 27333e9 the concrete
backend references outside this package were
`{'accountant/web/app.py': ['TypedTextExtractor']}`; measured after the change
they are `{}`. `tests/test_adapter_contract.py` counts them, so the number is
reported rather than assumed, and a new site is a failing test.

The seam that lifted the HTTP reader outage landed the same day. It adds no
selection site: `configure(extractor=...)` is annotated `Extractor`, the
default is `default_extractor()`, and the guard below is reached through a
FUNCTION rather than a class name — so nothing outside this package spells a
backend, and the measured count stays `{}`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from accountant.extract.adapter import (
    ExtractedRecord,
    Extractor,
    StubExtractor,
    TypedTextExtractor,
    UnavailableExtractor,
)
from accountant.extract.service import MALFORMED, reason_for

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


def _whatever_it_returned(backend: Extractor, data: bytes, mime: str) -> object:
    """`backend.extract(...)`, typed as what we actually know: nothing.

    `Extractor.extract` is ANNOTATED `-> ExtractedRecord`, and an annotation on
    an object somebody else wrote is a promise, not a fact. Declaring the
    result `object` here is what lets `GuardedExtractor.extract` check it —
    pyright narrows an assignment to the annotated return type, so calling
    `extract` directly makes the `isinstance` below provably dead code and
    strict mode is right to reject it. The check is not dead; the annotation is
    just not evidence.
    """
    return backend.extract(data, mime)


class GuardedExtractor:
    """A backend with its failure surface closed. Never raises, never blank.

    THE FAILURE THIS CLOSES, MEASURED
    ---------------------------------
    `pipeline.build_draft` calls `extractor.extract(...)` with nothing around
    it, and `accountant/web/app.py::Handler.handle_one_request` turns any
    escaping exception into HTTP 503 "Something in Accountant Dad broke". So a
    backend that raises produced a page saying the APPLICATION was broken, with
    no field, no reason, and no way for the person to tell a bug from a
    supplier's website being down. Two of the three HTTP outage scenarios in
    `tests/test_extract_outage.py` reach exactly that line.

    `ServiceExtractor` never raises — that is stated at the top of
    `accountant/extract/service.py` and proved ten ways. This class exists for
    the backends that make no such promise: the object a deployment injects
    through `app.configure(extractor=...)`, written by somebody who never read
    that docstring.

    TWO FAILURES, NOT ONE
    ---------------------
        it raised                 any Exception, turned into this outage's own
                                  sentence by `service.reason_for`
        it answered with junk     a backend that returns `None`, or a dict, or
                                  a half-built object. The Protocol says
                                  `ExtractedRecord`; an annotation is a promise
                                  and this is the boundary where promises from
                                  outside stop being trusted.

    The second is why `extract` below types the inner answer as `object`. It is
    not defensive noise: returning `None` on failure is one of the most common
    shapes a third-party client has, and an unchecked `None` reaches
    `record.per_field_source` as an AttributeError two frames later.

    `BaseException` is deliberately NOT caught, for the same reason
    `ServiceExtractor` does not catch it: a KeyboardInterrupt or a SystemExit
    is somebody stopping the process, and answering that with a tidy record
    would fight them.

    THE RECORD NAMES THE BACKEND THAT FAILED. A row that cannot say which one
    was down is not evidence about any of them, so `name` is read off the inner
    backend and falls back to its class name rather than to a constant.
    """

    def __init__(self, backend: Extractor) -> None:
        self._backend = backend
        stated: object = getattr(backend, "name", None)
        self.name = (
            stated if isinstance(stated, str) and stated else type(backend).__name__
        )

    def extract(self, data: bytes, mime: str) -> ExtractedRecord:
        try:
            answer = _whatever_it_returned(self._backend, data, mime)
        except Exception as exc:
            return self.outage(reason_for(exc))
        if not isinstance(answer, ExtractedRecord):
            return self.outage(
                f"{MALFORMED}: the reading backend answered with a "
                f"{type(answer).__name__} instead of a record"
            )
        return answer

    def outage(self, reason: str) -> ExtractedRecord:
        """The all-`not_found` record, built by the one class that builds it.

        `UnavailableExtractor`, not a second shape that resembles it. Two
        places that build an outage record is how one of them ends up without
        a reason on it, which is a silent blank wearing a label.
        """
        return UnavailableExtractor(reason, name=self.name).extract(b"", "")


def guarded(backend: Extractor) -> Extractor:
    """`backend`, unable to raise and unable to answer with a silent blank.

    A FUNCTION and not a class name at the call site, on purpose. Exit 7.1 is
    "no module outside `accountant/extract/` names a concrete backend", and
    `tests/test_adapter_contract.py` counts those names off the AST. A class in
    this package that defines `extract` IS a concrete backend by that scan's
    own derivation, so `accountant/web/app.py` spelling `GuardedExtractor`
    would be a selection site and the count would stop being zero. Calling a
    function costs nothing and keeps the measured number honest.

    Idempotent in effect: guarding an already-guarded backend wraps it twice
    and the inner one simply never fails.
    """
    return GuardedExtractor(backend)

"""One invoice, one model call, five fields. The cheapest possible feasibility test.

WHY THIS EXISTS
---------------
The current reader flattens a page into single-spaced lines and DISCARDS where
each word sat - a closed decision (`project.state.md` Decision 1), taken on a
measurement. It is also the ceiling. On the 62-document ground-truth corpus it
reads total 30, invoice_date 24, invoice_number 20 of 55, tax 20, and PARTY 0
OF 59.

Party is 0 because 38 of 54 supplier names are printed in the letterhead with no
label near them, and no vocabulary can reach an unlabelled name. A real Sleek
Bill invoice adds the other half: two fields on one line, single-spaced, where
the columns cannot be told apart without coordinates. Both holes are
geometry-shaped, and a vision model sees geometry.

THIS SCRIPT ANSWERS ONE QUESTION AND NOTHING ELSE: can a vision model read these
bills at all? One document, one call. If the answer is obviously no, that is
worth knowing before spending 37 more calls, and far before installing a second
Python for PaddleOCR.

WHAT IT IS NOT
--------------
Not a backend. It implements no `Extractor`, touches no file under
`accountant/`, and cannot reach the cage or Tally. Deleting it returns the
repository to exactly its current state.

Not a measurement of accuracy. It prints what the model said beside what the
ground truth says, for a person to look at. `scripts/measure_gemini_corpus.py`
is the scorer, and it runs only after a human has seen this output.

CONFIGURATION, AND WHY THERE IS NO DEFAULT MODEL
------------------------------------------------
`GEMINI_API_KEY` and `GEMINI_MODEL` must BOTH be set. There is no fallback and
no default name. A benchmark whose model can change underneath it is not a
benchmark - which is also why a moving alias like `gemini-flash-latest` is a bad
choice here even though it resolves.

THE KEY IS NEVER PRINTED, never written to a file, never placed in a manifest
and never searched for on disk. This module reads it from the environment and
hands it to one HTTPS request header.

NOTE ON RUNNING IT. `~/.zshrc` is read by INTERACTIVE shells only, so a
non-interactive tool shell does not see exports made there. Run under
`zsh -ic` if the variables appear unset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import pathlib
import sys
import time
import typing
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from accountant.money import group_indian  # noqa: E402

CORPUS = REPO / "data" / "problem1_corpus"
TRUTH = REPO / "artifacts" / "problem1_ground_truth.json"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"

#: The five the owner named. Deliberately not widened: these are the only fields
#: the ground truth carries, so they are the only ones that can be scored later.
FIELDS = ("party", "invoice_date", "invoice_number", "tax", "total")

#: What the model may say about a field. `CONFLICTING` exists so a bill printing
#: two different totals is reported as two candidates rather than resolved by the
#: model - choosing between them is a decision, and a reader does not get to make
#: decisions.
STATUSES = ("ACCEPTED_CANDIDATE", "CONFLICTING", "NOT_DETECTED", "UNREADABLE")

#: The response schema, handed to Gemini's structured-output mechanism rather
#: than asked for in prose. A schema the API enforces is a contract; a schema
#: described in a prompt is a request.
SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        name: {
            "type": "object",
            "properties": {
                "value": {"type": "string", "nullable": True},
                "status": {"type": "string", "enum": list(STATUSES)},
                "page": {"type": "integer", "nullable": True},
                "evidence_text": {"type": "string", "nullable": True},
            },
            "required": ["value", "status", "page", "evidence_text"],
        }
        for name in FIELDS
    },
    "required": list(FIELDS),
}

#: EVERY LINE HERE IS A FAILURE MODE THIS REPOSITORY HAS ALREADY MEASURED.
#:
#: "do not confuse supplier and buyer" - F-03. On a purchase bill the buyer is
#: the owner's own company, so reading one as the supplier files a vendor's
#: ledger under the customer's name and that balance is wrong for ever. MEASURED:
#: 14 corpus documents print `BILL TO` or `CLIENT` and no supplier label.
#:
#: "do not confuse invoice number with GSTIN, HSN, phone, postcode" - MEASURED:
#: a mid-line search once read `FEIN 132932696 GST 895524239` as 8.95 crore of
#: tax, because a registration number ended the line.
#:
#: "never pick between conflicting values" - MEASURED: one bill prints
#: `GRAND TOTAL 1,35,938.36` and `TOTAL PAYABLE 1,35,993.92`. Choosing is a coin
#: toss that moves money.
#:
#: "do not calculate a missing total" - a computed total passes every
#: conservation law by construction, which is the check answering itself.
PROMPT = """You are reading one invoice. Return only the five fields in the schema.

Rules, all of them absolute:
- Use ONLY what is visibly printed on this document.
- NEVER guess. If a field is absent or unreadable, return null with status
  NOT_DETECTED or UNREADABLE.
- If the document prints two different values that could each be the field,
  return status CONFLICTING and put both in evidence_text. Do NOT choose.
- Do NOT calculate a missing total, subtotal or tax from the other numbers.
- Do NOT convert currency.
- The party is the SUPPLIER - who issued this bill and is owed money. It is NOT
  the buyer, customer, client, or the 'bill to' name.
- Do NOT confuse the invoice number with a GSTIN, a tax registration number, an
  HSN/SAC code, a phone number, a postcode, or a purchase-order number.
- Do NOT confuse the tax amount with the total, and do NOT confuse a pre-tax
  subtotal with either.
- Preserve the raw printed text in evidence_text, including its punctuation.
- For tax and total, put the number exactly as printed in `value`.
"""


def json_object(value: object) -> dict[str, object] | None:
    """`value` as a JSON object, or `None` when it is not one.

    `isinstance(value, dict)` on its own leaves the key and value types unknown,
    so every `.get` off the result is unknown too and nothing downstream can be
    checked. Everything narrowed here came out of `json.loads`, where an object
    always has string keys - so that fact is stated once, PROVED by the
    isinstance and not assumed, exactly as `scripts/measure_problem1_corpus.py`
    already does at its own two ground-truth boundaries.

    Public on purpose: `scripts/measure_gemini_five.py` imports it, and a name
    that has to be imported with a `reportPrivateUsage` suppression is a name
    that should not have had the underscore.
    """
    if not isinstance(value, dict):
        return None
    return typing.cast(dict[str, object], value)


def _configured() -> tuple[str, str]:
    """The key and the model, or an explicit refusal. Never a default."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    model = os.environ.get("GEMINI_MODEL", "").strip()
    if not key or not model:
        missing = " and ".join(
            n for n, v in (("GEMINI_API_KEY", key), ("GEMINI_MODEL", model)) if not v
        )
        raise SystemExit(f"GEMINI_NOT_CONFIGURED: {missing} not set")
    return key, model


def _ask(data: bytes, mime: str, key: str, model: str) -> tuple[dict[str, object], int]:
    """One call. Returns the parsed answer and the latency in milliseconds.

    DELEGATES TO `_ask_measured` AND DROPS THE THIRD VALUE. The request is built
    in exactly one place; this keeps the two-value contract its existing callers
    were written against, so adding token counts cost them no edit.
    """
    parsed, latency, _ = _ask_measured(data, mime, key, model)
    return parsed, latency


def _ask_measured(
    data: bytes, mime: str, key: str, model: str
) -> tuple[dict[str, object], int, dict[str, int]]:
    """One call. Returns the answer, the latency in ms, and the API's own token counts.

    `usageMetadata` is what Google reports for the call - typically
    `promptTokenCount`, `candidatesTokenCount` and `totalTokenCount`. It was
    being parsed and thrown away. Counting tokens locally would be a second
    opinion about somebody else's meter, so only what the API states is kept,
    and an absent field yields an empty dict rather than a zero that looks
    measured.

    THIS RETURNS TOKENS, NOT MONEY. No price table is configured in this
    repository, and multiplying a token count by a rate nobody supplied would
    be inventing a figure.
    """
    import base64

    body = json.dumps(
        {
            "contents": [
                {
                    "parts": [
                        {"text": PROMPT},
                        {
                            "inline_data": {
                                "mime_type": mime,
                                "data": base64.b64encode(data).decode(),
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": SCHEMA,
                "temperature": 0,
            },
        }
    ).encode()
    # S310 asks whether this URL could carry a `file:` or custom scheme. It
    # cannot: `ENDPOINT` is a module constant beginning with a literal
    # "https://" and only the model NAME is interpolated. `tallyio/real.py`
    # documents the same shape for the same reason. The check is suppressed
    # with the argument written down, not removed.
    request = urllib.request.Request(  # noqa: S310
        f"{ENDPOINT}/{model}:generateContent",
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        answer = json.load(response)
    latency = int((time.monotonic() - started) * 1000)
    text = answer["candidates"][0]["content"]["parts"][0]["text"]
    parsed: dict[str, object] = json.loads(text)
    reported = json_object(answer.get("usageMetadata"))
    usage: dict[str, int] = (
        {k: v for k, v in reported.items() if isinstance(v, int)}
        if reported is not None
        else {}
    )
    return parsed, latency, usage


def _expected(truth: dict[str, object], field: str) -> str:
    """What the ground truth says, flattened to one readable line."""
    slot = json_object(truth.get(field))
    if slot is None:
        return "(no ground truth)"
    status = str(slot.get("status", "?"))
    value = slot.get("value")
    nested = json_object(value)
    if nested is not None:
        value = nested.get("text") or nested.get("paise")
    return f"{status}: {value}" if value is not None else status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", nargs="?", help="file name inside the corpus")
    args = parser.parse_args()

    key, model = _configured()

    documents = sorted(p for p in CORPUS.iterdir() if p.is_file())
    if not documents:
        raise SystemExit(f"no documents in {CORPUS}")
    path = next((p for p in documents if p.name == args.document), documents[0])

    data = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    truth_all: dict[str, dict[str, object]] = json.loads(TRUTH.read_text())
    truth = truth_all.get(path.stem) or truth_all.get(path.name) or {}

    print(f"document : {path.name}")
    print(f"sha256   : {hashlib.sha256(data).hexdigest()[:16]}...")
    # `group_indian`, not `:,`. This is a BYTE COUNT and not a rupee figure, so
    # `format_inr` would be the wrong tool - but the repository's grouping
    # convention is Indian for every number a person here reads, and `:,` is
    # western. `tests/test_inr_grouping.py` scans `scripts/` since 2026-08-13
    # and flags any comma in a format spec, which is how this line was found.
    print(f"mime     : {mime}   bytes: {group_indian(str(len(data)))}")
    print(f"model    : {model}\n")

    try:
        answer, latency = _ask(data, mime, key, model)
    except Exception as problem:
        print(f"CALL FAILED: {type(problem).__name__}: {str(problem)[:300]}")
        return 1

    print(f"call succeeded in {latency} ms\n")
    print(f"{'field':16} {'GEMINI':34} {'GROUND TRUTH':34}")
    print("-" * 86)
    for field in FIELDS:
        got = json_object(answer.get(field))
        said = "(missing from response)"
        if got is not None:
            value = got.get("value")
            said = (
                f"{got.get('status')}: {value}"
                if value is not None
                else str(got.get("status"))
            )
        print(f"{field:16} {said[:33]:34} {_expected(truth, field)[:33]:34}")

    print("\nevidence the model quoted:")
    for field in FIELDS:
        got = json_object(answer.get(field))
        if got is not None and got.get("evidence_text"):
            print(f"  {field:16} {str(got['evidence_text'])[:64]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

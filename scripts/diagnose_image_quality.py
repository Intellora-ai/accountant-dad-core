"""Can preprocessing recover the 21 documents the engine reads nothing from?

DIAGNOSIS ONLY. This script changes no production code and no production
behaviour. It measures the pictures, tries a fixed set of deterministic local
transformations on them, and counts what the engine returns for each. Nothing it
learns is wired into the reading path by this file.

WHY
---
MEASURED by `scripts/classify_unmatched_slots.py` over the 60 real image
documents: 105 of 300 field slots are IMAGE_QUALITY_FAILURE - 21 documents where
fewer than 5 OCR word rows carry any characters at all. That is 35% of the
corpus, and no label work of any kind touches it. Either the pixels can be
improved or those documents are unreadable; this script is how that question
gets an answer instead of an opinion.

PILLOW ONLY. The approved runtime dependencies are `pypdf`, `pytesseract` and
`Pillow`, and a fourth fails a test. So every transformation here is one Pillow
already provides: `convert`, `resize`, `point`, `ImageEnhance`, `ImageFilter`.
No OpenCV, no numpy, no network, no second OCR engine.

WHAT A "WIN" WOULD HAVE TO LOOK LIKE, stated before the numbers arrive so the
bar cannot move afterwards: a transformation must raise the count of legible
word rows AND produce at least one recognisable LABEL. More characters is not a
result - `scripts/measure_field_slots.py` counts fields, not glyphs, and an
engine coaxed into emitting more noise has made the problem worse.
"""

from __future__ import annotations

import argparse
import io
import pathlib
import statistics
import sys
import time
from collections.abc import Callable, Iterable

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from PIL import Image, ImageEnhance, ImageFilter  # noqa: E402

from accountant.extract.pagereader import read_lines  # noqa: E402
from accountant.labels import (  # noqa: E402
    DATE_LABEL,
    NET_LABELS,
    PARTY_LABELS,
    TAX_WHOLE,
    TOTAL_LABELS,
)

PICTURES = {".jpg", ".jpeg", ".png"}
LEGIBLE_ENOUGH = 5

EVERY_LABEL: tuple[str, ...] = tuple(
    {*TOTAL_LABELS, *TAX_WHOLE, *NET_LABELS, *PARTY_LABELS, *DATE_LABEL}
)


def documents(limit: int) -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for folder in ("real_invoices_indian", "real_invoices"):
        here = REPO / "data" / folder
        if here.is_dir():
            found.extend(
                p for p in sorted(here.iterdir()) if p.suffix.lower() in PICTURES
            )
    return found[:limit] if limit else found


def _legible(data: bytes) -> tuple[int, list[str]]:
    """Word rows carrying characters, and the words themselves."""
    try:
        lines = read_lines(data, deadline_seconds=30.0)
    except Exception:
        return 0, []
    words = [w.text for line in lines for w in line if w.text.strip()]
    return len(words), words


def _labels_in(words: list[str]) -> list[str]:
    upper = " ".join(words).upper()
    return sorted({label for label in EVERY_LABEL if label in upper})


def _greys(picture: Image.Image) -> list[float]:
    """Every pixel of a single-band picture, as numbers.

    THE ONE IGNORE IN THIS FILE, and it is Pillow's stub and not this code.
    `Image.getdata()` is declared to return `_imaging.ImagingCore`, which the
    shipped `_imaging.pyi` gives a `__getitem__` and no `__iter__` - so a type
    checker will not accept it as an iterable even though `list()` walks it at
    runtime, which is what Pillow documents it for. Kept to this one line so
    that no other unknown can hide behind it.
    """
    band: Iterable[float] = picture.getdata()  # pyright: ignore[reportAssignmentType]
    return list(band)


def _measure(picture: Image.Image) -> dict[str, float]:
    """Brightness, contrast and a blur proxy - all from Pillow alone.

    THE BLUR PROXY IS AN EDGE-ENERGY RATIO, not a Laplacian variance, because
    the usual Laplacian needs numpy and numpy is not an approved dependency.
    `ImageFilter.FIND_EDGES` then the mean of the result is the same idea with
    the tools available: a sharp page has strong edges, a blurred one does not.
    It is a PROXY and is reported as one - the number is comparable between two
    images here and is not a physical measurement of anything.
    """
    grey: Image.Image = picture.convert("L")
    pixels: list[float] = _greys(grey)
    edges: list[float] = _greys(grey.filter(ImageFilter.FIND_EDGES))
    low, high = grey.getextrema()
    if isinstance(low, tuple) or isinstance(high, tuple):
        # `getextrema` returns one (low, high) pair for a single-band image and
        # one pair PER BAND for a multi-band one, so its declared type is a
        # union and subtraction is not defined across it. `grey` is mode "L" and
        # can only ever be the first shape. RAISED, not asserted, and not
        # silently defaulted: `python -O` strips an assert, and a measurement
        # that quietly substitutes a zero for a number it could not compute is
        # the failure this whole file exists to avoid.
        raise TypeError(f"expected a single-band extrema pair, got {low!r} {high!r}")
    return {
        "brightness": statistics.fmean(pixels),
        "contrast_spread": float(high - low),
        "stdev": statistics.pstdev(pixels) if len(pixels) > 1 else 0.0,
        "edge_energy": statistics.fmean(edges),
    }


def _as_bytes(picture: Image.Image) -> bytes:
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


def _point(picture: Image.Image, lut: Callable[[int], float]) -> Image.Image:
    """`Image.point` into mode "1", with the lookup function's type stated.

    THE SECOND OF THIS FILE'S THREE IGNORES, and again Pillow's stub. `point`
    unions its `lut` parameter with `NumpyArray`, a name `PIL/_typing.py` only
    binds when numpy is importable - numpy is not one of this project's three
    approved dependencies, so the name is unbound and the entire parameter reads
    as unknown. Declaring `lut` here is what makes the pixel value an `int`
    again at every call site.
    """
    return picture.point(lut, mode="1")  # pyright: ignore[reportUnknownMemberType]


def _resize(picture: Image.Image, times: int) -> Image.Image:
    """`Image.resize` by a whole-number factor, LANCZOS, as every caller wants.

    THE THIRD IGNORE, and the same hole: `resize` unions its `size` parameter
    with the unbound `NumpyArray` too.
    """
    return picture.resize(  # pyright: ignore[reportUnknownMemberType]
        (picture.width * times, picture.height * times), Image.Resampling.LANCZOS
    )


def _threshold_mean(picture: Image.Image) -> Image.Image:
    """Split black from white at the picture's OWN mean grey, not at a fixed 128.

    Was an immediately-invoked lambda closing over the mean. Same two calls to
    `convert("L")` in the same order and the same threshold; it is a `def` only
    so the pixel value it is handed has a name and therefore a type.
    """
    mean = statistics.fmean(_greys(picture.convert("L")))
    return _point(picture.convert("L"), lambda v: 255 if v > mean else 0)


#: The candidate transformations, one at a time and never blindly combined.
#: Each is a name and a function from an image to an image.
def _trials() -> dict[str, Callable[[Image.Image], Image.Image]]:
    return {
        "original": lambda im: im,
        "grayscale": lambda im: im.convert("L"),
        "contrast_x2": lambda im: ImageEnhance.Contrast(im.convert("L")).enhance(2.0),
        "upscale_x2": lambda im: _resize(im.convert("L"), 2),
        "upscale_x3": lambda im: _resize(im.convert("L"), 3),
        "threshold_128": lambda im: _point(
            im.convert("L"), lambda v: 255 if v > 128 else 0
        ),
        "threshold_mean": _threshold_mean,
        "sharpen": lambda im: im.convert("L").filter(ImageFilter.SHARPEN),
        "denoise_median": lambda im: im.convert("L").filter(
            ImageFilter.MedianFilter(3)
        ),
        "upscale_x2_contrast": lambda im: ImageEnhance.Contrast(
            _resize(im.convert("L"), 2)
        ).enhance(2.0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--sample", type=int, default=6, help="documents to trial")
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=REPO / "artifacts" / "problem1_image_quality_diagnosis.md",
    )
    args = parser.parse_args()

    report: list[str] = []
    poor: list[tuple[pathlib.Path, dict[str, float], int]] = []

    print(
        "=== step 1: which documents read nothing, and what do their pixels look like"
    )
    for path in documents(args.limit):
        data = path.read_bytes()
        rows, _ = _legible(data)
        try:
            with Image.open(io.BytesIO(data)) as picture:
                picture.load()
                stats = _measure(picture)
                size = f"{picture.width}x{picture.height}"
                mode = picture.mode
        except Exception as exc:
            report.append(
                f"| {path.name} | UNOPENABLE ({type(exc).__name__}) | | | | |"
            )
            poor.append((path, {}, 0))
            continue
        if rows < LEGIBLE_ENOUGH:
            poor.append((path, stats, rows))
            report.append(
                f"| {path.name} | {size} | {mode} | {len(data) // 1024}KB | {rows} | "
                f"{stats['brightness']:.0f} | {stats['stdev']:.1f} | "
                f"{stats['contrast_spread']:.0f} | {stats['edge_energy']:.2f} |"
            )

    print(f"documents reading fewer than {LEGIBLE_ENOUGH} legible rows: {len(poor)}")

    print("\n=== step 2: try each method on a sample, one method at a time")
    sample = poor[: args.sample]
    trials = _trials()
    results: list[str] = []
    best: dict[str, int] = dict.fromkeys(trials, 0)

    for path, _stats, before in sample:
        data = path.read_bytes()
        print(f"\n  {path.name}  (before: {before} legible rows)")
        for name, transform in trials.items():
            started = time.monotonic()
            try:
                with Image.open(io.BytesIO(data)) as picture:
                    picture.load()
                    changed = transform(picture)
                    rows, words = _legible(_as_bytes(changed))
            except Exception as exc:
                print(f"    {name:22} FAILED {type(exc).__name__}")
                continue
            elapsed = (time.monotonic() - started) * 1000
            labels = _labels_in(words)
            best[name] += rows
            flag = "  <-- LABELS" if labels else ""
            print(
                f"    {name:22} rows {rows:5}  labels {len(labels):2}  "
                f"{elapsed:6.0f}ms{flag}"
            )
            results.append(
                f"| {path.name} | {name} | {before} | {rows} | "
                f"{', '.join(labels) or '-'} | {elapsed:.0f} |"
            )

    print("\n=== step 3: total legible rows per method, across the sample")
    for name, total in sorted(best.items(), key=lambda kv: -kv[1]):
        print(f"  {name:22} {total}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "# Problem 1 - can preprocessing recover the unreadable documents?\n\n"
        "DIAGNOSIS ONLY. No production code changed.\n\n"
        f"Documents with fewer than {LEGIBLE_ENOUGH} legible OCR rows: "
        f"**{len(poor)}**\n\n"
        "| document | size | mode | bytes | rows | brightness | stdev |"
        " spread | edges |\n"
        "|---|---|---|---|---|---|---|---|---|\n" + "\n".join(report) + "\n\n"
        "## Trials, one method at a time\n\n"
        "| document | method | rows before | rows after | labels recovered | ms |\n"
        "|---|---|---|---|---|---|\n" + "\n".join(results) + "\n\n"
        "## Total legible rows per method across the sample\n\n"
        + "\n".join(
            f"- `{n}` {t}" for n, t in sorted(best.items(), key=lambda kv: -kv[1])
        )
        + "\n"
    )
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

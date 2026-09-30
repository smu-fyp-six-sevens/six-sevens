"""
Run the PDF image-manipulation check over documents on disk and show every
step it took, without needing the API or the frontend.

The detector itself logs its reasoning at DEBUG through
`fraud_detection.image_manipulation`; this script turns that on, prints it
interleaved with a readable per-document report, and summarises at the end. So
the `--trace` output you see here is the same trace you get from the API once
that logger is at DEBUG — nothing is reconstructed for the CLI's benefit.

Usage (from backend/):
    python -m scripts.inspect_image_manipulation FILE.pdf
    python -m scripts.inspect_image_manipulation DIR --recursive
    python -m scripts.inspect_image_manipulation FILE.pdf --trace
    python -m scripts.inspect_image_manipulation DIR -r --only-findings
    python -m scripts.inspect_image_manipulation FILE.pdf --dump-regions out/
    python -m scripts.inspect_image_manipulation FILE.pdf --json

Tolerance overrides, for probing how sensitive a verdict is:
    --dpi-ratio 1.5 --detail-ratio 0.30 --min-area 800 --bpp-ratio 2.0

Exit status is 1 if any document produced a finding, 0 otherwise, so this can
gate a script.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from app.fraud_detection.image_manipulation import (
    ImageManipulationResult,
    Tolerances,
    check_image_manipulation,
)

LOGGER_NAME = "fraud_detection.image_manipulation"

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
RED, YELLOW, GREEN, GREY = "\033[31m", "\033[33m", "\033[32m", "\033[90m"

SEVERITY_COLOUR = {"high": RED, "medium": YELLOW, "low": GREY}


def _colour(text: str, code: str, enabled: bool) -> str:
    return f"{code}{text}{RESET}" if enabled else text


# --- reporting ---------------------------------------------------------------

def print_regions(result: ImageManipulationResult, colour: bool) -> None:
    """
    Step 1 (AC1): what was found and how each was measured.

    Every column here is an input to a later decision, so a surprising verdict
    can usually be explained from this table alone.
    """
    print(f"\n  {BOLD if colour else ''}1. Image regions found (AC1){RESET if colour else ''}")
    if not result.regions:
        print("     none — this document embeds no raster images at all")
        return

    header = (
        f"     {'pg':>2}  {'xref':>5}  {'pixels':>11}  {'drawn (pt)':>13}  "
        f"{'area':>8}  {'dpi':>7}  {'codec':<6}  {'bytes/px':>9}  "
        f"{'qsig':<13}  {'detail':>7}  {'pages':>5}  comparative use"
    )
    print(_colour(header, DIM, colour))
    for region in result.regions:
        width = region.bbox[2] - region.bbox[0]
        height = region.bbox[3] - region.bbox[1]
        detail = "n/a" if region.detail_ratio is None else f"{region.detail_ratio:.3f}"
        # Why a region was excluded matters more than the fact of it: a size
        # exclusion and a repetition exclusion have different consequences.
        if not region.comparable:
            verdict = "no (under size floor)"
        elif region.pages_used_on > 1:
            verdict = "baseline only (repeats)"
        else:
            verdict = "yes"
        print(
            f"     {region.page:>2}  {region.xref:>5}  "
            f"{region.pixel_width:>5}x{region.pixel_height:<5}  "
            f"{width:>6.0f}x{height:<6.0f}  {width * height:>8.0f}  "
            f"{region.effective_dpi:>7.1f}  {region.codec:<6}  "
            f"{region.bytes_per_pixel:>9.5f}  "
            f"{(region.quantisation_signature or '-'):<13}  {detail:>7}  "
            f"{region.pages_used_on:>5}  {verdict}"
        )


def print_gate(result: ImageManipulationResult, colour: bool) -> None:
    """Step 2: whether the comparative checks could run at all (AC7)."""
    print(f"\n  {BOLD if colour else ''}2. Comparability gate (AC7){RESET if colour else ''}")
    sized = [r for r in result.regions if r.comparable]
    distinct = {r.xref for r in sized}
    print(f"     {len(result.regions)} placement(s) of {len({r.xref for r in result.regions})} "
          f"distinct asset(s)")
    print(f"     {len(sized)} placement(s) clear the "
          f"{result.tolerances.min_region_area_pt2:.0f}pt2 size floor")
    print(f"     -> {len(distinct)} distinct comparable asset(s); "
          f"{result.tolerances.min_comparable_regions} needed to compare")
    if result.unable_to_assess:
        print(_colour(f"     UNABLE TO ASSESS: {result.unable_to_assess_reason}", YELLOW, colour))
    elif len(distinct) < result.tolerances.min_comparable_regions:
        print("     comparative checks skipped, but the intrinsic check found "
              "something — so this is a finding, not Unable to Assess")
    else:
        print("     comparative checks can run")


def print_findings(result: ImageManipulationResult, colour: bool) -> None:
    """Step 3: the indicators themselves (AC2-AC5)."""
    print(f"\n  {BOLD if colour else ''}3. Indicators (AC2-AC5){RESET if colour else ''}")
    if result.unable_to_assess and not result.indicators:
        print("     n/a — nothing was assessed")
        return
    if not result.indicators:
        print(_colour("     none — every signal agreed within tolerance", GREEN, colour))
        return

    for n, indicator in enumerate(result.indicators, 1):
        tint = SEVERITY_COLOUR.get(indicator.severity, "")
        label = f"{indicator.indicator} ({indicator.severity})"
        print(f"     {n}. {_colour(label, tint + BOLD, colour)}")
        print(f"        page {indicator.page + 1}, bbox "
              f"({indicator.bbox[0]:.0f}, {indicator.bbox[1]:.0f}) -> "
              f"({indicator.bbox[2]:.0f}, {indicator.bbox[3]:.0f})   [AC4]")
        print(f"        detected : {indicator.detected}")
        print(f"        expected : {indicator.expected}")
        print(f"        deviation: {indicator.deviation}")
        print(f"        {_colour(indicator.summary, DIM, colour)}")
        print(f"        evidence : {json.dumps(indicator.evidence, sort_keys=True)}")


def verdict_of(result: ImageManipulationResult) -> str:
    if result.indicators:
        return "FINDING"
    return "UNABLE" if result.unable_to_assess else "CLEAN"


# --- region dumping ----------------------------------------------------------

def dump_regions(pdf: Path, result: ImageManipulationResult, out_dir: Path) -> None:
    """
    Writes each embedded asset out as its own file, plus a crop of the page as
    rendered. Lets you look at what a number is actually describing — a detail
    ratio of 0.06 means little until you see the soft logo it came from.
    """
    import pymupdf

    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(pdf)
    try:
        seen: set[int] = set()
        for region in result.regions:
            if region.xref in seen:
                continue
            seen.add(region.xref)
            extracted = doc.extract_image(region.xref)
            stem = f"{pdf.stem}_xref{region.xref}"
            asset = out_dir / f"{stem}.{extracted['ext']}"
            asset.write_bytes(extracted["image"])

            # The same asset as the page actually renders it, at print scale.
            page = doc[region.page]
            clip = pymupdf.Rect(*region.bbox)
            page.get_pixmap(dpi=200, clip=clip).save(out_dir / f"{stem}_asdrawn.png")
            print(f"     wrote {asset.name} and {stem}_asdrawn.png")
    finally:
        doc.close()


# --- driver ------------------------------------------------------------------

def collect_pdfs(paths: list[str], recursive: bool) -> list[Path]:
    pdfs: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            pdfs += sorted(path.rglob("*.pdf") if recursive else path.glob("*.pdf"))
        elif path.is_file():
            pdfs.append(path)
        else:
            print(f"skipping {path}: not found", file=sys.stderr)
    return pdfs


def build_tolerances(args: argparse.Namespace) -> Tolerances:
    overrides = {
        key: value
        for key, value in (
            ("dpi_ratio", args.dpi_ratio),
            ("bytes_per_pixel_ratio", args.bpp_ratio),
            ("min_region_area_pt2", args.min_area),
            ("min_detail_ratio", args.detail_ratio),
            ("min_comparable_regions", args.min_regions),
        )
        if value is not None
    }
    if overrides:
        print(f"tolerance overrides: {overrides}\n")
    return Tolerances(**overrides)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("paths", nargs="+", help="PDF files, or directories of them")
    parser.add_argument("-r", "--recursive", action="store_true",
                        help="descend into subdirectories")
    parser.add_argument("-t", "--trace", action="store_true",
                        help="also show the detector's own DEBUG reasoning, "
                             "interleaved with the report")
    parser.add_argument("--only-findings", action="store_true",
                        help="print a full report only for documents with indicators")
    parser.add_argument("--dump-regions", metavar="DIR",
                        help="write each embedded asset, and a render of it as drawn, "
                             "into DIR for eyeballing")
    parser.add_argument("--json", action="store_true",
                        help="emit the full result objects as JSON instead of a report")
    parser.add_argument("--no-colour", action="store_true")
    parser.add_argument("--dpi-ratio", type=float)
    parser.add_argument("--bpp-ratio", type=float)
    parser.add_argument("--min-area", type=float)
    parser.add_argument("--detail-ratio", type=float)
    parser.add_argument("--min-regions", type=int)
    args = parser.parse_args()

    colour = not args.no_colour and sys.stdout.isatty()

    if args.trace:
        # The detector's own logger, not a reimplementation of it.
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("       trace | %(message)s"))
        detector_log = logging.getLogger(LOGGER_NAME)
        detector_log.setLevel(logging.DEBUG)
        detector_log.addHandler(handler)
        detector_log.propagate = False

    pdfs = collect_pdfs(args.paths, args.recursive)
    if not pdfs:
        print("no PDFs to inspect", file=sys.stderr)
        return 2

    tolerances = build_tolerances(args)
    results: list[tuple[Path, ImageManipulationResult]] = []

    for pdf in pdfs:
        result = check_image_manipulation(pdf.stem, pdf.read_bytes(), tolerances)
        results.append((pdf, result))

        if args.json or (args.only_findings and not result.indicators):
            continue

        verdict = verdict_of(result)
        tint = {"FINDING": RED, "UNABLE": YELLOW, "CLEAN": GREEN}[verdict]
        print("\n" + "=" * 100)
        print(f"{_colour(pdf.name, BOLD, colour)}   "
              f"{_colour(verdict, tint + BOLD, colour)}   "
              f"({result.pages_checked} page(s))")
        print("=" * 100)
        print_regions(result, colour)
        print_gate(result, colour)
        print_findings(result, colour)
        if args.dump_regions:
            print(f"\n  {BOLD if colour else ''}4. Region dump{RESET if colour else ''}")
            dump_regions(pdf, result, Path(args.dump_regions).expanduser())

    if args.json:
        print(json.dumps(
            {str(pdf): result.model_dump() for pdf, result in results},
            indent=2, sort_keys=True,
        ))
    else:
        tally = Counter(verdict_of(result) for _, result in results)
        print("\n" + "=" * 100)
        print(f"{len(results)} document(s): " + "  ".join(
            f"{name}={tally.get(name, 0)}" for name in ("FINDING", "CLEAN", "UNABLE")
        ))
        for pdf, result in results:
            if result.indicators:
                kinds = ", ".join(sorted({f"{i.indicator} ({i.severity})"
                                          for i in result.indicators}))
                print(f"  {_colour('FINDING', RED, colour)}  {pdf.name}: {kinds}")

    return 1 if any(result.indicators for _, result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())

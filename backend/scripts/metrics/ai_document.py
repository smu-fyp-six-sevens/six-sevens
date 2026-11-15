"""
C011's AI-generated proof of address (mastersheet: Proof of Address, POR_NRIC_20260925.pdf, "AI").

The mastersheet lists it as pending: no generator makes it, because a proof-of-address row with the NRIC template
reuses the person's NRIC and writes no file of its own. This builds that file.

It is a stand-in, not the output of an image model: nothing here can run one. The person's NRIC card is drawn by
the usual generator, then made to carry the traces the AI-generation check reads
(app/fraud_detection/authenticity/ai_generation.py), the way a picture from an image generator pasted into a PDF
would:

  - the card sits on a smooth, noise-free background, with none of the grain a camera or scanner adds;
  - the fixed 2px pattern that a generator's upsampling layers leave is added across the picture;
  - the picture is 1024 x 1024, a generator's preset size;
  - the PDF names an image generator as its creator and producer.

So it tests whether the pipeline flags a document with those traces. It says nothing about how the check does on
real generator output, which has to be tested with real samples.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import openpyxl
import pymupdf
from PIL import Image, ImageFilter

from doc_gen_scripts import truth
from doc_gen_scripts.case_facts import seeded_rng
from doc_gen_scripts.nric_passport.nric_generator import build_persona, card_regions, generate_nric_card

from .frauds import DIMENSION_TOOLS

CASE_ID = "C011"
SIDE = 1024
GENERATOR = "DALL-E 3 (OpenAI image generation)"
NOTE = (
    "Proof of address presented as a picture of the holder's NRIC, carrying the traces of an AI image generator: "
    "no sensor noise, a 2px upsampling pattern, a 1024x1024 preset size, and a generator named as the PDF's "
    "creator. Simulated traces, not real generator output."
)


def _rows(ws) -> list[dict[str, Any]]:
    header = [c.value for c in ws[1]]
    return [dict(zip(header, r)) for r in ws.iter_rows(min_row=2, values_only=True) if any(r)]


def _row(mastersheet: Path) -> tuple[dict[str, Any], dict[str, Any], str] | None:
    """(the proof-of-address row marked AI, that person's NRIC-Passport row, their address), if the sheet has one."""
    wb = openpyxl.load_workbook(str(mastersheet), data_only=True)
    target = next(
        (r for r in _rows(wb["Proof of Address"])
         if str(r.get("Case ID (auto)")) == CASE_ID and str(r.get("Detection Dimension") or "").upper().startswith("AI")),
        None,
    )
    if target is None:
        return None
    individual = str(target.get("Individual ID"))
    identity = next((r for r in _rows(wb["NRIC-Passport"]) if str(r.get("Individual ID")) == individual), {})
    person = next((r for r in _rows(wb["Individuals"]) if str(r.get("Individual ID")) == individual), {})
    # A passport holder's row has no NRIC card of its own; the card is drawn from the same identity facts.
    return target, {**identity, "Individual ID": individual}, str(person.get("Residential Address") or "")


def _background(rng: np.random.Generator) -> np.ndarray:
    """A soft, out-of-focus surface: low-frequency texture only, and no grain."""
    fy, fx = np.meshgrid(np.fft.fftfreq(SIDE), np.fft.fftfreq(SIDE), indexing="ij")
    base = np.real(np.fft.ifft2(np.fft.fft2(rng.normal(size=(SIDE, SIDE))) / (np.hypot(fy, fx) + 1 / SIDE) ** 2.0))
    gray = (base - base.min()) / np.ptp(base) * 70 + 150
    return np.stack([gray, gray * 0.97 + 4, gray * 0.92 + 9], axis=-1)


def _picture(card_pdf: bytes, seed: str) -> bytes:
    rng = np.random.default_rng(seeded_rng(f"{seed}|ai-document").randrange(2**32))
    canvas = Image.fromarray(_background(rng).astype(np.uint8))

    regions = card_regions()
    with pymupdf.open(stream=card_pdf, filetype="pdf") as doc:
        page = doc[0]
        top = 70
        for side in ("front", "back"):
            x0, y0, x1, y1 = regions[side]
            clip = pymupdf.Rect(x0, page.rect.height - y1, x1, page.rect.height - y0)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(3, 3), clip=clip, alpha=False)
            card = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            scale = min((SIDE - 180) / card.width, 400 / card.height)
            card = card.resize((int(card.width * scale), int(card.height * scale)), Image.LANCZOS)
            canvas.paste(card, ((SIDE - card.width) // 2, top))
            top += card.height + 60

    # A generator's output is soft and clean, then carries its upsampling layers' fixed pattern.
    rgb = np.asarray(canvas.filter(ImageFilter.GaussianBlur(0.6)), dtype=np.float64)
    cell = rng.normal(0, 1, (2, 2))
    cell -= cell.mean()
    yy, xx = np.mgrid[0:SIDE, 0:SIDE]
    rgb = np.clip(rgb + 1.6 * cell[yy % 2, xx % 2][..., None], 0, 255)

    buffer = io.BytesIO()
    Image.fromarray(rgb.astype(np.uint8)).save(buffer, "PNG")
    return buffer.getvalue()


def build(case_dir: Path, mastersheet: Path) -> list[str]:
    """Writes the document and its truth file into `case_dir`. Returns what went wrong, if anything."""
    found = _row(mastersheet)
    if found is None:
        return []  # this sheet has no AI-generated row, so there is nothing to build
    target, identity, address = found
    filename = str(target.get("Filename") or "POR_NRIC_AI.pdf")
    out = case_dir / filename
    scratch = case_dir / f".{out.stem}_ai_scratch.pdf"
    try:
        persona = build_persona(identity, address)
        persona["name"] = str(target.get("Individual Name (auto)") or persona["name"])
        persona["nric_no"] = str(target.get("NRIC/Passport No. (auto)") or persona["nric_no"])
        generate_nric_card(str(scratch), redact_photo=True, **persona)
        picture = _picture(scratch.read_bytes(), str(identity.get("Individual ID")))

        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        page.insert_image(pymupdf.Rect(47.5, 60, 547.5, 560), stream=picture)
        doc.set_metadata({"creator": GENERATOR, "producer": GENERATOR, "title": "Proof of address"})
        doc.save(str(out))
        doc.close()

        previous = os.environ.get("DOCGEN_TRUTH")
        os.environ["DOCGEN_TRUTH"] = "1"
        try:
            truth.record(out, "nric", truth.nric(persona, "single"), derived={"document_side"},
                         check_pdf=scratch, also_acceptable=["proof_of_address"])
        finally:
            if previous is None:
                os.environ.pop("DOCGEN_TRUTH", None)
            else:
                os.environ["DOCGEN_TRUTH"] = previous
        record = truth.truth_path(out)
        payload = json.loads(record.read_text())
        payload["fraud_expected"] = True
        payload["tampering"].append({
            "dimension": "AI-Generated",
            "defect_type": "ai_generated",
            "expected_tools": list(DIMENSION_TOOLS["AI-Generated"]),
            "description": NOTE,
            "edits": [],
        })
        record.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    except Exception as exc:
        return [f"{CASE_ID} {filename}: AI-generated document not built ({exc})"]
    finally:
        scratch.unlink(missing_ok=True)
    return []

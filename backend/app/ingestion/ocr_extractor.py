# ocr_extractor.py
from __future__ import annotations

from typing import cast

import numpy as np
from PIL import Image
from rapidocr import RapidOCR
from rapidocr.utils.output import RapidOCROutput


reader = RapidOCR()


def extract_image_with_ocr(image, page_number: int = 1) -> dict:
    if isinstance(image, Image.Image):
        image = np.array(image.convert("RGB"))

    # __call__'s declared return type covers every intermediate pipeline
    # stage's output (for callers that skip det/cls/rec); with none of those
    # skipped here, it always actually returns the full RapidOCROutput.
    result = cast(RapidOCROutput, reader(image))

    elements = []

    if result.boxes is not None:
        # boxes/txts/scores are produced together by the same detection pass.
        assert result.txts is not None and result.scores is not None
        for text, confidence in zip(result.txts, result.scores):
            elements.append({
                "text": text,
                "confidence": float(confidence),
                "extraction_method": "ocr"
            })

    return {
        "page_number": page_number,
        "text": " ".join(e["text"] for e in elements),
        "elements": elements
    }
# ocr_extractor.py
from __future__ import annotations

from typing import cast

import numpy as np
from PIL import Image
from rapidocr import RapidOCR
from rapidocr.utils.output import RapidOCROutput


class _LazyReader:
    """
    Defers RapidOCR()'s eager ONNX model loading (visible as several-second
    startup log lines) until the first real OCR call, instead of paying that
    cost at import time. That import-time cost is fine for a long-running
    process, but every one of Cloud Run's scale-to-zero cold starts would
    otherwise pay it even for a request that never touches OCR (e.g. a
    native-text PDF). Kept as a plain callable (not a function reassigning a
    module global) so tests can still do
    `monkeypatch.setattr(ocr_extractor, "reader", ...)` to replace it outright.
    """

    def __init__(self) -> None:
        self._reader: RapidOCR | None = None

    def __call__(self, image):
        if self._reader is None:
            self._reader = RapidOCR()
        return self._reader(image)


reader = _LazyReader()


def _bbox(box) -> list[float]:
    """RapidOCR's four corner points as [x0, y0, x1, y1]."""
    xs = [float(point[0]) for point in box]
    ys = [float(point[1]) for point in box]
    return [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)]


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
        for box, text, confidence in zip(result.boxes, result.txts, result.scores):
            elements.append({
                "text": text,
                "confidence": float(confidence),
                "extraction_method": "ocr",
                # Where the line sits, [x0, y0, x1, y1] in the image's pixels from the top-left.
                "bbox": _bbox(box),
            })

    return {
        "page_number": page_number,
        "text": " ".join(e["text"] for e in elements),
        "elements": elements
    }
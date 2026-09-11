# ocr_extractor.py
from __future__ import annotations

import numpy as np
from PIL import Image
from rapidocr import RapidOCR


reader = RapidOCR()


def extract_image_with_ocr(image, page_number: int = 1) -> dict:
    if isinstance(image, Image.Image):
        image = np.array(image.convert("RGB"))

    result = reader(image)

    elements = []

    if result.boxes is not None:
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
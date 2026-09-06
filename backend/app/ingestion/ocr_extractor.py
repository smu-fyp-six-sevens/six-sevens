# ocr_extractor.py
from __future__ import annotations

import numpy as np
from PIL import Image
import easyocr


reader = easyocr.Reader(["en"], gpu=False)


def extract_image_with_ocr(image, page_number: int = 1) -> dict:
    if isinstance(image, Image.Image):
        image = np.array(image)

    results = reader.readtext(image, detail=1)

    elements = []

    for bbox, text, confidence in results:
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
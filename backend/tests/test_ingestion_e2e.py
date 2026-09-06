import json
import math
from pathlib import Path

import pytest

from app.ingestion.ingestion_service import ingest_document


TEST_CASES = [
    ("test_files/C002_Constitution.pdf", "application/pdf"),
    ("test_files/Certificate_of_Incorporation.pdf", "application/pdf"),
    ("test_files/ocbc_sample.png", "image/png"),
    ("test_files/test_payslip.png", "image/png"),
    # add more once you have them, e.g.:
    # ("test_files/scanned_notes.pdf", "application/pdf"),
    # ("test_files/receipt.png", "image/png"),
]


def _assert_json_serializable(obj, path="root"):
    """Recursively confirm every value is a plain JSON-safe type (jsonb-safe)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            _assert_json_serializable(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_json_serializable(v, f"{path}[{i}]")
    elif isinstance(obj, (str, int, bool, type(None))):
        pass
    elif isinstance(obj, float):
        assert not math.isnan(obj), f"NaN found at {path}"
        assert not math.isinf(obj), f"Infinity found at {path}"
    else:
        pytest.fail(f"Non-JSON-safe type {type(obj)} at {path}: {obj!r}")


@pytest.mark.parametrize("file_path,content_type", TEST_CASES)
def test_ingestion_e2e_shape_and_db_readiness(file_path, content_type):
    path = Path(file_path)
    result = ingest_document(path.read_bytes(), path.name, content_type)

    # --- 1. Required top-level keys exist ---
    required_keys = {
        "file_name", "file_type", "metadata", "extraction_method",
        "raw_text", "pages", "needs_review", "status"
    }
    missing = required_keys - result.keys()
    assert not missing, f"Missing keys: {missing}"

    # --- 2. Correct types ---
    assert isinstance(result["file_name"], str)
    assert isinstance(result["file_type"], str)
    assert isinstance(result["metadata"], dict)
    assert result["extraction_method"] in ("native_pdf", "ocr")
    assert isinstance(result["raw_text"], str)
    assert isinstance(result["pages"], list)
    assert isinstance(result["needs_review"], bool)
    assert result["status"] == "completed"

    # --- 3. Pages structure ---
    assert len(result["pages"]) > 0, "No pages extracted"
    for page in result["pages"]:
        assert "page_number" in page
        assert "text" in page
        assert isinstance(page["page_number"], int)
        assert isinstance(page["text"], str)

        if "elements" in page:
            assert isinstance(page["elements"], list)
            for el in page["elements"]:
                assert "text" in el

                if result["extraction_method"] == "ocr":
                    assert "confidence" in el
                    assert isinstance(el["confidence"], float)
                    assert 0.0 <= el["confidence"] <= 1.0
                else:
                    assert "font" in el
                    assert "font_size" in el

    # --- 4. Content isn't suspiciously empty ---
    assert len(result["raw_text"].strip()) > 0, "raw_text is empty"

    # --- 5. Fully JSON-serializable (jsonb-safe) ---
    _assert_json_serializable(result)
    json.dumps(result)

    # --- 6. Print full output for manual inspection ---
    print(f"\n{'=' * 60}")
    print(f"FILE: {result['file_name']}")
    print(f"METHOD: {result['extraction_method']}  |  needs_review: {result['needs_review']}")
    print(f"PAGES: {len(result['pages'])}  |  raw_text length: {len(result['raw_text'])}")
    print(f"METADATA KEYS: {list(result['metadata'].keys())}")
    print(f"{'=' * 60}")
    print(json.dumps(result, indent=2)[:2000])
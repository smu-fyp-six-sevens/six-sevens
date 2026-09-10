# test_ingestion.py
from pathlib import Path
from app.ingestion.ingestion_service import ingest_document

cases = [
    ("test_files/fin_notes.pdf", "application/pdf"),      # native text PDF
    ("test_files/payslip_pdfver.pdf", "application/pdf"),  # scanned/OCR PDF
    ("test_files/ocbc_sample.png", "image/png"),               # image OCR
]

for path, content_type in cases:
    file_path = Path(path)
    result = ingest_document(
        file_path.read_bytes(),
        file_path.name,
        content_type
    )
    print(f"\n--- {path} ---")
    print("extraction_method:", result["extraction_method"])
    print("status:", result["status"])
    print("pages:", len(result["pages"]))
    print("text preview:", result["raw_text"][:2000])
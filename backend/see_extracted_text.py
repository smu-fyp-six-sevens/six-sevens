from pathlib import Path
from app.ingestion.ingestion_service import ingest_document

path = Path("test_files/Certificate_of_Incorporation.pdf")
result = ingest_document(path.read_bytes(), path.name, "application/pdf")

print("extraction_method:", result["extraction_method"])
print("needs_review:", result["needs_review"])
print("status:", result["status"])
print("pages:", len(result["pages"]))
print()
print("raw_text preview:")
print(result["raw_text"][:1000])
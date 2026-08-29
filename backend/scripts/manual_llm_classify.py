"""
Manual, real-network check of the layer-3 LLM classifier against DeepSeek.

Not part of the automated test suite (tests/test_classification.py mocks the
HTTP call) — this hits the real API using DEEPSEEK_API_KEY from .env, so you
can eyeball actual model output for a given piece of text.

Usage:
    python scripts/manual_llm_classify.py "some document text"
    python scripts/manual_llm_classify.py path/to/extracted_text.txt
    python scripts/manual_llm_classify.py            # uses the "payslip" preset
    python scripts/manual_llm_classify.py confusing   # adversarial preset, see below
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.classification.classification import DeepSeekClassificationError, classify_by_llm

_PRESETS = {
    "payslip": (
        "PAYSLIP\n"
        "Employee: Jane Tan\n"
        "Basic Salary: 3,000.00\n"
        "Gross Pay: 3,200.00\n"
        "Net Pay: 2,650.00\n"
        "Pay Period: March 2026"
    ),
    # A cover email that *names* several document types without being any
    # of them — the old regex layer would hit "bank statement" (0.95) and
    # confidently mislabel this. Good check for whether the LLM is actually
    # reasoning about what the text IS, not just pattern-matching words.
    "confusing": (
        "Dear Sir/Madam,\n\n"
        "Further to your request, please find enclosed the following "
        "supporting documents for the account opening application: "
        "bank statement, latest payslip, a copy of NRIC, and proof of "
        "address. Kindly let us know if any further documents are "
        "required to complete the KYC process.\n\n"
        "Regards,\nJohn"
    ),
}


def _resolve_text(arg: str | None) -> tuple[str, str]:
    """Returns (source_label, text) so the caller can print what was used."""
    if arg is None:
        return "no arg passed -> defaulting to 'payslip' preset", _PRESETS["payslip"]
    if arg in _PRESETS:
        return f"preset '{arg}'", _PRESETS[arg]
    path = Path(arg)
    if path.is_file():
        return f"file '{path}'", path.read_text()
    return "literal text argument", arg


async def main() -> None:
    source, text = _resolve_text(sys.argv[1] if len(sys.argv) > 1 else None)

    print(f"--- source: {source} ---")
    print("--- input text ---")
    print(text)
    print("--- classifying via DeepSeek ---")

    try:
        result = await classify_by_llm(text)
    except DeepSeekClassificationError as exc:
        print(f"LLM layer failed: {exc}")
        return

    print(f"document_type:      {result.document_type}")
    print(f"confidence:          {result.confidence}")
    print(f"matched_keywords:    {result.matched_keywords}")
    print(f"needs_manual_review: {result.needs_manual_review}")
    print(f"reasoning:           {result.reasoning}")


if __name__ == "__main__":
    asyncio.run(main())

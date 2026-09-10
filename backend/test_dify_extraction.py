# backend/test_dify_extraction.py
import asyncio
from app.dify_client import dify_client
from app.extraction_prompts import build_extraction_prompt
from app.config import settings

async def main():
    raw_text = """
    EMPLOYEE PAYSLIP
    Employee: Jane Tan
    Employer: Acme Pte Ltd
    Pay period: 01 Jan 2025 - 31 Jan 2025
    Gross pay: SGD 6,000.00
    Net pay: SGD 5,200.00
    """

    prompt = build_extraction_prompt("payslip", raw_text)
    print("=== PROMPT SENT ===")
    print(prompt)
    print()

    inputs = {"extraction_prompt": prompt}

    result = await dify_client.run_workflow_with_retry(
        settings.DIFY_EXTRACTION_WORKFLOW_ID, inputs
    )

    print("=== RAW DIFY RESPONSE ===")
    print(result)

if __name__ == "__main__":
    asyncio.run(main())